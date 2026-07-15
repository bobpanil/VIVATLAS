"""Поставить новую версию вместо старой.

Второе — и последнее — место в программе, которое пишет в Git. Правила такие
же жёсткие, как у импорта, но повод отказать тут ровно один и он главный:

    обновляем ТОЛЬКО то, что вы не трогали.

Если копию правили, перезапись затрёт правку молча и без следа. Поэтому
состояние перепроверяется прямо перед записью, вживую, а не берётся из базы:
между вчерашней проверкой и сегодняшней командой файл могли поправить.

Заменяется один файл — опорный, тот самый, по которому мы сравниваем. Тащить
весь репозиторий заново мы не умеем и не притворяемся: файлы, которых у
источника нет, остались бы висеть, а карточка врала бы, что всё обновлено.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from skill_atlas.models import UpstreamLink
from skill_atlas.providers.base import RepoRef
from skill_atlas.providers.gitea import GiteaProvider
from skill_atlas.upstream import STATUS_NAMES, UpstreamChecker
from skill_atlas.upstream_sync import check_link

log = logging.getLogger(__name__)


class UpdateRefused(RuntimeError):
    """Обновлять нельзя. Причина — в тексте, человеку её надо прочитать."""


@dataclass
class UpdatePlan:
    link_id: int
    artifact_name: str
    repo_full_name: str
    path: str  # что заменим у себя
    upstream_repo: str
    upstream_path: str
    old_sha: str
    new_sha: str
    content: bytes

    @property
    def size_kb(self) -> float:
        return len(self.content) / 1024


def _repo_ref(link: UpstreamLink) -> RepoRef:
    repo = link.artifact.repository
    return RepoRef(
        external_id=repo.external_id,
        owner=repo.owner,
        name=repo.name,
        default_branch=repo.default_branch,
        is_private=False,
        is_archived=repo.is_archived,
        is_empty=repo.is_empty,
        html_url=repo.html_url,
        clone_url=repo.clone_url,
        size_kb=repo.size_kb,
    )


async def plan_update(
    session: Session,
    provider: GiteaProvider,
    checker: UpstreamChecker,
    link: UpstreamLink,
) -> UpdatePlan:
    """Что именно заменим. Ничего не пишет — только смотрит.

    Отказ здесь — это нормальный исход, а не сбой. Из пяти состояний обновлять
    можно ровно одно.
    """
    if link.kind == "gitea-mirror":
        raise UpdateRefused(
            "это зеркало — Gitea тянет его сама, и наша запись сбила бы ей синхронизацию"
        )

    status = await check_link(session, provider, checker, link, link.artifact.repository)
    session.commit()

    if status != "update-available":
        why = {
            "in-sync": "уже стоит то же самое — обновлять нечего",
            "locally-modified": "вы правили эту копию — перезапись затрёт вашу правку",
            "diverged": "и вы правили, и у источника новое — тут нужны руки, а не эта команда",
            "unknown": link.check_error or STATUS_NAMES.get(status, status),
        }.get(status, STATUS_NAMES.get(status, status))
        raise UpdateRefused(why)

    if not link.upstream_path:
        raise UpdateRefused("не знаем, где у источника лежит этот файл")

    anchor = link.artifact.anchor_path
    if not anchor:
        raise UpdateRefused("у карточки нет опорного файла — нечего заменять")

    content = await checker.blob(link.upstream_repo, link.last_upstream_sha)
    if not content:
        raise UpdateRefused("у источника файл пустой — на такое не меняем")

    return UpdatePlan(
        link_id=link.id,
        artifact_name=link.artifact.name,
        repo_full_name=link.artifact.repository.full_name,
        path=anchor,
        upstream_repo=link.upstream_repo,
        upstream_path=link.upstream_path,
        old_sha=link.last_local_sha,
        new_sha=link.last_upstream_sha,
        content=content,
    )


async def apply_update(
    session: Session,
    provider: GiteaProvider,
    checker: UpstreamChecker,
    plan: UpdatePlan,
) -> str:
    """Записать. Возвращает слепок того, что получилось.

    Вызывается только после явного подтверждения.
    """
    link = session.get(UpstreamLink, plan.link_id)
    owner, name = link.artifact.repository.owner, link.artifact.repository.name

    log.info("заменяю %s/%s: %s", owner, name, plan.path)
    await provider.update_file(
        owner,
        name,
        plan.path,
        plan.content,
        message=f"Обновление из github.com/{plan.upstream_repo}",
        sha=plan.old_sha,  # Gitea откажет, если файл успели поправить
        branch=link.artifact.repository.default_branch or "main",
    )

    # Проверяем, что легло именно то. Слепок считается от содержимого, поэтому
    # он обязан совпасть с источником. Если нет — Gitea что-то сделала с файлом
    # по дороге (перевод переносов строк, например), и отметка бы наврала.
    ref = _repo_ref(link)
    head = await provider.get_head_sha(ref)
    ours = await provider.blob_shas(ref, head)
    got = ours.get(plan.path, "")
    if got != plan.new_sha:
        raise RuntimeError(
            f"записали, но получилось не то: у нас {got[:8]}, у источника "
            f"{plan.new_sha[:8]}. Отметку не двигаю — иначе она соврёт."
        )

    now = datetime.now(UTC)
    link.baseline_local_sha = got
    link.baseline_upstream_sha = plan.new_sha
    link.baseline_at = now
    link.last_local_sha = got
    link.last_upstream_sha = plan.new_sha
    link.last_checked_at = now
    link.status = "in-sync"
    link.check_error = None
    return got
