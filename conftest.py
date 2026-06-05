import builtins
import logging
import re
import pytest
import csv
import json
import os
import sys
import asyncio
from datetime import datetime
from pathlib import Path


def _enable_live_console_output() -> None:
    """Line-buffer stdout/stderr and default flush=True on print (Jenkins is not a TTY)."""
    if getattr(builtins, "_ubr_flush_print_installed", False):
        return
    _orig_print = builtins.print

    def print(*args, **kwargs):  # noqa: A001
        kwargs.setdefault("flush", True)
        return _orig_print(*args, **kwargs)

    builtins.print = print
    builtins._ubr_flush_print_installed = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(line_buffering=True)
            except Exception:
                pass


_enable_live_console_output()
import httpx
from playwright.async_api import async_playwright
from pages.locators import LoginPageLocators
from scrapli.driver.generic import AsyncGenericDriver
from utils.net_utils import format_http_host, normalize_ip
from utils.profile_manager import load_profile_bundle
from utils.recovery_manager import RecoveryManager, set_active_recovery_manager

ARTIFACTS_DIR = Path("reports/artifacts")


def _artifact_path(*parts: str) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS_DIR.joinpath(*parts)


def _profile_uses_ipv6(profile_name: str) -> bool:
    from utils.profile_manager import _load_profile_file

    dut = _load_profile_file(profile_name)["dut"]
    return bool(dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"))


def _cli_local_override(config, profile_name: str) -> str | None:
    """Do not apply default --local-ipv6 when the active profile is IPv4-only."""
    if _profile_uses_ipv6(profile_name):
        return config.getoption("--local-ipv6") or config.getoption("--local-ip") or None
    return config.getoption("--local-ip") or None


def _cli_remote_override(config, profile_name: str) -> str | None:
    if _profile_uses_ipv6(profile_name):
        return config.getoption("--remote-ipv6") or config.getoption("--remote-ip") or None
    return config.getoption("--remote-ip") or None

# =====================================================================
# 1. COMMAND LINE ARGUMENTS
# =====================================================================
def pytest_addoption(parser):
    group = parser.getgroup("UBR Automation Config")
    group.addoption("--local-ip", action="store", default="192.168.2.230", help="BTS/Local IP Address")
    group.addoption("--remote-ip", action="store", default="", help="Optional CPE IPv4 hint; discovery is dynamic when empty")
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
    group.addoption(
        "--fallback-ip",
        action="store",
        default="10.0.0.1",
        help="Recovery-only factory IPv4 for bootstrap link/device restore (not used in strict IPv6 tests)",
    )
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
        "--allow-regression",
        action="store_true",
        default=False,
        help="Enable stability regression tests (reboot/network-reload/firmware cycles).",
    )
    group.addoption(
        "--regression-iterations",
        action="store",
        default=None,
        type=int,
        help="Default cycle count for all regression tests when per-case options are unset.",
    )
    group.addoption(
        "--regression-iterations-reg01",
        action="store",
        default=None,
        type=int,
        help="Cycle count for REG_01 soft reboot.",
    )
    group.addoption(
        "--regression-iterations-reg02",
        action="store",
        default=None,
        type=int,
        help="Cycle count for REG_02 network soft reset.",
    )
    group.addoption(
        "--regression-iterations-reg03",
        action="store",
        default=None,
        type=int,
        help="Cycle count for REG_03 firmware upgrade.",
    )
    group.addoption(
        "--firmware-image",
        action="store",
        default="",
        help="Firmware image path for REG_03 firmware upgrade regression.",
    )
    group.addoption(
        "--regression-report",
        action="store",
        default="reports/artifacts/Regression_Report.html",
        help="Single HTML report path for all regression iterations/runs (append when state file exists).",
    )
    group.addoption(
        "--regression-fresh",
        action="store_true",
        default=False,
        help="Clear shared regression state and overwrite the report (default: append to same file).",
    )
    group.addoption(
        "--allow-attenuator-lab",
        action="store_true",
        default=False,
        help="Run lab tests that drive Vaunix LDA-602 attenuators (RF path).",
    )
    group.addoption(
        "--attenuator-backend",
        action="store",
        default="auto",
        help="Vaunix LDA backend: auto | dll | mock (lab tests default to mock).",
    )
    group.addoption(
        "--allow-ip-suite",
        action="store_true",
        default=False,
        help="Enable IP_01–IP_60 networking validation tests (tests/IP/).",
    )
    group.addoption(
        "--allow-ip-destructive",
        action="store_true",
        default=False,
        help="Allow IP cases that reboot, network-reload, or flap interfaces.",
    )
    group.addoption(
        "--no-ip-stop-on-first-fail",
        action="store_true",
        default=False,
        help="Keep running remaining IP tests after the first failure (default: stop at first fail).",
    )
    group.addoption(
        "--allow-ip-cpe",
        action="store_true",
        default=False,
        help="Include CPE-side IP tests (default: BTS only). Also enabled when -k contains 'cpe'.",
    )
    group.addoption(
        "--skip-testbed-bootstrap",
        action="store_true",
        default=False,
        help="Skip session testbed bootstrap (mgmt VLAN, CPE discovery, VLAN modes).",
    )
    group.addoption(
        "--bootstrap-only",
        action="store_true",
        default=False,
        help="Run testbed bootstrap then exit (no tests). Use with an empty or dummy test path.",
    )
    group.addoption(
        "--factory-provision",
        action="store_true",
        default=False,
        help=(
            "After factory reset: run factory_provision (VLAN/NMS/AIRTEL/link) before bootstrap. "
            "Use --profile factory_provision. For installer GUI use scripts/factory_provision.py --headed."
        ),
    )

