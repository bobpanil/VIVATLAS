"""Три языка интерфейса: английский (по умолчанию), русский, иврит.

Язык выбирается на СЕРВЕРЕ — кнопкой-переключателем, которая кладёт куку, а не в
localStorage как тема. Причина — иврит: он идёт справа налево, и `dir` на <html>
должен стоять уже в отданной странице, иначе при загрузке всё прыгает.

Строки лежат в vivatlas.translations как ключ -> {en, ru, he}. Английский —
источник и запасной вариант: нет перевода — показываем английский, нет и его —
сам ключ (в разработке сразу видно, что забыли).
"""

from fastapi import Request, Response

from vivatlas.translations import CATALOG

DEFAULT_LANG = "en"
COOKIE = "vivatlas_lang"
COOKIE_MAX_AGE = 365 * 24 * 3600

# код -> (родное название для переключателя, направление письма)
LANGS: dict[str, tuple[str, str]] = {
    "en": ("English", "ltr"),
    "ru": ("Русский", "ltr"),
    "he": ("עברית", "rtl"),
}


def dir_for(lang: str) -> str:
    return LANGS.get(lang, LANGS[DEFAULT_LANG])[1]


def normalize(lang: str | None) -> str:
    """Свести к одному из известных языков. Неизвестный/пустой — английский."""
    code = (lang or "").strip().lower()[:2]
    return code if code in LANGS else DEFAULT_LANG


def lang_from_request(request: Request) -> str:
    return normalize(request.cookies.get(COOKIE))


def set_lang_cookie(response: Response, lang: str, secure: bool) -> None:
    # httponly=False: язык не секрет, и пусть скрипт при желании его читает.
    response.set_cookie(
        COOKIE,
        normalize(lang),
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        samesite="lax",
        secure=secure,
        path="/",
    )


def translate(key: str, lang: str, **kw) -> str:
    """Строка на нужном языке. Нет перевода — английский, нет и его — сам ключ."""
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return text.format(**kw) if kw else text


def msg(request: Request, key: str, **kw) -> str:
    """Строка на языке запроса — для сообщений из кода (ошибки, HTTPException),
    у которых под рукой есть request, но нет контекста шаблона."""
    return translate(key, getattr(request.state, "lang", DEFAULT_LANG), **kw)


def label(prefix: str, slug: str, lang: str) -> str:
    """Ярлык из перечисления (тип/состояние/направление и т.п.) на нужном языке.
    Ключ — `prefix.slug`; нет такого — показываем сам slug (как раньше .get(s, s))."""
    key = f"{prefix}.{slug}"
    return translate(key, lang) if key in CATALOG else slug


def template_context(request: Request) -> dict:
    """Отдаётся в каждый шаблон (через context_processors у Jinja2Templates):
    `t('ключ')`, текущий `lang`, `dir`, список языков и языкозависимые ярлыки
    типов/состояний/направлений (раньше — статические словари в web.py)."""
    lang = getattr(request.state, "lang", DEFAULT_LANG)
    return {
        "t": lambda key, **kw: translate(key, lang, **kw),
        "lang": lang,
        "dir": dir_for(lang),
        "langs": LANGS,
        "type_name": lambda slug: label("type", slug, lang),
        "basis_name": lambda slug: label("basis", slug, lang),
        "status_name": lambda slug: label("status", slug, lang),
        "kind_name": lambda slug: label("kind", slug, lang),
        "purpose_name": lambda slug: label("purpose", slug, lang),
    }
