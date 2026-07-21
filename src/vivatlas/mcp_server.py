"""MCP access for AI assistants.

One set of tools, two ways to connect:
  stdio           — a local MCP client launches us as a program
  streamable-http — a remote MCP client hits the /mcp address

Responses are deliberately short. On the other end a model with limited
memory reads them: extra text crowds out the useful. So we return fields, not
prose, and don't dump the whole documentation.

The tools only read. Nothing is written to Git or the database.
"""

import logging

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from vivatlas import changes as ch
from vivatlas import filters as flt
from vivatlas.ai import build_embedding_model, build_text_model
from vivatlas.db import session_scope
from vivatlas.models import Artifact, ArtifactTag, Repository, Tag
from vivatlas.recommender import NO_MATCH_THRESHOLD
from vivatlas.recommender import recommend as do_recommend
from vivatlas.search import Mode
from vivatlas.search import search as do_search

log = logging.getLogger(__name__)

mcp = FastMCP(
    "vivatlas",
    instructions=(
        "A catalogue of skills, design kits, and tools from personal "
        "Git repositories. Tells you what's available, what each thing "
        "does, and what to pick for a specific task. Read-only."
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
    """Find tools by query. Understands Russian and English, searches by
    meaning — you can ask in your own words.

    query: what you're looking for, e.g. "brand colours and fonts"
    limit: how many to return, max 20
    type: optional filter — design-kit, claude-skill, skill, project
    """
    limit = max(1, min(limit, MAX_LIMIT))
    model = build_embedding_model()
    try:
        with session_scope() as session:
            hits = await do_search(
                session, query, model, mode=Mode.BOTH, limit=limit, artifact_type=type or None
            )
            # MCP connects without sign-in, so it's anonymous — we return only
            # shared cards. Otherwise other people's private stuff would leak through it.
            visible = set(session.scalars(flt.visible_ids(None)))
            hits = [h for h in hits if h.artifact_id in visible]
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
    """Pick a tool for a task described in words.

    Returns the best option, fallbacks, what each one can't do, and why
    similar ones were rejected. If nothing fits — it says so: the proximity
    threshold decides that, not the model, so the answer can be trusted.

    task: the task in words, e.g. "style a landing page like Airbnb"
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
                        f"There's no suitable tool in the catalogue. Closest match "
                        f"{r.top_similarity:.2f} against a threshold of {NO_MATCH_THRESHOLD}. "
                        f"Don't invent a tool — it really isn't there."
                    ),
                    "suggestions": r.suggestions,
                }

            # MCP without sign-in is anonymous: strip everything non-shared from the
            # recommendations, or the names of others' private cards would leak through them.
            visible = set(session.scalars(flt.visible_ids(None)))

            def vis(o) -> bool:
                return o.artifact.id in visible

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
                "best": opt(r.best) if r.best and vis(r.best) else None,
                "alternatives": [opt(a) for a in r.alternatives if vis(a)],
                "rejected": [
                    {"name": x.artifact.name, "why_not": x.why_not} for x in r.rejected if vis(x)
                ],
                "chain": [
                    {"id": s.artifact.id, "name": s.artifact.name, "step": s.step}
                    for s in r.chain if vis(s)
                ],
            }
    finally:
        await em.aclose()
        await tm.aclose()


@mcp.tool()
def get_artifact(artifact_id: int) -> dict:
    """Full card for a tool: three levels of description, tags, where it came from.

    artifact_id: number from search_artifacts or recommend_artifact
    """
    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        # An anonymous user (MCP without sign-in) sees only shared cards. Others' private
        # stuff is as if it didn't exist: the same response as for a nonexistent number.
        if a is None or a.hidden or not a.shared:
            return {"error": f"card {artifact_id} not found"}

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
            # Honest about data quality: let the other side know what to trust.
            "notes": _quality_notes(a),
        }


def _quality_notes(a: Artifact) -> list[str]:
    notes = []
    if a.confidence < 0.5:
        notes.append("type determined with low confidence, check it yourself")
    if not a.summary_short:
        notes.append("no description")
    if a.summary_error:
        notes.append(f"description didn't generate: {a.summary_error[:80]}")
    return notes


@mcp.tool()
def list_artifacts(type: str = "", limit: int = 20) -> dict:
    """List of tools in the catalogue, optionally of a single type.

    type: design-kit, claude-skill, skill, project, unknown — or empty
    limit: max 20
    """
    limit = max(1, min(limit, MAX_LIMIT))
    with session_scope() as session:
        # Shared cards only: MCP without sign-in is anonymous.
        vis = flt.visible_ids(None)
        query = select(Artifact).where(Artifact.id.in_(vis)).order_by(Artifact.name)
        count_q = select(func.count()).select_from(Artifact).where(Artifact.id.in_(vis))
        if type:
            query = query.where(Artifact.artifact_type == type)
            count_q = count_q.where(Artifact.artifact_type == type)
        rows = session.scalars(query.limit(limit)).all()
        total = session.scalar(count_q)
        return {
            "total": total,
            "showing": len(rows),
            "items": [_brief(session, a) for a in rows],
        }


@mcp.tool()
def list_tags(limit: int = 30) -> dict:
    """All catalogue tags with the number of tools for each."""
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
    """What's in the catalogue at all: how much of what, from which repositories."""
    with session_scope() as session:
        # Anonymous (MCP without sign-in) — only shared cards in all counters.
        vis = flt.visible_ids(None)
        by_type = session.execute(
            select(Artifact.artifact_type, func.count())
            .where(Artifact.id.in_(vis))
            .group_by(Artifact.artifact_type)
            .order_by(func.count().desc())
        ).all()
        by_owner = session.execute(
            select(Repository.owner, func.count(Artifact.id))
            .join(Artifact, Artifact.repository_id == Repository.id)
            .where(Artifact.id.in_(vis), Repository.gone_at.is_(None))
            .group_by(Repository.owner)
            .order_by(func.count(Artifact.id).desc())
        ).all()
        return {
            "artifacts": session.scalar(
                select(func.count()).select_from(Artifact).where(Artifact.id.in_(vis))
            ),
            "described": session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.id.in_(vis), Artifact.summary_short != "")
            ),
            "by_type": {t: c for t, c in by_type},
            "by_owner": {o: c for o, c in by_owner},
            "note": "Public repositories only. Private ones aren't scanned.",
        }


