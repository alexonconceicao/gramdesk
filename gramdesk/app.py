"""Janela principal: barra lateral de contas, cabeçalho, conteúdo e bloqueio."""

from __future__ import annotations

import json
import os
import sys
import time
from importlib import resources
from pathlib import Path

from PyQt6 import QtWebEngineWidgets  # noqa: F401 (precisa vir antes do QApplication)
from PyQt6.QtCore import QEvent, QSize, QStandardPaths, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import config, security, ui
from .gallery import GalleryPage
from .prefs import PreferencesPage
from . import donation, research
from .research_ui import ResearchPage


def _app_icon() -> QIcon:
    themed = QIcon.fromTheme(config.APP_ID)
    if not themed.isNull():
        return themed
    return QIcon(str(resources.files(__package__).joinpath("icon.svg")))


def _logo(size: int) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(_app_icon().pixmap(QSize(size, size)))
    lbl.setFixedSize(size, size)
    return lbl


def _separator() -> QFrame:
    f = QFrame()
    f.setObjectName("sep")
    f.setFixedHeight(1)
    return f


def _centered(*widgets, spacing: int = 12) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
    return lay


class LockScreen(ui.Backdrop):
    def __init__(self, on_unlock):
        super().__init__()
        self._on_unlock = on_unlock
        self._fails = 0
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(360)
        c = QVBoxLayout(card)
        c.setContentsMargins(30, 32, 30, 28)
        c.setSpacing(14)
        title = QLabel("Gramdesk bloqueado")
        title.setObjectName("sheetTitle")
        sub = QLabel("Digite sua senha para continuar")
        sub.setObjectName("muted")
        c.addLayout(_centered(_logo(64), title, sub, spacing=8))
        c.addSpacing(6)
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText("Senha")
        self.field.returnPressed.connect(self._try)
        key = self.field.addAction(ui.icon("key", ui.MUTED, 18), QLineEdit.ActionPosition.LeadingPosition)
        ui.themed_icon(key, "key", "muted", 18)
        c.addWidget(self.field)
        self.btn = QPushButton("Desbloquear")
        self.btn.setObjectName("primaryWide")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self._try)
        c.addWidget(self.btn)
        self.msg = QLabel("")
        self.msg.setObjectName("error")
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(self.msg)
        outer = QVBoxLayout(self)
        outer.addStretch()
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch()

    def reset(self):
        self.field.clear()
        self.msg.setText("")
        self.field.setFocus()

    def _try(self):
        if not self.field.isEnabled():
            return
        pwd = self.field.text()
        self.field.clear()
        if self._on_unlock(pwd):
            self._fails = 0
            return
        self._fails += 1
        if self._fails >= 5:
            wait = min(300, 15 * 2 ** (self._fails - 5))
            self.field.setEnabled(False)
            self.btn.setEnabled(False)
            self.msg.setText(f"Muitas tentativas. Aguarde {wait} s.")
            QTimer.singleShot(wait * 1000, self._release)
        else:
            self.msg.setText("Senha incorreta.")

    def _release(self):
        self.field.setEnabled(True)
        self.btn.setEnabled(True)
        self.msg.setText("")
        self.field.setFocus()


class EmptyState(ui.Backdrop):
    def __init__(self, on_add):
        super().__init__()
        title = QLabel("Bem-vindo ao Gramdesk")
        title.setObjectName("sheetTitle")
        sub = QLabel("Cada conta roda isolada, sem rastreadores, e hiberna quando você troca.")
        sub.setObjectName("muted")
        btn = QPushButton("  Adicionar conta")
        ui.themed_icon(btn, "plus", "white", 18)
        btn.setObjectName("primaryWide")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(on_add)
        outer = QVBoxLayout(self)
        outer.addStretch()
        outer.addLayout(_centered(_logo(88), title, sub, btn, spacing=14))
        outer.addStretch()


