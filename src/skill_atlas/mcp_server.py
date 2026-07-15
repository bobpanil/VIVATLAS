"""Доступ из ChatGPT и Claude Code.

Один набор инструментов, два способа подключения:
  stdio           — Claude Code запускает нас как программу
  streamable-http — ChatGPT ходит по адресу /mcp

Ответы намеренно короткие. На той стороне их читает модель с ограниченной
памятью: лишний текст вытесняет полезный. Поэтому отдаём поля, а не прозу, и
не льём документацию целиком.

Инструменты только читают. Ничего не пишется ни в Git, ни в базу.
"""

import logging

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from skill_atlas.ai import build_embedding_model, build_text_model
from skill_atlas.db import session_scope
from skill_atlas.models import Artifact, ArtifactTag, Repository, Tag
from skill_atlas.recommender import NO_MATCH_THRESHOLD
from skill_atlas.recommender import recommend as do_recommend
from skill_atlas.search import Mode
from skill_atlas.search import search as do_search

log = logging.getLogger(__name__)

mcp = FastMCP(
    "skill-atlas",
    instructions=(
        "Каталог скиллов, дизайн-наборов и инструментов из личных "
        "Git-репозиториев. Отвечает, что есть в наличии, что каждая вещь "
        "делает и что взять под конкретную задачу. Только чтение."
    ),
)

MAX_LIMIT = 20


def _tags(session, artifact_id: int, limit: int = 8) -> list[str]:
    return list(
        session.scalars(
            select(Tag.slug)
            .join(ArtifactTag, ArtifactTag.tag_id == Tag.id)
            .where(ArtifactTag.artifact_id == artifact_id)
            .order_by(ArtifactTag.confidence.desc())
            .limit(limit)
        )
    )


def _brief(session, a: Artifact) -> dict:
    return {
        "id": a.id,
        "name": f"{a.repository.owner}/{a.name}",
        "type": a.artifact_type,
        "summary": a.summary_short,
        "tags": _tags(session, a.id, limit=5),
    }


@mcp.tool()
async def search_artifacts(query: str, limit: int = 5, type: str = "") -> dict:
    """Найти инструменты по запросу. Понимает русский и английский, ищет по
    смыслу — можно спрашивать своими словами.

    query: что ищем, например "фирменные цвета и шрифты"
    limit: сколько вернуть, максимум 20
    type: необязательный фильтр — design-kit, claude-skill, skill, project
    """
    limit = max(1, min(limit, MAX_LIMIT))
    model = build_embedding_model()
    try:
        with session_scope() as session:
            hits = await do_search(
                session, query, model, mode=Mode.BOTH, limit=limit, artifact_type=type or None
            )
            return {
                "query": query,
                "found": len(hits),
                "items": [
                    {**_brief(session, h.artifact), "why_found": ", ".join(h.reasons)} for h in hits
                ],
            }
    finally:
        await model.aclose()


@mcp.tool()
async def recommend_artifact(task: str) -> dict:
    """Подобрать инструмент под задачу, описанную словами.

    Возвращает лучший вариант, запасные, чего каждый не умеет, и почему
    отброшено похожее. Если подходящего нет — так и скажет: это решает порог
    близости, а не модель, поэтому ответу можно верить.

    task: задача словами, например "оформить лендинг в стиле Airbnb"
    """
    em = build_embedding_model()
    tm = build_text_model()
    try:
        with session_scope() as session:
            r = await do_recommend(session, task, em, tm)

            if r.no_match:
                return {
                    "task": task,
                    "no_suitable_tool": True,
                    "explanation": (
                        f"Подходящего инструмента в каталоге нет. Ближайшее совпадение "
                        f"{r.top_similarity:.2f} при пороге {NO_MATCH_THRESHOLD}. "
                        f"Не выдумывай инструмент — его правда нет."
                    ),
                    "suggestions": r.suggestions,
                }

            def opt(o) -> dict:
                return {
                    "id": o.artifact.id,
                    "name": f"{o.artifact.repository.owner}/{o.artifact.name}",
                    "why": o.why,
                    "limitations": o.limitations,
                }

            return {
                "task": task,
                "no_suitable_tool": False,
                "confidence": round(r.confidence, 2),
                "basis": r.basis,
                "best": opt(r.best) if r.best else None,
                "alternatives": [opt(a) for a in r.alternatives],
                "rejected": [{"name": x.artifact.name, "why_not": x.why_not} for x in r.rejected],
                "chain": [
                    {"id": s.artifact.id, "name": s.artifact.name, "step": s.step} for s in r.chain
                ],
            }
    finally:
        await em.aclose()
        await tm.aclose()


