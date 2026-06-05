"""Configure management VLAN on lab PCs (primary automation host and secondary CPE-side PC)."""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
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


def _lab_pc_log_prefix(pc_cfg: dict[str, Any] | None) -> str:
    """Distinguish automation host vs remote secondary lab PC in console logs."""
    if pc_cfg is None:
        return "[lab-pc-local]"
    ssh_target = str(pc_cfg.get("ssh", "")).strip()
    if ssh_target:
        return "[lab-pc-remote]"
    if pc_cfg.get("local", True):
        return "[lab-pc-local]"
    return "[lab-pc-remote]"


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
    # Keep a single source of truth for management IPv6.
    # We explicitly clear stale global IPv6 from commonly used legacy VLAN subinterfaces
    # so host reachability checks cannot accidentally succeed via the wrong path.
    cleanup_known_vlan_globals = [
        f"ip -6 addr flush dev {shlex.quote(iface)}.100.101 2>/dev/null || true",
        f"ip -6 addr flush dev {shlex.quote(iface)}.100 2>/dev/null || true",
        f"ip -6 addr flush dev {shlex.quote(iface)}.200.201 2>/dev/null || true",
        f"ip -6 addr flush dev {shlex.quote(iface)}.200 2>/dev/null || true",
        f"ip -6 addr flush dev {shlex.quote(iface)}.201 2>/dev/null || true",
    ]
    if tagging.get("untagged") or mode == "untagged":
        vlan_if = iface
        cmds = [
            *cleanup_known_vlan_globals,
            f"ip -6 addr flush dev {shlex.quote(iface)} 2>/dev/null || true",
            f"ip -6 addr add {shlex.quote(cidr)} dev {shlex.quote(iface)} 2>/dev/null || true",
            f"ip link set {shlex.quote(iface)} up",
        ]
        return " && ".join(cmds), vlan_if

    if mode == "single":
        vlan_id = int(tagging.get("vlan_id", tagging.get("cvlan", 101)))
        vlan_if = f"{iface}.{vlan_id}"
        cmds = [
            *cleanup_known_vlan_globals,
            f"ip -6 addr flush dev {shlex.quote(iface)} 2>/dev/null || true",
            f"ip link add link {shlex.quote(iface)} name {shlex.quote(vlan_if)} type vlan id {vlan_id} 2>/dev/null || true",
            f"ip -6 addr flush dev {shlex.quote(vlan_if)} 2>/dev/null || true",
            f"ip -6 addr add {shlex.quote(cidr)} dev {shlex.quote(vlan_if)} 2>/dev/null || true",
            f"ip link set {shlex.quote(vlan_if)} up",
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
        *cleanup_known_vlan_globals,
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
        tag = _lab_pc_log_prefix(pc_cfg)
        if rc != 0:
            print(f"{tag} {label} failed: {out[:200]}")
            return False
        print(f"{tag} {label}")
        return True
    if not ssh_target:
        return False
    host, user = _parse_ssh_target(ssh_target)
    conn = await _open_pc_ssh(host, user, password)
    try:
        await conn.send_command(joined, timeout_ops=60)
        print(f"{_lab_pc_log_prefix(pc_cfg)} {label} on {user}@{host}")
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


def build_mgmt_vlan_ipv4_commands(
    iface: str,
    vlan_id: int,
    ipv4: str,
    netmask: str,
) -> tuple[str, str]:
    """Tagged mgmt VLAN subinterface with IPv4 (e.g. enp3s0.101 + 192.168.2.10/24)."""
    import ipaddress

    vlan_if = f"{iface}.{vlan_id}"
    host = normalize_ip(ipv4.split("/")[0])
    mask = netmask.strip() or "255.255.255.0"
    if "/" in ipv4:
        cidr = ipv4
    else:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}", strict=False).prefixlen
        cidr = f"{host}/{prefix}"
    iface_q = shlex.quote(iface)
    vlan_q = shlex.quote(vlan_if)
    cidr_q = shlex.quote(cidr)
    joined = " && ".join(
        [
            f"ip link set {iface_q} up",
            f"ip link show {vlan_q} >/dev/null 2>&1 || "
            f"ip link add link {iface_q} name {vlan_q} type vlan id {int(vlan_id)}",
            f"ip -4 addr flush dev {vlan_q} 2>/dev/null || true",
            f"ip -4 addr add {cidr_q} dev {vlan_q} 2>/dev/null || true",
            f"ip link set {vlan_q} up",
        ]
    )
    return joined, vlan_if


