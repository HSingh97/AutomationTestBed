"""Sanity-only Configured Channel Auto/Manual (Wireless → Radio 1 → Properties)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pages.locators import RadioPropertiesLocators, UITimeouts
from pages.radio_properties_page import RadioPropertiesPage
from utils.net_utils import format_luci_url
from utils.parsers import parse_iwconfig_active_channel
from utils.sanity_gui import resolve_luci_stok, sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.ui_helpers import execute_triple_apply


RADIO_IDX = 1
RADIO_URL_CHUNK = "/admin/wireless/radio1"
# Prefer name-based CSS; fall back to legacy xpath (tried separately — Playwright
# cannot mix CSS + XPath in one comma-separated selector string).
CHANNEL_SELECT_CSS = (
    "select[name='advwireless.ath1.channel'], "
    "select[name='wireless.wifi1.channel']"
)
CHANNEL_SELECT_XPATH = RadioPropertiesLocators.CONFIGURED_CHANNEL_DROPDOWN
ACTIVE_CHANNEL_CSS = "#wifi1_actchan, #opchannel, span[id*='actchan'], span[id*='opchan']"
ACTIVE_CHANNEL_XPATH = RadioPropertiesLocators.ACTIVE_CHANNEL_DISPLAY
# Lab-stable RF channel for recovery / reboot / FW / factory / ACS restore.
LAB_STABLE_CHANNEL = "149"
# Avoid UNII-1 low (36/37) and upper edge 170+ — poor lab RF vs ch149.
LAB_AVOID_CHANNELS = frozenset(
    {"36", "37"} | {str(n) for n in range(170, 200)}
)
# Preferred manual picks — 149 first; never list avoided channels.
MANUAL_CHANNEL_PREFERENCE = (
    "149", "153", "161", "165", "100", "104", "116", "132", "40", "44", "48"
)
# Sheet lists 5.1–6.4 GHz; UBR630 comment: 5180–5855 MHz (use comment for this platform).
UBR_FREQ_MIN_MHZ = 5180
UBR_FREQ_MAX_MHZ = 5855


def is_lab_avoid_channel(ch: str) -> bool:
    """True for channels this lab must not use (36/37/170+)."""
    n = channel_number(ch)
    if not n or not str(n).isdigit():
        return False
    if n in LAB_AVOID_CHANNELS:
        return True
    return int(n) >= 170


def normalize_lab_channel(ch: str, *, default: str = LAB_STABLE_CHANNEL) -> str:
    """Map empty/Auto/avoided channels to lab-stable 149 for recovery paths."""
    n = channel_number(ch)
    if not n or n == "auto" or is_lab_avoid_channel(n):
        return default
    return n


@dataclass(frozen=True)
class SanityChannelSnap:
    configured_uci: str  # advwireless / wireless channel
    wifi_uci: str  # wireless.wifi1.channel
    active_cli: str  # e.g. "143 (5715 MHz)"
    gui_configured: str = ""
    gui_active: str = ""


def channel_number(text: str) -> str:
    """Extract leading channel digits from '143 ( 5715 MHz )' / '42' / 'Auto'."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.lower() == "auto":
        return "auto"
    match = re.match(r"(\d+)", raw)
    return match.group(1) if match else ""


def is_auto_label(text: str) -> bool:
    return "auto" in str(text or "").strip().lower()


def channels_agree(expected: str, *observed: str) -> bool:
    """True when expected channel number appears in each non-empty observed value."""
    want = channel_number(expected)
    if not want or want == "auto":
        return False
    for obs in observed:
        got = channel_number(obs)
        if not got:
            return False
        if got != want:
            return False
    return True


def active_channel_present(text: str) -> bool:
    """Active Channel is a concrete channel (not empty / dash)."""
    raw = str(text or "").strip()
    if not raw or raw in ("-", "—", "N/A", "n/a"):
        return False
    return bool(channel_number(raw))


