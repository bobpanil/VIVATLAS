"""Settings behind the lock: for now — two-step verification.

Language, theme, and personal repositories will land here later. One page, and
its sections grow.
"""

import io
import json
import logging
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import func, select

from vivatlas import auth, avatars, caticons, catnames, i18n, runtime_settings, security
from vivatlas import twofactor, usericons
from vivatlas import categories as catperm
from vivatlas import filters as flt
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.models import Avatar, Category, OAuthToken, Source, User
from vivatlas.web import _counts

# Which hosts can be connected as your own source. Only Gitea works so far
# (Codeberg is the same Forgejo/Gitea). The rest are saved, and we'll add the
# crawl as the providers become ready.
SOURCE_KINDS = [
    ("gitea", "Gitea"),
    ("github", "GitHub"),
    ("gitlab", "GitLab"),
    ("bitbucket", "Bitbucket"),
    ("codeberg", "Codeberg"),
    ("git", "Other Git"),
]

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
# This module has its own template env — web.py globals don't reach here, so we
# register the category icons here too.
templates.env.globals["caticon"] = caticons.caticon_svg
router = APIRouter()


def _me(session, request: Request) -> User | None:
    return auth.current_user(session, request)


def _mask_token(enc: str, lang: str = "en") -> str:
    """A masked token. If it won't decrypt (the key changed) — don't crash the
    page; we honestly say it's unreadable."""
    if not enc:
        return ""
    try:
        return security.mask_secret(security.decrypt_secret(enc))
    except Exception:
        return i18n.translate("settings.token_unreadable", lang)


def _my_sources(session, user_id: int, lang: str = "en") -> list[dict]:
    """Your own private sources. The token goes out only masked."""
    rows = session.scalars(
        select(Source).where(Source.owner_user_id == user_id).order_by(Source.created_at)
    ).all()
    kinds = dict(SOURCE_KINDS)
    return [
        {
            "id": s.id,
            "kind_raw": s.kind,
            "kind": kinds.get(s.kind, s.kind),
            "display_name": s.display_name,
            "base_url": s.base_url,
            "has_token": bool(s.token_enc),
            "token_mask": _mask_token(s.token_enc, lang),
        }
        for s in rows
    ]


def _valid_url(u: str) -> bool:
    return u.startswith("http://") or u.startswith("https://")


# The browser-extension files, for the "Browser extension" tab's one-click download.
# Only the runtime files (not the multi-MB source art) go into the zip.
_EXT_FILES = [
    "manifest.json",
    "config.js",
    "popup.html",
    "popup.css",
    "popup.js",
    "README.md",
    "icons/icon16.png",
    "icons/icon48.png",
    "icons/icon128.png",
    "assets/logo-light.webp",
    "assets/logo-dark.webp",
    "assets/favicon2.svg",
]


