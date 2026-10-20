import json

from gramdesk import donation


def test_disabled_without_build_info():
    # O repositório nunca traz _build_info.json; sem ele o recurso fica desligado.
    assert donation.CONFIG is None or isinstance(donation.CONFIG, dict)


def test_should_show_rules(monkeypatch):
    monkeypatch.setattr(donation, "CONFIG", {**donation.DEFAULTS, "url": "https://exemplo.org/doar"})
    now = 10_000_000.0
    assert not donation.should_show({"first_seen": now}, now)  # ainda no período inicial
    later = now + 4 * donation.DAY
    assert donation.should_show({"first_seen": now}, later)
    assert not donation.should_show({"first_seen": now, "last_shown": later}, later + donation.DAY)
    assert donation.should_show({"first_seen": now, "last_shown": later}, later + 31 * donation.DAY)
    assert not donation.should_show({"first_seen": now, "never": True}, later + 99 * donation.DAY)


def test_rejects_non_https(tmp_path, monkeypatch):
    from importlib import resources

    f = tmp_path / "_build_info.json"
    f.write_text(json.dumps({"donation": {"url": "http://exemplo.org"}}), encoding="utf-8")
    monkeypatch.setattr(resources, "files", lambda _pkg: tmp_path)
    assert donation._load() is None
    f.write_text(json.dumps({"donation": {"url": "https://exemplo.org/x", "interval_days": 999}}), encoding="utf-8")
    cfg = donation._load()
    assert cfg["url"] == "https://exemplo.org/x" and cfg["interval_days"] == 365
