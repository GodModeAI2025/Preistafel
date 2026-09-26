#!/usr/bin/env python3
"""Messlauf auswerten: Kosten in Regionswährung, Erfolgsquote, Abo-Verbrauch.

  report.py --run ~/herdr-bench-runs/<run_id> --prices prices/<datum>_<region>.json [--open]

Schreibt ~/herdr-bench-runs/<run_id>/report.json und report.html (ohne externe Ressourcen).
"""
from __future__ import annotations

import argparse
import html
import json
import math
import statistics as st
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage as U  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PRICE_MAP = {"input_uncached": "input", "cache_read": "cache_read", "cache_write_5m": "cache_write_5m",
             "cache_write_1h": "cache_write_1h", "output": "output"}  # reasoning steckt in output


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def trial_cost(usage: dict, prices: dict) -> tuple[float | None, list[str]]:
    total, missing = 0.0, []
    for model, toks in (usage or {}).get("per_model", {}).items():
        p = prices["models"].get(model)
        if not p:
            missing.append(model)
            continue
        pm = p["local_per_mtok"]
        for cat, field in PRICE_MAP.items():
            n = toks.get(cat, 0)
            if n and pm.get(field) is None:
                field = "cache_write_5m" if field == "cache_write_1h" else field
            total += n * (pm.get(field) or 0) / 1e6
    return (None if missing and total == 0 else round(total, 6)), missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--prices", required=True)
    a = ap.parse_args()
    run = Path(a.run)
    prices = json.loads(Path(a.prices).read_text())
    plans = tomllib.loads((ROOT / "config/plans.toml").read_text())
    plan = json.loads((run / "plan.json").read_text())
    floor = plan["defaults"].get("quality_floor", 0.8)
    cur, fx = prices["currency"], prices["fx_usd_to_local"]
    trials = [json.loads(p.read_text()) for p in sorted((run / "trials").glob("*.json"))]

    # Abo-Anteil je Trial
    share: dict[str, dict] = {}
    blocks = run / "blocks.jsonl"
    if blocks.exists():
        for line in blocks.read_text().splitlines():
            b = json.loads(line)
            n = max(len(b["trial_ids"]), 1)
            for tid in b["trial_ids"]:
                share[tid] = {k: (None if v is None else v / n) for k, v in (b.get("delta") or {}).items()}
    for t in trials:
        if t.get("quota_delta"):
            share[t["trial_id"]] = t["quota_delta"]

    cohorts = {}
    for tdir in (ROOT / "tasks").iterdir():
        if (tdir / "task.toml").exists():
            tt = tomllib.loads((tdir / "task.toml").read_text())
            cohorts[tdir.name] = tt.get("cohort", "allgemein")
    for t in trials:
        t["cohort"] = cohorts.get(t["task"], "allgemein")
        t["cost"], t["cost_missing"] = trial_cost(t.get("usage"), prices)
        t["tokens"] = {c: sum(v.get(c, 0) for v in (t.get("usage") or {}).get("per_model", {}).values()) for c in U.CATS}
        t["passed"] = bool((t.get("verify") or {}).get("passed")) and t.get("status") == "completed"
        t["share"] = share.get(t["trial_id"], {})

    valid = [t for t in trials if t.get("status") not in ("rate_limited", "error")]
    rows = []
    for cid, cfg in plan["configs"].items():
        ts = [t for t in valid if t["config"] == cid]
        if not ts:
            continue
        k, n = sum(t["passed"] for t in ts), len(ts)
        p, lo, hi = wilson(k, n)
        costs = [t["cost"] for t in ts if t["cost"] is not None]
        csum = sum(costs) if costs else None
        wk = [t["share"].get("week") for t in ts if t["share"].get("week") is not None]
        fh = [t["share"].get("five_hour") for t in ts if t["share"].get("five_hour") is not None]
        week_share = st.mean(wk) if wk else None
        plan_usd = plans.get(cfg["plan"], {}).get("usd_month")
        plan_week_local = plan_usd * 12 / 52 * fx if plan_usd else None
        mean_cost = st.mean(costs) if costs else None
        rows.append({
            "config": cid, "harness": cfg["harness"], "model": cfg["model"], "effort": cfg["effort"], "plan": cfg["plan"],
            "n": n, "passed": k, "pass_rate": p, "ci": [lo, hi],
            "mean_cost": mean_cost, "cost_per_solved": (csum / k) if (csum is not None and k) else None,
            "median_tokens": st.median([sum(t["tokens"][c] for c in PRICE_MAP) for t in ts]),
            "median_wall_s": st.median([t.get("wall_s", 0) for t in ts]),
            "week_pct_per_task": week_share, "five_hour_pct_per_task": st.mean(fh) if fh else None,
            "tasks_per_week_at_limit": (100 / week_share) if week_share else None,
            "abo_cost_per_task": (plan_week_local * week_share / 100) if (plan_week_local and week_share) else None,
            "break_even_tasks_per_week": (plan_week_local / mean_cost) if (plan_week_local and mean_cost) else None,
            "blocked": sum(t.get("status") == "blocked" for t in ts), "timeouts": sum(t.get("status") == "timeout" for t in ts),
        })
    for r in rows:  # gesichert nur, wenn auch die Untergrenze des Intervalls die Schwelle hält
        r["confidence"] = "gesichert" if r["ci"][0] >= floor else "vorläufig"
    eligible = [r for r in rows if r["pass_rate"] >= floor and r["cost_per_solved"] is not None]
    best = min(eligible, key=lambda r: r["cost_per_solved"]) if eligible else None
    report = {"run_id": plan["run_id"], "region": prices["region"], "currency": cur, "fx": fx,
              "prices_source": prices.get("source"), "prices_commit": prices.get("source_commit"),
              "prices_fetched": prices.get("fetched_at"), "quota_mode": plan["quota_mode"], "quality_floor": floor,
              "recommendation": best["config"] if best else None, "configs": rows,
              "excluded": {"rate_limited": sum(t.get("status") == "rate_limited" for t in trials),
                           "error": sum(t.get("status") == "error" for t in trials)},
              "trials": [{k: t.get(k) for k in ("trial_id", "config", "task", "cohort", "harness", "model", "effort", "plan",
                                                  "rep", "status", "passed", "cost",
                                                  "cost_missing", "tokens", "wall_s", "share")} for t in trials]}
    (run / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (run / "report.html").write_text(render(report))
    print(run / "report.html")
    if best:
        print(f"Empfehlung ({best['confidence']}): {best['config']} – {best['pass_rate']:.0%} bestanden, "
              f"{best['cost_per_solved']:.3f} {cur} pro gelöster Aufgabe ({prices['region']})")
    return 0


# ------------------------------------------------------------------ HTML
def fmt(v, d=2, suf=""):
    return "–" if v is None else f"{v:,.{d}f}{suf}".replace(",", " ")


def scatter(rows: list[dict], cur: str) -> str:
    pts = [r for r in rows if r["cost_per_solved"] is not None]
    if not pts:
        return "<p class=muted>Keine Kostendaten.</p>"
    W, H, L, B = 640, 340, 64, 44
    xmax = max(r["cost_per_solved"] for r in pts) * 1.15 or 1
    X = lambda v: L + (W - L - 16) * v / xmax  # noqa: E731
    Y = lambda p: H - B - (H - B - 16) * p  # noqa: E731
    g = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Kosten pro gelöster Aufgabe gegen Erfolgsquote">']
    for i in range(0, 6):
        y = Y(i / 5)
        g.append(f'<line x1="{L}" x2="{W-16}" y1="{y}" y2="{y}" class=grid /><text x="{L-8}" y="{y+4}" class=ax text-anchor=end>{i*20}%</text>')
    for i in range(0, 5):
        v = xmax * i / 4
        g.append(f'<text x="{X(v)}" y="{H-B+18}" class=ax text-anchor=middle>{v:.2f}</text>')
    g.append(f'<text x="{(W+L)/2}" y="{H-6}" class=ax text-anchor=middle>{cur} pro gelöster Aufgabe (API-Äquivalent)</text>')
    for r in pts:
        cls = "cc" if r["harness"] == "claude" else "cx"
        x, y = X(r["cost_per_solved"]), Y(r["pass_rate"])
        g.append(f'<line x1="{x}" x2="{x}" y1="{Y(r["ci"][0])}" y2="{Y(r["ci"][1])}" class="err {cls}" />')
        g.append(f'<circle cx="{x}" cy="{y}" r="6" class="{cls}"><title>{html.escape(r["config"])}</title></circle>')
        g.append(f'<text x="{x+9}" y="{y-8}" class=lbl>{html.escape(r["config"])}</text>')
    g.append("</svg>")
    return "".join(g)


def render(rep: dict) -> str:
    cur = rep["currency"]
    rows = sorted(rep["configs"], key=lambda r: (r["cost_per_solved"] is None, r["cost_per_solved"] or 0))
    tr = []
    for r in rows:
        star = " ★" if r["config"] == rep["recommendation"] else ""
        tr.append(
            f"<tr><td><b>{html.escape(r['config'])}</b>{star}<br><span class=muted>{r['harness']} · {r['model']} · {r['effort']}</span></td>"
            f"<td>{r['passed']}/{r['n']} ({r['pass_rate']:.0%})<br><span class=muted>{r['ci'][0]:.0%}–{r['ci'][1]:.0%}</span></td>"
            f"<td>{fmt(r['mean_cost'], 3)}</td><td><b>{fmt(r['cost_per_solved'], 3)}</b></td>"
            f"<td>{fmt(r['median_tokens'] / 1000, 1)} k</td><td>{fmt(r['median_wall_s'], 0)} s</td>"
            f"<td>{fmt(r['five_hour_pct_per_task'], 2, ' %')}<br>{fmt(r['week_pct_per_task'], 2, ' %')}</td>"
            f"<td>{fmt(r['tasks_per_week_at_limit'], 0)}</td><td>{fmt(r['abo_cost_per_task'], 3)}</td>"
            f"<td>{fmt(r['break_even_tasks_per_week'], 0)}</td>"
            f"<td>{r['blocked']}/{r['timeouts']}</td></tr>")
    trials = []
    for t in sorted(rep["trials"], key=lambda t: (t["config"], t["task"], t["rep"])):
        tok = t.get("tokens") or {}
        trials.append(
            f"<tr><td>{html.escape(t['config'])}</td><td>{html.escape(t['task'])} r{t['rep']}</td><td>{t['status']}</td>"
            f"<td>{'✓' if t['passed'] else '✗'}</td><td>{fmt(t['cost'], 4)}</td>"
            f"<td>{fmt(tok.get('input_uncached', 0) / 1000, 1)} / {fmt(tok.get('cache_read', 0) / 1000, 1)} / "
            f"{fmt((tok.get('cache_write_5m', 0) + tok.get('cache_write_1h', 0)) / 1000, 1)} / {fmt(tok.get('output', 0) / 1000, 1)}</td>"
            f"<td>{fmt(t.get('wall_s'), 0)}</td></tr>")
    rec = next((r for r in rows if r["config"] == rep["recommendation"]), None)
    head = (f"<b>{html.escape(rec['config'])}</b> löst {rec['pass_rate']:.0%} der Aufgaben für "
            f"<b>{fmt(rec['cost_per_solved'], 3)} {cur}</b> pro gelöster Aufgabe (API-Äquivalent, Region {rep['region']}). "
            f"<span class=muted>Empfehlung {rec['confidence']}"
            + (" – für eine gesicherte Aussage mehr Wiederholungen messen." if rec['confidence'] == 'vorläufig' else ".") + "</span>"
            if rec else f"Keine Konfiguration erreicht die Qualitätsschwelle von {rep['quality_floor']:.0%}.")
    return f"""<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>herdr-bench {html.escape(rep['run_id'])}</title><style>
:root{{--bg:#fbfaf7;--fg:#1c1b19;--mut:#6d6a63;--line:#e4e0d8;--cc:#c8633a;--cx:#1f7a74;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16161a;--fg:#ecebe6;--mut:#9a978f;--line:#2c2c31;--cc:#e58a5f;--cx:#4fb3aa;--card:#1d1d22}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:auto;padding:32px 20px}} h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:17px;margin:32px 0 10px}}
.muted{{color:var(--mut);font-size:13px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:13.5px}} th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:12px}} svg{{width:100%;height:auto}} .grid{{stroke:var(--line)}} .ax,.lbl{{fill:var(--mut);font-size:11px}} .lbl{{fill:var(--fg)}}
circle.cc{{fill:var(--cc)}} circle.cx{{fill:var(--cx)}} .err{{stroke-width:2;opacity:.5}} .err.cc{{stroke:var(--cc)}} .err.cx{{stroke:var(--cx)}}
.key span{{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 6px 0 14px}}
</style></head><body><main>
<h1>Preissignal · Lauf {html.escape(rep['run_id'])}</h1>
<p class=muted>Region {rep['region']} · Währung {cur} · Preise {html.escape(str(rep['prices_fetched']))} (LiteLLM {html.escape(str(rep['prices_commit'] or '')[:10])}) · Kontingent-Modus {rep['quota_mode']} · ausgeschlossen: {rep['excluded']['rate_limited']} Limit, {rep['excluded']['error']} Fehler</p>
<div class=card><p style="margin:0;font-size:17px">{head}</p></div>
<h2>Kosten gegen Qualität</h2><div class=card>{scatter(rows, cur)}
<p class="muted key"><span style="background:var(--cc)"></span>Claude Code<span style="background:var(--cx)"></span>Codex · Linien = 95-%-Intervall der Erfolgsquote</p></div>
<h2>Konfigurationen</h2><div class="card wrap"><table><thead><tr><th>Konfiguration</th><th>Bestanden</th><th>Ø Kosten/Trial ({cur})</th><th>Kosten/gelöst ({cur})</th><th>Median Tokens</th><th>Median Zeit</th><th>Abo je Aufgabe<br>5 h / Woche</th><th>Aufgaben/Woche bis Limit</th><th>Abo-Äquivalent/Aufgabe ({cur})</th><th>Break-even (Aufgaben/Woche)</th><th>blocked/Timeout</th></tr></thead><tbody>{''.join(tr)}</tbody></table></div>
<p class=muted>API-Äquivalent = gezählte Tokens × Listenpreis der Region (inkl. Aufschlag, umgerechnet). Abo-Äquivalent = Planpreis pro Woche × Wochenanteil. Break-even = ab so vielen Aufgaben pro Woche ist das Abo günstiger als die API. Liegt der Break-even über „Aufgaben/Woche bis Limit“, reicht das Abo dafür nicht.</p>
<h2>Einzelne Sessions</h2><div class="card wrap"><table><thead><tr><th>Konfiguration</th><th>Aufgabe</th><th>Status</th><th>ok</th><th>Kosten ({cur})</th><th>Tokens k: Input / Cache-Read / Cache-Write / Output</th><th>Zeit s</th></tr></thead><tbody>{''.join(trials)}</tbody></table></div>
</main></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
