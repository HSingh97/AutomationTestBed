"""GUI network page assertions (GUI_50–GUI_78)."""

from __future__ import annotations

import re

from pages.commands import RootCommands
from pages.locators import DHCPLocators, EthernetLocators, NetworkLocators, UITimeouts
from utils.network_flows import (
    navigate_to_ethernet,
    navigate_to_radio_24_dhcp,
    open_network_submenu,
)
from utils.parsers import extract_uci_value, generate_test_ip, ssh_scalar
from utils.ui_helpers import attach_dialog_handler, validate_dropdown_lifecycle, validate_input_lifecycle
from utils.validators import validate_network_address, validate_param


def _admin_fallback(gui_page, fragment: str) -> str:
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    base = match.group(1) if match else ""
    return f"{base}/admin{fragment}"


async def _open_ip_config(gui_page):
    await open_network_submenu(gui_page, "/network/ip")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _ssh_value(root_ssh, command: str) -> str:
    return extract_uci_value((await root_ssh.send_command(command)).result)


async def _open_root_ssh(bsu_ip: str, device_creds: dict):
    from scrapli.driver.generic import AsyncGenericDriver

    conn = AsyncGenericDriver(
        host=bsu_ip,
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _assert_ip_field_lifecycle(
    root_ssh,
    gui_page,
    locator,
    uci_cmd,
    param_name,
    *,
    version="v4",
    navigate_ip: bool = True,
):
    if navigate_ip:
        await _open_ip_config(gui_page)
    current = ssh_scalar((await root_ssh.send_command(uci_cmd)).result)
    if not current or "not found" in current.lower():
        current = "192.168.2.1" if version == "v4" else "fd00::1/64"
    test_val = generate_test_ip(current, version=version)
    invalid = "999.999.999.999" if version == "v4" else "gggg::/64"
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        locator,
        test_val,
        invalid,
        uci_cmd,
        param_name,
        _admin_fallback(gui_page, "/network/ip"),
        parser=extract_uci_value,
    )


async def assert_gui_50_network_ip_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _open_ip_config(gui_page)
    attach_dialog_handler(gui_page)

    checks = [
        ("IPv4 Address", NetworkLocators.IPv4_ADDRESS, RootCommands.GET_NET_IP, None),
        ("IPv4 Netmask", NetworkLocators.IPv4_NETMASK, RootCommands.GET_NET_MASK, None),
        ("IPv4 Gateway", NetworkLocators.IPv4_GATEWAY, RootCommands.GET_NET_GW, None),
        ("IPv6 Address", NetworkLocators.IPv6_ADDRESS, None, RootCommands.GET_NET_IP6),
        ("IPv6 Gateway", NetworkLocators.IPv6_GATEWAY, None, RootCommands.GET_NET_GW6),
        ("Fallback IP", NetworkLocators.FALLBACK_IP, RootCommands.GET_FALLBACK_IP, None),
        ("Fallback Mask", NetworkLocators.FALLBACK_NETMASK, RootCommands.GET_FALLBACK_MASK, None),
    ]

    for label, locator, ssh_v4_cmd, ssh_v6_cmd in checks:
        element = gui_page.locator(locator).first
        if await element.count() == 0:
            continue
        gui_val = await element.input_value()
        if ssh_v6_cmd:
            ssh_v4 = await _ssh_value(root_ssh, ssh_v4_cmd) if ssh_v4_cmd else ""
            ssh_v6 = await _ssh_value(root_ssh, ssh_v6_cmd)
            validate_network_address(label, ssh_v4, ssh_v6, gui_val)
        else:
            ssh_val = await _ssh_value(root_ssh, ssh_v4_cmd)
            validate_param(label, ssh_val, gui_val)


async def assert_gui_51_edit_ip_config(gui_page, bsu_ip, device_creds):
    ssh = await _open_root_ssh(bsu_ip, device_creds)
    try:
        await _assert_ip_field_lifecycle(
            ssh, gui_page, NetworkLocators.IPv4_ADDRESS, RootCommands.GET_NET_IP, "IPv4 Address"
        )
    finally:
        await ssh.close()


async def assert_gui_52_edit_netmask_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _assert_ip_field_lifecycle(
        root_ssh, gui_page, NetworkLocators.IPv4_NETMASK, RootCommands.GET_NET_MASK, "IPv4 Netmask"
    )


async def assert_gui_53_edit_gateway_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _assert_ip_field_lifecycle(
        root_ssh, gui_page, NetworkLocators.IPv4_GATEWAY, RootCommands.GET_NET_GW, "IPv4 Gateway"
    )