def parse_mhz_from_label(text: str) -> int | None:
    """Extract MHz from '36 ( 5180 MHz )' / '36 (5.180 GHz)' / '5180 MHz'."""
    raw = str(text or "").strip()
    if not raw:
        return None
    mhz = re.search(r"(\d{4,5})\s*MHz", raw, re.I)
    if mhz:
        return int(mhz.group(1))
    ghz = re.search(r"(\d+(?:\.\d+)?)\s*GHz", raw, re.I)
    if ghz:
        try:
            return int(round(float(ghz.group(1)) * 1000))
        except ValueError:
            return None
    # Bare Frequency:5.180 style from CLI helpers.
    bare = re.search(r"Frequency:([\d.]+)", raw, re.I)
    if bare:
        try:
            return int(round(float(bare.group(1)) * 1000))
        except ValueError:
            return None
    return None


def in_ubr_frequency_range(mhz: int | None) -> bool:
    """True when MHz is inside UBR630 comment band 5180–5855."""
    if mhz is None:
        return False
    return UBR_FREQ_MIN_MHZ <= int(mhz) <= UBR_FREQ_MAX_MHZ


def _valid_ubr_channel_number(ch: str) -> bool:
    """True for concrete 5 GHz channels the radio can hold (not Auto / avoid list / >165)."""
    if not ch or not str(ch).isdigit():
        return False
    if is_lab_avoid_channel(ch):
        return False
    n = int(ch)
    # Ch 171 (5855 MHz) may appear in the comment band but firmware soft-fails to Auto.
    return 40 <= n <= 165


def options_in_ubr_frequency_range(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """Non-Auto channel options whose label frequency is in the UBR630 band."""
    out: list[dict[str, str]] = []
    for opt in options:
        if opt.get("disabled") or is_auto_label(opt.get("text", "")):
            continue
        ch = channel_number(opt.get("text", "") or opt.get("value", ""))
        if ch and not _valid_ubr_channel_number(ch):
            continue
        mhz = parse_mhz_from_label(opt.get("text", ""))
        if mhz is None:
            # Channel-only value: derive 5 GHz center from channel number.
            if ch and ch.isdigit() and _valid_ubr_channel_number(ch):
                n = int(ch)
                mhz = 5000 + n * 5
        if in_ubr_frequency_range(mhz) and not is_lab_avoid_channel(ch):
            out.append(opt)
    return out


def pick_frequency_targets(
    options: list[dict[str, str]],
    *,
    avoid: str = "",
    count: int = 2,
) -> list[str]:
    """Pick up to `count` distinct applyable in-band channels (prefer known-good)."""
    candidates = options_in_ubr_frequency_range(options)
    avoid_n = channel_number(avoid)
    filtered = [
        o
        for o in candidates
        if channel_number(o.get("text", "") or o.get("value", "")) != avoid_n
        and not is_lab_avoid_channel(o.get("text", "") or o.get("value", ""))
    ] or [
        o
        for o in candidates
        if not is_lab_avoid_channel(o.get("text", "") or o.get("value", ""))
    ]
    if not filtered:
        raise RuntimeError(
            f"[sanity] no Configured Channel options in {UBR_FREQ_MIN_MHZ}-{UBR_FREQ_MAX_MHZ} MHz "
            f"(lab avoids {sorted(LAB_AVOID_CHANNELS)[:5]}…)"
        )

    def _mhz(opt: dict[str, str]) -> int:
        mhz = parse_mhz_from_label(opt.get("text", ""))
        if mhz:
            return mhz
        ch = channel_number(opt.get("text", "") or opt.get("value", ""))
        if ch and ch.isdigit():
            return 5000 + int(ch) * 5
        return 0

    ordered = sorted(filtered, key=_mhz)
    picks: list[str] = []

    # Prefer known-good mid/high band first (never 36/37/170+).
    for pref in MANUAL_CHANNEL_PREFERENCE:
        if len(picks) >= count:
            break
        for o in ordered:
            label = o["text"] or o["value"]
            if channel_number(label) == pref and all(
                channel_number(p) != pref for p in picks
            ):
                picks.append(label)
                break

    # Prefer near-149 band fill over low UNII-1.
    if len(picks) < count:
        for o in sorted(ordered, key=lambda x: abs(_mhz(x) - 5745)):
            if len(picks) >= count:
                break
            label = o["text"] or o["value"]
            if all(channel_number(p) != channel_number(label) for p in picks):
                picks.append(label)
    return picks[:count]


def frequency_and_channel_match(expected_label: str, *observed: str) -> bool:
    """Configured/Active labels share channel; MHz agrees within ~10 (center offset)."""
    want_ch = channel_number(expected_label)
    want_mhz = parse_mhz_from_label(expected_label)
    if not want_ch or want_ch == "auto":
        return False
    for obs in observed:
        got_ch = channel_number(obs)
        if not got_ch or got_ch != want_ch:
            return False
        got_mhz = parse_mhz_from_label(obs)
        if want_mhz and got_mhz and abs(want_mhz - got_mhz) > 15:
            return False
        if got_mhz and not in_ubr_frequency_range(got_mhz):
            return False
    return True


async def open_sanity_radio_properties(
    gui_page,
    *,
    host: str,
    device_creds: dict | None = None,
) -> RadioPropertiesPage:
    if host and device_creds:
        bare = f"{format_luci_url(host).rstrip('/')}/admin/wireless/radio1"
        await gui_page.goto(bare, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=5000)
        stok = await resolve_luci_stok(gui_page)
        url = (
            f"{format_luci_url(host).rstrip('/')}/{stok}/admin/wireless/radio1"
            if stok
            else bare
        )
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=3000)
    page = RadioPropertiesPage(gui_page, local_ip=host or "192.168.2.1")
    await page.navigate()
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return page


