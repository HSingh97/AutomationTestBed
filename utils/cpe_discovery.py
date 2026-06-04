"""Discover CPE management addresses from BTS/CPE SSH (DHCP leases, neighbors, UCI)."""

from __future__ import annotations

import json
import re
import ipaddress
import shlex
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import normalize_ip
from utils.parsers import clean_ssh_output

IPV6_RE = re.compile(
    r"(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{1,4}",
    re.IGNORECASE,
)
IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b")

FACTORY_IPV4_EXCLUDE = frozenset({"0.0.0.0", "127.0.0.1", "10.0.0.1"})


async def _ssh_collect(ssh, commands: list[str]) -> str:
    chunks: list[str] = []
    for cmd in commands:
        if not cmd:
            continue
        try:
            result = await ssh.send_command(cmd, timeout_ops=30)
            chunks.append(clean_ssh_output(str(result.result or "")))
        except Exception as exc:
            chunks.append(str(exc))
    return "\n".join(chunks)


def parse_ipv6_from_text(text: str) -> list[str]:
    found: list[str] = []
    for ip in IPV6_RE.findall(text or ""):
        clean = normalize_ip(ip)
        if not clean or clean in found:
            continue
        # Filter out "IPv6-like" noise such as MAC addresses accidentally matched by the regex.
        try:
            ipaddress.IPv6Address(clean)
        except ValueError:
            continue
        if clean.startswith("fe80"):
            continue
        found.append(clean)
    return found


def is_valid_cpe_lan_ipv4(ip: str, *, factory_ipv4: str = "10.0.0.1") -> bool:
    clean = normalize_ip(str(ip).split("/")[0])
    if not clean:
        return False
    if clean in FACTORY_IPV4_EXCLUDE:
        return False
    if factory_ipv4 and clean == normalize_ip(factory_ipv4):
        return False
    try:
        addr = ipaddress.IPv4Address(clean)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_unspecified or addr.is_multicast)


def parse_ipv4_from_dhcp_leases(text: str, *, exclude: set[str] | None = None) -> list[str]:
    """Parse OpenWRT /tmp/dhcp.leases: '<expiry> <mac> <ip> <hostname> ...'."""
    skip = set(exclude or set())
    found: list[str] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and IPV4_RE.fullmatch(parts[2]):
            ip = normalize_ip(parts[2])
            if ip and ip not in skip and ip not in found:
                found.append(ip)
    return found


def parse_ipv4_from_neigh_arp(text: str, *, exclude: set[str] | None = None) -> list[str]:
    skip = set(exclude or set())
    found: list[str] = []
    for ip in IPV4_RE.findall(text or ""):
        clean = normalize_ip(ip)
        if clean and clean not in skip and clean not in found:
            found.append(clean)
    return found


async def read_cpe_lan_ipv4_via_secondary_hop(
    profile: dict[str, Any],
    password: str,
) -> str | None:
    """Read CPE network.lan.ipaddr via secondary PC → CPE factory SSH hop."""
    from utils.lab_pc_net import ensure_secondary_pc_cpe_hop_ready

    await ensure_secondary_pc_cpe_hop_ready(profile, password)
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        return None
    if "@" in ssh_target:
        sec_user, sec_host = ssh_target.split("@", 1)
    else:
        sec_user, sec_host = "root", ssh_target
    sec_pass = str(sec.get("password") or password).strip()
    factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))
    cpe_pass = str(password).strip()
    sec_conn = AsyncGenericDriver(
        host=sec_host.strip(),
        auth_username=(sec_user.strip() or "root"),
        auth_password=sec_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    try:
        await sec_conn.open()
        read_cmd = (
            "uci get network.lan.ipaddr 2>/dev/null || "
            "ucidyn get network.lan.ipaddr 2>/dev/null || "
            "ip -4 -o addr show dev br-lan 2>/dev/null | awk '{print $4}' | cut -d/ -f1"
        )
        hop = (
            f"sshpass -p {shlex.quote(cpe_pass)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "
            f"root@{shlex.quote(factory)} {shlex.quote(read_cmd)}"
        )
        result = await sec_conn.send_command(hop, timeout_ops=30)
        raw = clean_ssh_output(str(result.result or "")).strip().splitlines()
        ip = normalize_ip(raw[0].split("/")[0]) if raw else ""
        if ip and not ip.startswith("uci:") and is_valid_cpe_lan_ipv4(ip, factory_ipv4=factory):
            return ip
    finally:
        try:
            await sec_conn.close()
        except Exception:
            pass
    return None


async def discover_cpe_ipv4s_from_bts(
    bts_ssh,
    cfg: dict[str, Any],
    *,
    exclude_ipv4s: list[str] | None = None,
) -> list[str]:
    """Discover remote/CPE IPv4 addresses from BTS DHCP leases and neighbor tables."""
    disc = cfg.get("cpe_discovery", {}) or {}
    lease_cmds = list(
        disc.get("ipv4_lease_commands")
        or [
            "cat /tmp/dhcp.leases 2>/dev/null",
            "cat /tmp/hosts/dhcp 2>/dev/null",
            "ip -4 neigh show 2>/dev/null",
            "arp -an 2>/dev/null",
        ]
    )
    extra_cmds = list(disc.get("ipv4_discover_commands") or [])
    text = await _ssh_collect(bts_ssh, lease_cmds + extra_cmds)
    skip = {normalize_ip(str(x).split("/")[0]) for x in (exclude_ipv4s or []) if x}
    ips: list[str] = []
    for ip in parse_ipv4_from_dhcp_leases(text, exclude=skip):
        if is_valid_cpe_lan_ipv4(ip) and ip not in ips:
            ips.append(ip)
    for ip in parse_ipv4_from_neigh_arp(text, exclude=skip):
        if is_valid_cpe_lan_ipv4(ip) and ip not in ips:
            ips.append(ip)
    return ips


def _parse_ubus_ipv6_leases(text: str) -> list[str]:
    """Extract IPv6 addresses from `ubus call dhcp ipv6leases` JSON output."""
    found: list[str] = []
    try:
        # ubus may print JSON blob in output
        start = text.find("{")
        if start < 0:
            return found
        data = json.loads(text[start:])
        devices = data.get("device", []) if isinstance(data, dict) else []
        for dev in devices:
            for lease in dev.get("lease", []) or []:
                addr = lease.get("ipv6_addr") or lease.get("ip6addr") or lease.get("address")
                if addr:
                    found.append(normalize_ip(str(addr).split("/")[0]))
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return found


async def discover_cpe_ipv6s(
    bts_ssh,
    cfg: dict[str, Any],
    *,
    bts_mgmt_ipv6: str,
    exclude_ipv6s: list[str] | None = None,
) -> list[str]:
    """Discover CPE IPv6 from BTS using SSH only."""
    disc = cfg.get("cpe_discovery", {}) or {}
    ips: list[str] = []
    exclude = {normalize_ip(bts_mgmt_ipv6)}
    for item in exclude_ipv6s or []:
        if item:
            exclude.add(normalize_ip(item))

    lease_cmds = list(
        disc.get("lease_commands")
        or [
            "ubus call dhcp ipv6leases 2>/dev/null",
            "cat /tmp/dhcp.leases 2>/dev/null",
            "cat /tmp/hosts/* 2>/dev/null",
            "grep -r . /tmp/odhcp6d/ 2>/dev/null",
            "ip -6 neigh show 2>/dev/null",
        ]
    )
    extra_cmds = list(disc.get("discover_commands") or [])
    text = await _ssh_collect(bts_ssh, lease_cmds + extra_cmds)

    for ip in _parse_ubus_ipv6_leases(text):
        if ip not in ips:
            ips.append(ip)
    for ip in parse_ipv6_from_text(text):
        if ip not in ips:
            ips.append(ip)

    # Prefer addresses in same /64 prefix as BTS mgmt when multiple leases exist
    bts_prefix = ":".join(normalize_ip(bts_mgmt_ipv6).split(":")[:4]) if bts_mgmt_ipv6 else ""
    if bts_prefix:
        same_prefix = [ip for ip in ips if ip.startswith(bts_prefix) or bts_prefix in ip]
        if same_prefix:
            ips = same_prefix + [ip for ip in ips if ip not in same_prefix]

    return [ip for ip in ips if normalize_ip(ip) not in exclude]
