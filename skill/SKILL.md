---
name: herdr-bench
description: Misst, welches Coding-Modell mit welchem Effort eine Aufgabe am günstigsten löst. Spawnt dafür echte Claude-Code- und Codex-Sessions in herdr (herdr.dev), stellt Aufgaben, prüft Ergebnisse mit versteckten Tests, zählt Tokens pro Session, bewertet sie mit automatisch geholten API-Preisen einer Region (z. B. EU) und misst den Abo-Verbrauch (5-h- und Wochenlimit). Liefert einen HTML-Bericht. Danach veröffentlicht der Skill sich selbst, die bereinigte Messhistorie und eine als Preistafel gestaltete Landingpage per git auf GitHub Pages. Verwende diesen Skill IMMER, wenn der Nutzer Modelle, Efforts, Claude Code gegen Codex, Abos gegen API-Kosten, Tokenverbrauch pro Aufgabe, ein Preissignal, einen Modell-Benchmark oder herdr-Sessions zum Vermessen erwähnt, und wenn er eine Preistafel veröffentlichen oder aktualisieren möchte, auch wenn er nicht ausdrücklich „Benchmark“ sagt.
compatibility: Läuft in Claude Code innerhalb einer herdr-Pane (HERDR_ENV=1). Braucht herdr ≥ 0.9, claude und codex CLI mit Abo-Login, python3 ≥ 3.11, jq, git mit Push-Recht auf das Publikations-Repo.
---

# herdr-bench: Aufgaben an Agent-Sessions stellen, vermessen, bewerten

Du orchestrierst einen fairen Messlauf. Jede Messung („Trial“) ist genau eine frische, interaktive Agent-Session in einem eigenen herdr-Workspace mit eigenem Arbeitsverzeichnis. Der Agent bekommt eine Aufgabe, ein Verifier prüft das Ergebnis, danach werden Tokens aus dem Session-Transkript gezählt und mit regionalen Listenpreisen bewertet. Parallel wird der Abo-Verbrauch gemessen.

Warum interaktive Sessions statt `claude -p`: So läuft die Messung genau so wie echte Nutzung im Abo. Die Tokens kommen aus den Transkripten, die beide CLIs ohnehin schreiben, und das Abo-Kontingent aus den Anzeigen, die der Nutzer selbst sieht (`/usage`, `/status`).

## 0. Vorprüfung (immer zuerst)

