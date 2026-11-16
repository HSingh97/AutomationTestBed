#!/usr/bin/env bash
# Re-run previously failed dhcp_NN cases once; bench prep between cases; refresh HTML once at end.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=. PYTHONUNBUFFERED=1
PY="${PY:-/home/senao/Desktop/PythonProjects/.venv/bin/python}"
REPORTS="${REPORTS:-$HOME/Desktop/Puneet/Alpha 2 Automation Reports}"
PLAN="${PLAN:-$HOME/Downloads/Senao UBR P2MP Test Plan_v8.0a.xlsx}"
DATE="$(date +%Y%m%d)"
RUNS="reports/artifacts/case_runs"
LOG="$RUNS/alpha2_dhcp_retest_failed.log"

bench_prep() {
  "$PY" - <<'PY' || return 1
import asyncio
from utils.profile_manager import load_profile_bundle
from utils.dhcpv4_dhcpv6_flows import _open_root_ssh, _bts_ping_cpe, _hop_restore_static

async def main():
    dut = load_profile_bundle(profile_name="default").active["dut"]
    c = {"user": "root", "pass": dut["password"]}
    for host in (str(dut["local_ipv6"]), "10.0.0.1"):
        try:
            ssh = await _open_root_ssh(host, c)
            if (await _bts_ping_cpe(ssh, "192.168.2.11", count=2))[0]:
                await ssh.close()
                return
            await _hop_restore_static(c, "retest-prep")
            await asyncio.sleep(20)
            await ssh.close()
            return
        except Exception:
            continue
asyncio.run(main())
PY
}

FAILED=()
for j in "$RUNS"/dhcp_*.json; do
  [[ "$j" == *attempt* ]] && continue
  [[ "$j" =~ /dhcp_[0-9]+\.json$ ]] || continue
  n=$(basename "$j" .json | sed 's/dhcp_//')
  if ! "$PY" -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))['tests'][0]['outcome']=='passed' else 1)" "$j" 2>/dev/null; then
    FAILED+=("$n")
  fi
done
IFS=$'\n' FAILED=($(printf '%s\n' "${FAILED[@]}" | sort -n))
unset IFS

{
  echo "===== retest failed DHCP $(date -Iseconds) — ${#FAILED[@]} case(s) ====="
  for n in "${FAILED[@]}"; do
    node=$("$PY" -m pytest tests/DHCPv4_DHCPv6/test_DHCPv4_DHCPv6.py --profile default --collect-only -q 2>/dev/null | grep "::test_dhcp_${n}_" | head -1)
    [[ -z "$node" ]] && { echo "SKIP dhcp_${n}"; continue; }
    echo "--- dhcp_${n} ---"
    bench_prep || echo "WARN bench prep dhcp_${n}"
    json="$RUNS/dhcp_${n}.json"
    log="$RUNS/dhcp_${n}.log"
    set +o pipefail
    "$PY" -m pytest "$node" -v --profile default --tb=line --skip-testbed-bootstrap \
      --json-report --json-report-file="$json" --json-report-indent=2 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -o pipefail
    if [[ "$rc" -eq 0 ]]; then
      echo "PASS dhcp_${n}"
    else
      echo "FAIL dhcp_${n}"
    fi
  done
  "$PY" -m utils.dhcp_plan_report --runs-dir "$RUNS" --plan "$PLAN" --date "$DATE" \
    --out "$REPORTS/Senao_DHCP_Report_${DATE}.html"
  echo "===== report $REPORTS/Senao_DHCP_Report_${DATE}.html ====="
} | tee -a "$LOG"
