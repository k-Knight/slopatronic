import os
from pathlib import Path

from rapidfuzz import fuzz, process

from .config import EXTENSIONS, LIBRARY
from .folders import load_entries


def _files_in(root, recursive):
    try:
        paths = root.rglob("*") if recursive else root.iterdir()
        return [p for p in paths if p.is_file() and p.suffix.lower() in EXTENSIONS]
    except OSError:  # unreadable folder: skip it, not the whole library
        return []


def library_files():
    """All playable files: the managed folder (always recursive, in case the
    user arranges files into subfolders) plus every extra folder."""
    LIBRARY.mkdir(parents=True, exist_ok=True)
    files = list(_files_in(LIBRARY, recursive=True))
    seen_roots = {LIBRARY.resolve()}
    for entry in load_entries():
        root = Path(entry["path"])
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if not root.is_dir() or resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        files.extend(_files_in(root, bool(entry.get("recursive"))))
    unique = {}
    for p in files:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        unique.setdefault(str(resolved), resolved)  # overlaps count once
    return sorted(unique.values(), key=str)


def _under(path, root):
    try:
        return path.resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def library_tree(files=None):
    """Full tree rows for the Library screen: (kind, depth, name, path).

    kind is "dir" or "file"; depth 0 is a folder root (the managed folder
    first, then every extra folder). Subdirectory rows appear only under
    folders with recursive lookup on. Collapse/expand is the caller's job:
    the UI keeps a per-session set of expanded directories.
    """
    if files is None:
        files = library_files()
    rows = []
    seen_dirs = set()
    seen_files = set()
    roots = []
    for root, recursive in [(LIBRARY, True)] + [
            (Path(e["path"]), bool(e.get("recursive"))) for e in load_entries()]:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if str(resolved) not in seen_dirs and resolved.is_dir():
            seen_dirs.add(str(resolved))
            roots.append((resolved, recursive))
    for resolved, _recursive in roots:
        rows.append(("dir", 0, resolved.name or str(resolved), resolved))
        prefix = str(resolved) + os.sep
        # files are already resolved by library_files(): plain string prefix
        sub = [f for f in files if str(f).startswith(prefix) and str(f) not in seen_files]
        for path in sorted(sub, key=str):
            seen_files.add(str(path))
            rel = path.relative_to(resolved)
            for i, part in enumerate(rel.parts[:-1]):
                d = resolved.joinpath(*rel.parts[:i + 1])
                if str(d) not in seen_dirs:
                    seen_dirs.add(str(d))
                    rows.append(("dir", 1 + i, part, d))
            rows.append(("file", len(rel.parts), path.stem, path))
    return rows


def dir_file_counts(files):
    """Map of directory path (str) -> number of files anywhere beneath it.
    One pass over the files: each file counts for every ancestor."""
    counts = {}
    for f in files:
        for parent in f.parents:
            counts[str(parent)] = counts.get(str(parent), 0) + 1
    return counts


def fuzzy_paths(query, paths):
    by_name = {p.stem: p for p in paths}
    hits = process.extract(query, list(by_name), scorer=fuzz.WRatio, limit=20, score_cutoff=50)
    return [by_name[name] for name, _score, _index in hits]
