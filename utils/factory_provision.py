"""Factory-reset provisioning: installer Quick Start, VLAN 200/201, NMS, AIRTEL, link formation."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver

from utils.link_formation import (
    apply_cpe_pre_link_via_secondary_pc,
    ensure_cpe_link_credentials_always,
    ensure_p2mp_link_credentials,
    resolve_link_credentials_ssh,
)
from utils.net_utils import normalize_ip
from utils.quick_start_provision import provision_bts_quick_start_gui
from utils.regression_flows import _open_root_ssh
from utils.lab_pc_net import ensure_fallback_ethernet
from utils.testbed_bootstrap import (
    TestbedState,
    _apply_mgmt_to_dut,
    _link_health_bts,
    _setup_lab_pcs,
    _tb,
)
from utils.vlan_control import ensure_vlan_mode_ssh
from utils.vlan_uci import (
    build_bts_network_ipv6_commands,
    build_bts_qinq_commands,
    build_cpe_mgmtvlan_only_commands,
    build_nms_syslog_commands,
)


async def _open_ssh_user(host: str, username: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=normalize_ip(host),
        auth_username=username,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _open_ssh_password_chain(
    host: str,
    candidates: list[tuple[str, str]],
    *,
    label: str,
) -> tuple[AsyncGenericDriver, str, str]:
    last = ""
    for user, passwd in candidates:
        if not passwd:
            continue
        try:
            ssh = await _open_ssh_user(host, user, passwd)
            print(f"[factory] {label} SSH OK {user}@{host}")
            return ssh, user, passwd
        except Exception as exc:
            last = str(exc)
    raise ConnectionError(f"{label} SSH failed for {host}: {last}")


def _factory_cfg(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("factory_provision", {}) or {}


def _ssh_password_candidates(profile: dict[str, Any]) -> list[tuple[str, str]]:
    dut = profile.get("dut", {}) or {}
    fl = profile.get("factory_login", {}) or {}
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(user: str, pw: str) -> None:
        key = (user, pw)
        if pw and key not in seen:
            seen.add(key)
            out.append(key)

    add(str(fl.get("username", "installer")), str(fl.get("password", "")))
    add("root", str(fl.get("password", "")))
    add("root", str(dut.get("password", "")))
    return out


async def _run_uci_batch(ssh: AsyncGenericDriver, commands: list[str], *, label: str) -> None:
    for cmd in commands:
        if not cmd:
            continue
        result = await ssh.send_command(cmd, timeout_ops=90)
        out = str(result.result or "")
        if "error" in out.lower() and "not found" not in out.lower():
            print(f"[factory] {label} UCI warn ({cmd[:50]}): {out[:200]}")


async def _provision_bts_ssh_fallback(
    host: str,
    profile: dict[str, Any],
    password_candidates: list[tuple[str, str]],
) -> AsyncGenericDriver | None:
    tb = _tb(profile)
    nms = profile.get("nms", {}) or {}
    try:
        ssh, _, _ = await _open_ssh_password_chain(host, password_candidates, label="BTS fallback")
    except ConnectionError as exc:
        print(f"[factory] BTS fallback SSH skipped: {exc}")
        return None

    try:
        await _run_uci_batch(ssh, build_bts_qinq_commands(tb), label="BTS QinQ")
        await _run_uci_batch(ssh, build_bts_network_ipv6_commands(profile), label="BTS IPv6")
        await _run_uci_batch(ssh, build_nms_syslog_commands(nms), label="BTS NMS")
        await asyncio.sleep(20)
        return ssh
    except Exception as exc:
        print(f"[factory] BTS UCI apply error: {exc}")
        await ssh.close()
        return None


async def _provision_cpe_mgmtvlan_secondary(
    profile: dict[str, Any],
    password: str,
) -> bool:
    import shlex

    from utils.lab_pc_net import _parse_ssh_target

    tb = _tb(profile)
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        print("[factory] CPE: secondary_pc.ssh not set")
        return False

    cpe_factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "192.168.2.1")))
    host, user = _parse_ssh_target(ssh_target)
    pc_pass = str(sec.get("password") or password)
    uci_cmds = build_cpe_mgmtvlan_only_commands(tb)
    inner = " && ".join(shlex.quote(c) for c in uci_cmds)
    remote = (
        f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
        f"root@{cpe_factory} {inner}"
    )

    pc = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=pc_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await pc.open()
    try:
        result = await pc.send_command(remote, timeout_ops=90)
        out = str(result.result or "")
        if "error" in out.lower() and "entry not found" not in out.lower():
            print(f"[factory] CPE mgmt VLAN: {out[:300]}")
            return False
        print(f"[factory] CPE mgmt VLAN via {user}@{host} → {cpe_factory}")
        return True
    finally:
        await pc.close()


async def _wait_for_bts_fallback(host: str, timeout_s: int = 90) -> bool:
    import subprocess

    started = time.time()
    while time.time() - started < timeout_s:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
        )
        if proc.returncode == 0:
            return True
        await asyncio.sleep(3)
    return False


async def _wait_for_link(bts_ssh, profile: dict[str, Any], timeout_s: int) -> bool:
    started = time.time()
    while time.time() - started < timeout_s:
        if await _link_health_bts(bts_ssh, profile):
            return True
        await asyncio.sleep(3)
    return False


async def provision_factory_reset(
    profile_bundle,
    device_creds: dict[str, str],
    *,
    gui_page=None,
    fallback_ip: str | None = None,
) -> TestbedState:
    """
    After factory reset on both units:

    1. enp3s0 untagged + 10.0.0.xx → reach BTS fallback 10.0.0.1
    2. BTS basic config (installer GUI + UCI) on fallback
    3. enp3s0.200.201 QinQ + mgmt IPv6 (fallback IPv4 kept on parent enp3s0)
    4. BTS: AIRTEL SSID/key from serial
    5. CPE: mgmt VLAN only via secondary PC → factory 192.168.2.1
    6. CPE: same AIRTEL credentials (pre-link)
    7. Poll RF link on BTS mgmt IPv6
    """
    active = profile_bundle.active
    fp = _factory_cfg(active)
    if not fp.get("enabled", True):
        state = TestbedState()
        _apply_mgmt_to_dut(active, state)
        state.notes.append("factory_provision disabled")
        state.persist()
        return state

    password = device_creds["pass"]
    state = TestbedState()
    _apply_mgmt_to_dut(active, state)
    tb = _tb(active)
    bts_fallback = normalize_ip(
        fallback_ip or fp.get("bts_fallback_ipv4") or tb.get("recovery", {}).get("bts_fallback_ipv4", "10.0.0.1")
    )
    pw_chain = _ssh_password_candidates(active)
    link_wait = int(fp.get("link_wait_s", 120))

    primary_pc = dict(tb.get("primary_pc", {}) or {})
    print("[factory] Phase 0: enp3s0 untagged 10.0.0.xx (fallback path, no QinQ)")
    await ensure_fallback_ethernet(primary_pc, password)

    print(f"[factory] Waiting for BTS fallback {bts_fallback} (ping)...")
    if not await _wait_for_bts_fallback(bts_fallback, timeout_s=int(fp.get("fallback_wait_s", 90))):
        state.notes.append(f"BTS fallback {bts_fallback} not reachable — check power/cable")
        state.persist()
        print(f"[factory] ABORT: cannot reach {bts_fallback}. Power-cycle BTS and re-run.")
        return state

    gui_steps: list[str] = []
    if gui_page is not None:
        print(f"[factory] Phase 1: BTS installer GUI on {bts_fallback}")
        try:
            gui_steps = await provision_bts_quick_start_gui(gui_page, bts_fallback, active)
            state.notes.append(f"BTS GUI steps: {', '.join(gui_steps) or 'none'}")
        except Exception as exc:
            state.notes.append(f"BTS GUI provision failed: {exc}")
            print(f"[factory] BTS GUI: {exc}")
    else:
        state.notes.append("BTS GUI skipped (no browser page)")

    print(f"[factory] Phase 2: BTS UCI on fallback {bts_fallback}")
    bts_ssh = await _provision_bts_ssh_fallback(bts_fallback, active, pw_chain)

    print("[factory] Phase 3: lab PC VLAN 200/201 + mgmt IPv6")
    await _setup_lab_pcs(active, state, password)
    state.notes.append(f"lab PC QinQ svlan={state.qinq_svlan} cvlan={state.qinq_cvlan}")

    print("[factory] Phase 4: AIRTEL on BTS")
    link_creds = None
    bts_mgmt_ssh = None
    if bts_ssh is not None:
        try:
            link_creds = await ensure_p2mp_link_credentials(bts_ssh=bts_ssh, profile=active)
            state.notes.append(f"AIRTEL SSID={link_creds.ssid} serial={link_creds.serial}")
        except Exception as exc:
            state.notes.append(f"AIRTEL failed on fallback: {exc}")
        await bts_ssh.close()

    print("[factory] Phase 5: CPE mgmt VLAN only (secondary PC)")
    cpe_vlan_ok = await _provision_cpe_mgmtvlan_secondary(active, password)
    state.notes.append(f"CPE mgmt VLAN only ok={cpe_vlan_ok}")

    if link_creds is None and bts_mgmt_ssh is None:
        try:
            bts_mgmt_ssh = await _open_root_ssh(state.bts_mgmt_ipv6, password)
            link_creds = await resolve_link_credentials_ssh(bts_mgmt_ssh, active)
        except Exception:
            link_creds = None

    if link_creds is not None:
        print("[factory] Phase 6: CPE SSID/key (remote PC GUI → 10.0.0.1)")
        cpe_cred_ok = await ensure_cpe_link_credentials_always(
            active, link_creds, force=True, gui_page=gui_page, headless=gui_page is None
        )
        state.notes.append(f"CPE SSID/key remote GUI ok={cpe_cred_ok}")

        sec = tb.get("secondary_pc", {}) or {}
        if sec.get("enabled", True) and sec.get("ssh"):
            print("[factory] Phase 6b: CPE VLAN via secondary PC (if needed)")
            cpe_mode = "mgmtvlan_only" if fp.get("cpe_use_mgmtvlan_only", True) else "transparent"
            ok = await apply_cpe_pre_link_via_secondary_pc(
                sec,
                link_creds,
                active,
                profile_tb=tb,
                password=password,
                cpe_mode=cpe_mode,
            )
            state.notes.append(f"CPE secondary pre-link ok={ok}")

    print("[factory] Phase 7: wait for RF link")
    try:
        bts_mgmt_ssh = await _open_root_ssh(state.bts_mgmt_ipv6, password)
    except Exception:
        try:
            bts_mgmt_ssh, _, _ = await _open_ssh_password_chain(
                bts_fallback, pw_chain, label="BTS post-config"
            )
        except ConnectionError as exc:
            state.notes.append(f"BTS unreachable for link poll: {exc}")
            state.persist()
            return state

    state.link_up = await _wait_for_link(bts_mgmt_ssh, active, link_wait)
    state.notes.append(f"link_up={state.link_up} (waited up to {link_wait}s)")

    if state.link_up:
        nms = active.get("nms", {}) or {}
        nms_host = str(nms.get("syslog_ipv6", "")).strip()
        if nms_host:
            raw = await bts_mgmt_ssh.send_command(
                "uci get system.@system[0].log_ip 2>/dev/null", timeout_ops=15
            )
            log_ip = str(raw.result or "").strip()
            state.notes.append(f"BTS NMS log_ip={log_ip!r} (expected {nms_host})")

    await bts_mgmt_ssh.close()
    state.persist()
    return state
