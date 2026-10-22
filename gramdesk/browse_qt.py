"""Navegação passo a passo pedida pela linha de comando (gramdesk-browse), na janela do app.

Uma ação por pedido, uma por vez: abrir uma URL permitida, ler a grade do perfil (com no máximo
uma rolagem), inspecionar o post aberto ou baixar UMA mídia dele. Antes de cada ação o app
confere janela desbloqueada, conta logada, resfriamento, cotas e o intervalo mínimo entre ações
de navegação; a primeira ação de cada sessão pede aprovação na janela. Qualquer sinal de
bloqueio na página (checkpoint, "tente novamente mais tarde", tela de login) encerra a ação e
coloca a conta em resfriamento.
"""

from __future__ import annotations

import json
import random
import time
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, QUrl

from . import browse, ratelimit, security

# Códigos de saída combinados com a CLI.
OK, ERROR, APP, COOLDOWN, QUOTA, BUSY, REFUSED = 0, 1, 3, 4, 5, 6, 7
LOAD_TIMEOUT_MS = 30_000
READY_TIMEOUT_S = 15.0
DOWNLOAD_TIMEOUT_MS = 90_000  # um arquivo, uma requisição; sem resposta em 90 s, desiste
LOGIN_NEEDED = "Abra o Gramdesk e entre na conta antes de navegar."


class BrowseController(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.busy = False
        self.active: _Ctx | None = None  # referência forte: o PyQt guarda só referência fraca de métodos
        self.approved: set[str] = set()  # contas liberadas nesta sessão do app
        self.rng = random.Random()

    # ---------------------------------------------------------------- estado
    def account_status(self, acc_id: str) -> dict:
        st = self.win.rate.browse_status(acc_id, self.win.research_limits())
        st["approved"] = acc_id in self.approved
        return st

    # ---------------------------------------------------------------- entrada
    def handle(self, req: dict, reply: Callable[[dict], None]):
        """Pedido vindo do socket local (já validado como JSON). reply é chamado exatamente uma vez."""
        win = self.win
        rid = str(req.get("id", ""))
        action = str(req.get("action", ""))

        def answer(code: int, message: str = "", data: dict | None = None, **extra):
            reply({"proto": 1, "id": rid, "ok": code == OK, "code": code, "message": message[:600],
                   "data": data or {}, **extra})

        if not security.is_request_id(rid) or action not in browse.ACTIONS:
            answer(ERROR, "Pedido inválido.")
            return
        if win.outer.currentIndex() != 0:
            answer(APP, "O Gramdesk está bloqueado. Desbloqueie o app e entre na conta antes de navegar.")
            return
        if self.busy:
            answer(BUSY, "Já existe uma ação de navegação em andamento. Espere ela terminar.")
            return
        job = win.research_job
        if job is not None and job.state not in ("done", "error", "cancelled", "refused"):
            answer(BUSY, "Há uma pesquisa de perfil em andamento; a navegação espera ela terminar.")
            return
        s = win._research_session(str(req.get("account", ""))[:40])
        if s is None or s.profile is None or s.page is None or not s.logged_in:
            answer(APP, LOGIN_NEEDED)
            return

        target = None
        if action in browse.NAV_ACTIONS:
            target = browse.target_of(str(req.get("url", ""))[:2048])
            if target is None:
                answer(ERROR, "URL não permitida. Use um perfil (@perfil ou instagram.com/perfil/) ou um post/reel "
                              "(instagram.com/p/CODIGO/).")
                return
            if action != "open-url" and target["kind"] != "post":
                answer(ERROR, "Informe a URL de um post ou reel (instagram.com/p/CODIGO/).")
                return
        index = req.get("index")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 50):
            answer(ERROR, "Índice inválido (use 1 a 50).")
            return
        scroll = bool(req.get("scroll")) and action == "list-grid"
        current = s.page.url().toString()
        if action == "list-grid" and (browse.target_of(current) or {}).get("kind") != "profile":
            answer(ERROR, "A conta não está num perfil. Use primeiro: gramdesk-browse open @perfil")
            return
        post_open = bool(target and target["kind"] == "post" and
                         (action == "open-url" or not browse.same_post(current, target["code"])))
        navigation = action in browse.NAV_ACTIONS or scroll

        acc_id = s.account.id
        lim = win.research_limits()
        chk = win.rate.browse_check(acc_id, lim, post_open=post_open)
        if not chk["ok"]:
            win.rlog.write("nav-recusada", account=s.account.name, action=action, reason=chk["reason"])
            answer(COOLDOWN if chk["reason"] == "cooldown" else QUOTA, chk["message"], until=chk["until"])
            return

        self.busy = True
        self.active = _Ctx(self, s, rid, action, target, index, scroll, post_open, navigation, answer)
        QTimer.singleShot(0, self.active.approve)


