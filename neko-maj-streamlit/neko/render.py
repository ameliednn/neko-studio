"""FFmpeg montage: fast alternating shots, eased zooms and pans on photos, punchy
transitions and one colour look for the whole reel. 1080x1920 H.264 MP4 with no
text, no voice and no added faces."""
from __future__ import annotations

import hashlib
import os
import random
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from .config import CACHE_DIR, FFMPEG, FPS, HEIGHT, USER_AGENT, WIDTH
from .models import MediaItem

# Modelled on the travel reels the team picked as references: straight cuts only, about a
# second per video shot. Photos hold a little longer, since a still cut every second is tiring.
VIDEO_BEATS = [1.1, 1.3, 1.0, 1.2]     # seconds per video shot, slightly uneven like a human edit
PHOTO_BEATS = [1.7, 1.9, 1.6, 2.0]     # seconds per photo shot
INTRO_BEAT = 1.4                       # country shots before the place
DOWNLOAD_BUDGET = 90  # seconds per file
VIDEO_SPEED = 1.1  # stock footage is often slow; a light speed-up reads as energy, not as fast-forward

# Gentle photo moves, eased in and out: (start zoom, end zoom, x from, x to, y), where x and y
# place the frame inside the photo (0 = left/top, 1 = right/bottom). About 7 % of travel.
PHOTO_MOVES = [
    (1.00, 1.07, 0.5, 0.5, 0.5),    # push in, centre
    (1.00, 1.08, 0.3, 0.3, 0.45),   # push in towards the left
    (1.07, 1.00, 0.5, 0.5, 0.5),    # pull out
    (1.00, 1.08, 0.7, 0.7, 0.45),   # push in towards the right
    (1.06, 1.06, 0.35, 0.65, 0.5),  # slow travel left to right
    (1.00, 1.07, 0.5, 0.5, 0.25),   # push in towards the sky
    (1.06, 1.06, 0.65, 0.35, 0.5),  # slow travel right to left
]

# One look for every shot, so media from different cameras feel like one reel.
LOOKS = {
    "vif": ("eq=contrast=1.08:saturation=1.3:gamma=0.97:brightness=0.015,vibrance=intensity=0.3,"
            "colorbalance=rs=-0.03:bs=0.04:rh=0.04:bh=-0.04,unsharp=5:5:0.45:5:5:0,vignette=angle=PI/7"),
    "naturel": "eq=contrast=1.03:saturation=1.06,unsharp=5:5:0.3:5:5:0",
}
# Per-shot level stretch: evens out dull, dark or washed-out sources before the look.
NORMALIZE = "normalize=blackpt=black:whitept=white:smoothing={s}:independence=0:strength=0.55"

Progress = Callable[[float, str], None]


@dataclass
class Shot:
    item: MediaItem
    seconds: float
    variant: int  # how many times this media was used before: picks another segment / motion


def ffmpeg(args: list[str]) -> None:
    cmd = [FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-800:] or "FFmpeg a échoué")


