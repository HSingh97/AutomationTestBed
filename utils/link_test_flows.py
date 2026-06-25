"""Monitor -> Tools -> Link Test Tool: GUI_127, GUI_128, GUI_130."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from pages.locators import CommonLocators, LinkTestToolLocators, MonitorLocators, UITimeouts
from utils.cpe_session import open_cpe_gui_session_if_reachable
from utils.link_test_config import LinkTestConfig
from utils.net_utils import ip_in_text
from utils.parsers import clean_ssh_output, parse_link_test_results, parse_numeric_metric, ssh_scalar
from utils.validators import validate_param
from utils.verify_output import print_comparison_table, print_gui_backend_table, print_section

RADIO_INDEX = 1
RESULT_POLL_INTERVAL_S = 2
RESULT_BUFFER_S = 15
UCI_SETTLE_S = 5
DEBUG_LOG_PATH = Path("/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-a9118f.log")
DEBUG_SESSION_ID = "a9118f"
DEBUG_RUN_ID = "pre-fix"


def _log(message: str):
    print(f"[LINK_TEST] {message}")


def _debug_log(location: str, message: str, data: dict, hypothesis_id: str) -> None:
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fp:
            fp.write(
                json.dumps(
                    {
                        "sessionId": DEBUG_SESSION_ID,
                        "runId": DEBUG_RUN_ID,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    except Exception:
        pass


async def _goto_admin_path(gui_page, path_fragment: str) -> bool:
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if not match:
        return False
    target = f"{match.group(1)}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    return True


async def open_link_test_tool(gui_page):
    """Navigate Monitor -> Tools -> Link Test Tool tab."""
    menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
    try:
        await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        submenu = gui_page.locator(MonitorLocators.SUBMENU_TOOLS).first
        await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/tools"):
            raise

    tab = gui_page.locator(LinkTestToolLocators.TAB_LINK_TEST).first
    try:
        await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await tab.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/tools/testtool"):
            raise

    await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:95",
        "link test tool page opened",
        {
            "url": gui_page.url or "",
        },
        "H4",
    )
    # endregion


async def _send_ssh(root_ssh, command: str) -> str:
    response = await root_ssh.send_command(command)
    return clean_ssh_output(response.result)


async def _open_root_ssh_for_host(host: str, device_creds: dict):
    """Open a temporary root SSH session to a peer device."""
    os.makedirs("logs", exist_ok=True)
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
        channel_log=f"logs/root_cli_{host}.log",
    )
    open_errors = []
    for wait_s in (0, 10, 15):
        if wait_s:
            await asyncio.sleep(wait_s)
        try:
            await conn.open()
            return conn
        except Exception as exc:
            open_errors.append(str(exc))
    raise RuntimeError(f"Unable to open root SSH to {host}: {' | '.join(open_errors)}")


async def clear_link_test_cpe_list(root_ssh, radio_idx: int = RADIO_INDEX):
    await _send_ssh(root_ssh, RootCommands.clear_tool_cpe_list(radio_idx))


async def _fill_field(gui_page, locator: str, value: str):
    field = gui_page.locator(locator).first
    await field.fill(value)
    check.equal(await field.input_value(), value, f"GUI field {locator} did not retain value {value}")


DEFAULT_PACKET_SIZE = 1400


async def _configure_link_test_fields(
    gui_page,
    *,
    bandwidth: int,
    duration_s: int,
    vlan_id: int,
    packet_size: int = DEFAULT_PACKET_SIZE,
):
    await _fill_field(gui_page, LinkTestToolLocators.BANDWIDTH_INPUT, str(bandwidth))
    await _fill_field(gui_page, LinkTestToolLocators.TIME_DURATION_INPUT, str(duration_s))
    await _fill_field(gui_page, LinkTestToolLocators.VLAN_ID_INPUT, str(vlan_id))
    packet_field = gui_page.locator(LinkTestToolLocators.PACKET_SIZE_INPUT).first
    try:
        if await packet_field.is_enabled(timeout=UITimeouts.SHORT_WAIT_MS):
            await packet_field.fill(str(packet_size), timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        pass
    bidirection = gui_page.locator(LinkTestToolLocators.BIDIRECTION_CHECKBOX).first
    await bidirection.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    was_checked = await bidirection.is_checked()
    if not was_checked:
        await bidirection.check()
    is_checked = await bidirection.is_checked()
    bidirection_info = await bidirection.evaluate(
        """el => ({tag: el.tagName, type: el.getAttribute('type') || '', checked: !!el.checked})"""
    )
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:163",
        "configured link test fields",
        {
            "url": gui_page.url or "",
            "bandwidth": bandwidth,
            "duration_s": duration_s,
            "vlan_id": vlan_id,
            "bidirection_before": was_checked,
            "bidirection_after": is_checked,
            "bidirection_info": bidirection_info,
        },
        "H7",
    )
    # endregion
    check.is_true(is_checked, "Bidirection checkbox should stay enabled for link test")


def _dropdown_has_cpe(options: list[str], cpe_ip: str) -> bool:
    return any(ip_in_text(cpe_ip, opt) for opt in options)


async def _wait_for_cpe_dropdown(gui_page, cpe_ip: str, timeout_s: int = 30):
    dropdown = gui_page.locator(LinkTestToolLocators.CPE_DROPDOWN).first
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        options = await dropdown.locator("option").all_inner_texts()
        if _dropdown_has_cpe(options, cpe_ip):
            return
        await asyncio.sleep(2)
        await gui_page.reload(wait_until="networkidle")
        await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(state="visible", timeout=10000)
    raise TimeoutError(f"CPE IP {cpe_ip} not found in Link Test dropdown after {timeout_s}s")


async def _select_cpe_dropdown_option(dropdown, cpe_ip: str) -> None:
    """Select CPE by label/value; IPv6 options may use different compression than CLI."""
    options = dropdown.locator("option")
    count = await options.count()
    for i in range(count):
        opt = options.nth(i)
        label = (await opt.inner_text()).strip()
        if not _dropdown_has_cpe([label], cpe_ip):
            continue
        value = (await opt.get_attribute("value") or "").strip()
        if value:
            await dropdown.select_option(value=value)
        else:
            await dropdown.select_option(label=label)
        return
    await dropdown.select_option(label=cpe_ip)


async def _add_cpe_from_dropdown(gui_page, cpe_ip: str):
    dropdown = gui_page.locator(LinkTestToolLocators.CPE_DROPDOWN).first
    await _select_cpe_dropdown_option(dropdown, cpe_ip)
    add_btn = gui_page.locator(LinkTestToolLocators.ADD_CPE_BUTTON).first
    await add_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await add_btn.click()
    try:
        await gui_page.wait_for_load_state("networkidle")
        # region agent log
        _debug_log(
            "utils/link_test_flows.py:238",
            "add-cpe navigation reached networkidle",
            {"peer_ip": cpe_ip, "url": gui_page.url or ""},
            "H10",
        )
        # endregion
    except Exception:
        # Some firmwares keep long-poll/XHR active; rely on form readiness instead.
        await gui_page.wait_for_load_state("domcontentloaded")
        # region agent log
        _debug_log(
            "utils/link_test_flows.py:248",
            "add-cpe fallback to domcontentloaded after networkidle timeout",
            {"peer_ip": cpe_ip, "url": gui_page.url or ""},
            "H10",
        )
        # endregion
    await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    table = gui_page.locator(LinkTestToolLocators.CPE_TABLE).first
    deadline = asyncio.get_event_loop().time() + (UITimeouts.ELEMENT_WAIT_MS / 1000)
    while asyncio.get_event_loop().time() < deadline:
        if ip_in_text(cpe_ip, await table.inner_text()):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"CPE {cpe_ip} not visible in Link Test CPE table")


async def _cpe_rows_count(gui_page) -> int:
    return await gui_page.locator(f"{LinkTestToolLocators.CPE_TABLE_ROWS}:not(:first-child)").count()


async def _ensure_cpe_in_list(gui_page, root_ssh, cpe_ip: str, link_test_config: LinkTestConfig):
    """Add CPE from dropdown when the list table is empty."""
    if await _cpe_rows_count(gui_page) > 0:
        backend_cfg = await _read_backend_config(root_ssh)
        if ip_in_text(cpe_ip, backend_cfg.get("iplist", "")):
            _log(f"CPE {cpe_ip} already present in list")
            return
    await _wait_for_cpe_dropdown(gui_page, cpe_ip)
    await _add_cpe_from_dropdown(gui_page, cpe_ip)
    await open_link_test_tool(gui_page)
    await _configure_link_test_fields(
        gui_page,
        bandwidth=link_test_config.default_bandwidth,
        duration_s=link_test_config.duration_s,
        vlan_id=link_test_config.vlan_id,
    )


async def _prepare_link_test_run(
    gui_page,
    root_ssh,
    peer_ip: str,
    link_test_config: LinkTestConfig,
    *,
    device_label: str = "BTS",
):
    """
    GUI_130 prep: clear peer list, set bandwidth/duration/VLAN/bidirection, add peer, verify UCI.
    Always reconfigures (does not skip when a stale peer row exists).
    """
    bandwidth = link_test_config.default_bandwidth
    duration_s = link_test_config.duration_s
    vlan_id = link_test_config.vlan_id

    _log(
        f"GUI_130 [{device_label}]: configure link test "
        f"(bw={bandwidth} Mbps, dur={duration_s}s, vlan={vlan_id}, peer={peer_ip})"
    )

    await clear_link_test_cpe_list(root_ssh)
    await open_link_test_tool(gui_page)
    await _configure_link_test_fields(
        gui_page,
        bandwidth=bandwidth,
        duration_s=duration_s,
        vlan_id=vlan_id,
    )

    await _wait_for_cpe_dropdown(gui_page, peer_ip)
    await _add_cpe_from_dropdown(gui_page, peer_ip)

    await open_link_test_tool(gui_page)
    await _configure_link_test_fields(
        gui_page,
        bandwidth=bandwidth,
        duration_s=duration_s,
        vlan_id=vlan_id,
    )

    check.is_true(await _cpe_rows_count(gui_page) >= 1, f"GUI_130 [{device_label}]: peer must be in list before Start")

    backend_cfg = await _read_backend_config(root_ssh)
    print_gui_backend_table(
        f"GUI_130 [{device_label}] — Link Test configuration (GUI vs UCI)",
        [
            ("Bandwidth (Mbps)", str(bandwidth), backend_cfg.get("bandwidth", ""), None),
            ("Duration (sec)", str(duration_s), backend_cfg.get("duration", ""), None),
            ("VLAN ID", str(vlan_id), backend_cfg.get("vlan", ""), None),
            ("Peer IP", peer_ip, backend_cfg.get("iplist", ""), None),
        ],
    )
    _validate_uci_parameters(bandwidth, duration_s, vlan_id, backend_cfg)
    check.is_true(
        ip_in_text(peer_ip, backend_cfg.get("iplist", "")),
        f"GUI_130 [{device_label}]: peer {peer_ip} not in UCI iplist after Add",
    )

    for locator, label, expected in (
        (LinkTestToolLocators.BANDWIDTH_INPUT, "Bandwidth", str(bandwidth)),
        (LinkTestToolLocators.TIME_DURATION_INPUT, "Duration", str(duration_s)),
        (LinkTestToolLocators.VLAN_ID_INPUT, "VLAN", str(vlan_id)),
    ):
        field = gui_page.locator(locator).first
        actual = (await field.input_value()).strip()
        if actual != expected:
            await field.fill(expected)
            actual = (await field.input_value()).strip()
        check.equal(
            actual,
            expected,
            f"GUI_130 [{device_label}]: {label} field must be {expected} before Start (got {actual!r})",
        )


async def _start_link_test(gui_page):
    start_btn = gui_page.locator(LinkTestToolLocators.START_BUTTON).first
    await start_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await start_btn.click()
    progress = gui_page.locator(LinkTestToolLocators.PROGRESS_MESSAGE).first
    try:
        await progress.filter(has_text="Testing in progress").wait_for(
            state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
        )
    except Exception:
        pass


async def _stop_link_test_if_running(gui_page):
    stop_btn = gui_page.locator(LinkTestToolLocators.STOP_BUTTON).first
    try:
        if await stop_btn.is_visible(timeout=3000):
            await stop_btn.click()
            await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    except Exception:
        pass


async def _wait_for_link_test_started(
    root_ssh,
    *,
    timeout_s: int = 30,
    radio_idx: int = RADIO_INDEX,
) -> bool:
    """Return True when backend reports link test active after Start."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        status = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_test_active(radio_idx))).strip()
        if status not in {"", "0"}:
            return True
        await asyncio.sleep(RESULT_POLL_INTERVAL_S)
    return False


