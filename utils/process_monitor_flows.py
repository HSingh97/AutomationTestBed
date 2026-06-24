"""Process monitor (procmon) validation flows — local standalone DUT only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import pytest
import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from config.process_monitor_catalog import (
    CRASH_TEST_SERVICE_TARGETS,
    MONITORED_SERVICES,
    MONITORED_SERVICE_NAMES,
    PREFLIGHT_COUNTER_EXEMPT,
    SERVICE_SWEEP_ORDER,
    SSH_RECONNECT_SERVICES,
    validate_monitored_services_catalog,
)
from pages.commands import RootCommands
from utils.parsers import ssh_scalar
from utils.regression_flows import _close_ssh, _wait_for_ssh
from utils.regression_validation import (
    _parse_uptime_seconds,
    _ssh_run,
    _uptime_stable_after_network_event,
)

CRASH_TARGET_DEFAULT = "dnsmasq"
CRASH_TARGET_SECONDARY = "snmpd"
CRASH_TARGET_TERTIARY = "odhcpd"
CRASH_TARGET_LOGGING = "ezmcloud"
CRASH_TARGET_STRESS = "mcsd"
CRASH_TARGET_LOG_INTEGRITY = "netlinkevents"
CRASH_TARGET_KILL_SINGLE = "compass"
CRASH_TARGET_KILL_MULTI = ("senao-openapi-server", "netlinkevents")
CRASH_TARGET_KILL_LOAD = "kwn_devlocator"
CRITICAL_TARGET = "log"
CRITICAL_KILL_TARGET = "uhttpd"
NON_CRITICAL_TARGET = "snlog-scraper"
DEPENDENCY_PARENT = "network"
DEPENDENCY_CHILD = "dnsmasq"

# Services that may be idle on some firmware builds; not a hard visibility failure.
OPTIONAL_IDLE_SERVICES = frozenset({"sysstat", "cron", "breakpad"})
MUST_BE_RUNNING = frozenset(
    {"rpcd", "network", "dnsmasq", "sshd", "log", "uhttpd", "snmpd", "ntpd", "odhcpd"}
)

PROC_CORE_DIR = "/overlay/data/procmon/cores"
PROCESS_MONITOR_GUI_PATH = "/monitor/system_stats/process_monitoring"
RESPAWN_WAIT_S = 5
CORE_FILE_WAIT_S = 6
NETIFD_RECOVERY_S = 25
SSH_RECONNECT_WAIT_S = 90
POLL_INTERVAL_S = 0.35
KillSignal = Literal["SEGV", "KILL"]
_SHELL_JOB_NOISE = re.compile(r"^\[\d+\]\+|^Done\(")

# Dead-man backup only — unreachable DUT gets an immediate reboot from automation.
RECOVERY_REBOOT_DELAY_S = 35
RECOVERY_REBOOT_HELLO = "PROC_MON_HELLO"
RECOVERY_REBOOT_HEARTBEAT_S = 3
PROC_REBOOT_UP_TIMEOUT_S = 90
PROC_REBOOT_DOWN_POLL_S = 10
_RECOVERY_CANCEL = "/tmp/procmon_recovery_cancel"
_RECOVERY_ARM_PID = "/tmp/procmon_recovery_arm.pid"
_RECOVERY_REBOOT_ENABLED = True
_RECOVERY_LEFT_ARMED = False
_LAST_RECOVERY_TRIGGER = ""
# Services observed once not to respawn after crash/kill — skip in all later cases this session.
_NON_RESPAWNING_SERVICES: dict[str, str] = {}
# Services whose ubus total_crashes counter does not increment after crash (e.g. sshd on some builds).
_COUNTER_QUIRK_SERVICES: dict[str, str] = {}
# Services observed not running before crash — skip in later cases instead of failing repeatedly.
_NOT_RUNNING_SERVICES: dict[str, str] = {}
_KNOWN_RESPAWN_QUIRK_SERVICES = frozenset({"ntpd", "rpcd"})
_PREPARE_SSH_TIMEOUT_S = 25
# Batch sweep tuning — parallel crash + single polling loop instead of per-service serial work.
_BATCH_SWEEP_ENABLED = True
_BATCH_POLL_INTERVAL_S = 0.4
# Floor for batch respawn deadline — many services restart simultaneously so procd needs slack.
_BATCH_RESPAWN_TIMEOUT_S = 18
# Single shared poll window if batch missed services before marking them non-respawning.
_BATCH_RECONFIRM_TIMEOUT_S = 6


@dataclass
class ProcmonSshHandle:
    """Mutable session SSH — updated when prepare/reconnect opens a new connection."""

    conn: AsyncGenericDriver


_PROCMON_SSH: ProcmonSshHandle | None = None


def bind_procmon_ssh(conn: AsyncGenericDriver) -> ProcmonSshHandle:
    """Attach ProcessMonitor flows to the session SSH handle (pytest fixture)."""
    global _PROCMON_SSH
    _PROCMON_SSH = ProcmonSshHandle(conn)
    return _PROCMON_SSH


def _resolve_ssh(ssh: AsyncGenericDriver) -> AsyncGenericDriver:
    if _PROCMON_SSH is not None:
        return _PROCMON_SSH.conn
    return ssh


async def _publish_ssh(ssh: AsyncGenericDriver) -> AsyncGenericDriver:
    global _PROCMON_SSH
    if _PROCMON_SSH is not None:
        old = _PROCMON_SSH.conn
        _PROCMON_SSH.conn = ssh
        if old is not ssh:
            try:
                await _close_ssh(old)
            except Exception:
                pass
    return ssh


class _ServiceCrashSkipped(Exception):
    """Optional/idle service did not respawn — skip remaining checks for this service."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        super().__init__(service_name)


class ServiceCrashFailure(Exception):
    """Per-service crash step failed; sweep may continue."""

    def __init__(self, service_name: str, reason: str) -> None:
        self.service_name = service_name
        self.reason = reason
        super().__init__(reason)


_SWEEP_CONTINUE_ON_FAILURE = False
_SWEEP_CRITICAL_ONLY_FAIL = False


@dataclass
class ServiceInstance:
    service: str
    instance: str
    running: bool
    pid: int | None
    respawn_count: int
    total_crashes: int
    last_respawn: int
    last_exit: int
    respawn_retry: int | None = None

    @classmethod
    def from_ubus(cls, service: str, instance: str, payload: dict[str, Any]) -> ServiceInstance:
        respawn = payload.get("respawn") or {}
        retry = respawn.get("retry")
        return cls(
            service=service,
            instance=instance,
            running=bool(payload.get("running")),
            pid=int(payload["pid"]) if payload.get("pid") else None,
            respawn_count=int(payload.get("respawn_count") or 0),
            total_crashes=int(payload.get("total_crashes") or 0),
            last_respawn=int(payload.get("last_respawn") or 0),
            last_exit=int(payload.get("last_exit") or 0),
            respawn_retry=int(retry) if retry is not None else None,
        )


@dataclass
class CrashResult:
    service_name: str
    after: ServiceInstance
    signal: KillSignal
    core_path: str | None = None

    @property
    def core_expected(self) -> bool:
        return self.signal == "SEGV"

    @property
    def core_saved(self) -> bool:
        return not self.core_expected or bool(self.core_path)


PROC_PARTIAL_MARKER = "[PROC_PARTIAL]"
PROC_FAILED_MARKER = "[PROC_FAILED]"


def _fail_case(case_id: str, reason: str) -> None:
    """Hard failure — entire case FAILED (not partial)."""
    _log(case_id, f"FAILED: {reason}")
    pytest.fail(f"{PROC_FAILED_MARKER} {case_id} FAILED: {reason}")


def _service_is_critical(service_name: str) -> bool:
    meta = MONITORED_SERVICES.get(service_name, {})
    return bool(meta.get("critical")) or service_name in MUST_BE_RUNNING


@contextlib.contextmanager
def _sweep_failure_policy(case_id: str):
    """Sweep cases record per-service PARTIALs; critical-only cases hard-fail on critical services."""
    global _SWEEP_CONTINUE_ON_FAILURE, _SWEEP_CRITICAL_ONLY_FAIL
    prev_continue = _SWEEP_CONTINUE_ON_FAILURE
    prev_critical = _SWEEP_CRITICAL_ONLY_FAIL
    _SWEEP_CONTINUE_ON_FAILURE = True
    _SWEEP_CRITICAL_ONLY_FAIL = case_id in {"PROCESS_07", "PROCESS_18"}
    try:
        yield
    finally:
        _SWEEP_CONTINUE_ON_FAILURE = prev_continue
        _SWEEP_CRITICAL_ONLY_FAIL = prev_critical


def _non_respawning_summary() -> str:
    parts: list[str] = []
    if _NON_RESPAWNING_SERVICES:
        parts.append(
            "no-respawn=["
            + ", ".join(f"{n} ({r})" for n, r in sorted(_NON_RESPAWNING_SERVICES.items()))
            + "]"
        )
    if _COUNTER_QUIRK_SERVICES:
        parts.append(
            "no-counter=["
            + ", ".join(f"{n} ({r})" for n, r in sorted(_COUNTER_QUIRK_SERVICES.items()))
            + "]"
        )
    if _NOT_RUNNING_SERVICES:
        parts.append(
            "not-running=["
            + ", ".join(f"{n} ({r})" for n, r in sorted(_NOT_RUNNING_SERVICES.items()))
            + "]"
        )
    return "; ".join(parts) if parts else "none"


def _note_non_respawning_service(case_id: str, service_name: str, reason: str) -> None:
    """Remember a service that failed to respawn; skip it in all subsequent cases."""
    if service_name in _NON_RESPAWNING_SERVICES:
        return
    _NON_RESPAWNING_SERVICES[service_name] = reason
    _log(
        case_id,
        f"SESSION: {service_name} does not respawn on this DUT — "
        f"skipping in all further cases ({reason})",
    )


def _note_counter_quirk_service(case_id: str, service_name: str, reason: str) -> None:
    """Remember a service whose total_crashes counter does not increment."""
    if service_name in _COUNTER_QUIRK_SERVICES:
        return
    _COUNTER_QUIRK_SERVICES[service_name] = reason
    _log(
        case_id,
        f"SESSION: {service_name} crash counter does not increment on this DUT — "
        f"skipping in all further cases ({reason})",
    )


