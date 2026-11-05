"""Textual TUI for music_cli — vim style, clickable, with live filtering.

Vim flow:
  :  or i   open the command line (find / add / play / pause / …)
  /         open the command line as a live filter (library by name,
            search by title; on other tabs it jumps to library first)
  Esc       close the command line and go back to the lists
  j k g G   move the cursor, h l seek, - = volume, space pause, n b next/prev

The command line at the bottom is persistent: ': command / filter'.
Clicking works too: library row = add to queue, search row = download, and the
Now Playing tab has clickable transport buttons.
"""

import shlex
import threading
import time
from collections import deque

from rich.console import Console
from rich.table import Table
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.events import Click
from textual.widgets import (
    Button,
    DataTable,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)

from .client import ensure_daemon, request
from .config import BAR_CHARS
from .downloader import download, search
from .library import fuzzy_paths, library_files

console = Console()

SEEK_SECONDS = 5
VOLUME_STEP = 0.05
POLL_SECONDS = 0.05   # 20 status polls/s for smoother meter animation
IDLE_POLL_SECONDS = 0.25  # slow poll when idle — no wasted IPC
OFFLINE_POLL_SECONDS = 1.0  # when the daemon is dead, stop hammering the port
STATUS_TIMEOUT = 0.3   # quick-fail status socket timeout — must not block the UI
METER_DECAY = 0.75    # per-tick decay: bars fall gradually, hides stream-ahead lag
METER_DELAY_SECONDS = 1.0  # hold the meter back so it never runs ahead of the audio
VOL_BAR_CELLS = 20    # width of the visual volume block region inside vol_bar()
VOL_BAR_TEXT = 30     # total width of the vol_bar() string: "vol " + bar + "  " + "NNN%"
PROGRESS_WIDTH = 60   # characters in the song progress bar
BLOCKS = "▁▂▃▄▅▆▇█"   # 8 unicode block levels per cell row

# Nerd Font glyphs (Font Awesome patch). The TUI expects a Nerd Font in the
# terminal; on Windows Terminal pick e.g. "JetBrainsMono Nerd Font".
ICON_PLAY = "\uf04b"       # nf-fa-play
ICON_PAUSE = "\uf04c"     # nf-fa-pause
ICON_STOP = "\uf04d"      # nf-fa-stop
ICON_PREV = "\uf04a"      # nf-fa-step-backward
ICON_NEXT = "\uf04e"      # nf-fa-step-forward
ICON_MUSIC = "\uf001"     # nf-fa-music
ICON_SEARCH = "\uf002"    # nf-fa-search
ICON_QUEUE = "\uf0cb"     # nf-fa-list-ol
ICON_LIBRARY = "\uf02d"   # nf-fa-book
ICON_HELP = "\uf059"      # nf-fa-question_circle
ICON_VOL_UP = "\uf028"    # nf-fa-volume_up
ICON_VOL_DOWN = "\uf027"  # nf-fa-volume_down
ICON_IDLE = "\uf111"      # nf-fa-circle
ICON_OFFLINE = "\uf057"   # nf-fa-times_circle
ICON_SUCCESS = "\uf00c"   # nf-fa-check
ICON_ERROR = "\uf00d"     # nf-fa-times

