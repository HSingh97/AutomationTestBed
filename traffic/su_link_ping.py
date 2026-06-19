"""Wait for SU/CPE reachability after BTS bandwidth apply (QinQ PC ping or BTS SSH ping)."""

from __future__ import annotations

import ipaddress
import re
import shlex
import subprocess
import time
from typing import Any

from traffic.dut_radio_config import run_ssh_command
from traffic.kwn_sua_statistics import fetch_kwn_sua_statistics, resolve_sua_display_ip
from utils.lab_pc_net import _build_pc_link_commands
from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.vlan_uci import lab_pc_vlan_plan, qinq_tags_from_profile


def _ipv6_network(anchor: str, prefix_len: int) -> ipaddress.IPv6Network:
    host = normalize_ip(str(anchor).split("/")[0])
    return ipaddress.IPv6Network(f"{host}/{prefix_len}", strict=False)


def _in_network(addr: str, net: ipaddress.IPv6Network) -> bool:
    host = normalize_ip(str(addr).split("/")[0])
    return ipaddress.IPv6Address(host) in net


def _parse_global_ipv6_from_ip_addr(text: str) -> str | None:
    for line in text.splitlines():
        if "inet6" not in line or "scope global" not in line:
            continue
        match = re.search(r"inet6\s+([0-9a-fA-F:]+)/\d+", line)
        if match:
            return normalize_ip(match.group(1))
    return None


def _read_local_iface_global_ipv6(iface: str) -> str | None:
    iface_q = shlex.quote(iface)
    for cmd in (
        f"sudo -n ip -6 addr show dev {iface_q} scope global",
        f"ip -6 addr show dev {iface_q} scope global",
    ):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                host = _parse_global_ipv6_from_ip_addr(result.stdout)
                if host:
                    return host
        except (subprocess.TimeoutExpired, OSError):
            continue
    return None


def _read_bts_mgmt_ipv6(bts_ip: str, user: str, password: str) -> str | None:
    cmd = (
        "ip -6 addr show dev br-lan scope global 2>/dev/null | "
        "awk '/inet6/{gsub(/\\/.*/,\"\",$2); print $2; exit}'"
    )
    try:
        raw = run_ssh_command(bts_ip, user, password, cmd, timeout_s=15).strip()
        if raw and is_ipv6_literal(raw):
            return normalize_ip(raw)
    except RuntimeError:
        pass
    return None


def discover_su_hosts_from_bts(
    bts_ip: str,
    user: str,
    password: str,
    *,
    max_sua: int = 16,
) -> list[str]:
    """Live SU mgmt IPv6 from BTS ``/sys/class/kwn/sua{N}/statistics/ipv6``."""
    clients = fetch_kwn_sua_statistics(
        bts_ip,
        ssh_user=user,
        ssh_password=password,
        max_sua=max_sua,
    )
    hosts: list[str] = []
    for client in clients:
        ip = str(client.get("ip") or "").strip()
        if not ip or ip == "-":
            ip = resolve_sua_display_ip(
                ipv4=str(client.get("ip") or ""),
                ipv6=str(client.get("ipv6") or ""),
            )
        if ip and is_ipv6_literal(ip):
            hosts.append(normalize_ip(ip))
    return hosts


def _resolve_lab_pc_ipv6_cidr(
    profile_tb: dict[str, Any],
    dut: dict[str, Any] | None,
    *,
    inner_iface: str,
    bts_ip: str,
    bts_user: str,
    bts_password: str,
) -> str | None:
    """
    Lab PC source address for ping -6 -I enp3s0.S.C.

    Must be a global IPv6 in the same /120 as BTS mgmt (e.g. BTS ::1111, SUs ::11c9…).
    """
    mgmt = profile_tb.get("mgmt_vlan") or {}
    prefix_len = int(mgmt.get("prefix_len", 120))
    dut_cfg = dut or {}

    bts_anchor = (
        _read_bts_mgmt_ipv6(bts_ip, bts_user, bts_password)
        or str(dut_cfg.get("local_ipv6") or mgmt.get("ipv6_bts") or "").strip()
    )
    bts_anchor = normalize_ip(bts_anchor.split("/")[0]) if bts_anchor else ""
    bts_net = _ipv6_network(bts_anchor, prefix_len) if bts_anchor else None

    existing = _read_local_iface_global_ipv6(inner_iface)
    if existing and (bts_net is None or _in_network(existing, bts_net)):
        return f"{existing}/{prefix_len}"

    profile_pc = str(dut_cfg.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or "").strip()
    if profile_pc:
        pc_host = normalize_ip(profile_pc.split("/")[0])
        if bts_net is not None and not _in_network(pc_host, bts_net):
            print(
                f"[LINK] Profile lab PC IPv6 {pc_host} is outside BTS /{prefix_len} "
                f"({bts_anchor}); not assigning stale address on {inner_iface}"
            )
        else:
            return f"{pc_host}/{prefix_len}"

    if bts_anchor:
        print(
            f"[LINK] No lab PC IPv6 in BTS /{prefix_len} ({bts_anchor}); "
            f"set dut.bts_pc_ipv6 or bootstrap mgmt on {inner_iface}"
        )
    return None


