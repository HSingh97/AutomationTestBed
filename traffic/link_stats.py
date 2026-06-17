"""Fetch wireless link statistics via BTS SSH (sysfs link table)."""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any

from traffic.operating_rate_table import lookup_spec, operating_rate_mbps
from utils.net_utils import is_ipv6_literal, normalize_ip

DEFAULT_SSH_USER = "root"

SUA_STAT_FIELDS = (
    "name",
    "ip",
    "ipv6",
    "ipv6addr",
    "mgmt_ip",
    "mac",
    "tx_rate",
    "rx_rate",
    "tx_mcs",
    "rx_mcs",
    "out_mcs",
    "in_mcs",
    "l_snr1",
    "l_snr2",
    "r_snr1",
    "r_snr2",
    "l_rssi1",
    "l_rssi2",
    "r_rssi1",
    "r_rssi2",
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


def _extract_ip_from_text(value: str) -> str:
    if not value or value.strip() in {"-", "0.0.0.0", "0"}:
        return ""
    text = value.strip()
    if is_ipv6_literal(text):
        return normalize_ip(text)
    ipv6_match = re.search(r"(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,}", text)
    if ipv6_match:
        return normalize_ip(ipv6_match.group(0))
    ips = re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", text)
    for candidate in reversed(ips):
        if candidate != "0.0.0.0":
            return candidate
    return ""


def _resolve_client_ip(client: dict[str, Any], cpe_hosts: list[str] | None, index: int) -> str:
    for key in ("ip", "ipv6", "ipv6addr", "mgmt_ip"):
        resolved = _extract_ip_from_text(str(client.get(key) or ""))
        if resolved:
            return resolved
    if cpe_hosts and 0 < index <= len(cpe_hosts):
        return normalize_ip(cpe_hosts[index - 1])
    return ""


def _rate_display(
    rate_raw: str,
    mcs_hint: str | None,
) -> str:
    rate_mbps, mcs_parsed = _parse_rate_and_mcs(rate_raw)
    mcs = (mcs_hint or mcs_parsed or "").strip() or None
    return _format_rate_with_mcs(rate_mbps, mcs, rate_raw)


def _normalize_client(
    client: dict[str, Any],
    *,
    cpe_hosts: list[str] | None = None,
    display_index: int = 0,
) -> dict[str, Any]:
    """Normalize BTS AP link fields to match the device monitor table."""
    tx_raw = str(client.get("tx_rate") or client.get("out_rate") or "-")
    rx_raw = str(client.get("rx_rate") or client.get("in_rate") or "-")
    tx_mcs_hint = str(client.get("tx_mcs") or client.get("out_mcs") or "").strip() or None
    rx_mcs_hint = str(client.get("rx_mcs") or client.get("in_mcs") or "").strip() or None
    out_rate_mbps, out_mcs = _parse_rate_and_mcs(tx_raw)
    in_rate_mbps, in_mcs = _parse_rate_and_mcs(rx_raw)
    if out_mcs is None and tx_mcs_hint and tx_mcs_hint.isdigit():
        out_mcs = tx_mcs_hint
    if in_mcs is None and rx_mcs_hint and rx_mcs_hint.isdigit():
        in_mcs = rx_mcs_hint
    if out_rate_mbps is None:
        out_rate_mbps = client.get("out_rate_mbps") or client.get("tx_rate_mbps")
    if in_rate_mbps is None:
        in_rate_mbps = client.get("in_rate_mbps") or client.get("rx_rate_mbps")

    local_snr = _combined_snr([str(client.get("l_snr1", "-")), str(client.get("l_snr2", "-"))])
    remote_snr = _combined_snr([str(client.get("r_snr1", "-")), str(client.get("r_snr2", "-"))])
    resolved_ip = _resolve_client_ip(client, cpe_hosts, display_index)

    normalized = {
        **client,
        "system_name": client.get("system_name") or client.get("name") or "-",
        "ip": resolved_ip or "-",
        "tx_rate": _rate_display(tx_raw, out_mcs),
        "rx_rate": _rate_display(rx_raw, in_mcs),
        "out_rate": _rate_display(tx_raw, out_mcs),
        "in_rate": _rate_display(rx_raw, in_mcs),
        "out_rate_mbps": out_rate_mbps,
        "in_rate_mbps": in_rate_mbps,
        "out_mcs": out_mcs,
        "in_mcs": in_mcs,
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
    radio_idx: int = 1,
    source: str = "ssh",
    ssh_user: str = DEFAULT_SSH_USER,
    ssh_password: str = "",
    cpe_hosts: list[str] | None = None,
    snmp_community: str = "",
    snmp_radio_idx: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch per-SU link clients from BTS SSH (sysfs). SNMP params are ignored."""
    del snmp_community, snmp_radio_idx
    if source not in {"auto", "ssh"}:
        raise ValueError(f"Unsupported link stats source '{source}' (SNMP removed; use ssh)")
    ssh_clients = _fetch_link_clients_via_ssh(
        dut_ip=dut_ip,
        radio_idx=radio_idx,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        cpe_hosts=cpe_hosts,
    )
    return ssh_clients


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
    cpe_hosts: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not ssh_password:
        return []

    link_count_raw = _ssh_read_field(
        ssh_password=ssh_password,
        ssh_user=ssh_user,
        host=dut_ip,
        path=f"/sys/class/kwn/wifi{radio_idx}/statistics/links",
    )
    link_match = re.search(r"(\d+)", str(link_count_raw or "0"))
    link_count = int(link_match.group(1)) if link_match else 0
    if link_count <= 0:
        return []

    clients: list[dict[str, Any]] = []
    for sua_idx in range(1, link_count + 1):
        assoc = _ssh_read_field(
            ssh_password=ssh_password,
            ssh_user=ssh_user,
            host=dut_ip,
            path=f"/sys/class/kwn/wifi{radio_idx}/statistics/sua{sua_idx}/assoc",
        )
        if str(assoc).strip() in {"", "0", "-"}:
            continue

        client: dict[str, Any] = {
            "su_index": sua_idx,
            "source": "ssh",
        }
        for field in SUA_STAT_FIELDS:
            value = _ssh_read_field(
                ssh_password=ssh_password,
                ssh_user=ssh_user,
                host=dut_ip,
                path=f"/sys/class/kwn/wifi{radio_idx}/statistics/sua{sua_idx}/{field}",
            )
            if value != "-":
                client[field] = value

        ip_from_assoc = _ssh_read_field(
            ssh_password=ssh_password,
            ssh_user=ssh_user,
            host=dut_ip,
            path=(
                f"/sys/class/kwn/wifi{radio_idx}/statistics/sua{sua_idx}/ip"
            ),
        )
        if ip_from_assoc != "-":
            client["ip"] = ip_from_assoc

        if client.get("name"):
            client["system_name"] = client["name"]
        if client.get("tx_rate") or client.get("rx_rate") or client.get("mac"):
            clients.append(
                _normalize_client(client, cpe_hosts=cpe_hosts, display_index=len(clients) + 1)
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
    """Validate BTS downlink (Out) operating rate against the configured MCS."""
    expected = operating_rate_mbps(bandwidth, configured_mcs, spatial_streams=spatial_streams)
    spec = lookup_spec(configured_mcs, bandwidth, spatial_streams=spatial_streams)
    client_checks: list[dict[str, Any]] = []
    any_mismatch = False

    for index, client in enumerate(clients, start=1):
        if client.get("out_rate_mbps") is None and client.get("tx_rate_mbps") is None:
            normalized = _normalize_client(client, cpe_hosts=None, display_index=index)
        else:
            normalized = client
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
