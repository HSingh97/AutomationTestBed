"""CPE Connect to BTS — API_01 → full CPE factory reset → API_02 → API_03."""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from typing import Any

from utils.cpe_api_24_client import CpeApi24Client, CpeApiResponse
from utils.cpe_api_24_config import (
    API_17_LINK_DOWN_MAX_S,
    API_17_LINK_UP_MAX_S,
    API_17_WIFI_SETTLE_S,
    API_18_FIRST_TEST_DURATION_S,
    API_18_IN_PROGRESS_POLL_DELAY_S,
    API_18_IN_PROGRESS_POLL_MAX_S,
    API_18_OVERLAP_DELAY_S,
    CpeApi24Config,
    CPE_RADIO1_STA_WIFI_IFACE_IDX,
    DEFAULT_LINK_THROUGHPUT_DURATION_S,
    LINK_THROUGHPUT_TEST_PATH,
    UPLOAD_SW_STATUS_PATH,
    UPGRADE_SW_STATUS_PATH,
    UPGRADE_SW_VERSION_PATH,
    INVALID_FW_STATUS_POLL_DELAY_S,
    INVALID_FW_STATUS_POLL_MAX_S,
    UPGRADE_IN_PROGRESS_STATES,
    UPGRADE_SUCCESS_STATES,
    infer_cpe_model_from_fw_path,
)
from utils.cpe_api_24_lab import (
    _parse_wifi_ifaces_from_uci_show,
    is_bts_link_broken,
    wait_bts_link_up,
)
from utils.verify_output import print_comparison_table, print_kv_block, print_section


def _debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
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


def _body(response: CpeApiResponse) -> dict[str, Any]:
    return response.json_body if isinstance(response.json_body, dict) else {}


def _print_result(case_id: str, response: CpeApiResponse, expected_http: str) -> None:
    body = response.json_body
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), expected_http, "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )


async def assert_api_01_btsconnect_success(
    client: CpeApi24Client,
    *,
    ssid: str,
    password: str,
    case_id: str = "API_01",
) -> None:
    print_section(f"{case_id} — CPE btsconnect success")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/CPE/btsconnect",
            "BTS SSID": ssid,
            "Expected": "HTTP 200, Connected successfully. (~2 min link time)",
        },
    )
    # #region agent log
    _debug_log(
        run_id="run3",
        hypothesis_id="H7",
        location="utils/cpe_api_24_flows.py:assert_api_01_btsconnect_success",
        message="sending API_01 btsconnect",
        data={"ssid": ssid, "password_len": len(password or "")},
    )
    # #endregion
    response = await client.btsconnect(
        ssid, password, read_timeout_s=client.config.btsconnect_timeout_s
    )
    body = _body(response)
    # #region agent log
    _debug_log(
        run_id="run3",
        hypothesis_id="H8",
        location="utils/cpe_api_24_flows.py:assert_api_01_btsconnect_success",
        message="API_01 btsconnect response",
        data={
            "status_code": response.status_code,
            "status": str(body.get("status", "")),
            "reason": str(body.get("reason", "")),
            "message": str(body.get("message", ""))[:120],
        },
    )
    # #endregion
    _print_result(case_id, response, "200")
    assert response.status_code == 200, (
        f"{case_id}: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    )
    assert str(body.get("status", "")).lower() == "success", (
        f"{case_id}: expected status=success, got {body!r}"
    )
    assert "connected successfully" in str(body.get("message", "")).lower(), (
        f"{case_id}: expected Connected successfully., got {body.get('message')!r}"
    )


async def assert_api_02_btsconnect_auth_fail(client: CpeApi24Client, config: CpeApi24Config) -> None:
    """After API_01: full CPE factory reset, POST correct BTS SSID + wrong password → 401."""
    await client.factory_reset_and_wait(label="Before API_02")
    wrong_password = config.invalid_password
    print_section("API_02 — authentication failure")
    print_kv_block(
        "Flow",
        {
            "BTS SSID": config.bts_ssid,
            "Password": f'wrong "{wrong_password}"',
            "Expected": "HTTP 401, reason=authentication_fail",
        },
    )
    response = await client.btsconnect(
        config.bts_ssid,
        wrong_password,
        read_timeout_s=config.btsconnect_negative_timeout_s,
    )
    body = _body(response)
    _print_result("API_02", response, "401")
    assert response.status_code == 401, f"API_02: expected 401, got {response.status_code}: {response.raw_body[:400]}"
    assert str(body.get("status", "")).lower() == "failure", f"API_02: expected status=failure, got {body!r}"
    assert str(body.get("reason", "")).lower() == "authentication_fail", (
        f"API_02: expected reason=authentication_fail, got {body.get('reason')!r}"
    )


async def assert_api_03_btsconnect_ssid_not_found(client: CpeApi24Client, config: CpeApi24Config) -> None:
    """No reset — POST invalid BTS SSID + valid password → HTTP 404, reason=ssid_not_found."""
    invalid_ssid = config.invalid_ssid
    print_section("API_03 — SSID not found (no CPE reset before this case)")
    print_kv_block(
        "Flow",
        {
            "BTS SSID": f'invalid "{invalid_ssid}"',
            "Password": "(valid BTS key)",
            "Expected": "HTTP 404, status=failure, reason=ssid_not_found",
        },
    )
    response = await client.btsconnect(
        invalid_ssid,
        config.bts_password,
        read_timeout_s=config.btsconnect_negative_timeout_s,
    )
    body = _body(response)
    reason = str(body.get("reason", "")).lower()
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "404", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("JSON status", str(body.get("status", "")), "failure", "CHECK"),
            ("Reason", reason or "(missing)", "ssid_not_found", "CHECK"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 404, (
        f"API_03: expected HTTP 404 (ssid_not_found), got {response.status_code}: "
        f"{response.raw_body[:400]}"
    )
    assert str(body.get("status", "")).lower() == "failure", f"API_03: expected status=failure, got {body!r}"
    assert reason == "ssid_not_found", (
        f"API_03: expected reason=ssid_not_found, got {body.get('reason')!r}"
    )


async def _ensure_bts_cpe_link(
    client: CpeApi24Client,
    config: CpeApi24Config,
    *,
    bts_host: str,
    bts_username: str,
    bts_password: str,
    context: str,
    always_connect: bool = False,
) -> None:
    print_section(f"{context} — ensure CPE↔BTS link exists")
    if not always_connect:
        broken = await is_bts_link_broken(
            bts_host=bts_host,
            username=bts_username,
            password=bts_password,
            radio_idx=1,
        )
        if not broken:
            print("    -> BTS already reports active partner(s); keeping existing link.")
            return
        print("    -> No active link on BTS; forming link via btsconnect...")
    else:
        print("    -> Re-forming link via btsconnect (prior config wiped)...")
    response = await client.btsconnect(
        config.bts_ssid,
        config.bts_password,
        read_timeout_s=config.btsconnect_timeout_s,
    )
    body = _body(response)
    _print_result(context, response, "200")
    assert response.status_code == 200, (
        f"{context}: expected 200 while forming link, got {response.status_code}: "
        f"{response.raw_body[:400]}"
    )
    assert str(body.get("status", "")).lower() == "success", (
        f"{context}: expected status=success, got {body!r}"
    )


async def ensure_link_before_api_04(
    client: CpeApi24Client,
    config: CpeApi24Config,
    *,
    bts_host: str,
    bts_username: str,
    bts_password: str,
) -> None:
    await _ensure_bts_cpe_link(
        client,
        config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        context="Precondition for API_04",
    )


async def ensure_link_before_api_16(
    client: CpeApi24Client,
    config: CpeApi24Config,
    *,
    bts_host: str,
    bts_username: str,
    bts_password: str,
) -> None:
    await _ensure_bts_cpe_link(
        client,
        config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        context="Precondition for API_16",
    )


async def ensure_bts_link_after_api_12(
    client: CpeApi24Client,
    config: CpeApi24Config,
    *,
    bts_host: str,
    bts_username: str,
    bts_password: str,
) -> None:
    """API_12 wipes config (preserveConfig=false); re-form BTS↔CPE link for later cases."""
    await _ensure_bts_cpe_link(
        client,
        config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        context="Post API_12",
        always_connect=True,
    )


async def assert_api_04_alignment_info_stream_1s(client: CpeApi24Client) -> None:
    print_section("API_04 — alignment info stream (1s interval)")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/alignment-info/stream?interval_ms=1000",
            "Expected": "HTTP 200, status success, stream has alignment metrics",
        },
    )
    response = await client.get_event_stream_sample(
        "/api/v1/cpe/alignment-info/stream?interval_ms=1000",
        read_timeout_s=20.0,
        bytes_limit=4096,
    )
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("Content-Type", str((response.headers or {}).get("content-type", "")), "text/event-stream", "INFO"),
            ("Stream sample", response.raw_body[:120], "contains metrics/rssi_dbm", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_04: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    sample = response.raw_body.lower()
    assert ("event: metrics" in sample) or ("rssi_dbm" in sample), (
        f"API_04: stream sample missing expected metrics keys: {response.raw_body[:400]}"
    )


async def assert_api_05_alignment_info_stream_5s(client: CpeApi24Client) -> None:
    print_section("API_05 — alignment info stream (5s interval)")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/alignment-info/stream?interval_ms=5000",
            "Expected": "HTTP 200, status success, stream has alignment metrics",
        },
    )
    response = await client.get_event_stream_sample(
        "/api/v1/cpe/alignment-info/stream?interval_ms=5000",
        read_timeout_s=25.0,
        bytes_limit=4096,
    )
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("Content-Type", str((response.headers or {}).get("content-type", "")), "text/event-stream", "INFO"),
            ("Stream sample", response.raw_body[:120], "contains metrics/rssi_dbm", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_05: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    sample = response.raw_body.lower()
    assert ("event: metrics" in sample) or ("rssi_dbm" in sample), (
        f"API_05: stream sample missing expected metrics keys: {response.raw_body[:400]}"
    )


async def assert_api_06_alignment_data(client: CpeApi24Client) -> None:
    print_section("API_06 — alignment data snapshot")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/alignment-data",
            "Expected": "HTTP 200, JSON status=success, includes rssi_dbm/a1/a2",
        },
    )
    response = await client.get("/api/v1/cpe/alignment-data", read_timeout_s=20.0)
    body = _body(response)
    _print_result("API_06", response, "200")
    assert response.status_code == 200, f"API_06: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert str(body.get("status", "")).lower() == "success", f"API_06: expected status=success, got {body!r}"
    message = str(body.get("message", "")).lower()
    assert "alignment data fetched successfully" in message, (
        f"API_06: expected success message, got {body.get('message')!r}"
    )


def _sw_version_from_body(body: dict[str, Any]) -> str | None:
    for key in ("swversion", "sw_version", "version", "swVersion"):
        value = body.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != "-":
            return text
    return None


async def assert_api_07_sw_version(client: CpeApi24Client) -> None:
    print_section("API_07 — CPE software version")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/sw-version",
            "Expected": "HTTP 200, status=success, swversion returned",
        },
    )
    response = await client.get("/api/v1/cpe/sw-version", read_timeout_s=20.0)
    body = _body(response)
    sw_version = _sw_version_from_body(body)
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("JSON status", str(body.get("status", "")), "success", "CHECK"),
            ("swversion", sw_version or "(missing)", "non-empty", "CHECK"),
        ]
    )
    assert response.status_code == 200, f"API_07: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert str(body.get("status", "")).lower() == "success", f"API_07: expected status=success, got {body!r}"
    assert sw_version, f"API_07: expected swversion in response body, got {body!r}"


