"""Google AI Studio (Gemini)."""

import asyncio
import json
import logging

import httpx

log = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Сколько раз повторить при 429/503. Бесплатный уровень отвечает так регулярно.
_MAX_RETRIES = 4


class GoogleAIError(RuntimeError):
    pass


class _GoogleClient:
    def __init__(self, api_key: str, timeout: float) -> None:
        if not api_key:
            raise GoogleAIError("Не задан GOOGLE_API_KEY — впишите ключ в .env")
        self._api_key = api_key
        # Ключ идёт заголовком, а не параметром в адресе (?key=...). Google
        # принимает оба способа, но httpx пишет адреса в лог целиком — и ключ
        # утекал бы в каждую строчку лога. Заголовки в лог не попадают.
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
                        f"{model}: не отвечает после {_MAX_RETRIES} попыток "
                        f"(HTTP {response.status_code}). Возможно, кончилась дневная квота."
                    )
                log.warning("%s: HTTP %s, жду %.0fс", model, response.status_code, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 400:
                raise GoogleAIError(f"{model}: HTTP {response.status_code} {response.text[:200]}")
            return response.json()
        raise GoogleAIError("недостижимо")

    async def aclose(self) -> None:
        await self._client.aclose()


class GoogleTextModel(_GoogleClient):
    def __init__(self, api_key: str, model: str, timeout: float = 120.0) -> None:
        super().__init__(api_key, timeout)
        self.model = model

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        """Ответ приходит заполненной анкетой, а не свободным текстом."""
        data = await self._post(
            self.model,
            "generateContent",
            {
                "contents": [{"parts": [{"text": prompt}]}],
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
            raise GoogleAIError(f"пустой ответ, finishReason={candidate.get('finishReason')}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GoogleAIError(f"ответ не разобрался как JSON: {text[:200]}") from exc


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
            raise GoogleAIError(f"ожидали {self.dim} чисел, пришло {len(values)}")
        return values
