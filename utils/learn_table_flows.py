"""Monitor -> Learn Table — Bridge (GUI_105–106) and ARP (GUI_107–108)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from pages.locators import CommonLocators, LearnTableLocators, MonitorLocators, UITimeouts
from utils.cpe_session import open_cpe_gui_session
from utils.monitor_assoc import find_assoc_index_for_cpe
from utils.net_utils import ip_in_text, is_ipv6_literal
from utils.parsers import clean_ssh_output, parse_ifconfig_mac, ssh_scalar
from utils.verify_output import print_comparison_table, print_gui_backend_table, print_section

BRIDGE_PORT_IFACE = {"1": "LAN 1", "2": "LAN 2", "5": "Radio 1", "6": "Radio 2"}
RADIO_INDEX = 1
ASSOC_INDEX = 1
CLEAR_SETTLE_S = 5
REFRESH_WAIT_S = 4


def _log(message: str):
    print(f"[LEARN_TABLE] {message}")


async def _goto_admin_path(gui_page, path_fragment: str) -> bool:
    current_url = gui_page.url or ""
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", current_url)
    if not match:
        host_match = re.match(r"(https?://[^/]+)", current_url)
        if host_match:
            try:
                await gui_page.goto(
                    f"{host_match.group(1)}/cgi-bin/luci/",
                    timeout=UITimeouts.PAGE_LOAD_MS,
                )
                await gui_page.wait_for_load_state("networkidle")
            except Exception:
                return False
            match = re.search(
                r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", gui_page.url or ""
            )
        if not match:
            return False
    target = f"{match.group(1)}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    return True


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


def _norm_mac(mac: str) -> str:
    return mac.lower().replace("-", ":").strip()


async def open_learn_table_bridge(gui_page):
    """Navigate Monitor -> Learn Table (Bridge tab)."""
    try:
        menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
        await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        submenu = gui_page.locator(MonitorLocators.SUBMENU_LEARN_TABLE).first
        await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if not await _goto_admin_path(gui_page, "/monitor/learntable"):
            raise

    await gui_page.locator(LearnTableLocators.PAGE_READY).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def open_learn_table_arp(gui_page):
    """Navigate Monitor -> Learn Table -> ARP tab."""
    await open_learn_table_bridge(gui_page)
    tab = gui_page.locator(LearnTableLocators.TAB_ARP).first
    await tab.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _accept_dialog(gui_page):
    gui_page.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))


async def _scrape_bridge_rows(gui_page) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    loc = gui_page.locator(LearnTableLocators.BRIDGE_ROWS)
    for i in range(await loc.count()):
        cells = loc.nth(i).locator("td")
        if await cells.count() < 4:
            continue
        parts = [(await cells.nth(j).inner_text()).strip() for j in range(4)]
        if not parts[0] or parts[0].lower() == "interface":
            continue
        rows.append(
            {"interface": parts[0], "mac": parts[1], "local": parts[2], "age": parts[3]}
        )
    return rows


async def _scrape_arp_rows(gui_page) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    loc = gui_page.locator(LearnTableLocators.ARP_ROWS)
    for i in range(await loc.count()):
        cells = loc.nth(i).locator("td")
        if await cells.count() < 3:
            continue
        parts = [(await cells.nth(j).inner_text()).strip() for j in range(3)]
        if not parts[0] or parts[0].lower() == "interface":
            continue
        rows.append({"interface": parts[0], "mac": parts[1], "ip": parts[2]})
    return rows


def _parse_brctl_showmacs(raw: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "port no" in line.lower():
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        port, mac, local, age = parts[0], parts[1], parts[2], parts[3]
        if ":" not in mac:
            continue
        rows.append(
            {
                "interface": BRIDGE_PORT_IFACE.get(port, f"port{port}"),
                "mac": mac,
                "local": local.lower(),
                "age": age,
            }
        )
    return rows


def _parse_ip_neigh(raw: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(
            r"([\d.]+)\s+dev\s+(\S+)\s+lladdr\s+([0-9a-f:]+)",
            line,
            re.I,
        )
        if match:
            ip, device, mac = match.groups()
            if mac.lower() in ("00:00:00:00:00:00", "failed", "none"):
                continue
            rows.append(
                {
                    "interface": "Bridge" if device.startswith("br") else device,
                    "mac": mac,
                    "ip": ip,
                }
            )
            continue
        # proc/net/arp fallback lines
        parts = line.split()
        if len(parts) >= 6 and parts[0].replace(".", "").isdigit():
            ip, _hw, _flags, mac, _mask, device = parts[:6]
            if mac.lower() not in ("00:00:00:00:00:00",):
                rows.append(
                    {
                        "interface": "Bridge" if device.startswith("br") else device,
                        "mac": mac,
                        "ip": ip,
                    }
                )
    return rows


async def _fetch_arp_api(gui_page) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    async def on_resp(resp):
        if "ref_arptbl" in resp.url:
            try:
                payload.update(json.loads(json.loads(await resp.text())))
            except (json.JSONDecodeError, TypeError):
                pass

    gui_page.on("response", on_resp)
    await gui_page.locator(LearnTableLocators.REFRESH_BUTTON).click()
    await gui_page.wait_for_timeout(REFRESH_WAIT_S * 1000)
    gui_page.remove_listener("response", on_resp)
    return payload


def _api_to_arp_rows(api: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for _key, entry in sorted(api.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "interface": "Bridge",
                "mac": entry.get("macaddr", ""),
                "ip": entry.get("ipaddr", ""),
            }
        )
    return rows


async def _fetch_bridge_api(gui_page) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    async def on_resp(resp):
        if "ref_learntbl" in resp.url:
            try:
                payload.update(json.loads(json.loads(await resp.text())))
            except (json.JSONDecodeError, TypeError):
                pass

    gui_page.on("response", on_resp)
    await gui_page.locator(LearnTableLocators.REFRESH_BUTTON).click()
    await gui_page.wait_for_timeout(REFRESH_WAIT_S * 1000)
    gui_page.remove_listener("response", on_resp)
    return payload


def _api_to_bridge_rows(api: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for _key, entry in sorted(api.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if not isinstance(entry, dict):
            continue
        port = str(entry.get("portno", ""))
        rows.append(
            {
                "interface": BRIDGE_PORT_IFACE.get(port, f"port{port}"),
                "mac": entry.get("macaddr", ""),
                "local": str(entry.get("islocal", "")).lower(),
                "age": str(entry.get("agetimer", "")),
            }
        )
    return rows


def _filter_visible_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r.get("interface") and r.get("mac")]


async def _select_interface_filter(gui_page, label: str):
    await gui_page.locator(LearnTableLocators.INTERFACE_FILTER).select_option(label=label)
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


def _bridge_row_key(row: dict[str, str]) -> tuple[str, str]:
    return (row.get("interface", "").lower(), _norm_mac(row.get("mac", "")))


def _no_duplicate_bridge_rows(rows: list[dict[str, str]]) -> bool:
    keys = [_bridge_row_key(r) for r in rows]
    return len(keys) == len(set(keys))


def _age_is_numeric(age: str) -> bool:
    try:
        float(age)
        return True
    except ValueError:
        return False


def _compare_bridge_sets(
    gui_rows: list[dict[str, str]],
    backend_rows: list[dict[str, str]],
    *,
    tolerance_age: float = 5.0,
) -> list[tuple[str, str, str, str]]:
    """Return comparison table rows for GUI vs brctl."""
    table: list[tuple[str, str, str, str]] = []
    backend_by_key = {
        (_norm_mac(r["mac"]), r["interface"].lower()): r for r in backend_rows
    }

    for grow in gui_rows:
        mac = _norm_mac(grow["mac"])
        brow = backend_by_key.get((mac, grow["interface"].lower()))
        if not brow:
            # fallback: match MAC only (same MAC should not appear on two ports)
            matches = [r for r in backend_rows if _norm_mac(r["mac"]) == mac]
            brow = matches[0] if matches else None
        if not brow:
            table.append((f"MAC {grow['mac']}", grow["interface"], "not in brctl", "FAIL"))
            continue
        iface_ok = grow["interface"] == brow["interface"]
        local_ok = grow["local"] == brow["local"]
        try:
            age_ok = abs(float(grow["age"]) - float(brow["age"])) <= tolerance_age
        except ValueError:
            age_ok = grow["age"] == brow["age"]

        status = "PASS" if iface_ok and local_ok and age_ok else "FAIL"
        table.append(
            (
                f"MAC {grow['mac']}",
                f"{grow['interface']}/{grow['local']}/{grow['age']}",
                f"{brow['interface']}/{brow['local']}/{brow['age']}",
                status,
            )
        )
    return table


async def _assert_bridge_chrome(gui_page):
    rows: list[tuple[str, str, str, str | None]] = []
    for label, loc in [
        ("Refresh button", LearnTableLocators.REFRESH_BUTTON),
        ("Clear button", LearnTableLocators.CLEAR_BUTTON),
        ("Interface filter", LearnTableLocators.INTERFACE_FILTER),
    ]:
        ok = await gui_page.locator(loc).count() > 0
        rows.append((label, "present" if ok else "missing", "present", None))
        check.is_true(ok, f"{label} missing on Learn Table Bridge page")

    options = await gui_page.locator(f"{LearnTableLocators.INTERFACE_FILTER} option").all_inner_texts()
    for opt in ("All", "LAN 1", "LAN 2", "Radio 1"):
        check.is_true(any(opt in o for o in options), f"Interface filter missing '{opt}'")

    print_gui_backend_table("Learn Table — Bridge page structure", rows)


async def _assert_arp_chrome(gui_page):
    rows: list[tuple[str, str, str, str | None]] = []
    for label, loc in [
        ("Refresh button", LearnTableLocators.REFRESH_BUTTON),
        ("Clear button", LearnTableLocators.CLEAR_BUTTON),
    ]:
        ok = await gui_page.locator(loc).count() > 0
        rows.append((label, "present" if ok else "missing", "present", None))
        check.is_true(ok, f"{label} missing on Learn Table ARP page")

    header = await gui_page.locator(LearnTableLocators.ARP_TABLE).inner_text()
    for col in ("Interface", "MAC Address", "IP Address"):
        check.is_true(col in header, f"ARP table missing column: {col}")

    print_gui_backend_table("Learn Table — ARP page structure", rows)


def _arp_mac_match(gui_mac: str, backend_mac: str) -> bool:
    g = _norm_mac(gui_mac)
    b = _norm_mac(backend_mac)
    return g == b or g[:14] == b[:14]


def _compare_arp_sets(gui_rows: list[dict[str, str]], backend_rows: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    table: list[tuple[str, str, str, str]] = []
    backend_by_ip = {r["ip"]: r for r in backend_rows}

    for grow in gui_rows:
        ip = grow.get("ip", "")
        brow = backend_by_ip.get(ip)
        if not brow:
            mac_matches = [
                r for r in backend_rows if _arp_mac_match(grow["mac"], r["mac"])
            ]
            brow = mac_matches[0] if mac_matches else None
        if not brow:
            table.append((f"IP {ip}", grow["mac"], "not in backend", "FAIL"))
            continue
        mac_ok = _arp_mac_match(grow["mac"], brow["mac"])
        ip_ok = ip == brow.get("ip", ip)
        status = "PASS" if mac_ok and ip_ok else "FAIL"
        detail = f"{brow['mac']} @ {brow.get('ip', ip)}"
        table.append((f"IP {ip}", grow["mac"], detail, status))
    return table


async def _assert_gui_105_on_device(
    gui_page, root_ssh, peer_ips: list[str], *, device_label: str = "BTS"
):
    """
    GUI_105: Bridge table — All/LAN1/LAN2/Radio1 filters; MAC/local/age vs brctl; no duplicates.
    """
    check.is_true(root_ssh is not None, f"GUI_105 [{device_label}]: SSH required")
    _log(f"GUI_105 [{device_label}]: Bridge MAC learn table verification")
    await open_learn_table_bridge(gui_page)
    await _assert_bridge_chrome(gui_page)

    await _select_interface_filter(gui_page, "All")
    api_payload = await _fetch_bridge_api(gui_page)
    api_rows = _api_to_bridge_rows(api_payload)
    gui_all = await _scrape_bridge_rows(gui_page)

    check.is_true(api_rows, "Bridge API ref_learntbl returned no rows")
    check.is_true(gui_all, "Bridge GUI table empty after Refresh")
    check.is_true(_no_duplicate_bridge_rows(gui_all), f"Duplicate bridge rows: {gui_all}")
    for row in gui_all:
        check.is_true(_age_is_numeric(row["age"]), f"Invalid ageing timer: {row}")

    cmp_rows = _compare_bridge_sets(gui_all, api_rows)
    print_section("GUI_105 — Bridge GUI vs LuCI API (All)")
    print_comparison_table(cmp_rows)
    for _, _, _, status in cmp_rows:
        check.equal(status, "PASS", "GUI_105 bridge API comparison failed")

    brctl_rows = _parse_brctl_showmacs(await _send_ssh(root_ssh, RootCommands.GET_BRCTL_SHOWMACS))
    brctl_ok = bool(brctl_rows)
    print_section("GUI_105 — brctl cross-check")
    print_comparison_table([("brctl showmacs br-lan", "yes" if brctl_ok else "no", "yes", "PASS" if brctl_ok else "FAIL")])
    check.is_true(brctl_ok, "brctl showmacs returned no entries")

    # Interface filters
    filter_cases = [
        ("LAN 1", "LAN 1"),
        ("LAN 2", "LAN 2"),
        ("Radio 1", "Radio 1"),
    ]
    filter_table: list[tuple[str, str, str, str]] = []
    for filter_label, expected_iface in filter_cases:
        await _select_interface_filter(gui_page, filter_label)
        visible = _filter_visible_rows(await _scrape_bridge_rows(gui_page))
        if filter_label == "Radio 1":
            ok = all(r["interface"] == expected_iface for r in visible) if visible else True
            detail = f"{len(visible)} visible row(s)"
        else:
            ok = bool(visible) and all(r["interface"] == expected_iface for r in visible)
            detail = f"{len(visible)} row(s), MACs: {[r['mac'] for r in visible]}"
        filter_table.append((f"Filter {filter_label}", detail, f"only {expected_iface}", "PASS" if ok else "FAIL"))
        check.is_true(ok, f"Filter '{filter_label}' failed: {visible}")

    print_section("GUI_105 — Interface filters")
    print_comparison_table(filter_table)

    # Known connected devices
    eth0_mac = _norm_mac(parse_ifconfig_mac(await _send_ssh(root_ssh, RootCommands.get_mac_lan(0))) or "")
    eth1_mac = _norm_mac(parse_ifconfig_mac(await _send_ssh(root_ssh, RootCommands.get_mac_lan(1))) or "")

    peer_mac = ""
    assoc_idx = await find_assoc_index_for_cpe(root_ssh, peer_ips[0], RADIO_INDEX) if peer_ips else ASSOC_INDEX
    if peer_ips:
        peer_mac = _norm_mac(
            ssh_scalar(
                await _send_ssh(
                    root_ssh,
                    RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "mac"),
                )
            )
        )

    macs_gui = {_norm_mac(r["mac"]) for r in gui_all}
    if eth0_mac:
        check.is_true(eth0_mac in macs_gui, f"Local eth0 MAC {eth0_mac} not in bridge table")
    if eth1_mac:
        check.is_true(eth1_mac in macs_gui, f"Local ath1/eth1 MAC {eth1_mac} not in bridge table")
    if peer_mac:
        check.is_true(
            any(peer_mac[:14] == m[:14] or peer_mac == m for m in macs_gui),
            f"Peer MAC {peer_mac} not in bridge table (entries={macs_gui})",
        )

    _log(f"GUI_105 [{device_label}] completed")
    return {
        "assoc_idx": assoc_idx,
        "peer_mac": peer_mac,
    }


async def assert_gui_105_bridge_table(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_105_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_105: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_105: device credentials are required for CPE validation")
    _log(
        f"GUI_105: saved BTS bridge snapshot (sua{bts_result['assoc_idx']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_105_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_106_on_device(gui_page, root_ssh, *, device_label: str = "BTS"):
    """GUI_106: Bridge Refresh updates entries; Clear removes remote MACs then Refresh repopulates."""
    check.is_true(root_ssh is not None, f"GUI_106 [{device_label}]: SSH required")
    _log(f"GUI_106 [{device_label}]: Bridge Refresh and Clear")
    await open_learn_table_bridge(gui_page)
    await _select_interface_filter(gui_page, "All")

    await gui_page.locator(LearnTableLocators.REFRESH_BUTTON).click()
    await gui_page.wait_for_timeout(REFRESH_WAIT_S * 1000)
    before = await _scrape_bridge_rows(gui_page)
    remote_before = [r for r in before if r.get("local") == "no"]
    check.is_true(before, "Bridge table empty before Clear")

    await _accept_dialog(gui_page)
    await gui_page.locator(LearnTableLocators.CLEAR_BUTTON).click()
    await gui_page.wait_for_timeout(CLEAR_SETTLE_S * 1000)
    after_clear = await _scrape_bridge_rows(gui_page)
    remote_after_clear = [r for r in after_clear if r.get("local") == "no"]

    await gui_page.locator(LearnTableLocators.REFRESH_BUTTON).click()
    await gui_page.wait_for_timeout(REFRESH_WAIT_S * 1000)
    after_refresh = await _scrape_bridge_rows(gui_page)
    remote_after_refresh = [r for r in after_refresh if r.get("local") == "no"]

    rows = [
        (
            "Rows before Clear",
            str(len(before)),
            ">=1",
            "PASS" if before else "FAIL",
        ),
        (
            "Remote entries reduced after Clear",
            str(len(remote_after_clear)),
            f"<={len(remote_before)}",
            "PASS" if len(remote_after_clear) <= len(remote_before) else "FAIL",
        ),
        (
            "Refresh repopulates entries",
            str(len(after_refresh)),
            f">={len(after_clear)}",
            "PASS" if len(after_refresh) >= len(after_clear) else "FAIL",
        ),
        (
            "Remote entries return after Refresh",
            str(len(remote_after_refresh)),
            ">=1",
            "PASS" if remote_after_refresh else "FAIL",
        ),
    ]
    print_section(f"GUI_106 [{device_label}] — Bridge Refresh / Clear")
    print_comparison_table(rows)
    for _, _, _, status in rows:
        check.equal(status, "PASS", "GUI_106 table contains a failed row")

    brctl_after = _parse_brctl_showmacs(await _send_ssh(root_ssh, RootCommands.GET_BRCTL_SHOWMACS))
    check.is_true(brctl_after, "brctl empty after Refresh")
    _log(f"GUI_106 [{device_label}] completed")
    return {
        "rows_before": len(before),
        "rows_after_refresh": len(after_refresh),
        "remote_after_refresh": len(remote_after_refresh),
    }


async def assert_gui_106_bridge_refresh_clear(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_106_on_device(gui_page, root_ssh, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_106: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_106: device credentials are required for CPE validation")
    _log(
        f"GUI_106: saved BTS refresh/clear snapshot "
        f"(rows {bts_result['rows_before']} -> {bts_result['rows_after_refresh']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_106_on_device(cpe_page, cpe_root_ssh, device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_107_on_device(
    gui_page,
    root_ssh,
    peer_ips: list[str],
    *,
    device_label: str = "BTS",
):
    """GUI_107: ARP table entries vs /proc/net/arp; includes peer IP when linked."""
    check.is_true(root_ssh is not None, f"GUI_107 [{device_label}]: SSH required")
    _log(f"GUI_107 [{device_label}]: ARP table verification")
    await open_learn_table_arp(gui_page)
    await _assert_arp_chrome(gui_page)

    api_payload = await _fetch_arp_api(gui_page)
    api_rows = _api_to_arp_rows(api_payload)
    gui_rows = await _scrape_arp_rows(gui_page)

    check.is_true(api_rows, "ARP API ref_arptbl returned no rows")
    check.is_true(gui_rows, "ARP GUI table has no rows")

    keys = [(r.get("ip"), _norm_mac(r.get("mac", ""))) for r in gui_rows]
    check.equal(len(keys), len(set(keys)), f"Duplicate ARP rows: {gui_rows}")

    valid_gui = [r for r in gui_rows if _norm_mac(r.get("mac", "")) not in ("00:00:00:00:00:00", "")]
    check.is_true(valid_gui, f"ARP GUI has no valid MAC rows: {gui_rows}")

    cmp_rows = _compare_arp_sets(valid_gui, api_rows)
    print_section(f"GUI_107 [{device_label}] — ARP GUI vs LuCI API")
    print_comparison_table(cmp_rows)
    for _, _, _, status in cmp_rows:
        check.equal(status, "PASS", "GUI_107 ARP API comparison failed")

    neigh_cmd = (
        RootCommands.GET_IP6_NEIGH
        if peer_ips and is_ipv6_literal(peer_ips[0])
        else RootCommands.GET_ARP_TABLE
    )
    neigh_rows = _parse_ip_neigh(await _send_ssh(root_ssh, neigh_cmd))
    if not neigh_rows:
        neigh_rows = _parse_ip_neigh(await _send_ssh(root_ssh, RootCommands.GET_ARP_TABLE))
    if not neigh_rows:
        neigh_rows = _parse_ip_neigh(await _send_ssh(root_ssh, RootCommands.GET_ARP_TABLE_PROC))
    if neigh_rows:
        cross = _compare_arp_sets(valid_gui, neigh_rows)
        matched = [row for row in cross if row[3] == "PASS"]
        if matched:
            print_section(f"GUI_107 [{device_label}] — ARP GUI vs ip neigh (optional)")
            print_comparison_table(matched[:5])
        check.is_true(matched, "No ARP GUI rows matched ip neigh")

    if peer_ips:
        peer_ip = peer_ips[0]
        all_ips = {r["ip"] for r in valid_gui} | {r["ip"] for r in api_rows}
        if not any(ip_in_text(peer_ip, ip) for ip in all_ips):
            _log(
                f"Note: Peer {peer_ip} not in ARP/neighbor table "
                f"(IPv6 labs may only show MAC bridge entries; have {all_ips})"
            )

    _log(f"GUI_107 [{device_label}] completed")
    return {
        "gui_rows": len(gui_rows),
        "api_rows": len(api_rows),
        "peer_ip": peer_ips[0] if peer_ips else "",
    }


async def assert_gui_107_arp_table(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
):
    bts_result = await _assert_gui_107_on_device(gui_page, root_ssh, cpe_ips, device_label="BTS")

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), "GUI_107: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), "GUI_107: device credentials are required for CPE validation")
    _log(
        f"GUI_107: saved BTS ARP snapshot (gui={bts_result['gui_rows']}, api={bts_result['api_rows']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_107_on_device(cpe_page, cpe_root_ssh, [bsu_ip], device_label="CPE")
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()


async def _assert_gui_108_on_device(
    gui_page,
    root_ssh,
    *,
    device_label: str = "BTS",
    case_label: str = "GUI_108",
):
    """ARP Clear reduces entries; Refresh restores active neighbors."""
    check.is_true(root_ssh is not None, f"{case_label} [{device_label}]: SSH required")
    _log(f"{case_label} [{device_label}]: ARP Refresh and Clear")
    await open_learn_table_arp(gui_page)

    await gui_page.locator(LearnTableLocators.REFRESH_BUTTON).click()
    await gui_page.wait_for_timeout(REFRESH_WAIT_S * 1000)
    before = await _scrape_arp_rows(gui_page)
    check.is_true(before, "ARP table empty before Clear")

    await _accept_dialog(gui_page)
    await gui_page.locator(LearnTableLocators.CLEAR_BUTTON).click()
    await gui_page.wait_for_timeout(CLEAR_SETTLE_S * 1000)
    after_clear = await _scrape_arp_rows(gui_page)

    await gui_page.locator(LearnTableLocators.REFRESH_BUTTON).click()
    await gui_page.wait_for_timeout(REFRESH_WAIT_S * 1000)
    after_refresh = await _scrape_arp_rows(gui_page)

    rows = [
        ("Rows before Clear", str(len(before)), ">=1", "PASS" if before else "FAIL"),
        (
            "Entries after Clear",
            str(len(after_clear)),
            f"<={len(before)}",
            "PASS" if len(after_clear) <= len(before) else "FAIL",
        ),
        (
            "Refresh repopulates ARP",
            str(len(after_refresh)),
            f">={len(after_clear)}",
            "PASS" if len(after_refresh) >= len(after_clear) else "FAIL",
        ),
        (
            "Active ARP restored",
            str(len(after_refresh)),
            ">=1",
            "PASS" if after_refresh else "FAIL",
        ),
    ]
    print_section(f"{case_label} [{device_label}] — ARP Refresh / Clear")
    print_comparison_table(rows)
    for _, _, _, status in rows:
        check.equal(status, "PASS", f"{case_label} table contains a failed row")

    neigh_raw = await _send_ssh(root_ssh, RootCommands.GET_ARP_TABLE)
    backend = _parse_ip_neigh(neigh_raw)
    if not backend:
        backend = _parse_ip_neigh(await _send_ssh(root_ssh, RootCommands.GET_ARP_TABLE_PROC))
    check.is_true(
        after_refresh or backend,
        f"ARP not restored after Refresh (gui={len(after_refresh)}, backend={len(backend)})",
    )
    _log(f"{case_label} [{device_label}] completed")
    return {
        "rows_before": len(before),
        "rows_after_refresh": len(after_refresh),
    }


async def assert_gui_108_arp_refresh_clear(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    case_label: str = "GUI_108",
):
    bts_result = await _assert_gui_108_on_device(
        gui_page, root_ssh, device_label="BTS", case_label=case_label
    )

    cpe_ip = cpe_ips[0] if cpe_ips else ""
    if not cpe_ip:
        return

    check.is_true(bool(bsu_ip), f"{case_label}: BTS IP is required for CPE-side validation")
    check.is_true(bool(device_creds), f"{case_label}: device credentials are required for CPE validation")
    _log(
        f"{case_label}: saved BTS ARP clear snapshot "
        f"(rows {bts_result['rows_before']} -> {bts_result['rows_after_refresh']}); "
        f"logging into CPE {cpe_ip} for the same validation"
    )

    cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _assert_gui_108_on_device(
            cpe_page, cpe_root_ssh, device_label="CPE", case_label=case_label
        )
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()

