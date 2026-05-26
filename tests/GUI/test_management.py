import pytest

from utils.management_assertions import (
    assert_gui_63_timezone_random,
    assert_gui_64_ntp_full_cycle,
    assert_gui_65_sync_with_browser,
    assert_gui_66_logging_config,
    assert_gui_67_temp_logging_cycle,
    assert_gui_68_location_config,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Management]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_63
@pytest.mark.Management
async def test_gui_63_timezone_random(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_63_timezone_random(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_64
@pytest.mark.Management
async def test_gui_64_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_64_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_65
@pytest.mark.Management
async def test_gui_65_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_65_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_66
@pytest.mark.Management
async def test_gui_66_logging_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_66_logging_config(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_67
@pytest.mark.Management
async def test_gui_67_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_67_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_68
@pytest.mark.Management
async def test_gui_68_location_config(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_68_location_config(root_ssh, gui_page, bsu_ip, device_creds)
