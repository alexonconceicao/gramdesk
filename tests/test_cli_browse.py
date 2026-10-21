"""CLI gramdesk-browse: uma ação por chamada, nunca abre o app, códigos de saída (socket falso, sem rede)."""

import importlib.util
import json
import os
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="socket Unix")


@pytest.fixture
def cli(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("gd_browse", ROOT / "tools" / "gramdesk_browse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import subprocess

    def forbidden(*a, **k):
        raise AssertionError(f"tentou executar processo: {a}")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setenv("GRAMDESK_SOCKET", str(tmp_path / "gd.sock"))
    return mod


class FakeApp:
    """No lugar da janela aberta: responde STATUS e os pedidos 'B ' com uma resposta fixa."""

    def __init__(self, path: Path, status: dict | None, reply: dict | None = None):
        self.status = status
        self.reply = reply
        self.got: list[bytes] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(4)
        threading.Thread(target=self._loop, daemon=True).start()

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
                elif data.startswith(b"B ") and self.reply is not None:
                    req = json.loads(data[2:])
                    conn.sendall(json.dumps(dict(self.reply, id=req["id"])).encode() + b"\n")

    def requests(self) -> list[dict]:
        return [json.loads(g[2:]) for g in self.got if g.startswith(b"B ")]

    def close(self):
        self.srv.close()


def _status(logged=True, browse=None, busy=False, running=False, locked=False, with_browse=True):
    st = {"proto": 1, "app": "0.9.0", "locked": locked, "research_running": running, "limits": {},
          "accounts": [{"id": "a1", "name": "Pessoal", "open": True, "logged_in": logged, "current": True,
                        "rate": {"ok": True},
                        "browse": browse or {"ok": True, "wait": 0, "approved": True, "posts_today": 1,
                                             "posts_limit": 15, "actions_today": 3, "interval": 20, "jitter": 10}}]}
    if with_browse:
        st["browse"] = {"proto": 1, "busy": busy}
    return st


def _run(cli, argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    return e.value.code


def test_app_closed_exits_3_without_opening(cli, capsys):
    assert _run(cli, ["inspect", "https://www.instagram.com/p/AAA111aaa/"]) == 3
    assert "Abra o Gramdesk e entre na conta" in capsys.readouterr().err


def test_not_logged_in_exits_3_and_sends_nothing(cli, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(logged=False), {"ok": True, "code": 0})
    try:
        assert _run(cli, ["open", "@profa.ana"]) == 3
    finally:
        app.close()
    assert app.requests() == []
    assert "entre na conta" in capsys.readouterr().err


def test_old_app_without_browse_exits_3(cli, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(with_browse=False))
    try:
        assert _run(cli, ["grid"]) == 3
    finally:
        app.close()
    assert "0.9.0" in capsys.readouterr().err


def test_locked_exits_3(cli, tmp_path):
    app = FakeApp(tmp_path / "gd.sock", {"proto": 1, "locked": True})
    try:
        assert _run(cli, ["grid"]) == 3
    finally:
        app.close()


def test_cooldown_4_quota_5_busy_6(cli, tmp_path):
    cd = {"ok": False, "reason": "cooldown", "until": 0, "message": "Resfriamento ativo: aguarde até 15:30."}
    app = FakeApp(tmp_path / "gd.sock", _status(browse=cd), {"ok": True, "code": 0})
    try:
        assert _run(cli, ["inspect", "https://www.instagram.com/p/AAA111aaa/"]) == 4
        app.status = _status(browse={"ok": False, "reason": "posts", "until": 0, "message": "Teto diário de aberturas de post atingido."})
        assert _run(cli, ["inspect", "https://www.instagram.com/p/AAA111aaa/"]) == 5
        app.status = _status(busy=True)
        assert _run(cli, ["grid"]) == 6
        app.status = _status(running=True)
        assert _run(cli, ["grid"]) == 6
    finally:
        app.close()
    assert app.requests() == []


def test_invalid_url_rejected_locally(cli, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(), {"ok": True, "code": 0})
    try:
        assert _run(cli, ["inspect", "https://www.instagram.com/profa.ana/"]) == 1
        assert _run(cli, ["open", "https://www.instagram.com/accounts/edit/"]) == 1
    finally:
        app.close()
    assert app.requests() == []


def test_inspect_sends_one_action_and_prints_json(cli, tmp_path, capsys):
    data = {"type": "carousel", "caption": "Aula", "items_total": 3}
    app = FakeApp(tmp_path / "gd.sock", _status(), {"proto": 1, "ok": True, "code": 0, "data": data})
    try:
        assert _run(cli, ["inspect", "instagram.com/profa.ana/p/AAA111aaa/?x=1"]) == 0
    finally:
        app.close()
    reqs = app.requests()
    assert len(reqs) == 1
    assert reqs[0]["action"] == "inspect-post" and reqs[0]["url"] == "https://www.instagram.com/p/AAA111aaa/"
    assert len(reqs[0]["id"]) == 16
    assert json.loads(capsys.readouterr().out) == data


def test_wait_and_approval_are_announced(cli, tmp_path, capsys):
    b = {"ok": True, "wait": 12.4, "approved": False}
    app = FakeApp(tmp_path / "gd.sock", _status(browse=b), {"ok": True, "code": 0, "data": {}, "waited": 12.4})
    try:
        assert _run(cli, ["open", "@profa.ana"]) == 0
    finally:
        app.close()
    err = capsys.readouterr().err
    assert "aprove o pedido na janela" in err and "Aguardando 12 s" in err and "Esperou 12.4 s" in err


def test_grid_without_scroll_does_not_announce_wait(cli, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(browse={"ok": True, "wait": 15, "approved": True}),
                  {"ok": True, "code": 0, "data": {"items": []}})
    try:
        assert _run(cli, ["grid"]) == 0
        assert _run(cli, ["grid", "--scroll"]) == 0
    finally:
        app.close()
    reqs = app.requests()
    assert [r["scroll"] for r in reqs] == [False, True]
    assert capsys.readouterr().err.count("Aguardando") == 1


def test_app_side_codes_are_forwarded(cli, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(), {"ok": False, "code": 4, "message": "O Instagram pediu para ir mais devagar.",
                                                   "blocked": "rate-limit"})
    try:
        assert _run(cli, ["inspect", "https://www.instagram.com/p/AAA111aaa/"]) == 4
        app.reply = {"ok": False, "code": 7, "message": "Navegação recusada na janela do Gramdesk."}
        assert _run(cli, ["open", "@profa.ana"]) == 7
        app.reply = {"ok": False, "code": 99, "message": "estranho"}
        assert _run(cli, ["open", "@profa.ana"]) == 1
    finally:
        app.close()
    assert "mais devagar" in capsys.readouterr().err


def test_download_index_and_out_copy(cli, tmp_path, capsys):
    src = tmp_path / "Gramdesk" / "Pessoal" / "profa.ana"
    src.mkdir(parents=True)
    f = src / "profa.ana_AAA111aaa_02.jpg"
    f.write_bytes(b"jpeg")
    reply = {"ok": True, "code": 0, "message": "Salvo", "data": {"path": str(f), "index": 2, "items_total": 3}}
    app = FakeApp(tmp_path / "gd.sock", _status(), reply)
    out = tmp_path / "escolhidas"
    try:
        assert _run(cli, ["download", "https://www.instagram.com/p/AAA111aaa/", "--index", "2", "--out", str(out)]) == 0
        assert _run(cli, ["download", "https://www.instagram.com/p/AAA111aaa/", "--index", "0"]) == 1
    finally:
        app.close()
    reqs = app.requests()
    assert len(reqs) == 1 and reqs[0]["index"] == 2 and reqs[0]["action"] == "download-current"
    assert (out / f.name).read_bytes() == b"jpeg"
    assert json.loads(capsys.readouterr().out)["copied_to"] == str(out / f.name)


def test_status_subcommand(cli, tmp_path, capsys):
    app = FakeApp(tmp_path / "gd.sock", _status(browse={"ok": True, "wait": 8, "approved": False, "posts_today": 4,
                                                        "posts_limit": 15, "actions_today": 9, "interval": 20, "jitter": 10}))
    try:
        assert _run(cli, ["status"]) == 0
    finally:
        app.close()
    out = capsys.readouterr().out
    assert "Pessoal (atual): logada" in out and "4 de 15 posts abertos" in out and "pede aprovação" in out
    assert "próxima ação de navegação em 8 s" in out
    assert all(not g.startswith(b"B ") for g in app.got)


def test_no_batch_loop_in_cli():
    """Cada subcomando faz uma ação: o CLI envia no máximo um pedido 'B ' por execução."""
    src = (ROOT / "tools" / "gramdesk_browse.py").read_text(encoding="utf-8")
    assert src.count('talk(b"B "') == 1
    body = src[src.index("def request("):src.index("def out_json(")]
    assert "for " not in body and "while " not in body
