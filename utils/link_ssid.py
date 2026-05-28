"""Link SSID helpers — delegates to AIRTEL_SSID_GEN when auto_credentials is enabled."""

from __future__ import annotations

from typing import Any

from config.defaults import DEFAULT_VALUES, LINK_SSID
from pages.locators import RadioPropertiesLocators
from utils.link_formation import (
    ensure_p2mp_link_credentials,
    link_auto_enabled,
    resolve_link_credentials_ssh,
)
from utils.parsers import extract_uci_value
from pages.commands import RootCommands
from utils.ui_helpers import execute_triple_apply


def resolve_link_ssid(profile_bundle=None) -> str:
    """Return expected link SSID (from generator config or static profile)."""
    if profile_bundle is not None:
        active = profile_bundle.active
        if link_auto_enabled(active):
            return str(active.get("link", {}).get("ssid") or LINK_SSID)
        ssid = active.get("link", {}).get("ssid")
        if ssid:
            return str(ssid).strip()
    return DEFAULT_VALUES.get("SSID", LINK_SSID)


async def _current_bts_ssid(root_ssh, radio_idx: int = 1) -> str:
    raw = (await root_ssh.send_command(RootCommands.get_ssid(radio_idx))).result
    return extract_uci_value(str(raw or "").strip())


async def ensure_bts_link_ssid_ssh(
    root_ssh,
    *,
    ssid: str | None = None,
    radio_idx: int = 1,
    profile: dict[str, Any] | None = None,
) -> bool:
    """
    Sync P2MP link on BTS (and CPE when profile + cpe_ssh provided via ensure_p2mp_link_credentials).

    When link.auto_credentials is true (default), uses AIRTEL_SSID_GEN from BTS serial — no manual SSID.
    """
    if profile and link_auto_enabled(profile):
        await ensure_p2mp_link_credentials(
            bts_ssh=root_ssh,
            profile=profile,
            bts_radio_idx=radio_idx,
        )
        return True

    target = ssid or LINK_SSID
    current = await _current_bts_ssid(root_ssh, radio_idx)
    if current == target:
        print(f"[link] BTS SSID already '{target}'.")
        return False

    print(f"[link] Restoring BTS SSID '{current}' -> '{target}' via SSH.")
    await root_ssh.send_command(f"uci set wireless.@wifi-iface[{radio_idx}].ssid='{target}'")
    await root_ssh.send_command("uci commit wireless")
    await root_ssh.send_command("wifi reload 2>/dev/null || /etc/init.d/network reload")
    restored = await _current_bts_ssid(root_ssh, radio_idx)
    if restored != target:
        print(f"[link] WARN: BTS SSID after SSH apply is '{restored}' (wanted '{target}').")
        return False
    print(f"[link] BTS SSID restored to '{target}'.")
    return True


async def ensure_bts_link_ssid_gui(
    gui_page,
    root_ssh,
    *,
    ssid: str | None = None,
    radio_url_chunk: str = "/admin/wireless/radio1",
    profile: dict[str, Any] | None = None,
) -> bool:
    """GUI fallback for static SSID only; auto_credentials uses SSH AIRTEL path."""
    if profile and link_auto_enabled(profile):
        creds = await resolve_link_credentials_ssh(root_ssh, profile)
        target = creds.ssid
    else:
        target = ssid or LINK_SSID

    current = await _current_bts_ssid(root_ssh)
    if current == target:
        print(f"[link] BTS SSID already '{target}' (GUI check skipped).")
        return False

    print(f"[link] Restoring BTS SSID '{current}' -> '{target}' via GUI.")
    element = gui_page.locator(RadioPropertiesLocators.SSID_INPUT).first
    await element.wait_for(state="visible", timeout=15000)
    await element.clear()
    await element.fill(target)
    await execute_triple_apply(gui_page, radio_url_chunk)

    restored = await _current_bts_ssid(root_ssh)
    if restored != target:
        print(f"[link] WARN: BTS SSID after GUI apply is '{restored}' (wanted '{target}').")
        return False
    print(f"[link] BTS SSID restored to '{target}' via GUI.")
    return True
