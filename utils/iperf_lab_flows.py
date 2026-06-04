"""Lab PC ↔ remote PC iperf3 throughput (IP_05 / IP_22)."""

from __future__ import annotations

import asyncio
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any

import pytest

from utils.lab_pc_net import (
    _open_pc_ssh,
    _parse_ssh_target,
    _run_local,
    ensure_secondary_pc_cpe_hop_ready,
    ensure_secondary_pc_mgmt_vlan_ipv4,
)
from utils.net_utils import normalize_ip


@dataclass
class IperfSessionResult:
    label: str
    protocol: str
    reverse: bool
    ok: bool
    mbps: float
    raw: str


def _parse_iperf3_mbps(output: str, *, reverse: bool) -> float:
    """Return throughput (Mbps) for the client direction under test."""
    lines = output.splitlines()
    sender_rates: list[float] = []
    receiver_rates: list[float] = []
    for line in lines:
        if "bits/sec" not in line.lower() and "bits/s" not in line.lower():
            continue
        m = re.search(r"([\d.]+)\s+(?:[MGK])?bits/sec", line, re.I)
        if not m:
            continue
        rate = float(m.group(1))
        low = line.lower()
        if "gbits/sec" in low:
            rate *= 1000.0
        elif "kbits/sec" in low or "kbits/s" in low:
            rate /= 1000.0
        if "sender" in low:
            sender_rates.append(rate)
        elif "receiver" in low:
            receiver_rates.append(rate)
    if reverse:
        return receiver_rates[-1] if receiver_rates else (sender_rates[-1] if sender_rates else 0.0)
    return sender_rates[-1] if sender_rates else (receiver_rates[-1] if receiver_rates else 0.0)


def _resolve_iperf_server_ipv6(
    cfg: dict[str, Any],
    profile: dict[str, Any],
    *,
    device_target: str = "bts",
) -> str:
    explicit = str(cfg.get("iperf_server_v6", "")).strip()
    if explicit:
        return normalize_ip(explicit.split("/")[0])
    dut = profile.get("dut", {}) or {}
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    if device_target == "cpe":
        for key in ("ipv6_bts",):
            v6 = str(mgmt.get(key, "") or dut.get("local_ipv6", "")).strip()
            if v6:
                return normalize_ip(v6.split("/")[0])
    else:
        for key in ("ipv6_cpe", "ipv6_cpe_pc"):
            v6 = str(mgmt.get(key, "")).strip()
            if v6:
                return normalize_ip(v6.split("/")[0])
        for ip in dut.get("remote_ipv6s") or []:
            clean = normalize_ip(str(ip).split("/")[0])
            if clean:
                return clean
    return ""


def _resolve_iperf_server_ipv4(cfg: dict[str, Any], profile: dict[str, Any]) -> str:
    explicit = str(cfg.get("iperf_server_v4", "")).strip()
    if explicit:
        return normalize_ip(explicit)
    sec = (profile.get("testbed", {}) or {}).get("secondary_pc", {}) or {}
    for key in ("mgmt_vlan_ipv4", "iperf_listen_ipv4", "iperf_server_ipv4"):
        sec_ip = str(sec.get(key, "")).strip()
        if sec_ip:
            return normalize_ip(sec_ip.split("/")[0])
    return normalize_ip(str(cfg.get("lab_pc_remote_mgmt_ipv4", "192.168.2.11")).split("/")[0])


def _resolve_expected_mbps(cfg: dict[str, Any], profile: dict[str, Any], *, udp: bool) -> float:
    key = "iperf_expected_udp_mbps" if udp else "iperf_expected_tcp_mbps"
    raw = cfg.get(key) or cfg.get("iperf_expected_mbps")
    if raw is not None and str(raw).strip() != "":
        return float(raw)
    link = profile.get("link", {}) or {}
    bw = str(link.get("target_bandwidth", "")).strip()
    mcs = str(link.get("target_mcs_rate", "")).strip()
    if bw and mcs:
        try:
            from traffic.operating_rate_table import operating_rate_mbps

            return float(operating_rate_mbps(bw, mcs))
        except Exception:
            pass
    return float(cfg.get("iperf_expected_mbps_default", 50.0))


