"""Your own videos and photos, added from the phone to a finished reel. They are
yours and shot on location, so they count as reliable and go first in the montage."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .config import ROOT
from .models import RELIABLE, MediaItem

UPLOAD_DIR = ROOT / "uploads"
VIDEO_TYPES = ("mp4", "mov", "m4v", "webm")
PHOTO_TYPES = ("jpg", "jpeg", "png", "webp", "heic")


def _probe(path: Path) -> tuple[int, int, float]:
    """Width and height as displayed (iPhone videos store rotation separately), and duration."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:stream_side_data=rotation:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True)
    try:
        data = json.loads(out.stdout)
        stream = data["streams"][0]
        w, h = stream.get("width", 0), stream.get("height", 0)
        rotation = next((abs(int(s.get("rotation", 0))) for s in stream.get("side_data_list", [])), 0)
        if rotation in (90, 270):
            w, h = h, w
        return w, h, float(data.get("format", {}).get("duration") or 0)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return 0, 0, 0.0


def save(name: str, data: bytes) -> MediaItem:
    UPLOAD_DIR.mkdir(exist_ok=True)
    ext = name.rsplit(".", 1)[-1].lower()
    digest = hashlib.md5(data).hexdigest()[:12]
    path = UPLOAD_DIR / f"{digest}.{ext}"
    if not path.exists():
        path.write_bytes(data)
    w, h, duration = _probe(path)
    kind = "video" if ext in VIDEO_TYPES else "photo"
    return MediaItem(
        source="perso", source_id=digest, kind=kind, download_url=str(path), page_url="", thumb_url="",
        title=name, creator="Vous", license_name="Vos propres médias", width=w, height=h, duration=duration,
        status=RELIABLE, score=3.0, reasons=["votre média, filmé sur place"], quality=0.9)
