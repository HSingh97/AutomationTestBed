import pytest

from pages.radio_properties_page import RadioPropertiesPage
from utils.radio_properties_flows import (
    assert_bandwidth_lifecycle,
    assert_channel_consistency,
    assert_encryption_lifecycle,
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
