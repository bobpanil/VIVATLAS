"""Перенос существующих репозиториев на правило «путь как на GitHub».

Две части. Расчёт плана — чистый, ничего не трогает, проверяется тестами.
Выполнение — пишет в Gitea, по одному, и останавливается на первой же ошибке,
не пытаясь героически докрутить остальные: половина переноса это состояние,
из которого видно, где встали, а не куча наугад.

Правило для существующих карточек:
  - источник не записан            -> не трогаем, мы не знаем адрес на GitHub;
  - из источника одна карточка      -> владелец/репозиторий, как на GitHub;
  - из источника несколько карточек -> владелец/репозиторий-папка, чтобы
    74 набора из одного awesome-design-md не легли на один адрес.
"""

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas.importer import _safe_name
from vivatlas.models import Artifact, Repository, UpstreamLink
from vivatlas.providers.gitea import GiteaProvider

log = logging.getLogger(__name__)


@dataclass
class RemapItem:
    repo_id: int
    old_owner: str
    old_name: str
    new_owner: str
    new_name: str
    upstream_repo: str

    @property
    def old_full(self) -> str:
        return f"{self.old_owner}/{self.old_name}"

    @property
    def new_full(self) -> str:
        return f"{self.new_owner}/{self.new_name}"


@dataclass
class RemapPlan:
    changes: list[RemapItem]
    unchanged: list[str]  # источник не записан — нечем зеркалить
    already: list[str]  # уже по правилу, трогать нечего

    @property
    def new_orgs(self) -> list[str]:
        return sorted({i.new_owner for i in self.changes})


def _leaf(path: str) -> str:
    """Имя папки инструмента. У файла берём папку над ним, не сам файл."""
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return ""
    if "." in parts[-1] and len(parts) >= 2:
        return parts[-2]
    return parts[-1]


def target_for(upstream_repo: str, upstream_path: str, shared_source: bool) -> tuple[str, str]:
    """Куда карточка должна переехать. Владелец и имя по правилу."""
    gh_owner, gh_repo = upstream_repo.split("/", 1)
    owner = _safe_name(gh_owner)
    if shared_source:
        name = _safe_name(f"{gh_repo}-{_leaf(upstream_path)}")
    else:
        name = _safe_name(gh_repo)
    return owner, name


def compute_plan(session: Session) -> RemapPlan:
    """Что куда переедет. Ничего не трогает — только смотрит в базу."""
    repos = session.scalars(select(Repository).where(Repository.gone_at.is_(None))).all()

    links: dict[int, UpstreamLink | None] = {}
    for repo in repos:
        art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
        links[repo.id] = (
            session.scalar(select(UpstreamLink).where(UpstreamLink.artifact_id == art.id))
            if art
            else None
        )

    # Сколько карточек указывают на один и тот же источник.
    shared = Counter(link.upstream_repo for link in links.values() if link)

    changes: list[RemapItem] = []
    unchanged: list[str] = []
    already: list[str] = []

    for repo in repos:
        link = links[repo.id]
        if not link or "/" not in (link.upstream_repo or ""):
            unchanged.append(f"{repo.owner}/{repo.name}")
            continue
        owner, name = target_for(
            link.upstream_repo, link.upstream_path or "", shared[link.upstream_repo] > 1
        )
        if (owner, name) == (repo.owner, repo.name):
            already.append(f"{repo.owner}/{repo.name}")
            continue
        changes.append(
            RemapItem(
                repo_id=repo.id,
                old_owner=repo.owner,
                old_name=repo.name,
                new_owner=owner,
                new_name=name,
                upstream_repo=link.upstream_repo,
            )
        )

    _assert_no_collisions(changes, unchanged, already)
    return RemapPlan(changes=changes, unchanged=unchanged, already=already)


def _assert_no_collisions(
    changes: list[RemapItem], unchanged: list[str], already: list[str]
) -> None:
    """Два репозитория не должны приехать на один адрес. Молча слить два разных
    инструмента в один — худшее, что тут может случиться."""
    seen: dict[str, str] = {}
    fixed = set(unchanged) | set(already)
    for item in changes:
        if item.new_full in fixed:
            raise RuntimeError(
                f"{item.old_full} едет на {item.new_full}, а там уже стоит нетронутый репозиторий"
            )
        if item.new_full in seen:
            raise RuntimeError(
                f"столкновение: и {seen[item.new_full]}, и {item.old_full} едут на {item.new_full}"
            )
        seen[item.new_full] = item.old_full


async def apply_item(
    session: Session, provider: GiteaProvider, item: RemapItem, gitea_url: str
) -> None:
    """Перенести один репозиторий. Пишет в Gitea и в базу.

    Порядок важен: сначала имя в пределах старого владельца, потом передача.
    Так репозиторий до последнего шага остаётся там, где мы его знаем.
    """
    base = gitea_url.rstrip("/")

    # Целевой адрес не должен быть занят: иначе передача либо упадёт, либо
    # затрёт чужое. Проверяем до того, как что-то сделать.
    if await provider.repo_exists(item.new_owner, item.new_name):
        raise RuntimeError(f"{item.new_full} уже существует — не переношу поверх")

    if item.new_name != item.old_name:
        await provider.rename_repo(item.old_owner, item.old_name, item.new_name)

    if item.new_owner != item.old_owner:
        await provider.create_org(item.new_owner)
        await provider.transfer_repo(item.old_owner, item.new_name, item.new_owner)

    # Проверяем, что репозиторий и правда там, куда собирались.
    if not await provider.repo_exists(item.new_owner, item.new_name):
        raise RuntimeError(
            f"перенесли {item.old_full}, но по адресу {item.new_full} его нет — остановка"
        )

    repo = session.get(Repository, item.repo_id)
    repo.owner = item.new_owner
    repo.name = item.new_name
    repo.html_url = f"{base}/{item.new_owner}/{item.new_name}"
    repo.clone_url = f"{base}/{item.new_owner}/{item.new_name}.git"
    # Теперь адрес источника читается прямо из пути — но запишем и явно.
    repo.original_url = f"https://github.com/{item.upstream_repo}"
