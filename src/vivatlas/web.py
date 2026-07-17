"""Страницы для человека. API для программ живёт в api.py."""

import asyncio
import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from vivatlas import caticons, security
from vivatlas import changes as ch
from vivatlas import filters as flt
from vivatlas import purposes as pur
from vivatlas.ai import build_embedding_model, build_text_model
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.embeddings import embed_artifact
from vivatlas.finder import MAX_MEDIA_BYTES, Finder, looks_like_link
from vivatlas.import_run import execute, record_upstream
from vivatlas.importer import GitHubFetcher, ImportError_, plan_import
from vivatlas.indexer import index_repository
from vivatlas.models import (
    Artifact,
    ArtifactTag,
    Category,
    Favorite,
    Repository,
    Source,
    Tag,
    TagSuppression,
    UpstreamLink,
)
from vivatlas.providers import build_provider
from vivatlas.providers.gitea import GiteaProvider
from vivatlas.scanner import get_or_create_source, scan_source
from vivatlas.search import Mode, index_artifact_for_words
from vivatlas.search import search as do_search
from vivatlas.tagger import tag_artifact
from vivatlas.upstream import STATUS_NAMES

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()
log = logging.getLogger(__name__)

TYPE_NAMES = {
    "design-kit": "дизайн-набор",
    "claude-skill": "скилл Claude",
    "skill": "скилл",
    "claude-command": "команда",
    "claude-agent": "агент",
    "mcp-server": "MCP-сервер",
    "plugin": "плагин",
    "project": "проект",
    "draft": "черновик",
    "unknown": "не опознан",
}


def type_name(slug: str) -> str:
    return TYPE_NAMES.get(slug, slug)


BASIS_NAMES = {
    "documentation": "прямо сказано в описании",
    "tags": "выведено по тегам",
    "usage": "по истории использования",
    "ai-inference": "догадка по смыслу",
}


def basis_name(slug: str) -> str:
    return BASIS_NAMES.get(slug, slug or "не указано")


templates.env.globals["caticon"] = caticons.caticon_svg
templates.env.globals["type_name"] = type_name
templates.env.globals["basis_name"] = basis_name
templates.env.globals["status_name"] = lambda s: STATUS_NAMES.get(s, s)
templates.env.globals["kind_name"] = lambda k: ch.KIND_NAMES.get(k, k)
templates.env.globals["kind_mark"] = lambda k: ch.KIND_MARKS.get(k, "·")


def _combine(params: dict, **extra) -> dict:
    """Добавить к набору фильтров ещё что-то (обычно поисковый запрос)."""
    out = dict(params)
    out.update({k: v for k, v in extra.items() if v})
    return out


templates.env.filters["combine"] = _combine


def author_of(session, artifact: Artifact) -> str:
    """Кто сделал.

    Владелец в Gitea — это наша организация (design-lib, skills-lib), а не
    автор. Настоящий автор — владелец репозитория-источника. Источника нет —
    автор неизвестен, и врать про это не надо.
    """
    link = session.scalar(select(UpstreamLink).where(UpstreamLink.artifact_id == artifact.id))
    if link and link.upstream_repo and "/" in link.upstream_repo:
        return link.upstream_repo.split("/")[0]
    return ""


def preview_url(artifact: Artifact) -> str | None:
    """Превью берём прямо из Gitea — репозитории открытые, проксировать незачем."""
    if not artifact.preview_path or not artifact.repository.html_url:
        return None
    branch = artifact.repository.default_branch
    return f"{artifact.repository.html_url}/raw/branch/{branch}/{artifact.preview_path}"


def _counts(session, user_id: int | None = None) -> dict:
    # Считаем только видимое этому человеку: общее плюс своё частное. Черновики —
    # отдельный раздел, в общий счёт и типы не входят.
    vis = flt.visible_ids(user_id)
    not_draft = Artifact.artifact_type != "draft"
    by_type = session.execute(
        select(Artifact.artifact_type, func.count())
        .where(Artifact.id.in_(vis), not_draft)
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    return {
        "artifacts": session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.id.in_(vis), not_draft)
        )
        or 0,
        "drafts": flt.draft_count(session, user_id),
        "tags": session.scalar(select(func.count(func.distinct(ArtifactTag.tag_id)))) or 0,
        "by_type": by_type,
    }


