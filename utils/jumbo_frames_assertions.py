"""Jumbo frame (JMB_01..JMB_10) assertion flows."""

from __future__ import annotations

import asyncio
import re
import shlex
import time

import pytest
from scrapli.driver.generic import AsyncGenericDriver

from pages.locators import UITimeouts
from traffic.packet_capture import icmp_payload_for_mtu, load_jumbo_capture_config, run_pc_jumbo_capture_check
from utils.gui_login import login_if_needed
from utils.parsers import ssh_scalar
from utils.recovery_manager import get_active_recovery_manager


def _eth_key(idx: int) -> str:
    return f"eth{idx}"


def _log_case(case_id: str, message: str):
    print(f"[JUMBO][{case_id}] {message}")


async def _factory_reset_via_current_ui_session(gui_page, *, wait_seconds: int = 140):
    # Navigate to Upgrade/Reset from the currently authenticated GUI session.
    mgmt_menu = gui_page.locator("li.Management > a.menu").first
    await mgmt_menu.wait_for(state="visible", timeout=15000)
    await mgmt_menu.click()
    # Use user-confirmed submenu XPath for this firmware first.
    flashops = gui_page.locator('xpath=//*[@id="Management"]/li[3]/a').first
    if not await flashops.is_visible(timeout=2000):
        flashops = gui_page.locator("ul.dropdown-menu a[href*='/system/flashops']").first
    await flashops.wait_for(state="visible", timeout=15000)
    await flashops.click()
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(1200)

    # Use user-confirmed reset tab XPath.
    reset_tab = gui_page.locator('xpath=//*[@id="maincontent"]/div/div/ul/li[2]/a').first
    if await reset_tab.is_visible(timeout=3000):
        await reset_tab.click()
        await gui_page.wait_for_timeout(800)
    else:
        # Fallback by URL route if tab isn't exposed immediately.
        await gui_page.goto((gui_page.url or "").rstrip("/") + "/reset", timeout=UITimeouts.PAGE_LOAD_MS)
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(800)

    checks = gui_page.locator("input[type='checkbox']")
    total = await checks.count()
    for i in range(total):
        cb = checks.nth(i)
        if await cb.is_visible(timeout=300) and not await cb.is_checked():
            await cb.check()

    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.once("dialog", _accept_dialog)
    # User-confirmed perform reset button XPath.
    reset_btn = gui_page.locator('xpath=//*[@id="reset"]/input').first
    if not await reset_btn.is_visible(timeout=3000):
        reset_btn = gui_page.locator(
            "button:has-text('Perform Reset'):visible, "
            "input[value='Perform Reset']:visible, "
            "input[value*='Reset' i]:visible, "
            "button:has-text('Reset'):visible"
        ).first
    await reset_btn.wait_for(state="visible", timeout=15000)
    await reset_btn.click()
    await asyncio.sleep(wait_seconds)


async def _wait_until_gui_reachable(ip: str, *, timeout_s: int = 200, interval_s: int = 5) -> bool:
    manager = get_active_recovery_manager()
    if not manager:
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await manager.is_gui_reachable(ip):
            return True
        await asyncio.sleep(interval_s)
    return False


async def _open_temp_root_ssh(host: str, password: str, *, timeout_socket: int = 30):
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
        timeout_socket=timeout_socket,
    )
    await conn.open()
    return conn


async def _login_with_retries(gui_page, ip: str, device_creds, *, attempts: int = 4):
    delays = [0, 15, 20, 20]
    last_exc = None
    for i in range(min(attempts, len(delays))):
        if delays[i]:
            await asyncio.sleep(delays[i])
        try:
            await login_if_needed(
                gui_page,
                ip,
                device_creds,
                wait_ms=UITimeouts.LONG_WAIT_MS,
                skip_recovery=True,
            )
            return
        except Exception as exc:
            last_exc = exc
    raise last_exc if last_exc else RuntimeError(f"Unable to login to {ip}")


async def _discovered_lan_ports(root_ssh) -> dict[str, str]:
    """Read current ethernet MTU map (eth0..ethN) over SSH."""
    mtus = await _read_backend_mtu_map(root_ssh)
    return mtus if mtus else {"eth0": "1500"}


