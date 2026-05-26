from pages.locators import CommonLocators, ManagementLocators, UITimeouts
from utils.apply_triple import apply_triple as _apply_triple
from utils.network_flows import _goto_admin_path


async def open_management_system(gui_page):
    if await _goto_admin_path(gui_page, "/system/system"):
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
        return
    mgmt_menu = gui_page.locator(CommonLocators.MENU_MANAGEMENT).first
    await mgmt_menu.wait_for(state="visible", timeout=10000)
    await mgmt_menu.hover()
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    submenu = gui_page.locator(ManagementLocators.SUBMENU_SYSTEM).first
    await submenu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await submenu.click()
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def open_management_logging(gui_page):
    await open_management_system(gui_page)
    logging_tab = gui_page.locator(
        f"a:has-text('{ManagementLocators.TAB_LOGGING_LINK_TEXT}')"
    ).first
    await logging_tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await logging_tab.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def apply_triple(gui_page, save_locator, apply_icon_locator, confirm_apply_locator, settle_seconds=15, **kwargs):
    """Management save/apply; skip apply banner when LuCI commits immediately."""
    await _apply_triple(
        gui_page,
        save_locator,
        apply_icon_locator,
        confirm_apply_locator,
        settle_seconds,
        only_if_apply_visible=True,
        **kwargs,
    )
