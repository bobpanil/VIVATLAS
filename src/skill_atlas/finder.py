"""Найти репозиторий по чему угодно: ссылке, странице, скриншоту, тексту.

Одна дверь для всего. Что дали — то и разбираем:

    ссылка на GitHub  -> берём как есть, гадать не надо
    ссылка на сайт    -> читаем страницу, ищем в ней ссылки на GitHub
    картинка          -> модель читает, что на ней написано
    видео             -> модель слушает звук
    просто текст      -> модель вытаскивает название

Дальше всегда одинаково: собираем кандидатов, показываем их со звёздами и
описанием, человек выбирает. Автоматически не тащим никогда: название на слух
или с картинки распознаётся неточно, а ошибка стоит дорого.

Модели прямо запрещено выдумывать адрес репозитория. Пустой ответ лучше
правдоподобного вранья: по выдуманному адресу мы бы притащили не то.
"""

import base64
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

import httpx

from skill_atlas.ai.base import TextModel

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120"
# Facebook и другие отдают внятную страницу только мобильному виду. Проверено
# на реальном рилсе: обычный запрос получает 400, мобильный — заголовок,
# описание и ссылку на видео.
_UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
)

MAX_MEDIA_BYTES = 20_000_000

_GITHUB_REPO = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:[/#?)\s\"']|$)", re.I)
_OG = re.compile(r'<meta[^>]+property="og:(\w+)"[^>]+content="([^"]*)"', re.I)
_OG_ALT = re.compile(r'<meta[^>]+content="([^"]*)"[^>]+property="og:(\w+)"', re.I)

# Чужие репозитории, которые попадаются в тексте у всех подряд — это не то,
# что ищут.
_NOISE = {
    "actions",
    "features",
    "topics",
    "about",
    "pricing",
    "shields.io",
    "badges",
    "marketplace",
    "sponsors",
    "readme",
    "explore",
}


@dataclass
class Candidate:
    repo: str  # владелец/имя
    url: str
    stars: int = 0
    description: str = ""
    why: str = ""  # почему предложен — человеку это важнее, чем нам
    exact: bool = False  # адрес был прямо назван, а не найден поиском


@dataclass
class FindResult:
    kind: str  # github | web | image | video | text
    source: str
    candidates: list[Candidate] = field(default_factory=list)
    heard: str = ""  # что распознали: текст со страницы, речь, надпись
    language: str = ""  # язык оригинала — рилсы бывают на иврите и на чём угодно
    gist: str = ""  # о чём это по-русски, если оригинал не русский
    tool_name: str = ""
    notes: list[str] = field(default_factory=list)


def classify(source: str) -> str:
    s = source.strip()
    if _GITHUB_REPO.search(s) and s.lower().startswith(("http", "github.com")):
        return "github"
    if s.lower().startswith(("http://", "https://")):
        return "web"
    # Сюда попадает и то, что человек просто набрал в поиске. Проверка пути
    # трогает диск, и на строке с двоеточием или нулевым байтом Windows
    # ругается вместо ответа "нет такого файла". Любая беда тут значит одно:
    # это не файл, а слова.
    try:
        if Path(s).is_file():
            mime = mimetypes.guess_type(s)[0] or ""
            if mime.startswith("image/"):
                return "image"
            if mime.startswith("video/") or mime.startswith("audio/"):
                return "video"
    except (OSError, ValueError):
        pass
    return "text"


def looks_like_link(text: str) -> bool:
    """Похоже ли на ссылку — так, чтобы поиск не искал её среди названий.

    Гадаем только по однозначному: ссылка есть ссылка. Строку "last30days"
    толковать не беремся — непонятно, ищут это у себя или хотят притащить.
    """
    return classify(text) in ("github", "web")


def extract_repos(text: str) -> list[str]:
    """Все упомянутые репозитории, без шума и без повторов.

    Повторы считаем без оглядки на регистр: на сайте VoltAgent ссылки написаны
    и как VoltAgent/voltagent, и как voltagent/voltagent — для GitHub это один
    и тот же репозиторий, и человеку незачем видеть его дважды. Показываем то
    написание, которое встретилось первым.
    """
    found: list[str] = []
    seen: set[str] = set()
    for owner, repo in _GITHUB_REPO.findall(text or ""):
        if owner.lower() in _NOISE or repo.lower() in _NOISE:
            continue
        full = f"{owner}/{repo}"
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        found.append(full)
    return found


# Голое "владелец/репозиторий" без github.com. Так адрес выглядит на
# скриншотах и в речи. Ловим осторожно и обязательно проверяем у GitHub, что
# такое вообще есть: правило само по себе поймает и "design/system" из обычной
# фразы.
_BARE_REPO = re.compile(r"\b([A-Za-z][\w.\-]{1,38})/([A-Za-z][\w.\-]{1,38})\b")

_BARE_STOP = {
    "and",
    "or",
    "the",
    "for",
    "with",
    "input",
    "output",
    "km",
    "ms",
    "n",
    "yes",
    "no",
    "on",
    "off",
    "true",
    "false",
    "http",
    "https",
    "www",
}


def extract_bare_repos(text: str, limit: int = 3) -> list[str]:
    """Похожее на адрес репозитория, но без github.com. Только кандидаты —
    существование проверяется отдельно."""
    out: list[str] = []
    seen: set[str] = set()
    for owner, repo in _BARE_REPO.findall(text or ""):
        if owner.lower() in _BARE_STOP or repo.lower() in _BARE_STOP:
            continue
        if owner.lower() in _NOISE or repo.lower() in _NOISE:
            continue
        if "." in owner and "/" not in owner:  # похоже на домен, а не на владельца
            continue
        full = f"{owner}/{repo}"
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        out.append(full)
    return out[:limit]


def parse_og(page: str) -> dict[str, str]:
    """Теги og: со страницы, уже расшифрованные.

    Расшифровка обязательна, и это не косметика. В теге ссылка на видео
    записана как ...&amp;oe=6A5D9DE9&amp;... — в разметке & пишется так всегда.
    Если оставить как есть, в ссылке ломается подпись, и Facebook отвечает 403
    на совершенно правильную ссылку. Проверено на живом рилсе: без расшифровки
    403, с ней — 1.47 МБ mp4.
    """
    tags = {k.lower(): unescape(v) for k, v in _OG.findall(page)}
    for v, k in _OG_ALT.findall(page):
        tags.setdefault(k.lower(), unescape(v))
    return tags


SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "string"},
        "language": {"type": "string"},
        "gist": {"type": "string"},
        "tool_name": {"type": "string"},
        "github_repo": {"type": "string"},
        "keywords": {"type": "string"},
        "stars_mentioned": {"type": "integer"},
    },
    "required": [
        "heard",
        "language",
        "gist",
        "tool_name",
        "github_repo",
        "keywords",
        "stars_mentioned",
    ],
}

