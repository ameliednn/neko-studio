"""FFmpeg montage: fast alternating shots, eased zooms and pans on photos, punchy
transitions and one colour look for the whole reel. 1080x1920 H.264 MP4 with no
text, no voice and no added faces."""
from __future__ import annotations

import hashlib
import os
import random
import re
import subprocess
import threading
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
OPENER_BEAT = 1.9                      # the first shot: long enough to take in, short enough to hook
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


def source_url(item: MediaItem, light: bool = False) -> str:
    """Where to fetch the media. In the fast 720p mode, Wikimedia photos come at 1920 px
    instead of 3840 px: plenty for that size, about four times less to download."""
    if light and item.source == "wikimedia" and "/3840px-" in item.download_url:
        return item.download_url.replace("/3840px-", "/1920px-")
    return item.download_url


def cache_path(item: MediaItem, light: bool = False) -> Path:
    if item.source == "perso":  # your own uploads are already on disk
        return Path(item.download_url)
    url = source_url(item, light)
    ext = Path(url.split("?")[0]).suffix.lower() or (".mp4" if item.kind == "video" else ".jpg")
    return CACHE_DIR / f"{item.key}-{hashlib.md5(url.encode()).hexdigest()[:8]}{ext}"


def is_cached(item: MediaItem, light: bool = False) -> bool:
    path = cache_path(item, light)
    return path.exists() and path.stat().st_size > 0


_WIKIMEDIA_SLOTS = threading.BoundedSemaphore(2)  # downloads run in parallel; Wikimedia gets two at most


class OutOfTime(TimeoutError):
    """The reel's download time is used up: build it from what has arrived."""


