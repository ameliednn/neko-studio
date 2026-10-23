"""Wikimedia Commons: free, no key needed, files usually named after the exact
place. Only CC0 / public-domain / CC BY / CC BY-SA files are kept; BY and BY-SA
require crediting the author (the credits file lists them)."""
from __future__ import annotations

import re
from html import unescape

from ..models import MediaItem
from .base import MediaSource

API = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSES = ("cc0", "public domain", "pd", "cc by")  # "cc by" also covers cc by-sa
MAX_VIDEO_BYTES = 150 * 1024 * 1024


def _text(value) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


class WikimediaSource(MediaSource):
    name = "wikimedia"
    label = "Wikimedia Commons"

    def _query(self, search: str) -> list[dict]:
        params = {
            "action": "query", "format": "json", "formatversion": 2,
            "generator": "search", "gsrsearch": search, "gsrnamespace": 6, "gsrlimit": 40,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 3840,
        }
        r = self.session.get(API, params=params, timeout=30)
        r.raise_for_status()
        return r.json().get("query", {}).get("pages", [])

    def search(self, query: str) -> list[MediaItem]:
        items: list[MediaItem] = []
        # Photos the Commons community voted "Quality" or "Featured" come first: sharp, well exposed, well framed.
        searches = [f'"{query}" incategory:Quality_images filetype:bitmap',
                    f'"{query}" filetype:bitmap', f'"{query}" filetype:video']
        for search in searches:
            for page in self._query(search):
                info = (page.get("imageinfo") or [{}])[0]
                meta = {k: _text(v.get("value", "")) for k, v in (info.get("extmetadata") or {}).items()}
                license_name = meta.get("LicenseShortName", "")
                if not license_name.lower().startswith(ALLOWED_LICENSES):
                    continue
                if re.search(r"\b(nc|nd)\b", license_name.lower()):
                    continue
                mime = info.get("mime", "")
                is_video = mime.startswith("video/")
                if not is_video and mime not in ("image/jpeg", "image/png", "image/webp"):
                    continue
                if is_video and (info.get("size") or 0) > MAX_VIDEO_BYTES:
                    continue
                title = page.get("title", "").removeprefix("File:")
                description = " ".join([title, meta.get("ObjectName", ""),
                                        meta.get("ImageDescription", "")[:400], meta.get("Categories", "")])
                items.append(MediaItem(
                    source=self.name, source_id=str(page.get("pageid")),
                    kind="video" if is_video else "photo",
                    download_url=info.get("url") if is_video else info.get("thumburl") or info.get("url"),
                    page_url=info.get("descriptionurl", ""),
                    thumb_url=info.get("thumburl", "") if not is_video else "",
                    title=description, creator=meta.get("Artist", "")[:120] or "Auteur inconnu",
                    license_name=license_name, license_url=meta.get("LicenseUrl", ""),
                    attribution_required=meta.get("AttributionRequired", "") == "true",
                    width=info.get("width") or 0, height=info.get("height") or 0,
                    duration=float(info.get("duration") or 0),
                    quality=_quality(meta.get("Categories", ""), info.get("width") or 0, info.get("height") or 0),
                    year=_year(meta.get("DateTimeOriginal", "") or meta.get("DateTime", ""))))
        return items


def _quality(categories: str, width: int, height: int) -> float:
    cats = categories.lower()
    if "featured pictures" in cats:
        curated = 1.0
    elif "quality images" in cats:
        curated = 0.8
    elif "valued images" in cats:
        curated = 0.6
    else:
        curated = 0.3
    megapixels = width * height / 1e6
    return min(1.0, curated + min(megapixels / 60, 0.2))


def _year(text: str) -> int:
    found = re.search(r"\b(19|20)\d{2}\b", text)
    return int(found.group()) if found else 0
