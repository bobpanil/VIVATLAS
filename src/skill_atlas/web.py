"""Страницы для человека. API для программ живёт в api.py."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from skill_atlas.ai import build_embedding_model, build_text_model
from skill_atlas.db import session_scope
from skill_atlas.models import Artifact, ArtifactTag, TagSuppression
from skill_atlas.recommender import NO_MATCH_THRESHOLD
from skill_atlas.recommender import recommend as do_recommend
from skill_atlas.search import Mode
from skill_atlas.search import search as do_search

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
async def index(request: Request, q: str = "", type: str = "") -> HTMLResponse:
    model = build_embedding_model() if q else None
    try:
        with session_scope() as session:
            counts = _counts(session)

            if q:
                hits = await do_search(
                    session, q, model, mode=Mode.BOTH, limit=60, artifact_type=type or None
                )
                items = [
                    {
                        "id": h.artifact.id,
                        "name": h.artifact.name,
                        "owner": h.artifact.repository.owner,
                        "type": h.artifact.artifact_type,
                        "summary_short": h.artifact.summary_short,
                        "preview_url": preview_url(h.artifact),
                        "reasons": h.reasons,
                    }
                    for h in hits
                ]
            else:
                query = select(Artifact).order_by(Artifact.name)
                if type:
                    query = query.where(Artifact.artifact_type == type)
                items = [
                    {
                        "id": a.id,
                        "name": a.name,
                        "owner": a.repository.owner,
                        "type": a.artifact_type,
                        "summary_short": a.summary_short,
                        "preview_url": preview_url(a),
                        "reasons": [],
                    }
                    for a in session.scalars(query)
                ]

            return templates.TemplateResponse(
                request,
                "index.html",
                {"items": items, "q": q, "type": type, "counts": counts},
            )
    finally:
        if model:
            await model.aclose()


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

        return templates.TemplateResponse(
            request,
            "artifact.html",
            {
                "a": a,
                "tags": tags,
                "suppressed": suppressed,
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
