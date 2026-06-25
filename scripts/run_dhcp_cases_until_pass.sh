#!/usr/bin/env bash
# Run RADIO_24_17–20 until all pass (or max attempts).
set -uo pipefail
cd "$(dirname "$0")/.."
LOG="radio24_dhcp_run.log"
MARKER="RADIO_24_17 or RADIO_24_18 or RADIO_24_19 or RADIO_24_20"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "========== DHCP run attempt ${attempt}/${MAX_ATTEMPTS} $(date -Is) ==========" | tee -a "$LOG"
  set +e
  python3 -u -m pytest tests/Radio24Scan/test_2_4g_radio.py \
    -m "$MARKER" --profile=default -v 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  set -e
  if [[ "$rc" -eq 0 ]]; then
    echo "========== ALL DHCP CASES PASSED $(date -Is) ==========" | tee -a "$LOG"
    exit 0
  fi
  echo "========== attempt ${attempt} failed (exit ${rc}) — retry in 45s ==========" | tee -a "$LOG"
  sleep 45
done

echo "========== FAILED after ${MAX_ATTEMPTS} attempts ==========" | tee -a "$LOG"
exit 1
