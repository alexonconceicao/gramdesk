"""Seguidores: fotos (snapshots) de quem segue e de quem a conta segue, não seguidores, famosos,
lista permanente de quem deixou de seguir, lote de remoções e auditoria.

Módulo puro (sem Qt, sem rede). Tudo fica em <DATA_DIR>/follows/<conta>/, com permissão 600:
  state.json         última foto completa, foto parcial em andamento, contagens de seguidores, lista "manter"
  unfollowers.json   lista permanente de quem deixou de seguir (nunca é apagada pelo app)
  batch.json         lote de remoções em andamento (retomável)
  audit.jsonl        auditoria: uma linha JSON por evento
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

PK_RE = re.compile(r"^\d{1,25}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
CURSOR_RE = re.compile(r"^[A-Za-z0-9_=|:.-]{0,300}$")
KINDS = ("followers", "following")
MODES = ("nao-seguidores", "famosos", "selecao")
MAX_USERS = 50_000
COUNT_MAX_AGE = 30 * 86400  # contagem de seguidores de um perfil vale 30 dias
AUDIT_MAX_BYTES = 2 * 1024 * 1024
AUDIT_EVENTS = (
    "checagem", "checagem-parcial", "deixou-de-seguir", "voltou-a-seguir", "deixei-de-seguir",
    "lote-inicio", "lote-pausa", "lote-fim", "contagem", "bloqueio", "manter", "desmarcar-manter", "desacelerar",
)


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _num(v, default=0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _text(v, n: int) -> str:
    return v[:n] if isinstance(v, str) else ""


def user_entry(raw) -> tuple[str, dict] | None:
    """Usuário da API (lista de seguidores/seguindo) -> (pk, {u, n, v, p}). Inválido -> None."""
    if not isinstance(raw, dict):
        return None
    pk = str(raw.get("pk") or raw.get("pk_id") or raw.get("id") or "")
    username = raw.get("username")
    if not PK_RE.match(pk) or not isinstance(username, str) or not USERNAME_RE.match(username):
        return None
    return pk, {"u": username, "n": _text(raw.get("full_name"), 120), "v": bool(raw.get("is_verified")),
                "p": bool(raw.get("is_private"))}


def _users(raw) -> dict:
    out = {}
    if isinstance(raw, dict):
        for pk, e in list(raw.items())[:MAX_USERS]:
            if PK_RE.match(str(pk)) and isinstance(e, dict) and USERNAME_RE.match(str(e.get("u", ""))):
                out[str(pk)] = {"u": e["u"], "n": _text(e.get("n"), 120), "v": bool(e.get("v")), "p": bool(e.get("p"))}
    return out


def diff(prev: dict, new: dict) -> tuple[list[str], list[str]]:
    """(pks que saíram, pks que entraram) entre duas fotos de seguidores."""
    return sorted(set(prev) - set(new)), sorted(set(new) - set(prev))


def is_famous(count: int | None, threshold: int) -> bool | None:
    return None if count is None else count >= threshold


class FollowStore:
    def __init__(self, root: Path, account_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", account_id or ""):
            raise ValueError("conta inválida")
        self.dir = Path(root) / account_id
        self.state_path = self.dir / "state.json"
        self.unf_path = self.dir / "unfollowers.json"
        self.batch_path = self.dir / "batch.json"
        self.audit_path = self.dir / "audit.jsonl"
        self.load()

    # ---------- leitura ----------
    def load(self) -> None:
        raw = _read(self.state_path)
        raw = raw if isinstance(raw, dict) else {}
        self.followers = _users(raw.get("followers"))
        self.following = _users(raw.get("following"))
        self.taken_at = _num(raw.get("taken_at"))
        self.checks = max(0, int(_num(raw.get("checks"))))
        self.counts: dict[str, list] = {}
        for pk, c in (raw.get("counts") if isinstance(raw.get("counts"), dict) else {}).items():
            if PK_RE.match(str(pk)) and isinstance(c, list) and len(c) == 2:
                self.counts[str(pk)] = [max(0, int(_num(c[0]))), _num(c[1])]
        self.keep = {str(p) for p in (raw.get("keep") if isinstance(raw.get("keep"), list) else []) if PK_RE.match(str(p))}
        self.selected = {str(p) for p in (raw.get("selected") if isinstance(raw.get("selected"), list) else [])
                         if PK_RE.match(str(p))}
        self.partial = self._parse_partial(raw.get("partial"))
        unf = _read(self.unf_path)
        self.unfollowers = [self._parse_unf(e) for e in (unf if isinstance(unf, list) else [])]
        self.unfollowers = [e for e in self.unfollowers if e]

    @staticmethod
    def _parse_partial(p) -> dict | None:
        if not isinstance(p, dict) or p.get("kind") not in KINDS:
            return None
        cur = str(p.get("cursor") or "")
        return {
            "kind": p["kind"],
            "cursor": cur if CURSOR_RE.match(cur) else "",
            "followers": _users(p.get("followers")),
            "following": _users(p.get("following")),
            "started_at": _num(p.get("started_at")),
        }

    @staticmethod
    def _parse_unf(e) -> dict | None:
        if not isinstance(e, dict) or not PK_RE.match(str(e.get("pk", ""))) or not USERNAME_RE.match(str(e.get("u", ""))):
            return None
        return {"pk": str(e["pk"]), "u": e["u"], "n": _text(e.get("n"), 120), "lost_at": _num(e.get("lost_at")),
                "back_at": _num(e.get("back_at")), "times": max(1, int(_num(e.get("times"), 1))),
                "i_follow": bool(e.get("i_follow"))}

    def save(self) -> None:
        _atomic_write(self.state_path, {
            "version": 1, "followers": self.followers, "following": self.following, "taken_at": self.taken_at,
            "checks": self.checks, "counts": self.counts, "keep": sorted(self.keep), "partial": self.partial,
            "selected": sorted(self.selected & set(self.following)),
        })

    def _save_unf(self) -> None:
        _atomic_write(self.unf_path, self.unfollowers)

    @property
    def has_snapshot(self) -> bool:
        return self.taken_at > 0

    # ---------- foto (snapshot) ----------
    def begin_partial(self, now: float) -> dict:
        if self.partial is None:
            self.partial = {"kind": "followers", "cursor": "", "followers": {}, "following": {}, "started_at": now}
        return self.partial

    def add_page(self, kind: str, users: list, cursor: str, more: bool) -> int:
        """Acrescenta uma página da lista à foto parcial. Devolve quantos usuários entraram."""
        p = self.partial
        if p is None or kind != p["kind"]:
            return 0
        bucket = p[kind]
        n = 0
        for raw in users[:500]:
            e = user_entry(raw)
            if e and len(bucket) < MAX_USERS and e[0] not in bucket:
                bucket[e[0]] = e[1]
                n += 1
        cursor = str(cursor or "")
        if more and cursor and CURSOR_RE.match(cursor):
            p["cursor"] = cursor
        elif kind == "followers":
            p.update(kind="following", cursor="")
        else:
            p.update(kind="done", cursor="")
        return n

    @property
    def partial_done(self) -> bool:
        return self.partial is not None and self.partial.get("kind") == "done"

    def finish_snapshot(self, now: float) -> dict:
        """Fecha a foto parcial completa: compara com a anterior, atualiza a lista permanente e a auditoria."""
        p = self.partial
        if p is None or p.get("kind") != "done":
            raise ValueError("foto incompleta")
        first = not self.has_snapshot
        lost, gained = diff(self.followers, p["followers"]) if not first else ([], [])
        new_following = p["following"]
        by_pk = {e["pk"]: e for e in self.unfollowers}
        for pk in lost:
            old = self.followers[pk]
            e = by_pk.get(pk)
            if e is None:
                e = {"pk": pk, "u": old["u"], "n": old["n"], "lost_at": now, "back_at": 0.0, "times": 1, "i_follow": False}
                self.unfollowers.append(e)
                by_pk[pk] = e
            else:
                e.update(u=old["u"], n=old["n"], lost_at=now, back_at=0.0, times=e["times"] + 1)
            e["i_follow"] = pk in new_following
            self.audit("deixou-de-seguir", now, pk=pk, user=old["u"], name=old["n"], i_follow=e["i_follow"])
        for pk in gained:
            e = by_pk.get(pk)
            if e is not None and not e["back_at"]:
                e["back_at"] = now
                self.audit("voltou-a-seguir", now, pk=pk, user=p["followers"][pk]["u"])
        self.followers = p["followers"]
        self.following = new_following
        self.taken_at = now
        self.checks += 1
        self.partial = None
        self.save()
        self._save_unf()
        summary = {"followers": len(self.followers), "following": len(self.following), "lost": len(lost),
                   "gained": len(gained), "first": first}
        self.audit("checagem", now, **summary)
        return summary

    def drop_partial(self) -> None:
        self.partial = None
        self.save()

    # ---------- análises ----------
    def non_followers(self) -> list[dict]:
        """Quem a conta segue e não segue de volta (pela última foto completa), em ordem alfabética."""
        out = []
        for pk, e in self.following.items():
            if pk in self.followers:
                continue
            c = self.counts.get(pk)
            out.append({"pk": pk, **e, "count": c[0] if c else None, "keep": pk in self.keep})
        out.sort(key=lambda x: x["u"].lower())
        return out

    def split(self, threshold: int) -> dict[str, list[dict]]:
        """Não seguidores separados em comuns, famosos e sem contagem (exclui a lista "manter")."""
        groups = {"comuns": [], "famosos": [], "sem-contagem": []}
        for u in self.non_followers():
            if u["keep"]:
                continue
            f = is_famous(u["count"], threshold)
            groups["sem-contagem" if f is None else ("famosos" if f else "comuns")].append(u)
        return groups

    def need_counts(self, now: float, limit: int = 1000) -> list[dict]:
        """Não seguidores sem contagem recente de seguidores (para classificar famosos)."""
        out = [u for u in self.non_followers()
               if not u["keep"] and (u["pk"] not in self.counts or now - self.counts[u["pk"]][1] > COUNT_MAX_AGE)]
        return out[:limit]

    def set_count(self, pk: str, count: int, now: float) -> None:
        if PK_RE.match(pk) and isinstance(count, int) and count >= 0:
            self.counts[pk] = [count, now]

    def set_keep(self, pk: str, keep: bool, now: float) -> None:
        if not PK_RE.match(pk):
            return
        user = (self.following.get(pk) or {}).get("u", "")
        if keep and pk not in self.keep:
            self.keep.add(pk)
            self.audit("manter", now, pk=pk, user=user)
        elif not keep and pk in self.keep:
            self.keep.discard(pk)
            self.audit("desmarcar-manter", now, pk=pk, user=user)
        self.save()

    def set_keep_many(self, pks, keep: bool, now: float) -> int:
        """Marca/desmarca vários como "manter" (whitelist). Mantidos saem da seleção. Devolve quantos mudaram."""
        n = 0
        for pk in pks:
            if not PK_RE.match(str(pk)) or (pk in self.keep) == keep:
                continue
            user = (self.following.get(pk) or {}).get("u", "")
            if keep:
                self.keep.add(pk)
                self.selected.discard(pk)
            else:
                self.keep.discard(pk)
            self.audit("manter" if keep else "desmarcar-manter", now, pk=pk, user=user)
            n += 1
        if n:
            self.save()
        return n

    def set_selected(self, pks, on: bool) -> None:
        """Seleção para o lote (fica salva entre abas e sessões). Mantidos e quem já não é seguido ficam fora."""
        for pk in pks:
            if on and pk in self.following and pk not in self.keep:
                self.selected.add(pk)
            elif not on:
                self.selected.discard(pk)
        self.save()

    def record_unfollowed(self, pk: str, now: float, mode: str = "") -> None:
        """Remoção feita pelo app: tira da lista de seguindo e registra na auditoria."""
        e = self.following.pop(pk, None)
        self.selected.discard(pk)
        for u in self.unfollowers:
            if u["pk"] == pk:
                u["i_follow"] = False
        self.save()
        self._save_unf()
        self.audit("deixei-de-seguir", now, pk=pk, user=(e or {}).get("u", ""), mode=mode,
                   count=(self.counts.get(pk) or [None])[0])

    # ---------- lote de remoções ----------
    def load_batch(self) -> dict | None:
        raw = _read(self.batch_path)
        if not isinstance(raw, dict) or raw.get("mode") not in MODES:
            return None
        items = [str(p) for p in (raw.get("items") if isinstance(raw.get("items"), list) else []) if PK_RE.match(str(p))]
        done = [str(p) for p in (raw.get("done") if isinstance(raw.get("done"), list) else []) if PK_RE.match(str(p))]
        errors = {str(k): _text(v, 160) for k, v in (raw.get("errors") if isinstance(raw.get("errors"), dict) else {}).items()
                  if PK_RE.match(str(k))}
        return {"mode": raw["mode"], "items": items[:5000], "done": done[:5000], "errors": errors,
                "created_at": _num(raw.get("created_at")), "threshold": max(0, int(_num(raw.get("threshold"))))}

    def new_batch(self, mode: str, pks: list[str], threshold: int, now: float) -> dict:
        if mode not in MODES:
            raise ValueError("modo inválido")
        valid = [p for p in dict.fromkeys(pks) if PK_RE.match(p) and p in self.following and p not in self.keep]
        batch = {"mode": mode, "items": valid[:5000], "done": [], "errors": {}, "created_at": now, "threshold": threshold}
        _atomic_write(self.batch_path, batch)
        self.audit("lote-inicio", now, mode=mode, total=len(batch["items"]), threshold=threshold)
        return batch

    def extend_batch(self, batch: dict, pks) -> int:
        """Acrescenta ao lote quem ainda é seguido, não está em "manter" nem já no lote. Devolve quantos entraram."""
        have = set(batch["items"])
        added = 0
        for pk in dict.fromkeys(pks):
            if PK_RE.match(str(pk)) and pk in self.following and pk not in self.keep and pk not in have:
                if len(batch["items"]) >= 5000:
                    break
                batch["items"].append(pk)
                batch["errors"].pop(pk, None)
                have.add(pk)
                added += 1
        if added:
            self.save_batch(batch)
            self.audit("lote-inicio", mode="selecao", total=added)
        return added

    def remove_from_batch(self, batch: dict, pk: str) -> bool:
        if pk in batch["items"] and pk not in batch["done"]:
            batch["items"].remove(pk)
            batch["errors"].pop(pk, None)
            self.save_batch(batch)
            return True
        return False

    @staticmethod
    def batch_status(batch: dict | None, pk: str) -> str:
        """'' (fora), 'pendente', 'removido' ou 'erro'."""
        if not batch or pk not in batch["items"]:
            return ""
        if pk in batch["done"]:
            return "removido"
        if pk in batch["errors"]:
            return "erro"
        return "pendente"

    def save_batch(self, batch: dict) -> None:
        _atomic_write(self.batch_path, batch)

    def clear_batch(self) -> None:
        try:
            self.batch_path.unlink()
        except OSError:
            pass

    def next_in_batch(self, batch: dict) -> str | None:
        """Próximo pk a remover: pula feitos, com erro, marcados como "manter" e quem já não é seguido."""
        done = set(batch["done"])
        for pk in batch["items"]:
            if pk in done or pk in batch["errors"] or pk in self.keep or pk not in self.following:
                continue
            return pk
        return None

    # ---------- auditoria ----------
    def audit(self, event: str, now: float | None = None, **fields_) -> None:
        if event not in AUDIT_EVENTS:
            return
        now = time.time() if now is None else now
        rec = {"ts": round(now, 1), "event": event}
        for k, v in fields_.items():
            if isinstance(v, bool) or v is None or isinstance(v, int):
                rec[k] = v
            elif isinstance(v, float):
                rec[k] = round(v, 2)
            elif isinstance(v, str) and not re.search(r"https?://", v):
                rec[k] = v[:160]
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700)
            if self.audit_path.exists() and self.audit_path.stat().st_size > AUDIT_MAX_BYTES:
                # Gira sem perder histórico: arquivos antigos ficam como audit-<data>.jsonl.
                os.replace(self.audit_path, self.dir / time.strftime("audit-%Y%m%d-%H%M%S.jsonl", time.localtime(now)))
            fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def read_audit(self, limit: int = 500) -> list[dict]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("event") in AUDIT_EVENTS:
                out.append(rec)
        return out

    def check_due(self, now: float, hours: float = 20.0) -> bool:
        return self.has_snapshot and now - self.taken_at >= hours * 3600
