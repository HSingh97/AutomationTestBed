import pytest

from utils.management_assertions import (
    assert_gui_88_timezone_random,
    assert_gui_89_ntp_full_cycle,
    assert_gui_90_sync_with_browser,
    assert_gui_91_logging_config,
    assert_gui_92_temp_logging_cycle,
    assert_gui_93_location_config,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Management]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_88
@pytest.mark.Management
async def test_gui_88_timezone_random(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_88_timezone_random(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_89
@pytest.mark.Management
async def test_gui_89_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_89_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_90
@pytest.mark.Management
async def test_gui_90_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_90_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_91
@pytest.mark.Management
async def test_gui_91_logging_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_91_logging_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_92
@pytest.mark.Management
async def test_gui_92_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_92_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_93
@pytest.mark.Management
async def test_gui_93_location_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_93_location_config(root_ssh, gui_page, bsu_ip, device_creds)
