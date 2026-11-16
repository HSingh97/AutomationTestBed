"""CPE LuCI login helpers (e.g. GUI_84 BTS hyperlink opens CPE in a new tab)."""

from __future__ import annotations

import asyncio
import subprocess

from pages.locators import CommonLocators, LoginPageLocators, SummaryLocators, SummaryNetworkLocators, UITimeouts
from utils.net_utils import format_luci_url, ip_in_text, is_ipv6_literal, normalize_ip

async def _safe_visible(page, locator: str, timeout: int = UITimeouts.SHORT_WAIT_MS) -> bool:
    try:
        return await page.locator(locator).first.is_visible(timeout=timeout)
    except Exception:
        return False

async def _safe_count(page, locator: str) -> int:
    try:
        return await page.locator(locator).count()
    except Exception:
        return -1

async def _safe_body_sample(page, limit: int = 200) -> str:
    try:
        body_text = (await page.locator("body").inner_text()).strip()
        return " ".join(body_text.split())[:limit]
    except Exception:
        return ""

async def _wait_for_any_visible(page, locators: tuple[str, ...], timeout_ms: int) -> bool:
    tasks = [asyncio.create_task(page.locator(locator).first.wait_for(state="visible", timeout=timeout_ms)) for locator in locators]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        return any(task.exception() is None for task in done)
    except Exception:
        return False

async def _is_luci_page(page) -> bool:
    """True when the loaded page is LuCI (not a stray Apache/404 on port 80)."""
    url = page.url or ""
    if "/cgi-bin/luci" not in url:
        return False
    sample = (await _safe_body_sample(page, 400)).lower()
    if "not found" in sample and "apache" in sample:
        return False
    return await _wait_for_any_visible(
        page,
        (LoginPageLocators.USERNAME_INPUT, SummaryLocators.MODEL, CommonLocators.MENU_MONITOR),
        UITimeouts.SHORT_WAIT_MS,
    )

async def goto_cpe_luci(cpe_page, cpe_ip: str) -> None:
    """Open CPE LuCI; tolerate slow CPE UI and partial page loads."""
    schemes = ("https", "http") if is_ipv6_literal(cpe_ip) else ("https", "http")
    last_error = None
    timeouts = (UITimeouts.PAGE_LOAD_MS, UITimeouts.PAGE_LOAD_MS * 2)
    for timeout_ms in timeouts:
        for scheme in schemes:
            try:
                await cpe_page.goto(
                    format_luci_url(cpe_ip, scheme=scheme),
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                if await _is_luci_page(cpe_page):
                    return
                last_error = RuntimeError(f"{scheme} returned non-LuCI content at {cpe_page.url or ''}")
            except Exception as exc:
                last_error = exc
                try:
                    if await _is_luci_page(cpe_page):
                        return
                except Exception:
                    pass
    raise RuntimeError(f"Unable to open CPE LuCI for {cpe_ip}: {last_error}")

async def ensure_cpe_logged_in(
    cpe_page,
    cpe_ip: str,
    device_creds: dict,
    *,
    require_summary: bool = True,
) -> None:
    """Log in on CPE when needed; optionally accept any authenticated LuCI page."""
    await cpe_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    model_visible = await cpe_page.locator(SummaryLocators.MODEL).is_visible(
        timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    login_visible = await cpe_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible(
        timeout=UITimeouts.SHORT_WAIT_MS
    )
    menu_visible = await _safe_visible(cpe_page, CommonLocators.MENU_MONITOR)
    if not any((model_visible, login_visible, menu_visible)):
        await _wait_for_any_visible(
            cpe_page,
            (LoginPageLocators.USERNAME_INPUT, SummaryLocators.MODEL, CommonLocators.MENU_MONITOR),
            UITimeouts.PAGE_LOAD_MS,
        )
        model_visible = await _safe_visible(cpe_page, SummaryLocators.MODEL, UITimeouts.SHORT_WAIT_MS)
        login_visible = await _safe_visible(cpe_page, LoginPageLocators.USERNAME_INPUT, UITimeouts.SHORT_WAIT_MS)
        menu_visible = await _safe_visible(cpe_page, CommonLocators.MENU_MONITOR, UITimeouts.SHORT_WAIT_MS)
    if model_visible:
        return

    if login_visible:
        await cpe_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await cpe_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await cpe_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await cpe_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    elif "/cgi-bin/luci" not in (cpe_page.url or ""):
        await goto_cpe_luci(cpe_page, cpe_ip)
        await cpe_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        if await cpe_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible(
            timeout=UITimeouts.SHORT_WAIT_MS
        ):
            await cpe_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
            await cpe_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
            await cpe_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
            await cpe_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    if require_summary:
        await cpe_page.locator(SummaryLocators.MODEL).wait_for(
            state="visible",
            timeout=UITimeouts.PAGE_LOAD_MS * 2,
        )
        return

    for locator in (SummaryLocators.MODEL, CommonLocators.MENU_MONITOR):
        try:
            await cpe_page.locator(locator).wait_for(
                state="visible",
                timeout=UITimeouts.PAGE_LOAD_MS,
            )
            return
        except Exception:
            continue

    raise RuntimeError(f"CPE {cpe_ip}: authenticated LuCI page did not become ready")

def is_cpe_host_reachable(cpe_ip: str, *, timeout_s: int = 2) -> bool:
    """Return True when the test runner can reach the peer management address."""
    host = normalize_ip(cpe_ip)
    if not host:
        return False
    if is_ipv6_literal(host):
        cmd = ["ping6", "-c", "1", "-W", str(max(1, timeout_s)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_s)), host]
    try:
        return subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    except FileNotFoundError:
        return False

async def open_cpe_gui_session(context, cpe_ip: str, device_creds: dict, *, require_summary: bool = False):
    """Return a logged-in Playwright page for the CPE."""
    page = await context.new_page()
    await goto_cpe_luci(page, cpe_ip)
    await ensure_cpe_logged_in(page, cpe_ip, device_creds, require_summary=require_summary)
    return page

async def open_cpe_gui_session_if_reachable(
    context,
    cpe_ip: str,
    device_creds: dict,
    *,
    require_summary: bool = False,
):
    """
    Open a CPE GUI session when the peer is reachable from the test host.
    Returns None when the management path is down (common on IPv6 lab setups).
    """
    if not is_cpe_host_reachable(cpe_ip):
        print(f"[CPE_SESSION] {cpe_ip} not reachable from test host; skipping CPE GUI login")
        return None
    try:
        return await open_cpe_gui_session(
            context,
            cpe_ip,
            device_creds,
            require_summary=require_summary,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "UNREACHABLE" in message or "ERR_ADDRESS_UNREACHABLE" in message:
            print(f"[CPE_SESSION] {cpe_ip} GUI unreachable ({message}); skipping CPE-side validation")
            return None
        raise

async def read_summary_network_ips(cpe_page) -> str:
    parts: list[str] = []
    for locator in (SummaryNetworkLocators.IP_ADDRESS, SummaryNetworkLocators.IPV6_ADDRESS):
        if await cpe_page.locator(locator).count():
            parts.append((await cpe_page.locator(locator).inner_text()).strip())
    return " ".join(p for p in parts if p).strip()

async def summary_has_peer_ip(cpe_page, peer_ip: str) -> bool:
    return bool(peer_ip) and ip_in_text(peer_ip, await read_summary_network_ips(cpe_page))