_PROMPT = """Определи, о каком инструменте для разработчика тут речь.

{what}

Речь и надписи могут быть на ЛЮБОМ языке: английском, иврите, русском,
испанском, китайском — на каком угодно. Разбирай как есть, не удивляйся и не
отказывайся. Текст может идти справа налево — это нормально.

Верни:
- heard: что тут сказано или написано, дословно и кратко, НА ЯЗЫКЕ ОРИГИНАЛА
- language: язык оригинала по-русски, одним словом: английский, иврит, русский…
- gist: о чём это, ОДНОЙ СТРОКОЙ ПО-РУССКИ. Если оригинал и так русский —
  пустая строка.
- tool_name: название инструмента, если прозвучало или видно. Пиши его так,
  как оно пишется в Git — латиницей. Не переводи название и не записывай его
  буквами другого алфавита. Не поняли названия — пустая строка.
- github_repo: адрес вида владелец/репозиторий — ТОЛЬКО если он прямо назван
  или виден. Если не назван — ПУСТАЯ СТРОКА.
- keywords: 3-6 слов ПО-АНГЛИЙСКИ для поиска на GitHub, через пробел. Всегда
  по-английски, даже если оригинал на другом языке: на GitHub ищут так.
- stars_mentioned: если названо число звёзд — это число, иначе 0

Главное правило: НЕ ВЫДУМЫВАЙ адрес репозитория. Пустая строка лучше
правдоподобного вранья — по выдуманному адресу притащат не тот инструмент."""


