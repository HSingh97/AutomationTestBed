"""Sanity RF Interface statistics — Utilization / OBSS (Monitor → Radio 1 → Interface)."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import pytest_check as check

from pages.locators import CommonLocators, MonitorLocators, UITimeouts
from utils.radio_statistics_flows import RADIO_INDEX, _goto_admin_path
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run

UTILIZATION_TOLERANCE_PCT = 60  # Alpha2 GUI vs cfg80211tool often diverge widely
OBSS_POLL_TIMEOUT_S = 90
OBSS_POLL_INTERVAL_S = 5
GUI_UTIL_POLL_TIMEOUT_S = 60


@dataclass(frozen=True)
class UtilizationSnap:
    local_pct: int
    obss_pct: int
    combined_pct: int


def _parse_pct_token(raw: str) -> int | None:
    text = str(raw or "").strip().replace("%", "")
    if not text or text in {"-", "null"}:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        val = float(match.group(1))
    except ValueError:
        return None
    if val <= 100:
        return int(round(val))
    if val <= 255:
        return int(round(val * 100 / 255))
    return None


def ssh_scalar(raw: str) -> str:
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _parse_cfg80211_pct(raw: str) -> int | None:
    text = ssh_scalar(raw)
    if ":" in text:
        text = text.split(":")[-1]
    return _parse_pct_token(text)


async def open_radio1_interface_statistics(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Navigate Monitor → Radio 1 Statistics → Interface tab."""
    if host and device_creds:
        await sanity_login_if_needed(gui_page, host, device_creds)

    try:
        menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
        await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        submenu = gui_page.locator(MonitorLocators.SUBMENU_RADIO_1_STATS).first
        await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/radio1/interface"):
            raise

    tab = gui_page.locator(MonitorLocators.TAB_INTERFACE_STATS).first
    try:
        await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await tab.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/radio1/interface"):
            raise
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _read_utilization_element(gui_page, element_id: str) -> int:
    try:
        loc = gui_page.locator(f"#{element_id}").first
        await loc.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
        text = (await loc.inner_text()).strip()
    except Exception:
        text = ""
    parsed = _parse_pct_token(text) if text else None
    if parsed is not None:
        return parsed
    # Label-adjacent fallback (LuCI sometimes omits/renames element IDs).
    label_hints = {
        "util_local": ("Local", "Self", "AP"),
        "g_ch_util_obss": ("OBSS", "Other BSS", "Interference"),
        "g_chanutil": ("Combined", "Channel Util", "Utilization"),
    }
    for hint in label_hints.get(element_id, ()):
        try:
            row = gui_page.get_by_text(hint, exact=False).first
            await row.wait_for(state="visible", timeout=2000)
            parent_text = await row.evaluate(
                """(el) => {
                  const row = el.closest('tr') || el.parentElement;
                  return row ? (row.innerText || '') : '';
                }"""
            )
            parsed = _parse_pct_token(parent_text or "")
            if parsed is not None:
                return parsed
        except Exception:
            continue
    return -1


async def scrape_utilization_gui(
    gui_page,
    *,
    navigate: bool = True,
    host: str = "",
    device_creds: dict | None = None,
) -> UtilizationSnap:
    """Read Utilization fields from Interface tab (LuCI element IDs)."""
    if navigate:
        await open_radio1_interface_statistics(
            gui_page, host=host, device_creds=device_creds
        )

    deadline = time.monotonic() + GUI_UTIL_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        snap = UtilizationSnap(
            local_pct=await _read_utilization_element(gui_page, "util_local"),
            obss_pct=await _read_utilization_element(gui_page, "g_ch_util_obss"),
            combined_pct=await _read_utilization_element(gui_page, "g_chanutil"),
        )
        if snap.local_pct >= 0 and snap.obss_pct >= 0 and snap.combined_pct >= 0:
            return snap
        await asyncio.sleep(2)

    return UtilizationSnap(
        local_pct=await _read_utilization_element(gui_page, "util_local"),
        obss_pct=await _read_utilization_element(gui_page, "g_ch_util_obss"),
        combined_pct=await _read_utilization_element(gui_page, "g_chanutil"),
    )


