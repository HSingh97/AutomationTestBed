"""SANITY_112 — Installer login → Quick Start (Configuration) on BTS & CPE."""

from __future__ import annotations

import re

import pytest_check as check

from pages.locators import UITimeouts
from utils.net_utils import format_luci_url
from utils.sanity_gui import sanity_gui_login_and_verify, sanity_login_if_needed, verify_sanity_gui_authenticated
from utils.sanity_installer_dashboard import installer_gui_creds, logout_gui


async def _loginuser(gui_page) -> str:
    try:
        user = await gui_page.evaluate(
            'typeof loginuser !== "undefined" ? String(loginuser || "") : ""'
        )
        return str(user or "").strip()
    except Exception:
        return ""


async def open_installer_quickstart_configuration(
    gui_page,
    host: str,
    installer_creds: dict,
) -> None:
    """Installer login lands on Quick Start → Configuration."""
    ok = await sanity_gui_login_and_verify(
        gui_page, host, installer_creds, label="installer"
    )
    check.is_true(ok, "installer GUI login failed")

    url = (gui_page.url or "").lower()
    if "/admin/config/configuration" not in url:
        stok = ""
        m = re.search(r";stok=[A-Za-z0-9]+", gui_page.url or "")
        if m:
            stok = m.group(0)
        cfg_url = f"{format_luci_url(host).rstrip('/')}/{stok}/admin/config/configuration"
        await gui_page.goto(cfg_url, timeout=UITimeouts.PAGE_LOAD_MS, wait_until="domcontentloaded")
        await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    heading = gui_page.locator("text=QUICK START").first
    await heading.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)


async def assert_installer_quickstart_tabs(
    gui_page,
    *,
    case_id: str,
    device_label: str,
) -> None:
    """Installer Quick Start shows Configuration (not full root System/Location/Radio tabs)."""
    tabs = [
        t.strip()
        for t in await gui_page.locator("ul.cbi-tabmenu li a").all_inner_texts()
        if t.strip()
    ]
    for required in ("Configuration", "Link Statistics", "Site Survey"):
        check.is_true(
            required in tabs,
            f"{case_id} [{device_label}]: Quick Start tab {required!r} missing (tabs={tabs})",
        )
    for hidden in ("System", "Location", "Radio 1", "2.4 GHz Radio"):
        check.is_false(
            hidden in tabs,
            f"{case_id} [{device_label}]: installer should not see Quick Start tab {hidden!r}",
        )


async def _field_state(gui_page, selector: str) -> tuple[int, bool, bool]:
    loc = gui_page.locator(selector).first
    count = await loc.count()
    if not count:
        return 0, False, False
    visible = await loc.is_visible()
    enabled = await loc.is_enabled() if visible else False
    return count, visible, enabled


async def assert_bts_installer_quickstart_fields(
    gui_page,
    *,
    case_id: str,
    device_label: str,
) -> None:
    """BTS installer Configuration exposes Cascaded BTS, static IP, MVLAN, QinQ, NMS."""
    body = (await gui_page.locator("body").inner_text()).lower()
    check.is_true("radio mode" in body and "bts" in body, f"{case_id} [{device_label}]: BTS radio mode not shown")

    visible_fields = (
        ("#casbts, input[name*='casbts']", "Cascaded BTS"),
        ("input[name='network.lan.ipaddr']", "IPv4 IP Address"),
        ("input[name='network.lan.netmask']", "IPv4 Subnet Mask"),
        ("input[name='network.lan.gateway']", "IPv4 Gateway"),
        ("input[name='network.lan.ip6addr']", "IPv6 IP Address"),
        ("input[name='network.lan.ip6gw']", "IPv6 Gateway"),
        ("input[name*='mgmtvlan']", "Mgmt VLAN ID"),
        ("#trap-list", "NMS Servers table"),
    )
    for selector, label in visible_fields:
        count, visible, enabled = await _field_state(gui_page, selector)
        check.is_true(count > 0, f"{case_id} [{device_label}]: {label} control missing")
        check.is_true(visible, f"{case_id} [{device_label}]: {label} not visible")
        if "nms" not in label.lower():
            check.is_true(enabled, f"{case_id} [{device_label}]: {label} not editable")

    qinq_fields = (
        ("select[name*='svlanethertype']", "Outer VLAN EtherType"),
        ("input[name*='svlanprio']", "Outer VLAN Priority"),
        ("input[name*='svlan']", "Outer VLAN ID"),
        ("select[name*='cvlanethertype']", "Inner VLAN EtherType"),
        ("input[name*='cvlanprio']", "Inner VLAN Priority"),
        ("input[name*='cvlanid']", "Inner VLAN ID"),
    )
    for selector, label in qinq_fields:
        count, _, _ = await _field_state(gui_page, selector)
        check.is_true(count > 0, f"{case_id} [{device_label}]: {label} control missing in page")

    nms_ips = gui_page.locator("#trap-list input[name*='nmsserver'][name$='.ip']")
    nms_count = await nms_ips.count()
    check.is_true(
        nms_count >= 2,
        f"{case_id} [{device_label}]: expected NMS 1 & NMS 2 rows, found {nms_count}",
    )

    save = gui_page.locator("input.cbi-button[value='Save']").first
    check.is_true(await save.count() > 0, f"{case_id} [{device_label}]: Save button missing")
    check.is_true(await save.is_visible(), f"{case_id} [{device_label}]: Save button not visible")
    check.is_true(await save.is_enabled(), f"{case_id} [{device_label}]: Save button not enabled")


