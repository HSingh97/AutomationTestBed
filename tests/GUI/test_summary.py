import pytest

from pages.locators import SummaryLocators, SummaryNetworkLocators, SummaryPerformanceLocators, SummaryWirelessLocators, TopPanelLocators
from pages.commands import RootCommands

# Import all shared utilities
from utils.parsers import (
    parse_radio_status, parse_link_type, parse_radio_mode,
    parse_bandwidth, parse_configured_channel, parse_security,
    extract_uci_value, parse_ifconfig_mac, parse_iwconfig_active_channel
)
from utils.validators import (
    validate_param, validate_network_address, validate_time,
    validate_temperature, validate_cpu_mem, validate_speed_duplex,
    validate_throughput, validate_percentage
)

pytestmark = pytest.mark.sanity


# =====================================================================
# GUI_01: SYSTEM SUMMARY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_01
@pytest.mark.Summary
async def test_gui_01_summary_system(root_ssh, gui_page, bsu_ip):
    print("\n[+] Starting GUI_01: Cross-Validating System Summary (Root -> GUI)")

    print("    -> Navigating to Dashboard via Top Panel Logo...")
    await gui_page.locator(TopPanelLocators.LOGO).first.click()
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)

    ssh_model = (await root_ssh.send_command(RootCommands.GET_MODEL)).result.strip()
    ssh_hw = (await root_ssh.send_command(RootCommands.GET_HW_VERSION)).result.strip()
    ssh_bootloader = (await root_ssh.send_command(RootCommands.GET_BOOTLOADER)).result.strip()
    ssh_time = (await root_ssh.send_command(RootCommands.GET_TIME)).result.strip()
    ssh_temp = (await root_ssh.send_command(RootCommands.GET_TEMP)).result.strip()
    ssh_gps = (await root_ssh.send_command(RootCommands.GET_GPS)).result.strip()
    ssh_elevation = (await root_ssh.send_command(RootCommands.GET_ELEVATION)).result.strip()
    ssh_cpu = (await root_ssh.send_command(RootCommands.GET_CPU)).result.strip()
    ssh_mem = (await root_ssh.send_command(RootCommands.GET_MEM)).result.strip()

    await gui_page.wait_for_timeout(5000)

    gui_model = await gui_page.locator(SummaryLocators.MODEL).inner_text()
    gui_hw = await gui_page.locator(SummaryLocators.HW_VERSION).inner_text()
    gui_bootloader = await gui_page.locator(SummaryLocators.BOOTLOADER).inner_text()
    gui_time = await gui_page.locator(SummaryLocators.LOCAL_TIME).inner_text()
    gui_temp = await gui_page.locator(SummaryLocators.TEMPERATURE).inner_text()
    gui_gps = await gui_page.locator(SummaryLocators.GPS).inner_text()
    gui_elevation = await gui_page.locator(SummaryLocators.ELEVATION).inner_text()
    gui_cpu_mem = await gui_page.locator(SummaryLocators.CPU_MEMORY).inner_text()

    validate_param("MODEL", ssh_model, gui_model)
    validate_param("HW VERSION", ssh_hw, gui_hw)
    validate_param("BOOTLOADER", ssh_bootloader, gui_bootloader)
    validate_time("LOCAL TIME", ssh_time, gui_time)
    validate_temperature("TEMPERATURE", ssh_temp, gui_temp, tolerance=1.0)
    validate_param("GPS", ssh_gps, gui_gps)
    validate_param("ELEVATION", ssh_elevation, gui_elevation)
    validate_cpu_mem(ssh_cpu, ssh_mem, gui_cpu_mem, tolerance=5.0)

    print("[+] GUI_01 Soft Validation Complete.")


