"""Media sources. To add an authorised source, subclass MediaSource in a new
module and register it in SOURCES below; nothing else needs to change."""
from __future__ import annotations

from ..models import MediaItem
from .base import MediaSource
from .pexels import PexelsSource
from .pixabay import PixabaySource
from .wikimedia import WikimediaSource

SOURCES: dict[str, type[MediaSource]] = {
    PexelsSource.name: PexelsSource,
    PixabaySource.name: PixabaySource,
    WikimediaSource.name: WikimediaSource,
}


def search_all(names: list[str], queries: list[str]) -> tuple[list[MediaItem], list[str]]:
    """Run every query on every enabled source. Returns (items, errors)."""
    items: dict[str, MediaItem] = {}
    errors: list[str] = []
    for name in names:
        source = SOURCES[name]()
        if not source.available():
            errors.append(source.unavailable_reason())
            continue
        for query in queries:
            try:
                for item in source.search(query):
                    items.setdefault(item.key, item)
            except Exception as exc:  # network or quota problems must not crash the app
                errors.append(f"{source.label} : recherche « {query} » impossible ({exc})")
    return list(items.values()), errors
