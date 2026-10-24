"""Your own videos and photos, added from the phone to a finished reel. They are
yours and shot on location, so they count as reliable and go first in the montage."""
from __future__ import annotations

import hashlib

from .config import ROOT
from .models import RELIABLE, MediaItem
from .render import probe

UPLOAD_DIR = ROOT / "uploads"
VIDEO_TYPES = ("mp4", "mov", "m4v", "webm")
PHOTO_TYPES = ("jpg", "jpeg", "png", "webp", "heic")


def save(name: str, data: bytes) -> MediaItem:
    UPLOAD_DIR.mkdir(exist_ok=True)
    ext = name.rsplit(".", 1)[-1].lower()
    digest = hashlib.md5(data).hexdigest()[:12]
    path = UPLOAD_DIR / f"{digest}.{ext}"
    if not path.exists():
        path.write_bytes(data)
    w, h, duration = probe(path)  # displayed size: iPhone videos store their rotation apart
    kind = "video" if ext in VIDEO_TYPES else "photo"
    return MediaItem(
        source="perso", source_id=digest, kind=kind, download_url=str(path), page_url="", thumb_url="",
        title=name, creator="Vous", license_name="Vos propres médias", width=w, height=h, duration=duration,
        status=RELIABLE, score=3.0, reasons=["votre média, filmé sur place"], quality=0.9)
