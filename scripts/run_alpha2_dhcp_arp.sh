#!/usr/bin/env bash
# One-by-one DHCP then ARP/Bridge. One run per case; fix code before re-running failures.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=. PYTHONUNBUFFERED=1
PY="${PY:-/home/senao/Desktop/PythonProjects/.venv/bin/python}"
REPORTS="${REPORTS:-$HOME/Desktop/Puneet/Alpha 2 Automation Reports}"
PLAN="${PLAN:-}"
if [[ -z "$PLAN" ]]; then
  for p in \
    "$HOME/Downloads/Senao UBR P2MP Test Plan_v8.0a.xlsx" \
    "$HOME/Desktop/Puneet/Senao UBR P2MP Test Plan_v8.0a.xlsx"; do
    [[ -f "$p" ]] && PLAN="$p" && break
  done
fi
DATE="$(date +%Y%m%d)"
mkdir -p reports/artifacts/case_runs "$REPORTS"

run_one() {
  local node="$1" json="$2" log="$3"
  "$PY" -m pytest "$node" -v --profile default --tb=short --skip-testbed-bootstrap \
    --json-report --json-report-file="$json" --json-report-indent=2 \
    2>&1 | tee "$log"
  return "${PIPESTATUS[0]}"
}

run_case() {
  local node="$1" stem="$2"
  local json="reports/artifacts/case_runs/${stem}.json"
  local log="reports/artifacts/case_runs/${stem}.log"
  if run_one "$node" "$json" "$log"; then
    echo "PASS $stem"
    return 0
  fi
  echo "FAIL $stem"
  return 1
}

echo "===== collect DHCP ====="
mapfile -t DHCP_NODES < <(
  "$PY" -m pytest tests/DHCPv4_DHCPv6/test_DHCPv4_DHCPv6.py --profile default --collect-only -q \
    2>/dev/null | grep '::test_dhcp_' || true
)
echo "DHCP cases: ${#DHCP_NODES[@]}"
for node in "${DHCP_NODES[@]}"; do
  stem="$(echo "$node" | sed -n 's/.*::test_dhcp_\([0-9]\+\).*/dhcp_\1/p')"
  [[ -z "$stem" ]] && stem="dhcp_unknown"
  run_case "$node" "$stem" || true
done

if [[ -n "$PLAN" ]]; then
  "$PY" -m utils.dhcp_plan_report --runs-dir reports/artifacts/case_runs --plan "$PLAN" --date "$DATE" \
    --out "$REPORTS/Senao_DHCP_Report_${DATE}.html" || true
fi

if [[ "${DHCP_ONLY:-0}" == "1" ]]; then
  echo "===== done (DHCP only) ====="
  ls -lh "$REPORTS"/Senao_DHCP_Report_${DATE}.html 2>/dev/null || true
  exit 0
fi

echo "===== collect ARP/Bridge ====="
mapfile -t ARP_NODES < <(
  "$PY" -m pytest tests/ArpBridgeTable/test_arp_bridge_table.py --profile default --collect-only -q \
    2>/dev/null | grep '::test_arpbridge_' || true
)
echo "ARP/Bridge cases: ${#ARP_NODES[@]}"
for node in "${ARP_NODES[@]}"; do
  stem="$(echo "$node" | sed -n 's/.*::test_arpbridge_\([0-9]\+\).*/arpbridge_\1/p')"
  [[ -z "$stem" ]] && stem="arpbridge_unknown"
  run_case "$node" "$stem" || true
done

if [[ -n "$PLAN" ]]; then
  "$PY" -m utils.arpbridge_plan_report --runs-dir reports/artifacts/case_runs --plan "$PLAN" --date "$DATE" \
    --out "$REPORTS/Senao_ArpBridge_Report_${DATE}.html" || true
fi

echo "===== done ====="
ls -lh "$REPORTS"/Senao_{DHCP,ArpBridge}_Report_${DATE}.html 2>/dev/null || true