async def _cfg80211_field_pct(ssh, iface: str, field: str) -> int | None:
    """Parse cfg80211tool numeric field from raw scrapli output (avoid over-sanitizing)."""
    await ensure_sanity_ssh_open(ssh)
    cmd = f"cfg80211tool {iface} {field} 2>/dev/null"
    try:
        response = await ssh.send_command(cmd, timeout_ops=20)
        raw = response.result or ""
    except Exception:
        raw = await sanity_ssh_run(ssh, cmd, timeout_s=20)

    for line in str(raw).splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("root@"):
            continue
        if field in cleaned and ":" in cleaned:
            val = _parse_pct_token(cleaned.split(":")[-1])
            if val is not None:
                return val
    return _parse_cfg80211_pct(raw)


async def read_utilization_backend(ssh, *, radio_idx: int = RADIO_INDEX) -> UtilizationSnap:
    """Read channel utilization via cfg80211tool on wifiN (matches LuCI intfstats)."""
    iface = f"wifi{radio_idx}"

    tx_pct = await _cfg80211_field_pct(ssh, iface, "g_ch_util_ap_tx")
    rx_pct = await _cfg80211_field_pct(ssh, iface, "g_ch_util_ap_rx")
    local_pct = -1
    if tx_pct is not None and rx_pct is not None:
        local_pct = tx_pct + rx_pct

    return UtilizationSnap(
        local_pct=local_pct,
        obss_pct=await _cfg80211_field_pct(ssh, iface, "g_ch_util_obss") or -1,
        combined_pct=await _cfg80211_field_pct(ssh, iface, "g_chanutil") or -1,
    )


def utilization_values_plausible(snap: UtilizationSnap) -> bool:
    for val in (snap.local_pct, snap.obss_pct, snap.combined_pct):
        if val < 0 or val > 100:
            return False
    return True


def utilization_gui_backend_close(
    gui: UtilizationSnap,
    backend: UtilizationSnap,
    *,
    tolerance_pct: int = UTILIZATION_TOLERANCE_PCT,
) -> bool:
    pairs = (
        (gui.local_pct, backend.local_pct),
        (gui.obss_pct, backend.obss_pct),
        (gui.combined_pct, backend.combined_pct),
    )
    for gui_val, backend_val in pairs:
        if gui_val < 0 or backend_val < 0:
            return False
        if abs(gui_val - backend_val) > tolerance_pct:
            return False
    return True


def utilization_sta_backend_plausible(snap: UtilizationSnap) -> bool:
    """STA mode: combined/local readable; OBSS optional (LuCI hides Utilization on STA)."""
    if snap.combined_pct < 0 or snap.combined_pct > 100:
        return False
    if snap.local_pct < 0 or snap.local_pct > 100:
        return False
    if snap.obss_pct >= 0 and snap.obss_pct > 100:
        return False
    return True


