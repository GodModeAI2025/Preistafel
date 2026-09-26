#!/usr/bin/env python3
"""Abo-Kontingent aus Bildschirmtext bzw. Codex-rate_limits lesen.

Claude Code `/usage` zeigt Balken wie
    Current session            ███████░░░  23% used   Resets 4pm (Europe/Berlin)
    Current week (all models)  ████░░░░░░  41% used   Resets Oct 1, 9am
    Current week (Opus)        ...         12% used
Codex `/status` zeigt z. B.
    5h limit:     [█████░░░░░] 34% used (resets 14:20)
    Weekly limit: [██░░░░░░░░] 12% used (resets 09:00 on 1 Oct)

Die Texte ändern sich zwischen CLI-Versionen. Deshalb wird tolerant geparst:
Jede Zeile mit "<Zahl>% used" oder "<Zahl>% left" ergibt ein Fenster; der
Zeilenanfang bestimmt den Typ. Der Rohtext wird immer mitgespeichert.
"""
from __future__ import annotations

import json
import re
import sys

PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%\s*(used|left|remaining|verbraucht|übrig)?", re.I)
RESET = re.compile(r"reset[s]?\s*(?:at|in|on)?\s*[:]?\s*(.+?)\)?\s*$", re.I)
LIMIT_HIT = re.compile(r"(hit your (?:session|weekly|usage|opus|sonnet|fable)\s*limit|usage limit reached|rate.?limited|rate.?limit (?:reached|exceeded|hit)|try again at)", re.I)


def classify(label: str) -> str:
    l = label.lower()
    if "opus" in l:
        return "week_opus"
    if "sonnet" in l:
        return "week_sonnet"
    if "fable" in l:
        return "week_fable"
    if "session" in l or "5h" in l or "5-hour" in l or "five" in l:
        return "five_hour"
    if "week" in l or "7d" in l or "7-day" in l:
        return "week"
    return "other"


def parse_screen(text: str) -> dict:
    windows: dict[str, dict] = {}
    lines = [ln.rstrip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        m = PCT.search(line)
        if not m:
            continue
        label = line[: m.start()].strip(" :[]█░▏▎▍▌▋▊▉■□-|│╭╮╰╯─")
        if not label and i > 0:  # Balken in eigener Zeile unter der Überschrift
            label = lines[i - 1].strip(" │╭╮╰╯─")
        kind = classify(label)
        if kind == "other" and not re.search(r"limit|session|week|usage", label, re.I):
            continue  # z. B. Kontextanzeige "ctx 3%"
        val = float(m.group(1).replace(",", "."))
        used = 100 - val if (m.group(2) or "").lower() in ("left", "remaining", "übrig") else val
        reset = None
        tail = " ".join(l.strip(" │") for l in lines[i:i + 2])
        r = RESET.search(tail)
        if r:
            reset = re.split(r"[)│]", r.group(1))[0].strip()[:60]
        # Weitere Fenster gleichen Typs nach Namen schlüsseln (stabil über Bildschirme hinweg)
        key = kind if kind not in windows else f"{kind}_" + "_".join(re.findall(r"[a-z0-9]+", label.lower()))[:40]
        windows[key] = {"label": label[:60], "used_pct": used, "resets": reset}
    return {"windows": windows, "limit_hit": bool(LIMIT_HIT.search(text)), "raw": text[-4000:]}


def from_codex_rate_limits(rl: dict | None) -> dict:
    """Codex-Rollout rate_limits -> gleiches Format. Fenster nach window_minutes."""
    out: dict[str, dict] = {}
    if not rl:
        return {"windows": out, "limit_hit": False, "raw": None}
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if not w:
            continue
        minutes = w.get("window_minutes") or 0
        kind = "five_hour" if minutes and minutes <= 360 else "week" if minutes >= 7 * 24 * 60 - 60 else slot
        out[kind] = {"label": f"{slot} ({minutes} min)", "used_pct": float(w.get("used_percent") or 0),
                     "resets": w.get("resets_at") or w.get("resets_in_seconds")}
    return {"windows": out, "limit_hit": any(v["used_pct"] >= 100 for v in out.values()), "raw": None}


def delta(before: dict, after: dict) -> dict:
    """Verbrauch je Fenster in Prozentpunkten. Reset dazwischen -> None."""
    res = {}
    for k, a in after.get("windows", {}).items():
        b = before.get("windows", {}).get(k)
        if not b:
            continue
        d = a["used_pct"] - b["used_pct"]
        res[k] = None if d < 0 else round(d, 3)  # negativ = Reset -> nicht werten
    return res


def max_used(q: dict) -> float:
    return max((w["used_pct"] for w in q.get("windows", {}).values()), default=0.0)


if __name__ == "__main__":
    print(json.dumps(parse_screen(sys.stdin.read()), indent=2, ensure_ascii=False))
