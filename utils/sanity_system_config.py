"""Sanity-only System config (Location IDs, General timezone)."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from pages.commands import RootCommands
from pages.locators import CommonLocators, ManagementLocators, UITimeouts
from utils.management_flows import apply_triple, open_management_system
from utils.net_utils import format_luci_url
from utils.network_flows import _goto_admin_path
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.ui_helpers import (
    attach_dialog_handler,
    execute_triple_apply,
    fill_luci_input,
    luci_base_url,
    read_luci_input_value,
    uci_get_cmd_for_locator,
)

_PREFERRED_TEST_TIMEZONES = (
    "UTC",
    "GMT0",
    "Etc/UTC",
    "America/New_York",
    "Europe/London",
    "Asia/Tokyo",
    "America/Los_Angeles",
)


@dataclass(frozen=True)
class SanityLocationSnapshot:
    device_id: str
    site_id: str
    device_uci_cmd: str
    site_uci_cmd: str


@dataclass(frozen=True)
class SanityLocationTestValues:
    device_id: str
    site_id: str


def _admin_fallback(gui_page, fragment: str = "/system/system") -> str:
    base = luci_base_url(gui_page.url or "") or ""
    return f"{base}/admin{fragment}"


async def open_sanity_location_tab(gui_page) -> None:
    """Management → System → Location."""
    try:
        await open_management_system(gui_page)
    except Exception:
        if not await _goto_admin_path(gui_page, "/system/system"):
            raise
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    tab = gui_page.locator(
        f"a:has-text('{ManagementLocators.TAB_LOCATION_LINK_TEXT}')"
    ).first
    await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await tab.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def read_sanity_location_snapshot(gui_page) -> SanityLocationSnapshot:
    await open_sanity_location_tab(gui_page)
    device_loc = ManagementLocators.LOCATION_SYSTEM_NAME_XPATH
    site_loc = ManagementLocators.LOCATION_ADDRESS_XPATH
    await gui_page.locator(device_loc).first.wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    device_id = await read_luci_input_value(gui_page, device_loc)
    site_id = await read_luci_input_value(gui_page, site_loc)
    device_uci_cmd = await uci_get_cmd_for_locator(gui_page, device_loc)
    site_uci_cmd = await uci_get_cmd_for_locator(gui_page, site_loc)
    return SanityLocationSnapshot(
        device_id=device_id,
        site_id=site_id,
        device_uci_cmd=device_uci_cmd,
        site_uci_cmd=site_uci_cmd,
    )


async def read_sanity_location_backend(ssh, snap: SanityLocationSnapshot) -> tuple[str, str]:
    from utils.parsers import extract_uci_value, ssh_scalar

    device_raw = ssh_scalar((await ssh.send_command(snap.device_uci_cmd)).result)
    site_raw = ssh_scalar((await ssh.send_command(snap.site_uci_cmd)).result)
    return extract_uci_value(device_raw), extract_uci_value(site_raw)


async def apply_sanity_location_values(
    gui_page,
    *,
    device_id: str,
    site_id: str,
) -> None:
    attach_dialog_handler(gui_page)
    await open_sanity_location_tab(gui_page)
    await fill_luci_input(gui_page, ManagementLocators.LOCATION_SYSTEM_NAME_XPATH, device_id)
    await fill_luci_input(gui_page, ManagementLocators.LOCATION_ADDRESS_XPATH, site_id)
    await gui_page.wait_for_timeout(1000)
    await execute_triple_apply(gui_page, _admin_fallback(gui_page))
    await open_sanity_location_tab(gui_page)


def generate_sanity_location_test_values(
    *,
    role: str,
    original_device_id: str,
) -> SanityLocationTestValues:
    """Random alphanumeric IDs (no spaces/quotes — per LuCI Location page note)."""
    token = secrets.token_hex(3)
    prefix = "BTS" if role.upper() == "BTS" else "CPE"
    device_id = f"San{prefix}{token}"[:32]
    site_id = f"Site{token}"[:32]
    if device_id == original_device_id:
        device_id = f"San{prefix}{secrets.token_hex(2)}"[:32]
    return SanityLocationTestValues(device_id=device_id, site_id=site_id)


async def ensure_sanity_location_gui(
    gui_page,
    host: str,
    device_creds: dict,
) -> None:
    await sanity_login_if_needed(gui_page, host, device_creds)


_SANITY_TZ_WAIT_MS = max(UITimeouts.ELEMENT_WAIT_MS * 3, 45000)


async def _ensure_system_general_tab(gui_page) -> None:
    """Re-select General when Location/Logging tab is active."""
    for selector in (
        "ul.cbi-tabmenu > li > a[href*='/system/system']:not([href*='location']):not([href*='logging'])",
        "ul.cbi-tabmenu > li:first-child > a",
        "a:has-text('General')",
    ):
        tab = gui_page.locator(selector).first
        try:
            if await tab.is_visible(timeout=2000):
                await tab.click()
                await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
                return
        except Exception:
            continue


async def open_sanity_system_general(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Management → System → General (default tab)."""
    selectors = (
        ManagementLocators.TIMEZONE_DROPDOWN,
        ManagementLocators.TIMEZONE_SELECT_XPATH,
    )

    async def _wait_tz_visible(timeout_ms: int) -> bool:
        for selector in selectors:
            loc = gui_page.locator(selector).first
            try:
                await loc.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    if await _wait_tz_visible(3000):
        return

    try:
        await open_management_system(gui_page)
    except Exception:
        if await _goto_admin_path(gui_page, "/system/system"):
            await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
        elif host:
            url = f"{format_luci_url(host).rstrip('/')}/admin/system/system"
            await gui_page.goto(url, timeout=60000, wait_until="networkidle")
            if host and device_creds:
                await sanity_login_if_needed(gui_page, host, device_creds)
        else:
            raise

    await _ensure_system_general_tab(gui_page)
    if not await _wait_tz_visible(5000) and host:
        url = f"{format_luci_url(host).rstrip('/')}/admin/system/system"
        await gui_page.goto(url, timeout=60000, wait_until="networkidle")
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds)
        await _ensure_system_general_tab(gui_page)


