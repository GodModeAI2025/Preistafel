#!/usr/bin/env python3
"""herdr-bench Orchestrator.

  bench.py doctor                    Umgebung prüfen
  bench.py plan  [--tasks ..] ...    Messplan (~/herdr-bench-runs/<run_id>/plan.json) erzeugen
  bench.py run   --plan <file>       Plan sequenziell ausführen
  bench.py trial --plan <file> --trial <id>   einzelnen Trial (neu) messen

Alle herdr-Aufrufe laufen über die CLI (JSON-Antworten). --dry-run druckt die
Befehle nur, ohne Sessions zu starten.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quota as Q  # noqa: E402
import usage as U  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Läufe liegen AUSSERHALB des Skill-Ordners, damit Agents die versteckten Tests nicht finden.
RUNS = Path(os.environ.get("HERDR_BENCH_RUNS", Path.home() / "herdr-bench-runs"))
DRY = False
SKIP_ON_LIMIT = False
TRUST_RX = re.compile(r"(trust (the files|this folder)|Do you trust|allow Codex to work|Trust this directory)", re.I)
HOOK_REVIEW_RX = re.compile(r"hooks? needs? review", re.I)
KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class HerdrError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def herdr(*args: str, timeout: int | None = None, text: bool = False):
    cmd = ["herdr", *args]
    if DRY:
        print("DRY:", shlex.join(cmd))
        return "" if text else {"result": {}}
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        try:
            err = json.loads(p.stderr or "{}").get("error", {})
            raise HerdrError(err.get("code", "error"), err.get("message", p.stderr.strip()))
        except json.JSONDecodeError:
            raise HerdrError("error", p.stderr.strip() or p.stdout.strip())
    if text:
        return p.stdout
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": p.stdout}


def find_key(obj, key):
    """Tiefensuche nach einem Schlüssel (robust gegen Schemaänderungen)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def load_cfg():
    m = tomllib.loads((ROOT / "config/matrix.toml").read_text())
    return {c["id"]: c for c in m["config"]}, m.get("defaults", {})


def load_task(tid: str) -> dict:
    d = ROOT / "tasks" / tid
    t = tomllib.loads((d / "task.toml").read_text())
    t["dir"] = str(d)
    return t


# ------------------------------------------------------------------ doctor
def cmd_doctor(_a) -> int:
    ok = True

    def check(name, cond, hint=""):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FEHLT'}  {name}" + ("" if cond else f"  -> {hint}"))
        ok &= bool(cond)

    print("herdr-bench doctor")
    check("HERDR_ENV=1 (läuft in herdr-Pane)", os.environ.get("HERDR_ENV") == "1", "Claude Code innerhalb von herdr starten")
    harnesses = sorted({c["harness"] for c in load_cfg()[0].values()})
    for b in ("herdr", *harnesses, "jq", "git"):
        check(f"{b} im PATH", shutil.which(b), f"{b} installieren")
    try:
        st = herdr("status", "server", text=True)
        check("herdr-Server erreichbar", True)
    except Exception as e:  # noqa: BLE001
        check("herdr-Server erreichbar", False, str(e))
    try:
        st = herdr("integration", "status", text=True)
        for k in harnesses:
            line = next((l for l in st.splitlines() if l.lower().startswith(k + ":")), "")
            check(f"herdr-Integration {k}", line and "not installed" not in line.lower(),
                  f"herdr integration install {k}  (liefert Transkriptpfad über agent_session)")
    except Exception as e:  # noqa: BLE001
        check("herdr integration status", False, str(e))
    present = [k for k in KEY_VARS if os.environ.get(k)]
    check("keine API-Keys in dieser Umgebung", not present,
          f"{', '.join(present)} entfernen, sonst wird über die API statt das Abo abgerechnet")
    # Umgebung neuer Panes prüfen (erbt vom herdr-Server, nicht von dieser Shell)
    if not DRY and shutil.which("herdr"):
        try:
            ws = herdr("workspace", "create", "--cwd", str(ROOT), "--label", "bench:doctor", "--no-focus")
            wid, pid = find_key(ws, "workspace_id"), find_key(ws.get("result", {}).get("root_pane", {}), "pane_id")
            probe = " ".join(f'[ -n "${k}" ] && echo HAS_{k};' for k in KEY_VARS) + " echo PROBE_DONE"
            herdr("pane", "run", pid, probe)
            herdr("pane", "wait-output", pid, "--match", "PROBE_DONE", "--timeout", "10000")
            out = herdr("pane", "read", pid, "--source", "recent-unwrapped", "--lines", "20", text=True)
            leaked = sorted(set(re.findall(r"^HAS_(\w+)\s*$", out, re.M)))  # nur echte Ausgabe, nicht die Befehlszeile
            check("keine API-Keys in neuen herdr-Panes", not leaked,
                  f"{', '.join(leaked)} ist in der Umgebung des herdr-Servers gesetzt; Server ohne Keys neu starten")
            herdr("workspace", "close", wid)
        except Exception as e:  # noqa: BLE001
            check("Pane-Umgebung prüfbar", False, str(e))
    print("Ergebnis:", "bereit" if ok else "NICHT bereit")
    return 0 if ok else 1


