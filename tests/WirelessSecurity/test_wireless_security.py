import pytest

from utils.wireless_security_flows import (
    assert_w_security_01_configure_wpa2,
    assert_w_security_03_validate_rejection_logs,
    assert_w_security_05_link_uptime_after_restore,
    assert_w_security_06_link_stability_monitor,
    assert_w_security_07_wpa2_psk_connection,
    assert_w_security_09_wpa2_association,
    assert_w_security_11_open_mode_attempt,
    assert_w_security_14_invalid_passphrase,
)

pytestmark = [pytest.mark.sanity, pytest.mark.WirelessSecurity]


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_01
@pytest.mark.WirelessSecurity
async def test_w_security_01_configure_wpa2_ipv6(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_w_security_01_configure_wpa2(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_03
@pytest.mark.WirelessSecurity
async def test_w_security_03_validate_rejection_logs_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_03_validate_rejection_logs(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_05
@pytest.mark.WirelessSecurity
async def test_w_security_05_link_uptime_after_restore_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_05_link_uptime_after_restore(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_06
@pytest.mark.WirelessSecurity
async def test_w_security_06_link_stability_monitor_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_06_link_stability_monitor(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_07
@pytest.mark.WirelessSecurity
async def test_w_security_07_wpa2_psk_connection_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_07_wpa2_psk_connection(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_09
@pytest.mark.WirelessSecurity
async def test_w_security_09_wpa2_association_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_09_wpa2_association(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )


@pytest.mark.order(11)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_11
@pytest.mark.WirelessSecurity
async def test_w_security_11_open_mode_attempt_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_11_open_mode_attempt(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.W_SECURITY_14
@pytest.mark.WirelessSecurity
async def test_w_security_14_invalid_passphrase_ipv6(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
    request,
):
    await assert_w_security_14_invalid_passphrase(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=request.config.getoption("--fallback-ip"),
    )
