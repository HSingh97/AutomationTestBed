# tests/GUI/test_network.py

import pytest
from pages.locators import NetworkLocators
from pages.commands import RootCommands
from utils.validators import validate_param, validate_network_address

# Category markers for Jenkins and sorting
pytestmark = [pytest.mark.Network, pytest.mark.sanity]


# =====================================================================
# GUI_50: STATIC IPv4 CONFIGURATION CROSS-VALIDATION
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_50
async def test_gui_50_network_ipv4_static(root_ssh, gui_page, bsu_ip):
    """
    GUI_50: Validates that Static IPv4 details (IP, Netmask, Gateway)
    in the backend match the values displayed in the Network Configuration GUI.
    """
    print(f"\n[+] Starting GUI_50: Cross-Validating Static IPv4 Configuration on {bsu_ip}")

    # 1. Fetch Backend Data (Source of Truth)
    print("    -> Fetching IP details from Root CLI...")
    ssh_ipv4 = (await root_ssh.send_command(RootCommands.GET_IPv4)).result.strip()
    # Direct UCI call for Netmask to ensure accuracy
    ssh_mask = (await root_ssh.send_command("uci get network.lan.netmask")).result.strip()
    ssh_gw = (await root_ssh.send_command(RootCommands.GET_GATEWAYv4)).result.strip()

    # 2. GUI Navigation
    print("    -> Navigating to Network > IP Configuration...")
    await gui_page.locator(NetworkLocators.MENU_NETWORK).click()
    await gui_page.wait_for_timeout(1000)
    await gui_page.locator(NetworkLocators.SUBMENU_IP_CONFIG).click()

    # Wait for the specific IP input field to be visible before scraping
    await gui_page.wait_for_selector(NetworkLocators.IPv4_ADDRESS)
    await gui_page.wait_for_timeout(2000)

    # 3. Scrape Frontend Data
    print("    -> Scraping GUI input values...")
    gui_ipv4 = await gui_page.locator(NetworkLocators.IPv4_ADDRESS).input_value()
    gui_mask = await gui_page.locator(NetworkLocators.IPv4_NETMASK).input_value()
    gui_gw = await gui_page.locator(NetworkLocators.IPv4_GATEWAY).input_value()

    # 4. Validation
    # validate_network_address handles variations in IP formatting
    validate_network_address("IPv4 ADDRESS", ssh_ipv4, "", gui_ipv4)
    validate_param("NETMASK", ssh_mask, gui_mask)
    validate_param("GATEWAY", ssh_gw, gui_gw)

    print("[+] GUI_50 Validation Complete.")