def _note_not_running_service(case_id: str, service_name: str, reason: str) -> None:
    """Remember a service that was not running before crash so we don't keep retrying."""
    if service_name in _NOT_RUNNING_SERVICES:
        return
    _NOT_RUNNING_SERVICES[service_name] = reason
    _log(
        case_id,
        f"SESSION: {service_name} was not running on this DUT — "
        f"skipping crash attempts in all further cases ({reason})",
    )


def _is_known_quirk(service_name: str) -> bool:
    return (
        service_name in _NON_RESPAWNING_SERVICES
        or service_name in _COUNTER_QUIRK_SERVICES
        or service_name in _NOT_RUNNING_SERVICES
    )


async def prepare_procmon_case(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str,
) -> AsyncGenericDriver:
    """Reconnect SSH if needed and disarm dead-man recovery before a PROCESS_* case."""
    ssh = _resolve_ssh(ssh)
    if _NON_RESPAWNING_SERVICES:
        _log(case_id, f"SESSION non-respawning (skip): {_non_respawning_summary()}")
    await instant_recover_dut_if_armed(host, password, case_id=f"{case_id}_PRE")
    ping_ok, ping_detail = await _local_ping_host(host)
    if not ping_ok:
        _log(case_id, f"prepare: lab ping to {host} down ({ping_detail}) — recovery reboot")
        recovered = await _instant_reboot_and_wait(
            None,
            host,
            password,
            case_id=case_id,
            recovery_reason=f"prepare: lab ping to {host} not reachable before {case_id}",
        )
        if recovered is not None:
            return await _publish_ssh(recovered)
        raise ConnectionError(f"prepare: {host} unreachable after recovery reboot ({ping_detail})")
    try:
        return await _publish_ssh(await _ensure_live_ssh(ssh, host, password))
    except Exception as exc:
        _log(case_id, f"prepare: SSH not ready on {host} ({exc}) — recovery reboot")
        recovered = await _instant_reboot_and_wait(
            ssh,
            host,
            password,
            case_id=case_id,
            recovery_reason=f"prepare: SSH to {host} not ready before {case_id} ({exc})",
        )
        if recovered is not None:
            return await _publish_ssh(recovered)
        raise ConnectionError(f"prepare: SSH to {host} not restored after recovery reboot") from exc


def recovery_reboot_may_be_armed() -> bool:
    """True when a crash/kill case failed with recovery reboot still armed on the DUT."""
    return _RECOVERY_LEFT_ARMED


async def _local_ping_host(host: str, *, count: int = 1, timeout_s: int = 1) -> tuple[bool, str]:
    """Ping DUT from the lab host (not via SSH)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            str(count),
            "-W",
            str(timeout_s),
            host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s * count + 5)
        text = (stdout or b"").decode(errors="replace")
        ok = proc.returncode == 0 and "100% packet loss" not in text.lower()
        return ok, text.strip()[:200] or f"exit {proc.returncode}"
    except Exception as exc:
        return False, str(exc)[:200]


async def _ssh_alive_quick(ssh: AsyncGenericDriver | None) -> bool:
    if ssh is None:
        return False
    try:
        await ssh.send_command("echo ok", timeout_ops=5)
        return True
    except Exception:
        return False


def _format_recovery_reboot_context(
    failure_reason: str,
    *,
    host: str,
    ping_ok: bool,
    ping_detail: str,
    ssh_ok: bool,
) -> str:
    """Human-readable explanation for why the DUT is being rebooted."""
    reachability: list[str] = []
    if ping_ok:
        reachability.append(f"lab ping to {host} reachable")
    else:
        reachability.append(f"lab ping to {host} not reachable ({ping_detail})")
    if ssh_ok:
        reachability.append("SSH up")
    else:
        reachability.append("SSH down")
    return f"{failure_reason}; {'; '.join(reachability)}"


async def _instant_reboot_and_wait(
    ssh: AsyncGenericDriver | None,
    host: str,
    password: str,
    *,
    case_id: str,
    recovery_reason: str,
) -> AsyncGenericDriver | None:
    """Reboot DUT immediately and poll until lab ping + SSH are back."""
    global _RECOVERY_LEFT_ARMED
    _log(
        case_id,
        f"Recovery reboot: rebooting {host} because {recovery_reason}",
    )
    if ssh and await _ssh_alive_quick(ssh):
        try:
            await _ssh_run(ssh, "sync; reboot", timeout_ops=8)
            _log(case_id, f"Recovery reboot: reboot command sent to {host} over SSH.")
        except Exception as exc:
            _log(case_id, f"Recovery reboot: SSH reboot command failed ({exc}); waiting for device.")
        try:
            await _close_ssh(ssh)
        except Exception:
            pass
    else:
        _log(
            case_id,
            f"Recovery reboot: SSH unavailable on {host} — waiting for device to go down and come back.",
        )

    down_deadline = time.monotonic() + PROC_REBOOT_DOWN_POLL_S
    while time.monotonic() < down_deadline:
        ping_ok, _ = await _local_ping_host(host)
        if not ping_ok:
            _log(case_id, f"Recovery reboot: lab ping to {host} is down (device rebooting).")
            break
        await asyncio.sleep(POLL_INTERVAL_S)

    deadline = time.monotonic() + PROC_REBOOT_UP_TIMEOUT_S
    last_err = ""
    while time.monotonic() < deadline:
        ping_ok, ping_detail = await _local_ping_host(host)
        if ping_ok:
            try:
                conn = await _wait_for_ssh(host, password, timeout_s=10, interval_s=1)
                await _disarm_recovery_reboot(conn, case_id=case_id)
                _RECOVERY_LEFT_ARMED = False
                _log(
                    case_id,
                    f"Recovery reboot: device {host} back online (ping up, SSH restored) "
                    f"after reboot triggered because {recovery_reason}",
                )
                return conn
            except Exception as exc:
                last_err = str(exc)
        else:
            last_err = f"ping down ({ping_detail})"
        await asyncio.sleep(POLL_INTERVAL_S)
    _log(
        case_id,
        f"Recovery reboot: timed out after {PROC_REBOOT_UP_TIMEOUT_S}s waiting for {host} "
        f"({last_err}); originally rebooted because {recovery_reason}",
    )
    return None


async def instant_recover_dut_if_armed(
    host: str,
    password: str,
    *,
    case_id: str = "PROC_RECOVER",
) -> None:
    """Between tests: reboot only when ping or SSH is down; otherwise disarm dead-man."""
    if not _RECOVERY_REBOOT_ENABLED or not _RECOVERY_LEFT_ARMED:
        return
    ping_ok, ping_detail = await _local_ping_host(host)
    ssh: AsyncGenericDriver | None = None
    ssh_ok = False
    if ping_ok:
        try:
            ssh = await _wait_for_ssh(host, password, timeout_s=15, interval_s=1)
            ssh_ok = await _ssh_alive_quick(ssh)
        except Exception:
            ssh = None
    if ping_ok and ssh_ok and ssh is not None:
        trigger = _LAST_RECOVERY_TRIGGER or "prior test left recovery reboot armed"
        _log(
            case_id,
            f"No recovery reboot: {host} reachable (ping up, SSH up) after {trigger} — disarming dead-man only.",
        )
        await _disarm_recovery_reboot(ssh, case_id=case_id)
        return
    trigger = _LAST_RECOVERY_TRIGGER or "prior test left recovery reboot armed"
    recovery_reason = _format_recovery_reboot_context(
        trigger,
        host=host,
        ping_ok=ping_ok,
        ping_detail=ping_detail,
        ssh_ok=ssh_ok,
    )
    await _instant_reboot_and_wait(
        ssh,
        host,
        password,
        case_id=case_id,
        recovery_reason=recovery_reason,
    )


async def _fail_crash_case(
    ssh: AsyncGenericDriver | None,
    host: str,
    password: str,
    case_id: str,
    reason: str,
    *,
    service_name: str = "",
) -> None:
    """Fail a crash/kill step; reboot only when lab ping or SSH is down."""
    global _RECOVERY_LEFT_ARMED, _LAST_RECOVERY_TRIGGER
    ping_ok, ping_detail = await _local_ping_host(host)
    ssh_ok = await _ssh_alive_quick(ssh)
    _log(case_id, f"FAILED: {reason}")
    _log(
        case_id,
        f"Reachability at failure: lab ping={'UP' if ping_ok else 'DOWN'} ({ping_detail}); "
        f"ssh={'UP' if ssh_ok else 'down'}",
    )
    _LAST_RECOVERY_TRIGGER = reason
    if service_name:
        lowered = reason.lower()
        if "did not respawn" in lowered or "no crash recorded" in lowered:
            _note_non_respawning_service(case_id, service_name, reason)
        elif "crash counter did not increment" in lowered:
            _note_counter_quirk_service(case_id, service_name, reason)
        elif "not running" in lowered or "no pid" in lowered:
            _note_not_running_service(case_id, service_name, reason)

    # Do not reboot the whole DUT for a known per-service quirk while link is up.
    skip_dut_reboot = (
        ping_ok
        and ssh_ok
        and service_name
        and _is_known_quirk(service_name)
    )
    if skip_dut_reboot:
        _log(
            case_id,
            f"No recovery reboot: {service_name} already marked non-respawning this session.",
        )
        if ssh is not None:
            try:
                await _disarm_recovery_reboot(ssh, case_id=case_id)
            except Exception:
                pass
        _RECOVERY_LEFT_ARMED = False
    elif not ping_ok or not ssh_ok:
        _RECOVERY_LEFT_ARMED = True
        recovery_reason = _format_recovery_reboot_context(
            reason,
            host=host,
            ping_ok=ping_ok,
            ping_detail=ping_detail,
            ssh_ok=ssh_ok,
        )
        await _instant_reboot_and_wait(
            ssh,
            host,
            password,
            case_id=case_id,
            recovery_reason=recovery_reason,
        )
    elif not skip_dut_reboot:
        _log(
            case_id,
            f"No recovery reboot: {host} still reachable (ping up, SSH up) despite "
            f"{reason} — firmware/service issue; disarming dead-man and continuing.",
        )
        if ssh is not None:
            try:
                await _disarm_recovery_reboot(ssh, case_id=case_id)
            except Exception:
                pass
        _RECOVERY_LEFT_ARMED = False

    if _SWEEP_CONTINUE_ON_FAILURE:
        hard_fail = (
            _SWEEP_CRITICAL_ONLY_FAIL
            and service_name
            and _service_is_critical(service_name)
            and not _is_known_quirk(service_name)
        )
        if not hard_fail:
            _partial_case(case_id, reason)
            raise ServiceCrashFailure(service_name or reason.split(":", 1)[0], reason)
    pytest.fail(f"{PROC_FAILED_MARKER} {case_id} FAILED: {reason}")


def _partial_case(case_id: str, reason: str) -> None:
    """Soft discrepancy — case PARTIAL; suite continues."""
    _log(case_id, f"PARTIAL: {reason}")
    check.fail(f"{PROC_PARTIAL_MARKER} {case_id} PARTIAL: {reason}")


def _report_core_gaps(case_id: str, results: CrashResult | list[CrashResult]) -> None:
    """After crash recovery succeeds, flag missing core dumps as PARTIAL (not FAILED)."""
    items = [results] if isinstance(results, CrashResult) else list(results)
    missing: list[str] = []
    for item in items:
        if item.core_expected and not item.core_saved:
            missing.append(item.service_name)
    if not missing:
        return
    bullets = "".join(f"\n  - {name}" for name in missing)
    _partial_case(
        case_id,
        f"Crash recovery verified; core dump not saved under {PROC_CORE_DIR} for "
        f"{len(missing)} service(s):{bullets}",
    )


def _log(case_id: str, message: str) -> None:
    print(f"[PROC][{case_id}] {message}")


def set_recovery_reboot_enabled(enabled: bool) -> None:
    """Toggle SSH-loss recovery reboot (armed before each crash/kill)."""
    global _RECOVERY_REBOOT_ENABLED
    _RECOVERY_REBOOT_ENABLED = enabled


def _recovery_reboot_delay_for(service_name: str, recovery_timeout_s: int) -> int:
    """Backup dead-man delay (automation reboots instantly on failure)."""
    slack = 25 if service_name in SSH_RECONNECT_SERVICES else 12
    return max(RECOVERY_REBOOT_DELAY_S, recovery_timeout_s + slack)


def _recovery_arm_shell(delay_s: int) -> str:
    """One-liner: (re)start a delayed reboot job unless cancel file exists."""
    return (
        f"kill $(cat {_RECOVERY_ARM_PID} 2>/dev/null) 2>/dev/null || true; "
        f"rm -f {_RECOVERY_CANCEL}; "
        f"( sleep {int(delay_s)}; [ -f {_RECOVERY_CANCEL} ] || "
        f"( logger -t procmon_recovery 'no {RECOVERY_REBOOT_HELLO} — rebooting'; reboot ) ) & "
        f"echo $! > {_RECOVERY_ARM_PID}"
    )


async def _arm_recovery_reboot(
    ssh: AsyncGenericDriver,
    *,
    case_id: str,
    delay_s: int = RECOVERY_REBOOT_DELAY_S,
) -> None:
    """Arm recovery reboot unless automation disarms with hello."""
    if not _RECOVERY_REBOOT_ENABLED:
        return
    await _ssh_run(ssh, _recovery_arm_shell(delay_s), timeout_ops=15)
    _log(
        case_id,
        f"Recovery reboot armed ({delay_s}s without {RECOVERY_REBOOT_HELLO})",
    )


async def _recovery_reboot_hello(
    ssh: AsyncGenericDriver,
    *,
    case_id: str,
    delay_s: int = RECOVERY_REBOOT_DELAY_S,
) -> None:
    """Slide recovery timer — restart sleep job while SSH is alive."""
    if not _RECOVERY_REBOOT_ENABLED:
        return
    try:
        await _ssh_run(ssh, _recovery_arm_shell(delay_s), timeout_ops=15)
        _log(case_id, f"Recovery hello ({RECOVERY_REBOOT_HELLO}) — timer reset")
    except Exception as exc:
        _log(
            case_id,
            f"Recovery hello failed (SSH down — recovery reboot may follow): {exc}",
        )


async def _disarm_recovery_reboot(ssh: AsyncGenericDriver, *, case_id: str) -> None:
    """Permanently disarm recovery reboot (case success or intentional destructive reboot)."""
    global _RECOVERY_LEFT_ARMED
    if not _RECOVERY_REBOOT_ENABLED:
        return
    try:
        await _ssh_run(
            ssh,
            f"touch {_RECOVERY_CANCEL}; "
            f"kill $(cat {_RECOVERY_ARM_PID} 2>/dev/null) 2>/dev/null || true; "
            f"rm -f {_RECOVERY_ARM_PID} {_RECOVERY_CANCEL}",
            timeout_ops=15,
        )
        _RECOVERY_LEFT_ARMED = False
        _LAST_RECOVERY_TRIGGER = ""
        _log(case_id, f"Recovery reboot disarmed ({RECOVERY_REBOOT_HELLO})")
    except Exception as exc:
        _log(case_id, f"Recovery disarm skipped: {exc}")


def _core_name(service_name: str) -> str:
    meta = MONITORED_SERVICES[service_name]
    return str(meta.get("core_name") or meta["pgrep"])


def _parse_counter(value: Any) -> int:
    if value in (None, "", "-"):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _open_process_monitoring_gui(gui_page) -> None:
    from utils.monitor_flows import _goto_admin_path, open_monitor_submenu

    try:
        await open_monitor_submenu(gui_page, PROCESS_MONITOR_GUI_PATH)
    except Exception:
        if not await _goto_admin_path(gui_page, PROCESS_MONITOR_GUI_PATH):
            pytest.fail("Unable to open Monitor → System Stats → Process Monitoring in the web GUI.")
    seed = gui_page.locator("#process_monitoring_seed")
    await seed.wait_for(state="attached", timeout=20000)
    await gui_page.locator("#process_monitoring_root").wait_for(state="attached", timeout=20000)


async def _fetch_process_monitoring_gui_rows(gui_page) -> list[dict[str, Any]]:
    await _open_process_monitoring_gui(gui_page)
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if match:
        data_url = f"{match.group(1)}/admin/monitor/system_stats_process_data"
        try:
            response = await gui_page.request.get(data_url)
            if response.ok:
                payload = await response.json()
                rows = payload.get("rows") or []
                if isinstance(rows, list) and rows:
                    return [row for row in rows if isinstance(row, dict)]
        except Exception as exc:
            _log("PROC", f"Live process_monitoring API fetch failed, using seed: {exc}")
    seed_text = await gui_page.locator("#process_monitoring_seed").text_content()
    try:
        payload = json.loads(seed_text or "{}")
    except json.JSONDecodeError as exc:
        pytest.fail(f"Unable to parse process_monitoring_seed JSON: {exc}")
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        pytest.fail(f"process_monitoring_seed rows is not a list: {type(rows)!r}")
    return [row for row in rows if isinstance(row, dict)]


def _aggregate_gui_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse GUI rows (one per procd instance) into per-service aggregates."""
    aggregated: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("process") or "").strip()
        if not name:
            continue
        respawn = _parse_counter(row.get("respawn_count"))
        crashes = _parse_counter(row.get("total_crashes"))
        running = str(row.get("running") or "").strip().lower()
        entry = aggregated.setdefault(
            name,
            {"respawn_count": 0, "total_crashes": 0, "running": False, "instances": 0},
        )
        entry["instances"] += 1
        entry["respawn_count"] = max(entry["respawn_count"], respawn)
        entry["total_crashes"] = max(entry["total_crashes"], crashes)
        entry["running"] = entry["running"] or running == "running"
    return aggregated


