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
    "removi-seguidor", "importacao-meta", "tarefa-erro", "acao-recusada",
)
HISTORY_MAX = 2000
ACTIONS = ("unfollow", "remove")


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
                "p": bool(raw.get("is_private")),
                "a": bool(raw.get("anon_pic") or raw.get("has_anonymous_profile_picture"))}


def _users(raw) -> dict:
    out = {}
    if isinstance(raw, dict):
        for pk, e in list(raw.items())[:MAX_USERS]:
            if PK_RE.match(str(pk)) and isinstance(e, dict) and USERNAME_RE.match(str(e.get("u", ""))):
                out[str(pk)] = {"u": e["u"], "n": _text(e.get("n"), 120), "v": bool(e.get("v")), "p": bool(e.get("p")),
                                "a": bool(e.get("a"))}
    return out


def diff(prev: dict, new: dict) -> tuple[list[str], list[str]]:
    """(pks que saíram, pks que entraram) entre duas fotos de seguidores."""
    return sorted(set(prev) - set(new)), sorted(set(new) - set(prev))


def is_famous(count: int | None, threshold: int) -> bool | None:
    return None if count is None else count >= threshold


def famous(u: dict, threshold: int, verified: bool = True) -> bool:
    """Famoso pela própria lista (selo de verificado) ou pela contagem de seguidores, se já buscada."""
    if verified and u.get("v"):
        return True
    return bool(is_famous(u.get("count"), threshold))


_KEYBOARD = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
GHOST_LEVELS = ((65, "bot"), (45, "inativo"), (25, "suspeito"))


def ghost_score(u: dict) -> tuple[int, str, list[str]]:
    """Pontuação 0-100 de conta fantasma/bot, só com o que veio na lista (sem requisição a mais)."""
    name = u.get("u", "").lower()
    full = (u.get("n") or "").strip()
    score, why = 0, []
    if u.get("a"):
        score += 15
        why.append("sem foto de perfil")
    if re.search(r"\d{6,}", name):
        score += 20
        why.append("sequência longa de números")
    if re.fullmatch(r"[a-z]{2,6}\d{4,}", name):
        score += 25
        why.append("padrão letras + números")
    if any(row[i:i + 4] in name for row in _KEYBOARD for i in range(len(row) - 3)):
        score += 25
        why.append("letras seguidas do teclado")
    if not full:
        score += 10
        why.append("sem nome")
    elif full.lower() == name:
        score += 10
        why.append("nome igual ao @")
    if u.get("v"):
        score, why = 0, []
    score = min(100, score)
    level = next((lv for lim, lv in GHOST_LEVELS if score >= lim), "")
    return score, level, why


# ---------------------------------------------------------------- exportação oficial da Meta
_META_LINK = re.compile(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]{1,30})/?(?=[\"'?#<\s])")
_META_RESERVED = {"about", "accounts", "direct", "emails", "explore", "legal", "p", "privacy", "reel", "reels",
                  "stories", "www", "developer", "web"}
META_FILES = {
    "followers": ("followers_",),
    "following": ("following.",),
    "pending": ("pending_follow_requests",),
    "unfollowed": ("recently_unfollowed",),
}


def _meta_users_html(text: str) -> list[str]:
    return [m.group(1).lower() for m in _META_LINK.finditer(text) if m.group(1).lower() not in _META_RESERVED]


def _meta_users_json(data) -> list[str]:
    out = []

    def walk(o, depth=0):
        if depth > 12:
            return
        if isinstance(o, dict):
            for k in ("value", "title"):
                v = o.get(k)
                if isinstance(v, str) and USERNAME_RE.match(v.strip().lstrip("@")):
                    out.append(v.strip().lstrip("@").lower())
                    return
            href = o.get("href")
            if isinstance(href, str):
                out.extend(_meta_users_html(href + "/ "))
                return
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)

    walk(data)
    return out


def parse_meta_files(files: dict[str, str]) -> dict[str, list[str]]:
    """{nome do arquivo: conteúdo} da exportação da Meta (HTML ou JSON) -> listas de @ por categoria."""
    lists: dict[str, list[str]] = {k: [] for k in META_FILES}
    for name, text in files.items():
        base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        kind = next((k for k, prefixes in META_FILES.items() if any(base.startswith(p) for p in prefixes)), None)
        if kind is None or not base.endswith((".html", ".json")):
            continue
        if base.endswith(".json"):
            try:
                users = _meta_users_json(json.loads(text))
            except ValueError:
                users = []
        else:
            users = _meta_users_html(text)
        lists[kind].extend(users)
    return {k: sorted(set(v)) for k, v in lists.items()}


