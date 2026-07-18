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

# Ежедневный фоновый обход подключённых источников: новые репозитории приходят
# карточками и помечаются «новинкой». Проверяем раз в час, обходим не чаще
# суток. Метка last_auto_scan_at служит и замком между двумя серверами (8710 и
# 8711): кто первым её проставит, тот и обходит.
_AUTOSCAN_EVERY = timedelta(hours=24)
_AUTOSCAN_CHECK_SECONDS = 3600
_AUTOSCAN_WARMUP_SECONDS = 300


async def _autoscan_pass() -> None:
    from vivatlas.web import _run_user_scan

    now = datetime.now(UTC)
    edge = now - _AUTOSCAN_EVERY
    due = (Source.last_auto_scan_at.is_(None)) | (Source.last_auto_scan_at < edge)
    claimed: list[tuple[int, int]] = []
    with session_scope() as session:
        rows = session.execute(
            select(Source.id, Source.owner_user_id).where(
                Source.owner_user_id.is_not(None), Source.token_enc != "", due
            )
        ).all()
        for sid, uid in rows:
            # Забираем источник атомарно: ставим свежую метку только если всё
            # ещё пора. Второй сервер увидит нулевой rowcount и пропустит.
            res = session.execute(
                update(Source).where(Source.id == sid, due).values(last_auto_scan_at=now)
            )
            if res.rowcount:
                claimed.append((uid, sid))
        session.commit()
    for uid, sid in claimed:
        try:
            await _run_user_scan(uid, sid, progress=None)  # тихо, без полосы
        except Exception:
            log.exception("авто-скан источника %s не удался", sid)


async def _autoscan_loop() -> None:
    await asyncio.sleep(_AUTOSCAN_WARMUP_SECONDS)  # не грузим сам старт
    while True:
        try:
            await _autoscan_pass()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("авто-скан: проход не удался")
        await asyncio.sleep(_AUTOSCAN_CHECK_SECONDS)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # Без главного ключа дверь не запереть: на нём держатся подписи (2FA, ссылки
    # сброса) и шифрование чужих токенов. CLI `serve` это проверяет, но запуск
    # прямо по ASGI-объекту (uvicorn vivatlas.api:app) шёл бы мимо проверки и
    # поднимал бы наполовину незапертую программу. Падаем на старте, а не на
    # первом сбросе пароля.
    security.require_secret()
    # Наложить правки конфигурации из админки поверх .env, чтобы после
    # перезапуска действовали сохранённые токены/модели, а не только из файла.
    with contextlib.suppress(Exception):
        from vivatlas import runtime_settings
        from vivatlas.db import session_scope

        with session_scope() as s:
            runtime_settings.apply_config_overrides(s)
    task = asyncio.create_task(_autoscan_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="VivAtlas", version="0.1.0", lifespan=_lifespan)

# Пути, открытые без входа. Всё остальное — за замком. Список короткий
# намеренно: что не здесь, то закрыто, а не наоборот.
_OPEN_PREFIXES = (
    "/static/", "/login", "/setup", "/logout", "/forgot", "/reset", "/register", "/join",
    "/lang/", "/mcp-server",
)
_OPEN_EXACT = {"/health", "/favicon.png", "/apple-touch-icon.png", "/login/2fa"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Замок на всё. Не вошёл — на страницу входа, а не внутрь.

    MCP-сервер пока открыт: ChatGPT ходит без куки, ему нужен отдельный токен —
    это следующий шаг. Помечено, чтобы не забыть: за туннелем это дыра.
    """
    # Язык — для всех страниц, включая открытые (вход, сброс): по нему шаблон
    # ставит lang/dir и подставляет строки. Ставим до проверки замка.
    request.state.lang = i18n.lang_from_request(request)
    request.state.dir = i18n.dir_for(request.state.lang)

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
            name = user.display_name or user.email
            request.state.user_name = name
            request.state.user_email = user.email
            request.state.is_owner = user.is_owner
            # Инициалы для аватарки: по первым буквам слов имени, максимум две.
            parts = [p for p in name.replace(".", " ").split() if p]
            request.state.user_initials = (
                "".join(p[0] for p in parts[:2]).upper() or name[0].upper()
            )

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
app.include_router(admin_router)
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
        # Видно, если карточка общая или этот человек — её владелец.
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
            # Зона: чужое частное не отдаём даже через API.
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
    """Карточка, если этот человек вправе её видеть; иначе 404 (не 403 —
    незачем подтверждать, что чужая личная существует). Та же граница зон, что
    в visible_ids/get_artifact — теги не должны быть дырой мимо неё."""
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
    """Удаляет тег и запрещает его возвращение при следующем сканировании."""
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
