"""Tela de Seguidores: checagem, não seguidores, famosos, seleção para o lote, whitelist ("manter"),
lote de remoções ao vivo, quem deixou de seguir e auditoria."""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import follows, ui
from .research_ui import _button, _label

LIST_TABS = ("comuns", "famosos", "sem-contagem", "mantidos")
MAX_ROWS = 3000
STATUS_TEXT = {"pendente": "Na fila", "ativo": "Removendo agora...", "removido": "Removido", "erro": "Erro"}
AUDIT_TEXT = {
    "checagem": lambda r: (f"Checagem: {r.get('followers', 0)} seguidores, {r.get('following', 0)} seguindo, "
                           f"{r.get('lost', 0)} deixaram de seguir, {r.get('gained', 0)} novos"
                           + (" (primeira)" if r.get("first") else "")),
    "checagem-parcial": lambda r: f"Checagem parcial, parou no teto ({r.get('reason', '')}); continua na próxima",
    "deixou-de-seguir": lambda r: f"@{r.get('user', '?')} deixou de seguir" + (" (você ainda segue)" if r.get("i_follow") else ""),
    "voltou-a-seguir": lambda r: f"@{r.get('user', '?')} voltou a seguir",
    "deixei-de-seguir": lambda r: f"Você deixou de seguir @{r.get('user', '?')}"
                                  + (f" ({_fmt_count(r['count'])} seguidores)" if isinstance(r.get("count"), int) else ""),
    "lote-inicio": lambda r: f"Lote ({_mode_label(r.get('mode'))}): {r.get('total', 0)} perfis entraram",
    "lote-pausa": lambda r: f"Lote pausado ({r.get('reason', '')}), {r.get('done', 0)} removidos nesta sessão",
    "lote-fim": lambda r: f"Lote concluído: {r.get('done', 0)} removidos, {r.get('errors', 0)} com erro",
    "contagem": lambda r: f"Contagem de seguidores: {r.get('n', 0)} perfis",
    "bloqueio": lambda r: f"Bloqueio do Instagram ({r.get('kind', '')}); resfriamento até {r.get('cooldown_until', '?')}",
    "desacelerar": lambda r: (f"O Instagram pediu calma ({r.get('task', '')}): pausa de {r.get('minutes', '?')} min, "
                              f"ritmo {r.get('slow', '?')}x mais lento, seguiu sozinho"),
    "manter": lambda r: f"@{r.get('user', '?')} entrou na lista de manter",
    "desmarcar-manter": lambda r: f"@{r.get('user', '?')} saiu da lista de manter",
}