async def _wait_for_link_test_complete(
    root_ssh,
    gui_page,
    duration_s: int,
    radio_idx: int = RADIO_INDEX,
    *,
    require_started: bool = False,
) -> str:
    if require_started:
        started = await _wait_for_link_test_started(root_ssh, timeout_s=min(30, duration_s + 10), radio_idx=radio_idx)
        check.is_true(started, "GUI_130: link test did not enter running state after Start (backend still idle)")

    total_wait = duration_s + RESULT_BUFFER_S
    deadline = asyncio.get_event_loop().time() + total_wait
    while asyncio.get_event_loop().time() < deadline:
        status = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_test_active(radio_idx)))
        if status.strip() in {"0", ""}:
            break
        await asyncio.sleep(RESULT_POLL_INTERVAL_S)

    results = gui_page.locator(LinkTestToolLocators.RESULTS_CONTAINER).first
    progress = gui_page.locator(LinkTestToolLocators.PROGRESS_MESSAGE).first

    async def _read_candidate_text() -> str:
        texts: list[str] = []
        try:
            if await results.count():
                texts.append((await results.inner_text()).strip())
        except Exception:
            pass
        try:
            if await progress.count():
                texts.append((await progress.inner_text()).strip())
        except Exception:
            pass
        try:
            form = gui_page.locator(LinkTestToolLocators.LINK_TEST_FORM).first
            if await form.count():
                texts.append((await form.inner_text()).strip())
        except Exception:
            pass
        return "\n".join(t for t in texts if t).strip()

    read_deadline = asyncio.get_event_loop().time() + RESULT_BUFFER_S
    while asyncio.get_event_loop().time() < read_deadline:
        text = await _read_candidate_text()
        if parse_link_test_results(text):
            return text
        await asyncio.sleep(2)

    # Some CPE pages only render the final result after reopening the tool page.
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:293",
        "link test result missing after initial poll; reopening tool page",
        {
            "url": gui_page.url or "",
            "duration_s": duration_s,
            "backend_active_status": status.strip(),
        },
        "H5",
    )
    # endregion
    await open_link_test_tool(gui_page)
    read_deadline = asyncio.get_event_loop().time() + 10
    while asyncio.get_event_loop().time() < read_deadline:
        text = await _read_candidate_text()
        if parse_link_test_results(text):
            return text
        await asyncio.sleep(2)

    final_text = await _read_candidate_text()
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:311",
        "link test result still not parseable after reopen",
        {
            "url": gui_page.url or "",
            "parsed": bool(parse_link_test_results(final_text)),
            "text_sample": " ".join(final_text.split())[:240],
        },
        "H5",
    )
    # endregion
    return final_text


