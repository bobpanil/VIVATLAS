import json

import pytest

from vivatlas import mcp_server
from vivatlas.models import Artifact, ArtifactTag, Repository, Source, Tag


@pytest.fixture
def catalog(make_session, monkeypatch):
    """Feed the tools a temporary database instead of the production one."""
    session = make_session()
    source = Source(kind="fake", base_url="https://x", display_name="Fake")
    session.add(source)
    session.flush()
    repo = Repository(
        source_id=source.id,
        external_id="1",
        owner="design-lib",
        name="airbnb",
        default_branch="main",
        html_url="https://git.example.com/design-lib/airbnb",
    )
    session.add(repo)
    session.flush()
    art = Artifact(
        repository_id=repo.id,
        name="airbnb",
        artifact_type="design-kit",
        confidence=0.95,
        summary_short="Airbnb design kit",
        summary_normal="Colours and fonts",
        summary_technical="Tokens",
        file_count=3,
        source_commit="abc12345deadbeef",
        shared=True,  # shared card from the shared catalogue — MCP returns only these
    )
    session.add(art)
    session.flush()
    tag = Tag(slug="design-system", label="design-system", category="type")
    session.add(tag)
    session.flush()
    session.add(
        ArtifactTag(
            artifact_id=art.id, tag_id=tag.id, source="derived", confidence=0.95, origin="rule"
        )
    )
    session.commit()

    from contextlib import contextmanager

    @contextmanager
    def fake_scope():
        yield session

    monkeypatch.setattr(mcp_server, "session_scope", fake_scope)
    return session, art


async def call(name: str, args: dict) -> dict:
    result = await mcp_server.mcp.call_tool(name, args)
    return json.loads(result[0].text)


async def test_all_tools_are_registered():
    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        # read
        "search_artifacts",
        "recommend_artifact",
        "get_artifact",
        "list_artifacts",
        "list_tags",
        "catalog_overview",
        "list_recent_changes",
        "find_stale_artifacts",
        # write (per-user, OAuth)
        "add_to_library",
        "edit_card",
        "list_folders",
        "file_card",
    }


async def test_every_tool_has_a_description():
    # The description is what the model on the other side uses to decide whether
    # to call the tool. Without it, the tool is useless.
    for tool in await mcp_server.mcp.list_tools():
        assert tool.description and len(tool.description.strip()) > 20, tool.name


async def test_get_artifact_returns_card(catalog):
    _session, art = catalog
    d = await call("get_artifact", {"artifact_id": art.id})

    assert d["name"] == "design-lib/airbnb"
    assert d["summary_short"] == "Airbnb design kit"
    assert d["tags"][0]["source"] == "derived"
    assert d["commit"] == "abc12345"  # shortened, not the full one


async def test_get_artifact_missing_says_so_without_crashing(catalog):
    d = await call("get_artifact", {"artifact_id": 99999})
    assert "error" in d


async def test_quality_notes_warn_about_weak_data(catalog):
    session, art = catalog
    art.confidence = 0.1
    art.summary_short = ""
    session.commit()

    d = await call("get_artifact", {"artifact_id": art.id})
    # The other side needs to know what to trust and what not to.
    assert any("low confidence" in n for n in d["notes"])
    assert any("no description" in n for n in d["notes"])


async def test_catalog_overview_mentions_private_are_skipped(catalog):
    d = await call("catalog_overview", {})
    assert d["artifacts"] == 1
    assert "Private ones aren't scanned" in d["note"]


async def test_list_artifacts_limit_is_capped(catalog):
    # A model with limited memory reads the response — we can't let it ask for
    # a thousand cards and flood its whole memory.
    d = await call("list_artifacts", {"limit": 9999})
    assert d["showing"] <= mcp_server.MAX_LIMIT


async def test_list_artifacts_filters_by_type(catalog):
    assert (await call("list_artifacts", {"type": "design-kit"}))["showing"] == 1
    assert (await call("list_artifacts", {"type": "mcp-server"}))["showing"] == 0


async def test_list_tags(catalog):
    d = await call("list_tags", {})
    assert d["items"][0]["tag"] == "design-system"
    assert d["items"][0]["count"] == 1


class _Tok:
    def __init__(self, subject):
        self.subject = subject


