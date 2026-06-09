import pytest

from utils.arp_bridge_table_flows import (
    assert_arpbridge_01_basic_arp_resolution,
    assert_arpbridge_02_duplicate_ip_handling,
    assert_arpbridge_06_dynamic_ip_allocation,
    assert_arpbridge_07_gratuitous_arp_handling,
    assert_arpbridge_08_bridge_interface,
    assert_arpbridge_09_bridge_traffic,
    assert_arpbridge_10_mac_learning,
    assert_arpbridge_11_correct_port_forwarding,
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


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_06
@pytest.mark.ArpBridgeTable
async def test_arpbridge_06_dynamic_ip_allocation_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds, gui_browser):
    """
    ARPBRIDGE_06: CPE SSH only → Dynamic IPv4; BTS SSH read-only for ARP verify; restore CPE static.
    """
    await assert_arpbridge_06_dynamic_ip_allocation(
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        gui_browser=gui_browser,
        root_ssh=root_ssh,
    )


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.ARPBRIDGE_07
@pytest.mark.ArpBridgeTable
async def test_arpbridge_07_gratuitous_arp_handling_ipv4(root_ssh, cpe_ips, bsu_ip, device_creds):
    """
    ARPBRIDGE_07: gratuitous ARP from CPE → BTS ARP table, then BTS → CPE ARP table.
    """
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
    """
    ARPBRIDGE_08: br-lan bridge exists; eth0 + radio ports bridged; bridge table populated.
    """
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
    """
    ARPBRIDGE_09: ping linked peer through br-lan; destination receives packets (BTS & CPE).
    """
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
    """
    ARPBRIDGE_10: flush peer MAC from bridge FDB, ping, verify MAC re-learned on port.
    """
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
    """ARPBRIDGE_11: peer MAC on exactly one bridge port (no flooding)."""
    await assert_arpbridge_11_correct_port_forwarding(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )
