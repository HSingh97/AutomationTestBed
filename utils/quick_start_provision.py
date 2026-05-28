"""Factory-reset Quick Start provisioning via LuCI (installer login on fallback IP)."""

from __future__ import annotations

from typing import Any

from pages.locators import CommonLocators, ManagementLocators, NetworkLocators, UITimeouts
from utils.apply_triple import apply_triple as _apply_triple
from utils.gui_login import login_if_needed
from utils.management_flows import open_management_logging
from utils.net_utils import format_http_host
from utils.network_flows import open_network_submenu


async def login_factory_gui(gui_page, host: str, factory_login: dict[str, str]) -> None:
    """Log in with installer credentials on factory / fallback IPv4."""
    creds = {
        "user": str(factory_login.get("username", "installer")),
        "pass": str(factory_login.get("password", "senao123")),
    }
    await gui_page.goto(
        f"https://{format_http_host(host)}/cgi-bin/luci/",
        timeout=UITimeouts.PAGE_LOAD_MS,
        wait_until="commit",
    )
    await login_if_needed(gui_page, host, creds, wait_ms=3000, skip_recovery=True)


async def _try_open_quick_start(gui_page) -> bool:
    for selector in (
        "a[href*='quickstart']",
        "a[href*='quick_start']",
        "li.QuickStart > a.menu",
        "a:has-text('Quick Start')",
    ):
        loc = gui_page.locator(selector).first
        if await loc.count() > 0 and await loc.is_visible(timeout=2000):
            await loc.click()
            await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
            return True
    return False


async def _fill_if_visible(gui_page, selector: str, value: str) -> bool:
    loc = gui_page.locator(selector).first
    if await loc.count() == 0:
        return False
    try:
        await loc.wait_for(state="visible", timeout=3000)
        await loc.fill(value)
        return True
    except Exception:
        return False


async def provision_bts_quick_start_gui(
    gui_page,
    host: str,
    profile: dict[str, Any],
) -> list[str]:
    """
    BTS after factory reset (installer):
    Quick Start (if present) + Network IPv6 + Management NMS syslog IPv6.
    Returns list of completed step labels.
    """
    from utils.vlan_uci import _mgmt_from_profile

    factory_login = profile.get("factory_login", {}) or {}
    mgmt = _mgmt_from_profile(profile)
    nms = profile.get("nms", {}) or {}
    qs = profile.get("quick_start", {}) or {}
    done: list[str] = []

    await login_factory_gui(gui_page, host, factory_login)
    if await _try_open_quick_start(gui_page):
        done.append("quick_start_page")

    prefix = int(mgmt.get("prefix_len", 64))
    v6_bts = str(mgmt.get("ipv6_bts", "")).strip()
    v6_cidr = v6_bts if "/" in v6_bts else f"{v6_bts}/{prefix}"

    for sel, val, label in (
        (qs.get("ipv6_selector", NetworkLocators.IPv6_ADDRESS), v6_cidr, "qs_ipv6"),
        (qs.get("mgmt_vlan_selector", "input[name*='mgmtvlan'], select[name*='mgmtvlan']"), str(mgmt.get("uci_value", "")), "qs_mgmt_vlan"),
        (qs.get("nms_selector", "input[name*='nms'], input[name*='log_ip']"), str(nms.get("syslog_ipv6", "")), "qs_nms"),
    ):
        if val and await _fill_if_visible(gui_page, str(sel), str(val)):
            done.append(label)

    await open_network_submenu(gui_page, "/network/ip")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    if v6_bts and await _fill_if_visible(gui_page, NetworkLocators.IPv6_ADDRESS, v6_cidr):
        await _apply_triple(
            gui_page,
            NetworkLocators.SAVE_BUTTON,
            CommonLocators.APPLY_ICON,
            CommonLocators.CONFIRM_APPLY,
            settle_seconds=15,
            only_if_apply_visible=True,
        )
        done.append("network_ipv6")

    nms_host = str(nms.get("syslog_ipv6", "")).strip()
    nms_port = str(nms.get("syslog_port", "514")).strip()
    if nms_host:
        await open_management_logging(gui_page)
        if await _fill_if_visible(gui_page, ManagementLocators.LOG_IP_XPATH, nms_host):
            if nms_port:
                await _fill_if_visible(gui_page, ManagementLocators.LOG_PORT_XPATH, nms_port)
            await _apply_triple(
                gui_page,
                ManagementLocators.SAVE_BUTTON,
                CommonLocators.APPLY_ICON,
                CommonLocators.CONFIRM_APPLY,
                settle_seconds=10,
                only_if_apply_visible=True,
            )
            done.append("nms_syslog")

    return done


async def provision_cpe_quick_start_note() -> str:
    """
  CPE Quick Start is applied over SSH from the CPE lab PC (factory IPv4).
  LuCI on CPE is not driven from the primary automation host.
  """
    return (
        "CPE: use secondary PC → factory IP for mgmt VLAN UCI (transparent + mgmtvlan). "
        "CPE LuCI Quick Start (mgmt VLAN only) is equivalent to that SSH step."
    )
