"""Builds the home-screen icons (static/icon-*.png) from the Neko app icon.

The source artwork is a rounded square with a soft shadow on a white page; this
keeps only the square, since iOS and Android round the corners themselves.
Run once:  python tools/make_icon.py assets/neko-icon-source.png"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
# Inner square of the source artwork, as fractions of its width/height.
CROP = (0.135, 0.135, 0.865, 0.865)


def build(source: Path) -> None:
    img = Image.open(source).convert("RGB")
    w, h = img.size
    square = img.crop((round(CROP[0] * w), round(CROP[1] * h), round(CROP[2] * w), round(CROP[3] * h)))
    # The artwork's own rounded corners show the page and shadow: fill them with its cream background.
    side = square.width
    mask = Image.new("L", square.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, side - 1, side - 1], radius=round(0.2 * side), fill=255)
    cream = square.getpixel((side // 2, round(0.08 * side)))
    square = Image.composite(square, Image.new("RGB", square.size, cream), mask)
    out = ROOT / "static"
    out.mkdir(exist_ok=True)
    for size in (180, 512):
        square.resize((size, size), Image.LANCZOS).save(out / f"icon-{size}.png", optimize=True)
    print("icons written to", out)


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "neko-icon-source.png")
