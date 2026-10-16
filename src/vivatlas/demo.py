"""Demo mode: a public sandbox people can try before they install their own.

Off unless DEMO_MODE is set. With it on, the login page gets a "Try it" button
that makes a private account for whoever pressed it, signs them in, and deletes
the account with everything in it DEMO_VISITOR_HOURS later. A sandbox open to
anyone has to stay bounded and has to stay private, so:

- Only the owner and admins can share. A visitor's saves stay private: nobody
  else ever sees what a stranger typed in, and the shared catalogue stays the
  starter one the owner put there.
- A visitor owns at most DEMO_MAX_SAVES cards (saves still in the queue count),
  at most DEMO_MAX_VISITORS accounts live at once, and one address starts at
  most DEMO_STARTS_PER_HOUR of them. The database stays small whatever happens.
- The settings that could take an account away from the next person (password,
  email, two-step) or point the server somewhere new (sources) are
  closed to everyone but the owner and admins.

The reviewers' account (DEMO_REVIEWER_EMAIL) is a restricted account like a
visitor's, but it stays: only its cards are cleared after the same time.
"""

import asyncio
import logging
import math
import re
import secrets
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select

from vivatlas import auth, i18n, security
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.models import Artifact, CaptureJob, User

log = logging.getLogger(__name__)
router = APIRouter()

# Visitor accounts live under a domain that can never receive mail (RFC 2606), so
# nothing the server sends to them leaves the box, and they are easy to tell apart.
VISITOR_DOMAIN = "demo.invalid"
_VISITOR_RE = re.compile(r"^visitor-[a-z0-9]+@demo\.invalid$")

PURGE_EVERY_SECONDS = 600

# Closed to restricted accounts. Prefixes of the path, any method but GET.
_CLOSED = (
    "/settings/account/email",
    "/settings/account/password",
    "/settings/account/photo",
    "/settings/2fa/",
    "/settings/sources",
)
# Closed to the reviewers' account but not to a visitor, who may wipe their own.
_CLOSED_TO_KEPT = ("/settings/account/delete",)
# Endpoints that turn into a card: counted against DEMO_MAX_SAVES.
_SAVES = {"/api/ext/add", "/add", "/add/link", "/add/note"}


def is_visitor(email: str) -> bool:
    return bool(_VISITOR_RE.match(email or ""))


def restricted(user: User) -> bool:
    """A demo account bound by the sandbox rules: everyone but the owner and admins."""
    return settings.demo_mode and not (user.is_owner or user.is_admin)


def may_share(session, user_id: int | None) -> bool:
    """Whether this person's card may go into the shared catalogue."""
    if not settings.demo_mode:
        return True
    user = session.get(User, user_id) if user_id is not None else None
    return user is not None and not restricted(user)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _ttl() -> timedelta:
    return timedelta(hours=max(1, settings.demo_visitor_hours))


def saves_used(session, user_id: int) -> int:
    cards = session.scalar(
        select(func.count()).select_from(Artifact).where(Artifact.owner_user_id == user_id)
    )
    queued = session.scalar(
        select(func.count())
        .select_from(CaptureJob)
        .where(CaptureJob.user_id == user_id, CaptureJob.status.in_(("pending", "running")))
    )
    return int(cards or 0) + int(queued or 0)


def _refusal(request: Request, key: str, **kw) -> Response:
    lang = getattr(request.state, "lang", "en")
    msg = i18n.translate(key, lang, **kw)
    wants_json = request.url.path.startswith("/api/") or "application/json" in request.headers.get(
        "accept", ""
    )
    if wants_json:
        return JSONResponse({"ok": False, "error": msg, "detail": msg}, status_code=403)
    return _page(request, "demo_refused", status_code=403, message=msg)


def _page(request: Request, step: str, status_code: int = 200, **extra) -> HTMLResponse:
    from vivatlas.auth_web import templates

    return templates.TemplateResponse(
        request, "auth.html", {"step": step, **extra}, status_code=status_code
    )


