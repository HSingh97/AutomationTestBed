"""Discover CPE management IPv6 from BTS over SSH (DHCP/odhcpd leases, neighbors)."""

from __future__ import annotations

import json
import re
import ipaddress
from typing import Any

from utils.net_utils import normalize_ip
from utils.parsers import clean_ssh_output

IPV6_RE = re.compile(
    r"(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{1,4}",
    re.IGNORECASE,
)


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
