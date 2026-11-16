"""Link Test — BTS-AT-SW-01 (single BTS↔CPE link, GUI Link Test Tool).

Same model as WirelessSecurity / Functional:
  - No testbed bootstrap (do not mutate VLAN/link on session start)
  - Profile drives BTS + one CPE (`dut.local_ip`, `dut.remote_ips[0]`)
  - Preflight: RF link must be up (min 1 station on BTS)

Run with lab IPs (CLI defaults otherwise point at 192.168.2.230 / 10.0.0.1):
  pytest tests/LinkTest --profile ipv4_lab \\
    --local-ip 192.168.2.10 --remote-ip 192.168.2.11 --fallback-ip 192.168.2.10
"""

from __future__ import annotations

import asyncio
import time

import pytest

from utils.link_formation import link_health_bts
from utils.link_test_config import resolve_link_test_config
from utils.net_utils import normalize_ip


def pytest_collection_modifyitems(config, items):
    del items
    config.option.skip_testbed_bootstrap = True


@pytest.fixture(scope="session")
async def testbed_ready(request, profile_bundle, device_creds):
    """LinkTest: never run bootstrap — use the link already up on the bench."""
    del request, device_creds
    print("\n[link_test] Skipping testbed bootstrap — single BTS↔CPE link (Link Test Tool suite)")
    return profile_bundle


@pytest.fixture(scope="session")
def link_test_config(request, profile_bundle):
    return resolve_link_test_config(profile_bundle.active, request)


@pytest.fixture(scope="session")
def primary_cpe_ip(cpe_ips) -> str:
    """Single CPE on this bench — first entry from profile / --remote-ip."""
    if not cpe_ips:
        pytest.fail("[link_test] one CPE IP required (profile dut.remote_ips or --remote-ip)")
    if len(cpe_ips) > 1:
        print(f"[link_test] {len(cpe_ips)} CPE IPs in profile; using primary {cpe_ips[0]}")
    return cpe_ips[0]


@pytest.fixture(scope="session")
async def link_test_ready(root_ssh, profile_bundle, bsu_ip, primary_cpe_ip):
    """Preflight: BTS↔CPE RF link is up before any Link Test GUI case."""
    profile = profile_bundle.active
    link_cfg = profile.get("link") or {}
    timeout_s = float(link_cfg.get("health_check_timeout_s") or 120)
    min_clients = int(link_cfg.get("min_connected_clients") or 1)

    # Prefer live SSH / profile lab IP over stale CLI default (192.168.2.230).
    ssh_host = str(getattr(root_ssh, "host", "") or "").strip()
    profile_bts = normalize_ip(str((profile.get("dut") or {}).get("local_ip") or ""))
    effective_bts = normalize_ip(ssh_host) or profile_bts or normalize_ip(bsu_ip)

    ok, stations = await link_health_bts(root_ssh, profile)
    if not ok:
        print(f"[link_test] link not up yet (stations={stations}); waiting up to {timeout_s:.0f}s…")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            ok, stations = await link_health_bts(root_ssh, profile)
            if ok:
                break

    if not ok:
        pytest.fail(
            f"[link_test] BTS↔CPE link not up (need >={min_clients} station(s), got {stations}). "
            f"BTS={effective_bts} CPE={primary_cpe_ip}"
        )

    if effective_bts != normalize_ip(bsu_ip):
        print(f"[link_test] using effective BTS IP {effective_bts} (fixture bsu_ip was {bsu_ip})")
    print(f"[link_test] Link OK: BTS {effective_bts} ↔ CPE {primary_cpe_ip} ({stations} station(s))")
    return {
        "bts_ip": effective_bts,
        "cpe_ip": primary_cpe_ip,
        "stations": stations,
    }
