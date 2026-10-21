#!/usr/bin/env python3
"""gramdesk-browse: navegação passo a passo no Instagram pelo Gramdesk aberto, uma ação por chamada.

Em vez de ler o perfil em lote, pede ao app (já aberto e com a conta logada) que faça UMA coisa
na própria janela, como uma pessoa: abrir um perfil, ler a grade visível, abrir um post e ver o
conteúdo, baixar só aquele arquivo. Cada subcomando faz uma ação e sai; não existe laço em lote.

Uso:
  gramdesk-browse status
  gramdesk-browse open <@perfil|url>
  gramdesk-browse grid [--scroll]              # JSON com os links da grade (no máximo 1 rolagem)
  gramdesk-browse inspect <url-do-post>        # JSON: tipo, legenda, data, dimensões, URL da mídia
  gramdesk-browse download <url-do-post> [--index N] [--out PASTA]
  Opção global: --account NOME (padrão: a conta selecionada na janela)

Ritmo e proteção (decididos pelo app): intervalo mínimo entre ações de navegação (20 s + 0 a 10 s
aleatórios, contados do fim da ação anterior; se chamar antes, o app espera o restante), cada ação
conta como 1 item da cota diária, teto diário de aberturas de post (15), resfriamento após qualquer
sinal de bloqueio. A primeira ação de cada sessão do app pede aprovação na janela.

Códigos de saída: 0 ok · 1 erro · 3 app fechado, bloqueado, versão antiga ou conta sem login ·
4 resfriamento (ou bloqueio detectado agora) · 5 cota/teto diário ou por hora · 6 outra ação ou
pesquisa em andamento · 7 recusado na janela.

Só usa a biblioteca padrão. Nunca abre o app.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sys
import time
from pathlib import Path

APP_ID = "io.github.alexonconceicao.Gramdesk"
EXIT_OK, EXIT_ERR, EXIT_APP, EXIT_COOLDOWN, EXIT_QUOTA, EXIT_BUSY, EXIT_REFUSED = 0, 1, 3, 4, 5, 6, 7
LOGIN_NEEDED = "Abra o Gramdesk e entre na conta antes de navegar."
REPLY_TIMEOUT = 900.0  # aprovação na janela + intervalo mínimo + carregar + baixar

try:  # mesmas regras de URL do app (o app valida de novo)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gramdesk.browse import target_of  # noqa: E402
except Exception:  # noqa: BLE001
    target_of = None


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- socket
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
                while not buf.endswith(b"\n") and len(buf) < 4_000_000:
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


def pick_account(st: dict, name: str = "") -> dict | None:
    accs = [a for a in st.get("accounts") or [] if isinstance(a, dict)]
    if name:
        return next((a for a in accs if a.get("name") == name), None)
    return next((a for a in accs if a.get("current")), None)


def fmt_until(ts: float) -> str:
    now = time.time()
    same = time.strftime("%Y%m%d", time.localtime(ts)) == time.strftime("%Y%m%d", time.localtime(now))
    return time.strftime("%H:%M" if same else "%d/%m %H:%M", time.localtime(ts))


def evaluate(st: dict | None, account: str = "") -> tuple[int, str, dict | None]:
    """Pode pedir uma ação agora? (código, mensagem, conta). Não considera o intervalo mínimo (o app espera)."""
    if st is None:
        return EXIT_APP, LOGIN_NEEDED, None
    if st.get("outdated") or st.get("proto") != 1:
        return EXIT_APP, "O Gramdesk aberto é de uma versão antiga. Feche e abra o app de novo.", None
    if st.get("locked"):
        return EXIT_APP, "O Gramdesk está bloqueado. Desbloqueie o app e entre na conta antes de navegar.", None
    if not isinstance(st.get("browse"), dict):
        return EXIT_APP, "O Gramdesk aberto não tem navegação passo a passo (versão anterior à 0.9.0). Atualize e reabra.", None
    acc = pick_account(st, account)
    if acc is None or not acc.get("logged_in"):
        return EXIT_APP, LOGIN_NEEDED, acc
    if st["browse"].get("busy"):
        return EXIT_BUSY, "Já existe uma ação de navegação em andamento no Gramdesk.", acc
    if st.get("research_running"):
        return EXIT_BUSY, "Há uma pesquisa de perfil em andamento no Gramdesk.", acc
    b = acc.get("browse") or {}
    if not b.get("ok", True):
        code = EXIT_COOLDOWN if b.get("reason") == "cooldown" else EXIT_QUOTA
        msg = b.get("message") or "Limite atingido."
        if b.get("until") and "aguarde até" not in msg.lower():
            msg += f" Aguarde até {fmt_until(b['until'])}."
        return code, msg, acc
    return EXIT_OK, "", acc


# ---------------------------------------------------------------- ações
def request(action: str, account: str, nav: bool, **params) -> dict:
    """Confere o app, avisa sobre espera/aprovação, envia UMA ação e devolve a resposta (ou sai)."""
    code, msg, acc = evaluate(query_app(), account)
    if code:
        eprint(msg)
        sys.exit(code)
    b = (acc or {}).get("browse") or {}
    if not b.get("approved"):
        eprint("Primeira ação desta sessão: aprove o pedido na janela do Gramdesk.")
    wait = float(b.get("wait") or 0)
    if nav and wait > 0.5:
        eprint(f"Aguardando {int(wait + 0.5)} s (intervalo mínimo entre ações de navegação)...")
    req = {"id": secrets.token_hex(8), "action": action, "account": account, **params}
    raw = talk(b"B " + json.dumps(req).encode("utf-8") + b"\n", REPLY_TIMEOUT)
    if raw is None:
        eprint(LOGIN_NEEDED)
        sys.exit(EXIT_APP)
    try:
        rep = json.loads(raw.decode("utf-8"))
    except ValueError:
        eprint("Sem resposta do Gramdesk (fechado durante a ação ou versão antiga).")
        sys.exit(EXIT_ERR)
    if not isinstance(rep, dict):
        eprint("Resposta inválida do Gramdesk.")
        sys.exit(EXIT_ERR)
    if rep.get("waited"):
        eprint(f"Esperou {rep['waited']:g} s pelo intervalo mínimo.")
    if not rep.get("ok"):
        eprint(rep.get("message") or "A ação falhou.")
        c = rep.get("code")
        sys.exit(c if isinstance(c, int) and c in (1, 3, 4, 5, 6, 7) else EXIT_ERR)
    return rep


def out_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def check_url(text: str, post_only: bool) -> str:
    if target_of is None:
        return text
    t = target_of(text)
    if t is None:
        eprint("URL não permitida. Use @perfil, instagram.com/perfil/ ou instagram.com/p/CODIGO/.")
        sys.exit(EXIT_ERR)
    if post_only and t["kind"] != "post":
        eprint("Informe a URL de um post ou reel (instagram.com/p/CODIGO/).")
        sys.exit(EXIT_ERR)
    return t["url"]


def cmd_status(args) -> int:
    st = query_app()
    code, msg, acc = evaluate(st, args.account)
    if st is None:
        print("Gramdesk fechado. " + LOGIN_NEEDED)
        return EXIT_APP
    if code == EXIT_APP:
        print(msg)
        return code
    print(f"Gramdesk {st.get('app', '?')} aberto.")
    for a in st.get("accounts") or []:
        if not isinstance(a, dict):
            continue
        b = a.get("browse") or {}
        tag = " (atual)" if a.get("current") else ""
        login = "logada" if a.get("logged_in") else ("aberta sem login" if a.get("open") else "não aberta")
        line = (f"- {a.get('name')}{tag}: {login} · hoje {b.get('posts_today', 0)} de {b.get('posts_limit', '?')} "
                f"posts abertos, {b.get('actions_today', 0)} ações · "
                f"{'aprovada nesta sessão' if b.get('approved') else 'pede aprovação na 1ª ação'}")
        if b.get("wait"):
            line += f" · próxima ação de navegação em {int(b['wait'] + 0.5)} s"
        if b.get("message"):
            line += f" · {b['message']}"
        print(line)
    if acc is not None:
        b = acc.get("browse") or {}
        print(f"Intervalo mínimo: {b.get('interval', 20):g} s + 0 a {b.get('jitter', 10):g} s aleatórios.")
    if msg:
        print(msg)
    return code


def cmd_open(args) -> int:
    rep = request("open-url", args.account, True, url=check_url(args.target, False))
    out_json(rep.get("data") or {})
    return EXIT_OK


def cmd_grid(args) -> int:
    rep = request("list-grid", args.account, bool(args.scroll), scroll=bool(args.scroll))
    out_json(rep.get("data") or {})
    return EXIT_OK


def cmd_inspect(args) -> int:
    rep = request("inspect-post", args.account, True, url=check_url(args.url, True))
    out_json(rep.get("data") or {})
    return EXIT_OK


def cmd_download(args) -> int:
    params = {"url": check_url(args.url, True)}
    if args.index is not None:
        if not 1 <= args.index <= 50:
            eprint("Índice inválido (use 1 a 50).")
            return EXIT_ERR
        params["index"] = args.index
    out_dir = None
    if args.out:
        out_dir = Path(args.out).expanduser()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            eprint(f"Não deu para criar {out_dir}: {exc}")
            return EXIT_ERR
    rep = request("download-current", args.account, True, **params)
    data = dict(rep.get("data") or {})
    src = Path(str(data.get("path", "")))
    if out_dir is not None and src.is_file():
        dest = out_dir / src.name
        if dest.resolve() != src.resolve():
            shutil.copyfile(src, dest)  # só o conteúdo: sem metadados extras
        data["copied_to"] = str(dest)
    out_json(data)
    eprint(rep.get("message") or "")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gramdesk-browse",
        description="Navegação passo a passo pelo Gramdesk aberto: uma ação por chamada, no ritmo de uma pessoa.",
    )
    ap.add_argument("--account", default="", help="nome da conta no Gramdesk (padrão: a selecionada)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="app aberto, conta logada, cota, intervalo e aprovação").set_defaults(fn=cmd_status)
    p = sub.add_parser("open", help="abre um perfil ou post na janela do app")
    p.add_argument("target", help="@perfil, instagram.com/perfil/ ou URL de post/reel")
    p.set_defaults(fn=cmd_open)
    p = sub.add_parser("grid", help="lista os posts da grade do perfil aberto (JSON)")
    p.add_argument("--scroll", action="store_true", help="rola a página uma vez, devagar, antes de ler")
    p.set_defaults(fn=cmd_grid)
    p = sub.add_parser("inspect", help="abre o post e mostra tipo, legenda, data, dimensões e mídia (JSON)")
    p.add_argument("url")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("download", help="baixa UMA mídia do post (carrossel: --index N)")
    p.add_argument("url")
    p.add_argument("--index", type=int, default=None, help="item do carrossel, começando em 1")
    p.add_argument("--out", default="", help="copia o arquivo também para esta pasta")
    p.set_defaults(fn=cmd_download)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if os.name == "nt":
        eprint("gramdesk-browse ainda não funciona no Windows.")
        sys.exit(EXIT_APP)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
