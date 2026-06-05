#!/usr/bin/env bash
# Run pytest exactly like jenkins/jenkins-AutomationFramework (no push required).
# Override any Jenkins parameter via environment variables before invoking.
#
# Examples:
#   ./jenkins/run-local.sh
#   TEST_MARKERS=IP ./jenkins/run-local.sh
#   TEST_MARKERS=IP TEST_FILTER=IP_01 or IP_02 ./jenkins/run-local.sh
#   TEST_MARKERS=GUI,IP TEST_FILTER='Summary, TopPanel' ./jenkins/run-local.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# --- Jenkins parameters (defaults match jenkins-AutomationFramework) ---
TEST_MARKERS="${TEST_MARKERS:-GUI,IP}"
# Empty TEST_FILTER= is intentional (run all tests in paths). Default only when unset.
if [[ -z "${TEST_FILTER+x}" ]]; then
  TEST_FILTER="Summary, TopPanel, WirelessProperties"
fi
RECOVERY_PROFILE_NAME="${RECOVERY_PROFILE_NAME:-link_formation}"
FALLBACK_IP="${FALLBACK_IP:-10.0.0.1}"
LOCAL_IPV6="${LOCAL_IPV6:-}"
REMOTE_IPV6="${REMOTE_IPV6:-}"
SKIP_TESTBED_BOOTSTRAP="${SKIP_TESTBED_BOOTSTRAP:-false}"
RUN_IP_CPE="${RUN_IP_CPE:-false}"
BUILD_ID="${BUILD_ID:-local}"

# Optional: limit IP cases for a quick smoke (empty = Jenkins default scope)
# e.g. IP_SMOKE_FILTER="IP_01 or IP_02 or IP_03 or IP_04 or IP_05"
IP_SMOKE_FILTER="${IP_SMOKE_FILTER:-}"

upper() { echo "$1" | tr '[:lower:]' '[:upper:]'; }

# Parse markers
MARKERS_RAW="$(upper "${TEST_MARKERS}")"
IFS=',' read -ra MARKER_ARR <<< "${MARKERS_RAW}"
MARKERS=()
for m in "${MARKER_ARR[@]}"; do
  m="$(echo "$m" | xargs)"
  [[ -n "$m" ]] && MARKERS+=("$m")
