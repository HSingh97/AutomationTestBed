"""ARP & Bridge Table plan cases (ARPBRIDGE_01–14) — IPv4 lab (ARP / ip neigh)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time

import asyncssh
import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from utils.learn_table_flows import _parse_brctl_showmacs, _parse_ip_neigh
from utils.monitor_assoc import find_assoc_index_for_cpe
from utils.net_utils import ips_equal, is_ipv6_literal, normalize_ip
from utils.parsers import clean_ssh_output, ssh_scalar
from utils.verify_output import print_comparison_table, print_section

RADIO_INDEX = 1
_DEBUG_LOG = "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-065ec6.log"


def _log(message: str) -> None:
    print(f"[ARP_BRIDGE] {message}")


def _debug_log(
    location: str,
    message: str,
    data: dict,
    *,
    hypothesis_id: str,
    run_id: str = "pre-fix",
) -> None:
    # region agent log
    entry = {
        "sessionId": "065ec6",
        "location": location,
        "message": message,
        "data": data,
        "hypothesisId": hypothesis_id,
        "runId": run_id,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    # endregion


def _require_ipv4(ip: str, *, case_id: str, role: str) -> str:
    clean = normalize_ip(ip)
    check.is_true(clean, f"{case_id}: {role} IP required")
    check.is_true(
        not is_ipv6_literal(clean),
        f"{case_id}: IPv4-only; got {clean}",
    )
    return clean


async def _ssh(root_ssh, command: str) -> str:
    response = await root_ssh.send_command(command)
    return clean_ssh_output(response.result)


def _norm_mac(mac: str) -> str:
    return mac.lower().replace("-", ":").strip()


def _mac_match(left_mac: str, right_mac: str) -> bool:
    g = _norm_mac(left_mac)
    b = _norm_mac(right_mac)
    return bool(g and b and g == b)


def _mac_related(left_mac: str, right_mac: str) -> bool:
    if _mac_match(left_mac, right_mac):
        return True
    g = _norm_mac(left_mac)
    b = _norm_mac(right_mac)
    return bool(g and b and len(g) >= 14 and len(b) >= 14 and g[:14] == b[:14])


def _ping_success(raw: str) -> bool:
    lower = raw.lower()
    if "100% packet loss" in lower:
        return False
    if "bytes from" in lower:
        return True
    if "round-trip min/avg/max" in lower:
        return True
    return "0% packet loss" in lower


def _ip_neigh_cmd_failed(raw: str) -> bool:
    lower = (raw or "").lower()
    return any(
        token in lower
        for token in ("not complete", "rtnetlink", "error:", "invalid", "failed", "usage:", "unknown")
    )


def _ping_cmd(peer_ip: str, *, count: int = 4) -> str:
    return f"ping -c {count} -W 3 {peer_ip} 2>&1"


def _neigh_prefix() -> str:
    return "ip neigh"


async def _read_arp_neigh_rows(root_ssh, peer_ip: str) -> list[dict[str, str]]:
    prefix = _neigh_prefix()
    targeted = _parse_ip_neigh(await _ssh(root_ssh, f"{prefix} show {peer_ip} 2>/dev/null"))
    all_rows = _parse_ip_neigh(await _ssh(root_ssh, RootCommands.GET_ARP_TABLE))
    if not all_rows:
        all_rows = _parse_ip_neigh(await _ssh(root_ssh, RootCommands.GET_ARP_TABLE_PROC))
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in targeted + all_rows:
        key = (row.get("ip", ""), _norm_mac(row.get("mac", "")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _peer_ssh_relay_command(host: str, password: str, inner_command: str) -> str:
    escaped = inner_command.replace("'", "'\"'\"'")
    return (
        f"sshpass -p '{password}' ssh -T -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 "
        f"root@{normalize_ip(host)} '{escaped}'"
    )


def _cpe_ssh_relay_command(cpe_ip: str, password: str, inner_command: str) -> str:
    return _peer_ssh_relay_command(cpe_ip, password, inner_command)


def _extract_ipv4_from_text(raw: str) -> str:
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw or "")
    return normalize_ip(match.group(0)) if match else ""


async def _read_lan_proto(ssh) -> str:
    proto = ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_PROTO)).lower()
    if proto in ("static", "dhcp", "pppoe"):
        return proto
    # Fresh SSH sessions may still have MOTD on the first command; re-read once.
    return ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_PROTO)).lower()


async def _snapshot_lan_uci(ssh) -> dict[str, str]:
    return {
        "proto": await _read_lan_proto(ssh),
        "ipaddr": ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_IP)),
        "netmask": ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_MASK)),
        "gateway": ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_GW)),
    }


async def _ensure_bts_dhcp_server(root_ssh) -> None:
    """
    Ensure BTS DHCP server isn't disabled.
    (DHCP may still hand out the same IP due to reservations — that's OK for ARPBRIDGE_06.)
    """
    ignore = ssh_scalar(await _ssh(root_ssh, "uci get dhcp.lan.ignore 2>/dev/null"))
    if ignore.strip() == "1":
        _log("ARPBRIDGE_06: enabling BTS DHCP server on LAN")
        await _ssh(
            root_ssh,
            "uci set dhcp.lan.ignore='0'; uci commit dhcp; /etc/init.d/dnsmasq restart 2>/dev/null",
        )
        await asyncio.sleep(3)


async def _set_dynamic_ipv4_ssh(ssh) -> None:
    """Set dhcp in UCI and renew lease without a blocking network reload."""
    await _ssh(ssh, "uci set network.lan.proto='dhcp'")
    await _ssh(ssh, "uci commit network")
    await _ssh(
        ssh,
        "(udhcpc -i br-lan -n -q -t 8 >/tmp/arpbridge06_udhcpc.log 2>&1 &) && "
        "sleep 2 && echo ARPBRIDGE06_DHCP_APPLIED",
    )


async def _cpe_remote_exec(root_ssh, command: str, *, su_index: int = 1) -> str:
    escaped = command.replace('"', '\\"')
    return await _ssh(root_ssh, f'/usr/sbin/remote_exec.sh {su_index} "{escaped}"')


async def _ensure_lab_static_ips(root_ssh, bts_ip: str, cpe_ip: str) -> None:
    """Restore BTS .10 and CPE .11 on br-lan when DHCP tests left only fallback aliases."""
    bts_ip = normalize_ip(bts_ip)
    cpe_ip = normalize_ip(cpe_ip)
    bts_addrs = await _ssh(root_ssh, "ip -4 addr show dev br-lan 2>/dev/null")
    bts_uci_ip = ssh_scalar(await _ssh(root_ssh, "uci get network.lan.ipaddr 2>/dev/null"))
    bts_has = bts_ip in bts_addrs
    bts_has_cpe_ip = cpe_ip in bts_addrs or bts_uci_ip == cpe_ip

    if not bts_has or bts_has_cpe_ip:
        del_cpe = f"ip addr del {cpe_ip}/24 dev br-lan 2>/dev/null; " if bts_has_cpe_ip else ""
        await _ssh(
            root_ssh,
            f"uci set network.lan.proto='static'; "
            f"uci set network.lan.ipaddr='{bts_ip}'; "
            f"uci set network.lan.netmask='255.255.255.0'; "
            f"uci commit network; "
            f"{del_cpe}"
            f"ip addr add {bts_ip}/24 dev br-lan 2>/dev/null || "
            f"ip addr replace {bts_ip}/24 dev br-lan",
        )
        _log(f"ARPBRIDGE: restored BTS lab IP {bts_ip} on br-lan")

    ping_ok, _ = await _ping_peer(root_ssh, cpe_ip, attempts=3)
    if not ping_ok:
        await _restore_cpe_via_bts_remote_exec(root_ssh, cpe_ip, bts_ip)
        try:
            await _cpe_remote_exec(root_ssh, f"ip addr add {cpe_ip}/24 dev br-lan")
        except Exception as exc:
            _log(f"ARPBRIDGE: CPE ip addr add via remote_exec skipped ({exc})")
        await asyncio.sleep(3)
        _log(f"ARPBRIDGE: pushed CPE static restore for {cpe_ip} via BTS remote_exec")


async def _assert_cpe_role_before_dhcp(root_ssh, cpe_ip: str) -> None:
    """Fail fast when BTS is answering at the CPE management IP (lab mis-wired)."""
    cpe_ip = normalize_ip(cpe_ip)
    try:
        mode = ssh_scalar(
            await _cpe_remote_exec(root_ssh, "uci get wireless.@wifi-iface[0].mode 2>/dev/null")
        ).lower()
    except Exception as exc:
        _log(f"ARPBRIDGE_06: CPE role check skipped ({exc})")
        return
    if mode == "ap":
        raise RuntimeError(
            f"ARPBRIDGE_06: device at {cpe_ip} is BTS (ap), not CPE — "
            "restore lab static IPs before running DHCP test"
        )


async def _get_br_lan_mac(ssh) -> str:
    raw = await _ssh(ssh, "cat /sys/class/net/br-lan/address 2>/dev/null")
    return _norm_mac(raw)


async def _parse_bts_dhcp_lease_for_mac(root_ssh, cpe_mac: str) -> str:
    cpe_mac = _norm_mac(cpe_mac)
    if not cpe_mac:
        return ""
    leases_raw = await _ssh(
        root_ssh,
        "cat /tmp/dhcp.leases 2>/dev/null; "
        "cat /var/lib/misc/dnsmasq.leases 2>/dev/null; "
        "cat /var/dhcp.leases 2>/dev/null",
    )
    for line in leases_raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        lease_mac = _norm_mac(parts[1])
        lease_ip = normalize_ip(parts[2])
        if lease_ip and (lease_mac == cpe_mac or _mac_related(lease_mac, cpe_mac)):
            return lease_ip
    return ""


async def _restore_lan_uci_snapshot(ssh, snap: dict[str, str], *, device_label: str) -> None:
    proto = (snap.get("proto") or "static").strip() or "static"
    _log(f"ARPBRIDGE [{device_label}]: restore network.lan.proto={proto}")
    await _ssh(ssh, f"uci set network.lan.proto='{proto}'")
    if proto == "static":
        for key in ("ipaddr", "netmask", "gateway"):
            val = (snap.get(key) or "").strip()
            if val and "not found" not in val.lower() and "uci:" not in val.lower():
                await _ssh(ssh, f"uci set network.lan.{key}='{val}'")
    await _ssh(ssh, "uci commit network")
    ip = _extract_ipv4_from_text(snap.get("ipaddr", ""))
    if proto == "static" and ip:
        await _ssh(
            ssh,
            f"ip addr flush dev br-lan 2>/dev/null; ip addr add {ip}/24 dev br-lan 2>/dev/null; "
            f"echo RESTORED_{ip}",
        )


async def _restore_cpe_via_bts_remote_exec(
    root_ssh,
    static_cpe_ip: str,
    bts_ip: str,
    *,
    su_index: int = 1,
) -> bool:
    """Push static LAN UCI to CPE over RF when LAN SSH to .11 is down."""
    static_cpe_ip = normalize_ip(static_cpe_ip)
    bts_ip = normalize_ip(bts_ip)
    cmds = [
        "uci set network.lan.proto='static'",
        f"uci set network.lan.ipaddr='{static_cpe_ip}'",
        "uci set network.lan.netmask='255.255.255.0'",
        f"uci set network.lan.gateway='{bts_ip}'",
        "uci commit network",
    ]
    for inner in cmds:
        escaped = inner.replace('"', '\\"')
        raw = await _ssh(root_ssh, f'/usr/sbin/remote_exec.sh {su_index} "{escaped}"')
        if _ip_neigh_cmd_failed(raw) and "usage:" in raw.lower():
            _log(f"ARPBRIDGE: remote_exec restore failed on: {inner[:60]}")
            return False
    return True


async def restore_cpe_static_lab_ip(
    root_ssh,
    static_cpe_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    uci_snap: dict[str, str] | None = None,
    soft: bool = False,
    gui_browser=None,
    dhcp_cpe_ip: str = "",
) -> bool:
    """
    Force CPE back to static lab IPv4 (for tests after ARPBRIDGE_06 DHCP).
    Returns True when BTS can ping the original CPE management IP.
    """
    static_cpe_ip = _require_ipv4(static_cpe_ip, case_id="ARPBRIDGE", role="CPE")
    bts_ip = _require_ipv4(bts_ip, case_id="ARPBRIDGE", role="BTS")
    snap = {
        "proto": "static",
        "ipaddr": static_cpe_ip,
        "netmask": "255.255.255.0",
        "gateway": bts_ip,
    }
    if uci_snap:
        snap.update({k: v for k, v in uci_snap.items() if v})
    snap["proto"] = "static"
    snap["ipaddr"] = static_cpe_ip

    _log(f"ARPBRIDGE: restore CPE static lab IP {static_cpe_ip}")
    restored = False

    if gui_browser is not None and uci_snap:
        from utils.cpe_session import open_cpe_gui_session_if_reachable

        for host in (dhcp_cpe_ip, static_cpe_ip):
            if not host:
                continue
            restore_gui = await open_cpe_gui_session_if_reachable(
                gui_browser, host, device_creds, require_summary=False
            )
            if restore_gui is None:
                continue
            try:
                await _restore_static_ipv4_gui(restore_gui, snap)
                restored = True
                break
            except Exception as exc:
                _log(f"ARPBRIDGE: GUI restore via {host} failed ({exc})")
            finally:
                await restore_gui.close()

    for host in await _cpe_ssh_candidates(root_ssh, static_cpe_ip, extra_hosts=[dhcp_cpe_ip]):
        restore_ssh = None
        try:
            restore_ssh = await _open_root_ssh_for_host(host, device_creds)
            await _restore_lan_uci_snapshot(restore_ssh, snap, device_label="CPE")
            restored = True
            break
        except Exception as exc:
            _log(f"ARPBRIDGE: restore via direct@{host} failed ({exc})")
        finally:
            if restore_ssh is not None:
                await _close_ssh_session(restore_ssh)
        relay = await _open_cpe_ssh_via_bts_relay(root_ssh, host, device_creds)
        if not relay:
            continue
        restore_ssh, _ = relay
        try:
            await _restore_lan_uci_snapshot(restore_ssh, snap, device_label="CPE")
            restored = True
            break
        except Exception as exc:
            _log(f"ARPBRIDGE: restore via BTS relay@{host} failed ({exc})")

    if not restored:
        restored = await _restore_cpe_via_bts_remote_exec(root_ssh, static_cpe_ip, bts_ip)
        if restored:
            _log("ARPBRIDGE: restored CPE static IP via BTS remote_exec")

    await asyncio.sleep(5)
    ping_ok, _ = await _ping_peer(root_ssh, static_cpe_ip, attempts=4)
    ok = restored and ping_ok
    if not ok:
        _log(
            f"ARPBRIDGE: restore status restored={restored} ping={ping_ok} "
            f"(target {static_cpe_ip})"
        )
    if not soft:
        check.is_true(restored, f"ARPBRIDGE: CPE static restore SSH failed for {static_cpe_ip}")
        check.is_true(ping_ok, f"ARPBRIDGE: BTS cannot ping restored CPE {static_cpe_ip}")
    return ok


async def _select_ipv4_proto_option(gui_page, *, want: str) -> tuple[str, str]:
    """Pick Address type dropdown value; want is 'dhcp' or 'static'."""
    from pages.locators import NetworkLocators

    element = gui_page.locator(NetworkLocators.IPv4_PROTO).first
    await element.wait_for(state="visible", timeout=15000)
    options = await element.evaluate(
        "el => Array.from(el.options).map(o => ({text: (o.text||'').trim(), value: (o.value||'').trim()}))"
    )
    value = ""
    label = ""
    for opt in options:
        text_l = opt["text"].lower()
        value_l = opt["value"].lower()
        if want == "dhcp" and (value_l == "dhcp" or ("dynamic" in text_l and "ipv4" in text_l)):
            value = opt["value"]
            label = opt["text"]
            break
        if want == "static" and (value_l == "static" or ("static" in text_l and "ipv4" in text_l)):
            value = opt["value"]
            label = opt["text"]
            break
    check.is_true(value, f"ARPBRIDGE_06: {want} IPv4 option not found in GUI: {options}")
    await element.select_option(value=value)
    return value, label or want


async def _apply_ip_config_gui(gui_page, *, settle_seconds: int = 25) -> None:
    """Save → Apply → Confirm (CONFIGURATION CHANGES: network.lan.proto = …)."""
    from pages.locators import NetworkLocators
    from utils.network_flows import apply_triple

    await apply_triple(
        gui_page,
        NetworkLocators.SAVE_BUTTON,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=settle_seconds,
    )


async def _set_dynamic_ipv4_gui(gui_page) -> str:
    """Network > IP Configuration: Address type = Dynamic IPv4 → Apply."""
    from utils.network_assertions import _open_ip_config
    from utils.ui_helpers import attach_dialog_handler

    await _open_ip_config(gui_page)
    attach_dialog_handler(gui_page)
    _dhcp_value, dhcp_label = await _select_ipv4_proto_option(gui_page, want="dhcp")
    await _apply_ip_config_gui(gui_page, settle_seconds=25)
    _log("ARPBRIDGE_06: applied network.lan.proto=dhcp via GUI")
    return dhcp_label or "Dynamic IPv4 (dhcp)"


async def _restore_static_ipv4_gui(gui_page, snap: dict[str, str]) -> str:
    """Network > IP Configuration: restore Static IPv4 + original IP/gateway → Apply."""
    from pages.locators import NetworkLocators
    from utils.network_assertions import _open_ip_config
    from utils.ui_helpers import attach_dialog_handler

    await _open_ip_config(gui_page)
    attach_dialog_handler(gui_page)
    _static_value, static_label = await _select_ipv4_proto_option(gui_page, want="static")

    ip = _extract_ipv4_from_text(snap.get("ipaddr", ""))
    netmask = (snap.get("netmask") or "255.255.255.0").strip()
    gateway = _extract_ipv4_from_text(snap.get("gateway", ""))

    if ip:
        addr = gui_page.locator(NetworkLocators.IPv4_ADDRESS).first
        await addr.wait_for(state="visible", timeout=10000)
        await addr.fill(ip)
    if netmask and "not found" not in netmask.lower():
        mask = gui_page.locator(NetworkLocators.IPv4_NETMASK).first
        await mask.wait_for(state="visible", timeout=10000)
        await mask.fill(netmask)
    if gateway:
        gw = gui_page.locator(NetworkLocators.IPv4_GATEWAY).first
        await gw.wait_for(state="visible", timeout=10000)
        await gw.fill(gateway)

    await _apply_ip_config_gui(gui_page, settle_seconds=25)
    _log(f"ARPBRIDGE_06: restored Static IPv4 {ip or 'n/a'} via GUI")
    return static_label or "Static IPv4"


async def _wait_cpe_dhcp_ready(
    root_ssh,
    static_cpe_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    timeout_s: float = 90.0,
) -> tuple[str, str]:
    """Poll until CPE has a reachable DHCP IPv4 (same IP reservation is OK)."""
    static_cpe_ip = normalize_ip(static_cpe_ip)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s

    while loop.time() < deadline:
        _idx, cpe_mac, link_ip = await _resolve_link_partner(root_ssh, static_cpe_ip)
        lease_ip = await _parse_bts_dhcp_lease_for_mac(root_ssh, cpe_mac)
        for cand in (static_cpe_ip, lease_ip, link_ip):
            if not _valid_cpe_dhcp_ip(cand, static_cpe_ip, bts_ip):
                continue
            ping_ok, _ = await _ping_peer(root_ssh, cand, attempts=2)
            if ping_ok:
                source = "dhcp_lease" if cand == lease_ip else "ping"
                return cand, source
        await asyncio.sleep(4)

    return "", "timeout"


async def _cpe_ssh_candidates(
    root_ssh,
    static_cpe_ip: str,
    *,
    extra_hosts: list[str] | None = None,
) -> list[str]:
    static_cpe_ip = normalize_ip(static_cpe_ip)
    candidates: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        host = normalize_ip(_extract_ipv4_from_text(host) or host)
        if host and host not in seen:
            seen.add(host)
            candidates.append(host)

    for host in extra_hosts or []:
        add(host)
    add(static_cpe_ip)
    _idx, _mac, link_ip = await _resolve_link_partner(root_ssh, static_cpe_ip)
    add(link_ip)
    return candidates


async def _open_cpe_ssh_for_arpbridge(
    root_ssh,
    cpe_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    extra_hosts: list[str] | None = None,
) -> tuple[object, str]:
    errors: list[str] = []
    for host in await _cpe_ssh_candidates(
        root_ssh,
        cpe_ip,
        extra_hosts=extra_hosts,
    ):
        relay = await _open_cpe_ssh_via_bts_relay(root_ssh, host, device_creds)
        if relay:
            return relay
        try:
            return await _open_cpe_ssh(root_ssh, host, bts_ip, device_creds)
        except RuntimeError as exc:
            errors.append(f"{host}: {exc}")
    raise RuntimeError("CPE SSH failed for all candidates: " + " | ".join(errors))


def _valid_cpe_dhcp_ip(cand: str, static_cpe_ip: str, bts_ip: str) -> bool:
    """DHCP address must be a usable IPv4 in the same /24 as the lab static IP."""
    cand = normalize_ip(cand)
    static_cpe_ip = normalize_ip(static_cpe_ip)
    bts_ip = normalize_ip(bts_ip)
    if not cand or is_ipv6_literal(cand) or cand in {bts_ip, "0.0.0.0"}:
        return False
    parts = cand.split(".")
    static_parts = static_cpe_ip.split(".")
    if len(parts) != 4 or len(static_parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(o < 1 or o > 254 for o in octets):
        return False
    return parts[:3] == static_parts[:3]


async def _discover_dhcp_peer_ip(
    root_ssh,
    static_peer_ip: str,
    bts_ip: str,
    *,
    cpe_mac: str,
    timeout_s: float = 90.0,
) -> tuple[str, str, dict]:
    """Return CPE DHCP IPv4 with evidence source (dhcp_lease or ucidyn)."""
    static_peer_ip = normalize_ip(static_peer_ip)
    bts_ip = normalize_ip(bts_ip)
    cpe_mac = _norm_mac(cpe_mac)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last_details: dict = {"cpe_mac": cpe_mac}

    while loop.time() < deadline:
        lease_ip = await _parse_bts_dhcp_lease_for_mac(root_ssh, cpe_mac)
        if lease_ip and _valid_cpe_dhcp_ip(lease_ip, static_peer_ip, bts_ip):
            ping_ok, _ = await _ping_peer(root_ssh, lease_ip, attempts=2)
            if ping_ok:
                details = {"lease_ip": lease_ip, "cpe_mac": cpe_mac}
                _debug_log(
                    "arp_bridge_table_flows.py:_discover_dhcp_peer_ip",
                    "dhcp_lease",
                    details,
                    hypothesis_id="A",
                )
                return lease_ip, "dhcp_lease", details

        # Fallback: if lease file is empty, accept the static peer IP as long as
        # it is pingable; DHCP reservations may keep the same IP.
        ping_ok, _ = await _ping_peer(root_ssh, static_peer_ip, attempts=2)
        if ping_ok:
            details = {"lease_ip": lease_ip, "cpe_mac": cpe_mac}
            _debug_log(
                "arp_bridge_table_flows.py:_discover_dhcp_peer_ip",
                "dhcp_reserved_same_ip",
                details,
                hypothesis_id="B",
            )
            return static_peer_ip, "dhcp_reserved_same_ip", details

        last_details = {"lease_ip": lease_ip, "cpe_mac": cpe_mac}
        await asyncio.sleep(3)

    return "", "timeout", last_details


async def _flush_neigh_for_ip(root_ssh, peer_ip: str) -> None:
    dev = await _neigh_dev_for_peer(root_ssh, peer_ip)
    if dev:
        await _ssh(root_ssh, f"{_neigh_prefix()} del {peer_ip} dev {dev} 2>/dev/null")


async def _close_ssh_session(ssh) -> None:
    close_fn = getattr(ssh, "close", None)
    if callable(close_fn):
        result = close_fn()
        if asyncio.iscoroutine(result):
            await result


class _CpeViaBtsSshpass:
    def __init__(self, bts_ssh, cpe_ip: str, password: str):
        self._bts_ssh = bts_ssh
        self._cpe_ip = normalize_ip(cpe_ip)
        self._password = password

    async def send_command(self, command: str, **kwargs):
        return await self._bts_ssh.send_command(
            _cpe_ssh_relay_command(self._cpe_ip, self._password, command), **kwargs
        )

    async def close(self) -> None:
        return None


class _CpeViaAsyncSshTunnel:
    _CMD_TIMEOUT_S = 60

    def __init__(self, cpe_conn, bts_conn):
        self._cpe_conn = cpe_conn
        self._bts_conn = bts_conn

    async def send_command(self, command: str, **kwargs):
        timeout_s = kwargs.get("timeout", self._CMD_TIMEOUT_S)
        try:
            result = await asyncio.wait_for(
                self._cpe_conn.run(command, check=False),
                timeout=timeout_s,
            )
            text = (result.stdout or "") + (result.stderr or "")
        except asyncio.TimeoutError:
            text = f"ERROR: CPE command timed out after {timeout_s}s"

        class _Resp:
            result = text

        return _Resp()

    async def close(self) -> None:
        for conn in (self._cpe_conn, self._bts_conn):
            try:
                conn.close()
            except Exception:
                pass


async def _open_cpe_ssh_via_bts_relay(root_ssh, cpe_ip: str, device_creds: dict) -> tuple[object, str] | None:
    """CPE shell on BTS via sshpass (stable when CPE mgmt IP changed on LAN)."""
    password = device_creds["pass"]
    sshpass_path = clean_ssh_output(
        (await root_ssh.send_command("command -v sshpass 2>/dev/null || which sshpass 2>/dev/null")).result
    )
    if not sshpass_path.strip():
        return None
    return _CpeViaBtsSshpass(root_ssh, cpe_ip, password), "bts_relay"


async def _open_cpe_ssh(root_ssh, cpe_ip: str, bts_ip: str, device_creds: dict) -> tuple[object, str]:
    password = device_creds["pass"]
    cpe_norm = normalize_ip(cpe_ip)
    bts_norm = normalize_ip(bts_ip)

    relay = await _open_cpe_ssh_via_bts_relay(root_ssh, cpe_norm, device_creds)
    if relay:
        return relay

    direct_error: Exception | None = None
    try:
        return await _open_root_ssh_for_host(cpe_ip, device_creds), "direct"
    except Exception as exc:
        direct_error = exc
        _log(f"ARPBRIDGE: direct CPE SSH failed ({exc}); trying asyncssh tunnel")

    last_exc: Exception | None = None
    for attempt in range(1, 3):
        bts_conn = None
        try:
            bts_conn = await asyncssh.connect(
                bts_norm, username="root", password=password, known_hosts=None, connect_timeout=15
            )
            cpe_conn = await asyncssh.connect(
                cpe_norm,
                username="root",
                password=password,
                known_hosts=None,
                connect_timeout=15,
                tunnel=bts_conn,
            )
            probe = (await cpe_conn.run("echo ARPBRIDGE_TUNNEL_OK", check=False)).stdout or ""
            if "ARPBRIDGE_TUNNEL_OK" not in probe:
                raise RuntimeError(f"asyncssh tunnel probe failed: {probe[:80]!r}")
            return _CpeViaAsyncSshTunnel(cpe_conn, bts_conn), "asyncssh_tunnel"
        except (asyncssh.Error, OSError, RuntimeError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if bts_conn is not None:
                bts_conn.close()
            await asyncio.sleep(2)

    sshpass_path = clean_ssh_output(
        (await root_ssh.send_command("command -v sshpass 2>/dev/null || which sshpass 2>/dev/null")).result
    )
    if sshpass_path.strip():
        relay = _CpeViaBtsSshpass(root_ssh, cpe_ip, password)
        probe = clean_ssh_output((await relay.send_command("echo ARPBRIDGE_RELAY_OK")).result)
        if "ARPBRIDGE_RELAY_OK" in probe:
            return relay, "sshpass_relay"

    raise RuntimeError(
        f"CPE access failed: direct ({direct_error}); asyncssh tunnel ({last_exc}); "
        f"sshpass on BTS={'yes' if sshpass_path.strip() else 'no'}."
    )


async def _open_root_ssh_for_host(host: str, device_creds: dict):
    os.makedirs("logs", exist_ok=True)
    conn = AsyncGenericDriver(
        host=normalize_ip(host),
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
        channel_log=f"logs/arp_bridge_{normalize_ip(host)}.log",
    )
    for wait_s in (0, 10, 15):
        if wait_s:
            await asyncio.sleep(wait_s)
        try:
            await conn.open()
            return conn
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Unable to open root SSH to {host}: {last_error}")


async def _resolve_link_partner(
    root_ssh,
    peer_ip: str,
) -> tuple[int, str, str]:
    """
    Return (sua index, partner MAC, partner IP from link stats).
    Falls back to first active RF partner when management IP is not in sua stats.
    """
    target = normalize_ip(peer_ip)
    assoc_idx = await find_assoc_index_for_cpe(root_ssh, target, RADIO_INDEX)
    mac = _norm_mac(
        ssh_scalar(await _ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "mac")))
    )
    link_ip = ssh_scalar(await _ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, assoc_idx, "ip")))
    if mac and mac != "00:00:00:00:00:00":
        return assoc_idx, mac, link_ip

    for idx in range(1, 33):
        assoc = ssh_scalar(await _ssh(root_ssh, RootCommands.get_link_stat_associd(RADIO_INDEX, idx)))
        if assoc in {"", "0"}:
            continue
        cand_mac = _norm_mac(
            ssh_scalar(await _ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, idx, "mac")))
        )
        if not cand_mac or cand_mac == "00:00:00:00:00:00":
            continue
        cand_ip = ssh_scalar(await _ssh(root_ssh, RootCommands.get_link_stat_field(RADIO_INDEX, idx, "ip")))
        if ips_equal(target, cand_ip) or target in cand_ip:
            return idx, cand_mac, cand_ip
        if not mac:
            assoc_idx, mac, link_ip = idx, cand_mac, cand_ip

    return assoc_idx, mac, link_ip


async def _peer_mac_from_link(root_ssh, peer_ip: str) -> str:
    _, mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
    return mac


async def _ping_peer(root_ssh, peer_ip: str, *, attempts: int = 3) -> tuple[bool, str]:
    """Ping peer; retry and try via br-lan if default route ping fails."""
    last_raw = ""
    for attempt in range(attempts):
        last_raw = await _ssh(root_ssh, _ping_cmd(peer_ip, count=4))
        if _ping_success(last_raw):
            return True, last_raw
        br_raw = await _ssh(root_ssh, f"ping -c 4 -W 5 -I br-lan {peer_ip} 2>&1")
        if _ping_success(br_raw):
            return True, br_raw
        if attempt + 1 < attempts:
            await asyncio.sleep(2)
    return False, last_raw


async def _neigh_dev_for_peer(root_ssh, peer_ip: str) -> str:
    raw = await _ssh(root_ssh, f"{_neigh_prefix()} show {peer_ip} 2>/dev/null")
    match = re.search(r"dev\s+(\S+)", raw)
    return match.group(1) if match else "br-lan"


async def _neigh_mac_for_peer(root_ssh, peer_ip: str) -> str:
    for row in await _read_arp_neigh_rows(root_ssh, peer_ip):
        if ips_equal(peer_ip, row.get("ip", "")):
            return _norm_mac(row.get("mac", ""))
    return ""


async def _wait_arp_neighbor(
    root_ssh,
    peer_ip: str,
    link_mac: str,
    *,
    timeout_s: float = 12.0,
) -> dict:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last_rows: list[dict[str, str]] = []

    while loop.time() < deadline:
        neigh_rows = await _read_arp_neigh_rows(root_ssh, peer_ip)
        last_rows = neigh_rows

        by_ip = [r for r in neigh_rows if ips_equal(peer_ip, r.get("ip", ""))]
        if by_ip and _norm_mac(by_ip[0]["mac"]) not in ("", "00:00:00:00:00:00"):
            return {"mode": "arp_ip", "row": by_ip[0], "rows": neigh_rows}

        if link_mac:
            by_mac = [r for r in neigh_rows if _mac_related(r.get("mac", ""), link_mac)]
            if by_mac:
                return {"mode": "arp_mac", "row": by_mac[0], "rows": neigh_rows}

        brctl_rows = _parse_brctl_showmacs(await _ssh(root_ssh, RootCommands.GET_BRCTL_SHOWMACS))
        remote = [r for r in brctl_rows if r.get("local") == "no"]
        if link_mac:
            if any(_mac_related(r.get("mac", ""), link_mac) for r in remote):
                return {"mode": "bridge_fdb", "row": None, "rows": neigh_rows}
        elif remote:
            return {"mode": "bridge_fdb", "row": None, "rows": neigh_rows}

        await asyncio.sleep(1.0)

    return {"mode": "none", "row": None, "rows": last_rows}


async def _inject_duplicate_neigh(
    root_ssh,
    peer_ip: str,
    dup_mac: str,
    dev: str,
) -> tuple[bool, str, str]:
    prefix = _neigh_prefix()
    attempts = [
        ("replace", f"{prefix} replace {peer_ip} lladdr {dup_mac} dev {dev} 2>&1"),
        ("change", f"{prefix} change {peer_ip} lladdr {dup_mac} dev {dev} 2>&1"),
        (
            "del_add",
            f"{prefix} del {peer_ip} dev {dev} 2>/dev/null; "
            f"{prefix} add {peer_ip} lladdr {dup_mac} dev {dev} 2>&1",
        ),
    ]
    last_raw = ""
    for name, cmd in attempts:
        last_raw = await _ssh(root_ssh, cmd)
        if _mac_match(await _neigh_mac_for_peer(root_ssh, peer_ip), dup_mac):
            return True, name, last_raw
    return False, "all_failed", last_raw


async def _restore_first_arp_mac(
    root_ssh,
    peer_ip: str,
    first_mac: str,
    dup_mac: str,
    dev: str,
    *,
    device_label: str,
) -> str:
    """Re-ping then flush stale static neigh if duplicate MAC still installed."""
    prefix = _neigh_prefix()

    def _restored(mac: str) -> bool:
        return bool(mac) and (
            _mac_match(mac, first_mac) or _mac_related(mac, first_mac)
        ) and not _mac_match(mac, dup_mac)

    await _ssh(root_ssh, _ping_cmd(peer_ip, count=2))
    for _ in range(4):
        restored = await _neigh_mac_for_peer(root_ssh, peer_ip)
        if _restored(restored):
            return restored
        await asyncio.sleep(0.5)

    _log(f"ARPBRIDGE_02 [{device_label}]: flush neigh + re-ping for {peer_ip}")
    await _ssh(root_ssh, f"{prefix} del {peer_ip} dev {dev} 2>/dev/null")
    await _ssh(root_ssh, f"{prefix} flush dev {dev} 2>/dev/null")
    await _ssh(root_ssh, _ping_cmd(peer_ip, count=3))
    for _ in range(6):
        restored = await _neigh_mac_for_peer(root_ssh, peer_ip)
        if _restored(restored):
            return restored
        await asyncio.sleep(0.5)
    return restored


async def _assert_arpbridge_01_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_01", role=device_label)

    _log(f"ARPBRIDGE_01 [{device_label}]: ping {peer_ip} (ARP request)")
    ping_ok, ping_raw = await _ping_peer(root_ssh, peer_ip, attempts=5)
    check.is_true(ping_ok, f"ARPBRIDGE_01 [{device_label}]: ping failed: {ping_raw[:200]}")

    assoc_idx, link_mac, link_ip = await _resolve_link_partner(root_ssh, peer_ip)
    if not link_mac:
        _log(
            f"ARPBRIDGE_01 [{device_label}]: no RF link MAC in sua stats "
            f"(assoc_idx={assoc_idx}, link_ip={link_ip!r}); validating ARP table only"
        )

    neigh_result = await _wait_arp_neighbor(root_ssh, peer_ip, link_mac)
    mode = neigh_result["mode"]
    hit_row = neigh_result.get("row")
    neigh_rows = neigh_result["rows"]

    if mode == "arp_ip" and hit_row:
        neigh_mac = _norm_mac(hit_row["mac"])
        detail = f"{neigh_mac} ({hit_row.get('state', '')})"
        arp_status = "PASS"
    elif mode == "arp_mac" and hit_row:
        neigh_mac = _norm_mac(hit_row["mac"])
        detail = f"{neigh_mac} (ARP row)"
        arp_status = "WARN"
    elif mode == "bridge_fdb":
        neigh_mac = ""
        detail = f"bridge FDB has {link_mac}"
        arp_status = "WARN"
    else:
        neigh_mac = ""
        detail = "missing"
        arp_status = "FAIL"

    mac_ok = mode in ("arp_ip", "arp_mac", "bridge_fdb")
    if link_mac and neigh_mac:
        link_align = _mac_match(neigh_mac, link_mac) or _mac_related(neigh_mac, link_mac)
        link_status = "PASS" if link_align else "WARN"
    elif link_mac:
        link_align = False
        link_status = "WARN"
    else:
        link_align = mode == "bridge_fdb"
        link_status = "WARN" if mac_ok else "FAIL"

    print_section(f"ARPBRIDGE_01 [{device_label}] — IPv4 ARP resolution")
    print_comparison_table(
        [
            ("Ping peer (ARP trigger)", "ok" if ping_ok else "fail", "replies / RTT", "PASS" if ping_ok else "FAIL"),
            (f"ARP {peer_ip}", detail, "IP + lladdr", arp_status if ping_ok else "FAIL"),
            ("Resolution path", mode, "arp_ip | arp_mac | bridge_fdb", "PASS" if mode == "arp_ip" else ("WARN" if mac_ok else "FAIL")),
            (
                "ARP vs RF link MAC",
                neigh_mac or "n/a",
                link_mac or f"sua{assoc_idx} n/a",
                link_status,
            ),
            (
                "Link stats IP",
                link_ip or "n/a",
                peer_ip,
                "PASS" if (not link_ip or ips_equal(peer_ip, link_ip) or peer_ip in link_ip) else "WARN",
            ),
        ]
    )
    check.is_true(ping_ok and mac_ok, f"ARPBRIDGE_01 [{device_label}]: no ARP for {peer_ip} (mode={mode})")
    return ping_ok and mac_ok


async def _run_bts_and_cpe(
    root_ssh,
    peer_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    case_id: str,
    on_device,
) -> None:
    peer_ip = _require_ipv4(peer_ip, case_id=case_id, role="peer")
    bts_ip = _require_ipv4(bts_ip, case_id=case_id, role="BTS")

    if not await on_device(root_ssh, peer_ip, device_label="BTS"):
        _log(f"{case_id}: skipping CPE pass because BTS checks did not pass")
        return

    check.is_true(device_creds, f"{case_id}: device credentials required for CPE pass")
    try:
        cpe_ssh, access_mode = await _open_cpe_ssh_for_arpbridge(
            root_ssh, peer_ip, bts_ip, device_creds
        )
    except (RuntimeError, asyncio.TimeoutError) as exc:
        check.is_true(False, f"{case_id}: CPE access failed: {exc}")
        return

    try:
        await on_device(cpe_ssh, bts_ip, device_label=f"CPE ({access_mode})")
    finally:
        await _close_ssh_session(cpe_ssh)


async def assert_arpbridge_01_basic_arp_resolution(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_01 (IPv4): ping peer; ARP table has MAC for that IP (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_01: --remote-ip / profile remote_ips required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_01",
        on_device=_assert_arpbridge_01_on_device,
    )


def _duplicate_test_mac(first_mac: str) -> str:
    parts = first_mac.split(":")
    if len(parts) == 6:
        last = int(parts[-1], 16)
        parts[-1] = f"{(last ^ 0xAB) & 0xFF:02x}"
        candidate = ":".join(parts)
        if not _mac_match(candidate, first_mac):
            return candidate
    return "02:00:00:00:00:ab"


async def _assert_arpbridge_02_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_02", role=device_label)

    _log(f"ARPBRIDGE_02 [{device_label}]: establish first ARP entry for {peer_ip}")
    ping_ok, _ = await _ping_peer(root_ssh, peer_ip, attempts=4)
    check.is_true(ping_ok, f"ARPBRIDGE_02 [{device_label}]: initial ping failed")

    _, link_mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
    baseline = await _wait_arp_neighbor(root_ssh, peer_ip, link_mac, timeout_s=12.0)
    first_row = baseline.get("row")
    check.is_true(first_row, f"ARPBRIDGE_02 [{device_label}]: no baseline ARP for {peer_ip}")
    first_mac = _norm_mac(first_row["mac"])
    dev = await _neigh_dev_for_peer(root_ssh, peer_ip)

    dup_mac = _duplicate_test_mac(first_mac)
    _log(f"ARPBRIDGE_02 [{device_label}]: inject duplicate lladdr {dup_mac} on {dev}")
    inject_ok, inject_variant, inject_raw = await _inject_duplicate_neigh(root_ssh, peer_ip, dup_mac, dev)
    if not inject_ok:
        inject_ok = _mac_match(await _neigh_mac_for_peer(root_ssh, peer_ip), dup_mac)

    _log(f"ARPBRIDGE_02 [{device_label}]: re-ping to restore legitimate ARP")
    restored_mac = await _restore_first_arp_mac(
        root_ssh, peer_ip, first_mac, dup_mac, dev, device_label=device_label
    )

    rows_after = await _read_arp_neigh_rows(root_ssh, peer_ip)
    dup_ip_rows = [r for r in rows_after if ips_equal(peer_ip, r.get("ip", ""))]
    single_entry = len(dup_ip_rows) <= 1
    first_wins = bool(restored_mac) and (
        _mac_match(restored_mac, first_mac) or _mac_related(restored_mac, first_mac)
    )
    first_wins = first_wins and not _mac_match(restored_mac, dup_mac)

    print_section(f"ARPBRIDGE_02 [{device_label}] — Duplicate IPv4 ARP handling")
    print_comparison_table(
        [
            ("First learned MAC", first_mac, "recorded", "PASS" if first_mac else "FAIL"),
            (
                "Duplicate injected",
                f"{dup_mac} via {inject_variant}" if inject_ok else inject_raw[:50],
                dup_mac,
                "PASS" if inject_ok else "FAIL",
            ),
            ("MAC after re-ping", restored_mac or "missing", first_mac, "PASS" if first_wins else "FAIL"),
            ("Single ARP row", str(len(dup_ip_rows)), "1", "PASS" if single_entry else "FAIL"),
        ]
    )
    check.is_true(inject_ok, f"ARPBRIDGE_02 [{device_label}]: duplicate inject failed ({inject_raw[:80]})")
    check.is_true(first_wins, f"ARPBRIDGE_02 [{device_label}]: first MAC not restored (have {restored_mac})")
    check.is_true(single_entry, f"ARPBRIDGE_02 [{device_label}]: multiple ARP rows: {dup_ip_rows}")
    return inject_ok and first_wins and single_entry


async def assert_arpbridge_02_duplicate_ip_handling(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_02 (IPv4): duplicate ARP claim — first MAC wins after re-ping (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_02: --remote-ip required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_02",
        on_device=_assert_arpbridge_02_on_device,
    )


async def _arpbridge_06_print_table(
    device_label: str,
    *,
    address_type: str,
    old_ip: str,
    dhcp_ip: str,
    ping_ok: bool,
    mode: str,
    neigh_mac: str,
    link_mac: str,
) -> bool:
    mac_ok = mode in ("arp_ip", "arp_mac", "bridge_fdb")
    mac_align = bool(neigh_mac) and (
        not link_mac or _mac_match(neigh_mac, link_mac) or _mac_related(neigh_mac, link_mac)
    )
    print_section(f"ARPBRIDGE_06 [{device_label}] — Dynamic IPv4 allocation")
    print_comparison_table(
        [
            ("Address type", address_type, "network.lan.proto=dhcp", "PASS"),
            ("Peer IP (static)", old_ip, "before DHCP", "PASS"),
            ("Peer IP (DHCP)", dhcp_ip or "pending", "ucidyn / lease", "PASS" if dhcp_ip else "FAIL"),
            ("Ping peer", "ok" if ping_ok else "fail", "ARP trigger", "PASS" if ping_ok else "FAIL"),
            (
                f"ARP {dhcp_ip or old_ip}",
                neigh_mac or "missing",
                "IP + lladdr",
                "PASS" if mac_ok and mac_align else ("WARN" if mac_ok else "FAIL"),
            ),
            ("Resolution path", mode, "arp_ip | arp_mac | bridge_fdb", "PASS" if mode == "arp_ip" else ("WARN" if mac_ok else "FAIL")),
        ]
    )
    arp_ok = mac_ok and (mac_align or mode == "bridge_fdb")
    return bool(dhcp_ip) and ping_ok and arp_ok


async def _verify_arpbridge_06_observer(
    observer_ssh,
    peer_ip: str,
    link_mac: str,
    *,
    device_label: str,
    address_type: str,
    static_peer_ip: str,
) -> bool:
    peer_ip = normalize_ip(peer_ip)
    await _flush_neigh_for_ip(observer_ssh, peer_ip)
    ping_ok, _ = await _ping_peer(observer_ssh, peer_ip)
    neigh = await _wait_arp_neighbor(observer_ssh, peer_ip, link_mac, timeout_s=18.0)
    mode = neigh["mode"]
    row = neigh.get("row")
    neigh_mac = _norm_mac(row["mac"]) if row else ""
    ok = await _arpbridge_06_print_table(
        device_label,
        address_type=address_type,
        old_ip=static_peer_ip,
        dhcp_ip=peer_ip,
        ping_ok=ping_ok,
        mode=mode,
        neigh_mac=neigh_mac,
        link_mac=link_mac,
    )
    check.is_true(ok, f"ARPBRIDGE_06 [{device_label}]: ARP not updated for DHCP peer {peer_ip}")
    return ok


async def assert_arpbridge_06_dynamic_ip_allocation(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    gui_browser=None,
) -> None:
    """
    ARPBRIDGE_06 — Dynamic IP allocation:

    1. Set CPE to Dynamic IPv4 (GUI Apply)
    2. Wait until CPE gets a reachable DHCP IPv4
    3. Verify ARP on BTS and CPE (ping + ip neigh)
    4. Restore Static IPv4 in finally
    """
    check.is_true(cpe_ips, "ARPBRIDGE_06: --remote-ip required")
    creds = device_creds or {}
    check.is_true(creds, "ARPBRIDGE_06: device credentials required")
    static_cpe_ip = _require_ipv4(cpe_ips[0], case_id="ARPBRIDGE_06", role="CPE")
    bts_ip = _require_ipv4(bsu_ip or "", case_id="ARPBRIDGE_06", role="BTS")

    cpe_ssh = None
    cpe_gui = None
    lan_snap: dict[str, str] = {}
    dhcp_cpe_ip = ""
    address_type = "Dynamic IPv4 (dhcp)"

    try:
        await _ensure_lab_static_ips(root_ssh, bts_ip, static_cpe_ip)

        cpe_ssh, _ = await _open_cpe_ssh_for_arpbridge(root_ssh, static_cpe_ip, bts_ip, creds)
        lan_snap = await _snapshot_lan_uci(cpe_ssh)
        _log(f"ARPBRIDGE_06: saved CPE config proto={lan_snap.get('proto')} ip={lan_snap.get('ipaddr')}")

        # --- Step 1: CPE → Dynamic IPv4 ---
        used_gui = False
        if gui_browser is not None:
            from utils.cpe_session import open_cpe_gui_session_if_reachable

            cpe_gui = await open_cpe_gui_session_if_reachable(
                gui_browser, static_cpe_ip, creds, require_summary=False
            )
            if cpe_gui is not None:
                _log("ARPBRIDGE_06 [1/3]: GUI → Dynamic IPv4 (Network > IP Configuration → Apply)")
                address_type = await _set_dynamic_ipv4_gui(cpe_gui)
                used_gui = True
        if not used_gui:
            _log("ARPBRIDGE_06 [1/3]: SSH → uci set network.lan.proto=dhcp")
            await _set_dynamic_ipv4_ssh(cpe_ssh)

        await _close_ssh_session(cpe_ssh)
        cpe_ssh = None

        # --- Step 2: wait for CPE DHCP IPv4 ---
        dhcp_cpe_ip, dhcp_source = await _wait_cpe_dhcp_ready(
            root_ssh, static_cpe_ip, bts_ip, creds, timeout_s=90.0
        )
        check.is_true(dhcp_cpe_ip, "ARPBRIDGE_06: CPE did not get a DHCP IPv4 address")
        _log(
            f"ARPBRIDGE_06 [2/3]: CPE DHCP IP = {dhcp_cpe_ip} "
            f"(was static {static_cpe_ip}, via {dhcp_source})"
        )

        # --- Step 3: verify ARP on BTS and CPE ---
        _, link_mac_bts, _ = await _resolve_link_partner(root_ssh, dhcp_cpe_ip)
        await _verify_arpbridge_06_observer(
            root_ssh,
            dhcp_cpe_ip,
            link_mac_bts,
            device_label="BTS",
            address_type=address_type,
            static_peer_ip=static_cpe_ip,
        )

        cpe_observer, _ = await _open_cpe_ssh_for_arpbridge(
            root_ssh, static_cpe_ip, bts_ip, creds, extra_hosts=[dhcp_cpe_ip]
        )
        _, link_mac_cpe, _ = await _resolve_link_partner(cpe_observer, bts_ip)
        await _verify_arpbridge_06_observer(
            cpe_observer,
            bts_ip,
            link_mac_cpe,
            device_label="CPE",
            address_type=address_type,
            static_peer_ip=bts_ip,
        )
        if cpe_observer is not cpe_ssh:
            await _close_ssh_session(cpe_observer)

        _log("ARPBRIDGE_06 [3/3]: Dynamic IPv4 ARP verification PASSED")

    except RuntimeError as exc:
        check.is_true(False, f"ARPBRIDGE_06: {exc}")

    finally:
        _log(f"ARPBRIDGE_06: restore Static IPv4 {static_cpe_ip}")
        if cpe_gui is not None:
            await cpe_gui.close()
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)
        await _ensure_lab_static_ips(root_ssh, bts_ip, static_cpe_ip)
        await restore_cpe_static_lab_ip(
            root_ssh,
            static_cpe_ip,
            bts_ip,
            creds,
            uci_snap=lan_snap or None,
            soft=True,
            gui_browser=gui_browser,
            dhcp_cpe_ip=dhcp_cpe_ip,
        )


async def _send_gratuitous_arp(ssh, ip: str, *, iface: str = "br-lan") -> tuple[bool, str]:
    """Send unsolicited/gratuitous ARP for *ip* on *iface* (arping -U)."""
    ip = normalize_ip(ip)
    last_raw = ""
    for cmd in (
        f"arping -U -c 3 -I {iface} {ip} 2>&1",
        f"arping -U -b -c 3 -I {iface} {ip} 2>&1",
        f"arping -A -c 3 -I {iface} -s {ip} {ip} 2>&1",
    ):
        last_raw = await _ssh(ssh, cmd)
        lower = last_raw.lower()
        if "not found" in lower or "usage:" in lower or "invalid" in lower:
            continue
        if any(token in lower for token in ("sent", "reply", "bytes from", "unicast", "broadcast")):
            return True, last_raw
        if "arping" in lower and "error" not in lower:
            return True, last_raw
    return False, last_raw


async def _assert_arpbridge_07_on_device(
    observer_ssh,
    peer_ip: str,
    sender_ssh,
    *,
    device_label: str,
    sender_label: str,
) -> bool:
    """Flush ARP, peer sends gratuitous ARP; observer table must learn peer MAC."""
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_07", role="peer")
    _, link_mac, _ = await _resolve_link_partner(observer_ssh, peer_ip)

    await _ping_peer(observer_ssh, peer_ip, attempts=1)
    before_mac = await _neigh_mac_for_peer(observer_ssh, peer_ip)

    await _flush_neigh_for_ip(observer_ssh, peer_ip)
    await asyncio.sleep(1)
    after_flush = await _neigh_mac_for_peer(observer_ssh, peer_ip)

    _log(f"ARPBRIDGE_07 [{device_label}]: gratuitous ARP for {peer_ip} from {sender_label}")
    garp_ok, garp_raw = await _send_gratuitous_arp(sender_ssh, peer_ip)
    check.is_true(
        garp_ok,
        f"ARPBRIDGE_07 [{sender_label}]: gratuitous ARP failed: {garp_raw[:120]}",
    )

    neigh = await _wait_arp_neighbor(observer_ssh, peer_ip, link_mac, timeout_s=15.0)
    mode = neigh["mode"]
    row = neigh.get("row")
    neigh_mac = _norm_mac(row["mac"]) if row else ""
    rows_for_ip = [r for r in neigh["rows"] if ips_equal(peer_ip, r.get("ip", ""))]
    single_row = len(rows_for_ip) <= 1
    mac_ok = mode in ("arp_ip", "arp_mac", "bridge_fdb") and bool(neigh_mac)
    mac_align = bool(neigh_mac) and (
        not link_mac or _mac_match(neigh_mac, link_mac) or _mac_related(neigh_mac, link_mac)
    )
    consistent = mac_ok and (
        not before_mac or _mac_related(neigh_mac, before_mac) or _mac_match(neigh_mac, before_mac)
    )

    print_section(f"ARPBRIDGE_07 [{device_label}] — Gratuitous ARP handling")
    print_comparison_table(
        [
            ("ARP before GARP", before_mac or "cleared", "baseline / flushed", "PASS"),
            ("After flush", after_flush or "empty", "stale entry removed", "PASS" if not after_flush else "WARN"),
            ("Gratuitous ARP sent", "ok" if garp_ok else "fail", f"arping -U ({sender_label})", "PASS" if garp_ok else "FAIL"),
            (
                f"ARP {peer_ip}",
                neigh_mac or "missing",
                link_mac or "RF MAC",
                "PASS" if mac_ok and mac_align else ("WARN" if mac_ok else "FAIL"),
            ),
            ("GARP consistency", neigh_mac or "n/a", before_mac or "n/a", "PASS" if consistent else "WARN"),
            ("Resolution path", mode, "arp_ip | arp_mac | bridge_fdb", "PASS" if mode == "arp_ip" else ("WARN" if mac_ok else "FAIL")),
            ("Single ARP row", str(len(rows_for_ip)), "1", "PASS" if single_row else "FAIL"),
        ]
    )
    check.is_true(garp_ok and mac_ok, f"ARPBRIDGE_07 [{device_label}]: no ARP after gratuitous ARP (mode={mode})")
    check.is_true(single_row, f"ARPBRIDGE_07 [{device_label}]: multiple ARP rows: {rows_for_ip}")
    return garp_ok and mac_ok and single_row


async def assert_arpbridge_07_gratuitous_arp_handling(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_07 (IPv4): gratuitous ARP updates observer ARP table (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_07: --remote-ip required")
    creds = device_creds or {}
    check.is_true(creds, "ARPBRIDGE_07: device credentials required")
    cpe_ip = _require_ipv4(cpe_ips[0], case_id="ARPBRIDGE_07", role="CPE")
    bts_ip = _require_ipv4(bsu_ip or "", case_id="ARPBRIDGE_07", role="BTS")

    cpe_ssh = None
    try:
        cpe_ssh, access_mode = await _open_cpe_ssh_for_arpbridge(root_ssh, cpe_ip, bts_ip, creds)
        _log(f"ARPBRIDGE_07 [BTS]: CPE sender session ({access_mode})")
        if not await _assert_arpbridge_07_on_device(
            root_ssh,
            cpe_ip,
            cpe_ssh,
            device_label="BTS",
            sender_label="CPE",
        ):
            _log("ARPBRIDGE_07: skipping CPE pass because BTS checks did not pass")
            return
    finally:
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)

    cpe_ssh = None
    try:
        cpe_ssh, access_mode = await _open_cpe_ssh_for_arpbridge(root_ssh, cpe_ip, bts_ip, creds)
        _log(f"ARPBRIDGE_07 [CPE]: observer session ({access_mode})")
        await _assert_arpbridge_07_on_device(
            cpe_ssh,
            bts_ip,
            root_ssh,
            device_label="CPE",
            sender_label="BTS",
        )
    finally:
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)
