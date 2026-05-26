"""Shared Save → Apply → Confirm flow used by Management and Network GUI tests."""

import asyncio

from pages.locators import UITimeouts
from utils.recovery_manager import get_active_recovery_manager


def _resolve_locator(gui_page, locator):
    """Accept a Playwright Locator or a CSS/XPath selector string."""
    if hasattr(locator, "first"):
        return locator
    return gui_page.locator(locator)


async def apply_triple(
    gui_page,
    save_locator,
    apply_icon_locator,
    confirm_apply_locator,
    settle_seconds=15,
    *,
    only_if_apply_visible=False,
    between_step_ms=None,
):
    """
    Clicks Save, then top Apply, then confirm Apply, then waits for the device to settle.

    Args:
        only_if_apply_visible: If True, skip the apply/confirm clicks when the apply icon
            never appears (Network DHCP pages sometimes commit without a pending-apply UI).
        between_step_ms: Pause after each click; defaults to UITimeouts.MEDIUM_WAIT_MS.
    """
    gap = between_step_ms if between_step_ms is not None else UITimeouts.MEDIUM_WAIT_MS
    save = _resolve_locator(gui_page, save_locator)
    apply_icon = _resolve_locator(gui_page, apply_icon_locator)
    confirm_apply = _resolve_locator(gui_page, confirm_apply_locator)

    await save.first.scroll_into_view_if_needed()
    await save.first.click()
    await gui_page.wait_for_timeout(gap)
    if only_if_apply_visible:
        if not await apply_icon.first.is_visible(timeout=5000):
            return
    await apply_icon.first.click()
    await gui_page.wait_for_timeout(gap)
    if await confirm_apply.first.is_visible(timeout=5000):
        await confirm_apply.first.click()
    await asyncio.sleep(settle_seconds)
    manager = get_active_recovery_manager()
    if manager and not await manager.is_gui_reachable():
        await manager.run_soft_recovery()
