# herdr: genutzte Befehle und Fehlerbilder (Stand herdr 0.9.1)

Quelle: https://herdr.dev/docs/agent-automation/ und /docs/cli-reference/. Bei neuer herdr-Version zuerst `herdr api schema --json` gegen die hier genutzten Felder prüfen.

## Ablauf pro Trial

| Schritt | Befehl | Rückgabe |
|---|---|---|
| Workspace | `herdr workspace create --cwd <work> --label bench:<id> --no-focus [--env K=V]` | `.result.workspace.workspace_id`, `.result.root_pane.pane_id` |
| Agent starten | `herdr agent start <name> --kind claude\|codex --pane <p> --timeout 60000 -- <args>` | kehrt erst zurück, wenn der Agent bereit ist |
| Aufgabe | `herdr agent prompt <name> "<text>" --wait --until idle --until done --until blocked --timeout <ms>` | `.result.agent` mit Status |
| Bildschirm | `herdr agent read <name> --source visible` bzw. `recent-unwrapped --lines N` | Text |
| Transkript | `herdr agent get <name>` | `agent_session` (nur mit installierter Integration) |
| Verifier | `herdr pane split <p> --direction down --no-focus`, dann `pane run` und `pane wait-output --regex` | – |
| Aufräumen | `herdr workspace close <wid>` | – |

Namen müssen `[a-z][a-z0-9_-]{0,31}` erfüllen und unter lebenden Agents eindeutig sein.

## Fehlerbilder

- `agent_not_ready` beim Start: Der Agent zeigt einen Dialog, meist die Vertrauensabfrage für den Ordner. `bench.py` bestätigt nur Vertrauensdialoge (Regex `TRUST_RX`), alles andere bricht den Trial ab.
- `agent_blocked` oder Endstatus `blocked`: Der Agent wartet auf eine Freigabe oder stellt eine Frage. Das wird als `blocked` gewertet. Ursache ist meist ein zu enger Berechtigungsmodus. Prüfe `claude_permission_mode` und `codex_sandbox` in `config/matrix.toml`.
- `agent_prompt_stalled`: Der Agent hat innerhalb von 5 s nicht zu arbeiten begonnen. Vor einem erneuten Versuch den Bildschirm lesen, weil der Prompt trotzdem angekommen sein kann.
- `timeout`: Die Aufgabe hat länger gedauert als `timeout_s`. Das Skript sendet `ctrl+c` und wertet den Trial als `timeout`.
- Status `unknown`: herdr erkennt den Zustand nicht sicher. Das ist kein Erfolg. Mit `herdr agent explain <name> --verbose` nachsehen.
- Kein `agent_session` im Ergebnis: Die Integration fehlt (`herdr integration install claude` bzw. `codex`). Der Fallback sucht das Transkript über das Arbeitsverzeichnis. Das funktioniert, weil jeder Trial ein eigenes Verzeichnis hat.

## Mehrere Maschinen

`herdr --machine <label> …` leitet API-Befehle an eine andere Maschine weiter. Die Transkripte liegen dann aber auf dieser Maschine. Führe den Skill deshalb auf der Maschine aus, auf der die Sessions laufen, oder übertrage `~/.claude/projects` bzw. `~/.codex/sessions` nach dem Lauf.

## Zuschauen

Jeder Trial ist ein Workspace mit dem Label `bench:<trial_id>`. Der Nutzer kann in der herdr-Oberfläche live zusehen. Er sollte dabei aber nichts eintippen, sonst ist die Messung wertlos.
