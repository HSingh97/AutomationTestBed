"""Process monitor suite fixtures."""

from pathlib import Path

import pytest

from utils.process_monitor_flows import (
    assert_process_monitor_preflight,
    bind_procmon_ssh,
    _disarm_recovery_reboot,
    non_respawning_services_summary,
    recovery_reboot_may_be_armed,
)

# ProcessMonitor never uses the WAN/factory 10.0.0.1 path; the BTS is reached
# only on the LAN-side mgmt IP. Pin it here so the suite-local fixtures always
# target 192.168.2.1, regardless of profile defaults or --fallback-ip.
PROCMON_BTS_IP = "192.168.2.1"


@pytest.fixture(scope="session")
def bsu_ip(request, testbed_ready):  # noqa: ARG001  (matches root conftest signature)
    """Override root conftest's bsu_ip for the ProcessMonitor suite (LAN-only)."""
    return PROCMON_BTS_IP


@pytest.fixture(scope="session")
async def root_ssh(request, bsu_ip, device_creds, recovery_manager):
    """SSH to BTS exclusively over 192.168.2.1 for the ProcessMonitor suite."""
    from utils.ip_test_flows import _wait_ssh_any
    from utils.link_ssid import ensure_bts_link_ssid_ssh

    artifacts_dir = Path("reports/artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    profile = recovery_manager.profile_bundle.active
    print(f"[procmon] SSH pinned to {bsu_ip} (LAN mgmt); ignoring WAN/factory hosts")
    conn, effective = await _wait_ssh_any(
        [bsu_ip],
        device_creds["pass"],
        timeout_s=90,
        interval_s=5,
        mtu_recovery_profile=profile,
    )
    if effective != bsu_ip:
        print(f"[procmon] root_ssh active on {effective} (requested {bsu_ip})")

    await ensure_bts_link_ssid_ssh(
        conn,
        profile=profile,
        radio_idx=int(profile.get("link", {}).get("radio_idx", 1)),
    )
    try:
        yield conn
    finally:
        try:
            await conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
async def procmon_ssh(root_ssh):
    """Mutable session SSH shared across PROCESS_* tests (survives reconnect/reboot)."""
    return bind_procmon_ssh(root_ssh)


@pytest.fixture(scope="session", autouse=True)
async def process_monitor_preflight(request, procmon_ssh, gui_page):
    """Run GUI + SSH preflight before any PROCESS_* test when the suite is enabled."""
    if not request.config.getoption("--allow-process-monitor"):
        yield
        return
    await assert_process_monitor_preflight(procmon_ssh.conn, gui_page)
    yield
    summary = non_respawning_services_summary()
    if summary != "none":
        print(f"[PROC][PROC_SESSION] Session quirks (auto-skipped): {summary}")
    if request.config.getoption("--no-procmon-recovery-reboot"):
        return
    if recovery_reboot_may_be_armed():
        print(
            "[PROC][PROC_SESSION] Recovery still armed after suite "
            "(instant recovery may have already run).",
        )
        return
    try:
        await _disarm_recovery_reboot(procmon_ssh.conn, case_id="PROC_SESSION")
    except Exception:
        pass
