#!/usr/bin/env bash
# Trigger Jenkins: Automation Framework / Throughput Test - Trex
#
# Examples:
#   TARGET_STAND=test-qa-lab-02 ./jenkins/trigger-throughput.sh
#   TARGET_STAND=test-qa-lab-02 ./jenkins/trigger-throughput.sh --mcs MCS22 --wait
#   TARGET_STAND=test-harman2 ./jenkins/trigger-throughput.sh --bandwidth HT80 --mcs MCS23
#
# Auth: same as jenkins/trigger-build.sh (JENKINS_USER + ~/.jenkins-api-token)

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/Throughput%20Test%20-%20Trex"

TARGET_STAND="${TARGET_STAND:-test-qa-lab-02}"
BANDWIDTH="${BANDWIDTH:-HT80}"
MCS="${MCS:-MCS22}"
RATIO="${RATIO:-75:25}"
DURATION="${DURATION:-30}"
PACKET_SIZE="${PACKET_SIZE:-1500}"
PROFILE="${PROFILE:-}"
SU_COUNT="${SU_COUNT:-}"
TREX_SERVER="${TREX_SERVER:-}"
TREX_SU_SERVER="${TREX_SU_SERVER:-}"
BTS_IP="${BTS_IP:-}"
CPE_IP="${CPE_IP:-}"
WAIT="${WAIT:-false}"
POLL_SECONDS="${POLL_SECONDS:-20}"

usage() {
  cat <<'EOF'
Usage: trigger-throughput.sh [OPTIONS]

Options:
  --stand STAND       TARGET_STAND / agent label (default: qa-lab-02)
  --bandwidth LIST    Comma-separated htmode (default: HT80)
  --mcs LIST          Comma-separated MCS (default: MCS22)
  --ratio RATIO       DL:UL ratio (default: 75:25)
  --time SECONDS      Per-iteration duration (default: 30)
  --packet-size N     Frame size bytes (default: 1500)
  --profile NAME      Override profile (default: from benches.yaml)
  --su-count N        TRex SU count override
  --trex-server IP    BSU TRex host override
  --trex-su-server IP SU TRex host override
  --bts-ip IP         BTS IPv6 override
  --cpe-ip IP         CPE IPv6 override
  --wait              Poll until build completes
  -h, --help          Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stand) TARGET_STAND="$2"; shift 2 ;;
    --bandwidth) BANDWIDTH="$2"; shift 2 ;;
    --mcs) MCS="$2"; shift 2 ;;
    --ratio) RATIO="$2"; shift 2 ;;
    --time) DURATION="$2"; shift 2 ;;
    --packet-size) PACKET_SIZE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --su-count) SU_COUNT="$2"; shift 2 ;;
    --trex-server) TREX_SERVER="$2"; shift 2 ;;
    --trex-su-server) TREX_SU_SERVER="$2"; shift 2 ;;
    --bts-ip) BTS_IP="$2"; shift 2 ;;
    --cpe-ip) CPE_IP="$2"; shift 2 ;;
    --wait) WAIT=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
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

CRUMB_JSON="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" "${JENKINS_URL}/crumbIssuer/api/json")"
CRUMB="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumb',''))" <<<"$CRUMB_JSON")"
CRUMB_FIELD="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumbRequestField','Jenkins-Crumb'))" <<<"$CRUMB_JSON")"

POST_DATA=(
  --data-urlencode "TARGET_STAND=${TARGET_STAND}"
  --data-urlencode "Bandwidth=${BANDWIDTH}"
  --data-urlencode "MCS=${MCS}"
  --data-urlencode "DL:UL Ratio=${RATIO}"
  --data-urlencode "Throughput Test Time=${DURATION}"
  --data-urlencode "Packet Size=${PACKET_SIZE}"
)
[[ -n "$PROFILE" ]] && POST_DATA+=(--data-urlencode "PROFILE=${PROFILE}")
[[ -n "$SU_COUNT" ]] && POST_DATA+=(--data-urlencode "SU Count=${SU_COUNT}")
[[ -n "$TREX_SERVER" ]] && POST_DATA+=(--data-urlencode "TRex Server=${TREX_SERVER}")
[[ -n "$TREX_SU_SERVER" ]] && POST_DATA+=(--data-urlencode "TRex SU Server=${TREX_SU_SERVER}")
[[ -n "$BTS_IP" ]] && POST_DATA+=(--data-urlencode "BTS IP=${BTS_IP}")
[[ -n "$CPE_IP" ]] && POST_DATA+=(--data-urlencode "CPE IP=${CPE_IP}")

echo "[jenkins] Triggering Throughput Test - Trex on stand=${TARGET_STAND}"
HTTP="$(curl -s -o /tmp/trigger-throughput.out -w "%{http_code}" \
  -u "${JENKINS_USER}:${JENKINS_TOKEN}" -X POST \
  -H "${CRUMB_FIELD}: ${CRUMB}" \
  "${JENKINS_URL}/${JOB_PATH}/buildWithParameters" \
  "${POST_DATA[@]}")"

if [[ "$HTTP" != "201" && "$HTTP" != "200" && "$HTTP" != "302" ]]; then
  echo "Trigger failed (HTTP ${HTTP})" >&2
  head -20 /tmp/trigger-throughput.out >&2
  exit 1
fi

QUEUE_URL="$(grep -i '^Location:' /tmp/trigger-throughput.out | awk '{print $2}' | tr -d '\r' || true)"
echo "[jenkins] Queued: ${QUEUE_URL:-'(see Jenkins UI)'}"

if [[ "$WAIT" != "true" ]]; then
  exit 0
fi

echo "[jenkins] Waiting for build to start..."
BUILD_URL=""
for _ in $(seq 1 60); do
  sleep 2
  BUILD_URL="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" \
    "${JENKINS_URL}/${JOB_PATH}/lastBuild/api/json?tree=url,building" | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d['url'] if d.get('building') else '')")"
  [[ -n "$BUILD_URL" ]] && break
done

if [[ -z "$BUILD_URL" ]]; then
  echo "[jenkins] Timed out waiting for build to start" >&2
  exit 1
fi

echo "[jenkins] Build: ${BUILD_URL}"
while true; do
  BUILDING="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" \
    "${BUILD_URL}api/json?tree=building,result" | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if d.get('building') else d.get('result',''))")"
  if [[ "$BUILDING" != "yes" ]]; then
    echo "[jenkins] Finished: ${BUILDING}"
    exit 0
  fi
  sleep "$POLL_SECONDS"
done
