"""Fetch per-SU link statistics from BTS sysfs ``/sys/class/kwn/sua{N}/statistics/``.

Reusable for throughput reports, link validation, and future bench tooling.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any, Callable

from utils.net_utils import is_ipv6_literal, normalize_ip

DEFAULT_SSH_USER = "root"

# Fields exposed under /sys/class/kwn/sua{N}/statistics/ on UBR630 BTS.
KWN_SUA_STAT_FIELDS: tuple[str, ...] = (
    "ip",
    "ipv6",
    "mac",
    "r_custname",
    "r_model",
    "r_serialno",
    "tx_rate",
    "rx_rate",
    "tx_rate_mcs",
    "rx_rate_mcs",
    "l_snra1",
    "l_snra2",
    "l_snra3",
    "l_snra4",
    "r_snra1",
    "r_snra2",
    "r_snra3",
    "r_snra4",
    "comb_rssi",
    "r_comb_rssi",
    "l_noise",
    "r_noise",
    "l_power",
    "r_power",
    "tx_tput",
    "rx_tput",
    "associd",
    "assoc_time",
    "link_capacity",
    "opmode",
)

ReadFieldFn = Callable[[str], str]


def _run_shell(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True).strip()
    except Exception as exc:
        output = getattr(exc, "output", None) or ""
        return str(output).strip()


def ssh_read_sysfs_field(
    *,
    host: str,
    path: str,
    ssh_user: str = DEFAULT_SSH_USER,
    ssh_password: str = "",
) -> str:
    """Read one sysfs file on the BTS via sshpass SSH."""
    if not ssh_password:
        return "-"
    pw = shlex.quote(ssh_password)
    ssh_host = shlex.quote(host)
    ssh_user_q = shlex.quote(ssh_user)
    cmd = (
        f"sshpass -p {pw} ssh -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 {ssh_user_q}@{ssh_host} "
        f"\"cat {shlex.quote(path)} 2>/dev/null || echo -\""
    )
    value = _run_shell(cmd).strip()
    return value if value else "-"


def resolve_sua_display_ip(*, ipv4: str = "", ipv6: str = "") -> str:
    """Prefer IPv4; when missing or 0.0.0.0, use IPv6."""
    ip4 = str(ipv4 or "").strip()
    if ip4 and ip4 not in {"-", "0", "0.0.0.0"}:
        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip4):
            return ip4
    ip6 = str(ipv6 or "").strip()
    if not ip6 or ip6 == "-":
        return ""
    if is_ipv6_literal(ip6):
        return normalize_ip(ip6)
    ipv6_match = re.search(r"(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,}", ip6)
    if ipv6_match:
        return normalize_ip(ipv6_match.group(0))
    return ip6


def _meaningful_stat(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in {"-", "0"}


def is_sua_associated(raw: dict[str, Any]) -> bool:
    """True when the SUA slot has link identity or traffic."""
    if _meaningful_stat(str(raw.get("mac") or "")):
        return True
    if resolve_sua_display_ip(ipv4=str(raw.get("ip") or ""), ipv6=str(raw.get("ipv6") or "")):
        return True
    for key in ("rx_rate", "tx_rate", "associd"):
        if _meaningful_stat(str(raw.get(key) or "")):
            return True
    return False


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


def _format_rate_with_mcs(rate_mbps: float | None, mcs: str | None) -> str:
    if rate_mbps is None:
        return "-"
    if mcs and str(mcs).strip() not in {"", "-", "0"}:
        return f"{rate_mbps:.0f} ({mcs})"
    return f"{rate_mbps:.0f}"


def _chain_values(raw: dict[str, Any], key_prefix: str, *, count: int = 2) -> tuple[str, str]:
    """Read ``l_snra1``/``r_snra1`` style chain values; skip zero/empty."""
    values: list[str] = []
    for i in range(1, count + 1):
        value = str(raw.get(f"{key_prefix}a{i}") or "").strip()
        if not value or value in {"-", "0"}:
            continue
        try:
            if int(float(value)) == 0:
                continue
        except ValueError:
            pass
        values.append(value)
    if not values:
        return "-", "-"
    if len(values) == 1:
        return values[0], values[0]
    return values[0], values[1]


def _combined_metric(values: list[str]) -> str:
    nums: list[int] = []
    for value in values:
        if not value or value in {"-", "0"}:
            continue
        try:
            nums.append(int(float(value)))
        except ValueError:
            continue
    if not nums:
        return "-"
    return str(max(nums))


def format_rssi_dbm(value: str | None) -> str:
    """Format sysfs RSSI — positive sysfs values are shown as negative dBm."""
    text = str(value or "").strip()
    if not text or text in {"-", "0"}:
        return "—"
    try:
        number = float(text)
    except ValueError:
        return text if text.startswith("-") else "—"
    if number > 0:
        return str(int(-number))
    return str(int(number))


def _rssi_chain_values(raw: dict[str, Any]) -> tuple[str, str, str, str]:
    """Local/remote RSSI A1/A2 from comb_rssi and optional per-chain power."""
    l_a1 = format_rssi_dbm(str(raw.get("comb_rssi") or ""))
    l_a2 = format_rssi_dbm(str(raw.get("l_power") or ""))
    r_a1 = format_rssi_dbm(str(raw.get("r_comb_rssi") or ""))
    r_a2 = format_rssi_dbm(str(raw.get("r_power") or ""))
    if l_a2 == "—":
        l_a2 = "—"
    if r_a2 == "—":
        r_a2 = "—"
    return l_a1, l_a2, r_a1, r_a2


def normalize_kwn_sua_client(
    raw: dict[str, Any],
    *,
    display_index: int = 0,
    cpe_hosts: list[str] | None = None,
) -> dict[str, Any]:
    """Map raw sysfs SUA fields to the shared link-client shape used by reports."""
    sua_index = int(raw.get("sua_index") or display_index or 0)
    tx_mcs = str(raw.get("tx_rate_mcs") or "").strip() or None
    rx_mcs = str(raw.get("rx_rate_mcs") or "").strip() or None
    tx_rate_mbps = _parse_rate_mbps(str(raw.get("tx_rate") or ""))
    rx_rate_mbps = _parse_rate_mbps(str(raw.get("rx_rate") or ""))

    l_snr1, l_snr2 = _chain_values(raw, "l_snr")
    r_snr1, r_snr2 = _chain_values(raw, "r_snr")
    l_rssi1, l_rssi2, r_rssi1, r_rssi2 = _rssi_chain_values(raw)

    display_ip = resolve_sua_display_ip(
        ipv4=str(raw.get("ip") or ""),
        ipv6=str(raw.get("ipv6") or ""),
    )
    if not display_ip and cpe_hosts and 0 < display_index <= len(cpe_hosts):
        display_ip = normalize_ip(cpe_hosts[display_index - 1])

    system_name = (
        str(raw.get("r_custname") or "").strip()
        or str(raw.get("r_model") or "").strip()
        or f"cpe{display_index or sua_index}"
    )

    tx_rate = _format_rate_with_mcs(tx_rate_mbps, tx_mcs)
    rx_rate = _format_rate_with_mcs(rx_rate_mbps, rx_mcs)

    return {
        **raw,
        "su_index": sua_index,
        "system_name": system_name,
        "name": system_name,
        "ip": display_ip or "-",
        "ipv6": str(raw.get("ipv6") or "").strip() or "-",
        "l_snr1": l_snr1,
        "l_snr2": l_snr2,
        "r_snr1": r_snr1,
        "r_snr2": r_snr2,
        "l_rssi1": l_rssi1,
        "l_rssi2": l_rssi2,
        "r_rssi1": r_rssi1,
        "r_rssi2": r_rssi2,
        "combined_local_snr": _combined_metric([l_snr1, l_snr2]),
        "combined_remote_snr": _combined_metric([r_snr1, r_snr2]),
        "tx_rate": tx_rate,
        "rx_rate": rx_rate,
        "out_rate": tx_rate,
        "in_rate": rx_rate,
        "out_rate_mbps": tx_rate_mbps,
        "in_rate_mbps": rx_rate_mbps,
        "tx_rate_mbps": tx_rate_mbps,
        "rx_rate_mbps": rx_rate_mbps,
        "out_mcs": tx_mcs,
        "in_mcs": rx_mcs,
        "operating_mcs": rx_mcs or tx_mcs,
        "throughput_out_mbps": raw.get("tx_tput"),
        "throughput_in_mbps": raw.get("rx_tput"),
        "source": "kwn_sua_sysfs",
    }


def fetch_kwn_sua_statistics(
    dut_ip: str,
    *,
    ssh_user: str = DEFAULT_SSH_USER,
    ssh_password: str = "",
    max_sua: int = 16,
    cpe_hosts: list[str] | None = None,
    read_field: ReadFieldFn | None = None,
) -> list[dict[str, Any]]:
    """Return normalized link rows for associated ``sua1`` … ``sua{max_sua}`` slots."""
    if not ssh_password and read_field is None:
        return []

    def _read(path: str) -> str:
        if read_field is not None:
            return read_field(path)
        return ssh_read_sysfs_field(
            host=dut_ip,
            path=path,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
        )

    clients: list[dict[str, Any]] = []
    for sua_idx in range(1, max_sua + 1):
        base = f"/sys/class/kwn/sua{sua_idx}/statistics"
        raw: dict[str, Any] = {"sua_index": sua_idx}
        for field in KWN_SUA_STAT_FIELDS:
            value = _read(f"{base}/{field}")
            if value != "-":
                raw[field] = value
        if not is_sua_associated(raw):
            continue
        clients.append(
            normalize_kwn_sua_client(
                raw,
                display_index=len(clients) + 1,
                cpe_hosts=cpe_hosts,
            )
        )
    return clients
