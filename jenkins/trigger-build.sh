#!/usr/bin/env bash
# Trigger Jenkins job: Automation Framework / Automation Test Cases
#
# Examples:
#   ./jenkins/trigger-build.sh JMB_04
#   ./jenkins/trigger-build.sh --filter JMB_04 --markers JumboFrames
#   ./jenkins/trigger-build.sh --filter IP_18 --markers IP
#   ./jenkins/trigger-build.sh --markers ProcessMonitor --filter ProcessMonitor
#   ./jenkins/trigger-build.sh --markers ProcessMonitor --skip-bootstrap
#
# Auth (pick one):
#   export JENKINS_USER=harman JENKINS_TOKEN=<api-token>
#   echo '<api-token>' > ~/.jenkins-api-token && chmod 600 ~/.jenkins-api-token
#   export JENKINS_PASSWORD='<jenkins-password>'   # basic auth fallback
#
# Jenkins UI: User → Security → API Token → Generate new token

set -euo pipefail

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:8081}"
JENKINS_USER="${JENKINS_USER:-harman}"
JOB_PATH="job/Automation%20Framework/job/Automation%20Test%20Cases"

TARGET_STAND="${TARGET_STAND:-test-harman2}"
TEST_MARKERS="${TEST_MARKERS:-JumboFrames}"
TEST_FILTER="${TEST_FILTER:-}"
RECOVERY_PROFILE_NAME="${RECOVERY_PROFILE_NAME:-link_formation}"
LOCAL_IPV6="${LOCAL_IPV6:-}"
REMOTE_IPV6="${REMOTE_IPV6:-}"
FALLBACK_IP="${FALLBACK_IP:-10.0.0.1}"
SKIP_TESTBED_BOOTSTRAP="${SKIP_TESTBED_BOOTSTRAP:-false}"
RUN_IP_CPE="${RUN_IP_CPE:-false}"
WAIT="${WAIT:-false}"
WAIT_FOR_IDLE="${WAIT_FOR_IDLE:-true}"
POLL_SECONDS="${POLL_SECONDS:-15}"
LOG_TAIL_LINES="${LOG_TAIL_LINES:-80}"

usage() {
  cat <<'EOF'
Usage: trigger-build.sh [OPTIONS] [TEST_FILTER]

Run a single Jumbo case:
  ./jenkins/trigger-build.sh JMB_04

Options:
  --filter FILTER          pytest -k filter (comma = OR)
  --markers MARKERS        TEST_MARKERS (default: JumboFrames)
  --stand STAND            TARGET_STAND (default: test-harman2)
  --recovery-profile NAME  RECOVERY_PROFILE_NAME (default: link_formation)
  --skip-bootstrap         SKIP_TESTBED_BOOTSTRAP=true
  --run-ip-cpe             RUN_IP_CPE=true
  --procmon-serial URI     PROCMON_SERIAL_DEVICE (e.g. socket://127.0.0.1:7000)
  --procmon-clean-reboot   PROCMON_CLEAN_REBOOT_BEFORE_KILL=true
  --url URL                JENKINS_URL (default: http://127.0.0.1:8081)
  --user USER              JENKINS_USER (default: harman)
  --wait                   Poll until the triggered build finishes (and show log tail)
  --no-wait-idle           Queue immediately even if this job is already running/queued
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --filter) TEST_FILTER="$2"; shift 2 ;;
    --markers) TEST_MARKERS="$2"; shift 2 ;;
    --stand) TARGET_STAND="$2"; shift 2 ;;
    --recovery-profile) RECOVERY_PROFILE_NAME="$2"; shift 2 ;;
    --skip-bootstrap) SKIP_TESTBED_BOOTSTRAP=true; shift ;;
    --run-ip-cpe) RUN_IP_CPE=true; shift ;;
    --procmon-serial) PROCMON_SERIAL_DEVICE="$2"; shift 2 ;;
    --procmon-clean-reboot) PROCMON_CLEAN_REBOOT_BEFORE_KILL=true; shift ;;
    --url) JENKINS_URL="$2"; shift 2 ;;
    --user) JENKINS_USER="$2"; shift 2 ;;
    --wait) WAIT=true; shift ;;
    --no-wait-idle) WAIT_FOR_IDLE=false; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    *)
      if [[ -z "${TEST_FILTER}" ]]; then
        TEST_FILTER="$1"
      else
        echo "Unexpected argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

is_process_monitor_markers() {
  echo ",${TEST_MARKERS}," | grep -qi ',ProcessMonitor,'
}

if [[ -z "${TEST_FILTER}" ]]; then
  if is_process_monitor_markers; then
    TEST_FILTER=""
  else
    echo "TEST_FILTER is required (e.g. JMB_04 or --filter JMB_04). For ProcessMonitor use --markers ProcessMonitor." >&2
    usage >&2
    exit 1
  fi
fi

