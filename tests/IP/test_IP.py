"""IP_01–IP_37 networking validation (BTS & CPE).

Run the full suite: pytest tests/IP/ -m IP --allow-ip-suite ...
Run one case: pytest tests/IP/ -m IP_05 --allow-ip-suite ...
"""

from __future__ import annotations

import pytest

from utils.ip_validation_flows import case_needs_gui, run_ip_validation

pytestmark = [pytest.mark.IP, pytest.mark.asyncio(loop_scope="session")]


# --- IP_01–IP_04 functional (BTS) ---


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_01
async def test_ip_01_static_ipv4_bts(run_ip):
    await run_ip("IP_01", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_02
async def test_ip_02_ipv4_ping_local_bts(run_ip):
    await run_ip("IP_02", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_03
async def test_ip_03_ipv4_ping_remote_bts(run_ip):
    await run_ip("IP_03", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_04
async def test_ip_04_ipv4_gateway_bts(run_ip):
    await run_ip("IP_04", "bts")


# --- IP_05–IP_08 validation (BTS + CPE) ---


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_05
async def test_ip_05_ipv4_throughput_bts(run_ip):
    await run_ip("IP_05", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_06
async def test_ip_06_ipv4_long_ping_bts(run_ip):
    await run_ip("IP_06", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_06
async def test_ip_06_ipv4_long_ping_cpe(run_ip):
    await run_ip("IP_06", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_07
async def test_ip_07_ipv4_mtu_bts(run_ip):
    await run_ip("IP_07", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_07
async def test_ip_07_ipv4_mtu_cpe(run_ip):
    await run_ip("IP_07", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_08
async def test_ip_08_ipv4_fragmentation_bts(run_ip):
    await run_ip("IP_08", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_08
async def test_ip_08_ipv4_fragmentation_cpe(run_ip):
    await run_ip("IP_08", "cpe")


# --- IP_09–IP_15 negative / destructive (BTS + CPE) ---


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_09
async def test_ip_09_soft_reboot_bts(run_ip):
    await run_ip("IP_09", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_09
async def test_ip_09_soft_reboot_cpe(run_ip):
    await run_ip("IP_09", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_10
async def test_ip_10_hard_reboot_bts(run_ip):
    await run_ip("IP_10", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_10
async def test_ip_10_hard_reboot_cpe(run_ip):
    await run_ip("IP_10", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_11
async def test_ip_11_soft_reset_bts(run_ip):
    await run_ip("IP_11", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_11
async def test_ip_11_soft_reset_cpe(run_ip):
    await run_ip("IP_11", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_12
async def test_ip_12_reset_retain_bts(run_ip):
    await run_ip("IP_12", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_12
async def test_ip_12_reset_retain_cpe(run_ip):
    await run_ip("IP_12", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_13
async def test_ip_13_reboot_during_traffic_bts(run_ip):
    await run_ip("IP_13", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_13
async def test_ip_13_reboot_during_traffic_cpe(run_ip):
    await run_ip("IP_13", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_14
async def test_ip_14_interface_flap_bts(run_ip):
    await run_ip("IP_14", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_14
async def test_ip_14_interface_flap_cpe(run_ip):
    await run_ip("IP_14", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_15
async def test_ip_15_restore_backup_bts(run_ip):
    await run_ip("IP_15", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_15
async def test_ip_15_restore_backup_cpe(run_ip):
    await run_ip("IP_15", "cpe")


# --- IP_16–IP_17 validation (BTS + CPE) ---


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_16
async def test_ip_16_arp_resolution_bts(run_ip):
    await run_ip("IP_16", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_16
async def test_ip_16_arp_resolution_cpe(run_ip):
    await run_ip("IP_16", "cpe")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_17
async def test_ip_17_static_route_bts(run_ip):
    await run_ip("IP_17", "bts")


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_17
async def test_ip_17_static_route_cpe(run_ip):
    await run_ip("IP_17", "cpe")


# --- IP_18–IP_37 extended (IPv6 / dual-stack, parametrized) ---


@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
async def test_ip_extended_case(
    request,
    ip_extended_bundle,
    profile_bundle,
    bsu_ip,
    cpe_ips,
    device_creds,
    gui_page,
):
    case, target = ip_extended_bundle
    page = gui_page if case_needs_gui(case.case_id, target) else None
    request.node.add_marker(getattr(pytest.mark, case.case_id))
    await run_ip_validation(
        case.case_id,
        target,
        request=request,
        profile_bundle=profile_bundle,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        gui_page=page,
    )