def download(item: MediaItem, light: bool = False, deadline: float | None = None) -> Path:
    """Fetch a media into the cache. `deadline` (a time.monotonic() value) is a hard limit that
    also covers the pauses servers impose, so no single file can hold the reel back."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(item, light)
    if is_cached(item, light):
        return path
    limit = time.monotonic() + DOWNLOAD_BUDGET
    deadline = min(deadline, limit) if deadline else limit
    if item.source == "wikimedia":
        with _WIKIMEDIA_SLOTS:
            result = _fetch(item, source_url(item, light), path, deadline)
            time.sleep(1)  # stay polite with Wikimedia's servers
            return result
    return _fetch(item, source_url(item, light), path, deadline)


def _fetch(item: MediaItem, url: str, path: Path, deadline: float) -> Path:
    tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.part")
    for attempt in range(4):
        if time.monotonic() >= deadline:
            raise OutOfTime("temps de téléchargement écoulé")
        timeout = max(5.0, min(60.0, deadline - time.monotonic()))
        with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}) as r:
            if r.status_code in (429, 503):
                # Free media hosts throttle bursts. A short pause is worth waiting for, if there is
                # time; a long ban is reported at once so the montage goes on without this host.
                wait = r.headers.get("Retry-After", "")
                seconds = int(wait) if wait.isdigit() else 3 * (attempt + 1)
                if seconds > 30 or attempt == 3:
                    raise RateLimited(item.source, max(seconds, 60))
                if time.monotonic() + seconds >= deadline:
                    raise OutOfTime("temps de téléchargement écoulé")
                time.sleep(seconds)
                continue
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    f.write(chunk)
                    if time.monotonic() > deadline:
                        # A slow server would stall the whole reel: give up, the next media replaces it.
                        raise OutOfTime("téléchargement trop lent")
        break
    tmp.replace(path)
    return path


# Words that promise a breathtaking or mysterious first image.
HERO_WORDS = ("view", "panorama", "aerial", "drone", "overview", "from above", "skyline", "landscape",
              "lake", "village", "vue", "sunset", "sunrise", "golden hour", "mist", "misty", "fog", "foggy",
              "clouds", "sea of clouds", "night", "stars", "milky way", "illuminat", "lantern", "reflection",
              "waterfall", "cliff", "canyon", "snow", "aurora", "lighthouse", "bay", "coast", "autumn")
HERO_MINOR = ("sign", "plaque", "detail", "interior", "museum", "menu", "ticket", "door", "pension", "hotel",
              "statue", "grave", "portrait", "food", "dish")


def hero_score(item: MediaItem) -> float:
    """How promising a media is as the opening shot, from its description. About 0..6."""
    # Only the media's own name: long category lists would mention "lake" or "village" for any photo.
    title = item.title.lower().split(".")[0]
    value = (item.kind == "video") * 1.5 + min(3, sum(w in title for w in HERO_WORDS)) + item.score
    return value - 2 * any(w in title for w in HERO_MINOR)


_hero_score = hero_score


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


def plan(items: list[MediaItem], total: int, seed: int = 0, intro: list[MediaItem] | None = None,
         opener: MediaItem | None = None) -> list[Shot]:
    """The opening shot (the most striking media, picked by the pipeline from its pixels),
    then the optional country shots, then the shots asked for in the search note, then
    videos with photos spaced between them. Each media appears once."""
    rng = random.Random(seed)
    intro = [i for i in (intro or []) if not opener or i.key != opener.key][:2]
    if opener:
        items = [i for i in items if i.key != opener.key]
    videos = _diverse(sorted((i for i in items if i.kind == "video"), key=lambda i: -i.score))
    photos = _diverse(sorted((i for i in items if i.kind == "photo"), key=lambda i: -i.score))
    if seed:  # a "new variant": same quality bar, other media order and picks
        rng.shuffle(videos)
        rng.shuffle(photos)
    # Every photo and stock video appears once, never twice. Only your own videos give several
    # moments (one per 2.5 s of footage, up to 4), each cut from a different part of the clip.
    mine = [v for v in videos if v.source == "perso" for _ in range(max(1, min(4, int(v.duration / 2.5))))]
    videos = mine + [v for v in videos if v.source != "perso"]
    vids, phos = videos, photos
    # Interleave so photos are spread evenly among videos.
    slots = [((i + 0.5) / len(vids), v) for i, v in enumerate(vids)]
    slots += [((j + 1) / (len(phos) + 1), p) for j, p in enumerate(phos)]
    order = [item for _, item in sorted(slots, key=lambda s: s[0])]
    # Without a chosen opener, open on the most promising description.
    if order and not opener:
        hero = max(order, key=hero_score)
        order.remove(hero)
        order.insert(0, hero)
    # Shots asked for in the search note come early, so they are sure to be in the reel,
    # taking each requested shot in turn rather than all the bay views in a row.
    by_wish: dict[str, list[MediaItem]] = {}
    for i in order:
        if i.wish:
            by_wish.setdefault(i.wish, []).append(i)
    asked: list[MediaItem] = []
    while any(by_wish.values()):
        for group in by_wish.values():
            if group:
                asked.append(group.pop(0))
    order = asked + [i for i in order if not i.wish]
    # Two moments of the same clip of yours are never back to back.
    for k in range(1, len(order)):
        if order[k].key == order[k - 1].key:
            swap = next((j for j in range(k + 1, len(order)) if order[j].key != order[k].key), None)
            if swap is not None:
                order[k], order[swap] = order[swap], order[k]
    # Shots in order until the reel is full; nothing is reused. The opener holds a little
    # longer: it has one job, making people stay for the rest.
    seq: list[tuple[MediaItem, float]] = [(opener, OPENER_BEAT)] if opener else []
    seq += [(i, INTRO_BEAT) for i in intro]
    for k, item in enumerate(order):
        if sum(b for _, b in seq) >= total:
            break
        beats = VIDEO_BEATS if item.kind == "video" else PHOTO_BEATS
        seq.append((item, beats[(k + rng.randrange(len(beats))) % len(beats)]))
    if not seq:
        return []
    filled = sum(b for _, b in seq)
    if filled < total:
        # Not enough different media: hold each shot a little longer (up to +50 %) rather than
        # repeat one. If that is still short, the reel is shorter than asked.
        stretch = min(1.5, total / filled)
        seq = [(item, round(beat * stretch, 3)) for item, beat in seq]
    # Trim the last shot so the reel lasts exactly the chosen time (shots are joined as is).
    excess = max(0.0, sum(b for _, b in seq) - total)
    last_item, last_beat = seq[-1]
    if last_beat - excess >= 0.6 or len(seq) == 1:
        seq[-1] = (last_item, round(last_beat - excess, 3))
    else:  # too short to read: drop it and lengthen the shot before
        seq.pop()
        prev_item, prev_beat = seq[-1]
        seq[-1] = (prev_item, round(prev_beat + last_beat - excess, 3))
    used: dict[str, int] = {}
    shots = []
    for item, beat in seq:
        shots.append(Shot(item, beat, used.get(item.key, 0)))
        used[item.key] = used.get(item.key, 0) + 1
    return shots


def _encode(args: list[str], vf: str, frames: int, look: str, out: Path) -> None:
    # Each shot gets its final colour look and is encoded once, all with identical stream
    # settings (limited-range yuv420p, 30 fps, same encoder), so the reel is just these
    # files joined end to end with no second encode: about twice as fast on a small server.
    # Photos and videos arrive with different colour tags; all shots are converted to and
    # tagged as standard HD video (BT.709, limited range), or the joined reel would change
    # stream parameters at each cut, which some players and Instagram's processing mishandle.
    ffmpeg([*args, "-an", "-vf",
            f"{vf},{LOOKS.get(look, LOOKS['vif'])},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            f"setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-r", str(FPS), "-video_track_timescale", "15360", str(out)])


def _render_video_shot(src: Path, shot: Shot, frames: int, length: float, size: tuple[int, int],
                       look: str, out: Path) -> None:
    w, h = size
    needed = frames / FPS * VIDEO_SPEED  # seconds of source footage this shot consumes
    room = length - needed
    args: list[str] = []
    if room > 0.5:
        # Skip the first and last moments (often shaky), vary the segment on reuse.
        frac = [0.35, 0.7, 0.15, 0.55][shot.variant % 4]
        args += ["-ss", f"{room * frac:.2f}"]
    else:
        args += ["-stream_loop", "-1"]
    vf = (f"setpts=PTS/{VIDEO_SPEED},scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},fps={FPS},{NORMALIZE.format(s=30)},setsar=1")
    _encode([*args, "-i", str(src)], vf, frames, look, out)


def _render_photo_shot(src: Path, shot: Shot, frames: int, index: int, size: tuple[int, int],
                       look: str, out: Path) -> None:
    w, h = size
    ease = f"((1-cos(PI*on/{frames}))/2)"  # starts and lands softly, like a camera on a slider
    # Consecutive photos never get the same move; a reused photo gets another one.
    z0, z1, x0, x1, y = PHOTO_MOVES[(index * 3 + shot.variant * 2) % len(PHOTO_MOVES)]
    z = f"{z0}+({z1 - z0})*{ease}"
    x = f"(iw-iw/zoom)*({x0}+({x1 - x0})*{ease})"
    # Move inside a 1.5x canvas: smooth enough for these slow moves, 44 % fewer pixels than 2x.
    w2, h2 = round(w * 1.5 / 2) * 2, round(h * 1.5 / 2) * 2
    vf = (f"scale={w2}:{h2}:force_original_aspect_ratio=increase:flags=lanczos,crop={w2}:{h2},"
          f"{NORMALIZE.format(s=0)},zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)*{y}'"
          f":d={frames}:s={w}x{h}:fps={FPS},setsar=1")
    _encode(["-i", str(src)], vf, frames, look, out)


def render(shots: list[Shot], total: int, out: Path, workdir: Path, progress: Progress | None = None,
           seed: int = 0, look: str = "vif", size: tuple[int, int] = (WIDTH, HEIGHT), light: bool = False) -> Path:
    progress = progress or (lambda f, m: None)
    workdir.mkdir(parents=True, exist_ok=True)
    # Frames per shot from cumulative times, so rounding never adds or loses a frame overall.
    ends = [round(sum(s.seconds for s in shots[:k + 1]) * FPS) for k in range(len(shots))]
    frames = [e - (ends[k - 1] if k else 0) for k, e in enumerate(ends)]
    done = 0

    def one(k: int) -> None:
        nonlocal done
        shot = shots[k]
        src = download(shot.item, light)
        seg = workdir / f"shot_{k:02d}.mp4"
        if shot.item.kind == "video":
            _render_video_shot(src, shot, frames[k], shot.item.duration or probe_duration(src), size, look, seg)
        else:
            _render_photo_shot(src, shot, frames[k], k, size, look, seg)
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

    progress(0.95, "Assemblage")
    # Straight cuts, as in the reference reels: the finished shots are copied one after the
    # other into the reel, with no decoding, so this takes seconds even for 60 s.
    playlist = workdir / "shots.txt"
    playlist.write_text("".join(f"file 'shot_{k:02d}.mp4'\n" for k in range(len(shots))), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(playlist), "-c", "copy", "-an", "-movflags", "+faststart", str(out)])
    progress(1.0, "Terminé")
    return out


def poster(video: Path, out: Path) -> Path:
    """Small still used by the "Mes Reels" gallery."""
    ffmpeg(["-ss", "1.5", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)])
    return out
