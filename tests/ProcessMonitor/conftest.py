"""Process monitor suite fixtures."""

import pytest

from utils.process_monitor_flows import (
    assert_process_monitor_preflight,
    _disarm_recovery_reboot,
    instant_recover_dut_if_armed,
    recovery_reboot_may_be_armed,
)


@pytest.fixture(scope="session", autouse=True)
async def process_monitor_preflight(request, root_ssh, gui_page):
    """Run GUI + SSH preflight before any PROCESS_* test when the suite is enabled."""
    if not request.config.getoption("--allow-process-monitor"):
        yield
        return
    await assert_process_monitor_preflight(root_ssh, gui_page)
    yield
    if request.config.getoption("--no-procmon-recovery-reboot"):
        return
    if recovery_reboot_may_be_armed():
        print(
            "[PROC][PROC_SESSION] Recovery still armed after suite "
            "(instant recovery may have already run).",
        )
        return
    try:
        await _disarm_recovery_reboot(root_ssh, case_id="PROC_SESSION")
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def procmon_instant_recovery_between_tests(request, bsu_ip, device_creds):
    """After each PROCESS_* test, reboot only if ping/SSH down; else disarm dead-man."""
    yield
    if not request.config.getoption("--allow-process-monitor"):
        return
    if request.config.getoption("--no-procmon-recovery-reboot"):
        return
    await instant_recover_dut_if_armed(
        bsu_ip,
        device_creds["pass"],
        case_id="PROC_RECOVER",
    )