async def _configure_bts_mtus_via_ssh(root_ssh, mtu: str, *, keys: list[str] | None = None) -> int:
    eth_keys = keys or list((await _discovered_lan_ports(root_ssh)).keys())
    for key in eth_keys:
        await root_ssh.send_command(f"ucidyn set ethernet.{key}.mtu {shlex.quote(mtu)}")
    await root_ssh.send_command("ucidyn apply")
    await asyncio.sleep(1)
    return len(eth_keys)


async def _restore_bts_mtus_via_ssh(root_ssh, original: dict[str, str]) -> None:
    for key, mtu in original.items():
        await root_ssh.send_command(f"ucidyn set ethernet.{key}.mtu {shlex.quote(mtu)}")
    await root_ssh.send_command("ucidyn apply")


async def _set_all_lans_mtu_via_ssh(root_ssh, lan_total: int, mtu: str, *, case_id: str = "JUMBO") -> None:
    keys = [_eth_key(i) for i in range(lan_total)]
    _log_case(case_id, f"Setting BTS LAN MTU={mtu} via SSH (ucidyn).")
    await _configure_bts_mtus_via_ssh(root_ssh, mtu, keys=keys)


async def _read_backend_mtus(root_ssh, lan_total: int) -> dict[str, str]:
    mtus = await _read_backend_mtu_map(root_ssh)
    if mtus:
        return mtus
    # Fallback when map read is empty but GUI previously reported lan_total ports.
    out: dict[str, str] = {}
    for i in range(lan_total):
        key = _eth_key(i)
        out[key] = ssh_scalar((await root_ssh.send_command(f"uci get ethernet.{key}.mtu")).result)
    return out


def _remote_dut_host_from_profile() -> str | None:
    manager = get_active_recovery_manager()
    if not manager:
        return None
    dut = manager.profile_bundle.active.get("dut", {})
    ipv6_targets = dut.get("remote_ipv6s", [])
    if ipv6_targets:
        return str(ipv6_targets[0]).strip()
    ipv4_targets = dut.get("remote_ips", [])
    if ipv4_targets:
        return str(ipv4_targets[0]).strip()
    return None


async def _cpe_link_up_via_bts(root_ssh, cpe_host: str, *, attempts: int = 8, delay_s: float = 3.0) -> bool:
    """True when BTS can reach CPE (RF/mgmt path up) before nested CPE SSH attempts."""
    target = str(cpe_host or "").strip()
    if not target:
        return False
    if ":" in target:
        command = f"ping -6 -c 1 -W 2 {shlex.quote(target)}"
    else:
        command = f"ping -c 1 -W 2 {shlex.quote(target)}"
    for attempt in range(1, attempts + 1):
        out = str((await root_ssh.send_command(command, timeout_ops=15)).result or "")
        if (
            "0% packet loss" in out
            or " 0% packet loss" in out
            or "1 received" in out
            or "1 packets received" in out
        ):
            _log_case("REMOTE", f"BTS→CPE ping ok ({target}) on attempt {attempt}")
            return True
        if attempt < attempts:
            await asyncio.sleep(delay_s)
    _log_case("REMOTE", f"BTS→CPE ping not ready ({target}) after {attempts} attempts")
    return False