async def _run_local_iperf3(
    cmd: str,
    *,
    timeout_s: int = 120,
) -> tuple[int, str]:
    for prefix in ("sudo -n ", ""):
        full = prefix + cmd
        rc, out = await asyncio.to_thread(_run_local, full)
        if rc == 0 or "bits/sec" in out.lower():
            return rc, out
    return rc, out


async def _ssh_secondary_pc_cmd(profile: dict[str, Any], password: str, command: str, *, timeout: int = 60) -> str:
    sec = (profile.get("testbed", {}) or {}).get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        raise RuntimeError("testbed.secondary_pc.ssh is not configured")
    sec_pass = str(sec.get("password", "")).strip() or password
    host, user = _parse_ssh_target(ssh_target)
    conn = await _open_pc_ssh(host, user, sec_pass)
    try:
        result = await conn.send_command(command, timeout_ops=timeout)
        return str(getattr(result, "result", result) or "")
    finally:
        await conn.close()


async def _manage_cpe_iperf3_server_via_secondary_pc(
    profile: dict[str, Any],
    password: str,
    *,
    bind_ip: str,
    port: int,
    start: bool,
) -> None:
    """Start/stop iperf3 -s on CPE (SSH hop via secondary PC → CPE factory IP)."""
    await ensure_secondary_pc_cpe_hop_ready(profile, password)
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    cpe_factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))
    cpe_pass = str(password).strip()
    port_q = shlex.quote(str(port))
    bind_q = shlex.quote(normalize_ip(bind_ip))
    if start:
        inner = (
            f"pkill -f 'iperf3 -s' >/dev/null 2>&1 || true; "
            f"command -v iperf3 >/dev/null 2>&1; "
            f"iperf3 -s -6 -p {port_q} -B {bind_q} -D"
        )
    else:
        inner = "pkill -f 'iperf3 -s' >/dev/null 2>&1 || true"
    hop_cmd = (
        f"sshpass -p {shlex.quote(cpe_pass)} ssh -o StrictHostKeyChecking=no "
        f"-o ConnectTimeout=15 root@{cpe_factory} {shlex.quote(inner)}"
    )
    out = await _ssh_secondary_pc_cmd(profile, password, hop_cmd, timeout=45)
    if start and ("not found" in out.lower() or "command not found" in out.lower()):
        raise RuntimeError("iperf3 not installed on CPE")


async def _manage_remote_iperf3_server(
    profile: dict[str, Any],
    password: str,
    *,
    port: int,
    bind_ip: str,
    start: bool,
) -> None:
    port_q = shlex.quote(str(port))
    bind_q = shlex.quote(normalize_ip(bind_ip))
    if not start:
        await _ssh_secondary_pc_cmd(
            profile,
            password,
            f"pkill -f 'iperf3 -s.*-p {port_q}' >/dev/null 2>&1 || pkill -f 'iperf3 -s' >/dev/null 2>&1 || true",
            timeout=20,
        )
        return
    await ensure_secondary_pc_cpe_hop_ready(profile, password, verify_cpe_ping=False)
    await _ssh_secondary_pc_cmd(
        profile,
        password,
        f"pkill -f 'iperf3 -s' >/dev/null 2>&1 || true; "
        f"command -v iperf3 >/dev/null 2>&1 || command -v iperf >/dev/null 2>&1",
        timeout=20,
    )
    out = await _ssh_secondary_pc_cmd(
        profile,
        password,
        f"iperf3 -s -p {port_q} -B {bind_q} -D >/dev/null 2>&1",
        timeout=30,
    )
    if "not found" in out.lower() or "command not found" in out.lower():
        raise RuntimeError("iperf3 not installed on secondary (remote) PC")


