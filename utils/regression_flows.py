"""Stability regression flows: repeated reboot/reset/firmware upgrade with BTS<->CPE health checks."""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

import httpx
import pytest
from scrapli.driver.generic import AsyncGenericDriver
from scrapli.exceptions import ScrapliTimeout

from pages.locators import LoginPageLocators, TopPanelLocators, UITimeouts
from utils.gui_login import login_if_needed
NETWORK_RELOAD_CMD = "/etc/init.d/network reload"
from utils.net_utils import format_http_host
from utils.recovery_manager import RecoveryManager
from utils.regression_report import HealthCheckResult, get_regression_collector


def _log(case_id: str, message: str) -> None:
    print(f"[REGRESSION][{case_id}] {message}")


def _regression_cfg(profile_bundle) -> dict:
    return profile_bundle.active.get("regression", {})


def _ping_count(profile_bundle) -> int:
    return int(_regression_cfg(profile_bundle).get("ping_count", 5))


def _reboot_wait_s(profile_bundle) -> int:
    recovery = profile_bundle.active.get("recovery", {})
    reg = _regression_cfg(profile_bundle)
    return int(reg.get("reboot_wait_seconds") or recovery.get("reboot_wait_seconds", 150))


def _network_reload_wait_s(profile_bundle) -> int:
    reg = _regression_cfg(profile_bundle)
    return int(reg.get("network_reload_wait_seconds", 30))


def _web_timeout_s(profile_bundle) -> int:
    return int(_regression_cfg(profile_bundle).get("web_timeout_seconds", 20))


async def _open_root_ssh(host: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _close_ssh(conn: AsyncGenericDriver | None) -> None:
    if conn is None:
        return
    try:
        await conn.close()
    except Exception:
        pass


async def _ensure_ssh_open(ssh: AsyncGenericDriver) -> None:
    """Re-open scrapli session after reboot or network reload drops the channel."""
    try:
        await ssh.send_command("echo ok", timeout_ops=15)
        return
    except Exception:
        pass
    try:
        await ssh.close()
    except Exception:
        pass
    await ssh.open()


async def _wait_for_ssh(host: str, password: str, *, timeout_s: int, interval_s: int = 5) -> AsyncGenericDriver:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            conn = await _open_root_ssh(host, password)
            await conn.send_command("echo ok")
            return conn
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(interval_s)
    raise TimeoutError(f"SSH to {host} not ready within {timeout_s}s: {last_error}")


async def _run_ping(
    ssh: AsyncGenericDriver,
    target_host: str,
    *,
    count: int,
    check_id: str,
    device_role: str,
    device_host: str,
) -> HealthCheckResult:
    if ":" in target_host:
        command = f"ping -6 -c {count} {shlex.quote(target_host)}"
    else:
        command = f"ping -c {count} {shlex.quote(target_host)}"
    try:
        result = await ssh.send_command(command)
        output = str(result.result or "")
        out = output.lower()
        _log(check_id, f"{device_role} cmd={command}")
        print(f"[REGRESSION][{check_id}] raw output start")
        print(output.rstrip())
        print(f"[REGRESSION][{check_id}] raw output end")
        if "100% packet loss" in out:
            return HealthCheckResult(
                check_id=check_id,
                device_role=device_role,
                device_host=device_host,
                check_type="Ping",
                passed=False,
                detail=f"No replies to {target_host}",
            )
        if "bytes from" not in out:
            return HealthCheckResult(
                check_id=check_id,
                device_role=device_role,
                device_host=device_host,
                check_type="Ping",
                passed=False,
                detail=f"Ping did not succeed to {target_host}",
            )
        return HealthCheckResult(
            check_id=check_id,
            device_role=device_role,
            device_host=device_host,
            check_type="Ping",
            passed=True,
            detail=f"Replies received ({count} probes)",
        )
    except Exception as exc:
        return HealthCheckResult(
            check_id=check_id,
            device_role=device_role,
            device_host=device_host,
            check_type="Ping",
            passed=False,
            detail=str(exc),
        )


async def _http_reachable(host: str, *, timeout_s: int) -> tuple[bool, str]:
    url = f"https://{format_http_host(host)}/cgi-bin/luci/"
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout_s) as client:
            response = await client.get(url)
            if response.status_code < 500:
                return True, f"HTTP {response.status_code} from {url}"
            return False, f"HTTP {response.status_code} from {url}"
    except Exception as exc:
        return False, str(exc)