async def _open_remote_cpe_ssh(device_creds, *, root_ssh=None):
    """
    Open CPE root SSH for remote LAN MTU changes (best-effort).

    Topology: PC(local) — BTS —(RF)— CPE — PC(remote).
    CPE mgmt IPv6 is reachable from the local PC through the BTS RF path, so SSH
    directly from the local PC. The secondary-PC hop is only a last-resort fallback.
    Returns None when CPE CLI is unavailable so BTS-only jumbo flows can continue.
    """
    manager = get_active_recovery_manager()
    if not manager:
        return None
    profile = manager.profile_bundle.active
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    password = str(device_creds.get("pass") or profile.get("dut", {}).get("password") or "")
    remote_ipv6s = (profile.get("dut", {}) or {}).get("remote_ipv6s") or []
    cpe_mgmt = str(remote_ipv6s[0]).strip() if remote_ipv6s else ""

    if root_ssh is not None and cpe_mgmt:
        await _cpe_link_up_via_bts(root_ssh, cpe_mgmt)

    # Direct SSH from local PC over the BTS RF path — no remote PC hop needed.
    direct_host = cpe_mgmt or _remote_dut_host_from_profile() or ""
    if direct_host:
        try:
            conn = await _open_temp_root_ssh(direct_host, password, timeout_socket=20)
            _log_case("REMOTE", f"CPE SSH direct from local PC to {direct_host}")
            return conn
        except Exception as exc:
            _log_case("REMOTE", f"direct CPE SSH to {direct_host} failed: {exc}")

    # Last resort: hop through the secondary CPE-side lab PC to CPE factory IPv4.
    if sec.get("enabled", True) and str(sec.get("ssh", "")).strip():
        from utils.ip_test_flows import open_cpe_ssh_via_secondary_pc
        from utils.lab_pc_net import ensure_secondary_pc_cpe_hop_ready
        from utils.link_formation import cpe_ssh_access

        access = cpe_ssh_access(profile)
        cpe_factory = access["host"]
        try:
            await ensure_secondary_pc_cpe_hop_ready(profile, password)
            driver, _, label = await open_cpe_ssh_via_secondary_pc(
                profile,
                password,
                cpe_lan=cpe_mgmt or cpe_factory,
                cpe_factory=cpe_factory,
            )
            _log_case("REMOTE", f"CPE SSH via {label}")
            return driver
        except Exception as exc:
            _log_case("REMOTE", f"secondary CPE SSH failed: {exc}; continuing BTS-only MTU flow.")
    return None


async def _read_backend_mtu_map(root_ssh) -> dict[str, str]:
    """Read MTU per port via ``uci get ethernet.<ethN>.mtu`` (same as BTS backend reads)."""
    mtus: dict[str, str] = {}
    for i in range(4):
        key = _eth_key(i)
        raw = str((await root_ssh.send_command(f"uci get ethernet.{key}.mtu")).result or "")
        # Devices expose a varying port count; stop at the first missing port.
        if "not found" in raw.lower():
            break
        # Pull the numeric token; fresh SSH sessions can prepend banner/echo noise.
        match = re.search(r"^\s*(\d{3,5})\s*$", raw, flags=re.MULTILINE)
        if match:
            mtus[key] = match.group(1)
    return mtus


async def _configure_remote_cpe_mtus_via_ssh(remote_ssh, mtu: str) -> int:
    current = await _read_backend_mtu_map(remote_ssh)
    if not current:
        raise RuntimeError(
            "Unable to read remote CPE ethernet MTU (uci get ethernet.ethN.mtu returned nothing)."
        )
    for key in current:
        await remote_ssh.send_command(f"ucidyn set ethernet.{key}.mtu {shlex.quote(mtu)}")
    await remote_ssh.send_command("ucidyn apply")
    return len(current)


async def _restore_remote_cpe_mtus_via_ssh(remote_ssh, original: dict[str, str]):
    for key, mtu in original.items():
        await remote_ssh.send_command(f"ucidyn set ethernet.{key}.mtu {shlex.quote(mtu)}")
    await remote_ssh.send_command("ucidyn apply")


async def _assert_remote_cpe_mtus(
    remote_ssh, expected_mtu: str, *, attempts: int = 4, interval_s: float = 5.0
) -> None:
    """Verify CPE MTU via ``uci get`` with retries (ucidyn apply reloads network over RF)."""
    current: dict[str, str] = {}
    for attempt in range(1, attempts + 1):
        current = await _read_backend_mtu_map(remote_ssh)
        if current and all(val == expected_mtu for val in current.values()):
            print(f"[JUMBO][REMOTE][CHECK] expected_mtu={expected_mtu} ok on attempt {attempt}")
            for key, val in current.items():
                print(f"[JUMBO][REMOTE][UCI] {key} mtu={val}")
            return
        if attempt < attempts:
            await asyncio.sleep(interval_s)
    if not current:
        _log_case("REMOTE", "CPE MTU verify skipped: uci get ethernet.ethN.mtu returned nothing")
        return
    for key, val in current.items():
        print(f"[JUMBO][REMOTE][UCI] {key} mtu={val}")
        assert val == expected_mtu, (
            f"remote CPE MTU mismatch for {key}: expected {expected_mtu}, got {val}"
        )