def _fav_ids(session, user_id: int | None) -> set[int]:
    """Какие карточки этот человек занёс в избранное."""
    if user_id is None:
        return set()
    return set(
        session.scalars(select(Favorite.artifact_id).where(Favorite.user_id == user_id))
    )


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = "",
    type: str = "",
    tag: str = "",
    days: str = "",
    status: str = "",
    owner: str = "",
    fav: str = "",
    cat: str = "",
    draft: str = "",
    zone: str = "",
) -> HTMLResponse:
    f = flt.Filters(
        type=type, tag=tag, days=days, status=status, owner=owner, fav=fav, cat=cat,
        draft=draft, zone=zone,
    )

    # Вставили ссылку в поиск — искать её среди названий бессмысленно: такого
    # текста в карточках нет и быть не может. Раньше это молча возвращало
    # пустоту. Теперь предлагаем то, чего человек и хотел, — разобрать её.
    link = looks_like_link(q)

    model = build_embedding_model() if q and not link else None
    try:
        with session_scope() as session:
            user_id = getattr(request.state, "user_id", None)
            counts = _counts(session, user_id)
            fav_ids = _fav_ids(session, user_id)
            # Счётчик «избранное» — только по видимому: скрытые и вне зоны не в счёт,
            # иначе пилюля показывает больше, чем реально откроется.
            fav_visible = (
                session.scalar(
                    select(func.count())
                    .select_from(Artifact)
                    .where(Artifact.id.in_(fav_ids), Artifact.id.in_(flt.visible_ids(user_id)))
                )
                or 0
                if fav_ids
                else 0
            )

            if link:
                items = []
            elif q:
                # Поиск уже отобрал по смыслу — фильтры применяем к его выдаче,
                # а не к базе: иначе порядок по близости потеряется. Зона входит
                # в apply, поэтому чужое частное отсеется и в поиске.
                hits = await do_search(session, q, model, mode=Mode.BOTH, limit=200)
                allowed = {
                    a for a in session.scalars(flt.apply(select(Artifact.id), f, fav_ids, user_id))
                }
                items = [
                    _card(session, h.artifact, h.reasons, fav_ids)
                    for h in hits
                    if h.artifact.id in allowed
                ][:60]
            else:
                query = flt.apply(select(Artifact), f, fav_ids, user_id).order_by(Artifact.name)
                items = [_card(session, a, [], fav_ids) for a in session.scalars(query)]

            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "items": items,
                    "q": q,
                    "f": f,
                    "counts": counts,
                    "fav_count": fav_visible,
                    "types": flt.type_options(session, user_id),
                    "categories": flt.category_options(session, user_id),
                    "owners": flt.owner_options(session, user_id),
                    "tag_groups": flt.tag_groups(session, user_id=user_id),
                    "periods": flt.period_options(session, user_id),
                    "statuses": flt.status_options(session, user_id),
                    "period_names": {k: v[0] for k, v in flt.PERIODS.items()},
                    "link": link,
                    "nav": "all",
                    "active_cat": f.cat,
                    "active_draft": bool(f.draft),
                    "scan": scan_progress(user_id),
                    "zone_counts": flt.zone_counts(session, user_id),
                },
            )
    finally:
        if model:
            await model.aclose()


@router.post("/favorite/{artifact_id}")
def toggle_favorite(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Занести карточку в избранное или убрать. Избранное — личное, поэтому
    привязано к вошедшему. Возвращает JSON для страницы, редирект — без скрипта."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "нужно войти")
    with session_scope() as session:
        if session.get(Artifact, artifact_id) is None:
            raise HTTPException(404, "карточка не найдена")
        row = session.scalar(
            select(Favorite).where(
                Favorite.user_id == user_id, Favorite.artifact_id == artifact_id
            )
        )
        if row is not None:
            session.delete(row)
            now_fav = False
        else:
            session.add(Favorite(user_id=user_id, artifact_id=artifact_id))
            now_fav = True

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"favorite": now_fav})
    # Без скрипта: вернуться туда, откуда пришли. Только внутренний путь.
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


