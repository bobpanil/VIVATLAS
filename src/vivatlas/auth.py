"""Sign-in, sessions, who's at the door right now.

The rules that hold here:

  - the cookie carries a random key, the database its hash. Steal the database
    and you get no ready-made passes, only their fingerprints;
  - the cookie is HttpOnly: a script on the page can't read it, so it can't steal it;
  - Secure is set when the connection is over https. Locally over http the cookie
    still travels — otherwise signing in on the machine itself would be impossible;
  - on a wrong password argon2 is computed anyway, even if there's no such email.
    Otherwise the response time reveals which emails exist and which don't;
  - brute-forcing locks the account for a while. We count failures, we don't guess.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from itsdangerous import (
    BadData,
    BadSignature,
    SignatureExpired,
    TimestampSigner,
    URLSafeTimedSerializer,
)
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vivatlas import security
from vivatlas.config import settings
from vivatlas.models import Invite, User, UserSession

log = logging.getLogger(__name__)

COOKIE_NAME = "vivatlas_session"
SESSION_DAYS = 30

# Brute-force. After this many failures in a row the account is locked for this many
# minutes. Gently: the goal is to wear down brute-forcing, not punish the owner for a typo.
MAX_FAILS = 8
LOCK_MINUTES = 15

# Hash of a nonexistent password. Needed so that for an unknown email argon2 runs
# exactly as long as for a known one — otherwise the response time reveals who is
# registered. Computed once when the module loads.
_DUMMY_HASH = security.hash_password("no such user, this is a placeholder")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """A date from the database — with a timezone.

    SQLite doesn't store the timezone and returns a date without one. You can't
    compare such a date with datetime.now(UTC) — Python raises an error. Confirmed
    by a test: in production this would crash sign-in exactly when the account is
    locked. We assume everything in the database is UTC — UTC is all we write there.
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@dataclass
class LoginResult:
    ok: bool
    user: User | None = None
    needs_totp: bool = False
    locked_minutes: int = 0  # >0 — account locked, this many minutes left
    error: str = ""


def has_any_user(session: Session) -> bool:
    """Whether anyone exists at all. Empty — the program isn't set up yet."""
    return session.scalar(select(User.id).limit(1)) is not None


