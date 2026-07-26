"""Model registry.

Two providers, split by what only one of them can do. Embedding stays with Google:
every stored vector is 1536 numbers from gemini-embedding-2, and a vector from
another model wouldn't sit in the same space, so search would quietly go wrong.
Text — descriptions, translations — is tied to no one, so it can go to Ollama and
leave Google's tight free quota for the part that can't move.
"""

import logging

from vivatlas.ai.base import EmbeddingModel, TextModel
from vivatlas.ai.google import GoogleEmbeddingModel, GoogleTextModel
from vivatlas.ai.ollama import OllamaTextModel
from vivatlas.config import settings

log = logging.getLogger(__name__)

__all__ = ["EmbeddingModel", "TextModel", "build_text_model", "build_embedding_model"]


def _build_google_text() -> GoogleTextModel:
    return GoogleTextModel(
        api_key=settings.google_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )


class FallbackTextModel:
    """Ollama first, Google if that doesn't work out.

    Ollama's cloud can't enforce a schema — that's a local-only feature — so a reply
    can come back as prose, or with a field missing. The provider already asks twice;
    when it still can't produce the form we hand that one call to Google rather than
    leave a card undescribed. Audio and video always go to Google: the text models
    can't watch a clip. Google is built only if it's actually needed, so a working
    Ollama costs no Google quota at all.
    """

    def __init__(self, primary: TextModel) -> None:
        self._primary = primary
        self._google: GoogleTextModel | None = None
        # Shown on the card as "described by".
        self.model = getattr(primary, "model", "ollama")
        # How the work actually got done — the answer to "is the second model pulling
        # its weight?". Read by Admin's check button; see admin_web.ai_benchmark.
        self.primary_name = self.model
        self.served_by_primary = 0
        self.served_by_fallback = 0
        self.last_error = ""

    def _fallback(self) -> GoogleTextModel:
        if self._google is None:
            self._google = _build_google_text()
        return self._google

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        try:
            result = await self._primary.generate_json(prompt, schema)
        except Exception as exc:  # noqa: BLE001 — any provider failure is Google's turn
            log.warning("text: Ollama couldn't answer (%s) — falling back to Google", exc)
            self.served_by_fallback += 1
            self.last_error = str(exc)[:300]
            result = await self._fallback().generate_json(prompt, schema)
            self.model = getattr(self._google, "model", self.model)
            return result
        self.served_by_primary += 1
        self.model = self.primary_name
        return result

    async def generate_json_with_media(
        self, prompt: str, schema: dict, mime_type: str, data_base64: str
    ) -> dict:
        result = await self._fallback().generate_json_with_media(
            prompt, schema, mime_type, data_base64
        )
        self.model = getattr(self._google, "model", self.model)
        return result

    async def aclose(self) -> None:
        try:
            await self._primary.aclose()
        finally:
            if self._google is not None:
                await self._google.aclose()


def build_text_model() -> TextModel:
    """The model that writes. Ollama when it's configured, otherwise Google as before —
    and a misconfigured Ollama falls back instead of taking the catalogue down."""
    if settings.text_provider.strip().lower() == "ollama":
        try:
            return FallbackTextModel(
                OllamaTextModel(
                    api_key=settings.ollama_api_key,
                    model=settings.ollama_model,
                    base_url=settings.ollama_url,
                    timeout=settings.llm_timeout_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 — carry on with Google
            log.warning("text: Ollama isn't usable (%s) — using Google", exc)
    return _build_google_text()


def build_embedding_model() -> EmbeddingModel:
    return GoogleEmbeddingModel(
        api_key=settings.google_api_key,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
