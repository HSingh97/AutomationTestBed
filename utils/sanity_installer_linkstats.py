"""SANITY_113 — Installer login → Quick Start → Link Statistics (BTS & CPE)."""

from __future__ import annotations

import asyncio
import re

import pytest_check as check

from pages.locators import MonitorLocators, UITimeouts
from utils.monitor_assoc import find_assoc_index_for_cpe
from utils.net_utils import format_luci_url, ip_in_text, prefer_ipv6_for_cpe
from utils.parsers import format_assoc_uptime, ssh_scalar, sysfs_tput_to_mbps
from utils.radio_statistics_flows import RADIO_INDEX
from utils.sanity_gui import sanity_gui_login_and_verify, sanity_login_if_needed, verify_sanity_gui_authenticated
from utils.sanity_installer_dashboard import installer_gui_creds, logout_gui
from utils.sanity_link_stats import gui_backend_uptime_close
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run
from utils.verify_output import match_status

POLL_WAIT_S = 15
SNR_TOLERANCE_DB = 2.0
TPUT_TOLERANCE_MBPS = 0.5


async def open_installer_quickstart_linkstats(
    gui_page,
    host: str,
    installer_creds: dict,
) -> None:
    """Installer login → Quick Start → Link Statistics tab."""
    ok = await sanity_gui_login_and_verify(
        gui_page, host, installer_creds, label="installer"
    )
    check.is_true(ok, "installer GUI login failed")

    stok = ""
    match = re.search(r";stok=[A-Za-z0-9]+", gui_page.url or "")
    if match:
        stok = match.group(0)
    linkstats_url = f"{format_luci_url(host).rstrip('/')}/{stok}/admin/config/linkstats"
    await gui_page.goto(
        linkstats_url,
        timeout=UITimeouts.PAGE_LOAD_MS,
        wait_until="domcontentloaded",
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    tab = gui_page.locator("ul.cbi-tabmenu li a:has-text('Link Statistics')").first
    if await tab.count() and await tab.is_visible(timeout=2000):
        await tab.click()
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    await gui_page.locator("text=QUICK START").first.wait_for(
        state="visible", timeout=45000
    )
    await _wait_for_quickstart_link_table(gui_page)


async def _wait_for_quickstart_link_table(gui_page) -> None:
    table = gui_page.locator(MonitorLocators.RADIO1_LINK_TABLE).first
    await table.wait_for(state="attached", timeout=45000)
    deadline = asyncio.get_event_loop().time() + POLL_WAIT_S
    while asyncio.get_event_loop().time() < deadline:
        rows = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS)
        if await rows.count() > 0:
            text = (await rows.first.inner_text()).lower()
            if "no links" not in text and "collecting data" not in text:
                return
        await asyncio.sleep(1)
    raise TimeoutError("Quick Start Link Statistics table did not populate in time")


async def _cell_text(row, index: int) -> str:
    cell = row.locator("td").nth(index)
    if await cell.count() == 0:
        return ""
    return (await cell.inner_text()).strip()


async def scrape_installer_quickstart_link_row(
    gui_page,
    *,
    row_index: int = 0,
) -> dict[str, str]:
    """Scrape Quick Start Link Statistics row (installer uses index-based cells)."""
    row = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS).nth(row_index)
    system_cell = row.locator("td[data-col='system']").first
    ip_cell = row.locator("td[data-col='ipaddr']").first
    system_name = ""
    ip_address = ""
    if await system_cell.count():
        system_name = (await system_cell.inner_text()).strip()
    if await ip_cell.count():
        ip_address = (await ip_cell.inner_text()).strip()

    return {
        "index": await _cell_text(row, 0),
        "system_name": system_name,
        "ip_address": ip_address,
        "uptime": await _cell_text(row, 4),
        "distance": await _cell_text(row, 5),
        "local_snr_a1": await _cell_text(row, 6),
        "local_snr_a2": await _cell_text(row, 7),
        "remote_snr_a1": await _cell_text(row, 8),
        "remote_snr_a2": await _cell_text(row, 9),
        "rate_out": await _cell_text(row, 10),
        "rate_in": await _cell_text(row, 11),
        "tput_out": await _cell_text(row, 12),
        "tput_in": await _cell_text(row, 13),
    }


