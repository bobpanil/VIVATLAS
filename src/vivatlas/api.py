"""REST API."""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, update

from vivatlas import auth, i18n, security
from vivatlas import filters as flt
from vivatlas.admin_web import router as admin_router
from vivatlas.ai import build_embedding_model
from vivatlas.auth_web import router as auth_router
from vivatlas.db import session_scope
from vivatlas.mcp_server import http_app as mcp_http_app
from vivatlas.models import Artifact, ArtifactTag, Repository, ScanRun, Source, Tag, TagSuppression
from vivatlas.search import Mode
from vivatlas.search import search as do_search
from vivatlas.settings_web import router as settings_router
from vivatlas.tagger import add_manual_tag, remove_tag
from vivatlas.web import BASE
from vivatlas.web import router as web_router

log = logging.getLogger(__name__)

# Daily background crawl of connected sources: new repositories arrive as
# cards and get flagged "new". We check once an hour, crawl no more than once
# a day. The last_auto_scan_at marker also acts as a lock between two servers
# (8710 and 8711): whoever sets it first does the crawl.
_AUTOSCAN_EVERY = timedelta(hours=24)
_AUTOSCAN_CHECK_SECONDS = 3600
_AUTOSCAN_WARMUP_SECONDS = 300

# Retry of failed AI summaries: more often than the daily crawl, since a quota
# hiccup should heal within the hour, not the day.
_RETRY_EVERY_SECONDS = 1800
_RETRY_WARMUP_SECONDS = 420


async def _autoscan_pass() -> None:
    from vivatlas.web import _scan_one_source

    now = datetime.now(UTC)
    edge = now - _AUTOSCAN_EVERY
    due = (Source.last_auto_scan_at.is_(None)) | (Source.last_auto_scan_at < edge)
    claimed: list[int] = []
    with session_scope() as session:
        # Both shared (admin, no owner) and personal sources that carry a token —
        # index_repository files each into the right zone from the source's owner.
        rows = session.execute(
            select(Source.id).where(Source.token_enc != "", due)
        ).all()
        for (sid,) in rows:
            # Claim the source atomically: set a fresh marker only if it's
            # still due. The other server sees a zero rowcount and skips it.
            res = session.execute(
                update(Source).where(Source.id == sid, due).values(last_auto_scan_at=now)
            )
            if res.rowcount:
                claimed.append(sid)
        session.commit()
    for sid in claimed:
        try:
            await _scan_one_source(sid, progress=None)  # quietly, no progress bar
        except Exception:
            log.exception("auto-scan of source %s failed", sid)


async def _autoscan_loop() -> None:
    await asyncio.sleep(_AUTOSCAN_WARMUP_SECONDS)  # don't burden startup itself
    while True:
        try:
            await _autoscan_pass()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto-scan: pass failed")
        await asyncio.sleep(_AUTOSCAN_CHECK_SECONDS)


