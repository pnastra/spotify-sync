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


def test_dry_run_prints_commands(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sync, "spotdl_python", lambda: None)  # plain spotdl, no launcher
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


# --- folder bookkeeping / leftover folders ------------------------------------

PID2 = "1111111111111111111111"
URL2 = f"https://open.spotify.com/playlist/{PID2}"


def make_folder(cfg, name, files=1):
    path = cfg.music_dir / name
    path.mkdir(parents=True, exist_ok=True)
    for i in range(files):
        (path / f"t{i}.mp3").write_bytes(b"x")
    return path


def test_manifest_roundtrip(cfg):
    cfg.state_dir.mkdir(parents=True)
    assert sync.load_manifest(cfg) == {"playlists": {}, "leftovers": {}}
    m = {"playlists": {PID: "Swim"}, "leftovers": {}}
    sync.save_manifest(cfg, m)
    assert sync.load_manifest(cfg) == m


def test_synced_folder_uses_list_name_from_state(tmp_path):
    cfg = sync.load_config(write_config(tmp_path, playlists=[URL]))
    sf = sync.state_file(cfg, cfg.playlists[0])
    sf.parent.mkdir(parents=True)
    sf.write_text(json.dumps({"songs": [{"list_name": "Spotify Name"}]}))
    assert sync.synced_folder(cfg, cfg.playlists[0]) is None  # folder not created yet
    make_folder(cfg, "Spotify Name")
    assert sync.synced_folder(cfg, cfg.playlists[0]) == "Spotify Name"


def test_rename_in_config_moves_folder(cfg):
    make_folder(cfg, "Old Name", files=2)
    m = {"playlists": {PID: "Old Name"}, "leftovers": {}}
    assert sync.plan_renames(cfg, m) == [(PID, "Old Name", "Swim")]
    sync.apply_renames(cfg, m)
    assert (cfg.music_dir / "Swim" / "t1.mp3").exists()
    assert not (cfg.music_dir / "Old Name").exists()
    assert m["playlists"][PID] == "Swim"
    assert sync.find_orphans(cfg, m) == []


def test_rename_onto_existing_folder_leaves_leftover(cfg):
    make_folder(cfg, "Old Name")
    make_folder(cfg, "Swim")
    m = {"playlists": {PID: "Old Name"}, "leftovers": {}}
    sync.apply_renames(cfg, m)
    assert (cfg.music_dir / "Old Name").exists()
    assert sync.find_orphans(cfg, m) == [sync.Orphan(PID, "Old Name", drop_state=False)]


def test_record_folder_marks_spotify_rename_as_leftover(tmp_path):
    cfg = sync.load_config(write_config(tmp_path, playlists=[URL]))
    make_folder(cfg, "Before")
    make_folder(cfg, "After")
    sf = sync.state_file(cfg, cfg.playlists[0])
    sf.parent.mkdir(parents=True)
    sf.write_text(json.dumps({"songs": [{"list_name": "After"}]}))
    m = {"playlists": {PID: "Before"}, "leftovers": {}}
    sync.record_folder(cfg, m, cfg.playlists[0])
    assert m == {"playlists": {PID: "After"}, "leftovers": {"Before": PID}}


def test_removed_playlist_is_orphan_and_delete_removes_folder_and_state(cfg):
    folder = make_folder(cfg, "Gone", files=3)
    gone = sync.Playlist(url=URL2, playlist_id=PID2)
    sync.state_file(cfg, gone).parent.mkdir(parents=True, exist_ok=True)
    sync.state_file(cfg, gone).write_text("{}")
    sync.errors_file(cfg, gone).write_text("")
    keep = make_folder(cfg, "Swim")
    m = {"playlists": {PID: "Swim", PID2: "Gone"}, "leftovers": {}}

    orphans = sync.find_orphans(cfg, m)
    assert orphans == [sync.Orphan(PID2, "Gone", drop_state=True)]
    assert "3 files" in sync.describe_orphan(cfg, orphans[0])

    sync.delete_orphan(cfg, m, orphans[0])
    assert not folder.exists()
    assert not sync.state_file(cfg, gone).exists()
    assert not sync.errors_file(cfg, gone).exists()
    assert keep.exists()
    assert m == {"playlists": {PID: "Swim"}, "leftovers": {}}


def test_folder_still_used_by_configured_playlist_is_never_deleted(cfg):
    keep = make_folder(cfg, "Swim")
    m = {"playlists": {PID: "Swim", PID2: "Swim"}, "leftovers": {"Swim": PID2}}
    orphans = sync.find_orphans(cfg, m)
    assert orphans == [sync.Orphan(PID2, None, drop_state=True)]
    sync.delete_orphan(cfg, m, orphans[0])
    assert keep.exists()


@pytest.mark.parametrize("bad", ["..", ".", "", "../outside"])
def test_delete_orphan_refuses_paths_outside_music_dir(cfg, bad):
    cfg.music_dir.mkdir(parents=True)
    m = {"playlists": {}, "leftovers": {}}
    with pytest.raises(ValueError, match="refusing"):
        sync.delete_orphan(cfg, m, sync.Orphan(PID2, bad, drop_state=False))
    assert cfg.music_dir.exists()


def test_handle_orphans_keeps_when_prune_off(cfg, capsys):
    folder = make_folder(cfg, "Gone")
    m = {"playlists": {PID2: "Gone"}, "leftovers": {}}
    sync.handle_orphans(cfg, m, prune=False, assume_yes=True)
    assert folder.exists()
    assert "prune is off" in capsys.readouterr().out