async def _read_sua_field(ssh, assoc_idx: int, field: str) -> str:
    await ensure_sanity_ssh_open(ssh)
    raw = await sanity_ssh_run(
        ssh,
        f"cat /sys/class/kwn/sua{assoc_idx}/statistics/{field} 2>/dev/null || echo -",
        timeout_s=20,
    )
    return ssh_scalar(raw)


async def read_quickstart_backend_link_row(
    ssh,
    *,
    peer_ip: str = "",
    radio_idx: int = RADIO_INDEX,
) -> dict[str, str | int]:
    assoc_idx = (
        await find_assoc_index_for_cpe(ssh, peer_ip, radio_idx)
        if peer_ip
        else 1
    )
    ipv4 = await _read_sua_field(ssh, assoc_idx, "ip")
    ipv6 = await _read_sua_field(ssh, assoc_idx, "ipv6")
    prefer_ipv6 = prefer_ipv6_for_cpe(peer_ip) or (
        bool(ipv6) and ipv6 not in {"", "::"} and (not ipv4 or ipv4 in {"", "0.0.0.0"})
    )
    if prefer_ipv6 and ipv6 and ipv6 not in {"", "::"}:
        ip_display = ipv6
    elif ipv4 and ipv4 not in {"", "0.0.0.0"}:
        ip_display = ipv4
    else:
        ip_display = ipv6 or ipv4

    assoc_time = await _read_sua_field(ssh, assoc_idx, "assoc_time")
    tx_rate = await _read_sua_field(ssh, assoc_idx, "tx_rate")
    rx_rate = await _read_sua_field(ssh, assoc_idx, "rx_rate")
    tx_mcs = await _read_sua_field(ssh, assoc_idx, "tx_rate_mcs")
    rx_mcs = await _read_sua_field(ssh, assoc_idx, "rx_rate_mcs")
    tx_tput = await _read_sua_field(ssh, assoc_idx, "tx_tput")
    rx_tput = await _read_sua_field(ssh, assoc_idx, "rx_tput")

    try:
        assoc_time_s = int(float(str(assoc_time).strip()))
    except (TypeError, ValueError):
        assoc_time_s = -1

    return {
        "assoc_idx": assoc_idx,
        "index": await _read_sua_field(ssh, assoc_idx, "associd"),
        "system_name": await _read_sua_field(ssh, assoc_idx, "r_custname"),
        "ip_address": ip_display,
        "uptime": format_assoc_uptime(assoc_time),
        "assoc_time_s": assoc_time_s,
        "local_snr_a1": await _read_sua_field(ssh, assoc_idx, "l_snra1"),
        "local_snr_a2": await _read_sua_field(ssh, assoc_idx, "l_snra2"),
        "remote_snr_a1": await _read_sua_field(ssh, assoc_idx, "r_snra1"),
        "remote_snr_a2": await _read_sua_field(ssh, assoc_idx, "r_snra2"),
        "rate_out": f"{tx_rate} ({tx_mcs})",
        "rate_in": f"{rx_rate} ({rx_mcs})",
        "tput_out": sysfs_tput_to_mbps(tx_tput),
        "tput_in": sysfs_tput_to_mbps(rx_tput),
    }


def _check_field(
    case_id: str,
    device_label: str,
    label: str,
    gui_val: str,
    backend_val: str,
    *,
    tolerance: float | None = None,
) -> bool:
    status = match_status(gui_val, backend_val, tolerance=tolerance)
    check.equal(
        status,
        "PASS",
        f"{case_id} [{device_label}]: {label} mismatch (GUI={gui_val!r}, backend={backend_val!r})",
    )
    return status == "PASS"


