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

from vivatlas import auth, security
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


def code_url(request: Request, raw: str) -> str:
    """What the QR actually encodes: this server, as the browser reached it, plus
    the pass. `base_url` is the address the user is really on — behind a tunnel or
    a proxy that is the public one, which is the one the phone must be told."""
    return f"{str(request.base_url).rstrip('/')}/qr/{raw}"


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
