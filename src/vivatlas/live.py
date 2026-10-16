"""A fingerprint of what one person can see, so open pages can update themselves.

The catalogue changes under an open page all the time: a link sent from the phone or
the clipper becomes a card a few seconds later, the AI fills a description in, a card
moves to a folder on another device. Pages ask GET /live for this stamp every few
seconds while they're on screen and fetch themselves again only when it moves, so the
cheap question is asked often and the expensive answer only when there is news.

Every part is an aggregate over an indexed table, a handful of small queries; nothing
here reads card text. The stamp means nothing outside this comparison.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vivatlas import categories as catperm
from vivatlas import filters as flt
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    ArtifactReview,
    CaptureJob,
    Category,
    Change,
    Favorite,
    Preview,
    RemovedNotice,
)


def stamp(session: Session, user_id: int | None) -> str:
    visible = flt.visible_ids(user_id)
    parts = [
        # Cards appearing, going, or changing (a description, a rename, sharing).
        session.execute(
            select(func.count(), func.max(Artifact.id), func.max(Artifact.updated_at)).where(
                Artifact.id.in_(visible)
            )
        ).one(),
        session.scalar(
            select(func.max(Change.id)).where(
                Change.artifact_id.is_(None) | Change.artifact_id.in_(visible)
            )
        ),
        session.scalar(
            select(func.max(Preview.updated_at)).where(Preview.artifact_id.in_(visible))
        ),
        session.scalar(
            select(func.max(ArtifactReview.updated_at)).where(
                ArtifactReview.artifact_id.in_(visible)
            )
        ),
        # Folders: membership, and names, icons and order (no updated_at to read, so
        # a sum that any rename or move shifts).
        session.execute(
            select(func.count(), func.max(ArtifactCategory.id)).where(
                ArtifactCategory.artifact_id.in_(visible)
            )
        ).one(),
        session.execute(
            select(
                func.count(),
                func.max(Category.id),
                func.sum(
                    func.length(Category.name)
                    + func.length(Category.names_json)
                    + func.length(Category.icon)
                    + Category.position
                ),
            ).where(Category.id.in_(catperm.visible_category_ids(user_id)))
        ).one(),
    ]
    if user_id is not None:
        parts += [
            session.execute(
                select(func.count(), func.max(Favorite.id)).where(Favorite.user_id == user_id)
            ).one(),
            session.execute(
                select(func.count(), func.max(CaptureJob.updated_at)).where(
                    CaptureJob.user_id == user_id
                )
            ).one(),
            session.scalar(
                select(func.count())
                .select_from(RemovedNotice)
                .where(RemovedNotice.user_id == user_id, RemovedNotice.seen_at.is_(None))
            ),
        ]
    raw = repr([tuple(p) if hasattr(p, "_fields") else p for p in parts])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
