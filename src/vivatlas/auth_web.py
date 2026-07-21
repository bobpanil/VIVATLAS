"""Door pages: setup, sign-in, second code, sign-out.

Separate from web.py: those pages live behind the lock, these are the lock
itself. They also have their own template, without the catalogue sidebar:
until you sign in, the catalogue must not be visible.
"""

import ipaddress
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from vivatlas import auth, i18n, mailer, runtime_settings, security, twofactor, usericons
from vivatlas.db import session_scope
from vivatlas.models import User

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
router = APIRouter()


def _page(request: Request, step: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"step": step, **extra})


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


# --- first run: create the owner ------------------------------------------


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        if auth.has_any_user(session):
            return RedirectResponse("/login", status_code=303)
    return _page(request, "setup")


@router.post("/setup")
def setup_do(
    request: Request,
    email: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    email = email.strip().lower()
    with session_scope() as session:
        # The owner is created only once. Whoever gets here first becomes the
        # owner; coming back here again creates nothing.
        if auth.has_any_user(session):
            return RedirectResponse("/login", status_code=303)

        err = _validate(email, password, password2)
        if err:
            lang = getattr(request.state, "lang", "en")
            return _page(
                request, "setup", error=i18n.translate(err, lang),
                email=email, display_name=display_name,
            )

        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=True,
            avatar_preset=usericons.random_preset(),
        )
        session.add(user)
        session.flush()

        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        return response


# --- sign-in ---------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        can_register = runtime_settings.registration_open(session)
    return _page(request, "login", next=_safe_next(next), can_register=can_register)