async def _channel_dropdown(gui_page):
    for locator in (CHANNEL_SELECT_CSS, CHANNEL_SELECT_XPATH):
        element = gui_page.locator(locator).first
        try:
            if await element.count() == 0:
                continue
            await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
            return element
        except Exception:
            continue
    element = gui_page.locator(CHANNEL_SELECT_CSS).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    return element


async def list_configured_channel_options(gui_page) -> list[dict[str, str]]:
    element = await _channel_dropdown(gui_page)
    # Channel list is often filled asynchronously after radio page load.
    for _ in range(8):
        options = await element.evaluate(
            """
            el => Array.from(el.options || []).map(opt => ({
                text: (opt.textContent || "").trim(),
                value: (opt.value || "").trim(),
                disabled: !!opt.disabled
            })).filter(opt => opt.text || opt.value)
            """
        )
        manuals = [
            o
            for o in options
            if not o.get("disabled") and not is_auto_label(o.get("text", ""))
        ]
        if len(options) > 1 and manuals:
            return options
        await gui_page.wait_for_timeout(1000)
    return options


async def read_gui_configured_channel(gui_page) -> str:
    element = await _channel_dropdown(gui_page)
    return await element.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )


async def read_gui_active_channel(gui_page) -> str:
    for locator in (ACTIVE_CHANNEL_CSS, ACTIVE_CHANNEL_XPATH):
        loc = gui_page.locator(locator).first
        try:
            if await loc.count() == 0:
                continue
            await loc.wait_for(state="attached", timeout=5000)
            text = (await loc.inner_text()).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def apply_configured_channel_gui(
    gui_page,
    *,
    target: str,
    host: str = "",
    device_creds: dict | None = None,
) -> str:
    """Select Configured Channel (Auto or channel number/label) → Save → Apply.

    Returns the selected option text.
    """
    want = str(target or "").strip()
    match = None
    options: list[dict[str, str]] = []
    for attempt in range(3):
        await open_sanity_radio_properties(gui_page, host=host, device_creds=device_creds)
        options = await list_configured_channel_options(gui_page)
        if is_auto_label(want):
            match = next(
                (
                    o
                    for o in options
                    if not o["disabled"]
                    and (is_auto_label(o["text"]) or o["value"].lower() == "auto")
                ),
                None,
            )
        else:
            token = channel_number(want) or want
            match = next(
                (
                    o
                    for o in options
                    if not o["disabled"]
                    and (
                        o["value"] == token
                        or channel_number(o["text"]) == token
                        or token in o["text"]
                    )
                ),
                None,
            )
        if match is not None:
            break
        print(
            f"[sanity] Configured Channel options incomplete "
            f"(attempt {attempt + 1}/3, n={len(options)}); reloading radio page",
            flush=True,
        )
        await gui_page.wait_for_timeout(2000)
    if match is None:
        raise RuntimeError(
            f"[sanity] Configured Channel '{target}' not found; "
            f"available={[o['text'] for o in options[:12]]}"
        )
    element = await _channel_dropdown(gui_page)
    try:
        if await element.is_visible():
            await element.select_option(value=match["value"])
        else:
            raise RuntimeError("hidden")
    except Exception:
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
    return match["text"] or match["value"]


