from pathlib import Path

import imageio_ffmpeg
from yt_dlp import YoutubeDL

from .config import LIBRARY


def search(query, limit=10):
    opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist"}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [e for e in info.get("entries", []) if e]


def download(entry):
    """Save the entry's best audio stream in the library, without re-encoding. Returns (path, outcome)."""
    url = f"https://www.youtube.com/watch?v={entry['id']}"
    opts = {
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(LIBRARY / "%(title)s.%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),  # yt-dlp needs it to fix up some containers
    }
    LIBRARY.mkdir(parents=True, exist_ok=True)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        target = Path(ydl.prepare_filename(info))
        if target.exists():
            return target, "already in library"
        ydl.download([url])
        return target, "saved"