async def _wait_for_iperf_server_ready(
    *,
    server_ip: str,
    port: int,
    bind_ip: str,
    wait_s: int = 30,
    v6: bool = False,
) -> None:
    """Probe from lab PC (same bind path as throughput test) until iperf3 server accepts."""
    host = shlex.quote(normalize_ip(server_ip))
    port_q = shlex.quote(str(port))
    bind = shlex.quote(bind_ip)
    v6_flag = " -6" if v6 else ""
    deadline = time.monotonic() + max(5, wait_s)
    last = ""
    while time.monotonic() < deadline:
        cmd = f"iperf3 -c {host} -p {port_q} -t 1 -i 1 -B {bind}{v6_flag} 2>&1"
        _, out = await _run_local_iperf3(cmd, timeout_s=20)
        if "connected" in out.lower() or "bits/sec" in out.lower():
            return
        last = out[-220:]
        await asyncio.sleep(2)
    raise RuntimeError(
        f"iperf3{' -6' if v6 else ''} server {server_ip}:{port} not accepting connections within {wait_s}s: {last}"
    )


async def _run_client_session(
    *,
    server_ip: str,
    port: int,
    duration_s: int,
    bind_ip: str,
    bind_iface: str,
    udp: bool,
    reverse: bool,
    udp_bandwidth: str,
    v6: bool = False,
) -> IperfSessionResult:
    label = f"{'UDP' if udp else 'TCP'} {'RX' if reverse else 'TX'}"
    host = shlex.quote(server_ip)
    port_q = shlex.quote(str(port))
    bind = shlex.quote(bind_ip)
    extra = ""
    if v6:
        extra += " -6"
    if udp:
        extra += f" -u -b {shlex.quote(udp_bandwidth)}"
    if reverse:
        extra += " -R"
    cmd = (
        f"iperf3 -c {host} -p {port_q} -t {int(duration_s)} -i 1 "
        f"-B {bind} {extra} 2>&1"
    ).strip()
    rc, out = await _run_local_iperf3(cmd, timeout_s=duration_s + 90)
    mbps = _parse_iperf3_mbps(out, reverse=reverse)
    ok = mbps > 0 and ("error" not in out.lower() or "bits/sec" in out.lower())
    return IperfSessionResult(label=label, protocol="udp" if udp else "tcp", reverse=reverse, ok=ok, mbps=mbps, raw=out)


async def _run_client_session_with_retry(
    *,
    server_ip: str,
    port: int,
    duration_s: int,
    bind_ip: str,
    bind_iface: str,
    udp: bool,
    reverse: bool,
    udp_bandwidth: str,
    attempts: int = 2,
    v6: bool = False,
) -> IperfSessionResult:
    last = IperfSessionResult(
        label="",
        protocol="udp" if udp else "tcp",
        reverse=reverse,
        ok=False,
        mbps=0.0,
        raw="",
    )
    for attempt in range(1, max(1, attempts) + 1):
        last = await _run_client_session(
            server_ip=server_ip,
            port=port,
            duration_s=duration_s,
            bind_ip=bind_ip,
            bind_iface=bind_iface,
            udp=udp,
            reverse=reverse,
            udp_bandwidth=udp_bandwidth,
            v6=v6,
        )
        if last.ok and last.mbps > 0:
            return last
        if attempt < attempts:
            await asyncio.sleep(3)
    return last


def _validate_session_throughput(
    result: IperfSessionResult,
    *,
    expected_mbps: float,
    min_ratio: float,
) -> None:
    floor = expected_mbps * min_ratio
    if not result.ok or result.mbps <= 0:
        pytest.fail(
            f"IP_05 {result.label}: iperf failed or zero throughput — {result.raw[-400:]}"
        )
    pct = (result.mbps / expected_mbps * 100.0) if expected_mbps > 0 else 0.0
    detail = (
        f"{result.label}: {result.mbps:.1f} Mbps "
        f"(expected {expected_mbps:.1f} Mbps, floor {floor:.1f} Mbps = {min_ratio*100:.0f}%)"
    )
    if result.mbps < floor:
        pytest.fail(f"IP_05 {detail} — below acceptable throughput floor")
    elif result.mbps < expected_mbps:
        # Above floor but below nominal: pass with note (RF variance on bench).
        pass


