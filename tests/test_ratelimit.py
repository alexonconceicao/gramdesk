import json
import os
import random
import time

import pytest

from gramdesk import ratelimit
from gramdesk.ratelimit import Limits, Pacer, RateState, ResearchLog

NOW = time.mktime((2026, 10, 20, 14, 0, 0, 0, 0, -1))


def _run(pacer: Pacer, n: int, start: float = NOW) -> list[float]:
    """Simula n requisições seguindo exatamente o que o pacer manda esperar."""
    t, times = start, []
    for _ in range(n):
        d = pacer.decide(t)
        assert not d.stop
        t += d.wait
        pacer.record(t)
        times.append(t)
    return times


# ---------------------------------------------------------------- ritmo
def test_gaps_respect_min_delay_with_jitter():
    lim = Limits()
    times = _run(Pacer(lim, rng=random.Random(1)), 40)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= lim.min_delay
    assert len({round(g, 3) for g in gaps}) > 30  # jitter: intervalos não se repetem


def test_long_pause_every_n_items():
    lim = Limits(per_minute=12, per_hour=150)
    times = _run(Pacer(lim, rng=random.Random(2)), 25)
    gaps = [b - a for a, b in zip(times, times[1:])]
    long_gaps = [i for i, g in enumerate(gaps) if g >= lim.long_min]
    # gap i leva à requisição i+1; a pausa vem depois de cada bloco de long_every
    assert [i + 1 for i in long_gaps] == [8, 16, 24]


def test_per_minute_cap():
    lim = Limits(min_delay=2.0, max_delay=2.5, long_every=30, per_minute=5, per_hour=150)
    times = _run(Pacer(lim, rng=random.Random(3)), 30)
    for t in times:
        assert sum(1 for x in times if t - 60 < x <= t) <= 5


def test_per_hour_cap_stops_job_instead_of_long_wait():
    lim = Limits(per_hour=10, per_minute=10, min_delay=2.0, max_delay=2.5, long_every=30)
    recent = [NOW - 600 + i for i in range(10)]  # 10 requisições nos últimos 10 min
    d = Pacer(lim, recent=recent, rng=random.Random(4)).decide(NOW)
    assert d.stop == "hora"


def test_daily_items_budget_stops():
    p = Pacer(Limits(), items_left=2, rng=random.Random(5))
    _run(p, 2)
    assert p.decide(NOW + 999).stop == "dia"


def test_limits_are_clamped_and_coherent():
    lim = Limits.from_dict({"min_delay": 0.1, "max_delay": 0.2, "per_hour": 10_000, "daily_researches": True, "per_minute": 50})
    assert lim.min_delay == 2.0 and lim.max_delay >= lim.min_delay + 0.5
    assert lim.per_hour == 150 and lim.per_minute <= lim.per_hour
    assert lim.daily_researches == 3  # booleano é ignorado
    assert Limits.from_dict(None) == Limits()


