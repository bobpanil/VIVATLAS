import io
import tarfile

from vivatlas.archive import read_archive
from vivatlas.upstream import (
    decide_status,
    detect_from_mirror,
    detect_from_readme,
)


def tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for path, data in files.items():
            info = tarfile.TarInfo(name=f"repo-x/{path}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


REAL_FOOTER = (
    b"# Cohere\n\nBrand stuff.\n\n---\n\nPart of the **design-lib** collection. "
    b"Source: [getdesign.md](https://getdesign.md/cohere/design-md) "
    b"\xc2\xb7 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md).\n"
)


# --- where it came from ---


def test_finds_source_line_in_readme():
    # Real README footer from design-lib.
    contents = read_archive(tar({"README.md": REAL_FOOTER, "DESIGN.md": b"---\nversion: alpha\n"}))
    ref = detect_from_readme(contents, "cohere", "DESIGN.md")

    assert ref is not None
    assert ref.repo == "VoltAgent/awesome-design-md"
    assert ref.path == "design-md/cohere/DESIGN.md"
    assert ref.kind == "github-file"


def test_no_source_line_means_no_source():
    # No source is written, so there is none. We don't guess.
    contents = read_archive(tar({"README.md": b"# Tool\nJust a tool.\n"}))
    assert detect_from_readme(contents, "tool", "README.md") is None


def test_random_github_link_is_not_a_source():
    # A GitHub link in the text isn't a source yet. It needs the word Source.
    contents = read_archive(
        tar({"README.md": b"# Tool\nSee also https://github.com/some/other for ideas.\n"})
    )
    assert detect_from_readme(contents, "tool", "README.md") is None


def test_unknown_upstream_gets_no_guessed_path():
    # We know the path for one source only. For the rest — empty, not a
    # made-up value: comparing the wrong things is worse than not comparing.
    contents = read_archive(tar({"README.md": b"Source: [x](https://github.com/foo/bar).\n"}))
    ref = detect_from_readme(contents, "thing", "SKILL.md")
    assert ref.repo == "foo/bar"
    assert ref.path == ""


def test_mirror_metadata_is_read():
    ref = detect_from_mirror("https://github.com/Onflow-AI/Avenir-UX.git")
    assert ref is not None
    assert ref.repo == "Onflow-AI/Avenir-UX"
    assert ref.kind == "gitea-mirror"


def test_empty_mirror_url_is_none():
    assert detect_from_mirror("") is None
    assert detect_from_mirror("https://gitlab.com/a/b") is None  # only GitHub for now


# --- KEY POINT: tell a new version apart from your own edit ---

BASE_LOCAL = "aaa"
BASE_UP = "aaa"  # matched at the baseline


def test_identical_is_in_sync():
    assert decide_status("aaa", "aaa", BASE_LOCAL, BASE_UP) == "in-sync"


def test_upstream_moved_means_update_available():
    # Theirs is new, we didn't touch ours.
    assert decide_status("aaa", "bbb", BASE_LOCAL, BASE_UP) == "update-available"


def test_we_edited_means_locally_modified():
    # We edited, theirs is unchanged. Can't update — we'd overwrite the edit.
    assert decide_status("ccc", "aaa", BASE_LOCAL, BASE_UP) == "locally-modified"


def test_both_changed_means_diverged():
    assert decide_status("ccc", "bbb", BASE_LOCAL, BASE_UP) == "diverged"


def test_without_baseline_we_do_not_guess():
    # No baseline, no conclusion. "Files differ" means nothing on its own.
    assert decide_status("aaa", "", BASE_LOCAL, BASE_UP) == "unknown"
    assert decide_status("", "bbb", BASE_LOCAL, BASE_UP) == "unknown"


def test_local_edit_is_never_reported_as_update_available():
    # The most dangerous mistake: taking the user's edit for a new version and
    # offering to update over it. We check this directly.
    for local in ("ccc", "ddd", "zzz"):
        status = decide_status(local, BASE_UP, BASE_LOCAL, BASE_UP)
        assert status != "update-available", f"edit {local} mistaken for an update"
        assert status == "locally-modified"
