# Messmethodik

## Tokens pro Session

Claude Code schreibt jede Session als JSONL nach `~/.claude/projects/<cwd-slug>/`. Subagents schreiben eigene Dateien darunter. Jede Assistant-Nachricht enthält `message.usage` mit folgenden Feldern:
- `input_tokens` (ungecacht)
- `cache_read_input_tokens`
- `cache_creation_input_tokens`, aufgeteilt in `ephemeral_5m_input_tokens` und `ephemeral_1h_input_tokens`
- `output_tokens` (einschließlich Thinking)

Beim Streaming erscheint dieselbe `message.id` mehrfach, und frühe Einträge enthalten Platzhalter für den Output. `usage.py` wertet deshalb nur den letzten Eintrag je ID. Modelle werden ohne Datumssuffix geführt. Hilfsmodelle wie Haiku für Titel oder Subagents werden mitgezählt, weil sie real Kontingent verbrauchen.

Codex schreibt ein Rollout nach `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Die `token_count`-Events enthalten kumulierte `total_token_usage`:
- `input_tokens` enthält `cached_input_tokens`
- `output_tokens` enthält `reasoning_output_tokens`

Gewertet wird das letzte Event. Bekannte Lücke: Der WebSocket-Prewarm beim Start (ca. 9 k Tokens) fehlt in der Zählung.

## Kosten (API-Äquivalent)

Kosten = Σ über Modelle und Kategorien (Tokens × Regionspreis pro Token).

| Kategorie | Preisfeld |
|---|---|
| input_uncached | input |
| cache_read | cache_read |
| cache_write_5m | cache_write_5m |
| cache_write_1h | cache_write_1h (Abo-Sessions bei Claude cachen 1 h) |
| output (inkl. Reasoning) | output |

Die Preise kommen aus `prices/<datum>_<region>.json`. Regionsaufschlag und Währungsumrechnung sind dort bereits enthalten. Long-Context-Aufschläge (OpenAI über 272 k Tokens) werden nicht modelliert. Bei so großen Aufgaben das Preisfeld manuell prüfen.

## Qualität

Der Verifier führt versteckte Tests aus, die der Agent nie sieht. Bestanden heißt: alle versteckten Tests sind grün und der Status ist `completed`. Die Erfolgsquote wird mit einem 95-%-Wilson-Intervall angegeben. Eine Empfehlung gilt als „gesichert“, wenn auch die Untergrenze des Intervalls die Qualitätsschwelle erreicht. Sonst heißt sie „vorläufig“.

## Abo-Kontingent

Die Anzeigen `/usage` (Claude) und `/status` (Codex) liefern ganze Prozent je Fenster. Das 5-h-Fenster rollt, das Wochenfenster hat einen festen Reset.

- Block-Modus (Standard): Kontingent vor und nach allen Wiederholungen einer Konfiguration lesen. Die Differenz geteilt durch die Zahl der Trials ergibt den Anteil pro Aufgabe. Die Auflösung ist damit etwa 1 % ÷ n.
- Single-Modus: Kontingent vor und nach jeder Session lesen. Das ist nur bei großen Aufgaben sinnvoll.
- Liegt zwischen den Messungen ein Reset (die Differenz ist negativ), wird der Wert verworfen.
- Der Account darf während der Messung sonst nicht genutzt werden, weder im Chat noch in anderen Sessions. Beide Anbieter teilen das Kontingent über alle Oberflächen.

Abgeleitete Größen:

| Größe | Formel |
|---|---|
| Aufgaben pro Woche bis Limit | 100 ÷ Wochenanteil pro Aufgabe |
| Abo-Äquivalent pro Aufgabe | Planpreis × 12/52 × Wochenanteil/100 (in Regionswährung) |
| Break-even | Planpreis pro Woche ÷ mittlere API-Kosten pro Aufgabe |

Liegt der Break-even über „Aufgaben pro Woche bis Limit“, ist das Abo für dieses Muster nicht ausreichend.

## Fairness

- Pro Trial ein frisches Arbeitsverzeichnis (git-initialisiert) und eine frische Session
- Blöcke in zufälliger Reihenfolge mit gespeichertem Seed
- Effort und Modell explizit setzen, nie Aliase (`opus`, `sonnet`) verwenden
- CLI-Versionen während eines Laufs nicht ändern und Auto-Updates abschalten (`DISABLE_AUTOUPDATER=1` für Claude)
- Aufeinanderfolgende Trials können Cache-Präfixe teilen. Das ist bei Abo-Nutzung realistisch, im Bericht aber an hohen `cache_read`-Werten beim ersten Request erkennbar.
