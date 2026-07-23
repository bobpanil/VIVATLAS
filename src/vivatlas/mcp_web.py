"""The OAuth consent screen for the MCP connector.

The MCP OAuth provider (mcp_oauth) redirects the browser here from `/authorize`. This
page reuses the normal sign-in, shows what the client (e.g. ChatGPT) is asking for, and
on Allow mints the authorization code and bounces back to the client. It's the only
human step in the OAuth handshake.

The route is open (listed in api._OPEN_PREFIXES) and does its own sign-in check, so it
can carry the `req` id through the login round-trip — require_login's redirect keeps
only the path, which would drop it.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from vivatlas import auth, i18n, mcp_oauth
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.mcp_oauth import SCOPE

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
router = APIRouter()


# --- OAuth discovery at the domain root --------------------------------------
#
# The MCP server is mounted at /mcp-server, but an MCP client (ChatGPT) discovers
# OAuth from the DOMAIN ROOT: the 401 on /mcp-server/mcp advertises
# resource_metadata="…/.well-known/oauth-protected-resource/mcp-server/mcp" (RFC 9728
# path-insertion), and the authorization-server metadata is looked up at
# /.well-known/oauth-authorization-server/mcp-server (RFC 8414). The mounted app serves
# those documents *under* its own prefix, so at the root they'd 404 — and require_login
# would 303 them to the sign-in page, which reads to the client as "does not implement
# OAuth". We serve them here at the root instead. Values mirror what the SDK emits.


def _mcp_base() -> str | None:
    return settings.public_url.rstrip("/") if settings.public_url else None


def protected_resource_metadata() -> dict:
    base = _mcp_base()
    if base is None:
        raise HTTPException(404)
    return {
        "resource": f"{base}/mcp-server/mcp",
        "authorization_servers": [f"{base}/mcp-server"],
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata() -> dict:
    base = _mcp_base()
    if base is None:
        raise HTTPException(404)
    issuer = f"{base}/mcp-server"
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "revocation_endpoint": f"{issuer}/revoke",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "revocation_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
    }


@router.get("/.well-known/oauth-protected-resource/mcp-server/mcp")
@router.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource() -> JSONResponse:
    return JSONResponse(protected_resource_metadata())


@router.get("/.well-known/oauth-authorization-server/mcp-server")
@router.get("/.well-known/oauth-authorization-server")
def oauth_authorization_server() -> JSONResponse:
    return JSONResponse(authorization_server_metadata())


def _current_user(request: Request) -> tuple[int, str] | None:
    with session_scope() as session:
        user = auth.current_user(session, request)
        if user is None:
            return None
        return user.id, (user.display_name or user.email)


@router.get("/mcp/consent", response_class=HTMLResponse)
def consent(request: Request, req: str = ""):
    me = _current_user(request)
    if me is None:
        # Keep the whole URL (with ?req=) across sign-in; _safe_next allows it.
        nxt = quote(f"/mcp/consent?req={req}", safe="")
        return RedirectResponse(f"/login?next={nxt}", status_code=303)
    pending = mcp_oauth.pending_authorization(req)
    if pending is None:
        return templates.TemplateResponse(request, "mcp_consent.html", {"state": "expired"})
    client_id, _params = pending
    return templates.TemplateResponse(
        request,
        "mcp_consent.html",
        {
            "state": "ask",
            "req": req,
            "client_name": mcp_oauth.client_label(client_id),
            "account": me[1],
        },
    )


@router.post("/mcp/consent", response_class=HTMLResponse)
def consent_decide(
    request: Request,
    req: Annotated[str, Form()] = "",
    decision: Annotated[str, Form()] = "",
):
    me = _current_user(request)
    if me is None:
        return RedirectResponse("/login", status_code=303)
    url = (
        mcp_oauth.complete_authorization(req, me[0])
        if decision == "allow"
        else mcp_oauth.deny_authorization(req)
    )
    if url:
        return RedirectResponse(url, status_code=303)
    return templates.TemplateResponse(request, "mcp_consent.html", {"state": "expired"})
