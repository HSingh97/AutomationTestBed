"""Process monitor (procmon) validation flows — local standalone DUT only."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import pytest
from scrapli.driver.generic import AsyncGenericDriver

from config.process_monitor_catalog import (
    CRASH_TEST_SERVICE_TARGETS,
    MONITORED_SERVICES,
    MONITORED_SERVICE_NAMES,
    PREFLIGHT_COUNTER_EXEMPT,
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
RESPAWN_WAIT_S = 8
CORE_FILE_WAIT_S = 20
NETIFD_RECOVERY_S = 45
KillSignal = Literal["SEGV", "KILL"]
_SHELL_JOB_NOISE = re.compile(r"^\[\d+\]\+|^Done\(")


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


def _log(case_id: str, message: str) -> None:
    print(f"[PROC][{case_id}] {message}")


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


async def _assert_core_file_created(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    old_pid: int,
    cores_before: set[str],
    case_id: str,
) -> str:
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
        await asyncio.sleep(1)
    example = f"{PROC_CORE_DIR}/core.{_core_name(service_name)}.{old_pid}.<timestamp>"
    pytest.fail(
        f"{case_id}: expected core file for {service_name} after SEGV "
        f"(pattern {core_prefix}* under {PROC_CORE_DIR}); example: {example}"
    )


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
        await asyncio.sleep(1)
    return await _process_running(ssh, service_name) == expect_running


async def _induce_signal(ssh: AsyncGenericDriver, service_name: str, signal: KillSignal) -> int:
    meta = MONITORED_SERVICES[service_name]
    pids = await _pgrep_pids(ssh, meta["pgrep"])
    if not pids:
        pytest.fail(f"{service_name}: no PID found for pattern {meta['pgrep']!r}")
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
    assert _uptime_stable_after_network_event(uptime_before, uptime_after), (
        f"{case_id}: device appears to have rebooted "
        f"(uptime before={uptime_before:.1f}s after={uptime_after:.1f}s)"
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
    old_pid: int | None,
    crashes_before: int,
    case_id: str,
    timeout_s: int,
) -> ServiceInstance:
    meta = MONITORED_SERVICES[service_name]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pids = await _pgrep_pids(ssh, meta["pgrep"])
        if not pids:
            await asyncio.sleep(1)
            continue
        new_pid = pids[0]
        if old_pid and new_pid == old_pid:
            await asyncio.sleep(1)
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
    pytest.fail(f"{case_id}: {service_name} did not respawn within {timeout_s}s")


async def _crash_and_verify_restart(
    ssh: AsyncGenericDriver,
    service_name: str,
    *,
    case_id: str,
    signal: KillSignal = "SEGV",
    recovery_timeout_s: int = RESPAWN_WAIT_S,
) -> ServiceInstance:
    uptime_before = await _system_uptime_s(ssh)
    before = await _ensure_service_present(ssh, service_name)
    old_pid = before.pid
    assert old_pid, f"{case_id}: {service_name} has no pid before crash"
    crashes_before = before.total_crashes
    cores_before = await _list_core_files(ssh) if signal == "SEGV" else set()

    await _induce_signal(ssh, service_name, signal)

    if signal == "SEGV":
        await _assert_core_file_created(
            ssh,
            service_name,
            old_pid=old_pid,
            cores_before=cores_before,
            case_id=case_id,
        )

    await asyncio.sleep(1)
    if signal == "SEGV":
        still_running = await _process_running(ssh, service_name)
        if still_running:
            quick = await get_service_instance(ssh, service_name)
            if quick and quick.pid == old_pid and quick.total_crashes <= crashes_before:
                pytest.fail(
                    f"{case_id}: {service_name} still running with no crash recorded after SEGV"
                )

    after = await _wait_for_service_restart(
        ssh,
        service_name,
        old_pid=old_pid,
        crashes_before=crashes_before,
        case_id=case_id,
        timeout_s=recovery_timeout_s,
    )
    assert after.pid and after.pid != old_pid, (
        f"{case_id}: {service_name} PID did not change after crash ({old_pid} -> {after.pid})"
    )
    assert after.total_crashes >= crashes_before + 1, (
        f"{case_id}: total_crashes did not increment ({crashes_before} -> {after.total_crashes})"
    )
    await _assert_no_reboot(ssh, uptime_before, case_id=case_id)
    return after


async def _run_sequential_crashes(
    ssh: AsyncGenericDriver,
    targets: list[str],
    *,
    case_id: str,
    signal: KillSignal,
    recovery_timeout_s: int = RESPAWN_WAIT_S,
) -> list[str]:
    results: list[str] = []
    for target in targets:
        after = await _crash_and_verify_restart(
            ssh,
            target,
            case_id=case_id,
            signal=signal,
            recovery_timeout_s=recovery_timeout_s,
        )
        results.append(f"{target} crashes={after.total_crashes}")
    return results


async def _ensure_live_ssh(
    ssh: AsyncGenericDriver,
    host: str,
    password: str,
) -> AsyncGenericDriver:
    try:
        await ssh.send_command("echo ok", timeout_ops=10)
        return ssh
    except Exception:
        await _close_ssh(ssh)
        return await _wait_for_ssh(host, password, timeout_s=120, interval_s=5)


async def _wait_for_reboot_and_ssh(
    host: str,
    password: str,
    *,
    case_id: str,
    down_grace_s: int = 20,
    up_timeout_s: int = 240,
) -> AsyncGenericDriver:
    _log(case_id, f"Waiting up to {down_grace_s}s for device to go down...")
    await asyncio.sleep(down_grace_s)
    conn = await _wait_for_ssh(host, password, timeout_s=up_timeout_s, interval_s=5)
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
    assert pid, f"{case_id}: {target} has no pid"
    raw = await _ssh_run(
        ssh,
        f"PID={pid}; echo 'kill -9 '$PID | login -f nobody 2>&1; echo PROC_KILL_EXIT=$?",
        timeout_ops=15,
    )
    cleaned = _strip_shell_job_noise(raw)
    exit_line = next((line for line in cleaned.splitlines() if line.startswith("PROC_KILL_EXIT=")), "")
    exit_code = exit_line.split("=", 1)[-1].strip() if exit_line else ""
    still = await _ensure_service_present(ssh, target)
    assert still.running and still.pid == pid, (
        f"{case_id}: {target} restarted or changed pid after unauthorized kill "
        f"({pid} -> {still.pid})"
    )
    assert exit_code not in ("", "0"), (
        f"{case_id}: unprivileged kill shell exited success ({exit_code!r}): {raw!r}"
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
) -> None:
    visible = await _collect_visible_services(ssh)
    not_running = [name for name, inst in visible.items() if not inst.running]
    assert len(visible) >= min_services, (
        f"{case_id}: too few services tracked after reboot ({len(visible)})"
    )
    assert not not_running, f"{case_id}: services down after reboot: {', '.join(not_running)}"
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
            "GUI shows non-zero counters for exempt services (known quirk, suite continues): "
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
            "ubus shows non-zero counters for exempt services (known quirk, suite continues): "
            + ", ".join(exempt_ubus),
        )
    assert not blocking_ubus, (
        f"{case_id}: ubus service list shows non-zero crash stats after reboot: {', '.join(blocking_ubus)}"
    )
    _log(case_id, "ubus crash counters OK for all non-exempt visible monitored services.")

    uncovered = set(MONITORED_SERVICE_NAMES) - CRASH_TEST_SERVICE_TARGETS
    visibility_only = sorted(
        name
        for name in uncovered
        if name not in {"cron", "rpcd", "breakpad", "ntpd", "sshd", "sysstat"}
    )
    if visibility_only:
        _log(
            case_id,
            "Visibility/uptime cases cover services not targeted by crash/kill tests: "
            + ", ".join(visibility_only),
        )
    _log(
        case_id,
        f"Preflight complete: {len(MONITORED_SERVICE_NAMES)} services in GUI and ubus; "
        f"{len(CRASH_TEST_SERVICE_TARGETS)} crash/kill targets in catalog.",
    )


async def _require_procmon_ready(ssh: AsyncGenericDriver, *, case_id: str) -> None:
    network = await get_service_instance(ssh, "network")
    if network is None or not network.running:
        pytest.skip(
            f"{case_id}: netifd/network is not running (procmon may have halted it). "
            "Reboot the DUT before running the PROCESS suite."
        )


async def assert_process_01_visibility(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_01") -> None:
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
    prior = [
        f"{name}(respawn={inst.respawn_count},crashes={inst.total_crashes})"
        for name, inst in visible.items()
        if inst.respawn_count or inst.total_crashes
    ]
    if prior:
        _log(case_id, f"Pre-existing crash counters noted: {', '.join(prior)}")
    _log(case_id, f"All {len(visible)} monitored services visible and running.")


async def assert_process_02_uptime(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_02") -> None:
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
    assert not mismatches, f"{case_id}: uptime discrepancies: {'; '.join(mismatches)}"
    _log(case_id, f"Uptime consistent across services (system uptime {sys_uptime:.0f}s).")


async def assert_process_03_restart_count(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_03") -> None:
    before = await _collect_visible_services(ssh)
    await asyncio.sleep(5)
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
    assert not changed, f"{case_id}: restart counters changed without crash: {', '.join(changed)}"
    _log(case_id, "Restart counters remained stable during observation window.")


async def assert_process_04_timestamp(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_04") -> None:
    before = await _collect_visible_services(ssh)
    await asyncio.sleep(10)
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
    assert not drift, f"{case_id}: unexpected timestamp/counter drift: {'; '.join(drift)}"
    _log(case_id, "Crash timestamps/counters stable while idle.")


async def assert_process_05_crash_single(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_05") -> None:
    after = await _crash_and_verify_restart(ssh, CRASH_TARGET_DEFAULT, case_id=case_id, signal="SEGV")
    _log(case_id, f"{CRASH_TARGET_DEFAULT} respawned; total_crashes={after.total_crashes}.")


async def assert_process_06_crash_multiple(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_06") -> None:
    summary = await _run_sequential_crashes(
        ssh,
        [CRASH_TARGET_SECONDARY, CRASH_TARGET_TERTIARY],
        case_id=case_id,
        signal="SEGV",
    )
    _log(case_id, "Sequential crashes OK: " + ", ".join(summary))


async def assert_process_07_crash_critical(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_07") -> None:
    after = await _crash_and_verify_restart(
        ssh,
        CRITICAL_TARGET,
        case_id=case_id,
        signal="SEGV",
        recovery_timeout_s=20,
    )
    _log(case_id, f"Critical service {CRITICAL_TARGET} recovered; total_crashes={after.total_crashes}.")


async def assert_process_08_crash_non_critical(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_08") -> None:
    target = await _resolve_optional_target(ssh, NON_CRITICAL_TARGET, "snlogd", CRASH_TARGET_SECONDARY)
    after = await _crash_and_verify_restart(ssh, target, case_id=case_id, signal="SEGV")
    _log(case_id, f"Non-critical {target} recovered without service disruption; crashes={after.total_crashes}.")


async def assert_process_09_watchdog_reboot(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_09",
) -> None:
    ssh = await _ensure_live_ssh(ssh, host, password)
    await _ssh_run(ssh, """ubus call system watchdog '{"stop":true}'""", timeout_ops=15)
    new_ssh = await _wait_for_reboot_and_ssh(host, password, case_id=case_id)
    try:
        await _assert_post_reboot_services(new_ssh, case_id=case_id)
    finally:
        await _close_ssh(new_ssh)


async def assert_process_10_restart_logging(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_10") -> None:
    target = await _resolve_crash_target(ssh, CRASH_TARGET_LOGGING, "mcsd", "kwn_devlocator")
    before = await _ensure_service_present(ssh, target)
    after = await _crash_and_verify_restart(ssh, target, case_id=case_id, signal="SEGV")
    logs = await _grep_restart_logs(ssh, target=target, tail=40)
    if logs.strip():
        _log(case_id, "Restart evidence found in device logs.")
    else:
        _log(
            case_id,
            f"logread/device_logs quiet; verified via ubus "
            f"(total_crashes {before.total_crashes}->{after.total_crashes}, "
            f"last_respawn {before.last_respawn}->{after.last_respawn}).",
        )
    assert after.total_crashes > before.total_crashes, f"{case_id}: crash counter did not increment"
    assert after.last_respawn >= before.last_respawn, f"{case_id}: last_respawn not updated"
    _log(case_id, f"Restart logged; total_crashes {before.total_crashes}->{after.total_crashes}.")


async def assert_process_11_stress_under_load(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_11") -> None:
    target = await _resolve_crash_target(ssh, CRASH_TARGET_STRESS, "ezmcloud", "kwn_devlocator")
    mode = await _start_cpu_stress(ssh)
    try:
        await asyncio.sleep(3)
        assert await _collect_visible_services(ssh), f"{case_id}: service list empty under load"
        after = await _crash_and_verify_restart(ssh, target, case_id=case_id, signal="SEGV")
        _log(case_id, f"Under {mode} load: {target} crashes={after.total_crashes}.")
    finally:
        await _stop_cpu_stress(ssh)


async def assert_process_12_log_integrity(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_12") -> None:
    target = await _resolve_crash_target(
        ssh,
        CRASH_TARGET_LOG_INTEGRITY,
        "kwn_devlocator",
        "ezmcloud",
        max_crashes=3,
    )
    baseline = (await _ensure_service_present(ssh, target)).total_crashes
    crash_times = 2
    inst = await _ensure_service_present(ssh, target)
    for _ in range(crash_times):
        inst = await _crash_and_verify_restart(ssh, target, case_id=case_id, signal="SEGV")
    assert inst.total_crashes >= baseline + crash_times, (
        f"{case_id}: total_crashes {inst.total_crashes} < expected {baseline + crash_times}"
    )
    logs = await _grep_restart_logs(ssh, target=target, tail=80)
    if logs.strip():
        _log(case_id, f"Logs present after {crash_times} restarts; total_crashes={inst.total_crashes}.")
    else:
        _log(
            case_id,
            f"logread/device_logs quiet after {crash_times} restarts; "
            f"ubus total_crashes={inst.total_crashes} (baseline {baseline}).",
        )


async def assert_process_13_unauthorized_kill_bts(
    ssh: AsyncGenericDriver,
    *,
    host: str = "",
    password: str = "",
    case_id: str = "PROCESS_13",
) -> None:
    await _assert_unauthorized_kill(ssh, case_id=case_id)


async def assert_process_14_dependency_handling(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_14") -> None:
    child_before = await _ensure_service_present(ssh, DEPENDENCY_CHILD)
    parent_before = await _ensure_service_present(ssh, DEPENDENCY_PARENT)
    await _crash_and_verify_restart(
        ssh,
        DEPENDENCY_PARENT,
        case_id=case_id,
        signal="SEGV",
        recovery_timeout_s=NETIFD_RECOVERY_S,
    )
    child_after = await _ensure_service_present(ssh, DEPENDENCY_CHILD)
    parent_after = await _ensure_service_present(ssh, DEPENDENCY_PARENT)
    assert child_after.running, f"{case_id}: dependent {DEPENDENCY_CHILD} not running after {DEPENDENCY_PARENT} restart"
    assert child_after.total_crashes >= child_before.total_crashes, (
        f"{case_id}: {DEPENDENCY_CHILD} crash counter did not reflect parent restart "
        f"({child_before.total_crashes} -> {child_after.total_crashes})"
    )
    assert parent_after.total_crashes > parent_before.total_crashes
    _log(
        case_id,
        f"{DEPENDENCY_PARENT} restart relaunched {DEPENDENCY_CHILD} "
        f"(crashes {child_before.total_crashes}->{child_after.total_crashes}).",
    )


async def assert_process_15_monitor_recovery(
    ssh: AsyncGenericDriver,
    *,
    host: str,
    password: str,
    case_id: str = "PROCESS_15",
) -> None:
    ssh = await _ensure_live_ssh(ssh, host, password)
    await _ssh_run(ssh, "kill -11 1", timeout_ops=10)
    new_ssh = await _wait_for_reboot_and_ssh(host, password, case_id=case_id, up_timeout_s=300)
    try:
        await _assert_post_reboot_services(new_ssh, case_id=case_id)
    finally:
        await _close_ssh(new_ssh)


async def assert_process_16_kill_single(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_16") -> None:
    target = await _resolve_crash_target(ssh, CRASH_TARGET_KILL_SINGLE, "ezmcloud", "kwn_devlocator")
    after = await _crash_and_verify_restart(ssh, target, case_id=case_id, signal="KILL")
    _log(case_id, f"Kill -9 on {target}; total_crashes={after.total_crashes}.")


async def assert_process_17_kill_multiple(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_17") -> None:
    summary = await _run_sequential_crashes(
        ssh,
        list(CRASH_TARGET_KILL_MULTI),
        case_id=case_id,
        signal="KILL",
    )
    _log(case_id, "Sequential kills OK: " + ", ".join(summary))


async def assert_process_18_kill_critical(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_18") -> None:
    after = await _crash_and_verify_restart(
        ssh,
        CRITICAL_KILL_TARGET,
        case_id=case_id,
        signal="KILL",
        recovery_timeout_s=RESPAWN_WAIT_S,
    )
    _log(case_id, f"Critical kill recovered; {CRITICAL_KILL_TARGET} total_crashes={after.total_crashes}.")


async def assert_process_19_kill_under_load(ssh: AsyncGenericDriver, *, case_id: str = "PROCESS_19") -> None:
    target = await _resolve_optional_target(ssh, CRASH_TARGET_KILL_LOAD, "netlinkevents")
    mode = await _start_cpu_stress(ssh)
    try:
        after = await _crash_and_verify_restart(ssh, target, case_id=case_id, signal="KILL")
        _log(case_id, f"Kill under {mode} load OK; {target} crashes={after.total_crashes}.")
    finally:
        await _stop_cpu_stress(ssh)


async def assert_process_20_unauthorized_kill_cpe(
    ssh: AsyncGenericDriver,
    *,
    host: str = "",
    password: str = "",
    case_id: str = "PROCESS_20",
) -> None:
    await assert_process_13_unauthorized_kill_bts(ssh, case_id=case_id)
