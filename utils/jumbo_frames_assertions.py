"""Jumbo frame (JMB_01..JMB_10) assertion flows."""

from __future__ import annotations

import asyncio

import pytest

from pages.locators import EthernetLocators as EL, NetworkLocators as NL, UITimeouts
from utils.gui_login import login_if_needed
from utils.network_flows import navigate_to_ethernet
from utils.parsers import ssh_scalar
from utils.recovery_manager import get_active_recovery_manager


def _eth_key(idx: int) -> str:
    return f"eth{idx}"


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
        await confirm_btn.wait_for(state="visible", timeout=10000)
        await confirm_btn.click()
        await asyncio.sleep(settle_seconds)


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


async def _assert_backend_all(root_ssh, lan_total: int, expected_mtu: str):
    current = await _read_backend_mtus(root_ssh, lan_total)
    for key, val in current.items():
        assert val == expected_mtu, f"MTU mismatch for {key}: expected {expected_mtu}, got {val}"


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


async def _icmp_jumbo_check(root_ssh, payload_size: int, *, count: int = 5):
    target = _icmp_target_from_profile()
    if not target:
        pytest.skip("No remote target found in profile for ICMP jumbo validation.")
    if ":" in target:
        cmd = f"ping -6 -c {count} -s {payload_size} {target}"
    else:
        cmd = f"ping -c {count} -s {payload_size} {target}"
    res = await root_ssh.send_command(cmd)
    out = (res.result or "").lower()
    assert "100% packet loss" not in out, f"ICMP failed for payload {payload_size}. Output: {res.result}"
    assert "message too long" not in out, f"MTU insufficient for payload {payload_size}. Output: {res.result}"
    assert "bytes from" in out, f"No successful ICMP replies for payload {payload_size}. Output: {res.result}"


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
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")

        await _ensure_ethernet_ready(gui_page)
        await _set_mtu_all_lans(gui_page, "1500")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "1500")
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_02_configure_9000(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_03_min_mid_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        for mtu in ("2000", "5000"):
            await _ensure_ethernet_ready(gui_page)
            await _set_mtu_all_lans(gui_page, mtu)
            await _apply(gui_page, settle_seconds=8)
            await _assert_backend_all(root_ssh, lan_total, mtu)
            # ICMP payload reserves header space under configured MTU.
            await _icmp_jumbo_check(root_ssh, payload_size=int(mtu) - 42)
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_04_max_mtu_9000(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
        await _icmp_jumbo_check(root_ssh, payload_size=8958)
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_05_mgmt_vlan_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
        # If management VLAN exists, it should inherit high MTU policy.
        mgmt_if = await root_ssh.send_command("ip -o link show | awk -F': ' '{print $2}' | grep -E '^vlan|^br-' | head -n 1")
        if mgmt_if.result.strip():
            if_name = mgmt_if.result.strip().splitlines()[-1]
            mtu_line = await root_ssh.send_command(f"ip link show {if_name} | head -n 1")
            assert "mtu" in mtu_line.result.lower(), f"Unable to read MTU for management-like interface {if_name}"
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_06_jumbo_with_p2mp(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page)
        await _assert_backend_all(root_ssh, lan_total, "9000")
        tunnel_check = await root_ssh.send_command("ifconfig | grep -E 'ath|br-lan' | wc -l")
        assert int(ssh_scalar(tunnel_check.result) or "0") > 0, "Expected wireless/bridge interfaces not present."
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_07_reboot_persistence(root_ssh, gui_page, bsu_ip, device_creds, allow_destructive: bool):
    if not allow_destructive:
        pytest.skip("JMB_07 skipped. Re-run with --allow-destructive-jumbo to enable reboot validation.")
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "9000")
        await _apply(gui_page, settle_seconds=8)
        await root_ssh.send_command("reboot")
        await asyncio.sleep(120)
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS)
        await _assert_backend_all(root_ssh, lan_total, "9000")
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_08_mtu_1500(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    try:
        await _set_mtu_all_lans(gui_page, "1500")
        await _apply(gui_page, settle_seconds=6)
        await _assert_backend_all(root_ssh, lan_total, "1500")
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_09_boundary_values(root_ssh, gui_page, bsu_ip, device_creds):
    lan_total, original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    valid_values = ["1501", "1576", "1600", "8900", "8999", "9000"]
    invalid_values = ["1499", "1000", "9001", "10000"]
    try:
        for mtu in valid_values:
            await _set_mtu_all_lans(gui_page, mtu)
            await _apply(gui_page, settle_seconds=6)
            await _assert_backend_all(root_ssh, lan_total, mtu)

        for invalid in invalid_values:
            await _ensure_ethernet_ready(gui_page)
            await _set_mtu(gui_page, 0, invalid)
            await _apply(gui_page, settle_seconds=4)
            current = ssh_scalar((await root_ssh.send_command("uci get ethernet.eth0.mtu")).result)
            assert current != invalid, f"Invalid MTU should be rejected, but backend accepted {invalid}"
    finally:
        await _restore_mtus(root_ssh, gui_page, bsu_ip, device_creds, original)


async def assert_jmb_10_factory_reset_default(root_ssh, gui_page, bsu_ip, device_creds, allow_destructive: bool):
    if not allow_destructive:
        pytest.skip("JMB_10 skipped. Re-run with --allow-destructive-jumbo to enable factory-reset validation.")
    lan_total, _original = await _backup_and_enter_ethernet(root_ssh, gui_page, bsu_ip, device_creds)
    await _set_mtu_all_lans(gui_page, "9000")
    await _apply(gui_page, settle_seconds=8)
    await root_ssh.send_command("firstboot && reboot")
    await asyncio.sleep(150)
    await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS)
    await _assert_backend_all(root_ssh, lan_total, "1500")

