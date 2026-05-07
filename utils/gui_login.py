"""Single implementation of conditional GUI login (Management, Network, and other GUI tests)."""

from pages.locators import LoginPageLocators, UITimeouts
from utils.net_utils import format_http_host
from utils.recovery_manager import get_active_recovery_manager


async def login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=4000, *, skip_recovery=False):
    manager = get_active_recovery_manager()
    if manager and not skip_recovery:
        reachable = await manager.is_gui_reachable(bsu_ip)
        if not reachable:
            await manager.run_soft_recovery(gui_page=gui_page, bsu_ip=bsu_ip, device_creds=device_creds)

    is_in_session = "/cgi-bin/luci" in (gui_page.url or "")
    if not is_in_session:
        await gui_page.goto(f"https://{format_http_host(bsu_ip)}/cgi-bin/luci/", timeout=UITimeouts.PAGE_LOAD_MS)

    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible(timeout=UITimeouts.SHORT_WAIT_MS):
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(wait_ms)
