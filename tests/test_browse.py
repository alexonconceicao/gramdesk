"""Navegação passo a passo: regras puras (sem Qt, sem rede)."""

import random
import time

from gramdesk import browse
from gramdesk.ratelimit import Limits, RateState

NOW = time.mktime((2026, 10, 21, 14, 0, 0, 0, 0, -1))

GRID_HTML = """
<main role="main"><div><header><img alt="Foto do perfil de profa.ana" src="https://x.cdninstagram.com/pp.jpg" data-gd-w="150"></header>
<div class="grid">
  <a href="/profa.ana/p/AAA111aaa/" data-gd-vis="1">
    <div><img alt="Aula de forró" srcset="https://s.cdninstagram.com/a_320.jpg 320w, https://s.cdninstagram.com/a_640.jpg 640w" data-gd-vis="1"></div>
    <svg aria-label="Ícone de publicação fixada"><title>Ícone de publicação fixada</title></svg>
  </a>
  <a href="/profa.ana/p/BBB222bbb/" data-gd-vis="1"><img alt="Turma" src="https://s.cdninstagram.com/b.jpg">
    <svg aria-label="Carrossel"></svg></a>
  <a href="/reel/CCC333ccc/" data-gd-vis="0"><img alt="Passo básico" src="https://s.cdninstagram.com/c.jpg"></a>
  <a href="/profa.ana/p/DDD444ddd/" data-gd-vis="0"><img src="https://s.cdninstagram.com/d.jpg"><svg aria-label="Clip"></svg></a>
  <a href="/profa.ana/p/AAA111aaa/">duplicado</a>
  <a href="/outra.conta/p/EEE555eee/">sugestão de outro perfil</a>
  <a href="/explore/">explorar</a>
</div></div></main>
"""

POST_HTML = """
<article>
  <header><a href="/profa.ana/"><img alt="Foto do perfil de profa.ana" src="https://x.cdninstagram.com/pp.jpg" data-gd-w="32"></a>
    <a href="/profa.ana/">profa.ana</a></header>
  <div><ul>
    <li><img alt="Foto 1" data-gd-vis="1" data-gd-w="600" data-gd-nw="1080" data-gd-nh="1350"
         srcset="https://s.cdninstagram.com/v/p1_640.jpg 640w, https://s.cdninstagram.com/v/p1_1080.jpg 1080w, https://s.cdninstagram.com/v/p1_750.jpg 750w"
         src="https://s.cdninstagram.com/v/p1_640.jpg"></li>
    <li><img alt="Foto 2" data-gd-vis="0" data-gd-w="600" srcset="https://s.cdninstagram.com/v/p2_1080.jpg 1080w"></li>
  </ul>
  <button aria-label="Avançar"><svg></svg></button></div>
  <div><h1 dir="auto">Aula aberta sábado! #forro</h1>
  <time datetime="2026-10-18T15:00:00.000Z" title="18 de outubro de 2026">3 d</time>
  <time datetime="2026-10-19T10:00:00.000Z">comentário</time></div>
</article>
"""

VIDEO_HTML = """
<main><div>
  <img alt="capa" srcset="https://s.cdninstagram.com/v/capa.jpg 640w" data-gd-w="400">
  <video src="blob:https://www.instagram.com/1234-abcd" poster="https://s.cdninstagram.com/v/capa.jpg"
         data-gd-current="blob:https://www.instagram.com/1234-abcd" data-gd-nw="720" data-gd-nh="1280" data-gd-vis="1"></video>
</div></main>
"""


# ---------------------------------------------------------------- URLs
def test_target_of_allows_only_profiles_and_posts():
    assert browse.target_of("@profa.ana")["url"] == "https://www.instagram.com/profa.ana/"
    t = browse.target_of("https://www.instagram.com/profa.ana/p/AbC123xyz/?img_index=2")
    assert t == {"kind": "post", "url": "https://www.instagram.com/p/AbC123xyz/", "user": "profa.ana",
                 "code": "AbC123xyz", "media": "post"}
    assert browse.target_of("instagram.com/reel/AbC123xyz")["media"] == "reel"
    assert browse.target_of("https://www.instagram.com/profa.ana/reels/")["kind"] == "profile"
    for bad in ("https://www.instagram.com/accounts/edit/", "https://www.instagram.com/direct/inbox/",
                "https://www.instagram.com/explore/", "https://evil.com/profa.ana/", "javascript:alert(1)",
                "https://www.instagram.com/stories/profa.ana/123/", "http://instagram.com.evil.com/p/AAA11/",
                "https://www.instagram.com/p/../accounts/"):
        assert browse.target_of(bad) is None, bad


