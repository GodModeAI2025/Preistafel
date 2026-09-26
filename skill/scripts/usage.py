#!/usr/bin/env python3
"""Tokens einer Agent-Session zählen.

Claude Code: JSONL-Transkripte unter ~/.claude/projects/<cwd-slug>/ (inkl. Subagents).
  Jede Assistant-Nachricht trägt message.usage. Beim Streaming kann dieselbe
  message.id mehrfach vorkommen; es zählt der letzte Eintrag je ID.
Codex: Rollout ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl. Die Events
  token_count enthalten kumulierte total_token_usage; es zählt der letzte.
  Dort stehen auch rate_limits (Abo-Kontingent in Prozent).

Ausgabe: dict mit tokens je Modell in einheitlichen Kategorien:
  input_uncached, cache_read, cache_write_5m, cache_write_1h, output, reasoning_output
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CATS = ("input_uncached", "cache_read", "cache_write_5m", "cache_write_1h", "output", "reasoning_output")


def _empty() -> dict:
    return {c: 0 for c in CATS}


def _jsonl(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # letzte Zeile evtl. unvollständig


def normalize_model(model: str) -> str:
    """claude-opus-5-5-20260922 -> claude-opus-5-5 ; Präfixe entfernen."""
    m = model.split("/")[-1]
    parts = m.split("-")
    if parts and parts[-1].isdigit() and len(parts[-1]) == 8:
        parts = parts[:-1]
    return "-".join(parts)


# ---------------------------------------------------------------- Claude
def claude_project_dir(cwd: str, home: Path | None = None) -> Path:
    home = home or Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(Path(cwd).resolve()))
    return home / "projects" / slug


def claude_files(cwd: str | None = None, session_path: str | None = None, since: float = 0) -> list[Path]:
    files: list[Path] = []
    if session_path and Path(session_path).exists():
        p = Path(session_path)
        files.append(p)
        sub = p.with_suffix("")  # <session>/subagents/*.jsonl bei neueren Versionen
        if sub.is_dir():
            files += sorted(sub.rglob("*.jsonl"))
    elif cwd:
        d = claude_project_dir(cwd)
        if d.is_dir():
            files = [f for f in d.rglob("*.jsonl") if f.stat().st_mtime >= since]
    return files


def claude_usage(files: list[Path]) -> dict:
    last_by_id: dict[str, tuple[str, dict]] = {}
    anon = 0
    session_ids, models_seen = set(), set()
    for f in files:
        for ev in _jsonl(f):
            if ev.get("sessionId"):
                session_ids.add(ev["sessionId"])
            if ev.get("type") != "assistant":
                continue
            msg = ev.get("message") or {}
            usage, model = msg.get("usage"), msg.get("model")
            if not usage or not model or model == "<synthetic>":
                continue
            mid = msg.get("id") or f"anon-{anon}"
            anon += 1
            last_by_id[mid] = (model, usage)
    per_model: dict[str, dict] = {}
    for model, u in last_by_id.values():
        m = normalize_model(model)
        models_seen.add(m)
        t = per_model.setdefault(m, _empty())
        t["input_uncached"] += int(u.get("input_tokens") or 0)
        t["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        t["output"] += int(u.get("output_tokens") or 0)
        cc = u.get("cache_creation") or {}
        w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
        if w5 is None and w1 is None:  # keine Aufteilung -> als unbekannt 1h (Abo-Standard) markieren
            t["cache_write_1h"] += int(u.get("cache_creation_input_tokens") or 0)
            t.setdefault("_cache_split_assumed", True)
        else:
            t["cache_write_5m"] += int(w5 or 0)
            t["cache_write_1h"] += int(w1 or 0)
    return {"harness": "claude", "per_model": per_model, "messages": len(last_by_id),
            "session_ids": sorted(session_ids), "files": [str(f) for f in files]}


# ---------------------------------------------------------------- Codex
def codex_files(cwd: str | None = None, session_path: str | None = None, since: float = 0) -> list[Path]:
    if session_path and Path(session_path).exists():
        return [Path(session_path)]
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    root = home / "sessions"
    if not root.is_dir() or not cwd:
        return []
    want = str(Path(cwd).resolve())
    hits = []
    for f in root.rglob("rollout-*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        for ev in _jsonl(f):
            if ev.get("type") == "session_meta":
                if str(Path((ev.get("payload") or {}).get("cwd", "")).resolve()) == want:
                    hits.append(f)
                break
    return sorted(hits)


def codex_usage(files: list[Path]) -> dict:
    per_model: dict[str, dict] = {}
    rate_first = rate_last = None
    model, effort = None, None
    for f in files:
        total = None
        for ev in _jsonl(f):
            p = ev.get("payload") or {}
            if ev.get("type") == "turn_context":
                model = p.get("model") or model
                effort = p.get("effort") or p.get("reasoning_effort") or effort
            if ev.get("type") == "event_msg" and p.get("type") == "token_count":
                if (p.get("info") or {}).get("total_token_usage"):
                    total = p["info"]["total_token_usage"]
                if p.get("rate_limits"):
                    rate_first = rate_first or p["rate_limits"]
                    rate_last = p["rate_limits"]
        if total:
            m = normalize_model(model or "unknown")
            t = per_model.setdefault(m, _empty())
            inp, cached = int(total.get("input_tokens") or 0), int(total.get("cached_input_tokens") or 0)
            cw = int(total.get("cache_write_input_tokens") or 0)
            t["input_uncached"] += max(inp - cached - cw, 0)
            t["cache_read"] += cached
            t["cache_write_5m"] += cw
            t["output"] += int(total.get("output_tokens") or 0)  # enthält reasoning
            t["reasoning_output"] += int(total.get("reasoning_output_tokens") or 0)
    return {"harness": "codex", "per_model": per_model, "model": model, "effort": effort,
            "rate_limits_first": rate_first, "rate_limits_last": rate_last, "files": [str(f) for f in files]}


def collect(harness: str, cwd: str | None, session_path: str | None, since: float) -> dict:
    if harness == "claude":
        return claude_usage(claude_files(cwd, session_path, since))
    return codex_usage(codex_files(cwd, session_path, since))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("harness", choices=["claude", "codex"])
    ap.add_argument("--cwd")
    ap.add_argument("--session-path")
    ap.add_argument("--since", type=float, default=0)
    a = ap.parse_args()
    json.dump(collect(a.harness, a.cwd, a.session_path, a.since), sys.stdout, indent=2)
    print()
