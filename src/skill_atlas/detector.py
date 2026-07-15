"""Что за инструмент лежит в репозитории.

Правило простое: репозиторий = одна карточка. Так устроены все 99 репозиториев
в git.example.com — внутри всегда одна вещь. Если однажды появится
репозиторий с несколькими инструментами внутри, здесь появится разбор на
несколько карточек; пока его нет и он не нужен.
"""

import re
from dataclasses import dataclass, field

from skill_atlas.archive import RepoContents

# Опорный файл → тип и насколько уверены. Порядок важен: выигрывает первое
# совпадение.
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

# Если опорного файла нет — смотрим, на что похож репозиторий.
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
            reasons.append(f"в корне найден {filename}")
            return Detection(
                artifact_type=_refine_skill_type(artifact_type, contents, reasons),
                confidence=confidence,
                anchor_path=found.path,
                preview_path=_find_preview(contents),
                doc_text=_collect_doc(contents, found.path),
                reasons=reasons,
            )

    if _has_claude_dir(contents, "commands"):
        reasons.append("есть .claude/commands")
        return _simple(contents, "claude-command", 0.85, reasons)
    if _has_claude_dir(contents, "agents"):
        reasons.append("есть .claude/agents")
        return _simple(contents, "claude-agent", 0.85, reasons)

    marker = next((m for m in _PROJECT_MARKERS if contents.get(m)), None)
    if marker:
        reasons.append(f"похоже на проект: есть {marker}")
        return _simple(contents, "project", 0.6, reasons)

    if contents.get("README.md"):
        reasons.append("только README, тип неочевиден")
        return _simple(contents, "unknown", 0.3, reasons)

    reasons.append("опознать не по чему")
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
    """SKILL.md встречается и у ChatGPT, и у Claude — уточняем по содержимому."""
    if artifact_type != "skill":
        return artifact_type
    anchor = contents.find("SKILL.md", "skill.md")
    text = (anchor.text or "").lower() if anchor else ""
    if "claude" in text:
        reasons.append("в тексте упомянут Claude")
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
    """Текст для описания и поиска: опорный файл, при нехватке — плюс README."""
    chunks: list[str] = []
    if anchor_path:
        anchor = contents.get(anchor_path)
        if anchor and anchor.text:
            chunks.append(anchor.text)
    readme = contents.get("README.md")
    if readme and readme.text and readme.path != anchor_path:
        chunks.append(readme.text)
    return _trim("\n\n---\n\n".join(chunks))


def _trim(text: str, limit: int = 24_000) -> str:
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text if len(text) <= limit else text[:limit] + "\n…(обрезано)"