def build_mgmt_vlan_recover_commands(
    iface: str,
    vlan_id: int,
    ipv4: str,
    netmask: str,
) -> tuple[str, str]:
    """
    Recreate tagged mgmt VLAN after a flap (ip link down alone is not enough on this bench).
    Equivalent to: vconfig rem/add + ifconfig, using ip(route2).
    """
    import ipaddress

    vlan_if = f"{iface}.{vlan_id}"
    host = normalize_ip(ipv4.split("/")[0])
    mask = netmask.strip() or "255.255.255.0"
    if "/" in ipv4:
        cidr = ipv4
    else:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}", strict=False).prefixlen
        cidr = f"{host}/{prefix}"
    iface_q = shlex.quote(iface)
    vlan_q = shlex.quote(vlan_if)
    cidr_q = shlex.quote(cidr)
    joined = " && ".join(
        [
            f"ip link set {iface_q} up",
            f"ip link set {vlan_q} down 2>/dev/null || true",
            f"ip link del {vlan_q} 2>/dev/null || true",
            f"ip link add link {iface_q} name {vlan_q} type vlan id {int(vlan_id)}",
            f"ip -4 addr flush dev {vlan_q} 2>/dev/null || true",
            f"ip -4 addr add {cidr_q} dev {vlan_q} 2>/dev/null || true",
            f"ip link set {vlan_q} up",
        ]
    )
    return joined, vlan_if


async def restore_lab_pc_mgmt_vlan_ipv4(
    pc_cfg: dict[str, Any],
    *,
    vlan_id: int,
    ipv4: str,
    netmask: str,
    password: str,
) -> str | None:
    """Delete and recreate mgmt VLAN subinterface with IPv4 (post-flap recovery)."""
    iface = str(pc_cfg.get("mgmt_interface", "enp3s0"))
    joined, vlan_if = build_mgmt_vlan_recover_commands(iface, vlan_id, ipv4, netmask)
    ok = await _run_pc_network_command(
        pc_cfg,
        password,
        joined,
        label=f"{vlan_if} recreated (mgmt VLAN {vlan_id})",
    )
    return vlan_if if ok else None


def build_iface_mtu_command(iface: str, mtu: int) -> str:
    """Set link MTU on a lab PC interface (e.g. enp3s0.101)."""
    return f"ip link set {shlex.quote(iface)} mtu {int(mtu)}"


def read_local_iface_mtu(iface: str) -> str:
    """Read current MTU from sysfs on the automation host."""
    path = f"/sys/class/net/{iface}/mtu"
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def build_iface_link_state_commands(iface: str, *, up: bool) -> str:
    state = "up" if up else "down"
    return f"ip link set {shlex.quote(iface)} {state}"


async def set_lab_pc_iface_link_state(
    pc_cfg: dict[str, Any],
    iface: str,
    *,
    up: bool,
    password: str,
) -> bool:
    """Bring lab PC mgmt interface up/down (local or remote primary/secondary PC)."""
    joined = build_iface_link_state_commands(iface, up=up)
    state = "up" if up else "down"
    return await _run_pc_network_command(
        pc_cfg,
        password,
        joined,
        label=f"{iface} link {state}",
    )


async def set_lab_pc_iface_mtu(
    pc_cfg: dict[str, Any],
    iface: str,
    mtu: int,
    password: str,
) -> bool:
    """Apply MTU on backend PC mgmt interface (local or SSH to primary_pc)."""
    joined = build_iface_mtu_command(iface, mtu)
    return await _run_pc_network_command(
        pc_cfg,
        password,
        joined,
        label=f"{iface} mtu {mtu}",
    )


async def ensure_lab_pc_mgmt_vlan_ipv4(
    pc_cfg: dict[str, Any],
    *,
    vlan_id: int,
    ipv4: str,
    netmask: str,
    password: str,
) -> str | None:
    """Configure lab PC tagged mgmt VLAN IPv4; return interface name (e.g. enp3s0.101)."""
    iface = str(pc_cfg.get("mgmt_interface", "enp3s0"))
    joined, vlan_if = build_mgmt_vlan_ipv4_commands(iface, vlan_id, ipv4, netmask)
    ok = await _run_pc_network_command(
        pc_cfg,
        password,
        joined,
        label=f"{vlan_if} IPv4 {ipv4} (mgmt VLAN {vlan_id})",
    )
    return vlan_if if ok else None


def _mgmt_vlan_id_from_profile(profile: dict[str, Any]) -> int:
    tb = profile.get("testbed", {}) or {}
    mgmt = tb.get("mgmt_vlan", {}) or profile.get("mgmt_vlan", {}) or {}
    return int(mgmt.get("lab_pc_vlan_id", mgmt.get("uci_value", 101)))


