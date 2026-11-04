import time

import msvcrt
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .client import request

console = Console()
SEEK_SECONDS = 5
VOLUME_STEP = 0.05


def fmt_time(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def render(st):
    if not st["active"]:
        body = Text("■ idle: nothing playing. Start with play or add.", style="dim")
        return Panel(body, title="♪ music_cli", border_style="magenta", expand=False)

    name = st["queue"][st["index"]]
    elapsed, total = st["elapsed"], st["duration"]
    width = 36
    filled = min(width, int(width * elapsed / total)) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    up = st["queue"][st["index"] + 1:st["index"] + 4]

    body = Table.grid(padding=(0, 1))
    body.add_row(
        Text("⏸ paused", style="bold yellow") if st["paused"]
        else Text("▶ playing", style="bold green")
    )
    body.add_row(Text(name, style="bold"))
    body.add_row(Text(f"{fmt_time(elapsed)}  {bar}  {fmt_time(total)}", style="cyan"))
    body.add_row(Text(st["meter"], style="magenta"))
    volume = round(st["volume"] * 100)
    vol_filled = round(st["volume"] * 20)
    body.add_row(Text(f"vol   {'█' * vol_filled}{'░' * (20 - vol_filled)}  {volume:3d}%", style="green"))
    body.add_row(Text(f"up next: {'  ·  '.join(up) if up else '—'}", style="dim"))
    body.add_row(Text(
        f"track {st['index'] + 1}/{len(st['queue'])}   "
        "p pause · n next · b prev · ←/→ seek 5s · ↑/↓ volume · q close (music keeps playing)",
        style="dim",
    ))
    return Panel(body, title="♪ music_cli", border_style="magenta", expand=False)


def queue_table(st):
    table = Table(title="Queue")
    table.add_column("", width=2)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Track")
    for i, name in enumerate(st["queue"]):
        marker = "▶" if i == st["index"] else ""
        table.add_row(marker, str(i + 1), name)
    return table


def run_ui():
    first = request("status")
    if first is None:
        console.print("[red]The background player is not running.[/]")
        return
    try:
        with Live(render(first), console=console, auto_refresh=False) as live:
            while True:
                st = request("status")
                if st is None:
                    console.print("[red]The background player stopped.[/]")
                    break
                live.update(render(st), refresh=True)
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key in ("\x00", "\xe0"):
                        arrow = msvcrt.getwch()
                        if arrow == "K":
                            request("seek", seconds=-SEEK_SECONDS)
                        elif arrow == "M":
                            request("seek", seconds=SEEK_SECONDS)
                        elif arrow == "H":
                            request("volume", delta=VOLUME_STEP)
                        elif arrow == "P":
                            request("volume", delta=-VOLUME_STEP)
                    else:
                        key = key.lower()
                        if key == "p":
                            request("pause")
                        elif key == "n":
                            request("next")
                        elif key == "b":
                            request("prev")
                        elif key in ("+", "="):
                            request("volume", delta=VOLUME_STEP)
                        elif key == "-":
                            request("volume", delta=-VOLUME_STEP)
                        elif key in ("q", "\x03", "\x1b"):
                            break
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
