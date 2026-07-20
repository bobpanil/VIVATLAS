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
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy import update as sa_update

from vivatlas import categories as catperm
from vivatlas import caticons, catnames, i18n, security
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
    ArtifactCategory,
    ArtifactTag,
    Category,
    Change,
    Embedding,
    Favorite,
    RemovedNotice,
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

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
router = APIRouter()
log = logging.getLogger(__name__)

templates.env.globals["caticon"] = caticons.caticon_svg
# type_name / basis_name / status_name / kind_name — языкозависимые ярлыки,
# приходят из i18n.template_context (context_processors) и берутся из каталога.
# Здесь только нейтральная марка изменения (символ, язык не важен).
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
    mine = 0
    if user_id is not None:
        mine = (
            session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.owner_user_id == user_id, not_draft)
            )
            or 0
        )
    # Избранное — для бейджа в панели: столько же, сколько покажет вид /?fav=1
    # (видимое, не черновик, у этого человека). Аноним ничего в избранном не имеет.
    favorites = 0
    if user_id is not None:
        favorites = (
            session.scalar(
                select(func.count())
                .select_from(Favorite)
                .join(Artifact, Artifact.id == Favorite.artifact_id)
                .where(Favorite.user_id == user_id, Favorite.artifact_id.in_(vis), not_draft)
            )
            or 0
        )
    return {
        "artifacts": session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.id.in_(vis), not_draft)
        )
        or 0,
        "mine": mine,
        "favorites": favorites,
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