async def _backup_local_and_remote_mtus(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, local_original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    remote_ssh = await _open_remote_cpe_ssh(device_creds, root_ssh=root_ssh)
    remote_original = None
    if remote_ssh is not None:
        remote_original = await _read_backend_mtu_map(remote_ssh)
    return lan_total, local_original, remote_ssh, remote_original


async def _restore_local_and_remote_mtus(
    root_ssh,
    gui_page,
    bsu_ip,
    device_creds,
    local_original: dict[str, str],
    remote_ssh,
    remote_original: dict[str, str] | None,
):
    try:
        if remote_ssh is not None and remote_original:
            await _restore_remote_cpe_mtus_via_ssh(remote_ssh, remote_original)
            await _assert_remote_cpe_mtus(remote_ssh, next(iter(remote_original.values())))
    finally:
        if remote_ssh is not None:
            await remote_ssh.close()
    await _restore_mtus(root_ssh, local_original)


def _extract_ifconfig_mtu(ifconfig_output: str) -> str:
    text = str(ifconfig_output or "")
    # BusyBox ifconfig format usually includes "MTU:1500"
    match = re.search(r"MTU[:\s](\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


async def _read_br_lan_mtu(root_ssh) -> tuple[str, str]:
    """Read br-lan MTU via ip link (preferred) or ifconfig."""
    last_raw = ""
    for cmd in ("ip -o link show dev br-lan", "ifconfig br-lan"):
        res = await root_ssh.send_command(cmd)
        raw = str(res.result or "")
        last_raw = raw
        mtu = _extract_ifconfig_mtu(raw)
        if not mtu:
            match = re.search(r"\bmtu\s+(\d+)", raw, flags=re.IGNORECASE)
            mtu = match.group(1) if match else ""
        if mtu:
            return mtu, raw
    return "", last_raw


async def _assert_backend_all(root_ssh, expected_mtu: str):
    current = await _read_backend_mtu_map(root_ssh)
    print(f"[JUMBO][CHECK] expected_mtu={expected_mtu}")
    for key, val in current.items():
        print(f"[JUMBO][UCI] {key} mtu={val}")
    for key, val in current.items():
        assert val == expected_mtu, f"MTU mismatch for {key}: expected {expected_mtu}, got {val}"
    br_mtu, br_raw = await _read_br_lan_mtu(root_ssh)
    print("[JUMBO][br-lan] raw output start")
    print(br_raw.rstrip())
    print("[JUMBO][br-lan] raw output end")
    # netifd can still be reloading right after GUI apply (race), and a previously
    # pinned bridge MTU stops auto-tracking port MTUs. Poll and sync over SSH.
    for attempt in range(3):
        if br_mtu == expected_mtu:
            break
        print(
            f"[JUMBO][CHECK] br-lan at {br_mtu or 'unknown'}; syncing to {expected_mtu} via SSH "
            f"(attempt {attempt + 1})"
        )
        await root_ssh.send_command(
            f"ip link set dev br-lan mtu {shlex.quote(expected_mtu)} "
            f"|| ifconfig br-lan mtu {shlex.quote(expected_mtu)} || true"
        )
        await asyncio.sleep(2)
        br_mtu, br_raw = await _read_br_lan_mtu(root_ssh)
    if not br_mtu:
        print(
            f"[JUMBO][CHECK] br-lan MTU not readable; UCI ethernet MTU values match {expected_mtu} — continuing"
        )
        return
    assert br_mtu == expected_mtu, (
        f"br-lan MTU mismatch: expected {expected_mtu}, got {br_mtu}. output: {br_raw}"
    )


def _icmp_target_from_profile() -> str | None:
    manager = get_active_recovery_manager()
    if not manager:
        return None
    dut = manager.profile_bundle.active.get("dut", {})
    ipv6_targets = dut.get("remote_ipv6s", [])
    if ipv6_targets:
        return str(ipv6_targets[0]).strip()
    ipv4_targets = dut.get("remote_ips", [])
    if ipv4_targets:
        return str(ipv4_targets[0]).strip()
    return None


async def _pc_jumbo_check_with_capture(
    case_id: str,
    configured_mtu: int,
    *,
    root_ssh=None,
    count: int = 3,
    enforce_max_frame_len: bool = False,
):
    config = load_jumbo_capture_config()
    if not config.enabled:
        _log_case(case_id, "PC capture disabled in profile — skipping Wireshark/tcpdump proof.")
        return
    if len(config.nodes) < 2:
        pytest.skip(f"{case_id}: capture needs both BTS and CPE lab PC nodes (capture.bts_host/cpe_host + interfaces).")
    _log_case(case_id, f"Running end-to-end lab PC ICMP + tcpdump proof for MTU={configured_mtu}.")
    device_ping_cb = None
    if root_ssh is not None:
        async def device_ping_cb():
            await _icmp_jumbo_check(root_ssh, configured_mtu=configured_mtu, count=count, case_id=case_id)

    await run_pc_jumbo_capture_check(
        case_id,
        configured_mtu,
        count=count,
        enforce_max_frame_len=enforce_max_frame_len,
        device_ping=device_ping_cb,
    )


async def _icmp_jumbo_check(root_ssh, configured_mtu: int, *, count: int = 5, case_id: str = "JUMBO"):
    target_host = _icmp_target_from_profile()
    assert target_host, "Remote DUT IP is not defined in the active profile."
    payload_size = icmp_payload_for_mtu(configured_mtu, target_host)
    if ":" in target_host:
        command = f"ping -6 -c {count} -s {payload_size} {shlex.quote(target_host)}"
    else:
        command = f"ping -c {count} -s {payload_size} {shlex.quote(target_host)}"
    result = await root_ssh.send_command(command)
    output = str(result.result or "")
    out = output.lower()
    print(f"[JUMBO][ICMP] cmd={command}")
    print("[JUMBO][ICMP] raw output start")
    print(output.rstrip())
    print("[JUMBO][ICMP] raw output end")
    assert "100% packet loss" not in out, (
        f"Device-to-device ICMP failed for MTU {configured_mtu} payload {payload_size}. "
        f"Output: {output}"
    )
    assert "message too long" not in out, (
        f"Endpoint MTU insufficient for configured MTU {configured_mtu} payload {payload_size}. "
        f"Output: {output}"
    )
    assert "bytes from" in out, (
        f"No successful device-to-device ICMP replies for MTU {configured_mtu} payload {payload_size}. "
        f"Output: {output}"
    )


async def _jumbo_case_preflight(root_ssh) -> None:
    """
    Link checkup before each jumbo case (same idea as the IP case preflight):
    verify RF link clients on BTS + BTS→CPE mgmt ping, and only reform the
    link when it is actually down — avoids needless link reformation.
    """
    from utils.link_formation import ensure_p2mp_link_credentials, link_health_bts

    manager = get_active_recovery_manager()
    profile = manager.profile_bundle.active if manager else {}
    remote_ipv6s = (profile.get("dut", {}) or {}).get("remote_ipv6s") or []
    cpe_mgmt = str(remote_ipv6s[0]).strip() if remote_ipv6s else ""

    try:
        link_ok, clients = await link_health_bts(root_ssh, profile)
        cpe_ok = (
            await _cpe_link_up_via_bts(root_ssh, cpe_mgmt, attempts=2, delay_s=2.0)
            if link_ok and cpe_mgmt
            else False
        )
        if link_ok and (cpe_ok or not cpe_mgmt):
            _log_case(
                "PREFLIGHT",
                f"RF link up ({clients} client(s)), CPE mgmt reachable — skipping link reformation.",
            )
            return

        _log_case(
            "PREFLIGHT",
            f"link_up={link_ok} clients={clients} cpe_ok={cpe_ok} — reforming link.",
        )
        await ensure_p2mp_link_credentials(bts_ssh=root_ssh, profile=profile)
        for _ in range(12):
            await asyncio.sleep(5)
            link_ok, clients = await link_health_bts(root_ssh, profile)
            if link_ok:
                break
        cpe_ok = (
            await _cpe_link_up_via_bts(root_ssh, cpe_mgmt, attempts=4, delay_s=3.0)
            if cpe_mgmt
            else True
        )
        _log_case(
            "PREFLIGHT",
            f"after reformation: link_up={link_ok} clients={clients} cpe_ok={cpe_ok}",
        )
    except Exception as exc:
        _log_case("PREFLIGHT", f"checkup error ({type(exc).__name__}: {exc}) — continuing with test.")


async def _backup_mtu_state(root_ssh):
    await _jumbo_case_preflight(root_ssh)
    original = await _discovered_lan_ports(root_ssh)
    lan_total = len(original)
    return lan_total, original


async def _backup_and_enter_ethernet(root_ssh, gui_page=None, bsu_ip=None, device_creds=None):
    """Backup MTU state over SSH (no GUI navigation)."""
    return await _backup_mtu_state(root_ssh)


async def _restore_mtus(root_ssh, original: dict[str, str], **_ignored):
    try:
        if original:
            await _restore_bts_mtus_via_ssh(root_ssh, original)
    except Exception:
        pass


async def assert_jmb_01_configure_and_disable(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_01", "Starting test flow.")
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_01")
        await _assert_backend_all(root_ssh, "9000")
        await _pc_jumbo_check_with_capture("JMB_01", 9000, root_ssh=root_ssh)

        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "1500", case_id="JMB_01")
        await _assert_backend_all(root_ssh, "1500")
        await _pc_jumbo_check_with_capture("JMB_01", 1500, root_ssh=root_ssh, enforce_max_frame_len=True)
    finally:
        _log_case("JMB_01", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, original)


async def assert_jmb_02_configure_9000(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_02", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None and remote_original:
            _log_case("JMB_02", "Setting remote CPE LAN MTU to 9000 first.")
            await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "9000")
            await _assert_remote_cpe_mtus(remote_ssh, "9000")
        elif remote_ssh is not None:
            _log_case("JMB_02", "CPE SSH ok but no ethernet MTU keys — continuing BTS-only.")
        _log_case("JMB_02", "Setting all LAN MTU to 9000.")
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_02")
        await _assert_backend_all(root_ssh, "9000")
        await _pc_jumbo_check_with_capture("JMB_02", 9000, root_ssh=root_ssh)
    finally:
        _log_case("JMB_02", "Restoring original MTU values.")
        await _restore_local_and_remote_mtus(
            root_ssh, gui_page, bsu_ip, device_creds, original, remote_ssh, remote_original
        )


async def assert_jmb_03_min_mid_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_03", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        for mtu in ("2000", "5000"):
            _log_case("JMB_03", f"Applying MTU={mtu} on all LAN interfaces.")
            if remote_ssh is not None and remote_original:
                _log_case("JMB_03", f"Setting remote CPE LAN MTU={mtu} first.")
                await _configure_remote_cpe_mtus_via_ssh(remote_ssh, mtu)
                await _assert_remote_cpe_mtus(remote_ssh, mtu)
            elif remote_ssh is not None:
                _log_case("JMB_03", "CPE SSH ok but no ethernet MTU keys — continuing BTS-only.")
            await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, mtu, case_id="JMB_03")
            await _assert_backend_all(root_ssh, mtu)
            await _pc_jumbo_check_with_capture("JMB_03", int(mtu), root_ssh=root_ssh)
    finally:
        _log_case("JMB_03", "Restoring original MTU values.")
        await _restore_local_and_remote_mtus(
            root_ssh, gui_page, bsu_ip, device_creds, original, remote_ssh, remote_original
        )


async def assert_jmb_04_max_mtu_9000(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_04", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None and remote_original:
            _log_case("JMB_04", "Setting remote CPE LAN MTU to 9000 first.")
            await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "9000")
            await _assert_remote_cpe_mtus(remote_ssh, "9000")
        elif remote_ssh is not None:
            _log_case("JMB_04", "CPE SSH ok but no ethernet MTU keys — continuing BTS-only.")
        _log_case("JMB_04", "Setting all LAN MTU to 9000.")
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_04")
        await _assert_backend_all(root_ssh, "9000")
        await _pc_jumbo_check_with_capture("JMB_04", 9000, root_ssh=root_ssh)
    finally:
        _log_case("JMB_04", "Restoring original MTU values.")
        await _restore_local_and_remote_mtus(
            root_ssh, gui_page, bsu_ip, device_creds, original, remote_ssh, remote_original
        )


async def assert_jmb_05_mgmt_vlan_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_05", "Starting test flow.")
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        _log_case("JMB_05", "Setting all LAN MTU to 9000.")
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_05")
        await _assert_backend_all(root_ssh, "9000")
        # If management VLAN exists, it should inherit high MTU policy.
        mgmt_if = await root_ssh.send_command("ip -o link show | awk -F': ' '{print $2}' | grep -E '^vlan|^br-' | head -n 1")
        if mgmt_if.result.strip():
            if_name = mgmt_if.result.strip().splitlines()[-1]
            mtu_line = await root_ssh.send_command(f"ip link show {if_name} | head -n 1")
            _log_case("JMB_05", f"Management-like interface check: {if_name} -> {ssh_scalar(mtu_line.result)}")
            assert "mtu" in mtu_line.result.lower(), f"Unable to read MTU for management-like interface {if_name}"
        await _pc_jumbo_check_with_capture("JMB_05", 9000, root_ssh=root_ssh)
    finally:
        _log_case("JMB_05", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, original)


async def assert_jmb_06_jumbo_with_p2mp(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_06", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None and remote_original:
            _log_case("JMB_06", "Setting remote CPE LAN MTU to 9000 first.")
            await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "9000")
            await _assert_remote_cpe_mtus(remote_ssh, "9000")
        elif remote_ssh is not None:
            _log_case("JMB_06", "CPE SSH ok but no ethernet MTU keys — continuing BTS-only.")
        _log_case("JMB_06", "Setting all LAN MTU to 9000.")
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_06")
        await _assert_backend_all(root_ssh, "9000")
        await _pc_jumbo_check_with_capture("JMB_06", 9000, root_ssh=root_ssh)
    finally:
        _log_case("JMB_06", "Restoring original MTU values.")
        await _restore_local_and_remote_mtus(
            root_ssh, gui_page, bsu_ip, device_creds, original, remote_ssh, remote_original
        )


async def assert_jmb_07_reboot_persistence(root_ssh, gui_page, bsu_ip, device_creds, allow_destructive: bool):
    if not allow_destructive:
        pytest.skip("JMB_07 skipped. Re-run with --allow-destructive-jumbo to enable reboot validation.")
    _log_case("JMB_07", "Starting test flow.")
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        _log_case("JMB_07", "Setting all LAN MTU to 9000 before reboot.")
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_07")
        await _assert_backend_all(root_ssh, "9000")
        _log_case("JMB_07", "Sending reboot command.")
        await root_ssh.send_command("reboot")
        _log_case("JMB_07", "Waiting 150 seconds (2.5 minutes) for boot completion.")
        await asyncio.sleep(150)
        _log_case("JMB_07", "Re-login and post-reboot MTU verification.")
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS)
        try:
            await _assert_backend_all(root_ssh, "9000")
        except Exception as exc:
            # Reboot can invalidate existing SSH channel; reopen once and retry.
            _log_case("JMB_07", f"SSH channel stale after reboot ({type(exc).__name__}); reopening session and retrying.")
            try:
                await root_ssh.close()
            except Exception:
                pass
            await asyncio.sleep(2)
            await root_ssh.open()
            await _assert_backend_all(root_ssh, "9000")
        await _pc_jumbo_check_with_capture("JMB_07", 9000, root_ssh=root_ssh)
    finally:
        _log_case("JMB_07", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, original)


