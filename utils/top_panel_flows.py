from pages.commands import RootCommands
from pages.locators import TopPanelLocators
from utils.parsers import clean_ssh_output, extract_hostname_value, parse_desc_info
from utils.validators import validate_param, validate_uptime


async def assert_top_panel_logo(gui_page, bsu_ip):
    logo_element = gui_page.locator(TopPanelLocators.LOGO)
    assert await logo_element.count() > 0, "Senao Logo is not present on the top panel."
    await logo_element.click()
    await gui_page.wait_for_load_state("networkidle")
    assert bsu_ip in gui_page.url, f"Expected URL to contain IP {bsu_ip}, but got {gui_page.url}"


async def assert_top_panel_parameters(root_ssh, gui_page):
    ssh_sysname = extract_hostname_value((await root_ssh.send_command(RootCommands.GET_SYSNAME)).result)
    ssh_sw_ver = clean_ssh_output((await root_ssh.send_command(RootCommands.GET_SW_VERSION)).result)
    ssh_serial = clean_ssh_output((await root_ssh.send_command(RootCommands.GET_SERIAL_NO)).result)
    ssh_uptime = clean_ssh_output((await root_ssh.send_command(RootCommands.GET_UPTIME)).result)
    await gui_page.wait_for_timeout(2000)

    sysname_loc = gui_page.locator(TopPanelLocators.TOP_SYSNAME)
    desc_loc = gui_page.locator(TopPanelLocators.TOP_DESC_INFO)
    uptime_loc = gui_page.locator(TopPanelLocators.TOP_UPTIME)
    assert await sysname_loc.is_visible(), f"Could not find Sysname using locator: {TopPanelLocators.TOP_SYSNAME}"
    assert await desc_loc.is_visible(), f"Could not find combined desc using locator: {TopPanelLocators.TOP_DESC_INFO}"
    assert await uptime_loc.is_visible(), f"Could not find Uptime using locator: {TopPanelLocators.TOP_UPTIME}"

    gui_sysname = await sysname_loc.inner_text()
    gui_desc = await desc_loc.inner_text()
    gui_uptime = await uptime_loc.inner_text()
    gui_sw_ver, gui_serial = parse_desc_info(gui_desc)

    validate_param("SYSNAME", ssh_sysname, gui_sysname)
    validate_param("SW VERSION", ssh_sw_ver, gui_sw_ver)
    validate_param("SERIAL NO.", ssh_serial, gui_serial.upper())
    validate_uptime(ssh_uptime, gui_uptime, tolerance_seconds=10)


async def assert_top_panel_radio_redirect(gui_page):
    radio_0 = gui_page.locator(TopPanelLocators.SUBMENU_RADIO_0)
    if await radio_0.is_visible():
        await radio_0.click()
        await gui_page.wait_for_url("**/admin/monitor/radio0*", timeout=10000)

    radio_1 = gui_page.locator(TopPanelLocators.SUBMENU_RADIO_1)
    if await radio_1.is_visible():
        await radio_1.click()
        await gui_page.wait_for_url("**/admin/monitor/radio1*", timeout=10000)


async def assert_home_and_apply_buttons(gui_page):
    radio_0 = gui_page.locator(TopPanelLocators.SUBMENU_RADIO_0)
    assert await radio_0.is_visible(), "Could not find a link to navigate away from Home."
    await radio_0.click()
    await gui_page.wait_for_url("**/admin/monitor/radio0*", timeout=10000)

    home_btn = gui_page.locator(TopPanelLocators.HOME_BUTTON).first
    assert await home_btn.is_visible(), "Home button is incorrect or not visible."
    await home_btn.click()
    await gui_page.wait_for_url("**/admin/home*", timeout=10000)

    apply_btn = gui_page.locator(TopPanelLocators.APPLY_BUTTON).first
    if await apply_btn.is_enabled():
        await apply_btn.click()
        await gui_page.wait_for_timeout(3000)


async def assert_reboot_button_visible(gui_page):
    reboot_btn = gui_page.locator(TopPanelLocators.REBOOT_BUTTON)
    assert await reboot_btn.is_visible(), "Reboot button locator is incorrect or not visible."
    await reboot_btn.click()
    confirm_btn = gui_page.locator(TopPanelLocators.REBOOT_CONFIRM)
    if await confirm_btn.is_visible():
        return


async def assert_logout(gui_page):
    logout_btn = gui_page.locator(TopPanelLocators.LOGOUT_BUTTON).first
    await logout_btn.wait_for(state="visible", timeout=10000)
    await gui_page.wait_for_timeout(1000)
    await logout_btn.click()