def guard(session, request: Request, user: User) -> Response | None:
    """Called by the sign-in middleware for every request of a signed-in person.
    Returns the refusal to send instead, or None to let the request through. It
    also leaves what the page banner needs on request.state."""
    if not settings.demo_mode:
        return None
    visitor = is_visitor(user.email)
    request.state.demo_visitor = visitor
    request.state.demo_restricted = restricted(user)
    if visitor:
        ends = _aware(user.created_at) + _ttl()
        left = (ends - datetime.now(UTC)).total_seconds()
        request.state.demo_hours_left = max(1, math.ceil(left / 3600))
    if not restricted(user) or request.method in ("GET", "HEAD", "OPTIONS"):
        return None

    path = request.url.path
    if path.startswith("/artifact/") and path.endswith("/visibility"):
        return _refusal(request, "demo.err.no_share")
    if path.startswith(_CLOSED) or (not visitor and path.startswith(_CLOSED_TO_KEPT)):
        return _refusal(request, "demo.err.closed")
    if path in _SAVES and saves_used(session, user.id) >= settings.demo_max_saves:
        return _refusal(request, "demo.err.full", count=settings.demo_max_saves)
    return None


def template_info(request: Request) -> dict | None:
    """What base.html and the login page show in demo mode; None when it's off."""
    if not settings.demo_mode:
        return None
    return {
        "visitor": getattr(request.state, "demo_visitor", False),
        "restricted": getattr(request.state, "demo_restricted", False),
        "hours_left": getattr(request.state, "demo_hours_left", settings.demo_visitor_hours),
        "max_saves": settings.demo_max_saves,
        "get_url": settings.demo_get_url,
    }


# --- trying it -----------------------------------------------------------------

# Starts per address over the last hour. In memory on purpose: a restart forgets
# it, and that only ever lets a few more through, bounded by the visitor cap.
_starts: dict[str, deque] = {}


def _rate_ok(ip: str) -> bool:
    now = time.monotonic()
    window = _starts.setdefault(ip, deque())
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= max(1, settings.demo_starts_per_hour):
        return False
    window.append(now)
    if len(_starts) > 10_000:  # forget idle addresses so the map can't grow forever
        for key in [k for k, v in _starts.items() if not v]:
            del _starts[key]
    return True


def create_visitor(session) -> User:
    tag = secrets.token_hex(5)
    email = f"visitor-{tag}@{VISITOR_DOMAIN}"
    while session.scalar(select(User.id).where(User.email == email)) is not None:
        tag = secrets.token_hex(5)
        email = f"visitor-{tag}@{VISITOR_DOMAIN}"
    user = User(
        email=email,
        display_name=f"Visitor {int(tag[:4], 16) % 10000:04d}",
        # Nobody is told this: the visitor is signed in by the session cookie, and
        # once that's gone the account is as good as deleted.
        password_hash=security.hash_password(secrets.token_urlsafe(24)),
    )
    session.add(user)
    session.flush()
    return user


@router.post("/demo/start")
def start(request: Request) -> Response:
    if not settings.demo_mode:
        return RedirectResponse("/login", status_code=303)
    with session_scope() as session:
        if auth.current_user(session, request) is not None:
            return RedirectResponse("/", status_code=303)
        live = session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.email.like(f"visitor-%@{VISITOR_DOMAIN}"))
        )
        lang = getattr(request.state, "lang", "en")
        if int(live or 0) >= settings.demo_max_visitors:
            return _page(
                request, "demo_refused", 503, message=i18n.translate("demo.err.busy", lang)
            )
        if not _rate_ok(auth.client_ip(request)):
            return _page(
                request, "demo_refused", 429, message=i18n.translate("demo.err.rate", lang)
            )
        user = create_visitor(session)
        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        log.info("demo: started %s", user.email)
        return response


# --- the accounts the demo needs -------------------------------------------------