def pick_manual_channel(options: list[dict[str, str]], *, avoid: str = "") -> str:
    """Pick a concrete channel from dropdown options (not Auto; skip 36/37/170+)."""
    enabled = [o for o in options if not o["disabled"] and not is_auto_label(o["text"])]
    avoid_n = channel_number(avoid)

    def _ok(opt: dict[str, str]) -> bool:
        n = channel_number(opt["text"]) or channel_number(opt["value"])
        if not n:
            return False
        if avoid_n and n == avoid_n:
            return False
        if is_lab_avoid_channel(n):
            return False
        return True

    candidates = [o for o in enabled if _ok(o)]
    for pref in MANUAL_CHANNEL_PREFERENCE:
        for o in candidates:
            if channel_number(o["text"]) == pref or o["value"] == pref:
                return o["text"] or pref
    if candidates:
        return candidates[0]["text"] or candidates[0]["value"]
    raise RuntimeError("[sanity] no manual Configured Channel options available (lab avoids 36/37/170+)")


async def read_channel_snap(ssh, *, radio_idx: int = RADIO_IDX) -> SanityChannelSnap:
    await ensure_sanity_ssh_open(ssh)
    configured = (
        await sanity_ssh_run(ssh, f"uci -q get advwireless.ath{radio_idx}.channel", timeout_s=30)
    ).strip()
    wifi = (
        await sanity_ssh_run(ssh, f"uci -q get wireless.wifi{radio_idx}.channel", timeout_s=30)
    ).strip()
    # Prefer iw scalar so clean_ssh_output keeps Frequency/channel.
    freq = (
        await sanity_ssh_run(
            ssh,
            f"iwconfig ath{radio_idx} 2>/dev/null | tr '\\n' ' ' | "
            f"sed -n 's/.*Frequency:\\([0-9.]*\\) *GHz.*/\\1/p' | head -1",
            timeout_s=30,
        )
    ).strip()
    active = ""
    if freq:
        try:
            freq_mhz = int(round(float(freq) * 1000))
            active = parse_iwconfig_active_channel(f"Frequency:{freq} GHz") or f"{freq_mhz} MHz"
        except ValueError:
            active = ""
    if not active:
        iw_ch = (
            await sanity_ssh_run(
                ssh,
                f"iw dev ath{radio_idx} info 2>/dev/null | tr '\\n' ' ' | "
                f"sed -n 's/.*channel \\([0-9]*\\) (\\([0-9]*\\) MHz).*/\\1 (\\2 MHz)/p' | head -1",
                timeout_s=30,
            )
        ).strip()
        active = iw_ch
    return SanityChannelSnap(configured_uci=configured, wifi_uci=wifi, active_cli=active)


def channel_to_mhz(ch: str) -> int | None:
    """5 GHz center MHz for a channel number (5000 + 5*ch)."""
    n = channel_number(ch)
    if not n or not str(n).isdigit() or n == "auto":
        return None
    return 5000 + int(n) * 5


