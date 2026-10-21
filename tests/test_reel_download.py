"""Download de reel/vídeo pelo modo passo a passo: URL progressiva (mp4) dos dados que a página já
recebeu, nunca o stream blob:. Sem rede: o inject.js roda no node com DOM mínimo e fetch que falha."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from importlib import resources

import pytest

from gramdesk import browse, security

CODE = "DeZh22nx-HI"
CDN = "https://instagram.fssa17-1.fna.fbcdn.net"
V480 = f"{CDN}/o1/v/t16/f2/m86/AQ480.mp4?efg=e30%3D&_nc_cat=1&oh=00_a&oe=6700AAAA"
V720 = f"{CDN}/o1/v/t16/f2/m86/AQ720.mp4?efg=e30%3D&_nc_cat=1&oh=00_b&oe=6700AAAA"
V1080 = f"{CDN}/o1/v/t16/f2/m86/AQ1080.mp4?efg=e30%3D&_nc_cat=1&oh=00_c&oe=6700AAAA"
COVER = f"{CDN}/v/t51.29350-15/cover_1080.jpg?stp=dst-jpg_e35&_nc_ht=x"


def reel_item(**over):
    item = {
        "code": CODE, "pk": "3700000000000000001", "id": "3700000000000000001_123", "media_type": 2,
        "product_type": "clips", "taken_at": 1760000000,
        "user": {"pk": "123", "username": "escola.forro"},
        "caption": {"text": "Passo básico de forró"},
        "image_versions2": {"candidates": [
            {"width": 640, "height": 1138, "url": f"{CDN}/v/t51.29350-15/cover_640.jpg"},
            {"width": 1080, "height": 1920, "url": COVER},
        ]},
        "video_versions": [
            {"type": 101, "width": 720, "height": 1280, "url": V720},
            {"type": 102, "width": 1080, "height": 1920, "url": V1080},
            {"type": 103, "width": 480, "height": 854, "url": V480},
        ],
        "video_dash_manifest": "<MPD></MPD>",
    }
    item.update(over)
    return item


def sjs(data: dict) -> str:
    """Mesmo aninhamento do JSON que o Instagram embute na página (script data-sjs), com '\\/'."""
    payload = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [[
        "RelayPrefetchedStreamCache", "next", [], ["adp_PolarisPostRootQueryRelayPreloader_1",
                                                   {"__bbox": {"complete": True, "result": {"data": data}}}]]]}}]]]}
    return json.dumps(payload, ensure_ascii=False).replace("/", "\\/")


def page_html(*blobs: str) -> str:
    scripts = "".join(f'<script type="application/json" data-content-len="1" data-sjs>{b}</script>' for b in blobs)
    return f"<!DOCTYPE html><html><head>{scripts}</head><body><div id='root'></div></body></html>"


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self._in = [], False

    def handle_starttag(self, tag, attrs):
        self._in = tag == "script" and dict(attrs).get("type") == "application/json"
        if self._in:
            self.out.append("")

    def handle_endtag(self, tag):
        self._in = False

    def handle_data(self, data):
        if self._in:
            self.out[-1] += data


HARNESS = r"""
const fs = require('fs');
const [srcFile, inFile] = process.argv.slice(2);
const input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
globalThis.window = globalThis;
let netCalls = 0;
globalThis.fetch = async () => { netCalls += 1; throw new Error('sem rede nos testes'); };
globalThis.XMLHttpRequest = function () {};
XMLHttpRequest.prototype.open = function () {};
XMLHttpRequest.prototype.send = function () {};
globalThis.location = { pathname: input.path, href: 'https://www.instagram.com' + input.path };
const scripts = input.scripts.map((t) => ({ textContent: t }));
globalThis.document = {
  querySelectorAll: (sel) => (sel === 'script[type="application/json"]' ? scripts : []),
  querySelector: () => null, documentElement: null, hidden: false,
};
globalThis.innerWidth = 1280; globalThis.innerHeight = 900;
eval(fs.readFileSync(srcFile, 'utf8'));
const b = window['__gd_test'];
(async () => {
  const out = { media: JSON.parse(b.media()), netBefore: netCalls };
  if (input.start) {
    b.start('0123456789abcdef', 'current', null, null);
    for (let i = 0; i < 50; i++) {
      const r = b.take('0123456789abcdef');
      if (r) { out.start = JSON.parse(r); break; }
      await new Promise((ok) => setTimeout(ok, 10));
    }
  }
  out.net = netCalls;
  process.stdout.write(JSON.stringify(out));
})();
"""


def run_page(tmp_path, html: str, path: str = f"/reel/{CODE}/", start: bool = False) -> dict:
    if not shutil.which("node"):
        pytest.skip("node ausente")
    src = resources.files("gramdesk").joinpath("inject.js").read_text(encoding="utf-8")
    src = (src.replace("__GD_KEY__", "__gd_test").replace("__GD_GHOST__", "false")
           .replace("__GD_TOKEN__", "t" * 32).replace("__GD_BUTTONS__", "false"))
    p = _Scripts()
    p.feed(html)
    (tmp_path / "inject.js").write_text(src, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps({"path": path, "scripts": p.out, "start": start}), encoding="utf-8")
    (tmp_path / "h.js").write_text(HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(tmp_path / "h.js"), str(tmp_path / "inject.js"), str(tmp_path / "in.json")],
                       capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout)


# ---------------------------------------------------------------- inject.js (dados da página)
def test_reel_index_has_biggest_progressive_mp4_without_network(tmp_path):
    html = page_html(sjs({"xdt_api__v1__media__shortcode__web_info": {"items": [reel_item()]}}))
    out = run_page(tmp_path, html)
    idx = out["media"]
    assert out["net"] == 0  # nada sai para a rede
    assert idx["code"] == CODE and idx["type"] == "reel" and idx["user"] == "escola.forro"
    m = idx["media"][0]
    assert m["kind"] == "video" and m["url"] == V1080
    assert (m["width"], m["height"]) == (1080, 1920)
    assert [v["width"] for v in m["versions"]] == [1080, 720, 480]
    assert m["thumb"] == COVER


def test_reel_on_reels_path_and_graphql_shape(tmp_path):
    node = {"__typename": "XDTGraphVideo", "id": "3700000000000000001", "shortcode": CODE, "is_video": True,
            "product_type": "clips", "video_url": V720, "display_url": COVER,
            "dimensions": {"width": 720, "height": 1280}, "owner": {"id": "123", "username": "escola.forro"}}
    out = run_page(tmp_path, page_html(sjs({"xdt_shortcode_media": node})), path=f"/reels/{CODE}/")
    m = out["media"]["media"][0]
    assert m["kind"] == "video" and m["url"] == V720 and out["net"] == 0


def test_grid_thumbnail_does_not_replace_video_data(tmp_path):
    full = sjs({"xdt_api__v1__media__shortcode__web_info": {"items": [reel_item()]}})
    thumb = sjs({"user": {"edge_owner_to_timeline_media": {"edges": [{"node": {
        "id": "3700000000000000001", "shortcode": CODE, "is_video": True, "display_url": COVER,
        "dimensions": {"width": 1080, "height": 1920}}}]}}})
    out = run_page(tmp_path, page_html(full, thumb))
    assert out["media"]["media"][0]["url"] == V1080


def test_video_without_versions_is_not_the_cover(tmp_path):
    item = reel_item()
    del item["video_versions"]
    out = run_page(tmp_path, page_html(sjs({"xdt_api__v1__media__shortcode__web_info": {"items": [item]}})),
                   start=True)
    m = out["media"]["media"][0]
    assert m["kind"] == "video" and m["url"] == ""  # capa jpg não vira "o vídeo"
    # mecanismo do botão: tenta UMA consulta (aqui falha, sem rede) e informa quantas fez
    assert out["start"]["ok"] and out["start"]["api"] == 1 and out["net"] == 1
    assert browse.pick_file(out["start"]["files"], None)[0] is None


# ---------------------------------------------------------------- regras Python
REEL_DOM = """
<section><article>
  <header><a href="/escola.forro/">escola.forro</a></header>
  <div><video src="blob:https://www.instagram.com/2f1c9a3e-1111-2222-3333-444455556666" data-gd-vis="1"
        data-gd-nw="1080" data-gd-nh="1920" data-gd-w="420" data-gd-current="blob:https://www.instagram.com/2f1c9a3e"></video></div>
  <h1>Passo básico de forró</h1><time datetime="2025-10-09T08:53:20.000Z">9 out</time>
