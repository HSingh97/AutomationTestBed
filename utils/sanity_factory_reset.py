"""Sanity factory-reset helpers — Management → Upgrade/Reset → Reset tab."""

from __future__ import annotations

import asyncio

from pages.locators import UITimeouts
from utils.net_utils import format_luci_url
from utils.sanity_gui import resolve_luci_stok, sanity_login_if_needed


async def _reset_page_url(gui_page, host: str = "") -> str:
    stok = await resolve_luci_stok(gui_page)
    head = ""
    url = gui_page.url or ""
    if "/cgi-bin/luci" in url:
        head = url.split("/cgi-bin/luci", 1)[0] + "/cgi-bin/luci"
    elif host:
        head = format_luci_url(host).rstrip("/")
    if not head:
        return ""
    # resolve_luci_stok returns ";stok=TOKEN" (with prefix).
    if stok:
        return f"{head}/{stok}/admin/system/flashops/reset"
    return f"{head}/admin/system/flashops/reset"


async def _on_reset_page(gui_page) -> bool:
    url_l = (gui_page.url or "").lower()
    if "flashops" in url_l and "reset" in url_l:
        pass
    for sel in (
        "#reset input[type='button']",
        "input[value='Perform Reset']",
        "#retip",
        "#cont",
        "h4:has-text('Reset Parameters')",
        "p:has-text('Factory reset')",
    ):
        try:
            if await gui_page.locator(sel).first.is_visible(timeout=1500):
                return True
        except Exception:
            continue
    return False


async def navigate_to_factory_reset_tab(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Open Management → Upgrade/Reset → Reset tab (direct LuCI reset URL preferred)."""
    if host and device_creds:
        await sanity_login_if_needed(gui_page, host, device_creds)

    reset_url = await _reset_page_url(gui_page, host=host)
    if reset_url:
        print(f"[sanity] Factory Reset: goto {reset_url[:120]}", flush=True)
        await gui_page.goto(
            reset_url,
            timeout=max(UITimeouts.PAGE_LOAD_MS * 2, 30000),
            wait_until="domcontentloaded",
        )
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        if host and device_creds and not await _on_reset_page(gui_page):
            await sanity_login_if_needed(gui_page, host, device_creds)
            reset_url = await _reset_page_url(gui_page, host=host)
            if reset_url:
                await gui_page.goto(
                    reset_url,
                    timeout=max(UITimeouts.PAGE_LOAD_MS * 2, 30000),
                    wait_until="domcontentloaded",
                )
                await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    if not await _on_reset_page(gui_page):
        # Fallback: Management menu → flashops → Reset tab
        mgmt = gui_page.locator("li.Management > a.menu, #menu_management").first
        try:
            if await mgmt.is_visible(timeout=2000):
                await mgmt.click()
                await gui_page.wait_for_timeout(400)
        except Exception:
            pass
        flashops = gui_page.locator('xpath=//*[@id="Management"]/li[3]/a').first
        if not await flashops.is_visible(timeout=2000):
            flashops = gui_page.locator(
                "ul.dropdown-menu a[href*='/system/flashops'], a[href*='/system/flashops']"
            ).first
        await flashops.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await flashops.click()
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        reset_tab = gui_page.locator(
            'xpath=//*[@id="maincontent"]/div/div/ul/li[2]/a, '
            "a[href*='/flashops/reset']"
        ).first
        await reset_tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await reset_tab.click()
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    perform = gui_page.locator(
        "#reset input[type='button'], "
        "input[value='Perform Reset']"
    ).first
    await perform.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)


async def configure_factory_reset_keep_settings(gui_page) -> int:
    """
    Uncheck System / Network / Wireless wipe boxes so LuCI retains settings.

    On Senao reset.htm, checked boxes wipe the subsystem; unchecked = keep.
    Returns number of boxes unchecked.
    """
    toggled = 0
    for cid in ("3", "2", "1", "0"):
        cb = gui_page.locator(f"input#{cid}[type='checkbox']").first
        try:
            if await cb.count() == 0:
                continue
            if not await cb.is_visible(timeout=500):
                continue
            if await cb.is_checked():
                await cb.uncheck()
                toggled += 1
        except Exception:
            continue

    if toggled == 0:
        checkboxes = gui_page.locator(
            "#retip input[type='checkbox'], "
            "#retltype input[type='checkbox'], "
            "#retwireless input[type='checkbox'], "
            "input.cibi-input-select"
        )
        total = await checkboxes.count()
        for i in range(total):
            cb = checkboxes.nth(i)
            try:
                if not await cb.is_visible(timeout=300):
                    continue
                if await cb.is_checked():
                    await cb.uncheck()
                    toggled += 1
            except Exception:
                continue

    # Always force keep-all retain bits (0111 = System+Network+Radio1).
    await gui_page.evaluate(
        """() => {
            if (typeof ret_bin !== 'undefined') {
                ret_bin = '0111';
            }
            for (const id of ['0','1','2','3']) {
                const el = document.getElementById(id);
                if (el) el.checked = false;
            }
        }"""
    )
    await gui_page.wait_for_timeout(300)
    return toggled


async def trigger_factory_reset_perform(gui_page, *, settle_after_click_s: float = 5.0) -> None:
    """Click Perform Reset and accept the confirm dialog."""

    async def _accept_dialog(dialog) -> None:
        await dialog.accept()

    gui_page.once("dialog", _accept_dialog)
    reset_btn = gui_page.locator('xpath=//*[@id="reset"]/input').first
    if not await reset_btn.is_visible(timeout=3000):
        reset_btn = gui_page.locator(
            "input[value='Perform Reset']:visible, "
            "input[value*='Reset' i]:visible, "
            "button:has-text('Perform Reset'):visible"
        ).first
    await reset_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await reset_btn.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    if settle_after_click_s > 0:
        await asyncio.sleep(settle_after_click_s)


async def gui_factory_reset_keep_settings(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
    settle_after_click_s: float = 5.0,
) -> None:
    """
    GUI path for Case 77 keep-settings factory reset:
    Management → Upgrade/Reset → Reset → keep settings (uncheck wipe) → Perform Reset → OK.
    """
    await navigate_to_factory_reset_tab(
        gui_page, host=host, device_creds=device_creds
    )
    await configure_factory_reset_keep_settings(gui_page)
    await trigger_factory_reset_perform(
        gui_page, settle_after_click_s=settle_after_click_s
    )