# =====================================================================
# GUI_02: NETWORK SUMMARY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_02
@pytest.mark.Summary
async def test_gui_02_summary_network(root_ssh, gui_page, bsu_ip):
    print("\n[+] Starting GUI_02: Cross-Validating Network Summary (Root -> GUI)")

    print("    -> Navigating to Dashboard via Top Panel Logo...")
    await gui_page.locator(TopPanelLocators.LOGO).first.click()
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)

    ssh_ipv4 = (await root_ssh.send_command(RootCommands.GET_IPv4)).result.strip()
    ssh_ipv6 = (await root_ssh.send_command(RootCommands.GET_IPv6)).result.strip()
    ssh_gw_v4 = (await root_ssh.send_command(RootCommands.GET_GATEWAYv4)).result.strip()
    ssh_gw_v6 = (await root_ssh.send_command(RootCommands.GET_GATEWAYv6)).result.strip()

    await gui_page.wait_for_timeout(5000)

    gui_ip = await gui_page.locator(SummaryNetworkLocators.IP_ADDRESS).inner_text()
    gui_gw = await gui_page.locator(SummaryNetworkLocators.GATEWAY).inner_text()

    validate_network_address("IP ADDRESS", ssh_ipv4, ssh_ipv6, gui_ip)
    validate_network_address("GATEWAY", ssh_gw_v4, ssh_gw_v6, gui_gw)

    lan_num = 1
    while True:
        mac_locator = getattr(SummaryNetworkLocators, f"MAC_LAN{lan_num}", None)
        speed_locator = getattr(SummaryNetworkLocators, f"SPEED_DUPLEX_LAN{lan_num}", None)
        cable_locator = getattr(SummaryNetworkLocators, f"CABLE_LENGTH_LAN{lan_num}", None)

        if not mac_locator or not speed_locator or not cable_locator: break
        if await gui_page.locator(mac_locator).count() == 0: break

        eth_idx = lan_num - 1

        ssh_mac = (await root_ssh.send_command(RootCommands.get_mac_lan(eth_idx))).result.strip().upper()
        ssh_speed = (await root_ssh.send_command(RootCommands.get_speed_lan(eth_idx))).result.strip()
        ssh_duplex = (await root_ssh.send_command(RootCommands.get_duplex_lan(eth_idx))).result.strip()
        ssh_cable = (await root_ssh.send_command(RootCommands.get_cable_length_lan(eth_idx))).result.strip()

        gui_mac = await gui_page.locator(mac_locator).inner_text()
        gui_speed = await gui_page.locator(speed_locator).inner_text()
        gui_cable = await gui_page.locator(cable_locator).inner_text()

        validate_param(f"MAC LAN{lan_num}", ssh_mac, gui_mac.upper())
        validate_speed_duplex(f"SPEED LAN{lan_num}", ssh_speed, ssh_duplex, gui_speed)
        validate_param(f"CABLE LENGTH LAN{lan_num}", ssh_cable, gui_cable)

        lan_num += 1

    print("[+] GUI_02 Soft Validation Complete.")


# =====================================================================
# GUI_03: PERFORMANCE SUMMARY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_03
@pytest.mark.Summary
async def test_gui_03_summary_performance(root_ssh, gui_page, bsu_ip):
    print("\n[+] Starting GUI_03: Cross-Validating Performance Summary (Root -> GUI)")

    print("    -> Navigating to Dashboard via Top Panel Logo...")
    await gui_page.locator(TopPanelLocators.LOGO).first.click()
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)

    ssh_tx_r1 = (await root_ssh.send_command(RootCommands.GET_TX_R1)).result.strip()
    ssh_rx_r1 = (await root_ssh.send_command(RootCommands.GET_RX_R1)).result.strip()

    await gui_page.wait_for_timeout(5000)

    gui_tx_r1 = await gui_page.locator(SummaryPerformanceLocators.TX_R1).inner_text()
    gui_rx_r1 = await gui_page.locator(SummaryPerformanceLocators.RX_R1).inner_text()

    validate_throughput("TX R1", ssh_tx_r1, gui_tx_r1, tolerance=20.0)
    validate_throughput("RX R1", ssh_rx_r1, gui_rx_r1, tolerance=20.0)

    lan_num = 1
    while True:
        tx_locator = getattr(SummaryPerformanceLocators, f"TX_LAN{lan_num}", None)
        rx_locator = getattr(SummaryPerformanceLocators, f"RX_LAN{lan_num}", None)

        if not tx_locator or not rx_locator: break
        if await gui_page.locator(tx_locator).count() == 0: break

        eth_idx = lan_num - 1

        ssh_tx_lan = (await root_ssh.send_command(RootCommands.get_tx_lan(eth_idx))).result.strip()
        ssh_rx_lan = (await root_ssh.send_command(RootCommands.get_rx_lan(eth_idx))).result.strip()

        gui_tx_lan = await gui_page.locator(tx_locator).inner_text()
        gui_rx_lan = await gui_page.locator(rx_locator).inner_text()

        validate_throughput(f"TX LAN{lan_num}", ssh_tx_lan, gui_tx_lan, tolerance=20.0)
        validate_throughput(f"RX LAN{lan_num}", ssh_rx_lan, gui_rx_lan, tolerance=20.0)

        lan_num += 1

    print("[+] GUI_03 Soft Validation Complete.")