</article></section>
"""


def reel_index():
    return {"code": CODE, "user": "escola.forro", "count": 1, "type": "reel", "taken_at": 1760000000,
            "media": [{"kind": "video", "width": 1080, "height": 1920, "url": V1080, "thumb": COVER,
                       "versions": [{"url": V1080, "width": 1080, "height": 1920},
                                    {"url": V720, "width": 720, "height": 1280}]}]}


def test_inspect_reports_best_non_blob_url():
    info = browse.parse_post(REEL_DOM, f"https://www.instagram.com/reel/{CODE}/", {}, reel_index())
    assert info["type"] == "reel" and info["best_is_stream"] is True
    assert info["best"] == {"index": 1, "kind": "video", "url": V1080, "width": 1080, "height": 1920,
                            "thumb": COVER, "source": "page-data"}
    assert not info["best"]["url"].startswith("blob:")


def test_index_of_other_post_is_ignored():
    idx = dict(reel_index(), code="OutroPost11")
    info = browse.parse_post(REEL_DOM, f"https://www.instagram.com/reel/{CODE}/", {}, idx)
    assert info["best"] is None and info["sources"] == []


def test_sources_reject_blob_and_foreign_hosts_and_cover_for_video():
    idx = {"media": [
        {"kind": "video", "url": "blob:https://www.instagram.com/x", "width": 1080, "height": 1920,
         "versions": [{"url": "https://evil.example/v.mp4", "width": 1080, "height": 1920},
                      {"url": COVER, "width": 1080, "height": 1920},
                      {"url": V480, "width": 480, "height": 854}]},
        {"kind": "video", "url": "", "width": 720, "height": 1280},
        {"kind": "image", "url": COVER, "width": 1080, "height": 1920},
    ]}
    s = browse.index_sources(idx)
    assert s[0]["url"] == V480 and (s[0]["width"], s[0]["height"]) == (480, 854)
    assert s[1]["url"] == "" and s[1]["kind"] == "video"
    assert s[2]["url"] == COVER and s[2]["kind"] == "image"


def test_pick_source():
    s = browse.index_sources(reel_index())
    f, total, err = browse.pick_source(s, None)
    assert f["url"] == V1080 and total == 1 and err == ""
    assert browse.pick_source([], None) == (None, 0, "")
    two = browse.index_sources({"media": reel_index()["media"] * 2})
    assert "use --index" in browse.pick_source(two, None)[2]
    assert "fora do intervalo" in browse.pick_source(two, 3)[2]
    no_url = browse.index_sources({"media": [{"kind": "video", "url": ""}]})
    assert browse.pick_source(no_url, None) == (None, 1, "")  # sem URL: o app tenta o botão / avisa


def test_reels_path_is_the_same_post():
    t = browse.target_of(f"https://www.instagram.com/reels/{CODE}/")
    assert t["kind"] == "post" and t["code"] == CODE and t["media"] == "reel"
    assert t["url"] == f"https://www.instagram.com/reel/{CODE}/"
    assert browse.same_post(f"https://www.instagram.com/reels/{CODE}/", CODE)
    assert browse.target_of("https://www.instagram.com/escola.forro/reels/")["kind"] == "profile"


def test_downloaded_file_signature():
    assert security.media_magic_ok(b"\x00\x00\x00\x20ftypisom\x00\x00", ".mp4")
    assert not security.media_magic_ok(b"<!DOCTYPE html>", ".mp4")
    assert not security.media_magic_ok(b"", ".mp4")
    assert security.media_magic_ok(b"\xff\xd8\xff\xe0", ".jpg")
    assert security.media_magic_ok(b"\x00\x00\x00\x18ftypheic", ".heic")


def test_video_no_url_message():
    assert "sem URL baixável" in browse.VIDEO_NO_URL and "botão de baixar" in browse.VIDEO_NO_URL


SUGGESTED_REELS_DOM = """
<main role="main"><div>
  <section>
    <div><video src="blob:https://www.instagram.com/aaaa-bbbb" data-gd-vis="1" data-gd-nw="1080" data-gd-nh="1920"
          data-gd-w="420"></video></div>
    <img alt="capa do reel" data-gd-vis="1" data-gd-w="420" src="https://scontent.cdninstagram.com/v/blob_cover.jpg">
    <header><a href="/escola.forro/">escola.forro</a></header>
    <h1>Passo básico de forró</h1><time datetime="2025-10-09T08:53:20.000Z">9 out</time>
  </section>
  <aside><h2>Mais reels</h2>
    <a href="/reel/Mergulho111/"><img alt="Mergulho no mar" data-gd-vis="1" data-gd-w="300"
       srcset="https://scontent.cdninstagram.com/v/mergulho_640.jpg 640w"></a>
    <a href="/kids.fun/reel/Criancas22/"><div><img alt="Crianças brincando" data-gd-vis="1" data-gd-w="300"
       src="https://scontent.cdninstagram.com/v/criancas.jpg" data-gd-nw="640" data-gd-nh="1138"></div></a>
    <a href="/p/OutroPost33/"><video poster="https://scontent.cdninstagram.com/v/outro.jpg" src="blob:https://www.instagram.com/zz"
       data-gd-vis="0"></video></a>
  </aside>
