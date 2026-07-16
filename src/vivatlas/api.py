"""REST API."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from vivatlas import auth
from vivatlas.ai import build_embedding_model
from vivatlas.auth_web import router as auth_router
from vivatlas.db import session_scope
from vivatlas.mcp_server import http_app as mcp_http_app
from vivatlas.models import Artifact, ArtifactTag, Repository, ScanRun, Tag, TagSuppression
from vivatlas.search import Mode
from vivatlas.search import search as do_search
from vivatlas.settings_web import router as settings_router
from vivatlas.tagger import add_manual_tag, remove_tag
from vivatlas.web import BASE
from vivatlas.web import router as web_router

app = FastAPI(title="VivAtlas", version="0.1.0")

# Пути, открытые без входа. Всё остальное — за замком. Список короткий
# намеренно: что не здесь, то закрыто, а не наоборот.
_OPEN_PREFIXES = ("/static/", "/login", "/setup", "/logout", "/mcp-server")
_OPEN_EXACT = {"/health", "/favicon.png", "/apple-touch-icon.png", "/login/2fa"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Замок на всё. Не вошёл — на страницу входа, а не внутрь.

    MCP-сервер пока открыт: ChatGPT ходит без куки, ему нужен отдельный токен —
    это следующий шаг. Помечено, чтобы не забыть: за туннелем это дыра.
    """
    path = request.url.path
    if path in _OPEN_EXACT or path.startswith(_OPEN_PREFIXES):
        return await call_next(request)

    with session_scope() as session:
        user = auth.current_user(session, request)
        setup_needed = not auth.has_any_user(session)
        if user is not None:
            # Кладём простыми значениями, а не объект: сессия сейчас закроется,
            # и объект стал бы отвязанным. Шаблонам этого хватает.
            request.state.user_id = user.id
            request.state.user_name = user.display_name or user.email
            request.state.is_owner = user.is_owner

    if user is not None:
        return await call_next(request)

    # Программу ещё не настроили — веди к заведению хозяина.
    if setup_needed:
        return RedirectResponse("/setup", status_code=303)

    # Для страниц — на вход, запомнив, куда шли. Для API — честный 401, а не
    # переадресация: программа-клиент должна получить отказ, а не HTML.
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Нужно войти"}, status_code=401)
    nxt = f"?next={path}" if path != "/" else ""
    return RedirectResponse(f"/login{nxt}", status_code=303)


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(web_router)

# MCP для ChatGPT. Отдельным приложением: у него свой жизненный цикл, и
# смешивать его с обычными страницами нельзя.
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
def list_artifacts(type: str | None = None) -> dict:
    with session_scope() as session:
        query = select(Artifact).order_by(Artifact.name)
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
def get_artifact(artifact_id: int) -> dict:
    with session_scope() as session:
        a = session.get(Artifact, artifact_id)
        if a is None:
            raise HTTPException(404, "карточка не найдена")
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
    q: str,
    mode: Mode = Mode.BOTH,
    limit: int = 10,
    type: str | None = None,
) -> dict:
    model = build_embedding_model() if mode in (Mode.MEANING, Mode.BOTH) else None
    try:
        with session_scope() as session:
            hits = await do_search(session, q, model, mode=mode, limit=limit, artifact_type=type)
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


@app.get("/api/artifacts/{artifact_id}/tags")
def artifact_tags(artifact_id: int) -> dict:
    with session_scope() as session:
        if session.get(Artifact, artifact_id) is None:
            raise HTTPException(404, "карточка не найдена")
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
def add_tag_endpoint(artifact_id: int, slug: str) -> dict:
    with session_scope() as session:
        if session.get(Artifact, artifact_id) is None:
            raise HTTPException(404, "карточка не найдена")
        tag = add_manual_tag(session, artifact_id, slug)
        return {"ok": True, "slug": tag.slug, "source": "manual"}


@app.delete("/api/artifacts/{artifact_id}/tags/{slug}")
def remove_tag_endpoint(artifact_id: int, slug: str, reason: str = "") -> dict:
    """Удаляет тег и запрещает его возвращение при следующем сканировании."""
    with session_scope() as session:
        if session.get(Artifact, artifact_id) is None:
            raise HTTPException(404, "карточка не найдена")
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