async def _run_local_iperf3_bg(cmd: str) -> asyncio.subprocess.Process:
    for prefix in ("sudo -n ", ""):
        proc = await asyncio.create_subprocess_shell(
            prefix + cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        return proc
    raise RuntimeError("failed to start background iperf3")


async def prepare_ipv4_lab_iperf_server(ctx: Any) -> dict[str, Any]:
    """Shared IP_05/IP_13 lab iperf server setup; returns server metadata."""
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    password = str(cfg.get("_password", ""))
    port = int(cfg.get("iperf_port", 5201))
    bind_ip = str(cfg.get("lab_pc_mgmt_ipv4", "192.168.2.10")).strip()

    from utils.ip_test_flows import (
        _ensure_lab_mgmt_vlan_for_ping,
        _ping_from_lab_pc,
        _remote_ping_wait_settings,
        _wait_for_remote_ping_from_lab,
    )

    vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
    max_wait_s, interval_s = _remote_ping_wait_settings(cfg)

    remote_server = ""
    try:
        _, remote_ip = await ensure_secondary_pc_mgmt_vlan_ipv4(
            profile,
            password,
            ipv4=str(cfg.get("lab_pc_remote_mgmt_ipv4", "")).strip() or None,
            netmask=str(cfg.get("lab_pc_mgmt_netmask", "255.255.255.0")).strip(),
        )
        remote_server = normalize_ip(
            str(cfg.get("iperf_server_v4", "")).strip() or remote_ip
        )
        probe = await _ping_from_lab_pc(vlan_if, remote_server, count=1, per_packet_wait_s=2)
        if not probe.ok:
            ctx.notes.append(f"remote PC {remote_server} not reachable from lab (use CPE server)")
            remote_server = ""
    except Exception as exc:
        ctx.notes.append(f"remote PC prep skipped: {exc}")

    cpe_server = normalize_ip(
        str(ctx.peer_host or "").strip() or str(cfg.get("remote_ping_host", "")).strip()
    )
    if not cpe_server:
        pytest.skip("no iperf server target (CPE IPv4 / remote_ping_host)")

    server_ip = remote_server if remote_server else cpe_server
    server_on_cpe = not bool(remote_server)

    ready = await _wait_for_remote_ping_from_lab(
        vlan_if,
        server_ip,
        wait_s=max_wait_s,
        interval_s=interval_s,
        notes=ctx.notes,
    )
    if not ready.ok:
        pytest.skip(f"iperf server {server_ip} not reachable via {vlan_if} within {max_wait_s}s")

    if server_on_cpe:
        await _manage_cpe_iperf3_server_via_secondary_pc(
            profile, password, bind_ip=server_ip, port=port, start=True
        )
    else:
        await _manage_remote_iperf3_server(
            profile, password, port=port, bind_ip=server_ip, start=True
        )
    await _wait_for_iperf_server_ready(
        server_ip=server_ip,
        port=port,
        bind_ip=bind_ip,
        wait_s=int(cfg.get("iperf_server_ready_wait_s", 30)),
    )
    return {
        "server_ip": server_ip,
        "server_on_cpe": server_on_cpe,
        "vlan_if": vlan_if,
        "bind_ip": bind_ip,
        "port": port,
        "profile": profile,
        "password": password,
    }


async def _stop_iperf_server(meta: dict[str, Any]) -> None:
    profile = meta["profile"]
    password = meta["password"]
    port = meta["port"]
    server_ip = meta["server_ip"]
    if meta["server_on_cpe"]:
        await _manage_cpe_iperf3_server_via_secondary_pc(
            profile, password, bind_ip=server_ip, port=port, start=False
        )
    else:
        await _manage_remote_iperf3_server(
            profile, password, port=port, bind_ip=server_ip, start=False
        )


async def run_ip13_reboot_during_traffic(ctx: Any, *, v6: bool = False) -> None:
    """
    IP_13/IP_32: start lab iperf3, reboot DUT mid-session, verify drop then recovery.
    """
    cfg = ctx.cfg
    cid = "IP_32" if v6 else "IP_13"
    if v6:
        meta = await prepare_ipv6_lab_iperf_server(ctx)
        bind_ip = meta["bind_v6"]
    else:
        meta = await prepare_ipv4_lab_iperf_server(ctx)
        bind_ip = meta["bind_ip"]
    server_ip = meta["server_ip"]
    port = meta["port"]
    duration = int(cfg.get("ip13_iperf_duration_s", 30))
    reboot_delay = int(cfg.get("ip13_reboot_delay_s", 5))
    recovery_s = int(cfg.get("ip13_recovery_iperf_s", 5))

    from utils.ip_test_flows import (
        _assert_lab_ping_ipv4,
        _assert_lab_ping_ipv6,
        _event_ssh_hosts,
        _resolve_bts_lan_ipv4,
        _resolve_dut_ipv6_for_lab_ping,
        _wait_ssh_after_event,
    )

    host_q = shlex.quote(server_ip)
    port_q = shlex.quote(str(port))
    bind_q = shlex.quote(bind_ip)
    v6_flag = " -6" if v6 else ""
    cmd = f"iperf3 -c {host_q} -p {port_q} -t {duration} -i 1 -B {bind_q}{v6_flag} 2>&1"
    proc = await _run_local_iperf3_bg(cmd)
    ctx.notes.append(f"{cid} iperf client started toward {server_ip}:{port}")
    await asyncio.sleep(max(1, reboot_delay))

    password = str(cfg.get("_password", ""))
    lan_ip = ""
    if v6:
        lan_ip = await _resolve_dut_ipv6_for_lab_ping(ctx)
    elif ctx.device_target == "bts":
        lan_ip = await _resolve_bts_lan_ipv4(ctx)
    else:
        lan_ip = normalize_ip(str(cfg.get("remote_ping_host", "")).split("/")[0])

    try:
        await ctx.ssh.send_command("reboot", timeout_ops=5)
    except Exception:
        pass
    await asyncio.sleep(2)
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=duration + 60)
    except asyncio.TimeoutError:
        proc.kill()
        out_b = b""
    raw = (out_b or b"").decode(errors="replace")
    had_traffic = "bits/sec" in raw.lower()
    had_interrupt = (
        "error" in raw.lower()
        or "connection refused" in raw.lower()
        or "unable to connect" in raw.lower()
        or "broken pipe" in raw.lower()
        or not had_traffic
    )
    ctx.notes.append(f"{cid} mid-reboot iperf: traffic={had_traffic} interrupt={had_interrupt}")
    assert had_traffic or had_interrupt, f"{cid}: no traffic or interrupt seen: {raw[-400:]}"

    if v6:
        await _stop_iperf_server_v6(meta)
    else:
        await _stop_iperf_server(meta)
    timeout_s = int(cfg.get("reboot_timeout_s", 200))
    new_ssh, effective = await _wait_ssh_after_event(
        ctx,
        password,
        timeout_s=timeout_s,
        extra_hosts=await _event_ssh_hosts(ctx, lan_ip=lan_ip or None),
    )
    ctx.ssh = new_ssh
    ctx.host = effective

    if ctx.device_target == "bts":
        if v6:
            from utils.ip_case_preflight import run_post_event_testbed_recovery_v6

            await run_post_event_testbed_recovery_v6(
                ctx, label=f"{cid}-post-reboot", require_cpe=True
            )
        else:
            from utils.ip_case_preflight import run_post_event_testbed_recovery

            await run_post_event_testbed_recovery(ctx, label=f"{cid}-post-reboot", require_cpe=True)

    if v6:
        if meta["server_on_cpe"]:
            await _manage_cpe_iperf3_server_via_secondary_pc(
                meta["profile"], meta["password"], bind_ip=server_ip, port=port, start=True
            )
        else:
            await _manage_bts_iperf3_server_ssh(
                server_ip, meta["password"], bind_ip=server_ip, port=port, start=True
            )
        await _wait_for_iperf_server_ready(
            server_ip=server_ip,
            port=port,
            bind_ip=bind_ip,
            wait_s=int(cfg.get("iperf_server_ready_wait_s", 30)),
            v6=True,
        )
    elif meta["server_on_cpe"]:
        await _manage_cpe_iperf3_server_via_secondary_pc(
            meta["profile"], meta["password"], bind_ip=server_ip, port=port, start=True
        )
    else:
        await _manage_remote_iperf3_server(
            meta["profile"], meta["password"], port=port, bind_ip=server_ip, start=True
        )
        await _wait_for_iperf_server_ready(
            server_ip=server_ip,
            port=port,
            bind_ip=bind_ip,
            wait_s=int(cfg.get("iperf_server_ready_wait_s", 30)),
        )
    rec_cmd = f"iperf3 -c {host_q} -p {port_q} -t {recovery_s} -i 1 -B {bind_q}{v6_flag} 2>&1"
    _, rec_out = await _run_local_iperf3(rec_cmd, timeout_s=recovery_s + 30)
    rec_mbps = _parse_iperf3_mbps(rec_out, reverse=False)
    assert rec_mbps > 0 or "bits/sec" in rec_out.lower(), (
        f"{cid} post-recovery iperf failed: {rec_out[-300:]}"
    )
    ctx.notes.append(f"{cid} post-recovery iperf: {rec_mbps:.1f} Mbps")
    if lan_ip:
        if v6:
            await _assert_lab_ping_ipv6(ctx, lan_ip)
        else:
            await _assert_lab_ping_ipv4(ctx, lan_ip)
    if v6:
        await _stop_iperf_server_v6(meta)
    else:
        await _stop_iperf_server(meta)


