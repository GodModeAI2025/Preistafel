# Eigene Aufgaben anlegen

```
tasks/<id>/
├── task.toml      id, title, cohort, difficulty, timeout_s, verify_timeout_s, fixture, verify, prompt
├── fixture/       Ausgangszustand, den der Agent bekommt (wird pro Trial kopiert und git-initialisiert)
├── hidden/        versteckte Tests (test_hidden*.py), sieht der Agent nie
└── verify.sh      <workdir> <out.json>; schreibt {"passed": bool, "score": 0..1, "checks": {...}}
```

Für Python-Aufgaben reicht `verify.sh` als Einzeiler, der `tasks/verify_common.sh` aufruft. Andere Sprachen brauchen einen eigenen Verifier, der dasselbe JSON schreibt. Beispiele: Frontend mit `npm ci && npx vitest run` und Playwright, ABAP mit ADT-Syntaxcheck, Aktivierung und ABAP Unit.

Gute Aufgaben:
- haben eindeutig prüfbare Kriterien, weil versteckte Tests ehrlicher sind als ein LLM-Richter
- laufen in unter 15 Minuten und brauchen kein Netz (sonst Netzwerkbedarf im Prompt nennen)
- sind repräsentativ für die Kohorte (`cohort`), die das Preissignal abonniert
- nennen Signatur und Grenzfälle im Prompt, damit Fehlschläge echte Modellfehler sind und kein Rätselraten

Vor dem ersten Einsatz prüfen, dass die Fixture durchfällt und eine Referenzlösung besteht:
```bash
bash tasks/<id>/verify.sh tasks/<id>/fixture /tmp/v.json   # passed: false
bash tasks/<id>/verify.sh /pfad/zur/referenz /tmp/v.json   # passed: true
```
