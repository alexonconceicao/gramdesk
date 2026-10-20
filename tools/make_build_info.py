#!/usr/bin/env python3
"""Gera gramdesk/_build_info.json para builds de release (arquivo fora do git).

Lê variáveis de ambiente, normalmente vindas de segredos do pipeline:
  GRAMDESK_DONATION_URL            link https da página de doação (obrigatório para ligar)
  GRAMDESK_DONATION_TITLE          título do convite (opcional)
  GRAMDESK_DONATION_TEXT           texto do convite (opcional)
  GRAMDESK_DONATION_INTERVAL_DAYS  dias entre convites (opcional, padrão 30)
  GRAMDESK_DONATION_DELAY_DAYS     dias de uso antes do primeiro convite (opcional, padrão 3)
Sem GRAMDESK_DONATION_URL, remove o arquivo e o recurso fica desligado.
Nunca imprime o link.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

target = Path(__file__).resolve().parents[1] / "gramdesk" / "_build_info.json"
url = os.environ.get("GRAMDESK_DONATION_URL", "").strip()
if not url:
    target.unlink(missing_ok=True)
    print("build info: doação desligada")
    sys.exit(0)
p = urlsplit(url)
if p.scheme != "https" or not p.hostname:
    sys.exit("GRAMDESK_DONATION_URL precisa ser https")
don = {"url": url}
for env, key in (("GRAMDESK_DONATION_TITLE", "title"), ("GRAMDESK_DONATION_TEXT", "text")):
    if os.environ.get(env, "").strip():
        don[key] = os.environ[env].strip()
for env, key in (("GRAMDESK_DONATION_INTERVAL_DAYS", "interval_days"), ("GRAMDESK_DONATION_DELAY_DAYS", "delay_days")):
    if os.environ.get(env, "").strip().isdigit():
        don[key] = int(os.environ[env])
target.write_text(json.dumps({"donation": don}, ensure_ascii=False), encoding="utf-8")
print("build info: doação ligada")
