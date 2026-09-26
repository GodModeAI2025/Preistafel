#!/usr/bin/env python3
"""Skill, Messhistorie und Preistafel in ein git-Repo veröffentlichen (GitHub Pages).

  publish.py                      Vorschau: baut alles im lokalen Klon, zeigt den Diff, pusht NICHT
  publish.py --push               zusätzlich committen, taggen (run-<id>) und pushen
  publish.py --run <run_dir>      nur diesen Lauf aufnehmen (Standard: alle Läufe mit report.json)

Aufbau des Publikations-Repos:
  skill/                 Skill-Quellcode OHNE versteckte Tests (nur deren SHA-256)
  data/runs/<id>/        bereinigter report.json je Lauf
  data/prices/           verwendete Preis-Snapshots
  site/                  Preistafel (index.html), Feeds, history.json, herdr-bench.skill
  .github/workflows/pages.yml   deployt site/ nach GitHub Pages
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNS = Path(os.environ.get("HERDR_BENCH_RUNS", Path.home() / "herdr-bench-runs"))
sys.path.insert(0, str(HERE))
import board as SITE  # noqa: E402

# Felder, die öffentlich werden dürfen. Alles andere (Bildschirmtexte, Pfade, Transkripte) bleibt lokal.
REPORT_KEEP = ("run_id", "region", "currency", "fx", "prices_source", "prices_commit", "prices_fetched",
               "quota_mode", "quality_floor", "recommendation", "configs", "excluded", "trials")
TRIAL_KEEP = ("trial_id", "config", "task", "cohort", "harness", "model", "effort", "plan", "rep", "status",
              "passed", "cost", "cost_missing", "tokens", "wall_s", "share")
SECRET_RX = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-proj-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{32,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|CLAUDE_CODE_OAUTH_TOKEN\s*=\s*\S+"
    r"|\"(access|refresh|id)_token\"\s*:\s*\"[^\"]{12,})")
PRIVATE_RX = re.compile(r"(/home/[a-z_][\w.-]*/|/Users/[\w.-]+/|[\w.+-]+@[\w-]+\.[\w.-]+)")
SKILL_EXCLUDE = {"__pycache__", ".git", "runs", ".DS_Store"}


def sh(*cmd, cwd=None, check=True, capture=True):
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture)
    if check and p.returncode != 0:
        raise SystemExit(f"Befehl fehlgeschlagen: {' '.join(cmd)}\n{p.stderr or p.stdout}")
    return (p.stdout or "").strip()


def sanitize_report(rep: dict) -> dict:
    out = {k: rep.get(k) for k in REPORT_KEEP}
    out["trials"] = [{k: t.get(k) for k in TRIAL_KEEP} for t in rep.get("trials", [])]
    return out


def copy_skill(dst: Path) -> list[dict]:
    """Skill ohne versteckte Tests kopieren; Hashes der versteckten Dateien zurückgeben."""
    if dst.exists():
        shutil.rmtree(dst)
    hidden = []

    def ignore(dirpath, names):
        d = Path(dirpath)
        skip = {n for n in names if n in SKILL_EXCLUDE}
        if d.name == "prices":
            skip |= {n for n in names if n.endswith(".json")}
        if d.name == "hidden" and d.parent.parent.name == "tasks":
            for n in names:
                f = d / n
                if f.is_file():
                    hidden.append({"task": d.parent.name, "file": n, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
            skip |= set(names)
        return skip

    shutil.copytree(ROOT, dst, ignore=ignore)
    for task_dir in (dst / "tasks").iterdir():
        h = task_dir / "hidden"
        if h.is_dir():
            files = [x for x in hidden if x["task"] == task_dir.name]
            (h / "README.md").write_text(
                "Versteckte Tests sind absichtlich nicht veröffentlicht, damit sie nicht in Trainingsdaten\n"
                "oder in die Sicht gemessener Agents gelangen. Prüfsummen der lokal verwendeten Dateien:\n\n"
                + "\n".join(f"- `{x['file']}` sha256 `{x['sha256']}`" for x in files) + "\n")
    return hidden


def build_skill_zip(skill_dir: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(skill_dir.rglob("*")):
            if f.is_file():
                z.write(f, Path("herdr-bench") / f.relative_to(skill_dir))


def scan(repo: Path, paths: list[str]) -> list[str]:
    hits = []
    for rel in paths:
        f = repo / rel
        if not f.is_file() or f.suffix in (".skill", ".zip", ".png"):
            continue
        text = f.read_text(errors="ignore")
        for m in SECRET_RX.finditer(text):
            hits.append(f"GEHEIMNIS? {rel}: {m.group(0)[:24]}…")
        if rel.startswith(("data/", "site/")):
            for m in PRIVATE_RX.finditer(text):
                hits.append(f"PRIVAT? {rel}: {m.group(0)}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", help="Laufverzeichnis(se); Standard: alle unter $HERDR_BENCH_RUNS")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config/publish.toml"))
    a = ap.parse_args()
    cfg = tomllib.loads(Path(a.config).read_text())
    repo = Path(os.path.expanduser(cfg["repo_dir"]))
    remote, branch = cfg["remote"], cfg.get("branch", "main")

    # 1. Klon vorbereiten
    if not (repo / ".git").exists():
        if "<owner>" in remote:
            raise SystemExit("config/publish.toml: 'remote' ist noch ein Platzhalter.")
        repo.parent.mkdir(parents=True, exist_ok=True)
        if subprocess.run(["git", "clone", remote, str(repo)], text=True).returncode != 0:
            repo.mkdir(parents=True, exist_ok=True)
            sh("git", "init", "-b", branch, cwd=repo)
            sh("git", "remote", "add", "origin", remote, cwd=repo)
    has_commits = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, capture_output=True).returncode == 0
    if has_commits and sh("git", "remote", cwd=repo, check=False):
        subprocess.run(["git", "pull", "--ff-only", "origin", branch], cwd=repo, text=True, capture_output=True)
    sh("git", "checkout", "-q", "-B", branch, cwd=repo)  # leeres Remote klont sonst auf 'master' 

    # 2. Messdaten (bereinigt) übernehmen
    runs = [Path(r) for r in a.run] if a.run else sorted(p.parent for p in RUNS.glob("*/report.json"))
    new_ids = []
    for run in runs:
        rep = json.loads((run / "report.json").read_text())
        dst = repo / "data" / "runs" / rep["run_id"] / "report.json"
        if not dst.exists():
            new_ids.append(rep["run_id"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(sanitize_report(rep), indent=2, ensure_ascii=False))
    for pf in (ROOT / "prices").glob("*.json"):
        (repo / "data" / "prices").mkdir(parents=True, exist_ok=True)
        shutil.copy2(pf, repo / "data" / "prices" / pf.name)

    # 3. Skill selbst veröffentlichen
    site_dir = repo / "site"
    skill_ok = False
    if cfg.get("publish_skill", True):
        hidden = copy_skill(repo / "skill")
        build_skill_zip(repo / "skill", site_dir / "herdr-bench.skill")
        skill_ok = True
        print(f"Skill kopiert, {len(hidden)} versteckte Testdateien ausgelassen (nur Prüfsummen).")

    # 4. Preistafel bauen
    reports = [json.loads(p.read_text()) for p in sorted((repo / "data").glob("runs/*/report.json"))]
    hist = SITE.aggregate(reports, int(cfg.get("min_trials_per_cell", 3)))
    (site_dir / "feeds").mkdir(parents=True, exist_ok=True)
    (site_dir / "data").mkdir(parents=True, exist_ok=True)
    for cohort, rs in hist.items():
        cur = rs[-1]
        (site_dir / "feeds" / f"{cohort}.json").write_text(json.dumps(
            {"feed_version": "1.0", "cohort": cohort, "run_id": cur["run_id"], "date": cur["date"], "region": cur["region"],
             "currency": cur["currency"], "recommendation": cur["recommendation"], "quality_floor": cur["quality_floor"],
             "configs": cur["cells"]}, indent=2, ensure_ascii=False))
    (site_dir / "data" / "history.json").write_text(json.dumps(hist, indent=2, ensure_ascii=False))
    repo_url = re.sub(r"^git@github\.com:(.+?)(\.git)?$", r"https://github.com/\1", remote)
    (site_dir / "index.html").write_text(SITE.render(hist, cfg.get("site_title", "Preistafel"),
                                                     cfg.get("site_subtitle", ""), repo_url, skill_ok))
    (site_dir / ".nojekyll").write_text("")

    # 5. Pages-Workflow und README
    wf = repo / ".github" / "workflows" / "pages.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text((ROOT / "assets" / "pages.yml").read_text().replace("branches: [main]", f"branches: [{branch}]"))
    (repo / "README.md").write_text(
        f"# {cfg.get('site_title', 'Preistafel')}\n\n{cfg.get('site_subtitle', '')}\n\n"
        f"- Preistafel: {cfg.get('pages_url') or 'GitHub Pages dieses Repos'}\n"
        "- `data/runs/`: bereinigte Messergebnisse je Lauf\n- `data/prices/`: verwendete Preis-Snapshots\n"
        "- `skill/`: der Mess-Skill (herdr-bench); versteckte Tests nur als Prüfsumme\n"
        "- `site/feeds/<kohorte>.json`: maschinenlesbares Preissignal\n\n"
        "Einmalig in den Repo-Einstellungen: *Settings → Pages → Source: GitHub Actions*.\n")

    # 6. Prüfen, Vorschau, ggf. pushen
    sh("git", "add", "-A", cwd=repo)
    changed = [l for l in sh("git", "diff", "--cached", "--name-only", cwd=repo).splitlines() if l]
    hits = scan(repo, changed)
    if hits:
        sh("git", "reset", "-q", cwd=repo)
        print("ABGEBROCHEN – mögliche Geheimnisse oder private Daten gefunden:\n  " + "\n  ".join(hits[:20]))
        return 3
    if not changed:
        print("Keine Änderungen.")
        return 0
    print(sh("git", "diff", "--cached", "--stat", cwd=repo))
    if not a.push:
        print(f"\nVorschau fertig in {repo}. Seite lokal ansehen: {site_dir / 'index.html'}\n"
              "Veröffentlichen mit: python3 scripts/publish.py --push")
        return 0
    msg = f"Messlauf {', '.join(new_ids)}" if new_ids else "Preistafel/Skill aktualisiert"
    ident = ("-c", "user.name=herdr-bench", "-c", "user.email=herdr-bench@users.noreply.github.com")
    sh("git", *ident, "commit", "-q", "-m", msg, cwd=repo)
    for rid in new_ids:
        sh("git", *ident, "tag", "-f", "-a", f"run-{rid}", "-m", f"Messlauf {rid}", cwd=repo)
    p = subprocess.run(["git", "push", "--follow-tags", "-u", "origin", branch], cwd=repo, text=True)
    if p.returncode != 0:
        print("Push fehlgeschlagen. Commit liegt lokal; nach Klärung (Rechte, Branch-Schutz) erneut --push.")
        return 4
    print(f"Veröffentlicht. {cfg.get('pages_url') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
