"""Resolve which BTS management address is reachable (IPv6 first, then fallback)."""

from __future__ import annotations

import asyncio
import time
import subprocess

from config.defaults import SANITY_TEST_VALUES, TESTBED_DEFAULTS
from utils.cpe_api_24_lab import bts_ssh_host_candidates
from utils.lab_pc_net import (
    ensure_fallback_ethernet,
    ensure_pc_interface_up,
    ensure_pc_ipv4_on_interface,
)
from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.sanity_network_config import derive_sanity_alt_ipv6
from utils.sanity_recovery import (
    bts_factory_ipv4,
    ensure_bts_pc_factory_lan,
    profile_baseline_snapshots,
    sanity_03_restore_link,
    sanity_preflight_restore_if_factory,
)
from utils.sanity_ssh import (
    close_sanity_ssh,
    open_sanity_ssh,
    sanitize_sanity_scalar,
    wait_sanity_ssh,
)


async def clean_bts_mgmt_vlan(
    fallback_ip: str,
    password: str,
    *,
    settle_s: int = 10,
    timeout_s: int = 25,
) -> bool:
    """
    No-VLAN sanity: set BTS mgmtvlan=1 without a full network reload when possible.

    Uses wifi reload only so the lab PC LAN path stays up.
    """
    fallback_ip = normalize_ip(fallback_ip)
    if not fallback_ip:
        return False
    try:
        conn = await open_sanity_ssh(
            fallback_ip, password, label="BTS-clean", timeout_s=timeout_s
        )
    except Exception:  # noqa: BLE001
        return False

    try:
        cur = sanitize_sanity_scalar(
            (await conn.send_command("uci -q get vlan.ath1.mgmtvlan")).result
        )
        if cur == "1":
            return True

        for cmd in ("uci set vlan.ath1.mgmtvlan='1'", "uci commit vlan"):
            await conn.send_command(cmd, timeout_ops=timeout_s)
        await conn.send_command(
            "wifi reload >/dev/null 2>&1; echo RELOAD_DONE",
            timeout_ops=max(timeout_s, 30),
        )
    except Exception:  # noqa: BLE001
        await close_sanity_ssh(conn)
        return False

    await close_sanity_ssh(conn)
    if settle_s > 0:
        await asyncio.sleep(settle_s)
    return True


def _bts_fallback_candidates(request, profile_bundle, bts_v6: str) -> list[str]:
    tb = profile_bundle.active.get("testbed", {}) or {}
    rec = (tb.get("recovery", {}) or {})
    dut = profile_bundle.active.get("dut", {}) or {}
    values = SANITY_TEST_VALUES
    alt_v6 = ""
    if bts_v6 and is_ipv6_literal(bts_v6):
        try:
            alt_v6 = derive_sanity_alt_ipv6(f"{bts_v6}/120", role="BTS").split("/")[0]
        except Exception:
            alt_v6 = ""
    extras = [
        request.config.getoption("--fallback-ip") or "",
        alt_v6,
        rec.get("bts_fallback_ipv4") or "",
        dut.get("local_ip") or "",
        "10.0.0.1",
        bts_factory_ipv4(values),
    ]
    return bts_ssh_host_candidates(bts_v6, extra_hosts=extras)


def _bts_ethernet_pc_cfg(profile: dict) -> dict:
    """Lab PC NIC toward BTS RF path (enp3s0) — not enp1s0 factory LAN."""
    tb = profile.get("testbed", {}) or {}
    primary = dict(tb.get("primary_pc", {}) or {})
    defaults = dict(TESTBED_DEFAULTS.get("primary_pc", {}) or {})
    iface = str(
        primary.get("bts_mgmt_interface")
        or defaults.get("mgmt_interface")
        or "enp3s0"
    )
    fb = str(
        primary.get("bts_fallback_ipv4")
        or defaults.get("fallback_ipv4")
        or "10.0.0.10"
    ).split("/")[0]
    prefix = int(
        primary.get("bts_fallback_prefix_len")
        or defaults.get("fallback_prefix_len")
        or 8
    )
    return {
        **primary,
        "local": primary.get("local", True),
        "mgmt_interface": iface,
        "fallback_ipv4": fb,
        "fallback_prefix_len": prefix,
    }


