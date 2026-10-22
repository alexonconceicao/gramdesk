"""Fila para baixar todas as mídias de um perfil, um item por vez.

Módulo puro (sem Qt, sem rede): guarda a ordem da grade, o que já foi baixado e o que falhou,
decide o próximo item e o que fazer com cada resposta da navegação passo a passo. O ritmo, as
cotas e o resfriamento ficam no BrowseController (ratelimit), que executa cada passo.

Estado por conta e perfil em <dados>/queues/<conta>-<perfil>.json (0600), para retomar depois.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import browse, security
from .ratelimit import _atomic_write

# Mesmos códigos de resposta do BrowseController (browse_qt).
OK, ERROR, APP, COOLDOWN, QUOTA, BUSY, REFUSED = 0, 1, 3, 4, 5, 6, 7
KINDS = {"video": ("video", "reel"), "image": ("image", "carousel")}
_CODE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
MAX_ITEMS = 2000


class ProfileQueue:
    def __init__(self, path: Path, profile: str, folder: Path, only: str = ""):
        self.path = path
        self.profile = profile
        self.folder = folder  # Downloads/Gramdesk/<conta>/<perfil>
        self.only = only if only in KINDS else ""
        self.items: list[dict] = []
        self.totals: dict[str, int] = {}
        self.done: dict[str, list[int]] = {}
        self.errors: dict[str, str] = {}
        self.grid_complete = False

    @classmethod
    def load(cls, root: Path, acc_id: str, profile: str, folder: Path, only: str = "") -> "ProfileQueue":
        q = cls(Path(root) / f"{acc_id}-{security.folder_name(profile)}.json", profile, Path(folder), only)
        try:
            raw = json.loads(q.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            return q
        q.merge(raw.get("items") if isinstance(raw.get("items"), list) else [])
        known = {i["code"] for i in q.items}
        for code, n in (raw.get("totals") or {}).items() if isinstance(raw.get("totals"), dict) else ():
            if code in known and isinstance(n, int) and 0 < n <= 50:
                q.totals[code] = n
        for code, idx in (raw.get("done") or {}).items() if isinstance(raw.get("done"), dict) else ():
            if code in known and isinstance(idx, list):
                q.done[code] = sorted({i for i in idx if isinstance(i, int) and 0 < i <= 50})
        q.grid_complete = raw.get("grid_complete") is True
        return q

    def save(self) -> None:
        _atomic_write(self.path, {"version": 1, "profile": self.profile, "grid_complete": self.grid_complete,
                                  "items": self.items, "totals": self.totals, "done": self.done})

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    # ---------------------------------------------------------------- grade
    def merge(self, items) -> int:
        """Acrescenta posts novos da grade, na ordem em que aparecem. Devolve quantos entraram."""
        known = {i["code"] for i in self.items}
        added = 0
        for it in items:
            if not isinstance(it, dict) or len(self.items) >= MAX_ITEMS:
                continue
            code, url = str(it.get("code") or ""), str(it.get("url") or "")
            if code in known or not _CODE.match(code):
                continue
            t = browse.target_of(url)
            if not t or t["kind"] != "post" or t["code"] != code:
                continue
            kind = it.get("kind") if it.get("kind") in ("image", "carousel", "video", "reel") else "image"
            self.items.append({"code": code, "url": t["url"], "kind": kind})
            known.add(code)
            added += 1
        return added

    # ---------------------------------------------------------------- próximo item
    def start(self) -> None:
        """Nova rodada: tenta de novo os itens que falharam na anterior."""
        self.errors = {}

    def _wanted(self, it: dict) -> bool:
        return not self.only or it["kind"] in KINDS[self.only]

    def _on_disk(self, code: str) -> bool:
        """Post de arquivo único já baixado antes (mesmo nome que o download usa)."""
        stem = browse.file_name(security.folder_name(self.profile), code, ".jpg")[:-4]
        return any(self.folder.glob(f"{stem}.*"))

    def _complete(self, it: dict) -> bool:
        code = it["code"]
        total = self.totals.get(code)
        if total is None and not self.done.get(code) and self._on_disk(code):
            self.totals[code], self.done[code] = 1, [1]
        total = self.totals.get(code)
        return total is not None and len(self.done.get(code, [])) >= total

    def next_task(self) -> tuple[str, str, int] | None:
        for it in self.items:
            if not self._wanted(it) or it["code"] in self.errors or self._complete(it):
                continue
            got = set(self.done.get(it["code"], []))
            total = self.totals.get(it["code"]) or 1
            index = next(i for i in range(1, total + 1) if i not in got)
            return it["code"], it["url"], index
        return None

    def progress(self) -> tuple[int, int]:
        wanted = [it for it in self.items if self._wanted(it)]
        return sum(1 for it in wanted if self._complete(it)), len(wanted)

    # ---------------------------------------------------------------- respostas
    def apply(self, task: tuple[str, str, int], code: int, data: dict, message: str = "") -> str:
        """Registra a resposta de um passo. Devolve 'next', 'pause' (limite/bloqueio/app) ou 'retry' (ocupado)."""
        post, _url, index = task
        if code == OK:
            total = data.get("items_total") if isinstance(data.get("items_total"), int) else 1
            self.totals[post] = max(1, min(50, total or 1))
            self.done[post] = sorted(set(self.done.get(post, [])) | {index})
            self.save()
            return "next"
        if code == BUSY:
            return "retry"
        if code == ERROR:
            self.errors[post] = (message or "erro")[:200]
            return "next"
        return "pause"