def on_air_channel_matches(active_cli: str, want_ch: str, *, tol_mhz: int = 40) -> bool:
    """True when live ath frequency is near the expected channel (HT80 center ok)."""
    want = channel_number(want_ch)
    if not want or want == "auto":
        return False
    if channels_agree(want, active_cli):
        return True
    want_mhz = channel_to_mhz(want)
    got_mhz = parse_mhz_from_label(active_cli)
    if want_mhz and got_mhz and abs(want_mhz - got_mhz) <= tol_mhz:
        return True
    return False


async def acs_scan_in_progress(ssh) -> bool:
    """True when ucidyn apply reports CAC/ACS/Spectrum Scan still running."""
    await ensure_sanity_ssh_open(ssh)
    try:
        out = str(await sanity_ssh_run(ssh, "ucidyn apply 2>&1 | tail -5", timeout_s=60) or "")
    except Exception as exc:
        out = str(exc)
    text = out.lower()
    return "acs" in text or "cac" in text or "spectrum scan" in text


async def wait_acs_idle(
    ssh,
    *,
    timeout_s: int = 180,
    poll_s: float = 5.0,
    label: str = "ACS",
) -> bool:
    """Wait until CAC/ACS/Spectrum Scan is no longer blocking ucidyn apply."""
    import asyncio
    import time

    deadline = time.monotonic() + max(10, timeout_s)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        busy = await acs_scan_in_progress(ssh)
        if not busy:
            if attempt > 1:
                print(f"[sanity] {label}: ACS/CAC idle after {attempt} poll(s)", flush=True)
            return True
        if attempt == 1 or attempt % 6 == 0:
            left = int(deadline - time.monotonic())
            print(f"[sanity] {label}: ACS/CAC still running ({left}s left)", flush=True)
        await asyncio.sleep(max(1.0, poll_s))
    print(f"[sanity] {label}: ACS/CAC still busy after {timeout_s}s", flush=True)
    return False


