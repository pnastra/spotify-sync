import json
import os
import subprocess
import sys

import pytest

import sync

PID = "37i9dQZF1DXcBWIGoYBM5M"
URL = f"https://open.spotify.com/playlist/{PID}?si=abc123"


def write_config(tmp_path, **overrides):
    data = {
        "music_dir": str(tmp_path / "music"),
        "state_dir": str(tmp_path / "state"),
        "playlists": [{"name": "Swim", "url": URL}],
    }
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def cfg(tmp_path):
    return sync.load_config(write_config(tmp_path))


# --- URL / config parsing ---------------------------------------------------


def test_parse_playlist_url_accepts_tracking_params():
    assert sync.parse_playlist_url(URL) == PID
    assert sync.parse_playlist_url(f"https://open.spotify.com/intl-de/playlist/{PID}") == PID


@pytest.mark.parametrize(
    "bad",
    [
        "https://open.spotify.com/collection/tracks",
        "saved",
        f"https://open.spotify.com/album/{PID}",
        "https://example.com/playlist/x",
    ],
)
def test_parse_playlist_url_rejects_non_playlists(bad):
    with pytest.raises(sync.ConfigError):
        sync.parse_playlist_url(bad)


def test_config_rejects_credentials(tmp_path):
    with pytest.raises(sync.ConfigError, match="client_secret"):
        sync.load_config(write_config(tmp_path, client_secret="x"))


def test_safe_folder_name():
    assert sync.safe_folder_name("a/b:c?") == "a_b_c_"


# --- command building -------------------------------------------------------


def test_first_run_uses_url_and_save_file(cfg):
    cmd = sync.build_command(cfg, cfg.playlists[0])
    assert cmd[:3] == ["spotdl", "sync", f"https://open.spotify.com/playlist/{PID}"]
    assert cmd[cmd.index("--save-file") + 1].endswith(f"{PID}.spotdl")
    assert cmd[cmd.index("--format") + 1] == "mp3"
    assert cmd[cmd.index("--output") + 1].endswith(
        os.path.join("music", "Swim", "{artists} - {title}.{output-ext}")
    )
    assert "--sync-without-deleting" in cmd
    assert "--m3u" not in cmd


def test_later_run_uses_state_file_without_save_file(cfg):
    sf = sync.state_file(cfg, cfg.playlists[0])
    sf.parent.mkdir(parents=True)
    sf.write_text("{}")
    cmd = sync.build_command(cfg, cfg.playlists[0])
    assert cmd[:3] == ["spotdl", "sync", str(sf)]
    assert "--save-file" not in cmd


def test_prune_allows_deletion(cfg):
    assert "--sync-without-deleting" not in sync.build_command(cfg, cfg.playlists[0], prune=True)


def test_unnamed_playlist_uses_list_name(tmp_path):
    cfg = sync.load_config(write_config(tmp_path, playlists=[URL]))
    out = sync.build_command(cfg, cfg.playlists[0])
    assert "{list-name}" in out[out.index("--output") + 1]


def test_cookie_file_passed(tmp_path):
    cfg = sync.load_config(write_config(tmp_path, cookie_file=str(tmp_path / "c.txt")))
    cmd = sync.build_command(cfg, cfg.playlists[0])
    assert cmd[cmd.index("--cookie-file") + 1] == str(tmp_path / "c.txt")


# --- lockfile ---------------------------------------------------------------


def test_lock_acquire_release(tmp_path):
    path = tmp_path / ".sync.lock"
    with sync.SyncLock(path):
        assert path.read_text() == str(os.getpid())
    assert not path.exists()


def test_lock_blocks_when_held_by_live_process(tmp_path):
    path = tmp_path / ".sync.lock"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        path.write_text(str(proc.pid))
        with pytest.raises(sync.LockError):
            sync.SyncLock(path).acquire()
        assert path.exists()  # someone else's lock is left alone
    finally:
        proc.kill()
        proc.wait()


def test_stale_lock_is_replaced(tmp_path):
    path = tmp_path / ".sync.lock"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    path.write_text(str(proc.pid))
    with sync.SyncLock(path):
        assert path.read_text() == str(os.getpid())


# --- CLI --------------------------------------------------------------------


def test_dry_run_prints_commands(tmp_path, capsys):
    assert sync.main(["--config", str(write_config(tmp_path)), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("spotdl sync ")
    assert not (tmp_path / "music").exists()


def test_prune_from_config(tmp_path):
    cfg = sync.load_config(write_config(tmp_path, prune=True))
    assert cfg.prune is True


def test_prune_must_be_bool(tmp_path):
    with pytest.raises(sync.ConfigError, match="prune"):
        sync.load_config(write_config(tmp_path, prune="yes"))


@pytest.mark.parametrize(
    ("cfg_prune", "flags", "deletes"),
    [
        (False, [], False),
        (True, [], True),
        (True, ["--keep"], False),
        (False, ["--prune"], True),
    ],
)
def test_cli_prune_overrides(tmp_path, capsys, cfg_prune, flags, deletes):
    path = write_config(tmp_path, prune=cfg_prune)
    assert sync.main(["--config", str(path), "--dry-run", *flags]) == 0
    out = capsys.readouterr().out
    assert ("--sync-without-deleting" not in out) is deletes


def test_errors_file_passed(cfg):
    cmd = sync.build_command(cfg, cfg.playlists[0])
    assert cmd[cmd.index("--save-errors") + 1].endswith(f"{PID}.errors.txt")


def test_read_errors_skips_timestamps(tmp_path):
    path = tmp_path / "e.txt"
    path.write_text(
        "2026-10-09-14-50-01\n"
        "https://open.spotify.com/track/abc - LookupError: No results found for song: X\n"
    )
    assert sync.read_errors(path) == [
        "https://open.spotify.com/track/abc - LookupError: No results found for song: X"
    ]
    assert sync.read_errors(tmp_path / "missing.txt") == []
