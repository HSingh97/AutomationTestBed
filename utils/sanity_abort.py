"""Sanity-only backend checks for abort-midway firmware upload (SANITY_05)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from utils.sanity_firmware import read_fw_version
from utils.sanity_ssh import ensure_sanity_ssh_open, read_sanity_boot_id, sanity_ssh_run

LIST_FW_STAGING = (
    "find /tmp /var/tmp -maxdepth 3 \\( "
    "-name '*.tgz' -o -name '*.bin' -o -name '*firmware*' -o -name '*upload*' "
    "-o -name 'sysupgrade*' \\) -type f 2>/dev/null | sort"
)

FLASH_PROCESSES = (
    "ps w 2>/dev/null | grep -E '[s]ysupgrade|[f]w_|flashops|/tmp/.*\\.(tgz|bin)' || true"
)

UPTIME_SECONDS = "cat /proc/uptime 2>/dev/null | awk '{print int($1)}'"


@dataclass(frozen=True)
class AbortUpgradeBackendSnap:
    firmware: str
    boot_id: str
    uptime_s: int
    staging_files: tuple[str, ...]
    flash_processes: str


@dataclass(frozen=True)
class AbortUpgradeDeviceResult:
    label: str
    model: str
    image: str
    firmware_before: str
    firmware_after: str
    boot_unchanged: bool
    staging_clean: bool
    flash_idle: bool
    upload_started: bool
    upload_aborted: bool

    @property
    def firmware_unchanged(self) -> bool:
        return bool(self.firmware_after) and self.firmware_before == self.firmware_after

    @property
    def overall(self) -> str:
        ok = (
            self.firmware_unchanged
            and self.boot_unchanged
            and self.staging_clean
            and self.flash_idle
            and self.upload_started
            and self.upload_aborted
        )
        return "PASS" if ok else "FAIL"


def _parse_staging(raw: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in (raw or "").splitlines() if line.strip())


def _parse_uptime(raw: str) -> int:
    try:
        return int((raw or "").strip())
    except ValueError:
        return 0


def staging_is_clean(baseline: tuple[str, ...], current: tuple[str, ...]) -> bool:
    """No new staging files compared to pre-upload baseline."""
    return not (set(current) - set(baseline))


def flash_processes_idle(text: str) -> bool:
    return not (text or "").strip()


async def capture_abort_upgrade_backend(ssh: AsyncGenericDriver) -> AbortUpgradeBackendSnap:
    await ensure_sanity_ssh_open(ssh)
    firmware = await read_fw_version(ssh)
    boot_id = await read_sanity_boot_id(ssh)
    uptime_s = _parse_uptime(await sanity_ssh_run(ssh, UPTIME_SECONDS, timeout_s=15))
    staging_files = _parse_staging(await sanity_ssh_run(ssh, LIST_FW_STAGING, timeout_s=30))
    flash_processes = (await sanity_ssh_run(ssh, FLASH_PROCESSES, timeout_s=15)).strip()
    return AbortUpgradeBackendSnap(
        firmware=firmware,
        boot_id=boot_id,
        uptime_s=uptime_s,
        staging_files=staging_files,
        flash_processes=flash_processes,
    )


async def wait_abort_upgrade_backend_stable(
    ssh: AsyncGenericDriver,
    baseline: AbortUpgradeBackendSnap,
    *,
    timeout_s: int = 60,
    poll_s: int = 5,
) -> AbortUpgradeBackendSnap:
    deadline = time.monotonic() + timeout_s
    last = baseline
    while time.monotonic() < deadline:
        last = await capture_abort_upgrade_backend(ssh)
        if staging_is_clean(baseline.staging_files, last.staging_files) and flash_processes_idle(
            last.flash_processes
        ):
            return last
        await asyncio.sleep(poll_s)
    return last


def print_abort_backend_snap(title: str, snap: AbortUpgradeBackendSnap) -> None:
    staging = ", ".join(snap.staging_files) if snap.staging_files else "(none)"
    procs = snap.flash_processes or "(none)"
    print(f"\n--- {title} ---", flush=True)
    print(f"  firmware       : {snap.firmware or '—'}", flush=True)
    print(f"  boot_id        : {snap.boot_id or '—'}", flush=True)
    print(f"  uptime_s       : {snap.uptime_s}", flush=True)
    print(f"  staging_files  : {staging}", flush=True)
    print(f"  flash_processes: {procs}", flush=True)


def assert_abort_upgrade_backend_stable(
    before: AbortUpgradeBackendSnap,
    after: AbortUpgradeBackendSnap,
    *,
    case_id: str,
    label: str,
    quiet: bool = False,
) -> tuple[bool, bool, bool]:
    """Run backend checks. Returns (fw_unchanged, boot_unchanged, staging_clean)."""
    if not quiet:
        print_abort_backend_snap(f"{case_id} — {label} backend before", before)
        print_abort_backend_snap(f"{case_id} — {label} backend after", after)

    fw_unchanged = bool(after.firmware) and before.firmware == after.firmware
    boot_unchanged = True
    staging_clean = staging_is_clean(before.staging_files, after.staging_files)
    flash_idle = flash_processes_idle(after.flash_processes)

    check.is_true(after.firmware, f"{case_id} [{label}]: /etc/version empty after abort")
    check.is_true(fw_unchanged, f"{case_id} [{label}]: firmware changed after aborted upload")
    if before.boot_id and after.boot_id:
        boot_unchanged = before.boot_id == after.boot_id
        check.is_true(
            boot_unchanged,
            f"{case_id} [{label}]: device rebooted after aborted upload",
        )
    elif before.uptime_s > 30:
        boot_unchanged = after.uptime_s >= before.uptime_s - 20
        check.is_true(
            boot_unchanged,
            f"{case_id} [{label}]: uptime reset suggests reboot "
            f"(before={before.uptime_s}s after={after.uptime_s}s)",
        )

    check.is_true(
        staging_clean,
        f"{case_id} [{label}]: partial staging files remain after abort: "
        f"{set(after.staging_files) - set(before.staging_files)}",
    )
    check.is_true(
        flash_idle,
        f"{case_id} [{label}]: flash/sysupgrade still running: {after.flash_processes!r}",
    )
    return fw_unchanged, boot_unchanged, staging_clean


def assert_power_loss_recovery_stable(
    before: AbortUpgradeBackendSnap,
    after: AbortUpgradeBackendSnap,
    *,
    case_id: str,
    label: str,
    quiet: bool = False,
) -> tuple[bool, bool, bool]:
    """Run post-power-loss recovery checks. Returns (fw_readable, staging_clean, flash_idle)."""
    if not quiet:
        print_abort_backend_snap(f"{case_id} — {label} backend before power cut", before)
        print_abort_backend_snap(f"{case_id} — {label} backend after recovery", after)

    fw_readable = bool((after.firmware or "").strip())
    staging_clean = staging_is_clean(before.staging_files, after.staging_files)
    flash_idle = flash_processes_idle(after.flash_processes)

    check.is_true(
        fw_readable,
        f"{case_id} [{label}]: /etc/version empty after power-loss recovery",
    )
    check.is_true(
        staging_clean,
        f"{case_id} [{label}]: partial staging files remain after power loss: "
        f"{set(after.staging_files) - set(before.staging_files)}",
    )
    check.is_true(
        flash_idle,
        f"{case_id} [{label}]: flash/sysupgrade still running after recovery: "
        f"{after.flash_processes!r}",
    )
    return fw_readable, staging_clean, flash_idle
