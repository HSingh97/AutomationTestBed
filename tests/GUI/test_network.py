# tests/GUI/test_network.py

import pytest
from pages.locators import NetworkLocators
from pages.commands import RootCommands
from utils.validators import validate_param, validate_network_address

pytestmark = [pytest.mark.Network, pytest.mark.sanity]


# =====================================================================
# GUI_50: STATIC IPv4 CONFIGURATION
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_50
async def test_gui_50_network_ipv4_static(root_ssh, gui_page, bsu_ip):
    """
    Validates that the Static IPv4 Configuration set in the backend 
    is correctly reflected in the GUI Network -> IP Configuration page.
    """
    print("\n[+] Starting GUI_50: Cross-Validating Static IPv4 Configuration (Root -> GUI)")

    # 1. Fetch "Source of Truth" from Root Backend
    print("    -> Fetching IP details from Root CLI...")
    ssh_ipv4 = (await root_ssh.send_command(RootCommands.GET_IPv4)).result.strip()
    # If RootCommands does not have GET_NETMASK, we use the shell command directly
    ssh_mask = (await root_ssh.send_command("uci get network.lan.netmask")).result.strip()
    ssh_gw = (await root_ssh.send_command(RootCommands.GET_GATEWAYv4)).result.strip()

    # 2. Navigate to Network -> IP Configuration
    print("    -> Navigating to Network > IP Configuration...")
    await gui_page.locator(NetworkLocators.MENU_NETWORK).click()
    await gui_page.wait_for_timeout(1000)  # Short wait for menu expansion
    await gui_page.locator(NetworkLocators.SUBMENU_IP_CONFIG).click()

    # Wait for the page content to load
    await gui_page.wait_for_selector(NetworkLocators.IPv4_ADDRESS)
    await gui_page.wait_for_timeout(2000)

    # 3. Scrape GUI Input Values
    # We use .input_value() because these are editable <input> fields
    print("    -> Scraping GUI configuration values...")
    gui_ipv4 = await gui_page.locator(NetworkLocators.IPv4_ADDRESS).input_value()
    gui_mask = await gui_page.locator(NetworkLocators.IPv4_NETMASK).input_value()
    gui_gw = await gui_page.locator(NetworkLocators.IPv4_GATEWAY).input_value()

    # 4. Cross-Validation Assertions
    # Using your existing shared validators
    validate_network_address("IPv4 ADDRESS", ssh_ipv4, "", gui_ipv4)
    validate_param("NETMASK", ssh_mask, gui_mask)
    validate_param("GATEWAY", ssh_gw, gui_gw)

    print("[+] GUI_50 Validation Complete.")