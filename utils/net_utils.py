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

