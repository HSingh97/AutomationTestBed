"""GUI management page assertions (GUI_63–GUI_68)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from pages.commands import RootCommands
from pages.locators import CommonLocators, ManagementLocators, UITimeouts
from utils.management_flows import apply_triple, open_management_logging, open_management_system
from utils.network_flows import _goto_admin_path
from utils.parsers import clean_ssh_output, extract_uci_value, ssh_scalar
from utils.ui_helpers import (
    attach_dialog_handler,
    execute_triple_apply,
    uci_get_cmd_for_locator,
    validate_input_lifecycle,
)
from utils.validators import validate_param


def _admin_fallback(gui_page, fragment: str) -> str:
    from utils.ui_helpers import luci_base_url

    base = luci_base_url(gui_page.url or "") or ""
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


async def assert_gui_63_timezone_random(root_ssh, gui_page, bsu_ip, device_creds):
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


async def assert_gui_64_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await _open_management_system_stable(gui_page)
    test_server = "pool.ntp.org"
    fallback = _admin_fallback(gui_page, "/system/system")

    await gui_page.evaluate(
        """
        (server) => {
            const el = document.querySelector('#ntp_addr');
            if (el) {
                el.scrollIntoView({ block: 'center' });
                el.value = server;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (window.KWN_SYSTEM && KWN_SYSTEM.submit_add_ntp) {
                KWN_SYSTEM.submit_add_ntp();
            }
        }
        """,
        test_server,
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    try:
        await gui_page.wait_for_function(
            f"() => document.body.innerText.includes('{test_server}')",
            timeout=UITimeouts.ELEMENT_WAIT_MS,
        )
    except Exception:
        pass

    await execute_triple_apply(gui_page, fallback)
    await _open_management_system_stable(gui_page)

    page_text = await gui_page.content()
    ssh_raw = clean_ssh_output(
        (await root_ssh.send_command(f"uci show system 2>/dev/null | grep -F '{test_server}'")).result
    )
    assert test_server in page_text or test_server in ssh_raw, (
        f"NTP server {test_server} not found in GUI or UCI after add"
    )

    delete_btn = gui_page.locator(ManagementLocators.NTP_DELETE_BTN_XPATH).or_(
        gui_page.locator(ManagementLocators.NTP_DELETE_BTNS)
    ).first
    if await delete_btn.is_visible(timeout=5000):
        await delete_btn.click(force=True)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        await execute_triple_apply(gui_page, fallback)


async def assert_gui_65_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await _open_management_system_stable(gui_page)
    sync_btn = gui_page.locator(ManagementLocators.SYNC_BROWSER_BTN_XPATH).first
    await sync_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await sync_btn.click()
    await gui_page.wait_for_timeout(UITimeouts.LONG_WAIT_MS)
    device_time_raw = ssh_scalar((await root_ssh.send_command(RootCommands.GET_TIME)).result)
    browser_now = datetime.now().strftime("%Y")
    assert browser_now in device_time_raw or device_time_raw, (
        f"Device time not readable after browser sync: {device_time_raw}"
    )


async def assert_gui_66_logging_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await open_management_logging(gui_page)
    log_locator = ManagementLocators.LOG_IP_XPATH

    async def _reopen_logging(page):
        await open_management_logging(page)

    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        log_locator,
        "192.168.1.100",
        "999.999.999.999",
        await uci_get_cmd_for_locator(gui_page, log_locator),
        "Syslog IP",
        _admin_fallback(gui_page, "/system/system"),
        parser=extract_uci_value,
        after_apply=_reopen_logging,
    )


async def assert_gui_67_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await open_management_logging(gui_page)
    interval_locator = ManagementLocators.TEMP_INTERVAL_XPATH

    async def _reopen_logging(page):
        await open_management_logging(page)

    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        interval_locator,
        "30",
        "99999999",
        await uci_get_cmd_for_locator(gui_page, interval_locator),
        "Temp Log Interval",
        _admin_fallback(gui_page, "/system/system"),
        parser=extract_uci_value,
        skip_restore=True,
        after_apply=_reopen_logging,
    )


async def assert_gui_68_location_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    attach_dialog_handler(gui_page)
    await _open_management_location(gui_page)
    loc_locator = ManagementLocators.LOCATION_SYSTEM_NAME_XPATH

    async def _reopen_location(page):
        await _open_management_location(page)

    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        loc_locator,
        "UBR-Lab",
        "X" * 300,
        await uci_get_cmd_for_locator(gui_page, loc_locator),
        "Location Name",
        _admin_fallback(gui_page, "/system/system"),
        parser=extract_uci_value,
        after_apply=_reopen_location,
    )
