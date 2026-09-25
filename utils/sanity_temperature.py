"""Sanity-only board temperature helpers (Dashboard → Summary → System)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pages.commands import RootCommands
from pages.locators import SummaryLocators, TopPanelLocators, UITimeouts
from utils.net_utils import format_luci_url
from utils.parsers import normalize_gui_metric
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open

# Fallback reads when ``tmp101`` is broken on some lab BTS images.
# Avoid ``$((...))`` — some BTS ash builds hit "arithmetic syntax error".
_TEMP_FALLBACK_COMMANDS = (
    # sysfs thermal zones (millidegrees → °C via awk)
    "for z in /sys/class/thermal/thermal_zone*/temp; do "
    "[ -r \"$z\" ] || continue; "
    "awk 'BEGIN{ok=0} /^[0-9-]+$/{printf \"%.1f\\n\", $1/1000; ok=1; exit} END{exit ok?0:1}' \"$z\" "
    "&& break; "
    "done",
    # lm-sensors style, if present
    "sensors 2>/dev/null | awk '/[Tt]emp/{print; exit}'",
    # common hwmon paths (millidegrees)
    "for n in /sys/class/hwmon/hwmon*/temp*_input; do "
    "[ -r \"$n\" ] || continue; "
    "awk 'BEGIN{ok=0} /^[0-9-]+$/{printf \"%.1f\\n\", $1/1000; ok=1; exit} END{exit ok?0:1}' \"$n\" "
    "&& break; "
    "done",
)


@dataclass(frozen=True)
class SanityTempSnap:
    gui_raw: str
    gui_c: float | None
    ssh_raw: str
    ssh_c: float | None


def parse_temp_celsius(raw: str) -> float | None:
    text = str(raw or "").strip()
    # Drop shell prompt glue if still present.
    if "root@" in text:
        text = text.split("root@", 1)[0].strip()
    text = normalize_gui_metric(text)
    if not text or text == "-":
        return None
    # Non-numeric / script error payloads are unavailable, not temperatures.
    lower = text.lower()
    if any(
        token in lower
        for token in (
            "error",
            "failed",
            "not found",
            "no such",
            "arithmetic syntax",
            "permission denied",
        )
    ):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _ssh_output_lines(raw: str) -> str:
    text = str(raw or "").replace("\r", "")
    keep: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("root@"):
            continue
        keep.append(s)
    return " ".join(keep).strip()


def _format_temp_display(raw: str, parsed: float | None) -> str:
    display = raw or "-"
    if parsed is not None and "°" not in display and "C" not in display.upper():
        display = f"{parsed} °C"
    return display


async def open_sanity_summary_system(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Dashboard / Summary / System (home logo → summary page)."""
    if host and device_creds:
        await sanity_login_if_needed(gui_page, host, device_creds)

    opened = False
    if host:
        for path in ("/admin/status/overview", "/"):
            try:
                await gui_page.goto(
                    f"{format_luci_url(host).rstrip('/')}{path}",
                    timeout=90000,
                    wait_until="domcontentloaded",
                )
                opened = True
                break
            except Exception:
                continue
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds)

    if not opened:
        logo = gui_page.locator(TopPanelLocators.LOGO).first
        try:
            if await logo.is_visible(timeout=5000):
                await logo.click()
                await gui_page.wait_for_load_state("domcontentloaded")
                await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
        except Exception:
            pass

    temp = gui_page.locator(SummaryLocators.TEMPERATURE).first
    # LuCI summary widgets can take >10s after ACS/reboot; wait longer.
    # Lab boards often leave temperature as "-" / hidden — don't hard-fail navigation.
    try:
        await temp.wait_for(state="visible", timeout=max(UITimeouts.ELEMENT_WAIT_MS, 45000))
    except Exception:
        # Fall back to any Overview anchor (model / local time).
        for loc in (SummaryLocators.MODEL, SummaryLocators.LOCAL_TIME, SummaryLocators.HW_VERSION):
            try:
                await gui_page.locator(loc).first.wait_for(state="attached", timeout=15000)
                break
            except Exception:
                continue
    await gui_page.wait_for_timeout(UITimeouts.LONG_WAIT_MS)


async def read_gui_temperature(gui_page) -> tuple[str, float | None]:
    loc = gui_page.locator(SummaryLocators.TEMPERATURE).first
    await loc.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    raw = (await loc.inner_text()).strip()
    return raw, parse_temp_celsius(raw)


async def _ssh_temp_from_command(ssh, command: str) -> tuple[str, float | None]:
    response = await ssh.send_command(command, timeout_ops=30)
    raw = _ssh_output_lines(str(response.result or ""))
    parsed = parse_temp_celsius(raw)
    return _format_temp_display(raw, parsed), parsed


async def read_ssh_temperature(ssh) -> tuple[str, float | None]:
    """
    Read board temperature via ``tmp101``, then alternate sensors on failure.

    Non-numeric / error payloads are treated as unavailable (None), not as °C.
    """
    await ensure_sanity_ssh_open(ssh)

    display, parsed = await _ssh_temp_from_command(ssh, RootCommands.GET_TEMP)
    if parsed is not None:
        return display, parsed

    last_raw = display
    for cmd in _TEMP_FALLBACK_COMMANDS:
        try:
            fb_display, fb_parsed = await _ssh_temp_from_command(ssh, cmd)
        except Exception:
            continue
        if fb_display and fb_display != "-":
            last_raw = fb_display
        if fb_parsed is not None:
            return fb_display, fb_parsed

    # Preserve original tmp101 error text when no fallback produced a number.
    return last_raw or "-", None


async def read_sanity_temp_snap(gui_page, ssh) -> SanityTempSnap:
    gui_raw, gui_c = await read_gui_temperature(gui_page)
    ssh_raw, ssh_c = await read_ssh_temperature(ssh)

    # GUI '-' + SSH error/unavailable: one retry after a short settle (async Summary).
    if gui_c is None and ssh_c is None:
        try:
            await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
        except Exception:
            pass
        gui_raw, gui_c = await read_gui_temperature(gui_page)
        ssh_raw, ssh_c = await read_ssh_temperature(ssh)

    return SanityTempSnap(gui_raw=gui_raw, gui_c=gui_c, ssh_raw=ssh_raw, ssh_c=ssh_c)


def temp_in_plausible_range(
    value_c: float | None,
    *,
    min_c: float,
    max_c: float,
) -> bool:
    if value_c is None:
        return False
    return float(min_c) <= float(value_c) <= float(max_c)


def temp_gui_ssh_match(
    gui_c: float | None,
    ssh_c: float | None,
    *,
    tolerance_c: float,
) -> tuple[bool, float]:
    if gui_c is None or ssh_c is None:
        return False, -1.0
    diff = abs(float(gui_c) - float(ssh_c))
    return diff <= float(tolerance_c), diff
