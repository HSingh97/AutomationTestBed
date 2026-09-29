#!/usr/bin/env bash
# A60/A61 Logs suite — DUT-side (NMS client validation pending).
#
# Examples:
#   ./scripts/run_a60_logs.sh --allow-logs-lab
#   ./scripts/run_a60_logs.sh --allow-logs-lab --allow-logs-destructive
#   ./scripts/run_a60_logs.sh --allow-logs-lab -k 'LOGS_01 or LOGS_96'

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:-}:."
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_STR="$(date +%Y%m%d)"
REPORT_JSON="reports/artifacts/logs_report_${STAMP}.json"
mkdir -p reports/artifacts

set +e
pytest tests/Logs/test_logs.py \
  --profile a60_lab \
  --local-ip 10.0.0.120 \
  -m Logs \
  --json-report --json-report-file="$REPORT_JSON" \
  "$@"
RC=$?
set -e
# Preserve pytest RC when this script is piped (e.g. `| tee`).
export LOGS_SUITE_RC="$RC"

if [[ -f "$REPORT_JSON" ]]; then
  cp -f "$REPORT_JSON" reports/artifacts/report.json
  python3 utils/report_generator.py "Logs" "10.0.0.120" "$DATE_STR" \
    --profile a60_lab \
    --output-prefix "Senao_UBR_LOGS" || true
  echo "[logs] JSON: $REPORT_JSON"
  ls -1t reports/artifacts/Senao_UBR_LOGS_*Report_*.html 2>/dev/null | head -3 || true
else
  echo "[logs] WARN: missing $REPORT_JSON"
fi

exit $RC
