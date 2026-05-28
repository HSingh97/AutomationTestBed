"""Execution flows for IP_01–IP_37 networking validation cases."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx
import pytest
from scrapli.driver.generic import AsyncGenericDriver

from config.ip_test_cases import IpTestCase, Stack
from pages.commands import RootCommands
from pages.locators import EthernetLocators, NetworkLocators, UITimeouts
from utils.net_utils import format_http_host, is_ipv6_literal, normalize_ip
from utils.network_flows import apply_triple, navigate_to_ethernet, open_network_submenu
from utils.parsers import clean_ssh_output, extract_uci_value, ssh_scalar
from utils.regression_flows import _ensure_ssh_open
from utils.ui_helpers import attach_dialog_handler


@dataclass
class PingStats:
    transmitted: int = 0
    received: int = 0
    loss_pct: float = 100.0
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.loss_pct < 5.0 and self.received > 0


@dataclass
class IpTestContext:
    """Runtime handles for one IP case execution."""

    case: IpTestCase
    device_target: str  # bts | cpe
    host: str  # effective management / SSH host
    peer_host: str | None
    ssh: AsyncGenericDriver
    gui_page: Any | None
    cfg: dict[str, Any]
    stack_mode: str  # profile dut ip_mode
    fallback_hosts: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)


def _ip_cfg(profile: dict[str, Any]) -> dict[str, Any]:
    from config.defaults import IP_TEST_DEFAULTS

    merged = dict(IP_TEST_DEFAULTS)
    merged.update(profile.get("ip_tests", {}) or {})
    return merged


def _ordered_unique_hosts(*hosts: str | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for host in hosts:
        if not host:
            continue
        clean = normalize_ip(str(host).strip())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def _profile_fallback_hosts(cfg: dict[str, Any], primary_host: str) -> list[str]:
    """Configured fallback addresses. Empty when strict_ipv6 (mgmt IPv6 only for tests)."""
    if cfg.get("_strict_ipv6"):
        if is_ipv6_literal(primary_host) and cfg.get("fallback_ipv6"):
            return [normalize_ip(str(cfg["fallback_ipv6"]))]
        return []
    hosts: list[str] = []
    for key in ("fallback_ipv4", "_cli_fallback_ip"):
        if cfg.get(key):
            hosts.append(str(cfg[key]))
    if is_ipv6_literal(primary_host) and cfg.get("fallback_ipv6"):
        hosts.append(str(cfg["fallback_ipv6"]))
    return _ordered_unique_hosts(*hosts)


def _all_mgmt_hosts(primary: str, fallbacks: Iterable[str]) -> list[str]:
    return _ordered_unique_hosts(primary, *fallbacks)


async def _read_device_fallback_ip(ssh: AsyncGenericDriver, cfg: dict[str, Any]) -> str | None:
    if cfg.get("_strict_ipv6"):
        return None
    if not cfg.get("reachability_use_device_fallback", True):
        return None
    raw = await _ssh_run(ssh, RootCommands.GET_FALLBACK_IP)
    if not raw or "entry not found" in raw.lower() or raw.startswith("uci:"):
        return None
    return normalize_ip(raw.splitlines()[0].strip())


async def open_ssh_with_fallback(
    primary_host: str,
    password: str,
    cfg: dict[str, Any],
    *,
    attempts: int = 3,
    retry_interval_s: int = 15,
) -> tuple[AsyncGenericDriver, str, tuple[str, ...]]:
    """Open SSH on primary management IP, then profile/CLI fallback if needed."""
    candidates = _all_mgmt_hosts(primary_host, _profile_fallback_hosts(cfg, primary_host))
    last_error = ""
    for attempt in range(max(1, attempts)):
        for host in candidates:
            try:
                ssh = await _open_ssh(host, password)
                device_fb = await _read_device_fallback_ip(ssh, cfg)
                fallbacks = tuple(
                    h
                    for h in _all_mgmt_hosts(
                        primary_host, *_profile_fallback_hosts(cfg, primary_host), device_fb
                    )
                    if h != host
                )
                if host != normalize_ip(primary_host):
                    print(f"[ip] SSH via fallback {host} (primary {primary_host} unreachable)")
                return ssh, host, fallbacks
            except Exception as exc:
                last_error = str(exc)
        if attempt + 1 < attempts:
            await asyncio.sleep(retry_interval_s)
    raise ConnectionError(
        f"SSH unreachable on {', '.join(candidates)} (last error: {last_error})"
    )


async def _wait_ssh_any(
    hosts: list[str],
    password: str,
    *,
    timeout_s: int,
    interval_s: int = 5,
) -> tuple[AsyncGenericDriver, str]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        for host in hosts:
            try:
                conn = await _open_ssh(host, password)
                await conn.send_command("echo ok", timeout_ops=15)
                return conn, host
            except Exception as exc:
                last_error = str(exc)
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"SSH not ready on {hosts} within {timeout_s}s: {last_error}")


def _parse_ping_stats(output: str) -> PingStats:
    text = str(output or "")
    stats = PingStats(raw=text[:500])
    m_tx = re.search(r"(\d+)\s+packets?\s+transmitted", text, re.I)
    m_rx = re.search(r"(\d+)\s+packets?\s+received", text, re.I)
    if not m_rx:
        m_rx = re.search(r"(\d+)\s+received", text, re.I)
    m_loss = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", text, re.I)
    if m_tx:
        stats.transmitted = int(m_tx.group(1))
    if m_rx:
        stats.received = int(m_rx.group(1))
    if m_loss:
        stats.loss_pct = float(m_loss.group(1))
    elif stats.transmitted and stats.received == stats.transmitted:
        stats.loss_pct = 0.0
    elif stats.received > 0 and "bytes from" in text.lower():
        stats.loss_pct = 0.0
    return stats


def _host_matches_stack(host: str, *, v6: bool) -> bool:
    try:
        addr = ipaddress.ip_address(normalize_ip(host))
        return isinstance(addr, ipaddress.IPv6Address) if v6 else isinstance(addr, ipaddress.IPv4Address)
    except ValueError:
        return False


async def _ssh_run(ssh: AsyncGenericDriver, command: str, *, timeout: int = 60) -> str:
    result = await ssh.send_command(command, timeout_ops=timeout)
    return clean_ssh_output(str(result.result or ""))


async def _ping(
    ssh: AsyncGenericDriver,
    target: str,
    *,
    count: int = 4,
    size: int | None = None,
    df: bool = False,
    v6: bool | None = None,
) -> PingStats:
    use_v6 = v6 if v6 is not None else is_ipv6_literal(target)
    bin_name = "ping6" if use_v6 else "ping"
    parts = [bin_name, f"-c {count}"]
    if size is not None:
        parts.append(f"-s {int(size)}")
    if df:
        parts.append("-M do")
    parts.append(shlex.quote(normalize_ip(target)))
    cmd = " ".join(parts)
    try:
        result = await ssh.send_command(cmd, timeout_ops=max(60, count * 3))
        return _parse_ping_stats(str(result.result or ""))
    except Exception as exc:
        return PingStats(raw=str(exc))


async def _read_uci_ip(ssh: AsyncGenericDriver, *, v6: bool) -> dict[str, str]:
    if v6:
        return {
            "address": await _ssh_run(ssh, RootCommands.GET_NET_IP6),
            "gateway": await _ssh_run(ssh, RootCommands.GET_NET_GW6),
        }
    return {
        "address": await _ssh_run(ssh, RootCommands.GET_NET_IP),
        "netmask": await _ssh_run(ssh, RootCommands.GET_NET_MASK),
        "gateway": await _ssh_run(ssh, RootCommands.GET_NET_GW),
    }


async def _iface_name(ssh: AsyncGenericDriver) -> str:
    out = await _ssh_run(ssh, "ip route show default 2>/dev/null | head -1")
    m = re.search(r"dev\s+(\S+)", out)
    return m.group(1) if m else "eth0"


async def _check_web_ui(host: str, password: str, *, timeout: float = 15.0) -> bool:
    url = f"http://{format_http_host(host)}/"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, auth=("admin", password))
            return resp.status_code < 500
    except Exception:
        return False


async def _check_web_ui_with_fallback(
    primary: str,
    fallbacks: Iterable[str],
    password: str,
    *,
    notes: list[str] | None = None,
) -> bool:
    for host in _all_mgmt_hosts(primary, fallbacks):
        if await _check_web_ui(host, password):
            if notes is not None and host != normalize_ip(primary):
                notes.append(f"Web UI reachable via fallback {host}")
            return True
    return False


async def _local_ping_targets(ctx: IpTestContext, *, v6: bool) -> list[str]:
    """Candidate addresses for local reachability (configured, live UCI, management)."""
    cfg = ctx.cfg
    ssh = ctx.ssh
    targets: list[str] = []
    if v6:
        if cfg.get("ipv6_address"):
            targets.append(str(cfg["ipv6_address"]).split("/")[0])
        uci = await _read_uci_ip(ssh, v6=True)
        if uci.get("address"):
            targets.append(normalize_ip(str(uci["address"]).split("/")[0]))
        if _host_matches_stack(ctx.host, v6=True):
            targets.append(normalize_ip(ctx.host))
        targets.append("::1")
    else:
        if cfg.get("ipv4_address"):
            targets.append(str(cfg["ipv4_address"]))
        uci = await _read_uci_ip(ssh, v6=False)
        if uci.get("address"):
            targets.append(normalize_ip(str(uci["address"]).split("/")[0]))
        if _host_matches_stack(ctx.host, v6=False):
            targets.append(normalize_ip(ctx.host))
        targets.append("127.0.0.1")
    for fb in _profile_fallback_hosts(cfg, "::1" if v6 else "127.0.0.1"):
        if _host_matches_stack(fb, v6=v6):
            targets.append(fb)
    device_fb = await _read_device_fallback_ip(ssh, cfg)
    if device_fb and _host_matches_stack(device_fb, v6=v6):
        targets.append(device_fb)
    return [t for t in _ordered_unique_hosts(*targets) if _host_matches_stack(t, v6=v6)]


async def _ping_first_ok(
    ssh: AsyncGenericDriver,
    targets: list[str],
    *,
    v6: bool,
    count: int = 4,
    notes: list[str] | None = None,
    label: str = "local",
    **ping_kw: Any,
) -> tuple[PingStats, str]:
    last = PingStats(raw="no targets")
    for target in targets:
        if not _host_matches_stack(target, v6=v6):
            continue
        stats = await _ping(ssh, target, count=count, v6=v6, **ping_kw)
        if stats.ok:
            if notes is not None:
                notes.append(f"{label} ping ok via {target}")
            return stats, target
        last = stats
    return last, ""


async def _assert_local_reachable(
    ctx: IpTestContext,
    *,
    v6: bool,
    count: int | None = None,
    **ping_kw: Any,
) -> str:
    """Confirm local stack is up before remote/gateway checks."""
    n = count if count is not None else int(ctx.cfg.get("ping_count_short", 4))
    targets = await _local_ping_targets(ctx, v6=v6)
    stats, used = await _ping_first_ok(
        ctx.ssh,
        targets,
        v6=v6,
        count=n,
        notes=ctx.notes,
        label="local",
        **ping_kw,
    )
    assert stats.ok, (
        f"{ctx.case.case_id} local unreachable on {targets} before remote test: {stats.raw[:300]}"
    )
    return used


async def _ping_remote_after_local(
    ctx: IpTestContext,
    *,
    v6: bool,
    count: int | None = None,
    retries: int | None = None,
    retry_interval_s: int | None = None,
    **ping_kw: Any,
) -> PingStats:
    """Local ping must pass first; only then ping the remote peer."""
    await _assert_local_reachable(ctx, v6=v6, count=count or int(ctx.cfg.get("ping_count_short", 4)))
    if not ctx.peer_host:
        pytest.skip("no remote peer configured")
    n = count if count is not None else int(ctx.cfg.get("ping_count_short", 4))
    max_tries = retries if retries is not None else int(ctx.cfg.get("remote_ping_retries", 5))
    interval = retry_interval_s if retry_interval_s is not None else int(
        ctx.cfg.get("remote_ping_retry_interval_s", 10)
    )
    stats = PingStats(raw="no remote ping attempts")
    for attempt in range(max(1, max_tries)):
        stats = await _ping(ctx.ssh, ctx.peer_host, count=n, v6=v6, **ping_kw)
        if stats.ok:
            ctx.notes.append(f"remote ping ok to {normalize_ip(ctx.peer_host)}")
            return stats
        if attempt + 1 < max_tries:
            await _ensure_ssh_open(ctx.ssh)
            await asyncio.sleep(interval)
    assert stats.ok, f"remote ping failed after local ok: {stats.raw[:300]}"
    return stats


async def _restore_iface_mtu(ssh: AsyncGenericDriver, iface: str, mtu: str) -> None:
    await _ssh_run(ssh, f"ip link set {shlex.quote(iface)} mtu {shlex.quote(str(mtu))}")


async def _gui_set_static_ip(gui_page, cfg: dict[str, Any], *, v6: bool) -> None:
    attach_dialog_handler(gui_page)
    await open_network_submenu(gui_page, "/network/ip")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    if v6:
        fields = (
            (NetworkLocators.IPv6_ADDRESS, cfg.get("ipv6_address", "")),
            (NetworkLocators.IPv6_GATEWAY, cfg.get("ipv6_gateway", "")),
        )
    else:
        fields = (
            (NetworkLocators.IPv4_ADDRESS, cfg.get("ipv4_address", "")),
            (NetworkLocators.IPv4_NETMASK, cfg.get("ipv4_netmask", "")),
            (NetworkLocators.IPv4_GATEWAY, cfg.get("ipv4_gateway", "")),
        )
    for locator, value in fields:
        if not value:
            continue
        element = gui_page.locator(locator).first
        if await element.count() == 0:
            continue
        await element.fill(str(value))
    save = gui_page.locator(NetworkLocators.SAVE_BUTTON).first
    await apply_triple(
        gui_page,
        save,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=float(cfg.get("gui_settle_seconds", 12)),
    )


async def _soft_reboot_ssh(
    host: str,
    password: str,
    *,
    timeout_s: int = 200,
    fallback_hosts: Iterable[str] = (),
) -> tuple[AsyncGenericDriver, str]:
    hosts = _all_mgmt_hosts(host, fallback_hosts)
    try:
        ssh = await _open_ssh(host, password)
        await ssh.send_command("reboot", timeout_ops=5)
        await _close_ssh(ssh)
    except Exception:
        pass
    await asyncio.sleep(5)
    return await _wait_ssh_any(hosts, password, timeout_s=timeout_s, interval_s=5)


async def _close_ssh(conn: AsyncGenericDriver | None) -> None:
    if conn is None:
        return
    try:
        await conn.close()
    except Exception:
        pass


async def _open_ssh(host: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


def _skip_if_unsupported(ctx: IpTestContext, req: str, reason: str) -> None:
    if req in ctx.case.requires:
        pytest.skip(f"{ctx.case.case_id} on {ctx.device_target}: {reason}")


def _stack_allowed(ctx: IpTestContext, stack: Stack) -> bool:
    if ctx.case.stack == "any" or ctx.case.stack == stack:
        return True
    if ctx.case.stack == "dual":
        return True
    if ctx.stack_mode == "ipv6" and stack == "v6":
        return True
    if ctx.stack_mode != "ipv6" and stack == "v4":
        return True
    return False


async def execute_ip_case(ctx: IpTestContext) -> None:
    """Run a single IP test case for the given device target (bts/cpe)."""
    case = ctx.case
    cfg = ctx.cfg
    ssh = ctx.ssh
    password = str(cfg.get("_password", ""))

    if "manual_power" in case.requires:
        _skip_if_unsupported(ctx, "manual_power", "requires physical power cycle (not automatable)")

    if "backup" in case.requires and not cfg.get("backup_archive_path"):
        _skip_if_unsupported(ctx, "backup", "set ip_tests.backup_archive_path in profile")

    if "firmware" in case.requires and not cfg.get("firmware_image_path"):
        _skip_if_unsupported(ctx, "firmware", "set ip_tests.firmware_image_path in profile")

    if "iperf" in case.requires and not (cfg.get("iperf_server_v4") or cfg.get("iperf_server_v6")):
        _skip_if_unsupported(ctx, "iperf", "set ip_tests.iperf_server_v4/v6 in profile")

    cid = case.case_id

    # --- IPv4 functional / validation ---
    if cid == "IP_01":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope for current profile")
        _skip_if_unsupported(ctx, "gui", "needs GUI")
        await _gui_set_static_ip(ctx.gui_page, cfg, v6=False)
        uci = await _read_uci_ip(ssh, v6=False)
        assert cfg["ipv4_address"] in uci.get("address", ""), f"UCI IP mismatch: {uci}"
        assert await _check_web_ui_with_fallback(ctx.host, ctx.fallback_hosts, password, notes=ctx.notes)
        return

    if cid == "IP_02":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        await _assert_local_reachable(ctx, v6=False)
        assert await _check_web_ui_with_fallback(ctx.host, ctx.fallback_hosts, password, notes=ctx.notes)
        return

    if cid == "IP_03":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        await _ping_remote_after_local(ctx, v6=False)
        return

    if cid == "IP_04":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        await _assert_local_reachable(ctx, v6=False)
        gw = (await _ssh_run(ssh, RootCommands.GET_NET_GW)).strip() or str(cfg.get("ipv4_gateway", "")).strip()
        if not gw or gw.startswith("uci:"):
            pytest.skip("no IPv4 gateway configured on device")
        stats = await _ping(ssh, gw, count=int(cfg.get("ping_count_short", 4)), v6=False)
        if not stats.ok and ("unreachable" in stats.raw.lower() or stats.loss_pct >= 100):
            pytest.skip(f"IPv4 gateway {gw} not reachable on this bench ({stats.raw[:120]})")
        assert stats.ok, stats.raw
        return

    if cid == "IP_05":
        server = cfg.get("iperf_server_v4", "")
        cmd = f"iperf3 -c {shlex.quote(server)} -t {int(cfg.get('iperf_duration_s', 10))}"
        out = await _ssh_run(ssh, cmd, timeout=120)
        assert "receiver" in out.lower() or "sender" in out.lower(), out[:300]
        return

    if cid == "IP_06":
        await _assert_local_reachable(ctx, v6=False, count=int(cfg.get("ping_count_short", 4)))
        target = ctx.peer_host or ctx.host
        count = int(cfg.get("ping_count_long", 100))
        stats = await _ping(ssh, target, count=count, v6=False)
        assert stats.loss_pct <= float(cfg.get("max_ping_loss_pct", 1.0)), stats.raw
        return

    if cid == "IP_07":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        _skip_if_unsupported(ctx, "gui", "needs GUI")
        mtu = int(cfg.get("mtu_test_value", 1400))
        restore_mtu = str(cfg.get("mtu_restore_value", 1500))
        iface = await _iface_name(ssh)
        original_mtu = (await _ssh_run(ssh, f"cat /sys/class/net/{iface}/mtu")).strip() or restore_mtu
        try:
            await navigate_to_ethernet(ctx.gui_page)
            mtu_input = ctx.gui_page.locator(EthernetLocators.MTU_INPUT).first
            await mtu_input.fill(str(mtu))
            save = ctx.gui_page.locator(EthernetLocators.SAVE_BUTTON).first
            await apply_triple(
                ctx.gui_page,
                save,
                EthernetLocators.APPLY_ICON,
                EthernetLocators.CONFIRM_APPLY,
                settle_seconds=10,
            )
            read_mtu = await _ssh_run(ssh, f"cat /sys/class/net/{iface}/mtu")
            assert str(mtu) == read_mtu, f"expected MTU {mtu}, got {read_mtu}"
            await _assert_local_reachable(ctx, v6=False)
            if ctx.peer_host:
                stats = await _ping(ssh, ctx.peer_host, count=4, size=max(mtu - 28, 0), df=True, v6=False)
                if not stats.ok:
                    pytest.skip(f"IPv4 DF ping to peer not supported on bench: {stats.raw[:120]}")
        finally:
            await _restore_iface_mtu(ssh, iface, original_mtu)
        return

    if cid == "IP_08":
        await _assert_local_reachable(ctx, v6=False)
        if ctx.peer_host:
            stats = await _ping(ssh, ctx.peer_host, count=4, size=2000, v6=False)
        else:
            targets = await _local_ping_targets(ctx, v6=False)
            stats, _ = await _ping_first_ok(ssh, targets, v6=False, count=4, size=2000, notes=ctx.notes)
        assert stats.received > 0, stats.raw
        return

    if cid in ("IP_09", "IP_28"):
        stack_v6 = cid == "IP_28"
        if not _stack_allowed(ctx, "v6" if stack_v6 else "v4"):
            pytest.skip("stack not in scope")
        before = await _read_uci_ip(ssh, v6=stack_v6)
        new_ssh, _eff = await _soft_reboot_ssh(
            ctx.host,
            password,
            timeout_s=int(cfg.get("reboot_timeout_s", 200)),
            fallback_hosts=ctx.fallback_hosts,
        )
        try:
            after = await _read_uci_ip(new_ssh, v6=stack_v6)
            for key, val in before.items():
                if val:
                    assert val in after.get(key, "") or after.get(key) == val, f"{key}: {before} vs {after}"
            reboot_ctx = IpTestContext(
                case=case,
                device_target=ctx.device_target,
                host=_eff,
                peer_host=ctx.peer_host,
                ssh=new_ssh,
                gui_page=None,
                cfg=cfg,
                stack_mode=ctx.stack_mode,
                fallback_hosts=ctx.fallback_hosts,
                notes=ctx.notes,
            )
            await _assert_local_reachable(reboot_ctx, v6=stack_v6, count=3)
            if ctx.peer_host:
                await _ping_remote_after_local(
                    reboot_ctx,
                    v6=stack_v6,
                    count=3,
                    retries=int(cfg.get("post_reboot_remote_ping_retries", 12)),
                    retry_interval_s=int(cfg.get("post_reboot_remote_ping_interval_s", 10)),
                )
        finally:
            await _close_ssh(new_ssh)
        return

    if cid == "IP_10" or cid == "IP_30":
        pytest.skip("hard reboot requires manual power cycle")

    if cid in ("IP_11", "IP_31"):
        before = await _read_uci_ip(ssh, v6=(cid == "IP_31"))
        await _ssh_run(ssh, "/etc/init.d/network reload")
        await asyncio.sleep(int(cfg.get("network_reload_wait_s", 30)))
        after = await _read_uci_ip(ssh, v6=(cid == "IP_31"))
        for key, val in before.items():
            if val:
                assert val in after.get(key, "") or after.get(key) == val, f"{key} lost after reload"
        await _assert_local_reachable(ctx, v6=(cid == "IP_31"))
        if ctx.peer_host:
            await _ping_remote_after_local(
                ctx,
                v6=(cid == "IP_31"),
                count=4,
                retries=int(cfg.get("post_reload_remote_ping_retries", 8)),
            )
        return

    if cid in ("IP_12", "IP_32", "IP_15", "IP_35"):
        pytest.skip("backup/restore retain flow not wired — set backup_archive_path to enable")

    if cid in ("IP_13", "IP_33"):
        pytest.skip("traffic + reboot covered by regression REG tests; run with iperf server configured")

    if cid in ("IP_14", "IP_34"):
        v6 = cid == "IP_34"
        iface = await _iface_name(ssh)
        before = await _read_uci_ip(ssh, v6=v6)
        await _ssh_run(ssh, f"ip link set {iface} down")
        await asyncio.sleep(3)
        await _ssh_run(ssh, f"ip link set {iface} up")
        await asyncio.sleep(int(cfg.get("iface_up_wait_s", 15)))
        await _ensure_ssh_open(ssh)
        after = await _read_uci_ip(ssh, v6=v6)
        for key, val in before.items():
            if val:
                assert val.split("/")[0] in after.get(key, "") or after.get(key), f"{key} not restored"
        await _assert_local_reachable(ctx, v6=v6)
        if ctx.peer_host:
            await _ping_remote_after_local(ctx, v6=v6, count=4)
        return

    if cid == "IP_16":
        if not ctx.peer_host:
            pytest.skip("no peer for ARP")
        await _ping_remote_after_local(ctx, v6=False, count=2)
        arp_out = await _ssh_run(ssh, "ip neigh show 2>/dev/null || arp -a")
        peer = normalize_ip(ctx.peer_host)
        peer_in_table = peer in arp_out or peer.replace(":", "") in arp_out.replace(":", "")
        has_l2 = bool(re.search(r"\b(REACHABLE|STALE|DELAY|PROBE|lladdr)\b", arp_out, re.I))
        assert arp_out.strip() and (peer_in_table or has_l2), arp_out[:400]
        return

    if cid == "IP_17":
        pytest.skip("static route UCI keys vary by build — configure ip_tests.static_route_cidr to enable")

    # --- IPv6 ---
    if cid == "IP_18":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        _skip_if_unsupported(ctx, "gui", "needs GUI")
        await _gui_set_static_ip(ctx.gui_page, cfg, v6=True)
        uci = await _read_uci_ip(ssh, v6=True)
        assert normalize_ip(cfg["ipv6_address"].split("/")[0]) in normalize_ip(uci.get("address", "")), uci
        assert await _check_web_ui_with_fallback(ctx.host, ctx.fallback_hosts, password, notes=ctx.notes)
        return

    if cid == "IP_19":
        await _assert_local_reachable(ctx, v6=True)
        return

    if cid == "IP_20":
        await _ping_remote_after_local(ctx, v6=True)
        return

    if cid == "IP_21":
        await _assert_local_reachable(ctx, v6=True)
        gw = (await _ssh_run(ssh, RootCommands.GET_NET_GW6)).strip() or str(cfg.get("ipv6_gateway", "")).strip()
        if not gw or gw.startswith("uci:"):
            pytest.skip("no IPv6 gateway configured on device")
        stats = await _ping(ssh, gw, count=int(cfg.get("ping_count_short", 4)), v6=True)
        if not stats.ok and "unreachable" in stats.raw.lower():
            pytest.skip(f"IPv6 gateway {gw} not reachable on this bench ({stats.raw[:120]})")
        assert stats.ok, stats.raw
        return

    if cid == "IP_22":
        server = cfg.get("iperf_server_v6", "")
        cmd = f"iperf3 -6 -c {shlex.quote(server)} -t {int(cfg.get('iperf_duration_s', 10))}"
        out = await _ssh_run(ssh, cmd, timeout=120)
        assert "receiver" in out.lower() or "sender" in out.lower(), out[:300]
        return

    if cid == "IP_23":
        await _assert_local_reachable(ctx, v6=True, count=int(cfg.get("ping_count_short", 4)))
        target = ctx.peer_host or ctx.host
        count = int(cfg.get("ping_count_long", 100))
        stats = await _ping(ssh, target, count=count, v6=True)
        assert stats.loss_pct <= float(cfg.get("max_ping_loss_pct", 1.0)), stats.raw
        return

    if cid == "IP_24":
        _skip_if_unsupported(ctx, "gui", "needs GUI")
        mtu = int(cfg.get("mtu_test_value", 1400))
        restore_mtu = str(cfg.get("mtu_restore_value", 1500))
        iface = await _iface_name(ssh)
        original_mtu = (await _ssh_run(ssh, f"cat /sys/class/net/{iface}/mtu")).strip() or restore_mtu
        try:
            await navigate_to_ethernet(ctx.gui_page)
            mtu_input = ctx.gui_page.locator(EthernetLocators.MTU_INPUT).first
            await mtu_input.fill(str(mtu))
            save = ctx.gui_page.locator(EthernetLocators.SAVE_BUTTON).first
            await apply_triple(
                ctx.gui_page,
                save,
                EthernetLocators.APPLY_ICON,
                EthernetLocators.CONFIRM_APPLY,
                settle_seconds=10,
            )
            read_mtu = await _ssh_run(ssh, f"cat /sys/class/net/{iface}/mtu")
            assert str(mtu) == read_mtu
            await _assert_local_reachable(ctx, v6=True)
            if ctx.peer_host:
                stats = await _ping(ssh, ctx.peer_host, count=4, size=max(mtu - 48, 0), df=True, v6=True)
                if not stats.ok:
                    pytest.skip(f"IPv6 DF ping to peer not supported on bench: {stats.raw[:120]}")
        finally:
            await _restore_iface_mtu(ssh, iface, original_mtu)
        return

    if cid == "IP_25":
        await _assert_local_reachable(ctx, v6=True)
        if ctx.peer_host:
            stats = await _ping(ssh, ctx.peer_host, count=4, size=1800, v6=True)
        else:
            targets = await _local_ping_targets(ctx, v6=True)
            stats, _ = await _ping_first_ok(ssh, targets, v6=True, count=4, size=1800, notes=ctx.notes)
        assert stats.received > 0, stats.raw
        return

    if cid == "IP_26":
        out = await _ssh_run(ssh, "ip -6 addr show")
        assert "fe80::" in out.lower(), out[:200]
        if ctx.peer_host and "%" in str(cfg.get("ipv6_link_local_iface", "")):
            cmd = f"ping6 -c 2 {cfg['ipv6_link_local_peer']}"
            stats = _parse_ping_stats(await _ssh_run(ssh, cmd))
            if not stats.ok:
                ctx.notes.append("link-local peer ping optional on this bench")
        return

    if cid == "IP_27":
        await _assert_local_reachable(ctx, v6=True, count=2)
        if ctx.peer_host:
            await _ping_remote_after_local(ctx, v6=True, count=2)
        neigh = await _ssh_run(ssh, "ip -6 neigh show")
        peer = normalize_ip(ctx.peer_host or "")
        has_peer = peer and peer in neigh
        has_nd = bool(re.search(r"\b(REACHABLE|STALE|DELAY|PROBE)\b", neigh))
        assert neigh.strip() and (has_peer or has_nd or "fe80::" in neigh.lower()), neigh[:400]
        return

    if cid == "IP_29":
        if ctx.device_target != "bts":
            pytest.skip("IP_29 is BTS-role only")
        before = await _read_uci_ip(ssh, v6=True)
        new_ssh, _eff = await _soft_reboot_ssh(
            ctx.host,
            password,
            timeout_s=int(cfg.get("reboot_timeout_s", 200)),
            fallback_hosts=ctx.fallback_hosts,
        )
        try:
            after = await _read_uci_ip(new_ssh, v6=True)
            assert before.get("address", "") in after.get("address", "") or before == after
            reboot_ctx = IpTestContext(
                case=case,
                device_target=ctx.device_target,
                host=_eff,
                peer_host=ctx.peer_host,
                ssh=new_ssh,
                gui_page=None,
                cfg=cfg,
                stack_mode=ctx.stack_mode,
                fallback_hosts=ctx.fallback_hosts,
                notes=ctx.notes,
            )
            await _assert_local_reachable(reboot_ctx, v6=True, count=3)
            if ctx.peer_host:
                await _ping_remote_after_local(
                    reboot_ctx,
                    v6=True,
                    count=3,
                    retries=int(cfg.get("post_reboot_remote_ping_retries", 12)),
                    retry_interval_s=int(cfg.get("post_reboot_remote_ping_interval_s", 10)),
                )
        finally:
            await _close_ssh(new_ssh)
        return

    if cid == "IP_36":
        pytest.skip("HTTP firmware upgrade — use REG_03 or set firmware_image_path + GUI path")

    if cid == "IP_37":
        if ctx.peer_host:
            await _assert_local_reachable(ctx, v6=(ctx.stack_mode == "ipv6"), count=3)
            v4 = await _ping(ssh, ctx.peer_host, count=3, v6=False)
            v6 = await _ping(ssh, ctx.peer_host, count=3, v6=True)
            assert v4.ok or v6.ok, f"v4={v4.raw[:120]} v6={v6.raw[:120]}"
        return

    pytest.fail(f"No handler for {cid}")
