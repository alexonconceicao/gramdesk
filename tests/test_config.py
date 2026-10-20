import json
import os

import pytest


@pytest.fixture
def cfgmod(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    from gramdesk import config

    return importlib.reload(config)


def test_roundtrip_and_permissions(cfgmod):
    c = cfgmod.Config(accounts=[cfgmod.Account.new("A")])
    cfgmod.save(c)
    if os.name != "nt":  # no Windows o isolamento vem das ACLs do perfil do usuário
        assert oct(os.stat(cfgmod.CONFIG_FILE).st_mode & 0o777) == "0o600"
        assert oct(os.stat(cfgmod.CONFIG_DIR).st_mode & 0o777) == "0o700"
    loaded = cfgmod.load()
    assert [a.name for a in loaded.accounts] == ["A"]


def test_rejects_bad_entries(cfgmod):
    cfgmod.private_dir(cfgmod.CONFIG_DIR)
    cfgmod.CONFIG_FILE.write_text(
        json.dumps(
            {
                "accounts": [{"id": "../../x", "name": "evil"}, {"id": "abcdef012345", "name": "ok"}],
                "hibernate": "rm -rf",
                "lock": {"alg": "md5", "hash": "x"},
                "pending_delete": ["../../home"],
            }
        )
    )
    c = cfgmod.load()
    assert [a.id for a in c.accounts] == ["abcdef012345"]
    assert c.hibernate == "discard" and c.lock is None and c.pending_delete == []


def test_corrupt_file(cfgmod):
    cfgmod.private_dir(cfgmod.CONFIG_DIR)
    cfgmod.CONFIG_FILE.write_text("{nope")
    assert cfgmod.load().accounts == []
    assert cfgmod.CONFIG_FILE.with_suffix(".json.bad").exists()


def test_purge(cfgmod):
    acc = cfgmod.Account.new("A")
    storage, cache = cfgmod.profile_dirs(acc.id)
    storage.mkdir(parents=True)
    (storage / "Cookies").write_text("x")
    c = cfgmod.Config(pending_delete=[acc.id])
    cfgmod.purge_pending(c)
    assert not storage.exists() and c.pending_delete == []


def test_appearance_and_account_fields(cfgmod):
    cfgmod.private_dir(cfgmod.CONFIG_DIR)
    cfgmod.CONFIG_FILE.write_text(
        json.dumps(
            {
                "accounts": [{"id": "abcdef012345", "name": "a", "zoom": 999, "keep_alive": 1, "color": True}],
                "theme": "neon",
                "accent": "oceano",
                "idle_lock": 7,
            }
        )
    )
    c = cfgmod.load()
    a = c.accounts[0]
    assert a.zoom == cfgmod.ZOOM_MAX and a.keep_alive is True and a.color == 0
    assert c.theme == "system" and c.accent == "oceano" and c.idle_lock == 0
    cfgmod.save(c)
    assert cfgmod.load().accent == "oceano"
