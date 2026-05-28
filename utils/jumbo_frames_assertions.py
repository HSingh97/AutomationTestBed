"""Jumbo frame (JMB_01..JMB_10) assertion flows."""

from __future__ import annotations

import asyncio
import re
import shlex
import time

import pytest
from scrapli.driver.generic import AsyncGenericDriver

from pages.locators import EthernetLocators as EL, NetworkLocators as NL, UITimeouts
from traffic.packet_capture import icmp_payload_for_mtu
from utils.gui_login import login_if_needed
from utils.network_flows import navigate_to_ethernet
from utils.parsers import ssh_scalar
from utils.recovery_manager import get_active_recovery_manager


def _eth_key(idx: int) -> str:
    return f"eth{idx}"


def _log_case(case_id: str, message: str):
    print(f"[JUMBO][{case_id}] {message}")


async def _lan_count(gui_page) -> int:
    tabs = gui_page.locator(EL.LAN_TABS)
    await tabs.first.wait_for(state="visible", timeout=15000)
    return len(await tabs.all())


async def _ensure_ethernet_ready(gui_page):
    """
    Ensure Ethernet page tab menu is visible.
    Recovers from intermittent router/apply intermediate screens.
    """
    for _ in range(3):
        await navigate_to_ethernet(gui_page)
        tabs = gui_page.locator(EL.LAN_TABS).first
        if await tabs.is_visible(timeout=5000):
            return
        await gui_page.reload()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(1500)
    await gui_page.locator(EL.LAN_TABS).first.wait_for(state="visible", timeout=15000)


async def _apply(gui_page, settle_seconds=10):
    await gui_page.locator(EL.SAVE_BUTTON).first.click()
    await gui_page.wait_for_timeout(3000)
    apply_icon = gui_page.locator(NL.APPLY_ICON).first
    if await apply_icon.is_visible(timeout=5000):
        await apply_icon.click()
        await gui_page.wait_for_timeout(3000)
        confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
        # On some firmware/pages Apply commits directly without a confirm prompt.
        if await confirm_btn.is_visible(timeout=4000):
            await confirm_btn.click()
        await asyncio.sleep(settle_seconds)


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


async def _open_temp_root_ssh(host: str, password: str):
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
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


async def _set_mtu(gui_page, lan_idx: int, mtu: str):
    await gui_page.locator(EL.LAN_TABS).nth(lan_idx).click()
    await gui_page.wait_for_timeout(1500)
    field = gui_page.locator(EL.MTU_INPUT).first
    await field.wait_for(state="visible", timeout=10000)
    await field.fill(mtu)


async def _set_mtu_all_lans(gui_page, mtu: str):
    await _ensure_ethernet_ready(gui_page)
    lan_total = await _lan_count(gui_page)
    for i in range(lan_total):
        await _set_mtu(gui_page, i, mtu)


async def _read_backend_mtus(root_ssh, lan_total: int) -> dict[str, str]:
    mtus = {}
    for i in range(lan_total):
        key = _eth_key(i)
        cmd = f"uci get ethernet.{key}.mtu"
        mtus[key] = ssh_scalar((await root_ssh.send_command(cmd)).result)
    return mtus


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


async def _set_backend_mtus_via_ssh(root_ssh, lan_total: int, mtu: str):
    commands = []
    for i in range(lan_total):
        key = _eth_key(i)
        commands.append(f"uci set ethernet.{key}.mtu={shlex.quote(mtu)}")
        commands.append(f"ifconfig {key} mtu {shlex.quote(mtu)} || ip link set dev {key} mtu {shlex.quote(mtu)} || true")
    commands.append("uci commit ethernet")
    commands.append(f"ifconfig br-lan mtu {shlex.quote(mtu)} || ip link set dev br-lan mtu {shlex.quote(mtu)} || true")
    for command in commands:
        await root_ssh.send_command(command)


