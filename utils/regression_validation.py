"""Log and link validation for stability regression iterations."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import shlex

from pages.commands import RootCommands
from scrapli.driver.generic import AsyncGenericDriver

from utils.parsers import clean_ssh_output


@dataclass
class RegressionValidation:
    """Customer-facing validation evidence for one regression iteration."""

    passed: bool = True
    partial: bool = False
    summary: str = ""
    events: list[dict[str, str]] = field(default_factory=list)
    device_time_before: str = ""
    device_time_after: str = ""
    uptime_before_s: float | None = None
    uptime_after_s: float | None = None
    reboot_confirmed: bool | None = None
    uptime_stable: bool | None = None
    ping_recovery_seconds: float | None = None
    ping_recovery_limit_s: int | None = None
    link_dropped: bool | None = None
    link_reestablished: bool | None = None
    link_restore_seconds: float | None = None
    link_uptime_max_s: int | None = None
    wifi_events_log_path: str = ""
    device_logs_validation: bool = False
    device_logs_ok: bool | None = None
    wireless_logs_ok: bool | None = None
    links_before: str = ""
    links_after: str = ""
    device_log_excerpt: str = ""
    wireless_log_excerpt: str = ""
    system_log_excerpt: str = ""
    log_excerpt: str = ""

    def add_event(self, step: str, detail: str) -> None:
        self.events.append(
            {
                "time": time.strftime("%H:%M:%S"),
                "step": step,
                "detail": detail,
            }
        )

    def build_combined_log_excerpt(self) -> None:
        if self.device_logs_validation and self.device_log_excerpt:
            self.log_excerpt = f"=== Device logs ===\n{self.device_log_excerpt}"
            return
        if self.wifi_events_log_path and (self.wireless_log_excerpt or self.log_excerpt):
            body = self.wireless_log_excerpt or self.log_excerpt
            self.log_excerpt = f"=== WiFi link events ===\n{body}"
            return
        sections = []
        if self.device_log_excerpt:
            sections.append(f"=== Device logs (/etc/device_logs) ===\n{self.device_log_excerpt}")
        if self.wireless_log_excerpt:
            sections.append(f"=== Wireless / network logs ===\n{self.wireless_log_excerpt}")
        if self.system_log_excerpt:
            sections.append(f"=== System logread (filtered) ===\n{self.system_log_excerpt}")
        self.log_excerpt = "\n\n".join(sections)

    def to_dict(self) -> dict[str, Any]:
        self.build_combined_log_excerpt()
        return {
            "passed": self.passed,
            "partial": self.partial,
            "summary": self.summary,
            "events": list(self.events),
            "device_time_before": self.device_time_before,
            "device_time_after": self.device_time_after,
            "uptime_before_s": self.uptime_before_s,
            "uptime_after_s": self.uptime_after_s,
            "reboot_confirmed": self.reboot_confirmed,
            "uptime_stable": self.uptime_stable,
            "ping_recovery_seconds": self.ping_recovery_seconds,
            "ping_recovery_limit_s": self.ping_recovery_limit_s,
            "link_dropped": self.link_dropped,
            "link_reestablished": self.link_reestablished,
            "link_restore_seconds": self.link_restore_seconds,
            "wifi_events_log_path": self.wifi_events_log_path,
            "device_logs_validation": self.device_logs_validation,
            "link_uptime_max_s": self.link_uptime_max_s,
            "device_logs_ok": self.device_logs_ok,
            "wireless_logs_ok": self.wireless_logs_ok,
            "links_before": self.links_before,
            "links_after": self.links_after,
            "device_log_excerpt": self.device_log_excerpt,
            "wireless_log_excerpt": self.wireless_log_excerpt,
            "system_log_excerpt": self.system_log_excerpt,
            "log_excerpt": self.log_excerpt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RegressionValidation:
        if not data:
            return cls()
        obj = cls(
            passed=bool(data.get("passed", True)),
            partial=bool(data.get("partial")),
            summary=str(data.get("summary") or ""),
            events=list(data.get("events") or []),
            device_time_before=str(data.get("device_time_before") or ""),
            device_time_after=str(data.get("device_time_after") or ""),
            uptime_before_s=data.get("uptime_before_s"),
            uptime_after_s=data.get("uptime_after_s"),
            reboot_confirmed=data.get("reboot_confirmed"),
            uptime_stable=data.get("uptime_stable"),
            ping_recovery_seconds=data.get("ping_recovery_seconds"),
            ping_recovery_limit_s=data.get("ping_recovery_limit_s"),
            link_dropped=data.get("link_dropped"),
            link_reestablished=data.get("link_reestablished"),
            link_restore_seconds=data.get("link_restore_seconds"),
            wifi_events_log_path=str(data.get("wifi_events_log_path") or ""),
            device_logs_validation=bool(data.get("device_logs_validation")),
            link_uptime_max_s=data.get("link_uptime_max_s"),
            device_logs_ok=data.get("device_logs_ok"),
            wireless_logs_ok=data.get("wireless_logs_ok"),
            links_before=str(data.get("links_before") or ""),
            links_after=str(data.get("links_after") or ""),
            device_log_excerpt=str(data.get("device_log_excerpt") or ""),
            wireless_log_excerpt=str(data.get("wireless_log_excerpt") or ""),
            system_log_excerpt=str(data.get("system_log_excerpt") or ""),
            log_excerpt=str(data.get("log_excerpt") or ""),
        )
        if not obj.log_excerpt:
            obj.build_combined_log_excerpt()
        return obj


def _log(case_id: str, message: str) -> None:
    print(f"[REGRESSION][{case_id}] {message}")


async def _open_root_ssh(host: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _close_ssh(conn: AsyncGenericDriver | None) -> None:
    if conn is None:
        return
    try:
        await conn.close()
    except Exception:
        pass


async def _ping_once(
    ssh: AsyncGenericDriver,
    target_host: str,
    *,
    count: int = 2,
) -> tuple[bool, str]:
    if ":" in target_host:
        command = f"ping -6 -c {count} {shlex.quote(target_host)}"
    else:
        command = f"ping -c {count} {shlex.quote(target_host)}"
    try:
        result = await ssh.send_command(command, timeout_ops=30)
        output = str(result.result or "").lower()
        if "100% packet loss" in output or "bytes from" not in output:
            return False, output.strip()[:200]
        return True, "replies received"
    except Exception as exc:
        return False, str(exc)


def regression_timeouts(profile_bundle) -> dict[str, int]:
    reg = profile_bundle.active.get("regression", {})
    return {
        "soft_reboot_ping_s": int(reg.get("soft_reboot_ping_timeout_seconds", 180)),
        "firmware_ping_s": int(reg.get("firmware_ping_timeout_seconds", 420)),
        "soft_reset_link_uptime_s": int(reg.get("soft_reset_link_uptime_max_seconds", 90)),
        "radio_idx": int(reg.get("radio_idx", 1)),
    }


def _parse_uptime_seconds(raw: str) -> float | None:
    cleaned = clean_ssh_output(raw)
    if not cleaned:
        return None
    try:
        return float(cleaned.split()[0])
    except (TypeError, ValueError):
        return None


def _parse_remote_partner_count(raw: str) -> int:
    cleaned = clean_ssh_output(raw)
    if not cleaned:
        return 0
    match = re.search(r"(\d+)", cleaned)
    return int(match.group(1)) if match else 0


def _uptime_indicates_reboot(before: float | None, after: float | None) -> bool:
    if before is None or after is None:
        return False
    if after < 90:
        return before > after + 20
    drop = before - after
    return drop > 60 or after < before * 0.35


def _uptime_stable_after_network_event(before: float | None, after: float | None) -> bool:
    """Network reload should not reboot — uptime should stay high."""
    if before is None or after is None:
        return True
    return after >= before - 120


def _text_has_reboot_markers(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "reboot",
        "restart",
        "software reset",
        "kernel",
        "jffs2",
        "procd",
        "watchdog",
        "sysinit",
        "starting kernel",
        "umount",
        "power",
    )
    return any(marker in lowered for marker in markers)


def _text_has_wireless_reset_markers(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "network",
        "reload",
        "wifi",
        "wireless",
        "ath",
        "link",
        "partner",
        "disconnect",
        "connect",
        "kwn",
        "interface",
    )
    return any(marker in lowered for marker in markers)


async def _ssh_run(ssh: AsyncGenericDriver, command: str, *, timeout_ops: int = 45) -> str:
    result = await ssh.send_command(command, timeout_ops=timeout_ops)
    return str(result.result or "")


async def capture_device_snapshot(
    ssh: AsyncGenericDriver,
    *,
    radio_idx: int,
    case_id: str,
    label: str,
) -> dict[str, Any]:
    device_time = clean_ssh_output(await _ssh_run(ssh, RootCommands.GET_LOCAL_TIME))
    uptime_raw = await _ssh_run(ssh, RootCommands.GET_UPTIME)
    uptime_s = _parse_uptime_seconds(uptime_raw)
    partners_raw = await _ssh_run(ssh, RootCommands.get_remote_partners(radio_idx))
    partners = _parse_remote_partner_count(partners_raw)
    links_raw = await _ssh_run(
        ssh, f"cat /sys/class/kwn/wifi{radio_idx}/statistics/links 2>/dev/null || echo 'n/a'"
    )
    links = clean_ssh_output(links_raw) or "n/a"
    _log(case_id, f"{label}: time={device_time} uptime={uptime_s}s partners={partners}")
    return {
        "device_time": device_time,
        "uptime_s": uptime_s,
        "remote_partners": partners,
        "links": links,
    }


async def capture_reboot_logs(ssh: AsyncGenericDriver) -> tuple[str, str, str]:
    device_tail = clean_ssh_output(await _ssh_run(ssh, RootCommands.GET_DEVICE_LOGS_TAIL))
    device_reboot = clean_ssh_output(await _ssh_run(ssh, RootCommands.GET_DEVICE_LOGS_REBOOT_GREP))
    device_block = "\n".join(part for part in (device_reboot, device_tail) if part).strip()

    system_reboot = clean_ssh_output(await _ssh_run(ssh, RootCommands.GET_LOGREAD_REBOOT_GREP))
    if not system_reboot:
        system_reboot = clean_ssh_output(
            await _ssh_run(ssh, "logread 2>/dev/null | tail -n 80", timeout_ops=60)
        )
    return device_block, "", system_reboot


def wifi_events_log_path(radio_idx: int) -> str:
    return f"/tmp/kwn-wifi{radio_idx}-events.log"


def _wifi_event_lines(log_text: str) -> list[str]:
    return [line.strip() for line in log_text.splitlines() if line.strip()]


def _new_event_lines_since(before: list[str], after: list[str]) -> list[str]:
    if len(after) > len(before):
        return after[len(before) :]
    before_set = set(before)
    return [line for line in after if line not in before_set]


def _is_link_terminate_line(line: str) -> bool:
    return "terminated link" in line.lower()


def _is_link_establish_line(line: str) -> bool:
    return "established link" in line.lower()


def _pair_link_events_from_lines(lines: list[str]) -> tuple[str | None, str | None]:
    """Last terminate then first establish after it (one soft-reset cycle)."""
    terminate: str | None = None
    establish: str | None = None
    for line in lines:
        if _is_link_terminate_line(line):
            terminate = line
            establish = None
        elif terminate and _is_link_establish_line(line):
            establish = line
            break
    return terminate, establish


def _latest_terminate_establish_pair(lines: list[str]) -> tuple[str | None, str | None]:
    """Most recent establish in the log, with the terminate line before it."""
    establish_idx: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        if _is_link_establish_line(lines[i]):
            establish_idx = i
            break
    if establish_idx is None:
        return None, None
    terminate: str | None = None
    for i in range(establish_idx - 1, -1, -1):
        if _is_link_terminate_line(lines[i]):
            terminate = lines[i]
            break
    return terminate, lines[establish_idx]


def _format_soft_reset_excerpt(
    terminate: str | None, establish: str | None, *, fallback_lines: list[str] | None = None
) -> str:
    if not terminate and not establish:
        if fallback_lines:
            return "\n".join(fallback_lines[-25:])
        return ""
    sections: list[str] = []
    if terminate:
        sections.append(f"--- Link terminated ---\n{terminate}")
    if establish:
        sections.append(f"--- Link re-established ---\n{establish}")
    return "\n\n".join(sections)


def _soft_reset_link_excerpt(
    new_lines: list[str],
    lines_after: list[str],
    lines_before: list[str] | None = None,
    *,
    as_pair: bool = False,
) -> str | tuple[str | None, str | None]:
    """Resolve terminate/establish lines for this cycle (fallback when poll missed capture)."""
    before_set = set(lines_before or [])
    cycle_lines = [line for line in lines_after if line not in before_set]
    terminate, establish = _pair_link_events_from_lines(cycle_lines or new_lines)
    if not establish:
        terminate, establish = _latest_terminate_establish_pair(lines_after)
    if not terminate and not establish:
        terminate, establish = _pair_link_events_from_lines(new_lines)
    if as_pair:
        return terminate, establish
    return _format_soft_reset_excerpt(
        terminate, establish, fallback_lines=new_lines or lines_after
    )


async def read_wifi_events_log(ssh: AsyncGenericDriver, radio_idx: int) -> str:
    raw = await _ssh_run(ssh, RootCommands.get_wifi_events_log(radio_idx), timeout_ops=60)
    return clean_ssh_output(raw) or raw.strip()


def _device_log_lines(log_text: str) -> list[str]:
    return [line.strip() for line in log_text.splitlines() if line.strip()]


async def read_device_logs(ssh: AsyncGenericDriver) -> str:
    raw = await _ssh_run(ssh, RootCommands.GET_DEVICE_LOGS, timeout_ops=60)
    return clean_ssh_output(raw) or raw.strip()


def _is_device_init_success_line(line: str) -> bool:
    lower = line.lower()
    return "device init" in lower and "success" in lower


def _format_device_init_excerpt(init_line: str | None, *, fallback_lines: list[str] | None = None) -> str:
    if init_line:
        return f"--- Device init ---\n{init_line}"
    if fallback_lines:
        return "\n".join(fallback_lines[-25:])
    return ""


async def capture_soft_reboot_snapshot(ssh: AsyncGenericDriver, *, case_id: str, label: str) -> dict[str, Any]:
    """Baseline device log lines before soft reboot."""
    lines = _device_log_lines(await read_device_logs(ssh))
    _log(case_id, f"{label}: device_logs lines={len(lines)}")
    return {"device_log_lines": lines}


async def validate_soft_reboot_iteration(
    *,
    bts_host: str,
    cpe_hosts: list[str],
    password: str,
    ping_timeout_s: int,
    snapshot_before: dict[str, Any],
) -> RegressionValidation:
    """After soft reboot: BTS reachable via ping within limit; Device Init Success in /etc/device_logs."""
    validation = RegressionValidation(
        ping_recovery_limit_s=ping_timeout_s,
        device_logs_validation=True,
    )
    lines_before: list[str] = list(snapshot_before.get("device_log_lines") or [])

    cpe_target = cpe_hosts[0] if cpe_hosts else ""
    start = time.monotonic()
    ping_ok = False
    captured_init: str | None = None

    while time.monotonic() - start < ping_timeout_s:
        elapsed = int(time.monotonic() - start)
        try:
            conn = await _open_root_ssh(bts_host, password)
            try:
                if cpe_target:
                    ping_ok, _detail = await _ping_once(conn, cpe_target, count=2)
                else:
                    await conn.send_command("echo ok")
                    ping_ok = True
                if not captured_init:
                    lines_now = _device_log_lines(await read_device_logs(conn))
                    for line in _new_event_lines_since(lines_before, lines_now):
                        if _is_device_init_success_line(line):
                            captured_init = line
                            break
            finally:
                await _close_ssh(conn)
        except Exception:
            pass
        if ping_ok:
            validation.ping_recovery_seconds = round(time.monotonic() - start, 1)
            break
        await asyncio.sleep(10)

    if not ping_ok:
        validation.passed = False
        validation.ping_recovery_seconds = round(time.monotonic() - start, 1)
        validation.reboot_confirmed = False
        validation.summary = (
            f"Device did not recover within {ping_timeout_s}s "
            f"(waited {validation.ping_recovery_seconds}s)."
        )
        return validation

    if not captured_init:
        try:
            conn = await _open_root_ssh(bts_host, password)
            try:
                lines_after = _device_log_lines(await read_device_logs(conn))
                for line in _new_event_lines_since(lines_before, lines_after):
                    if _is_device_init_success_line(line):
                        captured_init = line
                        break
                if not captured_init:
                    for line in reversed(lines_after):
                        if _is_device_init_success_line(line):
                            captured_init = line
                            break
            finally:
                await _close_ssh(conn)
        except Exception:
            pass

    validation.reboot_confirmed = bool(captured_init)
    validation.device_log_excerpt = _format_device_init_excerpt(captured_init)
    validation.build_combined_log_excerpt()

    if not captured_init:
        validation.passed = False
        validation.partial = True
        validation.summary = (
            f"Ping recovered in {validation.ping_recovery_seconds}s but "
            "no Device Init Success line in /etc/device_logs."
        )
    else:
        validation.summary = (
            f"Soft reboot OK: ping in {validation.ping_recovery_seconds}s "
            f"(limit {ping_timeout_s}s); device init confirmed in logs."
        )
    return validation


async def validate_reboot_iteration(
    *,
    ssh: AsyncGenericDriver,
    bts_host: str,
    cpe_hosts: list[str],
    password: str,
    profile_bundle,
    case_id: str,
    radio_idx: int,
    ping_timeout_s: int,
    snapshot_before: dict[str, Any],
) -> RegressionValidation:
    """After reboot: ping within limit; uptime reset; reboot lines in device/system logs."""
    validation = RegressionValidation(ping_recovery_limit_s=ping_timeout_s)
    validation.device_time_before = str(snapshot_before.get("device_time") or "")
    validation.uptime_before_s = snapshot_before.get("uptime_s")
    validation.links_before = str(snapshot_before.get("links") or "")

    cpe_target = cpe_hosts[0] if cpe_hosts else ""
    start = time.monotonic()
    ping_ok = False
    validation.add_event("Ping recovery", f"Waiting up to {ping_timeout_s}s for BTS→CPE ping")

    while time.monotonic() - start < ping_timeout_s:
        elapsed = int(time.monotonic() - start)
        try:
            conn = await _open_root_ssh(bts_host, password)
            try:
                if cpe_target:
                    ping_ok, _detail = await _ping_once(conn, cpe_target, count=2)
                else:
                    await conn.send_command("echo ok")
                    ping_ok = True
            finally:
                await _close_ssh(conn)
        except Exception as exc:
            validation.add_event("Ping attempt", f"{elapsed}s: {exc}")
        if ping_ok:
            validation.ping_recovery_seconds = round(time.monotonic() - start, 1)
            validation.add_event(
                "Ping recovery",
                f"BTS→CPE reachable in {validation.ping_recovery_seconds}s",
            )
            break
        await asyncio.sleep(10)

    if not ping_ok:
        validation.passed = False
        validation.ping_recovery_seconds = round(time.monotonic() - start, 1)
        validation.summary = (
            f"Ping did not recover within {ping_timeout_s}s "
            f"(waited {validation.ping_recovery_seconds}s)."
        )
        validation.add_event("Ping recovery", validation.summary)
        return validation

    log_ssh = await _open_root_ssh(bts_host, password)
    try:
        after = await capture_device_snapshot(
            log_ssh, radio_idx=radio_idx, case_id=case_id, label="After reboot"
        )
        validation.device_time_after = str(after.get("device_time") or "")
        validation.uptime_after_s = after.get("uptime_s")
        validation.links_after = str(after.get("links") or "")
        device_log, _wireless, system_log = await capture_reboot_logs(log_ssh)
        validation.device_log_excerpt = device_log
        validation.system_log_excerpt = system_log
        validation.build_combined_log_excerpt()
    finally:
        await _close_ssh(log_ssh)

    uptime_before = validation.uptime_before_s
    uptime_after = validation.uptime_after_s
    uptime_reset = _uptime_indicates_reboot(uptime_before, uptime_after)
    device_reboot = _text_has_reboot_markers(validation.device_log_excerpt)
    system_reboot = _text_has_reboot_markers(validation.system_log_excerpt)
    log_reboot = device_reboot or system_reboot

    validation.reboot_confirmed = bool(uptime_reset and log_reboot)
    validation.add_event(
        "Uptime",
        f"before={uptime_before}s after={uptime_after}s "
        f"({'reboot indicated' if uptime_reset else 'no significant reset'})",
    )
    validation.add_event(
        "Device logs",
        f"reboot markers {'found' if device_reboot else 'not found'}",
    )
    validation.add_event(
        "System logread",
        f"reboot markers {'found' if system_reboot else 'not found'}",
    )

    if not uptime_reset:
        validation.passed = False
        validation.summary = (
            f"Uptime did not reset (before={uptime_before}s, after={uptime_after}s)."
        )
    elif not log_reboot:
        validation.passed = False
        validation.summary = (
            "Uptime reset but no reboot evidence in device or system logs."
        )
    else:
        validation.summary = (
            f"Reboot confirmed (uptime {uptime_before}s→{uptime_after}s, logs OK); "
            f"ping in {validation.ping_recovery_seconds}s (limit {ping_timeout_s}s)."
        )
    return validation


async def validate_soft_reset_iteration(
    *,
    ssh: AsyncGenericDriver,
    radio_idx: int,
    case_id: str,
    snapshot_before: dict[str, Any],
    reload_started_at: float,
    max_link_restore_s: int,
) -> RegressionValidation:
    """Verify link terminated then re-established using /tmp/kwn-wifiN-events.log."""
    log_path = wifi_events_log_path(radio_idx)
    validation = RegressionValidation(
        link_uptime_max_s=max_link_restore_s,
        wifi_events_log_path=log_path,
    )
    validation.device_time_before = str(snapshot_before.get("device_time") or "")

    lines_before = _wifi_event_lines(await read_wifi_events_log(ssh, radio_idx))

    terminated_seen = False
    established_seen = False
    captured_terminate: str | None = None
    captured_establish: str | None = None
    deadline = time.monotonic() + max_link_restore_s + 20

    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        lines_now = _wifi_event_lines(await read_wifi_events_log(ssh, radio_idx))
        new_lines = _new_event_lines_since(lines_before, lines_now)
        for line in new_lines:
            if _is_link_terminate_line(line):
                terminated_seen = True
                captured_terminate = line
            if terminated_seen and _is_link_establish_line(line):
                established_seen = True
                captured_establish = line
                validation.link_restore_seconds = round(time.monotonic() - reload_started_at, 1)
                break
        if established_seen:
            break

    lines_after = _wifi_event_lines(await read_wifi_events_log(ssh, radio_idx))
    new_lines = _new_event_lines_since(lines_before, lines_after)
    validation.link_dropped = terminated_seen or any(
        _is_link_terminate_line(line) for line in new_lines
    )
    validation.link_reestablished = established_seen or (
        validation.link_dropped
        and any(_is_link_establish_line(line) for line in new_lines)
    )

    if not validation.link_restore_seconds and validation.link_reestablished:
        validation.link_restore_seconds = round(time.monotonic() - reload_started_at, 1)

    terminate_line, establish_line = captured_terminate, captured_establish
    if not establish_line or not terminate_line:
        t2, e2 = _soft_reset_link_excerpt(
            new_lines, lines_after, lines_before, as_pair=True
        )
        terminate_line = terminate_line or t2
        establish_line = establish_line or e2
    validation.wireless_log_excerpt = _format_soft_reset_excerpt(terminate_line, establish_line)
    validation.build_combined_log_excerpt()

    failures: list[str] = []
    if not validation.link_dropped:
        failures.append("link did not terminate after network reload")
    if not validation.link_reestablished:
        failures.append("link did not re-establish after terminate")
    elif (
        validation.link_restore_seconds is not None
        and validation.link_restore_seconds > max_link_restore_s
    ):
        failures.append(
            f"link restore took {validation.link_restore_seconds}s (limit {max_link_restore_s}s)"
        )

    if failures:
        validation.passed = False
        validation.partial = True
        validation.summary = "; ".join(failures).capitalize() + "."
    else:
        validation.summary = (
            f"Soft reset OK: link terminated and re-established in "
            f"{validation.link_restore_seconds}s."
        )
    return validation
