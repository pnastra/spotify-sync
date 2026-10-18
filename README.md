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
  "official_only": false,
  "playlists": [
    { "name": "Swim Mix", "url": "https://open.spotify.com/playlist/…" },
    "https://open.spotify.com/playlist/…"
  ]
}
```

- `prune`: when `true`, a track you remove from the Spotify playlist also has its MP3
  deleted from the playlist folder. spotdl only deletes files it recorded in that playlist's
  state file, never other files in the folder. Defaults to `false` (add new tracks only).
- `official_only`: when `true`, only official YouTube Music *songs* are used. Live concerts, music
  videos and fan uploads are never downloaded; a track with no official song is skipped and
  listed in `state/<playlist-id>.errors.txt`. Defaults to `false` (videos are a fallback).
- `english_search` (default `true`): see [Why a launcher?](#why-a-launcher-spotdl_enpy).
- `log_dir` (default `logs`): where run logs are written.
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
python3 sync.py --official-only        # this run: official songs only, whatever the config says
python3 sync.py --any-source           # this run: allow videos as a fallback
python3 sync.py --no-log               # do not write a log file this run
python3 sync.py --config other.json    # use a different config
```

On the first run for a playlist, the wrapper calls `spotdl sync <url> --save-file
state/<id>.spotdl`. Later runs call `spotdl sync state/<id>.spotdl`. The filename template
must stay the same between runs: spotdl works out which files are which from it.

### Logs and the end-of-run summary

Every run writes `logs/sync-<date>_<time>.log` (gitignored) and finishes with a summary:

```
== Summary ==
10 official songs, 2 from videos, 41 already present, 1 not found
  from videos: Artist - Title; Artist - Other Title
```

"Official songs" came from a `music.youtube.com` link, "from videos" from a `www.youtube.com`
link (live recordings, fan uploads, music videos), and the titles of video-sourced tracks are
listed so you can check them. To see where each track came from later:
`grep -E "Downloaded|LookupError" logs/*.log`.

### Why a launcher (`spotdl_en.py`)?

spotdl 4.5.2 creates its YouTube Music search client with `language="de"`. In that mode the
"Songs" search returns nothing, so spotdl never finds official songs and always falls back to
videos. `spotdl_en.py` switches that one setting to English and then runs spotdl unchanged, using
the Python environment of your pipx-installed spotdl. Set `"english_search": false` to run plain
`spotdl` instead (for example once the bug is fixed upstream).

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

### Keeping `config.json` in step with your Spotify account

`update_config.py` rewrites the `playlists` list in `config.json` with the playlists **you
created**. Playlists you only follow are left out, and so are any named in the optional
`"exclude_playlists"` setting (default: `My Shazam Tracks`, `Liked Songs`). Liked Songs is not a
playlist in Spotify's API, so it is never included.

```sh
python3 update_config.py --dry-run     # show what would be added/removed, change nothing
python3 update_config.py               # show changes, ask y/N, write config.json
python3 update_config.py --no-browser  # print the login link instead of opening a browser
python3 sync.py --update               # refresh the list, then sync
```

- It reuses the credentials in `~/.spotdl/config.json` and spotdl's Python environment (no extra
  installs). The first run needs a one-time browser login with read-only access to your private
  playlists. Add `http://127.0.0.1:9900/` to your Spotify app's **Redirect URIs** first.
- Only the `playlists` key is rewritten; other settings and any `name` you set are kept.
  A backup is saved as `config.json.bak`.
- It refuses to write if Spotify returns nothing, or fewer than half your current playlists.
- It never touches music files. Folders of removed playlists are offered for deletion by
  `sync.py` (see above).
- "Created by you" means Spotify lists you as the owner. Playlists made by a collaborator and
  shared with you count as followed and are left out.

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
  versions, covers, extended mixes; `official_only` removes most of the live and video ones), and quality depends on the source (usually ~128–160 kbps
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
