"""Tarefas de seguidores na página oculta da conta: checagem (foto das listas), contagem de seguidores
dos não seguidores (para separar famosos) e lote de "deixar de seguir".

Uma tarefa por vez, nunca junto com pesquisa ou navegação. Cada requisição passa pelo rate limiting
(Pacer para leitura; unfollow_check/record_unfollow para remoções) e o primeiro sinal de bloqueio
encerra a tarefa sem novas tentativas e ativa o resfriamento da conta.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import config, follows, ratelimit, security

OK, ERROR, APP, COOLDOWN, QUOTA, BUSY, REFUSED = 0, 1, 3, 4, 5, 6, 7
COUNTS_PER_RUN = 60  # contagens de seguidores por execução (cada uma é 1 requisição)
COUNTS_SLOW = 3.0  # leitura de perfil é a mais vigiada: começa 3x mais lenta que a leitura de listas
MAX_CONSECUTIVE_ERRORS = 3
AUTO_CHECK_HOURS = 20.0
PAGE_TRIES = 3  # tentativas de abrir a página oculta antes de desistir
PARTIAL_MAX_AGE = 3 * 86400
ACTION_BLOCK_RE = re.compile(r"feedback_required|restringimos|we restrict|action blocked|bloquead", re.I)
KIND_LABEL = {"check": "Checagem de seguidores", "counts": "Contagem de seguidores", "unfollow": "Lote (deixar de seguir / remover seguidor)"}


class FollowsController(QObject):
    progress = pyqtSignal(str)
    changed = pyqtSignal()
    item = pyqtSignal(str, str)  # pk, estado no lote: ativo | removido | erro | pendente | fora

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.job: dict | None = None
        self.last_message = ""
        self._stores: dict[str, follows.FollowStore] = {}
        self._queued_start: str = ""  # conta com lote esperando a tarefa atual terminar
        self._timer = QTimer(self, singleShot=True, timeout=self._step)
        self._daily = QTimer(self, interval=30 * 60 * 1000, timeout=self._daily_tick)
        self._daily.start()
        QTimer.singleShot(120_000, self._daily_tick)

    # ---------------------------------------------------------------- consultas
    def store(self, acc_id: str) -> follows.FollowStore:
        """Mesma instância para a tela e para a tarefa em andamento (estado sempre coerente)."""
        st = self._stores.get(acc_id)
        if st is None:
            st = self._stores[acc_id] = follows.FollowStore(config.DATA_DIR / "follows", acc_id)
        return st

    def batch(self, acc_id: str) -> dict | None:
        job = self.job
        if job is not None and job["kind"] == "unfollow" and job["acc"] == acc_id:
            return job["batch"]
        return self.store(acc_id).load_batch()

    def running_unfollow(self, acc_id: str) -> bool:
        return self.job is not None and self.job["kind"] == "unfollow" and self.job["acc"] == acc_id

    def split(self, st) -> dict[str, list[dict]]:
        lim = self.limits()
        return st.split(lim.famous_threshold, bool(lim.famous_verified))

    def queue(self, s, pks: list[str], action: str = "unfollow") -> tuple[int, str]:
        """Põe perfis no lote (deixar de seguir ou remover seguidor): entra no lote em andamento, no salvo ou
        cria um novo, e inicia se der."""
        acc = s.account.id
        st = self.store(acc)
        if self.running_unfollow(acc):
            added = st.extend_batch(self.job["batch"], pks, action)
            for pk in pks:
                self.item.emit(pk, st.batch_status(self.job["batch"], pk) or "fora")
            return OK, f"{added} adicionado(s) ao lote em andamento."
        batch = st.load_batch()
        if batch is None:
            batch = st.new_batch("selecao", [], self.limits().famous_threshold, time.time())
        added = st.extend_batch(batch, pks, action)
        if not st.next_in_batch(batch):
            return ERROR, "Nada para fazer (mantidos e quem já saiu ficam fora)."
        verb = "Removendo seguidores" if action == "remove" else "Deixando de seguir"
        if self.busy:
            self._queued_start = acc
            self.changed.emit()
            return OK, f"{added} no lote. Começa quando a tarefa atual terminar."
        code, msg = self.start("unfollow", s)
        return (OK, f"{added} no lote. {verb} no ritmo seguro...") if code == OK else (code, msg)

    def unqueue(self, acc_id: str, pk: str) -> bool:
        b = self.batch(acc_id)
        if b is None or (self.running_unfollow(acc_id) and self.job.get("pk") == pk):
            return False
        ok = self.store(acc_id).remove_from_batch(b, pk)
        if ok:
            self.item.emit(pk, "fora")
        return ok

    @property
    def busy(self) -> bool:
        return self.job is not None

    def limits(self) -> ratelimit.Limits:
        return self.win.research_limits()

    def precheck(self, s, kind: str) -> tuple[int, str]:
        win = self.win
        if win.outer.currentIndex() != 0:
            return APP, "O Gramdesk está bloqueado."
        if self.busy:
            return BUSY, "Já existe uma tarefa de seguidores em andamento."
        job = win.research_job
        if job is not None and job.state not in ("done", "error", "cancelled", "refused"):
            return BUSY, "Há uma pesquisa de perfil em andamento; espere ela terminar."
        if win.browse.busy:
            return BUSY, "Há uma ação de navegação em andamento; espere ela terminar."
        if s is None or s.profile is None or not s.logged_in:
            return APP, "Abra o Gramdesk e entre na conta antes."
        lim = self.limits()
        if kind == "unfollow":
            chk = win.rate.unfollow_check(s.account.id, lim)
        else:
            chk = win.rate.check(s.account.id, lim)
            if not chk["ok"] and chk["reason"] == "researches":
                chk = {"ok": True}  # a cota de pesquisas não vale para seguidores; itens e resfriamento sim
        if not chk["ok"]:
            return (COOLDOWN if chk["reason"] == "cooldown" else QUOTA), chk["message"]
        return OK, ""

    def summary(self, acc_id: str) -> dict:
        st = self.store(acc_id)
        lim = self.limits()
        groups = self.split(st)
        batch = self.batch(acc_id)
        return {
            "taken_at": st.taken_at, "checks": st.checks, "followers": len(st.followers), "following": len(st.following),
            "non_followers": sum(len(g) for g in groups.values()), "famous": len(groups["famosos"]),
            "common": len(groups["comuns"]), "keep": len(st.keep),
            "unknown": len(st.need_counts(time.time(), verified=bool(lim.famous_verified))),
            "mutuals": sum(1 for pk in st.following if pk in st.followers),
            "fans": sum(1 for pk in st.followers if pk not in st.following),
            "unfollowers": len(st.unfollowers), "partial": st.partial is not None,
            "batch": {"mode": batch["mode"], "total": len(batch["items"]), "done": len(batch["done"]),
                      "errors": len(batch["errors"])} if batch else None,
            "unfollow": self.win.rate.unfollow_status(acc_id, lim), "threshold": lim.famous_threshold,
        }

    # ---------------------------------------------------------------- início
    def start(self, kind: str, s, *, mode: str = "", pks: list[str] | None = None, auto: bool = False,
              on_done: Callable[[int, str], None] | None = None) -> tuple[int, str]:
        """Inicia uma tarefa. Devolve (código, mensagem); código OK = começou."""
        if kind not in KIND_LABEL:
            return ERROR, "Tarefa inválida."
        code, msg = self.precheck(s, kind)
        if code != OK:
            return code, msg
        acc = s.account.id
        st = self.store(acc)
        now = time.time()
        lim = self.limits()
        job = {"kind": kind, "s": s, "acc": acc, "store": st, "auto": auto, "on_done": on_done, "page": None,
               "requests": 0, "errors": 0, "done": 0, "started": now, "pacer": None, "mode": mode,
               "soft": 0, "soft_total": 0, "slow": self.win.rate.slow_factor(acc, now), "hold_until": 0.0,
               "tries": 0}
        if kind in ("check", "counts"):
            job["pacer"] = self.win.rate.pacer(acc, lim)
            if kind == "counts":
                job["pacer"].slow = min(ratelimit.SLOW_MAX, job["pacer"].slow * COUNTS_SLOW)
        if kind == "check":
            if st.partial is not None and now - st.partial["started_at"] > PARTIAL_MAX_AGE:
                st.drop_partial()  # foto parcial velha demais mistura listas de dias diferentes
            st.begin_partial(now)
            st.save()
        elif kind == "counts":
            if not st.has_snapshot:
                return ERROR, "Faça uma checagem de seguidores antes."
            job["todo"] = st.need_counts(now, COUNTS_PER_RUN, bool(lim.famous_verified))
            if not job["todo"]:
                return ERROR, "Todos os não seguidores já têm contagem recente."
        elif kind == "unfollow":
            batch = st.load_batch()
            if pks is not None:
                if mode not in follows.MODES:
                    return ERROR, "Modo inválido."
                batch = st.new_batch(mode, pks, lim.famous_threshold, now)
            if batch is None or st.next_in_batch(batch) is None:
                if batch is not None:
                    st.clear_batch()
                return ERROR, "Nada para remover."
            job["batch"] = batch
            job["mode"] = batch["mode"]
        self.job = job
        self.win.rlog.write("seg-inicio", account=s.account.name, kind=kind, auto=auto)
        self._open_page()
        self._emit(f"{KIND_LABEL[kind]}: abrindo sessão...")
        self.changed.emit()
        return OK, ""

    def _page_ready(self, ok: bool):
        job = self.job
        if job is None or job.get("ready"):
            return
        if not ok:
            self._retry_page("a página do Instagram não carregou")
            return
        job["ready"] = True
        QTimer.singleShot(400, self._check_bridge)

    def _check_bridge(self):
        """Confere que o script da página responde antes da primeira ação (sem rede: só lê o cookie)."""
        job = self.job
        if job is None or job["page"] is None:
            return
        job["s"].follow_call(job["page"], "me", None, self._bridge_ready, timeout_s=15.0)

    def _bridge_ready(self, res: dict):
        job = self.job
        if job is None:
            return
        if res.get("ok"):
            job["tries"] = 0
            self._schedule(0.5)
            return
        err = str(res.get("error") or "")
        if "sem sessão" in err:
            self._blocked("login", 401)
            return
        self._retry_page(err or "a página não respondeu")

    def _retry_page(self, why: str):
        """Recria a página oculta algumas vezes antes de desistir (robots.txt pode falhar logo após abrir o app)."""
        job = self.job
        job["tries"] = job.get("tries", 0) + 1
        if job["tries"] > PAGE_TRIES:
            self._end(ERROR, f"Não deu para preparar a sessão de trabalho ({why}). "
                             "Recarregue a conta no app (Ctrl+R) e tente de novo.")
            return
        self._drop_page(job)
        job["ready"] = False
        self._emit(f"Preparando a sessão de trabalho (tentativa {job['tries'] + 1})...")
        QTimer.singleShot(2000, self._open_page)

    def _open_page(self):
        """Lote: usa a página da própria conta (contexto real do site). Leitura: página oculta separada.
        Se a ponte da página da conta não responder, cai para a página oculta."""
        job = self.job
        if job is None:
            return
        page = job["s"].page if job["kind"] == "unfollow" and job["tries"] == 0 else None
        if page is not None:
            job["page"] = page
            job["own_page"] = False
            QTimer.singleShot(0, lambda: self._page_ready(True))
            return
        job["own_page"] = True
        job["page"] = job["s"].worker_page(lambda *_a: None, self._page_ready)

    @staticmethod
    def _drop_page(job: dict) -> None:
        """Fecha só a página oculta criada pela tarefa; a página da conta continua viva."""
        if job.get("page") is not None and job.get("own_page", True):
            job["s"].release_worker_page(job["page"])
        job["page"] = None

    def cancel(self):
        if self.job is None:
            return
        if self.job["kind"] == "unfollow":
            self.job["store"].audit("lote-pausa", reason="cancelado pelo usuário", done=self.job["done"])
        self._end(OK, "Tarefa interrompida. O lote fica salvo e pode ser retomado.")

    # ---------------------------------------------------------------- ritmo
    def _schedule(self, wait: float, quiet: bool = False):
        if self.job is None:
            return
        if wait >= 15 and not quiet:
            self._emit(f"{KIND_LABEL[self.job['kind']]}: pausa de {ratelimit.human_left(wait) if wait >= 60 else str(int(wait)) + ' s'} "
                       "para manter o ritmo humano...")
        self._timer.start(int(max(0.0, wait) * 1000))

    def _read_permit(self) -> bool:
        """Pacer da leitura: agenda a próxima requisição ou encerra por teto. True = pode seguir agora."""
        job = self.job
        d = job["pacer"].decide(time.time())
        if d.stop == "hora" and not job.get("granted"):
            # Teto por hora: espera a janela andar em vez de parar.
            job["granted"] = True
            self._emit(f"{KIND_LABEL[job['kind']]}: teto de {self.limits().per_hour} por hora atingido; "
                       f"continua em {ratelimit.human_left(d.wait)}...")
            self._schedule(d.wait, quiet=True)
            return False
        if d.stop == "dia":
            if job["kind"] == "check":
                job["store"].audit("checagem-parcial", reason=d.stop)
            lim = self.limits()
            why = (f"cota diária de {lim.daily_items} itens" if d.stop == "dia"
                   else f"teto de {lim.per_hour} itens por hora")
            self._end(QUOTA, f"Parou no {why}. O progresso está salvo e continua na próxima vez.")
            return False
        if d.wait > 0.2 and not job.get("granted"):
            job["granted"] = True
            self._schedule(d.wait)
            return False
        job["granted"] = False
        now = time.time()
        job["pacer"].record(now)
        self.win.rate.record_request(job["acc"], now)
        job["requests"] += 1
        return True

    def _step(self):
        job = self.job
        if job is None or job["page"] is None:
            return
        kind = job["kind"]
        s = job["s"]
        if not s.logged_in:
            self._blocked("login", 401)
            return
        rem = job["hold_until"] - time.time()
        if rem > 0.2:
            self._schedule(rem)
            return
        if kind == "check":
            p = job["store"].partial
            if p is None or p["kind"] == "done":
                self._finish_check()
                return
            if not self._read_permit():
                return
            s.follow_call(job["page"], p["kind"], p["cursor"], self._on_page)
        elif kind == "counts":
            if not job["todo"]:
                self._finish_counts()
                return
            if not self._read_permit():
                return
            s.follow_call(job["page"], "profile", job["todo"][0]["u"], self._on_count)
        else:
            batch = job["batch"]
            pk = job["store"].next_in_batch(batch)
            if pk is None:
                self._finish_batch()
                return
            chk = self.win.rate.unfollow_check(job["acc"], self.limits())
            if not chk["ok"]:
                job["store"].audit("lote-pausa", reason=chk["reason"], done=job["done"])
                self._end(COOLDOWN if chk["reason"] == "cooldown" else QUOTA, chk["message"])
                return
            if chk["wait"] > 0.2:
                self._schedule(chk["wait"])
                return
            job["pk"] = pk
            self.item.emit(pk, "ativo")
            remove = pk in batch.get("remove", [])
            st = job["store"]
            user = (st.followers if remove else st.following).get(pk, {}).get("u", "?")
            job["user"] = user
            job["remove"] = remove
            what = "Removendo o seguidor" if remove else "Deixando de seguir"
            self._emit(f"{what} @{user} ({job['done'] + 1} nesta sessão, "
                       f"{len(batch['done']) + 1} de {len(batch['items'])} no lote)...")
            if not follows.USERNAME_RE.match(user):
                self._on_unfollow({"ok": False, "error": "perfil sem nome de usuário na lista"})
                return
            # A ação é feita no botão do próprio site: abre o perfil na janela e clica como uma pessoa.
            s.open_for_action(f"https://www.instagram.com/{user}/", self._profile_open)

    def _profile_open(self, ok: bool):
        job = self.job
        if job is None or not job.get("pk"):
            return
        if not ok:
            self._on_unfollow({"ok": False, "error": "o perfil não abriu na janela"})
            return
        op = "ui_remove_follower" if job.get("remove") else "ui_unfollow"
        job["s"].follow_call(job["page"], op, job["user"], self._on_unfollow, timeout_s=90.0)

    # ---------------------------------------------------------------- respostas
    def _common(self, res: dict):
        """Trata bloqueio e erro. True = resposta boa; "retry" = aviso de limite, mesma requisição depois da pausa."""
        job = self.job
        if job is None:
            return False
        if res.get("ok"):
            job["errors"] = 0
            job["soft"] = 0
            return True
        blk = res.get("blocked")
        if isinstance(blk, dict):
            try:
                status = int(blk.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            text = str(blk.get("text") or "")
            kind = ratelimit.classify_block(status, str(blk.get("url") or ""), text)
            # Em remoção, "feedback_required"/"restringimos" é bloqueio de ação: nunca insistir.
            action_block = job["kind"] == "unfollow" and ACTION_BLOCK_RE.search(text)
            if kind == "rate-limit" and not action_block and self._slow_down(status):
                return "retry"
            if kind:
                self._blocked(kind, status)
                return False
            if blk.get("html"):
                # O endereço usado não devolve mais dados (o Instagram mudou o endpoint da ação).
                self._end(ERROR, "O Instagram respondeu com a página em vez de confirmar a ação. Recarregue a conta "
                                 "(Ctrl+R) e tente de novo; se continuar, o endereço da ação mudou no site.")
                return False
            if job["kind"] == "unfollow":
                # Recusa só deste perfil (o Instagram responde 200 com falha): registra e segue para o próximo.
                job["errors"] += 1
                job["last_fail"] = f"HTTP {status}: {text[:120]}" if text else f"HTTP {status}"
                job["store"].audit("acao-recusada", status=status, motivo=text[:160], user=job.get("user", ""))
                return False
            self._end(ERROR, f"Resposta inesperada do Instagram (HTTP {status}); tarefa interrompida.")
            return False
        job["errors"] += 1
        return False

    def _slow_down(self, status: int) -> bool:
        """'Vá mais devagar': pausa crescente (5, 10, 20, 40 min) e ritmo 2x mais lento, sem parar.
        Checkpoint e login continuam parando na hora. Desiste após SOFT_MAX_TOTAL avisos na tarefa."""
        job = self.job
        wait = ratelimit.soft_backoff(job["soft"] + 1)
        if wait is None or job["soft_total"] >= ratelimit.SOFT_MAX_TOTAL:
            return False
        job["soft"] += 1
        job["soft_total"] += 1
        slow = self.win.rate.record_soft(job["acc"])
        job["slow"] = max(job["slow"] * 2, slow)
        if job["pacer"] is not None:
            base = COUNTS_SLOW if job["kind"] == "counts" else 1.0
            job["pacer"].slow = min(ratelimit.SLOW_MAX, max(job["pacer"].slow * 2, slow * base))
        job["granted"] = False
        job["hold_until"] = time.time() + wait
        minutes = int(round(wait / 60))
        job["store"].audit("desacelerar", task=job["kind"], status=status, minutes=minutes,
                           slow=round(job["pacer"].slow if job["pacer"] else job["slow"], 1))
        self.win.rlog.write("seg-desacelerar", account=job["s"].account.name, kind=job["kind"], status=status,
                            seconds=wait, n=job["soft_total"])
        self._emit(f"O Instagram pediu para ir mais devagar. Pausa de {minutes} min e ritmo mais lento; "
                   "a tarefa continua sozinha depois.")
        self._schedule(wait, quiet=True)
        return True

    def _on_page(self, res: dict):
        job = self.job
        r = self._common(res)
        if r == "retry":
            return
        if not r:
            if self.job is not None:
                self._end(ERROR, "Falha ao ler a lista: " + str(res.get("error", ""))[:120] + ". O progresso está salvo.")
            return
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        st = job["store"]
        kind = st.partial["kind"]
        st.add_page(kind, data.get("users") if isinstance(data.get("users"), list) else [],
                    str(data.get("cursor") or ""), bool(data.get("more")))
        st.save()
        p = st.partial
        label = "seguidores" if kind == "followers" else "seguindo"
        self._emit(f"Checagem: {len(p['followers'])} seguidores, {len(p['following'])} seguindo (lendo {label})...")
        self._step()

    def _on_count(self, res: dict):
        job = self.job
        if job is None:
            return
        r = self._common(res)
        if r == "retry":
            return
        u = job["todo"].pop(0)
        if r:
            data = res.get("data") if isinstance(res.get("data"), dict) else {}
            n = data.get("followers")
            if isinstance(n, int) and not isinstance(n, bool):
                job["store"].set_count(u["pk"], n, time.time())
                job["done"] += 1
            if job["done"] % 5 == 0:
                job["store"].save()
        elif self.job is None:
            return
        elif job["errors"] >= MAX_CONSECUTIVE_ERRORS:
            job["store"].save()
            self._end(ERROR, "Muitas falhas seguidas ao ler perfis; contagem interrompida.")
            return
        self._emit(f"Contando seguidores: {job['done']} feitos, {len(job['todo'])} restantes nesta rodada...")
        self._step()

    def _on_unfollow(self, res: dict):
        job = self.job
        if job is None:
            return
        pk = job.pop("pk", "")
        st, batch = job["store"], job["batch"]
        r = self._common(res)
        if r == "retry":
            return
        if r:
            now = time.time()
            nxt = self.win.rate.record_unfollow(job["acc"], self.limits(), now)
            if job["slow"] > 1:  # depois de aviso de limite, intervalo multiplicado
                job["hold_until"] = max(job["hold_until"], now + (nxt - now) * job["slow"])
            if pk in batch.get("remove", []):
                st.record_removed(pk, now)
            else:
                st.record_unfollowed(pk, now, job["mode"])
            batch["done"].append(pk)
            st.save_batch(batch)
            job["done"] += 1
            self.win.rlog.write("seg-remocao", account=job["s"].account.name, n=job["done"])
            self.item.emit(pk, "removido")
            self._step()
            return
        if self.job is None:
            return
        err = str(res.get("error") or job.pop("last_fail", "") or "erro")[:160]
        if "carregando" in err or "página fechada" in err or "tempo esgotado" in err:
            # A ponte da página sumiu (recarregou, app reaberto): recria e repete a mesma ação.
            job["pk"] = pk
            self._retry_page(err)
            return
        # Erro comum (sem sinal de bloqueio): registra e conta como tentativa no ritmo.
        self.win.rate.record_unfollow(job["acc"], self.limits())
        batch["errors"][pk] = err
        st.save_batch(batch)
        self.item.emit(pk, "erro")
        if job["errors"] >= MAX_CONSECUTIVE_ERRORS:
            st.audit("lote-pausa", reason=f"falhas seguidas: {err}", done=job["done"])
            self._end(ERROR, f"Três falhas seguidas ({err}); lote pausado por segurança. "
                             "Abra um perfil da lista no app e confira se o botão de seguir responde.")
            return
        self._step()

    # ---------------------------------------------------------------- fim
    def _finish_check(self):
        job = self.job
        summary = job["store"].finish_snapshot(time.time())
        self.win.rate.record_success(job["acc"])
        if summary["first"]:
            msg = f"Primeira checagem: {summary['followers']} seguidores e {summary['following']} seguindo."
        else:
            msg = (f"Checagem concluída: {summary['followers']} seguidores, {summary['lost']} deixaram de seguir, "
                   f"{summary['gained']} novos.")
        if summary["lost"] and job["auto"]:
            self.win.toast.show_message(f"{summary['lost']} pessoa(s) deixaram de seguir {job['s'].account.name}.", 8000,
                                        "Ver", lambda: self.win.open_follows())
        self._end(OK, msg)

    def _finish_counts(self):
        job = self.job
        job["store"].save()
        job["store"].audit("contagem", n=job["done"])
        left = len(job["store"].need_counts(time.time(), verified=bool(self.limits().famous_verified)))
        self._end(OK, f"Contagem concluída: {job['done']} perfis." + (f" Faltam {left}; rode de novo depois." if left else ""))

    def _finish_batch(self):
        job = self.job
        b = job["batch"]
        job["store"].audit("lote-fim", mode=b["mode"], done=len(b["done"]), errors=len(b["errors"]))
        job["store"].clear_batch()
        self._end(OK, f"Lote concluído: {len(b['done'])} removidos" + (f", {len(b['errors'])} com erro." if b["errors"] else "."))

    def _blocked(self, kind: str, status: int):
        job = self.job
        until = self.win.rate.record_block(job["acc"], kind)
        job["store"].audit("bloqueio", kind=kind, status=status, task=job["kind"], cooldown_until=ratelimit.hhmm(until))
        self.win.rlog.write("bloqueio", account=job["s"].account.name, profile="seguidores", kind=kind, status=status,
                            requests=job["requests"], cooldown_until=ratelimit.hhmm(until))
        msg = (f"{ratelimit.BLOCK_LABEL[kind]}. Tarefa interrompida sem novas tentativas; "
               f"liberada às {ratelimit.hhmm(until)}. O progresso está salvo.")
        self._end(COOLDOWN, msg)

    def _end(self, code: int, msg: str):
        job = self.job
        if job is None:
            return
        self.job = None
        self._timer.stop()
        self._drop_page(job)
        self.win.rlog.write("seg-fim", account=job["s"].account.name, kind=job["kind"], code=code,
                            requests=job["requests"], done=job["done"], seconds=time.time() - job["started"],
                            reason=msg if code != OK else "")
        if code != OK:
            job["store"].audit("tarefa-erro", kind=job["kind"], code=code, reason=msg, done=job["done"])
        self.last_message = msg
        self._emit(msg)
        if not job["auto"] or code != OK:
            self.win.toast.show_message(msg, 7000)
        if job.get("pk"):
            self.item.emit(job["pk"], "pendente")
        self.changed.emit()
        if job["on_done"]:
            job["on_done"](code, msg)
        acc, self._queued_start = self._queued_start, ""
        if acc and code == OK:
            s = self.win.sessions.get(acc)
            if s is not None:
                QTimer.singleShot(1500, lambda: self.start("unfollow", s))

    def _emit(self, msg: str):
        self.last_message = msg
        self.progress.emit(msg)

    # ---------------------------------------------------------------- checagem diária
    def _daily_tick(self):
        win = self.win
        if not self.limits().follow_check or self.busy or win.outer.currentIndex() != 0:
            return
        now = time.time()
        for s in list(win.sessions.values()):
            if s.profile is None or not s.logged_in:
                continue
            st = self.store(s.account.id)
            if st.check_due(now, AUTO_CHECK_HOURS) or st.partial is not None and st.has_snapshot:
                code, _msg = self.start("check", s, auto=True)
                if code == OK:
                    return

    # ---------------------------------------------------------------- linha de comando
    def handle(self, req: dict, reply: Callable[[dict], None]):
        """Pedido `F {json}` do socket local: status, check ou list. reply é chamado uma vez."""
        win = self.win
        op = str(req.get("op", ""))
        rid = str(req.get("id", ""))

        def answer(code: int, message: str = "", data=None):
            reply({"proto": 1, "id": rid, "ok": code == OK, "code": code, "message": message[:600], "data": data or {}})

        if not security.is_request_id(rid) or op not in ("status", "check", "list"):
            answer(ERROR, "Pedido inválido.")
            return
        if win.outer.currentIndex() != 0:
            answer(APP, "O Gramdesk está bloqueado.")
            return
        s = win._research_session(str(req.get("account", ""))[:40])
        if s is None:
            answer(APP, "Conta não encontrada ou não aberta no app.")
            return
        acc = s.account.id
        if op == "status":
            answer(OK, "", {"account": s.account.name, "logged_in": bool(s.profile is not None and s.logged_in),
                            "busy": self.busy, **self.summary(acc)})
            return
        if op == "list":
            what = str(req.get("what", ""))
            st = self.store(acc)
            groups = self.split(st)
            if what == "nao-seguidores":
                items = groups["comuns"]
            elif what == "famosos":
                items = groups["famosos"]
            elif what == "mutuos":
                items = st.mutuals()
            elif what == "fas":
                items = st.fans()
            elif what == "deixaram":
                items = sorted(st.unfollowers, key=lambda e: -e["lost_at"])
            else:
                answer(ERROR, "Lista inválida (nao-seguidores, famosos, mutuos, fas, deixaram).")
                return
            answer(OK, "", {"account": s.account.name, "taken_at": st.taken_at, "items": items[:5000]})
            return
        # check: com a checagem diária ligada e uma foto anterior, roda sem perguntar (o usuário já autorizou).
        st = self.store(acc)
        if not (self.limits().follow_check and st.has_snapshot):
            win.raise_window()
            if not win.confirm("Permitir checagem de seguidores?",
                               f"Um programa no seu computador pediu para checar seguidores de \"{s.account.name}\". "
                               "É somente leitura, no ritmo de uma pessoa.\n\nSe você não pediu isso, recuse.", "Permitir"):
                answer(REFUSED, "Recusado na janela do Gramdesk.")
                return
        code, msg = self.start("check", s, auto=True, on_done=lambda c, m: answer(c, m, self.summary(acc)))
        if code != OK:
            answer(code, msg)
