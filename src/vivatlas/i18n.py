"""Three UI languages: English (default), Russian, Hebrew.

The language is chosen on the SERVER — by a toggle button that sets a cookie,
not in localStorage like the theme. The reason is Hebrew: it runs right to left,
and `dir` on <html> must already be set in the served page, otherwise everything
jumps around on load.

Strings live in vivatlas.translations as key -> {en, ru, he}. English is the
source and the fallback: no translation — show English, none of that either —
the key itself (in development you immediately see what's missing).
"""

from fastapi import Request, Response

from vivatlas.translations import CATALOG

DEFAULT_LANG = "en"
COOKIE = "vivatlas_lang"
COOKIE_MAX_AGE = 365 * 24 * 3600

# code -> (native name for the toggle, writing direction)
LANGS: dict[str, tuple[str, str]] = {
    "en": ("English", "ltr"),
    "ru": ("Русский", "ltr"),
    "he": ("עברית", "rtl"),
}


def dir_for(lang: str) -> str:
    return LANGS.get(lang, LANGS[DEFAULT_LANG])[1]


def normalize(lang: str | None) -> str:
    """Reduce to one of the known languages. Unknown/empty — English."""
    code = (lang or "").strip().lower()[:2]
    return code if code in LANGS else DEFAULT_LANG


def lang_from_request(request: Request) -> str:
    return normalize(request.cookies.get(COOKIE))


def set_lang_cookie(response: Response, lang: str, secure: bool) -> None:
    # httponly=False: the language is no secret, let a script read it if it wants.
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
    """String in the requested language. No translation — English, none — the key itself."""
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return text.format(**kw) if kw else text


def msg(request: Request, key: str, **kw) -> str:
    """String in the request's language — for messages from code (errors, HTTPException)
    that have a request at hand but no template context."""
    return translate(key, getattr(request.state, "lang", DEFAULT_LANG), **kw)


def label(prefix: str, slug: str, lang: str) -> str:
    """Label from an enumeration (type/status/purpose etc.) in the requested language.
    The key is `prefix.slug`; if there's none — show the slug itself (as the old .get(s, s))."""
    key = f"{prefix}.{slug}"
    return translate(key, lang) if key in CATALOG else slug


from pathlib import Path as _Path

_STATIC_DIR = _Path(__file__).parent / "static"


def asset(name: str) -> str:
    """A static URL with a cache-busting ?v=<mtime>, so a stylesheet/script edit
    shows on the next load instead of a stale cache. Shared with every template
    (base, modal, auth, consent) via the context processor below — the Android
    WebView and desktop browsers both reuse app.css aggressively."""
    try:
        v = int((_STATIC_DIR / name).stat().st_mtime)
    except OSError:
        v = 0
    return f"/static/{name}?v={v}"


def template_context(request: Request) -> dict:
    """Passed into every template (via context_processors on Jinja2Templates):
    `t('key')`, the current `lang`, `dir`, the language list and language-dependent
    labels for types/statuses/purposes (previously — static dicts in web.py)."""
    lang = getattr(request.state, "lang", DEFAULT_LANG)
    return {
        "t": lambda key, **kw: translate(key, lang, **kw),
        "lang": lang,
        "dir": dir_for(lang),
        "langs": LANGS,
        "asset": asset,
        "type_name": lambda slug: label("type", slug, lang),
        "basis_name": lambda slug: label("basis", slug, lang),
        "status_name": lambda slug: label("status", slug, lang),
        "kind_name": lambda slug: label("kind", slug, lang),
        "purpose_name": lambda slug: label("purpose", slug, lang),
    }
