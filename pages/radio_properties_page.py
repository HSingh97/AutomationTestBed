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
        if "chrome-error" in (self.page.url or ""):
            await self.page.goto(
                f"https://{format_http_host(self.local_ip)}/cgi-bin/luci/admin/wireless/radio1",
                timeout=15000,
            )
            await self.page.wait_for_timeout(4000)

        ssid_field = self.page.locator(RadioPropertiesLocators.SSID_INPUT).first
        if await ssid_field.count() > 0:
            try:
                await ssid_field.wait_for(state="visible", timeout=3000)
                return
            except Exception:
                pass

        # Prefer stok URL when session already established (CPE/BTS Senao LuCI)
        match = re.search(
            r"(https?://[^/]+/cgi-bin/luci/;stok=[a-f0-9]+)",
            self.page.url or "",
        )
        if match:
            await self.page.goto(
                f"{match.group(1)}/admin/wireless/radio1",
                timeout=15000,
                wait_until="commit",
            )
            await self.page.wait_for_timeout(2000)
            if await ssid_field.count() > 0:
                try:
                    await ssid_field.wait_for(state="visible", timeout=5000)
                    return
                except Exception:
                    pass

        # Sidebar: Wireless (bootstrap collapse on CPE) -> Radio 1
        wireless_menu = self.page.locator(RadioPropertiesLocators.MENU_WIRELESS).first
        if await wireless_menu.count() > 0:
            await wireless_menu.click()
            await self.page.wait_for_timeout(1000)
            wireless_panel = self.page.locator("#Wireless").first
            if await wireless_panel.count() > 0:
                await wireless_panel.evaluate(
                    "el => { el.classList.add('in'); el.style.height = 'auto'; }"
                )

        radio_1 = self.page.locator("a[href*='/admin/wireless/radio1']").first
        if await radio_1.count() == 0:
            radio_1 = self.page.locator(
                "ul#Wireless a[href*='wireless/radio1'], "
                f"{RadioPropertiesLocators.SUBMENU_RADIO_1}"
            ).first
        if await radio_1.count() > 0:
            await radio_1.click()
            await self.page.wait_for_timeout(2000)

        await ssid_field.wait_for(state="attached", timeout=15000)

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
