import pytest

from utils.monitor_flows import (
    assert_gui_105_bridge_table,
    assert_gui_106_bridge_refresh_clear,
    assert_gui_107_arp_table,
    assert_gui_108_arp_refresh_clear,
    assert_gui_109_config_logs,
    assert_gui_110_device_logs,
    assert_gui_111_temperature_logs,
    assert_gui_112_system_logs,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Monitor]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_105
@pytest.mark.Monitor
async def test_gui_105_bridge_table(gui_page, bsu_ip, device_creds):
    await assert_gui_105_bridge_table(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_106
@pytest.mark.Monitor
async def test_gui_106_bridge_refresh_clear(gui_page, bsu_ip, device_creds):
    await assert_gui_106_bridge_refresh_clear(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_107
@pytest.mark.Monitor
async def test_gui_107_arp_table(gui_page, bsu_ip, device_creds):
    await assert_gui_107_arp_table(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_108
@pytest.mark.Monitor
async def test_gui_108_arp_refresh_clear(gui_page, bsu_ip, device_creds):
    await assert_gui_108_arp_refresh_clear(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_109
@pytest.mark.Monitor
async def test_gui_109_config_logs(gui_page, bsu_ip, device_creds):
    await assert_gui_109_config_logs(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_110
@pytest.mark.Monitor
async def test_gui_110_device_logs(gui_page, bsu_ip, device_creds):
    await assert_gui_110_device_logs(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_111
@pytest.mark.Monitor
async def test_gui_111_temperature_logs(gui_page, bsu_ip, device_creds):
    await assert_gui_111_temperature_logs(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_112
@pytest.mark.Monitor
async def test_gui_112_system_logs(gui_page, bsu_ip, device_creds):
    await assert_gui_112_system_logs(gui_page, bsu_ip, device_creds)
