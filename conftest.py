import pytest
import csv
import os
import asyncio
from datetime import datetime
import httpx
from playwright.async_api import async_playwright
from pages.locators import LoginPageLocators
from scrapli.driver.generic import AsyncGenericDriver
from utils.net_utils import format_http_host, normalize_ip
from utils.profile_manager import load_profile_bundle
from utils.recovery_manager import RecoveryManager, set_active_recovery_manager

# =====================================================================
# EVENT LOOP MANAGER (Fixes the "Attached to different loop" crash)
# =====================================================================
@pytest.fixture(scope="session")
def event_loop():
    """Overrides pytest default function-scoped event loop"""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()

# =====================================================================
# 1. COMMAND LINE ARGUMENTS
# =====================================================================
def pytest_addoption(parser):
    group = parser.getgroup("UBR Automation Config")
    group.addoption("--local-ip", action="store", default="192.168.2.230", help="BTS/Local IP Address")
    group.addoption("--remote-ip", action="store", default="192.168.2.231", help="CPE/Remote IP Address")
    group.addoption(
        "--local-ipv6",
        action="store",
        default="2401:4900:d0:40d4:0:17b8:0:330",
        help="BTS/Local IPv6 Address (strict IPv6 mode)",
    )
    group.addoption(
        "--remote-ipv6",
        action="store",
        default="2401:4900:d0:40d4::17b8:0:331",
        help="Comma-separated CPE IPv6 addresses",
    )
    group.addoption("--fallback-ip", action="store", default="10.0.0.1", help="BTS/CPE Fallback IP Address")
    group.addoption("--username", action="store", default="root", help="Device Username")
    group.addoption("--password", action="store", default="Sen@0ubRNwk$", help="Device Password")
    group.addoption(
        "--allow-destructive-jumbo",
        action="store_true",
        default=False,
        help="Enable destructive jumbo tests (reboot/factory reset).",
    )
    group.addoption("--profile", action="store", default="default", help="Profile name from profiles/<name>.yaml")
    group.addoption(
        "--recovery-profile",
        action="store",
        default="link_formation",
        help="Recovery profile name from profiles/<name>.yaml",
    )

# =====================================================================
# 2. PARAMETER FIXTURES
# =====================================================================
@pytest.fixture(scope="session")
def bsu_ip(request, profile_bundle):
    dut = profile_bundle.active["dut"]
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        return normalize_ip(str(dut["local_ipv6"]))
    return request.config.getoption("--local-ip")

@pytest.fixture(scope="session")
def cpe_ips(request, profile_bundle):
    dut = profile_bundle.active["dut"]
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        cli_remote_v6 = request.config.getoption("--remote-ipv6")
        if cli_remote_v6:
            return [normalize_ip(ip.strip()) for ip in cli_remote_v6.split(",") if ip.strip()]
        return [normalize_ip(str(ip)) for ip in dut.get("remote_ipv6s", []) if str(ip).strip()]
    raw = request.config.getoption("--remote-ip")
    return [normalize_ip(ip.strip()) for ip in raw.split(",") if ip.strip()]

@pytest.fixture(scope="session")
def device_creds(request):
    return {
        "user": request.config.getoption("--username"),
        "pass": request.config.getoption("--password")
    }


@pytest.fixture(scope="session")
def profile_bundle(request):
    local_ip_override = request.config.getoption("--local-ipv6") or request.config.getoption("--local-ip")
    return load_profile_bundle(
        profile_name=request.config.getoption("--profile"),
        recovery_profile_name=request.config.getoption("--recovery-profile"),
        local_ip=local_ip_override,
        username=request.config.getoption("--username"),
        password=request.config.getoption("--password"),
    )


@pytest.fixture(scope="session")
def recovery_manager(profile_bundle):
    manager = RecoveryManager(profile_bundle)
    set_active_recovery_manager(manager)
    return manager

# =====================================================================
# 3. SSH ENGINES
# =====================================================================
@pytest.fixture(scope="session")
async def root_ssh(bsu_ip, device_creds, recovery_manager):
    """SSH connection to the Linux backend as 'root'."""
    os.makedirs("logs", exist_ok=True)
    device = {
        "host": bsu_ip,
        "auth_username": "root",
        "auth_password": device_creds["pass"],
        "auth_strict_key": False,
        "transport": "asyncssh",
        "channel_log": f"logs/root_cli_{bsu_ip}.log",
    }
    conn = AsyncGenericDriver(**device)
    open_errors = []
    for wait_s in (0, 15, 20, 20):
        if wait_s:
            await asyncio.sleep(wait_s)
        try:
            await conn.open()
            break
        except Exception as exc:
            open_errors.append(str(exc))
    else:
        raise RuntimeError(f"Unable to open root SSH to {bsu_ip} after retries: {' | '.join(open_errors)}")
    await recovery_manager.ensure_link_or_recover(bsu_ip=bsu_ip, device_creds=device_creds, root_ssh=conn)
    yield conn
    await conn.close()

