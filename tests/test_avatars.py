"""Аватары: конвертация в webp и защита от «картинок-бомб»."""
import io

import pytest
from PIL import Image

from vivatlas import avatars


def _png(w: int, h: int) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 60, 40)).save(b, format="PNG")
    return b.getvalue()


def test_png_to_webp_square():
    out = avatars.to_webp(_png(40, 30), "image/png")
    # сигнатура webp: RIFF....WEBP
    assert out[:4] == b"RIFF" and out[8:12] == b"WEBP"
    im = Image.open(io.BytesIO(out))
    assert im.size == (avatars.SIZE, avatars.SIZE)  # обрезано в квадрат


def test_empty_rejected():
    with pytest.raises(avatars.AvatarError):
        avatars.to_webp(b"", "image/png")


def test_not_an_image_rejected():
    with pytest.raises(avatars.AvatarError):
        avatars.to_webp(b"this is not an image", "image/png")


def test_oversized_dimension_rejected():
    # 13000×1 — крошечный файл, но сторона больше потолка: должно отсечься ДО
    # раскодировки, не разворачиваясь в память.
    data = _png(avatars.MAX_SIDE + 1000, 1)
    with pytest.raises(avatars.AvatarError):
        avatars.to_webp(data, "image/png")


def test_raw_size_cap():
    with pytest.raises(avatars.AvatarError):
        avatars.to_webp(b"x" * (avatars.MAX_UPLOAD + 1), "image/png")