async def _restore_backend_mtus_via_ssh(root_ssh, original: dict[str, str]):
    commands = []
    for key, mtu in original.items():
        commands.append(f"uci set ethernet.{key}.mtu={shlex.quote(mtu)}")
        commands.append(f"ifconfig {key} mtu {shlex.quote(mtu)} || ip link set dev {key} mtu {shlex.quote(mtu)} || true")
    commands.append("uci commit ethernet")
    if original:
        first_mtu = next(iter(original.values()))
        commands.append(
            f"ifconfig br-lan mtu {shlex.quote(first_mtu)} || ip link set dev br-lan mtu {shlex.quote(first_mtu)} || true"
        )
    for command in commands:
        await root_ssh.send_command(command)


async def _open_remote_cpe_ssh(device_creds):
    remote_host = _remote_dut_host_from_profile()
    if not remote_host:
        return None
    return await _open_temp_root_ssh(remote_host, device_creds["pass"])


async def _read_backend_mtu_map(root_ssh) -> dict[str, str]:
    result = await root_ssh.send_command("uci show ethernet")
    text = str(result.result or "")
    keys = sorted(set(re.findall(r"ethernet\.(eth\d+)\.mtu=", text)))
    mtus: dict[str, str] = {}
    for key in keys:
        mtu = ssh_scalar((await root_ssh.send_command(f"uci get ethernet.{key}.mtu")).result)
        if mtu:
            mtus[key] = mtu
    return mtus


async def _configure_remote_cpe_mtus_via_ssh(remote_ssh, mtu: str) -> int:
    current = await _read_backend_mtu_map(remote_ssh)
    if not current:
        raise RuntimeError("Unable to discover remote CPE ethernet MTU keys over SSH.")
    for key in current:
        await remote_ssh.send_command(f"ucidyn set ethernet.{key}.mtu {shlex.quote(mtu)}")
    await remote_ssh.send_command("ucidyn apply")
    return len(current)


async def _restore_remote_cpe_mtus_via_ssh(remote_ssh, original: dict[str, str]):
    for key, mtu in original.items():
        await remote_ssh.send_command(f"ucidyn set ethernet.{key}.mtu {shlex.quote(mtu)}")
    await remote_ssh.send_command("ucidyn apply")


async def _backup_local_and_remote_mtus(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, local_original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    remote_ssh = await _open_remote_cpe_ssh(device_creds)
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
            await _assert_backend_all(remote_ssh, len(remote_original), next(iter(remote_original.values())))
    finally:
        if remote_ssh is not None:
            await remote_ssh.close()
    await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, local_original)


def _extract_ifconfig_mtu(ifconfig_output: str) -> str:
    text = str(ifconfig_output or "")
    # BusyBox ifconfig format usually includes "MTU:1500"
    match = re.search(r"MTU[:\s](\d+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


async def _read_br_lan_ifconfig(root_ssh) -> tuple[str, str]:
    # Keep raw output for runtime evidence and parse MTU from it.
    res = await root_ssh.send_command("ifconfig br-lan")
    raw = str(res.result or "")
    parsed = _extract_ifconfig_mtu(raw)
    return parsed, raw


async def _assert_backend_all(root_ssh, lan_total: int, expected_mtu: str):
    current = await _read_backend_mtus(root_ssh, lan_total)
    print(f"[JUMBO][CHECK] expected_mtu={expected_mtu}")
    for key, val in current.items():
        print(f"[JUMBO][UCI] {key} mtu={val}")
    for key, val in current.items():
        assert val == expected_mtu, f"MTU mismatch for {key}: expected {expected_mtu}, got {val}"
    br_mtu, br_raw = await _read_br_lan_ifconfig(root_ssh)
    print("[JUMBO][IFCONFIG][br-lan] raw output start")
    print(br_raw.rstrip())
    print("[JUMBO][IFCONFIG][br-lan] raw output end")
    assert br_mtu, f"Unable to parse MTU from ifconfig br-lan output: {br_raw}"
    assert br_mtu == expected_mtu, f"br-lan MTU mismatch: expected {expected_mtu}, got {br_mtu}. ifconfig: {br_raw}"


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


async def _icmp_jumbo_check(root_ssh, configured_mtu: int, *, count: int = 5, case_id: str = "JUMBO"):
    _ = case_id
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


async def _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds):
    await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.MEDIUM_WAIT_MS)
    await _ensure_ethernet_ready(gui_page)
    lan_total = await _lan_count(gui_page)
    original = await _read_backend_mtus(root_ssh, lan_total)
    return lan_total, original


