#!/usr/bin/env bash
# Security suite — UBR655 sheet Security (SEC-01–16).
#
#   ./scripts/run_security.sh --allow-security-lab

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="${PYTHONPATH:-}:."

STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_STR="$(date +%Y%m%d)"
REPORT_JSON="reports/artifacts/security_report_${STAMP}.json"
mkdir -p reports/artifacts
PROFILE="${PROFILE:-ipv4_lab}"

set +e
pytest tests/Security/test_security.py \
  --profile "$PROFILE" \
  -m Security \
  --json-report --json-report-file="$REPORT_JSON" \
  "$@"
RC=$?
set -e

if [[ -f "$REPORT_JSON" ]]; then
  cp -f "$REPORT_JSON" reports/artifacts/report.json
  python3 utils/report_generator.py "Security" "192.168.2.10" "$DATE_STR" \
    --profile "$PROFILE" \
    --output-prefix "Senao_UBR_SECURITY" || true
fi
exit "$RC"
