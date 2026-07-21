"""Common interface to the models.

Currently Google AI Studio is implemented. If Google cuts the free tier,
write a second class with the same methods; the rest of the code stays unchanged.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextModel(Protocol):
    """Generate text against a strict schema."""

    async def generate_json(self, prompt: str, schema: dict) -> dict: ...

    async def generate_json_with_media(
        self, prompt: str, schema: dict, mime_type: str, data_base64: str
    ) -> dict:
        """Same, but with an image, video, or audio."""
        ...

    async def aclose(self) -> None: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    """Turn text into numbers for semantic search."""

    dim: int

    async def embed(self, text: str) -> list[float]: ...
    async def aclose(self) -> None: ...
