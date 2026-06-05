"""Execution flows for IP_01–IP_60 networking validation cases."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shlex
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx
import pytest
from scrapli.driver.generic import AsyncGenericDriver
from scrapli.exceptions import ScrapliTimeout

from config.ip_test_cases import PLANNED_IP_CASE_IDS, IpTestCase, Stack
from pages.commands import RootCommands
from pages.locators import EthernetLocators, NetworkLocators, UITimeouts
from utils.net_utils import format_http_host, is_ipv6_literal, normalize_ip
from utils.network_flows import apply_triple, navigate_to_ethernet, open_network_submenu
from utils.parsers import clean_ssh_output, extract_uci_value, is_uci_error, ssh_scalar
from utils.lab_pc_net import (
    ensure_lab_pc_mgmt_vlan_ipv4,
    ensure_secondary_pc_cpe_hop_ready,
    read_local_iface_mtu,
    restore_lab_pc_mgmt_vlan_ipv4,
    set_lab_pc_iface_link_state,
    set_lab_pc_iface_mtu,
)
from utils.regression_flows import _ensure_ssh_open, _navigate_to_flashops, _open_upgrade_tab
from utils.ui_helpers import attach_dialog_handler
from utils.vlan_uci import build_cpe_mgmtvlan_only_commands


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


def _ipv6_prefix_len(cfg: dict[str, Any]) -> int:
    profile = cfg.get("_profile") or {}
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    return int(cfg.get("ipv6_prefix_len", mgmt.get("prefix_len", 120)))


def ipv6_equal(addr_a: str, addr_b: str) -> bool:
    """True when two IPv6 literals are the same address (any :: compression)."""
    try:
        a = ipaddress.IPv6Address(normalize_ip(str(addr_a).split("/")[0]))
        b = ipaddress.IPv6Address(normalize_ip(str(addr_b).split("/")[0]))
        return a == b
    except ValueError:
        return normalize_ip(str(addr_a)) == normalize_ip(str(addr_b))


def ipv6_addr_in_text(expected: str, text: str) -> bool:
    """True if expected IPv6 appears in command output (handles :: compression)."""
    try:
        want = ipaddress.IPv6Address(normalize_ip(str(expected).split("/")[0]))
    except ValueError:
        return str(expected).lower() in (text or "").lower()
    if not text:
        return False
    for chunk in re.split(r"[\s,;]+", str(text)):
        chunk = chunk.strip()
        if ":" not in chunk:
            continue
        try:
            if ipaddress.IPv6Address(chunk.split("/")[0]) == want:
                return True
        except ValueError:
            continue
    for line in text.splitlines():
        if "inet6" not in line.lower():
            continue
        m = re.search(r"inet6\s+([0-9a-f:]+)/\d+", line, re.I)
        if not m:
            continue
        try:
            if ipaddress.IPv6Address(m.group(1)) == want:
                return True
        except ValueError:
            continue
    return False


def format_ipv6_cidr(address: str, cfg: dict[str, Any]) -> str:
    """Ensure IPv6 LAN/mgmt addresses use the profile prefix (default /120)."""
    raw = str(address).strip()
    if not raw:
        return raw
    if "/" in raw:
        host, plen = raw.split("/", 1)
        return f"{normalize_ip(host)}/{int(plen)}"
    return f"{normalize_ip(raw.split('/')[0])}/{_ipv6_prefix_len(cfg)}"


def _ipv6_values_for_role(
    cfg: dict[str, Any],
    profile: dict[str, Any],
    *,
    device_target: str,
) -> dict[str, str]:
    dut = profile.get("dut", {}) or {}
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    if device_target == "cpe":
        addr_raw = (
            str(cfg.get("ipv6_address_cpe") or cfg.get("ipv6_address") or "").strip()
            or str(mgmt.get("ipv6_cpe") or (dut.get("remote_ipv6s") or [""])[0]).strip()
        )
        gw = str(cfg.get("ipv6_gateway_cpe") or cfg.get("ipv6_gateway") or "").strip()
    else:
        addr_raw = (
            str(cfg.get("ipv6_address_bts") or cfg.get("ipv6_address") or "").strip()
            or str(mgmt.get("ipv6_bts") or dut.get("local_ipv6", "")).strip()
        )
        gw = str(cfg.get("ipv6_gateway_bts") or cfg.get("ipv6_gateway") or "").strip()
    return {
        "ipv6_address": format_ipv6_cidr(addr_raw, cfg),
        "ipv6_gateway": normalize_ip(gw.split("/")[0]) if gw else "",
    }


def _ipv6_values_for_target(ctx: IpTestContext) -> dict[str, str]:
    profile = ctx.cfg.get("_profile") or {}
    return _ipv6_values_for_role(ctx.cfg, profile, device_target=ctx.device_target)


def _canonical_ipv6(addr: str) -> str:
    """Address form the kernel accepts on ``ping6 -I`` (matches assigned iface addr)."""
    try:
        return str(ipaddress.IPv6Address(normalize_ip(str(addr).split("/")[0])))
    except ValueError:
        return normalize_ip(str(addr).split("/")[0])


def _lab_ping_bind_ipv6(cfg: dict[str, Any], profile: dict[str, Any]) -> str:
    bound = str(cfg.get("_lab_ping_bind_ipv6") or "").strip()
    if bound:
        return _canonical_ipv6(bound)
    dut = profile.get("dut", {}) or {}
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    raw = str(
        dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or dut.get("mgmt_oob_ipv6") or ""
    ).strip()
    return _canonical_ipv6(raw) if raw else ""


def resolve_ip_peer_host(
    *,
    device_target: str,
    bsu_ip: str,
    cpe_ips: list[str],
    profile: dict[str, Any],
    cfg: dict[str, Any],
) -> str | None:
    """Remote peer for BTS-side ping/iperf. Final IPv4 is resolved dynamically in ensure_cpe_ipv4_ready."""
    dut = profile.get("dut", {}) or {}
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        if device_target == "bts":
            if cpe_ips:
                return normalize_ip(str(cpe_ips[0]).split("/")[0])
            mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
            cpe_v6 = str(mgmt.get("ipv6_cpe", "")).strip()
            if cpe_v6:
                return normalize_ip(cpe_v6.split("/")[0])
            for ip in dut.get("remote_ipv6s") or []:
                clean = normalize_ip(str(ip).split("/")[0])
                if clean:
                    return clean
        if device_target == "cpe":
            return normalize_ip(str(bsu_ip).split("/")[0]) if bsu_ip else None
        return None
    if device_target != "bts":
        return bsu_ip or None
    if cfg.get("use_cpe_peer", False) and cpe_ips:
        hint = normalize_ip(str(cpe_ips[0]))
        if hint:
            return hint
    explicit = str(cfg.get("remote_ping_host", "")).strip()
    if explicit:
        return normalize_ip(explicit)
    dut = profile.get("dut", {}) or {}
    for ip in dut.get("remote_ips") or []:
        clean = normalize_ip(str(ip).split("/")[0])
        if clean and clean not in ("10.0.0.1", "127.0.0.1"):
            return clean
    return None


def _cpe_ipv4_hint_candidates(cfg: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    """Optional profile/CLI hints — used only when live discovery needs a configure fallback."""
    from utils.cpe_discovery import is_valid_cpe_lan_ipv4

    hints: list[str] = []
    for key in ("cpe_ipv4_candidates", "remote_ping_host", "cpe_ipv4_default_address"):
        raw = str(cfg.get(key, "")).strip()
        if not raw:
            continue
        items = [x.strip() for x in raw.split(",") if x.strip()] if "," in raw else [raw]
        for item in items:
            clean = normalize_ip(item.split("/")[0])
            if clean and is_valid_cpe_lan_ipv4(clean) and clean not in hints:
                hints.append(clean)
    tb = profile.get("testbed", {}) or {}
    factory = normalize_ip(str(tb.get("secondary_pc", {}).get("cpe_factory_ipv4", "10.0.0.1")))
    for ip in profile.get("dut", {}).get("remote_ips") or []:
        clean = normalize_ip(str(ip).split("/")[0])
        if clean and is_valid_cpe_lan_ipv4(clean, factory_ipv4=factory) and clean not in hints:
            hints.append(clean)
    return hints


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
    """Configured fallback addresses. strict_ipv6 still allows IPv4 factory SSH when enabled."""
    if cfg.get("_strict_ipv6"):
        hosts: list[str] = []
        if cfg.get("ssh_allow_ipv4_fallback", True):
            for key in ("fallback_ipv4", "_cli_fallback_ip"):
                if cfg.get(key):
                    hosts.append(normalize_ip(str(cfg[key]).split("/")[0]))
            profile = cfg.get("_profile") or {}
            rec = (profile.get("testbed", {}) or {}).get("recovery", {}) or {}
            if rec.get("bts_fallback_ipv4"):
                hosts.append(normalize_ip(str(rec["bts_fallback_ipv4"])))
            if "10.0.0.1" not in hosts:
                hosts.append("10.0.0.1")
        if is_ipv6_literal(primary_host) and cfg.get("fallback_ipv6"):
            hosts.append(normalize_ip(str(cfg["fallback_ipv6"])))
        return _ordered_unique_hosts(*hosts)
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
                merged_fallbacks = _ordered_unique_hosts(
                    *_profile_fallback_hosts(cfg, primary_host),
                    device_fb,
                )
                fallbacks = tuple(
                    h for h in _all_mgmt_hosts(primary_host, merged_fallbacks) if h != host
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
    if stats.received == 0:
        replies = len(re.findall(r"bytes from", text, re.I))
        if replies:
            stats.received = replies
            if stats.transmitted:
                stats.loss_pct = max(
                    0.0, 100.0 * (1.0 - replies / max(1, stats.transmitted))
                )
            else:
                stats.loss_pct = 0.0
    return stats


def _host_matches_stack(host: str, *, v6: bool) -> bool:
    try:
        addr = ipaddress.ip_address(normalize_ip(host))
        return isinstance(addr, ipaddress.IPv6Address) if v6 else isinstance(addr, ipaddress.IPv4Address)
    except ValueError:
        return False


async def _ssh_run_raw(ssh: AsyncGenericDriver, command: str, *, timeout: int = 60) -> str:
    """Full command output (ifconfig, ip addr, ping, etc.)."""
    result = await ssh.send_command(command, timeout_ops=timeout)
    return str(result.result or "").replace("\r", "")


async def _ssh_run(ssh: AsyncGenericDriver, command: str, *, timeout: int = 60) -> str:
    result = await ssh.send_command(command, timeout_ops=timeout)
    return clean_ssh_output(str(result.result or ""))


def _ucidyn_set_line(key: str, value: str) -> str:
    """Shell line: ucidyn set <uci-key> <value> (in-house apply path, same as LuCI)."""
    return f"ucidyn set {key} {shlex.quote(str(value).strip())}"


async def _ucidyn_set(
    ssh: AsyncGenericDriver, key: str, value: str, *, timeout: int = 60
) -> None:
    await _ssh_run(ssh, _ucidyn_set_line(key, value), timeout=timeout)


async def _ucidyn_apply(ssh: AsyncGenericDriver, *, timeout: int = 120) -> None:
    await _ssh_run(ssh, "ucidyn apply", timeout=timeout)


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


def _uci_scalar_clean(raw: str, *, ipv4: bool = False, ipv6: bool = False) -> str:
    from utils.parsers import pick_scalar_ipv4, pick_scalar_ipv6

    if ipv6:
        val = pick_scalar_ipv6(raw)
    elif ipv4:
        val = pick_scalar_ipv4(raw)
    else:
        val = ssh_scalar(raw)
    return "" if is_uci_error(val) else val


async def _ssh_drain_banner(ssh: AsyncGenericDriver) -> None:
    """Clear MOTD/prompt noise on a fresh SSH session before UCI reads."""
    await _ssh_run(ssh, "true", timeout=15)


async def _read_uci_ip(ssh: AsyncGenericDriver, *, v6: bool) -> dict[str, str]:
    await _ssh_drain_banner(ssh)
    if v6:
        return {
            "address": _uci_scalar_clean(
                await _ssh_run(ssh, RootCommands.GET_NET_IP6), ipv6=True
            ),
            "gateway": _uci_scalar_clean(
                await _ssh_run(ssh, RootCommands.GET_NET_GW6), ipv6=True
            ),
        }
    return {
        "address": _uci_scalar_clean(
            await _ssh_run(ssh, RootCommands.GET_NET_IP), ipv4=True
        ),
        "netmask": _uci_scalar_clean(
            await _ssh_run(ssh, RootCommands.GET_NET_MASK), ipv4=True
        ),
        "gateway": _uci_scalar_clean(
            await _ssh_run(ssh, RootCommands.GET_NET_GW), ipv4=True
        ),
    }


async def _device_ipv6_matches_apply(
    ssh: AsyncGenericDriver, apply: dict[str, str]
) -> bool:
    uci = await _read_uci_ip(ssh, v6=True)
    if not ipv6_equal(apply["ipv6_address"], str(uci.get("address", ""))):
        return False
    gw = str(apply.get("ipv6_gateway", "")).strip()
    uci_gw = str(uci.get("gateway", "")).strip()
    if not gw:
        return True
    if not uci_gw:
        return True
    return ipv6_equal(gw, uci_gw)


async def _cli_apply_ipv6_static(
    ssh: AsyncGenericDriver,
    apply: dict[str, str],
    cfg: dict[str, Any],
    *,
    verify_gateway: bool = True,
) -> None:
    """Set network.lan.ip6proto/ip6addr/ip6gw via UCI (authoritative for IP_18)."""
    addr = str(apply.get("ipv6_address", "")).strip()
    gw = str(apply.get("ipv6_gateway", "")).strip()
    if not addr:
        pytest.fail("IPv6 address missing for UCI apply")
    await _ucidyn_set(ssh, "network.lan.ip6proto", "static")
    await _ucidyn_set(ssh, "network.lan.ip6addr", addr)
    if gw:
        await _ucidyn_set(ssh, "network.lan.ip6gw", gw)
    await _ucidyn_apply(ssh)
    await asyncio.sleep(int(cfg.get("network_reload_wait_s", 20)))

    uci_addr = _uci_scalar_clean(await _ssh_run(ssh, RootCommands.GET_NET_IP6))
    uci_gw = _uci_scalar_clean(await _ssh_run(ssh, RootCommands.GET_NET_GW6)) if gw else ""
    if not uci_addr:
        pytest.fail(
            f"UCI network.lan.ip6addr not set after apply (wanted {addr}); "
            f"raw={await _ssh_run(ssh, RootCommands.GET_NET_IP6)[:80]}"
        )
    if not ipv6_equal(addr, uci_addr):
        pytest.fail(f"UCI ip6addr mismatch: want {addr}, got {uci_addr}")
    if verify_gateway and gw and uci_gw and not ipv6_equal(gw, uci_gw):
        pytest.fail(f"UCI ip6gw mismatch: want {gw}, got {uci_gw}")


async def _gui_apply_ipv6_static(
    gui_page,
    cfg: dict[str, Any],
    apply: dict[str, str],
) -> None:
    """LuCI static IPv6: ip6proto + address/gateway (clears stale 'uci: Entry not found' text)."""
    from utils.ui_helpers import fill_luci_input

    attach_dialog_handler(gui_page)
    await open_network_submenu(gui_page, "/network/ip")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    proto6 = gui_page.locator("select[name*='network.lan.ip6proto']").first
    if await proto6.count() > 0:
        for opt in ("static", "Static"):
            try:
                await proto6.select_option(value=opt)
                break
            except Exception:
                try:
                    await proto6.select_option(label=opt)
                    break
                except Exception:
                    continue

    addr = str(apply.get("ipv6_address", "")).strip()
    gw = str(apply.get("ipv6_gateway", "")).strip()
    addr_loc = gui_page.locator(NetworkLocators.IPv6_ADDRESS).first
    if addr and await addr_loc.count() > 0:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_ADDRESS, addr)
    elif addr:
        raise RuntimeError("IPv6 address field not found in LuCI")
    gw_loc = gui_page.locator(NetworkLocators.IPv6_GATEWAY).first
    if gw and await gw_loc.count() > 0:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_GATEWAY, gw)

    save = gui_page.locator(NetworkLocators.SAVE_BUTTON).first
    await apply_triple(
        gui_page,
        save,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=float(cfg.get("gui_settle_seconds", 12)),
    )


async def _apply_ipv6_static_on_device(ctx: IpTestContext, apply: dict[str, str]) -> None:
    """Apply static IPv6 on DUT; verify UCI and fall back to CLI if GUI wrote invalid values."""
    ssh = ctx.ssh
    cfg = ctx.cfg
    role = ctx.device_target.upper()
    addr = apply["ipv6_address"]
    gw = apply.get("ipv6_gateway", "")
    print(f"[ipv6] applying static {addr} gw={gw} on {role} ({type(ssh).__name__})")

    if await _device_ipv6_matches_apply(ssh, apply):
        ctx.notes.append(f"{role} IPv6 already configured ({addr}); skipping re-apply")
        return

    if ctx.gui_page is not None and ctx.device_target == "bts":
        try:
            await _gui_apply_ipv6_static(ctx.gui_page, cfg, apply)
            await asyncio.sleep(int(cfg.get("network_reload_wait_s", 10)))
            if await _device_ipv6_matches_apply(ssh, apply):
                ctx.notes.append("IPv6 applied via GUI")
                return
            ctx.notes.append("IPv6 GUI apply incomplete; using CLI/UCI")
        except Exception as exc:
            ctx.notes.append(f"IPv6 GUI apply failed ({exc}); using CLI/UCI")

    await _cli_apply_ipv6_static(ssh, apply, cfg)
    ctx.notes.append(f"{role} IPv6 applied via CLI/UCI: {addr}")


async def _iface_name(ssh: AsyncGenericDriver) -> str:
    out = await _ssh_run(ssh, "ip route show default 2>/dev/null | head -1")
    m = re.search(r"dev\s+(\S+)", out)
    return m.group(1) if m else "eth0"


async def _check_web_ui(
    host: str,
    password: str,
    *,
    username: str = "admin",
    timeout: float = 15.0,
) -> bool:
    url = f"http://{format_http_host(host)}/"
    user = (username or "admin").strip() or "admin"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, auth=(user, password))
            return resp.status_code < 500
    except Exception:
        return False


async def _check_web_ui_with_fallback(
    primary: str,
    fallbacks: Iterable[str],
    password: str,
    *,
    username: str = "admin",
    notes: list[str] | None = None,
) -> bool:
    for host in _all_mgmt_hosts(primary, fallbacks):
        if await _check_web_ui(host, password, username=username):
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


def _lab_mgmt_vlan_id(profile: dict[str, Any]) -> int:
    tb = profile.get("testbed", {}) or {}
    mgmt = tb.get("mgmt_vlan", {}) or profile.get("mgmt_vlan", {}) or {}
    return int(mgmt.get("lab_pc_vlan_id", mgmt.get("uci_value", 101)))


def _lab_primary_pc(profile: dict[str, Any]) -> dict[str, Any]:
    tb = profile.get("testbed", {}) or {}
    return dict(tb.get("primary_pc", {}) or {})


async def _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx: IpTestContext) -> str:
    """Keep lab PC mgmt VLAN at /120 IPv6 (do not replace with IPv4 setup)."""
    from utils.ip_case_preflight import preflight_step3_lab_mgmt_ipv6

    await preflight_step3_lab_mgmt_ipv6(ctx)
    profile = ctx.cfg.get("_profile") or {}
    parent = str(_lab_primary_pc(profile).get("mgmt_interface", "enp3s0"))
    vlan_id = _lab_mgmt_vlan_id(profile)
    return f"{parent}.{vlan_id}"


async def _ensure_lab_mgmt_vlan_for_ping(ctx: IpTestContext) -> str:
    """Bring up tagged mgmt VLAN on lab PC with 192.168.2.x for ping/Web tests."""
    profile = ctx.cfg.get("_profile") or {}
    pc = _lab_primary_pc(profile)
    vlan_id = _lab_mgmt_vlan_id(profile)
    ipv4 = str(ctx.cfg.get("lab_pc_mgmt_ipv4", "192.168.2.10")).strip()
    mask = str(ctx.cfg.get("lab_pc_mgmt_netmask", "255.255.255.0")).strip()
    password = str(ctx.cfg.get("_password", ""))
    vlan_if = await ensure_lab_pc_mgmt_vlan_ipv4(
        pc,
        vlan_id=vlan_id,
        ipv4=ipv4,
        netmask=mask,
        password=password,
    )
    if not vlan_if:
        pytest.skip(
            f"lab PC mgmt VLAN {vlan_id} setup failed "
            f"(need sudo on {pc.get('mgmt_interface', 'enp3s0')}.{vlan_id})"
        )
    ctx.notes.append(f"lab mgmt interface {vlan_if} -> {ipv4}")
    ctx.cfg["_lab_ping_bind_ipv4"] = normalize_ip(ipv4.split("/")[0])
    return vlan_if


def _lab_ping_bind_ipv4(cfg: dict[str, Any]) -> str | None:
    raw = str(cfg.get("_lab_ping_bind_ipv4") or cfg.get("lab_pc_mgmt_ipv4", "")).split("/")[0].strip()
    return normalize_ip(raw) if raw else None


def _lab_ping_kwargs(cfg: dict[str, Any]) -> dict[str, Any]:
    bind = _lab_ping_bind_ipv4(cfg)
    return {"bind_ipv4": bind} if bind else {}


def _run_local_cmd(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


async def _wait_for_lab_ping6_during_apply(
    ctx: IpTestContext,
    expected_host: str,
    *,
    wait_s: int,
    interval_s: int,
) -> str:
    """Poll lab ping6 while device applies static IPv6 (IP_18)."""
    from utils.ip_case_preflight import preflight_step3_lab_mgmt_ipv6

    await preflight_step3_lab_mgmt_ipv6(ctx)
    vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
    host = normalize_ip(expected_host)
    deadline = time.monotonic() + wait_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        stats = await _ping_from_lab_pc_v6(
            vlan_if,
            host,
            count=1,
            bind_ipv6=_lab_ping_bind_ipv6(ctx.cfg, ctx.cfg.get("_profile") or {}),
        )
        if stats.ok:
            ctx.notes.append(
                f"lab ping6 to {host} ok during apply wait (attempt {attempt})"
            )
            return vlan_if
        remaining = int(deadline - time.monotonic())
        ctx.notes.append(f"apply wait ping6 {attempt}: {host} not up ({remaining}s left)")
        await asyncio.sleep(max(1, interval_s))
    return vlan_if


async def _wait_for_lab_ping_during_apply(
    ctx: IpTestContext,
    target: str,
    *,
    wait_s: int,
    interval_s: int,
) -> str:
    """After GUI apply: keep pinging from mgmt VLAN while address comes up on br-lan."""
    vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
    host = normalize_ip(target)
    deadline = time.monotonic() + max(1, wait_s)
    attempt = 0
    last_raw: str = ""
    while time.monotonic() < deadline:
        attempt += 1
        stats = await _ping_from_lab_pc(vlan_if, host, count=1)
        if stats.ok:
            ctx.notes.append(
                f"lab ping to {host} via {vlan_if} ok during apply wait (attempt {attempt})"
            )
            return vlan_if
        last_raw = stats.raw or last_raw
        remaining = int(deadline - time.monotonic())
        ctx.notes.append(
            f"apply wait ping {attempt}: {host} not up yet ({remaining}s left)"
        )
        await asyncio.sleep(max(1, interval_s))
    ctx.notes.append(
        f"apply wait ping FAILED after {wait_s}s: {host} via {vlan_if} last='{last_raw[:120]}'"
    )
    return vlan_if


def _remote_ping_wait_settings(cfg: dict[str, Any]) -> tuple[int, int]:
    """Max wait ceiling (s) and retry interval (s) for CPE reachability polls."""
    max_s = int(cfg.get("ip03_remote_wait_max_s") or cfg.get("ip03_remote_wait_s", 90))
    interval_s = int(cfg.get("ip03_remote_retry_interval_s", 5))
    return max(1, max_s), max(1, interval_s)


async def _ping_from_lab_pc_v6(
    bind_iface: str,
    target: str,
    *,
    count: int = 4,
    size: int | None = None,
    per_packet_wait_s: int = 2,
    bind_ipv6: str | None = None,
    df: bool = False,
) -> PingStats:
    """ping6 from lab PC (mgmt VLAN) to DUT LAN/mgmt IPv6."""
    host = shlex.quote(normalize_ip(target))
    extra = f" -s {int(size)}" if size is not None else ""
    if df:
        extra += " -M do"
    bind_attempts: list[str] = []
    if bind_ipv6:
        bind_attempts.append(_canonical_ipv6(bind_ipv6))
    if bind_iface not in bind_attempts:
        bind_attempts.append(bind_iface)

    last_out = ""
    for bind in bind_attempts:
        bind_q = shlex.quote(bind)
        cmd = f"ping6 -I {bind_q} -c {int(count)} -W {int(per_packet_wait_s)}{extra} {host}"
        for sudo in (True, False):
            full = f"sudo -n {cmd}" if sudo else cmd
            rc, out = await asyncio.to_thread(_run_local_cmd, full)
            last_out = out
            stats = _parse_ping_stats(out)
            if stats.ok or stats.received > 0:
                return stats
            if sudo and rc != 0 and "password" not in out.lower():
                continue
            if "cannot assign" in out.lower():
                break
    return _parse_ping_stats(last_out)


async def _assert_lab_ping_ipv6(
    ctx: IpTestContext,
    target: str,
    *,
    count: int | None = None,
) -> PingStats:
    profile = ctx.cfg.get("_profile") or {}
    vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
    host = normalize_ip(target)
    bind_v6 = _lab_ping_bind_ipv6(ctx.cfg, profile)
    n = count or int(ctx.cfg.get("ping_count_short", 4))
    stats = await _ping_from_lab_pc_v6(
        vlan_if, host, count=n, bind_ipv6=bind_v6 or None
    )
    assert stats.ok or stats.received > 0, (
        f"lab PC ping6 {host} via {vlan_if} (bind {bind_v6 or vlan_if}) failed: {stats.raw[:300]}"
    )
    ctx.notes.append(
        f"lab ping6 {host}: rx={stats.received} loss={stats.loss_pct}%"
    )
    return stats


async def _ping_from_lab_pc(
    bind_iface: str,
    target: str,
    *,
    count: int = 4,
    size: int | None = None,
    df: bool = False,
    per_packet_wait_s: int = 2,
    bind_ipv4: str | None = None,
) -> PingStats:
    """Ping from automation host bound to tagged mgmt VLAN (per test plan)."""
    host = shlex.quote(normalize_ip(target))
    bind = shlex.quote(normalize_ip(bind_ipv4.split("/")[0])) if bind_ipv4 else shlex.quote(bind_iface)
    extra = ""
    if size is not None:
        extra += f" -s {int(size)}"
    if df:
        extra += " -M do"
    cmd = f"ping -I {bind} -c {int(count)} -W {int(per_packet_wait_s)}{extra} {host}"
    for sudo in (True, False):
        full = f"sudo -n {cmd}" if sudo else cmd
        rc, out = await asyncio.to_thread(_run_local_cmd, full)
        stats = _parse_ping_stats(out)
        if stats.ok or stats.received > 0:
            return stats
        if sudo and rc != 0 and "password" not in out.lower():
            continue
    return _parse_ping_stats(out)


async def _wait_for_remote_ping_from_lab(
    bind_iface: str,
    target: str,
    *,
    wait_s: int,
    interval_s: int,
    count: int = 1,
    per_packet_wait_s: int = 1,
    notes: list[str] | None = None,
    **ping_kw: Any,
) -> PingStats:
    """
    Poll until remote ping succeeds or max wait is reached.
    Probes every interval_s (default 5s); exits immediately on first success (not a fixed sleep).
    """
    started = time.monotonic()
    deadline = started + max(1, wait_s)
    last = PingStats(raw="no attempts")
    attempt = 0
    host = normalize_ip(target)
    while time.monotonic() < deadline:
        attempt += 1
        last = await _ping_from_lab_pc(
            bind_iface,
            host,
            count=count,
            per_packet_wait_s=per_packet_wait_s,
            **ping_kw,
        )
        if last.ok:
            if notes is not None:
                elapsed = time.monotonic() - started
                notes.append(
                    f"remote ping {host} ok after {elapsed:.1f}s (probe {attempt}, max {wait_s}s)"
                )
            return last
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval_s, remaining))
    if notes is not None:
        elapsed = time.monotonic() - started
        notes.append(f"remote ping {host} not ready after {elapsed:.1f}s ({attempt} probes)")
    return last


async def _wait_for_remote_ping6_from_lab(
    bind_iface: str,
    target: str,
    *,
    wait_s: int,
    interval_s: int,
    bind_ipv6: str | None = None,
    count: int = 1,
    per_packet_wait_s: int = 1,
    size: int | None = None,
    df: bool = False,
    notes: list[str] | None = None,
) -> PingStats:
    """Poll lab ping6 until remote target responds or timeout."""
    started = time.monotonic()
    deadline = started + max(1, wait_s)
    last = PingStats(raw="no ping6 attempts")
    attempt = 0
    host = normalize_ip(target)
    while time.monotonic() < deadline:
        attempt += 1
        last = await _ping_from_lab_pc_v6(
            bind_iface,
            host,
            count=count,
            per_packet_wait_s=per_packet_wait_s,
            bind_ipv6=bind_ipv6,
            size=size,
            df=df,
        )
        if last.ok or last.received > 0:
            if notes is not None:
                elapsed = time.monotonic() - started
                notes.append(
                    f"remote ping6 {host} ok after {elapsed:.1f}s (probe {attempt}, max {wait_s}s)"
                )
            return last
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval_s, remaining))
    if notes is not None:
        elapsed = time.monotonic() - started
        notes.append(f"remote ping6 {host} not ready after {elapsed:.1f}s ({attempt} probes)")
    return last


async def _resolve_ipv6_gateway(ctx: IpTestContext) -> str:
    """Device IPv6 default gateway from UCI or profile."""
    apply = _ipv6_values_for_target(ctx)
    uci_gw = _uci_scalar_clean(await _ssh_run(ctx.ssh, RootCommands.GET_NET_GW6))
    gw = uci_gw or str(apply.get("ipv6_gateway", "")).strip()
    return normalize_ip(gw.split("/")[0]) if gw else ""


async def _run_ip21_ipv6_gateway(ctx: IpTestContext) -> None:
    """IP_21: ping6 + traceroute6 to configured IPv6 gateway (mirrors IP_04)."""
    from utils.ip_case_preflight import ensure_device_ipv6_configured

    cfg = ctx.cfg
    ssh = ctx.ssh
    await ensure_device_ipv6_configured(ctx)
    await _assert_local_reachable(ctx, v6=True, count=int(cfg.get("ping_count_short", 4)))

    gw = await _resolve_ipv6_gateway(ctx)
    if not gw:
        pytest.skip("IP_21: no IPv6 gateway (run IP_18 or set ip_tests.ipv6_gateway_*)")

    ctx.notes.append(f"IP_21 gateway {gw}")
    stats = await _ping(ssh, gw, count=int(cfg.get("ping_count_short", 4)), v6=True)
    if not stats.ok and "unreachable" in stats.raw.lower():
        pytest.skip(f"IP_21: gateway {gw} not reachable on bench: {stats.raw[:120]}")
    assert stats.ok or stats.received > 0, f"IP_21 ping6 gateway failed: {stats.raw[:300]}"

    route = await _ssh_run_raw(ssh, f"ip -6 route get {shlex.quote(gw)} 2>&1")
    ctx.notes.append(f"IP_21 ip -6 route get: {route.strip()[:220]}")
    assert gw.lower() in route.lower() or "via" in route.lower(), (
        f"IP_21: unexpected route to {gw}: {route[:300]}"
    )

    trace = await _run_traceroute(ssh, gw, v6=True)
    if trace:
        ctx.notes.append(f"IP_21 traceroute6: {trace[:280]}")
    else:
        ctx.notes.append("IP_21: traceroute6/tracepath6 unavailable (ping6 ok)")


async def _run_ip23_ipv6_long_ping(ctx: IpTestContext) -> None:
    """IP_23: DUT ping6 to remote peer -c N (mirrors IP_06 over RF)."""
    cfg = ctx.cfg
    ssh = ctx.ssh
    await _assert_local_reachable(ctx, v6=True, count=int(cfg.get("ping_count_short", 4)))
    await _ping_remote_after_local(
        ctx,
        v6=True,
        count=1,
        retries=int(cfg.get("post_reboot_remote_ping_retries", 12)),
        retry_interval_s=int(cfg.get("post_reboot_remote_ping_interval_s", 10)),
    )
    target = normalize_ip(str(ctx.peer_host or ""))
    if not target:
        pytest.skip("IP_23: no remote IPv6 peer (run IP_18 on CPE)")

    count = int(cfg.get("ping_count_long", 1000))
    ctx.notes.append(f"IP_23 long ping6 {target} -c {count} from {ctx.device_target.upper()}")
    stats = await _ping(ssh, target, count=count, v6=True)
    max_loss = float(cfg.get("max_ping_loss_pct", 2.0))
    assert stats.loss_pct <= max_loss or stats.received >= count * (1 - max_loss / 100), (
        f"IP_23 long ping6 loss {stats.loss_pct}% > {max_loss}%: {stats.raw[:300]}"
    )
    ctx.notes.append(
        f"IP_23 long ping6 ok: rx={stats.received}/{count} loss={stats.loss_pct}%"
    )


async def _run_ip25_ipv6_fragmentation(ctx: IpTestContext) -> None:
    """IP_25: large ping6 to remote peer (mirrors IP_08)."""
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    frag_size = int(cfg.get("ipv6_fragmentation_ping_size", 2000))
    await _assert_local_reachable(ctx, v6=True, count=int(cfg.get("ping_count_short", 4)))

    target = normalize_ip(str(ctx.peer_host or ""))
    if not target:
        pytest.skip("IP_25: no remote IPv6 peer")

    vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
    bind_v6 = _lab_ping_bind_ipv6(cfg, profile)
    max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
    ctx.notes.append(
        f"IP_25 lab ping6 {target} via {vlan_if} (small + {frag_size}-byte)"
    )

    small = await _wait_for_remote_ping6_from_lab(
        vlan_if,
        target,
        wait_s=max_wait_s,
        interval_s=interval_s,
        bind_ipv6=bind_v6 or None,
        count=1,
        notes=ctx.notes,
    )
    if not small.ok and small.received == 0:
        await _ping_remote_after_local(ctx, v6=True, count=1)
        stats = await _ping(ctx.ssh, target, count=4, size=64, v6=True)
        assert stats.received > 0, f"IP_25 small ping6 failed: {stats.raw[:200]}"
    else:
        assert small.received > 0, f"IP_25 small ping6 failed: {small.raw[:200]}"

    large = await _ping_from_lab_pc_v6(
        vlan_if, target, count=4, size=frag_size, bind_ipv6=bind_v6 or None
    )
    if large.received == 0:
        large = await _ping(ctx.ssh, target, count=4, size=frag_size, v6=True)
    assert large.received > 0, f"IP_25 large ping6 ({frag_size}b) failed: {large.raw[:300]}"
    ctx.notes.append(
        f"IP_25 fragmentation ok: {frag_size}b rx={large.received} loss={large.loss_pct}%"
    )


async def _assert_br_lan_ipv4(
    ssh: AsyncGenericDriver,
    cfg: dict[str, Any],
    expected_ip: str,
    *,
    notes: list[str] | None = None,
) -> str:
    """Verify configured IPv4 on br-lan (ifconfig / ip addr), per IP_01."""
    bridge = str(cfg.get("verify_lan_bridge", "br-lan")).strip() or "br-lan"
    expected = normalize_ip(expected_ip.split("/")[0])
    wait_s = int(cfg.get("network_reload_wait_s", 20))
    attempts = int(cfg.get("br_lan_verify_attempts", 4))
    for attempt in range(max(1, attempts)):
        for cmd in (
            f"ifconfig {shlex.quote(bridge)} 2>/dev/null",
            f"ip -4 addr show dev {shlex.quote(bridge)} 2>/dev/null",
        ):
            out = await _ssh_run_raw(ssh, cmd)
            if expected in out:
                return out[:400]
            all_v4 = await _ssh_run_raw(ssh, "ip -4 addr show 2>/dev/null")
        if expected in all_v4:
            if notes is not None:
                notes.append(f"{expected} present on device but not on {bridge} yet (attempt {attempt + 1})")
            line = next((ln for ln in all_v4.splitlines() if expected in ln), "")
            if f"dev {bridge}" in all_v4.split(expected)[0][-80:]:
                return all_v4[:400]
        if attempt + 1 < attempts:
            await asyncio.sleep(wait_s // 2)
        br_out = await _ssh_run_raw(ssh, f"ip -4 addr show dev {shlex.quote(bridge)} 2>/dev/null")
        pytest.fail(f"{expected} not on {bridge}; ip output: {br_out[:300]}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_backup_archive(cfg: dict[str, Any], device_target: str) -> Path | None:
    from utils.device_backup import get_session_backup_path

    role = "CPE" if device_target == "cpe" else "BTS"
    cached = get_session_backup_path(role)
    if cached is not None:
        return cached
    explicit = str(cfg.get("backup_archive_path", "")).strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = _repo_root() / path
        return path if path.is_file() else None
    profile = cfg.get("_profile") or {}
    recovery = profile.get("recovery", {}) or {}
    link = profile.get("link_recovery", {}) or {}
    if device_target == "cpe":
        rel = recovery.get("cpe_restore_archive") or link.get("cpe_archive")
    else:
        rel = recovery.get("bts_restore_archive") or link.get("bts_archive")
    if not rel:
        return None
    path = _repo_root() / str(rel)
    return path if path.is_file() else None


def _parse_uci_network_lan(content: str) -> dict[str, str]:
    """Parse LAN IPv4 fields from an UCI network config file body."""
    section = "none"
    lan: dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("config interface 'lan'") or line.startswith('config interface "lan"'):
            section = "lan"
            continue
        if line.startswith("config ") and section == "lan":
            break
        if section != "lan":
            continue
        m = re.match(r"option\s+(\w+)\s+'([^']*)'", line)
        if not m:
            m = re.match(r'option\s+(\w+)\s+"([^"]*)"', line)
        if m:
            lan[m.group(1)] = m.group(2)
    return lan


def _read_lan_ipv4_from_backup_archive(archive: Path) -> dict[str, str]:
    """
    Inspect sysupgrade backup tar.gz for network.lan IPv4 settings.
    Returns uci-style keys: ipaddr, netmask, gateway (may be partial if absent).
    """
    if not archive.is_file():
        raise FileNotFoundError(f"backup archive missing: {archive}")
    min_bytes = 256
    if archive.stat().st_size < min_bytes:
        raise ValueError(f"backup archive too small ({archive.stat().st_size} bytes): {archive.name}")
    network_names = ("etc/config/network", "network", "./etc/config/network")
    with tarfile.open(archive, "r:*") as tar:
        members = {m.name.lstrip("./"): m for m in tar.getmembers() if m.isfile()}
        target = None
        for name in network_names:
            if name in members:
                target = members[name]
                break
        if target is None:
            for name, member in members.items():
                if name.endswith("config/network") or name.endswith("/network"):
                    target = member
                    break
        if target is None:
            raise ValueError(f"backup archive has no etc/config/network: {archive.name}")
        extracted = tar.extractfile(target)
        if extracted is None:
            raise ValueError(f"cannot read network config from backup: {archive.name}")
        content = extracted.read().decode("utf-8", errors="replace")
    lan = _parse_uci_network_lan(content)
    ipaddr = lan.get("ipaddr", "").strip()
    return {
        "address": ipaddr,
        "netmask": lan.get("netmask", "").strip(),
        "gateway": lan.get("gateway", "").strip(),
    }


def _assert_backup_matches_live_uci(
    archive: Path,
    live_uci: dict[str, str],
    *,
    notes: list[str] | None = None,
) -> None:
    """Fail fast if sysupgrade backup does not reflect the live device LAN IPv4."""
    backup_uci = _read_lan_ipv4_from_backup_archive(archive)
    live_addr = normalize_ip(str(live_uci.get("address", "")).split("/")[0])
    backup_addr = normalize_ip(str(backup_uci.get("address", "")).split("/")[0])
    if not backup_addr:
        pytest.fail(
            f"IP_15 backup {archive.name} has no network.lan.ipaddr — archive may be empty or wrong format"
        )
    if live_addr and backup_addr != live_addr:
        pytest.fail(
            f"IP_15 backup LAN IP mismatch: live={live_addr} backup={backup_addr} "
            f"(archive {archive.name} does not match device before reset)"
        )
    live_mask = str(live_uci.get("netmask", "")).strip()
    backup_mask = str(backup_uci.get("netmask", "")).strip()
    if live_mask and backup_mask and live_mask != backup_mask:
        pytest.fail(
            f"IP_15 backup netmask mismatch: live={live_mask} backup={backup_mask}"
        )
    if notes is not None:
        notes.append(
            f"IP_15 backup validated: LAN {backup_addr}"
            + (f"/{backup_mask}" if backup_mask else "")
        )


async def _cli_set_mtu(ssh: AsyncGenericDriver, iface: str, mtu: int, cfg: dict[str, Any]) -> None:
    await _ssh_run(ssh, f"ip link set {shlex.quote(iface)} mtu {int(mtu)}")
    try:
        await _ucidyn_set(ssh, "network.lan.mtu", str(int(mtu)))
    except Exception:
        await _ucidyn_set(ssh, "network.@device[0].mtu", str(int(mtu)))
    await _ucidyn_apply(ssh)
    await asyncio.sleep(int(cfg.get("network_reload_wait_s", 15)))


async def _apply_lab_pc_mtu_change(ctx: IpTestContext, *, v6: bool) -> None:
    """IP_07 / IP_24: change MTU on backend (lab) PC mgmt VLAN iface, not on DUT."""
    cfg = ctx.cfg
    cid = ctx.case.case_id
    mtu = int(cfg.get("mtu_test_value", 1400))
    restore_mtu = str(cfg.get("mtu_restore_value", 1500))
    profile = cfg.get("_profile") or {}
    pc = _lab_primary_pc(profile)
    password = str(cfg.get("_password", ""))

    if v6:
        vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
        bind_v6 = _lab_ping_bind_ipv6(cfg, profile)
    else:
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        bind_v6 = None

    original_mtu = read_local_iface_mtu(vlan_if) or restore_mtu
    ctx.notes.append(f"{cid} lab PC MTU on {vlan_if}: {original_mtu} -> {mtu}")
    ok = await set_lab_pc_iface_mtu(pc, vlan_if, mtu, password)
    if not ok:
        pytest.skip(f"{cid}: could not set MTU {mtu} on lab PC {vlan_if}")
    read_mtu = read_local_iface_mtu(vlan_if) or ""
    if str(mtu) != read_mtu:
        pytest.skip(f"{cid}: lab PC MTU did not stick on {vlan_if} (expected {mtu}, got {read_mtu})")
    try:
        target = ctx.peer_host or str(cfg.get("remote_ping_host", "")).strip()
        if not target:
            pytest.skip(f"{cid}: no remote ping target")
        target = normalize_ip(target)
        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        header_overhead = 48 if v6 else 28
        df_size = max(mtu - header_overhead, 64)

        if v6:
            sm = await _wait_for_remote_ping6_from_lab(
                vlan_if,
                target,
                wait_s=max_wait_s,
                interval_s=interval_s,
                bind_ipv6=bind_v6,
                count=1,
                notes=ctx.notes,
            )
            if not sm.ok and sm.received == 0:
                pytest.skip(
                    f"{cid}: remote {target} not reachable via ping6 on {vlan_if} "
                    f"within {max_wait_s}s: {sm.raw[:120]}"
                )
            sm = await _ping_from_lab_pc_v6(
                vlan_if, target, count=3, size=64, bind_ipv6=bind_v6
            )
            lg = await _ping_from_lab_pc_v6(
                vlan_if, target, count=3, size=df_size, df=True, bind_ipv6=bind_v6
            )
            if lg.received == 0:
                lg = await _wait_for_remote_ping6_from_lab(
                    vlan_if,
                    target,
                    wait_s=max_wait_s,
                    interval_s=interval_s,
                    bind_ipv6=bind_v6,
                    count=1,
                    size=df_size,
                    df=True,
                    notes=ctx.notes,
                )
                if lg.received > 0:
                    lg = await _ping_from_lab_pc_v6(
                        vlan_if,
                        target,
                        count=3,
                        size=df_size,
                        df=True,
                        bind_ipv6=bind_v6,
                    )
        else:
            sm = await _wait_for_remote_ping_from_lab(
                vlan_if,
                target,
                wait_s=max_wait_s,
                interval_s=interval_s,
                count=1,
                notes=ctx.notes,
                size=64,
            )
            if not sm.ok:
                pytest.skip(
                    f"{cid}: CPE {target} not reachable via {vlan_if} within {max_wait_s}s: {sm.raw[:120]}"
                )
            sm = await _ping_from_lab_pc(vlan_if, target, count=3, size=64)
            lg = await _ping_from_lab_pc(vlan_if, target, count=3, size=df_size, df=True)
            if not lg.ok:
                lg = await _wait_for_remote_ping_from_lab(
                    vlan_if,
                    target,
                    wait_s=max_wait_s,
                    interval_s=interval_s,
                    count=1,
                    notes=ctx.notes,
                    size=df_size,
                    df=True,
                )
                if lg.ok:
                    lg = await _ping_from_lab_pc(vlan_if, target, count=3, size=df_size, df=True)

        ctx.notes.append(
            f"{cid} ping {'6 ' if v6 else ''}{target} via {vlan_if} "
            f"(small rx={sm.received}, DF/large rx={lg.received})"
        )
        assert sm.received > 0 and lg.received > 0, (
            f"{cid} small/DF ping failed: small={sm.raw[:120]} df={lg.raw[:120]}"
        )
    finally:
        await set_lab_pc_iface_mtu(pc, vlan_if, int(original_mtu), password)
        ctx.notes.append(f"{cid} restored lab PC {vlan_if} MTU to {original_mtu}")


async def _apply_mtu_change(ctx: IpTestContext, *, v6: bool) -> None:
    cfg = ctx.cfg
    ssh = ctx.ssh
    mtu = int(cfg.get("mtu_test_value", 1400))
    restore_mtu = str(cfg.get("mtu_restore_value", 1500))
    iface = await _iface_name(ssh)
    original_mtu = (await _ssh_run(ssh, f"cat /sys/class/net/{iface}/mtu")).strip() or restore_mtu
    header_overhead = 48 if v6 else 28
    try:
        if ctx.gui_page is not None:
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
        else:
            await _cli_set_mtu(ssh, iface, mtu, cfg)
        read_mtu_raw = (await _ssh_run(ssh, f"cat /sys/class/net/{iface}/mtu")).strip()
        match = re.search(r"(\d+)", read_mtu_raw)
        read_mtu = match.group(1) if match else read_mtu_raw
        if str(mtu) != read_mtu:
            pytest.skip(f"MTU apply did not stick on {iface} (expected {mtu}, got {read_mtu_raw})")
        await _assert_local_reachable(ctx, v6=v6)
        if ctx.peer_host:
            stats = await _ping(
                ssh,
                ctx.peer_host,
                count=4,
                size=max(mtu - header_overhead, 0),
                df=True,
                v6=v6,
            )
            if not stats.ok:
                ctx.notes.append(f"DF ping to peer optional on bench: {stats.raw[:120]}")
    finally:
        await _restore_iface_mtu(ssh, iface, original_mtu)


def _factory_first_ssh_hosts(hosts: list[str]) -> list[str]:
    """Prefer 10.0.0.x factory path before mgmt IPv6 (bench after IPv4 runs)."""
    factory: list[str] = []
    rest: list[str] = []
    for host in hosts:
        if host.startswith("10.0.0."):
            factory.append(host)
        else:
            rest.append(host)
    return _ordered_unique_hosts(*factory, *rest)


def _device_ssh_host_candidates(ctx: IpTestContext) -> list[str]:
    """Ordered SSH targets: factory IPv4, LAN IPv4, mgmt IPv6."""
    cfg = ctx.cfg
    hosts: list[str] = []
    if ctx.host:
        hosts.append(ctx.host)
    baseline = _baseline_ipv4_values(cfg)
    if baseline.get("ipv4_address"):
        hosts.append(str(baseline["ipv4_address"]))
    hosts.extend(_ipv4_test_address_candidates(cfg))
    cli_fb = cfg.get("_cli_fallback_ip")
    if cli_fb:
        hosts.append(str(cli_fb))
    hosts.extend(ctx.fallback_hosts)
    profile = cfg.get("_profile") or {}
    tb = profile.get("testbed", {}) or {}
    rec = tb.get("recovery", {}) or {}
    if rec.get("bts_fallback_ipv4"):
        hosts.append(str(rec["bts_fallback_ipv4"]))
    dut = profile.get("dut", {}) or {}
    if dut.get("local_ip"):
        hosts.append(str(dut["local_ip"]))
    if ctx.device_target == "bts":
        mgmt = tb.get("mgmt_vlan", {}) or profile.get("mgmt_vlan", {}) or {}
        v6 = str(mgmt.get("ipv6_bts", "")).strip()
        if v6:
            hosts.append(normalize_ip(v6.split("/")[0]))
    elif ctx.device_target == "cpe":
        mgmt = tb.get("mgmt_vlan", {}) or profile.get("mgmt_vlan", {}) or {}
        v6 = str(mgmt.get("ipv6_cpe", "")).strip()
        if v6:
            hosts.append(normalize_ip(v6.split("/")[0]))
    return _factory_first_ssh_hosts(_ordered_unique_hosts(*hosts))


def _resolve_ip17_route_spec(
    ctx: IpTestContext,
    *,
    cidr: str,
    gateway: str,
    ping_target: str,
) -> tuple[str, str]:
    """
    Pick a static route that does not replace the device's connected LAN /24.
    When the profile uses 192.168.2.0/24 (same as br-lan), use host route to the ping target.
    """
    cidr = str(cidr or "").strip()
    gateway = str(gateway or "").strip()
    peer = normalize_ip(str(ping_target or ctx.peer_host or "").split("/")[0])
    bridge = str(ctx.cfg.get("verify_lan_bridge", "br-lan")).strip() or "br-lan"
    lan_prefix = str(ctx.cfg.get("ipv4_netmask", "255.255.255.0"))
    try:
        if "/" in str(ctx.cfg.get("ipv4_address", "")):
            lan_net = ipaddress.ip_network(str(ctx.cfg["ipv4_address"]), strict=False)
        else:
            lan_ip = str(ctx.cfg.get("ipv4_address", "192.168.2.230")).split("/")[0]
            plen = ipaddress.IPv4Network(f"0.0.0.0/{lan_prefix}", strict=False).prefixlen
            lan_net = ipaddress.ip_network(f"{lan_ip}/{plen}", strict=False)
    except ValueError:
        lan_net = ipaddress.ip_network("192.168.2.0/24", strict=False)

    if peer:
        try:
            peer_addr = ipaddress.ip_address(peer)
            if not cidr or cidr == str(lan_net):
                ctx.notes.append(
                    f"IP_17: using host route {peer}/32 (avoid replacing connected {lan_net} on {bridge})"
                )
                return f"{peer}/32", gateway
            net = ipaddress.ip_network(cidr, strict=False)
            if peer_addr in net and net.prefixlen < lan_net.prefixlen:
                ctx.notes.append(f"IP_17: narrowing route {cidr} -> {peer}/32 for safe cleanup")
                return f"{peer}/32", gateway
        except ValueError:
            pass
    if cidr:
        return cidr, gateway
    if peer:
        return f"{peer}/32", gateway
    return cidr, gateway


def _ip17_cleanup_commands(cidr: str, *, gateway: str, bridge: str) -> str:
    """
    Remove only the test static route, then restore connected LAN /24 if it was removed.
    """
    cidr = str(cidr or "").strip()
    gateway = str(gateway or "").strip()
    bridge = str(bridge or "br-lan").strip() or "br-lan"
    if not cidr:
        return "true"
    cidr_q = shlex.quote(cidr)
    bridge_q = shlex.quote(bridge)
    parts: list[str] = []
    if gateway:
        parts.append(f"ip route del {cidr_q} via {shlex.quote(gateway)} 2>/dev/null || true")
    parts.append(f"ip route del {cidr_q} 2>/dev/null || true")
    if "/" in cidr:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            if net.prefixlen <= 24 and net.version == 4:
                parts.append(
                    f"ip route show table main 2>/dev/null | grep -qE '{net.network_address}/{net.prefixlen}' "
                    f"|| ip route replace {cidr_q} dev {bridge_q} scope link 2>/dev/null || true"
                )
        except ValueError:
            pass
    return " ; ".join(parts)


async def _remove_static_route_on_device(
    ctx: IpTestContext,
    cidr: str,
    *,
    gateway: str = "",
) -> None:
    """
    Delete IP_17 test route and restore connected br-lan /24 if needed.
    Uses current SSH or reconnects via factory IPv4 / mgmt IPv6.
    """
    cidr = str(cidr or "").strip()
    if not cidr:
        return
    bridge = str(ctx.cfg.get("verify_lan_bridge", "br-lan")).strip()
    cmd = _ip17_cleanup_commands(cidr, gateway=gateway, bridge=bridge)
    password = str(ctx.cfg.get("_password", ""))
    errors: list[str] = []

    if ctx.ssh is not None:
        try:
            await _ensure_ssh_open(ctx.ssh)
            await _ssh_run(ctx.ssh, cmd, timeout=20)
            ctx.notes.append(f"IP_17 removed route {cidr} on {ctx.host}")
            return
        except Exception as exc:
            errors.append(f"current session: {exc}")

    old_ssh = ctx.ssh
    try:
        hosts = _device_ssh_host_candidates(ctx)
        new_ssh, effective = await _wait_ssh_any(hosts, password, timeout_s=90, interval_s=3)
        if old_ssh is not None and old_ssh is not new_ssh:
            await _close_ssh(old_ssh)
        ctx.ssh = new_ssh
        ctx.host = effective
        await _ssh_run(ctx.ssh, cmd, timeout=20)
        ctx.notes.append(f"IP_17 removed route {cidr} via fallback SSH {effective}")
    except Exception as exc:
        errors.append(str(exc))
        ctx.notes.append(f"IP_17 route cleanup failed for {cidr}: {'; '.join(errors)}")


async def _run_traceroute(ssh: AsyncGenericDriver, target: str, *, v6: bool, timeout_s: int = 25) -> str:
    host = shlex.quote(normalize_ip(target))
    if v6:
        cmd = f"traceroute6 -n -m 5 -w 2 {host} 2>/dev/null || tracepath6 -n {host} 2>/dev/null || true"
    else:
        cmd = f"traceroute -n -m 5 -w 2 {host} 2>/dev/null || tracepath -n {host} 2>/dev/null || true"
    return (await _ssh_run(ssh, cmd, timeout=timeout_s)).strip()


async def _resolve_ip17_ping_target(ctx: IpTestContext) -> str:
    """Use a 192.168.2.x CPE address reachable from the lab VLAN (not factory 10.0.0.1)."""
    explicit = str(ctx.cfg.get("static_route_ping_target", "")).strip()
    if explicit:
        return normalize_ip(explicit)
    peer = normalize_ip(str(ctx.peer_host or "").split("/")[0])
    if peer and not peer.startswith("10.0.0."):
        return peer
    discovered = await discover_cpe_ipv4_addresses(ctx)
    vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
    ping_kw = _lab_ping_kwargs(ctx.cfg)
    for ip in discovered:
        if str(ip).startswith("10.0.0."):
            continue
        stats = await _ping_from_lab_pc(
            vlan_if, ip, count=1, per_packet_wait_s=1, **ping_kw
        )
        if stats.ok:
            ctx.notes.append(f"IP_17 ping target from discovery: {ip}")
            ctx.peer_host = ip
            return ip
    pytest.fail(
        f"IP_17: no reachable CPE on 192.168.2.x from {vlan_if} (candidates={discovered[:8]})"
    )


async def _assert_static_route(ctx: IpTestContext) -> None:
    cfg = ctx.cfg
    ssh = ctx.ssh
    await _ensure_ssh_open(ssh)
    cidr = str(cfg.get("static_route_cidr") or cfg.get("static_route_target", "")).strip()
    ping_target = await _resolve_ip17_ping_target(ctx)
    gateway = str(cfg.get("static_route_gateway", "")).strip()
    if not gateway:
        gateway = str(cfg.get("ipv4_gateway", "")).strip()
    if not gateway or gateway.startswith("uci:"):
        gateway = (await _ssh_run(ssh, RootCommands.GET_NET_GW, timeout=20)).strip()
    if not gateway or gateway.startswith("uci:"):
        pytest.skip("no static-route gateway (set ip_tests.static_route_gateway or device default gw)")
    route_cidr, route_gw = _resolve_ip17_route_spec(
        ctx, cidr=cidr, gateway=gateway, ping_target=ping_target
    )
    cidr = route_cidr or cidr
    if not cidr:
        pytest.skip("set ip_tests.static_route_cidr or provide CPE peer for auto /32 route")
    if route_gw:
        gateway = route_gw
    try:
        await _ssh_run(
            ssh,
            f"ip route replace {shlex.quote(cidr)} via {shlex.quote(gateway)}",
            timeout=30,
        )
        ctx.notes.append(f"IP_17 route {cidr} via {gateway}")
        if ctx.device_target == "bts":
            vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
            stats = await _ping_from_lab_pc(
                vlan_if,
                ping_target,
                count=int(cfg.get("ping_count_short", 4)),
                **_lab_ping_kwargs(cfg),
            )
            assert stats.ok, f"IP_17 lab ping {ping_target} via {vlan_if}: {stats.raw[:300]}"
            ctx.notes.append(f"IP_17 lab ping to {ping_target} ok")
        else:
            stats = await _ping(ssh, ping_target, count=int(cfg.get("ping_count_short", 4)), v6=False)
            assert stats.ok, stats.raw
        route_get = await _ssh_run_raw(
            ssh, f"ip route get {shlex.quote(ping_target)} 2>&1", timeout=20
        )
        ctx.notes.append(f"IP_17 route get {ping_target}: {route_get.strip()[:240]}")
        assert ping_target in route_get and f"via {gateway}" in route_get, (
            f"IP_17: expected {ping_target} via {gateway}; route get:\n{route_get[:400]}"
        )
        try:
            trace = await _run_traceroute(ssh, ping_target, v6=False, timeout_s=20)
            if trace:
                ctx.notes.append(f"traceroute: {trace[:240]}")
        except Exception as exc:
            ctx.notes.append(f"IP_17 traceroute optional skip: {exc}")
    finally:
        await _remove_static_route_on_device(ctx, cidr, gateway=gateway)


async def _ensure_gui_on_host(gui_page, host: str, device_creds: dict[str, str]) -> None:
    """Re-authenticate LuCI on the device LAN/mgmt IP used for the test."""
    from pages.locators import LoginPageLocators
    from utils.net_utils import format_http_host

    target = normalize_ip(str(host).split("/")[0])
    url = f"https://{format_http_host(target)}/cgi-bin/luci/"
    await gui_page.goto(url, wait_until="commit", timeout=30000)
    login = gui_page.locator(LoginPageLocators.USERNAME_INPUT)
    if await login.is_visible(timeout=5000):
        await login.fill(device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
    await gui_page.wait_for_timeout(2000)


async def _navigate_flashops_reset_tab(gui_page) -> None:
    await _navigate_to_flashops(gui_page)
    reset_tab = gui_page.locator('xpath=//*[@id="maincontent"]/div/div/ul/li[2]/a').first
    if await reset_tab.is_visible(timeout=3000):
        await reset_tab.click()
        await gui_page.wait_for_timeout(800)
    else:
        await gui_page.goto((gui_page.url or "").rstrip("/") + "/reset", timeout=UITimeouts.PAGE_LOAD_MS)
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(800)


async def _gui_check_all_reset_checkboxes(gui_page, *, check: bool) -> int:
    """
    Senao reset page checkboxes select subsystems to WIPE (see jumbo_frames_assertions).
    check=True  → factory reset WITHOUT retain (IP_15 / JMB_10: enable every wipe box).
    check=False → reset WITH retain (IP_12: clear every wipe box so settings stay).
    Returns number of boxes toggled.
    """
    toggled = 0
    checkboxes = gui_page.locator("input[type='checkbox']")
    total = await checkboxes.count()
    for i in range(total):
        cb = checkboxes.nth(i)
        if not await cb.is_visible(timeout=500):
            continue
        is_checked = await cb.is_checked()
        if check and not is_checked:
            await cb.check()
            toggled += 1
        elif not check and is_checked:
            await cb.uncheck()
            toggled += 1
    return toggled


async def _gui_reset_retain_mode(gui_page) -> None:
    """Reset page: uncheck wipe boxes so LuCI performs reset WITH retain."""
    toggled = await _gui_check_all_reset_checkboxes(gui_page, check=False)
    checked = await gui_page.locator("input[type='checkbox']:checked").count()
    if checked > 0:
        for pattern in ("reset", "erase", "clear", "wipe", "factory"):
            labels = gui_page.locator(f"label:text-matches('{pattern}', 'i')")
            count = await labels.count()
            for i in range(count):
                label = labels.nth(i)
                cb = label.locator("input[type='checkbox']").first
                if await cb.count() == 0:
                    continue
                if await cb.is_visible(timeout=500) and await cb.is_checked():
                    await cb.uncheck()
                    toggled += 1
    checked = await gui_page.locator("input[type='checkbox']:checked").count()
    if checked > 0:
        pytest.fail(
            f"IP_12: {checked} reset/wipe checkbox(es) still enabled — "
            "would factory-reset without retaining configuration"
        )


async def _gui_reset_factory_wipe_mode(gui_page) -> None:
    """Reset page: check all wipe boxes for factory reset WITHOUT retain (IP_15)."""
    await _gui_check_all_reset_checkboxes(gui_page, check=True)


async def _gui_upgrade_keep_settings(gui_page) -> None:
    """Upgrade tab: enable keep-settings boxes before firmware flash (IP_35)."""
    toggled = await _gui_check_all_reset_checkboxes(gui_page, check=True)
    if toggled == 0:
        for pattern in ("keep", "retain", "settings", "preserve", "config"):
            labels = gui_page.locator(f"label:text-matches('{pattern}', 'i')")
            count = await labels.count()
            for i in range(count):
                label = labels.nth(i)
                cb = label.locator("input[type='checkbox']").first
                if await cb.count() == 0:
                    continue
                if await cb.is_visible(timeout=500) and not await cb.is_checked():
                    await cb.check()


async def _gui_trigger_reset(gui_page, *, wait_seconds: int) -> None:
    async def _accept_dialog(dialog):
        await dialog.accept()

    gui_page.once("dialog", _accept_dialog)
    reset_btn = gui_page.locator('xpath=//*[@id="reset"]/input').first
    if not await reset_btn.is_visible(timeout=3000):
        reset_btn = gui_page.locator(
            "input[value='Perform Reset']:visible, "
            "input[value*='Reset' i]:visible, "
            "button:has-text('Reset'):visible"
        ).first
    await reset_btn.wait_for(state="visible", timeout=15000)
    await reset_btn.click()
    await asyncio.sleep(wait_seconds)


async def _cli_factory_reset_on_ssh(
    ssh: AsyncGenericDriver,
    cfg: dict[str, Any],
    *,
    retain_all: bool,
    notes: list[str] | None = None,
) -> None:
    """
    Senao factory reset via SSH:
      ucidyn set tftp.retip.retainip "<value>"
      /usr/sbin/factory_reset.sh

    retainip=0 → reset all (no retain, IP_15)
    retainip=7 → retain all (IP_12)
    """
    if retain_all:
        value = int(cfg.get("factory_reset_retainip_all", 7))
        label = "retain all"
    else:
        value = int(cfg.get("factory_reset_retainip_none", 0))
        label = "reset all"
    key = str(cfg.get("factory_reset_ucidyn_key", "tftp.retip.retainip")).strip()
    script = str(cfg.get("factory_reset_script", "/usr/sbin/factory_reset.sh")).strip()
    await _ssh_run(ssh, f'ucidyn set {key} "{value}"')
    verify = (await _ssh_run(ssh, f"ucidyn get {key} 2>/dev/null || uci get {key} 2>/dev/null")).strip()
    if verify and str(value) not in verify:
        pytest.fail(f"factory reset retainip not applied (expected {value}, got {verify!r})")
    if notes is not None:
        notes.append(f"factory reset ({label}): ucidyn set {key}={value}, running {script}")
    try:
        await ssh.send_command(script, timeout_ops=15)
    except Exception:
        pass


async def _gui_factory_reset(gui_page, cfg: dict[str, Any]) -> None:
    await _navigate_flashops_reset_tab(gui_page)
    await _gui_reset_factory_wipe_mode(gui_page)
    await _gui_trigger_reset(gui_page, wait_seconds=int(cfg.get("reboot_timeout_s", 200)))


async def _gui_reset_with_retain(gui_page, cfg: dict[str, Any]) -> None:
    await _navigate_flashops_reset_tab(gui_page)
    await _gui_reset_retain_mode(gui_page)
    await _gui_trigger_reset(gui_page, wait_seconds=int(cfg.get("reboot_timeout_s", 200)))


async def _ssh_factory_reset(host: str, password: str, cfg: dict[str, Any], fallbacks: Iterable[str]) -> tuple[AsyncGenericDriver, str]:
    hosts = _all_mgmt_hosts(host, fallbacks)
    try:
        ssh = await _open_ssh(host, password)
        await _cli_factory_reset_on_ssh(ssh, cfg, retain_all=False)
        await _close_ssh(ssh)
    except Exception:
        pass
    await asyncio.sleep(5)
    return await _wait_ssh_any(
        hosts,
        password,
        timeout_s=int(cfg.get("reboot_timeout_s", 200)),
        interval_s=5,
    )


async def _read_firmware_version(ssh: AsyncGenericDriver) -> str:
    return (await _ssh_run(ssh, "cat /etc/version 2>/dev/null || uci get system.@system[0].hostname 2>/dev/null")).strip()


async def _scp_from_ssh_host(
    host: str,
    password: str,
    remote_path: str,
    local_path: Path,
) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = (
        f"sshpass -p {shlex.quote(password)} scp -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 "
        f"root@{shlex.quote(host)}:{shlex.quote(remote_path)} "
        f"{shlex.quote(str(local_path))}"
    )
    rc, out = await asyncio.to_thread(_run_local_cmd, cmd)
    if rc != 0 or not local_path.is_file():
        raise RuntimeError(f"scp {host}:{remote_path} failed: {out[:220]}")


async def _scp_to_ssh_host(
    host: str,
    password: str,
    local_path: Path,
    remote_path: str,
) -> None:
    cmd = (
        f"sshpass -p {shlex.quote(password)} scp -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 {shlex.quote(str(local_path))} "
        f"root@{shlex.quote(host)}:{shlex.quote(remote_path)}"
    )
    rc, out = await asyncio.to_thread(_run_local_cmd, cmd)
    if rc != 0:
        raise RuntimeError(f"scp to {host}:{remote_path} failed: {out[:220]}")


async def _create_device_backup_archive(
    ssh: AsyncGenericDriver,
    cfg: dict[str, Any],
    *,
    device_target: str,
    live_uci: dict[str, str] | None = None,
) -> Path:
    """
    Fresh backup before IP_15/IP_35 reset: GUI download to temp (preferred),
    SSH sysupgrade -b fallback. Pre-baked config/BTS.tar.gz is never used unless
    ip_tests.allow_stale_backup_archive is true.
    """
    from utils.device_backup import take_device_backup

    notes = cfg.get("_notes")
    stale = _resolve_backup_archive(cfg, device_target)
    require_fresh = bool(cfg.get("ip15_require_fresh_backup", True))
    if stale is not None and require_fresh and isinstance(notes, list):
        notes.append(
            f"IP_15 ignoring pre-baked archive {stale.name}; taking fresh GUI/SSH backup"
        )

    password = str(cfg.get("_password", ""))
    host = normalize_ip(str(cfg.get("_last_effective_host", "")).strip())
    if not host:
        host = normalize_ip(str(getattr(ssh, "host", "")))
    gui_page = cfg.get("_gui_page")
    gui_ip = str(cfg.get("_gui_ip", "")).strip()
    if not gui_ip and gui_page is not None:
        from utils.device_backup import gui_host_from_page

        gui_ip = gui_host_from_page(gui_page, host)
    elif not gui_ip:
        profile = cfg.get("_profile") or {}
        dut = profile.get("dut", {}) or {}
        gui_ip = normalize_ip(
            str(dut.get("local_ip") or cfg.get("_cli_fallback_ip") or host).split("/")[0]
        )
    device_creds = cfg.get("_device_creds") or {"user": "admin", "pass": password}
    role = "CPE" if device_target == "cpe" else "BTS"

    try:
        archive = await take_device_backup(
            gui_page=gui_page,
            ssh=ssh,
            device_creds=device_creds,
            gui_ip=gui_ip,
            password=password,
            role=role,
            cfg=cfg,
            live_uci=live_uci,
            notes=notes if isinstance(notes, list) else None,
        )
    except RuntimeError as exc:
        pytest.fail(f"IP_15 fresh backup failed: {exc}")

    if isinstance(notes, list) and not live_uci:
        backup_uci = _read_lan_ipv4_from_backup_archive(archive)
        notes.append(
            f"backup saved {archive.name} ({archive.stat().st_size} bytes, "
            f"LAN {backup_uci.get('address', '?')})"
        )
    return archive


async def _run_restore_backup(ctx: IpTestContext, *, v6: bool) -> None:
    """
    IP_15/IP_35: backup → factory reset WITHOUT retain → restore archive.

    Bench note: after restore, UCI/ifconfig can show the correct LAN (e.g. 192.168.2.230)
    while mgmt-VLAN ping from the lab PC (enp3s0.101) still fails — BTS may not reach
    192.168.2.10 either, but CPE 192.168.2.241 over RF can still work. A full BTS reboot
    is required to reinit bridge/ath1/transparent VLAN; ping only passed after that reboot.
    """
    cfg = ctx.cfg
    password = str(cfg.get("_password", ""))
    cfg["_notes"] = ctx.notes
    cfg["_last_effective_host"] = ctx.host
    cfg["_gui_page"] = ctx.gui_page
    cfg["_device_creds"] = {"user": "admin", "pass": password}
    profile = cfg.get("_profile") or {}
    dut = profile.get("dut", {}) or {}
    cfg["_gui_ip"] = normalize_ip(
        str(dut.get("local_ip") or cfg.get("_cli_fallback_ip") or ctx.host).split("/")[0]
    )
    before = await _read_uci_ip(ctx.ssh, v6=v6)
    fw_before = await _read_firmware_version(ctx.ssh)
    ctx.notes.append(f"IP_15 before UCI {before}, firmware {fw_before}")

    archive = await _create_device_backup_archive(
        ctx.ssh, cfg, device_target=ctx.device_target, live_uci=before
    )
    cid = ctx.case.case_id
    ctx.notes.append(f"{cid} backup archive {archive.name}")

    expected_lan_ip = normalize_ip(str(before.get("address", "")).split("/")[0])

    if ctx.gui_page is not None and ctx.device_target == "bts":
        ctx.notes.append("IP_15: factory reset WITHOUT retain (retainip=0) via CLI")
        await _cli_factory_reset_on_ssh(ctx.ssh, cfg, retain_all=False, notes=ctx.notes)
    else:
        await _close_ssh(ctx.ssh)
        ctx.ssh, ctx.host = await _ssh_factory_reset(
            ctx.host, password, cfg, ctx.fallback_hosts
        )

    profile = cfg.get("_profile") or {}
    recovery = profile.get("recovery", {}) or {}
    # After factory reset WITHOUT retain, LuCI/SSH come up on factory LAN IP (not fallback).
    factory_default_ip = normalize_ip(
        str(cfg.get("ipv4_default_address") or "192.168.2.1").split("/")[0]
    )
    restore_hosts = [factory_default_ip]
    fallback_restore = normalize_ip(
        str(recovery.get("restore_default_ip") or cfg.get("_cli_fallback_ip") or "10.0.0.1").split("/")[0]
    )
    if fallback_restore and fallback_restore not in restore_hosts:
        restore_hosts.append(fallback_restore)
    restore_hosts.extend(h for h in _all_mgmt_hosts("", ctx.fallback_hosts) if h not in restore_hosts)
    ctx.notes.append(f"IP_15: post-reset SSH/GUI via {restore_hosts[:4]}")
    await _close_ssh(ctx.ssh)
    restore_ssh, effective = await _wait_ssh_any(
        restore_hosts,
        password,
        timeout_s=int(cfg.get("reboot_timeout_s", 200)),
        interval_s=5,
    )
    ctx.ssh = restore_ssh
    ctx.host = effective

    if ctx.gui_page is not None and ctx.device_target == "bts":
        from utils.recovery_manager import RecoveryManager

        class _Bundle:
            def __init__(self, active: dict[str, Any]):
                self.active = active

        bundle = _Bundle(profile)
        rec = bundle.active.setdefault("recovery", {})
        try:
            rec["bts_restore_archive"] = str(archive.relative_to(_repo_root()))
        except ValueError:
            rec["bts_restore_archive"] = str(archive)
        manager = RecoveryManager(bundle)
        device_creds = {"user": "admin", "pass": password}
        post_ip = expected_lan_ip or normalize_ip(
            str(cfg.get("ipv4_default_address") or cfg.get("ipv4_address", "")).split("/")[0]
        )
        ok = await manager.run_profile_restore(
            gui_page=ctx.gui_page,
            device_creds=device_creds,
            role="BTS",
            post_restore_ip=post_ip or effective,
            bundle_path=archive,
            restore_gui_ip=factory_default_ip,
        )
        if not ok:
            ctx.notes.append(
                f"IP_15: GUI restore failed ({manager.metrics.last_error}) — sysupgrade -r via SSH"
            )
            remote = str(cfg.get("backup_remote_path", "/tmp/ip_test_backup.tar.gz"))
            await _scp_to_ssh_host(effective, password, archive, remote)
            try:
                await _ssh_run(restore_ssh, f"sysupgrade -r {shlex.quote(remote)}", timeout=120)
            except Exception as exc:
                pytest.fail(f"IP_15 sysupgrade restore failed on {effective}: {exc}")
            await _close_ssh(restore_ssh)
            restore_ssh, effective = await _wait_ssh_any(
                await _event_ssh_hosts(ctx),
                password,
                timeout_s=int(cfg.get("reboot_timeout_s", 200)),
                interval_s=5,
            )
            ctx.ssh = restore_ssh
            ctx.host = effective
        else:
            ctx.notes.append("IP_15: GUI restore completed")
            await _close_ssh(restore_ssh)
            restore_ssh, effective = await _wait_ssh_any(
                await _event_ssh_hosts(ctx),
                password,
                timeout_s=int(cfg.get("reboot_timeout_s", 200)),
                interval_s=5,
            )
            ctx.ssh = restore_ssh
            ctx.host = effective
    else:
        await _scp_to_ssh_host(effective, password, archive, str(cfg.get("backup_remote_path", "/tmp/ip_test_backup.tar.gz")))
        remote = str(cfg.get("backup_remote_path", "/tmp/ip_test_backup.tar.gz"))
        try:
            await _ssh_run(restore_ssh, f"sysupgrade -r {shlex.quote(remote)}", timeout=120)
        except Exception as exc:
            pytest.fail(f"IP_15 sysupgrade restore failed on {effective}: {exc}")
        await _close_ssh(restore_ssh)
        restore_ssh, effective = await _wait_ssh_any(
            await _event_ssh_hosts(ctx),
            password,
            timeout_s=int(cfg.get("reboot_timeout_s", 200)),
            interval_s=5,
        )
        ctx.ssh = restore_ssh
        ctx.host = effective

    if ctx.device_target == "bts":
        from utils.ip_case_preflight import run_ip15_post_restore_recovery

        await run_ip15_post_restore_recovery(ctx, v6=v6)

    after: dict[str, str] = {}
    fw_after = ""
    for attempt in range(4):
        try:
            after = await _read_uci_ip(ctx.ssh, v6=v6)
            fw_after = await _read_firmware_version(ctx.ssh)
            if fw_after:
                break
        except Exception as exc:
            ctx.notes.append(f"IP_15 post-restore read attempt {attempt + 1}: {exc}")
        if attempt + 1 < 4:
            await asyncio.sleep(10)
            try:
                await _reconnect_device_ssh(ctx, timeout_s=90)
            except Exception as exc:
                ctx.notes.append(f"IP_15 post-restore SSH retry: {exc}")
    ctx.notes.append(f"IP_15 after UCI {after}, firmware {fw_after or '(unreadable)'}")
    _verify_uci_ip_retained(before, after, v6=v6, case_id=cid)
    if not fw_after:
        ctx.notes.append(f"{cid}: firmware unreadable after restore (UCI retained — non-fatal)")
    elif fw_before and fw_after != fw_before:
        ctx.notes.append(f"{cid} WARN: firmware {fw_before} -> {fw_after}")
    lan_ip = ""
    if not v6:
        lan_ip = expected_lan_ip
        if not lan_ip and ctx.device_target == "bts":
            lan_ip = await _resolve_bts_lan_ipv4(ctx)
        elif not lan_ip:
            lan_ip = normalize_ip(
                str(ctx.peer_host or cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")).split("/")[0]
            )
    elif expected_lan_ip:
        lan_ip = expected_lan_ip
    if lan_ip and not lan_ip.startswith("10.0.0."):
        if v6:
            print(f"[{cid}] verifying BTS reachable via mgmt VLAN ping6 to {lan_ip}")
            max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
            vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
            bind_v6 = _lab_ping_bind_ipv6(cfg, profile)
            recovered = await _wait_for_remote_ping6_from_lab(
                vlan_if,
                lan_ip,
                wait_s=max(30, max_wait_s),
                interval_s=interval_s,
                bind_ipv6=bind_v6,
                notes=ctx.notes,
            )
            assert recovered.ok or recovered.received > 0, (
                f"{cid}: mgmt VLAN ping6 to {lan_ip} failed after restore: {recovered.raw[:200]}"
            )
            print(f"[{cid}] PASS: mgmt VLAN ping6 to BTS {lan_ip} ok after restore")
        else:
            print(f"[{cid}] verifying BTS reachable via mgmt VLAN ping to {lan_ip}")
            ping_wait = int(cfg.get("post_reboot_ping_wait_s", 90))
            await _assert_lab_ping_ipv4(
                ctx,
                lan_ip,
                wait_s=ping_wait,
                interval_s=int(cfg.get("post_reboot_remote_ping_interval_s", 10)),
            )
            print(f"[{cid}] PASS: mgmt VLAN ping to BTS {lan_ip} ok after restore")
    elif lan_ip:
        ctx.notes.append(f"{cid}: skip lab ping to fallback/mgmt {lan_ip}")
    await _assert_local_reachable(ctx, v6=v6)
    if ctx.peer_host:
        await _ping_remote_after_local(ctx, v6=v6, count=4)


async def _run_interface_flap_case(ctx: IpTestContext, *, v6: bool) -> None:
    """
    IP_14/IP_33: flap the lab PC mgmt VLAN interface toward the DUT — not br-lan on the device.
    Device stays reachable (e.g. factory 10.0.0.1) so UCI can be verified without reboot.
    """
    cfg = ctx.cfg
    password = str(cfg.get("_password", ""))
    profile = cfg.get("_profile") or {}
    pc = _lab_primary_pc(profile)
    down_s = int(cfg.get("iface_flap_down_s", 3))
    wait_s = int(cfg.get("iface_up_wait_s", 45))
    cid = ctx.case.case_id

    before = await _read_uci_ip(ctx.ssh, v6=v6)
    lan_ip = ""
    if v6:
        lan_ip = await _resolve_dut_ipv6_for_lab_ping(ctx)
    elif ctx.device_target == "bts":
        lan_ip = await _resolve_bts_lan_ipv4(ctx)
    else:
        lan_ip = normalize_ip(
            str(ctx.peer_host or cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")).split("/")[0]
        )

    if v6:
        vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
        bind_v6 = _lab_ping_bind_ipv6(cfg, profile)
    else:
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        bind_v6 = None
    vlan_id = _lab_mgmt_vlan_id(profile)
    lab_ipv4 = str(cfg.get("lab_pc_mgmt_ipv4", "192.168.2.10")).strip()
    lab_mask = str(cfg.get("lab_pc_mgmt_netmask", "255.255.255.0")).strip()
    if lan_ip:
        if v6:
            await _assert_lab_ping_ipv6(ctx, lan_ip, count=2)
        else:
            await _assert_lab_ping_ipv4(ctx, lan_ip, count=2)

    ctx.notes.append(f"{cid}: lab PC {vlan_if} down for {down_s}s (device not touched)")
    started = time.monotonic()
    ok_down = await set_lab_pc_iface_link_state(pc, vlan_if, up=False, password=password)
    if not ok_down:
        pytest.fail(f"{cid}: could not bring down lab PC {vlan_if}")
    await asyncio.sleep(down_s)

    if lan_ip:
        if v6:
            down_stats = await _ping_from_lab_pc_v6(
                vlan_if, lan_ip, count=2, per_packet_wait_s=1, bind_ipv6=bind_v6
            )
        else:
            down_stats = await _ping_from_lab_pc(vlan_if, lan_ip, count=2, per_packet_wait_s=1)
        if down_stats.ok:
            ctx.notes.append(f"{cid}: ping to {lan_ip} still ok while {vlan_if} down (optional)")
        else:
            ctx.notes.append(f"{cid}: ping to {lan_ip} failed while {vlan_if} down (expected)")

    if v6:
        from utils.ip_case_preflight import preflight_step3_lab_mgmt_ipv6

        await preflight_step3_lab_mgmt_ipv6(ctx)
        vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
        bind_v6 = _lab_ping_bind_ipv6(cfg, profile)
        ctx.notes.append(f"{cid}: recreated {vlan_if} with lab /120 IPv6 (mgmt VLAN restore)")
    else:
        vlan_if = await restore_lab_pc_mgmt_vlan_ipv4(
            pc,
            vlan_id=vlan_id,
            ipv4=lab_ipv4,
            netmask=lab_mask,
            password=password,
        )
        if not vlan_if:
            pytest.fail(
                f"{cid}: could not recreate lab PC mgmt VLAN {vlan_id} on {pc.get('mgmt_interface', 'enp3s0')}"
            )
        ctx.notes.append(f"{cid}: recreated {vlan_if} with {lab_ipv4} (ip link del/add, not link-up only)")
    await asyncio.sleep(2)

    if lan_ip:
        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        if v6:
            recovered = await _wait_for_remote_ping6_from_lab(
                vlan_if,
                lan_ip,
                wait_s=min(max(wait_s, 30), max_wait_s),
                interval_s=interval_s,
                bind_ipv6=bind_v6,
                notes=ctx.notes,
                count=1,
            )
        else:
            recovered = await _wait_for_remote_ping_from_lab(
                vlan_if,
                lan_ip,
                wait_s=min(max(wait_s, 30), max_wait_s),
                interval_s=interval_s,
                notes=ctx.notes,
                count=1,
            )
        assert recovered.ok or recovered.received > 0, (
            f"{cid}: ping to {lan_ip} did not recover after {vlan_if} up "
            f"(waited {wait_s}s): {recovered.raw[:200]}"
        )
        if v6:
            await _assert_lab_ping_ipv6(ctx, lan_ip, count=int(cfg.get("ping_count_short", 4)))
        else:
            await _assert_lab_ping_ipv4(ctx, lan_ip, count=int(cfg.get("ping_count_short", 4)))

    # LAN SSH may have dropped while lab VLAN was down; reconnect without rebooting DUT.
    try:
        await ctx.ssh.send_command("echo ip14_ok", timeout_ops=10)
    except Exception:
        ctx.notes.append(f"{cid}: reopening SSH after lab PC flap")
        await _reconnect_device_ssh(ctx, timeout_s=wait_s + 60)

    after = await _read_uci_ip(ctx.ssh, v6=v6)
    _verify_uci_ip_retained(before, after, v6=v6, case_id=cid)
    elapsed = time.monotonic() - started
    ctx.notes.append(f"{cid} lab PC {vlan_if} flap completed in {elapsed:.1f}s; device UCI retained")

    await _assert_local_reachable(ctx, v6=v6)
    if ctx.peer_host and ctx.device_target == "bts":
        await _ping_remote_after_local(
            ctx,
            v6=v6,
            count=int(cfg.get("ping_count_short", 4)),
            retries=int(cfg.get("post_reload_remote_ping_retries", 8)),
        )


def _extract_ipv4_neigh_lines(text: str, peer: str) -> list[str]:
    """Return neighbor/ARP lines that reference the peer IPv4."""
    peer = normalize_ip(peer)
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or peer not in line:
            continue
        if "lladdr" in line.lower() or re.search(r"\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b", line, re.I):
            lines.append(line)
        elif line.lower().startswith("arp") or "dev " in line:
            lines.append(line)
    return lines


def _log_ip16_arp_entry(
    ctx: IpTestContext,
    *,
    peer: str,
    neigh_get: str,
    arp_out: str,
    role: str,
) -> None:
    """Record the exact IPv4 neighbor/ARP entry in test notes and console."""
    label = role.strip() or "device"
    combined = f"{neigh_get.strip()}\n{arp_out.strip()}".strip()
    lines = _extract_ipv4_neigh_lines(combined, peer)
    if neigh_get.strip() and neigh_get.strip() not in lines:
        ng = neigh_get.strip()
        if peer in ng or "lladdr" in ng.lower():
            lines.insert(0, ng)
    mac_m = re.search(r"lladdr\s+([0-9a-f:]{11,17})", combined, re.I)
    state_m = re.search(
        r"\b(REACHABLE|STALE|DELAY|PROBE|PERMANENT|FAILED|INCOMPLETE)\b", combined, re.I
    )
    mac = mac_m.group(1) if mac_m else ""
    state = state_m.group(1) if state_m else ""
    ctx.notes.append(f"IP_16 {label} ip neigh get {peer}: {neigh_get.strip()[:300]}")
    if lines:
        for line in lines:
            ctx.notes.append(f"IP_16 {label} ARP entry: {line}")
    else:
        ctx.notes.append(f"IP_16 {label} ARP entry lines: (none matched peer in table dump)")
    if mac:
        summary = f"IP_16 {label} ARP resolved {peer} -> {mac}"
        if state:
            summary += f" ({state})"
        ctx.notes.append(summary)
        print(f"[IP_16] {summary}")
        for line in lines:
            print(f"[IP_16]   {line}")
    elif combined and not lines:
        ctx.notes.append(f"IP_16 {label} full neigh dump: {combined[:500]}")


def _read_local_ipv4_neigh(peer: str, *, dev: str = "") -> tuple[str, str]:
    """Read IPv4 neighbor/ARP for peer on the automation host (lab PC)."""
    peer_q = shlex.quote(normalize_ip(peer))
    dev_q = shlex.quote(dev) if dev else ""
    neigh_get_cmd = f"ip neigh get {peer_q} 2>&1"
    if dev:
        table_cmd = (
            f"ip -4 neigh show dev {dev_q} 2>/dev/null; "
            f"ip neigh show 2>/dev/null | grep -F {peer_q} || true; "
            f"arp -an 2>/dev/null | grep -F {peer_q} || true"
        )
    else:
        table_cmd = (
            f"ip -4 neigh show 2>/dev/null; "
            f"ip neigh show 2>/dev/null | grep -F {peer_q} || true; "
            f"arp -an 2>/dev/null | grep -F {peer_q} || true"
        )
    _, neigh_get = _run_local_cmd(neigh_get_cmd)
    _, arp_out = _run_local_cmd(table_cmd)
    return neigh_get, arp_out


async def _run_arp_resolution_case(ctx: IpTestContext) -> None:
    if not ctx.peer_host:
        pytest.skip("IP_16: no remote peer configured")
    peer = normalize_ip(ctx.peer_host)
    cfg = ctx.cfg
    count = int(cfg.get("ping_count_short", 4))
    if ctx.device_target == "bts":
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        lab_stats = await _ping_from_lab_pc(vlan_if, peer, count=count)
        if not lab_stats.ok:
            max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
            lab_stats = await _wait_for_remote_ping_from_lab(
                vlan_if, peer, wait_s=max_wait_s, interval_s=interval_s, notes=ctx.notes
            )
        if lab_stats.ok:
            ctx.notes.append(f"IP_16 lab ping to {peer} ok via {vlan_if}")
        else:
            ctx.notes.append(f"IP_16 lab ping to {peer} failed (continuing BTS ARP check)")
        lab_neigh_get, lab_arp_out = _read_local_ipv4_neigh(peer, dev=vlan_if)
        _log_ip16_arp_entry(
            ctx, peer=peer, neigh_get=lab_neigh_get, arp_out=lab_arp_out, role=f"lab PC ({vlan_if})"
        )
    try:
        await _ensure_ssh_open(ctx.ssh)
        # Populate IPv4 neighbor entry on BTS (lab ping alone does not fill BTS ip neigh).
        bts_ping = await _ping(ctx.ssh, peer, count=count, v6=False)
        ctx.notes.append(f"IP_16 BTS ping to {peer}: loss={bts_ping.loss_pct}% raw={bts_ping.raw[:120]}")
        if not bts_ping.ok:
            pytest.fail(f"IP_16 BTS cannot reach CPE {peer} for ARP: {bts_ping.raw[:300]}")
        neigh_get = await _ssh_run(
            ctx.ssh, f"ip neigh get {shlex.quote(peer)} 2>&1", timeout=15
        )
        arp_out = await _ssh_run(
            ctx.ssh,
            f"ip -4 neigh show 2>/dev/null; ip neigh show 2>/dev/null | grep -F {shlex.quote(peer)} || arp -an 2>/dev/null | grep -F {shlex.quote(peer)} || true",
            timeout=30,
        )
        combined = f"{neigh_get}\n{arp_out}"
    except Exception as exc:
        pytest.fail(f"IP_16: could not read neigh table on BTS: {exc}")
    _log_ip16_arp_entry(ctx, peer=peer, neigh_get=neigh_get, arp_out=arp_out, role="BTS")
    peer_in_table = peer in combined
    has_mac = bool(
        re.search(rf"{re.escape(peer)}[^\n]*lladdr\s+([0-9a-f:]{{11,17}})", combined, re.I)
        or re.search(r"lladdr\s+([0-9a-f:]{{11,17}})", neigh_get, re.I)
    )
    has_l2 = bool(re.search(r"\b(REACHABLE|STALE|DELAY|PROBE|PERMANENT|lladdr)\b", combined, re.I))
    assert combined.strip() and (peer_in_table or has_mac) and has_l2, (
        f"IP_16: no IPv4 ARP/neigh entry for {peer} on BTS after ping; output:\n{combined[:400]}"
    )


async def _run_static_route_case(ctx: IpTestContext) -> None:
    cfg = ctx.cfg
    if not str(cfg.get("static_route_ping_target", "")).strip() and ctx.peer_host:
        cfg["static_route_ping_target"] = normalize_ip(ctx.peer_host)
    if ctx.device_target == "bts":
        await _reconnect_device_ssh(ctx)
        await _ensure_ssh_open(ctx.ssh)
        await _ssh_run(ctx.ssh, "true", timeout=15)
    await _assert_static_route(ctx)


async def _run_reset_retain(ctx: IpTestContext, *, v6: bool) -> None:
    """
    IP_12/IP_31: factory reset WITH retain (retainip=7). No backup/restore.
    IPv4 BTS: apply non-default test LAN before reset; after reset: mgmt baseline,
    then link formation (tx=1), then verify UCI/IP retained.
    """
    cfg = ctx.cfg
    password = str(cfg.get("_password", ""))
    cid = ctx.case.case_id
    default_ip = normalize_ip(str(cfg.get("ipv4_default_address", "192.168.2.1")).split("/")[0])

    await _reconnect_device_ssh(ctx, timeout_s=60)
    lan_ip = ""
    retain_test_ip = ""
    if v6:
        from utils.ip_case_preflight import ensure_device_ipv6_configured

        await ensure_device_ipv6_configured(ctx)
        before = await _read_uci_ip(ctx.ssh, v6=True)
        lan_ip = normalize_ip(str(before.get("address", "")).split("/")[0]) or await _resolve_dut_ipv6_for_lab_ping(ctx)
    elif ctx.device_target == "bts":
        from utils.ip_case_preflight import ensure_non_default_bts_lan_ipv4

        retain_test_ip, before = await ensure_non_default_bts_lan_ipv4(ctx)
        lan_ip = retain_test_ip
    else:
        lan_ip = normalize_ip(
            str(ctx.peer_host or cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")).split("/")[0]
        )
        before = await _read_uci_ip(ctx.ssh, v6=False)

    before_addr = normalize_ip(str(before.get("address", "")).split("/")[0])

    ctx.notes.append(f"{cid}: before reset UCI={before}")
    print(
        f"[{cid}] before factory reset WITH retain: "
        f"LAN={before_addr or lan_ip} UCI={before}"
    )

    started = time.monotonic()
    retain_value = int(cfg.get("factory_reset_retainip_all", 7))
    ctx.notes.append(f"{cid}: factory reset WITH retain (retainip={retain_value}) via CLI — no backup restore")
    print(f"[{cid}] factory reset WITH retain (retainip={retain_value}), no backup/restore")

    await _cli_factory_reset_on_ssh(ctx.ssh, cfg, retain_all=True, notes=ctx.notes)
    await _close_ssh(ctx.ssh)
    timeout_s = int(cfg.get("reboot_timeout_s", 200))
    max_down = int(cfg.get("reboot_max_downtime_s", timeout_s))
    new_ssh, effective = await _wait_ssh_after_event(
        ctx,
        password,
        timeout_s=timeout_s,
        extra_hosts=await _event_ssh_hosts(ctx, lan_ip=lan_ip or before_addr or None),
    )
    downtime = time.monotonic() - started
    ctx.ssh = new_ssh
    ctx.host = effective
    print(f"[{cid}] SSH back on {effective} after reset ({int(downtime)}s downtime)")
    settle_s = int(cfg.get("post_reset_settle_s", cfg.get("post_reboot_settle_s", 150)))
    if downtime < settle_s:
        extra = settle_s - downtime
        ctx.notes.append(
            f"{cid}: waiting {extra:.0f}s more ({settle_s}s post-reset settle before ping/link)"
        )
        print(
            f"[{cid}] post-reset settle: {extra:.0f}s more "
            f"({settle_s}s total since reset before ping/link checks)"
        )
        await asyncio.sleep(extra)

    if ctx.device_target == "bts" and not v6:
        from utils.ip_case_preflight import run_post_event_testbed_recovery

        async def _ip12_retain_check() -> None:
            nonlocal after, after_addr
            after = await _read_uci_ip(ctx.ssh, v6=False)
            after_addr = normalize_ip(str(after.get("address", "")).split("/")[0])
            ctx.notes.append(f"{cid}: after reset UCI={after}")
            print(f"[{cid}] after reset UCI={after} (before link formation)")
            _verify_uci_ip_retained(before, after, v6=False, case_id=cid)
            if after_addr == default_ip:
                msg = (
                    f"{cid} FAIL: LAN IPv4 reverted to factory default {default_ip} after reset WITH retain "
                    f"(before={before_addr}) — cannot tell retain from default"
                )
                print(msg)
                pytest.fail(msg)
            if before_addr and after_addr and before_addr != after_addr:
                msg = (
                    f"{cid} FAIL: LAN IPv4 not retained after factory reset WITH retain — "
                    f"before={before_addr} after={after_addr}"
                )
                print(msg)
                pytest.fail(msg)

        after: dict[str, str] = {}
        after_addr = ""
        link_timeout = int(cfg.get("post_reboot_link_timeout_s", 120))
        cfg["_link_recovery_timeout_override"] = link_timeout
        try:
            await run_post_event_testbed_recovery(
                ctx,
                label="post-reset",
                after_mgmt_hook=_ip12_retain_check,
                require_cpe=False,
                link_formation=True,
                strict=False,
            )
        finally:
            cfg.pop("_link_recovery_timeout_override", None)
        lan_ip = after_addr or lan_ip
        if lan_ip:
            ping_wait = int(cfg.get("post_reset_ping_wait_s", cfg.get("post_reboot_ping_wait_s", 90)))
            await _assert_lab_ping_ipv4(
                ctx,
                lan_ip,
                wait_s=ping_wait,
                interval_s=int(cfg.get("post_reboot_remote_ping_interval_s", 10)),
            )
    elif ctx.device_target == "bts" and v6:
        from utils.ip_case_preflight import run_post_event_testbed_recovery_v6

        async def _ip31_retain_check() -> None:
            nonlocal after, after_addr
            after = await _read_uci_ip(ctx.ssh, v6=True)
            after_addr = normalize_ip(str(after.get("address", "")).split("/")[0])
            ctx.notes.append(f"{cid}: after reset UCI={after}")
            print(f"[{cid}] after reset UCI={after} (before link formation)")
            _verify_uci_ip_retained(before, after, v6=True, case_id=cid)

        after: dict[str, str] = {}
        after_addr = ""
        link_timeout = int(cfg.get("post_reboot_link_timeout_s", 120))
        cfg["_link_recovery_timeout_override"] = link_timeout
        try:
            await run_post_event_testbed_recovery_v6(
                ctx,
                label="post-reset",
                after_mgmt_hook=_ip31_retain_check,
                require_cpe=False,
                strict=False,
            )
        finally:
            cfg.pop("_link_recovery_timeout_override", None)
        lan_ip = after_addr or lan_ip
    else:
        after = await _read_uci_ip(ctx.ssh, v6=v6)
        after_addr = normalize_ip(str(after.get("address", "")).split("/")[0])
        ctx.notes.append(f"{cid}: after reset UCI={after}")
        print(f"[{cid}] after reset UCI={after}")
        _verify_uci_ip_retained(before, after, v6=v6, case_id=cid)
        if not v6 and before_addr and after_addr and before_addr != after_addr:
            msg = (
                f"{cid} FAIL: LAN IPv4 not retained after factory reset WITH retain — "
                f"before={before_addr} after={after_addr}"
            )
            print(msg)
            pytest.fail(msg)
        if not v6 and lan_ip:
            await _assert_post_event_reachability(
                ctx, lan_ip=lan_ip, v6=v6, downtime_s=downtime, max_downtime_s=max_down
            )
        else:
            await _assert_local_reachable(ctx, v6=v6)
            if ctx.peer_host:
                await _ping_remote_after_local(ctx, v6=v6, count=4)


async def _run_firmware_http_keep_settings(ctx: IpTestContext) -> None:
    image = Path(str(ctx.cfg.get("firmware_image_path", "")).strip()).expanduser()
    if not image.is_file():
        if not image.is_absolute():
            image = _repo_root() / image
    if not image.is_file():
        pytest.skip("set ip_tests.firmware_image_path to a local firmware image")
    if ctx.gui_page is None:
        pytest.skip("IP_35 requires BTS GUI session for HTTP upgrade")
    password = str(ctx.cfg.get("_password", ""))
    before_v4 = await _read_uci_ip(ctx.ssh, v6=False)
    before_v6 = await _read_uci_ip(ctx.ssh, v6=True)
    await _navigate_to_flashops(ctx.gui_page)
    await _open_upgrade_tab(ctx.gui_page)
    await _gui_upgrade_keep_settings(ctx.gui_page)
    file_input = ctx.gui_page.locator("input[type='file']").first
    await file_input.wait_for(state="visible", timeout=15000)
    await file_input.set_input_files(str(image.resolve()))
    async def _accept_flash_dialog(dialog):
        await dialog.accept()

    ctx.gui_page.once("dialog", _accept_flash_dialog)
    flash_btn = ctx.gui_page.locator(
        "input[value*='Flash' i]:visible, "
        "input[value*='Upgrade' i]:visible, "
        "button:has-text('Flash'):visible, "
        "button:has-text('Upgrade'):visible"
    ).first
    await flash_btn.wait_for(state="visible", timeout=15000)
    await flash_btn.click()
    await _close_ssh(ctx.ssh)
    hosts = _all_mgmt_hosts(ctx.host, ctx.fallback_hosts)
    new_ssh, effective = await _wait_ssh_any(
        hosts,
        password,
        timeout_s=int(ctx.cfg.get("reboot_timeout_s", 200)) + 120,
        interval_s=8,
    )
    ctx.ssh = new_ssh
    ctx.host = effective
    after_v4 = await _read_uci_ip(new_ssh, v6=False)
    after_v6 = await _read_uci_ip(new_ssh, v6=True)
    if before_v4.get("address"):
        assert before_v4["address"] in after_v4.get("address", "") or after_v4.get("address"), (
            f"IPv4 not retained: {before_v4} vs {after_v4}"
        )
    if before_v6.get("address"):
        assert before_v6["address"].split("/")[0] in after_v6.get("address", ""), (
            f"IPv6 not retained: {before_v6} vs {after_v6}"
        )
    await _assert_local_reachable(ctx, v6=(ctx.stack_mode == "ipv6"), count=3)
    assert await _check_web_ui_with_fallback(
        ctx.host, ctx.fallback_hosts, password, notes=ctx.notes
    )


def _ipv4_test_address_candidates(cfg: dict[str, Any]) -> list[str]:
    """Ordered LAN IPs to try for IP_01 (non-default, not already on device)."""
    raw = cfg.get("ipv4_test_address_candidates")
    if raw:
        items = [x.strip() for x in str(raw).split(",") if x.strip()]
    else:
        items = [
            str(cfg.get("ipv4_test_address", "192.168.2.230")),
            str(cfg.get("ipv4_test_address_alt", "192.168.2.240")),
            str(cfg.get("ipv4_test_address_alt2", "192.168.2.220")),
        ]
    default_ip = normalize_ip(str(cfg.get("ipv4_default_address", "192.168.2.1")).split("/")[0])
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        ip = normalize_ip(item.split("/")[0])
        if ip == default_ip or ip in seen:
            continue
        seen.add(ip)
        ordered.append(ip)
    if not ordered:
        pytest.fail("ip_tests.ipv4_test_address_candidates must list IPs other than 192.168.2.1")
    return ordered


async def _read_device_lan_ipv4s(ssh: AsyncGenericDriver, cfg: dict[str, Any]) -> set[str]:
    ips: set[str] = set()
    bridge = str(cfg.get("verify_lan_bridge", "br-lan")).strip() or "br-lan"
    uci = await _read_uci_ip(ssh, v6=False)
    if uci.get("address"):
        ips.add(normalize_ip(str(uci["address"]).split("/")[0]))
    out = await _ssh_run_raw(
        ssh,
        f"ifconfig {shlex.quote(bridge)} 2>/dev/null; "
        f"ip -4 addr show dev {shlex.quote(bridge)} 2>/dev/null",
    )
    for match in re.finditer(r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)", out):
        ips.add(normalize_ip(match.group(1)))
    return ips


async def _ipv4_test_apply_values_for_device(
    ssh: AsyncGenericDriver, cfg: dict[str, Any]
) -> dict[str, str]:
    """Pick first candidate not already on br-lan / UCI (e.g. skip .230 if already applied)."""
    configured = await _read_device_lan_ipv4s(ssh, cfg)
    for test_ip in _ipv4_test_address_candidates(cfg):
        if test_ip in configured:
            continue
        return {
            "ipv4_address": test_ip,
            "ipv4_netmask": str(cfg.get("ipv4_test_netmask") or cfg.get("ipv4_netmask", "")),
            "ipv4_gateway": str(cfg.get("ipv4_test_gateway") or cfg.get("ipv4_gateway", "")),
        }
    pytest.fail(
        "IP_01: all test LAN IPs already on device "
        f"{sorted(configured)}; add ipv4_test_address_candidates in profile"
    )


async def _verified_bts_lan_ipv4(ctx: IpTestContext) -> str:
    """Ping-verified BTS LAN IPv4 from preflight, or discover via ensure_bts_ipv4_ready."""
    cfg = ctx.cfg
    cached = normalize_ip(str(cfg.get("_preflight_bts_lan_ipv4", "")).split("/")[0])
    if cached:
        return cached
    host = await ensure_bts_ipv4_ready(ctx)
    cfg["_preflight_bts_lan_ipv4"] = host
    return host


async def _verified_cpe_lan_ipv4(ctx: IpTestContext) -> str:
    """Ping-verified CPE LAN IPv4 from preflight, or discover via ensure_cpe_ipv4_ready."""
    cfg = ctx.cfg
    cached = normalize_ip(
        str(ctx.peer_host or cfg.get("_preflight_cpe_ipv4", "")).split("/")[0]
    )
    if cached:
        return cached
    host = await ensure_cpe_ipv4_ready(ctx)
    ctx.peer_host = host
    cfg["_preflight_cpe_ipv4"] = host
    return host


async def _resolve_bts_lan_ipv4(ctx: IpTestContext) -> str:
    """Active BTS LAN IPv4 (e.g. 192.168.2.240 after IP_01), not factory 10.0.0.1."""
    cfg = ctx.cfg
    host = normalize_ip(str(ctx.host or ""))
    if host and _host_matches_stack(host, v6=False) and not host.startswith("10.0.0."):
        return host
    ips = await _read_device_lan_ipv4s(ctx.ssh, cfg)
    for ip in reversed(_ipv4_test_address_candidates(cfg)):
        if ip in ips:
            return ip
    default = normalize_ip(str(cfg.get("ipv4_default_address", "192.168.2.1")).split("/")[0])
    for ip in sorted(ips):
        if ip != default:
            return ip
    return str(cfg.get("ipv4_address", "192.168.2.1")).split("/")[0]


async def _resolve_dut_ipv6_for_lab_ping(ctx: IpTestContext) -> str:
    """Profile/UCI IPv6 used for lab ping6 toward DUT after events (IP_28–IP_34)."""
    apply = _ipv6_values_for_target(ctx)
    host = normalize_ip(apply["ipv6_address"].split("/")[0])
    if host:
        return host
    uci = await _read_uci_ip(ctx.ssh, v6=True)
    return normalize_ip(str(uci.get("address", "")).split("/")[0])


async def _wait_for_rf_link(ssh: AsyncGenericDriver, cfg: dict[str, Any]) -> bool:
    from utils.testbed_bootstrap import _link_health_bts

    profile = cfg.get("_profile") or {}
    timeout_s = int(cfg.get("link_wait_s", 120))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await _link_health_bts(ssh, profile):
            return True
        await asyncio.sleep(5)
    return False


async def _gui_set_static_ip(
    gui_page,
    cfg: dict[str, Any],
    *,
    v6: bool,
    apply_values: dict[str, str] | None = None,
) -> None:
    attach_dialog_handler(gui_page)
    await open_network_submenu(gui_page, "/network/ip")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    values = apply_values if apply_values is not None else cfg
    if v6:
        await _gui_apply_ipv6_static(gui_page, cfg, values)
        return
    proto = gui_page.locator(NetworkLocators.IPv4_PROTO).first
    if await proto.count() > 0:
        try:
            await proto.select_option(value="static")
        except Exception:
            try:
                await proto.select_option(label="Static")
            except Exception:
                pass
    fields = (
        (NetworkLocators.IPv4_ADDRESS, values.get("ipv4_address", "")),
        (NetworkLocators.IPv4_NETMASK, values.get("ipv4_netmask", "")),
        (NetworkLocators.IPv4_GATEWAY, values.get("ipv4_gateway", "")),
    )
    from utils.ui_helpers import fill_luci_input

    for locator, value in fields:
        if not value:
            continue
        element = gui_page.locator(locator).first
        if await element.count() == 0:
            continue
        await fill_luci_input(gui_page, locator, str(value))
    save = gui_page.locator(NetworkLocators.SAVE_BUTTON).first
    await apply_triple(
        gui_page,
        save,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=float(cfg.get("gui_settle_seconds", 12)),
    )


async def _cli_apply_ipv4_static(
    ssh: AsyncGenericDriver,
    apply: dict[str, str],
    cfg: dict[str, Any],
    *,
    post_apply_wait_s: int | None = None,
) -> None:
    """Apply static IPv4 on LAN via ucidyn set/apply (IP_01 SSH path; same as LuCI)."""
    ipaddr = normalize_ip(str(apply.get("ipv4_address", "")).split("/")[0])
    netmask = str(apply.get("ipv4_netmask", "255.255.255.0")).strip()
    gateway = str(apply.get("ipv4_gateway", "")).strip()
    if not ipaddr:
        pytest.fail("IP_01: no IPv4 address to apply via CLI")
    await _ucidyn_set(ssh, "network.lan.proto", "static")
    await _ucidyn_set(ssh, "network.lan.ipaddr", ipaddr)
    if netmask:
        await _ucidyn_set(ssh, "network.lan.netmask", netmask)
    if gateway:
        await _ucidyn_set(ssh, "network.lan.gateway", gateway)
    await _ucidyn_apply(ssh)
    wait = (
        int(post_apply_wait_s)
        if post_apply_wait_s is not None
        else int(cfg.get("network_reload_wait_s", 20))
    )
    if wait > 0:
        await asyncio.sleep(wait)


async def _cli_set_static_ip(
    ssh: AsyncGenericDriver,
    cfg: dict[str, Any],
    *,
    v6: bool,
    apply_values: dict[str, str] | None = None,
) -> None:
    """Fallback static-IP configuration path when GUI session is unavailable."""
    values = apply_values if apply_values is not None else cfg
    if v6:
        await _cli_apply_ipv6_static(ssh, values, cfg)
        return
    else:
        ipaddr = str(values.get("ipv4_address", "")).strip()
        netmask = str(values.get("ipv4_netmask", "")).strip()
        gw = str(values.get("ipv4_gateway", "")).strip()
        if not ipaddr:
            pytest.fail("IPv4 address missing in profile for CLI static-IP path")
        await _ucidyn_set(ssh, "network.lan.proto", "static")
        await _ucidyn_set(ssh, "network.lan.ipaddr", ipaddr)
        if netmask:
            await _ucidyn_set(ssh, "network.lan.netmask", netmask)
        if gw:
            await _ucidyn_set(ssh, "network.lan.gateway", gw)
        await _ucidyn_apply(ssh)
        await asyncio.sleep(int(cfg.get("network_reload_wait_s", 20)))


async def _cli_set_ipv4_gateway(ssh: AsyncGenericDriver, gateway: str, cfg: dict[str, Any]) -> None:
    gw = str(gateway).strip()
    if not gw:
        pytest.skip("no gateway configured for IP_04")
    await _ucidyn_set(ssh, "network.lan.gateway", gw)
    await _ucidyn_apply(ssh)
    await asyncio.sleep(int(cfg.get("network_reload_wait_s", 15)))


async def _apply_ipv4_gateway(ctx: IpTestContext, gateway: str) -> None:
    gw = str(gateway).strip()
    if not gw:
        pytest.skip("set ip_tests.ip04_gateway for IP_04")
    if ctx.gui_page is not None:
        attach_dialog_handler(ctx.gui_page)
        await open_network_submenu(ctx.gui_page, "/network/ip")
        await ctx.gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
        gw_input = ctx.gui_page.locator(NetworkLocators.IPv4_GATEWAY).first
        await gw_input.fill(gw)
        save = ctx.gui_page.locator(NetworkLocators.SAVE_BUTTON).first
        await apply_triple(
            ctx.gui_page,
            save,
            NetworkLocators.APPLY_ICON,
            NetworkLocators.CONFIRM_APPLY,
            settle_seconds=float(ctx.cfg.get("gui_settle_seconds", 12)),
        )
        ctx.notes.append(f"IP_04 gateway {gw} via GUI")
    else:
        await _cli_set_ipv4_gateway(ctx.ssh, gw, ctx.cfg)
        ctx.notes.append(f"IP_04 gateway {gw} via CLI/UCI")
    uci_gw = (await _ssh_run(ctx.ssh, RootCommands.GET_NET_GW)).strip()
    assert gw in uci_gw, f"gateway not in UCI after apply: {uci_gw}"
    ctx.notes.append(f"gateway set to {gw}")


async def _run_iperf_validation(
    ssh: AsyncGenericDriver,
    cfg: dict[str, Any],
    *,
    v6: bool,
    peer_host: str | None,
) -> str:
    """
    Run iperf validation without skipping:
    - preferred: configured iperf server
    - fallback: peer host
    - last resort: localhost (starts iperf3 server on DUT)
    """
    server = str(cfg.get("iperf_server_v6" if v6 else "iperf_server_v4", "")).strip()
    if not server and peer_host:
        server = normalize_ip(peer_host)
    duration = int(cfg.get("iperf_duration_s", 10))
    proto_flag = "-6" if v6 else ""
    attempts: list[str] = []

    async def _try_client(target: str) -> tuple[bool, str]:
        cmds = [
            f"iperf3 {proto_flag} -c {shlex.quote(target)} -t {duration}".strip(),
            f"iperf {proto_flag} -c {shlex.quote(target)} -t {duration}".strip(),
        ]
        last = ""
        for cmd in cmds:
            out = await _ssh_run(ssh, cmd, timeout=120)
            lower = out.lower()
            ok = (
                ("receiver" in lower)
                or ("sender" in lower)
                or ("bits/sec" in lower)
                or ("iperf done" in lower)
            )
            if ok:
                return True, out
            last = out
        return False, last

    if server:
        ok, out = await _try_client(server)
        attempts.append(f"server={server}")
        if ok:
            return out

    # Last-resort local loopback check: validates iperf binary/run path on DUT.
    await _ssh_run(ssh, "pkill -f \"iperf3 -s\" >/dev/null 2>&1 || true")
    await _ssh_run(ssh, "pkill -f \"iperf -s\" >/dev/null 2>&1 || true")
    _ = await _ssh_run(ssh, "(iperf3 -s -D >/dev/null 2>&1 || iperf -s -D >/dev/null 2>&1 || true)")
    loop_target = "::1" if v6 else "127.0.0.1"
    ok, out = await _try_client(loop_target)
    attempts.append(f"server={loop_target}(local)")
    if ok:
        return out
    pytest.fail(f"iperf validation failed ({', '.join(attempts)}): {out[:300]}")


async def _configure_cpe_ipv4_and_mgmt_vlan_once(
    cpe_ssh: AsyncGenericDriver,
    *,
    remote_ipv4: str,
    netmask: str,
    gateway: str,
    profile: dict[str, Any],
) -> None:
    """
    Configure CPE mgmt VLAN + static IPv4 in a single command batch,
    then one commit/apply cycle (saves time and avoids partial state).
    """
    tb = profile.get("testbed", {}) or {}
    vlan_cmds = [
        cmd
        for cmd in build_cpe_mgmtvlan_only_commands(tb)
        if "uci commit vlan" not in cmd and "/etc/init.d/network reload" not in cmd
    ]
    cmds = [
        *vlan_cmds,
        _ucidyn_set_line("network.lan.proto", "static"),
        _ucidyn_set_line("network.lan.ipaddr", normalize_ip(remote_ipv4)),
        _ucidyn_set_line("network.lan.netmask", netmask),
    ]
    if str(gateway).strip():
        cmds.append(_ucidyn_set_line("network.lan.gateway", gateway))
    cmds.extend(["uci commit vlan", "ucidyn apply"])
    await _ssh_run_raw(cpe_ssh, " && ".join(cmds), timeout=120)


async def _configure_cpe_via_secondary_pc_once(
    *,
    profile: dict[str, Any],
    password: str,
    remote_ipv4: str,
    netmask: str,
    gateway: str,
) -> None:
    """
    Configure CPE through secondary PC SSH hop in one batch:
    - CPE mgmt VLAN
    - CPE LAN proto static
    - CPE IPv4/netmask/gateway
    - single commit/reload
    """
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        pytest.skip("IP_03: secondary_pc.ssh not configured")
    if "@" in ssh_target:
        sec_user, sec_host = ssh_target.split("@", 1)
    else:
        sec_user, sec_host = "root", ssh_target
    sec_user = sec_user.strip() or "root"
    sec_host = sec_host.strip()
    if not sec_host:
        pytest.skip("IP_03: secondary_pc.ssh host is empty")

    sec_password = str(sec.get("password") or password).strip()
    cpe_password = str(password).strip()
    cpe_factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))

    await ensure_secondary_pc_cpe_hop_ready(profile, password)

    vlan_cmds = [
        cmd
        for cmd in build_cpe_mgmtvlan_only_commands(tb)
        if "uci commit vlan" not in cmd and "/etc/init.d/network reload" not in cmd
    ]
    cpe_cmds = [
        *vlan_cmds,
        _ucidyn_set_line("network.lan.proto", "static"),
        _ucidyn_set_line("network.lan.ipaddr", normalize_ip(remote_ipv4)),
        _ucidyn_set_line("network.lan.netmask", netmask),
    ]
    if str(gateway).strip():
        cpe_cmds.append(_ucidyn_set_line("network.lan.gateway", gateway))
    cpe_cmds.extend(
        [
            "uci commit vlan",
            "ucidyn apply",
            "ucidyn get network.lan.ipaddr 2>/dev/null || uci get network.lan.ipaddr 2>/dev/null",
        ]
    )
    sec_conn = AsyncGenericDriver(
        host=sec_host,
        auth_username=sec_user,
        auth_password=sec_password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await sec_conn.open()
    try:
        # 1) Validate L3 reachability from secondary PC to CPE fallback (retry after hop setup).
        ping_cmd = f"ping -c 2 -W 2 {shlex.quote(cpe_factory)}"
        ping_out = ""
        for attempt in range(1, 4):
            ping_out = await _ssh_run_raw(sec_conn, ping_cmd, timeout=20)
            if "0% packet loss" in ping_out or " 0% packet loss" in ping_out:
                break
            if attempt < 3:
                await ensure_secondary_pc_cpe_hop_ready(profile, password)
                await asyncio.sleep(2)
        else:
            raise AssertionError(f"secondary->CPE ping failed: {ping_out[:220]}")

        # 2) Validate nested SSH before pushing config.
        ssh_probe = (
            f"sshpass -p {shlex.quote(cpe_password)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=12 "
            f"root@{cpe_factory} 'echo cpe_ok'"
        )
        probe_out = ""
        for attempt in range(1, 4):
            probe_out = await _ssh_run_raw(sec_conn, ssh_probe, timeout=25)
            if "cpe_ok" in probe_out:
                break
            if attempt < 3:
                await ensure_secondary_pc_cpe_hop_ready(profile, password)
                await asyncio.sleep(3)
        else:
            raise AssertionError(f"secondary->CPE SSH probe failed: {probe_out[:220]}")

        # 3) Push all CPE config in one pass, then verify IPv4.
        for cmd in cpe_cmds:
            remote_cmd = (
                f"sshpass -p {shlex.quote(cpe_password)} "
                "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
                f"root@{cpe_factory} {shlex.quote(cmd)}"
            )
            _ = await _ssh_run_raw(sec_conn, remote_cmd, timeout=35)

        verify_cmd = (
            f"sshpass -p {shlex.quote(cpe_password)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
            f"root@{cpe_factory} 'uci get network.lan.ipaddr 2>/dev/null'"
        )
        verify_out = await _ssh_run_raw(sec_conn, verify_cmd, timeout=20)
        if normalize_ip(remote_ipv4) not in verify_out:
            raise AssertionError(
                f"CPE IPv4 not applied via secondary hop: expected {remote_ipv4}, got {verify_out[:220]}"
            )
    finally:
        await _close_ssh(sec_conn)


async def _configure_cpe_ipv6_via_secondary_pc_once(
    *,
    profile: dict[str, Any],
    password: str,
    ipv6_address: str,
    ipv6_gateway: str = "",
) -> None:
    """
    Configure CPE static IPv6 through secondary PC SSH hop (factory 10.0.0.1).
    Mirrors IPv4 ``_configure_cpe_via_secondary_pc_once`` for IP_18 / ensure_cpe_ipv6_ready.
    """
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        pytest.fail("CPE IPv6: testbed.secondary_pc.ssh not configured")
    if "@" in ssh_target:
        sec_user, sec_host = ssh_target.split("@", 1)
    else:
        sec_user, sec_host = "root", ssh_target
    sec_user = sec_user.strip() or "root"
    sec_host = sec_host.strip()
    sec_password = str(sec.get("password") or password).strip()
    cpe_password = str(password).strip()
    cpe_factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))

    print(
        f"[cpe-v6] secondary PC {sec_user}@{sec_host} → CPE {cpe_factory}: "
        f"ip6addr={ipv6_address} ip6gw={ipv6_gateway or '(none)'}"
    )
    await ensure_secondary_pc_cpe_hop_ready(profile, password)

    vlan_cmds = [
        cmd
        for cmd in build_cpe_mgmtvlan_only_commands(tb)
        if "uci commit vlan" not in cmd and "/etc/init.d/network reload" not in cmd
    ]
    cpe_cmds = [
        *vlan_cmds,
        _ucidyn_set_line("network.lan.ip6proto", "static"),
        _ucidyn_set_line("network.lan.ip6addr", str(ipv6_address).strip()),
    ]
    if str(ipv6_gateway).strip():
        cpe_cmds.append(_ucidyn_set_line("network.lan.ip6gw", str(ipv6_gateway).strip()))
    cpe_cmds.extend(
        [
            "uci commit vlan",
            "ucidyn apply",
            "ucidyn get network.lan.ip6addr 2>/dev/null || uci get network.lan.ip6addr 2>/dev/null",
        ]
    )

    sec_conn = AsyncGenericDriver(
        host=sec_host,
        auth_username=sec_user,
        auth_password=sec_password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await sec_conn.open()
    try:
        ping_cmd = f"ping -c 2 -W 2 {shlex.quote(cpe_factory)}"
        ping_out = await _ssh_run_raw(sec_conn, ping_cmd, timeout=20)
        if "0% packet loss" not in ping_out and " 0% packet loss" not in ping_out:
            raise AssertionError(f"secondary→CPE ping failed: {ping_out[:220]}")

        ssh_probe = (
            f"sshpass -p {shlex.quote(cpe_password)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "
            f"root@{cpe_factory} 'echo cpe_ok'"
        )
        probe_out = await _ssh_run_raw(sec_conn, ssh_probe, timeout=25)
        if "cpe_ok" not in probe_out:
            raise AssertionError(f"secondary→CPE SSH probe failed: {probe_out[:220]}")

        for cmd in cpe_cmds:
            remote_cmd = (
                f"sshpass -p {shlex.quote(cpe_password)} "
                "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
                f"root@{cpe_factory} {shlex.quote(cmd)}"
            )
            await _ssh_run_raw(sec_conn, remote_cmd, timeout=45)

        verify_cmd = (
            f"sshpass -p {shlex.quote(cpe_password)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
            f"root@{cpe_factory} 'uci get network.lan.ip6addr 2>/dev/null'"
        )
        verify_out = await _ssh_run_raw(sec_conn, verify_cmd, timeout=20)

        if not ipv6_equal(ipv6_address, verify_out):
            raise AssertionError(
                f"CPE IPv6 not applied via secondary hop: want {ipv6_address}, got {verify_out[:220]}"
            )
        print(f"[cpe-v6] remote CPE UCI ip6addr confirmed: {verify_out.strip()[:80]}")
    finally:
        await _close_ssh(sec_conn)


async def ensure_cpe_ipv6_ready(
    ctx: IpTestContext,
    *,
    apply: dict[str, str] | None = None,
    force: bool = False,
) -> str:
    """Ensure CPE LAN has profile static IPv6 (secondary PC → CPE factory SSH)."""
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    password = str(cfg.get("_password", ""))
    cpe_apply = apply or _ipv6_values_for_role(cfg, profile, device_target="cpe")
    addr = cpe_apply["ipv6_address"]
    addr_host = normalize_ip(addr.split("/")[0])
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    cpe_factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))

    if not force:
        try:
            hop, _, _ = await open_cpe_ssh_via_secondary_pc(
                profile,
                password,
                cpe_lan=cpe_factory,
                cpe_factory=cpe_factory,
            )
            uci = await _read_uci_ip(hop, v6=True)
            await _close_ssh(hop)
            if ipv6_equal(addr, str(uci.get("address", ""))):
                msg = f"CPE IPv6 already configured: {uci.get('address', '')}"
                print(f"[cpe-v6] {msg}")
                ctx.notes.append(msg)
                cfg["_cpe_ipv6_configured"] = addr_host
                return addr_host
        except Exception as exc:
            ctx.notes.append(f"CPE IPv6 precheck: {exc}")

    await _configure_cpe_ipv6_via_secondary_pc_once(
        profile=profile,
        password=password,
        ipv6_address=addr,
        ipv6_gateway=str(cpe_apply.get("ipv6_gateway", "")),
    )
    await asyncio.sleep(int(cfg.get("network_reload_wait_s", 20)))

    hop, _, _ = await open_cpe_ssh_via_secondary_pc(
        profile,
        password,
        cpe_lan=cpe_factory,
        cpe_factory=cpe_factory,
    )
    try:
        uci = await _read_uci_ip(hop, v6=True)
        if not ipv6_equal(addr, str(uci.get("address", ""))):
            pytest.fail(f"CPE IPv6 verify failed after apply: want {addr}, uci={uci}")
        ip6_out = await _ssh_run_raw(
            hop, "ip -6 addr show dev br-lan 2>/dev/null; ip -6 addr show"
        )
        ctx.notes.append(
            f"CPE IPv6 configured on remote device: UCI={uci.get('address', '')} "
            f"ip6={'ok' if ipv6_addr_in_text(addr_host, ip6_out) else 'pending'}"
        )
        print(f"[cpe-v6] remote CPE ready at {addr_host}")
    finally:
        await _close_ssh(hop)

    cfg["_cpe_ipv6_configured"] = addr_host
    return addr_host


async def _cold_reboot_bts(ctx: IpTestContext, *, label: str = "reboot") -> None:
    """Reboot BTS, wait for SSH, then post_reboot_settle_s before ping/link checks."""
    password = str(ctx.cfg.get("_password", ""))
    started = time.monotonic()
    try:
        await ctx.ssh.send_command("reboot", timeout_ops=5)
    except Exception:
        pass
    await _close_ssh(ctx.ssh)
    await asyncio.sleep(5)
    ssh, effective = await _wait_ssh_any(
        await _event_ssh_hosts(ctx),
        password,
        timeout_s=int(ctx.cfg.get("reboot_timeout_s", 200)),
        interval_s=5,
    )
    ctx.ssh = ssh
    ctx.host = effective
    settle_s = int(ctx.cfg.get("post_reboot_settle_s", 150))
    downtime = time.monotonic() - started
    if downtime < settle_s:
        extra = settle_s - downtime
        ctx.notes.append(f"{label}: SSH on {effective} after {downtime:.0f}s — waiting {extra:.0f}s settle")
        print(f"[{label}] post-reboot settle: {extra:.0f}s ({settle_s}s total)")
        await asyncio.sleep(extra)
    else:
        ctx.notes.append(f"{label}: SSH on {effective} after {downtime:.0f}s (>= {settle_s}s settle)")


async def recover_bts_lab_ping_via_reboot(
    ctx: IpTestContext,
    *,
    label: str = "ping-recovery",
) -> None:
    """
    Last resort when mgmt-VLAN ping to BTS LAN fails but UCI/br-lan looks correct.
    Cold reboot + post-event recovery (bench apply/restore bug).
    """
    if ctx.device_target != "bts":
        return
    cfg = ctx.cfg
    if not cfg.get("enable_reboot_ping_recovery", True):
        return
    cid = ctx.case.case_id if getattr(ctx, "case", None) else label
    ctx.notes.append(f"{cid}: lab ping failed — last resort cold reboot for ping recovery")
    print(f"[{cid}] last resort: cold BTS reboot for lab ping recovery")
    await _cold_reboot_bts(ctx, label=f"{cid}-{label}")
    from utils.ip_case_preflight import run_post_event_testbed_recovery

    link_timeout = int(cfg.get("post_reboot_link_timeout_s", 120))
    cfg["_link_recovery_timeout_override"] = link_timeout
    try:
        await run_post_event_testbed_recovery(
            ctx,
            label=f"{cid}-{label}",
            require_cpe=False,
            strict=False,
            link_formation=True,
        )
    finally:
        cfg.pop("_link_recovery_timeout_override", None)


async def ensure_bts_ipv4_ready(ctx: IpTestContext) -> str:
    """
    Discover BTS LAN IPv4 by lab ping (UCI may differ from reachable address).
    """
    cfg = ctx.cfg

    async def _attempt() -> str:
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        candidates: list[str] = []
        seen: set[str] = set()

        def _bts_ping_candidate(ip: str) -> bool:
            clean = normalize_ip(str(ip).split("/")[0])
            if not clean or clean.startswith("10.0.0."):
                return False
            try:
                return isinstance(ipaddress.ip_address(clean), ipaddress.IPv4Address)
            except ValueError:
                return False

        try:
            await _reconnect_device_ssh(ctx, timeout_s=60)
        except Exception as exc:
            ctx.notes.append(f"precheck: BTS SSH reconnect skipped: {exc}")

        default_ip = normalize_ip(
            str(cfg.get("ipv4_default_address", "192.168.2.1")).split("/")[0]
        )

        def _add_candidate(ip: str, *, front: bool = False) -> None:
            clean = normalize_ip(str(ip).split("/")[0])
            if clean and clean not in seen and _bts_ping_candidate(clean):
                seen.add(clean)
                if front:
                    candidates.insert(0, clean)
                else:
                    candidates.append(clean)

        chain = cfg.get("_ip_suite_chain") or {}
        cached_bts = normalize_ip(
            str(
                cfg.get("_preflight_bts_lan_ipv4")
                or chain.get("bts_lan_ipv4", "")
            ).split("/")[0]
        )
        if cached_bts:
            _add_candidate(cached_bts, front=True)

        # Then profile test LAN IPs (e.g. 192.168.2.240 after IP_01).
        for ip in reversed(_ipv4_test_address_candidates(cfg)):
            _add_candidate(ip)

        try:
            lan_ips = await _read_device_lan_ipv4s(ctx.ssh, cfg)
        except Exception as exc:
            ctx.notes.append(f"precheck: skip UCI LAN read over SSH: {exc}")
            lan_ips = []
        for ip in sorted(lan_ips):
            if normalize_ip(ip) != default_ip:
                _add_candidate(ip)
        for ip in lan_ips:
            if normalize_ip(ip) == default_ip:
                _add_candidate(ip)

        _add_candidate(str(cfg.get("ipv4_address", "")).split("/")[0])
        ssh_host = normalize_ip(str(ctx.host).split("/")[0])
        if ssh_host and _bts_ping_candidate(ssh_host):
            _add_candidate(ssh_host)
        if default_ip:
            _add_candidate(default_ip)

        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        per_candidate_wait = min(int(cfg.get("bts_precheck_ping_wait_s", 20)), max_wait_s)
        last_raw = ""
        ping_kw = _lab_ping_kwargs(cfg)
        for target in candidates:
            stats = await _ping_from_lab_pc(
                vlan_if, target, count=1, per_packet_wait_s=1, **ping_kw
            )
            if stats.ok:
                ctx.notes.append(f"precheck BTS IPv4 {target} via {vlan_if} ok")
                return target
            stats = await _wait_for_remote_ping_from_lab(
                vlan_if,
                target,
                wait_s=per_candidate_wait,
                interval_s=interval_s,
                notes=ctx.notes,
                **ping_kw,
            )
            if stats.ok:
                ctx.notes.append(
                    f"precheck BTS IPv4 {target} via {vlan_if} ok (after wait)"
                )
                return target
            last_raw = stats.raw

        raise AssertionError(
            f"BTS IPv4 pre-check failed for {candidates} via {vlan_if} "
            f"(mgmt VLAN ping from {ctx.cfg.get('lab_pc_mgmt_ipv4', '192.168.2.10')}): {last_raw[:220]}. "
            "Confirm BTS vlan.ath1.mode=transparent and vlan.ath1.mgmtvlan=101 before br-lan ping."
        )

    try:
        return await _attempt()
    except AssertionError as first_exc:
        if not cfg.get("enable_link_recovery", True):
            raise

        # If ping just dropped (bench is sometimes slow after IP changes), try
        # a minimal recovery and re-run the ping verification once.
        ctx.notes.append(f"{first_exc} — running RF+network recovery for BTS precheck")
        try:
            from utils.ip_link_recovery import ensure_testbed_link_ready

            await ensure_testbed_link_ready(ctx)
        except Exception as exc:
            ctx.notes.append(f"BTS precheck: RF recovery warn: {exc}")

        try:
            await _trigger_network_reload_sync(ctx.ssh, cfg)
        except Exception as exc:
            ctx.notes.append(f"BTS precheck: network reload warn: {exc}")

        try:
            return await _attempt()
        except AssertionError as second_exc:
            if not cfg.get("enable_reboot_ping_recovery", True):
                raise second_exc from first_exc
            ctx.notes.append(f"{second_exc} — trying cold reboot ping recovery")
            await recover_bts_lab_ping_via_reboot(ctx, label="bts-precheck")
            return await _attempt()


async def discover_cpe_ipv4_addresses(ctx: IpTestContext) -> list[str]:
    """
    Build ordered CPE LAN IPv4 candidates:
    1) live UCI on CPE (secondary SSH hop)
    2) BTS DHCP/neighbor tables
    3) optional profile hints
    """
    from utils.cpe_discovery import (
        discover_cpe_ipv4s_from_bts,
        is_valid_cpe_lan_ipv4,
        read_cpe_lan_ipv4_via_secondary_hop,
    )

    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    password = str(cfg.get("_password", ""))
    found: list[str] = []
    seen: set[str] = set()
    tb = profile.get("testbed", {}) or {}
    factory = normalize_ip(str(tb.get("secondary_pc", {}).get("cpe_factory_ipv4", "10.0.0.1")))

    def _add(ip: str | None, source: str) -> None:
        if not ip:
            return
        clean = normalize_ip(str(ip).split("/")[0])
        if not clean or clean in seen:
            return
        if not is_valid_cpe_lan_ipv4(clean, factory_ipv4=factory):
            return
        seen.add(clean)
        found.append(clean)
        ctx.notes.append(f"CPE IPv4 candidate {clean} ({source})")

    try:
        live = await read_cpe_lan_ipv4_via_secondary_hop(profile, password)
        _add(live, "CPE UCI via secondary hop")
    except Exception as exc:
        ctx.notes.append(f"CPE UCI read skipped: {exc}")

    if ctx.device_target == "bts" and ctx.ssh is not None:
        try:
            bts_lan = await _resolve_bts_lan_ipv4(ctx)
            exclude = [bts_lan, ctx.host, factory]
            for ip in await discover_cpe_ipv4s_from_bts(ctx.ssh, cfg, exclude_ipv4s=exclude):
                _add(ip, "BTS DHCP/neigh")
        except Exception as exc:
            ctx.notes.append(f"BTS-side CPE IPv4 discovery skipped: {exc}")

    for ip in _cpe_ipv4_hint_candidates(cfg, profile):
        _add(ip, "profile hint")

    return found


async def _probe_cpe_ipv4_from_lab(ctx: IpTestContext, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    chain = ctx.cfg.get("_ip_suite_chain") or {}
    cached = normalize_ip(
        str(
            ctx.peer_host
            or ctx.cfg.get("_preflight_cpe_ipv4")
            or chain.get("cpe_lan_ipv4", "")
        ).split("/")[0]
    )
    ordered = list(candidates)
    if cached and cached in ordered:
        ordered.remove(cached)
        ordered.insert(0, cached)
    elif cached and cached not in ordered:
        ordered.insert(0, cached)
    candidates = ordered
    vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
    max_wait_s, interval_s = _remote_ping_wait_settings(ctx.cfg)
    for ip in candidates:
        stats = await _ping_from_lab_pc(vlan_if, ip, count=1, per_packet_wait_s=1)
        if stats.ok:
            ctx.notes.append(f"CPE {ip} ping ok via {vlan_if}")
            return ip
        stats = await _wait_for_remote_ping_from_lab(
            vlan_if,
            ip,
            wait_s=min(max_wait_s, 30),
            interval_s=interval_s,
            notes=ctx.notes,
            count=1,
        )
        if stats.ok:
            ctx.notes.append(f"CPE {ip} ping ok via {vlan_if} (after wait)")
            return ip
    return None


async def ensure_cpe_ipv4_ready(ctx: IpTestContext) -> str:
    """
    Discover CPE LAN IPv4 dynamically, ping-verify from lab PC, configure only if unreachable.
    """
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    password = str(cfg.get("_password", ""))

    async def _attempt() -> str:
        candidates = await discover_cpe_ipv4_addresses(ctx)
        if not candidates:
            raise AssertionError(
                "CPE IPv4 discovery found no candidates (check link, secondary PC hop, or profile hints)"
            )

        reachable = await _probe_cpe_ipv4_from_lab(ctx, candidates)
        if reachable:
            ctx.notes.append(f"precheck CPE IPv4 {reachable} (discovered, ping verified)")
            cfg["_discovered_cpe_ipv4"] = reachable
            return reachable

        configure_ip = candidates[0]
        ctx.notes.append(f"precheck CPE {configure_ip} not pingable — configuring via secondary hop")
        await ensure_secondary_pc_cpe_hop_ready(profile, password)
        await _configure_cpe_via_secondary_pc_once(
            profile=profile,
            password=password,
            remote_ipv4=configure_ip,
            netmask=str(cfg.get("ipv4_netmask", "255.255.255.0")).strip(),
            gateway=str(cfg.get("ipv4_gateway", "")).strip(),
        )
        reachable = await _probe_cpe_ipv4_from_lab(ctx, [configure_ip])
        if not reachable:
            raise AssertionError(
                f"CPE IPv4 {configure_ip} not reachable via lab mgmt VLAN after secondary-hop configure"
            )
        ctx.notes.append(f"precheck CPE configured and reachable at {configure_ip}")
        cfg["_discovered_cpe_ipv4"] = configure_ip
        return configure_ip

    try:
        return await _attempt()
    except AssertionError as first_exc:
        if not cfg.get("enable_link_recovery", True):
            raise
        from utils.ip_link_recovery import ensure_testbed_link_ready

        ctx.notes.append(f"CPE precheck failed ({first_exc}) — running RF link recovery")
        link_ok = await ensure_testbed_link_ready(ctx)
        if not link_ok:
            raise
        return await _attempt()


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


class CpeSecondaryHopDriver:
    """Run CPE shell commands through secondary PC → CPE factory SSH hop."""

    host = "cpe-via-secondary"
    port = 22

    def __init__(
        self,
        sec_conn: AsyncGenericDriver,
        *,
        cpe_factory: str,
        cpe_user: str,
        cpe_password: str,
        label: str,
    ) -> None:
        self._sec = sec_conn
        self._factory = normalize_ip(cpe_factory)
        self._user = (cpe_user or "root").strip() or "root"
        self._password = cpe_password
        self._label = label

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        await _close_ssh(self._sec)

    async def send_command(self, command: str, *, timeout_ops: int = 60):
        hop = (
            f"sshpass -p {shlex.quote(self._password)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
            f"{shlex.quote(self._user)}@{self._factory} {shlex.quote(command)}"
        )
        return await self._sec.send_command(hop, timeout_ops=timeout_ops)


async def _probe_cpe_ssh_via_secondary(
    sec_conn: AsyncGenericDriver,
    profile: dict[str, Any],
    cpe_factory: str,
) -> tuple[str, str]:
    """Return (user, password) for nested SSH secondary PC → CPE factory."""
    from utils.link_formation import _cpe_ssh_credential_attempts

    factory = normalize_ip(cpe_factory)
    last_out = ""
    for cpe_user, cpe_pass in _cpe_ssh_credential_attempts(profile):
        probe = (
            f"sshpass -p {shlex.quote(cpe_pass)} "
            "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "
            f"{shlex.quote(cpe_user)}@{factory} 'echo cpe_ok'"
        )
        out = await _ssh_run_raw(sec_conn, probe, timeout=25)
        if "cpe_ok" in out:
            return cpe_user, cpe_pass
        last_out = out
    hint = ""
    if "sshpass" in last_out.lower() and "not found" in last_out.lower():
        hint = " (install sshpass on secondary PC)"
    raise ConnectionError(f"secondary→CPE SSH probe failed: {last_out[:220]}{hint}")


async def open_cpe_ssh_via_secondary_pc(
    profile: dict[str, Any],
    password: str,
    *,
    cpe_lan: str,
    cpe_factory: str,
) -> tuple[CpeSecondaryHopDriver, str, str]:
    """Open CPE CLI via secondary PC hop (10.0.0.11 → 10.0.0.1 CPE factory)."""
    await ensure_secondary_pc_cpe_hop_ready(profile, password)
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        raise RuntimeError("testbed.secondary_pc.ssh is not configured")
    if "@" in ssh_target:
        sec_user, sec_host = ssh_target.split("@", 1)
    else:
        sec_user, sec_host = "root", ssh_target
    sec_pass = str(sec.get("password") or password).strip()
    sec_conn = AsyncGenericDriver(
        host=sec_host.strip(),
        auth_username=(sec_user.strip() or "root"),
        auth_password=sec_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await sec_conn.open()
    factory = normalize_ip(cpe_factory)
    try:
        cpe_user, cpe_pass = await _probe_cpe_ssh_via_secondary(
            sec_conn, profile, factory
        )
    except ConnectionError:
        await _close_ssh(sec_conn)
        raise
    label = f"secondary→CPE({cpe_user}@{factory})"
    driver = CpeSecondaryHopDriver(
        sec_conn,
        cpe_factory=factory,
        cpe_user=cpe_user,
        cpe_password=cpe_pass,
        label=label,
    )
    effective = normalize_ip(cpe_lan) if cpe_lan else factory
    return driver, effective, label


async def _read_uptime_s(ssh: AsyncGenericDriver) -> float:
    raw = await _ssh_run(ssh, RootCommands.GET_UPTIME)
    try:
        return float(raw.split()[0])
    except (IndexError, ValueError):
        return 0.0


def _verify_uci_ip_retained(
    before: dict[str, str],
    after: dict[str, str],
    *,
    v6: bool,
    case_id: str = "IP_12",
) -> None:
    """Fail with explicit message if factory reset WITH retain did not keep UCI."""
    stack = "IPv6" if v6 else "IPv4"
    failures: list[str] = []
    for key, val in before.items():
        if not val or val.startswith("uci:"):
            continue
        got = after.get(key, "")
        if v6 and key == "address":
            if not ipv6_equal(val, got):
                failures.append(f"{key}: before={val!r} after={got!r}")
            continue
        needle = val.split("/")[0] if key == "address" else val
        if needle not in got and got != val:
            failures.append(f"{key}: before={val!r} after={got!r}")
    if failures:
        msg = (
            f"{case_id} FAIL: {stack} settings NOT retained after factory reset WITH retain — "
            + "; ".join(failures)
        )
        print(msg)
        pytest.fail(msg)
    before_addr = str(before.get("address", "")).split("/")[0]
    after_addr = str(after.get("address", "")).split("/")[0]
    event = "reboot" if case_id in ("IP_09", "IP_28") else "factory reset WITH retain"
    print(
        f"{case_id} PASS: {stack} retained after {event} "
        f"(LAN {after_addr or before_addr})"
    )


async def _event_ssh_hosts(ctx: IpTestContext, *, lan_ip: str | None = None) -> list[str]:
    cfg = ctx.cfg
    hosts: list[str] = []
    if lan_ip:
        hosts.append(normalize_ip(lan_ip))
    if ctx.device_target == "bts":
        try:
            hosts.append(await _resolve_bts_lan_ipv4(ctx))
        except Exception:
            pass
    elif ctx.device_target == "cpe":
        peer = str(ctx.peer_host or cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")).strip()
        if peer:
            hosts.append(normalize_ip(peer.split("/")[0]))
    hosts.extend(_all_mgmt_hosts(ctx.host, ctx.fallback_hosts))
    return _ordered_unique_hosts(*hosts)


async def _wait_ssh_after_event(
    ctx: IpTestContext,
    password: str,
    *,
    timeout_s: int,
    extra_hosts: Iterable[str] = (),
) -> tuple[AsyncGenericDriver, str]:
    hosts = _ordered_unique_hosts(*await _event_ssh_hosts(ctx), *extra_hosts)
    if isinstance(ctx.ssh, CpeSecondaryHopDriver):
        profile = ctx.cfg.get("_profile") or {}
        tb = profile.get("testbed", {}) or {}
        sec = tb.get("secondary_pc", {}) or {}
        factory = normalize_ip(str(sec.get("cpe_factory_ipv4", "10.0.0.1")))
        await ensure_secondary_pc_cpe_hop_ready(profile, password)
        await asyncio.sleep(5)
        driver, effective, _label = await open_cpe_ssh_via_secondary_pc(
            profile,
            password,
            cpe_lan=str(ctx.host),
            cpe_factory=factory,
        )
        return driver, effective
    return await _wait_ssh_any(hosts, password, timeout_s=timeout_s, interval_s=5)


async def _assert_lab_ping_ipv4(
    ctx: IpTestContext,
    target: str,
    *,
    count: int | None = None,
    wait_s: int = 0,
    interval_s: int = 5,
    allow_reboot_recovery: bool = True,
) -> PingStats:
    vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
    host = normalize_ip(target)
    n = count or int(ctx.cfg.get("ping_count_short", 4))

    async def _ping_once() -> PingStats:
        if wait_s > 0:
            return await _wait_for_remote_ping_from_lab(
                vlan_if,
                host,
                wait_s=wait_s,
                interval_s=interval_s,
                count=n,
                notes=ctx.notes,
            )
        return await _ping_from_lab_pc(vlan_if, host, count=n)

    stats = await _ping_once()
    if stats.ok:
        ctx.notes.append(f"lab ping {host} via {vlan_if}: loss={stats.loss_pct}%")
        return stats

    if (
        allow_reboot_recovery
        and ctx.device_target == "bts"
        and ctx.cfg.get("enable_reboot_ping_recovery", True)
    ):
        cid = ctx.case.case_id if getattr(ctx, "case", None) else "lab-ping"
        ctx.notes.append(
            f"{cid}: lab ping {host} failed — trying cold reboot recovery before re-ping"
        )
        await recover_bts_lab_ping_via_reboot(ctx, label="lab-ping")
        stats = await _ping_once()
        if stats.ok:
            ctx.notes.append(
                f"lab ping {host} via {vlan_if} ok after reboot recovery: loss={stats.loss_pct}%"
            )
            return stats

    assert stats.ok, f"lab PC ping {host} via {vlan_if} failed: {stats.raw[:300]}"
    ctx.notes.append(f"lab ping {host} via {vlan_if}: loss={stats.loss_pct}%")
    return stats


async def _assert_post_event_reachability(
    ctx: IpTestContext,
    *,
    lan_ip: str,
    v6: bool,
    downtime_s: float,
    max_downtime_s: int,
) -> None:
    assert downtime_s <= max_downtime_s, (
        f"{ctx.case.case_id}: downtime {downtime_s:.0f}s exceeds limit {max_downtime_s}s"
    )
    ctx.notes.append(f"downtime {downtime_s:.1f}s (limit {max_downtime_s}s)")
    if v6 and lan_ip:
        max_wait_s, interval_s = _remote_ping_wait_settings(ctx.cfg)
        vlan_if = await _ensure_lab_mgmt_vlan_ipv6_for_ping(ctx)
        profile = ctx.cfg.get("_profile") or {}
        bind_v6 = _lab_ping_bind_ipv6(ctx.cfg, profile)
        wait_total = int(ctx.cfg.get("post_reboot_remote_ping_retries", 12)) * int(
            ctx.cfg.get("post_reboot_remote_ping_interval_s", 10)
        )
        recovered = await _wait_for_remote_ping6_from_lab(
            vlan_if,
            lan_ip,
            wait_s=max(wait_total, max_wait_s),
            interval_s=interval_s,
            bind_ipv6=bind_v6,
            notes=ctx.notes,
        )
        assert recovered.ok or recovered.received > 0, (
            f"{ctx.case.case_id}: lab ping6 to {lan_ip} failed after event: {recovered.raw[:200]}"
        )
    elif not v6 and ctx.device_target == "bts":
        await _assert_lab_ping_ipv4(
            ctx,
            lan_ip,
            wait_s=int(ctx.cfg.get("post_reboot_remote_ping_retries", 12))
            * int(ctx.cfg.get("post_reboot_remote_ping_interval_s", 10)),
            interval_s=int(ctx.cfg.get("post_reboot_remote_ping_interval_s", 10)),
        )
    elif not v6 and ctx.device_target == "cpe":
        await _assert_lab_ping_ipv4(
            ctx,
            lan_ip,
            wait_s=int(ctx.cfg.get("post_reboot_remote_ping_retries", 12))
            * int(ctx.cfg.get("post_reboot_remote_ping_interval_s", 10)),
            interval_s=int(ctx.cfg.get("post_reboot_remote_ping_interval_s", 10)),
        )
    await _assert_local_reachable(ctx, v6=v6, count=int(ctx.cfg.get("ping_count_short", 4)))
    if ctx.peer_host and ctx.device_target == "bts":
        await _ping_remote_after_local(
            ctx,
            v6=v6,
            count=int(ctx.cfg.get("ping_count_short", 4)),
            retries=int(ctx.cfg.get("post_reboot_remote_ping_retries", 12)),
            retry_interval_s=int(ctx.cfg.get("post_reboot_remote_ping_interval_s", 10)),
        )


async def _trigger_network_reload_sync(ssh: AsyncGenericDriver, cfg: dict[str, Any]) -> None:
    try:
        await ssh.send_command("ucidyn apply", timeout_ops=120)
    except ScrapliTimeout:
        pass
    await asyncio.sleep(int(cfg.get("network_reload_wait_s", 20)))


async def _run_soft_reboot_case(ctx: IpTestContext, *, v6: bool) -> None:
    cfg = ctx.cfg
    password = str(cfg.get("_password", ""))
    ssh = ctx.ssh
    before = await _read_uci_ip(ssh, v6=v6)
    lan_ip = ""
    if v6:
        lan_ip = await _resolve_dut_ipv6_for_lab_ping(ctx)
    elif ctx.device_target == "bts":
        lan_ip = await _resolve_bts_lan_ipv4(ctx)
    else:
        lan_ip = normalize_ip(
            str(ctx.peer_host or cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")).split("/")[0]
        )
    ctx.notes.append(f"{ctx.case.case_id}: UCI before reboot {before}")
    hosts = await _event_ssh_hosts(ctx, lan_ip=lan_ip or None)
    started = time.monotonic()
    try:
        await ssh.send_command("reboot", timeout_ops=5)
    except Exception:
        pass
    await _close_ssh(ssh)
    await asyncio.sleep(5)
    timeout_s = int(cfg.get("reboot_timeout_s", 200))
    max_down = int(cfg.get("reboot_max_downtime_s", timeout_s))
    new_ssh, effective = await _wait_ssh_after_event(
        ctx, password, timeout_s=timeout_s, extra_hosts=hosts
    )
    downtime = time.monotonic() - started
    ctx.ssh = new_ssh
    ctx.host = effective
    cid = ctx.case.case_id
    settle_s = int(cfg.get("post_reboot_settle_s", 150))
    if downtime < settle_s:
        extra = settle_s - downtime
        ctx.notes.append(
            f"{cid}: SSH on {effective} after {downtime:.0f}s — "
            f"waiting {extra:.0f}s more ({settle_s}s post-reboot settle before ping/link)"
        )
        print(
            f"[{cid}] post-reboot settle: {extra:.0f}s more "
            f"({settle_s}s total since reboot before ping/link checks)"
        )
        await asyncio.sleep(extra)
    else:
        ctx.notes.append(
            f"{cid}: SSH on {effective} after {downtime:.0f}s (>= {settle_s}s settle, continuing)"
        )

    async def _post_reboot_checks() -> None:
        nonlocal after
        after = await _read_uci_ip(ctx.ssh, v6=v6)
        _verify_uci_ip_retained(before, after, v6=v6, case_id=cid)
        ctx.notes.append(f"{cid}: UCI after reboot {after}")
        uptime = await _read_uptime_s(ctx.ssh)
        ctx.notes.append(f"post-reboot uptime {uptime:.0f}s on {ctx.host}")
        assert uptime < max(max_down, 600), f"uptime {uptime}s suggests device did not reboot"

    after: dict[str, str] = {}
    if ctx.device_target == "bts" and v6:
        from utils.ip_case_preflight import case_requires_cpe, run_post_event_testbed_recovery_v6

        await run_post_event_testbed_recovery_v6(
            ctx,
            label="post-reboot",
            after_mgmt_hook=_post_reboot_checks,
            require_cpe=case_requires_cpe(cid),
        )
    elif ctx.device_target == "bts" and not v6:
        from utils.ip_case_preflight import case_requires_cpe, run_post_event_testbed_recovery

        link_timeout = int(cfg.get("post_reboot_link_timeout_s", 120))
        cfg["_link_recovery_timeout_override"] = link_timeout
        try:
            await run_post_event_testbed_recovery(
                ctx,
                label="post-reboot",
                after_mgmt_hook=_post_reboot_checks,
                require_cpe=False,
                link_formation=True,
                strict=False,
            )
        finally:
            cfg.pop("_link_recovery_timeout_override", None)
        if lan_ip:
            ping_wait = int(cfg.get("post_reboot_ping_wait_s", 90))
            await _assert_lab_ping_ipv4(
                ctx,
                lan_ip,
                wait_s=ping_wait,
                interval_s=int(cfg.get("post_reboot_remote_ping_interval_s", 10)),
            )
    else:
        await _post_reboot_checks()
        await _assert_local_reachable(ctx, v6=v6, count=3)
        if lan_ip and v6:
            await _assert_lab_ping_ipv6(ctx, lan_ip, count=int(cfg.get("ping_count_short", 4)))
        if ctx.peer_host and ctx.device_target == "bts":
            await _ping_remote_after_local(
                ctx,
                v6=v6,
                count=int(cfg.get("ping_count_short", 4)),
                retries=int(cfg.get("post_reboot_remote_ping_retries", 12)),
            )


async def _run_soft_reset_case(ctx: IpTestContext, *, v6: bool) -> None:
    cfg = ctx.cfg
    password = str(cfg.get("_password", ""))
    ssh = ctx.ssh
    before = await _read_uci_ip(ssh, v6=v6)
    lan_ip = ""
    if v6:
        lan_ip = await _resolve_dut_ipv6_for_lab_ping(ctx)
    elif ctx.device_target == "bts":
        lan_ip = await _resolve_bts_lan_ipv4(ctx)
    else:
        lan_ip = normalize_ip(
            str(ctx.peer_host or cfg.get("remote_ping_host") or cfg.get("cpe_ipv4_default_address", "")).split("/")[0]
        )
    ctx.notes.append(f"{ctx.case.case_id}: UCI before network reload {before}")
    started = time.monotonic()
    await _trigger_network_reload_sync(ssh, cfg)
    await _close_ssh(ssh)
    reload_timeout = int(cfg.get("network_reload_max_wait_s", 120))
    new_ssh, effective = await _wait_ssh_after_event(
        ctx, password, timeout_s=reload_timeout, extra_hosts=await _event_ssh_hosts(ctx, lan_ip=lan_ip or None)
    )
    elapsed = time.monotonic() - started
    ctx.ssh = new_ssh
    ctx.host = effective
    cid = ctx.case.case_id

    async def _post_reload_checks() -> None:
        nonlocal after
        after = await _read_uci_ip(ctx.ssh, v6=v6)
        _verify_uci_ip_retained(before, after, v6=v6, case_id=cid)
        ctx.notes.append(f"{cid}: network reload restored in {elapsed:.1f}s")

    after: dict[str, str] = {}
    if ctx.device_target == "bts" and v6:
        from utils.ip_case_preflight import case_requires_cpe, run_post_event_testbed_recovery_v6

        await run_post_event_testbed_recovery_v6(
            ctx,
            label="post-reload",
            after_mgmt_hook=_post_reload_checks,
            require_cpe=case_requires_cpe(cid),
        )
    elif ctx.device_target == "bts" and not v6:
        from utils.ip_case_preflight import case_requires_cpe, run_post_event_testbed_recovery

        await run_post_event_testbed_recovery(
            ctx,
            label="post-reload",
            after_mgmt_hook=_post_reload_checks,
            require_cpe=case_requires_cpe(cid),
        )
    else:
        await _post_reload_checks()
        await _assert_local_reachable(ctx, v6=v6)
        if lan_ip and v6:
            await _assert_lab_ping_ipv6(ctx, lan_ip, count=int(cfg.get("ping_count_short", 4)))
        if ctx.peer_host and ctx.device_target == "bts":
            await _ping_remote_after_local(
                ctx,
                v6=v6,
                count=int(cfg.get("ping_count_short", 4)),
                retries=int(cfg.get("post_reload_remote_ping_retries", 8)),
            )


async def _cli_reset_with_retain_on_ssh(ssh: AsyncGenericDriver, cfg: dict[str, Any]) -> None:
    override = str(cfg.get("reset_retain_command", "")).strip()
    if override:
        try:
            await ssh.send_command(f"{override} && sync && reboot", timeout_ops=15)
        except Exception:
            pass
        return
    await _cli_factory_reset_on_ssh(ssh, cfg, retain_all=True)


def _skip_if_unsupported(ctx: IpTestContext, req: str, reason: str) -> None:
    if req in ctx.case.requires:
        pytest.skip(f"{ctx.case.case_id} on {ctx.device_target}: {reason}")


def _baseline_ipv4_values(cfg: dict[str, Any]) -> dict[str, str]:
    """Profile baseline LAN IPv4 used to restore testbed after mutating cases."""
    addr = str(
        cfg.get("ipv4_default_address") or cfg.get("ip03_verify_ipv4_address") or cfg.get("ipv4_address", "")
    ).split("/")[0].strip()
    return {
        "ipv4_address": normalize_ip(addr) if addr else "",
        "ipv4_netmask": str(cfg.get("ipv4_netmask", "255.255.255.0")).strip(),
        "ipv4_gateway": str(cfg.get("ipv4_gateway", "")).strip(),
    }


async def _capture_ip_recovery_snapshot(ctx: IpTestContext) -> dict[str, Any]:
    snap: dict[str, Any] = {"case_id": ctx.case.case_id, "device_target": ctx.device_target}
    if ctx.ssh is None:
        return snap
    try:
        snap["ipv4_uci"] = await _read_uci_ip(ctx.ssh, v6=False)
        snap["ipv6_uci"] = await _read_uci_ip(ctx.ssh, v6=True)
        snap["gateway"] = (await _ssh_run(ctx.ssh, RootCommands.GET_NET_GW)).strip()
    except Exception as exc:
        snap["capture_error"] = str(exc)
    return snap


async def _reconnect_device_ssh(ctx: IpTestContext, *, timeout_s: int = 90) -> None:
    password = str(ctx.cfg.get("_password", ""))
    ordered = _device_ssh_host_candidates(ctx)
    if not ordered:
        return
    new_ssh, effective = await _wait_ssh_any(ordered, password, timeout_s=timeout_s, interval_s=3)
    if ctx.ssh is not None and ctx.ssh is not new_ssh:
        await _close_ssh(ctx.ssh)
    ctx.ssh = new_ssh
    ctx.host = effective


async def _restore_bts_baseline_ipv4(ctx: IpTestContext) -> None:
    apply = _baseline_ipv4_values(ctx.cfg)
    if not apply["ipv4_address"]:
        return
    await _reconnect_device_ssh(ctx)
    await _cli_apply_ipv4_static(ctx.ssh, apply, ctx.cfg)
    ctx.notes.append(f"recovery: BTS IPv4 restored to {apply['ipv4_address']}")
    await asyncio.sleep(int(ctx.cfg.get("network_reload_wait_s", 15)))


async def _restore_ipv4_gateway(ctx: IpTestContext, gateway: str) -> None:
    gw = str(gateway).strip()
    if not gw or gw.startswith("uci:"):
        gw = str(ctx.cfg.get("ipv4_gateway", "")).strip()
    if not gw:
        return
    await _reconnect_device_ssh(ctx)
    await _cli_set_ipv4_gateway(ctx.ssh, gw, ctx.cfg)
    ctx.notes.append(f"recovery: gateway restored to {gw}")


# IP_15/IP_34 run backup+restore inside the case; IP_12/IP_31 only reset-with-retain (no archive restore).
_SKIP_POST_CASE_RECOVERY = frozenset({"IP_12", "IP_15", "IP_31", "IP_34"})


async def run_ip_post_case_recovery(
    ctx: IpTestContext,
    snapshot: dict[str, Any],
    *,
    case_failed: bool,
) -> None:
    """Leave testbed reachable for the next case; restore mutating changes when needed."""
    if not ctx.cfg.get("enable_ip_post_case_recovery", True):
        return
    cid = ctx.case.case_id
    if cid in _SKIP_POST_CASE_RECOVERY:
        return

    from config.ip_test_cases import IP_LIGHT_POST_CASE_IDS, is_fast_path_ip_case

    light_post = cid in IP_LIGHT_POST_CASE_IDS or (
        bool(ctx.cfg.get("ip_fast_path_enabled", True)) and is_fast_path_ip_case(cid)
    )
    # For fast-path/throughput cases we normally keep post-case recovery lightweight
    # (SSH reachability only). If the case actually failed (often during preflight),
    # we need to try heavier recovery (VLAN/link/reachability) to fix real bench
    # reachability issues like "mgmt-VLAN ping doesn't work".
    if light_post and not case_failed:
        from utils.ip_case_preflight import preflight_step1_fallback_ssh

        try:
            await preflight_step1_fallback_ssh(ctx)
            label = "throughput" if cid in IP_LIGHT_POST_CASE_IDS else "fast-path"
            ctx.notes.append(f"{cid}: {label} post-case (SSH check only)")
        except Exception as exc:
            ctx.notes.append(f"{cid}: light post-case SSH warn: {exc}")
        return

    ctx.notes.append(f"{cid}: post-case recovery")
    restore_failed = False

    try:
        if (
            cid == "IP_01"
            and ctx.device_target == "bts"
            and ctx.cfg.get("ip01_restore_baseline_after_case", False)
        ):
            await _restore_bts_baseline_ipv4(ctx)
        elif cid == "IP_04" and ctx.device_target == "bts":
            await _restore_ipv4_gateway(ctx, str(snapshot.get("gateway", "")))
        elif cid == "IP_17" and ctx.device_target == "bts":
            raw_cidr = str(ctx.cfg.get("static_route_cidr") or ctx.cfg.get("static_route_target", "")).strip()
            gw = str(ctx.cfg.get("static_route_gateway", "")).strip()
            peer = str(ctx.peer_host or ctx.cfg.get("static_route_ping_target", "")).strip()
            cidr, gw = _resolve_ip17_route_spec(
                ctx, cidr=raw_cidr, gateway=gw, ping_target=peer
            )
            if cidr:
                await _remove_static_route_on_device(ctx, cidr, gateway=gw)
        elif cid == "IP_18":
            profile = ctx.cfg.get("_profile") or {}
            if ctx.device_target == "bts":
                apply = _ipv6_values_for_target(ctx)
                await _reconnect_device_ssh(ctx)
                await _cli_apply_ipv6_static(ctx.ssh, apply, ctx.cfg)
                ctx.notes.append(
                    f"recovery: BTS IPv6 set to profile {apply.get('ipv6_address', '')}"
                )
                if ctx.cfg.get("ip18_configure_cpe", True):
                    await ensure_cpe_ipv6_ready(ctx, force=True)
                    ctx.notes.append("recovery: CPE IPv6 re-applied via secondary hop")
            else:
                cpe_apply = _ipv6_values_for_role(
                    ctx.cfg, profile, device_target="cpe"
                )
                await ensure_cpe_ipv6_ready(ctx, apply=cpe_apply, force=True)
                ctx.notes.append(
                    f"recovery: CPE IPv6 set to profile {cpe_apply.get('ipv6_address', '')}"
                )
    except Exception as exc:
        restore_failed = True
        ctx.notes.append(f"{cid}: config restore failed: {exc}")

    if ctx.cfg.get("link_recovery_after_case", True):
        from utils.ip_case_preflight import (
            run_post_event_testbed_recovery,
            run_post_event_testbed_recovery_v6,
        )

        profile = ctx.cfg.get("_profile") or {}
        dut = profile.get("dut", {}) or {}
        tb = profile.get("testbed", {}) or {}
        v6_suite = (
            dut.get("ip_mode") == "ipv6"
            or tb.get("strict_ipv6")
            or ctx.stack_mode == "ipv6"
        )
        try:
            if v6_suite and _stack_allowed(ctx, "v6"):
                await run_post_event_testbed_recovery_v6(
                    ctx, label="post-case", strict=False
                )
            elif _stack_allowed(ctx, "v4"):
                await run_post_event_testbed_recovery(
                    ctx,
                    label="post-case",
                    strict=False,
                )
        except Exception as exc:
            ctx.notes.append(f"{cid}: post-case testbed recovery failed: {exc}")
            if case_failed:
                raise


async def execute_ip_case_with_recovery(ctx: IpTestContext) -> None:
    snapshot = await _capture_ip_recovery_snapshot(ctx)
    case_failed = False
    try:
        await execute_ip_case(ctx)
    except BaseException:
        case_failed = True
        raise
    finally:
        chain = ctx.cfg.get("_ip_suite_chain")
        if isinstance(chain, dict):
            chain["ok"] = not case_failed
            if not case_failed:
                from utils.ip_suite_state import mark_suite_healthy, save_chain

                bts = normalize_ip(
                    str(ctx.cfg.get("_preflight_bts_lan_ipv4", "")).split("/")[0]
                )
                cpe = normalize_ip(
                    str(ctx.peer_host or ctx.cfg.get("_preflight_cpe_ipv4", "")).split("/")[0]
                )
                mark_suite_healthy(
                    chain,
                    bts_ip=bts,
                    cpe_ip=cpe,
                    local_pc_ok=bool(chain.get("local_pc_ok", True)),
                    remote_pc_ok=bool(chain.get("remote_pc_ok", bool(cpe))),
                    bts_ping_ok=True,
                    cpe_ping_ok=bool(cpe) if cpe else None,
                )
                save_chain(ctx.cfg.get("_repo_root", "."), chain)
            elif case_failed:
                chain["suite_healthy"] = False
                from utils.ip_suite_state import save_chain

                save_chain(ctx.cfg.get("_repo_root", "."), chain)
        try:
            await run_ip_post_case_recovery(ctx, snapshot, case_failed=case_failed)
        except Exception as exc:
            ctx.notes.append(f"post-case recovery error (non-fatal): {exc}")


def _stack_allowed(ctx: IpTestContext, stack: Stack) -> bool:
    """True when this case should run IPv4 or IPv6 steps (not merely profile ip_mode)."""
    case_stack = ctx.case.stack
    if case_stack == "dual":
        return True
    if case_stack == stack:
        return True
    if case_stack == "any":
        if stack == "v6":
            return ctx.stack_mode == "ipv6"
        return ctx.stack_mode != "ipv6"
    return False


async def execute_ip_case(ctx: IpTestContext) -> None:
    """Run a single IP test case for the given device target (bts/cpe)."""
    case = ctx.case
    cfg = ctx.cfg
    ssh = ctx.ssh
    password = str(cfg.get("_password", ""))

    if "manual_power" in case.requires:
        _skip_if_unsupported(ctx, "manual_power", "requires physical power cycle (not automatable)")

    if "firmware" in case.requires and not str(cfg.get("firmware_image_path", "")).strip():
        _skip_if_unsupported(ctx, "firmware", "set ip_tests.firmware_image_path in profile")

    cid = case.case_id

    # Preflight: skip when suite healthy; else minimal unless prior case broke ping/link.
    if _stack_allowed(ctx, "v4") and ctx.case.stack in ("v4", "dual", "any"):
        from utils.ip_case_preflight import preflight_should_be_full, run_ip_case_preflight

        use_minimal = bool(cfg.get("ip_fast_path_enabled", True)) and not preflight_should_be_full(
            ctx
        )
        await run_ip_case_preflight(ctx, stack_v4=True, minimal=use_minimal)
        ssh = ctx.ssh

    profile = cfg.get("_profile") or {}
    dut = profile.get("dut", {}) or {}
    if _stack_allowed(ctx, "v6") and ctx.case.stack in ("v6", "dual", "any"):
        if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
            from utils.ip_case_preflight import (
                case_requires_cpe,
                preflight_should_be_full,
                run_ip_case_preflight_v6,
            )

            use_minimal = bool(cfg.get("ip_fast_path_enabled", True)) and not preflight_should_be_full(
                ctx
            )
            await run_ip_case_preflight_v6(
                ctx,
                skip_device_v6_ping=(cid == "IP_18"),
                require_cpe=case_requires_cpe(cid) if cid != "IP_18" else False,
                minimal=use_minimal,
            )
            ssh = ctx.ssh

    # --- IPv4 functional / validation ---
    if cid == "IP_01":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope for current profile")
        configured_before = await _read_device_lan_ipv4s(ssh, cfg)
        apply = await _ipv4_test_apply_values_for_device(ssh, cfg)
        expected = apply["ipv4_address"]
        if configured_before:
            ctx.notes.append(f"IP_01 device LAN before apply: {sorted(configured_before)}")
        post_apply_wait_s = int(cfg.get("ip01_post_reload_wait_s", 40))
        if ctx.gui_page is not None:
            ctx.notes.append(f"IP_01 applying new LAN IP {expected} via GUI (not 192.168.2.1)")
            await _gui_set_static_ip(ctx.gui_page, cfg, v6=False, apply_values=apply)
            ctx.notes.append(
                f"IP_01: ucidyn apply via GUI — keeping SSH open {post_apply_wait_s}s before br-lan + ping"
            )
            await asyncio.sleep(post_apply_wait_s)
        else:
            ctx.notes.append(
                f"IP_01 applying new LAN IP {expected} via ucidyn on open SSH (not 192.168.2.1)"
            )
            await _cli_apply_ipv4_static(ssh, apply, cfg, post_apply_wait_s=post_apply_wait_s)
        if_out = await _assert_br_lan_ipv4(
            ctx.ssh, cfg, expected, notes=ctx.notes
        )
        ctx.notes.append(f"IP_01 br-lan verify ok: {if_out[:200]}")
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        ping_kw = _lab_ping_kwargs(cfg)
        ping_stats = await _ping_from_lab_pc(
            vlan_if,
            expected,
            count=int(cfg.get("ping_count_short", 4)),
            **ping_kw,
        )
        assert ping_stats.ok, (
            f"IP_01: lab ping to {expected} via {vlan_if} failed after "
            f"{post_apply_wait_s}s post-apply wait: {ping_stats.raw[:300]}"
        )
        ctx.notes.append(
            f"IP_01 lab ping {expected} via {vlan_if}: loss={ping_stats.loss_pct}%"
        )
        verify_ssh = None
        verify_hosts = _all_mgmt_hosts(
            expected,
            tuple(h for h in (ctx.host, *ctx.fallback_hosts) if normalize_ip(h) != expected),
        )
        for host in verify_hosts:
            try:
                verify_ssh = await _open_ssh(host, password)
                ctx.notes.append(f"IP_01 post-apply SSH on {host}")
                break
            except Exception as exc:
                ctx.notes.append(f"IP_01 post-apply SSH {host} failed: {exc}")
        if verify_ssh is None:
            pytest.fail(
                f"IP_01: cannot SSH to applied LAN IP {expected} "
                f"(tried {verify_hosts}) after post-reload br-lan + lab ping"
            )
        uci = await _read_uci_ip(verify_ssh, v6=False)
        assert expected in uci.get("address", ""), f"UCI IP mismatch: {uci}"
        await _close_ssh(verify_ssh)
        cfg["_preflight_bts_lan_ipv4"] = expected
        chain = cfg.get("_ip_suite_chain")
        if isinstance(chain, dict):
            chain["bts_lan_ipv4"] = expected
        return

    if cid == "IP_02":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        local_ip = await _verified_bts_lan_ipv4(ctx)
        ctx.notes.append(f"IP_02 local target {local_ip} (preflight ping-verified)")
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        stats = await _ping_from_lab_pc(
            vlan_if, local_ip, count=int(cfg.get("ping_count_short", 4))
        )
        assert stats.ok, (
            f"IP_02 local ping failed: {local_ip} via {vlan_if} "
            f"(0% loss required): {stats.raw[:300]}"
        )
        ctx.notes.append(f"local ping {local_ip} via {vlan_if}: loss={stats.loss_pct}%")
        creds = cfg.get("_device_creds") or {"user": "root", "pass": password}
        gui_user = str(creds.get("user") or "root")
        assert await _check_web_ui_with_fallback(
            local_ip,
            (ctx.host, cfg.get("_cli_fallback_ip"), "10.0.0.1"),
            password,
            username=gui_user,
            notes=ctx.notes,
        ), f"Web UI not reachable at {local_ip} (tried fallbacks)"
        return

    if cid == "IP_03":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        verify_v4 = await _verified_bts_lan_ipv4(ctx)
        ctx.notes.append(f"IP_03: BTS LAN {verify_v4} (preflight ping-verified), via {vlan_if}")
        local_stats = await _ping_from_lab_pc(
            vlan_if, verify_v4, count=int(cfg.get("ping_count_short", 4))
        )
        assert local_stats.ok, f"local ping {verify_v4} via {vlan_if}: {local_stats.raw[:200]}"
        remote = await _verified_cpe_lan_ipv4(ctx)
        ctx.notes.append(f"IP_03: CPE remote {remote} (preflight ping-verified)")
        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        remote_stats = await _ping_from_lab_pc(
            vlan_if, remote, count=int(cfg.get("ping_count_short", 4))
        )
        if not remote_stats.ok:
            remote_stats = await _wait_for_remote_ping_from_lab(
                vlan_if,
                remote,
                wait_s=max_wait_s,
                interval_s=interval_s,
                notes=ctx.notes,
            )
        assert remote_stats.ok, (
            f"IP_03 remote ping {remote} via {vlan_if} failed: {remote_stats.raw[:300]}"
        )
        ctx.notes.append(
            f"IP_03 remote ping {remote} via {vlan_if}: loss={remote_stats.loss_pct}%"
        )
        if cfg.get("ip03_verify_ipv4_address"):
            expect = normalize_ip(str(cfg.get("ip03_verify_ipv4_address", "")).split("/")[0])
            if expect and remote != expect:
                ctx.notes.append(f"IP_03 WARN: remote {remote} != profile hint {expect}")
        return

    if cid == "IP_04":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        gw = str(cfg.get("ip04_gateway") or cfg.get("ipv4_gateway", "")).strip()
        device_ip = await _verified_bts_lan_ipv4(ctx)
        ctx.notes.append(
            f"IP_04 gateway {gw}, backend ping to BTS {device_ip} (preflight ping-verified)"
        )
        await _apply_ipv4_gateway(ctx, gw)
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        stats = await _ping_from_lab_pc(
            vlan_if, device_ip, count=int(cfg.get("ping_count_short", 4))
        )
        assert stats.ok, (
            f"IP_04 backend PC ping to BTS {device_ip} via {vlan_if} failed: {stats.raw[:300]}"
        )
        ctx.notes.append(f"backend ping to BTS {device_ip} via {vlan_if}: loss={stats.loss_pct}%")
        return

    if cid == "IP_05":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        from utils.iperf_lab_flows import run_ip05_ipv4_lab_throughput

        await run_ip05_ipv4_lab_throughput(ctx)
        return

    if cid == "IP_06":
        await _assert_local_reachable(ctx, v6=False, count=int(cfg.get("ping_count_short", 4)))
        target = ctx.peer_host or str(cfg.get("remote_ping_host", "")).strip()
        if not target:
            pytest.skip("IP_06: no remote ping target (CPE IPv4 after precheck)")
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        ctx.notes.append(
            f"IP_06: ping from lab PC {vlan_if} -> CPE remote {normalize_ip(target)} "
            f"(count_long={cfg.get('ping_count_long', 100)})"
        )
        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        small_stats = await _ping_from_lab_pc(vlan_if, target, count=1, size=64, per_packet_wait_s=1)
        if not small_stats.ok:
            small_stats = await _wait_for_remote_ping_from_lab(
                vlan_if,
                target,
                wait_s=max_wait_s,
                interval_s=interval_s,
                notes=ctx.notes,
                size=64,
            )
        if not small_stats.ok and ("unreachable" in small_stats.raw.lower() or small_stats.loss_pct >= 100):
            pytest.skip(f"IP_06 remote {target} unreachable via {vlan_if}: {small_stats.raw[:120]}")
        assert small_stats.ok, f"small-packet ping failed: {small_stats.raw[:200]}"
        large_stats = await _ping_from_lab_pc(vlan_if, target, count=3, size=1200)
        assert large_stats.ok, f"large-packet ping failed: {large_stats.raw[:200]}"
        count = int(cfg.get("ping_count_long", 100))
        stats = await _ping_from_lab_pc(vlan_if, target, count=count)
        assert stats.loss_pct <= float(cfg.get("max_ping_loss_pct", 1.0)), stats.raw
        return

    if cid == "IP_07":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        await _apply_lab_pc_mtu_change(ctx, v6=False)
        return

    if cid == "IP_08":
        await _assert_local_reachable(ctx, v6=False)
        target = ctx.peer_host or str(cfg.get("remote_ping_host", "")).strip()
        if not target:
            pytest.skip("IP_08: no remote ping target (CPE IPv4 after precheck)")
        vlan_if = await _ensure_lab_mgmt_vlan_for_ping(ctx)
        ctx.notes.append(
            f"IP_08: ping from lab PC {vlan_if} -> CPE remote {normalize_ip(target)} "
            "(small + 2000-byte fragmentation)"
        )
        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        small = await _ping_from_lab_pc(vlan_if, target, count=1, size=64, per_packet_wait_s=1)
        if not small.ok:
            small = await _wait_for_remote_ping_from_lab(
                vlan_if,
                target,
                wait_s=max_wait_s,
                interval_s=interval_s,
                notes=ctx.notes,
                size=64,
            )
        if not small.ok and ("unreachable" in small.raw.lower() or small.loss_pct >= 100):
            pytest.skip(f"IP_08 remote {target} unreachable via {vlan_if}: {small.raw[:120]}")
        assert small.ok, small.raw
        stats = await _ping_from_lab_pc(vlan_if, target, count=4, size=2000)
        assert stats.received > 0, stats.raw
        return

    if cid in ("IP_09", "IP_28"):
        stack_v6 = cid == "IP_28"
        if not _stack_allowed(ctx, "v6" if stack_v6 else "v4"):
            pytest.skip("stack not in scope")
        await _run_soft_reboot_case(ctx, v6=stack_v6)
        return

    if cid in ("IP_10", "IP_29"):
        pytest.skip("hard reboot requires manual power cycle (PDU not available)")

    if cid in ("IP_11", "IP_30"):
        stack_v6 = cid == "IP_30"
        if not _stack_allowed(ctx, "v6" if stack_v6 else "v4"):
            pytest.skip("stack not in scope")
        await _run_soft_reset_case(ctx, v6=stack_v6)
        return

    if cid in ("IP_12", "IP_31"):
        if not _stack_allowed(ctx, "v6" if cid == "IP_31" else "v4"):
            pytest.skip("stack not in scope")
        await _run_reset_retain(ctx, v6=(cid == "IP_31"))
        return

    if cid in ("IP_13", "IP_32"):
        if not _stack_allowed(ctx, "v6" if cid == "IP_32" else "v4"):
            pytest.skip("stack not in scope")
        from utils.iperf_lab_flows import run_ip13_reboot_during_traffic

        await run_ip13_reboot_during_traffic(ctx, v6=(cid == "IP_32"))
        return

    if cid in ("IP_14", "IP_33"):
        if not _stack_allowed(ctx, "v6" if cid == "IP_33" else "v4"):
            pytest.skip("stack not in scope")
        await _run_interface_flap_case(ctx, v6=(cid == "IP_33"))
        return

    if cid in ("IP_15", "IP_34"):
        restore_v6 = cid == "IP_34"
        cfg["enable_ip15_factory_restore"] = True
        await _run_restore_backup(ctx, v6=restore_v6)
        return

    if cid == "IP_16":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        await _run_arp_resolution_case(ctx)
        return

    if cid == "IP_17":
        if not _stack_allowed(ctx, "v4"):
            pytest.skip("IPv4 not in scope")
        await _run_static_route_case(ctx)
        return

    # --- IPv6 functional / validation (IP_18–IP_26) ---
    if cid == "IP_18":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        from utils.ip_case_preflight import preflight_step3_lab_mgmt_ipv6

        apply = _ipv6_values_for_target(ctx)
        expected = normalize_ip(apply["ipv6_address"].split("/")[0])
        await preflight_step3_lab_mgmt_ipv6(ctx)
        ctx.notes.append(
            f"IP_18: lab PC /120 ready; applying {ctx.device_target.upper()} "
            f"{apply['ipv6_address']} gw={apply.get('ipv6_gateway', '')}"
        )
        await _apply_ipv6_static_on_device(ctx, apply)

        if ctx.device_target == "bts" and cfg.get("ip18_configure_cpe", True):
            cpe_host = await ensure_cpe_ipv6_ready(ctx)
            ctx.notes.append(f"IP_18: remote CPE IPv6 configured at {cpe_host}")

        wait_s = int(cfg.get("ip18_apply_wait_s", 40))
        interval_s = int(cfg.get("ip18_apply_ping_interval_s", 3))
        ctx.notes.append(f"IP_18: waiting {wait_s}s for lab ping6 while apply completes")
        await _wait_for_lab_ping6_during_apply(
            ctx, expected, wait_s=wait_s, interval_s=interval_s
        )
        verify_ssh = ctx.ssh
        if ctx.device_target == "bts":
            profile = cfg.get("_profile") or {}
            for host in _factory_first_ssh_hosts(
                _device_ssh_host_candidates(ctx)
            ):
                try:
                    verify_ssh = await _open_ssh(host, password)
                    ctx.notes.append(f"IP_18 post-apply SSH on {host}")
                    break
                except Exception:
                    continue
        else:
            ctx.notes.append(f"IP_18 post-apply verify via CPE SSH hop ({ctx.host})")
        uci = await _read_uci_ip(verify_ssh, v6=True)
        assert ipv6_equal(apply["ipv6_address"], str(uci.get("address", ""))), (
            f"IP_18: UCI IPv6 mismatch: {uci}"
        )
        ip6_out = await _ssh_run_raw(
            verify_ssh, "ip -6 addr show dev br-lan 2>/dev/null; ip -6 addr show"
        )
        if not ipv6_addr_in_text(expected, ip6_out):
            ctx.notes.append(
                f"IP_18: {expected} not in ip -6 output (UCI ok); snippet: {ip6_out[:200]}"
            )
        await _assert_lab_ping_ipv6(ctx, expected, count=int(cfg.get("ping_count_short", 4)))
        if ctx.device_target == "bts":
            assert await _check_web_ui_with_fallback(
                ctx.host, ctx.fallback_hosts, password, notes=ctx.notes
            )
        return

    if cid == "IP_19":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        from utils.ip_case_preflight import ensure_device_ipv6_configured

        await ensure_device_ipv6_configured(ctx)
        rounds = int(cfg.get("ip19_local_ping_rounds", 3))
        for r in range(rounds):
            used = await _assert_local_reachable(
                ctx, v6=True, count=int(cfg.get("ping_count_short", 4))
            )
            ctx.notes.append(f"IP_19 local ping6 round {r + 1}/{rounds} via {used}")
        assert await _check_web_ui_with_fallback(
            ctx.host, ctx.fallback_hosts, password, notes=ctx.notes
        ), "Web UI not reachable after local IPv6 ping"
        return

    if cid == "IP_20":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        max_wait_s, interval_s = _remote_ping_wait_settings(cfg)
        retries = max(
            int(cfg.get("ip20_remote_ping_retries", 0)),
            int(cfg.get("post_reboot_remote_ping_retries", 12)),
            max(1, max_wait_s // interval_s),
        )
        try:
            await _ping_remote_after_local(
                ctx,
                v6=True,
                retries=retries,
                retry_interval_s=int(
                    cfg.get("ip20_remote_ping_interval_s")
                    or cfg.get("post_reboot_remote_ping_interval_s", 10)
                ),
            )
        except AssertionError as exc:
            if cfg.get("ip20_skip_if_cpe_unreachable", True):
                pytest.skip(
                    f"IP_20: BTS→CPE IPv6 {ctx.peer_host} not reachable over RF "
                    f"(run IP_18 on CPE or set ipv6 on CPE LAN first): {exc}"
                )
            raise
        if ctx.peer_host:
            rev = await _ping(ssh, ctx.host, count=int(cfg.get("ping_count_short", 4)), v6=True)
            ctx.notes.append(
                f"IP_20 reverse ping6 peer→DUT: rx={rev.received} loss={rev.loss_pct}%"
            )
        return

    if cid == "IP_21":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        await _run_ip21_ipv6_gateway(ctx)
        return

    if cid == "IP_22":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        from utils.iperf_lab_flows import run_ip22_ipv6_lab_throughput

        await run_ip22_ipv6_lab_throughput(ctx)
        return

    if cid == "IP_23":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        await _run_ip23_ipv6_long_ping(ctx)
        return

    if cid == "IP_24":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        await _apply_lab_pc_mtu_change(ctx, v6=True)
        return

    if cid == "IP_25":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        await _run_ip25_ipv6_fragmentation(ctx)
        return

    if cid == "IP_26":
        if not _stack_allowed(ctx, "v6"):
            pytest.skip("IPv6 not in scope")
        out = await _ssh_run(ssh, "ip -6 addr show")
        assert "fe80::" in out.lower(), out[:200]
        iface = str(cfg.get("ipv6_link_local_iface", "")).strip()
        if not iface:
            m = re.search(r"^\d+:\s+(\S+):.*\n\s+inet6 fe80::", out, re.M)
            iface = m.group(1) if m else "br-lan"
        peer_ll = str(cfg.get("ipv6_link_local_peer", "")).strip()
        if not peer_ll and ctx.peer_host:
            peer_out = await _ssh_run(ssh, f"ip -6 neigh show dev {shlex.quote(iface)}")
            m = re.search(r"(fe80::[0-9a-f:]+)", peer_out, re.I)
            if m:
                peer_ll = f"{m.group(1)}%{iface}"
        if peer_ll:
            if "%" not in peer_ll:
                peer_ll = f"{peer_ll}%{iface}"
            stats = _parse_ping_stats(
                await _ssh_run(ssh, f"ping6 -c 4 {shlex.quote(peer_ll)}")
            )
            assert stats.ok, f"IP_26 link-local ping failed: {stats.raw[:300]}"
            ctx.notes.append(f"IP_26 link-local ok: {peer_ll}")
        else:
            ctx.notes.append("IP_26: fe80 present; no peer link-local target on bench")
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

    if cid == "IP_35":
        await _run_firmware_http_keep_settings(ctx)
        return

    if cid == "IP_36":
        if ctx.peer_host:
            await _assert_local_reachable(ctx, v6=(ctx.stack_mode == "ipv6"), count=3)
            v4 = await _ping(ssh, ctx.peer_host, count=3, v6=False)
            v6 = await _ping(ssh, ctx.peer_host, count=3, v6=True)
            assert v4.ok or v6.ok, f"v4={v4.raw[:120]} v6={v6.raw[:120]}"
        return

    if cid in PLANNED_IP_CASE_IDS:
        pytest.skip(f"{cid}: planned (isolation/multicast) — automation not implemented yet")

    pytest.fail(f"No handler for {cid}")
