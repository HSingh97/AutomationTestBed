from __future__ import annotations

import asyncio
from datetime import datetime
import re
from typing import Any

from playwright.async_api import async_playwright
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from pages.locators import CommonLocators, ManagementLocators, MonitorLocators, UITimeouts
from utils.gui_login import login_if_needed
from utils.management_flows import open_management_system
from utils.parsers import (
    normalize_mac_address,
    normalize_monitor_interface_label,
    parse_arp_entries,
    parse_bridge_fdb_entries,
    parse_monitor_log_lines,
)
from utils.recovery_manager import get_active_recovery_manager

BRIDGE_FILTERS = (
    ("0", "All"),
    ("1", "LAN 1"),
    ("2", "LAN 2"),
    ("3", "Radio 1"),
)

SYSTEM_LOG_TABS = {
    "Configuration": MonitorLocators.LOG_CONFIG_TAB,
    "Device": MonitorLocators.LOG_DEVICE_TAB,
    "Temperature": MonitorLocators.LOG_TEMPERATURE_TAB,
    "System": MonitorLocators.LOG_SYSTEM_TAB,
}


def _log(role: str, message: str):
    print(f"[MONITOR][{role}] {message}")


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


async def _open_remote_monitor_target(gui_page, host: str, device_creds: dict[str, str]):
    delays = (0, 5, 10)
    last_exc = None
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        remote_playwright = None
        remote_browser = None
        remote_context = None
        try:
            remote_playwright = await async_playwright().start()
            remote_browser = await remote_playwright.chromium.launch(headless=True)
            remote_context = await remote_browser.new_context(ignore_https_errors=True)
            remote_page = await remote_context.new_page()
            await login_if_needed(
                remote_page,
                host,
                device_creds,
                wait_ms=UITimeouts.LONG_WAIT_MS,
                skip_recovery=True,
            )
            remote_ssh = await _open_temp_root_ssh(host, device_creds["pass"])
            return remote_page, remote_ssh, remote_context, remote_browser, remote_playwright
        except Exception as exc:
            last_exc = exc
            try:
                if 'remote_page' in locals():
                    await remote_page.close()
            finally:
                if remote_context is not None:
                    await remote_context.close()
                if remote_browser is not None:
                    await remote_browser.close()
                if remote_playwright is not None:
                    await remote_playwright.stop()
    raise last_exc if last_exc else RuntimeError(f"Unable to open remote monitor target {host}.")


async def _send_command_with_retry(root_ssh, command: str, *, attempts: int = 2):
    last_exc = None
    for attempt in range(attempts):
        try:
            return await root_ssh.send_command(command)
        except Exception as exc:
            last_exc = exc
            if attempt == attempts - 1:
                raise
            try:
                await root_ssh.close()
            except Exception:
                pass
            await asyncio.sleep(2)
            await root_ssh.open()
    raise last_exc if last_exc else RuntimeError(f"Unable to run command: {command}")


async def _goto_admin_path(gui_page, path_fragment: str):
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if not match:
        return False
    base = match.group(1)
    target = f"{base}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("networkidle")
    return True


async def open_monitor_submenu(gui_page, href_fragment: str):
    monitor_menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
    try:
        await monitor_menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await monitor_menu.click(timeout=5000)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    except Exception:
        used_direct = await _goto_admin_path(gui_page, href_fragment)
        if used_direct:
            return
        raise

    submenu = gui_page.locator(CommonLocators.submenu_by_href(href_fragment)).first
    try:
        await submenu.click(timeout=5000)
        await gui_page.wait_for_load_state("networkidle")
        return
    except Exception:
        used_direct = await _goto_admin_path(gui_page, href_fragment)
        if not used_direct:
            raise


