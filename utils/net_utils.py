"""Network/IP formatting helpers for IPv4/IPv6-safe URLs and shell commands."""

from __future__ import annotations

import ipaddress

from utils.parsers import extract_ip_objects

UNREACHABLE_IPV4 = "192.0.0.1"
# Non-routable target for unreachable ping/traceroute on IPv6-only stacks.
# Valid ULA form; typically unrouted from the DUT (unlike overlong literals rejected by ping).
UNREACHABLE_IPV6 = "fd00:dead:beef::1"


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
    """Mgmt IP for testbed tables — host only (Unit column already has BTS/SU).

    ``label`` / ``prefix_len`` are kept for call-site compatibility; they are not
    shown (avoids ``SU1192.168.2.32/120``-style glue).
    """
    _ = label, prefix_len
    raw = str(ipv6 or "").strip()
    lower = raw.lower()
    if (
        not raw
        or raw in {"—", "-", "N/A", "n/a"}
        or "uci:" in lower
        or "entry not found" in lower
        or "sshpass" in lower
        or "/bin/sh:" in lower
    ):
        return "—"
    host = normalize_ip(raw.split("/")[0])
    if not host or host in {"—", "-"}:
        return "—"
    return host


def format_luci_url(ip_text: str, *, scheme: str = "https") -> str:
    """LuCI base URL with bracketed IPv6 host when needed."""
    return f"{scheme}://{format_http_host(ip_text)}/cgi-bin/luci/"


def ips_equal(left: str, right: str) -> bool:
    """Compare two IP strings (handles IPv6 compression differences)."""
    left_n = normalize_ip(left)
    right_n = normalize_ip(right)
    if not left_n or not right_n:
        return False
    try:
        return ipaddress.ip_address(left_n) == ipaddress.ip_address(right_n)
    except ValueError:
        return left_n.lower() == right_n.lower()


def ip_in_text(ip_text: str, haystack: str) -> bool:
    """True when ip_text appears in haystack (substring or parsed IP objects)."""
    if not ip_text or not haystack:
        return False
    target = normalize_ip(ip_text)
    if target in haystack or normalize_ip(haystack) == target:
        return True
    try:
        needle = ipaddress.ip_address(target)
    except ValueError:
        return target in haystack
    return needle in extract_ip_objects(haystack)


def unreachable_ping_target(reachable_target: str) -> str:
    """Pick an unreachable diagnostic target for the active address family."""
    if reachable_target and is_ipv6_literal(reachable_target):
        return UNREACHABLE_IPV6
    return UNREACHABLE_IPV4


def prefer_ipv6_for_cpe(cpe_ip: str | None, *, strict_ipv6: bool = False) -> bool:
    """Whether monitor flows should treat link/CPE identity as IPv6-first."""
    if strict_ipv6:
        return True
    return bool(cpe_ip and is_ipv6_literal(cpe_ip))

