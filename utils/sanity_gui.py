"""Sanity-only GUI helpers — login, flashops navigation, keep-settings upgrade."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pages.locators import CommonLocators, LoginPageLocators, UITimeouts

from utils.net_utils import format_http_host, format_luci_url, is_ipv6_literal

_DEBUG_LOG = Path(
    "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-5d791c.log"
)


def _dbg(hypothesis_id: str, location: str, message: str, data: dict | None = None) -> None:
    # region agent log
    try:
        entry = {
            "sessionId": "5d791c",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass
    # endregion


async def sanity_login_if_needed(gui_page, host: str, device_creds: dict, *, wait_ms: int = 4000) -> None:
    """Log into LuCI when session expired / page is on chrome-error / wrong host."""
    target = format_http_host(host)
    # Prefer the page's current scheme when already on LuCI; otherwise HTTP.
    # CPE Chromium hangs on HTTPS; lab uhttpd is tuned for HTTP.
    current = (gui_page.url or "")
    if current.lower().startswith("https://"):
        url = f"http://{target}/cgi-bin/luci/"
    elif current.lower().startswith("http://"):
        url = f"http://{target}/cgi-bin/luci/"
    else:
        url = format_luci_url(host, scheme="http")
    current_l = current.lower()
    host_token = target.lower().strip("[]")
    need_goto = (
        not current
        or "chrome-error" in current_l
        or "chromewebdata" in current_l
        or "/cgi-bin/luci" not in current_l
        or host_token not in current_l.replace("[", "").replace("]", "")
    )
    if need_goto:
        await gui_page.goto(
            url,
            timeout=max(UITimeouts.PAGE_LOAD_MS * 3, 45000),
            wait_until="commit",
        )

    login = gui_page.locator(LoginPageLocators.USERNAME_INPUT)
    if await login.is_visible(timeout=UITimeouts.MEDIUM_WAIT_MS):
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        try:
            async with gui_page.expect_navigation(
                timeout=max(wait_ms, 20000), wait_until="domcontentloaded"
            ):
                await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        except Exception:
            await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
            await gui_page.wait_for_timeout(wait_ms)
        # LuCI redirects to /cgi-bin/luci/;stok=TOKEN — wait for it.
        try:
            await gui_page.wait_for_url("**/;stok=**", timeout=max(wait_ms, 15000))
        except Exception:
            await gui_page.wait_for_timeout(min(wait_ms, 3000))
    # Authenticated but URL still bare /cgi-bin/luci/ — hit root again to pick up stok.
    if ";stok=" not in (gui_page.url or "") and host:
        try:
            login2 = gui_page.locator(LoginPageLocators.USERNAME_INPUT)
            if not await login2.is_visible(timeout=1500):
                await gui_page.goto(
                    url,
                    timeout=max(UITimeouts.PAGE_LOAD_MS * 2, 30000),
                    wait_until="domcontentloaded",
                )
                await gui_page.wait_for_timeout(800)
                if await login2.is_visible(timeout=1500):
                    await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                    await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                    try:
                        async with gui_page.expect_navigation(
                            timeout=20000, wait_until="domcontentloaded"
                        ):
                            await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
                    except Exception:
                        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
                        await gui_page.wait_for_timeout(wait_ms)
                try:
                    await gui_page.wait_for_url("**/;stok=**", timeout=15000)
                except Exception:
                    pass
        except Exception:
            pass


async def verify_sanity_gui_authenticated(gui_page, *, timeout_ms: int = 20000) -> bool:
    """True when LuCI main menu is visible and the login form is not."""
    login = gui_page.locator(LoginPageLocators.USERNAME_INPUT)
    if await login.is_visible(timeout=min(3000, timeout_ms)):
        return False
    for selector in (
        CommonLocators.MENU_MONITOR,
        "li.Management > a.menu",
        "li.Monitor > a.menu",
        "#maincontent",
    ):
        loc = gui_page.locator(selector).first
        try:
            if await loc.is_visible(timeout=timeout_ms // 4):
                return True
        except Exception:
            continue
    return False


async def sanity_gui_login_and_verify(
    gui_page,
    host: str,
    device_creds: dict,
    *,
    label: str = "device",
) -> bool:
    """Navigate to host, log in if needed, and confirm authenticated LuCI."""
    # Prefer IPv4 HTTP for CPE — IPv6 HTTPS hangs after reboot (uhttpd redirect).
    hosts: list[str] = []
    if is_ipv6_literal(host):
        hosts.extend(["192.168.2.11", host])
    else:
        hosts.append(host)
    last_error = ""
    for try_host in hosts:
        for scheme in ("http", "https"):
            try:
                await gui_page.goto(
                    format_luci_url(try_host, scheme=scheme),
                    timeout=UITimeouts.PAGE_LOAD_MS * 2,
                    wait_until="domcontentloaded",
                )
                await sanity_login_if_needed(gui_page, try_host, device_creds)
                ok = await verify_sanity_gui_authenticated(gui_page)
                if ok:
                    print(f"[sanity] {label} GUI login verified at {try_host} ({scheme})", flush=True)
                    return True
            except Exception as exc:
                last_error = str(exc)
    print(f"[sanity] {label} GUI login failed at {host}: {last_error}", flush=True)
    return False


async def force_luci_session_with_stok(
    gui_page,
    host: str,
    device_creds: dict,
    *,
    label: str = "device",
    retries: int = 3,
) -> str:
    """
    Recover a clean LuCI session on ``host`` and return ``;stok=TOKEN``.

    Used after CPE→BTS handoff, power-cut, or chrome-error pages where the
    shared Playwright tab may still point at a dead peer or lack a stok.
    """
    last_error = ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            # Abort any hung navigation left from a peer that went offline.
            try:
                await gui_page.goto("about:blank", timeout=10000, wait_until="commit")
            except Exception:
                pass
            ok = await sanity_gui_login_and_verify(
                gui_page, host, device_creds, label=f"{label}-stok{attempt}"
            )
            stok = await resolve_luci_stok(gui_page)
            if ok and stok:
                print(
                    f"[sanity] {label}: LuCI stok ready on {host} (attempt {attempt})",
                    flush=True,
                )
                return stok
            # Authenticated but URL missing stok — force a root reload.
            if ok and not stok:
                await gui_page.goto(
                    format_luci_url(host).rstrip("/") + "/",
                    timeout=UITimeouts.PAGE_LOAD_MS * 2,
                    wait_until="domcontentloaded",
                )
                await gui_page.wait_for_timeout(1000)
                try:
                    await gui_page.wait_for_url("**/;stok=**", timeout=15000)
                except Exception:
                    pass
                stok = await resolve_luci_stok(gui_page)
                if stok:
                    return stok
            last_error = f"authenticated={ok} stok={stok!r} url={gui_page.url!r}"
        except Exception as exc:
            last_error = str(exc)
            print(
                f"[sanity] {label}: stok recovery attempt {attempt} failed: {exc}",
                flush=True,
            )
        await asyncio.sleep(2)
    raise RuntimeError(
        f"[sanity] {label}: could not obtain LuCI ;stok= on {host} after {retries} attempts "
        f"({last_error})"
    )

async def open_cpe_gui_page(
    context,
    cpe_ip: str,
    device_creds: dict,
    *,
    alt_ipv4: str | None = "192.168.2.11",
    required: bool = True,
):
    """Open a logged-in CPE LuCI tab (IPv4-first HTTP).

    Prefer an isolated Chromium context — the shared BTS session browser
    often stalls on CPE LuCI (redirect/CGI hang). When ``required=False``,
    return None instead of raising if LuCI is unreachable.
    """
    page = None
    isolated = None  # (playwright, browser, context) when we own the lifecycle

    async def _spawn_isolated():
        nonlocal page, isolated
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        isolated_ctx = await browser.new_context(ignore_https_errors=True)
        isolated = (pw, browser, isolated_ctx)
        page = await isolated_ctx.new_page()

    # Always prefer isolated browser for CPE — shared context hangs frequently.
    try:
        await _spawn_isolated()
    except Exception as exc:
        print(f"[sanity] CPE GUI isolated launch failed ({exc}); trying shared context", flush=True)
        try:
            page = await context.new_page()
        except Exception as exc2:
            if not required:
                print(f"[sanity] CPE GUI skipped (context): {exc2}", flush=True)
                return None
            raise

    # CPE LuCI may still advertise HTTPS; Chromium hangs on CPE TLS.
    # Abort HTTPS requests so the browser stays on HTTP (do not rewrite
    # scheme in-place — Playwright rejects protocol changes on continue_).
    async def _block_https(route):
        url = route.request.url or ""
        if url.startswith("https://"):
            await route.abort()
        else:
            await route.continue_()

    try:
        await page.route("**/*", _block_https)
    except Exception as exc:
        print(f"[sanity] CPE GUI HTTPS block route skipped: {exc}", flush=True)

    # CPE (UBR650) often hangs Chromium on HTTPS while HTTP LuCI answers.
    # Prefer HTTP; try IPv4 when IPv6 CGI is slow/hung.
    hosts: list[str] = []
    for h in (alt_ipv4, cpe_ip):
        if h and h not in hosts:
            hosts.append(h)
    schemes = ("http",)
    last_error = ""
    try:
        for host in hosts:
            for scheme in schemes:
                for wait_until in ("domcontentloaded", "commit"):
                    timeout_ms = 45000
                    for attempt in (1, 2):
                        try:
                            await page.goto(
                                format_luci_url(host, scheme=scheme),
                                timeout=timeout_ms,
                                wait_until=wait_until,
                            )
                            try:
                                await page.wait_for_selector(
                                    LoginPageLocators.USERNAME_INPUT,
                                    timeout=20000,
                                    state="visible",
                                )
                            except Exception:
                                pass
                            await sanity_login_if_needed(page, host, device_creds)
                            print(
                                f"[sanity] CPE GUI logged in at {host} ({scheme}/{wait_until})",
                                flush=True,
                            )
                            if isolated is not None:
                                page._sanity_isolated_playwright = isolated  # type: ignore[attr-defined]
                            return page
                        except Exception as exc:
                            last_error = str(exc)
                            print(
                                f"[sanity] CPE GUI {scheme}/{wait_until} open failed "
                                f"({host} try={attempt}): {exc}",
                                flush=True,
                            )
                            if attempt == 1 and "Timeout" in last_error:
                                await asyncio.sleep(2)
                                continue
                            break
                    # Recreate page after a hung navigation.
                    try:
                        await page.close()
                    except Exception:
                        pass
                    if isolated is not None:
                        try:
                            page = await isolated[2].new_page()
                            try:
                                await page.route("**/*", _block_https)
                            except Exception:
                                pass
                        except Exception:
                            try:
                                await _spawn_isolated()
                                try:
                                    await page.route("**/*", _block_https)
                                except Exception:
                                    pass
                            except Exception:
                                page = await context.new_page()
                                try:
                                    await page.route("**/*", _block_https)
                                except Exception:
                                    pass
                    else:
                        page = await context.new_page()
                        try:
                            await page.route("**/*", _block_https)
                        except Exception:
                            pass
        try:
            await page.close()
        except Exception:
            pass
        if isolated is not None:
            pw, browser, isolated_ctx = isolated
            try:
                await isolated_ctx.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass
        if not required:
            print(
                f"[sanity] CPE GUI unavailable at {cpe_ip} ({last_error}); continuing without it",
                flush=True,
            )
            return None
        raise RuntimeError(f"[sanity] Unable to open CPE GUI at {cpe_ip}: {last_error}")
    except Exception:
        if isolated is not None and page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if not required:
            print(
                f"[sanity] CPE GUI unavailable at {cpe_ip}; continuing without it",
                flush=True,
            )
            return None
        raise


async def close_cpe_gui_page(page) -> None:
    """Close CPE page and any isolated Playwright stack attached to it."""
    if page is None:
        return
    isolated = getattr(page, "_sanity_isolated_playwright", None)
    try:
        await page.close()
    except Exception:
        pass
    if not isolated:
        return
    pw, browser, isolated_ctx = isolated
    for closer in (
        getattr(isolated_ctx, "close", None),
        getattr(browser, "close", None),
        getattr(pw, "stop", None),
    ):
        if closer is None:
            continue
        try:
            await closer()
        except Exception:
            pass


def _luci_stok_from_url(url: str) -> str:
    """Return ``;stok=TOKEN`` segment from a LuCI URL, or empty."""
    import re as _re

    m = _re.search(r";stok=[A-Za-z0-9]+", url or "")
    return m.group(0) if m else ""


async def resolve_luci_stok(gui_page) -> str:
    """Return ``;stok=TOKEN`` from the page URL or any authenticated href."""
    stok = _luci_stok_from_url(gui_page.url or "")
    if stok:
        return stok
    try:
        href = await gui_page.evaluate(
            """() => {
              const a = document.querySelector('a[href*=\";stok=\"]');
              return a ? (a.href || a.getAttribute('href') || '') : '';
            }"""
        )
        stok = _luci_stok_from_url(str(href or ""))
        if stok:
            return stok
    except Exception:
        pass
    return ""


async def navigate_to_flashops(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    menu_timeout = max(UITimeouts.PAGE_LOAD_MS * 3, 45000)

    async def _ensure_auth() -> None:
        if not (host and device_creds):
            return
        stok = await resolve_luci_stok(gui_page)
        host_token = format_http_host(host).lower().strip("[]")
        url_l = (gui_page.url or "").lower().replace("[", "").replace("]", "")
        on_host = host_token in url_l and "/cgi-bin/luci" in url_l
        if stok and on_host:
            try:
                if await verify_sanity_gui_authenticated(gui_page, timeout_ms=8000):
                    return
            except Exception:
                pass
        await force_luci_session_with_stok(
            gui_page, host, device_creds, label="flashops", retries=3
        )

    async def _flashops_url() -> str:
        await _ensure_auth()
        stok = await resolve_luci_stok(gui_page)
        if not stok and host:
            # Land on LuCI root to obtain a fresh stok, then rebuild.
            await gui_page.goto(
                format_luci_url(host).rstrip("/") + "/",
                timeout=menu_timeout,
                wait_until="domcontentloaded",
            )
            await gui_page.wait_for_timeout(1200)
            await _ensure_auth()
            stok = await resolve_luci_stok(gui_page)
        head = ""
        if "/cgi-bin/luci" in (gui_page.url or ""):
            head = (gui_page.url or "").split("/cgi-bin/luci", 1)[0] + "/cgi-bin/luci"
        elif host:
            head = format_luci_url(host).rstrip("/")
        if not head:
            return ""
        if stok:
            return f"{head}/{stok}/admin/system/flashops"
        print("[sanity] Flashops: no LuCI stok after login — refusing bare URL", flush=True)
        return ""

    async def _on_flashops_ready() -> bool:
        url_l = (gui_page.url or "").lower()
        if "flashops" not in url_l:
            return False
        # Require an authenticated flash form marker — bare /flashops without stok is useless.
        if not await resolve_luci_stok(gui_page):
            return False
        for sel in (
            "#keep",
            "#image",
            "input[type='file'][name='image']",
            "input[type='file']",
            "a[href*='/flashops/flash']",
            "a:has-text('Upgrade')",
        ):
            try:
                if await gui_page.locator(sel).first.is_visible(timeout=2500):
                    return True
            except Exception:
                continue
            try:
                if await gui_page.locator(sel).first.count():
                    return True
            except Exception:
                continue
        return False

    async def _goto_flashops_direct() -> bool:
        flash_url = await _flashops_url()
        if not flash_url:
            return False
        print(f"[sanity] Flashops menu miss — direct goto {flash_url[:110]}", flush=True)
        try:
            await gui_page.goto(flash_url, timeout=menu_timeout, wait_until="domcontentloaded")
            await gui_page.wait_for_timeout(1500)
            await _ensure_auth()
            if not await _on_flashops_ready():
                # Post-login stok changed — rebuild and retry once.
                flash_url = await _flashops_url()
                if flash_url:
                    await gui_page.goto(
                        flash_url, timeout=menu_timeout, wait_until="domcontentloaded"
                    )
                    await gui_page.wait_for_timeout(1200)
            return await _on_flashops_ready()
        except Exception as exc:
            print(f"[sanity] Flashops direct goto failed: {exc}", flush=True)
            return False

    await _ensure_auth()
    mgmt_menu = gui_page.locator(
        "li.Management > a.menu, li.System > a.menu, a.menu:has-text('Management')"
    ).first
    try:
        await mgmt_menu.wait_for(state="visible", timeout=menu_timeout)
    except Exception:
        if host:
            base = f"{format_luci_url(host).rstrip('/')}/"
        else:
            current = gui_page.url or ""
            if "/cgi-bin/luci" in current:
                base = current.split("/cgi-bin/luci")[0] + "/cgi-bin/luci/"
            else:
                base = current
        print(f"[sanity] Management menu missing — reloading {base[:80]}", flush=True)
        await gui_page.goto(base, timeout=menu_timeout, wait_until="domcontentloaded")
        await gui_page.wait_for_timeout(1500)
        await _ensure_auth()
        stok = await resolve_luci_stok(gui_page)
        if stok and host:
            head = format_luci_url(host).rstrip("/")
            await gui_page.goto(
                f"{head}/{stok}/", timeout=menu_timeout, wait_until="domcontentloaded"
            )
            await gui_page.wait_for_timeout(800)
        try:
            await mgmt_menu.wait_for(state="visible", timeout=menu_timeout)
        except Exception:
            if await _goto_flashops_direct():
                await gui_page.wait_for_load_state("networkidle")
                return
            raise
    await mgmt_menu.click()
    flashops = gui_page.locator('xpath=//*[@id="Management"]/li[3]/a').first
    if not await flashops.is_visible(timeout=3000):
        flashops = gui_page.locator(
            "ul.dropdown-menu a[href*='/system/flashops'], "
            "a[href*='/system/flashops'], "
            "a:has-text('Upgrade/Reset'), a:has-text('Flash')"
        ).first
    try:
        await flashops.wait_for(state="visible", timeout=menu_timeout)
        await flashops.click()
        await gui_page.wait_for_timeout(1000)
        if not await _on_flashops_ready():
            raise RuntimeError("flashops click did not yield upgrade form")
    except Exception:
        if not await _goto_flashops_direct():
            raise
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(1200)


async def open_upgrade_tab(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    async def _open_once() -> None:
        for selector in (
            'xpath=//*[@id="maincontent"]/div/div/ul/li[1]/a',
            "a[href*='/flashops/flash']",
            "a:has-text('Upgrade')",
            "li:has-text('Upgrade') a",
        ):
            tab = gui_page.locator(selector).first
            if await tab.is_visible(timeout=2000):
                await tab.click()
                await gui_page.wait_for_timeout(800)
                break
        else:
            current = (gui_page.url or "").rstrip("/")
            if "/flash" not in current.lower():
                # Prefer stok-preserving relative path.
                flash_url = f"{current}/flash" if current else ""
                if flash_url:
                    await gui_page.goto(flash_url, timeout=UITimeouts.PAGE_LOAD_MS)
                    await gui_page.wait_for_load_state("networkidle")
                    await gui_page.wait_for_timeout(800)

        http_tab = gui_page.locator(
            "a:has-text('HTTP'):visible, li:has-text('HTTP') a:visible"
        ).first
        if await http_tab.count() and await http_tab.is_visible(timeout=2000):
            await http_tab.click()
            _dbg(
                "B",
                "sanity_gui.py:open_upgrade_tab",
                "HTTP sub-tab clicked",
                {"url": gui_page.url or ""},
            )

    async def _upgrade_form_ready() -> bool:
        for sel in ("#image", "input[type='file'][name='image']", "input[type='file']", "#keep"):
            loc = gui_page.locator(sel).first
            try:
                if await loc.count() and (
                    await loc.is_visible(timeout=2000) or sel.startswith("input[type='file']")
                ):
                    return True
            except Exception:
                continue
        return False

    await _open_once()
    try:
        # Prefer file input; #keep is optional on some builds (default-checked / hidden).
        if not await _upgrade_form_ready():
            await gui_page.locator("#keep, #image, input[type='file']").first.wait_for(
                state="attached", timeout=45000
            )
    except Exception:
        print("[sanity] Upgrade tab form missing — re-auth + flashops retry", flush=True)
        if host and device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds, wait_ms=5000)
            await navigate_to_flashops(gui_page, host=host, device_creds=device_creds)
        await _open_once()
        if not await _upgrade_form_ready():
            await gui_page.locator("#image, input[type='file']").first.wait_for(
                state="attached", timeout=45000
            )
    # Soft: #keep may be absent; file picker is required for abort upload.
    file_input = gui_page.locator("#image, input[type='file'][name='image'], input[type='file']").first
    await file_input.wait_for(state="attached", timeout=45000)


async def _keep_settings_checked(gui_page) -> bool:
    keep_cb = gui_page.locator("#keep, input[name='keep']")
    if await keep_cb.count():
        try:
            return await keep_cb.first.is_checked()
        except Exception:
            return False
    for pattern in ("keep settings", "keep", "retain", "preserve"):
        labels = gui_page.locator(f"label:text-matches('{pattern}', 'i')")
        count = await labels.count()
        for i in range(count):
            label = labels.nth(i)
            checkbox = label.locator("input[type='checkbox']").first
            if await checkbox.count() == 0 or not await checkbox.is_visible(timeout=500):
                continue
            if await checkbox.is_checked():
                return True
    return False


async def _set_keep_settings(gui_page) -> bool:
    keep_cb = gui_page.locator("#keep, input[name='keep']")
    if await keep_cb.count() and await keep_cb.first.is_visible(timeout=3000):
        if not await keep_cb.first.is_checked():
            await keep_cb.first.check()
        print("[sanity] Keep settings: #keep checked", flush=True)
        checked = await _keep_settings_checked(gui_page)
        _dbg("A", "sanity_gui.py:_set_keep_settings", "keep via #keep", {"checked": checked})
        return checked

    for pattern in ("keep settings", "keep", "retain", "preserve"):
        labels = gui_page.locator(f"label:text-matches('{pattern}', 'i')")
        count = await labels.count()
        for i in range(count):
            label = labels.nth(i)
            checkbox = label.locator("input[type='checkbox']").first
            if await checkbox.count() == 0 or not await checkbox.is_visible(timeout=500):
                continue
            if not await checkbox.is_checked():
                await checkbox.check()
            print(f"[sanity] Keep settings: label '{pattern}' checked", flush=True)
            checked = await _keep_settings_checked(gui_page)
            _dbg("A", "sanity_gui.py:_set_keep_settings", "keep via label", {"pattern": pattern, "checked": checked})
            return checked

    checkbox = gui_page.locator("input[type='checkbox']").first
    if await checkbox.is_visible(timeout=2000) and not await checkbox.is_checked():
        await checkbox.check()
        print("[sanity] Keep settings: first visible checkbox checked", flush=True)
        checked = await _keep_settings_checked(gui_page)
        _dbg("A", "sanity_gui.py:_set_keep_settings", "keep via first checkbox", {"checked": checked})
        return checked

    checked = await _keep_settings_checked(gui_page)
    _dbg("A", "sanity_gui.py:_set_keep_settings", "keep settings not found", {"checked": checked})
    return checked


async def _wait_for_verify_page(gui_page, *, timeout_s: int = 180) -> str:
    deadline = time.monotonic() + timeout_s
    last_body = ""
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            body = (await gui_page.locator("body").inner_text(timeout=45000)).strip()
        except Exception:
            await asyncio.sleep(3)
            continue
        last_body = body
        lower = body.lower()
        if any(token in lower for token in ("error", "invalid", "failed", "mismatch", "not allowed")):
            _dbg(
                "D",
                "sanity_gui.py:_wait_for_verify_page",
                "inline upgrade error detected",
                {"body": body[:400], "url": gui_page.url or ""},
            )
        if ("flash firmware" in lower and "verify" in lower) or (
            "checksum" in lower and ("proceed" in lower or "size:" in lower)
        ):
            _dbg("C", "sanity_gui.py:_wait_for_verify_page", "verify page matched text", {"attempt": attempt})
            return body
        if await gui_page.locator(
            "input[value*='Proceed' i]:visible, button:has-text('Proceed'):visible"
        ).count():
            _dbg("C", "sanity_gui.py:_wait_for_verify_page", "verify page matched Proceed button", {"attempt": attempt})
            return body
        if attempt == 1 or attempt % 10 == 0:
            _dbg(
                "C",
                "sanity_gui.py:_wait_for_verify_page",
                "still waiting for verify page",
                {
                    "attempt": attempt,
                    "url": gui_page.url or "",
                    "body": body[:240],
                    "seconds_left": int(deadline - time.monotonic()),
                },
            )
        await asyncio.sleep(3)
    _dbg(
        "C",
        "sanity_gui.py:_wait_for_verify_page",
        "verify page timeout",
        {"last_body": last_body[:400], "url": gui_page.url or ""},
    )
    raise TimeoutError(
        f"[sanity] Firmware verify page did not appear ({timeout_s}s). "
        f"Last body: {last_body[:240]}"
    )


async def _click_proceed(gui_page) -> None:
    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.on("dialog", _accept_dialog)
    proceed = gui_page.locator(
        "input[value*='Proceed' i]:visible, "
        "button:has-text('Proceed'):visible, "
        "a:has-text('Proceed'):visible"
    ).first
    await proceed.wait_for(state="visible", timeout=180000)
    await proceed.click(no_wait_after=True)
    print("[sanity] Clicked Proceed — flashing started", flush=True)


async def gui_http_firmware_start_install(
    gui_page,
    image_path: Path,
    *,
    upload_timeout_s: int = 120,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """
    Management → Upgrade/Reset → HTTP → keep ON → upload → Proceed.

    Returns after Proceed is clicked (flash/install started). Does not wait for reboot.
    """
    await navigate_to_flashops(gui_page, host=host, device_creds=device_creds)
    await open_upgrade_tab(gui_page, host=host, device_creds=device_creds)
    keep_ok = await _set_keep_settings(gui_page)
    if not keep_ok:
        raise RuntimeError(
            "[sanity] Keep settings checkbox is not enabled; refusing firmware upload"
        )

    file_input = gui_page.locator("#image, input[type='file'][name='image']").first
    if not await file_input.count() or not await file_input.is_visible(timeout=1000):
        file_input = gui_page.locator("input[type='file']").first
    await file_input.wait_for(state="attached", timeout=45000)
    await file_input.set_input_files(str(image_path.resolve()))
    print(f"[sanity] Selected firmware: {image_path.name}", flush=True)

    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.on("dialog", _accept_dialog)
    flash_btn = gui_page.locator("#fw_submit")
    if not await flash_btn.count() or not await flash_btn.is_visible(timeout=3000):
        flash_btn = gui_page.locator(
            "input[value*='Upgrade' i]:visible, "
            "input[value*='Flash' i]:visible, "
            "button:has-text('Upgrade'):visible, "
            "button:has-text('Flash'):visible"
        ).first
    await flash_btn.wait_for(state="visible", timeout=45000)
    print("[sanity] Clicked Upgrade — uploading, waiting for verify page", flush=True)
    try:
        async with gui_page.expect_navigation(
            timeout=upload_timeout_s * 1000,
            wait_until="commit",
        ):
            await flash_btn.click(no_wait_after=True)
    except Exception:
        await flash_btn.click(no_wait_after=True)

    await _wait_for_verify_page(gui_page, timeout_s=upload_timeout_s)
    await _click_proceed(gui_page)
    print("[sanity] Flash/install started — ready for PDU power cut", flush=True)


async def gui_http_firmware_upgrade_keep_settings(
    gui_page,
    image_path: Path,
    *,
    upload_timeout_s: int,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """
    Management → Upgrade/Reset → HTTP → Keep settings ON → file → Upgrade → Proceed.
    """
    await navigate_to_flashops(gui_page, host=host, device_creds=device_creds)
    await open_upgrade_tab(gui_page, host=host, device_creds=device_creds)
    keep_ok = await _set_keep_settings(gui_page)
    if not keep_ok:
        raise RuntimeError(
            "[sanity] Keep settings checkbox is not enabled; refusing firmware upload"
        )

    file_input = gui_page.locator("#image, input[type='file'][name='image']").first
    if not await file_input.count() or not await file_input.is_visible(timeout=1000):
        file_input = gui_page.locator("input[type='file']").first
    await file_input.wait_for(state="attached", timeout=45000)
    image_abs = str(image_path.resolve())
    await file_input.set_input_files(image_abs)
    print(f"[sanity] Selected firmware: {image_path.name}", flush=True)

    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.on("dialog", _accept_dialog)
    flash_btn = gui_page.locator("#fw_submit")
    if not await flash_btn.count() or not await flash_btn.is_visible(timeout=3000):
        flash_btn = gui_page.locator(
            "input[value*='Upgrade' i]:visible, "
            "input[value*='Flash' i]:visible, "
            "button:has-text('Upgrade'):visible, "
            "button:has-text('Flash'):visible"
        ).first
    await flash_btn.wait_for(state="visible", timeout=45000)
    print("[sanity] Clicked Upgrade — uploading, waiting for verify page", flush=True)
    try:
        async with gui_page.expect_navigation(
            timeout=upload_timeout_s * 1000,
            wait_until="commit",
        ):
            await flash_btn.click(no_wait_after=True)
    except Exception:
        await flash_btn.click(no_wait_after=True)

    verify_body = await _wait_for_verify_page(gui_page, timeout_s=upload_timeout_s)
    lower = verify_body.lower()
    if "kept" not in lower and "keep" not in lower:
        print(
            "[sanity] Verify page did not mention keep settings "
            "(#keep was enabled before upload)",
            flush=True,
        )
    await _click_proceed(gui_page)


async def _unset_keep_settings(gui_page) -> bool:
    keep_cb = gui_page.locator("#keep, input[name='keep']")
    if await keep_cb.count() and await keep_cb.first.is_visible(timeout=3000):
        if await keep_cb.first.is_checked():
            await keep_cb.first.uncheck()
        print("[sanity] Keep settings: #keep unchecked", flush=True)
        checked = await _keep_settings_checked(gui_page)
        _dbg("A", "sanity_gui.py:_unset_keep_settings", "unkeep via #keep", {"checked": checked})
        return not checked

    for pattern in ("keep settings", "keep", "retain", "preserve"):
        labels = gui_page.locator(f"label:text-matches('{pattern}', 'i')")
        count = await labels.count()
        for i in range(count):
            label = labels.nth(i)
            checkbox = label.locator("input[type='checkbox']").first
            if await checkbox.count() == 0 or not await checkbox.is_visible(timeout=500):
                continue
            if await checkbox.is_checked():
                await checkbox.uncheck()
            print(f"[sanity] Keep settings: label '{pattern}' unchecked", flush=True)
            checked = await _keep_settings_checked(gui_page)
            _dbg(
                "A",
                "sanity_gui.py:_unset_keep_settings",
                "unkeep via label",
                {"pattern": pattern, "checked": checked},
            )
            return not checked

    checked = await _keep_settings_checked(gui_page)
    _dbg("A", "sanity_gui.py:_unset_keep_settings", "keep settings not found", {"checked": checked})
    return not checked


async def gui_http_firmware_upgrade_without_keep_settings(
    gui_page,
    image_path: Path,
    *,
    upload_timeout_s: int,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """
    Management → Upgrade/Reset → HTTP → Keep settings OFF → file → Upgrade → Proceed.
    """
    await navigate_to_flashops(gui_page, host=host, device_creds=device_creds)
    await open_upgrade_tab(gui_page, host=host, device_creds=device_creds)
    keep_cleared = await _unset_keep_settings(gui_page)
    if not keep_cleared:
        raise RuntimeError(
            "[sanity] Keep settings checkbox is still enabled; refusing without-keep upload"
        )

    file_input = gui_page.locator("#image, input[type='file'][name='image']").first
    if not await file_input.count() or not await file_input.is_visible(timeout=1000):
        file_input = gui_page.locator("input[type='file']").first
    await file_input.wait_for(state="attached", timeout=45000)
    image_abs = str(image_path.resolve())
    await file_input.set_input_files(image_abs)
    print(f"[sanity] Selected firmware: {image_path.name}", flush=True)
    _dbg(
        "E",
        "sanity_gui.py:gui_http_firmware_upgrade_without_keep_settings",
        "firmware file selected",
        {"image": image_path.name, "keep_cleared": keep_cleared, "url": gui_page.url or ""},
    )

    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.on("dialog", _accept_dialog)
    flash_btn = gui_page.locator("#fw_submit")
    btn_selector = "#fw_submit"
    if not await flash_btn.count() or not await flash_btn.is_visible(timeout=3000):
        btn_selector = "fallback upgrade button"
        flash_btn = gui_page.locator(
            "input[value*='Upgrade' i]:visible, "
            "input[value*='Flash' i]:visible, "
            "button:has-text('Upgrade'):visible, "
            "button:has-text('Flash'):visible"
        ).first
    await flash_btn.wait_for(state="visible", timeout=45000)
    _dbg(
        "B",
        "sanity_gui.py:gui_http_firmware_upgrade_without_keep_settings",
        "about to click upgrade",
        {"selector": btn_selector, "url": gui_page.url or ""},
    )
    print("[sanity] Clicked Upgrade — uploading, waiting for verify page", flush=True)
    try:
        async with gui_page.expect_navigation(
            timeout=upload_timeout_s * 1000,
            wait_until="commit",
        ):
            await flash_btn.click(no_wait_after=True)
    except Exception as exc:
        _dbg(
            "B",
            "sanity_gui.py:gui_http_firmware_upgrade_without_keep_settings",
            "navigation not immediate after upgrade click",
            {"error": str(exc), "url": gui_page.url or ""},
        )
        await flash_btn.click(no_wait_after=True)

    verify_body = await _wait_for_verify_page(gui_page, timeout_s=upload_timeout_s)
    lower = verify_body.lower()
    if "kept" in lower or "will be kept" in lower:
        print(
            "[sanity] WARNING: verify page still mentions keeping settings "
            "(checkbox was unchecked before upload)",
            flush=True,
        )
    await _click_proceed(gui_page)


WRONG_MODEL_REJECTION_PATTERNS: tuple[str, ...] = (
    "does not contain a supported format",
    "generic image format for your platform",
    "invalid image",
    "invalid firmware",
    "not supported",
    "image check failed",
    "mismatch",
)


async def _wait_for_upgrade_rejection(gui_page, *, timeout_s: int = 120) -> str:
    """Wait for LuCI to reject a bad firmware upload (no verify / Proceed page)."""
    deadline = time.monotonic() + timeout_s
    last_body = ""
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            body = (await gui_page.locator("body").inner_text(timeout=15000)).strip()
        except Exception:
            await asyncio.sleep(2)
            continue
        last_body = body
        lower = body.lower()

        if ("flash firmware" in lower and "verify" in lower) or (
            "checksum" in lower and ("proceed" in lower or "size:" in lower)
        ):
            raise RuntimeError(
                "[sanity] Wrong-model image reached verify page — upload was accepted unexpectedly"
            )
        if await gui_page.locator(
            "input[value*='Proceed' i]:visible, button:has-text('Proceed'):visible"
        ).count():
            raise RuntimeError(
                "[sanity] Proceed button visible — wrong-model upload was accepted unexpectedly"
            )

        for selector in (".alert-danger", ".alert-error", ".cbi-section-error"):
            alert = gui_page.locator(selector).first
            if await alert.count() and await alert.is_visible(timeout=500):
                text = (await alert.inner_text()).strip()
                if text:
                    return text

        if any(pattern in lower for pattern in WRONG_MODEL_REJECTION_PATTERNS):
            return body

        if attempt == 1 or attempt % 8 == 0:
            _dbg(
                "F",
                "sanity_gui.py:_wait_for_upgrade_rejection",
                "waiting for rejection",
                {"attempt": attempt, "url": gui_page.url or "", "body": body[:200]},
            )
        await asyncio.sleep(2)

    raise TimeoutError(
        f"[sanity] Wrong-model firmware rejection did not appear ({timeout_s}s). "
        f"Last body: {last_body[:240]}"
    )


def rejection_message_ok(text: str) -> bool:
    lower = (text or "").lower()
    return any(pattern in lower for pattern in WRONG_MODEL_REJECTION_PATTERNS)


async def gui_http_firmware_upload_wrong_model_rejected(
    gui_page,
    image_path: Path,
    *,
    upload_timeout_s: int = 120,
    host: str = "",
    device_creds: dict | None = None,
) -> str:
    """
    Management → Upgrade/Reset → HTTP → wrong-model file → Upgrade.

    Expects LuCI to reject the image and leave the device unchanged.
    """
    await navigate_to_flashops(gui_page, host=host, device_creds=device_creds)
    await open_upgrade_tab(gui_page, host=host, device_creds=device_creds)

    file_input = gui_page.locator("#image, input[type='file'][name='image']").first
    if not await file_input.count() or not await file_input.is_visible(timeout=1000):
        file_input = gui_page.locator("input[type='file']").first
    await file_input.wait_for(state="attached", timeout=45000)
    image_abs = str(image_path.resolve())
    await file_input.set_input_files(image_abs)
    print(f"[sanity] Selected wrong-model firmware: {image_path.name}", flush=True)

    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.on("dialog", _accept_dialog)
    flash_btn = gui_page.locator("#fw_submit")
    if not await flash_btn.count() or not await flash_btn.is_visible(timeout=3000):
        flash_btn = gui_page.locator(
            "input[value*='Upgrade' i]:visible, "
            "input[value*='Flash' i]:visible, "
            "button:has-text('Upgrade'):visible, "
            "button:has-text('Flash'):visible"
        ).first
    await flash_btn.wait_for(state="visible", timeout=45000)
    print("[sanity] Clicked Upgrade — expecting wrong-model rejection", flush=True)
    try:
        async with gui_page.expect_navigation(
            timeout=upload_timeout_s * 1000,
            wait_until="commit",
        ):
            await flash_btn.click(no_wait_after=True)
    except Exception:
        await flash_btn.click(no_wait_after=True)

    rejection = await _wait_for_upgrade_rejection(gui_page, timeout_s=upload_timeout_s)
    print(f"[sanity] Wrong-model upload rejected: {rejection[:220]}", flush=True)
    return rejection


async def _wait_for_upload_aborted_ui(gui_page, *, timeout_s: int = 60) -> None:
    """After route abort, ensure we did not reach verify / Proceed."""
    url = (gui_page.url or "").lower()
    if "chromewebdata" in url or url.startswith("chrome-error:"):
        print("[sanity] Browser error page after abort — skipping UI settle", flush=True)
        return

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            body = (await gui_page.locator("body").inner_text(timeout=10000)).strip()
        except Exception:
            await asyncio.sleep(2)
            continue
        lower = body.lower()
        if ("flash firmware" in lower and "verify" in lower) or (
            "checksum" in lower and ("proceed" in lower or "size:" in lower)
        ):
            raise RuntimeError(
                "[sanity] Verify page appeared — aborted upload may have completed unexpectedly"
            )
        if await gui_page.locator(
            "input[value*='Proceed' i]:visible, button:has-text('Proceed'):visible"
        ).count():
            raise RuntimeError(
                "[sanity] Proceed button visible — aborted upload may have completed unexpectedly"
            )
        if "uploading" not in lower:
            return
        await asyncio.sleep(2)
    print("[sanity] Uploading indicator still visible after abort window — continuing checks", flush=True)


def _is_firmware_upload_post(request) -> bool:
    url = (request.url or "").lower()
    if request.method != "POST" or "flashops" not in url:
        return False
    if "/flash" in url or "/upgrade" in url:
        return True
    content_type = (request.headers.get("content-type") or "").lower()
    return "multipart/form-data" in content_type


async def gui_http_firmware_upload_abort_midway(
    gui_page,
    image_path: Path,
    *,
    host: str = "",
    device_creds: dict | None = None,
    upload_timeout_s: int = 120,
    abort_delay_s: float = 2.0,
) -> dict[str, bool]:
    """
    Management → Upgrade/Reset → HTTP → correct-model file → Upgrade.

    Aborts the flashops POST mid-upload via Playwright route (after ``abort_delay_s``).
    """
    upload_post_seen = asyncio.Event()
    upload_aborted = asyncio.Event()
    abort_once = False

    async def intercept_upload(route):
        nonlocal abort_once
        request = route.request
        if abort_once or not _is_firmware_upload_post(request):
            await route.continue_()
            return
        abort_once = True
        upload_post_seen.set()
        if abort_delay_s > 0:
            await asyncio.sleep(abort_delay_s)
        await route.abort("failed")
        upload_aborted.set()
        _dbg(
            "G",
            "sanity_gui.py:intercept_upload",
            "flashops POST aborted",
            {"url": request.url or ""},
        )

    await gui_page.route("**/*", intercept_upload)
    try:
        await navigate_to_flashops(gui_page, host=host, device_creds=device_creds)
        await open_upgrade_tab(gui_page, host=host, device_creds=device_creds)

        file_input = gui_page.locator("#image, input[type='file'][name='image']").first
        if not await file_input.count() or not await file_input.is_visible(timeout=1000):
            file_input = gui_page.locator("input[type='file']").first
        await file_input.wait_for(state="attached", timeout=45000)
        await file_input.set_input_files(str(image_path.resolve()))
        print(f"[sanity] Selected firmware for abort test: {image_path.name}", flush=True)

        async def _accept_dialog(dialog):
            await dialog.accept()

        gui_page.on("dialog", _accept_dialog)
        flash_btn = gui_page.locator("#fw_submit")
        if not await flash_btn.count() or not await flash_btn.is_visible(timeout=3000):
            flash_btn = gui_page.locator(
                "input[value*='Upgrade' i]:visible, "
                "input[value*='Flash' i]:visible, "
                "button:has-text('Upgrade'):visible, "
                "button:has-text('Flash'):visible"
            ).first
        await flash_btn.wait_for(state="visible", timeout=45000)

        print(
            f"[sanity] Clicked Upgrade — will abort POST after {abort_delay_s}s",
            flush=True,
        )
        # Do not use expect_navigation — aborting the POST yields net::ERR_FAILED.
        await flash_btn.click(no_wait_after=True, timeout=60000)

        deadline = time.monotonic() + min(upload_timeout_s, 120)
        uploading_seen = False
        while time.monotonic() < deadline:
            if upload_aborted.is_set():
                break
            try:
                body = (await gui_page.locator("body").inner_text(timeout=3000)).lower()
                if "uploading" in body:
                    uploading_seen = True
            except Exception:
                pass
            if upload_post_seen.is_set():
                break
            await asyncio.sleep(0.3)

        if not upload_aborted.is_set():
            raise TimeoutError(
                f"[sanity] Firmware upload POST was not aborted within {min(upload_timeout_s, 120)}s"
            )

        await _wait_for_upload_aborted_ui(gui_page, timeout_s=30)

        result = {
            "upload_post_seen": upload_post_seen.is_set(),
            "upload_aborted": upload_aborted.is_set(),
            "uploading_seen": uploading_seen,
        }
        _dbg("G", "sanity_gui.py:gui_http_firmware_upload_abort_midway", "abort complete", result)
        print(f"[sanity] Upload abort signals: {result}", flush=True)
        return result
    finally:
        try:
            await gui_page.unroute("**/*", intercept_upload)
        except Exception:
            await gui_page.unroute("**/*")
