"""Sanity-only RF link health check (no link formation / VLAN bootstrap)."""

from __future__ import annotations

import asyncio
import shlex
import time
from typing import Any

import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.sanity_ssh import ensure_sanity_ssh_open, sanity_ssh_run, sanitize_sanity_scalar


def _link_config(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("link", {}) or {}


async def sanity_link_health_bts(ssh: AsyncGenericDriver, profile: dict[str, Any]) -> tuple[bool, int]:
    """Return (link_up, connected_station_count) from BTS ath interface."""
    link = _link_config(profile)
    min_clients = int(link.get("min_connected_clients", 1))
    radio_idx = int(link.get("radio_idx", 1))
    ath = f"ath{radio_idx}"
    out = await ssh.send_command(
        f"wlanconfig {ath} list 2>/dev/null | grep -cE '^[0-9a-f][0-9a-f]:' || echo 0",
        timeout_ops=25,
    )
    try:
        count = int(str(out.result or "0").strip().split()[0])
    except (ValueError, IndexError):
        count = 0
    if count < min_clients:
        out2 = await ssh.send_command(
            f"iw dev {ath} station dump 2>/dev/null | grep -c '^Station' || echo 0",
            timeout_ops=25,
        )
        try:
            count = int(str(out2.result or "0").strip().split()[0])
        except (ValueError, IndexError):
            count = 0
    return count >= min_clients, count


async def wait_sanity_rf_link(
    bts_ssh: AsyncGenericDriver,
    profile: dict[str, Any],
    *,
    case_id: str,
    label: str,
    timeout_s: int,
    poll_s: int,
    required_stable: int = 3,
    recovery_cb=None,
    recovery_every_s: int = 60,
    fail_on_timeout: bool = True,
) -> bool:
    """
    Wait for BTS station count ≥ min_connected_clients.

    recovery_cb: optional async callable invoked periodically while link is down
    (e.g. re-push CPE SSID/key via hop after channel/ACS changes).

    Returns True when link is stable. On timeout: records pytest_check failure
    when fail_on_timeout=True, then returns False either way.
    """
    print(
        f"[sanity] {case_id} [{label}] waiting BTS↔CPE RF link (timeout={timeout_s}s)",
        flush=True,
    )
    deadline = time.monotonic() + timeout_s
    last_detail = ""
    attempt = 0
    stable_count = 0
    last_recovery = 0.0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            await ensure_sanity_ssh_open(bts_ssh)
            ok, stations = await sanity_link_health_bts(bts_ssh, profile)
            last_detail = f"wlan_stations={stations}"
            if ok:
                stable_count += 1
                print(
                    f"[sanity] {case_id} [{label}] RF link check PASS "
                    f"({last_detail}, stable {stable_count}/{required_stable})",
                    flush=True,
                )
                if stable_count >= required_stable:
                    print(
                        f"[sanity] {case_id} [{label}] RF link stable ({last_detail})",
                        flush=True,
                    )
                    return True
            else:
                stable_count = 0
                now = time.monotonic()
                if (
                    recovery_cb is not None
                    and (now - last_recovery) >= max(15, recovery_every_s)
                ):
                    last_recovery = now
                    try:
                        print(
                            f"[sanity] {case_id} [{label}] mid-wait RF recovery "
                            f"({last_detail})",
                            flush=True,
                        )
                        await recovery_cb()
                    except Exception as rec_exc:
                        print(
                            f"[sanity] {case_id} [{label}] recovery note: {rec_exc}",
                            flush=True,
                        )
        except Exception as exc:
            last_detail = str(exc)
            stable_count = 0
        if attempt == 1 or attempt % 6 == 0:
            print(
                f"[sanity] {case_id} [{label}] RF link pending ({last_detail}, "
                f"{int(deadline - time.monotonic())}s left)",
                flush=True,
            )
        await asyncio.sleep(max(1, poll_s))
    if fail_on_timeout:
        check.is_true(
            False,
            f"{case_id} [{label}]: RF link not up after upgrade ({last_detail})",
        )
    else:
        print(
            f"[sanity] {case_id} [{label}] RF link soft-timeout ({last_detail})",
            flush=True,
        )
    return False


async def _ping_once(host: str, *, timeout_s: int = 3) -> bool:
    host = normalize_ip(host)
    binary = "ping6" if is_ipv6_literal(host) else "ping"
    process = await asyncio.create_subprocess_exec(
        binary,
        "-c",
        "1",
        "-W",
        str(max(1, timeout_s)),
        host,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await process.wait() == 0


def _ping_output_ok(raw: str) -> bool:
    text = (raw or "").lower()
    if not text.strip():
        return False
    if " 0% packet loss" in text or "0 packets lost" in text:
        return True
    if "bytes from" in text and "100% packet loss" not in text:
        return True
    return "1 packets received" in text and " 0 packets received" not in text


async def _ping_once_via_ssh(
    ssh: AsyncGenericDriver,
    host: str,
    *,
    timeout_s: int = 8,
    count: int = 2,
) -> bool:
    """Ping a host from an on-device SSH session (BTS/CPE shell)."""
    host = normalize_ip(host)
    if not host:
        return False
    await ensure_sanity_ssh_open(ssh)
    wait_s = max(1, timeout_s)
    quoted = shlex.quote(host)
    if is_ipv6_literal(host):
        cmd = f"ping -6 -c {count} -W {wait_s} {quoted} 2>&1"
    else:
        cmd = f"ping -c {count} -W {wait_s} {quoted} 2>&1"
    try:
        response = await ssh.send_command(cmd, timeout_ops=wait_s + count * 5 + 15)
        raw = response.result or ""
    except Exception:
        return False
    return _ping_output_ok(raw)


async def _device_lan_ping_targets(ssh: AsyncGenericDriver) -> list[str]:
    """Return configured LAN IPv6/IPv4 addresses for peer ping targets."""
    hosts: list[str] = []
    for cmd in ("uci -q get network.lan.ip6addr", "uci -q get network.lan.ipaddr"):
        try:
            response = await ssh.send_command(cmd, timeout_ops=20)
            value = sanitize_sanity_scalar(response.result or "")
        except Exception:
            value = ""
        host = normalize_ip(str(value or "").split("/")[0])
        if host and host not in hosts:
            hosts.append(host)
    return hosts


async def wait_sanity_bts_peer_ping_stable(
    bts_ssh: AsyncGenericDriver,
    *,
    peer_label: str,
    peer_hosts: list[str],
    case_id: str,
    label: str,
    timeout_s: int,
    poll_s: int,
    consecutive_passes: int = 2,
) -> None:
    """
    Require consecutive successful pings from BTS to CPE (or other peer).

    Used after SANITY_03 restore when the lab PC may not yet have restored-mgmt
    IPv6 routes even though the RF link is already up.
    """
    targets = [normalize_ip(h) for h in peer_hosts if normalize_ip(h)]
    check.is_true(targets, f"{case_id} [{label}]: no peer ping targets configured")
    deadline = time.monotonic() + timeout_s
    streak = 0
    attempt = 0
    last_detail = ""
    print(
        f"[sanity] {case_id} [{label}] verifying BTS→{peer_label} ping "
        f"({consecutive_passes} consecutive passes, targets={', '.join(targets)})",
        flush=True,
    )
    while time.monotonic() < deadline:
        attempt += 1
        ok = False
        hit = ""
        for host in targets:
            if await _ping_once_via_ssh(bts_ssh, host, timeout_s=3):
                ok = True
                hit = host
                break
        last_detail = f"{peer_label}={'PASS' if ok else 'FAIL'}"
        if hit:
            last_detail += f" via {hit}"
        if ok:
            streak += 1
            print(
                f"[sanity] BTS→peer ping check {attempt}: {last_detail} "
                f"(stable {streak}/{consecutive_passes})",
                flush=True,
            )
            if streak >= consecutive_passes:
                return
        else:
            streak = 0
            print(f"[sanity] BTS→peer ping check {attempt}: {last_detail}", flush=True)
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(
        f"[sanity] {case_id} [{label}]: BTS could not ping {peer_label} "
        f"({last_detail}, targets={', '.join(targets)})"
    )


async def wait_sanity_ping_stable(
    hosts: dict[str, str],
    *,
    timeout_s: int,
    poll_s: int,
    consecutive_passes: int = 3,
) -> None:
    """Require consecutive successful pings to every post-upgrade endpoint."""
    deadline = time.monotonic() + timeout_s
    streak = 0
    attempt = 0
    last: dict[str, bool] = {}
    print(
        f"[sanity] Verifying post-upgrade ping stability "
        f"({consecutive_passes} consecutive passes required)",
        flush=True,
    )
    while time.monotonic() < deadline:
        attempt += 1
        results = await asyncio.gather(
            *(_ping_once(host) for host in hosts.values())
        )
        last = dict(zip(hosts.keys(), results))
        detail = ", ".join(
            f"{label}={'PASS' if ok else 'FAIL'}" for label, ok in last.items()
        )
        if all(results):
            streak += 1
            print(
                f"[sanity] Ping check {attempt}: {detail} "
                f"(stable {streak}/{consecutive_passes})",
                flush=True,
            )
            if streak >= consecutive_passes:
                return
        else:
            streak = 0
            print(f"[sanity] Ping check {attempt}: {detail}", flush=True)
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(
        "[sanity] Post-upgrade ping did not become stable: "
        + ", ".join(
            f"{label}={'PASS' if ok else 'FAIL'}" for label, ok in last.items()
        )
    )


async def _ping_peer_from_ssh(
    ssh: AsyncGenericDriver,
    targets: list[str],
    *,
    attempts: int = 3,
    timeout_s: int = 8,
) -> tuple[bool, str]:
    """Try ping from device SSH to first reachable target in ``targets``."""
    hosts = [normalize_ip(h) for h in targets if normalize_ip(h)]
    if not hosts:
        return False, "(no targets)"
    for host in hosts:
        for _ in range(max(1, attempts)):
            if await _ping_once_via_ssh(ssh, host, timeout_s=timeout_s):
                return True, host
    return False, hosts[0]


async def verify_sanity_bidirectional_device_ping(
    bts_ssh: AsyncGenericDriver,
    cpe_ssh: AsyncGenericDriver,
    *,
    bts_to_cpe_targets: list[str],
    cpe_to_bts_targets: list[str],
    case_id: str = "SANITY_49",
    attempts: int = 3,
) -> tuple[bool, bool, str, str]:
    """
    Ping CPE from BTS SSH and BTS from CPE SSH.

    Returns (bts_to_cpe_ok, cpe_to_bts_ok, bts_hit, cpe_hit).
    """
    print(f"[sanity] {case_id}: BTS→CPE ping via SSH (targets={bts_to_cpe_targets})", flush=True)
    bts_ok, bts_hit = await _ping_peer_from_ssh(
        bts_ssh, bts_to_cpe_targets, attempts=attempts
    )
    print(
        f"[sanity] {case_id}: BTS→CPE {'PASS' if bts_ok else 'FAIL'}"
        + (f" ({bts_hit})" if bts_ok else ""),
        flush=True,
    )
    print(f"[sanity] {case_id}: CPE→BTS ping via SSH (targets={cpe_to_bts_targets})", flush=True)
    cpe_ok, cpe_hit = await _ping_peer_from_ssh(
        cpe_ssh, cpe_to_bts_targets, attempts=attempts
    )
    print(
        f"[sanity] {case_id}: CPE→BTS {'PASS' if cpe_ok else 'FAIL'}"
        + (f" ({cpe_hit})" if cpe_ok else ""),
        flush=True,
    )
    return bts_ok, cpe_ok, bts_hit, cpe_hit
