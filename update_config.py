#!/usr/bin/env python3
"""Refresh the "playlists" list in config.json from your Spotify account.

Keeps only playlists YOU created (not ones you follow), minus any named in
"exclude_playlists" (default: "My Shazam Tracks"). Liked Songs is not a playlist
in Spotify's API, so it is never included. Only the "playlists" key is rewritten;
names you gave existing playlists are kept. Music files are never touched; folder
cleanup stays in sync.py.

Uses spotdl's own Spotify credentials (~/.spotdl/config.json) and its Python
environment (which ships spotipy). The first run opens a browser to approve
read-only access to your private playlists.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_DIR / "config.json"
SPOTDL_CONFIG = Path("~/.spotdl/config.json").expanduser()
SPOTIFY_CACHE = Path("~/.spotdl/.spotipy").expanduser()
REDIRECT_URI = "http://127.0.0.1:9900/"
SCOPE = "playlist-read-private"
DEFAULT_EXCLUDES = ["My Shazam Tracks", "Liked Songs"]
MIN_KEEP_RATIO = 0.5  # abort if the new list is under half the size of the old one


class UpdateError(RuntimeError):
    pass


def select_playlists(items: list[dict], me_id: str, excludes: list[str]) -> list[dict]:
    """Return [{"id", "name"}] for playlists owned by me_id and not excluded."""
    skip = {e.casefold() for e in excludes}
    picked, seen = [], set()
    for item in items:
        if not item or not item.get("id"):
            continue
        if (item.get("owner") or {}).get("id") != me_id:
            continue
        if (item.get("name") or "").casefold() in skip:
            continue
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        picked.append({"id": item["id"], "name": item.get("name") or ""})
    return picked


def entry_id(entry: str | dict) -> str:
    url = entry if isinstance(entry, str) else entry["url"]
    return url.split("/playlist/")[1].split("?")[0].split("/")[0].strip()


def merge_playlists(existing: list, picked: list[dict]) -> tuple[list, list[dict], list[str]]:
    """Return (new_entries, added, removed_ids). Existing entries keep their names."""
    by_id = {entry_id(e): e for e in existing}
    new_entries, added = [], []
    for p in picked:
        if p["id"] in by_id:
            new_entries.append(by_id[p["id"]])
        else:
            new_entries.append({"url": f"https://open.spotify.com/playlist/{p['id']}"})
            added.append(p)
    kept = {p["id"] for p in picked}
    removed = [i for i in by_id if i not in kept]
    return new_entries, added, removed


def check_sane(old_count: int, new_count: int) -> None:
    if new_count == 0:
        raise UpdateError("Spotify returned no matching playlists; config left unchanged.")
    if old_count >= 4 and new_count < old_count * MIN_KEEP_RATIO:
        raise UpdateError(
            f"New list ({new_count}) is under half the old one ({old_count}); "
            "refusing to overwrite. Check your login and the exclude list."
        )


def write_config(path: Path, config: dict) -> None:
    shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _spotdl_python() -> str | None:
    """Python interpreter of the pipx environment that runs spotdl."""
    exe = shutil.which("spotdl")
    if not exe:
        return None
    try:
        first = Path(exe).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return None
    py = first[2:].strip() if first.startswith("#!") else None
    return py if py and Path(py).exists() else None


def fetch_owned_playlists(excludes: list[str], open_browser: bool = True) -> list[dict]:
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        py = _spotdl_python()
        if py and os.environ.get("_UPDATE_CONFIG_REEXEC") != "1":
            os.environ["_UPDATE_CONFIG_REEXEC"] = "1"
            os.execv(py, [py, "-I", str(Path(__file__).resolve()), *sys.argv[1:]])
        raise UpdateError("spotipy not found; install spotdl with pipx.") from None

    try:
        creds = json.loads(SPOTDL_CONFIG.read_text(encoding="utf-8"))
        client_id, client_secret = creds["client_id"], creds["client_secret"]
    except (OSError, KeyError, json.JSONDecodeError):
        raise UpdateError(f"client_id/client_secret missing in {SPOTDL_CONFIG}.") from None

    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_handler=spotipy.cache_handler.CacheFileHandler(str(SPOTIFY_CACHE)),
        open_browser=open_browser,
    )
    sp = spotipy.Spotify(auth_manager=auth, requests_timeout=30, retries=3)
    me_id = sp.current_user()["id"]
    items, page = [], sp.current_user_playlists(limit=50)
    while page:
        items.extend(page.get("items", []))
        page = sp.next(page) if page.get("next") else None
    return select_playlists(items, me_id, excludes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument("--yes", action="store_true", help="apply without asking")
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="print the Spotify login link instead of opening it; open it in any browser, "
        "approve, then paste the (error-looking) address you land on back into the terminal",
    )
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # show login prompts immediately

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"{args.config} not found; copy config.example.json first.", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{args.config} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    excludes = config.get("exclude_playlists", DEFAULT_EXCLUDES)
    existing = config.get("playlists", [])
    try:
        picked = fetch_owned_playlists(excludes, open_browser=not args.no_browser)
        check_sane(len(existing), len(picked))
        new_entries, added, removed = merge_playlists(existing, picked)
    except UpdateError as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        return 1

    print(f"{len(picked)} playlists you created (excluding {', '.join(excludes) or 'nothing'}).")
    for p in added:
        print(f"  + {p['name']}  ({p['id']})")
    for pid in removed:
        print(f"  - {pid}")
    if not added and not removed:
        print("config.json is already up to date.")
        return 0
    if args.dry_run:
        print("dry run: nothing written.")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("not a terminal; use --yes to apply.", file=sys.stderr)
            return 1
        if input("Apply to config.json? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Unchanged.")
            return 0
    config["playlists"] = new_entries
    write_config(args.config, config)
    print(f"Updated {args.config} (backup: {args.config.name}.bak).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