done
if [[ ${#MARKERS[@]} -eq 0 ]]; then
  echo "TEST_MARKERS is empty. Use GUI, IP, Regression, and/or JumboFrames." >&2
  exit 1
fi

is_ipv4_only_ip_token() {
  local t u n
  t="$(echo "$1" | xargs)"
  u="$(upper "$t")"
  if [[ "$u" =~ ^IP_?0*([0-9]+)$ ]]; then
    n="${BASH_REMATCH[1]}"
    [[ "$n" -ge 1 && "$n" -le 17 ]]
    return
  fi
  return 1
}

has_marker() {
  local want="$1"
  for m in "${MARKERS[@]}"; do
    [[ "$m" == "$want" ]] && return 0
  done
  return 1
}

# Profile resolution (mirrors resolveProfileName)
PROFILE_NAME="default"
if has_marker IP; then
  PROFILE_NAME="ipv6_quickrun"
  if [[ -n "${TEST_FILTER}" ]]; then
    IFS=',' read -ra KPARTS <<< "${TEST_FILTER}"
    all_ipv4_only=true
    has_k=false
    for k in "${KPARTS[@]}"; do
      k="$(echo "$k" | xargs)"
      [[ -z "$k" ]] && continue
      has_k=true
      if ! is_ipv4_only_ip_token "$k"; then
        all_ipv4_only=false
        break
      fi
    done
    if $has_k && $all_ipv4_only; then
      PROFILE_NAME="ipv4_quickrun"
    fi
  fi
fi

# Paths, -m, extra flags
TEST_PATHS=()
M_PARTS=()
EXTRA_FLAGS=()

if has_marker GUI; then
  TEST_PATHS+=("tests/GUI/")
fi
if [[ "${SKIP_TESTBED_BOOTSTRAP}" == "true" ]]; then
  EXTRA_FLAGS+=("--skip-testbed-bootstrap")
fi
if has_marker IP; then
  TEST_PATHS+=("tests/IP/")
  M_PARTS+=("IP")
  EXTRA_FLAGS+=("--allow-ip-suite" "--allow-ip-destructive" "--no-ip-stop-on-first-fail")
fi
if has_marker REGRESSION; then
  TEST_PATHS+=("tests/Regression/")
  M_PARTS+=("Regression")
  EXTRA_FLAGS+=("--allow-regression" "--regression-fresh")
fi
if has_marker JUMBOFRAMES; then
  TEST_PATHS+=("tests/JumboFrames/")
  M_PARTS+=("JumboFrames")
  EXTRA_FLAGS+=("--allow-destructive-jumbo")
fi

for m in "${MARKERS[@]}"; do
  case "$m" in
    GUI|IP|REGRESSION|JUMBOFRAMES) ;;
    *) echo "Unknown TEST_MARKERS entry: $m" >&2; exit 1 ;;
  esac
done

# -k filter (mirrors guiOnlyFilter + ipScope)
IFS=',' read -ra KPARTS <<< "${TEST_FILTER}"
K_PARTS=()
for k in "${KPARTS[@]}"; do
  k="$(echo "$k" | xargs)"
  [[ -n "$k" ]] && K_PARTS+=("$k")
done

RUN_IP_CPE_BOOL=false
if [[ "${RUN_IP_CPE}" == "true" ]]; then
  RUN_IP_CPE_BOOL=true
fi
for k in "${K_PARTS[@]}"; do
  if [[ "$(echo "$k" | tr '[:upper:]' '[:lower:]')" == "cpe" ]]; then
    RUN_IP_CPE_BOOL=true
  fi
done

if $RUN_IP_CPE_BOOL; then
  IP_SCOPE="IP_"
else
  IP_SCOPE="(IP_ and bts)"
fi

GUI_ONLY_FILTER=false
if has_marker GUI && has_marker IP && [[ ${#K_PARTS[@]} -gt 0 ]]; then
  gui_only=true
  for k in "${K_PARTS[@]}"; do
    if [[ "$(upper "$k")" =~ ^IP_ ]]; then
      gui_only=false
      break
    fi
  done
  $gui_only && GUI_ONLY_FILTER=true
fi

join_or() {
  local out="" item
  for item in "$@"; do
    if [[ -z "${out}" ]]; then
      out="${item}"
    else
      out="${out} or ${item}"
    fi
  done
  echo "${out}"
}

K_EXPR=""
if [[ ${#K_PARTS[@]} -gt 0 ]]; then
  K_EXPR="$(join_or "${K_PARTS[@]}")"
  if $GUI_ONLY_FILTER; then
    K_EXPR="(${K_EXPR}) or ${IP_SCOPE}"
  fi
elif has_marker IP && ! $RUN_IP_CPE_BOOL; then
  K_EXPR="${IP_SCOPE}"
fi

if [[ -n "${IP_SMOKE_FILTER}" ]]; then
  if [[ -n "${K_EXPR}" ]]; then
    K_EXPR="(${K_EXPR}) and (${IP_SMOKE_FILTER})"
  else
    K_EXPR="${IP_SMOKE_FILTER}"
  fi
  # Smoke runs are IP-only unless caller also set GUI tokens in TEST_FILTER.
  if has_marker IP && ! has_marker GUI; then
    K_EXPR="${IP_SMOKE_FILTER}"
  fi
fi

TEST_M_EXPR=""
if [[ ${#MARKERS[@]} -eq 1 && ${#M_PARTS[@]} -eq 1 ]]; then
  TEST_M_EXPR="-m ${M_PARTS[0]}"
fi

# venv (same as Jenkins Install Dependencies stage)
PYTHON="venv/bin/python"
if [[ ! -x "${PYTHON}" ]] || ! "${PYTHON}" -c "import pytest" 2>/dev/null; then
  echo "[run-local] Setting up venv (pip install -r requirements.txt)..."
  python3 -m venv venv
  venv/bin/pip install --upgrade pip
  venv/bin/pip install -r requirements.txt
  export PLAYWRIGHT_BROWSERS_PATH="${ROOT}/.playwright-browsers"
  venv/bin/playwright install chromium
fi

export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${ROOT}/.playwright-browsers}"

mkdir -p reports/artifacts
rm -f reports/artifacts/report.json reports/artifacts/testbed_summary.json \
      reports/artifacts/Senao_UBR_*.html reports/artifacts/Senao_UBR_*.csv \
      reports/artifacts/Senao_GUI_*.html reports/artifacts/Senao_GUI_*.csv \
      Senao_UBR_*.html Senao_GUI_*.html 2>/dev/null || true

IPV6_LOCAL_ARGS=()
[[ -n "${LOCAL_IPV6}" ]] && IPV6_LOCAL_ARGS=(--local-ipv6 "${LOCAL_IPV6}")
IPV6_REMOTE_ARGS=()
[[ -n "${REMOTE_IPV6}" ]] && IPV6_REMOTE_ARGS=(--remote-ipv6 "${REMOTE_IPV6}")

echo "----------------------------------------"
echo "UBR Validation (local — same as Jenkins)"
echo "Markers:        ${MARKERS[*]}"
echo "Test paths:     ${TEST_PATHS[*]}"
echo "pytest -m:      ${TEST_M_EXPR:-(paths only)}"
echo "pytest -k:      ${K_EXPR:-(none)}"
if $GUI_ONLY_FILTER; then
  echo "IP scope:       $(${RUN_IP_CPE_BOOL} && echo 'BTS + CPE' || echo 'BTS only')"
  echo "GUI filter:     applies to tests/GUI/ only; IP via ${IP_SCOPE}"
elif has_marker IP && ! $RUN_IP_CPE_BOOL; then
  echo "IP scope:       BTS only"
fi
echo "Extra flags:    ${EXTRA_FLAGS[*]:-(none)}"
echo "Profile:        ${PROFILE_NAME}"
echo "Recovery:       ${RECOVERY_PROFILE_NAME}"
echo "Fallback IP:    ${FALLBACK_IP}"
echo "Git revision:   $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "----------------------------------------"

export PYTHONUNBUFFERED=1
export PYTHONPATH=.

PYTEST_ARGS=(
  -u -m pytest "${TEST_PATHS[@]}" -v
)
[[ -n "${TEST_M_EXPR}" ]] && PYTEST_ARGS+=(${TEST_M_EXPR})
[[ -n "${K_EXPR}" ]] && PYTEST_ARGS+=(-k "${K_EXPR}")
PYTEST_ARGS+=(
  --profile "${PROFILE_NAME}"
  --recovery-profile "${RECOVERY_PROFILE_NAME}"
  --fallback-ip "${FALLBACK_IP}"
  "${IPV6_LOCAL_ARGS[@]}"
  "${IPV6_REMOTE_ARGS[@]}"
  "${EXTRA_FLAGS[@]}"
  --json-report --json-report-file=reports/artifacts/report.json
)

echo "[run-local] stdbuf -oL -eL ${PYTHON} ${PYTEST_ARGS[*]}"
echo "----------------------------------------"

set +e
stdbuf -oL -eL "${PYTHON}" "${PYTEST_ARGS[@]}"
PYTEST_RC=$?
set -e

if [[ -f reports/artifacts/report.json ]]; then
  echo "[run-local] Generating customer report..."
  PYTHONPATH=. "${PYTHON}" utils/report_generator.py \
    "${BUILD_ID}" "${LOCAL_IPV6:-profile-default}" "$(date +%Y%m%d)" \
    --profile "${PROFILE_NAME}" --output-prefix Senao_UBR || true
fi

echo "----------------------------------------"
echo "[run-local] pytest exit code: ${PYTEST_RC}"
exit "${PYTEST_RC}"
