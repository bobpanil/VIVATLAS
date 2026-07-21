"""What kind of tool lives in the repository.

The rule is simple: repository = one card. That's how all 99 repositories in the
observed Gitea are laid out — there's always one thing inside. If a repository
with several tools inside ever shows up, this is where the split into several
cards will appear; for now there isn't one and it isn't needed.
"""

import re
from dataclasses import dataclass, field

from vivatlas.archive import RepoContents

# Anchor file → type and how confident we are. Order matters: the first
# match wins.
ANCHORS: list[tuple[str, str, float]] = [
    ("SKILL.md", "skill", 0.95),
    ("skill.md", "skill", 0.95),
    ("DESIGN.md", "design-kit", 0.95),
    ("AGENTS.md", "agent", 0.85),
    ("mcp.json", "mcp-server", 0.9),
    (".mcp.json", "mcp-server", 0.7),
    ("plugin.json", "plugin", 0.85),
    ("manifest.json", "plugin", 0.6),
]

# If there's no anchor file — look at what the repository resembles.
_PROJECT_MARKERS = ("pyproject.toml", "package.json", "requirements.txt", "go.mod", "Cargo.toml")


@dataclass
class Detection:
    artifact_type: str
    confidence: float
    anchor_path: str | None
    preview_path: str | None = None
    doc_text: str = ""
    reasons: list[str] = field(default_factory=list)


def detect(contents: RepoContents) -> Detection:
    reasons: list[str] = []

    for filename, artifact_type, confidence in ANCHORS:
        found = contents.get(filename)
        if found is not None:
            reasons.append(f"found {filename} in the root")
            return Detection(
                artifact_type=_refine_skill_type(artifact_type, contents, reasons),
                confidence=confidence,
                anchor_path=found.path,
                preview_path=_find_preview(contents),
                doc_text=_collect_doc(contents, found.path),
                reasons=reasons,
            )

    if _has_claude_dir(contents, "commands"):
        reasons.append("has .claude/commands")
        return _simple(contents, "claude-command", 0.85, reasons)
    if _has_claude_dir(contents, "agents"):
        reasons.append("has .claude/agents")
        return _simple(contents, "claude-agent", 0.85, reasons)

    marker = next((m for m in _PROJECT_MARKERS if contents.get(m)), None)
    if marker:
        reasons.append(f"looks like a project: has {marker}")
        return _simple(contents, "project", 0.6, reasons)

    if contents.get("README.md"):
        reasons.append("README only, type unclear")
        return _simple(contents, "unknown", 0.3, reasons)

    if _fallback_docs(contents):
        # Type not identified, but there's something to read — a description comes out anyway.
        reasons.append("documentation exists, but type unclear")
        return _simple(contents, "unknown", 0.3, reasons)

    reasons.append("nothing to identify by")
    return Detection("unknown", 0.1, None, reasons=reasons)


def _simple(contents: RepoContents, artifact_type: str, confidence: float, reasons: list[str]):
    readme = contents.get("README.md")
    return Detection(
        artifact_type=artifact_type,
        confidence=confidence,
        anchor_path=readme.path if readme else None,
        preview_path=_find_preview(contents),
        doc_text=_collect_doc(contents, readme.path if readme else None),
        reasons=reasons,
    )


def _refine_skill_type(artifact_type: str, contents: RepoContents, reasons: list[str]) -> str:
    """SKILL.md appears in both ChatGPT and Claude — refine by content."""
    if artifact_type != "skill":
        return artifact_type
    anchor = contents.find("SKILL.md", "skill.md")
    text = (anchor.text or "").lower() if anchor else ""
    if "claude" in text:
        reasons.append("Claude mentioned in the text")
        return "claude-skill"
    return "skill"


def _has_claude_dir(contents: RepoContents, sub: str) -> bool:
    prefix = f".claude/{sub}/"
    return any(p.lower().startswith(prefix) and p.endswith(".md") for p in contents.paths)


def _find_preview(contents: RepoContents) -> str | None:
    for name in ("preview.svg", "preview.png", "preview.jpg"):
        found = contents.get(name)
        if found:
            return found.path
    return None


def _collect_doc(contents: RepoContents, anchor_path: str | None) -> str:
    """Text for the description and search.

    Order: anchor file, README, and if neither exists — whatever documentation
    turns up. The last step isn't cosmetic: skills-lib/crgr-security-scanners has
    neither SKILL.md nor README.md, but does have SECURITY_SCANNERS_INSTALL.md.
    Without this step the card came out described as "no documentation", and by
    meaning there was no finding it.
    """
    chunks: list[str] = []
    used: set[str] = set()

    if anchor_path:
        anchor = contents.get(anchor_path)
        if anchor and anchor.text:
            chunks.append(anchor.text)
            used.add(anchor.path)

    readme = contents.get("README.md")
    if readme and readme.text and readme.path not in used:
        chunks.append(readme.text)
        used.add(readme.path)

    if not chunks:
        for f in _fallback_docs(contents):
            if f.path not in used:
                chunks.append(f"# {f.path}\n\n{f.text}")
                used.add(f.path)

    return _trim("\n\n---\n\n".join(chunks))


# Housekeeping files, not counted as documentation.
_NOT_DOCS = ("license", "changelog", "contributing", "code_of_conduct", "security.md")


def _fallback_docs(contents: RepoContents, limit: int = 3) -> list:
    """Any text files that look like documentation. Largest first."""
    candidates = [
        f
        for f in contents.files
        if f.text
        and f.path.lower().endswith((".md", ".txt"))
        and "/" not in f.path  # root only: nested files usually aren't about the essence
        and not any(skip in f.path.lower() for skip in _NOT_DOCS)
    ]
    candidates.sort(key=lambda f: -len(f.text or ""))
    return candidates[:limit]


def _trim(text: str, limit: int = 24_000) -> str:
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"
