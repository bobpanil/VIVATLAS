from tests.test_archive import make_tar

from skill_atlas.archive import read_archive
from skill_atlas.detector import detect


def d(files: dict[str, bytes]):
    return detect(read_archive(make_tar(files)))


# --- реальные формы из git.example.com ---


def test_design_lib_repo():
    # Так устроены все 74 репозитория design-lib.
    r = d({"DESIGN.md": b"# Airbnb\nBrand colors", "README.md": b"x", "preview.svg": b"<svg/>"})
    assert r.artifact_type == "design-kit"
    assert r.anchor_path == "DESIGN.md"
    assert r.preview_path == "preview.svg"
    assert r.confidence >= 0.9


def test_single_file_skill():
    # skills-lib/brandkit — ровно один файл.
    r = d({"SKILL.md": b"# Brandkit\nGenerates brand kits"})
    assert r.artifact_type == "skill"
    assert r.anchor_path == "SKILL.md"


def test_skill_mentioning_claude_is_marked_as_claude_skill():
    r = d({"SKILL.md": b"# Tool\nUse with Claude Code"})
    assert r.artifact_type == "claude-skill"


def test_python_project():
    # skills-lib/avenir-ux — проект на 51 файл.
    r = d({"pyproject.toml": b"[project]", "README.md": b"# Avenir", "src/a/__init__.py": b""})
    assert r.artifact_type == "project"
    assert r.anchor_path == "README.md"


def test_docs_only_repo_is_unknown_not_guessed():
    # crgr-security-scanners — документация и CI, опорного файла нет.
    r = d({"README.md": b"# Scanners", "SECURITY_SCANNERS_INSTALL.md": b"install"})
    assert r.artifact_type == "unknown"
    assert r.confidence < 0.5  # честно признаёмся, что не уверены


# --- приоритеты и края ---


def test_skill_md_wins_over_project_markers():
    r = d({"SKILL.md": b"# S", "pyproject.toml": b"[project]"})
    assert r.artifact_type == "skill"


def test_claude_commands_dir():
    r = d({".claude/commands/deploy.md": b"# deploy", "README.md": b"x"})
    assert r.artifact_type == "claude-command"


def test_mcp_server():
    r = d({"mcp.json": b"{}", "README.md": b"x"})
    assert r.artifact_type == "mcp-server"


def test_empty_repo_is_unknown_with_low_confidence():
    r = d({"logo.png": b"\x89PNG"})
    assert r.artifact_type == "unknown"
    assert r.confidence <= 0.2
    assert r.anchor_path is None


def test_doc_text_includes_anchor_and_readme():
    r = d({"SKILL.md": b"ANCHOR TEXT", "README.md": b"README TEXT"})
    assert "ANCHOR TEXT" in r.doc_text
    assert "README TEXT" in r.doc_text


def test_reasons_are_recorded():
    r = d({"SKILL.md": b"# x"})
    assert r.reasons and "SKILL.md" in r.reasons[0]