@router.get("/scan/status")
def scan_status(request: Request) -> JSONResponse:
    """Состояние идущего скана — для полосы прогресса на главной. Опрашивается
    страницей раз в пару секунд, пока идёт сбор."""
    user_id = getattr(request.state, "user_id", None)
    prog = scan_progress(user_id)
    return JSONResponse(prog or {"state": "idle"})


@router.post("/scan/dismiss")
def scan_dismiss(request: Request) -> JSONResponse:
    """Закрыть полосу (человек нажал ✕ или увидел итог)."""
    clear_scan(getattr(request.state, "user_id", None))
    return JSONResponse({"ok": True})


@router.get("/scan/cards")
def scan_cards(request: Request, after: int = 0) -> JSONResponse:
    """Готовая разметка карточек, добавленных после `after` (id больше него).
    Пока идёт скан, главная опрашивает это и вставляет новые карточки поштучно,
    не перезагружая страницу; заодно отдаём свежий общий счётчик для пилюли."""
    user_id = getattr(request.state, "user_id", None)
    with session_scope() as session:
        fav_ids = _fav_ids(session, user_id)
        arts = session.scalars(
            select(Artifact)
            .where(
                Artifact.id.in_(flt.visible_ids(user_id)),
                Artifact.id > after,
                Artifact.artifact_type != "draft",
            )
            .order_by(Artifact.id)
        ).all()
        tmpl = templates.get_template("_carditem.html")
        html = "".join(
            tmpl.render(it=_card(session, a, [], fav_ids), next_path="/") for a in arts
        )
        max_id = arts[-1].id if arts else after
        total = _counts(session, user_id)["artifacts"]
    return JSONResponse({"html": html, "total": total, "max_id": max_id, "count": len(arts)})


_CAT_STOP = {"и", "для", "по", "the", "and", "for", "of", "или", "с", "на"}


def _auto_category(session, art: Artifact) -> int | None:
    """Сама подобрать категорию новому инструменту: у какой папки слова из
    названия встречаются в тексте карточки (имя, описание, направление, теги).
    Не угадали — оставляем без папки, человек переложит перетаскиванием."""
    cats = session.scalars(select(Category)).all()
    if not cats:
        return None

    purpose = pur.detect_for(session, art.id, art.name)[0].label
    tag_slugs = session.scalars(
        select(Tag.slug).join(ArtifactTag).where(ArtifactTag.artifact_id == art.id)
    )
    text = " ".join(
        [art.name or "", art.summary_short or "", purpose, *(s for s in tag_slugs)]
    ).lower()
    # По целым словам, а не по подстроке: иначе «cli» находится в «clickhouse».
    text_words = set(re.split(r"[^0-9a-zа-яё]+", text))

    best_id, best_score = None, 0
    for c in cats:
        words = [
            w
            for w in re.split(r"[^0-9a-zа-яё]+", c.name.lower())
            if len(w) >= 3 and w not in _CAT_STOP
        ]
        score = sum(1 for w in words if w in text_words)
        if score > best_score:
            best_id, best_score = c.id, score
    return best_id


def _zone(a: Artifact) -> str:
    """Зона карточки по одной отметке: частная, если помечена личной
    (private_to_user_id задан); иначе общая (публичная)."""
    return "private" if a.private_to_user_id is not None else "common"


