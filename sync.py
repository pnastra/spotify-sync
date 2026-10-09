#!/usr/bin/env python3
"""Mirror Spotify playlists into one MP3 folder per playlist using `spotdl sync`.

Built for headphones that play plain folders of MP3s (e.g. Shokz OpenSwim Pro):
no M3U playlists, no Liked Songs, run on demand only.

Spotify credentials are NOT read or stored here. spotdl takes them from its own
config file (~/.spotdl/config.json) or its defaults.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_DIR / "config.json"
LOCK_NAME = ".sync.lock"
MANIFEST_NAME = "folders.json"
FILENAME_TEMPLATE = "{artists} - {title}.{output-ext}"

PLAYLIST_URL_RE = re.compile(
    r"^https://open\.spotify\.com/(?:intl-[a-z-]+/)?playlist/([A-Za-z0-9]{22})(?:[/?#].*)?$"
)
VALID_BITRATES = {
    "auto",
    "disable",
    "8k",
    "16k",
    "24k",
    "32k",
    "40k",
    "48k",
    "64k",
    "80k",
    "96k",
    "112k",
    "128k",
    "160k",
    "192k",
    "224k",
    "256k",
    "320k",
    *(str(n) for n in range(10)),
}


class ConfigError(ValueError):
    pass


class LockError(RuntimeError):
    pass


@dataclass
class Playlist:
    url: str
    playlist_id: str
    name: str | None = None

    @property
    def folder(self) -> str:
        # Fall back to spotdl's own playlist-name variable when no name is given.
        return self.name if self.name else "{list-name}"


@dataclass
class Config:
    music_dir: Path
    state_dir: Path
    cookie_file: Path | None = None
    bitrate: str = "192k"
    threads: int = 4
    audio: list[str] = field(default_factory=lambda: ["youtube-music"])
    prune: bool = False
    playlists: list[Playlist] = field(default_factory=list)


def parse_playlist_url(url: str) -> str:
    """Return the playlist ID for a Spotify playlist URL, or raise ConfigError."""
    url = url.strip()
    if "collection/tracks" in url or url == "saved":
        raise ConfigError("Liked Songs are not supported; use a playlist URL.")
    match = PLAYLIST_URL_RE.match(url)
    if not match:
        raise ConfigError(f"Not a Spotify playlist URL: {url!r}")
    return match.group(1)


def safe_folder_name(name: str) -> str:
    """Make a playlist name safe to use as a single folder name."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().strip(".")
    if not cleaned:
        raise ConfigError(f"Playlist name {name!r} is empty after cleaning.")
    return cleaned


def _expand(path: str, base: Path) -> Path:
    p = Path(os.path.expanduser(path))
    return p if p.is_absolute() else (base / p)


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"{path} not found. Copy config.example.json to config.json and edit it."
        ) from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None

    for secret in ("client_id", "client_secret", "auth_token"):
        if secret in raw:
            raise ConfigError(
                f"{secret!r} does not belong in {path.name}; put it in ~/.spotdl/config.json."
            )

    base = path.parent
    if "music_dir" not in raw:
        raise ConfigError("config is missing 'music_dir'.")

    bitrate = str(raw.get("bitrate", "192k"))
    if bitrate not in VALID_BITRATES:
        raise ConfigError(f"Invalid bitrate {bitrate!r}.")

    threads = int(raw.get("threads", 4))
    if threads < 1:
        raise ConfigError("threads must be >= 1.")

    playlists = []
    seen = set()
    for entry in raw.get("playlists", []):
        if isinstance(entry, str):
            entry = {"url": entry}
        pid = parse_playlist_url(entry["url"])
        if pid in seen:
            raise ConfigError(f"Playlist {pid} is listed twice.")
        seen.add(pid)
        name = entry.get("name")
        playlists.append(
            Playlist(
                url=f"https://open.spotify.com/playlist/{pid}",
                playlist_id=pid,
                name=safe_folder_name(name) if name else None,
            )
        )
    if not playlists:
        raise ConfigError("config has no playlists.")

    prune = raw.get("prune", False)
    if not isinstance(prune, bool):
        raise ConfigError("prune must be true or false.")

    cookie = raw.get("cookie_file")
    return Config(
        music_dir=_expand(raw["music_dir"], base),
        state_dir=_expand(raw.get("state_dir", "state"), base),
        cookie_file=_expand(cookie, base) if cookie else None,
        bitrate=bitrate,
        threads=threads,
        audio=list(raw.get("audio", ["youtube-music"])),
        prune=prune,
        playlists=playlists,
    )


