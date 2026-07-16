"""Выполнение импорта: создать репозиторий, залить файлы, записать источник.

Единственное место в программе, которое пишет в Git. Правила жёсткие:

  - только создание нового. Существующий репозиторий не трогаем никогда.
  - имя занято -> отказ, а не перезапись.
  - что-то пошло не так на середине -> откатываем созданное, чтобы не
    оставлять половину.
  - вызывается только после явного подтверждения.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas.importer import ImportPlan
from vivatlas.models import Repository, Source, UpstreamLink
from vivatlas.providers.gitea import GiteaProvider
from vivatlas.scanner import get_or_create_source

log = logging.getLogger(__name__)


@dataclass
class ImportResult:
    repo_full_name: str
    files_written: int
    upstream_repo: str
    upstream_path: str
    repository_id: int


async def execute(
    session: Session,
    provider: GiteaProvider,
    plan: ImportPlan,
    gitea_url: str,
) -> ImportResult:
    owner, name = plan.target_owner, plan.target_name

    if await provider.repo_exists(owner, name):
        raise RuntimeError(
            f"{owner}/{name} уже есть. Импорт создаёт только новое — "
            f"перезаписывать существующее не буду. Выберите другое имя."
        )

    description = f"Импортировано из github.com/{plan.source.full_repo}"
    if plan.source.path:
        description += f"/{plan.source.path}"

    log.info("создаю %s/%s", owner, name)
    created = await provider.create_repo(owner, name, description)

    written = 0
    try:
        for f in plan.files:
            await provider.put_file(
                owner,
                name,
                f.path,
                f.content,
                message=f"Импорт из {plan.source.full_repo}",
            )
            written += 1
            if written % 20 == 0:
                log.info("  залито %d/%d", written, len(plan.files))
    except Exception:
        # Половина репозитория хуже, чем ничего: карточка соберётся кривой, а
        # отметка источника будет врать. Откатываем.
        log.error("залилось %d из %d — сношу созданное", written, len(plan.files))
        try:
            await provider.delete_repo(owner, name)
            log.info("откат выполнен, %s/%s удалён", owner, name)
        except Exception as exc:
            log.error("ОТКАТ НЕ УДАЛСЯ: %s/%s остался наполовину залитым: %s", owner, name, exc)
        raise

    source = get_or_create_source(session, "gitea", gitea_url, "Gitea")
    row = _record_repository(session, source, created, plan)
    session.flush()

    return ImportResult(
        repo_full_name=f"{owner}/{name}",
        files_written=written,
        upstream_repo=plan.source.full_repo,
        upstream_path=plan.source.path,
        repository_id=row.id,
    )


def _record_repository(
    session: Session, source: Source, created: dict, plan: ImportPlan
) -> Repository:
    now = datetime.now(UTC)
    row = Repository(
        source_id=source.id,
        external_id=str(created["id"]),
        owner=plan.target_owner,
        name=plan.target_name,
        default_branch=created.get("default_branch") or "main",
        description=created.get("description") or "",
        html_url=created.get("html_url") or "",
        clone_url=created.get("clone_url") or "",
        size_kb=0,
        original_url=f"https://github.com/{plan.source.full_repo}",
        remote_updated_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(row)
    return row


def record_upstream(session: Session, artifact_id: int, plan: ImportPlan) -> UpstreamLink:
    """Записать источник и отметку.

    Здесь отметка честна по построению: мы только что скопировали файлы, значит
    в этот момент копия и оригинал совпадают заведомо. Никакого "разошлось до
    того, как мы начали следить" быть не может.

    Запись может уже существовать: сборка карточки видит original_url, который
    мы сами и проставили, и заводит источник по нему. Наши сведения точнее —
    у нас есть слепки, — поэтому перезаписываем, а не падаем.
    """
    anchor = next(
        (f for f in plan.files if f.path.lower() in ("skill.md", "design.md", "readme.md")),
        None,
    )
    now = datetime.now(UTC)
    sha = anchor.sha if anchor else ""

    link = session.scalar(select(UpstreamLink).where(UpstreamLink.artifact_id == artifact_id))
    if link is None:
        link = UpstreamLink(artifact_id=artifact_id)
        session.add(link)

    link.kind = "github-file" if plan.source.kind != "repo" else "gitea-mirror"
    link.upstream_repo = plan.source.full_repo
    link.upstream_path = anchor.upstream_path if anchor else ""
    link.upstream_url = f"https://github.com/{plan.source.full_repo}"
    link.discovered_by = "импортировано этой программой"
    link.baseline_local_sha = sha
    link.baseline_upstream_sha = sha
    link.baseline_at = now
    link.last_local_sha = sha
    link.last_upstream_sha = sha
    link.last_checked_at = now
    link.status = "in-sync" if anchor else "unknown"
    link.check_error = None if anchor else "нет опорного файла — не с чем сравнивать"
    return link
