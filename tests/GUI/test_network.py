import pytest

from utils.network_assertions import (
    assert_gui_50_network_ip_config,
    assert_gui_51_edit_ip_config,
    assert_gui_52_edit_netmask_config,
    assert_gui_53_edit_gateway_config,
    assert_gui_54_edit_fallback_ip,
    assert_gui_55_edit_fallback_mask,
    assert_gui_70_ethernet_speed_duplex,
    assert_gui_71_ethernet_mtu,
    assert_gui_72_dhcp_server_status,
    assert_gui_73_dhcp_lease_time,
    assert_gui_74_radio_24_ip_config,
    assert_gui_75_radio_24_mask_config,
    assert_gui_76_radio_24_dhcp_status,
    assert_gui_77_radio_24_pool_range,
    assert_gui_78_radio_24_lease_time,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Network]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_50
@pytest.mark.Network
async def test_gui_50_network_ip_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_50_network_ip_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_51
@pytest.mark.Network
async def test_gui_51_edit_ip_config(gui_page, bsu_ip, device_creds):
    await assert_gui_51_edit_ip_config(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_52
@pytest.mark.Network
async def test_gui_52_edit_netmask_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_52_edit_netmask_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_53
@pytest.mark.Network
async def test_gui_53_edit_gateway_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_53_edit_gateway_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_54
@pytest.mark.Network
async def test_gui_54_edit_fallback_ip(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_54_edit_fallback_ip(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_55
@pytest.mark.Network
async def test_gui_55_edit_fallback_mask(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_55_edit_fallback_mask(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_70
@pytest.mark.Network
async def test_gui_70_ethernet_speed_duplex(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_70_ethernet_speed_duplex(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_71
@pytest.mark.Network
async def test_gui_71_ethernet_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_71_ethernet_mtu(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_72
@pytest.mark.Network
async def test_gui_72_dhcp_server_status(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_72_dhcp_server_status(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_73
@pytest.mark.Network
async def test_gui_73_dhcp_lease_time(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_73_dhcp_lease_time(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_74
@pytest.mark.Network
async def test_gui_74_radio_24_ip_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_74_radio_24_ip_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_75
@pytest.mark.Network
async def test_gui_75_radio_24_mask_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_75_radio_24_mask_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_76
@pytest.mark.Network
async def test_gui_76_radio_24_dhcp_status(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_76_radio_24_dhcp_status(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_77
@pytest.mark.Network
async def test_gui_77_radio_24_pool_range(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_77_radio_24_pool_range(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_78
@pytest.mark.Network
async def test_gui_78_radio_24_lease_time(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_78_radio_24_lease_time(root_ssh, gui_page, bsu_ip, device_creds)