def test_same_post_ignores_owner_prefix():
    assert browse.same_post("https://www.instagram.com/profa.ana/p/AAA111aaa/", "AAA111aaa")
    assert not browse.same_post("https://www.instagram.com/profa.ana/", "AAA111aaa")


# ---------------------------------------------------------------- grade
def test_parse_grid_lists_posts_in_order_without_duplicates():
    items = browse.parse_grid(GRID_HTML, owner="profa.ana")
    assert [i["code"] for i in items] == ["AAA111aaa", "BBB222bbb", "CCC333ccc", "DDD444ddd"]
    by = {i["code"]: i for i in items}
    assert by["AAA111aaa"]["pinned"] and by["AAA111aaa"]["kind"] == "image" and by["AAA111aaa"]["visible"]
    assert by["AAA111aaa"]["thumb"].endswith("a_640.jpg")
    assert by["BBB222bbb"]["kind"] == "carousel"
    assert by["CCC333ccc"]["kind"] == "reel" and by["CCC333ccc"]["url"] == "https://www.instagram.com/reel/CCC333ccc/"
    assert not by["CCC333ccc"]["visible"]
    assert by["DDD444ddd"]["kind"] == "reel"


def test_parse_avatar_reads_profile_picture_from_header():
    assert browse.parse_avatar(GRID_HTML) == "https://x.cdninstagram.com/pp.jpg"
    assert browse.parse_avatar("<main><img alt='Turma' src='https://s.cdninstagram.com/b.jpg'></main>") == ""


def test_parse_grid_without_owner_keeps_other_profiles():
    assert "EEE555eee" in [i["code"] for i in browse.parse_grid(GRID_HTML)]


# ---------------------------------------------------------------- post
def test_best_srcset_picks_largest_width():
    assert browse.best_srcset("a.jpg 640w, b.jpg 1080w, c.jpg 750w") == ("b.jpg", 1080)
    assert browse.best_srcset("a.jpg 1x, b.jpg 2x") == ("b.jpg", 0)
    assert browse.best_srcset("") == ("", 0)


def test_parse_post_carousel_caption_date_and_best_resolution():
    info = browse.parse_post(POST_HTML, "https://www.instagram.com/p/AAA111aaa/", {}, None)
    assert info["type"] == "carousel" and info["owner"] == "profa.ana"
    assert info["caption"] == "Aula aberta sábado! #forro"
    assert info["date"] == "2026-10-18T15:00:00.000Z" and info["date_text"] == "18 de outubro de 2026"
    assert info["best_url"].endswith("p1_1080.jpg")  # visível e de maior resolução
    assert (info["width"], info["height"]) == (1080, 1350)
    assert [m["index"] for m in info["media"]] == [1, 2]
    assert all("pp.jpg" not in m["url"] for m in info["media"])  # foto de perfil fora
    assert info["items_total"] == 2


def test_parse_post_video_uses_stream_and_drops_cover_image():
    info = browse.parse_post(VIDEO_HTML, "https://www.instagram.com/reel/RRR111rrr/", {}, {"count": 1, "user": "profa.ana"})
    assert info["type"] == "reel" and info["owner"] == "profa.ana"
    assert len(info["media"]) == 1 and info["media"][0]["kind"] == "video"
    assert info["best_is_stream"] and (info["width"], info["height"]) == (720, 1280)


def test_parse_post_falls_back_to_meta_and_index():
    html = '<main><img srcset="https://s.cdninstagram.com/x.jpg 1080w" data-gd-w="500"></main>'
    meta = {"og:description": '12 curtidas, 1 comentários - profa.ana em 18 de outubro de 2026: "Turma nova".'}
    info = browse.parse_post(html, "https://www.instagram.com/p/XYZ12345/", meta, {"taken_at": 1760799600, "count": 3})
    assert info["caption"] == "Turma nova"
    assert info["date"] == "2025-10-18T15:00:00.000Z"
    assert info["type"] == "carousel" and info["items_total"] == 3


