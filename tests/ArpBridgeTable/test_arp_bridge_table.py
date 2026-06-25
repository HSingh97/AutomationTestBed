import pytest

from utils.arp_bridge_table_flows import (
    assert_arpbridge_01_basic_arp_resolution,
    assert_arpbridge_02_duplicate_ip_handling,
    assert_arpbridge_07_gratuitous_arp_handling,
    assert_arpbridge_08_bridge_interface,
    assert_arpbridge_09_bridge_traffic,
    assert_arpbridge_10_mac_learning,
    assert_arpbridge_11_correct_port_forwarding,
    assert_arpbridge_12_bridge_table_entry_update,
    assert_arpbridge_13_refresh_clear,
    assert_arpbridge_14_bridge_table_aging,
)

pytestmark = [pytest.mark.sanity, pytest.mark.ArpBridgeTable]


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_01
@pytest.mark.ArpBridgeTable
async def test_arpbridge_01_basic_arp_resolution_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_01_basic_arp_resolution(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_02
@pytest.mark.ArpBridgeTable
async def test_arpbridge_02_duplicate_ip_handling_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_02_duplicate_ip_handling(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_07
@pytest.mark.ArpBridgeTable
async def test_arpbridge_07_gratuitous_arp_handling_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_07_gratuitous_arp_handling(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_08
@pytest.mark.ArpBridgeTable
async def test_arpbridge_08_bridge_interface_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_08_bridge_interface(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_09
@pytest.mark.ArpBridgeTable
async def test_arpbridge_09_bridge_traffic_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_09_bridge_traffic(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(10)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_10
@pytest.mark.ArpBridgeTable
async def test_arpbridge_10_mac_learning_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_10_mac_learning(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(11)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_11
@pytest.mark.ArpBridgeTable
async def test_arpbridge_11_correct_port_forwarding_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_11_correct_port_forwarding(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(12)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_12
@pytest.mark.ArpBridgeTable
async def test_arpbridge_12_bridge_table_entry_update_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_12_bridge_table_entry_update(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_13
@pytest.mark.ArpBridgeTable
async def test_arpbridge_13_refresh_clear_ipv4(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_13_refresh_clear(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_14
@pytest.mark.ArpBridgeTable
async def test_arpbridge_14_bridge_table_aging_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_arpbridge_14_bridge_table_aging(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )
