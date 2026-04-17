# tests/GUI/test_network.py

import pytest
from pages.locators import NetworkLocators
from pages.commands import RootCommands
from utils.validators import validate_param, validate_network_address

pytestmark = [pytest.mark.Network, pytest.mark.sanity]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_50
async def test_gui_50_network_ipv4_static(root_ssh, gui_page, bsu_ip):

    print(f"\n[+] Starting GUI_50: Cross-Validating Static IPv4 Configuration on {bsu_ip}")

    # 1. Fetch Backend Data (Source of Truth)
    print("    -> Fetching IP details from Root CLI...")
    ssh_ipv4 = (await root_ssh.send_command(RootCommands.GET_IPv4)).result.strip()
    ssh_mask = (await root_ssh.send_command("uci get network.lan.netmask")).result.strip()
    ssh_gw = (await root_ssh.send_command(RootCommands.GET_GATEWAYv4)).result.strip()

    # 2. GUI Navigation
    print("    -> Navigating to Network > IP Configuration...")

    # Using .first to bypass the "resolved to 3 elements" strict mode error
    await gui_page.locator(NetworkLocators.MENU_NETWORK).first.click()
    await gui_page.wait_for_timeout(1000)

    # Click the submenu specifically inside the sidebar
    await gui_page.locator(NetworkLocators.SUBMENU_IP_CONFIG).first.click()

    # 3. Wait for the configuration form to render
    await gui_page.wait_for_selector(NetworkLocators.IPv4_ADDRESS, state="visible")
    await gui_page.wait_for_timeout(1000)

    # 4. Scrape Frontend Data
    print("    -> Scraping values from the technical name attributes...")
    gui_ipv4 = await gui_page.locator(NetworkLocators.IPv4_ADDRESS).input_value()
    gui_mask = await gui_page.locator(NetworkLocators.IPv4_NETMASK).input_value()
    gui_gw = await gui_page.locator(NetworkLocators.IPv4_GATEWAY).input_value()

    # 5. Validation
    validate_network_address("IPv4 ADDRESS", ssh_ipv4, "", gui_ipv4)
    validate_param("NETMASK", ssh_mask, gui_mask)
    validate_param("GATEWAY", ssh_gw, gui_gw)

    print("[+] GUI_50 Validation Successful.")