"""The steps the interface calls: search, generate, and reading back past reels."""
from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

from . import quality, verify, wishes
from .config import CACHE_DIR, MIN_MEDIA, OUTPUT_DIR, QUALITIES, USER_AGENT
from .layers import Layer, apply_layers
from .models import REJECTED, RELIABLE, TO_CHECK, MediaItem
from .render import OutOfTime, Progress, RateLimited, download, ffmpeg, hero_score, is_cached, plan, poster, render
from .sources import search_all

STATUS_ORDER = {RELIABLE: 0, TO_CHECK: 1, REJECTED: 2}
DOWNLOADS_BUDGET = 150  # seconds for all downloads of a reel; after that, it is made from what arrived


def find_media(place: str, sources: list[str], note: str = "") -> tuple[list[MediaItem], list[str]]:
    """Search, verify and rank. Shots asked for in the note get their own searches and go
    first; a requested shot with no authentic media is reported, never faked with a look-alike."""
    query, asked = verify.parse_query(place), wishes.parse(note)
    base = query.core or query.main
    extra = [verify.LooseQuery(f"{w.search} {query.country}" if query.kind == "theme" else f"{base} {w.search}")
             for w in asked]
    items, errors = search_all(sources, verify.search_queries(place) + extra)
    items = [verify.score(i, place) for i in items]
    verify.confirm_by_neighbours(items, place)
    reliable = [i for i in items if i.status == RELIABLE]
    found = {w.text: sum(w.matches(i.title) for i in reliable) for w in asked}
    for item in reliable:
        # A media fitting several requested shots answers the rarest one, so each gets covered.
        fits = [w for w in asked if w.matches(item.title)]
        if fits:
            wish = min(fits, key=lambda w: found[w.text])
            item.wish = wish.text
            item.score += 0.6
            item.reasons.insert(0, f"plan demandé : {wish.text}")
    items.sort(key=lambda i: (STATUS_ORDER[i.status], -i.score))
    for w in asked:
        if not found[w.text]:
            errors.append(f"Aucun média authentique trouvé pour « {w.text} » : ce plan manquera "
                          "plutôt que d'être remplacé par un endroit ressemblant.")
    return items, errors


def signature_words(items: list[MediaItem], place: str) -> set[str]:
    """What you see here and nowhere else: words that recur in the names of this place's media
    but are not generic ("funaya" in Ine, "gassho" in Shirakawa-go)."""
    query = verify.parse_query(place)
    known = set(verify.normalize(f"{query.core or query.main} {query.country} {' '.join(query.region)}").split())
    generic = verify.COMMON | known | set(verify.LINK_WORDS) | {w for w in verify.STRIKING if " " not in w} | {
        "japan", "japanese", "photo", "image", "view", "winter", "summer", "spring", "autumn", "night", "day",
        "morning", "evening", "with", "this", "that", "from", "into", "over", "under", "near", "nature",
        "heritage", "traditional", "water", "world", "site", "unesco", "culture", "cultural", "historic",
        "history", "old", "architecture", "church", "kirche", "kath", "catholic", "interior", "tourism",
        "travel", "landscape", "scenery", "beautiful", "panoramio", "commons", "wikimedia"}
    # Region names ("Kyoto Prefecture", "Yosa District") situate a place, they are not its signature.
    regions = {verify.normalize(m).strip() for item in items for m in
               re.findall(r"([A-Z][\w-]+)\s+(?:Prefecture|District|Province|County|Region|State|Department)",
                          item.title)}
    counts: dict[str, int] = {}
    for item in items:
        name = verify.normalize(re.split(r"\.(?:jpe?g|png|webp|webm|ogv|mp4)\b", item.title, flags=re.I)[0])
        for word in set(name.split()):
            if (len(word) >= 4 and not word.isdigit() and word not in generic and word not in regions
                    and "altar" not in word):
                counts[word] = counts.get(word, 0) + 1
    recurring = sorted((w for w, c in counts.items() if c >= 3), key=lambda w: -counts[w])
    return set(recurring[:3])


def rank_openers(items: list[MediaItem], intro: list[MediaItem], place: str,
                 progress: Progress | None = None, light: bool = False) -> list[tuple[float, MediaItem, Path]]:
    """The first shot decides whether people watch on: panoramic, misty or mysterious, and
    showing something you do not see elsewhere. Candidates come from the place itself (country
    views only if the place has none). Each is judged on its real pixels (mood: mist, night
    lights, golden light; and sharp, well-exposed colour), on its description (aerial,
    panorama, fog, sea of clouds...) and on whether it shows the place's signature. A shot
    asked for as "ouverture : ..." in the search note wins. Returns (score, media, still)."""
    pool = items or intro
    signature = signature_words(pool, place)

    def shows_signature(item: MediaItem) -> bool:
        return any(f" {w} " in verify.normalize(item.title.split(".")[0]) for w in signature)

    def minor(item: MediaItem) -> bool:  # a sign, an interior, an animal close-up: never an opener
        name = verify.normalize(item.title.split(".")[0])
        return any(f" {w} " in name for w in verify.MINOR)

    asked = [i for i in pool if i.wish and wishes.asks_opener(i.wish) and not minor(i)]
    candidates = asked or sorted((i for i in pool if not minor(i)),
                                 key=lambda i: hero_score(i) + 1.5 * shows_signature(i), reverse=True)[:8]
    if progress:
        progress(0.02, "Choix de la première image")

    def judge(item: MediaItem) -> tuple[float, MediaItem, Path] | None:
        try:
            still = _still(item, light)
        except Exception:
            return None
        value = (0.3 * quality.rate(still) + 0.25 * quality.atmosphere(still) + 0.25 * shows_signature(item)
                 + 0.2 * min(1.0, max(0.0, hero_score(item)) / 5))
        return value, item, still

    with ThreadPoolExecutor(max_workers=4) as workers:
        rated = [r for r in workers.map(judge, candidates) if r]
    rated.sort(key=lambda r: -r[0])
    return rated