# ------------------------------------------------------------------ plan
def cmd_plan(a) -> int:
    cfgs, defaults = load_cfg()
    configs = a.configs.split(",") if a.configs else list(cfgs)
    tasks = sorted(p.name for p in (ROOT / "tasks").iterdir() if (p / "task.toml").exists()) \
        if a.tasks == "all" else a.tasks.split(",")
    repeats = a.repeats or defaults.get("repeats", 3)
    mode = a.quota_mode or defaults.get("quota_mode", "block")
    seed = a.seed if a.seed is not None else int(time.time())
    rng = random.Random(seed)
    run_id = a.run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    blocks = []
    for c in configs:
        if c not in cfgs:
            print(f"Unbekannte Konfiguration {c}", file=sys.stderr)
            return 2
        trials = [{"trial_id": f"{c}__{t}__r{r}", "config": c, "task": t, "rep": r}
                  for t in tasks for r in range(1, repeats + 1)]
        rng.shuffle(trials)
        blocks.append({"config": c, "trials": trials})
    rng.shuffle(blocks)
    if mode == "single":  # alles mischen, jeder Trial ist ein eigener Messblock
        flat = [t for b in blocks for t in b["trials"]]
        rng.shuffle(flat)
        blocks = [{"config": t["config"], "trials": [t]} for t in flat]
    plan = {"run_id": run_id, "created": dt.datetime.now().isoformat(timespec="seconds"), "seed": seed,
            "quota_mode": mode, "tasks": tasks, "configs": {c: cfgs[c] for c in configs},
            "defaults": defaults, "blocks": blocks}
    out = RUNS / run_id / "plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2))
    n = sum(len(b["trials"]) for b in blocks)
    print(f"{out}\n{len(configs)} Konfigurationen × {len(tasks)} Aufgaben × {repeats} Wdh. = {n} Trials, "
          f"{len(blocks)} Messblöcke, Modus {mode}, Seed {seed}")
    return 0


# ------------------------------------------------------------------ Session-Bausteine
def agent_args(cfg: dict, defaults: dict) -> list[str]:
    if cfg["harness"] == "claude":
        return ["--model", cfg["model"], "--permission-mode", defaults.get("claude_permission_mode", "acceptEdits")]
    return ["-m", cfg["model"], "-c", f'model_reasoning_effort="{cfg["effort"]}"',
            "-s", defaults.get("codex_sandbox", "workspace-write"), "-a", "never"]


def open_session(cfg: dict, defaults: dict, cwd: Path, label: str, name: str) -> dict:
    env = (["--env", f"CLAUDE_CODE_EFFORT_LEVEL={cfg['effort']}", "--env", "DISABLE_AUTOUPDATER=1"]
           if cfg["harness"] == "claude" else [])
    ws = herdr("workspace", "create", "--cwd", str(cwd), "--label", label, "--no-focus", *env)
    wid = find_key(ws, "workspace_id")
    pane = find_key((ws.get("result") or {}).get("root_pane", {}), "pane_id")
    try:
        herdr("agent", "start", name, "--kind", cfg["harness"], "--pane", pane,
              "--timeout", str(defaults.get("startup_timeout_ms", 60000)), "--", *agent_args(cfg, defaults))
    except HerdrError as e:
        if e.code != "agent_not_ready":
            raise
    # herdr meldet den Agenten auch mit offenem Vertrauensdialog als bereit. Dort ist "No, exit"
    # vorausgewählt, ein blindes Enter würde den Agenten beenden. Deshalb erst die Zustimmung wählen.
    for _ in range(3):
        time.sleep(1.5)
        screen = herdr("pane", "read", pane, "--source", "visible", text=True)
        if HOOK_REVIEW_RX.search(screen or ""):  # Codex: neue Hooks muss der Nutzer selbst freigeben
            raise HerdrError("startup_blocked", "Hook wartet auf Freigabe (einmal in codex prüfen und mit t freigeben): "
                             + screen[-300:])
        if not TRUST_RX.search(screen or ""):
            break
        keys = ["down", "enter"] if re.search(r"❯\s*(\d\.\s*)?No", screen) else ["enter"]
        herdr("agent", "send-keys", name, *keys)
        herdr("agent", "wait", name, "--until", "idle", "--until", "done", "--timeout", "30000")
    else:
        raise HerdrError("startup_blocked", screen[-500:])
    return {"workspace_id": wid, "pane_id": pane, "name": name}