async def open_monitor_bridge_table(gui_page):
    await open_monitor_submenu(gui_page, "/monitor/learntable")
    await gui_page.locator(MonitorLocators.BRIDGE_TABLE).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def open_monitor_arp_table(gui_page):
    await open_monitor_submenu(gui_page, "/monitor/learntable")
    arp_tab = gui_page.locator(MonitorLocators.ARP_TAB).first
    try:
        await arp_tab.wait_for(state="visible", timeout=5000)
        await arp_tab.click(timeout=5000)
        await gui_page.wait_for_load_state("networkidle")
    except Exception:
        used_direct = await _goto_admin_path(gui_page, "/monitor/learntable/arptbl")
        if not used_direct:
            raise
    await gui_page.locator(MonitorLocators.ARP_TABLE).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def open_monitor_system_logs(gui_page):
    await open_monitor_submenu(gui_page, "/monitor/logs")
    await gui_page.locator(MonitorLocators.LOG_TEXTAREA).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _open_monitor_log_tab(gui_page, tab_name: str):
    await open_monitor_system_logs(gui_page)
    selector = SYSTEM_LOG_TABS[tab_name]
    tab = gui_page.locator(selector).first
    try:
        await tab.wait_for(state="visible", timeout=5000)
        await tab.click(timeout=5000)
    except Exception:
        await gui_page.evaluate("(type) => get_logs(type)", tab_name)
    await gui_page.locator(MonitorLocators.LOG_TEXTAREA).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def _open_management_system_stable(gui_page):
    try:
        await open_management_system(gui_page)
    except Exception:
        used_direct = await _goto_admin_path(gui_page, "/system/system")
        if not used_direct:
            raise
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _wait_for_monitor_action(gui_page, selector: str):
    await gui_page.wait_for_timeout(1500)
    await gui_page.locator(selector).first.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def _click_monitor_action(gui_page, selector: str):
    button = gui_page.locator(selector).first
    await button.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await button.click()
    await _wait_for_monitor_action(gui_page, selector)


async def _set_bridge_filter(gui_page, value: str):
    dropdown = gui_page.locator(MonitorLocators.INTERFACE_FILTER).first
    await dropdown.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await dropdown.select_option(value)
    await _wait_for_monitor_action(gui_page, MonitorLocators.REFRESH_BUTTON)


async def _extract_visible_table_rows(gui_page, selector: str) -> list[list[str]]:
    rows = gui_page.locator(selector)
    return await rows.evaluate_all(
        """
        els => els
          .map(row => {
            const visible = !!(row.offsetParent || row.getClientRects().length);
            const cells = Array.from(row.querySelectorAll("td"))
              .map(cell => (cell.innerText || cell.textContent || "").replace(/\\s+/g, " ").trim())
              .filter(Boolean);
            return { visible, cells };
          })
          .filter(row => row.visible && row.cells.length > 0)
          .map(row => row.cells)
        """
    )


