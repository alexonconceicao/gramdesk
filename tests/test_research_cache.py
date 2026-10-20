import json
import time

from gramdesk import research_cache as rc

NOW = time.time()
OPTS = {"posts": 30, "comments": True, "commentPosts": 2, "highlights": True}


def _folder(root, name="perfil", stamp="20261020-100000", state="done", age=3600, posts=12, cursor="QVFD",
            feed_done=False, comments=None, highlights=True):
    d = root / f"{name}-{stamp}"
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({
        "username": name, "state": state, "updated_at": NOW - age, "collected_at": NOW - age,
        "resume": {"cursor": cursor, "feed_done": feed_done},
    }))
    (d / "profile.json").write_text(json.dumps({"id": "123", "username": name}))
    (d / "posts.json").write_text(json.dumps(
        [{"pk": str(1000 + i), "code": f"C{i}", "comments": i} for i in range(posts)]))
    (d / "comments.json").write_text(json.dumps(comments or {}))
    if highlights:
        (d / "highlights.json").write_text("[]")
    return d


def test_find_reusable_respects_validity_and_state(tmp_path):
    assert rc.find_reusable(tmp_path, "perfil", NOW) is None
    old = _folder(tmp_path, stamp="20261019-080000", age=30 * 3600)
    assert rc.find_reusable(tmp_path, "perfil", NOW) is None  # passou de 24 h
    _folder(tmp_path, stamp="20261020-090000", state="collecting", age=60)
    assert rc.find_reusable(tmp_path, "perfil", NOW) is None  # em andamento não conta
    new = _folder(tmp_path, stamp="20261020-100000", age=3600)
    assert rc.find_reusable(tmp_path, "perfil", NOW) == new
    assert rc.find_reusable(tmp_path, "perfil", NOW, max_age=48 * 3600) == new
    assert rc.find_reusable(tmp_path, "outro", NOW) is None
    assert rc.find_reusable(tmp_path, "../perfil", NOW) is None
    assert old.exists()


def test_plan_collects_only_missing(tmp_path):
    d = _folder(tmp_path, posts=12, comments={"1011": []})
    p = rc.plan(d, OPTS)
    assert p["have_posts"] == 12 and p["need_posts"] == 18 and p["cursor"] == "QVFD"
    assert p["comments_done"] == ["1011"]
    assert not p["need_highlights"] and not p["complete"]
    payload = rc.resume_payload(p)
    assert payload["need"] == 18 and payload["profile"]["id"] == "123"
    assert "C0" in payload["codes"] and payload["highlightsDone"] is True


def test_plan_complete_when_nothing_missing(tmp_path):
    d = _folder(tmp_path, posts=30, comments={"1029": [], "1028": []})
    p = rc.plan(d, OPTS)
    assert p["need_posts"] == 0 and p["complete"]
    # pedir mais posts ou destaques que faltam reabre a coleta
    assert not rc.plan(d, dict(OPTS, posts=60))["complete"]
    d2 = _folder(tmp_path, stamp="20261020-110000", posts=30, comments={"1029": [], "1028": []}, highlights=False)
    assert not rc.plan(d2, OPTS)["complete"]


def test_plan_feed_exhausted_counts_as_complete(tmp_path):
    d = _folder(tmp_path, posts=5, feed_done=True, comments={"1004": [], "1003": []})
    p = rc.plan(d, OPTS)
    assert p["need_posts"] == 0 and p["complete"]
