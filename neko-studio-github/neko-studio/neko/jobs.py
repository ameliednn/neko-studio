"""Reels are made in a background thread, so the phone can lock its screen or
reload the page without stopping the montage. The page polls the job by id."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from .config import MIN_MEDIA, default_quality
from .models import MediaItem
from .pipeline import best_reliable, find_country_media, find_media, generate, rank_openers

# Media handed to the montage when nobody reviews them. Each is used once, so a 60 s reel
# needs about 40 different ones; the rest replace dull or failed photos.
AUTO_PICK = 45


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
    quality: str = field(default_factory=default_quality)  # "hd" or "rapide"
    mine: list[MediaItem] = field(default_factory=list)    # your own uploads, used first
    note: str = ""                         # optional search note: shots the reel should contain
    pick_opener: bool = False             # stop to let you choose the first image among three
    choices: list = field(default_factory=list)  # (score, media, still image) offered as openers
    pending: tuple = ()                   # (media, country views) kept while you choose
    state: str = "search"                 # search -> (review) -> (opener) -> render -> done | error
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
          look: str = "vif", quality: str | None = None, mine: list[MediaItem] | None = None,
          note: str = "", pick_opener: bool = False) -> Job:
    """New job. With `items`, the search and checks are skipped and the montage starts at once.
    `mine` are your own videos and photos: they go first and found media fill the remaining time."""
    job = Job(uuid.uuid4().hex[:10], place, seconds, sources, seed, set(exclude or ()), review, intro, look,
              quality or default_quality(), list(mine or []), note, pick_opener)
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, items), daemon=True).start()
    return job


def _run(job: Job, chosen: list[MediaItem] | None) -> None:
    try:
        if chosen is None:
            job.items, job.errors = find_media(job.place, job.sources, job.note)
            found = best_reliable(job.items, AUTO_PICK, job.exclude)
            chosen = job.mine + found
            if job.review or len(chosen) < MIN_MEDIA:
                # Not enough media we can vouch for: a human decides, nothing generic slips in.
                job.state, job.message = "review", "Vérification nécessaire"
                return
        intro: list[MediaItem] = []
        if job.intro:
            job.message = "Recherche de belles vues du pays…"
            intro = find_country_media(job.place, job.sources, {i.key for i in chosen} | job.exclude)
        if job.pick_opener:
            job.message = "Préparation des propositions de première image…"
            job.choices = rank_openers(chosen, intro, job.place, light=job.quality == "rapide")[:3]
            if job.choices:
                job.pending = (chosen, intro)
                job.state, job.message = "opener", "Choisissez la première image"
                return
        _render(job, chosen, intro, None)
    except Exception as exc:
        job.state, job.error = "error", str(exc)


def choose(job: Job, key: str) -> None:
    """Your pick for the first image: the montage resumes with it."""
    chosen, intro = job.pending
    opener = next((m for _, m, _ in job.choices if m.key == key), None)
    job.state, job.message, job.progress = "render", "Montage avec votre première image…", 0.05
    threading.Thread(target=lambda: _safe(_render, job, chosen, intro, opener), daemon=True).start()


def _safe(fn, job: Job, *args) -> None:
    try:
        fn(job, *args)
    except Exception as exc:
        job.state, job.error = "error", str(exc)


def _render(job: Job, chosen: list[MediaItem], intro: list[MediaItem], opener: MediaItem | None) -> None:
    job.state = "render"

    def progress(fraction: float, message: str) -> None:
        job.progress, job.message = fraction, message

    job.reel = generate(job.place, chosen, job.seconds, progress, seed=job.seed, intro=intro,
                        look=job.look, quality_mode=job.quality,
                        notes=[e for e in job.errors if e.startswith("Aucun média")], opener=opener).name
    job.state, job.progress, job.message = "done", 1.0, "Votre Reel est prêt"
