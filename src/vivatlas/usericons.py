"""Набор аватаров по умолчанию — классические бюсты с орбитой (бренд VivAtlas).

Готовые webp лежат в static/usericons/avatar-NN.webp. Человеку при создании
достаётся случайный; в настройках можно выбрать другой или загрузить своё фото
(оно берёт верх над набором — см. /avatar в settings_web).

Список ключей выводим из папки, а не хардкодим: добавили файл — появился в
выборе, ничего больше править не нужно.
"""

import pathlib
import random

_DIR = pathlib.Path(__file__).parent / "static" / "usericons"

# Ключи вида "avatar-01" (без расширения), по порядку. Пустой список — если
# папку не выложили (например, в урезанной сборке); тогда показ падает на
# инициалы, а выбор в настройках просто пуст.
PRESETS: list[str] = sorted(p.stem for p in _DIR.glob("avatar-*.webp"))


def is_valid(key: str) -> bool:
    """Ключ — из набора? Защита от произвольного значения из формы."""
    return key in PRESETS


def random_preset() -> str:
    """Случайный ключ набора (или '' если набора нет)."""
    return random.choice(PRESETS) if PRESETS else ""


def path(key: str) -> pathlib.Path | None:
    """Путь к webp набора по ключу, или None если ключ чужой."""
    return _DIR / f"{key}.webp" if is_valid(key) else None


def read_bytes(key: str) -> bytes | None:
    """Байты webp набора по ключу, или None если ключа/файла нет."""
    p = path(key)
    if p is not None and p.exists():
        return p.read_bytes()
    return None
