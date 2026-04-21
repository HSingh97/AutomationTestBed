import pytest
from pages.locators import TopPanelLocators, LoginPageLocators
from pages.commands import RootCommands
from utils.validators import validate_param, validate_uptime
from utils.parsers import parse_desc_info  # Imported the new parser

pytestmark = pytest.mark.sanity


# =====================================================================
# GUI_05: GUI Top Panel - Logo Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_05
@pytest.mark.TopPanel
async def test_gui_05_top_panel_logo(gui_page, bsu_ip):
    print("\n[+] Starting GUI_05: Verifying Top Panel Logo Hyperlink")

    logo_element = gui_page.locator(TopPanelLocators.LOGO)

    assert await logo_element.count() > 0, "Senao Logo is not present on the top panel."

    href_value = await logo_element.get_attribute("href")
    print(f"    -> Logo href attribute: {href_value}")

    await logo_element.click()
    await gui_page.wait_for_load_state("networkidle")

    current_url = gui_page.url
    assert bsu_ip in current_url, f"Expected URL to contain IP {bsu_ip}, but got {current_url}"
    print("[+] GUI_05 Logo Hyperlink Validation Complete.")


# =====================================================================
# GUI_06: GUI Top Panel - Parameter Verification
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_06
@pytest.mark.TopPanel
async def test_gui_06_top_panel_parameters(root_ssh, gui_page):
    print("\n[+] Starting GUI_06: Cross-Validating Top Panel Parameters (Root -> GUI)")

    # Fetch values from Backend
    ssh_sysname = (await root_ssh.send_command(RootCommands.GET_SYSNAME)).result.strip()
    ssh_sw_ver = (await root_ssh.send_command(RootCommands.GET_SW_VERSION)).result.strip()
    ssh_serial = (await root_ssh.send_command(RootCommands.GET_SERIAL_NO)).result.strip()
    ssh_uptime = (await root_ssh.send_command(RootCommands.GET_UPTIME)).result.strip()

    await gui_page.wait_for_timeout(2000)

    # Fast-fail locator checks
    sysname_loc = gui_page.locator(TopPanelLocators.TOP_SYSNAME)
    desc_loc = gui_page.locator(TopPanelLocators.TOP_DESC_INFO)
    uptime_loc = gui_page.locator(TopPanelLocators.TOP_UPTIME)

    assert await sysname_loc.is_visible(), f"Could not find Sysname using locator: {TopPanelLocators.TOP_SYSNAME}"
    assert await desc_loc.is_visible(), f"Could not find Combined Desc (SW/Serial) using locator: {TopPanelLocators.TOP_DESC_INFO}"
    assert await uptime_loc.is_visible(), f"Could not find Uptime using locator: {TopPanelLocators.TOP_UPTIME}"

    # Fetch values from GUI Top Panel
    gui_sysname = await sysname_loc.inner_text()
    gui_desc = await desc_loc.inner_text()
    gui_uptime = await uptime_loc.inner_text()

    # Parse the combined description string into its two components
    gui_sw_ver, gui_serial = parse_desc_info(gui_desc)

    # Validate matches
    validate_param("SYSNAME", ssh_sysname, gui_sysname)
    validate_param("SW VERSION", ssh_sw_ver, gui_sw_ver)
    validate_param("SERIAL NO.", ssh_serial, gui_serial.upper())
    validate_uptime(ssh_uptime, gui_uptime, tolerance_seconds=10)

    print("[+] GUI_06 Top Panel Parameters Validation Complete.")


# =====================================================================
# GUI_07: GUI Top Panel - Radio Navigation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_07
@pytest.mark.TopPanel
async def test_gui_07_top_panel_radio_redirect(gui_page):
    print("\n[+] Starting GUI_07: Verifying Radio Navigation from Top Panel")

    # 1. Check Radio 0 (2.4 GHz) Submenu
    radio_0 = gui_page.locator(TopPanelLocators.SUBMENU_RADIO_0)
    if await radio_0.is_visible():
        await radio_0.click()

        # Wait specifically for the URL to change instead of networkidle
        try:
            await gui_page.wait_for_url("**/admin/monitor/radio0*", timeout=10000)
            print("    -> Successfully redirected to 2.4 GHz statistics.")
        except Exception:
            assert False, f"Did not redirect to 2.4GHz Stats. Current URL: {gui_page.url}"
    else:
        print("    -> WARNING: Radio 0 (2.4 GHz) submenu not visible.")

    # 2. Check Radio 1 Submenu
    radio_1 = gui_page.locator(TopPanelLocators.SUBMENU_RADIO_1)
    if await radio_1.is_visible():
        await radio_1.click()

        # Wait specifically for the URL to change instead of networkidle
        try:
            await gui_page.wait_for_url("**/admin/monitor/radio1*", timeout=10000)
            print("    -> Successfully redirected to Radio 1 statistics.")
        except Exception:
            assert False, f"Did not redirect to Radio 1 Stats. Current URL: {gui_page.url}"
    else:
        print("    -> WARNING: Radio 1 submenu not visible.")

    print("[+] GUI_07 Radio Navigation Validation Complete.")