# =====================================================================
# GUI_04: WIRELESS SUMMARY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_04
@pytest.mark.Summary
async def test_gui_04_summary_wireless(root_ssh, gui_page, bsu_ip):
    print("\n[+] Starting GUI_04: Cross-Validating Wireless Summary (Root -> GUI)")

    print("    -> Navigating to Dashboard via Top Panel Logo...")
    await gui_page.locator(TopPanelLocators.LOGO).first.click()
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)

    await gui_page.wait_for_timeout(5000)
    print("    -> Commencing Dynamic Wireless Radio Discovery & Validation...")

    radio_num = 0
    while radio_num <= 1:
        status_loc = SummaryWirelessLocators.RADIO_STATUS.format(radio_num)

        if await gui_page.locator(status_loc).count() == 0:
            if radio_num == 0:
                print("    -> WARNING: No Wireless Radios found on the GUI page!")
            break

        print(f"\n    -> [Radio {radio_num}] Pulling parameters from Root Backend...")
        wifi_idx = radio_num

        ssh_status_raw = (await root_ssh.send_command(RootCommands.get_radio_status(wifi_idx))).result.strip()
        ssh_status = parse_radio_status(ssh_status_raw)

        iwconfig_out = (await root_ssh.send_command(RootCommands.get_mac_wireless(wifi_idx))).result.strip()
        ssh_mac = parse_ifconfig_mac(iwconfig_out)

        # 2. Fetch Active Channel using iwconfig
        iwconfig_out = (await root_ssh.send_command(RootCommands.get_active_channel(wifi_idx))).result.strip()
        ssh_act_ch = parse_iwconfig_active_channel(iwconfig_out)

        # Apply 10 MHz offset exclusively for Radio 1 active channel
        if radio_num == 1 and ssh_act_ch.isdigit():
            ssh_act_ch = str(int(ssh_act_ch) - 10)

        ssh_link_raw = (await root_ssh.send_command(RootCommands.get_link_type(wifi_idx))).result.strip()
        ssh_link = parse_link_type(ssh_link_raw)

        ssh_mode_raw = (await root_ssh.send_command(RootCommands.get_radio_mode(wifi_idx))).result.strip()
        ssh_mode = parse_radio_mode(ssh_mode_raw, radio_num)

        ssh_band_raw = (await root_ssh.send_command(RootCommands.get_bandwidth(wifi_idx))).result.strip()
        ssh_band = parse_bandwidth(ssh_band_raw)

        ssh_ssid_raw = (await root_ssh.send_command(RootCommands.get_ssid(wifi_idx))).result.strip()
        ssh_ssid = extract_uci_value(ssh_ssid_raw)

        ssh_conf_ch_raw = (await root_ssh.send_command(RootCommands.get_configured_channel(wifi_idx))).result.strip()
        ssh_conf_ch = parse_configured_channel(ssh_conf_ch_raw)

        ssh_sec_raw = (await root_ssh.send_command(RootCommands.get_security(wifi_idx))).result.strip()
        ssh_sec = parse_security(ssh_sec_raw)

        ssh_parts = (await root_ssh.send_command(RootCommands.get_remote_partners(wifi_idx))).result.strip()
        ssh_rtx = (await root_ssh.send_command(RootCommands.get_rtx_percentage(wifi_idx))).result.strip()

        await gui_page.wait_for_timeout(5000)

        print(f"    -> [Radio {radio_num}] Scraping GUI data fields...")
        gui_status = await gui_page.locator(SummaryWirelessLocators.RADIO_STATUS.format(radio_num)).inner_text()
        gui_mac = await gui_page.locator(SummaryWirelessLocators.MAC_ADDRESS.format(radio_num)).inner_text()
        gui_link = await gui_page.locator(SummaryWirelessLocators.LINK_TYPE.format(radio_num)).inner_text()
        gui_mode = await gui_page.locator(SummaryWirelessLocators.RADIO_MODE.format(radio_num)).inner_text()
        gui_band = await gui_page.locator(SummaryWirelessLocators.BANDWIDTH.format(radio_num)).inner_text()
        gui_ssid = await gui_page.locator(SummaryWirelessLocators.SSID.format(radio_num)).inner_text()
        gui_conf_ch = await gui_page.locator(SummaryWirelessLocators.CONFIGURED_CHANNEL.format(radio_num)).inner_text()
        gui_act_ch = await gui_page.locator(SummaryWirelessLocators.ACTIVE_CHANNEL.format(radio_num)).inner_text()
        gui_sec = await gui_page.locator(SummaryWirelessLocators.SECURITY.format(radio_num)).inner_text()
        gui_rtx = await gui_page.locator(SummaryWirelessLocators.RTX_PERCENTAGE.format(radio_num)).inner_text()
        gui_parts = await gui_page.locator(SummaryWirelessLocators.REMOTE_PARTNERS.format(radio_num)).inner_text()

        print(f"    -> [Radio {radio_num}] Validating Backend vs Frontend...")

        # State flags for validation logic
        is_disabled = (ssh_status.lower() == "disable")
        is_unlinked_su = (ssh_mode == "SU" and ssh_parts == "0")

        # Status and MAC are always validated regardless of state
        validate_param(f"R{radio_num} RADIO STATUS", ssh_status, gui_status)
        validate_param(f"R{radio_num} MAC ADDRESS", ssh_mac, gui_mac.upper())

        if is_disabled:
            print(f"    -> R{radio_num} LINK TYPE: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} RADIO MODE: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} BANDWIDTH: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} SSID: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} CONFIGURED CHANNEL: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} ACTIVE CHANNEL: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} SECURITY: SKIPPED (Radio Disabled)")
            if radio_num != 0:
                print(f"    -> R{radio_num} RTX PERCENTAGE: SKIPPED (Radio Disabled)")
            print(f"    -> R{radio_num} REMOTE PARTNERS: SKIPPED (Radio Disabled)")
        else:
            validate_param(f"R{radio_num} LINK TYPE", ssh_link, gui_link)
            validate_param(f"R{radio_num} RADIO MODE", ssh_mode, gui_mode)

            # Conditionally skip Bandwidth
            if is_unlinked_su:
                print(f"    -> R{radio_num} BANDWIDTH: SKIPPED (Unlinked SU)")
            else:
                validate_param(f"R{radio_num} BANDWIDTH", ssh_band, gui_band)

            validate_param(f"R{radio_num} SSID", ssh_ssid, gui_ssid)
            validate_param(f"R{radio_num} CONFIGURED CHANNEL", ssh_conf_ch, gui_conf_ch)

            # Conditionally skip Active Channel
            if is_unlinked_su:
                print(f"    -> R{radio_num} ACTIVE CHANNEL: SKIPPED (Unlinked SU)")
            else:
                validate_param(f"R{radio_num} ACTIVE CHANNEL", ssh_act_ch, gui_act_ch)

            validate_param(f"R{radio_num} SECURITY", ssh_sec, gui_sec)

            # Conditionally skip RTX Percentage
            if radio_num == 0:
                print(f"    -> R{radio_num} RTX PERCENTAGE: SKIPPED (Not supported for R0)")
            elif is_unlinked_su:
                print(f"    -> R{radio_num} RTX PERCENTAGE: SKIPPED (Unlinked SU)")
            else:
                validate_percentage(f"R{radio_num} RTX PERCENTAGE", ssh_rtx, gui_rtx, tolerance=5.0)

            validate_param(f"R{radio_num} REMOTE PARTNERS", ssh_parts, gui_parts)

        radio_num += 1

    print("\n[+] GUI_04 Soft Validation Complete.")