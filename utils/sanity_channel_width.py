"""Sanity-only Channel Width / Bandwidth (Wireless → Radio 1 → Properties)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pages.locators import RadioPropertiesLocators, UITimeouts
from pages.radio_properties_page import RadioPropertiesPage
from utils.net_utils import format_luci_url
from utils.parsers import parse_bandwidth
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.ui_helpers import execute_triple_apply
from traffic.operating_rate_table import uci_htmode_matches


RADIO_IDX = 1
RADIO_URL_CHUNK = "/admin/wireless/radio1"
# Sheet / GUI labels → UCI HT* via uci_htmode_value
CHANNEL_WIDTH_GUI = ("20 MHz", "40 MHz", "80 MHz", "160 MHz")


@dataclass(frozen=True)
class SanityBandwidthSnap:
    htmode: str  # raw UCI e.g. HT80, HT40+
    gui_mhz: str  # e.g. "80 MHz"


def _mhz_from_htmode(htmode: str) -> str:
    return parse_bandwidth(htmode) or ""


def gui_label_to_htmode(gui_label: str) -> str:
    """'80 MHz' → 'HT80'; '40 MHz' → 'HT40+'; '160 MHz' → 'HT160'."""
    token = _mhz_token(gui_label)
    if not token:
        raise ValueError(f"[sanity] bad Channel Width label: {gui_label!r}")
    if token == "40":
        return "HT40+"
    return f"HT{token}"


def _htmode_equal(expected_ht: str, actual_ht: str) -> bool:
    exp = str(expected_ht or "").upper().replace(" ", "").rstrip("+")
    act = str(actual_ht or "").upper().replace(" ", "").rstrip("+")
    if not exp or not act:
        return False
    if exp == act or exp in act or act in exp:
        return True
    # Prefer shared helper when bandwidth is in the operating-rate table.
    try:
        return uci_htmode_matches(exp, actual_ht)
    except ValueError:
        return False


async def read_sanity_bandwidth_snap(ssh, *, radio_idx: int = RADIO_IDX) -> SanityBandwidthSnap:
    await ensure_sanity_ssh_open(ssh)
    raw = (
        await sanity_ssh_run(ssh, f"uci -q get wireless.wifi{radio_idx}.htmode", timeout_s=30)
    ).strip()
    return SanityBandwidthSnap(htmode=raw, gui_mhz=_mhz_from_htmode(raw))


async def open_sanity_radio_properties(
    gui_page,
    *,
    host: str,
    device_creds: dict | None = None,
) -> RadioPropertiesPage:
    """Wireless → Radio 1 → Properties."""
    if host and device_creds:
        url = f"{format_luci_url(host).rstrip('/')}/admin/wireless/radio1"
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds)
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds)
    page = RadioPropertiesPage(gui_page, local_ip=host or "192.168.2.1")
    await page.navigate()
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return page


def _mhz_token(label: str) -> str | None:
    match = re.search(r"(160|80|40|20)", str(label or ""))
    return match.group(1) if match else None


def option_text_matches_width(opt_text: str, gui_label: str) -> bool:
    """Match '20 MHz' without treating '160 MHz' as a hit for 20."""
    text = (opt_text or "").strip()
    label = (gui_label or "").strip()
    if not text or not label:
        return False
    if text == label:
        return True
    token = _mhz_token(label)
    if not token:
        return False
    return bool(re.search(rf"(^|[^\d]){re.escape(token)}([^\d]|$)", text))


async def _dropdown_options(gui_page, locator: str) -> list[dict[str, str]]:
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    return await element.evaluate(
        """
        el => Array.from(el.options || []).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )


async def _wait_bandwidth_options(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
    retries: int = 5,
) -> list[dict[str, str]]:
    """Reload Radio Properties until Channel Width options are populated."""
    last: list[dict[str, str]] = []
    for attempt in range(retries):
        if attempt:
            await open_sanity_radio_properties(gui_page, host=host, device_creds=device_creds)
            await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        try:
            last = await _dropdown_options(gui_page, RadioPropertiesLocators.BANDWIDTH_DROPDOWN)
        except Exception:
            last = []
        if last:
            return last
        await gui_page.wait_for_timeout(1500)
    return last


async def read_sanity_gui_bandwidth(gui_page) -> str:
    element = gui_page.locator(RadioPropertiesLocators.BANDWIDTH_DROPDOWN).first
    if await element.count() == 0:
        return ""
    return await element.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )


