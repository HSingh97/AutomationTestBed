import pytest

from utils.summary_flows import (
    assert_summary_network,
    assert_summary_performance,
    assert_summary_system,
    assert_summary_wireless,
)

pytestmark = pytest.mark.sanity


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_01
@pytest.mark.Summary
async def test_gui_01_summary_system(root_ssh, gui_page, bsu_ip):
    await assert_summary_system(root_ssh, gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_02
@pytest.mark.Summary
async def test_gui_02_summary_network(root_ssh, gui_page, bsu_ip):
    await assert_summary_network(root_ssh, gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_03
@pytest.mark.Summary
async def test_gui_03_summary_performance(root_ssh, gui_page, bsu_ip):
    await assert_summary_performance(root_ssh, gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_04
@pytest.mark.Summary
async def test_gui_04_summary_wireless(root_ssh, gui_page, bsu_ip):
    await assert_summary_wireless(root_ssh, gui_page)
