"""Cache e retomada da pesquisa de perfil (módulo puro, sem Qt).

Uma pesquisa recente do mesmo perfil (dentro da validade, padrão 24 h) é reaproveitada:
a nova coleta continua na mesma pasta e busca só o que falta (mais posts a partir do cursor
salvo, comentários de posts ainda não lidos, destaques se faltarem).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

REUSABLE_STATES = ("done", "error", "cancelled")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def find_reusable(root: Path, username: str, now: float | None = None, max_age: float = 24 * 3600) -> Path | None:
    """Pasta mais recente de @username com coleta aproveitável dentro da validade."""
    now = time.time() if now is None else now
    if not root.is_dir() or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username or ""):
        return None
    best, best_ts = None, 0.0
    for d in root.glob(f"{username}-*"):
        if not d.is_dir() or d.is_symlink():
            continue
        st = _load(d / "status.json", {})
        if not isinstance(st, dict) or st.get("username") != username or st.get("state") not in REUSABLE_STATES:
            continue
        ts = st.get("collected_at") or st.get("updated_at") or 0
        if not isinstance(ts, (int, float)) or now - ts > max_age or ts > now + 60:
            continue
        if not (d / "profile.json").exists():
            continue
        if ts > best_ts:
            best, best_ts = d, float(ts)
    return best


def comment_targets(posts: list[dict], n: int) -> list[str]:
    """pks dos n posts mais comentados (mesma regra do script da página)."""
    cands = [p for p in posts if isinstance(p, dict) and isinstance(p.get("comments"), (int, float)) and p["comments"] > 0
             and re.fullmatch(r"\d{1,25}", str(p.get("pk", "")))]
    cands.sort(key=lambda p: p["comments"], reverse=True)
    return [str(p["pk"]) for p in cands[:max(0, n)]]


def plan(folder: Path, opts: dict) -> dict:
    """O que já existe na pasta e o que falta buscar para atender `opts`."""
    profile = _load(folder / "profile.json", None)
    posts = _load(folder / "posts.json", [])
    comments = _load(folder / "comments.json", {})
    highlights = _load(folder / "highlights.json", None)
    st = _load(folder / "status.json", {})
    resume = st.get("resume") if isinstance(st, dict) and isinstance(st.get("resume"), dict) else {}
    posts = [p for p in posts if isinstance(p, dict)] if isinstance(posts, list) else []
    comments = comments if isinstance(comments, dict) else {}
    profile = profile if isinstance(profile, dict) and re.fullmatch(r"\d{1,25}", str(profile.get("id", ""))) else None

    want = int(opts.get("posts", 60))
    feed_done = bool(resume.get("feed_done"))
    cursor = str(resume.get("cursor") or "")[:200]
    have = len(posts)
    need_posts = 0 if (feed_done or have >= want) else want - have
    targets = comment_targets(posts, int(opts.get("commentPosts", 15))) if opts.get("comments") else []
    missing_comments = [pk for pk in targets if pk not in comments]
    need_highlights = bool(opts.get("highlights")) and not isinstance(highlights, list)
    complete = profile is not None and need_posts == 0 and not missing_comments and not need_highlights
    return {
        "profile": profile,
        "posts": posts,
        "comments": {k: v for k, v in comments.items() if isinstance(v, list)},
        "highlights": highlights if isinstance(highlights, list) else [],
        "have_posts": have,
        "need_posts": need_posts,
        "cursor": cursor,
        "feed_done": feed_done,
        "comments_done": [k for k in comments if re.fullmatch(r"\d{1,25}", str(k))],
        "need_highlights": need_highlights,
        "complete": complete,
    }


def resume_payload(p: dict) -> dict:
    """Dados mínimos enviados ao script da página para retomar a coleta."""
    return {
        "profile": p["profile"],
        "have": p["have_posts"],
        "need": p["need_posts"],
        "cursor": p["cursor"],
        "feedDone": p["feed_done"] or p["need_posts"] == 0,
        "codes": [str(x.get("code", "")) for x in p["posts"]][:400],
        "known": [{"pk": str(x.get("pk", "")), "comments": x.get("comments") or 0} for x in p["posts"]][:400],
        "commentsDone": p["comments_done"][:100],
        "highlightsDone": not p["need_highlights"],
    }