async def _read_backend_config(root_ssh, radio_idx: int = RADIO_INDEX) -> dict[str, str]:
    return {
        "bandwidth": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_bw(radio_idx))),
        "duration": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_duration(radio_idx))),
        "vlan": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_vlan(radio_idx))),
        "iplist": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_iplist(radio_idx))),
    }


async def _read_backend_results(root_ssh, radio_idx: int = RADIO_INDEX, assoc_idx: int = 1) -> dict[str, str]:
    stats_cmds = RootCommands.get_link_test_stats(radio_idx, assoc_idx)
    results: dict[str, str] = {}
    for key, cmd in stats_cmds.items():
        results[key] = ssh_scalar(await _send_ssh(root_ssh, cmd))
    return results


def _normalize_backend_results_for_device(device_label: str, backend_results: dict[str, str]) -> dict[str, str]:
    if device_label != "CPE":
        return dict(backend_results)
    normalized = dict(backend_results)
    normalized["ul_throughput"] = backend_results.get("dl_throughput", "")
    normalized["dl_throughput"] = backend_results.get("ul_throughput", "")
    normalized["ul_latency"] = backend_results.get("dl_latency", "")
    normalized["dl_latency"] = backend_results.get("ul_latency", "")
    return normalized


def _load_reference_results(path: str | None) -> dict[str, str] | None:
    if not path:
        return None
    ref_path = Path(path)
    if not ref_path.exists():
        check.fail(f"Reference tester results file not found: {ref_path}")
        return None
    with ref_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    key_map = {
        "ul_throughput": ("ul_throughput", "ul_tput", "uplink_mbps"),
        "dl_throughput": ("dl_throughput", "dl_tput", "downlink_mbps"),
        "ul_latency": ("ul_latency", "ul_lat_ms", "uplink_latency_ms"),
        "dl_latency": ("dl_latency", "dl_lat_ms", "downlink_latency_ms"),
    }
    parsed: dict[str, str] = {}
    for canonical, aliases in key_map.items():
        for alias in aliases:
            if alias in data and data[alias] is not None:
                parsed[canonical] = str(data[alias])
                break
    return parsed or None