class Finder:
    def __init__(self, github_token: str = "", timeout: float = 60.0) -> None:
        gh = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
        if github_token:
            gh["Authorization"] = f"Bearer {github_token}"
        self._gh = httpx.AsyncClient(base_url="https://api.github.com", headers=gh, timeout=timeout)
        self._web = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def find(self, source: str, model: TextModel | None = None) -> FindResult:
        kind = classify(source)
        result = FindResult(kind=kind, source=source)

        if kind == "github":
            repos = extract_repos(source)
            if repos:
                result.candidates = [await self._describe(repos[0], "адрес дали прямо", exact=True)]
            return result

        if kind == "web":
            await self._from_web(source, result, model)
        elif kind in ("image", "video"):
            await self._from_media(source, kind, result, model)
        else:
            await self._from_text(source, result, model)

        if not result.candidates and (result.tool_name or result.heard):
            await self._search(result)
        result.candidates.sort(key=lambda c: (not c.exact, -c.stars))
        return result

    # --- источники ---

    async def _from_web(self, url: str, result: FindResult, model: TextModel | None) -> None:
        html = ""
        for ua in (_UA_MOBILE, _UA):
            try:
                r = await self._web.get(url, headers={"User-Agent": ua})
                if r.status_code == 200 and len(r.text) > 500:
                    html = r.text
                    break
            except Exception as exc:
                result.notes.append(f"страница не открылась ({ua.split('(')[0].strip()}): {exc}")
        if not html:
            result.notes.append("страницу прочитать не удалось")
            return

        og = parse_og(html)
        result.heard = (og.get("title", "") + " " + og.get("description", "")).strip()

        repos = extract_repos(html)
        if repos:
            result.notes.append(f"на странице найдено ссылок на GitHub: {len(repos)}")
            for repo in repos[:3]:
                result.candidates.append(
                    await self._describe(repo, "ссылка есть на странице", True)
                )
            return

        result.notes.append("на странице нет ссылок на GitHub — придётся искать по смыслу")

        # Рилс: ссылок на странице нет, весь смысл — в звуке. Название на слух
        # распознаётся неточно, поэтому дальше всё равно поиск и выбор руками.
        video = og.get("video") or og.get("video:url") or og.get("video:secure_url")
        if video and model is not None:
            data = await self._download(video, result)
            if data:
                result.notes.append(f"слушаю ролик, {len(data) / 1024 / 1024:.1f} МБ")
                await self._ask(
                    model,
                    "Послушай звук этого ролика. Текст на экране тоже прочитай, если он есть.",
                    result,
                    media=("video/mp4", data),
                )
                return

        if result.heard and model is not None:
            await self._ask(model, f"Текст со страницы:\n\n{result.heard[:1500]}", result)

    async def _download(self, url: str, result: FindResult) -> bytes | None:
        """Скачать ролик. Великоватые не берём: модель их всё равно не примет."""
        try:
            async with self._web.stream("GET", url, headers={"User-Agent": _UA_MOBILE}) as response:
                if response.status_code != 200:
                    result.notes.append(f"ролик не отдался: HTTP {response.status_code}")
                    return None
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        result.notes.append(
                            f"ролик больше {MAX_MEDIA_BYTES // 1_000_000} МБ — не тяну"
                        )
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as exc:
            result.notes.append(f"ролик не скачался: {exc}")
            return None

    async def _from_media(
        self, path: str, kind: str, result: FindResult, model: TextModel | None
    ) -> None:
        if model is None:
            result.notes.append("без модели картинку и видео не разобрать")
            return
        data = Path(path).read_bytes()
        if len(data) > MAX_MEDIA_BYTES:
            result.notes.append(f"файл великоват: {len(data) / 1024 / 1024:.0f} МБ")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        what = (
            "Прочитай, что написано на этой картинке."
            if kind == "image"
            else "Послушай звук этого ролика."
        )
        await self._ask(model, what, result, media=(mime, data))

    async def _from_text(self, text: str, result: FindResult, model: TextModel | None) -> None:
        repos = extract_repos(text)
        if repos:
            for repo in repos[:3]:
                result.candidates.append(await self._describe(repo, "адрес есть в тексте", True))
            return
        if model is None:
            result.notes.append("без модели из текста ничего не вытащить")
            return
        await self._ask(model, f"Текст:\n\n{text[:2000]}", result)

    # --- модель ---

    async def _ask(
        self,
        model: TextModel,
        what: str,
        result: FindResult,
        media: tuple[str, bytes] | None = None,
    ) -> None:
        prompt = _PROMPT.format(what=what)
        try:
            if media is not None:
                data = await model.generate_json_with_media(
                    prompt, SCHEMA, media[0], base64.b64encode(media[1]).decode()
                )
            else:
                data = await model.generate_json(prompt, SCHEMA)
        except Exception as exc:
            result.notes.append(f"модель не справилась: {exc}")
            return

        result.heard = (data.get("heard") or result.heard).strip()
        result.language = (data.get("language") or "").strip()
        result.gist = (data.get("gist") or "").strip()
        result.tool_name = (data.get("tool_name") or "").strip()
        stars = int(data.get("stars_mentioned") or 0)
        if stars:
            result.notes.append(f"названо звёзд: около {stars:,}".replace(",", " "))

        # Смотрим и в поле адреса, и в распознанный текст. Модель осторожничает:
        # на карточке GitHub было написано "VoltAgent/awesome-design-md", она
        # это прочитала в heard, но в поле адреса положила пусто. Адрес был
        # прямо перед глазами — грех его терять.
        sure: list[tuple[str, str]] = []
        for repo in extract_repos(data.get("github_repo") or "") or extract_bare_repos(
            data.get("github_repo") or "", limit=1
        ):
            sure.append((repo, "адрес прозвучал"))
        # Полную ссылку в надписи берём как есть: это уже адрес, гадать не о чем.
        # Так подписывают репозиторий в рилсах — "Ссылка: https://github.com/...".
        for repo in extract_repos(result.heard):
            sure.append((repo, "ссылка видна целиком"))

        # Проверяем у GitHub всё, что назвала модель. Запрет "не выдумывай
        # адрес" она соблюдает не всегда: на живом рилсе выдала skills/last-30-day
        # — такого репозитория нет, а мы бы предложили его тащить. Просить
        # модель не врать можно, полагаться на это — нельзя.
        known = {c.repo.lower() for c in result.candidates}
        for repo, why in sure[:3]:
            if repo.lower() in known:
                continue
            if not await self._exists(repo):
                result.notes.append(f"модель назвала {repo}, но такого нет — не верим")
                continue
            result.candidates.append(await self._describe(repo, why, True))
            known.add(repo.lower())

        # Голое "владелец/репозиторий" без github.com — так адрес пишут на
        # картинках и произносят вслух. Спрашиваем у GitHub, есть ли такое:
        # правило само по себе поймает и "design/system" из обычной фразы.
        for repo in extract_bare_repos(result.heard):
            if repo.lower() in known:
                continue
            if not await self._exists(repo):
                continue
            result.candidates.append(await self._describe(repo, "адрес виден в надписи", True))
            known.add(repo.lower())

        if not result.candidates:
            result.notes.append("адрес репозитория не назван — будем искать по названию")

        result.notes.append(f"ключевые слова для поиска: {data.get('keywords', '')}")
        result._keywords = data.get("keywords", "")  # type: ignore[attr-defined]
        result._stars = stars  # type: ignore[attr-defined]

    # --- поиск ---

    async def _search(self, result: FindResult) -> None:
        """Ищем несколькими способами: название на слух распознаётся неточно.

        Проверено вживую: модель услышала "last30dayskill", по нему не находится
        ничего. Нашлось только по "last 30 days skill" — с пробелами.
        """
        queries: list[str] = []
        name = result.tool_name
        if name:
            queries.append(name)
            spaced = re.sub(r"[-_]", " ", name)
            if spaced != name:
                queries.append(spaced)
            split = re.sub(r"(\d+)", r" \1 ", spaced).strip()
            if split not in queries:
                queries.append(split)
        kw = getattr(result, "_keywords", "")
        if kw:
            queries.append(kw)

        seen = {c.repo.lower() for c in result.candidates}
        for q in queries:
            for repo, stars, desc in await self._github_search(q):
                if repo.lower() in seen:
                    continue
                seen.add(repo.lower())
                why = f"нашлось по запросу «{q}»"
                said_stars = getattr(result, "_stars", 0)
                if said_stars and stars:
                    ratio = stars / said_stars
                    if 0.5 <= ratio <= 3:
                        why += f"; звёзды сходятся ({stars:,})".replace(",", " ")
                result.candidates.append(
                    Candidate(
                        repo=repo,
                        url=f"https://github.com/{repo}",
                        stars=stars,
                        description=desc,
                        why=why,
                    )
                )
            if len(result.candidates) >= 5:
                break
        result.candidates.sort(key=lambda c: (not c.exact, -c.stars))
        result.candidates = result.candidates[:5]

    async def _github_search(self, query: str) -> list[tuple[str, int, str]]:
        try:
            r = await self._gh.get(
                "/search/repositories", params={"q": query, "sort": "stars", "per_page": 3}
            )
            if r.status_code != 200:
                return []
            return [
                (i["full_name"], i["stargazers_count"], (i.get("description") or "")[:90])
                for i in r.json().get("items", [])
            ]
        except Exception:
            return []

    async def _exists(self, repo: str) -> bool:
        try:
            r = await self._gh.get(f"/repos/{repo}")
            return r.status_code == 200
        except Exception:
            return False

    async def _describe(self, repo: str, why: str, exact: bool = False) -> Candidate:
        c = Candidate(repo=repo, url=f"https://github.com/{repo}", why=why, exact=exact)
        try:
            r = await self._gh.get(f"/repos/{repo}")
            if r.status_code == 200:
                d = r.json()
                c.stars = d.get("stargazers_count", 0)
                c.description = (d.get("description") or "")[:90]
            elif r.status_code == 404:
                c.why += " — но такого репозитория нет"
        except Exception:
            pass
        return c

    async def aclose(self) -> None:
        await self._gh.aclose()
        await self._web.aclose()