def state_file(cfg: Config, playlist: Playlist) -> Path:
    return cfg.state_dir / f"{playlist.playlist_id}.spotdl"


def errors_file(cfg: Config, playlist: Playlist) -> Path:
    return cfg.state_dir / f"{playlist.playlist_id}.errors.txt"


def read_errors(path: Path) -> list[str]:
    """Return the failed-track lines spotdl wrote with --save-errors (skips timestamps)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line for line in lines if line.startswith("http")]


def build_command(
    cfg: Config, playlist: Playlist, prune: bool = False, spotdl: str = "spotdl"
) -> list[str]:
    """Build the `spotdl sync` command for one playlist.

    First run: sync from the URL and write the .spotdl state file.
    Later runs: sync from the state file (spotdl refuses --save-file then).
    """
    sf = state_file(cfg, playlist)
    if sf.exists():
        cmd = [spotdl, "sync", str(sf)]
    else:
        cmd = [spotdl, "sync", playlist.url, "--save-file", str(sf)]

    output = cfg.music_dir / playlist.folder / FILENAME_TEMPLATE
    cmd += [
        "--output",
        str(output),
        "--format",
        "mp3",
        "--bitrate",
        cfg.bitrate,
        "--threads",
        str(cfg.threads),
        "--audio",
        *cfg.audio,
        "--overwrite",
        "skip",
        "--print-errors",
        "--save-errors",
        str(errors_file(cfg, playlist)),
    ]
    if cfg.cookie_file:
        cmd += ["--cookie-file", str(cfg.cookie_file)]
    if not prune:
        cmd.append("--sync-without-deleting")
    return cmd


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SyncLock:
    """Exclusive lockfile holding the owner's PID; stale locks are replaced."""

    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def acquire(self) -> None:
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    pid = int(self.path.read_text().strip() or 0)
                except (ValueError, OSError):
                    pid = 0
                if pid and _pid_alive(pid):
                    raise LockError(f"Another sync is running (pid {pid}, {self.path}).") from None
                self.path.unlink(missing_ok=True)  # stale lock
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            self.acquired = True
            return
        raise LockError(f"Could not acquire {self.path}.")

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def __enter__(self) -> SyncLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# --- folder bookkeeping -----------------------------------------------------
#
# state/folders.json remembers which folder each playlist was synced into:
#   {"playlists": {"<id>": "<folder>"}, "leftovers": {"<old folder>": "<id>"}}
# That lets a run rename a folder when its configured name changes (no
# re-download) and offer to delete folders of playlists removed from config.


@dataclass
class Orphan:
    playlist_id: str
    folder: str | None  # None: folder is still used by a configured playlist
    drop_state: bool  # playlist removed from config: delete its state files too


def manifest_path(cfg: Config) -> Path:
    return cfg.state_dir / MANIFEST_NAME


