"""SANITY_111 — Installer login lands on Dashboard with full Summary sections."""

from __future__ import annotations

import re

import pytest_check as check

from config.defaults import SANITY_TEST_VALUES
from pages.locators import LoginPageLocators, SummaryLocators, TopPanelLocators, UITimeouts
from utils.sanity_gui import sanity_gui_login_and_verify, sanity_login_if_needed, verify_sanity_gui_authenticated
from utils.sanity_summary import scrape_summary_overview_gui
from utils.sanity_temperature import open_sanity_summary_system


def installer_gui_creds(profile: dict, device_creds: dict) -> dict[str, str]:
    """Resolve installer GUI credentials from profile or sane defaults."""
    fl = profile.get("factory_login") or {}
    values = SANITY_TEST_VALUES
    user = str(fl.get("username") or values.get("installer_username") or "installer").strip()
    password = str(
        fl.get("password")
        or values.get("installer_password")
        or "senao123"
    ).strip()
    if not password:
        password = str(device_creds.get("pass", "")).strip()
    return {"user": user, "pass": password}


async def logout_gui(gui_page) -> None:
    """Logout from LuCI and wait for the login form."""
    logout_btn = gui_page.locator(TopPanelLocators.LOGOUT_BUTTON).first
    if await logout_btn.is_visible(timeout=3000):
        await logout_btn.click()
    else:
        current = gui_page.url or ""
        if "/cgi-bin/luci" in current:
            base = current.split("/cgi-bin/luci", 1)[0]
            await gui_page.goto(
                f"{base}/cgi-bin/luci/logout",
                timeout=UITimeouts.PAGE_LOAD_MS,
                wait_until="domcontentloaded",
            )
    login = gui_page.locator(LoginPageLocators.USERNAME_INPUT)
    await login.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)


def _populated(value: str) -> bool:
    clean = str(value or "").strip()
    return bool(clean) and clean not in {"-", "—", "N/A", "n/a", "null", "unknown"}


def _dashboard_url_ok(url: str) -> bool:
    lower = (url or "").lower()
    return any(
        token in lower
        for token in ("/admin/home", "/admin/status/overview", "/admin/status", ";stok=")
    )


async def assert_landed_on_dashboard(gui_page, *, case_id: str, device_label: str) -> None:
    """Installer login should route to the Dashboard / Summary page."""
    url = gui_page.url or ""
    check.is_true(
        _dashboard_url_ok(url),
        f"{case_id} [{device_label}]: expected Dashboard URL after installer login, got {url!r}",
    )
    model = gui_page.locator(SummaryLocators.MODEL).first
    try:
        await model.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        await open_sanity_summary_system(gui_page)
        await model.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)


async def assert_dashboard_section_headings(
    gui_page,
    *,
    case_id: str,
    device_label: str,
) -> None:
    """Summary/System, Network, Performance, and Wireless blocks are visible."""
    patterns = (
        ("Summary", r"summary|system"),
        ("Network", r"network"),
        ("Performance", r"performance"),
        ("Wireless", r"wireless"),
    )
    body = (await gui_page.locator("body").inner_text()).lower()
    for label, pattern in patterns:
        heading = gui_page.locator(
            f"h1:has-text('{label}'), h2:has-text('{label}'), h3:has-text('{label}'), "
            f"legend:has-text('{label}'), .cbi-section legend:has-text('{label}')"
        ).first
        visible = False
        try:
            visible = await heading.is_visible(timeout=2000)
        except Exception:
            visible = False
        if not visible and label == "Summary":
            visible = bool(re.search(pattern, body))
        elif not visible:
            visible = bool(re.search(pattern, body))
        check.is_true(
            visible,
            f"{case_id} [{device_label}]: Dashboard section {label!r} not visible",
        )


def assert_installer_dashboard_matches_root(
    root_fields: dict[str, str],
    installer_fields: dict[str, str],
    *,
    case_id: str,
    device_label: str,
) -> None:
    """Installer sees the same populated Overview fields as root."""
    missing: list[str] = []
    for key, root_val in root_fields.items():
        if not _populated(root_val):
            continue
        inst_val = installer_fields.get(key, "")
        if not _populated(inst_val):
            missing.append(key)
    check.is_true(
        not missing,
        f"{case_id} [{device_label}]: installer Dashboard missing populated fields "
        f"visible to root: {', '.join(missing)}",
    )


async def verify_installer_dashboard_on_device(
    gui_page,
    host: str,
    root_creds: dict,
    installer_creds: dict,
    *,
    device_label: str,
    case_id: str,
) -> bool:
    """
    Root baseline on Dashboard → logout → installer login → same sections visible.
    Restores root session before returning.
    """
    root_ok = await sanity_gui_login_and_verify(
        gui_page, host, root_creds, label=f"{device_label} (root)"
    )
    check.is_true(root_ok, f"{case_id} [{device_label}]: root GUI login failed")

    await open_sanity_summary_system(gui_page, host=host, device_creds=root_creds)
    await gui_page.wait_for_timeout(UITimeouts.LONG_WAIT_MS)
    root_fields = await scrape_summary_overview_gui(gui_page)
    await assert_dashboard_section_headings(
        gui_page, case_id=case_id, device_label=f"{device_label} (root)"
    )

    await logout_gui(gui_page)

    installer_ok = await sanity_gui_login_and_verify(
        gui_page, host, installer_creds, label=f"{device_label} (installer)"
    )
    check.is_true(installer_ok, f"{case_id} [{device_label}]: installer GUI login failed")
    await assert_landed_on_dashboard(gui_page, case_id=case_id, device_label=device_label)
    await assert_dashboard_section_headings(gui_page, case_id=case_id, device_label=device_label)

    await open_sanity_summary_system(gui_page, host=host, device_creds=installer_creds)
    await gui_page.wait_for_timeout(UITimeouts.LONG_WAIT_MS)
    installer_fields = await scrape_summary_overview_gui(gui_page)
    assert_installer_dashboard_matches_root(
        root_fields,
        installer_fields,
        case_id=case_id,
        device_label=device_label,
    )

    await logout_gui(gui_page)
    await sanity_login_if_needed(gui_page, host, root_creds)
    restored = await verify_sanity_gui_authenticated(gui_page)
    check.is_true(
        restored,
        f"{case_id} [{device_label}]: failed to restore root GUI session",
    )
    return installer_ok and restored
