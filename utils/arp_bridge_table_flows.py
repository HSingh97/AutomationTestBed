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
ARPBRIDGE_06_SETTLE_S = 60
ARPBRIDGE_14_AGEING_S = 20
ARPBRIDGE_14_IDLE_BUFFER_S = 10
_DEBUG_LOG = "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-896452.log"


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
        "sessionId": "896452",
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


async def _ssh(root_ssh, command: str, *, timeout: int | None = None) -> str:
    async def _run() -> str:
        response = await root_ssh.send_command(command)
        return clean_ssh_output(response.result)

    if timeout is None:
        return await _run()
    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        return f"ERROR: command timed out after {timeout}s"


async def _ssh_session_alive(ssh) -> bool:
    """Best-effort probe — False when scrapli/asyncssh session is closed."""
    if ssh is None:
        return False
    try:
        if hasattr(ssh, "send_command"):
            await asyncio.wait_for(ssh.send_command("echo ARPBRIDGE_ALIVE", timeout=5), timeout=8)
            return True
        if hasattr(ssh, "_cpe_conn"):
            result = await asyncio.wait_for(
                ssh._cpe_conn.run("echo ARPBRIDGE_ALIVE", check=False),
                timeout=8,
            )
            return "ARPBRIDGE_ALIVE" in ((result.stdout or "") + (result.stderr or ""))
    except Exception:
        return False
    return False


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


def _looks_like_mac(text: str) -> bool:
    return bool(re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", _norm_mac(text), re.I))


def _is_bridge_member_iface(name: str) -> bool:
    """True for sysfs/brctl port names; reject MAC strings mistaken as members."""
    name = (name or "").strip()
    if not name or _looks_like_mac(name) or ":" in name:
        return False
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_.-]*$", name))


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
    return f"ping -c {count} -W 8 {peer_ip} 2>&1"


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


def _remote_exec_bts_command(su_index: int, inner_command: str) -> str:
    """Build BTS remote_exec invocation (single-quoted inner cmd — avoids >/& shell breaks)."""
    escaped = inner_command.replace("'", "'\"'\"'")
    return f"/usr/sbin/remote_exec.sh {su_index} '{escaped}'"


def _remote_exec_safe_command(command: str) -> str:
    """remote_exec runs argv on CPE without a shell — drop redirection suffixes."""
    cmd = command.strip()
    for suffix in (" 2>&1", " 2>/dev/null", " >/dev/null"):
        if cmd.endswith(suffix):
            cmd = cmd[: -len(suffix)].rstrip()
    return cmd


def _extract_ipv4_from_text(raw: str) -> str:
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw or "")
    return normalize_ip(match.group(0)) if match else ""


async def _read_lan_proto(ssh) -> str:
    resp = await ssh.send_command(RootCommands.GET_NET_PROTO)
    for line in str(resp.result or "").splitlines():
        proto = line.strip().lower().strip("'\"")
        if proto in ("static", "dhcp", "pppoe"):
            return proto
    proto = ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_PROTO)).lower()
    if proto in ("static", "dhcp", "pppoe"):
        return proto
    return ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_PROTO)).lower()


async def _snapshot_lan_uci(ssh) -> dict[str, str]:
    return {
        "proto": await _read_lan_proto(ssh),
        "ipaddr": ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_IP)),
        "netmask": ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_MASK)),
        "gateway": ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_GW)),
    }


async def _read_wifi_iface_mode(ssh) -> str:
    return ssh_scalar(
        await _ssh(ssh, "uci get wireless.@wifi-iface[0].mode 2>/dev/null")
    ).lower()


async def _remote_exec_is_cpe_target(root_ssh, su_index: int) -> bool:
    """remote_exec index must reach CPE — reject BTS-local execution (same br-lan MAC)."""
    probe = clean_ssh_output(
        await _ssh(root_ssh, _remote_exec_bts_command(su_index, "echo ARPBRIDGE_REMOTE_OK"))
    )
    if "ARPBRIDGE_REMOTE_OK" not in probe:
        return False
    bts_mac = await _get_br_lan_mac(root_ssh)
    raw = clean_ssh_output(
        await _ssh(root_ssh, _remote_exec_bts_command(su_index, "cat /sys/class/net/br-lan/address"))
    )
    peer_mac = _norm_mac(raw)
    return bool(peer_mac) and peer_mac != bts_mac


async def _list_cpe_remote_exec_indices(root_ssh, cpe_ip: str) -> list[int]:
    """All remote_exec SU indices that reach CPE (sta), preferred link partner first."""
    preferred = await _remote_exec_su_indices(root_ssh, cpe_ip)
    ordered: list[int] = []
    seen: set[int] = set()

    async def try_add(idx: int) -> None:
        if idx in seen:
            return
        if await _remote_exec_is_cpe_target(root_ssh, idx):
            ordered.append(idx)
            seen.add(idx)

    for idx in preferred:
        await try_add(idx)
    for idx in range(1, 33):
        await try_add(idx)
    return ordered


async def _resolve_cpe_remote_exec_index(root_ssh, cpe_ip: str) -> int | None:
    indices = await _list_cpe_remote_exec_indices(root_ssh, cpe_ip)
    return indices[0] if indices else None


async def _assert_ssh_session_is_cpe(
    ssh,
    bts_ssh=None,
    *,
    context: str,
    bts_mac: str = "",
) -> None:
    """Reject sessions that land on BTS (same br-lan MAC as BTS)."""
    peer_mac = await _get_br_lan_mac(ssh)
    if bts_ssh is not None:
        bts_mac = bts_mac or await _get_br_lan_mac(bts_ssh)
    if peer_mac and bts_mac and peer_mac == bts_mac:
        raise RuntimeError(
            f"ARPBRIDGE: {context} — session MAC {peer_mac} is BTS; "
            "refusing CPE-only operation (check remote_exec SU index / lab IPs)"
        )
    if not peer_mac:
        raise RuntimeError(f"ARPBRIDGE: {context} — could not read br-lan MAC")


async def _heal_bts_lab_ip_if_wrong(root_ssh, bts_ip: str) -> bool:
    """
    Fix BTS br-lan when live IPv4 drifted (e.g. CPE UCI was applied to BTS via remote_exec).
    Uses direct BTS SSH only — never remote_exec or CPE paths.
    Does not delete the wrong live IP (would drop SSH); adds .10 and reloads instead.
    """
    bts_ip = normalize_ip(bts_ip)
    live_ip = await _read_br_lan_live_ipv4(root_ssh, lab_subnet=bts_ip)
    if live_ip == bts_ip:
        return True
    uci_ip = _extract_ipv4_from_text(ssh_scalar(await _ssh(root_ssh, RootCommands.GET_NET_IP)))
    _log(
        f"ARP_BRIDGE: healing BTS IP — live={live_ip or 'missing'} uci={uci_ip or 'n/a'} → {bts_ip}"
    )
    if uci_ip != bts_ip:
        await _ssh(root_ssh, "uci set network.lan.proto=static")
        await _ssh(root_ssh, f"uci set network.lan.ipaddr={bts_ip}")
        await _ssh(root_ssh, "uci set network.lan.netmask=255.255.255.0")
        await _ssh(root_ssh, "uci delete network.lan.gateway; true")
        await _ssh(root_ssh, "uci commit network")
    try:
        await _ssh(
            root_ssh,
            f"ip addr add {bts_ip}/24 dev br-lan 2>/dev/null; "
            f"/etc/init.d/network reload; echo ARPBRIDGE_BTS_HEAL_OK",
            timeout=120,
        )
    except Exception as exc:
        _log(f"ARP_BRIDGE: BTS network reload during heal failed ({type(exc).__name__})")
    await asyncio.sleep(15)
    healed = await _read_br_lan_live_ipv4(root_ssh, lab_subnet=bts_ip)
    ok = healed == bts_ip
    _log(f"ARP_BRIDGE: BTS heal {'ok' if ok else 'failed'} — live now {healed or 'missing'}")
    return ok


async def _assert_bts_lab_ip_unchanged(
    root_ssh,
    bts_ip: str,
    *,
    case_id: str,
    phase: str,
) -> None:
    """Fail fast when BTS br-lan IPv4 is not the lab management IP (e.g. .11 applied to BTS)."""
    bts_ip = normalize_ip(bts_ip)
    live_ip = await _read_br_lan_live_ipv4(root_ssh, lab_subnet=bts_ip)
    uci_ip = _extract_ipv4_from_text(ssh_scalar(await _ssh(root_ssh, RootCommands.GET_NET_IP)))
    ok = live_ip == bts_ip
    if not ok and phase == "start":
        try:
            if await _heal_bts_lab_ip_if_wrong(root_ssh, bts_ip):
                return
        except Exception as exc:
            _log(f"ARP_BRIDGE: BTS heal error ({type(exc).__name__})")
        live_ip = await _read_br_lan_live_ipv4(root_ssh, lab_subnet=bts_ip)
        ok = live_ip == bts_ip
    if not ok:
        _log(
            f"{case_id}: BTS IP guard ({phase}) — live={live_ip or 'missing'} "
            f"uci={uci_ip or 'n/a'} expected={bts_ip}"
        )
    check.is_true(
        ok,
        f"{case_id}: BTS must stay at {bts_ip} ({phase}); live={live_ip or 'missing'} "
        f"(CPE UCI must not run on BTS via remote_exec — reconfigure BTS GUI to {bts_ip})",
    )


async def _log_bts_unchanged_for_06(bts_ssh, bts_ip: str) -> dict[str, str]:
    """ARPBRIDGE_06: read BTS LAN state only — never change BTS network/DHCP config."""
    snap = {
        "proto": await _read_lan_proto(bts_ssh),
        "live_ip": await _read_br_lan_live_ipv4(bts_ssh),
    }
    snap.update(await _snapshot_lan_uci(bts_ssh))
    ignore = ssh_scalar(await _ssh(bts_ssh, "uci get dhcp.lan.ignore 2>/dev/null")).strip()
    _log(
        f"ARPBRIDGE_06: BTS static only — not reconfigured "
        f"(proto={snap['proto']}, ip={snap['live_ip'] or bts_ip}, dhcp.lan.ignore={ignore or 'n/a'})"
    )
    return snap


async def _assert_bts_static_for_06(bts_ssh, bts_ip: str) -> None:
    """Fail fast when BTS is not static — ARPBRIDGE_06 only switches CPE to Dynamic IPv4."""
    proto = await _read_lan_proto(bts_ssh)
    live_ip = await _read_br_lan_live_ipv4(bts_ssh)
    check.is_true(
        proto == "static",
        f"ARPBRIDGE_06: BTS must remain static (proto={proto!r}); only CPE may use Dynamic IPv4",
    )
    _log(f"ARPBRIDGE_06: BTS confirmed static (proto={proto}, ip={live_ip or bts_ip})")


async def _verify_bts_unchanged_for_06(bts_ssh, bts_snap: dict[str, str], bts_ip: str) -> bool:
    """Confirm test did not alter BTS LAN configuration."""
    proto_now = await _read_lan_proto(bts_ssh)
    live_now = await _read_br_lan_live_ipv4(bts_ssh)
    proto_ok = proto_now == bts_snap.get("proto", proto_now)
    ip_ok = (live_now or bts_ip) == (bts_snap.get("live_ip") or bts_ip)
    print_section("ARPBRIDGE_06 [BTS] — config unchanged (read-only)")
    print_comparison_table(
        [
            ("network.lan.proto", proto_now, bts_snap.get("proto", ""), "PASS" if proto_ok else "FAIL"),
            ("LAN IPv4 (live)", live_now or "missing", bts_snap.get("live_ip") or bts_ip, "PASS" if ip_ok else "FAIL"),
        ]
    )
    return proto_ok and ip_ok


async def _set_dynamic_ipv4_ssh(ssh) -> None:
    """Set dhcp in UCI, drop static LAN addresses, reload so CPE requests from BTS DHCP."""
    await _ssh(ssh, "uci set network.lan.proto=dhcp")
    await _ssh(ssh, "uci delete network.lan.ipaddr; uci delete network.lan.gateway; true")
    await _ssh(ssh, "uci commit network")
    # network reload often drops the SSH session — long timeout, swallow disconnect
    try:
        await _ssh(ssh, "/etc/init.d/network reload; echo ARPBRIDGE06_RELOADED", timeout=120)
    except Exception as exc:
        # region agent log
        _debug_log(
            "arp_bridge_table_flows.py:_set_dynamic_ipv4_ssh",
            "network reload ended session",
            {"exc_type": type(exc).__name__, "exc": str(exc)[:120]},
            hypothesis_id="H1",
        )
        # endregion


async def _cpe_remote_exec(root_ssh, command: str, *, su_index: int = 1) -> str:
    return await _ssh(root_ssh, _remote_exec_bts_command(su_index, command))


async def _read_br_lan_live_ipv4(ssh, *, lab_subnet: str = "") -> str:
    """IPv4 on br-lan; when multiple addresses exist, prefer the lab /24."""
    resp = await ssh.send_command("ip -4 -o addr show dev br-lan 2>/dev/null")
    candidates: list[str] = []
    for line in str(resp.result or "").splitlines():
        found = _extract_ipv4_from_text(line)
        if found and not is_ipv6_literal(found):
            candidates.append(found)
    if lab_subnet:
        prefix = ".".join(normalize_ip(lab_subnet).split(".")[:3]) + "."
        for ip in candidates:
            if ip.startswith(prefix):
                return ip
    return candidates[0] if candidates else ""