def test_handle_orphans_asks_and_respects_no(cfg, monkeypatch):
    folder = make_folder(cfg, "Gone")
    m = {"playlists": {PID2: "Gone"}, "leftovers": {}}
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    sync.handle_orphans(cfg, m, prune=True, assume_yes=False)
    assert folder.exists()
    monkeypatch.setattr("builtins.input", lambda _: "y")
    sync.handle_orphans(cfg, m, prune=True, assume_yes=False)
    assert not folder.exists()


def test_handle_orphans_non_tty_without_yes_keeps(cfg, monkeypatch):
    folder = make_folder(cfg, "Gone")
    m = {"playlists": {PID2: "Gone"}, "leftovers": {}}
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    sync.handle_orphans(cfg, m, prune=True, assume_yes=False)
    assert folder.exists()


def test_dry_run_reports_leftovers_without_deleting(tmp_path, capsys):
    path = write_config(tmp_path, prune=True)
    cfg = sync.load_config(path)
    folder = make_folder(cfg, "Gone")
    cfg.state_dir.mkdir(parents=True)
    sync.save_manifest(cfg, {"playlists": {PID2: "Gone"}, "leftovers": {}})
    assert sync.main(["--config", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would ask to delete" in out and "Gone" in out
    assert folder.exists()


def test_removing_name_renames_back_to_spotify_name(tmp_path):
    cfg = sync.load_config(write_config(tmp_path, playlists=[URL]))
    make_folder(cfg, "Custom")
    sf = sync.state_file(cfg, cfg.playlists[0])
    sf.parent.mkdir(parents=True)
    sf.write_text(json.dumps({"songs": [{"list_name": "Spotify Name"}]}))
    m = {"playlists": {PID: "Custom"}, "leftovers": {}}
    sync.apply_renames(cfg, m)
    assert (cfg.music_dir / "Spotify Name" / "t0.mp3").exists()
    assert m["playlists"][PID] == "Spotify Name"


# --- English-search launcher, official_only, log tally ---------------------------


def test_build_command_accepts_prefix_list_and_official_flag(cfg):
    cmd = sync.build_command(cfg, cfg.playlists[0], spotdl=["py", "-I", "launcher.py"])
    assert cmd[:5] == [
        "py",
        "-I",
        "launcher.py",
        "sync",
        f"https://open.spotify.com/playlist/{PID}",
    ]
    assert "--only-verified-results" not in cmd
    cmd = sync.build_command(cfg, cfg.playlists[0], official_only=True)
    assert "--only-verified-results" in cmd


def test_official_and_english_flags_load_and_validate(tmp_path):
    cfg = sync.load_config(write_config(tmp_path))
    assert (cfg.official_only, cfg.english_search) == (False, True)
    cfg = sync.load_config(write_config(tmp_path, official_only=True, english_search=False))
    assert (cfg.official_only, cfg.english_search) == (True, False)
    with pytest.raises(sync.ConfigError, match="official_only"):
        sync.load_config(write_config(tmp_path, official_only="yes"))


def test_resolve_spotdl_uses_launcher_or_falls_back(cfg, monkeypatch, capsys):
    monkeypatch.setattr(sync, "spotdl_python", lambda: "/venv/bin/python")
    assert sync.resolve_spotdl(cfg) == ["/venv/bin/python", "-I", str(sync.LAUNCHER)]
    monkeypatch.setattr(sync, "spotdl_python", lambda: None)
    assert sync.resolve_spotdl(cfg) == ["spotdl"]
    assert "warning" in capsys.readouterr().err
    cfg.english_search = False
    assert sync.resolve_spotdl(cfg) == ["spotdl"]


def test_launcher_switches_to_english():
    text = sync.LAUNCHER.read_text()
    assert 'language="en"' in text and "_create_client" in text


def test_tally_classifies_sources_and_handles_wrapped_urls():
    t = sync.Tally()
    for line in [
        'Downloaded "Artist - Song A": https://music.youtube.com/watch?v=aaa',
        'Downloaded "Artist - Song B":',
        "https://www.youtube.com/watch?v=bbb",
        "Skipping Artist - Song C (file already exists)",
        "unrelated line https://www.youtube.com/watch?v=zzz",
    ]:
        t.feed(line)
    t.not_found = 2
    assert t.songs == ["Artist - Song A"]
    assert t.videos == ["Artist - Song B"]
    assert t.existing == 1
    text = t.summary()
    assert "1 official songs, 1 from videos, 1 already present, 2 not found" in text
    assert "Artist - Song B" in text


def test_tally_add():
    a, b = sync.Tally(songs=["x"], existing=1), sync.Tally(videos=["y"], not_found=3)
    a.add(b)
    assert (a.songs, a.videos, a.existing, a.not_found) == (["x"], ["y"], 1, 3)


def test_run_logged_streams_and_tallies(tmp_path):
    script = 'print(\'Downloaded "T":\'); print("https://music.youtube.com/watch?v=q")'
    t = sync.Tally()
    with open(tmp_path / "x.log", "w") as log:
        rc = sync.run_logged([sys.executable, "-c", script], log, t)
    assert rc == 0 and t.songs == ["T"]
    assert "music.youtube.com" in (tmp_path / "x.log").read_text()
