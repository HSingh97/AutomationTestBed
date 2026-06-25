"""Network/IP formatting helpers for IPv4/IPv6-safe URLs and shell commands."""

from __future__ import annotations

import ipaddress


def normalize_ip(ip_text: str) -> str:
    ip_text = (ip_text or "").strip()
    if ip_text.startswith("[") and ip_text.endswith("]"):
        return ip_text[1:-1]
    return ip_text


def is_ipv6_literal(ip_text: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(normalize_ip(ip_text)), ipaddress.IPv6Address)
    except ValueError:
        return False


def format_http_host(ip_text: str) -> str:
    clean = normalize_ip(ip_text)
    return f"[{clean}]" if is_ipv6_literal(clean) else clean


def format_ssh_host(ip_text: str) -> str:
    clean = normalize_ip(ip_text)
    return f"[{clean}]" if is_ipv6_literal(clean) else clean


def format_snmp_host(ip_text: str) -> str:
    clean = normalize_ip(ip_text)
    return f"udp6:[{clean}]" if is_ipv6_literal(clean) else clean


def format_mgmt_ipv6_display(label: str, ipv6: str, *, prefix_len: int = 120) -> str:
    """Compact testbed summary IP, e.g. ``BTS2001:...:1111/120``."""
    raw = str(ipv6 or "").strip()
    if not raw or raw in {"—", "-", "N/A", "n/a"}:
        return "—"
    host = normalize_ip(raw.split("/")[0])
    plen = prefix_len
    if "/" in raw:
        try:
            plen = int(raw.split("/", 1)[1])
        except ValueError:
            pass
    tag = str(label or "").strip().upper() or "NODE"
    return f"{tag}{host}/{plen}"

