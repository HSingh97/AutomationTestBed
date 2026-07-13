#!/usr/bin/env bash
# System packages required by lab SSH / TRex / BTS tooling (not installable via pip).
# Sourced from Jenkins "Install Dependencies" stages.
set -euo pipefail

need=()
if ! command -v sshpass >/dev/null 2>&1; then
  need+=(sshpass)
fi

if ((${#need[@]} > 0)); then
  echo "[deps] Installing system packages: ${need[*]}"
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "[deps] ERROR: apt-get not available; install manually: ${need[*]}" >&2
    exit 1
  fi
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${need[@]}"
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "[deps] ERROR: sshpass is required for password SSH to BTS/TRex/CPE and is still missing." >&2
  echo "[deps] Fix: sudo apt-get install -y sshpass" >&2
  exit 1
fi

echo "[deps] sshpass: $(command -v sshpass)"
sshpass -V 2>&1 | head -1 || true
