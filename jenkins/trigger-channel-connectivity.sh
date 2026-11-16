#!/usr/bin/env bash
# Trigger Jenkins: Automation Framework / Channel Connectivity
#
# Examples:
#   ./jenkins/trigger-channel-connectivity.sh --stand test-qa-lab-01 \
#     --bts-ip 192.168.3.20 --cpe-ip 192.168.3.21 --bandwidth HT20 --channels 36,40,44
#
# Auth: same as jenkins/trigger-build.sh (JENKINS_USER + ~/.jenkins-api-token)

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/Channel%20Connectivity"

TARGET_STAND="${TARGET_STAND:-test-qa-lab-01}"
BANDWIDTH="${BANDWIDTH:-all}"
CHANNELS="${CHANNELS:-all}"
BTS_IP="${BTS_IP:-}"
CPE_IP="${CPE_IP:-}"
WAIT="${WAIT:-false}"
POLL_SECONDS="${POLL_SECONDS:-20}"

usage() {
  cat <<'EOF'
Usage: trigger-channel-connectivity.sh [OPTIONS]

Options:
  --stand STAND       TARGET_STAND / agent label (default: test-qa-lab-01)
  --bts-ip IP         BTS management IP (default: stand profile)
  --cpe-ip IP         CPE management IP (default: stand profile)
  --bandwidth LIST    htmode list or all (default: all → HT20,HT40,HT80)
  --channels LIST     Channel numbers or all (default: all)
  --wait              Poll until build completes
  -h, --help          Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stand) TARGET_STAND="$2"; shift 2 ;;
    --bts-ip) BTS_IP="$2"; shift 2 ;;
    --cpe-ip) CPE_IP="$2"; shift 2 ;;
    --bandwidth) BANDWIDTH="$2"; shift 2 ;;
    --channels) CHANNELS="$2"; shift 2 ;;
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
  --data-urlencode "Bandwidth=${BANDWIDTH}"
  --data-urlencode "Channels=${CHANNELS}"
)

echo "[jenkins] Triggering Channel Connectivity on stand=${TARGET_STAND}"
HTTP="$(curl -s -D /tmp/trigger-channel.hdr -o /tmp/trigger-channel.out -w "%{http_code}" \
  -u "${JENKINS_USER}:${JENKINS_TOKEN}" -X POST \
  -H "${CRUMB_FIELD}: ${CRUMB}" \
  "${JENKINS_URL}/${JOB_PATH}/buildWithParameters" \
  "${POST_DATA[@]}")"

if [[ "$HTTP" != "201" && "$HTTP" != "200" && "$HTTP" != "302" ]]; then
  echo "Trigger failed (HTTP ${HTTP})" >&2
  head -20 /tmp/trigger-channel.out >&2
  exit 1
fi

QUEUE_URL="$(grep -i '^Location:' /tmp/trigger-channel.hdr | awk '{print $2}' | tr -d '\r' || true)"
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
