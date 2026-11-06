"""Folders the library reads from.

The default (managed) folder is always included and always scanned
recursively. Extra folders and the remembered player volume live in the
single config.json inside ~/.slopatronic — always generated on first use:

    {"folders": [{"path": "/abs/dir", "recursive": false}, ...],
     "volume": 1.0}

`recursive` (default false) makes the scan descend into subdirectories.
The Library tree's collapse/expand state is per-session, never saved.
Removing an extra folder only unregisters it — files on disk are never
touched.
"""

from pathlib import Path

from .config import LIBRARY, load_config, save_config


def load_entries():
    """Extra folder entries as [{"path", "recursive"}, ...]."""
    entries = load_config().get("folders", [])
    out = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path"):
            out.append({"path": str(entry["path"]),
                        "recursive": bool(entry.get("recursive", False))})
    return out


def save_entries(entries):
    data = load_config()
    data["folders"] = entries
    save_config(data)


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
