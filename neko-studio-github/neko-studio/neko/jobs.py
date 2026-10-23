"""Reels are made in a background thread, so the phone can lock its screen or
reload the page without stopping the montage. The page polls the job by id."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from .config import MIN_MEDIA
from .models import MediaItem
from .pipeline import best_reliable, find_country_media, find_media, generate

AUTO_PICK = 24  # media handed to the montage when nobody reviews them (spares replace dull photos)


@dataclass
class Job:
    id: str
    place: str
    seconds: int
    sources: list[str]
    seed: int = 0
    exclude: set[str] = field(default_factory=set)
    review: bool = False                  # always stop for a human check before the montage
    intro: bool = True                    # open on two wide views of the country
    look: str = "vif"                     # colour look: "vif" or "naturel"
    state: str = "search"                 # search -> (review) -> render -> done | error
    progress: float = 0.0
    message: str = "Recherche des images…"
    items: list[MediaItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reel: str = ""                        # output folder name once done
    error: str = ""


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def start(place: str, seconds: int, sources: list[str], seed: int = 0, exclude: set[str] | None = None,
          review: bool = False, items: list[MediaItem] | None = None, intro: bool = True,
          look: str = "vif") -> Job:
    """New job. With `items`, the search and checks are skipped and the montage starts at once."""
    job = Job(uuid.uuid4().hex[:10], place, seconds, sources, seed, set(exclude or ()), review, intro, look)
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, items), daemon=True).start()
    return job


def _run(job: Job, chosen: list[MediaItem] | None) -> None:
    try:
        if chosen is None:
            job.items, job.errors = find_media(job.place, job.sources)
            chosen = best_reliable(job.items, AUTO_PICK, job.exclude)
            if job.review or len(chosen) < MIN_MEDIA:
                # Not enough media we can vouch for: a human decides, nothing generic slips in.
                job.state, job.message = "review", "Vérification nécessaire"
                return
        intro: list[MediaItem] = []
        if job.intro:
            job.message = "Recherche de belles vues du pays…"
            intro = find_country_media(job.place, job.sources, {i.key for i in chosen} | job.exclude)
        job.state = "render"

        def progress(fraction: float, message: str) -> None:
            job.progress, job.message = fraction, message

        job.reel = generate(job.place, chosen, job.seconds, progress, seed=job.seed, intro=intro,
                            look=job.look).name
        job.state, job.progress, job.message = "done", 1.0, "Votre Reel est prêt"
    except Exception as exc:
        job.state, job.error = "error", str(exc)
