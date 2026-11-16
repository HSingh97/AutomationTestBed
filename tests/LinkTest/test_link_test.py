
from __future__ import annotations

import pytest

from utils.link_at_sw_flows import (
    assert_link_test_03_sequential_ul_dl,
    assert_link_test_04_long_duration,
    assert_link_test_05_high_bandwidth_80mhz,
    assert_link_test_07_restart_bts_during_test,
    assert_link_test_08_client_reconnect_after_drop,
    assert_link_test_13_excessive_duration,
    assert_link_test_15_valid_speed_from_cpe,
    assert_link_test_17_cpe_reboot_during_test,
    assert_link_test_18_network_congestion,
    assert_link_test_19_packet_loss_simulation,
    assert_link_test_20_latency_simulation,
    assert_link_test_22_server_unreachable,
    assert_link_test_23_metrics_validation,
    assert_link_test_24_rapid_repeated_tests,
)

pytestmark = pytest.mark.LINK_TEST

@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_03
async def test_link_test_03_sequential_ul_dl(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_03_sequential_ul_dl(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_04
async def test_link_test_04_long_duration(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_04_long_duration(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_05
async def test_link_test_05_high_bandwidth_80mhz(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_05_high_bandwidth_80mhz(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_07
async def test_link_test_07_restart_bts_during_test(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_07_restart_bts_during_test(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_08
async def test_link_test_08_client_reconnect_after_drop(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_08_client_reconnect_after_drop(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_13
async def test_link_test_13_excessive_duration(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_13_excessive_duration(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_15
async def test_link_test_15_valid_speed_from_cpe(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_15_valid_speed_from_cpe(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_17
async def test_link_test_17_cpe_reboot_during_test(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_17_cpe_reboot_during_test(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_18
async def test_link_test_18_network_congestion(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_18_network_congestion(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_19
async def test_link_test_19_packet_loss_simulation(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_19_packet_loss_simulation(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_20
async def test_link_test_20_latency_simulation(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_20_latency_simulation(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(22)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_22
async def test_link_test_22_server_unreachable(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_22_server_unreachable(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(23)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_23
async def test_link_test_23_metrics_validation(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_23_metrics_validation(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )


@pytest.mark.order(24)
@pytest.mark.asyncio(scope="session")
@pytest.mark.LINK_TEST_24
async def test_link_test_24_rapid_repeated_tests(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    link_test_config,
    link_test_ready,
):
    await assert_link_test_24_rapid_repeated_tests(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        link_test_ready=link_test_ready,
    )
