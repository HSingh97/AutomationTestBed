"""
CPE API — PC auto-joins CPE hidden mgmt Wi‑Fi KWDEJPOQ (169.254.254.1); BTS creds for link restore only.
"""

from __future__ import annotations

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

def _bts_on_cli(request) -> bool:
    ssid = (request.config.getoption("--cpe-24-bts-ssid") or "").strip()
    pw = request.config.getoption("--cpe-24-bts-password")
    return bool(ssid) and pw is not None and str(pw) != ""

def pytest_collection_modifyitems(config, items):
    """API suite: PC joins CPE 2.4 GHz mgmt Wi‑Fi — no mgmt VLAN / testbed bootstrap."""
    if any(item.get_closest_marker("CPE_API_24") for item in items):
        config.option.skip_testbed_bootstrap = True

@pytest.fixture(scope="session")
async def testbed_ready(profile_bundle):
    print("\n[api] Skipping testbed bootstrap (no mgmt VLAN — CPE 2.4 GHz mgmt Wi‑Fi only)")
    return profile_bundle

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

    if config.fetch_bts_via_ssh and not _bts_on_cli(request):
        print_section("Preflight — fetch BTS SSID/key for btsconnect POST")
        bts = await fetch_bts_credentials_for_api(
            bts_host=bsu_ip,
            cpe_mgmt_host=config.cpe_ssh_host,
            username=device_creds["user"],
            password=device_creds["pass"],
            radio_idx=1,
        )
        config = replace(config, bts_ssid=bts.ssid, bts_password=bts.password)

    config = merge_cli_overrides(config, request)
    require_cpe_api_24_cli(config)
    return config

@pytest.fixture(scope="session")
async def cpe_24_mgmt_wifi_ready(cpe_api_24_config, request, bsu_ip, device_creds, profile_bundle):
    if request.config.getoption("--skip-cpe-api-24"):
        pytest.skip("CPE API tests skipped (--skip-cpe-api-24).")

    import asyncio

    from utils.pdu_power import pdu_enabled, pdu_hard_reboot

    async def _join() -> object:
        return await ensure_pc_on_cpe_mgmt_wifi(
            cpe_api_24_config,
            wifi_interface=cpe_api_24_config.wifi_interface,
            auto_join_wifi=request.config.getoption("--cpe-24-auto-join-wifi"),
            bts_host=bsu_ip if cpe_api_24_config.fetch_bts_via_ssh else None,
            bts_username=device_creds["user"],
            bts_password=device_creds["pass"],
        )

    try:
        join = await _join()
    except RuntimeError as exc:
        if not pdu_enabled(profile_bundle.active):
            raise
        print(
            f"\n[api] CPE mgmt Wi‑Fi join failed ({exc}). "
            "PDU power-cycle CPE, then retry join..."
        )
        await pdu_hard_reboot(profile_bundle.active, device_target="cpe")
        await asyncio.sleep(120)
        join = await _join()
    yield join

    if join.method == "nmcli" and not request.config.getoption("--cpe-24-wifi-leave-connected"):
        await disconnect_pc_cpe_wifi(
            join.ssid or cpe_api_24_config.wifi_connection_name,
            wifi_interface=join.interface,
        )

@pytest.fixture(scope="session")
async def cpe_api_24_client(cpe_api_24_config, cpe_24_mgmt_wifi_ready, request):
    if request.config.getoption("--skip-cpe-api-24"):
        pytest.skip("CPE API tests skipped (--skip-cpe-api-24).")

    if not cpe_24_mgmt_wifi_ready.connected:
        pytest.fail("Connect PC to CPE 2.4 GHz mgmt Wi‑Fi manually, then re-run.")

    config = cpe_api_24_config
    if cpe_24_mgmt_wifi_ready.ssid and cpe_24_mgmt_wifi_ready.ssid != config.cpe_mgmt_ssid:
        config = replace(
            config,
            cpe_mgmt_ssid=cpe_24_mgmt_wifi_ready.ssid,
            cpe_mgmt_password=(
                cpe_24_mgmt_wifi_ready.mgmt_password
                if cpe_24_mgmt_wifi_ready.mgmt_password is not None
                else config.cpe_mgmt_password
            ),
            wifi_connection_name=cpe_24_mgmt_wifi_ready.ssid,
        )

    print_kv_block(
        "API run",
        {
            "PC connected SSID": cpe_24_mgmt_wifi_ready.ssid or "KWDEJPOQ",
            "CPE mgmt SSID (required)": config.cpe_mgmt_ssid or "KWDEJPOQ",
            "BTS btsconnect SSID (CPE backhaul only)": config.bts_ssid,
            "CPE API": config.base_url,
        },
    )

    client = CpeApi24Client(config)
    # Factory reset only in API_01 / API_02 flows — not for API_04+.
    return client