@router.get("/lang/{code}")
def set_language(request: Request, code: str, next: str = "/") -> RedirectResponse:
    """Переключить язык интерфейса: кладём куку и возвращаемся, откуда пришли.
    Открыт без входа — язык меняют и на странице входа."""
    dest = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(dest, status_code=303)
    i18n.set_lang_cookie(resp, code, secure=request.url.scheme == "https")
    return resp


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
    sort: str = "",
) -> HTMLResponse:
    f = flt.Filters(
        type=type, tag=tag, days=days, status=status, owner=owner, fav=fav, cat=cat,
        draft=draft, zone=zone, sort=sort,
    )

    # Вставили ссылку в поиск — искать её среди названий бессмысленно: такого
    # текста в карточках нет и быть не может. Раньше это молча возвращало
    # пустоту. Теперь предлагаем то, чего человек и хотел, — разобрать её.
    link = looks_like_link(q)

    model = build_embedding_model() if q and not link else None
    try:
        with session_scope() as session:
            user_id = getattr(request.state, "user_id", None)
            lang = getattr(request.state, "lang", "en")
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
                    _card(session, h.artifact, h.reasons, fav_ids, lang, user_id)
                    for h in hits
                    if h.artifact.id in allowed
                ][:60]
            else:
                query = flt.apply(select(Artifact), f, fav_ids, user_id).order_by(
                    *flt.sort_order(f.sort)
                )
                items = [
                    _card(session, a, [], fav_ids, lang, user_id) for a in session.scalars(query)
                ]

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
                    "categories": flt.category_options(session, user_id, lang),
                    "owners": flt.owner_options(session, user_id),
                    "tag_groups": flt.tag_groups(session, user_id=user_id, lang=lang),
                    "periods": flt.period_options(session, user_id, lang),
                    "statuses": flt.status_options(session, user_id, lang),
                    "period_names": {k: i18n.label("period", k, lang) for k in flt.PERIODS},
                    "link": link,
                    "nav": "all",
                    "active_cat": f.cat,
                    "active_draft": bool(f.draft),
                    "scan": scan_progress(user_id),
                    "zone_counts": flt.zone_counts(session, user_id),
                    "removed_notices": _removed_notices(session, user_id),
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
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        # Видимость — та же, что везде: избранное можно ставить только на то, что
        # человек вправе видеть. Иначе по 200/404 виден факт существования чужой
        # личной карточки, а её имя потом утекает отметкой об удалении.
        mine = art is not None and art.owner_user_id is not None and art.owner_user_id == user_id
        if art is None or not (art.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
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
    lang = getattr(request.state, "lang", "en")
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
        # request обязателен: карточка считает права (владелец/администратор) по
        # request.state. Без него шаблон падал UndefinedError на каждой карточке.
        html = "".join(
            tmpl.render(
                it=_card(session, a, [], fav_ids, lang, user_id), next_path="/", request=request
            )
            for a in arts
        )
        max_id = arts[-1].id if arts else after
        total = _counts(session, user_id)["artifacts"]
    return JSONResponse({"html": html, "total": total, "max_id": max_id, "count": len(arts)})


_CAT_STOP = {"и", "для", "по", "the", "and", "for", "of", "или", "с", "на"}


def _auto_category(session, art: Artifact, user_id: int | None) -> int | None:
    """Сама подобрать ЛИЧНУЮ папку новому инструменту: у какой из личных папок
    этого человека слова из названия встречаются в тексте карточки (имя,
    описание, направление, теги). Общие (админские) папки не трогаем — туда
    карточку кладут руками, да и новая карточка пока личная. Не угадали или
    личных папок нет — оставляем без папки, человек переложит перетаскиванием."""
    if user_id is None:
        return None
    cats = session.scalars(
        select(Category).where(Category.owner_user_id == user_id)
    ).all()
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
    """Зона карточки: общая, если она расшарена (shared); иначе частная —
    видит только владелец."""
    return "common" if a.shared else "private"


def _artifact_categories(session, artifact_id: int, user_id: int | None, lang: str) -> list[dict]:
    """Папки карточки, которые вправе видеть ЭТОТ человек: общие + свои личные.
    Чужое личное членство (кто-то положил общую карточку в свою личную папку)
    другим не показываем. Сначала общие, потом свои личные."""
    rows = session.scalars(
        select(Category)
        .join(ArtifactCategory, ArtifactCategory.category_id == Category.id)
        .where(
            ArtifactCategory.artifact_id == artifact_id,
            Category.id.in_(catperm.visible_category_ids(user_id)),
        )
        .order_by(Category.owner_user_id.is_not(None), Category.position, Category.name)
    ).all()
    return [
        {
            "id": c.id,
            "name": catnames.label(c.names_json, c.name, lang),
            "icon": c.icon,
            "color": caticons.category_color(c.id),
            "owned": c.owner_user_id is not None,
        }
        for c in rows
    ]


def _card(
    session,
    a: Artifact,
    reasons: list[str],
    fav_ids: set[int] = frozenset(),
    lang: str = "en",
    user_id: int | None = None,
) -> dict:
    purpose, _score = pur.detect_for(session, a.id, a.name)
    return {
        "categories": _artifact_categories(session, a.id, user_id, lang),
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
        # Владелец и «общий» — чтобы шаблон решил, показывать ли кнопки «в общее»
        # / «снять» / «удалить». Права считает шаблон по request.state.
        "owner_id": a.owner_user_id,
        "shared": a.shared,
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
        lang = getattr(request.state, "lang", "en")
        a = session.get(Artifact, artifact_id)
        if a is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        # Зона: чужое частное не показываем даже по прямой ссылке. «Не найдена»,
        # а не «нельзя» — незачем подтверждать, что такая карточка существует.
        # Видно, если карточка общая или этот человек — её владелец.
        mine = a.owner_user_id is not None and a.owner_user_id == user_id
        if not (a.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))

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
                "categories": flt.category_options(session, user_id, lang),
                # Папки, в которых карточка сейчас лежит (видимые этому человеку) —
                # для раздела «Папки» на странице карточки: убрать/добавить.
                "art_categories": _artifact_categories(session, a.id, user_id, lang),
                "art_id": a.id,
                # Можно ли класть эту карточку в ОБЩИЕ папки: только общую (shared)
                # и только администратору — общие папки настраивает он. Личные —
                # всегда свои.
                "can_file_shared": a.shared and getattr(request.state, "is_owner", False),
                "active_cat": "",
                "active_draft": False,
            },
        )


