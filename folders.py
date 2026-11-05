"""Folders the library reads from.

The default (managed) folder is always included and always scanned
recursively. Extra folders live in folders.json inside the library (base)
folder — the config itself is stored in the base folder:

    [{"path": "/abs/dir", "recursive": false}, ...]

`recursive` (default false) makes the scan descend into subdirectories.
The Library tree's collapse/expand state is per-session, never saved.
Removing an extra folder only unregisters it — files on disk are never
touched.
"""

import json
from pathlib import Path

from .config import DIRS_FILE, LIBRARY


def load_entries():
    """Extra folder entries as [{"path", "recursive"}, ...]."""
    if not DIRS_FILE.exists():
        return []
    try:
        data = json.loads(DIRS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("path"):
            entries.append({"path": str(entry["path"]),
                            "recursive": bool(entry.get("recursive", False))})
    return entries


def save_entries(entries):
    LIBRARY.mkdir(parents=True, exist_ok=True)
    DIRS_FILE.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def _norm(path):
    return Path(path).expanduser().resolve()


def folders():
    """All library folders as dicts, managed one first.
    The managed folder is always recursive; it starts expanded in the
    tree (per-session state, never saved)."""
    return [{"path": str(LIBRARY), "recursive": True, "managed": True}] + [
        {"path": e["path"], "recursive": e["recursive"], "managed": False}
        for e in load_entries()
    ]


def is_managed(path):
    """True if the file lives inside the default library folder."""
    try:
        return Path(path).resolve().is_relative_to(LIBRARY.resolve())
    except (OSError, ValueError):
        return False


def add_folder(path, recursive=False):
    """Register an extra folder. Returns the resolved path."""
    folder = _norm(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"not a directory: {folder}")
    if folder == LIBRARY.resolve():
        raise ValueError("the default library folder is always included")
    entries = [e for e in load_entries() if _norm(e["path"]) != folder]
    entries.append({"path": str(folder), "recursive": bool(recursive)})
    save_entries(entries)
    return folder


def remove_folder(path):
    """Unregister an extra folder (files are kept). Returns the removed path or None."""
    folder = _norm(path)
    entries = [e for e in load_entries() if _norm(e["path"]) != folder]
    if len(entries) == len(load_entries()):
        return None
    save_entries(entries)
    return folder


def set_recursive(path, value):
    """Set the recursive flag of an extra folder. Returns the new value."""
    folder = _norm(path)
    value = bool(value)
    entries = load_entries()
    for entry in entries:
        if _norm(entry["path"]) == folder:
            entry["recursive"] = value
            save_entries(entries)
            return value
    raise KeyError(f"not a registered extra folder: {folder}")
    folder = _norm(path)
    value = bool(value)
    entries = load_entries()
    for entry in entries:
        if _norm(entry["path"]) == folder:
            entry[flag] = value
            save_entries(entries)
            return value
    raise KeyError(f"not a registered extra folder: {folder}")
