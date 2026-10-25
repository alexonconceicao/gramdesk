from gramdesk import follows_report

ROWS = [{"pk": "1", "u": "ana", "n": "Ana, a", "v": True, "p": False, "a": False, "count": None, "ghost": 0,
         "ghost_level": "", "keep": False}]


def test_csv_has_bom_header_and_quotes():
    out = follows_report.to_csv(ROWS, {"1": "pendente"})
    assert out.startswith("﻿usuario,nome,id")
    assert '"Ana, a"' in out and "pendente" in out


def test_csv_unfollowers_layout():
    out = follows_report.to_csv([{"pk": "2", "u": "bia", "n": "", "lost_at": 1_790_000_000, "back_at": 0, "times": 2,
                                  "i_follow": True}])
    assert "deixou_em" in out.splitlines()[0] and ",2,sim" in out


def test_report_escapes_and_draws_chart():
    rows = [dict(ROWS[0], u="x<script>", n="<b>")]
    html = follows_report.report_html("Conta <1>", {"followers": 10}, [[1, 5, 6], [2, 7, 6]],
                                      {"comuns": rows}, [], {"suspeito": 2})
    assert "<script>" not in html.split("<style>")[1] and "&lt;b&gt;" in html and "<polyline" in html
    assert "suspeito: 2" in html
    assert "segunda checagem" in follows_report.history_svg([[1, 1, 1]])