# Infer marker from filter when caller did not override TEST_MARKERS env.
if [[ "${TEST_MARKERS}" == "JumboFrames" && "${TEST_FILTER}" =~ ^GUI ]]; then
  TEST_MARKERS="GUI"
elif [[ "${TEST_MARKERS}" == "JumboFrames" && "${TEST_FILTER}" =~ ^IP_ ]]; then
  TEST_MARKERS="IP"
elif [[ "${TEST_MARKERS}" == "JumboFrames" && "${TEST_FILTER}" =~ ^PROCESS ]]; then
  TEST_MARKERS="ProcessMonitor"
fi

resolve_auth() {
  if [[ -n "${JENKINS_TOKEN:-}" ]]; then
    printf '%s:%s' "${JENKINS_USER}" "${JENKINS_TOKEN}"
    return
  fi
  if [[ -f "${HOME}/.jenkins-api-token" ]]; then
    printf '%s:%s' "${JENKINS_USER}" "$(tr -d '[:space:]' < "${HOME}/.jenkins-api-token")"
    return
  fi
  if [[ -n "${JENKINS_PASSWORD:-}" ]]; then
    printf '%s:%s' "${JENKINS_USER}" "${JENKINS_PASSWORD}"
    return
  fi
  if [[ -t 0 ]]; then
    read -rsp "Jenkins password for ${JENKINS_USER}: " JENKINS_PASSWORD
    echo
    printf '%s:%s' "${JENKINS_USER}" "${JENKINS_PASSWORD}"
    return
  fi
  echo "Set JENKINS_TOKEN, write ~/.jenkins-api-token, or export JENKINS_PASSWORD." >&2
  echo "Create token: ${JENKINS_URL}/user/${JENKINS_USER}/configure → API Token" >&2
  exit 1
}

AUTH="$(resolve_auth)"
BASE_URL="${JENKINS_URL%/}"
JOB_API="${BASE_URL}/${JOB_PATH}"

jenkins_api() {
  local url="$1"
  shift
  curl -fsS -u "${AUTH}" "$@" "${url}"
}

read_job_build_numbers() {
  jenkins_api "${JOB_API}/api/json" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); b=d.get("lastBuild") or {}; print(b.get("number") or 0); print(d.get("nextBuildNumber") or 0)' 2>/dev/null \
    || echo -e "0\n0"
}

read_last_build_number() {
  read_job_build_numbers | head -1
}

read_next_build_number() {
  read_job_build_numbers | tail -1
}

job_is_busy() {
  local building queued
  building="$(jenkins_api "${JOB_API}/api/json" 2>/dev/null \
    | python3 -c 'import json,sys; b=(json.load(sys.stdin).get("lastBuild") or {}); print(bool(b.get("building")))' 2>/dev/null || echo False)"
  queued="$(jenkins_api "${BASE_URL}/queue/api/json" 2>/dev/null \
    | python3 -c 'import json,sys; u=sys.argv[1]; d=json.load(sys.stdin); print(any(u in (i.get("task",{}).get("url") or "") for i in d.get("items",[])))' "${JOB_API}/" 2>/dev/null || echo False)"
  [[ "${building}" == "True" || "${queued}" == "True" ]]
}

wait_for_build_number() {
  local expected="$1"
  local deadline=$(( $(date +%s) + 120 ))
  local current=""
  while [[ $(date +%s) -lt ${deadline} ]]; do
    current="$(read_last_build_number)"
    if [[ -n "${current}" && "${expected}" -gt 0 && "${current}" -ge "${expected}" ]]; then
      echo "${current}"
      return 0
    fi
    sleep 2
  done
  echo ""
  return 1
}

wait_for_job_idle() {
  local active
  active="$(jenkins_api "${JOB_API}/api/json" 2>/dev/null \
    | python3 -c 'import json,sys; b=(json.load(sys.stdin).get("lastBuild") or {}); print("{} building={}".format(b.get("number","?"), b.get("building", False)))' 2>/dev/null || echo "unknown")"
  echo "[jenkins] Waiting for idle job (current: ${active})..."
  while job_is_busy; do
    sleep "${POLL_SECONDS}"
  done
  echo "[jenkins] Job is idle — safe to queue next build."
}

show_build_log_tail() {
  local build_no="$1"
  echo "[jenkins] --- console tail (last ${LOG_TAIL_LINES} lines) #${build_no} ---"
  jenkins_api "${JOB_API}/${build_no}/consoleText" 2>/dev/null | tail -n "${LOG_TAIL_LINES}" || true
  echo "[jenkins] --- end console tail ---"
}

echo "[jenkins] URL:     ${BASE_URL}"
echo "[jenkins] Job:     Automation Framework / Automation Test Cases"
echo "[jenkins] Stand:   ${TARGET_STAND}"
echo "[jenkins] Markers: ${TEST_MARKERS}"
echo "[jenkins] Filter:  ${TEST_FILTER}"

if [[ "${WAIT_FOR_IDLE}" == "true" ]] && job_is_busy; then
  wait_for_job_idle
fi

