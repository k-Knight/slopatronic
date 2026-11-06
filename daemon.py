import json
import socketserver
import threading
import time
from pathlib import Path

from .config import HOST, PORT, correction_enabled, load_volume, save_volume
from .loudness import gain_multiplier, measure_gain
from .player import Player


class Daemon:
    """Owns the queue and the player. Requests come in over a local socket."""

    def __init__(self):
        self.lock = threading.RLock()
        self.queue = []
        self.index = 0
        self.player = Player()
        # the knob; per-track normalization gain is separate
        self.user_volume = load_volume()
        self.player.set_volume(self.user_volume)
        # in-memory loudness gains (dB) per queued track path, measured when
        # a track is queued; never written to disk, gone when the daemon stops
        self.gains = {}
        self.server = None
        self.stopping = threading.Event()

    def _measure_async(self, paths):
        """Measure queued tracks in the background and cache the gain in
        memory; if one of them is already playing, apply it as soon as known.
        Skipped entirely when the loudness_correction toggle is off."""
        if not correction_enabled():
            return

        def work():
            for path in paths:
                gain = measure_gain(path)
                with self.lock:
                    self.gains[str(path)] = gain or 0.0
                    if (self.player.loaded and self.index < len(self.queue)
                            and str(self.queue[self.index]) == str(path)):
                        self.player.set_gain(gain_multiplier(gain))
        threading.Thread(target=work, daemon=True).start()

    def _load_current(self):
        self.player.load(self.queue[self.index])
        # passive volume normalization: the in-memory gain measured at queue
        # time, applied on top of the user's volume knob; neutral unless the
        # loudness_correction toggle is on and a measured gain exists
        gain = self.gains.get(str(self.queue[self.index]), 0.0) if correction_enabled() else 0.0
        self.player.set_gain(gain_multiplier(gain))

    def _go_idle(self):
        self.player.stop()
        self.queue = []
        self.index = 0
        self.gains.clear()

    def _advance(self):
        if self.index + 1 < len(self.queue):
            self.index += 1
            self._load_current()
        else:
            self._go_idle()

    def status(self):
        if not self.player.loaded:
            return {"active": False, "queue": [], "index": 0, "paused": False,
                    "elapsed": 0, "duration": 0, "meter": "", "volume": self.player.volume}
        return {
            "active": True,
            "queue": [p.stem for p in self.queue],
            # clamp defensively: no client must ever see an out-of-range index
            "index": min(self.index, len(self.queue) - 1),
            "paused": self.player.paused,
            "elapsed": self.player.elapsed(),
            "duration": self.player.duration(),
            "meter": self.player.meter(),
            "volume": self.user_volume,
        }

    def handle(self, req):
        cmd = req.get("cmd")
        with self.lock:
            if cmd == "status":
                pass
            elif cmd == "add":
                paths = [Path(p) for p in req["paths"]]
                if paths and not self.player.loaded:
                    self.queue = paths
                    self.index = 0
                    self._load_current()
                else:
                    self.queue.extend(paths)
                self._measure_async(paths)
            elif cmd == "remove":
                # drop one queued track; removing the currently playing one
                # skips straight to the track that takes its slot
                idx = int(req["index"])
                if not (0 <= idx < len(self.queue)):
                    return {"error": "no such queue index"}
                popped = self.queue.pop(idx)
                self.gains.pop(str(popped), None)
                if idx < self.index:
                    self.index -= 1
                elif idx == self.index:
                    if not self.queue or idx == len(self.queue):
                        # removed the playing track and nothing follows it
                        # (queue drained, or it was the last element):
                        # nothing more to play — stop and clear, exactly like
                        # a natural end-of-queue
                        self._go_idle()
                    else:
                        self._load_current()  # play the new occupant of the slot
                elif self.index >= len(self.queue):
                    self.index = max(0, len(self.queue) - 1)
            elif cmd == "play":
                # playskip: the chosen track replaces the current one and starts now.
                path = Path(req["path"])
                if self.player.loaded:
                    self.queue[self.index] = path
                else:
                    self.queue = [path]
                    self.index = 0
                self._load_current()
                self._measure_async([path])
            elif cmd == "pause" and self.player.loaded:
                self.player.toggle_pause()
            elif cmd == "next" and self.player.loaded:
                self._advance()
            elif cmd == "prev" and self.player.loaded and self.index > 0:
                self.index -= 1
                self._load_current()
            elif cmd == "seek" and self.player.loaded:
                self.player.seek(float(req["seconds"]))
            elif cmd == "volume":
                self.user_volume = min(1.0, max(0.0, self.user_volume + float(req["delta"])))
                self.player.set_volume(self.user_volume)
                save_volume(self.user_volume)  # remembered for the next start
            elif cmd == "stop":
                self._go_idle()
                self.stopping.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return {"ok": True}
            else:
                return {"error": f"unknown or inapplicable command: {cmd}"}
            return self.status()

    def monitor(self):
        """Advances to the next track when the current one has finished."""
        while not self.stopping.is_set():
            time.sleep(0.2)
            with self.lock:
                done = self.player.loaded and self.player.finished()
            if done:
                time.sleep(0.5)  # let the device drain its buffer
                with self.lock:
                    if self.player.loaded and self.player.finished():
                        self._advance()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline()
        if not line:
            return
        try:
            response = self.server.state.handle(json.loads(line))
        except Exception as exc:  # a bad request must not take the player down
            response = {"error": str(exc)}
        self.wfile.write((json.dumps(response) + "\n").encode())


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True


def serve():
    state = Daemon()
    server = _Server((HOST, PORT), _Handler)
    server.state = state
    state.server = server
    threading.Thread(target=state.monitor, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        state.player.close()
