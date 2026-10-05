#!/usr/bin/env bash
# Firewall suite — UBR655 sheet Firewall (FIREWALL_01–06).
#
#   ./scripts/run_firewall.sh --allow-firewall-lab
#   ./scripts/run_firewall.sh --allow-firewall-lab -k FIREWALL_01

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:-}:."

STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_STR="$(date +%Y%m%d)"
REPORT_JSON="reports/artifacts/firewall_report_${STAMP}.json"
mkdir -p reports/artifacts

PROFILE="${PROFILE:-ipv4_lab}"

set +e
pytest tests/Firewall/test_firewall.py \
  --profile "$PROFILE" \
  -m Firewall \
  --json-report --json-report-file="$REPORT_JSON" \
  "$@"
RC=$?
set -e
export FIREWALL_SUITE_RC="$RC"

if [[ -f "$REPORT_JSON" ]]; then
  cp -f "$REPORT_JSON" reports/artifacts/report.json
  python3 utils/report_generator.py "Firewall" "192.168.2.10" "$DATE_STR" \
    --profile "$PROFILE" \
    --output-prefix "Senao_UBR_FIREWALL" || true
  echo "[firewall] JSON: $REPORT_JSON"
  ls -1t reports/artifacts/Senao_UBR_FIREWALL_*Report_*.html 2>/dev/null | head -3 || true
else
  echo "[firewall] no JSON report written"
fi
exit "$RC"
