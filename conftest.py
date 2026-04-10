import pytest
import csv
import os
import asyncio
from datetime import datetime
import httpx
from playwright.async_api import async_playwright

# --- FIXED IMPORT PATH ---
from scrapli.driver.generic import AsyncGenericDriver

import asyncio

# Add this fixture to force a single Event Loop for the whole session
@pytest.fixture(scope="session")
def event_loop():
    """Overrides pytest default function-scoped event loop"""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()

# =====================================================================
# 1. COMMAND LINE ARGUMENTS (From your Jenkins/Legacy setup)
# =====================================================================
def pytest_addoption(parser):
    group = parser.getgroup("UBR Automation Config")
    group.addoption("--local-ip", action="store", default="192.168.1.230", help="BSU/Local IP Address")
    group.addoption("--remote-ip", action="store", default="192.168.1.231",
                    help="CPE/Remote IP Address (comma-separated)")
    group.addoption("--username", action="store", default="root", help="Device Username")
    group.addoption("--password", action="store", default="admin", help="Device Password")
    group.addoption("--bandwidth", action="store", default="HT20", help="Bandwidth")
    group.addoption("--mcs-rate", action="store", default="MCS0", help="MCS Rate")
    group.addoption("--channels", action="store", default="36,50,62,100,120,149,161,167,171", help="Channels to test")
    group.addoption("--powers", action="store", default="26", help="Tx power levels")


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
# SSH ENGINE 1: ROOT BACKEND
# =====================================================================
@pytest.fixture(scope="session")
async def bsu_root_cli(bsu_ip, device_creds):
    """SSH connection to the Linux backend as 'root'."""
    os.makedirs("logs", exist_ok=True)
    device = {
        "host": bsu_ip,
        "auth_username": "root",
        "auth_password": device_creds["pass"],
        "auth_strict_key": False,
        "transport": "asyncssh",
        # FIX: Pass the exact file path directly to channel_log
        "channel_log": f"logs/root_cli_{bsu_ip}.log",
    }
    conn = AsyncGenericDriver(**device)
    await conn.open()
    yield conn
    await conn.close()

# =====================================================================
# SSH ENGINE 2: ADMIN CLI
# =====================================================================
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
        # FIX: Pass the exact file path directly to channel_log
        "channel_log": f"logs/admin_cli_{bsu_ip}.log",
    }
    conn = AsyncGenericDriver(**device)
    await conn.open()
    yield conn
    await conn.close()

@pytest.fixture(scope="session")
async def bsu_api(bsu_ip, device_creds):
    """
    Establishes an asynchronous HTTP session for GUI/NMS validations.
    Reuses the underlying TCP connection for massive speed gains.
    """
    base_url = f"https://{bsu_ip}"

    # Using verify=False because hardware often uses self-signed certs
    async with httpx.AsyncClient(base_url=base_url, verify=False) as client:
        yield client


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
# 5. PLAYWRIGHT GUI ENGINE (For Web Automation & Tracing)
# =====================================================================
@pytest.fixture(scope="session")
async def gui_browser():
    """
    Spins up a headless Chromium browser for GUI testing.
    """
    async with async_playwright() as p:
        # headless=True runs invisibly in the background.
        # Change to headless=False if you want to watch the browser pop up and move!
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()