#!/usr/bin/env bash
# qa-lab QoS multi-DSCP TRex throughput: start servers, run qos_throughput_script, stop.
#
# Eight DSCP streams (NC/VO/VI/GM/MM/LD/BE/BK) share configured DL/UL bandwidth.
#
#   BSU=192.168.1.1  SU=192.168.1.2  (override via env)

set -euo pipefail

BENCH_SSH="${BENCH_SSH:-root@10.0.150.102}"
BENCH_PW="${BENCH_PW:-senao1234#}"
TREX_PW="${TREX_PW:-ubuntu}"
BSU="${BSU:-192.168.1.1}"
SU="${SU:-192.168.1.2}"
TREX_DIR="${TREX_DIR:-/opt/v3.06}"
PYPATH="${PYPATH:-/opt/v3.06/automation/trex_control_plane/interactive/}"
SU_COUNT="${SU_COUNT:-1}"
DURATION="${DURATION:-30}"
DL_BW="${DL_BW:-100M}"
UL_BW="${UL_BW:-100M}"
BSU_PORTS="${BSU_PORTS:-0,1}"
PKT_SIZE="${PKT_SIZE:-1500}"
# Optional: CLASSES="voice video best_effort background" or TAGS="voice gaming"
CLASSES="${CLASSES:-}"
TAGS="${TAGS:-}"
EQUAL_SHARE="${EQUAL_SHARE:-0}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT_SCRIPT="${REPO_ROOT}/traffic/scripts/qos_throughput_script.py"
QOS_CLASSES="${REPO_ROOT}/traffic/scripts/qos_classes.py"
QINQ_TAGS="${REPO_ROOT}/traffic/scripts/qinq_tags.py"

kill_trex_host() {
  local host=$1
  echo "[qos-trex] stopping on ${host}"
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "root@${host}" \
    "pkill -f 't-rex-64 -i --no-scapy-server' 2>/dev/null || true; pkill -f '_t-rex-64' 2>/dev/null || true" \
    || true
}

start_trex_host() {
  local host=$1
  echo "[qos-trex] starting on ${host}"
  kill_trex_host "$host"
  sleep 2
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${host}" bash -s <<INNER
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
cd ${TREX_DIR}
nohup ./t-rex-64 -i --no-scapy-server -c 1 --no-ofed-check > /tmp/trex_server.log 2>&1 &
sleep 15
pgrep -af '_t-rex-64' | head -1 || { tail -30 /tmp/trex_server.log; exit 1; }
INNER
}

deploy_scripts() {
  local host=$1
  sshpass -p "$TREX_PW" scp -o StrictHostKeyChecking=no \
    "$CLIENT_SCRIPT" "$QOS_CLASSES" "$QINQ_TAGS" "root@${host}:/root/"
}

extra_qos_args() {
  local args=""
  if [[ -n "$CLASSES" ]]; then
    # shellcheck disable=SC2086
    args="$args --classes $CLASSES"
  fi
  if [[ -n "$TAGS" ]]; then
    # shellcheck disable=SC2086
    args="$args --tags $TAGS"
  fi
  if [[ "$EQUAL_SHARE" == "1" ]]; then
    args="$args --equal-share"
  fi
  echo "$args"
}

run_test() {
  local test_rc=0
  local qos_extra
  qos_extra="$(extra_qos_args)"
  trap 'kill_trex_host "$BSU"; kill_trex_host "$SU"' EXIT

  deploy_scripts "$BSU"
  start_trex_host "$BSU"
  if [[ "$SU_COUNT" -gt 3 ]]; then
    start_trex_host "$SU"
  fi
  sleep 5

  echo "[qos-trex] run: su=${SU_COUNT} dl=${DL_BW} ul=${UL_BW} size=${PKT_SIZE} duration=${DURATION}s ${qos_extra}"
  # shellcheck disable=SC2086
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${BSU}" \
    "export PYTHONPATH=${PYPATH}; export TREX_PORTS=${BSU_PORTS}; cd /root && \
     python3 qos_throughput_script.py --debug --server-bsu 127.0.0.1 \
     $([[ "$SU_COUNT" -gt 3 ]] && echo "--server-su ${SU}") \
     --su ${SU_COUNT} --dl-bw ${DL_BW} --ul-bw ${UL_BW} --size ${PKT_SIZE} --duration ${DURATION} \
     --dir bidi --proto udp --ports ${BSU_PORTS} ${qos_extra}" || test_rc=$?

  kill_trex_host "$BSU"
  kill_trex_host "$SU"
  trap - EXIT
  return "$test_rc"
}

