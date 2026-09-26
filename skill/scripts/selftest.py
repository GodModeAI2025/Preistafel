#!/usr/bin/env python3
"""Selbsttest ohne herdr und ohne Tokenverbrauch.

Prüft Parser (Transkript, Rollout, Kontingent), Kostenrechnung und den Bericht
mit synthetischen Daten. Nach jeder Änderung an den Skripten ausführen:
    python3 scripts/selftest.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(HERE))
import quota as Q  # noqa: E402
import report as R  # noqa: E402
import usage as U  # noqa: E402

fails = 0


def check(name, cond, got=None):
    global fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + ("" if cond else f"  -> {got}"))
    fails += 0 if cond else 1


print("Parser")
c = U.claude_usage([FX / "claude_session.jsonl"])
opus = c["per_model"].get("claude-opus-5-5", {})
check("Claude: Streaming-Duplikat nur einmal, letzter Stand", opus.get("output") == 1212, opus)
check("Claude: Datumssuffix normalisiert", "claude-opus-5-5-20260922" not in c["per_model"], list(c["per_model"]))
check("Claude: 1h-Cache-Writes getrennt", opus.get("cache_write_1h") == 21500 and opus.get("cache_write_5m") == 0, opus)
check("Claude: Subagent-Modell mitgezählt", c["per_model"].get("claude-haiku-4-5", {}).get("input_uncached") == 900)
check("Claude: <synthetic> ignoriert", "<synthetic>" not in c["per_model"])
x = U.codex_usage([FX / "codex_rollout.jsonl"])
g = x["per_model"].get("gpt-6-sol", {})
check("Codex: Input ohne Cache", g.get("input_uncached") == 20000, g)
check("Codex: Cache-Read", g.get("cache_read") == 30000, g)
check("Codex: Reasoning separat, im Output enthalten", g.get("output") == 4000 and g.get("reasoning_output") == 2500, g)
qc = Q.from_codex_rate_limits(x["rate_limits_last"])
check("Codex rate_limits -> five_hour/week", qc["windows"]["five_hour"]["used_pct"] == 11.5 and qc["windows"]["week"]["used_pct"] == 3.4, qc)

print("Kontingent-Anzeigen")
s = Q.parse_screen((FX / "claude_usage_screen.txt").read_text())
w = s["windows"]
check("Claude /usage: Session 23 %", w.get("five_hour", {}).get("used_pct") == 23, w)
check("Claude /usage: Woche 41 %", w.get("week", {}).get("used_pct") == 41, w)
check("Claude /usage: Opus-Woche 12 %", w.get("week_opus", {}).get("used_pct") == 12, w)
s2 = Q.parse_screen((FX / "codex_status_screen.txt").read_text())
w2 = s2["windows"]
check("Codex /status: 5h 34 %", w2.get("five_hour", {}).get("used_pct") == 34, w2)
check("Codex /status: Woche 12 %", w2.get("week", {}).get("used_pct") == 12, w2)
check("Codex /status: Kontextanzeige ignoriert", all("Context" not in v["label"] for v in w2.values()), w2)
s3 = Q.parse_screen((FX / "codex_status_box_screen.txt").read_text())
check("Codex-Rahmen: Wochenfenster 76 %", s3["windows"].get("week", {}).get("used_pct") == 76.0, s3["windows"])
check("Codex-Rahmen: Zusatzfenster stabil benannt", "week_luna_reserve_weekly_limit" in s3["windows"], list(s3["windows"]))
check("Codex-Rahmen: Reset ohne Rahmenreste", s3["windows"].get("week", {}).get("resets") == "09:20 on 30 Sep", s3["windows"].get("week"))
check("Codex-Rahmen: Hinweistext ist kein Limit", not s3["limit_hit"])
check("Limit-Meldung erkannt", Q.parse_screen((FX / "claude_limit_screen.txt").read_text())["limit_hit"])
d = Q.delta({"windows": {"week": {"used_pct": 40}, "five_hour": {"used_pct": 90}}},
            {"windows": {"week": {"used_pct": 41.5}, "five_hour": {"used_pct": 5}}})
check("Delta: Reset wird verworfen", d == {"week": 1.5, "five_hour": None}, d)

print("Kosten und Bericht")
prices = {"region": "eu", "currency": "EUR", "fx_usd_to_local": 0.92, "source": "test", "source_commit": "abc",
          "fetched_at": "2026-09-25", "models": {
              "claude-opus-5-5": {"local_per_mtok": {"input": 4.048, "output": 20.24, "cache_read": 0.2024, "cache_write_5m": 5.06, "cache_write_1h": 8.096}},
              "claude-haiku-4-5": {"local_per_mtok": {"input": 1.012, "output": 5.06, "cache_read": 0.1012, "cache_write_5m": 1.265, "cache_write_1h": 2.024}},
              "gpt-6-sol": {"local_per_mtok": {"input": 2.024, "output": 10.12, "cache_read": 0.2024, "cache_write_5m": 2.53, "cache_write_1h": None}}}}
cost, missing = R.trial_cost(c, prices)
exp = (15 * 4.048 + 1212 * 20.24 + 20000 * 0.2024 + 21500 * 8.096 + 900 * 1.012 + 50 * 5.06) / 1e6
check("Kosten Claude-Session", abs(cost - exp) < 1e-6 and not missing, (cost, exp))
cost2, _ = R.trial_cost(x, prices)
exp2 = (20000 * 2.024 + 30000 * 0.2024 + 4000 * 10.12) / 1e6
check("Kosten Codex-Session", abs(cost2 - exp2) < 1e-6, (cost2, exp2))
check("Wilson 8/10", abs(R.wilson(8, 10)[1] - 0.49) < 0.01, R.wilson(8, 10))

with tempfile.TemporaryDirectory() as tmp:
    run = Path(tmp) / "run"
    (run / "trials").mkdir(parents=True)
    plan = {"run_id": "selftest", "quota_mode": "block",
            "defaults": {"quality_floor": 0.6},
            "configs": {"cc-opus": {"harness": "claude", "model": "claude-opus-5-5", "effort": "medium", "plan": "claude-max-20x"},
                        "cx-sol": {"harness": "codex", "model": "gpt-6-sol", "effort": "high", "plan": "chatgpt-pro-20x"}}}
    (run / "plan.json").write_text(json.dumps(plan))
    ids = {"cc-opus": [], "cx-sol": []}
    for cfg, usage_ in (("cc-opus", c), ("cx-sol", x)):
        for r in range(1, 4):
            tid = f"{cfg}__demo__r{r}"
            ids[cfg].append(tid)
            (run / "trials" / f"{tid}.json").write_text(json.dumps({
                "trial_id": tid, "config": cfg, "task": "demo", "rep": r, "status": "completed", "wall_s": 100 + r,
                "usage": usage_, "verify": {"passed": r != 3 or cfg == "cx-sol", "score": 1.0}}))
    with (run / "blocks.jsonl").open("w") as fh:
        fh.write(json.dumps({"config": "cc-opus", "trial_ids": ids["cc-opus"], "delta": {"week": 3.0, "five_hour": 12.0}}) + "\n")
        fh.write(json.dumps({"config": "cx-sol", "trial_ids": ids["cx-sol"], "delta": {"week": 1.2, "five_hour": None}}) + "\n")
    pf = Path(tmp) / "prices.json"
    pf.write_text(json.dumps(prices))
    out = subprocess.run([sys.executable, str(HERE / "report.py"), "--run", str(run), "--prices", str(pf)],
                         capture_output=True, text=True)
    check("report.py läuft", out.returncode == 0, out.stderr[-500:])
    rep = json.loads((run / "report.json").read_text())
    rows = {r["config"]: r for r in rep["configs"]}
    check("Wochenanteil je Aufgabe = Blockdelta / n", abs(rows["cc-opus"]["week_pct_per_task"] - 1.0) < 1e-9, rows["cc-opus"])
    check("Reset-Fenster ergibt None", rows["cx-sol"]["five_hour_pct_per_task"] is None, rows["cx-sol"])
    check("Empfehlung vorhanden", rep["recommendation"] in rows, rep["recommendation"])
    html_ok = (run / "report.html").read_text()
    check("HTML enthält Chart", "<svg" in html_ok and "Kosten gegen Qualität" in html_ok)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(html_ok)

print("Veröffentlichung")
import publish as P  # noqa: E402
rep = {"run_id": "20260925-0900", "region": "eu", "currency": "EUR", "configs": [], "quality_floor": 0.8,
       "trials": [{"trial_id": "a", "config": "c", "task": "t", "cohort": "k", "status": "completed", "passed": True,
                   "cost": 0.1, "tokens": {}, "wall_s": 1, "share": {}, "screen_tail": "Account: x@y.de",
                   "workdir": "/home/u/w"}]}
san = P.sanitize_report(rep)
check("Bereinigung entfernt Bildschirmtext und Pfade", "screen_tail" not in san["trials"][0] and "workdir" not in san["trials"][0])
with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "data" / "x.json"
    f.parent.mkdir()
    fake_key = "sk-" + "ant-" + "abcdefghijklmnopqrs"  # zur Laufzeit gebaut, damit der Quelltext den Scan nicht auslöst
    f.write_text('{"k": "%s", "p": "/home/mark/x"}' % fake_key)
    hits = P.scan(Path(tmp), ["data/x.json"])
    check("Scan findet Key und Home-Pfad", len(hits) == 2, hits)
import board as B  # noqa: E402
h = B.aggregate([dict(san, configs=[])], 1)
check("Tafel aggregiert Kohorte", "k" in h and h["k"][-1]["date"] == "25.09.2026", h.keys())
check("Tafel rendert", "Preistafel" in B.render(h, "Preistafel", "x", None))

print("FEHLER:" if fails else "Alles grün.", fails if fails else "")
sys.exit(1 if fails else 0)
