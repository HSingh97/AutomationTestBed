"""Centralized link health and recovery manager."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
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

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parent.parent

    def _resolve_restore_bundle(self, role: str = "BTS") -> Path:
        recovery = self.profile_bundle.active.get("recovery", {})
        role_upper = str(role).upper()
        if role_upper == "CPE":
            rel = recovery.get("cpe_restore_archive", "config/CPE.tar.gz")
        else:
            rel = recovery.get("bts_restore_archive", "config/BTS.tar.gz")
        return self._repo_root() / str(rel)

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

    async def run_factory_reset_via_ui(self, *, gui_page, bsu_ip, device_creds) -> bool:
        """
        Preferred reset path:
        Management -> Upgrade/Reset -> Reset Page table -> select all -> Reset -> accept popup.
        """
        if not self.profile_bundle.active["recovery"].get("allow_factory_reset", False):
            return False
        try:
            from utils.gui_login import login_if_needed

            await login_if_needed(
                gui_page,
                bsu_ip,
                device_creds,
                wait_ms=5000,
                skip_recovery=True,
            )
            mgmt_menu = gui_page.locator("li.Management > a.menu").first
            await mgmt_menu.wait_for(state="visible", timeout=15000)
            await mgmt_menu.click()
            flashops = gui_page.locator("ul.dropdown-menu a[href*='/system/flashops']").first
            await flashops.wait_for(state="visible", timeout=15000)
            await flashops.click()
            await gui_page.wait_for_load_state("networkidle")
            await gui_page.wait_for_timeout(1500)

            # Open reset section/tab if needed.
            for selector in (
                "a:has-text('Reset Page table')",
                "a:has-text('Reset')",
                "li:has-text('Reset') a",
            ):
                tab = gui_page.locator(selector).first
                if await tab.is_visible(timeout=1000):
                    await tab.click()
                    await gui_page.wait_for_timeout(800)
                    break

            # Select all visible reset parameters if checkboxes are present.
            checkboxes = gui_page.locator("input[type='checkbox']")
            cb_count = await checkboxes.count()
            for idx in range(cb_count):
                cb = checkboxes.nth(idx)
                if await cb.is_visible(timeout=500) and not await cb.is_checked():
                    await cb.check()

            # Accept confirmation popup automatically.
            async def _accept_dialog(dialog):
                await dialog.accept()

            gui_page.once("dialog", _accept_dialog)
            reset_btn = gui_page.locator("input[value='Reset']:visible, button:has-text('Reset')").first
            await reset_btn.wait_for(state="visible", timeout=15000)
            await reset_btn.click()

            self.metrics.factory_resets += 1
            await asyncio.sleep(140)
            return True
        except Exception as exc:
            self.metrics.last_error = f"UI factory reset failed: {exc}"
            return False

    async def run_profile_restore(
        self,
        *,
        gui_page,
        device_creds,
        role: str = "BTS",
        post_restore_ip: str | None = None,
    ) -> bool:
        """
        Restore saved profile via GUI:
        default IP (192.168.2.1) -> Management -> Upgrade/Reset -> Restore.
        """
        recovery = self.profile_bundle.active.get("recovery", {})
        restore_ip = str(recovery.get("restore_default_ip", "192.168.2.1")).strip()
        bundle_path = self._resolve_restore_bundle(role=role)
        if not bundle_path.exists():
            self.metrics.last_error = f"Restore archive not found: {bundle_path}"
            return False

        try:
            from utils.gui_login import login_if_needed

            await login_if_needed(
                gui_page,
                restore_ip,
                device_creds,
                wait_ms=4000,
                skip_recovery=True,
            )
            mgmt_menu = gui_page.locator("li.Management > a.menu").first
            await mgmt_menu.wait_for(state="visible", timeout=15000)
            await mgmt_menu.click()
            flashops = gui_page.locator("ul.dropdown-menu a[href*='/system/flashops']").first
            await flashops.wait_for(state="visible", timeout=15000)
            await flashops.click()
            await gui_page.wait_for_load_state("networkidle")
            await gui_page.wait_for_timeout(1500)

            file_input = gui_page.locator("input[type='file']").first
            await file_input.wait_for(state="visible", timeout=15000)
            await file_input.set_input_files(str(bundle_path))

            restore_btn = gui_page.locator("input[value='Restore']:visible").first
            await restore_btn.wait_for(state="visible", timeout=15000)
            await restore_btn.click()

            wait_s = int(recovery.get("reboot_wait_seconds", 150))
            await asyncio.sleep(wait_s)

            target = post_restore_ip or self.profile_bundle.active["dut"].get("local_ipv6") or self.profile_bundle.active["dut"].get("local_ip")
            target_ip = str(target) if target else None
            if not target_ip:
                self.metrics.last_error = "Profile restore target IP is empty."
                return False

            # Add boot-buffer retries after restore (requested extra 15/20s behavior).
            if await self.is_gui_reachable(target_ip, timeout_s=8):
                return True
            for extra_wait in (15, 20, 20):
                await asyncio.sleep(extra_wait)
                if await self.is_gui_reachable(target_ip, timeout_s=8):
                    return True
            self.metrics.last_error = f"Profile restore completed but target not reachable yet: {target_ip}"
            return False
        except Exception as exc:
            self.metrics.last_error = f"Profile restore failed: {exc}"
            return False

    async def ensure_link_or_recover(self, *, gui_page=None, bsu_ip=None, device_creds=None, root_ssh=None) -> bool:
        if await self.is_gui_reachable(bsu_ip):
            return True
        if await self.run_soft_recovery(gui_page=gui_page, bsu_ip=bsu_ip, device_creds=device_creds):
            return True
        reset_ok = False
        if gui_page is not None and bsu_ip and device_creds is not None:
            # Preferred path: UI factory reset.
            reset_ok = await self.run_factory_reset_via_ui(gui_page=gui_page, bsu_ip=bsu_ip, device_creds=device_creds)
        if not reset_ok and root_ssh is not None:
            # Last resort: SSH reset when GUI path is unavailable.
            reset_ok = await self.run_factory_reset_fallback(root_ssh)
        if reset_ok:
            if gui_page is not None and device_creds is not None:
                # After factory reset, bootstrap from default IP and restore saved baseline config.
                await self.run_profile_restore(
                    gui_page=gui_page,
                    device_creds=device_creds,
                    role="BTS",
                    post_restore_ip=bsu_ip,
                )
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

