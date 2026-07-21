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
        "search_artifacts",
        "recommend_artifact",
        "get_artifact",
        "list_artifacts",
        "list_tags",
        "catalog_overview",
        "list_recent_changes",
        "find_stale_artifacts",
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
