"""CLI gramdesk-follows: nunca abre o app, nunca remove ninguém, repassa códigos de saída (socket falso)."""

import importlib.util
import json
import os
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="socket Unix")

STATUS = {"proto": 1, "locked": False, "follows": {"proto": 1, "busy": False},
          "accounts": [{"id": "a1", "name": "Pessoal", "current": True, "logged_in": True}]}


@pytest.fixture
def cli(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("gd_follows", ROOT / "tools" / "gramdesk_follows.py")
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
    def __init__(self, path: Path, status, reply=None):
        self.status, self.reply, self.got = status, reply, []
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
                elif data.startswith(b"F ") and self.reply is not None:
                    req = json.loads(data[2:])
                    conn.sendall(json.dumps(dict(self.reply, id=req["id"])).encode() + b"\n")

    def requests(self):
        return [json.loads(g[2:]) for g in self.got if g.startswith(b"F ")]


def test_app_closed_exits_3(cli, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["check"])
    assert e.value.code == 3 and "Abra o Gramdesk" in capsys.readouterr().err


def test_locked_or_old_app_never_sends_request(cli, tmp_path):
    app = FakeApp(tmp_path / "gd.sock", {"proto": 1, "locked": True})
    with pytest.raises(SystemExit) as e:
        cli.main(["status"])
    assert e.value.code == 3 and app.requests() == []


def test_old_app_without_follows(cli, tmp_path):
    st = dict(STATUS)
    st.pop("follows")
    app = FakeApp(tmp_path / "gd.sock", st)
    with pytest.raises(SystemExit) as e:
        cli.main(["check"])
    assert e.value.code == 3 and app.requests() == []


def test_check_passes_cooldown_code(cli, tmp_path):
    app = FakeApp(tmp_path / "gd.sock", STATUS, {"proto": 1, "ok": False, "code": 4, "message": "Resfriamento ativo"})
    with pytest.raises(SystemExit) as e:
        cli.main(["check"])
    assert e.value.code == 4
    assert [r["op"] for r in app.requests()] == ["check"]


def test_list_prints_unfollowers(cli, tmp_path, capsys):
    items = [{"pk": "1", "u": "bia", "n": "Bia", "lost_at": 1_790_000_000, "back_at": 0}]
    FakeApp(tmp_path / "gd.sock", STATUS, {"proto": 1, "ok": True, "code": 0, "data": {"items": items, "taken_at": 1}})
    assert cli.main(["--account", "Pessoal", "list", "deixaram"]) == 0
    assert "@bia" in capsys.readouterr().out


def test_cli_has_no_unfollow_command():
    src = (ROOT / "tools" / "gramdesk_follows.py").read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]
    assert '"unfollow"' not in body and "destroy" not in body
