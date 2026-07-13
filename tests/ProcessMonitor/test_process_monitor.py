"""Process monitor suite (PROCESS_01–PROCESS_19) — local standalone DUT."""

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
async def test_process_01_visibility(request, procmon_ssh, bsu_ip, device_creds, gui_page):
    _require_process_monitor(request)
    await assert_process_01_visibility(
        procmon_ssh.conn,
        host=bsu_ip,
        password=device_creds["pass"],
        gui_page=gui_page,
    )


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_02
@pytest.mark.ProcessMonitor
async def test_process_02_uptime(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_02_uptime(
        procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"]
    )


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_03
@pytest.mark.ProcessMonitor
async def test_process_03_restart_count(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_03_restart_count(
        procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"]
    )


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_04
@pytest.mark.ProcessMonitor
async def test_process_04_timestamp(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_04_timestamp(
        procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"]
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_05
@pytest.mark.ProcessMonitor
async def test_process_05_crash_single(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_05_crash_single(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_06
@pytest.mark.ProcessMonitor
async def test_process_06_crash_multiple(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_06_crash_multiple(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_07
@pytest.mark.ProcessMonitor
async def test_process_07_crash_critical(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_07_crash_critical(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_08
@pytest.mark.ProcessMonitor
async def test_process_08_crash_non_critical(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_08_crash_non_critical(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_10
@pytest.mark.ProcessMonitor
async def test_process_10_restart_logging(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_10_restart_logging(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(10)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_11
@pytest.mark.ProcessMonitor
async def test_process_11_stress_under_load(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_11_stress_under_load(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(11)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_12
@pytest.mark.ProcessMonitor
async def test_process_12_log_integrity(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_12_log_integrity(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(12)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_13
@pytest.mark.ProcessMonitor
async def test_process_13_unauthorized_kill(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_13_unauthorized_kill_bts(
        procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"]
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_14
@pytest.mark.ProcessMonitor
async def test_process_14_dependency_handling(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_14_dependency_handling(
        procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"]
    )


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_16
@pytest.mark.ProcessMonitor
async def test_process_16_kill_single(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_16_kill_single(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(16)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_17
@pytest.mark.ProcessMonitor
async def test_process_17_kill_multiple(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_17_kill_multiple(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_18
@pytest.mark.ProcessMonitor
async def test_process_18_kill_critical(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_18_kill_critical(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_19
@pytest.mark.ProcessMonitor
async def test_process_19_kill_under_load(request, procmon_ssh, bsu_ip, device_creds):
    _require_process_monitor(request)
    await assert_process_19_kill_under_load(procmon_ssh.conn, host=bsu_ip, password=device_creds["pass"])


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_09
@pytest.mark.ProcessMonitor
async def test_process_09_watchdog_reboot(request, procmon_ssh, bsu_ip, device_creds):
    _require_destructive_process(request)
    await assert_process_09_watchdog_reboot(
        procmon_ssh.conn,
        host=bsu_ip,
        password=device_creds["pass"],
    )


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.PROCESS_15
@pytest.mark.ProcessMonitor
async def test_process_15_monitor_recovery(request, procmon_ssh, bsu_ip, device_creds):
    _require_destructive_process(request)
    await assert_process_15_monitor_recovery(
        procmon_ssh.conn,
        host=bsu_ip,
        password=device_creds["pass"],
    )