@router.get("/dev", response_class=HTMLResponse)
def dev_page(request: Request) -> HTMLResponse:
    """Живой справочник дизайн-языка: токены цвета, шрифты, скругления, кнопки,
    поля, иконки, карточки, меню — на одном экране. Чтобы дизайн можно было
    «вынести» и держать единым: правишь набор — сверяешься здесь."""
    return templates.TemplateResponse(request, "dev.html", {"nav": "dev"})


@router.post("/artifact/{artifact_id}/category")
def set_category(
    request: Request,
    artifact_id: int,
    cat: Annotated[str, Form()] = "",
    op: Annotated[str, Form()] = "add",
    next: Annotated[str, Form()] = "/",
) -> Response:
    """Положить карточку в папку (op=add) или вынуть (op=remove). Членство —
    многие-ко-многим: одна карточка бывает и в общей папке, и в личных.

    Права: в ОБЩУЮ папку раскладывает владелец карточки или администратор, и
    только общую (shared) карточку; в СВОЮ ЛИЧНУЮ папку любой кладёт любую
    карточку, которую вправе видеть; в чужую личную — нельзя (её и не видно)."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        # Видимость карточки: чужое частное — «не найдено», чтобы не подтверждать,
        # что она существует.
        mine = art is not None and art.owner_user_id is not None and art.owner_user_id == user_id
        if art is None or not (art.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        category = session.get(Category, int(cat)) if cat.isdigit() else None
        # Чужую личную папку не подтверждаем существованием — «не найдено».
        if category is None or not catperm.can_view(category, user_id):
            raise HTTPException(404, i18n.msg(request, "err.category_not_found"))
        is_admin = getattr(request.state, "is_owner", False)
        if not catperm.can_file(art, category, user_id, is_admin):
            raise HTTPException(403, i18n.msg(request, "err.categorize_forbidden"))

        existing = session.scalar(
            select(ArtifactCategory).where(
                ArtifactCategory.artifact_id == art.id,
                ArtifactCategory.category_id == category.id,
            )
        )
        if op == "remove":
            changed = existing is not None
            if existing is not None:
                session.delete(existing)
            member = False
        else:  # add (по умолчанию — перетаскивание в папку)
            changed = existing is None
            if existing is None:
                session.add(ArtifactCategory(artifact_id=art.id, category_id=category.id))
            member = True

    if "application/json" in request.headers.get("accept", ""):
        # changed=false — членство уже было таким: клиент не двигает счётчики
        # (иначе повторный бросок на свёрнутую в «+N» папку задвоил бы счёт).
        return JSONResponse({"ok": True, "cat": int(cat), "member": member, "changed": changed})
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


@router.post("/artifact/{artifact_id}/visibility")
def toggle_visibility(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Переключить зону карточки: частная (видна только владельцу) ↔ общая
    (видят все).

    Выложить в общее может ТОЛЬКО владелец — чужое личное администратор даже не
    видит, не то что делится им за человека. Снять с общего может владелец ИЛИ
    администратор: за общий каталог он отвечает. Владение при этом не меняется —
    снятая с общего карточка возвращается своему же владельцу."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        is_admin = getattr(request.state, "is_owner", False)
        is_owner = art.owner_user_id is not None and art.owner_user_id == user_id
        if art.shared:
            if not (is_owner or is_admin):
                raise HTTPException(403, i18n.msg(request, "err.unshare_owner_or_admin"))
            art.shared = False
            now_private = True
        else:
            if not is_owner:
                raise HTTPException(403, i18n.msg(request, "err.share_owner_only"))
            art.shared = True
            now_private = False

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            {"private": now_private, "zone": "private" if now_private else "common"}
        )
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


def _delete_artifact(session, art: Artifact, actor_user_id: int) -> None:
    """Убрать карточку из каталога совсем — со всеми хвостами.

    Кого предупредить: избранное — это ссылка, не копия, и тем, кто держал
    карточку у себя, нельзя дать ей исчезнуть молча. Плюс владелец, если удаляет
    не он (это администратор снял общую). Себя не уведомляем.
    """
    aid, name = art.id, art.name

    fav_users = set(
        session.scalars(select(Favorite.user_id).where(Favorite.artifact_id == aid)).all()
    )
    affected = set(fav_users)
    if art.owner_user_id is not None:
        affected.add(art.owner_user_id)
    affected.discard(actor_user_id)
    for uid in affected:
        session.add(RemovedNotice(user_id=uid, artifact_name=name))

    # Связанные строки удаляем сами: у части внешних ключей нет каскада, и без
    # этого база не даст удалить карточку. Историю изменений не теряем — только
    # отвязываем от исчезнувшей карточки.
    session.execute(text("DELETE FROM artifacts_fts WHERE rowid = :id"), {"id": aid})
    session.execute(sa_delete(Embedding).where(Embedding.artifact_id == aid))
    session.execute(sa_delete(ArtifactTag).where(ArtifactTag.artifact_id == aid))
    session.execute(sa_delete(TagSuppression).where(TagSuppression.artifact_id == aid))
    session.execute(sa_delete(UpstreamLink).where(UpstreamLink.artifact_id == aid))
    session.execute(sa_delete(Favorite).where(Favorite.artifact_id == aid))
    session.execute(sa_update(Change).where(Change.artifact_id == aid).values(artifact_id=None))

    # Метим репозиторий удалённым человеком и хороним его: иначе следующий скан
    # увидит его живым и соберёт карточку заново — «навсегда» было бы неправдой.
    from datetime import UTC, datetime

    repo = session.get(Repository, art.repository_id)
    if repo is not None:
        repo.user_removed = True
        repo.gone_at = datetime.now(UTC)

    session.delete(art)


@router.post("/artifact/{artifact_id}/delete")
def delete_artifact(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Удалить карточку. Может владелец — свою (любую); администратор — только
    общую (в чужое личное он не заглядывает, значит и не удаляет). Тем, у кого
    она была в избранном, останется отметка «удалено»."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    is_admin = getattr(request.state, "is_owner", False)
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        is_owner = art.owner_user_id is not None and art.owner_user_id == user_id
        if not (is_owner or (is_admin and art.shared)):
            raise HTTPException(403, i18n.msg(request, "err.delete_owner_or_admin"))
        _delete_artifact(session, art, user_id)

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "deleted": artifact_id})
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


def _removed_notices(session, user_id: int | None) -> list[dict]:
    """Непрочитанные отметки «у вас удалили карточку из избранного»."""
    if user_id is None:
        return []
    rows = session.scalars(
        select(RemovedNotice)
        .where(RemovedNotice.user_id == user_id, RemovedNotice.seen_at.is_(None))
        .order_by(RemovedNotice.removed_at.desc())
    ).all()
    return [{"id": r.id, "name": r.artifact_name} for r in rows]


@router.post("/notices/dismiss")
def dismiss_notices(request: Request) -> JSONResponse:
    """Закрыть отметки об удалённых карточках — все разом."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    from datetime import UTC, datetime

    with session_scope() as session:
        session.execute(
            sa_update(RemovedNotice)
            .where(RemovedNotice.user_id == user_id, RemovedNotice.seen_at.is_(None))
            .values(seen_at=datetime.now(UTC))
        )
    return JSONResponse({"ok": True})


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
    на месте и читается. Возвращает (ключ_ошибки, имя_источника) — ошибку
    переводит вызывающий на языке запроса. Сеть (список репозиториев) и всё
    остальное — уже в фоне, чтобы кнопка отвечала сразу."""
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src is None or src.owner_user_id != user_id:
            return ("scan.err.source_not_found", "")
        kind, token_enc, name = src.kind, src.token_enc, src.display_name

    if kind not in _GITEA_KINDS:
        return ("scan.err.gitea_only", "")
    if not token_enc:
        return ("scan.err.no_token", "")
    try:
        security.decrypt_secret(token_enc)
    except Exception:
        return ("scan.err.token_unreadable", "")
    return ("", name)


def launch_user_scan(user_id: int, source_id: int, source_name: str, lang: str = "en") -> None:
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
    task = asyncio.create_task(
        _run_user_scan(user_id, source_id, _SCANS[user_id], force=True, lang=lang)
    )
    _SCAN_TASKS.add(task)
    task.add_done_callback(_SCAN_TASKS.discard)


async def _run_user_scan(
    user_id: int,
    source_id: int,
    progress: dict | None = None,
    force: bool = False,
    lang: str = "en",
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
        setp(state="error", error=i18n.translate("scan.err.token_lost", lang))
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
                        # Новую карточку кладём личной владельцу (в общее выкладывает
                        # он сам кнопкой) и помечаем «новинкой». У уже существующей
                        # владельца, зону и категорию не трогаем — уважаем его выбор.
                        if result.startswith("created"):
                            art.hidden = False
                            art.owner_user_id = user_id
                            art.shared = False
                            art.is_new = True
                            auto_cid = _auto_category(session, art, user_id)
                            if auto_cid is not None:
                                session.add(
                                    ArtifactCategory(artifact_id=art.id, category_id=auto_cid)
                                )
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
                error=i18n.msg(request, "add.err.file_too_big", mb=MAX_MEDIA_BYTES // 1_000_000),
                source=src,
            )
        fd, name = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        tmp = Path(name)
        src = str(tmp)

    if not src:
        return _add_page(request, "start", error=i18n.msg(request, "add.err.need_input"))

    finder = Finder(github_token=settings.github_token)
    model = build_text_model() if settings.google_api_key else None
    try:
        result = await finder.find(src, model)
    except Exception as exc:
        return _add_page(
            request,
            "start",
            error=i18n.msg(request, "add.err.parse_failed", err=exc),
            source=source,
        )
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
        return _add_page(
            request, "refused", message=i18n.msg(request, "add.err.failed", err=exc), url=url, to=to
        )
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
        return _add_page(
            request, "refused", message=i18n.msg(request, "add.err.no_gitea_token"), url=url
        )

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

            # Личной у создателя — ДО первого коммита с этой карточкой: импорт
            # идёт в общий Gitea, и index_repository пометил бы её общей. Между
            # тем коммитом и AI-описанием (секунды) она висела бы видимой всем.
            art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
            art.owner_user_id = user_id
            art.shared = False
            session.commit()

            record_upstream(session, art.id, plan)
            await embed_artifact(session, embed_model, art)
            await tag_artifact(session, art, text_model)
            index_artifact_for_words(session, art)
            # Сама подобрать личную папку по смыслу — человеку останется поправить.
            auto_cid = _auto_category(session, art, user_id)
            if auto_cid is not None:
                session.add(ArtifactCategory(artifact_id=art.id, category_id=auto_cid))
            session.commit()
            card = {
                "id": art.id,
                "name": art.name,
                "owner": art.repository.owner,
                "summary_short": art.summary_short,
                "preview_url": preview_url(art),
            }
    except Exception as exc:
        return _add_page(
            request, "refused", message=i18n.msg(request, "add.err.failed", err=exc), url=url, to=to
        )
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
            owner_user_id=user_id,
            shared=False,
        )
        session.add(art)
        session.flush()
    else:
        art.name = (name or art.name)[:256]
        art.summary_short = summary or art.summary_short
        art.owner_user_id = user_id
        art.shared = False
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
        # Зону задаёт владелец черновика (или бесхозного — тогда он им и станет).
        if art is not None and art.owner_user_id in (user_id, None):
            art.owner_user_id = user_id
            art.shared = zone != "private"
    return RedirectResponse("/", status_code=303)
