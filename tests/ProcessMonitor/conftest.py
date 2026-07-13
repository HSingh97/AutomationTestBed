"""Process monitor suite fixtures."""

from pathlib import Path

import pytest

from utils.process_monitor_flows import (
    PROCMON_SSH_BACKUP,
    PROCMON_SSH_PREFERRED,
    assert_process_monitor_preflight,
    bind_procmon_ssh,
    _disarm_recovery_reboot,
    non_respawning_services_summary,
    procmon_ssh_candidates,
    recovery_reboot_may_be_armed,
)

# Prefer LAN mgmt; soft-fallback to WAN/factory. Unreachable candidates are skipped.
_PROCMON_EFFECTIVE_IP = PROCMON_SSH_PREFERRED


@pytest.fixture(scope="session")
async def root_ssh(request, testbed_ready, device_creds, recovery_manager):  # noqa: ARG001
    """SSH to BTS: try 192.168.2.1 first, then 10.0.0.1 if LAN is down."""
    global _PROCMON_EFFECTIVE_IP
    from utils.ip_test_flows import _wait_ssh_any
    from utils.link_ssid import ensure_bts_link_ssid_ssh

    artifacts_dir = Path("reports/artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    profile = recovery_manager.profile_bundle.active
    hosts = procmon_ssh_candidates()
    print(
        f"[procmon] SSH candidates (preferred={PROCMON_SSH_PREFERRED}, "
        f"backup={PROCMON_SSH_BACKUP}): {hosts}"
    )
    conn, effective = await _wait_ssh_any(
        hosts,
        device_creds["pass"],
        timeout_s=90,
        interval_s=5,
        mtu_recovery_profile=profile,
    )
    _PROCMON_EFFECTIVE_IP = effective
    if effective == PROCMON_SSH_PREFERRED:
        print(f"[procmon] root_ssh active on {effective} (LAN mgmt)")
    elif effective == PROCMON_SSH_BACKUP:
        print(f"[procmon] root_ssh active on {effective} (WAN/factory backup; LAN unreachable)")
    else:
        print(f"[procmon] root_ssh active on {effective}")

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
def bsu_ip(root_ssh):  # noqa: ARG001
    """BTS IP that ProcessMonitor actually connected to (LAN preferred, factory backup)."""
    return _PROCMON_EFFECTIVE_IP


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
