"""Fetch wireless link statistics via SSH/SNMP and validate operating rates."""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any

from traffic.operating_rate_table import lookup_spec, operating_rate_mbps
from utils.net_utils import format_snmp_host

DEFAULT_SNMP_COMMUNITY = "ubr@rw123"
DEFAULT_RADIO_IDX = 2
DEFAULT_SSH_USER = "root"

OID_SU_NAME_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.3"
OID_SU_IP_WALK_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.4"
OID_LOCAL_SNRA1_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.13"
OID_LOCAL_SNRA2_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.14"
OID_REMOTE_SNRA1_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.15"
OID_REMOTE_SNRA2_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.16"
# BTS AP view: Out (downlink to SU) / In (uplink from SU).
OID_OUT_RATE_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.10"
OID_IN_RATE_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.9"
OID_OUT_TPUT_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.11"
OID_IN_TPUT_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.12"

SUA_STAT_FIELDS = (
    "name",
    "ip",
    "mac",
    "tx_rate",
    "rx_rate",
    "l_snr1",
    "l_snr2",
    "r_snr1",
    "r_snr2",
    "tx_tput",
    "rx_tput",
)


def _run_shell(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True).strip()
    except Exception as exc:
        output = getattr(exc, "output", None) or ""
        return str(output).strip()


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


def _parse_rate_and_mcs(raw: str) -> tuple[float | None, str | None]:
    """Parse device-style rates such as ``1201 (23)``."""
    if not raw or raw == "-":
        return None, None
    text = str(raw).strip()
    rate_match = re.search(r"([\d.]+)", text.replace(",", ""))
    mcs_match = re.search(r"\((\d+)\)", text)
    rate = float(rate_match.group(1)) if rate_match else None
    mcs = mcs_match.group(1) if mcs_match else None
    return rate, mcs


def _format_rate_with_mcs(rate_mbps: float | None, mcs: str | None, raw: str | None = None) -> str:
    if raw and raw not in {"-", ""}:
        return raw
    if rate_mbps is None:
        return "-"
    if mcs:
        return f"{rate_mbps:.0f} ({mcs})"
    return f"{rate_mbps:.0f}"


def _combined_snr(values: list[str]) -> str:
    nums: list[int] = []
    for value in values:
        if not value or value == "-":
            continue
        try:
            nums.append(int(float(value)))
        except ValueError:
            continue
    if not nums:
        return "-"
    return str(max(nums))


def _extract_ip_from_snmp_value(value_part: str) -> str:
    ipv6_match = re.search(
        r"(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,}",
        value_part,
    )
    if ipv6_match:
        return ipv6_match.group(0)
    ips = re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", value_part)
    return ips[-1] if ips else ""


def _normalize_client(client: dict[str, Any]) -> dict[str, Any]:
    """Normalize BTS AP link fields to match the device monitor table."""
    out_raw = str(client.get("out_rate") or client.get("tx_rate") or "-")
    in_raw = str(client.get("in_rate") or client.get("rx_rate") or "-")
    out_rate_mbps, out_mcs = _parse_rate_and_mcs(out_raw)
    in_rate_mbps, in_mcs = _parse_rate_and_mcs(in_raw)
    if out_rate_mbps is None:
        out_rate_mbps = client.get("out_rate_mbps") or client.get("tx_rate_mbps")
    if in_rate_mbps is None:
        in_rate_mbps = client.get("in_rate_mbps") or client.get("rx_rate_mbps")

    local_snr = _combined_snr([str(client.get("l_snr1", "-")), str(client.get("l_snr2", "-"))])
    remote_snr = _combined_snr([str(client.get("r_snr1", "-")), str(client.get("r_snr2", "-"))])

    normalized = {
        **client,
        "system_name": client.get("system_name") or client.get("name") or "-",
        "ip": client.get("ip") or "-",
        "out_rate": _format_rate_with_mcs(out_rate_mbps, out_mcs, out_raw),
        "in_rate": _format_rate_with_mcs(in_rate_mbps, in_mcs, in_raw),
        "out_rate_mbps": out_rate_mbps,
        "in_rate_mbps": in_rate_mbps,
        "out_mcs": out_mcs,
        "in_mcs": in_mcs,
        "tx_rate": _format_rate_with_mcs(out_rate_mbps, out_mcs, out_raw),
        "rx_rate": _format_rate_with_mcs(in_rate_mbps, in_mcs, in_raw),
        "tx_rate_mbps": out_rate_mbps,
        "rx_rate_mbps": in_rate_mbps,
        "combined_local_snr": local_snr,
        "combined_remote_snr": remote_snr,
        "throughput_out_mbps": client.get("throughput_out_mbps") or client.get("tx_tput"),
        "throughput_in_mbps": client.get("throughput_in_mbps") or client.get("rx_tput"),
    }
    return normalized


def fetch_link_clients(
    dut_ip: str,
    *,
    snmp_community: str = DEFAULT_SNMP_COMMUNITY,
    radio_idx: int = DEFAULT_RADIO_IDX,
    source: str = "auto",
    ssh_user: str = DEFAULT_SSH_USER,
    ssh_password: str = "",
) -> list[dict[str, Any]]:
    if source not in {"auto", "ssh", "snmp"}:
        raise ValueError(f"Unsupported link stats source '{source}'")
    if source in {"auto", "ssh"}:
        ssh_clients = _fetch_link_clients_via_ssh(
            dut_ip=dut_ip,
            radio_idx=radio_idx,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
        )
        if ssh_clients or source == "ssh":
            return [_normalize_client(client) for client in ssh_clients]
    snmp_clients = _fetch_link_clients_via_snmp(
        dut_ip=dut_ip,
        snmp_community=snmp_community,
        radio_idx=radio_idx,
    )
    return [_normalize_client(client) for client in snmp_clients]


def _snmp_get(snmp_host: str, snmp_community: str, oid: str) -> str:
    cmd = f"snmpget -v 2c -c {snmp_community} {snmp_host} {oid}"
    return _parse_snmp_value(_run_shell(cmd))


def _discover_snmp_su_indices(
    snmp_host: str,
    snmp_community: str,
    radio_idx: int,
) -> list[str]:
    indices: list[str] = []
    for base_oid in (OID_SU_IP_WALK_BASE, OID_SU_NAME_BASE, OID_OUT_RATE_BASE):
        walk_cmd = f"snmpwalk -v 2c -c {snmp_community} {snmp_host} {base_oid}.{radio_idx}"
        walk_out = _run_shell(walk_cmd)
        for line in walk_out.splitlines():
            idx_match = re.search(rf"\.{radio_idx}\.(\d+)\s*=", line)
            if idx_match and idx_match.group(1) not in indices:
                indices.append(idx_match.group(1))
    return sorted(indices, key=lambda value: int(value))


def _fetch_link_clients_via_snmp(
    dut_ip: str,
    *,
    snmp_community: str,
    radio_idx: int,
) -> list[dict[str, Any]]:
    snmp_host = format_snmp_host(dut_ip)
    su_indices = _discover_snmp_su_indices(snmp_host, snmp_community, radio_idx)
    clients: list[dict[str, Any]] = []

    for su_index in su_indices:

        def get_rf(base_oid: str) -> str:
            return _snmp_get(snmp_host, snmp_community, f"{base_oid}.{radio_idx}.{su_index}")

        ip_raw = get_rf(OID_SU_IP_WALK_BASE)
        clients.append(
            {
                "su_index": int(su_index),
                "system_name": get_rf(OID_SU_NAME_BASE),
                "ip": _extract_ip_from_snmp_value(ip_raw) or ip_raw,
                "l_snr1": get_rf(OID_LOCAL_SNRA1_BASE),
                "l_snr2": get_rf(OID_LOCAL_SNRA2_BASE),
                "r_snr1": get_rf(OID_REMOTE_SNRA1_BASE),
                "r_snr2": get_rf(OID_REMOTE_SNRA2_BASE),
                "out_rate": get_rf(OID_OUT_RATE_BASE),
                "in_rate": get_rf(OID_IN_RATE_BASE),
                "tx_tput": get_rf(OID_OUT_TPUT_BASE),
                "rx_tput": get_rf(OID_IN_TPUT_BASE),
                "source": "snmp",
            }
        )
    return clients


def _ssh_read_field(
    *,
    ssh_password: str,
    ssh_user: str,
    host: str,
    path: str,
) -> str:
    pw = shlex.quote(ssh_password)
    ssh_host = shlex.quote(host)
    ssh_user_q = shlex.quote(ssh_user)
    cmd = (
        f"sshpass -p {pw} ssh -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 {ssh_user_q}@{ssh_host} "
        f"\"cat {path} 2>/dev/null || echo -\""
    )
    value = _run_shell(cmd).strip()
    return value if value else "-"


def _fetch_link_clients_via_ssh(
    *,
    dut_ip: str,
    radio_idx: int,
    ssh_user: str,
    ssh_password: str,
) -> list[dict[str, Any]]:
    if not ssh_password:
        return []

    candidate_indices = []
    for idx in (0, 1, 2, 3, int(radio_idx), 4):
        if idx >= 0 and idx not in candidate_indices:
            candidate_indices.append(idx)

    for wifi_idx in candidate_indices:
        link_count_raw = _ssh_read_field(
            ssh_password=ssh_password,
            ssh_user=ssh_user,
            host=dut_ip,
            path=f"/sys/class/kwn/wifi{wifi_idx}/statistics/links",
        )
        link_match = re.search(r"(\d+)", str(link_count_raw or "0"))
        link_count = int(link_match.group(1)) if link_match else 0
        if link_count <= 0:
            continue

        clients: list[dict[str, Any]] = []
        for sua_idx in range(1, link_count + 1):
            client: dict[str, Any] = {
                "su_index": sua_idx,
                "source": "ssh",
            }
            for field in SUA_STAT_FIELDS:
                value = _ssh_read_field(
                    ssh_password=ssh_password,
                    ssh_user=ssh_user,
                    host=dut_ip,
                    path=f"/sys/class/kwn/wifi{wifi_idx}/statistics/sua{sua_idx}/{field}",
                )
                if value != "-":
                    client[field] = value
            if client.get("name"):
                client["system_name"] = client["name"]
            if client.get("tx_rate") or client.get("rx_rate") or client.get("ip"):
                clients.append(client)

        if clients:
            return clients

    return []


def validate_operating_rates(
    *,
    bandwidth: str,
    configured_mcs: str,
    clients: list[dict[str, Any]],
    spatial_streams: int = 2,
    tolerance_mbps: float = 10.0,
    tolerance_pct: float = 0.08,
) -> dict[str, Any]:
    """Validate BTS downlink (Out) operating rate against the configured MCS."""
    expected = operating_rate_mbps(bandwidth, configured_mcs, spatial_streams=spatial_streams)
    spec = lookup_spec(configured_mcs, bandwidth, spatial_streams=spatial_streams)
    client_checks: list[dict[str, Any]] = []
    any_mismatch = False

    for client in clients:
        normalized = _normalize_client(client)
        out_rate = normalized.get("out_rate_mbps") or normalized.get("tx_rate_mbps")
        out_ok = _rate_matches(out_rate, expected, tolerance_mbps, tolerance_pct)
        mismatch = out_ok is not True
        any_mismatch = any_mismatch or mismatch
        client_checks.append(
            {
                **normalized,
                "expected_rate_mbps": expected,
                "out_rate_ok": out_ok,
                "tx_rate_ok": out_ok,
                "rx_rate_ok": None,
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
        "connected_cpe_count": len(client_checks),
        "operating_rate_ok": not any_mismatch and bool(client_checks),
        "operating_rate_mismatch": any_mismatch,
    }


def _rate_matches(
    actual: float | None,
    expected: float,
    tolerance_mbps: float,
    tolerance_pct: float,
) -> bool | None:
    if actual is None:
        return None
    delta = abs(actual - expected)
    return delta <= tolerance_mbps or delta <= expected * tolerance_pct
