"""Turning cards into numbers and proximity search.

We search by brute force, without clever pointers. On 99-500 cards this is
tens of milliseconds: 500 × 1536 numbers is 3 MB, and scanning them is faster
than the overhead of any index. The rough ceiling of this approach is around
fifty thousand cards; we are a very long way from that.
"""

import hashlib
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas.ai.base import EmbeddingModel
from vivatlas.models import Artifact, Embedding

log = logging.getLogger(__name__)


def embedding_text(artifact: Artifact) -> str:
    """What exactly we turn into numbers.

    We take the name and descriptions rather than the raw documentation: the
    descriptions are already stripped of markup and clutter, and are structured
    the same way across all cards.
    """
    parts = [
        artifact.name,
        artifact.artifact_type,
        artifact.summary_short,
        artifact.summary_normal,
        artifact.summary_technical,
    ]
    text = "\n".join(p for p in parts if p)
    return text or artifact.name


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_blob(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


async def embed_artifact(
    session: Session, model: EmbeddingModel, artifact: Artifact, force: bool = False
) -> str:
    text = embedding_text(artifact)
    digest = text_hash(text)

    existing = session.scalar(
        select(Embedding).where(
            Embedding.artifact_id == artifact.id,
            Embedding.model == getattr(model, "model", "unknown"),
        )
    )
    if existing and existing.source_hash == digest and not force:
        return "unchanged"

    vector = await model.embed(text)

    if existing is None:
        session.add(
            Embedding(
                artifact_id=artifact.id,
                model=getattr(model, "model", "unknown"),
                dim=model.dim,
                vector=to_blob(vector),
                source_hash=digest,
            )
        )
        return "created"

    existing.vector = to_blob(vector)
    existing.dim = model.dim
    existing.source_hash = digest
    return "updated"


class VectorIndex:
    """All vectors held in memory. Rebuilt from scratch on every search — at
    our volume this is cheaper than tracking cache staleness."""

    def __init__(self, ids: list[int], matrix: np.ndarray) -> None:
        self.ids = ids
        self.matrix = matrix

    @classmethod
    def load(cls, session: Session, model_name: str) -> "VectorIndex":
        rows = session.execute(
            select(Embedding.artifact_id, Embedding.vector).where(Embedding.model == model_name)
        ).all()
        if not rows:
            return cls([], np.empty((0, 0), dtype=np.float32))

        ids = [r[0] for r in rows]
        matrix = np.vstack([from_blob(r[1]) for r in rows])
        # Normalise to unit length up front: then proximity is a simple
        # multiplication, without a division at every step.
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return cls(ids, matrix / norms)

    def search(self, query_vector: list[float], limit: int = 10) -> list[tuple[int, float]]:
        if not self.ids:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        scores = self.matrix @ (q / norm)
        top = np.argsort(-scores)[:limit]
        return [(self.ids[i], float(scores[i])) for i in top]
