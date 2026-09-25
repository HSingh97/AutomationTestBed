"""Sanity LLDP helpers — enable/disable, capture, and neighbor table checks."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from pages.locators import DiagnosticsLocators, UITimeouts
from utils.diagnostics_flows import (
    _scrape_lldp_rows,
    _select_util,
    open_diagnostics,
)
from utils.net_utils import ips_equal, normalize_ip
from utils.parsers import ssh_scalar
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run

LLDP_BPF = "ether proto 0x88cc"
DEFAULT_LLDP_IFACE = "ath1"
PCAP_PATH = "/tmp/sanity81_lldp.pcap"


@dataclass(frozen=True)
class LldpCaptureResult:
    duration_s: int
    iface: str
    hostname: str
    tx_count: int
    pcap_path: str
    raw_excerpt: str


async def read_lldp_identity(ssh: AsyncGenericDriver) -> tuple[str, str]:
    """Return (hostname, eth0 MAC) for LLDP TX identification."""
    hostname = (await sanity_ssh_run(ssh, "uci -q get system.@system[0].hostname")).strip()
    mac = (await sanity_ssh_run(ssh, "cat /sys/class/net/eth0/address 2>/dev/null")).strip()
    return hostname, mac.lower()


async def lldp_service_running(ssh: AsyncGenericDriver) -> bool:
    out = (await sanity_ssh_run(ssh, "pidof lldpd >/dev/null && echo yes || echo no")).strip()
    return out.lower() == "yes"


async def set_lldp_enabled(ssh: AsyncGenericDriver, *, enabled: bool) -> None:
    """Start or stop the global LLDP daemon on the device."""
    if enabled:
        await sanity_ssh_run(ssh, "/etc/init.d/lldpd restart", timeout_s=25)
    else:
        await sanity_ssh_run(ssh, "/etc/init.d/lldpd stop", timeout_s=20)
        await sanity_ssh_run(ssh, "killall -9 lldpd 2>/dev/null || true", timeout_s=10)
        # Drain residual TX scheduling before a disable-capture.
        await asyncio.sleep(3)


async def count_lldp_hostname_in_pcap(
    ssh: AsyncGenericDriver,
    pcap_path: str,
    hostname: str,
) -> tuple[int, str]:
    """Offline pcap read — fast and scrapli-safe."""
    if not pcap_path:
        return 0, ""
    host = shlex.quote((hostname or "").strip())
    path = shlex.quote(pcap_path)
    cmd = (
        f"tcpdump -r {path} -nn {LLDP_BPF} 2>/dev/null | grep -F {host} | head -5 | tr '\\n' ';'"
    )
    raw = (await sanity_ssh_run(ssh, cmd, timeout_s=30)).strip()
    count = 0
    for line in raw.replace(";", "\n").splitlines():
        if "LLDP" in line and ((hostname or "") in line):
            count += 1
    if count == 0 and raw and "LLDP" in raw and hostname in raw:
        count = 1
    return count, raw[:600]


async def capture_lldp_tx_from_device(
    ssh: AsyncGenericDriver,
    *,
    hostname: str,
    iface: str = DEFAULT_LLDP_IFACE,
    duration_s: int = 12,
    pcap_path: str = PCAP_PATH,
) -> LldpCaptureResult:
    """
    Write a short LLDP pcap on-device (``tcpdump -w``), then count CPE TX frames.
    """
    safe_iface = shlex.quote(iface)
    path = shlex.quote(pcap_path)
    packets = max(4, min(8, int(duration_s) // 2 or 4))
    await ensure_sanity_ssh_open(ssh, retries=2, timeout_s=15)
    await sanity_ssh_run(ssh, f"rm -f {path}", timeout_s=5)
    # Bound capture time — without `timeout`, -c never finishes if no LLDP frames.
    await sanity_ssh_run(
        ssh,
        f"timeout {max(15, int(duration_s) + 5)} tcpdump -i {safe_iface} -nn "
        f"-c {packets} -w {path} {LLDP_BPF} 2>/dev/null || true",
        timeout_s=max(60, int(duration_s) + 40),
    )
    tx_count, excerpt = await count_lldp_hostname_in_pcap(ssh, pcap_path, hostname)
    if not excerpt:
        excerpt = pcap_path
    return LldpCaptureResult(
        duration_s=int(duration_s),
        iface=iface,
        hostname=(hostname or "").strip(),
        tx_count=tx_count,
        pcap_path=pcap_path,
        raw_excerpt=excerpt,
    )


def _mac_prefix_match(gui_mac: str, backend_mac: str) -> bool:
    gui_parts = gui_mac.lower().replace("-", ":").split(":")
    backend_parts = backend_mac.lower().replace("-", ":").split(":")
    if len(gui_parts) < 5 or len(backend_parts) < 5:
        return gui_mac.lower() == backend_mac.lower()
    return gui_parts[:5] == backend_parts[:5]


async def _find_assoc_index_for_peer(
    ssh: AsyncGenericDriver,
    peer_ip: str,
    *,
    radio_idx: int = 1,
) -> int:
    """Resolve sua/sub index for a linked peer IP on this device."""
    target = normalize_ip(peer_ip)
    for idx in range(1, 33):
        assoc = ssh_scalar(
            await sanity_ssh_run(
                ssh, RootCommands.get_link_stat_associd(radio_idx, idx), timeout_s=15
            )
        )
        if assoc in {"", "0"}:
            continue
        ipv4 = ssh_scalar(
            await sanity_ssh_run(
                ssh,
                RootCommands.get_link_stat_field(radio_idx, idx, "ip"),
                timeout_s=15,
            )
        )
        ipv6 = ssh_scalar(
            await sanity_ssh_run(
                ssh,
                RootCommands.get_link_stat_field(radio_idx, idx, "ipv6"),
                timeout_s=15,
            )
        )
        if (
            ips_equal(target, ipv4)
            or ips_equal(target, ipv6)
            or target in ipv4
            or target in ipv6
        ):
            return idx
    return 1


async def read_expected_lldp_peer(
    ssh: AsyncGenericDriver,
    peer_ip: str,
    *,
    radio_idx: int = 1,
) -> tuple[str, str]:
    """Return (peer MAC, peer system name) from RF link statistics."""
    await ensure_sanity_ssh_open(ssh, retries=2, timeout_s=15)
    assoc_idx = await _find_assoc_index_for_peer(ssh, peer_ip, radio_idx=radio_idx)
    mac = ssh_scalar(
        await sanity_ssh_run(
            ssh,
            RootCommands.get_link_stat_field(radio_idx, assoc_idx, "mac"),
            timeout_s=15,
        )
    ).strip().lower()
    name = ssh_scalar(
        await sanity_ssh_run(
            ssh,
            RootCommands.get_link_stat_field(radio_idx, assoc_idx, "r_custname"),
            timeout_s=15,
        )
    ).strip()
    if not name:
        name = ssh_scalar(
            await sanity_ssh_run(ssh, "uci -q get system.@system[0].hostname", timeout_s=10)
        ).strip()
    return mac, name


async def wait_lldp_neighbors(gui_page, *, timeout_s: float = 30) -> list[dict[str, str]]:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        neighbors = await _scrape_lldp_rows(gui_page)
        if neighbors:
            return neighbors
        await asyncio.sleep(1)
    return await _scrape_lldp_rows(gui_page)


async def verify_cpe_lldp_neighbor_table(
    gui_page,
    *,
    expected_mac: str,
    expected_name: str,
    case_id: str,
    neighbor_timeout_s: float = 30,
) -> list[dict[str, str]]:
    """Monitor → Tools → LLDP; verify linked BTS appears in neighbor table."""
    await open_diagnostics(gui_page)
    await _select_util(gui_page, DiagnosticsLocators.UTIL_LLDP)
    await gui_page.locator(DiagnosticsLocators.LLDP_TABLE).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )

    header_text = await gui_page.locator(DiagnosticsLocators.LLDP_TABLE).inner_text()
    for col in ("Index", "Interface", "MAC Address", "System Name", "System Description"):
        check.is_true(
            col in header_text,
            f"{case_id}: LLDP table missing column header: {col}",
        )

    neighbors = await wait_lldp_neighbors(gui_page, timeout_s=neighbor_timeout_s)
    check.is_true(
        len(neighbors) >= 1,
        f"{case_id}: LLDP neighbors table has no data rows",
    )

    exp_mac = (expected_mac or "").strip().lower()
    exp_name = (expected_name or "").strip().lower()
    mac_match = False
    name_match = False
    rows: list[tuple[str, str, str, str]] = []

    for n in neighbors:
        mac = (n.get("mac") or "").lower()
        sys_name = (n.get("system_name") or "").lower()
        iface = n.get("interface") or ""
        if exp_mac and (_mac_prefix_match(mac, exp_mac) or mac == exp_mac):
            mac_match = True
        if exp_name and exp_name in sys_name:
            name_match = True
        rows.append(
            (
                f"Row {n.get('index', '?')}",
                f"{mac} / {sys_name} / {iface}",
                f"{exp_mac or 'any'} / {exp_name or 'any'}",
                "PASS" if mac and iface else "FAIL",
            )
        )

    from utils.verify_output import print_comparison_table

    print_comparison_table(rows)
    check.is_true(
        mac_match or name_match,
        f"{case_id}: BTS peer not in LLDP neighbors "
        f"(mac={exp_mac!r}, name={exp_name!r}): {neighbors}",
    )
    for n in neighbors:
        check.is_true(n.get("mac"), f"{case_id}: LLDP row missing MAC: {n}")
        check.is_true(n.get("interface"), f"{case_id}: LLDP row missing interface: {n}")

    return neighbors


async def read_lldp_mgmt_ips(ssh: AsyncGenericDriver) -> list[str]:
    """Return management addresses advertised by LLDP neighbors (lldpcli)."""
    cmd = (
        "/usr/sbin/lldpcli show neighbors 2>/dev/null "
        "| awk '/MgmtIP:/{printf \"%s,\", $2} END{print \"\"}' | sed 's/,$//'"
    )
    out = (await sanity_ssh_run(ssh, cmd, timeout_s=15)).strip()
    if out:
        return [ip for ip in out.split(",") if ip]

    await ensure_sanity_ssh_open(ssh)
    response = await ssh.send_command(
        "/usr/sbin/lldpcli show neighbors 2>/dev/null",
        timeout_ops=15,
    )
    text = str(response.result or "").replace("\r", "")
    ips: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if "MgmtIP:" not in line:
            continue
        ip = line.split("MgmtIP:", 1)[1].strip()
        if ip:
            ips.append(ip)
    return ips


async def wait_lldp_mgmt_ips(
    ssh: AsyncGenericDriver,
    *,
    timeout_s: float = 30,
) -> list[str]:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        ips = await read_lldp_mgmt_ips(ssh)
        if ips:
            return ips
        await asyncio.sleep(1)
    return await read_lldp_mgmt_ips(ssh)


def _cell_populated(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and v not in {"—", "-", "n/a", "N/A"}


async def verify_cpe_lldp_discovery_table(
    gui_page,
    cpe_ssh: AsyncGenericDriver,
    *,
    expected_mac: str,
    expected_name: str,
    expected_ip: str,
    case_id: str,
    neighbor_timeout_s: float = 30,
    lldp_refresh_settle_s: float = 5,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Monitor → Tools → LLDP discovery table: MAC, System Name, System Description,
    and IP (GUI column if present, else lldpcli MgmtIP). Restart LLDP and verify
    the table repopulates with the linked peer.
    """
    await open_diagnostics(gui_page)
    await _select_util(gui_page, DiagnosticsLocators.UTIL_LLDP)
    await gui_page.locator(DiagnosticsLocators.LLDP_TABLE).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )

    header_text = await gui_page.locator(DiagnosticsLocators.LLDP_TABLE).inner_text()
    for col in ("MAC Address", "System Name", "System Description"):
        check.is_true(
            col in header_text,
            f"{case_id}: LLDP discovery table missing column header: {col}",
        )
    has_ip_column = "IP Address" in header_text

    neighbors_before = await wait_lldp_neighbors(gui_page, timeout_s=neighbor_timeout_s)
    check.is_true(
        len(neighbors_before) >= 1,
        f"{case_id}: LLDP discovery table has no data rows",
    )

    exp_mac = (expected_mac or "").strip().lower()
    exp_name = (expected_name or "").strip().lower()
    exp_ip = normalize_ip(expected_ip or "")
    peer_row: dict[str, str] | None = None
    rows: list[tuple[str, str, str, str]] = []

    for n in neighbors_before:
        mac = (n.get("mac") or "").lower()
        sys_name = (n.get("system_name") or "").lower()
        descr = n.get("description") or ""
        check.is_true(_cell_populated(mac), f"{case_id}: LLDP row missing MAC: {n}")
        check.is_true(
            _cell_populated(sys_name),
            f"{case_id}: LLDP row missing System Name: {n}",
        )
        check.is_true(
            _cell_populated(descr),
            f"{case_id}: LLDP row missing System Description: {n}",
        )
        if has_ip_column:
            ip_val = n.get("ip_address") or ""
            check.is_true(
                _cell_populated(ip_val),
                f"{case_id}: LLDP row missing IP Address: {n}",
            )
        if exp_mac and (_mac_prefix_match(mac, exp_mac) or mac == exp_mac):
            peer_row = n
        elif exp_name and exp_name in sys_name:
            peer_row = n
        rows.append(
            (
                f"Row {n.get('index', '?')}",
                f"{mac} / {sys_name} / {descr[:40]}",
                f"{exp_mac or 'any'} / {exp_name or 'any'}",
                "PASS" if _cell_populated(mac) and _cell_populated(sys_name) else "FAIL",
            )
        )

    from utils.verify_output import print_comparison_table

    print_comparison_table(rows)
    check.is_true(
        peer_row is not None,
        f"{case_id}: BTS peer not in LLDP discovery table "
        f"(mac={exp_mac!r}, name={exp_name!r}): {neighbors_before}",
    )

    mgmt_ips = await wait_lldp_mgmt_ips(cpe_ssh, timeout_s=neighbor_timeout_s)
    check.is_true(
        bool(mgmt_ips),
        f"{case_id}: lldpcli reported no MgmtIP for LLDP neighbors",
    )
    if exp_ip and mgmt_ips:
        ip_match = any(
            ips_equal(exp_ip, normalize_ip(ip)) or exp_ip in normalize_ip(ip)
            for ip in mgmt_ips
        )
        check.is_true(
            ip_match,
            f"{case_id}: LLDP MgmtIP {mgmt_ips!r} does not include BTS IP {exp_ip!r}",
        )
        if not has_ip_column:
            print(
                f"{case_id}: GUI table has no IP Address column; "
                f"verified MgmtIP via lldpcli: {mgmt_ips}"
            )

    await set_lldp_enabled(cpe_ssh, enabled=True)
    await asyncio.sleep(max(1.0, float(lldp_refresh_settle_s)))
    await _select_util(gui_page, DiagnosticsLocators.UTIL_LLDP)
    neighbors_after = await wait_lldp_neighbors(gui_page, timeout_s=neighbor_timeout_s)
    check.is_true(
        len(neighbors_after) >= 1,
        f"{case_id}: LLDP discovery table empty after LLDP restart",
    )

    after_mac_match = False
    for n in neighbors_after:
        mac = (n.get("mac") or "").lower()
        sys_name = (n.get("system_name") or "").lower()
        if exp_mac and (_mac_prefix_match(mac, exp_mac) or mac == exp_mac):
            after_mac_match = True
        elif exp_name and exp_name in sys_name:
            after_mac_match = True
    check.is_true(
        after_mac_match,
        f"{case_id}: BTS peer missing from LLDP table after restart/update: "
        f"before={neighbors_before}, after={neighbors_after}",
    )

    return neighbors_before, neighbors_after