async def test_private_card_is_scoped_to_the_signed_in_caller(catalog, monkeypatch):
    session, art = catalog
    art.shared = False
    art.owner_user_id = 7
    session.commit()

    # anonymous connection → only shared cards, so this private one is "not found"
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: None)
    assert "error" in await call("get_artifact", {"artifact_id": art.id})

    # signed in as its owner → visible
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: _Tok("7"))
    assert (await call("get_artifact", {"artifact_id": art.id}))["name"] == "design-lib/airbnb"

    # signed in as someone else → not found (no cross-user leakage)
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: _Tok("999"))
    assert "error" in await call("get_artifact", {"artifact_id": art.id})


async def test_write_tools_refuse_anonymous(catalog, monkeypatch):
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: None)
    # call_tool surfaces the ValueError; either it raises or returns an error payload.
    try:
        out = await mcp_server.mcp.call_tool("list_folders", {})
        assert out[1].get("isError") or "error" in json.loads(out[0].text).get("error", "sign")
    except Exception as exc:  # noqa: BLE001
        assert "Sign in" in str(exc)


async def test_oauth_token_roundtrip(make_session, monkeypatch):
    from contextlib import contextmanager

    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    from vivatlas import mcp_oauth
    from vivatlas.models import User

    session = make_session()
    session.add(User(id=7, email="a@x.com", display_name="A", password_hash="h"))
    session.commit()

    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(mcp_oauth, "session_scope", scope)

    prov = mcp_oauth.VivatlasOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="c1", redirect_uris=["https://chatgpt.example/callback"]
    )
    await prov.register_client(client)
    assert (await prov.get_client("c1")).client_id == "c1"

    params = AuthorizationParams(
        state="st",
        scopes=[mcp_oauth.SCOPE],
        code_challenge="challenge123",
        redirect_uri="https://chatgpt.example/callback",
        redirect_uri_provided_explicitly=True,
    )
    url = await prov.authorize(client, params)
    req_id = url.split("req=")[1]

    redirect = mcp_oauth.complete_authorization(req_id, user_id=7)
    assert "state=st" in redirect and "code=" in redirect
    code = redirect.split("code=")[1].split("&")[0]

    ac = await prov.load_authorization_code(client, code)
    assert ac is not None and ac.subject == "7"

    token = await prov.exchange_authorization_code(client, ac)
    assert token.access_token and token.refresh_token

    access = await prov.load_access_token(token.access_token)
    assert access is not None and access.subject == "7"

    await prov.revoke_token(access)
    assert await prov.load_access_token(token.access_token) is None


def test_oauth_discovery_documents_served_at_domain_root(monkeypatch):
    """An MCP client (ChatGPT) discovers OAuth from the domain root — the mounted
    /mcp-server app serves those documents under its own prefix, so we mirror them at
    the root. Without this the auth middleware 303s /.well-known/* to the sign-in page
    and the client reports "does not implement OAuth"."""
    from fastapi.testclient import TestClient

    from vivatlas.api import app
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "public_url", "https://vivatlas.example.com")
    client = TestClient(app)  # no lifespan: these routes are open, need no DB

    # The exact URL the 401 WWW-Authenticate header advertises (RFC 9728).
    r = client.get(
        "/.well-known/oauth-protected-resource/mcp-server/mcp", follow_redirects=False
    )
    assert r.status_code == 200, r.status_code  # not a 303 to /login
    assert r.headers["content-type"].startswith("application/json")
    pr = r.json()
    assert pr["resource"] == "https://vivatlas.example.com/mcp-server/mcp"
    assert pr["authorization_servers"] == ["https://vivatlas.example.com/mcp-server"]

    # The authorization-server metadata (RFC 8414 path-insertion for the issuer).
    r = client.get(
        "/.well-known/oauth-authorization-server/mcp-server", follow_redirects=False
    )
    assert r.status_code == 200, r.status_code
    md = r.json()
    assert md["issuer"] == "https://vivatlas.example.com/mcp-server"
    assert md["authorization_endpoint"] == "https://vivatlas.example.com/mcp-server/authorize"
    assert md["token_endpoint"] == "https://vivatlas.example.com/mcp-server/token"
    assert md["registration_endpoint"] == "https://vivatlas.example.com/mcp-server/register"

    # No public URL configured → the MCP stays anonymous and advertises no OAuth
    # (404, still not a login redirect).
    monkeypatch.setattr(settings, "public_url", "")
    r = client.get(
        "/.well-known/oauth-protected-resource/mcp-server/mcp", follow_redirects=False
    )
    assert r.status_code == 404, r.status_code