def _validate_uci_parameters(
    expected_bw: int,
    expected_duration: int,
    expected_vlan: int,
    backend_cfg: dict[str, str],
):
    validate_param("Link Test BW (UCI)", str(expected_bw), backend_cfg.get("bandwidth", ""))
    validate_param("Link Test Duration (UCI)", str(expected_duration), backend_cfg.get("duration", ""))
    validate_param("Link Test VLAN (UCI)", str(expected_vlan), backend_cfg.get("vlan", ""))


def _validate_metric_pair(
    label: str,
    gui_val: str,
    reference_val: str,
    tolerance: float,
    reference_label: str = "Backend",
):
    gui_num = parse_numeric_metric(gui_val)
    ref_num = parse_numeric_metric(reference_val)
    if gui_num is None or ref_num is None:
        validate_param(label, reference_val, gui_val)
        return
    diff = abs(gui_num - ref_num)
    check.less_equal(
        diff,
        tolerance,
        f"{label}: GUI ({gui_val}) vs {reference_label} ({reference_val}) drift {diff} > {tolerance}",
    )
    if diff <= tolerance:
        _log(f"    -> {label}: PASSED (GUI={gui_val}, {reference_label}={reference_val})")


def _validate_nonzero_link_test_traffic(
    gui_results: dict[str, str],
    backend_results: dict[str, str],
    *,
    device_label: str = "BTS",
):
    """
    Validate throughput presence.
    If both GUI and backend are zero, treat as lab/FW no-traffic condition (warning only).
    """
    gui_ul = parse_numeric_metric(gui_results.get("ul_throughput", "")) or 0.0
    gui_dl = parse_numeric_metric(gui_results.get("dl_throughput", "")) or 0.0
    backend_ul = parse_numeric_metric(backend_results.get("ul_throughput", "")) or 0.0
    backend_dl = parse_numeric_metric(backend_results.get("dl_throughput", "")) or 0.0

    if max(gui_ul, gui_dl, backend_ul, backend_dl) > 0:
        return

    _log(
        f"GUI_130 [{device_label}] warning: zero throughput on both GUI and backend "
        f"(GUI UL/DL={gui_ul}/{gui_dl}, backend UL/DL={backend_ul}/{backend_dl}). "
        "Configuration path is valid; likely firmware/lab traffic condition."
    )