def read_meta_export(path: Path) -> dict[str, list[str]]:
    """Pasta ou .zip da exportação "Baixar suas informações" do Instagram. Só lê; nada vai para a rede."""
    import zipfile

    path = Path(path)
    wanted = tuple(p for prefixes in META_FILES.values() for p in prefixes)
    files: dict[str, str] = {}
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist()[:20000]:
                base = info.filename.rsplit("/", 1)[-1].lower()
                if base.startswith(wanted) and info.file_size < 50_000_000:
                    files[info.filename] = z.read(info).decode("utf-8", "replace")
    elif path.is_dir():
        for f in list(path.rglob("*"))[:50000]:
            if f.is_file() and not f.is_symlink() and f.name.lower().startswith(wanted) and f.stat().st_size < 50_000_000:
                files[str(f)] = f.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError("escolha a pasta ou o .zip da exportação")
    out = parse_meta_files(files)
    if not out["followers"] and not out["following"]:
        raise ValueError("não achei followers_1 nem following na exportação")
    return out


def meta_analysis(lists: dict[str, list[str]]) -> dict[str, list[str]]:
    followers, following = set(lists.get("followers", [])), set(lists.get("following", []))
    return {"nao-seguidores": sorted(following - followers), "mutuos": sorted(following & followers),
            "fas": sorted(followers - following), "pendentes": list(lists.get("pending", [])),
            "deixei-de-seguir": list(lists.get("unfollowed", []))}


