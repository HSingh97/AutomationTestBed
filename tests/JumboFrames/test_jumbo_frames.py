import pytest

from utils.jumbo_frames_assertions import (
    assert_jmb_01_configure_and_disable,
    assert_jmb_02_configure_9000,
    assert_jmb_03_min_mid_mtu,
    assert_jmb_04_max_mtu_9000,
    assert_jmb_05_mgmt_vlan_mtu,
    assert_jmb_06_jumbo_with_p2mp,
    assert_jmb_07_reboot_persistence,
    assert_jmb_08_mtu_1500,
    assert_jmb_09_boundary_values,
    assert_jmb_10_factory_reset_default,
)

pytestmark = [pytest.mark.sanity, pytest.mark.JumboFrames, pytest.mark.Network]


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_01
@pytest.mark.Jumbo
async def test_jmb_01_configure_and_disable(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_01_configure_and_disable(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_02
@pytest.mark.Jumbo
async def test_jmb_02_configure_9000(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_02_configure_9000(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_03
@pytest.mark.Jumbo
async def test_jmb_03_min_mid_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_03_min_mid_mtu(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_04
@pytest.mark.Jumbo
async def test_jmb_04_max_mtu_9000(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_04_max_mtu_9000(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_05
@pytest.mark.Jumbo
async def test_jmb_05_mgmt_vlan_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_05_mgmt_vlan_mtu(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_06
@pytest.mark.Jumbo
async def test_jmb_06_jumbo_with_p2mp(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_06_jumbo_with_p2mp(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_07
@pytest.mark.Jumbo
async def test_jmb_07_reboot_persistence(root_ssh, gui_page, bsu_ip, device_creds, request):
    await assert_jmb_07_reboot_persistence(
        root_ssh, gui_page, bsu_ip, device_creds, request.config.getoption("--allow-destructive-jumbo")
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_08
@pytest.mark.Jumbo
async def test_jmb_08_mtu_1500(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_08_mtu_1500(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_09
@pytest.mark.Jumbo
async def test_jmb_09_boundary_values(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_jmb_09_boundary_values(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.JMB_10
@pytest.mark.Jumbo
async def test_jmb_10_factory_reset_default(root_ssh, gui_page, bsu_ip, device_creds, request):
    await assert_jmb_10_factory_reset_default(
        root_ssh, gui_page, bsu_ip, device_creds, request.config.getoption("--allow-destructive-jumbo")
    )

