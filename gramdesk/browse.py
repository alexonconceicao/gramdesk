"""Navegação passo a passo (gramdesk-browse): regras puras, sem Qt e sem rede.

Em vez de ler o perfil por requisições internas em sequência (pesquisa), o app navega na
própria janela como uma pessoa: abre o perfil, lê a grade já renderizada, abre UM post,
confere o conteúdo e baixa só aquele arquivo. Cada ação é pedida pela linha de comando,
uma por vez, e o app decide o ritmo.

Aqui ficam as partes testáveis:
- target_of / allowed_url: quais URLs do Instagram podem ser abertas e o que são;
- parse_html: árvore mínima do HTML que a página manda (já anotado pelo script injetado);
- parse_grid / parse_post: links da grade e dados do post aberto, só a partir do DOM;
- best_srcset: maior resolução de um srcset;
- classify_signals: sinais de bloqueio na página (checkpoint, limite, tela de login);
- file_name: nome previsível do arquivo baixado.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from . import ratelimit, security

ACTIONS = ("open-url", "list-grid", "inspect-post", "download-current")
NAV_ACTIONS = ("open-url", "inspect-post", "download-current")  # sujeitas ao intervalo mínimo
RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "legal",
    "developer", "challenge", "checkpoint", "web", "api", "graphql", "emails", "session", "ajax",
    "data", "privacy", "terms", "lite", "oauth", "nametag", "your_activity", "notifications",
}

_USER = r"[A-Za-z0-9._]{1,30}"
_CODE = r"[A-Za-z0-9_-]{5,64}"
_PROFILE_PATH = re.compile(rf"^/({_USER})/(?:(reels|tagged)/)?$")
_POST_PATH = re.compile(rf"^/(?:({_USER})/)?(p|reel|tv)/({_CODE})/?$")
_PROFILE_ALT = re.compile(r"profile picture|foto do perfil|foto de perfil|avatar", re.I)
_CAROUSEL_LABEL = re.compile(r"^(carousel|carrossel|sequência|álbum)$", re.I)
_REEL_LABEL = re.compile(r"^(clip|clipe|reel|reels)$", re.I)
_VIDEO_LABEL = re.compile(r"^(video|vídeo)$", re.I)
_PINNED_LABEL = re.compile(r"pinned|fixad", re.I)
_NEXT_LABEL = re.compile(r"^(next|avançar|próximo|seguinte)$", re.I)


# ---------------------------------------------------------------- URLs
def target_of(text: str) -> dict | None:
    """O que é a URL/perfil: {'kind': 'profile'|'post', 'url', 'user', 'code', 'media'} ou None.

    Só aceita perfil (/<perfil>/, /<perfil>/reels/, /<perfil>/tagged/) e post/reel
    (/p/<código>/, /reel/<código>/, /<perfil>/p/<código>/). Query string é descartada.
    """
    url = security.parse_instagram_input(text)
    if not url:
        return None
    p = urlsplit(url)
    if (p.hostname or "") != "www.instagram.com":
        return None
    path = p.path if p.path.endswith("/") else p.path + "/"
    m = _POST_PATH.match(path)
    if m:
        user, kind, code = m.group(1) or "", m.group(2), m.group(3)
        if user and user.lower() in RESERVED:
            return None
        seg = "reel" if kind == "reel" else "p"
        return {"kind": "post", "url": f"https://www.instagram.com/{seg}/{code}/", "user": user, "code": code,
                "media": "reel" if kind == "reel" else "post"}
    m = _PROFILE_PATH.match(path)
    if m and m.group(1).lower() not in RESERVED:
        tab = f"{m.group(2)}/" if m.group(2) else ""
        return {"kind": "profile", "url": f"https://www.instagram.com/{m.group(1)}/{tab}", "user": m.group(1),
                "code": "", "media": ""}
    return None


def allowed_url(text: str) -> str | None:
    t = target_of(text)
    return t["url"] if t else None


def same_post(current_url: str, code: str) -> bool:
    t = target_of(current_url or "")
    return bool(t and t["kind"] == "post" and t["code"] == code)


# ---------------------------------------------------------------- HTML mínimo
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node | str] = []
        self.parent = parent

    def get(self, key: str, default: str = "") -> str:
        v = self.attrs.get(key)
        return v if isinstance(v, str) else default

    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def find_all(self, *tags: str):
        return [n for n in self.iter() if n.tag in tags]

    def find(self, *tags: str):
        return next((n for n in self.iter() if n.tag in tags), None)

    def text_content(self) -> str:
        out = []

        def walk(n):
            for c in n.children:
                if isinstance(c, str):
                    out.append(c)
                elif c.tag == "br":
                    out.append("\n")
                else:
                    walk(c)

        walk(self)
        return "".join(out)

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 60000:  # página fora do normal: para de crescer
            return
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self.cur.tag == tag:
            self.cur = self.cur.parent or self.root

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n is not self.root:
            if n.tag == tag:
                self.cur = n.parent or self.root
                return
            n = n.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def parse_html(html: str) -> Node:
    b = _Builder()
    b.feed(html or "")
    b.close()
    return b.root


# ---------------------------------------------------------------- mídia
def best_srcset(srcset: str) -> tuple[str, int]:
    """Maior candidato de um srcset ('url 640w, url2 1080w'). Devolve (url, largura); largura 0 se não informada."""
    best_url, best_rank, best_w = "", -1.0, 0
    for part in (srcset or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url, w, rank = bits[0], 0, 0.0
        if len(bits) > 1:
            d = bits[1].lower()
            try:
                if d.endswith("w"):
                    w = int(float(d[:-1]))
                    rank = float(w)
                elif d.endswith("x"):
                    rank = float(d[:-1])  # densidade: só ordena entre si, largura desconhecida
            except ValueError:
                pass
        if rank > best_rank:
            best_url, best_rank, best_w = url, rank, w
    return best_url, best_w


def _int(v) -> int:
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


def _abs(url: str) -> str:
    if not url:
        return ""
    if url.startswith(("blob:", "data:")):
        return url
    return urljoin("https://www.instagram.com/", url)


def media_of(el: Node) -> dict:
    """Melhor URL e dimensões de um <img> ou <video> (anotado pelo app com data-gd-*)."""
    nw, nh = _int(el.get("data-gd-nw")), _int(el.get("data-gd-nh"))
    if el.tag == "video":
        cur = el.get("data-gd-current") or el.get("src")
        if not cur:
            src = el.find("source")
            cur = src.get("src") if src else ""
        url = _abs(cur)
        return {"kind": "video", "url": url, "blob": url.startswith("blob:"), "width": nw or _int(el.get("width")),
                "height": nh or _int(el.get("height")), "poster": _abs(el.get("poster")),
                "visible": el.get("data-gd-vis") == "1"}
    url, w = best_srcset(el.get("srcset"))
    if not url:
        url = el.get("data-gd-current") or el.get("src")
    url = _abs(url)
    width = w or nw
    height = round(nh * width / nw) if (nw and nh and width) else nh
    return {"kind": "image", "url": url, "blob": False, "width": width, "height": height,
            "alt": el.get("alt")[:600], "visible": el.get("data-gd-vis") == "1"}


def _is_content_img(el: Node) -> bool:
    if _PROFILE_ALT.search(el.get("alt")):
        return False
    shown = _int(el.get("data-gd-w"))
    _u, w = best_srcset(el.get("srcset"))
    if shown and shown < 150:
        return False
    if any(a.tag == "header" for a in el.ancestors()):
        return False
    return shown >= 200 or w >= 320 or _int(el.get("data-gd-nw")) >= 320


# ---------------------------------------------------------------- grade
def _post_href(href: str) -> tuple[str, str, str] | None:
    p = urlsplit(href or "")
    if p.netloc and not security.host_matches(p.hostname or "", ("instagram.com",)):
        return None
    path = p.path if p.path.endswith("/") else p.path + "/"
    m = _POST_PATH.match(path)
    if not m or (m.group(1) and m.group(1).lower() in RESERVED):
        return None
    return m.group(1) or "", m.group(2), m.group(3)


def _labels(node: Node) -> list[str]:
    out = []
    for n in [node, *node.iter()]:
        for key in ("aria-label", "title"):
            v = n.get(key).strip()
            if v:
                out.append(v)
        if n.tag == "title":
            t = n.text_content().strip()
            if t:
                out.append(t)
    return out


def parse_grid(html: str, owner: str = "") -> list[dict]:
    """Links de posts/reels da grade do perfil, na ordem da página, sem repetir.

    Cada item: url, code, kind (image|carousel|video|reel), visible, pinned, alt, thumb.
    Com owner, ignora links de outros perfis (ex.: sugestões).
    """
    root = parse_html(html)
    out, seen = [], set()
    for a in root.find_all("a"):
        hit = _post_href(a.get("href"))
        if not hit:
            continue
        user, seg, code = hit
        if owner and user and user.lower() != owner.lower():
            continue
        if code in seen:
            continue
        seen.add(code)
        labels = _labels(a)
        kind = "image"
        if seg == "reel" or any(_REEL_LABEL.match(x) for x in labels):
            kind = "reel"
        elif any(_CAROUSEL_LABEL.match(x) for x in labels):
            kind = "carousel"
        elif any(_VIDEO_LABEL.match(x) for x in labels):
            kind = "video"
        img = a.find("img")
        thumb = media_of(img)["url"] if img is not None else ""
        out.append({
            "url": f"https://www.instagram.com/{'reel' if seg == 'reel' else 'p'}/{code}/",
            "code": code,
            "kind": kind,
            "visible": a.get("data-gd-vis") == "1",
            "pinned": any(_PINNED_LABEL.search(x) for x in labels),
            "alt": (img.get("alt") if img is not None else "")[:300],
            "thumb": thumb,
        })
        if len(out) >= 300:
            break
    return out


# ---------------------------------------------------------------- post
_OG_CAPTION = re.compile(r':\s*["“](.*)["”]\s*\.?\s*$', re.S)


def _owner_from(root: Node) -> str:
    for scope in (root.find("header"), root):
        if scope is None:
            continue
        for a in scope.find_all("a"):
            p = urlsplit(a.get("href"))
            m = re.match(rf"^/({_USER})/$", p.path or "")
            if m and m.group(1).lower() not in RESERVED:
                return m.group(1)
    return ""


def _caption_from(root: Node, meta: dict) -> str:
    h1 = root.find("h1")
    if h1 is not None:
        t = h1.text_content().strip()
        if t:
            return t[:6000]
    desc = str(meta.get("og:description") or meta.get("description") or "")
    m = _OG_CAPTION.search(desc)
    return m.group(1).strip()[:6000] if m else ""


def parse_post(html: str, url: str = "", meta: dict | None = None, index: dict | None = None) -> dict:
    """Dados do post aberto a partir do DOM: tipo, legenda, data, dimensões e mídias (maior resolução).

    meta: tags og:* da página; index: o que o app já recebeu do próprio site sobre o post (sem
    requisição extra), usado só para completar o que o DOM não mostra (total do carrossel etc.).
    """
    meta = meta if isinstance(meta, dict) else {}
    index = index if isinstance(index, dict) else {}
    root = parse_html(html)
    tgt = target_of(url) or {}

    media, seen = [], set()
    for el in root.iter():
        if el.tag == "video" or (el.tag == "img" and _is_content_img(el)):
            m = media_of(el)
            key = m["url"] if not m["blob"] else m["url"] + m.get("poster", "")
            if not m["url"] or key in seen:
                continue
            # capa do vídeo (poster) repetida como <img>: fica só o vídeo
            if m["kind"] == "image" and any(x.get("poster") and _same_file(x["poster"], m["url"]) for x in media):
                continue
            seen.add(key)
            media.append(m)
    # <img> de capa que vem antes do <video>
    media = [m for m in media if not (m["kind"] == "image" and any(
        v["kind"] == "video" and v.get("poster") and _same_file(v["poster"], m["url"]) for v in media))]

    labels = _labels(root)
    has_next = any(_NEXT_LABEL.match(x) for x in labels)
    carousel_lis = 0
    for ul in root.find_all("ul"):
        lis = [li for li in ul.children if isinstance(li, Node) and li.tag == "li"
               and any(n.tag in ("img", "video") for n in li.iter())]
        carousel_lis = max(carousel_lis, len(lis))
    total = _int(index.get("count")) or 0
    if total > 1 or has_next or carousel_lis > 1 or len(media) > 1 and tgt.get("media") != "reel":
        kind = "carousel"
    elif tgt.get("media") == "reel" or index.get("type") == "reel":
        kind = "reel"
    elif any(m["kind"] == "video" for m in media) or index.get("type") == "video":
        kind = "video"
    else:
        kind = "image"

    time_el = root.find("time")
    date = time_el.get("datetime") if time_el is not None else ""
    date_text = (time_el.get("title") or time_el.text_content().strip()) if time_el is not None else ""
    if not date and index.get("taken_at"):
        import datetime as _dt

        try:
            date = _dt.datetime.fromtimestamp(float(index["taken_at"]), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except (TypeError, ValueError, OverflowError, OSError):
            date = ""

    caption = _caption_from(root, meta) or str(index.get("caption") or "")[:6000]
    for i, m in enumerate(media, 1):
        m["index"] = i
    visible = [m for m in media if m.get("visible")] or media
    best = max(visible, key=lambda m: (m["width"] or 0) * (m["height"] or 1), default=None)
    owner = _owner_from(root) or str(index.get("user") or "") or tgt.get("user", "")
    return {
        "url": tgt.get("url", url),
        "code": tgt.get("code", ""),
        "owner": owner,
        "type": kind,
        "caption": caption,
        "date": date,
        "date_text": date_text[:80],
        "width": best["width"] if best else 0,
        "height": best["height"] if best else 0,
        "best_url": best["url"] if best else "",
        "best_is_stream": bool(best and best["blob"]),
        "items_total": total or (carousel_lis if kind == "carousel" else 1) or len(media),
        "media": media[:20],
    }


def _same_file(a: str, b: str) -> bool:
    pa, pb = urlsplit(a or "").path, urlsplit(b or "").path
    return bool(pa) and pa.rsplit("/", 1)[-1] == pb.rsplit("/", 1)[-1]


# ---------------------------------------------------------------- bloqueio
def classify_signals(sig: dict | None) -> str | None:
    """Sinais que a página mostrou depois de uma ação: checkpoint, limite ou tela de login.

    sig: {'url', 'loginForm': bool, 'alerts': texto de diálogos/avisos pequenos, 'title'}.
    Legendas e comentários não entram em 'alerts' (o script injetado só lê diálogos sem post).
    """
    if not isinstance(sig, dict):
        return None
    url = str(sig.get("url") or "")[:500]
    path = urlsplit(url).path
    if re.match(r"^/(challenge|checkpoint)(/|$)", path) or re.search(r"/(challenge|checkpoint)/", path):
        return "checkpoint"
    if path.startswith("/accounts/login") or path.startswith("/accounts/suspended"):
        return "login"
    alerts = str(sig.get("alerts") or "")[:4000]
    if alerts:
        kind = ratelimit.classify_block(200, "", alerts)
        if kind in ("rate-limit", "checkpoint"):
            return kind
    if sig.get("loginForm"):
        return "login"
    return None


# ---------------------------------------------------------------- arquivos
def file_name(owner: str, code: str, ext: str, index: int | None = None, total: int = 1) -> str:
    """<perfil>_<código>[_NN].ext, saneado. Mesmo post e item = mesmo nome (não duplica)."""
    base = f"{owner or 'instagram'}_{code or 'midia'}"
    if total > 1 or (index or 1) > 1:
        base += f"_{int(index or 1):02d}"
    return security.safe_filename(base, ext)


def pick_file(files: list, index: int | None) -> tuple[dict | None, int, str]:
    """Escolhe UM arquivo do post (carrossel exige índice 1..N). Devolve (arquivo, total, erro)."""
    files = [f for f in files if isinstance(f, dict) and security.is_safe_media_url(f.get("url"))]
    total = len(files)
    if not total:
        return None, 0, "Nenhuma mídia encontrada no post aberto."
    if index is None:
        if total > 1:
            return None, total, f"Carrossel com {total} itens: use --index 1..{total} (um item por chamada)."
        index = 1
    if not 1 <= index <= total:
        return None, total, f"Índice fora do intervalo: o post tem {total} item(ns)."
    return files[index - 1], total, ""
