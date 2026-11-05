import subprocess
import sys
from array import array

import imageio_ffmpeg
import miniaudio

from .config import CHANNELS, FRAMES_PER_SECOND, METER_BANDS, METER_FULL_SCALE, SAMPLE_RATE

FORMAT = miniaudio.SampleFormat.SIGNED16
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def decode(path):
    """Decode any audio file to 16-bit stereo PCM at SAMPLE_RATE, using the bundled ffmpeg."""
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-vn",
           "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"]
    if sys.platform == "win32":
        result = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "ffmpeg could not decode the file")
    samples = array("h")
    samples.frombytes(result.stdout)
    return samples


class Player:
    """Decodes one track at a time and streams it to the sound device."""

    def __init__(self):
        self.device = miniaudio.PlaybackDevice(
            output_format=FORMAT, nchannels=CHANNELS, sample_rate=SAMPLE_RATE
        )
        self.samples = array("h")
        self.pos = 0
        self.paused = False
        self.loaded = False
        self.volume = 1.0
        self._running = False

    def _stream(self):
        needed = yield array("h")
        while True:
            count = needed * CHANNELS
            chunk = self.samples[self.pos:self.pos + count]
            self.pos += len(chunk)
            chunk.extend(array("h", [0]) * (count - len(chunk)))
            if self.volume < 1.0:
                chunk = array("h", [int(s * self.volume) for s in chunk])
            needed = yield chunk

    def set_volume(self, volume):
        self.volume = round(min(1.0, max(0.0, volume)), 2)

    def _start(self):
        stream = self._stream()
        next(stream)
        self.device.start(stream)
        self._running = True

    def _halt(self):
        if self._running:
            self.device.stop()
            self._running = False

    def load(self, path):
        samples = decode(path)  # decode first, so a bad file leaves the current track playing
        self._halt()
        self.samples = samples
        self.pos = 0
        self.paused = False
        self.loaded = True
        self._start()

    def stop(self):
        self._halt()
        self.samples = array("h")
        self.pos = 0
        self.paused = False
        self.loaded = False

    def toggle_pause(self):
        if self.paused:
            self._start()
        else:
            self._halt()
        self.paused = not self.paused

    def seek(self, seconds):
        target = self.pos + int(seconds * FRAMES_PER_SECOND)
        self.pos = max(0, min(len(self.samples), target))
        self.pos -= self.pos % CHANNELS

    def elapsed(self):
        return self.pos / FRAMES_PER_SECOND

    def duration(self):
        return len(self.samples) / FRAMES_PER_SECOND

    def finished(self):
        return self.pos >= len(self.samples)

    def meter(self):
        """Loudness per band for the next few frames, as 0..1 ratios."""
        if not self.loaded or self.paused:
            return [0.0] * METER_BANDS
        window = self.samples[self.pos:self.pos + 4096]
        band = max(1, len(window) // METER_BANDS)
        ratios = []
        for i in range(METER_BANDS):
            chunk = window[i * band:(i + 1) * band]
            level = sum(abs(s) for s in chunk) / band if chunk else 0
            ratios.append(round(min(1.0, level / METER_FULL_SCALE), 2))
        return ratios

    def close(self):
        self.stop()
        self.device.close()
