"""Neko Studio — web app, phone first. Start with:  python -m streamlit run app.py

Screens follow the address bar: no parameter = home, ?job=<id> = a reel being
made (survives a locked phone or a reload), ?reel=<folder> = a finished reel."""
from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components


def _ensure_ffmpeg() -> str:
    """Hosted servers may skip packages.txt. Then use the FFmpeg shipped with the
    imageio-ffmpeg package (installed now if requirements.txt was not picked up), exposed
    as plain "ffmpeg" on PATH so every module finds it. Returns '' or what went wrong."""
    if shutil.which("ffmpeg"):
        return ""
    try:
        import imageio_ffmpeg
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "imageio-ffmpeg>=0.5,<1"],
                       capture_output=True, text=True)
        try:
            import importlib
            importlib.invalidate_caches()
            import imageio_ffmpeg
        except ImportError as exc:
            return f"imageio-ffmpeg n'a pas pu être installé ({exc})"
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        folder = Path(tempfile.gettempdir()) / "neko-ffmpeg"
        folder.mkdir(exist_ok=True)
        link = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not link.exists():
            try:
                link.symlink_to(exe)
            except OSError:
                shutil.copy(exe, link)
                link.chmod(0o755)
        os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"
        return ""
    except Exception as exc:
        return f"FFmpeg de secours inutilisable ({exc})"


FFMPEG_PROBLEM = _ensure_ffmpeg()  # before importing neko, which looks FFmpeg up once

from neko import jobs, uploads  # noqa: E402
from neko.config import ROOT, DURATIONS, MIN_MEDIA, default_quality, ffmpeg_path, secret  # noqa: E402
from neko.models import REJECTED, RELIABLE, TO_CHECK, MediaItem  # noqa: E402
from neko.pipeline import list_reels, load_reel  # noqa: E402
from neko.sources import SOURCES  # noqa: E402

IDEAS = ["Shirakawa-go, Japon", "Randonnées Japon", "Bord de mer Corée", "Hallstatt, Autriche"]
AUTH_COOKIE = "neko_auth"
QUALITY_LABELS = {"rapide": "Rapide (720p)", "hd": "HD (1080p)"}
MEDIA_TYPES = list(uploads.VIDEO_TYPES + uploads.PHOTO_TYPES)

st.set_page_config(page_title="Neko Studio", page_icon=str(ROOT / "static" / "icon-180.png"), layout="centered")

st.markdown("""
<style>
  header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer { display: none; }
  .block-container { max-width: 560px; padding: 2.2rem 1.1rem 4rem; }
  .neko-logo { display: block; width: 56px; height: 56px; border-radius: 16px; margin: 0 auto 1.2rem; }
  .neko-hero { text-align: center; font-size: 2.3rem; line-height: 1.12; font-weight: 800;
               letter-spacing: -0.03em; margin: 0 0 0.4rem; color: #1F1F1F; }
  .neko-hero span { color: #F26B1D; }
  .neko-sub { text-align: center; color: #7A7A7A; margin: 0 0 1.6rem; }
  .neko-title { font-size: 1.6rem; font-weight: 800; letter-spacing: -0.02em; margin: 0.4rem 0 0; }
  .neko-meta { font-size: 0.8rem; color: #7A7A7A; line-height: 1.35; }
  .neko-badge { display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 2px 8px;
                border-radius: 999px; margin-right: 4px; }
  .b-fiable { background: #E8F6EC; color: #1E7B3A; } .b-verifier { background: #FFF1E6; color: #B4540F; }
  .b-ecarte { background: #F1F1F1; color: #6B6B6B; } .b-video { background: #F26B1D; color: #FFF; }
  [data-testid="stForm"] { border: none; padding: 0; }
  /* 16px or more stops iPhone from zooming into the field */
  [data-testid="stTextInput"] input { font-size: 1.15rem; padding: 0.95rem 1.1rem; }
  [data-testid="stTextInput"] > div > div { border-radius: 18px; }
  .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
      border-radius: 999px; font-weight: 700; min-height: 3rem; }
  [data-testid="stSegmentedControl"] { display: flex; justify-content: center; }
  [data-testid="stVideo"] video { max-height: 72vh; width: auto; max-width: 100%; display: block;
                                   margin: 0 auto; border-radius: 20px; background: #000; }
  div[data-testid="stImage"] img { border-radius: 12px; aspect-ratio: 3 / 4; object-fit: cover; }
  @media (max-width: 640px) {
    .block-container { padding-top: 1.4rem; }
    .neko-hero { font-size: 2rem; }
    div[data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap; gap: 0.7rem; }
    div[data-testid="stColumn"] { flex: 1 1 calc(50% - 0.7rem) !important; min-width: calc(50% - 0.7rem) !important; }
  }
</style>
""", unsafe_allow_html=True)


