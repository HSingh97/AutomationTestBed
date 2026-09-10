"""IPv4 bench preflight and recovery (reused by IP_01–IP_60 and manual bootstrap)."""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from utils.net_utils import normalize_ip
from utils.vlan_control import _normalize_mode_name, ensure_vlan_mode_ssh, read_vlan_uci_ssh
from utils.vlan_uci import expected_bts_qinq_text


def _tb(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("testbed", {}) or {}


def _expected_bts_vlan(profile: dict[str, Any]) -> tuple[str, str]:
    tb = _tb(profile)
    factory = tb.get("factory_defaults", {}) or {}
    mode = str(factory.get("bts_vlan_mode", "transparent")).strip().lower()
    mgmt = tb.get("mgmt_vlan", {}) or {}
    qinq = tb.get("qinq", {}) or {}
    mgmt_val = str(int(mgmt.get("uci_value", qinq.get("cvlan", 101))))
    return mode, mgmt_val


def audit_bts_vlan_baseline(profile: dict[str, Any], current: dict[str, str]) -> list[str]:
    expected_mode, expected_mgmt = _expected_bts_vlan(profile)
    issues: list[str] = []
    mode = _normalize_mode_name(current.get("mode", ""))
    want_mode = _normalize_mode_name(expected_mode)
    if mode != want_mode:
        issues.append(f"vlan.ath1.mode={current.get('mode', '?')} (want {expected_mode})")
    mgmt = str(current.get("mgmtvlan", "")).strip()
    if mgmt != expected_mgmt:
        issues.append(f"vlan.ath1.mgmtvlan={mgmt or 'unset'} (want {expected_mgmt})")
    if want_mode == "qinq":
        exp = expected_bts_qinq_text(_tb(profile))
        for key in ("svlan", "cvlan"):
            got = str(current.get(key, "")).strip()
            if got and got != exp[key]:
                issues.append(f"vlan.ath1.{key}={got} (want {exp[key]})")
    return issues


def _log(ctx, msg: str) -> None:
    ctx.notes.append(msg)
    print(f"[preflight] {msg}")


def _run_local(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


async def preflight_step1_fallback_ssh(ctx) -> None:
    """Connect BTS/CPE SSH using fallback chain (10.0.0.1 → LAN → mgmt IPv6)."""
    from utils.ip_test_flows import (
        CpeSecondaryHopDriver,
        _close_ssh,
        _device_ssh_host_candidates,
        _ordered_unique_hosts,
        _wait_ssh_any,
    )

    if ctx.device_target == "cpe" and isinstance(ctx.ssh, CpeSecondaryHopDriver):
        _log(ctx, f"step 1 OK: keep CPE SSH hop (host={ctx.host})")
        return

    cfg = ctx.cfg
    password = str(cfg.get("_password", ""))
    all_hosts = _device_ssh_host_candidates(ctx)
    if not all_hosts:
        pytest.fail("preflight step 1: no SSH host candidates (profile/dut/fallback empty)")

    # Prefer factory/fallback first — bench is often reachable only on 10.0.0.1 after reset.
    fallback_first: list[str] = []
    rest: list[str] = []
    cli_fb = normalize_ip(str(cfg.get("_cli_fallback_ip") or ""))
    profile = cfg.get("_profile") or {}
    dut_fb = normalize_ip(str(profile.get("dut", {}).get("local_ip", "")))
    rec_fb = normalize_ip(
        str((profile.get("testbed", {}) or {}).get("recovery", {}).get("bts_fallback_ipv4", ""))
    )
    fallback_set = {h for h in (cli_fb, dut_fb, rec_fb, "10.0.0.1") if h}
    for h in all_hosts:
        if h in fallback_set or h.startswith("10.0.0."):
            fallback_first.append(h)
        else:
            rest.append(h)
    hosts = _ordered_unique_hosts(*fallback_first, *rest)

    if ctx.ssh is not None:
        try:
            await _close_ssh(ctx.ssh)
        except Exception:
            pass

    ssh, effective = await _wait_ssh_any(hosts, password, timeout_s=90, interval_s=3)
    ctx.ssh = ssh
    ctx.host = effective
    _log(ctx, f"step 1 OK: SSH via {effective} (candidates tried: {hosts[:5]})")


async def preflight_step2_device_config(ctx) -> None:
    """Check BTS VLAN UCI; apply transparent + mgmtvlan 101 if missing. Resolve LAN/CPE IPs."""
    from utils.ip_test_flows import (
        _reconnect_device_ssh,
        _resolve_bts_lan_ipv4,
        discover_cpe_ipv4_addresses,
    )

    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    tb = _tb(profile)
    ip_cfg = profile.get("ip_tests", {}) or {}

    if ctx.device_target != "bts" or not tb.get("configure_vlan_modes", True):
        return

    expected_mode, expected_mgmt = _expected_bts_vlan(profile)
    reload_s = int(ip_cfg.get("baseline_network_reload_wait_s", 30))
    vlan_ssh = tb.get("vlan_ssh", {}) or {}

    try:
        await _reconnect_device_ssh(ctx, timeout_s=60)
    except Exception as exc:
        _log(ctx, f"step 2: SSH reconnect note: {exc}")

    current = await read_vlan_uci_ssh(ctx.ssh, tb, "bts")
    issues = audit_bts_vlan_baseline(profile, current)
    if issues:
        _log(ctx, f"step 2: applying BTS VLAN — {'; '.join(issues)}")
        ok = await ensure_vlan_mode_ssh(ctx.ssh, "bts", expected_mode, vlan_ssh, profile_tb=tb)
        if not ok:
            pytest.fail(
                f"preflight step 2: could not apply BTS VLAN "
                f"(mode={expected_mode}, mgmtvlan={expected_mgmt})"
            )
        await asyncio.sleep(reload_s)
        await _reconnect_device_ssh(ctx, timeout_s=90)
        current = await read_vlan_uci_ssh(ctx.ssh, tb, "bts")
        issues = audit_bts_vlan_baseline(profile, current)
        if issues:
            pytest.fail(
                "preflight step 2: BTS VLAN still wrong after apply: "
                + "; ".join(issues)
            )
    else:
        _log(
            ctx,
            f"step 2 OK: BTS VLAN mode={current.get('mode', expected_mode)} "
            f"mgmtvlan={current.get('mgmtvlan', expected_mgmt)}",
        )

    bts_lan = await _resolve_bts_lan_ipv4(ctx)
    cfg["_preflight_bts_lan_ipv4"] = bts_lan
    _log(ctx, f"step 2: BTS LAN IPv4 for ping = {bts_lan}")

    try:
        cpe_candidates = await discover_cpe_ipv4_addresses(ctx)
        if cpe_candidates:
            cfg["_preflight_cpe_candidates"] = cpe_candidates
            if not ctx.peer_host:
                ctx.peer_host = cpe_candidates[0]
            _log(
                ctx,
                f"step 2: CPE remote IPv4 candidate(s) = {cpe_candidates[:4]} "
                f"(primary {ctx.peer_host})",
            )
        else:
            _log(ctx, "step 2: no CPE IPv4 discovered yet (link may still be down)")
    except Exception as exc:
        _log(ctx, f"step 2: CPE discovery deferred: {exc}")


async def preflight_step3_lab_mgmt_interface(ctx) -> None:
    """Create/verify lab PC tagged mgmt VLAN subinterface for ping (e.g. enp3s0.101)."""
    from utils.ip_test_flows import _ensure_lab_mgmt_vlan_for_ping, _lab_primary_pc

    profile = ctx.cfg.get("_profile") or {}
    pc = _lab_primary_pc(profile)
    vlan_id = int(_tb(profile).get("mgmt_vlan", {}).get("lab_pc_vlan_id", 101))
    parent = str(pc.get("mgmt_interface", "enp3s0"))
    expected_if = f"{parent}.{vlan_id}"

    vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
    if not vlan_if:
        pytest.fail(
            f"preflight step 3: lab mgmt VLAN {expected_if} not configured "
            f"(need sudo on {parent})"
        )

    rc, out = _run_local(f"ip link show dev {shlex.quote(vlan_if)}")
    if rc != 0:
        pytest.fail(f"preflight step 3: {vlan_if} missing on lab PC: {out[:200]}")
    if "state down" in out.lower() and "state down" in out.lower().replace("state unknown", ""):
        _run_local(f"ip link set {shlex.quote(vlan_if)} up")
        rc, out = _run_local(f"ip link show dev {shlex.quote(vlan_if)}")
    bind = ctx.cfg.get("lab_pc_mgmt_ipv4", "192.168.2.10")
    rc2, addr_out = _run_local(f"ip -4 addr show dev {shlex.quote(vlan_if)}")
    if rc2 != 0 or normalize_ip(str(bind).split("/")[0]) not in addr_out:
        pytest.fail(
            f"preflight step 3: {vlan_if} has no {bind} — got: {addr_out[:180]}"
        )
    _log(ctx, f"step 3 OK: lab interface {vlan_if} up with {bind}")


async def preflight_step4_link_formation(ctx, *, strict: bool = True) -> bool:
    """Bench RF: txpower=1 first, then SSID/key sync + client poll (does not change LAN UCI)."""
    from utils.ip_link_recovery import apply_bts_tx_power_from_cfg, ensure_testbed_link_ready

    cfg = ctx.cfg
    cid = ctx.case.case_id
    if not cfg.get("enable_link_recovery", True):
        return True

    if ctx.device_target == "bts":
        try:
            await apply_bts_tx_power_from_cfg(ctx)
        except Exception as exc:
            _log(ctx, f"step 4: txpower apply note: {exc}")

    if not cfg.get("link_recovery_before_precheck", True):
        return True

    link_ok = await ensure_testbed_link_ready(ctx)
    if not link_ok and strict:
        _log(ctx, "step 4: RF link down after link formation")
    return link_ok


async def preflight_step4_reachability(
    ctx,
    *,
    skip_bts_precheck: bool = False,
    require_cpe: bool = False,
    strict: bool = True,
) -> None:
    """Mgmt-VLAN ping to BTS and CPE (after link formation when used)."""
    from utils.ip_test_flows import ensure_bts_ipv4_ready, ensure_cpe_ipv4_ready

    cfg = ctx.cfg
    cid = ctx.case.case_id

    if not skip_bts_precheck and ctx.device_target == "bts":
        bts_ip = await ensure_bts_ipv4_ready(ctx)
        cfg["_preflight_bts_lan_ipv4"] = bts_ip
        _log(ctx, f"step 4 OK: BTS reachable at {bts_ip} via mgmt VLAN")

    if require_cpe:
        try:
            cpe_ip = await ensure_cpe_ipv4_ready(ctx)
            ctx.peer_host = cpe_ip
            cfg["_preflight_cpe_ipv4"] = cpe_ip
            _log(ctx, f"step 4 OK: CPE reachable at {cpe_ip} via mgmt VLAN")
            chain = cfg.get("_ip_suite_chain")
            if isinstance(chain, dict):
                from utils.ip_suite_state import mark_suite_healthy, save_chain

                mark_suite_healthy(
                    chain,
                    bts_ip=str(cfg.get("_preflight_bts_lan_ipv4", "")),
                    cpe_ip=cpe_ip,
                    remote_pc_ok=True,
                    cpe_ping_ok=True,
                )
                save_chain(cfg.get("_repo_root", "."), chain)
        except Exception as exc:
            if strict:
                pytest.fail(f"{cid}: step 4 — CPE not reachable: {exc}")
            _log(ctx, f"step 4: CPE reachability warn: {exc}")
    elif ctx.peer_host:
        cfg["_preflight_cpe_ipv4"] = normalize_ip(str(ctx.peer_host).split("/")[0])


async def preflight_step4_link_and_reachability(
    ctx,
    *,
    skip_bts_precheck: bool = False,
    require_cpe: bool = False,
    strict: bool = True,
) -> None:
    """Link formation (tx=1, SSID/key) then mgmt-VLAN ping."""
    cfg = ctx.cfg
    cid = ctx.case.case_id

    link_ok = await preflight_step4_link_formation(ctx, strict=strict)
    if not link_ok and require_cpe and strict:
        pytest.fail(
            f"{cid}: preflight step 4 — RF link down after recovery "
            "(SSID/key/txpower on BTS + CPE via fallback)"
        )
    elif not link_ok:
        _log(ctx, "step 4: RF link down (case may not need CPE)")

    await preflight_step4_reachability(
        ctx,
        skip_bts_precheck=skip_bts_precheck,
        require_cpe=require_cpe,
        strict=strict,
    )


def case_requires_cpe(case_id: str) -> bool:
    return case_id in {
        "IP_03", "IP_05", "IP_06", "IP_07", "IP_08",
        "IP_09", "IP_11", "IP_12", "IP_13", "IP_14", "IP_16", "IP_17",
        "IP_20", "IP_22", "IP_23", "IP_24", "IP_25", "IP_26",
        "IP_28", "IP_30", "IP_31", "IP_32", "IP_33",
    }


async def preflight_step3_lab_mgmt_ipv6(ctx) -> None:
    """Lab PC tagged mgmt VLAN with /120 IPv6 (same layer as testbed bootstrap)."""
    from utils.ip_test_flows import (
        _lab_mgmt_vlan_id,
        _lab_primary_pc,
        format_ipv6_cidr,
    )
    from utils.lab_pc_net import configure_mgmt_interface, ensure_fallback_subnet
    from utils.vlan_uci import mgmt_access_vlan_plan

    profile = ctx.cfg.get("_profile") or {}
    tb = _tb(profile)
    mgmt = tb.get("mgmt_vlan", {}) or {}
    dut = profile.get("dut", {}) or {}
    prefix_len = int(ctx.cfg.get("ipv6_prefix_len", mgmt.get("prefix_len", 120)))
    pc_v6_raw = str(
        dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or dut.get("mgmt_oob_ipv6") or ""
    ).strip()
    if not pc_v6_raw:
        pytest.fail("preflight step 3v6: dut.bts_pc_ipv6 / mgmt_vlan.ipv6_bts_pc not set")
    pc_cidr = format_ipv6_cidr(pc_v6_raw, ctx.cfg)
    pc = _lab_primary_pc(profile)
    password = str(ctx.cfg.get("_password", ""))
    vlan_id = _lab_mgmt_vlan_id(profile)

    await ensure_fallback_subnet(pc, password)
    ok = await configure_mgmt_interface(
        pc,
        ipv6_address=pc_cidr,
        prefix_len=prefix_len,
        password=password,
        vlan_id=vlan_id,
        tagging=mgmt_access_vlan_plan(tb),
    )
    if not ok:
        pytest.fail(f"preflight step 3v6: lab PC mgmt IPv6 {pc_cidr} not configured")

    parent = str(pc.get("mgmt_interface", "enp3s0"))
    vlan_if = f"{parent}.{vlan_id}"
    rc, out = _run_local(f"ip -6 addr show dev {shlex.quote(vlan_if)}")
    want_host = normalize_ip(pc_cidr.split("/")[0])
    has_v6 = False
    if rc == 0:
        import ipaddress

        try:
            want_addr = ipaddress.IPv6Address(want_host)
            for line in out.splitlines():
                if "inet6" not in line.lower():
                    continue
                m = re.search(r"inet6\s+([0-9a-f:]+)/\d+", line, re.I)
                if m and ipaddress.IPv6Address(m.group(1)) == want_addr:
                    has_v6 = True
                    break
        except ValueError:
            has_v6 = want_host in out
    if not has_v6:
        pytest.fail(
            f"preflight step 3v6: {vlan_if} missing {pc_cidr} — got: {out[:200]}"
        )
    from utils.ip_test_flows import _canonical_ipv6

    ctx.cfg["_lab_ping_bind_ipv6"] = _canonical_ipv6(pc_cidr.split("/")[0])
    _log(ctx, f"step 3v6 OK: lab {vlan_if} has {pc_cidr}")


async def _read_device_ipv6_state(ctx) -> dict[str, str]:
    from utils.ip_test_flows import _read_uci_ip, _ssh_run_raw

    uci = await _read_uci_ip(ctx.ssh, v6=True)
    show = await _ssh_run_raw(ctx.ssh, "ip -6 addr show dev br-lan 2>/dev/null; ip -6 addr show")
    return {"uci_address": str(uci.get("address", "")), "uci_gateway": str(uci.get("gateway", "")), "ip6_show": show}


async def preflight_step4_ipv6_reachability(
    ctx,
    *,
    require_cpe: bool = False,
    strict: bool = True,
) -> None:
    """Lab ping6 to device static/mgmt IPv6 after link formation."""
    from utils.ip_test_flows import (
        _assert_lab_ping_ipv6,
        _ipv6_values_for_target,
        _ping_remote_after_local,
    )

    cid = ctx.case.case_id
    apply = _ipv6_values_for_target(ctx)
    target = normalize_ip(apply["ipv6_address"].split("/")[0])
    try:
        await _assert_lab_ping_ipv6(ctx, target, count=int(ctx.cfg.get("ping_count_short", 4)))
        _log(ctx, f"step 4v6 OK: lab ping6 to device {target}")
    except Exception as exc:
        if strict:
            pytest.fail(f"{cid}: step 4v6 — device {target} not reachable via lab ping6: {exc}")
        _log(ctx, f"step 4v6 warn: lab ping6 {target}: {exc}")

    if require_cpe and ctx.peer_host:
        from utils.ip_test_flows import _remote_ping_wait_settings

        max_wait_s, interval_s = _remote_ping_wait_settings(ctx.cfg)
        retries = max(
            int(ctx.cfg.get("post_reboot_remote_ping_retries", 12)),
            max(1, max_wait_s // interval_s),
        )
        try:
            await _ping_remote_after_local(
                ctx,
                v6=True,
                count=int(ctx.cfg.get("ping_count_short", 4)),
                retries=retries,
                retry_interval_s=interval_s,
            )
            _log(ctx, f"step 4v6 OK: DUT ping6 to peer {ctx.peer_host}")
        except AssertionError as exc:
            if ctx.cfg.get("ip_preflight_warn_remote_cpe_v6", True):
                _log(ctx, f"step 4v6 warn: BTS→CPE ping6 {ctx.peer_host}: {exc}")
            elif strict:
                pytest.fail(f"{cid}: step 4v6 — CPE {ctx.peer_host} not reachable: {exc}")


async def ensure_device_ipv6_configured(ctx) -> None:
    """IP_19+ require static IPv6 on DUT (from IP_18 or profile)."""
    from utils.ip_test_flows import _ipv6_values_for_target

    state = await _read_device_ipv6_state(ctx)
    apply = _ipv6_values_for_target(ctx)
    from utils.ip_test_flows import ipv6_addr_in_text, ipv6_equal

    uci_addr = str(state.get("uci_address", ""))
    if not ipv6_equal(apply["ipv6_address"], uci_addr) and not ipv6_addr_in_text(
        apply["ipv6_address"].split("/")[0], state.get("ip6_show", "")
    ):
        pytest.fail(
            f"{ctx.case.case_id}: device IPv6 not configured "
            f"(expected {apply['ipv6_address']} in UCI/ip -6). "
            "Run IP_18 first or set ip_tests.ipv6_address_* in profile."
        )
    ctx.notes.append(f"device IPv6 OK: UCI={uci_addr[:80]}")


async def run_ip_case_preflight_v6(
    ctx,
    *,
    require_cpe: bool | None = None,
    skip_device_v6_ping: bool = False,
    skip_bts_precheck: bool = False,
    minimal: bool = False,
) -> None:
    """
    IPv6 suite mirrors IPv4 preflight:
      1) fallback SSH (10.0.0.1 → … → mgmt IPv6)
      2) BTS VLAN baseline
      3) lab PC mgmt VLAN + /120 on backend PC
      4) link formation + ping6 (skipped for IP_18 before static apply)
    """
    cfg = ctx.cfg
    ip_cfg = (cfg.get("_profile") or {}).get("ip_tests", {}) or {}
    if not ip_cfg.get("preflight_enabled", True):
        return

    cid = ctx.case.case_id
    if _preflight_skipped_chain_ok(ctx):
        chain = cfg.get("_ip_suite_chain") or {}
        cached_bts = str(chain.get("bts_lan_ipv4") or cfg.get("_preflight_bts_lan_ipv4") or "")
        if cached_bts:
            cfg["_preflight_bts_lan_ipv4"] = normalize_ip(cached_bts.split("/")[0])
        cached_cpe = str(
            chain.get("cpe_lan_ipv6")
            or cfg.get("_preflight_cpe_ipv6")
            or ""
        )
        if cached_cpe:
            ctx.peer_host = normalize_ip(cached_cpe.split("/")[0])
        _log(
            ctx,
            f"=== {cid} IPv6 preflight skipped (suite healthy: "
            f"BTS={cfg.get('_preflight_bts_lan_ipv4', '')} "
            f"CPE={ctx.peer_host or 'n/a'}) ===",
        )
        return

    need_cpe = case_requires_cpe(cid) if require_cpe is None else require_cpe
    skip_ping = skip_device_v6_ping or cid == "IP_18"

    if minimal:
        _log(ctx, f"=== {cid} IPv6 preflight (fast) ===")
        await preflight_step1_fallback_ssh(ctx)
        await preflight_step3_lab_mgmt_ipv6(ctx)
        if not skip_ping:
            await preflight_step4_ipv6_reachability(
                ctx, require_cpe=need_cpe, strict=False
            )
        _log(ctx, f"=== {cid} IPv6 preflight (fast) done ===")
        return

    _log(ctx, f"=== {cid} IPv6 preflight start ===")
    await preflight_step1_fallback_ssh(ctx)
    if ctx.device_target == "bts":
        await preflight_step2_device_config(ctx)
    await preflight_step3_lab_mgmt_ipv6(ctx)

    state = await _read_device_ipv6_state(ctx)
    ctx.cfg["_device_ipv6_preflight"] = state
    _log(
        ctx,
        f"device IPv6 before case: uci={state.get('uci_address', '')[:60]} "
        f"fe80={'fe80::' in state.get('ip6_show', '').lower()}",
    )

    link_ok = await preflight_step4_link_formation(ctx, strict=need_cpe and not skip_ping)
    if not link_ok and need_cpe and not skip_ping:
        pytest.fail(f"{cid}: IPv6 preflight — RF link down")

    if need_cpe and cid not in ("IP_18",):
        from utils.ip_test_flows import ensure_cpe_ipv6_ready

        await ensure_cpe_ipv6_ready(ctx)

    if not skip_ping:
        if cid != "IP_18":
            await ensure_device_ipv6_configured(ctx)
        await preflight_step4_ipv6_reachability(
            ctx, require_cpe=need_cpe, strict=need_cpe
        )
    else:
        _log(ctx, "step 4v6: deferred (IP_18 will apply static IPv6 then lab ping6)")

    _log(ctx, f"=== {cid} IPv6 preflight complete ===")


async def run_post_event_testbed_recovery_v6(
    ctx,
    *,
    label: str = "post-event",
    link_formation: bool = True,
    verify_reachability: bool = True,
    require_cpe: bool | None = None,
    strict: bool = False,
    after_mgmt_hook: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """
    Post-reboot/reset/post-case recovery for IPv6 suite (mirrors run_post_event_testbed_recovery).
    """
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    ip_cfg = profile.get("ip_tests", {}) or {}
    if not ip_cfg.get("preflight_enabled", True):
        return True
    if not ip_cfg.get("post_event_recovery_enabled", True):
        return True

    cid = ctx.case.case_id
    need_cpe = case_requires_cpe(cid) if require_cpe is None else require_cpe

    _log(ctx, f"=== {cid} {label} IPv6 recovery start ===")
    await preflight_step1_fallback_ssh(ctx)
    if ctx.device_target == "bts":
        await preflight_step2_device_config(ctx)
    await preflight_step3_lab_mgmt_ipv6(ctx)

    if after_mgmt_hook is not None:
        await after_mgmt_hook()

    link_ok = True
    if link_formation:
        link_ok = await preflight_step4_link_formation(ctx, strict=strict and need_cpe)
        if not link_ok and need_cpe and strict:
            pytest.fail(f"{cid}: {label} — RF link down (IPv6 recovery)")

    if need_cpe and cfg.get("ip18_configure_cpe", True):
        from utils.ip_test_flows import ensure_cpe_ipv6_ready

        try:
            await ensure_cpe_ipv6_ready(ctx, strict=strict)
        except Exception as exc:
            if strict:
                raise
            _log(ctx, f"{label} v6: CPE configure warn: {exc}")

    if verify_reachability:
        try:
            from utils.ip_test_flows import _reconnect_device_ssh

            await _reconnect_device_ssh(ctx, timeout_s=90)
        except Exception as exc:
            _log(ctx, f"{label} v6: SSH reconnect before reachability: {exc}")
        await preflight_step4_ipv6_reachability(
            ctx, require_cpe=need_cpe, strict=strict
        )

    _log(ctx, f"=== {cid} {label} IPv6 recovery complete ===")
    return link_ok


def _preflight_skipped_chain_ok(ctx) -> bool:
    """Skip preflight when suite is healthy (cached BTS/CPE IPs + lab PCs verified)."""
    from config.ip_test_cases import IP_ALWAYS_PREFLIGHT_CASE_IDS
    from utils.ip_suite_state import chain_allows_preflight_skip

    cfg = ctx.cfg
    cid = ctx.case.case_id
    if cid in IP_ALWAYS_PREFLIGHT_CASE_IDS:
        return False
    if not cfg.get("ip_skip_preflight_when_chain_ok", True):
        return False
    chain = cfg.get("_ip_suite_chain") or {}
    need_cpe = case_requires_cpe(cid)
    return chain_allows_preflight_skip(chain, case_id=cid, require_cpe=need_cpe)


def preflight_should_be_full(ctx) -> bool:
    """
    Full preflight (VLAN + link formation) only when bench health is unknown or broken.
    Otherwise use minimal fast preflight (SSH + lab VLAN + ping only).
    """
    from config.ip_test_cases import IP_ALWAYS_PREFLIGHT_CASE_IDS

    cfg = ctx.cfg
    chain = cfg.get("_ip_suite_chain") or {}
    cid = ctx.case.case_id
    if cid in IP_ALWAYS_PREFLIGHT_CASE_IDS:
        return False
    if not chain.get("ok", False):
        return True
    if not chain.get("suite_healthy", False):
        return True
    return False


async def run_ip_case_preflight(
    ctx,
    *,
    stack_v4: bool = True,
    skip_bts_precheck: bool = False,
    require_cpe: bool | None = None,
    minimal: bool = False,
) -> None:
    """
    Run steps 1–4 before the case body. Applies config when missing (does not skip apply).
    """
    cfg = ctx.cfg
    ip_cfg = cfg.get("_profile", {}).get("ip_tests", {}) or {}
    if not ip_cfg.get("preflight_enabled", True):
        return
    if not stack_v4:
        return

    cid = ctx.case.case_id
    if _preflight_skipped_chain_ok(ctx):
        chain = cfg.get("_ip_suite_chain") or {}
        cached_bts = str(chain.get("bts_lan_ipv4") or cfg.get("_preflight_bts_lan_ipv4") or "")
        if cached_bts:
            cfg["_preflight_bts_lan_ipv4"] = normalize_ip(cached_bts.split("/")[0])
        cached_cpe = str(chain.get("cpe_lan_ipv4") or cfg.get("_preflight_cpe_ipv4") or "")
        if cached_cpe:
            cpe_v4 = normalize_ip(cached_cpe.split("/")[0])
            try:
                import ipaddress

                if isinstance(ipaddress.ip_address(cpe_v4), ipaddress.IPv4Address):
                    ctx.peer_host = cpe_v4
                    cfg["_preflight_cpe_ipv4"] = cpe_v4
            except ValueError:
                pass
        _log(
            ctx,
            f"=== {cid} preflight skipped (suite healthy: "
            f"BTS={cfg.get('_preflight_bts_lan_ipv4', '')} "
            f"CPE={cfg.get('_preflight_cpe_ipv4') or ctx.peer_host or 'n/a'} local+remote PC ok) ===",
        )
        return
    need_cpe = case_requires_cpe(cid) if require_cpe is None else require_cpe
    skip_bts = skip_bts_precheck or cid in ("IP_01", "IP_15", "IP_34")

    if minimal:
        from config.ip_test_cases import case_requires_bts_lan_ping

        _log(ctx, f"=== {cid} preflight (fast) ===")
        await preflight_step1_fallback_ssh(ctx)
        await preflight_step3_lab_mgmt_interface(ctx)
        skip_bts = skip_bts_precheck or cid in ("IP_01", "IP_15", "IP_34")
        if case_requires_bts_lan_ping(cid):
            skip_bts = False
        await preflight_step4_reachability(
            ctx,
            skip_bts_precheck=skip_bts,
            require_cpe=need_cpe,
            strict=False,
        )
        chain = cfg.get("_ip_suite_chain")
        if isinstance(chain, dict):
            from utils.ip_suite_state import mark_suite_healthy, save_chain

            mark_suite_healthy(
                chain,
                bts_ip=str(cfg.get("_preflight_bts_lan_ipv4", "")),
                cpe_ip=str(ctx.peer_host or cfg.get("_preflight_cpe_ipv4", "")),
                local_pc_ok=True,
                remote_pc_ok=bool(need_cpe),
                cpe_ping_ok=bool(need_cpe and ctx.peer_host),
            )
            save_chain(cfg.get("_repo_root", "."), chain)
        _log(ctx, f"=== {cid} preflight (fast) done ===")
        return

    _log(ctx, f"=== {cid} preflight start ===")
    await preflight_step1_fallback_ssh(ctx)
    if ctx.device_target == "bts":
        await preflight_step2_device_config(ctx)
    await preflight_step3_lab_mgmt_interface(ctx)
    await preflight_step4_link_and_reachability(
        ctx,
        skip_bts_precheck=skip_bts,
        require_cpe=need_cpe,
    )
    _log(ctx, f"=== {cid} preflight complete — running case ===")


async def run_post_reset_preflight(ctx) -> None:
    """
    After factory reset/restore: mgmt access only (SSH, VLAN 101, lab enp3s0.101).
    RF link formation (txpower/SSID) is separate — see run_post_event_testbed_recovery.
    """
    await preflight_step1_fallback_ssh(ctx)
    await preflight_step2_device_config(ctx)
    await preflight_step3_lab_mgmt_interface(ctx)


async def run_post_event_testbed_recovery(
    ctx,
    *,
    label: str = "post-event",
    mgmt_baseline: bool = True,
    link_formation: bool = True,
    verify_reachability: bool = True,
    require_cpe: bool | None = None,
    skip_bts_precheck: bool = False,
    strict: bool = True,
    after_mgmt_hook: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """
    Standard bench recovery used after reboot/reset/restore and after every IPv4 case.

    Order (layers kept separate):
      1–3) mgmt baseline when mgmt_baseline=True
      hook) optional async callback (e.g. IP_12 UCI retain check) after baseline, before RF
      4a) link formation: txpower=1 + SSID/key when link_formation=True
      4b) mgmt-VLAN ping when verify_reachability=True
    """
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    ip_cfg = profile.get("ip_tests", {}) or {}
    if not ip_cfg.get("preflight_enabled", True):
        return True
    if not ip_cfg.get("post_event_recovery_enabled", True):
        return True

    cid = ctx.case.case_id
    need_cpe = case_requires_cpe(cid) if require_cpe is None else require_cpe
    skip_bts = skip_bts_precheck

    _log(ctx, f"=== {cid} {label} recovery start ===")

    if mgmt_baseline and ctx.device_target == "bts":
        await run_post_reset_preflight(ctx)
    elif mgmt_baseline:
        await preflight_step1_fallback_ssh(ctx)
        await preflight_step3_lab_mgmt_interface(ctx)

    if after_mgmt_hook is not None:
        await after_mgmt_hook()

    link_ok = True
    if link_formation:
        from utils.ip_link_recovery import run_post_reset_link_formation

        if ctx.device_target == "bts":
            link_ok = await run_post_reset_link_formation(ctx)
        else:
            link_ok = await preflight_step4_link_formation(ctx, strict=strict)
        if not link_ok and need_cpe and strict:
            pytest.fail(
                f"{cid}: {label} — RF link down after link formation "
                "(txpower/SSID/key on BTS + CPE)"
            )
        elif not link_ok:
            _log(ctx, f"{label}: RF link formation incomplete")

    if verify_reachability:
        await preflight_step4_reachability(
            ctx,
            skip_bts_precheck=skip_bts,
            require_cpe=need_cpe,
            strict=strict,
        )

    _log(ctx, f"=== {cid} {label} recovery complete ===")
    return link_ok


async def ensure_bts_testbed_baseline(ctx) -> None:
    """Mgmt VLAN + link + ping after reset/restore (see run_post_event_testbed_recovery)."""
    ip_cfg = (ctx.cfg.get("_profile") or {}).get("ip_tests", {}) or {}
    if not ip_cfg.get("require_testbed_baseline", True):
        return
    await run_post_event_testbed_recovery(ctx, label="baseline")


async def reboot_bts_then_recovery(
    ctx,
    *,
    label: str = "post-reboot",
    v6: bool = False,
    strict: bool = False,
    require_cpe: bool | None = None,
) -> None:
    """Cold reboot BTS, settle, then post-event recovery."""
    from utils.ip_test_flows import _cold_reboot_bts

    await _cold_reboot_bts(ctx, label=label)
    need_cpe = case_requires_cpe(ctx.case.case_id) if require_cpe is None else require_cpe
    if v6:
        await run_post_event_testbed_recovery_v6(
            ctx, label=label, require_cpe=need_cpe, strict=strict
        )
    else:
        await run_post_event_testbed_recovery(
            ctx,
            label=label,
            require_cpe=need_cpe,
            strict=strict,
        )


async def run_ip15_post_restore_recovery(ctx, *, v6: bool = False) -> None:
    """
    IP_15/IP_34: after sysupgrade restore, mgmt baseline then cold reboot.

    Bench: UCI can show the correct LAN while lab mgmt-VLAN ping to BTS fails until reboot.
    Skip link formation / ping before reboot — they waste time and fail on stale bridge state.
    """
    cid = ctx.case.case_id if getattr(ctx, "case", None) else ("IP_34" if v6 else "IP_15")
    settle_s = int(ctx.cfg.get("post_restore_settle_s", 30))
    if settle_s > 0:
        ctx.notes.append(f"{cid}: post-restore settle {settle_s}s before recovery")
        await asyncio.sleep(settle_s)

    if v6:
        await preflight_step1_fallback_ssh(ctx)
        await preflight_step2_device_config(ctx)
        await preflight_step3_lab_mgmt_ipv6(ctx)
    else:
        await run_post_reset_preflight(ctx)

    if not ctx.cfg.get("ip15_reboot_after_restore", True):
        if v6:
            await run_post_event_testbed_recovery_v6(
                ctx, label=f"{cid}-baseline", require_cpe=False, strict=False
            )
        else:
            await run_post_event_testbed_recovery(
                ctx, label=f"{cid}-baseline", require_cpe=False, strict=False
            )
        return

    ctx.notes.append(
        f"{cid}: cold reboot after restore (mgmt-VLAN ping to BTS failed until reboot on bench)"
    )
    print(f"[{cid}] cold reboot after restore — required for lab ping to BTS LAN")
    await reboot_bts_then_recovery(
        ctx,
        label=f"{cid}-post-reboot",
        v6=v6,
        strict=False,
        require_cpe=False,
    )


async def ensure_non_default_bts_lan_ipv4(ctx) -> tuple[str, dict[str, str]]:
    """
    Ensure BTS LAN is a non-default test IP (e.g. 192.168.2.230) before retain/restore checks.
    Reused by IP_12 and manual recovery.
    """
    from utils.ip_test_flows import (
        _cli_apply_ipv4_static,
        _read_device_lan_ipv4s,
        _read_uci_ip,
        _reconnect_device_ssh,
    )

    cfg = ctx.cfg
    cid = ctx.case.case_id
    default_ip = normalize_ip(str(cfg.get("ipv4_default_address", "192.168.2.1")).split("/")[0])
    target_ip = normalize_ip(str(cfg.get("ipv4_test_address", "192.168.2.230")).split("/")[0])
    if target_ip == default_ip:
        pytest.fail(f"{cid}: ipv4_test_address must not be factory default {default_ip}")

    configured = await _read_device_lan_ipv4s(ctx.ssh, cfg)
    if target_ip not in configured:
        apply = {
            "ipv4_address": target_ip,
            "ipv4_netmask": str(cfg.get("ipv4_test_netmask") or cfg.get("ipv4_netmask", "")),
            "ipv4_gateway": str(cfg.get("ipv4_test_gateway") or cfg.get("ipv4_gateway", "")),
        }
        wait_s = int(cfg.get("ip01_post_reload_wait_s", cfg.get("network_reload_wait_s", 40)))
        ctx.notes.append(
            f"{cid}: applying test LAN {target_ip} via ucidyn (not {default_ip}); "
            f"keeping SSH open {wait_s}s before reconnect"
        )
        await _cli_apply_ipv4_static(ctx.ssh, apply, cfg, post_apply_wait_s=wait_s)
        await _reconnect_device_ssh(ctx, timeout_s=120)

    before = await _read_uci_ip(ctx.ssh, v6=False)
    before_addr = normalize_ip(str(before.get("address", "")).split("/")[0])
    if before_addr == default_ip:
        pytest.fail(
            f"{cid}: BTS LAN still {default_ip} — need non-default test IP {target_ip}"
        )
    return before_addr or target_ip, before


async def run_manual_ipv4_recovery(
    profile: dict[str, Any],
    *,
    password: str,
    fallback_ip: str = "10.0.0.1",
) -> tuple[str, str]:
    """CLI/bootstrap entry: apply test LAN, full recovery, return (bts_ip, cpe_ip)."""
    from config.ip_test_cases import IpTestCase
    from utils.ip_test_flows import (
        IpTestContext,
        _ip_cfg,
        _open_ssh,
        ensure_bts_ipv4_ready,
        ensure_cpe_ipv4_ready,
    )

    cfg = _ip_cfg(profile)
    cfg["_profile"] = profile
    cfg["_password"] = password
    cfg["_cli_fallback_ip"] = fallback_ip
    case = IpTestCase("IP_01", "Bench recovery", "Functional", "bts", "v4")
    ssh = await _open_ssh(fallback_ip, password)
    ctx = IpTestContext(
        case=case,
        device_target="bts",
        host=fallback_ip,
        peer_host=None,
        ssh=ssh,
        gui_page=None,
        cfg=cfg,
        stack_mode="ipv4",
        fallback_hosts=(fallback_ip,),
    )
    await ensure_non_default_bts_lan_ipv4(ctx)
    await run_post_event_testbed_recovery(ctx, label="manual-recovery", require_cpe=True)
    return await ensure_bts_ipv4_ready(ctx), await ensure_cpe_ipv4_ready(ctx)
