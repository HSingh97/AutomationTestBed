#!/usr/bin/env bash
# Install and configure ser2net so /dev/ttyUSB0 (BTS) and /dev/ttyUSB1 (CPE/other)
# can be shared between minicom (live monitoring) and the ProcessMonitor
# automation. Idempotent: safe to re-run.
#
# After running this, point minicom at the TCP broker:
#     minicom -D telnet://127.0.0.1:7000      # BTS console
#     minicom -D telnet://127.0.0.1:7001      # secondary console (if used)
#
# And run ProcessMonitor with:
#     PROCMON_SERIAL_DEVICE=socket://127.0.0.1:7000
#     PROCMON_CLEAN_REBOOT_BEFORE_KILL=true

set -euo pipefail

CONFIG_PATH=/etc/ser2net.yaml
PORT_BTS=7000
PORT_AUX=7001

require_sudo() {
  if ! sudo -n true 2>/dev/null; then
    echo "[serial-broker] sudo is required (passwordless preferred). Aborting." >&2
    exit 1
  fi
}

stop_minicom_on_ttyUSB() {
  local pids
  pids=$(pgrep -f "minicom .*-D[[:space:]]*/dev/ttyUSB[01]" || true)
  if [[ -n "$pids" ]]; then
    echo "[serial-broker] Stopping minicom processes holding /dev/ttyUSB[01]: $pids"
    # shellcheck disable=SC2086
    sudo kill $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    sudo kill -9 $pids 2>/dev/null || true
  fi
}

install_ser2net() {
  if command -v ser2net >/dev/null 2>&1; then
    echo "[serial-broker] ser2net already installed: $(ser2net -v 2>&1 | head -1)"
    return
  fi
  echo "[serial-broker] Installing ser2net..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ser2net
}

write_config() {
  echo "[serial-broker] Writing $CONFIG_PATH"
  sudo tee "$CONFIG_PATH" >/dev/null <<YAML
# Managed by scripts/setup_serial_broker.sh — do not hand-edit; re-run the script.
# Multi-reader serial-to-TCP broker for the BTS USB consoles.

connection: &bts_console
  accepter: tcp,127.0.0.1,${PORT_BTS}
  enable: on
  options:
    banner: ""
    kickolduser: false
    telnet-brk-on-sync: true
    max-connections: 8
  connector: serialdev,/dev/ttyUSB0,115200n81,local

connection: &aux_console
  accepter: tcp,127.0.0.1,${PORT_AUX}
  enable: on
  options:
    banner: ""
    kickolduser: false
    telnet-brk-on-sync: true
    max-connections: 8
  connector: serialdev,/dev/ttyUSB1,115200n81,local
YAML
}

restart_service() {
  echo "[serial-broker] Restarting ser2net service..."
  sudo systemctl daemon-reload || true
  sudo systemctl enable ser2net >/dev/null 2>&1 || true
  sudo systemctl restart ser2net
  sleep 1
  sudo systemctl --no-pager --lines=15 status ser2net || true
}

verify() {
  echo "[serial-broker] Verifying TCP brokers are listening..."
  ss -ltn | grep -E ":${PORT_BTS}|:${PORT_AUX}" || {
    echo "[serial-broker] WARN: expected TCP ports ${PORT_BTS}/${PORT_AUX} not listening." >&2
    exit 2
  }
  echo "[serial-broker] Done. Brokers ready:"
  echo "  /dev/ttyUSB0 -> telnet 127.0.0.1 ${PORT_BTS}  (BTS console)"
  echo "  /dev/ttyUSB1 -> telnet 127.0.0.1 ${PORT_AUX}  (secondary console)"
}

require_sudo
stop_minicom_on_ttyUSB
install_ser2net
write_config
restart_service
verify