def _fmt_count(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 1000:
        return f"{n / 1000:.1f} mil".replace(".", ",").replace(",0 ", " ")
    return str(n)


def _mode_label(mode) -> str:
    return {"famosos": "não seguidores famosos", "selecao": "seleção"}.get(mode, "não seguidores")


def _when(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "nunca"


def estimate(n: int, lim) -> str:
    """Tempo aproximado de um lote com o ritmo atual."""
    cap = max(1, min(lim.unfollow_daily, 200))
    if n > cap:
        return f"cerca de {math.ceil(n / cap)} dias ({cap} por dia)"
    per = (lim.unfollow_min_delay + lim.unfollow_max_delay) / 2
    secs = n * max(per, 3600 / lim.unfollow_per_hour) + (n // lim.unfollow_long_every) * 300
    h, m = divmod(int(secs / 60) + 1, 60)
    return f"cerca de {h} h {m:02d} min" if h else f"cerca de {m} min"


class FollowsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("prefs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("prefsViewport")
        outer.addWidget(self.scroll, 1)
        self.bar = self._build_bar()
        outer.addWidget(self.bar)
        self.tab = "comuns"
        self.filter = ""
        self.status = None
        self.info = None
        self.rows: dict[str, dict] = {}
        self._rendered_tab = ""
        self._filter_timer = QTimer(self, singleShot=True, interval=150, timeout=self._apply_filter)
        win.follows.progress.connect(self._progress)
        win.follows.changed.connect(self._changed)
        win.follows.item.connect(self._on_item)

    # ---------------------------------------------------------------- dados
    def _ctx(self):
        cur = self.win.current
        if cur is None:
            return None, None, None
        ctl = self.win.follows
        return cur, ctl.store(cur.account.id), ctl.batch(cur.account.id)

    def _tab_items(self, st, lim) -> dict[str, list[dict]]:
        groups = st.split(lim.famous_threshold)
        groups["mantidos"] = [u for u in st.non_followers() if u["keep"]]
        return groups

    # ---------------------------------------------------------------- sinais
    def _changed(self):
        if self.isVisible():
            self.render()

    def _progress(self, text: str):
        if self.status is None:
            return
        try:
            self.status.setText(text)
        except RuntimeError:
            self.status = None

    def _on_item(self, pk: str, state: str):
        """Atualização ao vivo de uma linha (lote andando), sem redesenhar a tela."""
        if not self.isVisible():
            return
        r = self.rows.get(pk)
        if r is not None:
            try:
                self._paint_row(r, "" if state == "fora" else state)
            except RuntimeError:
                self.rows.pop(pk, None)
        self._refresh_info()
        self._refresh_batch_info()
        self._update_bar()

    # ---------------------------------------------------------------- tela
    def open(self):
        self.render()

    def render(self):
        keep_scroll = self.scroll.verticalScrollBar().value() if self._rendered_tab == self.tab else 0
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.status = self.info = self.batch_info = None
        self.rows = {}
        self._rendered_tab = self.tab
        page = QWidget()
        page.setObjectName("prefsPage")
        h = QHBoxLayout(page)
        h.setContentsMargins(36, 22, 36, 24)
        col = QWidget()
        col.setMaximumWidth(920)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        h.addWidget(col, 1)

        top = QHBoxLayout()
        top.addWidget(_button("Voltar ao Instagram", self.win.close_research, "nav", "back"))
        top.addStretch()
        v.addLayout(top)
        v.addWidget(_label("Seguidores", "pageTitle"))
        cur, st, batch = self._ctx()
        if cur is None:
            v.addWidget(_label("Abra uma conta primeiro.", "muted"))
            v.addStretch()
            self.scroll.setWidget(page)
            self._update_bar()
            return
        lim = self.win.follows.limits()
        v.addWidget(self._head(cur, st, lim))
        groups = self._tab_items(st, lim)
        pend = sum(1 for p in (batch or {}).get("items", []) if st.batch_status(batch, p) == "pendente")
        tabs = ui.Segmented([
            ("comuns", f"Não seguidores ({len(groups['comuns'])})"),
            ("famosos", f"Famosos ({len(groups['famosos'])})"),
            ("sem-contagem", f"Sem contagem ({len(groups['sem-contagem'])})"),
            ("mantidos", f"Manter ({len(groups['mantidos'])})"),
            ("lote", f"Lote ({pend})"),
            ("deixaram", f"Deixaram ({len(st.unfollowers)})"),
            ("auditoria", "Auditoria"),
        ], self.tab)
        tabs.changed.connect(self._set_tab)
        v.addWidget(tabs, 0, Qt.AlignmentFlag.AlignLeft)

        if self.tab in (*LIST_TABS, "deixaram", "lote"):
            self.field = QLineEdit(self.filter)
            self.field.setPlaceholderText("Filtrar por @usuário ou nome (na hora)")
            self.field.setClearButtonEnabled(True)
            self.field.textChanged.connect(self._filter_changed)
            v.addWidget(self.field)

        if not st.has_snapshot and self.tab not in ("auditoria", "deixaram"):
            v.addWidget(_label("Nenhuma checagem ainda. Clique em \"Checar agora\".", "muted"))
        elif self.tab in LIST_TABS:
            self._list_tab(v, groups[self.tab], batch, st)
        elif self.tab == "lote":
            self._batch_tab(v, st, batch, lim)
        elif self.tab == "deixaram":
            self._unfollowers_tab(v, st, batch)
        else:
            self._audit_tab(v, st)
        v.addStretch()
        self.scroll.setWidget(page)
        self._apply_filter()
        self._update_bar()
        if keep_scroll:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(keep_scroll))

    def _head(self, cur, st, lim) -> QFrame:
        ctl = self.win.follows
        panel = QFrame()
        panel.setObjectName("panel")
        f = QVBoxLayout(panel)
        f.setContentsMargins(20, 16, 20, 16)
        f.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(_label(f"Conta: {cur.account.name}", "rowTitle"), 1)
        busy = ctl.busy
        if not busy and st.has_snapshot and self.win.follows.summary(cur.account.id)["unknown"]:
            cnt = _button("Contar seguidores", lambda: self._run("counts"), "", "search")
            cnt.setToolTip("Busca quantos seguidores tem cada não seguidor, para separar os famosos.")
            cnt.setEnabled(cur.logged_in)
            row.addWidget(cnt)
        chk = _button("Checar agora", lambda: self._run("check"), "primaryWide", "refresh", "white")
        chk.setEnabled(not busy and cur.logged_in)
        row.addWidget(chk)
        f.addLayout(row)
        self.info = _label("", "muted", True)
        f.addWidget(self.info)
        self._refresh_info()
        if not cur.logged_in:
            f.addWidget(_label("Entre na conta para usar esta tela.", "error", True))
        u = ctl.summary(cur.account.id)["unfollow"]
        if not u["ok"] and u["reason"] == "cooldown":
            f.addWidget(_label(u["message"], "error", True))
        if busy:
            run = QHBoxLayout()
            run.addWidget(_label(f"Em andamento: {_job_label(ctl.job)}", "rowTitle"), 1)
            run.addWidget(_button("Pausar", ctl.cancel, "danger", "pause", "danger"))
            f.addLayout(run)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setRange(0, 0)
            f.addWidget(bar)
        self.status = _label(ctl.last_message or "", "muted", True)
        f.addWidget(self.status)
        return panel

    def _refresh_info(self):
        cur, st, batch = self._ctx()
        if cur is None or self.info is None:
            return
        s = self.win.follows.summary(cur.account.id)
        lim = self.win.follows.limits()
        b = s["batch"]
        txt = (f"Última checagem: {_when(st.taken_at)}  ·  {s['followers']} seguidores  ·  {s['following']} seguindo  ·  "
               f"{s['non_followers']} não seguem de volta  ·  famoso = {_fmt_count(lim.famous_threshold)}+  ·  "
               f"remoções hoje {s['unfollow']['unfollows_today']} de {s['unfollow']['unfollow_cap']}")
        if b:
            txt += f"  ·  lote: {b['done']} de {b['total']} removidos" + (f", {b['errors']} com erro" if b["errors"] else "")
        try:
            self.info.setText(txt)
        except RuntimeError:
            self.info = None

    # ---------------------------------------------------------------- abas
    def _list_tab(self, v, items: list[dict], batch, st):
        hint = {
            "comuns": "Quem você segue e não segue de volta, abaixo do limite de famoso. Marque quem sai e clique "
                      "em \"Deixar de seguir\" lá embaixo; a seleção vale entre as abas.",
            "famosos": "Não seguidores acima do limite de famoso. Marque os que quer deixar de seguir; os demais "
                       "podem ir para \"Manter\".",
            "sem-contagem": "Ainda sem contagem de seguidores (podem ser famosos). Dá para selecionar mesmo assim "
                            "ou usar \"Contar seguidores\" antes.",
            "mantidos": "Whitelist: perfis aqui nunca entram em lote. Selecione e use \"Tirar de manter\" para liberar.",
        }[self.tab]
        v.addWidget(_label(hint, "muted", True))
        if not items:
            v.addWidget(_label("Ninguém aqui.", "muted"))
        for u in items[:MAX_ROWS]:
            v.addWidget(self._user_row(u, st, batch))
        if len(items) > MAX_ROWS:
            v.addWidget(_label(f"Mostrando {MAX_ROWS} de {len(items)}; use o filtro.", "muted"))

    def _batch_tab(self, v, st, batch, lim):
        ctl = self.win.follows
        acc = self.win.current.account.id
        if not batch:
            v.addWidget(_label("Nenhum lote. Selecione perfis nas outras abas e clique em \"Deixar de seguir\".", "muted", True))
            return
        running = ctl.running_unfollow(acc)
        items = batch["items"]
        pend = [p for p in items if st.batch_status(batch, p) == "pendente" and p in st.following]
        line = QHBoxLayout()
        self.batch_info = _label("", "muted", True)
        line.addWidget(self.batch_info, 1)
        self._refresh_batch_info()
        if not running and pend:
            b = _button("Retomar", lambda: self._run("unfollow"), "primary", "play", "white")
            b.setEnabled(self._can_act())
            line.addWidget(b)
        if not running:
            line.addWidget(_button("Descartar lote", self._discard_batch, "danger", "trash", "danger"))
        v.addLayout(line)
        order = sorted(items, key=lambda p: {"pendente": 0, "erro": 1, "removido": 2}.get(st.batch_status(batch, p), 3))
        for pk in order[:MAX_ROWS]:
            e = st.following.get(pk) or self._known(st, pk)
            u = {"pk": pk, "u": e.get("u", "?"), "n": e.get("n", ""), "v": e.get("v", False), "p": e.get("p", False),
                 "count": (st.counts.get(pk) or [None])[0], "keep": pk in st.keep}
            v.addWidget(self._user_row(u, st, batch, in_batch_tab=True))

    def _refresh_batch_info(self):
        lbl = getattr(self, "batch_info", None)
        cur, st, batch = self._ctx()
        if lbl is None or batch is None or self.tab != "lote":
            return
        pend = [p for p in batch["items"] if st.batch_status(batch, p) == "pendente" and p in st.following]
        try:
            lbl.setText(f"{len(batch['done'])} removidos, {len(pend)} na fila, {len(batch['errors'])} com erro. "
                        f"Fila restante: {estimate(len(pend), self.win.follows.limits())}.")
        except RuntimeError:
            self.batch_info = None

    @staticmethod
    def _known(st, pk: str) -> dict:
        for e in st.unfollowers:
            if e["pk"] == pk:
                return e
        return {}

    def _unfollowers_tab(self, v, st, batch):
        v.addWidget(_label("Lista permanente: cada checagem compara com a anterior e registra quem saiu. "
                           "Nada aqui é apagado pelo app.", "muted", True))
        items = sorted(st.unfollowers, key=lambda e: -e["lost_at"])
        if not items:
            v.addWidget(_label("Ninguém deixou de seguir desde a primeira checagem.", "muted"))
        for e in items[:MAX_ROWS]:
            follow_now = e["pk"] in st.following
            txt = f"Deixou de seguir em {_when(e['lost_at'])}"
            if e["times"] > 1:
                txt += f" ({e['times']}ª vez)"
            if e["back_at"]:
                txt += f"  ·  voltou em {_when(e['back_at'])}"
            if follow_now:
                txt += "  ·  você ainda segue"
            u = {"pk": e["pk"], "u": e["u"], "n": e["n"], "v": False, "p": False, "count": None,
                 "keep": e["pk"] in st.keep}
            v.addWidget(self._user_row(u, st, batch, subtitle=txt, selectable=follow_now and not e["back_at"]))

    def _audit_tab(self, v, st):
        recs = st.read_audit(500)
        if not recs:
            v.addWidget(_label("Sem eventos ainda.", "muted"))
        for r in recs:
            row, h = self._row_frame()
            fmt = AUDIT_TEXT.get(r.get("event"))
            h.addWidget(_label(_when(r.get("ts", 0)), "muted"))
            h.addWidget(_label(fmt(r) if fmt else r.get("event", ""), wrap=True), 1)
            v.addWidget(row)

    # ---------------------------------------------------------------- linhas
    def _row_frame(self):
        row = QFrame()
        row.setObjectName("row")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 6, 12, 6)
        h.setSpacing(10)
        return row, h

    def _user_row(self, u: dict, st, batch, *, subtitle: str = "", selectable: bool = True,
                  in_batch_tab: bool = False) -> QFrame:
        row, h = self._row_frame()
        pk = u["pk"]
        box = QCheckBox()
        box.setToolTip("Selecionar para o lote (a seleção vale entre as abas)")
        box.setChecked(pk in st.selected and self.tab != "mantidos")
        box.toggled.connect(lambda on, pk=pk: self._select([pk], on))
        h.addWidget(box)
        col = QVBoxLayout()
        col.setSpacing(1)
        badge = " ✓" if u.get("v") else ""
        col.addWidget(_label(f"@{u['u']}{badge}" + (f"  ·  {u['n']}" if u.get("n") else ""), "rowTitle"))
        sub = subtitle or (f"{_fmt_count(u['count'])} seguidores" + ("  ·  privado" if u.get("p") else ""))
        col.addWidget(_label(sub, "muted"))
        h.addLayout(col, 1)
        status = _label("", "muted")
        h.addWidget(status)
        h.addWidget(ui.icon_button("external", "Abrir perfil", lambda: self._open_profile(u["u"])))
        keep = _button("Tirar de manter" if u["keep"] else "Manter", lambda: self._keep([pk], not u["keep"]))
        keep.setToolTip("Whitelist: perfis mantidos nunca entram em lote.")
        h.addWidget(keep)
        out = None
        if in_batch_tab:
            out = _button("Tirar do lote", lambda: self._unqueue(pk))
            h.addWidget(out)
        r = {"row": row, "box": box, "status": status, "keep": keep, "out": out, "text": f"{u['u']} {u.get('n', '')}".lower(),
             "selectable": (self.tab == "mantidos" and not in_batch_tab)
                           or (selectable and pk in st.following and not u["keep"])}
        self.rows[pk] = r
        self._paint_row(r, st.batch_status(batch, pk))
        return row

    def _paint_row(self, r: dict, state: str):
        r["state"] = state
        r["status"].setText(STATUS_TEXT.get(state, ""))
        r["status"].setObjectName("error" if state == "erro" else "muted")
        r["status"].style().unpolish(r["status"])
        r["status"].style().polish(r["status"])
        locked = state in ("removido", "ativo") or (state == "pendente" and self.tab != "mantidos") or not r["selectable"]
        r["box"].setEnabled(not locked)
        if locked and r["box"].isChecked():
            r["box"].blockSignals(True)
            r["box"].setChecked(False)
            r["box"].blockSignals(False)
        r["keep"].setEnabled(state not in ("removido", "ativo"))
        if r["out"] is not None:
            r["out"].setVisible(state in ("pendente", "erro"))
        r["row"].setEnabled(state != "removido")

    # ---------------------------------------------------------------- barra de seleção
    def _build_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("panel")
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 10, 24, 10)
        h.setSpacing(8)
        self.sel_label = _label("", "rowTitle")
        h.addWidget(self.sel_label, 1)
        self.btn_all = _button("Selecionar visíveis", self._select_visible)
        self.btn_none = _button("Limpar seleção", self._clear_selection)
        self.btn_keep = _button("Manter", lambda: self._keep_selected(True), "", "check")
        self.btn_unkeep = _button("Tirar de manter", lambda: self._keep_selected(False))
        self.btn_go = _button("Deixar de seguir", self._queue_selected, "danger", "close", "danger")
        for b in (self.btn_all, self.btn_none, self.btn_keep, self.btn_unkeep, self.btn_go):
            h.addWidget(b)
        return bar

    def _update_bar(self):
        cur, st, batch = self._ctx()
        show = cur is not None and st is not None and st.has_snapshot and self.tab in (*LIST_TABS, "deixaram")
        self.bar.setVisible(show)
        if not show:
            return
        sel = [p for p in st.selected if p in st.following and p not in st.keep]
        lim = self.win.follows.limits()
        famous = sum(1 for p in sel if follows.is_famous((st.counts.get(p) or [None])[0], lim.famous_threshold))
        unknown = sum(1 for p in sel if p not in st.counts)
        extra = f" ({famous} famosos, {unknown} sem contagem)" if sel and (famous or unknown) else ""
        running = self.win.follows.running_unfollow(cur.account.id)
        self.sel_label.setText(f"{len(sel)} selecionado(s){extra}" if sel else "Nenhum selecionado")
        self.btn_none.setEnabled(bool(st.selected))
        self.btn_keep.setVisible(self.tab != "mantidos")
        self.btn_keep.setEnabled(bool(sel))
        self.btn_unkeep.setVisible(self.tab == "mantidos")
        kept_sel = [p for p, r in self.rows.items() if r["box"].isChecked()] if self.tab == "mantidos" else []
        self.btn_unkeep.setEnabled(bool(kept_sel))
        self.btn_go.setText(("Adicionar ao lote" if running else "Deixar de seguir") + (f" ({len(sel)})" if sel else ""))
        self.btn_go.setEnabled(bool(sel) and cur.logged_in)
        self.btn_all.setVisible(self.tab != "mantidos")

    # ---------------------------------------------------------------- filtro
    def _filter_changed(self, text: str):
        self.filter = text
        self._filter_timer.start()

    def _apply_filter(self):
        q = self.filter.strip().lstrip("@").lower()
        for r in self.rows.values():
            try:
                r["row"].setVisible(not q or q in r["text"])
            except RuntimeError:
                pass

    # ---------------------------------------------------------------- ações
    def _select(self, pks, on: bool):
        cur, st, _ = self._ctx()
        if st is None:
            return
        if self.tab == "mantidos":  # na aba Manter a caixa só serve para "Tirar de manter"
            self._update_bar()
            return
        st.set_selected(pks, on)
        self._update_bar()

    def _select_visible(self):
        cur, st, _ = self._ctx()
        pks = [pk for pk, r in self.rows.items()
               if r["row"].isVisible() and r["box"].isEnabled() and not r["box"].isChecked()]
        for pk in pks:
            b = self.rows[pk]["box"]
            b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(False)
        st.set_selected(pks, True)
        self._update_bar()

    def _clear_selection(self):
        cur, st, _ = self._ctx()
        st.set_selected(list(st.selected), False)
        for r in self.rows.values():
            r["box"].blockSignals(True)
            r["box"].setChecked(False)
            r["box"].blockSignals(False)
        self._update_bar()

    def _keep(self, pks, keep: bool):
        cur, st, _ = self._ctx()
        st.set_keep_many(pks, keep, time.time())
        self.render()

    def _keep_selected(self, keep: bool):
        cur, st, _ = self._ctx()
        if keep:
            pks = [p for p in st.selected if p in st.following]
        else:
            pks = [pk for pk, r in self.rows.items() if r["box"].isChecked()]
        if pks:
            n = st.set_keep_many(pks, keep, time.time())
            self.win.toast.show_message(f"{n} perfil(is) {'na lista de manter' if keep else 'liberados da lista de manter'}.")
        self.render()

    def _queue_selected(self):
        cur, st, _ = self._ctx()
        pks = [p for p in st.selected if p in st.following and p not in st.keep]
        if not pks:
            return
        lim = self.win.follows.limits()
        famous = [p for p in pks if follows.is_famous((st.counts.get(p) or [None])[0], lim.famous_threshold)]
        running = self.win.follows.running_unfollow(cur.account.id)
        names = ", ".join("@" + st.following[p]["u"] for p in famous[:8]) + ("..." if len(famous) > 8 else "")
        warn = f"\n\nInclui {len(famous)} famoso(s): {names}." if famous else ""
        if not self.win.confirm(
            "Adicionar ao lote?" if running else "Deixar de seguir?",
            f"Deixar de seguir {len(pks)} perfil(is) com a conta \"{cur.account.name}\".{warn}\n\n"
            f"Uma remoção por vez, {lim.unfollow_min_delay:g} a {lim.unfollow_max_delay:g} s entre elas, até "
            f"{lim.unfollow_per_hour} por hora e {min(lim.unfollow_daily, 200)} por dia: {estimate(len(pks), lim)}. "
            "Se o Instagram pedir calma, o app pausa e segue mais devagar. O lote fica salvo e dá para tirar "
            "perfis dele na aba Lote enquanto não foram removidos.\n\nNão dá para desfazer pelo app.",
            "Adicionar" if running else "Deixar de seguir", danger=True,
        ):
            return
        code, msg = self.win.follows.queue(cur, pks)
        st.set_selected(pks, False)
        self.win.toast.show_message(msg, 6000)
        self.render()

    def _unqueue(self, pk: str):
        cur = self.win.current
        if cur is not None and not self.win.follows.unqueue(cur.account.id, pk):
            self.win.toast.show_message("Esse perfil já está sendo removido ou saiu do lote.")
        self.render()

    def _can_act(self) -> bool:
        cur = self.win.current
        return not self.win.follows.busy and cur is not None and cur.logged_in

    def _set_tab(self, tab: str):
        self.tab = tab
        self.render()

    def _run(self, kind: str):
        code, msg = self.win.follows.start(kind, self.win.current)
        if code != 0:
            self.win.toast.show_message(msg, 7000)
        self.render()

    def _discard_batch(self):
        cur, st, b = self._ctx()
        if b and not self.win.confirm("Descartar lote?", "Os perfis que ainda estão na fila não serão removidos.",
                                      "Descartar", danger=True):
            return
        if b:
            st.audit("lote-pausa", reason="descartado", done=len(b["done"]))
        st.clear_batch()
        self.render()

    def _open_profile(self, username: str):
        if follows.USERNAME_RE.match(username):
            self.win.open_link(f"https://www.instagram.com/{username}/")

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.win.close_research()
        else:
            super().keyPressEvent(e)


def _job_label(job: dict) -> str:
    from .follows_qt import KIND_LABEL

    return KIND_LABEL.get(job["kind"], job["kind"])
