import argparse
import time

import questionary


from .client import ensure_daemon, request
from .downloader import download, search_all
from .library import fuzzy_paths, library_files, library_tree
from .ui import console, queue_table, run_ui


def pick_youtube(query):
    """Search every source (shared search_all), print results grouped by
    source with per-source failure reasons, then let the user tick tracks
    to download (the original checkbox picker)."""
    flat = []  # (source label, entry) across all sources, for the picker
    for source, entries, error in search_all(query):
        if error:
            console.print(f"[red]{source}: search failed — "
                          f"{type(error).__name__}: {error}[/]")
            continue
        console.print(f"[green]{source}: {len(entries)} results:[/]")
        for i, entry in enumerate(entries, 1):
            title = entry.get("title", "untitled")
            url = entry.get("url") or entry.get("id") or ""
            console.print(f"  {i:2d}. {title}  [dim]{url}[/]")
            flat.append((source, entry))
    if not flat:
        console.print("[red]No results.[/]")
        return []
    picked = questionary.checkbox(
        "Tick tracks with space, then Enter to download:",
        choices=[questionary.Choice(f"[{source}] {e.get('title', 'untitled')}",
                                    value=e)
                 for source, e in flat],
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()
    if picked is None:
        return []
    if not picked:
        console.print("[dim]Nothing ticked. Press space on a track to tick it, then Enter.[/]")
    return picked


def fetch(entry):
    """Download one search result into the library. Returns the path, or None on failure."""
    title = entry.get("title", "untitled")
    try:
        with console.status(f"Downloading {title}..."):
            path, outcome = download(entry)
    except Exception as exc:  # one failed track should not stop the rest
        console.print(f"  [red]✘[/] {title}: {exc}")
        return None
    console.print(f"  [green]✔[/] {title} [dim]({outcome})[/]")
    return path


def pick_library_track(query):
    """Resolve a query to one library track: a number, a fuzzy match, or the best hit."""
    files = library_files()
    if not files:
        console.print("[dim]Library is empty. Use find first.[/]")
        return None
    if query.isdigit() and 1 <= int(query) <= len(files):
        return files[int(query) - 1]
    candidates = fuzzy_paths(query, files) if query else files
    if not candidates:
        console.print(f"[red]No library match for '{query}'.[/] Try: find {query}")
        return None
    return candidates[0]  # best fuzzy hit


def cmd_find(args):
    for entry in pick_youtube(" ".join(args.query)):
        fetch(entry)


def cmd_add(args):
    chosen = pick_library_track(" ".join(args.query).strip())
    if chosen is None:
        return
    ensure_daemon()
    if request("add", paths=[str(chosen)]) is None:
        console.print("[red]The player did not respond. Try again.[/]")
        return
    console.print(f"Queued: {chosen.stem}")


def cmd_play(args):
    chosen = pick_library_track(" ".join(args.query).strip())
    if chosen is None:
        return
    ensure_daemon()
    response = request("play", path=str(chosen))
    if response is None or "error" in response:
        reason = response.get("error", "") if response else "no response"
        console.print(f"[red]Could not play {chosen.stem}:[/] {reason}")
        return
    console.print(f"Playing: {chosen.stem}")


def cmd_list(_args):
    # same tree the TUI Library tab shows: folder roots, subfolders, tracks
    rows = library_tree()
    files = [p for kind, _d, _n, p in rows if kind == "file"]
    if not files:
        console.print("[dim]No music yet. Use find first.[/]")
        return
    numbers = iter(range(1, len(files) + 1))
    for kind, depth, name, path in rows:
        if kind == "dir":
            console.print(f"{'  ' * depth}[bold]{name}/[/]")
        else:
            console.print(f"{'  ' * depth}[dim]{next(numbers):>3d}.[/] {name}")


def cmd_queue(_args):
    st = request("status")
    if st is None or not st["active"]:
        console.print("[dim]Nothing playing.[/]")
        return
    console.print(queue_table(st))


def cmd_ui(_args):
    run_ui()


def control(cmd):
    def run(_args):
        if request(cmd) is None:
            console.print("[dim]Nothing playing.[/]")
    return run


def cmd_stop(_args):
    if request("stop") is None:
        console.print("[dim]The player is not running.[/]")
        return
    deadline = time.time() + 3  # wait for the port to close so the next command starts a fresh player
    while time.time() < deadline and request("status") is not None:
        time.sleep(0.1)
    console.print("Player stopped.")


def main():
    parser = argparse.ArgumentParser(description="Find music on YouTube (audio only) and play it in the background.")
    sub = parser.add_subparsers(dest="command", required=True)

    commands = {
        "find": (cmd_find, "search YouTube and download audio only", "+"),
        "add": (cmd_add, "add a library track to the end of the queue", "*"),
        "play": (cmd_play, "play a library track now, replacing the current one", "*"),
        "list": (cmd_list, "list downloaded tracks", None),
        "queue": (cmd_queue, "show the current queue", None),
        "ui": (cmd_ui, "full TUI: search, download, queue and player controls", None),
        "pause": (control("pause"), "pause or resume", None),
        "next": (control("next"), "skip to the next track", None),
        "prev": (control("prev"), "go back to the previous track", None),
        "stop": (cmd_stop, "stop the background player", None),
    }
    for name, (func, help_text, nargs) in commands.items():
        p = sub.add_parser(name, help=help_text)
        if nargs:
            p.add_argument("query", nargs=nargs)
        p.set_defaults(func=func)

    args = parser.parse_args()
    args.func(args)
