from pages.locators import CommonLocators, ManagementLocators, UITimeouts
from utils.apply_triple import apply_triple as _apply_triple


async def open_management_system(gui_page):
    mgmt_menu = gui_page.locator(CommonLocators.MENU_MANAGEMENT).first
    await mgmt_menu.wait_for(state="visible", timeout=10000)
    await mgmt_menu.click()
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    await gui_page.locator(ManagementLocators.SUBMENU_SYSTEM).first.click()
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def open_management_logging(gui_page):
    await open_management_system(gui_page)
    logging_tab = gui_page.locator(ManagementLocators.TAB_LOGGING_XPATH).first
    await logging_tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await logging_tab.click()
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def apply_triple(gui_page, save_locator, apply_icon_locator, confirm_apply_locator, settle_seconds=15, **kwargs):
    """Management pages always run the full apply chain."""
    await _apply_triple(
        gui_page,
        save_locator,
        apply_icon_locator,
        confirm_apply_locator,
        settle_seconds,
        only_if_apply_visible=False,
        **kwargs,
    )