async def assert_gui_54_edit_fallback_ip(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _assert_ip_field_lifecycle(
        root_ssh, gui_page, NetworkLocators.FALLBACK_IP, RootCommands.GET_FALLBACK_IP, "Fallback IP"
    )


async def assert_gui_55_edit_fallback_mask(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _assert_ip_field_lifecycle(
        root_ssh,
        gui_page,
        NetworkLocators.FALLBACK_NETMASK,
        RootCommands.GET_FALLBACK_MASK,
        "Fallback Netmask",
    )


async def _assert_ethernet_field(root_ssh, gui_page, field_locator, uci_template: str, param_name: str):
    await navigate_to_ethernet(gui_page)
    attach_dialog_handler(gui_page)
    uci_cmd = uci_template.format(idx=0)
    current = ssh_scalar((await root_ssh.send_command(uci_cmd)).result) or "1500"
    test_val = "9000" if "mtu" in param_name.lower() else current
    invalid = "99999" if "mtu" in param_name.lower() else current
    if "mtu" in param_name.lower():
        test_val = "9000"
        invalid = "99999"
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        field_locator,
        test_val,
        invalid,
        uci_cmd,
        param_name,
        _admin_fallback(gui_page, "/network/eth"),
        parser=extract_uci_value,
        skip_restore=True,
    )


async def assert_gui_70_ethernet_speed_duplex(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await navigate_to_ethernet(gui_page)
    dropdown = gui_page.locator(EthernetLocators.SPEED_DROPDOWN).first
    await dropdown.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    options = await dropdown.locator("option").evaluate_all(
        "els => els.map(o => o.text.trim()).filter(Boolean)"
    )
    if not options:
        return
    uci_cmd = "uci get network.@device[0].speed"
    await validate_dropdown_lifecycle(
        gui_page,
        root_ssh,
        EthernetLocators.SPEED_DROPDOWN,
        options,
        uci_cmd,
        "Ethernet Speed",
        _admin_fallback(gui_page, "/network/eth"),
        parser=extract_uci_value,
        test_all_options=False,
    )


async def assert_gui_71_ethernet_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _assert_ethernet_field(root_ssh, gui_page, EthernetLocators.MTU_INPUT, "cat /sys/class/net/eth{idx}/mtu", "Ethernet MTU")


async def _assert_dhcp_dropdown(root_ssh, gui_page, locator, uci_cmd, param_name, fragment: str):
    await open_network_submenu(gui_page, fragment)
    dropdown = gui_page.locator(locator).first
    await dropdown.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    options = await dropdown.locator("option").evaluate_all(
        "els => els.map(o => o.text.trim()).filter(Boolean)"
    )
    if not options:
        return
    await validate_dropdown_lifecycle(
        gui_page,
        root_ssh,
        locator,
        options,
        uci_cmd,
        param_name,
        _admin_fallback(gui_page, fragment),
        parser=extract_uci_value,
        test_all_options=False,
    )


async def assert_gui_72_dhcp_server_status(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await _assert_dhcp_dropdown(
        root_ssh, gui_page, DHCPLocators.DHCP_SERVER_DROPDOWN, RootCommands.GET_DHCP_IGNORE, "DHCP Server", "/network/dhcp"
    )


async def assert_gui_73_dhcp_lease_time(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await open_network_submenu(gui_page, "/network/dhcp")
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        DHCPLocators.LEASE_TIME_INPUT,
        "2h",
        "999999h",
        RootCommands.GET_DHCP_LEASE,
        "DHCP Lease Time",
        _admin_fallback(gui_page, "/network/dhcp"),
        parser=extract_uci_value,
    )


async def assert_gui_74_radio_24_ip_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await navigate_to_radio_24_dhcp(gui_page)
    current = ssh_scalar((await root_ssh.send_command(RootCommands.GET_LAN24_IP)).result) or "192.168.10.1"
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        DHCPLocators.RADIO_24_IP,
        generate_test_ip(current),
        "999.999.999.999",
        RootCommands.GET_LAN24_IP,
        "Radio 2.4 IP",
        _admin_fallback(gui_page, "/network/dhcp"),
        parser=extract_uci_value,
    )


async def assert_gui_75_radio_24_mask_config(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await navigate_to_radio_24_dhcp(gui_page)
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        DHCPLocators.RADIO_24_MASK,
        "255.255.255.0",
        "255.255.255.999",
        RootCommands.GET_LAN24_MASK,
        "Radio 2.4 Mask",
        _admin_fallback(gui_page, "/network/dhcp"),
        parser=extract_uci_value,
    )


async def assert_gui_76_radio_24_dhcp_status(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await navigate_to_radio_24_dhcp(gui_page)
    await _assert_dhcp_dropdown(
        root_ssh,
        gui_page,
        DHCPLocators.RADIO_24_DHCP_DROPDOWN,
        RootCommands.GET_LAN24_DHCP_IGNORE,
        "Radio 2.4 DHCP",
        "/network/dhcp",
    )


async def assert_gui_77_radio_24_pool_range(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await navigate_to_radio_24_dhcp(gui_page)
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        DHCPLocators.RADIO_24_START_IP,
        "192.168.10.50",
        "999.999.999.999",
        RootCommands.GET_LAN24_START,
        "Radio 2.4 Pool Start",
        _admin_fallback(gui_page, "/network/dhcp"),
        parser=extract_uci_value,
    )


async def assert_gui_78_radio_24_lease_time(root_ssh, gui_page, bsu_ip, device_creds):
    del bsu_ip, device_creds
    await navigate_to_radio_24_dhcp(gui_page)
    await validate_input_lifecycle(
        gui_page,
        root_ssh,
        DHCPLocators.RADIO_24_LEASE_TIME_INPUT,
        "1h",
        "999999h",
        RootCommands.GET_LAN24_LEASE,
        "Radio 2.4 Lease",
        _admin_fallback(gui_page, "/network/dhcp"),
        parser=extract_uci_value,
    )
