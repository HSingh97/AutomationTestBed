"""Monitor -> Radio Statistics -> Link tab (GUI_83–GUI_84, GUI_88–GUI_92)."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from pages.locators import (
    CommonLocators,
    LoginPageLocators,
    MonitorLocators,
    SummaryLocators,
    SummaryNetworkLocators,
    UITimeouts,
)
from utils.net_utils import (
    format_http_host,
    format_luci_url,
    ip_in_text,
    is_ipv6_literal,
    ips_equal,
    normalize_ip,
    prefer_ipv6_for_cpe,
)
from utils.parsers import (
    clean_ssh_output,
    format_assoc_uptime,
    parse_comb_snr_cell,
    parse_ifconfig_mac,
    parse_numeric_metric,
    parse_rate_mbps_cell,
    parse_throughput_cell,
    ssh_scalar,
    sysfs_tput_to_mbps,
)
from utils.cpe_session import (
    ensure_cpe_logged_in,
    goto_cpe_luci,
    is_cpe_host_reachable,
    open_cpe_gui_session_if_reachable,
)
from utils.monitor_assoc import find_assoc_index_for_cpe
from utils.verify_output import print_comparison_table, print_gui_backend_table, print_section

RADIO_INDEX = 1
LINK_INTF_ID = "5"  # firmware table id for Radio 1 link stats
POLL_WAIT_S = 12
DISCONNECT_RECONNECT_MAX_S = 5.0
CLEAR_MIN_TPUT = 50_000  # sysfs tx_tput must grow above this before Clear (post-disconnect)
CLEAR_POLL_S = 6.0
CLEAR_PEAK_SAMPLES = 6
COUNTER_ABS_TOL = 3000
COUNTER_REL_TOL = 0.08
SNR_TOLERANCE_DB = 2.0


def _log(message: str):
    print(f"[RADIO_STATS] {message}")


async def _goto_admin_path(gui_page, path_fragment: str) -> bool:
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or "")
    if not match:
        return False
    target = f"{match.group(1)}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return True


async def open_radio1_link_statistics(gui_page):
    """Navigate Monitor -> Radio 1 Statistics -> Link tab."""
    try:
        menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
        await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        submenu = gui_page.locator(MonitorLocators.SUBMENU_RADIO_1_STATS).first
        await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/radio1"):
            raise

    link_tab = gui_page.locator(MonitorLocators.TAB_LINK_STATS).first
    try:
        await link_tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await link_tab.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        pass

    table = gui_page.locator(MonitorLocators.RADIO1_LINK_TABLE).first
    await table.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await _wait_for_link_table_rows(gui_page)
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _wait_for_link_table_rows(gui_page):
    table = gui_page.locator(MonitorLocators.RADIO1_LINK_TABLE).first
    deadline = asyncio.get_event_loop().time() + POLL_WAIT_S
    while asyncio.get_event_loop().time() < deadline:
        rows = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS)
        count = await rows.count()
        if count > 0:
            text = await rows.first.inner_text()
            if "No Links" not in text:
                return
        await asyncio.sleep(1)
    raise TimeoutError("Radio 1 Link statistics table did not populate in time")


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


async def _infer_prefer_ipv6(root_ssh, radio_idx: int, assoc_idx: int, cpe_ip: str | None) -> bool:
    if prefer_ipv6_for_cpe(cpe_ip):
        return True
    ipv4 = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "ip")))
    ipv6 = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "ipv6")))
    ipv4_empty = not ipv4 or ipv4 in {"", "0.0.0.0"}
    ipv6_valid = bool(ipv6) and ipv6 not in {"", "::"}
    return ipv6_valid and ipv4_empty


async def _read_backend_link_row(root_ssh, radio_idx: int, assoc_idx: int, *, prefer_ipv6: bool) -> dict[str, str]:
    ipv4 = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "ip")))
    ipv6 = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "ipv6")))
    if prefer_ipv6 and ipv6 and ipv6 not in {"", "::"}:
        ip_display = ipv6
    elif ipv4 and ipv4 not in {"", "0.0.0.0"}:
        ip_display = ipv4
    else:
        ip_display = ipv6 or ipv4

    assoc_time = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "assoc_time")))
    tx_rate = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "tx_rate")))
    rx_rate = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "rx_rate")))
    tx_mcs = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "tx_rate_mcs")))
    rx_mcs = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "rx_rate_mcs")))
    tx_tput = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "tx_tput")))
    rx_tput = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "rx_tput")))

    return {
        "index": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_associd(radio_idx, assoc_idx))),
        "system_name": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "r_custname"))),
        "ip_address": ip_display,
        "uptime": format_assoc_uptime(assoc_time),
        "local_snr": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "comb_rssi"))),
        "remote_snr": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "r_comb_rssi"))),
        "rate_out": f"{tx_rate} ({tx_mcs})",
        "rate_in": f"{rx_rate} ({rx_mcs})",
        "tput_out": sysfs_tput_to_mbps(tx_tput),
        "tput_in": sysfs_tput_to_mbps(rx_tput),
    }


async def _scrape_gui_link_row(gui_page, row_index: int = 0) -> dict[str, str]:
    row = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS).nth(row_index)

    async def cell_text(col: str) -> str:
        cell = row.locator(f"td[data-col='{col}']").first
        if await cell.count() == 0:
            return ""
        return (await cell.inner_text()).strip()

    ip_html = ""
    ip_cell = row.locator("td[data-col='ipaddr']").first
    if await ip_cell.count():
        ip_html = (await ip_cell.inner_text()).strip()

    local_snr, remote_snr = "", ""
    comb_cell = row.locator("td[data-col='comb_snr']").first
    if await comb_cell.count():
        spans = [t.strip() for t in await comb_cell.locator("span").all_inner_texts() if t.strip()]
        if len(spans) >= 2:
            local_snr, remote_snr = spans[0], spans[1]
        else:
            local_snr, remote_snr = parse_comb_snr_cell(await comb_cell.inner_text())

    rate_text = await cell_text("mbps")
    tput_text = await cell_text("thrpt")
    rate_out, rate_in = parse_rate_mbps_cell(rate_text)
    tput_out, tput_in = parse_throughput_cell(tput_text)

    return {
        "index": await cell_text("index"),
        "system_name": await cell_text("system"),
        "ip_address": ip_html,
        "uptime": await cell_text("uptime"),
        "local_snr": local_snr,
        "remote_snr": remote_snr,
        "rate_out": rate_out,
        "rate_in": rate_in,
        "tput_out": tput_out,
        "tput_in": tput_in,
    }


def _validate_link_field(label: str, backend_val: str, gui_val: str, *, tolerance: float | None = None):
    from utils.verify_output import match_status

    status = match_status(gui_val, backend_val, tolerance=tolerance)
    check.equal(status, "PASS", f"{label} mismatch (GUI={gui_val}, Backend={backend_val})")


async def _assert_gui_83_on_device(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """
    GUI_83: Monitor -> Radio 1 Statistics -> Link.
    Validates Index, System Name, IP/IPv6, Uptime, SNR, Rate, Throughput vs backend sysfs.
    """
    cpe_ip = cpe_ips[0] if cpe_ips else ""

    _log(f"GUI_83 [{device_label}]: Radio 1 Link statistics (peer={cpe_ip or 'auto'})")
    check.is_true(root_ssh is not None, f"GUI_83 [{device_label}]: SSH required for backend validation")
    await open_radio1_link_statistics(gui_page)

    link_count = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_active_link_count(RADIO_INDEX)))
    check.is_true(link_count not in {"", "0"}, "Expected at least one active Radio 1 link")

    gui_row = await _scrape_gui_link_row(gui_page, 0)

    assoc_idx = await find_assoc_index_for_cpe(root_ssh, cpe_ip, RADIO_INDEX) if cpe_ip else 1
    prefer_ipv6 = await _infer_prefer_ipv6(root_ssh, RADIO_INDEX, assoc_idx, cpe_ip or None)
    backend_row = await _read_backend_link_row(root_ssh, RADIO_INDEX, assoc_idx, prefer_ipv6=prefer_ipv6)

    table = print_gui_backend_table(
        f"GUI_83 [{device_label}] — Monitor → Radio 1 Statistics → Link (sua{assoc_idx})",
        [
            ("Index", gui_row["index"], backend_row["index"], None),
            ("System Name", gui_row["system_name"], backend_row["system_name"], None),
            ("IP Address", gui_row["ip_address"], backend_row["ip_address"], None),
            ("Uptime", gui_row["uptime"], backend_row["uptime"], None),
            ("Local SNR (dB)", gui_row["local_snr"], backend_row["local_snr"], 2.0),
            ("Remote SNR (dB)", gui_row["remote_snr"], backend_row["remote_snr"], 2.0),
            ("Rate Out", gui_row["rate_out"], backend_row["rate_out"], None),
            ("Rate In", gui_row["rate_in"], backend_row["rate_in"], None),
            ("Throughput Out", gui_row["tput_out"], backend_row["tput_out"], 0.5),
            ("Throughput In", gui_row["tput_in"], backend_row["tput_in"], 0.5),
        ],
    )

    _validate_link_field("Index", backend_row["index"], gui_row["index"])
    _validate_link_field("System Name", backend_row["system_name"], gui_row["system_name"])
    _validate_link_field("IP Address", backend_row["ip_address"], gui_row["ip_address"])
    _validate_link_field("Uptime", backend_row["uptime"], gui_row["uptime"])
    _validate_link_field("Local SNR (dB)", backend_row["local_snr"], gui_row["local_snr"], tolerance=2.0)
    _validate_link_field("Remote SNR (dB)", backend_row["remote_snr"], gui_row["remote_snr"], tolerance=2.0)
    _validate_link_field("Rate Out (Mbps)", backend_row["rate_out"], gui_row["rate_out"])
    _validate_link_field("Rate In (Mbps)", backend_row["rate_in"], gui_row["rate_in"])
    _validate_link_field("Throughput Out (Mbps)", backend_row["tput_out"], gui_row["tput_out"], tolerance=0.5)
    _validate_link_field("Throughput In (Mbps)", backend_row["tput_in"], gui_row["tput_in"], tolerance=0.5)

    for _, _, _, status in table:
        check.equal(status, "PASS", "GUI_83 table contains a failed row")

    if cpe_ip:
        check.is_true(
            ip_in_text(cpe_ip, gui_row["ip_address"]),
            f"CPE {cpe_ip} not reflected in GUI IP/IPv6 column",
        )

    _log(f"GUI_83 [{device_label}] completed: Radio 1 Link statistics match backend")
    return {
        "assoc_idx": assoc_idx,
        "gui_row": gui_row,
        "backend_row": backend_row,
    }


async def assert_gui_83_radio_link_statistics(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_83_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_83: BTS IP is required for the follow-up CPE validation")
    check.is_true(bool(device_creds), "GUI_83: device credentials are required for CPE validation")
    _log(
        f"GUI_83: saved BTS link snapshot (sua{bts_result['assoc_idx']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_83_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _find_link_row_index(gui_page, cpe_ip: str | None = None) -> int:
    """Return row index in Radio 1 link table (default first active row)."""
    rows = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS)
    count = await rows.count()
    check.is_true(count > 0, "Radio 1 Link statistics table has no rows")
    if not cpe_ip:
        return 0
    target = normalize_ip(cpe_ip)
    for i in range(count):
        row = rows.nth(i)
        ip_cell = row.locator("td[data-col='ipaddr']").first
        if await ip_cell.count() == 0:
            continue
        text = (await ip_cell.inner_text()).strip()
        if ip_in_text(cpe_ip, text):
            return i
    return 0


async def _is_on_link_statistics_page(gui_page) -> bool:
    url = gui_page.url or ""
    on_radio1 = "/monitor/radio1" in url and "/details/" not in url
    table_visible = await gui_page.locator(MonitorLocators.RADIO1_LINK_TABLE).is_visible(
        timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    return on_radio1 and table_visible


async def _is_on_detailed_statistics_page(gui_page) -> bool:
    url = gui_page.url or ""
    if "/details/" not in url:
        return False
    back_visible = await gui_page.locator(MonitorLocators.DETAILED_STATS_BACK).first.is_visible(
        timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    return back_visible


async def open_link_detailed_statistics(
    gui_page,
    cpe_ip: str | None = None,
    *,
    row_index: int | None = None,
):
    """
    Monitor -> Radio 1 Statistics -> Link -> click a link row (not the IP hyperlink).
    Lands on Detailed Statistics (/admin/monitor/radio1/details/...).
    """
    await open_radio1_link_statistics(gui_page)
    idx = row_index if row_index is not None else await _find_link_row_index(gui_page, cpe_ip)
    row = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS).nth(idx)
    await row.locator("td[data-col='index']").click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    check.is_true(
        await _is_on_detailed_statistics_page(gui_page),
        f"Expected Detailed Statistics page after row click (url={gui_page.url})",
    )


async def _backend_link_count(root_ssh) -> int:
    raw = ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_active_link_count(RADIO_INDEX)))
    match = re.search(r":(\d+)", raw)
    if match:
        return int(match.group(1))
    return int(raw) if raw.isdigit() else 0


async def _backend_assoc_time(root_ssh, assoc_idx: int = 1) -> int:
    raw = ssh_scalar(
        await _send_ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "assoc_time"))
    )
    try:
        return int(float(raw))
    except ValueError:
        return -1


async def _backend_detail_tput(root_ssh, assoc_idx: int = 1) -> tuple[int, int]:
    tx = ssh_scalar(
        await _send_ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "tx_tput"))
    )
    rx = ssh_scalar(
        await _send_ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "rx_tput"))
    )
    try:
        return int(float(tx)), int(float(rx))
    except ValueError:
        return -1, -1


async def _wait_for_measurable_tput(root_ssh, assoc_idx: int, *, min_tput: int = CLEAR_MIN_TPUT) -> tuple[int, int]:
    """Wait for link byte counters to accumulate so Clear has a measurable baseline."""
    deadline = time.monotonic() + CLEAR_POLL_S + 12.0
    last_tx, last_rx = -1, -1
    while time.monotonic() < deadline:
        tx, rx = await _backend_detail_tput(root_ssh, assoc_idx)
        last_tx, last_rx = tx, rx
        if tx >= min_tput or rx >= min_tput:
            return tx, rx
        await asyncio.sleep(0.5)
    return last_tx, last_rx


async def _find_peer_ip_link(gui_page, peer_ip: str):
    """Return (link locator, visible text, href) for the row matching peer_ip."""
    rows = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS)
    count = await rows.count()
    fallback = None
    for i in range(count):
        row = rows.nth(i)
        link = row.locator("td[data-col='ipaddr'] a").first
        if await link.count() == 0:
            continue
        text = (await link.inner_text()).strip()
        href = (await link.get_attribute("href") or "").strip()
        if fallback is None:
            fallback = (link, text, href)
        if ip_in_text(peer_ip, text) or ip_in_text(peer_ip, href):
            return link, text, href
    if fallback:
        return fallback
    raise RuntimeError("No peer IP hyperlink found in Radio 1 Link statistics table")


async def _verify_target_summary_page(target_page, target_ip: str) -> tuple[bool, str, str]:
    """Return (summary_ok, summary_ip_text, page_status for table)."""
    login_visible = await target_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible(
        timeout=UITimeouts.SHORT_WAIT_MS
    )
    model_visible = await target_page.locator(SummaryLocators.MODEL).is_visible(
        timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    gui_ip_parts: list[str] = []
    for locator in (
        SummaryNetworkLocators.IP_ADDRESS,
        SummaryNetworkLocators.IPV6_ADDRESS,
    ):
        if await target_page.locator(locator).count():
            gui_ip_parts.append((await target_page.locator(locator).inner_text()).strip())
    gui_ip = " ".join(g for g in gui_ip_parts if g).strip()
    ip_ok = bool(target_ip) and ip_in_text(target_ip, gui_ip)
    summary_ok = model_visible and ip_ok and not login_visible
    page_status = "SUMMARY" if summary_ok else "not_ready"
    return summary_ok, gui_ip, page_status


async def _verify_single_ip_hyperlink(
    gui_page,
    target_ip: str,
    device_creds: dict,
    *,
    source_label: str,
    target_label: str,
):
    ip_link, link_text, link_href = await _find_peer_ip_link(gui_page, target_ip)
    check.is_true(bool(link_href), f"{source_label}: hyperlink has no href for {target_ip}")
    host_in_href = ip_in_text(target_ip, link_href) or format_http_host(target_ip) in link_href
    check.is_true(
        host_in_href or ip_in_text(target_ip, link_text),
        f"{source_label}: link does not target {target_label} {target_ip} (text={link_text}, href={link_href})",
    )

    if not is_cpe_host_reachable(target_ip):
        print_gui_backend_table(
            f"GUI_84 [{source_label}] — {target_label} {target_ip} hyperlink (test host cannot open peer GUI)",
            [
                (f"{target_label} IP (link text)", target_ip, link_text, None),
                ("Link href", link_href, link_href, None),
                ("Peer GUI from test host", "reachable", "not reachable (IPv6 mgmt path)", None),
            ],
        )
        _log(
            f"GUI_84 [{source_label}]: verified hyperlink to {target_ip}; "
            "skipped new-tab login because peer is not reachable from test host"
        )
        return {
            "target_ip": target_ip,
            "link_text": link_text,
            "link_href": link_href,
            "target_url": "",
            "summary_ip": "",
        }

    async with gui_page.context.expect_page(timeout=UITimeouts.PAGE_LOAD_MS) as new_page_info:
        await ip_link.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    target_page = await new_page_info.value
    await target_page.wait_for_load_state("domcontentloaded")
    await goto_cpe_luci(target_page, target_ip)

    await ensure_cpe_logged_in(target_page, target_ip, device_creds)

    target_url = target_page.url or ""
    host_ok = ip_in_text(target_ip, target_url) or format_http_host(target_ip) in target_url
    summary_ok, gui_ip, page_status = await _verify_target_summary_page(target_page, target_ip)

    table = print_gui_backend_table(
        f"GUI_84 [{source_label}] — {target_label} {target_ip} hyperlink opens web GUI",
        [
            (f"{target_label} IP (link text)", target_ip, link_text, None),
            ("Link href", link_href, link_href, None),
            ("New tab URL host", target_ip, target_url, None),
            (f"{target_label} GUI page", "SUMMARY", page_status, None),
            ("Summary IP", target_ip, gui_ip or "(empty)", None),
        ],
    )

    for _, _, _, status in table:
        check.equal(status, "PASS", f"GUI_84 table contains a failed row for {source_label} -> {target_label}")

    check.is_true(host_ok, f"{source_label}: new tab URL does not reference {target_label} {target_ip}: {target_url}")
    check.is_true(
        summary_ok,
        f"{source_label}: SUMMARY page not loaded after login (url={target_url}, summary_ip={gui_ip})",
    )

    await target_page.close()
    snapshot = {
        "target_ip": target_ip,
        "link_text": link_text,
        "link_href": link_href,
        "target_url": target_url,
        "summary_ip": gui_ip,
    }
    _log(f"GUI_84 [{source_label}] saved hyperlink snapshot for {target_label} {target_ip}")
    return snapshot


async def _assert_gui_84_on_bts(gui_page, cpe_ips: list[str], device_creds: dict):
    """GUI_84 on BTS: CPE IP hyperlink opens CPE web GUI."""
    check.is_true(bool(cpe_ips), "GUI_84 requires at least one CPE (--remote-ip or profile)")

    await open_radio1_link_statistics(gui_page)
    row_count = await gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS).count()
    check.is_true(
        row_count >= len(cpe_ips),
        f"GUI_84: expected at least {len(cpe_ips)} link row(s), found {row_count}",
    )

    for cpe_ip in cpe_ips:
        _log(f"GUI_84: CPE IP hyperlink (cpe={cpe_ip})")
        await _verify_single_ip_hyperlink(
            gui_page,
            cpe_ip,
            device_creds,
            source_label="BTS",
            target_label="CPE",
        )
        await open_radio1_link_statistics(gui_page)

    _log(f"GUI_84 completed: verified {len(cpe_ips)} CPE(s)")


async def assert_gui_84_cpe_ip_hyperlink(
    gui_page,
    cpe_ips: list[str],
    device_creds: dict,
    *,
    bsu_ip: str | None = None,
):
    await _assert_gui_84_on_bts(gui_page, cpe_ips, device_creds)

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_84: BTS IP is required for CPE-side hyperlink verification")
    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    try:
        await open_radio1_link_statistics(cpe_page)
        _log(f"GUI_84: saved BTS hyperlink data; verifying BTS hyperlink from CPE {cpe_ip}")
        await _verify_single_ip_hyperlink(
            cpe_page,
            bsu_ip,
            device_creds,
            source_label="CPE",
            target_label="BTS",
        )
    finally:
        await cpe_page.close()


async def _assert_gui_88_on_device(gui_page, peer_ips: list[str], *, device_label: str = "BTS"):
    """
    GUI_88: Link row -> Detailed Statistics -> Back returns to RF Link Statistics.
    """
    cpe_ip = peer_ips[0] if peer_ips else None
    _log(f"GUI_88 [{device_label}]: Detailed Statistics Back (peer={cpe_ip or 'first row'})")

    await open_link_detailed_statistics(gui_page, cpe_ip)
    details_url = gui_page.url or ""

    await gui_page.locator(MonitorLocators.DETAILED_STATS_BACK).first.click(
        timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    on_link_page = await _is_on_link_statistics_page(gui_page)
    page_label = "RF Link Statistics" if on_link_page else "other"
    table = print_gui_backend_table(
        f"GUI_88 [{device_label}] — Back button returns to RF Link Statistics",
        [("Returned to Link tab", "RF Link Statistics", page_label, None)],
    )
    for _, _, _, status in table:
        check.equal(status, "PASS", "GUI_88 table contains a failed row")

    check.is_true(on_link_page, f"Back did not return to Link statistics (url={gui_page.url})")
    _log(f"GUI_88 [{device_label}] completed: returned from {details_url} to {gui_page.url}")
    return {
        "details_url": details_url,
        "final_url": gui_page.url or "",
    }


async def assert_gui_88_detailed_statistics_back(
    gui_page,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_88_on_device(gui_page, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_88: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_88: device credentials are required for CPE validation")
    _log(
        f"GUI_88: saved BTS snapshot ({bts_result['details_url']} -> {bts_result['final_url']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    try:
        await _assert_gui_88_on_device(cpe_page, [bsu_ip], device_label="CPE")
    finally:
        await cpe_page.close()


async def _assert_gui_89_on_device(
    gui_page,
    root_ssh,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """
    GUI_89: Detailed Statistics -> Disconnect briefly drops RF link (< 5 s reconnect).
    """
    cpe_ip = peer_ips[0] if peer_ips else None
    assoc_idx = await find_assoc_index_for_cpe(root_ssh, cpe_ip, RADIO_INDEX) if cpe_ip else 1
    _log(f"GUI_89 [{device_label}]: Detailed Statistics Disconnect (peer={cpe_ip or 'first row'}, sua{assoc_idx})")

    assoc_before = await _backend_assoc_time(root_ssh, assoc_idx)
    links_before = await _backend_link_count(root_ssh)

    await open_link_detailed_statistics(gui_page, cpe_ip)
    await gui_page.locator(MonitorLocators.DETAILED_STATS_DISCONNECT).first.click(
        force=True,
        timeout=UITimeouts.ELEMENT_WAIT_MS,
    )
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    left_details = "/details/" not in (gui_page.url or "")
    t0 = time.monotonic()
    saw_reset = False
    links_after = links_before
    assoc_min = assoc_before

    while time.monotonic() - t0 < DISCONNECT_RECONNECT_MAX_S + 0.5:
        await asyncio.sleep(0.15)
        assoc_now = await _backend_assoc_time(root_ssh, assoc_idx)
        links_now = await _backend_link_count(root_ssh)
        links_after = links_now
        if assoc_now >= 0 and assoc_before >= 0 and (
            assoc_now < assoc_before or assoc_now <= 10
        ):
            saw_reset = True
        assoc_min = min(assoc_min, assoc_now) if assoc_now >= 0 else assoc_min
        if saw_reset and links_now >= 1:
            break

    reconnect_ok = links_after >= 1
    duration_s = round(time.monotonic() - t0, 2)
    within_5s = duration_s <= DISCONNECT_RECONNECT_MAX_S

    table = print_gui_backend_table(
        f"GUI_89 [{device_label}] — Disconnect briefly drops RF link",
        [
            ("Left details page", "yes", "yes" if left_details else "no", None),
            ("Assoc time reset", "yes", "yes" if saw_reset else "no", None),
            ("Link count after", ">=1", str(links_after), None),
            ("Within 5 seconds", "yes", "yes" if within_5s else "no", None),
        ],
    )
    for _, _, _, status in table:
        check.equal(status, "PASS", "GUI_89 table contains a failed row")

    check.is_true(left_details or await _is_on_link_statistics_page(gui_page), "Disconnect did not leave Detailed Statistics")
    check.is_true(saw_reset, f"Assoc time did not reset after disconnect (before={assoc_before}, min={assoc_min})")
    check.is_true(within_5s, f"Disconnect/reconnect took {duration_s}s (limit {DISCONNECT_RECONNECT_MAX_S}s)")
    check.is_true(reconnect_ok, f"RF link not restored (links={links_after})")
    _log(
        f"GUI_89 [{device_label}] completed: disconnect/reconnect in {duration_s}s "
        f"(links {links_before}->{links_after})"
    )
    return {
        "assoc_idx": assoc_idx,
        "duration_s": duration_s,
        "links_before": links_before,
        "links_after": links_after,
    }


async def assert_gui_89_detailed_statistics_disconnect(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_89_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_89: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_89: device credentials are required for CPE validation")
    _log(
        f"GUI_89: saved BTS disconnect snapshot (sua{bts_result['assoc_idx']}, "
        f"{bts_result['links_before']}->{bts_result['links_after']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_89_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _peak_detail_tput(root_ssh, assoc_idx: int, samples: int = CLEAR_PEAK_SAMPLES) -> tuple[int, int]:
    """Sample sysfs tput over a short window; return peak tx/rx (baseline for Clear)."""
    tx_peak, rx_peak = 0, 0
    for _ in range(samples):
        tx, rx = await _backend_detail_tput(root_ssh, assoc_idx)
        tx_peak = max(tx_peak, tx, 0)
        rx_peak = max(rx_peak, rx, 0)
        await asyncio.sleep(0.3)
    return tx_peak, rx_peak


async def _assert_gui_90_on_device(
    gui_page,
    root_ssh,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """
    GUI_90: Detailed Statistics -> Clear resets on-device link traffic counters.
    """
    cpe_ip = peer_ips[0] if peer_ips else None
    check.is_true(root_ssh is not None, f"GUI_90: SSH required")
    assoc_idx = await find_assoc_index_for_cpe(root_ssh, cpe_ip, RADIO_INDEX) if cpe_ip else 1
    _log(f"GUI_90 [{device_label}]: Detailed Statistics Clear (peer={cpe_ip or 'first row'}, sua{assoc_idx})")
    await open_link_detailed_statistics(gui_page, cpe_ip)
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    await _wait_for_measurable_tput(root_ssh, assoc_idx)
    tx_before, rx_before = await _peak_detail_tput(root_ssh, assoc_idx)
    check.is_true(
        tx_before >= CLEAR_MIN_TPUT or rx_before >= CLEAR_MIN_TPUT,
        f"Link counters did not reach measurable level before Clear (tx={tx_before}, rx={rx_before})",
    )

    await gui_page.locator(MonitorLocators.DETAILED_STATS_CLEAR).first.click(
        timeout=UITimeouts.ELEMENT_WAIT_MS
    )

    still_on_details = await _is_on_detailed_statistics_page(gui_page)
    tx_after, rx_after = tx_before, rx_before
    cleared = False
    deadline = time.monotonic() + CLEAR_POLL_S
    while time.monotonic() < deadline:
        await asyncio.sleep(0.15)
        tx_now, rx_now = await _backend_detail_tput(root_ssh, assoc_idx)
        if tx_now >= 0 and rx_now >= 0 and (tx_now < tx_before or rx_now < rx_before):
            tx_after, rx_after = tx_now, rx_now
            cleared = True
            break

    table = print_gui_backend_table(
        f"GUI_90 [{device_label}] — Clear resets link statistics",
        [
            ("On details page", "yes", "yes" if still_on_details else "no", None),
            (
                "Statistics reset",
                "yes",
                "yes" if cleared else "no",
                None,
            ),
        ],
    )
    for _, _, _, status in table:
        check.equal(status, "PASS", "GUI_90 table contains a failed row")

    check.is_true(still_on_details, f"Clear left Detailed Statistics page (url={gui_page.url})")
    check.is_true(
        cleared,
        f"Clear did not reset tput counters (tx {tx_before}->{tx_after}, rx {rx_before}->{rx_after})",
    )
    _log(
        f"GUI_90 [{device_label}] completed: cleared stats "
        f"(tx {tx_before}->{tx_after}, rx {rx_before}->{rx_after})"
    )
    return {
        "assoc_idx": assoc_idx,
        "tx_before": tx_before,
        "tx_after": tx_after,
        "rx_before": rx_before,
        "rx_after": rx_after,
    }


async def assert_gui_90_detailed_statistics_clear(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_90_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_90: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_90: device credentials are required for CPE validation")
    _log(
        f"GUI_90: saved BTS clear snapshot (sua{bts_result['assoc_idx']}, "
        f"tx {bts_result['tx_before']}->{bts_result['tx_after']}, "
        f"rx {bts_result['rx_before']}->{bts_result['rx_after']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_90_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _detail_id_text(gui_page, css_selector: str) -> str:
    loc = gui_page.locator(css_selector).first
    if await loc.count() == 0:
        return ""
    return (await loc.inner_text()).strip()


async def _detail_row_pair(gui_page, row_label: str) -> tuple[str, str]:
    """Return (local, remote) column text for a labeled statistics row."""
    rows = gui_page.locator("tr")
    for i in range(await rows.count()):
        text = (await rows.nth(i).inner_text()).strip()
        if text.startswith(row_label):
            parts = re.split(r"[\t\n]+", text)
            if len(parts) >= 3:
                return parts[1].strip(), parts[2].strip()
    return "", ""


async def _ssh_link_field(root_ssh, assoc_idx: int, field: str) -> str:
    return ssh_scalar(
        await _send_ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, field))
    )


def _noise_sysfs_to_dbm(raw: str) -> str:
    """Convert sysfs noise counter to dBm shown in Detailed Statistics GUI."""
    try:
        value = int(float(raw))
        if value > 50:
            return str(value - 200)
        return str(value)
    except ValueError:
        return raw


def _normalize_detail_value(val: str) -> str:
    clean = str(val or "").strip()
    lower = clean.lower()
    if not clean or clean in ("-", "::", "n/a"):
        return ""
    if "uci" in lower and "not found" in lower:
        return ""
    return clean


def _detail_values_match(label: str, gui_val: str, backend_val: str, tol: float | None) -> bool:
    gui_n = _normalize_detail_value(gui_val)
    backend_n = _normalize_detail_value(backend_val)

    if not gui_n and not backend_n:
        return True

    if "MAC" in label:
        return gui_n.lower() == backend_n.lower()

    if "IP" in label or "IPv6" in label:
        if ips_equal(gui_n, backend_n):
            return True
        if ip_in_text(backend_n, gui_n) or ip_in_text(gui_n, backend_n):
            return True

    if tol is not None:
        g_num, b_num = parse_numeric_metric(gui_n), parse_numeric_metric(backend_n)
        if g_num is not None and b_num is not None:
            return abs(g_num - b_num) <= tol

    if "SNR" in label:
        g_num, b_num = parse_numeric_metric(gui_n), parse_numeric_metric(backend_n)
        if g_num is not None and b_num is not None:
            return abs(g_num - b_num) <= SNR_TOLERANCE_DB

    if any(token in label for token in ("Packets", "Retries", "RTX")):
        g_num, b_num = parse_numeric_metric(gui_n), parse_numeric_metric(backend_n)
        if g_num is not None and b_num is not None:
            bound = max(COUNTER_ABS_TOL, COUNTER_REL_TOL * max(g_num, b_num, 1))
            return abs(g_num - b_num) <= bound

    return backend_n.lower() in gui_n.lower() or gui_n.lower() in backend_n.lower()


def _validate_detail_metric(label: str, backend_val: str, gui_val: str, *, tolerance: float | None = None):
    if _detail_values_match(label, gui_val, backend_val, tolerance):
        return
    check.fail(f"{label} mismatch (GUI={gui_val}, Backend={backend_val})")


async def _run_detail_comparison_table(title: str, comparisons: list[tuple[str, str, str, float | None]]):
    """comparisons: (label, gui_value, backend_value, optional_tolerance)."""
    table_rows = []
    for label, gui_val, backend_val, tol in comparisons:
        status = "PASS" if _detail_values_match(label, gui_val, backend_val, tol) else "FAIL"
        table_rows.append((label, gui_val, backend_val, status))
    print_section(title)
    print_comparison_table(table_rows)
    for label, _, _, status in table_rows:
        check.equal(status, "PASS", f"{title}: {label} failed")
    for label, gui_val, backend_val, tol in comparisons:
        _validate_detail_metric(label, backend_val, gui_val, tolerance=tol)


async def _snapshot_backend_detail(root_ssh, assoc_idx: int) -> dict[str, str]:
    """Read sysfs link statistics once to avoid drift between field reads."""
    fields = [
        "ip", "ipv6", "mac", "r_custname",
        "l_lat_lon", "r_lat_lon",
        "l_snra1", "l_snra2", "r_snra1", "r_snra2",
        "l_siga1", "l_siga2", "r_siga1", "r_siga2",
        "l_noise", "r_noise",
        "l_power", "r_power", "tx_rate", "rx_rate", "tx_tput", "rx_tput",
        "l_txdata", "r_txdata", "l_rxdata", "r_rxdata",
        "l_retries", "r_retries", "l_drop", "r_drop", "l_rtx", "r_rtx", "r_buildno",
    ]
    snapshot: dict[str, str] = {}
    for field in fields:
        snapshot[field] = await _ssh_link_field(root_ssh, assoc_idx, field)
    snapshot["lan_ip"] = ssh_scalar(await _send_ssh(root_ssh, RootCommands.GET_IPv4))
    snapshot["lan_ipv6"] = ssh_scalar(await _send_ssh(root_ssh, RootCommands.GET_IPv6))
    snapshot["hostname"] = ssh_scalar(await _send_ssh(root_ssh, RootCommands.GET_SYSNAME))
    snapshot["fw_local"] = ssh_scalar(await _send_ssh(root_ssh, RootCommands.GET_SW_VERSION))
    snapshot["lan_mac"] = parse_ifconfig_mac(
        (await root_ssh.send_command(RootCommands.get_mac_wireless(RADIO_INDEX))).result
    )
    return snapshot


async def _snapshot_gui_detail(gui_page) -> dict[str, str]:
    local_mac, remote_mac = await _detail_row_pair(gui_page, "MAC Address")
    local_fw, remote_fw = await _detail_row_pair(gui_page, "Firmware Version")
    local_rate, remote_rate = await _detail_row_pair(gui_page, "Rate")
    return {
        "l_ip": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_IP),
        "ip": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_IP),
        "l_ipv6": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_IPV6),
        "ipv6": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_IPV6),
        "lan_mac": local_mac,
        "mac": remote_mac,
        "l_custname": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_NAME),
        "r_custname": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_NAME),
        "l_lat_lon": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_GPS),
        "r_lat_lon": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_GPS),
        "l_snra1": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_SNRA1),
        "l_snra2": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_SNRA2),
        "r_snra1": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_SNRA1),
        "r_snra2": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_SNRA2),
        "l_siga1": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_SIGA1),
        "l_siga2": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_SIGA2),
        "r_siga1": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_SIGA1),
        "r_siga2": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_SIGA2),
        "l_noise": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_NOISE),
        "r_noise": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_NOISE),
        "l_power": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_POWER),
        "r_power": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_POWER),
        "local_rate": local_rate,
        "remote_rate": remote_rate,
        "tx_tput": await _detail_id_text(gui_page, MonitorLocators.DETAILED_TX_TPUT),
        "rx_tput": await _detail_id_text(gui_page, MonitorLocators.DETAILED_RX_TPUT),
        "l_txdata": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_TXDATA),
        "r_txdata": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_TXDATA),
        "l_rxdata": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_RXDATA),
        "r_rxdata": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_RXDATA),
        "l_retries": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_RETRIES),
        "r_retries": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_RETRIES),
        "l_drop": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_DROP),
        "r_drop": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_DROP),
        "l_rtx": await _detail_id_text(gui_page, MonitorLocators.DETAIL_LOCAL_RTX),
        "r_rtx": await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_RTX),
        "fw_local": local_fw,
        "fw_remote": remote_fw or await _detail_id_text(gui_page, MonitorLocators.DETAIL_REMOTE_FW),
    }


async def _assert_gui_91_on_device(
    gui_page,
    root_ssh,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """
    GUI_91: Detailed Statistics — IP, MAC, System Name, GPS, SNR/Signal, Noise (Local & Remote).
    """
    cpe_ip = peer_ips[0] if peer_ips else None
    check.is_true(root_ssh is not None, f"GUI_91 [{device_label}]: SSH required")
    assoc_idx = await find_assoc_index_for_cpe(root_ssh, cpe_ip, RADIO_INDEX) if cpe_ip else 1
    _log(f"GUI_91 [{device_label}]: Detailed Statistics identity (peer={cpe_ip or 'first row'}, sua{assoc_idx})")

    await open_link_detailed_statistics(gui_page, cpe_ip)

    backend = await _snapshot_backend_detail(root_ssh, assoc_idx)
    gui = await _snapshot_gui_detail(gui_page)

    comparisons = [
        ("Local IP", gui["l_ip"], backend["lan_ip"], None),
        ("Remote IP", gui["ip"], backend["ip"], None),
        ("Local IPv6", gui["l_ipv6"], backend["lan_ipv6"], None),
        ("Remote IPv6", gui["ipv6"], backend["ipv6"], None),
        ("Local MAC", gui["lan_mac"], backend["lan_mac"], None),
        ("Remote MAC", gui["mac"], backend["mac"], None),
        ("Local System Name", gui["l_custname"], backend["hostname"], None),
        ("Remote System Name", gui["r_custname"], backend["r_custname"], None),
        ("Local GPS", gui["l_lat_lon"], backend["l_lat_lon"], None),
        ("Remote GPS", gui["r_lat_lon"], backend["r_lat_lon"], None),
        ("Local SNR A1 (dB)", gui["l_snra1"], backend["l_snra1"], SNR_TOLERANCE_DB),
        ("Remote SNR A1 (dB)", gui["r_snra1"], backend["r_snra1"], SNR_TOLERANCE_DB),
        ("Local SNR A2 (dB)", gui["l_snra2"], backend["l_snra2"], SNR_TOLERANCE_DB),
        ("Remote SNR A2 (dB)", gui["r_snra2"], backend["r_snra2"], SNR_TOLERANCE_DB),
        ("Local Signal A1 (dBm)", gui["l_siga1"], backend["l_siga1"], None),
        ("Remote Signal A1 (dBm)", gui["r_siga1"], backend["r_siga1"], None),
        ("Local Noise (dBm)", gui["l_noise"], _noise_sysfs_to_dbm(backend["l_noise"]), None),
        ("Remote Noise (dBm)", gui["r_noise"], _noise_sysfs_to_dbm(backend["r_noise"]), None),
    ]

    await _run_detail_comparison_table(
        f"GUI_91 [{device_label}] — Detailed Statistics identity (GUI vs backend)",
        comparisons,
    )
    _log(f"GUI_91 [{device_label}] completed: identity fields match backend with units populated")
    return {
        "assoc_idx": assoc_idx,
        "peer_ip": cpe_ip or "",
    }


async def assert_gui_91_detailed_statistics_identity(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_91_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_91: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_91: device credentials are required for CPE validation")
    _log(
        f"GUI_91: saved BTS identity snapshot (sua{bts_result['assoc_idx']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_91_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_92_on_device(
    gui_page,
    root_ssh,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """
    GUI_92: Detailed Statistics — Tx Power, Rate, Throughput, Packets, Retries, Drop, RTX %, Firmware.
    """
    cpe_ip = peer_ips[0] if peer_ips else None
    check.is_true(root_ssh is not None, f"GUI_92 [{device_label}]: SSH required")
    assoc_idx = await find_assoc_index_for_cpe(root_ssh, cpe_ip, RADIO_INDEX) if cpe_ip else 1
    _log(f"GUI_92 [{device_label}]: Detailed Statistics performance (peer={cpe_ip or 'first row'}, sua{assoc_idx})")

    await open_link_detailed_statistics(gui_page, cpe_ip)

    backend = await _snapshot_backend_detail(root_ssh, assoc_idx)
    gui = await _snapshot_gui_detail(gui_page)
    tx_mbps = sysfs_tput_to_mbps(backend["tx_tput"])
    rx_mbps = sysfs_tput_to_mbps(backend["rx_tput"])

    comparisons = [
        ("Local Tx Power (dBm)", gui["l_power"], backend["l_power"], None),
        ("Remote Tx Power (dBm)", gui["r_power"], backend["r_power"], None),
        ("Local Rate (Mbps)", gui["local_rate"], backend["tx_rate"], None),
        ("Remote Rate (Mbps)", gui["remote_rate"], backend["rx_rate"], None),
        ("Local Throughput (Mbps)", gui["tx_tput"], tx_mbps, 0.5),
        ("Remote Throughput (Mbps)", gui["rx_tput"], rx_mbps, 0.5),
        ("Local Total Tx Packets", gui["l_txdata"], backend["l_txdata"], None),
        ("Remote Total Tx Packets", gui["r_txdata"], backend["r_txdata"], None),
        ("Local Total Rx Packets", gui["l_rxdata"], backend["l_rxdata"], None),
        ("Remote Total Rx Packets", gui["r_rxdata"], backend["r_rxdata"], None),
        ("Local Retries", gui["l_retries"], backend["l_retries"], None),
        ("Remote Retries", gui["r_retries"], backend["r_retries"], None),
        ("Local Dropped", gui["l_drop"], backend["l_drop"], None),
        ("Remote Dropped", gui["r_drop"], backend["r_drop"], None),
        ("Local RTX (%)", gui["l_rtx"], backend["l_rtx"], None),
        ("Remote RTX (%)", gui["r_rtx"], backend["r_rtx"], None),
        ("Local Firmware", gui["fw_local"], backend["fw_local"], None),
        ("Remote Firmware", gui["fw_remote"], backend["r_buildno"], None),
    ]

    await _run_detail_comparison_table(
        f"GUI_92 [{device_label}] — Detailed Statistics performance (GUI vs backend)",
        comparisons,
    )
    _log(f"GUI_92 [{device_label}] completed: performance fields match backend with units populated")
    return {
        "assoc_idx": assoc_idx,
        "peer_ip": cpe_ip or "",
    }


async def assert_gui_92_detailed_statistics_performance(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_92_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_92: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_92: device credentials are required for CPE validation")
    _log(
        f"GUI_92: saved BTS performance snapshot (sua{bts_result['assoc_idx']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session_if_reachable(gui_page.context, cpe_ip, device_creds)
    if not cpe_page:
        return
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_92_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()