async def assert_api_08_sw_version_unavailable_when_file_removed(client: CpeApi24Client) -> None:
    """
    Manual:
      rm -rf /etc/version
      curl -i http://169.254.254.1/api/v1/cpe/sw-version -> 503 software_info_unavailable
      recreate /etc/version
    """
    print_section("API_08 — sw-version returns 503 if /etc/version missing")
    print_kv_block(
        "Flow",
        {
            "SSH": "backup /etc/version, rm -rf /etc/version",
            "API": "http://169.254.254.1/api/v1/cpe/sw-version",
            "Expected": "HTTP 503, reason=software_info_unavailable",
            "Cleanup": "restore /etc/version",
        },
    )

    backup_cmd = "sh -c 'if [ -f /etc/version ]; then cat /etc/version; fi'"
    rm_cmd = "rm -rf /etc/version"

    exit_status, stdout, stderr = await client.ssh_run(backup_cmd, timeout_s=10.0)
    # #region agent log
    _debug_log(
        run_id="run2",
        hypothesis_id="H4",
        location="utils/cpe_api_24_flows.py:assert_api_08",
        message="backup /etc/version result",
        data={"exit_status": exit_status, "bytes": len(stdout), "preview": stdout[:120]},
    )
    # #endregion
    if exit_status not in (0, None):
        raise RuntimeError(f"API_08: failed reading /etc/version before delete: {stderr[:200] or stdout[:200]}")
    original = stdout

    try:
        exit_status, stdout, stderr = await client.ssh_run(rm_cmd, timeout_s=10.0)
        # #region agent log
        _debug_log(
            run_id="run2",
            hypothesis_id="H3",
            location="utils/cpe_api_24_flows.py:assert_api_08",
            message="remove /etc/version result",
            data={"exit_status": exit_status, "stderr": (stderr or "")[:120]},
        )
        # #endregion
        if exit_status not in (0, None):
            raise RuntimeError(f"API_08: rm /etc/version failed: {stderr[:200] or stdout[:200]}")

        response = await client.get("/api/v1/cpe/sw-version", read_timeout_s=20.0)
        body = _body(response)
        # #region agent log
        _debug_log(
            run_id="run2",
            hypothesis_id="H3",
            location="utils/cpe_api_24_flows.py:assert_api_08",
            message="sw-version response after delete",
            data={"status_code": response.status_code, "reason": str(body.get("reason", ""))},
        )
        # #endregion
        print_comparison_table(
            [
                ("HTTP status", str(response.status_code), "503", "CHECK"),
                ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
                ("JSON status", str(body.get("status", "")), "failure", "CHECK"),
                ("Reason", str(body.get("reason", "")), "software_info_unavailable", "CHECK"),
            ]
        )
        assert response.status_code == 503, f"API_08: expected 503, got {response.status_code}: {response.raw_body[:400]}"
        assert str(body.get("status", "")).lower() == "failure", f"API_08: expected status=failure, got {body!r}"
        assert str(body.get("reason", "")).lower() == "software_info_unavailable", (
            f"API_08: expected reason=software_info_unavailable, got {body.get('reason')!r}"
        )
    finally:
        # Restore original file contents (or write a minimal placeholder if it didn't exist).
        restore_text = original if original.strip() else "UNKNOWN\n"
        # base64 avoids heredoc/shell quoting bugs that wrote the delimiter into /etc/version.
        restore_b64 = base64.b64encode(restore_text.encode("utf-8")).decode("ascii")
        restore_cmd = f"umask 022; echo {restore_b64} | base64 -d > /etc/version"
        exit_status, stdout, stderr = await client.ssh_run(restore_cmd, timeout_s=10.0)
        # #region agent log
        _debug_log(
            run_id="run2",
            hypothesis_id="H5",
            location="utils/cpe_api_24_flows.py:assert_api_08",
            message="restore /etc/version result",
            data={"exit_status": exit_status, "stderr": (stderr or "")[:120], "restored_bytes": len(restore_text)},
        )
        # #endregion
        verify_status, verify_out, verify_err = await client.ssh_run(
            "sh -c 'if [ -f /etc/version ]; then wc -c /etc/version; cat /etc/version; else echo MISSING; fi'",
            timeout_s=10.0,
        )
        # #region agent log
        _debug_log(
            run_id="run2",
            hypothesis_id="H5",
            location="utils/cpe_api_24_flows.py:assert_api_08",
            message="post-restore /etc/version verify",
            data={
                "exit_status": verify_status,
                "stdout_preview": verify_out[:180],
                "stderr_preview": verify_err[:120],
            },
        )
        # #endregion