EXPECTED_BUILD_NUMBER="$(read_next_build_number)"

CRUMB_JSON="$(curl -fsS -u "${AUTH}" "${BASE_URL}/crumbIssuer/api/json" 2>/dev/null || true)"
CRUMB_FIELD=""
CRUMB_VALUE=""
if [[ -n "${CRUMB_JSON}" ]]; then
  CRUMB_FIELD="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("crumbRequestField",""))' <<< "${CRUMB_JSON}" 2>/dev/null || true)"
  CRUMB_VALUE="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("crumb",""))' <<< "${CRUMB_JSON}" 2>/dev/null || true)"
fi

PARAMS=(
  "TEST_MARKERS=${TEST_MARKERS}"
  "TEST_FILTER=${TEST_FILTER}"
  "RECOVERY_PROFILE_NAME=${RECOVERY_PROFILE_NAME}"
  "Local%20IPv6%20Address=${LOCAL_IPV6}"
  "Remote%20IPv6%20Address=${REMOTE_IPV6}"
  "FALLBACK_IP=${FALLBACK_IP}"
  "SKIP_TESTBED_BOOTSTRAP=${SKIP_TESTBED_BOOTSTRAP}"
  "RUN_IP_CPE=${RUN_IP_CPE}"
  "TARGET_STAND=${TARGET_STAND}"
  "PROCMON_SERIAL_DEVICE=${PROCMON_SERIAL_DEVICE:-}"
  "PROCMON_CLEAN_REBOOT_BEFORE_KILL=${PROCMON_CLEAN_REBOOT_BEFORE_KILL:-false}"
)

# Form-encoded POST with per-param urlencoding (filters may contain spaces/commas).
CURL_ARGS=(-sS -u "${AUTH}" -X POST -D /tmp/jenkins-trigger.headers -o /tmp/jenkins-trigger.out -w '%{http_code}')
for param in "${PARAMS[@]}"; do
  CURL_ARGS+=(--data-urlencode "${param}")
done
CURL_ARGS+=("${BASE_URL}/${JOB_PATH}/buildWithParameters")
if [[ -n "${CRUMB_FIELD}" && -n "${CRUMB_VALUE}" ]]; then
  CURL_ARGS+=(-H "${CRUMB_FIELD}: ${CRUMB_VALUE}")
fi

HTTP_CODE="$(curl "${CURL_ARGS[@]}")" || {
  echo "Jenkins trigger failed." >&2
  head -20 /tmp/jenkins-trigger.out >&2 || true
  exit 1
}

if [[ "${HTTP_CODE}" != "201" && "${HTTP_CODE}" != "200" && "${HTTP_CODE}" != "302" ]]; then
  echo "Unexpected HTTP ${HTTP_CODE} from Jenkins." >&2
  head -20 /tmp/jenkins-trigger.out >&2 || true
  exit 1
fi

QUEUE_URL="$(grep -Fi '^Location:' /tmp/jenkins-trigger.headers 2>/dev/null | tail -1 | tr -d '\r' | awk '{print $2}' || true)"

echo "[jenkins] Waiting for build #${EXPECTED_BUILD_NUMBER}..."
BUILD_NUMBER="$(wait_for_build_number "${EXPECTED_BUILD_NUMBER}")" || true
if [[ -z "${BUILD_NUMBER}" ]]; then
  echo "[jenkins] ERROR: POST returned ${HTTP_CODE} but build #${EXPECTED_BUILD_NUMBER} did not appear within 120s." >&2
  [[ -n "${QUEUE_URL}" ]] && echo "[jenkins] Queue item: ${QUEUE_URL}" >&2
  exit 1
fi

BUILD_URL="$(jenkins_api "${JOB_API}/${BUILD_NUMBER}/api/json" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' 2>/dev/null || true)"
echo "[jenkins] Build #${BUILD_NUMBER} queued"
echo "[jenkins] ${BUILD_URL:-${BASE_URL}/${JOB_PATH}/${BUILD_NUMBER}/}"

if [[ "${WAIT}" == "true" && -n "${BUILD_NUMBER}" ]]; then
  echo "[jenkins] Waiting for build #${BUILD_NUMBER}..."
  while true; do
    STATUS_JSON="$(curl -fsS -u "${AUTH}" "${BASE_URL}/${JOB_PATH}/${BUILD_NUMBER}/api/json?tree=building,result")"
    BUILDING="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("building", True))' <<< "${STATUS_JSON}")"
    RESULT="$(python3 -c 'import json,sys; r=json.load(sys.stdin).get("result"); print("" if r is None else r)' <<< "${STATUS_JSON}")"
    if [[ "${BUILDING}" == "False" ]]; then
      echo "[jenkins] Finished: ${RESULT:-UNKNOWN}"
      show_build_log_tail "${BUILD_NUMBER}"
      [[ "${RESULT}" == "SUCCESS" ]] && exit 0
      exit 1
    fi
    sleep "${POLL_SECONDS}"
  done
fi