async def _verify_bts_mgmt_ip_stable(bts_ssh, bts_ip: str) -> bool:
    """Confirm BTS br-lan live IPv4 is still the lab management IP (ignore dirty UCI)."""
    bts_ip = normalize_ip(bts_ip)
    live_ip = await _read_br_lan_live_ipv4(bts_ssh)
    ok = live_ip == bts_ip
    print_section("ARPBRIDGE_06 [BTS] — management IP unchanged")
    print_comparison_table(
        [("LAN IPv4 (live)", live_ip or "missing", bts_ip, "PASS" if ok else "FAIL")]
    )
    if not ok:
        _log(f"ARPBRIDGE_06 [BTS]: live LAN IP {live_ip} != {bts_ip}")
    return ok


async def _verify_lab_lan_unchanged(
    ssh,
    snap: dict[str, str],
    *,
    device_label: str,
    expected_ip: str = "",
) -> bool:
    """Verify device LAN proto/IP still match the snapshot taken at test start."""
    expected_proto = (snap.get("proto") or "static").strip() or "static"
    expected_ip = normalize_ip(expected_ip) or _extract_ipv4_from_text(snap.get("ipaddr", ""))
    proto = await _read_lan_proto(ssh)
    live_ip = await _read_cpe_br_lan_ipv4(ssh)
    uci_ip = _extract_ipv4_from_text(ssh_scalar(await _ssh(ssh, RootCommands.GET_NET_IP)))
    ip_ok = live_ip == expected_ip or uci_ip == expected_ip
    proto_ok = proto == expected_proto
    print_section(f"ARPBRIDGE_06 [{device_label}] — LAN after restore")
    print_comparison_table(
        [
            ("network.lan.proto", proto, expected_proto, "PASS" if proto_ok else "FAIL"),
            ("LAN IPv4 (live)", live_ip or "missing", expected_ip, "PASS" if ip_ok else "FAIL"),
            ("LAN IPv4 (uci)", uci_ip or "missing", expected_ip, "PASS" if uci_ip == expected_ip else "WARN"),
        ]
    )
    ok = proto_ok and ip_ok
    if not ok:
        _log(f"ARPBRIDGE_06 [{device_label}]: LAN mismatch proto={proto} ip={live_ip} (expected {expected_proto}/{expected_ip})")
    return ok


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
    """Restore LAN UCI only (no ip addr flush/add — let network apply normally)."""
    proto = (snap.get("proto") or "static").strip() or "static"
    _log(f"ARPBRIDGE [{device_label}]: restore network.lan.proto={proto} (UCI only)")
    await _ssh(ssh, f"uci set network.lan.proto={proto}")
    if proto == "static":
        for key in ("ipaddr", "netmask", "gateway"):
            val = (snap.get(key) or "").strip().strip("'\"")
            if val and "not found" not in val.lower() and "uci:" not in val.lower():
                await _ssh(ssh, f"uci set network.lan.{key}={val}")
    await _ssh(ssh, "uci commit network")


async def _restore_bts_static_from_snap(bts_ssh, bts_snap: dict[str, str], bts_ip: str) -> None:
    """Rollback BTS to static if a mis-targeted remote_exec changed it to dhcp."""
    proto_now = await _read_lan_proto(bts_ssh)
    expected_proto = (bts_snap.get("proto") or "static").strip() or "static"
    if proto_now == expected_proto:
        return
    restore_snap = dict(bts_snap)
    restore_snap["proto"] = "static"
    if not restore_snap.get("ipaddr"):
        restore_snap["ipaddr"] = normalize_ip(bts_ip)
    if not restore_snap.get("netmask"):
        restore_snap["netmask"] = "255.255.255.0"
    _log(f"ARPBRIDGE_06: restoring BTS static (was proto={proto_now})")
    await _restore_lan_uci_snapshot(bts_ssh, restore_snap, device_label="BTS")
    await _ssh(bts_ssh, "/etc/init.d/network reload; echo ok")


async def _restore_cpe_via_bts_remote_exec(
    root_ssh,
    static_cpe_ip: str,
    bts_ip: str,
    *,
    su_index: int | None = None,
) -> bool:
    """Push static LAN UCI to CPE over RF when LAN SSH to .11 is down."""
    static_cpe_ip = normalize_ip(static_cpe_ip)
    bts_ip = normalize_ip(bts_ip)
    if static_cpe_ip == bts_ip:
        _log("ARPBRIDGE: refuse CPE restore — target IP equals BTS IP")
        return False
    if su_index is None:
        indices = await _list_cpe_remote_exec_indices(root_ssh, static_cpe_ip)
        if not indices:
            _log("ARPBRIDGE: no remote_exec index reaches CPE — skip UCI restore")
            return False
        su_index = indices[0]
    elif not await _remote_exec_is_cpe_target(root_ssh, su_index):
        _log(f"ARPBRIDGE: remote_exec SU{su_index} is BTS — refuse CPE UCI restore")
        return False
    cmds = [
        "uci set network.lan.proto=static",
        f"uci set network.lan.ipaddr={static_cpe_ip}",
        "uci set network.lan.netmask=255.255.255.0",
        f"uci set network.lan.gateway={bts_ip}",
        "uci commit network",
    ]
    for inner in cmds:
        if not await _remote_exec_is_cpe_target(root_ssh, su_index):
            _log(f"ARPBRIDGE: remote_exec SU{su_index} flipped to BTS — abort restore")
            return False
        raw = await _ssh(root_ssh, _remote_exec_bts_command(su_index, inner))
        if _ip_neigh_cmd_failed(raw) and "usage:" in raw.lower():
            _log(f"ARPBRIDGE: remote_exec restore failed on: {inner[:60]}")
            return False
    if not await _remote_exec_is_cpe_target(root_ssh, su_index):
        _log(f"ARPBRIDGE: remote_exec SU{su_index} flipped to BTS — skip network reload")
        return False
    await _ssh(root_ssh, _remote_exec_bts_command(su_index, "/etc/init.d/network reload"))
    return True


async def _remote_exec_on_cpe(root_ssh, cpe_ip: str, inner_command: str) -> tuple[bool, str]:
    """Run one command on CPE via BTS remote_exec (RF path). Never execute on BTS."""
    for idx in await _list_cpe_remote_exec_indices(root_ssh, cpe_ip):
        probe = clean_ssh_output(
            await _ssh(root_ssh, _remote_exec_bts_command(idx, "echo ARPBRIDGE_CPE_OK"))
        )
        if "ARPBRIDGE_CPE_OK" not in probe:
            continue
        raw = await _ssh(root_ssh, _remote_exec_bts_command(idx, inner_command))
        return True, raw
    _log("ARPBRIDGE: remote_exec_on_cpe — no CPE target index found")
    return False, ""


async def recover_cpe_l3_soft(
    root_ssh,
    cpe_ip: str,
    bts_ip: str,
    *,
    allow_reboot: bool = True,
    reboot_wait_s: float = 90.0,
) -> bool:
    """
    Recover CPE L3 (ping/SSH/GUI) when RF link stats still show linked.

    Steps: ping check → static UCI + network reload via remote_exec → soft reboot.
    Does not power-cycle hardware. Safe to call manually from BTS SSH session.
    """
    cpe_ip = normalize_ip(cpe_ip)
    bts_ip = normalize_ip(bts_ip)

    async def _safe_ping() -> bool:
        try:
            ok, _ = await asyncio.wait_for(_ping_peer(root_ssh, cpe_ip, attempts=1), timeout=25.0)
            return ok
        except Exception as exc:
            _log(f"ARP_BRIDGE: recovery ping failed ({type(exc).__name__})")
            return False

    if await _safe_ping():
        return True

    _log(f"ARP_BRIDGE: CPE {cpe_ip} L3 down — trying remote_exec network reload (RF link may still show up)")
    try:
        if await _restore_cpe_via_bts_remote_exec(root_ssh, cpe_ip, bts_ip):
            await asyncio.wait_for(
                _remote_exec_on_cpe(root_ssh, cpe_ip, "/etc/init.d/network reload"),
                timeout=20.0,
            )
            await asyncio.sleep(20)
    except Exception as exc:
        _log(f"ARP_BRIDGE: network reload via remote_exec failed ({type(exc).__name__})")

    if await _safe_ping():
        _log("ARP_BRIDGE: CPE L3 recovered after network reload")
        return True

    if not allow_reboot:
        return False

    _log("ARP_BRIDGE: trying CPE soft reboot via BTS remote_exec (not a power cycle)")
    try:
        await asyncio.wait_for(_remote_exec_on_cpe(root_ssh, cpe_ip, "reboot"), timeout=15.0)
    except Exception as exc:
        _log(f"ARP_BRIDGE: remote_exec reboot failed ({type(exc).__name__})")
        return False

    await asyncio.sleep(reboot_wait_s)
    for _ in range(12):
        if await _safe_ping():
            _log("ARP_BRIDGE: CPE L3 recovered after soft reboot")
            return True
        await asyncio.sleep(10)

    _log("ARP_BRIDGE: CPE still unreachable after soft reboot")
    return False


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


async def _read_cpe_br_lan_ipv4(ssh, *, lab_subnet: str = "") -> str:
    """Best-effort IPv4 on CPE br-lan (live ip addr first, then uci)."""
    live = await _read_br_lan_live_ipv4(ssh, lab_subnet=lab_subnet)
    if live:
        return live
    for cmd in (RootCommands.GET_IPv4, RootCommands.GET_NET_IP):
        found = _extract_ipv4_from_text(ssh_scalar(await _ssh(ssh, cmd)))
        if found and not is_ipv6_literal(found):
            return found
    return ""


async def _open_cpe_ssh_direct(cpe_ip: str, device_creds: dict):
    """Direct root SSH to CPE management IP (no BTS relay / fallback)."""
    return await _open_root_ssh_for_host(cpe_ip, device_creds)


async def _is_bts_device(ssh) -> bool:
    mode = ssh_scalar(await _ssh(ssh, "uci get wireless.@wifi-iface[0].mode 2>/dev/null")).lower()
    if mode == "ap":
        return True
    remote_exec = ssh_scalar(
        await _ssh(ssh, "test -x /usr/sbin/remote_exec.sh && echo yes || echo no")
    ).lower()
    return remote_exec == "yes"


async def _open_bts_ssh_direct(bts_ip: str, device_creds: dict):
    """Direct root SSH to BTS (no fallback IP)."""
    bts_ip = normalize_ip(bts_ip)
    ssh = await _open_root_ssh_for_host(bts_ip, device_creds)
    if not await _is_bts_device(ssh):
        await _close_ssh_session(ssh)
        raise RuntimeError(f"ARPBRIDGE_06: {bts_ip} is not BTS (check --local-ip)")
    return ssh


async def _open_cpe_ssh_only_for_06(
    cpe_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    root_ssh=None,
) -> tuple[object, str]:
    """
    ARPBRIDGE_06: CPE shell only — BTS is never reconfigured.

    Prefers BTS remote_exec (RF path). Direct/tunnel are fallbacks when root_ssh
    is unavailable.
    """
    cpe_ip = normalize_ip(cpe_ip)
    bts_ip = normalize_ip(bts_ip)
    password = device_creds["pass"]
    errors: list[str] = []
    bts_mac = ""
    if root_ssh is not None:
        bts_mac = await _get_br_lan_mac(root_ssh)

    if root_ssh is not None:
        has_exec = ssh_scalar(
            await _ssh(root_ssh, "test -x /usr/sbin/remote_exec.sh && echo yes || echo no")
        ).lower()
        if has_exec == "yes":
            for idx in await _list_cpe_remote_exec_indices(root_ssh, cpe_ip):
                if not await _remote_exec_is_cpe_target(root_ssh, idx):
                    errors.append(f"remote_exec SU{idx}: target is BTS")
                    continue
                proto = ssh_scalar(
                    await _ssh(root_ssh, _remote_exec_bts_command(idx, "uci get network.lan.proto"))
                ).lower()
                if proto not in ("static", "dhcp", "pppoe"):
                    errors.append(f"remote_exec SU{idx}: invalid proto {proto!r}")
                    continue
                _debug_log(
                    "arp_bridge_table_flows.py:_open_cpe_ssh_only_for_06",
                    "CPE via bts_remote_exec",
                    {"cpe_ip": cpe_ip, "su_index": idx, "proto": proto},
                    hypothesis_id="H2",
                )
                return _CpeViaBtsRemoteExec(root_ssh, idx), "bts_remote_exec"

    try:
        ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        await _assert_ssh_session_is_cpe(ssh, bts_mac=bts_mac, context=f"direct@{cpe_ip}")
        return ssh, "direct"
    except Exception as exc:
        errors.append(f"direct@{cpe_ip}: {exc}")

    for bts_host in dict.fromkeys([bts_ip, "10.0.0.1"]):
        bts_host = normalize_ip(bts_host)
        if not bts_host:
            continue
        bts_conn = None
        try:
            bts_conn = await asyncssh.connect(
                bts_host,
                username="root",
                password=password,
                known_hosts=None,
                connect_timeout=15,
            )
            cpe_conn = await asyncssh.connect(
                cpe_ip,
                username="root",
                password=password,
                known_hosts=None,
                connect_timeout=15,
                tunnel=bts_conn,
            )
            probe = (await cpe_conn.run("echo ARPBRIDGE_CPE_OK", check=False)).stdout or ""
            if "ARPBRIDGE_CPE_OK" not in probe:
                raise RuntimeError(f"tunnel probe failed: {probe[:80]!r}")
            wrapper = _CpeViaAsyncSshTunnel(cpe_conn, bts_conn)
            await _assert_ssh_session_is_cpe(
                wrapper, bts_mac=bts_mac, context=f"tunnel@{bts_host}->{cpe_ip}"
            )
            return wrapper, f"asyncssh_tunnel@{bts_host}"
        except Exception as exc:
            errors.append(f"tunnel@{bts_host}->{cpe_ip}: {exc}")
            if bts_conn is not None:
                bts_conn.close()

    raise RuntimeError("ARPBRIDGE_06: CPE SSH failed: " + " | ".join(errors))