def probe(path: Path) -> tuple[int, int, float]:
    """Displayed width and height (phone videos store their rotation apart) and duration.
    Reads FFmpeg's own summary, so no separate ffprobe is needed on hosted servers."""
    info = subprocess.run([FFMPEG or "ffmpeg", "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True, errors="replace").stderr
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else 0.0
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", info)
    w, h = (int(size[1]), int(size[2])) if size else (0, 0)
    rotation = re.search(r"rotat\w*\s*(?:of\s*)?:?\s*(-?\d+)", info)  # "rotate : 90" or "rotation of -90.00"
    if rotation and abs(int(rotation[1])) % 180 == 90:
        w, h = h, w
    return w, h, seconds


def probe_duration(path: Path) -> float:
    return probe(path)[2]


class RateLimited(RuntimeError):
    """The media host asked us to stop for a long while; other files from it will fail too."""

    def __init__(self, source: str, seconds: int):
        super().__init__(f"{source} limite temporairement les téléchargements ({seconds // 60 or 1} min)")
        self.source, self.seconds = source, seconds


def cache_path(item: MediaItem) -> Path:
    if item.source == "perso":  # your own uploads are already on disk
        return Path(item.download_url)
    ext = Path(item.download_url.split("?")[0]).suffix.lower() or (".mp4" if item.kind == "video" else ".jpg")
    return CACHE_DIR / f"{item.key}-{hashlib.md5(item.download_url.encode()).hexdigest()[:8]}{ext}"


def is_cached(item: MediaItem) -> bool:
    path = cache_path(item)
    return path.exists() and path.stat().st_size > 0


def download(item: MediaItem) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(item)
    if is_cached(item):
        return path
    tmp = path.with_suffix(path.suffix + ".part")
    for attempt in range(4):
        with requests.get(item.download_url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as r:
            if r.status_code in (429, 503):
                # Free media hosts throttle bursts. A short pause is worth waiting for;
                # a long ban is reported at once so the montage goes on without this host.
                wait = r.headers.get("Retry-After", "")
                seconds = int(wait) if wait.isdigit() else 3 * (attempt + 1)
                if seconds > 30 or attempt == 3:
                    raise RateLimited(item.source, max(seconds, 60))
                time.sleep(seconds)
                continue
            r.raise_for_status()
            started = time.monotonic()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    f.write(chunk)
                    if time.monotonic() - started > DOWNLOAD_BUDGET:
                        # A server trickling bytes would stall the whole reel: give up, the next media replaces it.
                        raise TimeoutError(f"téléchargement trop lent (> {DOWNLOAD_BUDGET} s)")
        break
    tmp.replace(path)
    if item.source == "wikimedia":
        time.sleep(1)  # stay polite with Wikimedia's servers
    return path


HERO_WORDS = ("view", "panorama", "aerial", "drone", "overview", "from above", "skyline", "landscape",
              "lake", "village", "vue", "sunset", "sunrise")


def _hero_score(item: MediaItem) -> float:
    # Only the media's own name: long category lists would mention "lake" or "village" for any photo.
    title = item.title.lower().split(".")[0]
    return (item.kind == "video") * 2 + sum(w in title for w in HERO_WORDS) + item.score


def _diverse(items: list[MediaItem]) -> list[MediaItem]:
    """Round-robin across creators so one photo series does not fill the reel."""
    by_creator: dict[str, list[MediaItem]] = {}
    for i in items:
        by_creator.setdefault(i.creator, []).append(i)
    out: list[MediaItem] = []
    while any(by_creator.values()):
        for group in by_creator.values():
            if group:
                out.append(group.pop(0))
    return out


def plan(items: list[MediaItem], total: int, seed: int = 0, intro: list[MediaItem] | None = None) -> list[Shot]:
    """Optional country shots, then the place's most striking view, then videos with
    photos spaced between them. Media are reused, with another segment or motion,
    only when there are not enough."""
    rng = random.Random(seed)
    intro = (intro or [])[:2]
    videos = _diverse(sorted((i for i in items if i.kind == "video"), key=lambda i: -i.score))
    photos = _diverse(sorted((i for i in items if i.kind == "photo"), key=lambda i: -i.score))
    if seed:  # a "new variant": same quality bar, other media order and picks
        rng.shuffle(videos)
        rng.shuffle(photos)
    # Real footage carries the reel, as in the reference reels: use several moments of each
    # video, each later rendered from another segment. Your own videos give one moment per
    # 2.5 s of footage (up to 4), stock videos one per 6 s (up to 3).
    mine = [v for v in videos if v.source == "perso" for _ in range(max(1, min(4, int(v.duration / 2.5))))]
    stock = [v for v in videos if v.source != "perso"]
    stock = [v for rep in range(3) for v in stock if rep < max(1, int(v.duration / 6))]
    videos = mine + stock
    # Enough media for the reel at the average beat, videos first.
    n = max(4, round(total / 1.3))
    pool = (videos + photos)[:n]
    vids = [i for i in pool if i.kind == "video"]
    phos = [i for i in pool if i.kind == "photo"]
    # Interleave so photos are spread evenly among videos.
    slots = [((i + 0.5) / len(vids), v) for i, v in enumerate(vids)]
    slots += [((j + 1) / (len(phos) + 1), p) for j, p in enumerate(phos)]
    order = [item for _, item in sorted(slots, key=lambda s: s[0])]
    # Open on the widest, most striking view: the first second decides if people keep watching.
    if order:
        hero = max(order, key=_hero_score)
        order.remove(hero)
        order.insert(0, hero)
    if not order:
        order = list(intro)
    # Shots in order until the reel is full; media are reused only when they run out.
    target = total + 0.2  # a little slack so frame rounding never leaves the reel short; -t trims it
    seq: list[tuple[MediaItem, float]] = [(i, INTRO_BEAT) for i in intro]
    k = 0
    while sum(b for _, b in seq) < target:
        item = order[k % len(order)]
        if seq and item.key == seq[-1][0].key and len(order) > 1:
            item = order[(k + 1) % len(order)]  # never the same media twice in a row
        beats = VIDEO_BEATS if item.kind == "video" else PHOTO_BEATS
        seq.append((item, beats[(k + rng.randrange(len(beats))) % len(beats)]))
        k += 1
    used: dict[str, int] = {}
    shots = []
    for item, beat in seq:
        shots.append(Shot(item, beat, used.get(item.key, 0)))
        used[item.key] = used.get(item.key, 0) + 1
    return shots


def _encode(args: list[str], vf: str, frames: int, out: Path) -> None:
    # Every shot leaves with identical stream settings (limited-range yuv420p, 30 fps, same
    # encoder) so the final pass can join them end to end without decoding them all at once.
    ffmpeg([*args, "-an", "-vf", f"{vf},scale=out_range=tv,format=yuv420p", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-r", str(FPS), "-video_track_timescale", "15360", str(out)])


def _render_video_shot(src: Path, shot: Shot, length: float, out: Path) -> None:
    frames = round(shot.seconds * FPS)
    needed = shot.seconds * VIDEO_SPEED  # seconds of source footage this shot consumes
    room = length - needed
    args: list[str] = []
    if room > 0.5:
        # Skip the first and last moments (often shaky), vary the segment on reuse.
        frac = [0.35, 0.7, 0.15, 0.55][shot.variant % 4]
        args += ["-ss", f"{room * frac:.2f}"]
    else:
        args += ["-stream_loop", "-1"]
    vf = (f"setpts=PTS/{VIDEO_SPEED},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
          f"crop={WIDTH}:{HEIGHT},fps={FPS},{NORMALIZE.format(s=30)},setsar=1,format=yuv420p")
    _encode([*args, "-i", str(src)], vf, frames, out)


def _render_photo_shot(src: Path, shot: Shot, index: int, out: Path) -> None:
    frames = round(shot.seconds * FPS)
    ease = f"((1-cos(PI*on/{frames}))/2)"  # starts and lands softly, like a camera on a slider
    # Consecutive photos never get the same move; a reused photo gets another one.
    z0, z1, x0, x1, y = PHOTO_MOVES[(index * 3 + shot.variant * 2) % len(PHOTO_MOVES)]
    z = f"{z0}+({z1 - z0})*{ease}"
    x = f"(iw-iw/zoom)*({x0}+({x1 - x0})*{ease})"
    w2, h2 = WIDTH * 2, HEIGHT * 2  # move inside a 2x canvas so motion stays smooth
    vf = (f"scale={w2}:{h2}:force_original_aspect_ratio=increase:flags=lanczos,crop={w2}:{h2},"
          f"{NORMALIZE.format(s=0)},zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)*{y}'"
          f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,format=yuv420p")
    _encode(["-i", str(src)], vf, frames, out)


def render(shots: list[Shot], total: int, out: Path, workdir: Path, progress: Progress | None = None,
           seed: int = 0, look: str = "vif") -> Path:
    progress = progress or (lambda f, m: None)
    workdir.mkdir(parents=True, exist_ok=True)
    segments = [round(shot.seconds * FPS) / FPS for shot in shots]
    done = 0

    def one(k: int) -> None:
        nonlocal done
        shot = shots[k]
        src = download(shot.item)
        seg = workdir / f"shot_{k:02d}.mp4"
        if shot.item.kind == "video":
            _render_video_shot(src, shot, shot.item.duration or probe_duration(src), seg)
        else:
            _render_photo_shot(src, shot, k, seg)
        done += 1
        progress(0.25 + 0.6 * done / len(shots), f"Montage des plans {done}/{len(shots)}")

    # Shots are independent FFmpeg runs: several at once use all the processor's cores.
    # Shots sharing a photo run in the same worker so its prepared still is made only once.
    by_media: dict[str, list[int]] = {}
    for k, shot in enumerate(shots):
        by_media.setdefault(shot.item.key, []).append(k)
    # Cores this process may really use (a hosted server can report the whole machine's).
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 2)
    with ThreadPoolExecutor(max_workers=max(1, min(4, cores // 2))) as pool:
        for future in [pool.submit(lambda ks=ks: [one(k) for k in ks]) for ks in by_media.values()]:
            future.result()

    progress(0.87, "Assemblage et couleurs")
    # Straight cuts, as in the reference reels: the shots are read one after the other from a
    # list, so memory stays low even for a 60 s reel of 40 shots on a small hosted server.
    playlist = workdir / "shots.txt"
    playlist.write_text("".join(f"file 'shot_{k:02d}.mp4'\n" for k in range(len(segments))), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(playlist), "-vf", f"{LOOKS.get(look, LOOKS['vif'])},format=yuv420p",
            "-t", str(total), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", str(out)])
    progress(1.0, "Terminé")
    return out


def poster(video: Path, out: Path) -> Path:
    """Small still used by the "Mes Reels" gallery."""
    ffmpeg(["-ss", "1.5", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)])
    return out
