"""IP_01–IP_60 networking validation (BTS & CPE).

Enabled cases: config.ip_test_cases.ACTIVE_IP_CASE_IDS (all implemented IPv4 + IPv6 except IP_29).
IP_15/IP_34 restore backup always runs (enable_ip15_factory_restore forced True).
IP_27, IP_28–IP_34, IP_35–IP_36: test_ip_extended_case (parametrized).
IP_37–IP_60: catalog only — not collected.

Run: pytest tests/IP/ -m IP --allow-ip-suite ...
"""

from __future__ import annotations

import pytest

from config.ip_test_cases import ACTIVE_IP_CASE_IDS, is_active_ip_case
from utils.ip_validation_flows import case_needs_gui, run_ip_validation

pytestmark = [pytest.mark.IP, pytest.mark.asyncio(loop_scope="session")]


def _skip_unless_active(case_id: str):
    return pytest.mark.skipif(
        not is_active_ip_case(case_id),
        reason=f"{case_id} not in ACTIVE_IP_CASE_IDS (handler exists but disabled in suite)",
    )


# --- IP_01–IP_04 functional (BTS) ---


@_skip_unless_active("IP_01")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_01
async def test_ip_01_static_ipv4_bts(run_ip):
    await run_ip("IP_01", "bts")


@_skip_unless_active("IP_02")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_02
async def test_ip_02_ipv4_ping_local_bts(run_ip):
    await run_ip("IP_02", "bts")


@_skip_unless_active("IP_03")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_03
async def test_ip_03_ipv4_ping_remote_bts(run_ip):
    await run_ip("IP_03", "bts")


@_skip_unless_active("IP_04")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_04
async def test_ip_04_ipv4_gateway_bts(run_ip):
    await run_ip("IP_04", "bts")


# --- IP_05–IP_08 validation (BTS + CPE) ---


@_skip_unless_active("IP_05")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_05
async def test_ip_05_ipv4_throughput_bts(run_ip):
    await run_ip("IP_05", "bts")


@_skip_unless_active("IP_06")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_06
async def test_ip_06_ipv4_long_ping_bts(run_ip):
    await run_ip("IP_06", "bts")


@_skip_unless_active("IP_06")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_06
async def test_ip_06_ipv4_long_ping_cpe(run_ip):
    await run_ip("IP_06", "cpe")


@_skip_unless_active("IP_07")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_07
async def test_ip_07_ipv4_mtu_bts(run_ip):
    await run_ip("IP_07", "bts")


@_skip_unless_active("IP_07")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_07
async def test_ip_07_ipv4_mtu_cpe(run_ip):
    await run_ip("IP_07", "cpe")


@_skip_unless_active("IP_08")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_08
async def test_ip_08_ipv4_fragmentation_bts(run_ip):
    await run_ip("IP_08", "bts")


@_skip_unless_active("IP_08")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_08
async def test_ip_08_ipv4_fragmentation_cpe(run_ip):
    await run_ip("IP_08", "cpe")


# --- IP_09–IP_15 negative / destructive (BTS + CPE) ---


@_skip_unless_active("IP_09")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_09
async def test_ip_09_soft_reboot_bts(run_ip):
    await run_ip("IP_09", "bts")


@_skip_unless_active("IP_09")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_09
async def test_ip_09_soft_reboot_cpe(run_ip):
    await run_ip("IP_09", "cpe")


# IP_10 — Hard Reboot (manual PDU); handler skips; not in ACTIVE_IP_CASE_IDS
# @_skip_unless_active("IP_10")
# @pytest.mark.asyncio(scope="session")
# @pytest.mark.IP
# @pytest.mark.IP_10
# async def test_ip_10_hard_reboot_bts(run_ip):
#     await run_ip("IP_10", "bts")
#
# @_skip_unless_active("IP_10")
# @pytest.mark.asyncio(scope="session")
# @pytest.mark.IP
# @pytest.mark.IP_10
# async def test_ip_10_hard_reboot_cpe(run_ip):
#     await run_ip("IP_10", "cpe")


@_skip_unless_active("IP_11")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_11
async def test_ip_11_soft_reset_bts(run_ip):
    await run_ip("IP_11", "bts")


@_skip_unless_active("IP_11")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_11
async def test_ip_11_soft_reset_cpe(run_ip):
    await run_ip("IP_11", "cpe")


@_skip_unless_active("IP_12")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_12
async def test_ip_12_reset_retain_bts(run_ip):
    await run_ip("IP_12", "bts")


@_skip_unless_active("IP_12")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_12
async def test_ip_12_reset_retain_cpe(run_ip):
    await run_ip("IP_12", "cpe")


@_skip_unless_active("IP_13")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_13
async def test_ip_13_reboot_during_traffic_bts(run_ip):
    await run_ip("IP_13", "bts")


@_skip_unless_active("IP_13")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_13
async def test_ip_13_reboot_during_traffic_cpe(run_ip):
    await run_ip("IP_13", "cpe")


