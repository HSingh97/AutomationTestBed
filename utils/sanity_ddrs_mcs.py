"""Sanity-only DDRS / MCS helpers (Wireless → Radio 1 → DDRS/ATPC)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pages.locators import RadioPropertiesLocators, UITimeouts
from pages.radio_properties_page import RadioPropertiesPage
from utils.ddrs_ui_validation import find_mcs_option_text
from utils.net_utils import format_luci_url
from utils.parsers import parse_ddrs_status
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.ui_helpers import execute_form_save, execute_triple_apply


RADIO_IDX = 1
DDRS_URL_CHUNK = "/admin/wireless/radio1/ddrs1"

# GUI labels
SPATIAL_AUTO = "Auto"
SPATIAL_DUAL = "Dual"
SPATIAL_SINGLE = "Single"
DDRS_ENABLE = "Enable"
DDRS_DISABLE = "Disable"

# This firmware: spatialstream 1=Single, 2=Dual, 3=Auto (not 0/1/2).
SPATIAL_TO_UCI = {SPATIAL_SINGLE: "1", SPATIAL_DUAL: "2", SPATIAL_AUTO: "3"}
SPATIAL_FROM_UCI = {
    "0": "single",  # legacy
    "1": "single",
    "2": "dual",
    "3": "auto",
    "single": "single",
    "dual": "dual",
    "auto": "auto",
}
DDRS_UCI = {DDRS_DISABLE: "0", DDRS_ENABLE: "1"}

MAX_MOD_LOCATOR = "#maxrateid, select[name='txparam.ath1.ddrsmaxrate']"
MIN_MOD_LOCATOR = "#minrateid, select[name='txparam.ath1.ddrsminrate']"
MAX_DUAL_LOCATOR = "#maxdualmcs, select[name='txparam.ath1.maxdualmcs']"
MAX_SINGLE_LOCATOR = "#maxsinglemcs, select[name='txparam.ath1.maxsinglemcs']"
MOD_INDEX_LOCATOR = RadioPropertiesLocators.MODULATION_INDEX_DROPDOWN

_MCS_RE = re.compile(r"MCS\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class SanityDdrsSnap:
    ddrs_status: str  # enable|disable
    spatial: str  # single|dual|auto
    spatial_uci: str  # raw UCI for exact restore
    ddrs_uci: str
    ddrsrate: str
    ddrsmaxrate: str
    ddrsminrate: str
    maxsinglemcs: str
    maxdualmcs: str


def _uci_get(radio_idx: int, key: str) -> str:
    return f"uci -q get txparam.ath{radio_idx}.{key}"


def _parse_spatial(raw: str) -> str:
    return SPATIAL_FROM_UCI.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())


def _parse_ddrs(raw: str) -> str:
    return parse_ddrs_status(raw) or "enable"


async def read_sanity_ddrs_snap(ssh, *, radio_idx: int = RADIO_IDX) -> SanityDdrsSnap:
    await ensure_sanity_ssh_open(ssh)
    raw_ddrs = (await sanity_ssh_run(ssh, _uci_get(radio_idx, "ddrsstatus"), timeout_s=30)).strip()
    raw_spatial = (await sanity_ssh_run(ssh, _uci_get(radio_idx, "spatialstream"), timeout_s=30)).strip()
    return SanityDdrsSnap(
        ddrs_status=_parse_ddrs(raw_ddrs),
        spatial=_parse_spatial(raw_spatial),
        spatial_uci=raw_spatial or "3",
        ddrs_uci=raw_ddrs or "1",
        ddrsrate=(await sanity_ssh_run(ssh, _uci_get(radio_idx, "ddrsrate"), timeout_s=30)).strip(),
        ddrsmaxrate=(await sanity_ssh_run(ssh, _uci_get(radio_idx, "ddrsmaxrate"), timeout_s=30)).strip(),
        ddrsminrate=(await sanity_ssh_run(ssh, _uci_get(radio_idx, "ddrsminrate"), timeout_s=30)).strip(),
        maxsinglemcs=(await sanity_ssh_run(ssh, _uci_get(radio_idx, "maxsinglemcs"), timeout_s=30)).strip(),
        maxdualmcs=(await sanity_ssh_run(ssh, _uci_get(radio_idx, "maxdualmcs"), timeout_s=30)).strip(),
    )


async def open_sanity_ddrs_page(
    gui_page,
    *,
    host: str,
    device_creds: dict | None = None,
) -> RadioPropertiesPage:
    """Wireless → Radio 1 → DDRS/ATPC."""
    if host and device_creds:
        url = f"{format_luci_url(host).rstrip('/')}/admin/wireless/radio1/ddrs1"
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds)
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds)
    page = RadioPropertiesPage(gui_page, local_ip=host or "192.168.2.1")
    await page.open_ddrs_atpc()
    await gui_page.locator(RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN).first.wait_for(
        state="attached",
        timeout=45000,
    )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return page


async def _dropdown_options(gui_page, locator: str) -> list[dict[str, str]]:
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    return await element.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )


async def _select_dropdown_text(gui_page, locator: str, option_text: str) -> None:
    options = await _dropdown_options(gui_page, locator)
    match = next(
        (o for o in options if o["text"] == option_text and not o["disabled"]),
        None,
    )
    if match is None:
        match = next(
            (o for o in options if option_text.lower() in o["text"].lower() and not o["disabled"]),
            None,
        )
    if match is None:
        raise RuntimeError(
            f"[sanity] DDRS option '{option_text}' not in {locator}; "
            f"available={[o['text'] for o in options]}"
        )
    element = gui_page.locator(locator).first
    if await element.is_visible():
        await element.select_option(value=match["value"])
    else:
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
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def _select_mcs_dropdown(gui_page, locator: str, mcs_num: int) -> str:
    """Select MCS{n}. On HE UIs that only list MCS0–11, map MCS12–23 → n%12."""
    options = await _dropdown_options(gui_page, locator)
    want = int(mcs_num)
    token = find_mcs_option_text(options, want)
    if not token:
        mapped = want % 12
        token = find_mcs_option_text(options, mapped)
        if token:
            print(
                f"[sanity] MCS{want} not in {locator}; using MCS{mapped} "
                f"(HE GUI MCS0–11 map)",
                flush=True,
            )
            want = mapped
    if not token:
        available = sorted(_mcs_numbers_from_options(options))
        if available and int(mcs_num) >= max(available):
            # Requested "max" MCS beyond GUI — pick highest listed.
            want = max(available)
            token = find_mcs_option_text(options, want)
            if token:
                print(
                    f"[sanity] MCS{mcs_num} not in {locator}; using max available MCS{want}",
                    flush=True,
                )
    if not token:
        raise RuntimeError(
            f"[sanity] MCS{mcs_num} not found in {locator}; "
            f"options={[o['text'] for o in options]}"
        )
    await _select_dropdown_text(gui_page, locator, token)
    return token


def mcs_equiv(got: object, expect: object) -> bool:
    """True if UCI/GUI MCS matches expect, allowing HE MCS12–23 ↔ MCS0–11 map."""
    try:
        g = int(str(got).strip())
        e = int(str(expect).strip())
    except (TypeError, ValueError):
        return str(got or "").strip() == str(expect or "").strip()
    return g == e or (g % 12) == (e % 12)


async def _wait_for_mcs_field(gui_page, locator: str, *, timeout_ms: int = 30000) -> None:
    """After Spatial Stream change + Save, wait for page/fields to reload."""
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=timeout_ms)
    # LuCI may keep element attached but hidden until reload finishes.
    for _ in range(20):
        try:
            visible = await element.is_visible()
            if visible:
                return
        except Exception:
            pass
        await gui_page.wait_for_timeout(500)
    await element.wait_for(state="visible", timeout=timeout_ms)


async def set_spatial_stream_and_wait_reload(
    gui_page,
    spatial: str,
    *,
    wait_locator: str,
) -> None:
    """
    Set Spatial Stream (Auto / Dual / Single), Save so dependent MCS fields reload,
    then wait for the target MCS dropdown to appear.
    """
    await _select_dropdown_text(
        gui_page, RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN, spatial
    )
    await execute_form_save(gui_page)
    try:
        await gui_page.wait_for_load_state("domcontentloaded", timeout=45000)
    except Exception:
        pass
    await gui_page.wait_for_timeout(2000)
    await _wait_for_mcs_field(gui_page, wait_locator)


def _mcs_numbers_from_options(options: list[dict[str, str]]) -> set[int]:
    found: set[int] = set()
    for opt in options:
        if opt.get("disabled"):
            continue
        match = _MCS_RE.search(opt.get("text") or "")
        if match:
            found.add(int(match.group(1)))
    return found


async def read_sanity_mcs_dropdown_range(
    gui_page,
    *,
    spatial: str,
    ddrs_enable: bool = False,
    host: str = "",
    device_creds: dict | None = None,
) -> set[int]:
    """
    DDRS Disable (default) + Spatial Single|Dual → wait reload → return MCS indices
    from Modulation Index dropdown.
    """
    await open_sanity_ddrs_page(gui_page, host=host, device_creds=device_creds)
    ddrs_token = DDRS_ENABLE if ddrs_enable else DDRS_DISABLE
    await _select_dropdown_text(
        gui_page, RadioPropertiesLocators.DDRS_STATUS_DROPDOWN, ddrs_token
    )
    await execute_form_save(gui_page)
    await gui_page.wait_for_timeout(1500)
    wait_loc = MAX_MOD_LOCATOR if ddrs_enable else MOD_INDEX_LOCATOR
    await set_spatial_stream_and_wait_reload(gui_page, spatial, wait_locator=wait_loc)
    options = await _dropdown_options(gui_page, wait_loc)
    return _mcs_numbers_from_options(options)


async def apply_sanity_mcs_manual_single(
    gui_page,
    *,
    mcs_num: int,
    ddrs_enable: bool,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """
    Manual MCS via Single stream:
      Spatial Stream → Single → wait reload → Max/Modulation Index = MCS{n} → Save → Apply.
    """
    await apply_sanity_mcs_manual(
        gui_page,
        mcs_num=mcs_num,
        ddrs_enable=ddrs_enable,
        spatial=SPATIAL_SINGLE,
        host=host,
        device_creds=device_creds,
    )


async def apply_sanity_mcs_manual(
    gui_page,
    *,
    mcs_num: int,
    ddrs_enable: bool,
    spatial: str,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """
    Manual MCS (DDRS Disable or Enable):
      Spatial Stream → Single|Dual → wait reload → Modulation/Max Index → Save → Apply.

    Single → MCS 0–11; Dual → MCS 12–23 (or MCS 0–11 on HE UIs that fold the range).
    """
    spatial = str(spatial).strip().title()
    mcs_num = int(mcs_num)
    if spatial == SPATIAL_SINGLE and not (0 <= mcs_num <= 11):
        raise ValueError(f"[sanity] Single stream expects MCS 0–11, got MCS{mcs_num}")
    if spatial == SPATIAL_DUAL and not (0 <= mcs_num <= 23):
        raise ValueError(f"[sanity] Dual stream expects MCS 0–23, got MCS{mcs_num}")

    await open_sanity_ddrs_page(gui_page, host=host, device_creds=device_creds)
    ddrs_token = DDRS_ENABLE if ddrs_enable else DDRS_DISABLE
    await _select_dropdown_text(
        gui_page, RadioPropertiesLocators.DDRS_STATUS_DROPDOWN, ddrs_token
    )
    await execute_form_save(gui_page)
    await gui_page.wait_for_timeout(1500)

    # DDRS Disable → Modulation Index; Enable+Single → Max Mod; Enable+Dual → Max Mod.
    if not ddrs_enable:
        wait_loc = MOD_INDEX_LOCATOR
    else:
        wait_loc = MAX_MOD_LOCATOR
    await set_spatial_stream_and_wait_reload(gui_page, spatial, wait_locator=wait_loc)
    await _select_mcs_dropdown(gui_page, wait_loc, mcs_num)
    await execute_triple_apply(gui_page, DDRS_URL_CHUNK)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def apply_sanity_mcs_auto_max_dual(
    gui_page,
    *,
    max_dual_mcs: int,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """DDRS Enable + Spatial Auto → wait reload → Max Dual Stream MCS → Save → Apply."""
    await open_sanity_ddrs_page(gui_page, host=host, device_creds=device_creds)
    await _select_dropdown_text(
        gui_page, RadioPropertiesLocators.DDRS_STATUS_DROPDOWN, DDRS_ENABLE
    )
    await execute_form_save(gui_page)
    await gui_page.wait_for_timeout(1500)
    await set_spatial_stream_and_wait_reload(
        gui_page, SPATIAL_AUTO, wait_locator=MAX_DUAL_LOCATOR
    )
    await _select_mcs_dropdown(gui_page, MAX_DUAL_LOCATOR, max_dual_mcs)
    await execute_triple_apply(gui_page, DDRS_URL_CHUNK)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def apply_sanity_mcs_auto_range_max(
    gui_page,
    *,
    max_mcs: int,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """DDRS Enable + Spatial Single → Max MCS in MCS Auto Range → Save → Apply."""
    await apply_sanity_mcs_manual_single(
        gui_page,
        mcs_num=max_mcs,
        ddrs_enable=True,
        host=host,
        device_creds=device_creds,
    )


async def apply_sanity_ddrs_snap_ssh(
    ssh,
    snap: SanityDdrsSnap,
    *,
    settle_seconds: int = 10,
) -> None:
    """Restore DDRS/MCS UCI snapshot via ucidyn (raw UCI values)."""
    import asyncio

    await ensure_sanity_ssh_open(ssh)
    ddrs_uci = str(snap.ddrs_uci or ("1" if snap.ddrs_status.startswith("enable") else "0"))
    spatial_uci = str(snap.spatial_uci or SPATIAL_TO_UCI.get(snap.spatial.title(), "3"))
    cmds = [
        f"ucidyn set txparam.ath{RADIO_IDX}.ddrsstatus {ddrs_uci}",
        f"ucidyn set txparam.ath{RADIO_IDX}.spatialstream {spatial_uci}",
    ]
    if snap.ddrsrate:
        cmds.append(f"ucidyn set txparam.ath{RADIO_IDX}.ddrsrate {snap.ddrsrate}")
    if snap.ddrsmaxrate:
        cmds.append(f"ucidyn set txparam.ath{RADIO_IDX}.ddrsmaxrate {snap.ddrsmaxrate}")
    if snap.ddrsminrate:
        cmds.append(f"ucidyn set txparam.ath{RADIO_IDX}.ddrsminrate {snap.ddrsminrate}")
    if snap.maxsinglemcs:
        cmds.append(f"ucidyn set txparam.ath{RADIO_IDX}.maxsinglemcs {snap.maxsinglemcs}")
    if snap.maxdualmcs:
        cmds.append(f"ucidyn set txparam.ath{RADIO_IDX}.maxdualmcs {snap.maxdualmcs}")
    cmds.append("ucidyn apply")
    for cmd in cmds:
        try:
            await sanity_ssh_run(ssh, cmd, timeout_s=120)
        except Exception:
            pass
    if settle_seconds:
        await asyncio.sleep(settle_seconds)


async def verify_sanity_ddrs_backend(
    ssh,
    *,
    ddrs_enable: bool | None = None,
    spatial: str | None = None,
    ddrsrate: int | None = None,
    ddrsmaxrate: int | None = None,
    maxdualmcs: int | None = None,
    retries: int = 5,
    delay_s: float = 3.0,
) -> tuple[bool, SanityDdrsSnap]:
    """SSH/UCI check for DDRS / Spatial / MCS fields."""
    import asyncio

    last = SanityDdrsSnap("", "", "", "", "", "", "", "", "")
    want_spatial = (spatial or "").strip().lower()
    for attempt in range(retries):
        last = await read_sanity_ddrs_snap(ssh)
        ok = True
        if ddrs_enable is not None:
            is_en = last.ddrs_status.startswith("enable")
            ok = ok and (is_en == ddrs_enable)
        if want_spatial:
            ok = ok and (last.spatial == want_spatial)
        if ddrsrate is not None:
            ok = ok and mcs_equiv(last.ddrsrate, ddrsrate)
        if ddrsmaxrate is not None:
            ok = ok and mcs_equiv(last.ddrsmaxrate, ddrsmaxrate)
        if maxdualmcs is not None:
            ok = ok and mcs_equiv(last.maxdualmcs, maxdualmcs)
        if ok:
            return True, last
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False, last