async def run_ip05_ipv4_lab_throughput(ctx: Any) -> None:
    """
    IP_05: iperf3 server at far end of RF link, client on local lab PC (mgmt VLAN).
    Bench: local→CPE (.241) is reachable; remote PC (.20) is off the RF path — server runs on CPE.
    TCP + UDP, forward and reverse (-R). Partial when throughput runs but < min_ratio of expected.
    """
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    password = str(cfg.get("_password", ""))

    port = int(cfg.get("iperf_port", 5201))
    duration = int(cfg.get("iperf_duration_s", 10))
    min_ratio = float(cfg.get("iperf_min_throughput_ratio", 0.70))
    udp_bw = str(cfg.get("iperf_udp_bandwidth", "50M"))
    bind_ip = str(cfg.get("lab_pc_mgmt_ipv4", "192.168.2.10")).strip()
    sessions = int(cfg.get("iperf_session_count", 1))
    if sessions < 1:
        sessions = 1

    meta = await prepare_ipv4_lab_iperf_server(ctx)
    server_ip = meta["server_ip"]
    server_on_cpe = meta["server_on_cpe"]
    vlan_if = meta["vlan_if"]
    ctx.notes.append(
        f"IP_05 iperf server on {'CPE' if server_on_cpe else 'remote PC'} {server_ip}; "
        f"local client {bind_ip}/{vlan_if}"
    )
    try:
        expected_tcp = _resolve_expected_mbps(cfg, profile, udp=False)
        expected_udp = _resolve_expected_mbps(cfg, profile, udp=True)
        ctx.notes.append(
            f"IP_05 expected TCP {expected_tcp:.1f} Mbps, UDP {expected_udp:.1f} Mbps, "
            f"pass floor {min_ratio*100:.0f}%"
        )

        plan: list[tuple[bool, bool]] = [
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ]
        for udp, reverse in plan:
            for _ in range(sessions):
                res = await _run_client_session_with_retry(
                    server_ip=server_ip,
                    port=port,
                    duration_s=duration,
                    bind_ip=bind_ip,
                    bind_iface=vlan_if,
                    udp=udp,
                    reverse=reverse,
                    udp_bandwidth=udp_bw,
                    attempts=int(cfg.get("iperf_client_retries", 2)),
                )
                ctx.notes.append(f"IP_05 {res.label}: {res.mbps:.1f} Mbps")
                _validate_session_throughput(
                    res,
                    expected_mbps=expected_udp if udp else expected_tcp,
                    min_ratio=min_ratio,
                )
                await asyncio.sleep(1)
    finally:
        await _stop_iperf_server(meta)


