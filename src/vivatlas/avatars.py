"""Аватары: приводим любую загруженную картинку к квадратному webp.

Растровые (png/jpeg/gif/bmp) — через Pillow. SVG — рендерим headless-Chromium
(Playwright): на Windows нет нативного Cairo, а Chromium уже есть и берёт любой
SVG. EXIF снимаем — не тащим геометку со снимка телефона.
"""

import base64
import io

SIZE = 256  # квадрат стороны, px
MAX_UPLOAD = 8 * 1024 * 1024  # 8 МБ на вход (сырых байт) — аватар столько не весит
# Потолок по РАСКОДИРОВАННЫМ пикселям и стороне: маленький, но сильно сжатый
# файл (сплошной цвет 13000×13000) весит сотни КБ, а в память разворачивается
# в сотни МБ. Проверяем размер из заголовка ДО полной раскодировки.
MAX_PIXELS = 50_000_000  # ~50 Мп: телефонное фото проходит, «бомба» — нет
MAX_SIDE = 12000


class AvatarError(Exception):
    """Не смогли принять картинку. Текст — ключ для перевода у вызывающего."""


def _is_svg(data: bytes) -> bool:
    head = data[:1024].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def to_webp(data: bytes, content_type: str = "") -> bytes:
    """Байты загрузки → байты webp 256×256. AvatarError, если не картинка."""
    if not data:
        raise AvatarError("avatar.err.empty")
    if len(data) > MAX_UPLOAD:
        raise AvatarError("avatar.err.too_big")
    if _is_svg(data) or "svg" in (content_type or "").lower():
        data = _svg_to_png(data)
    return _raster_to_webp(data)


def _raster_to_webp(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    # Подстраховка на уровне Pillow: выше 2× этого он сам бросит DecompressionBomb.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(data))
        # Размер берём из заголовка (open ленив) и режем ДО load(): так «бомба»
        # не успевает развернуться в память.
        if im.size[0] * im.size[1] > MAX_PIXELS or max(im.size) > MAX_SIDE:
            raise AvatarError("avatar.err.too_big")
        im.load()
    except AvatarError:
        raise
    except Exception as e:  # noqa: BLE001 — любой сбой = «не картинка»
        raise AvatarError("avatar.err.unreadable") from e
    im = ImageOps.exif_transpose(im)  # снять поворот с телефона
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    # Квадрат по центру, без искажения пропорций.
    im = ImageOps.fit(im, (SIZE, SIZE), method=Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=82, method=6)
    return out.getvalue()


def _svg_to_png(data: bytes) -> bytes:
    """SVG → PNG через headless-Chromium. Ленивый импорт Playwright: растровые
    загрузки его не трогают. Зовётся только из СИНХРОННОГО маршрута (иначе
    sync_playwright падает внутри работающего цикла asyncio)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        raise AvatarError("avatar.err.svg_unsupported") from e
    b64 = base64.b64encode(data).decode()
    html = (
        '<!doctype html><body style="margin:0">'
        f'<img src="data:image/svg+xml;base64,{b64}" '
        f'style="width:{SIZE}px;height:{SIZE}px;object-fit:contain">'
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(
                viewport={"width": SIZE, "height": SIZE}, device_scale_factor=2
            ).new_page()
            page.set_content(html, wait_until="load")
            png = page.locator("img").screenshot()
            browser.close()
        return png
    except AvatarError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AvatarError("avatar.err.svg_failed") from e
