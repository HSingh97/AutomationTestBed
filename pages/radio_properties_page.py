from pages.locators import RadioPropertiesLocators


class RadioPropertiesPage:
    RADIO_1_URL_CHUNK = "/admin/wireless/radio1"

    def __init__(self, gui_page, local_ip="192.168.2.230"):
        self.page = gui_page
        self.local_ip = local_ip

    async def navigate(self):
        if "chrome-error" in self.page.url:
            await self.page.goto(
                f"https://{self.local_ip}/cgi-bin/luci/admin/wireless/radio1",
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

    def locator(self, value):
        return self.page.locator(value).first