def head_tags(set_cookie: str = "") -> None:
    """Home-screen icon and name for iPhone/Android, plus the 'remember me' cookie."""
    components.html(f"""<script>
      // The app's page; on Streamlit Cloud it sits in a frame of a same-origin shell page, and
      // iPhone reads the home-screen icon from the outermost page only.
      const app = window.parent.document;
      let d = app;
      try {{ d = window.top.document; }} catch (e) {{}}
      const icon = new URL("app/static/icon-180.png", app.baseURI).href;
      const add = (tag, attrs) => {{
        const sel = tag + Object.entries(attrs).slice(0, 1).map(([k, v]) => `[${{k}}="${{v}}"]`).join("");
        if (d.head.querySelector(sel)) return;
        const el = d.createElement(tag); Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        d.head.appendChild(el);
      }};
      add("link", {{rel: "apple-touch-icon", href: icon}});
      add("meta", {{name: "apple-mobile-web-app-title", content: "Neko Studio"}});
      add("meta", {{name: "theme-color", content: "#FFFFFF"}});
      {f'app.cookie = "{AUTH_COOKIE}={set_cookie}; max-age=31536000; path=/; samesite=strict";' if set_cookie else ""}
    </script>""", height=0)


def gate() -> None:
    """Optional password (NEKO_PASSWORD in .env), remembered on the device for a year."""
    expected = secret("NEKO_PASSWORD")
    if not expected:
        return
    token = hmac.new(expected.encode(), b"neko-studio", hashlib.sha256).hexdigest()
    if st.session_state.get("authed") or hmac.compare_digest(st.context.cookies.get(AUTH_COOKIE, ""), token):
        return
    st.markdown('<img class="neko-logo" src="app/static/icon-180.png">', unsafe_allow_html=True)
    st.markdown('<p class="neko-hero">Neko <span>Studio</span></p>', unsafe_allow_html=True)
    with st.form("login"):
        pw = st.text_input("Mot de passe", type="password")
        ok = st.form_submit_button("Entrer", type="primary", width="stretch")
    if ok and hmac.compare_digest(pw, expected):
        st.session_state.authed = True
        st.session_state.cookie_token = token  # written by head_tags() on the next run
        st.rerun()
    if ok:
        st.error("Mot de passe incorrect.")
    st.stop()


def prefs() -> dict:
    """Montage options chosen on the home screen (defaults when this device never opened them)."""
    look = "naturel" if st.session_state.get("opt_look") == "Naturelles" else "vif"
    quality = next((k for k, v in QUALITY_LABELS.items() if v == st.session_state.get("opt_quality")),
                   default_quality())
    return {"intro": st.session_state.get("opt_intro", True), "look": look, "quality": quality,
            "pick_opener": st.session_state.get("opt_pick", False)}


def go(**params: str) -> None:
    st.query_params.clear()
    st.query_params.update(params)
    st.rerun()


def thumb(item: MediaItem) -> str:
    if item.source == "wikimedia":
        name = item.page_url.rsplit("/", 1)[-1].removeprefix("File:")
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name)}?width=330"
    return item.thumb_url


# ---------- Home ----------

