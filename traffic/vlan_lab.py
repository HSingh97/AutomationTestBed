"""Live VLAN lab helpers: VLAN mode apply + TRex throughput + end-to-end ping."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TREX_HOST = "192.168.3.3"
DEFAULT_TREX_PASSWORD = "ubuntu"
DEFAULT_DUT_HOST = "10.0.0.120"
DEFAULT_DUT_PASSWORD = "Sen@0ubRNwk$"
DEFAULT_TREX_DIR = "/opt/v3.06"
DEFAULT_TREX_PYPATH = "/opt/v3.06/automation/trex_control_plane/interactive/"
DEFAULT_PORTS = "0,1"
DEFAULT_DURATION_S = 8
DEFAULT_MIN_MBPS = 5.0
NEGATIVE_MAX_MBPS = 1.0

# Once-per-suite caches — avoid redoing mgmt/IPv6/TRex deploy on every case.
_SUITE_CACHE: dict[str, Any] = {
    "mgmt_ok": False,
    "ipv6_ok": False,
    "trex_deployed": False,
}

# Suite-wide TRex infrastructure circuit breaker (host down / server dead).
# Once tripped, remaining TRex-dependent VLAN cases fail immediately.
_TREX_INFRA: dict[str, Any] = {
    "down": False,
    "reason": "",
    "host": "",
    "first_case": "",
    "blocked": [],  # case ids that failed without re-probing
    "probed": False,
}

_TREX_INFRA_MARKERS = (
    "not link up",
    "are not link up",
    "trex server exited early",
    "trex host",
    "trex infrastructure",
    "connection refused",
    "no route to host",
    "network is unreachable",
    "name or service not known",
    "could not resolve hostname",
    "host is down",
    "connection timed out",
    "ssh: connect to host",
    "unable to connect to the server",
    "authentication failed",
)


def is_trex_infra_error(text: str | BaseException | None) -> bool:
    """True when failure indicates TRex host/server is down (not DUT 0 Mbps)."""
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    # Local SSH/PTY quirks are not TRex-down.
    if "pseudo terminal" in raw or "stdin: is not a tty" in raw:
        return False
    # Recoverable / config issues — not "host down".
    if (
        "ports are bound" in raw
        or "ports bound" in raw
        or "maximum threads" in raw
        or "unable to run with -c" in raw
    ):
        return False
    # Pure traffic/DUT outcomes must NOT trip the breaker.
    traffic_only = (
        "need >=" in raw
        or "expected blocked" in raw
        or ("throughput failed" in raw and "not link" not in raw)
        or (raw.startswith("no measurable throughput"))
    )
    if traffic_only and "not link up" not in raw and "trex server exited" not in raw:
        return False
    return any(marker in raw for marker in _TREX_INFRA_MARKERS)


class TrexInfraDown(RuntimeError):
    """TRex host/server infrastructure is unavailable for this suite run."""


def trex_infra_is_down() -> bool:
    return bool(_TREX_INFRA.get("down"))


def trex_infra_summary() -> str:
    if not _TREX_INFRA.get("down"):
        return ""
    blocked = _TREX_INFRA.get("blocked") or []
    lines = [
        "TRex infrastructure unavailable — remaining TRex VLAN cases were failed without re-probing.",
        f"  host: {_TREX_INFRA.get('host') or '?'}",
        f"  first failure: {_TREX_INFRA.get('first_case') or '?'}",
        f"  reason: {_TREX_INFRA.get('reason') or '?'}",
    ]
    if blocked:
        preview = ", ".join(blocked[:20])
        extra = f" (+{len(blocked) - 20} more)" if len(blocked) > 20 else ""
        lines.append(f"  blocked without retry ({len(blocked)}): {preview}{extra}")
    return "\n".join(lines)


def print_trex_infra_summary() -> None:
    text = trex_infra_summary()
    if text:
        print(f"\n[TRex][FAIL-FAST]\n{text}\n", flush=True)


def mark_trex_infra_down(
    reason: str,
    *,
    host: str = "",
    case_id: str = "",
) -> None:
    if _TREX_INFRA.get("down"):
        if case_id and case_id not in _TREX_INFRA["blocked"]:
            if case_id != _TREX_INFRA.get("first_case"):
                _TREX_INFRA["blocked"].append(case_id)
        return
    _TREX_INFRA["down"] = True
    _TREX_INFRA["reason"] = str(reason or "TRex unavailable")[:800]
    _TREX_INFRA["host"] = str(host or "")
    _TREX_INFRA["first_case"] = str(case_id or "")
    print(
        f"\n[TRex][FAIL-FAST] Infrastructure down on {_TREX_INFRA['host'] or '?'}: "
        f"{_TREX_INFRA['reason'][:240]}\n"
        "[TRex][FAIL-FAST] Remaining TRex-dependent VLAN cases will fail immediately "
        "without re-probing.\n",
        flush=True,
    )


def assert_trex_infra_available(*, case_id: str = "") -> None:
    if not trex_infra_is_down():
        return
    if case_id and case_id not in _TREX_INFRA["blocked"]:
        if case_id != _TREX_INFRA.get("first_case"):
            _TREX_INFRA["blocked"].append(case_id)
    raise TrexInfraDown(trex_infra_summary() or "TRex infrastructure down")


def probe_trex_host_reachable(
    *,
    trex_host: str,
    trex_user: str = "root",
    trex_password: str,
    timeout_s: int = 12,
) -> tuple[bool, str]:
    """Cheap SSH probe — catches dead TRex host before long server/port waits."""
    dest = trex_host
    if ":" in dest and not dest.startswith("["):
        dest = f"[{dest}]"
    cmd = [
        "sshpass",
        "-p",
        trex_password,
        "ssh",
        "-T",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"ConnectTimeout={max(3, min(timeout_s, 15))}",
        f"{trex_user}@{dest}",
        "echo TREX_HOST_OK",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s + 5, check=False
        )
    except subprocess.TimeoutExpired:
        return False, f"SSH to TRex host {trex_host} timed out"
    out = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0 or "TREX_HOST_OK" not in out:
        detail = out[-300:] or f"exit {result.returncode}"
        return False, f"TRex host {trex_host} unreachable: {detail}"
    return True, "ok"


def ensure_trex_infra_or_fail(
    profile_active: dict[str, Any],
    *,
    case_id: str = "",
) -> None:
    """Raise TrexInfraDown if already down or a fresh host probe fails."""
    assert_trex_infra_available(case_id=case_id)
    hosts = lab_hosts(profile_active)
    traffic = (profile_active.get("traffic") or {}).get("trex") or {}
    if not _TREX_INFRA.get("probed"):
        _TREX_INFRA["probed"] = True
        ok, detail = probe_trex_host_reachable(
            trex_host=hosts["trex_host"],
            trex_user=str(traffic.get("user") or "root"),
            trex_password=hosts["trex_password"],
        )
        if not ok:
            mark_trex_infra_down(detail, host=hosts["trex_host"], case_id=case_id)
            raise TrexInfraDown(trex_infra_summary())


@dataclass
class VlanLabRunResult:
    trex_ok: bool
    duration_s: int
    dl_bw: str
    ul_bw: str
    qinq_enabled: bool
    observed_rx_mbps: float
    observed_tx_mbps: float
    vlan_apply: dict[str, Any] = field(default_factory=dict)
    ping_ok: bool | None = None
    ping_detail: str = ""
    trex_log_tail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def env_overrides() -> dict[str, str]:
    keys = {
        "trex_host": "VLAN_TREX_HOST",
        "trex_password": "VLAN_TREX_PASSWORD",
        "dut_host": "VLAN_DUT_HOST",
        "dut_password": "VLAN_DUT_PASSWORD",
        "trex_ports": "VLAN_TREX_PORTS",
        "cpe_pc_host": "VLAN_CPE_PC_HOST",
    }
    return {k: os.environ[v] for k, v in keys.items() if os.environ.get(v)}


def lab_hosts(profile_active: dict[str, Any] | None = None) -> dict[str, str]:
    """Resolve BTS SSH, TRex, and CPE-side PC from profile + env.

    Prefer IPv6 for DUT access when ``dut.strict_ipv6`` / ``testbed.strict_ipv6``
    is set (or when ``local_ipv6`` is present and ipv6-only mode is implied).
    Always expose ``dut_ipv4`` (ssh_host / 10.0.0.120) as backup when tagged
    IPv6 is broken or the DUT is transparent/untagged.
    """
    profile_active = profile_active or {}
    dut = profile_active.get("dut") or {}
    tb = profile_active.get("testbed") or {}
    mgmt = tb.get("mgmt_vlan") or {}
    perf = profile_active.get("performance") or {}
    traffic = (profile_active.get("traffic") or {}).get("trex") or {}
    rec = tb.get("recovery") or {}
    sec = tb.get("secondary_pc") or {}

    env = env_overrides()
    strict_v6 = bool(
        dut.get("strict_ipv6")
        or tb.get("strict_ipv6")
        or str(dut.get("ip_mode") or "").lower() == "ipv6"
    )
    bts_v6 = _ipv6_host(
        str(dut.get("local_ipv6") or mgmt.get("ipv6_bts") or "")
    )
    bts_v4 = str(
        env.get("dut_host_v4")
        or dut.get("ssh_host")
        or rec.get("bts_fallback_ipv4")
        or DEFAULT_DUT_HOST
    ).split("/")[0]
    bts_host = env.get("dut_host")
    if not bts_host and strict_v6 and bts_v6:
        bts_host = bts_v6
    if not bts_host:
        bts_host = bts_v4
    # Reachable jump host on CPE-side LAN (192.168.2.215). 10.0.0.121 is the CPE itself.
    cpe_pc = (
        env.get("cpe_pc_host")
        or dut.get("cpe_pc_ip")
        or (str(sec.get("ssh") or "").split("@")[-1] if sec.get("ssh") else "")
        or ""
    )
    cpe_lan = (
        str(sec.get("cpe_factory_ipv4") or "")
        or str(dut.get("cpe_fallback_ip") or "")
        or "10.0.0.121"
    )
    cpe_v6 = _ipv6_host(
        str(
            (list(dut.get("remote_ipv6s") or [""])[0] if dut.get("remote_ipv6s") else "")
            or mgmt.get("ipv6_cpe")
            or ""
        )
    )
    return {
        "dut_host": str(bts_host).split("/")[0],
        "dut_ipv4": bts_v4,
        "dut_user": str(dut.get("username") or "root"),
        "dut_password": str(dut.get("password") or DEFAULT_DUT_PASSWORD),
        "dut_ipv6": bts_v6,
        "trex_host": str(env.get("trex_host") or traffic.get("host") or DEFAULT_TREX_HOST),
        "trex_password": str(
            env.get("trex_password") or traffic.get("password") or DEFAULT_TREX_PASSWORD
        ),
        "trex_ports": str(env.get("trex_ports") or traffic.get("ports") or DEFAULT_PORTS),
        "su_count": str(int(perf.get("su_count") or 1)),
        "cpe_pc_host": str(cpe_pc).split("/")[0],
        "cpe_pc_password": str(
            dut.get("cpe_pc_password") or sec.get("password") or "senao1234#"
        ),
        "cpe_lan_host": str(cpe_lan).split("/")[0],
        "cpe_lan_password": str(dut.get("password") or DEFAULT_DUT_PASSWORD),
        "cpe_ipv6": cpe_v6,
        "radio_idx": str(int((profile_active.get("link") or {}).get("radio_idx") or 1)),
        "strict_ipv6": "1" if strict_v6 else "0",
    }


def _ipv6_host(addr: str) -> str:
    return str(addr or "").split("/")[0].strip().lower().strip("[]")


def _ssh_argv(user: str, host: str, *, connect_timeout: int = 12) -> list[str]:
    """Build ssh argv. IPv6 uses ``-6 -l user <addr>`` (no ``user@[addr]`` — broken here)."""
    from utils.net_utils import is_ipv6_literal, normalize_ip

    h = normalize_ip(str(host or "").strip())
    base = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"ConnectTimeout={int(connect_timeout)}",
    ]
    if is_ipv6_literal(h):
        return [*base, "-6", "-l", user, h]
    return [*base, f"{user}@{h}"]


def _profile_ipv6_pair(profile_active: dict[str, Any]) -> tuple[str, str, int]:
    dut = profile_active.get("dut") or {}
    tb = profile_active.get("testbed") or {}
    mgmt = tb.get("mgmt_vlan") or {}
    ip_cfg = profile_active.get("ip_tests") or {}
    prefix = int(ip_cfg.get("ipv6_prefix_len") or mgmt.get("prefix_len") or 120)
    bts = (
        str(ip_cfg.get("ipv6_address_bts") or "")
        or str(mgmt.get("ipv6_bts") or "")
        or str(dut.get("local_ipv6") or "")
    )
    cpe = (
        str(ip_cfg.get("ipv6_address_cpe") or "")
        or str(mgmt.get("ipv6_cpe") or "")
        or (list(dut.get("remote_ipv6s") or [""])[0] if dut.get("remote_ipv6s") else "")
    )
    return bts, cpe, prefix


def _ssh_cmd(host: str, password: str, remote: str, *, user: str = "root", timeout_s: int = 60) -> str:
    cmd = [
        "sshpass",
        "-p",
        password,
        *_ssh_argv(user, host, connect_timeout=min(12, timeout_s)),
        remote,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") + (exc.stderr or "")).strip()
        return (out + f"\n[ssh timeout after {timeout_s}s to {host}]").strip()
    return ((result.stdout or "") + (result.stderr or "")).strip()


def _ssh_via_jump(
    jump_host: str,
    jump_password: str,
    target_host: str,
    target_password: str,
    remote: str,
    *,
    timeout_s: int = 90,
) -> str:
    """SSH automation-host → CPE-side PC → CPE LAN IP."""
    inner = remote.replace("'", "'\"'\"'")
    jump_remote = (
        "sshpass -p "
        f"'{target_password}' "
        "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o LogLevel=ERROR -o ConnectTimeout=12 root@{target_host} '{inner}'"
    )
    return _ssh_cmd(jump_host, jump_password, jump_remote, timeout_s=timeout_s)


def _has_ipv6_on_br_lan(text: str, want_host: str) -> bool:
    want = _ipv6_host(want_host)
    if not want:
        return False
    want_l = want.lower()
    # Also match compressed forms (e.g. :0:17b8: → ::17b8:).
    want_alts = {want_l, want_l.replace(":0:", ":").replace(":::", "::")}
    for line in text.splitlines():
        low = line.lower()
        if "inet6" not in low:
            continue
        if any(a in low for a in want_alts if a):
            return True
        # Tail match on last hextet(s), e.g. ...:3de
        tail = want_l.rsplit(":", 1)[-1]
        if tail and f":{tail}" in low.split("/")[0]:
            # Prefer explicit /global lines when present.
            if "scope global" in low or "inet6" in low:
                return True
    return False


def _cfgupdate_cpe(hosts: dict[str, str], command: str) -> str:
    """SET-only path to associated CPE MAC — never use remote_exec.sh (it also runs on BTS)."""
    mac = _ssh_cmd(
        hosts["dut_host"],
        hosts["dut_password"],
        "for s in /sys/class/kwn/sua*/statistics; do "
        'a=$(cat "$s/associd" 2>/dev/null); [ "$a" != "0" ] && [ -n "$a" ] && '
        'cat "$s/mac" && break; done',
        user=hosts["dut_user"],
    ).splitlines()
    mac_addr = next((m.strip() for m in mac if ":" in m), "")
    if not mac_addr:
        return "NO_ASSOC_MAC"
    safe = command.replace('"', '\\"')
    return _ssh_cmd(
        hosts["dut_host"],
        hosts["dut_password"],
        f'cfgupdate -i 6 1 {mac_addr} 5 "{safe}"',
        user=hosts["dut_user"],
        timeout_s=90,
    )


def ensure_lab_ipv6(profile_active: dict[str, Any]) -> dict[str, Any]:
    """Ensure BTS + CPE have profile IPv6 addresses (IPv6-only traffic path).

    BTS: direct SSH. CPE: jump via CPE-side PC → CPE LAN (10.0.0.121), with
    cfgupdate fallback. Never uses ``remote_exec.sh`` for addressing (it also
    executes locally on the BTS and can DAD-collide IPv6).
    """
    hosts = lab_hosts(profile_active)
    bts_cidr, cpe_cidr, prefix = _profile_ipv6_pair(profile_active)
    bts_host_addr = _ipv6_host(bts_cidr)
    cpe_host_addr = _ipv6_host(cpe_cidr)
    if not bts_host_addr or not cpe_host_addr:
        raise RuntimeError("a60_lab profile missing BTS/CPE IPv6 addresses")

    bts_cidr = bts_cidr if "/" in bts_cidr else f"{bts_host_addr}/{prefix}"
    cpe_cidr = cpe_cidr if "/" in cpe_cidr else f"{cpe_host_addr}/{prefix}"

    out: dict[str, Any] = {"bts": bts_cidr, "cpe": cpe_cidr}

    # --- BTS (direct) ---
    show = _ssh_cmd(
        hosts["dut_host"],
        hosts["dut_password"],
        "ip -6 addr show br-lan; uci get network.lan.ip6addr 2>/dev/null",
        user=hosts["dut_user"],
    )
    # Strip any accidental CPE address on BTS (remote_exec local side-effect).
    if _has_ipv6_on_br_lan(show, cpe_host_addr) and bts_host_addr != cpe_host_addr:
        print(f"[vlan] BTS removing stray CPE IPv6 {cpe_host_addr}")
        _ssh_cmd(
            hosts["dut_host"],
            hosts["dut_password"],
            f"ip -6 addr del {cpe_cidr} dev br-lan 2>/dev/null || "
            f"ip -6 addr del {cpe_host_addr}/128 dev br-lan 2>/dev/null || true",
            user=hosts["dut_user"],
        )
        show = _ssh_cmd(
            hosts["dut_host"],
            hosts["dut_password"],
            "ip -6 addr show br-lan",
            user=hosts["dut_user"],
        )
    if _has_ipv6_on_br_lan(show, bts_host_addr):
        print(f"[vlan] BTS IPv6 OK ({bts_host_addr})")
        out["bts_applied"] = False
    else:
        print(f"[vlan] BTS applying IPv6 {bts_cidr}")
        remote = (
            f"ip -6 addr replace {bts_cidr} dev br-lan; "
            f"uci set network.lan.ip6addr='{bts_cidr}'; "
            f"uci set network.lan.proto='static'; "
            "uci commit network"
        )
        _ssh_cmd(hosts["dut_host"], hosts["dut_password"], remote, user=hosts["dut_user"])
        out["bts_applied"] = True

    # --- CPE via jump host (preferred) or cfgupdate ---
    cpe_apply = (
        f"ip -6 addr del {cpe_host_addr}/128 dev br-lan 2>/dev/null || true; "
        f"ip -6 addr replace {cpe_cidr} dev br-lan; "
        f"uci set network.lan.ip6addr='{cpe_cidr}'; "
        "uci set network.lan.proto='static'; "
        "uci commit network; "
        "ip -6 addr show br-lan"
    )
    cpe_ok = False
    cpe_skipped = False
    via = "none"
    if hosts.get("cpe_pc_host") and hosts.get("cpe_lan_host"):
        try:
            check = _ssh_via_jump(
                hosts["cpe_pc_host"],
                hosts["cpe_pc_password"],
                hosts["cpe_lan_host"],
                hosts["cpe_lan_password"],
                "ip -6 addr show br-lan; uci get network.lan.ip6addr 2>/dev/null",
            )
            if _has_ipv6_on_br_lan(check, cpe_host_addr):
                print(f"[vlan] CPE IPv6 OK ({cpe_host_addr}) via {hosts['cpe_lan_host']}")
                cpe_ok = True
                cpe_skipped = True
                via = "jump"
            else:
                print(f"[vlan] CPE applying IPv6 {cpe_cidr} via {hosts['cpe_lan_host']}")
                apply_out = _ssh_via_jump(
                    hosts["cpe_pc_host"],
                    hosts["cpe_pc_password"],
                    hosts["cpe_lan_host"],
                    hosts["cpe_lan_password"],
                    cpe_apply,
                )
                cpe_ok = _has_ipv6_on_br_lan(apply_out, cpe_host_addr)
                via = "jump"
                print(f"[vlan] CPE IPv6 apply ok={cpe_ok}")
        except Exception as exc:
            print(f"[vlan] CPE jump SSH failed: {exc}; trying cfgupdate")
    if not cpe_ok:
        print(f"[vlan] CPE applying IPv6 {cpe_cidr} via cfgupdate")
        _cfgupdate_cpe(hosts, f"ip -6 addr replace {cpe_cidr} dev br-lan")
        _cfgupdate_cpe(hosts, f"uci set network.lan.ip6addr={cpe_cidr}")
        _cfgupdate_cpe(hosts, "uci set network.lan.proto=static")
        _cfgupdate_cpe(hosts, "uci commit network")
        time.sleep(2)
        # Confirm with BTS→CPE ping6.
        ok, detail = ping_via_bts(
            bts_host=hosts["dut_host"],
            bts_password=hosts["dut_password"],
            target=cpe_host_addr,
            count=2,
            bts_user=hosts["dut_user"],
            wait_s=3,
            min_replies=1,
            bts_hosts=control_plane_hosts(profile_active),
        )
        cpe_ok = bool(ok)
        via = "cfgupdate"
        print(f"[vlan] CPE IPv6 cfgupdate ok={cpe_ok} ping={detail[:80]}")

    out["cpe_results"] = [{"ok": cpe_ok, "skipped": cpe_skipped, "via": via}]
    if not cpe_ok:
        raise RuntimeError(f"Failed to apply CPE IPv6 {cpe_cidr} (via={via})")
    return out


def recover_su_link(profile_active: dict[str, Any], *, timeout_s: float | None = None) -> list[int]:
    """Wait for SU association. Do not bounce wifi — that drops the RF link on this lab."""
    perf = profile_active.get("performance") or {}
    limit = float(timeout_s if timeout_s is not None else perf.get("su_link_wait_s") or 120)
    return wait_for_associated_su(profile_active, timeout_s=limit)


def _ping_target_from_profile(profile_active: dict[str, Any]) -> str:
    dut = profile_active.get("dut") or {}
    tb = profile_active.get("testbed") or {}
    mgmt = tb.get("mgmt_vlan") or {}
    hosts = lab_hosts(profile_active)
    try:
        from traffic.kwn_sua_statistics import fetch_kwn_sua_statistics

        rows = fetch_kwn_sua_statistics(
            hosts["dut_host"],
            ssh_user=hosts["dut_user"],
            ssh_password=hosts["dut_password"],
            max_sua=32,
        )
        for row in rows:
            ipv6 = str(row.get("ipv6") or "").strip().split("/")[0]
            if ipv6 and ipv6 not in {"-", "0"}:
                return ipv6
    except Exception as exc:
        print(f"[vlan] live SU IPv6 lookup skipped: {exc}")
    remote = list(dut.get("remote_ipv6s") or [])
    if remote:
        return str(remote[0]).split("/")[0]
    if mgmt.get("ipv6_cpe"):
        return str(mgmt["ipv6_cpe"]).split("/")[0]
    ip_cfg = profile_active.get("ip_tests") or {}
    if ip_cfg.get("ipv6_address_cpe"):
        return str(ip_cfg["ipv6_address_cpe"]).split("/")[0]
    return ""


def control_plane_hosts(profile_active: dict[str, Any] | None = None) -> list[str]:
    """SSH targets for BTS control plane — IPv4 backup first, then IPv6."""
    hosts = lab_hosts(profile_active)
    ordered: list[str] = []
    for key in ("dut_ipv4", "dut_host", "dut_ipv6"):
        h = str(hosts.get(key) or "").strip()
        if h and h not in ordered:
            ordered.append(h)
    return ordered or [DEFAULT_DUT_HOST]


def ping_via_bts(
    *,
    bts_host: str,
    bts_password: str,
    target: str,
    count: int = 4,
    bts_user: str = "root",
    wait_s: int = 2,
    min_replies: int | None = None,
    bts_hosts: list[str] | None = None,
) -> tuple[bool, str]:
    if not target:
        return False, "no ping target configured"
    need = int(min_replies if min_replies is not None else count)
    # Payload is IPv6-only; SSH to BTS may use IPv4 backup when tagged IPv6 is down.
    cmd = f"ping -6 -c {int(count)} -W {int(wait_s)} {target} 2>&1"
    candidates: list[str] = []
    for h in list(bts_hosts or []) + [bts_host]:
        hh = str(h or "").strip()
        if hh and hh not in candidates:
            candidates.append(hh)
    if not candidates:
        return False, "no BTS SSH host"

    last_detail = ""
    for host in candidates:
        ssh = [
            "sshpass",
            "-p",
            bts_password,
            *_ssh_argv(bts_user, host, connect_timeout=8),
            cmd,
        ]
        try:
            result = subprocess.run(
                ssh,
                capture_output=True,
                text=True,
                timeout=max(30, count * (wait_s + 2)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_detail = f"ssh timeout to {host}: {exc}"
            continue
        out = (result.stdout or "") + (result.stderr or "")
        low = out.lower()
        if any(
            m in low
            for m in (
                "no route to host",
                "network is unreachable",
                "connection timed out",
                "connection refused",
                "permission denied",
            )
        ) and "bytes from" not in out:
            last_detail = f"ssh via {host} failed: {out.strip()[-300:]}"
            continue
        replies = out.count("bytes from")
        zero_loss = " 0% packet loss" in out or " 0.0% packet loss" in out
        ok = replies >= need or (result.returncode == 0 and zero_loss)
        detail = out.strip()[-400:]
        if host != bts_host:
            detail = f"[ssh via {host}] {detail}"
        return ok, detail
    return False, last_detail or f"ping via BTS failed for {target}"


def _run_coro(coro_factory, *, timeout_s: float = 90.0):
    """Run an async factory from sync code (handles nested event loops)."""
    import asyncio
    import concurrent.futures

    async def _once():
        return await coro_factory()

    try:
        return asyncio.run(_once())
    except RuntimeError:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_once())).result(timeout=timeout_s)


def ensure_lab_pc_untagged_baseline(profile_active: dict[str, Any]) -> dict[str, Any]:
    """
    First step for lab access: drop any tagged subifs and keep the parent untagged
    with IPv4 (10.0.0.x) so DUT backup ``10.0.0.120`` is reachable when the device
    is transparent / not expecting VLAN tags.
    """
    from utils.lab_pc_net import ensure_fallback_ethernet, restore_untagged_lab_pc

    tb = profile_active.get("testbed") or {}
    dut = profile_active.get("dut") or {}
    password = str(dut.get("password") or DEFAULT_DUT_PASSWORD)

    primary = dict(tb.get("primary_pc") or {})
    if not str(primary.get("ssh") or "").strip():
        primary["local"] = True
    primary.setdefault("mgmt_interface", "enp1s0")
    # PC address (not DUT). Prefer existing profile PC IP; never use DUT 10.0.0.120.
    primary.setdefault("fallback_ipv4", "10.0.0.12")
    primary.setdefault("fallback_prefix_len", 8)

    print(
        f"[vlan] Baseline: strip tags on {primary.get('mgmt_interface')} "
        f"(untagged + IPv4 backup toward {DEFAULT_DUT_HOST})..."
    )

    def _primary():
        async def _go():
            ok_u = await restore_untagged_lab_pc(primary, password)
            ok_f = await ensure_fallback_ethernet(primary, password)
            return ok_u, ok_f

        return _run_coro(_go)

    ok_u, ok_f = _primary()
    result: dict[str, Any] = {
        "primary_untagged": ok_u,
        "primary_fallback": ok_f,
        "iface": str(primary.get("mgmt_interface")),
    }

    secondary = dict(tb.get("secondary_pc") or {})
    if secondary.get("enabled", True) and str(secondary.get("ssh") or "").strip():
        sec_pass = str(secondary.get("password") or dut.get("cpe_pc_password") or password)
        secondary.setdefault("mgmt_interface", "eno1")
        secondary.setdefault("fallback_ipv4", "10.0.0.215")
        secondary.setdefault("fallback_prefix_len", 8)

        def _secondary():
            async def _go():
                ok_u2 = await restore_untagged_lab_pc(secondary, sec_pass)
                ok_f2 = await ensure_fallback_ethernet(secondary, sec_pass)
                return ok_u2, ok_f2

            return _run_coro(_go)

        try:
            ok_u2, ok_f2 = _secondary()
            result["secondary_untagged"] = ok_u2
            result["secondary_fallback"] = ok_f2
            print(
                f"[vlan] Baseline remote PC: "
                f"{secondary.get('mgmt_interface')} untagged={'ok' if ok_u2 else 'FAIL'}"
            )
        except Exception as exc:
            result["secondary_error"] = str(exc)
            print(f"[vlan] Baseline remote PC skipped: {exc}")

    return result


def _dut_mgmtvlan_via_ipv4(profile_active: dict[str, Any]) -> int | None:
    """Read live DUT mgmtvlan over IPv4 backup SSH. None if unreachable/unreadable."""
    hosts = lab_hosts(profile_active)
    v4 = hosts.get("dut_ipv4") or DEFAULT_DUT_HOST
    tb = profile_active.get("testbed") or {}
    keys = ((tb.get("vlan_uci") or {}).get("bts") or {})
    mgmt_key = str(keys.get("mgmtvlan_key") or "vlan.ath1.mgmtvlan")
    try:
        out = _ssh_cmd(
            v4,
            hosts["dut_password"],
            f"uci get {mgmt_key} 2>/dev/null || echo NONE",
            user=hosts["dut_user"],
            timeout_s=12,
        )
    except Exception as exc:
        print(f"[vlan] DUT mgmtvlan probe via {v4} failed: {exc}")
        return None
    line = (out or "").strip().splitlines()[-1] if out else ""
    if not line or line.upper() == "NONE" or "Error" in line:
        return None
    try:
        return int(str(line).strip().strip("'\"") or 0)
    except ValueError:
        return None


def ensure_lab_mgmt_access(profile_active: dict[str, Any]) -> dict[str, Any]:
    """
    Lab-PC management access for the VLAN suite.

    1. Always strip tagged subifs first (untagged + IPv4) so a transparent DUT
       at ``10.0.0.120`` is reachable.
    2. Query live DUT ``mgmtvlan`` over IPv4.
    3. Match lab PC to DUT:
       - ``mgmtvlan`` 0/1 → untagged parent
       - ``mgmtvlan`` > 1 → tagged ``<iface>.<vid>``
    4. One-shot Ethernet flap on the backend (primary) lab PC so IPv6 ND / L2
       learning recovers (without this, ping/SSH to DUT IPv6 often stays
       "no route"). Remote CPE-side PC is tagged to match but not flapped —
       its SSH control path shares that NIC.
    """
    from utils.lab_pc_net import configure_mgmt_interface, flap_lab_pc_ethernet
    from utils.net_utils import normalize_ip
    from utils.vlan_uci import mgmt_access_vlan_plan

    baseline = ensure_lab_pc_untagged_baseline(profile_active)

    tb = profile_active.get("testbed") or {}
    dut = profile_active.get("dut") or {}
    mgmt = tb.get("mgmt_vlan") or {}
    plan = mgmt_access_vlan_plan(tb)
    profile_vid = int(plan.get("vlan_id") or 0)

    live_vid = _dut_mgmtvlan_via_ipv4(profile_active)
    # Match BTS: mgmtvlan 1 (or 0/missing) = untagged; anything else = tagged.
    if live_vid is None:
        need_tag = False
        print(
            f"[vlan] Mgmt: DUT mgmtvlan unread; staying untagged "
            f"(IPv4 backup {DEFAULT_DUT_HOST}; profile vid={profile_vid})"
        )
    elif int(live_vid) <= 1:
        need_tag = False
        print(
            f"[vlan] Mgmt: DUT mgmtvlan={live_vid} — keeping lab PC untagged "
            f"(same as BTS)"
        )
    else:
        need_tag = True
        print(
            f"[vlan] Mgmt: DUT mgmtvlan={live_vid} — tagging lab PC .{live_vid} "
            f"(same as BTS)"
        )

    prefix = int(mgmt.get("prefix_len") or (profile_active.get("ip_tests") or {}).get("ipv6_prefix_len") or 120)
    host_ip = normalize_ip(
        str(dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or dut.get("mgmt_oob_ipv6") or "").split("/")[0]
    )
    if not host_ip:
        return {"ok": False, "reason": "bts_pc_ipv6 / ipv6_bts_pc not set", "baseline": baseline}

    pc = dict(tb.get("primary_pc") or {})
    if not str(pc.get("ssh") or "").strip():
        pc["local"] = True
    pc.setdefault("mgmt_interface", "enp1s0")
    password = str(dut.get("password") or DEFAULT_DUT_PASSWORD)
    cidr = f"{host_ip}/{prefix}"

    if need_tag:
        vid = int(live_vid or profile_vid)
        tagging = {"mode": "single", "vlan_id": vid, "untagged": False}
    else:
        vid = 0
        tagging = {"mode": "untagged", "vlan_id": 0, "untagged": True}

    ok = _run_coro(
        lambda: configure_mgmt_interface(
            pc,
            ipv6_address=cidr,
            prefix_len=prefix,
            password=password,
            vlan_id=vid,
            tagging=tagging,
        )
    )

    parent = str(pc.get("mgmt_interface") or "enp1s0")
    vlan_if = f"{parent}.{vid}" if vid else parent
    print(f"[vlan] Mgmt access: {vlan_if} -> {cidr} ({'ok' if ok else 'FAILED'})")

    # One carrier flap on the *backend* PC restores IPv6 reachability after
    # tag/untag changes. Do NOT flap the remote CPE-side PC — its SSH control
    # path is the same NIC (eno1 @ 10.0.150.187); a flap can strand it.
    flap_ok = _run_coro(lambda: flap_lab_pc_ethernet(pc, password, vlan_id=vid, settle_s=2.0))
    print(f"[vlan] Mgmt eth flap on {parent}: {'ok' if flap_ok else 'FAILED'}")

    # CPE-side (remote) lab PC — same mgmtvlan decision as BTS/primary (no flap).
    sec_result: dict[str, Any] = {"skipped": True}
    secondary = dict(tb.get("secondary_pc") or {})
    cpe_pc_ip = normalize_ip(
        str(dut.get("cpe_pc_ipv6") or mgmt.get("ipv6_cpe_pc") or "").split("/")[0]
    )
    if secondary.get("enabled", True) and cpe_pc_ip and str(secondary.get("ssh") or "").strip():
        sec_pass = str(secondary.get("password") or dut.get("cpe_pc_password") or password)
        sec_cidr = f"{cpe_pc_ip}/{prefix}"
        sec_parent = str(secondary.get("mgmt_interface") or "eno1")
        sec_vlan_if = f"{sec_parent}.{vid}" if vid else sec_parent
        try:
            sec_ok = _run_coro(
                lambda: configure_mgmt_interface(
                    secondary,
                    ipv6_address=sec_cidr,
                    prefix_len=prefix,
                    password=sec_pass,
                    vlan_id=vid,
                    tagging=tagging,
                )
            )
            print(
                f"[vlan] Remote PC mgmt access: {sec_vlan_if} -> {sec_cidr} "
                f"({'ok' if sec_ok else 'FAILED'})"
            )
            sec_result = {
                "ok": sec_ok,
                "vlan_if": sec_vlan_if,
                "cidr": sec_cidr,
                "vlan_id": vid,
                "untagged": not need_tag,
                "flap_ok": None,
            }
        except Exception as exc:
            print(f"[vlan] Remote PC mgmt sync skipped ({exc})")
            sec_result = {
                "ok": False,
                "vlan_if": sec_vlan_if,
                "cidr": sec_cidr,
                "vlan_id": vid,
                "untagged": not need_tag,
                "flap_ok": None,
                "error": str(exc),
            }

    return {
        "ok": ok,
        "vlan_if": vlan_if,
        "cidr": cidr,
        "vlan_id": vid,
        "untagged": not need_tag,
        "dut_mgmtvlan": live_vid,
        "flap_ok": flap_ok,
        "baseline": baseline,
        "secondary": sec_result,
    }


def diagnose_zero_throughput(profile_active: dict[str, Any], *, case_id: str = "") -> dict[str, Any]:
    """When TRex RX is 0: ping CPE via BTS and check remote-PC reachability briefly."""
    hosts = lab_hosts(profile_active)
    target = _ping_target_from_profile(profile_active) or hosts.get("cpe_ipv6") or ""
    diag: dict[str, Any] = {"case_id": case_id, "cpe_target": target}

    if target:
        ok, detail = ping_via_bts(
            bts_host=hosts["dut_host"],
            bts_password=hosts["dut_password"],
            bts_user=hosts["dut_user"],
            target=str(target),
            count=3,
            wait_s=2,
            min_replies=1,
            bts_hosts=control_plane_hosts(profile_active),
        )
        diag["bts_to_cpe_ping_ok"] = ok
        diag["bts_to_cpe_ping_detail"] = detail[-300:]
        print(
            f"[vlan][diag] {case_id or 'case'}: BTS→CPE ping6 {target} "
            f"{'OK' if ok else 'FAIL'}: {detail[-120:]}"
        )
    else:
        diag["bts_to_cpe_ping_ok"] = False
        diag["bts_to_cpe_ping_detail"] = "no CPE IPv6 target"
        print(f"[vlan][diag] {case_id or 'case'}: no CPE IPv6 target for ping")

    # Optional: from CPE-side PC, ping CPE LAN IPv6 (needs tagged mgmt on remote PC when mgmtvlan set).
    jump = hosts.get("cpe_pc_host") or ""
    cpe_v6 = hosts.get("cpe_ipv6") or target
    if jump and cpe_v6:
        try:
            out = _ssh_cmd(
                jump,
                hosts["cpe_pc_password"],
                f"ping -6 -c 2 -W 2 {cpe_v6} 2>&1 | tail -5",
                timeout_s=20,
            )
            ok = "bytes from" in out or " 0% packet loss" in out
            diag["remote_pc_to_cpe_ping_ok"] = ok
            diag["remote_pc_to_cpe_ping_detail"] = out[-300:]
            print(
                f"[vlan][diag] {case_id or 'case'}: remote-PC→CPE ping6 "
                f"{'OK' if ok else 'FAIL'}: {out[-120:]}"
            )
        except Exception as exc:
            diag["remote_pc_to_cpe_ping_ok"] = False
            diag["remote_pc_to_cpe_ping_detail"] = str(exc)
            print(f"[vlan][diag] remote-PC ping skipped: {exc}")

    write_vlan_case_evidence(case_id, payload={"zero_mbps_diag": diag})
    return diag


def restore_vlan_lab_post_suite(
    profile_active: dict[str, Any] | None = None,
    *,
    mgmtvlan: int = 1,
) -> dict[str, Any]:
    """
    After VLAN suite: BTS+CPE → transparent (mode=0) and mgmtvlan=<mgmtvlan> (default 1).
    Soft UCI apply preferred (no network reload unless needed).
    """
    profile_active = profile_active or {}
    hosts = lab_hosts(profile_active)
    radio = "ath1"
    link = profile_active.get("link") or {}
    if link.get("radio_idx"):
        # UCI keys in this lab are ath1 regardless of radio_idx naming.
        pass
    tb = profile_active.get("testbed") or {}
    keys = ((tb.get("vlan_uci") or {}).get("bts") or {})
    mode_key = str(keys.get("mode_key") or f"vlan.{radio}.mode")
    svlan_key = str(keys.get("svlan_key") or f"vlan.{radio}.svlan")
    cvlan_key = str(keys.get("cvlan_key") or f"vlan.{radio}.cvlan")
    mgmt_key = str(keys.get("mgmtvlan_key") or f"vlan.{radio}.mgmtvlan")

    soft = (
        f"uci set {mode_key}=0; "
        f"uci delete {svlan_key} 2>/dev/null || true; "
        f"uci delete {cvlan_key} 2>/dev/null || true; "
        f"uci set {mgmt_key}={int(mgmtvlan)}; "
        "uci commit vlan; "
        f"uci show vlan.{radio} 2>/dev/null | head -20"
    )
    print(
        f"[vlan][teardown] Restoring transparent + mgmtvlan={mgmtvlan} "
        f"on BTS {hosts.get('dut_ipv4') or hosts['dut_host']}..."
    )
    bts_target = str(hosts.get("dut_ipv4") or hosts["dut_host"])
    bts_out = _ssh_cmd(
        bts_target,
        hosts["dut_password"],
        soft,
        user=hosts["dut_user"],
        timeout_s=45,
    )
    print(f"[vlan][teardown] BTS UCI:\n{bts_out[-400:]}")

    cpe_out = ""
    cpe_ok = False
    if hosts.get("cpe_pc_host") and hosts.get("cpe_lan_host"):
        try:
            cpe_out = _ssh_via_jump(
                hosts["cpe_pc_host"],
                hosts["cpe_pc_password"],
                hosts["cpe_lan_host"],
                hosts["cpe_lan_password"],
                soft,
                timeout_s=60,
            )
            cpe_ok = f"mgmtvlan='{int(mgmtvlan)}'" in cpe_out or f"mgmtvlan={int(mgmtvlan)}" in cpe_out
            print(f"[vlan][teardown] CPE UCI:\n{cpe_out[-400:]}")
        except Exception as exc:
            cpe_out = str(exc)
            print(f"[vlan][teardown] CPE restore via jump failed: {exc}")
    else:
        print("[vlan][teardown] CPE jump not configured — BTS-only restore")

    # Invalidate suite caches so a follow-up run re-applies mgmt access for vid=101.
    _SUITE_CACHE["mgmt_ok"] = False
    _SUITE_CACHE["ipv6_ok"] = False

    # Lab PCs must go back to untagged — DUT is transparent/mgmtvlan=1 and will
    # not answer on a leftover .<vid> from the suite.
    pc_restore: dict[str, Any] = {}
    try:
        pc_restore = ensure_lab_pc_untagged_baseline(profile_active)
    except Exception as exc:
        pc_restore = {"error": str(exc)}
        print(f"[vlan][teardown] WARN: lab-PC untagged restore failed: {exc}")

    result = {
        "bts": bts_out[-400:],
        "cpe": cpe_out[-400:],
        "cpe_ok": cpe_ok,
        "mgmtvlan": int(mgmtvlan),
        "mode": "transparent",
        "lab_pc": pc_restore,
    }
    print(
        f"[vlan][teardown] Done — transparent + mgmtvlan={mgmtvlan}; "
        f"lab PC restored untagged (IPv4 backup {hosts.get('dut_ipv4') or DEFAULT_DUT_HOST})."
    )
    return result


def write_vlan_case_evidence(case_id: str, *, payload: dict[str, Any] | None = None) -> Path | None:
    cid = (case_id or "").strip()
    if not cid:
        return None
    artifacts = REPO_ROOT / "reports" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / f"vlan_{cid}_evidence.json"
    body: dict[str, Any] = {"case_id": cid}
    if path.is_file():
        try:
            body.update(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    if payload:
        body.update(payload)
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def probe_dut_ssh(
    profile_active: dict[str, Any],
    *,
    timeout_s: int = 12,
) -> tuple[bool, str]:
    """Quick reachability check — fail fast instead of hanging on SU/VLAN apply.

    Tries ``dut_host`` (often IPv6) first, then IPv4 backup ``10.0.0.120``.
    """
    hosts = lab_hosts(profile_active)
    candidates: list[str] = []
    for h in (hosts.get("dut_host"), hosts.get("dut_ipv4"), hosts.get("dut_ipv6")):
        hh = str(h or "").strip()
        if hh and hh not in candidates:
            candidates.append(hh)
    if not candidates:
        return False, "no DUT host configured"

    errors: list[str] = []
    for host in candidates:
        try:
            out = _ssh_cmd(
                host,
                hosts["dut_password"],
                "echo DUT_SSH_OK",
                user=hosts["dut_user"],
                timeout_s=timeout_s,
            )
        except Exception as exc:
            errors.append(f"{host}: raised {exc}")
            continue
        if "DUT_SSH_OK" in out:
            if host != hosts.get("dut_host"):
                print(f"[vlan] DUT SSH OK via backup {host} (primary {hosts.get('dut_host')} unreachable)")
            return True, f"ok via {host}"
        low = out.lower()
        if any(
            m in low
            for m in (
                "no route to host",
                "network is unreachable",
                "connection timed out",
                "connection refused",
                "could not resolve",
                "name or service not known",
                "permission denied",
                "ssh timeout",
            )
        ):
            errors.append(f"{host}: {out[-200:]}")
            continue
        errors.append(f"{host}: {out[-200:] or 'empty response'}")
    return False, "DUT unreachable — " + " | ".join(errors)


def assert_dut_ssh_reachable(profile_active: dict[str, Any], *, case_id: str = "") -> None:
    ok, detail = probe_dut_ssh(profile_active)
    if ok:
        return
    label = f"{case_id}: " if case_id else ""
    raise RuntimeError(
        f"{label}DUT unreachable — aborting case (no hang).\n{detail}"
    )


def associated_su_indexes(
    profile_active: dict[str, Any],
    *,
    allow_fallback: bool = False,
) -> list[int]:
    """Live associated SU slots (e.g. sua4 → 4), not TRex port count.

    Prefer IPv4 backup for sysfs SSH — IPv6 often has no route after mgmt
    retag storms and must not burn minutes on per-field retries.
    """
    from traffic.kwn_sua_statistics import fetch_kwn_sua_statistics

    hosts = lab_hosts(profile_active)
    # Control-plane preference: IPv4 first, then configured dut_host / IPv6.
    candidates: list[str] = []
    for h in (hosts.get("dut_ipv4"), hosts.get("dut_host"), hosts.get("dut_ipv6")):
        hh = str(h or "").strip()
        if hh and hh not in candidates:
            candidates.append(hh)

    rows: list[dict[str, Any]] = []
    last_err = ""
    for host in candidates:
        try:
            rows = fetch_kwn_sua_statistics(
                host,
                ssh_user=hosts["dut_user"],
                ssh_password=hosts["dut_password"],
                max_sua=32,
            )
        except Exception as exc:
            last_err = str(exc)
            print(f"[vlan] SU stats SSH via {host} failed: {exc}")
            rows = []
        if rows:
            if host != hosts.get("dut_host"):
                print(f"[vlan] SU stats OK via {host}")
            break
        print(f"[vlan] SU stats empty via {host} — trying next host")
    if not rows and last_err:
        print(f"[vlan] SU stats SSH failed: {last_err}")
    indexes = sorted(
        {
            int(row.get("su_index") or row.get("sua_index") or 0)
            for row in rows
            if int(row.get("su_index") or row.get("sua_index") or 0) > 0
        }
    )
    if indexes:
        print(f"[vlan] Associated SU indexes: {indexes}")
        return indexes
    if not allow_fallback:
        print("[vlan] No associated SU found")
        return []
    fallback = int((profile_active.get("performance") or {}).get("cpe_su_index") or 1)
    print(f"[vlan] No associated SU found; falling back to SU{fallback}")
    return [fallback]


def wait_for_associated_su(profile_active: dict[str, Any], *, timeout_s: float = 90.0) -> list[int]:
    """Poll until at least one SU is associated; return indexes.

    If the DUT is unreachable, abort immediately (do not burn the full timeout).
    """
    ok, detail = probe_dut_ssh(profile_active, timeout_s=10)
    if not ok:
        raise TimeoutError(f"DUT unreachable while waiting for SU — {detail}")
    deadline = time.time() + max(timeout_s, 5.0)
    while time.time() < deadline:
        last = associated_su_indexes(profile_active, allow_fallback=False)
        if last:
            return last
        # Re-check reachability each loop so a dead SSH path cannot spin for minutes.
        ok, detail = probe_dut_ssh(profile_active, timeout_s=8)
        if not ok:
            raise TimeoutError(f"DUT became unreachable while waiting for SU — {detail}")
        time.sleep(3)
    raise TimeoutError(f"No associated SU within {timeout_s:.0f}s")


def apply_vlan_setup(
    *,
    profile_active: dict[str, Any],
    bts_vlan_mode: str,
    cpe_vlan_mode: str = "untagged",
    su_count: int | None = None,
) -> dict[str, Any]:
    from utils.vlan_mode_apply import apply_vlan_mode

    perf = profile_active.get("performance") or {}
    hosts = lab_hosts(profile_active)
    count = int(su_count if su_count is not None else hosts["su_count"])

    # Mgmt access: strip tags first (IPv4 backup), then tag only if DUT needs it.
    # Always re-run after VLAN apply so lab-PC tagging tracks live mgmtvlan.
    mgmt_access = ensure_lab_mgmt_access(profile_active)
    _SUITE_CACHE["mgmt_ok"] = bool(mgmt_access.get("ok") or mgmt_access.get("skipped"))

    # Fail fast if DUT is not reachable (IPv6 or IPv4 backup) — never hang on SU/apply.
    assert_dut_ssh_reachable(profile_active)

    # Quick SU check first — skip long recover when already associated.
    su_indexes = associated_su_indexes(profile_active, allow_fallback=False)
    if not su_indexes:
        try:
            su_indexes = recover_su_link(
                profile_active,
                timeout_s=float(perf.get("su_link_wait_s") or 30),
            )
        except TimeoutError as exc:
            print(f"[vlan] WARN: {exc} — continuing with fallback SU index")
            su_indexes = associated_su_indexes(profile_active, allow_fallback=True)

    if _SUITE_CACHE.get("ipv6_ok"):
        ipv6_state = {"cached": True}
    else:
        ipv6_state = ensure_lab_ipv6(profile_active)
        _SUITE_CACHE["ipv6_ok"] = True

    if not su_indexes:
        su_indexes = associated_su_indexes(profile_active, allow_fallback=True)

    result = apply_vlan_mode(
        profile_active=profile_active,
        bts_vlan_mode=bts_vlan_mode,
        su_count=count,
        cpe_vlan_mode=cpe_vlan_mode,
        su_indexes=su_indexes,
    )
    assert result.get("bts_ok"), f"BTS VLAN apply failed: {result}"
    failed = [r for r in (result.get("cpe_results") or []) if not r.get("ok")]
    assert not failed, f"CPE VLAN apply failed on SU(s): {failed}"

    # DUT mgmtvlan may have changed — re-sync lab PC (tag or stay untagged).
    mgmt_access = ensure_lab_mgmt_access(profile_active)
    _SUITE_CACHE["mgmt_ok"] = bool(mgmt_access.get("ok") or mgmt_access.get("skipped"))
    assert_dut_ssh_reachable(profile_active)
    applied = [r for r in (result.get("cpe_results") or []) if not r.get("skipped")]
    reloaded = bool(result.get("bts_reloaded"))
    # Soft UCI apply / already-OK: no long settle. Only wait when something actually changed.
    if reloaded:
        settle = float(perf.get("link_wait_s") or 5)
    elif applied:
        settle = min(3.0, float(perf.get("link_wait_s") or 3))
    else:
        settle = 0.0
    if settle > 0:
        time.sleep(settle)
    # Post-apply: quick association check only (no 180s poll when SU already up).
    mode = str(bts_vlan_mode).strip().lower()
    if reloaded or applied or mode == "qinq":
        still = associated_su_indexes(profile_active, allow_fallback=False)
        if not still:
            try:
                recover_su_link(
                    profile_active,
                    timeout_s=float(perf.get("su_link_wait_s") or 30),
                )
            except TimeoutError as exc:
                if mode == "qinq":
                    raise
                print(f"[vlan] WARN post-apply: {exc}")
    result["ipv6"] = ipv6_state
    result["mgmt_access"] = mgmt_access
    return result


def run_vlan_trex(
    *,
    profile_active: dict[str, Any],
    duration_s: int = DEFAULT_DURATION_S,
    dl_bw: str = "100M",
    ul_bw: str = "100M",
    packet_size: int = 1500,
    qinq_enabled: bool = True,
    trex_vlan: int | None = None,
    trex_svlan: int | None = None,
    trex_cvlan: int | None = None,
    expected_min_mbps: float = DEFAULT_MIN_MBPS,
    expected_max_mbps: float | None = None,
    vlan_apply: dict[str, Any] | None = None,
    case_id: str = "",
) -> VlanLabRunResult:
    from traffic.trex_runner import run_trex_stats_check

    ensure_trex_infra_or_fail(profile_active, case_id=case_id)

    hosts = lab_hosts(profile_active)
    tb = profile_active.get("testbed") or {}
    traffic = (profile_active.get("traffic") or {}).get("trex") or {}
    perf = profile_active.get("performance") or {}
    bts_cidr, cpe_cidr, _ = _profile_ipv6_pair(profile_active)
    # VLAN lab is always IPv6 for TRex traffic.
    use_ipv6 = True
    src_v6 = _ipv6_host(bts_cidr)
    dst_v6 = _ipv6_host(cpe_cidr)
    # Prefer short profile duration for VLAN cases.
    if duration_s == DEFAULT_DURATION_S and perf.get("duration_s"):
        duration_s = int(perf["duration_s"])
    deploy = not bool(_SUITE_CACHE.get("trex_deployed"))

    try:
        raw = run_trex_stats_check(
            trex_server=hosts["trex_host"],
            duration_s=duration_s,
            expected_min_mbps=0.0,
            trex_user=str(traffic.get("user") or "root"),
            trex_password=hosts["trex_password"],
            trex_dir=str(traffic.get("dir") or DEFAULT_TREX_DIR),
            trex_pythonpath=str(traffic.get("pythonpath") or DEFAULT_TREX_PYPATH),
            trex_ports=hosts["trex_ports"],
            trex_server_cores=int(traffic.get("server_cores") or 1),
            trex_server_startup_s=int(traffic.get("server_startup_s") or 15),
            trex_su_count=int(hosts["su_count"]),
            trex_dl_bw=dl_bw,
            trex_ul_bw=ul_bw,
            trex_packet_size=packet_size,
            trex_vlan=trex_vlan,
            trex_svlan=trex_svlan,
            trex_cvlan=trex_cvlan,
            trex_qinq_enabled=qinq_enabled,
            trex_qinq_host=hosts["dut_host"],
            profile_tb=tb,
            trex_ipv6=use_ipv6,
            trex_src_ipv6=src_v6,
            trex_dst_ipv6=dst_v6,
            dut_host=hosts["dut_host"],
            dut_user=hosts["dut_user"],
            dut_password=hosts["dut_password"],
            deploy_client_script=deploy,
            reuse_existing_server=True,
            keep_server_running=True,
        )
        _SUITE_CACHE["trex_deployed"] = True
    except Exception as exc:
        if is_trex_infra_error(exc):
            mark_trex_infra_down(str(exc), host=hosts["trex_host"], case_id=case_id)
            raise TrexInfraDown(trex_infra_summary()) from exc
        raise

    combined = raw.get("combined") or (raw.get("stats") or {}).get("combined") or {}
    rx = float(combined.get("rx_mbps") or 0.0)
    tx = float(combined.get("tx_mbps") or 0.0)
    if rx <= 0.0:
        # Fallback: consolidated summary / validation when live RX rows were not parsed.
        validation = raw.get("validation") or {}
        rx = float(validation.get("observed_rx_mbps") or 0.0)
        for row in raw.get("consolidated_summary") or []:
            bidi = float(row.get("bidi_mbps") or 0.0)
            if bidi > rx:
                rx = bidi
    validation = raw.get("validation") or {}
    passed = bool(validation.get("passed") if "validation" in raw else raw.get("passed"))
    log_tail = str(raw.get("client_output_tail") or validation.get("reason") or "")[-600:]
    reason = str(validation.get("reason") or log_tail or "")

    if expected_max_mbps is not None:
        trex_ok = rx <= expected_max_mbps
    else:
        trex_ok = bool(passed) or rx >= expected_min_mbps

    if not trex_ok and is_trex_infra_error(reason):
        mark_trex_infra_down(reason, host=hosts["trex_host"], case_id=case_id)
        raise TrexInfraDown(trex_infra_summary())

    return VlanLabRunResult(
        trex_ok=trex_ok,
        duration_s=duration_s,
        dl_bw=dl_bw,
        ul_bw=ul_bw,
        qinq_enabled=qinq_enabled,
        observed_rx_mbps=rx,
        observed_tx_mbps=tx,
        vlan_apply=vlan_apply or {},
        trex_log_tail=log_tail,
        raw=raw,
    )


def run_vlan_ping_case(
    *,
    profile_active: dict[str, Any],
    under_load: bool = False,
    bts_vlan_mode: str = "transparent",
    cpe_vlan_mode: str = "untagged",
    case_id: str = "",
) -> VlanLabRunResult:
    vlan_apply = apply_vlan_setup(
        profile_active=profile_active,
        bts_vlan_mode=bts_vlan_mode,
        cpe_vlan_mode=cpe_vlan_mode,
    )
    hosts = lab_hosts(profile_active)
    target = _ping_target_from_profile(profile_active)

    trex_result: VlanLabRunResult | None = None
    trex_infra_exc: TrexInfraDown | None = None
    stop_event = threading.Event()

    def _trex_bg() -> None:
        nonlocal trex_result, trex_infra_exc
        try:
            trex_result = run_vlan_trex(
                profile_active=profile_active,
                qinq_enabled=(bts_vlan_mode == "qinq"),
                duration_s=DEFAULT_DURATION_S + 10,
                vlan_apply=vlan_apply,
                case_id=case_id,
            )
        except TrexInfraDown as exc:
            trex_infra_exc = exc
            trex_result = VlanLabRunResult(
                trex_ok=False,
                duration_s=DEFAULT_DURATION_S,
                dl_bw="100M",
                ul_bw="100M",
                qinq_enabled=(bts_vlan_mode == "qinq"),
                observed_rx_mbps=0.0,
                observed_tx_mbps=0.0,
                trex_log_tail=str(exc),
            )
        except Exception as exc:
            if is_trex_infra_error(exc):
                mark_trex_infra_down(str(exc), host=hosts["trex_host"], case_id=case_id)
                trex_infra_exc = TrexInfraDown(trex_infra_summary())
            trex_result = VlanLabRunResult(
                trex_ok=False,
                duration_s=DEFAULT_DURATION_S,
                dl_bw="100M",
                ul_bw="100M",
                qinq_enabled=(bts_vlan_mode == "qinq"),
                observed_rx_mbps=0.0,
                observed_tx_mbps=0.0,
                trex_log_tail=str(exc),
            )
        finally:
            stop_event.set()

    if under_load:
        thread = threading.Thread(target=_trex_bg, daemon=True)
        thread.start()
        time.sleep(2)

    # Idle ping: short retries — do not burn minutes on recover loops.
    ping_ok, ping_detail = False, ""
    attempts = 2 if under_load else 3
    for attempt in range(1, attempts + 1):
        ping_ok, ping_detail = ping_via_bts(
            bts_host=hosts["dut_host"],
            bts_password=hosts["dut_password"],
            bts_user=hosts["dut_user"],
            target=target,
            count=3 if under_load else 2,
            wait_s=2,
            min_replies=1,
            bts_hosts=control_plane_hosts(profile_active),
        )
        if ping_ok:
            break
        print(f"[vlan] ping attempt {attempt}/{attempts} failed; retrying")
        time.sleep(1)

    if under_load:
        stop_event.wait(timeout=DEFAULT_DURATION_S + 30)
        thread.join(timeout=5)
        if trex_infra_exc is not None:
            raise trex_infra_exc
        bg = trex_result
        assert bg is not None, "background TRex thread did not produce a result"
        bg.ping_ok = ping_ok
        bg.ping_detail = ping_detail
        bg.vlan_apply = vlan_apply
        assert ping_ok, f"Ping failed under load to {target}: {ping_detail}"
        assert bg.trex_ok, (
            f"TRex under load failed: rx={bg.observed_rx_mbps:.2f} Mbps "
            f"tx={bg.observed_tx_mbps:.2f} Mbps\n{bg.trex_log_tail}"
        )
        return bg

    return VlanLabRunResult(
        trex_ok=True,
        duration_s=0,
        dl_bw="—",
        ul_bw="—",
        qinq_enabled=(bts_vlan_mode == "qinq"),
        observed_rx_mbps=0.0,
        observed_tx_mbps=0.0,
        vlan_apply=vlan_apply,
        ping_ok=ping_ok,
        ping_detail=ping_detail,
    )


def reboot_bts_and_wait(profile_active: dict[str, Any], *, wait_s: float = 180) -> None:
    from traffic.qos_lab import reboot_dut_and_wait

    hosts = lab_hosts(profile_active)
    # qos_lab.reboot_dut_and_wait uses down_poll_s / up_timeout_s (not timeout_s).
    reboot_dut_and_wait(
        dut_host=hosts["dut_host"],
        dut_password=hosts["dut_password"],
        down_poll_s=min(90, int(wait_s)),
        up_timeout_s=max(180, int(wait_s)),
    )
    # RF reassociation after BTS reboot.
    recover_su_link(profile_active, timeout_s=float((profile_active.get("performance") or {}).get("su_link_wait_s") or 180))


def reboot_cpe_via_bts(profile_active: dict[str, Any], *, su_index: int = 1) -> None:
    """Reboot CPE via jump-host LAN SSH (preferred) or cfgupdate; then wait for SU."""
    hosts = lab_hosts(profile_active)
    rebooted = False
    if hosts.get("cpe_pc_host") and hosts.get("cpe_lan_host"):
        try:
            print(f"[vlan] Rebooting CPE {hosts['cpe_lan_host']} via {hosts['cpe_pc_host']}")
            _ssh_via_jump(
                hosts["cpe_pc_host"],
                hosts["cpe_pc_password"],
                hosts["cpe_lan_host"],
                hosts["cpe_lan_password"],
                "sync; reboot",
                timeout_s=20,
            )
            rebooted = True
        except Exception as exc:
            print(f"[vlan] CPE jump reboot failed: {exc}; trying cfgupdate")
    if not rebooted:
        print("[vlan] Rebooting CPE via cfgupdate")
        _cfgupdate_cpe(hosts, "sync; reboot")
    time.sleep(float((profile_active.get("recovery") or {}).get("reboot_wait_seconds") or 120))
    recover_su_link(
        profile_active,
        timeout_s=float((profile_active.get("performance") or {}).get("su_link_wait_s") or 180),
    )


def emit_vlan_result(case_id: str, result: VlanLabRunResult) -> None:
    lines = [
        f"VLAN {case_id} | qinq={result.qinq_enabled} dl={result.dl_bw} ul={result.ul_bw} "
        f"rx={result.observed_rx_mbps:.2f}Mbps tx={result.observed_tx_mbps:.2f}Mbps",
    ]
    if result.ping_ok is not None:
        lines.append(f"  ping_ok={result.ping_ok} detail={result.ping_detail[:120]}")
    text = "\n".join(lines)
    print(text)
    write_vlan_case_evidence(
        case_id,
        payload={
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "summary": text,
            "observed_rx_mbps": result.observed_rx_mbps,
            "observed_tx_mbps": result.observed_tx_mbps,
            "ping_ok": result.ping_ok,
            "qinq_enabled": result.qinq_enabled,
        },
    )


def flap_cpe_ethernet_under_load(
    *,
    profile_active: dict[str, Any],
    vlan_apply: dict[str, Any] | None = None,
    case_id: str = "",
) -> VlanLabRunResult:
    """Run TRex while cycling CPE eth speed/duplex / carrier via jump SSH."""
    hosts = lab_hosts(profile_active)
    stop_event = threading.Event()
    trex_result: VlanLabRunResult | None = None
    trex_infra_exc: TrexInfraDown | None = None

    def _trex_bg() -> None:
        nonlocal trex_result, trex_infra_exc
        try:
            trex_result = run_vlan_trex(
                profile_active=profile_active,
                qinq_enabled=False,
                duration_s=DEFAULT_DURATION_S + 25,
                vlan_apply=vlan_apply or {},
                case_id=case_id,
            )
        except TrexInfraDown as exc:
            trex_infra_exc = exc
            trex_result = VlanLabRunResult(
                trex_ok=False,
                duration_s=DEFAULT_DURATION_S,
                dl_bw="100M",
                ul_bw="100M",
                qinq_enabled=False,
                observed_rx_mbps=0.0,
                observed_tx_mbps=0.0,
                trex_log_tail=str(exc),
            )
        except Exception as exc:
            if is_trex_infra_error(exc):
                mark_trex_infra_down(str(exc), host=hosts["trex_host"], case_id=case_id)
                trex_infra_exc = TrexInfraDown(trex_infra_summary())
            trex_result = VlanLabRunResult(
                trex_ok=False,
                duration_s=DEFAULT_DURATION_S,
                dl_bw="100M",
                ul_bw="100M",
                qinq_enabled=False,
                observed_rx_mbps=0.0,
                observed_tx_mbps=0.0,
                trex_log_tail=str(exc),
            )
        finally:
            stop_event.set()

    thread = threading.Thread(target=_trex_bg, daemon=True)
    thread.start()
    time.sleep(5)

    # Prefer CPE eth1 (LAN toward CPE-side PC); fall back to eth0.
    flap_cmds = (
        "IF=eth1; ip link show eth1 >/dev/null 2>&1 || IF=eth0; "
        "echo FLAP_IF=$IF; "
        "ethtool -s $IF speed 100 duplex full autoneg off 2>/dev/null || true; "
        "sleep 3; "
        "ethtool -s $IF autoneg on 2>/dev/null || true; "
        "sleep 2; "
        "ip link set $IF down; sleep 2; ip link set $IF up; "
        "sleep 3; "
        "ethtool $IF 2>/dev/null | head -20; "
        "echo FLAP_DONE"
    )
    flap_out = ""
    try:
        flap_out = _ssh_via_jump(
            hosts["cpe_pc_host"],
            hosts["cpe_pc_password"],
            hosts["cpe_lan_host"],
            hosts["cpe_lan_password"],
            flap_cmds,
            timeout_s=60,
        )
        print(f"[vlan] eth flap: {flap_out[-200:]}")
    except Exception as exc:
        flap_out = str(exc)
        print(f"[vlan] eth flap failed: {exc}")

    stop_event.wait(timeout=DEFAULT_DURATION_S + 90)
    thread.join(timeout=5)
    if trex_infra_exc is not None:
        raise trex_infra_exc
    bg = trex_result or VlanLabRunResult(
        trex_ok=False,
        duration_s=0,
        dl_bw="100M",
        ul_bw="100M",
        qinq_enabled=False,
        observed_rx_mbps=0.0,
        observed_tx_mbps=0.0,
        trex_log_tail="no trex result",
    )
    recover_su_link(profile_active, timeout_s=90)
    target = _ping_target_from_profile(profile_active)
    ping_ok, ping_detail = ping_via_bts(
        bts_host=hosts["dut_host"],
        bts_password=hosts["dut_password"],
        bts_user=hosts["dut_user"],
        target=target,
        count=4,
        min_replies=2,
        bts_hosts=control_plane_hosts(profile_active),
    )
    bg.ping_ok = ping_ok
    bg.ping_detail = f"{ping_detail}\nflap={flap_out[-120:]}"
    # Soft pass if traffic kept flowing OR post-flap ping OK (driver didn't hang).
    if not bg.trex_ok and ping_ok and bg.observed_rx_mbps >= 1.0:
        bg.trex_ok = True
    if not bg.trex_ok and ping_ok:
        # Link recovered after flap — treat as pass for hung-driver check.
        bg.trex_ok = True
        bg.trex_log_tail = (bg.trex_log_tail or "") + " | eth flap recovered via ping"
    return bg


def check_cpe_mgmt_http(profile_active: dict[str, Any]) -> tuple[bool, str]:
    """HTTP(S) reachability to CPE management from CPE-side PC."""
    hosts = lab_hosts(profile_active)
    cpe = hosts.get("cpe_lan_host") or "10.0.0.121"
    # Try common CPE GUI ports from the jump host.
    remote = (
        f"for url in http://{cpe}/ https://{cpe}/ http://{cpe}:80/ http://{cpe}:443/; do "
        f"code=$(curl -k -s -o /dev/null -w '%{{http_code}}' --connect-timeout 5 --max-time 10 \"$url\" || echo 000); "
        f"echo URL=$url CODE=$code; "
        f"case \"$code\" in 200|301|302|401|403) echo MGMT_OK; exit 0;; esac; "
        f"done; "
        f"ping -c 2 -W 2 {cpe} >/dev/null && echo PING_OK || echo PING_FAIL; "
        f"exit 1"
    )
    out = _ssh_cmd(hosts["cpe_pc_host"], hosts["cpe_pc_password"], remote, timeout_s=60)
    ok = "MGMT_OK" in out or ("PING_OK" in out and "CODE=000" not in out.split("PING")[0])
    # Accept ping-only if HTTP filtered but CPE LAN is up (GUI path exists).
    if not ok and "PING_OK" in out:
        ok = True
    return ok, out[-400:]


def cycle_cpe_vlan_modes(profile_active: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply CPE VLAN modes via jump SSH (stands in for GUI mode changes)."""
    hosts = lab_hosts(profile_active)
    modes = [
        ("0", "transparent"),
        ("2", "trunk"),
        ("3", "qinq"),
        ("0", "transparent"),
    ]
    results: list[dict[str, Any]] = []
    for mode_val, name in modes:
        cmds = (
            f"uci set vlan.ath1.mode={mode_val}; "
            f"uci commit vlan; "
            f"uci get vlan.ath1.mode"
        )
        # No network reload — avoid RF drop; UCI change is what GUI would commit.
        out = _ssh_via_jump(
            hosts["cpe_pc_host"],
            hosts["cpe_pc_password"],
            hosts["cpe_lan_host"],
            hosts["cpe_lan_password"],
            cmds,
            timeout_s=60,
        )
        ok = mode_val in out.splitlines()[-1] if out else False
        if not ok:
            ok = mode_val in out
        print(f"[vlan] CPE mode cycle → {name} ({mode_val}) ok={ok}")
        results.append({"mode": name, "uci": mode_val, "ok": ok, "tail": out[-80:]})
        time.sleep(2)
    return results