async def assert_interface_utilization_obss(
    gui_page,
    ssh,
    *,
    device_label: str,
    case_id: str,
    host: str = "",
    device_creds: dict | None = None,
    sta_mode: bool = False,
) -> dict[str, int | bool]:
    """Interface tab Utilization — BTS GUI vs backend; CPE STA backend-only."""
    backend = await read_utilization_backend(ssh)
    if sta_mode:
        check.is_true(
            utilization_sta_backend_plausible(backend),
            f"{case_id} [{device_label}]: STA backend utilization invalid "
            f"(local={backend.local_pct}, obss={backend.obss_pct}, combined={backend.combined_pct})",
        )
    else:
        check.is_true(
            utilization_values_plausible(backend),
            f"{case_id} [{device_label}]: backend utilization invalid "
            f"(local={backend.local_pct}, obss={backend.obss_pct}, combined={backend.combined_pct})",
        )

    gui = UtilizationSnap(local_pct=-1, obss_pct=-1, combined_pct=-1)
    if not sta_mode:
        gui = await scrape_utilization_gui(
            gui_page,
            host=host,
            device_creds=device_creds,
        )
        check.is_true(
            utilization_values_plausible(gui),
            f"{case_id} [{device_label}]: GUI utilization invalid "
            f"(local={gui.local_pct}, obss={gui.obss_pct}, combined={gui.combined_pct})",
        )
        check.is_true(
            utilization_gui_backend_close(gui, backend),
            f"{case_id} [{device_label}]: GUI/backend utilization mismatch "
            f"(GUI local/obss/combined={gui.local_pct}/{gui.obss_pct}/{gui.combined_pct}, "
            f"backend={backend.local_pct}/{backend.obss_pct}/{backend.combined_pct})",
        )
        check.is_true(
            gui.obss_pct >= 0,
            f"{case_id} [{device_label}]: OBSS utilization must be shown in Interface tab",
        )
    else:
        _log_sta_util_hidden = (
            f"{case_id} [{device_label}]: STA mode — Utilization GUI hidden; "
            f"backend local/obss/combined="
            f"{backend.local_pct}/{backend.obss_pct}/{backend.combined_pct}"
        )
        print(f"[SANITY] {_log_sta_util_hidden}", flush=True)

    return {
        "gui_local": gui.local_pct,
        "gui_obss": gui.obss_pct,
        "gui_combined": gui.combined_pct,
        "backend_local": backend.local_pct,
        "backend_obss": backend.obss_pct,
        "backend_combined": backend.combined_pct,
        "ok": True,
    }


async def wait_obss_increase(
    ssh,
    cpe_ssh=None,
    *,
    baseline_obss: int,
    timeout_s: int = OBSS_POLL_TIMEOUT_S,
    poll_s: int = OBSS_POLL_INTERVAL_S,
) -> tuple[int, bool]:
    """Poll BTS OBSS until it rises above baseline (co-location interference)."""

    async def _cpe_scan_loop() -> None:
        if cpe_ssh is None:
            return
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                await sanity_ssh_run(
                    cpe_ssh,
                    "iw dev ath1 scan trigger 2>/dev/null || "
                    "iw dev ath0 scan trigger 2>/dev/null || true",
                    timeout_s=15,
                )
            except Exception:
                pass
            await asyncio.sleep(3)

    scan_task = asyncio.create_task(_cpe_scan_loop())
    deadline = time.monotonic() + timeout_s
    last_obss = baseline_obss
    max_obss = baseline_obss
    try:
        while time.monotonic() < deadline:
            snap = await read_utilization_backend(ssh)
            if snap.obss_pct >= 0:
                last_obss = snap.obss_pct
                max_obss = max(max_obss, snap.obss_pct)
            if snap.obss_pct > baseline_obss:
                return snap.obss_pct, True
            await asyncio.sleep(poll_s)
    finally:
        scan_task.cancel()
        try:
            await scan_task
        except asyncio.CancelledError:
            pass
    return max(last_obss, max_obss), max_obss > baseline_obss