Führe `python3 scripts/bench.py doctor` aus. Es prüft:
- `HERDR_ENV=1` (sonst sofort stoppen und sagen: „Ich laufe nicht in einer herdr-Pane.“)
- `herdr`, `claude`, `codex`, `jq`, `git` vorhanden, herdr-Server erreichbar
- herdr-Integrationen für claude und codex installiert (`herdr integration status`), damit herdr den Pfad des Session-Transkripts meldet
- **keine** API-Keys in der Umgebung (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`). Sonst würde über die API statt über das Abo abgerechnet. In dem Fall abbrechen und den Nutzer fragen.

Weise den Nutzer einmal kurz auf die Abo-Regeln hin (Details: `references/abo-und-policy.md`). Das Abo gehört einer Person, es wird immer nur eine Session gleichzeitig gemessen, und der Account darf während der Messung sonst nicht genutzt werden, weil das die Kontingent-Messung verfälscht.

## 1. Preise der Region holen

```bash
python3 scripts/prices.py --region eu            # oder: global, us, eu-azure
```

Das Skript lädt die maschinenlesbare Preisliste von LiteLLM (GitHub, Commit-SHA wird gespeichert). Die Region wird darauf abgebildet:
- für Anthropic über regionale Einträge (`eu.anthropic.<modell>`) oder den `inference_geo`-Aufschlag
- für OpenAI über `regional_processing_uplift_multiplier_<region>`

Beträge werden per EZB-Kurs in die Regionswährung umgerechnet. Ergebnis: `prices/<datum>_<region>.json`.

Gleiche danach die Hauptmodelle stichprobenartig mit den offiziellen Preisseiten ab (`references/preise-regionen.md` nennt die URLs; nutze web_fetch). Weicht ein Wert um mehr als 2 % ab, trag die offizielle Zahl in `config/price_overrides.toml` ein und führe das Skript erneut aus. Die Überschreibung wird im Preis-File vermerkt.

## 2. Messplan festlegen

Die Konfigurationen stehen in `config/matrix.toml`: Harness (`claude` oder `codex`), Modell, Effort und Abo-Plan. Die Aufgaben liegen unter `tasks/<id>/task.toml`. Kläre mit dem Nutzer nur, was fehlt:
- welche Aufgaben
- wie viele Wiederholungen (Standard 3)
- Messmodus für das Kontingent: `block` (Standard) oder `single`

Rechne vorab den Umfang aus und nenne ihn: Trials = Konfigurationen × Aufgaben × Wiederholungen.

```bash
python3 scripts/bench.py plan --tasks all --repeats 3 --quota-mode block
```

Warum `block`: Die Abo-Anzeigen melden nur ganze Prozent. Eine einzelne kleine Aufgabe bewegt die Anzeige oft um 0 oder 1 %. Im Block-Modus laufen alle Wiederholungen einer Konfiguration direkt hintereinander, und das Kontingent wird vor und nach dem Block gelesen. Die Tokens werden trotzdem pro Session einzeln gezählt. `single` misst jede Session einzeln und lohnt sich nur bei großen Aufgaben.

## 3. Messlauf starten

```bash
python3 scripts/bench.py run --plan ~/herdr-bench-runs/<run_id>/plan.json
```

Der Lauf ist strikt sequenziell. Pro Trial geschieht Folgendes (Details und Fehlerbilder in `references/herdr.md`):

1. Die Fixture der Aufgabe wird nach `~/herdr-bench-runs/<run_id>/work/<trial_id>` kopiert und als git-Repo initialisiert. Läufe liegen bewusst außerhalb des Skill-Ordners (änderbar per `HERDR_BENCH_RUNS`), damit der gemessene Agent die versteckten Tests unter `tasks/*/hidden` nicht findet.
2. `herdr workspace create --cwd <work> --env …` legt den Workspace an. Die Effort-Variable wird nur für diese Session gesetzt.
3. Das Kontingent wird vorher gelesen: `/usage` bei Claude bzw. `/status` bei Codex.
4. `herdr agent start t<id> --kind claude|codex --pane <p> -- <flags>` startet den Agenten. Trust-Dialoge beim Start bestätigt das Skript automatisch.
5. `herdr agent prompt t<id> "<Aufgabe>" --wait --until idle --until done --until blocked --timeout …` stellt die Aufgabe. Die Zeitmessung läuft vom Absenden bis zum Zustand `idle` oder `done`.
6. Meldet der Agent `blocked`, ist eine Freigabe oder Rückfrage offen. Der Trial wird dann als `blocked` gewertet und nie automatisch freigegeben, weil das die Vergleichbarkeit zerstören würde.
7. Der Verifier läuft in einer Nachbar-Pane: `herdr pane split` und `pane run "bash verify.sh"`. Er schreibt `verify.json` mit Punktzahl und Bestanden-Flag.
8. Das Kontingent wird nachher erneut gelesen. Bei Codex kommt es zusätzlich präzise aus dem Rollout.
9. Das Transkript wird über `herdr agent get` (Feld `agent_session.path`) gefunden. Fallback ist das Projektverzeichnis bzw. das Rollout mit passendem `cwd`. `scripts/usage.py` zählt daraus die Tokens pro Modell.
10. Der Workspace wird geschlossen. Das Arbeitsverzeichnis bleibt zur Nachkontrolle liegen.

Läuft ein Limit voll (Anzeige ≥ 98 % oder Limit-Meldung im Bildschirm), pausiert der Lauf bis zum angezeigten Reset und setzt dann fort. Rate-limitierte Trials werden wiederholt und nie als Fehlschlag gezählt.

Melde während des Laufs knapp den Fortschritt, etwa „Trial 7/24: codex gpt-6-sol high auf py-bugfix-dates, bestanden, 41 k Tokens, 0,12 €“. Der Nutzer kann jederzeit in herdr zuschauen: Jeder Trial ist ein eigener Workspace mit dem Label `bench:<trial_id>`.

## 4. Auswerten und zeigen

```bash
python3 scripts/report.py --run ~/herdr-bench-runs/<run_id> --prices prices/<datei>.json
```

Das Skript erzeugt `~/herdr-bench-runs/<run_id>/report.html` (selbsttragend, ohne externe Ressourcen) und `report.json`. Veröffentliche die HTML-Datei als Artifact oder öffne sie. Fasse im Chat nur das Wichtigste zusammen: die günstigste Konfiguration, die die Qualitätsschwelle erreicht, den größten Ausreißer und den Abo-Break-even.

Kennzahlen (Formeln in `references/messmethodik.md`):
- **Erfolgsquote** pro Konfiguration mit 95-%-Intervall (Wilson)
- **API-Äquivalent** pro Trial in Regionswährung: Tokens pro Kategorie × Regionspreis
- **Kosten pro gelöster Aufgabe**: Summe der Kosten ÷ Anzahl bestandener Trials
- **Abo-Anteil**: Wochen- und 5-h-Kontingent in Prozent pro Trial, daraus Trials pro Woche bis zum Limit
- **Abo-Äquivalent pro Aufgabe**: (Planpreis pro Woche) × Wochenanteil
- **Break-even**: ab wie vielen Aufgaben pro Woche das Abo günstiger ist als die API in dieser Region

## 5. Veröffentlichen: Skill, Historie, Preistafel

Nach jedem abgeschlossenen und ausgewerteten Lauf wird veröffentlicht. Ziel ist das git-Repo aus `config/publish.toml` (`remote`, `branch`, `repo_dir`). Ist `remote` noch ein Platzhalter, frag den Nutzer nach dem Repo und trag es ein.

```bash
python3 scripts/publish.py            # Vorschau: baut alles im lokalen Klon, zeigt den Diff
python3 scripts/publish.py --push     # erst nach Freigabe durch den Nutzer
```

Zeig dem Nutzer vor dem Push immer die Vorschau: die Diff-Statistik und einen Screenshot oder Link von `site/index.html`. Push erst nach seinem Okay, denn das Repo ist öffentlich sichtbar.

Das Skript übernimmt folgende Schritte:
- **Messdaten:** Jeder `report.json` wird bereinigt nach `data/runs/<run_id>/` übernommen. Nur Kennzahlen, Tokens, Kosten und Status werden veröffentlicht, keine Bildschirmtexte, Transkripte, Pfade oder Account-Daten. Die Preis-Snapshots landen in `data/prices/`.
- **Der Skill selbst:** Er wird nach `skill/` kopiert und als `site/herdr-bench.skill` zum Download angeboten. Die versteckten Tests fehlen dabei, von ihnen stehen nur SHA-256-Prüfsummen im Repo. Sonst gerieten sie in Trainingsdaten und künftige Messungen wären wertlos.
- **Preistafel:** `scripts/board.py` baut `site/index.html` aus der gesamten Historie. Pro Kohorte gibt es eine Tafel mit dem Preis pro gelöster Aufgabe, der Erfolgsquote, den Aufgaben pro Woche im Abo, einem Trendpfeil gegenüber dem Vorlauf, einem Verlauf und der markierten Empfehlung. Dazu kommen die Feeds `site/feeds/<kohorte>.json` und `site/data/history.json`.
- **Deployment:** `.github/workflows/pages.yml` deployt `site/` bei jedem Push nach GitHub Pages. Einmalig muss der Nutzer im Repo *Settings → Pages → Source: GitHub Actions* einstellen.
- **Schutz:** Vor dem Commit prüft ein Scan auf API-Keys, Tokens, private Schlüssel, Home-Pfade und E-Mail-Adressen. Bei einem Treffer bricht die Veröffentlichung ab und nichts wird committet. Dann die Quelle bereinigen, nie den Scan umgehen.
- **Versionierung:** Neue Läufe werden als Tag `run-<run_id>` markiert. Die Historie wird nie umgeschrieben.

Details und Fehlerbilder stehen in `references/veroeffentlichung.md`.

## Regeln, die die Messung ehrlich halten

- Pro Trial immer eine frische Session. Nie `--resume`, nie eine Session für mehrere Trials, weil sonst der Kontext-Cache die Kosten verfälscht.
- Die Reihenfolge der Blöcke wird mit gespeichertem Seed gemischt.
- Das Kontingent nur werten, wenn zwischen den beiden Messungen kein Reset lag. Das Skript prüft das anhand der Reset-Zeiten.
- Preise niemals raten. Fehlt ein Modell in der Preisdatei, bleibt der Kostenwert leer und der Bericht markiert ihn.
- Abo-Sessions cachen bei Claude eine Stunde lang. Cache-Writes werden daher mit dem 1-h-Preis bewertet, sofern das Transkript sie so ausweist.

## Dateien

- `scripts/bench.py`: doctor, plan, run, trial (Orchestrierung über die herdr-CLI)
- `scripts/prices.py`: Regionspreise holen und umrechnen
- `scripts/usage.py`: Tokens aus Claude-Transkripten und Codex-Rollouts zählen
- `scripts/quota.py`: `/usage`- und `/status`-Anzeigen parsen
- `scripts/report.py`: Aggregation und HTML-Bericht
- `scripts/board.py`: Preistafel, Feeds und Historie aus allen veröffentlichten Läufen
- `scripts/publish.py`: bereinigen, Skill kopieren, Tafel bauen, Geheimnis-Scan, commit, tag, push
- `scripts/selftest.py`: prüft die Parser gegen `tests/fixtures`; nach Änderungen ausführen
- `config/`: `matrix.toml`, `regions.toml`, `plans.toml`, `price_overrides.toml`, `publish.toml`
- `assets/pages.yml`: GitHub-Pages-Workflow für das Publikations-Repo
- `tasks/`: Beispielaufgaben mit Fixture und `verify.sh`. Neue Aufgaben nach demselben Muster anlegen, siehe `references/aufgaben.md`
- `references/`: `herdr.md`, `messmethodik.md`, `preise-regionen.md`, `abo-und-policy.md`, `aufgaben.md`, `veroeffentlichung.md`