async def force_radio_channel_reload(
    ssh,
    channel: str,
    *,
    settle_seconds: int = 25,
    htmode: str | None = None,
) -> None:
    """
    Force a concrete channel when ACS/CAC blocks soft apply.

    Steps: disable ACS knobs → set UCI → hard wifi bounce → verify on-air MHz.
    Never leave the radio on 36/37/170+.
    """
    import asyncio

    await ensure_sanity_ssh_open(ssh)
    ch = str(channel or "").strip()
    if not ch:
        return
    if is_auto_label(ch):
        value = "auto"
    else:
        value = normalize_lab_channel(ch, default=LAB_STABLE_CHANNEL)
        if value != (channel_number(ch) or ch):
            print(
                f"[sanity] force_radio_channel_reload: remapped {ch!r} → {value} "
                f"(lab avoids 36/37/170+)",
                flush=True,
            )

    cur_ht = ""
    try:
        cur_ht = str(
            await sanity_ssh_run(
                ssh, f"uci -q get wireless.wifi{RADIO_IDX}.htmode", timeout_s=20
            )
            or ""
        ).strip()
    except Exception:
        cur_ht = ""
    keep_ht = str(htmode or cur_ht or "HT80").strip() or "HT80"
    want_mhz = channel_to_mhz(value) if value != "auto" else None

    # Abort ACS and pin channel/width in UCI before bouncing the radio.
    # Do NOT killall -9 hostapd — that can leave wifi1 with no ath VAP until reboot.
    prep = (
        "set +e; "
        "uci set advwireless.ath1.kwndfsacs=0 2>/dev/null || true; "
        "uci set advwireless.ath1.dcsstatus=0 2>/dev/null || true; "
        f"uci set wireless.wifi{RADIO_IDX}.disabled=0; "
        f"uci set wireless.wifi{RADIO_IDX}.channel={value}; "
        f"uci set wireless.wifi{RADIO_IDX}.htmode={keep_ht}; "
        f"uci set advwireless.ath{RADIO_IDX}.channel={value} 2>/dev/null || true; "
        # Keep SSID broadcast so CPE can (re)associate after channel bounce.
        f"uci delete wireless.@wifi-iface[{RADIO_IDX}].hidden 2>/dev/null || true; "
        f"uci set wireless.@wifi-iface[{RADIO_IDX}].hidden=0; "
        "uci commit wireless; "
        "uci commit advwireless 2>/dev/null || true; "
        "wifi down; "
        "sleep 5; "
        "wifi up"
    )
    try:
        await sanity_ssh_run(ssh, prep, timeout_s=180)
    except Exception as exc:
        print(f"[sanity] force_radio_channel_reload bounce note: {exc}", flush=True)

    await asyncio.sleep(max(10, min(settle_seconds, 25)))
    # If ath VAP never came back, one more wifi up (still no hostapd -9).
    try:
        ath_check = await sanity_ssh_run(
            ssh,
            f"iwconfig ath{RADIO_IDX} 2>/dev/null | head -1",
            timeout_s=20,
        )
        if "ath" not in str(ath_check or "").lower():
            print(
                f"[sanity] force_radio_channel_reload: ath{RADIO_IDX} missing — wifi up retry",
                flush=True,
            )
            await sanity_ssh_run(
                ssh, "set +e; wifi down; sleep 4; wifi up; sleep 12", timeout_s=120
            )
    except Exception as exc:
        print(f"[sanity] force_radio_channel_reload ath-check note: {exc}", flush=True)

    # Soft apply once ACS is idle (may still report scan briefly after wifi up).
    await wait_acs_idle(ssh, timeout_s=60, poll_s=4.0, label="force-ch")
    if value != "auto":
        try:
            await sanity_ssh_run(
                ssh,
                "set +e; "
                f"ucidyn set wireless.wifi{RADIO_IDX}.channel {value}; "
                f"ucidyn set wireless.wifi{RADIO_IDX}.htmode {keep_ht}; "
                f"ucidyn set advwireless.ath{RADIO_IDX}.channel {value}; "
                "ucidyn apply; true",
                timeout_s=120,
            )
        except Exception as exc:
            print(f"[sanity] force_radio_channel_reload ucidyn note: {exc}", flush=True)

    # Runtime freq nudge if UCI says target but ath is still on wrong band (e.g. 5.18).
    if want_mhz:
        snap = await read_channel_snap(ssh)
        if not on_air_channel_matches(snap.active_cli, value):
            ghz = f"{want_mhz / 1000.0:.3f}".rstrip("0").rstrip(".")
            print(
                f"[sanity] force_radio_channel_reload: on-air {snap.active_cli!r} "
                f"!= ch{value} — iwconfig freq {ghz}G",
                flush=True,
            )
            try:
                await sanity_ssh_run(
                    ssh,
                    f"iwconfig ath{RADIO_IDX} freq {ghz}G 2>/dev/null || "
                    f"iw dev ath{RADIO_IDX} set freq {want_mhz} 2>/dev/null || true",
                    timeout_s=30,
                )
            except Exception as exc:
                print(f"[sanity] force_radio_channel_reload freq note: {exc}", flush=True)
            await asyncio.sleep(5)

    if settle_seconds:
        await asyncio.sleep(max(0, settle_seconds - 8))

    snap = await read_channel_snap(ssh)
    # ACS sometimes rewrites UCI back to 36 after wifi up even when on-air is 149.
    if value != "auto" and (
        is_lab_avoid_channel(snap.wifi_uci)
        or (
            channel_number(snap.wifi_uci)
            and channel_number(snap.wifi_uci) != value
        )
    ):
        print(
            f"[sanity] force_radio_channel_reload: UCI drifted to {snap.wifi_uci!r} "
            f"— re-pin {value}/{keep_ht}",
            flush=True,
        )
        try:
            await sanity_ssh_run(
                ssh,
                "set +e; "
                "uci set advwireless.ath1.kwndfsacs=0 2>/dev/null || true; "
                f"uci set wireless.wifi{RADIO_IDX}.channel={value}; "
                f"uci set wireless.wifi{RADIO_IDX}.htmode={keep_ht}; "
                f"uci set advwireless.ath{RADIO_IDX}.channel={value} 2>/dev/null || true; "
                "uci commit wireless; "
                "uci commit advwireless 2>/dev/null || true; "
                f"ucidyn set wireless.wifi{RADIO_IDX}.channel {value} 2>/dev/null || true; "
                f"ucidyn set wireless.wifi{RADIO_IDX}.htmode {keep_ht} 2>/dev/null || true; "
                "ucidyn apply 2>/dev/null || true",
                timeout_s=120,
            )
            await asyncio.sleep(5)
            snap = await read_channel_snap(ssh)
        except Exception as exc:
            print(f"[sanity] force_radio_channel_reload re-pin note: {exc}", flush=True)

    print(
        f"[sanity] force_radio_channel_reload done: want={value}/{keep_ht} "
        f"uci={snap.wifi_uci!r} active={snap.active_cli!r}",
        flush=True,
    )


