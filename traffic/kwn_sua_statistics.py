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


def _is_ssh_noise(text: str) -> bool:
    """True when shell/SSH failure text leaked into a supposed sysfs value."""
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    markers = (
        "sshpass",
        "permission denied",
        "connection refused",
        "connection timed out",
        "no route to host",
        "name or service not known",
        "could not resolve",
        "/bin/sh:",
        "command not found",
        "no such file",
    )
    return any(marker in lower for marker in markers)


def clean_sysfs_value(value: str) -> str:
    """Return a single-line sysfs value, or '-' when empty/SSH noise."""
    text = str(value or "").strip()
    if not text or _is_ssh_noise(text):
        return "-"
    # Sysfs scalars are single-line; multi-line is almost always command noise.
    if "\n" in text or "\r" in text:
        return "-"
    return text


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
    return clean_sysfs_value(_run_shell(cmd))


def resolve_sua_display_ip(*, ipv4: str = "", ipv6: str = "") -> str:
    """Prefer IPv4; when missing or 0.0.0.0, use IPv6. Never return SSH/shell noise."""
    candidates = (
        clean_sysfs_value(str(ipv4 or "")),
        clean_sysfs_value(str(ipv6 or "")),
    )
    # Prefer a real IPv4 when present.
    for candidate in candidates:
        if candidate in {"-", "0", "0.0.0.0"}:
            continue
        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", candidate):
            return candidate
    # Otherwise accept IPv6 from either sysfs field (ip or ipv6).
    for candidate in candidates:
        if candidate in {"-", "0", "0.0.0.0"}:
            continue
        if is_ipv6_literal(candidate):
            return normalize_ip(candidate)
        ipv6_match = re.search(r"(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,}", candidate)
        if ipv6_match:
            parsed = normalize_ip(ipv6_match.group(0))
            if is_ipv6_literal(parsed):
                return parsed
    return ""


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
    """Local/remote RSSI from comb_rssi sysfs fields (not l_power/r_power)."""
    l_a1 = format_rssi_dbm(str(raw.get("comb_rssi") or ""))
    r_a1 = format_rssi_dbm(str(raw.get("r_comb_rssi") or ""))
    # Second-chain RSSI sysfs is not exposed; avoid l_power/r_power (tx power, not dBm RSSI).
    return l_a1, "—", r_a1, "—"


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


def _parse_bulk_kwn_output(raw: str) -> dict[int, dict[str, str]]:
    """Parse one-SSH bulk dump of ``/sys/class/kwn/sua{N}/statistics`` fields."""
    slots: dict[int, dict[str, str]] = {}
    current_idx: int | None = None
    current: dict[str, str] = {}
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        if text == "---":
            if current_idx is not None:
                slots[current_idx] = current
            current_idx = None
            current = {}
            continue
        if text.startswith("SUA_INDEX="):
            if current_idx is not None:
                slots[current_idx] = current
            try:
                current_idx = int(text.split("=", 1)[1])
            except ValueError:
                current_idx = None
            current = {}
            continue
        if "=" in text and current_idx is not None:
            key, value = text.split("=", 1)
            if value != "-":
                current[key] = value
    if current_idx is not None:
        slots[current_idx] = current
    return slots


def ssh_read_kwn_sysfs_bulk(
    *,
    host: str,
    ssh_user: str = DEFAULT_SSH_USER,
    ssh_password: str = "",
    max_sua: int = 16,
    fields: tuple[str, ...] = KWN_SUA_STAT_FIELDS,
) -> dict[int, dict[str, str]]:
    """Read all SUA statistics in a single SSH session."""
    if not ssh_password:
        return {}
    field_list = " ".join(fields)
    inner = (
        f"for idx in $(seq 1 {max_sua}); do "
        f'base="/sys/class/kwn/sua${{idx}}/statistics"; '
        f'[ -d "$base" ] || continue; '
        f'echo "SUA_INDEX=$idx"; '
        f"for f in {field_list}; do "
        f'printf "%s=" "$f"; cat "$base/$f" 2>/dev/null || echo -; echo; '
        f"done; echo ---; done"
    )
    pw = shlex.quote(ssh_password)
    ssh_host = shlex.quote(host)
    ssh_user_q = shlex.quote(ssh_user)
    cmd = (
        f"sshpass -p {pw} ssh -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 {ssh_user_q}@{ssh_host} "
        f"{shlex.quote(inner)}"
    )
    text = _run_shell(cmd).strip()
    if not text or _is_ssh_noise(text):
        return {}
    return _parse_bulk_kwn_output(text)


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

    bulk: dict[int, dict[str, str]] = {}
    if read_field is None and ssh_password:
        bulk = ssh_read_kwn_sysfs_bulk(
            host=dut_ip,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            max_sua=max_sua,
        )

    def _read(path: str) -> str:
        if read_field is not None:
            return read_field(path)
        match = re.search(r"/sua(\d+)/statistics/([^/]+)$", path)
        if match and bulk:
            sua_idx = int(match.group(1))
            field = match.group(2)
            return bulk.get(sua_idx, {}).get(field, "-")
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
        if bulk and sua_idx in bulk:
            raw.update(bulk[sua_idx])
        else:
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


_OPERATING_MCS_FIELDS: tuple[str, ...] = (
    "rx_rate_mcs",
    "rx_rate",
    "tx_rate_mcs",
    "tx_rate",
    "ip",
    "ipv6",
    "mac",
)


def read_operating_mcs_by_sua_slot(
    dut_ip: str,
    *,
    ssh_user: str = DEFAULT_SSH_USER,
    ssh_password: str = "",
    max_sua: int = 16,
) -> dict[int, dict[str, str]]:
    """
    Operating MCS and PHY rate per BTS SUA slot from sysfs
    ``/sys/class/kwn/sua{N}/statistics/`` (``rx_rate_mcs`` is the CPE DL MCS index).
    """
    bulk: dict[int, dict[str, str]] = {}
    if ssh_password:
        bulk = ssh_read_kwn_sysfs_bulk(
            host=dut_ip,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            max_sua=max_sua,
        )

    slots: dict[int, dict[str, str]] = {}
    for sua_idx in range(1, max_sua + 1):
        base = f"/sys/class/kwn/sua{sua_idx}/statistics"
        row: dict[str, str] = {"sua_index": str(sua_idx)}
        if bulk and sua_idx in bulk:
            for key in _OPERATING_MCS_FIELDS:
                row[key] = str(bulk[sua_idx].get(key, "-"))
        else:
            for key in _OPERATING_MCS_FIELDS:
                row[key] = ssh_read_sysfs_field(
                    host=dut_ip,
                    path=f"{base}/{key}",
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
                )
        slots[sua_idx] = row
    return slots
