"""Sanity-only post without-keep recovery — restore BTS/CPE link via CPE PC hop."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from pathlib import Path
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver

from config.defaults import IP_TEST_DEFAULTS
from utils.lab_pc_net import ensure_pc_interface_up, ensure_pc_ipv4_on_interface
from utils.net_utils import is_ipv6_literal, normalize_ip
from utils.sanity_link import wait_sanity_rf_link
from utils.sanity_ssh import (
    close_sanity_ssh,
    open_sanity_ssh,
    sanity_ssh_run,
    tune_uhttpd_for_playwright,
    wait_sanity_ssh,
)


def _log(msg: str) -> None:
    print(f"[sanity] {msg}", flush=True)


def _log_q(msg: str, *, quiet: bool) -> None:
    if not quiet:
        _log(msg)


_DEBUG_LOG = Path(
    "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-5d791c.log"
)


def _dbg03(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
) -> None:
    # region agent log
    try:
        entry = {
            "sessionId": "5d791c",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass
    # endregion


def cpe_pc_credentials(profile: dict[str, Any], values: dict[str, Any]) -> tuple[str, str, str]:
    """Return (host, user, password) for the CPE bench PC."""
    dut = profile.get("dut", {}) or {}
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    user = "root"
    host_from_ssh = ""
    if "@" in ssh_target:
        user, host_from_ssh = ssh_target.split("@", 1)
    else:
        host_from_ssh = ssh_target
    host = normalize_ip(
        str(values.get("cpe_pc_ip") or dut.get("cpe_pc_ip") or host_from_ssh or "10.0.150.40")
    )
    password = str(
        values.get("cpe_pc_password")
        or dut.get("cpe_pc_password")
        or sec.get("password")
        or "senao1234#"
    ).strip()
    return host, (user or "root").strip(), password


def cpe_factory_ipv4(profile: dict[str, Any], values: dict[str, Any]) -> str:
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    return normalize_ip(
        str(
            values.get("cpe_factory_ipv4")
            or sec.get("cpe_factory_ipv4")
            or "10.0.0.1"
        )
    )


def bts_factory_ipv4(values: dict[str, Any]) -> str:
    return normalize_ip(str(values.get("bts_factory_ipv4") or "192.168.2.1"))


async def ensure_bts_pc_factory_lan(profile: dict[str, Any], device_password: str) -> None:
    """Ensure the BTS lab PC can reach factory LAN 192.168.2.1 (keep IPv6/VLAN up)."""
    tb = profile.get("testbed", {}) or {}
    primary = dict(tb.get("primary_pc", {}) or {})
    if not primary:
        return
    await ensure_pc_interface_up(primary, device_password)
    await ensure_pc_ipv4_on_interface(
        primary,
        device_password,
        cidr="192.168.2.200/24",
    )


async def open_cpe_pc_ssh(profile: dict[str, Any], values: dict[str, Any]) -> AsyncGenericDriver:
    host, user, password = cpe_pc_credentials(profile, values)
    _log(f"Opening CPE PC SSH {user}@{host}")
    conn = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
        comms_prompt_pattern=r"[#$]\s*$",
    )
    await asyncio.wait_for(conn.open(), timeout=40)
    await conn.send_command("echo ok", timeout_ops=20)
    return conn


async def _probe_cpe_via_pc(
    pc_ssh: AsyncGenericDriver,
    cpe_host: str,
    passwords: list[str],
) -> tuple[str, str]:
    """Return (user, password) for nested SSH CPE PC → CPE factory."""
    last = ""
    for pw in passwords:
        if not pw:
            continue
        probe = (
            f"sshpass -p {shlex.quote(pw)} "
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-o ConnectTimeout=15 root@{shlex.quote(cpe_host)} 'echo cpe_ok' 2>&1"
        )
        out = (await pc_ssh.send_command(probe, timeout_ops=30)).result or ""
        if "cpe_ok" in out:
            return "root", pw
        last = out
    raise ConnectionError(f"[sanity] CPE hop probe failed for {cpe_host}: {last[:220]}")


class SanityCpeHop:
    """Run CPE commands through CPE PC → factory SSH."""

    def __init__(
        self,
        pc_ssh: AsyncGenericDriver,
        *,
        cpe_host: str,
        cpe_user: str,
        cpe_password: str,
    ) -> None:
        self._pc = pc_ssh
        self._host = normalize_ip(cpe_host)
        self._user = cpe_user
        self._password = cpe_password

    async def run(self, command: str, *, timeout_s: int = 60) -> str:
        hop = (
            f"sshpass -p {shlex.quote(self._password)} "
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-o ConnectTimeout=20 {shlex.quote(self._user)}@{self._host} "
            f"{shlex.quote(command)}"
        )
        out = await self._pc.send_command(hop, timeout_ops=timeout_s)
        return str(out.result or "")

    async def close(self) -> None:
        await close_sanity_ssh(self._pc)


def _cpe_hop_candidate_hosts(
    profile: dict[str, Any],
    values: dict[str, Any],
) -> list[str]:
    """Prefer live lab IPv4 before factory; CPE is often still on 192.168.2.11 after RF drop."""
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    dut = profile.get("dut", {}) or {}
    link = profile.get("link", {}) or {}
    candidates = [
        normalize_ip(
            str(
                dut.get("cpe_lab_ipv4")
                or link.get("cpe_lab_ipv4")
                or values.get("cpe_lab_ipv4")
                or "192.168.2.11"
            ).split("/")[0]
        ),
        normalize_ip(str(sec.get("cpe_ssh_ipv4") or sec.get("cpe_gui_ipv4") or "").split("/")[0]),
        cpe_factory_ipv4(profile, values),
        normalize_ip(str(dut.get("cpe_fallback_ip") or link.get("cpe_fallback_ipv4") or "").split("/")[0]),
    ]
    out: list[str] = []
    for host in candidates:
        if host and host not in out:
            out.append(host)
    return out


async def wait_cpe_hop(
    profile: dict[str, Any],
    values: dict[str, Any],
    device_password: str,
    *,
    timeout_s: int = 600,
    poll_s: int = 10,
    prefer_hosts: list[str] | None = None,
) -> SanityCpeHop:
    """Wait until CPE is reachable via CPE PC hop (lab IPv4 first, then factory)."""
    tb = profile.get("testbed", {}) or {}
    sec = dict(tb.get("secondary_pc", {}) or {})
    _, _, pc_pass = cpe_pc_credentials(profile, values)
    sec.setdefault("fallback_ipv4", "192.168.2.110")
    sec.setdefault("fallback_prefix_len", 24)
    await ensure_pc_interface_up(sec, pc_pass)
    await ensure_pc_ipv4_on_interface(sec, pc_pass, cidr="192.168.2.110/24")

    hosts = [normalize_ip(h) for h in (prefer_hosts or []) if h]
    for host in _cpe_hop_candidate_hosts(profile, values):
        if host not in hosts:
            hosts.append(host)
    passwords = [
        device_password,
        str(values.get("device_password_alt") or ""),
        "Sen@0ubRNwk$",
        "admin",
        "senao123",
    ]
    deadline = time.monotonic() + timeout_s
    attempt = 0
    last_err = ""
    _log(f"Waiting CPE via CPE PC hop → {hosts}")
    while time.monotonic() < deadline:
        attempt += 1
        pc_ssh = None
        try:
            pc_ssh = await open_cpe_pc_ssh(profile, values)
            # ensure sshpass exists
            await pc_ssh.send_command(
                "command -v sshpass >/dev/null || "
                "(apt-get update -qq && apt-get install -y -qq sshpass) || true",
                timeout_ops=120,
            )
            for host in hosts:
                try:
                    user, pw = await _probe_cpe_via_pc(pc_ssh, host, passwords)
                    _log(f"CPE hop ready at {host} (attempt {attempt})")
                    return SanityCpeHop(pc_ssh, cpe_host=host, cpe_user=user, cpe_password=pw)
                except Exception as host_exc:
                    last_err = str(host_exc)
            if pc_ssh is not None:
                await close_sanity_ssh(pc_ssh)
                pc_ssh = None
        except Exception as exc:
            last_err = str(exc)
            if pc_ssh is not None:
                await close_sanity_ssh(pc_ssh)
        if attempt == 1 or attempt % 6 == 0:
            _log(
                f"CPE hop pending ({last_err[:120]}, "
                f"{int(deadline - time.monotonic())}s left)"
            )
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(f"[sanity] CPE hop not ready at {hosts}: {last_err}")


async def _resolve_cpe_sta_iface_idx(hop: SanityCpeHop) -> int:
    """Return UCI @wifi-iface index for CPE Radio-1 STA (BTS link)."""
    raw = await hop.run(
        "uci show wireless 2>/dev/null | grep -E '@wifi-iface\\[[0-9]+\\]\\.mode='",
        timeout_s=20,
    )
    sta_indices: list[int] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if ".mode=" not in line:
            continue
        if "'sta'" not in line and "sta" not in line.split("=", 1)[-1]:
            continue
        head = line.split(".mode=", 1)[0]
        if "[" not in head or "]" not in head:
            continue
        idx_s = head.split("[", 1)[1].split("]", 1)[0]
        if idx_s.isdigit():
            sta_indices.append(int(idx_s))
    if not sta_indices:
        return 1
    return 1 if 1 in sta_indices else min(sta_indices)


def _bts_radio1_wireless(bts_before: dict[str, str]) -> tuple[str, str, str, str]:
    """SSID/key/nwksecret/encryption captured from BTS before upgrade."""
    ssid = (bts_before.get("wireless.ssid") or "").strip()
    key = (bts_before.get("wireless.key") or "").strip()
    nwk = (bts_before.get("wireless.nwksecret") or key).strip()
    enc = (bts_before.get("wireless.encryption") or "").strip()
    return ssid, key, nwk, enc


async def restore_bts_from_snapshot(
    bts_ssh: AsyncGenericDriver,
    before: dict[str, str],
    *,
    radio_idx: int = 1,
) -> None:
    """
    Push original BTS config then soft-reboot so LAN + ch149 stick after without-keep.

    Uses short stepwise SSH commands (scrapli truncates long base64 one-liners
    before ``uci commit``). Caller reconnects on restored IPs after reboot.
    """
    ipv4 = normalize_ip((before.get("network.lan.ipaddr") or "").split("/")[0])
    netmask = (before.get("network.lan.netmask") or "255.255.255.0").strip()
    gw = (before.get("network.lan.gateway") or "").strip()
    ipv6 = (before.get("network.lan.ip6addr") or "").strip()
    ipv6_gw = (before.get("network.lan.ip6gw") or "").strip()
    proto = (before.get("network.lan.proto") or "static").strip() or "static"
    ssid = (before.get("wireless.ssid") or "").strip()
    key = (before.get("wireless.key") or "").strip()
    nwk = (before.get("wireless.nwksecret") or key).strip()
    enc = (before.get("wireless.encryption") or "").strip()
    hostname = (before.get("system.hostname") or "").strip()
    mgmtvlan = (before.get("vlan.ath1.mgmtvlan") or "1").strip() or "1"
    if not ipv4:
        raise RuntimeError("[sanity] BTS restore missing baseline IPv4")
    if not ssid or not key:
        raise RuntimeError("[sanity] BTS restore missing baseline SSID/key")

    _log(
        f"Restoring BTS — {ipv4}/{netmask}, IPv6={ipv6 or '(none)'}, "
        f"SSID={ssid!r}, VLAN=transparent/mgmtvlan={mgmtvlan}"
    )
    _log("Restoring BTS RF on channel 149 / HT80 (lab-stable, ACS off)")

    steps = [
        "uci set vlan.ath1.mode='transparent'",
        "uci delete vlan.ath1.svlan 2>/dev/null || true",
        "uci delete vlan.ath1.cvlanid 2>/dev/null || true",
        "uci delete vlan.ath1.tvlanid 2>/dev/null || true",
        f"uci set vlan.ath1.mgmtvlan='{mgmtvlan}'",
        "uci commit vlan",
        f"uci set network.lan.proto='{proto}'",
        f"uci set network.lan.ipaddr='{ipv4}'",
        f"uci set network.lan.netmask='{netmask}'",
    ]
    if gw:
        steps.append(f"uci set network.lan.gateway='{gw}'")
    if ipv6:
        steps.append("uci set network.lan.ip6proto='static'")
        steps.append(f"uci set network.lan.ip6addr='{ipv6}'")
        if ipv6_gw:
            steps.append(f"uci set network.lan.ip6gw='{ipv6_gw}'")
    steps.append("uci commit network")
    if hostname:
        steps.append(f"uci set system.@system[0].hostname={shlex.quote(hostname)}")
        steps.append("uci commit system")
    steps.extend(
        [
            f"uci set wireless.@wifi-iface[{radio_idx}].ssid={shlex.quote(ssid)}",
            f"uci set wireless.@wifi-iface[{radio_idx}].key={shlex.quote(key)}",
            f"uci set wireless.wifi{radio_idx}.nwksecret={shlex.quote(nwk)}",
        ]
    )
    if enc:
        steps.append(
            f"uci set wireless.@wifi-iface[{radio_idx}].encryption={shlex.quote(enc)}"
        )
    steps.extend(
        [
            "uci set advwireless.ath1.kwndfsacs=0 2>/dev/null || true",
            f"uci set wireless.wifi{radio_idx}.channel='149'",
            f"uci set wireless.wifi{radio_idx}.htmode='HT80'",
            f"uci set advwireless.ath{radio_idx}.channel='149' 2>/dev/null || true",
            f"uci delete wireless.@wifi-iface[{radio_idx}].hidden 2>/dev/null || true",
            f"uci set wireless.@wifi-iface[{radio_idx}].hidden=0",
            "uci commit wireless",
            "uci commit advwireless 2>/dev/null || true",
        ]
    )
    # Apply in small batches so scrapli never truncates mid-commit.
    batch: list[str] = []
    for cmd in steps:
        batch.append(cmd)
        if len(batch) >= 4 or cmd.startswith("uci commit"):
            await sanity_ssh_run(bts_ssh, " ; ".join(batch), timeout_s=40)
            batch = []
    if batch:
        await sanity_ssh_run(bts_ssh, " ; ".join(batch), timeout_s=40)

    got_ip = (
        await sanity_ssh_run(bts_ssh, "uci -q get network.lan.ipaddr", timeout_s=20) or ""
    ).strip()
    got_ch = (
        await sanity_ssh_run(bts_ssh, f"uci -q get wireless.wifi{radio_idx}.channel", timeout_s=20)
        or ""
    ).strip()
    _log(f"BTS UCI after commit: ipaddr={got_ip!r} channel={got_ch!r} (want {ipv4}/149)")
    if got_ip != ipv4 or got_ch != "149":
        raise RuntimeError(
            f"[sanity] BTS restore UCI did not stick before reboot "
            f"(ip={got_ip!r} ch={got_ch!r}, want {ipv4}/149)"
        )

    # Live-add restored IPs first so wait_bts_after_restore can find .10 even if
    # ucidyn apply drops SSH before reboot (UBR655 factory often stays on .1).
    try:
        await sanity_ssh_run(
            bts_ssh,
            " ; ".join(
                [
                    f"ip addr add {ipv4}/24 dev br-lan 2>/dev/null || true",
                    (
                        f"ip -6 addr add {ipv6} dev br-lan 2>/dev/null || true"
                        if ipv6
                        else "true"
                    ),
                    "ucidyn apply >/dev/null 2>&1 || true",
                ]
            ),
            timeout_s=90,
        )
    except Exception as exc:
        _log(f"BTS restore ucidyn apply dropped SSH (expected): {exc}")
        return

    try:
        await sanity_ssh_run(bts_ssh, "sync; sleep 1; reboot", timeout_s=12)
    except Exception as exc:
        _log(f"BTS restore SSH dropped after soft reboot (expected): {exc}")


async def wait_bts_after_restore(
    *,
    password: str,
    target_ipv4: str,
    factory_ipv4: str,
    target_ipv6: str,
    source_v6: str,
    timeout_s: int,
    poll_s: int,
) -> tuple[AsyncGenericDriver, str]:
    """
    After restore apply, BTS may still be on factory IP briefly, then move to
    restored IPv4/IPv6. Poll all candidates; prefer restored addresses.
    """
    candidates: list[tuple[str, str]] = []
    for host, bind in (
        (target_ipv4, ""),
        (target_ipv6, source_v6 if target_ipv6 and ":" in target_ipv6 else ""),
        (factory_ipv4, ""),
    ):
        host = normalize_ip((host or "").split("/")[0])
        if host and host not in {h for h, _ in candidates}:
            candidates.append((host, bind))

    deadline = time.monotonic() + timeout_s
    attempt = 0
    last_err = ""
    _log(
        "Waiting BTS after restore on: "
        + ", ".join(h for h, _ in candidates)
    )
    while time.monotonic() < deadline:
        attempt += 1
        # Prefer restored IPv4/IPv6 over factory once they answer ICMP.
        for host, bind in candidates:
            try:
                conn = await open_sanity_ssh(
                    host,
                    password,
                    source_v6=bind,
                    label=f"BTS@{host}",
                    timeout_s=min(20, max(10, poll_s + 5)),
                )
                lan = await sanity_ssh_run(conn, "uci -q get network.lan.ipaddr")
                live = await sanity_ssh_run(
                    conn,
                    "ip -4 -o addr show br-lan 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | tr '\\n' ' '",
                )
                _log(
                    f"BTS reachable at {host} "
                    f"(uci={lan or '—'} live={live or '—'}, attempt {attempt})"
                )
                return conn, host
            except Exception as exc:
                last_err = str(exc)
        if attempt == 1 or attempt % 6 == 0:
            _log(
                f"BTS restore reconnect pending "
                f"({int(deadline - time.monotonic())}s left)"
            )
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(
        f"[sanity] BTS not reachable after restore on "
        f"{[h for h, _ in candidates]}: {last_err}"
    )


async def ensure_bts_lan_matches(
    bts_ssh: AsyncGenericDriver,
    before: dict[str, str],
    *,
    password: str,
    factory_ipv4: str,
    source_v6: str,
    timeout_s: int,
    poll_s: int,
    radio_idx: int = 1,
) -> tuple[AsyncGenericDriver, str]:
    """If UCI/live LAN is still factory, re-apply and wait for restored IPs."""
    want_v4 = normalize_ip((before.get("network.lan.ipaddr") or "").split("/")[0])
    want_v6 = normalize_ip((before.get("network.lan.ip6addr") or "").split("/")[0])
    cur_v4 = normalize_ip(await sanity_ssh_run(bts_ssh, "uci -q get network.lan.ipaddr"))
    live = await sanity_ssh_run(
        bts_ssh,
        "ip -4 -o addr show br-lan 2>/dev/null | awk '{print $4}' | cut -d/ -f1",
    )
    live_ips = {normalize_ip(x) for x in (live or "").split() if x.strip()}
    if want_v4 and want_v4 in live_ips:
        host = want_v6 or want_v4
        return bts_ssh, host

    _log(
        f"BTS live LAN {sorted(live_ips) or '(none)'} "
        f"(uci={cur_v4!r}, want {want_v4}); re-applying + network reload"
    )
    await restore_bts_from_snapshot(bts_ssh, before, radio_idx=radio_idx)
    await close_sanity_ssh(bts_ssh)
    await asyncio.sleep(8)
    return await wait_bts_after_restore(
        password=password,
        target_ipv4=want_v4,
        factory_ipv4=factory_ipv4,
        target_ipv6=want_v6,
        source_v6=source_v6,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )


async def restore_cpe_from_snapshot(
    hop: SanityCpeHop,
    before: dict[str, str],
    bts_before: dict[str, str],
    *,
    radio_idx: int = 1,
    cpe_radio_idx: int | None = None,
) -> None:
    """Push original CPE network + BTS Radio-1 SSID/key onto CPE via hop."""
    ipv4 = normalize_ip((before.get("network.lan.ipaddr") or "").split("/")[0])
    netmask = (before.get("network.lan.netmask") or "255.255.255.0").strip()
    gw = (before.get("network.lan.gateway") or "").strip()
    ipv6 = (before.get("network.lan.ip6addr") or "").strip()
    ipv6_gw = (before.get("network.lan.ip6gw") or "").strip()
    proto = (before.get("network.lan.proto") or "static").strip() or "static"
    ssid, key, nwk, enc = _bts_radio1_wireless(bts_before)
    mgmtvlan = (before.get("vlan.ath1.mgmtvlan") or "1").strip() or "1"
    if cpe_radio_idx is None:
        cpe_radio_idx = await _resolve_cpe_sta_iface_idx(hop)
    if not ipv4:
        raise RuntimeError("[sanity] CPE restore missing baseline IPv4")
    if not ssid or not key:
        raise RuntimeError("[sanity] CPE restore missing BTS Radio-1 SSID/key")

    _log(
        f"Restoring CPE via hop — {ipv4}/{netmask}, IPv6={ipv6 or '(none)'}, "
        f"BTS Radio-1 SSID={ssid!r} on iface[{cpe_radio_idx}]"
    )
    parts = [
        "uci set vlan.ath1.mode='transparent'",
        "uci delete vlan.ath1.svlan 2>/dev/null || true",
        "uci delete vlan.ath1.cvlanid 2>/dev/null || true",
        "uci delete vlan.ath1.tvlanid 2>/dev/null || true",
        f"uci set vlan.ath1.mgmtvlan='{mgmtvlan}'",
        "uci commit vlan",
        f"uci set network.lan.proto='{proto}'",
        f"uci set network.lan.ipaddr='{ipv4}'",
        f"uci set network.lan.netmask='{netmask}'",
    ]
    if gw:
        parts.append(f"uci set network.lan.gateway='{gw}'")
    if ipv6:
        parts.append("uci set network.lan.ip6proto='static'")
        parts.append(f"uci set network.lan.ip6addr='{ipv6}'")
        if ipv6_gw:
            parts.append(f"uci set network.lan.ip6gw='{ipv6_gw}'")
    wireless_cmds = [
        f"uci set wireless.@wifi-iface[{cpe_radio_idx}].ssid={shlex.quote(ssid)}",
        f"uci set wireless.@wifi-iface[{cpe_radio_idx}].key={shlex.quote(key)}",
        f"uci set wireless.wifi{cpe_radio_idx}.nwksecret={shlex.quote(nwk)}",
        # Bridged STA on this UBR build needs WDS or wpa_supplicant is refused.
        f"uci set wireless.@wifi-iface[{cpe_radio_idx}].wds=1",
        f"uci set wireless.@wifi-iface[{cpe_radio_idx}].network=lan",
        f"uci set wireless.@wifi-iface[{cpe_radio_idx}].mode=sta",
        f"uci set wireless.@wifi-iface[{cpe_radio_idx}].disabled=0",
    ]
    if enc:
        wireless_cmds.insert(
            2,
            f"uci set wireless.@wifi-iface[{cpe_radio_idx}].encryption={shlex.quote(enc)}",
        )
    parts.extend(
        [
            *wireless_cmds,
            "uci commit network",
            "uci commit wireless",
            "/etc/init.d/network reload >/dev/null 2>&1 || true",
            "wifi reload >/dev/null 2>&1 || true",
            "echo RESTORE_DONE",
        ]
    )
    batch = " ; ".join(parts)
    try:
        await hop.run(batch, timeout_s=40)
    except Exception as exc:
        _log(f"CPE restore hop closed (expected on apply): {exc}")


async def _ensure_bts_lab_channel_149(
    bts_ssh: AsyncGenericDriver,
    *,
    reboot: bool = False,
) -> None:
    """
    Lab leave-state after without-keep / factory: Configured+on-air channel 149 HT80.

    When ``reboot`` is True, commit then soft-reboot (used once if RF link fails).
    """
    radio_idx = 1
    cmds = (
        "set +e; "
        "uci set advwireless.ath1.kwndfsacs=0 2>/dev/null || true; "
        f"uci set wireless.wifi{radio_idx}.channel=149; "
        f"uci set wireless.wifi{radio_idx}.htmode=HT80; "
        f"uci set advwireless.ath{radio_idx}.channel=149 2>/dev/null || true; "
        f"uci delete wireless.@wifi-iface[{radio_idx}].hidden 2>/dev/null || true; "
        f"uci set wireless.@wifi-iface[{radio_idx}].hidden=0; "
        "uci commit wireless; "
        "uci commit advwireless 2>/dev/null || true; "
    )
    if reboot:
        _log("Soft reboot BTS after pinning channel 149 (one link-recovery attempt)")
        cmds += "sync; reboot; echo CH149_REBOOT"
        try:
            await sanity_ssh_run(bts_ssh, cmds, timeout_s=15)
        except Exception as exc:
            _log(f"BTS ch149 soft reboot dropped SSH (expected): {exc}")
        return

    # Soft pin only — no wifi bounce (caller already rebooted on restore).
    cur = (await sanity_ssh_run(bts_ssh, f"uci -q get wireless.wifi{radio_idx}.channel") or "").strip()
    if cur == "149":
        _log("BTS channel already UCI 149")
        return
    _log(f"BTS channel UCI={cur!r} — pin 149/HT80 (no bounce)")
    await sanity_ssh_run(bts_ssh, cmds + "echo CH149_PINNED", timeout_s=40)


async def ensure_bts_rf_transparent(bts_ssh: AsyncGenericDriver) -> None:
    """No-VLAN sanity: BTS data path must be transparent for CPE IPv6 over RF."""
    mode = (await sanity_ssh_run(bts_ssh, "uci -q get vlan.ath1.mode")).strip()
    mgmt = (await sanity_ssh_run(bts_ssh, "uci -q get vlan.ath1.mgmtvlan")).strip()
    if mode in ("transparent", "0", "") and mgmt in ("1", ""):
        _log(f"BTS RF VLAN OK (mode={mode or 'transparent'}, mgmtvlan={mgmt or '1'})")
        return

    _log(
        f"BTS RF VLAN {mode or '(unset)'} / mgmtvlan={mgmt or '(unset)'} "
        "-> transparent + mgmtvlan=1"
    )
    parts = [
        "uci set vlan.ath1.mode='transparent'",
        "uci delete vlan.ath1.svlan 2>/dev/null || true",
        "uci delete vlan.ath1.cvlanid 2>/dev/null || true",
        "uci delete vlan.ath1.tvlanid 2>/dev/null || true",
        "uci set vlan.ath1.mgmtvlan='1'",
        "uci commit vlan",
    ]
    await sanity_ssh_run(bts_ssh, " ; ".join(parts), timeout_s=40)
    try:
        await sanity_ssh_run(
            bts_ssh, "ucidyn apply >/dev/null 2>&1 || wifi reload >/dev/null 2>&1 || true", timeout_s=90
        )
    except Exception as exc:
        _log(f"BTS RF transparent apply dropped SSH (expected): {exc}")
        return
    await asyncio.sleep(8)


def _profile_network_targets(profile: dict[str, Any]) -> dict[str, str]:
    """Known bench IPv4/IPv6 targets from active profile + defaults."""
    dut = profile.get("dut", {}) or {}
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    ip_cfg = {**IP_TEST_DEFAULTS, **(profile.get("ip_tests", {}) or {})}
    cpe_v6 = normalize_ip(
        str((dut.get("remote_ipv6s") or [mgmt.get("ipv6_cpe") or ""])[0] or "")
    )
    prefix = str(ip_cfg.get("ipv6_prefix_len") or mgmt.get("prefix_len") or "120")
    cpe_v6_full = str(ip_cfg.get("ipv6_address_cpe") or f"{cpe_v6}/{prefix}")
    ipv6_gw = str(
        ip_cfg.get("ipv6_gateway_cpe")
        or dut.get("cpe_pc_ipv6")
        or mgmt.get("ipv6_cpe_pc")
        or ""
    )
    ipv4 = normalize_ip(
        str(ip_cfg.get("lab_pc_remote_mgmt_ipv4") or "192.168.2.11").split("/")[0]
    )
    netmask = str(ip_cfg.get("ipv4_netmask") or "255.255.255.0")
    return {
        "cpe_v6": cpe_v6,
        "cpe_v6_addr": cpe_v6_full,
        "cpe_v6_gw": ipv6_gw,
        "cpe_ipv4": ipv4,
        "cpe_netmask": netmask,
    }


async def _read_bts_wireless_for_cpe(
    bts_ssh: AsyncGenericDriver,
    *,
    radio_idx: int,
) -> tuple[str, str, str]:
    ssid = (
        await sanity_ssh_run(bts_ssh, f"uci -q get wireless.@wifi-iface[{radio_idx}].ssid")
    ).strip()
    key = (
        await sanity_ssh_run(bts_ssh, f"uci -q get wireless.@wifi-iface[{radio_idx}].key")
    ).strip()
    nwk = (
        await sanity_ssh_run(bts_ssh, f"uci -q get wireless.wifi{radio_idx}.nwksecret")
    ).strip()
    return ssid, key, nwk or key


async def bootstrap_cpe_network_from_profile(
    profile: dict[str, Any],
    values: dict[str, Any],
    bts_ssh: AsyncGenericDriver,
    device_password: str,
    *,
    radio_idx: int = 1,
) -> None:
    """Push profile IPv4/IPv6 + live BTS SSID/key onto factory CPE via CPE PC hop."""
    targets = _profile_network_targets(profile)
    ssid, key, nwk = await _read_bts_wireless_for_cpe(bts_ssh, radio_idx=radio_idx)
    if not ssid or not key:
        raise RuntimeError("[sanity] Cannot bootstrap CPE: BTS SSID/key unavailable")

    _log(
        f"Bootstrapping CPE via hop — IPv4={targets['cpe_ipv4']}, "
        f"IPv6={targets['cpe_v6_addr']}, SSID={ssid!r}"
    )
    hop = await wait_cpe_hop(profile, values, device_password, timeout_s=180, poll_s=8)
    try:
        cpe_stub = {
            "network.lan.ipaddr": targets["cpe_ipv4"],
            "network.lan.netmask": targets["cpe_netmask"],
            "network.lan.ip6addr": targets["cpe_v6_addr"],
            "network.lan.ip6gw": targets["cpe_v6_gw"],
            "vlan.ath1.mgmtvlan": "1",
        }
        bts_stub = {
            "wireless.ssid": ssid,
            "wireless.key": key,
            "wireless.nwksecret": nwk,
        }
        await restore_cpe_from_snapshot(hop, cpe_stub, bts_stub, radio_idx=radio_idx)
    finally:
        await hop.close()
    await asyncio.sleep(15)


async def _quick_cpe_reachable(
    cpe_v6: str,
    password: str,
    source_v6: str,
    *,
    timeout_s: int = 25,
) -> bool:
    cpe_v6 = normalize_ip(cpe_v6)
    if not cpe_v6 or not is_ipv6_literal(cpe_v6):
        return False
    try:
        conn = await open_sanity_ssh(
            cpe_v6,
            password,
            source_v6=source_v6,
            label="CPE-probe",
            timeout_s=timeout_s,
        )
        await close_sanity_ssh(conn)
        return True
    except Exception:
        return False


def _link_recovery_profile(profile: dict[str, Any], profile_bundle) -> dict[str, Any]:
    """Merge active testbed/dut with recovery link settings.

    Active profile link wins over the recovery profile (default: link_formation).
    Otherwise AIRTEL auto_credentials from recovery overrides lab static
    UBR655_R1_Test and breaks SANITY pre-case CPE recovery.
    """
    recovery = getattr(profile_bundle, "recovery", None) or {}
    merged = dict(recovery or profile)
    merged["testbed"] = profile.get("testbed", merged.get("testbed", {}))
    merged["dut"] = profile.get("dut", merged.get("dut", {}))
    merged["ip_tests"] = profile.get("ip_tests", merged.get("ip_tests", {}))
    active_link = dict(profile.get("link", {}) or {})
    recovery_link = dict(recovery.get("link", {}) or {}) if recovery else {}
    # recovery defaults first, then active lab SSID/key/auto_credentials on top
    merged["link"] = {**recovery_link, **active_link} if (recovery_link or active_link) else {}
    link = merged["link"]
    # Static-lab recovery needs usable RF power; profile default "1" is for IP retain benches.
    if link and not bool(link.get("auto_credentials", False)):
        if str(link.get("tx_power_default", "1")).strip() in ("", "1"):
            link["tx_power_default"] = "26"
    return merged


async def ensure_sanity_cpe_ssh(
    bts_ssh: AsyncGenericDriver,
    cpe_v6: str,
    password: str,
    source_v6: str,
    profile: dict[str, Any],
    profile_bundle,
    values: dict[str, Any],
    *,
    timeout_s: int,
    poll_s: int,
    quiet: bool = False,
) -> AsyncGenericDriver:
    """
    Connect to CPE SSH for baseline work.

    When CPE IPv6 is down (e.g. after a failed SANITY_03), recover without PC VLAN:
    BTS RF transparent, RF link credentials, then profile-based CPE network bootstrap.
    """
    cpe_v6 = normalize_ip(cpe_v6)
    cpe_v4 = normalize_ip(
        str(
            (profile.get("dut", {}) or {}).get("cpe_lab_ipv4")
            or (profile.get("link", {}) or {}).get("cpe_lab_ipv4")
            or values.get("cpe_lab_ipv4")
            or "192.168.2.11"
        ).split("/")[0]
    )

    # Prefer live lab addresses first (IPv6 then IPv4) before RF/hop recovery.
    # Suite runs often leave IPv6 flaky while CPE is still reachable on lab IPv4.
    async def _return_tuned(ssh: AsyncGenericDriver) -> AsyncGenericDriver:
        await tune_uhttpd_for_playwright(ssh, quiet=quiet)
        return ssh

    if await _quick_cpe_reachable(cpe_v6, password, source_v6):
        return await _return_tuned(
            await wait_sanity_ssh(
                cpe_v6,
                password,
                source_v6=source_v6,
                label="CPE",
                timeout_s=min(30, timeout_s),
                poll_s=min(5, poll_s),
            )
        )
    if cpe_v4 and cpe_v4 != cpe_v6 and await _quick_cpe_reachable(cpe_v4, password, ""):
        _log_q(f"CPE IPv6 down — using lab IPv4 {cpe_v4}", quiet=quiet)
        return await _return_tuned(
            await wait_sanity_ssh(
                cpe_v4,
                password,
                source_v6="",
                label="CPE-v4",
                timeout_s=min(30, timeout_s),
                poll_s=min(5, poll_s),
            )
        )

    _log_q(f"CPE {cpe_v6} not reachable — running sanity pre-case link recovery (no VLAN)", quiet=quiet)
    await ensure_bts_rf_transparent(bts_ssh)

    from utils.ip_link_recovery import recover_testbed_link_ssh

    link_profile = _link_recovery_profile(profile, profile_bundle)
    notes: list[str] = []
    await recover_testbed_link_ssh(
        bts_ssh=bts_ssh,
        profile=link_profile,
        password=password,
        notes=notes,
        cfg={
            "link_recovery_timeout_s": min(180, max(60, timeout_s // 3)),
            "link_recovery_poll_s": poll_s,
        },
    )
    if not quiet:
        for note in notes:
            _log(note)

    if not await _quick_cpe_reachable(cpe_v6, password, source_v6, timeout_s=25):
        if cpe_v4 and await _quick_cpe_reachable(cpe_v4, password, "", timeout_s=15):
            _log_q(f"CPE recovered on lab IPv4 {cpe_v4} — skip factory hop", quiet=quiet)
        else:
            _log_q("CPE still unreachable — bootstrapping CPE network from profile via hop", quiet=quiet)
            radio_idx = int((profile.get("link", {}) or {}).get("radio_idx", 1))
            await bootstrap_cpe_network_from_profile(
                profile,
                values,
                bts_ssh,
                password,
                radio_idx=radio_idx,
            )
            # Hop restore + wifi reload needs time before mgmt IPs answer.
            await asyncio.sleep(25)

    last_error = ""
    # Give post-hop / post-link recovery more time than a quick probe.
    ipv6_budget = min(180, max(60, timeout_s // 4))
    for host, bind, label, budget in (
        (cpe_v6, source_v6, "CPE", ipv6_budget),
        (cpe_v4, "", "CPE-v4", min(180, max(90, timeout_s // 3))),
    ):
        if not host:
            continue
        try:
            if host == cpe_v4 and host != cpe_v6:
                _log_q(f"CPE IPv6 still down — trying lab IPv4 {cpe_v4}", quiet=quiet)
            return await _return_tuned(
                await wait_sanity_ssh(
                    host,
                    password,
                    source_v6=bind,
                    label=label,
                    timeout_s=budget,
                    poll_s=poll_s,
                )
            )
        except Exception as exc:
            last_error = str(exc)
            # If IPv6 is hard-unreachable, skip remaining IPv6 budget and try IPv4 now.
            if host == cpe_v6 and ("Network is unreachable" in last_error or "Errno 101" in last_error):
                _log_q(f"CPE IPv6 unreachable ({last_error}) — falling back to IPv4", quiet=quiet)
                continue
    raise TimeoutError(
        f"[sanity] CPE SSH not ready at {cpe_v6} (also tried {cpe_v4}): {last_error}"
    )


async def sanity_03_restore_link(
    *,
    profile: dict[str, Any],
    values: dict[str, Any],
    device_creds: dict,
    bts_before: dict[str, str],
    cpe_before: dict[str, str],
    bts_original_host: str,
    cpe_original_host: str,
    source_v6: str,
    link_timeout_s: int,
    poll_s: int,
    rf_stable: int = 2,
    ping_stable: int = 2,
) -> None:
    """
    After without-keep flash:
      1. Reach BTS at factory 192.168.2.1 and restore original config
      2. Reconnect via restored IPv4 first (then IPv6)
      3. Hop CPE PC → CPE factory and restore
      4. Wait RF link + ping original BTS/CPE addresses
    """
    password = device_creds["pass"]
    factory_bts = bts_factory_ipv4(values)
    radio_idx = int((profile.get("link", {}) or {}).get("radio_idx", 1))
    ssh_timeout_s = int(values.get("ssh_recovery_timeout_s", 600))
    target_v4 = normalize_ip(
        (bts_before.get("network.lan.ipaddr") or "").split("/")[0]
    )
    target_v6 = normalize_ip(
        (bts_before.get("network.lan.ip6addr") or "").split("/")[0]
    ) or normalize_ip(bts_original_host)

    await ensure_bts_pc_factory_lan(profile, password)

    _log(f"Connecting BTS factory SSH at {factory_bts}")
    bts_ssh = await wait_sanity_ssh(
        factory_bts,
        password,
        label="BTS factory",
        timeout_s=ssh_timeout_s,
        poll_s=poll_s,
    )
    _dbg03(
        "D",
        "sanity_recovery.py:sanity_03_restore_link",
        "BTS factory SSH open",
        {"factory_bts": factory_bts},
    )
    try:
        await restore_bts_from_snapshot(bts_ssh, bts_before, radio_idx=radio_idx)
    finally:
        await close_sanity_ssh(bts_ssh)

    # Soft reboot from restore — give BTS time to drop before polling.
    await asyncio.sleep(25)
    bts_ssh, reached = await wait_bts_after_restore(
        password=password,
        target_ipv4=target_v4,
        factory_ipv4=factory_bts,
        target_ipv6=target_v6,
        source_v6=source_v6,
        timeout_s=ssh_timeout_s,
        poll_s=poll_s,
    )
    bts_ssh, reached = await ensure_bts_lan_matches(
        bts_ssh,
        bts_before,
        password=password,
        factory_ipv4=factory_bts,
        source_v6=source_v6,
        timeout_s=ssh_timeout_s,
        poll_s=poll_s,
        radio_idx=radio_idx,
    )
    await ensure_bts_rf_transparent(bts_ssh)
    await _ensure_bts_lab_channel_149(bts_ssh)

    # Prefer IPv6 when it answers quickly; never burn the full SSH timeout on it.
    preferred = target_v6 if target_v6 and ":" in target_v6 else (
        target_v4 or reached
    )
    reconnect_host = reached
    _dbg03(
        "D",
        "sanity_recovery.py:sanity_03_restore_link",
        "BTS restored and RF transparent",
        {"reached": reached, "preferred": preferred},
    )
    if preferred and preferred != reached:
        try:
            await close_sanity_ssh(bts_ssh)
            _log(f"Trying preferred BTS host {preferred} (90s)")
            bts_ssh = await wait_sanity_ssh(
                preferred,
                password,
                source_v6=source_v6 if ":" in preferred else "",
                label="BTS preferred",
                timeout_s=min(90, ssh_timeout_s),
                poll_s=poll_s,
            )
            reconnect_host = preferred
            await _ensure_bts_lab_channel_149(bts_ssh)
        except Exception as exc:
            _log(
                f"Preferred BTS {preferred} not ready ({exc}) — "
                f"continuing on {reached}"
            )
            bts_ssh = await wait_sanity_ssh(
                reached,
                password,
                source_v6=source_v6 if ":" in reached else "",
                label="BTS reached",
                timeout_s=min(180, ssh_timeout_s),
                poll_s=poll_s,
            )
            reconnect_host = reached
            await _ensure_bts_lab_channel_149(bts_ssh)

    hop = await wait_cpe_hop(
        profile,
        values,
        password,
        timeout_s=min(ssh_timeout_s, 360),
        poll_s=poll_s,
    )
    try:
        await restore_cpe_from_snapshot(
            hop, cpe_before, bts_before, radio_idx=radio_idx
        )
    finally:
        await hop.close()

    _log_q(f"Waiting CPE SSH at restored address {cpe_original_host}", quiet=False)
    _dbg03(
        "E",
        "sanity_recovery.py:sanity_03_restore_link",
        "CPE restore via hop done, waiting SSH",
        {"cpe_original_host": cpe_original_host},
    )
    cpe_ssh_wait_s = min(ssh_timeout_s, 240)
    try:
        cpe_ssh = await wait_sanity_ssh(
            cpe_original_host,
            password,
            source_v6=source_v6,
            label="CPE post-restore",
            timeout_s=cpe_ssh_wait_s,
            poll_s=poll_s,
        )
        await close_sanity_ssh(cpe_ssh)
        _dbg03(
            "E",
            "sanity_recovery.py:sanity_03_restore_link",
            "CPE SSH reachable after restore",
            {"cpe_original_host": cpe_original_host},
        )
    except Exception as exc:
        _dbg03(
            "E",
            "sanity_recovery.py:sanity_03_restore_link",
            "CPE SSH not yet reachable after restore",
            {"cpe_original_host": cpe_original_host, "error": str(exc)},
        )
        _log(f"CPE SSH pending after restore ({exc}) — continuing to RF link wait")

    await asyncio.sleep(10)
    first_link_timeout = max(120, min(link_timeout_s, 240))
    try:
        await _verify_post_restore_cpe_reachability(
            bts_ssh,
            profile,
            cpe_before=cpe_before,
            cpe_original_host=cpe_original_host,
            bts_mgmt_host=reconnect_host or bts_original_host,
            link_timeout_s=first_link_timeout,
            poll_s=poll_s,
            ping_stable=ping_stable,
            rf_stable=rf_stable,
        )
    except Exception as link_exc:
        _log(
            f"RF link not up after restore ({link_exc}) — "
            "soft reboot BTS once, re-pin ch149, then one more link wait"
        )
        try:
            await _ensure_bts_lab_channel_149(bts_ssh, reboot=True)
        except Exception as reb_exc:
            _log(f"BTS soft reboot kick note: {reb_exc}")
        try:
            await close_sanity_ssh(bts_ssh)
        except Exception:
            pass
        await asyncio.sleep(20)
        bts_ssh, reached2 = await wait_bts_after_restore(
            password=password,
            target_ipv4=target_v4,
            factory_ipv4=factory_bts,
            target_ipv6=target_v6,
            source_v6=source_v6,
            timeout_s=min(ssh_timeout_s, 420),
            poll_s=poll_s,
        )
        reconnect_host = reached2
        await _ensure_bts_lab_channel_149(bts_ssh)
        # One soft retry only — if this fails the case fails.
        await _verify_post_restore_cpe_reachability(
            bts_ssh,
            profile,
            cpe_before=cpe_before,
            cpe_original_host=cpe_original_host,
            bts_mgmt_host=reconnect_host or bts_original_host,
            link_timeout_s=max(180, min(link_timeout_s, 300)),
            poll_s=poll_s,
            ping_stable=ping_stable,
            rf_stable=rf_stable,
        )
    _dbg03(
        "E",
        "sanity_recovery.py:sanity_03_restore_link",
        "RF link stable after restore",
        {"reconnect_host": reconnect_host, "cpe_original_host": cpe_original_host},
    )
    await close_sanity_ssh(bts_ssh)
    _log("SANITY_03 restore complete — RF link up; backend verify next")


async def _verify_post_restore_cpe_reachability(
    bts_ssh: AsyncGenericDriver,
    profile: dict[str, Any],
    *,
    cpe_before: dict[str, str],
    cpe_original_host: str,
    bts_mgmt_host: str,
    link_timeout_s: int,
    poll_s: int,
    ping_stable: int,
    rf_stable: int,
) -> None:
    """Wait for RF link during restore; raise if link never forms."""
    ok = await wait_sanity_rf_link(
        bts_ssh,
        profile,
        case_id="SANITY_03",
        label="post-restore",
        timeout_s=link_timeout_s,
        poll_s=poll_s,
        required_stable=rf_stable,
        fail_on_timeout=False,  # we handle retry/fail in caller
    )
    if not ok:
        raise TimeoutError(
            f"[sanity] SANITY_03 RF link not up after {link_timeout_s}s "
            f"(BTS={bts_mgmt_host}, CPE={cpe_original_host})"
        )
    _log("RF link stable during restore — backend SSH verify follows in Step 5")


def profile_baseline_snapshots(profile: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Build BTS/CPE restore snapshots from active profile (preflight factory recovery)."""
    dut = profile.get("dut", {}) or {}
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    link = profile.get("link", {}) or {}
    ip_cfg = {**IP_TEST_DEFAULTS, **(profile.get("ip_tests", {}) or {})}
    prefix = str(ip_cfg.get("ipv6_prefix_len") or mgmt.get("prefix_len") or "120")
    bts_v6 = normalize_ip(str(dut.get("local_ipv6") or mgmt.get("ipv6_bts") or ""))
    cpe_v6 = normalize_ip(str((dut.get("remote_ipv6s") or [mgmt.get("ipv6_cpe") or ""])[0] or ""))
    bts_v4 = normalize_ip(str(ip_cfg.get("lab_pc_mgmt_ipv4") or "192.168.2.10").split("/")[0])
    cpe_v4 = normalize_ip(
        str(ip_cfg.get("lab_pc_remote_mgmt_ipv4") or "192.168.2.11").split("/")[0]
    )
    netmask = str(ip_cfg.get("ipv4_netmask") or "255.255.255.0")
    bts_v6_gw = normalize_ip(
        str(
            ip_cfg.get("ipv6_gateway_bts")
            or dut.get("bts_pc_ipv6")
            or mgmt.get("ipv6_bts_pc")
            or ""
        )
    )
    cpe_v6_gw = normalize_ip(
        str(
            ip_cfg.get("ipv6_gateway_cpe")
            or dut.get("cpe_pc_ipv6")
            or mgmt.get("ipv6_cpe_pc")
            or ""
        )
    )
    ssid = str(link.get("ssid") or "").strip()
    key = str(link.get("key") or "").strip()
    bts_before = {
        "network.lan.proto": "static",
        "network.lan.ipaddr": bts_v4,
        "network.lan.netmask": netmask,
        "network.lan.ip6addr": f"{bts_v6}/{prefix}" if bts_v6 else "",
        "network.lan.ip6gw": bts_v6_gw,
        "wireless.ssid": ssid,
        "wireless.key": key,
        "wireless.nwksecret": key,
        "vlan.ath1.mgmtvlan": "1",
    }
    cpe_before = {
        "network.lan.proto": "static",
        "network.lan.ipaddr": cpe_v4,
        "network.lan.netmask": netmask,
        "network.lan.ip6addr": f"{cpe_v6}/{prefix}" if cpe_v6 else "",
        "network.lan.ip6gw": cpe_v6_gw,
        "vlan.ath1.mgmtvlan": "1",
    }
    return bts_before, cpe_before


