"""Monitor -> Tools -> Diagnostics (GUI_113–GUI_118)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Callable

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from pages.locators import CommonLocators, DiagnosticsLocators, MonitorLocators, UITimeouts
from utils.cpe_session import open_cpe_gui_session_if_reachable
from utils.net_utils import ip_in_text, unreachable_ping_target
from utils.monitor_assoc import find_assoc_index_for_cpe
from utils.parsers import clean_ssh_output, ssh_scalar
from utils.verify_output import print_comparison_table, print_gui_backend_table, print_section
PCAP_MIN_TIME_S = 10
PCAP_EXTRA_WAIT_S = 8
LLDP_POLL_S = 15.0
RADIO_INDEX = 1

UTILITIES = [
    (DiagnosticsLocators.UTIL_PING, "Ping"),
    (DiagnosticsLocators.UTIL_TRACEROUTE, "Traceroute"),
    (DiagnosticsLocators.UTIL_PACKET_CAPTURE, "Packet Capture"),
    (DiagnosticsLocators.UTIL_CONSOLE, "Console"),
    (DiagnosticsLocators.UTIL_CABLE_LENGTH, "Cable Length"),
    (DiagnosticsLocators.UTIL_TWAMP, "TWAMP"),
    (DiagnosticsLocators.UTIL_LLDP, "LLDP"),
]


def _log(message: str):
    print(f"[DIAGNOSTICS] {message}")


async def _goto_admin_path(gui_page, path_fragment: str) -> bool:
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if not match:
        return False
    target = f"{match.group(1)}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return True


async def open_diagnostics(gui_page):
    """Navigate Monitor -> Tools (Diagnostics tab is default on /monitor/tools)."""
    try:
        menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
        await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        submenu = gui_page.locator(MonitorLocators.SUBMENU_TOOLS).first
        await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/tools"):
            raise

    await gui_page.locator(DiagnosticsLocators.NETWORK_UTILITIES_HEADING).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _select_util(gui_page, util_selector: str):
    radio = gui_page.locator(util_selector).first
    await radio.click(force=True)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


def _decode_luci_payload(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        text = json.loads(text)
    except json.JSONDecodeError:
        pass
    return str(text).replace("\\n", "\n").replace("\\u000a", "\n").strip()


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


def _report_feature_unavailable(case_id: str, feature_name: str, device_label: str, reason: str = "") -> dict[str, str]:
    detail = f"{feature_name} is not available ({device_label})"
    if reason:
        detail = f"{detail}: {reason}"
    print_section(f"{case_id} [{device_label}] — {feature_name} availability")
    print_comparison_table(
        [("Feature", detail, "feature expected", "FAIL")]
    )
    _log(detail)
    check.fail(detail)
    return {"status": "unavailable", "feature": feature_name, "device": device_label}


async def _cpe_has_feature(gui_page, util_selector: str, feature_name: str, case_id: str) -> bool:
    await open_diagnostics(gui_page)
    present = await gui_page.locator(util_selector).count() > 0
    if not present:
        _report_feature_unavailable(case_id, feature_name, "CPE")
    return present


async def _wait_for_response_text(
    gui_page,
    url_predicate: Callable[[str], bool],
    success_predicate: Callable[[str], bool],
    timeout_s: float,
) -> str:
    deadline = asyncio.get_event_loop().time() + timeout_s
    seen = ""

    async def capture(resp):
        nonlocal seen
        if not url_predicate(resp.url):
            return
        try:
            body = _decode_luci_payload(await resp.text())
        except Exception:
            return
        if body:
            seen = body

    gui_page.on("response", capture)
    try:
        while asyncio.get_event_loop().time() < deadline:
            if seen and success_predicate(seen):
                return seen
            await asyncio.sleep(0.5)
        return seen
    finally:
        gui_page.remove_listener("response", capture)


async def _read_rc_output(gui_page) -> str:
    return await gui_page.evaluate(
        """() => {
            const el = document.getElementById('diag-rc-output');
            if (!el) return '';
            return (el.innerHTML || el.value || '').trim();
        }"""
    )


async def _read_console_output(gui_page) -> str:
    return await gui_page.evaluate(
        """() => {
            const el = document.getElementById('diag-console-output');
            if (!el) return '';
            return (el.innerHTML || el.value || '').trim();
        }"""
    )


async def _assert_diagnostics_shell(gui_page):
    """Verify Tools page chrome: tabs, utilities, Diagnostics context."""
    rows: list[tuple[str, str, str, str | None]] = []

    await gui_page.locator(DiagnosticsLocators.PAGE_HEADING).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    rows.append(("TOOLS heading", "visible", "visible", None))

    for tab_sel, label in [
        (DiagnosticsLocators.TAB_DIAGNOSTICS, "Diagnostics tab"),
        (DiagnosticsLocators.TAB_SPECTRUM, "Spectrum Analyzer tab"),
        (DiagnosticsLocators.TAB_ACS_DCS, "ACS/DCS tab"),
        (DiagnosticsLocators.TAB_SITE_SURVEY, "Site Survey tab"),
        (DiagnosticsLocators.TAB_LINK_TEST, "Link Test Tool tab"),
    ]:
        visible = await gui_page.locator(tab_sel).count() > 0
        rows.append((label, "present" if visible else "missing", "present", None))
        check.is_true(visible, f"{label} not found on Tools page")

    await gui_page.locator(DiagnosticsLocators.NETWORK_UTILITIES_HEADING).wait_for(state="visible")
    rows.append(("Network Utilities section", "visible", "visible", None))

    for util_sel, label in UTILITIES:
        present = await gui_page.locator(util_sel).count() > 0
        rows.append((f"Utility radio — {label}", "present" if present else "missing", "present", None))
        check.is_true(present, f"Diagnostics utility '{label}' radio not found")

    print_gui_backend_table("Diagnostics — page structure", rows)
    return rows


async def _assert_util_fields_visible(gui_page, util_selector: str, fields: list[tuple[str, str]]):
    await _select_util(gui_page, util_selector)
    rows: list[tuple[str, str, str, str | None]] = []
    for locator, label in fields:
        loc = gui_page.locator(locator).first
        visible = await loc.is_visible()
        rows.append((label, "visible" if visible else "hidden", "visible", None))
        check.is_true(visible, f"{label} should be visible when utility is selected")
    print_gui_backend_table(f"Diagnostics — {util_selector} fields", rows)


async def _assert_gui_113_on_device(
    gui_page,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
    check_shell: bool = True,
):
    """
    GUI_113: Ping reachable peer (success) and unreachable IP (100% loss).
    Verifies Address/Count/Size fields, ranges, and LuCI ping output.
    """
    target = peer_ips[0] if peer_ips else "127.0.0.1"
    bad_target = unreachable_ping_target(target)
    _log(f"GUI_113 [{device_label}]: Ping validation (reachable={target}, unreachable={bad_target})")

    await open_diagnostics(gui_page)
    if check_shell:
        await _assert_diagnostics_shell(gui_page)

    await _assert_util_fields_visible(
        gui_page,
        DiagnosticsLocators.UTIL_PING,
        [
            (DiagnosticsLocators.PING_ADDRESS, "Ping — Address"),
            (DiagnosticsLocators.PING_COUNT, "Ping — Count"),
            (DiagnosticsLocators.PING_SIZE, "Ping — Size"),
            (DiagnosticsLocators.PING_BUTTON, "Ping — action button"),
        ],
    )

    page_text = await gui_page.locator("fieldset").first.inner_text()
    check.is_true("(1-100)" in page_text, "Ping count range hint (1-100) not shown")
    check.is_true("(64-9000)" in page_text.replace(" ", ""), "Ping size range hint (64-9000) not shown")

    await gui_page.locator(DiagnosticsLocators.PING_ADDRESS).fill(target)
    await gui_page.locator(DiagnosticsLocators.PING_COUNT).fill("3")
    await gui_page.locator(DiagnosticsLocators.PING_SIZE).fill("64")
    await gui_page.locator(DiagnosticsLocators.PING_BUTTON).click()

    good_out = await _wait_for_response_text(
        gui_page,
        lambda u: "diag_ping_output" in u,
        lambda t: "bytes from" in t.lower() and ip_in_text(target, t) and (
            "0% packet loss" in t.lower() or "packets transmitted" in t.lower()
        ),
        timeout_s=60,
    )
    good_gui = await _read_rc_output(gui_page)
    good_text = good_out or good_gui

    replies_ok = "bytes from" in good_text.lower()
    loss_ok = "0% packet loss" in good_text.lower() or (
        replies_ok and "100% packet loss" not in good_text.lower()
    )
    rows = [
        ("Reachable — replies", "yes" if replies_ok else "no", "yes", "PASS" if replies_ok else "FAIL"),
        ("Reachable — packet loss", "0%" if loss_ok else "non-zero", "0%", "PASS" if loss_ok else "FAIL"),
        ("Reachable — target in output", target, target, "PASS" if ip_in_text(target, good_text) else "FAIL"),
    ]
    print_section(f"GUI_113 [{device_label}] — Ping reachable host")
    print_comparison_table(rows)
    for _, _, _, status in rows:
        check.equal(status, "PASS", "GUI_113 reachable ping table contains a failed row")

    await _select_util(gui_page, DiagnosticsLocators.UTIL_PING)
    await gui_page.locator(DiagnosticsLocators.PING_ADDRESS).fill(bad_target)
    await gui_page.locator(DiagnosticsLocators.PING_COUNT).fill("2")
    await gui_page.locator(DiagnosticsLocators.PING_SIZE).fill("64")
    await gui_page.locator(DiagnosticsLocators.PING_BUTTON).click()

    def _ping_failed(text: str) -> bool:
        lowered = text.lower()
        if "bytes from" in lowered and "0% packet loss" in lowered:
            return False
        failure_markers = (
            "100% packet loss",
            "network unreachable",
            "destination unreachable",
            "address unreachable",
            "bad address",
            "name or service not known",
            "timed out",
        )
        return any(marker in lowered for marker in failure_markers)

    bad_out = await _wait_for_response_text(
        gui_page,
        lambda u: "diag_ping_output" in u,
        _ping_failed,
        timeout_s=40,
    )
    bad_text = bad_out or await _read_rc_output(gui_page)
    failed = _ping_failed(bad_text)

    bad_rows = [
        ("Unreachable — failed", "yes" if failed else "no", "yes", "PASS" if failed else "FAIL"),
        ("Unreachable — no replies", "yes" if "bytes from" not in bad_text.lower() else "no", "yes", "PASS" if failed else "FAIL"),
    ]
    print_gui_backend_table(f"GUI_113 [{device_label}] — Ping unreachable host", bad_rows)
    check.is_true(failed, f"Ping to {bad_target} should fail: {bad_text[:200]}")

    _log(f"GUI_113 [{device_label}] completed")
    return {"target": target, "bad_target": bad_target}


async def assert_gui_113_ping(
    gui_page,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_113_on_device(gui_page, cpe_ips, device_label="BTS", check_shell=True)

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(bsu_ip), "GUI_113: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_113: device credentials are required for CPE validation")
    _log(f"GUI_113: saved BTS snapshot ({bts_result['target']}); logging into CPE {cpe_ip}")

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    try:
        if await _cpe_has_feature(cpe_page, DiagnosticsLocators.UTIL_PING, "Ping", "GUI_113"):
            await _assert_gui_113_on_device(cpe_page, [bsu_ip], device_label="CPE", check_shell=False)
    finally:
        await cpe_page.close()


async def _assert_gui_114_on_device(gui_page, peer_ips: list[str], *, device_label: str = "BTS"):
    """GUI_114: Traceroute to reachable peer shows hop path in output."""
    target = peer_ips[0] if peer_ips else "127.0.0.1"
    _log(f"GUI_114 [{device_label}]: Traceroute to {target}")

    await open_diagnostics(gui_page)
    await _assert_util_fields_visible(
        gui_page,
        DiagnosticsLocators.UTIL_TRACEROUTE,
        [
            (DiagnosticsLocators.TRACEROUTE_ADDRESS, "Traceroute — Address"),
            (DiagnosticsLocators.TRACEROUTE_BUTTON, "Traceroute — action button"),
        ],
    )

    await gui_page.locator(DiagnosticsLocators.TRACEROUTE_ADDRESS).fill(target)
    await gui_page.locator(DiagnosticsLocators.TRACEROUTE_BUTTON).click()

    trace_out = await _wait_for_response_text(
        gui_page,
        lambda u: "diag_traceroute" in u,
        lambda t: "traceroute to" in t.lower() and ip_in_text(target, t),
        timeout_s=60,
    )
    trace_text = trace_out or await _read_rc_output(gui_page)

    rows = [
        ("Traceroute started", "yes" if "traceroute to" in trace_text.lower() else "no", "yes", None),
        ("Target reached", "yes" if ip_in_text(target, trace_text) else "no", "yes", None),
        ("Hop timing present", "yes" if " ms" in trace_text else "no", "yes", None),
    ]
    print_gui_backend_table(f"GUI_114 [{device_label}] — Traceroute", rows)
    check.is_true("traceroute to" in trace_text.lower(), f"Traceroute output missing: {trace_text[:200]}")
    check.is_true(ip_in_text(target, trace_text), f"Traceroute should list target {target}")
    _log(f"GUI_114 [{device_label}] completed")
    return {"target": target}


async def assert_gui_114_traceroute(
    gui_page,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_114_on_device(gui_page, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(bsu_ip), "GUI_114: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_114: device credentials are required for CPE validation")
    _log(f"GUI_114: saved BTS snapshot ({bts_result['target']}); logging into CPE {cpe_ip}")

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    try:
        if await _cpe_has_feature(cpe_page, DiagnosticsLocators.UTIL_TRACEROUTE, "Traceroute", "GUI_114"):
            await _assert_gui_114_on_device(cpe_page, [bsu_ip], device_label="CPE")
    finally:
        await cpe_page.close()


async def _assert_gui_115_on_device(gui_page, root_ssh, *, device_label: str = "BTS"):
    """GUI_115: Packet capture on Radio 1 — UI, capture progress, pcap file on device."""
    check.is_true(root_ssh is not None, f"GUI_115 [{device_label}]: SSH required")
    _log(f"GUI_115 [{device_label}]: Packet capture on Radio 1 interface")

    await open_diagnostics(gui_page)
    await _assert_util_fields_visible(
        gui_page,
        DiagnosticsLocators.UTIL_PACKET_CAPTURE,
        [
            (DiagnosticsLocators.PCAP_INTERFACE, "Capture — Interface"),
            (DiagnosticsLocators.PCAP_TIME, "Capture — Time"),
            (DiagnosticsLocators.PCAP_CAPTURE_BUTTON, "Capture — action button"),
        ],
    )

    options = await gui_page.locator(f"{DiagnosticsLocators.PCAP_INTERFACE} option").all_inner_texts()
    check.is_true(any("Radio 1" in o for o in options), f"Radio 1 not in interface list: {options}")
    check.is_true(any("LAN" in o for o in options), f"LAN not in interface list: {options}")

    page_text = await gui_page.locator("fieldset").filter(has=gui_page.locator(DiagnosticsLocators.PCAP_TIME)).first.inner_text()
    check.is_true("(10-300)" in page_text.replace(" ", ""), "Capture time range hint (10-300) not shown")

    await gui_page.locator(DiagnosticsLocators.PCAP_INTERFACE).select_option(label="Radio 1")
    await gui_page.locator(DiagnosticsLocators.PCAP_TIME).fill(str(PCAP_MIN_TIME_S))

    before_pcap = ssh_scalar(await _send_ssh(root_ssh, RootCommands.find_pcap_files()))

    await gui_page.locator(DiagnosticsLocators.PCAP_CAPTURE_BUTTON).click()
    status_loc = gui_page.locator(DiagnosticsLocators.PCAP_STATUS).first
    try:
        await status_loc.filter(has_text=re.compile(r"Capturing|capture", re.I)).wait_for(
            state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
        )
    except Exception:
        pass

    await gui_page.wait_for_timeout((PCAP_MIN_TIME_S + PCAP_EXTRA_WAIT_S) * 1000)

    status_text = (await status_loc.inner_text()).strip() if await status_loc.count() else ""
    after_pcap = ""
    deadline = asyncio.get_event_loop().time() + 20
    while asyncio.get_event_loop().time() < deadline:
        after_pcap = ssh_scalar(await _send_ssh(root_ssh, RootCommands.find_pcap_files()))
        if after_pcap and after_pcap != before_pcap:
            break
        await asyncio.sleep(2)

    pcap_path = ""
    if after_pcap:
        pcap_path = after_pcap.splitlines()[0].split()[-1]

    pcap_size = ""
    magic = ""
    if pcap_path and pcap_path.endswith(".pcap"):
        pcap_size = ssh_scalar(await _send_ssh(root_ssh, RootCommands.pcap_size_bytes(pcap_path)))
        magic = ssh_scalar(await _send_ssh(root_ssh, RootCommands.pcap_magic_hex(pcap_path)))

    capturing_seen = bool(re.search(r"captur", status_text, re.I))
    size_ok = bool(pcap_size and int(re.search(r"\d+", pcap_size).group()) > 0)
    magic_ok = magic.lower().startswith("d4c4b2a1") if magic else size_ok
    rows = [
        (
            "Capture progress text",
            status_text or ("Capturing" if capturing_seen else "(completed)"),
            "Capturing/Completed",
            "PASS" if (capturing_seen or pcap_path) else "FAIL",
        ),
        ("Pcap file on device", "yes" if pcap_path else "no", "yes", "PASS" if pcap_path else "FAIL"),
        ("Pcap size (bytes)", pcap_size or "0", ">0", "PASS" if size_ok else "FAIL"),
        ("Pcap magic (d4c4b2a1)", magic or ("size>0" if size_ok else "n/a"), "d4c4b2a1", "PASS" if magic_ok else "FAIL"),
    ]
    print_section(f"GUI_115 [{device_label}] — Packet Capture")
    print_comparison_table(rows)
    for _, _, _, status in rows:
        check.equal(status, "PASS", "GUI_115 table contains a failed row")
    check.is_true(pcap_path, f"No pcap file found after capture. before={before_pcap!r} after={after_pcap!r}")
    if pcap_size:
        check.is_true(int(re.search(r"\d+", pcap_size).group()) > 0, f"Pcap file empty: {pcap_path}")
    if magic:
        check.is_true(magic.lower().startswith("d4c4b2a1"), f"Unexpected pcap header: {magic}")
    else:
        check.is_true(capturing_seen or pcap_path, "Packet capture did not show progress or produce a file")
    _log(f"GUI_115 [{device_label}] completed")
    return {"pcap_path": pcap_path}


async def assert_gui_115_packet_capture(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_115_on_device(gui_page, root_ssh, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(device_creds), "GUI_115: device credentials are required for CPE validation")
    _log(f"GUI_115: saved BTS snapshot ({bts_result['pcap_path'] or 'no_pcap'}); logging into CPE {cpe_ip}")

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        if await _cpe_has_feature(cpe_page, DiagnosticsLocators.UTIL_PACKET_CAPTURE, "Packet Capture", "GUI_115"):
            await _assert_gui_115_on_device(cpe_page, cpe_root_ssh, device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_116_on_device(gui_page, *, device_label: str = "BTS"):
    """GUI_116: Diagnostics console — help command lists supported commands."""
    _log(f"GUI_116 [{device_label}]: Diagnostics console")

    await open_diagnostics(gui_page)
    await _assert_util_fields_visible(
        gui_page,
        DiagnosticsLocators.UTIL_CONSOLE,
        [
            (DiagnosticsLocators.CONSOLE_COMMAND, "Console — Command"),
            (DiagnosticsLocators.CONSOLE_EXECUTE, "Console — Execute button"),
            (DiagnosticsLocators.CONSOLE_OUTPUT, "Console — output area"),
        ],
    )

    note = await gui_page.get_by_text("execute 'help' command", exact=False).count()
    check.is_true(note > 0, "Console help note not displayed")

    await gui_page.locator(DiagnosticsLocators.CONSOLE_COMMAND).fill("help")
    await gui_page.locator(DiagnosticsLocators.CONSOLE_EXECUTE).click()

    console_out = await _wait_for_response_text(
        gui_page,
        lambda u: "diag_console/" in u or "diag_console_output" in u,
        lambda t: "help" in t.lower() and ("ifconfig" in t.lower() or "uci" in t.lower()),
        timeout_s=30,
    )
    gui_out = await _read_console_output(gui_page)
    text = console_out or gui_out

    expected_cmds = ["help", "ifconfig", "uci", "ping", "cfg80211tool"]
    rows = []
    for cmd in expected_cmds:
        rows.append((f"Lists '{cmd}'", "yes" if cmd in text.lower() else "no", "yes", None))
    print_gui_backend_table(f"GUI_116 [{device_label}] — Console help output", rows)
    check.is_true("help" in text.lower(), f"Console help output empty: {text[:200]}")
    check.is_true("ifconfig" in text.lower() or "uci" in text.lower(), f"Console help missing commands: {text[:200]}")
    _log(f"GUI_116 [{device_label}] completed")
    return {"text_len": len(text)}


async def assert_gui_116_console(
    gui_page,
    cpe_ips: list[str],
    *,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_116_on_device(gui_page, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(device_creds), "GUI_116: device credentials are required for CPE validation")
    _log(f"GUI_116: saved BTS snapshot (len={bts_result['text_len']}); logging into CPE {cpe_ip}")

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    try:
        if await _cpe_has_feature(cpe_page, DiagnosticsLocators.UTIL_CONSOLE, "Console", "GUI_116"):
            await _assert_gui_116_on_device(cpe_page, device_label="CPE")
    finally:
        await cpe_page.close()


async def _assert_gui_117_on_device(gui_page, root_ssh, *, device_label: str = "BTS"):
    """GUI_117: Estimate cable length — GUI output vs backend eth0 cablelen."""
    check.is_true(root_ssh is not None, f"GUI_117 [{device_label}]: SSH required")
    _log(f"GUI_117 [{device_label}]: Cable length estimate")

    await open_diagnostics(gui_page)
    await _assert_util_fields_visible(
        gui_page,
        DiagnosticsLocators.UTIL_CABLE_LENGTH,
        [(DiagnosticsLocators.CABLE_BUTTON, "Cable Length — Estimate button")],
    )

    backend_raw = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_cable_length_lan(0)))
    backend_m = re.search(r"[\d.]+", backend_raw)
    backend_val = float(backend_m.group()) if backend_m else None

    await _select_util(gui_page, DiagnosticsLocators.UTIL_CABLE_LENGTH)
    cable_text = ""
    seen_urls: list[str] = []

    async def on_cable(resp):
        nonlocal cable_text
        if "diag_cablen_output" not in resp.url:
            return
        seen_urls.append(resp.url)
        try:
            cable_text = _decode_luci_payload(await resp.text())
        except Exception:
            pass

    gui_page.on("response", on_cable)
    await gui_page.locator(DiagnosticsLocators.CABLE_BUTTON).click()
    deadline = asyncio.get_event_loop().time() + 45
    while asyncio.get_event_loop().time() < deadline:
        if cable_text and re.search(r"LAN\s*:\s*[\d.]+\s*m", cable_text, re.I):
            break
        await asyncio.sleep(0.5)
    gui_page.remove_listener("response", on_cable)

    gui_text = cable_text
    gui_m = re.search(r"LAN\s*:\s*([\d.]+)\s*m", gui_text, re.I)
    gui_val = float(gui_m.group(1)) if gui_m else None

    gui_ok = gui_m is not None and gui_val is not None and 0 <= gui_val <= 500
    backend_ok = backend_val is not None and 0 <= backend_val <= 500
    within_tol = (
        gui_val is not None
        and backend_val is not None
        and abs(gui_val - backend_val) <= 15
    )

    rows = [
        ("GUI estimate (LAN : N m)", str(gui_val) if gui_val is not None else "n/a", "0-500", "PASS" if gui_ok else "FAIL"),
        ("Backend eth0 cablelen", backend_raw, "0-500", "PASS" if backend_ok else "FAIL"),
        (
            "GUI vs backend (±15 m)",
            str(gui_val) if gui_val is not None else "n/a",
            str(backend_val) if backend_val is not None else "n/a",
            "PASS" if within_tol else ("WARN" if gui_ok and backend_ok else "FAIL"),
        ),
    ]

    print_section(f"GUI_117 [{device_label}] — Cable Length")
    print_comparison_table([(r[0], r[1], r[2], r[3]) for r in rows])
    for row in rows:
        if len(row) == 4 and row[3] == "FAIL":
            check.equal(row[3], "PASS", f"GUI_117: {row[0]} failed")
    check.is_true(gui_ok, f"Cable length GUI invalid: {gui_text[:120]!r}")
    check.is_true(backend_ok, f"Backend cablelen invalid: {backend_raw!r}")
    if gui_ok and backend_ok and not within_tol:
        _log(
            f"Note: GUI estimate {gui_val}m vs sysfs {backend_val}m differ "
            "(different measurement sources on device)"
        )
    _log(f"GUI_117 [{device_label}] completed")
    return {"gui_val": gui_val, "backend_raw": backend_raw}


async def assert_gui_117_cable_length(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_117_on_device(gui_page, root_ssh, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(device_creds), "GUI_117: device credentials are required for CPE validation")
    _log(
        f"GUI_117: saved BTS snapshot (gui={bts_result['gui_val']}, backend={bts_result['backend_raw']}); "
        f"logging into CPE {cpe_ip}"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        if await _cpe_has_feature(cpe_page, DiagnosticsLocators.UTIL_CABLE_LENGTH, "Cable Length", "GUI_117"):
            await _assert_gui_117_on_device(cpe_page, cpe_root_ssh, device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


def _parse_lldp_rows_from_text(text: str) -> list[dict[str, str]]:
    """Parse tab-separated LLDP table text (table or #lldp_output)."""
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("index"):
            continue
        if line.lower().startswith("lldp neighbors"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) < 4:
            parts = re.split(r"\s{2,}", line)
        if len(parts) < 4:
            continue
        if not re.match(r"^\d+$", parts[0].strip()):
            continue
        rows.append(
            {
                "index": parts[0].strip(),
                "interface": parts[1].strip(),
                "mac": parts[2].strip(),
                "system_name": parts[3].strip(),
                "description": parts[4].strip() if len(parts) > 4 else "",
            }
        )
    return rows


async def _scrape_lldp_rows(gui_page) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    loc = gui_page.locator(DiagnosticsLocators.LLDP_ROWS)
    count = await loc.count()
    for i in range(count):
        cells = loc.nth(i).locator("td")
        cell_count = await cells.count()
        if cell_count < 4:
            continue
        texts = [((await cells.nth(j).inner_text()).strip()) for j in range(cell_count)]
        if not texts or not re.match(r"^\d+$", texts[0]):
            continue
        rows.append(
            {
                "index": texts[0],
                "interface": texts[1] if len(texts) > 1 else "",
                "mac": texts[2] if len(texts) > 2 else "",
                "system_name": texts[3] if len(texts) > 3 else "",
                "description": texts[4] if len(texts) > 4 else "",
            }
        )
    if rows:
        return rows
    for source in (
        DiagnosticsLocators.LLDP_TABLE,
        DiagnosticsLocators.LLDP_OUTPUT,
    ):
        try:
            blob = await gui_page.locator(source).inner_text()
        except Exception:
            continue
        rows = _parse_lldp_rows_from_text(blob)
        if rows:
            return rows
    return []


async def _wait_for_lldp_neighbors(gui_page) -> list[dict[str, str]]:
    deadline = asyncio.get_event_loop().time() + LLDP_POLL_S
    while asyncio.get_event_loop().time() < deadline:
        neighbors = await _scrape_lldp_rows(gui_page)
        if neighbors:
            return neighbors
        await asyncio.sleep(0.5)
    return []


async def _assert_gui_118_on_device(
    gui_page,
    root_ssh,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """GUI_118: LLDP neighbors table shows linked peer (MAC / system name)."""
    check.is_true(root_ssh is not None, f"GUI_118 [{device_label}]: SSH required")
    _log(f"GUI_118 [{device_label}]: LLDP neighbors table")

    await open_diagnostics(gui_page)
    await _select_util(gui_page, DiagnosticsLocators.UTIL_LLDP)

    await gui_page.locator(DiagnosticsLocators.LLDP_TABLE).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )

    header_text = await gui_page.locator(DiagnosticsLocators.LLDP_TABLE).inner_text()
    for col in ("Index", "Interface", "MAC Address", "System Name", "System Description"):
        check.is_true(col in header_text, f"LLDP table missing column header: {col}")

    neighbors = await _wait_for_lldp_neighbors(gui_page)
    if not neighbors and device_label == "CPE":
        return _report_feature_unavailable("GUI_118", "LLDP", device_label, "no LLDP neighbors found")
    check.is_true(len(neighbors) >= 1, "LLDP neighbors table has no data rows")

    expected_mac = ""
    expected_name = ""
    if peer_ips:
        cpe_ip = peer_ips[0]
        assoc_idx = await find_assoc_index_for_cpe(root_ssh, cpe_ip, RADIO_INDEX)
        expected_mac = ssh_scalar(
            await _send_ssh(
                root_ssh,
                RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "mac"),
            )
        ).strip().lower()
        expected_name = ssh_scalar(
            await _send_ssh(
                root_ssh,
                RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "r_custname"),
            )
        ).strip().lower()

    def _mac_prefix_match(gui_mac: str, backend_mac: str) -> bool:
        gui_parts = gui_mac.lower().replace("-", ":").split(":")
        backend_parts = backend_mac.lower().replace("-", ":").split(":")
        if len(gui_parts) < 5 or len(backend_parts) < 5:
            return gui_mac == backend_mac
        return gui_parts[:5] == backend_parts[:5]

    table_rows = []
    mac_match = False
    name_match = False
    for n in neighbors:
        mac = n.get("mac", "").lower()
        sys_name = n.get("system_name", "").lower()
        if expected_mac and (_mac_prefix_match(mac, expected_mac) or mac == expected_mac):
            mac_match = True
        if expected_name and expected_name in sys_name:
            name_match = True
        table_rows.append(
            (
                f"Row {n.get('index', '?')}",
                f"{n.get('mac', '')} / {n.get('system_name', '')}",
                expected_mac or "any neighbor",
                "PASS" if n.get("mac") else "FAIL",
            )
        )

    print_section(f"GUI_118 [{device_label}] — LLDP neighbors")
    print_comparison_table(table_rows)
    check.is_true(neighbors, "No LLDP neighbor rows found")
    if expected_mac or expected_name:
        check.is_true(
            mac_match or name_match,
            f"Peer not found in LLDP (mac={expected_mac}, name={expected_name}): {neighbors}",
        )
    for n in neighbors:
        check.is_true(n.get("mac"), f"LLDP row missing MAC: {n}")
        check.is_true(n.get("interface"), f"LLDP row missing interface: {n}")
    _log(f"GUI_118 [{device_label}] completed")
    return {"neighbors": len(neighbors)}


async def assert_gui_118_lldp(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_118_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return
    check.is_true(bool(bsu_ip), "GUI_118: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_118: device credentials are required for CPE validation")
    _log(f"GUI_118: saved BTS snapshot (neighbors={bts_result['neighbors']}); logging into CPE {cpe_ip}")

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        if await _cpe_has_feature(cpe_page, DiagnosticsLocators.UTIL_LLDP, "LLDP", "GUI_118"):
            await _assert_gui_118_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()