class MainWindow(QMainWindow):
    def __init__(self, cfg: config.Config):
        super().__init__()
        from .web import AccountSession

        self._Session = AccountSession
        self.cfg = cfg
        self.sessions: dict[str, AccountSession] = {}
        self.current: AccountSession | None = None
        self._buttons: dict[str, ui.AvatarButton] = {}
        self._initial_lock = False
        self.download_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or Path.home()
        ) / "Gramdesk"

        self.setWindowTitle("Gramdesk")
        self.setWindowIcon(_app_icon())
        self.resize(1200, 840)
        self.setMinimumSize(760, 520)

        self.outer = QStackedWidget()
        self.setCentralWidget(self.outer)
        self.outer.addWidget(self._build_shell())
        self.lock_screen = LockScreen(self._unlock)
        self.outer.addWidget(self.lock_screen)
        self.toast = ui.Toast(self.outer)

        for acc in cfg.accounts:
            self._add_button(acc)
        for i in range(9):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self._switch_index(i))
        for seq, fn in (
            ("Ctrl+G", lambda: self.ghost_btn.click()),
            ("Ctrl+D", lambda: self._collect("current")),
            ("Ctrl+Shift+D", lambda: self._collect("stories")),
            ("Ctrl+L", lambda: self._lock()),
            ("Ctrl+R", self._reload),
            ("F5", self._reload),
            ("Alt+Left", lambda: self._nav("Back")),
            ("Alt+Right", lambda: self._nav("Forward")),
            ("Ctrl+,", lambda: self.open_prefs()),
            ("Ctrl+J", lambda: self.open_gallery()),
            ("Ctrl+K", self._focus_link_bar),
            ("Ctrl+Shift+F", lambda: self.open_research(self._profile_in_view())),
            ("Ctrl+Shift+,", self._current_account_prefs),
            ("Ctrl+=", lambda: self._zoom_step(10)),
            ("Ctrl++", lambda: self._zoom_step(10)),
            ("Ctrl+-", lambda: self._zoom_step(-10)),
            ("Ctrl+0", lambda: self._zoom_step(0)),
        ):
            QShortcut(QKeySequence(seq), self, activated=fn)

        self._pending_link = ""
        self._offered_clip = ""
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)

        self._idle = QTimer(self, singleShot=True, timeout=self._idle_timeout)
        QApplication.instance().installEventFilter(self)
        self._arm_idle()

        self._ticker = QTimer(self, interval=2500, timeout=self._refresh_header)
        self._ticker.start()

        if cfg.lock:
            self._lock(initial=True)
        else:
            QTimer.singleShot(0, self._start)
        if donation.enabled() and not cfg.donation.get("first_seen"):
            cfg.donation["first_seen"] = time.time()
            config.save(cfg)
        QTimer.singleShot(45_000, self.maybe_ask_donation)

    # ---------- estrutura ----------
    def _build_shell(self) -> QWidget:
        shell = QWidget()
        shell.setObjectName("shell")
        h = QHBoxLayout(shell)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(72)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(4, 14, 8, 12)
        sv.setSpacing(6)
        sv.addWidget(_logo(36), 0, Qt.AlignmentFlag.AlignHCenter)
        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFixedSize(28, 1)
        sv.addSpacing(6)
        sv.addWidget(sep, 0, Qt.AlignmentFlag.AlignHCenter)
        sv.addSpacing(4)
        holder = QWidget()
        holder.setObjectName("sideHolder")
        self.accounts_box = QVBoxLayout(holder)
        self.accounts_box.setContentsMargins(0, 0, 0, 0)
        self.accounts_box.setSpacing(4)
        self.add_btn = ui.AddButton()
        self.add_btn.clicked.connect(self.add_account)
        self.accounts_box.addWidget(self.add_btn)
        self.accounts_box.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("sideViewport")
        sv.addWidget(scroll, 1)
        sv.addWidget(ui.icon_button("gallery", "Meus downloads (Ctrl+J)", lambda: self.open_gallery(), 40), 0, Qt.AlignmentFlag.AlignHCenter)
        sv.addWidget(ui.icon_button("settings", "Preferências (Ctrl+,)", lambda: self.open_prefs(), 40), 0, Qt.AlignmentFlag.AlignHCenter)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.header = self._build_header()
        rv.addWidget(self.header)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.empty = EmptyState(self.add_account)
        self.stack.addWidget(self.empty)
        self.prefs = PreferencesPage(self)
        self.stack.addWidget(self.prefs)
        self.gallery = GalleryPage(self)
        self.stack.addWidget(self.gallery)
        self.research_job = None
        self.research_page = ResearchPage(self)
        self.stack.addWidget(self.research_page)
        self._before_prefs = None
        rv.addWidget(self.stack, 1)

        h.addWidget(side)
        h.addWidget(right, 1)
        return shell

    def _build_header(self) -> QFrame:
        head = QFrame()
        head.setObjectName("header")
        head.setFixedHeight(58)
        h = QHBoxLayout(head)
        h.setContentsMargins(12, 0, 14, 0)
        h.setSpacing(4)
        self.nav_buttons = [
            ui.icon_button("back", "Voltar (Alt+←)", lambda: self._nav("Back")),
            ui.icon_button("forward", "Avançar (Alt+→)", lambda: self._nav("Forward")),
            ui.icon_button("refresh", "Recarregar (Ctrl+R)", self._reload),
            ui.icon_button("home", "Início", self._home),
        ]
        for b in self.nav_buttons:
            h.addWidget(b)
        h.addSpacing(10)
        self.chip = QPushButton()
        self.chip.setObjectName("chip")
        self.chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chip.setToolTip("Opções da conta atual")
        self.chip.setFixedHeight(46)
        ch = QHBoxLayout(self.chip)
        ch.setContentsMargins(6, 4, 10, 4)
        ch.setSpacing(10)
        self.chip_avatar = QLabel()
        self.chip_avatar.setFixedSize(32, 32)
        ch.addWidget(self.chip_avatar)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = QLabel("Gramdesk")
        self.title.setObjectName("title")
        self.subtitle = QLabel("Nenhuma conta aberta")
        self.subtitle.setObjectName("subtitle")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        ch.addLayout(col)
        self.chip_chevron = QLabel()
        self.chip_chevron.setFixedSize(16, 16)
        ch.addWidget(self.chip_chevron)
        for w in (self.chip_avatar, self.title, self.subtitle, self.chip_chevron):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.chip.clicked.connect(lambda: self.current and self._account_menu(self.current.account.id, self.chip))
        h.addWidget(self.chip)
        h.addSpacing(10)
        self.link_bar = QLineEdit()
        self.link_bar.setObjectName("linkBar")
        self.link_bar.setPlaceholderText("Colar link do Instagram ou @perfil  (Ctrl+K)")
        self.link_bar.setClearButtonEnabled(True)
        self.link_bar.setMinimumWidth(180)
        self.link_bar.setMaximumWidth(460)
        link_icon = self.link_bar.addAction(ui.icon("link", ui.MUTED, 18), QLineEdit.ActionPosition.LeadingPosition)
        ui.themed_icon(link_icon, "link", "muted", 18)
        self.link_bar.returnPressed.connect(lambda: self.open_link(self.link_bar.text()))
        h.addWidget(self.link_bar, 1)
        h.addSpacing(10)

        self.ghost_btn = QPushButton("  Fantasma")
        self.ghost_btn.setObjectName("pill")
        self.ghost_btn.setCheckable(True)
        self.ghost_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ghost_btn.setToolTip("Ver stories sem aparecer na lista de visualizações (Ctrl+G)")
        self.ghost_btn.toggled.connect(self._toggle_ghost)
        self.ghost_btn.toggled.connect(lambda _on: self._sync_ghost_icon())
        self._sync_ghost_icon()
        h.addWidget(self.ghost_btn)
        h.addSpacing(4)
        dl = QPushButton("  Baixar")
        dl.setObjectName("primary")
        ui.themed_icon(dl, "download", "white", 18)
        dl.setCursor(Qt.CursorShape.PointingHandCursor)
        dl.setToolTip("Baixar a mídia aberta na tela (Ctrl+D)\nBotão direito sobre uma mídia também baixa")
        dl.clicked.connect(lambda: self._collect("current"))
        h.addWidget(dl)
        h.addSpacing(4)
        h.addWidget(ui.icon_button("stories", "Baixar todos os stories do perfil (Ctrl+Shift+D)", lambda: self._collect("stories")))
        h.addWidget(ui.icon_button("gallery", "Meus downloads (Ctrl+J)", lambda: self.open_gallery(self._current_folder())))
        h.addWidget(ui.icon_button("search", "Pesquisar perfil (Ctrl+Shift+F)", lambda: self.open_research(self._profile_in_view())))
        acc_btn = ui.icon_button("user", "Configurações da conta (Ctrl+Shift+,)", self._current_account_prefs)
        h.addWidget(acc_btn)
        h.addWidget(ui.icon_button("lock", "Bloquear (Ctrl+L)", lambda: self._lock()))
        self.header_actions = [self.ghost_btn, dl, self.chip, acc_btn, self.link_bar]
        return head

    # ---------- contas ----------
    def color(self, acc: config.Account) -> str:
        return ui.PALETTE[acc.color % len(ui.PALETTE)]

    _color = color

    def state_label(self, acc_id: str) -> str:
        s = self.sessions.get(acc_id)
        if s is not None and s is self.current:
            return "ativa agora"
        if s is not None and s.loaded:
            return "em segundo plano" if not s.hibernating else "hibernando"
        return "não carregada"

    def _add_button(self, acc: config.Account):
        b = ui.AvatarButton(acc.name, self.color(acc))
        b.pressed.connect(lambda i=acc.id: setattr(self, "_was_current", bool(self.current and self.current.account.id == i)))
        b.clicked.connect(lambda _=False, i=acc.id: self._avatar_clicked(i))
        b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        b.customContextMenuRequested.connect(lambda _pos, i=acc.id: self._account_menu(i))
        self.group.addButton(b)
        self.accounts_box.insertWidget(self.accounts_box.indexOf(self.add_btn), b, 0, Qt.AlignmentFlag.AlignHCenter)
        self._buttons[acc.id] = b
        self._paint_button(acc.id)

    def _paint_button(self, acc_id: str):
        acc = self.cfg.account(acc_id)
        b = self._buttons.get(acc_id)
        if not acc or not b:
            return
        s = self.sessions.get(acc_id)
        if s is not None and s is self.current:
            state = "active"
        elif s is not None and s.loaded:
            state = "sleep"
        else:
            state = "off"
        b.set_data(acc.name, self.color(acc), state)
        idx = [a.id for a in self.cfg.accounts].index(acc_id)
        hint = f"  ·  Ctrl+{idx + 1}" if idx < 9 else ""
        b.setToolTip(
            f"<b>{acc.name}</b><br>{self.state_label(acc_id)}{hint}"
            f"<br><span style='color:{ui.MUTED}'>Clique na conta ativa para ver opções</span>"
        )

    def _reorder_buttons(self):
        for acc in self.cfg.accounts:
            b = self._buttons.get(acc.id)
            if b:
                self.accounts_box.removeWidget(b)
        for acc in self.cfg.accounts:
            b = self._buttons.get(acc.id)
            if b:
                self.accounts_box.insertWidget(self.accounts_box.indexOf(self.add_btn), b, 0, Qt.AlignmentFlag.AlignHCenter)
        for acc in self.cfg.accounts:
            self._paint_button(acc.id)

    def _avatar_clicked(self, acc_id: str):
        if getattr(self, "_was_current", False) and self.stack.currentWidget() not in (self.prefs, self.gallery, self.research_page):
            self._buttons[acc_id].setChecked(True)
            self._account_menu(acc_id)
        else:
            self.switch_to(acc_id)

    def _account_menu(self, acc_id: str, anchor: QWidget | None = None):
        acc = self.cfg.account(acc_id)
        b = self._buttons.get(acc_id)
        if not acc or not b:
            return
        s = self.sessions.get(acc_id)
        pop = QFrame(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        pop.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        pop.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box = QFrame(pop)
        box.setObjectName("popover")
        box.setFixedWidth(272)
        outer = QVBoxLayout(pop)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 12, 10, 10)
        v.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 6)
        head.setSpacing(12)
        head.addWidget(ui.BigAvatar(acc.name, self.color(acc), 48))
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(acc.name)
        name.setObjectName("rowTitle")
        st = QLabel(self.state_label(acc_id).capitalize())
        st.setObjectName("muted")
        col.addWidget(name)
        col.addWidget(st)
        head.addLayout(col, 1)
        v.addLayout(head)

        ghost = ui.Toggle(acc.ghost)
        grow = QHBoxLayout()
        grow.setContentsMargins(10, 6, 8, 6)
        grow.addWidget(QLabel(pixmap=ui.icon("ghost", ui.MUTED, 18).pixmap(18, 18)))
        grow.addSpacing(6)
        grow.addWidget(QLabel("Modo fantasma"), 1)
        grow.addWidget(ghost)
        v.addLayout(grow)
        ghost.toggled.connect(lambda on: self.update_account(acc_id, ghost=on))

        v.addWidget(_separator())

        def item(icon_name, text, fn, danger=False, enabled=True):
            btn = QPushButton("  " + text)
            btn.setObjectName("itemDanger" if danger else "item")
            btn.setIcon(ui.icon(icon_name, ui.DANGER if danger else ui.TEXT, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(enabled)
            btn.clicked.connect(lambda: (pop.close(), QTimer.singleShot(0, fn)))
            v.addWidget(btn)

        if s is not self.current:
            item("forward", "Abrir conta", lambda: self.switch_to(acc_id))
        item("settings", "Configurações da conta", lambda: self.open_prefs("accounts", acc_id))
        item("edit", "Renomear e cor", lambda: self._edit_account(acc_id))
        loaded = bool(s and s.loaded)
        item("refresh", "Recarregar", lambda: s and s.page and s.page.triggerAction(s.page.WebAction.Reload), enabled=loaded)
        item("broom", "Limpar cache", lambda: s and s.clear_cache(), enabled=loaded)
        item("search", "Pesquisar perfil", lambda: self.open_research(self._profile_in_view()))
        item("gallery", "Ver downloads da conta", lambda: self.open_gallery(security.folder_name(acc.name)))
        v.addWidget(_separator())
        item("trash", "Sair e remover conta", lambda: self.remove_account(acc_id), danger=True)

        pop.adjustSize()
        if anchor is not None:
            pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
            pop.move(pos.x(), pos.y() + 6)
        else:
            pos = b.mapToGlobal(b.rect().topRight())
            pop.move(pos.x() + 8, pos.y() - 6)
        pop.show()

    def update_account(self, acc_id: str, *, name=None, color=None, ghost=None, zoom=None, keep_alive=None) -> bool:
        acc = self.cfg.account(acc_id)
        if not acc:
            return False
        s = self.sessions.get(acc_id)
        if name is not None:
            try:
                acc.name = security.clean_account_name(name)
            except ValueError:
                self.toast.show_message("Nome inválido.")
                return False
        if color is not None and 0 <= color < len(ui.PALETTE):
            acc.color = color
        if zoom is not None:
            acc.zoom = config.clamp_zoom(zoom)
            if s:
                s.apply_zoom()
        if keep_alive is not None:
            acc.keep_alive = bool(keep_alive)
            if not keep_alive and s and s is not self.current:
                s.hibernate()
        if ghost is not None and ghost != acc.ghost:
            if s:
                s.set_ghost(ghost)
            else:
                acc.ghost = ghost
            if s is self.current:
                self.ghost_btn.blockSignals(True)
                self.ghost_btn.setChecked(ghost)
                self.ghost_btn.blockSignals(False)
                self._sync_ghost_icon()
            self.toast.show_message(f"Modo fantasma {'ligado' if ghost else 'desligado'} em {acc.name}.")
        config.save(self.cfg)
        self._paint_button(acc_id)
        if s is not None and s is self.current:
            self.setWindowTitle(f"{acc.name} · Gramdesk")
        self._refresh_header()
        return True

    def move_account(self, acc_id: str, delta: int):
        ids = [a.id for a in self.cfg.accounts]
        i = ids.index(acc_id)
        j = i + delta
        if 0 <= j < len(ids):
            self.cfg.accounts[i], self.cfg.accounts[j] = self.cfg.accounts[j], self.cfg.accounts[i]
            config.save(self.cfg)
            self._reorder_buttons()

    def _current_account_prefs(self):
        if self.current:
            self.open_prefs("accounts", self.current.account.id)
        else:
            self.open_prefs("accounts")

    def _zoom_step(self, step: int):
        if not self.current:
            return
        acc = self.current.account
        z = 100 if step == 0 else acc.zoom + step
        self.update_account(acc.id, zoom=z)
        self.toast.show_message(f"Zoom: {acc.zoom}%", 1200)

    def _session(self, acc_id: str):
        s = self.sessions.get(acc_id)
        if s is None:
            s = self._Session(self.cfg.account(acc_id), self.cfg, self.download_root, self)
            s.status.connect(lambda msg, s=s: s is self.current and self.toast.show_message(msg))
            s.externalLink.connect(self._external)
            s.downloaded.connect(self._on_downloaded)
            self.sessions[acc_id] = s
        return s

    def switch_to(self, acc_id: str):
        if self.outer.currentIndex() != 0 or not self.cfg.account(acc_id):
            return
        prev = self.current
        s = self._session(acc_id)
        view = s.activate()
        if self.stack.indexOf(view) == -1:
            self.stack.addWidget(view)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, s=s: self._web_menu(s, pos))
        self.header.show()
        self.stack.setCurrentWidget(view)
        view.setFocus()
        self.current = s
        self._buttons[acc_id].setChecked(True)
        self.ghost_btn.blockSignals(True)
        self.ghost_btn.setChecked(s.account.ghost)
        self.ghost_btn.blockSignals(False)
        self._sync_ghost_icon()
        self.setWindowTitle(f"{s.account.name} · Gramdesk")
        if self.cfg.last_account != acc_id:
            self.cfg.last_account = acc_id
            config.save(self.cfg)
        if prev is not None and prev is not s:
            QTimer.singleShot(0, lambda: (prev.hibernate(), self._paint_button(prev.account.id)))
        self._paint_button(acc_id)
        self._refresh_header()

    def _refresh_header(self):
        s = self.current
        enabled = s is not None
        for w in self.nav_buttons + self.header_actions:
            w.setEnabled(enabled)
        self.chip_chevron.setPixmap(ui.icon("chevron", ui.MUTED, 16).pixmap(16, 16))
        if not s:
            self.title.setText("Gramdesk")
            self.subtitle.setText("Nenhuma conta aberta")
            self.chip_avatar.setPixmap(_app_icon().pixmap(QSize(32, 32)))
            self._fit_chip()
            return
        self.title.setText(s.account.name)
        self.chip_avatar.setPixmap(ui.avatar_icon(s.account.name, self.color(s.account), 32).pixmap(32, 32))
        asleep = sum(1 for x in self.sessions.values() if x.loaded and x.hibernating)
        parts = ["Fantasma ligado" if s.account.ghost else "Visualizações visíveis", f"{s.blocked_count} bloqueios"]
        if s.account.zoom != 100:
            parts.append(f"zoom {s.account.zoom}%")
        if asleep:
            parts.append(f"{asleep} hibernando")
        self.subtitle.setText("  ·  ".join(parts))
        self._fit_chip()

    def _fit_chip(self):
        self.chip.layout().activate()
        self.chip.setFixedWidth(min(420, self.chip.layout().sizeHint().width()))

    def _switch_index(self, i: int):
        if i < len(self.cfg.accounts):
            self.switch_to(self.cfg.accounts[i].id)

    def _start(self):
        if not self.cfg.accounts:
            self.stack.setCurrentWidget(self.empty)
            self._refresh_header()
            return
        self.switch_to(self.cfg.last_account or self.cfg.accounts[0].id)
        QTimer.singleShot(0, self._flush_pending_link)

    def _account_sheet(self, acc: config.Account | None):
        editing = acc is not None
        sheet = ui.Sheet(
            self.outer,
            "Editar conta" if editing else "Nova conta",
            "Nome e cor ficam só neste computador." if editing
            else "Cada conta ganha um perfil isolado: cookies, cache e sessão separados.",
        )
        color = acc.color if editing else len(self.cfg.accounts) % len(ui.PALETTE)
        name0 = acc.name if editing else f"Conta {len(self.cfg.accounts) + 1}"
        preview = ui.BigAvatar(name0, ui.PALETTE[color])
        sheet.body.addWidget(preview, 0, Qt.AlignmentFlag.AlignHCenter)
        field = QLineEdit(name0)
        field.setPlaceholderText("Ex.: Pessoal, Trabalho, Loja")
        field.setMaxLength(40)
        field.selectAll()
        field.textChanged.connect(lambda t: preview.set_data(t or "?", preview.color))
        field.returnPressed.connect(sheet.accept_default)
        sheet.body.addWidget(QLabel("Nome"))
        sheet.body.addWidget(field)
        sheet.body.addWidget(QLabel("Cor"))
        row = QHBoxLayout()
        row.setSpacing(6)
        swatches = []
        for i, c in enumerate(ui.PALETTE):
            sw = ui.Swatch(c)
            sw.setChecked(i == color)
            sw.clicked.connect(lambda _=False, c=c: preview.set_data(preview.name, c))
            swatches.append(sw)
            row.addWidget(sw)
        row.addStretch()
        sheet.body.addLayout(row)
        ghost = ui.Toggle(acc.ghost if editing else True)
        sheet.body.addWidget(ui.setting_row("Modo fantasma", "Abrir stories sem aparecer nas visualizações.", ghost))
        sheet.add_button("Cancelar", None, "ghostBtn")
        sheet.add_button("Salvar" if editing else "Adicionar conta", "ok", "primaryWide", default=True)
        if sheet.exec(field) != "ok":
            return None
        try:
            name = security.clean_account_name(field.text())
        except ValueError:
            self.toast.show_message("Nome inválido.")
            return None
        idx = next((i for i, sw in enumerate(swatches) if sw.isChecked()), color)
        return name, idx, ghost.isChecked()

    def add_account(self):
        if len(self.cfg.accounts) >= config.MAX_ACCOUNTS:
            self.toast.show_message(f"Limite de {config.MAX_ACCOUNTS} contas.")
            return
        res = self._account_sheet(None)
        if not res:
            return
        name, color, ghost = res
        acc = config.Account.new(name, color, ghost)
        self.cfg.accounts.append(acc)
        config.save(self.cfg)
        self._add_button(acc)
        if self.stack.currentWidget() is self.prefs:
            self.prefs.account_id = acc.id
            self.prefs.render()
        else:
            self.switch_to(acc.id)
        self.toast.show_message("Conta criada. Faça login normalmente na página do Instagram.")

    def _edit_account(self, acc_id: str):
        acc = self.cfg.account(acc_id)
        res = self._account_sheet(acc) if acc else None
        if res:
            name, color, ghost = res
            self.update_account(acc_id, name=name, color=color, ghost=ghost)

    def confirm(self, title: str, text: str, ok: str, danger: bool = False) -> bool:
        sheet = ui.Sheet(self.outer, title, text, width=420)
        sheet.add_button("Cancelar", None, "ghostBtn")
        sheet.add_button(ok, True, "danger" if danger else "primaryWide", default=True)
        return sheet.exec() is True

    def remove_account(self, acc_id: str):
        acc = self.cfg.account(acc_id)
        if not acc or not self.confirm(
            f"Remover \"{acc.name}\"?",
            "Sai da conta e apaga cookies, cache e sessão deste computador. Arquivos já baixados ficam.",
            "Remover conta",
            danger=True,
        ):
            return
        if self.research_job is not None and self.research_job.session is self.sessions.get(acc_id):
            self.research_job.cancel()
        s = self.sessions.pop(acc_id, None)
        if s is not None:
            s.wipe()
            if s.view is not None:
                self.stack.removeWidget(s.view)
            s.close()
            s.deleteLater()
        b = self._buttons.pop(acc_id)
        self.group.removeButton(b)
        b.deleteLater()
        self.cfg.accounts = [a for a in self.cfg.accounts if a.id != acc_id]
        self.cfg.pending_delete.append(acc_id)
        if self.cfg.last_account == acc_id:
            self.cfg.last_account = ""
        config.save(self.cfg)
        if self.current is s:
            self.current = None
            self.setWindowTitle("Gramdesk")
            if self.stack.currentWidget() is not self.prefs:
                self._start()
        for a in self.cfg.accounts:
            self._paint_button(a.id)
        self._refresh_header()
        if self.stack.currentWidget() is self.prefs or self.current is None and self.cfg.accounts:
            self.open_prefs("accounts")
        self.toast.show_message("Conta removida. Os arquivos do perfil somem do disco ao reiniciar.")

    # ---------- ações ----------
    def _nav(self, action: str):
        if self.current and self.current.page:
            from PyQt6.QtWebEngineCore import QWebEnginePage

            self.current.page.triggerAction(getattr(QWebEnginePage.WebAction, action))

    def _reload(self):
        self._nav("Reload")

    def _home(self):
        if self.current and self.current.page:
            from .web import HOME

            self.current.page.setUrl(HOME)

    def _toggle_ghost(self, checked: bool):
        if not self.current:
            return
        self.current.set_ghost(checked)
        config.save(self.cfg)
        self._refresh_header()
        self.toast.show_message(
            "Modo fantasma ligado: stories não marcam visualização." if checked else "Modo fantasma desligado."
        )

    def _collect(self, mode: str):
        if self.current and self.outer.currentIndex() == 0:
            self.current.collect(mode)

    def _web_menu(self, s, pos):
        from PyQt6.QtWebEngineCore import QWebEnginePage

        W = QWebEnginePage.WebAction
        m = ui.menu(self)
        zoom = s.view.zoomFactor() or 1.0
        m.addAction(ui.icon("download"), "Baixar esta mídia", lambda: s.collect("current", pos.x() / zoom, pos.y() / zoom))
        m.addAction(ui.icon("stories"), "Baixar todos os stories", lambda: s.collect("stories"))
        m.addSeparator()
        for text, name, act in (("Copiar", "copy", W.Copy), ("Colar", "paste", W.Paste), ("Copiar link", "link", W.CopyLinkToClipboard)):
            if s.page.action(act).isEnabled():
                m.addAction(ui.icon(name), text, lambda act=act: s.page.triggerAction(act))
        m.addSeparator()
        m.addAction(ui.icon("back"), "Voltar", lambda: s.page.triggerAction(W.Back))
        m.addAction(ui.icon("refresh"), "Recarregar", lambda: s.page.triggerAction(W.Reload))
        m.exec(s.view.mapToGlobal(pos))

    def _external(self, url: QUrl):
        if self.confirm(
            "Abrir link externo?",
            f"Este link sai do Instagram e vai abrir no navegador do sistema:\n\n{url.toDisplayString()[:300]}",
            "Abrir no navegador",
        ):
            QDesktopServices.openUrl(url)

    # ---------- preferências ----------
    def open_prefs(self, section: str = "", account_id: str = ""):
        if self.outer.currentIndex() != 0:
            return
        if self.stack.currentWidget() is not self.prefs:
            self._before_prefs = self.stack.currentWidget()
        self.header.hide()
        self.stack.setCurrentWidget(self.prefs)
        self.prefs.open(section, account_id)
        self.prefs.setFocus()

    def open_gallery(self, folder: str = ""):
        if self.outer.currentIndex() != 0:
            return
        self.header.hide()
        self.stack.setCurrentWidget(self.gallery)
        self.gallery.open(folder)

    def close_gallery(self):
        if self.gallery.viewer.isVisible():
            self.gallery.viewer.close_viewer()
        self.close_prefs(force=True)

    # ---------- links ----------
    def _focus_link_bar(self):
        if self.outer.currentIndex() != 0:
            return
        if self.stack.currentWidget() in (self.prefs, self.gallery) and self.current:
            self.switch_to(self.current.account.id)
        self.link_bar.setFocus()
        self.link_bar.selectAll()

    def open_link(self, text: str) -> bool:
        url = security.parse_instagram_input(text)
        if not url:
            self.toast.show_message("Isso não parece um link do Instagram nem um @perfil.")
            return False
        if self.outer.currentIndex() != 0:
            self._pending_link = url  # abre depois de desbloquear
            return True
        if self.current is None:
            if not self.cfg.accounts:
                self.toast.show_message("Adicione uma conta para abrir links.")
                return False
            self._start()
        if self.current is None:
            self._pending_link = url
            return True
        self.switch_to(self.current.account.id)
        self.current.navigate(QUrl(url))
        self.link_bar.clear()
        self.current.view.setFocus()
        self.toast.show_message(f"Abrindo em {self.current.account.name}", 2000)
        return True

    def _flush_pending_link(self):
        req = getattr(self, "_pending_research", None)
        if req and self.outer.currentIndex() == 0:
            self._pending_research = None
            QTimer.singleShot(800, lambda: self.handle_research_request(req))
        if self._pending_link and self.current is not None and self.outer.currentIndex() == 0:
            url, self._pending_link = self._pending_link, ""
            self.open_link(url)

    def _on_app_state(self, state):
        if state != Qt.ApplicationState.ApplicationActive or self.outer.currentIndex() != 0 or not self.cfg.accounts:
            return
        text = (QApplication.clipboard().text() or "").strip()
        if not text or text == self._offered_clip or "instagr" not in text.lower():
            return
        url = security.parse_instagram_input(text)
        if not url:
            return
        self._offered_clip = text
        where = self.current.account.name if self.current else "conta atual"
        self.toast.show_message("Link do Instagram copiado", 6000, f"Abrir em {where}", lambda: self.open_link(url))

    # ---------- apoio ao projeto ----------
    def maybe_ask_donation(self):
        if self.outer.currentIndex() != 0 or self.findChild(ui.Sheet) is not None:
            return
        if donation.should_show(self.cfg.donation):
            self.ask_donation()

    def ask_donation(self):
        c = donation.CONFIG
        if c is None:
            return
        self.cfg.donation["last_shown"] = time.time()
        config.save(self.cfg)
        sheet = ui.Sheet(self.outer, c["title"], c["text"], width=560)
        if c.get("liquid"):
            box = QFrame()
            box.setObjectName("row")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 12, 14, 12)
            bl.setSpacing(8)
            head = QLabel(f"Liquid ({c['liquid_assets']})")
            head.setObjectName("rowTitle")
            bl.addWidget(head)
            addr = QLabel("\u200b".join(c["liquid"][i:i + 12] for i in range(0, len(c["liquid"]), 12)))
            addr.setObjectName("mono")
            addr.setWordWrap(True)
            bl.addWidget(addr)
            copy = QPushButton("  Copiar endereço")
            ui.themed_icon(copy, "copy", "text", 18)
            copy.setCursor(Qt.CursorShape.PointingHandCursor)
            copy.clicked.connect(lambda: (QApplication.clipboard().setText(c["liquid"]),
                                          self.toast.show_message("Endereço Liquid copiado.")))
            bl.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
            sheet.body.addWidget(box)
        sheet.add_button("Não mostrar mais", "never", "ghostBtn", left=True)
        if c.get("url"):
            sheet.add_button("Agora não", None, "ghostBtn")
            sheet.add_button(c["button"], "go", "primaryWide", default=True)
        else:
            sheet.add_button("Fechar", None, "primaryWide", default=True)
        res = sheet.exec()
        if res == "never":
            self.cfg.donation["never"] = True
            config.save(self.cfg)
            self.toast.show_message("Tudo bem, não vamos mais perguntar. O link fica em Preferências > Sobre.")
        elif res == "go":
            QDesktopServices.openUrl(QUrl(c["url"]))

    def raise_window(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------- pesquisa de perfil ----------
    def _profile_in_view(self) -> str:
        if self.current and self.current.page:
            return research.username_from_url(self.current.page.url().toString()) or ""
        return ""

    def open_research(self, username: str = ""):
        if self.outer.currentIndex() != 0:
            return
        self.header.hide()
        self.stack.setCurrentWidget(self.research_page)
        self.research_page.open(username)
        self.research_page.setFocus()

    def close_research(self):
        self.close_prefs(force=True)

    def start_research(self, username: str, opts: dict, rid: str = "", account_name: str = "") -> bool:
        if self.research_job is not None and self.research_job.state not in research.STATES_FINAL:
            self.toast.show_message("Já existe uma pesquisa em andamento.")
            return False
        acc = next((a for a in self.cfg.accounts if account_name and a.name == account_name), None)
        if acc is not None and (self.current is None or self.current.account.id != acc.id):
            self.switch_to(acc.id)
        if self.current is None:
            self.toast.show_message("Abra uma conta logada para pesquisar.")
            return False
        root = research.research_root(self.download_root)
        try:
            job = research.ResearchJob(self.current, username, opts, rid or security.new_request_id(), root, self)
        except ValueError:
            self.toast.show_message("Pedido de pesquisa inválido.")
            return False
        self.research_job = job
        job.progress.connect(self.research_page.update_progress)
        job.finished.connect(self._research_finished)
        job.start()
        if self.stack.currentWidget() is self.research_page:
            self.research_page.render()
        self.toast.show_message(f"Pesquisando @{username} em segundo plano. Pode continuar usando o app.", 4000)
        return True

    def _research_finished(self, state: str, msg: str):
        job = self.research_job
        if self.stack.currentWidget() is self.research_page:
            self.research_page.render()
        if job is not None:
            self.toast.show_message(msg, 6000, "Ver pesquisa", lambda: self.open_research(job.username))

    def handle_research_request(self, req: dict):
        """Pedido vindo da linha de comando (ex.: Claude Code). Sempre pede aprovação."""
        username = research.clean_username(str(req.get("username", "")))
        rid = str(req.get("rid", ""))
        if not username or not security.is_request_id(rid):
            return
        opts = req.get("opts") if isinstance(req.get("opts"), dict) else {}
        account = str(req.get("account", ""))[:40]
        if self.outer.currentIndex() != 0:
            self._pending_research = req
            return
        self.raise_window()
        acc_name = account or (self.current.account.name if self.current else "conta atual")
        ok = self.confirm(
            "Permitir pesquisa de perfil?",
            f"Um programa no seu computador (linha de comando) pediu para pesquisar @{username} usando a conta "
            f"\"{acc_name}\". A coleta é somente leitura e fica salva em Downloads/Gramdesk/Pesquisas.\n\n"
            "Se você não pediu isso, recuse.",
            "Permitir",
        )
        if not ok or not self.start_research(username, opts, rid, account):
            research.mark_refused(research.research_root(self.download_root), username, rid,
                                  "Recusada pelo usuário." if not ok else "Não foi possível iniciar.")

    def _current_folder(self) -> str:
        return security.folder_name(self.current.account.name) if self.current else ""

    def _on_downloaded(self, path: str, msg: str):
        self.gallery.schedule_reload()
        if msg:
            folder = Path(path).parent.name if path else self._current_folder()
            self.toast.show_message(msg, 4000, "Ver downloads", lambda: self.open_gallery(folder))

    def close_prefs(self, force: bool = False):
        if self.stack.currentWidget() is not self.prefs and not force:
            return
        if self.current is not None:
            self.switch_to(self.current.account.id)
        else:
            self.header.show()
            self._start()

    def set_option(self, key: str, value):
        if key == "hibernate" and value in config.HIBERNATE_MODES:
            self.cfg.hibernate = value
        elif key in ("block_third_party", "block_telemetry"):
            setattr(self.cfg, key, bool(value))
        elif key == "feed_buttons":
            self.cfg.feed_buttons = bool(value)
            config.save(self.cfg)
            for s in self.sessions.values():
                s.reload_scripts()
            return
        elif key == "idle_lock" and value in config.IDLE_LOCK:
            self.cfg.idle_lock = value
            self._arm_idle()
        else:
            return
        config.save(self.cfg)

    def set_appearance(self, theme: str | None = None, accent: str | None = None, site: bool | None = None):
        site_before = self._site_scheme()
        if theme in config.THEMES:
            self.cfg.theme = theme
        if accent in config.ACCENTS:
            self.cfg.accent = accent
        if site is not None:
            self.cfg.site_follows_theme = bool(site)
        config.save(self.cfg)
        ui.apply_theme(QApplication.instance(), self.cfg.theme, self.cfg.accent)
        self._sync_ghost_icon()
        self._refresh_header()
        if self._site_scheme() != self._site_scheme_at_start:
            self.prefs.restart_needed = True
        elif site_before != self._site_scheme():
            self.prefs.restart_needed = False
        if self.stack.currentWidget() is self.prefs:
            self.prefs.render()

    def _site_scheme(self) -> str:
        if not self.cfg.site_follows_theme:
            return "default"
        return ui.MODE

    def restart(self):
        from PyQt6.QtCore import QProcess

        config.save(self.cfg)
        QProcess.startDetached(sys.executable, ["-m", "gramdesk", *sys.argv[1:]])
        self.close()
        QApplication.instance().quit()

    def _sync_ghost_icon(self):
        on = self.ghost_btn.isChecked()
        self.ghost_btn.setIcon(ui.icon("ghost", "#ffffff" if on else ui.TEXT, 18))

    # ---------- bloqueio por inatividade ----------
    def _arm_idle(self):
        if self.cfg.lock and self.cfg.idle_lock and self.outer.currentIndex() == 0:
            self._idle.start(self.cfg.idle_lock * 60_000)
        else:
            self._idle.stop()

    def _idle_timeout(self):
        if self.cfg.lock and self.cfg.idle_lock and self.outer.currentIndex() == 0:
            self._lock()

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.MouseMove):
            if self._idle.isActive():
                self._idle.start()
        return False

    def open_downloads(self, acc: config.Account | None = None):
        d = self.download_root
        if acc is not None:
            d = d / security.folder_name(acc.name)
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def set_password(self):
        has = bool(self.cfg.lock)
        sheet = ui.Sheet(
            self.outer,
            "Senha de bloqueio",
            "Protege a interface. Para proteger os dados no disco, use criptografia de disco.",
            width=420,
        )
        fields = {}
        for key, label, show in (("cur", "Senha atual", has), ("new", "Nova senha (mín. 6)", True), ("rep", "Repita a nova senha", True)):
            if not show:
                continue
            f = QLineEdit()
            f.setEchoMode(QLineEdit.EchoMode.Password)
            f.setPlaceholderText(label)
            f.returnPressed.connect(sheet.accept_default)
            sheet.body.addWidget(f)
            fields[key] = f
        err = QLabel("")
        err.setObjectName("error")
        sheet.body.addWidget(err)
        if has:
            sheet.add_button("Remover senha", "remove", "danger", left=True)
        sheet.add_button("Cancelar", None, "ghostBtn")
        sheet.add_button("Salvar", "save", "primaryWide", default=True)
        first = fields.get("cur") or fields["new"]
        action = sheet.exec(first)
        if action is None:
            return
        if has and not security.verify_password(fields["cur"].text(), self.cfg.lock):
            self.toast.show_message("Senha atual incorreta.")
            return
        if action == "remove":
            self.cfg.lock = None
            config.save(self.cfg)
            self._arm_idle()
            self._refresh_prefs()
            self.toast.show_message("Bloqueio por senha removido.")
            return
        new, rep = fields["new"].text(), fields["rep"].text()
        if len(new) < 6:
            self.toast.show_message("Use pelo menos 6 caracteres.")
            return
        if new != rep:
            self.toast.show_message("As senhas não conferem.")
            return
        self.cfg.lock = security.hash_password(new)
        config.save(self.cfg)
        self._arm_idle()
        self._refresh_prefs()
        self.toast.show_message("Senha definida. Ctrl+L bloqueia.")

    def _refresh_prefs(self):
        if self.stack.currentWidget() is self.prefs:
            self.prefs.render()

    def _lock(self, initial: bool = False):
        if not self.cfg.lock:
            if not initial:
                self.toast.show_message("Defina uma senha em Preferências > Privacidade para usar o bloqueio.")
            return
        self._idle.stop()
        self.outer.setCurrentIndex(1)
        self.lock_screen.reset()
        for s in self.sessions.values():
            QTimer.singleShot(0, s.hibernate)
        self._initial_lock = initial
        QTimer.singleShot(50, lambda: [self._paint_button(a.id) for a in self.cfg.accounts])

    def _unlock(self, pwd: str) -> bool:
        if not security.verify_password(pwd, self.cfg.lock):
            time.sleep(0.3)
            return False
        self.outer.setCurrentIndex(0)
        self._arm_idle()
        if self._initial_lock or self.current is None:
            self._initial_lock = False
            self._start()
        else:
            self.switch_to(self.current.account.id)
        QTimer.singleShot(0, self._flush_pending_link)
        return True

    def closeEvent(self, e):  # noqa: N802
        if self.research_job is not None and self.research_job.state not in research.STATES_FINAL:
            self.research_job.cancel()
        config.save(self.cfg)
        for s in self.sessions.values():
            s.close()
        super().closeEvent(e)


