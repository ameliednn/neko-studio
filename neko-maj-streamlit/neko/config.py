"""Settings and secrets. API keys are read from the environment or a local .env
file and are never written into the code or shown in the interface."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"
CACHE_DIR = ROOT / "cache"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
DURATIONS = (15, 20, 30, 45, 60)
MIN_MEDIA = 4  # below this, the reel would repeat too much or rely on doubtful media

def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("'\""))


_load_dotenv()

# Wikimedia asks automated tools to identify themselves with a way to contact them.
USER_AGENT = f"NekoStudio/1.0 (private travel reel tool; {os.environ.get('NEKO_CONTACT', 'no contact set')})"


def secret(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _add_winget_ffmpeg_to_path() -> None:
    """FFmpeg installed by winget is only on PATH for newly opened windows; find it anyway."""
    if shutil.which("ffmpeg") or os.name != "nt":
        return
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for exe in sorted(packages.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"), reverse=True):
        os.environ["PATH"] = f"{exe.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        return


_add_winget_ffmpeg_to_path()


def _find_ffmpeg() -> str | None:
    """The system FFmpeg, or else the one shipped with the imageio-ffmpeg package, so hosted
    servers that skipped packages.txt still work."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = _find_ffmpeg()


def ffmpeg_path() -> str | None:
    return FFMPEG