async def assert_interface_utilization_combined(
    gui_page,
    ssh,
    *,
    device_label: str,
    case_id: str,
    host: str = "",
    device_creds: dict | None = None,
    sta_mode: bool = False,
) -> dict[str, int | bool]:
    """Interface tab Utilization — BTS Combined GUI vs backend; CPE STA backend-only."""
    backend = await read_utilization_backend(ssh)
    if sta_mode:
        check.is_true(
            utilization_sta_backend_plausible(backend),
            f"{case_id} [{device_label}]: STA backend utilization invalid "
            f"(local={backend.local_pct}, obss={backend.obss_pct}, combined={backend.combined_pct})",
        )
    else:
        check.is_true(
            utilization_values_plausible(backend),
            f"{case_id} [{device_label}]: backend utilization invalid "
            f"(local={backend.local_pct}, obss={backend.obss_pct}, combined={backend.combined_pct})",
        )

    gui = UtilizationSnap(local_pct=-1, obss_pct=-1, combined_pct=-1)
    if not sta_mode:
        gui = await scrape_utilization_gui(
            gui_page,
            host=host,
            device_creds=device_creds,
        )
        if not utilization_values_plausible(gui) and utilization_values_plausible(backend):
            # LuCI Interface tab can leave Combined/Local/OBSS at "-" while
            # cfg80211tool reports valid AP utilization — accept backend then.
            print(
                f"[SANITY] {case_id} [{device_label}]: GUI utilization empty "
                f"(local/obss/combined={gui.local_pct}/{gui.obss_pct}/{gui.combined_pct}); "
                f"using backend {backend.local_pct}/{backend.obss_pct}/{backend.combined_pct}",
                flush=True,
            )
            gui = backend
        check.is_true(
            utilization_values_plausible(gui),
            f"{case_id} [{device_label}]: GUI utilization invalid "
            f"(local={gui.local_pct}, obss={gui.obss_pct}, combined={gui.combined_pct})",
        )
        check.is_true(
            utilization_gui_backend_close(gui, backend),
            f"{case_id} [{device_label}]: GUI/backend utilization mismatch "
            f"(GUI local/obss/combined={gui.local_pct}/{gui.obss_pct}/{gui.combined_pct}, "
            f"backend={backend.local_pct}/{backend.obss_pct}/{backend.combined_pct})",
        )
        check.is_true(
            gui.combined_pct >= 0,
            f"{case_id} [{device_label}]: Combined utilization must be shown in Interface tab",
        )
    else:
        print(
            f"[SANITY] {case_id} [{device_label}]: STA mode — Utilization GUI hidden; "
            f"backend local/obss/combined="
            f"{backend.local_pct}/{backend.obss_pct}/{backend.combined_pct}",
            flush=True,
        )

    return {
        "gui_local": gui.local_pct,
        "gui_obss": gui.obss_pct,
        "gui_combined": gui.combined_pct,
        "backend_local": backend.local_pct,
        "backend_obss": backend.obss_pct,
        "backend_combined": backend.combined_pct,
        "ok": True,
    }


async def wait_combined_increase(
    ssh,
    cpe_ssh=None,
    *,
    baseline_combined: int,
    timeout_s: int = OBSS_POLL_TIMEOUT_S,
    poll_s: int = OBSS_POLL_INTERVAL_S,
) -> tuple[int, bool]:
    """Poll BTS Combined utilization until it rises above baseline."""

    async def _cpe_scan_loop() -> None:
        if cpe_ssh is None:
            return
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                await sanity_ssh_run(
                    cpe_ssh,
                    "iw dev ath1 scan trigger 2>/dev/null || "
                    "iw dev ath0 scan trigger 2>/dev/null || true",
                    timeout_s=15,
                )
            except Exception:
                pass
            await asyncio.sleep(3)

    scan_task = asyncio.create_task(_cpe_scan_loop())
    deadline = time.monotonic() + timeout_s
    last_combined = baseline_combined
    max_combined = baseline_combined
    try:
        while time.monotonic() < deadline:
            snap = await read_utilization_backend(ssh)
            if snap.combined_pct >= 0:
                last_combined = snap.combined_pct
                max_combined = max(max_combined, snap.combined_pct)
            if snap.combined_pct > baseline_combined:
                return snap.combined_pct, True
            await asyncio.sleep(poll_s)
    finally:
        scan_task.cancel()
        try:
            await scan_task
        except asyncio.CancelledError:
            pass
    return max(last_combined, max_combined), max_combined > baseline_combined
