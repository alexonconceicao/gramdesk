#!/usr/bin/env python3
"""gramdesk-follows: checagem de seguidores pelo Gramdesk aberto (somente leitura).

Uso:
  gramdesk-follows status                      # resumo da conta: seguidores, não seguidores, famosos, lote
  gramdesk-follows check                       # refaz a checagem e registra quem deixou de seguir
  gramdesk-follows list nao-seguidores|famosos|deixaram [--json]
  Opção global: --account NOME (padrão: a conta selecionada na janela)

Feito para rodar uma vez por dia (cron ou timer do systemd). A checagem lê as listas de seguidores e
de seguindo no ritmo do app (uma requisição por vez, intervalo aleatório, tetos por hora e por dia) e
para ao primeiro sinal de limite do Instagram. Com a checagem diária ligada nas Preferências e uma
checagem anterior feita no app, roda sem pedir aprovação; senão pede na janela.

Deixar de seguir só é feito pela janela do app, com confirmação. Este comando nunca remove ninguém.

Códigos de saída: 0 ok · 1 erro · 3 app fechado, bloqueado ou conta sem login · 4 resfriamento ou
bloqueio detectado · 5 teto diário ou por hora (o progresso fica salvo) · 6 outra tarefa em andamento ·
7 recusado na janela.

Só usa a biblioteca padrão. Nunca abre o app.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path

APP_ID = "io.github.alexonconceicao.Gramdesk"
EXIT_OK, EXIT_ERR, EXIT_APP = 0, 1, 3
KNOWN_CODES = (1, 3, 4, 5, 6, 7)
LOGIN_NEEDED = "Abra o Gramdesk e entre na conta antes."
CHECK_TIMEOUT = 4 * 3600.0  # contas grandes levam tempo no ritmo humano


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def socket_paths() -> list[Path]:
    env = os.environ.get("GRAMDESK_SOCKET", "")
    if env:
        return [Path(env)]
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime:
        return []
    return [Path(runtime) / "app" / APP_ID / "gramdesk.sock", Path(runtime) / "gramdesk.sock"]


def talk(payload: bytes, timeout: float) -> bytes | None:
    """Envia uma linha e lê a resposta (uma linha). None = app fechado."""
    if not hasattr(socket, "AF_UNIX"):
        return None
    for path in socket_paths():
        if not path.exists():
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sk:
                sk.settimeout(3.0)
                sk.connect(str(path))
                sk.sendall(payload)
                sk.settimeout(timeout)
                buf = b""
                while not buf.endswith(b"\n") and len(buf) < 32_000_000:
                    try:
                        chunk = sk.recv(65536)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                return buf
        except OSError:
            continue
    return None


def query_app() -> dict | None:
    raw = talk(b"STATUS\n", 3.0)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {"outdated": True}
    return data if isinstance(data, dict) else {"outdated": True}


def preflight(account: str) -> None:
    st = query_app()
    if st is None:
        eprint(LOGIN_NEEDED)
        sys.exit(EXIT_APP)
    if st.get("outdated") or st.get("proto") != 1:
        eprint("O Gramdesk aberto é de uma versão antiga. Feche e abra o app de novo.")
        sys.exit(EXIT_APP)
    if st.get("locked"):
        eprint("O Gramdesk está bloqueado. Desbloqueie o app antes.")
        sys.exit(EXIT_APP)
    if not isinstance(st.get("follows"), dict):
        eprint("O Gramdesk aberto não tem a tela de Seguidores (versão antiga). Atualize e reabra.")
        sys.exit(EXIT_APP)
    accs = [a for a in st.get("accounts") or [] if isinstance(a, dict)]
    acc = next((a for a in accs if a.get("name") == account), None) if account else next((a for a in accs if a.get("current")), None)
    if acc is None or not acc.get("logged_in"):
        eprint(LOGIN_NEEDED)
        sys.exit(EXIT_APP)


def request(op: str, account: str, timeout: float = 30.0, **params) -> dict:
    preflight(account)
    req = {"id": secrets.token_hex(8), "op": op, "account": account, **params}
    raw = talk(b"F " + json.dumps(req).encode("utf-8") + b"\n", timeout)
    if not raw:
        eprint("O Gramdesk não respondeu (app fechado ou tempo esgotado).")
        sys.exit(EXIT_ERR)
    try:
        rep = json.loads(raw.decode("utf-8"))
    except ValueError:
        eprint("Resposta inválida do Gramdesk.")
        sys.exit(EXIT_ERR)
    if not rep.get("ok"):
        eprint(rep.get("message") or "Falhou.")
        code = rep.get("code")
        sys.exit(code if code in KNOWN_CODES else EXIT_ERR)
    return rep


def when(ts) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "nunca"


def print_summary(d: dict) -> None:
    print(f"Conta: {d.get('account', '?')}" + ("" if d.get("logged_in", True) else " (sem login)"))
    print(f"Última checagem: {when(d.get('taken_at'))}  ({d.get('checks', 0)} no total)")
    print(f"Seguidores: {d.get('followers', 0)} · seguindo: {d.get('following', 0)}")
    print(f"Não seguem de volta: {d.get('non_followers', 0)} (comuns {d.get('common', 0)}, famosos {d.get('famous', 0)}, "
          f"sem contagem {d.get('unknown', 0)}, mantidos {d.get('keep', 0)})")
    print(f"Deixaram de seguir (lista permanente): {d.get('unfollowers', 0)}")
    b = d.get("batch")
    if b:
        print(f"Lote salvo: {b['done']} de {b['total']} removidos ({b['mode']})")
    if d.get("partial"):
        print("Há uma checagem parcial salva; a próxima continua dela.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gramdesk-follows", description="Checagem de seguidores pelo Gramdesk aberto.")
    ap.add_argument("--account", default="", help="nome da conta no app (padrão: a selecionada)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check")
    lp = sub.add_parser("list")
    lp.add_argument("what", choices=("nao-seguidores", "famosos", "deixaram"))
    lp.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        print_summary(request("status", a.account)["data"])
    elif a.cmd == "check":
        eprint("Checando seguidores no ritmo do app (pode levar vários minutos)...")
        rep = request("check", a.account, CHECK_TIMEOUT)
        print(rep.get("message", ""))
        print_summary(rep.get("data") or {})
    else:
        data = request("list", a.account, 60.0, what=a.what)["data"]
        items = data.get("items") or []
        if a.json:
            print(json.dumps(items, ensure_ascii=False, indent=1))
        else:
            for it in items:
                if a.what == "deixaram":
                    extra = f"deixou em {when(it.get('lost_at'))}" + (f", voltou em {when(it['back_at'])}" if it.get("back_at") else "")
                else:
                    c = it.get("count")
                    extra = f"{c} seguidores" if isinstance(c, int) else "sem contagem"
                print(f"@{it.get('u')}\t{it.get('n', '')}\t{extra}")
            eprint(f"{len(items)} perfis (checagem de {when(data.get('taken_at'))}).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