def home() -> None:
    st.markdown('<img class="neko-logo" src="app/static/icon-180.png">', unsafe_allow_html=True)
    st.markdown('<p class="neko-hero">Quelle destination<br><span>aujourd\'hui ?</span></p>', unsafe_allow_html=True)
    st.markdown('<p class="neko-sub">Un lieu, des images réelles, un Reel prêt à publier.</p>',
                unsafe_allow_html=True)

    with st.form("create", border=False):
        place = st.text_input("Destination", key="place_input", placeholder="Un lieu ou une envie : Shirakawa-go, Japon · Randonnées Japon",
                              label_visibility="collapsed")
        choice = st.segmented_control("Durée", [f"{d} s" for d in DURATIONS], default="20 s",
                                      label_visibility="collapsed")
        note = st.text_area("Plans souhaités (facultatif)", key="note_input", height=80,
                            placeholder="Ex. : baie vue du large, façades funaya, ruelles, bateau de pêche",
                            help="Séparez les idées par des virgules. Chacune est cherchée à part et passe en "
                                 "priorité ; si rien d'authentique n'est trouvé, l'app le signale au lieu de la "
                                 "remplacer par un endroit ressemblant. Les mots courants sont traduits, l'anglais "
                                 "donne les meilleurs résultats.")
        files = st.file_uploader("Ajouter mes vidéos et photos (facultatif)", type=MEDIA_TYPES,
                                 accept_multiple_files=True,
                                 help="Elles passent en premier ; Neko Studio complète avec des images "
                                      "réelles du lieu pour atteindre la durée choisie.")
        submitted = st.form_submit_button("Créer mon Reel", type="primary", width="stretch")

    st.pills("Idées", IDEAS, key="idea", label_visibility="collapsed",
             on_change=lambda: st.session_state.update(place_input=st.session_state.idea or ""))

    with st.expander("Options"):
        labels = {name: cls.label for name, cls in SOURCES.items()}
        st.multiselect("Sources autorisées", list(labels), default=list(labels), format_func=labels.get,
                       key="opt_sources")
        st.segmented_control("Qualité", list(QUALITY_LABELS.values()), default=QUALITY_LABELS[default_quality()],
                             key="opt_quality",
                             help="Rapide : environ deux fois moins de calcul, idéal sur le serveur en ligne. "
                                  "HD : la meilleure netteté, plus long.")
        st.toggle("Choisir moi-même la première image (3 propositions)", key="opt_pick",
                  help="Avant le montage, l'app propose ses 3 meilleures ouvertures : vues panoramiques, "
                       "brumeuses ou mystérieuses, montrant ce qu'on ne voit nulle part ailleurs. "
                       "Vous pouvez aussi écrire « ouverture : brume sur la baie » dans les plans souhaités.")
        st.toggle("Ajouter 2 belles vues du pays après la première image (si le pays est indiqué)", value=True,
                  key="opt_intro")
        st.segmented_control("Couleurs", ["Vives", "Naturelles"], default="Vives", key="opt_look")
        st.toggle("Toujours me montrer les images avant le montage", key="opt_review")

    if submitted:
        if not place.strip():
            st.warning("Écrivez d'abord un lieu, par exemple « Shirakawa-go, Japon ».")
        else:
            seconds = int((choice or "20 s").split()[0])
            sources = st.session_state.get("opt_sources") or list(SOURCES)
            with st.spinner("Envoi de vos médias…") if files else st.empty():
                mine = [uploads.save(f.name, f.getvalue()) for f in files or []]
            job = jobs.start(place.strip(), seconds, sources, review=st.session_state.get("opt_review", False),
                             mine=mine, note=note, **prefs())
            go(job=job.id)

    reels = list_reels(9)
    if reels:
        st.markdown("#### Mes Reels")
        cols = st.columns(3)
        for k, r in enumerate(reels):
            with cols[k % 3]:
                if r["poster"].exists():
                    st.image(str(r["poster"]))
                if st.button(f"{r['place'].split(',')[0]} · {r['seconds']} s", key=f"open_{r['name']}",
                             width="stretch"):
                    go(reel=r["name"])


# ---------- A reel being made ----------

def job_screen(job_id: str) -> None:
    job = jobs.get(job_id)
    if not job:
        st.info("Ce montage n'est plus en mémoire (le studio a redémarré). Retrouvez vos Reels terminés "
                "sur l'accueil.")
        if st.button("Accueil", type="primary", width="stretch"):
            go()
        return
    if job.state == "done":
        go(reel=job.reel)

    st.markdown(f'<p class="neko-title">{job.place}</p>', unsafe_allow_html=True)
    mine = f" · {len(job.mine)} de vos médias" if job.mine else ""
    st.markdown(f'<p class="neko-meta">Reel de {job.seconds} s · {QUALITY_LABELS[job.quality]}{mine}</p>',
                unsafe_allow_html=True)

    if job.state in ("search", "render"):
        @st.fragment(run_every=1.0)
        def watch() -> None:
            current = jobs.get(job_id)
            if current.state not in ("search", "render"):
                st.rerun(scope="app")
            st.progress(min(current.progress, 1.0), current.message)

        watch()
        st.caption("Vous pouvez verrouiller votre téléphone : le montage continue sur le serveur. "
                   "Rouvrez Neko Studio pour retrouver votre Reel.")
    elif job.state == "error":
        st.error(f"Le montage a échoué : {job.error}")
        if st.button("Réessayer", type="primary", width="stretch"):
            go(job=jobs.start(job.place, job.seconds, job.sources, job.seed + 1, intro=job.intro, look=job.look,
                              quality=job.quality, mine=job.mine, note=job.note).id)
        if st.button("Accueil", width="stretch"):
            go()
    elif job.state == "review":
        review_screen(job)
    elif job.state == "opener":
        opener_screen(job)


