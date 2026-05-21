"""Fetch wireless link statistics over SNMP and validate operating rates."""

from __future__ import annotations

import re
import subprocess
from typing import Any

from traffic.operating_rate_table import lookup_spec, operating_rate_mbps
from utils.net_utils import format_snmp_host

DEFAULT_SNMP_COMMUNITY = "ubr@rw123"
DEFAULT_RADIO_IDX = 2

OID_SU_IP_WALK_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.4"
OID_LOCAL_SNRA1_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.13"
OID_LOCAL_SNRA2_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.14"
OID_REMOTE_SNRA1_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.15"
OID_REMOTE_SNRA2_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.16"
OID_TX_RATE_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.10"
OID_RX_RATE_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.9"


def _run_shell(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True).strip()
    except Exception:
        return ""


def _parse_snmp_value(output: str) -> str:
    if "No Such" in output or not output:
        return "-"
    if "STRING:" in output:
        return output.split("STRING:")[1].strip().strip('"')
    return output.split(":")[-1].strip() if ":" in output else output.split()[-1].strip()


def _parse_rate_mbps(raw: str) -> float | None:
    if not raw or raw == "-":
        return None
    match = re.search(r"([\d.]+)", str(raw).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def fetch_link_clients(
    dut_ip: str,
    *,
    snmp_community: str = DEFAULT_SNMP_COMMUNITY,
    radio_idx: int = DEFAULT_RADIO_IDX,
) -> list[dict[str, Any]]:
    snmp_host = format_snmp_host(dut_ip)
    walk_cmd = f"snmpwalk -v 2c -c {snmp_community} {snmp_host} {OID_SU_IP_WALK_BASE}.{radio_idx}"
    walk_out = _run_shell(walk_cmd)
    clients: list[dict[str, Any]] = []

    for line in walk_out.splitlines():
        if "=" not in line:
            continue
        value_part = line.split("=", 1)[1].strip()
        if "STRING:" in value_part.upper():
            value_part = re.split(r"STRING:\s*", value_part, maxsplit=1, flags=re.IGNORECASE)[-1]
        value_part = value_part.strip().strip('"')
        ips = re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", value_part)
        idx_match = re.search(rf"\.{radio_idx}\.(\d+)\s*=", line)
        if not ips or not idx_match:
            continue
        su_index = idx_match.group(1)

        def get_rf(base_oid: str) -> str:
            cmd = f"snmpget -v 2c -c {snmp_community} {snmp_host} {base_oid}.{radio_idx}.{su_index}"
            return _parse_snmp_value(_run_shell(cmd))

        clients.append(
            {
                "ip": ips[-1],
                "l_snr1": get_rf(OID_LOCAL_SNRA1_BASE),
                "l_snr2": get_rf(OID_LOCAL_SNRA2_BASE),
                "r_snr1": get_rf(OID_REMOTE_SNRA1_BASE),
                "r_snr2": get_rf(OID_REMOTE_SNRA2_BASE),
                "tx_rate": get_rf(OID_TX_RATE_BASE),
                "rx_rate": get_rf(OID_RX_RATE_BASE),
                "tx_rate_mbps": _parse_rate_mbps(get_rf(OID_TX_RATE_BASE)),
                "rx_rate_mbps": _parse_rate_mbps(get_rf(OID_RX_RATE_BASE)),
            }
        )
    return clients


def validate_operating_rates(
    *,
    bandwidth: str,
    configured_mcs: str,
    clients: list[dict[str, Any]],
    spatial_streams: int = 2,
    tolerance_mbps: float = 10.0,
    tolerance_pct: float = 0.08,
) -> dict[str, Any]:
    expected = operating_rate_mbps(bandwidth, configured_mcs, spatial_streams=spatial_streams)
    spec = lookup_spec(configured_mcs, bandwidth, spatial_streams=spatial_streams)
    client_checks: list[dict[str, Any]] = []
    any_mismatch = False

    for client in clients:
        tx = client.get("tx_rate_mbps")
        rx = client.get("rx_rate_mbps")
        tx_ok = _rate_matches(tx, expected, tolerance_mbps, tolerance_pct)
        rx_ok = _rate_matches(rx, expected, tolerance_mbps, tolerance_pct)
        mismatch = not (tx_ok and rx_ok)
        any_mismatch = any_mismatch or mismatch
        client_checks.append(
            {
                **client,
                "expected_rate_mbps": expected,
                "tx_rate_ok": tx_ok,
                "rx_rate_ok": rx_ok,
                "rate_mismatch": mismatch,
            }
        )

    return {
        "configured_mcs": configured_mcs,
        "spec": spec,
        "expected_operating_rate_mbps": expected,
        "tolerance_mbps": tolerance_mbps,
        "tolerance_pct": tolerance_pct,
        "clients": client_checks,
        "operating_rate_ok": not any_mismatch and bool(client_checks),
        "operating_rate_mismatch": any_mismatch,
    }


def _rate_matches(
    actual: float | None,
    expected: float,
    tolerance_mbps: float,
    tolerance_pct: float,
) -> bool:
    if actual is None:
        return False
    delta = abs(actual - expected)
    return delta <= tolerance_mbps or delta <= expected * tolerance_pct
