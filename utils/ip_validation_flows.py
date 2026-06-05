"""Thin runners for tests/IP (same pattern as utils/monitor_flows.py)."""

from __future__ import annotations

from typing import Any

import pytest

from config.ip_test_cases import case_by_id
from utils.ip_test_flows import (
    IpTestContext,
    _cpe_ipv4_hint_candidates,
    _factory_first_ssh_hosts,
    _ip_cfg,
    _ipv4_test_address_candidates,
    _ordered_unique_hosts,
    _wait_ssh_any,
    execute_ip_case_with_recovery,
    open_cpe_ssh_via_secondary_pc,
    open_ssh_with_fallback,
    resolve_ip_peer_host,
)
from utils.net_utils import normalize_ip
from utils.regression_flows import _close_ssh


def require_ip_suite(request, profile_bundle) -> None:
    if not request.config.getoption("--allow-ip-suite"):
        pytest.skip("IP suite skipped. Re-run with --allow-ip-suite")
    ip_cfg = profile_bundle.active.get("ip_tests", {}) or {}
    if not ip_cfg.get("enabled", False):
        pytest.skip("ip_tests.enabled is false in profile")


def case_needs_gui(case_id: str, target: str) -> bool:
    case = case_by_id(case_id)
    return target == "bts" and (
        "gui" in case.requires or case_id in ("IP_15", "IP_34", "IP_35")
    )


async def open_ip_ssh_session(
    *,
    case_id: str,
    target: str,
    request,
    profile_bundle,
    bsu_ip: str,
    cpe_ips: list[str],
    device_creds: dict[str, str],
):
    """Return (ssh, host, fallbacks) for one IP case target."""
    cfg = _ip_cfg(profile_bundle.active)
    dut = profile_bundle.active.get("dut", {}) or {}
    tb = profile_bundle.active.get("testbed", {}) or {}
    primary = bsu_ip if target == "bts" else ""

    ipv6_mode = dut.get("ip_mode") == "ipv6" or tb.get("strict_ipv6")
    if ipv6_mode:
        cfg["_strict_ipv6"] = True
        cfg["ssh_allow_ipv4_fallback"] = True
        cfg["_profile"] = profile_bundle.active
        rec = tb.get("recovery", {}) or {}
        cli_fb = (
            request.config.getoption("--fallback-ip")
            or rec.get("bts_fallback_ipv4")
            or dut.get("local_ip")
            or "10.0.0.1"
        )
        cfg["_cli_fallback_ip"] = normalize_ip(str(cli_fb).split("/")[0])
        cfg["_device_target"] = target

        if target == "cpe":
            sec = tb.get("secondary_pc", {}) or {}
            factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))
            mgmt = tb.get("mgmt_vlan", {}) or {}
            cpe_mgmt = normalize_ip(str(cpe_ips[0]).split("/")[0]) if cpe_ips else ""
            if not cpe_mgmt:
                cpe_mgmt = normalize_ip(str(mgmt.get("ipv6_cpe", "")).split("/")[0])
            try:
                ssh, effective_host, _ = await open_cpe_ssh_via_secondary_pc(
                    profile_bundle.active,
                    device_creds["pass"],
                    cpe_lan=cpe_mgmt or factory,
                    cpe_factory=factory,
                )
                return ssh, effective_host, (factory, cpe_mgmt or factory)
            except Exception as exc:
                if case_id in {
                    "IP_18", "IP_19", "IP_20", "IP_21", "IP_22", "IP_23",
                    "IP_24", "IP_25", "IP_26",
                    "IP_28", "IP_30", "IP_31", "IP_32", "IP_33", "IP_34", "IP_35",
                }:
                    pytest.fail(f"CPE SSH via secondary PC failed: {exc}")
                pytest.skip(f"CPE SSH via secondary PC unavailable: {exc}")

        mgmt = tb.get("mgmt_vlan", {}) or {}
        hosts = _factory_first_ssh_hosts(
            _ordered_unique_hosts(
                cfg["_cli_fallback_ip"],
                str(rec.get("bts_fallback_ipv4", "")),
                str(dut.get("local_ip", "")),
                normalize_ip(str(bsu_ip).split("/")[0]),
                str(mgmt.get("ipv6_bts", dut.get("local_ipv6", ""))),
            )
        )
        ssh, effective_host = await _wait_ssh_any(
            hosts,
            device_creds["pass"],
            timeout_s=90,
            interval_s=3,
        )
        fallbacks = tuple(h for h in hosts if normalize_ip(h) != normalize_ip(effective_host))
        return ssh, effective_host, fallbacks

    if target == "cpe":
        hints = _cpe_ipv4_hint_candidates(cfg, profile_bundle.active)
        cpe_lan = ""
        try:
            from utils.cpe_discovery import read_cpe_lan_ipv4_via_secondary_hop

            live = await read_cpe_lan_ipv4_via_secondary_hop(
                profile_bundle.active, device_creds["pass"]
            )
            if live:
                cpe_lan = live
        except Exception:
            pass
        if not cpe_lan:
            cpe_lan = str(
                cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")
            ).strip()
        if not cpe_lan and hints:
            cpe_lan = hints[0]
        sec = tb.get("secondary_pc", {}) or {}
        factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))
        primary = normalize_ip(cpe_lan.split("/")[0]) if cpe_lan else factory
        try:
            ssh, effective_host, _ = await open_cpe_ssh_via_secondary_pc(
                profile_bundle.active,
                device_creds["pass"],
                cpe_lan=primary,
                cpe_factory=factory,
            )
            return ssh, effective_host, (factory, primary)
        except Exception as exc:
            if case_id in {
                "IP_06", "IP_07", "IP_08", "IP_09", "IP_11", "IP_12",
                "IP_13", "IP_14", "IP_15", "IP_16", "IP_17",
                "IP_18", "IP_19", "IP_20", "IP_21", "IP_22", "IP_23",
                "IP_24", "IP_25", "IP_26",
                "IP_28", "IP_30", "IP_31", "IP_32", "IP_33", "IP_34", "IP_35",
            }:
                pytest.fail(f"CPE SSH via secondary PC failed: {exc}")
            pytest.skip(f"CPE SSH via secondary PC unavailable: {exc}")

    if target == "bts" and case_id in {
        "IP_02", "IP_04", "IP_05", "IP_06", "IP_07", "IP_08",
        "IP_09", "IP_11", "IP_12", "IP_13", "IP_14", "IP_15", "IP_16", "IP_17",
    }:
        lan_hosts = list(reversed(_ipv4_test_address_candidates(cfg)))
        default_lan = str(cfg.get("ipv4_address", "")).split("/")[0].strip()
        if default_lan:
            lan_hosts.append(normalize_ip(default_lan))
        cli_fb = request.config.getoption("--fallback-ip") or bsu_ip
        ssh, effective_host = await _wait_ssh_any(
            [normalize_ip(h) for h in lan_hosts if h] + [normalize_ip(cli_fb)],
            device_creds["pass"],
            timeout_s=90,
            interval_s=3,
        )
        fallbacks = tuple(
            h
            for h in lan_hosts + [cli_fb]
            if normalize_ip(str(h)) != normalize_ip(effective_host)
        )
        return ssh, effective_host, fallbacks

    if target == "bts" and case_id not in ("IP_01", "IP_03"):
        configured = str(cfg.get("ipv4_address", "")).split("/")[0].strip()
        if configured:
            primary = normalize_ip(configured)
    if target == "bts" and case_id in ("IP_01", "IP_03"):
        rec = tb.get("recovery", {}) or {}
        fb = str(
            cfg.get("fallback_ipv4") or rec.get("bts_fallback_ipv4") or dut.get("local_ip", "")
        ).strip()
        cli_fb = request.config.getoption("--fallback-ip")
        primary = normalize_ip(cli_fb or fb or bsu_ip)

    if not primary and target == "bts":
        primary = bsu_ip
    if not primary:
        pytest.skip(f"no host for target {target}")

    cfg["_strict_ipv6"] = bool(dut.get("strict_ipv6") or tb.get("strict_ipv6"))
    cfg["_cli_fallback_ip"] = (
        None if cfg["_strict_ipv6"] else request.config.getoption("--fallback-ip")
    )
    cfg["_device_target"] = target
    ssh, effective_host, fallbacks = await open_ssh_with_fallback(
        primary,
        device_creds["pass"],
        cfg,
        attempts=int(cfg.get("ssh_connect_attempts", 3)),
        retry_interval_s=int(cfg.get("ssh_connect_retry_interval_s", 15)),
    )
    return ssh, effective_host, fallbacks