def build_qinq_pc_setup_commands(
    profile_tb: dict[str, Any],
    *,
    cidr: str | None = None,
) -> tuple[str, str] | None:
    """QinQ subif + global mgmt IPv6 on the lab PC (same /120 as BTS and SUs)."""
    primary = profile_tb.get("primary_pc") or {}
    iface = str(primary.get("mgmt_interface") or "enp3s0").strip()
    if not cidr:
        return None
    tagging = lab_pc_vlan_plan(profile_tb, side="bts")
    if str(tagging.get("mode", "")).lower() != "qinq":
        svlan, cvlan = qinq_tags_from_profile(profile_tb)
        if svlan is None or cvlan is None:
            return None
        tagging = {"mode": "qinq", "svlan": svlan, "cvlan": cvlan, "untagged": False}
    joined, inner = _build_pc_link_commands(iface, tagging=tagging, cidr=cidr)
    return joined, inner


def _ping_cmd(host: str, *, interface: str | None = None, count: int = 2, wait_s: int = 3) -> str:
    target = normalize_ip(host)
    iface_part = f"-I {shlex.quote(interface)} " if interface else ""
    if is_ipv6_literal(target):
        return f"ping -6 {iface_part}-c {count} -W {wait_s} {shlex.quote(target)}"
    return f"ping {iface_part}-c {count} -W {wait_s} {shlex.quote(target)}"