async def assert_cpe_installer_quickstart_automatic(
    gui_page,
    *,
    case_id: str,
    device_label: str,
) -> None:
    """CPE installer Quick Start has no BTS VLAN/QinQ/Cascaded options (automatic CPE path)."""
    body = (await gui_page.locator("body").inner_text()).lower()
    check.is_true("radio mode" in body and "cpe" in body, f"{case_id} [{device_label}]: CPE radio mode not shown")

    for label in ("cascaded bts", "mgmt vlan id", "outer vlan", "inner vlan", "q-in-q"):
        check.is_false(
            label in body,
            f"{case_id} [{device_label}]: BTS-only Quick Start option {label!r} should not appear on CPE",
        )

    _, cas_visible, _ = await _field_state(gui_page, "#casbts, input[name*='casbts']")
    check.is_false(cas_visible, f"{case_id} [{device_label}]: Cascaded BTS must not be visible on CPE")

    mgmt_count, mgmt_visible, _ = await _field_state(gui_page, "input[name*='mgmtvlan']")
    check.is_true(
        mgmt_count == 0 or not mgmt_visible,
        f"{case_id} [{device_label}]: Mgmt VLAN must not be configurable on CPE",
    )

    for selector in (
        "select[name*='svlanethertype']",
        "input[name*='svlan']",
        "select[name*='cvlanethertype']",
        "input[name*='cvlanid']",
    ):
        count, visible, _ = await _field_state(gui_page, selector)
        check.is_true(
            count == 0 or not visible,
            f"{case_id} [{device_label}]: QinQ field {selector} must not be visible on CPE",
        )

    check.is_true("wireless" in body, f"{case_id} [{device_label}]: CPE WIRELESS section not shown")


async def verify_installer_quickstart_on_device(
    gui_page,
    host: str,
    root_creds: dict,
    installer_creds: dict,
    *,
    device_label: str,
    case_id: str,
    is_ap: bool,
) -> bool:
    """Installer Quick Start verification for BTS (AP) or CPE (STA). Restores root session."""
    await logout_gui(gui_page)
    await open_installer_quickstart_configuration(gui_page, host, installer_creds)

    user = await _loginuser(gui_page)
    check.equal(user, "installer", f"{case_id} [{device_label}]: expected installer session, got {user!r}")

    url = (gui_page.url or "").lower()
    check.is_true(
        "/admin/config" in url,
        f"{case_id} [{device_label}]: installer should land on Quick Start, got {url!r}",
    )

    await assert_installer_quickstart_tabs(gui_page, case_id=case_id, device_label=device_label)

    if is_ap:
        await assert_bts_installer_quickstart_fields(
            gui_page, case_id=case_id, device_label=device_label
        )
    else:
        await assert_cpe_installer_quickstart_automatic(
            gui_page, case_id=case_id, device_label=device_label
        )

    await logout_gui(gui_page)
    await sanity_login_if_needed(gui_page, host, root_creds)
    restored = await verify_sanity_gui_authenticated(gui_page)
    check.is_true(restored, f"{case_id} [{device_label}]: failed to restore root GUI session")
    return restored
