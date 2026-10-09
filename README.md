# spotify-sync

A thin wrapper around [`spotdl sync`](https://github.com/spotDL/spotify-downloader) that mirrors
your Spotify playlists into **one folder of MP3s per playlist**. It's built for headphones that
play plain folders, like the **Shokz OpenSwim Pro**.

- One folder per playlist (`<music_dir>/<playlist name>/Artist - Title.mp3`)
- MP3 only, no M3U files, no Liked Songs
- Runs only when you start it (no daemon, no scheduler)
- Optional pruning: with `"prune": true`, files for tracks you remove on Spotify are deleted locally
- Renaming a playlist in `config.json` renames its folder instead of re-downloading it
- Folders of playlists you remove from `config.json` are offered for deletion (asks y/N)
- A lockfile (`.sync.lock`) stops two runs from overlapping

## Prerequisites

| Tool | Install (macOS) |
|---|---|
| Python ≥ 3.10 | `brew install python` |
| [pipx](https://pipx.pypa.io) | `brew install pipx` |
| [spotdl](https://github.com/spotDL/spotify-downloader) 4.x | `pipx install spotdl` |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | installed with spotdl; keep it current with `pipx upgrade spotdl` |
| [ffmpeg](https://ffmpeg.org) (includes `ffprobe`) | `brew install ffmpeg` or `spotdl --download-ffmpeg` |

## Setup

### 1. Spotify developer app credentials

spotdl talks to the Spotify Web API, and works best with your own app credentials.

1. Go to <https://developer.spotify.com/dashboard> and **Create app**.
2. Set any name, add redirect URI `http://127.0.0.1:9900/`, and select **Web API**.
3. Copy the **Client ID** and **Client secret**.
4. Run `spotdl --generate-config` once, then put them in **spotdl's** config,
   `~/.spotdl/config.json`:

   ```json
   "client_id": "…",
   "client_secret": "…",
   ```

Credentials never go in this repo's `config.json`. `sync.py` refuses to run if they do.

### 2. YouTube Music cookies (optional, recommended)

spotdl finds audio on YouTube Music. A cookies file from a logged-in browser session helps
avoid rate limits and age-gating, and lets YouTube Music Premium accounts get higher-bitrate
sources.

1. Install a "cookies.txt" exporter extension that writes **Netscape format**.
2. Sign in at <https://music.youtube.com>, then export cookies for that site.
3. Save the file outside the repo, e.g. `~/.config/spotdl/cookies.txt`, and run `chmod 600` on it.

Treat this file like a password, because it's a live login session.

### 3. Configure playlists

```sh
cp config.example.json config.json
```

```json
{
  "music_dir": "~/Music/OpenSwim",
  "state_dir": "state",
  "cookie_file": "~/.config/spotdl/cookies.txt",
  "bitrate": "192k",
  "threads": 4,
  "audio": ["youtube-music"],
  "prune": false,
  "playlists": [
    { "name": "Swim Mix", "url": "https://open.spotify.com/playlist/…" },
    "https://open.spotify.com/playlist/…"
  ]
}
```

- `prune`: when `true`, a track you remove from the Spotify playlist also has its MP3
  deleted from the playlist folder. spotdl only deletes files it recorded in that playlist's
  state file, never other files in the folder. Defaults to `false` (add new tracks only).
- `name` is optional. Without it, the folder takes the Spotify playlist name.
- `state_dir` holds one `<playlist-id>.spotdl` file per playlist, which spotdl uses to know
  what was already downloaded. It also holds `folders.json`, which records each playlist's
  folder. Relative paths resolve next to `config.json`.
- Get a playlist URL in Spotify with **⋯ → Share → Copy link to playlist**.

## Usage

```sh
python3 sync.py --dry-run              # show the spotdl commands, change nothing
python3 sync.py                        # sync every playlist (prunes only if "prune": true)
python3 sync.py --only "Swim Mix"      # one playlist, by name or playlist ID
python3 sync.py --prune                # delete removed tracks this run, whatever the config says
python3 sync.py --keep                 # never delete this run, whatever the config says
python3 sync.py --yes                  # delete leftover folders without the y/N question
python3 sync.py --config other.json    # use a different config
```

On the first run for a playlist, the wrapper calls `spotdl sync <url> --save-file
state/<id>.spotdl`. Later runs call `spotdl sync state/<id>.spotdl`. The filename template
must stay the same between runs: spotdl works out which files are which from it.

### Renamed and removed playlists

- **Renamed in `config.json`** (adding, changing or removing `name`): before syncing, the
  playlist's existing folder is renamed to match, so nothing is downloaded again.
- **Removed from `config.json`**: after syncing, its folder is listed with its file count and
  size, then you're asked `Delete these? [y/N]`. Answering `y` deletes the folder and its
  state files. `--yes` skips the question. Without a terminal (cron, scripts) and without
  `--yes`, nothing is deleted.
- With `"prune": false` or `--keep`, leftover folders are only reported, never deleted.
- `--dry-run` shows planned renames and leftover folders.
- Only folders directly inside `music_dir` that this tool recorded are ever deleted. A
  folder another configured playlist still uses is never touched.

When you delete or unfollow a playlist on Spotify, remove it from `config.json` too. Until
you do, spotdl reports an error for it on every run.

## Copying to the OpenSwim Pro

The OpenSwim Pro shows up as a USB drive when it's connected with its magnetic cable.

```sh
# Copies new or changed files; does not delete anything on the headphones.
rsync -rtv --modify-window=2 --exclude '.*' ~/Music/OpenSwim/ "/Volumes/OpenSwim Pro/"
```

- Replace `/Volumes/OpenSwim Pro` with the volume name Finder shows.
- Add `--delete` only if you want the headphones to mirror the folder exactly. That
  removes files on the device.
- In MP3 mode, the headphones switch between folders with the **+/− long-press** gesture
  (see the Shokz manual for your firmware).
- Eject before unplugging: `diskutil eject "/Volumes/OpenSwim Pro"`.
- macOS writes `._*` files to FAT drives. Run `dot_clean "/Volumes/OpenSwim Pro"` to remove them.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest -q
```

CI (GitHub Actions) runs ruff and pytest on every push.

## Known limitations

- Audio comes from YouTube/YouTube Music, not from Spotify. Wrong matches happen (live
  versions, covers, extended mixes), and quality depends on the source (usually ~128–160 kbps
  AAC/Opus re-encoded to MP3). A higher `bitrate` setting can't add back quality the source
  doesn't have.
- Some tracks have no match on YouTube Music ("LookupError: No results found"). They're
  skipped and listed in `state/<playlist-id>.errors.txt`, and `sync.py` exits with status 1.
  Re-running retries them. Adding `"youtube"` after `"youtube-music"` in `audio` gives a
  fallback source, but its matches are less reliable.
- spotdl has no "no M3U" flag. If `m3u` is set in `~/.spotdl/config.json`, spotdl will still
  write M3U files, so leave it `null` there.
- Liked Songs and non-playlist URLs (albums, artists) are rejected on purpose.
- A playlist renamed **on Spotify** (with no `name` set in config) is noticed only after
  spotdl has downloaded it into a new folder. The old folder is then offered for deletion as a
  leftover. Set a `name` in config to keep a stable folder.
- Changing the filename template between runs makes spotdl re-download everything.
- Spotify API changes and YouTube anti-bot measures break spotdl and yt-dlp from time to time.
  `pipx upgrade spotdl` (and refreshing cookies) usually fixes it.
- The OpenSwim Pro sorts tracks by filename or copy order, not playlist order.

## Disclaimer

This project is a convenience wrapper around third-party tools and is not affiliated with
Spotify, YouTube, Google or Shokz. **You are responsible for making sure your use complies with
Spotify's and YouTube's Terms of Service and with copyright law where you live.** Only download
content you have the right to download. The software is provided "as is" under the MIT
License, without warranty of any kind.

## License

[MIT](LICENSE)
