import json
import os
import random
import stat
import sys

import pytest

from gramdesk import follows
from gramdesk.ratelimit import UNFOLLOW_HARD_CAP, Limits, RateState

NOW = 1_790_000_000.0


def u(pk, name, **kw):
    return {"pk": str(pk), "username": name, "full_name": name.title(), **kw}


def snap(st, followers, following, now):
    st.begin_partial(now)
    st.add_page("followers", followers, "", False)
    st.add_page("following", following, "", False)
    return st.finish_snapshot(now)


@pytest.fixture
def st(tmp_path):
    return follows.FollowStore(tmp_path, "acc1")


def test_user_entry_rejects_bad_data():
    assert follows.user_entry(u(1, "ana"))[0] == "1"
    assert follows.user_entry({"pk": "x1", "username": "ana"}) is None
    assert follows.user_entry({"pk": "1", "username": "a b"}) is None
    assert follows.user_entry("nada") is None


def test_partial_pages_follow_cursor(st):
    st.begin_partial(NOW)
    st.add_page("followers", [u(1, "ana")], "QVFD", True)
    assert st.partial["kind"] == "followers" and st.partial["cursor"] == "QVFD"
    st.add_page("following", [u(9, "x")], "", False)  # tipo errado: ignorado
    assert st.partial["following"] == {}
    st.add_page("followers", [u(2, "bia")], "", False)
    assert st.partial["kind"] == "following" and st.partial["cursor"] == ""
    st.add_page("following", [u(3, "caio")], "", False)
    assert st.partial_done


def test_finish_requires_complete(st):
    st.begin_partial(NOW)
    with pytest.raises(ValueError):
        st.finish_snapshot(NOW)


def test_first_snapshot_has_no_losses(st):
    s = snap(st, [u(1, "ana")], [u(1, "ana"), u(2, "bia")], NOW)
    assert s == {"followers": 1, "following": 2, "lost": 0, "gained": 0, "first": True}
    assert st.unfollowers == []


def test_unfollowers_list_is_permanent_and_audited(tmp_path, st):
    snap(st, [u(1, "ana"), u(2, "bia")], [u(1, "ana"), u(2, "bia")], NOW)
    s = snap(st, [u(1, "ana")], [u(1, "ana"), u(2, "bia")], NOW + 86400)
    assert s["lost"] == 1
    assert st.unfollowers[0]["u"] == "bia" and st.unfollowers[0]["i_follow"] is True
    # voltou a seguir: fica na lista com a data de volta
    snap(st, [u(1, "ana"), u(2, "bia")], [u(1, "ana"), u(2, "bia")], NOW + 2 * 86400)
    assert st.unfollowers[0]["back_at"] == NOW + 2 * 86400
    # saiu de novo: conta a segunda vez, sem duplicar
    snap(st, [u(1, "ana")], [u(1, "ana")], NOW + 3 * 86400)
    assert len(st.unfollowers) == 1 and st.unfollowers[0]["times"] == 2 and st.unfollowers[0]["back_at"] == 0.0
    again = follows.FollowStore(tmp_path, "acc1")
    assert again.unfollowers == st.unfollowers
    events = [r["event"] for r in again.read_audit()]
    assert events.count("deixou-de-seguir") == 2 and "voltou-a-seguir" in events and events.count("checagem") == 4


def test_split_famous_common_unknown_and_keep(st):
    snap(st, [u(1, "ana")], [u(1, "ana"), u(2, "star"), u(3, "zeca"), u(4, "novo"), u(5, "amigo")], NOW)
    st.set_count("2", 50_000, NOW)
    st.set_count("3", 120, NOW)
    st.set_count("5", 90, NOW)
    st.set_keep("5", True, NOW)
    g = st.split(10_000)
    assert [x["u"] for x in g["famosos"]] == ["star"]
    assert [x["u"] for x in g["comuns"]] == ["zeca"]
    assert [x["u"] for x in g["sem-contagem"]] == ["novo"]
    assert [x["u"] for x in st.need_counts(NOW)] == ["novo"]
    assert [x["u"] for x in st.need_counts(NOW + follows.COUNT_MAX_AGE + 1)] == ["novo", "star", "zeca"]
    assert st.split(100)["famosos"][0]["u"] == "star" and len(st.split(100)["famosos"]) == 2


def test_batch_skips_keep_and_not_following(st):
    snap(st, [], [u(1, "a"), u(2, "b"), u(3, "c")], NOW)
    st.set_keep("2", True, NOW)
    b = st.new_batch("nao-seguidores", ["1", "2", "3", "99", "1"], 10_000, NOW)
    assert b["items"] == ["1", "3"]
    assert st.next_in_batch(b) == "1"
    st.record_unfollowed("1", NOW, "nao-seguidores")
    b["done"].append("1")
    st.save_batch(b)
    assert st.next_in_batch(st.load_batch()) == "3"
    assert "1" not in st.following
    b["errors"]["3"] = "falhou"
    assert st.next_in_batch(b) is None
    rec = st.read_audit()[0]
    assert rec["event"] == "deixei-de-seguir" and rec["user"] == "a"
    with pytest.raises(ValueError):
        st.new_batch("tudo", ["3"], 1, NOW)