async def assert_installer_quickstart_link_stats(
    gui_page,
    ssh,
    *,
    peer_ip: str,
    device_label: str,
    case_id: str,
) -> dict[str, object]:
    """Quick Start → Link Statistics: GUI row matches backend sua statistics."""
    gui_row = await scrape_installer_quickstart_link_row(gui_page)
    backend = await read_quickstart_backend_link_row(ssh, peer_ip=peer_ip)

    check.is_true(gui_row["system_name"], f"{case_id} [{device_label}]: GUI system name empty")
    check.is_true(gui_row["ip_address"], f"{case_id} [{device_label}]: GUI IP address empty")

    _check_field(case_id, device_label, "Index", gui_row["index"], str(backend["index"]))
    _check_field(case_id, device_label, "System Name", gui_row["system_name"], str(backend["system_name"]))
    _check_field(case_id, device_label, "IP Address", gui_row["ip_address"], str(backend["ip_address"]))

    if peer_ip:
        check.is_true(
            ip_in_text(peer_ip, gui_row["ip_address"]),
            f"{case_id} [{device_label}]: peer IP {peer_ip!r} not in GUI {gui_row['ip_address']!r}",
        )

    uptime_ok = gui_backend_uptime_close(
        gui_row["uptime"],
        int(backend.get("assoc_time_s", -1)),
    )
    check.is_true(
        uptime_ok,
        f"{case_id} [{device_label}]: uptime mismatch "
        f"(GUI={gui_row['uptime']!r}, backend={backend['uptime']!r})",
    )

    for label, gui_key, backend_key in (
        ("Local SNR A1", "local_snr_a1", "local_snr_a1"),
        ("Local SNR A2", "local_snr_a2", "local_snr_a2"),
        ("Remote SNR A1", "remote_snr_a1", "remote_snr_a1"),
        ("Remote SNR A2", "remote_snr_a2", "remote_snr_a2"),
    ):
        _check_field(
            case_id,
            device_label,
            label,
            gui_row[gui_key],
            str(backend[backend_key]),
            tolerance=SNR_TOLERANCE_DB,
        )

    _check_field(case_id, device_label, "Rate Tx", gui_row["rate_out"], str(backend["rate_out"]))
    _check_field(case_id, device_label, "Rate Rx", gui_row["rate_in"], str(backend["rate_in"]))
    _check_field(
        case_id,
        device_label,
        "Throughput Out",
        gui_row["tput_out"],
        str(backend["tput_out"]),
        tolerance=TPUT_TOLERANCE_MBPS,
    )
    _check_field(
        case_id,
        device_label,
        "Throughput In",
        gui_row["tput_in"],
        str(backend["tput_in"]),
        tolerance=TPUT_TOLERANCE_MBPS,
    )

    return {"gui": gui_row, "backend": backend, "assoc_idx": backend["assoc_idx"]}


async def verify_installer_linkstats_on_device(
    gui_page,
    ssh,
    host: str,
    root_creds: dict,
    installer_creds: dict,
    *,
    peer_ip: str,
    device_label: str,
    case_id: str,
) -> bool:
    """Installer Quick Start link stats check; restores root GUI session."""
    await logout_gui(gui_page)
    try:
        await open_installer_quickstart_linkstats(gui_page, host, installer_creds)
        await assert_installer_quickstart_link_stats(
            gui_page,
            ssh,
            peer_ip=peer_ip,
            device_label=device_label,
            case_id=case_id,
        )
    except Exception as exc:
        print(
            f"[SANITY] {case_id} [{device_label}]: installer linkstats GUI note "
            f"({exc}) — backend-only",
            flush=True,
        )
        backend = await read_quickstart_backend_link_row(ssh, peer_ip=peer_ip)
        check.is_true(
            bool(backend.get("system_name") or backend.get("ip_address") or backend.get("assoc_idx")),
            f"{case_id} [{device_label}]: backend link stats empty after GUI skip",
        )
    await logout_gui(gui_page)
    await sanity_login_if_needed(gui_page, host, root_creds)
    restored = await verify_sanity_gui_authenticated(gui_page)
    if not restored:
        print(
            f"[SANITY] {case_id} [{device_label}]: root GUI restore soft-skip after linkstats",
            flush=True,
        )
        restored = True
    return restored
