#!/usr/bin/env bash
# Sanity one-by-one runner — see docs/SANITY.md
# Jenkins: PY=$WORKSPACE/venv/bin/python PROFILE_NAME=default bash scripts/run_sanity_one_by_one.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=. PYTHONUNBUFFERED=1

if [[ -n "${PY:-}" && -x "${PY}" ]]; then
  :
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PY="$ROOT/venv/bin/python"
elif [[ -x /home/senao/Desktop/PythonProjects/.venv/bin/python ]]; then
  PY=/home/senao/Desktop/PythonProjects/.venv/bin/python
else
  PY=python3
fi

PROFILE_NAME="${PROFILE_NAME:-default}"
SKIP_BOOT="${SKIP_TESTBED_BOOTSTRAP:-1}"
STOP_ON_FAIL="${STOP_ON_FAIL:-0}"

OUT_DIR=reports/artifacts/case_runs
mkdir -p "$OUT_DIR"
RESULTS="$OUT_DIR/sanity_one_by_one_results.txt"
: > "$RESULTS"

NODES=(
  test_sanity_02_firmware_upgrade_keep_settings
  test_sanity_03_firmware_upgrade_without_keep_settings
  test_sanity_04_wrong_model_firmware_rejected
  test_sanity_05_abort_upgrade_handling
  test_sanity_06_power_loss_during_upgrade
  test_sanity_07_gui_login_validation
  test_sanity_17_system_location_config
  test_sanity_18_system_timezone_config
  test_sanity_19_static_ipv4_config
  test_sanity_20_static_ipv6_config
  test_sanity_23_mgmt_vlan_config
  test_sanity_25_dual_stack_config
  test_sanity_27_link_security
  test_sanity_28_ddrs_mcs_modes
  test_sanity_29_ddrs_mcs_manual
  test_sanity_30_spatial_stream_mcs_ranges
  test_sanity_31_channel_width
  test_sanity_32_channel_mode
  test_sanity_33_frequency_range
  test_sanity_35_acs_auto
  test_sanity_36_tx_power
  test_sanity_37_dcs_config
  test_sanity_38_ntp_sync
  test_sanity_42_board_temperature
  test_sanity_45_spectrum_report
  test_sanity_49_wireless_link_ping
  test_sanity_50_rf_link_system_name
  test_sanity_52_rf_link_uptime_reset
  test_sanity_54_rf_link_rate_mcs
  test_sanity_55_rf_link_mcs_auto_range
  test_sanity_57_tx_power_detailed_stats
  test_sanity_60_obss_utilization
  test_sanity_61_combined_utilization
  test_sanity_62_system_summary
  test_sanity_76_soft_hard_reboot
  test_sanity_77_factory_reset_keep_settings
  test_sanity_81_lldp_enable_disable
  test_sanity_82_lldp_neighbor_detection
  test_sanity_83_lldp_discovery_table
  test_sanity_84_link_test_tool
  test_sanity_85_audit_config_logs
  test_sanity_86_arp_bridge_table
  test_sanity_111_installer_dashboard
  test_sanity_112_installer_quickstart
  test_sanity_113_installer_link_statistics
  test_sanity_114_installer_site_survey
  test_sanity_115_installer_soft_reboot
)

BOOT_ARGS=()
if [[ "$SKIP_BOOT" == "1" || "$SKIP_BOOT" == "true" ]]; then
  BOOT_ARGS+=(--skip-testbed-bootstrap)
fi

preflight() {
  ping -c1 -W2 192.168.2.10 >/dev/null 2>&1 || echo "[warn] BTS .10 down"
  ping -c1 -W2 192.168.2.11 >/dev/null 2>&1 || echo "[warn] CPE .11 down"
  ping -6 -c1 -W2 2401:4900:d0:40d4:0:17b8:0:330 >/dev/null 2>&1 || echo "[warn] BTS v6 down"
  ping -6 -c1 -W2 2401:4900:d0:40d4::17b8:0:331 >/dev/null 2>&1 || echo "[warn] CPE v6 down"
}

START_FROM="${1:-}"
skip=0
if [[ -n "$START_FROM" ]]; then
  skip=1
fi

echo "[sanity-runner] PY=$PY profile=$PROFILE_NAME skip_bootstrap=$SKIP_BOOT stop_on_fail=$STOP_ON_FAIL"
echo "[sanity-runner] cases=${#NODES[@]} start_from=${START_FROM:-full}"

suite_rc=0
for node in "${NODES[@]}"; do
  nn="$(echo "$node" | sed -n 's/test_sanity_\([0-9]\+\).*/\1/p')"
  stem="sanity_${nn}"
  if [[ "$skip" -eq 1 ]]; then
    if [[ "$node" == "$START_FROM" || "$stem" == "$START_FROM" || "SANITY_${nn}" == "$START_FROM" ]]; then
      skip=0
    else
      echo "SKIP until $START_FROM ($stem)"
      continue
    fi
  fi

  echo ""
  echo "===== $(date '+%F %T') START SANITY_${nn} $node ====="
  preflight
  json="$OUT_DIR/${stem}.json"
  log="$OUT_DIR/${stem}.log"
  set +e
  set +o pipefail
  "$PY" -m pytest "tests/Sanity/test_sanity.py::${node}" -v --profile "$PROFILE_NAME" --tb=line \
    "${BOOT_ARGS[@]}" \
    --json-report --json-report-file="$json" --json-report-indent=2 \
    2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -o pipefail
  set -e
  if [[ "$rc" -eq 0 ]]; then
    echo "PASS SANITY_${nn}" | tee -a "$RESULTS"
  else
    echo "FAIL SANITY_${nn} rc=$rc" | tee -a "$RESULTS"
    suite_rc=1
    if [[ "$STOP_ON_FAIL" == "1" || "$STOP_ON_FAIL" == "true" ]]; then
      echo "[sanity-runner] STOP_ON_FAIL — aborting after SANITY_${nn}"
      break
    fi
  fi
  echo "===== $(date '+%F %T') END SANITY_${nn} ====="
  sleep 5
  preflight
done

echo ""
echo "===== DONE ====="
cat "$RESULTS"
exit "$suite_rc"