@pytest.fixture(scope="session")
async def bsu_admin_cli(bsu_ip, device_creds):
    """SSH connection to the device CLI as 'admin'."""
    os.makedirs("logs", exist_ok=True)
    device = {
        "host": bsu_ip,
        "auth_username": "admin",
        "auth_password": device_creds["pass"],
        "auth_strict_key": False,
        "transport": "asyncssh",
        "channel_log": f"logs/admin_cli_{bsu_ip}.log",
    }
    conn = AsyncGenericDriver(**device)
    await conn.open()
    yield conn
    await conn.close()

# =====================================================================
# 4. REPORTING HOOKS
# =====================================================================
def pytest_html_report_title(report):
    report.title = "Senao UBR P2MP - Detailed Engineering Execution Report"

@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Generates the Customer-Facing CSV summary at the end of the run."""
    reports_dir = "reports"
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    customer_report_path = os.path.join(reports_dir, f"Customer_Summary_{timestamp}.csv")
    reporter = session.config.pluginmanager.get_plugin('terminalreporter')
    if reporter:
        passed = len(reporter.stats.get('passed', []))
        failed = len(reporter.stats.get('failed', []))
        skipped = len(reporter.stats.get('skipped', []))
        total = passed + failed + skipped
        success_rate = f"{(passed / total) * 100:.2f}%" if total > 0 else "0.00%"

        with open(customer_report_path, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Senao UBR P2MP - Test Execution Summary"])
            writer.writerow(["Date Executed", datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
            writer.writerow([])
            writer.writerow(["Total Executed", "Passed", "Failed", "Skipped", "Success Rate"])
            writer.writerow([total, passed, failed, skipped, success_rate])
            manager = getattr(session.config, "_ubr_recovery_manager", None)
            if manager:
                metrics = manager.metrics
                writer.writerow([])
                writer.writerow(["Recovery Metrics"])
                writer.writerow(["Attempts", "Successes", "Failures", "Factory Resets", "Total Recovery Seconds"])
                writer.writerow(
                    [
                        metrics.attempts,
                        metrics.successes,
                        metrics.failures,
                        metrics.factory_resets,
                        f"{metrics.total_recovery_seconds:.2f}",
                    ]
                )
                writer.writerow(["Last Recovery Error", metrics.last_error or "None"])

# =====================================================================
# 5. PLAYWRIGHT GUI ENGINE
# =====================================================================
@pytest.fixture(scope="session")
async def gui_browser():
    """Spins up a headless Chromium browser for GUI testing."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


# =====================================================================
# 6. GLOBAL AUTHENTICATION ENGINE (LIVE TAB)
# =====================================================================
@pytest.fixture(scope="session")
async def gui_page(gui_browser, bsu_ip, device_creds, recovery_manager):
    """
    Logs into the GUI once per test run and yields the LIVE authenticated page.
    This safely bypasses the strict URL-token security on Senao devices.
    """
    print("\n[+] Performing Global GUI Session Login...")

    # Open the browser and start the DVR trace for the entire session
    context = await gui_browser.new_context(ignore_https_errors=True)
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()

    # Go to the IP and log in, allowing for transient GUI reachability issues.
    gui_errors = []
    for wait_s in (0, 5, 10, 15):
        if wait_s:
            await asyncio.sleep(wait_s)
        try:
            await page.goto(f"https://{format_http_host(bsu_ip)}")
            await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
            await page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
            await page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
            break
        except Exception as exc:
            gui_errors.append(str(exc))
    else:
        raise RuntimeError(f"Unable to open GUI to {bsu_ip} after retries: {' | '.join(gui_errors)}")

    print("    -> Waiting 3 seconds for Dashboard routing...")
    await page.wait_for_timeout(3000)

    # Hand the LIVE, logged-in page to the tests!
    yield page

    # When all tests finish, save the trace and close the browser
    os.makedirs("logs", exist_ok=True)
    await context.tracing.stop(path=f"logs/global_gui_trace.zip")
    await context.close()


def pytest_configure(config):
    try:
        local_ip_override = config.getoption("--local-ipv6") or config.getoption("--local-ip")
        bundle = load_profile_bundle(
            profile_name=config.getoption("--profile"),
            recovery_profile_name=config.getoption("--recovery-profile"),
            local_ip=local_ip_override,
            username=config.getoption("--username"),
            password=config.getoption("--password"),
        )
        manager = RecoveryManager(bundle)
        set_active_recovery_manager(manager)
        config._ubr_recovery_manager = manager
    except Exception:
        config._ubr_recovery_manager = None