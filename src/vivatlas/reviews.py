"""Reviews on cards: a verdict, a note and the projects a tool fits.

Written by an AI agent (Maestro, through the MCP set_review tool) that has just read
untrusted content, so everything is checked here, on the server, the same way for
every caller: the verdict comes from a fixed list, the note is plain text with a
length cap, project names are short and few. The page renders all of it escaped and
never as markup or links. Author and time are always set here, never by the caller.
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas.models import REVIEW_VERDICTS, ArtifactReview, OAuthClient

NOTE_MAX = 2000
PROJECTS_MAX = 10
PROJECT_MAX = 60
VIA_MAX = 120

# Control characters except tab and newline. They have no business in a note and
# can be used to make a line render differently from how it reads in the source.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


class ReviewError(ValueError):
    """What was wrong with a review, in words an agent can act on."""


def clean_verdict(verdict: str) -> str:
    v = (verdict or "").strip().lower()
    if v not in REVIEW_VERDICTS:
        raise ReviewError("verdict must be one of: " + ", ".join(REVIEW_VERDICTS))
    return v


def clean_note(note: str) -> str:
    text = (note or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text).strip()
    if len(text) > NOTE_MAX:
        raise ReviewError(f"note is {len(text)} characters; the limit is {NOTE_MAX}")
    return text


def clean_projects(projects) -> list[str]:
    if projects is None:
        return []
    if isinstance(projects, str):
        projects = projects.split(",")
    out: list[str] = []
    seen: set[str] = set()
    for p in projects:
        name = " ".join(_CONTROL.sub("", str(p)).split())
        if not name:
            continue
        if len(name) > PROJECT_MAX:
            raise ReviewError(f"project name longer than {PROJECT_MAX} characters: {name[:20]}…")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    if len(out) > PROJECTS_MAX:
        raise ReviewError(f"at most {PROJECTS_MAX} projects")
    return out


def client_label(session: Session, client_id: str | None) -> str:
    """The name an OAuth client registered under, as plain text. The client chose it,
    so it is trimmed and stripped of control characters, and only ever shown escaped."""
    if not client_id:
        return ""
    row = session.get(OAuthClient, client_id)
    if row is None:
        return ""
    try:
        name = json.loads(row.info_json).get("client_name") or ""
    except (ValueError, AttributeError):
        name = ""
    return " ".join(_CONTROL.sub("", str(name)).split())[:VIA_MAX]


def upsert(
    session: Session,
    artifact_id: int,
    author_user_id: int,
    verdict: str,
    note: str = "",
    projects=None,
    via: str = "",
) -> ArtifactReview:
    """Write this author's review of the card, replacing their previous one."""
    v = clean_verdict(verdict)
    n = clean_note(note)
    ps = clean_projects(projects)
    row = session.scalar(
        select(ArtifactReview).where(
            ArtifactReview.artifact_id == artifact_id,
            ArtifactReview.author_user_id == author_user_id,
        )
    )
    if row is None:
        row = ArtifactReview(artifact_id=artifact_id, author_user_id=author_user_id)
        session.add(row)
    row.verdict = v
    row.note = n
    row.projects_json = json.dumps(ps, ensure_ascii=False)
    row.via = via[:VIA_MAX]
    session.flush()
    return row


def projects_of(row: ArtifactReview) -> list[str]:
    try:
        value = json.loads(row.projects_json or "[]")
    except ValueError:
        return []
    return [str(p) for p in value] if isinstance(value, list) else []


def for_card(session: Session, artifact_id: int) -> list[ArtifactReview]:
    return list(
        session.scalars(
            select(ArtifactReview)
            .where(ArtifactReview.artifact_id == artifact_id)
            .order_by(ArtifactReview.updated_at.desc(), ArtifactReview.id.desc())
        )
    )


def as_dict(row: ArtifactReview) -> dict:
    author = row.author
    return {
        "review_id": row.id,
        "verdict": row.verdict,
        "note": row.note,
        "projects": projects_of(row),
        "by": (author.display_name or author.email) if author else "",
        "via": row.via,
        "updated": row.updated_at.isoformat() if row.updated_at else "",
    }
