"""Страницы для человека. API для программ живёт в api.py."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from skill_atlas import changes as ch
from skill_atlas import filters as flt
from skill_atlas.ai import build_embedding_model, build_text_model
from skill_atlas.db import session_scope
from skill_atlas.models import Artifact, ArtifactTag, TagSuppression, UpstreamLink
from skill_atlas.recommender import NO_MATCH_THRESHOLD
from skill_atlas.recommender import recommend as do_recommend
from skill_atlas.search import Mode
from skill_atlas.search import search as do_search
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
    model = build_embedding_model() if q else None
    try:
        with session_scope() as session:
            counts = _counts(session)

            if q:
                # Поиск уже отобрал по смыслу — фильтры применяем к его выдаче,
                # а не к базе: иначе порядок по близости потеряется.
                hits = await do_search(session, q, model, mode=Mode.BOTH, limit=200)
                allowed = {a for a in session.scalars(flt.apply(select(Artifact.id), f))}
                items = [_card(h.artifact, h.reasons) for h in hits if h.artifact.id in allowed][
                    :60
                ]
            else:
                query = flt.apply(select(Artifact), f).order_by(Artifact.name)
                items = [_card(a, []) for a in session.scalars(query)]

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
                },
            )
    finally:
        if model:
            await model.aclose()


def _card(a: Artifact, reasons: list[str]) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "owner": a.repository.owner,
        "type": a.artifact_type,
        "summary_short": a.summary_short,
        "preview_url": preview_url(a),
        "reasons": reasons,
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
                "oldest": oldest,
                "newest": newest,
                "counts": _counts(session),
            },
        )
