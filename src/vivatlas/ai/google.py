"""Google AI Studio (Gemini)."""

import asyncio
import json
import logging

import httpx

log = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta"

# How many times to retry on 429/503. The free tier responds like this regularly.
_MAX_RETRIES = 4


class GoogleAIError(RuntimeError):
    pass


class _GoogleClient:
    def __init__(self, api_key: str, timeout: float) -> None:
        if not api_key:
            raise GoogleAIError("GOOGLE_API_KEY is not set — add the key to .env")
        self._api_key = api_key
        # The key goes in a header, not as a URL parameter (?key=...). Google
        # accepts both, but httpx logs full URLs — so the key would leak into
        # every log line. Headers don't get logged.
        self._client = httpx.AsyncClient(
            base_url=_BASE,
            timeout=timeout,
            headers={"x-goog-api-key": api_key},
        )

    async def _post(self, model: str, method: str, payload: dict) -> dict:
        delay = 2.0
        for attempt in range(_MAX_RETRIES):
            response = await self._client.post(f"/models/{model}:{method}", json=payload)
            if response.status_code in (429, 503):
                if attempt == _MAX_RETRIES - 1:
                    raise GoogleAIError(
                        f"{model}: not responding after {_MAX_RETRIES} attempts "
                        f"(HTTP {response.status_code}). The daily quota may have run out."
                    )
                log.warning("%s: HTTP %s, waiting %.0fs", model, response.status_code, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 400:
                raise GoogleAIError(f"{model}: HTTP {response.status_code} {response.text[:200]}")
            return response.json()
        raise GoogleAIError("unreachable")

    async def aclose(self) -> None:
        await self._client.aclose()


class GoogleTextModel(_GoogleClient):
    def __init__(self, api_key: str, model: str, timeout: float = 120.0) -> None:
        super().__init__(api_key, timeout)
        self.model = model

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        """The response comes as a filled-in form, not free text."""
        return await self._generate([{"text": prompt}], schema)

    async def generate_json_with_media(
        self, prompt: str, schema: dict, mime_type: str, data_base64: str
    ) -> dict:
        """With an image, video, or audio.

        Verified on live data: gemini-3.1-flash-lite accepts audio, video, and
        images — on the free tier. A 1.5 MB clip cost 4600 tokens.
        """
        return await self._generate(
            [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": data_base64}}],
            schema,
        )

    async def _generate(self, parts: list[dict], schema: dict) -> dict:
        data = await self._post(
            self.model,
            "generateContent",
            {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                    "maxOutputTokens": 8192,
                },
            },
        )
        candidate = (data.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise GoogleAIError(f"empty response, finishReason={candidate.get('finishReason')}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GoogleAIError(f"response didn't parse as JSON: {text[:200]}") from exc


class GoogleEmbeddingModel(_GoogleClient):
    def __init__(self, api_key: str, model: str, dim: int, timeout: float = 60.0) -> None:
        super().__init__(api_key, timeout)
        self.model = model
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        data = await self._post(
            self.model,
            "embedContent",
            {
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.dim,
            },
        )
        values = data["embedding"]["values"]
        if len(values) != self.dim:
            raise GoogleAIError(f"expected {self.dim} numbers, got {len(values)}")
        return values
