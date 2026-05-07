"""Centralized link health and recovery manager."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from utils.net_utils import format_http_host


@dataclass
class RecoveryState:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    factory_resets: int = 0
    total_recovery_seconds: float = 0.0
    last_error: str = ""


@dataclass
class RecoveryManager:
    profile_bundle: Any
    metrics: RecoveryState = field(default_factory=RecoveryState)

    async def is_gui_reachable(self, local_ip: str | None = None, timeout_s: int = 6) -> bool:
        dut = self.profile_bundle.active["dut"]
        target_ip = local_ip or dut.get("local_ipv6") or dut.get("local_ip")
        url = f"https://{format_http_host(target_ip)}"
        try:
            async with httpx.AsyncClient(verify=False, timeout=timeout_s) as client:
                resp = await client.get(url)
                return resp.status_code < 500
        except Exception:
            return False

    async def run_soft_recovery(self, gui_page=None, bsu_ip=None, device_creds=None) -> bool:
        start = time.monotonic()
        self.metrics.attempts += 1
        retry_backoff = self.profile_bundle.active["recovery"].get("retry_backoff_seconds", [3, 8])
        try:
            if gui_page is not None and bsu_ip and device_creds:
                from utils.gui_login import login_if_needed

                await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=3000, skip_recovery=True)
                await gui_page.wait_for_timeout(1000)

            for wait_s in retry_backoff:
                if await self.is_gui_reachable(bsu_ip):
                    self.metrics.successes += 1
                    self.metrics.total_recovery_seconds += time.monotonic() - start
                    return True
                await asyncio.sleep(wait_s)

            self.metrics.failures += 1
            self.metrics.last_error = "Soft recovery retry budget exhausted."
            self.metrics.total_recovery_seconds += time.monotonic() - start
            return False
        except Exception as exc:
            self.metrics.failures += 1
            self.metrics.last_error = str(exc)
            self.metrics.total_recovery_seconds += time.monotonic() - start
            return False

    async def run_factory_reset_fallback(self, root_ssh) -> bool:
        if not self.profile_bundle.active["recovery"].get("allow_factory_reset", False):
            return False
        try:
            reset_cmd = self.profile_bundle.active["recovery"].get("factory_reset_command", "").strip()
            if not reset_cmd:
                return False
            self.metrics.factory_resets += 1
            await root_ssh.send_command(reset_cmd)
            wait_s = int(self.profile_bundle.active["recovery"].get("reboot_wait_seconds", 120))
            await asyncio.sleep(wait_s)
            return True
        except Exception as exc:
            self.metrics.last_error = f"Factory reset fallback failed: {exc}"
            return False

    async def ensure_link_or_recover(self, *, gui_page=None, bsu_ip=None, device_creds=None, root_ssh=None) -> bool:
        if await self.is_gui_reachable(bsu_ip):
            return True
        if await self.run_soft_recovery(gui_page=gui_page, bsu_ip=bsu_ip, device_creds=device_creds):
            return True
        if root_ssh is not None:
            reset_ok = await self.run_factory_reset_fallback(root_ssh)
            if reset_ok and await self.is_gui_reachable(bsu_ip):
                self.metrics.successes += 1
                return True
        return False


_ACTIVE_MANAGER: RecoveryManager | None = None


def set_active_recovery_manager(manager: RecoveryManager) -> None:
    global _ACTIVE_MANAGER
    _ACTIVE_MANAGER = manager


def get_active_recovery_manager() -> RecoveryManager | None:
    return _ACTIVE_MANAGER