def _card(session, a: Artifact, reasons: list[str], fav_ids: set[int] = frozenset()) -> dict:
    purpose, _score = pur.detect_for(session, a.id, a.name)
    cat = session.get(Category, a.category_id) if a.category_id else None
    return {
        "category": (
            {
                "id": cat.id,
                "name": cat.name,
                "icon": cat.icon,
                "color": caticons.category_color(cat.id),
            }
            if cat
            else None
        ),
        "id": a.id,
        "name": a.name,
        "owner": a.repository.owner,
        "type": a.artifact_type,
        "summary_short": a.summary_short,
        "preview_url": preview_url(a),
        # Копируем ссылку на ИСТОЧНИК (откуда взято), а не на хранилище в Gitea.
        # Источник — то, что показываем и чем делятся. Нет источника — пусть
        # будет хранилище, чем ничего.
        "source_url": a.repository.original_url or a.repository.html_url,
        "favorite": a.id in fav_ids,
        "zone": _zone(a),
        "is_new": a.is_new,
        "reasons": reasons,
        "author": author_of(session, a),
        "created": a.repository.remote_created_at,
        "updated": a.repository.remote_updated_at,
        "purpose": purpose,
    }


@router.get("/a/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: int) -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        a = session.get(Artifact, artifact_id)
        if a is None:
            raise HTTPException(404, "карточка не найдена")
        # Зона: чужое частное не показываем даже по прямой ссылке. «Не найдена»,
        # а не «нельзя» — незачем подтверждать, что такая карточка существует.
        priv = a.private_to_user_id
        if priv is not None and priv != user_id:
            raise HTTPException(404, "карточка не найдена")

        # Открыл — значит увидел: гасим бейдж «новое».
        if a.is_new:
            a.is_new = False

        links = session.scalars(
            select(ArtifactTag).where(ArtifactTag.artifact_id == artifact_id)
        ).all()
        # Сначала свои решения, потом правила, потом догадки — по убыванию
        # надёжности, а не по алфавиту.
        order = {"manual": 0, "derived": 1, "ai": 2}
        tags = sorted(
            (
                {
                    "slug": link.tag.slug,
                    "source": link.source,
                    "confidence": link.confidence,
                    "origin": link.origin,
                }
                for link in links
            ),
            key=lambda t: (order.get(t["source"], 9), -t["confidence"], t["slug"]),
        )
        suppressed = [
            {"slug": s.tag.slug}
            for s in session.scalars(
                select(TagSuppression).where(TagSuppression.artifact_id == artifact_id)
            )
        ]
        upstream = session.scalar(
            select(UpstreamLink).where(UpstreamLink.artifact_id == artifact_id)
        )

        return templates.TemplateResponse(
            request,
            "artifact.html",
            {
                "a": a,
                "tags": tags,
                "suppressed": suppressed,
                "upstream": upstream,
                "author": author_of(session, a),
                "purpose": pur.detect_for(session, a.id, a.name)[0],
                "preview_url": preview_url(a),
                "zone": _zone(a),
                "is_draft": a.artifact_type == "draft",
                "counts": _counts(session, user_id),
                "categories": flt.category_options(session, user_id),
                "active_cat": "",
                "active_draft": False,
            },
        )


@router.post("/artifact/{artifact_id}/category")
def set_category(
    request: Request,
    artifact_id: int,
    cat: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> Response:
    """Положить карточку в категорию-папку (или вынуть, если cat пуст).
    Категории общие — раскладывает владелец."""
    if not getattr(request.state, "is_owner", False):
        raise HTTPException(403, "раскладывать по категориям может владелец")
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, "карточка не найдена")
        if cat and cat.isdigit() and session.get(Category, int(cat)) is not None:
            art.category_id = int(cat)
        else:
            art.category_id = None
        new_cat = art.category_id

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "cat": new_cat})
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


@router.post("/artifact/{artifact_id}/visibility")
def toggle_visibility(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Переключить зону карточки: частная (видна только владельцу) ↔ публичная
    (видят все). После скана карточки приходят частными; кнопкой в футере человек
    публикует нужные. Менять может хозяин каталога, владелец источника карточки
    или тот, кому она уже частная."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "нужно войти")
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, "карточка не найдена")
        cur = art.private_to_user_id
        src_owner = art.repository.source.owner_user_id
        is_owner = getattr(request.state, "is_owner", False)
        if not (is_owner or src_owner == user_id or cur == user_id):
            raise HTTPException(403, "менять зону может владелец")
        if cur is None:
            art.private_to_user_id = user_id  # сделать частной
            now_private = True
        else:
            art.private_to_user_id = None  # опубликовать
            now_private = False

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            {"private": now_private, "zone": "private" if now_private else "common"}
        )
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