def preview(path: Path, width: int = 360) -> bytes:
    """A light JPEG of a large photo or video still, quick to show on a phone."""
    from io import BytesIO
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((width, width * 2))
        out = BytesIO()
        img.save(out, "JPEG", quality=82)
        return out.getvalue()


def opener_screen(job: jobs.Job) -> None:
    st.markdown("**Quelle première image ?** Elle décide si on regarde la suite : choisissez la plus "
                "spectaculaire, mystérieuse ou étonnante.")
    cols = st.columns(len(job.choices))
    for col, (_, item, still) in zip(cols, job.choices):
        with col:
            try:
                st.image(preview(still))
            except Exception:
                st.caption(item.title.split(".")[0][:40])
            kind = "Vidéo" if item.kind == "video" else "Photo"
            st.caption(f"{kind} · {item.reasons[0][:50] if item.reasons else ''}")
            if st.button("Ouvrir avec celle-ci", key=f"pick_{job.id}_{item.key}", type="primary", width="stretch"):
                jobs.choose(job, item.key)
                st.rerun()


def media_card(item: MediaItem, key: str, label: str, default: bool) -> bool:
    css, name = {RELIABLE: ("b-fiable", "Fiable"), TO_CHECK: ("b-verifier", "À vérifier"),
                 REJECTED: ("b-ecarte", "Écarté")}[item.status]
    if thumb(item):
        st.image(thumb(item))
    else:  # videos without a preview image, and your own uploads
        st.markdown(f'<div style="aspect-ratio:3/4;border-radius:12px;background:#FFF4EC;display:flex;'
                    f'align-items:center;justify-content:center;color:#B4540F;font-weight:700;padding:8px;'
                    f'text-align:center;font-size:0.85rem">{item.title.split(".")[0][:40]}</div>',
                    unsafe_allow_html=True)
    kind = '<span class="neko-badge b-video">VIDÉO</span>' if item.kind == "video" else ""
    link = f' · <a href="{item.page_url}" target="_blank">source</a>' if item.page_url else ""
    st.markdown(f'{kind}<span class="neko-badge {css}">{name}</span>'
                f'<div class="neko-meta">{item.reasons[0] if item.reasons else ""}{link}</div>', unsafe_allow_html=True)
    return st.checkbox(label, value=default, key=key)


def review_screen(job: jobs.Job) -> None:
    reliable = [i for i in job.items if i.status == RELIABLE]
    doubtful = [i for i in job.items if i.status == TO_CHECK]
    if len(reliable) + len(job.mine) < MIN_MEDIA:
        st.warning(f"Il manque des médias authentiques : seulement {len(reliable)} image(s) portent le nom exact "
                   f"de ce lieu. Cochez uniquement celles qui montrent vraiment {job.place}, ajoutez vos propres "
                   "médias depuis l'accueil, ou essayez une orthographe plus courante (en anglais, avec le pays).")
    else:
        st.info("Choisissez les images à utiliser. Les images fiables sont déjà cochées.")
    if job.mine:
        st.caption(f"Vos {len(job.mine)} médias sont inclus d'office et passeront en premier.")
    for err in job.errors:
        st.caption(err)
    picked = {i.key for i in jobs.best_reliable(job.items, jobs.AUTO_PICK)}
    selected: list[MediaItem] = list(job.mine)
    cols = st.columns(3)
    for k, item in enumerate((reliable + doubtful)[:36]):
        with cols[k % 3]:
            if media_card(item, f"rv_{job.id}_{item.key}", "Utiliser", item.key in picked):
                selected.append(item)
    if not reliable and not doubtful:
        st.error(f"Aucune image de {job.place} trouvée. Vérifiez l'orthographe du lieu.")

    unsure = [i for i in selected if i.status != RELIABLE]
    ready = len(selected) >= MIN_MEDIA
    if unsure:
        ready = st.checkbox(f"Je confirme que les {len(unsure)} image(s) « à vérifier » cochées montrent bien "
                            f"{job.place}.") and ready
    st.caption(f"{len(selected)} média(s) choisi(s) · il en faut au moins {MIN_MEDIA}.")
    if st.button("Créer le Reel avec cette sélection", type="primary", disabled=not ready, width="stretch"):
        go(job=jobs.start(job.place, job.seconds, job.sources, job.seed, items=selected, intro=job.intro,
                          look=job.look, quality=job.quality).id)
    if st.button("Changer de destination", width="stretch"):
        go()