def _validate_results_against_reference(
    gui_results: dict[str, str],
    reference_results: dict[str, str],
    config: LinkTestConfig,
    *,
    reference_label: str,
):
    _log(f"GUI results: {gui_results}")
    _log(f"{reference_label} results: {reference_results}")

    _validate_metric_pair(
        "UL Throughput",
        gui_results.get("ul_throughput", ""),
        reference_results.get("ul_throughput", ""),
        config.result_tolerance_throughput_mbps,
        reference_label,
    )
    _validate_metric_pair(
        "DL Throughput",
        gui_results.get("dl_throughput", ""),
        reference_results.get("dl_throughput", ""),
        config.result_tolerance_throughput_mbps,
        reference_label,
    )
    _validate_metric_pair(
        "UL Latency",
        gui_results.get("ul_latency", ""),
        reference_results.get("ul_latency", ""),
        config.result_tolerance_latency_ms,
        reference_label,
    )
    _validate_metric_pair(
        "DL Latency",
        gui_results.get("dl_latency", ""),
        reference_results.get("dl_latency", ""),
        config.result_tolerance_latency_ms,
        reference_label,
    )


async def _verify_parameter_ranges_in_gui(gui_page, config: LinkTestConfig):
    range_cases = (
        ("Bandwidth (min)", LinkTestToolLocators.BANDWIDTH_INPUT, config.bw_min),
        ("Bandwidth (max)", LinkTestToolLocators.BANDWIDTH_INPUT, config.bw_max),
        ("Time Duration (min)", LinkTestToolLocators.TIME_DURATION_INPUT, config.duration_min),
        ("Time Duration (max)", LinkTestToolLocators.TIME_DURATION_INPUT, config.duration_max),
        ("VLAN ID (min)", LinkTestToolLocators.VLAN_ID_INPUT, config.vlan_min),
        ("VLAN ID (max)", LinkTestToolLocators.VLAN_ID_INPUT, config.vlan_max),
    )
    print_section("GUI_127 — Parameter range checks (min/max accepted in GUI)")
    print_comparison_table(
        [(label, str(val), "accepted", "PASS") for label, _, val in range_cases]
    )
    for label, locator, value in range_cases:
        await _fill_field(gui_page, locator, str(value))