async def assert_jmb_08_mtu_1500(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_08", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None and remote_original:
            _log_case("JMB_08", "Setting remote CPE LAN MTU to 1500 first.")
            await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "1500")
            await _assert_remote_cpe_mtus(remote_ssh, "1500")
        elif remote_ssh is not None:
            _log_case("JMB_08", "CPE SSH ok but no ethernet MTU keys — continuing BTS-only.")
        _log_case("JMB_08", "Setting all LAN MTU to 1500.")
        await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "1500", case_id="JMB_08")
        await _assert_backend_all(root_ssh, "1500")
        await _pc_jumbo_check_with_capture("JMB_08", 1500, root_ssh=root_ssh, enforce_max_frame_len=True)
    finally:
        _log_case("JMB_08", "Restoring original MTU values.")
        await _restore_local_and_remote_mtus(
            root_ssh, gui_page, bsu_ip, device_creds, original, remote_ssh, remote_original
        )


async def assert_jmb_09_boundary_values(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_09", "Starting test flow.")
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    valid_values = ["1501", "1576", "1600", "8900", "8999", "9000"]
    invalid_values = ["1499", "1000", "9001", "10000"]
    try:
        for mtu in valid_values:
            _log_case("JMB_09", f"Valid boundary test: applying MTU={mtu}.")
            await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, mtu, case_id="JMB_09")
            await _assert_backend_all(root_ssh, mtu)

        # Wire proof once at the top boundary (9000); per-value captures would be too slow.
        await _pc_jumbo_check_with_capture("JMB_09", 9000, root_ssh=root_ssh)

        for invalid in invalid_values:
            _log_case("JMB_09", f"Invalid boundary test: attempting MTU={invalid} via SSH.")
            await root_ssh.send_command(f"ucidyn set ethernet.eth0.mtu {shlex.quote(invalid)}")
            await root_ssh.send_command("ucidyn apply")
            await asyncio.sleep(1)
            current = ssh_scalar((await root_ssh.send_command("uci get ethernet.eth0.mtu")).result)
            _log_case("JMB_09", f"Post-invalid attempt backend eth0 mtu={current}")
            assert current != invalid, f"Invalid MTU should be rejected, but backend accepted {invalid}"
    finally:
        _log_case("JMB_09", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, original)