def _extension_dir() -> Path | None:
    """Where the unpacked extension lives, across dev and Docker. In the image the
    Dockerfile copies it to /app/extension (the working dir); from a source checkout it
    sits at the repo root. VIVATLAS_EXTENSION_DIR overrides. None if not shipped."""
    candidates = []
    env = os.environ.get("VIVATLAS_EXTENSION_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "extension")
    candidates.append(Path(__file__).resolve().parents[2] / "extension")
    candidates.append(Path("/app/extension"))
    for c in candidates:
        if (c / "manifest.json").is_file():
            return c
    return None


def _ext_config_js(server_url: str) -> str:
    """config.js pre-wired to this server (json.dumps handles quoting/escaping)."""
    return (
        "'use strict';\n"
        "// Pre-wired to this VIVATLAS by the download in Settings — no 'enter server' step.\n"
        f"window.VIVATLAS_SERVER = {json.dumps(server_url)};\n"
    )


def _ext_manifest(path: Path, server_url: str, firefox: bool) -> str:
    """The manifest with this server granted at install (host_permissions), and the
    Firefox add-on id added for the Firefox build."""
    data = json.loads(path.read_text(encoding="utf-8"))
    host = server_url.rstrip("/") + "/*"
    hosts = list(data.get("host_permissions", []))
    if host not in hosts:
        hosts.append(host)
    data["host_permissions"] = hosts
    if firefox:
        data["browser_specific_settings"] = {
            "gecko": {"id": "vivatlas-clipper@vivatlas.app", "strict_min_version": "115.0"}
        }
    else:
        data.pop("browser_specific_settings", None)
    return json.dumps(data, indent=2)


@router.get("/settings/extension.zip")
def extension_zip(request: Request, browser: str = "chrome") -> Response:
    """Download the browser clipper, pre-wired to this server. `browser=firefox`
    adds the Firefox add-on id; otherwise a Chrome/Edge build. Loaded unpacked, it
    signs in from your browser's own VIVATLAS session — no separate extension login."""
    with session_scope() as session:
        if _me(session, request) is None:
            raise HTTPException(401, "Sign in first.")
        server_url = runtime_settings.site_url(session) or str(request.base_url).rstrip("/")
    ext = _extension_dir()
    if ext is None:
        raise HTTPException(404, "The extension files are not bundled with this server.")
    firefox = browser.lower() == "firefox"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in _EXT_FILES:
            p = ext / rel
            if not p.is_file():
                continue
            arc = "vivatlas-clipper/" + rel
            if rel == "manifest.json":
                z.writestr(arc, _ext_manifest(p, server_url, firefox))
            elif rel == "config.js":
                z.writestr(arc, _ext_config_js(server_url))
            else:
                z.write(p, arcname=arc)
    name = "vivatlas-clipper-firefox.zip" if firefox else "vivatlas-clipper.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _mcp_grants(session, user_id: int) -> list[dict]:
    """Apps (OAuth clients) that currently hold a live token for this user."""
    from vivatlas import mcp_oauth

    ids = [
        cid
        for (cid,) in session.query(OAuthToken.client_id)
        .filter(OAuthToken.user_id == user_id, OAuthToken.revoked_at.is_(None))
        .distinct()
    ]
    return [{"client_id": cid, "name": mcp_oauth.client_label(cid)} for cid in ids]


@router.post("/settings/mcp/revoke", response_class=HTMLResponse)
def mcp_revoke(request: Request, client_id: Annotated[str, Form()] = "") -> RedirectResponse:
    """Cut off an authorized app: revoke all of its tokens for this user."""
    with session_scope() as session:
        me = _me(session, request)
        session.query(OAuthToken).filter(
            OAuthToken.user_id == me.id, OAuthToken.client_id == client_id
        ).update({OAuthToken.revoked_at: datetime.now(UTC)})
    return RedirectResponse("/settings", status_code=303)


def _security_page(
    request: Request, session, me, error: str = "", **msgs
) -> HTMLResponse:
    """The full settings page — with an error/message or without. A single point
    to assemble the context, so they show in the modal rather than crash it. msgs
    — targeted section messages (account_msg/account_error, etc.)."""
    lang = getattr(request.state, "lang", "en")
    # Flush pending DB changes before reading: without this session.get returns
    # the just-deleted (session.delete) avatar from the identity map, and after
    # "Remove photo"/picking a preset avatar the page would show has_avatar as a
    # stale True (the button wouldn't dim, the checkmark on the pick wouldn't set).
    session.flush()
    has_avatar = session.get(Avatar, me.id) is not None
    return _page(
        request,
        session,
        "security",
        me=me,
        totp_on=bool(me.totp_enabled_at),
        backup_left=twofactor.unused_backup_count(me),
        has_avatar=has_avatar,
        avatar_presets=usericons.PRESETS,
        avatar_preset=me.avatar_preset,
        categories=flt.category_options(session, me.id, lang),
        cat_icons=caticons.ICON_SLUGS,
        my_sources=_my_sources(session, me.id, lang),
        source_kinds=SOURCE_KINDS,
        # The address the extension should point at: the configured public URL, else
        # whatever host this request came in on.
        server_url=runtime_settings.site_url(session) or str(request.base_url).rstrip("/"),
        extension_available=_extension_dir() is not None,
        # ChatGPT / MCP connector: the URL to paste, and the apps currently authorized.
        mcp_enabled=bool(settings.public_url),
        mcp_url=(settings.public_url.rstrip("/") + "/mcp-server/mcp") if settings.public_url else "",
        mcp_grants=_mcp_grants(session, me.id),
        error=error,
        **msgs,
    )


def _page(request: Request, session, step: str, **extra) -> HTMLResponse:
    user_id = getattr(request.state, "user_id", None)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"step": step, "counts": _counts(session, user_id), "nav": "settings", **extra},
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        return _security_page(request, session, me)