@_skip_unless_active("IP_14")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_14
async def test_ip_14_interface_flap_bts(run_ip):
    await run_ip("IP_14", "bts")


@_skip_unless_active("IP_14")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_14
async def test_ip_14_interface_flap_cpe(run_ip):
    await run_ip("IP_14", "cpe")


@_skip_unless_active("IP_15")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_15
async def test_ip_15_restore_backup_bts(run_ip):
    await run_ip("IP_15", "bts")


@_skip_unless_active("IP_15")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_15
async def test_ip_15_restore_backup_cpe(run_ip):
    await run_ip("IP_15", "cpe")


# --- IP_16–IP_17 validation (BTS + CPE) ---


@_skip_unless_active("IP_16")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_16
async def test_ip_16_arp_resolution_bts(run_ip):
    await run_ip("IP_16", "bts")


@_skip_unless_active("IP_16")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_16
async def test_ip_16_arp_resolution_cpe(run_ip):
    await run_ip("IP_16", "cpe")


@_skip_unless_active("IP_17")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_17
async def test_ip_17_static_route_bts(run_ip):
    await run_ip("IP_17", "bts")


@_skip_unless_active("IP_17")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_17
async def test_ip_17_static_route_cpe(run_ip):
    await run_ip("IP_17", "cpe")


# --- IP_18–IP_26 IPv6 functional / validation (BTS + CPE) ---


@_skip_unless_active("IP_18")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_18
async def test_ip_18_static_ipv6_bts(run_ip):
    await run_ip("IP_18", "bts")


@_skip_unless_active("IP_18")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_18
async def test_ip_18_static_ipv6_cpe(run_ip):
    await run_ip("IP_18", "cpe")


@_skip_unless_active("IP_19")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_19
async def test_ip_19_ipv6_ping_local_bts(run_ip):
    await run_ip("IP_19", "bts")


@_skip_unless_active("IP_19")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_19
async def test_ip_19_ipv6_ping_local_cpe(run_ip):
    await run_ip("IP_19", "cpe")


@_skip_unless_active("IP_20")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_20
async def test_ip_20_ipv6_ping_remote_bts(run_ip):
    await run_ip("IP_20", "bts")


@_skip_unless_active("IP_20")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_20
async def test_ip_20_ipv6_ping_remote_cpe(run_ip):
    await run_ip("IP_20", "cpe")


@_skip_unless_active("IP_21")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_21
async def test_ip_21_ipv6_gateway_bts(run_ip):
    await run_ip("IP_21", "bts")


@_skip_unless_active("IP_21")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_21
async def test_ip_21_ipv6_gateway_cpe(run_ip):
    await run_ip("IP_21", "cpe")


@_skip_unless_active("IP_22")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_22
async def test_ip_22_ipv6_throughput_bts(run_ip):
    await run_ip("IP_22", "bts")


@_skip_unless_active("IP_22")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_22
async def test_ip_22_ipv6_throughput_cpe(run_ip):
    await run_ip("IP_22", "cpe")


@_skip_unless_active("IP_23")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_23
async def test_ip_23_ipv6_long_ping_bts(run_ip):
    await run_ip("IP_23", "bts")


@_skip_unless_active("IP_23")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_23
async def test_ip_23_ipv6_long_ping_cpe(run_ip):
    await run_ip("IP_23", "cpe")


@_skip_unless_active("IP_24")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_24
async def test_ip_24_ipv6_mtu_bts(run_ip):
    await run_ip("IP_24", "bts")


@_skip_unless_active("IP_24")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_24
async def test_ip_24_ipv6_mtu_cpe(run_ip):
    await run_ip("IP_24", "cpe")


@_skip_unless_active("IP_25")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_25
async def test_ip_25_ipv6_fragmentation_bts(run_ip):
    await run_ip("IP_25", "bts")


@_skip_unless_active("IP_25")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_25
async def test_ip_25_ipv6_fragmentation_cpe(run_ip):
    await run_ip("IP_25", "cpe")


@_skip_unless_active("IP_26")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_26
async def test_ip_26_ipv6_link_local_bts(run_ip):
    await run_ip("IP_26", "bts")


@_skip_unless_active("IP_26")
@pytest.mark.asyncio(scope="session")
@pytest.mark.IP
@pytest.mark.IP_26
async def test_ip_26_ipv6_link_local_cpe(run_ip):
    await run_ip("IP_26", "cpe")


# --- IP_27–IP_36 extended (parametrized): IP_27, IP_28–IP_34, IP_35–IP_36 ---
# IP_29 (hard reboot) and IP_37–IP_60 are not in ACTIVE_IP_CASE_IDS


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
    if case.case_id not in ACTIVE_IP_CASE_IDS:
        pytest.skip(f"{case.case_id} not in ACTIVE_IP_CASE_IDS")
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


# --- IP_37–IP_60: planned isolation / multicast (no pytest collection yet) ---