async def bts_radio_essid_live(ssh) -> str:
    """Return live ath ESSID (empty if AP down / ACS blanked)."""
    await ensure_sanity_ssh_open(ssh)
    raw = await sanity_ssh_run(
        ssh,
        f"iwconfig ath{RADIO_IDX} 2>/dev/null | sed -n 's/.*ESSID:\\(.*\\)/\\1/p' | "
        "tr -d '\"' | head -1",
        timeout_s=30,
    )
    return str(raw or "").strip()


async def apply_configured_channel_ssh(
    ssh,
    channel: str,
    *,
    settle_seconds: int = 15,
    force_reload: bool | None = None,
) -> None:
    """Restore configured channel via UCI (Auto → auto).

    For non-Auto channels, fall back to wifi down/up when ACS blocks ucidyn apply.
    Use force_reload=True to always bounce the radio (post-ACS recovery).
    """
    import asyncio

    await ensure_sanity_ssh_open(ssh)
    ch = str(channel or "").strip()
    if not ch:
        return
    if is_auto_label(ch):
        value = "auto"
    else:
        value = channel_number(ch) or ch

    use_force = bool(force_reload)
    if use_force is False and force_reload is None and not is_auto_label(ch):
        # Prefer soft apply first for normal restore.
        use_force = False

    try:
        await sanity_ssh_run(
            ssh, f"ucidyn set wireless.wifi{RADIO_IDX}.channel {value}", timeout_s=60
        )
        await sanity_ssh_run(
            ssh, f"ucidyn set advwireless.ath{RADIO_IDX}.channel {value}", timeout_s=60
        )
        apply_out = await sanity_ssh_run(ssh, "ucidyn apply", timeout_s=120)
        apply_text = str(apply_out or "")
        if "ACS" in apply_text or "CAC" in apply_text or "Spectrum Scan" in apply_text:
            print(
                f"[sanity] ucidyn apply blocked by scan ({apply_text.strip()[:80]}) "
                f"— forcing radio reload for channel {value}",
                flush=True,
            )
            await force_radio_channel_reload(
                ssh, value, settle_seconds=max(settle_seconds, 25)
            )
            return
    except Exception:
        if not is_auto_label(ch):
            await force_radio_channel_reload(
                ssh, value, settle_seconds=max(settle_seconds, 25)
            )
            return
    if force_reload and not is_auto_label(ch):
        await force_radio_channel_reload(
            ssh, value, settle_seconds=max(settle_seconds, 25)
        )
        return
    if settle_seconds:
        await asyncio.sleep(settle_seconds)


async def wait_active_channel_stable(
    ssh,
    *,
    retries: int = 12,
    delay_s: float = 3.0,
) -> str:
    import asyncio

    last = ""
    for attempt in range(retries):
        snap = await read_channel_snap(ssh)
        last = snap.active_cli
        if active_channel_present(last):
            return last
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return last
