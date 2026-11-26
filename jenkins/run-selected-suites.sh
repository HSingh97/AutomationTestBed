#!/usr/bin/env bash
# Run the suites ticked in Automation Test Cases, one suite after another.
# Addresses and recovery come from the bench profile. No extra IP arguments.
set -u

PROFILE="${PROFILE_NAME:?PROFILE_NAME is required}"
RECOVERY="${RECOVERY_PROFILE_NAME:?RECOVERY_PROFILE_NAME is required}"
SUITES="${SUITES:?SUITES is required}"

mkdir -p reports/artifacts
rm -f reports/artifacts/suite_*.json reports/artifacts/suite_*.rc reports/artifacts/report.json

fail=0

run_one() {
  local name="$1"
  shift
  echo "========== ${name} =========="
  set +e
  stdbuf -oL -eL venv/bin/python -u -m pytest "$@" -v \
    --profile "${PROFILE}" \
    --recovery-profile "${RECOVERY}" \
    --json-report --json-report-file="reports/artifacts/suite_${name}.json"
  local rc=$?
  set -e
  echo "${rc}" > "reports/artifacts/suite_${name}.rc"
  if [[ "${rc}" -ne 0 ]]; then
    fail=1
    echo "[suite] ${name} exited ${rc}"
  else
    echo "[suite] ${name} finished"
  fi
}

IFS=',' read -r -a selected <<< "${SUITES}"
for raw in "${selected[@]}"; do
  name="$(echo "${raw}" | tr -d '[:space:]')"
  [[ -z "${name}" ]] && continue
  case "${name}" in
    GUI) run_one GUI tests/GUI/ ;;
    IP) run_one IP tests/IP/ --allow-ip-suite --allow-ip-destructive --no-ip-stop-on-first-fail ;;
    JUMBO) run_one JUMBO tests/JumboFrames/ --allow-destructive-jumbo ;;
    REGRESSION) run_one REGRESSION tests/Regression/ --allow-regression --regression-fresh -k "not REG_04" ;;
    PROCESS) run_one PROCESS tests/ProcessMonitor/ --allow-process-monitor --allow-destructive-process ;;
    QOS) run_one QOS tests/QoS/ --allow-qos-lab --allow-qos-destructive ;;
    USERMGMT) run_one USERMGMT tests/UserMgmt/ --allow-um-lab --allow-um-destructive ;;
    VLAN) run_one VLAN tests/VLAN/ --allow-vlan-lab --allow-vlan-destructive ;;
    LOGS) run_one LOGS tests/Logs/ --allow-logs-lab ;;
    FIREWALL) run_one FIREWALL tests/Firewall/ --allow-firewall-lab ;;
    DFS) run_one DFS tests/DFS/ --allow-dfs-lab ;;
    SECURITY) run_one SECURITY tests/Security/ --allow-security-lab ;;
    ACB) run_one ACB tests/AsymmetricCBW/ --allow-acb-lab ;;
    WIRELESS) run_one WIRELESS tests/WirelessSecurity/ ;;
    ARP) run_one ARP tests/ArpBridgeTable/ ;;
    API) run_one API tests/API/ ;;
    RADIO24) run_one RADIO24 tests/Radio24Scan/ ;;
    SANITY) run_one SANITY tests/Sanity/ ;;
    DDRS) run_one DDRS tests/DDRS/ ;;
    DHCP) run_one DHCP tests/DHCPv4_DHCPv6/ ;;
    FUNCTIONAL) run_one FUNCTIONAL tests/Functional/ --allow-functional ;;
    LINKTEST) run_one LINKTEST tests/LinkTest/ ;;
    SPECTRUM) run_one SPECTRUM tests/SpectrumSiteSurvey/ ;;
    *)
      echo "Unknown suite: ${name}" >&2
      exit 2
      ;;
  esac
done

PYTHONPATH=. venv/bin/python - << 'PY'
import json
from pathlib import Path

artifacts = Path("reports/artifacts")
chunks = sorted(artifacts.glob("suite_*.json"))
tests = []
base = None
for path in chunks:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[report] skip {path.name}: {exc}")
        continue
    if base is None:
        base = data
    tests.extend(data.get("tests") or [])
if base is None:
    raise SystemExit("no suite json reports to merge")
base["tests"] = tests
summary = base.setdefault("summary", {})
summary["total"] = len(tests)
for key in ("passed", "failed", "skipped", "error", "xfailed", "xpassed"):
    summary[key] = sum(
        1 for test in tests if str(test.get("outcome", "")).lower() == key
    )
(artifacts / "report.json").write_text(json.dumps(base), encoding="utf-8")
print(f"[report] merged {len(tests)} tests from {len(chunks)} suites")
PY

exit "${fail}"
