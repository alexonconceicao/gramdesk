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


def _make_blech32(hrp: str, data: list[int]) -> str:
    """Endereço sintético com checksum válido (só para teste)."""
    values = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + data + [0] * 12
    pm = donation._blech32_polymod(values) ^ 1
    check = [(pm >> 5 * (11 - i)) & 31 for i in range(12)]
    return hrp + "1" + "".join(donation._B32[d] for d in data + check)


def test_liquid_address_checksum():
    good = _make_blech32("lq", [0] + [i % 32 for i in range(80)])
    assert donation.liquid_address_ok(good)
    bad = good[:-1] + ("q" if good[-1] != "q" else "p")
    assert not donation.liquid_address_ok(bad)
    assert not donation.liquid_address_ok("lq1" + "b" * 90)  # fora do alfabeto bech32
    assert not donation.liquid_address_ok("bc1qxyz")
