import httpx
import pytest
import respx

from vivatlas.ai.google import GoogleAIError, GoogleEmbeddingModel, GoogleTextModel

BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY = "test-secret-key-12345"


@respx.mock
async def test_key_goes_in_header_not_in_url():
    # There was a leak: the key was passed as ?key=... and httpx wrote the full URL
    # to the log — the key ended up in every log line.
    route = respx.post(f"{BASE}/models/m:embedContent").mock(
        return_value=httpx.Response(200, json={"embedding": {"values": [0.1, 0.2]}})
    )
    model = GoogleEmbeddingModel(KEY, "m", dim=2)
    await model.embed("hello")
    await model.aclose()

    request = route.calls.last.request
    assert KEY not in str(request.url), "key in the URL — it will leak into the logs"
    assert request.headers["x-goog-api-key"] == KEY


@respx.mock
async def test_empty_key_fails_loudly():
    with pytest.raises(GoogleAIError, match="GOOGLE_API_KEY"):
        GoogleEmbeddingModel("", "m", dim=2)


@respx.mock
async def test_wrong_dimension_is_an_error():
    # The model returned a different count of numbers than requested. Can't accept it silently:
    # such numbers aren't comparable to the rest in the database.
    respx.post(f"{BASE}/models/m:embedContent").mock(
        return_value=httpx.Response(200, json={"embedding": {"values": [0.1, 0.2, 0.3]}})
    )
    model = GoogleEmbeddingModel(KEY, "m", dim=2)
    with pytest.raises(GoogleAIError, match="expected 2"):
        await model.embed("x")
    await model.aclose()


@respx.mock
async def test_retries_on_overload_then_succeeds():
    route = respx.post(f"{BASE}/models/m:generateContent").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": '{"a":1}'}]}}]}
            ),
        ]
    )
    model = GoogleTextModel(KEY, "m")
    result = await model.generate_json("p", {"type": "object"})
    await model.aclose()

    assert result == {"a": 1}
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_retries_with_clear_message():
    respx.post(f"{BASE}/models/m:generateContent").mock(return_value=httpx.Response(429))
    model = GoogleTextModel(KEY, "m")
    with pytest.raises(GoogleAIError, match="quota"):
        await model.generate_json("p", {"type": "object"})
    await model.aclose()


@respx.mock
async def test_empty_answer_is_an_error_not_empty_dict():
    respx.post(f"{BASE}/models/m:generateContent").mock(
        return_value=httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]})
    )
    model = GoogleTextModel(KEY, "m")
    with pytest.raises(GoogleAIError, match="empty response"):
        await model.generate_json("p", {"type": "object"})
    await model.aclose()
