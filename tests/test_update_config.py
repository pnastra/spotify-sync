import json

import pytest

import update_config as uc

ME = "me"


def pl(pid, name, owner=ME):
    return {"id": pid, "name": name, "owner": {"id": owner}}


def test_select_keeps_only_my_playlists_and_applies_excludes():
    items = [
        pl("a" * 22, "Swim"),
        pl("b" * 22, "Followed", owner="someone"),
        pl("c" * 22, "My Shazam Tracks"),
        pl("d" * 22, "liked songs"),
        pl("e" * 22, "Run"),
        pl("a" * 22, "Swim"),  # duplicate
        None,
        {"id": None, "name": "x", "owner": {"id": ME}},
    ]
    got = uc.select_playlists(items, ME, uc.DEFAULT_EXCLUDES)
    assert [p["name"] for p in got] == ["Swim", "Run"]


def test_exclude_is_case_insensitive():
    got = uc.select_playlists([pl("a" * 22, "my shazam tracks")], ME, ["My Shazam Tracks"])
    assert got == []


def test_entry_id_handles_strings_and_dicts():
    url = "https://open.spotify.com/playlist/" + "a" * 22
    assert uc.entry_id(url + "?si=x") == "a" * 22
    assert uc.entry_id({"url": url, "name": "N"}) == "a" * 22


def test_merge_keeps_names_adds_and_removes():
    base = "https://open.spotify.com/playlist/"
    existing = [{"name": "Swim Mix", "url": base + "a" * 22}, base + "z" * 22]
    picked = [{"id": "a" * 22, "name": "Swim"}, {"id": "n" * 22, "name": "New"}]
    new, added, removed = uc.merge_playlists(existing, picked)
    assert new[0] == {"name": "Swim Mix", "url": base + "a" * 22}
    assert new[1] == {"url": base + "n" * 22}
    assert [a["name"] for a in added] == ["New"]
    assert removed == ["z" * 22]


@pytest.mark.parametrize(("old", "new"), [(10, 0), (10, 4), (70, 3)])
def test_check_sane_rejects_suspicious_results(old, new):
    with pytest.raises(uc.UpdateError):
        uc.check_sane(old, new)


@pytest.mark.parametrize(("old", "new"), [(0, 3), (10, 5), (3, 1), (70, 69)])
def test_check_sane_accepts_normal_results(old, new):
    uc.check_sane(old, new)


def run_main(tmp_path, monkeypatch, picked, args, config=None):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config or {"music_dir": "music", "playlists": []}))
    monkeypatch.setattr(uc, "fetch_owned_playlists", lambda _excludes, **_kw: picked)
    rc = uc.main(["--config", str(path), *args])
    return rc, path


def test_main_applies_with_yes_and_writes_backup(tmp_path, monkeypatch):
    picked = [{"id": "a" * 22, "name": "Swim"}]
    rc, path = run_main(tmp_path, monkeypatch, picked, ["--yes"])
    data = json.loads(path.read_text())
    assert rc == 0
    assert data["music_dir"] == "music"  # other keys untouched
    assert data["playlists"] == [{"url": "https://open.spotify.com/playlist/" + "a" * 22}]
    assert (tmp_path / "config.json.bak").exists()


def test_main_dry_run_writes_nothing(tmp_path, monkeypatch):
    picked = [{"id": "a" * 22, "name": "Swim"}]
    rc, path = run_main(tmp_path, monkeypatch, picked, ["--dry-run"])
    assert rc == 0
    assert json.loads(path.read_text())["playlists"] == []
    assert not (tmp_path / "config.json.bak").exists()


def test_main_aborts_on_empty_result(tmp_path, monkeypatch):
    base = "https://open.spotify.com/playlist/"
    config = {"music_dir": "m", "playlists": [base + "a" * 22]}
    rc, path = run_main(tmp_path, monkeypatch, [], ["--yes"], config)
    assert rc == 1
    assert json.loads(path.read_text()) == config


def test_main_without_terminal_or_yes_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    picked = [{"id": "a" * 22, "name": "Swim"}]
    rc, path = run_main(tmp_path, monkeypatch, picked, [])
    assert rc == 1
    assert json.loads(path.read_text())["playlists"] == []


def test_no_browser_flag_is_passed_through(tmp_path, monkeypatch):
    seen = {}

    def fake(_excludes, open_browser=True):
        seen["open_browser"] = open_browser
        return [{"id": "a" * 22, "name": "Swim"}]

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"playlists": []}))
    monkeypatch.setattr(uc, "fetch_owned_playlists", fake)
    assert uc.main(["--config", str(path), "--dry-run", "--no-browser"]) == 0
    assert seen == {"open_browser": False}
