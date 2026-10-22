"""Botão "Baixar todas as mídias do perfil": fila executada passo a passo pelo BrowseController.

1. Lê a grade do perfil aberto, rolando uma vez por passo até ela parar de crescer.
2. Baixa um item por vez (cada carrossel item a item). O ritmo, as cotas, o teto diário de
   aberturas de post e o resfriamento são os mesmos da navegação passo a passo: a fila nunca
   baixa em rajada e para sozinha ao bater um limite ou um sinal de bloqueio do Instagram.
O estado fica salvo por perfil (profile_queue), então dá para pausar, fechar e retomar depois.
"""

from __future__ import annotations

import secrets

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from . import browse, config, security, ui
from .profile_queue import ProfileQueue

BUSY_RETRY_MS = 30_000
GRID_STALE_ROUNDS = 2  # rolagens seguidas sem posts novos encerram a leitura da grade
MAX_SCROLLS = 60
LABEL = {"": "todas as mídias", "video": "só vídeos", "image": "só fotos"}


class QueueBar(QFrame):
    def __init__(self, runner: "ProfileQueueRunner"):
        super().__init__()
        self.setObjectName("header")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 6, 12, 6)
        h.setSpacing(8)
        self.text = QLabel()
        self.text.setObjectName("subtitle")
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        h.addWidget(self.text, 1)
        self.pause_btn = QPushButton("Pausar")
        self.pause_btn.setObjectName("pill")
        self.pause_btn.clicked.connect(runner.toggle_pause)
        h.addWidget(self.pause_btn)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setObjectName("ghostBtn")
        self.cancel_btn.clicked.connect(runner.cancel)
        h.addWidget(self.cancel_btn)
        self.close_btn = ui.icon_button("close", "Fechar", self.hide, 28)
        h.addWidget(self.close_btn)
        self.hide()


class ProfileQueueRunner(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.bar = QueueBar(self)
        self.q: ProfileQueue | None = None
        self.acc_id = ""
        self.acc_name = ""
        self.state = "idle"  # idle | grid | download | paused | done
        self.task: tuple[str, str, int] | None = None
        self.grid_count = 0
        self.stale = 0
        self.scrolls = 0
        self.waiting = False  # um pedido em andamento no BrowseController

    # ---------------------------------------------------------------- comandos
    def menu(self, anchor):
        m = ui.menu(self.win)
        for only in ("", "video", "image"):
            m.addAction(ui.icon("download"), f"Baixar {LABEL[only]} do perfil", lambda o=only: self.start(o))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def start(self, only: str = ""):
        win = self.win
        if self.state in ("grid", "download"):
            self.bar.show()
            win.toast.show_message("A fila já está rodando.")
            return
        s = win.current
        here = browse.target_of(s.page.url().toString()) if s and s.page else None
        if win.outer.currentIndex() != 0 or s is None or not s.logged_in:
            win.toast.show_message("Entre na conta antes de baixar.")
            return
        if not here or here["kind"] != "profile":
            win.toast.show_message("Abra um perfil para baixar as mídias dele.")
            return
        self.acc_id, self.acc_name = s.account.id, s.account.name
        folder = win.download_root / security.folder_name(self.acc_name) / security.folder_name(here["user"])
        self.q = ProfileQueue.load(config.DATA_DIR / "queues", self.acc_id, here["user"], folder, only)
        self.q.start()
        # O clique no botão é a autorização: não pede a confirmação feita para a linha de comando.
        win.browse.approved.add(self.acc_id)
        self.grid_count = self.stale = self.scrolls = 0
        self.bar.pause_btn.setText("Pausar")
        self.bar.show()
        win.rlog.write("fila-inicio", account=self.acc_name, only=only or "tudo", known=len(self.q.items))
        if self.q.grid_complete and self.q.items:
            self.state = "download"
            self.step()
        else:
            self.state = "grid"
            self.request({"action": "list-grid"})

    def toggle_pause(self):
        if self.state in ("grid", "download"):
            self.pause("Pausada. Toque em Retomar para continuar do mesmo ponto.")
        elif self.state == "paused" and self.q is not None:
            self.bar.pause_btn.setText("Pausar")
            self.state = "download" if self.q.grid_complete else "grid"
            if not self.waiting:
                self.step() if self.state == "download" else self.request({"action": "list-grid"})

    def cancel(self):
        if self.q is not None:
            self.q.clear()
        self.state, self.q, self.task = "idle", None, None
        self.bar.hide()
        self.win.toast.show_message("Fila cancelada.")

    def pause(self, why: str):
        self.state = "paused"
        self.bar.pause_btn.setText("Retomar")
        self.show_status(why)

    # ---------------------------------------------------------------- passos
    def request(self, req: dict):
        self.waiting = True
        req = {"id": secrets.token_hex(8), "account": self.acc_id, **req}
        self.win.browse.handle(req, self.on_reply)

    def step(self):
        if self.state != "download" or self.q is None:
            return
        self.task = self.q.next_task()
        if self.task is None:
            self.finish()
            return
        code, url, index = self.task
        self.show_status(f"Baixando {code}" + (f" (item {index})" if index > 1 else ""))
        self.request({"action": "download-current", "url": url, "index": index})

    def on_reply(self, res: dict):
        self.waiting = False
        if self.q is None:  # cancelada enquanto o passo rodava
            return
        code, msg, data = res.get("code", 1), str(res.get("message") or ""), res.get("data") or {}
        if self.state == "grid" or (self.state == "paused" and self.task is None):
            self.after_grid(code, msg, data)
            return
        if self.task is None:
            return
        what = self.q.apply(self.task, code, data, msg)
        if code not in (0, 6):
            self.win.rlog.write("fila-item", account=self.acc_name, code=code)
        if what == "pause":
            self.pause(f"Parada: {msg}")
        elif what == "retry":
            self.show_status("Outra ação de navegação em andamento; tentando de novo em 30 s.")
            QTimer.singleShot(BUSY_RETRY_MS, self.step)
        elif self.state == "download":
            self.step()
        else:
            self.show_status("Pausada.")

    def after_grid(self, code: int, msg: str, data: dict):
        if code == 6:
            QTimer.singleShot(BUSY_RETRY_MS, lambda: self.state == "grid" and self.request({"action": "list-grid", "scroll": self.scrolls > 0}))
            return
        if code != 0:
            self.pause(f"Parada ao ler a grade: {msg}")
            return
        items = data.get("items") or []
        self.q.merge(items)
        self.q.save()
        grew = len(items) > self.grid_count
        self.grid_count = max(self.grid_count, len(items))
        self.stale = 0 if grew else self.stale + 1
        self.show_status(f"Lendo a grade: {len(self.q.items)} post(s) encontrados")
        if self.stale >= GRID_STALE_ROUNDS or self.scrolls >= MAX_SCROLLS:
            self.q.grid_complete = True
            self.q.save()
            if self.state == "grid":
                self.state = "download"
                self.step()
            return
        if self.state == "grid":
            self.scrolls += 1
            self.request({"action": "list-grid", "scroll": True})

    def finish(self):
        self.state = "done"
        done, total = self.q.progress() if self.q else (0, 0)
        errs = len(self.q.errors) if self.q else 0
        self.bar.pause_btn.hide()
        tail = f", {errs} com erro (tente de novo depois)" if errs else ""
        self.show_status(f"Concluída: {done} de {total} post(s) baixados{tail}.")
        self.win.rlog.write("fila-fim", account=self.acc_name, done=done, total=total, errors=errs)
        self.win.toast.show_message(f"Fila do perfil concluída: {done} de {total}.", 6000, "Ver downloads",
                                    lambda: self.win.open_gallery(security.folder_name(self.acc_name)))
        self.state = "idle"

    def show_status(self, text: str):
        self.bar.pause_btn.setVisible(self.state != "done")
        if self.q is None:
            self.bar.text.setText(text)
            return
        done, total = self.q.progress()
        head = f"Fila @{self.q.profile} ({LABEL[self.q.only]}) · {done} de {total}"
        self.bar.text.setText(f"{head} · {text}")