async def _verify_web_login(
    gui_browser,
    host: str,
    device_creds: dict[str, str],
    *,
    check_id: str,
    device_role: str,
    timeout_s: int,
) -> HealthCheckResult:
    reachable, reach_detail = await _http_reachable(host, timeout_s=timeout_s)
    if not reachable:
        return HealthCheckResult(
            check_id=check_id,
            device_role=device_role,
            device_host=host,
            check_type="Web",
            passed=False,
            detail=f"HTTPS unreachable: {reach_detail}",
        )

    context = await gui_browser.new_context(ignore_https_errors=True)
    page = await context.new_page()
    try:
        await page.goto(
            f"https://{format_http_host(host)}/cgi-bin/luci/",
            wait_until="commit",
            timeout=timeout_s * 1000,
        )
        login_input = page.locator(LoginPageLocators.USERNAME_INPUT)
        await login_input.wait_for(state="visible", timeout=timeout_s * 1000)
        await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await page.wait_for_timeout(4000)
        if await login_input.is_visible(timeout=3000):
            return HealthCheckResult(
                check_id=check_id,
                device_role=device_role,
                device_host=host,
                check_type="Web",
                passed=False,
                detail="Login form still visible after credentials submitted",
            )
        return HealthCheckResult(
            check_id=check_id,
            device_role=device_role,
            device_host=host,
            check_type="Web",
            passed=True,
            detail=f"GUI login OK ({reach_detail})",
        )
    except Exception as exc:
        return HealthCheckResult(
            check_id=check_id,
            device_role=device_role,
            device_host=host,
            check_type="Web",
            passed=False,
            detail=str(exc),
        )
    finally:
        await context.close()


def _format_failure_summary(case_id: str, phase: str, checks: list[HealthCheckResult]) -> str:
    lines = [f"{case_id} [{phase}] connectivity failed:"]
    for check in checks:
        if check.passed:
            continue
        lines.append(
            f"  - {check.device_role} ({check.device_host}) {check.check_type} "
            f"[{check.check_id}]: {check.detail}"
        )
    return "\n".join(lines)


async def verify_iteration_health(
    *,
    case_id: str,
    phase: str,
    bts_host: str,
    cpe_hosts: list[str],
    device_creds: dict[str, str],
    profile_bundle,
    gui_browser,
) -> None:
    """
    After each regression iteration, verify:
      - BTS -> CPE ping
      - CPE -> BTS ping
      - BTS web (HTTPS + login)
      - CPE web (HTTPS + login)
  Pass/fail for the iteration requires every check to pass.
    """
    if not cpe_hosts:
        pytest.fail(f"{case_id}: no CPE targets configured for health validation.")

    count = _ping_count(profile_bundle)
    web_timeout = _web_timeout_s(profile_bundle)
    password = device_creds["pass"]
    checks: list[HealthCheckResult] = []

    bts_ssh = await _wait_for_ssh(bts_host, password, timeout_s=120, interval_s=5)
    cpe_ssh_map: dict[str, AsyncGenericDriver] = {}
    try:
        for cpe_host in cpe_hosts:
            cpe_ssh_map[cpe_host] = await _wait_for_ssh(cpe_host, password, timeout_s=120, interval_s=5)

        for cpe_host in cpe_hosts:
            checks.append(
                await _run_ping(
                    bts_ssh,
                    cpe_host,
                    count=count,
                    check_id=f"BTS_to_CPE",
                    device_role="BTS (Local)",
                    device_host=bts_host,
                )
            )
            checks.append(
                await _run_ping(
                    cpe_ssh_map[cpe_host],
                    bts_host,
                    count=count,
                    check_id=f"CPE_to_BTS",
                    device_role="CPE (Remote)",
                    device_host=cpe_host,
                )
            )

        checks.append(
            await _verify_web_login(
                gui_browser,
                bts_host,
                device_creds,
                check_id="BTS_GUI_LOGIN",
                device_role="BTS (Local)",
                timeout_s=web_timeout,
            )
        )
        for cpe_host in cpe_hosts:
            checks.append(
                await _verify_web_login(
                    gui_browser,
                    cpe_host,
                    device_creds,
                    check_id="CPE_GUI_LOGIN",
                    device_role="CPE (Remote)",
                    timeout_s=web_timeout,
                )
            )
    finally:
        for cpe_ssh in cpe_ssh_map.values():
            await _close_ssh(cpe_ssh)
        await _close_ssh(bts_ssh)

    collector = get_regression_collector()
    collector.record_iteration(case_id, phase, checks)

    failures = [check for check in checks if not check.passed]
    if failures:
        pytest.fail(_format_failure_summary(case_id, phase, checks))


