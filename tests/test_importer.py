import pytest

from vivatlas.importer import ImportError_, parse_url

# --- whole repository ---


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
    # /tree/main is just a branch, not a folder.
    s = parse_url("https://github.com/a/b/tree/main")
    assert s.kind == "repo"
    assert s.ref == "main"


# --- folder ---


def test_folder_url():
    # Exactly where design-lib came from.
    s = parse_url("https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/stripe")
    assert s.kind == "folder"
    assert s.path == "design-md/stripe"
    assert s.ref == "main"
    assert s.leaf == "stripe"
    # Name by the mirror rule: repository + folder, not just the folder. Otherwise
    # 74 sets from a single awesome-design-md would land on one address.
    assert s.suggested_name == "awesome-design-md-stripe"


def test_deep_folder_name_is_the_last_part():
    s = parse_url("https://github.com/a/b/tree/main/x/y/z/my-tool")
    assert s.leaf == "my-tool"
    assert s.suggested_name == "b-my-tool"


# --- file ---


def test_blob_url():
    s = parse_url("https://github.com/a/b/blob/main/skills/thing/SKILL.md")
    assert s.kind == "file"
    assert s.path == "skills/thing/SKILL.md"
    assert s.leaf == "SKILL"
    assert s.suggested_name == "b-SKILL"


def test_raw_url():
    s = parse_url("https://raw.githubusercontent.com/a/b/main/docs/SKILL.md")
    assert s.kind == "file"
    assert s.owner == "a"
    assert s.path == "docs/SKILL.md"


# --- what we can't do, we say plainly ---


def test_gitlab_is_rejected_clearly():
    with pytest.raises(ImportError_, match="github.com"):
        parse_url("https://gitlab.com/a/b")


def test_garbage_is_rejected():
    with pytest.raises(ImportError_):
        parse_url("just text")
    with pytest.raises(ImportError_):
        parse_url("https://example.com/a/b")


def test_bare_github_root_is_rejected():
    with pytest.raises(ImportError_):
        parse_url("https://github.com")


def test_whitespace_is_tolerated():
    s = parse_url("  https://github.com/a/b  ")
    assert s.full_repo == "a/b"


# --- name from the GitHub address (path mirror rule) ---


def test_whole_repo_mirrors_github_exactly():
    # Boris's own canonical case: the Gitea address mirrors the GitHub address.
    src = parse_url("https://github.com/mvanhorn/last30days-skill")
    assert src.mirror_owner == "mvanhorn"
    assert src.mirror_name == "last30days-skill"


def test_owner_case_is_kept():
    src = parse_url("https://github.com/Onflow-AI/Avenir-UX")
    assert src.mirror_owner == "Onflow-AI"
    assert src.mirror_name == "Avenir-UX"


def test_subfolder_of_a_monorepo_carries_the_folder():
    # 74 sets from a single awesome-design-md must split into different names,
    # otherwise all 74 would land on one address.
    a = parse_url("https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/airbnb")
    b = parse_url("https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/apple")
    assert a.mirror_owner == b.mirror_owner == "VoltAgent"
    assert a.mirror_name == "awesome-design-md-airbnb"
    assert b.mirror_name == "awesome-design-md-apple"
    assert a.mirror_name != b.mirror_name


def test_two_airbnb_from_different_sources_do_not_collide():
    # Boris's worry: the second airbnb must not clash with the first. They have different
    # owners — and the addresses diverge on their own.
    ours = parse_url("https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/airbnb")
    theirs = parse_url("https://github.com/someone/design-kits/tree/main/airbnb")
    assert (
        f"{ours.mirror_owner}/{ours.mirror_name}" != f"{theirs.mirror_owner}/{theirs.mirror_name}"
    )


def test_unsafe_characters_become_dashes_not_gone():
    # We don't drop the space: "foo bar" and "foobar" are different names.
    src = parse_url("https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/my kit")
    assert src.mirror_name == "awesome-design-md-my-kit"
