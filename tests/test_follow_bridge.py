"""Ponte de seguidores do inject.js no node: requisição certa, sessão pelos cookies e bloqueio detectado."""

import json
import shutil
import subprocess
from importlib import resources

import pytest

HARNESS = r"""
const fs = require('fs');
const [srcFile, inFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
globalThis.window = globalThis;
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), method: (init && init.method) || 'GET', headers: (init && init.headers) || {},
               body: (init && init.body) || '' });
  const r = input.response;
  return { status: r.status, ok: r.status >= 200 && r.status < 300, redirected: false, url: String(url),
           text: async () => r.body };
};
globalThis.XMLHttpRequest = function () {};
XMLHttpRequest.prototype.open = function () {};
XMLHttpRequest.prototype.send = function () {};
globalThis.location = { pathname: '/', href: 'https://www.instagram.com/' };
globalThis.document = { cookie: input.cookie, querySelectorAll: () => [], querySelector: () => null,
                        documentElement: { innerHTML: input.html || '' }, hidden: false };
globalThis.innerWidth = 1280; globalThis.innerHeight = 900;
eval(fs.readFileSync(srcFile, 'utf8'));
const b = window['__gd_test'];
(async () => {
  const out = { accepted: b.follow('0123456789abcdef', input.op, input.arg) };
  for (let i = 0; i < 200 && out.accepted; i++) {
    const r = b.take('0123456789abcdef');
    if (r) { out.result = JSON.parse(r); break; }
    await new Promise((ok) => setTimeout(ok, 10));
  }
  out.calls = calls;
  process.stdout.write(JSON.stringify(out));
})();
"""

COOKIE = "csrftoken=abc123; ds_user_id=4242; mid=x"


PAGE_HTML = '{"www_claim":"hmac.AAA","rollout_hash":"1011","ASBD_ID":"129477"}'


def run(tmp_path, op, arg, status=200, body="{}", cookie=COOKIE, html=PAGE_HTML):
    if not shutil.which("node"):
        pytest.skip("node ausente")
    src = resources.files("gramdesk").joinpath("inject.js").read_text(encoding="utf-8")
    src = (src.replace("__GD_KEY__", "__gd_test").replace("__GD_GHOST__", "false")
           .replace("__GD_TOKEN__", "t" * 32).replace("__GD_BUTTONS__", "false"))
    (tmp_path / "inject.js").write_text(src, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps({"op": op, "arg": arg, "cookie": cookie,
                                                  "html": html, "response": {"status": status, "body": body}}), encoding="utf-8")
    (tmp_path / "h.js").write_text(HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(tmp_path / "h.js"), str(tmp_path / "inject.js"), str(tmp_path / "in.json")],
                       capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout)


def test_followers_page(tmp_path):
    body = json.dumps({"users": [{"pk": 11, "username": "ana", "full_name": "Ana", "is_verified": True,
                                  "profile_pic_url": "https://x"}], "next_max_id": "QVFE", "status": "ok"})
    out = run(tmp_path, "followers", "QVFD", body=body)
    assert len(out["calls"]) == 1
    url = out["calls"][0]["url"]
    assert url.startswith("/api/v1/friendships/4242/followers/?count=50") and "max_id=QVFD" in url
    assert out["result"] == {"ok": True, "data": {"users": [{"pk": "11", "username": "ana", "full_name": "Ana",
                                                            "is_verified": True, "is_private": False, "anon_pic": False}],
                                                 "cursor": "QVFE", "more": True}}


def test_unfollow_posts_with_csrf_and_checks_result(tmp_path):
    out = run(tmp_path, "unfollow", "777", body=json.dumps({"friendship_status": {"following": False}, "status": "ok"}))
    c = out["calls"][0]
    assert c["method"] == "POST" and c["url"] == "/api/v1/friendships/destroy/777/"
    assert c["headers"]["X-ASBD-ID"] and c["headers"]["X-Instagram-AJAX"] and c["headers"]["X-IG-WWW-Claim"]
    assert c["headers"]["X-CSRFToken"] == "abc123" and "user_id=777" in c["body"]
    assert out["result"] == {"ok": True, "data": {"ok": True}}


