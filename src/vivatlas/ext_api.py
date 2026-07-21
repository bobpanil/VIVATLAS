"""JSON API for the browser extension.

Sign in (with the same MFA as the web), check the session, sign out, and capture a
page or link into VIVATLAS. The extension keeps the token returned by login and sends
it as `Authorization: Bearer <token>` on its own cross-site requests (the SameSite
cookie isn't sent on those). The same login ALSO sets the session cookie, so opening
the web UI from the extension needs no second sign-in.

/login and /mfa are the only open endpoints; everything else is behind the normal
sign-in lock (which now also accepts the Bearer token).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import JSONResponse

from vivatlas import auth, i18n, twofactor
from vivatlas.db import session_scope
from vivatlas.models import User
from vivatlas.web import ext_capture

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ext")


def _user_json(u: User) -> dict:
    return {
        "email": u.email,
        "name": u.display_name or u.email,
        "is_admin": bool(u.is_owner or u.is_admin),
    }


def _session_response(session, user: User, request: Request, extra: dict) -> JSONResponse:
    """Open a session and return JSON that carries BOTH the Bearer token (for the
    extension's own calls) and the Set-Cookie (so the web UI opens already signed in)."""
    carrier = Response()
    token = auth.open_session(session, user, request, carrier)
    body = {"ok": True, "token": token, "user": _user_json(user), **extra}
    resp = JSONResponse(body)
    cookie = carrier.headers.get("set-cookie")
    if cookie:
        resp.headers["set-cookie"] = cookie
    return resp


@router.post("/login")
def ext_login(request: Request, payload: Annotated[dict, Body()]) -> JSONResponse:
    """Email + password. Either signs in, or asks for the MFA code (returning a short-
    lived ticket the extension echoes back to /mfa)."""
    lang = getattr(request.state, "lang", "en")
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("password", ""))
    with session_scope() as session:
        result = auth.check_login(session, email, password)
        if not result.ok:
            key = "auth.err.locked" if result.locked_minutes else (
                result.error or "auth.err.bad_credentials"
            )
            return JSONResponse({"ok": False, "error": i18n.translate(key, lang)}, status_code=401)
        if result.needs_totp:
            ticket = auth.make_totp_ticket_token(result.user)
            return JSONResponse({"ok": True, "mfa_required": True, "ticket": ticket})
        return _session_response(session, result.user, request, {"mfa_required": False})


@router.post("/mfa")
def ext_mfa(request: Request, payload: Annotated[dict, Body()]) -> JSONResponse:
    """The second step: the ticket from /login plus a code (TOTP, or a backup code when
    `backup` is set)."""
    lang = getattr(request.state, "lang", "en")
    ticket = str(payload.get("ticket", ""))
    code = str(payload.get("code", ""))
    use_backup = bool(payload.get("backup"))
    user_id = auth.read_totp_ticket_token(ticket)
    if user_id is None:
        return JSONResponse(
            {"ok": False, "error": i18n.translate("auth.err.totp_bad", lang), "expired": True},
            status_code=401,
        )
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return JSONResponse(
                {"ok": False, "error": i18n.translate("auth.err.bad_credentials", lang)},
                status_code=401,
            )
        good = (
            twofactor.use_backup_code(session, user, code)
            if use_backup
            else twofactor.verify_totp(user, code)
        )
        if not good:
            return JSONResponse(
                {"ok": False, "error": i18n.translate("auth.err.totp_bad", lang)}, status_code=401
            )
        return _session_response(session, user, request, {"mfa_required": False})


@router.get("/session")
def ext_session(request: Request) -> JSONResponse:
    """Who the token belongs to — the extension calls this on open to know if it's still
    signed in. (The lock has already validated the token by the time we get here.)"""
    return JSONResponse(
        {
            "ok": True,
            "user": {
                "email": getattr(request.state, "user_email", ""),
                "name": getattr(request.state, "user_name", ""),
                "is_admin": getattr(request.state, "is_admin", False),
            },
        }
    )


@router.post("/logout")
def ext_logout(request: Request) -> JSONResponse:
    """Revoke this session (the token stops working) and drop the cookie."""
    resp = JSONResponse({"ok": True})
    with session_scope() as session:
        auth.close_session(session, request, resp)
    return resp


@router.post("/add")
async def ext_add(request: Request, payload: Annotated[dict, Body()]) -> JSONResponse:
    """Capture a tool. `url` (current tab or pasted), optional `title` and `text` (the
    grabbed page), and `shared` (public vs private). A GitHub repo imports in the
    background; anything else is kept as a draft. Returns fast so browsing continues."""
    lang = getattr(request.state, "lang", "en")
    user_id = getattr(request.state, "user_id", None)
    url = str(payload.get("url", "")).strip()
    title = str(payload.get("title", ""))
    text = str(payload.get("text", ""))
    shared = bool(payload.get("shared"))
    if not url and not text.strip():
        return JSONResponse(
            {"ok": False, "error": i18n.translate("add.err.need_input", lang)}, status_code=400
        )
    result = await ext_capture(url, title, text, user_id, shared)
    return JSONResponse({"ok": True, **result})