def _parse_float(raw_value: str) -> float | None:
    match = re.search(r"-?[\d.]+", str(raw_value or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


async def _read_gui_bridge_entries(gui_page) -> list[dict[str, Any]]:
    entries = []
    for cells in await _extract_visible_table_rows(gui_page, MonitorLocators.BRIDGE_ROWS):
        if len(cells) < 4:
            continue
        interface = normalize_monitor_interface_label(cells[0])
        mac = normalize_mac_address(cells[1])
        local_text = cells[2].strip().lower()
        ageing_seconds = _parse_float(cells[3])
        if not interface or not mac:
            continue
        entries.append(
            {
                "interface": interface,
                "mac": mac,
                "local": local_text == "yes",
                "ageing_seconds": ageing_seconds,
            }
        )
    return entries


async def _read_gui_arp_entries(gui_page) -> list[dict[str, Any]]:
    entries = []
    for cells in await _extract_visible_table_rows(gui_page, MonitorLocators.ARP_ROWS):
        if len(cells) < 3:
            continue
        interface = normalize_monitor_interface_label(cells[0])
        mac = normalize_mac_address(cells[1])
        ip_address = cells[2].strip()
        if not interface or not mac or not ip_address:
            continue
        entries.append(
            {
                "interface": interface,
                "mac": mac,
                "ip": ip_address,
            }
        )
    return entries


async def _read_bridge_backend_entries(root_ssh) -> list[dict[str, Any]]:
    raw = str((await _send_command_with_retry(root_ssh, RootCommands.GET_BRIDGE_FDB)).result or "")
    return parse_bridge_fdb_entries(raw)


async def _read_arp_backend_entries(root_ssh) -> list[dict[str, Any]]:
    raw = str((await _send_command_with_retry(root_ssh, RootCommands.GET_ARP_TABLE)).result or "")
    return parse_arp_entries(raw)


async def _read_gui_log_lines(gui_page) -> list[str]:
    raw = await gui_page.locator(MonitorLocators.LOG_TEXTAREA).first.input_value()
    return parse_monitor_log_lines(raw)


async def _read_config_backend_lines(root_ssh) -> list[str]:
    raw = str((await _send_command_with_retry(root_ssh, RootCommands.GET_CONFIG_LOGS)).result or "")
    return parse_monitor_log_lines(raw, newest_first=True)


async def _read_device_backend_lines(root_ssh) -> list[str]:
    raw = str((await _send_command_with_retry(root_ssh, RootCommands.GET_DEVICE_LOGS)).result or "")
    return parse_monitor_log_lines(raw, newest_first=True)


async def _read_temperature_backend_lines(root_ssh) -> list[str]:
    raw = str((await _send_command_with_retry(root_ssh, RootCommands.GET_TEMPERATURE_LOGS)).result or "")
    return parse_monitor_log_lines(raw, newest_first=True)


async def _read_system_backend_lines(root_ssh) -> list[str]:
    raw = str((await _send_command_with_retry(root_ssh, RootCommands.GET_SYSTEM_LOGS)).result or "")
    return parse_monitor_log_lines(raw, newest_first=True)


def _assert_no_duplicates(signatures: list[tuple], context: str):
    duplicates = {sig for sig in signatures if signatures.count(sig) > 1}
    assert not duplicates, f"{context} contains duplicate entries: {sorted(duplicates)}"


def _assert_bridge_entries(role: str, gui_entries: list[dict[str, Any]], backend_entries: list[dict[str, Any]], filter_label: str):
    expected_entries = backend_entries
    if filter_label != "All":
        expected_entries = [entry for entry in backend_entries if entry["interface"] == filter_label]

    gui_signatures = sorted((entry["interface"], entry["mac"], entry["local"]) for entry in gui_entries)
    expected_signatures = sorted((entry["interface"], entry["mac"], entry["local"]) for entry in expected_entries)
    _assert_no_duplicates(gui_signatures, f"{role} Bridge GUI ({filter_label})")
    assert gui_signatures == expected_signatures, (
        f"{role} Bridge Table mismatch for filter {filter_label}. "
        f"Expected {expected_signatures}, got {gui_signatures}"
    )

    for entry in gui_entries:
        assert entry["ageing_seconds"] is not None, f"{role} Bridge entry missing ageing time: {entry}"
        assert entry["ageing_seconds"] >= 0, f"{role} Bridge entry has invalid ageing time: {entry}"


def _assert_arp_entries(role: str, gui_entries: list[dict[str, Any]], backend_entries: list[dict[str, Any]]):
    gui_signatures = sorted((entry["interface"], entry["mac"], entry["ip"]) for entry in gui_entries)
    expected_signatures = sorted((entry["interface"], entry["mac"], entry["ip"]) for entry in backend_entries)
    _assert_no_duplicates(gui_signatures, f"{role} ARP GUI")
    assert gui_signatures == expected_signatures, (
        f"{role} ARP Table mismatch. Expected {expected_signatures}, got {gui_signatures}"
    )


def _assert_bridge_relearn_behavior(role: str, gui_entries: list[dict[str, Any]], backend_entries: list[dict[str, Any]]):
    gui_signatures = {(entry["interface"], entry["mac"], entry["local"]) for entry in gui_entries}
    backend_signatures = {(entry["interface"], entry["mac"], entry["local"]) for entry in backend_entries}
    _assert_no_duplicates(sorted(gui_signatures), f"{role} Bridge GUI relearn")
    assert gui_signatures, f"{role} Bridge Table is empty after relearn."
    assert gui_signatures.issubset(backend_signatures), (
        f"{role} Bridge relearn entries are not a subset of current backend entries. "
        f"GUI={sorted(gui_signatures)} backend={sorted(backend_signatures)}"
    )


def _assert_arp_relearn_behavior(role: str, gui_entries: list[dict[str, Any]], backend_entries: list[dict[str, Any]]):
    gui_signatures = {(entry["interface"], entry["mac"], entry["ip"]) for entry in gui_entries}
    backend_signatures = {(entry["interface"], entry["mac"], entry["ip"]) for entry in backend_entries}
    _assert_no_duplicates(sorted(gui_signatures), f"{role} ARP GUI relearn")
    if not gui_signatures and not backend_signatures:
        return
    assert gui_signatures, f"{role} ARP Table is empty after relearn."
    assert gui_signatures.issubset(backend_signatures) or not backend_signatures, (
        f"{role} ARP relearn entries are not aligned with backend state. "
        f"GUI={sorted(gui_signatures)} backend={sorted(backend_signatures)}"
    )


def _assert_log_lines_match(role: str, tab_name: str, gui_lines: list[str], backend_lines: list[str]):
    assert gui_lines == backend_lines, (
        f"{role} {tab_name} logs mismatch. "
        f"Expected {backend_lines[:10]}, got {gui_lines[:10]}"
    )


async def _wait_for_bridge_match(role: str, gui_page, root_ssh, filter_label: str, *, timeout_s: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        backend_entries = await _read_bridge_backend_entries(root_ssh)
        gui_entries = await _read_gui_bridge_entries(gui_page)
        try:
            _assert_bridge_entries(role, gui_entries, backend_entries, filter_label)
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(1.5)
    if last_error:
        raise last_error


async def _wait_for_arp_match(role: str, gui_page, root_ssh, *, timeout_s: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        backend_entries = await _read_arp_backend_entries(root_ssh)
        gui_entries = await _read_gui_arp_entries(gui_page)
        try:
            _assert_arp_entries(role, gui_entries, backend_entries)
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(1.5)
    if last_error:
        raise last_error


async def _wait_for_bridge_relearn(role: str, gui_page, root_ssh, *, timeout_s: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        backend_entries = await _read_bridge_backend_entries(root_ssh)
        gui_entries = await _read_gui_bridge_entries(gui_page)
        try:
            _assert_bridge_relearn_behavior(role, gui_entries, backend_entries)
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(1.5)
    if last_error:
        raise last_error


async def _wait_for_arp_relearn(role: str, gui_page, root_ssh, *, timeout_s: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        backend_entries = await _read_arp_backend_entries(root_ssh)
        gui_entries = await _read_gui_arp_entries(gui_page)
        try:
            _assert_arp_relearn_behavior(role, gui_entries, backend_entries)
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(1.5)
    if last_error:
        raise last_error


async def _wait_for_log_match(role: str, tab_name: str, gui_page, root_ssh, backend_reader, *, timeout_s: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        backend_lines = await backend_reader(root_ssh)
        gui_lines = await _read_gui_log_lines(gui_page)
        try:
            _assert_log_lines_match(role, tab_name, gui_lines, backend_lines)
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(1.5)
    if last_error:
        raise last_error


async def _wait_for_log_contains(role: str, tab_name: str, gui_page, expected_fragment: str, *, timeout_s: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        gui_lines = await _read_gui_log_lines(gui_page)
        if any(expected_fragment in line for line in gui_lines):
            return
        await asyncio.sleep(1.0)
    raise AssertionError(f"{role} {tab_name} logs did not contain expected fragment: {expected_fragment}")


async def _wait_for_config_log_update(role: str, gui_page, root_ssh, expected_fragment: str, *, timeout_s: float = 30.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        await _click_monitor_action(gui_page, MonitorLocators.LOG_REFRESH_BUTTON)
        backend_lines = await _read_config_backend_lines(root_ssh)
        gui_lines = await _read_gui_log_lines(gui_page)
        try:
            _assert_log_lines_match(role, "Configuration", gui_lines, backend_lines)
            assert any(expected_fragment in line for line in backend_lines[:10]), (
                f"{role} backend configuration logs did not include the updated entry: {expected_fragment}"
            )
            assert any(expected_fragment in line for line in gui_lines[:10]), (
                f"{role} GUI configuration logs did not include the updated entry: {expected_fragment}"
            )
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(2.0)
    if last_error:
        raise last_error


async def _get_timezone_choices(gui_page, host: str, device_creds: dict[str, str]) -> tuple[str, str]:
    await login_if_needed(gui_page, host, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS, skip_recovery=True)
    await _open_management_system_stable(gui_page)
    timezone_dropdown = gui_page.locator(ManagementLocators.TIMEZONE_DROPDOWN).first
    await timezone_dropdown.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    current_value = await timezone_dropdown.input_value()
    option_values = await timezone_dropdown.locator("option").evaluate_all(
        "els => els.map(option => option.value).filter(Boolean)"
    )
    for option_value in option_values:
        if option_value != current_value:
            return current_value, option_value
    raise AssertionError("Unable to find an alternate timezone option for config log validation.")


async def _set_timezone_and_apply(gui_page, host: str, device_creds: dict[str, str], timezone_value: str):
    await login_if_needed(gui_page, host, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS, skip_recovery=True)
    await _open_management_system_stable(gui_page)
    timezone_dropdown = gui_page.locator(ManagementLocators.TIMEZONE_DROPDOWN).first
    await timezone_dropdown.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await timezone_dropdown.select_option(timezone_value)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    await gui_page.locator(ManagementLocators.SAVE_BUTTON).first.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    apply_icon = gui_page.locator(CommonLocators.APPLY_ICON).first
    if await apply_icon.is_visible(timeout=5000):
        await apply_icon.click()
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
        confirm_apply = gui_page.locator(CommonLocators.CONFIRM_APPLY).first
        if await confirm_apply.is_visible(timeout=5000):
            await confirm_apply.click()
            await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    await asyncio.sleep(10)
    await login_if_needed(gui_page, host, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS, skip_recovery=True)


async def _open_monitor_targets(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.MEDIUM_WAIT_MS)
    bts_ssh = await _open_temp_root_ssh(bsu_ip, device_creds["pass"])
    targets = [
        {
            "role": "BTS",
            "host": bsu_ip,
            "page": gui_page,
            "ssh": bts_ssh,
            "owns_resources": True,
        }
    ]

    remote_host = _remote_dut_host_from_profile()
    if remote_host:
        remote_page, remote_ssh, remote_context, remote_browser, remote_playwright = await _open_remote_monitor_target(
            gui_page, remote_host, device_creds
        )
        targets.append(
            {
                "role": "CPE",
                "host": remote_host,
                "page": remote_page,
                "ssh": remote_ssh,
                "context": remote_context,
                "browser": remote_browser,
                "playwright": remote_playwright,
                "owns_resources": True,
            }
        )
    return targets


async def _close_monitor_targets(targets: list[dict[str, Any]]):
    for target in targets:
        if not target.get("owns_resources"):
            continue
        try:
            await target["ssh"].close()
        finally:
            if target.get("context") is None:
                continue
            try:
                await target["page"].close()
            finally:
                try:
                    await target["context"].close()
                finally:
                    try:
                        if target.get("browser") is not None:
                            await target["browser"].close()
                    finally:
                        if target.get("playwright") is not None:
                            await target["playwright"].stop()


async def _assert_bridge_table_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating Learn Table -> Bridge entries.")
    await open_monitor_bridge_table(gui_page)

    for filter_value, filter_label in BRIDGE_FILTERS:
        _log(role, f"Checking Bridge filter {filter_label}.")
        await _set_bridge_filter(gui_page, filter_value)
        await _wait_for_bridge_match(role, gui_page, root_ssh, filter_label)


async def _assert_bridge_refresh_clear_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating Learn Table -> Bridge Refresh/Clear behavior.")
    await open_monitor_bridge_table(gui_page)
    await _set_bridge_filter(gui_page, "0")
    await _wait_for_bridge_match(role, gui_page, root_ssh, "All")

    await _click_monitor_action(gui_page, MonitorLocators.REFRESH_BUTTON)
    await _wait_for_bridge_match(role, gui_page, root_ssh, "All")

    await _click_monitor_action(gui_page, MonitorLocators.CLEAR_BUTTON)
    await _wait_for_bridge_relearn(role, gui_page, root_ssh)


async def _assert_arp_table_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating Learn Table -> ARP entries.")
    await open_monitor_arp_table(gui_page)
    await _wait_for_arp_match(role, gui_page, root_ssh)


async def _assert_arp_refresh_clear_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating Learn Table -> ARP Refresh/Clear behavior.")
    await open_monitor_arp_table(gui_page)
    await _wait_for_arp_match(role, gui_page, root_ssh)

    await _click_monitor_action(gui_page, MonitorLocators.REFRESH_BUTTON)
    await _wait_for_arp_match(role, gui_page, root_ssh)

    await _click_monitor_action(gui_page, MonitorLocators.CLEAR_BUTTON)
    await _wait_for_arp_relearn(role, gui_page, root_ssh)


async def _assert_config_logs_for_target(role: str, host: str, gui_page, root_ssh, device_creds: dict[str, str]):
    _log(role, "Validating System Logs -> Configuration and Refresh behavior.")
    current_timezone = None
    updated_timezone = None
    try:
        current_timezone, updated_timezone = await _get_timezone_choices(gui_page, host, device_creds)
        await _set_timezone_and_apply(gui_page, host, device_creds, updated_timezone)
        await _open_monitor_log_tab(gui_page, "Configuration")
        expected_fragment = f"system.@system[0].timezone = {updated_timezone}"
        await _wait_for_config_log_update(role, gui_page, root_ssh, expected_fragment)
    finally:
        if current_timezone and updated_timezone and current_timezone != updated_timezone:
            await _set_timezone_and_apply(gui_page, host, device_creds, current_timezone)


async def _assert_device_logs_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating System Logs -> Device and Refresh behavior.")
    await _open_monitor_log_tab(gui_page, "Device")
    await _click_monitor_action(gui_page, MonitorLocators.LOG_REFRESH_BUTTON)
    await _wait_for_log_match(role, "Device", gui_page, root_ssh, _read_device_backend_lines)
    gui_lines = await _read_gui_log_lines(gui_page)
    assert gui_lines, f"{role} Device logs are empty."
    assert gui_lines[0] != "Log File is empty", f"{role} Device logs did not expose any device events."


async def _assert_temperature_logs_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating System Logs -> Temperature Refresh/Clear behavior.")
    await _open_monitor_log_tab(gui_page, "Temperature")
    await _click_monitor_action(gui_page, MonitorLocators.LOG_REFRESH_BUTTON)
    await _wait_for_log_match(role, "Temperature", gui_page, root_ssh, _read_temperature_backend_lines)

    await _click_monitor_action(gui_page, MonitorLocators.LOG_CLEAR_BUTTON)
    await _wait_for_log_contains(role, "Temperature", gui_page, "logs are cleared")
    await _click_monitor_action(gui_page, MonitorLocators.LOG_REFRESH_BUTTON)
    await _wait_for_log_match(role, "Temperature", gui_page, root_ssh, _read_temperature_backend_lines)
    gui_lines = await _read_gui_log_lines(gui_page)
    assert gui_lines == ["Log File is empty"], f"{role} Temperature logs were not cleared."


async def _assert_system_logs_for_target(role: str, gui_page, root_ssh):
    _log(role, "Validating System Logs -> System and Refresh behavior.")
    await _open_monitor_log_tab(gui_page, "System")
    marker = f"cursor-monitor-{role.lower()}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    await _send_command_with_retry(root_ssh, RootCommands.emit_system_log_marker(marker))
    await _click_monitor_action(gui_page, MonitorLocators.LOG_REFRESH_BUTTON)
    await _wait_for_log_contains(role, "System", gui_page, marker)
    gui_lines = await _read_gui_log_lines(gui_page)
    backend_lines = await _read_system_backend_lines(root_ssh)
    assert any(marker in line for line in backend_lines), f"{role} System logs backend did not record marker: {marker}"
    recent_overlap = [line for line in backend_lines[:12] if line in gui_lines[:20]]
    assert recent_overlap, f"{role} System logs did not reflect recent backend entries after refresh."


async def assert_gui_105_bridge_table(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_bridge_table_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_106_bridge_refresh_clear(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_bridge_refresh_clear_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_107_arp_table(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_arp_table_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_108_arp_refresh_clear(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_arp_refresh_clear_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_109_config_logs(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_config_logs_for_target(
                target["role"],
                target["host"],
                target["page"],
                target["ssh"],
                device_creds,
            )
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_110_device_logs(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_device_logs_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_111_temperature_logs(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_temperature_logs_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)


async def assert_gui_112_system_logs(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_monitor_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_system_logs_for_target(target["role"], target["page"], target["ssh"])
    finally:
        await _close_monitor_targets(targets)