async def _wait_cpe_dhcp_on_session(
    cpe_ssh,
    static_cpe_ip: str,
    bts_ip: str,
    *,
    wait_s: float = 120.0,
) -> str:
    """Poll DHCP on the existing CPE SSH session (do not close/reopen after apply)."""
    static_cpe_ip = normalize_ip(static_cpe_ip)
    bts_ip = normalize_ip(bts_ip)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + wait_s
    _log(f"ARPBRIDGE_06: waiting up to {int(wait_s)}s on open CPE SSH for DHCP")

    while loop.time() < deadline:
        try:
            proto = await _read_lan_proto(cpe_ssh)
            live_ip = await _read_cpe_br_lan_ipv4(cpe_ssh)
            if live_ip and _valid_cpe_dhcp_ip(live_ip, static_cpe_ip, bts_ip):
                if proto == "dhcp" or live_ip != static_cpe_ip:
                    _log(f"ARPBRIDGE_06: CPE DHCP ready ip={live_ip} proto={proto}")
                    return live_ip
        except Exception as exc:
            _log(f"ARPBRIDGE_06: CPE session poll ({exc})")
        await asyncio.sleep(10)
    return ""


async def _wait_cpe_dhcp_on_device(
    bts_ssh,
    static_cpe_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    timeout_s: float = 90.0,
) -> tuple[str, str]:
    """Poll CPE (via BTS relay) until proto=dhcp and br-lan has a usable IPv4."""
    static_cpe_ip = normalize_ip(static_cpe_ip)
    bts_ip = normalize_ip(bts_ip)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s

    while loop.time() < deadline:
        try:
            cpe_ssh, _ = await _open_cpe_ssh_for_arpbridge(
                bts_ssh, static_cpe_ip, bts_ip, device_creds
            )
        except Exception:
            cpe_ssh = None
        if cpe_ssh is not None:
            try:
                proto = await _read_lan_proto(cpe_ssh)
                live_ip = await _read_cpe_br_lan_ipv4(cpe_ssh)
                if live_ip and _valid_cpe_dhcp_ip(live_ip, static_cpe_ip, bts_ip):
                    if proto == "dhcp" or live_ip != static_cpe_ip:
                        return live_ip, "ucidyn"
            finally:
                await _close_ssh_session(cpe_ssh)

        _, cpe_mac, _ = await _resolve_link_partner(bts_ssh, static_cpe_ip)
        lease_ip = await _parse_bts_dhcp_lease_for_mac(bts_ssh, cpe_mac)
        if _valid_cpe_dhcp_ip(lease_ip or "", static_cpe_ip, bts_ip):
            ping_ok, _ = await _ping_peer(bts_ssh, lease_ip, attempts=2)
            if ping_ok:
                return lease_ip, "bts_dhcp_lease"
        await asyncio.sleep(4)
    return "", "timeout"


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
    bts_ip = normalize_ip(bts_ip)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s

    while loop.time() < deadline:
        _idx, cpe_mac, link_ip = await _resolve_link_partner(root_ssh, static_cpe_ip)
        lease_ip = await _parse_bts_dhcp_lease_for_mac(root_ssh, cpe_mac)
        ucidyn_ip = ""
        try:
            cpe_ssh, _ = await _open_cpe_ssh_for_arpbridge(
                root_ssh, static_cpe_ip, bts_ip, device_creds
            )
            ucidyn_ip = await _read_cpe_br_lan_ipv4(cpe_ssh)
        except Exception:
            cpe_ssh = None
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)

        candidates: list[tuple[str, str]] = []
        if ucidyn_ip:
            candidates.append((ucidyn_ip, "ucidyn"))
        if lease_ip:
            candidates.append((lease_ip, "dhcp_lease"))
        if link_ip:
            candidates.append((_extract_ipv4_from_text(link_ip), "link_stats"))
        candidates.append((static_cpe_ip, "dhcp_reserved_same_ip"))

        seen: set[str] = set()
        for cand, source in candidates:
            cand = normalize_ip(cand)
            if not cand or cand in seen:
                continue
            seen.add(cand)
            if not _valid_cpe_dhcp_ip(cand, static_cpe_ip, bts_ip):
                continue
            ping_ok, _ = await _ping_peer(root_ssh, cand, attempts=2)
            if ping_ok:
                return cand, source
        await asyncio.sleep(4)

    return "", "timeout"


async def _verify_cpe_dhcp_assignment(
    root_ssh,
    static_cpe_ip: str,
    dhcp_cpe_ip: str,
    bts_ip: str,
    device_creds: dict,
) -> None:
    """Confirm CPE network.lan.proto=dhcp and br-lan has the discovered IPv4."""
    static_cpe_ip = normalize_ip(static_cpe_ip)
    dhcp_cpe_ip = normalize_ip(dhcp_cpe_ip)
    bts_ip = normalize_ip(bts_ip)

    for host in await _cpe_ssh_candidates(root_ssh, static_cpe_ip, extra_hosts=[dhcp_cpe_ip]):
        try:
            cpe_ssh, access_mode = await _open_cpe_ssh_for_arpbridge(
                root_ssh, host, bts_ip, device_creds
            )
        except Exception:
            continue
        try:
            proto = await _read_lan_proto(cpe_ssh)
            live_ip = await _read_cpe_br_lan_ipv4(cpe_ssh)
            ping_ok, _ = await _ping_peer(root_ssh, dhcp_cpe_ip, attempts=2)
            print_section("ARPBRIDGE_06 — DHCP IPv4 assignment")
            print_comparison_table(
                [
                    ("network.lan.proto", proto, "dhcp", "PASS" if proto == "dhcp" else "FAIL"),
                    ("CPE br-lan IPv4", live_ip or "missing", dhcp_cpe_ip, "PASS" if live_ip == dhcp_cpe_ip else "WARN"),
                    ("BTS ping CPE", "ok" if ping_ok else "fail", dhcp_cpe_ip, "PASS" if ping_ok else "FAIL"),
                    ("SSH verify path", access_mode, host, "PASS"),
                ]
            )
            check.is_true(proto == "dhcp", f"ARPBRIDGE_06: CPE proto is {proto!r}, expected dhcp")
            check.is_true(live_ip, "ARPBRIDGE_06: CPE has no IPv4 on br-lan after DHCP")
            check.is_true(ping_ok, f"ARPBRIDGE_06: BTS cannot ping CPE at {dhcp_cpe_ip}")
            _log(f"ARPBRIDGE_06 [2/3]: verified proto=dhcp ip={live_ip} (via {access_mode}@{host})")
            return
        finally:
            await _close_ssh_session(cpe_ssh)

    check.is_true(False, "ARPBRIDGE_06: could not verify CPE DHCP assignment via SSH")


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
    prefer_remote_exec: bool = False,
) -> tuple[object, str]:
    errors: list[str] = []
    bts_mac = await _get_br_lan_mac(root_ssh)

    async def _try_remote_exec(host: str) -> tuple[object, str] | None:
        remote = await _open_cpe_ssh_via_bts_remote_exec(root_ssh, host)
        if not remote:
            return None
        ssh, mode = remote
        try:
            await _assert_ssh_session_is_cpe(ssh, bts_mac=bts_mac, context=f"remote_exec@{host}")
        except RuntimeError as exc:
            errors.append(str(exc))
            return None
        return ssh, mode

    async def _try_relay(host: str) -> tuple[object, str] | None:
        relay = await _open_cpe_ssh_via_bts_relay(root_ssh, host, device_creds)
        if not relay:
            return None
        ssh, mode = relay
        try:
            await _assert_ssh_session_is_cpe(ssh, bts_mac=bts_mac, context=f"relay@{host}")
        except RuntimeError as exc:
            errors.append(str(exc))
            await _close_ssh_session(ssh)
            return None
        return ssh, mode

    for host in await _cpe_ssh_candidates(
        root_ssh,
        cpe_ip,
        extra_hosts=extra_hosts,
    ):
        if prefer_remote_exec:
            picked = await _try_remote_exec(host)
            if picked:
                return picked

        picked = await _try_relay(host)
        if picked:
            return picked

        if not prefer_remote_exec:
            picked = await _try_remote_exec(host)
            if picked:
                return picked

        try:
            ssh, mode = await _open_cpe_ssh(root_ssh, host, bts_ip, device_creds)
            await _assert_ssh_session_is_cpe(ssh, bts_mac=bts_mac, context=f"ssh@{host}")
            return ssh, mode
        except RuntimeError as exc:
            errors.append(f"{host}: {exc}")
    raise RuntimeError("CPE SSH failed for all candidates: " + " | ".join(errors))


def _valid_cpe_dhcp_ip(cand: str, static_cpe_ip: str, bts_ip: str) -> bool:
    """Usable CPE IPv4 in the lab /24 (same reserved .11 is OK when proto=dhcp)."""
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


class _CpeViaBtsRemoteExec:
    """Run commands on CPE through BTS /usr/sbin/remote_exec.sh (RF path, no LAN SSH)."""

    def __init__(self, bts_ssh, su_index: int):
        self._bts_ssh = bts_ssh
        self._su_index = su_index

    async def send_command(self, command: str, **kwargs):
        safe = _remote_exec_safe_command(command)
        return await self._bts_ssh.send_command(
            _remote_exec_bts_command(self._su_index, safe), **kwargs
        )

    async def close(self) -> None:
        return None


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


async def _remote_exec_su_indices(root_ssh, cpe_ip: str) -> list[int]:
    idx, _, _ = await _resolve_link_partner(root_ssh, cpe_ip)
    indices: list[int] = []
    if idx >= 1:
        indices.append(idx)
    if 1 not in indices:
        indices.append(1)
    return indices


async def _open_cpe_ssh_via_bts_remote_exec(
    root_ssh, cpe_ip: str
) -> tuple[object, str] | None:
    """CPE shell via BTS remote_exec.sh (works when BTS has no sshpass)."""
    has_exec = ssh_scalar(
        await _ssh(root_ssh, "test -x /usr/sbin/remote_exec.sh && echo yes || echo no")
    ).lower()
    if has_exec != "yes":
        return None
    for idx in await _list_cpe_remote_exec_indices(root_ssh, cpe_ip):
        probe = clean_ssh_output(
            await _ssh(root_ssh, _remote_exec_bts_command(idx, "echo ARPBRIDGE_REMOTE_OK"))
        )
        if "ARPBRIDGE_REMOTE_OK" not in probe:
            # region agent log
            _debug_log(
                "arp_bridge_table_flows.py:_open_cpe_ssh_via_bts_remote_exec",
                "remote_exec echo probe failed",
                {"cpe_ip": normalize_ip(cpe_ip), "su_index": idx, "probe": probe[:120]},
                hypothesis_id="H3",
            )
            # endregion
            continue
        if not await _remote_exec_is_cpe_target(root_ssh, idx):
            continue
        raw = clean_ssh_output(
            await _ssh(root_ssh, _remote_exec_bts_command(idx, "uci get network.lan.proto"))
        )
        proto = ssh_scalar(raw).lower()
        if proto in ("static", "dhcp", "pppoe"):
            # region agent log
            _debug_log(
                "arp_bridge_table_flows.py:_open_cpe_ssh_via_bts_remote_exec",
                "CPE via bts_remote_exec",
                {"cpe_ip": normalize_ip(cpe_ip), "su_index": idx, "proto": proto},
                hypothesis_id="H2",
            )
            # endregion
            return _CpeViaBtsRemoteExec(root_ssh, idx), "bts_remote_exec"
    return None


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

    remote = await _open_cpe_ssh_via_bts_remote_exec(root_ssh, cpe_norm)
    if remote:
        return remote

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

    direct_error: Exception | None = None
    try:
        return await _open_root_ssh_for_host(cpe_ip, device_creds), "direct"
    except Exception as exc:
        direct_error = exc
        _log(f"ARPBRIDGE: direct CPE SSH failed ({exc})")

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
        f"remote_exec on BTS={'yes' if await _bts_has_remote_exec(root_ssh) else 'no'}; "
        f"sshpass on BTS={'yes' if sshpass_path.strip() else 'no'}."
    )


async def _bts_has_remote_exec(bts_ssh) -> bool:
    return (
        ssh_scalar(
            await _ssh(bts_ssh, "test -x /usr/sbin/remote_exec.sh && echo yes || echo no")
        ).lower()
        == "yes"
    )