class _Ctx:
    """Uma ação em andamento (encadeada por callbacks do Qt)."""

    def __init__(self, ctl: BrowseController, s, rid, action, target, index, scroll, post_open, navigation, answer):
        self.ctl, self.win, self.s = ctl, ctl.win, s
        self.rid, self.action, self.target = rid, action, target
        self.index, self.scroll = index, scroll
        self.post_open, self.navigation = post_open, navigation
        self._answer = answer
        self.waited = 0.0
        self.done = False
        self.acc_id = s.account.id
        self.acc_name = s.account.name

    # ---------------------------------------------------------------- fim
    def finish(self, code: int, message: str = "", data: dict | None = None, **extra):
        if self.done:
            return
        self.done = True
        self.ctl.busy = False
        if self.ctl.active is self:
            self.ctl.active = None
        if self.navigation and code not in (REFUSED,):
            self.win.rate.browse_touch(self.acc_id, self.win.research_limits(), rng=self.ctl.rng)
        self.win.rlog.write("nav-fim", account=self.acc_name, action=self.action, code=code)
        self._answer(code, message, data, waited=round(self.waited, 1), **extra)

    def lim(self) -> ratelimit.Limits:
        return self.win.research_limits()

    # ---------------------------------------------------------------- 1. aprovação
    def approve(self):
        if self.acc_id in self.ctl.approved:
            self.pace()
            return
        win = self.win
        win.raise_window()
        ok = win.confirm(
            "Permitir navegação passo a passo?",
            f"Um programa no seu computador (linha de comando) pediu para navegar no Instagram usando a conta "
            f"\"{self.acc_name}\": abrir perfis e posts nesta janela, um por vez, e baixar mídias escolhidas para "
            "Downloads/Gramdesk.\n\nCada ação respeita o intervalo mínimo e as cotas. A permissão vale até fechar "
            "o app. Se você não pediu isso, recuse.",
            "Permitir",
        )
        if not ok:
            win.rlog.write("nav-recusada", account=self.acc_name, action=self.action, reason="usuario")
            self.finish(REFUSED, "Navegação recusada na janela do Gramdesk.")
            return
        self.ctl.approved.add(self.acc_id)
        win.rlog.write("nav-aprovada", account=self.acc_name)
        self.pace()

    # ---------------------------------------------------------------- 2. ritmo
    def pace(self):
        if self.s.page is None or not self.s.logged_in:
            self.finish(APP, LOGIN_NEEDED)
            return
        chk = self.win.rate.browse_check(self.acc_id, self.lim(), post_open=self.post_open)
        if not chk["ok"]:
            self.finish(COOLDOWN if chk["reason"] == "cooldown" else QUOTA, chk["message"], until=chk["until"])
            return
        wait = chk["wait"] if self.navigation else 0.0
        if wait > 0.5:
            self.waited += wait
            self.win.rlog.write("nav-pausa", account=self.acc_name, action=self.action, seconds=wait)
            self.win.toast.show_message(f"Navegação passo a passo: aguardando {int(wait + 0.5)} s (intervalo mínimo).", 4000)
            QTimer.singleShot(int(wait * 1000) + 50, self.pace)
            return
        self.run()

    # ---------------------------------------------------------------- 3. ação
    def run(self):
        win, s = self.win, self.s
        if s is not win.current:
            win.switch_to(s.account.id)
        win.rate.record_browse(self.acc_id, self.lim(), post_open=self.post_open, navigation=self.navigation,
                               rng=self.ctl.rng)
        win.rlog.write("nav-acao", account=self.acc_name, action=self.action, post=self.post_open,
                       scroll=self.scroll)
        if self.action == "open-url":
            self.load(self.target["url"], lambda: self.snapshot(
                "post" if self.target["kind"] == "post" else "grid", self.after_open))
        elif self.action == "list-grid":
            self.list_grid()
        else:
            self.ensure_post(lambda: self.snapshot("post", self.after_post))

    def load(self, url: str, then):
        page = self.s.page
        state = {"fired": False}

        def on_loaded(_ok):
            if state["fired"]:
                return
            state["fired"] = True
            try:
                page.loadFinished.disconnect(on_loaded)
            except TypeError:
                pass
            # pequena pausa para o site renderizar, como quem espera a página aparecer
            QTimer.singleShot(int(self.ctl.rng.uniform(2.0, 3.5) * 1000), then)

        page.loadFinished.connect(on_loaded)
        QTimer.singleShot(LOAD_TIMEOUT_MS, lambda: on_loaded(False))
        self.s.navigate(QUrl(url))

    def ensure_post(self, then):
        if browse.same_post(self.s.page.url().toString(), self.target["code"]):
            then()
        else:
            self.load(self.target["url"], then)

    def snapshot(self, kind: str, then, deadline: float | None = None):
        """Lê o DOM já renderizado (sem rede); repete por até 15 s até o conteúdo aparecer."""
        deadline = deadline or time.monotonic() + READY_TIMEOUT_S
        if self.done:
            return
        if self.s.page is None:
            self.finish(ERROR, "A página da conta foi fechada.")
            return

        def got(raw):
            if self.done:
                return
            try:
                data = json.loads(raw) if isinstance(raw, str) and len(raw) < 3_000_000 else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                if time.monotonic() < deadline:
                    QTimer.singleShot(1000, lambda: self.snapshot(kind, then, deadline))
                else:
                    self.finish(ERROR, "Não deu para ler a página (ainda carregando?).")
                return
            if self.blocked(data.get("signals")):
                return
            if not data.get("ready") and time.monotonic() < deadline:
                QTimer.singleShot(1000, lambda: self.snapshot(kind, then, deadline))
                return
            then(data)

        self.s.bridge_call("snap", [kind], got)

    def blocked(self, signals) -> bool:
        kind = browse.classify_signals(signals)
        if kind is None and not self.s.logged_in:
            kind = "login"
        if kind is None:
            return False
        until = self.win.rate.record_block(self.acc_id, kind)
        self.win.rlog.write("bloqueio", account=self.acc_name, action=self.action, kind=kind,
                            cooldown_until=ratelimit.hhmm(until))
        msg = (f"{ratelimit.BLOCK_LABEL[kind]}. Navegação parada sem novas tentativas; "
               f"liberada de novo às {ratelimit.hhmm(until)}.")
        if kind == "checkpoint":
            msg += " Abra a conta no app e conclua a verificação."
        elif kind == "login":
            msg += " Entre na conta de novo no app."
        self.win.toast.show_message(msg, 9000)
        self.finish(COOLDOWN, msg, blocked=kind, until=until)
        return True

    # ----- open-url
    def after_open(self, data: dict):
        here = browse.target_of(str(data.get("url", ""))) or {}
        self.finish(OK, "Página aberta.", {"url": here.get("url") or self.target["url"], "kind": here.get("kind", ""),
                                           "user": here.get("user", ""), "code": here.get("code", ""),
                                           "ready": bool(data.get("ready"))})

    # ----- list-grid
    def list_grid(self):
        here = browse.target_of(self.s.page.url().toString())
        if not here or here["kind"] != "profile":
            self.finish(ERROR, "A conta não está num perfil. Use primeiro: gramdesk-browse open @perfil")
            return
        if not self.scroll:
            self.snapshot("grid", lambda d: self.after_grid(d, here))
            return

        def scrolled(_ok):
            QTimer.singleShot(int(self.ctl.rng.uniform(2.5, 4.0) * 1000),
                              lambda: self.snapshot("grid", lambda d: self.after_grid(d, here)))

        self.s.bridge_call("scrollOnce", [], scrolled)

    def after_grid(self, data: dict, here: dict):
        html = str(data.get("html") or "")
        items = browse.parse_grid(html, here["user"])
        self.finish(OK, f"{len(items)} post(s) na grade.", {
            "profile": here["user"], "url": here["url"], "scrolled": self.scroll, "avatar": browse.parse_avatar(html),
            "count": len(items), "visible": sum(1 for i in items if i["visible"]), "items": items,
        })

    # ----- inspect-post / download-current
    def after_post(self, data: dict):
        info = browse.parse_post(str(data.get("html") or ""), str(data.get("url") or self.target["url"]),
                                 data.get("meta"), data.get("index"))
        if not info["code"]:
            info["code"] = self.target["code"]
        if self.action == "inspect-post":
            self.finish(OK, "Post inspecionado.", info)
            return
        self.download(info)

    def download(self, info: dict):
        """Baixa UM arquivo do post aberto.

        1º: URL direta (mp4 progressivo de maior resolução / imagem original) dos dados que a página
        já recebeu, sem nenhuma requisição ao Instagram. 2º, só se esses dados não trouxerem a URL:
        o mesmo mecanismo do botão de download, que pode fazer UMA consulta à API (dentro desta
        mesma ação, já contada e no ritmo). Vídeo nunca cai para a capa nem para o stream blob:.
        """
        srcs = info.get("sources") or []
        pos = (self.index or 1) - 1
        wants_video = info.get("type") in ("reel", "video") or (0 <= pos < len(srcs) and srcs[pos].get("kind") == "video")
        src, total, err = browse.pick_source(srcs, self.index)
        if err:
            self.finish(ERROR, err, {"items_total": total})
            return
        if src is not None:
            self.fetch(info, src, total, "page-data")
            return

        def got(res: dict):
            if self.done:
                return
            calls = res.get("api") if isinstance(res.get("api"), int) else 0
            if calls:
                self.win.rlog.write("nav-api", account=self.acc_name, action=self.action, requests=calls)
            if not res.get("ok"):
                self.finish(ERROR, "Falha ao localizar a mídia: " + str(res.get("error", ""))[:160])
                return
            f, n, err2 = browse.pick_file(res.get("files") or [], self.index)
            if f is None:
                self.finish(ERROR, browse.VIDEO_NO_URL if wants_video and n == 0 else err2, {"items_total": n})
                return
            if wants_video and security.media_ext(f["url"]) != ".mp4":
                self.finish(ERROR, browse.VIDEO_NO_URL, {"items_total": n})
                return
            self.fetch(info, {"url": f["url"], "kind": f.get("kind") or "", "width": f.get("w") or 0,
                              "height": f.get("h") or 0}, n, "button")

        self.s.current_files(got)

    def fetch(self, info: dict, src: dict, total: int, source: str):
        idx = self.index or 1
        url = src["url"]
        ext = security.media_ext(url)
        owner = info.get("owner") or self.target.get("user") or "instagram"
        folder = security.folder_name(owner)
        target_dir = self.win.download_root / security.folder_name(self.acc_name) / folder
        name = browse.file_name(folder, info["code"], ext, idx, total)
        path = target_dir / name
        out = {"path": str(path), "name": name, "index": idx, "items_total": total, "owner": owner,
               "code": info["code"], "type": info["type"], "kind": "video" if ext == ".mp4" else "image",
               "width": int(src.get("width") or 0), "height": int(src.get("height") or 0), "source": source}
        if path.exists():
            self.finish(OK, "Já baixado antes.", dict(out, already=True))
            return

        def done(ok: bool, msg: str):
            if self.done:
                return
            if ok and path.exists():
                self.win.gallery.schedule_reload()
                self.finish(OK, f"Salvo: {name}", dict(out, already=False))
            else:
                self.win.rlog.write("nav-download-falhou", account=self.acc_name, reason=msg[:160])
                self.finish(ERROR, f"Download falhou: {name}. {msg}".strip())

        if not self.s.fetch_media(url, path, done, timeout_ms=DOWNLOAD_TIMEOUT_MS):
            self.finish(ERROR, browse.VIDEO_NO_URL if out["kind"] == "video" else "URL de mídia não permitida.")