def test_unfollow_not_confirmed_is_error(tmp_path):
    out = run(tmp_path, "unfollow", "777", body=json.dumps({"friendship_status": {"following": True}, "status": "ok"}))
    assert out["result"]["ok"] is False and "não confirmou" in out["result"]["error"]


def test_rate_limit_is_reported_as_block(tmp_path):
    out = run(tmp_path, "unfollow", "777", status=429, body="Please wait a few minutes before you try again.")
    assert out["result"]["ok"] is False and out["result"]["blocked"]["status"] == 429


def test_invalid_args_and_no_session_never_hit_network(tmp_path):
    assert run(tmp_path, "unfollow", "abc")["calls"] == []
    out = run(tmp_path, "following", "", cookie="mid=x")
    assert out["calls"] == [] and out["result"]["error"] == "sem sessão"
    assert run(tmp_path, "apagar", "1")["accepted"] is False


def test_remove_follower_checks_followed_by(tmp_path):
    out = run(tmp_path, "remove_follower", "55", body=json.dumps({"friendship_status": {"followed_by": False}, "status": "ok"}))
    c = out["calls"][0]
    assert c["method"] == "POST" and c["url"] == "/api/v1/friendships/remove_follower/55/"
    assert out["result"] == {"ok": True, "data": {"ok": True}}
    bad = run(tmp_path, "remove_follower", "55", body=json.dumps({"friendship_status": {"followed_by": True}, "status": "ok"}))
    assert bad["result"]["ok"] is False


def test_unfollow_fail_body_is_reported_as_block_with_status(tmp_path):
    """200 com status fail: o JS devolve 'blocked' com a mensagem, para o app registrar o motivo."""
    out = run(tmp_path, "unfollow", "777", body=json.dumps({"status": "fail", "message": "Something went wrong"}))
    assert out["result"]["blocked"]["status"] == 200 and "Something" in out["result"]["blocked"]["text"]


def test_html_response_is_flagged_not_dumped(tmp_path):
    """Página HTML no lugar do JSON: motivo curto e marca html, sem despejar o CSS do site."""
    page = "<!DOCTYPE html><html><style>:root{--fds-black:#000}</style><body>x</body></html>"
    out = run(tmp_path, "unfollow", "777", body=page)
    blk = out["result"]["blocked"]
    assert blk["html"] is True and blk["text"] == "o site respondeu com a página em vez dos dados"


def test_unfollow_confirms_when_body_has_no_state(tmp_path):
    """Resposta sem friendship_status: confere a relação antes de dar como feita."""
    out = run(tmp_path, "unfollow", "777", body=json.dumps({"status": "ok"}))
    assert [c["url"] for c in out["calls"]] == ["/api/v1/friendships/destroy/777/", "/api/v1/friendships/show/777/"]
    assert out["result"]["data"]["checked"] is True


def test_headers_come_from_the_page(tmp_path):
    out = run(tmp_path, "following", "", body=json.dumps({"users": [], "status": "ok"}))
    h = out["calls"][0]["headers"]
    assert h["X-IG-WWW-Claim"] == "hmac.AAA" and h["X-Instagram-AJAX"] == "1011" and h["X-ASBD-ID"] == "129477"
    fallback = run(tmp_path, "following", "", body=json.dumps({"users": [], "status": "ok"}), html="")
    assert fallback["calls"][0]["headers"]["X-IG-WWW-Claim"] == "0"