def _still(item: MediaItem, light: bool) -> Path:
    """An image to judge a candidate opener by: the light preview the media bank provides
    (960 px for Wikimedia photos), so full files are only downloaded for media actually used."""
    preview = item.thumb_url
    if item.source == "wikimedia" and item.kind == "photo" and "/3840px-" in item.download_url:
        preview = item.download_url.replace("/3840px-", "/960px-")
    if preview and not is_cached(item, light):
        thumb = CACHE_DIR / f"{item.key}-preview.jpg"
        if not thumb.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            r = requests.get(preview, timeout=15, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            thumb.write_bytes(r.content)
        return thumb
    if item.kind == "photo":
        return download(item, light, deadline=time.monotonic() + 30)
    path = download(item, light, deadline=time.monotonic() + 30)
    still = path.with_name(f"{path.stem}-still.jpg")
    if not still.exists():  # judge the moment the shot will show
        ffmpeg(["-ss", f"{max(0.0, item.duration * 0.35):.2f}", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=640:-2", "-q:v", "3", str(still)])
    return still


def choose_opener(items: list[MediaItem], intro: list[MediaItem], place: str, seed: int = 0,
                  progress: Progress | None = None, light: bool = False) -> MediaItem | None:
    """The best opener; a new variant opens on the runner-up."""
    rated = rank_openers(items, intro, place, progress, light)
    return rated[min(seed % 2, len(rated) - 1)][1] if rated else None


def find_country_media(place: str, sources: list[str], avoid: set[str] = frozenset()) -> list[MediaItem]:
    """Two striking wide views of the country, to open the reel. Empty if no country was given."""
    query = verify.parse_query(place)
    country = query.country
    if not country or query.kind == "theme":  # a theme reel already shows the whole country
        return []
    items, _ = search_all(sources, [f"{country} landscape", country])
    items = [verify.score_country(i, country) for i in items if i.key not in avoid]
    ok = sorted((i for i in items if i.status == RELIABLE), key=lambda i: -i.score)
    picked: list[MediaItem] = []
    for item in ok:  # two different views, not twice the same monument
        subject = verify.normalize(item.title.split(".")[0]).split()[:4]
        if all(subject != verify.normalize(p.title.split(".")[0]).split()[:4] for p in picked):
            picked.append(item)
        if len(picked) == 2:
            break
    return picked


def best_reliable(items: list[MediaItem], limit: int, exclude: set[str] = frozenset()) -> list[MediaItem]:
    """The media picked automatically: reliable only, videos first, then the most striking."""
    ok = [i for i in items if i.status == RELIABLE and i.key not in exclude]
    # Shots asked for in the search note are always kept, whatever the limit.
    ok.sort(key=lambda i: (not i.wish, i.kind != "video", -i.score))
    return ok[:max(limit, sum(1 for i in ok if i.wish))]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", verify.normalize(text).strip()).strip("-") or "reel"


def generate(place: str, items: list[MediaItem], seconds: int, progress: Progress | None = None,
             layers: list[Layer] | None = None, seed: int = 0, intro: list[MediaItem] | None = None,
             look: str = "vif", quality_mode: str = "hd", notes: list[str] | None = None,
             opener: MediaItem | None = None) -> Path:
    """Builds the reel and its records; returns the reel's folder."""
    size = QUALITIES.get(quality_mode, QUALITIES["hd"])
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Short names: Windows refuses paths longer than 260 characters.
    folder = OUTPUT_DIR / f"{stamp}-{_slug(place)[:20]}"
    work = folder / "work"
    progress = progress or (lambda f, m: None)
    pool, intro, failed, dull, limited = list(items), list(intro or []), [], [], ""
    light = quality_mode == "rapide"  # lighter downloads are plenty for 720p
    opener = opener or choose_opener(pool, intro, place, seed, progress, light)  # yours if you picked it
    started = time.monotonic()
    while True:
        shots = plan(pool, seconds, seed, intro, opener)
        needed = list({s.item.key: s.item for s in shots}.values())
        spare = len(pool) + len(intro) - len(needed)  # media we could swap in
        broken: list[MediaItem] = []
        done = 0

        def fetch(item: MediaItem):
            nonlocal done
            if item is not opener and not is_cached(item, light) and time.monotonic() - started > DOWNLOADS_BUDGET:
                return item, None, "late"  # out of time: build the reel from what has arrived
            try:
                # Everything must have arrived by the deadline; the opener gets one more minute.
                path = download(item, light, deadline=started + DOWNLOADS_BUDGET + 60 * (item is opener))
            except OutOfTime:
                return item, None, "late"
            except RateLimited as exc:
                return item, None, exc
            except Exception as exc:
                return item, None, f"{item.page_url} ({exc})"
            finally:
                done += 1
                progress(0.05 + 0.2 * done / len(needed), f"Téléchargement des images {done}/{len(needed)}")
            return item, path, None

        with ThreadPoolExecutor(max_workers=4) as workers:  # several downloads at once
            results = list(workers.map(fetch, needed))
        for item, path, problem in results:
            if isinstance(problem, RateLimited):
                # Every other file from this host would fail too: keep only what we already have.
                limited = str(problem)
                failed.append(limited)
                broken += [i for i in pool + intro if i.source == problem.source and not is_cached(i, light)]
            elif problem == "late":
                broken.append(item)
            elif problem:
                broken.append(item)
                failed.append(problem)
            elif (item.kind == "photo" and item is not opener and len(broken) < spare
                    and quality.rate(path) < quality.POOR):
                broken.append(item)  # blurry, dull or badly exposed: the next best photo takes its place
                dull.append(item.page_url)
        if time.monotonic() - started > DOWNLOADS_BUDGET:
            # Out of time: from now on only media already on disk are planned.
            broken += [i for i in pool + intro if not is_cached(i, light) and i not in broken and i is not opener]
        if not broken:
            break
        pool = [i for i in pool if i not in broken]  # replace failed media with the next best ones
        intro = [i for i in intro if i not in broken]
        if len(pool) < MIN_MEDIA:
            if limited:
                raise RuntimeError(f"{limited}. Réessayez dans quelques minutes. Astuce : renseignez "
                                   "NEKO_CONTACT dans le fichier .env, Wikimedia limite moins les outils identifiés.")
            raise RuntimeError("Trop de médias impossibles à télécharger : " + "; ".join(failed))
    # Each media is used once: with too few different media the reel can end up shorter.
    asked, seconds = seconds, round(sum(s.seconds for s in shots))
    montage = render(shots, seconds, work / "montage.mp4", work, progress, seed, look, size, light)
    video = apply_layers(montage, layers or [], folder / "reel.mp4")
    poster(video, folder / "poster.jpg")
    used = {s.item.key: s.item for s in shots}
    manifest = {
        "place": place, "created": stamp, "duration_s": seconds, "requested_s": asked, "format": f"{size[0]}x{size[1]}", "seed": seed,
        "look": look,
        "warning": "Le statut « fiable » repose sur le titre des médias, pas sur une géolocalisation. Les plans "
                   "de rôle « country » montrent le pays, pas le lieu. Vérifiez chaque plan avant publication.",
        "shots": [{"media": s.item.key, "seconds": s.seconds, "role": s.item.role} for s in shots],
        "media": [i.to_dict() for i in used.values()],
        "skipped": failed,
        "replaced_low_quality": dull,
        "opener": opener.key if opener else "",
        "notes": notes or [],
    }
    (folder / "sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "credits.txt").write_text(credits_text(list(used.values())), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return folder


def load_reel(name: str) -> dict | None:
    """A past reel by folder name, or None if it does not exist."""
    folder = OUTPUT_DIR / Path(name).name  # never leave the outputs folder
    manifest = folder / "sources.json"
    if not (folder / "reel.mp4").exists() or not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {
        "name": folder.name, "folder": folder, "video": folder / "reel.mp4", "poster": folder / "poster.jpg",
        "sources": manifest, "credits": folder / "credits.txt", "place": data["place"],
        "seconds": data["duration_s"], "seed": data.get("seed", 0), "shots": len(data["shots"]),
        "format": data.get("format", "1080x1920").replace("x", " × "),
        "requested": data.get("requested_s", data["duration_s"]),
        "notes": data.get("notes", []),
        "media": [MediaItem(**m) for m in data["media"]],
        "download_name": f"neko-{_slug(data['place'])}-{data['duration_s']}s.mp4",
        "created": datetime.strptime(data["created"], "%Y%m%d-%H%M%S"),
    }


def list_reels(limit: int = 12) -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    reels = [load_reel(d.name) for d in sorted(OUTPUT_DIR.iterdir(), reverse=True) if d.is_dir()]
    return [r for r in reels if r][:limit]


def credits_text(items: list[MediaItem]) -> str:
    lines = ["Crédits des médias (à reprendre dans la légende si une attribution est requise)", ""]
    for i in items:
        must = " — attribution obligatoire" if i.attribution_required or i.license_name.lower().startswith("cc by") else ""
        lines.append(f"- {i.kind} · {i.creator} · {i.license_name}{must}")
        lines.append(f"  {i.page_url}")
    return "\n".join(lines) + "\n"
