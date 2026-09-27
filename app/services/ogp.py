from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = (18, 20, 22)
FG = (240, 240, 236)
SUB = (170, 174, 178)
ACCENT = (0, 150, 136)

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
]


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, size: int, max_width: int) -> str:
    font = _font(size)
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def render(spectrogram: Path | None, title: str, place: str, dst: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    if spectrogram and spectrogram.exists():
        with Image.open(spectrogram) as spec:
            spec_rgb = spec.convert("RGB").resize((W, 330))
        img.paste(spec_rgb, (0, 150))
    draw.rectangle((0, 0, W, 10), fill=ACCENT)
    draw.text((56, 44), _fit(draw, title, 60, W - 112), font=_font(60), fill=FG)
    draw.text((56, 510), _fit(draw, place or "", 40, W - 112), font=_font(40), fill=FG)
    draw.text((56, 566), "きこえる地図", font=_font(28), fill=SUB)
    img.save(dst, "PNG", optimize=True)