async def _list_core_files(ssh: AsyncGenericDriver) -> set[str]:
    raw = await _ssh_run(ssh, f"ls -1 {PROC_CORE_DIR}/ 2>/dev/null || true", timeout_ops=15)
    return {line.strip() for line in raw.splitlines() if line.strip().startswith("core.")}


async def _find_core_file(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    old_pid: int,
    cores_before: set[str],
    case_id: str,
) -> str | None:
    core_prefix = f"core.{_core_name(service_name)}.{old_pid}."
    deadline = time.monotonic() + CORE_FILE_WAIT_S
    while time.monotonic() < deadline:
        cores_after = await _list_core_files(ssh)
        new_cores = cores_after - cores_before
        matches = sorted(name for name in new_cores if name.startswith(core_prefix))
        if matches:
            path = f"{PROC_CORE_DIR}/{matches[0]}"
            _log(case_id, f"Core dump present: {path}")
            return path
        await asyncio.sleep(POLL_INTERVAL_S)
    _log(
        case_id,
        f"Core dump not found for {service_name} (expected {core_prefix}* under {PROC_CORE_DIR})",
    )
    return None


def _strip_shell_job_noise(raw: str) -> str:
    lines = []
    for line in str(raw or "").replace("\r", "").splitlines():
        text = line.strip()
        if not text or _SHELL_JOB_NOISE.search(text):
            continue
        lines.append(text)
    return "\n".join(lines)


def _parse_ubus_json(raw: str) -> dict[str, Any]:
    cleaned = _strip_shell_job_noise(raw).lstrip()
    decoder = json.JSONDecoder()
    obj, idx = decoder.raw_decode(cleaned)
    if idx < len(cleaned.strip()):
        trailing = cleaned[idx:].strip()
        if trailing and not _SHELL_JOB_NOISE.search(trailing):
            _log("PROC", f"Ignoring trailing SSH noise after ubus JSON: {trailing[:80]!r}")
    return obj


async def _system_uptime_s(ssh: AsyncGenericDriver) -> float:
    raw = await _ssh_run(ssh, RootCommands.GET_UPTIME, timeout_ops=15)
    parsed = _parse_uptime_seconds(_strip_shell_job_noise(raw))
    if parsed is None:
        pytest.fail(f"Unable to parse system uptime from: {raw!r}")
    return parsed


async def _epoch_now(ssh: AsyncGenericDriver) -> int:
    return int(ssh_scalar(await _ssh_run(ssh, "date +%s", timeout_ops=15)) or "0")


