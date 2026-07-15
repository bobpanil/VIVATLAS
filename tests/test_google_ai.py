import httpx
import pytest
import respx

from skill_atlas.ai.google import GoogleAIError, GoogleEmbeddingModel, GoogleTextModel

BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY = "test-secret-key-12345"


@respx.mock
async def test_key_goes_in_header_not_in_url():
    # Была утечка: ключ передавался как ?key=... и httpx писал полный адрес в
    # лог — ключ оказывался в каждой строчке лога.
    route = respx.post(f"{BASE}/models/m:embedContent").mock(
        return_value=httpx.Response(200, json={"embedding": {"values": [0.1, 0.2]}})
    )
    model = GoogleEmbeddingModel(KEY, "m", dim=2)
    await model.embed("привет")
    await model.aclose()

    request = route.calls.last.request
    assert KEY not in str(request.url), "ключ в адресе — утечёт в логи"
    assert request.headers["x-goog-api-key"] == KEY


@respx.mock
async def test_empty_key_fails_loudly():
    with pytest.raises(GoogleAIError, match="GOOGLE_API_KEY"):
        GoogleEmbeddingModel("", "m", dim=2)


@respx.mock
async def test_wrong_dimension_is_an_error():
    # Модель вернула не столько чисел, сколько просили. Молча принять нельзя:
    # такие числа несравнимы с остальными в базе.
    respx.post(f"{BASE}/models/m:embedContent").mock(
        return_value=httpx.Response(200, json={"embedding": {"values": [0.1, 0.2, 0.3]}})
    )
    model = GoogleEmbeddingModel(KEY, "m", dim=2)
    with pytest.raises(GoogleAIError, match="ожидали 2"):
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
    with pytest.raises(GoogleAIError, match="квота"):
        await model.generate_json("p", {"type": "object"})
    await model.aclose()


@respx.mock
async def test_empty_answer_is_an_error_not_empty_dict():
    respx.post(f"{BASE}/models/m:generateContent").mock(
        return_value=httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]})
    )
    model = GoogleTextModel(KEY, "m")
    with pytest.raises(GoogleAIError, match="пустой ответ"):
        await model.generate_json("p", {"type": "object"})
    await model.aclose()
