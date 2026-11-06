"""On-demand loudness measurement — in-memory only, nothing is written to disk.

When a track is queued, one ffmpeg decode (ebur128) measures its integrated
loudness plus the momentary (400 ms) series. The gain is the quieter of:
  - matching the common-sense loudness_target from config.json, or
  - keeping the loudest 400 ms blocks (99th percentile) under a ceiling,
  clamped to ±15 dB, and applied on top of the user's volume knob when the
  file starts to play. The daemon keeps results in memory for the session.
"""

import math
import re
import subprocess
import sys

from .config import load_target

CEILING_LUFS = -6.0   # the loudest 400 ms blocks must stay under this
MAX_GAIN_DB = 15.0    # never correct more than this (protects against junk)
PERCENTILE = 0.99     # "the N loudest 400 ms parts" -> 99th percentile
FFMPEG = None  # resolved lazily; the bundled ffmpeg from imageio_ffmpeg


def _ffmpeg():
    global FFMPEG
    if FFMPEG is None:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    return FFMPEG


def measure(path):
    """Measure one file. Returns (integrated_lufs, p99_momentary_lufs).
    Values are None when ffmpeg cannot measure (broken/missing file)."""
    cmd = [_ffmpeg(), "-nostdin", "-i", str(path), "-filter_complex", "ebur128",
           "-f", "null", "-"]
    if sys.platform == "win32":
        result = subprocess.run(cmd, capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        result = subprocess.run(cmd, capture_output=True)
    stderr = result.stderr.decode(errors="replace")
    integrated = [float(m) for m in re.findall(r"I:\s*(-?\d+\.?\d*) LUFS", stderr)]
    # momentary (400 ms) blocks, 10 updates/s; "-inf" (silence) does not match
    momentary = [float(m) for m in re.findall(r"M:\s*(-?\d+\.?\d*)", stderr)]
    if not integrated or not momentary:
        return None, None
    momentary.sort()
    p99 = momentary[min(len(momentary) - 1, int(PERCENTILE * len(momentary)))]
    return integrated[-1], p99  # the last I is the final summary value


def compute_gain(integrated, p99_momentary, target):
    """The hybrid gain: match the configured target loudness, but never
    boost the loudest sections past the ceiling."""
    gain = min(target - integrated, CEILING_LUFS - p99_momentary)
    return round(max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain)), 2)


def measure_gain(path):
    """Measure one track against the configured loudness_target.
    Returns the gain in dB, or None when it cannot be measured."""
    try:
        integrated, p99 = measure(path)
        if integrated is None:
            return None
        return compute_gain(integrated, p99, load_target())
    except Exception:
        return None


def gain_multiplier(gain_db):
    """Playback multiplier: user volume is multiplied by this.
    0 dB (unmeasured) -> 1.0."""
    return 10.0 ** (float(gain_db or 0.0) / 20.0)
