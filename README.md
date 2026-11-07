# slopatronic

Terminal music player that finds tracks on YouTube, Bandcamp and Mixcloud, downloads the audio, and keeps playing in the background while you do other things.

## Install

Requires Python 3.10+ (ffmpeg is bundled via `imageio-ffmpeg`).

```sh
pip install -r requirements.txt
```

On Windows, use `slopatronic.cmd` from the parent directory (or set `PYTHONPATH` yourself). On Linux/macOS, run from the parent directory:

```sh
python -m slopatronic
```

## Usage

```sh
slopatronic find <query>    # search all sources, tick what you want, download audio only
slopatronic list            # show downloaded tracks
slopatronic add <query>     # fuzzy-match a track and append it to the queue
slopatronic play <query>    # play now, replacing whatever is on
slopatronic pause / next / prev / stop
slopatronic queue           # show the queue with a live progress view
slopatronic ui              # full TUI
```

`<query>` can be a library index number, a fuzzy match, or a search string.

### TUI

`slopatronic ui` opens a Textual app with three tabs: search results, library tree, and now playing (queue, controls, volume, a live level meter). There's a command mode and a filter mode; tables are clickable.

## How it works

- A detached daemon owns the queue and decodes tracks with ffmpeg to raw PCM, streamed through miniaudio. CLI commands talk to it over a local socket (port 47651), so music keeps playing after the terminal closes.
- Downloads go to `~/.slopatronic` by default. No re-encoding — the best audio stream is saved as-is.
- You can register extra music folders in `~/.slopatronic/config.json` (with optional recursive scanning); your own files are never touched.
- Tracks are loudness-normalized on the fly: each queued track is measured with ebur128, and a gain is applied to hit the target in config (default -12 LUFS), capped so loud sections never exceed a -6 LUFS ceiling. Gains live in memory only.

## Config

`~/.slopatronic/config.json`, created on first run:

```json
{
  "folders": [{"path": "/abs/dir", "recursive": false}],
  "volume": 1.0,
  "loudness_target": -12.0,
  "loudness_correction": true
}
```
