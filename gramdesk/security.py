"""Regras de segurança puras (sem Qt), testáveis isoladamente.

Tudo que decide o que pode carregar, o que pode ser baixado e como nomes
de arquivo são gerados passa por aqui.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from urllib.parse import urlsplit

# Hosts onde a navegação principal pode acontecer dentro do app.
NAV_HOSTS = ("instagram.com", "facebook.com")

# Hosts de onde subrecursos (scripts, imagens, XHR, websockets) podem vir.
RESOURCE_HOSTS = (
    "instagram.com",
    "cdninstagram.com",
    "fbcdn.net",
    "facebook.com",
    "fbsbx.com",
)

# reCAPTCHA aparece em desafios de login; liberado só nesse caminho.
CAPTCHA_HOSTS = ("www.google.com", "www.gstatic.com", "www.recaptcha.net")

# Hosts aceitos como origem de mídia para download.
MEDIA_HOSTS = ("cdninstagram.com", "fbcdn.net")

MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4", ".m4a"}

# Telemetria do próprio Instagram/Meta. Lista conservadora: só caminhos de log.
TELEMETRY_RE = re.compile(
    r"/logging_client_events|/ajax/bz|/ajax/logging/|^/tr/?$",
    re.I,
)

# Endpoint REST que marca story como visto (o GraphQL é tratado no JS injetado).
STORY_SEEN_RE = re.compile(r"/stories/reel/seen|/media/seen/", re.I)

ALLOWED_SCHEMES_PASSTHROUGH = {"data", "blob", "about", "qrc"}
ALLOWED_NETWORK_SCHEMES = {"https", "wss"}

_NAME_BAD = re.compile(r"[\x00-\x1f\x7f]")
_FILE_BAD = re.compile(r"[^A-Za-z0-9._-]+")
_HEX_ID = re.compile(r"^[a-f0-9]{12}$")
_REQ_ID = re.compile(r"^[a-f0-9]{16}$")


def host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in suffixes)


def is_nav_allowed(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == "https" and host_matches(p.hostname or "", NAV_HOSTS)


_USER_RE = re.compile(r"^@?([A-Za-z0-9._]{1,30})$")


def parse_instagram_input(text: str) -> str | None:
    """Converte link colado, 'instagram.com/x' ou '@perfil' em URL https do Instagram; None se não for."""
    t = (text or "").strip()
    if not t or len(t) > 2048 or any(c.isspace() for c in t):
        return None
    m = _USER_RE.match(t)
    if m and (t.startswith("@") or not re.search(r"instagr(am)?\.", t, re.I)):
        return f"https://www.instagram.com/{m.group(1)}/"
    if "://" not in t:
        if ":" in t.split("/", 1)[0]:
            return None  # esquemas como javascript:, data:
        t = "https://" + t
    try:
        p = urlsplit(t)
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or p.username or p.password or port not in (None, 443):
        return None
    host = (p.hostname or "").lower()
    if host in ("instagr.am", "www.instagr.am"):
        host = "www.instagram.com"
    if not host_matches(host, ("instagram.com",)):
        return None
    if host == "instagram.com" or host == "m.instagram.com":
        host = "www.instagram.com"
    path = p.path or "/"
    query = ("?" + p.query) if p.query else ""
    return f"https://{host}{path}{query}"


def is_captcha(url: str) -> bool:
    p = urlsplit(url)
    return (
        p.scheme == "https"
        and (p.hostname or "").lower() in CAPTCHA_HOSTS
        and p.path.startswith("/recaptcha/")
    )


def classify_request(url: str, *, block_third_party: bool, block_telemetry: bool, ghost: bool) -> str | None:
    """Retorna o motivo do bloqueio, ou None se a requisição pode seguir."""
    p = urlsplit(url)
    scheme = p.scheme.lower()
    if scheme in ALLOWED_SCHEMES_PASSTHROUGH:
        return None
    if scheme not in ALLOWED_NETWORK_SCHEMES:
        return "insecure-scheme"
    host = p.hostname or ""
    if block_third_party and not host_matches(host, RESOURCE_HOSTS) and not is_captcha(url):
        return "third-party"
    if block_telemetry and TELEMETRY_RE.search(p.path):
        return "telemetry"
    if ghost and STORY_SEEN_RE.search(p.path):
        return "story-seen"
    return None


def is_safe_media_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 4096:
        return False
    p = urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        return False
    if not host_matches(p.hostname or "", MEDIA_HOSTS):
        return False
    return media_ext(url) is not None


def media_ext(url: str) -> str | None:
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    if dot == -1 or "/" in path[dot:]:
        return None
    ext = path[dot:]
    if ext == ".jpeg":
        ext = ".jpg"
    return ext if ext in MEDIA_EXTS else None


def safe_filename(hint: str, ext: str) -> str:
    """Nome de arquivo sem separadores, sem ponto inicial, tamanho limitado."""
    if ext not in MEDIA_EXTS:
        raise ValueError("extensão não permitida")
    base = _FILE_BAD.sub("_", str(hint or "")).strip("._-")[:120] or "midia"
    return base + ext


def folder_name(account_name: str) -> str:
    """Subpasta de downloads da conta (mesma regra de saneamento dos arquivos)."""
    return safe_filename(account_name, ".jpg")[:-4]


def clean_account_name(name: str) -> str:
    name = _NAME_BAD.sub("", str(name or "")).strip()
    if not name:
        raise ValueError("nome vazio")
    return name[:40]


def new_account_id() -> str:
    return secrets.token_hex(6)


def is_account_id(value: str) -> bool:
    return isinstance(value, str) and bool(_HEX_ID.match(value))


def new_request_id() -> str:
    return secrets.token_hex(8)


def is_request_id(value: str) -> bool:
    return isinstance(value, str) and bool(_REQ_ID.match(value))


# Senha de bloqueio: scrypt (memory-hard), sal aleatório, comparação em tempo constante.
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return {"alg": "scrypt", "salt": salt.hex(), "hash": dk.hex()}


def verify_password(password: str, record: dict | None) -> bool:
    if not record or record.get("alg") != "scrypt":
        return False
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.compare_digest(dk, expected)
