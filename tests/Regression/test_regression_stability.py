"""Stability regression: repeated reboot/reset/firmware upgrade with BTS<->CPE health checks."""

import pytest

from utils.regression_flows import (
    run_firmware_upgrade_regression,
    run_soft_reboot_regression,
    run_soft_reset_regression,
)
from utils.regression_device_info import collect_testbed_summary
from utils.regression_report import get_regression_collector

pytestmark = [pytest.mark.sanity, pytest.mark.Regression]


def _require_regression(request):
    if not request.config.getoption("--allow-regression"):
        pytest.skip(
            "Regression tests skipped. Re-run with --allow-regression "
            "(reserved bench; reboot/reset/firmware cycles)."
        )


def _iterations(request, profile_bundle):
    cli_value = request.config.getoption("--regression-iterations")
    if cli_value is not None:
        return int(cli_value)
    reg = profile_bundle.active.get("regression", {})
    return int(reg.get("iterations", 3))


async def _prime_collector(request, bsu_ip, cpe_ips, profile_bundle, device_creds):
    collector = get_regression_collector()
    summary = getattr(request.config, "_regression_testbed_summary", None)
    if summary is None:
        summary = await collect_testbed_summary(bsu_ip, cpe_ips, device_creds["pass"])
        request.config._regression_testbed_summary = summary
    collector.set_meta(
        executed_at=request.config._regression_report_ts,
        bts_host=bsu_ip,
        cpe_hosts=cpe_ips,
        iterations=_iterations(request, profile_bundle),
        testbed_summary=summary,
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.REG_01
@pytest.mark.Regression
async def test_reg_01_soft_reboot_cycles(
    request,
    root_ssh,
    gui_page,
    gui_browser,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    recovery_manager,
):
    _require_regression(request)
    await _prime_collector(request, bsu_ip, cpe_ips, profile_bundle, device_creds)
    await run_soft_reboot_regression(
        gui_page=gui_page,
        gui_browser=gui_browser,
        root_ssh=root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        recovery_manager=recovery_manager,
        iterations=_iterations(request, profile_bundle),
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.REG_02
@pytest.mark.Regression
async def test_reg_02_network_soft_reset_cycles(
    request,
    root_ssh,
    gui_page,
    gui_browser,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    recovery_manager,
):
    _require_regression(request)
    await _prime_collector(request, bsu_ip, cpe_ips, profile_bundle, device_creds)
    await run_soft_reset_regression(
        gui_page=gui_page,
        gui_browser=gui_browser,
        root_ssh=root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        recovery_manager=recovery_manager,
        iterations=_iterations(request, profile_bundle),
    )


@pytest.mark.asyncio(scope="session")
@pytest.mark.REG_03
@pytest.mark.Regression
async def test_reg_03_firmware_upgrade_cycles(
    request,
    root_ssh,
    gui_page,
    gui_browser,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    recovery_manager,
):
    _require_regression(request)
    await _prime_collector(request, bsu_ip, cpe_ips, profile_bundle, device_creds)
    firmware_image = request.config.getoption("--firmware-image") or profile_bundle.active.get(
        "regression", {}
    ).get("firmware_image", "")
    if not str(firmware_image).strip():
        pytest.skip("REG_03 skipped. Provide --firmware-image or regression.firmware_image in profile.")
    await run_firmware_upgrade_regression(
        gui_page=gui_page,
        gui_browser=gui_browser,
        root_ssh=root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        recovery_manager=recovery_manager,
        iterations=_iterations(request, profile_bundle),
        firmware_image=str(firmware_image).strip(),
    )
