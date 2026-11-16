"""Radio24Scan suite — scan + CPE mgmt Wi‑Fi API (NOT Functional mgmt-VLAN bootstrap)."""

from __future__ import annotations

import asyncio

import pytest

from utils.radio24_lab_net import ensure_radio24_lab_ipv6_routes


def pytest_configure(config):
    # Same method as API suite: no Functional testbed/mgmt-VLAN bootstrap.
    if hasattr(config.option, "skip_testbed_bootstrap"):
        config.option.skip_testbed_bootstrap = True
    if hasattr(config.option, "cpe_24_auto_join_wifi"):
        config.option.cpe_24_auto_join_wifi = True


def pytest_collection_modifyitems(config, items):
    del items
    if hasattr(config.option, "skip_testbed_bootstrap"):
        config.option.skip_testbed_bootstrap = True


@pytest.fixture(scope="session", autouse=True)
async def _radio24_lab_preflight(profile_bundle):
    """Wi‑Fi radio + lan24 DHCP only — never touches Functional VLAN 101 / BTS mgmt VLAN."""
    await asyncio.to_thread(ensure_radio24_lab_ipv6_routes, profile_bundle)
    try:
        from utils.radio24_scan_flows import _set_cpe_lan24_dhcp_server
        from config.defaults import RADIO_24_SCAN_DEFAULTS

        ssid = str(RADIO_24_SCAN_DEFAULTS.get("CPE_MGMT_HIDDEN_SSID") or "KWDEJPOQ")
        await _set_cpe_lan24_dhcp_server(profile_bundle, True, mgmt_ssid=ssid)
    except Exception as exc:
        print(f"[radio24] lan24 DHCP preflight skipped: {exc}")


@pytest.fixture(scope="session")
async def testbed_ready(request, profile_bundle, device_creds):
    """Radio24: skip Functional bootstrap (mgmt VLAN / VLAN modes / discovery)."""
    del request, device_creds
    print(
        "\n[radio24] Skipping Functional testbed bootstrap "
        "(no mgmt VLAN 101 on BTS/PC LAN — 2.4 GHz mgmt Wi‑Fi + factory path)"
    )
    return profile_bundle