# --- your account: password, email, photo, deletion -----------------------


def _require_me(session, request: Request) -> User:
    me = _me(session, request)
    if me is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    return me


@router.post("/settings/account/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current: Annotated[str, Form()] = "",
    new: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Change your password: confirm with the current one first, then check strength."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        if not security.verify_password(current, me.password_hash):
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.bad_current", lang)
            )
        if new != confirm:
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.pw_mismatch", lang)
            )
        key = security.check_password_strength(new)
        if key:
            return _security_page(request, session, me, account_error=i18n.translate(key, lang))
        me.password_hash = security.hash_password(new)
        # Changing the password kicks out all sessions (including a stolen one —
        # that's the whole reason people change it), and we hand the current user a
        # fresh one right away so they aren't signed out. Password reset does the same.
        auth.revoke_all(session, me)
        resp = _security_page(
            request, session, me, account_msg=i18n.translate("account.pw_changed", lang)
        )
        auth.open_session(session, me, request, resp)
        return resp


@router.post("/settings/account/name", response_class=HTMLResponse)
def change_name(
    request: Request, display_name: Annotated[str, Form()] = ""
) -> HTMLResponse:
    """Change your display name — shown in the sidebar and on the cards you add. Never
    left empty: a blank falls back to the local part of your email."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        me.display_name = display_name.strip()[:128] or me.email.split("@")[0]
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.name_changed", lang)
        )


@router.post("/settings/account/email", response_class=HTMLResponse)
def change_email(
    request: Request,
    email: Annotated[str, Form()] = "",
    email_confirm: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Change your email: confirm with the password, lowercase it, keep it
    unique. The new email is entered twice — a typo in the address would lock you
    out (reset emails would go to the wrong place)."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        new = email.strip().lower()
        if "@" not in new or len(new) < 5:
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.email_bad", lang)
            )
        if new != email_confirm.strip().lower():
            return _security_page(
                request, session, me,
                account_error=i18n.translate("account.err.email_mismatch", lang),
            )
        if not security.verify_password(password, me.password_hash):
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.bad_current", lang)
            )
        taken = session.scalar(select(User).where(User.email == new, User.id != me.id))
        if taken is not None:
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.email_taken", lang)
            )
        me.email = new
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.email_changed", lang)
        )


@router.post("/settings/account/photo", response_class=HTMLResponse)
def upload_avatar(
    request: Request, photo: Annotated[UploadFile, File()]
) -> HTMLResponse:
    """Upload a profile photo. We convert to a square webp (png/jpeg/gif/bmp —
    Pillow; svg — headless Chromium). A SYNCHRONOUS route on purpose: rasterizing
    svg via sync-Playwright can't live inside the asyncio loop."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        # Read with an in-memory cap (before Pillow even): without a limiter a
        # huge upload would settle whole into RAM before the size check.
        data = photo.file.read(avatars.MAX_UPLOAD + 1)
        try:
            webp = avatars.to_webp(data, photo.content_type or "")
        except avatars.AvatarError as exc:
            return _security_page(
                request, session, me, account_error=i18n.translate(str(exc), lang)
            )
        row = session.get(Avatar, me.id)
        if row is None:
            session.add(Avatar(user_id=me.id, webp=webp))
        else:
            row.webp = webp
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.photo_saved", lang)
        )


@router.post("/settings/account/photo/delete", response_class=HTMLResponse)
def delete_avatar(request: Request) -> HTMLResponse:
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        row = session.get(Avatar, me.id)
        if row is not None:
            session.delete(row)
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.photo_removed", lang)
        )


