import pytest
import pytest_check as check
import os
import re
import datetime

from pages.locators import SummaryLocators, SummaryNetworkLocators
from pages.commands import RootCommands

pytestmark = pytest.mark.sanity


def parse_device_time(time_str):
    clean_str = re.sub(r'\s[A-Z]{3,4}\s', ' ', time_str)
    clean_str = re.sub(r'\s+', ' ', clean_str).strip()
    return datetime.datetime.strptime(clean_str, "%a %b %d %H:%M:%S %Y")


# =====================================================================
# GUI_01: SYSTEM SUMMARY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_01
async def test_gui_01_summary_system(root_ssh, gui_page, bsu_ip):
    print("\n[+] Starting GUI_01: Cross-Validating System Summary (Root -> GUI)")

    print("    -> Pulling all 8 parameters from Root Backend...")
    cli_model = (await root_ssh.send_command(RootCommands.GET_MODEL)).result.strip()
    cli_hw = (await root_ssh.send_command(RootCommands.GET_HW_VERSION)).result.strip()
    cli_bootloader = (await root_ssh.send_command(RootCommands.GET_BOOTLOADER)).result.strip()
    cli_time = (await root_ssh.send_command(RootCommands.GET_TIME)).result.strip()
    cli_temp = (await root_ssh.send_command(RootCommands.GET_TEMP)).result.strip()
    cli_gps = (await root_ssh.send_command(RootCommands.GET_GPS)).result.strip()
    cli_elevation = (await root_ssh.send_command(RootCommands.GET_ELEVATION)).result.strip()
    cli_cpu_mem = (await root_ssh.send_command(RootCommands.GET_CPU_MEM)).result.strip()

    # The gui_page is already on the dashboard from the conftest login!
    print("    -> Scraping GUI data fields...")
    gui_model = await gui_page.locator(SummaryLocators.MODEL).inner_text()
    gui_hw = await gui_page.locator(SummaryLocators.HW_VERSION).inner_text()
    gui_bootloader = await gui_page.locator(SummaryLocators.BOOTLOADER).inner_text()
    gui_time = await gui_page.locator(SummaryLocators.LOCAL_TIME).inner_text()
    gui_temp = await gui_page.locator(SummaryLocators.TEMPERATURE).inner_text()
    gui_gps = await gui_page.locator(SummaryLocators.GPS).inner_text()
    gui_elevation = await gui_page.locator(SummaryLocators.ELEVATION).inner_text()
    gui_cpu_mem = await gui_page.locator(SummaryLocators.CPU_MEMORY).inner_text()

    print("    -> Validating Backend vs Frontend...")
    check.is_in(cli_model, gui_model, f"Model Mismatch! SSH: {cli_model} | GUI: {gui_model}")
    check.is_in(cli_hw, gui_hw, f"HW Version Mismatch! SSH: {cli_hw} | GUI: {gui_hw}")
    check.is_in(cli_bootloader, gui_bootloader, f"Bootloader Mismatch! SSH: {cli_bootloader} | GUI: {gui_bootloader}")

    try:
        cli_dt = parse_device_time(cli_time)
        gui_dt = parse_device_time(gui_time)
        time_diff_seconds = abs((gui_dt - cli_dt).total_seconds())
        check.less_equal(time_diff_seconds, 10,
                         f"Time Mismatch! Delta is {time_diff_seconds}s. CLI: '{cli_time}' | GUI: '{gui_time}'")
    except Exception as e:
        check.fail(f"Failed to parse time strings. CLI: '{cli_time}' | GUI: '{gui_time}' | Error: {e}")

    check.is_in(cli_temp, gui_temp, f"Temp Mismatch! SSH: {cli_temp} | GUI: {gui_temp}")
    check.is_in(cli_gps, gui_gps, f"GPS Mismatch! SSH: {cli_gps} | GUI: {gui_gps}")
    check.is_in(cli_elevation, gui_elevation, f"Elevation Mismatch! SSH: {cli_elevation} | GUI: {gui_elevation}")

    print("[+] GUI_01 Soft Validation Complete.")


