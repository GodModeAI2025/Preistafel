#!/usr/bin/env python3
"""Landingpage „Preistafel“ aus der Messhistorie bauen.

  board.py --data <repo>/data --out <repo>/site [--title ..] [--subtitle ..]

Liest data/runs/*/report.json, baut je Kohorte eine Tafel aus dem jüngsten Lauf
(mit Trend gegenüber dem vorherigen Lauf derselben Kohorte) und schreibt:
  site/index.html              Preistafel (selbsttragend, ohne externe Ressourcen)
  site/feeds/<kohorte>.json    maschinenlesbares Preissignal je Kohorte
  site/data/history.json       komplette Historie (Konfiguration × Kohorte × Lauf)
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import defaultdict
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def de(v, d=2):
    if v is None:
        return "–"
    return f"{v:,.{d}f}".replace(",", "\u202f").replace(".", ",")


SYM = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}


def aggregate(reports: list[dict], min_n: int) -> dict:
    """history[kohorte] = [{run_id, date, region, currency, cells{config: {...}}}] chronologisch."""
    hist: dict[str, list] = defaultdict(list)
    for rep in sorted(reports, key=lambda r: r["run_id"]):
        by = defaultdict(lambda: defaultdict(list))
        meta = {}
        for t in rep["trials"]:
            if t.get("status") in ("rate_limited", "error"):
                continue
            by[t.get("cohort") or "allgemein"][t["config"]].append(t)
            meta[t["config"]] = t
        cfg_rows = {r["config"]: r for r in rep["configs"]}
        for cohort, cfgs in by.items():
            cells = {}
            for cid, ts in cfgs.items():
                k, n = sum(bool(t.get("passed")) for t in ts), len(ts)
                p, lo, hi = wilson(k, n)
                costs = [t["cost"] for t in ts if t.get("cost") is not None]
                wk = [t["share"].get("week") for t in ts if (t.get("share") or {}).get("week") is not None]
                row = cfg_rows.get(cid, {})
                cells[cid] = {
                    "harness": row.get("harness") or meta[cid].get("harness"), "model": row.get("model") or meta[cid].get("model"),
                    "effort": row.get("effort") or meta[cid].get("effort"), "plan": row.get("plan") or meta[cid].get("plan"),
                    "n": n, "passed": k, "pass_rate": p, "ci": [lo, hi],
                    "cost_per_solved": (sum(costs) / k) if (costs and k) else None,
                    "mean_cost": (sum(costs) / len(costs)) if costs else None,
                    "median_wall_s": sorted(t.get("wall_s") or 0 for t in ts)[n // 2],
                    "week_pct_per_task": (sum(wk) / len(wk)) if wk else None,
                    "tasks_per_week_at_limit": (100 / (sum(wk) / len(wk))) if wk and sum(wk) > 0 else None,
                    "enough_data": n >= min_n,
                }
            floor = rep.get("quality_floor", 0.8)
            ok = [c for c, v in cells.items() if v["pass_rate"] >= floor and v["cost_per_solved"] is not None and v["enough_data"]]
            best = min(ok, key=lambda c: cells[c]["cost_per_solved"]) if ok else None
            m = re.match(r"(\d{4})(\d{2})(\d{2})", rep["run_id"])
            date = f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else (rep.get("prices_fetched") or "")[:10]
            hist[cohort].append({"run_id": rep["run_id"], "date": date,
                                 "region": rep["region"], "currency": rep["currency"], "quality_floor": floor,
                                 "prices_commit": rep.get("prices_commit"), "recommendation": best, "cells": cells})
    return dict(hist)


def trend(cur, prev, key, better_low=True):
    if not prev or cur.get(key) is None or prev.get(key) is None or prev[key] == 0:
        return ""
    ch = (cur[key] - prev[key]) / prev[key]
    if abs(ch) < 0.03:
        return '<span class="tr eq" title="unverändert">＝</span>'
    good = (ch < 0) if better_low else (ch > 0)
    arrow = "▼" if ch < 0 else "▲"
    return f'<span class="tr {"up" if good else "dn"}" title="{de(ch * 100, 0)} % ggü. Vorlauf">{arrow} {de(abs(ch) * 100, 0)} %</span>'


def spark(points: list[float | None], w=120, h=28) -> str:
    vals = [p for p in points if p is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = w / (len(points) - 1)
    pts = [f"{i * step:.1f},{h - 3 - (p - lo) / rng * (h - 6):.1f}" for i, p in enumerate(points) if p is not None]
    return f'<svg class=spark viewBox="0 0 {w} {h}" aria-hidden=true><polyline points="{" ".join(pts)}"/></svg>'


def board(cohort: str, runs: list[dict]) -> str:
    cur, prev = runs[-1], (runs[-2] if len(runs) > 1 else None)
    sym = SYM.get(cur["currency"], cur["currency"])
    rows = sorted(cur["cells"].items(), key=lambda kv: (not kv[1]["enough_data"], kv[1]["cost_per_solved"] is None,
                                                        kv[1]["cost_per_solved"] or 0))
    lines = []
    for cid, c in rows:
        p = (prev or {}).get("cells", {}).get(cid)
        rec = cid == cur["recommendation"]
        hist_pts = [r["cells"].get(cid, {}).get("cost_per_solved") for r in runs]
        abo = f"≈ {de(c['tasks_per_week_at_limit'], 0)} / Woche" if c["tasks_per_week_at_limit"] else "–"
        lines.append(f"""
