"""Keep BTS on the lab link SSID so P2MP stays up between GUI tests."""

from __future__ import annotations

from config.defaults import DEFAULT_VALUES, LINK_SSID
from pages.commands import RootCommands
from pages.locators import RadioPropertiesLocators
from utils.parsers import extract_uci_value
from utils.ui_helpers import execute_triple_apply


def resolve_link_ssid(profile_bundle=None) -> str:
    if profile_bundle is not None:
        ssid = profile_bundle.active.get("link", {}).get("ssid")
        if ssid:
            return str(ssid).strip()
    return DEFAULT_VALUES.get("SSID", LINK_SSID)


async def _current_bts_ssid(root_ssh, radio_idx: int = 1) -> str:
    raw = (await root_ssh.send_command(RootCommands.get_ssid(radio_idx))).result
    return extract_uci_value(str(raw or "").strip())


async def ensure_bts_link_ssid_ssh(root_ssh, *, ssid: str | None = None, radio_idx: int = 1) -> bool:
    """Set BTS SSID over SSH when it drifted from the lab link value."""
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
) -> bool:
    """Apply lab link SSID through the GUI (Apply x3) when SSH-only change is insufficient."""
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