def _chromium_flags(cfg: config.Config, mode: str):
    # Lido pelo Qt WebEngine só ao criar o primeiro perfil: pode ser definido após o QApplication.
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    extra = ["--disable-background-networking"]
    if cfg.site_follows_theme:
        # prefers-color-scheme do site: 0 = escuro, 1 = claro
        extra.append(f"--blink-settings=preferredColorScheme={0 if mode == 'dark' else 1}")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags + [f for f in extra if f not in flags])


def _socket_name() -> str:
    """Caminho do socket de instância única.

    No Flatpak cada instância tem o próprio /tmp; o diretório $XDG_RUNTIME_DIR/app/<id> é
    compartilhado entre instâncias do mesmo app e só o usuário acessa (0700).
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    flatpak_id = os.environ.get("FLATPAK_ID", "")
    if runtime and os.path.isdir(runtime):
        base = os.path.join(runtime, "app", flatpak_id) if flatpak_id else runtime
        if os.path.isdir(base):
            return os.path.join(base, "gramdesk.sock")
    if os.name == "nt":
        import getpass

        # Named pipe do Windows; UserAccessOption restringe ao usuário atual.
        return "gramdesk-" + "".join(c for c in getpass.getuser() if c.isalnum())[:40]
    return f"gramdesk-{os.getuid()}"


SOCKET_NAME = _socket_name()


def _forward_to_running(args: list[str]) -> bool:
    """Se já existe uma janela aberta, entrega o link para ela e encerra esta."""
    from PyQt6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if not sock.waitForConnected(300):
        return False
    req = _research_args(args)
    if req:
        payload = "R " + json.dumps(req)
    else:
        payload = next((a for a in args if security.parse_instagram_input(a)), "") or "show"
    sock.write(payload.encode()[:4096] + b"\n")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def _research_args(args: list[str]) -> dict | None:
    """--research PERFIL [--rid HEX] [--posts N] [--account NOME] [--no-comments] [--no-highlights] [--videos]"""
    if "--research" not in args:
        return None
    i = args.index("--research")
    if i + 1 >= len(args):
        return None

    def val(flag, default=""):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default

    try:
        posts = int(val("--posts", "60"))
    except ValueError:
        posts = 60
    return {
        "username": args[i + 1],
        "rid": val("--rid") or security.new_request_id(),
        "account": val("--account"),
        "opts": {
            "posts": posts,
            "comments": "--no-comments" not in args,
            "highlights": "--no-highlights" not in args,
            "videos": "--videos" in args,
        },
    }


def _listen(win: "MainWindow"):
    from PyQt6.QtNetwork import QLocalServer

    server = QLocalServer(win)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(SOCKET_NAME):
        QLocalServer.removeServer(SOCKET_NAME)  # socket órfão de uma execução que caiu
        server.listen(SOCKET_NAME)

    def accept():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def read():
            data = bytes(conn.readAll()).decode("utf-8", "replace").strip()[:4096]
            conn.disconnectFromServer()
            if data.startswith("R "):
                try:
                    req = json.loads(data[2:])
                except ValueError:
                    return
                if isinstance(req, dict):
                    win.handle_research_request(req)
                return
            win.raise_window()
            if data and data != "show":
                win.open_link(data)

        conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    return server


def _selftest(app) -> int:
    """Autoteste usado no CI (Windows/Linux): sobe a janela, carrega o Instagram e confere o script injetado."""
    out = os.environ.get("GRAMDESK_SELFTEST_OUT", "")
    cfg = config.Config(accounts=[config.Account.new("Selftest")])
    ui.apply_theme(app, "dark", "instagram")
    _chromium_flags(cfg, ui.MODE)
    win = MainWindow(cfg)
    win._site_scheme_at_start = win._site_scheme()
    win.resize(1200, 800)
    win.show()
    result = {"ok": False, "version": config.VERSION, "platform": sys.platform}

    def finish():
        s = win.current
        if s is None or s.page is None:
            result["error"] = "sem sessão"
            write()
            return

        def got(r):
            result.update({
                "url": s.page.url().toString(),
                "bridge": r == "object",
                "blocked": s.blocked_count,
                "title": s.page.title()[:80],
            })
            result["ok"] = bool(result["bridge"] and "instagram.com" in result["url"])
            write()

        s.page.runJavaScript(f"typeof window[{json.dumps(s._key)}]", 0, got)

    def write():
        data = json.dumps(result, ensure_ascii=False)
        if out:
            Path(out).write_text(data, encoding="utf-8")
        else:
            print(data, flush=True)
        win.close()
        app.exit(0 if result["ok"] else 1)

    QTimer.singleShot(25000, finish)
    QTimer.singleShot(90000, lambda: (result.setdefault("error", "timeout"), write()))
    return app.exec()


def main():
    os.umask(0o077)  # tudo que o app criar fica legível só pelo usuário
    if os.name == "nt":
        try:  # ícone e agrupamento corretos na barra de tarefas
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(config.APP_ID)
        except (AttributeError, OSError):
            pass
    cfg = config.load()
    QApplication.setApplicationName("gramdesk")
    QApplication.setApplicationDisplayName("Gramdesk")
    QApplication.setDesktopFileName(config.APP_ID)
    app = QApplication(sys.argv)
    if "--selftest" in sys.argv:
        sys.exit(_selftest(app))
    if _forward_to_running(sys.argv[1:]):
        return
    ui.apply_theme(app, cfg.theme, cfg.accent)
    _chromium_flags(cfg, ui.MODE)
    app.setWindowIcon(_app_icon())
    config.purge_pending(cfg)
    win = MainWindow(cfg)
    win._site_scheme_at_start = win._site_scheme()
    win._server = _listen(win)
    first_link = next((security.parse_instagram_input(a) for a in sys.argv[1:] if security.parse_instagram_input(a)), None)
    if first_link:
        win._pending_link = first_link
    first_research = _research_args(sys.argv[1:])
    if first_research:
        win._pending_research = first_research
    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):
        hints.colorSchemeChanged.connect(lambda _s: cfg.theme == "system" and win.set_appearance())
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