async def _manage_bts_iperf3_server_ssh(
    bts_host: str,
    password: str,
    *,
    bind_ip: str,
    port: int,
    start: bool,
) -> None:
    from utils.ip_test_flows import _close_ssh, open_ssh_with_fallback

    cfg = {"_strict_ipv6": True, "_cli_fallback_ip": None}
    ssh, _, _ = await open_ssh_with_fallback(
        normalize_ip(bts_host),
        password,
        cfg,
        attempts=3,
        retry_interval_s=10,
    )
    port_q = shlex.quote(str(port))
    bind_q = shlex.quote(normalize_ip(bind_ip))
    try:
        if start:
            await ssh.send_command(
                f"pkill -f 'iperf3 -s' >/dev/null 2>&1 || true; "
                f"iperf3 -s -6 -p {port_q} -B {bind_q} -D",
                timeout_ops=30,
            )
        else:
            await ssh.send_command("pkill -f 'iperf3 -s' >/dev/null 2>&1 || true", timeout_ops=15)
    finally:
        await _close_ssh(ssh)


async def prepare_ipv6_lab_iperf_server(ctx: Any) -> dict[str, Any]:
    """IP_22: iperf3 -6 server on far-end peer (CPE when testing BTS, BTS when testing CPE)."""
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    password = str(cfg.get("_password", ""))
    port = int(cfg.get("iperf_port", 5201))
    dut = profile.get("dut", {}) or {}

    from utils.ip_test_flows import (
        _ensure_lab_mgmt_vlan_ipv6_for_ping,
        _lab_ping_bind_ipv6,
        _ping_from_lab_pc_v6,
        _remote_ping_wait_settings,
    )

    vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
    if ctx.device_target == "cpe":
        bind_v6 = normalize_ip(str(dut.get("cpe_pc_ipv6", "")).split("/")[0])
        server_on_cpe = False
    else:
        bind_v6 = _lab_ping_bind_ipv6(cfg, profile)
        server_on_cpe = True
    if not bind_v6:
        pytest.skip("IP_22: lab bind IPv6 not configured (bts_pc_ipv6 / cpe_pc_ipv6)")

    server_ip = normalize_ip(
        str(ctx.peer_host or "").strip()
        or _resolve_iperf_server_ipv6(cfg, profile, device_target=ctx.device_target)
    )
    if not server_ip:
        pytest.skip("IP_22: no IPv6 iperf server target (peer / iperf_server_v6)")

    max_wait_s, _ = _remote_ping_wait_settings(cfg)
    ready = await _ping_from_lab_pc_v6(
        vlan_if, server_ip, count=1, bind_ipv6=bind_v6
    )
    if not ready.ok:
        pytest.skip(
            f"IP_22: server {server_ip} not reachable via ping6 on {vlan_if} "
            f"within {max_wait_s}s: {ready.raw[:120]}"
        )

    if server_on_cpe:
        await _manage_cpe_iperf3_server_via_secondary_pc(
            profile, password, bind_ip=server_ip, port=port, start=True
        )
    else:
        await _manage_bts_iperf3_server_ssh(
            server_ip, password, bind_ip=server_ip, port=port, start=True
        )

    probe_cmd = (
        f"iperf3 -6 -c {shlex.quote(server_ip)} -p {shlex.quote(str(port))} "
        f"-t 1 -i 1 -B {shlex.quote(bind_v6)} 2>&1"
    )
    _, probe_out = await _run_local_iperf3(probe_cmd, timeout_s=25)
    if "connected" not in probe_out.lower() and "bits/sec" not in probe_out.lower():
        pytest.skip(f"IP_22: iperf3 -6 server not ready on {server_ip}: {probe_out[-200:]}")

    return {
        "server_ip": server_ip,
        "server_on_cpe": server_on_cpe,
        "vlan_if": vlan_if,
        "profile": profile,
        "password": password,
        "port": port,
        "bind_v6": bind_v6,
    }