async def _navigate_to_flashops(gui_page) -> None:
    mgmt_menu = gui_page.locator("li.Management > a.menu").first
    await mgmt_menu.wait_for(state="visible", timeout=15000)
    await mgmt_menu.click()
    flashops = gui_page.locator('xpath=//*[@id="Management"]/li[3]/a').first
    if not await flashops.is_visible(timeout=2000):
        flashops = gui_page.locator("ul.dropdown-menu a[href*='/system/flashops']").first
    await flashops.wait_for(state="visible", timeout=15000)
    await flashops.click()
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(1200)


async def _open_upgrade_tab(gui_page) -> None:
    for selector in (
        'xpath=//*[@id="maincontent"]/div/div/ul/li[1]/a',
        'xpath=//*[@id="maincontent"]/div/div/ul/li[1]/a',
        "a[href*='/flashops/flash']",
        "a:has-text('Upgrade')",
        "li:has-text('Upgrade') a",
    ):
        tab = gui_page.locator(selector).first
        if await tab.is_visible(timeout=1500):
            await tab.click()
            await gui_page.wait_for_timeout(800)
            return
    await gui_page.goto((gui_page.url or "").rstrip("/") + "/flash", timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(800)


async def _trigger_soft_reboot(gui_page, root_ssh: AsyncGenericDriver | None, *, use_gui: bool) -> None:
    if use_gui:
        reboot_btn = gui_page.locator(TopPanelLocators.REBOOT_BUTTON)
        await reboot_btn.wait_for(state="visible", timeout=15000)
        await reboot_btn.click()
        confirm_btn = gui_page.locator(TopPanelLocators.REBOOT_CONFIRM)
        await confirm_btn.wait_for(state="visible", timeout=10000)
        await confirm_btn.click()
        return
    assert root_ssh is not None, "SSH reboot requested but root_ssh is unavailable."
    await _ensure_ssh_open(root_ssh)
    await root_ssh.send_command("reboot")


async def _send_network_reload(ssh: AsyncGenericDriver, *, label: str, case_id: str) -> None:
    """Issue network reload; command may block — use background exec and tolerate SSH timeout."""
    reload_bg = f"{NETWORK_RELOAD_CMD} >/dev/null 2>&1 &"
    _log(case_id, f"{label}: {reload_bg}")
    try:
        await ssh.send_command(reload_bg, timeout_ops=15)
    except ScrapliTimeout:
        _log(case_id, f"{label}: reload dispatched (SSH channel closed during reload — expected)")


async def _trigger_network_soft_reset(
    *,
    bts_ssh: AsyncGenericDriver,
    cpe_hosts: list[str],
    device_creds: dict[str, str],
    case_id: str,
) -> None:
    """Reload network stack on CPE(s) first, then BTS — BTS reload can drop CPE SSH."""
    password = device_creds["pass"]
    for cpe_host in cpe_hosts:
        cpe_ssh = await _wait_for_ssh(cpe_host, password, timeout_s=60, interval_s=3)
        try:
            await _send_network_reload(cpe_ssh, label=f"CPE {cpe_host}", case_id=case_id)
        finally:
            await _close_ssh(cpe_ssh)
    await _ensure_ssh_open(bts_ssh)
    await _send_network_reload(bts_ssh, label="BTS", case_id=case_id)


async def run_soft_reboot_regression(
    *,
    gui_page,
    gui_browser,
    root_ssh: AsyncGenericDriver,
    bsu_ip: str,
    cpe_ips: list[str],
    device_creds: dict[str, str],
    profile_bundle,
    recovery_manager: RecoveryManager,
    iterations: int,
    case_id: str = "REG_01",
) -> None:
    _log(case_id, f"Starting {iterations} soft reboot cycle(s).")
    reg = _regression_cfg(profile_bundle)
    use_gui_reboot = bool(reg.get("reboot_via_gui", False))
    wait_s = _reboot_wait_s(profile_bundle)
    await _ensure_ssh_open(root_ssh)

    await verify_iteration_health(
        case_id=case_id,
        phase="baseline",
        bts_host=bsu_ip,
        cpe_hosts=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        gui_browser=gui_browser,
    )

    for cycle in range(1, iterations + 1):
        _log(case_id, f"Cycle {cycle}/{iterations}: triggering soft reboot.")
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.MEDIUM_WAIT_MS, skip_recovery=True)
        await _trigger_soft_reboot(gui_page, root_ssh, use_gui=use_gui_reboot)
        _log(case_id, f"Cycle {cycle}/{iterations}: waiting {wait_s}s for boot.")
        await asyncio.sleep(wait_s)
        try:
            await root_ssh.close()
        except Exception:
            pass
        await recovery_manager.ensure_link_or_recover(
            gui_page=gui_page,
            bsu_ip=bsu_ip,
            device_creds=device_creds,
            root_ssh=root_ssh,
        )
        await _ensure_ssh_open(root_ssh)
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS, skip_recovery=True)
        await verify_iteration_health(
            case_id=case_id,
            phase=f"cycle-{cycle}",
            bts_host=bsu_ip,
            cpe_hosts=cpe_ips,
            device_creds=device_creds,
            profile_bundle=profile_bundle,
            gui_browser=gui_browser,
        )
    _log(case_id, "All soft reboot cycles completed.")