# =====================================================================
# GUI_02: NETWORK SUMMARY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_02
async def test_gui_02_summary_network(root_ssh, gui_page, bsu_ip):
    print("\n[+] Starting GUI_02: Cross-Validating Network Summary (Root -> GUI)")

    print("    -> Pulling network parameters from Root Backend...")
    ssh_ip = (await root_ssh.send_command(RootCommands.GET_IP)).result.strip()
    ssh_gw = (await root_ssh.send_command(RootCommands.GET_GATEWAY)).result.strip()
    ssh_mac1 = (await root_ssh.send_command(RootCommands.GET_MAC_LAN1)).result.strip().upper()
    ssh_mac2 = (await root_ssh.send_command(RootCommands.GET_MAC_LAN2)).result.strip().upper()
    ssh_speed1 = (await root_ssh.send_command(RootCommands.GET_SPEED_DUPLEX_LAN1)).result.strip()
    ssh_speed2 = (await root_ssh.send_command(RootCommands.GET_SPEED_DUPLEX_LAN2)).result.strip()
    ssh_cable1 = (await root_ssh.send_command(RootCommands.GET_CABLE_LENGTH_LAN1)).result.strip()
    ssh_cable2 = (await root_ssh.send_command(RootCommands.GET_CABLE_LENGTH_LAN2)).result.strip()

    # If Senao requires you to click a "Network" tab, uncomment this:
    # await gui_page.click(SummaryNetworkLocators.NETWORK_TAB_BUTTON)
    await gui_page.wait_for_timeout(5000)

    print("    -> Scraping GUI data fields...")
    gui_ip = await gui_page.locator(SummaryNetworkLocators.IP_ADDRESS).inner_text()
    gui_gw = await gui_page.locator(SummaryNetworkLocators.GATEWAY).inner_text()
    gui_mac1 = await gui_page.locator(SummaryNetworkLocators.MAC_LAN1).inner_text()
    gui_mac2 = await gui_page.locator(SummaryNetworkLocators.MAC_LAN2).inner_text()
    gui_speed1 = await gui_page.locator(SummaryNetworkLocators.SPEED_DUPLEX_LAN1).inner_text()
    gui_speed2 = await gui_page.locator(SummaryNetworkLocators.SPEED_DUPLEX_LAN2).inner_text()
    gui_cable1 = await gui_page.locator(SummaryNetworkLocators.CABLE_LENGTH_LAN1).inner_text()
    gui_cable2 = await gui_page.locator(SummaryNetworkLocators.CABLE_LENGTH_LAN2).inner_text()

    print("    -> Validating Backend vs Frontend...")
    check.is_in(ssh_ip, gui_ip, f"IP Mismatch! ssh: {ssh_ip} | GUI: {gui_ip}")
    check.is_in(ssh_gw, gui_gw, f"Gateway Mismatch! ssh: {ssh_gw} | GUI: {gui_gw}")
    check.is_in(ssh_mac1, gui_mac1.upper(), f"LAN 1 MAC Mismatch! ssh: {ssh_mac1} | GUI: {gui_mac1}")
    check.is_in(ssh_mac2, gui_mac2.upper(), f"LAN 2 MAC Mismatch! ssh: {ssh_mac2} | GUI: {gui_mac2}")

    check.is_true(bool(ssh_speed1) and ssh_speed1 in gui_speed1,
                  f"LAN 1 Speed Mismatch! ssh: {ssh_speed1} | GUI: {gui_speed1}")
    check.is_true(bool(ssh_speed2) and ssh_speed2 in gui_speed2,
                  f"LAN 2 Speed Mismatch! ssh: {ssh_speed2} | GUI: {gui_speed2}")
    check.is_in(ssh_cable1, gui_cable1, f"LAN 1 Cable Length Mismatch! ssh: {ssh_cable1} | GUI: {gui_cable1}")
    check.is_in(ssh_cable2, gui_cable2, f"LAN 2 Cable Length Mismatch! ssh: {ssh_cable2} | GUI: {gui_cable2}")

    print("[+] GUI_02 Soft Validation Complete.")