HELP_TEXT = """\
[bold]Vim-style keys[/] — the command line at the bottom is always there;
press Esc any time to hand control back to the lists
  [b]:[/] or [b]i[/]      focus the command line (ex-style commands below)
  [b]/[/]          start a filter in the command line — everything after
                   the / narrows the list live (Library by name, Search
                   by title; on other tabs it jumps to the Library
                   first). Enter keeps the filter and highlights the
                   list, Esc clears the filter
  [b]j[/] / [b]k[/]      cursor down / up
  [b]g[/] / [b]G[/]      cursor to top / bottom of the list
  [b]\u2190[/] / [b]\u2192[/]      previous / next tab
  [b]h[/] / [b]l[/]      seek 5 s back / forward
  [b]-[/] / [b]=[/]      volume down / up
  [b]space[/]       pause or resume
  [b]n[/] / [b]b[/]       next / previous track
  [b]d[/]          download highlighted search result / remove the
                   highlighted track from the queue
  [b]a[/]          add highlighted library track to the queue
  [b]Enter[/]       add to queue (Library) or download (Search) the row
  [b]r[/]          refresh the library
  [b]s[/]          stop the player
  [b]q[/]          quit the TUI (music keeps playing)

[bold]Mouse[/]: click a Library row to add it to the queue, click a Search row
to download it, click the transport buttons on Now Playing,
click a tab header to switch.

[bold]Commands[/]
  [cyan]find <query>[/]     search YouTube; results land in the Search tab
  [cyan]add <query>[/]      add a matching library track to the end of the queue
  [cyan]play <query>[/]     play a matching library track now (playskip)
  [cyan]list[/] / [cyan]queue[/]        refresh the Library / Queue tab
  [cyan]pause[/]            pause or resume
  [cyan]next[/] / [cyan]prev[/]         skip forward / back
  [cyan]seek <seconds>[/]   jump to a position (negative goes back)
  [cyan]vol <0-100>[/]      set the volume directly
  [cyan]stop[/]             stop the background player and clear the queue
  [cyan]help[/]             show this help
"""


