import pytest

from utils.diagnostics_flows import (
    assert_gui_113_ping,
    assert_gui_114_traceroute,
    assert_gui_115_packet_capture,
    assert_gui_116_console,
    assert_gui_117_cable_length,
    assert_gui_118_lldp,
)
from utils.link_test_flows import (
    assert_gui_127_link_test_parameters,
    assert_gui_128_link_test_cpe,
    assert_gui_130_link_test_results,
)
from utils.radio_statistics_flows import (
    assert_gui_83_radio_link_statistics,
    assert_gui_84_cpe_ip_hyperlink,
    assert_gui_88_detailed_statistics_back,
    assert_gui_89_detailed_statistics_disconnect,
    assert_gui_90_detailed_statistics_clear,
    assert_gui_91_detailed_statistics_identity,
    assert_gui_92_detailed_statistics_performance,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Monitor]


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_83
@pytest.mark.Monitor
async def test_gui_83_radio_link_statistics(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    """Radio 1 Statistics Link tab — validate parameters vs backend."""
    await assert_gui_83_radio_link_statistics(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_84
@pytest.mark.Monitor
async def test_gui_84_cpe_ip_hyperlink(gui_page, cpe_ips, device_creds, bsu_ip):
    """Radio 1 Statistics Link tab — CPE IP opens CPE web GUI."""
    await assert_gui_84_cpe_ip_hyperlink(gui_page, cpe_ips, device_creds, bsu_ip=bsu_ip)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_88
@pytest.mark.Monitor
async def test_gui_88_detailed_statistics_back(gui_page, cpe_ips, bsu_ip, device_creds):
    await assert_gui_88_detailed_statistics_back(
        gui_page,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_89
@pytest.mark.Monitor
async def test_gui_89_detailed_statistics_disconnect(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_gui_89_detailed_statistics_disconnect(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_90
@pytest.mark.Monitor
async def test_gui_90_detailed_statistics_clear(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_gui_90_detailed_statistics_clear(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_91
@pytest.mark.Monitor
async def test_gui_91_detailed_statistics_identity(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_gui_91_detailed_statistics_identity(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_92
@pytest.mark.Monitor
async def test_gui_92_detailed_statistics_performance(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_gui_92_detailed_statistics_performance(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_113
@pytest.mark.Monitor
async def test_gui_113_ping(gui_page, cpe_ips, bsu_ip, device_creds):
    await assert_gui_113_ping(
        gui_page,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_114
@pytest.mark.Monitor
async def test_gui_114_traceroute(gui_page, cpe_ips, bsu_ip, device_creds):
    await assert_gui_114_traceroute(
        gui_page,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_115
@pytest.mark.Monitor
async def test_gui_115_packet_capture(gui_page, root_ssh, cpe_ips, device_creds):
    await assert_gui_115_packet_capture(
        gui_page,
        root_ssh,
        cpe_ips,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_116
@pytest.mark.Monitor
async def test_gui_116_console(gui_page, cpe_ips, device_creds):
    await assert_gui_116_console(gui_page, cpe_ips, device_creds=device_creds)


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_117
@pytest.mark.Monitor
async def test_gui_117_cable_length(gui_page, root_ssh, cpe_ips, device_creds):
    await assert_gui_117_cable_length(
        gui_page,
        root_ssh,
        cpe_ips,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_118
@pytest.mark.Monitor
async def test_gui_118_lldp(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds):
    await assert_gui_118_lldp(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_127
@pytest.mark.Monitor
async def test_gui_127_link_test_parameters(gui_page, root_ssh, cpe_ips, link_test_config, bsu_ip, device_creds):
    await assert_gui_127_link_test_parameters(
        gui_page,
        root_ssh,
        link_test_config,
        cpe_ips=cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_128
@pytest.mark.Monitor
async def test_gui_128_link_test_cpe(gui_page, root_ssh, cpe_ips, link_test_config, bsu_ip, device_creds):
    await assert_gui_128_link_test_cpe(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_130
@pytest.mark.Monitor
async def test_gui_130_link_test_results(gui_page, root_ssh, cpe_ips, link_test_config, bsu_ip, device_creds):
    await assert_gui_130_link_test_results(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )
