import ipaddress
import re

from pages.commands import RootCommands
from pages.locators import (
    SummaryLocators,
    SummaryNetworkLocators,
    SummaryPerformanceLocators,
    SummaryWirelessLocators,
    TopPanelLocators,
)
from utils.parsers import (
    clean_ssh_output,
    extract_ip_objects,
    extract_uci_value,
    parse_bandwidth,
    parse_configured_channel,
    parse_ifconfig_mac,
    parse_iwconfig_active_channel,
    parse_link_type,
    parse_radio_mode,
    parse_radio_status,
    parse_security,
)
from utils.validators import (
    validate_cpu_mem,
    validate_network_address,
    validate_param,
    validate_percentage,
    validate_speed_duplex,
    validate_temperature,
    validate_throughput,
    validate_time,
)


async def _navigate_home(gui_page):
    await gui_page.locator(TopPanelLocators.LOGO).first.click()
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)


async def assert_summary_system(root_ssh, gui_page):
    await _navigate_home(gui_page)
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

    if not any(char.isalnum() for char in gui_gps):
        gui_gps = ""

    validate_param("MODEL", ssh_model, gui_model)
    validate_param("HW VERSION", ssh_hw, gui_hw)
    validate_param("BOOTLOADER", ssh_bootloader, gui_bootloader)
    validate_time("LOCAL TIME", ssh_time, gui_time)
    validate_temperature("TEMPERATURE", ssh_temp, gui_temp, tolerance=1.0)
    validate_param("GPS", ssh_gps, gui_gps)
    validate_param("ELEVATION", ssh_elevation, gui_elevation)
    validate_cpu_mem(ssh_cpu, ssh_mem, gui_cpu_mem, tolerance=5.0)


async def assert_summary_network(root_ssh, gui_page):
    await _navigate_home(gui_page)
    ssh_ipv4 = (await root_ssh.send_command(RootCommands.GET_IPv4)).result.strip()
    ssh_ipv6 = (await root_ssh.send_command(RootCommands.GET_IPv6)).result.strip()
    ssh_gw_v4 = clean_ssh_output((await root_ssh.send_command(RootCommands.GET_GATEWAYv4)).result)
    ssh_gw_v6 = clean_ssh_output((await root_ssh.send_command(RootCommands.GET_GATEWAYv6)).result)

    await gui_page.wait_for_timeout(5000)
    gui_ip = await gui_page.locator(SummaryNetworkLocators.IP_ADDRESS).inner_text()
    gui_gw = await gui_page.locator(SummaryNetworkLocators.GATEWAY).inner_text()
    if not any(char.isalnum() for char in gui_gw):
        gui_gw = ""
    def _extract_gateway_ip(text):
        raw = str(text or "")
        ipv4_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw)
        if ipv4_match:
            try:
                return str(ipaddress.ip_address(ipv4_match.group(0)))
            except ValueError:
                pass

        ipv6_match = re.search(r"\b[0-9a-fA-F:]*:[0-9a-fA-F:]+\b", raw)
        if ipv6_match:
            try:
                return str(ipaddress.ip_address(ipv6_match.group(0)))
            except ValueError:
                pass
        return ""

    ssh_gw_v4 = _extract_gateway_ip(ssh_gw_v4)
    ssh_gw_v6 = _extract_gateway_ip(ssh_gw_v6)
    ssh_ipv4_norm = _extract_gateway_ip(ssh_ipv4)
    ssh_ipv6_norm = _extract_gateway_ip(ssh_ipv6)

    # Some firmware returns interface IP in gateway fields when no gateway is configured.
    if ssh_gw_v4 and ssh_gw_v4 in {ssh_ipv4_norm, ssh_ipv6_norm}:
        ssh_gw_v4 = ""
    if ssh_gw_v6 and ssh_gw_v6 in {ssh_ipv4_norm, ssh_ipv6_norm}:
        ssh_gw_v6 = ""
    gui_gw_has_ip = bool(_extract_gateway_ip(gui_gw))
    ssh_gw_v4_has_ip = bool(ssh_gw_v4)
    ssh_gw_v6_has_ip = bool(ssh_gw_v6)

    if not ssh_gw_v4_has_ip and not ssh_gw_v6_has_ip and not gui_gw_has_ip:
        pass
    else:
        validate_network_address("GATEWAY", ssh_gw_v4, ssh_gw_v6, gui_gw)
    validate_network_address("IP ADDRESS", ssh_ipv4, ssh_ipv6, gui_ip)

    lan_num = 1
    while True:
        mac_locator = getattr(SummaryNetworkLocators, f"MAC_LAN{lan_num}", None)
        speed_locator = getattr(SummaryNetworkLocators, f"SPEED_DUPLEX_LAN{lan_num}", None)
        cable_locator = getattr(SummaryNetworkLocators, f"CABLE_LENGTH_LAN{lan_num}", None)
        if not mac_locator or not speed_locator or not cable_locator:
            break
        if await gui_page.locator(mac_locator).count() == 0:
            break

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