@router.post("/login")
def login_do(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse:
    dest = _safe_next(next)
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        can_register = runtime_settings.registration_open(session)
        result = auth.check_login(session, email, password)

        if result.locked_minutes:
            return _page(
                request,
                "login",
                error=i18n.translate("auth.err.locked", lang, minutes=result.locked_minutes),
                email=email,
                next=dest,
                can_register=can_register,
            )
        if not result.ok:
            return _page(
                request, "login", error=i18n.translate(result.error, lang), email=email,
                next=dest, can_register=can_register,
            )

        if result.needs_totp:
            response = _page(request, "totp", next=dest)
            auth.issue_totp_ticket(response, result.user, _secure(request))
            return response

        response = RedirectResponse(dest, status_code=303)
        auth.open_session(session, result.user, request, response)
        return response


# --- second code -----------------------------------------------------------


@router.post("/login/2fa")
def login_2fa(
    request: Request,
    code: Annotated[str, Form()] = "",
    use_backup: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse:
    dest = _safe_next(next)
    user_id = auth.read_totp_ticket(request)
    if user_id is None:
        # The ticket is stale or missing — start sign-in over.
        return RedirectResponse("/login", status_code=303)

    # An empty code is not an attempt but a switch between the "code from app" /
    # "backup code" views. Show the right form without a false error.
    if not code.strip():
        return _page(request, "totp", next=dest, backup=bool(use_backup))

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return RedirectResponse("/login", status_code=303)

        if use_backup:
            good = twofactor.use_backup_code(session, user, code)
        else:
            good = twofactor.verify_totp(user, code)

        if not good:
            return _page(
                request,
                "totp",
                error=i18n.translate("auth.err.totp_bad", getattr(request.state, "lang", "en")),
                next=dest,
                backup=bool(use_backup),
            )

        response = RedirectResponse(dest, status_code=303)
        auth.open_session(session, user, request, response)
        auth.clear_totp_ticket(response)
        return response


# --- sign-out --------------------------------------------------------------


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    with session_scope() as session:
        auth.close_session(session, request, response)
    return response


# --- forgot password -------------------------------------------------------


@router.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
    return _page(request, "forgot")


async def _send_reset_quietly(cfg, to: str, subject: str, html: str, text: str) -> None:
    """Send the reset email in the background, swallowing any error. In the
    background so the page response doesn't depend on whether such an email
    exists and whether sending succeeded: otherwise the response time would
    reveal whether the account exists."""
    try:
        await mailer.send(cfg, to, subject, html, text)
    except mailer.MailError as exc:
        log.warning("reset email failed to reach %s: %s", to, exc)


def _is_local_host(host: str) -> bool:
    """Is this our own address — loopback or home network. Only such hosts do we
    trust to put themselves into the link when site_url is not set."""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _reset_link_base(session, request: Request) -> str | None:
    """Where to get the domain for the link in the email. None — nowhere safe to take it from.

    If the owner set site_url — we use it. If not, the request address can be
    substituted, BUT only when it is our own host (loopback/LAN): behind a
    tunnel, and on a public address in general, the request Host is set by the
    client and cannot be trusted — otherwise the link in the email is led off
    to a foreign domain and used to change the password (reset poisoning). On a
    public address without site_url we simply don't send the link.
    """
    configured = runtime_settings.site_url(session)
    if configured:
        return configured
    host = request.url.hostname or ""
    if _is_local_host(host):
        return str(request.base_url).rstrip("/")
    return None


@router.post("/forgot")
def forgot_do(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Send a link to change the password.

    The response is always the same — "if such an email exists, a message was
    sent". We neither confirm nor deny that the account exists: otherwise the
    reset page becomes a checker for other people's emails. The email itself
    goes out in the background.
    """
    email = email.strip().lower()
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        user = session.scalar(select(User).where(User.email == email))
        if user is not None and user.is_active:
            cfg = runtime_settings.get_smtp(session)
            base = _reset_link_base(session, request)
            if not cfg.is_configured:
                log.warning("password reset requested, but email is not configured: %s", email)
            elif base is None:
                log.warning(
                    "password reset: site_url is not set and the request address (%s) is not local "
                    "— not sending the link",
                    request.url.hostname,
                )
            else:
                # We don't let SecretMissing crash here: without the key we
                # can't sign, but the response must stay the same as for a
                # nonexistent email — otherwise 500 vs 200 reveals the account exists.
                try:
                    token = auth.make_reset_token(user)
                    link = f"{base}/reset?token={token}"
                    html, text = mailer.render(
                        "password_reset",
                        getattr(request.state, "lang", "en"),
                        link=link,
                        name=user.display_name,
                        minutes=auth.RESET_MAX_AGE // 60,
                    )
                except security.SecretMissing:
                    log.error("password reset: no SECRET_KEY — can't sign the link")
                else:
                    background.add_task(
                        _send_reset_quietly, cfg, user.email,
                        "Change your password — VivAtlas", html, text
                    )
    return _page(request, "forgot_sent")


# --- change password via link ----------------------------------------------


@router.get("/reset", response_class=HTMLResponse)
def reset_page(request: Request, token: str = "") -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        user = auth.read_reset_token(session, token)
    if user is None:
        return _page(request, "reset_bad")
    return _page(request, "reset", token=token)


@router.post("/reset")
def reset_do(
    request: Request,
    token: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        user = auth.read_reset_token(session, token)
        if user is None:
            return _page(request, "reset_bad")
        if password != password2:
            return _page(
                request, "reset", token=token,
                error=i18n.translate("auth.err.pw_mismatch", lang),
            )
        weak = security.check_password_strength(password)
        if weak:
            return _page(request, "reset", token=token, error=i18n.translate(weak, lang))

        # A new password makes the old link dead (it carries a fingerprint of
        # the previous hash) and tears down all open sessions: if someone got
        # into the account, changing the password should kick them out, not leave them sitting.
        user.password_hash = security.hash_password(password)
        auth.revoke_all(session, user)
    return _page(request, "reset_done")


# --- open registration (if the owner enabled it) ---------------------------


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        if not runtime_settings.registration_open(session):
            return _page(request, "register_closed")
    return _page(request, "register")


@router.post("/register")
def register_do(
    request: Request,
    email: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Create your own account — only when the owner has opened registration. A
    new user is always a regular one (not the owner) and active right away:
    there's no email confirmation, and access to registration is decided by
    the owner's toggle anyway."""
    email = email.strip().lower()
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        if not runtime_settings.registration_open(session):
            return _page(request, "register_closed")
        err = _validate(email, password, password2)
        if err:
            return _page(
                request, "register", error=i18n.translate(err, lang),
                email=email, display_name=display_name,
            )
        if session.scalar(select(User).where(User.email == email)) is not None:
            return _page(
                request, "register", error=i18n.translate("auth.err.email_taken", lang),
                email=email, display_name=display_name,
            )
        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=False,
            avatar_preset=usericons.random_preset(),
        )
        session.add(user)
        session.flush()
        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        return response


# --- invitation: accept and create an account ------------------------------


@router.get("/join", response_class=HTMLResponse)
def join_page(request: Request, code: str = "") -> HTMLResponse:
    with session_scope() as session:
        inv = auth.read_invite(session, code)
        if inv is None:
            return _page(request, "join_bad")
        email = inv.email
    return _page(request, "join", code=code, email=email, email_locked=bool(email))


@router.post("/join")
def join_do(
    request: Request,
    code: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Accept an invitation: set name and password, create an account and sign in.
    We don't allow changing the email of a bound invitation — it's set by the owner."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        inv = auth.read_invite(session, code)
        if inv is None:
            return _page(request, "join_bad")
        use_email = (inv.email or email).strip().lower()
        locked = bool(inv.email)
        err = _validate(use_email, password, password2)
        if err:
            return _page(
                request, "join", code=code, email=use_email, email_locked=locked,
                error=i18n.translate(err, lang), display_name=display_name,
            )
        if session.scalar(select(User).where(User.email == use_email)) is not None:
            return _page(
                request, "join", code=code, email=use_email, email_locked=locked,
                error=i18n.translate("auth.err.email_taken", lang), display_name=display_name,
            )
        user = User(
            email=use_email,
            display_name=display_name.strip() or use_email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=False,
            avatar_preset=usericons.random_preset(),
        )
        session.add(user)
        session.flush()
        # An invitation is single-use: we claim it atomically. If that didn't
        # work (accepted in parallel) — roll back and don't create a second account.
        if not auth.consume_invite(session, inv, user):
            session.rollback()
            return _page(request, "join_bad")
        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        return response


# --- checks ----------------------------------------------------------------


def _validate(email: str, password: str, password2: str) -> str:
    """Empty means OK; otherwise the error KEY (translated at display time)."""
    if "@" not in email or len(email) < 5:
        return "auth.err.email_invalid"
    if password != password2:
        return "auth.err.pw_mismatch"
    weak = security.check_password_strength(password)
    if weak:
        return weak
    return ""


def _safe_next(target: str) -> str:
    """Where to return after sign-in. Only an internal path: a foreign address in
    next is an open redirect, used to lead to a fake sign-in page."""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/"
