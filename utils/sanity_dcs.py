"""Sanity-only DCS helpers (Wireless → Radio 1 → DCS)."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from pages.locators import UITimeouts
from pages.radio_properties_page import RadioPropertiesPage
from utils.net_utils import format_luci_url
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.ui_helpers import execute_triple_apply, fill_luci_input, read_luci_input_value


RADIO_IDX = 1
DCS_URL_CHUNK = "/admin/wireless/radio1/dcs1"
DCS_STATUS_SELECT = (
    "select#dcstatus, "
    "select[name='advwireless.ath1.dcsstatus'], "
    "select[name='advwireless.ath1.dcsstatus']"
)
DCS_RTX_INPUT = (
    "#dcs_rtx input, "
    "input[name='advwireless.ath1.dcsthrld']"
)

DCS_ENABLE = "Enable"
DCS_DISABLE = "Disable"
DCS_STATUS_TO_UCI = {DCS_ENABLE: "1", DCS_DISABLE: "0"}
DCS_STATUS_FROM_UCI = {
    "0": DCS_DISABLE,
    "1": DCS_ENABLE,
    "disable": DCS_DISABLE,
    "enable": DCS_ENABLE,
}

# Sheet: RTx Threshold 0–100 %
RTX_MIN = 0
RTX_MAX = 100
RTX_TEST_ALT = "40"  # used when baseline is 25 (GUI default)


@dataclass(frozen=True)
class SanityDcsSnap:
    status_uci: str  # 0/1
    rtx_uci: str
    status_gui: str = ""
    rtx_gui: str = ""


def dcs_status_label(uci_or_gui: str) -> str:
    raw = str(uci_or_gui or "").strip().lower()
    if raw in ("1", "enable"):
        return DCS_ENABLE
    if raw in ("0", "disable"):
        return DCS_DISABLE
    if "enable" in raw:
        return DCS_ENABLE
    if "disable" in raw:
        return DCS_DISABLE
    return DCS_STATUS_FROM_UCI.get(raw, str(uci_or_gui or "").strip())


def normalize_rtx(raw: str) -> str:
    match = re.search(r"(\d+)", str(raw or ""))
    return match.group(1) if match else ""


def pick_rtx_target(current: str) -> str:
    cur = normalize_rtx(current) or "25"
    if cur != RTX_TEST_ALT:
        return RTX_TEST_ALT
    return "30"


async def open_sanity_dcs_page(
    gui_page,
    *,
    host: str,
    device_creds: dict | None = None,
) -> RadioPropertiesPage:
    """Wireless → Radio 1 → DCS (preserve LuCI stok when possible)."""
    import re as _re

    async def _goto_dcs(url: str) -> None:
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        if host and device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds)

    # Prefer stok-qualified URL from the current session (KWN pages need it).
    stok_base = ""
    match = _re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if match:
        stok_base = match.group(1)
        await _goto_dcs(f"{stok_base}{DCS_URL_CHUNK}")
    elif host and device_creds:
        base = format_luci_url(host).rstrip("/")
        await _goto_dcs(f"{base}/admin/wireless/radio1")
        await sanity_login_if_needed(gui_page, host, device_creds)
        match = _re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
        if match:
            stok_base = match.group(1)
            await _goto_dcs(f"{stok_base}{DCS_URL_CHUNK}")
        else:
            await _goto_dcs(f"{base}{DCS_URL_CHUNK}")

    page = RadioPropertiesPage(gui_page, local_ip=host or "192.168.2.1")
    tab = gui_page.locator("ul.cbi-tabmenu > li > a[href*='dcs']").first
    try:
        if await tab.count() > 0 and "dcs" not in (gui_page.url or "").lower():
            await tab.click(timeout=5000)
            await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    except Exception:
        pass

    last_exc: Exception | None = None
    for locator in (
        "select#dcstatus",
        "select[name='advwireless.ath1.dcsstatus']",
        "select[name*='dcsstatus']",
        "text=DCS Status",
    ):
        try:
            await gui_page.locator(locator).first.wait_for(
                state="attached", timeout=15000
            )
            if locator.startswith("text="):
                # Label present — wait specifically for the select next.
                await gui_page.locator("select#dcstatus, select[name*='dcsstatus']").first.wait_for(
                    state="attached", timeout=10000
                )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        # Last resort: radio1 then DCS tab via stok.
        if stok_base:
            await _goto_dcs(f"{stok_base}/admin/wireless/radio1")
            try:
                await tab.click(timeout=8000)
            except Exception:
                pass
            await gui_page.locator("select#dcstatus, select[name*='dcsstatus']").first.wait_for(
                state="attached", timeout=20000
            )
        else:
            raise last_exc
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return page


async def read_gui_dcs_status(gui_page) -> str:
    el = gui_page.locator(DCS_STATUS_SELECT).first
    await el.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    text = await el.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )
    return dcs_status_label(text)


async def read_gui_dcs_rtx(gui_page) -> str:
    raw = await read_luci_input_value(gui_page, DCS_RTX_INPUT)
    return normalize_rtx(raw)


async def apply_dcs_gui(
    gui_page,
    *,
    status: str | None = None,
    rtx: str | None = None,
    host: str = "",
    device_creds: dict | None = None,
    settle_s: float = 10.0,
) -> None:
    """Set DCS Status and/or RTx Threshold → Save/Apply."""
    await open_sanity_dcs_page(gui_page, host=host, device_creds=device_creds)
    if status is not None:
        label = dcs_status_label(status)
        want_uci = DCS_STATUS_TO_UCI.get(label, "0" if label == DCS_DISABLE else "1")
        el = gui_page.locator(DCS_STATUS_SELECT).first
        options = await el.evaluate(
            """
            el => Array.from(el.options || []).map(o => ({
                text: (o.textContent || "").trim(),
                value: (o.value || "").trim(),
                disabled: !!o.disabled
            }))
            """
        )
        match = next(
            (
                o
                for o in options
                if not o["disabled"]
                and (
                    o["value"] == want_uci
                    or dcs_status_label(o["text"]) == label
                    or label.lower() in o["text"].lower()
                )
            ),
            None,
        )
        if match is None:
            raise RuntimeError(
                f"[sanity] DCS Status '{label}' not found; available={options}"
            )
        try:
            if await el.is_visible():
                await el.select_option(value=match["value"])
            else:
                raise RuntimeError("hidden")
        except Exception:
            await el.evaluate(
                """
                (el, value) => {
                    el.value = value;
                    Array.from(el.options).forEach(opt => {
                        opt.selected = opt.value === value;
                    });
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                match["value"],
            )
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    if rtx is not None:
        value = normalize_rtx(rtx)
        n = int(value)
        if n < RTX_MIN or n > RTX_MAX:
            raise ValueError(f"[sanity] RTx Threshold {value} outside {RTX_MIN}-{RTX_MAX}")
        await fill_luci_input(gui_page, DCS_RTX_INPUT, value)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    await execute_triple_apply(gui_page, DCS_URL_CHUNK)
    if settle_s:
        await asyncio.sleep(settle_s)


