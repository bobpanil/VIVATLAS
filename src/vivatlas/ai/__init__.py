"""Реестр моделей."""

from vivatlas.ai.base import EmbeddingModel, TextModel
from vivatlas.ai.google import GoogleEmbeddingModel, GoogleTextModel
from vivatlas.config import settings

__all__ = ["EmbeddingModel", "TextModel", "build_text_model", "build_embedding_model"]


def build_text_model() -> TextModel:
    return GoogleTextModel(
        api_key=settings.google_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )


def build_embedding_model() -> EmbeddingModel:
    return GoogleEmbeddingModel(
        api_key=settings.google_api_key,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
