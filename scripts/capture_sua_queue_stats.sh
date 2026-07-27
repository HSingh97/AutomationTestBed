#!/usr/bin/env bash
# Capture suaN queue0..queue7 rx_tput/tx_tput every INTERVAL seconds while traffic runs.
#
# Example (1 Hz for 120s on sua4):
#   DUT=10.0.0.1 DUT_PW='Sen@0ubRNwk$' SUA=sua4 INTERVAL=1 DURATION=120 \
#     ./scripts/capture_sua_queue_stats.sh
#
# Run this in parallel with qos_throughput_script / run_qa_lab_qos_trex.sh.

set -euo pipefail

DUT="${DUT:-10.0.0.1}"
DUT_PW="${DUT_PW:-Sen@0ubRNwk$}"
SUA="${SUA:-sua4}"
INTERVAL="${INTERVAL:-1}"
DURATION="${DURATION:-60}"
OUT="${OUT:-reports/artifacts/${SUA}_queue_stats_$(date +%Y%m%d_%H%M%S).csv}"

mkdir -p "$(dirname "$OUT")"

{
  echo -n "timestamp"
  for q in 0 1 2 3 4 5 6 7; do
    echo -n ",q${q}_rx,q${q}_tx"
  done
  echo
} >"$OUT"

echo "[queue-stats] DUT=$DUT $SUA interval=${INTERVAL}s duration=${DURATION}s -> $OUT"

END=$((SECONDS + DURATION))
SAMPLE=0
while [ "$SECONDS" -lt "$END" ]; do
  TS=$(date +%H:%M:%S)
  RAW=$(
    sshpass -p "$DUT_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${DUT}" \
      "for q in 0 1 2 3 4 5 6 7; do
         rx=\$(cat /sys/class/kwn/${SUA}/queue_stats/queue\$q/rx_tput 2>/dev/null || echo 0)
         tx=\$(cat /sys/class/kwn/${SUA}/queue_stats/queue\$q/tx_tput 2>/dev/null || echo 0)
         printf '%s %s ' \"\$rx\" \"\$tx\"
       done; echo" 2>/dev/null || true
  )
  LINE="$TS"
  # shellcheck disable=SC2086
  set -- $RAW
  while [ $# -ge 2 ]; do
    LINE="${LINE},${1},${2}"
    shift 2
  done
  echo "$LINE" >>"$OUT"
  SAMPLE=$((SAMPLE + 1))
  # compact console line
  printf "[%s] #%03d " "$TS" "$SAMPLE"
  i=0
  # shellcheck disable=SC2086
  set -- $RAW
  while [ $# -ge 2 ]; do
    printf "Q%d rx=%s tx=%s  " "$i" "$1" "$2"
    i=$((i + 1))
    shift 2
  done
  echo
  sleep "$INTERVAL"
done

echo "[queue-stats] done samples=$SAMPLE file=$OUT"
