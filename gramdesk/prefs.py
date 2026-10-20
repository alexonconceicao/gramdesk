"""Tela de Preferências: aparência, contas, privacidade, downloads, atalhos e sobre."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import config, donation, security, ui
from .config import VERSION

SECTIONS = (
    ("appearance", "Aparência", "palette"),
    ("accounts", "Contas", "user"),
    ("privacy", "Privacidade e segurança", "shield"),
    ("downloads", "Downloads", "folder"),
    ("research", "Pesquisa de perfil", "search"),
    ("shortcuts", "Atalhos", "keyboard"),
    ("about", "Sobre", "info"),
)

SHORTCUTS = (
    ("Ctrl+1 … 9", "Trocar de conta"),
    ("Ctrl+D", "Baixar a mídia aberta"),
    ("Ctrl+Shift+D", "Baixar todos os stories do perfil"),
    ("Ctrl+J", "Meus downloads (galeria)"),
    ("Ctrl+K", "Colar link ou @perfil e abrir na conta atual"),
    ("Ctrl+Shift+F", "Pesquisa de perfil (coleta para análise)"),
    ("Ctrl+G", "Ligar/desligar modo fantasma"),
    ("Ctrl+L", "Bloquear o app"),
    ("Ctrl+,", "Preferências"),
    ("Ctrl+Shift+,", "Configurações da conta atual"),
    ("Ctrl+R / F5", "Recarregar"),
    ("Alt+← / Alt+→", "Voltar / avançar"),
    ("Ctrl+= / Ctrl+- / Ctrl+0", "Zoom da conta"),
    ("Esc", "Fechar diálogo ou Preferências"),
)


def _label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def _section_title(text: str) -> QLabel:
    return _label(text.upper(), "sectionTitle")


def _button(text: str, slot, role: str = "", icon_name: str = "", icon_role: str = "text") -> QPushButton:
    b = QPushButton(("  " + text) if icon_name else text)
    if role:
        b.setObjectName(role)
    if icon_name:
        ui.themed_icon(b, icon_name, icon_role, 18)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(slot)
    return b


class PreferencesPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("prefs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.section = "appearance"
        self.account_id = ""
        self.restart_needed = False

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("prefsNav")
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav.setFixedWidth(250)
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(14, 16, 14, 16)
        nv.setSpacing(4)
        back = _button("Voltar ao Instagram", self.win.close_prefs, "nav", "back")
        nv.addWidget(back)
        nv.addSpacing(10)
        nv.addWidget(_label("Preferências", "pageTitle"))
        nv.addSpacing(10)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav: dict[str, QPushButton] = {}
        for key, title, icon_name in SECTIONS:
            b = _button(title, lambda _=False, key=key: self.open(key), "nav", icon_name)
            b.setCheckable(True)
            b.setIconSize(QSize(18, 18))
            self._nav_group.addButton(b)
            self._nav[key] = b
            nv.addWidget(b)
        nv.addStretch()
        nv.addWidget(_label(f"Gramdesk {VERSION}", "muted"))
        h.addWidget(nav)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("prefsViewport")
        h.addWidget(self.scroll, 1)

    # ---------- navegação ----------
    def open(self, section: str = "", account_id: str = ""):
        if section:
            self.section = section
        if account_id:
            self.account_id = account_id
        self._nav[self.section].setChecked(True)
        self.render()

    def render(self):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        page = QWidget()
        page.setObjectName("prefsPage")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(40, 30, 40, 40)
        col = QWidget()
        col.setMaximumWidth(780)
        self.body = QVBoxLayout(col)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        outer.addWidget(col, 1)
        title = dict((k, t) for k, t, _ in SECTIONS)[self.section]
        self.body.addWidget(_label(title, "pageTitle"))
        self.body.addSpacing(6)
        getattr(self, "_" + self.section)()
        self.body.addStretch()
        self.scroll.setWidget(page)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.win.close_prefs()
        else:
            super().keyPressEvent(e)

    # ---------- seções ----------
    def _appearance(self):
        cfg = self.win.cfg
        b = self.body
        b.addWidget(_section_title("Tema"))
        seg = ui.Segmented([("system", "Sistema"), ("light", "Claro"), ("dark", "Escuro")], cfg.theme)
        seg.changed.connect(lambda v: self.win.set_appearance(theme=v))
        b.addWidget(ui.setting_row("Tema do app", "Sistema acompanha o modo claro/escuro do GNOME/KDE.", seg))
        site = ui.Toggle(cfg.site_follows_theme)
        site.toggled.connect(lambda v: self.win.set_appearance(site=v))
        b.addWidget(ui.setting_row(
            "Instagram acompanha o tema",
            "O site usa o mesmo modo claro/escuro do app. Mudanças valem ao reiniciar o Gramdesk.",
            site,
        ))
        if self.restart_needed:
            row = QFrame()
            row.setObjectName("row")
            rh = QHBoxLayout(row)
            rh.setContentsMargins(14, 10, 10, 10)
            rh.addWidget(_label("Reinicie para o Instagram trocar de tema.", wrap=True), 1)
            rh.addWidget(_button("Reiniciar agora", self.win.restart, "primary", "refresh", "white"))
            b.addWidget(row)

        b.addSpacing(10)
        b.addWidget(_section_title("Cor de destaque"))
        panel = QFrame()
        panel.setObjectName("row")
        grid = QHBoxLayout(panel)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setSpacing(18)
        for key, (name, stops, _accent) in ui.ACCENTS.items():
            cell = QVBoxLayout()
            cell.setSpacing(6)
            sw = ui.AccentSwatch(stops)
            sw.setChecked(cfg.accent == key)
            sw.clicked.connect(lambda _=False, key=key: self.win.set_appearance(accent=key))
            cell.addWidget(sw, 0, Qt.AlignmentFlag.AlignHCenter)
            cell.addWidget(_label(name, "muted"), 0, Qt.AlignmentFlag.AlignHCenter)
            grid.addLayout(cell)
        grid.addStretch()
        b.addWidget(panel)

    def _accounts(self):
        win, cfg = self.win, self.win.cfg
        b = self.body
        if not cfg.accounts:
            b.addWidget(_label("Nenhuma conta ainda.", "muted"))
            b.addWidget(_button("Adicionar conta", win.add_account, "primaryWide", "plus", "white"), 0, Qt.AlignmentFlag.AlignLeft)
            return
        if not cfg.account(self.account_id):
            self.account_id = (win.current.account.id if win.current else cfg.accounts[0].id)

        cols = QHBoxLayout()
        cols.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(8)
        group = QButtonGroup(self)
        for acc in cfg.accounts:
            row = QPushButton(f"  {acc.name}\n  {win.state_label(acc.id)}")
            row.setObjectName("accountRow")
            row.setCheckable(True)
            row.setChecked(acc.id == self.account_id)
            row.setIcon(ui.avatar_icon(acc.name, win.color(acc), 36))
            row.setIconSize(QSize(36, 36))
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setMinimumWidth(220)
            row.clicked.connect(lambda _=False, i=acc.id: self.open("accounts", i))
            group.addButton(row)
            left.addWidget(row)
        left.addWidget(_button("Adicionar conta", win.add_account, "ghostBtn", "plus", "muted"), 0, Qt.AlignmentFlag.AlignLeft)
        left.addStretch()
        cols.addLayout(left)
        cols.addWidget(self._account_detail(cfg.account(self.account_id)), 1)
        b.addLayout(cols)

    def _account_detail(self, acc: config.Account) -> QFrame:
        win = self.win
        s = win.sessions.get(acc.id)
        panel = QFrame()
        panel.setObjectName("panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        avatar = ui.BigAvatar(acc.name, win.color(acc), 60)
        head.addWidget(avatar)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label(acc.name, "sheetTitle"))
        col.addWidget(_label(win.state_label(acc.id).capitalize(), "muted"))
        head.addLayout(col, 1)
        head.addWidget(_button("Abrir conta", lambda: win.switch_to(acc.id), "primary", "forward", "white"))
        v.addLayout(head)

        v.addWidget(_section_title("Identificação"))
        name = QLineEdit(acc.name)
        name.setMaxLength(40)
        name.setPlaceholderText("Nome da conta")
        name.editingFinished.connect(lambda: win.update_account(acc.id, name=name.text()) and self._refresh_soon())
        v.addWidget(name)
        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        for i, c in enumerate(ui.PALETTE):
            sw = ui.Swatch(c)
            sw.setChecked(i == acc.color)
            sw.clicked.connect(lambda _=False, i=i: (win.update_account(acc.id, color=i), avatar.set_data(acc.name, win.color(acc))))
            swatches.addWidget(sw)
        swatches.addStretch()
        v.addLayout(swatches)

        v.addWidget(_section_title("Comportamento"))
        ghost = ui.Toggle(acc.ghost)
        ghost.toggled.connect(lambda on: win.update_account(acc.id, ghost=on))
        v.addWidget(ui.setting_row("Modo fantasma", "Abrir stories sem aparecer nas visualizações.", ghost))
        keep = ui.Toggle(acc.keep_alive)
        keep.toggled.connect(lambda on: win.update_account(acc.id, keep_alive=on))
        v.addWidget(ui.setting_row(
            "Manter ativa em segundo plano",
            "Não hiberna ao trocar de conta. Útil para acompanhar DMs, mas usa mais memória.",
            keep,
        ))
        zrow = QFrame()
        zrow.setObjectName("row")
        zh = QHBoxLayout(zrow)
        zh.setContentsMargins(14, 12, 14, 12)
        zc = QVBoxLayout()
        zc.setSpacing(2)
        zc.addWidget(_label("Zoom da página", "rowTitle"))
        zc.addWidget(_label("Tamanho do Instagram nesta conta.", "muted"))
        zh.addLayout(zc, 1)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(config.ZOOM_MIN // 10, config.ZOOM_MAX // 10)
        slider.setValue(acc.zoom // 10)
        slider.setFixedWidth(170)
        val = _label(f"{acc.zoom}%", "rowTitle")
        val.setFixedWidth(44)
        slider.valueChanged.connect(lambda z: (val.setText(f"{z * 10}%"), win.update_account(acc.id, zoom=z * 10)))
        zh.addWidget(slider)
        zh.addWidget(val)
        v.addWidget(zrow)

        v.addWidget(_section_title("Dados"))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        loaded = bool(s and s.loaded)
        reload_btn = _button("Recarregar", lambda: s and s.page and s.page.triggerAction(s.page.WebAction.Reload), "", "refresh")
        reload_btn.setEnabled(loaded)
        cache_btn = _button("Limpar cache", lambda: s and s.clear_cache(), "", "broom")
        cache_btn.setEnabled(loaded)
        actions.addWidget(reload_btn)
        actions.addWidget(cache_btn)
        actions.addWidget(_button("Ver downloads", lambda: win.open_gallery(security.folder_name(acc.name)), "", "gallery"))
        actions.addStretch()
        v.addLayout(actions)

        order = QHBoxLayout()
        order.setSpacing(8)
        idx = [a.id for a in win.cfg.accounts].index(acc.id)
        up = _button("Mover para cima", lambda: (win.move_account(acc.id, -1), self.render()), "", "up")
        up.setEnabled(idx > 0)
        down = _button("Mover para baixo", lambda: (win.move_account(acc.id, 1), self.render()), "", "down")
        down.setEnabled(idx < len(win.cfg.accounts) - 1)
        order.addWidget(up)
        order.addWidget(down)
        order.addStretch()
        v.addLayout(order)

        v.addSpacing(6)
        danger = QHBoxLayout()
        danger.addWidget(_label("Sair e apagar cookies, cache e sessão desta conta neste computador.", "muted", True), 1)
        danger.addWidget(_button("Remover conta", lambda: win.remove_account(acc.id), "danger", "trash", "danger"))
        v.addLayout(danger)
        return panel

    def _refresh_soon(self):
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(0, self.render)
        return True

    def _privacy(self):
        win, cfg = self.win, self.win.cfg
        b = self.body
        b.addWidget(_section_title("Rede"))
        for key, title, desc, value in (
            ("block_third_party", "Bloquear rastreadores de terceiros",
             "Só carrega domínios do Instagram/Meta e CDNs de mídia.", cfg.block_third_party),
            ("block_telemetry", "Bloquear telemetria do Instagram",
             "Descarta os envios de log conhecidos do site.", cfg.block_telemetry),
        ):
            t = ui.Toggle(value)
            t.toggled.connect(lambda v, key=key: win.set_option(key, v))
            b.addWidget(ui.setting_row(title, desc, t))

        b.addSpacing(8)
        b.addWidget(_section_title("Memória"))
        seg = ui.Segmented([("discard", "Descartar"), ("freeze", "Congelar")], cfg.hibernate)
        seg.changed.connect(lambda v: win.set_option("hibernate", v))
        b.addWidget(ui.setting_row(
            "Contas inativas",
            "Descartar libera a RAM (recarrega ao voltar). Congelar pausa sem liberar (volta na hora).",
            seg,
        ))

        b.addSpacing(8)
        b.addWidget(_section_title("Bloqueio"))
        pw = _button("Alterar senha" if cfg.lock else "Definir senha", win.set_password, "", "key")
        b.addWidget(ui.setting_row(
            "Senha do app",
            "Ligada: pede senha ao abrir e no Ctrl+L." if cfg.lock else "Desligada. Defina para ativar o bloqueio.",
            pw,
        ))
        combo = QComboBox()
        for minutes in config.IDLE_LOCK:
            combo.addItem("Nunca" if minutes == 0 else f"Após {minutes} min", minutes)
        combo.setCurrentIndex(config.IDLE_LOCK.index(cfg.idle_lock))
        combo.setEnabled(bool(cfg.lock))
        combo.currentIndexChanged.connect(lambda i: win.set_option("idle_lock", config.IDLE_LOCK[i]))
        combo.setMinimumWidth(150)
        b.addWidget(ui.setting_row("Bloquear por inatividade", "Bloqueia sozinho quando ninguém mexe no app.", combo))

    def _downloads(self):
        win = self.win
        b = self.body
        b.addWidget(ui.setting_row(
            "Meus downloads",
            "Galeria com tudo que você baixou: filtro por conta, fotos e vídeos, player embutido.",
            _button("Ver downloads", lambda: win.open_gallery(), "primary", "gallery", "white"),
        ))
        fb = ui.Toggle(win.cfg.feed_buttons)
        fb.toggled.connect(lambda v: win.set_option("feed_buttons", v))
        b.addWidget(ui.setting_row(
            "Botão de baixar em cada mídia",
            "Mostra um botão sobre cada foto, vídeo e slide de carrossel no feed, reels e stories.",
            fb,
        ))
        b.addWidget(ui.setting_row(
            "Pasta de downloads",
            f"{win.download_root}\nCada conta tem a própria subpasta.",
            _button("Abrir", lambda: win.open_downloads(), "", "folder"),
        ))
        b.addWidget(ui.setting_row(
            "O que é aceito",
            "Só fotos e vídeos vindos das CDNs do Instagram, pedidos pelo próprio app. "
            "O site não consegue iniciar downloads sozinho. Itens já baixados não são duplicados.",
            QWidget(),
        ))

    def _research(self):
        win = self.win
        lim = win.research_limits()
        b = self.body
        b.addWidget(_label(
            "Limites para não martelar o Instagram e proteger a conta. Valores por conta; os padrões são conservadores. "
            "Se o Instagram pedir para ir mais devagar, a coleta para na hora e a conta entra em resfriamento "
            "(30 min, 1 h, 2 h... até 24 h), que zera após uma coleta bem-sucedida.",
            "muted", True,
        ))

        def seg(key, options, current):
            w = ui.Segmented([(str(v), lbl) for v, lbl in options], str(current))
            w.changed.connect(lambda v, key=key: (win.set_option("research_limits", {key: float(v) if "." in v else int(v)}),
                                                  self._refresh_soon()))
            return w

        b.addWidget(_section_title("Cota diária"))
        b.addWidget(ui.setting_row("Pesquisas por dia", "Coletas que acessam o Instagram, por conta.",
                                   seg("daily_researches", [(1, "1"), (2, "2"), (3, "3"), (5, "5")], lim.daily_researches)))
        b.addWidget(ui.setting_row("Itens por dia", "Requisições à API por conta (perfil, página de posts, comentários, destaques).",
                                   seg("daily_items", [(100, "100"), (200, "200"), (400, "400")], lim.daily_items)))
        b.addWidget(_section_title("Ritmo"))
        b.addWidget(ui.setting_row("Itens por hora", f"Teto na última hora. Por minuto: {lim.per_minute}.",
                                   seg("per_hour", [(30, "30"), (60, "60"), (90, "90")], lim.per_hour)))
        pace = ui.Segmented([("2.5", "Normal"), ("4.0", "Lento"), ("6.0", "Muito lento")], f"{lim.min_delay:.1f}")
        pace.changed.connect(lambda v: (win.set_option("research_limits", {"min_delay": float(v), "max_delay": float(v) * 2.4}),
                                        self._refresh_soon()))
        b.addWidget(ui.setting_row("Intervalo entre requisições",
                                   f"Agora: {lim.min_delay:g} a {lim.max_delay:g} s, com pausa de {lim.long_min:g} a {lim.long_max:g} s "
                                   f"a cada {lim.long_every} itens.", pace))
        b.addWidget(ui.setting_row("Cache", f"Pesquisas do mesmo perfil feitas nas últimas {lim.cache_hours} h são reaproveitadas; "
                                   "só o que falta é buscado.", QWidget()))
        cur = win.current
        if cur is not None:
            st = win.rate.status(cur.account.id, lim)
            txt = st["message"] or (f"Liberada. Hoje: {st['researches_today']} pesquisas, {st['items_today']} itens.")
            b.addWidget(ui.setting_row(f"Situação de {cur.account.name}", txt, QWidget()))

    def _shortcuts(self):
        panel = QFrame()
        panel.setObjectName("panel")
        g = QGridLayout(panel)
        g.setContentsMargins(20, 16, 20, 16)
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(12)
        for r, (keys, desc) in enumerate(SHORTCUTS):
            k = _label(keys, "kbd")
            g.addWidget(k, r, 0, Qt.AlignmentFlag.AlignLeft)
            g.addWidget(_label(desc), r, 1)
        g.setColumnStretch(1, 1)
        self.body.addWidget(panel)

    def _about(self):
        b = self.body
        panel = QFrame()
        panel.setObjectName("panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(self.win.windowIcon().pixmap(QSize(56, 56)))
        head.addWidget(logo)
        col = QVBoxLayout()
        col.addWidget(_label("Gramdesk", "sheetTitle"))
        col.addWidget(_label(f"Versão {VERSION}  ·  Licença MIT", "muted"))
        head.addLayout(col, 1)
        v.addLayout(head)
        v.addWidget(_label(
            "Cliente desktop leve para o Instagram, com motor Chromium (Qt WebEngine), contas isoladas, "
            "hibernação, modo fantasma e downloads. Sem telemetria e sem servidores próprios.",
            wrap=True,
        ))
        links = QLabel(
            f"<a style='color:{ui.ACCENT}' href='https://github.com/alexonconceicao/gramdesk'>Código no GitHub</a>"
            f"  ·  <a style='color:{ui.ACCENT}' href='https://github.com/alexonconceicao/gramdesk/blob/main/SECURITY.md'>Segurança</a>"
            f"  ·  <a style='color:{ui.ACCENT}' href='https://github.com/alexonconceicao/gramdesk/issues'>Reportar problema</a>"
        )
        links.setOpenExternalLinks(True)
        v.addWidget(links)
        v.addWidget(_label("Projeto independente, sem vínculo com a Meta ou o Instagram.", "muted", True))
        if donation.enabled():
            v.addWidget(_button(donation.CONFIG["button"], self.win.ask_donation, "primary", "gift", "white"), 0, Qt.AlignmentFlag.AlignLeft)
        b.addWidget(panel)