# ---------------------------------------------------------------- bloqueio
def test_classify_signals():
    c = browse.classify_signals
    assert c({"url": "https://www.instagram.com/challenge/action/?next=/"}) == "checkpoint"
    assert c({"url": "https://www.instagram.com/accounts/login/?next=/p/x/"}) == "login"
    assert c({"url": "https://www.instagram.com/p/x/", "alerts": "Tente novamente mais tarde. Restringimos determinadas atividades"}) == "rate-limit"
    assert c({"url": "https://www.instagram.com/p/x/", "alerts": "Please wait a few minutes before you try again."}) == "rate-limit"
    assert c({"url": "https://www.instagram.com/profa.ana/", "loginForm": True}) == "login"
    assert c({"url": "https://www.instagram.com/profa.ana/", "alerts": "", "loginForm": False}) is None
    assert c(None) is None


# ---------------------------------------------------------------- arquivos
def test_file_name_is_predictable_and_safe():
    assert browse.file_name("profa.ana", "AAA111aaa", ".jpg") == "profa.ana_AAA111aaa.jpg"
    assert browse.file_name("profa.ana", "AAA111aaa", ".mp4", 2, 5) == "profa.ana_AAA111aaa_02.mp4"
    assert "/" not in browse.file_name("../../etc", "x/y", ".jpg")


def test_pick_file_requires_index_for_carousel():
    files = [{"url": f"https://s.cdninstagram.com/v/{i}.jpg"} for i in range(3)] + [{"url": "https://evil.com/x.jpg"}]
    f, total, err = browse.pick_file(files, None)
    assert f is None and total == 3 and "--index 1..3" in err
    f, total, err = browse.pick_file(files, 2)
    assert f["url"].endswith("/1.jpg") and not err
    assert browse.pick_file(files, 4)[0] is None
    assert browse.pick_file(files[:1], None)[0]["url"].endswith("/0.jpg")


# ---------------------------------------------------------------- ritmo
def test_min_interval_with_jitter_between_actions(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits()
    assert st.browse_check("a1", lim, NOW)["wait"] == 0
    st.record_browse("a1", lim, NOW, rng=random.Random(1))
    nxt = st.browse_touch("a1", lim, NOW + 5, rng=random.Random(2))  # ação terminou 5 s depois
    assert NOW + 5 + lim.browse_interval <= nxt <= NOW + 5 + lim.browse_interval + lim.browse_jitter
    chk = st.browse_check("a1", lim, NOW + 10)
    assert chk["ok"] and abs(chk["wait"] - (nxt - NOW - 10)) < 1e-6  # chamou antes: espera o restante
    assert st.browse_check("a1", lim, nxt + 0.1)["wait"] == 0
    # persistido
    assert RateState(tmp_path / "r.json").browse_check("a1", lim, NOW + 10)["wait"] > 0


def test_jitter_varies(tmp_path):
    lim = Limits()
    rng = random.Random(7)
    gaps = set()
    for i in range(10):
        st = RateState(tmp_path / f"r{i}.json")
        gaps.add(round(st.browse_touch("a1", lim, NOW, rng=rng) - NOW, 3))
    assert len(gaps) == 10 and min(gaps) >= 8 and max(gaps) <= 20


def test_daily_post_cap_and_counts_as_request(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits.from_dict({"browse_daily_posts": 2})
    for i in range(2):
        st.record_browse("a1", lim, NOW + i * 60, post_open=True)
    chk = st.browse_check("a1", lim, NOW + 200, post_open=True)
    assert not chk["ok"] and chk["reason"] == "posts"
    assert st.browse_check("a1", lim, NOW + 200, post_open=False)["ok"]  # ler a grade ainda pode
    assert st.status("a1", lim, NOW + 200)["items_today"] == 2  # cada ação = 1 requisição


def test_cooldown_blocks_browse(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits()
    st.record_block("a1", "rate-limit", NOW)
    chk = st.browse_check("a1", lim, NOW + 60)
    assert not chk["ok"] and chk["reason"] == "cooldown" and "aguarde até" in chk["message"]


def test_research_quota_does_not_block_browse(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits(daily_researches=1)
    st.begin("a1", NOW)
    assert not st.check("a1", lim, NOW)["ok"]
    assert st.browse_check("a1", lim, NOW)["ok"]


def test_browse_limits_are_clamped():
    lim = Limits.from_dict({"browse_interval": 1, "browse_daily_posts": 10_000, "browse_jitter": -5})
    assert lim.browse_interval == 5.0 and lim.browse_daily_posts == 300 and lim.browse_jitter == 0.0
    assert Limits().browse_daily_posts == 150 and Limits().browse_interval == 8.0 and Limits().browse_jitter == 12.0
