from __future__ import annotations

import re

from ..config import secret
from ..models import MediaItem
from .base import MediaSource

API = "https://api.pexels.com"
LICENSE_URL = "https://www.pexels.com/license/"


def _slug_title(page_url: str) -> str:
    """Pexels pages look like /video/aerial-view-of-shirakawa-go-123/ ; the slug is the title."""
    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"-?\d+$", "", slug).replace("-", " ")


def _best_video_file(files: list[dict], portrait: bool) -> dict | None:
    mp4 = [f for f in files if f.get("link") and f.get("file_type") == "video/mp4" and f.get("width")]
    if not mp4:
        return None
    # Enough pixels for a 1080x1920 crop without downloading huge 4K files when avoidable.
    if portrait:
        return min(mp4, key=lambda f: abs(f["width"] - 1080))
    return min(mp4, key=lambda f: abs(f["height"] - 1440))


class PexelsSource(MediaSource):
    name = "pexels"
    label = "Pexels"

    def __init__(self) -> None:
        super().__init__()
        self.key = secret("PEXELS_API_KEY")
        if self.key:
            self.session.headers["Authorization"] = self.key

    def available(self) -> bool:
        return bool(self.key)

    def unavailable_reason(self) -> str:
        return "Pexels : clé API absente (voir le guide d'installation, étape 3)."

    def _get(self, endpoint: str, query: str, **params) -> dict:
        r = self.session.get(API + endpoint, params={"query": query, "per_page": 40, **params}, timeout=30)
        r.raise_for_status()
        return r.json()

    def search(self, query: str) -> list[MediaItem]:
        items: list[MediaItem] = []
        for v in self._get("/videos/search", query).get("videos", []):
            portrait = (v.get("height") or 0) > (v.get("width") or 0)
            f = _best_video_file(v.get("video_files", []), portrait)
            if not f:
                continue
            user = v.get("user") or {}
            items.append(MediaItem(
                source=self.name, source_id=str(v["id"]), kind="video",
                download_url=f["link"], page_url=v.get("url", ""), thumb_url=v.get("image", ""),
                title=_slug_title(v.get("url", "")), creator=user.get("name", ""),
                creator_url=user.get("url", ""), license_name="Licence Pexels",
                license_url=LICENSE_URL, width=v.get("width") or 0, height=v.get("height") or 0,
                duration=float(v.get("duration") or 0), quality=0.8))  # curated stock footage
        for p in self._get("/v1/search", query).get("photos", []):
            src = p.get("src") or {}
            items.append(MediaItem(
                source=self.name, source_id=str(p["id"]), kind="photo",
                download_url=src.get("original") or src.get("large2x", ""), page_url=p.get("url", ""),
                thumb_url=src.get("medium", ""),
                title=f"{p.get('alt') or ''} {_slug_title(p.get('url', ''))}",
                creator=p.get("photographer", ""), creator_url=p.get("photographer_url", ""),
                license_name="Licence Pexels", license_url=LICENSE_URL,
                width=p.get("width") or 0, height=p.get("height") or 0, quality=0.75))
        return items
