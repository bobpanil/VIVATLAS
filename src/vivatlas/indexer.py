"""Building cards: download the repository, detect, summarize, save."""

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
        is_private=False,  # private ones never reach the database, see scanner.is_scannable
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
    """Build the card for a single repository. Returns what happened."""
    ref = _to_ref(row)
    head = await provider.get_head_sha(ref)

    artifact = session.scalar(select(Artifact).where(Artifact.repository_id == row.id))

    # Same commit and the summary is in place — no point downloading the archive.
    if artifact and artifact.source_commit == head and not force:
        if artifact.summary_short:
            return "unchanged"

    blob = await provider.download_archive(ref, head)
    contents = read_archive(blob)
    detection = detect(contents)
    content_hash = hashlib.sha256(blob).hexdigest()

    if artifact is None:
        # Ownership and "shared" follow the source owner: a SHARED source (with no
        # owner) yields shared cards, a PERSONAL one yields private cards tied to the
        # owner. That way no creation path (including the bulk index_all) leaves a card
        # public by default. We read the owner BEFORE creating the card:
        # touching row.source is a query, and it would trigger an autoflush of the still-empty
        # (name=NULL) card and break the insert.
        src_owner = row.source.owner_user_id
        artifact = Artifact(
            repository_id=row.id, owner_user_id=src_owner, shared=src_owner is None
        )
        session.add(artifact)
        outcome = "created"
    else:
        outcome = "updated"
    # Did the content really change, or are we just rebuilding? We record the event
    # only in the first case, otherwise every --force run would spawn lies.
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

    # We look for the source here, not later: right now we have the full README, whereas in
    # doc_text it is truncated at 24 thousand characters — the Source line sits at the very
    # end and doesn't survive the truncation.
    session.flush()  # artifact.id is needed
    discover_for_artifact(session, artifact, contents, original_url=row.original_url or "")

    if outcome == "created":
        changes.record(
            session,
            "added",
            repository_id=row.id,
            artifact_id=artifact.id,
            title=row.full_name,
            details=f"type: {detection.artifact_type}",
        )
    elif content_changed:
        what = "content updated"
        if previous_type and previous_type != detection.artifact_type:
            what += f"; type changed: {previous_type} -> {detection.artifact_type}"
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
            # The card stays — without a summary, but with a note explaining why.
            # We must not silently pretend a summary exists.
            artifact.summary_error = str(exc)[:500]
            log.warning("%s: summary failed: %s", row.full_name, exc)
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
            # Save each card right away. A single transaction for the whole run
            # won't do: a break in the middle would wipe out all the work done.
            session.commit()
        except Exception as exc:
            session.rollback()
            result.failed += 1
            log.error("[%d/%d] %s — ERROR: %s", i, len(rows), row.full_name, exc)
            if delay and i < len(rows):
                await asyncio.sleep(delay)
            continue

        # Count only what actually landed in the database.
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
