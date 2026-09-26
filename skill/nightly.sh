#!/usr/bin/env bash
# Nächtlicher Messlauf: Vorprüfung, Preise, Plan, Lauf, Bericht, Veröffentlichung.
# Veröffentlicht nur, wenn kein Trial mit Status "error" oder "rate_limited" endet.
#
#   NIGHTLY_CONFIGS   kommagetrennte Konfigurationen aus config/matrix.toml
#   PUBLISH_CONFIG    Publikations-Konfiguration (Standard: config/publish.toml)
set -euo pipefail
cd "$(dirname "$0")"

PY=python3.13
CONFIGS="${NIGHTLY_CONFIGS:-cc-sonnet5-medium,cc-opus55-medium,cx-gpt6sol-medium,cx-gpt6sol-high}"
PUBLISH_CONFIG="${PUBLISH_CONFIG:-config/publish.toml}"
RUNS="${HERDR_BENCH_RUNS:-$HOME/herdr-bench-runs}"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUNS"
exec > >(tee -a "$RUNS/nightly-$RUN_ID.log") 2>&1

echo "== herdr-bench nightly $RUN_ID ($CONFIGS)"
$PY scripts/bench.py doctor
$PY scripts/prices.py --region eu
PRICES="prices/$(date +%F)_eu.json"
$PY scripts/bench.py plan --configs "$CONFIGS" --tasks all --repeats 3 --quota-mode block --run-id "$RUN_ID"
$PY scripts/bench.py run --plan "$RUNS/$RUN_ID/plan.json"
$PY scripts/report.py --run "$RUNS/$RUN_ID" --prices "$PRICES"

bad=$(jq -s '[.[] | select(.status == "error" or .status == "rate_limited")] | length' "$RUNS/$RUN_ID"/trials/*.json)
if [ "$bad" -gt 0 ]; then
  echo "== NICHT veröffentlicht: $bad Trials mit Fehler oder Limit. Details: $RUNS/$RUN_ID/trials"
  exit 3
fi
$PY scripts/publish.py --run "$RUNS/$RUN_ID" --push --config "$PUBLISH_CONFIG"
echo "== fertig: $RUN_ID veröffentlicht"
