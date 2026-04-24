import re
import pytest
from scrapli.driver.generic import AsyncGenericDriver

# ADDED LoginPageLocators to the imports
from pages.locators import TopPanelLocators, RadioPropertiesLocators, LoginPageLocators
from pages.commands import RootCommands

# Import our universal UI helpers
from utils.ui_helpers import validate_dropdown_lifecycle, validate_input_lifecycle

# Import parsing helpers directly from utils.parsers
from utils.parsers import (
    extract_uci_value,
    parse_radio_status,
    parse_radio_mode,
    parse_link_type,
    parse_bandwidth
)

pytestmark = pytest.mark.sanity

# Constants for this specific page
RADIO_1_URL_CHUNK = "/admin/wireless/radio1"


# =====================================================================
# SETUP: Navigate Helper (Specific to this test file)
# =====================================================================
async def navigate_to_radio_properties_page(gui_page, local_ip="192.168.2.230"):
    if "chrome-error" in gui_page.url:
        print("    -> [RECOVERY] Chrome error page detected! Forcing a hard reload to router UI...")
        await gui_page.goto(f"https://{local_ip}/cgi-bin/luci/admin/wireless/radio1", timeout=15000)
        await gui_page.wait_for_timeout(4000)
    if "radio1" in gui_page.url.lower() and "admin" in gui_page.url.lower():
        return
    radio_1 = gui_page.locator(RadioPropertiesLocators.SUBMENU_RADIO_1).first
    if not await radio_1.is_visible():
        await gui_page.locator(RadioPropertiesLocators.MENU_WIRELESS).first.click()
        await gui_page.wait_for_timeout(1000)
    await radio_1.click()


# =====================================================================
# GUI_17: Wireless - Radio - Properties [Status]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_17
#@pytest.mark.WirelessProperties
async def test_gui_17_radio_status(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_17: Verifying Radio Status (Enable/Disable)")

    await validate_dropdown_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.STATUS_DROPDOWN,
        expected_options=["Enable", "Disable"],
        uci_cmd=RootCommands.get_radio_status(1),
        param_name="Status",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=parse_radio_status,
        test_all_options=True
    )


# =====================================================================
# GUI_18: Wireless - Radio - Properties [Link Type]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_18
#@pytest.mark.WirelessProperties
async def test_gui_18_link_type(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_18: Verifying Link Type (PTP/PTMP)")

    await validate_dropdown_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.LINK_TYPE_DROPDOWN,
        expected_options=["PTP", "PTMP"],
        uci_cmd=RootCommands.get_link_type(1),
        param_name="Link Type",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=parse_link_type,
        test_all_options=True
    )