def test_files_are_private_and_corruption_tolerated(tmp_path, st):
    snap(st, [u(1, "ana")], [u(1, "ana")], NOW)
    st.audit("manter", NOW, user="ana", link="https://x")
    if sys.platform != "win32":
        for p in (st.state_path, st.unf_path, st.audit_path):
            assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert "link" not in st.read_audit()[0]
    st.state_path.write_text("{lixo")
    st.unf_path.write_text(json.dumps([{"pk": "x"}, {"pk": "7", "u": "ok"}]))
    again = follows.FollowStore(tmp_path, "acc1")
    assert again.followers == {} and [e["u"] for e in again.unfollowers] == ["ok"]
    with pytest.raises(ValueError):
        follows.FollowStore(tmp_path, "../x")


def test_check_due(st):
    assert not st.check_due(NOW)  # sem checagem manual, a automática não roda
    snap(st, [], [], NOW)
    assert not st.check_due(NOW + 3600) and st.check_due(NOW + 21 * 3600)


# ---------------------------------------------------------------- ritmo das remoções
def test_unfollow_pacing_and_long_pause(tmp_path):
    rs = RateState(tmp_path / "r.json")
    lim = Limits()
    rng = random.Random(3)
    nxt = rs.record_unfollow("a1", lim, NOW, rng=rng)
    assert 30 <= nxt - NOW <= 60
    chk = rs.unfollow_check("a1", lim, NOW + 5)
    assert chk["ok"] and chk["wait"] > 20
    t = NOW
    for _ in range(lim.unfollow_long_every - 1):
        t += 70
        last = rs.record_unfollow("a1", lim, t, rng=rng)
    assert last - t >= 180  # 10ª remoção: pausa longa


def test_unfollow_hourly_and_daily_caps(tmp_path):
    rs = RateState(tmp_path / "r.json")
    lim = Limits.from_dict({"unfollow_per_hour": 5, "unfollow_daily": 10})
    for i in range(5):
        rs.record_unfollow("a1", lim, NOW + i)
    chk = rs.unfollow_check("a1", lim, NOW + 100)
    assert chk["ok"] and chk["wait"] > 3000  # espera a janela da hora
    for i in range(5):
        rs.record_unfollow("a1", lim, NOW + 4000 + i)
    chk = rs.unfollow_check("a1", lim, NOW + 9000)
    assert not chk["ok"] and chk["reason"] == "unfollows"


def test_unfollow_respects_cooldown_and_hard_cap(tmp_path):
    rs = RateState(tmp_path / "r.json")
    rs.record_block("a1", "rate-limit", NOW)
    assert rs.unfollow_check("a1", Limits(), NOW + 60)["reason"] == "cooldown"
    assert Limits.from_dict({"unfollow_daily": 10_000}).unfollow_daily == UNFOLLOW_HARD_CAP
    lim = Limits.from_dict({"unfollow_min_delay": 1, "unfollow_max_delay": 1, "famous_threshold": 5})
    assert lim.unfollow_min_delay == 20 and lim.unfollow_max_delay >= 25 and lim.famous_threshold == 1000


def test_unfollow_state_survives_reload(tmp_path):
    rs = RateState(tmp_path / "r.json")
    rs.record_unfollow("a1", Limits(), NOW)
    again = RateState(tmp_path / "r.json")
    assert again.unfollow_status("a1", Limits(), NOW + 10)["unfollows_today"] == 1


def test_selection_persists_and_respects_keep(tmp_path, st):
    snap(st, [], [u(1, "a"), u(2, "b"), u(3, "star")], NOW)
    st.set_selected(["1", "3", "99"], True)
    assert st.selected == {"1", "3"}
    assert follows.FollowStore(tmp_path, "acc1").selected == {"1", "3"}
    assert st.set_keep_many(["3", "2"], True, NOW) == 2
    assert st.selected == {"1"} and st.keep == {"2", "3"}
    st.set_selected(["3"], True)  # mantido não entra na seleção
    assert st.selected == {"1"}
    st.record_unfollowed("1", NOW)
    assert st.selected == set()


def test_extend_and_remove_from_batch(st):
    snap(st, [], [u(1, "a"), u(2, "b"), u(3, "c"), u(4, "d")], NOW)
    st.set_keep("4", True, NOW)
    b = st.new_batch("selecao", ["1"], 10_000, NOW)
    assert st.extend_batch(b, ["1", "2", "3", "4", "99"]) == 2
    assert b["items"] == ["1", "2", "3"]
    assert st.batch_status(b, "2") == "pendente" and st.batch_status(b, "4") == ""
    assert st.remove_from_batch(b, "2") and st.load_batch()["items"] == ["1", "3"]
    b["done"].append("1")
    assert not st.remove_from_batch(b, "1") and st.batch_status(b, "1") == "removido"