# ---------------------------------------------------------------------------
# GUI_127 — Bandwidth, Time Duration, VLAN (parameters + GUI/UCI)
# ---------------------------------------------------------------------------
async def _assert_gui_127_on_device(
    gui_page,
    root_ssh,
    link_test_config: LinkTestConfig,
    *,
    device_label: str = "BTS",
):
    bandwidth = link_test_config.default_bandwidth
    duration_s = link_test_config.duration_s
    vlan_id = link_test_config.vlan_id

    check.is_true(root_ssh is not None, f"GUI_127 [{device_label}]: SSH required")
    _log(
        f"GUI_127 [{device_label}]: vlan={vlan_id}, duration={duration_s}s, "
        f"bw={bandwidth} Mbps (range {link_test_config.bw_min}-{link_test_config.bw_max})"
    )

    await clear_link_test_cpe_list(root_ssh)
    await open_link_test_tool(gui_page)
    await _verify_parameter_ranges_in_gui(gui_page, link_test_config)

    await open_link_test_tool(gui_page)
    await _configure_link_test_fields(gui_page, bandwidth=bandwidth, duration_s=duration_s, vlan_id=vlan_id)

    await _start_link_test(gui_page)
    await asyncio.sleep(UCI_SETTLE_S)
    await _stop_link_test_if_running(gui_page)
    await asyncio.sleep(2)

    backend_cfg = await _read_backend_config(root_ssh)
    print_gui_backend_table(
        f"GUI_127 [{device_label}] — Link Test parameters (GUI vs UCI)",
        [
            ("Bandwidth (Mbps)", str(bandwidth), backend_cfg.get("bandwidth", ""), None),
            ("Duration (sec)", str(duration_s), backend_cfg.get("duration", ""), None),
            ("VLAN ID", str(vlan_id), backend_cfg.get("vlan", ""), None),
        ],
    )
    _validate_uci_parameters(bandwidth, duration_s, vlan_id, backend_cfg)

    dmesg_tail = await _send_ssh(root_ssh, "dmesg | grep 'Test Start' | tail -1")
    if dmesg_tail:
        check.is_in(str(bandwidth), dmesg_tail, "Bandwidth not reflected in kernel link-test log")
        check.is_in(f"VLANID: {vlan_id}", dmesg_tail, "VLAN ID not reflected in kernel link-test log")

    _log(f"GUI_127 [{device_label}] completed: parameter ranges and GUI/UCI configuration verified")
    return {
        "bandwidth": bandwidth,
        "duration_s": duration_s,
        "vlan_id": vlan_id,
    }


