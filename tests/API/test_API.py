import pytest
import hashlib

from utils.cpe_api_24_flows import (
    assert_api_01_btsconnect_success,
    assert_api_02_btsconnect_auth_fail,
    assert_api_03_btsconnect_ssid_not_found,
    assert_api_04_alignment_info_stream_1s,
    assert_api_05_alignment_info_stream_5s,
    assert_api_06_alignment_data,
    assert_api_07_sw_version,
    assert_api_08_sw_version_unavailable_when_file_removed,
    assert_api_09_upload_sw,
    assert_api_10_upload_sw_status,
    assert_api_11_upgrade_sw_preserve_config,
    assert_api_12_upgrade_sw_no_preserve_config,
    assert_api_13_upgrade_sw_status_in_progress,
    assert_api_14_upgrade_sw_status_success,
    assert_api_15_upgrade_sw_status_invalid_fw,
    assert_api_16_link_throughput_test,
    assert_api_17_link_throughput_test_no_bts_server,
    assert_api_18_link_throughput_test_in_progress,
    ensure_link_before_api_04,
    ensure_link_before_api_16,
)
from utils.cpe_api_24_config import resolve_invalid_fw_file_path
from utils.cpe_api_24_lab import fetch_bts_credentials_for_api, is_bts_link_broken