async def _prepare_pc_for_bts_host(profile: dict, password: str, host: str) -> None:
    """Add lab PC IPv4 routes needed to reach BTS — never flush IPv6 or drop parent LAN."""
    tb = profile.get("testbed", {}) or {}
    primary = dict(tb.get("primary_pc", {}) or {})
    if not primary:
        return
    factory = bts_factory_ipv4(SANITY_TEST_VALUES)
    host = normalize_ip(host)
    if host.startswith("10.0.0."):
        # BTS fallback 10.0.0.1 lives on enp3s0 — not primary_pc enp1s0 (192.168.2.x LAN).
        bts_pc = _bts_ethernet_pc_cfg(profile)
        await ensure_fallback_ethernet(bts_pc, password)
        return
    await ensure_pc_interface_up(primary, password)
    if host == factory:
        await ensure_bts_pc_factory_lan(profile, password)
        return
    if host.startswith("192.168.2."):
        await ensure_pc_ipv4_on_interface(primary, password, cidr="192.168.2.200/24")


def _host_reachable(host: str, *, timeout_s: int = 2) -> bool:
    host = normalize_ip(host)
    if not host:
        return False
    if is_ipv6_literal(host):
        cmd = ["ping6", "-c", "1", "-W", str(max(1, timeout_s)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_s)), host]
    try:
        return subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    except FileNotFoundError:
        return False


async def _ssh_reachable(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    timeout_s: int = 20,
) -> bool:
    try:
        conn = await open_sanity_ssh(
            host,
            password,
            source_v6=source_v6 if is_ipv6_literal(host) else "",
            label="BTS-probe",
            timeout_s=timeout_s,
        )
        await close_sanity_ssh(conn)
        return True
    except Exception:
        return False


async def _wait_preferred_bts_host(
    *,
    bts_v6: str,
    password: str,
    source_v6: str,
    profile: dict,
    candidates: list[str],
    timeout_s: int = 240,
    poll_s: int = 10,
) -> tuple[str, str, str]:
    """After factory restore, wait for profile mgmt IPv6 or known IPv4."""
    preferred: list[str] = []
    for raw in (bts_v6, *candidates):
        host = normalize_ip(raw)
        if host and host not in preferred and host != bts_factory_ipv4(SANITY_TEST_VALUES):
            preferred.append(host)

    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        for host in preferred:
            bind = source_v6 if is_ipv6_literal(host) else ""
            try:
                conn = await wait_sanity_ssh(
                    host,
                    password,
                    source_v6=bind,
                    label="BTS post-restore",
                    timeout_s=min(30, timeout_s),
                    poll_s=poll_s,
                )
                await close_sanity_ssh(conn)
                note = "ipv6" if is_ipv6_literal(host) else f"restored {host}"
                print(f"[sanity] BTS management after restore: {host}", flush=True)
                return host, bind, note
            except Exception as exc:
                last_err = str(exc)
        await asyncio.sleep(max(1, poll_s))
    raise RuntimeError(
        f"[sanity] BTS not reachable after factory restore on {preferred}: {last_err}"
    )