def _run_local_cmd(cmd: str) -> bool:
    for attempt_cmd in (f"sudo -n sh -c {shlex.quote(cmd)}", cmd):
        try:
            result = subprocess.run(
                attempt_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            continue
    return False


def _local_ping_ok(cmd: str) -> bool:
    return _run_local_cmd(cmd)


def _bts_ping_ok(bts_ip: str, user: str, password: str, host: str, *, count: int = 2) -> bool:
    try:
        run_ssh_command(
            bts_ip,
            user,
            password,
            _ping_cmd(host, count=count),
            timeout_s=20,
        )
        return True
    except RuntimeError:
        return False


def _ensure_local_qinq_iface(
    profile_tb: dict[str, Any],
    dut: dict[str, Any] | None,
    *,
    bts_ip: str,
    bts_user: str,
    bts_password: str,
) -> str | None:
    primary = profile_tb.get("primary_pc") or {}
    if not primary.get("local", True):
        return None

    tagging = lab_pc_vlan_plan(profile_tb, side="bts")
    if str(tagging.get("mode", "")).lower() != "qinq":
        svlan, cvlan = qinq_tags_from_profile(profile_tb)
        if svlan is None or cvlan is None:
            return None
        inner = f"{primary.get('mgmt_interface', 'enp3s0')}.{svlan}.{cvlan}"
    else:
        svlan = int(tagging.get("svlan", 0))
        cvlan = int(tagging.get("cvlan", 0))
        inner = f"{primary.get('mgmt_interface', 'enp3s0')}.{svlan}.{cvlan}"

    cidr = _resolve_lab_pc_ipv6_cidr(
        profile_tb,
        dut,
        inner_iface=inner,
        bts_ip=bts_ip,
        bts_user=bts_user,
        bts_password=bts_password,
    )
    if cidr is None:
        return None

    setup = build_qinq_pc_setup_commands(profile_tb, cidr=cidr)
    if setup is None:
        return None
    joined, inner_iface = setup
    if not _run_local_cmd(joined):
        return None
    print(f"[LINK] QinQ interface ready for ping: {inner_iface} -> {cidr}")
    return inner_iface


def wait_for_su_links(
    *,
    cpe_hosts: list[str],
    profile_tb: dict[str, Any] | None,
    bts_ip: str,
    bts_user: str,
    bts_password: str,
    dut: dict[str, Any] | None = None,
    timeout_s: float = 120.0,
    poll_s: float = 5.0,
    ping_count: int = 2,
    min_responding: int | None = None,
    phase: str = "after bandwidth apply",
    strict: bool = False,
    link_debug_dir: str | None = None,
    radio_idx: int = 1,
) -> dict[str, Any]:
    """
    After bandwidth apply, poll until SU mgmt addresses respond to ping.

    Targets are discovered live from BTS KWN SUA sysfs when possible (same /120 as BTS).
    Prefers lab PC QinQ interface with a global IPv6 in that /120; falls back to BTS SSH ping.
    """
    tb = profile_tb or {}
    discovered = discover_su_hosts_from_bts(bts_ip, bts_user, bts_password)
    if discovered:
        targets = discovered
        print(f"[LINK] SU targets from BTS sysfs: {', '.join(targets)}")
    else:
        targets = [normalize_ip(h) for h in cpe_hosts if str(h).strip()]
        if targets:
            print(f"[LINK] SU targets from profile (BTS sysfs empty): {', '.join(targets)}")

    if not targets:
        if strict and min_responding:
            print(f"[LINK] No SU targets discovered {phase} — need {min_responding}")
            return {"ok": False, "responding": [], "method": "none", "reason": "no cpe hosts"}
        return {"ok": True, "responding": [], "method": "none", "reason": "no cpe hosts"}

    required = min_responding if min_responding is not None else len(targets)
    qinq_iface = _ensure_local_qinq_iface(
        tb,
        dut,
        bts_ip=bts_ip,
        bts_user=bts_user,
        bts_password=bts_password,
    )
    deadline = time.time() + timeout_s
    last_method = "qinq-pc" if qinq_iface else "bts-ssh"
    attempt = 0

    print(
        f"[LINK] Waiting for {required}/{max(len(targets), required)} SU link(s) {phase} "
        f"(timeout {timeout_s:.0f}s, via {last_method})"
    )

    def _maybe_debug_snapshot(attempt_no: int, suffix: str) -> None:
        if not link_debug_dir:
            return
        from traffic.link_debug_snapshot import write_link_debug_snapshot

        write_link_debug_snapshot(
            link_debug_dir,
            label=f"attempt{attempt_no}_{suffix}",
            bts_ip=bts_ip,
            bts_user=bts_user,
            bts_password=bts_password,
            profile_tb=tb,
            dut_cfg=dut,
            cpe_hosts=cpe_hosts,
            radio_idx=radio_idx,
            attempt=attempt_no,
            phase=phase,
            qinq_iface=qinq_iface,
        )

    _maybe_debug_snapshot(0, "start")

    while time.time() < deadline:
        attempt += 1
        live = discover_su_hosts_from_bts(bts_ip, bts_user, bts_password)
        if live:
            targets = live
        responding: list[str] = []
        for host in targets:
            ok = False
            if qinq_iface:
                ok = _local_ping_ok(_ping_cmd(host, interface=qinq_iface, count=ping_count))
                last_method = "qinq-pc"
            if not ok:
                ok = _bts_ping_ok(bts_ip, bts_user, bts_password, host, count=ping_count)
                last_method = "bts-ssh" if not qinq_iface else "qinq-pc+bts-ssh"
            if ok:
                responding.append(host)

        if len(responding) >= required:
            print(
                f"[LINK] SU ping OK: {len(responding)}/{required} responding "
                f"(method={last_method}, attempt={attempt})"
            )
            _maybe_debug_snapshot(attempt, "success")
            return {
                "ok": True,
                "responding": responding,
                "method": last_method,
                "qinq_interface": qinq_iface,
                "attempts": attempt,
                "targets": targets,
            }

        if attempt == 1 or attempt % 4 == 0:
            missing = [h for h in targets if h not in responding]
            print(
                f"[LINK] SU ping {len(responding)}/{len(targets)} up "
                f"(attempt {attempt}, waiting for {', '.join(missing[:4])})"
            )
            _maybe_debug_snapshot(attempt, "poll")
        time.sleep(poll_s)

    missing = [h for h in targets if h not in responding]
    not_in_targets = max(0, required - len(targets))
    missing_detail = missing if missing else (
        [f"(sysfs/profile only lists {len(targets)}; need {required})"] if not_in_targets else []
    )
    suffix = "" if strict else " — continuing"
    print(
        f"[WARN] SU ping timeout: only {len(responding)}/{required} responded "
        f"(missing: {', '.join(missing_detail)}){suffix}"
    )
    _maybe_debug_snapshot(attempt, "timeout")
    return {
        "ok": False,
        "responding": responding,
        "missing": missing,
        "method": last_method,
        "qinq_interface": qinq_iface,
        "attempts": attempt,
        "targets": targets,
    }