# ---------------------------------------------------------------- backoff
def test_backoff_exponential_with_cap():
    mins = [ratelimit.cooldown_seconds(n) // 60 for n in range(0, 9)]
    assert mins == [0, 30, 60, 120, 240, 480, 960, 1440, 1440]


@pytest.mark.parametrize("status,url,text,kind", [
    (429, "", "", "rate-limit"),
    (400, "", '{"message":"Please wait a few minutes before you try again.","status":"fail"}', "rate-limit"),
    (200, "", "Tente novamente mais tarde. Restringimos determinadas atividades", "rate-limit"),
    (400, "", "feedback_required", "rate-limit"),
    (400, "", "checkpoint_required", "checkpoint"),
    (200, "https://www.instagram.com/challenge/", "", "checkpoint"),
    (200, "https://www.instagram.com/accounts/login/", "", "login"),
    (401, "", "", "login"),
    (403, "", '{"message":"login_required"}', "login"),
    (500, "", "erro interno", None),
    (404, "", "", None),
])
def test_classify_block(status, url, text, kind):
    assert ratelimit.classify_block(status, url, text) == kind


# ---------------------------------------------------------------- persistência
def test_cooldown_persists_grows_and_resets(tmp_path):
    path = tmp_path / "data" / "ratelimit.json"
    st = RateState(path)
    lim = Limits()
    until1 = st.record_block("acc1", "rate-limit", NOW)
    assert until1 == NOW + 30 * 60
    if os.name != "nt":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    # outro processo (ou reinício do app) enxerga o resfriamento
    st2 = RateState(path)
    chk = st2.check("acc1", lim, NOW + 60)
    assert not chk["ok"] and chk["reason"] == "cooldown" and "aguarde até" in chk["message"]
    assert st2.check("acc2", lim, NOW + 60)["ok"]  # por conta
    # novo bloqueio depois de liberar: dobra
    until2 = st2.record_block("acc1", "rate-limit", until1 + 10)
    assert until2 == until1 + 10 + 3600
    assert RateState(path).status("acc1", lim, until1 + 20)["level"] == 2
    # sucesso zera
    st2.record_success("acc1", until2 + 5)
    st3 = RateState(path)
    assert st3.check("acc1", lim, until2 + 6)["ok"]
    st3.record_block("acc1", "checkpoint", until2 + 10)
    assert RateState(path).status("acc1", lim, until2 + 11)["cooldown_until"] == until2 + 10 + 1800


def test_corrupt_state_file_is_tolerated(tmp_path):
    path = tmp_path / "ratelimit.json"
    path.write_text("{nao json")
    st = RateState(path)
    assert st.check("a", Limits(), NOW)["ok"]
    path.write_text(json.dumps({"accounts": {"../x": {"level": 3}, "ok": {"level": "x", "cooldown_until": NOW + 50}}}))
    st = RateState(path)
    assert "../x" not in st.data["accounts"]
    assert not st.check("ok", Limits(), NOW)["ok"]


# ---------------------------------------------------------------- cota diária
def test_daily_research_quota_and_reset_next_day(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits(daily_researches=3)
    for _ in range(3):
        assert st.check("a", lim, NOW)["ok"]
        st.begin("a", NOW)
    chk = st.check("a", lim, NOW)
    assert not chk["ok"] and chk["reason"] == "researches"
    assert chk["until"] == ratelimit.next_midnight(NOW)
    assert RateState(tmp_path / "r.json").check("a", lim, NOW + 86400)["ok"]


def test_daily_item_quota_and_hour_window(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits(daily_items=30)
    for i in range(30):
        st.record_request("a", NOW + i * 3)
    chk = st.check("a", lim, NOW + 100)
    assert not chk["ok"] and chk["reason"] == "items"
    s = RateState(tmp_path / "r.json").status("a", lim, NOW + 100)
    assert s["items_today"] == 30 and s["items_last_hour"] == 30
    assert RateState(tmp_path / "r.json").status("a", lim, NOW + 4000)["items_last_hour"] == 0
    # o pacer de uma nova coleta herda a janela da última hora e o saldo do dia
    p = RateState(tmp_path / "r.json").pacer("a", Limits(daily_items=40), NOW + 100)
    assert p.items_left == 10 and len(p.recent) == 30


# ---------------------------------------------------------------- log
def test_log_has_no_sensitive_data(tmp_path):
    log = ResearchLog(tmp_path / "research.log")
    log.write("bloqueio", account="Pessoal", kind="rate-limit", sessionid="abc", csrftoken="x",
              cookie="c", url="https://x", note="veja https://evil", status=429, options={"posts": 60, "token": "t"})
    line = (tmp_path / "research.log").read_text()
    rec = json.loads(line)
    assert rec["event"] == "bloqueio" and rec["kind"] == "rate-limit" and rec["status"] == 429
    for bad in ("abc", "csrftoken", "cookie", "https://", "token"):
        assert bad not in line
    if os.name != "nt":
        assert oct(os.stat(tmp_path / "research.log").st_mode & 0o777) == "0o600"



def test_rotacao_do_cookie_de_sessao_mantem_login():
    """Instagram adiciona o sessionid novo antes de remover o antigo: continua logado."""
    from gramdesk.session_cookies import track_session

    s: set = set()
    assert track_session(s, b"sessionid", ".instagram.com", "/", b"velho", True)
    assert track_session(s, b"sessionid", ".instagram.com", "/", b"novo", True)
    assert track_session(s, b"sessionid", ".instagram.com", "/", b"velho", False)
    assert not track_session(s, b"sessionid", ".instagram.com", "/", b"novo", False)
    assert not track_session(set(), b"csrftoken", ".instagram.com", "/", b"x", True)
    # sessionid pode não chegar ao app (HttpOnly): ds_user_id basta.
    assert track_session(set(), b"ds_user_id", ".instagram.com", "/", b"123", True)


def test_link_externo_do_instagram_abre_fora():
    from gramdesk.security import unwrap_link_shim

    assert unwrap_link_shim("https://l.instagram.com/?u=https%3A%2F%2Fwa.me%2F5571999&e=AT0") == "https://wa.me/5571999"
    assert unwrap_link_shim("https://www.instagram.com/perfil/") is None
    assert unwrap_link_shim("https://l.instagram.com/?u=javascript%3Aalert(1)") is None


# ---------------------------------------------------------------- desacelerar em vez de parar
def test_soft_backoff_grows_then_gives_up():
    from gramdesk.ratelimit import SOFT_BACKOFF, soft_backoff

    rng = random.Random(1)
    waits = [soft_backoff(n, rng) for n in range(1, len(SOFT_BACKOFF) + 1)]
    assert all(0.8 * b <= w <= 1.2 * b for w, b in zip(waits, SOFT_BACKOFF))
    assert waits[0] >= 240 and soft_backoff(len(SOFT_BACKOFF) + 1) is None and soft_backoff(0) is None


def test_soft_hits_slow_next_pacer(tmp_path):
    st = RateState(tmp_path / "r.json")
    lim = Limits()
    assert st.pacer("a1", lim, NOW).slow == 1.0
    assert st.record_soft("a1", NOW) == 2.0
    st.record_soft("a1", NOW + 10)
    again = RateState(tmp_path / "r.json")
    p = again.pacer("a1", lim, NOW + 60, rng=random.Random(2))
    assert p.slow == 4.0
    p.record(NOW + 60)
    assert p.decide(NOW + 60).wait >= lim.min_delay * 4
    assert again.slow_factor("a1", NOW + 7 * 3600) == 1.0  # passa depois de 6 h
    assert st.check("a1", lim, NOW + 20)["ok"]  # aviso tratado com espera não gera resfriamento
