import httpx
import pytest
import respx

from vivatlas.providers.gitea import _PAGE_SIZE, GiteaProvider

BASE = "https://git.example.com"
API = f"{BASE}/api/v1"


def repo_json(rid: int, name: str, private: bool = False) -> dict:
    return {
        "id": rid,
        "name": name,
        "owner": {"login": "skills-lib"},
        "default_branch": "main",
        "private": private,
        "archived": False,
        "empty": False,
        "html_url": f"{BASE}/skills-lib/{name}",
        "clone_url": f"{BASE}/skills-lib/{name}.git",
        "size": 24,
        "description": "",
        "updated_at": "2026-06-26T10:00:00Z",
    }


@respx.mock
async def test_sends_browser_user_agent():
    # The instance responds 403 without a recognizable User-Agent — if the header
    # is lost, scanning silently stops working entirely.
    route = respx.get(f"{API}/repos/search").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    provider = GiteaProvider(BASE)
    await provider.list_repositories()
    await provider.aclose()

    assert "Mozilla" in route.calls.last.request.headers["user-agent"]


@respx.mock
async def test_token_is_sent_when_given():
    route = respx.get(f"{API}/repos/search").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    provider = GiteaProvider(BASE, token="secret-token")
    await provider.list_repositories()
    await provider.aclose()

    assert route.calls.last.request.headers["authorization"] == "token secret-token"


@respx.mock
async def test_no_authorization_header_without_token():
    route = respx.get(f"{API}/repos/search").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    provider = GiteaProvider(BASE)
    await provider.list_repositories()
    await provider.aclose()

    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_walks_all_pages():
    page1 = [repo_json(i, f"repo-{i}") for i in range(_PAGE_SIZE)]
    page2 = [repo_json(999, "last-one")]

    respx.get(f"{API}/repos/search", params={"page": "1"}).mock(
        return_value=httpx.Response(200, json={"data": page1})
    )
    respx.get(f"{API}/repos/search", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json={"data": page2})
    )

    provider = GiteaProvider(BASE)
    repos = await provider.list_repositories()
    await provider.aclose()

    assert len(repos) == _PAGE_SIZE + 1
    assert repos[-1].name == "last-one"


@respx.mock
async def test_private_flag_is_carried_through():
    respx.get(f"{API}/repos/search").mock(
        return_value=httpx.Response(200, json={"data": [repo_json(1, "secret", private=True)]})
    )
    provider = GiteaProvider(BASE)
    repos = await provider.list_repositories()
    await provider.aclose()

    assert repos[0].is_private is True


@respx.mock
async def test_http_error_is_raised_not_swallowed():
    respx.get(f"{API}/repos/search").mock(return_value=httpx.Response(403))
    provider = GiteaProvider(BASE)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.list_repositories()
    await provider.aclose()


def test_provider_really_has_all_its_methods():
    # Once the write methods ended up added inside another function rather than the
    # class. The program built, the tests passed, but the import failed on a live run.
    from vivatlas.providers.gitea import GiteaProvider

    for name in (
        "list_repositories",
        "get_head_sha",
        "download_archive",
        "blob_shas",
        "repo_exists",
        "create_repo",
        "put_file",
        "delete_repo",
        "aclose",
    ):
        assert hasattr(GiteaProvider, name), f"GiteaProvider has no {name}"
        assert callable(getattr(GiteaProvider, name))


def test_provider_satisfies_the_common_interface():
    from vivatlas.providers.base import GitProvider
    from vivatlas.providers.gitea import GiteaProvider

    assert isinstance(GiteaProvider(BASE), GitProvider)