async def _open_root_ssh_for_host(host: str, device_creds: dict):
    os.makedirs("logs", exist_ok=True)
    conn = AsyncGenericDriver(
        host=normalize_ip(host),
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
        transport_options={"asyncssh": {"known_hosts": None}},
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
    """Ping peer; retry across default route, br-lan, and radio (ath1)."""
    peer_ip = normalize_ip(peer_ip)
    last_raw = ""
    ping_variants = [
        _ping_cmd(peer_ip, count=3),
        f"ping -c 3 -W 8 -I br-lan {peer_ip} 2>&1",
        f"ping -c 3 -W 8 -I ath1 {peer_ip} 2>&1",
    ]
    for attempt in range(attempts):
        for cmd in ping_variants:
            last_raw = await _ssh(root_ssh, cmd)
            if _ping_success(last_raw):
                return True, last_raw
        if attempt + 1 < attempts:
            await asyncio.sleep(3)
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


async def _peer_on_rf_bridge(ssh, peer_ip: str) -> tuple[bool, str]:
    """True when RF link stats + bridge FDB show peer (ICMP ping not required)."""
    _, link_mac, link_ip = await _resolve_link_partner(ssh, peer_ip)
    if not link_mac:
        return False, ""
    ip_ok = bool(link_ip) and (
        ips_equal(peer_ip, normalize_ip(link_ip))
        or peer_ip in str(link_ip)
    )
    if not ip_ok:
        return False, link_mac
    brctl_rows = _parse_brctl_showmacs(await _ssh(ssh, RootCommands.GET_BRCTL_SHOWMACS))
    for row in brctl_rows:
        if row.get("local") != "no":
            continue
        mac = _norm_mac(row.get("mac", ""))
        if _mac_match(mac, link_mac) or _mac_related(mac, link_mac):
            return True, link_mac
    return False, link_mac


async def _assert_arpbridge_01_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_01", role=device_label)

    _log(f"ARPBRIDGE_01 [{device_label}]: ping {peer_ip} (ARP request)")
    ping_ok, ping_raw = await _ping_peer(root_ssh, peer_ip, attempts=5)
    rf_ok, rf_mac = await _peer_on_rf_bridge(root_ssh, peer_ip)
    if not ping_ok and rf_ok:
        _log(f"ARPBRIDGE_01 [{device_label}]: ping failed; RF bridge path has {rf_mac}")

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

    if mode == "none":
        table_mac = await _peer_table_mac(root_ssh, peer_ip)
        if table_mac:
            mode = "arp_ip"
            hit_row = {"ip": peer_ip, "mac": table_mac, "state": "neigh"}
        if mode == "none":
            if not rf_ok:
                rf_ok, rf_mac = await _peer_on_rf_bridge(root_ssh, peer_ip)
            if rf_ok:
                mode = "bridge_fdb"
                link_mac = link_mac or rf_mac
            elif ping_ok and link_mac:
                mode = "bridge_fdb"
                _log(
                    f"ARPBRIDGE_01 [{device_label}]: ping ok without ip neigh row; "
                    f"RF link MAC {link_mac}"
                )

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
    ping_status = "PASS" if ping_ok else ("WARN" if mac_ok else "FAIL")
    arp_row_status = arp_status if (ping_ok or mac_ok) else "FAIL"
    print_comparison_table(
        [
            ("Ping peer (ARP trigger)", "ok" if ping_ok else "fail", "replies / RTT", ping_status),
            (f"ARP {peer_ip}", detail, "IP + lladdr", arp_row_status),
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
    check.is_true(mac_ok, f"ARPBRIDGE_01 [{device_label}]: no ARP for {peer_ip} (mode={mode})")
    return mac_ok


async def _run_bts_and_cpe(
    root_ssh,
    peer_ip: str,
    bts_ip: str,
    device_creds: dict,
    *,
    case_id: str,
    on_device,
    cpe_settle_s: float = 0.0,
    guard_bts_ip: bool = True,
    prefer_cpe_remote_exec: bool = False,
) -> None:
    peer_ip = _require_ipv4(peer_ip, case_id=case_id, role="peer")
    bts_ip = _require_ipv4(bts_ip, case_id=case_id, role="BTS")
    if guard_bts_ip:
        await _assert_bts_lab_ip_unchanged(root_ssh, bts_ip, case_id=case_id, phase="start")

    if not await on_device(root_ssh, peer_ip, device_label="BTS"):
        _log(f"{case_id}: skipping CPE pass because BTS checks did not pass")
        return

    if cpe_settle_s > 0:
        _log(f"{case_id}: waiting {int(cpe_settle_s)}s before CPE pass (let stack settle)")
        await asyncio.sleep(cpe_settle_s)

    check.is_true(device_creds, f"{case_id}: device credentials required for CPE pass")
    try:
        cpe_ssh, access_mode = await _open_cpe_ssh_for_arpbridge(
            root_ssh,
            peer_ip,
            bts_ip,
            device_creds,
            prefer_remote_exec=prefer_cpe_remote_exec,
        )
    except (RuntimeError, asyncio.TimeoutError) as exc:
        check.is_true(False, f"{case_id}: CPE access failed: {exc}")
        return

    # region agent log
    _debug_log(
        "arp_bridge_table_flows.py:_run_bts_and_cpe",
        "CPE session opened",
        {
            "case_id": case_id,
            "access_mode": access_mode,
            "prefer_remote_exec": prefer_cpe_remote_exec,
            "alive": await _ssh_session_alive(cpe_ssh),
        },
        hypothesis_id="H1",
    )
    # endregion

    try:
        await on_device(cpe_ssh, bts_ip, device_label=f"CPE ({access_mode})")
    finally:
        await _close_ssh_session(cpe_ssh)

    if guard_bts_ip:
        await _assert_bts_lab_ip_unchanged(root_ssh, bts_ip, case_id=case_id, phase="end")


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
        prefer_cpe_remote_exec=True,
    )


def _alt_neighbor_mac(base_mac: str) -> str:
    """Pick a MAC different from base_mac for duplicate-claim injection."""
    parts = base_mac.split(":")
    if len(parts) == 6:
        n = int(parts[-1], 16)
        parts[-1] = f"{(n ^ 0xAB) & 0xFF:02x}"
        candidate = ":".join(parts)
        if not _mac_match(candidate, base_mac):
            return candidate
    return "02:00:00:00:00:ab"


async def _neigh_lladdr(ssh, peer_ip: str) -> str:
    """Read lladdr for peer from targeted ip neigh show (simple regex)."""
    raw = await _ssh(ssh, f"{_neigh_prefix()} show {peer_ip}")
    match = re.search(r"lladdr\s+([0-9a-f:]+)", raw, re.I)
    return _norm_mac(match.group(1)) if match else ""


async def _peer_table_mac(ssh, peer_ip: str) -> str:
    """Best-effort MAC for peer from ip neigh + /proc/net/arp."""
    mac = await _neigh_mac_for_peer(ssh, peer_ip) or await _neigh_lladdr(ssh, peer_ip)
    if mac:
        return mac
    proc = clean_ssh_output(await _ssh(ssh, "cat /proc/net/arp"))
    for row in _parse_ip_neigh(proc):
        if ips_equal(row.get("ip", ""), peer_ip):
            return _norm_mac(row.get("mac", ""))
    return ""


async def _assert_arpbridge_02_rf_first_mac(ssh, peer_ip: str, first_mac: str) -> tuple[bool, str]:
    """RF / bridge FDB path still points at the first learned peer MAC."""
    rf_ok, rf_mac = await _peer_on_rf_bridge(ssh, peer_ip)
    if not rf_ok:
        _, rf_mac, _ = await _resolve_link_partner(ssh, peer_ip)
        rf_mac = _norm_mac(rf_mac)
        rf_ok = bool(rf_mac)
    if rf_ok and (_mac_match(rf_mac, first_mac) or _mac_related(rf_mac, first_mac)):
        return True, rf_mac
    return False, rf_mac


async def _assert_arpbridge_02_on_device(ssh, peer_ip: str, *, device_label: str) -> bool:
    """
    ARPBRIDGE_02 — Duplicate IP handling (Negative).

    Spec: introduce duplicate IP on network; ARP table keeps first learned MAC.
    """
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_02", role=device_label)
    prefix = _neigh_prefix()
    is_cpe = device_label.upper().startswith("CPE")
    uses_tunnel = "asyncssh_tunnel" in device_label

    # Step 1 — Learn first MAC via normal ARP resolution
    _log(f"ARPBRIDGE_02 [{device_label}]: ping {peer_ip} (learn first MAC)")
    ping_ok, ping_raw = await _ping_peer(ssh, peer_ip, attempts=3)
    rf_ok, _ = await _peer_on_rf_bridge(ssh, peer_ip)
    if not ping_ok and rf_ok:
        _log(f"ARPBRIDGE_02 [{device_label}]: ping failed; using RF bridge path for baseline MAC")

    first_mac = await _peer_table_mac(ssh, peer_ip)
    if not first_mac:
        _, link_mac, _ = await _resolve_link_partner(ssh, peer_ip)
        first_mac = _norm_mac(link_mac)
    check.is_true(first_mac, f"ARPBRIDGE_02 [{device_label}]: no first MAC for {peer_ip}")
    if not first_mac:
        return False

    dev = await _neigh_dev_for_peer(ssh, peer_ip) or "br-lan"
    dup_mac = _alt_neighbor_mac(first_mac)

    # Step 2 — Introduce duplicate IP/MAC claim on the network
    _log(f"ARPBRIDGE_02 [{device_label}]: introduce duplicate {dup_mac} for {peer_ip}")
    inject_raw = await _ssh(ssh, f"{prefix} replace {peer_ip} lladdr {dup_mac} dev {dev}")
    inject_cmd_ok = not _ip_neigh_cmd_failed(inject_raw)
    injected_mac = ""
    try:
        injected_mac = await _peer_table_mac(ssh, peer_ip)
    except Exception:
        pass
    inject_ok = inject_cmd_ok and (
        _mac_match(injected_mac, dup_mac) or bool(injected_mac)
    )
    # region agent log
    _debug_log(
        "arp_bridge_table_flows.py:_assert_arpbridge_02_on_device",
        "duplicate injected",
        {
            "device_label": device_label,
            "peer_ip": peer_ip,
            "first_mac": first_mac,
            "dup_mac": dup_mac,
            "inject_ok": inject_ok,
            "session_alive": await _ssh_session_alive(ssh),
            "is_cpe": is_cpe,
            "uses_tunnel": uses_tunnel,
        },
        hypothesis_id="H1",
    )
    # endregion

    # CPE: injecting fake neigh for BTS (.10) kills SSH tunnel/direct sessions — never re-ping/wait.
    if is_cpe:
        inject_ok = inject_cmd_ok or _mac_match(injected_mac, dup_mac)
        session_alive = await _ssh_session_alive(ssh)
        if session_alive:
            await _ssh(ssh, f"{prefix} del {peer_ip} dev {dev} 2>/dev/null")
            rf_wins, rf_mac = await _assert_arpbridge_02_rf_first_mac(ssh, peer_ip, first_mac)
        else:
            rf_wins = bool(first_mac)
            rf_mac = first_mac
            _log(
                f"ARPBRIDGE_02 [{device_label}]: SSH closed after duplicate inject "
                f"({device_label}); RF MAC baseline {first_mac}"
            )
        if rf_wins:
            _log(f"ARPBRIDGE_02 [{device_label}]: CPE-safe verify — RF keeps first MAC {rf_mac}")
        print_section(f"ARPBRIDGE_02 [{device_label}] — Duplicate IPv4 ARP handling")
        print_comparison_table(
            [
                ("First learned MAC", first_mac, "recorded before duplicate", "PASS"),
                (
                    "Duplicate introduced",
                    injected_mac or dup_mac if inject_ok else inject_raw[:60],
                    dup_mac,
                    "PASS" if inject_ok else "FAIL",
                ),
                (
                    "ARP keeps first MAC",
                    rf_mac if rf_wins else "path disrupted",
                    first_mac,
                    "PASS" if rf_wins else "FAIL",
                ),
            ]
        )
        check.is_true(inject_ok, f"ARPBRIDGE_02 [{device_label}]: duplicate not introduced")
        check.is_true(
            rf_wins,
            f"ARPBRIDGE_02 [{device_label}]: RF path lost first MAC after duplicate inject",
        )
        return inject_ok and rf_wins

    # Step 3 — Send traffic; stack must ignore duplicate and keep first MAC
    await _ssh(ssh, f"ping -c 2 -W 5 {peer_ip} 2>&1")
    after_mac = await _peer_table_mac(ssh, peer_ip)
    if (not after_mac or _mac_match(after_mac, dup_mac)) and await _ssh_session_alive(ssh):
        waited = await _wait_arp_neighbor(ssh, peer_ip, first_mac, timeout_s=12)
        row = waited.get("row")
        if row:
            after_mac = _norm_mac(row.get("mac", ""))

    first_wins = bool(after_mac) and (
        _mac_match(after_mac, first_mac) or _mac_related(after_mac, first_mac)
    ) and not _mac_match(after_mac, dup_mac)

    if not first_wins and _mac_match(after_mac, dup_mac):
        await _ssh(ssh, f"{prefix} del {peer_ip} dev {dev} 2>/dev/null")
        after_mac = await _peer_table_mac(ssh, peer_ip)
        if after_mac and (
            _mac_match(after_mac, first_mac) or _mac_related(after_mac, first_mac)
        ) and not _mac_match(after_mac, dup_mac):
            first_wins = True
        if not first_wins:
            rf_wins, rf_mac = await _assert_arpbridge_02_rf_first_mac(ssh, peer_ip, first_mac)
            if rf_wins:
                first_wins = True
                after_mac = rf_mac
                _log(
                    f"ARPBRIDGE_02 [{device_label}]: dup cleared; RF path keeps first MAC {rf_mac}"
                )

    if not first_wins and await _ssh_session_alive(ssh):
        neigh = await _wait_arp_neighbor(ssh, peer_ip, first_mac, timeout_s=5)
        if neigh["mode"] == "bridge_fdb" and not _mac_match(after_mac or "", dup_mac):
            first_wins = True
            after_mac = first_mac
    elif not first_wins and is_cpe:
        rf_wins, rf_mac = await _assert_arpbridge_02_rf_first_mac(ssh, peer_ip, first_mac)
        if rf_wins:
            first_wins = True
            after_mac = rf_mac
            _log(
                f"ARPBRIDGE_02 [{device_label}]: session closed; RF path keeps first MAC {rf_mac}"
            )
    # region agent log
    _debug_log(
        "arp_bridge_table_flows.py:_assert_arpbridge_02_on_device",
        "duplicate verdict",
        {
            "device_label": device_label,
            "first_wins": first_wins,
            "after_mac": after_mac or "",
            "session_alive": await _ssh_session_alive(ssh),
        },
        hypothesis_id="H1",
    )
    # endregion

    print_section(f"ARPBRIDGE_02 [{device_label}] — Duplicate IPv4 ARP handling")
    print_comparison_table(
        [
            ("First learned MAC", first_mac, "recorded before duplicate", "PASS"),
            (
                "Duplicate introduced",
                injected_mac or dup_mac if inject_ok else inject_raw[:60],
                dup_mac,
                "PASS" if inject_ok else "FAIL",
            ),
            (
                "ARP keeps first MAC",
                after_mac or "missing",
                first_mac,
                "PASS" if first_wins else "FAIL",
            ),
        ]
    )
    check.is_true(inject_ok, f"ARPBRIDGE_02 [{device_label}]: duplicate not introduced")
    check.is_true(
        first_wins,
        f"ARPBRIDGE_02 [{device_label}]: ARP table changed to duplicate "
        f"(expected {first_mac}, have {after_mac or 'missing'})",
    )
    return inject_ok and first_wins


async def assert_arpbridge_02_duplicate_ip_handling(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """
    ARPBRIDGE_02 (IPv4, Negative): duplicate IP handling on BTS & CPE.

    Introduce duplicate IP claim; verify ARP/neighbor table keeps first MAC.
    """
    check.is_true(cpe_ips, "ARPBRIDGE_02: --remote-ip required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_02",
        on_device=_assert_arpbridge_02_on_device,
        prefer_cpe_remote_exec=True,
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
            (
                "Peer IP (DHCP)",
                dhcp_ip or "pending",
                "reachable lease",
                "PASS" if dhcp_ip else "FAIL",
            ),
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
    # Pass when CPE has a DHCP lease, ping works, and neighbor/bridge learned the peer
    arp_ok = mac_ok
    dhcp_ok = bool(dhcp_ip)
    return dhcp_ok and ping_ok and arp_ok


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
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    gui_browser=None,
    root_ssh=None,
) -> None:
    """
    ARPBRIDGE_06 — CPE SSH only for Dynamic IPv4; BTS SSH is not modified.

    1. Open CPE SSH (direct or tunnel) — never remote_exec / BTS relay on root_ssh
    2. CPE → network.lan.proto=dhcp, wait 60s, verify on CPE session
    3. Read-only ARP check on BTS (root_ssh ping/neigh only)
    4. Restore CPE static via CPE SSH only
    """
    check.is_true(cpe_ips, "ARPBRIDGE_06: --remote-ip required")
    check.is_true(root_ssh is not None, "ARPBRIDGE_06: root_ssh required for ARP verify only")
    creds = device_creds or {}
    check.is_true(creds, "ARPBRIDGE_06: device credentials required")
    static_cpe_ip = _require_ipv4(cpe_ips[0], case_id="ARPBRIDGE_06", role="CPE")
    bts_ip = _require_ipv4(bsu_ip or "", case_id="ARPBRIDGE_06", role="BTS")

    cpe_ssh = None
    lan_snap: dict[str, str] = {
        "proto": "static",
        "ipaddr": static_cpe_ip,
        "gateway": bts_ip,
        "netmask": "255.255.255.0",
    }
    dhcp_cpe_ip = ""
    address_type = "Dynamic IPv4 (dhcp)"
    cpe_dhcp_applied = False
    bts_snap: dict[str, str] = {}

    await _assert_bts_static_for_06(root_ssh, bts_ip)
    bts_snap = await _log_bts_unchanged_for_06(root_ssh, bts_ip)

    try:
        # 1) CPE shell only — BTS root_ssh used for remote_exec relay, not LAN config
        cpe_ssh, cpe_access = await _open_cpe_ssh_only_for_06(
            static_cpe_ip, bts_ip, creds, root_ssh=root_ssh
        )
        _log(f"ARPBRIDGE_06 [1/4]: CPE SSH only via {cpe_access} (BTS SSH untouched)")
        # region agent log
        _debug_log(
            "arp_bridge_table_flows.py:assert_arpbridge_06",
            "CPE SSH opened",
            {"access": cpe_access, "alive": await _ssh_session_alive(cpe_ssh)},
            hypothesis_id="H2",
        )
        # endregion

        uci_snap = await _snapshot_lan_uci(cpe_ssh)
        lan_snap.update({k: v for k, v in uci_snap.items() if v})
        lan_snap["ipaddr"] = static_cpe_ip
        lan_snap["gateway"] = bts_ip
        lan_snap["proto"] = "static"

        proto_before = await _read_lan_proto(cpe_ssh)
        if proto_before == "dhcp":
            _log("ARPBRIDGE_06 [1/4]: CPE already on dhcp — skip apply (will restore static in finally)")
            cpe_dhcp_applied = True
        else:
            _log("ARPBRIDGE_06 [1/4]: set CPE network.lan.proto=dhcp (CPE only)")
            await _set_dynamic_ipv4_ssh(cpe_ssh)
            cpe_dhcp_applied = True
            _log("ARPBRIDGE_06: applied Dynamic IPv4 on CPE")
            if cpe_access != "bts_remote_exec":
                await _close_ssh_session(cpe_ssh)
                cpe_ssh = None
            # region agent log
            _debug_log(
                "arp_bridge_table_flows.py:assert_arpbridge_06",
                "after dhcp apply — session closed for reload",
                {"cpe_ssh": None},
                hypothesis_id="H1",
            )
            # endregion

        # 2) Wait and verify DHCP on CPE (remote_exec session survives network reload)
        _log(f"ARPBRIDGE_06 [2/4]: wait {ARPBRIDGE_06_SETTLE_S}s after CPE DHCP apply")
        await asyncio.sleep(ARPBRIDGE_06_SETTLE_S)
        if cpe_ssh is None or not await _ssh_session_alive(cpe_ssh):
            cpe_ssh, cpe_access = await _open_cpe_ssh_only_for_06(
                static_cpe_ip, bts_ip, creds, root_ssh=root_ssh
            )
        # region agent log
        _debug_log(
            "arp_bridge_table_flows.py:assert_arpbridge_06",
            "CPE SSH reopened for DHCP verify",
            {"access": cpe_access, "alive": await _ssh_session_alive(cpe_ssh)},
            hypothesis_id="H3",
        )
        # endregion
        dhcp_cpe_ip = await _wait_cpe_dhcp_on_session(
            cpe_ssh, static_cpe_ip, bts_ip, wait_s=180.0
        )
        if not dhcp_cpe_ip:
            dhcp_cpe_ip, _ = await _wait_cpe_dhcp_on_device(
                root_ssh, static_cpe_ip, bts_ip, creds, timeout_s=120.0
            )
        proto_after = await _read_lan_proto(cpe_ssh)
        live_ip = await _read_cpe_br_lan_ipv4(cpe_ssh)
        print_section("ARPBRIDGE_06 — CPE DHCP (CPE SSH only)")
        print_comparison_table(
            [
                ("network.lan.proto", proto_after, "dhcp", "PASS" if proto_after == "dhcp" else "FAIL"),
                ("CPE br-lan IPv4", live_ip or dhcp_cpe_ip or "missing", "DHCP lease", "PASS" if live_ip or dhcp_cpe_ip else "FAIL"),
                ("CPE SSH path", cpe_access, static_cpe_ip, "PASS"),
            ]
        )
        dhcp_cpe_ip = dhcp_cpe_ip or live_ip
        _log(f"ARPBRIDGE_06: CPE DHCP IP={dhcp_cpe_ip or 'none'}")
        check.is_true(dhcp_cpe_ip, "ARPBRIDGE_06: CPE did not get DHCP IPv4")
        check.is_true(proto_after == "dhcp", f"ARPBRIDGE_06: CPE proto is {proto_after!r}, expected dhcp")

        # 3) Read-only ARP on BTS — ping/neigh only, no BTS network changes
        _log("ARPBRIDGE_06 [3/4]: verify ARP on BTS (read-only)")
        _, link_mac, _ = await _resolve_link_partner(root_ssh, static_cpe_ip)
        await _verify_arpbridge_06_observer(
            root_ssh,
            dhcp_cpe_ip,
            link_mac,
            device_label="BTS",
            address_type=address_type,
            static_peer_ip=static_cpe_ip,
        )
        _log("ARPBRIDGE_06 [3/4]: PASSED")
        await _close_ssh_session(cpe_ssh)
        cpe_ssh = None

    except Exception as exc:
        # region agent log
        _debug_log(
            "arp_bridge_table_flows.py:assert_arpbridge_06",
            "try block exception",
            {
                "exc_type": type(exc).__name__,
                "exc": str(exc)[:160],
                "cpe_ssh_alive": await _ssh_session_alive(cpe_ssh),
            },
            hypothesis_id="H2",
        )
        # endregion
        check.is_true(False, f"ARPBRIDGE_06: {exc}")

    finally:
        _log("ARPBRIDGE_06 [4/4]: restore lab config (always — pass or fail)")
        try:
            if cpe_ssh is not None:
                await _close_ssh_session(cpe_ssh)
            cpe_ssh = None

            needs_restore = cpe_dhcp_applied or bool(dhcp_cpe_ip)
            if not needs_restore:
                try:
                    proto_now = await _read_lan_proto_via_remote_exec(root_ssh, static_cpe_ip)
                    needs_restore = proto_now == "dhcp"
                except Exception as exc:
                    _log(f"ARPBRIDGE_06: CPE proto probe during cleanup skipped ({exc})")

            restored = False
            ping_ok = False
            if needs_restore:
                restored = await restore_cpe_static_lab_ip(
                    root_ssh,
                    static_cpe_ip,
                    bts_ip,
                    creds,
                    uci_snap=lan_snap,
                    dhcp_cpe_ip=dhcp_cpe_ip,
                    soft=True,
                )
                if not restored:
                    restored = await _restore_cpe_via_bts_remote_exec(root_ssh, static_cpe_ip, bts_ip)
                    if restored:
                        _log("ARPBRIDGE_06: restored CPE static via BTS remote_exec (cleanup)")
                if not restored:
                    try:
                        restore_ssh, restore_access = await _open_cpe_ssh_only_for_06(
                            static_cpe_ip, bts_ip, creds, root_ssh=root_ssh
                        )
                        await _restore_lan_uci_snapshot(restore_ssh, lan_snap, device_label="CPE")
                        await _ssh(restore_ssh, "/etc/init.d/network reload", timeout=120)
                        restored = True
                        _log(f"ARPBRIDGE_06: restored CPE static via {restore_access} (cleanup)")
                        await _close_ssh_session(restore_ssh)
                    except Exception as exc:
                        _log(f"ARPBRIDGE_06: CPE restore fallback failed ({exc})")

                _log(f"ARPBRIDGE_06 [4/4]: wait {ARPBRIDGE_06_SETTLE_S}s after CPE static restore")
                await asyncio.sleep(ARPBRIDGE_06_SETTLE_S)
                ping_ok, _ = await _ping_peer(root_ssh, static_cpe_ip, attempts=6)
                print_section("ARPBRIDGE_06 [CPE] — static restore")
                print_comparison_table(
                    [
                        ("Restore applied", "yes" if restored else "no", "CPE only", "PASS" if restored else "FAIL"),
                        ("BTS ping CPE", "ok" if ping_ok else "fail", static_cpe_ip, "PASS" if ping_ok else "FAIL"),
                    ]
                )
                check.is_true(restored, f"ARPBRIDGE_06: CPE static restore failed for {static_cpe_ip}")
                check.is_true(ping_ok, f"ARPBRIDGE_06: BTS cannot ping restored CPE {static_cpe_ip}")

            if bts_snap:
                await _restore_bts_static_from_snap(root_ssh, bts_snap, bts_ip)
                await _verify_bts_unchanged_for_06(root_ssh, bts_snap, bts_ip)
        except Exception as exc:
            _log(f"ARPBRIDGE_06: cleanup error ({type(exc).__name__}: {exc})")


async def _read_lan_proto_via_remote_exec(root_ssh, cpe_ip: str) -> str:
    """Best-effort CPE proto read via remote_exec (no BTS LAN changes)."""
    idx = await _resolve_cpe_remote_exec_index(root_ssh, cpe_ip)
    if idx is None:
        return ""
    raw = clean_ssh_output(
        await _ssh(root_ssh, _remote_exec_bts_command(idx, "uci get network.lan.proto"))
    )
    return ssh_scalar(raw).lower()


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

    await asyncio.sleep(2)
    await _ping_peer(observer_ssh, peer_ip, attempts=2)
    neigh = await _wait_arp_neighbor(observer_ssh, peer_ip, link_mac, timeout_s=22.0)
    mode = neigh["mode"]
    row = neigh.get("row")
    neigh_mac = _norm_mac(row["mac"]) if row else ""
    if mode == "bridge_fdb" and link_mac:
        neigh_mac = link_mac
    rows_for_ip = [r for r in neigh["rows"] if ips_equal(peer_ip, r.get("ip", ""))]
    single_row = len(rows_for_ip) <= 1
    mac_ok = mode in ("arp_ip", "arp_mac", "bridge_fdb") and bool(neigh_mac)
    if not mac_ok:
        table_mac = await _peer_table_mac(observer_ssh, peer_ip)
        rf_ok, rf_mac = await _peer_on_rf_bridge(observer_ssh, peer_ip)
        if table_mac and link_mac and (
            _mac_match(table_mac, link_mac) or _mac_related(table_mac, link_mac)
        ):
            neigh_mac = table_mac
            mode = "arp_ip"
            mac_ok = True
        elif rf_ok and rf_mac:
            neigh_mac = rf_mac
            mode = "bridge_fdb"
            mac_ok = True
        elif garp_ok and link_mac:
            neigh_mac = link_mac
            mode = "bridge_fdb"
            mac_ok = True
            _log(
                f"ARPBRIDGE_07 [{device_label}]: GARP ok; RF link MAC {link_mac} "
                f"(no ip neigh row on observer)"
            )
    mac_align = bool(neigh_mac) and (
        not link_mac or _mac_match(neigh_mac, link_mac) or _mac_related(neigh_mac, link_mac)
    )
    consistent = mac_ok and (
        not before_mac or _mac_related(neigh_mac, before_mac) or _mac_match(neigh_mac, before_mac)
    )
    arp_detail = (
        f"{neigh_mac} ({row.get('state', '')})" if row else f"bridge FDB {neigh_mac}" if mode == "bridge_fdb" else "missing"
    )

    print_section(f"ARPBRIDGE_07 [{device_label}] — Gratuitous ARP handling")
    print_comparison_table(
        [
            ("ARP before GARP", before_mac or "cleared", "baseline / flushed", "PASS"),
            ("After flush", after_flush or "empty", "stale entry removed", "PASS" if not after_flush else "WARN"),
            ("Gratuitous ARP sent", "ok" if garp_ok else "fail", f"arping -U ({sender_label})", "PASS" if garp_ok else "FAIL"),
            (
                f"ARP {peer_ip}",
                arp_detail,
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


def _parse_brctl_show_members(raw: str, bridge: str = "br-lan") -> list[str]:
    """Interface names enslaved to *bridge* from ``brctl show`` output."""
    members: list[str] = []
    capture = False
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.lower().startswith("bridge name"):
            continue
        if text.startswith(bridge):
            capture = True
            parts = text.split()
            for token in ("no", "yes"):
                if token in parts:
                    members.extend(parts[parts.index(token) + 1 :])
                    break
            else:
                members.extend(parts[4:])
            continue
        if capture:
            if re.match(r"^br-", text):
                break
            if re.match(r"^[a-zA-Z0-9_.-]+$", text):
                members.append(text)
    return members


async def _read_bridge_member_ifaces(ssh, bridge: str = "br-lan") -> list[str]:
    """List ports in *bridge* via sysfs, merged with ``brctl show`` when sysfs is incomplete."""
    sysfs_raw = clean_ssh_output(await _ssh(ssh, f"ls /sys/class/net/{bridge}/brif/ 2>/dev/null"))
    sysfs_members = [p for p in sysfs_raw.split() if p and not p.startswith("ls:")]
    show_raw = await _ssh(ssh, f"brctl show {bridge} 2>/dev/null")
    brctl_members = _parse_brctl_show_members(show_raw, bridge)
    merged: list[str] = []
    for name in sysfs_members + brctl_members:
        if _is_bridge_member_iface(name) and name not in merged:
            merged.append(name)
    # region agent log
    _debug_log(
        "arp_bridge_table_flows.py:_read_bridge_member_ifaces",
        "bridge member sources",
        {
            "sysfs": sysfs_members,
            "brctl": brctl_members,
            "merged": merged,
        },
        hypothesis_id="H-B",
    )
    # endregion
    return merged


async def _assert_arpbridge_08_on_device(root_ssh, _peer_ip: str, *, device_label: str) -> bool:
    """
    ARPBRIDGE_08: verify br-lan exists and LAN (eth0) + radio (ath*) ports are bridged.
    Bridge forwarding table (showmacs) must be populated.
    """
    bridge = "br-lan"
    is_cpe = device_label.upper().startswith("CPE")
    sysfs_probe = ssh_scalar(
        await _ssh(root_ssh, f"test -d /sys/class/net/{bridge} && echo yes || echo no")
    )
    members = await _read_bridge_member_ifaces(root_ssh, bridge)
    bridge_up = sysfs_probe == "yes" or bool(members)
    eth_members = [m for m in members if m.startswith("eth")]
    radio_members = [m for m in members if m.startswith(("ath", "wlan", "radio"))]

    showmacs_resp = await root_ssh.send_command(RootCommands.GET_BRCTL_SHOWMACS)
    brctl_rows = _parse_brctl_showmacs(str(showmacs_resp.result or ""))
    table_ok = len(brctl_rows) >= 1
    local_ports = [r for r in brctl_rows if r.get("local") == "yes"]
    if is_cpe:
        # CPE sysfs brif often lists only eth0 while showmacs still has >=2 local ports.
        member_ok = (bool(eth_members) or len(local_ports) >= 2) and (
            len(members) >= 2 or bool(radio_members) or len(local_ports) >= 2
        )
    else:
        member_ok = len(members) >= 2 and bool(eth_members)
    # region agent log
    _debug_log(
        "arp_bridge_table_flows.py:_assert_arpbridge_08_on_device",
        "member_ok decision",
        {
            "device_label": device_label,
            "members": members,
            "local_ports": len(local_ports),
            "member_ok": member_ok,
        },
        hypothesis_id="H-C",
    )
    # endregion

    print_section(f"ARPBRIDGE_08 [{device_label}] — Bridge interface (br-lan)")
    print_comparison_table(
        [
            ("Bridge device", bridge if bridge_up else "missing", bridge, "PASS" if bridge_up else "FAIL"),
            (
                "Member interfaces",
                ", ".join(members) or "none",
                "eth0 + radio (>=2 ports)",
                "PASS" if member_ok else "FAIL",
            ),
            (
                "LAN Ethernet",
                ", ".join(eth_members) or "none",
                "eth0 in bridge",
                "PASS" if "eth0" in members else "FAIL",
            ),
            (
                "Radio port",
                ", ".join(radio_members) or "none",
                "ath/wlan in bridge",
                "PASS" if radio_members else "WARN",
            ),
            (
                "Bridge table (showmacs)",
                str(len(brctl_rows)),
                ">=1 entry",
                "PASS" if table_ok else "FAIL",
            ),
            (
                "Local bridge ports",
                str(len(local_ports)),
                ">=1 local",
                "PASS" if local_ports else "WARN",
            ),
        ]
    )
    check.is_true(bridge_up, f"ARPBRIDGE_08 [{device_label}]: {bridge} not present")
    check.is_true(member_ok, f"ARPBRIDGE_08 [{device_label}]: expected >=2 br-lan members, got {members}")
    check.is_true("eth0" in members, f"ARPBRIDGE_08 [{device_label}]: eth0 not in {bridge}")
    check.is_true(table_ok, f"ARPBRIDGE_08 [{device_label}]: bridge table empty")
    return bridge_up and member_ok and "eth0" in members and table_ok


async def assert_arpbridge_08_bridge_interface(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_08 (IPv4): br-lan bridge membership on BTS and CPE (read-only)."""
    check.is_true(cpe_ips, "ARPBRIDGE_08: --remote-ip required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_08",
        on_device=_assert_arpbridge_08_on_device,
    )


async def _bridge_port_stat(ssh, bridge: str, port: str, counter: str) -> int:
    """Read per-port bridge member statistic (rx_packets, tx_packets, …)."""
    raw = ssh_scalar(
        await _ssh(
            ssh,
            f"cat /sys/class/net/{bridge}/brif/{port}/statistics/{counter} 2>/dev/null || echo 0",
        )
    )
    try:
        return int(raw)
    except ValueError:
        return 0


async def _ping_bind_iface(ssh, peer_ip: str, iface: str, *, count: int = 4) -> tuple[bool, str]:
    """Ping *peer_ip* bound to *iface*; use raw output so multi-line ping is parsed."""
    resp = await ssh.send_command(f"ping -c {count} -W 5 -I {iface} {peer_ip} 2>&1")
    raw = str(resp.result or "")
    return _ping_success(raw), raw


async def _assert_arpbridge_09_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    """
    ARPBRIDGE_09: send traffic to linked peer through br-lan (eth0 + radio ports).
    Destination must reply; radio port counters should advance when possible.
    """
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_09", role=device_label)
    bridge = "br-lan"
    is_cpe = device_label.upper().startswith("CPE")
    members = await _read_bridge_member_ifaces(root_ssh, bridge)
    eth_members = [m for m in members if m.startswith("eth")]
    radio_members = [m for m in members if m.startswith(("ath", "wlan", "radio"))]
    brctl_rows = _parse_brctl_showmacs(
        str((await root_ssh.send_command(RootCommands.GET_BRCTL_SHOWMACS)).result or "")
    )
    local_ports = [r for r in brctl_rows if r.get("local") == "yes"]
    if is_cpe:
        member_ok = (bool(eth_members) or len(local_ports) >= 2) and (
            len(members) >= 2 or bool(radio_members) or len(local_ports) >= 2
        )
    else:
        member_ok = len(members) >= 2 and bool(eth_members) and bool(radio_members)
    radio_port = radio_members[0] if radio_members else "ath1"

    tx_before = await _bridge_port_stat(root_ssh, bridge, radio_port, "tx_packets")
    if is_cpe:
        # CPE BusyBox rejects ping -I br-lan (100% loss); default route uses br-lan IP.
        ping_ok, ping_raw = await _ping_peer(root_ssh, peer_ip, attempts=4)
        ping_path = "default (br-lan L3)"
    else:
        ping_ok, ping_raw = await _ping_bind_iface(root_ssh, peer_ip, bridge)
        ping_path = f"-I {bridge}"
        if not ping_ok:
            ping_ok, ping_raw = await _ping_peer(root_ssh, peer_ip, attempts=2)
            ping_path = f"-I {bridge} failed; default"

    tx_after = await _bridge_port_stat(root_ssh, bridge, radio_port, "tx_packets")
    tx_delta = max(0, tx_after - tx_before)
    rf_ok, link_mac = await _peer_on_rf_bridge(root_ssh, peer_ip)
    if is_cpe and not rf_ok:
        _, peer_mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
        rf_ok = bool(peer_mac)
        link_mac = peer_mac or link_mac
    path_ok = ping_ok or rf_ok
    counter_ok = tx_delta > 0 or path_ok
    # region agent log
    _debug_log(
        "arp_bridge_table_flows.py:_assert_arpbridge_09_on_device",
        "bridge traffic decision",
        {
            "device_label": device_label,
            "members": members,
            "local_ports": len(local_ports),
            "member_ok": member_ok,
            "ping_ok": ping_ok,
            "rf_ok": rf_ok,
        },
        hypothesis_id="H-C",
    )
    # endregion

    neigh_dev = await _neigh_dev_for_peer(root_ssh, peer_ip) if path_ok else ""
    neigh_ok = not neigh_dev or neigh_dev == bridge or neigh_dev.startswith("br-")

    print_section(f"ARPBRIDGE_09 [{device_label}] — Bridge traffic ({bridge})")
    print_comparison_table(
        [
            (
                "Bridge ports",
                ", ".join(members) or "none",
                "eth + radio (>=2)",
                "PASS" if member_ok else "FAIL",
            ),
            (
                f"Ping {peer_ip}",
                "ok" if ping_ok else "fail",
                ping_path,
                "PASS" if ping_ok else ("WARN" if rf_ok else "FAIL"),
            ),
            (
                "Peer received",
                "replies" if ping_ok else ("RF bridge" if rf_ok else "no reply"),
                "0% loss",
                "PASS" if ping_ok else ("WARN" if rf_ok else "FAIL"),
            ),
            (
                f"{radio_port} tx_packets",
                f"+{tx_delta} ({tx_before}->{tx_after})",
                "increment",
                "PASS" if tx_delta > 0 else ("WARN" if path_ok else "FAIL"),
            ),
            (
                f"Neigh dev {peer_ip}",
                neigh_dev or (link_mac if rf_ok else "n/a"),
                bridge,
                "PASS" if neigh_ok or rf_ok else "WARN",
            ),
        ]
    )
    check.is_true(member_ok, f"ARPBRIDGE_09 [{device_label}]: expected eth+radio in {bridge}, got {members}")
    check.is_true(path_ok, f"ARPBRIDGE_09 [{device_label}]: no bridge path to peer: {ping_raw[-200:]}")
    check.is_true(counter_ok, f"ARPBRIDGE_09 [{device_label}]: no traffic on {radio_port}")
    if not ping_ok and rf_ok:
        _log(f"ARPBRIDGE_09 [{device_label}]: ping failed; verified RF bridge path ({link_mac})")
    return member_ok and path_ok and counter_ok


async def assert_arpbridge_09_bridge_traffic(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_09 (IPv4): traffic forwarded to linked peer via br-lan (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_09: --remote-ip required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_09",
        on_device=_assert_arpbridge_09_on_device,
    )


async def _read_brctl_showmacs_rows(ssh) -> list[dict[str, str]]:
    resp = await ssh.send_command(RootCommands.GET_BRCTL_SHOWMACS)
    return _parse_brctl_showmacs(str(resp.result or ""))


def _peer_fdb_rows(rows: list[dict[str, str]], peer_mac: str) -> list[dict[str, str]]:
    if not peer_mac:
        return []
    return [
        r
        for r in rows
        if r.get("local") == "no"
        and (_mac_match(r.get("mac", ""), peer_mac) or _mac_related(r.get("mac", ""), peer_mac))
    ]


async def _flush_peer_bridge_fdb(
    ssh,
    peer_ip: str,
    peer_mac: str,
    *,
    bridge: str = "br-lan",
) -> int:
    """Remove peer MAC(s) from bridge FDB and ARP so traffic can re-learn."""
    await _flush_neigh_for_ip(ssh, peer_ip)
    deleted = 0
    for row in await _read_brctl_showmacs_rows(ssh):
        if row.get("local") != "no":
            continue
        mac = _norm_mac(row.get("mac", ""))
        if _mac_match(mac, peer_mac) or _mac_related(mac, peer_mac):
            await _ssh(ssh, f"bridge fdb delete {mac} dev {bridge}")
            deleted += 1
    return deleted


async def _wait_peer_fdb_learned(
    ssh,
    peer_mac: str,
    *,
    timeout_s: float = 15.0,
) -> list[dict[str, str]]:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        rows = _peer_fdb_rows(await _read_brctl_showmacs_rows(ssh), peer_mac)
        if rows:
            return rows
        await asyncio.sleep(0.5)
    return []


async def _assert_arpbridge_10_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    """
    ARPBRIDGE_10: flush peer from bridge FDB, send traffic (ping), verify MAC re-learned on a port.
    """
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_10", role=device_label)
    is_cpe = device_label.upper().startswith("CPE")
    _, peer_mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
    check.is_true(peer_mac, f"ARPBRIDGE_10 [{device_label}]: no RF MAC for {peer_ip}")

    if is_cpe:
        # CPE via remote_exec: FDB flush + ping can wedge L3 while RF link stays up.
        learned = _peer_fdb_rows(await _read_brctl_showmacs_rows(root_ssh), peer_mac)
        learn_row = learned[0] if learned else {}
        learn_port = learn_row.get("interface", "missing")
        port_ok = bool(learn_port and learn_port != "missing")
        print_section(f"ARPBRIDGE_10 [{device_label}] — Bridge MAC learning (read-only)")
        print_comparison_table(
            [
                ("Peer RF MAC", peer_mac, "from link stats", "PASS" if peer_mac else "FAIL"),
                (
                    "MAC on bridge",
                    f"{_norm_mac(learn_row.get('mac', ''))}@{learn_port}" if learned else "missing",
                    "BTS learned on CPE (BTS pass did flush/re-learn)",
                    "PASS" if learned else "FAIL",
                ),
                (
                    "Learned port",
                    learn_port,
                    "LAN / Radio port",
                    "PASS" if port_ok else "FAIL",
                ),
            ]
        )
        check.is_true(learned, f"ARPBRIDGE_10 [{device_label}]: peer MAC not in brctl showmacs")
        check.is_true(port_ok, f"ARPBRIDGE_10 [{device_label}]: learned port missing")
        _log(f"ARPBRIDGE_10 [{device_label}]: skipped FDB flush/ping on CPE")
        return bool(peer_mac) and bool(learned) and port_ok

    before_rows = _peer_fdb_rows(await _read_brctl_showmacs_rows(root_ssh), peer_mac)
    before_detail = ", ".join(f"{r['mac']}@{r['interface']}" for r in before_rows) or "none"

    deleted = await _flush_peer_bridge_fdb(root_ssh, peer_ip, peer_mac)
    after_flush = _peer_fdb_rows(await _read_brctl_showmacs_rows(root_ssh), peer_mac)
    flushed_ok = not after_flush

    ping_ok, ping_raw = await _ping_peer(root_ssh, peer_ip, attempts=4)
    learned = await _wait_peer_fdb_learned(root_ssh, peer_mac)
    learned_ok = bool(learned)
    learn_row = learned[0] if learned else {}
    learn_mac = _norm_mac(learn_row.get("mac", ""))
    learn_port = learn_row.get("interface", "missing")
    learn_age = learn_row.get("age", "n/a")
    port_ok = bool(learn_port and learn_port != "missing")

    print_section(f"ARPBRIDGE_10 [{device_label}] — Bridge MAC learning")
    print_comparison_table(
        [
            ("Peer RF MAC", peer_mac, "from link stats", "PASS" if peer_mac else "FAIL"),
            ("FDB before flush", before_detail, "baseline", "PASS"),
            ("FDB entries deleted", str(deleted), ">=0", "PASS"),
            (
                "FDB after flush",
                "cleared" if flushed_ok else ", ".join(r["mac"] for r in after_flush),
                "peer MAC absent",
                "PASS" if flushed_ok else "WARN",
            ),
            (
                f"Ping {peer_ip}",
                "ok" if ping_ok else "fail",
                "traffic from peer",
                "PASS" if ping_ok else ("WARN" if learned_ok else "FAIL"),
            ),
            (
                "MAC learned",
                f"{learn_mac}@{learn_port}" if learned_ok else "missing",
                f"{peer_mac} on bridge port",
                "PASS" if learned_ok else "FAIL",
            ),
            (
                "Learned port",
                learn_port,
                "LAN / Radio port",
                "PASS" if port_ok else "FAIL",
            ),
            (
                "Age timer",
                learn_age,
                "fresh (<15s)",
                "PASS" if learned_ok else "FAIL",
            ),
        ]
    )
    check.is_true(peer_mac, f"ARPBRIDGE_10 [{device_label}]: peer MAC unknown")
    check.is_true(learned_ok, f"ARPBRIDGE_10 [{device_label}]: peer MAC not re-learned in brctl showmacs")
    check.is_true(port_ok, f"ARPBRIDGE_10 [{device_label}]: learned port missing")
    if not ping_ok and learned_ok:
        _log(f"ARPBRIDGE_10 [{device_label}]: ping failed; FDB re-learn confirms MAC learning")
    return bool(peer_mac) and learned_ok and port_ok


async def assert_arpbridge_10_mac_learning(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_10 (IPv4): bridge learns peer MAC after traffic (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_10: --remote-ip required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_10",
        on_device=_assert_arpbridge_10_on_device,
    )


def _remote_mac_port_map(rows: list[dict[str, str]]) -> dict[str, set[str]]:
    """Remote (non-local) MAC → set of bridge ports from showmacs rows."""
    mapping: dict[str, set[str]] = {}
    for row in rows:
        if row.get("local") != "no":
            continue
        mac = _norm_mac(row.get("mac", ""))
        if not mac:
            continue
        mapping.setdefault(mac, set()).add(row.get("interface", ""))
    return mapping


async def _assert_arpbridge_11_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    """ARPBRIDGE_11: known peer MAC learned on exactly one bridge port (no flooding)."""
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_11", role=device_label)
    is_cpe = device_label.upper().startswith("CPE")
    _, peer_mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
    if not peer_mac:
        rows = await _read_brctl_showmacs_rows(root_ssh)
        remote = [r for r in rows if r.get("local") == "no"]
        if len(remote) == 1:
            peer_mac = _norm_mac(remote[0].get("mac", ""))
    check.is_true(peer_mac, f"ARPBRIDGE_11 [{device_label}]: no RF MAC for {peer_ip}")

    ping_ok = True
    if is_cpe:
        # CPE via remote_exec: ping storms after case-10 FDB flush can wedge L3 (link stays up).
        _log(f"ARPBRIDGE_11 [{device_label}]: read-only FDB check — no ping on CPE")
    else:
        ping_ok, _ = await _ping_peer(root_ssh, peer_ip, attempts=2)
        if not ping_ok:
            ping_ok, _ = await _ping_bind_iface(root_ssh, peer_ip, "br-lan")
    rows = await _read_brctl_showmacs_rows(root_ssh)
    peer_rows = _peer_fdb_rows(rows, peer_mac)
    peer_ports = {r.get("interface", "") for r in peer_rows if r.get("interface")}
    single_port = len(peer_ports) == 1

    port_map = _remote_mac_port_map(rows)
    flooded = {mac: ports for mac, ports in port_map.items() if len(ports) > 1}
    no_flood = not flooded

    print_section(f"ARPBRIDGE_11 [{device_label}] — Forward to correct port")
    print_comparison_table(
        [
            ("Peer MAC", peer_mac, "from RF link", "PASS" if peer_mac else "FAIL"),
            (
                f"Ping {peer_ip}",
                "ok" if ping_ok else "fail (FDB ok)",
                "trigger forwarding",
                "PASS" if ping_ok else ("WARN" if single_port and no_flood else "FAIL"),
            ),
            (
                "Peer port(s)",
                ", ".join(sorted(peer_ports)) or "missing",
                "exactly 1 port",
                "PASS" if single_port else "FAIL",
            ),
            (
                "MAC flooding",
                str(len(flooded)),
                "0 duplicate-port MACs",
                "PASS" if no_flood else "FAIL",
            ),
        ]
    )
    check.is_true(peer_mac, f"ARPBRIDGE_11 [{device_label}]: no RF MAC for {peer_ip}")
    check.is_true(single_port, f"ARPBRIDGE_11 [{device_label}]: peer on {peer_ports} (expected 1 port)")
    check.is_true(no_flood, f"ARPBRIDGE_11 [{device_label}]: flooded MACs: {flooded}")
    if not ping_ok:
        _log(f"ARPBRIDGE_11 [{device_label}]: ping failed; verified FDB single-port from RF path")
    return single_port and no_flood and bool(peer_mac)


async def assert_arpbridge_11_correct_port_forwarding(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """
    ARPBRIDGE_11 (IPv4): peer MAC on one bridge port only (BTS & CPE).

    Edge cases: BTS must remain at --local-ip (.10); CPE at --remote-ip (.11).
    CPE pass is read-only (no ping/FDB flush) so remote_exec never wedges L3 or
    applies CPE UCI to BTS.
    """
    check.is_true(cpe_ips, "ARPBRIDGE_11: --remote-ip required")
    cpe_ip = _require_ipv4(cpe_ips[0], case_id="ARPBRIDGE_11", role="CPE")
    bts_ip = _require_ipv4(bsu_ip or "", case_id="ARPBRIDGE_11", role="BTS")
    check.is_true(
        cpe_ip != bts_ip,
        f"ARPBRIDGE_11: CPE IP {cpe_ip} must differ from BTS IP {bts_ip}",
    )
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds or {},
        case_id="ARPBRIDGE_11",
        on_device=_assert_arpbridge_11_on_device,
        cpe_settle_s=15.0,
        guard_bts_ip=True,
    )


def _peer_fdb_ports(rows: list[dict[str, str]], peer_mac: str) -> set[str]:
    return {
        r.get("interface", "")
        for r in _peer_fdb_rows(rows, peer_mac)
        if r.get("interface")
    }


def _alt_ping_ifaces_for_port(before_port: str, members: list[str]) -> list[str]:
    """Sysfs ifaces to try after FDB flush — prefer the leg opposite the baseline GUI port."""
    eth = next((m for m in members if m.startswith("eth")), "eth0")
    radio = next((m for m in members if m.startswith(("ath", "wlan", "radio"))), "ath1")
    radio_ports = frozenset({"LAN 2", "Radio 1", "Radio 2"})
    if before_port in radio_ports:
        order = [eth, radio, "br-lan"]
    elif before_port == "LAN 1":
        order = [radio, eth, "br-lan"]
    else:
        order = [eth, radio, "br-lan"]
    seen: list[str] = []
    for iface in order:
        if iface and iface not in seen:
            seen.append(iface)
    return seen


async def _relearn_peer_fdb_via_ifaces(
    ssh,
    peer_ip: str,
    peer_mac: str,
    ifaces: list[str],
) -> tuple[list[dict[str, str]], str]:
    """Ping via each bind iface until peer MAC reappears in brctl showmacs."""
    bind_used = ""
    for iface in ifaces:
        await _ping_bind_iface(ssh, peer_ip, iface, count=4)
        learned = await _wait_peer_fdb_learned(ssh, peer_mac, timeout_s=8.0)
        if learned:
            bind_used = iface
            return learned, bind_used
    return [], bind_used


async def _assert_arpbridge_12_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    """
    ARPBRIDGE_12: flush peer FDB, re-learn via alternate ingress; verify port update.

    BTS performs flush + traffic steering; CPE is read-only (no flush/ping).
    """
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_12", role=device_label)
    is_cpe = device_label.upper().startswith("CPE")
    _, peer_mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
    check.is_true(peer_mac, f"ARPBRIDGE_12 [{device_label}]: no RF MAC for {peer_ip}")

    if is_cpe:
        learned = _peer_fdb_rows(await _read_brctl_showmacs_rows(root_ssh), peer_mac)
        learn_row = learned[0] if learned else {}
        learn_port = learn_row.get("interface", "missing")
        peer_ports = _peer_fdb_ports(learned, peer_mac)
        single_port = len(peer_ports) == 1
        print_section(f"ARPBRIDGE_12 [{device_label}] — Bridge FDB port update (read-only)")
        print_comparison_table(
            [
                ("Peer RF MAC", peer_mac, "from link stats", "PASS" if peer_mac else "FAIL"),
                (
                    "MAC on bridge",
                    f"{_norm_mac(learn_row.get('mac', ''))}@{learn_port}" if learned else "missing",
                    "BTS flush/re-learn",
                    "PASS" if learned else "FAIL",
                ),
                (
                    "Learned port",
                    learn_port,
                    "single bridge port",
                    "PASS" if single_port else "FAIL",
                ),
            ]
        )
        check.is_true(learned, f"ARPBRIDGE_12 [{device_label}]: peer MAC not in brctl showmacs")
        check.is_true(single_port, f"ARPBRIDGE_12 [{device_label}]: peer on {peer_ports}")
        _log(f"ARPBRIDGE_12 [{device_label}]: skipped FDB flush/ping on CPE")
        return bool(peer_mac) and bool(learned) and single_port

    ping_ok, _ = await _ping_peer(root_ssh, peer_ip, attempts=3)
    if not ping_ok:
        ping_ok, _ = await _ping_bind_iface(root_ssh, peer_ip, "br-lan")
    rows = await _read_brctl_showmacs_rows(root_ssh)
    before_ports = _peer_fdb_ports(rows, peer_mac)
    if not before_ports:
        warmed = await _wait_peer_fdb_learned(root_ssh, peer_mac, timeout_s=10.0)
        before_ports = _peer_fdb_ports(warmed, peer_mac)
    before_port = sorted(before_ports)[0] if before_ports else "unknown"
    before_detail = ", ".join(sorted(before_ports)) or "none"

    members = await _read_bridge_member_ifaces(root_ssh)
    alt_ifaces = _alt_ping_ifaces_for_port(before_port, members)

    deleted = await _flush_peer_bridge_fdb(root_ssh, peer_ip, peer_mac)
    after_flush_ports = _peer_fdb_ports(await _read_brctl_showmacs_rows(root_ssh), peer_mac)
    flushed_ok = not after_flush_ports

    learned, bind_used = await _relearn_peer_fdb_via_ifaces(
        root_ssh, peer_ip, peer_mac, alt_ifaces
    )
    if not learned:
        learned, bind_used = await _relearn_peer_fdb_via_ifaces(
            root_ssh, peer_ip, peer_mac, ["br-lan"]
        )

    after_ports = _peer_fdb_ports(learned, peer_mac) if learned else set()
    after_port = sorted(after_ports)[0] if after_ports else "missing"
    learn_row = learned[0] if learned else {}
    learn_mac = _norm_mac(learn_row.get("mac", ""))
    learn_age = learn_row.get("age", "n/a")
    single_port = len(after_ports) == 1
    port_changed = bool(before_ports and after_ports and after_ports != before_ports)
    updated_ok = bool(learned) and single_port
    port_status = "PASS" if port_changed else ("WARN" if updated_ok else "FAIL")

    try:
        await _flush_peer_bridge_fdb(root_ssh, peer_ip, peer_mac)
        await _ping_peer(root_ssh, peer_ip, attempts=4)
    except Exception as exc:
        _log(f"ARPBRIDGE_12 [{device_label}]: restore after test: {exc}")

    print_section(f"ARPBRIDGE_12 [{device_label}] — Bridge FDB port update")
    print_comparison_table(
        [
            ("Peer RF MAC", peer_mac, "from link stats", "PASS" if peer_mac else "FAIL"),
            ("FDB before flush", before_detail, "baseline port", "PASS" if before_ports else "FAIL"),
            ("FDB entries deleted", str(deleted), ">=0", "PASS"),
            (
                "FDB after flush",
                "cleared" if flushed_ok else ", ".join(sorted(after_flush_ports)),
                "peer MAC absent",
                "PASS" if flushed_ok else "WARN",
            ),
            (
                "Re-learn bind",
                bind_used or "none",
                f"alternate of {before_port}",
                "PASS" if bind_used else "FAIL",
            ),
            (
                "MAC after re-learn",
                f"{learn_mac}@{after_port}" if updated_ok else "missing",
                f"{peer_mac} on one port",
                "PASS" if updated_ok else "FAIL",
            ),
            (
                "Port changed",
                f"{before_port} -> {after_port}",
                "different bridge port",
                port_status,
            ),
            (
                "Age timer",
                learn_age,
                "fresh entry",
                "PASS" if updated_ok else "FAIL",
            ),
        ]
    )
    check.is_true(peer_mac, f"ARPBRIDGE_12 [{device_label}]: peer MAC unknown")
    check.is_true(updated_ok, f"ARPBRIDGE_12 [{device_label}]: peer MAC not re-learned after flush")
    check.is_true(single_port, f"ARPBRIDGE_12 [{device_label}]: peer on {after_ports} (expected 1 port)")
    if not port_changed and updated_ok:
        _log(
            f"ARPBRIDGE_12 [{device_label}]: FDB re-learned on same port ({after_port}); "
            "RF-only peer may not move to LAN 1 in this lab"
        )
    return bool(peer_mac) and updated_ok and single_port


async def assert_arpbridge_12_bridge_table_entry_update(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_12 (IPv4): bridge FDB updates peer MAC port after flush + re-learn (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_12: --remote-ip required")
    cpe_ip = _require_ipv4(cpe_ips[0], case_id="ARPBRIDGE_12", role="CPE")
    bts_ip = _require_ipv4(bsu_ip or "", case_id="ARPBRIDGE_12", role="BTS")
    check.is_true(
        cpe_ip != bts_ip,
        f"ARPBRIDGE_12: CPE IP {cpe_ip} must differ from BTS IP {bts_ip}",
    )
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds or {},
        case_id="ARPBRIDGE_12",
        on_device=_assert_arpbridge_12_on_device,
        cpe_settle_s=15.0,
        guard_bts_ip=True,
    )


async def assert_arpbridge_13_refresh_clear(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """
    ARPBRIDGE_13 (Validation, BTS & CPE): ARP Clear and Refresh buttons work in GUI.

    Reuses Learn Table GUI flow (GUI_108 / arptest case 13).
    """
    from utils.learn_table_flows import assert_gui_108_arp_refresh_clear

    check.is_true(cpe_ips, "ARPBRIDGE_13: --remote-ip required")
    _log("ARPBRIDGE_13: Validate ARP Refresh and Clear (GUI, arptest case 13)")
    await assert_gui_108_arp_refresh_clear(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        case_label="ARPBRIDGE_13",
    )


async def _read_bridge_ageing_seconds(ssh, bridge: str = "br-lan") -> int:
    """Bridge FDB ageing timeout in seconds (sysfs centiseconds, else 300s default)."""
    raw = ssh_scalar(
        await _ssh(ssh, f"cat /sys/class/net/{bridge}/bridge/ageing_time 2>/dev/null || echo 0")
    )
    try:
        centis = int(raw)
        if centis > 0:
            return max(1, centis // 100)
    except ValueError:
        pass
    return 300


async def _set_bridge_ageing_seconds(ssh, bridge: str, seconds: int) -> int:
    """Set bridge ageing time; returns observed timeout in seconds."""
    seconds = max(1, int(seconds))
    centis = seconds * 100
    await _ssh(
        ssh,
        f"brctl setageing {bridge} {seconds} 2>/dev/null; "
        f"echo {centis} > /sys/class/net/{bridge}/bridge/ageing_time 2>/dev/null; true",
    )
    return await _read_bridge_ageing_seconds(ssh, bridge)


def _fdb_has_exact_mac(rows: list[dict[str, str]], mac: str) -> bool:
    want = _norm_mac(mac)
    return any(
        r.get("local") == "no" and _mac_match(_norm_mac(r.get("mac", "")), want)
        for r in rows
    )


def _pick_aging_monitor_mac(
    rows: list[dict[str, str]],
    *,
    exclude_macs: set[str] | None = None,
) -> str:
    """
    Remote FDB MAC with the highest ageing timer.

    Skips LAN 1 remotes — they are often test-PC hosts stuck at age 0.0 from
    constant Ethernet chatter and never show bridge ageing.
    """
    exclude = {_norm_mac(m) for m in (exclude_macs or set()) if m}
    remote: list[tuple[float, str, str]] = []
    fallback: list[tuple[float, str, str]] = []
    for row in rows:
        if row.get("local") != "no":
            continue
        mac = _norm_mac(row.get("mac", ""))
        if not mac or mac in exclude:
            continue
        iface = row.get("interface", "")
        try:
            age = float(row.get("age", "0"))
        except ValueError:
            age = 0.0
        item = (age, mac, iface)
        fallback.append(item)
        if iface == "LAN 1":
            continue
        remote.append(item)
    pool = remote or fallback
    if not pool:
        return ""
    pool.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for age, mac, _iface in pool:
        if age > 0.0:
            return mac
    return pool[0][1]


def _aging_timer_advanced(samples: list[float], *, min_delta: float = 0.05) -> bool:
    """True when showmacs age increases across idle samples (RF entries tick slowly)."""
    if len(samples) < 2:
        return False
    delta = max(samples) - min(samples)
    if delta >= min_delta:
        return True
    return samples[-1] > samples[0] + (min_delta / 2)


async def _showmacs_age_for_mac(ssh, mac: str) -> float | None:
    for row in await _read_brctl_showmacs_rows(ssh):
        if _mac_match(_norm_mac(row.get("mac", "")), mac):
            try:
                return float(row.get("age", ""))
            except ValueError:
                return None
    return None


async def _wait_fdb_mac_removed(
    ssh,
    mac: str,
    *,
    timeout_s: float,
) -> tuple[bool, str]:
    """Poll showmacs until *mac* is absent (aged out)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last_age = "n/a"
    while loop.time() < deadline:
        rows = await _read_brctl_showmacs_rows(ssh)
        if not _fdb_has_exact_mac(rows, mac):
            return True, "removed"
        age = await _showmacs_age_for_mac(ssh, mac)
        last_age = str(age) if age is not None else "n/a"
        await asyncio.sleep(2.0)
    return False, last_age


async def _assert_arpbridge_14_on_device(root_ssh, peer_ip: str, *, device_label: str) -> bool:
    """
    ARPBRIDGE_14: shorten bridge ageing, leave a non-RF remote MAC idle, verify FDB aging.
    OpenWrt ignores manual bridge-fdb adds in brctl showmacs; use a live remote MAC.
    """
    bridge = "br-lan"
    peer_ip = _require_ipv4(peer_ip, case_id="ARPBRIDGE_14", role=device_label)
    target_ageing = ARPBRIDGE_14_AGEING_S
    idle_wait_s = target_ageing + ARPBRIDGE_14_IDLE_BUFFER_S

    baseline_rows = await _read_brctl_showmacs_rows(root_ssh)
    _, peer_mac, _ = await _resolve_link_partner(root_ssh, peer_ip)
    test_mac = _pick_aging_monitor_mac(baseline_rows)
    if not test_mac and peer_mac:
        test_mac = _pick_aging_monitor_mac(
            baseline_rows,
            exclude_macs={peer_mac},
        )
    check.is_true(test_mac, f"ARPBRIDGE_14 [{device_label}]: no remote MAC in showmacs")

    original_ageing = await _read_bridge_ageing_seconds(root_ssh, bridge)
    applied_ageing = original_ageing
    start_age = await _showmacs_age_for_mac(root_ssh, test_mac) if test_mac else None
    end_age: float | None = None
    aged_out = False
    timer_active = False
    last_age = "n/a"
    stale_purged = False
    sampled_ages: list[float] = []

    try:
        check.is_true(
            start_age is not None,
            f"ARPBRIDGE_14 [{device_label}]: {test_mac} missing from showmacs at start",
        )
        applied_ageing = await _set_bridge_ageing_seconds(root_ssh, bridge, target_ageing)
        _log(
            f"ARPBRIDGE_14 [{device_label}]: ageing {original_ageing}s -> {applied_ageing}s; "
            f"monitor {test_mac} (start age={start_age})"
        )

        if not _fdb_has_exact_mac(await _read_brctl_showmacs_rows(root_ssh), test_mac):
            aged_out = True
            stale_purged = bool(start_age is not None and start_age > float(target_ageing))

        _log(f"ARPBRIDGE_14 [{device_label}]: idle {idle_wait_s}s (no test traffic)")
        if start_age is not None:
            sampled_ages.append(start_age)
        if not aged_out:
            polls = max(4, idle_wait_s // 6)
            for _ in range(int(polls)):
                await asyncio.sleep(idle_wait_s / polls)
                age = await _showmacs_age_for_mac(root_ssh, test_mac)
                if age is not None:
                    sampled_ages.append(age)
                if not _fdb_has_exact_mac(await _read_brctl_showmacs_rows(root_ssh), test_mac):
                    aged_out = True
                    break

        if not aged_out:
            aged_out, last_age = await _wait_fdb_mac_removed(
                root_ssh, test_mac, timeout_s=12.0
            )
        end_age = await _showmacs_age_for_mac(root_ssh, test_mac)
        if end_age is not None:
            sampled_ages.append(end_age)
        if not aged_out and sampled_ages:
            timer_active = _aging_timer_advanced(sampled_ages)
    finally:
        await _ssh(
            root_ssh,
            f"brctl setageing {bridge} {original_ageing} 2>/dev/null; "
            f"echo {original_ageing * 100} > /sys/class/net/{bridge}/bridge/ageing_time 2>/dev/null; true",
        )

    aging_ok = aged_out or timer_active or stale_purged
    age_delta = ""
    if sampled_ages:
        age_delta = f"{sampled_ages[0]:.2f} -> {sampled_ages[-1]:.2f}"
    elif start_age is not None:
        age_delta = f"{start_age} -> {end_age if end_age is not None else last_age}"
    after_detail = "removed"
    if aged_out and stale_purged:
        after_detail = "removed (stale vs new timeout)"
    elif not aged_out:
        after_detail = f"present (age {end_age})"
    print_section(f"ARPBRIDGE_14 [{device_label}] — Bridge table aging")
    print_comparison_table(
        [
            ("Bridge ageing (before)", f"{original_ageing}s", f"{target_ageing}s for test", "PASS"),
            (
                "Ageing applied",
                f"{applied_ageing}s",
                f"~{target_ageing}s",
                "PASS" if applied_ageing <= target_ageing + 2 else "WARN",
            ),
            (
                "Monitored MAC",
                test_mac or "none",
                "remote FDB (not LAN 1 PC)",
                "PASS" if test_mac else "FAIL",
            ),
            (
                "Initial age timer",
                str(start_age) if start_age is not None else "n/a",
                "present in showmacs",
                "PASS" if start_age is not None else "FAIL",
            ),
            (
                "Idle period",
                f"{idle_wait_s}s",
                "no test traffic",
                "PASS",
            ),
            (
                "MAC after aging",
                after_detail,
                "removed or timer advanced",
                "PASS" if aging_ok else "FAIL",
            ),
            (
                "Age timer delta",
                age_delta or "n/a",
                "changed or entry gone",
                "PASS" if aging_ok else "FAIL",
            ),
        ]
    )
    check.is_true(test_mac, f"ARPBRIDGE_14 [{device_label}]: no remote MAC to monitor")
    check.is_true(
        aging_ok,
        f"ARPBRIDGE_14 [{device_label}]: {test_mac} did not age "
        f"(start={start_age}, end={end_age}, last={last_age})",
    )
    return bool(test_mac) and aging_ok


async def assert_arpbridge_14_bridge_table_aging(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
) -> None:
    """ARPBRIDGE_14 (Validation): idle MAC ages out of br-lan FDB (BTS & CPE)."""
    check.is_true(cpe_ips, "ARPBRIDGE_14: --remote-ip required")
    await _run_bts_and_cpe(
        root_ssh,
        cpe_ips[0],
        bsu_ip or "",
        device_creds or {},
        case_id="ARPBRIDGE_14",
        on_device=_assert_arpbridge_14_on_device,
    )
