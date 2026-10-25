"""Tela de Seguidores: checagem, não seguidores, famosos, quem deixou de seguir, lote de remoções e auditoria."""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from . import follows, ui
from .research_ui import _button, _label

TABS = (("comuns", "Não seguidores"), ("famosos", "Não seguidores famosos"), ("sem-contagem", "Sem contagem"),
        ("deixaram", "Deixaram de seguir"), ("auditoria", "Auditoria"))
MAX_ROWS = 400
AUDIT_TEXT = {
    "checagem": lambda r: (f"Checagem: {r.get('followers', 0)} seguidores, {r.get('following', 0)} seguindo, "
                           f"{r.get('lost', 0)} deixaram de seguir, {r.get('gained', 0)} novos"
                           + (" (primeira)" if r.get("first") else "")),
    "checagem-parcial": lambda r: f"Checagem parcial, parou no teto ({r.get('reason', '')}); continua na próxima",
    "deixou-de-seguir": lambda r: f"@{r.get('user', '?')} deixou de seguir" + (" (você ainda segue)" if r.get("i_follow") else ""),
    "voltou-a-seguir": lambda r: f"@{r.get('user', '?')} voltou a seguir",
    "deixei-de-seguir": lambda r: f"Você deixou de seguir @{r.get('user', '?')}"
                                  + (f" ({_fmt_count(r['count'])} seguidores)" if isinstance(r.get("count"), int) else ""),
    "lote-inicio": lambda r: f"Lote criado ({_mode_label(r.get('mode'))}): {r.get('total', 0)} perfis",
    "lote-pausa": lambda r: f"Lote pausado ({r.get('reason', '')}), {r.get('done', 0)} removidos nesta sessão",
    "lote-fim": lambda r: f"Lote concluído: {r.get('done', 0)} removidos, {r.get('errors', 0)} com erro",
    "contagem": lambda r: f"Contagem de seguidores: {r.get('n', 0)} perfis",
    "bloqueio": lambda r: f"Bloqueio do Instagram ({r.get('kind', '')}); resfriamento até {r.get('cooldown_until', '?')}",
    "desacelerar": lambda r: (f"O Instagram pediu calma ({r.get('task', '')}): pausa de {r.get('minutes', '?')} min, "
                              f"ritmo {r.get('slow', '?')}x mais lento, seguiu sozinho"),
    "manter": lambda r: f"@{r.get('user', '?')} marcado para manter",
    "desmarcar-manter": lambda r: f"@{r.get('user', '?')} desmarcado de manter",
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
    return "não seguidores famosos" if mode == "famosos" else "não seguidores"


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
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("prefsViewport")
        outer.addWidget(self.scroll)
        self.tab = "comuns"
        self.filter = ""
        self.status = None
        win.follows.progress.connect(self._progress)
        win.follows.changed.connect(self._changed)

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

    def open(self):
        self.render()

    def render(self):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.status = None
        page = QWidget()
        page.setObjectName("prefsPage")
        h = QHBoxLayout(page)
        h.setContentsMargins(36, 22, 36, 36)
        col = QWidget()
        col.setMaximumWidth(900)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        h.addWidget(col, 1)

        top = QHBoxLayout()
        top.addWidget(_button("Voltar ao Instagram", self.win.close_research, "nav", "back"))
        top.addStretch()
        v.addLayout(top)
        v.addWidget(_label("Seguidores", "pageTitle"))
        v.addWidget(_label(
            "Compara quem você segue com quem segue de volta, separa os famosos e guarda para sempre quem deixou "
            "de seguir. Tudo no ritmo de uma pessoa, uma requisição por vez; ao primeiro sinal de limite do "
            "Instagram a tarefa para e a conta entra em resfriamento.",
            "muted", True,
        ))
        cur = self.win.current
        if cur is None:
            v.addWidget(_label("Abra uma conta primeiro.", "muted"))
            v.addStretch()
            self.scroll.setWidget(page)
            return
        ctl = self.win.follows
        acc = cur.account
        st = ctl.store(acc.id)
        summ = ctl.summary(acc.id)
        lim = ctl.limits()
        v.addWidget(self._head(cur, st, summ, lim))

        tabs = ui.Segmented([(k, t) for k, t in TABS], self.tab)
        tabs.changed.connect(self._set_tab)
        v.addWidget(tabs, 0, Qt.AlignmentFlag.AlignLeft)
        if self.tab in ("comuns", "famosos", "sem-contagem", "deixaram"):
            field = QLineEdit(self.filter)
            field.setPlaceholderText("Filtrar por @usuário ou nome")
            field.returnPressed.connect(lambda: self._set_filter(field.text()))
            field.editingFinished.connect(lambda: self._set_filter(field.text()))
            v.addWidget(field)

        if not st.has_snapshot and self.tab != "auditoria":
            v.addWidget(_label("Nenhuma checagem ainda. Clique em \"Checar agora\".", "muted"))
        elif self.tab in ("comuns", "famosos", "sem-contagem"):
            self._list_tab(v, st, lim)
        elif self.tab == "deixaram":
            self._unfollowers_tab(v, st)
        if self.tab == "auditoria":
            self._audit_tab(v, st)
        v.addStretch()
        self.scroll.setWidget(page)

    # ---------- blocos ----------
    def _head(self, cur, st, summ, lim) -> QFrame:
        ctl = self.win.follows
        panel = QFrame()
        panel.setObjectName("panel")
        f = QVBoxLayout(panel)
        f.setContentsMargins(20, 18, 20, 18)
        f.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(_label(f"Conta: {cur.account.name}", "rowTitle"), 1)
        busy = ctl.busy
        chk = _button("Checar agora", lambda: self._run("check"), "primaryWide", "refresh", "white")
        chk.setEnabled(not busy and cur.logged_in)
        row.addWidget(chk)
        f.addLayout(row)
        info = (f"Última checagem: {_when(st.taken_at)}  ·  {summ['followers']} seguidores  ·  {summ['following']} seguindo  ·  "
                f"{summ['non_followers']} não seguem de volta ({summ['famous']} famosos, {summ['unknown']} sem contagem, "
                f"{summ['keep']} mantidos)  ·  {summ['unfollowers']} na lista de quem deixou de seguir")
        f.addWidget(_label(info, "muted", True))
        u = summ["unfollow"]
        f.addWidget(_label(
            f"Famoso = {_fmt_count(lim.famous_threshold)} seguidores ou mais (Preferências > Seguidores). "
            f"Remoções hoje: {u['unfollows_today']} de {u['unfollow_cap']}; ritmo {lim.unfollow_min_delay:g} a "
            f"{lim.unfollow_max_delay:g} s, até {lim.unfollow_per_hour} por hora. "
            + ("Checagem diária automática ligada." if lim.follow_check else "Checagem diária automática desligada."),
            "muted", True,
        ))
        if summ["partial"] and not busy:
            f.addWidget(_label("Há uma checagem parcial salva; \"Checar agora\" continua de onde parou.", "muted", True))
        if not u["ok"] and u["reason"] == "cooldown":
            f.addWidget(_label(u["message"], "error", True))
        if not cur.logged_in:
            f.addWidget(_label("Entre na conta para usar esta tela.", "error", True))
        if summ["unknown"] and not busy:
            cnt = _button(f"Contar seguidores dos não seguidores ({summ['unknown']} sem contagem)",
                          lambda: self._run("counts"), "", "search")
            cnt.setEnabled(cur.logged_in)
            f.addWidget(cnt, 0, Qt.AlignmentFlag.AlignLeft)
        b = summ["batch"]
        if b and not busy:
            line = QHBoxLayout()
            line.addWidget(_label(f"Lote salvo ({_mode_label(b['mode'])}): {b['done']} de {b['total']} removidos"
                                  + (f", {b['errors']} com erro" if b["errors"] else ""), "muted", True), 1)
            line.addWidget(_button("Retomar lote", lambda: self._run("unfollow"), "primary", "play", "white"))
            line.addWidget(_button("Descartar", self._discard_batch, "danger", "close", "danger"))
            f.addLayout(line)
        if busy:
            run = QHBoxLayout()
            job = ctl.job
            run.addWidget(_label(f"Em andamento: {job['kind'] and _job_label(job)}", "rowTitle"), 1)
            run.addWidget(_button("Interromper", ctl.cancel, "danger", "close", "danger"))
            f.addLayout(run)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setRange(0, 0)
            f.addWidget(bar)
        self.status = _label(ctl.last_message or "", "muted", True)
        f.addWidget(self.status)
        return panel

    def _list_tab(self, v, st, lim):
        groups = st.split(lim.famous_threshold)
        items = groups[self.tab]
        mode = "famosos" if self.tab == "famosos" else "nao-seguidores"
        if self.tab == "comuns":
            v.addWidget(_label("Quem você segue e não segue de volta, abaixo do limite de famoso. Perfis sem contagem "
                               "de seguidores ficam na aba \"Sem contagem\" e não entram no lote.", "muted", True))
        elif self.tab == "famosos":
            v.addWidget(_label("Não seguidores com seguidores acima do limite de famoso (artistas, marcas, páginas).",
                               "muted", True))
        else:
            v.addWidget(_label("Não seguidores ainda sem contagem de seguidores. Use \"Contar seguidores\" para "
                               "classificar em comuns ou famosos.", "muted", True))
        if self.tab != "sem-contagem" and items:
            btn = _button(f"Deixar de seguir todos ({len(items)})", lambda: self._batch(mode, [u["pk"] for u in items]),
                          "danger", "close", "danger")
            btn.setEnabled(self._can_act())
            v.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        shown = self._filtered(items)
        if not shown:
            v.addWidget(_label("Ninguém aqui.", "muted"))
        for u in shown[:MAX_ROWS]:
            v.addWidget(self._user_row(u, mode))
        if len(shown) > MAX_ROWS:
            v.addWidget(_label(f"Mostrando {MAX_ROWS} de {len(shown)}; use o filtro.", "muted"))
        kept = [u for u in st.non_followers() if u["keep"]]
        if kept and self.tab == "comuns":
            v.addWidget(_label("MANTIDOS", "sectionTitle"))
            for u in self._filtered(kept)[:MAX_ROWS]:
                v.addWidget(self._user_row(u, mode))

    def _unfollowers_tab(self, v, st):
        v.addWidget(_label("Lista permanente: cada checagem compara com a anterior e registra quem saiu. "
                           "Nada aqui é apagado pelo app.", "muted", True))
        items = sorted(st.unfollowers, key=lambda e: -e["lost_at"])
        shown = self._filtered(items)
        if not shown:
            v.addWidget(_label("Ninguém deixou de seguir desde a primeira checagem.", "muted"))
        for e in shown[:MAX_ROWS]:
            row, h = self._row_frame()
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_label(f"@{e['u']}" + (f"  ·  {e['n']}" if e["n"] else ""), "rowTitle"))
            txt = f"Deixou de seguir em {_when(e['lost_at'])}"
            if e["times"] > 1:
                txt += f" ({e['times']}ª vez)"
            if e["back_at"]:
                txt += f"  ·  voltou em {_when(e['back_at'])}"
            follow_now = e["pk"] in st.following
            if follow_now:
                txt += "  ·  você ainda segue"
            col.addWidget(_label(txt, "muted"))
            h.addLayout(col, 1)
            h.addWidget(self._icon("external", "Abrir perfil", lambda u=e["u"]: self._open_profile(u)))
            if follow_now and not e["back_at"]:
                b = _button("Deixar de seguir", lambda pk=e["pk"]: self._batch("nao-seguidores", [pk]), "danger")
                b.setEnabled(self._can_act())
                h.addWidget(b)
            v.addWidget(row)

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

    # ---------- linhas ----------
    def _row_frame(self):
        row = QFrame()
        row.setObjectName("row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)
        return row, h

    def _user_row(self, u: dict, mode: str) -> QFrame:
        row, h = self._row_frame()
        col = QVBoxLayout()
        col.setSpacing(2)
        badge = " ✓" if u["v"] else ""
        col.addWidget(_label(f"@{u['u']}{badge}" + (f"  ·  {u['n']}" if u["n"] else ""), "rowTitle"))
        col.addWidget(_label(f"{_fmt_count(u['count'])} seguidores" + ("  ·  privado" if u["p"] else ""), "muted"))
        h.addLayout(col, 1)
        h.addWidget(self._icon("external", "Abrir perfil", lambda: self._open_profile(u["u"])))
        keep = _button("Desmarcar" if u["keep"] else "Manter", lambda: self._keep(u["pk"], not u["keep"]))
        keep.setToolTip("Perfis mantidos nunca entram em lote de remoção.")
        h.addWidget(keep)
        if not u["keep"]:
            b = _button("Deixar de seguir", lambda: self._batch(mode, [u["pk"]]), "danger")
            b.setEnabled(self._can_act())
            h.addWidget(b)
        return row

    def _can_act(self) -> bool:
        cur = self.win.current
        return not self.win.follows.busy and cur is not None and cur.logged_in

    def _icon(self, name, tip, fn):
        return ui.icon_button(name, tip, fn)

    def _filtered(self, items: list[dict]) -> list[dict]:
        q = self.filter.strip().lstrip("@").lower()
        if not q:
            return items
        return [u for u in items if q in u["u"].lower() or q in u.get("n", "").lower()]

    # ---------- ações ----------
    def _set_tab(self, tab: str):
        self.tab = tab
        self.render()

    def _set_filter(self, text: str):
        if text != self.filter:
            self.filter = text
            self.render()

    def _run(self, kind: str):
        code, msg = self.win.follows.start(kind, self.win.current)
        if code != 0:
            self.win.toast.show_message(msg, 7000)
        self.render()

    def _batch(self, mode: str, pks: list[str]):
        lim = self.win.follows.limits()
        n = len(pks)
        who = "perfil" if n == 1 else "perfis"
        if not self.win.confirm(
            "Deixar de seguir?",
            f"Deixar de seguir {n} {who} ({_mode_label(mode)}) com a conta \"{self.win.current.account.name}\".\n\n"
            f"Uma remoção por vez, {lim.unfollow_min_delay:g} a {lim.unfollow_max_delay:g} s entre elas, até "
            f"{lim.unfollow_per_hour} por hora e {min(lim.unfollow_daily, 200)} por dia: {estimate(n, lim)}. "
            "Para ao primeiro sinal de limite do Instagram. O lote fica salvo e pode ser retomado.\n\n"
            "Não dá para desfazer pelo app.",
            "Deixar de seguir", danger=True,
        ):
            return
        code, msg = self.win.follows.start("unfollow", self.win.current, mode=mode, pks=pks)
        if code != 0:
            self.win.toast.show_message(msg, 7000)
        self.render()

    def _keep(self, pk: str, keep: bool):
        st = self.win.follows.store(self.win.current.account.id)
        st.set_keep(pk, keep, time.time())
        self.render()

    def _discard_batch(self):
        st = self.win.follows.store(self.win.current.account.id)
        b = st.load_batch()
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
