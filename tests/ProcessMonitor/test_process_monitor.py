"""Process monitor suite (PROCESS_01–PROCESS_20) — local standalone DUT."""

import pytest

from utils.process_monitor_flows import (
    assert_process_01_visibility,
    assert_process_02_uptime,
    assert_process_03_restart_count,
    assert_process_04_timestamp,
    assert_process_05_crash_single,
    assert_process_06_crash_multiple,
    assert_process_07_crash_critical,
    assert_process_08_crash_non_critical,
    assert_process_09_watchdog_reboot,
    assert_process_10_restart_logging,
    assert_process_11_stress_under_load,
    assert_process_12_log_integrity,
    assert_process_13_unauthorized_kill_bts,
    assert_process_14_dependency_handling,
    assert_process_15_monitor_recovery,
    assert_process_16_kill_single,
    assert_process_17_kill_multiple,
    assert_process_18_kill_critical,
    assert_process_19_kill_under_load,
    assert_process_20_unauthorized_kill_cpe,
)

pytestmark = [pytest.mark.sanity, pytest.mark.ProcessMonitor]


def _require_process_monitor(request):
    if not request.config.getoption("--allow-process-monitor"):
        pytest.skip(
            "Process monitor tests skipped. Re-run with --allow-process-monitor "
            "(induces process crashes/kills on the local DUT)."
        )


def _require_destructive_process(request):
    _require_process_monitor(request)
    if not request.config.getoption("--allow-destructive-process"):
        pytest.skip(
            "Destructive process monitor tests skipped. Re-run with "
            "--allow-process-monitor --allow-destructive-process "
            "(PROCESS_09/15 reboot the device)."
        )


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_01
@pytest.mark.ProcessMonitor
async def test_process_01_visibility(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_01_visibility(root_ssh)


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_02
@pytest.mark.ProcessMonitor
async def test_process_02_uptime(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_02_uptime(root_ssh)


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_03
@pytest.mark.ProcessMonitor
async def test_process_03_restart_count(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_03_restart_count(root_ssh)


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_04
@pytest.mark.ProcessMonitor
async def test_process_04_timestamp(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_04_timestamp(root_ssh)


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_05
@pytest.mark.ProcessMonitor
async def test_process_05_crash_single(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_05_crash_single(root_ssh)


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_06
@pytest.mark.ProcessMonitor
async def test_process_06_crash_multiple(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_06_crash_multiple(root_ssh)


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_07
@pytest.mark.ProcessMonitor
async def test_process_07_crash_critical(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_07_crash_critical(root_ssh)


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_08
@pytest.mark.ProcessMonitor
async def test_process_08_crash_non_critical(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_08_crash_non_critical(root_ssh)


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_10
@pytest.mark.ProcessMonitor
async def test_process_10_restart_logging(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_10_restart_logging(root_ssh)


@pytest.mark.order(10)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_11
@pytest.mark.ProcessMonitor
async def test_process_11_stress_under_load(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_11_stress_under_load(root_ssh)


@pytest.mark.order(11)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_12
@pytest.mark.ProcessMonitor
async def test_process_12_log_integrity(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_12_log_integrity(root_ssh)


@pytest.mark.order(12)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_13
@pytest.mark.ProcessMonitor
async def test_process_13_unauthorized_kill(request, root_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_13_unauthorized_kill_bts(
        root_ssh,
        host=bsu_ip,
        password=device_creds["pass"],
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_16
@pytest.mark.ProcessMonitor
async def test_process_16_kill_single(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_16_kill_single(root_ssh)


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_17
@pytest.mark.ProcessMonitor
async def test_process_17_kill_multiple(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_17_kill_multiple(root_ssh)


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_18
@pytest.mark.ProcessMonitor
async def test_process_18_kill_critical(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_18_kill_critical(root_ssh)


@pytest.mark.order(16)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_19
@pytest.mark.ProcessMonitor
async def test_process_19_kill_under_load(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_19_kill_under_load(root_ssh)


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_20
@pytest.mark.ProcessMonitor
async def test_process_20_unauthorized_kill(request, root_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_20_unauthorized_kill_cpe(
        root_ssh,
        host=bsu_ip,
        password=device_creds["pass"],
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_14
@pytest.mark.ProcessMonitor
async def test_process_14_dependency_handling(request, root_ssh):
    _require_process_monitor(request)
    await assert_process_14_dependency_handling(root_ssh)


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_09
@pytest.mark.ProcessMonitor
async def test_process_09_watchdog_reboot(request, root_ssh, bsu_ip, device_creds):
    _require_destructive_process(request)
    await assert_process_09_watchdog_reboot(
        root_ssh,
        host=bsu_ip,
        password=device_creds["pass"],
    )


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_15
@pytest.mark.ProcessMonitor
async def test_process_15_monitor_recovery(request, root_ssh, bsu_ip, device_creds):
    _require_destructive_process(request)
    await assert_process_15_monitor_recovery(
        root_ssh,
        host=bsu_ip,
        password=device_creds["pass"],
    )