if [[ "$(hostname -s 2>/dev/null || hostname)" == "qa-lab-02" ]]; then
  run_test
else
  sshpass -p "$BENCH_PW" scp -o StrictHostKeyChecking=no \
    "$CLIENT_SCRIPT" "$QOS_CLASSES" "$QINQ_TAGS" "${BENCH_SSH}:/tmp/"
  sshpass -p "$BENCH_PW" ssh -o StrictHostKeyChecking=no "$BENCH_SSH" \
    "TREX_PW='${TREX_PW}' BSU='${BSU}' SU='${SU}' SU_COUNT='${SU_COUNT}' DURATION='${DURATION}' \
     DL_BW='${DL_BW}' UL_BW='${UL_BW}' BSU_PORTS='${BSU_PORTS}' PKT_SIZE='${PKT_SIZE}' \
     CLASSES='${CLASSES}' TAGS='${TAGS}' EQUAL_SHARE='${EQUAL_SHARE}' \
     TREX_DIR='${TREX_DIR}' PYPATH='${PYPATH}' bash -s" <<'REMOTE'
set -euo pipefail
kill_trex_host() {
  local host=$1
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${host}" \
    "pkill -f 't-rex-64 -i --no-scapy-server' 2>/dev/null || true; pkill -f '_t-rex-64' 2>/dev/null || true" || true
}
start_trex_host() {
  local host=$1
  kill_trex_host "$host"; sleep 2
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${host}" \
    "echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages; cd ${TREX_DIR}; \
     nohup ./t-rex-64 -i --no-scapy-server -c 1 --no-ofed-check > /tmp/trex_server.log 2>&1 & sleep 15; \
     pgrep -af '_t-rex-64' | head -1"
}
test_rc=0
trap 'kill_trex_host "$BSU"; kill_trex_host "$SU"' EXIT
sshpass -p "$TREX_PW" scp -o StrictHostKeyChecking=no \
  /tmp/qos_throughput_script.py /tmp/qos_classes.py /tmp/qinq_tags.py "root@${BSU}:/root/"
start_trex_host "$BSU"
if [[ "$SU_COUNT" -gt 3 ]]; then
  start_trex_host "$SU"
fi
sleep 5
extra=""
[[ -n "${CLASSES}" ]] && extra="$extra --classes $CLASSES"
[[ -n "${TAGS}" ]] && extra="$extra --tags $TAGS"
[[ "${EQUAL_SHARE}" == "1" ]] && extra="$extra --equal-share"
su_arg=""
[[ "$SU_COUNT" -gt 3 ]] && su_arg="--server-su ${SU}"
echo "[qos-trex] run: su=${SU_COUNT} dl=${DL_BW} ul=${UL_BW} duration=${DURATION}s"
# shellcheck disable=SC2086
sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${BSU}" \
  "export PYTHONPATH=${PYPATH}; export TREX_PORTS=${BSU_PORTS}; cd /root && \
   python3 qos_throughput_script.py --debug --server-bsu 127.0.0.1 ${su_arg} \
   --su ${SU_COUNT} --dl-bw ${DL_BW} --ul-bw ${UL_BW} --size ${PKT_SIZE} --duration ${DURATION} \
   --dir bidi --proto udp --ports ${BSU_PORTS} ${extra}" || test_rc=$?
kill_trex_host "$BSU"
kill_trex_host "$SU"
trap - EXIT
exit "$test_rc"
REMOTE
fi