</div></main>
"""


def test_inspect_reel_ignores_suggested_reels_and_returns_own_poster():
    url = f"https://www.instagram.com/reel/{CODE}/"
    info = browse.parse_post(SUGGESTED_REELS_DOM, url, {}, reel_index())
    urls = " ".join(m["url"] + m.get("poster", "") for m in info["media"])
    assert "mergulho" not in urls and "criancas" not in urls and "outro" not in urls and "blob_cover" not in urls
    assert len(info["media"]) == 1 and info["media"][0]["kind"] == "video"
    assert info["type"] == "reel" and info["items_total"] == 1
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["caption"] == "Passo básico de forró" and info["owner"] == "escola.forro"
    assert info["poster"] == COVER and info["media"][0]["poster"] == COVER
    assert info["best"]["url"] == V1080


def test_suggested_links_dropped_even_without_page_data():
    info = browse.parse_post(SUGGESTED_REELS_DOM, f"https://www.instagram.com/reel/{CODE}/", {}, None)
    assert [m["kind"] for m in info["media"]] == ["video"]
    assert info["poster"] == "" and info["best"] is None


def test_post_media_linking_to_itself_is_kept():
    dom = (f'<article><a href="/p/{CODE}/"><img alt="Foto" data-gd-vis="1" data-gd-w="600" '
           f'srcset="https://scontent.cdninstagram.com/v/self_1080.jpg 1080w"></a>'
           '<time datetime="2025-10-09T08:53:20.000Z">9 out</time></article>')
    info = browse.parse_post(dom, f"https://www.instagram.com/p/{CODE}/")
    assert [m["url"] for m in info["media"]] == ["https://scontent.cdninstagram.com/v/self_1080.jpg"]
