import json
import re
import urllib.request
from urllib.parse import quote
from pathlib import Path

import imageio_ffmpeg
from yt_dlp import YoutubeDL

from .config import LIBRARY


def search(query, limit=10, prefix="ytsearch"):
    """Search via any yt-dlp search prefix (ytsearch, scsearch, ...)."""
    opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist"}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{prefix}{limit}:{query}", download=False)
    return [e for e in info.get("entries", []) if e]


def _json_url(url, timeout=8):
    """Small helper: GET a JSON endpoint with a browser-ish User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def bandcamp_search(query, limit=10):
    """Bandcamp has no yt-dlp search prefix; use the same autocomplete_elastic
    endpoint the Bandcamp website search box calls from the browser (POST,
    no API key, not bot-blocked)."""
    body = json.dumps({"search_text": query, "search_filter": "t",
                       "full_page": False, "fan_id": None}).encode()
    req = urllib.request.Request(
        "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic",
        data=body, headers={"User-Agent": "Mozilla/5.0",
                            "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    results = []
    for hit in data.get("auto", {}).get("results", []):
        url = hit.get("item_url_path")
        if hit.get("type") != "t" or not url:  # tracks only
            continue
        name = hit.get("name", "untitled")
        band = hit.get("band_name", "")
        results.append({"id": url, "url": url,
                        "title": f"{band} - {name}" if band else name,
                        "extractor": "bandcamp"})
        if len(results) >= limit:
            break
    if not results:
        raise RuntimeError("no results on Bandcamp")
    return results


def mixcloud_search(query, limit=10):
    """Mixcloud public search API; returns uploads yt-dlp can download."""
    data = _json_url(
        "https://api.mixcloud.com/search/?q=" + quote(query)
        + "&type=cloudcast&limit=" + str(limit))
    return [{"id": item.get("key", ""), "url": item.get("url"),
             "title": item.get("name", "untitled"),
             "extractor": "mixcloud"}
            for item in data.get("data", [])[:limit] if item.get("url")]


# Every source the Search tab queries: the two yt-dlp search prefixes plus
# the two public-endpoint searches (still downloaded through yt-dlp).
SEARCH_SOURCES = (
    ("YouTube", lambda q, n: search(q, n, "ytsearch")),
    ("SoundCloud", lambda q, n: search(q, n, "scsearch")),
    ("Bandcamp", bandcamp_search),
    ("Mixcloud", mixcloud_search),
)

# display order of the grouped Search tab
SOURCE_ORDER = [name for name, _fn in SEARCH_SOURCES]


def search_all(query, limit=10):
    """The single shared multi-source search, used by both the CLI and the
    TUI. Runs every source in SEARCH_SOURCES and returns one tuple per
    source: (name, entries, error) — error is None on success so callers
    can show exactly why a source failed."""
    out = []
    for name, fn in SEARCH_SOURCES:
        try:
            out.append((name, fn(query, limit), None))
        except Exception as exc:
            out.append((name, [], exc))
    return out


def download(entry):
    """Save the entry's best audio stream in the library, without re-encoding. Returns (path, outcome)."""
    url = (entry.get("webpage_url") or entry.get("url")
           or f"https://www.youtube.com/watch?v={entry['id']}")
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