# =====================================================================
# 2. PARAMETER FIXTURES
# =====================================================================
@pytest.fixture(scope="session")
async def testbed_ready(request, profile_bundle, device_creds):
    """
    Configure lab for mgmt VLAN-only access:
    BTS QinQ, CPE transparent, mgmt addresses, CPE IP from BTS DHCP (SSH only).
    """
    tb = profile_bundle.active.get("testbed", {}) or {}
    if request.config.getoption("--skip-testbed-bootstrap") or not tb.get("bootstrap_on_start", True):
        return profile_bundle

    if request.config.getoption("--factory-provision"):
        from utils.factory_provision import provision_factory_reset

        print("\n[testbed] Factory provision (post-reset VLAN/NMS/AIRTEL/link)...")
        cli_fb = request.config.getoption("--fallback-ip") or None
        await provision_factory_reset(
            profile_bundle,
            device_creds,
            gui_page=None,
            fallback_ip=cli_fb,
        )

    from utils.testbed_bootstrap import bootstrap_testbed

    print("\n[testbed] Running session bootstrap (mgmt VLAN, VLAN modes, CPE discovery)...")
    cli_fb = request.config.getoption("--fallback-ip") or None
    await bootstrap_testbed(
        profile_bundle,
        device_creds,
        gui_page=None,
        cli_fallback_ip=cli_fb,
    )
    return profile_bundle


def _ip_suite_include_cpe(config) -> bool:
    if config.getoption("--allow-ip-cpe"):
        return True
    keyword = str(getattr(config.option, "keyword", "") or "")
    return bool(re.search(r"\bcpe\b", keyword, re.IGNORECASE))


def _is_ip_cpe_test_item(item) -> bool:
    if not item.get_closest_marker("IP"):
        return False
    tail = item.nodeid.split("::")[-1].lower()
    if tail.endswith("_cpe") or "_cpe[" in tail:
        return True
    if "-cpe]" in item.nodeid.lower():
        return True
    return False


def pytest_collection_modifyitems(config, items):
    if config.getoption("--bootstrap-only"):
        selected = [
            item
            for item in items
            if "testbed" in item.nodeid or "tests/ip/" in item.nodeid.lower()
        ]
        if not selected and items:
            selected = items[:1]
        items[:] = selected[:1]
        return

    if config.getoption("--allow-ip-suite") and not _ip_suite_include_cpe(config):
        items[:] = [item for item in items if not _is_ip_cpe_test_item(item)]


@pytest.fixture(scope="session")
def bsu_ip(request, testbed_ready):
    dut = testbed_ready.active["dut"]
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        cli_local_v6 = request.config.getoption("--local-ipv6")
        if cli_local_v6:
            return normalize_ip(cli_local_v6)
        return normalize_ip(str(dut["local_ipv6"]))
    if dut.get("local_ip"):
        return normalize_ip(str(dut["local_ip"]))
    return request.config.getoption("--local-ip")

