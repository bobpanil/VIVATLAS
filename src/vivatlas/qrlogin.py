"""Signing a phone in by scanning a code shown on a machine that already is.

The password is the wrong thing to ask a phone for: it is the longest secret a
person owns and the phone keyboard is the worst place to type it. So a browser
that is already signed in mints a one-time pass, draws it as a QR, and the app
reads it off the screen.

What the code carries is the whole credential, so it is built to be worth as
little as possible for as short a time as possible — see [QrLogin] for why each
of short-lived, single-use and hashed is there. Nothing here trusts the token
itself: `claim` is the only door, and it checks all three before it opens a
session.

The code encodes an ordinary URL — `https://your-vivatlas/qr/<token>` — rather
than a bare secret, so one scan tells the phone *which* server as well as *who*.
A fresh install can therefore be signed in without ever typing the address
either.
"""

from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from vivatlas import auth, runtime_settings, security
from vivatlas.models import QrLogin, User

# How long a shown code stays good. Long enough to unlock the phone, open the app
# and aim it; short enough that a code left on an unattended screen — or caught by
# a camera behind you — is already dead by the time anyone tries it.
TTL_SECONDS = 90


def _now() -> datetime:
    return datetime.now(UTC)


def mint(session: Session, user: User) -> str:
    """Issue a one-time pass for `user` and return the raw token (shown once, in the
    QR, and never stored — the database keeps only its hash)."""
    # Clear this user's earlier codes first. Showing a second QR should retire the
    # first: otherwise every refresh of the page leaves another live pass behind.
    session.execute(delete(QrLogin).where(QrLogin.user_id == user.id))

    raw = security.new_token()
    session.add(
        QrLogin(
            user_id=user.id,
            token_hash=security.token_hash(raw),
            expires_at=_now() + timedelta(seconds=TTL_SECONDS),
        )
    )
    return raw


def code_url(session: Session, request: Request, raw: str) -> str | None:
    """What the QR encodes: this server, plus the pass. None when there is nowhere
    safe to name — the caller must then show the owner what to set, not a code.

    The address comes from the one rule that decides such things
    ([runtime_settings.public_base_url]): the owner's configured site address, or
    the request's own only on our own host. A code is a credential the phone will
    carry back, so naming a host the client chose is the same mistake as a
    poisoned reset link, with the same fix.

    Behind a tunnel this is also simply what makes it work. The request arrives as
    plain http on an internal address, so a code built from it names http:// for an
    https-only site and walks the phone into a 301 it will not follow. TRUSTED_PROXIES
    would make the request truthful, but it is optional and most installs behind a
    tunnel will not have it, so nothing here is allowed to depend on it.
    """
    base = runtime_settings.public_base_url(session, request)
    return f"{base}/qr/{raw}" if base else None


def code_origin(session: Session, request: Request) -> str | None:
    """Just the address the code will point at — no pass in it. Shown beside the QR
    so a wrong one is visible at a glance rather than only as a sign-in that
    quietly fails."""
    return runtime_settings.public_base_url(session, request)


def claim(
    session: Session, raw: str, request: Request, response: Response
) -> tuple[str, User] | None:
    """Spend a pass and open a session, returning the raw session token and whose it
    is — or None if the pass is unknown, expired or already spent.

    The row is stamped used BEFORE the session is opened, so a code raced by two
    readers signs in at most one of them."""
    if not raw:
        return None

    row = (
        session.query(QrLogin)
        .filter(QrLogin.token_hash == security.token_hash(raw))
        .one_or_none()
    )
    if row is None or row.used_at is not None:
        return None

    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite hands back naive datetimes
        expires = expires.replace(tzinfo=UTC)
    if expires <= _now():
        return None

    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None

    row.used_at = _now()
    row.used_ip = auth.client_ip(request)
    row.used_user_agent = (request.headers.get("user-agent") or "")[:256]
    session.flush()

    return auth.open_session(session, user, request, response), user


def sweep(session: Session) -> None:
    """Drop passes that are spent or stale. Nothing depends on this running — claim
    refuses them anyway — it just keeps the table from growing forever."""
    session.execute(delete(QrLogin).where(QrLogin.expires_at <= _now()))
