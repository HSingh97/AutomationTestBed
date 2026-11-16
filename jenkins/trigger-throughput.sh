#!/usr/bin/env bash
# Trigger Jenkins: Automation Framework / Throughput Test - Trex
#
# Examples:
#   ./jenkins/trigger-throughput.sh
#   ./jenkins/trigger-throughput.sh --mcs MCS23 --bandwidth HT80
#   ./jenkins/trigger-throughput.sh --stand test-qa-lab-02 --wait
#
# Auth: same as jenkins/trigger-build.sh (JENKINS_USER + ~/.jenkins-api-token)

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/Throughput%20Test%20-%20Trex"

TARGET_STAND="${TARGET_STAND:-test-qa-lab-01}"
BANDWIDTH="${BANDWIDTH:-all}"
MCS="${MCS:-all}"
TREX_IP="${TREX_IP:-}"
BTS_IP="${BTS_IP:-}"
CPE_IP="${CPE_IP:-}"
RATIO="${RATIO:-75:25}"
DURATION="${DURATION:-30}"
PACKET_SIZE="${PACKET_SIZE:-1500}"
SU_COUNT="${SU_COUNT:-6}"
WAIT="${WAIT:-false}"
POLL_SECONDS="${POLL_SECONDS:-20}"

usage() {
  cat <<'EOF'
Usage: trigger-throughput.sh [OPTIONS]

Options:
  --stand STAND       TARGET_STAND / agent label (default: test-qa-lab-01)
  --bandwidth LIST    Which boxes to check: HT20,HT40+,HT80,HT160 or all
                      (default: all except HT160)
  --mcs LIST          MCS list or all (default: all → MCS0..MCS23)
  --trex-ip IP        TRex server IP (default: stand profile)
  --bts-ip IP         BTS management IP (default: stand profile)
  --cpe-ip IP         CPE management IP (default: stand profile)
  --ratio RATIO       DL:UL ratio (default: 75:25)
  --time SECONDS      Per-iteration duration (default: 30)
  --packet-size N     Frame size bytes (default: 1500)
  --su-count N        Number of SUs (default: 6)
  --wait              Poll until build completes
  -h, --help          Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stand) TARGET_STAND="$2"; shift 2 ;;
    --bandwidth) BANDWIDTH="$2"; shift 2 ;;
    --mcs) MCS="$2"; shift 2 ;;
    --trex-ip) TREX_IP="$2"; shift 2 ;;
    --bts-ip) BTS_IP="$2"; shift 2 ;;
    --cpe-ip) CPE_IP="$2"; shift 2 ;;
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

HT20=false
HT40=false
HT80=false
HT160=false
bw_key="$(printf '%s' "${BANDWIDTH}" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
if [[ -z "${bw_key}" || "${bw_key}" == "ALL" ]]; then
  HT20=true
  HT40=true
  HT80=true
else
  IFS=',' read -ra bw_items <<<"${bw_key}"
  for item in "${bw_items[@]}"; do
    case "${item}" in
      HT20) HT20=true ;;
      HT40|HT40+) HT40=true ;;
      HT80) HT80=true ;;
      HT160) HT160=true ;;
      *) echo "Unknown bandwidth: ${item}" >&2; exit 1 ;;
    esac
  done
fi

CRUMB_JSON="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" "${JENKINS_URL}/crumbIssuer/api/json")"
CRUMB="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumb',''))" <<<"$CRUMB_JSON")"
CRUMB_FIELD="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumbRequestField','Jenkins-Crumb'))" <<<"$CRUMB_JSON")"

POST_DATA=(
  --data-urlencode "TARGET_STAND=${TARGET_STAND}"
  --data-urlencode "HT20=${HT20}"
  --data-urlencode "HT40+=${HT40}"
  --data-urlencode "HT80=${HT80}"
  --data-urlencode "HT160=${HT160}"
  --data-urlencode "MCS=${MCS}"
  --data-urlencode "TRex IP=${TREX_IP}"
  --data-urlencode "BTS IP=${BTS_IP}"
  --data-urlencode "CPE IP=${CPE_IP}"
  --data-urlencode "DL:UL Ratio=${RATIO}"
  --data-urlencode "Throughput Test Time=${DURATION}"
  --data-urlencode "Packet Size=${PACKET_SIZE}"
  --data-urlencode "No of SU=${SU_COUNT}"
)

echo "[jenkins] Triggering Throughput Test - Trex on stand=${TARGET_STAND}"
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