async def run_soft_reset_regression(
    *,
    gui_page,
    gui_browser,
    root_ssh: AsyncGenericDriver,
    bsu_ip: str,
    cpe_ips: list[str],
    device_creds: dict[str, str],
    profile_bundle,
    recovery_manager: RecoveryManager,
    iterations: int,
    case_id: str = "REG_02",
) -> None:
    """N-cycle network interface soft reset (BTS + CPE) via /etc/init.d/network reload."""
    _log(case_id, f"Starting {iterations} network soft-reset cycle(s) ({NETWORK_RELOAD_CMD}).")
    settle_s = _network_reload_wait_s(profile_bundle)
    await _ensure_ssh_open(root_ssh)

    await verify_iteration_health(
        case_id=case_id,
        phase="baseline",
        bts_host=bsu_ip,
        cpe_hosts=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        gui_browser=gui_browser,
    )

    for cycle in range(1, iterations + 1):
        _log(case_id, f"Cycle {cycle}/{iterations}: network reload on BTS and CPE.")
        await _ensure_ssh_open(root_ssh)
        try:
            await _trigger_network_soft_reset(
                bts_ssh=root_ssh,
                cpe_hosts=cpe_ips,
                device_creds=device_creds,
                case_id=case_id,
            )
        except Exception as exc:
            _log(case_id, f"Network reload command error ({exc}); reopening BTS SSH.")
            try:
                await root_ssh.close()
            except Exception:
                pass
            await root_ssh.open()
            raise

        _log(case_id, f"Cycle {cycle}/{iterations}: waiting {settle_s}s for interfaces to settle.")
        await asyncio.sleep(settle_s)

        try:
            await root_ssh.send_command("echo ok")
        except Exception:
            _log(case_id, "BTS SSH stale after network reload; reopening session.")
            try:
                await root_ssh.close()
            except Exception:
                pass
            await root_ssh.open()

        await recovery_manager.ensure_link_or_recover(
            gui_page=gui_page,
            bsu_ip=bsu_ip,
            device_creds=device_creds,
            root_ssh=root_ssh,
        )
        await _ensure_ssh_open(root_ssh)
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS, skip_recovery=True)
        await verify_iteration_health(
            case_id=case_id,
            phase=f"cycle-{cycle}",
            bts_host=bsu_ip,
            cpe_hosts=cpe_ips,
            device_creds=device_creds,
            profile_bundle=profile_bundle,
            gui_browser=gui_browser,
        )
    _log(case_id, "All network soft-reset cycles completed.")


