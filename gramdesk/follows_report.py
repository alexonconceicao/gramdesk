"""Exportação CSV e relatório HTML da tela de Seguidores. Puro (sem Qt), testável."""

from __future__ import annotations

import csv
import html
import io
import time

CSV_FIELDS = ("usuario", "nome", "id", "verificado", "privado", "sem_foto", "seguidores", "fantasma", "nivel",
              "mantido", "estado")


def to_csv(rows: list[dict], states: dict[str, str] | None = None) -> str:
    """Linhas da tela -> CSV (UTF-8 com BOM para abrir certo em planilhas)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows and "lost_at" in rows[0]:
        w.writerow(("usuario", "nome", "id", "deixou_em", "voltou_em", "vezes", "voce_segue"))
        for e in rows:
            w.writerow((e["u"], e.get("n", ""), e["pk"], _date(e.get("lost_at")), _date(e.get("back_at")),
                        e.get("times", 1), "sim" if e.get("i_follow") else "não"))
    else:
        w.writerow(CSV_FIELDS)
        for u in rows:
            w.writerow((u["u"], u.get("n", ""), u["pk"], _yn(u.get("v")), _yn(u.get("p")), _yn(u.get("a")),
                        "" if u.get("count") is None else u["count"], u.get("ghost", ""), u.get("ghost_level", ""),
                        _yn(u.get("keep")), (states or {}).get(u["pk"], "")))
    return "\ufeff" + buf.getvalue()


def _yn(v) -> str:
    return "sim" if v else "não"


def _date(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def history_svg(history: list[list], width: int = 760, height: int = 240) -> str:
    """Gráfico de linhas (seguidores e seguindo por checagem) em SVG inline."""
    pts = [h for h in history if h[0] > 0][-200:]
    if len(pts) < 2:
        return "<p class='muted'>O gráfico aparece a partir da segunda checagem.</p>"
    pad = 36
    t0, t1 = pts[0][0], pts[-1][0]
    vals = [v for h in pts for v in (h[1], h[2])]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi += 1

    def xy(t, v):
        x = pad + (t - t0) / max(1.0, t1 - t0) * (width - 2 * pad)
        y = height - pad - (v - lo) / (hi - lo) * (height - 2 * pad)
        return f"{x:.1f},{y:.1f}"

    lines = []
    for idx, color, label in ((1, "#d62976", "seguidores"), (2, "#4f8cff", "seguindo")):
        poly = " ".join(xy(h[0], h[idx]) for h in pts)
        lines.append(f"<polyline fill='none' stroke='{color}' stroke-width='2.5' points='{poly}'/>")
        last = pts[-1]
        lines.append(f"<text x='{width - pad + 4}' y='{xy(last[0], last[idx]).split(',')[1]}' fill='{color}' "
                     f"font-size='12'>{last[idx]} {label}</text>")
    axis = (f"<text x='{pad}' y='{height - 8}' font-size='11' fill='#888'>{_date(t0)[:10]}</text>"
            f"<text x='{width - pad}' y='{height - 8}' font-size='11' fill='#888' text-anchor='end'>{_date(t1)[:10]}</text>"
            f"<text x='4' y='{pad}' font-size='11' fill='#888'>{hi}</text>"
            f"<text x='4' y='{height - pad}' font-size='11' fill='#888'>{lo}</text>")
    return (f"<svg viewBox='0 0 {width + 90} {height}' width='100%' xmlns='http://www.w3.org/2000/svg'>"
            f"{axis}{''.join(lines)}</svg>")


def report_html(account: str, summary: dict, history: list[list], groups: dict[str, list[dict]],
                unfollowers: list[dict], ghosts: dict[str, int], now: float | None = None) -> str:
    now = time.time() if now is None else now
    esc = html.escape

    def table(title: str, rows: list[dict], limit: int = 50) -> str:
        if not rows:
            return f"<h3>{esc(title)}</h3><p class='muted'>Ninguém.</p>"
        body = "".join(
            f"<tr><td>@{esc(u['u'])}</td><td>{esc(u.get('n', ''))}</td>"
            f"<td>{'' if u.get('count') is None else u['count']}</td><td>{esc(u.get('ghost_level', ''))}</td></tr>"
            for u in rows[:limit])
        more = f"<p class='muted'>... e mais {len(rows) - limit}.</p>" if len(rows) > limit else ""
        return (f"<h3>{esc(title)} ({len(rows)})</h3><table><tr><th>Usuário</th><th>Nome</th><th>Seguidores</th>"
                f"<th>Fantasma</th></tr>{body}</table>{more}")

    lost = "".join(f"<tr><td>@{esc(e['u'])}</td><td>{esc(e.get('n', ''))}</td><td>{_date(e['lost_at'])}</td>"
                   f"<td>{_date(e.get('back_at'))}</td></tr>" for e in sorted(unfollowers, key=lambda e: -e["lost_at"])[:100])
    cards = "".join(f"<div class='card'><b>{v}</b><span>{esc(k)}</span></div>" for k, v in (
        ("seguidores", summary.get("followers", 0)), ("seguindo", summary.get("following", 0)),
        ("não seguem de volta", summary.get("non_followers", 0)), ("famosos", summary.get("famous", 0)),
        ("mútuos", summary.get("mutuals", 0)), ("fãs", summary.get("fans", 0)),
        ("deixaram de seguir", summary.get("unfollowers", 0)), ("mantidos", summary.get("keep", 0))))
    gh = " · ".join(f"{esc(k)}: {v}" for k, v in ghosts.items())
    return f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<title>Relatório de seguidores: {esc(account)}</title>
<style>
body{{font:15px system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 16px;color:#16161d}}
h1{{margin-bottom:4px}} .muted{{color:#777}} table{{border-collapse:collapse;width:100%;margin:8px 0 18px}}
td,th{{text-align:left;padding:5px 8px;border-bottom:1px solid #eee;font-size:13.5px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}
.card{{background:#f5f5f8;border-radius:12px;padding:12px}} .card b{{display:block;font-size:22px}}
.card span{{color:#666;font-size:12.5px}}
@media print{{.card{{border:1px solid #ddd}}}}
</style>
<h1>Relatório de seguidores</h1>
<p class="muted">Conta {esc(account)} · gerado em {_date(now)} · última checagem {_date(summary.get('taken_at'))}</p>
<div class="cards">{cards}</div>
<h2>Evolução</h2>{history_svg(history)}
<h2>Contas suspeitas entre os seus seguidores</h2><p>{gh or 'nenhuma'}</p>
<h2>Listas</h2>
{table('Não seguem de volta (comuns)', groups.get('comuns', []))}
{table('Não seguem de volta (famosos)', groups.get('famosos', []))}
{table('Fãs: seguem você e você não segue', groups.get('fas', []))}
<h2>Deixaram de seguir</h2>
<table><tr><th>Usuário</th><th>Nome</th><th>Saiu em</th><th>Voltou em</th></tr>{lost}</table>
<p class="muted">Gerado pelo Gramdesk, só com dados locais. Para PDF, use Imprimir > Salvar como PDF.</p>
</html>"""
