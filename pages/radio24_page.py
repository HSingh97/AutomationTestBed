import re

from pages.locators import Radio24Locators
from utils.net_utils import format_http_host


class Radio24Page:
    RADIO_0_URL_CHUNK = Radio24Locators.RADIO_0_URL_CHUNK

    def __init__(self, gui_page, local_ip="192.168.2.230"):
        self.page = gui_page
        self.local_ip = local_ip

    async def navigate(self):
        page = self.page
        if "chrome-error" in (page.url or ""):
            await page.goto(
                f"https://{format_http_host(self.local_ip)}/cgi-bin/luci/admin/wireless/radio0",
                timeout=15000,
            )
            await page.wait_for_timeout(4000)

        if "radio0" in (page.url or "").lower() and "admin" in (page.url or "").lower():
            return

        radio_24 = page.locator(Radio24Locators.SUBMENU_RADIO_24).first
        if not await radio_24.is_visible():
            await page.locator(Radio24Locators.MENU_WIRELESS).first.click()
            await page.wait_for_timeout(1000)
        await radio_24.click()
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)

        if "radio0" not in (page.url or "").lower():
            match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", page.url or "")
            if match:
                await page.goto(f"{match.group(1)}{self.RADIO_0_URL_CHUNK}", timeout=15000)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(2000)