# ---------- A finished reel ----------

def reel_screen(name: str) -> None:
    reel = load_reel(name)
    if not reel:
        st.info("Ce Reel est introuvable.")
        if st.button("Accueil", type="primary", width="stretch"):
            go()
        return
    if st.button("← Nouvelle destination"):
        go()
    st.markdown(f'<p class="neko-title">{reel["place"]}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="neko-meta">{reel["seconds"]} s · {reel["shots"]} plans · {reel["format"]} · '
                f'{reel["created"]:%d/%m/%Y %H:%M}</p>', unsafe_allow_html=True)
    if reel["requested"] > reel["seconds"]:
        st.info(f"Reel de {reel['seconds']} s au lieu de {reel['requested']} s : chaque image n'est utilisée "
                "qu'une fois et il n'y avait pas assez de médias différents. Ajoutez vos vidéos ou élargissez "
                "la recherche (par exemple avec un thème : « Randonnées Japon »).")
    for missing in reel["notes"]:
        st.warning(missing)
    st.video(str(reel["video"]))
    st.download_button("Télécharger le MP4", reel["video"].read_bytes(), file_name=reel["download_name"],
                       mime="video/mp4", type="primary", width="stretch")
    st.caption("Sur iPhone : touchez Télécharger, ouvrez l'app Fichiers › Téléchargements, touchez la vidéo, "
               "puis Partager › Enregistrer la vidéo pour la retrouver dans Photos.")
    if st.button("Nouvelle variante (autre ordre, autres images)", width="stretch"):
        go(job=jobs.start(reel["place"], reel["seconds"], list(SOURCES), reel["seed"] + 1, **prefs()).id)

    with st.expander("Ajouter mes vidéos et photos"):
        st.caption("Vos propres prises de vue passent en premier dans le nouveau montage. "
                   "Vidéos (MP4, MOV) et photos (JPG, PNG, HEIC) depuis votre pellicule.")
        files = st.file_uploader("Vos médias", type=MEDIA_TYPES, accept_multiple_files=True,
                                 label_visibility="collapsed", key=f"up_{reel['name']}")
        if st.button("Refaire le Reel avec mes médias", type="primary", disabled=not files, width="stretch"):
            mine = [uploads.save(f.name, f.getvalue()) for f in files]
            kept = [m for m in reel["media"] if m.role != "country"]
            go(job=jobs.start(reel["place"], reel["seconds"], list(SOURCES), reel["seed"] + 1,
                              items=mine + kept, **prefs()).id)

    with st.expander(f"Images utilisées ({len(reel['media'])}) — retirer et refaire"):
        removed: set[str] = set()
        cols = st.columns(3)
        for k, item in enumerate(reel["media"]):
            with cols[k % 3]:
                if media_card(item, f"rm_{reel['name']}_{item.key}", "Retirer", False):
                    removed.add(item.key)
        if st.button(f"Refaire sans les {len(removed)} image(s) retirée(s)", disabled=not removed, width="stretch"):
            go(job=jobs.start(reel["place"], reel["seconds"], list(SOURCES), reel["seed"] + 1, exclude=removed,
                              **prefs()).id)

    with st.expander("Crédits et licences"):
        st.code(reel["credits"].read_text(encoding="utf-8"), language=None)
        st.download_button("Crédits à citer (TXT)", reel["credits"].read_bytes(), file_name="credits.txt",
                           mime="text/plain", width="stretch")
        st.download_button("Sources détaillées (JSON)", reel["sources"].read_bytes(), file_name="sources.json",
                           mime="application/json", width="stretch")


head_tags(set_cookie=st.session_state.pop("cookie_token", ""))
gate()
if not ffmpeg_path():
    st.error("FFmpeg est introuvable. Suivez l'étape 2 du guide d'installation, puis relancez Neko Studio.")
    # What to send for help: why the fallback failed and which files this server really runs.
    here = Path(__file__).resolve().parent
    st.code(f"{FFMPEG_PROBLEM or 'FFmpeg de secours non trouvé'}\n"
            f"Dossier de l'app : {here}\nFichiers : {', '.join(sorted(p.name for p in here.iterdir()))}\n"
            f"Python : {sys.version.split()[0]}", language=None)
    st.stop()

params = st.query_params
if "job" in params:
    job_screen(params["job"])
elif "reel" in params:
    reel_screen(params["reel"])
else:
    home()
