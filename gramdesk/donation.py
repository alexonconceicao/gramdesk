"""Convite opcional para apoiar o projeto.

O código não contém link nem endereço de doação. A configuração entra só nos
builds de release, num arquivo gerado na hora do build (_build_info.json, fora
do git) a partir de segredos do pipeline. Sem esse arquivo o recurso fica
desligado. O app nunca consulta servidor para isso.
"""

from __future__ import annotations

import json
import re
import time
from importlib import resources
from urllib.parse import urlsplit

DAY = 86400
# Endereço Liquid (confidencial lq1/ex1 em bech32/blech32, ou legado base58). Só formato, sem rede.
LIQUID_RE = re.compile(r"(?:(?:lq1|ex1)[02-9ac-hj-np-z]{38,110}|[VGHQ][1-9A-HJ-NP-Za-km-z]{33,90})")
_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BLECH_GEN = (0x7D52FBA40BD886, 0x5E8DBF1A03950C, 0x1C3A3C74072A18, 0x385D72FA0E5139, 0x7093E5A608865B)


def _blech32_polymod(values) -> int:
    c = 1
    for v in values:
        top = c >> 55
        c = ((c & 0x7FFFFFFFFFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                c ^= _BLECH_GEN[i]
    return c


def liquid_address_ok(addr: str) -> bool:
    """Formato + checksum blech32/blech32m para endereços confidenciais (lq1/ex1)."""
    if not LIQUID_RE.fullmatch(addr or ""):
        return False
    if addr[:3] not in ("lq1", "ex1"):
        return True  # legado base58: só o formato
    hrp, data = addr[:2], [_B32.index(ch) for ch in addr[3:]]
    pm = _blech32_polymod([ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + data)
    return pm == (1 if data[0] == 0 else 0x455972A3350F7A1)


DEFAULTS = {
    "title": "Apoie o Gramdesk",
    "text": "O Gramdesk é gratuito, sem anúncios e sem coleta de dados. "
            "Se ele te ajuda no dia a dia, considere apoiar o projeto com Pix ou cripto.",
    "button": "Apoiar o projeto",
    "interval_days": 30,
    "delay_days": 3,
}


def _load() -> dict | None:
    try:
        raw = resources.files(__package__).joinpath("_build_info.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (FileNotFoundError, OSError, ValueError, ModuleNotFoundError):
        return None
    if not isinstance(data, dict):
        return None
    d = data.get("donation")
    if not isinstance(d, dict):
        return None
    url = str(d.get("url", "") or "")
    if url:
        p = urlsplit(url)
        if p.scheme != "https" or not p.hostname or p.username or p.password or len(url) > 500:
            url = ""
    liquid = str(d.get("liquid", "") or "").strip()
    if liquid and not liquid_address_ok(liquid):
        liquid = ""
    if not url and not liquid:
        return None
    out = dict(DEFAULTS)
    for key in ("title", "text", "button"):
        if isinstance(d.get(key), str) and d[key].strip():
            out[key] = d[key].strip()[: 120 if key != "text" else 600]
    for key, lo, hi in (("interval_days", 1, 365), ("delay_days", 0, 60)):
        if isinstance(d.get(key), int) and not isinstance(d.get(key), bool):
            out[key] = max(lo, min(hi, d[key]))
    out["url"] = url
    out["liquid"] = liquid
    out["liquid_assets"] = str(d.get("liquid_assets") or "DePix, L-BTC e USDT")[:80]
    return out


CONFIG = _load()


def enabled() -> bool:
    return CONFIG is not None


def should_show(state: dict, now: float | None = None) -> bool:
    if CONFIG is None or state.get("never"):
        return False
    now = now or time.time()
    first = state.get("first_seen") or now
    if now - first < CONFIG["delay_days"] * DAY:
        return False
    return now - (state.get("last_shown") or 0) >= CONFIG["interval_days"] * DAY