async def read_dcs_snap(ssh, *, radio_idx: int = RADIO_IDX) -> SanityDcsSnap:
    await ensure_sanity_ssh_open(ssh)
    status = (
        await sanity_ssh_run(
            ssh, f"uci -q get advwireless.ath{radio_idx}.dcsstatus", timeout_s=30
        )
    ).strip()
    rtx = (
        await sanity_ssh_run(
            ssh, f"uci -q get advwireless.ath{radio_idx}.dcsthrld", timeout_s=30
        )
    ).strip()
    return SanityDcsSnap(status_uci=status, rtx_uci=normalize_rtx(rtx) or rtx)


async def apply_dcs_ssh(
    ssh,
    *,
    status: str | None = None,
    rtx: str | None = None,
    settle_seconds: int = 8,
    radio_idx: int = RADIO_IDX,
) -> None:
    await ensure_sanity_ssh_open(ssh)
    try:
        if status is not None:
            label = dcs_status_label(status)
            uci = DCS_STATUS_TO_UCI.get(label, "0")
            await sanity_ssh_run(
                ssh, f"ucidyn set advwireless.ath{radio_idx}.dcsstatus {uci}", timeout_s=60
            )
        if rtx is not None:
            value = normalize_rtx(rtx)
            await sanity_ssh_run(
                ssh, f"ucidyn set advwireless.ath{radio_idx}.dcsthrld {value}", timeout_s=60
            )
        await sanity_ssh_run(ssh, "ucidyn apply", timeout_s=120)
    except Exception:
        pass
    if settle_seconds:
        await asyncio.sleep(settle_seconds)


async def verify_dcs_backend(
    ssh,
    *,
    status: str | None = None,
    rtx: str | None = None,
    retries: int = 8,
    delay_s: float = 2.0,
) -> tuple[bool, SanityDcsSnap]:
    last = SanityDcsSnap("", "")
    for attempt in range(retries):
        last = await read_dcs_snap(ssh)
        status_ok = True
        rtx_ok = True
        if status is not None:
            status_ok = dcs_status_label(last.status_uci) == dcs_status_label(status)
        if rtx is not None:
            rtx_ok = normalize_rtx(last.rtx_uci) == normalize_rtx(rtx)
        if status_ok and rtx_ok:
            return True, last
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False, last
