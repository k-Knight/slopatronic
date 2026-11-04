import argparse
import time

import questionary
from rich.table import Table

from .client import ensure_daemon, request
from .downloader import download, search
from .library import fuzzy_paths, library_files
from .ui import console, queue_table, run_ui


def pick_youtube(query):
    """Search YouTube and let the user tick results. Returns the chosen entries."""
    with console.status(f"Searching YouTube for '{query}'..."):
        results = search(query)
    if not results:
        console.print("[red]No results.[/]")
        return []
    picked = questionary.checkbox(
        "Tick tracks with space, then Enter to download:",
        choices=[questionary.Choice(e.get("title", "untitled"), value=i) for i, e in enumerate(results)],
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()
    if picked is None:
        return []
    if not picked:
        console.print("[dim]Nothing ticked. Press space on a track to tick it, then Enter.[/]")
    return [results[i] for i in picked]


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
    """Resolve a query to one library track: a number, a fuzzy match, or a pick from several."""
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
    if len(candidates) == 1:
        return candidates[0]
    return questionary.select(
        "Pick a track:",
        choices=[questionary.Choice(p.stem, value=p) for p in candidates],
        use_search_filter=True,
        use_jk_keys=False,
    ).ask()


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
    files = library_files()
    if not files:
        console.print("[dim]No music yet. Use find first.[/]")
        return
    table = Table(title=str(files[0].parent))
    table.add_column("#", justify="right", style="dim")
    table.add_column("Track")
    for i, path in enumerate(files, 1):
        table.add_row(str(i), path.stem)
    console.print(table)


def cmd_queue(_args):
    st = request("status")
    if st is None or not st["active"]:
        console.print("[dim]Nothing playing.[/]")
        return
    console.print(queue_table(st))


def cmd_ui(_args):
    ensure_daemon()
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
        "ui": (cmd_ui, "show the player panel with keyboard controls", None),
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
