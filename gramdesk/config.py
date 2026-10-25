"""Configuração persistente com validação estrita e gravação atômica."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import ratelimit, security

APP_NAME = "gramdesk"
APP_ID = "io.github.alexonconceicao.Gramdesk"
VERSION = "0.12.3"


IS_WINDOWS = os.name == "nt"


def _portable_home() -> Path | None:
    """GRAMDESK_HOME, ou um arquivo portable.txt ao lado do executável, guarda tudo numa pasta só."""
    env = os.environ.get("GRAMDESK_HOME", "")
    if env and os.path.isabs(env):
        return Path(env)
    import sys

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "portable.txt").exists():
            return exe_dir / "data"
    return None


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    base = Path(value) if value and os.path.isabs(value) else Path.home() / fallback
    return base / APP_NAME


def _dirs() -> tuple[Path, Path, Path]:
    home = _portable_home()
    if home is not None:
        return home / "config", home / "data", home / "cache"
    if IS_WINDOWS:
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return roaming / "Gramdesk", local / "Gramdesk" / "Data", local / "Gramdesk" / "Cache"
    return _xdg("XDG_CONFIG_HOME", ".config"), _xdg("XDG_DATA_HOME", ".local/share"), _xdg("XDG_CACHE_HOME", ".cache")


CONFIG_DIR, DATA_DIR, CACHE_DIR = _dirs()
CONFIG_FILE = CONFIG_DIR / "config.json"
RATE_FILE = DATA_DIR / "ratelimit.json"  # resfriamento e cota da pesquisa (0600)
RESEARCH_LOG = DATA_DIR / "research.log"  # registro local das coletas (0600)

HIBERNATE_MODES = ("discard", "freeze")
COLORS = 8
THEMES = ("system", "dark", "light")
ACCENTS = ("instagram", "violeta", "oceano", "esmeralda")
IDLE_LOCK = (0, 5, 15, 30, 60)
ZOOM_MIN, ZOOM_MAX = 50, 200


def clamp_zoom(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return 100
    return max(ZOOM_MIN, min(ZOOM_MAX, value))


def _color(value, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < COLORS else fallback
MAX_ACCOUNTS = 20


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def profile_dirs(account_id: str) -> tuple[Path, Path]:
    if not security.is_account_id(account_id):
        raise ValueError("id de conta inválido")
    return DATA_DIR / "profiles" / account_id, CACHE_DIR / "profiles" / account_id


@dataclass
class Account:
    id: str
    name: str
    ghost: bool = True
    color: int = 0
    zoom: int = 100
    keep_alive: bool = False

    @classmethod
    def new(cls, name: str, color: int = 0, ghost: bool = True) -> "Account":
        return cls(
            id=security.new_account_id(),
            name=security.clean_account_name(name),
            ghost=bool(ghost),
            color=_color(color, 0),
        )


@dataclass
class Config:
    accounts: list[Account] = field(default_factory=list)
    last_account: str = ""
    hibernate: str = "discard"
    block_third_party: bool = True
    block_telemetry: bool = True
    theme: str = "system"
    accent: str = "instagram"
    site_follows_theme: bool = True
    idle_lock: int = 0
    feed_buttons: bool = True
    # Fechar a janela manda o app para a bandeja (área de notificação) em vez de sair.
    tray: bool = True
    donation: dict = field(default_factory=dict)
    lock: dict | None = None
    pending_delete: list[str] = field(default_factory=list)
    research_limits: dict = field(default_factory=lambda: ratelimit.Limits().to_dict())

    def account(self, account_id: str) -> Account | None:
        return next((a for a in self.accounts if a.id == account_id), None)


def _parse(raw: dict) -> Config:
    cfg = Config()
    seen: set[str] = set()
    for item in raw.get("accounts", [])[:MAX_ACCOUNTS]:
        try:
            acc_id = item["id"]
            if not security.is_account_id(acc_id) or acc_id in seen:
                continue
            cfg.accounts.append(
                Account(
                    id=acc_id,
                    name=security.clean_account_name(item["name"]),
                    ghost=bool(item.get("ghost", True)),
                    color=_color(item.get("color"), len(cfg.accounts) % COLORS),
                    zoom=clamp_zoom(item.get("zoom", 100)),
                    keep_alive=bool(item.get("keep_alive", False)),
                )
            )
            seen.add(acc_id)
        except (KeyError, TypeError, ValueError):
            continue
    if raw.get("last_account") in seen:
        cfg.last_account = raw["last_account"]
    if raw.get("hibernate") in HIBERNATE_MODES:
        cfg.hibernate = raw["hibernate"]
    cfg.block_third_party = bool(raw.get("block_third_party", True))
    cfg.block_telemetry = bool(raw.get("block_telemetry", True))
    if raw.get("theme") in THEMES:
        cfg.theme = raw["theme"]
    if raw.get("accent") in ACCENTS:
        cfg.accent = raw["accent"]
    cfg.site_follows_theme = bool(raw.get("site_follows_theme", True))
    cfg.feed_buttons = bool(raw.get("feed_buttons", True))
    cfg.tray = bool(raw.get("tray", True))
    don = raw.get("donation") if isinstance(raw.get("donation"), dict) else {}
    cfg.donation = {
        k: v for k, v in {
            "first_seen": don.get("first_seen"),
            "last_shown": don.get("last_shown"),
            "never": don.get("never"),
        }.items()
        if (k == "never" and isinstance(v, bool)) or (k != "never" and isinstance(v, (int, float)) and not isinstance(v, bool))
    }
    if raw.get("idle_lock") in IDLE_LOCK:
        cfg.idle_lock = raw["idle_lock"]
    lock = raw.get("lock")
    if isinstance(lock, dict) and lock.get("alg") == "scrypt" and isinstance(lock.get("salt"), str) and isinstance(lock.get("hash"), str):
        cfg.lock = {"alg": "scrypt", "salt": lock["salt"], "hash": lock["hash"]}
    cfg.pending_delete = [i for i in raw.get("pending_delete", []) if security.is_account_id(i) and i not in seen]
    cfg.research_limits = ratelimit.Limits.from_dict(raw.get("research_limits")).to_dict()
    return cfg


def load() -> Config:
    private_dir(CONFIG_DIR)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError):
        # Arquivo corrompido: preserva para análise e começa limpo.
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE.with_suffix(".json.bad"))
        except OSError:
            pass
        return Config()
    return _parse(raw)


def save(cfg: Config) -> None:
    private_dir(CONFIG_DIR)
    data = {
        "accounts": [{
                "id": a.id,
                "name": a.name,
                "ghost": a.ghost,
                "color": a.color,
                "zoom": a.zoom,
                "keep_alive": a.keep_alive,
            } for a in cfg.accounts],
        "last_account": cfg.last_account,
        "hibernate": cfg.hibernate,
        "block_third_party": cfg.block_third_party,
        "block_telemetry": cfg.block_telemetry,
        "theme": cfg.theme,
        "accent": cfg.accent,
        "site_follows_theme": cfg.site_follows_theme,
        "idle_lock": cfg.idle_lock,
        "feed_buttons": cfg.feed_buttons,
        "tray": cfg.tray,
        "donation": cfg.donation,
        "lock": cfg.lock,
        "pending_delete": cfg.pending_delete,
        "research_limits": ratelimit.Limits.from_dict(cfg.research_limits).to_dict(),
    }
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def purge_pending(cfg: Config) -> None:
    """Apaga dados de contas removidas (feito na inicialização, antes de abrir perfis)."""
    remaining = []
    for acc_id in cfg.pending_delete:
        ok = True
        # O Qt também cria um diretório padrão vazio com o nome do perfil.
        for d in (*profile_dirs(acc_id), DATA_DIR / "QtWebEngine" / f"gd-{acc_id}"):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                ok = ok and not d.exists()
        if not ok:
            remaining.append(acc_id)
    if remaining != cfg.pending_delete:
        cfg.pending_delete = remaining
        save(cfg)