def build_untagged_ipv4_commands(iface: str, ipv4: str, netmask: str) -> tuple[str, str]:
    """Assign IPv4 on native port (CPE side — untagged), e.g. enp3s0 + 192.168.2.20/24."""
    import ipaddress

    host = normalize_ip(ipv4.split("/")[0])
    mask = netmask.strip() or "255.255.255.0"
    prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}", strict=False).prefixlen
    cidr = f"{host}/{prefix}"
    iface_q = shlex.quote(iface)
    cidr_q = shlex.quote(cidr)
    host_re = host.replace(".", r"\.")
    joined = " && ".join(
        [
            f"ip link set {iface_q} up",
            (
                f"ip -4 addr show dev {iface_q} | grep -qE 'inet {host_re}/' "
                f"|| ip -4 addr add {cidr_q} dev {iface_q} 2>/dev/null || true"
            ),
        ]
    )
    return joined, iface


def build_secondary_pc_parent_prepare_commands(
    iface: str,
    sec: dict[str, Any],
) -> str:
    """
    Before enp3s0.101 + 192.168.2.x: remove 192.168.2.0/24 from parent enp3s0
    (same subnet on parent + VLAN breaks mgmt). Keep 10.0.0.x on parent for CPE hop.
    Optionally relocate parent to 192.168.3.x.
    """
    import ipaddress

    iface_q = shlex.quote(iface)
    parts = [f"ip link set {iface_q} up"]
    if sec.get("clear_parent_ipv4", True):
        # Drop only 192.168.2.0/24 from parent — never flush 10.0.0.x (CPE factory SSH hop).
        parts.append(
            f"for cidr in $(ip -4 -o addr show dev {iface_q} | awk '{{print $4}}'); do "
            f"case \"$cidr\" in 192.168.2.*) ip -4 addr del \"$cidr\" dev {iface_q} 2>/dev/null || true ;; esac; "
            f"done"
        )
    relocate = str(sec.get("parent_relocate_ipv4", "")).strip()
    if relocate:
        host = normalize_ip(relocate.split("/")[0])
        mask = str(sec.get("parent_relocate_netmask", "255.255.255.0")).strip()
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}", strict=False).prefixlen
        cidr = relocate if "/" in relocate else f"{host}/{prefix}"
        parts.append(
            f"ip -4 addr add {shlex.quote(cidr)} dev {iface_q} 2>/dev/null || true"
        )
    prefix = int(sec.get("fallback_prefix_len", 8))
    fallback_v4 = str(sec.get("fallback_ipv4", "10.0.0.11")).strip()
    fallback_cidr = fallback_v4 if "/" in fallback_v4 else f"{fallback_v4}/{prefix}"
    parts.append(build_fallback_ipv4_commands(iface, fallback_cidr))
    return " && ".join(parts)


def _secondary_pc_use_tagged_mgmt(profile: dict[str, Any]) -> bool:
    """Remote PC mgmt: tagged enp3s0.101 when use_tagged_mgmt_vlan is true."""
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    if "use_tagged_mgmt_vlan" in sec:
        return bool(sec.get("use_tagged_mgmt_vlan"))
    cpe_tag = (tb.get("lab_pc_tagging", {}) or {}).get("cpe", {}) or {}
    mode = str(cpe_tag.get("mode", "untagged")).lower()
    return mode not in ("untagged", "") and not cpe_tag.get("untagged")


async def ensure_secondary_pc_cpe_hop_ready(
    profile: dict[str, Any],
    dut_password: str,
    *,
    verify_cpe_ping: bool = True,
    max_wait_s: int = 45,
    interval_s: int = 3,
) -> None:
    """
    Ensure secondary PC parent enp3s0 has 10.0.0.xx and can reach CPE factory IP.
    Required before nested SSH to CPE (10.0.0.1) from the remote lab PC.
    """
    tb = profile.get("testbed", {}) or {}
    sec = dict(tb.get("secondary_pc", {}) or {})
    if not sec.get("enabled", True):
        raise RuntimeError("testbed.secondary_pc is disabled")
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        raise RuntimeError("testbed.secondary_pc.ssh is not configured")

    sec_pass = str(sec.get("password", "")).strip() or dut_password
    iface = str(sec.get("mgmt_interface", "enp3s0"))
    ok = await ensure_fallback_subnet(sec, sec_pass)
    if not ok:
        raise RuntimeError(f"secondary PC {iface} 10.0.0.x fallback setup failed")

    if not verify_cpe_ping:
        return

    cpe_factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))
    host, user = _parse_ssh_target(ssh_target)
    conn = await _open_pc_ssh(host, user, sec_pass)
    try:
        deadline = time.monotonic() + max(1, max_wait_s)
        last = ""
        while time.monotonic() < deadline:
            result = await conn.send_command(
                f"ping -c 1 -W 2 {shlex.quote(cpe_factory)}",
                timeout_ops=12,
            )
            out = str(result.result or "")
            if (
                "0% packet loss" in out
                or " 0% packet loss" in out
                or "1 received" in out
                or "1 packets received" in out
            ):
                print(
                    f"[lab-pc-remote] secondary→CPE ping {cpe_factory} ok on {user}@{host}"
                )
                return
            last = out[:220]
            await asyncio.sleep(max(1, interval_s))
        raise RuntimeError(
            f"secondary→CPE ping {cpe_factory} failed after {max_wait_s}s: {last}"
        )
    finally:
        await conn.close()