@router.post("/settings/account/avatar-preset", response_class=HTMLResponse)
def set_avatar_preset(
    request: Request, preset: Annotated[str, Form()] = ""
) -> HTMLResponse:
    """Pick an avatar from the "busts" set. An uploaded photo overrides the set,
    so when a set avatar is picked we delete the photo — else the pick wouldn't show."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        if not usericons.is_valid(preset):
            return _security_page(
                request, session, me,
                account_error=i18n.translate("account.avatar_bad", lang),
            )
        me.avatar_preset = preset
        row = session.get(Avatar, me.id)
        if row is not None:
            session.delete(row)
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.avatar_saved", lang)
        )


@router.post("/settings/account/delete", response_class=HTMLResponse)
def delete_account(request: Request, password: Annotated[str, Form()] = "") -> Response:
    """Delete your account. Confirm with the password. We don't let the last
    owner be deleted (else there's no one to manage). Shared cards go to the app
    owner, personal ones go with the user (the same cleanup as the admin's)."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        if not security.verify_password(password, me.password_hash):
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.bad_current", lang)
            )
        if me.is_owner:
            other_owner = session.scalar(
                select(User).where(
                    User.is_owner.is_(True), User.is_active.is_(True), User.id != me.id
                )
            )
            if other_owner is None:
                return _security_page(
                    request, session, me,
                    account_error=i18n.translate("account.err.last_owner", lang),
                )
        # Whom to hand the shared cards to: any ACTIVE owner other than the one
        # leaving — not banned, else the cards settle with someone who can't sign in.
        heir = session.scalar(
            select(User)
            .where(User.is_owner.is_(True), User.is_active.is_(True), User.id != me.id)
            .order_by(User.created_at)
        )
        heir_id = heir.id if heir is not None else me.id
        from vivatlas.admin_web import _purge_user

        response = RedirectResponse("/login", status_code=303)
        auth.close_session(session, request, response)
        _purge_user(session, me, heir_id)
        return response


@router.get("/avatar/{user_id}")
def avatar(request: Request, user_id: int) -> Response:
    """Serve the avatar (webp). Behind the lock: shown to signed-in users (avatar
    in the menu and on cards). Priority: uploaded photo → default preset avatar →
    404 (the template then shows initials). The uploaded photo is cached briefly,
    so a change shows quickly; the preset — longer, it doesn't change."""
    with session_scope() as session:
        row = session.get(Avatar, user_id)
        if row is not None:
            return Response(
                content=row.webp,
                media_type="image/webp",
                headers={"Cache-Control": "private, max-age=60"},
            )
        user = session.get(User, user_id)
        if user is not None and user.avatar_preset:
            data = usericons.read_bytes(user.avatar_preset)
            if data is not None:
                return Response(
                    content=data,
                    media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=3600"},
                )
        raise HTTPException(404, "no avatar")


# --- category folders: shared (admin) and personal (each user's own) --------
#
# Shared ones (owner empty) are created and kept only by the admin — the common
# catalogue. Personal ones (owner set) each user keeps for themselves; others'
# personal ones aren't visible even to the admin. vivatlas.categories checks rights.


def _scope_cond(owner_id: int | None):
    """The SQL condition "within the same ownership scope", so name/position are
    counted within the shared set OR within one user's personal set."""
    if owner_id is None:
        return Category.owner_user_id.is_(None)
    return Category.owner_user_id == owner_id


def _safe_next(nxt: str) -> str:
    """Where to return after acting on a folder. Shared folders are managed from
    the admin panel (next=/admin), personal ones — from settings (the default).
    Internal addresses only, so a form can't steer you off to another site."""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/settings"


def _authorize_category(session, request: Request, cat: Category | None):
    """Check the right to manage a specific folder. Returns (me). 404 on another's
    personal one (we don't confirm it exists), 403 on a shared one without admin rights."""
    me = _me(session, request)
    if cat is None or not catperm.can_view(cat, me.id):
        raise HTTPException(404, i18n.msg(request, "err.category_not_found"))
    if not catperm.can_manage(cat, me.id, me.is_owner or me.is_admin):
        raise HTTPException(403, i18n.msg(request, "err.categories_owner_only"))
    return me


@router.post("/settings/categories", response_class=HTMLResponse)
def category_create(
    request: Request,
    name: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "private",
    next: Annotated[str, Form()] = "/settings",
) -> Response:
    """Create a folder. scope=shared — shared (admin only); otherwise personal
    (each user's own). The name is unique within its own scope.

    For AJAX (Accept: application/json) we return the HTML of the new row — the
    script inserts it into the list at once, without reloading the modal. Without
    the script (or the network) — a plain redirect, the page redraws with the folder in place."""
    dest = _safe_next(next)
    lang = getattr(request.state, "lang", "en")
    wants_json = "application/json" in request.headers.get("accept", "")
    with session_scope() as session:
        me = _me(session, request)
        name = name.strip()
        if not name:
            return JSONResponse({"ok": False}) if wants_json else RedirectResponse(
                dest, status_code=303
            )
        if scope == "shared":
            if not (me.is_owner or me.is_admin):
                raise HTTPException(403, i18n.msg(request, "err.categories_owner_only"))
            owner: int | None = None
        else:
            owner = me.id
        cond = _scope_cond(owner)
        if session.scalar(select(Category).where(Category.name == name, cond)) is not None:
            if wants_json:
                return JSONResponse(
                    {"ok": False, "error": i18n.translate("settings.folder_exists", lang)}
                )
            return RedirectResponse(dest, status_code=303)
        pos = session.scalar(select(func.max(Category.position)).where(cond)) or 0
        # No icon chosen — we pick by the meaning of the name; can be replaced later.
        chosen = icon[:32] or caticons.suggest_icon(name)
        cat = Category(
            name=name[:128],
            names_json=catnames.translate_category_name(name),
            icon=chosen,
            position=pos + 1,
            owner_user_id=owner,
        )
        session.add(cat)
        session.flush()
        if wants_json:
            c = {
                "value": cat.id,
                "label": catnames.label(cat.names_json, cat.name, lang),
                "icon": cat.icon,
                "count": 0,
            }
            html = templates.env.get_template("_catrow.html").render(
                c=c, cat_icons=caticons.ICON_SLUGS, back=dest, **i18n.template_context(request)
            )
            return JSONResponse({"ok": True, "id": cat.id, "html": html})
    return RedirectResponse(dest, status_code=303)


@router.post("/settings/categories/{cat_id}/update", response_class=HTMLResponse)
def category_update(
    request: Request,
    cat_id: int,
    name: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Rename and/or change the icon. Only within your own scope (personal — your
    own, shared — admin)."""
    with session_scope() as session:
        cat = session.get(Category, cat_id)
        _authorize_category(session, request, cat)
        name = name.strip()
        if name:
            cond = _scope_cond(cat.owner_user_id)
            dup = session.scalar(
                select(Category).where(Category.name == name, Category.id != cat_id, cond)
            )
            if dup is None:
                cat.name = name[:128]
                cat.names_json = catnames.translate_category_name(cat.name)
        cat.icon = icon[:32]
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/settings/categories/{cat_id}/move", response_class=HTMLResponse)
def category_move(
    request: Request,
    cat_id: int,
    dir: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Swap places with the neighbour in order (up/down) — within your own scope
    (shared ones reorder among shared, personal among your own)."""
    with session_scope() as session:
        cat = session.get(Category, cat_id)
        _authorize_category(session, request, cat)
        cats = session.scalars(
            select(Category)
            .where(_scope_cond(cat.owner_user_id))
            .order_by(Category.position, Category.name)
        ).all()
        # NB: the form parameter `next` shadows the built-in next(), so we find
        # the index without it.
        matches = [i for i, c in enumerate(cats) if c.id == cat_id]
        idx = matches[0] if matches else None
        if idx is not None:
            swap = idx - 1 if dir == "up" else idx + 1
            if 0 <= swap < len(cats):
                # Rewrite positions in order, swapping the two spots — more robust
                # than changing one value (positions could have collided or been 0).
                cats[idx], cats[swap] = cats[swap], cats[idx]
                for i, c in enumerate(cats):
                    c.position = i
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/settings/categories/reorder", response_class=HTMLResponse)
def category_reorder(
    request: Request,
    order: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Folder order is set by dragging: a list of ids arrives in the new order.
    We reorder only those the user is entitled to manage (their own personal or,
    for the admin, shared) — others in the list we silently skip."""
    with session_scope() as session:
        me = _me(session, request)
        for pos, part in enumerate(order.split(",")):
            part = part.strip()
            if part.isdigit():
                cat = session.get(Category, int(part))
                if cat is not None and catperm.can_manage(cat, me.id, me.is_owner or me.is_admin):
                    cat.position = pos
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/settings/categories/{cat_id}/delete", response_class=HTMLResponse)
def category_delete(
    request: Request, cat_id: int, next: Annotated[str, Form()] = "/settings"
) -> RedirectResponse:
    with session_scope() as session:
        cat = session.get(Category, cat_id)
        _authorize_category(session, request, cat)
        # Folder membership (ArtifactCategory) goes by cascade via ondelete=CASCADE;
        # the cards themselves remain.
        session.delete(cat)
    return RedirectResponse(_safe_next(next), status_code=303)


# --- personal repositories (private zone) ----------------------------------
# Each user has their own token-bearing sources. The token is entered by the
# user and only them — the program neither types nor fills it; on the server it
# lands encrypted and goes out only masked.


@router.post("/settings/sources", response_class=HTMLResponse)
def source_create(
    request: Request,
    kind: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    base_url: Annotated[str, Form()] = "",
    token: Annotated[str, Form()] = "",
) -> Response:
    with session_scope() as session:
        me = _me(session, request)
        kinds = {k for k, _ in SOURCE_KINDS}
        base_url = base_url.strip()
        # Show the error IN the modal rather than crash it: a bad address is an
        # everyday thing, and you can't lose the whole modal over it.
        if kind not in kinds:
            lang = getattr(request.state, "lang", "en")
            return _security_page(
                request, session, me, error=i18n.translate("settings.src.pick_host", lang)
            )
        if not _valid_url(base_url):
            return _security_page(
                request, session, me,
                error=i18n.translate("settings.src.bad_url", getattr(request.state, "lang", "en")),
            )
        session.add(
            Source(
                kind=kind,
                display_name=(display_name.strip() or base_url)[:128],
                base_url=base_url[:512],
                owner_user_id=me.id,
                token_enc=security.encrypt_secret(token.strip()) if token.strip() else "",
                enabled=True,
            )
        )
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/sources/{source_id}/update", response_class=HTMLResponse)
def source_update(
    request: Request,
    source_id: int,
    kind: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    base_url: Annotated[str, Form()] = "",
    token: Annotated[str, Form()] = "",
) -> Response:
    """Edit your source: host, name, address, token. We change the token only if
    a new one is entered — an empty field keeps the old one."""
    with session_scope() as session:
        me = _me(session, request)
        src = session.get(Source, source_id)
        if src is None or src.owner_user_id != me.id:
            raise HTTPException(404, i18n.msg(request, "err.source_not_found"))
        kinds = {k for k, _ in SOURCE_KINDS}
        base_url = base_url.strip()
        if base_url and not _valid_url(base_url):
            return _security_page(
                request, session, me,
                error=i18n.translate("settings.src.bad_url", getattr(request.state, "lang", "en")),
            )
        if kind in kinds:
            src.kind = kind
        if base_url:
            src.base_url = base_url[:512]
        src.display_name = (display_name.strip() or src.base_url)[:128]
        if token.strip():
            src.token_enc = security.encrypt_secret(token.strip())
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/sources/{source_id}/delete", response_class=HTMLResponse)
def source_delete(request: Request, source_id: int) -> RedirectResponse:
    with session_scope() as session:
        me = _me(session, request)
        src = session.get(Source, source_id)
        # You can delete only your own source — and a shared one must not be touched.
        if src is not None and src.owner_user_id == me.id:
            session.delete(src)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/sources/{source_id}/scan", response_class=HTMLResponse)
async def source_scan(request: Request, source_id: int) -> Response:
    """Crawl your source and gather cards into the private zone. The fast part
    (the repository list) we do at once — an access/address error is shown right
    in the modal. The long crawl (download and describe each) goes to the
    background, and a progress bar appears on the home page."""
    from vivatlas.web import launch_user_scan, precheck_user_scan, scan_progress

    user_id = getattr(request.state, "user_id", None)

    # A scan is already running — don't launch a second, just send to the home page bar.
    prog = scan_progress(user_id)
    if prog and prog.get("state") == "running":
        return RedirectResponse("/", status_code=303)

    # Instant checks only (no network): an error goes straight to the modal. The
    # crawl itself, including fetching the repository list, goes to the background
    # — the button responds immediately, and progress shows as a bar on the home page.
    error_key, source_name = precheck_user_scan(user_id, source_id)
    if error_key:
        with session_scope() as session:
            me = _me(session, request)
            return _security_page(request, session, me, error=i18n.msg(request, error_key))
    launch_user_scan(user_id, source_id, source_name, getattr(request.state, "lang", "en"))
    return RedirectResponse("/", status_code=303)


# --- enabling: show the QR -------------------------------------------------


@router.post("/settings/2fa/start", response_class=HTMLResponse)
def totp_start(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        # We already save the secret (encrypted), but we DON'T enable the check:
        # until the user enters a code, we don't know the app is really linked.
        # Enabling earlier means locking yourself out of your own account.
        secret = twofactor.new_secret()
        me.totp_secret_enc = security.encrypt_secret(secret)
        session.flush()

        uri = twofactor.provisioning_uri(secret, me.email)
        return _page(
            request,
            session,
            "qr",
            qr=Markup(twofactor.qr_svg(uri)),
            secret=secret,  # show it manually too: not everyone has a scanner handy
        )


# --- enabling: confirm with a code ----------------------------------------


@router.post("/settings/2fa/confirm", response_class=HTMLResponse)
def totp_confirm(request: Request, code: Annotated[str, Form()] = "") -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        if not twofactor.verify_totp(me, code):
            secret = security.decrypt_secret(me.totp_secret_enc)
            uri = twofactor.provisioning_uri(secret, me.email)
            return _page(
                request,
                session,
                "qr",
                qr=Markup(twofactor.qr_svg(uri)),
                secret=secret,
                error=i18n.msg(request, "settings.2fa.err.bad_code_clock"),
            )

        # The code is right — the app is linked. We enable it and hand out backup
        # codes at once: without them, losing the phone locks you out forever.
        me.totp_enabled_at = datetime.now(UTC)
        codes = twofactor.make_backup_codes(session, me)
        user_id = me.id

        # Make the write durable RIGHT NOW, before we render anything: if the
        # enabling doesn't survive this moment, the user must not see codes that
        # aren't in the database.
        session.commit()

        # A self-check in a separate session: did the enabling really land in the
        # database. There was a complaint that the check turned off after saving
        # codes — this log line will say for sure whether the write landed or rolled back.
        with session_scope() as check:
            stuck = check.get(User, user_id)
            if stuck and stuck.totp_enabled_at is not None:
                log.info("2FA enabled and recorded: user %s", user_id)
            else:
                log.error("2FA did NOT get recorded after confirmation: user %s", user_id)

        return _page(request, session, "codes", codes=codes, fresh=True)


# --- backup codes again ----------------------------------------------------


@router.post("/settings/2fa/backup", response_class=HTMLResponse)
def backup_regen(request: Request, code: Annotated[str, Form()] = "") -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if not me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        # Reissuing codes takes a code from the app: otherwise anyone who sits at
        # an open screen for a minute prints themselves a new set of keys.
        if not twofactor.verify_totp(me, code):
            return _security_page(
                request, session, me,
                error=i18n.msg(request, "settings.2fa.err.bad_code_no_regen"),
            )
        codes = twofactor.make_backup_codes(session, me)
        return _page(request, session, "codes", codes=codes, fresh=False)


# --- disabling -------------------------------------------------------------


@router.post("/settings/2fa/disable", response_class=HTMLResponse)
def totp_disable(request: Request, password: Annotated[str, Form()] = "") -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if not me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        # Disabling takes the password: removing the second door should be done by
        # someone who knows the first, not someone who just ended up at another's screen.
        if not security.verify_password(password, me.password_hash):
            return _security_page(
                request, session, me,
                error=i18n.msg(request, "settings.2fa.err.bad_password"),
            )

        me.totp_enabled_at = None
        me.totp_secret_enc = ""
        me.totp_last_code = ""
        for row in list(me.backup_codes):
            me.backup_codes.remove(row)
            session.delete(row)
        return RedirectResponse("/settings", status_code=303)