def ensure_accounts(session) -> list[str]:
    """Make the owner and the reviewers' account if they're missing. Idempotent,
    run at every startup in demo mode. Returns what it did, for the log."""
    done = []
    owner = session.scalar(select(User).where(User.is_owner.is_(True)))
    if owner is None:
        session.add(
            User(
                email=settings.demo_owner_email.strip().lower(),
                display_name="VIVATLAS",
                password_hash=security.hash_password(secrets.token_urlsafe(32)),
                is_owner=True,
            )
        )
        done.append(f"made the owner {settings.demo_owner_email}")
    email = settings.demo_reviewer_email.strip().lower()
    password = settings.demo_reviewer_password
    if email and password:
        reviewer = session.scalar(select(User).where(User.email == email))
        if reviewer is None:
            session.add(
                User(
                    email=email,
                    display_name="Reviewer",
                    password_hash=security.hash_password(password),
                )
            )
            done.append(f"made the reviewers' account {email}")
        elif not security.verify_password(password, reviewer.password_hash):
            reviewer.password_hash = security.hash_password(password)
            done.append(f"reset the reviewers' password for {email}")
        if reviewer is not None:
            reviewer.is_active = True
    session.flush()
    return done


# --- clearing out ----------------------------------------------------------------


def purge(session, now: datetime | None = None) -> dict:
    """Delete visitors past their time, and the reviewers' cards past it. Counts
    of both, for the log."""
    from vivatlas.admin_web import _purge_user
    from vivatlas.web import _delete_artifact

    now = now or datetime.now(UTC)
    cutoff = now - _ttl()
    owner = session.scalar(select(User).where(User.is_owner.is_(True)))
    if owner is None:
        return {"visitors": 0, "cards": 0}
    users = session.scalars(
        select(User).where(User.is_owner.is_(False), User.is_admin.is_(False))
    ).all()
    gone_users = gone_cards = 0
    for user in users:
        if is_visitor(user.email):
            if _aware(user.created_at) <= cutoff:
                _purge_user(session, user, owner.id)
                gone_users += 1
            continue
        old = session.scalars(
            select(Artifact).where(Artifact.owner_user_id == user.id, Artifact.created_at <= cutoff)
        ).all()
        for art in old:
            _delete_artifact(session, art, owner.id)
            gone_cards += 1
    session.flush()
    return {"visitors": gone_users, "cards": gone_cards}


def _purge_once() -> dict:
    with session_scope() as session:
        return purge(session)


async def purge_loop() -> None:
    while True:
        try:
            done = await asyncio.to_thread(_purge_once)
            if done["visitors"] or done["cards"]:
                log.info("demo: deleted %(visitors)d visitors and %(cards)d old cards", done)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("demo: clearing out failed")
        await asyncio.sleep(PURGE_EVERY_SECONDS)


def startup() -> None:
    # Not suppressed: with no owner made, /setup would be open to whoever comes
    # first. Better the server doesn't start.
    with session_scope() as session:
        for step in ensure_accounts(session):
            log.info("demo: %s", step)


# --- the starter catalogue ---------------------------------------------------------


def seed(urls: list[str]) -> int:
    """Queue public links as the owner's SHARED saves: the catalogue a visitor
    finds on arrival. A link already queued once is skipped, so running it again
    after an edit to the list adds only the new ones. Returns how many it queued."""
    from vivatlas import captures

    if not settings.demo_mode:
        raise SystemExit("DEMO_MODE is off: this is for the public demo only")
    with session_scope() as session:
        ensure_accounts(session)
        owner_id = session.scalar(select(User.id).where(User.is_owner.is_(True)))
        known = set(session.scalars(select(CaptureJob.url).where(CaptureJob.user_id == owner_id)))
    fresh = [u for u in dict.fromkeys(urls) if u not in known]
    for url in fresh:
        asyncio.run(captures.enqueue(url=url, user_id=owner_id, shared=True, via="seed"))
    return len(fresh)


def _read_list(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        # The first word of each line; a link keeps its #fragment, a line that
        # starts with # is a comment.
        words = (line.split(maxsplit=1)[0] for line in fh if line.strip())
        return [w for w in words if w.startswith(("http://", "https://"))]


if __name__ == "__main__":
    # python -m vivatlas.demo seed links.txt   (one link per line, # comments)
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "seed":
        sys.exit("usage: python -m vivatlas.demo seed FILE")
    print(f"queued {seed(_read_list(sys.argv[2]))} links; the server turns them into cards")
