"""Convite opcional para apoiar o projeto.

O código não contém link nem endereço de doação. A configuração entra só nos
builds de release, num arquivo gerado na hora do build (_build_info.json, fora
do git) a partir de segredos do pipeline. Sem esse arquivo o recurso fica
desligado. O app nunca consulta servidor para isso.
"""

from __future__ import annotations

import json
import time
from importlib import resources
from urllib.parse import urlsplit

DAY = 86400
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
    url = str(d.get("url", ""))
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or len(url) > 500:
        return None
    out = dict(DEFAULTS)
    for key in ("title", "text", "button"):
        if isinstance(d.get(key), str) and d[key].strip():
            out[key] = d[key].strip()[: 120 if key != "text" else 600]
    for key, lo, hi in (("interval_days", 1, 365), ("delay_days", 0, 60)):
        if isinstance(d.get(key), int) and not isinstance(d.get(key), bool):
            out[key] = max(lo, min(hi, d[key]))
    out["url"] = url
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