def read_quota(sess: dict, harness: str) -> dict:
    cmd = "/usage" if harness == "claude" else "/status"
    try:
        herdr("agent", "prompt", sess["name"], cmd)
        time.sleep(3)
        screen = herdr("agent", "read", sess["name"], "--source", "visible", text=True)
        if harness == "claude":
            herdr("agent", "send-keys", sess["name"], "esc")
        herdr("agent", "wait", sess["name"], "--until", "idle", "--until", "done", "--timeout", "15000")
        q = Q.parse_screen(screen)
    except HerdrError as e:
        q = {"windows": {}, "limit_hit": False, "raw": None, "error": str(e)}
    q["read_at"] = time.time()
    return q


def close_session(sess: dict | None) -> None:
    if sess and sess.get("workspace_id"):
        try:
            herdr("workspace", "close", sess["workspace_id"])
        except HerdrError as e:
            log(f"Workspace schließen fehlgeschlagen: {e}")


def prepare_workdir(task: dict, work: Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    src = Path(task["dir"]) / task.get("fixture", "fixture")
    shutil.copytree(src, work)
    for c in (["git", "init", "-q"], ["git", "add", "-A"],
              ["git", "-c", "user.email=bench@local", "-c", "user.name=bench", "commit", "-qm", "fixture"]):
        subprocess.run(c, cwd=work, check=True, capture_output=True)


def run_verify(sess: dict, task: dict, work: Path, out_json: Path, timeout_s: int) -> dict:
    split = herdr("pane", "split", sess["pane_id"], "--direction", "down", "--no-focus", "--cwd", str(work))
    vp = find_key((split.get("result") or {}).get("pane", {}), "pane_id")
    script = Path(task["dir"]) / task.get("verify", "verify.sh")
    herdr("pane", "run", vp, f"bash {shlex.quote(str(script))} {shlex.quote(str(work))} "
                             f"{shlex.quote(str(out_json))}; echo BENCH_VERIFY_EXIT=$?")
    herdr("pane", "wait-output", vp, "--regex", r"BENCH_VERIFY_EXIT=\d+", "--timeout", str(timeout_s * 1000))
    if DRY or not out_json.exists():
        return {"passed": False, "score": 0.0, "error": "verify.json fehlt"}
    return json.loads(out_json.read_text())


# ------------------------------------------------------------------ Trial
def run_trial(plan: dict, t: dict, run_dir: Path, quota_single: bool) -> dict:
    cfg = plan["configs"][t["config"]]
    d = plan["defaults"]
    task = load_task(t["task"])
    work = run_dir / "work" / t["trial_id"]
    name = "t" + re.sub(r"[^a-z0-9]", "", t["trial_id"].lower())[-28:]
    rec = {"trial_id": t["trial_id"], "config": t["config"], "task": t["task"], "rep": t["rep"],
           "harness": cfg["harness"], "model": cfg["model"], "effort": cfg["effort"], "plan": cfg["plan"],
           "workdir": str(work), "started": dt.datetime.now().isoformat(timespec="seconds")}
    prepare_workdir(task, work)
    t0_files = time.time()
    sess = None
    try:
        sess = open_session(cfg, d, work, f"bench:{t['trial_id']}"[:60], name)
        if quota_single:
            rec["quota_before"] = read_quota(sess, cfg["harness"])
        prompt = task["prompt"].strip() + "\n\nArbeite selbstständig und ohne Rückfragen. Wenn du fertig bist, antworte nur mit: BENCH_DONE"
        t0 = time.time()
        timeout_ms = int(task.get("timeout_s", d.get("task_timeout_s", 1800)) * 1000)
        try:
            res = herdr("agent", "prompt", name, prompt, "--wait", "--until", "idle", "--until", "done",
                        "--until", "blocked", "--timeout", str(timeout_ms))
            state = find_key(res, "status") or find_key(res, "state") or "idle"
            rec["status"] = "blocked" if state == "blocked" else "completed"
        except HerdrError as e:
            rec["status"] = "timeout" if e.code in ("timeout", "agent_prompt_stalled") else "error"
            rec["error"] = str(e)
            herdr("agent", "send-keys", name, "ctrl+c")
        rec["wall_s"] = round(time.time() - t0, 1)
        screen = herdr("agent", "read", name, "--source", "recent-unwrapped", "--lines", "60", text=True)
        if Q.LIMIT_HIT.search(screen or ""):
            rec["status"] = "rate_limited"
        rec["screen_tail"] = (screen or "")[-1500:]
        # Transkript finden und Tokens zählen
        agent = herdr("agent", "get", name)
        sp = find_key(find_key(agent, "agent_session") or {}, "path") if isinstance(agent, dict) else None
        rec["session_path"] = sp
        rec["usage"] = U.collect(cfg["harness"], str(work), sp, t0_files - 5)
        total = sum(v.get(k, 0) for v in (rec["usage"].get("per_model") or {}).values() for k in U.CATS)
        if rec["status"] == "completed" and not total:  # kein Transkript/keine Tokens = Agent hat nicht gearbeitet
            rec["status"], rec["error"] = "error", "keine Tokens gezählt, Agent hat die Aufgabe nicht bearbeitet"
        if cfg["harness"] == "codex" and rec["usage"].get("rate_limits_last"):
            rec["quota_codex_after"] = Q.from_codex_rate_limits(rec["usage"]["rate_limits_last"])
        # Prüfen
        vdir = run_dir / "verify"
        vdir.mkdir(parents=True, exist_ok=True)
        rec["verify"] = run_verify(sess, task, work, vdir / f"{t['trial_id']}.json", int(task.get("verify_timeout_s", 300)))
        if quota_single:
            rec["quota_after"] = read_quota(sess, cfg["harness"])
            rec["quota_delta"] = Q.delta(rec["quota_before"], rec["quota_after"])
    except HerdrError as e:
        rec["status"], rec["error"] = "error", str(e)
        if sess and not rec.get("screen_tail"):  # Bildschirm zur Diagnose sichern (bleibt lokal)
            try:
                rec["screen_tail"] = herdr("pane", "read", sess["pane_id"], "--source", "recent-unwrapped",
                                           "--lines", "40", text=True)[-1500:]
            except HerdrError:
                pass
    finally:
        close_session(sess)
    rec["ended"] = dt.datetime.now().isoformat(timespec="seconds")
    return rec


def quota_probe(cfg: dict, defaults: dict, run_dir: Path, tag: str) -> dict:
    """Eigene kurze Session nur zum Ablesen des Kontingents (kostet keine Tokens)."""
    probe_dir = run_dir / "work" / f"_probe_{cfg['harness']}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    if not (probe_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=probe_dir, check=False)
    sess = None
    try:
        sess = open_session(cfg, defaults, probe_dir, f"bench:quota-{tag}"[:60], f"q{cfg['harness'][:2]}{int(time.time()) % 100000}")
        return read_quota(sess, cfg["harness"])
    except HerdrError as e:
        return {"windows": {}, "limit_hit": False, "error": str(e), "read_at": time.time()}
    finally:
        close_session(sess)


def wait_for_capacity(cfg, defaults, run_dir, skip: bool = False) -> bool:
    """True = Kontingent frei. Mit skip=True wird nicht gewartet, sondern False gemeldet (Block überspringen)."""
    thr = defaults.get("limit_pause_threshold", 98)
    waited = 0
    while True:
        q = quota_probe(cfg, defaults, run_dir, "check")
        if not q.get("limit_hit") and Q.max_used(q) < thr:
            return True
        if skip:
            log(f"Kontingent {cfg['harness']} bei {Q.max_used(q):.0f} % – Block wird übersprungen")
            return False
        if waited >= 8 * 3600:
            raise SystemExit("Kontingent nach 8 h Wartezeit nicht frei; Lauf abgebrochen (Fortsetzen mit run --resume).")
        log(f"Kontingent {cfg['harness']} bei {Q.max_used(q):.0f} % – pausiere 15 min (bisher {waited // 60} min)")
        time.sleep(900)
        waited += 900


# ------------------------------------------------------------------ run
def cmd_run(a) -> int:
    plan = json.loads(Path(a.plan).read_text())
    run_dir = Path(a.plan).parent
    tdir = run_dir / "trials"
    tdir.mkdir(exist_ok=True)
    total = sum(len(b["trials"]) for b in plan["blocks"])
    done = 0
    single = plan["quota_mode"] == "single"
    blocks_out = run_dir / "blocks.jsonl"
    skipped: list[str] = []
    for b in plan["blocks"]:
        cfg = plan["configs"][b["config"]]
        todo = [t for t in b["trials"] if not (tdir / f"{t['trial_id']}.json").exists()]
        done += len(b["trials"]) - len(todo)
        if not todo:
            continue
        if not DRY and not wait_for_capacity(cfg, plan["defaults"], run_dir, skip=SKIP_ON_LIMIT):
            skipped.append(b["config"])
            continue
        qb = None if single else quota_probe(cfg, plan["defaults"], run_dir, "before")
        ids = []
        for t in todo:
            for attempt in range(1, 4):
                rec = run_trial(plan, t, run_dir, single)
                rec["attempt"] = attempt
                if rec.get("status") != "rate_limited":
                    break
                log("Limit erreicht – warte auf Kontingent und wiederhole")
                wait_for_capacity(cfg, plan["defaults"], run_dir)
            (tdir / f"{t['trial_id']}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
            ids.append(t["trial_id"])
            done += 1
            toks = sum(sum(v.get(k, 0) for k in U.CATS if k != "reasoning_output")
                       for v in (rec.get("usage") or {}).get("per_model", {}).values())
            v = rec.get("verify") or {}
            log(f"Trial {done}/{total}: {rec['config']} · {rec['task']} r{rec['rep']} → {rec.get('status')}, "
                f"{'bestanden' if v.get('passed') else 'nicht bestanden'} ({v.get('score', 0):.2f}), "
                f"{toks / 1000:.1f} k Tokens, {rec.get('wall_s', 0):.0f} s")
        if not single:
            qa = quota_probe(cfg, plan["defaults"], run_dir, "after")
            with blocks_out.open("a") as fh:
                fh.write(json.dumps({"config": b["config"], "harness": cfg["harness"], "plan": cfg["plan"],
                                     "trial_ids": ids, "quota_before": qb, "quota_after": qa,
                                     "delta": Q.delta(qb or {}, qa)}, ensure_ascii=False) + "\n")
    if skipped:
        log(f"Wegen Kontingent übersprungen: {', '.join(skipped)}")
    log(f"Fertig: {run_dir}. Auswertung: python3 scripts/report.py --run {run_dir} --prices prices/<datei>.json")
    return 0


def cmd_trial(a) -> int:
    plan = json.loads(Path(a.plan).read_text())
    run_dir = Path(a.plan).parent
    t = next(t for b in plan["blocks"] for t in b["trials"] if t["trial_id"] == a.trial)
    rec = run_trial(plan, t, run_dir, True)
    (run_dir / "trials").mkdir(exist_ok=True)
    (run_dir / "trials" / f"{t['trial_id']}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    print(json.dumps({k: rec.get(k) for k in ("trial_id", "status", "wall_s", "verify", "quota_delta")}, indent=2))
    return 0


def main() -> int:
    global DRY, SKIP_ON_LIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("plan")
    p.add_argument("--tasks", default="all")
    p.add_argument("--configs")
    p.add_argument("--repeats", type=int)
    p.add_argument("--quota-mode", choices=["block", "single"])
    p.add_argument("--seed", type=int)
    p.add_argument("--run-id")
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--skip-on-limit", action="store_true", help="Blöcke mit erschöpftem Kontingent überspringen statt warten")
    tr = sub.add_parser("trial")
    tr.add_argument("--plan", required=True)
    tr.add_argument("--trial", required=True)
    a = ap.parse_args()
    DRY = a.dry_run
    SKIP_ON_LIMIT = getattr(a, "skip_on_limit", False)
    return {"doctor": cmd_doctor, "plan": cmd_plan, "run": cmd_run, "trial": cmd_trial}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