# =====================================================================
# GUI_19: Wireless - Radio - Properties [Radio Mode]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_19
@pytest.mark.WirelessProperties
async def test_gui_19_radio_mode(gui_page, root_ssh, request):
    # Retrieve the fallback configurations from conftest.py options
    fallback_ip = request.config.getoption("--fallback-ip")
    local_ip = request.config.getoption("--local-ip")
    username = request.config.getoption("--username")
    password = request.config.getoption("--password")

    # 1. Establish an isolated SSH connection to the Fallback IP
    print(f"\n[+] Establishing isolated SSH connection to Fallback IP ({fallback_ip})...")
    fallback_ssh = AsyncGenericDriver(
        host=fallback_ip,
        auth_username=username,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh"
    )
    await fallback_ssh.open()

    try:
        # 2. Pivot the active GUI session to the Fallback IP to survive the CPE DHCP changes
        current_url = gui_page.url
        if fallback_ip not in current_url:
            print(f"    -> Pivoting GUI session to Fallback IP ({fallback_ip})...")
            match = re.search(r'(https?://)(?:[^/]+)(/.*)', current_url)
            if match:
                safe_url = f"https://{fallback_ip}{match.group(2)}"
                try:
                    await gui_page.goto(safe_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    print("    -> [DEBUG] Token carryover timed out. Attempting fresh navigation...")
                    await gui_page.goto(f"https://{fallback_ip}/admin/wireless/radio1", wait_until="domcontentloaded",
                                        timeout=15000)

            await gui_page.wait_for_timeout(2000)

        # ==========================================================
        # CRITICAL FIX: Did the router kick us to the login screen?
        # ==========================================================
        try:
            if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=2000):
                print("    -> [DEBUG] Kicked to login screen! Re-authenticating on Fallback IP...")
                await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.fill(username)
                await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).first.fill(password)
                await gui_page.locator(LoginPageLocators.LOGIN_BUTTON).first.click()
                await gui_page.wait_for_timeout(3000)  # Wait for dashboard routing
        except Exception:
            pass  # Not on login page, proceed normally
        # ==========================================================

        # Ensure we are on the correct page via the fallback IP
        await navigate_to_radio_properties_page(gui_page)
        print("\n[+] Starting GUI_19: Verifying Radio Mode (BTS/CPE)")

        # Pass in the fallback_ssh client instead of root_ssh
        await validate_dropdown_lifecycle(
            gui_page, fallback_ssh,
            locator=RadioPropertiesLocators.RADIO_MODE_DROPDOWN,
            expected_options=["BTS", "CPE"],
            uci_cmd=RootCommands.get_radio_mode(1),
            param_name="Radio Mode",
            fallback_url=RADIO_1_URL_CHUNK,
            parser=lambda raw: parse_radio_mode(raw, 1),
            test_all_options=True
        )
    finally:
        # Guarantee the background SSH connection is closed cleanly
        await fallback_ssh.close()
        print(f"    -> Closed isolated SSH connection to Fallback IP.")

        # Wait for the network bridge to physically come back online!
        print(f"    -> [TEARDOWN] Waiting 15 seconds for router hardware to stabilize...")
        await gui_page.wait_for_timeout(15000)

        # ==========================================================
        # TEARDOWN: Pivot the browser safely back to the Local IP
        # ==========================================================
        current_url = gui_page.url

        # CRITICAL FIX: Catch the Chrome Error URL too!
        if fallback_ip in current_url or "chrome-error" in current_url:
            print(f"    -> [TEARDOWN] Pivoting GUI session back to Primary IP ({local_ip})...")

            match = re.search(r'(https?://)(?:[^/]+)(/.*)', current_url)

            # If we have a valid URL with a token, swap the IP and go
            if match and "chrome-error" not in current_url:
                safe_url = f"https://{local_ip}{match.group(2)}"
                try:
                    await gui_page.goto(safe_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    await gui_page.goto(f"https://{local_ip}/cgi-bin/luci", wait_until="domcontentloaded",
                                        timeout=15000)

            # If we got Dinosaur'd, we lost the token. Go to base URL to force a clean re-login.
            else:
                print("    -> [TEARDOWN] Recovering from Chrome network crash...")
                await gui_page.goto(f"https://{local_ip}/cgi-bin/luci", wait_until="domcontentloaded", timeout=15000)

            await gui_page.wait_for_timeout(3000)

            # Re-authenticate on the Primary IP so GUI_20 doesn't get stuck!
            try:
                if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=3000):
                    print("    -> [TEARDOWN] Kicked to login screen! Re-authenticating on Primary IP...")
                    await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.fill(username)
                    await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).first.fill(password)
                    await gui_page.locator(LoginPageLocators.LOGIN_BUTTON).first.click()
                    await gui_page.wait_for_timeout(5000)  # Wait for dashboard routing to finish
            except Exception:
                pass

# =====================================================================
# GUI_20: Wireless - Radio - Properties [SSID]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_20
@pytest.mark.WirelessProperties
async def test_gui_20_ssid(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_20: Verifying SSID Input (Range 1-32 chars)")

    await validate_input_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.SSID_INPUT,
        valid_val="A_Valid_32_Character_SSID_123456",  # Exactly 32
        invalid_val="An_Invalid_33_Character_SSID_1234567",  # 33 characters
        uci_cmd=RootCommands.get_ssid(1),
        param_name="SSID",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=extract_uci_value
    )


# =====================================================================
# GUI_21: Wireless - Radio - Properties [Bandwidth]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_21
@pytest.mark.WirelessProperties
async def test_gui_21_bandwidth(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_21: Verifying Bandwidth (20 / 40 / 80 MHz)")

    await validate_dropdown_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.BANDWIDTH_DROPDOWN,
        expected_options=["20 MHz", "40 MHz", "80 MHz", "160 MHz"],
        uci_cmd=RootCommands.get_bandwidth(1),
        param_name="Bandwidth",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=parse_bandwidth,
        test_all_options=True
    )