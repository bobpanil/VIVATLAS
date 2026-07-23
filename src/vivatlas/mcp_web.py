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

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from vivatlas import auth, i18n, mcp_oauth
from vivatlas.db import session_scope

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
router = APIRouter()


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
