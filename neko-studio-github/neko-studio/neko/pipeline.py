"""The steps the interface calls: search, generate, and reading back past reels."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import quality, verify
from .config import MIN_MEDIA, OUTPUT_DIR, WIDTH, HEIGHT
from .layers import Layer, apply_layers
from .models import REJECTED, RELIABLE, TO_CHECK, MediaItem
from .render import Progress, RateLimited, download, is_cached, plan, poster, render
from .sources import search_all

STATUS_ORDER = {RELIABLE: 0, TO_CHECK: 1, REJECTED: 2}


def find_media(place: str, sources: list[str]) -> tuple[list[MediaItem], list[str]]:
    items, errors = search_all(sources, verify.search_queries(place))
    items = [verify.score(i, place) for i in items]
    items.sort(key=lambda i: (STATUS_ORDER[i.status], -i.score))
    return items, errors


def find_country_media(place: str, sources: list[str], avoid: set[str] = frozenset()) -> list[MediaItem]:
    """Two striking wide views of the country, to open the reel. Empty if no country was given."""
    country = verify.parse_place(place)[1]
    if not country:
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
    ok.sort(key=lambda i: (i.kind != "video", -i.score))
    return ok[:limit]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", verify.normalize(text).strip()).strip("-") or "reel"


def generate(place: str, items: list[MediaItem], seconds: int, progress: Progress | None = None,
             layers: list[Layer] | None = None, seed: int = 0, intro: list[MediaItem] | None = None,
             look: str = "vif") -> Path:
    """Builds the reel and its records; returns the reel's folder."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Short names: Windows refuses paths longer than 260 characters.
    folder = OUTPUT_DIR / f"{stamp}-{_slug(verify.parse_place(place)[0])[:20]}"
    work = folder / "work"
    progress = progress or (lambda f, m: None)
    pool, intro, failed, dull, limited = list(items), list(intro or []), [], [], ""
    while True:
        shots = plan(pool, seconds, seed, intro)
        needed = list({s.item.key: s.item for s in shots}.values())
        spare = len(pool) + len(intro) - len(needed)  # media we could swap in
        broken = []
        for k, item in enumerate(needed):
            progress(0.02 + 0.2 * k / len(needed), f"Téléchargement des images {k + 1}/{len(needed)}")
            try:
                path = download(item)
            except RateLimited as exc:
                # Every other file from this host would fail too: keep only what we already have.
                limited = str(exc)
                failed.append(limited)
                broken += [i for i in pool + intro if i.source == exc.source and not is_cached(i)]
                progress(0.2, f"{limited} : montage avec les images déjà récupérées")
                break
            except Exception as exc:
                broken.append(item)
                failed.append(f"{item.page_url} ({exc})")
                continue
            if item.kind == "photo" and len(broken) < spare and quality.rate(path) < quality.POOR:
                broken.append(item)  # blurry, dull or badly exposed: the next best photo takes its place
                dull.append(item.page_url)
        if not broken:
            break
        pool = [i for i in pool if i not in broken]  # replace failed media with the next best ones
        intro = [i for i in intro if i not in broken]
        if len(pool) < MIN_MEDIA:
            if limited:
                raise RuntimeError(f"{limited}. Réessayez dans quelques minutes. Astuce : renseignez "
                                   "NEKO_CONTACT dans le fichier .env, Wikimedia limite moins les outils identifiés.")
            raise RuntimeError("Trop de médias impossibles à télécharger : " + "; ".join(failed))
    montage = render(shots, seconds, work / "montage.mp4", work, progress, seed, look)
    video = apply_layers(montage, layers or [], folder / "reel.mp4")
    poster(video, folder / "poster.jpg")
    used = {s.item.key: s.item for s in shots}
    manifest = {
        "place": place, "created": stamp, "duration_s": seconds, "format": f"{WIDTH}x{HEIGHT}", "seed": seed,
        "look": look,
        "warning": "Le statut « fiable » repose sur le titre des médias, pas sur une géolocalisation. Les plans "
                   "de rôle « country » montrent le pays, pas le lieu. Vérifiez chaque plan avant publication.",
        "shots": [{"media": s.item.key, "seconds": s.seconds, "role": s.item.role} for s in shots],
        "media": [i.to_dict() for i in used.values()],
        "skipped": failed,
        "replaced_low_quality": dull,
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
