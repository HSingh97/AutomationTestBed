import pytest

from utils.arp_bridge_table_flows import (
    assert_arpbridge_01_basic_arp_resolution,
    assert_arpbridge_02_duplicate_ip_handling,
    assert_arpbridge_06_dynamic_ip_allocation,
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
async def test_arpbridge_06_dynamic_ip_allocation_ipv4(
    root_ssh, cpe_ips, bsu_ip, device_creds, gui_browser
):
    """
    ARPBRIDGE_06: CPE Address type = Dynamic IPv4 (network.lan.proto=dhcp) → verify ARP → restore Static IPv4.

    GUI: Network > IP Configuration → Dynamic IPv4 → Save → Apply (configuration changes).
    Restores original static IP/gateway in finally for later cases.
    """
    await assert_arpbridge_06_dynamic_ip_allocation(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        gui_browser=gui_browser,
    )
