"""Tela de Pesquisa de perfil: formulário, progresso e histórico."""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import research, ui

STATE_LABEL = {
    "collecting": "Coletando",
    "downloading": "Baixando mídias",
    "done": "Concluída",
    "error": "Erro",
    "cancelled": "Cancelada",
    "refused": "Recusada",
}


def _button(text: str, slot, role: str = "", icon_name: str = "", icon_role: str = "text") -> QPushButton:
    b = QPushButton(("  " + text) if icon_name else text)
    if role:
        b.setObjectName(role)
    if icon_name:
        ui.themed_icon(b, icon_name, icon_role, 18)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def analyze_command(folder: str) -> str:
    return f'gramdesk-research --analyze "{folder}"'


class ResearchPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("prefs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("prefsViewport")
        outer.addWidget(self.scroll)
        self.prefill = ""
        self.opts = {"posts": 60, "comments": True, "highlights": True, "videos": False}

    def open(self, username: str = ""):
        if username:
            self.prefill = username
        self.render()

    def render(self):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        page = QWidget()
        page.setObjectName("prefsPage")
        h = QHBoxLayout(page)
        h.setContentsMargins(36, 22, 36, 36)
        col = QWidget()
        col.setMaximumWidth(860)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        h.addWidget(col, 1)

        top = QHBoxLayout()
        top.addWidget(_button("Voltar ao Instagram", self.win.close_research, "nav", "back"))
        top.addStretch()
        top.addWidget(_button("Abrir pasta", self._open_root, "", "folder"))
        v.addLayout(top)
        v.addWidget(_label("Pesquisa de perfil", "pageTitle"))
        v.addWidget(_label(
            "Coleta somente leitura de um perfil (bio, links, posts, legendas, engajamento, comentários, "
            "destaques e mídias) usando a conta atual, no ritmo de uma pessoa navegando. "
            "Depois o Claude analisa e gera o briefing para montar um site.",
            "muted", True,
        ))

        v.addWidget(self._form())
        job = self.win.research_job
        if job is not None and job.state not in research.STATES_FINAL:
            v.addWidget(self._running(job))

        v.addSpacing(6)
        v.addWidget(_label("HISTÓRICO", "sectionTitle"))
        items = research.list_researches(research.research_root(self.win.download_root))
        if not items:
            v.addWidget(_label("Nenhuma pesquisa ainda.", "muted"))
        for st in items[:40]:
            v.addWidget(self._row(st))
        v.addStretch()
        self.scroll.setWidget(page)

    # ---------- blocos ----------
    def _form(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        f = QVBoxLayout(panel)
        f.setContentsMargins(20, 18, 20, 18)
        f.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.field = QLineEdit(self.prefill)
        self.field.setPlaceholderText("@perfil ou link do perfil")
        self.field.returnPressed.connect(self._start)
        row.addWidget(self.field, 1)
        cur = self.win.current
        acc = cur.account.name if cur else "nenhuma conta aberta"
        start = _button("Pesquisar", self._start, "primaryWide", "search", "white")
        idle = self.win.research_job is None or self.win.research_job.state in research.STATES_FINAL
        row.addWidget(start)
        f.addLayout(row)
        f.addWidget(_label(f"Usando a conta: {acc}", "muted"))
        blocked = ""
        if cur is not None:
            lim = self.win.research_limits()
            st = self.win.rate.status(cur.account.id, lim)
            f.addWidget(_label(
                f"Hoje: {st['researches_today']} de {lim.daily_researches} pesquisas · "
                f"{st['items_today']} de {lim.daily_items} itens · última hora {st['items_last_hour']} de {lim.per_hour}. "
                f"Ritmo: {lim.min_delay:g} a {lim.max_delay:g} s entre requisições, uma por vez. "
                f"Pesquisas do mesmo perfil nas últimas {lim.cache_hours} h reaproveitam o que já foi coletado.",
                "muted", True,
            ))
            if not st["ok"]:
                blocked = st["message"]
                f.addWidget(_label(blocked + " (pesquisas já completas no cache continuam disponíveis)", "error", True))
            elif not cur.logged_in:
                f.addWidget(_label(self.win.LOGIN_NEEDED, "error", True))
        start.setEnabled(cur is not None and idle)

        posts = ui.Segmented([("30", "30 posts"), ("60", "60"), ("120", "120"), ("200", "200")], str(self.opts["posts"]))
        posts.changed.connect(lambda v: self.opts.__setitem__("posts", int(v)))
        f.addWidget(ui.setting_row("Quantidade de posts", "Mais posts = análise melhor, coleta mais lenta.", posts))
        for key, title, desc in (
            ("comments", "Comentários", "Lê os comentários dos posts mais comentados (bons para depoimentos)."),
            ("highlights", "Destaques", "Inclui os destaques fixados no perfil."),
            ("videos", "Baixar vídeos", "Além das capas, salva os vídeos (ocupa mais espaço)."),
        ):
            t = ui.Toggle(self.opts[key])
            t.toggled.connect(lambda on, key=key: self.opts.__setitem__(key, on))
            f.addWidget(ui.setting_row(title, desc, t))
        return panel

    def _running(self, job) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        f = QVBoxLayout(panel)
        f.setContentsMargins(20, 16, 20, 16)
        f.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(_label(f"Pesquisando @{job.username}", "rowTitle"), 1)
        head.addWidget(_button("Cancelar", job.cancel, "danger", "close", "danger"))
        f.addLayout(head)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setRange(0, 0)
        f.addWidget(self.bar)
        self.status = _label("Iniciando...", "muted", True)
        f.addWidget(self.status)
        return panel

    def update_progress(self, text: str, done: int, total: int):
        if getattr(self, "status", None) is None:
            return
        try:
            self.status.setText(text)
            if total > 1:
                self.bar.setRange(0, total)
                self.bar.setValue(min(done, total))
            else:
                self.bar.setRange(0, 0)
        except RuntimeError:
            self.status = None

    def _row(self, st: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(12)
        folder = Path(st["dir"])
        pic = folder / "media" / "profile.jpg"
        avatar = QLabel()
        avatar.setFixedSize(44, 44)
        if pic.exists():
            avatar.setPixmap(ui.circle_pixmap(QPixmap(str(pic)), 44))
        else:
            avatar.setPixmap(ui.avatar_icon(st.get("username", "?"), ui.PALETTE[1], 44).pixmap(44, 44))
        h.addWidget(avatar)
        col = QVBoxLayout()
        col.setSpacing(2)
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(st.get("started_at", 0)))
        c = st.get("counts") or {}
        col.addWidget(_label(f"@{st.get('username', '?')}  ·  {STATE_LABEL.get(st.get('state'), st.get('state', ''))}", "rowTitle"))
        info = f"{when}  ·  conta {st.get('account', '?')}  ·  {c.get('posts', 0)} posts  ·  {c.get('media_files', 0)} mídias"
        if (folder / "site-brief.json").exists():
            info += "  ·  analisada"
        col.addWidget(_label(info, "muted"))
        if st.get("state") in ("error", "refused") and st.get("error"):
            col.addWidget(_label(str(st["error"])[:160], "error", True))
        h.addLayout(col, 1)
        if (folder / "report.md").exists():
            h.addWidget(self._icon_btn("info", "Abrir relatório", lambda: self._open(folder / "report.md")))
        if (folder / "preview.html").exists():
            h.addWidget(self._icon_btn("external", "Abrir prévia do site", lambda: self._open(folder / "preview.html")))
        if st.get("state") == "done":
            h.addWidget(self._icon_btn("copy", "Copiar comando de análise com Claude", lambda: self._copy_cmd(str(folder))))
        h.addWidget(self._icon_btn("folder", "Abrir pasta", lambda: self._open(folder)))
        return row

    def _icon_btn(self, name, tip, fn):
        b = ui.icon_button(name, tip, fn)
        b.setIconSize(QSize(18, 18))
        return b

    # ---------- ações ----------
    def _start(self):
        user = research.clean_username(self.field.text())
        if not user:
            self.win.toast.show_message("Informe um @perfil ou link de perfil válido.")
            return
        self.prefill = user
        self.win.start_research(user, dict(self.opts))

    def _copy_cmd(self, folder: str):
        QApplication.clipboard().setText(analyze_command(folder))
        self.win.toast.show_message("Comando copiado. Rode no terminal ou peça ao Claude Code.")

    def _open(self, path: Path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_root(self):
        root = research.research_root(self.win.download_root)
        root.mkdir(parents=True, exist_ok=True)
        self._open(root)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.win.close_research()
        else:
            super().keyPressEvent(e)
