#!/usr/bin/env bash
# Asymmetric CBW suite — UBR655 sheet Asymmetric CBW (ACB_01–12).
#
#   ./scripts/run_acb.sh --allow-acb-lab

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="${PYTHONPATH:-}:."

STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_STR="$(date +%Y%m%d)"
REPORT_JSON="reports/artifacts/acb_report_${STAMP}.json"
mkdir -p reports/artifacts
PROFILE="${PROFILE:-ipv4_lab}"

set +e
pytest tests/AsymmetricCBW/test_acb.py \
  --profile "$PROFILE" \
  -m AsymmetricCBW \
  --json-report --json-report-file="$REPORT_JSON" \
  "$@"
RC=$?
set -e

if [[ -f "$REPORT_JSON" ]]; then
  cp -f "$REPORT_JSON" reports/artifacts/report.json
  python3 utils/report_generator.py "AsymmetricCBW" "192.168.2.10" "$DATE_STR" \
    --profile "$PROFILE" \
    --output-prefix "Senao_UBR_ACB" || true
fi
exit "$RC"
