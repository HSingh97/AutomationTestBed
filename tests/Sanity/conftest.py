"""Sanity suite — IPv6 preferred; BTS falls back to factory IPv4 when v6 is down."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from scrapli.driver.generic import AsyncGenericDriver

from config.defaults import SANITY_TEST_VALUES
from pages.locators import CommonLocators, LoginPageLocators
from utils.lab_pc_net import ensure_lab_pc_untagged_ipv4, ensure_pc_interface_up
from utils.net_utils import format_http_host, is_ipv6_literal, normalize_ip
from utils.sanity_bts_host import _ipv6_endpoints_from_profile, resolve_bts_mgmt_host
from utils.sanity_ssh import close_sanity_ssh, wait_sanity_ssh

ARTIFACTS_DIR = Path("reports/artifacts")


def _ipv6_endpoints(profile_bundle, request) -> tuple[str, str, str]:
    return _ipv6_endpoints_from_profile(profile_bundle, request)


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("Sanity") for item in items):
        config.option.skip_testbed_bootstrap = True


class SanityBtsSsh:
    """Mutable session BTS SSH — reopens after cases that drop the transport."""

    def __init__(self, conn: AsyncGenericDriver, *, reopen_cb) -> None:
        self._conn = conn
        self._reopen_cb = reopen_cb

    async def replace_connection(self, conn: AsyncGenericDriver) -> None:
        if conn is self._conn:
            return
        try:
            await self._conn.close()
        except Exception:
            pass
        self._conn = conn

    async def ensure_open(self) -> None:
        try:
            await self._conn.send_command("echo ok", timeout_ops=10)
            return
        except Exception:
            pass
        try:
            await self._conn.close()
        except Exception:
            pass
        self._conn = await self._reopen_cb()
        print("[sanity] BTS SSH session reopened after disconnect", flush=True)

    async def send_command(self, *args, **kwargs):
        await self.ensure_open()
        return await self._conn.send_command(*args, **kwargs)

    async def close(self):
        return await self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


async def _heal_sanity_pc_ipv6(profile_bundle, device_creds) -> None:
    """Re-add PC IPv6 bind if a prior case flushed it (mgmt VLAN cleanup)."""
    profile = profile_bundle.active
    tb = profile.get("testbed", {}) or {}
    primary = dict(tb.get("primary_pc", {}) or {})
    if not primary.get("local", True):
        return
    dut = profile.get("dut", {}) or {}
    mgmt = tb.get("mgmt_vlan", {}) or {}
    pc_v6 = normalize_ip(str(dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or ""))
    if not pc_v6:
        return
    prefix = int(mgmt.get("prefix_len") or 120)
    pc_ipv4 = normalize_ip(str(primary.get("fallback_ipv4") or "192.168.2.200").split("/")[0])
    await ensure_lab_pc_untagged_ipv4(
        primary,
        ipv4=pc_ipv4,
        netmask=str(primary.get("fallback_netmask") or "255.255.255.0"),
        password=str(primary.get("password") or device_creds["pass"]),
        mgmt_ipv6_cidr=f"{pc_v6}/{prefix}",
    )


@pytest.fixture(scope="session")
async def testbed_ready(profile_bundle, device_creds):
    # Keep lab PC parent LAN up — never flush IPv6 or delete VLAN subifs at session start.
    tb = profile_bundle.active.get("testbed", {}) or {}
    primary = dict(tb.get("primary_pc", {}) or {})
    if primary.get("local", True):
        await ensure_pc_interface_up(primary, device_creds["pass"])
    return profile_bundle


@pytest.fixture(scope="session")
def sanity_bts_bind(request, profile_bundle, device_creds):
    """Resolved BTS host + optional IPv6 source bind (set during async fixtures)."""
    return {"host": "", "source_v6": "", "note": ""}


@pytest.fixture(scope="session")
async def bsu_ip(request, testbed_ready, profile_bundle, device_creds, sanity_bts_bind):
    host, source_v6, note = await resolve_bts_mgmt_host(request, profile_bundle, device_creds)
    sanity_bts_bind["host"] = host
    sanity_bts_bind["source_v6"] = source_v6
    sanity_bts_bind["note"] = note
    return host


@pytest.fixture(scope="session")
def cpe_ips(request, profile_bundle):
    _, cpe_v6, _ = _ipv6_endpoints(profile_bundle, request)
    if not cpe_v6 or not is_ipv6_literal(cpe_v6):
        pytest.fail("[sanity] CPE IPv6 required (--remote-ipv6)")
    raw = (request.config.getoption("--remote-ipv6") or "").strip()
    if raw:
        return [normalize_ip(ip.strip()) for ip in raw.split(",") if ip.strip()]
    return [cpe_v6]


@pytest.fixture(scope="session")
async def root_ssh(request, bsu_ip, device_creds, sanity_bts_bind, profile_bundle):
    values = SANITY_TEST_VALUES
    source_v6 = sanity_bts_bind.get("source_v6") or ""
    password = device_creds["pass"]
    recovery_s = int(values.get("ssh_recovery_timeout_s", 600))
    poll_s = int(values.get("poll_interval_s", 10))
    profile = profile_bundle.active
    tb = profile.get("testbed", {}) or {}
    rec = tb.get("recovery", {}) or {}
    fallbacks = [
        normalize_ip(str(rec.get("bts_fallback_ipv4") or "").split("/")[0]),
        normalize_ip(str(values.get("bts_lab_ipv4") or "192.168.2.10")),
        normalize_ip(str(values.get("bts_factory_ipv4") or "")),
        "10.0.0.1",
    ]

    async def _reopen_bts_ssh() -> AsyncGenericDriver:
        await _heal_sanity_pc_ipv6(profile_bundle, device_creds)
        hosts = [bsu_ip, *[h for h in fallbacks if h and h != bsu_ip]]
        last_error = ""
        for idx, host in enumerate(hosts):
            bind = source_v6 if is_ipv6_literal(host) else ""
            try:
                if idx > 0:
                    print(f"[sanity] BTS SSH reopen fallback → {host}", flush=True)
                return await wait_sanity_ssh(
                    host,
                    password,
                    source_v6=bind,
                    label="BTS",
                    timeout_s=recovery_s if idx == 0 else min(180, recovery_s),
                    poll_s=poll_s,
                )
            except Exception as exc:
                last_error = str(exc)
        raise ConnectionError(f"[sanity] BTS SSH reopen failed ({last_error})")

    print(f"\n[sanity] Waiting for BTS SSH: {bsu_ip}")
    conn = await _reopen_bts_ssh()
    wrapper = SanityBtsSsh(conn, reopen_cb=_reopen_bts_ssh)
    try:
        yield wrapper
    finally:
        await close_sanity_ssh(wrapper)


@pytest.fixture(scope="session")
async def gui_browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


async def _login_bts_gui(page, host: str, device_creds: dict) -> None:
    url = f"http://{format_http_host(host)}/cgi-bin/luci/"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if await page.locator(LoginPageLocators.USERNAME_INPUT).is_visible(timeout=15000):
        await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
        await page.wait_for_timeout(5000)
    for locator in (CommonLocators.MENU_MONITOR, "li.Management > a.menu"):
        try:
            await page.locator(locator).first.wait_for(state="visible", timeout=30000)
            return
        except Exception:
            continue
    if not await page.locator(LoginPageLocators.USERNAME_INPUT).is_visible(timeout=3000):
        return
    raise RuntimeError(f"[sanity] BTS GUI at {host} did not reach authenticated LuCI")


@pytest.fixture(scope="session")
async def gui_page(gui_browser, bsu_ip, device_creds, sanity_bts_bind):
    note = sanity_bts_bind.get("note") or "primary"
    print(f"\n[sanity] Waiting for BTS GUI login: {bsu_ip} ({note})")
    context = await gui_browser.new_context(ignore_https_errors=True)
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()

    logged_in = False
    last_error = ""
    for wait_s in (0, 5, 10, 15):
        if wait_s:
            print(f"[sanity] GUI retry in {wait_s}s …", flush=True)
            await asyncio.sleep(wait_s)
        try:
            await _login_bts_gui(page, bsu_ip, device_creds)
            logged_in = True
            print(f"[sanity] BTS GUI logged in at {bsu_ip}")
            break
        except Exception as exc:
            last_error = str(exc)

    if not logged_in:
        raise RuntimeError(f"[sanity] Unable to open BTS GUI at {bsu_ip}: {last_error}")

    await page.wait_for_timeout(2000)
    yield page

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    await context.tracing.stop(path=str(ARTIFACTS_DIR / "sanity_gui_trace.zip"))
    await context.close()