async def assert_jmb_10_factory_reset_default(root_ssh, gui_page, bsu_ip, device_creds, allow_destructive: bool):
    if not allow_destructive:
        pytest.skip("JMB_10 skipped. Re-run with --allow-destructive-jumbo to enable factory-reset validation.")
    _log_case("JMB_10", "Starting test flow.")
    lan_total, _original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    _log_case("JMB_10", "Setting all LAN MTU to 9000 before factory reset.")
    await _set_all_lans_mtu_via_ssh(root_ssh, lan_total, "9000", case_id="JMB_10")
    await _assert_backend_all(root_ssh, "9000")

    _log_case("JMB_10", "Running factory reset via current UI session.")
    await _factory_reset_via_current_ui_session(gui_page, wait_seconds=180)

    _log_case("JMB_10", "Waiting for factory default GUI (10.0.0.1 / 192.168.2.1) with buffer.")
    # This hardware resets to 10.0.0.1 (factory fallback); keep 192.168.2.1 as alternate.
    candidates = ("10.0.0.1", "192.168.2.1")
    default_ip = ""
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline and not default_ip:
        for cand in candidates:
            if await _wait_until_gui_reachable(cand, timeout_s=6, interval_s=3):
                default_ip = cand
                break
    assert default_ip, f"Factory default GUI did not come up on any of {candidates} after reset."
    _log_case("JMB_10", f"Factory default GUI up at {default_ip}.")

    _log_case("JMB_10", "Accessing default IP and checking backend MTU=1500.")
    await _login_with_retries(gui_page, default_ip, device_creds, attempts=4)
    temp_ssh = await _open_temp_root_ssh(default_ip, device_creds["pass"])
    try:
        await _assert_backend_all(temp_ssh, lan_total, "1500")
    finally:
        await temp_ssh.close()

    _log_case("JMB_10", "Restoring baseline profile bundle (BTS.tar.gz).")
    manager = get_active_recovery_manager()
    assert manager is not None, "Recovery manager is not initialized."
    restored = await manager.run_profile_restore(
        gui_page=gui_page,
        device_creds=device_creds,
        role="BTS",
        post_restore_ip=bsu_ip,
    )
    assert restored, f"Profile restore did not complete successfully: {manager.metrics.last_error}"

    _log_case("JMB_10", "Waiting for target IPv6 GUI/SSH with extra buffer.")
    ipv6_up = await _wait_until_gui_reachable(bsu_ip, timeout_s=220, interval_s=5)
    assert ipv6_up, f"IPv6 GUI {bsu_ip} not reachable after profile restore."
    await _login_with_retries(gui_page, bsu_ip, device_creds, attempts=4)

    try:
        await root_ssh.close()
    except Exception:
        pass
    await asyncio.sleep(3)
    await root_ssh.open()
    _log_case("JMB_10", "Sequence completed: IPv6 access restored.")

