import pytest

from skill_atlas.importer import ImportError_, parse_url

# --- целый репозиторий ---


def test_plain_repo_url():
    s = parse_url("https://github.com/mvanhorn/last30days-skill")
    assert s.kind == "repo"
    assert s.full_repo == "mvanhorn/last30days-skill"
    assert s.suggested_name == "last30days-skill"


def test_repo_url_with_git_suffix():
    assert parse_url("https://github.com/Onflow-AI/Avenir-UX.git").kind == "repo"


def test_repo_url_with_trailing_slash():
    assert parse_url("https://github.com/a/b/").kind == "repo"


def test_tree_without_path_is_whole_repo():
    # /tree/main — это просто ветка, а не папка.
    s = parse_url("https://github.com/a/b/tree/main")
    assert s.kind == "repo"
    assert s.ref == "main"


# --- папка ---


def test_folder_url():
    # Ровно то, откуда приехали design-lib.
    s = parse_url("https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/stripe")
    assert s.kind == "folder"
    assert s.path == "design-md/stripe"
    assert s.ref == "main"
    assert s.suggested_name == "stripe"


def test_deep_folder_name_is_the_last_part():
    s = parse_url("https://github.com/a/b/tree/main/x/y/z/my-tool")
    assert s.suggested_name == "my-tool"


# --- файл ---


def test_blob_url():
    s = parse_url("https://github.com/a/b/blob/main/skills/thing/SKILL.md")
    assert s.kind == "file"
    assert s.path == "skills/thing/SKILL.md"
    assert s.suggested_name == "SKILL"


def test_raw_url():
    s = parse_url("https://raw.githubusercontent.com/a/b/main/docs/SKILL.md")
    assert s.kind == "file"
    assert s.owner == "a"
    assert s.path == "docs/SKILL.md"


# --- чего не умеем, о том говорим прямо ---


def test_gitlab_is_rejected_clearly():
    with pytest.raises(ImportError_, match="github.com"):
        parse_url("https://gitlab.com/a/b")


def test_garbage_is_rejected():
    with pytest.raises(ImportError_):
        parse_url("просто текст")
    with pytest.raises(ImportError_):
        parse_url("https://example.com/a/b")


def test_bare_github_root_is_rejected():
    with pytest.raises(ImportError_):
        parse_url("https://github.com")


def test_whitespace_is_tolerated():
    s = parse_url("  https://github.com/a/b  ")
    assert s.full_repo == "a/b"
