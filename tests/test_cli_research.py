"""CLI gramdesk-research: nunca abre o app, respeita resfriamento e cota (sem rede)."""

import importlib.util
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="socket Unix")


@pytest.fixture
def cli(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("gd_cli", ROOT / "tools" / "gramdesk_research.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def forbidden(*a, **k):  # abrir o app (ou qualquer processo) é proibido no fluxo de coleta
        raise AssertionError(f"tentou executar processo: {a}")

    monkeypatch.setattr(mod.subprocess, "Popen", forbidden)
    monkeypatch.setattr(mod.subprocess, "run", forbidden)
    monkeypatch.setattr(mod, "research_root", lambda: tmp_path / "Pesquisas")
    monkeypatch.setenv("GRAMDESK_SOCKET", str(tmp_path / "gd.sock"))
    return mod


class FakeApp:
    """Servidor no lugar da janela aberta: responde STATUS e guarda os pedidos."""

    def __init__(self, path: Path, status: dict | None):
        self.status = status
        self.got: list[bytes] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(4)
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(65536)
                self.got.append(data)
                if data.strip() == b"STATUS" and self.status is not None:
                    conn.sendall(json.dumps(self.status).encode() + b"\n")

    def close(self):
        self.srv.close()


def _status(logged=True, rate=None, running=False, locked=False):
    return {"proto": 1, "app": "0.8.0", "locked": locked, "research_running": running, "limits": {},
            "accounts": [{"id": "a1", "name": "Pessoal", "open": True, "logged_in": logged, "current": True,
                          "rate": rate or {"ok": True}}]}


def _main(cli, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["gramdesk-research", *argv])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_app_closed_never_opens_app(cli, monkeypatch, capsys):
    code = _main(cli, monkeypatch, ["@perfil", "--no-analyze"])
    assert code == 3
    assert "Abra o Gramdesk e entre na conta antes de pesquisar" in capsys.readouterr().err


def test_not_logged_in_refuses(cli, monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(logged=False))
    try:
        assert _main(cli, monkeypatch, ["@perfil", "--no-analyze"]) == 3
    finally:
        app.close()
    assert "entre na conta" in capsys.readouterr().err
    assert all(not g.startswith(b"R ") for g in app.got)  # nenhum pedido de coleta enviado


def test_old_app_without_status_reply(cli, monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", None)
    try:
        assert _main(cli, monkeypatch, ["@perfil", "--no-analyze"]) == 3
    finally:
        app.close()
    assert "versão antiga" in capsys.readouterr().err


def test_locked_app_refuses(cli, monkeypatch, tmp_path):
    app = FakeApp(tmp_path / "gd.sock", {"proto": 1, "locked": True})
    try:
        assert _main(cli, monkeypatch, ["@perfil"]) == 3
    finally:
        app.close()


def test_cooldown_exits_with_wait_message(cli, monkeypatch, tmp_path, capsys):
    until = time.time() + 1800
    rate = {"ok": False, "reason": "cooldown", "until": until,
            "message": "O Instagram pediu para ir mais devagar. Resfriamento ativo: aguarde até 15:30 (faltam 30 min)."}
    app = FakeApp(tmp_path / "gd.sock", _status(rate=rate))
    try:
        assert _main(cli, monkeypatch, ["@perfil", "--no-analyze"]) == 4
    finally:
        app.close()
    assert "aguarde até" in capsys.readouterr().err


def test_quota_exits_5_and_busy_exits_6(cli, monkeypatch, tmp_path):
    rate = {"ok": False, "reason": "researches", "until": time.time() + 600, "message": "Cota diária atingida (3 de 3 pesquisas)."}
    app = FakeApp(tmp_path / "gd.sock", _status(rate=rate))
    try:
        assert _main(cli, monkeypatch, ["@perfil", "--no-analyze"]) == 5
        app.status = _status(running=True)
        assert _main(cli, monkeypatch, ["@perfil", "--no-analyze"]) == 6
    finally:
        app.close()


def test_ok_sends_request_over_socket(cli, monkeypatch, tmp_path):
    app = FakeApp(tmp_path / "gd.sock", _status())
    monkeypatch.setattr(cli, "wait_collect", lambda rid, timeout: (_ for _ in ()).throw(SystemExit(0)))
    try:
        assert _main(cli, monkeypatch, ["@perfil", "--no-analyze", "--posts", "30"]) == 0
    finally:
        app.close()
    reqs = [json.loads(g[2:]) for g in app.got if g.startswith(b"R ")]
    assert len(reqs) == 1 and reqs[0]["username"] == "perfil" and reqs[0]["opts"]["posts"] == 30


def test_status_option(cli, monkeypatch, tmp_path, capsys):
    rate = {"ok": False, "reason": "cooldown", "until": time.time() + 3600, "level": 2,
            "message": "Resfriamento ativo: aguarde até 16:00 (faltam 1 h).", "researches_today": 1, "items_today": 20}
    app = FakeApp(tmp_path / "gd.sock", _status(rate=rate))
    try:
        assert _main(cli, monkeypatch, ["--status"]) == 4
    finally:
        app.close()
    out = capsys.readouterr().out
    assert "Pessoal (atual): logada" in out and "nível de resfriamento 2" in out and "aguarde até 16:00" in out


def test_status_offline_reads_state_file(cli, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GRAMDESK_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "data").mkdir(parents=True)
    (tmp_path / "home" / "config").mkdir(parents=True)
    (tmp_path / "home" / "data" / "ratelimit.json").write_text(json.dumps(
        {"accounts": {"a1": {"level": 1, "cooldown_until": time.time() + 900, "day": time.strftime("%Y-%m-%d"),
                             "researches": 1, "items": 12}}}))
    (tmp_path / "home" / "config" / "config.json").write_text(json.dumps({"accounts": [{"id": "a1", "name": "Pessoal"}]}))
    assert _main(cli, monkeypatch, ["--status"]) == 3
    out = capsys.readouterr().out
    assert "Gramdesk fechado" in out and "Pessoal: hoje 1 pesquisas, 12 itens · resfriamento até" in out


def test_app_refuses_research_flag_without_window():
    """`gramdesk --research` sem janela aberta não pode abrir o app (checagem estática do main)."""
    src = (ROOT / "gramdesk" / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def main():"):]
    fwd = body.index("_forward_to_running(sys.argv[1:])")
    refuse = body.index("if _research_args(sys.argv[1:]):")
    window = body.index("win = MainWindow(cfg)")
    assert fwd < refuse < window
    assert "sys.exit(3)" in body[refuse:window]