async def assert_api_09_upload_sw(client: CpeApi24Client, fw_path: str) -> None:
    """POST firmware .tgz from lab PC /tftpboot to CPE upload-sw."""
    from pathlib import Path

    image = Path(fw_path).expanduser().resolve()
    cpe_model = infer_cpe_model_from_fw_path(str(image))
    if not cpe_model:
        raise AssertionError(
            f"API_09: cannot detect UBR model from firmware filename: {image.name}\n"
            "  Use a name like Senao-UBR650-xxx-apps.tgz for model detection in logs."
        )
    client.set_fw_upload_model(cpe_model)
    print_kv_block(
        "FW upload — CPE model",
        {
            "Model (from filename)": cpe_model,
            "API_11+": "after upgrade, polls until reboot, device up, and BTS link",
        },
    )
    # #region agent log
    _debug_log(
        run_id="post-fix",
        hypothesis_id="H4",
        location="utils/cpe_api_24_flows.py:assert_api_09",
        message="cpe model from fw filename",
        data={"fw_path": str(image.name), "cpe_model": cpe_model},
    )
    # #endregion
    print_section("API_09 — upload firmware (upload-sw)")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/upload-sw",
            "FW file": str(image),
            "Expected": "HTTP 200, status=success",
        },
    )
    response = await client.upload_sw(str(image))
    body = _body(response)
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "curl", "INFO"),
            ("JSON body", str(response.json_body)[:120], "status=success", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_09: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert str(body.get("status", "")).lower() == "success", f"API_09: expected status=success, got {body!r}"


async def assert_api_10_upload_sw_status(client: CpeApi24Client) -> None:
    """GET upload status after API_09 — expects fw upload success."""
    cpe_model = client.fw_upload_model()
    print_section("API_10 — upload firmware status (upload-sw-status)")
    flow: dict[str, str] = {
        "API": "http://169.254.254.1/api/v1/cpe/upload-sw-status",
        "Expected": "HTTP 200, state=SUCCESS, progress=100",
    }
    if cpe_model:
        flow["Next (API_11)"] = f"upgrade + wait until BTS link ({cpe_model})"
    print_kv_block("Flow", flow)
    response = await client.get("/api/v1/cpe/upload-sw-status", read_timeout_s=60.0)
    body = _body(response)
    state = str(body.get("state", body.get("status", ""))).upper()
    progress = body.get("progress")
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("State", state, "SUCCESS", "CHECK"),
            ("Progress", str(progress), "100", "CHECK"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_10: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert state == "SUCCESS", f"API_10: expected state=SUCCESS, got {body!r}"
    assert progress == 100 or str(progress) == "100", (
        f"API_10: expected progress=100 (upload complete), got {body.get('progress')!r}"
    )


async def assert_api_11_upgrade_sw_preserve_config(
    client: CpeApi24Client,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> None:
    """POST upgrade-sw with preserveConfig=true (after upload in API_09/10)."""
    model, _ = await client.resolve_cpe_model()
    print_section("API_11 — start firmware upgrade (preserve config)")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/upgrade-sw",
            "Body": '{"delay":"5", "preserveConfig":true}',
            "Expected": "HTTP 200, status=UPGRADE_STARTED",
            "CPE model (from FW upload)": model,
            "Then": "poll until reboot, device up, and BTS link",
        },
    )
    response = await client.upgrade_sw(delay_s="5", preserve_config=True, read_timeout_s=60.0)
    body = _body(response)
    status = str(body.get("status", body.get("state", ""))).upper()
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("Status", status, "UPGRADE_STARTED", "CHECK"),
            ("Message", str(body.get("message", ""))[:80], "reboot during process", "INFO"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_11: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert status == "UPGRADE_STARTED", f"API_11: expected status=UPGRADE_STARTED, got {body!r}"

    await client.wait_post_upgrade_and_link(
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
    )


async def ensure_mgmt_api_for_tests(
    client: CpeApi24Client,
    config: CpeApi24Config,
    *,
    bts_host: str | None,
    bts_username: str,
    bts_password: str,
    label: str,
) -> None:
    from utils.cpe_mgmt_wifi import ensure_pc_mgmt_api_ready

    await ensure_pc_mgmt_api_ready(
        config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label=label,
    )


async def assert_api_12_upgrade_sw_no_preserve_config(
    client: CpeApi24Client,
    config: CpeApi24Config,
    fw_path: str,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> None:
    """Same flow as API_11 (upload + upgrade); preserveConfig=false; then wait up + btsconnect."""
    model, _ = await client.resolve_cpe_model()

    print_section("API_12 — re-upload firmware (same as API_11)")
    await assert_api_09_upload_sw(client, fw_path)
    await assert_api_10_upload_sw_status(client)

    print_section("API_12 — start firmware upgrade (without preserve config)")
    print_kv_block(
        "Flow",
        {
            "API": "http://169.254.254.1/api/v1/cpe/upgrade-sw",
            "Body": '{"delay":"5", "preserveConfig":false}',
            "Expected": "HTTP 200, status=UPGRADE_STARTED",
            "CPE model (from FW upload)": model,
            "Then": "wait for device up, then btsconnect (API_01)",
        },
    )
    response = await client.upgrade_sw(delay_s="5", preserve_config=False, read_timeout_s=60.0)
    body = _body(response)
    status = str(body.get("status", body.get("state", ""))).upper()
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("Status", status, "UPGRADE_STARTED", "CHECK"),
            ("preserveConfig", "false", "false", "CHECK"),
            ("Message", str(body.get("message", ""))[:80], "reboot during process", "INFO"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_12: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert status == "UPGRADE_STARTED", f"API_12: expected status=UPGRADE_STARTED, got {body!r}"

    up = await client.wait_mgmt_device_up_after_reboot(
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label="API_12",
    )
    print_comparison_table(
        [
            ("CPE mgmt API", "up", "up", "CHECK"),
            ("CPE mgmt SSID", str(up.get("cpe_mgmt_ssid", "")), "(new after wipe)", "INFO"),
            ("Wait (s)", f"{up.get('elapsed_s', 0):.0f}", "—", "INFO"),
        ]
    )

    await assert_api_01_btsconnect_success(
        client,
        ssid=config.bts_ssid,
        password=config.bts_password,
        case_id="API_12",
    )


async def assert_api_13_upgrade_sw_status_in_progress(
    client: CpeApi24Client,
    fw_path: str,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> None:
    """GET upgrade-sw-status while upgrade is in progress (after upload + upgrade start)."""
    from pathlib import Path

    await ensure_mgmt_api_for_tests(
        client,
        client.config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label="API_13",
    )

    image = Path(fw_path).expanduser().resolve()
    print_section("API_13 — upgrade status (in progress)")
    print_kv_block(
        "Flow",
        {
            "API": f"http://169.254.254.1{UPGRADE_SW_STATUS_PATH}",
            "Note": "Spec: upgrade-sw-version → device uses upgrade-sw-status",
            "Setup": "upload FW, POST upgrade-sw, then GET status",
            "Expected": "HTTP 200, firmware upgrade in progress",
        },
    )

    await assert_api_09_upload_sw(client, str(image))
    await assert_api_10_upload_sw_status(client)

    upgrade_resp = await client.upgrade_sw(delay_s="5", preserve_config=True, read_timeout_s=60.0)
    upgrade_body = _body(upgrade_resp)
    upgrade_status = str(upgrade_body.get("status", upgrade_body.get("state", ""))).upper()
    assert upgrade_resp.status_code == 200, (
        f"API_13 setup: expected upgrade 200, got {upgrade_resp.status_code}: {upgrade_resp.raw_body[:400]}"
    )
    assert upgrade_status == "UPGRADE_STARTED", f"API_13 setup: expected UPGRADE_STARTED, got {upgrade_body!r}"

    response = await client.get(UPGRADE_SW_STATUS_PATH, read_timeout_s=60.0)
    body = _body(response)
    state = str(body.get("state", body.get("status", ""))).upper().replace(" ", "_")
    if state == "IN PROGRESS":
        state = "IN_PROGRESS"
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("Status", state, "INPROGRESS", "CHECK"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 200, f"API_13: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    assert state in UPGRADE_IN_PROGRESS_STATES, (
        f"API_13: expected firmware upgrade in progress, got state={body.get('state')!r} body={body!r}"
    )


async def _ensure_upgrade_started_for_api_14(
    client: CpeApi24Client,
    fw_path: str | None,
) -> float:
    """If API_13 did not run, upload FW and start upgrade (preserve config)."""
    from pathlib import Path

    response = await client.get(UPGRADE_SW_STATUS_PATH, read_timeout_s=60.0)
    body = _body(response)
    state = str(body.get("state", body.get("status", ""))).upper().replace(" ", "_")
    if state == "IN PROGRESS":
        state = "IN_PROGRESS"
    if state in UPGRADE_IN_PROGRESS_STATES or state in UPGRADE_SUCCESS_STATES:
        return time.monotonic()

    if not fw_path:
        raise AssertionError(
            "API_14: no upgrade in progress. Run API_13 first in the same session, or pass "
            "--cpe-24-fw-file for standalone API_14."
        )

    image = Path(fw_path).expanduser().resolve()
    print_section("API_14 — setup (upload + start upgrade; API_13 not run)")
    await assert_api_09_upload_sw(client, str(image))
    await assert_api_10_upload_sw_status(client)
    upgrade_resp = await client.upgrade_sw(delay_s="5", preserve_config=True, read_timeout_s=60.0)
    upgrade_body = _body(upgrade_resp)
    upgrade_status = str(upgrade_body.get("status", upgrade_body.get("state", ""))).upper()
    assert upgrade_resp.status_code == 200, (
        f"API_14 setup: expected upgrade 200, got {upgrade_resp.status_code}: {upgrade_resp.raw_body[:400]}"
    )
    assert upgrade_status == "UPGRADE_STARTED", (
        f"API_14 setup: expected UPGRADE_STARTED, got {upgrade_body!r}"
    )
    return time.monotonic()


async def assert_api_14_upgrade_sw_status_success(
    client: CpeApi24Client,
    fw_path: str | None,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> None:
    """After API_13: wait for upgrade SUCCESS on upgrade-sw-status, then BTS link."""
    await ensure_mgmt_api_for_tests(
        client,
        client.config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label="API_14",
    )

    upgrade_flow_start = await _ensure_upgrade_started_for_api_14(client, fw_path)

    print_section("API_14 — upgrade status (fw upgrade success)")
    print_kv_block(
        "Flow",
        {
            "API": f"http://169.254.254.1{UPGRADE_SW_VERSION_PATH}",
            "Note": "Spec: upgrade-sw-version → device uses upgrade-sw-status",
            "After": "API_13 (in progress) — poll until SUCCESS, then BTS link",
            "Expected": "HTTP 200, fw upgrade success",
        },
    )

    await client.wait_upgrade_sw_status_success(label="API_14")

    await client.wait_bts_link_up_after_device(
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label="API_14",
        upgrade_start=upgrade_flow_start,
    )

    response = await client.get(UPGRADE_SW_VERSION_PATH, read_timeout_s=60.0)
    body = _body(response)
    state = str(body.get("state", body.get("status", ""))).upper().replace(" ", "_")
    if state == "IN PROGRESS":
        state = "IN_PROGRESS"
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("Status", state, "SUCCESS", "CHECK"),
            ("Message", str(body.get("message", ""))[:80], "fw upgrade success", "INFO"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 200, (
        f"API_14: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    )
    assert state in UPGRADE_SUCCESS_STATES, (
        f"API_14: expected fw upgrade success, got state={body.get('state')!r} body={body!r}"
    )


def _invalid_fw_indicated(body: dict[str, Any], raw_body: str) -> bool:
    err = body.get("error")
    if isinstance(err, dict):
        code = str(err.get("code", "")).upper()
        if code in ("FW_INVALID_IMAGE", "INVALID_FW_FILE", "INVALID_FW"):
            return True
        msg = str(err.get("message", "")).upper()
        if "INVALID" in msg and ("FIRMWARE" in msg or "FW" in msg or "INCOMPATIBLE" in msg):
            return True
    blob = raw_body.upper()
    return "FW_INVALID" in blob or ("INVALID" in blob and "FIRMWARE" in blob)


def _response_is_invalid_fw(response: CpeApiResponse) -> bool:
    return response.status_code == 422 and _invalid_fw_indicated(
        _body(response), response.raw_body
    )


def _print_api_15_result(response: CpeApiResponse, *, elapsed_s: float | None = None) -> None:
    body = _body(response)
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = str(err.get("code", body.get("state", "")))
    message = str(err.get("message", body.get("message", "")))[:80]
    rows = [
        ("HTTP status", str(response.status_code), "422", "CHECK"),
        ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
        ("Error code", code, "FW_INVALID_IMAGE", "CHECK"),
        ("Message", message, "invalid / incompatible", "INFO"),
        ("JSON body", str(body)[:120], "—", "INFO"),
    ]
    if elapsed_s is not None:
        rows.insert(3, ("Elapsed (s)", f"{elapsed_s:.0f}", "—", "INFO"))
    print_comparison_table(rows)


async def _poll_upgrade_sw_status_invalid_fw(client: CpeApi24Client) -> CpeApiResponse:
    """Poll GET upgrade-sw-status until HTTP 422 (device often shows INPROGRESS first)."""
    start = time.monotonic()
    deadline = start + INVALID_FW_STATUS_POLL_MAX_S
    last_response: CpeApiResponse | None = None
    last_state = ""

    while time.monotonic() < deadline:
        response = await client.get(UPGRADE_SW_VERSION_PATH, read_timeout_s=30.0, log=False)
        last_response = response
        body = _body(response)
        last_state = str(body.get("state", body.get("status", ""))).upper()

        if _response_is_invalid_fw(response):
            return response

        if last_state in UPGRADE_SUCCESS_STATES:
            raise AssertionError(
                f"API_15: wrong-model FW reached SUCCESS on upgrade-sw-status: {body!r}"
            )

        await asyncio.sleep(INVALID_FW_STATUS_POLL_DELAY_S)

    last = last_response
    raise AssertionError(
        f"API_15: no HTTP 422 within {INVALID_FW_STATUS_POLL_MAX_S:.0f}s. "
        f"Last GET={last.status_code if last else 'n/a'} state={last_state!r} "
        f"body={(last.raw_body[:300] if last else '')!r}"
    )


async def assert_api_15_upgrade_sw_status_invalid_fw(
    client: CpeApi24Client,
    invalid_fw_path: str,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> None:
    """Wrong-model FW: upload → upgrade-sw → poll upgrade-sw-status for 422."""
    from pathlib import Path

    await ensure_mgmt_api_for_tests(
        client,
        client.config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label="API_15",
    )

    image = Path(invalid_fw_path).expanduser().resolve()
    wrong_model = infer_cpe_model_from_fw_path(str(image)) or "unknown"
    cpe_model, _ = await client.resolve_cpe_model()
    flow_start = time.monotonic()

    print_section("API_15 — invalid firmware (wrong-model)")
    print_kv_block(
        "Flow",
        {
            "Steps": "upload-sw → upgrade-sw → GET upgrade-sw-status (poll)",
            "CPE": cpe_model,
            "FW file": image.name,
            "FW model": wrong_model,
            "Expected": "HTTP 422, FW_INVALID_IMAGE / invalid firmware",
        },
    )

    upload_resp = await client.upload_sw(
        str(image),
        read_timeout_s=client.config.fw_upload_timeout_s,
    )
    if _response_is_invalid_fw(upload_resp):
        _print_api_15_result(upload_resp, elapsed_s=time.monotonic() - flow_start)
        return

    upgrade_resp = await client.upgrade_sw(delay_s="5", preserve_config=True, read_timeout_s=60.0)
    if _response_is_invalid_fw(upgrade_resp):
        _print_api_15_result(upgrade_resp, elapsed_s=time.monotonic() - flow_start)
        return

    assert upgrade_resp.status_code == 200, (
        f"API_15: expected upgrade-sw 200 to start validation, got {upgrade_resp.status_code}: "
        f"{upgrade_resp.raw_body[:300]}"
    )

    poll_start = time.monotonic()
    response = await _poll_upgrade_sw_status_invalid_fw(client)
    _print_api_15_result(response, elapsed_s=time.monotonic() - poll_start)


async def assert_api_16_link_throughput_test(
    client: CpeApi24Client,
    *,
    duration_s: int = DEFAULT_LINK_THROUGHPUT_DURATION_S,
) -> None:
    """POST link-throughput-test; expect HTTP 200, status=success (throughput fields from device)."""
    print_section("API_16 — link throughput test")
    print_kv_block(
        "Flow",
        {
            "API": f"http://169.254.254.1{LINK_THROUGHPUT_TEST_PATH}",
            "Body": f'{{"duration":{duration_s}}}',
            "Expected": "HTTP 200, status=success (dl_tput/ul_tput; link_speed N/A on current FW)",
            "Note": "Requires active BTS↔CPE link",
        },
    )
    response = await client.link_throughput_test(duration_s=duration_s)
    body = _body(response)
    dl_tput = body.get("dl_tput")
    ul_tput = body.get("ul_tput")
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("JSON status", str(body.get("status", "")), "success", "CHECK"),
            ("dl_tput", str(dl_tput), "present", "CHECK"),
            ("ul_tput", str(ul_tput), "present", "CHECK"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 200, (
        f"API_16: expected 200, got {response.status_code}: {response.raw_body[:400]}"
    )
    assert str(body.get("status", "")).lower() == "success", (
        f"API_16: expected status=success, got {body!r}"
    )
    assert dl_tput is not None and ul_tput is not None, (
        f"API_16: expected dl_tput and ul_tput in response, got {body!r}"
    )


def _failure_reason_from_body(body: dict[str, Any]) -> str:
    reason = str(body.get("reason", "")).strip().lower()
    if reason:
        return reason
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("reason", err.get("message", ""))).strip().lower()
    return str(body.get("message", "")).strip().lower()


async def _cpe_sta_iface_index_for_radio1(client: CpeApi24Client) -> str:
    """Return UCI wifi-iface index for CPE STA (Radio 1 / BTS link), prefer iface[1]."""
    _, out, _ = await client.ssh_run(
        "uci show wireless | grep -E '@wifi-iface\\[[0-9]+\\]\\.(mode|ssid)='",
        timeout_s=15.0,
    )
    parsed = _parse_wifi_ifaces_from_uci_show(out or "")
    sta_indices = sorted(
        (idx for idx, data in parsed.items() if data.get("mode") == "sta"),
        key=int,
    )
    if not sta_indices:
        raise RuntimeError("API_17: no CPE wireless STA iface (mode=sta) found.")
    preferred = str(CPE_RADIO1_STA_WIFI_IFACE_IDX)
    if preferred in sta_indices:
        return preferred
    return sta_indices[0]


async def _read_cpe_wifi_iface_uci(client: CpeApi24Client, idx: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("ssid", "disabled", "network", "ifname", "mode"):
        _, out, _ = await client.ssh_run(
            f"uci -q get wireless.@wifi-iface[{idx}].{key}",
            timeout_s=10.0,
        )
        fields[key] = (out or "").strip()
    return fields


async def _resolve_cpe_sta_ifname(client: CpeApi24Client, uci: dict[str, str]) -> str:
    ifname = uci.get("ifname", "")
    if ifname:
        return ifname
    _, out, _ = await client.ssh_run(
        "iw dev 2>/dev/null | awk '/Interface/{i=$2} /type/{if($2==\"managed\"){print i; exit}}'",
        timeout_s=15.0,
    )
    return (out or "").strip().splitlines()[0] if out and out.strip() else ""


async def _stop_cpe_sta_link(client: CpeApi24Client, *, network: str, ifname: str) -> None:
    if network:
        await client.ssh_run(f"ifdown {network} 2>/dev/null", timeout_s=30.0)
    if ifname:
        await client.ssh_run(
            f"wpa_cli -i {ifname} disconnect 2>/dev/null; "
            f"iw dev {ifname} disconnect 2>/dev/null; "
            f"ifconfig {ifname} down 2>/dev/null",
            timeout_s=20.0,
        )


async def break_cpe_radio1_sta_ssid_for_api_17(
    client: CpeApi24Client,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> dict[str, str]:
    """Random SSID + disable Radio 1 STA, reload Wi‑Fi, verify UCI and BTS link down."""
    idx = await _cpe_sta_iface_index_for_radio1(client)
    uci = await _read_cpe_wifi_iface_uci(client, idx)
    original_ssid = uci.get("ssid", "")
    if not original_ssid:
        raise RuntimeError(f"API_17: wireless.@wifi-iface[{idx}].ssid is empty.")

    ifname = await _resolve_cpe_sta_ifname(client, uci)
    network = uci.get("network", "")
    await _stop_cpe_sta_link(client, network=network, ifname=ifname)

    random_ssid = f"ZINV{random.randint(100000, 999999)}"
    await client.ssh_run(
        f"uci set wireless.@wifi-iface[{idx}].ssid='{random_ssid}'",
        timeout_s=10.0,
    )
    await client.ssh_run(f"uci set wireless.@wifi-iface[{idx}].disabled=1", timeout_s=10.0)
    await client.ssh_run("uci commit wireless", timeout_s=20.0)
    await client.ssh_run("wifi reload 2>/dev/null || wifi", timeout_s=90.0)

    _, verify_out, _ = await client.ssh_run(
        f"uci -q get wireless.@wifi-iface[{idx}].ssid",
        timeout_s=10.0,
    )
    applied = (verify_out or "").strip()
    if applied != random_ssid:
        raise RuntimeError(
            f"API_17: Radio 1 SSID not applied in UCI (expected {random_ssid!r}, got {applied!r})."
        )
    print(
        f"    -> CPE Radio 1 wifi-iface[{idx}] ({uci.get('mode', 'sta')}): "
        f"SSID {original_ssid!r} -> {random_ssid!r}, disabled=1"
        + (f", ifdown {network}" if network else "")
        + (f", {ifname} down" if ifname else "")
    )

    if bts_host and bts_password:
        deadline = time.monotonic() + API_17_LINK_DOWN_MAX_S
        while time.monotonic() < deadline:
            if await is_bts_link_broken(
                bts_host=bts_host,
                username=bts_username,
                password=bts_password,
                radio_idx=1,
            ):
                print("    -> BTS radio 1 remote partners=0 (link down).")
                break
            await asyncio.sleep(3.0)
        else:
            raise RuntimeError(
                f"API_17: BTS link still up after Radio 1 SSID/disabled change "
                f"({API_17_LINK_DOWN_MAX_S:.0f}s)."
            )

    return {
        "iface_idx": idx,
        "ssid": original_ssid,
        "disabled": uci.get("disabled", "0") or "0",
        "network": network,
        "ifname": ifname,
    }


async def restore_cpe_radio1_sta_ssid(
    client: CpeApi24Client,
    backup: dict[str, str],
) -> None:
    idx = backup.get("iface_idx", "")
    if not idx:
        return
    await client.ssh_run(
        f"uci set wireless.@wifi-iface[{idx}].ssid='{backup['ssid']}'",
        timeout_s=10.0,
    )
    disabled = backup.get("disabled", "0") or "0"
    await client.ssh_run(
        f"uci set wireless.@wifi-iface[{idx}].disabled={disabled}",
        timeout_s=10.0,
    )
    await client.ssh_run("uci commit wireless", timeout_s=20.0)
    await client.ssh_run("wifi reload 2>/dev/null || wifi", timeout_s=90.0)
    network = backup.get("network", "")
    if network:
        await client.ssh_run(f"ifup {network} 2>/dev/null", timeout_s=30.0)
    print(
        f"    -> Restored CPE Radio 1 wifi-iface[{idx}]: "
        f"SSID={backup['ssid']!r}, disabled={disabled}."
    )


def _assert_api_17_throughput_failure(
    response: CpeApiResponse,
    *,
    method: str,
) -> None:
    body = _body(response)
    reason = _failure_reason_from_body(body)
    print_comparison_table(
        [
            ("Method", method, "—", "INFO"),
            ("HTTP status", str(response.status_code), "409", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("JSON status", str(body.get("status", body.get("state", ""))), "failure", "CHECK"),
            ("reason", reason or "(missing)", "server did not respond", "CHECK"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 409, (
        f"API_17: expected 409, got {response.status_code}: {response.raw_body[:400]}"
    )
    status = str(body.get("status", body.get("state", ""))).lower()
    assert status == "failure", f"API_17: expected status=failure, got {body!r}"
    assert "server" in reason and "respond" in reason, (
        f"API_17: expected reason=server did not respond, got {body!r}"
    )


async def assert_api_17_link_throughput_test_no_bts_server(
    client: CpeApi24Client,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
    duration_s: int = DEFAULT_LINK_THROUGHPUT_DURATION_S,
) -> None:
    """
    Break BTS↔CPE link (random CPE Radio 1 STA SSID), POST link-throughput-test → 409 failure.
    Restores original SSID afterward (no factory reset).
    """
    print_section("API_17 — link throughput test (no BTS server)")
    print_kv_block(
        "Flow",
        {
            "Precondition": "CPE Radio 1 random SSID + disabled=1; BTS partners=0",
            "API": f"http://169.254.254.1{LINK_THROUGHPUT_TEST_PATH}",
            "Body": f'{{"duration":{duration_s}}}',
            "Expected": "HTTP 409, status=failure, reason=server did not respond",
            "Cleanup": "restore original CPE Radio 1 SSID",
        },
    )

    if not bts_host or not bts_password:
        raise RuntimeError("API_17 requires --local-ipv6 to confirm BTS link is down.")

    backup = await break_cpe_radio1_sta_ssid_for_api_17(
        client,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
    )
    try:
        if API_17_WIFI_SETTLE_S > 0:
            await asyncio.sleep(API_17_WIFI_SETTLE_S)
        response = await client.link_throughput_test(duration_s=duration_s)
        _assert_api_17_throughput_failure(
            response,
            method="CPE Radio 1 random SSID (no BTS link)",
        )
    finally:
        await restore_cpe_radio1_sta_ssid(client, backup)
        await wait_bts_link_up(
            bts_host=bts_host or "",
            username=bts_username,
            password=bts_password,
            max_wait_s=API_17_LINK_UP_MAX_S,
            label="API_17",
        )


async def assert_api_18_link_throughput_test_in_progress(
    client: CpeApi24Client,
    *,
    first_duration_s: int = API_18_FIRST_TEST_DURATION_S,
) -> None:
    """
    Start link-throughput-test, POST again while still running → HTTP 503, test_in_progress.
    """
    print_section("API_18 — link throughput test already in progress")
    print_kv_block(
        "Flow",
        {
            "API": f"http://169.254.254.1{LINK_THROUGHPUT_TEST_PATH}",
            "Step 1": f"POST duration={first_duration_s} (runs in background)",
            "Step 2": f"POST again after {API_18_OVERLAP_DELAY_S:.0f}s while first test runs",
            "Expected": "HTTP 503, status=failure, reason=test_in_progress",
        },
    )

    print(f"    -> Starting first throughput test ({first_duration_s}s)...")
    first_task = asyncio.create_task(
        client.link_throughput_test(duration_s=first_duration_s),
        name="api_18_first_throughput",
    )
    await asyncio.sleep(API_18_OVERLAP_DELAY_S)

    response: CpeApiResponse | None = None
    poll_start = time.monotonic()
    deadline = poll_start + API_18_IN_PROGRESS_POLL_MAX_S
    while time.monotonic() < deadline:
        response = await client.link_throughput_test(
            duration_s=5,
            read_timeout_s=30.0,
        )
        reason = _failure_reason_from_body(_body(response))
        if response.status_code == 503 and (
            "test_in_progress" in reason or "in progress" in reason
        ):
            break
        await asyncio.sleep(API_18_IN_PROGRESS_POLL_DELAY_S)

    if response is None:
        raise RuntimeError("API_18: no overlapping POST response captured.")

    body = _body(response)
    reason = _failure_reason_from_body(body)
    print_comparison_table(
        [
            ("HTTP status", str(response.status_code), "503", "CHECK"),
            ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
            ("JSON status", str(body.get("status", body.get("state", ""))), "failure", "CHECK"),
            ("reason", reason or "(missing)", "test_in_progress", "CHECK"),
            ("JSON body", str(body)[:120], "—", "INFO"),
        ]
    )
    assert response.status_code == 503, (
        f"API_18: expected 503, got {response.status_code}: {response.raw_body[:400]}"
    )
    status = str(body.get("status", body.get("state", ""))).lower()
    assert status == "failure", f"API_18: expected status=failure, got {body!r}"
    assert "test_in_progress" in reason or "in progress" in reason, (
        f"API_18: expected reason=test_in_progress, got {body!r}"
    )

    print("    -> Waiting for first throughput test to finish...")
    first_resp = await first_task
    print(
        f"    -> First test completed: HTTP {first_resp.status_code} "
        f"({first_resp.raw_body[:80]!r}...)"
    )
