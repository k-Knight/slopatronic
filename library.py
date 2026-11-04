from rapidfuzz import fuzz, process

from .config import EXTENSIONS, LIBRARY


def library_files():
    LIBRARY.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in LIBRARY.iterdir() if p.suffix.lower() in EXTENSIONS)


def fuzzy_paths(query, paths):
    by_name = {p.stem: p for p in paths}
    hits = process.extract(query, list(by_name), scorer=fuzz.WRatio, limit=20, score_cutoff=50)
    return [by_name[name] for name, _score, _index in hits]
