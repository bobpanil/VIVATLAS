import pytest

from vivatlas.finder import (
    Candidate,
    Finder,
    classify,
    extract_bare_repos,
    extract_repos,
    parse_og,
)

# --- what we were given ---


def test_github_link_is_recognised():
    assert classify("https://github.com/DeusData/codebase-memory-mcp") == "github"
    assert classify("github.com/VoltAgent/awesome-design-md") == "github"


def test_other_site_is_web():
    assert classify("https://www.facebook.com/share/r/1Ek3xcL84A/") == "web"
    assert classify("https://voltagent.dev/") == "web"


def test_words_are_text():
    assert classify("a skill that gathers news over the past 30 days") == "text"


def test_file_kind_comes_from_extension(tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"x")
    mp4 = tmp_path / "reel.mp4"
    mp4.write_bytes(b"x")
    assert classify(str(png)) == "image"
    assert classify(str(mp4)) == "video"


def test_missing_file_is_not_an_image():
    # A path to a nonexistent file is just words, not a picture.
    assert classify("C:/no/such/file.png") == "text"


# --- full links ---


def test_link_from_a_reel_caption():
    # A real case: the caption under a reel in a screenshot.
    heard = (
        "Link to the repository 👉 More combos in my Telegram via the link in my bio. "
        "Link: https://github.com/DeusData/codebase-memory-mcp I've got everything about..."
    )
    assert extract_repos(heard) == ["DeusData/codebase-memory-mcp"]


def test_bare_rule_alone_would_lose_that_link():
    # Why full links are parsed separately and first: the rule for a bare
    # "owner/repository" trips over github.com/ and loses the whole address.
    heard = "Link: https://github.com/DeusData/codebase-memory-mcp"
    assert "DeusData/codebase-memory-mcp" not in extract_bare_repos(heard)


def test_link_variants():
    assert extract_repos("see https://github.com/a/b.git") == ["a/b"]
    assert extract_repos("(https://github.com/a/b)") == ["a/b"]
    assert extract_repos("https://github.com/a/b/tree/main/skills/x") == ["a/b"]


def test_no_repeats():
    text = "github.com/a/b and again github.com/a/b"
    assert extract_repos(text) == ["a/b"]


def test_common_github_links_are_not_tools():
    text = "https://github.com/features/actions and https://github.com/topics/mcp"
    assert extract_repos(text) == []


# --- a bare address: this is how they write it in images ---


def test_bare_address_from_a_screenshot():
    # A GitHub card in a screenshot: the name is written out, but there's no link.
    assert extract_bare_repos("VoltAgent/awesome-design-md 102k stars") == [
        "VoltAgent/awesome-design-md"
    ]


def test_ordinary_phrases_are_not_addresses():
    assert extract_bare_repos("input/output") == []
    assert extract_bare_repos("true/false and yes/no") == []
    assert extract_bare_repos("speed 60 km/ms") == []


def test_domain_is_not_an_owner():
    assert extract_bare_repos("go to voltagent.dev/docs") == []


def test_bare_finds_are_limited():
    text = " ".join(f"own{i}/repo{i}" for i in range(10))
    assert len(extract_bare_repos(text)) == 3


# --- the page ---


def test_og_tags_both_ways_round():
    html = """
    <meta property="og:title" content="A movie about skills">
    <meta content="https://x/v.mp4" property="og:video">
    """
    og = parse_og(html)
    assert og["title"] == "A movie about skills"
    assert og["video"] == "https://x/v.mp4"


def test_no_og_tags_is_empty_not_an_error():
    assert parse_og("<html><body>nothing</body></html>") == {}


def test_same_repo_written_differently_is_one_repo():
    # A real case: on voltagent.dev the links are written both ways.
    # For GitHub the case of the owner doesn't matter — it's one repository.
    text = "github.com/VoltAgent/voltagent and github.com/voltagent/voltagent"
    assert extract_repos(text) == ["VoltAgent/voltagent"]


def test_og_values_are_unescaped():
    # A real case, cost us a 403: in markup & is always written as &amp;.
    # Leave it as is and you break the signature in the video link.
    html = '<meta property="og:video" content="https://v.fbcdn.net/x.mp4?oh=aa&amp;oe=6A5D9DE9">'
    assert parse_og(html)["video"] == "https://v.fbcdn.net/x.mp4?oh=aa&oe=6A5D9DE9"


def test_og_title_is_unescaped_too():
    html = '<meta property="og:title" content="&quot;Skill&quot; for Claude &amp; Cursor">'
    assert parse_og(html)["title"] == '"Skill" for Claude & Cursor'


# --- we take the model only at its word, and we check the word ---


class FakeModel:
    def __init__(self, **data):
        self.data = {
            "heard": "",
            "tool_name": "",
            "github_repo": "",
            "keywords": "",
            "stars_mentioned": 0,
            **data,
        }

    async def generate_json(self, prompt, schema):
        return self.data

    async def generate_json_with_media(self, prompt, schema, mime_type, data_base64):
        return self.data

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_invented_address_is_thrown_away(monkeypatch):
    # A real case: after listening to the reel, the model produced skills/last-30-day.
    # There's no such repository — and we'd have suggested pulling it in.
    finder = Finder()

    async def nothing_exists(repo):
        return False

    async def search(result):
        result.candidates.append(
            Candidate(repo="mvanhorn/last30days-skill", url="", stars=52316, why="found")
        )

    monkeypatch.setattr(finder, "_exists", nothing_exists)
    monkeypatch.setattr(finder, "_search", search)

    result = await finder.find(
        "a skill about news for the last 30 days",
        FakeModel(github_repo="skills/last-30-day", tool_name="last-30-days-skill"),
    )
    assert [c.repo for c in result.candidates] == ["mvanhorn/last30days-skill"]
    assert any("don't trust" in n for n in result.notes)
    await finder.aclose()


@pytest.mark.asyncio
async def test_real_address_from_the_model_is_used(monkeypatch):
    finder = Finder()

    async def everything_exists(repo):
        return True

    async def describe(repo, why, exact=False):
        return Candidate(repo=repo, url=f"https://github.com/{repo}", why=why, exact=exact)

    monkeypatch.setattr(finder, "_exists", everything_exists)
    monkeypatch.setattr(finder, "_describe", describe)

    result = await finder.find(
        "that very skill", FakeModel(github_repo="DeusData/codebase-memory-mcp")
    )
    assert [c.repo for c in result.candidates] == ["DeusData/codebase-memory-mcp"]
    assert result.candidates[0].exact
    await finder.aclose()
