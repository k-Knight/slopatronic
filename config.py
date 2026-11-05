from pathlib import Path

LIBRARY = Path.home() / "my_py_music"
EXTENSIONS = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}

# Extra folders the library also reads from, persisted as JSON:
# [{"path": "/abs/dir", "recursive": false}, ...]
DIRS_FILE = LIBRARY / "folders.json"

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
