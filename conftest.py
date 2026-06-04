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
from utils.link_test_config import resolve_link_test_config
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
    group.addoption(
        "--link-test-vlan",
        action="store",
        default=None,
        help="Link Test Tool VLAN ID override (0-4094). Profile/CLI priority: CLI > profile > default.",
    )
    group.addoption(
        "--link-test-duration",
        action="store",
        default=None,
        help="Link Test Tool duration in seconds (override).",
    )
    group.addoption(
        "--link-test-bw-min",
        action="store",
        default=None,
        help="Link Test Tool minimum random bandwidth in Mbps (override).",
    )
    group.addoption(
        "--link-test-bw-max",
        action="store",
        default=None,
        help="Link Test Tool maximum random bandwidth in Mbps (override).",
    )
    group.addoption(
        "--link-test-reference-json",
        action="store",
        default=None,
        help="Optional JSON file with external tester throughput/latency for GUI_130 comparison.",
    )
    group.addoption(
        "--skip-cpe-api-24",
        action="store_true",
        default=False,
        help="Skip CPE 2.4 GHz btsconnect API tests (169.254.254.1).",
    )
    group.addoption(
        "--cpe-24-api-base",
        action="store",
        default=None,
        help="Override CPE 2.4 GHz mgmt API base (default fixed http://169.254.254.1).",
    )
    group.addoption(
        "--cpe-24-bts-ssid",
        action="store",
        default=None,
        help="Step 2 Case 1: BTS SSID in btsconnect POST (links CPE to BTS; not PC Wi‑Fi).",
    )
    group.addoption(
        "--cpe-24-bts-password",
        action="store",
        default=None,
        help="Step 2 Case 1: BTS security key in btsconnect POST body.",
    )
    group.addoption(
        "--cpe-24-btsconnect-timeout",
        action="store",
        default=None,
        help="Seconds to wait for btsconnect HTTP response (default 300; CPE links to BTS first).",
    )
    group.addoption(
        "--cpe-24-btsconnect-negative-timeout",
        action="store",
        default=None,
        help="Read timeout for API_02/API_03 btsconnect (default 120; should fail fast).",
    )
    group.addoption(
        "--cpe-24-reset-wait-s",
        action="store",
        default=None,
        help="Seconds to wait after full CPE factory reset (default 120).",
    )
    group.addoption(
        "--cpe-24-factory-reset-command",
        action="store",
        default=None,
        help="SSH command for full CPE reset on 169.254.254.1 (default: firstboot && reboot).",
    )
    group.addoption(
        "--cpe-24-factory-reset-path",
        action="store",
        default=None,
        help="Optional mgmt REST path for factory reset (default: SSH firstboot on 169.254.254.1).",
    )
    group.addoption(
        "--cpe-24-ssh-host",
        action="store",
        default=None,
        help="CPE SSH host for factory reset when on mgmt Wi‑Fi (default 169.254.254.1).",
    )
    group.addoption(
        "--cpe-24-no-bts-ssh-fetch",
        action="store_true",
        default=False,
        help="Do not SSH to BTS for btsconnect creds; pass --cpe-24-bts-ssid/password.",
    )
    group.addoption(
        "--cpe-24-invalid-ssid",
        action="store",
        default=None,
        help="API_03: fake BTS SSID (default UBR_INVALID_SSID_NOT_FOUND).",
    )
    group.addoption(
        "--cpe-24-invalid-password",
        action="store",
        default=None,
        help="API_02: wrong BTS password (default wrong-password-99).",
    )
    group.addoption(
        "--cpe-24-wifi-interface",
        action="store",
        default=None,
        help="Lab PC Wi‑Fi NIC (optional; auto-detect e.g. wlp3s0). Omit if not wlan0.",
    )
    group.addoption(
        "--cpe-24-mgmt-ssid",
        action="store",
        default=None,
        help="Optional: only for --cpe-24-auto-join-wifi (manual join needs no mgmt CLI).",
    )
    group.addoption(
        "--cpe-24-mgmt-password",
        action="store",
        default=None,
        help="Optional: only for --cpe-24-auto-join-wifi.",
    )
    group.addoption(
        "--cpe-24-auto-join-wifi",
        action="store_true",
        default=False,
        help="Use nmcli to join CPE mgmt Wi‑Fi (default: you connect manually; tests verify SSID + API).",
    )
    group.addoption(
        "--cpe-24-wifi-leave-connected",
        action="store_true",
        default=False,
        help="Do not disconnect nmcli profile after API tests.",
    )
    group.addoption(
        "--cpe-24-fw-file",
        action="store",
        default=None,
        help="API_09: firmware .tgz filename or path (default dir: /tftpboot).",
    )
    group.addoption(
        "--cpe-24-fw-dir",
        action="store",
        default=None,
        help="API_09: directory containing firmware image (default /tftpboot).",
    )
    group.addoption(
        "--cpe-24-fw-upload-timeout",
        action="store",
        default=None,
        help="API_09: seconds to wait for upload-sw HTTP response (default 900).",
    )
    group.addoption(
        "--cpe-24-cpe-model",
        action="store",
        default=None,
        help="CPE model label for logs. Inferred from FW filename if omitted.",
    )
    group.addoption(
        "--cpe-24-invalid-fw-file",
        action="store",
        default=None,
        help="API_15: wrong-model firmware .tgz (default: pick other Senao-UBR*.tgz in /tftpboot).",
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


@pytest.fixture(scope="session")
def link_test_config(request, profile_bundle):
    """Monitor Link Test Tool settings (CLI overrides profile link_test section)."""
    return resolve_link_test_config(profile_bundle.active, request)

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

    # Go to the IP and log in
    await page.goto(f"https://{format_http_host(bsu_ip)}")
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