@mcp.tool()
def get_artifact(artifact_id: int) -> dict:
    """Полная карточка инструмента: три уровня описания, теги, откуда взят.

    artifact_id: номер из search_artifacts или recommend_artifact
    """
    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        if a is None:
            return {"error": f"карточки {artifact_id} нет"}

        links = session.scalars(
            select(ArtifactTag).where(ArtifactTag.artifact_id == artifact_id)
        ).all()
        return {
            "id": a.id,
            "name": f"{a.repository.owner}/{a.name}",
            "type": a.artifact_type,
            "type_confidence": a.confidence,
            "summary_short": a.summary_short,
            "summary_normal": a.summary_normal,
            "summary_technical": a.summary_technical,
            "tags": [
                {"slug": link.tag.slug, "source": link.source, "confidence": link.confidence}
                for link in links
            ],
            "files": a.file_count,
            "anchor_file": a.anchor_path,
            "url": a.repository.html_url,
            "commit": (a.source_commit or "")[:8],
            # Честно про качество данных: пусть та сторона знает, чему верить.
            "notes": _quality_notes(a),
        }


def _quality_notes(a: Artifact) -> list[str]:
    notes = []
    if a.confidence < 0.5:
        notes.append("тип определён неуверенно, проверь сам")
    if not a.summary_short:
        notes.append("описания нет")
    if a.summary_error:
        notes.append(f"описание не сгенерировалось: {a.summary_error[:80]}")
    return notes


@mcp.tool()
def list_artifacts(type: str = "", limit: int = 20) -> dict:
    """Список инструментов в каталоге, при желании одного типа.

    type: design-kit, claude-skill, skill, project, unknown — или пусто
    limit: максимум 20
    """
    limit = max(1, min(limit, MAX_LIMIT))
    with session_scope() as session:
        query = select(Artifact).order_by(Artifact.name)
        if type:
            query = query.where(Artifact.artifact_type == type)
        rows = session.scalars(query.limit(limit)).all()
        total = session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.artifact_type == type if type else True)
        )
        return {
            "total": total,
            "showing": len(rows),
            "items": [_brief(session, a) for a in rows],
        }


@mcp.tool()
def list_tags(limit: int = 30) -> dict:
    """Все теги каталога с числом инструментов у каждого."""
    with session_scope() as session:
        rows = session.execute(
            select(Tag.slug, func.count(ArtifactTag.id))
            .join(ArtifactTag, ArtifactTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(func.count(ArtifactTag.id).desc())
            .limit(limit)
        ).all()
        return {"items": [{"tag": s, "count": n} for s, n in rows]}


@mcp.tool()
def catalog_overview() -> dict:
    """Что вообще есть в каталоге: сколько чего, из каких репозиториев."""
    with session_scope() as session:
        by_type = session.execute(
            select(Artifact.artifact_type, func.count())
            .group_by(Artifact.artifact_type)
            .order_by(func.count().desc())
        ).all()
        by_owner = session.execute(
            select(Repository.owner, func.count())
            .where(Repository.gone_at.is_(None))
            .group_by(Repository.owner)
            .order_by(func.count().desc())
        ).all()
        return {
            "artifacts": session.scalar(select(func.count()).select_from(Artifact)),
            "described": session.scalar(
                select(func.count()).select_from(Artifact).where(Artifact.summary_short != "")
            ),
            "by_type": {t: c for t, c in by_type},
            "by_owner": {o: c for o, c in by_owner},
            "note": "Только открытые репозитории. Приватные не сканируются.",
        }


def run_stdio() -> None:
    """Для Claude Code."""
    mcp.run(transport="stdio")


def http_app():
    """Для ChatGPT — монтируется в основное приложение."""
    return mcp.streamable_http_app()
