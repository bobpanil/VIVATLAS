"""End-to-end OAuth + MCP check against a running VIVATLAS whose PUBLIC_URL is BASE.

Not collected by pytest (no test_ prefix): it needs a live server and a signed-in
session. It replays how ChatGPT, Claude.ai, Claude Code, Gemini CLI and an OIDC-style
client register and sign in, and then runs the official MCP Python SDK client. This is
what found that tokens looked expired on servers east of UTC and that some clients
were refused over scopes.

For each client profile: discovery -> dynamic registration -> authorize (+ consent as
a signed-in user) -> token (PKCE) -> MCP initialize / tools/list / tools/call -> refresh.
Plus one run with the official MCP Python SDK client doing the whole dance itself.

Usage: python oauth_e2e.py BASE COOKIE_NAME COOKIE_VALUE
"""

import asyncio
import base64
import hashlib
import json
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

BASE, COOKIE_NAME, COOKIE = sys.argv[1], sys.argv[2], sys.argv[3]
MCP = f"{BASE}/mcp-server/mcp"

PROFILES = {
    "claude_code": dict(
        reg={
            "client_name": "Claude Code (vivatlas)",
            "redirect_uris": ["http://localhost:53123/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        scope=None,
    ),
    "claude_ai": dict(
        reg={
            "client_name": "Claude",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
        scope="vivatlas",
    ),
    "chatgpt": dict(
        reg={
            "client_name": "ChatGPT",
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_basic",
        },
        scope="vivatlas",
    ),
    "gemini_cli": dict(
        reg={
            "client_name": "Gemini CLI MCP Client",
            "redirect_uris": ["http://localhost:7777/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "",
        },
        scope=None,
    ),
    "gemini_cli_scoped": dict(
        reg={
            "client_name": "Gemini CLI MCP Client",
            "redirect_uris": ["http://localhost:7777/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "vivatlas",
        },
        scope="vivatlas",
    ),
    "oidc_style_client": dict(
        reg={
            "client_name": "Some MCP client",
            "redirect_uris": ["http://127.0.0.1:9999/cb"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid profile offline_access",
        },
        scope="openid profile offline_access",
    ),
}


def pkce():
    v = secrets.token_urlsafe(48)
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def consent(client: httpx.Client, authorize_url: str) -> str:
    """Follow authorize as a browser would, approve on the consent page, return the
    redirect to the client's callback (which carries code and state)."""
    r = client.get(authorize_url, follow_redirects=False)
    if r.status_code not in (302, 303, 307):
        raise RuntimeError(f"authorize: {r.status_code} {r.text[:200]}")
    loc = r.headers["location"]
    consent_url = loc if loc.startswith("http") else BASE + loc
    req = parse_qs(urlparse(consent_url).query).get("req", [""])[0]
    if not req:
        raise RuntimeError(f"authorize redirected somewhere else: {loc}")
    page = client.get(consent_url, cookies={COOKIE_NAME: COOKIE})
    if page.status_code != 200 or "allow" not in page.text.lower():
        raise RuntimeError(f"consent page: {page.status_code}")
    d = client.post(
        f"{BASE}/mcp/consent",
        data={"req": req, "decision": "allow"},
        cookies={COOKIE_NAME: COOKIE},
        follow_redirects=False,
    )
    if d.status_code not in (302, 303):
        raise RuntimeError(f"consent post: {d.status_code} {d.text[:200]}")
    return d.headers["location"]


def mcp_call(client, token, method, params, session_id=None, rid=1):
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session_id:
        h["Mcp-Session-Id"] = session_id
    r = client.post(
        MCP, headers=h, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    body = r.text
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        data = [ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")]
        body = data[-1] if data else "{}"
    return r, (json.loads(body) if body.strip() else {})


def run_profile(name, prof):
    out = {"profile": name}
    with httpx.Client(timeout=30) as c:
        # Discovery the way MCP clients do it: 401 -> resource metadata -> AS metadata.
        r = c.post(
            MCP,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        )
        rm = r.headers.get("www-authenticate", "")
        prm_url = (
            rm.split('resource_metadata="')[1].split('"')[0] if "resource_metadata" in rm else None
        )
        prm = c.get(prm_url).json()
        as_base = prm["authorization_servers"][0]
        p = urlparse(as_base)
        asm = c.get(
            f"{p.scheme}://{p.netloc}/.well-known/oauth-authorization-server{p.path}"
        ).json()
        out["discovery"] = (
            "ok"
            if r.status_code == 401 and asm.get("registration_endpoint")
            else f"fail {r.status_code}"
        )
        # Registration.
        reg = c.post(asm["registration_endpoint"], json=prof["reg"])
        if reg.status_code not in (200, 201):
            out["register"] = f"FAIL {reg.status_code} {reg.text[:160]}"
            return out
        ci = reg.json()
        out["register"] = (
            f"ok (auth={ci.get('token_endpoint_auth_method')}, "
            f"secret={'yes' if ci.get('client_secret') else 'no'})"
        )
        # Authorize + consent.
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(8)
        q = {
            "response_type": "code",
            "client_id": ci["client_id"],
            "redirect_uri": prof["reg"]["redirect_uris"][0],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": prm["resource"],
        }
        if prof["scope"] is not None:
            q["scope"] = prof["scope"]
        try:
            cb = consent(c, f"{asm['authorization_endpoint']}?{urlencode(q)}")
        except RuntimeError as e:
            out["authorize"] = f"FAIL {e}"
            return out
        cbq = parse_qs(urlparse(cb).query)
        if "code" not in cbq or cbq.get("state", [""])[0] != state:
            out["authorize"] = f"FAIL callback {cb[:160]}"
            return out
        out["authorize"] = "ok"
        # Token.
        form = {
            "grant_type": "authorization_code",
            "code": cbq["code"][0],
            "redirect_uri": q["redirect_uri"],
            "code_verifier": verifier,
            "client_id": ci["client_id"],
            "resource": prm["resource"],
        }
        auth = None
        if ci.get("token_endpoint_auth_method") == "client_secret_post":
            form["client_secret"] = ci["client_secret"]
        elif ci.get("token_endpoint_auth_method") == "client_secret_basic":
            auth = (ci["client_id"], ci["client_secret"])
        tok = c.post(asm["token_endpoint"], data=form, auth=auth)
        if tok.status_code != 200:
            out["token"] = f"FAIL {tok.status_code} {tok.text[:160]}"
            return out
        tj = tok.json()
        out["token"] = f"ok (refresh={'yes' if tj.get('refresh_token') else 'no'})"
        # MCP session.
        r, j = mcp_call(
            c,
            tj["access_token"],
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": name, "version": "1"},
            },
        )
        sid = r.headers.get("mcp-session-id")
        mcp_call(c, tj["access_token"], "notifications/initialized", {}, sid, rid=None)
        r2, tools = mcp_call(c, tj["access_token"], "tools/list", {}, sid, rid=2)
        names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
        r3, call = mcp_call(
            c,
            tj["access_token"],
            "tools/call",
            {"name": "list_artifacts", "arguments": {"limit": 5}},
            sid,
            rid=3,
        )
        ok_call = "result" in call and not call["result"].get("isError")
        out["mcp"] = (
            f"ok ({len(names)} tools, list_artifacts {'ok' if ok_call else 'FAIL'})"
            if r.status_code == 200 and names
            else f"FAIL init {r.status_code}"
        )
        # Refresh.
        if tj.get("refresh_token"):
            rf = {
                "grant_type": "refresh_token",
                "refresh_token": tj["refresh_token"],
                "client_id": ci["client_id"],
            }
            if ci.get("token_endpoint_auth_method") == "client_secret_post":
                rf["client_secret"] = ci["client_secret"]
            rr = c.post(asm["token_endpoint"], data=rf, auth=auth)
            out["refresh"] = (
                "ok"
                if rr.status_code == 200 and rr.json().get("access_token")
                else f"FAIL {rr.status_code} {rr.text[:120]}"
            )
    return out


async def run_official_sdk():
    """The official MCP Python SDK client doing discovery, registration, PKCE, token and
    the session itself; only the browser step (consent) is simulated."""
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider, TokenStorage
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

    class Mem(TokenStorage):
        def __init__(self):
            self.t = None
            self.c = None

        async def get_tokens(self) -> OAuthToken | None:
            return self.t

        async def set_tokens(self, tokens: OAuthToken) -> None:
            self.t = tokens

        async def get_client_info(self) -> OAuthClientInformationFull | None:
            return self.c

        async def set_client_info(self, info: OAuthClientInformationFull) -> None:
            self.c = info

    box = {}

    async def redirect_handler(url: str) -> None:
        with httpx.Client(timeout=30) as c:
            box["cb"] = consent(c, url)

    async def callback_handler():
        q = parse_qs(urlparse(box["cb"]).query)
        return q["code"][0], q.get("state", [None])[0]

    provider = OAuthClientProvider(
        server_url=MCP,
        client_metadata=OAuthClientMetadata(
            client_name="official MCP SDK client",
            redirect_uris=["http://localhost:3030/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=Mem(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    async with streamablehttp_client(MCP, auth=provider) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            res = await s.call_tool("list_changes", {"limit": 3})
            return {
                "profile": "official_python_sdk",
                "mcp": f"ok ({len(tools.tools)} tools, "
                f"list_changes {'ok' if not res.isError else 'FAIL'})",
            }


if __name__ == "__main__":
    for name, prof in PROFILES.items():
        try:
            print(json.dumps(run_profile(name, prof)))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"profile": name, "error": f"{type(e).__name__}: {e}"[:300]}))
    try:
        print(json.dumps(asyncio.run(run_official_sdk())))
    except Exception as e:  # noqa: BLE001
        print(
            json.dumps(
                {"profile": "official_python_sdk", "error": f"{type(e).__name__}: {str(e)[:300]}"}
            )
        )