async def cpe_has_bandwidth_dropdown(gui_page) -> bool:
    """Sheet: CPE has no separate Channel Width control (or not editable)."""
    loc = gui_page.locator(RadioPropertiesLocators.BANDWIDTH_DROPDOWN).first
    if await loc.count() == 0:
        return False
    try:
        await loc.wait_for(state="attached", timeout=3000)
    except Exception:
        return False
    # Treat disabled / hidden as "no separate CBW option".
    try:
        disabled = await loc.is_disabled()
        visible = await loc.is_visible()
        if disabled or not visible:
            return False
    except Exception:
        return False
    return True


async def apply_sanity_channel_width_gui(
    gui_page,
    *,
    gui_label: str,
    host: str = "",
    device_creds: dict | None = None,
    ssh=None,
) -> None:
    """BTS: Properties → Channel Width / Bandwidth → Save → Apply.

    On UBR655 / HE builds the GUI select often does not stick in UCI; for
    20/40/80 MHz we fall back to SSH ``ucidyn`` when post-apply UCI/runtime
    still shows the prior CBW. 160 MHz stays GUI-only (SSH can desync RF).
    """
    await open_sanity_radio_properties(gui_page, host=host, device_creds=device_creds)
    options = await _wait_bandwidth_options(
        gui_page, host=host, device_creds=device_creds, retries=5
    )
    match = next(
        (
            o
            for o in options
            if not o["disabled"] and option_text_matches_width(o["text"], gui_label)
        ),
        None,
    )
    if match is None:
        raise RuntimeError(
            f"[sanity] Channel Width '{gui_label}' not found; "
            f"available={[o['text'] for o in options]}"
        )
    element = gui_page.locator(RadioPropertiesLocators.BANDWIDTH_DROPDOWN).first
    # Prefer label match; fall back to value / JS set (some builds ignore select_option).
    selected = False
    if await element.is_visible():
        for kwargs in (
            {"label": match["text"]},
            {"value": match["value"]},
        ):
            try:
                await element.select_option(**kwargs)
                selected = True
                break
            except Exception:
                continue
    if not selected:
        await element.evaluate(
            """
            (el, value) => {
                el.value = value;
                Array.from(el.options).forEach(opt => { opt.selected = opt.value === value; });
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
            """,
            match["value"],
        )
    await execute_triple_apply(gui_page, RADIO_URL_CHUNK)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    # Confirm UCI/runtime; SSH fallback for non-160 when GUI did not commit.
    if ssh is None or "160" in str(gui_label):
        return
    try:
        ht = gui_label_to_htmode(gui_label)
    except Exception:
        return
    import asyncio as _asyncio

    ok_uci, snap = await verify_sanity_bandwidth_uci(ssh, gui_label, retries=4, delay_s=2.0)
    runtime = await read_sanity_runtime_mode(ssh)
    if ok_uci and runtime_mode_matches_width(runtime, gui_label):
        return
    # GUI select did not stick — commit via SSH (known-good on this FW for 20/40/80).
    if not ok_uci:
        await apply_sanity_bandwidth_ssh(ssh, ht, settle_seconds=20)
    else:
        # UCI already correct; radio may still be mid-switch (runtime briefly "11A").
        await _asyncio.sleep(10)
    ok_uci2, snap2 = await verify_sanity_bandwidth_uci(ssh, gui_label, retries=8, delay_s=2.0)
    runtime2 = ""
    for _ in range(10):
        runtime2 = await read_sanity_runtime_mode(ssh)
        if runtime_mode_matches_width(runtime2, gui_label):
            return
        await _asyncio.sleep(3)
    # UCI committed is enough here — caller waits for RF/link. Only raise if UCI wrong.
    if not ok_uci2:
        raise RuntimeError(
            f"[sanity] Channel Width '{gui_label}' not active after GUI+SSH "
            f"(UCI={snap2.htmode!r} was {snap.htmode!r}, runtime={runtime2!r})"
        )


async def apply_sanity_bandwidth_ssh(
    ssh,
    htmode: str,
    *,
    settle_seconds: int = 15,
) -> None:
    import asyncio

    await ensure_sanity_ssh_open(ssh)
    ht = str(htmode or "").strip()
    if not ht:
        return
    try:
        await sanity_ssh_run(
            ssh, f"ucidyn set wireless.wifi{RADIO_IDX}.htmode {ht}", timeout_s=60
        )
        await sanity_ssh_run(ssh, "ucidyn apply", timeout_s=120)
    except Exception:
        pass
    if settle_seconds:
        await asyncio.sleep(settle_seconds)


