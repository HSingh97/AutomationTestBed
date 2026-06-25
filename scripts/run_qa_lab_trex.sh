#!/usr/bin/env bash
# qa-lab-02 PTMP TRex: start both servers, run test, always kill servers on exit.
#
#   192.168.1.1 port 0 = BTS (100 Mbps link) | ports 1-3 = CPE (1 Gbps)
#   192.168.1.2 port 0 = CPE 4 (1 Gbps)
#
# Default 100/100: 100M DL from BTS port 0, 100M UL split across 4 CPE (25M each)

set -euo pipefail

BENCH_SSH="${BENCH_SSH:-root@10.0.150.102}"
BENCH_PW="${BENCH_PW:-senao1234#}"
TREX_PW="${TREX_PW:-ubuntu}"
BSU="${BSU:-192.168.1.1}"
SU="${SU:-192.168.1.2}"
TREX_DIR="${TREX_DIR:-/opt/v3.06}"
PYPATH="${PYPATH:-/opt/v3.06/automation/trex_control_plane/interactive/}"
SU_COUNT="${SU_COUNT:-4}"
DURATION="${DURATION:-30}"
DL_BW="${DL_BW:-100M}"
UL_BW="${UL_BW:-100M}"
BSU_PORTS="${BSU_PORTS:-0,1,2,3}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT_SCRIPT="${REPO_ROOT}/traffic/scripts/master_script_extended_16SU.py"

kill_trex_host() {
  local host=$1
  echo "[trex] stopping on ${host}"
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "root@${host}" \
    "pkill -f 't-rex-64 -i --no-scapy-server' 2>/dev/null || true; pkill -f '_t-rex-64' 2>/dev/null || true" \
    || true
}

start_trex_host() {
  local host=$1
  echo "[trex] starting on ${host}"
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

check_ports() {
  local host=$1 label=$2
  echo "[trex] ${label} (${host})"
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${host}" \
    "export PYTHONPATH=${PYPATH}; python3 -c \"
from trex.stl.api import STLClient
c=STLClient()
c.connect()
for p in c.get_port_info():
    print(f'  port {p[\\\"index\\\"]}: link={p[\\\"link\\\"]} speed={p.get(\\\"speed\\\",0)}G')
c.disconnect()
\""
}

run_test() {
  local test_rc=0
  trap 'kill_trex_host "$BSU"; kill_trex_host "$SU"' EXIT

  sshpass -p "$TREX_PW" scp -o StrictHostKeyChecking=no "$CLIENT_SCRIPT" "root@${BSU}:/root/master_script_extended_16SU.py"
  start_trex_host "$BSU"
  start_trex_host "$SU"
  sleep 5
  check_ports "$BSU" "BSU server"
  check_ports "$SU" "SU server"

  echo "[trex] run: su=${SU_COUNT} dl=${DL_BW} ul=${UL_BW} duration=${DURATION}s"
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${BSU}" \
    "export PYTHONPATH=${PYPATH}; export TREX_PORTS=${BSU_PORTS}; cd /root && \
     python3 master_script_extended_16SU.py --debug --server-bsu 127.0.0.1 --server-su ${SU} \
     --su ${SU_COUNT} --dl-bw ${DL_BW} --ul-bw ${UL_BW} --size 1500 --duration ${DURATION} \
     --dir bidi --proto udp --ports ${BSU_PORTS}" || test_rc=$?

  kill_trex_host "$BSU"
  kill_trex_host "$SU"
  trap - EXIT
  return "$test_rc"
}

if [[ "$(hostname -s 2>/dev/null || hostname)" == "qa-lab-02" ]]; then
  run_test
else
  sshpass -p "$BENCH_PW" scp -o StrictHostKeyChecking=no "$CLIENT_SCRIPT" "${BENCH_SSH}:/tmp/master_script_extended_16SU.py"
  sshpass -p "$BENCH_PW" ssh -o StrictHostKeyChecking=no "$BENCH_SSH" \
    "TREX_PW='${TREX_PW}' BSU='${BSU}' SU='${SU}' SU_COUNT='${SU_COUNT}' DURATION='${DURATION}' \
     DL_BW='${DL_BW}' UL_BW='${UL_BW}' BSU_PORTS='${BSU_PORTS}' TREX_DIR='${TREX_DIR}' PYPATH='${PYPATH}' \
     CLIENT_SCRIPT='/tmp/master_script_extended_16SU.py' bash -s" <<'REMOTE'
set -euo pipefail
kill_trex_host() {
  local host=$1
  echo "[trex] stopping on ${host}"
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
check_ports() {
  local host=$1 label=$2
  echo "[trex] ${label} (${host})"
  sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${host}" \
    "export PYTHONPATH=${PYPATH}; python3 -c \"
from trex.stl.api import STLClient
c=STLClient(); c.connect()
for p in c.get_port_info(): print(f'  port {p[\\\"index\\\"]}: link={p[\\\"link\\\"]} speed={p.get(\\\"speed\\\",0)}G')
c.disconnect()
\""
}
test_rc=0
trap 'kill_trex_host "$BSU"; kill_trex_host "$SU"' EXIT
sshpass -p "$TREX_PW" scp -o StrictHostKeyChecking=no "$CLIENT_SCRIPT" "root@${BSU}:/root/master_script_extended_16SU.py"
start_trex_host "$BSU"
start_trex_host "$SU"
sleep 5
check_ports "$BSU" "BSU server"
check_ports "$SU" "SU server"
echo "[trex] run: su=${SU_COUNT} dl=${DL_BW} ul=${UL_BW} duration=${DURATION}s"
sshpass -p "$TREX_PW" ssh -o StrictHostKeyChecking=no "root@${BSU}" \
  "export PYTHONPATH=${PYPATH}; export TREX_PORTS=${BSU_PORTS}; cd /root && \
   python3 master_script_extended_16SU.py --debug --server-bsu 127.0.0.1 --server-su ${SU} \
   --su ${SU_COUNT} --dl-bw ${DL_BW} --ul-bw ${UL_BW} --size 1500 --duration ${DURATION} \
   --dir bidi --proto udp --ports ${BSU_PORTS}" || test_rc=$?
kill_trex_host "$BSU"
kill_trex_host "$SU"
trap - EXIT
exit "$test_rc"
REMOTE
fi