UI_HARNESS = r"""
const fs = require('fs');
const [srcFile, inFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
globalThis.window = globalThis;
globalThis.fetch = async () => { throw new Error('sem rede'); };
globalThis.XMLHttpRequest = function () {};
XMLHttpRequest.prototype.open = function () {};
XMLHttpRequest.prototype.send = function () {};
globalThis.location = { pathname: input.path, href: 'https://www.instagram.com' + input.path };
const clicked = [];
let state = input.state;           // 'following' | 'follow'
function el(text, opts) {
  opts = opts || {};
  return {
    textContent: text, offsetParent: {}, getBoundingClientRect: () => ({ width: 90, height: 32 }),
    click() { clicked.push(text); if (opts.sets) state = opts.sets; if (opts.opens) dialogOpen = opts.opens; },
  };
}
let dialogOpen = null;
globalThis.document = {
  cookie: 'csrftoken=a; ds_user_id=1',
  documentElement: { innerHTML: '' },
  hidden: false,
  querySelector: (sel) => (sel === 'main' ? mainNode : null),
  querySelectorAll(sel) {
    if (sel === '[role="dialog"]') return dialogOpen ? [dialogOpen] : [];
    return [];
  },
};
const mainNode = {
  querySelectorAll() {
    return state === 'following'
      ? [el('Seguindo', { opens: dialogNode }), el('Mais opções', { opens: optionsNode })]
      : [el('Seguir')];
  },
};
const dialogNode = { querySelectorAll: () => [el('Deixar de seguir', { sets: 'follow' }), el('Cancelar')] };
const optionsNode = { querySelectorAll: () => [el('Remover seguidor', { opens: confirmNode }), el('Denunciar')] };
const confirmNode = { querySelectorAll: () => [el('Remover')] };
globalThis.innerWidth = 1280; globalThis.innerHeight = 900;
eval(fs.readFileSync(srcFile, 'utf8'));
const b = window['__gd_test'];
(async () => {
  const out = { accepted: b.follow('0123456789abcdef', input.op, input.arg) };
  for (let i = 0; i < 400 && out.accepted; i++) {
    const r = b.take('0123456789abcdef');
    if (r) { out.result = JSON.parse(r); break; }
    await new Promise((ok) => setTimeout(ok, 10));
  }
  out.clicked = clicked;
  process.stdout.write(JSON.stringify(out));
})();
"""


def run_ui(tmp_path, op, arg, path="/thy_garcia11/", state="following"):
    if not shutil.which("node"):
        pytest.skip("node ausente")
    src = resources.files("gramdesk").joinpath("inject.js").read_text(encoding="utf-8")
    src = (src.replace("__GD_KEY__", "__gd_test").replace("__GD_GHOST__", "false")
           .replace("__GD_TOKEN__", "t" * 32).replace("__GD_BUTTONS__", "false"))
    (tmp_path / "inject.js").write_text(src, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps({"op": op, "arg": arg, "path": path, "state": state}), encoding="utf-8")
    (tmp_path / "ui.js").write_text(UI_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(tmp_path / "ui.js"), str(tmp_path / "inject.js"), str(tmp_path / "in.json")],
                       capture_output=True, text=True, timeout=60, check=True)
    return json.loads(r.stdout)


def test_ui_unfollow_clicks_button_and_dialog(tmp_path):
    out = run_ui(tmp_path, "ui_unfollow", "thy_garcia11")
    assert out["clicked"] == ["Seguindo", "Deixar de seguir"]
    assert out["result"] == {"ok": True, "data": {"ok": True}}


def test_ui_unfollow_when_already_not_following(tmp_path):
    out = run_ui(tmp_path, "ui_unfollow", "thy_garcia11", state="follow")
    assert out["clicked"] == [] and out["result"]["data"]["already"] is True


def test_ui_action_requires_the_profile_open(tmp_path):
    out = run_ui(tmp_path, "ui_unfollow", "thy_garcia11", path="/outro/")
    assert out["result"]["ok"] is False and "não está aberto" in out["result"]["error"]
    assert run_ui(tmp_path, "ui_unfollow", "não-vale")["result"]["error"] == "perfil inválido"


def test_ui_remove_follower_uses_options_menu(tmp_path):
    out = run_ui(tmp_path, "ui_remove_follower", "thy_garcia11")
    assert out["clicked"] == ["Mais opções", "Remover seguidor", "Remover"]
    assert out["result"]["ok"] is True
