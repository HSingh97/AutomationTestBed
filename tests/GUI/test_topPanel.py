import pytest

from utils.top_panel_flows import (
    assert_home_and_apply_buttons,
    assert_logout,
    assert_reboot_button_visible,
    assert_top_panel_logo,
    assert_top_panel_parameters,
    assert_top_panel_radio_redirect,
)

pytestmark = pytest.mark.sanity


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_05
@pytest.mark.TopPanel
async def test_gui_05_top_panel_logo(gui_page, bsu_ip):
    await assert_top_panel_logo(gui_page, bsu_ip)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_06
@pytest.mark.TopPanel
async def test_gui_06_top_panel_parameters(root_ssh, gui_page):
    await assert_top_panel_parameters(root_ssh, gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_07
@pytest.mark.TopPanel
async def test_gui_07_top_panel_radio_redirect(gui_page):
    await assert_top_panel_radio_redirect(gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_08
@pytest.mark.TopPanel
async def test_gui_08_home_and_apply_buttons(gui_page, root_ssh):
    await assert_home_and_apply_buttons(gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_09
@pytest.mark.TopPanel
async def test_gui_09_reboot_device(gui_page):
    await assert_reboot_button_visible(gui_page)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_10
@pytest.mark.TopPanel
async def test_gui_10_logout(gui_page):
    await assert_logout(gui_page)