@mcp.tool()
def list_recent_changes(days: int = 30, kind: str = "") -> dict:
    """What appeared, changed, or disappeared recently.

    days: over how many days
    kind: added | updated | removed | renamed — or empty
    """
    with session_scope() as session:
        events = ch.since(session, days=days)
        if kind:
            events = [e for e in events if e.kind == kind]
        return {
            "days": days,
            "total": len(events),
            "summary": ch.summary(session, days=days),
            "items": [
                {
                    "kind": e.kind,
                    "name": e.title,
                    "details": e.details,
                    "when": e.created_at.isoformat(),
                    "artifact_id": e.artifact_id,
                }
                for e in events[:MAX_LIMIT]
            ],
        }


@mcp.tool()
def find_stale_artifacts(days: int = 365) -> dict:
    """What hasn't been touched in a long time — candidates for removal.

    days: how many days counts as a long stretch
    """
    with session_scope() as session:
        items = ch.stale(session, days=days)
        oldest, newest = ch.oldest_and_newest(session)
        return {
            "threshold_days": days,
            "total": len(items),
            # An empty list needs explaining, otherwise it reads as a breakage.
            "note": (
                f"The oldest in the catalogue is {oldest} days, the newest {newest} days."
                if not items
                else ""
            ),
            "items": [
                {
                    "id": i.artifact.id,
                    "name": i.artifact.repository.full_name,
                    "days_untouched": i.days,
                    "why": i.reason,
                }
                for i in items[:MAX_LIMIT]
            ],
        }


def run_stdio() -> None:
    """For a local MCP client (stdio)."""
    mcp.run(transport="stdio")


def http_app():
    """For a remote MCP client — mounted into the main application."""
    return mcp.streamable_http_app()