<li class="row{' rec' if rec else ''}{' thin' if not c['enough_data'] else ''}">
  <div class=name><span class="h {c['harness']}">{'Claude Code' if c['harness'] == 'claude' else 'Codex'}</span>
    <b>{html.escape(c['model'] or cid)}</b> <span class=eff>{html.escape(c['effort'] or '')}</span>
    {'<span class=tag>Empfehlung</span>' if rec else ''}{'' if c['enough_data'] else '<span class="tag muted">zu wenig Daten</span>'}</div>
  <div class=dots aria-hidden=true></div>
  <div class=price><span class=num>{de(c['cost_per_solved'])}</span><span class=cur>{sym}</span>{trend(c, p, 'cost_per_solved')}</div>
  <div class=meta>
    <span title="Erfolgsquote, 95-%-Intervall {de(c['ci'][0] * 100, 0)}–{de(c['ci'][1] * 100, 0)} %">✓ {de(c['pass_rate'] * 100, 0)} % <small>({c['passed']}/{c['n']})</small>{trend(c, p, 'pass_rate', better_low=False)}</span>
    <span title="Median Laufzeit">⏱ {de(c['median_wall_s'] / 60, 1)} min</span>
    <span title="Aufgaben pro Woche bis zum Abo-Wochenlimit ({html.escape(c['plan'] or '')})">Abo {abo}</span>
    {spark(hist_pts)}
  </div>
</li>""")
    title = cohort.replace("-", " ").title()
    return f"""
<section class=board id="{html.escape(cohort)}">
  <header><h2>{html.escape(title)}</h2>
  <p>Preis pro <b>gelöster</b> Aufgabe · Region {html.escape(cur['region'].upper())} · Stand {html.escape(cur['date'])} · Mindestquote {de(cur['quality_floor'] * 100, 0)} %</p></header>
  <ul>{''.join(lines)}</ul>
  <footer><a href="feeds/{html.escape(cohort)}.json">Feed (JSON)</a> · Lauf {html.escape(cur['run_id'])} · {len(runs)} Messläufe in der Historie</footer>
</section>"""


def render(hist: dict, title: str, subtitle: str, repo_url: str | None, skill_ok: bool = False) -> str:
    boards = "".join(board(c, r) for c, r in sorted(hist.items()))
    latest_run = max(hist.values(), key=lambda r: r[-1]["run_id"])[-1] if hist else None
    latest = latest_run["date"] if latest_run else "–"
    commit = next((r[-1].get("prices_commit") for r in hist.values() if r[-1].get("prices_commit")), None)
    nav = " · ".join(f'<a href="#{html.escape(c)}">{html.escape(c.replace("-", " ").title())}</a>' for c in sorted(hist))
    skill_link = '<a href="herdr-bench.skill">Skill herunterladen</a> · ' if skill_ok else ""
    repo = f' · <a href="{html.escape(repo_url)}">Quellcode & Rohdaten</a>' if repo_url else ""
    return f"""<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(title)}</title><meta name=description content="{html.escape(subtitle)}">