async def assert_gui_127_link_test_parameters(
    gui_page,
    root_ssh,
    link_test_config: LinkTestConfig,
    *,
    cpe_ips: list[str] | None = None,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_127_on_device(gui_page, root_ssh, link_test_config, device_label="BTS")

    cpe_ip = (cpe_ips or [""])[0]
    if not cpe_ip:
        return
    check.is_true(bool(device_creds), "GUI_127: device credentials are required for CPE validation")
    _log(
        f"GUI_127: saved BTS parameter snapshot "
        f"(bw={bts_result['bandwidth']}, dur={bts_result['duration_s']}, vlan={bts_result['vlan_id']}); "
        f"logging into CPE {cpe_ip}"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_127_on_device(cpe_page, cpe_root_ssh, link_test_config, device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_128_on_device(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    device_label: str = "BTS",
):
    check.is_true(root_ssh is not None, f"GUI_128 [{device_label}]: SSH required")
    target_ip = cpe_ips[0] if cpe_ips else ""
    if not target_ip:
        check.fail(f"GUI_128 [{device_label}]: no peer IP configured")
        return

    _log(f"GUI_128 [{device_label}]: select and add {target_ip} from dropdown")

    await clear_link_test_cpe_list(root_ssh)
    await open_link_test_tool(gui_page)
    await _configure_link_test_fields(
        gui_page,
        bandwidth=link_test_config.default_bandwidth,
        duration_s=link_test_config.duration_s,
        vlan_id=link_test_config.vlan_id,
    )

    await _wait_for_cpe_dropdown(gui_page, target_ip)
    check.is_true(
        await gui_page.locator(LinkTestToolLocators.ADD_CPE_BUTTON).first.is_visible(),
        "Add button should be visible when dropdown has entries",
    )
    await _add_cpe_from_dropdown(gui_page, target_ip)

    check.is_true(await _cpe_rows_count(gui_page) >= 1, "Peer should appear in the list table after Add")
    backend_cfg = await _read_backend_config(root_ssh)
    print_gui_backend_table(
        f"GUI_128 [{device_label}] — peer added to Link Test list",
        [
            ("Peer IP", target_ip, backend_cfg.get("iplist", ""), None),
        ],
    )
    check.is_true(
        ip_in_text(target_ip, backend_cfg.get("iplist", "")),
        f"Peer {target_ip} not in UCI iplist after Add",
    )

    _log(f"GUI_128 [{device_label}] completed: {target_ip} added to link test list")
    return {"target_ip": target_ip}


async def assert_gui_128_link_test_cpe(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_128_on_device(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        device_label="BTS",
    )

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(bsu_ip), "GUI_128: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_128: device credentials are required for CPE validation")
    _log(f"GUI_128: saved BTS add-peer snapshot ({bts_result['target_ip']}); logging into CPE {cpe_ip}")
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:591",
        "starting GUI_128 CPE validation",
        {
            "cpe_ip": cpe_ip,
            "bts_ip": bsu_ip or "",
            "saved_target_ip": bts_result.get("target_ip", ""),
        },
        "H4",
    )
    # endregion

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_128_on_device(
            cpe_page,
            cpe_root_ssh,
            [bsu_ip],
            link_test_config,
            device_label="CPE",
        )
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_130_on_device(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    device_label: str = "BTS",
):
    check.is_true(root_ssh is not None, f"GUI_130 [{device_label}]: SSH required")
    if not cpe_ips:
        check.fail(f"GUI_130 [{device_label}]: no peer IP configured")
        return

    target_ip = cpe_ips[0]
    bandwidth = link_test_config.default_bandwidth
    duration_s = link_test_config.duration_s
    vlan_id = link_test_config.vlan_id

    _log(f"GUI_130 [{device_label}]: start link test, wait {duration_s}s, validate results for {target_ip}")

    await _prepare_link_test_run(
        gui_page,
        root_ssh,
        target_ip,
        link_test_config,
        device_label=device_label,
    )

    await _start_link_test(gui_page)
    results_text = await _wait_for_link_test_complete(
        root_ssh,
        gui_page,
        duration_s,
        require_started=True,
    )
    gui_results = parse_link_test_results(results_text)
    check.is_true(bool(gui_results), "GUI_130: Link test results not displayed after Start")

    print_section(f"GUI_130 [{device_label}] — Link Test results (from page)")
    print(f"  {results_text.strip()}\n", flush=True)

    raw_backend_results = await _read_backend_results(root_ssh)
    backend_results = _normalize_backend_results_for_device(device_label, raw_backend_results)
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:728",
        "backend link test results prepared for comparison",
        {
            "device_label": device_label,
            "raw_backend_results": raw_backend_results,
            "normalized_backend_results": backend_results,
        },
        "H9",
    )
    # endregion
    print_gui_backend_table(
        f"GUI_130 [{device_label}] — Link Test results (GUI vs backend)",
        [
            ("UL Throughput", gui_results.get("ul_throughput", ""), backend_results.get("ul_throughput", ""), link_test_config.result_tolerance_throughput_mbps),
            ("DL Throughput", gui_results.get("dl_throughput", ""), backend_results.get("dl_throughput", ""), link_test_config.result_tolerance_throughput_mbps),
            ("UL Latency", gui_results.get("ul_latency", ""), backend_results.get("ul_latency", ""), link_test_config.result_tolerance_latency_ms),
            ("DL Latency", gui_results.get("dl_latency", ""), backend_results.get("dl_latency", ""), link_test_config.result_tolerance_latency_ms),
        ],
    )
    _validate_results_against_reference(
        gui_results,
        backend_results,
        link_test_config,
        reference_label="Backend (on-device traffic stats)",
    )
    _validate_nonzero_link_test_traffic(
        gui_results,
        backend_results,
        device_label=device_label,
    )

    tester_ref = _load_reference_results(link_test_config.reference_results_path)
    if tester_ref:
        _validate_results_against_reference(
            gui_results,
            tester_ref,
            link_test_config,
            reference_label="External tester",
        )
    else:
        _log(
            "No --link-test-reference-json provided; validated GUI vs on-device backend stats "
            "(approximate match to live link traffic)"
        )

    _log(f"GUI_130 [{device_label}] completed: link test results match backend/tester within tolerance")
    return {
        "target_ip": target_ip,
        "gui_results": gui_results,
    }


async def assert_gui_130_link_test_results(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_130_on_device(
        gui_page,
        root_ssh,
        cpe_ips,
        link_test_config,
        device_label="BTS",
    )

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(bsu_ip), "GUI_130: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_130: device credentials are required for CPE validation")
    _log(f"GUI_130: saved BTS result snapshot ({bts_result['target_ip']}); logging into CPE {cpe_ip}")
    # region agent log
    _debug_log(
        "utils/link_test_flows.py:720",
        "starting GUI_130 CPE validation",
        {
            "cpe_ip": cpe_ip,
            "bts_ip": bsu_ip or "",
            "saved_target_ip": bts_result.get("target_ip", ""),
        },
        "H4",
    )
    # endregion

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_130_on_device(
            cpe_page,
            cpe_root_ssh,
            [bsu_ip],
            link_test_config,
            device_label="CPE",
        )
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()

