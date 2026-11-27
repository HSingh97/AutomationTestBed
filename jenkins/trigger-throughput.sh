#!/usr/bin/env bash
# Trigger Jenkins: Automation Framework / Throughput Test - Trex
#
# Examples:
#   ./jenkins/trigger-throughput.sh
#   ./jenkins/trigger-throughput.sh --mcs MCS23 --bandwidth HT80
#   ./jenkins/trigger-throughput.sh --wait
#
# Auth: same as jenkins/trigger-build.sh (JENKINS_USER + ~/.jenkins-api-token)

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/Throughput%20Test%20-%20Trex"

BANDWIDTH="${BANDWIDTH:-all}"
MCS="${MCS:-all}"
RATIO="${RATIO:-50:50}"
DURATION="${DURATION:-30}"
PACKET_SIZE="${PACKET_SIZE:-1500}"
SU_COUNT="${SU_COUNT:-1}"
TREX_SERVER="${TREX_SERVER:-192.168.1.2}"
WAIT="${WAIT:-false}"
POLL_SECONDS="${POLL_SECONDS:-20}"

usage() {
  cat <<'EOF'
Usage: trigger-throughput.sh [OPTIONS]

Options:
  --bandwidth LIST    HT20,HT40+,HT80,HT160 or all (default: HT20,HT40+,HT80)
  --mcs LIST          MCS list or all (default: all → MCS0..MCS23)
  --ratio RATIO       DL:UL ratio (default: 50:50)
  --time SECONDS      Per-iteration duration (default: 30)
  --packet-size N     Frame size bytes (default: 1500)
  --su-count N        Number of SUs (default: 1)
  --trex-server IP    TRex server (default: 192.168.1.2)
  --wait              Poll until build completes
  -h, --help          Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stand) echo "This job runs only on qa-lab-01" >&2; exit 1 ;;
    --bandwidth) BANDWIDTH="$2"; shift 2 ;;
    --mcs) MCS="$2"; shift 2 ;;
    --trex-server|--trex-ip) TREX_SERVER="$2"; shift 2 ;;
    --bts-ip|--cpe-ip) echo "Radio addresses come from the qa-lab-01 bench" >&2; exit 1 ;;
    --ratio) RATIO="$2"; shift 2 ;;
    --time) DURATION="$2"; shift 2 ;;
    --packet-size) PACKET_SIZE="$2"; shift 2 ;;
    --su-count) SU_COUNT="$2"; shift 2 ;;
    --wait) WAIT=true; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    *) echo "Unexpected argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${JENKINS_TOKEN:-}" ]]; then
  if [[ -f "${HOME}/.jenkins-api-token" ]]; then
    JENKINS_TOKEN="$(<"${HOME}/.jenkins-api-token")"
  else
    echo "Set JENKINS_TOKEN or create ~/.jenkins-api-token" >&2
    exit 1
  fi
fi

bw_key="$(printf '%s' "${BANDWIDTH}" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
if [[ -z "${bw_key}" || "${bw_key}" == "ALL" ]]; then
  bw_key="HT20,HT40+,HT80"
fi
BANDWIDTH=""
IFS=',' read -ra bw_items <<<"${bw_key}"
for item in "${bw_items[@]}"; do
  case "${item}" in
    HT20|HT80|HT160) norm="${item}" ;;
    HT40|HT40+) norm="HT40+" ;;
    *) echo "Unknown bandwidth: ${item}" >&2; exit 1 ;;
  esac
  BANDWIDTH="${BANDWIDTH:+${BANDWIDTH},}${norm}"
done

mcs_key="$(printf '%s' "${MCS}" | tr -d ' ' )"
if [[ -z "${mcs_key}" || "${mcs_key,,}" == "all" ]]; then
  MCS=""
  for n in $(seq 0 23); do
    MCS="${MCS:+${MCS},}MCS${n}"
  done
else
  MCS=""
  IFS=',' read -ra mcs_items <<<"${mcs_key}"
  for item in "${mcs_items[@]}"; do
    item="$(printf '%s' "${item}" | tr '[:lower:]' '[:upper:]')"
    if [[ ! "${item}" =~ ^MCS([0-9]|1[0-9]|2[0-3])$ ]]; then
      echo "Unknown MCS: ${item}" >&2
      exit 1
    fi
    MCS="${MCS:+${MCS},}${item}"
  done
fi

CRUMB_JSON="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" "${JENKINS_URL}/crumbIssuer/api/json")"
CRUMB="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumb',''))" <<<"$CRUMB_JSON")"
CRUMB_FIELD="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumbRequestField','Jenkins-Crumb'))" <<<"$CRUMB_JSON")"

POST_DATA=(
  --data-urlencode "BANDWIDTH=${BANDWIDTH}"
  --data-urlencode "MCS=${MCS}"
  --data-urlencode "DL:UL ratio=${RATIO}"
  --data-urlencode "Seconds each=${DURATION}"
  --data-urlencode "Packet size=${PACKET_SIZE}"
  --data-urlencode "SU count=${SU_COUNT}"
  --data-urlencode "TRex server IP=${TREX_SERVER}"
)

echo "[jenkins] Triggering Throughput Test - Trex on qa-lab-01"
HTTP="$(curl -s -D /tmp/trigger-throughput.hdr -o /tmp/trigger-throughput.out -w "%{http_code}" \
  -u "${JENKINS_USER}:${JENKINS_TOKEN}" -X POST \
  -H "${CRUMB_FIELD}: ${CRUMB}" \
  "${JENKINS_URL}/${JOB_PATH}/buildWithParameters" \
  "${POST_DATA[@]}")"

if [[ "$HTTP" != "201" && "$HTTP" != "200" && "$HTTP" != "302" ]]; then
  echo "Trigger failed (HTTP ${HTTP})" >&2
  head -20 /tmp/trigger-throughput.out >&2
  exit 1
fi

QUEUE_URL="$(grep -i '^Location:' /tmp/trigger-throughput.hdr | awk '{print $2}' | tr -d '\r' || true)"
echo "[jenkins] Queued: ${QUEUE_URL:-'(see Jenkins UI)'}"

if [[ "$WAIT" != "true" ]]; then
  exit 0
fi

echo "[jenkins] Waiting for build to start..."
BUILD_NUM=""
for _ in $(seq 1 60); do
  sleep 2
  BUILD_NUM="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" \
    "${JENKINS_URL}/${JOB_PATH}/lastBuild/api/json?tree=number,building" | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d['number'] if d.get('building') else '')" 2>/dev/null || true)"
  [[ -n "$BUILD_NUM" ]] && break
done

if [[ -z "$BUILD_NUM" ]]; then
  echo "[jenkins] Timed out waiting for build to start" >&2
  exit 1
fi

BUILD_API="${JENKINS_URL}/${JOB_PATH}/${BUILD_NUM}/"
echo "[jenkins] Build #${BUILD_NUM}: ${BUILD_API}"
while true; do
  BUILDING="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" \
    "${BUILD_API}api/json?tree=building,result" | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if d.get('building') else d.get('result',''))")"
  if [[ "$BUILDING" != "yes" ]]; then
    echo "[jenkins] Finished: ${BUILDING}"
    exit 0
  fi
  sleep "$POLL_SECONDS"
done
