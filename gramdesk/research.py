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

from . import security
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


class ResearchJob(QObject):
    progress = pyqtSignal(str, int, int)  # texto, feito, total
    finished = pyqtSignal(str, str)  # estado final, mensagem

    def __init__(self, session, username: str, opts: dict, rid: str, root: Path, parent=None):
        super().__init__(parent)
        if not USERNAME_RE.match(username) or not security.is_request_id(rid):
            raise ValueError("pedido inválido")
        self.session = session
        self.username = username
        self.rid = rid
        self.opts = {
            "posts": max(1, min(300, int(opts.get("posts", 60)))),
            "comments": bool(opts.get("comments", True)),
            "commentPosts": max(0, min(25, int(opts.get("commentPosts", 15)))),
            "highlights": bool(opts.get("highlights", True)),
            "videos": bool(opts.get("videos", False)),
        }
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = root / f"{username}-{stamp}"
        self.media = self.dir / "media"
        self.profile: dict | None = None
        self.posts: list[dict] = []
        self.comments: dict[str, list] = {}
        self.highlights: list[dict] = []
        self.via = ""
        self.state = "collecting"
        self.error = ""
        self.pending = 0
        self.saved = 0
        self.queued = 0
        self._page = None
        self._collect_done = False
        self._started = time.time()
        self._grace = QTimer(self, singleShot=True, interval=180_000, timeout=self._finish_downloads)

    # ---------- ciclo ----------
    def start(self):
        self.media.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self._status()
        self._page = self.session.worker_page(self._on_bridge_event, self._on_page_ready)
        self.progress.emit("Abrindo sessão de pesquisa...", 0, self.opts["posts"])

    def _on_page_ready(self, ok: bool):
        if not ok:
            self._fail("Não carregou a página do Instagram.")
            return
        self.session.run_research(self._page, self.rid, self.username, self.opts, self._started_cb)

    def _started_cb(self, accepted):
        if not accepted:
            self._fail("A página recusou o pedido (já existe pesquisa em andamento?).")

    def cancel(self):
        if self.state in STATES_FINAL:
            return
        if self._page is not None:
            self.session.cancel_research(self._page, self.rid)
        self._end("cancelled", "Pesquisa cancelada.")

    # ---------- eventos da página ----------
    def _on_bridge_event(self, rid: str, phase: str, data):
        if rid != self.rid or self.state in STATES_FINAL:
            return
        if phase == "progress" and isinstance(data, dict):
            step = data.get("step")
            if step == "posts":
                self.progress.emit(f"Posts: {data.get('got', 0)} de {data.get('max', 0)}", int(data.get("got", 0)), int(data.get("max", 1)))
            elif step == "comentarios":
                self.progress.emit(f"Comentários de {len(self.comments)} posts...", len(self.posts), self.opts["posts"])
            elif step == "privado":
                self.progress.emit("Perfil privado: só dá para ver o que a conta já segue.", 0, 1)
            elif step == "perfil":
                self.progress.emit(f"Lendo @{self.username}...", 0, self.opts["posts"])
        elif phase == "profile" and isinstance(data, dict):
            self.profile = data
            pic = data.get("profile_pic")
            if security.is_safe_media_url(pic):
                data["profile_pic_file"] = self._download(pic, "profile")
            _write_json(self.dir / "profile.json", data)
        elif phase == "posts" and isinstance(data, dict):
            self.via = str(data.get("via", ""))[:20]
            for post in (data.get("posts") or [])[:50]:
                if isinstance(post, dict):
                    self._add_post(post)
            _write_json(self.dir / "posts.json", self.posts)
            self._status()
        elif phase == "comments" and isinstance(data, dict):
            pk = str(data.get("pk", ""))
            if re.fullmatch(r"\d{1,25}", pk):
                self.comments[pk] = [c for c in (data.get("comments") or [])[:30] if isinstance(c, dict)]
                _write_json(self.dir / "comments.json", self.comments)
        elif phase == "highlights" and isinstance(data, list):
            self._add_highlights(data[:40])
            _write_json(self.dir / "highlights.json", self.highlights)
        elif phase == "done":
            self._collect_done = True
            self.state = "downloading"
            self._status()
            self.progress.emit(f"Coleta concluída. Baixando mídias ({self.pending} restantes)...", 1, 1)
            if self.pending == 0:
                self._finish_downloads()
            else:
                self._grace.start()
        elif phase == "error" and isinstance(data, dict):
            msg = str(data.get("message", "erro"))[:200]
            if msg == "rate-limit":
                msg = "O Instagram pediu para ir mais devagar (limite de requisições). O que já foi coletado está salvo."
            elif msg == "sem-acesso":
                msg = "Sem acesso: confira se a conta está logada."
            if self.posts:
                self._collect_done = True
                self.error = msg
                self.state = "downloading"
                self._grace.start()
                self.progress.emit(msg, 1, 1)
            else:
                self._fail(msg)

    def _add_post(self, post: dict):
        code = str(post.get("code", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
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

    # ---------- mídia ----------
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
        self.session.queue_file(url, target, name, self._file_done, page=self._page)
        return rel

    def _file_done(self, ok: bool):
        self.pending = max(0, self.pending - 1)
        if ok:
            self.saved += 1
        if self._collect_done and self.pending == 0:
            self._finish_downloads()
        elif self._collect_done and self.pending % 10 == 0:
            self.progress.emit(f"Baixando mídias ({self.pending} restantes)...", 1, 1)

    def _finish_downloads(self):
        if self.state in STATES_FINAL:
            return
        self._grace.stop()
        if self.error:
            self._end("done", f"Coleta parcial: {self.error}")
        else:
            self._end("done", f"Pronto: {len(self.posts)} posts e {self.saved} arquivos de mídia.")

    def _fail(self, msg: str):
        self.error = msg
        self._end("error", msg)

    def _end(self, state: str, msg: str):
        self.state = state
        if state == "error":
            self.error = msg
        self._status(message=msg)
        if self._page is not None:
            self.session.release_worker_page(self._page)
            self._page = None
        self.finished.emit(state, msg)

    def _status(self, message: str = ""):
        _write_json(self.dir / "status.json", {
            "rid": self.rid,
            "username": self.username,
            "account": self.session.account.name,
            "state": self.state,
            "message": message,
            "error": self.error,
            "started_at": self._started,
            "updated_at": time.time(),
            "options": self.opts,
            "counts": {
                "posts": len(self.posts),
                "media_files": self.saved,
                "media_pending": self.pending,
                "comments_posts": len(self.comments),
                "highlights": len(self.highlights),
            },
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


