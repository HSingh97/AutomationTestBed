import pytest

from utils.radio24_flows import (
    assert_gui_34_radio_24_status,
    assert_gui_35_radio_24_ssid,
    assert_gui_36_radio_24_bandwidth,
    assert_gui_37_radio_24_channel_auto,
    assert_gui_38_radio_24_encryption,
    assert_gui_39_radio_24_key,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Wireless24]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_34
@pytest.mark.Wireless24
async def test_gui_34_radio_24_status(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_34_radio_24_status(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_35
@pytest.mark.Wireless24
async def test_gui_35_radio_24_ssid(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_35_radio_24_ssid(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_36
@pytest.mark.Wireless24
async def test_gui_36_radio_24_bandwidth(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_36_radio_24_bandwidth(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_37
@pytest.mark.Wireless24
async def test_gui_37_radio_24_channel_auto(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_37_radio_24_channel_auto(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_38
@pytest.mark.Wireless24
async def test_gui_38_radio_24_encryption(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_38_radio_24_encryption(root_ssh, gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_39
@pytest.mark.Wireless24
async def test_gui_39_radio_24_key(root_ssh, gui_page, bsu_ip, device_creds):
    await assert_gui_39_radio_24_key(root_ssh, gui_page, bsu_ip, device_creds)
