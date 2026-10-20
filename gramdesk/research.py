"""Pesquisa de perfil: coleta somente leitura e grava um dataset local para análise.

Saída em ~/Downloads/Gramdesk/Pesquisas/<perfil>-<data>/:
  status.json     estado da coleta (lido pela CLI gramdesk-research)
  profile.json    dados públicos do perfil
  posts.json      posts normalizados (legenda, hashtags, engajamento, mídias)
  comments.json   comentários dos posts mais comentados
  highlights.json destaques e seus itens
  media/          imagens (e vídeos, se pedido) com nomes estáveis
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import ratelimit, research_cache, security
from .config import VERSION

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
STATES_FINAL = ("done", "error", "cancelled", "refused")
MAX_MEDIA = 900


def research_root(download_root: Path) -> Path:
    return download_root / "Pesquisas"


def clean_username(text: str) -> str | None:
    t = (text or "").strip()
    url = security.parse_instagram_input(t)
    if url:
        m = re.match(r"https://www\.instagram\.com/([A-Za-z0-9._]{1,30})/?$", url)
        if m and m.group(1) not in ("p", "reel", "reels", "stories", "explore", "accounts", "direct"):
            return m.group(1)
    t = t.lstrip("@")
    return t if USERNAME_RE.match(t) else None


def username_from_url(url: str) -> str | None:
    m = re.match(r"https://www\.instagram\.com/([A-Za-z0-9._]{1,30})/(?:reels/|tagged/)?(?:\?.*)?$", url or "")
    if m and m.group(1) not in ("p", "reel", "reels", "stories", "explore", "accounts", "direct"):
        return m.group(1)
    return None


def _write_json(path: Path, data) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_researches(root: Path) -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if not d.is_dir() or d.is_symlink():
            continue
        try:
            st = json.loads((d / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(st, dict):
            st["dir"] = str(d)
            out.append(st)
    out.sort(key=lambda s: s.get("started_at", 0), reverse=True)
    return out


class Limiter:
    """O que a coleta precisa do rate limiting: estado persistente, limites, log e conta."""

    def __init__(self, state: ratelimit.RateState, limits: ratelimit.Limits, log: ratelimit.ResearchLog, account_id: str):
        self.state = state
        self.limits = limits
        self.log = log
        self.account_id = account_id


class ResearchJob(QObject):
    progress = pyqtSignal(str, int, int)  # texto, feito, total
    finished = pyqtSignal(str, str)  # estado final, mensagem

    def __init__(self, session, username: str, opts: dict, rid: str, root: Path, limiter: Limiter,
                 cache_dir: Path | None = None, parent=None):
        super().__init__(parent)
        if not USERNAME_RE.match(username) or not security.is_request_id(rid):
            raise ValueError("pedido inválido")
        self.session = session
        self.username = username
        self.rid = rid
        self.limiter = limiter
        self.opts = self.normalize_opts(opts)
        self.profile: dict | None = None
        self.posts: list[dict] = []
        self.comments: dict[str, list] = {}
        self.highlights: list[dict] = []
        self.plan: dict | None = None
        self.cursor = ""
        self.feed_done = False
        self.highlights_done = False
        if cache_dir is not None:
            self.dir = cache_dir
            self.plan = research_cache.plan(cache_dir, self.opts)
            self.profile = self.plan["profile"]
            self.posts = self.plan["posts"]
            self.comments = self.plan["comments"]
            self.highlights = self.plan["highlights"]
            self.cursor = self.plan["cursor"]
            self.feed_done = self.plan["feed_done"]
            self.highlights_done = not self.plan["need_highlights"] and bool(self.highlights)
        else:
            self.dir = root / f"{username}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.media = self.dir / "media"
        self.via = ""
        self.state = "collecting"
        self.error = ""
        self.block = ""
        self.pending = 0
        self.saved = 0
        self.queued = 0
        self.requests = 0
        self._page = None
        self._collect_done = False
        self._started = time.time()
        self._pacer = limiter.state.pacer(limiter.account_id, limiter.limits)
        self._dl_queue: list[tuple] = []
        self._dl_active = False
        self._gate_timer: QTimer | None = None
        # Encerra a espera por downloads se nenhum terminar por 2 min (reinicia a cada arquivo).
        self._grace = QTimer(self, singleShot=True, interval=120_000, timeout=self._finish_downloads)

    @staticmethod
    def normalize_opts(opts: dict) -> dict:
        return {
            "posts": max(1, min(300, int(opts.get("posts", 60)))),
            "comments": bool(opts.get("comments", True)),
            "commentPosts": max(0, min(25, int(opts.get("commentPosts", 15)))),
            "highlights": bool(opts.get("highlights", True)),
            "videos": bool(opts.get("videos", False)),
        }

    @property
    def from_cache(self) -> bool:
        return self.plan is not None

    @property
    def needs_network(self) -> bool:
        return not (self.plan and self.plan["complete"])

    # ---------- ciclo ----------
    def start(self):
        self.media.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        lim = self.limiter
        lim.log.write("inicio", account=self.session.account.name, profile=self.username, options=self.opts,
                      cache=self.from_cache, network=self.needs_network,
                      have_posts=len(self.posts))
        if not self.needs_network:
            self._collect_done = True
            self._status()
            QTimer.singleShot(0, lambda: self._end("done", f"Pronto (do cache): {len(self.posts)} posts já coletados, nada novo a buscar."))
            return
        lim.state.begin(lim.account_id)
        self._status()
        self._page = self.session.worker_page(self._on_bridge_event, self._on_page_ready)
        msg = "Retomando coleta salva..." if self.from_cache else "Abrindo sessão de pesquisa..."
        self.progress.emit(msg, len(self.posts), self.opts["posts"])

    def _on_page_ready(self, ok: bool):
        if self.state in STATES_FINAL or self._page is None:
            return
        if not ok:
            self._fail("Não carregou a página do Instagram.")
            return
        opts = dict(self.opts)
        if self.plan is not None:
            p = dict(self.plan, cursor=self.cursor, feed_done=self.feed_done)
            opts["resume"] = research_cache.resume_payload(p)
        self.session.run_research(self._page, self.rid, self.username, opts, self._started_cb)

    def _started_cb(self, accepted):
        if not accepted and self.state not in STATES_FINAL:
            self._fail("A página recusou o pedido (já existe pesquisa em andamento?).")

    def cancel(self):
        if self.state in STATES_FINAL:
            return
        if self._page is not None:
            self.session.cancel_research(self._page, self.rid)
        self._end("cancelled", "Pesquisa cancelada.")

    # ---------- ritmo ----------
    def _on_gate(self, what: str):
        lim = self.limiter
        d = self._pacer.decide(time.time())
        if d.stop:
            lim.log.write("limite-local", account=self.session.account.name, profile=self.username,
                          kind=d.stop, requests=self.requests)
            self.session.release_research(self._page, self.rid, False, d.stop)
            return
        if d.wait >= 15:
            lim.log.write("pausa", account=self.session.account.name, seconds=d.wait, step=what)
            self.progress.emit(f"Pausa de {int(d.wait)} s para manter o ritmo humano...", len(self.posts), self.opts["posts"])
        self._gate_timer = QTimer(self, singleShot=True, interval=int(d.wait * 1000), timeout=lambda: self._grant(what))
        self._gate_timer.start()

    def _grant(self, what: str):
        if self.state in STATES_FINAL or self._page is None:
            return
        now = time.time()
        self._pacer.record(now)
        self.requests += 1
        self.limiter.state.record_request(self.limiter.account_id, now)
        self.limiter.log.write("requisicao", account=self.session.account.name, step=what, n=self.requests)
        self.session.release_research(self._page, self.rid, True)

    # ---------- eventos da página ----------
    def _on_bridge_event(self, rid: str, phase: str, data):
        if rid != self.rid or self.state in STATES_FINAL:
            return
        if phase == "gate":
            self._on_gate(str((data or {}).get("what", ""))[:20] if isinstance(data, dict) else "")
        elif phase == "progress" and isinstance(data, dict):
            step = data.get("step")
            if step == "posts":
                self.progress.emit(f"Posts: {data.get('got', 0)} de {data.get('max', 0)}", int(data.get("got", 0)), int(data.get("max", 1)))
            elif step == "comentarios":
                self.progress.emit(f"Comentários de {len(self.comments)} posts...", len(self.posts), self.opts["posts"])
            elif step == "privado":
                self.progress.emit("Perfil privado: só dá para ver o que a conta já segue.", 0, 1)
            elif step == "perfil":
                self.progress.emit(f"Lendo @{self.username}...", 0, self.opts["posts"])
            elif step == "perfil-cache":
                self.progress.emit(f"@{self.username}: usando perfil do cache, buscando só o que falta...", len(self.posts), self.opts["posts"])
        elif phase == "profile" and isinstance(data, dict):
            self.profile = data
            pic = data.get("profile_pic")
            if security.is_safe_media_url(pic):
                data["profile_pic_file"] = self._download(pic, "profile")
            data.pop("profile_pic", None)
            _write_json(self.dir / "profile.json", data)
        elif phase == "posts" and isinstance(data, dict):
            self.via = str(data.get("via", ""))[:20]
            for post in (data.get("posts") or [])[:50]:
                if isinstance(post, dict):
                    self._add_post(post)
            cur = str(data.get("cursor") or "")[:200]
            self.cursor = cur if re.fullmatch(r"[A-Za-z0-9_=-]{0,200}", cur) else ""
            self.feed_done = not data.get("more")
            _write_json(self.dir / "posts.json", self.posts)
            self._status()
        elif phase == "comments" and isinstance(data, dict):
            pk = str(data.get("pk", ""))
            if re.fullmatch(r"\d{1,25}", pk):
                self.comments[pk] = [c for c in (data.get("comments") or [])[:30] if isinstance(c, dict)]
                _write_json(self.dir / "comments.json", self.comments)
        elif phase == "highlights" and isinstance(data, list):
            self.highlights = []
            self._add_highlights(data[:40])
            self.highlights_done = True
            _write_json(self.dir / "highlights.json", self.highlights)
        elif phase == "done":
            self.limiter.state.record_success(self.limiter.account_id)
            self._collect_done = True
            self.state = "downloading"
            self._status()
            self.progress.emit(f"Coleta concluída. Baixando mídias ({self.pending} restantes)...", 1, 1)
            if self.pending == 0:
                self._finish_downloads()
            else:
                self._grace.start()
        elif phase == "error" and isinstance(data, dict):
            self._on_error(data)

    def _on_error(self, data: dict):
        lim = self.limiter
        raw = str(data.get("message", "erro"))[:200]
        msg = raw
        if raw == "blocked":
            try:
                status = int(data.get("status") or 0)
            except (TypeError, ValueError):
                status = 0
            kind = ratelimit.classify_block(status, str(data.get("url") or ""), str(data.get("text") or ""))
            if kind:
                self.block = kind
                until = lim.state.record_block(lim.account_id, kind)
                lim.log.write("bloqueio", account=self.session.account.name, profile=self.username, kind=kind,
                              status=status, requests=self.requests, cooldown_until=ratelimit.hhmm(until))
                msg = (f"{ratelimit.BLOCK_LABEL[kind]}. Coleta interrompida sem novas tentativas; "
                       f"nova coleta liberada às {ratelimit.hhmm(until)}.")
                if kind == "checkpoint":
                    msg += " Abra a conta no app e conclua a verificação."
                elif kind == "login":
                    msg += " Entre na conta de novo no app."
            else:
                msg = f"Resposta inesperada do Instagram (HTTP {status}); coleta interrompida."
        elif raw.startswith("limite-local:"):
            why = raw.split(":", 1)[1]
            if why == "dia":
                msg = f"Cota diária de {lim.limits.daily_items} itens atingida."
            else:
                msg = f"Limite local de {lim.limits.per_hour} itens por hora atingido."
            msg += " O que já foi coletado está salvo; a próxima pesquisa deste perfil continua daqui."
        elif raw == "cancelado":
            return
        lim.log.write("erro", account=self.session.account.name, profile=self.username,
                      kind=self.block or raw.split(":")[0][:30], posts=len(self.posts))
        if self.posts:
            self._collect_done = True
            self.error = msg
            self.state = "downloading"
            self._status()
            self.progress.emit(msg, 1, 1)
            if self.pending == 0:
                self._finish_downloads()
            else:
                self._grace.start()
        else:
            self._fail(msg)

    def _add_post(self, post: dict):
        code = str(post.get("code", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code) or any(p.get("code") == code for p in self.posts):
            return
        media = post.get("media") if isinstance(post.get("media"), list) else []
        for i, m in enumerate(media[:20]):
            if not isinstance(m, dict):
                continue
            stem = f"{code}_{i + 1}"
            if m.get("kind") == "video":
                if security.is_safe_media_url(m.get("thumb")):
                    m["thumb_file"] = self._download(m["thumb"], stem)
                if self.opts["videos"] and security.is_safe_media_url(m.get("url")):
                    m["file"] = self._download(m["url"], stem)
            elif security.is_safe_media_url(m.get("url")):
                m["file"] = self._download(m["url"], stem)
            # URLs assinadas expiram; o dataset guarda só arquivos locais.
            m.pop("url", None)
            m.pop("thumb", None)
        post["media"] = media
        self.posts.append(post)

    def _add_highlights(self, items: list):
        for h in items:
            if not isinstance(h, dict) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", str(h.get("id", ""))):
                continue
            hid = str(h["id"]).replace("highlight:", "")
            if security.is_safe_media_url(h.get("cover")):
                h["cover_file"] = self._download(h["cover"], f"highlight_{hid}_cover", "highlights")
            h.pop("cover", None)
            for j, it in enumerate((h.get("items") or [])[:60]):
                m = it.get("media") if isinstance(it, dict) else None
                if isinstance(m, dict):
                    src = m.get("thumb") if m.get("kind") == "video" else m.get("url")
                    if security.is_safe_media_url(src):
                        m["file"] = self._download(src, f"highlight_{hid}_{j + 1}", "highlights")
                    m.pop("url", None)
                    m.pop("thumb", None)
            self.highlights.append(h)

    # ---------- mídia (um download por vez, com intervalo aleatório) ----------
    def _download(self, url: str, stem: str, sub: str = "") -> str | None:
        if self.queued >= MAX_MEDIA:
            return None
        ext = security.media_ext(url)
        if not ext:
            return None
        name = security.safe_filename(stem, ext)
        target = self.media / sub if sub else self.media
        rel = f"media/{sub + '/' if sub else ''}{name}"
        if (target / name).exists():
            return rel
        self.queued += 1
        self.pending += 1
        self._dl_queue.append((url, target, name))
        self._pump()
        return rel

    def _pump(self):
        if self._dl_active or not self._dl_queue or self.state in STATES_FINAL:
            return
        url, target, name = self._dl_queue.pop(0)
        self._dl_active = True
        page = self._page
        self.session.queue_file(url, target, name, self._file_done, page=page)

    def _file_done(self, ok: bool):
        self._dl_active = False
        self.pending = max(0, self.pending - 1)
        if ok:
            self.saved += 1
        if self._dl_queue:
            QTimer.singleShot(int(ratelimit.download_gap() * 1000), self._pump)
        if self._collect_done and self.pending == 0:
            self._finish_downloads()
        elif self._collect_done:
            self._grace.start()  # reinicia a espera
            if self.pending % 10 == 0:
                self.progress.emit(f"Baixando mídias ({self.pending} restantes)...", 1, 1)

    def _finish_downloads(self):
        if self.state in STATES_FINAL:
            return
        self._grace.stop()
        if self.error:
            self._end("done", f"Coleta parcial: {self.error}")
        else:
            src = " (retomada do cache)" if self.from_cache else ""
            self._end("done", f"Pronto{src}: {len(self.posts)} posts e {self.saved} arquivos de mídia novos.")

    def _fail(self, msg: str):
        self.error = msg
        self._end("error", msg)

    def _end(self, state: str, msg: str):
        if self.state in STATES_FINAL:
            return
        self.state = state
        if state == "error":
            self.error = msg
        if self._gate_timer is not None:
            self._gate_timer.stop()
        self._dl_queue.clear()
        self._status(message=msg)
        self.limiter.log.write("fim", account=self.session.account.name, profile=self.username, state=state,
                               posts=len(self.posts), media_files=self.saved, requests=self.requests,
                               block=self.block, seconds=time.time() - self._started, cache=self.from_cache)
        if self._page is not None:
            self.session.release_worker_page(self._page)
            self._page = None
        self.finished.emit(state, msg)

    def _status(self, message: str = ""):
        prev = {}
        if self.from_cache:
            try:
                prev = json.loads((self.dir / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prev = {}
        # Validade do cache conta da primeira coleta: retomar não estica o prazo.
        collected_at = prev.get("collected_at") or prev.get("started_at") or self._started
        _write_json(self.dir / "status.json", {
            "rid": self.rid,
            "username": self.username,
            "account": self.session.account.name,
            "state": self.state,
            "message": message,
            "error": self.error,
            "block": self.block,
            "started_at": prev.get("started_at", self._started) if self.from_cache else self._started,
            "updated_at": time.time(),
            "collected_at": collected_at,
            "options": self.opts,
            "counts": {
                "posts": len(self.posts),
                "media_files": self.saved,
                "media_pending": self.pending,
                "comments_posts": len(self.comments),
                "highlights": len(self.highlights),
                "requests": self.requests,
            },
            "resume": {"cursor": self.cursor, "feed_done": self.feed_done, "highlights_done": self.highlights_done},
            "from_cache": self.from_cache,
            "source": self.via,
            "app": f"Gramdesk {VERSION}",
        })


def mark_refused(root: Path, username: str, rid: str, reason: str) -> Path:
    d = root / f"{username}-{time.strftime('%Y%m%d-%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "status.json", {
        "rid": rid, "username": username, "state": "refused", "message": reason, "error": reason,
        "started_at": time.time(), "updated_at": time.time(), "counts": {}, "app": f"Gramdesk {VERSION}",
    })
    return d


