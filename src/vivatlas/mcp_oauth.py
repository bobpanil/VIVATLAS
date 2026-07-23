"""OAuth authorization server for the MCP endpoint.

Lets an MCP client — ChatGPT's Developer-Mode connector — sign in as a VIVATLAS user
and act on their behalf. The `mcp` SDK provides the protocol plumbing (discovery
metadata, dynamic client registration, `/authorize`, `/token`, `/revoke`, PKCE, bearer
auth); this module implements the provider it calls, wiring it to VIVATLAS's users and
its existing token hashing.

The user approves once on a consent page that reuses the normal sign-in. The issued
access token carries the user id as its `subject`; every MCP tool reads that and scopes
to it (private + shared), so ChatGPT sees exactly what that user sees.

Access/refresh tokens live in the DB (hashed, like sessions). Auth codes and pending
consents are short-lived and kept in memory (single process; they last minutes).
"""

import time
from datetime import UTC, datetime, timedelta

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from vivatlas import security
from vivatlas.db import session_scope
from vivatlas.models import OAuthClient, OAuthToken as OAuthTokenRow

SCOPE = "vivatlas"  # a single scope — full access as the signed-in user
ACCESS_TTL = 3600  # 1 hour
REFRESH_TTL = 60 * 24 * 3600  # 60 days
CODE_TTL = 300  # 5 minutes to redeem an auth code
PENDING_TTL = 600  # 10 minutes to finish the consent screen


def _now() -> datetime:
    return datetime.now(UTC)


# Short-lived, in-memory, single-process. These live for minutes and don't need to
# survive a restart; the long-lived access/refresh tokens are in the database.
_PENDING: dict[str, tuple[str, AuthorizationParams, float]] = {}
_CODES: dict[str, AuthorizationCode] = {}


def _sweep() -> None:
    now = time.time()
    for rid, (_c, _p, ts) in list(_PENDING.items()):
        if now - ts > PENDING_TTL:
            _PENDING.pop(rid, None)
    for code, ac in list(_CODES.items()):
        if ac.expires_at < now:
            _CODES.pop(code, None)


def _active_token(session, raw: str, kind: str) -> OAuthTokenRow | None:
    row = (
        session.query(OAuthTokenRow)
        .filter_by(token_hash=security.token_hash(raw), kind=kind)
        .first()
    )
    if row is None or row.revoked_at is not None:
        return None
    exp = row.expires_at
    if exp is not None:
        if exp.tzinfo is None:  # SQLite hands back naive datetimes
            exp = exp.replace(tzinfo=UTC)
        if exp < _now():
            return None
    return row


class VivatlasOAuthProvider(OAuthAuthorizationServerProvider):
    # --- dynamic client registration -----------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with session_scope() as s:
            row = s.get(OAuthClient, client_id)
            return OAuthClientInformationFull.model_validate_json(row.info_json) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        info = client_info.model_dump_json()
        with session_scope() as s:
            existing = s.get(OAuthClient, client_info.client_id)
            if existing:
                existing.info_json = info
            else:
                s.add(OAuthClient(client_id=client_info.client_id, info_json=info))

    # --- authorization: hand the user to the consent page --------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        _sweep()
        req_id = security.new_token()
        _PENDING[req_id] = (client.client_id, params, time.time())
        # Root-relative: the browser resolves it against this origin and lands on the
        # same-origin consent page, which reuses the normal VIVATLAS sign-in.
        return f"/mcp/consent?req={req_id}"

    # --- authorization codes -------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        _sweep()
        ac = _CODES.get(authorization_code)
        return ac if ac and ac.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        _CODES.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.subject, authorization_code.scopes)

    # --- refresh tokens ------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with session_scope() as s:
            row = _active_token(s, refresh_token, "refresh")
            if row is None or row.client_id != client.client_id:
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=row.scopes.split() if row.scopes else [],
                expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
                subject=str(row.user_id),
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        with session_scope() as s:  # rotate: revoke the presented refresh token
            row = _active_token(s, refresh_token.token, "refresh")
            if row is not None:
                row.revoked_at = _now()
        return self._issue(client.client_id, refresh_token.subject, scopes or refresh_token.scopes)

    # --- access-token validation (runs on every tool call) -------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        with session_scope() as s:
            row = _active_token(s, token, "access")
            if row is None:
                return None
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes.split() if row.scopes else [],
                expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
                subject=str(row.user_id),
            )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with session_scope() as s:
            row = (
                s.query(OAuthTokenRow)
                .filter_by(token_hash=security.token_hash(token.token))
                .first()
            )
            if row is not None:
                row.revoked_at = _now()

    # --- helpers -------------------------------------------------------------

    def _issue(self, client_id: str, subject: str | None, scopes: list[str]) -> OAuthToken:
        access, refresh = security.new_token(), security.new_token()
        try:
            user_id = int(subject) if subject is not None else 0
        except ValueError:
            user_id = 0
        scope_str = " ".join(scopes or [SCOPE])
        with session_scope() as s:
            s.add(
                OAuthTokenRow(
                    token_hash=security.token_hash(access),
                    kind="access",
                    client_id=client_id,
                    user_id=user_id,
                    scopes=scope_str,
                    expires_at=_now() + timedelta(seconds=ACCESS_TTL),
                )
            )
            s.add(
                OAuthTokenRow(
                    token_hash=security.token_hash(refresh),
                    kind="refresh",
                    client_id=client_id,
                    user_id=user_id,
                    scopes=scope_str,
                    expires_at=_now() + timedelta(seconds=REFRESH_TTL),
                )
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=scope_str,
        )


provider = VivatlasOAuthProvider()


# --- consent-page helpers (used by the web route) ----------------------------


def pending_authorization(req_id: str) -> tuple[str, AuthorizationParams] | None:
    """(client_id, params) for a pending consent, or None if unknown/expired."""
    _sweep()
    got = _PENDING.get(req_id)
    if got is None:
        return None
    client_id, params, _ts = got
    return client_id, params


def complete_authorization(req_id: str, user_id: int) -> str | None:
    """The user approved: mint an auth code bound to them and return the client's
    redirect URL (with code + state). None if the request expired."""
    got = _PENDING.pop(req_id, None)
    if got is None:
        return None
    client_id, params, _ts = got
    code = security.new_token()
    _CODES[code] = AuthorizationCode(
        code=code,
        scopes=params.scopes or [SCOPE],
        expires_at=time.time() + CODE_TTL,
        client_id=client_id,
        code_challenge=params.code_challenge,
        redirect_uri=params.redirect_uri,
        redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        subject=str(user_id),
    )
    return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)


def deny_authorization(req_id: str) -> str | None:
    """The user declined: return the client's redirect URL with an error, or None."""
    got = _PENDING.pop(req_id, None)
    if got is None:
        return None
    _client_id, params, _ts = got
    return construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)


def client_label(client_id: str) -> str:
    """A human name for the consent screen ('ChatGPT'), best-effort."""
    with session_scope() as s:
        row = s.get(OAuthClient, client_id)
    if row is None:
        return "An application"
    try:
        info = OAuthClientInformationFull.model_validate_json(row.info_json)
        return info.client_name or "An application"
    except Exception:
        return "An application"