async def run_ip_validation(
    case_id: str,
    target: str,
    *,
    request,
    profile_bundle,
    bsu_ip: str,
    cpe_ips: list[str],
    device_creds: dict[str, str],
    gui_page: Any | None = None,
) -> None:
    """Build context, run case + recovery (used by tests/IP/test_IP.py)."""
    require_ip_suite(request, profile_bundle)
    case = case_by_id(case_id)
    if "destructive" in case.requires and not request.config.getoption(
        "--allow-ip-destructive"
    ):
        pytest.skip(f"{case_id}: add --allow-ip-destructive")

    cfg = _ip_cfg(profile_bundle.active)
    dut = profile_bundle.active.get("dut", {}) or {}
    tb = profile_bundle.active.get("testbed", {}) or {}
    cfg["_strict_ipv6"] = bool(dut.get("strict_ipv6") or tb.get("strict_ipv6"))
    cfg["ssh_allow_ipv4_fallback"] = True
    cfg["_password"] = device_creds["pass"]
    cfg["_device_creds"] = device_creds
    rec = tb.get("recovery", {}) or {}
    if cfg["_strict_ipv6"]:
        cfg["_cli_fallback_ip"] = normalize_ip(
            str(
                request.config.getoption("--fallback-ip")
                or rec.get("bts_fallback_ipv4")
                or dut.get("local_ip")
                or "10.0.0.1"
            ).split("/")[0]
        )
    else:
        cfg["_cli_fallback_ip"] = request.config.getoption("--fallback-ip")
    cfg["_device_target"] = target
    cfg["_profile"] = profile_bundle.active
    cfg["_profile_bundle"] = profile_bundle
    if not hasattr(request.config, "_ip_suite_chain"):
        request.config._ip_suite_chain = {"ok": True}
    cfg["_ip_suite_chain"] = request.config._ip_suite_chain

    ssh, host, fallbacks = await open_ip_ssh_session(
        case_id=case_id,
        target=target,
        request=request,
        profile_bundle=profile_bundle,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
    )
    try:
        peer = resolve_ip_peer_host(
            device_target=target,
            bsu_ip=bsu_ip,
            cpe_ips=cpe_ips,
            profile=profile_bundle.active,
            cfg=cfg,
        )
        ctx = IpTestContext(
            case=case,
            device_target=target,
            host=host,
            peer_host=peer,
            ssh=ssh,
            gui_page=gui_page if case_needs_gui(case_id, target) else None,
            cfg=cfg,
            stack_mode=str(dut.get("ip_mode", "ipv4")),
            fallback_hosts=fallbacks,
        )
        await execute_ip_case_with_recovery(ctx)
    finally:
        await _close_ssh(ssh)