def check_login(session: Session, email: str, password: str) -> LoginResult:
    """Check email and password. Does NOT create a session — sign-in does that separately.

    Returns what to do next: let in, ask for the second code, or refuse.
    """
    email = email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))

    # Locked? We check before the password: no point trying, the door is closed.
    locked = _aware(user.locked_until) if user else None
    if locked and locked > _now():
        left = int((locked - _now()).total_seconds() // 60) + 1
        return LoginResult(ok=False, locked_minutes=left, error="auth.err.locked")

    # We always verify the password — even for a nonexistent email, against the placeholder.
    stored = user.password_hash if user else _DUMMY_HASH
    ok = security.verify_password(password, stored)

    if not user or not ok or not user.is_active:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILS:
                user.locked_until = _now() + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
        # The same response for "no such email" and "wrong password": we don't tip off
        # the brute-forcer that the email was guessed.
        return LoginResult(ok=False, error="auth.err.bad_credentials")

    # Password correct — reset the failure counter.
    user.failed_logins = 0
    user.locked_until = None

    # Rehash the password if the argon2 settings have been tightened since.
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    if user.totp_enabled_at:
        return LoginResult(ok=True, user=user, needs_totp=True)
    return LoginResult(ok=True, user=user)


def open_session(session: Session, user: User, request: Request, response: Response) -> str:
    """Create a session and set the cookie. Called once sign-in is confirmed. Returns the
    raw session token: the cookie carries it for the web UI, and the browser extension
    keeps the same token to send as a Bearer header on its own cross-site API calls."""
    raw = security.new_token()
    row = UserSession(
        user_id=user.id,
        token_hash=security.token_hash(raw),
        user_agent=(request.headers.get("user-agent") or "")[:256],
        ip=_client_ip(request),
        expires_at=_now() + timedelta(days=SESSION_DAYS),
    )
    session.add(row)
    user.last_login_at = _now()

    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return raw


def _token_from_request(request: Request) -> str | None:
    """The session token: the cookie for a browser, or an `Authorization: Bearer <token>`
    header for the extension (whose cross-site fetches don't carry the SameSite cookie)."""
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        return raw
    authz = request.headers.get("authorization", "")
    if authz[:7].lower() == "bearer ":
        return authz[7:].strip() or None
    return None


def current_user(session: Session, request: Request) -> User | None:
    """Who's at the door right now. None — nobody. Accepts the session cookie or a
    Bearer token (the extension)."""
    raw = _token_from_request(request)
    if not raw:
        return None
    row = session.scalar(
        select(UserSession).where(UserSession.token_hash == security.token_hash(raw))
    )
    if row is None or row.revoked_at is not None or _aware(row.expires_at) <= _now():
        return None
    # We update "last seen" no more than once every couple of minutes. Otherwise every
    # render of any page became a write to the database, and SQLite admits only one
    # writer at a time — that's a needless write for no reason and a needless cause of
    # locks. For "where am I signed in" minute-level precision isn't needed anyway.
    seen = _aware(row.last_seen_at)
    if seen is None or (_now() - seen) > timedelta(minutes=2):
        row.last_seen_at = _now()
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user


def close_session(session: Session, request: Request, response: Response) -> None:
    """Sign out: revoke this session and remove the cookie. Works for a cookie or a
    Bearer token (the extension)."""
    raw = _token_from_request(request)
    if raw:
        row = session.scalar(
            select(UserSession).where(UserSession.token_hash == security.token_hash(raw))
        )
        if row and row.revoked_at is None:
            row.revoked_at = _now()
    response.delete_cookie(COOKIE_NAME, path="/")


def revoke_all(session: Session, user: User) -> int:
    """Sign out on all devices. Returns how many sessions were closed."""
    rows = session.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    ).all()
    for row in rows:
        row.revoked_at = _now()
    return len(rows)


# --- ticket between the password and the second step --------------------
#
# The password is correct, but two-step verification is enabled. We need to carry "this
# user passed the password" to the second-code page without opening a session yet. We put
# a signed tag into a short-lived cookie: we store nothing in the database, and it can't be
# forged — the signature is on the secret key. It lives 5 minutes: enough to enter the
# code, and a ticket left on someone else's screen goes stale on its own.

TOTP_TICKET_COOKIE = "vivatlas_2fa"
_TICKET_MAX_AGE = 300


def _signer() -> TimestampSigner:
    if not settings.secret_key:
        raise security.SecretMissing("SECRET_KEY is not set — can't sign the second sign-in step.")
    return TimestampSigner(settings.secret_key, salt="skill-atlas/2fa-ticket")


def make_totp_ticket_token(user: User) -> str:
    """The signed 'passed the password, owes a code' tag. The web flow puts it in a
    cookie; the extension carries it in the JSON body between the two steps."""
    return _signer().sign(str(user.id)).decode("ascii")


