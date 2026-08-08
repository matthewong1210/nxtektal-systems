#!/usr/bin/env bash
# scripts/twin_demo.sh — capture -> USD -> viewer, one command.
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="${1:-handoff_station_outage}"
SEED="${2:-7}"

.venv/bin/python scripts/facility_twin_capture.py \
  --scenario "$SCENARIO" --seed "$SEED"

EPISODE="reports/digital_twin/sim-baseline/dev/${SCENARIO}-seed${SEED}"
.venv/bin/python -m nxt_range_twin --episode-dir "$EPISODE"

echo "USD stage: ${EPISODE}/usd/episode.usda"
echo "Validate:  .venv/bin/usdchecker ${EPISODE}/usd/episode.usda"
echo "           (if usdchecker is not on PATH, fall back to:"
echo "            .venv/bin/python -c \"from pxr import Usd; assert Usd.Stage.Open('${EPISODE}/usd/episode.usda')\")"
echo "Viewer:    (launch the nxt_range_demo Streamlit app per its README;"
echo "            briefings sidecar: reports/demo/${SCENARIO}-seed${SEED}/briefings.jsonl)"