def fmt_time(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def progress_bar(elapsed, total):
    """One-row block progress bar (always rendered, even when idle)."""
    ratio = min(1.0, elapsed / total) if total else 0.0
    filled = round(ratio * PROGRESS_WIDTH)
    return "█" * filled + "░" * (PROGRESS_WIDTH - filled)


def meter_rows(meter):
    """Two stacked rows form one 2-cell-tall regular bar per band.
    The level is quantized to 16 steps: the bottom row holds the lower 8,
    the top row the upper 8 — no fading peaks, plain unicode blocks.
    Accepts per-band 0..1 ratios (new daemon) or the old single-row bar
    string (daemon still running the previous code)."""
    if isinstance(meter, str):
        ratios = [BAR_CHARS.index(c) / (len(BAR_CHARS) - 1)
                  for c in meter if c in BAR_CHARS]
    else:
        ratios = meter
    steps = [round(min(1.0, max(0.0, r)) * 16) for r in ratios]
    top = "".join(BLOCKS[n - 9] if n > 8 else " " for n in steps)
    bottom = "".join(BLOCKS[min(n, 8) - 1] if n else " " for n in steps)
    return top + "\n" + bottom


def queue_table(st):
    """Rich table of the queue for the plain terminal (`music_cli queue`)."""
    table = Table(title="Queue")
    table.add_column("", width=2)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Track")
    for i, name in enumerate(st["queue"]):
        table.add_row("▶" if i == st["index"] else "", str(i + 1), name)
    return table


class ClickableTable(DataTable):
    """DataTable whose rows respond to a single click: a library row is
    added to the queue, a search row downloads. The action always uses the
    row the mouse actually landed on — never the cursor, which can lag
    behind the mouse or already sit on another row."""

    BINDINGS = [b for b in DataTable.BINDINGS if b.key not in ("left", "right")]

    def _on_click(self, event: Click):
        # Replace DataTable's default click handling entirely: the stock one
        # only posts RowSelected when you re-click the highlighted row, which
        # made one click trigger two different actions.
        if self.id not in ("libraryview", "results") or not self.row_count:
            super()._on_click(event)
            return
        event.stop()
        row = event.style.meta.get("row")
        if row is None or not (0 <= row < self.row_count):
            return
        # move the cursor to the clicked row so keyboard and mouse agree
        self.cursor_coordinate = Coordinate(row, 0)
        self._scroll_cursor_into_view(animate=False)
        if self.id == "libraryview":
            self.app.action_add()       # click = add to queue (Enter still plays)
        elif self.id == "results":
            self.app.download_row()


class MusicTUI(App):
    TITLE = f"{ICON_MUSIC} music_cli"
    SUB_TITLE = "youtube finder + background player"
    AUTO_FOCUS = None  # start with the lists in control; ':' focuses the command line
    ENABLE_COMMAND_PALETTE = False  # no ctrl+p palette — the command line is the bar

    CSS = """
    Screen { background: $surface; }
    #command { dock: bottom; margin: 0 1 1 1; border: round $accent; }
    #command > .input--placeholder { color: $text-disabled; text-style: dim; }

    #np_pane { height: 1fr; align: center middle; padding: 0 2; }
    #np_box { width: 1fr; max-width: 96; height: auto; border: round magenta;
              padding: 1 3; background: $panel; }
    #np_status { width: 1fr; text-align: center; margin-bottom: 1; }
    #np_title { width: 1fr; text-align: center; text-style: bold; color: $text;
                margin-bottom: 1; }
    #np_bar { width: 1fr; text-align: center; color: dodgerblue; text-style: bold;
              margin-top: 1; }
    #np_time { width: 1fr; text-align: center; color: $text-muted; margin-top: 1; }
    #np_meter { width: 1fr; text-align: center; color: mediumpurple; margin-top: 1; }
    #np_vol { width: 1fr; text-align: center; color: green; margin-top: 1; }
    #np_buttons { height: auto; margin-top: 1; align: center middle; }
    #np_buttons Button { min-width: 10; margin: 0 1 0 0; }
    #np_buttons Button:last-of-type { margin-right: 0; }
    #statusline { height: 1; margin: 0 1; color: $text-muted; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        Binding("colon", "command_mode", "command", key_display=":"),
        Binding("i", "insert_mode", "command", show=False),
        Binding("slash", "filter_mode", "filter", key_display="/"),
        Binding("escape", "focus_table", "lists", show=False),
        Binding("left", "prev_tab", "prev tab", show=False),
        Binding("right", "next_tab", "next tab", show=False),
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("up", "cursor_up", "up", show=False),
        Binding("down", "cursor_down", "down", show=False),
        Binding("enter", "enter_action", "enter", show=False),
        Binding("g", "cursor_top", "top", show=False),
        Binding("G", "cursor_bottom", "bottom", show=False),
        Binding("space", "pause", "pause/resume", show=False),
        Binding("n", "next", "next", show=False),
        Binding("b", "prev", "prev", show=False),
        Binding("h", "seek_back", "seek -5s", show=False),
        Binding("l", "seek_fwd", "seek +5s", show=False),
        Binding("minus", "vol_down", "vol -", show=False),
        Binding("equals_sign", "vol_up", "vol +", show=False),  # '=' on Textual >= 0.6x
        Binding("equal", "vol_up", "", show=False),             # older key name
        Binding("plus", "vol_up", "", show=False),
        Binding("d", "download", "download", show=False),
        Binding("a", "add", "add to queue", show=False),
        Binding("r", "refresh", "refresh", show=False),
        Binding("s", "stop_player", "stop", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.search_results = []       # every result of the last find
        self.shown_results = []        # results visible after filtering
        self.library_paths = []        # every library track
        self.shown_library = []        # library rows visible after filtering
        self._meter_prev = None        # last band ratios, for decay smoothing
        self._meter_queue = deque()    # (timestamp, ratios) frames for the delay line
        self._was_offline = False      # poll slowly until the daemon answers again
        self._status_failures = 0      # transient timeouts must not read as offline
        self.shown_queue = []          # visible queue rows as (original index, name)
        self._last_queue_key = None    # skips rebuilds that would reset the cursor
        self._last_status = None       # last daemon status, for live queue filtering

    # ----- layout -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="now"):
            with TabPane(f"{ICON_PLAY} Now Playing", id="now"):
                with Vertical(id="np_pane"):
                    with Vertical(id="np_box"):
                        yield Static("", id="np_status")
                        yield Static("", id="np_title")
                        yield Static("", id="np_bar")
                        yield Static("", id="np_time")
                        yield Static("", id="np_meter")
                        yield Static("", id="np_vol")
                        with Horizontal(id="np_buttons"):
                            yield Button(f"{ICON_PREV} prev", id="btn-prev")
                            yield Button(f"{ICON_PAUSE} pause", id="btn-pause")
                            yield Button(f"{ICON_NEXT} next", id="btn-next")
                            yield Button(f"{ICON_STOP} stop", id="btn-stop")
                            yield Button(f"{ICON_VOL_DOWN} vol", id="btn-voldown")
                            yield Button(f"{ICON_VOL_UP} vol", id="btn-volup")
                yield Static("", id="statusline")
            with TabPane(f"{ICON_SEARCH} Search", id="search"):
                yield ClickableTable(id="results")
            with TabPane(f"{ICON_QUEUE} Queue", id="queue"):
                yield ClickableTable(id="queueview")
            with TabPane(f"{ICON_LIBRARY} Library", id="library"):
                yield ClickableTable(id="libraryview")
            with TabPane(f"{ICON_HELP} Help", id="help"):
                yield Static(HELP_TEXT)
        yield Input(placeholder=": command   / filter", id="command")

    def on_mount(self):
        results = self.query_one("#results", DataTable)
        results.add_columns("Title")
        queue = self.query_one("#queueview", DataTable)
        queue.add_columns("", "#", "Track")
        library = self.query_one("#libraryview", DataTable)
        library.add_columns("#", "Track")
        for table in (results, queue, library):
            table.cursor_type = "row"

        # poll fast while playing (smooth meter), slowly when idle/stopped
        self._poll = self.set_interval(POLL_SECONDS, self.refresh_status)
        self.refresh_status()
        self.refresh_library()

    # ----- command line open / close --------------------------------------

    def _open_input(self, prefix=""):
        """Focus the persistent command line with an optional prefix char,
        cursor placed after it and nothing selected."""
        inp = self.query_one("#command", Input)
        inp.value = ""
        inp.cursor_position = 0
        inp.focus()
        if prefix:
            try:
                inp.insert_text_at_cursor(prefix)
            except AttributeError:  # very old Textual fallback
                inp.value = prefix
                inp.cursor_position = len(prefix)
        # Textual's focus handler selects the whole content one refresh after
        # focus() — re-assert the cursor after that so typing appends instead
        # of replacing the prefix char.
        self.call_after_refresh(lambda: setattr(inp, "cursor_position", len(inp.value)))

    def _close_input(self):
        inp = self.query_one("#command", Input)
        inp.value = ""           # clears any active filter via Input.Changed
        self.focus_active_table()

    def action_command_mode(self):
        self._open_input(":")

    def action_insert_mode(self):
        self._open_input()

    def action_filter_mode(self):
        self._open_input("/")

    # ----- status polling -----------------------------------------------

    def smooth_meter(self, meter):
        """Decay-smooth the band ratios so bars fall gradually."""
        if isinstance(meter, str) or not isinstance(meter, list):
            return meter  # legacy daemon: no smoothing possible
        if self._meter_prev is None or len(self._meter_prev) != len(meter):
            self._meter_prev = list(meter)
            return meter
        smoothed = [max(cur, prev * METER_DECAY)
                    for cur, prev in zip(meter, self._meter_prev)]
        self._meter_prev = smoothed
        return smoothed

    def delayed_meter(self, meter):
        """Hold meter frames back METER_DELAY_SECONDS so the bars never run
        ahead of what the listener actually hears (miniaudio streams ahead
        of playback by its internal buffer)."""
        if isinstance(meter, str) or not isinstance(meter, list):
            return meter  # legacy daemon: delay not applicable
        now = time.monotonic()
        self._meter_queue.append((now, meter))
        cutoff = now - METER_DELAY_SECONDS
        shown = None
        while len(self._meter_queue) > 1 and self._meter_queue[0][0] <= cutoff:
            shown = self._meter_queue.popleft()[1]
        if shown is None:  # queue younger than the delay: show the oldest frame
            shown = self._meter_queue[0][1]
        return shown

    def refresh_status(self):
        # a dead daemon must never block the UI thread: quick-fail timeout.
        # The request is ALWAYS issued — the poll interval (1 s offline) is
        # what throttles a dead port, and this is what lets the TUI notice
        # the engine coming up later on its own.
        st = request("status", _timeout=STATUS_TIMEOUT)
        if st is None:
            self._status_failures += 1
            if self._status_failures < 3:  # keep the current view, retry fast
                self._poll.interval = IDLE_POLL_SECONDS
                return
            self._was_offline = True
            self._poll.interval = OFFLINE_POLL_SECONDS
        else:
            self._status_failures = 0
            self._was_offline = False
            self._poll.interval = (POLL_SECONDS if st.get("active")
                                   else IDLE_POLL_SECONDS)
        active = bool(st and st.get("active"))
        bar = self.query_one("#np_bar", Static)
        self._last_status = st

        if st is None:
            self.query_one("#np_status", Static).update(
                f"[red]{ICON_OFFLINE} offline[/] — the daemon starts on the first add/play command")
            self.query_one("#np_title", Static).update("[dim]nothing playing[/]")
            self.query_one("#np_time", Static).update("")
            self.query_one("#np_meter", Static).update("")
            self.query_one("#np_vol", Static).update("")
            bar.update(progress_bar(0, 0))
            self.query_one("#statusline", Static).update("[red]offline[/]")
            return

        if not st.get("active"):
            self.query_one("#np_status", Static).update(
                f"[dim]{ICON_IDLE} idle — press : and run find <query>[/]")
            self.query_one("#np_title", Static).update("[dim]nothing playing[/]")
            self.query_one("#np_time", Static).update("")
            self.query_one("#np_meter", Static).update("")
            self.query_one("#np_vol", Static).update(self.vol_bar(st.get("volume", 1.0)))
            bar.update(progress_bar(0, 0))
            self.query_one("#statusline", Static).update("idle")
        else:
            name = st["queue"][st["index"]]
            elapsed, total = st["elapsed"], st["duration"] or 1
            state = (f"[bold yellow]{ICON_PAUSE} paused[/]" if st["paused"]
                     else f"[bold green]{ICON_PLAY} playing[/]")
            self.query_one("#np_status", Static).update(state)
            self.query_one("#np_title", Static).update(name)
            bar.update(progress_bar(elapsed, total))
            self.query_one("#np_time", Static).update(
                f"{fmt_time(elapsed)} / {fmt_time(total)}   ·   track "
                f"{st['index'] + 1}/{len(st['queue'])}")
            self.query_one("#np_meter", Static).update(
                meter_rows(self.smooth_meter(self.delayed_meter(st["meter"]))))
            self.query_one("#np_vol", Static).update(self.vol_bar(st["volume"]))
            self.query_one("#statusline", Static).update(
                f"queue {st['index'] + 1}/{len(st['queue'])} · "
                + ("paused" if st["paused"] else "playing"))

        if self.query_one(TabbedContent).active == "queue":
            self.render_queue(st, self.filter_query())
        # the pause button mirrors the state: show what pressing it will do
        self.query_one("#btn-pause", Button).label = (
            f"{ICON_PLAY} resume" if st and st.get("active") and st.get("paused")
            else f"{ICON_PAUSE} pause")

    @staticmethod
    def vol_bar(volume):
        filled = round(volume * VOL_BAR_CELLS)
        return f"vol {'█' * filled}{'░' * (VOL_BAR_CELLS - filled)}  {round(volume * 100):3d}%"

    def render_queue(self, st, query=""):
        """Render the queue (optionally filtered). Rebuilding a DataTable
        resets its cursor, so identical content is never re-rendered —
        this is what keeps j/k and g/G usable while the song plays."""
        table = self.query_one("#queueview", DataTable)
        rows = [(i, name) for i, name in enumerate(st["queue"] if st else [])
                if query in name.lower()]
        self.shown_queue = rows
        key = (tuple(rows), bool(st and st.get("active")))
        if key == self._last_queue_key:
            return
        self._last_queue_key = key
        table.clear()
        if not st or not st.get("active"):
            return
        for i, name in rows:
            table.add_row(ICON_PLAY if i == st["index"] else "", str(i + 1), name)

    # ----- library + filter ------------------------------------------------

    def refresh_library(self):
        self.library_paths = library_files()
        self.render_library(self.filter_query())

    def filter_query(self):
        value = self.query_one("#command", Input).value
        return value[1:].strip().lower() if value.startswith("/") else ""

    def render_library(self, query=""):
        table = self.query_one("#libraryview", DataTable)
        table.clear()
        self.shown_library = [p for p in self.library_paths if query in p.stem.lower()]
        for i, path in enumerate(self.shown_library, 1):
            table.add_row(str(i), path.stem)

    def render_results(self, query=""):
        table = self.query_one("#results", DataTable)
        table.clear()
        needle = query.lower()
        self.shown_results = [e for e in self.search_results
                              if needle in e.get("title", "untitled").lower()]
        for entry in self.shown_results:
            table.add_row(entry.get("title", "untitled"))

    @on(Input.Changed, "#command")
    def filter_changed(self, event: Input.Changed):
        """A value starting with '/' live-filters the current list."""
        if not event.value.startswith("/"):
            # left filter mode (e.g. backspaced over the /) -> show everything again
            self.reset_filter()
            return
        query = event.value[1:].strip().lower()
        active = self.query_one(TabbedContent).active
        if active == "search":
            self.render_results(query)
        elif active == "queue":
            self.render_queue(self._last_status, query)
        else:
            if active != "library":
                self.query_one(TabbedContent).active = "library"
            self.render_library(query)

    def reset_filter(self):
        self.render_library()
        self.render_results()
        self.render_queue(self._last_status)

    # ----- search / download ---------------------------------------------

    def run_search(self, query):
        """Search YouTube in the background and fill the Search tab."""

        def work():
            try:
                results = search(query)
            except Exception as exc:
                self.call_from_thread(self.notify, f"search failed: {exc}", severity="error")
                return
            self.search_results = results
            self.call_from_thread(self.render_results, self.filter_query())
            self.call_from_thread(self.notify,
                                  f"{len(results)} results — j/k to move, d or Enter to download")

        threading.Thread(target=work, daemon=True).start()

    def download_row(self):
        if not self.shown_results:
            return
        entry = self.shown_results[self.query_one("#results", DataTable).cursor_row]
        title = entry.get("title", "untitled")
        self.notify(f"downloading {title}…")

        def work():
            try:
                path, outcome = download(entry)
                self.call_from_thread(self.notify, f"{ICON_SUCCESS} {title} ({outcome})")
                self.call_from_thread(self.refresh_library)
            except Exception as exc:
                self.call_from_thread(self.notify, f"{ICON_ERROR} {title}: {exc}", severity="error")

        threading.Thread(target=work, daemon=True).start()

    # ----- commands -------------------------------------------------------

    def _ensure_player(self):
        """Start the daemon if needed, telling the user what is happening and
        refreshing the Now Playing tab as soon as it is up."""
        if request("status", _timeout=STATUS_TIMEOUT) is not None:
            return
        self.notify("the player is not running — starting it…")
        ensure_daemon()
        self._was_offline = False
        self.refresh_status()

    def run_command(self, raw):
        raw = raw.strip()
        if raw.startswith(":"):  # ex-style prefix is optional sugar
            raw = raw[1:].strip()
        if not raw:
            return
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
        cmd, args = parts[0].lower(), " ".join(parts[1:])

        if cmd == "help":
            self.query_one(TabbedContent).active = "help"
        elif cmd == "find":
            if not args:
                self.notify("usage: find <query>", severity="warning")
            else:
                self.notify(f"searching YouTube for '{args}'…")
                self.run_search(args)
                self.query_one(TabbedContent).active = "search"
        elif cmd == "add":
            chosen = self.resolve_library(args)
            if chosen is not None:
                self._ensure_player()
                if request("add", paths=[str(chosen)]) is None:
                    self.notify("the player did not respond. Try again.", severity="error")
                else:
                    self.notify(f"queued: {chosen.stem}")
        elif cmd == "play":
            chosen = self.resolve_library(args)
            if chosen is not None:
                self._ensure_player()
                response = request("play", path=str(chosen))
                if response is None or "error" in response:
                    reason = response.get("error", "") if response else "no response"
                    self.notify(f"could not play {chosen.stem}: {reason}", severity="error")
                else:
                    self.notify(f"playing: {chosen.stem}")
        elif cmd == "list":
            self.refresh_library()
            self.query_one(TabbedContent).active = "library"
        elif cmd == "queue":
            self.refresh_status()
            self.query_one(TabbedContent).active = "queue"
        elif cmd == "pause":
            request("pause")
        elif cmd == "next":
            request("next")
        elif cmd == "prev":
            request("prev")
        elif cmd == "seek":
            try:
                request("seek", seconds=float(args))
            except ValueError:
                self.notify("usage: seek <seconds>  (negative goes back)", severity="warning")
        elif cmd in ("vol", "volume"):
            try:
                target = min(1.0, max(0.0, float(args.rstrip("%")) / 100))
                current = (request("status") or {}).get("volume", 1.0)
                request("volume", delta=target - current)
            except ValueError:
                self.notify("usage: vol <0-100>", severity="warning")
        elif cmd == "stop":
            self.action_stop_player()
        else:
            self.notify(f"unknown command '{cmd}' — type help", severity="error")

    def resolve_library(self, query):
        """Resolve a query to one library path, or None with a notice."""
        if not self.library_paths:
            self.refresh_library()
        files = self.library_paths
        if not files:
            self.notify("Library is empty. Use: find <query>", severity="warning")
            return None
        if query.isdigit() and 1 <= int(query) <= len(files):
            return files[int(query) - 1]
        candidates = fuzzy_paths(query, files) if query else files
        if not candidates:
            self.notify(f"No library match for '{query}'. Try: find {query}", severity="error")
            return None
        return candidates[0]  # best fuzzy hit; the Library tab offers full choice

    # ----- focus management -----------------------------------------------

    TABLE_BY_TAB = {"search": "#results", "queue": "#queueview", "library": "#libraryview"}

    def focus_active_table(self):
        """Move focus from the command line to the list of the active tab."""
        selector = self.TABLE_BY_TAB.get(self.query_one(TabbedContent).active)
        if selector:
            self.query_one(selector, DataTable).focus()
        else:
            # tabs without a list (Now Playing, Help) must not keep the
            # command bar in focus — vim keys only work when it lets go
            self.set_focus(None)

    def active_table(self):
        """The list of the active tab — navigation keys work on it no matter
        which widget currently holds focus (tab bar, nothing, …)."""
        selector = self.TABLE_BY_TAB.get(self.query_one(TabbedContent).active)
        return self.query_one(selector, DataTable) if selector else None

    def focused_table(self):
        return self.focused if isinstance(self.focused, DataTable) else None

    def action_focus_table(self):
        """Esc: leave the command line (clearing any filter) and refocus the list."""
        inp = self.query_one("#command", Input)
        if inp.value.startswith("/"):
            self._close_input()
        else:
            self.focus_active_table()

    def on_input_submitted(self, event: Input.Submitted):
        value = event.value
        inp = event.input
        if value.startswith("/"):
            # Enter in filter mode: keep the filter, hand control to the list.
            self.focus_active_table()
            return
        self.run_command(value)
        inp.value = ""
        # hand control to the results/lists so vim keys work right away
        self.focus_active_table()

    @on(Click, "#np_bar")
    def bar_clicked(self, event: Click):
        """Click anywhere on the progress bar to seek there."""
        st = request("status")
        if not st or not st.get("active"):
            return
        # the bar string is centered inside the widget — map the click column
        # back onto the bar's own coordinates
        start = max(0, (event.widget.outer_size[0] - PROGRESS_WIDTH) // 2)
        ratio = min(1.0, max(0.0, (event.x - start) / PROGRESS_WIDTH))
        target = ratio * (st["duration"] or 0)
        request("seek", seconds=target - st["elapsed"])  # seek is relative
        self.refresh_status()

    @on(Click, "#np_vol")
    def vol_clicked(self, event: Click):
        """Click on the volume bar's cells to set the level. Clicks outside
        the 20-cell block region are ignored — the visual bar is centered,
        so its cells start 4 chars (the 'vol ' label) into the string."""
        start = max(0, (event.widget.outer_size[0] - VOL_BAR_TEXT) // 2)
        bar_start = start + 4
        if not (bar_start <= event.x < bar_start + VOL_BAR_CELLS):
            return  # clicked the margins, not the bar
        target = (event.x - bar_start + 0.5) / VOL_BAR_CELLS
        st = request("status")
        if not st:
            return
        request("volume", delta=min(1.0, max(0.0, target)) - st.get("volume", 1.0))
        self.refresh_status()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "libraryview":
            self.action_add()  # Enter and click both add to the queue
        elif event.data_table.id == "results":
            self.download_row()

    # ----- clickable buttons ----------------------------------------------

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed):
        actions = {"btn-prev": self.action_prev, "btn-pause": self.action_pause,
                   "btn-next": self.action_next, "btn-stop": self.action_stop_player,
                   "btn-voldown": self.action_vol_down, "btn-volup": self.action_vol_up}
        handler = actions.get(event.button.id)
        if handler:
            handler()
            self.refresh_status()

    # ----- vim actions ------------------------------------------------------

    def play_track(self, path):
        self._ensure_player()
        response = request("play", path=str(path))
        if response is None or "error" in response:
            reason = response.get("error", "") if response else "no response"
            self.notify(f"could not play {path.stem}: {reason}", severity="error")
        else:
            self.notify(f"playing: {path.stem}")

    def action_download(self):
        active = self.query_one(TabbedContent).active
        if active == "search":
            self.download_row()
        elif active == "queue":
            self.remove_row()

    def action_enter_action(self):
        """Enter with no list focused: act on the highlighted row anyway."""
        active = self.query_one(TabbedContent).active
        if active == "library":
            self.action_add()
        elif active == "search":
            self.download_row()

    def remove_row(self):
        """Remove the highlighted track from the queue (d on the Queue tab)."""
        if not self.shown_queue:
            return
        idx = self.shown_queue[self.query_one("#queueview", DataTable).cursor_row][0]
        response = request("remove", index=idx)
        if response is None or "error" in response:
            reason = response.get("error", "no response") if response else "no response"
            self.notify(f"could not remove: {reason}", severity="error")
        else:
            self.notify("removed from queue")
            self._last_queue_key = None
            self.refresh_status()

    def action_add(self):
        """Add the highlighted library track to the queue (once)."""
        if self.query_one(TabbedContent).active != "library" or not self.shown_library:
            return
        path = self.shown_library[self.query_one("#libraryview", DataTable).cursor_row]
        self._ensure_player()
        st = request("status") or {}
        if path.stem in st.get("queue", []):
            self.notify(f"already queued: {path.stem}")
            return
        if request("add", paths=[str(path)]) is None:
            self.notify("the player did not respond. Try again.", severity="error")
        else:
            self.notify(f"queued: {path.stem}")

    def action_refresh(self):
        if self.query_one(TabbedContent).active == "library":
            self.refresh_library()

    def action_cursor_down(self):
        table = self.active_table()
        if table:
            table.action_cursor_down()

    def action_cursor_up(self):
        table = self.active_table()
        if table:
            table.action_cursor_up()

    def action_cursor_top(self):
        table = self.active_table()
        if table:
            for _ in range(table.row_count):
                table.action_cursor_up()

    def action_cursor_bottom(self):
        table = self.active_table()
        if table:
            for _ in range(table.row_count):
                table.action_cursor_down()

    # ----- tab switching --------------------------------------------------

    def action_prev_tab(self):
        self._switch_tab(-1)

    def action_next_tab(self):
        self._switch_tab(1)

    def _switch_tab(self, delta):
        """Move to the neighbouring tab and put its list in control."""
        tc = self.query_one(TabbedContent)
        pane_ids = [pane.id for pane in self.query(TabPane) if pane.id]
        if tc.active not in pane_ids:
            return
        new = (pane_ids.index(tc.active) + delta) % len(pane_ids)
        tc.active = pane_ids[new]
        self.focus_active_table()

    # ----- player transport -------------------------------------------------

    def action_pause(self):
        request("pause")

    def action_next(self):
        request("next")

    def action_prev(self):
        request("prev")

    def action_seek_back(self):
        request("seek", seconds=-SEEK_SECONDS)

    def action_seek_fwd(self):
        request("seek", seconds=SEEK_SECONDS)

    def action_vol_up(self):
        request("volume", delta=VOLUME_STEP)

    def action_vol_down(self):
        request("volume", delta=-VOLUME_STEP)

    def action_stop_player(self):
        if request("stop") is None:
            self.notify("the player is not running", severity="warning")
        else:
            self.notify("player stopped")


def run_ui():
    # start the engine in the background — the TUI comes up immediately and
    # picks the daemon up automatically on its next status poll
    def start_daemon():
        try:
            ensure_daemon()
        except SystemExit:  # engine failed to start: the TUI stays "offline"
            pass
    threading.Thread(target=start_daemon, daemon=True).start()
    MusicTUI().run()
