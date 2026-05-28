"""Collect BTS/CPE model, firmware, IP, VLAN, and QoS for regression report header."""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from pages.commands import RootCommands
from scrapli.driver.generic import AsyncGenericDriver
from utils.net_utils import format_snmp_host
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
        model = _sanitize_model(model_raw)
        fw_version = _sanitize_fw(fw_raw)
        ip = ssh_scalar((await ssh.send_command(RootCommands.GET_IPv6)).result) or fallback_ip or host
        vlan = await asyncio.to_thread(fetch_vlan_label, host)
        qos = await _fetch_qos_label(ssh, host)
        return DeviceSummary(
            model=model,
            fw_version=fw_version,
            ip=ip,
            vlan=vlan or "—",
            qos=qos or "—",
        )
    finally:
        await _close_ssh(ssh)


async def collect_testbed_summary(
    bts_host: str,
    cpe_hosts: list[str],
    password: str,
) -> dict[str, Any]:
    cpe_host = cpe_hosts[0] if cpe_hosts else ""
    bts = await collect_device_summary(bts_host, password, fallback_ip=bts_host)
    if cpe_host:
        try:
            cpe = await collect_device_summary(cpe_host, password, fallback_ip=cpe_host)
        except Exception as exc:
            print(f"[testbed] CPE summary unavailable ({cpe_host}): {exc}")
            cpe = DeviceSummary(ip=cpe_host)
    else:
        cpe = DeviceSummary(ip="—")
    return {"bts": bts.as_dict(), "cpe": cpe.as_dict()}
