"""Collect BTS/CPE model, firmware, IP, VLAN, and QoS for regression report header."""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from pages.commands import RootCommands
from scrapli.driver.generic import AsyncGenericDriver
from traffic.kwn_sua_statistics import read_operating_mcs_by_sua_slot, resolve_sua_display_ip
from utils.net_utils import format_snmp_host, normalize_ip
from utils.parsers import clean_ssh_output, ssh_scalar

SNMP_COMMUNITY = "ubr@rw123"
SNMP_RADIO_IDX = "2"
OID_VLAN_STATUS_SCALAR = ".1.3.6.1.4.1.52619.1.1.4.1.0"
OID_VLAN_MODE_SCALAR = ".1.3.6.1.4.1.52619.1.1.4.2.0"
VLAN_MODE_MAP = {"0": "Transparent", "1": "Access", "2": "Trunk", "3": "QinQ"}

_MODEL_TOKEN_RE = re.compile(r"\b(?:UBR|EOC|ADE)[A-Za-z0-9._-]*\b", re.IGNORECASE)
_FW_TOKEN_RE = re.compile(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b")


@dataclass
class DeviceSummary:
    model: str = "—"
    fw_version: str = "—"
    ip: str = "—"
    vlan: str = "—"
    qos: str = "—"

    def as_dict(self) -> dict[str, str]:
        return {
            "model": self.model,
            "fw_version": self.fw_version,
            "ip": self.ip,
            "vlan": self.vlan,
            "qos": self.qos,
        }


def _run_shell(command: str) -> str:
    try:
        return subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, timeout=12).decode(
            "utf-8", errors="replace"
        ).strip()
    except Exception:
        return ""


def _parse_snmp_value(output: str) -> str:
    if "No Such" in output or not output:
        return ""
    if "STRING:" in output:
        return output.split("STRING:", 1)[1].strip().strip('"')
    if ":" in output:
        return output.split(":", 1)[-1].strip().strip('"')
    return output.strip().strip('"')


def _snmp_scalar(host: str, oid: str) -> str:
    snmp_host = format_snmp_host(host)
    cmd = f"snmpget -v 2c -c {SNMP_COMMUNITY} {snmp_host} {oid}"
    return _parse_snmp_value(_run_shell(cmd))


def fetch_vlan_label(host: str) -> str:
    status = _snmp_scalar(host, OID_VLAN_STATUS_SCALAR)
    if not status:
        return "—"
    enabled = "Enabled" if status == "1" else "Disabled"
    mode_raw = _snmp_scalar(host, OID_VLAN_MODE_SCALAR)
    mode = VLAN_MODE_MAP.get(mode_raw, mode_raw or "—")
    if enabled == "Disabled":
        return enabled
    return f"{enabled} ( {mode} )"


