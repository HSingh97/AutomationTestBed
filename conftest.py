import pytest
import csv
import os
import asyncio
from datetime import datetime
import httpx
from playwright.async_api import async_playwright
from pages.locators import LoginPageLocators
from scrapli.driver.generic import AsyncGenericDriver

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
    group.addoption("--username", action="store", default="root", help="Device Username")
    group.addoption("--password", action="store", default="Sen@0ubRNwk$", help="Device Password")

# =====================================================================
# 2. PARAMETER FIXTURES
# =====================================================================
@pytest.fixture(scope="session")
def bsu_ip(request):
    return request.config.getoption("--local-ip")

@pytest.fixture(scope="session")
def cpe_ips(request):
    raw = request.config.getoption("--remote-ip")
    return [ip.strip() for ip in raw.split(",") if ip.strip()]

@pytest.fixture(scope="session")
def device_creds(request):
    return {
        "user": request.config.getoption("--username"),
        "pass": request.config.getoption("--password")
    }

# =====================================================================
# 3. SSH ENGINES
# =====================================================================
@pytest.fixture(scope="session")
async def root_ssh(bsu_ip, device_creds):
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
    await conn.open()
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
async def gui_page(gui_browser, bsu_ip, device_creds):
    """
    Logs into the GUI once per test run and yields the LIVE authenticated page.
    This safely bypasses the strict URL-token security on Senao devices.
    """
    print("\n[+] Performing Global GUI Session Login...")

    # Open the browser and start the DVR trace for the entire session
    context = await gui_browser.new_context(ignore_https_errors=True)
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()

    # Go to the IP and log in
    await page.goto(f"https://{bsu_ip}")
    await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
    await page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
    await page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")

    print("    -> Waiting 3 seconds for Dashboard routing...")
    await page.wait_for_timeout(3000)

    # Hand the LIVE, logged-in page to the tests!
    yield page

    # When all tests finish, save the trace and close the browser
    os.makedirs("logs", exist_ok=True)
    await context.tracing.stop(path=f"logs/global_gui_trace.zip")
    await context.close()