<style>
:root{{--page:#efe9df;--ink:#1d1c1a;--mut:#6f695f;--board:#1f2a26;--board2:#27352f;--chalk:#f3efe4;--dim:#a9b3a8;--gold:#f0c35a;--cc:#ec8f62;--cx:#6fd1c6;--good:#8fdc8a;--bad:#ff8f7a}}
@media (prefers-color-scheme:dark){{:root{{--page:#121413;--ink:#ecebe6;--mut:#9a978f}}}}
*{{box-sizing:border-box}} :root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}
body{{margin:0;background:var(--page);color:var(--ink);font:16px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:980px;margin:auto;padding:40px 18px 64px}}
.hero h1{{font:800 clamp(30px,5vw,48px)/1.05 ui-serif,Georgia,serif;margin:0;letter-spacing:-.01em}}
.hero p{{color:var(--mut);margin:10px 0 0;max-width:60ch}} nav{{margin:18px 0 8px;font-size:14px}} a{{color:inherit}}
.board{{background:linear-gradient(180deg,var(--board2),var(--board));color:var(--chalk);border-radius:18px;padding:26px 24px 18px;margin:28px 0;
  box-shadow:0 0 0 10px #6b4f33,0 0 0 12px #3d2c1c,0 18px 40px rgba(0,0,0,.35)}}
.board header h2{{font:700 26px/1.1 ui-serif,Georgia,serif;margin:0;color:var(--gold);letter-spacing:.02em}}
.board header p{{margin:6px 0 16px;color:var(--dim);font-size:13.5px}}
.board ul{{list-style:none;margin:0;padding:0}}
.row{{display:grid;grid-template-columns:minmax(0,auto) 1fr auto;column-gap:10px;align-items:baseline;padding:12px 6px;border-bottom:1px dashed rgba(243,239,228,.18)}}
.row.rec{{background:rgba(240,195,90,.09);border-radius:10px}} .row.thin{{opacity:.55}}
.name{{font-size:17px}} .name b{{font-weight:650}} .eff{{color:var(--dim);font-size:14px}}
.h{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:2px 7px;border-radius:99px;margin-right:6px;border:1px solid}}
.h.claude{{color:var(--cc)}} .h.codex{{color:var(--cx)}}
.tag{{margin-left:8px;font-size:11px;background:var(--gold);color:#241c08;padding:2px 8px;border-radius:99px;font-weight:700}} .tag.muted{{background:transparent;color:var(--dim);border:1px solid var(--dim)}}
.dots{{border-bottom:2px dotted rgba(243,239,228,.35);transform:translateY(-5px)}}
.price{{white-space:nowrap}} .num{{font:700 30px/1 ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}} .cur{{margin-left:4px;color:var(--dim);font-size:18px}}
.meta{{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px 18px;color:var(--dim);font-size:13px;margin-top:4px;align-items:center}}
.tr{{margin-left:8px;font-size:12px;font-family:ui-monospace,Menlo,monospace}} .tr.up{{color:var(--good)}} .tr.dn{{color:var(--bad)}} .tr.eq{{color:var(--dim)}}
.spark{{width:120px;height:28px}} .spark polyline{{fill:none;stroke:var(--gold);stroke-width:1.6}}
.board footer{{margin-top:12px;font-size:12.5px;color:var(--dim)}}
.note{{font-size:14px;color:var(--mut);max-width:70ch}} .note h3{{color:var(--ink);font-size:16px;margin:28px 0 6px}}
@media (max-width:560px){{.row{{grid-template-columns:1fr auto}} .dots{{display:none}} .num{{font-size:24px}}}}
</style></head><body><main>
<div class=hero><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}. Stand {html.escape(latest)}.</p></div>
<nav>{nav}</nav>
{boards or '<p>Noch keine Messdaten veröffentlicht.</p>'}
<div class=note>
<h3>Wie die Preise entstehen</h3>
<p>Jede Zeile ist eine Kombination aus Werkzeug, Modell und Denkaufwand. Sie hat dieselben Aufgaben in frischen, echten Sessions gelöst. Versteckte Tests prüfen das Ergebnis. Der Preis ist das API-Äquivalent: gezählte Tokens mal Listenpreis der Region, geteilt durch die Zahl der gelösten Aufgaben. „Abo“ zeigt, wie viele solcher Aufgaben ein Wochenkontingent schafft. Pfeile vergleichen mit dem vorigen Messlauf, grün heißt besser.</p>
<p>Preise aus LiteLLM{(' (Commit ' + html.escape(commit[:10]) + ')') if commit else ''}, stichprobenartig mit den offiziellen Preisseiten abgeglichen. Kleine Stichproben schwanken; blasse Zeilen haben zu wenig Messungen.</p>
<p>{skill_link}<a href="data/history.json">Messhistorie (JSON)</a>{repo}</p>
</div>
</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Preistafel KI-Coding-Modelle")
    ap.add_argument("--subtitle", default="Gemessen in echten Agent-Sessions")
    ap.add_argument("--repo-url")
    ap.add_argument("--min-n", type=int, default=3)
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    reports = [json.loads(p.read_text()) for p in sorted(data.glob("runs/*/report.json"))]
    hist = aggregate(reports, a.min_n)
    (out / "feeds").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    for cohort, runs in hist.items():
        cur = runs[-1]
        feed = {"feed_version": "1.0", "cohort": cohort, "run_id": cur["run_id"], "date": cur["date"],
                "region": cur["region"], "currency": cur["currency"], "recommendation": cur["recommendation"],
                "quality_floor": cur["quality_floor"], "configs": cur["cells"]}
        (out / "feeds" / f"{cohort}.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False))
    (out / "data" / "history.json").write_text(json.dumps(hist, indent=2, ensure_ascii=False))
    (out / "index.html").write_text(render(hist, a.title, a.subtitle, a.repo_url))
    (out / ".nojekyll").write_text("")
    print(f"{out / 'index.html'}  ({len(hist)} Kohorten, {len(reports)} Läufe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
