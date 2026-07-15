"""Поиск: по словам, по смыслу и вместе.

Два способа дополняют друг друга. Поиск по словам точно находит по названию
(`brandkit`), но бессилен, если запрос на русском, а инструмент назван
по-английски. Поиск по смыслу наоборот: понимает "вытащить таблицы из пдф",
но может промахнуться мимо точного имени. Поэтому по умолчанию — оба.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from skill_atlas.ai.base import EmbeddingModel
from skill_atlas.embeddings import VectorIndex
from skill_atlas.models import Artifact

# Сглаживание при слиянии двух списков. 60 — общепринятое значение: оно не даёт
# первому месту одного списка задавить весь другой список.
RRF_K = 60


class Mode(StrEnum):
    WORDS = "words"
    MEANING = "meaning"
    BOTH = "both"


@dataclass
class Hit:
    artifact_id: int
    score: float
    by_words: float | None = None
    by_meaning: float | None = None
    artifact: Artifact | None = None
    reasons: list[str] = field(default_factory=list)


def _clean_query(query: str) -> str:
    """FTS5 понимает свой язык запросов, и кавычка или скобка из обычного
    вопроса роняют его с ошибкой. Оставляем слова, остальное выкидываем."""
    words = re.findall(r"[\w\-]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{w}"' for w in words if len(w) > 1)


def search_by_words(session: Session, query: str, limit: int = 20) -> list[tuple[int, float]]:
    cleaned = _clean_query(query)
    if not cleaned:
        return []
    rows = session.execute(
        text(
            """
            SELECT rowid, bm25(artifacts_fts, 10.0, 5.0, 3.0, 2.0, 1.0) AS rank
            FROM artifacts_fts
            WHERE artifacts_fts MATCH :q
            ORDER BY rank
            LIMIT :lim
            """
        ),
        {"q": cleaned, "lim": limit},
    ).all()
    # bm25 в SQLite отрицательный, и чем меньше — тем лучше. Переворачиваем,
    # чтобы больше значило лучше, как везде.
    return [(r[0], -float(r[1])) for r in rows]


async def search_by_meaning(
    session: Session, model: EmbeddingModel, query: str, limit: int = 20
) -> list[tuple[int, float]]:
    index = VectorIndex.load(session, getattr(model, "model", "unknown"))
    if not index.ids:
        return []
    vector = await model.embed(query)
    return index.search(vector, limit=limit)


def _rrf(ranked_lists: list[list[tuple[int, float]]]) -> dict[int, float]:
    """Слияние по местам, а не по оценкам.

    Оценки двух способов несравнимы: bm25 и близость векторов живут в разных
    шкалах. Поэтому смотрим не на оценку, а на место в списке.
    """
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for position, (artifact_id, _score) in enumerate(ranked):
            scores[artifact_id] = scores.get(artifact_id, 0.0) + 1.0 / (RRF_K + position + 1)
    return scores


async def search(
    session: Session,
    query: str,
    model: EmbeddingModel | None = None,
    mode: Mode = Mode.BOTH,
    limit: int = 10,
    artifact_type: str | None = None,
) -> list[Hit]:
    by_words: list[tuple[int, float]] = []
    by_meaning: list[tuple[int, float]] = []

    if mode in (Mode.WORDS, Mode.BOTH):
        by_words = search_by_words(session, query, limit=limit * 3)
    if mode in (Mode.MEANING, Mode.BOTH) and model is not None:
        by_meaning = await search_by_meaning(session, model, query, limit=limit * 3)

    words_map = dict(by_words)
    meaning_map = dict(by_meaning)

    if mode == Mode.BOTH:
        merged = _rrf([by_words, by_meaning])
    elif mode == Mode.WORDS:
        merged = words_map
    else:
        merged = meaning_map

    order = sorted(merged.items(), key=lambda kv: -kv[1])

    hits: list[Hit] = []
    for artifact_id, score in order:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            continue
        if artifact_type and artifact.artifact_type != artifact_type:
            continue

        reasons = []
        if artifact_id in words_map:
            reasons.append("совпали слова")
        if artifact_id in meaning_map:
            reasons.append("близко по смыслу")

        hits.append(
            Hit(
                artifact_id=artifact_id,
                score=score,
                by_words=words_map.get(artifact_id),
                by_meaning=meaning_map.get(artifact_id),
                artifact=artifact,
                reasons=reasons,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def index_artifact_for_words(session: Session, artifact: Artifact) -> None:
    """Обновить строку карточки в таблице поиска по словам."""
    session.execute(text("DELETE FROM artifacts_fts WHERE rowid = :id"), {"id": artifact.id})
    session.execute(
        text(
            """
            INSERT INTO artifacts_fts(rowid, name, summary_short, summary_normal,
                                      summary_technical, doc_text)
            VALUES (:id, :name, :s1, :s2, :s3, :doc)
            """
        ),
        {
            "id": artifact.id,
            "name": artifact.name,
            "s1": artifact.summary_short,
            "s2": artifact.summary_normal,
            "s3": artifact.summary_technical,
            "doc": artifact.doc_text,
        },
    )


def all_artifact_ids(session: Session) -> list[int]:
    return list(session.scalars(select(Artifact.id)))