async def resolve_bts_mgmt_host(
    request,
    profile_bundle,
    device_creds: dict,
) -> tuple[str, str, str]:
    """
    Return (bts_host, source_v6_bind, note).

    Prefer CLI IPv6; then profile/recovery/factory IPv4 chain. If BTS is stuck on
    factory LAN after a prior without-keep run, restore profile config first.
    """
    bts_v6, cpe_v6, source_v6 = _ipv6_endpoints_from_profile(profile_bundle, request)
    password = device_creds["pass"]
    profile = profile_bundle.active
    values = SANITY_TEST_VALUES
    factory_bts = bts_factory_ipv4(values)
    candidates = _bts_fallback_candidates(request, profile_bundle, bts_v6)

    if bts_v6 and is_ipv6_literal(bts_v6):
        if _host_reachable(bts_v6) or await _ssh_reachable(
            bts_v6, password, source_v6=source_v6, timeout_s=25
        ):
            print(f"[sanity] BTS management: IPv6 {bts_v6}", flush=True)
            return bts_v6, source_v6, "ipv6"

        print(
            f"[sanity] BTS IPv6 {bts_v6} unreachable — trying recovery IPv4 chain",
            flush=True,
        )

    for host in candidates:
        if not host:
            continue
        if is_ipv6_literal(host):
            if not await _ssh_reachable(
                host, password, source_v6=source_v6, timeout_s=25
            ):
                continue
            proto = ""
            try:
                conn = await open_sanity_ssh(
                    host, password, source_v6=source_v6, label="BTS-probe", timeout_s=25
                )
                proto = sanitize_sanity_scalar(
                    (await conn.send_command("uci -q get network.lan.proto")).result
                )
                await close_sanity_ssh(conn)
            except Exception:
                proto = ""
            if proto == "dhcp":
                print(
                    f"[sanity] BTS reachable at {host} with proto=dhcp — "
                    "restoring profile static network",
                    flush=True,
                )
                bts_before, cpe_before = profile_baseline_snapshots(profile)
                await sanity_03_restore_link(
                    profile=profile,
                    values=values,
                    device_creds=device_creds,
                    bts_before=bts_before,
                    cpe_before=cpe_before,
                    bts_original_host=bts_v6,
                    cpe_original_host=cpe_v6,
                    source_v6=source_v6,
                    link_timeout_s=int(values.get("link_recovery_timeout_s", 480)),
                    poll_s=int(values.get("poll_interval_s", 10)),
                )
                return await _wait_preferred_bts_host(
                    bts_v6=bts_v6,
                    password=password,
                    source_v6=source_v6,
                    profile=profile,
                    candidates=candidates,
                )
            note = "ipv6" if host == bts_v6 else f"ipv6 alt {host}"
            print(f"[sanity] BTS management: {note} {host}", flush=True)
            return host, source_v6, note

        await _prepare_pc_for_bts_host(profile, password, host)
        if not await _ssh_reachable(host, password, timeout_s=25):
            continue

        if host == factory_bts:
            restored = await sanity_preflight_restore_if_factory(
                profile,
                values=values,
                device_creds=device_creds,
                bts_original_host=bts_v6,
                cpe_original_host=cpe_v6,
                source_v6=source_v6,
            )
            if restored:
                return await _wait_preferred_bts_host(
                    bts_v6=bts_v6,
                    password=password,
                    source_v6=source_v6,
                    profile=profile,
                    candidates=candidates,
                )
            print(
                f"[sanity] BTS reachable at factory {factory_bts} but restore skipped/failed",
                flush=True,
            )
            return factory_bts, "", f"factory {factory_bts}"

        await clean_bts_mgmt_vlan(host, password)
        if bts_v6 and is_ipv6_literal(bts_v6):
            if _host_reachable(bts_v6) or await _ssh_reachable(
                bts_v6, password, source_v6=source_v6, timeout_s=25
            ):
                print(
                    f"[sanity] BTS management: IPv6 {bts_v6} (via fallback {host})",
                    flush=True,
                )
                return bts_v6, source_v6, "ipv6 after fallback"

        print(f"[sanity] BTS management: fallback IPv4 {host}", flush=True)
        return host, "", f"fallback {host}"

    print(
        "[sanity] All direct BTS hosts failed — attempting factory profile restore",
        flush=True,
    )
    restored = await sanity_preflight_restore_if_factory(
        profile,
        values=values,
        device_creds=device_creds,
        bts_original_host=bts_v6,
        cpe_original_host=cpe_v6,
        source_v6=source_v6,
    )
    if restored:
        return await _wait_preferred_bts_host(
            bts_v6=bts_v6,
            password=password,
            source_v6=source_v6,
            profile=profile,
            candidates=candidates,
        )

    raise RuntimeError(
        f"[sanity] BTS unreachable at IPv6 {bts_v6 or '(none)'} "
        f"and recovery chain {candidates}. Check RF link / device power / network."
    )


def _ipv6_endpoints_from_profile(profile_bundle, request) -> tuple[str, str, str]:
    dut = profile_bundle.active.get("dut", {}) or {}
    mgmt = (profile_bundle.active.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    bts_v6 = normalize_ip(
        (request.config.getoption("--local-ipv6") or "").strip()
        or str(dut.get("local_ipv6") or mgmt.get("ipv6_bts") or "")
    )
    cpe_v6 = normalize_ip(
        (request.config.getoption("--remote-ipv6") or "").strip().split(",")[0]
        or str((dut.get("remote_ipv6s") or [mgmt.get("ipv6_cpe")])[0] or "")
    )
    source_v6 = normalize_ip(str(dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or ""))
    return bts_v6, cpe_v6, source_v6
