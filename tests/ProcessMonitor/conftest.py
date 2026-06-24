"""Process monitor suite fixtures."""

import pytest

from utils.process_monitor_flows import (
    assert_process_monitor_preflight,
    _disarm_recovery_reboot,
    non_respawning_services_summary,
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
    summary = non_respawning_services_summary()
    if summary != "none":
        print(f"[PROC][PROC_SESSION] Non-respawning this session (skipped in later cases): {summary}")
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
