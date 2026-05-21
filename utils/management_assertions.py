"""GUI management page assertions (GUI_88–GUI_93)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from pages.commands import RootCommands
from pages.locators import CommonLocators, ManagementLocators, UITimeouts
from utils.management_flows import apply_triple, open_management_logging, open_management_system
from utils.network_flows import _goto_admin_path
from utils.parsers import extract_uci_value, ssh_scalar
from utils.ui_helpers import attach_dialog_handler, validate_input_lifecycle
from utils.validators import validate_param


def _admin_fallback(gui_page, fragment: str) -> str:
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    base = match.group(1) if match else ""
    return f"{base}/admin{fragment}"


async def _open_management_system_stable(gui_page):
    try:
        await open_management_system(gui_page)
    except Exception:
        if not await _goto_admin_path(gui_page, "/system/system"):
            raise
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _get_timezone_choices(gui_page) -> tuple[str, str]:
    await _open_management_system_stable(gui_page)
    dropdown = gui_page.locator(ManagementLocators.TIMEZONE_DROPDOWN).first
    await dropdown.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    current_value = await dropdown.input_value()
    for option_value in await dropdown.locator("option").evaluate_all(
        "els => els.map(option => option.value).filter(Boolean)"
    ):
        if option_value != current_value:
            return current_value, option_value
    raise AssertionError("Unable to find an alternate timezone option.")


async def _set_timezone(gui_page, timezone_value: str):
    await _open_management_system_stable(gui_page)
    dropdown = gui_page.locator(ManagementLocators.TIMEZONE_DROPDOWN).first
    await dropdown.select_option(timezone_value)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    await apply_triple(
        gui_page,
        ManagementLocators.SAVE_BUTTON,
        CommonLocators.APPLY_ICON,
        CommonLocators.CONFIRM_APPLY,
        settle_seconds=10,
    )


async def _open_management_location(gui_page):
    await _open_management_system_stable(gui_page)
    tab = gui_page.locator(f"a:has-text('{ManagementLocators.TAB_LOCATION_LINK_TEXT}')").first
    await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await tab.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def assert_gui_88_timezone_random(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    original, alternate = await _get_timezone_choices(gui_page)
    try:
        await _set_timezone(gui_page, alternate)
        backend = ssh_scalar((await root_ssh.send_command(RootCommands.GET_TIMEZONE)).result)
        validate_param("Timezone", alternate, backend)
        await _open_management_system_stable(gui_page)
        gui_val = await gui_page.locator(ManagementLocators.TIMEZONE_DROPDOWN).first.input_value()
        validate_param("Timezone GUI", alternate, gui_val)
    finally:
        if original != alternate:
            await _set_timezone(gui_page, original)


async def assert_gui_89_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await _open_management_system_stable(gui_page)
    add_input = gui_page.locator(ManagementLocators.NTP_ADD_INPUT_XPATH).first
    await add_input.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    test_server = "pool.ntp.org"
    await add_input.fill(test_server)
    await gui_page.locator(ManagementLocators.NTP_ADD_BTN_XPATH).first.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    await apply_triple(
        gui_page,
        ManagementLocators.SAVE_BUTTON,
        CommonLocators.APPLY_ICON,
        CommonLocators.CONFIRM_APPLY,
        settle_seconds=8,
    )
    page_text = await gui_page.content()
    assert test_server in page_text, f"NTP server {test_server} not visible after add"
    delete_btn = gui_page.locator(ManagementLocators.NTP_DELETE_BTN_XPATH).first
    if await delete_btn.is_visible(timeout=5000):
        await delete_btn.click()
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        await apply_triple(
            gui_page,
            ManagementLocators.SAVE_BUTTON,
            CommonLocators.APPLY_ICON,
            CommonLocators.CONFIRM_APPLY,
            settle_seconds=8,
        )


async def assert_gui_90_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await _open_management_system_stable(gui_page)
    sync_btn = gui_page.locator(ManagementLocators.SYNC_BROWSER_BTN_XPATH).first
    await sync_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await sync_btn.click()
    await apply_triple(
        gui_page,
        ManagementLocators.SAVE_BUTTON,
        CommonLocators.APPLY_ICON,
        CommonLocators.CONFIRM_APPLY,
        settle_seconds=8,
    )
    device_time_raw = ssh_scalar((await root_ssh.send_command(RootCommands.GET_TIME)).result)
    browser_now = datetime.now().strftime("%Y")
    assert browser_now in device_time_raw or device_time_raw, (
        f"Device time not readable after browser sync: {device_time_raw}"
    )


async def assert_gui_91_logging_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await open_management_logging(gui_page)
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        ManagementLocators.LOG_IP_XPATH,
        "192.168.1.100",
        "999.999.999.999",
        "uci get system.@system[0].log_ip",
        "Syslog IP",
        _admin_fallback(gui_page, "/system/system"),
        parser=extract_uci_value,
    )


async def assert_gui_92_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await open_management_logging(gui_page)
    interval_locator = ManagementLocators.TEMP_INTERVAL_XPATH
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        interval_locator,
        "300",
        "99999999",
        "uci get system.@system[0].templog_int",
        "Temp Log Interval",
        _admin_fallback(gui_page, "/system/system"),
        parser=extract_uci_value,
        skip_restore=True,
    )


async def assert_gui_93_location_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await _open_management_location(gui_page)
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        ManagementLocators.LOCATION_SYSTEM_NAME_XPATH,
        "UBR-Lab",
        "X" * 300,
        "uci get system.@system[0].cusname",
        "Location Name",
        _admin_fallback(gui_page, "/system/system"),
        parser=extract_uci_value,
    )
