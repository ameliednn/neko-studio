"""Looks at a downloaded photo and rates it 0..1 on what makes a reel shot work:
sharpness, colourfulness and exposure. Blurry scans, grey skies shot in fog and
dark interiors score low and get replaced when better media exist."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

POOR = 0.3  # below this, swap the photo for another one when possible


def rate(path: Path) -> float:
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((640, 640))
            rgb = np.asarray(img, dtype=np.float32)
    except Exception:
        return 0.5  # unreadable here (e.g. a video): stay neutral, FFmpeg decides
    gray = rgb.mean(axis=2)
    # Sharpness: variance of a Laplacian; ~60 is soft, 400+ is crisp at this size.
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    sharp = np.clip((np.log1p(lap.var()) - np.log1p(60)) / (np.log1p(600) - np.log1p(60)), 0, 1)
    # Colourfulness (Hasler & Süsstrunk): ~15 is dull, 60+ is vivid.
    rg = rgb[..., 0] - rgb[..., 1]
    yb = 0.5 * (rgb[..., 0] + rgb[..., 1]) - rgb[..., 2]
    colour = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())
    vivid = np.clip((colour - 15) / 45, 0, 1)
    # Exposure: penalise very dark or very bright images and heavy clipping.
    mean = gray.mean() / 255
    clipped = ((gray < 5) | (gray > 250)).mean()
    exposure = np.clip(1 - abs(mean - 0.5) * 2.2, 0, 1) * (1 - min(clipped * 3, 1))
    return float(0.4 * sharp + 0.35 * vivid + 0.25 * exposure)
