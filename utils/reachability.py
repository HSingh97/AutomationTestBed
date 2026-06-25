"""Reachability policy: strict mgmt IPv6 for devices; IPv4 only for recovery."""

from __future__ import annotations

from typing import Any

from utils.net_utils import is_ipv6_literal, normalize_ip


def _append_host(hosts: list[str], seen: set[str], raw: str) -> None:
    h = normalize_ip(str(raw or "").strip())
    if h and h not in seen:
        seen.add(h)
        hosts.append(h)


def is_strict_ipv6(profile: dict[str, Any]) -> bool:
    """When true, device SSH/GUI/tests use mgmt IPv6 only (no IPv4 fallbacks)."""
    dut = profile.get("dut", {}) or {}
    tb = profile.get("testbed", {}) or {}
    return bool(dut.get("strict_ipv6") or tb.get("strict_ipv6", dut.get("ip_mode") == "ipv6"))


def collect_recovery_fallback_hosts(
    profile: dict[str, Any],
    *,
    role: str = "bts",
    cli_fallback: str | None = None,
) -> list[str]:
    """
    IPv4 addresses used ONLY during link/device recovery (archive restore, factory LuCI).

    Never used for normal test execution when strict_ipv6 is enabled.
    """
    tb = profile.get("testbed", {}) or {}
    recovery = profile.get("recovery", {}) or {}
    rec_cfg = tb.get("recovery", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    hosts: list[str] = []
    seen: set[str] = set()

    for key in ("bts_fallback_ipv4", "bootstrap_fallback_ipv4"):
        val = rec_cfg.get(key) or tb.get(key)
        if val:
            _append_host(hosts, seen, str(val))

    for fb in tb.get("bootstrap_fallback_ipv4s") or rec_cfg.get("bootstrap_fallback_ipv4s") or []:
        _append_host(hosts, seen, str(fb))

    _append_host(hosts, seen, str(recovery.get("restore_default_ip", "")))

    if cli_fallback:
        _append_host(hosts, seen, str(cli_fallback))

    if role == "cpe":
        _append_host(hosts, seen, str(sec.get("cpe_factory_ipv4", "192.168.2.1")))

    return hosts


def collect_test_fallback_hosts(
    profile: dict[str, Any],
    primary_host: str,
    *,
    cli_fallback: str | None = None,
) -> list[str]:
    """
    Fallback hosts for test SSH when strict mode is off or IPv6 fallback is configured.
    Returns empty list when strict_ipv6 — tests must use mgmt IPv6 only.
    """
    if is_strict_ipv6(profile):
        ip_cfg = profile.get("ip_tests", {}) or {}
        if is_ipv6_literal(primary_host) and ip_cfg.get("fallback_ipv6"):
            return [normalize_ip(str(ip_cfg["fallback_ipv6"]))]
        return []

    cfg = profile.get("ip_tests", {}) or {}
    hosts: list[str] = []
    seen: set[str] = set()
    if cfg.get("fallback_ipv4"):
        _append_host(hosts, seen, str(cfg["fallback_ipv4"]))
    if cli_fallback:
        _append_host(hosts, seen, str(cli_fallback))
    if is_ipv6_literal(primary_host) and cfg.get("fallback_ipv6"):
        _append_host(hosts, seen, str(cfg["fallback_ipv6"]))
    return hosts


# Backward-compatible alias (recovery-only callers should use collect_recovery_fallback_hosts)
def collect_ssh_fallback_hosts(
    profile: dict[str, Any],
    *,
    testbed: dict[str, Any] | None = None,
    cli_fallback: str | None = None,
    primary_host: str = "",
    role: str = "bts",
    recovery_only: bool = True,
) -> list[str]:
    if recovery_only or not is_strict_ipv6(profile):
        return collect_recovery_fallback_hosts(profile, role=role, cli_fallback=cli_fallback)
    return collect_test_fallback_hosts(profile, primary_host, cli_fallback=cli_fallback)
