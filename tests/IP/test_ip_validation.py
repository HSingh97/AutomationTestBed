"""IP_01–IP_37 networking validation (BTS and CPE)."""

from __future__ import annotations

import pytest

from config.ip_test_cases import IP_TEST_CASES, IpTestCase, device_targets
from utils.ip_test_flows import IpTestContext, _ip_cfg, execute_ip_case, open_ssh_with_fallback
from utils.regression_flows import _close_ssh

pytestmark = [pytest.mark.IP, pytest.mark.asyncio(loop_scope="session")]


def pytest_generate_tests(metafunc):
    if "ip_case_bundle" not in metafunc.fixturenames:
        return
    params = []
    for case in IP_TEST_CASES:
        for target in device_targets(case):
            params.append(
                pytest.param(
                    (case, target),
                    id=f"{case.case_id}-{target.upper()}",
                )
            )
    metafunc.parametrize("ip_case_bundle", params)


def _require_ip_suite(request, profile_bundle):
    if not request.config.getoption("--allow-ip-suite"):
        pytest.skip("IP suite skipped. Re-run with --allow-ip-suite")
    ip_cfg = profile_bundle.active.get("ip_tests", {}) or {}
    if not ip_cfg.get("enabled", False):
        pytest.skip("ip_tests.enabled is false in profile")


@pytest.fixture
def gui_for_ip(request, ip_case_bundle):
    """Load BTS LuCI session only for GUI IP cases (avoids browser setup on ping-only tests)."""
    case, target = ip_case_bundle
    if "gui" not in case.requires or target != "bts":
        return None
    return request.getfixturevalue("gui_page")


@pytest.fixture
async def ip_ssh_session(request, ip_case_bundle, bsu_ip, cpe_ips, device_creds, profile_bundle):
    """SSH to BTS or CPE; retry configured fallback management IP if primary is down."""
    _case, target = ip_case_bundle
    primary = bsu_ip if target == "bts" else (cpe_ips[0] if cpe_ips else "")
    if not primary:
        pytest.skip(f"no host for target {target}")
    cfg = _ip_cfg(profile_bundle.active)
    dut = profile_bundle.active.get("dut", {}) or {}
    tb = profile_bundle.active.get("testbed", {}) or {}
    cfg["_strict_ipv6"] = bool(dut.get("strict_ipv6") or tb.get("strict_ipv6"))
    cfg["_cli_fallback_ip"] = None if cfg["_strict_ipv6"] else request.config.getoption("--fallback-ip")
    cfg["_device_target"] = target
    try:
        ssh, effective_host, fallbacks = await open_ssh_with_fallback(
            primary,
            device_creds["pass"],
            cfg,
            attempts=int(cfg.get("ssh_connect_attempts", 3)),
            retry_interval_s=int(cfg.get("ssh_connect_retry_interval_s", 15)),
        )
    except ConnectionError as exc:
        pytest.fail(str(exc))
    yield ssh, effective_host, target, fallbacks
    await _close_ssh(ssh)


async def test_ip_case(
    request,
    ip_case_bundle,
    ip_ssh_session,
    gui_for_ip,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    _require_ip_suite(request, profile_bundle)
    case, target = ip_case_bundle
    ssh, host, _, fallbacks = ip_ssh_session
    cfg = _ip_cfg(profile_bundle.active)
    dut = profile_bundle.active.get("dut", {}) or {}
    tb = profile_bundle.active.get("testbed", {}) or {}
    cfg["_strict_ipv6"] = bool(dut.get("strict_ipv6") or tb.get("strict_ipv6"))
    cfg["_password"] = device_creds["pass"]
    cfg["_cli_fallback_ip"] = None if cfg["_strict_ipv6"] else request.config.getoption("--fallback-ip")
    cfg["_device_target"] = target

    if "gui" in case.requires and target != "bts":
        pytest.skip(f"{case.case_id}: GUI flows run on BTS LuCI session only")

    if "destructive" in case.requires and not request.config.getoption("--allow-ip-destructive"):
        pytest.skip(f"{case.case_id}: add --allow-ip-destructive for reboot/reset/flap cases")

    peer = cpe_ips[0] if target == "bts" and cpe_ips else bsu_ip
    dut = profile_bundle.active.get("dut", {})
    stack_mode = str(dut.get("ip_mode", "ipv4"))

    ctx = IpTestContext(
        case=case,
        device_target=target,
        host=host,
        peer_host=peer,
        ssh=ssh,
        gui_page=gui_for_ip if target == "bts" else None,
        cfg=cfg,
        stack_mode=stack_mode,
        fallback_hosts=fallbacks,
    )
    request.node.add_marker(getattr(pytest.mark, case.case_id))
    await execute_ip_case(ctx)
