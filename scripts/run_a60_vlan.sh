#!/usr/bin/env bash
# A60/A61 VLAN suite — BTS 10.0.0.120, CPE-side PC 10.0.0.121.
#
# Examples:
#   ./scripts/run_a60_vlan.sh                    # dry collection / N/A skips
#   ./scripts/run_a60_vlan.sh --allow-vlan-lab   # live TRex (implemented cases)
#   ./scripts/run_a60_vlan.sh --allow-vlan-lab -k VLAN_03
#   VLAN_TREX_HOST=192.168.3.3 ./scripts/run_a60_vlan.sh --allow-vlan-lab -k VLAN_03

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:-}:."

pytest tests/VLAN/test_vlan.py \
  --profile a60_lab \
  --local-ip 10.0.0.120 \
  -m VLAN \
  "$@"