async def _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original: dict[str, str]):
    try:
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.MEDIUM_WAIT_MS)
        await _ensure_ethernet_ready(gui_page)
        for i, (_key, mtu) in enumerate(original.items()):
            await _set_mtu(gui_page, i, mtu)
        await _apply(gui_page, settle_seconds=8)
    except Exception:
        pass


async def assert_jmb_01_configure_and_disable(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_01", "Starting test flow.")
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        _log_case("JMB_01", "Setting all LAN MTU to 9000.")
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")

        await _ensure_ethernet_ready(gui_page)
        _log_case("JMB_01", "Setting all LAN MTU to 1500.")
        await _set_mtu_all_lans(gui_page, "1500")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "1500")
    finally:
        _log_case("JMB_01", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_02_configure_9000(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_02", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None:
            _log_case("JMB_02", "Setting remote CPE LAN MTU to 9000 first.")
            remote_lan_total = await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "9000")
            await _assert_backend_all(remote_ssh, remote_lan_total, "9000")
        _log_case("JMB_02", "Setting all LAN MTU to 9000.")
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
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
            if remote_ssh is not None:
                _log_case("JMB_03", f"Setting remote CPE LAN MTU={mtu} first.")
                remote_lan_total = await _configure_remote_cpe_mtus_via_ssh(remote_ssh, mtu)
                await _assert_backend_all(remote_ssh, remote_lan_total, mtu)
            await _ensure_ethernet_ready(gui_page)
            await _set_mtu_all_lans(gui_page, mtu)
            await _apply(gui_page, settle_seconds=8)
            await _assert_backend_all(root_ssh, lan_total, mtu)
            _log_case("JMB_03", f"Running device-to-device ICMP validation for MTU={mtu}.")
            await _icmp_jumbo_check(root_ssh, configured_mtu=int(mtu), case_id="JMB_03")
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
        if remote_ssh is not None:
            _log_case("JMB_04", "Setting remote CPE LAN MTU to 9000 first.")
            remote_lan_total = await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "9000")
            await _assert_backend_all(remote_ssh, remote_lan_total, "9000")
        _log_case("JMB_04", "Setting all LAN MTU to 9000.")
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
        _log_case("JMB_04", "Running device-to-device ICMP validation for MTU=9000.")
        await _icmp_jumbo_check(root_ssh, configured_mtu=9000, case_id="JMB_04")
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
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
        # If management VLAN exists, it should inherit high MTU policy.
        mgmt_if = await root_ssh.send_command("ip -o link show | awk -F': ' '{print $2}' | grep -E '^vlan|^br-' | head -n 1")
        if mgmt_if.result.strip():
            if_name = mgmt_if.result.strip().splitlines()[-1]
            mtu_line = await root_ssh.send_command(f"ip link show {if_name} | head -n 1")
            _log_case("JMB_05", f"Management-like interface check: {if_name} -> {ssh_scalar(mtu_line.result)}")
            assert "mtu" in mtu_line.result.lower(), f"Unable to read MTU for management-like interface {if_name}"
    finally:
        _log_case("JMB_05", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_06_jumbo_with_p2mp(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_06", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None:
            _log_case("JMB_06", "Setting remote CPE LAN MTU to 9000 first.")
            remote_lan_total = await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "9000")
            await _assert_backend_all(remote_ssh, remote_lan_total, "9000")
        _log_case("JMB_06", "Setting all LAN MTU to 9000.")
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
        _log_case("JMB_06", "Running device-to-device ICMP validation for MTU=9000 (same as JMB_04).")
        await _icmp_jumbo_check(root_ssh, configured_mtu=9000, case_id="JMB_06")
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
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page, settle_seconds=8)
        _log_case("JMB_07", "Sending reboot command.")
        await root_ssh.send_command("reboot")
        _log_case("JMB_07", "Waiting 150 seconds (2.5 minutes) for boot completion.")
        await asyncio.sleep(150)
        _log_case("JMB_07", "Re-login and post-reboot MTU verification.")
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS)
        try:
            await _assert_backend_all(root_ssh, lan_total, "9000")
        except Exception as exc:
            # Reboot can invalidate existing SSH channel; reopen once and retry.
            _log_case("JMB_07", f"SSH channel stale after reboot ({type(exc).__name__}); reopening session and retrying.")
            try:
                await root_ssh.close()
            except Exception:
                pass
            await asyncio.sleep(2)
            await root_ssh.open()
            await _assert_backend_all(root_ssh, lan_total, "9000")
    finally:
        _log_case("JMB_07", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_08_mtu_1500(root_ssh, gui_page, bsu_ip, device_creds):
    _log_case("JMB_08", "Starting test flow.")
    lan_total, original, remote_ssh, remote_original = await _backup_local_and_remote_mtus(
        root_ssh, gui_page, bsu_ip, device_creds
    )
    try:
        if remote_ssh is not None:
            _log_case("JMB_08", "Setting remote CPE LAN MTU to 1500 first.")
            remote_lan_total = await _configure_remote_cpe_mtus_via_ssh(remote_ssh, "1500")
            await _assert_backend_all(remote_ssh, remote_lan_total, "1500")
        _log_case("JMB_08", "Setting all LAN MTU to 1500.")
        await _set_mtu_all_lans(gui_page, "1500")
        await _apply(gui_page, settle_seconds=6)
        await _assert_backend_all(root_ssh, lan_total, "1500")
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
            await _set_mtu_all_lans(gui_page, mtu)
            await _apply(gui_page, settle_seconds=6)
            await _assert_backend_all(root_ssh, lan_total, mtu)

        for invalid in invalid_values:
            _log_case("JMB_09", f"Invalid boundary test: attempting MTU={invalid}.")
            await _ensure_ethernet_ready(gui_page)
            await _set_mtu(gui_page, 0, invalid)
            await _apply(gui_page, settle_seconds=4)
            current = ssh_scalar((await root_ssh.send_command("uci get ethernet.eth0.mtu")).result)
            _log_case("JMB_09", f"Post-invalid attempt backend eth0 mtu={current}")
            assert current != invalid, f"Invalid MTU should be rejected, but backend accepted {invalid}"
    finally:
        _log_case("JMB_09", "Restoring original MTU values.")
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_10_factory_reset_default(root_ssh, gui_page, bsu_ip, device_creds, allow_destructive: bool):
    if not allow_destructive:
        pytest.skip("JMB_10 skipped. Re-run with --allow-destructive-jumbo to enable factory-reset validation.")
    _log_case("JMB_10", "Starting test flow.")
    lan_total, _original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    _log_case("JMB_10", "Setting all LAN MTU to 9000 before factory reset.")
    await _set_mtu_all_lans(gui_page, "9000")
    await _apply(gui_page, settle_seconds=8)
    await _assert_backend_all(root_ssh, lan_total, "9000")

    _log_case("JMB_10", "Running factory reset via current UI session.")
    await _factory_reset_via_current_ui_session(gui_page, wait_seconds=180)

    _log_case("JMB_10", "Waiting for default IP GUI (192.168.2.1) with buffer.")
    default_ip = "192.168.2.1"
    default_up = await _wait_until_gui_reachable(default_ip, timeout_s=200, interval_s=5)
    assert default_up, "Default GUI 192.168.2.1 did not come up after factory reset."

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