# =====================================================================
# GUI_08: GUI Top Panel - Home and Apply Button
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_08
@pytest.mark.TopPanel
async def test_gui_08_home_and_apply_buttons(gui_page, root_ssh):
    print("\n[+] Starting GUI_08: Verifying Home and Apply Buttons")

    # 1. Navigate to any module other than Home
    # Since the submenus are already visible, we skip clicking a master menu and click the link directly
    print("    -> Navigating away from Home page...")
    radio_0 = gui_page.locator(TopPanelLocators.SUBMENU_RADIO_0)

    assert await radio_0.is_visible(), "Could not find a link to navigate away from Home."
    await radio_0.click()

    try:
        await gui_page.wait_for_url("**/admin/monitor/radio0*", timeout=10000)
        print("    -> Successfully navigated away from Home page.")
    except Exception:
        assert False, f"Failed to navigate away from Home page. Current URL: {gui_page.url}"

    # 2. Click the Home button (Top Right House Icon)
    print("    -> Clicking Home button...")
    home_btn = gui_page.locator(TopPanelLocators.HOME_BUTTON).first
    assert await home_btn.is_visible(), "Home button (House icon) is incorrect or not visible."
    await home_btn.click()

    # Wait for the URL to return to the home/summary state
    try:
        await gui_page.wait_for_url("**/admin/home*", timeout=10000)
        print("    -> Home button successfully redirected to Home Page.")
    except Exception:
        assert False, f"Home button did not redirect properly. Current URL: {gui_page.url}"

    # 3. Test Apply Button (Top Right Floppy Disk Icon)
    print("    -> Checking Apply button...")
    apply_btn = gui_page.locator(TopPanelLocators.APPLY_BUTTON).first

    if await apply_btn.is_enabled():
        await apply_btn.click()
        await gui_page.wait_for_timeout(3000)
        print("    -> Apply button successfully clicked.")
    else:
        print("    -> WARNING: Apply button is disabled (expected if no changes were made).")

    print("[+] GUI_08 Home and Apply Button Validation Complete.")

# =====================================================================
# GUI_09: GUI Top Panel - Reboot Functionality
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_09
@pytest.mark.TopPanel
async def test_gui_09_reboot_device(gui_page):
    print("\n[+] Starting GUI_09: Verifying Reboot Button functionality")

    reboot_btn = gui_page.locator(TopPanelLocators.REBOOT_BUTTON)
    assert await reboot_btn.is_visible(), "Reboot button locator is incorrect or not visible."
    await reboot_btn.click()

    confirm_btn = gui_page.locator(TopPanelLocators.REBOOT_CONFIRM)
    if await confirm_btn.is_visible():
        print("    -> Reboot confirmation prompt appeared.")
    else:
        print("    -> Reboot initiated immediately or no prompt found.")

    print("[+] GUI_09 Reboot Initiation Complete. (Actual device restart bypassed to maintain session state)")


# =====================================================================
# GUI_10: GUI Top Panel - Logout
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_10
@pytest.mark.TopPanel
async def test_gui_10_logout(gui_page):
    print("\n[+] Starting GUI_10: Verifying Logout functionality")

    logout_btn = gui_page.locator(TopPanelLocators.LOGOUT_BUTTON)
    assert await logout_btn.is_visible(), "Logout button locator is incorrect or not visible."
    await logout_btn.click()

    await gui_page.wait_for_load_state("networkidle")

    username_input = gui_page.locator(LoginPageLocators.USERNAME_INPUT)
    assert await username_input.is_visible(), "Logout button did not redirect to the login page properly."

    print("[+] GUI_10 Logout Validation Complete.")