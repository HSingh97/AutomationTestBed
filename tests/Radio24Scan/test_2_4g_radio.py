import pytest

from utils.radio24_scan_flows import (
    assert_radio_24_01_hidden_ssid_not_visible,
    assert_radio_24_02_connect_hidden_ssid,
    assert_radio_24_03_connect_hidden_ssid_wrong_password,
    assert_radio_24_04_reboot_cpe_scan_hidden_ssid,
    assert_radio_24_08_fw_upgrade_scan_hidden_ssid,
    assert_radio_24_17_dhcp_server_down_no_ip,
    assert_radio_24_18_dhcp_recovery_after_reboot,
    assert_radio_24_19_dhcp_stress_connect_disconnect,
    assert_radio_24_20_dhcp_logs_lease_assignment,
    assert_radio_24_21_no_ssid_during_boot,
    assert_radio_24_22_ssid_appears_after_boot,
    assert_radio_24_41_valid_api_call,
    assert_radio_24_42_invalid_api_call,
    assert_radio_24_43_unauthorized_access,
    assert_radio_24_44_malformed_request,
    assert_radio_24_45_api_under_load,
    assert_radio_24_46_api_after_reboot,
    assert_radio_24_49_api_response_time,
    assert_radio_24_50_api_logging,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Radio24Scan]


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_01
@pytest.mark.Radio24Scan
async def test_radio_24_01_hidden_ssid_not_visible(profile_bundle):
    """2.4G_RADIO_01 — scan near CPE; hidden mgmt SSID must not appear in passive scan."""
    await assert_radio_24_01_hidden_ssid_not_visible(profile_bundle)


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_02
@pytest.mark.Radio24Scan
async def test_radio_24_02_connect_hidden_ssid(profile_bundle):
    """2.4G_RADIO_02 — connect hidden SSID with correct credentials; link up, no internet."""
    await assert_radio_24_02_connect_hidden_ssid(profile_bundle)


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_03
@pytest.mark.Radio24Scan
async def test_radio_24_03_connect_hidden_ssid_wrong_password(profile_bundle):
    """2.4G_RADIO_03 — SSID hidden; wrong password must not connect."""
    await assert_radio_24_03_connect_hidden_ssid_wrong_password(profile_bundle)


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_04
@pytest.mark.Radio24Scan
async def test_radio_24_04_reboot_cpe_scan_hidden_ssid(profile_bundle):
    """2.4G_RADIO_04 — reboot CPE; after link up, hidden SSID must not appear in scan."""
    await assert_radio_24_04_reboot_cpe_scan_hidden_ssid(profile_bundle)


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_08
@pytest.mark.Radio24Scan
async def test_radio_24_08_fw_upgrade_scan_hidden_ssid(profile_bundle):
    """2.4G_RADIO_08 — FW upgrade preserve config; after link up, SSID must stay hidden."""
    await assert_radio_24_08_fw_upgrade_scan_hidden_ssid(profile_bundle)


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_17
@pytest.mark.Radio24Scan
async def test_radio_24_17_dhcp_server_down_no_ip(profile_bundle):
    """2.4G_RADIO_17 — disable CPE 2.4 GHz DHCP; scan PC must not connect on mgmt Wi‑Fi."""
    await assert_radio_24_17_dhcp_server_down_no_ip(profile_bundle)


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_18
@pytest.mark.Radio24Scan
async def test_radio_24_18_dhcp_recovery_after_reboot(profile_bundle):
    """2.4G_RADIO_18 — reboot CPE with DHCP on; after link up get 169.254.254.100."""
    await assert_radio_24_18_dhcp_recovery_after_reboot(profile_bundle)


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_19
@pytest.mark.Radio24Scan
async def test_radio_24_19_dhcp_stress_connect_disconnect(profile_bundle):
    """2.4G_RADIO_19 — toggle Wi‑Fi radio; LAN ports stay up; auto-reconnect with DHCP IP."""
    await assert_radio_24_19_dhcp_stress_connect_disconnect(profile_bundle)


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_20
@pytest.mark.Radio24Scan
async def test_radio_24_20_dhcp_logs_lease_assignment(profile_bundle):
    """2.4G_RADIO_20 — CPE Leases tab must match this scan PC's MAC, IP, and hostname."""
    await assert_radio_24_20_dhcp_logs_lease_assignment(profile_bundle)


