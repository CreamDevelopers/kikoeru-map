from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SIZES = {"icon-192.png": 192, "icon-512.png": 512, "icon-32.png": 32, "apple-touch-icon.png": 180}


def resize_square(src: Image.Image, size: int) -> Image.Image:
    img = src.convert("RGBA")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return canvas


def main(out: Path) -> None:
    with Image.open(out / "newicon.png") as src:
        for name, size in SIZES.items():
            resize_square(src, size).save(out / name, optimize=True)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "app/static/icons"))
