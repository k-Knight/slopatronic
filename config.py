import json
from pathlib import Path

# Everything lives in one hidden folder; the config is one file inside it.
LIBRARY = Path.home() / ".slopatronic"
CONFIG_FILE = LIBRARY / "config.json"

DEFAULT_CONFIG = {
    "folders": [],   # extra library folders: [{"path": ..., "recursive": ...}]
    "volume": 1.0,   # remembered player volume (0..1)
    "loudness_target": -12.0,  # common-sense target loudness (LUFS) tracks are adjusted to
    "loudness_correction": True,  # toggle: adjust track volume to the loudness target
}
EXTENSIONS = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}

# The background player listens on this local port.
HOST = "127.0.0.1"
PORT = 47651

SAMPLE_RATE = 44100
CHANNELS = 2
FRAMES_PER_SECOND = SAMPLE_RATE * CHANNELS

METER_BANDS = 24
METER_FULL_SCALE = 12000
BAR_CHARS = "▁▂▃▄▅▆▇█"

# Folder that contains the slopatronic package, used to start the player in the background.
PACKAGE_PARENT = Path(__file__).resolve().parents[1]


def ensure_config():
    """Always make sure the config file exists (even if empty)."""
    LIBRARY.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(dict(DEFAULT_CONFIG))


def load_config():
    """The config dict, merged over the defaults; generates the file if needed."""
    ensure_config()
    data = dict(DEFAULT_CONFIG)
    try:
        data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return data


def save_config(data):
    LIBRARY.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_volume():
    return min(1.0, max(0.0, float(load_config()["volume"])))


def save_volume(volume):
    data = load_config()
    data["volume"] = round(min(1.0, max(0.0, float(volume))), 3)
    save_config(data)


def load_target():
    """The common-sense loudness target (LUFS) from the config file."""
    return float(load_config()["loudness_target"])


def correction_enabled():
    """Whether loudness correction is toggled on in the config file."""
    return bool(load_config()["loudness_correction"])