async def _open_ssh(host: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _close_ssh(conn: AsyncGenericDriver | None) -> None:
    if conn is None:
        return
    try:
        await conn.close()
    except Exception:
        pass


async def _ssh_cmd(ssh: AsyncGenericDriver, command: str) -> str:
    result = await ssh.send_command(command)
    return clean_ssh_output(str(result.result or ""))


def _sanitize_model(raw_output: str) -> str:
    """
    Extract stable model token from noisy shell output.
    Prevents banner/ascii junk from leaking into report headers.
    """
    text = str(raw_output or "").replace("\r", "\n")
    for token in _MODEL_TOKEN_RE.findall(text):
        if token:
            return token.upper()
    scalar = ssh_scalar(raw_output).strip()
    if scalar and re.fullmatch(r"[A-Za-z0-9._-]{3,40}", scalar):
        return scalar
    return "—"


def _parse_ademodel(raw_output: str) -> str:
    """Return ``/etc/ademodel`` content for report headers."""
    scalar = ssh_scalar(raw_output).strip()
    if scalar and scalar not in {"-", "—", "UNKNOWN", "unknown", "n/a", "N/A"}:
        if re.fullmatch(r"[A-Za-z0-9._-]{3,40}", scalar):
            return scalar
    return _sanitize_model(raw_output)


def _sanitize_fw(raw_output: str) -> str:
    text = str(raw_output or "")
    match = _FW_TOKEN_RE.search(text)
    if match:
        return match.group(0)
    scalar = ssh_scalar(raw_output).strip()
    return scalar if scalar else "—"


async def _fetch_qos_label(ssh: AsyncGenericDriver, host: str) -> str:
    """Best-effort QoS label via UCI, with SNMP/CLI fallback."""
    qos_uci = await _ssh_cmd(ssh, "uci show ath1qos 2>/dev/null")
    if qos_uci:
        if re.search(r"policy|policies", qos_uci, flags=re.IGNORECASE):
            if re.search(r"=.*policy", qos_uci, flags=re.IGNORECASE) or qos_uci.count("cfg") > 3:
                return "Policies Applied"
        if re.search(r"['\"]?all['\"]?", qos_uci, flags=re.IGNORECASE):
            return "All"
        if "auto" in qos_uci.lower():
            return "Auto"

    # Some builds expose a simple QoS mode scalar.
    for cmd in (
        "uci get ath1qos.qoscfg.qosmode 2>/dev/null",
        "uci get ath1qos.qoscfg.mode 2>/dev/null",
        "uci get qos.@global[0].mode 2>/dev/null",
    ):
        value = ssh_scalar((await ssh.send_command(cmd)).result)
        if value:
            return value.replace("_", " ").title()

    _ = host
    return "—"


async def collect_device_summary(host: str, password: str, *, fallback_ip: str = "") -> DeviceSummary:
    ssh = await _open_ssh(host, password)
    try:
        model_raw = str((await ssh.send_command(RootCommands.GET_MODEL)).result or "")
        fw_raw = str((await ssh.send_command(RootCommands.GET_SW_VERSION)).result or "")
        model = _parse_ademodel(model_raw)
        fw_version = _sanitize_fw(fw_raw)
        ip = ssh_scalar((await ssh.send_command(RootCommands.GET_IPv6)).result) or ""
        ip_lower = ip.lower()
        if (
            not ip
            or "uci:" in ip_lower
            or "entry not found" in ip_lower
            or ip_lower in {"none", "n/a", "unknown", "-"}
        ):
            ip = str(fallback_ip or host or "").strip()
        if ip and ("uci:" in ip.lower() or "entry not found" in ip.lower()):
            ip = "—"
        vlan = await asyncio.to_thread(fetch_vlan_label, host)
        qos = await _fetch_qos_label(ssh, host)
        return DeviceSummary(
            model=model,
            fw_version=fw_version,
            ip=ip or "—",
            vlan=vlan or "—",
            qos=qos or "—",
        )
    finally:
        await _close_ssh(ssh)


def fetch_su_ipv6_from_bts_sysfs(
    bts_ssh_ip: str,
    password: str,
    *,
    su_count: int,
    ssh_user: str = "root",
) -> list[str]:
    """Live SU mgmt IPv6 from BTS ``/sys/class/kwn/sua{N}/statistics/ipv6``."""
    slots = read_operating_mcs_by_sua_slot(
        bts_ssh_ip,
        ssh_user=ssh_user,
        ssh_password=password,
        max_sua=max(su_count, 1),
    )
    hosts: list[str] = []
    for su_index in range(1, su_count + 1):
        slot = slots.get(su_index, {})
        ip = resolve_sua_display_ip(
            ipv4=str(slot.get("ip") or ""),
            ipv6=str(slot.get("ipv6") or ""),
        )
        hosts.append(normalize_ip(ip) if ip and ip != "-" else "")
    return hosts


async def collect_testbed_summary(
    bts_host: str,
    cpe_hosts: list[str],
    password: str,
    *,
    bts_ssh_host: str | None = None,
    ipv6_prefix_len: int = 120,
    su_count: int | None = None,
) -> dict[str, Any]:
    profile_hosts = [str(h).strip() for h in cpe_hosts if str(h).strip()]
    sysfs_hosts: list[str] = []
    effective_count = su_count or len(profile_hosts) or 1
    if bts_ssh_host and password:
        try:
            sysfs_hosts = await asyncio.to_thread(
                fetch_su_ipv6_from_bts_sysfs,
                bts_ssh_host,
                password,
                su_count=effective_count,
            )
            if any(sysfs_hosts):
                print(
                    f"[testbed] SU IPv6 from BTS sysfs: "
                    f"{sum(1 for ip in sysfs_hosts if ip)}/{effective_count}"
                )
        except Exception as exc:
            print(f"[testbed] WARN sysfs SU IPv6 read failed: {exc}")

    merged_hosts: list[str] = []
    for index in range(effective_count):
        sysfs_ip = sysfs_hosts[index] if index < len(sysfs_hosts) else ""
        profile_ip = profile_hosts[index] if index < len(profile_hosts) else ""
        merged_hosts.append(sysfs_ip or profile_ip)

    bts_ssh_target = str(bts_ssh_host or bts_host).strip()
    bts = await collect_device_summary(bts_ssh_target, password, fallback_ip=bts_host)

    cpe_entries: list[dict[str, Any]] = []
    if merged_hosts:
        async def _summary_for_host(host: str) -> DeviceSummary | Exception:
            if not host:
                return DeviceSummary(ip="—")
            try:
                return await collect_device_summary(host, password, fallback_ip=host)
            except Exception as exc:
                return exc

        results = await asyncio.gather(
            *[_summary_for_host(host) for host in merged_hosts],
        )
        for index, (host, result) in enumerate(zip(merged_hosts, results), start=1):
            sysfs_ip = sysfs_hosts[index - 1] if index - 1 < len(sysfs_hosts) else ""
            if isinstance(result, Exception):
                print(f"[testbed] SU{index} summary unavailable ({host or 'no-ip'}): {result}")
                device = DeviceSummary(ip=sysfs_ip or host or "—")
            else:
                device = result
                if sysfs_ip:
                    device.ip = sysfs_ip
            cpe_entries.append(
                {
                    "label": f"SU{index}",
                    "su_index": index,
                    **device.as_dict(),
                }
            )

    legacy_cpe = cpe_entries[0] if cpe_entries else DeviceSummary(ip="—").as_dict()
    legacy_cpe = {
        key: value
        for key, value in legacy_cpe.items()
        if key not in {"label", "su_index"}
    }
    return {
        "bts": bts.as_dict(),
        "cpe": legacy_cpe,
        "cpes": cpe_entries,
        "su_count": len(cpe_entries),
        "ipv6_prefix_len": ipv6_prefix_len,
    }
