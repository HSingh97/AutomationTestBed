#!/usr/bin/env bash
# Trigger Jenkins: Automation Framework / CPU Utilization
#
# Examples:
#   ./jenkins/trigger-cpu-utilization.sh --stand test-qa-lab-01 \
#     --bts-ip 192.168.3.20 --cpe-ip 192.168.3.21 --duration 10
#
# Auth: same as jenkins/trigger-build.sh (JENKINS_USER + ~/.jenkins-api-token)

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/CPU%20Utilization"

TARGET_STAND="${TARGET_STAND:-test-qa-lab-01}"
BTS_IP="${BTS_IP:-192.168.3.20}"
CPE_IP="${CPE_IP:-192.168.3.21}"
DURATION="${DURATION:-10}"
SETTLE="${SETTLE:-30}"
POLL="${POLL:-5}"
NO_LINK_CLOSED="${NO_LINK_CLOSED:-true}"
LINK_CLOSED="${LINK_CLOSED:-true}"
NO_LINK_OPEN="${NO_LINK_OPEN:-true}"
LINK_OPEN="${LINK_OPEN:-true}"
THROUGHPUT="${THROUGHPUT:-false}"
WAIT="${WAIT:-false}"
POLL_SECONDS="${POLL_SECONDS:-20}"

usage() {
  cat <<'EOF'
Usage: trigger-cpu-utilization.sh [OPTIONS]

Options:
  --stand STAND            TARGET_STAND / agent label (default: test-qa-lab-01)
  --bts-ip IP              BTS management IP (default: 192.168.3.20)
  --cpe-ip IP              CPE management IP (default: 192.168.3.21)
  --duration MINUTES       Minutes to record each case (default: 10)
  --settle SECONDS         Seconds before the first sample (default: 30)
  --poll SECONDS           Seconds between samples (default: 5)
  --no-link-closed BOOL    Case 1 (default: true)
  --link-closed BOOL       Case 2 (default: true)
  --no-link-open BOOL      Case 3 (default: true)
  --link-open BOOL         Case 4 (default: true)
  --throughput BOOL        Case 5 (default: false)
  --wait                   Poll until the build completes
  -h, --help               Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stand) TARGET_STAND="$2"; shift 2 ;;
    --bts-ip) BTS_IP="$2"; shift 2 ;;
    --cpe-ip) CPE_IP="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --settle) SETTLE="$2"; shift 2 ;;
    --poll) POLL="$2"; shift 2 ;;
    --no-link-closed) NO_LINK_CLOSED="$2"; shift 2 ;;
    --link-closed) LINK_CLOSED="$2"; shift 2 ;;
    --no-link-open) NO_LINK_OPEN="$2"; shift 2 ;;
    --link-open) LINK_OPEN="$2"; shift 2 ;;
    --throughput) THROUGHPUT="$2"; shift 2 ;;
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

CRUMB_JSON="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" "${JENKINS_URL}/crumbIssuer/api/json")"
CRUMB="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumb',''))" <<<"$CRUMB_JSON")"
CRUMB_FIELD="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumbRequestField','Jenkins-Crumb'))" <<<"$CRUMB_JSON")"

POST_DATA=(
  --data-urlencode "TARGET_STAND=${TARGET_STAND}"
  --data-urlencode "BTS IP=${BTS_IP}"
  --data-urlencode "CPE IP=${CPE_IP}"
  --data-urlencode "Duration=${DURATION}"
  --data-urlencode "Settle=${SETTLE}"
  --data-urlencode "Poll=${POLL}"
  --data-urlencode "NO_LINK_CLOSED=${NO_LINK_CLOSED}"
  --data-urlencode "LINK_CLOSED=${LINK_CLOSED}"
  --data-urlencode "NO_LINK_OPEN=${NO_LINK_OPEN}"
  --data-urlencode "LINK_OPEN=${LINK_OPEN}"
  --data-urlencode "THROUGHPUT=${THROUGHPUT}"
)

echo "[jenkins] Triggering CPU Utilization on stand=${TARGET_STAND}"
HTTP="$(curl -s -D /tmp/trigger-cpu.hdr -o /tmp/trigger-cpu.out -w "%{http_code}" \
  -u "${JENKINS_USER}:${JENKINS_TOKEN}" -X POST \
  -H "${CRUMB_FIELD}: ${CRUMB}" \
  "${JENKINS_URL}/${JOB_PATH}/buildWithParameters" \
  "${POST_DATA[@]}")"

if [[ "$HTTP" != "201" && "$HTTP" != "200" && "$HTTP" != "302" ]]; then
  echo "Trigger failed (HTTP ${HTTP})" >&2
  head -20 /tmp/trigger-cpu.out >&2
  exit 1
fi

QUEUE_URL="$(grep -i '^Location:' /tmp/trigger-cpu.hdr | awk '{print $2}' | tr -d '\r' || true)"
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
