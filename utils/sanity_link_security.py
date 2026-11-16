"""Sanity-only Link Security (Wireless → Radio 1 → Encryption / Key / Network Secret)."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from config.defaults import SANITY_TEST_VALUES, WIRELESS_SECURITY_TEST_VALUES
from pages.locators import RadioPropertiesLocators, UITimeouts, WirelessSecurityLocators
from pages.radio_properties_page import RadioPropertiesPage
from utils.net_utils import format_luci_url
from utils.sanity_commands import SanityCommands
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.ui_helpers import execute_triple_apply, fill_luci_input


RADIO_IDX = 1
AES_GUI = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_GUI"]
AES_UCI = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_UCI"]
NONE_GUI = WIRELESS_SECURITY_TEST_VALUES["INSECURE_ENCRYPTION_GUI"]
NONE_UCI = WIRELESS_SECURITY_TEST_VALUES["INSECURE_ENCRYPTION_UCI"]
AES_TEST_KEY = str(SANITY_TEST_VALUES.get("link_security_aes_key") or "SanityAesKey27")
AES_TEST_NWKSECRET = str(SANITY_TEST_VALUES.get("link_security_aes_nwksecret") or "SanAes27")
APPLY_SETTLE_MS = 15000


@dataclass(frozen=True)
class SanityWirelessSnap:
    encryption: str
    key: str
    nwksecret: str


def encryption_uci_label(uci: str) -> str:
    raw = str(uci or "").strip().lower()
    if raw in ("none", ""):
        return NONE_GUI
    if "ccmp-256" in raw or raw == AES_UCI.lower():
        return AES_GUI
    return raw or "(empty)"


def _clean_secret(raw: str) -> str:
    return str(raw or "").strip().strip("'\"")


def _enc_matches(actual: str, expected_uci: str) -> bool:
    actual_n = _clean_secret(actual).lower()
    expected = str(expected_uci or "").strip().strip("'\"").lower()
    if expected in ("", "none"):
        return actual_n in ("", "none")
    if expected == AES_UCI.lower() or "ccmp-256" in expected:
        return actual_n == AES_UCI.lower() or "ccmp-256" in actual_n or actual_n in (
            "psk2+ccmp-256",
            "psk2+aes-256",
            "ccmp-256",
        )
    return actual_n == expected


async def read_sanity_wireless_snap(ssh, *, radio_idx: int = RADIO_IDX) -> SanityWirelessSnap:
    await ensure_sanity_ssh_open(ssh)
    enc = (await sanity_ssh_run(ssh, SanityCommands.get_encryption(radio_idx), timeout_s=30)).strip()
    key = _clean_secret(await sanity_ssh_run(ssh, SanityCommands.get_key(radio_idx), timeout_s=30))
    nwk = _clean_secret(await sanity_ssh_run(ssh, SanityCommands.get_nwksecret(radio_idx), timeout_s=30))
    return SanityWirelessSnap(encryption=enc, key=key, nwksecret=nwk)


async def verify_sanity_link_security_backend(
    ssh,
    *,
    encryption_uci: str,
    key: str = "",
    nwksecret: str = "",
    check_secrets: bool = False,
    retries: int = 5,
    delay_s: float = 3.0,
) -> tuple[bool, SanityWirelessSnap]:
    """SSH/UCI backend check for encryption (+ optional key / Network Secret)."""
    import asyncio

    last = SanityWirelessSnap(encryption="", key="", nwksecret="")
    for attempt in range(retries):
        last = await read_sanity_wireless_snap(ssh)
        enc_ok = _enc_matches(last.encryption, encryption_uci)
        key_ok = (not check_secrets) or (not key) or (_clean_secret(last.key) == _clean_secret(key))
        nwk_ok = (not check_secrets) or (not nwksecret) or (
            _clean_secret(last.nwksecret) == _clean_secret(nwksecret)
        )
        if enc_ok and key_ok and nwk_ok:
            return True, last
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False, last


async def open_sanity_radio1(
    gui_page,
    *,
    host: str,
    device_creds: dict | None = None,
) -> None:
    """Wireless → Radio 1 (Properties)."""
    if host and device_creds:
        url = f"{format_luci_url(host).rstrip('/')}/admin/wireless/radio1"
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds)
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await sanity_login_if_needed(gui_page, host, device_creds)
    page = RadioPropertiesPage(gui_page, local_ip=host or "192.168.2.1")
    await page.navigate()
    await gui_page.locator(WirelessSecurityLocators.ENCRYPTION_DROPDOWN).first.wait_for(
        state="attached",
        timeout=UITimeouts.ELEMENT_WAIT_MS,
    )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def read_sanity_gui_encryption(gui_page) -> str:
    element = gui_page.locator(WirelessSecurityLocators.ENCRYPTION_DROPDOWN).first
    return await element.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )


async def select_sanity_encryption_gui(gui_page, option_text: str) -> None:
    element = gui_page.locator(WirelessSecurityLocators.ENCRYPTION_DROPDOWN).first
    options = await element.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )
    match = next((opt for opt in options if opt["text"] == option_text and not opt["disabled"]), None)
    if match is None:
        # Tolerate "None" / "none" / "Open" labels for open mode.
        lowered = option_text.lower()
        match = next(
            (
                opt
                for opt in options
                if not opt["disabled"]
                and (
                    opt["text"].lower() == lowered
                    or (lowered in ("none", "open") and opt["text"].lower() in ("none", "open"))
                )
            ),
            None,
        )
    if match is None:
        raise RuntimeError(
            f"[sanity] Encryption option '{option_text}' not found; available={[o['text'] for o in options]}"
        )
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


async def apply_sanity_encryption_gui(
    gui_page,
    *,
    encryption_gui: str,
    key: str = "",
    nwksecret: str = "",
    host: str = "",
    device_creds: dict | None = None,
    settle_ms: int = APPLY_SETTLE_MS,
) -> None:
    """Wireless → Radio 1: set Encryption (+ key / Network Secret when AES), Save/Apply."""
    await open_sanity_radio1(gui_page, host=host, device_creds=device_creds)
    await select_sanity_encryption_gui(gui_page, encryption_gui)
    if encryption_gui.lower() not in ("none", "open"):
        if key:
            key_loc = gui_page.locator(RadioPropertiesLocators.ENCRYPTION_KEY_INPUT).first
            if await key_loc.count():
                try:
                    await fill_luci_input(gui_page, RadioPropertiesLocators.ENCRYPTION_KEY_INPUT, key)
                except Exception:
                    pass
        if nwksecret:
            nwk_loc = gui_page.locator(RadioPropertiesLocators.NETWORK_SECRET_INPUT).first
            if await nwk_loc.count():
                try:
                    await fill_luci_input(gui_page, RadioPropertiesLocators.NETWORK_SECRET_INPUT, nwksecret)
                except Exception:
                    pass
    await execute_triple_apply(gui_page, WirelessSecurityLocators.RADIO_URL_CHUNK)
    await gui_page.wait_for_timeout(max(1000, int(settle_ms)))


async def apply_sanity_encryption_ssh(
    ssh,
    *,
    encryption_uci: str,
    key: str = "",
    nwksecret: str = "",
    settle_seconds: int = 15,
) -> None:
    """Set encryption (+ optional key / nwksecret) via ucidyn apply."""
    await ensure_sanity_ssh_open(ssh)
    enc_q = str(encryption_uci).strip()
    # Match wireless-security: do not over-quote; OpenWrt ash treats + literally.
    await sanity_ssh_run(
        ssh,
        f"ucidyn set wireless.@wifi-iface[{RADIO_IDX}].encryption {enc_q}",
        timeout_s=60,
    )
    # Always write key / Network Secret when provided (needed for AES apply and full snap restore).
    if key:
        await sanity_ssh_run(
            ssh,
            f"ucidyn set wireless.@wifi-iface[{RADIO_IDX}].key {shlex.quote(key)}",
            timeout_s=60,
        )
    if nwksecret:
        await sanity_ssh_run(
            ssh,
            f"ucidyn set wireless.wifi{RADIO_IDX}.nwksecret {shlex.quote(nwksecret)}",
            timeout_s=60,
        )
    try:
        await sanity_ssh_run(ssh, "ucidyn apply", timeout_s=120)
    except Exception:
        pass
    if settle_seconds:
        import asyncio

        await asyncio.sleep(settle_seconds)


async def verify_sanity_encryption_uci(
    ssh,
    expected_uci: str,
    *,
    retries: int = 5,
    delay_s: float = 3.0,
) -> bool:
    ok, _ = await verify_sanity_link_security_backend(
        ssh,
        encryption_uci=expected_uci,
        check_secrets=False,
        retries=retries,
        delay_s=delay_s,
    )
    return ok


async def verify_sanity_secrets_uci(
    ssh,
    *,
    key: str = "",
    nwksecret: str = "",
    retries: int = 5,
    delay_s: float = 3.0,
) -> bool:
    """Confirm Key / Network Secret UCI match expected values (when provided)."""
    if not key and not nwksecret:
        return True
    # Use current encryption as don't-care by reading snap first then checking secrets only.
    import asyncio

    expect_key = _clean_secret(key)
    expect_nwk = _clean_secret(nwksecret)
    for attempt in range(retries):
        snap = await read_sanity_wireless_snap(ssh)
        key_ok = (not expect_key) or (_clean_secret(snap.key) == expect_key)
        nwk_ok = (not expect_nwk) or (_clean_secret(snap.nwksecret) == expect_nwk)
        if key_ok and nwk_ok:
            return True
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False


async def restore_sanity_wireless_snap(
    ssh,
    snap: SanityWirelessSnap,
    *,
    settle_seconds: int = 15,
) -> None:
    enc = str(snap.encryption or "").strip().strip("'\"")
    if not enc:
        enc = NONE_UCI
    key = _clean_secret(snap.key)
    nwk = _clean_secret(snap.nwksecret)
    await apply_sanity_encryption_ssh(
        ssh,
        encryption_uci=enc,
        key=key,
        nwksecret=nwk,
        settle_seconds=settle_seconds,
    )