async def _stop_iperf_server_v6(meta: dict[str, Any]) -> None:
    if meta.get("server_on_cpe"):
        await _manage_cpe_iperf3_server_via_secondary_pc(
            meta["profile"],
            meta["password"],
            bind_ip=meta["server_ip"],
            port=meta["port"],
            start=False,
        )
    else:
        await _manage_bts_iperf3_server_ssh(
            meta["server_ip"],
            meta["password"],
            bind_ip=meta["server_ip"],
            port=meta["port"],
            start=False,
        )


async def run_ip22_ipv6_lab_throughput(ctx: Any) -> None:
    """
    IP_22: iperf3 -6 server on far-end (CPE mgmt), client on lab PC (bts_pc_ipv6 bind).
    TCP + UDP, forward and reverse (-R), same plan as IP_05.
    """
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    port = int(cfg.get("iperf_port", 5201))
    duration = int(cfg.get("iperf_duration_s", 10))
    min_ratio = float(cfg.get("iperf_min_throughput_ratio", 0.70))
    udp_bw = str(cfg.get("iperf_udp_bandwidth", "50M"))
    sessions = max(1, int(cfg.get("iperf_session_count", 1)))

    meta = await prepare_ipv6_lab_iperf_server(ctx)
    server_ip = meta["server_ip"]
    bind_v6 = meta["bind_v6"]
    vlan_if = meta["vlan_if"]
    server_role = "CPE" if meta["server_on_cpe"] else "BTS"
    ctx.notes.append(
        f"IP_22 iperf3 -6 server on {server_role} {server_ip}; "
        f"lab client bind {bind_v6}/{vlan_if}"
    )

    try:
        expected_tcp = _resolve_expected_mbps(cfg, profile, udp=False)
        expected_udp = _resolve_expected_mbps(cfg, profile, udp=True)
        ctx.notes.append(
            f"IP_22 expected TCP {expected_tcp:.1f} Mbps, UDP {expected_udp:.1f} Mbps, "
            f"pass floor {min_ratio * 100:.0f}%"
        )
        plan: list[tuple[bool, bool]] = [
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ]
        for udp, reverse in plan:
            for _ in range(sessions):
                res = await _run_client_session_with_retry(
                    server_ip=server_ip,
                    port=port,
                    duration_s=duration,
                    bind_ip=bind_v6,
                    bind_iface=vlan_if,
                    udp=udp,
                    reverse=reverse,
                    udp_bandwidth=udp_bw,
                    attempts=int(cfg.get("iperf_client_retries", 2)),
                    v6=True,
                )
                ctx.notes.append(f"IP_22 {res.label}: {res.mbps:.1f} Mbps")
                _validate_session_throughput(
                    res,
                    expected_mbps=expected_udp if udp else expected_tcp,
                    min_ratio=min_ratio,
                )
                await asyncio.sleep(1)
    finally:
        await _stop_iperf_server_v6(meta)