async def fetch_service_list(ssh: AsyncGenericDriver) -> dict[str, Any]:
    raw = await _ssh_run(ssh, "ubus call service list", timeout_ops=60)
    try:
        return _parse_ubus_json(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Unable to parse ubus service list JSON: {exc}\nRaw: {raw[:500]}")


def _first_instance(services: dict[str, Any], service_name: str) -> ServiceInstance | None:
    entry = services.get(service_name)
    if not isinstance(entry, dict):
        return None
    instances = entry.get("instances") or {}
    if not isinstance(instances, dict) or not instances:
        return None
    candidates: list[ServiceInstance] = []
    for inst_name, payload in instances.items():
        if not isinstance(payload, dict):
            continue
        command = " ".join(payload.get("command") or []).lower()
        if "hotplug" in command:
            continue
        candidates.append(ServiceInstance.from_ubus(service_name, inst_name, payload))
    if not candidates:
        inst_name, payload = next(iter(instances.items()))
        if not isinstance(payload, dict):
            return None
        return ServiceInstance.from_ubus(service_name, inst_name, payload)
    return sorted(candidates, key=lambda inst: (not inst.running, inst.total_crashes))[0]


async def get_service_instance(ssh: AsyncGenericDriver, service_name: str) -> ServiceInstance | None:
    return _first_instance(await fetch_service_list(ssh), service_name)


async def _pgrep_pids(ssh: AsyncGenericDriver, pattern: str) -> list[int]:
    raw = await _ssh_run(ssh, f"pgrep -f '{pattern}' 2>/dev/null || true", timeout_ops=15)
    return [int(line) for line in raw.splitlines() if line.strip().isdigit()]


async def _process_running(ssh: AsyncGenericDriver, service_name: str) -> bool:
    meta = MONITORED_SERVICES.get(service_name)
    if not meta:
        return False
    return bool(await _pgrep_pids(ssh, meta["pgrep"]))


async def _wait_for_running(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    expect_running: bool,
    timeout_s: int = RESPAWN_WAIT_S,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await _process_running(ssh, service_name) == expect_running:
            return True
        await asyncio.sleep(POLL_INTERVAL_S)
    return await _process_running(ssh, service_name) == expect_running


async def _induce_signal(
    ssh: AsyncGenericDriver,
    service_name: str,
    signal: KillSignal,
    *,
    case_id: str,
) -> int:
    meta = MONITORED_SERVICES[service_name]
    pids = await _pgrep_pids(ssh, meta["pgrep"])
    if not pids:
        _fail_case(
            case_id,
            f"{service_name} has no running process (pgrep {meta['pgrep']!r} returned no PID)",
        )
    pid = pids[0]
    sig = "-SEGV" if signal == "SEGV" else "-9"
    await _ssh_run(ssh, f"kill {sig} {pid}", timeout_ops=10)
    return pid


async def _ensure_service_present(ssh: AsyncGenericDriver, service_name: str) -> ServiceInstance:
    inst = await get_service_instance(ssh, service_name)
    if inst is None:
        pytest.skip(f"{service_name} not present in ubus service list on this firmware.")
    return inst


async def _assert_no_reboot(ssh: AsyncGenericDriver, uptime_before: float, *, case_id: str) -> None:
    uptime_after = await _system_uptime_s(ssh)
    if not _uptime_stable_after_network_event(uptime_before, uptime_after):
        _fail_case(
            case_id,
            f"Unexpected device reboot during test "
            f"(uptime before={uptime_before:.1f}s after={uptime_after:.1f}s)",
        )


async def _collect_visible_services(ssh: AsyncGenericDriver) -> dict[str, ServiceInstance]:
    services = await fetch_service_list(ssh)
    return {
        name: inst
        for name in MONITORED_SERVICES
        if (inst := _first_instance(services, name)) is not None
    }


async def _assert_baseline_counters(case_id: str, ssh: AsyncGenericDriver) -> dict[str, ServiceInstance]:
    """Services with zero crash counters (best-effort; skipped when DUT has prior crashes)."""
    visible = await _collect_visible_services(ssh)
    assert visible, f"{case_id}: no monitored services found in ubus service list"
    zeroed = {
        name: inst
        for name, inst in visible.items()
        if inst.respawn_count == 0 and inst.total_crashes == 0
    }
    if zeroed:
        return zeroed
    _log(case_id, "No services with zero crash counters; using full visible set for baseline.")
    return visible


async def _resolve_optional_target(ssh: AsyncGenericDriver, *candidates: str) -> str:
    for name in candidates:
        if await get_service_instance(ssh, name) is not None:
            return name
    pytest.skip(f"None of {candidates!r} present in ubus service list on this firmware.")


async def _resolve_crash_target(
    ssh: AsyncGenericDriver,
    *candidates: str,
    max_crashes: int | None = None,
) -> str:
    """Pick a running service procd will respawn (retry > 0)."""
    for name in candidates:
        inst = await get_service_instance(ssh, name)
        if inst is None or not inst.running:
            continue
        if inst.respawn_retry == 0:
            continue
        if max_crashes is not None and inst.total_crashes >= max_crashes:
            continue
        return name
    pytest.skip(
        f"No crashable target among {candidates!r} "
        f"(need running service with respawn retry > 0{f', total_crashes < {max_crashes}' if max_crashes is not None else ''})."
    )


async def _process_age_s(ssh: AsyncGenericDriver, pid: int) -> float | None:
    raw = await _ssh_run(
        ssh,
        f"awk 'BEGIN{{getline u < \"/proc/uptime\"; split(u,a,\" \"); sys=a[1]}} "
        f"{{print sys - $22/100}}' /proc/{pid}/stat 2>/dev/null",
        timeout_ops=15,
    )
    try:
        return float(ssh_scalar(_strip_shell_job_noise(raw)))
    except ValueError:
        return None


async def _wait_for_service_restart(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    host: str,
    password: str,
    old_pid: int | None,
    crashes_before: int,
    case_id: str,
    timeout_s: int,
) -> ServiceInstance:
    meta = MONITORED_SERVICES[service_name]
    deadline = time.monotonic() + timeout_s
    next_heartbeat = time.monotonic()
    while time.monotonic() < deadline:
        if time.monotonic() >= next_heartbeat:
            await _recovery_reboot_hello(ssh, case_id=case_id)
            next_heartbeat = time.monotonic() + RECOVERY_REBOOT_HEARTBEAT_S
        pids = await _pgrep_pids(ssh, meta["pgrep"])
        if not pids:
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        new_pid = pids[0]
        if old_pid and new_pid == old_pid:
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        inst = await get_service_instance(ssh, service_name)
        if inst is not None and inst.running and inst.pid == new_pid:
            return inst
        return ServiceInstance(
            service=service_name,
            instance=inst.instance if inst else "pgrep",
            running=True,
            pid=new_pid,
            respawn_count=inst.respawn_count if inst else 0,
            total_crashes=max(crashes_before + 1, inst.total_crashes if inst else 0),
            last_respawn=inst.last_respawn if inst else 0,
            last_exit=inst.last_exit if inst else 0,
            respawn_retry=inst.respawn_retry if inst else None,
        )
    reason = (
        f"{service_name} did not respawn within {timeout_s}s after crash (process did not restart)"
    )
    first_observation = service_name not in _NON_RESPAWNING_SERVICES
    _note_non_respawning_service(case_id, service_name, reason)
    if (
        first_observation
        and service_name in MUST_BE_RUNNING
        and service_name not in _KNOWN_RESPAWN_QUIRK_SERVICES
    ):
        try:
            await _ssh_run(
                ssh,
                f"/etc/init.d/{service_name} restart 2>/dev/null || "
                f"service {service_name} restart 2>/dev/null || true",
                timeout_ops=20,
            )
            await asyncio.sleep(2)
            inst = await get_service_instance(ssh, service_name)
            if inst and inst.running and inst.pid and inst.pid != old_pid:
                _log(case_id, f"{service_name} recovered via init.d restart after crash timeout")
                _NON_RESPAWNING_SERVICES.pop(service_name, None)
                return inst
        except Exception:
            pass
    if service_name in OPTIONAL_IDLE_SERVICES:
        _log(case_id, f"{reason} — optional/idle service, skipping")
        raise _ServiceCrashSkipped(service_name)
    await _fail_crash_case(ssh, host, password, case_id, reason, service_name=service_name)


async def _crash_and_verify_restart(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    host: str,
    password: str,
    case_id: str,
    signal: KillSignal = "SEGV",
    recovery_timeout_s: int = RESPAWN_WAIT_S,
) -> CrashResult:
    uptime_before = await _system_uptime_s(ssh)
    before = await _ensure_service_present(ssh, service_name)
    old_pid = before.pid
    if not old_pid:
        await _fail_crash_case(
            ssh,
            host,
            password,
            case_id,
            f"{service_name} has no PID before crash (process not running)",
            service_name=service_name,
        )
    crashes_before = before.total_crashes
    cores_before = await _list_core_files(ssh) if signal == "SEGV" else set()

    reboot_delay = _recovery_reboot_delay_for(service_name, recovery_timeout_s)
    await _arm_recovery_reboot(ssh, case_id=case_id, delay_s=reboot_delay)
    await _induce_signal(ssh, service_name, signal, case_id=case_id)

    await asyncio.sleep(0.5)
    if signal == "SEGV":
        still_running = await _process_running(ssh, service_name)
        if still_running:
            quick = await get_service_instance(ssh, service_name)
            if quick and quick.pid == old_pid and quick.total_crashes <= crashes_before:
                _log(case_id, f"SEGV had no effect on {service_name}; retrying with KILL")
                await _induce_signal(ssh, service_name, "KILL", case_id=case_id)
                await asyncio.sleep(0.5)
                still_running = await _process_running(ssh, service_name)
                quick = await get_service_instance(ssh, service_name)
                if still_running and quick and quick.pid == old_pid and quick.total_crashes <= crashes_before:
                    reason = (
                        f"{service_name} still running with no crash recorded after SEGV/KILL "
                        f"(pid {old_pid} unchanged)"
                    )
                    if service_name in OPTIONAL_IDLE_SERVICES or service_name in PREFLIGHT_COUNTER_EXEMPT:
                        label = (
                            "optional/idle service, skipping"
                            if service_name in OPTIONAL_IDLE_SERVICES
                            else "known firmware SEGV quirk, skipping"
                        )
                        _log(case_id, f"{reason} — {label}")
                        if service_name in _KNOWN_RESPAWN_QUIRK_SERVICES or service_name in PREFLIGHT_COUNTER_EXEMPT:
                            _note_non_respawning_service(case_id, service_name, reason)
                        raise _ServiceCrashSkipped(service_name)
                    await _fail_crash_case(
                        ssh, host, password, case_id, reason, service_name=service_name
                    )

    after = await _wait_for_service_restart(
        ssh,
        service_name,
        host=host,
        password=password,
        old_pid=old_pid,
        crashes_before=crashes_before,
        case_id=case_id,
        timeout_s=recovery_timeout_s,
    )
    if not after.pid or after.pid == old_pid:
        await _fail_crash_case(
            ssh,
            host,
            password,
            case_id,
            f"{service_name} PID did not change after crash ({old_pid} -> {after.pid})",
            service_name=service_name,
        )
    if after.total_crashes < crashes_before + 1:
        await _fail_crash_case(
            ssh,
            host,
            password,
            case_id,
            f"{service_name} crash counter did not increment "
            f"({crashes_before} -> {after.total_crashes})",
            service_name=service_name,
        )
    await _assert_no_reboot(ssh, uptime_before, case_id=case_id)

    core_path = None
    if signal == "SEGV" and old_pid:
        core_path = await _find_core_file(
            ssh,
            service_name,
            old_pid=old_pid,
            cores_before=cores_before,
            case_id=case_id,
        )

    await _disarm_recovery_reboot(ssh, case_id=case_id)
    return CrashResult(
        service_name=service_name,
        after=after,
        signal=signal,
        core_path=core_path,
    )


def _recovery_timeout_for(service_name: str) -> int:
    if service_name == "network":
        return NETIFD_RECOVERY_S
    if service_name == "log":
        return 12
    if service_name in {"rpcd", "ntpd"}:
        return 12
    return RESPAWN_WAIT_S


async def _crash_services_batch(
    ssh: AsyncGenericDriver,
    service_names: list[str],
    *,
    host: str,
    password: str,
    case_id: str,
    signal: KillSignal,
    check_logs: bool = False,
) -> tuple[list[CrashResult], list[tuple[str, str]]]:
    """Batch-crash multiple non-SSH services and verify all respawn in a single polling loop.

    Returns (results, skipped) — each per-service entry tagged so the case can summarize.
    """
    services_before = await fetch_service_list(ssh)
    cores_before = await _list_core_files(ssh) if signal == "SEGV" else set()

    pre: dict[str, tuple[int, int, ServiceInstance]] = {}
    skipped: list[tuple[str, str]] = []
    for name in service_names:
        inst = _first_instance(services_before, name)
        if inst is None or not inst.running or not inst.pid:
            skipped.append((name, "not running before batch crash"))
            if name in MUST_BE_RUNNING:
                _note_not_running_service(case_id, name, "no PID before crash")
            continue
        pre[name] = (inst.pid, inst.total_crashes, inst)

    if not pre:
        return [], skipped

    timeout_s = max(
        _BATCH_RESPAWN_TIMEOUT_S,
        max(_recovery_timeout_for(n) for n in pre) + 5,
    )
    reboot_delay = max(_recovery_reboot_delay_for(n, timeout_s) for n in pre)
    await _arm_recovery_reboot(ssh, case_id=case_id, delay_s=reboot_delay)

    pids_csv = " ".join(str(pid) for pid, _, _ in pre.values())
    sig = "-SEGV" if signal == "SEGV" else "-9"
    _log(
        case_id,
        f"BATCH {signal} on {len(pre)} services: "
        + ", ".join(f"{n}(pid={pid})" for n, (pid, _, _) in pre.items()),
    )
    await _ssh_run(ssh, f"kill {sig} {pids_csv} 2>/dev/null || true", timeout_ops=15)

    if signal == "SEGV":
        await asyncio.sleep(0.5)
        services_check = await fetch_service_list(ssh)
        retry_pids: list[int] = []
        for name, (old_pid, crashes_before, _) in pre.items():
            inst = _first_instance(services_check, name)
            if inst and inst.pid == old_pid and inst.total_crashes <= crashes_before:
                retry_pids.append(old_pid)
        if retry_pids:
            _log(case_id, f"SEGV had no effect on {len(retry_pids)} pids; retrying with KILL")
            await _ssh_run(
                ssh,
                f"kill -9 {' '.join(str(p) for p in retry_pids)} 2>/dev/null || true",
                timeout_ops=10,
            )

    deadline = time.monotonic() + timeout_s
    pending = set(pre)
    after_by_name: dict[str, ServiceInstance] = {}
    next_heartbeat = time.monotonic() + RECOVERY_REBOOT_HEARTBEAT_S
    while pending and time.monotonic() < deadline:
        if time.monotonic() >= next_heartbeat:
            await _recovery_reboot_hello(ssh, case_id=case_id, delay_s=reboot_delay)
            next_heartbeat = time.monotonic() + RECOVERY_REBOOT_HEARTBEAT_S
        services_after = await fetch_service_list(ssh)
        for name in list(pending):
            old_pid, _crashes_before, _ = pre[name]
            inst = _first_instance(services_after, name)
            if inst and inst.running and inst.pid and inst.pid != old_pid:
                after_by_name[name] = inst
                pending.discard(name)
        if pending:
            await asyncio.sleep(_BATCH_POLL_INTERVAL_S)

    if pending:
        recheck_deadline = time.monotonic() + _BATCH_RECONFIRM_TIMEOUT_S
        while pending and time.monotonic() < recheck_deadline:
            services_after = await fetch_service_list(ssh)
            for name in list(pending):
                old_pid, _crashes_before, _ = pre[name]
                inst = _first_instance(services_after, name)
                if inst and inst.running and inst.pid and inst.pid != old_pid:
                    after_by_name[name] = inst
                    pending.discard(name)
                    _log(case_id, f"{name} respawned late (after batch deadline); accepting")
            if pending:
                await asyncio.sleep(_BATCH_POLL_INTERVAL_S)

    for name in pending:
        reason = (
            f"{name} did not respawn within {timeout_s + _BATCH_RECONFIRM_TIMEOUT_S}s after batch crash"
        )
        _note_non_respawning_service(case_id, name, reason)
        skipped.append((name, reason))

    cores_after: set[str] = set()
    if signal == "SEGV":
        cores_after = await _list_core_files(ssh)
    new_cores = cores_after - cores_before

    results: list[CrashResult] = []
    for name, inst in after_by_name.items():
        old_pid, crashes_before, _ = pre[name]
        if inst.total_crashes < crashes_before + 1:
            reason = (
                f"{name} crash counter did not increment "
                f"({crashes_before} -> {inst.total_crashes})"
            )
            _note_counter_quirk_service(case_id, name, reason)
            skipped.append((name, reason))
            continue
        core_path = None
        if signal == "SEGV":
            prefix = f"core.{_core_name(name)}.{old_pid}."
            matches = sorted(c for c in new_cores if c.startswith(prefix))
            if matches:
                core_path = f"{PROC_CORE_DIR}/{matches[0]}"
                _log(case_id, f"Core dump present: {core_path}")
            else:
                _log(
                    case_id,
                    f"Core dump not found for {name} "
                    f"(expected {prefix}* under {PROC_CORE_DIR})",
                )
        results.append(CrashResult(service_name=name, after=inst, signal=signal, core_path=core_path))
        _log(case_id, f"{name} recovered; total_crashes={inst.total_crashes}, pid={inst.pid}")

    if check_logs and results:
        names_csv = "|".join(re.escape(r.service_name) for r in results)
        logs = await _ssh_run(
            ssh,
            f"logread 2>/dev/null | grep -iE '{names_csv}' | tail -n 60",
            timeout_ops=30,
        )
        if logs.strip():
            _log(case_id, "Batch restart log evidence present.")
        else:
            _log(case_id, "Batch restart logs quiet; relying on ubus counters.")

    await _disarm_recovery_reboot(ssh, case_id=case_id)
    return results, skipped


async def _classify_service_for_crash(
    ssh: AsyncGenericDriver,
    service_name: str,
) -> tuple[Literal["test", "skip", "fail"], str]:
    if service_name in _NON_RESPAWNING_SERVICES:
        return "skip", f"session: does not respawn ({_NON_RESPAWNING_SERVICES[service_name]})"
    if service_name in _COUNTER_QUIRK_SERVICES:
        return "skip", f"session: counter quirk ({_COUNTER_QUIRK_SERVICES[service_name]})"
    if service_name in _NOT_RUNNING_SERVICES:
        return "skip", f"session: not running ({_NOT_RUNNING_SERVICES[service_name]})"
    inst = await get_service_instance(ssh, service_name)
    if inst is None:
        return "skip", "not registered in ubus on this firmware"
    if inst.respawn_retry == 0:
        return "skip", "procd respawn disabled (retry=0)"
    if not inst.running:
        if service_name in OPTIONAL_IDLE_SERVICES:
            return "skip", "optional/idle service not running"
        if service_name not in MUST_BE_RUNNING:
            return "skip", "monitored service not running"
        return "fail", "required service not running"
    if not inst.pid:
        return "fail", "no PID (process not running)"
    return "test", ""


async def _crashable_services_on_dut(ssh: AsyncGenericDriver) -> set[str]:
    crashable: set[str] = set()
    for name in MONITORED_SERVICE_NAMES:
        action, _ = await _classify_service_for_crash(ssh, name)
        if action == "test":
            crashable.add(name)
    return crashable


async def _reconnect_ssh(host: str, password: str, *, case_id: str, service_name: str) -> AsyncGenericDriver:
    """Wait for sshd/network to restore SSH; recovery-reboot if the link does not return."""
    _log(case_id, f"Reconnecting SSH after {service_name} crash...")
    deadline = time.monotonic() + SSH_RECONNECT_WAIT_S
    last_error = ""
    while time.monotonic() < deadline:
        ping_ok, ping_detail = await _local_ping_host(host)
        if not ping_ok:
            last_error = f"ping down ({ping_detail})"
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        try:
            return await _wait_for_ssh(host, password, timeout_s=10, interval_s=1)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(1)
    _log(
        case_id,
        f"SSH reconnect after {service_name} timed out ({last_error}); triggering recovery reboot",
    )
    recovered = await _instant_reboot_and_wait(
        None,
        host,
        password,
        case_id=case_id,
        recovery_reason=(
            f"SSH not restored within {SSH_RECONNECT_WAIT_S}s after {service_name} crash ({last_error})"
        ),
    )
    if recovered is None:
        raise TimeoutError(
            f"SSH to {host} not ready after {service_name} crash and recovery reboot: {last_error}"
        )
    return recovered


async def _crash_and_verify_restart_maybe_reconnect(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    host: str,
    password: str,
    case_id: str,
    signal: KillSignal = "SEGV",
    recovery_timeout_s: int = RESPAWN_WAIT_S,
) -> tuple[CrashResult, AsyncGenericDriver]:
    if service_name not in SSH_RECONNECT_SERVICES:
        result = await _crash_and_verify_restart(
            ssh,
            service_name,
            host=host,
            password=password,
            case_id=case_id,
            signal=signal,
            recovery_timeout_s=recovery_timeout_s,
        )
        return result, ssh

    uptime_before = await _system_uptime_s(ssh)
    before = await _ensure_service_present(ssh, service_name)
    old_pid = before.pid
    if not old_pid:
        await _fail_crash_case(
            ssh,
            host,
            password,
            case_id,
            f"{service_name} has no PID before crash (process not running)",
            service_name=service_name,
        )
    crashes_before = before.total_crashes
    cores_before = await _list_core_files(ssh) if signal == "SEGV" else set()

    reboot_delay = _recovery_reboot_delay_for(service_name, recovery_timeout_s)
    await _arm_recovery_reboot(ssh, case_id=case_id, delay_s=reboot_delay)
    await _induce_signal(ssh, service_name, signal, case_id=case_id)
    try:
        await ssh.send_command("echo ok", timeout_ops=3)
    except Exception:
        pass
    try:
        await _close_ssh(ssh)
    except Exception:
        pass

    ssh = await _publish_ssh(
        await _reconnect_ssh(host, password, case_id=case_id, service_name=service_name)
    )
    await _recovery_reboot_hello(ssh, case_id=case_id, delay_s=reboot_delay)
    after = await _wait_for_service_restart(
        ssh,
        service_name,
        host=host,
        password=password,
        old_pid=old_pid,
        crashes_before=crashes_before,
        case_id=case_id,
        timeout_s=recovery_timeout_s,
    )
    if not after.pid or after.pid == old_pid:
        await _fail_crash_case(
            ssh,
            host,
            password,
            case_id,
            f"{service_name} PID did not change after crash ({old_pid} -> {after.pid})",
            service_name=service_name,
        )
    if after.total_crashes < crashes_before + 1:
        await _fail_crash_case(
            ssh,
            host,
            password,
            case_id,
            f"{service_name} crash counter did not increment "
            f"({crashes_before} -> {after.total_crashes})",
            service_name=service_name,
        )
    await _assert_no_reboot(ssh, uptime_before, case_id=case_id)

    core_path = None
    if signal == "SEGV" and old_pid:
        core_path = await _find_core_file(
            ssh,
            service_name,
            old_pid=old_pid,
            cores_before=cores_before,
            case_id=case_id,
        )

    await _disarm_recovery_reboot(ssh, case_id=case_id)
    return (
        CrashResult(
            service_name=service_name,
            after=after,
            signal=signal,
            core_path=core_path,
        ),
        ssh,
    )


async def _run_all_services_crash_case(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str,
    signal: KillSignal,
    crashes_per_service: int = 1,
    under_stress: bool = False,
    check_logs: bool = False,
) -> AsyncGenericDriver:
    """Run crash/kill scenario on every procd-respawnable service (official case full coverage)."""
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    await _require_procmon_ready(ssh, case_id=case_id)
    crashable_before = await _crashable_services_on_dut(ssh)
    results: list[CrashResult] = []
    skipped: list[tuple[str, str]] = []
    stress_mode: str | None = None

    if under_stress:
        stress_mode = await _start_cpu_stress(ssh)
        await asyncio.sleep(2)
        if not await _collect_visible_services(ssh):
            _fail_case(case_id, "ubus service list empty under CPU stress")

    case_completed = False
    with _sweep_failure_policy(case_id):
        try:
            batch_targets: list[str] = []
            serial_targets: list[str] = []
            for service_name in SERVICE_SWEEP_ORDER:
                action, reason = await _classify_service_for_crash(ssh, service_name)
                if action == "skip":
                    _log(case_id, f"Skipping {service_name}: {reason}")
                    skipped.append((service_name, reason))
                    continue
                if action == "fail":
                    if service_name in MUST_BE_RUNNING:
                        _note_not_running_service(case_id, service_name, reason)
                    _partial_case(case_id, f"{service_name}: {reason}")
                    skipped.append((service_name, reason))
                    continue
                if service_name in SSH_RECONNECT_SERVICES:
                    serial_targets.append(service_name)
                else:
                    batch_targets.append(service_name)

            for attempt in range(1, crashes_per_service + 1):
                attempt_label = (
                    f" attempt {attempt}/{crashes_per_service}" if crashes_per_service > 1 else ""
                )
                if batch_targets and _BATCH_SWEEP_ENABLED:
                    _log(case_id, f"Batch {signal} sweep starting{attempt_label}")
                    batch_results, batch_skipped = await _crash_services_batch(
                        ssh,
                        batch_targets,
                        host=host,
                        password=password,
                        case_id=case_id,
                        signal=signal,
                        check_logs=check_logs,
                    )
                    results.extend(batch_results)
                    skipped.extend(batch_skipped)
                    batch_targets = [
                        name for name in batch_targets if not _is_known_quirk(name)
                    ]

                for service_name in list(serial_targets):
                    if _is_known_quirk(service_name):
                        _log(
                            case_id,
                            f"Skipping {service_name}: session quirk (already noted)",
                        )
                        skipped.append((service_name, "session quirk"))
                        continue
                    timeout_s = _recovery_timeout_for(service_name)
                    _log(case_id, f"{signal} on {service_name}{attempt_label}")
                    try:
                        result, ssh = await _crash_and_verify_restart_maybe_reconnect(
                            ssh,
                            service_name,
                            host=host,
                            password=password,
                            case_id=case_id,
                            signal=signal,
                            recovery_timeout_s=timeout_s,
                        )
                    except _ServiceCrashSkipped as exc:
                        skipped.append((exc.service_name, "did not respawn after crash"))
                        try:
                            ssh = await _publish_ssh(
                                await _ensure_live_ssh(
                                    ssh, host, password, timeout_s=SSH_RECONNECT_WAIT_S
                                )
                            )
                            await _disarm_recovery_reboot(ssh, case_id=case_id)
                        except Exception:
                            pass
                        continue
                    except ServiceCrashFailure as exc:
                        skipped.append((exc.service_name, exc.reason))
                        ssh = await _publish_ssh(
                            await _ensure_live_ssh(
                                ssh, host, password, timeout_s=SSH_RECONNECT_WAIT_S
                            )
                        )
                        continue
                    results.append(result)
                    if check_logs:
                        before_crashes = result.after.total_crashes - 1
                        logs = await _grep_restart_logs(ssh, target=service_name, tail=40)
                        if logs.strip():
                            _log(case_id, f"{service_name}: restart evidence in device logs.")
                        else:
                            _log(
                                case_id,
                                f"{service_name}: logs quiet; ubus total_crashes={result.after.total_crashes} "
                                f"(was {before_crashes}).",
                            )
                    _log(
                        case_id,
                        f"{service_name} recovered; total_crashes={result.after.total_crashes}, pid={result.after.pid}",
                    )
            case_completed = True
        finally:
            if under_stress:
                try:
                    live = await _ensure_live_ssh(
                        ssh, host, password, timeout_s=SSH_RECONNECT_WAIT_S
                    )
                    await _stop_cpu_stress(live)
                    ssh = live
                except Exception as exc:
                    _log(case_id, f"CPU stress cleanup skipped ({exc})")

    if not case_completed:
        return await _publish_ssh(
            await _ensure_live_ssh(ssh, host, password, timeout_s=SSH_RECONNECT_WAIT_S)
        )

    tested = {result.service_name for result in results}
    attempted = set(tested)
    attempted |= {name for name, _ in skipped}
    missed = sorted(crashable_before - attempted)
    if missed:
        _partial_case(
            case_id,
            f"Did not run {signal} on crashable services: {', '.join(missed)}",
        )

    if signal == "SEGV":
        _report_core_gaps(case_id, results)

    _log(
        case_id,
        f"All-services {signal} complete: {len(tested)} services, "
        f"{len(results)} crash(es), {len(skipped)} skipped"
        + (f"; stress={stress_mode}" if stress_mode else "")
        + f"; skipped=[{'; '.join(f'{n} ({r})' for n, r in skipped) or 'none'}]",
    )
    ssh = await _ensure_live_ssh(ssh, host, password, timeout_s=SSH_RECONNECT_WAIT_S)
    await _disarm_recovery_reboot(ssh, case_id=case_id)
    return await _publish_ssh(ssh)


async def _assert_unauthorized_kill_all_services(
    ssh: AsyncGenericDriver,
    *,
    case_id: str,
) -> None:
    crashable = await _crashable_services_on_dut(ssh)
    tested_names: list[str] = []
    skipped: list[str] = []
    for service_name in SERVICE_SWEEP_ORDER:
        action, reason = await _classify_service_for_crash(ssh, service_name)
        if action != "test":
            skipped.append(f"{service_name} ({reason})")
            continue
        await _assert_unauthorized_kill(ssh, case_id=case_id, target=service_name)
        tested_names.append(service_name)
    missed = sorted(crashable - set(tested_names))
    if missed:
        _fail_case(case_id, f"Unauthorized kill not attempted on: {', '.join(missed)}")
    _log(
        case_id,
        f"Unauthorized kill blocked on {len(tested_names)} services; "
        f"skipped {len(skipped)} [{'; '.join(skipped) or 'none'}]",
    )


async def _run_sequential_crashes(
    ssh: AsyncGenericDriver,
    targets: list[str],
    *,
    host: str,
    password: str,
    case_id: str,
    signal: KillSignal,
    recovery_timeout_s: int = RESPAWN_WAIT_S,
) -> list[CrashResult]:
    results: list[CrashResult] = []
    for target in targets:
        results.append(
            await _crash_and_verify_restart(
                ssh,
                target,
                host=host,
                password=password,
                case_id=case_id,
                signal=signal,
                recovery_timeout_s=recovery_timeout_s,
            )
        )
    _report_core_gaps(case_id, results)
    return results


async def _ensure_live_ssh(
    ssh: AsyncGenericDriver,
    host: str,
    password: str,
    *,
    timeout_s: int | None = None,
) -> AsyncGenericDriver:
    wait_s = _PREPARE_SSH_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        await ssh.send_command("echo ok", timeout_ops=10)
        return ssh
    except Exception:
        try:
            await _close_ssh(ssh)
        except Exception:
            pass
        ping_ok, ping_detail = await _local_ping_host(host)
        if not ping_ok:
            raise ConnectionError(f"lab ping to {host} down ({ping_detail})")
        return await _wait_for_ssh(host, password, timeout_s=wait_s, interval_s=1)


def non_respawning_services_summary() -> str:
    """Session-wide list of services that failed to respawn (for logging)."""
    return _non_respawning_summary()


async def _wait_for_reboot_and_ssh(
    host: str,
    password: str,
    *,
    case_id: str,
    down_poll_s: int = PROC_REBOOT_DOWN_POLL_S,
    up_timeout_s: int = PROC_REBOOT_UP_TIMEOUT_S,
) -> AsyncGenericDriver:
    _log(case_id, f"Waiting up to {down_poll_s}s for device to go down...")
    down_deadline = time.monotonic() + down_poll_s
    while time.monotonic() < down_deadline:
        ping_ok, _ = await _local_ping_host(host)
        if not ping_ok:
            break
        await asyncio.sleep(POLL_INTERVAL_S)
    conn = await _wait_for_ssh(host, password, timeout_s=up_timeout_s, interval_s=2)
    _log(case_id, f"SSH restored on {host} after watchdog reboot.")
    return conn


async def _start_cpu_stress(ssh: AsyncGenericDriver) -> str:
    raw = await _ssh_run(
        ssh,
        "sh -c 'if command -v stress-ng >/dev/null 2>&1; then "
        "nohup stress-ng --cpu 2 --cpu-method matrixprod --timeout 90s "
        ">/tmp/proc_stress.log 2>&1 </dev/null & echo PROC_STRESS_MODE=stress-ng; "
        "else ( while true; do :; done ) </dev/null >/dev/null 2>&1 & echo PROC_STRESS_MODE=yes; fi'",
        timeout_ops=15,
    )
    for line in reversed(_strip_shell_job_noise(raw).splitlines()):
        if "PROC_STRESS_MODE=" in line:
            return line.split("=", 1)[-1].strip()
    return "yes"


async def _stop_cpu_stress(ssh: AsyncGenericDriver) -> None:
    await _ssh_run(ssh, "killall stress-ng 2>/dev/null; killall yes 2>/dev/null; true", timeout_ops=15)


async def _grep_restart_logs(ssh: AsyncGenericDriver, *, target: str, tail: int = 80) -> str:
    logread = await _ssh_run(
        ssh,
        "logread 2>/dev/null | grep -iE "
        f"'{re.escape(target)}|procd|respawn|restart|crash|segv|signal' | tail -n {tail}",
        timeout_ops=45,
    )
    if logread.strip():
        return logread
    return await _ssh_run(
        ssh,
        "grep -iE "
        f"'{re.escape(target)}|procd|respawn|restart|crash|segv|signal' /etc/device_logs 2>/dev/null | tail -n {tail}",
        timeout_ops=45,
    )


async def _assert_unauthorized_kill(
    ssh: AsyncGenericDriver,
    *,
    case_id: str,
    target: str = "senao-openapi-server",
) -> None:
    inst = await _ensure_service_present(ssh, target)
    pid = inst.pid
    if not pid:
        _fail_case(case_id, f"{target} has no PID (process not running)")
    raw = await _ssh_run(
        ssh,
        f"PID={pid}; echo 'kill -9 '$PID | login -f nobody 2>&1; echo PROC_KILL_EXIT=$?",
        timeout_ops=15,
    )
    cleaned = _strip_shell_job_noise(raw)
    exit_line = next((line for line in cleaned.splitlines() if line.startswith("PROC_KILL_EXIT=")), "")
    exit_code = exit_line.split("=", 1)[-1].strip() if exit_line else ""
    still = await _ensure_service_present(ssh, target)
    if not still.running or still.pid != pid:
        _fail_case(
            case_id,
            f"{target} restarted or changed PID after unauthorized kill ({pid} -> {still.pid})",
        )
    if exit_code in ("", "0"):
        _fail_case(
            case_id,
            f"Unprivileged kill unexpectedly succeeded (exit={exit_code!r}): {raw!r}",
        )
    audit = await _ssh_run(
        ssh,
        "logread 2>/dev/null | grep -iE 'denied|permission|unauthorized|kill' | tail -n 20",
        timeout_ops=30,
    )
    _log(
        case_id,
        f"Unauthorized kill blocked (exit={exit_code}); audit tail: {audit[:120] or '(none)'}",
    )


async def _assert_post_reboot_services(
    ssh: AsyncGenericDriver,
    *,
    case_id: str,
    min_services: int = 10,
    settle_s: int = 150,
) -> None:
    """Poll ubus for service list after reboot; tolerate slow ubusd start (don't hang on it)."""
    deadline = time.monotonic() + settle_s
    visible: dict = {}
    not_running: list[str] = []
    last_hang_log = 0.0
    while time.monotonic() < deadline:
        try:
            visible = await asyncio.wait_for(_collect_visible_services(ssh), timeout=12)
        except asyncio.TimeoutError:
            exc_msg = "ubus probe exceeded 12s"
        except Exception as exc:
            exc_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        else:
            not_running = [
                name
                for name, inst in visible.items()
                if not inst.running and name not in OPTIONAL_IDLE_SERVICES
            ]
            if len(visible) >= min_services and not not_running:
                break
            await asyncio.sleep(2)
            continue
        now = time.monotonic()
        if now - last_hang_log > 10:
            _log(case_id, f"Post-reboot ubus probe not ready yet ({exc_msg}); retrying...")
            last_hang_log = now
        await asyncio.sleep(2)

    idle_down = [name for name, inst in visible.items() if not inst.running and name in OPTIONAL_IDLE_SERVICES]
    if idle_down:
        _log(case_id, f"Optional/idle services not running after reboot: {', '.join(idle_down)}")
    if len(visible) < min_services:
        _fail_case(
            case_id,
            f"Too few monitored services tracked after reboot ({len(visible)} < {min_services})",
        )
    if not_running:
        quirky = [n for n in not_running if _is_known_quirk(n) or n not in MUST_BE_RUNNING]
        critical = [n for n in not_running if n in MUST_BE_RUNNING and not _is_known_quirk(n)]
        for name in quirky:
            _note_not_running_service(case_id, name, "not running after reboot")
        if quirky:
            bullets = "".join(f"\n  - {n}" for n in quirky)
            _partial_case(
                case_id,
                f"Known-quirky service(s) did not start after reboot:{bullets}",
            )
        if critical:
            bullets = "".join(f"\n  - {n}" for n in critical)
            _fail_case(
                case_id,
                f"Critical process(es) did not start after reboot:{bullets}",
            )
    _log(case_id, f"Post-reboot monitor OK; {len(visible)} services tracked.")


# --- Public case assertions ---


async def assert_process_monitor_preflight(
    ssh: AsyncGenericDriver,
    gui_page,
    *,
    case_id: str = "PROC_PREFLIGHT",
) -> None:
    """
    Before any PROCESS_* case:
    1. Catalog has no duplicate service entries.
    2. Web GUI System Stats → Process Monitoring shows zero crash stats after reboot.
    3. All catalog services appear in GUI + ubus; crash targets stay in catalog.
    """
    catalog_errors = validate_monitored_services_catalog()
    assert not catalog_errors, f"{case_id}: catalog validation failed: {'; '.join(catalog_errors)}"
    _log(case_id, f"Catalog OK ({len(MONITORED_SERVICE_NAMES)} monitored services, no duplicates).")

    await _require_procmon_ready(ssh, case_id=case_id)

    visible = await _collect_visible_services(ssh)
    gui_rows = await _fetch_process_monitoring_gui_rows(gui_page)
    gui_by_service = _aggregate_gui_rows(gui_rows)
    assert gui_rows, f"{case_id}: Process Monitoring GUI table is empty."

    duplicate_monitored = [
        name
        for name in MONITORED_SERVICE_NAMES
        if sum(1 for row in gui_rows if str(row.get("process") or "").strip() == name) > 1
    ]
    if duplicate_monitored:
        _log(
            case_id,
            f"Multiple GUI rows for monitored services (instances): {', '.join(duplicate_monitored)}",
        )

    missing_gui = [name for name in visible if name not in gui_by_service]
    assert not missing_gui, (
        f"{case_id}: ubus-visible monitored services missing from Process Monitoring GUI: "
        f"{', '.join(missing_gui)}"
    )

    absent = [name for name in MONITORED_SERVICE_NAMES if name not in visible]
    if absent:
        _log(case_id, f"Monitored services not registered in ubus on this firmware: {', '.join(absent)}")

    def _counter_label(name: str, respawn: int, crashes: int) -> str:
        return f"{name}(respawn={respawn},crashes={crashes})"

    non_zero_gui_names = [
        name
        for name in visible
        if gui_by_service[name]["respawn_count"] or gui_by_service[name]["total_crashes"]
    ]
    exempt_gui = [_counter_label(name, gui_by_service[name]["respawn_count"], gui_by_service[name]["total_crashes"])
                  for name in non_zero_gui_names if name in PREFLIGHT_COUNTER_EXEMPT]
    blocking_gui = [_counter_label(name, gui_by_service[name]["respawn_count"], gui_by_service[name]["total_crashes"])
                    for name in non_zero_gui_names if name not in PREFLIGHT_COUNTER_EXEMPT]
    if exempt_gui:
        _log(
            case_id,
            "GUI shows non-zero baseline counters (PROCESS_01 will report): "
            + ", ".join(exempt_gui),
        )
    assert not blocking_gui, (
        f"{case_id}: Process Monitoring GUI shows non-zero stats after reboot "
        f"(reboot DUT before running): {', '.join(blocking_gui)}"
    )
    _log(case_id, "GUI Process Monitoring baseline OK for all non-exempt monitored services.")

    missing_ubus = [name for name in MONITORED_SERVICE_NAMES if name not in visible]
    if missing_ubus:
        _log(
            case_id,
            f"Catalog services absent from ubus (skipped for zero-counter check): {', '.join(missing_ubus)}",
        )

    non_zero_ubus_names = [
        name for name, inst in visible.items() if inst.respawn_count or inst.total_crashes
    ]
    exempt_ubus = [
        _counter_label(name, visible[name].respawn_count, visible[name].total_crashes)
        for name in non_zero_ubus_names
        if name in PREFLIGHT_COUNTER_EXEMPT
    ]
    blocking_ubus = [
        _counter_label(name, visible[name].respawn_count, visible[name].total_crashes)
        for name in non_zero_ubus_names
        if name not in PREFLIGHT_COUNTER_EXEMPT
    ]
    if exempt_ubus:
        _log(
            case_id,
            "ubus shows non-zero baseline counters (PROCESS_01 will report): "
            + ", ".join(exempt_ubus),
        )
    assert not blocking_ubus, (
        f"{case_id}: ubus service list shows non-zero crash stats after reboot: {', '.join(blocking_ubus)}"
    )
    _log(case_id, "ubus crash counters OK for all non-exempt visible monitored services.")

    uncovered = set(MONITORED_SERVICE_NAMES) - CRASH_TEST_SERVICE_TARGETS
    if uncovered:
        _log(
            case_id,
            "Catalog services without crash/kill sweep assignment: "
            + ", ".join(sorted(uncovered)),
        )
    _log(
        case_id,
        f"Preflight complete: {len(MONITORED_SERVICE_NAMES)} services in GUI and ubus; "
        f"{len(CRASH_TEST_SERVICE_TARGETS)} services in full SEGV/KILL sweep plan.",
    )


async def _require_procmon_ready(ssh: AsyncGenericDriver, *, case_id: str) -> None:
    network = await get_service_instance(ssh, "network")
    if network is None or not network.running:
        pytest.skip(
            f"{case_id}: netifd/network is not running (procmon may have halted it). "
            "Reboot the DUT before running the PROCESS suite."
        )


async def assert_process_01_visibility(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_01",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    await _require_procmon_ready(ssh, case_id=case_id)
    visible = await _collect_visible_services(ssh)
    assert visible, f"{case_id}: no monitored services found in ubus service list"
    absent = [name for name in MONITORED_SERVICE_NAMES if name not in visible]
    if absent:
        _log(case_id, f"Catalog services not in ubus on this firmware: {', '.join(absent)}")
    not_running = [name for name, inst in visible.items() if not inst.running]
    hard_down = [name for name in not_running if name in MUST_BE_RUNNING]
    soft_down = [name for name in not_running if name in OPTIONAL_IDLE_SERVICES]
    other_down = [name for name in not_running if name not in MUST_BE_RUNNING and name not in OPTIONAL_IDLE_SERVICES]
    if soft_down:
        _log(case_id, f"Optional/idle services not running: {', '.join(soft_down)}")
    if other_down:
        _log(case_id, f"Other monitored services not running: {', '.join(other_down)}")
    assert not hard_down, f"{case_id}: required services not running: {', '.join(hard_down)}"

    counter_issues: list[str] = []
    for name, inst in sorted(visible.items()):
        if not inst.respawn_count and not inst.total_crashes:
            continue
        label = f"{name}(respawn={inst.respawn_count},crashes={inst.total_crashes})"
        if name in PREFLIGHT_COUNTER_EXEMPT:
            counter_issues.append(f"{label} — possible firmware bug (expected 0 after reboot)")
            _partial_case(
                case_id,
                f"{name} baseline crash counters not zero after reboot "
                f"(respawn={inst.respawn_count}, crashes={inst.total_crashes}); possible firmware bug",
            )
        else:
            counter_issues.append(label)
            _fail_case(
                case_id,
                f"{name} baseline crash counters not zero after reboot "
                f"(respawn={inst.respawn_count}, crashes={inst.total_crashes})",
            )
    if counter_issues:
        _log(case_id, f"Baseline counter discrepancies: {', '.join(counter_issues)}")
    else:
        _log(case_id, "PASSED: baseline crash counters are zero for all visible monitored services.")
    _log(case_id, f"All {len(visible)} monitored services visible; required services running.")
    return ssh


async def assert_process_02_uptime(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_02",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    await _require_procmon_ready(ssh, case_id=case_id)
    sys_uptime = await _system_uptime_s(ssh)
    now_epoch = await _epoch_now(ssh)
    mismatches: list[str] = []
    for name, inst in (await _collect_visible_services(ssh)).items():
        if not inst.running or inst.pid is None:
            if name in OPTIONAL_IDLE_SERVICES:
                continue
            mismatches.append(f"{name}: not running")
            continue
        proc_age = await _process_age_s(ssh, inst.pid)
        if proc_age is None:
            if inst.total_crashes == 0 and inst.last_respawn:
                proc_age = float(now_epoch - inst.last_respawn)
            else:
                mismatches.append(f"{name}: unable to read process age for pid {inst.pid}")
                continue
        if inst.total_crashes == 0 and proc_age > sys_uptime + 30:
            mismatches.append(f"{name}: process age {proc_age:.0f}s exceeds system uptime {sys_uptime:.0f}s")
        if inst.last_respawn and abs((now_epoch - inst.last_respawn) - proc_age) > 45:
            mismatches.append(
                f"{name}: last_respawn skew (proc_age={proc_age:.0f}s, "
                f"epoch_delta={now_epoch - inst.last_respawn}s)"
            )
    if mismatches:
        _fail_case(case_id, f"Uptime discrepancies: {'; '.join(mismatches)}")
    _log(case_id, f"Uptime consistent across services (system uptime {sys_uptime:.0f}s).")
    return ssh


async def assert_process_03_restart_count(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_03",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    before = await _collect_visible_services(ssh)
    await asyncio.sleep(3)
    after = await _collect_visible_services(ssh)
    changed = [
        name
        for name, inst_before in before.items()
        if (inst_after := after.get(name))
        and (
            inst_before.respawn_count != inst_after.respawn_count
            or inst_before.total_crashes != inst_after.total_crashes
        )
    ]
    if changed:
        _fail_case(case_id, f"Restart counters changed without crash: {', '.join(changed)}")
    _log(case_id, "Restart counters remained stable during observation window.")
    return ssh


async def assert_process_04_timestamp(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_04",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    before = await _collect_visible_services(ssh)
    await asyncio.sleep(5)
    after = await _collect_visible_services(ssh)
    drift: list[str] = []
    for name, inst_before in before.items():
        inst_after = after.get(name)
        if inst_after is None:
            continue
        if inst_before.total_crashes != inst_after.total_crashes:
            drift.append(f"{name}: total_crashes {inst_before.total_crashes}->{inst_after.total_crashes}")
        if inst_before.last_exit != inst_after.last_exit and inst_after.total_crashes == 0:
            drift.append(f"{name}: last_exit changed without crash")
    if drift:
        _fail_case(case_id, f"Unexpected timestamp/counter drift: {'; '.join(drift)}")
    _log(case_id, "Crash timestamps/counters stable while idle.")
    return ssh


async def assert_process_05_crash_single(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_05",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="SEGV", crashes_per_service=1
    )


async def assert_process_06_crash_multiple(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_06",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="SEGV", crashes_per_service=1
    )


async def assert_process_07_crash_critical(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_07",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="SEGV", crashes_per_service=1
    )


async def assert_process_08_crash_non_critical(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_08",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="SEGV", crashes_per_service=1
    )


async def assert_process_09_watchdog_reboot(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_09",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    await _disarm_recovery_reboot(ssh, case_id=case_id)
    await _ssh_run(ssh, """ubus call system watchdog '{"stop":true}'""", timeout_ops=15)
    new_ssh = await _wait_for_reboot_and_ssh(host, password, case_id=case_id)
    try:
        await _assert_post_reboot_services(new_ssh, case_id=case_id)
        return await _publish_ssh(new_ssh)
    except Exception:
        await _close_ssh(new_ssh)
        raise


async def assert_process_10_restart_logging(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_10",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh,
        host=host,
        password=password,
        case_id=case_id,
        signal="SEGV",
        crashes_per_service=1,
        check_logs=True,
    )


async def assert_process_11_stress_under_load(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_11",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh,
        host=host,
        password=password,
        case_id=case_id,
        signal="SEGV",
        crashes_per_service=1,
        under_stress=True,
    )


async def assert_process_12_log_integrity(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_12",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh,
        host=host,
        password=password,
        case_id=case_id,
        signal="SEGV",
        crashes_per_service=2,
        check_logs=True,
    )


async def assert_process_13_unauthorized_kill_bts(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_13",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    await _assert_unauthorized_kill_all_services(ssh, case_id=case_id)
    return ssh


async def assert_process_14_dependency_handling(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_14",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    child_before = await _ensure_service_present(ssh, DEPENDENCY_CHILD)
    parent_before = await _ensure_service_present(ssh, DEPENDENCY_PARENT)
    parent_result, ssh = await _crash_and_verify_restart_maybe_reconnect(
        ssh,
        DEPENDENCY_PARENT,
        host=host,
        password=password,
        case_id=case_id,
        signal="SEGV",
        recovery_timeout_s=NETIFD_RECOVERY_S,
    )
    child_after = await _ensure_service_present(ssh, DEPENDENCY_CHILD)
    parent_after = await _ensure_service_present(ssh, DEPENDENCY_PARENT)
    if not child_after.running:
        _fail_case(
            case_id,
            f"Dependent process {DEPENDENCY_CHILD} did not start after {DEPENDENCY_PARENT} restart",
        )
    if child_after.total_crashes < child_before.total_crashes:
        _fail_case(
            case_id,
            f"{DEPENDENCY_CHILD} crash counter did not reflect parent restart "
            f"({child_before.total_crashes} -> {child_after.total_crashes})",
        )
    if parent_after.total_crashes <= parent_before.total_crashes:
        _fail_case(
            case_id,
            f"{DEPENDENCY_PARENT} crash counter did not increment after SEGV "
            f"({parent_before.total_crashes} -> {parent_after.total_crashes})",
        )
    _log(
        case_id,
        f"{DEPENDENCY_PARENT} restart relaunched {DEPENDENCY_CHILD} "
        f"(crashes {child_before.total_crashes}->{child_after.total_crashes}).",
    )
    _report_core_gaps(case_id, parent_result)
    return await _publish_ssh(ssh)


async def assert_process_15_monitor_recovery(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_15",
) -> AsyncGenericDriver:
    ssh = await prepare_procmon_case(ssh, host=host, password=password, case_id=case_id)
    await _disarm_recovery_reboot(ssh, case_id=case_id)
    await _ssh_run(ssh, "kill -11 1", timeout_ops=10)
    new_ssh = await _wait_for_reboot_and_ssh(host, password, case_id=case_id, up_timeout_s=300)
    try:
        await _assert_post_reboot_services(new_ssh, case_id=case_id)
        return await _publish_ssh(new_ssh)
    except Exception:
        await _close_ssh(new_ssh)
        raise


async def assert_process_16_kill_single(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_16",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="KILL", crashes_per_service=1
    )


async def assert_process_17_kill_multiple(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_17",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="KILL", crashes_per_service=2
    )


async def assert_process_18_kill_critical(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_18",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh, host=host, password=password, case_id=case_id, signal="KILL", crashes_per_service=1
    )


async def assert_process_19_kill_under_load(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_19",
) -> AsyncGenericDriver:
    return await _run_all_services_crash_case(
        ssh,
        host=host,
        password=password,
        case_id=case_id,
        signal="KILL",
        crashes_per_service=1,
        under_stress=True,
    )