async def _retry_loop() -> None:
    """Cards whose AI summary failed (a quota hiccup, usually) get another try on a
    short cadence — from the docs already stored, so no re-download and working cards
    are untouched. Separate from the daily crawl so a rate-limited batch heals within
    the hour instead of a day later."""
    await asyncio.sleep(_RETRY_WARMUP_SECONDS)
    while True:
        try:
            from vivatlas.web import retry_failed_summaries

            fixed = await retry_failed_summaries()
            if fixed:
                log.info("retry: filled in %d missing summaries", fixed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("retry: pass failed")
        await asyncio.sleep(_RETRY_EVERY_SECONDS)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # Without the secret key the door won't lock: it backs the signatures (2FA,
    # reset links) and the encryption of other users' tokens. The CLI `serve`
    # checks this, but launching straight from the ASGI object
    # (uvicorn vivatlas.api:app) would skip the check and bring up a half-
    # unlocked program. Fail at startup, not on the first password reset.
    security.require_secret()
    # Catch the DB schema up to the current code right at startup — the same as
    # `init-db` does. Otherwise new code on an old database would fail on missing
    # columns (which is what happened with avatar_preset). Idempotent: on an up-
    # to-date database — 0 steps. Do NOT swallow the error: a broken migration
    # should be seen at startup, not caught on the first request.
    from vivatlas.migrate import ensure_schema

    for step in ensure_schema():
        log.info("DB schema: %s", step)
    # Apply config edits from the admin panel on top of .env so that after a
    # restart the saved tokens/models take effect, not just those from the file.
    with contextlib.suppress(Exception):
        from vivatlas import runtime_settings
        from vivatlas.db import session_scope

        with session_scope() as s:
            runtime_settings.apply_config_overrides(s)
    tasks = [asyncio.create_task(_autoscan_loop()), asyncio.create_task(_retry_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="VivAtlas", version="0.1.0", lifespan=_lifespan)

# Paths open without sign-in. Everything else is behind the lock. The list is
# short deliberately: whatever isn't here is closed, not the other way round.
_OPEN_PREFIXES = (
    "/static/", "/login", "/setup", "/logout", "/forgot", "/reset", "/register", "/join",
    "/lang/", "/mcp-server",
)
_OPEN_EXACT = {"/health", "/favicon.png", "/apple-touch-icon.png", "/login/2fa"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Lock on everything. Not signed in — off to the sign-in page, not inside.

    The MCP server is open for now: ChatGPT comes without a cookie and needs its
    own token — that's the next step. Flagged so we don't forget: behind a tunnel it's a hole.
    """
    # Language — for all pages, including open ones (sign-in, reset): from it the
    # template sets lang/dir and substitutes strings. Set it before the lock check.
    request.state.lang = i18n.lang_from_request(request)
    request.state.dir = i18n.dir_for(request.state.lang)

    path = request.url.path
    if path in _OPEN_EXACT or path.startswith(_OPEN_PREFIXES):
        return await call_next(request)

    with session_scope() as session:
        user = auth.current_user(session, request)
        setup_needed = not auth.has_any_user(session)
        if user is not None:
            # Store as plain values, not the object: the session is about to close
            # and the object would become detached. This is enough for templates.
            request.state.user_id = user.id
            name = user.display_name or user.email
            request.state.user_name = name
            request.state.user_email = user.email
            request.state.is_owner = user.is_owner
            # The owner is always an admin; admins are promoted by the owner. Admin
            # powers (admin panel, managing shared content) key off this.
            request.state.is_admin = user.is_owner or user.is_admin
            # Initials for the avatar: from the first letters of the name's words, max two.
            parts = [p for p in name.replace(".", " ").split() if p]
            request.state.user_initials = (
                "".join(p[0] for p in parts[:2]).upper() or name[0].upper()
            )

    if user is not None:
        return await call_next(request)

    # The program isn't set up yet — lead to creating the owner.
    if setup_needed:
        return RedirectResponse("/setup", status_code=303)

    # For pages — to sign-in, remembering where we were headed. For the API — an
    # honest 401, not a redirect: a client program should get a rejection, not HTML.
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sign-in required"}, status_code=401)
    nxt = f"?next={path}" if path != "/" else ""
    return RedirectResponse(f"/login{nxt}", status_code=303)


class _RevalidatingStatic(StaticFiles):
    """Static files with mandatory revalidation. Without Cache-Control the browser
    caches app.css/js "by heuristic" and after edits shows the old layout (the user
    sees cruft that's no longer in the code). no-cache = "keep it, but ask the
    server every time": with an ETag that's a cheap 304 if the file hasn't changed,
    and a fresh response if it has. That way old CSS no longer sticks."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", _RevalidatingStatic(directory=str(BASE / "static")), name="static")
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(admin_router)
app.include_router(web_router)

# MCP for ChatGPT. As a separate application: it has its own lifecycle, and
# mixing it with the regular pages isn't allowed.
app.mount("/mcp-server", mcp_http_app())


@app.get("/health")
def health() -> dict:
    with session_scope() as session:
        repo_count = session.scalar(
            select(func.count()).select_from(Repository).where(Repository.gone_at.is_(None))
        )
        artifact_count = session.scalar(select(func.count()).select_from(Artifact))
        described = session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.summary_short != "")
        )
    return {
        "status": "ok",
        "repositories": repo_count,
        "artifacts": artifact_count,
        "described": described,
    }


@app.get("/api/artifacts")
def list_artifacts(request: Request, type: str | None = None) -> dict:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        query = (
            select(Artifact)
            .where(Artifact.id.in_(flt.visible_ids(user_id)))
            .order_by(Artifact.name)
        )
        if type:
            query = query.where(Artifact.artifact_type == type)
        rows = session.scalars(query).all()
        return {
            "total": len(rows),
            "items": [
                {
                    "id": a.id,
                    "name": a.name,
                    "repository": a.repository.full_name,
                    "type": a.artifact_type,
                    "confidence": a.confidence,
                    "summary_short": a.summary_short,
                    "has_preview": bool(a.preview_path),
                }
                for a in rows
            ],
        }


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(request: Request, artifact_id: int) -> dict:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        a = session.get(Artifact, artifact_id)
        if a is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        # Visible if the card is shared or this user is its owner.
        mine = a.owner_user_id is not None and a.owner_user_id == user_id
        if not (a.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        return {
            "id": a.id,
            "name": a.name,
            "repository": a.repository.full_name,
            "html_url": a.repository.html_url,
            "type": a.artifact_type,
            "confidence": a.confidence,
            "detect_reasons": a.detect_reasons,
            "anchor_path": a.anchor_path,
            "preview_path": a.preview_path,
            "file_count": a.file_count,
            "summary_short": a.summary_short,
            "summary_normal": a.summary_normal,
            "summary_technical": a.summary_technical,
            "summary_model": a.summary_model,
            "summary_error": a.summary_error,
            "source_commit": a.source_commit,
            "updated_at": a.updated_at,
        }


@app.get("/api/search")
async def search_endpoint(
    request: Request,
    q: str,
    mode: Mode = Mode.BOTH,
    limit: int = 10,
    type: str | None = None,
) -> dict:
    model = build_embedding_model() if mode in (Mode.MEANING, Mode.BOTH) else None
    try:
        with session_scope() as session:
            user_id = getattr(request.state, "user_id", None)
            visible = set(session.scalars(flt.visible_ids(user_id)))
            hits = await do_search(session, q, model, mode=mode, limit=limit, artifact_type=type)
            # Zone: we don't hand out others' private items even via the API.
            hits = [h for h in hits if h.artifact_id in visible]
            return {
                "query": q,
                "mode": mode,
                "total": len(hits),
                "items": [
                    {
                        "id": h.artifact_id,
                        "name": h.artifact.name,
                        "repository": h.artifact.repository.full_name,
                        "type": h.artifact.artifact_type,
                        "summary_short": h.artifact.summary_short,
                        "score": round(h.score, 5),
                        "reasons": h.reasons,
                    }
                    for h in hits
                ],
            }
    finally:
        if model:
            await model.aclose()


def _visible_or_404(session, artifact_id: int, user_id: int | None, lang: str = "en") -> Artifact:
    """The card, if this user is entitled to see it; otherwise 404 (not 403 —
    no need to confirm that someone else's private one exists). The same zone
    boundary as in visible_ids/get_artifact — tags mustn't be a hole around it."""
    a = session.get(Artifact, artifact_id)
    mine = a is not None and a.owner_user_id is not None and a.owner_user_id == user_id
    if a is None or not (a.shared or mine):
        raise HTTPException(404, i18n.translate("err.artifact_not_found", lang))
    return a


@app.get("/api/artifacts/{artifact_id}/tags")
def artifact_tags(request: Request, artifact_id: int) -> dict:
    with session_scope() as session:
        _visible_or_404(
            session,
            artifact_id,
            getattr(request.state, "user_id", None),
            getattr(request.state, "lang", "en"),
        )
        links = session.scalars(
            select(ArtifactTag).where(ArtifactTag.artifact_id == artifact_id)
        ).all()
        banned = session.scalars(
            select(TagSuppression).where(TagSuppression.artifact_id == artifact_id)
        ).all()
        return {
            "tags": [
                {
                    "slug": link.tag.slug,
                    "category": link.tag.category,
                    "source": link.source,
                    "confidence": link.confidence,
                    "origin": link.origin,
                    "manually_confirmed": link.manually_confirmed,
                }
                for link in links
            ],
            "suppressed": [{"slug": b.tag.slug, "reason": b.reason} for b in banned],
        }


@app.post("/api/artifacts/{artifact_id}/tags/{slug}")
def add_tag_endpoint(request: Request, artifact_id: int, slug: str) -> dict:
    with session_scope() as session:
        _visible_or_404(
            session,
            artifact_id,
            getattr(request.state, "user_id", None),
            getattr(request.state, "lang", "en"),
        )
        tag = add_manual_tag(session, artifact_id, slug)
        return {"ok": True, "slug": tag.slug, "source": "manual"}


@app.delete("/api/artifacts/{artifact_id}/tags/{slug}")
def remove_tag_endpoint(request: Request, artifact_id: int, slug: str, reason: str = "") -> dict:
    """Removes the tag and prevents its return on the next scan."""
    with session_scope() as session:
        _visible_or_404(
            session,
            artifact_id,
            getattr(request.state, "user_id", None),
            getattr(request.state, "lang", "en"),
        )
        remove_tag(session, artifact_id, slug, reason=reason)
        return {"ok": True, "slug": slug, "suppressed": True}


@app.get("/api/tags")
def list_tags() -> dict:
    with session_scope() as session:
        rows = session.execute(
            select(Tag.slug, Tag.category, func.count(ArtifactTag.id))
            .join(ArtifactTag, ArtifactTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(func.count(ArtifactTag.id).desc())
        ).all()
        return {
            "total": len(rows),
            "items": [{"slug": s, "category": c, "count": n} for s, c, n in rows],
        }


@app.get("/api/stats")
def stats() -> dict:
    with session_scope() as session:
        by_type = session.execute(
            select(Artifact.artifact_type, func.count())
            .group_by(Artifact.artifact_type)
            .order_by(func.count().desc())
        ).all()
        failed = session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.summary_error.is_not(None))
        )
        return {
            "by_type": {t: c for t, c in by_type},
            "summary_failures": failed,
        }


@app.get("/api/repositories")
def list_repositories() -> dict:
    with session_scope() as session:
        rows = session.scalars(
            select(Repository)
            .where(Repository.gone_at.is_(None))
            .order_by(Repository.owner, Repository.name)
        ).all()
        return {
            "total": len(rows),
            "items": [
                {
                    "id": r.id,
                    "full_name": r.full_name,
                    "owner": r.owner,
                    "name": r.name,
                    "description": r.description,
                    "size_kb": r.size_kb,
                    "html_url": r.html_url,
                    "updated_at": r.remote_updated_at,
                }
                for r in rows
            ],
        }


@app.get("/api/scan-runs")
def list_scan_runs() -> dict:
    with session_scope() as session:
        rows = session.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(20)).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "status": r.status,
                    "repos_seen": r.repos_seen,
                    "repos_added": r.repos_added,
                    "repos_updated": r.repos_updated,
                    "repos_gone": r.repos_gone,
                    "repos_skipped_private": r.repos_skipped_private,
                    "error": r.error,
                }
                for r in rows
            ]
        }