def load_manifest(cfg: Config) -> dict:
    try:
        data = json.loads(manifest_path(cfg).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {
        "playlists": dict(data.get("playlists", {})),
        "leftovers": dict(data.get("leftovers", {})),
    }


def save_manifest(cfg: Config, manifest: dict) -> None:
    path = manifest_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def state_list_name(cfg: Config, playlist: Playlist) -> str | None:
    """The Spotify playlist name stored in the spotdl state file, if any."""
    try:
        songs = json.loads(state_file(cfg, playlist).read_text(encoding="utf-8")).get("songs")
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return (songs[0].get("list_name") or None) if songs else None


def target_folder(cfg: Config, playlist: Playlist) -> str | None:
    """The folder this playlist should sync into, if known before running spotdl."""
    return playlist.name or state_list_name(cfg, playlist)


def synced_folder(cfg: Config, playlist: Playlist) -> str | None:
    """The folder this playlist's tracks live in, if it can be determined."""
    name = target_folder(cfg, playlist)
    return name if name and (playlist.name or (cfg.music_dir / name).is_dir()) else None


def record_folder(cfg: Config, manifest: dict, playlist: Playlist) -> None:
    folder = synced_folder(cfg, playlist)
    if not folder:
        return
    old = manifest["playlists"].get(playlist.playlist_id)
    if old and old != folder and (cfg.music_dir / old).is_dir():
        manifest["leftovers"][old] = playlist.playlist_id  # e.g. renamed on Spotify
    manifest["playlists"][playlist.playlist_id] = folder
    manifest["leftovers"].pop(folder, None)


def plan_renames(cfg: Config, manifest: dict) -> list[tuple[str, str, str]]:
    """(playlist_id, old, new) for playlists whose folder name changed in config.

    Covers adding, changing or removing a "name" (the latter falls back to the
    Spotify name saved in the state file). Renames made on Spotify itself are
    only seen after spotdl runs, so those still re-download (see README).
    """
    plans = []
    for p in cfg.playlists:
        old = manifest["playlists"].get(p.playlist_id)
        new = target_folder(cfg, p)
        if new and old and old != new and (cfg.music_dir / old).is_dir():
            plans.append((p.playlist_id, old, new))
    return plans


def apply_renames(cfg: Config, manifest: dict) -> list[str]:
    messages = []
    for pid, old, new in plan_renames(cfg, manifest):
        src, dst = cfg.music_dir / old, cfg.music_dir / new
        if dst.exists():
            manifest["leftovers"][old] = pid
            messages.append(f"'{new}' already exists; '{old}' is now a leftover folder")
        else:
            src.rename(dst)
            messages.append(f"renamed folder '{old}' -> '{new}'")
        manifest["playlists"][pid] = new
    return messages


def find_orphans(cfg: Config, manifest: dict) -> list[Orphan]:
    configured = {p.playlist_id for p in cfg.playlists}
    in_use = {p.name for p in cfg.playlists if p.name}
    in_use |= {f for pid, f in manifest["playlists"].items() if pid in configured}
    orphans = [
        Orphan(pid, None if folder in in_use else folder, drop_state=True)
        for pid, folder in sorted(manifest["playlists"].items())
        if pid not in configured
    ]
    orphans += [
        Orphan(pid, folder, drop_state=False)
        for folder, pid in sorted(manifest["leftovers"].items())
        if folder not in in_use
    ]
    return orphans


def _folder_summary(path: Path) -> str:
    if not path.is_dir():
        return "already gone"
    files = [f for f in path.rglob("*") if f.is_file()]
    size = sum(f.stat().st_size for f in files) / 1_000_000
    return f"{len(files)} files, {size:.1f} MB"


def describe_orphan(cfg: Config, o: Orphan) -> str:
    why = "removed from config" if o.drop_state else "old folder"
    if o.folder is None:
        return f"  (state only) playlist {o.playlist_id}: {why}"
    return f"  {cfg.music_dir / o.folder}/  ({_folder_summary(cfg.music_dir / o.folder)}; {why})"


def delete_orphan(cfg: Config, manifest: dict, o: Orphan) -> None:
    if o.folder is not None:
        path = cfg.music_dir / o.folder
        # Only ever delete a direct child of music_dir.
        if o.folder in ("", ".", "..") or path.resolve().parent != cfg.music_dir.resolve():
            raise ValueError(f"refusing to delete {path}")
        if path.is_dir():
            shutil.rmtree(path)
        manifest["leftovers"].pop(o.folder, None)
    if o.drop_state:
        playlist = Playlist(url="", playlist_id=o.playlist_id)
        state_file(cfg, playlist).unlink(missing_ok=True)
        errors_file(cfg, playlist).unlink(missing_ok=True)
        manifest["playlists"].pop(o.playlist_id, None)


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("not a terminal; skipping (use --yes to delete without asking)")
        return False
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def handle_orphans(cfg: Config, manifest: dict, prune: bool, assume_yes: bool) -> None:
    orphans = find_orphans(cfg, manifest)
    if not orphans:
        return
    print("\nFolders no longer matching any playlist in config:")
    for o in orphans:
        print(describe_orphan(cfg, o))
    if not prune:
        print("Not deleted (prune is off; run with --prune to remove them).")
        return
    if not confirm("Delete these? [y/N] ", assume_yes):
        print("Kept.")
        return
    for o in orphans:
        delete_orphan(cfg, manifest, o)
        print(f"deleted {o.folder or 'state for ' + o.playlist_id}")


def check_tools() -> list[str]:
    return [t for t in ("spotdl", "ffmpeg") if shutil.which(t) is None]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="path to config.json (default: next to sync.py)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print the spotdl commands without running them"
    )
    ap.add_argument(
        "--only",
        metavar="NAME_OR_ID",
        action="append",
        help="sync only this playlist (name or ID); repeatable",
    )
    deletion = ap.add_mutually_exclusive_group()
    deletion.add_argument(
        "--prune",
        dest="prune",
        action="store_true",
        default=None,
        help="DELETE local files for tracks removed from the playlist (overrides config)",
    )
    deletion.add_argument(
        "--keep",
        dest="prune",
        action="store_false",
        help="never delete local files on this run (overrides config)",
    )
    ap.add_argument(
        "--yes", action="store_true", help="delete leftover folders without asking (with prune)"
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    prune = cfg.prune if args.prune is None else args.prune
    playlists = cfg.playlists
    if args.only:
        wanted = set(args.only)
        playlists = [p for p in playlists if p.playlist_id in wanted or p.name in wanted]
        if not playlists:
            print(f"No playlist matches {args.only}.", file=sys.stderr)
            return 2

    if cfg.cookie_file and not cfg.cookie_file.is_file():
        print(f"warning: cookie file not found: {cfg.cookie_file}", file=sys.stderr)

    if args.dry_run:
        manifest = load_manifest(cfg)
        for _, old, new in plan_renames(cfg, manifest):
            print(f"# would rename folder '{old}' -> '{new}'")
        for p in playlists:
            print(shlex.join(build_command(cfg, p, prune=prune)))
        orphans = find_orphans(cfg, manifest)
        if orphans:
            print(f"# leftover folders ({'would ask to delete' if prune else 'kept, prune off'}):")
            for o in orphans:
                print("#" + describe_orphan(cfg, o))
        return 0

    missing = check_tools()
    if missing:
        print(f"missing required tools: {', '.join(missing)}", file=sys.stderr)
        return 2

    if prune:
        print("prune is ON: files for tracks removed on Spotify will be deleted.", flush=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.music_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    try:
        with SyncLock(REPO_DIR / LOCK_NAME):
            manifest = load_manifest(cfg)
            try:
                for msg in apply_renames(cfg, manifest):
                    print(msg)
                for p in playlists:
                    print(f"==> {p.name or p.playlist_id}", flush=True)
                    if p.name:
                        (cfg.music_dir / p.name).mkdir(parents=True, exist_ok=True)
                    ef = errors_file(cfg, p)
                    ef.unlink(missing_ok=True)  # spotdl appends; keep only this run's errors
                    rc = subprocess.call(build_command(cfg, p, prune=prune))
                    record_folder(cfg, manifest, p)
                    failed = read_errors(ef)
                    if rc != 0:
                        failures += 1
                        print(f"spotdl exited with {rc} for {p.url}", file=sys.stderr)
                    elif failed:
                        # spotdl exits 0 even when individual tracks fail
                        failures += 1
                        print(f"{len(failed)} track(s) failed; see {ef}", file=sys.stderr)
                handle_orphans(cfg, manifest, prune, args.yes)
            finally:
                save_manifest(cfg, manifest)
    except LockError as exc:
        print(exc, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
