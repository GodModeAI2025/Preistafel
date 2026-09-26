# Veröffentlichung

## Repo-Aufbau

```
README.md
.github/workflows/pages.yml     deployt site/ bei jedem Push (GitHub Pages über Actions)
skill/                          Quellcode des Skills; tasks/*/hidden/ nur README mit SHA-256
data/runs/<run_id>/report.json  bereinigte Ergebnisse, nie nachträglich geändert
data/prices/<datum>_<region>.json
site/index.html                 Preistafel
site/feeds/<kohorte>.json       aktuelles Preissignal je Kohorte (für Router oder Abonnenten)
site/data/history.json          alle Läufe je Kohorte und Konfiguration
site/herdr-bench.skill          installierbares Skill-Paket
```

## Was veröffentlicht wird und was nicht

| öffentlich | bleibt lokal |
|---|---|
| Konfiguration, Aufgabe, Kohorte, Status, bestanden, Tokens je Kategorie, Kosten, Laufzeit, Kontingentanteil | Bildschirmtexte (`screen_tail`, `raw`), Transkript- und Arbeitspfade, Rohdaten des Kontingents (enthalten ggf. Account und E-Mail) |
| Aufgaben-Prompts und Fixtures | versteckte Tests (nur Prüfsumme) |
| Preis-Snapshots mit Quelle und Commit | Zugangsdaten jeder Art |

Maßgeblich sind die Whitelists `REPORT_KEEP` und `TRIAL_KEEP` in `scripts/publish.py`. Neue Felder sind erst öffentlich, wenn sie dort ergänzt werden.

## Einmalige Einrichtung

1. Leeres GitHub-Repo anlegen (öffentlich oder privat; Pages auf privaten Repos braucht einen passenden Plan).
2. In `config/publish.toml` `remote` (z. B. `git@github.com:<owner>/<repo>.git`) und `pages_url` eintragen.
3. Push-Recht über SSH-Key oder `gh auth login` einrichten. Der Skill fasst keine Credentials an.
4. Nach dem ersten Push *Settings → Pages → Source: GitHub Actions* einstellen.

## Fehlerbilder

- **„ABGEBROCHEN – mögliche Geheimnisse …“**: Die Datei aus der Meldung prüfen und die Ursache in den Rohdaten beheben, meist ein Pfad oder eine E-Mail in einem neu hinzugefügten Feld. Danach erneut ausführen. Die Muster nie abschwächen, nur um durchzukommen.
- **Push abgelehnt**: Fehlende Rechte oder ein Branch-Schutz. Der Commit liegt lokal im Klon (`repo_dir`). Nach der Klärung `--push` erneut ausführen.
- **Pages zeigt 404**: Die Pages-Quelle steht nicht auf „GitHub Actions“, oder der Workflow ist noch nicht durchgelaufen (Reiter *Actions*).
- **Kohorte fehlt auf der Tafel**: In `task.toml` fehlt `cohort`. Die Aufgabe erscheint dann unter „Allgemein“.

## Regelmäßig veröffentlichen

Die Tafel wird aus der gesamten Historie gebaut. Es reicht also, nach jedem Lauf `publish.py --push` auszuführen. Trendpfeile vergleichen jede Konfiguration mit dem vorigen Lauf derselben Kohorte. Bei Preisänderungen ändern sich die Kosten deshalb auch ohne neue Modellleistung. Der Preis-Commit steht dazu im Footer.