@dataclass(frozen=True)
class SanityTimezoneSnapshot:
    timezone: str
    local_time_gui: str
    offset_ssh: str


async def _timezone_dropdown(gui_page):
    last_error = ""
    for selector in (
        ManagementLocators.TIMEZONE_DROPDOWN,
        ManagementLocators.TIMEZONE_SELECT_XPATH,
    ):
        dropdown = gui_page.locator(selector).first
        try:
            await dropdown.wait_for(state="visible", timeout=_SANITY_TZ_WAIT_MS)
            return dropdown
        except Exception as exc:
            last_error = str(exc)
    raise TimeoutError(f"[sanity] timezone dropdown not visible: {last_error}")


async def read_sanity_ssh_offset(ssh) -> str:
    await ensure_sanity_ssh_open(ssh)
    raw = await sanity_ssh_run(ssh, "date +%z 2>/dev/null", timeout_s=20)
    match = re.search(r"[+-]\d{4}", raw)
    return match.group(0) if match else raw.strip()


async def _read_gui_local_time(gui_page) -> str:
    body = await gui_page.inner_text("body")
    match = re.search(r"Local Time\s*([^\n]+)", body or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


async def read_sanity_timezone_snapshot(
    gui_page,
    ssh,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> SanityTimezoneSnapshot:
    await open_sanity_system_general(gui_page, host=host, device_creds=device_creds)
    dropdown = await _timezone_dropdown(gui_page)
    timezone = (await dropdown.input_value()).strip()
    local_time_gui = await _read_gui_local_time(gui_page)
    backend = await read_sanity_timezone_backend(ssh)
    if backend and not timezone:
        timezone = backend
    offset_ssh = await read_sanity_ssh_offset(ssh)
    return SanityTimezoneSnapshot(
        timezone=timezone or backend,
        local_time_gui=local_time_gui,
        offset_ssh=offset_ssh,
    )


async def pick_alternate_timezone(gui_page, current: str) -> str:
    dropdown = await _timezone_dropdown(gui_page)
    options = await dropdown.locator("option").evaluate_all(
        "els => els.map(option => option.value).filter(Boolean)"
    )
    current = (current or "").strip()
    for preferred in _PREFERRED_TEST_TIMEZONES:
        if preferred in options and preferred != current:
            return preferred
    for option in options:
        if option != current:
            return option
    raise RuntimeError("[sanity] no alternate timezone option available")


async def apply_sanity_timezone(
    gui_page,
    timezone_value: str,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    attach_dialog_handler(gui_page)
    selectors = (
        ManagementLocators.TIMEZONE_DROPDOWN,
        ManagementLocators.TIMEZONE_SELECT_XPATH,
    )
    on_page = False
    for selector in selectors:
        loc = gui_page.locator(selector).first
        try:
            if await loc.is_visible(timeout=2000):
                on_page = True
                break
        except Exception:
            continue
    if not on_page:
        await open_sanity_system_general(gui_page, host=host, device_creds=device_creds)
    dropdown = await _timezone_dropdown(gui_page)
    await dropdown.select_option(timezone_value)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    await apply_triple(
        gui_page,
        ManagementLocators.SAVE_BUTTON,
        CommonLocators.APPLY_ICON,
        CommonLocators.CONFIRM_APPLY,
        settle_seconds=10,
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def read_sanity_timezone_backend(ssh) -> str:
    from utils.parsers import extract_uci_value

    await ensure_sanity_ssh_open(ssh)
    raw = await sanity_ssh_run(ssh, RootCommands.GET_TIMEZONE, timeout_s=30)
    return extract_uci_value(raw)