# Хостинги, которые сейчас умеет сканировать провайдер Gitea. Codeberg — это
# Forgejo (форк Gitea), тот же API.
_GITEA_KINDS = {"gitea", "codeberg"}


# Прогресс идущих сканов: user_id -> {state, total, done, added, source, error}.
# Живёт в памяти одного процесса-сервера (8710). Скан — фоновая задача того же
# цикла; главная страница опрашивает статус и рисует полосу. Пропадёт при
# перезапуске — это нормально: полоса просто исчезнет, карточки останутся.
_SCANS: dict[int, dict] = {}
# Держим сильную ссылку на фоновые задачи: create_task хранит лишь слабую, и
# без этого сборщик мусора может убить скан на середине.
_SCAN_TASKS: set = set()


def scan_progress(user_id: int | None) -> dict | None:
    """Состояние скана этого человека для полосы прогресса. Нет — None."""
    if user_id is None:
        return None
    return _SCANS.get(user_id)


def clear_scan(user_id: int | None) -> None:
    """Убрать полосу после того, как человек её закрыл или увидел итог."""
    if user_id is not None:
        _SCANS.pop(user_id, None)


def precheck_user_scan(user_id: int | None, source_id: int) -> tuple[str, str]:
    """Мгновенные проверки без сети: это свой источник, хостинг поддержан, токен
    на месте и читается. Возвращает (ошибка, имя_источника). Сеть (список
    репозиториев) и всё остальное — уже в фоне, чтобы кнопка отвечала сразу."""
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src is None or src.owner_user_id != user_id:
            return ("Источник не найден.", "")
        kind, token_enc, name = src.kind, src.token_enc, src.display_name

    if kind not in _GITEA_KINDS:
        return (
            "Скан пока умеет только Gitea и Codeberg. Если это ваш Gitea — "
            "выберите «Gitea» в списке хостингов слева и нажмите «Сохранить», "
            "затем «Сканировать». Провайдеры для GitHub и других добавим позже.",
            "",
        )
    if not token_enc:
        return ("У источника нет токена — впишите токен доступа и сохраните.", "")
    try:
        security.decrypt_secret(token_enc)
    except Exception:
        return ("Токен нечитаем (сменился ключ шифрования) — впишите его заново.", "")
    return ("", name)


def launch_user_scan(user_id: int, source_id: int, source_name: str) -> None:
    """Запустить фоновый скан. Заводит полосу прогресса и отдаёт управление
    сразу — весь обход (даже получение списка) идёт задачей того же цикла, а
    кнопка мгновенно ведёт на главную, где видна полоса."""
    _SCANS[user_id] = {
        "state": "running",
        "total": 0,
        "done": 0,
        "added": 0,
        "source": source_name,
        "error": "",
    }
    # Ручной запуск — полная пересборка (force): человек нажал и ждёт свежего.
    task = asyncio.create_task(_run_user_scan(user_id, source_id, _SCANS[user_id], force=True))
    _SCAN_TASKS.add(task)
    task.add_done_callback(_SCAN_TASKS.discard)


