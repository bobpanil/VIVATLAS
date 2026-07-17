"""Иконки для категорий-папок — набор для ручного выбора.

Простые контурные значки 16×16, рисуются цветом текста. Ключ хранится в
Category.icon; пусто — папка без значка. Держим набор в одном месте, чтобы и
выбор в настройках, и показ в сайдбаре брали из него.
"""

# ruff: noqa: E501 — это данные: пути SVG длинные, ломать их по строкам вредно.

# slug -> внутренность <svg viewBox="0 0 16 16"> (без внешнего тега)
ICONS: dict[str, str] = {
    "folder": '<path d="M2 4.5A1.5 1.5 0 0 1 3.5 3H6l1.5 1.5h5A1.5 1.5 0 0 1 14 6v5.5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5z"/>',
    "star": '<path d="M8 2l1.8 3.7 4 .6-2.9 2.8.7 4L8 11.9 4.4 13.1l.7-4L2.2 6.3l4-.6z"/>',
    "heart": '<path d="M8 13.5S2.5 10 2.5 6.2A2.7 2.7 0 0 1 8 5a2.7 2.7 0 0 1 5.5 1.2C13.5 10 8 13.5 8 13.5z"/>',
    "palette": '<path d="M8 2a6 6 0 1 0 0 12c1 0 1.5-.7 1.5-1.4 0-.9-.7-1.2-.7-2 0-.5.4-.9 1-.9h1.2A3.3 3.3 0 0 0 14 6.4C14 3.9 11.3 2 8 2z"/><circle cx="5" cy="7.5" r=".9" fill="currentColor" stroke="none"/><circle cx="7.6" cy="5" r=".9" fill="currentColor" stroke="none"/><circle cx="10.8" cy="6" r=".9" fill="currentColor" stroke="none"/>',
    "brush": '<path d="M11 2.5l2.5 2.5-5.5 5.5-2.5-2.5z"/><path d="M5.5 8L3 10.5c-.7.7-.7 2 0 2.7l.3.3c.8.8 2.4.6 3-.3L8 11"/>',
    "code": '<path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4"/>',
    "terminal": '<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M4.5 6.5L7 8.5 4.5 10.5M8.5 10.5H11"/>',
    "rocket": '<path d="M8 2c2.5 1 4 3.5 4 6l-2 2H6l-2-2c0-2.5 1.5-5 4-6z"/><circle cx="8" cy="6.5" r="1.1"/><path d="M6 12c-1 .5-1.5 1.5-1.5 2.5C5.5 14 6.5 13.5 7 12.5M10 12c1 .5 1.5 1.5 1.5 2.5C10.5 14 9.5 13.5 9 12.5"/>',
    "bolt": '<path d="M9 2L3.5 9H8l-1 5 5.5-7H8z"/>',
    "flask": '<path d="M6.5 2v4L3 12a1.3 1.3 0 0 0 1.2 2h7.6A1.3 1.3 0 0 0 13 12L9.5 6V2M6 2h4M5.2 10h5.6"/>',
    "search": '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
    "database": '<ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
    "chart": '<path d="M2.5 13.5h11M4.5 13V8M8 13V4M11.5 13V6.5"/>',
    "layers": '<path d="M8 2.5l5.5 3-5.5 3-5.5-3zM2.5 8.5L8 11.5l5.5-3M2.5 11L8 14l5.5-3"/>',
    "layout": '<rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M2.5 6.5h11M6.5 6.5v6.5"/>',
    "grid": '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
    "box": '<path d="M8 2l5.5 3v6L8 14l-5.5-3V5zM2.5 5L8 8l5.5-3M8 8v6"/>',
    "cloud": '<path d="M5 12h6.5A2.5 2.5 0 0 0 12 7.1 3.5 3.5 0 0 0 5.2 7 2.5 2.5 0 0 0 5 12z"/>',
    "globe": '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c2.2 2 2.2 9 0 11M8 2.5c-2.2 2-2.2 9 0 11"/>',
    "tag": '<path d="M2.5 7.5V3.5A1 1 0 0 1 3.5 2.5h4l6 6a1 1 0 0 1 0 1.4l-3.6 3.6a1 1 0 0 1-1.4 0z"/><circle cx="5.5" cy="5.5" r=".8" fill="currentColor" stroke="none"/>',
    "book": '<path d="M3 3.5A1.5 1.5 0 0 1 4.5 2H13v10H4.5A1.5 1.5 0 0 0 3 13.5zM3 13.5A1.5 1.5 0 0 1 4.5 12H13"/>',
    "camera": '<rect x="2" y="4.5" width="12" height="8" rx="1.5"/><path d="M5.5 4.5l1-1.5h3l1 1.5"/><circle cx="8" cy="8.5" r="2.2"/>',
    "film": '<rect x="2.5" y="3" width="11" height="10" rx="1.5"/><path d="M5.5 3v10M10.5 3v10M2.5 6h3M2.5 10h3M10.5 6h3M10.5 10h3"/>',
    "cog": '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v2M8 12.2v2M14.2 8h-2M3.8 8h-2M12.4 3.6l-1.4 1.4M5 11l-1.4 1.4M12.4 12.4L11 11M5 5L3.6 3.6"/>',
    "wrench": '<path d="M10.5 2.5a3 3 0 0 0-3.7 3.9l-4 4a1.3 1.3 0 0 0 1.8 1.8l4-4a3 3 0 0 0 3.9-3.7l-1.8 1.8-1.8-.4-.4-1.8z"/>',
    "bug": '<rect x="5" y="5.5" width="6" height="7" rx="3"/><path d="M6 4.5a2 2 0 0 1 4 0M2.5 7h2.5M11 7h2.5M2.5 12h2.5M11 12h2.5M3 9.5h2M11 9.5h2"/>',
    "shield": '<path d="M8 2l5 2v4c0 3-2.2 5.4-5 6.2C5.2 13.4 3 11 3 8V4z"/>',
    "sparkles": '<path d="M8 2.5l1.2 3.3L12.5 7l-3.3 1.2L8 11.5 6.8 8.2 3.5 7l3.3-1.2z"/><path d="M12 11l.5 1.4 1.5.6-1.5.6L12 15l-.5-1.4-1.5-.6 1.5-.6z"/>',
    "pin": '<path d="M8 2a4 4 0 0 0-4 4c0 3 4 8 4 8s4-5 4-8a4 4 0 0 0-4-4z"/><circle cx="8" cy="6" r="1.4"/>',
    "flag": '<path d="M4 14V2.5M4 3h7l-1.5 2.5L11 8H4"/>',
    "bell": '<path d="M8 2a3.5 3.5 0 0 0-3.5 3.5c0 4-1.5 5-1.5 5h10s-1.5-1-1.5-5A3.5 3.5 0 0 0 8 2zM6.5 13a1.5 1.5 0 0 0 3 0"/>',
}

