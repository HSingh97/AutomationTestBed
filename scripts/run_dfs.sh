#!/usr/bin/env bash
# DFS suite — UBR655 sheet DFS. Radar: radartool -i wifi1 bangradar
#
#   ./scripts/run_dfs.sh --allow-dfs-lab
#   ./scripts/run_dfs.sh --allow-dfs-lab --allow-dfs-long
#   ./scripts/run_dfs.sh --allow-dfs-lab -k TC_DFS_012

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:-}:."

STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_STR="$(date +%Y%m%d)"
REPORT_JSON="reports/artifacts/dfs_report_${STAMP}.json"
mkdir -p reports/artifacts

PROFILE="${PROFILE:-ipv4_lab}"

set +e
pytest tests/DFS/test_dfs.py \
  --profile "$PROFILE" \
  -m DFS \
  --json-report --json-report-file="$REPORT_JSON" \
  "$@"
RC=$?
set -e
export DFS_SUITE_RC="$RC"

if [[ -f "$REPORT_JSON" ]]; then
  cp -f "$REPORT_JSON" reports/artifacts/report.json
  python3 utils/report_generator.py "DFS" "192.168.2.10" "$DATE_STR" \
    --profile "$PROFILE" \
    --output-prefix "Senao_UBR_DFS" || true
  echo "[dfs] JSON: $REPORT_JSON"
  ls -1t reports/artifacts/Senao_UBR_DFS_*Report_*.html 2>/dev/null | head -3 || true
else
  echo "[dfs] no JSON report written"
fi
exit "$RC"