async def _run_user_scan(
    user_id: int, source_id: int, progress: dict | None = None, force: bool = False
) -> None:
    """Фоновый обход источника целиком: получить список репозиториев, затем по
    одному — скачать, описать, разложить в частную зону владельца. С progress
    (ручной запуск) двигаем полосу на главной; без него (ежедневный авто-скан)
    работаем тихо. force — полная пересборка (ручной); без него авто-скан
    пропускает неизменившиеся репозитории, тратя ИИ только на новые. Сбой на
    одном репозитории не роняет остальные; общий сбой помечает полосу ошибкой."""

    def bump(key: str, n: int = 1) -> None:
        if progress is not None:
            progress[key] = progress.get(key, 0) + n

    def setp(**kw) -> None:
        if progress is not None:
            progress.update(kw)

    # Токен берём из базы здесь, в фоне: в precheck его не расшифровываем дольше
    # нужного. Источник свой — уже проверено (ручной запуск) либо это личный
    # источник из авто-обхода.
    with session_scope() as session:
        src = session.get(Source, source_id)
        base_url, token_enc = (src.base_url, src.token_enc) if src else ("", "")
    try:
        token = security.decrypt_secret(token_enc) if token_enc else ""
    except Exception:
        setp(state="error", error="Токен стал нечитаем.")
        return

    provider = GiteaProvider(base_url=base_url, token=token, timeout=settings.http_timeout_seconds)
    text_model = build_text_model()
    embed_model = build_embedding_model()
    try:
        # Получить и сохранить список репозиториев. Пока total=0, полоса честно
        # пишет «читаю список репозиториев…».
        with session_scope() as session:
            src = session.get(Source, source_id)
            await scan_source(session, provider, src, include_private=True)
            session.commit()
            # select(Repository.id) отдаёт уже сами id-числа, а не строки.
            repo_ids = list(
                session.scalars(
                    select(Repository.id).where(
                        Repository.source_id == source_id, Repository.gone_at.is_(None)
                    )
                )
            )
        setp(total=len(repo_ids))
        for rid in repo_ids:
            try:
                with session_scope() as session:
                    repo = session.get(Repository, rid)
                    result = await index_repository(
                        session, provider, text_model, repo, force=force
                    )
                    art = session.scalar(select(Artifact).where(Artifact.repository_id == rid))
                    # «unchanged» — коммит тот же, карточка уже собрана: не тратим
                    # ИИ впустую (важно для ежедневного авто-скана).
                    if art is not None and not result.startswith("unchanged"):
                        await embed_artifact(session, embed_model, art)
                        await tag_artifact(session, art, text_model)
                        index_artifact_for_words(session, art)
                        # Новую карточку кладём приватной владельцу (публикует он
                        # сам кнопкой) и помечаем «новинкой». У уже существующей
                        # зону и категорию не трогаем — уважаем выбор человека.
                        if result.startswith("created"):
                            art.hidden = False
                            art.private_to_user_id = user_id
                            art.is_new = True
                            art.category_id = _auto_category(session, art)
                            bump("added")
                    session.commit()
            except Exception:
                log.exception("scan: репозиторий %s не собрался", rid)
            bump("done")
        setp(state="done")
    except Exception as exc:
        log.exception("scan источника %s не удался", source_id)
        setp(state="error", error=str(exc))
    finally:
        await provider.aclose()
        await text_model.aclose()
        await embed_model.aclose()


@router.get("/recommend")
def recommend_redirect(task: str = "") -> RedirectResponse:
    """«Что взять?» слит с поиском: одно окно на всё. Старую ссылку с задачей
    уводим прямо в поиск, чтобы закладки не сломались.

    Рекомендации никуда не делись — они остались для ChatGPT через MCP, где у
    ответа есть место под объяснения. На сайте же поиск и так ранжирует по
    смыслу, и отдельная страница только раздваивала «спросить программу»."""
    q = f"?q={quote(task.strip())}" if task.strip() else ""
    return RedirectResponse(f"/{q}", status_code=308)


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        return templates.TemplateResponse(
            request, "help.html", {"counts": _counts(session, user_id), "nav": "help"}
        )


@router.get("/changes", response_class=HTMLResponse)
def changes_page(request: Request, kind: str = "", stale: str = "") -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        stale_mode = bool(stale)
        stale_items = ch.stale(session) if stale_mode else []
        oldest, newest = ch.oldest_and_newest(session)

        by_kind = {}
        for k in ("added", "updated", "removed", "renamed"):
            n = len(ch.recent(session, limit=9999, kind=k))
            if n:
                by_kind[k] = n

        return templates.TemplateResponse(
            request,
            "changes.html",
            {
                "items": ch.recent(session, limit=100, kind=kind) if not stale_mode else [],
                "kind": kind,
                "counts_by_kind": by_kind,
                "total": sum(by_kind.values()),
                "stale_mode": stale_mode,
                "stale_items": stale_items,
                "stale_count": len(ch.stale(session)),
                "stale_days": ch.STALE_DAYS,
                "nav": "changes",
                "oldest": oldest,
                "newest": newest,
                "counts": _counts(session, user_id),
            },
        )


