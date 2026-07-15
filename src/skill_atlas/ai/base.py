"""Общий интерфейс к моделям.

Сейчас реализован Google AI Studio. Если Google урежет бесплатный уровень —
пишется второй класс с теми же методами, остальной код не меняется.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextModel(Protocol):
    """Генерация текста по строгой схеме."""

    async def generate_json(self, prompt: str, schema: dict) -> dict: ...

    async def generate_json_with_media(
        self, prompt: str, schema: dict, mime_type: str, data_base64: str
    ) -> dict:
        """То же, но с картинкой, видео или звуком."""
        ...

    async def aclose(self) -> None: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    """Превращение текста в числа для поиска по смыслу."""

    dim: int

    async def embed(self, text: str) -> list[float]: ...
    async def aclose(self) -> None: ...
