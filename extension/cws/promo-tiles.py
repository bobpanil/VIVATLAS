"""Draw the Chrome Web Store promo tiles into images/ (needs Pillow):
python extension/cws/promo-tiles.py"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

R = Path(__file__).resolve().parents[2]
out = Path(__file__).resolve().parent / "images"
BG = (253, 248, 241); INK = (35, 37, 29); MUTE = (108, 110, 99); ORANGE = (247, 165, 1)
logo = Image.open(f"{R}/extension/assets/logo-light.png").convert("RGB")
bbox = ImageChops.difference(logo, Image.new("RGB", logo.size, BG)).point(lambda v: 255 if v > 24 else 0).convert("L").getbbox()
logo = logo.crop(bbox)
font = lambda size, bold=True: ImageFont.truetype(f"{R}/src/vivatlas/static/fonts/IBMPlexSans-{'Bold' if bold else 'Regular'}.ttf", size)

def tile(w, h, logo_w, title, sub, ts, ss, name):
    im = Image.new("RGB", (w, h), BG); d = ImageDraw.Draw(im)
    lg = logo.resize((logo_w, round(logo.height * logo_w / logo.width)), Image.LANCZOS)
    tf, sf = font(ts), font(ss, False)
    th = d.textbbox((0, 0), title, font=tf)[3]; sh = d.textbbox((0, 0), sub, font=sf)[3]
    gap = round(h * 0.07)
    total = lg.height + gap + th + round(gap * 0.45) + sh
    y = (h - total) // 2
    # Paste only the drawing: a soft mask from how far each pixel is from the paper.
    diff = ImageChops.difference(lg, Image.new("RGB", lg.size, BG)).convert("L")
    mask = diff.point(lambda v: 0 if v < 10 else min(255, (v - 10) * 8))
    im.paste(lg, ((w - lg.width) // 2, y), mask); y += lg.height + gap
    d.text((w // 2, y), title, font=tf, fill=INK, anchor="mt"); y += th + round(gap * 0.45)
    d.text((w // 2, y), sub, font=sf, fill=MUTE, anchor="mt")
    d.rectangle((0, h - max(4, h // 70), w, h), fill=ORANGE)
    im.save(f"{out}/{name}.png"); print(name, im.size)

tile(440, 280, 340, "Clipper", "Any page into your catalogue, in one click", 30, 17, "promo-small-440x280")
tile(1400, 560, 900, "Clipper", "Any page into your own VIVATLAS catalogue, in one click", 72, 36, "promo-marquee-1400x560")