# --- добавление ---------------------------------------------------------
#
# Три шага, и порядок тут — не украшение:
#
#   1. что дали  -> ищем, показываем кандидатов. Ничего не пишем.
#   2. выбрали   -> показываем план: что создастся, сколько файлов. Не пишем.
#   3. нажали    -> пишем.
#
# Автоматически не тащим никогда. Название на слух и с картинки распознаётся
# неточно, модель иногда выдумывает адрес — на живом рилсе выдала
# skills/last-30-day, которого не существует. Решает человек, глазами.


def _add_page(request: Request, step: str, **extra) -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        return templates.TemplateResponse(
            request,
            "add.html",
            {"step": step, "counts": _counts(session, user_id), "nav": "add", **extra},
        )


@router.get("/add", response_class=HTMLResponse)
def add_start(request: Request) -> HTMLResponse:
    return _add_page(request, "start")


@router.post("/add", response_class=HTMLResponse)
async def add_find(
    request: Request,
    source: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    """Шаг 1: что дали — то и разбираем. Ничего не пишем."""
    tmp: Path | None = None
    src = source.strip()

    if file is not None and file.filename:
        # Расширение сохраняем: по нему finder отличает картинку от ролика.
        suffix = Path(file.filename).suffix or ".bin"
        data = await file.read()
        if len(data) > MAX_MEDIA_BYTES:
            return _add_page(
                request,
                "start",
                error=f"Файл больше {MAX_MEDIA_BYTES // 1_000_000} МБ — модель такой не примет.",
                source=src,
            )
        fd, name = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        tmp = Path(name)
        src = str(tmp)

    if not src:
        return _add_page(request, "start", error="Дайте ссылку, скриншот или хотя бы название.")

    finder = Finder(github_token=settings.github_token)
    model = build_text_model() if settings.google_api_key else None
    try:
        result = await finder.find(src, model)
    except Exception as exc:
        return _add_page(request, "start", error=f"Не получилось разобрать: {exc}", source=source)
    finally:
        await finder.aclose()
        if model is not None:
            await model.aclose()
        if tmp is not None:
            tmp.unlink(missing_ok=True)

    return _add_page(
        request,
        "found",
        result=result,
        given=file.filename if (file and file.filename) else source,
    )


@router.post("/add/plan", response_class=HTMLResponse)
async def add_plan(
    request: Request,
    url: Annotated[str, Form()],
    to: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Шаг 2: что именно будет создано. По-прежнему ничего не пишем."""
    fetcher = GitHubFetcher(token=settings.github_token)
    try:
        plan = await plan_import(fetcher, url, target_owner=to, target_name=name)
    except ImportError_ as exc:
        # Отказ бывает полезным: "это целый проект, а вот папки внутри,
        # похожие на инструменты" — со ссылками, по которым можно продолжить.
        return _add_page(request, "refused", message=str(exc), url=url, to=to)
    except Exception as exc:
        return _add_page(request, "refused", message=f"Не получилось: {exc}", url=url, to=to)
    finally:
        await fetcher.aclose()

    return _add_page(request, "plan", plan=plan, url=url, to=to)


@router.post("/add/run")
async def add_run(
    request: Request,
    url: Annotated[str, Form()],
    to: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
):
    """Шаг 3: записываем. Только сюда и только по нажатию.

    План строим заново, а не храним между шагами. Лишняя закачка архива, зато
    никакого устаревшего плана: между "показали" и "нажали" человек мог уйти
    пить чай, а у источника за это время всё поменялось.
    """
    if not settings.gitea_token:
        return _add_page(request, "refused", message="Нет GITEA_TOKEN — писать нечем.", url=url)

    fetcher = GitHubFetcher(token=settings.github_token)
    try:
        plan = await plan_import(fetcher, url, target_owner=to, target_name=name)
    except Exception as exc:
        await fetcher.aclose()
        return _add_page(request, "refused", message=str(exc), url=url, to=to)
    await fetcher.aclose()

    user_id = getattr(request.state, "user_id", None)
    provider = build_provider("gitea")
    text_model = build_text_model()
    embed_model = build_embedding_model()
    try:
        with session_scope() as session:
            result = await execute(session, provider, plan, settings.gitea_url)
            session.commit()

            repo = session.get(Repository, result.repository_id)
            await index_repository(session, provider, text_model, repo, force=True)
            session.commit()

            art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
            record_upstream(session, art.id, plan)
            await embed_artifact(session, embed_model, art)
            await tag_artifact(session, art, text_model)
            index_artifact_for_words(session, art)
            # Сама подобрать папку по смыслу — человеку останется поправить.
            art.category_id = _auto_category(session, art)
            # По умолчанию новая карточка — личная у создателя: пока он не выберет
            # «расшаренная», другие её не видят. Так «появляется в списке после
            # сохранения» выполняется для всех остальных.
            art.private_to_user_id = user_id
            session.commit()
            card = {
                "id": art.id,
                "name": art.name,
                "owner": art.repository.owner,
                "summary_short": art.summary_short,
                "preview_url": preview_url(art),
            }
    except Exception as exc:
        return _add_page(request, "refused", message=f"Не получилось: {exc}", url=url, to=to)
    finally:
        await provider.aclose()
        await text_model.aclose()
        await embed_model.aclose()

    return _add_page(request, "done", card=card)


def _create_draft(session, user_id, source_url: str, name: str, summary: str, heard: str) -> int:
    """Черновик: карточка без импорта из GitHub. Когда ссылку или ролик не
    удалось свести к репозиторию, сохраняем, что распознали, — чтобы обработать
    потом. Живёт в отдельном источнике «Черновики», личная у создателя."""
    src = get_or_create_source(session, "draft", "", "Черновики")
    key = source_url or name or heard or "черновик"
    ext = "draft-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]  # noqa: S324

    repo = session.scalar(
        select(Repository).where(Repository.source_id == src.id, Repository.external_id == ext)
    )
    if repo is None:
        repo = Repository(
            source_id=src.id,
            external_id=ext,
            owner="черновик",
            name=(name or "черновик")[:256],
            default_branch="",
            html_url="",
            original_url=source_url or "",
        )
        session.add(repo)
        session.flush()

    art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
    if art is None:
        art = Artifact(
            repository_id=repo.id,
            name=(name or "черновик")[:256],
            artifact_type="draft",
            summary_short=summary or "",
            doc_text=heard or "",
            private_to_user_id=user_id,
        )
        session.add(art)
        session.flush()
    else:
        art.name = (name or art.name)[:256]
        art.summary_short = summary or art.summary_short
        art.private_to_user_id = user_id
    index_artifact_for_words(session, art)
    return art.id


@router.post("/add/draft")
def add_draft(
    request: Request,
    source: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    heard: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Не свелось к GitHub — делаем черновик и ведём к тому же выбору зоны."""
    user_id = getattr(request.state, "user_id", None)
    with session_scope() as session:
        aid = _create_draft(
            session, user_id, source.strip(), name.strip(), summary.strip(), heard.strip()
        )
        art = session.get(Artifact, aid)
        card = {
            "id": art.id,
            "name": art.name,
            "owner": art.repository.owner,
            "summary_short": art.summary_short,
            "preview_url": preview_url(art),
        }
    return _add_page(request, "done", card=card)


@router.post("/add/save")
def add_save(
    request: Request,
    artifact_id: Annotated[int, Form()],
    zone: Annotated[str, Form()] = "shared",
) -> RedirectResponse:
    """Финал создания: карточку отмечают личной или расшаренной и сохраняют.
    До этого она — личный черновик создателя."""
    user_id = getattr(request.state, "user_id", None)
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        # Менять зону может только тот, чей это черновик (или уже общий им же).
        if art is not None and art.private_to_user_id in (user_id, None):
            art.private_to_user_id = user_id if zone == "private" else None
    return RedirectResponse("/", status_code=303)