# Порядок для сетки выбора.
ICON_SLUGS: list[str] = list(ICONS.keys())


# Подсказка иконки по смыслу названия: список (ключ-иконка, слова). Первое
# совпадение выигрывает. Слова и русские, и английские — язык запроса не важен.
_SUGGEST: list[tuple[str, tuple[str, ...]]] = [
    ("palette", ("дизайн", "design", "оформл", "ui", "ux", "фронт", "front", "вёрст", "верст", "стил", "бренд", "brand")),
    ("terminal", ("автоматиз", "automat", "cli", "скрипт", "script", "терминал", "terminal", "команд", "bash", "shell")),
    ("code", ("код", "code", "разработ", "программ", "рефактор", "refactor", "dev")),
    ("search", ("исследован", "research", "аналит", "analyt", "поиск", "search", "изуч")),
    ("database", ("данны", "data", "база", "database", "хранил", "sql", "датасет")),
    ("chart", ("график", "chart", "метрик", "metric", "статист", "dashboard", "отчёт", "отчет")),
    ("shield", ("безопасн", "security", "secur", "защит", "приват", "privacy")),
    ("bug", ("ошибк", "bug", "баг", "дебаг", "debug", "тест", "test", "провер", "qa")),
    ("cloud", ("облак", "cloud", "деплой", "deploy", "devops", "хостинг", "infra", "ci")),
    ("globe", ("сеть", "network", "web", "api", "интернет", "глобал", "http")),
    ("film", ("медиа", "media", "видео", "video", "ролик", "reel", "фильм")),
    ("camera", ("фото", "photo", "картин", "image", "снимок", "скрин")),
    ("book", ("документ", "doc", "книг", "book", "справоч", "guide", "заметк", "note", "wiki")),
    ("rocket", ("ракет", "rocket", "запуск", "launch", "старт", "mvp", "релиз", "release")),
    ("bolt", ("быстр", "perf", "speed", "performance", "скорост", "оптимиз")),
    ("sparkles", ("ai", "нейро", "gpt", "llm", "магия", "idea", "идея", "умн")),
    ("wrench", ("инструмент", "tool", "наладк", "утилит", "util", "конфиг", "config")),
    ("star", ("избранн", "favor", "любим", "star", "лучш", "топ")),
]


# Цвета категорий: у каждой папки свой устойчивый цвет, чтобы все её карточки
# (значок и дата в подвале, значок в сайдбаре) читались как одна группа. Берём
# по id, детерминированно — цвет не «прыгает» между заходами.
CATEGORY_COLORS: list[str] = [
    "#c2418f",  # малиновый
    "#2f7d8c",  # бирюзовый
    "#4a5bbf",  # индиго
    "#d98200",  # янтарный
    "#2e8b57",  # зелёный
    "#7a4fb5",  # фиолетовый
    "#2f6fb0",  # синий
    "#b5643a",  # терракота
    "#0f9d8f",  # изумруд
    "#a03c78",  # пурпур
]


def category_color(cat_id: int) -> str:
    """Устойчивый цвет папки по её id. Одна папка — один цвет для всех карточек."""
    return CATEGORY_COLORS[(int(cat_id) - 1) % len(CATEGORY_COLORS)]


def suggest_icon(name: str) -> str:
    """Иконка, подходящая по смыслу названия. Не угадали — папка."""
    low = name.lower()
    for slug, words in _SUGGEST:
        if any(w in low for w in words):
            return slug
    return "folder"


def icon_inner(slug: str) -> str:
    """Внутренность svg по ключу, или пусто."""
    return ICONS.get(slug, "")


def caticon_svg(slug: str):
    """Готовый <svg> иконки для шаблонов. Пусто — ничего. Регистрируется как
    глобал caticon в КАЖДОМ окружении шаблонов, где нужен (у модулей свои env)."""
    from markupsafe import Markup

    inner = ICONS.get(slug, "")
    if not inner:
        return ""
    return Markup(f'<svg class="cicon" viewBox="0 0 16 16" aria-hidden="true">{inner}</svg>')
