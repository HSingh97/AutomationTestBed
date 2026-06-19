#!/usr/bin/env bash
# Trigger Jenkins: Automation Framework / VLAN Link Debug Campaign
#
# Example:
#   TARGET_STAND=test-qa-lab-02 ./jenkins/trigger-vlan-debug-campaign.sh --wait
#   TARGET_STAND=test-qa-lab-02 ./jenkins/trigger-vlan-debug-campaign.sh --iterations 2

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/VLAN%20Link%20Debug%20Campaign"

TARGET_STAND="${TARGET_STAND:-test-qa-lab-02}"
BANDWIDTH="${BANDWIDTH:-HT20,HT40,HT80}"
MCS="${MCS:-MCS14}"
ITERATIONS="${ITERATIONS:-3}"
SU_LINK_WAIT="${SU_LINK_WAIT:-240}"
BW_APPLY_WAIT="${BW_APPLY_WAIT:-90}"
BW_RUNNING_WAIT="${BW_RUNNING_WAIT:-180}"
PROFILE="${PROFILE:-}"
WAIT="${WAIT:-false}"
POLL_SECONDS="${POLL_SECONDS:-30}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stand) TARGET_STAND="$2"; shift 2 ;;
    --bandwidth) BANDWIDTH="$2"; shift 2 ;;
    --mcs) MCS="$2"; shift 2 ;;
    --iterations) ITERATIONS="$2"; shift 2 ;;
    --su-link-wait) SU_LINK_WAIT="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --wait) WAIT=true; shift ;;
    -h|--help)
      echo "Usage: trigger-vlan-debug-campaign.sh [--wait] [--iterations N]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${JENKINS_TOKEN:-}" ]]; then
  JENKINS_TOKEN="$(<"${HOME}/.jenkins-api-token")"
fi

CRUMB_JSON="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" "${JENKINS_URL}/crumbIssuer/api/json")"
CRUMB="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumb',''))" <<<"$CRUMB_JSON")"
CRUMB_FIELD="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('crumbRequestField','Jenkins-Crumb'))" <<<"$CRUMB_JSON")"

POST_DATA=(
  --data-urlencode "TARGET_STAND=${TARGET_STAND}"
  --data-urlencode "Bandwidth=${BANDWIDTH}"
  --data-urlencode "MCS=${MCS}"
  --data-urlencode "Iterations=${ITERATIONS}"
  --data-urlencode "SU Link Wait (s)=${SU_LINK_WAIT}"
  --data-urlencode "BW Apply Wait (s)=${BW_APPLY_WAIT}"
  --data-urlencode "BW Running Wait (s)=${BW_RUNNING_WAIT}"
)
[[ -n "$PROFILE" ]] && POST_DATA+=(--data-urlencode "PROFILE=${PROFILE}")

echo "[jenkins] Triggering VLAN Link Debug Campaign on stand=${TARGET_STAND}"
HTTP="$(curl -s -o /tmp/trigger-vlan-debug.out -w "%{http_code}" \
  -u "${JENKINS_USER}:${JENKINS_TOKEN}" -X POST \
  -H "${CRUMB_FIELD}: ${CRUMB}" \
  "${JENKINS_URL}/${JOB_PATH}/buildWithParameters" \
  "${POST_DATA[@]}")"

if [[ "$HTTP" != "201" && "$HTTP" != "200" && "$HTTP" != "302" ]]; then
  echo "Trigger failed (HTTP ${HTTP})" >&2
  head -30 /tmp/trigger-vlan-debug.out >&2
  exit 1
fi

echo "[jenkins] Queued VLAN debug campaign"

if [[ "$WAIT" != "true" ]]; then
  exit 0
fi

sleep 5
BUILD_NUM="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" \
  "${JENKINS_URL}/${JOB_PATH}/lastBuild/api/json?tree=number,building" | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['number'])")"
URL="${JENKINS_URL}/${JOB_PATH}/${BUILD_NUM}/"
echo "[jenkins] Build #${BUILD_NUM}: ${URL}"

while true; do
  JSON="$(curl -fsS -u "${JENKINS_USER}:${JENKINS_TOKEN}" "${URL}api/json?tree=building,result")"
  BUILDING="$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('building'))" <<<"$JSON")"
  RESULT="$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result') or '')" <<<"$JSON")"
  if [[ "$BUILDING" == "False" ]]; then
    echo "[jenkins] Finished: ${RESULT}"
    exit 0
  fi
  sleep "$POLL_SECONDS"
done
