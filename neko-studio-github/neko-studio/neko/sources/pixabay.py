"""Pixabay: free stock videos and photos (key from pixabay.com/api/docs/)."""
from __future__ import annotations

import re

from ..config import secret
from ..models import MediaItem
from .base import MediaSource

API = "https://pixabay.com/api/"
LICENSE_URL = "https://pixabay.com/service/license-summary/"


def _slug_title(page_url: str) -> str:
    """Pixabay pages look like /videos/hallstatt-austria-lake-12345/ ; the slug describes the shot."""
    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"-?\d+$", "", slug).replace("-", " ")


class PixabaySource(MediaSource):
    name = "pixabay"
    label = "Pixabay"

    def __init__(self) -> None:
        super().__init__()
        self.key = secret("PIXABAY_API_KEY")

    def available(self) -> bool:
        return bool(self.key)

    def unavailable_reason(self) -> str:
        return "Pixabay : clé API absente (facultatif, voir le guide)."

    def _get(self, endpoint: str, query: str, **params) -> list[dict]:
        r = self.session.get(API + endpoint, params={"key": self.key, "q": query, "per_page": 50,
                                                     "safesearch": "true", **params}, timeout=30)
        r.raise_for_status()
        return r.json().get("hits", [])

    def search(self, query: str) -> list[MediaItem]:
        items: list[MediaItem] = []
        for v in self._get("videos/", query):
            files = v.get("videos") or {}
            # Full HD is plenty for a 1080x1920 reel; 4K files are 4-8 times heavier to download.
            sizes = [files.get(k) or {} for k in ("large", "medium", "small")]
            sizes = [s for s in sizes if s.get("url")]
            hd = [s for s in sizes if 1080 <= min(s.get("width") or 0, s.get("height") or 0) <= 1440]
            f = hd[0] if hd else (sizes[0] if sizes else {})
            if not f.get("url"):
                continue
            items.append(MediaItem(
                source=self.name, source_id=str(v["id"]), kind="video", download_url=f["url"],
                page_url=v.get("pageURL", ""), thumb_url=(files.get("tiny") or {}).get("thumbnail", ""),
                title=f"{_slug_title(v.get('pageURL', ''))} {v.get('tags', '')}", creator=v.get("user", ""),
                license_name="Licence Pixabay", license_url=LICENSE_URL,
                width=f.get("width") or 0, height=f.get("height") or 0,
                duration=float(v.get("duration") or 0), quality=0.75))
        for p in self._get("", query, image_type="photo"):
            # The free API serves photos at 1280 px on the long side, whatever the original size.
            full_w, full_h = p.get("imageWidth") or 0, p.get("imageHeight") or 0
            ratio = min(1.0, 1280 / max(full_w, full_h, 1))
            items.append(MediaItem(
                source=self.name, source_id=str(p["id"]), kind="photo",
                download_url=p.get("largeImageURL", ""), page_url=p.get("pageURL", ""),
                thumb_url=p.get("webformatURL", ""),
                title=f"{_slug_title(p.get('pageURL', ''))} {p.get('tags', '')}", creator=p.get("user", ""),
                license_name="Licence Pixabay", license_url=LICENSE_URL,
                width=round(full_w * ratio), height=round(full_h * ratio), quality=0.7))
        return items
