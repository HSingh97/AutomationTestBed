#!/usr/bin/env bash
# Install Greenbone Community Edition (OpenVAS) on the lab PC for FIREWALL_06.
# Run on Ubuntu 22.04/24.04. Requires sudo. First feed sync can take 1–2 hours.

set -euo pipefail

MODE="${1:-docker}"

echo "[openvas] mode=$MODE"

if [[ "$MODE" == "native" ]]; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y gvm
  echo "[openvas] running gvm-setup (feed sync is long)..."
  sudo gvm-setup
  sudo gvm-check-setup || true
  echo
  echo "Start:  sudo gvm-start"
  echo "Then set GVM_USER / GVM_PASSWORD and re-run FIREWALL_06."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[openvas] docker not found. Install docker first, or run: $0 native"
  exit 1
fi

WORKDIR="${OPENVAS_DIR:-$HOME/greenbone-community-container}"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

COMPOSE_URL="https://greenbone.github.io/docs/latest/_static/docker-compose.yml"
if [[ ! -f docker-compose.yml ]]; then
  echo "[openvas] downloading Greenbone compose from $COMPOSE_URL"
  curl -fsSL "$COMPOSE_URL" -o docker-compose.yml
fi

docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d

echo
echo "[openvas] containers started in $WORKDIR"
echo "  UI:        https://127.0.0.1:9392  (default user admin)"
echo "  Password:  docker compose -f $WORKDIR/docker-compose.yml logs gvmd | grep -i password"
echo
echo "After feeds are synced, either:"
echo "  export GVM_USER=admin GVM_PASSWORD='<password>'"
echo "  export OPENVAS_SCAN_CMD='<your gvm-cli XML task that scans {host}>'"
echo "then:"
echo "  pytest tests/Firewall -k FIREWALL_06 --allow-firewall-lab --profile ipv4_lab"
