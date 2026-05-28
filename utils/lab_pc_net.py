"""Configure management VLAN on lab PCs (primary automation host and secondary CPE-side PC)."""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver
from utils.net_utils import normalize_ip


def _parse_ssh_target(target: str) -> tuple[str, str]:
    """Return (host, user). target may be user@host or host."""
    target = (target or "").strip()
    if "@" in target:
        user, host = target.split("@", 1)
        return host.strip(), user.strip() or "root"
    return target, "root"


async def _open_pc_ssh(host: str, user: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


def _run_local(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _build_pc_link_commands(
    iface: str,
    *,
    tagging: dict[str, Any],
    cidr: str,
) -> tuple[str, str]:
    """
    Build Linux `ip` commands for lab PC port toward BTS (QinQ) or CPE (untagged).
    Returns (joined shell commands, human-readable if name).
    """
    mode = str(tagging.get("mode", "untagged")).lower()
    if tagging.get("untagged") or mode == "untagged":
        vlan_if = iface
        cmds = [
            f"ip -6 addr flush dev {shlex.quote(iface)} 2>/dev/null || true",
            f"ip -6 addr add {shlex.quote(cidr)} dev {shlex.quote(iface)} 2>/dev/null || true",
            f"ip link set {shlex.quote(iface)} up",
        ]
        return " && ".join(cmds), vlan_if

    # QinQ toward BTS: mgmt IPv6 on inner tag only.
    # Keep native IPv4 (10.0.0.xx) on parent — fallback 10.0.0.1 stays reachable on enp3s0.
    svlan = int(tagging.get("svlan", 100))
    cvlan = int(tagging.get("cvlan", 101))
    outer = f"{iface}.{svlan}"
    inner = f"{outer}.{cvlan}"
    cmds = [
        f"ip -6 addr flush dev {shlex.quote(iface)} 2>/dev/null || true",
        f"ip link add link {shlex.quote(iface)} name {shlex.quote(outer)} type vlan id {svlan} 2>/dev/null || true",
        f"ip link add link {shlex.quote(outer)} name {shlex.quote(inner)} type vlan id {cvlan} 2>/dev/null || true",
        f"ip -6 addr flush dev {shlex.quote(outer)} 2>/dev/null || true",
        f"ip -6 addr flush dev {shlex.quote(inner)} 2>/dev/null || true",
        f"ip -6 addr add {shlex.quote(cidr)} dev {shlex.quote(inner)} 2>/dev/null || true",
        f"ip link set {shlex.quote(outer)} up",
        f"ip link set {shlex.quote(inner)} up",
        f"ip link set {shlex.quote(iface)} up",
    ]
    return " && ".join(cmds), inner


def build_restore_untagged_commands(iface: str) -> str:
    """Remove QinQ subifs; keep native enp3s0 up (IPv4 10.0.0.xx for fallback stays)."""
    iface_q = shlex.quote(iface)
    return " && ".join(
        [
            f"ip link set {iface_q}.200.201 down 2>/dev/null || true",
            f"ip link del {iface_q}.200.201 2>/dev/null || true",
            f"ip link set {iface_q}.200 down 2>/dev/null || true",
            f"ip link del {iface_q}.200 2>/dev/null || true",
            f"ip link set {iface_q}.100.101 down 2>/dev/null || true",
            f"ip link del {iface_q}.100.101 2>/dev/null || true",
            f"ip link set {iface_q}.100 down 2>/dev/null || true",
            f"ip link del {iface_q}.100 2>/dev/null || true",
            f"ip -6 addr flush dev {iface_q} 2>/dev/null || true",
            f"ip link set {iface_q} up",
        ]
    )


def build_fallback_ipv4_commands(iface: str, ipv4_cidr: str) -> str:
    """Ensure enp3s0 has a 10.0.0.xx address for BTS fallback access (add only if missing)."""
    iface_q = shlex.quote(iface)
    cidr_q = shlex.quote(ipv4_cidr)
    return " && ".join(
        [
            f"ip link set {iface_q} up",
            (
                f"ip -4 addr show dev {iface_q} | grep -qE 'inet 10\\.0\\.' "
                f"|| ip -4 addr add {cidr_q} dev {iface_q} 2>/dev/null || true"
            ),
        ]
    )


async def _run_pc_network_command(pc_cfg: dict[str, Any], password: str, joined: str, *, label: str) -> bool:
    ssh_target = str(pc_cfg.get("ssh", "")).strip()
    if not ssh_target and pc_cfg.get("local", True):
        rc, out = await asyncio.to_thread(_run_local, f"sudo -n sh -c {shlex.quote(joined)}")
        if rc != 0:
            rc, out = await asyncio.to_thread(_run_local, joined)
        if rc != 0:
            print(f"[lab-pc] {label} failed: {out[:200]}")
            return False
        print(f"[lab-pc] {label}")
        return True
    if not ssh_target:
        return False
    host, user = _parse_ssh_target(ssh_target)
    conn = await _open_pc_ssh(host, user, password)
    try:
        await conn.send_command(joined, timeout_ops=60)
        print(f"[lab-pc] {label} on {user}@{host}")
        return True
    finally:
        await conn.close()


async def restore_untagged_lab_pc(pc_cfg: dict[str, Any], password: str) -> bool:
    """Drop stacked VLANs on BTS lab PC; native enp3s0 stays up for 10.0.0.x fallback."""
    iface = str(pc_cfg.get("mgmt_interface", "enp3s0"))
    joined = build_restore_untagged_commands(iface)
    return await _run_pc_network_command(pc_cfg, password, joined, label=f"restored untagged {iface}")


async def ensure_fallback_ethernet(pc_cfg: dict[str, Any], password: str) -> bool:
    """
    Phase 0 for factory reset:
    enp3s0 untagged with 10.0.0.xx — reach BTS fallback 10.0.0.1 before any QinQ/mgmt IPv6.
    """
    iface = str(pc_cfg.get("mgmt_interface", "enp3s0"))
    prefix = int(pc_cfg.get("fallback_prefix_len", 8))
    fallback_v4 = str(pc_cfg.get("fallback_ipv4", "10.0.0.141")).strip()
    cidr = fallback_v4 if "/" in fallback_v4 else f"{fallback_v4}/{prefix}"
    joined = " && ".join(
        [build_restore_untagged_commands(iface), build_fallback_ipv4_commands(iface, cidr)]
    )
    return await _run_pc_network_command(
        pc_cfg,
        password,
        joined,
        label=f"{iface} fallback {cidr} (untagged, no QinQ yet)",
    )


async def ensure_fallback_subnet(pc_cfg: dict[str, Any], password: str) -> bool:
    """
    Ensure a 10.0.0.x address exists on the given PC interface.
    Adds the configured fallback address only when no 10.0.0.x is present.
    """
    iface = str(pc_cfg.get("mgmt_interface", "enp3s0"))
    prefix = int(pc_cfg.get("fallback_prefix_len", 8))
    fallback_v4 = str(pc_cfg.get("fallback_ipv4", "10.0.0.10")).strip()
    cidr = fallback_v4 if "/" in fallback_v4 else f"{fallback_v4}/{prefix}"
    joined = build_fallback_ipv4_commands(iface, cidr)
    return await _run_pc_network_command(
        pc_cfg,
        password,
        joined,
        label=f"{iface} ensure fallback {cidr}",
    )


async def configure_mgmt_interface(
    pc_cfg: dict[str, Any],
    *,
    ipv6_address: str,
    prefix_len: int,
    password: str,
    vlan_id: int,
    tagging: dict[str, Any] | None = None,
) -> bool:
    """
    Bring up lab PC interface toward BTS (QinQ stacked) or CPE (untagged native).
    """
    iface = str(pc_cfg.get("mgmt_interface", "enp3s0"))
    host_part = normalize_ip(ipv6_address.split("/")[0])
    cidr = ipv6_address if "/" in ipv6_address else f"{host_part}/{prefix_len}"

    tag_plan = tagging or {"untagged": True, "vlan_id": vlan_id}
    if not tagging and vlan_id:
        tag_plan = {"mode": "single", "vlan_id": vlan_id, "untagged": False, "svlan": 0, "cvlan": vlan_id}

    joined, vlan_if = _build_pc_link_commands(iface, tagging=tag_plan, cidr=cidr)

    ssh_target = str(pc_cfg.get("ssh", "")).strip()
    if not ssh_target and pc_cfg.get("local", True):
        # Run the full `joined` command under sudo. Without wrapping, `sudo -n`
        # only applies to the first command in the chain before the `&&` operators.
        rc, out = await asyncio.to_thread(
            _run_local,
            f"sudo -n sh -c {shlex.quote(joined)}",
        )
        if rc != 0:
            rc, out = await asyncio.to_thread(_run_local, joined)
        if rc != 0:
            print(f"[lab-pc] local mgmt VLAN setup failed: {out[:300]}")
            return False
        print(f"[lab-pc] local {vlan_if} -> {cidr}")
        return True

    host, user = _parse_ssh_target(ssh_target)
    conn = await _open_pc_ssh(host, user, password)
    try:
        result = await conn.send_command(joined, timeout_ops=60)
        out = str(result.result or "")
        if "RTNETLINK" in out and "File exists" not in out and "Error" in out:
            print(f"[lab-pc] remote {host} setup: {out[:300]}")
        print(f"[lab-pc] {user}@{host} {vlan_if} -> {cidr}")
        return True
    finally:
        await conn.close()
