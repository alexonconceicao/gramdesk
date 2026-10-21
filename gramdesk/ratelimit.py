"""Rate limiting da pesquisa de perfil: ritmo humano, bloqueios, resfriamento e cota diária.

Módulo puro (sem Qt, sem rede) para ser testado isoladamente.

- Pacer: decide quanto esperar antes de cada requisição (intervalo com jitter, pausas longas
  a cada N itens, teto por minuto e por hora) ou manda parar.
- classify_block: reconhece as respostas de limite/desafio/login do Instagram.
- RateState: estado persistente por conta (resfriamento exponencial, contadores do dia,
  janela da última hora), gravado com permissão 600.
- ResearchLog: registro local em JSON por linha, sem cookies, tokens nem URLs.

"Item" = uma requisição à API do Instagram (perfil, página de 12 posts, comentários de um
post, bandeja ou lote de destaques). Downloads de mídia vêm da CDN e têm ritmo próprio.
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

COOLDOWN_BASE = 30 * 60  # 1º bloqueio: 30 min
COOLDOWN_MAX = 24 * 3600  # teto: 24 h
HOUR = 3600.0
MINUTE = 60.0
MAX_WAIT_IN_JOB = 300.0  # espera maior que isso (teto por hora) encerra a coleta como parcial

BLOCK_KINDS = ("rate-limit", "checkpoint", "login")
BLOCK_LABEL = {
    "rate-limit": "O Instagram pediu para ir mais devagar (limite de requisições)",
    "checkpoint": "O Instagram pediu uma verificação de segurança (checkpoint)",
    "login": "O Instagram pediu login de novo",
}


# ---------------------------------------------------------------- limites
@dataclass
class Limits:
    daily_researches: int = 3  # pesquisas com acesso à rede por conta e por dia
    daily_items: int = 200  # requisições à API por conta e por dia
    per_hour: int = 60  # requisições por conta na última hora
    per_minute: int = 8  # requisições por conta no último minuto
    min_delay: float = 2.5  # intervalo mínimo entre requisições (s)
    max_delay: float = 6.0  # intervalo máximo (jitter uniforme entre os dois)
    long_every: int = 8  # a cada N requisições, uma pausa longa
    long_min: float = 20.0
    long_max: float = 45.0
    cache_hours: int = 24  # validade do cache de uma pesquisa
    # Navegação passo a passo (gramdesk-browse): uma ação por vez, na janela do app.
    browse_interval: float = 20.0  # intervalo mínimo entre ações de navegação (s)
    browse_jitter: float = 10.0  # acréscimo aleatório de 0 até este valor (s)
    browse_daily_posts: int = 15  # aberturas de post por conta e por dia

    RANGES = {
        "daily_researches": (1, 10),
        "daily_items": (30, 600),
        "per_hour": (10, 150),
        "per_minute": (2, 12),
        "min_delay": (2.0, 30.0),
        "max_delay": (2.5, 60.0),
        "long_every": (3, 30),
        "long_min": (5.0, 300.0),
        "long_max": (5.0, 600.0),
        "cache_hours": (1, 168),
        "browse_interval": (10.0, 300.0),
        "browse_jitter": (0.0, 120.0),
        "browse_daily_posts": (1, 100),
    }

    @classmethod
    def from_dict(cls, raw) -> "Limits":
        lim = cls()
        raw = raw if isinstance(raw, dict) else {}
        for f in fields(cls):
            v = raw.get(f.name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            lo, hi = cls.RANGES[f.name]
            v = max(lo, min(hi, v))
            setattr(lim, f.name, int(v) if isinstance(getattr(lim, f.name), int) else float(v))
        # intervalos coerentes: max sempre acima do min
        lim.max_delay = max(lim.max_delay, lim.min_delay + 0.5)
        lim.long_max = max(lim.long_max, lim.long_min)
        lim.per_minute = min(lim.per_minute, lim.per_hour)
        return lim

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- ritmo
@dataclass
class Decision:
    wait: float = 0.0  # segundos até poder fazer a próxima requisição
    stop: str = ""  # motivo para parar ("hora", "dia"); vazio = pode seguir


class Pacer:
    """Agenda as requisições de uma coleta (concorrência 1)."""

    def __init__(self, limits: Limits, recent=(), items_left: int | None = None, rng: random.Random | None = None):
        self.limits = limits
        self.recent = sorted(float(t) for t in recent)
        self.items_left = limits.daily_items if items_left is None else max(0, int(items_left))
        self.rng = rng or random.Random()
        self.count = 0  # requisições feitas nesta coleta
        self.last = self.recent[-1] if self.recent else 0.0

    def decide(self, now: float) -> Decision:
        lim = self.limits
        if self.count >= self.items_left:
            return Decision(stop="dia")
        gap = self.rng.uniform(lim.min_delay, lim.max_delay)
        if self.count and self.count % lim.long_every == 0:
            gap += self.rng.uniform(lim.long_min, lim.long_max)
        earliest = max(now, self.last + gap) if self.last else now + self.rng.uniform(0.5, 1.5)
        # Teto por minuto: o per_minute-ésimo mais recente precisa sair da janela.
        if len(self.recent) >= lim.per_minute:
            earliest = max(earliest, self.recent[-lim.per_minute] + MINUTE + self.rng.uniform(1, 4))
        if len(self.recent) >= lim.per_hour:
            earliest = max(earliest, self.recent[-lim.per_hour] + HOUR + 1)
        wait = max(0.0, earliest - now)
        if wait > MAX_WAIT_IN_JOB:
            return Decision(wait=wait, stop="hora")
        return Decision(wait=wait)

    def record(self, ts: float) -> None:
        self.count += 1
        self.last = ts
        self.recent.append(ts)
        cutoff = ts - HOUR
        self.recent = [t for t in self.recent if t > cutoff]


def download_gap(rng: random.Random | None = None) -> float:
    """Intervalo entre downloads de mídia (CDN), em segundos; downloads são um por vez."""
    return (rng or random).uniform(0.4, 1.2)


# ---------------------------------------------------------------- bloqueios
_RATE_TEXT = re.compile(
    r"please wait a few minutes|wait a few minutes before|try again later|tente novamente mais tarde|"
    r"aguarde alguns minutos|espere alguns minutos|rate.?limit|too many requests|feedback_required|"
    r"we restrict certain activity|restringimos determinadas atividades",
    re.I,
)
_CHECKPOINT = re.compile(r"checkpoint_required|challenge_required|/challenge/|/checkpoint/", re.I)
_LOGIN = re.compile(r"login_required|/accounts/login|not-logged-in|\"require_login\"\s*:\s*true", re.I)


def classify_block(status: int, url: str = "", text: str = "") -> str | None:
    """Tipo de bloqueio do Instagram (rate-limit, checkpoint, login) ou None se não for bloqueio."""
    url = url or ""
    text = (text or "")[:4000]
    if status == 429:
        return "rate-limit"
    if _CHECKPOINT.search(url) or _CHECKPOINT.search(text):
        return "checkpoint"
    if _RATE_TEXT.search(text):
        return "rate-limit"
    if _LOGIN.search(url) or _LOGIN.search(text) or status in (401, 403):
        return "login"
    return None


def cooldown_seconds(level: int) -> int:
    """30 min, 1 h, 2 h, 4 h, 8 h, 16 h, 24 h (teto)."""
    if level <= 0:
        return 0
    return int(min(COOLDOWN_MAX, COOLDOWN_BASE * 2 ** min(level - 1, 16)))


def day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def next_midnight(ts: float) -> float:
    t = time.localtime(ts)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 0, 0, 0, 0, 0, -1))


def hhmm(ts: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    fmt = "%H:%M" if day_key(ts) == day_key(now) else "%d/%m %H:%M"
    return time.strftime(fmt, time.localtime(ts))


def human_left(seconds: float) -> str:
    m = max(1, int(round(seconds / 60)))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- estado persistente
def _blank() -> dict:
    return {"level": 0, "cooldown_until": 0.0, "last_block": "", "last_block_at": 0.0,
            "day": "", "researches": 0, "items": 0, "recent": [],
            "browse_posts": 0, "browse_actions": 0, "browse_next": 0.0}


def _num(v, default=0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


class RateState:
    """ratelimit.json no diretório de dados do app (0600). Chave = id da conta."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 1, "accounts": {}}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        accounts = raw.get("accounts") if isinstance(raw, dict) else None
        self.data = {"version": 1, "accounts": {}}
        if isinstance(accounts, dict):
            for key, a in list(accounts.items())[:50]:
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or not isinstance(a, dict):
                    continue
                e = _blank()
                e["level"] = max(0, min(30, int(_num(a.get("level")))))
                e["cooldown_until"] = _num(a.get("cooldown_until"))
                e["last_block"] = a.get("last_block") if a.get("last_block") in BLOCK_KINDS else ""
                e["last_block_at"] = _num(a.get("last_block_at"))
                e["day"] = a.get("day") if isinstance(a.get("day"), str) and len(a["day"]) == 10 else ""
                e["researches"] = max(0, int(_num(a.get("researches"))))
                e["items"] = max(0, int(_num(a.get("items"))))
                rec = a.get("recent") if isinstance(a.get("recent"), list) else []
                e["recent"] = sorted(_num(t) for t in rec[-400:] if _num(t) > 0)
                e["browse_posts"] = max(0, int(_num(a.get("browse_posts"))))
                e["browse_actions"] = max(0, int(_num(a.get("browse_actions"))))
                e["browse_next"] = _num(a.get("browse_next"))
                self.data["accounts"][key] = e

    def save(self) -> None:
        _atomic_write(self.path, self.data)

    def _acc(self, acc: str, now: float) -> dict:
        e = self.data["accounts"].setdefault(acc, _blank())
        today = day_key(now)
        if e["day"] != today:
            e.update(day=today, researches=0, items=0, browse_posts=0, browse_actions=0)
        e["recent"] = [t for t in e["recent"] if t > now - HOUR]
        return e

    # ----- consultas
    def check(self, acc: str, limits: Limits, now: float | None = None) -> dict:
        """{'ok': bool, 'reason': cooldown|researches|items|'', 'until': ts, 'message': str}"""
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        if e["cooldown_until"] > now:
            label = BLOCK_LABEL.get(e["last_block"], "O Instagram limitou a conta")
            return {"ok": False, "reason": "cooldown", "until": e["cooldown_until"],
                    "message": f"{label}. Resfriamento ativo: aguarde até {hhmm(e['cooldown_until'], now)} "
                               f"(faltam {human_left(e['cooldown_until'] - now)})."}
        if e["researches"] >= limits.daily_researches:
            until = next_midnight(now)
            return {"ok": False, "reason": "researches", "until": until,
                    "message": f"Cota diária atingida ({e['researches']} de {limits.daily_researches} pesquisas). "
                               f"Aguarde até {hhmm(until, now)}."}
        if e["items"] >= limits.daily_items:
            until = next_midnight(now)
            return {"ok": False, "reason": "items", "until": until,
                    "message": f"Cota diária de itens atingida ({e['items']} de {limits.daily_items}). "
                               f"Aguarde até {hhmm(until, now)}."}
        return {"ok": True, "reason": "", "until": 0.0, "message": ""}

    def status(self, acc: str, limits: Limits, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        chk = self.check(acc, limits, now)
        return {
            **chk,
            "level": e["level"],
            "cooldown_until": e["cooldown_until"] if e["cooldown_until"] > now else 0.0,
            "last_block": e["last_block"],
            "last_block_at": e["last_block_at"],
            "researches_today": e["researches"],
            "items_today": e["items"],
            "items_last_hour": len(e["recent"]),
            "limits": limits.to_dict(),
        }

    def pacer(self, acc: str, limits: Limits, now: float | None = None, rng: random.Random | None = None) -> Pacer:
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        return Pacer(limits, e["recent"], limits.daily_items - e["items"], rng)

    # ----- registros
    def begin(self, acc: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._acc(acc, now)["researches"] += 1
        self.save()

    def record_request(self, acc: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        e["items"] += 1
        e["recent"].append(now)
        self.save()

    def record_block(self, acc: str, kind: str, now: float | None = None) -> float:
        """Sobe um nível de resfriamento e devolve o horário em que libera."""
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        e["level"] = min(30, e["level"] + 1)
        e["cooldown_until"] = max(e["cooldown_until"], now + cooldown_seconds(e["level"]))
        e["last_block"] = kind if kind in BLOCK_KINDS else "rate-limit"
        e["last_block_at"] = now
        self.save()
        return e["cooldown_until"]

    def record_success(self, acc: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        if e["level"] or e["cooldown_until"]:
            e["level"] = 0
            e["cooldown_until"] = 0.0
        self.save()


    # ----- navegação passo a passo
    def browse_check(self, acc: str, limits: Limits, now: float | None = None, *, post_open: bool = False) -> dict:
        """Pode fazer UMA ação de navegação agora? Mesmo formato de check() + 'wait' (s até o intervalo mínimo).

        Não olha a cota de pesquisas (são coisas diferentes), mas respeita resfriamento, itens do dia,
        teto por hora e por minuto e o teto diário de aberturas de post.
        """
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        base = {"ok": True, "reason": "", "until": 0.0, "message": "", "wait": max(0.0, e["browse_next"] - now)}
        if e["cooldown_until"] > now:
            label = BLOCK_LABEL.get(e["last_block"], "O Instagram limitou a conta")
            return {**base, "ok": False, "reason": "cooldown", "until": e["cooldown_until"],
                    "message": f"{label}. Resfriamento ativo: aguarde até {hhmm(e['cooldown_until'], now)} "
                               f"(faltam {human_left(e['cooldown_until'] - now)})."}
        until = next_midnight(now)
        if e["items"] >= limits.daily_items:
            return {**base, "ok": False, "reason": "items", "until": until,
                    "message": f"Cota diária de itens atingida ({e['items']} de {limits.daily_items}). "
                               f"Aguarde até {hhmm(until, now)}."}
        if post_open and e["browse_posts"] >= limits.browse_daily_posts:
            return {**base, "ok": False, "reason": "posts", "until": until,
                    "message": f"Teto diário de aberturas de post atingido ({e['browse_posts']} de "
                               f"{limits.browse_daily_posts}). Aguarde até {hhmm(until, now)}."}
        if len(e["recent"]) >= limits.per_hour:
            free = e["recent"][-limits.per_hour] + HOUR
            return {**base, "ok": False, "reason": "hour", "until": free,
                    "message": f"Teto de {limits.per_hour} ações por hora atingido. Aguarde até {hhmm(free, now)}."}
        last_min = [t for t in e["recent"] if t > now - MINUTE]
        if len(last_min) >= limits.per_minute:
            base["wait"] = max(base["wait"], last_min[-limits.per_minute] + MINUTE - now + 1)
        return base

    def record_browse(self, acc: str, limits: Limits, now: float | None = None, *, post_open: bool = False,
                      navigation: bool = True, rng: random.Random | None = None) -> float:
        """Conta a ação como 1 requisição e agenda a próxima ação de navegação. Devolve esse horário."""
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        e["items"] += 1
        e["recent"].append(now)
        e["browse_actions"] += 1
        if post_open:
            e["browse_posts"] += 1
        if navigation:
            gap = limits.browse_interval + (rng or random).uniform(0, limits.browse_jitter)
            e["browse_next"] = max(e["browse_next"], now + gap)
        self.save()
        return e["browse_next"]

    def browse_touch(self, acc: str, limits: Limits, now: float | None = None, rng: random.Random | None = None) -> float:
        """Ao terminar uma ação de navegação: o intervalo mínimo conta a partir do fim dela."""
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        gap = limits.browse_interval + (rng or random).uniform(0, limits.browse_jitter)
        e["browse_next"] = max(e["browse_next"], now + gap)
        self.save()
        return e["browse_next"]

    def browse_status(self, acc: str, limits: Limits, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        e = self._acc(acc, now)
        chk = self.browse_check(acc, limits, now)
        return {**chk, "posts_today": e["browse_posts"], "actions_today": e["browse_actions"],
                "posts_limit": limits.browse_daily_posts, "next_at": e["browse_next"] if e["browse_next"] > now else 0.0,
                "interval": limits.browse_interval, "jitter": limits.browse_jitter}


# ---------------------------------------------------------------- log
_LOG_FORBIDDEN = re.compile(r"cookie|token|session|csrf|url|auth|pass", re.I)


class ResearchLog:
    """research.log (JSON por linha, 0600, gira em 256 KB). Nunca grava cookies, tokens, URLs ou textos do site."""

    MAX_BYTES = 256 * 1024

    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def clean(fields_: dict) -> dict:
        out = {}
        for k, v in fields_.items():
            if not isinstance(k, str) or _LOG_FORBIDDEN.search(k):
                continue
            if isinstance(v, bool) or v is None or isinstance(v, int):
                out[k] = v
            elif isinstance(v, float):
                out[k] = round(v, 2)
            elif isinstance(v, str):
                if re.search(r"https?://", v):
                    continue
                out[k] = v[:120]
            elif isinstance(v, dict):
                out[k] = ResearchLog.clean(v)
        return out

    def write(self, event: str, **fields_) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event[:40], **self.clean(fields_)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
                os.replace(self.path, self.path.with_suffix(".log.1"))
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.chmod(self.path, 0o600)
        except OSError:
            pass
