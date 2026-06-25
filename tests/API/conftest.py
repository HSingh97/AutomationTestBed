"""
CPE API — PC auto-joins CPE hidden mgmt Wi‑Fi KWDEJPOQ (169.254.254.1); BTS creds for link restore only.
"""

from __future__ import annotations

import json
import time

import pytest
from dataclasses import replace

from utils.cpe_api_24_client import CpeApi24Client
from utils.cpe_api_24_config import (
    CPE_24_MGMT_API_HOST,
    merge_cli_overrides,
    require_cpe_api_24_cli,
    resolve_cpe_api_24_config,
)
from utils.cpe_api_24_lab import fetch_bts_credentials_for_api
from utils.cpe_mgmt_wifi import disconnect_pc_cpe_wifi, ensure_pc_on_cpe_mgmt_wifi
from utils.verify_output import print_kv_block, print_section

DEBUG_LOG_PATH = "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-a5d6ea.log"
DEBUG_SESSION_ID = "a5d6ea"


def _debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _bts_on_cli(request) -> bool:
    ssid = (request.config.getoption("--cpe-24-bts-ssid") or "").strip()
    pw = request.config.getoption("--cpe-24-bts-password")
    return bool(ssid) and pw is not None and str(pw) != ""


@pytest.fixture(scope="session")
async def cpe_api_24_config(request, profile_bundle, bsu_ip, device_creds):
    if request.config.getoption("--skip-cpe-api-24"):
        pytest.skip("CPE API tests skipped (--skip-cpe-api-24).")

    config = resolve_cpe_api_24_config(request, profile_bundle.active)
    # Force reset path to 2.4 GHz management host only.
    config = replace(
        config,
        cpe_ssh_host=CPE_24_MGMT_API_HOST,
        cpe_ssh_tunnel_host=None,
        cpe_ssh_via_bts_tunnel=False,
    )
    print(f"    -> [Preflight] CPE reset SSH host fixed to {CPE_24_MGMT_API_HOST}")
    # #region agent log
    _debug_log(
        run_id="run1",
        hypothesis_id="H13",
        location="tests/API/conftest.py:cpe_api_24_config",
        message="selected 2.4 mgmt host for reset",
        data={"selected_reset_host": CPE_24_MGMT_API_HOST},
    )
    # #endregion

    if config.fetch_bts_via_ssh and not _bts_on_cli(request):
        print_section("Preflight — fetch BTS SSID/key for btsconnect POST")
        bts = await fetch_bts_credentials_for_api(
            bts_host=bsu_ip,
            cpe_mgmt_host=config.cpe_ssh_host,
            username=device_creds["user"],
            password=device_creds["pass"],
            radio_idx=1,
        )
        # #region agent log
        _debug_log(
            run_id="run3",
            hypothesis_id="H6",
            location="tests/API/conftest.py:cpe_api_24_config",
            message="fetched BTS creds for API",
            data={"ssid": bts.ssid, "password_len": len(bts.password or ""), "radio_idx": 1},
        )
        # #endregion
        config = replace(config, bts_ssid=bts.ssid, bts_password=bts.password)

    config = merge_cli_overrides(config, request)
    require_cpe_api_24_cli(config)
    return config


@pytest.fixture(scope="session")
async def cpe_24_mgmt_wifi_ready(cpe_api_24_config, request, bsu_ip, device_creds):
    if request.config.getoption("--skip-cpe-api-24"):
        pytest.skip("CPE API tests skipped (--skip-cpe-api-24).")

    join = await ensure_pc_on_cpe_mgmt_wifi(
        cpe_api_24_config,
        wifi_interface=cpe_api_24_config.wifi_interface,
        auto_join_wifi=request.config.getoption("--cpe-24-auto-join-wifi"),
        bts_host=bsu_ip if cpe_api_24_config.fetch_bts_via_ssh else None,
        bts_username=device_creds["user"],
        bts_password=device_creds["pass"],
    )
    yield join

    if join.method == "nmcli" and not request.config.getoption("--cpe-24-wifi-leave-connected"):
        await disconnect_pc_cpe_wifi(
            cpe_api_24_config.wifi_connection_name,
            wifi_interface=join.interface,
        )


@pytest.fixture(scope="session")
async def cpe_api_24_client(cpe_api_24_config, cpe_24_mgmt_wifi_ready, request):
    if request.config.getoption("--skip-cpe-api-24"):
        pytest.skip("CPE API tests skipped (--skip-cpe-api-24).")

    if not cpe_24_mgmt_wifi_ready.connected:
        pytest.fail("Connect PC to CPE 2.4 GHz mgmt Wi‑Fi manually, then re-run.")

    print_kv_block(
        "API run",
        {
            "PC connected SSID": cpe_24_mgmt_wifi_ready.ssid or "KWDEJPOQ",
            "CPE mgmt SSID (required)": cpe_api_24_config.cpe_mgmt_ssid or "KWDEJPOQ",
            "BTS btsconnect SSID (CPE backhaul only)": cpe_api_24_config.bts_ssid,
            "CPE API": cpe_api_24_config.base_url,
        },
    )

    client = CpeApi24Client(cpe_api_24_config)
    # Factory reset only in API_01 / API_02 flows — not for API_04+.
    return client