def read_totp_ticket_token(token: str) -> int | None:
    """The user id from a ticket token, or None if forged/stale."""
    if not token:
        return None
    try:
        raw = _signer().unsign(token, max_age=_TICKET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return int(raw.decode("ascii"))
    except ValueError:
        return None


def issue_totp_ticket(response: Response, user: User, secure: bool) -> None:
    response.set_cookie(
        TOTP_TICKET_COOKIE,
        make_totp_ticket_token(user),
        max_age=_TICKET_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def read_totp_ticket(request: Request) -> int | None:
    return read_totp_ticket_token(request.cookies.get(TOTP_TICKET_COOKIE) or "")


def clear_totp_ticket(response: Response) -> None:
    response.delete_cookie(TOTP_TICKET_COOKIE, path="/")


# --- password reset link ---------------------------------------------------
#
# The link is signed with the secret key, lives an hour and isn't stored in the database:
# it can't be forged, and we don't create an extra table for one-time tokens. "One-time"
# rests on the password fingerprint: the token embeds the fingerprint of the current hash,
# and as soon as the password is changed (including via this same link), the fingerprint no
# longer matches — the old link is dead. So one link changes the password exactly once.

RESET_MAX_AGE = 3600  # seconds: an hour to reach the email and change the password


def _reset_serializer() -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise security.SecretMissing("SECRET_KEY is not set — can't sign the reset link.")
    return URLSafeTimedSerializer(settings.secret_key, salt="skill-atlas/password-reset")


def _pw_fingerprint(password_hash: str) -> str:
    """A short password fingerprint. Not the hash itself — no need to put that in the link;
    what suffices is something that changes with the password and makes the link dead."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def make_reset_token(user: User) -> str:
    """A signed token for the reset link. Called when the user asked for it."""
    return _reset_serializer().dumps({"uid": user.id, "fp": _pw_fingerprint(user.password_hash)})


def read_reset_token(session: Session, token: str, max_age: int = RESET_MAX_AGE) -> User | None:
    """Whom the link belongs to. None — forged, stale, or already used.

    We check everything: the signature, the expiry, that the user exists and is active,
    and that the password hasn't changed since issuance (fingerprint). Any slip — None,
    with no hints.
    """
    if not token:
        return None
    try:
        data = _reset_serializer().loads(token, max_age=max_age)
    except BadData:  # signature, expiry, or garbage — BadData covers all of it
        return None
    if not isinstance(data, dict):
        return None
    uid = data.get("uid")
    fp = data.get("fp")
    if not isinstance(uid, int) or not isinstance(fp, str):
        return None
    user = session.get(User, uid)
    if user is None or not user.is_active:
        return None
    if not security.same_secret(fp, _pw_fingerprint(user.password_hash)):
        return None
    return user


# --- invitations ------------------------------------------------------------
#
# The owner invites a user with a /join?code=… link. The database holds the HASH of the
# code, not the code itself (steal the database and you get no working links), same as with
# sessions. The link lives two weeks and is one-time: once accepted, we mark used_at. An
# invitation can be tied to an email (then on /join the email is already set) or open (email="").

INVITE_DAYS = 14


def make_invite(session: Session, email: str, created_by: int) -> str:
    """Create an invitation and return the RAW code for the link. We store the hash."""
    raw = security.new_token()
    session.add(
        Invite(
            code_hash=security.token_hash(raw),
            email=(email or "").strip().lower(),
            created_by=created_by,
            expires_at=_now() + timedelta(days=INVITE_DAYS),
        )
    )
    return raw


def read_invite(session: Session, code: str) -> Invite | None:
    """Whether the invitation is live by the code from the link. None — forged, stale, or
    already accepted."""
    if not code:
        return None
    row = session.scalar(select(Invite).where(Invite.code_hash == security.token_hash(code)))
    if row is None or row.used_at is not None or _aware(row.expires_at) <= _now():
        return None
    return row


def consume_invite(session: Session, inv: Invite, user: User) -> bool:
    """Mark the invitation accepted ATOMICALLY — one-time-ness under a race.

    The conditional UPDATE fires only while used_at is still empty; rowcount==0 means
    it was accepted by a parallel request first (for an OPEN invitation, where everyone
    has their own email, the users.email uniqueness wouldn't have caught the second
    account). Then no account may be created — the caller rolls back the transaction."""
    res = session.execute(
        update(Invite)
        .where(Invite.id == inv.id, Invite.used_at.is_(None))
        .values(used_at=_now(), used_by=user.id)
    )
    return res.rowcount == 1


def _client_ip(request: Request) -> str:
    """The visitor's address. Behind a tunnel the real address arrives in a header."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
