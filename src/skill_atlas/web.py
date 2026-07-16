"""Страницы для человека. API для программ живёт в api.py."""

import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from skill_atlas import changes as ch
from skill_atlas import filters as flt
from skill_atlas import purposes as pur
from skill_atlas.ai import build_embedding_model, build_text_model
from skill_atlas.config import settings
from skill_atlas.db import session_scope
from skill_atlas.embeddings import embed_artifact
from skill_atlas.finder import MAX_MEDIA_BYTES, Finder, looks_like_link
from skill_atlas.import_run import execute, record_upstream
from skill_atlas.importer import GitHubFetcher, ImportError_, plan_import
from skill_atlas.indexer import index_repository
from skill_atlas.models import Artifact, ArtifactTag, Repository, TagSuppression, UpstreamLink
from skill_atlas.providers import build_provider
from skill_atlas.recommender import NO_MATCH_THRESHOLD
from skill_atlas.recommender import recommend as do_recommend
from skill_atlas.search import Mode, index_artifact_for_words
from skill_atlas.search import search as do_search
from skill_atlas.tagger import tag_artifact
from skill_atlas.upstream import STATUS_NAMES

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()

TYPE_NAMES = {
    "design-kit": "дизайн-набор",
    "claude-skill": "скилл Claude",
    "skill": "скилл",
    "claude-command": "команда",
    "claude-agent": "агент",
    "mcp-server": "MCP-сервер",
    "plugin": "плагин",
    "project": "проект",
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


def _counts(session) -> dict:
    by_type = session.execute(
        select(Artifact.artifact_type, func.count())
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    return {
        "artifacts": session.scalar(select(func.count()).select_from(Artifact)) or 0,
        "tags": session.scalar(select(func.count(func.distinct(ArtifactTag.tag_id)))) or 0,
        "by_type": by_type,
    }


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = "",
    type: str = "",
    tag: str = "",
    days: str = "",
    status: str = "",
    owner: str = "",
) -> HTMLResponse:
    f = flt.Filters(type=type, tag=tag, days=days, status=status, owner=owner)

    # Вставили ссылку в поиск — искать её среди названий бессмысленно: такого
    # текста в карточках нет и быть не может. Раньше это молча возвращало
    # пустоту. Теперь предлагаем то, чего человек и хотел, — разобрать её.
    link = looks_like_link(q)

    model = build_embedding_model() if q and not link else None
    try:
        with session_scope() as session:
            counts = _counts(session)

            if link:
                items = []
            elif q:
                # Поиск уже отобрал по смыслу — фильтры применяем к его выдаче,
                # а не к базе: иначе порядок по близости потеряется.
                hits = await do_search(session, q, model, mode=Mode.BOTH, limit=200)
                allowed = {a for a in session.scalars(flt.apply(select(Artifact.id), f))}
                items = [
                    _card(session, h.artifact, h.reasons) for h in hits if h.artifact.id in allowed
                ][:60]
            else:
                query = flt.apply(select(Artifact), f).order_by(Artifact.name)
                items = [_card(session, a, []) for a in session.scalars(query)]

            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "items": items,
                    "q": q,
                    "f": f,
                    "counts": counts,
                    "types": flt.type_options(session),
                    "owners": flt.owner_options(session),
                    "tag_groups": flt.tag_groups(session),
                    "periods": flt.period_options(session),
                    "statuses": flt.status_options(session),
                    "period_names": {k: v[0] for k, v in flt.PERIODS.items()},
                    "link": link,
                    "nav": "all",
                },
            )
    finally:
        if model:
            await model.aclose()


def _card(session, a: Artifact, reasons: list[str]) -> dict:
    purpose, _score = pur.detect_for(session, a.id, a.name)
    return {
        "id": a.id,
        "name": a.name,
        "owner": a.repository.owner,
        "type": a.artifact_type,
        "summary_short": a.summary_short,
        "preview_url": preview_url(a),
        "reasons": reasons,
        "author": author_of(session, a),
        "created": a.repository.remote_created_at,
        "updated": a.repository.remote_updated_at,
        "purpose": purpose,
    }


@router.get("/a/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: int) -> HTMLResponse:
    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        if a is None:
            raise HTTPException(404, "карточка не найдена")

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
                "counts": _counts(session),
            },
        )


@router.get("/recommend", response_class=HTMLResponse)
async def recommend_page(request: Request, task: str = "") -> HTMLResponse:
    result = None
    embedding_model = text_model = None
    try:
        if task.strip():
            embedding_model = build_embedding_model()
            text_model = build_text_model()
            with session_scope() as session:
                result = await do_recommend(session, task, embedding_model, text_model)
                counts = _counts(session)
        else:
            with session_scope() as session:
                counts = _counts(session)

        return templates.TemplateResponse(
            request,
            "recommend.html",
            {
                "r": result,
                "task": task,
                "counts": counts,
                "threshold": NO_MATCH_THRESHOLD,
                "nav": "recommend",
            },
        )
    finally:
        if embedding_model:
            await embedding_model.aclose()
        if text_model:
            await text_model.aclose()


@router.get("/changes", response_class=HTMLResponse)
def changes_page(request: Request, kind: str = "", stale: str = "") -> HTMLResponse:
    with session_scope() as session:
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
                "counts": _counts(session),
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
        return templates.TemplateResponse(
            request,
            "add.html",
            {"step": step, "counts": _counts(session), "nav": "add", **extra},
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
    to: Annotated[str, Form()] = "skills-lib",
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
    to: Annotated[str, Form()] = "skills-lib",
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
            session.commit()
            artifact_id = art.id
    except Exception as exc:
        return _add_page(request, "refused", message=f"Не получилось: {exc}", url=url, to=to)
    finally:
        await provider.aclose()
        await text_model.aclose()
        await embed_model.aclose()

    return RedirectResponse(f"/artifact/{artifact_id}", status_code=303)