def meta_diff(old: dict, new: dict) -> dict[str, list[str]]:
    """Entre duas exportações: quem deixou de seguir, novos seguidores, quem você passou a seguir ou deixou."""
    of, nf = set(old.get("followers", [])), set(new.get("followers", []))
    og, ng = set(old.get("following", [])), set(new.get("following", []))
    return {"sairam": sorted(of - nf), "entraram": sorted(nf - of), "comecei": sorted(ng - og),
            "parei": sorted(og - ng), "novos-mutuos": sorted((nf & ng) - (of & og))}


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
        self.selected_remove = {str(p) for p in (raw.get("selected_remove") if isinstance(raw.get("selected_remove"), list)
                                                 else []) if PK_RE.match(str(p))}
        self.removed_by_me = {str(p) for p in (raw.get("removed_by_me") if isinstance(raw.get("removed_by_me"), list)
                                               else []) if PK_RE.match(str(p))}
        hist = raw.get("history") if isinstance(raw.get("history"), list) else []
        self.history = [[_num(h[0]), int(_num(h[1])), int(_num(h[2]))] for h in hist[-HISTORY_MAX:]
                        if isinstance(h, list) and len(h) == 3]
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
            "selected_remove": sorted(self.selected_remove & set(self.followers)),
            "removed_by_me": sorted(self.removed_by_me), "history": self.history[-HISTORY_MAX:],
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
        mine = [pk for pk in lost if pk in self.removed_by_me]  # removidos pelo app não "deixaram de seguir"
        lost = [pk for pk in lost if pk not in self.removed_by_me]
        self.removed_by_me -= set(mine) | set(p["followers"])
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
        self.history.append([now, len(self.followers), len(self.following)])
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
            out.append(self._row(pk, e))
        out.sort(key=lambda x: x["u"].lower())
        return out

    def _row(self, pk: str, e: dict) -> dict:
        c = self.counts.get(pk)
        u = {"pk": pk, **e, "count": c[0] if c else None, "keep": pk in self.keep}
        u["ghost"], u["ghost_level"], u["ghost_why"] = ghost_score(u)
        return u

    def mutuals(self) -> list[dict]:
        return sorted((self._row(pk, e) for pk, e in self.following.items() if pk in self.followers),
                      key=lambda x: x["u"].lower())

    def fans(self) -> list[dict]:
        """Seguem você e você não segue de volta."""
        return sorted((self._row(pk, e) for pk, e in self.followers.items() if pk not in self.following),
                      key=lambda x: x["u"].lower())

    def split(self, threshold: int, verified: bool = True) -> dict[str, list[dict]]:
        """Não seguidores em comuns e famosos (verificado ou contagem >= limite); exclui a lista "manter"."""
        groups = {"comuns": [], "famosos": []}
        for u in self.non_followers():
            if not u["keep"]:
                groups["famosos" if famous(u, threshold, verified) else "comuns"].append(u)
        return groups

    def need_counts(self, now: float, limit: int = 1000, verified: bool = True) -> list[dict]:
        """Refinamento opcional: não seguidores sem contagem recente (verificados já contam como famosos)."""
        out = [u for u in self.non_followers()
               if not u["keep"] and not (verified and u["v"])
               and (u["pk"] not in self.counts or now - self.counts[u["pk"]][1] > COUNT_MAX_AGE)]
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

    def set_selected(self, pks, on: bool, action: str = "unfollow") -> None:
        """Seleção para o lote (fica salva entre abas e sessões). Mantidos ficam fora.
        unfollow: só quem você segue; remove (remover seguidor): só quem segue você."""
        target, pool = (self.selected, self.following) if action == "unfollow" else (self.selected_remove, self.followers)
        for pk in pks:
            if on and pk in pool and pk not in self.keep:
                target.add(pk)
            elif not on:
                target.discard(pk)
        self.save()

    def record_removed(self, pk: str, now: float) -> None:
        """Seguidor removido pelo app (sem bloquear): sai da lista de seguidores sem contar como "deixou de seguir"."""
        e = self.followers.pop(pk, None)
        self.selected_remove.discard(pk)
        self.removed_by_me.add(pk)
        self.save()
        self.audit("removi-seguidor", now, pk=pk, user=(e or {}).get("u", ""))

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
        remove = [str(p) for p in (raw.get("remove") if isinstance(raw.get("remove"), list) else []) if str(p) in items]
        return {"mode": raw["mode"], "items": items[:5000], "done": done[:5000], "errors": errors, "remove": remove,
                "created_at": _num(raw.get("created_at")), "threshold": max(0, int(_num(raw.get("threshold"))))}

    def new_batch(self, mode: str, pks: list[str], threshold: int, now: float) -> dict:
        if mode not in MODES:
            raise ValueError("modo inválido")
        valid = [p for p in dict.fromkeys(pks) if PK_RE.match(p) and p in self.following and p not in self.keep]
        batch = {"mode": mode, "items": valid[:5000], "done": [], "errors": {}, "remove": [], "created_at": now,
                 "threshold": threshold}
        _atomic_write(self.batch_path, batch)
        self.audit("lote-inicio", now, mode=mode, total=len(batch["items"]), threshold=threshold)
        return batch

    def extend_batch(self, batch: dict, pks, action: str = "unfollow") -> int:
        """Acrescenta ao lote (deixar de seguir quem você segue, ou remover quem segue você), fora "manter"
        e quem já está no lote. Devolve quantos entraram."""
        pool = self.following if action == "unfollow" else self.followers
        batch.setdefault("remove", [])
        have = set(batch["items"])
        added = 0
        for pk in dict.fromkeys(pks):
            if PK_RE.match(str(pk)) and pk in pool and pk not in self.keep and pk not in have:
                if len(batch["items"]) >= 5000:
                    break
                batch["items"].append(pk)
                if action == "remove":
                    batch["remove"].append(pk)
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
            if pk in batch.get("remove", []):
                batch["remove"].remove(pk)
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
        remove = set(batch.get("remove", []))
        for pk in batch["items"]:
            pool = self.followers if pk in remove else self.following
            if pk in done or pk in batch["errors"] or pk in self.keep or pk not in pool:
                continue
            return pk
        return None

    # ---------- exportação da Meta (importada) ----------
    def save_meta(self, lists: dict, now: float) -> dict | None:
        """Guarda a importação (só @) e devolve a anterior para comparar."""
        prev = self.load_meta()
        rec = {"at": now, **{k: [x for x in v if USERNAME_RE.match(x)][:MAX_USERS] for k, v in lists.items()
                             if k in META_FILES}}
        if prev:
            _atomic_write(self.dir / "meta-prev.json", prev)
        _atomic_write(self.dir / "meta.json", rec)
        self.audit("importacao-meta", now, followers=len(rec.get("followers", [])),
                   following=len(rec.get("following", [])), pending=len(rec.get("pending", [])))
        return prev

    def load_meta(self, prev: bool = False) -> dict | None:
        raw = _read(self.dir / ("meta-prev.json" if prev else "meta.json"))
        if not isinstance(raw, dict):
            return None
        out = {"at": _num(raw.get("at"))}
        for k in META_FILES:
            v = raw.get(k)
            out[k] = [x for x in v if isinstance(x, str) and USERNAME_RE.match(x)] if isinstance(v, list) else []
        return out

    def pks_by_username(self, names, pool: str = "following") -> list[str]:
        idx = {e["u"].lower(): pk for pk, e in (self.following if pool == "following" else self.followers).items()}
        return [idx[n] for n in names if n in idx]

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