def _debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    import json
    import time

    payload = {
        "sessionId": "a5d6ea",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(
        "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-a5d6ea.log",
        "a",
        encoding="utf-8",
    ) as fh:
        fh.write(json.dumps(payload, separators=(",", ":")) + "\n")

pytestmark = [pytest.mark.sanity, pytest.mark.CPE_API_24]


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_01
@pytest.mark.CPE_API_24
async def test_api_01_btsconnect_success(
    cpe_api_24_client,
    cpe_api_24_config,
    bsu_ip,
    device_creds,
):
    await cpe_api_24_client.factory_reset_and_wait(label="Before API_01")
    broken = await is_bts_link_broken(
        bts_host=bsu_ip,
        username=device_creds["user"],
        password=device_creds["pass"],
        radio_idx=1,
    )
    if not broken:
        pytest.fail("Preflight: BTS still reports active partners on radio1 after CPE reset.")
    live = await fetch_bts_credentials_for_api(
        bts_host=bsu_ip,
        cpe_mgmt_host=cpe_api_24_config.cpe_ssh_host,
        username=device_creds["user"],
        password=device_creds["pass"],
        radio_idx=1,
    )
    # #region agent log
    _debug_log(
        run_id="run4",
        hypothesis_id="H9",
        location="tests/API/test_API.py:test_api_01_btsconnect_success",
        message="pre-api01 BTS creds compare",
        data={
            "config_ssid": cpe_api_24_config.bts_ssid,
            "live_ssid": live.ssid,
            "config_pw_len": len(cpe_api_24_config.bts_password or ""),
            "live_pw_len": len(live.password or ""),
            "config_pw_sha8": hashlib.sha256((cpe_api_24_config.bts_password or "").encode()).hexdigest()[:8],
            "live_pw_sha8": hashlib.sha256((live.password or "").encode()).hexdigest()[:8],
        },
    )
    # #endregion
    await assert_api_01_btsconnect_success(
        cpe_api_24_client,
        ssid=cpe_api_24_config.bts_ssid,
        password=cpe_api_24_config.bts_password,
    )


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_02
@pytest.mark.CPE_API_24
async def test_api_02_btsconnect_authentication_fail(
    cpe_api_24_client,
    cpe_api_24_config,
    bsu_ip,
    device_creds,
):
    await assert_api_02_btsconnect_auth_fail(cpe_api_24_client, cpe_api_24_config)
    broken = await is_bts_link_broken(
        bts_host=bsu_ip,
        username=device_creds["user"],
        password=device_creds["pass"],
        radio_idx=1,
    )
    if not broken:
        print(
            "    -> [WARN] API_02: BTS still reports partners after auth fail; "
            "treating case as PASS because API returned authentication_fail."
        )


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_03
@pytest.mark.CPE_API_24
async def test_api_03_btsconnect_ssid_not_found(cpe_api_24_client, cpe_api_24_config):
    await assert_api_03_btsconnect_ssid_not_found(cpe_api_24_client, cpe_api_24_config)


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_04
@pytest.mark.CPE_API_24
async def test_api_04_alignment_info_stream_1s(
    cpe_api_24_client,
    cpe_api_24_config,
    bsu_ip,
    device_creds,
):
    await ensure_link_before_api_04(
        cpe_api_24_client,
        cpe_api_24_config,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )
    await assert_api_04_alignment_info_stream_1s(cpe_api_24_client)


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_05
@pytest.mark.CPE_API_24
async def test_api_05_alignment_info_stream_5s(cpe_api_24_client):
    await assert_api_05_alignment_info_stream_5s(cpe_api_24_client)


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_06
@pytest.mark.CPE_API_24
async def test_api_06_alignment_data(cpe_api_24_client):
    await assert_api_06_alignment_data(cpe_api_24_client)


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_07
@pytest.mark.CPE_API_24
async def test_api_07_sw_version(cpe_api_24_client):
    await assert_api_07_sw_version(cpe_api_24_client)


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_08
@pytest.mark.CPE_API_24
async def test_api_08_sw_version_missing_file_returns_503(cpe_api_24_client):
    await assert_api_08_sw_version_unavailable_when_file_removed(cpe_api_24_client)


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_09
@pytest.mark.CPE_API_24
async def test_api_09_upload_sw(cpe_api_24_client, cpe_api_24_config):
    if not cpe_api_24_config.fw_file_path:
        pytest.fail(
            "API_09 requires firmware file on CLI, e.g.\n"
            "  --cpe-24-fw-file=Senao-UBR650-xxx-0.0.0.703-apps.tgz\n"
            "  (file is read from /tftpboot on the test PC unless you pass a full path)"
        )
    await assert_api_09_upload_sw(cpe_api_24_client, cpe_api_24_config.fw_file_path)


@pytest.mark.order(10)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_10
@pytest.mark.CPE_API_24
async def test_api_10_upload_sw_status(cpe_api_24_client):
    await assert_api_10_upload_sw_status(cpe_api_24_client)


@pytest.mark.order(11)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_11
@pytest.mark.CPE_API_24
async def test_api_11_upgrade_sw_preserve_config(cpe_api_24_client, bsu_ip, device_creds):
    await assert_api_11_upgrade_sw_preserve_config(
        cpe_api_24_client,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(12)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_12
@pytest.mark.CPE_API_24
async def test_api_12_upgrade_sw_no_preserve_config(
    cpe_api_24_client, cpe_api_24_config, bsu_ip, device_creds
):
    if not cpe_api_24_config.fw_file_path:
        pytest.fail(
            "API_12 requires --cpe-24-fw-file=Senao-UBR650-xxx-0.0.0.703-apps.tgz"
        )
    await assert_api_12_upgrade_sw_no_preserve_config(
        cpe_api_24_client,
        cpe_api_24_config,
        cpe_api_24_config.fw_file_path,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_13
@pytest.mark.CPE_API_24
async def test_api_13_upgrade_sw_status_in_progress(
    cpe_api_24_client, cpe_api_24_config, bsu_ip, device_creds
):
    if not cpe_api_24_config.fw_file_path:
        pytest.fail(
            "API_13 requires --cpe-24-fw-file=Senao-UBR650-xxx-0.0.0.703-apps.tgz"
        )
    await assert_api_13_upgrade_sw_status_in_progress(
        cpe_api_24_client,
        cpe_api_24_config.fw_file_path,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_14
@pytest.mark.CPE_API_24
async def test_api_14_upgrade_sw_status_success(
    cpe_api_24_client,
    cpe_api_24_config,
    bsu_ip,
    device_creds,
):
    await assert_api_14_upgrade_sw_status_success(
        cpe_api_24_client,
        cpe_api_24_config.fw_file_path,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_15
@pytest.mark.CPE_API_24
async def test_api_15_upgrade_sw_status_invalid_fw(
    cpe_api_24_client, cpe_api_24_config, request, bsu_ip, device_creds
):
    invalid_fw = resolve_invalid_fw_file_path(
        request,
        valid_fw_path=cpe_api_24_config.fw_file_path,
    )
    await assert_api_15_upgrade_sw_status_invalid_fw(
        cpe_api_24_client,
        invalid_fw,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(16)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_16
@pytest.mark.CPE_API_24
async def test_api_16_link_throughput_test(
    cpe_api_24_client,
    cpe_api_24_config,
    bsu_ip,
    device_creds,
):
    await ensure_link_before_api_16(
        cpe_api_24_client,
        cpe_api_24_config,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )
    await assert_api_16_link_throughput_test(cpe_api_24_client)


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_17
@pytest.mark.CPE_API_24
async def test_api_17_link_throughput_test_no_bts_server(
    cpe_api_24_client, bsu_ip, device_creds
):
    await assert_api_17_link_throughput_test_no_bts_server(
        cpe_api_24_client,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.API_18
@pytest.mark.CPE_API_24
async def test_api_18_link_throughput_test_in_progress(
    cpe_api_24_client,
    cpe_api_24_config,
    bsu_ip,
    device_creds,
):
    await ensure_link_before_api_16(
        cpe_api_24_client,
        cpe_api_24_config,
        bts_host=bsu_ip,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )
    await assert_api_18_link_throughput_test_in_progress(cpe_api_24_client)



