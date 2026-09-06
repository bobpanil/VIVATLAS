"""Avatars: normalise any uploaded image to a square webp.

Raster (png/jpeg/gif/bmp) — via Pillow. SVG — rendered with headless Chromium
(Playwright): Windows has no native Cairo, but Chromium is already there and
handles any SVG. We strip EXIF — no dragging a geotag along from a phone photo.
"""

import base64
import io

SIZE = 256  # square side, px
MAX_UPLOAD = 8 * 1024 * 1024  # 8 MB of input (raw bytes) — an avatar never weighs that much
# Cap on DECODED pixels and side length: a small but heavily compressed file
# (solid colour 13000×13000) weighs hundreds of KB, yet expands in memory to
# hundreds of MB. We check the size from the header BEFORE full decoding.
MAX_PIXELS = 50_000_000  # ~50 MP: a phone photo passes, a "bomb" doesn't
MAX_SIDE = 12000


class AvatarError(Exception):
    """Couldn't accept the image. The text is a translation key for the caller."""


def _is_svg(data: bytes) -> bool:
    head = data[:1024].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def to_webp(data: bytes, content_type: str = "") -> bytes:
    """Upload bytes → 256×256 webp bytes. AvatarError if it's not an image."""
    if not data:
        raise AvatarError("avatar.err.empty")
    if len(data) > MAX_UPLOAD:
        raise AvatarError("avatar.err.too_big")
    if _is_svg(data) or "svg" in (content_type or "").lower():
        data = _svg_to_png(data)
    return _raster_to_webp(data)


def _raster_to_webp(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    # Safety net at the Pillow level: above 2× this it throws DecompressionBomb itself.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(data))
        # Take the size from the header (open is lazy) and bail BEFORE load(): that
        # way the "bomb" never gets to expand in memory.
        if im.size[0] * im.size[1] > MAX_PIXELS or max(im.size) > MAX_SIDE:
            raise AvatarError("avatar.err.too_big")
        im.load()
    except AvatarError:
        raise
    except Exception as e:  # noqa: BLE001 — any failure = "not an image"
        raise AvatarError("avatar.err.unreadable") from e
    im = ImageOps.exif_transpose(im)  # undo the phone's rotation
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    # Centred square, without distorting the proportions.
    im = ImageOps.fit(im, (SIZE, SIZE), method=Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=82, method=6)
    return out.getvalue()


def _svg_to_png(data: bytes, width: int = SIZE) -> bytes:
    """SVG → PNG. Cairo where there is one, headless Chromium where there isn't.

    Chromium came first because this was written on Windows, which has no native
    Cairo — but it is the wrong way round for a server: the container has no
    browser, so on it every SVG failed, silently, and a card whose repository
    offers `preview.svg` (which is the convention across this catalogue's own
    Gitea) simply never got a picture. Cairo is a few megabytes in the image and
    renders font weights faithfully, which these generated cards lean on.

    Cairo also has no objection to being called inside a running event loop,
    which sync_playwright does — so with it first, this is safe from async code
    as well as from the synchronous upload route.
    """
    try:
        import cairosvg

        return cairosvg.svg2png(bytestring=data, output_width=width)
    except ImportError:
        pass  # no Cairo here — try a browser
    except Exception as e:  # noqa: BLE001 — a malformed SVG is not "unsupported"
        raise AvatarError("avatar.err.svg_failed") from e

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
