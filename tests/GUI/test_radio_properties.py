import pytest

from pages.radio_properties_page import RadioPropertiesPage
from utils.radio_properties_flows import (
    assert_bandwidth_lifecycle,
    assert_channel_consistency,
    assert_encryption_lifecycle,
    assert_gui_23_dl_ul_ratio,
    assert_gui_24_ddrs_status,
    assert_gui_25_spatial_stream,
    assert_gui_26_modulation_index,
    assert_gui_27_atpc_status,
    assert_gui_28_transmit_power,
    assert_gui_29_maximum_eirp,
    assert_max_cpe_lifecycle,
    assert_radio_status_lifecycle,
    assert_ssid_lifecycle,
)

pytestmark = pytest.mark.sanity


@pytest.fixture(scope="session")
def radio_page(gui_page, bsu_ip):
    return RadioPropertiesPage(gui_page, local_ip=bsu_ip)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_17
@pytest.mark.WirelessProperties
async def test_gui_17_radio_status(radio_page, root_ssh):
    await assert_radio_status_lifecycle(radio_page, root_ssh)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_18
@pytest.mark.WirelessProperties
async def test_gui_18_ssid(radio_page, root_ssh):
    await assert_ssid_lifecycle(radio_page, root_ssh)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_19
@pytest.mark.WirelessProperties
async def test_gui_19_bandwidth(radio_page, root_ssh):
    await assert_bandwidth_lifecycle(radio_page, root_ssh)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_20
@pytest.mark.WirelessProperties
async def test_gui_20_channel(radio_page, root_ssh):
    await assert_channel_consistency(radio_page, root_ssh)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_21
@pytest.mark.WirelessProperties
async def test_gui_21_encryption(radio_page, root_ssh):
    await assert_encryption_lifecycle(radio_page, root_ssh)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_22
@pytest.mark.WirelessProperties
async def test_gui_22_max_cpe(radio_page, root_ssh):
    await assert_max_cpe_lifecycle(radio_page, root_ssh)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_23
@pytest.mark.WirelessProperties
async def test_gui_23_dl_ul_ratio(gui_page, bsu_ip, device_creds):
    await assert_gui_23_dl_ul_ratio(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_24
@pytest.mark.WirelessProperties
async def test_gui_24_ddrs_status(gui_page, bsu_ip, device_creds):
    await assert_gui_24_ddrs_status(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_25
@pytest.mark.WirelessProperties
async def test_gui_25_spatial_stream(gui_page, bsu_ip, device_creds):
    await assert_gui_25_spatial_stream(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_26
@pytest.mark.WirelessProperties
async def test_gui_26_modulation_index(gui_page, bsu_ip, device_creds):
    await assert_gui_26_modulation_index(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_27
@pytest.mark.WirelessProperties
async def test_gui_27_atpc_status(gui_page, bsu_ip, device_creds):
    await assert_gui_27_atpc_status(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_28
@pytest.mark.WirelessProperties
async def test_gui_28_transmit_power(gui_page, bsu_ip, device_creds):
    await assert_gui_28_transmit_power(gui_page, bsu_ip, device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_29
@pytest.mark.WirelessProperties
async def test_gui_29_maximum_eirp(gui_page, bsu_ip, device_creds):
    await assert_gui_29_maximum_eirp(gui_page, bsu_ip, device_creds)
