import json
import socket
import subprocess
import sys
import time

from .config import HOST, PACKAGE_PARENT, PORT


def request(cmd, **kwargs):
    """Send one command to the background player. Returns None if it is not running."""
    payload = json.dumps({"cmd": cmd, **kwargs}) + "\n"
    try:
        with socket.create_connection((HOST, PORT), timeout=2) as sock:
            sock.sendall(payload.encode())
            line = sock.makefile("rb").readline()
        return json.loads(line)
    except (OSError, ValueError):
        return None


def ensure_daemon():
    """Start the background player if it is not already running."""
    if request("status") is not None:
        return
    flags = (
        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    )
    subprocess.Popen(
        [sys.executable, "-c", "from music_cli.daemon import serve; serve()"],
        cwd=PACKAGE_PARENT,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if request("status") is not None:
            return
        time.sleep(0.1)
    raise SystemExit("Could not start the background player.")
