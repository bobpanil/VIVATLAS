"""Pollinations — free, keyless image generation.

The whole API is a URL: GET /prompt/<text> and the answer is the picture. No
account, no key, no quota to enable. What that buys is exactly what it costs —
their free tier serves whichever small model they are running that week, and
the pictures are soft and generic. Good enough to be chosen on purpose, which is
why it is opt-in (IMAGE_MODEL=pollinations:flux) rather than the default, and
why the designed cover stays behind it as the fallback.

Two things to know before turning it on: each card's name and one-line summary
leave the server (the same data the description model already sees, but a
different third party); and their queue is sometimes slow — forty seconds a
picture was observed — so the timeout is generous and a failure pauses drawing
for a while rather than trying every card in turn.
"""

import hashlib
import logging
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

BASE = "https://image.pollinations.ai"
PREFIX = "pollinations"  # IMAGE_MODEL=pollinations:<model>, e.g. pollinations:flux


class PollinationsError(Exception):
    pass


def is_pollinations(image_model: str) -> bool:
    return (image_model or "").strip().lower().startswith(PREFIX)


def model_name(image_model: str) -> str:
    """"pollinations:flux" -> "flux"; bare "pollinations" -> their default."""
    _, _, name = (image_model or "").strip().partition(":")
    return name.strip() or "flux"


class PollinationsImages:
    def __init__(self, timeout: float = 150.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "VIVATLAS/1.0 (+preview)"},
        )

    async def generate_image(self, prompt: str, model: str) -> bytes:
        """A picture for the prompt. Landscape at the card's proportions, no
        watermark. The seed is the prompt's own hash, so the same card asks for
        the same picture twice rather than a new one every lap."""
        seed = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:6], 16)
        params = {
            "width": 1280, "height": 800, "nologo": "true",
            "model": model_name(model), "seed": seed,
        }
        r = await self._client.get(f"/prompt/{quote(prompt)}", params=params)
        if r.status_code != 200:
            raise PollinationsError(f"pollinations: HTTP {r.status_code} {r.text[:120]}")
        if not r.headers.get("content-type", "").startswith("image/"):
            raise PollinationsError("pollinations: answered with something that isn't an image")
        return r.content

    async def aclose(self) -> None:
        await self._client.aclose()