async def run_firmware_upgrade_regression(
    *,
    gui_page,
    gui_browser,
    root_ssh: AsyncGenericDriver,
    bsu_ip: str,
    cpe_ips: list[str],
    device_creds: dict[str, str],
    profile_bundle,
    recovery_manager: RecoveryManager,
    iterations: int,
    firmware_image: str,
    case_id: str = "REG_03",
) -> None:
    image_path = Path(firmware_image).expanduser().resolve()
    if not image_path.is_file():
        pytest.fail(f"{case_id}: firmware image not found: {image_path}")

    _log(case_id, f"Starting {iterations} firmware upgrade cycle(s) using {image_path}.")
    wait_s = _reboot_wait_s(profile_bundle)

    await verify_iteration_health(
        case_id=case_id,
        phase="baseline",
        bts_host=bsu_ip,
        cpe_hosts=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        gui_browser=gui_browser,
    )

    for cycle in range(1, iterations + 1):
        _log(case_id, f"Cycle {cycle}/{iterations}: uploading firmware and flashing.")
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.MEDIUM_WAIT_MS, skip_recovery=True)
        await _navigate_to_flashops(gui_page)
        await _open_upgrade_tab(gui_page)

        file_input = gui_page.locator("input[type='file']").first
        await file_input.wait_for(state="visible", timeout=15000)
        await file_input.set_input_files(str(image_path))

        async def _accept_dialog(dialog):
            await dialog.accept()

        gui_page.once("dialog", _accept_dialog)
        flash_btn = gui_page.locator(
            "input[value*='Flash' i]:visible, "
            "input[value*='Upgrade' i]:visible, "
            "button:has-text('Flash'):visible, "
            "button:has-text('Upgrade'):visible"
        ).first
        await flash_btn.wait_for(state="visible", timeout=15000)
        await flash_btn.click()

        _log(case_id, f"Cycle {cycle}/{iterations}: waiting {wait_s}s for upgrade reboot.")
        await asyncio.sleep(wait_s)
        try:
            await root_ssh.close()
        except Exception:
            pass
        await recovery_manager.ensure_link_or_recover(
            gui_page=gui_page,
            bsu_ip=bsu_ip,
            device_creds=device_creds,
            root_ssh=root_ssh,
        )
        await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.LONG_WAIT_MS, skip_recovery=True)
        await verify_iteration_health(
            case_id=case_id,
            phase=f"cycle-{cycle}",
            bts_host=bsu_ip,
            cpe_hosts=cpe_ips,
            device_creds=device_creds,
            profile_bundle=profile_bundle,
            gui_browser=gui_browser,
        )
    _log(case_id, "All firmware upgrade cycles completed.")
