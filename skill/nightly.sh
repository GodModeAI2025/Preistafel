#!/usr/bin/env bash
# Nächtlicher Messlauf: Vorprüfung, Preise, Plan, Lauf, Bericht, Veröffentlichung.
# Veröffentlicht nur, wenn höchstens 10 % der Trials mit "error" oder "rate_limited" enden.
# Blöcke mit erschöpftem Abo-Kontingent werden übersprungen statt abgewartet.
#
#   NIGHTLY_CONFIGS   kommagetrennte Konfigurationen aus config/matrix.toml
#   PUBLISH_CONFIG    Publikations-Konfiguration (Standard: config/publish.toml)
set -euo pipefail
cd "$(dirname "$0")"

PY=python3.13
CONFIGS="${NIGHTLY_CONFIGS:-}"   # leer = alle Konfigurationen aus config/matrix.toml
PUBLISH_CONFIG="${PUBLISH_CONFIG:-config/publish.toml}"
RUNS="${HERDR_BENCH_RUNS:-$HOME/herdr-bench-runs}"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUNS"
exec > >(tee -a "$RUNS/nightly-$RUN_ID.log") 2>&1

echo "== herdr-bench nightly $RUN_ID (${CONFIGS:-alle Konfigurationen})"
$PY scripts/bench.py doctor
$PY scripts/prices.py --region eu
PRICES="prices/$(date +%F)_eu.json"
$PY scripts/bench.py plan ${CONFIGS:+--configs "$CONFIGS"} --tasks all --repeats 3 --quota-mode block --run-id "$RUN_ID"
$PY scripts/bench.py run --plan "$RUNS/$RUN_ID/plan.json" --skip-on-limit
$PY scripts/report.py --run "$RUNS/$RUN_ID" --prices "$PRICES"

shopt -s nullglob
trials=("$RUNS/$RUN_ID"/trials/*.json)
if [ ${#trials[@]} -eq 0 ]; then
  echo "== NICHT veröffentlicht: keine Trials gelaufen (Kontingente erschöpft?)"
  exit 3
fi
# Einzelne Fehler blendet die Tafel aus; ab 10 % Fehlern liegt ein grundsätzliches Problem vor.
bad=$(jq -s '[.[] | select(.status == "error" or .status == "rate_limited")] | length' "${trials[@]}")
if [ $((bad * 10)) -gt ${#trials[@]} ]; then
  echo "== NICHT veröffentlicht: $bad von ${#trials[@]} Trials mit Fehler oder Limit. Details: $RUNS/$RUN_ID/trials"
  exit 3
fi
[ "$bad" -gt 0 ] && echo "== Hinweis: $bad von ${#trials[@]} Trials mit Fehler, auf der Tafel ausgeblendet"
$PY scripts/publish.py --run "$RUNS/$RUN_ID" --push --config "$PUBLISH_CONFIG"
echo "== fertig: $RUN_ID veröffentlicht"
