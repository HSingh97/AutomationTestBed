"""Wait for SU/CPE reachability after BTS bandwidth apply (QinQ PC ping or BTS SSH ping)."""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import Any

from traffic.dut_radio_config import run_ssh_command
from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.vlan_uci import qinq_tags_from_profile


def build_qinq_link_up_commands(iface: str, svlan: int, cvlan: int) -> str:
    """Bring up stacked VLAN subinterfaces for QinQ ping (-I enp3s0.S.C)."""
    iface_q = shlex.quote(iface)
    outer = f"{iface}.{svlan}"
    inner = f"{outer}.{cvlan}"
    outer_q = shlex.quote(outer)
    inner_q = shlex.quote(inner)
    return " && ".join(
        [
            f"ip link add link {iface_q} name {outer_q} type vlan id {svlan} 2>/dev/null || true",
            f"ip link add link {outer_q} name {inner_q} type vlan id {cvlan} 2>/dev/null || true",
            f"ip link set {outer_q} up",
            f"ip link set {inner_q} up",
            f"ip link set {iface_q} up",
        ]
    )


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


def _ensure_local_qinq_iface(profile_tb: dict[str, Any]) -> str | None:
    primary = profile_tb.get("primary_pc") or {}
    if not primary.get("local", True):
        return None
    iface = str(primary.get("mgmt_interface") or "enp3s0").strip()
    svlan, cvlan = qinq_tags_from_profile(profile_tb)
    if svlan is None or cvlan is None:
        return None
    inner = f"{iface}.{svlan}.{cvlan}"
    cmd = build_qinq_link_up_commands(iface, svlan, cvlan)
    if not _run_local_cmd(cmd):
        return None
    print(f"[LINK] QinQ interface ready for ping: {inner} (S={svlan} C={cvlan})")
    return inner


def wait_for_su_links(
    *,
    cpe_hosts: list[str],
    profile_tb: dict[str, Any] | None,
    bts_ip: str,
    bts_user: str,
    bts_password: str,
    timeout_s: float = 120.0,
    poll_s: float = 5.0,
    ping_count: int = 2,
    min_responding: int | None = None,
) -> dict[str, Any]:
    """
    After bandwidth apply, poll until SU mgmt addresses respond to ping.

  Prefers lab PC QinQ interface (double-tagged toward BTS); falls back to ping from BTS SSH.
    """
    targets = [normalize_ip(h) for h in cpe_hosts if str(h).strip()]
    if not targets:
        return {"ok": True, "responding": [], "method": "none", "reason": "no cpe hosts"}

    required = min_responding if min_responding is not None else len(targets)
    tb = profile_tb or {}
    qinq_iface = _ensure_local_qinq_iface(tb)
    deadline = time.time() + timeout_s
    last_method = "qinq-pc" if qinq_iface else "bts-ssh"
    attempt = 0

    print(
        f"[LINK] Waiting for {required}/{len(targets)} SU ping(s) after bandwidth apply "
        f"(timeout {timeout_s:.0f}s, via {last_method})"
    )

    while time.time() < deadline:
        attempt += 1
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
                f"[LINK] SU ping OK: {len(responding)}/{len(targets)} responding "
                f"(method={last_method}, attempt={attempt})"
            )
            return {
                "ok": True,
                "responding": responding,
                "method": last_method,
                "qinq_interface": qinq_iface,
                "attempts": attempt,
            }

        if attempt == 1 or attempt % 4 == 0:
            missing = [h for h in targets if h not in responding]
            print(
                f"[LINK] SU ping {len(responding)}/{len(targets)} up "
                f"(attempt {attempt}, waiting for {', '.join(missing[:4])})"
            )
        time.sleep(poll_s)

    missing = [h for h in targets if h not in responding]
    print(
        f"[WARN] SU ping timeout: only {len(responding)}/{required} responded "
        f"(missing: {', '.join(missing)}) — continuing"
    )
    return {
        "ok": False,
        "responding": responding,
        "missing": missing,
        "method": last_method,
        "qinq_interface": qinq_iface,
        "attempts": attempt,
    }
