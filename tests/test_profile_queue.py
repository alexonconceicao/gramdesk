import json
import os

from gramdesk import profile_queue as pq
from gramdesk.profile_queue import ProfileQueue

GRID = [
    {"code": "AAA111aaa", "url": "https://www.instagram.com/p/AAA111aaa/", "kind": "image"},
    {"code": "BBB222bbb", "url": "https://www.instagram.com/p/BBB222bbb/", "kind": "carousel"},
    {"code": "CCC333ccc", "url": "https://www.instagram.com/reel/CCC333ccc/", "kind": "reel"},
]


def _q(tmp_path, **kw) -> ProfileQueue:
    return ProfileQueue.load(tmp_path / "queues", "acc1", "profa.ana", tmp_path / "dl", **kw)


def test_merge_keeps_grid_order_and_skips_duplicates(tmp_path):
    q = _q(tmp_path)
    assert q.merge(GRID[:2]) == 2
    assert q.merge(GRID) == 1
    assert [i["code"] for i in q.items] == ["AAA111aaa", "BBB222bbb", "CCC333ccc"]


def test_next_task_goes_in_order_and_expands_carousel(tmp_path):
    q = _q(tmp_path)
    q.merge(GRID)
    assert q.next_task() == ("AAA111aaa", GRID[0]["url"], 1)
    q.apply(("AAA111aaa", GRID[0]["url"], 1), pq.OK, {"items_total": 1})
    assert q.next_task() == ("BBB222bbb", GRID[1]["url"], 1)
    q.apply(("BBB222bbb", GRID[1]["url"], 1), pq.OK, {"items_total": 3})
    assert q.next_task() == ("BBB222bbb", GRID[1]["url"], 2)
    q.apply(("BBB222bbb", GRID[1]["url"], 2), pq.OK, {"items_total": 3})
    q.apply(("BBB222bbb", GRID[1]["url"], 3), pq.OK, {"items_total": 3})
    assert q.next_task() == ("CCC333ccc", GRID[2]["url"], 1)
    q.apply(("CCC333ccc", GRID[2]["url"], 1), pq.OK, {"items_total": 1})
    assert q.next_task() is None
    assert q.progress() == (3, 3)


def test_resume_from_saved_state(tmp_path):
    q = _q(tmp_path)
    q.merge(GRID)
    q.apply(("AAA111aaa", GRID[0]["url"], 1), pq.OK, {"items_total": 1})
    q.grid_complete = True
    q.save()
    again = _q(tmp_path)
    assert again.grid_complete
    assert again.next_task() == ("BBB222bbb", GRID[1]["url"], 1)
    assert oct(os.stat(again.path).st_mode & 0o777) == "0o600"


def test_skips_posts_already_downloaded(tmp_path):
    folder = tmp_path / "dl"
    folder.mkdir()
    (folder / "profa.ana_AAA111aaa.jpg").write_bytes(b"x")
    q = _q(tmp_path)
    q.merge(GRID)
    assert q.next_task()[0] == "BBB222bbb"
    assert q.progress() == (1, 3)


def test_item_error_does_not_stop_queue(tmp_path):
    q = _q(tmp_path)
    q.merge(GRID)
    assert q.apply(("AAA111aaa", GRID[0]["url"], 1), pq.ERROR, {}, "Download falhou") == "next"
    assert q.next_task()[0] == "BBB222bbb"
    assert q.errors == {"AAA111aaa": "Download falhou"}
    q.start()  # nova rodada tenta de novo os que falharam
    assert q.next_task()[0] == "AAA111aaa"


def test_limits_and_blocks_pause_without_retry(tmp_path):
    q = _q(tmp_path)
    q.merge(GRID)
    task = q.next_task()
    for code in (pq.COOLDOWN, pq.QUOTA, pq.APP):
        assert q.apply(task, code, {}, "limite") == "pause"
        assert q.next_task() == task  # nada marcado: retoma do mesmo item
    assert q.apply(task, pq.BUSY, {}, "ocupado") == "retry"


def test_filter_only_videos_or_photos(tmp_path):
    v = _q(tmp_path, only="video")
    v.merge(GRID)
    assert v.next_task()[0] == "CCC333ccc"
    assert v.progress() == (0, 1)
    p = ProfileQueue.load(tmp_path / "q2", "acc1", "profa.ana", tmp_path / "dl", only="image")
    p.merge(GRID)
    assert [p.next_task()[0]] == ["AAA111aaa"]
    assert p.progress() == (0, 2)


def test_corrupt_state_starts_fresh(tmp_path):
    q = _q(tmp_path)
    q.path.parent.mkdir(parents=True, exist_ok=True)
    q.path.write_text("{nao json", encoding="utf-8")
    assert _q(tmp_path).items == []
    q.path.write_text(json.dumps({"items": [{"code": "../x", "url": "javascript:1", "kind": "image"}]}), encoding="utf-8")
    assert _q(tmp_path).items == []