async def read_sanity_runtime_bandwidth(ssh, *, radio_idx: int = RADIO_IDX) -> str:
    """Runtime CBW from cfg80211tool (e.g. 11AHE80) — CPE may follow BTS without UCI change."""
    await ensure_sanity_ssh_open(ssh)
    raw = (
        await sanity_ssh_run(
            ssh, f"cfg80211tool ath{radio_idx} get_mode 2>/dev/null || true", timeout_s=30
        )
    ).strip()
    return raw


def runtime_bandwidth_matches(gui_or_ht: str, runtime_mode: str) -> bool:
    """True when cfg80211tool get_mode reflects expected Channel Width."""
    text = str(runtime_mode or "").upper()
    mhz = re.search(r"(160|80|40|20)", str(gui_or_ht or ""))
    if not mhz:
        return False
    token = mhz.group(1)
    # Examples: 11AHE80, 11AHE40PLUS, HT80, HE160
    return f"HE{token}" in text or f"HT{token}" in text or f"VHT{token}" in text


async def read_sanity_runtime_mode(ssh, *, radio_idx: int = RADIO_IDX) -> str:
    """Runtime channel width from cfg80211tool (e.g. 11AHE80) — CPE UCI may lag BTS."""
    await ensure_sanity_ssh_open(ssh)
    # Print only the mode token so clean_ssh_output keeps it.
    cmd = (
        f"cfg80211tool ath{radio_idx} get_mode 2>/dev/null "
        f"| tr '\\t' ' ' | sed -n 's/.*get_mode://p' | head -1"
    )
    raw = (await sanity_ssh_run(ssh, cmd, timeout_s=30)).strip()
    if raw:
        return raw
    # Fallback: full line, extract locally.
    full = (
        await sanity_ssh_run(
            ssh, f"cfg80211tool ath{radio_idx} get_mode 2>/dev/null | tr '\\t' ' '", timeout_s=30
        )
    ).strip()
    match = re.search(r"get_mode:(\S+)", full, re.I)
    if match:
        return match.group(1)
    # Last resort: any HE/VHT/HT width token.
    match = re.search(r"(?:HE|VHT|HT)(160|80|40|20)", full, re.I)
    return match.group(0) if match else full


def runtime_mode_matches_width(runtime: str, gui_label: str) -> bool:
    """True if cfg80211tool-style mode reflects 20/40/80/160 (e.g. 11AHE80).

    Do not treat bare UCI values like HT160 as a runtime match — SSH cleanup can
    echo the last UCI get and falsely pass CBW checks.
    """
    mhz = re.search(r"(160|80|40|20)", str(gui_label or ""))
    if not mhz:
        return False
    text = str(runtime or "").upper().replace(" ", "")
    if not text:
        return False
    token = mhz.group(1)
    # cfg80211tool: 11AHE80 / 11AHE40PLUS / 11ACVHT80 / 11AHE160
    if re.search(rf"(?:HE|VHT){token}", text):
        return True
    # Rare plain HT40PLUS from get_mode (not bare HT80 from UCI).
    if "PLUS" in text and re.search(rf"HT{token}", text):
        return True
    return False


async def verify_sanity_bandwidth_uci(
    ssh,
    expected_gui_or_ht: str,
    *,
    retries: int = 8,
    delay_s: float = 3.0,
) -> tuple[bool, SanityBandwidthSnap]:
    """Match UCI htmode to GUI label ('80 MHz') or HT* string."""
    import asyncio

    last = SanityBandwidthSnap("", "")
    expected = str(expected_gui_or_ht or "").strip()
    if re.search(r"MHz", expected, re.I) or re.fullmatch(r"(160|80|40|20)", expected):
        try:
            expected_ht = gui_label_to_htmode(
                expected if "MHz" in expected else f"{expected} MHz"
            )
        except Exception:
            expected_ht = expected
    else:
        expected_ht = expected

    for attempt in range(retries):
        last = await read_sanity_bandwidth_snap(ssh)
        if _htmode_equal(expected_ht, last.htmode):
            return True, last
        if last.gui_mhz and option_text_matches_width(last.gui_mhz, expected):
            return True, last
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False, last


async def verify_sanity_cpe_follows_cbw(
    ssh,
    expected_gui_or_ht: str,
    *,
    retries: int = 10,
    delay_s: float = 3.0,
) -> tuple[bool, str]:
    """CPE follows BTS Channel Width on-air (runtime mode), not necessarily UCI htmode."""
    import asyncio

    last = ""
    for attempt in range(retries):
        last = await read_sanity_runtime_bandwidth(ssh)
        if runtime_bandwidth_matches(expected_gui_or_ht, last):
            return True, last
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False, last
