import re

from pages.locators import RadioPropertiesLocators
from utils.net_utils import format_http_host


class RadioPropertiesPage:
    RADIO_1_URL_CHUNK = "/admin/wireless/radio1"
    RADIO_1_DDRS_URL_CHUNK = "/admin/wireless/radio1/ddrs1"

    def __init__(self, gui_page, local_ip="192.168.2.230"):
        self.page = gui_page
        self.local_ip = local_ip

    async def navigate(self):
        if "chrome-error" in self.page.url:
            await self.page.goto(
                f"https://{format_http_host(self.local_ip)}/cgi-bin/luci/admin/wireless/radio1",
                timeout=15000,
            )
            await self.page.wait_for_timeout(4000)

        if "radio1" in self.page.url.lower() and "admin" in self.page.url.lower():
            return

        radio_1 = self.page.locator(RadioPropertiesLocators.SUBMENU_RADIO_1).first
        if not await radio_1.is_visible():
            await self.page.locator(RadioPropertiesLocators.MENU_WIRELESS).first.click()
            await self.page.wait_for_timeout(1000)
        await radio_1.click()

    async def open_ddrs_atpc(self):
        await self.navigate()
        ddrs_tab = self.page.locator(RadioPropertiesLocators.TAB_DDRS_ATPC).first
        try:
            await ddrs_tab.wait_for(state="visible", timeout=5000)
            await ddrs_tab.click(timeout=5000)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)
            return
        except Exception:
            match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", self.page.url or "")
            if not match:
                raise
            target = f"{match.group(1)}{self.RADIO_1_DDRS_URL_CHUNK}"
            await self.page.goto(target, timeout=15000)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(2000)

    def locator(self, value):
        return self.page.locator(value).first
