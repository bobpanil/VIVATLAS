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

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from vivatlas import changes as ch
from vivatlas import filters as flt
from vivatlas.ai import build_embedding_model, build_text_model
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.mcp_oauth import SCOPE
from vivatlas.models import Artifact, ArtifactTag, Repository, Tag, User
from vivatlas.recommender import NO_MATCH_THRESHOLD
from vivatlas.recommender import recommend as do_recommend
from vivatlas.search import Mode
from vivatlas.search import search as do_search

log = logging.getLogger(__name__)

MAX_LIMIT = 20

_INSTRUCTIONS = (
    "A per-user catalogue of skills, design kits, and tools from Git repositories. "
    "Signed in over OAuth you see your own private cards plus shared ones and can add "
    "tools and edit or file cards; connected anonymously you see only shared cards, "
    "read-only. When unsure what exists, list_folders / catalog_overview first."
)


def _build_mcp() -> FastMCP:
    """OAuth-enabled when a public URL is configured (so ChatGPT can connect as a
    specific user); otherwise the original anonymous, shared-only, read-only server."""
    if not settings.public_url:
        return FastMCP("vivatlas", instructions=_INSTRUCTIONS)

    from mcp.server.auth.settings import (
        AuthSettings,
        ClientRegistrationOptions,
        RevocationOptions,
    )

    from vivatlas.mcp_oauth import provider

    base = settings.public_url.rstrip("/")
    return FastMCP(
        "vivatlas",
        instructions=_INSTRUCTIONS,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=f"{base}/mcp-server",  # type: ignore[arg-type]
            resource_server_url=f"{base}/mcp-server/mcp",  # type: ignore[arg-type]
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )


mcp = _build_mcp()


def _caller_user_id() -> int | None:
    """The signed-in user for this tool call (the OAuth token's subject), or None when
    the connection is anonymous."""
    tok = get_access_token()
    if tok is None or not tok.subject:
        return None
    try:
        return int(tok.subject)
    except (TypeError, ValueError):
        return None


def _require_user() -> int:
    uid = _caller_user_id()
    if uid is None:
        raise ValueError("Sign in first: writing needs an OAuth connection (anonymous is read-only).")
    return uid


def _is_admin(session, uid: int | None) -> bool:
    if uid is None:
        return False
    u = session.get(User, uid)
    return bool(u and (u.is_owner or u.is_admin))


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
            visible = set(session.scalars(flt.visible_ids(_caller_user_id())))
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
            visible = set(session.scalars(flt.visible_ids(_caller_user_id())))

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
    uid = _caller_user_id()
    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        # Shared to everyone, or private to the signed-in caller. Anyone else's private
        # card is as if it didn't exist — the same response as a nonexistent number.
        mine = a is not None and a.owner_user_id is not None and a.owner_user_id == uid
        if a is None or a.hidden or not (a.shared or mine):
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
        vis = flt.visible_ids(_caller_user_id())
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
        vis = flt.visible_ids(_caller_user_id())
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
        # MCP is unauthenticated: only ever surface shared cards (user_id=_caller_user_id()), never
        # anyone's private ones — same boundary as the rest of the MCP tools.
        events = ch.since(session, days=days, user_id=_caller_user_id())
        if kind:
            events = [e for e in events if e.kind == kind]
        return {
            "days": days,
            "total": len(events),
            "summary": ch.summary(session, days=days, user_id=_caller_user_id()),
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
        items = ch.stale(session, days=days, user_id=_caller_user_id())
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


@mcp.tool()
async def add_to_library(url: str, title: str = "", shared: bool = False) -> dict:
    """Add a tool to your library from a link — a GitHub repo or any web page. It's
    processed in the background (summarised, tagged, filed), so this returns at once.

    url: the link to add
    title: optional title (otherwise taken from the page/repo)
    shared: true to put it in the shared catalogue; default false = your private zone
    """
    uid = _require_user()
    from vivatlas.web import ext_capture

    res = await ext_capture(url.strip(), title.strip(), "", uid, shared)
    return {"status": "processing", "url": url.strip(), "shared": shared, **res}


@mcp.tool()
def edit_card(
    artifact_id: int,
    name: str = "",
    type: str = "",
    summary_short: str = "",
    summary_normal: str = "",
    summary_technical: str = "",
) -> dict:
    """Edit one of your cards — only the non-empty fields change. Allowed on a card you
    own, or (as an admin) on a shared one.

    artifact_id: the card to edit
    name / type: rename / re-type (optional)
    summary_short / summary_normal / summary_technical: the three descriptions (optional)
    """
    uid = _require_user()
    from vivatlas.search import index_artifact_for_words

    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        if a is None:
            return {"error": f"card {artifact_id} not found"}
        mine = a.owner_user_id is not None and a.owner_user_id == uid
        if not (mine or (_is_admin(session, uid) and a.shared)):
            return {"error": "not allowed to edit this card"}
        if name.strip():
            a.name = name.strip()[:200]
        if type.strip():
            a.artifact_type = type.strip()[:40]
        if summary_short.strip():
            a.summary_short = summary_short.strip()
        if summary_normal.strip():
            a.summary_normal = summary_normal.strip()
        if summary_technical.strip():
            a.summary_technical = summary_technical.strip()
        a.summary_error = None
        a.summary_model = "manual"
        index_artifact_for_words(session, a)
        return {
            "ok": True,
            "id": a.id,
            "name": f"{a.repository.owner}/{a.name}",
            "type": a.artifact_type,
        }


@mcp.tool()
def list_folders() -> dict:
    """Your folders (id + name) for filing cards — shared folders plus your personal
    ones."""
    uid = _require_user()
    from vivatlas.models import Category

    with session_scope() as session:
        cats = (
            session.query(Category)
            .filter((Category.owner_user_id == uid) | (Category.owner_user_id.is_(None)))
            .order_by(Category.owner_user_id.isnot(None), Category.position, Category.name)
            .all()
        )
        return {
            "items": [
                {"id": c.id, "name": c.name, "shared": c.owner_user_id is None} for c in cats
            ]
        }


@mcp.tool()
def file_card(artifact_id: int, folder_id: int, op: str = "add") -> dict:
    """Put a card into (or take it out of) one of your folders.

    artifact_id: the card
    folder_id: the folder (from list_folders)
    op: "add" (default) or "remove"
    """
    uid = _require_user()
    from vivatlas import categories as catperm
    from vivatlas.models import ArtifactCategory, Category

    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        cat = session.get(Category, folder_id)
        if a is None or cat is None or not catperm.can_view(cat, uid):
            return {"error": "card or folder not found"}
        if not catperm.can_file(a, cat, uid, _is_admin(session, uid)):
            return {"error": "not allowed to file this card here"}
        link = (
            session.query(ArtifactCategory)
            .filter_by(artifact_id=a.id, category_id=cat.id)
            .first()
        )
        if op == "remove":
            if link:
                session.delete(link)
            return {"ok": True, "op": "remove", "artifact_id": a.id, "folder_id": cat.id}
        if not link:
            session.add(ArtifactCategory(artifact_id=a.id, category_id=cat.id))
        return {"ok": True, "op": "add", "artifact_id": a.id, "folder_id": cat.id}


def run_stdio() -> None:
    """For a local MCP client (stdio)."""
    mcp.run(transport="stdio")


def http_app():
    """For a remote MCP client — mounted into the main application."""
    return mcp.streamable_http_app()
