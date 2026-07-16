"""Сборка карточек: скачать репозиторий, распознать, описать, сохранить."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas import changes
from vivatlas.ai.base import TextModel
from vivatlas.archive import read_archive
from vivatlas.detector import detect
from vivatlas.models import Artifact, Repository
from vivatlas.providers.base import GitProvider, RepoRef
from vivatlas.summarizer import summarize
from vivatlas.upstream_sync import discover_for_artifact

log = logging.getLogger(__name__)


@dataclass
class IndexResult:
    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    summarized: int = 0
    summary_failed: int = 0
    failed: int = 0


def _to_ref(row: Repository) -> RepoRef:
    return RepoRef(
        external_id=row.external_id,
        owner=row.owner,
        name=row.name,
        default_branch=row.default_branch,
        is_private=False,  # в базу приватные не попадают, см. scanner.is_scannable
        is_archived=row.is_archived,
        is_empty=row.is_empty,
        html_url=row.html_url,
        clone_url=row.clone_url,
        size_kb=row.size_kb,
        description=row.description,
        updated_at=row.remote_updated_at,
    )


async def index_repository(
    session: Session,
    provider: GitProvider,
    text_model: TextModel | None,
    row: Repository,
    force: bool = False,
) -> str:
    """Собрать карточку для одного репозитория. Возвращает что произошло."""
    ref = _to_ref(row)
    head = await provider.get_head_sha(ref)

    artifact = session.scalar(select(Artifact).where(Artifact.repository_id == row.id))

    # Коммит тот же и описания на месте — качать архив незачем.
    if artifact and artifact.source_commit == head and not force:
        if artifact.summary_short:
            return "unchanged"

    blob = await provider.download_archive(ref, head)
    contents = read_archive(blob)
    detection = detect(contents)
    content_hash = hashlib.sha256(blob).hexdigest()

    if artifact is None:
        artifact = Artifact(repository_id=row.id)
        session.add(artifact)
        outcome = "created"
    else:
        outcome = "updated"
    # Содержимое правда поменялось, или мы просто пересобираем? Событие пишем
    # только в первом случае, иначе каждый прогон с --force плодил бы враньё.
    content_changed = artifact.content_hash not in (None, content_hash)
    previous_type = artifact.artifact_type

    artifact.name = row.name
    artifact.artifact_type = detection.artifact_type
    artifact.confidence = detection.confidence
    artifact.detect_reasons = "; ".join(detection.reasons)
    artifact.anchor_path = detection.anchor_path
    artifact.preview_path = detection.preview_path
    artifact.doc_text = detection.doc_text
    artifact.file_count = len(contents.files)
    artifact.file_paths = json.dumps(contents.paths, ensure_ascii=False)
    artifact.source_commit = head
    artifact.content_hash = content_hash
    artifact.updated_at = datetime.now(UTC)

    row.last_scanned_commit = head
    row.last_scanned_at = datetime.now(UTC)

    # Источник ищем здесь, а не потом: сейчас README целиком в руках, а в
    # doc_text он обрезан на 24 тысячах знаков — строчка Source стоит в самом
    # конце и в обрезку не попадает.
    session.flush()  # нужен artifact.id
    discover_for_artifact(session, artifact, contents, original_url=row.original_url or "")

    if outcome == "created":
        changes.record(
            session,
            "added",
            repository_id=row.id,
            artifact_id=artifact.id,
            title=row.full_name,
            details=f"тип: {detection.artifact_type}",
        )
    elif content_changed:
        what = "содержимое обновилось"
        if previous_type and previous_type != detection.artifact_type:
            what += f"; тип сменился: {previous_type} -> {detection.artifact_type}"
        changes.record(
            session,
            "updated",
            repository_id=row.id,
            artifact_id=artifact.id,
            title=row.full_name,
            details=what,
        )

    if text_model is not None:
        try:
            summaries = await summarize(
                text_model,
                full_name=row.full_name,
                artifact_type=detection.artifact_type,
                doc_text=detection.doc_text,
                file_count=len(contents.files),
            )
            artifact.summary_short = summaries["summary_short"]
            artifact.summary_normal = summaries["summary_normal"]
            artifact.summary_technical = summaries["summary_technical"]
            artifact.summary_model = getattr(text_model, "model", None)
            artifact.summary_error = None
        except Exception as exc:
            # Карточка остаётся — без описания, но с пометкой почему.
            # Молча притворяться, что описание есть, нельзя.
            artifact.summary_error = str(exc)[:500]
            log.warning("%s: описание не вышло: %s", row.full_name, exc)
            return outcome + "+no-summary"

    return outcome


async def index_all(
    session: Session,
    provider: GitProvider,
    text_model: TextModel | None,
    delay: float = 0.0,
    limit: int | None = None,
    force: bool = False,
) -> IndexResult:
    rows = session.scalars(
        select(Repository)
        .where(Repository.gone_at.is_(None), Repository.is_empty.is_(False))
        .order_by(Repository.owner, Repository.name)
    ).all()
    if limit:
        rows = rows[:limit]

    result = IndexResult()
    for i, row in enumerate(rows, 1):
        try:
            outcome = await index_repository(session, provider, text_model, row, force=force)
            # Сохраняем каждую карточку сразу. Одной транзакцией на весь прогон
            # нельзя: обрыв на середине унёс бы всю проделанную работу.
            session.commit()
        except Exception as exc:
            session.rollback()
            result.failed += 1
            log.error("[%d/%d] %s — ОШИБКА: %s", i, len(rows), row.full_name, exc)
            if delay and i < len(rows):
                await asyncio.sleep(delay)
            continue

        # Считаем только то, что действительно легло в базу.
        result.processed += 1
        if outcome.startswith("created"):
            result.created += 1
        elif outcome.startswith("updated"):
            result.updated += 1
        elif outcome == "unchanged":
            result.unchanged += 1
        if outcome.endswith("+no-summary"):
            result.summary_failed += 1
        elif outcome != "unchanged" and text_model is not None:
            result.summarized += 1
        log.info("[%d/%d] %s — %s", i, len(rows), row.full_name, outcome)

        if delay and i < len(rows):
            await asyncio.sleep(delay)

    return result