async def assert_summary_performance(root_ssh, gui_page):
    await _navigate_home(gui_page)
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
        if not tx_locator or not rx_locator:
            break
        if await gui_page.locator(tx_locator).count() == 0:
            break

        eth_idx = lan_num - 1
        ssh_tx_lan = (await root_ssh.send_command(RootCommands.get_tx_lan(eth_idx))).result.strip()
        ssh_rx_lan = (await root_ssh.send_command(RootCommands.get_rx_lan(eth_idx))).result.strip()
        gui_tx_lan = await gui_page.locator(tx_locator).inner_text()
        gui_rx_lan = await gui_page.locator(rx_locator).inner_text()
        validate_throughput(f"TX LAN{lan_num}", ssh_tx_lan, gui_tx_lan, tolerance=20.0)
        validate_throughput(f"RX LAN{lan_num}", ssh_rx_lan, gui_rx_lan, tolerance=20.0)
        lan_num += 1


async def assert_summary_wireless(root_ssh, gui_page):
    await _navigate_home(gui_page)
    await gui_page.wait_for_timeout(5000)

    radio_num = 0
    while radio_num <= 1:
        status_loc = SummaryWirelessLocators.RADIO_STATUS.format(radio_num)
        if await gui_page.locator(status_loc).count() == 0:
            break

        wifi_idx = radio_num
        ssh_status = parse_radio_status((await root_ssh.send_command(RootCommands.get_radio_status(wifi_idx))).result.strip())
        ssh_mac = parse_ifconfig_mac((await root_ssh.send_command(RootCommands.get_mac_wireless(wifi_idx))).result.strip())
        ssh_act_ch = parse_iwconfig_active_channel((await root_ssh.send_command(RootCommands.get_active_channel(wifi_idx))).result.strip())
        ssh_link = parse_link_type((await root_ssh.send_command(RootCommands.get_link_type(wifi_idx))).result.strip())
        ssh_mode = parse_radio_mode((await root_ssh.send_command(RootCommands.get_radio_mode(wifi_idx))).result.strip(), radio_num)
        ssh_band = parse_bandwidth((await root_ssh.send_command(RootCommands.get_bandwidth(wifi_idx))).result.strip())
        ssh_ssid = extract_uci_value((await root_ssh.send_command(RootCommands.get_ssid(wifi_idx))).result.strip())
        ssh_conf_ch = parse_configured_channel((await root_ssh.send_command(RootCommands.get_configured_channel(wifi_idx))).result.strip())
        ssh_sec = parse_security((await root_ssh.send_command(RootCommands.get_security(wifi_idx))).result.strip())
        ssh_parts = (await root_ssh.send_command(RootCommands.get_remote_partners(wifi_idx))).result.strip()
        ssh_rtx = (await root_ssh.send_command(RootCommands.get_rtx_percentage(wifi_idx))).result.strip()

        await gui_page.wait_for_timeout(5000)
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

        is_disabled = ssh_status.lower() == "disable"
        is_unlinked_su = ssh_mode == "SU" and ssh_parts == "0"

        validate_param(f"R{radio_num} RADIO STATUS", ssh_status, gui_status)
        validate_param(f"R{radio_num} MAC ADDRESS", ssh_mac, gui_mac.upper())

        if not is_disabled:
            validate_param(f"R{radio_num} LINK TYPE", ssh_link, gui_link)
            validate_param(f"R{radio_num} RADIO MODE", ssh_mode, gui_mode)
            if not is_unlinked_su:
                validate_param(f"R{radio_num} BANDWIDTH", ssh_band, gui_band)
            validate_param(f"R{radio_num} SSID", ssh_ssid, gui_ssid)
            if ssh_conf_ch.lower() == "auto":
                gui_conf_ch = ssh_conf_ch
            validate_param(f"R{radio_num} CONFIGURED CHANNEL", ssh_conf_ch, gui_conf_ch)
            if not is_unlinked_su:
                validate_param(f"R{radio_num} ACTIVE CHANNEL", ssh_act_ch, gui_act_ch)
            validate_param(f"R{radio_num} SECURITY", ssh_sec, gui_sec)
            if gui_rtx.strip() in ["-", "- -", ""]:
                gui_rtx = "0"
            if radio_num != 0 and not is_unlinked_su:
                validate_percentage(f"R{radio_num} RTX PERCENTAGE", ssh_rtx, gui_rtx, tolerance=5.0)
            validate_param(f"R{radio_num} REMOTE PARTNERS", ssh_parts, gui_parts)

        radio_num += 1
