"""Shared data structures for Neko Studio."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Verification statuses, from most to least trustworthy.
RELIABLE = "fiable"
TO_CHECK = "à vérifier"
REJECTED = "écarté"


@dataclass
class MediaItem:
    source: str            # "pexels", "wikimedia"
    source_id: str
    kind: str              # "video" or "photo"
    download_url: str
    page_url: str
    thumb_url: str
    title: str             # human-readable description used for verification
    creator: str
    creator_url: str = ""
    license_name: str = ""
    license_url: str = ""
    attribution_required: bool = False
    width: int = 0
    height: int = 0
    duration: float = 0.0  # seconds, videos only
    status: str = TO_CHECK
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    quality: float = 0.5   # 0..1, from the source's own curation (featured, quality image, resolution)
    year: int = 0          # when it was shot, if known
    role: str = "place"    # "place", or "country" for the opening shots
    wish: str = ""         # the requested shot from the search note it answers, if any

    @property
    def key(self) -> str:
        return f"{self.source}-{self.source_id}"

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width > 0

    def to_dict(self) -> dict:
        return asdict(self)
