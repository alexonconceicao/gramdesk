"""Tela de Seguidores: checagem (listas completas cruzadas), não seguidores, famosos, mútuos, fãs, seleção para
o lote, lista de manter, lote ao vivo (deixar de seguir e remover seguidor), quem deixou de seguir, histórico,
pontuação de conta fantasma, filtros, CSV, relatório, exportação oficial da Meta e auditoria."""

from __future__ import annotations

import math
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import follows, follows_report, ui
from .research_ui import _button, _label

LIST_TABS = ("comuns", "famosos", "mutuos", "fas", "mantidos")
SELECT_TABS = (*LIST_TABS, "deixaram")
MAX_ROWS = 3000
STATUS_TEXT = {"pendente": "Na fila", "ativo": "Agora...", "removido": "Feito", "erro": "Erro"}
GHOST_TEXT = {"bot": "provável bot", "inativo": "provável inativo", "suspeito": "suspeito"}
FILTERS = (("v", "Verificados"), ("p", "Privados"), ("a", "Sem foto"), ("g", "Suspeitos"))
AUDIT_TEXT = {
    "checagem": lambda r: (f"Checagem: {r.get('followers', 0)} seguidores, {r.get('following', 0)} seguindo, "
                           f"{r.get('lost', 0)} deixaram de seguir, {r.get('gained', 0)} novos"
                           + (" (primeira)" if r.get("first") else "")),
    "checagem-parcial": lambda r: f"Checagem parcial, parou no teto ({r.get('reason', '')}); continua na próxima",
    "deixou-de-seguir": lambda r: f"@{r.get('user', '?')} deixou de seguir" + (" (você ainda segue)" if r.get("i_follow") else ""),
    "voltou-a-seguir": lambda r: f"@{r.get('user', '?')} voltou a seguir",
    "deixei-de-seguir": lambda r: f"Você deixou de seguir @{r.get('user', '?')}"
                                  + (f" ({_fmt_count(r['count'])} seguidores)" if isinstance(r.get("count"), int) else ""),
    "removi-seguidor": lambda r: f"Você removeu o seguidor @{r.get('user', '?')} (sem bloquear)",
    "lote-inicio": lambda r: f"Lote ({_mode_label(r.get('mode'))}): {r.get('total', 0)} perfis entraram",
    "lote-pausa": lambda r: f"Lote pausado ({r.get('reason', '')}), {r.get('done', 0)} feitos nesta sessão",
    "lote-fim": lambda r: f"Lote concluído: {r.get('done', 0)} feitos, {r.get('errors', 0)} com erro",
    "contagem": lambda r: f"Contagem de seguidores: {r.get('n', 0)} perfis",
    "bloqueio": lambda r: f"Bloqueio do Instagram ({r.get('kind', '')}); resfriamento até {r.get('cooldown_until', '?')}",
    "desacelerar": lambda r: (f"O Instagram pediu calma ({r.get('task', '')}): pausa de {r.get('minutes', '?')} min, "
                              f"ritmo {r.get('slow', '?')}x mais lento, seguiu sozinho"),
    "manter": lambda r: f"@{r.get('user', '?')} entrou na lista de manter",
    "desmarcar-manter": lambda r: f"@{r.get('user', '?')} saiu da lista de manter",
    "acao-recusada": lambda r: (f"O Instagram recusou a ação em @{r.get('user', '?')} "
                                f"(HTTP {r.get('status', '?')}): {r.get('motivo', 'sem detalhe')}"),
    "tarefa-erro": lambda r: f"{r.get('kind', 'tarefa')} interrompida: {r.get('reason', '')}",
    "importacao-meta": lambda r: (f"Exportação da Meta importada: {r.get('followers', 0)} seguidores, "
                                  f"{r.get('following', 0)} seguindo, {r.get('pending', 0)} pedidos pendentes"),
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


def _clock(seconds: float) -> str:
    """Tempo que falta, em mm:ss (ou h:mm:ss)."""
    total = max(0, int(seconds + 0.5))
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


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


class HistoryChart(QWidget):
    """Seguidores e seguindo ao longo das checagens."""

    def __init__(self, history: list[list]):
        super().__init__()
        self.history = [h for h in history if h[0] > 0][-300:]
        self.setMinimumHeight(220)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, pad = self.width(), self.height(), 34
        p.setPen(QColor(ui.MUTED))
        if len(self.history) < 2:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "O gráfico aparece a partir da segunda checagem.")
            return
        t0, t1 = self.history[0][0], self.history[-1][0]
        vals = [v for x in self.history for v in (x[1], x[2])]
        lo, hi = min(vals), max(vals)
        hi = hi if hi > lo else lo + 1

        def pt(t, v):
            return QPointF(pad + (t - t0) / max(1.0, t1 - t0) * (w - 2 * pad - 90),
                           h - pad - (v - lo) / (hi - lo) * (h - 2 * pad))

        p.drawText(4, pad, str(hi))
        p.drawText(4, h - pad, str(lo))
        p.drawText(pad, h - 8, time.strftime("%d/%m", time.localtime(t0)))
        p.drawText(w - pad - 140, h - 8, time.strftime("%d/%m %H:%M", time.localtime(t1)))
        for idx, color, label in ((1, "#d62976", "seguidores"), (2, "#4f8cff", "seguindo")):
            p.setPen(QPen(QColor(color), 2.4))
            pts = [pt(x[0], x[idx]) for x in self.history]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
            p.drawText(pts[-1] + QPointF(8, 4), f"{self.history[-1][idx]} {label}")


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
        self.meta_view = "nao-seguidores"
        self.filter = ""
        self.flags: set[str] = set()
        self.status = self.info = self.batch_info = None
        self.rows: dict[str, dict] = {}
        self._rendered_tab = ""
        self._filter_timer = QTimer(self, singleShot=True, interval=150, timeout=self._apply_filter)
        self._clock = QTimer(self, interval=1000, timeout=self._refresh_clocks)
        self._clock.start()
        self.cool = None
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

    def _action(self) -> str:
        return "remove" if self.tab == "fas" else "unfollow"

    def _selected(self, st) -> set[str]:
        return st.selected_remove if self._action() == "remove" else st.selected

    def _tab_items(self, st) -> dict[str, list[dict]]:
        groups = self.win.follows.split(st)
        groups["mutuos"] = [u for u in st.mutuals() if not u["keep"]]
        groups["fas"] = [u for u in st.fans() if not u["keep"]]
        kept = {u["pk"]: u for u in st.non_followers() + st.mutuals() + st.fans() if u["keep"]}
        groups["mantidos"] = sorted(kept.values(), key=lambda x: x["u"].lower())
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
        self.status = self.info = self.batch_info = self.cool = None
        self.rows = {}
        self._rendered_tab = self.tab
        page = QWidget()
        page.setObjectName("prefsPage")
        h = QHBoxLayout(page)
        h.setContentsMargins(36, 22, 36, 24)
        col = QWidget()
        col.setMaximumWidth(960)
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
        v.addWidget(self._head(cur, st))
        groups = self._tab_items(st)
        pend = sum(1 for p in (batch or {}).get("items", []) if st.batch_status(batch, p) == "pendente")
        tabs = ui.Segmented([
            ("comuns", f"Não seguidores ({len(groups['comuns'])})"),
            ("famosos", f"Famosos ({len(groups['famosos'])})"),
            ("mutuos", f"Mútuos ({len(groups['mutuos'])})"),
            ("fas", f"Fãs ({len(groups['fas'])})"),
            ("mantidos", f"Manter ({len(groups['mantidos'])})"),
            ("lote", f"Lote ({pend})"),
            ("deixaram", f"Deixaram ({len(st.unfollowers)})"),
        ], self.tab)
        tabs.changed.connect(self._set_tab)
        v.addWidget(tabs, 0, Qt.AlignmentFlag.AlignLeft)
        tabs2 = ui.Segmented([("historico", "Histórico"), ("meta", "Exportação da Meta"), ("auditoria", "Auditoria")],
                             self.tab)
        tabs2.changed.connect(self._set_tab)
        v.addWidget(tabs2, 0, Qt.AlignmentFlag.AlignLeft)

        if self.tab in (*SELECT_TABS, "lote"):
            row = QHBoxLayout()
            self.field = QLineEdit(self.filter)
            self.field.setPlaceholderText("Filtrar por @usuário ou nome (na hora)")
            self.field.setClearButtonEnabled(True)
            self.field.textChanged.connect(self._filter_changed)
            row.addWidget(self.field, 1)
            if self.tab in LIST_TABS:
                for key, text in FILTERS:
                    chip = QCheckBox(text)
                    chip.setToolTip("Mostrar só estes")
                    chip.setChecked(key in self.flags)
                    chip.toggled.connect(lambda on, key=key: self._flag(key, on))
                    row.addWidget(chip)
            v.addLayout(row)

        if not st.has_snapshot and self.tab not in ("auditoria", "deixaram", "meta", "historico"):
            v.addWidget(_label("Nenhuma checagem ainda. Clique em \"Checar agora\": o app baixa a lista completa de "
                               "seguidores e de quem você segue e cruza as duas.", "muted", True))
        elif self.tab in LIST_TABS:
            self._list_tab(v, groups[self.tab], batch, st)
        elif self.tab == "lote":
            self._batch_tab(v, st, batch, lim)
        elif self.tab == "deixaram":
            self._unfollowers_tab(v, st, batch)
        elif self.tab == "historico":
            self._history_tab(v, st)
        elif self.tab == "meta":
            self._meta_tab(v, st)
        else:
            self._audit_tab(v, st)
        v.addStretch()
        self.scroll.setWidget(page)
        self._apply_filter()
        self._update_bar()
        if keep_scroll:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(keep_scroll))

    def _head(self, cur, st) -> QFrame:
        ctl = self.win.follows
        panel = QFrame()
        panel.setObjectName("panel")
        f = QVBoxLayout(panel)
        f.setContentsMargins(20, 16, 20, 16)
        f.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(_label(f"Conta: {cur.account.name}", "rowTitle"), 1)
        busy = ctl.busy
        summ = ctl.summary(cur.account.id)
        if st.has_snapshot:
            row.addWidget(_button("CSV", self._export_csv, "", "download"))
            row.addWidget(_button("Relatório", self._report, "", "info"))
        if not busy and st.has_snapshot and summ["unknown"]:
            cnt = _button("Refinar famosos", lambda: self._run("counts"), "", "search")
            cnt.setToolTip(f"Opcional: busca o número de seguidores de {summ['unknown']} não seguidores sem selo, "
                           "um por um e devagar. Sem isso, famoso = conta verificada.")
            cnt.setEnabled(cur.logged_in)
            row.addWidget(cnt)
        chk = _button("Checar agora", lambda: self._run("check"), "primaryWide", "refresh", "white")
        chk.setToolTip("Baixa a lista completa de seguidores e de quem você segue e cruza as duas.")
        chk.setEnabled(not busy and cur.logged_in)
        row.addWidget(chk)
        f.addLayout(row)
        self.info = _label("", "muted", True)
        f.addWidget(self.info)
        self._refresh_info()
        if not cur.logged_in:
            f.addWidget(_label("Entre na conta para usar esta tela.", "error", True))
        self.cool = _label("", "error", True)
        f.addWidget(self.cool)
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

    def _refresh_clocks(self):
        """Contagem regressiva ao vivo: espera entre ações e resfriamento da conta."""
        if not self.isVisible() or self.win.current is None:
            return
        self._refresh_info()
        self._refresh_batch_info()
        if self.cool is None:
            return
        u = self.win.follows.summary(self.win.current.account.id)["unfollow"]
        try:
            if u["ok"] or u["reason"] != "cooldown":
                self.cool.setText("")
                self.cool.setVisible(False)
                return
            left = u["until"] - time.time()
            self.cool.setVisible(True)
            self.cool.setText(f"{u['message'].split('.')[0]}. Liberado em {_clock(left)}.")
        except RuntimeError:
            self.cool = None

    def _refresh_info(self):
        cur, st, batch = self._ctx()
        if cur is None or self.info is None:
            return
        s = self.win.follows.summary(cur.account.id)
        lim = self.win.follows.limits()
        fam = "verificado" + (f" ou {_fmt_count(lim.famous_threshold)}+ seguidores" if s["unknown"] < s["non_followers"] else "")
        txt = (f"Última checagem: {_when(st.taken_at)}  ·  {s['followers']} seguidores  ·  {s['following']} seguindo  ·  "
               f"{s['non_followers']} não seguem de volta  ·  {s['mutuals']} mútuos  ·  {s['fans']} fãs  ·  "
               f"famoso = {fam}  ·  ações hoje {s['unfollow']['unfollows_today']} de {s['unfollow']['unfollow_cap']}")
        b = s["batch"]
        if b:
            txt += f"  ·  lote: {b['done']} de {b['total']} feitos" + (f", {b['errors']} com erro" if b["errors"] else "")
        ctl = self.win.follows
        if ctl.waiting_until:
            txt += f"  ·  {ctl.waiting_label} em {_clock(ctl.waiting_until - time.time())}"
        try:
            self.info.setText(txt)
        except RuntimeError:
            self.info = None

    # ---------------------------------------------------------------- abas
    def _list_tab(self, v, items: list[dict], batch, st):
        hint = {
            "comuns": "Você segue e não seguem de volta. Marque quem sai e use a barra de baixo; a seleção vale entre as abas.",
            "famosos": "Não seguem de volta e são famosos (verificados, ou com seguidores acima do limite se você refinou). "
                       "Marque os que quer deixar; os demais podem ir para \"Manter\".",
            "mutuos": "Vocês se seguem. Dá para deixar de seguir daqui também.",
            "fas": "Seguem você e você não segue de volta. Aqui o lote REMOVE o seguidor (sem bloquear). A pontuação "
                   "de fantasma ajuda a achar bots: use o filtro \"Suspeitos\".",
            "mantidos": "Lista de manter: nunca entram em lote. Marque e use \"Tirar de manter\" para liberar.",
        }[self.tab]
        v.addWidget(_label(hint, "muted", True))
        if self.tab in ("comuns", "famosos", "mutuos", "fas"):
            v.addWidget(_label("No lote, o app navega até cada perfil nesta janela e clica no botão do site. "
                               "Evite navegar à mão enquanto ele roda.", "muted", True))
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
            v.addWidget(_label("Nenhum lote. Marque perfis nas outras abas e use a barra de baixo.", "muted", True))
            return
        running = ctl.running_unfollow(acc)
        line = QHBoxLayout()
        self.batch_info = _label("", "muted", True)
        line.addWidget(self.batch_info, 1)
        self._refresh_batch_info()
        pend = [p for p in batch["items"] if st.batch_status(batch, p) == "pendente"]
        if not running and pend:
            b = _button("Retomar", lambda: self._run("unfollow"), "primary", "play", "white")
            b.setEnabled(self._can_act())
            line.addWidget(b)
        if batch["errors"]:
            again = _button(f"Tentar de novo ({len(batch['errors'])})", self._retry_errors, "", "refresh")
            again.setToolTip("Devolve à fila os perfis que deram erro.")
            again.setEnabled(self._can_act() or running)
            line.addWidget(again)
        if not running:
            line.addWidget(_button("Descartar lote", self._discard_batch, "danger", "trash", "danger"))
        v.addLayout(line)
        remove = set(batch.get("remove", []))
        order = sorted(batch["items"], key=lambda p: {"pendente": 0, "erro": 1, "removido": 2}.get(st.batch_status(batch, p), 3))
        for pk in order[:MAX_ROWS]:
            e = st.following.get(pk) or st.followers.get(pk) or self._known(st, pk)
            u = st._row(pk, {"u": e.get("u", "?"), "n": e.get("n", ""), "v": e.get("v", False), "p": e.get("p", False),
                             "a": e.get("a", False)})
            sub = ("remover seguidor" if pk in remove else "deixar de seguir") + f"  ·  {_fmt_count(u['count'])} seguidores"
            v.addWidget(self._user_row(u, st, batch, in_batch_tab=True, subtitle=sub))

    def _refresh_batch_info(self):
        lbl = self.batch_info
        cur, st, batch = self._ctx()
        if lbl is None or batch is None or self.tab != "lote":
            return
        pend = [p for p in batch["items"] if st.batch_status(batch, p) == "pendente"]
        try:
            lbl.setText(f"{len(batch['done'])} feitos, {len(pend)} na fila, {len(batch['errors'])} com erro. "
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
                           "Nada aqui é apagado pelo app. Quem você ainda segue pode ser marcado para deixar de seguir.",
                           "muted", True))
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
            u = st._row(e["pk"], {"u": e["u"], "n": e["n"], "v": False, "p": False, "a": False})
            v.addWidget(self._user_row(u, st, batch, subtitle=txt, selectable=follow_now and not e["back_at"]))

    def _history_tab(self, v, st):
        v.addWidget(_label("Seguidores e seguindo em cada checagem (a checagem diária alimenta o gráfico).", "muted", True))
        v.addWidget(HistoryChart(st.history))
        prev = None
        rows = []
        for ts, fol, fing in st.history:
            d = "" if prev is None else f"  ({fol - prev[0]:+d} seguidores, {fing - prev[1]:+d} seguindo)"
            rows.append(f"{_when(ts)}: {fol} seguidores, {fing} seguindo{d}")
            prev = (fol, fing)
        for text in reversed(rows[-200:]):
            row, h = self._row_frame()
            h.addWidget(_label(text), 1)
            v.addWidget(row)

    def _meta_tab(self, v, st):
        v.addWidget(_label(
            "Use a exportação oficial do Instagram (Central de Contas > Suas informações e permissões > Baixar suas "
            "informações > Seguidores e seguindo, formato HTML ou JSON). Escolha a pasta ou o .zip: o app lê "
            "followers_1, following e pedidos pendentes e cruza tudo na hora, sem acessar o Instagram.",
            "muted", True))
        line = QHBoxLayout()
        line.addWidget(_button("Importar .zip", lambda: self._import_meta(False), "primary", "folder", "white"))
        line.addWidget(_button("Importar pasta", lambda: self._import_meta(True), "", "folder"))
        line.addStretch()
        v.addLayout(line)
        meta = st.load_meta()
        if not meta:
            return
        an = follows.meta_analysis(meta)
        prev = st.load_meta(prev=True)
        v.addWidget(_label(f"Exportação importada em {_when(meta['at'])}: {len(meta['followers'])} seguidores, "
                           f"{len(meta['following'])} seguindo, {len(an['nao-seguidores'])} não seguem de volta, "
                           f"{len(an['mutuos'])} mútuos, {len(an['fas'])} fãs, {len(an['pendentes'])} pedidos pendentes.",
                           "rowTitle", True))
        views = [("nao-seguidores", "Não seguidores"), ("mutuos", "Mútuos"), ("fas", "Fãs"), ("pendentes", "Pedidos pendentes"),
                 ("deixei-de-seguir", "Você deixou de seguir")]
        if prev:
            views.append(("diferenca", "Diferença"))
        seg = ui.Segmented(views, self.meta_view)
        seg.changed.connect(lambda k: (setattr(self, "meta_view", k), self.render()))
        v.addWidget(seg, 0, Qt.AlignmentFlag.AlignLeft)
        if self.meta_view == "diferenca" and prev:
            d = follows.meta_diff(prev, meta)
            v.addWidget(_label(f"Comparando com a exportação de {_when(prev['at'])}.", "muted"))
            for key, title in (("sairam", "Deixaram de seguir"), ("entraram", "Novos seguidores"),
                               ("comecei", "Você passou a seguir"), ("parei", "Você deixou de seguir"),
                               ("novos-mutuos", "Novos mútuos")):
                v.addWidget(_label(f"{title.upper()} ({len(d[key])})", "sectionTitle"))
                for name in d[key][:500]:
                    v.addWidget(self._name_row(name))
            return
        names = an.get(self.meta_view, [])
        if self.meta_view == "nao-seguidores" and names:
            live = st.pks_by_username(names)
            b = _button(f"Marcar no lote os {len(live)} que ainda constam na última checagem",
                        lambda: self._select_from_meta(live), "", "check")
            b.setEnabled(bool(live))
            v.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        if self.meta_view == "pendentes" and names:
            v.addWidget(_label("Pedidos que você mandou e ninguém aceitou. Abra o perfil e toque em \"Solicitado\" "
                               "para cancelar.", "muted", True))
        for name in names[:MAX_ROWS]:
            v.addWidget(self._name_row(name))

    def _name_row(self, name: str) -> QFrame:
        row, h = self._row_frame()
        h.addWidget(_label(f"@{name}", "rowTitle"), 1)
        h.addWidget(ui.icon_button("external", "Abrir perfil", lambda: self._open_profile(name)))
        self.rows["meta:" + name] = {"row": row, "box": QCheckBox(), "status": QWidget(), "keep": QWidget(), "out": None,
                                     "text": name.lower(), "flags": {}, "selectable": False}
        return row

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
        action = self._action()
        pool = st.followers if action == "remove" else st.following
        box = QCheckBox()
        box.setToolTip("Remover este seguidor" if action == "remove" else "Selecionar para deixar de seguir")
        box.setChecked(pk in self._selected(st) and self.tab != "mantidos")
        box.toggled.connect(lambda on, pk=pk: self._select([pk], on))
        h.addWidget(box)
        col = QVBoxLayout()
        col.setSpacing(1)
        badge = " ✓" if u.get("v") else ""
        col.addWidget(_label(f"@{u['u']}{badge}" + (f"  ·  {u['n']}" if u.get("n") else ""), "rowTitle"))
        extra = []
        if u.get("p"):
            extra.append("privado")
        if u.get("a"):
            extra.append("sem foto")
        if u.get("ghost_level"):
            extra.append(f"{GHOST_TEXT[u['ghost_level']]} ({u['ghost']}): {', '.join(u['ghost_why'][:3])}")
        first = (f"{_fmt_count(u['count'])} seguidores" if u.get("count") is not None
                 else ("verificado" if u.get("v") else ""))
        sub = subtitle or "  ·  ".join([x for x in [first] + extra if x]) or " "
        sub_lbl = _label(sub, "muted")
        col.addWidget(sub_lbl)
        h.addLayout(col, 1)
        status = _label("", "muted")
        h.addWidget(status)
        h.addWidget(ui.icon_button("external", "Abrir perfil", lambda: self._open_profile(u["u"])))
        keep = _button("Tirar de manter" if u["keep"] else "Manter", lambda: self._keep([pk], not u["keep"]))
        keep.setToolTip("Lista de manter: nunca entram em lote.")
        h.addWidget(keep)
        out = None
        if in_batch_tab:
            out = _button("Tirar do lote", lambda: self._unqueue(pk))
            h.addWidget(out)
        r = {"row": row, "box": box, "status": status, "keep": keep, "out": out,
             "text": f"{u['u']} {u.get('n', '')}".lower(),
             "flags": {"v": bool(u.get("v")), "p": bool(u.get("p")), "a": bool(u.get("a")), "g": bool(u.get("ghost_level"))},
             "selectable": (self.tab == "mantidos" and not in_batch_tab) or (selectable and pk in pool and not u["keep"])}
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
        show = cur is not None and st is not None and st.has_snapshot and self.tab in SELECT_TABS
        self.bar.setVisible(show)
        if not show:
            return
        action = self._action()
        pool = st.followers if action == "remove" else st.following
        sel = [p for p in self._selected(st) if p in pool and p not in st.keep]
        lim = self.win.follows.limits()
        extra = ""
        if action == "unfollow":
            fam = sum(1 for p in sel if follows.famous(st._row(p, st.following[p]), lim.famous_threshold,
                                                       bool(lim.famous_verified)))
            extra = f" ({fam} famosos)" if fam else ""
        running = self.win.follows.running_unfollow(cur.account.id)
        noun = "seguidor(es) para remover" if action == "remove" else "para deixar de seguir"
        self.sel_label.setText(f"{len(sel)} {noun}{extra}" if sel else "Nenhum selecionado")
        self.btn_none.setEnabled(bool(self._selected(st)))
        self.btn_keep.setVisible(self.tab != "mantidos")
        self.btn_keep.setEnabled(bool(sel))
        self.btn_unkeep.setVisible(self.tab == "mantidos")
        kept_sel = [p for p, r in self.rows.items() if r["box"].isChecked()] if self.tab == "mantidos" else []
        self.btn_unkeep.setEnabled(bool(kept_sel))
        verb = "Remover seguidores" if action == "remove" else "Deixar de seguir"
        self.btn_go.setText(("Adicionar ao lote" if running else verb) + (f" ({len(sel)})" if sel else ""))
        self.btn_go.setVisible(self.tab != "mantidos")
        self.btn_go.setEnabled(bool(sel) and cur.logged_in)
        self.btn_all.setVisible(self.tab != "mantidos")

    # ---------------------------------------------------------------- filtro
    def _filter_changed(self, text: str):
        self.filter = text
        self._filter_timer.start()

    def _flag(self, key: str, on: bool):
        (self.flags.add if on else self.flags.discard)(key)
        self._apply_filter()

    def _apply_filter(self):
        q = self.filter.strip().lstrip("@").lower()
        flags = self.flags if self.tab in LIST_TABS else set()
        for r in self.rows.values():
            try:
                ok = (not q or q in r["text"]) and all(r["flags"].get(f) for f in flags)
                r["row"].setVisible(ok)
            except RuntimeError:
                pass

    # ---------------------------------------------------------------- ações
    def _select(self, pks, on: bool):
        cur, st, _ = self._ctx()
        if st is None:
            return
        if self.tab != "mantidos":  # na aba Manter a caixa só serve para "Tirar de manter"
            st.set_selected(pks, on, self._action())
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
        st.set_selected(pks, True, self._action())
        self._update_bar()

    def _clear_selection(self):
        cur, st, _ = self._ctx()
        st.set_selected(list(self._selected(st)), False, self._action())
        for r in self.rows.values():
            r["box"].blockSignals(True)
            r["box"].setChecked(False)
            r["box"].blockSignals(False)
        self._update_bar()

    def _select_from_meta(self, pks: list[str]):
        cur, st, _ = self._ctx()
        st.set_selected(pks, True, "unfollow")
        self.win.toast.show_message(f"{len(pks)} marcados. Confira na aba Não seguidores e use a barra de baixo.")
        self.tab = "comuns"
        self.render()

    def _keep(self, pks, keep: bool):
        cur, st, _ = self._ctx()
        st.set_keep_many(pks, keep, time.time())
        for pk in pks:
            st.selected_remove.discard(pk)
        self.render()

    def _keep_selected(self, keep: bool):
        cur, st, _ = self._ctx()
        if keep:
            pool = st.followers if self._action() == "remove" else st.following
            pks = [p for p in self._selected(st) if p in pool]
        else:
            pks = [pk for pk, r in self.rows.items() if r["box"].isChecked()]
        if pks:
            n = st.set_keep_many(pks, keep, time.time())
            for pk in pks:
                st.selected_remove.discard(pk)
            self.win.toast.show_message(f"{n} perfil(is) {'na lista de manter' if keep else 'liberados da lista de manter'}.")
        self.render()

    def _queue_selected(self):
        cur, st, _ = self._ctx()
        action = self._action()
        pool = st.followers if action == "remove" else st.following
        pks = [p for p in self._selected(st) if p in pool and p not in st.keep]
        if not pks:
            return
        lim = self.win.follows.limits()
        running = self.win.follows.running_unfollow(cur.account.id)
        warn = ""
        if action == "unfollow":
            famous = [p for p in pks if follows.famous(st._row(p, st.following[p]), lim.famous_threshold,
                                                       bool(lim.famous_verified))]
            names = ", ".join("@" + st.following[p]["u"] for p in famous[:8]) + ("..." if len(famous) > 8 else "")
            warn = f"\n\nInclui {len(famous)} famoso(s): {names}." if famous else ""
            what = f"Deixar de seguir {len(pks)} perfil(is)"
        else:
            what = f"Remover {len(pks)} seguidor(es) (eles deixam de seguir você, sem bloqueio nem aviso)"
        if not self.win.confirm(
            "Adicionar ao lote?" if running else "Confirmar lote?",
            f"{what} com a conta \"{cur.account.name}\".{warn}\n\n"
            "O app abre cada perfil na janela e clica no botão do próprio site, uma ação por vez, "
            f"{lim.unfollow_min_delay:g} a {lim.unfollow_max_delay:g} s entre elas, até "
            f"{lim.unfollow_per_hour} por hora e {min(lim.unfollow_daily, 200)} por dia: {estimate(len(pks), lim)}. "
            "Se o Instagram pedir calma, o app pausa e segue mais devagar. Dá para tirar perfis do lote na aba Lote "
            "enquanto não foram feitos.\n\nNão dá para desfazer pelo app.",
            "Adicionar" if running else "Confirmar", danger=True,
        ):
            return
        code, msg = self.win.follows.queue(cur, pks, action)
        st.set_selected(pks, False, action)
        self.win.toast.show_message(msg, 6000)
        self.render()

    def _retry_errors(self):
        cur, _st, _b = self._ctx()
        code, msg = self.win.follows.retry_errors(cur)
        self.win.toast.show_message(msg, 6000)
        self.render()

    def _unqueue(self, pk: str):
        cur = self.win.current
        if cur is not None and not self.win.follows.unqueue(cur.account.id, pk):
            self.win.toast.show_message("Esse perfil já está sendo processado ou saiu do lote.")
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
        if b and not self.win.confirm("Descartar lote?", "Os perfis que ainda estão na fila não serão processados.",
                                      "Descartar", danger=True):
            return
        if b:
            st.audit("lote-pausa", reason="descartado", done=len(b["done"]))
        st.clear_batch()
        self.render()

    # ---------------------------------------------------------------- arquivos
    def _out_dir(self) -> Path:
        d = self.win.download_root / "Seguidores"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _visible_items(self, st) -> list[dict]:
        if self.tab == "deixaram":
            return sorted(st.unfollowers, key=lambda e: -e["lost_at"])
        items = self._tab_items(st).get(self.tab if self.tab in LIST_TABS else "comuns", [])
        return [u for u in items if (r := self.rows.get(u["pk"])) is None or r["row"].isVisible()]

    def _export_csv(self):
        cur, st, batch = self._ctx()
        items = self._visible_items(st)
        states = {u["pk"]: st.batch_status(batch, u["pk"]) for u in items} if batch else {}
        tab = self.tab if self.tab in SELECT_TABS else "comuns"
        path = self._out_dir() / f"{cur.account.name}-{tab}-{time.strftime('%Y%m%d-%H%M')}.csv"
        path.write_text(follows_report.to_csv(items, states), encoding="utf-8")
        self.win.toast.show_message(f"CSV salvo: {path.name} ({len(items)} linhas)", 6000, "Abrir pasta",
                                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))))

    def _report(self):
        cur, st, _ = self._ctx()
        groups = self._tab_items(st)
        ghosts: dict[str, int] = {}
        for u in st.fans() + st.mutuals():
            if u["ghost_level"]:
                ghosts[GHOST_TEXT[u["ghost_level"]]] = ghosts.get(GHOST_TEXT[u["ghost_level"]], 0) + 1
        html = follows_report.report_html(cur.account.name, self.win.follows.summary(cur.account.id), st.history,
                                          groups, st.unfollowers, ghosts)
        path = self._out_dir() / f"relatorio-{cur.account.name}-{time.strftime('%Y%m%d-%H%M')}.html"
        path.write_text(html, encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.win.toast.show_message(f"Relatório salvo em Downloads/Gramdesk/Seguidores ({path.name}).", 6000)

    def _import_meta(self, folder: bool):
        cur, st, _ = self._ctx()
        if folder:
            path = QFileDialog.getExistingDirectory(self, "Pasta da exportação do Instagram")
        else:
            path, _f = QFileDialog.getOpenFileName(self, "Exportação do Instagram (.zip)", "", "ZIP (*.zip)")
        if not path:
            return
        try:
            lists = follows.read_meta_export(Path(path))
        except (ValueError, OSError) as exc:
            self.win.toast.show_message(f"Não deu para ler a exportação: {exc}", 8000)
            return
        st.save_meta(lists, time.time())
        self.meta_view = "nao-seguidores"
        self.win.toast.show_message(f"Importado: {len(lists['followers'])} seguidores e {len(lists['following'])} seguindo.")
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
