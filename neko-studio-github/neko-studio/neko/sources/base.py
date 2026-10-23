from __future__ import annotations

import requests

from ..config import USER_AGENT
from ..models import MediaItem


class MediaSource:
    name = ""
    label = ""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return f"{self.label} indisponible"

    def search(self, query: str) -> list[MediaItem]:
        raise NotImplementedError