async def sanity_preflight_restore_if_factory(
    profile: dict[str, Any],
    *,
    values: dict[str, Any],
    device_creds: dict,
    bts_original_host: str,
    cpe_original_host: str,
    source_v6: str,
) -> bool:
    """
    When a prior SANITY_03 run left BTS on factory LAN, restore profile config
    before the next sanity case starts.
    """
    factory_bts = bts_factory_ipv4(values)
    password = device_creds["pass"]
    poll_s = int(values.get("poll_interval_s", 10))
    link_timeout_s = int(values.get("link_recovery_timeout_s", 480))

    await ensure_bts_pc_factory_lan(profile, password)
    if not await _quick_bts_factory_check(factory_bts, password):
        return False

    _log(
        f"BTS still on factory LAN ({factory_bts}) — preflight restore from profile"
    )
    bts_before, cpe_before = profile_baseline_snapshots(profile)
    await sanity_03_restore_link(
        profile=profile,
        values=values,
        device_creds=device_creds,
        bts_before=bts_before,
        cpe_before=cpe_before,
        bts_original_host=bts_original_host,
        cpe_original_host=cpe_original_host,
        source_v6=source_v6,
        link_timeout_s=link_timeout_s,
        poll_s=poll_s,
        rf_stable=2,
        ping_stable=2,
    )
    return True


async def _quick_bts_factory_check(factory_host: str, password: str) -> bool:
    try:
        conn = await open_sanity_ssh(
            factory_host,
            password,
            label="BTS factory probe",
            timeout_s=20,
        )
    except Exception:
        return False
    try:
        lan = normalize_ip(await sanity_ssh_run(conn, "uci -q get network.lan.ipaddr"))
        factory = normalize_ip(factory_host)
        return bool(lan) and lan == factory
    except Exception:
        return False
    finally:
        await close_sanity_ssh(conn)