@pytest.mark.order(21)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_21
@pytest.mark.Radio24Scan
async def test_radio_24_21_no_ssid_during_boot(profile_bundle):
    """2.4G_RADIO_21 — reboot CPE; hidden SSID connect must fail while CPE is booting."""
    await assert_radio_24_21_no_ssid_during_boot(profile_bundle)


@pytest.mark.order(22)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_22
@pytest.mark.Radio24Scan
async def test_radio_24_22_ssid_appears_after_boot(profile_bundle):
    """2.4G_RADIO_22 — after reboot when CPE is up, SSID must be available and connect."""
    await assert_radio_24_22_ssid_appears_after_boot(profile_bundle)


@pytest.mark.order(41)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_41
@pytest.mark.Radio24Scan
async def test_radio_24_41_valid_api_call(cpe_api_24_client):
    """2.4G_RADIO_41 — valid GET sw-version on CPE 2.4 GHz mgmt → HTTP 200."""
    await assert_radio_24_41_valid_api_call(cpe_api_24_client)


@pytest.mark.order(42)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_42
@pytest.mark.Radio24Scan
async def test_radio_24_42_invalid_api_call(cpe_api_24_client):
    """2.4G_RADIO_42 — invalid API path → error; no side effect on valid API."""
    await assert_radio_24_42_invalid_api_call(cpe_api_24_client)


@pytest.mark.order(43)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_43
@pytest.mark.Radio24Scan
async def test_radio_24_43_unauthorized_access(cpe_api_24_client):
    """2.4G_RADIO_43 — GET sw-version read access on mgmt Wi‑Fi (no btsconnect)."""
    await assert_radio_24_43_unauthorized_access(cpe_api_24_client)


@pytest.mark.order(44)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_44
@pytest.mark.Radio24Scan
async def test_radio_24_44_malformed_request(cpe_api_24_client):
    """2.4G_RADIO_44 — invalid sw-version URL → 404; GET sw-version still OK."""
    await assert_radio_24_44_malformed_request(cpe_api_24_client)


@pytest.mark.order(45)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_45
@pytest.mark.Radio24Scan
async def test_radio_24_45_api_under_load(cpe_api_24_client):
    """2.4G_RADIO_45 — 100 API requests in 1 min; CPE stays responsive."""
    await assert_radio_24_45_api_under_load(cpe_api_24_client)


@pytest.mark.order(46)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_46
@pytest.mark.Radio24Scan
async def test_radio_24_46_api_after_reboot(cpe_api_24_client, bsu_ip, device_creds):
    """2.4G_RADIO_46 — reboot; backend SSH + GET sw-version; wait BTS↔CPE link."""
    await assert_radio_24_46_api_after_reboot(
        cpe_api_24_client,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(49)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_49
@pytest.mark.Radio24Scan
async def test_radio_24_49_api_response_time(cpe_api_24_client):
    """2.4G_RADIO_49 — GET sw-version response < 500 ms; backend /etc/version match."""
    await assert_radio_24_49_api_response_time(cpe_api_24_client)


@pytest.mark.order(50)
@pytest.mark.asyncio(scope="session")
@pytest.mark.RADIO_24_50
@pytest.mark.Radio24Scan
async def test_radio_24_50_api_logging(cpe_api_24_client):
    """2.4G_RADIO_50 — GET sw-version; request/response logged in CPE device config logs."""
    await assert_radio_24_50_api_logging(cpe_api_24_client)

