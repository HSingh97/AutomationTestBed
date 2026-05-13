import asyncio
import re

from pages.locators import CommonLocators, DHCPLocators, NetworkLocators, UITimeouts
from utils.apply_triple import apply_triple as _apply_triple


async def _goto_admin_path(gui_page, path_fragment: str):
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if not match:
        return False
    base = match.group(1)
    target = f"{base}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("networkidle")
    return True


async def open_network_submenu(gui_page, href_fragment):
    try:
        await gui_page.locator(CommonLocators.MENU_NETWORK).first.click(timeout=5000)
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
        # Some pages keep submenu in collapsed/hidden state after apply screens.
        pass
    used_direct = await _goto_admin_path(gui_page, href_fragment)
    if not used_direct:
        await submenu.click()
        await gui_page.wait_for_load_state("networkidle")


async def navigate_to_dhcp(gui_page):
    await open_network_submenu(gui_page, "/network/dhcp")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def navigate_to_radio_24_dhcp(gui_page):
    await navigate_to_dhcp(gui_page)
    radio_tab = gui_page.locator("ul.cbi-tabmenu > li > a").filter(has_text=DHCPLocators.TAB_RADIO_24_TEXT).first
    await radio_tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await radio_tab.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def navigate_to_ethernet(gui_page):
    await open_network_submenu(gui_page, "/network/eth")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def apply_triple(gui_page, save_locator, apply_icon_locator, confirm_apply_locator, settle_seconds=10, **kwargs):
    """Network submenu may omit the apply banner; only commit when the icon is shown."""
    await _apply_triple(
        gui_page,
        save_locator,
        apply_icon_locator,
        confirm_apply_locator,
        settle_seconds,
        only_if_apply_visible=True,
        **kwargs,
    )


async def save_apply_if_visible(gui_page, save_locator, settle_seconds=10, *, save_wait_ms=4000):
    """
    DHCP-style flow: Save, then Apply+Confirm only if the red apply icon appears.
    Uses NetworkLocators for apply/confirm.
    """
    await save_locator.first.click()
    await gui_page.wait_for_timeout(save_wait_ms)
    apply_icon = gui_page.locator(NetworkLocators.APPLY_ICON).first
    if await apply_icon.is_visible(timeout=5000):
        await apply_icon.click()
        await gui_page.wait_for_timeout(5000)
        confirm_btn = gui_page.locator(NetworkLocators.CONFIRM_APPLY).first
        await confirm_btn.wait_for(state="visible", timeout=10000)
        await confirm_btn.click()
        await asyncio.sleep(settle_seconds)
