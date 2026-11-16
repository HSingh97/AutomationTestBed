"""Sanity soft/hard reboot helpers — GUI Reboot top panel and PDU PoE cycle."""

from __future__ import annotations

import asyncio

import pytest_check as check

from pages.locators import TopPanelLocators, UITimeouts
from utils.pdu_power import pdu_enabled, pdu_hard_reboot
from utils.sanity_gui import sanity_gui_login_and_verify
from utils.sanity_ssh import wait_sanity_reboot_cycle, wait_sanity_ssh


async def trigger_gui_soft_reboot(gui_page) -> None:
    """Top panel Reboot → Perform reboot (LuCI admin/reboot?reboot=1)."""
    reboot_link = gui_page.locator(TopPanelLocators.REBOOT_BUTTON).first
    await reboot_link.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await reboot_link.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    perform = gui_page.locator("a.btn[href*='reboot=1']").first
    try:
        await perform.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        perform = gui_page.get_by_role("link", name="Perform reboot").first
        await perform.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await perform.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def wait_device_after_soft_reboot(
    host: str,
    password: str,
    *,
    boot_id_before: str,
    source_v6: str,
    label: str,
    reboot_timeout_s: int,
    poll_s: int,
    settle_s: int,
    alt_hosts: list[str] | None = None,
):
    """Wait for GUI-triggered reboot to complete; return fresh SSH session."""
    return await wait_sanity_reboot_cycle(
        host,
        password,
        boot_id_before=boot_id_before,
        source_v6=source_v6,
        label=label,
        recovery_timeout_s=reboot_timeout_s,
        poll_s=poll_s,
        settle_s=settle_s,
        alt_hosts=alt_hosts,
    )


async def wait_device_after_hard_reboot(
    host: str,
    password: str,
    *,
    source_v6: str,
    label: str,
    reboot_timeout_s: int,
    poll_s: int,
    settle_s: int,
):
    """Wait for SSH after PDU power cycle."""
    if settle_s > 0:
        await asyncio.sleep(settle_s)
    return await wait_sanity_ssh(
        host,
        password,
        source_v6=source_v6,
        label=f"{label} post-PDU",
        timeout_s=reboot_timeout_s,
        poll_s=poll_s,
    )


async def assert_gui_login_after_reboot(
    gui_page,
    host: str,
    device_creds: dict,
    *,
    device_label: str,
    case_id: str,
    alt_hosts: list[str] | None = None,
    soft: bool = False,
) -> bool:
    """Prefer HTTP lab IPv4 after reboot — IPv6 LuCI often redirects to HTTPS and hangs."""
    from utils.net_utils import is_ipv6_literal

    candidates: list[str] = []
    # Prefer explicit same-device alt IPv4, then primary host. Never try the peer's IP.
    for h in list(alt_hosts or []) + [host]:
        h = str(h or "").strip()
        if h and h not in candidates:
            candidates.append(h)
    if host and not is_ipv6_literal(host):
        candidates = [host] + [c for c in candidates if c != host]

    ok = False
    last_host = host
    for try_host in candidates:
        last_host = try_host
        ok = await sanity_gui_login_and_verify(
            gui_page,
            try_host,
            device_creds,
            label=device_label,
        )
        if ok:
            break
    if soft and not ok:
        print(
            f"[SANITY] {case_id} [{device_label}]: GUI login soft-skip after reboot "
            f"at {last_host}",
            flush=True,
        )
        return False
    check.is_true(
        ok,
        f"{case_id} [{device_label}]: GUI login failed after reboot at {last_host}",
    )
    return ok


async def pdu_hard_reboot_device(
    profile: dict,
    *,
    device_target: str,
    case_id: str,
    label: str,
) -> None:
    check.is_true(
        pdu_enabled(profile),
        f"{case_id} [{label}]: PDU must be configured for hard reboot (PoE power cycle)",
    )
    await pdu_hard_reboot(profile, device_target=device_target)