@pytest.fixture(scope="session")
def cpe_ips(request, testbed_ready):
    dut = testbed_ready.active["dut"]
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        cli_remote_v6 = request.config.getoption("--remote-ipv6")
        if cli_remote_v6:
            return [normalize_ip(ip.strip()) for ip in cli_remote_v6.split(",") if ip.strip()]
        return [normalize_ip(str(ip)) for ip in dut.get("remote_ipv6s", []) if str(ip).strip()]
    if dut.get("remote_ips"):
        return [normalize_ip(str(ip)) for ip in dut["remote_ips"] if str(ip).strip()]
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
    profile_name = request.config.getoption("--profile")
    return load_profile_bundle(
        profile_name=profile_name,
        recovery_profile_name=request.config.getoption("--recovery-profile"),
        local_ip=_cli_local_override(request.config, profile_name),
        remote_ip=_cli_remote_override(request.config, profile_name),
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
def _session_bts_ssh_hosts(request, profile: dict, bsu_ip: str) -> list[str]:
    """BTS SSH chain: factory IPv4 first, then mgmt IPv6 (matches IP preflight step 1)."""
    from utils.ip_test_flows import _factory_first_ssh_hosts, _ordered_unique_hosts

    dut = profile.get("dut", {}) or {}
    tb = profile.get("testbed", {}) or {}
    rec = tb.get("recovery", {}) or {}
    mgmt = tb.get("mgmt_vlan", {}) or {}
    cli_fb = (
        request.config.getoption("--fallback-ip")
        or rec.get("bts_fallback_ipv4")
        or dut.get("local_ip")
        or "10.0.0.1"
    )
    return _factory_first_ssh_hosts(
        _ordered_unique_hosts(
            cli_fb,
            rec.get("bts_fallback_ipv4"),
            dut.get("local_ip"),
            bsu_ip,
            mgmt.get("ipv6_bts"),
            dut.get("local_ipv6"),
        )
    )


@pytest.fixture(scope="session")
async def root_ssh(request, bsu_ip, device_creds, recovery_manager):
    """SSH connection to the Linux backend as 'root'."""
    from utils.ip_test_flows import _wait_ssh_any

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    profile = recovery_manager.profile_bundle.active
    hosts = _session_bts_ssh_hosts(request, profile, bsu_ip)
    conn, effective = await _wait_ssh_any(
        hosts,
        device_creds["pass"],
        timeout_s=90,
        interval_s=5,
    )
    if effective != normalize_ip(bsu_ip):
        print(f"[ssh] root_ssh session on {effective} (profile primary {bsu_ip})")
    await recovery_manager.ensure_link_or_recover(bsu_ip=bsu_ip, device_creds=device_creds, root_ssh=conn)
    from utils.link_ssid import ensure_bts_link_ssid_ssh

    profile = recovery_manager.profile_bundle.active
    await ensure_bts_link_ssid_ssh(
        conn,
        profile=profile,
        radio_idx=int(profile.get("link", {}).get("radio_idx", 1)),
    )
    yield conn
    await conn.close()

@pytest.fixture(scope="session")
async def bsu_admin_cli(bsu_ip, device_creds):
    """SSH connection to the device CLI as 'admin'."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    device = {
        "host": bsu_ip,
        "auth_username": "admin",
        "auth_password": device_creds["pass"],
        "auth_strict_key": False,
        "transport": "asyncssh",
        "channel_log": str(_artifact_path(f"admin_cli_{bsu_ip}.log")),
    }
    conn = AsyncGenericDriver(**device)
    await conn.open()
    yield conn
    await conn.close()

# =====================================================================
# 4. REPORTING HOOKS
# =====================================================================
def pytest_html_report_title(report):
    if getattr(report.config, "_regression_mode", False):
        report.title = "UBR Stability Regression — Pytest Execution Report"
        return
    report.title = "Senao UBR P2MP - Detailed Engineering Execution Report"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    progress = getattr(item.config, "_ip_suite_progress", None)
    if progress is not None and report.when == "call" and item.get_closest_marker("IP"):
        from utils.ip_suite_progress import outcome_from_report

        duration = getattr(call, "duration", 0.0) or 0.0
        progress.record(
            item.nodeid,
            outcome=outcome_from_report(report),
            duration_s=float(duration),
        )
    if report.when != "call" or "Regression" not in item.keywords:
        return
    try:
        from pytest_html import extras as html_extras
    except ImportError:
        return
    collector = getattr(item.config, "_regression_collector", None)
    if collector is None:
        return
    summary_html = collector.summary_for_test(item.nodeid)
    extra = getattr(report, "extra", [])
    extra.append(html_extras.html(f"<h4>Iteration Ping/Web Matrix</h4>{summary_html}"))
    report.extra = extra


def _resolve_testbed_hosts(config):
    """BTS and CPE hosts/password using mgmt VLAN addresses (post-bootstrap dut)."""
    profile_name = config.getoption("--profile")
    bundle = load_profile_bundle(
        profile_name=profile_name,
        recovery_profile_name=config.getoption("--recovery-profile"),
        local_ip=_cli_local_override(config, profile_name),
        remote_ip=_cli_remote_override(config, profile_name),
        username=config.getoption("--username"),
        password=config.getoption("--password"),
    )
    dut = bundle.active["dut"]
    password = config.getoption("--password")
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        bsu_host = normalize_ip(str(dut["local_ipv6"]))
        cli_remote_v6 = config.getoption("--remote-ipv6")
        if cli_remote_v6:
            cpe_hosts = [normalize_ip(ip.strip()) for ip in cli_remote_v6.split(",") if ip.strip()]
        else:
            cpe_hosts = [normalize_ip(str(ip)) for ip in dut.get("remote_ipv6s", []) if str(ip).strip()]
    else:
        bsu_host = config.getoption("--local-ip")
        raw = config.getoption("--remote-ip")
        cpe_hosts = [normalize_ip(ip.strip()) for ip in raw.split(",") if ip.strip()]
    return bsu_host, cpe_hosts, password


def _write_testbed_summary(config) -> None:
    """Persist BTS/CPE model, FW, IP, VLAN, QoS for customer HTML report generation."""
    report_json = _artifact_path("report.json")
    if not report_json.is_file() and not os.path.isfile("report.json"):
        return
    try:
        from utils.regression_device_info import collect_testbed_summary

        bsu_host, cpe_hosts, password = _resolve_testbed_hosts(config)
        summary = asyncio.run(collect_testbed_summary(bsu_host, cpe_hosts, password))
        with _artifact_path("testbed_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        print(f"\n[report] Testbed summary written (BTS={bsu_host}, CPE={cpe_hosts[:1] or ['—']})")
    except Exception as exc:
        print(f"\n[report] Testbed summary collection skipped: {exc}")


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Generates customer CSV and regression HTML summaries at end of run."""
    _write_testbed_summary(session.config)
    reports_dir = str(ARTIFACTS_DIR)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    customer_report_path = os.path.join(reports_dir, f"Customer_Summary_{timestamp}.csv")
    reporter = session.config.pluginmanager.get_plugin('terminalreporter')
    pytest_stats = {}
    if reporter:
        passed = len(reporter.stats.get('passed', []))
        failed = len(reporter.stats.get('failed', []))
        skipped = len(reporter.stats.get('skipped', []))
        total = passed + failed + skipped
        success_rate = f"{(passed / total) * 100:.2f}%" if total > 0 else "0.00%"
        pytest_stats = {"passed": passed, "failed": failed, "skipped": skipped, "total": total}

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

    if getattr(session.config, "_regression_mode", False):
        from pathlib import Path
        from utils.regression_report import get_regression_collector

        from utils.regression_report import RegressionReportCollector

        state_path = getattr(session.config, "_regression_state_path", None)
        collector = get_regression_collector()
        if state_path and Path(state_path).is_file():
            merged = RegressionReportCollector.load_from_state_file(state_path)
            collector.iterations = merged.iterations
            collector.meta = merged.meta
        elif collector._state_path:
            collector._persist_state()
        regression_html = Path(
            getattr(session.config, "_regression_report_path", None)
            or session.config.getoption("--regression-report")
        )
        collector.render_html(regression_html, pytest_stats=pytest_stats)
        session.config._regression_html_report = str(regression_html.resolve())
        print(f"\n[REGRESSION] Detailed HTML report: {regression_html.resolve()}")
        pytest_html = getattr(session.config, "_regression_pytest_html", None)
        if pytest_html:
            print(f"[REGRESSION] Pytest HTML report: {pytest_html}")

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
async def gui_page(request, gui_browser, bsu_ip, device_creds, recovery_manager, root_ssh):
    """
    Logs into the GUI once per test run and yields the LIVE authenticated page.
    This safely bypasses the strict URL-token security on Senao devices.
    """
    print("\n[+] Performing Global GUI Session Login...")

    # Open the browser and start the DVR trace for the entire session
    context = await gui_browser.new_context(ignore_https_errors=True)
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()

    profile = recovery_manager.profile_bundle.active
    gui_hosts = _session_bts_ssh_hosts(request, profile, bsu_ip)
    gui_errors: list[str] = []
    logged_in = False
    for wait_s in (0, 5, 10, 15):
        if wait_s:
            await asyncio.sleep(wait_s)
        for gui_host in gui_hosts:
            try:
                await page.goto(
                    f"https://{format_http_host(gui_host)}/cgi-bin/luci/",
                    wait_until="commit",
                    timeout=15000,
                )
                await page.locator(LoginPageLocators.USERNAME_INPUT).wait_for(
                    state="visible", timeout=15000
                )
                await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                await page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                await page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
                logged_in = True
                break
            except Exception as exc:
                gui_errors.append(f"{gui_host}: {exc}")
        if logged_in:
            break
    if not logged_in:
        await recovery_manager.ensure_link_or_recover(
            gui_page=page,
            bsu_ip=bsu_ip,
            device_creds=device_creds,
        )
        gui_errors_after = []
        for wait_s in (0, 10, 20, 30):
            if wait_s:
                await asyncio.sleep(wait_s)
            for gui_host in gui_hosts:
                try:
                    await page.goto(
                        f"https://{format_http_host(gui_host)}/cgi-bin/luci/",
                        wait_until="commit",
                        timeout=20000,
                    )
                    await page.locator(LoginPageLocators.USERNAME_INPUT).wait_for(
                        state="visible", timeout=20000
                    )
                    await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                    await page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                    await page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
                    logged_in = True
                    break
                except Exception as exc:
                    gui_errors_after.append(f"{gui_host}: {exc}")
            if logged_in:
                break
        else:
            raise RuntimeError(
                "Unable to open GUI to "
                f"{bsu_ip} after retries and recovery: "
                f"initial={' | '.join(gui_errors)}; "
                f"after_recovery={' | '.join(gui_errors_after)}"
            )

    print("    -> Waiting 3 seconds for Dashboard routing...")
    await page.wait_for_timeout(3000)

    from utils.link_ssid import ensure_bts_link_ssid_gui

    profile = recovery_manager.profile_bundle.active
    try:
        await ensure_bts_link_ssid_gui(page, root_ssh, profile=profile)
    except Exception as exc:
        print(f"[link] GUI SSID restore after login skipped: {exc}")

    ip_cfg = profile.get("ip_tests", {}) or {}
    if ip_cfg.get("take_session_backup", False):
        from utils.device_backup import ensure_session_device_backup

        await ensure_session_device_backup(
            page,
            profile=profile,
            device_creds=device_creds,
            gui_ip=bsu_ip,
            ssh=root_ssh,
            password=device_creds["pass"],
        )

    # Hand the LIVE, logged-in page to the tests!
    yield page

    # When all tests finish, save the trace and close the browser
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    await context.tracing.stop(path=str(_artifact_path("global_gui_trace.zip")))
    await context.close()


def _quiet_ssh_library_logs() -> None:
    """Keep Jenkins console to pytest prints + IP progress table (not scrapli/asyncssh INFO)."""
    try:
        import asyncssh.logging as ash_log

        ash_log.set_log_level("WARNING")
        ash_log.set_sftp_log_level("WARNING")
    except Exception:
        pass
    for name in (
        "scrapli",
        "scrapli.driver",
        "scrapli.channel",
        "scrapli.transport",
        "asyncssh",
        "asyncssh.connection",
        "asyncssh.stream",
        "asyncssh.sftp",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


_quiet_ssh_library_logs()


def pytest_collection_finish(session):
    count = len(session.items)
    print(f"\n[pytest] Collected {count} test(s). Starting session setup (not stuck)...\n")
    if session.config.getoption("--allow-ip-suite"):
        ip_items = [i for i in session.items if i.get_closest_marker("IP")]
        if ip_items:
            from utils.ip_suite_progress import IpSuiteProgress

            session.config._ip_suite_progress = IpSuiteProgress(
                [item.nodeid for item in ip_items]
            )


def pytest_sessionstart(session):
    print(
        "[pytest] Session fixtures next: testbed_ready → root_ssh → gui_page "
        "(bootstrap/SSH/GUI can take several minutes on Jenkins).\n"
    )


def pytest_configure(config):
    _quiet_ssh_library_logs()
    from config.ip_test_cases import IP_TEST_CASES

    for case in IP_TEST_CASES:
        config.addinivalue_line("markers", f"{case.case_id}: {case.title} ({case.category})")

    if config.getoption("--allow-ip-suite") and not config.getoption("--no-ip-stop-on-first-fail"):
        profile_name = config.getoption("--profile") or "default"
        stop = True
        try:
            from utils.profile_manager import _load_profile_file

            ip_cfg = (_load_profile_file(profile_name).get("ip_tests") or {})
            stop = bool(ip_cfg.get("stop_on_first_failure", True))
        except Exception:
            pass
        if stop and getattr(config.option, "maxfail", 0) == 0:
            config.option.maxfail = 1
            print("\n[IP] stop_on_first_failure: aborting suite after first failed case (use --no-ip-stop-on-first-fail to continue)")

    regression_mode = bool(config.getoption("--allow-regression"))
    config._regression_mode = regression_mode
    config._regression_report_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if regression_mode:
        from pathlib import Path

        from utils.regression_report import init_regression_collector_for_session

        repo_root = Path(os.path.dirname(__file__))
        reports_dir = repo_root / "reports" / "artifacts"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = Path(config.getoption("--regression-report"))
        if not report_path.is_absolute():
            report_path = repo_root / report_path
        state_path = report_path.parent / "regression_collector_state.json"

        append = not config.getoption("--regression-fresh") and state_path.is_file()
        collector = init_regression_collector_for_session(state_path=state_path, append=append)
        config._regression_collector = collector
        config._regression_report_path = str(report_path)
        config._regression_state_path = str(state_path)
        if append:
            print(f"[REGRESSION] Appending to shared report: {report_path}")

        if not getattr(config.option, "htmlpath", None):
            config.option.htmlpath = str(reports_dir / "regression_pytest.html")
            config.option.self_contained_html = True
        config._regression_pytest_html = config.option.htmlpath

    try:
        profile_name = config.getoption("--profile")
        bundle = load_profile_bundle(
            profile_name=profile_name,
            recovery_profile_name=config.getoption("--recovery-profile"),
            local_ip=_cli_local_override(config, profile_name),
            remote_ip=_cli_remote_override(config, profile_name),
            username=config.getoption("--username"),
            password=config.getoption("--password"),
        )
        manager = RecoveryManager(bundle)
        set_active_recovery_manager(manager)
        config._ubr_recovery_manager = manager
    except Exception:
        config._ubr_recovery_manager = None


# =====================================================================
# 7. IP SUITE (tests/IP/test_IP.py — same layout as GUI tests)
# =====================================================================
@pytest.fixture
async def run_ip(request, profile_bundle, bsu_ip, cpe_ips, device_creds, gui_page):
    """Callable: await run_ip('IP_05', 'bts')."""

    from utils.ip_validation_flows import case_needs_gui, run_ip_validation

    async def _run(case_id: str, target: str) -> None:
        page = gui_page if case_needs_gui(case_id, target) else None
        await run_ip_validation(
            case_id,
            target,
            request=request,
            profile_bundle=profile_bundle,
            bsu_ip=bsu_ip,
            cpe_ips=cpe_ips,
            device_creds=device_creds,
            gui_page=page,
        )

    return _run


def pytest_generate_tests(metafunc):
    """Parametrize active IP_27–IP_36 in test_IP.test_ip_extended_case only."""
    if "ip_extended_bundle" not in metafunc.fixturenames:
        return
    from config.ip_test_cases import IP_TEST_CASES, ACTIVE_IP_CASE_IDS, device_targets

    include_cpe = _ip_suite_include_cpe(metafunc.config)
    params = []
    for case in IP_TEST_CASES:
        num = int(case.case_id.split("_", 1)[1])
        if num < 27 or case.case_id not in ACTIVE_IP_CASE_IDS:
            continue
        targets = device_targets(case)
        if not include_cpe:
            targets = tuple(t for t in targets if t == "bts")
        for target in targets:
            params.append(
                pytest.param((case, target), id=f"{case.case_id}-{target.upper()}")
            )
    metafunc.parametrize("ip_extended_bundle", params)