async def ensure_secondary_pc_mgmt_vlan_ipv4(
    profile: dict[str, Any],
    dut_password: str,
    *,
    vlan_id: int | None = None,
    ipv4: str | None = None,
    netmask: str | None = None,
) -> tuple[str | None, str]:
    """
    Configure remote (secondary) PC IPv4 for IP_05/iperf.
    Tagged (default for IP suite): move parent off 192.168.2.0/24, keep 10.0.0.x,
    create enp3s0.101 if missing, assign mgmt IPv4 (e.g. 192.168.2.11).
    Returns (interface_name, server_ipv4).
    """
    tb = profile.get("testbed", {}) or {}
    sec = dict(tb.get("secondary_pc", {}) or {})
    if not sec.get("enabled", True):
        raise RuntimeError("testbed.secondary_pc is disabled")
    if not str(sec.get("ssh", "")).strip():
        raise RuntimeError("testbed.secondary_pc.ssh is not configured")

    host_ip = normalize_ip(
        str(
            ipv4
            or sec.get("mgmt_vlan_ipv4")
            or sec.get("iperf_listen_ipv4")
            or "192.168.2.11"
        ).split("/")[0]
    )
    mask = str(netmask or sec.get("mgmt_vlan_netmask") or "255.255.255.0").strip()
    sec_pass = str(sec.get("password", "")).strip() or dut_password
    iface = str(sec.get("mgmt_interface", "enp3s0"))

    await ensure_secondary_pc_cpe_hop_ready(profile, dut_password, verify_cpe_ping=False)

    if _secondary_pc_use_tagged_mgmt(profile):
        prep = build_secondary_pc_parent_prepare_commands(iface, sec)
        ok_prep = await _run_pc_network_command(
            sec,
            sec_pass,
            prep,
            label=f"{iface} clear/relocate parent IPv4 before mgmt VLAN",
        )
        if not ok_prep:
            raise RuntimeError(f"remote PC parent {iface} prepare failed")

    if not _secondary_pc_use_tagged_mgmt(profile):
        joined, if_name = build_untagged_ipv4_commands(iface, host_ip, mask)
        ok = await _run_pc_network_command(
            sec,
            sec_pass,
            joined,
            label=f"{if_name} untagged IPv4 {host_ip} (remote PC toward CPE)",
        )
        if not ok:
            raise RuntimeError(f"remote PC untagged IPv4 setup failed ({host_ip} on {iface})")
        await ensure_fallback_subnet(sec, sec_pass)
        return if_name, host_ip

    vid = int(vlan_id if vlan_id is not None else _mgmt_vlan_id_from_profile(profile))
    vlan_if = await ensure_lab_pc_mgmt_vlan_ipv4(
        sec,
        vlan_id=vid,
        ipv4=host_ip,
        netmask=mask,
        password=sec_pass,
    )
    if not vlan_if:
        raise RuntimeError(
            f"remote PC mgmt VLAN {vid} setup failed ({host_ip} on {iface})"
        )
    await ensure_fallback_subnet(sec, sec_pass)
    return vlan_if, host_ip


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
            print(f"[lab-pc-local] local mgmt VLAN setup failed: {out[:300]}")
            return False
        print(f"[lab-pc-local] {vlan_if} -> {cidr}")
        return True

    host, user = _parse_ssh_target(ssh_target)
    conn = await _open_pc_ssh(host, user, password)
    try:
        result = await conn.send_command(joined, timeout_ops=60)
        out = str(result.result or "")
        if "RTNETLINK" in out and "File exists" not in out and "Error" in out:
            print(f"[lab-pc-remote] {host} setup: {out[:300]}")
        print(f"[lab-pc-remote] {user}@{host} {vlan_if} -> {cidr}")
        return True
    finally:
        await conn.close()
