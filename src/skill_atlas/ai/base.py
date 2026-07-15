"""Общий интерфейс к моделям.

Сейчас реализован Google AI Studio. Если Google урежет бесплатный уровень —
пишется второй класс с теми же методами, остальной код не меняется.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextModel(Protocol):
    """Генерация текста по строгой схеме."""

    async def generate_json(self, prompt: str, schema: dict) -> dict: ...
    async def aclose(self) -> None: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    """Превращение текста в числа для поиска по смыслу."""

    dim: int

    async def embed(self, text: str) -> list[float]: ...
    async def aclose(self) -> None: ...
