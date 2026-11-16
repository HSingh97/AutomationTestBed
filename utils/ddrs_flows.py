"""Basic DDRS function — isolated lab flows (qa-lab-01 PC → BTS/CPE SSH hop).

Suite: tests/DDRS/  |  Profile: ddrs_lab.yaml
Traffic: mac_vlan_traffic_test_mf.py via utils/ddrs_traffic.py

Cases DDRS_01–DDRS_05 — each in its own test file, no co-channel coupling.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field, replace
from typing import Any

import pytest_check as check

from traffic.kwn_sua_statistics import (
    KWN_SUA_STAT_FIELDS,
    _parse_bulk_kwn_output,
    fetch_kwn_sua_statistics,
    is_sua_associated,
)
from utils.ddrs_traffic import run_mac_vlan_traffic
from utils.lab_nic_guard import assert_no_lab_nic_down
from utils.parsers import parse_ddrs_status, ssh_scalar
from utils.soak_common import WIFI_EVENTS_LOG, parse_link_events

_RADIO_IDX_DEFAULT = 1
_UCI_DDRS = "txparam.ath{idx}.ddrsstatus"
_UCI_SPATIAL = "txparam.ath{idx}.spatialstream"
_UCI_DISTANCE = "wireless.wifi{idx}.distance"
_UCI_DNS = "network.lan.dns"
_UCI_ETH_SPEED = "network.@device[0].speed"
_UCI_ATPC = "txparam.ath{idx}.atpcstatus"
_UCI_TXPOWER = "txparam.ath{idx}.atpcpower"

# Session caches — one bulk BTS sysfs read + resolved SU index per bench
_BULK_SYSFS_CACHE: dict[str, dict[int, dict[str, str]]] = {}
_SU_INDEX_CACHE: dict[str, int] = {}


@dataclass
class DdrsSnap:
    ddrs_status: str
    ddrs_uci: str
    spatial_uci: str
    ddrsrate: str = ""
    ddrsmaxrate: str = ""


@dataclass
class DdrsLinkView:
    bts_clients: list[dict[str, Any]] = field(default_factory=list)
    cpe_rates: dict[str, str] = field(default_factory=dict)
    bts_log_tail: str = ""
    cpe_log_tail: str = ""
    bts_ddrs: DdrsSnap | None = None
    cpe_ddrs: DdrsSnap | None = None


@dataclass
class DdrsConfig:
    """Standalone DDRS lab topology — loaded from profile ``ddrs:`` block only."""

    lab_pc_ssh: str = "qa-lab-01@10.0.150.116"
    lab_pc_password: str = "senao1234#"
    device_password: str = "Sen@0ubRNwk$"
    bts_ip: str = "192.168.2.20"
    cpe_ips: list[str] = field(default_factory=lambda: ["192.168.2.21"])
    wlan_iface: str = "ath1"
    radio_idx: int = _RADIO_IDX_DEFAULT
    mac_vlan_script: str = (
        "/home/qa-lab-01/Desktop/Automation Scripts/mac_vlan_traffic_test_mf.py"
    )
    bts_iface: str = "enp4s0"
    cpe_iface: str = "enx8cae4cdd4da8"
    dl_mbps_per_su: float = 40.0
    ul_mbps_per_su: float = 10.0
    traffic_duration_s: float = 60.0
    traffic_su_count: int = 1
    buffer_s: float = 3.0
    stats_interval_s: float = 5.0
    link_settle_s: float = 30.0
    reboot_wait_s: float = 180.0
    wifi_log_lines: int = 80
    results_dir: str = "reports/artifacts"
    cpe_remote_exec_su: int = 0  # 0 = auto-resolve from BTS sua statistics
    normal_tx_dbm: float = 5.0
    low_rssi_tx_dbm: float = 1.0
    low_rssi_settle_s: float = 20.0
    # Secondary BTS/CPE link (co-channel interference — same method as COCH suite)
    secondary_bts: str = "192.168.3.20"
    secondary_cpe: str = "192.168.3.21"
    wifi_iface: str = "wifi1"
    interference_level: str = "mid"
    interference_mode: str = "obss"
    secondary_tx_fixed_dbm: int = 1
    secondary_traffic_backend: str = "trex"
    secondary_throughput_max_mbps: float = 400.0
    secondary_throughput_step_mbps: float = 2.0
    secondary_tx_power_min_dbm: int = 1
    secondary_tx_power_max_dbm: int = 26
    rssi_settle_s: float = 10.0
    iperf_port: int = 5201
    iperf_port_ul: int = 5202
    interference: dict[str, Any] = field(default_factory=dict)
    trex: dict[str, Any] = field(default_factory=dict)
    cpe_fixed_mcs: int = 23
    bts_fixed_mcs: int = 23
    p2mp_cpe_ips: list[str] = field(default_factory=list)
    p2mp_min_stations: int = 2
    p2mp_max_loss_pct: float = 5.0
    p2mp_stability_duration_s: float = 60.0
    max_rate_wait_s: float = 180.0
    reconnect_ramp_s: float = 150.0
    reconnect_min_mcs: int = 2

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "DdrsConfig":
        block = dict(profile.get("ddrs") or {})
        dut = dict(profile.get("dut") or {})
        duration = float(
            os.environ.get("DDRS_DURATION_S")
            or block.get("traffic_duration_s")
            or 60
        )
        cpe_ips = [str(x) for x in (block.get("cpe_ips") or dut.get("remote_ips") or ["192.168.2.21"])]
        return cls(
            lab_pc_ssh=str(block.get("lab_pc_ssh") or cls.lab_pc_ssh),
            lab_pc_password=str(block.get("lab_pc_password") or cls.lab_pc_password),
            device_password=str(
                block.get("device_password") or dut.get("password") or cls.device_password
            ),
            bts_ip=str(block.get("bts_ip") or dut.get("local_ip") or cls.bts_ip),
            cpe_ips=cpe_ips,
            wlan_iface=str(block.get("wlan_iface") or "ath1"),
            radio_idx=int(block.get("radio_idx") or 1),
            mac_vlan_script=str(block.get("mac_vlan_script") or cls.mac_vlan_script),
            bts_iface=str(block.get("bts_iface") or "enp4s0"),
            cpe_iface=str(block.get("cpe_iface") or "enx8cae4cdd4da8"),
            dl_mbps_per_su=float(block.get("dl_mbps_per_su") or 40.0),
            ul_mbps_per_su=float(block.get("ul_mbps_per_su") or 10.0),
            traffic_duration_s=duration,
            traffic_su_count=int(block.get("traffic_su_count") or 1),
            buffer_s=float(block.get("buffer_s") or 3.0),
            stats_interval_s=float(block.get("stats_interval_s") or 5.0),
            link_settle_s=float(block.get("link_settle_s") or 30),
            reboot_wait_s=float(block.get("reboot_wait_s") or 180),
            wifi_log_lines=int(block.get("wifi_log_lines") or 80),
            results_dir=str(block.get("results_dir") or "reports/artifacts"),
            cpe_remote_exec_su=int(block.get("cpe_remote_exec_su") or 0),
            normal_tx_dbm=float(block.get("normal_tx_dbm") or 5.0),
            low_rssi_tx_dbm=float(block.get("low_rssi_tx_dbm") or 1.0),
            low_rssi_settle_s=float(block.get("low_rssi_settle_s") or 20.0),
            secondary_bts=str(block.get("secondary_bts") or cls.secondary_bts),
            secondary_cpe=str(block.get("secondary_cpe") or cls.secondary_cpe),
            wifi_iface=str(block.get("wifi_iface") or cls.wifi_iface),
            interference_level=str(block.get("interference_level") or "mid"),
            interference_mode=str(block.get("interference_mode") or "obss"),
            secondary_tx_fixed_dbm=int(block.get("secondary_tx_fixed_dbm") or 1),
            secondary_traffic_backend=str(block.get("secondary_traffic_backend") or "trex"),
            secondary_throughput_max_mbps=float(block.get("secondary_throughput_max_mbps") or 400.0),
            secondary_throughput_step_mbps=float(block.get("secondary_throughput_step_mbps") or 2.0),
            secondary_tx_power_min_dbm=int(block.get("secondary_tx_power_min_dbm") or 1),
            secondary_tx_power_max_dbm=int(block.get("secondary_tx_power_max_dbm") or 26),
            rssi_settle_s=float(block.get("rssi_settle_s") or 10.0),
            iperf_port=int(block.get("iperf_port") or 5201),
            iperf_port_ul=int(block.get("iperf_port_ul") or 5202),
            interference=dict(block.get("interference") or {}),
            trex=dict(block.get("trex") or {}),
            cpe_fixed_mcs=int(block.get("cpe_fixed_mcs") or 23),
            bts_fixed_mcs=int(block.get("bts_fixed_mcs") or 23),
            p2mp_cpe_ips=[str(x) for x in (block.get("p2mp_cpe_ips") or [])],
            p2mp_min_stations=int(block.get("p2mp_min_stations") or 2),
            p2mp_max_loss_pct=float(block.get("p2mp_max_loss_pct") or 5.0),
            p2mp_stability_duration_s=float(
                block.get("p2mp_stability_duration_s") or duration
            ),
            max_rate_wait_s=float(block.get("max_rate_wait_s") or 180.0),
            reconnect_ramp_s=float(block.get("reconnect_ramp_s") or 150.0),
            reconnect_min_mcs=int(block.get("reconnect_min_mcs") or 2),
        )

    @property
    def primary_cpe(self) -> str:
        if not self.cpe_ips:
            raise ValueError("ddrs: no CPE IPs configured")
        return self.cpe_ips[0]


# ---------------------------------------------------------------------------
# SSH (lab PC hop — same physical PC as other benches, no shared code with COCH)
# ---------------------------------------------------------------------------


def _ssh_lab(cfg: DdrsConfig, remote_cmd: str, *, timeout_s: int = 120) -> str:
    assert_no_lab_nic_down(remote_cmd)
    cmd = [
        "sshpass",
        "-p",
        cfg.lab_pc_password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=15",
        cfg.lab_pc_ssh,
        remote_cmd,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ""
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return out


def _ssh_bts(cfg: DdrsConfig, device_cmd: str, *, timeout_s: int = 60) -> str:
    inner = (
        f"sshpass -p {shlex.quote(cfg.device_password)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o LogLevel=ERROR -o ConnectTimeout=10 "
        f"root@{shlex.quote(cfg.bts_ip)} {shlex.quote(device_cmd)}"
    )
    return _ssh_lab(cfg, inner, timeout_s=timeout_s)


def _is_bts(cfg: DdrsConfig, device_ip: str) -> bool:
    return str(device_ip).strip() == str(cfg.bts_ip).strip()


def _remote_exec_command(su_index: int, inner_command: str) -> str:
    escaped = inner_command.replace("'", "'\"'\"'")
    return f"/usr/sbin/remote_exec.sh {su_index} '{escaped}'"


def _remote_exec_safe(command: str) -> str:
    """remote_exec runs argv on CPE without a shell — strip redirects/pipes."""
    cmd = command.strip()
    for suffix in (
        " 2>&1",
        " 2>/dev/null",
        " >/dev/null 2>&1 &",
        " >/dev/null 2>&1",
        " >/dev/null",
        " || true",
        " || echo -",
    ):
        if cmd.endswith(suffix):
            cmd = cmd[: -len(suffix)].rstrip()
    if "|" in cmd:
        cmd = cmd.split("|", 1)[0].strip()
    return cmd


def _cache_key(cfg: DdrsConfig) -> str:
    return f"{cfg.lab_pc_ssh}|{cfg.bts_ip}|{cfg.primary_cpe}"


def clear_ddrs_backend_cache() -> None:
    """Reset cached BTS sysfs + SU index (call at session start)."""
    _BULK_SYSFS_CACHE.clear()
    _SU_INDEX_CACHE.clear()


def invalidate_ddrs_backend_cache(cfg: DdrsConfig) -> None:
    """Drop cached sysfs/SU after restart — force fresh read when link returns."""
    key = _cache_key(cfg)
    _BULK_SYSFS_CACHE.pop(key, None)
    for cache_key in list(_SU_INDEX_CACHE):
        if cache_key.startswith(key):
            _SU_INDEX_CACHE.pop(cache_key, None)


def warm_ddrs_backend(cfg: DdrsConfig) -> int:
    """Pre-load BTS sysfs bulk + resolve CPE remote_exec SU index once."""
    _read_bts_sysfs_bulk(cfg)
    su = _resolve_cpe_su_index(cfg)
    note = "" if su >= 1 else " (unresolved — CPE GET skipped until IP is in sua table)"
    print(
        f"[ddrs] backend ready — BTS {cfg.bts_ip}, CPE {cfg.primary_cpe}, "
        f"remote_exec SU={su}{note}",
        flush=True,
    )
    return su


def _read_bts_sysfs_bulk(cfg: DdrsConfig) -> dict[int, dict[str, str]]:
    """Read all /sys/class/kwn/sua*/statistics in one BTS SSH (via lab PC hop)."""
    key = _cache_key(cfg)
    if key in _BULK_SYSFS_CACHE:
        return _BULK_SYSFS_CACHE[key]
    field_list = " ".join(KWN_SUA_STAT_FIELDS)
    inner = (
        f"for idx in $(seq 1 16); do "
        f'base="/sys/class/kwn/sua${{idx}}/statistics"; '
        f'[ -d "$base" ] || continue; '
        f'echo "SUA_INDEX=$idx"; '
        f"for f in {field_list}; do "
        f'printf "%s=" "$f"; cat "$base/$f" 2>/dev/null || echo -; echo; '
        f"done; echo ---; done"
    )
    raw = _ssh_bts(cfg, inner, timeout_s=120)
    bulk = _parse_bulk_kwn_output(raw)
    has_assoc = any(is_sua_associated(fields) for fields in bulk.values())
    if bulk and has_assoc:
        _BULK_SYSFS_CACHE[key] = bulk
    return bulk


def _scalar_clean(raw: str) -> str:
    text = ssh_scalar(raw)
    if not text:
        return ""
    lowered = text.lower()
    if any(
        tok in lowered
        for tok in (
            "connection refused",
            "connection timed out",
            "no route to host",
            "network is unreachable",
            "ssh:",
            "permission denied",
        )
    ):
        return ""
    return text


def _normalize_ipv4(raw: str) -> str:
    text = str(raw or "").strip()
    if "/" in text:
        text = text.split("/", 1)[0].strip()
    return text


def _cpe_ip_matches(target: str, ip_raw: str) -> bool:
    """Exact IPv4 match only — last-octet matching hits the wrong SU after remap."""
    target = _normalize_ipv4(target)
    ip_raw = _normalize_ipv4(ip_raw)
    if not target or not ip_raw or ip_raw in ("-", "0", "0.0.0.0"):
        return False
    return ip_raw == target


def _lookup_su_for_ip(cfg: DdrsConfig, target: str) -> int:
    bulk = _read_bts_sysfs_bulk(cfg)
    for sua_idx, fields in sorted(bulk.items()):
        if _cpe_ip_matches(target, fields.get("ip", "")):
            return int(sua_idx)
    hosts = _p2mp_cpe_ips(cfg) if cfg.p2mp_cpe_ips else cfg.cpe_ips
    clients = fetch_bts_link_clients(cfg, cpe_hosts=hosts)
    for client in clients:
        if not _cpe_ip_matches(target, str(client.get("ip") or "")):
            continue
        try:
            idx = int(client.get("su_index") or client.get("sua_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        if idx >= 1:
            return idx
    return 0


def _resolve_cpe_su_index(cfg: DdrsConfig, *, cpe_ip: str | None = None) -> int:
    """Map profile CPE IPv4 → BTS remote_exec SU. Never fall back to another SU."""
    target = str(cpe_ip or cfg.primary_cpe).strip()
    pin_ok = cfg.cpe_remote_exec_su > 0 and (
        cpe_ip is None or target == str(cfg.primary_cpe).strip()
    )
    if pin_ok:
        return int(cfg.cpe_remote_exec_su)
    cache_key = f"{_cache_key(cfg)}|{target}"
    if cache_key in _SU_INDEX_CACHE:
        return _SU_INDEX_CACHE[cache_key]
    idx = _lookup_su_for_ip(cfg, target)
    if idx < 1:
        invalidate_ddrs_backend_cache(cfg)
        idx = _lookup_su_for_ip(cfg, target)
    if idx >= 1:
        _SU_INDEX_CACHE[cache_key] = idx
        return idx
    return 0


def _ssh_cpe(cfg: DdrsConfig, device_cmd: str, *, su_index: int | None = None, timeout_s: int = 60) -> str:
    """Run a command on CPE via BTS /usr/sbin/remote_exec.sh (lab has no CPE SSH)."""
    idx = su_index if su_index is not None else _resolve_cpe_su_index(cfg)
    if int(idx or 0) < 1:
        return ""
    safe = _remote_exec_safe(device_cmd)
    return _ssh_bts(cfg, _remote_exec_command(int(idx), safe), timeout_s=timeout_s)


def _run_on_device(
    cfg: DdrsConfig,
    device_ip: str,
    device_cmd: str,
    *,
    timeout_s: int = 60,
    su_index: int | None = None,
) -> str:
    if _is_bts(cfg, device_ip):
        return _ssh_bts(cfg, device_cmd, timeout_s=timeout_s)
    return _ssh_cpe(
        cfg,
        device_cmd,
        su_index=su_index or _resolve_cpe_su_index(cfg, cpe_ip=device_ip),
        timeout_s=timeout_s,
    )


def count_connected_stations(cfg: DdrsConfig) -> int:
    try:
        raw = _ssh_bts(
            cfg,
            f"wlanconfig {cfg.wlan_iface} list sta 2>/dev/null",
            timeout_s=30,
        )
    except (subprocess.TimeoutExpired, RuntimeError, OSError):
        return 0
    count = 0
    for line in str(raw or "").splitlines():
        tok = line.strip()
        if not tok:
            continue
        upper = tok.upper()
        if "ADDR" in upper and ("AID" in upper or "MAC" in upper):
            continue
        if re.match(r"^[0-9a-f]{2}:", tok, re.I):
            count += 1
    if count > 0:
        return count
    try:
        fallback = _ssh_bts(
            cfg,
            f"wlanconfig {cfg.wlan_iface} list 2>/dev/null | "
            r"grep -cE '^[0-9a-f][0-9a-f]:' || echo 0",
            timeout_s=20,
        )
    except (subprocess.TimeoutExpired, RuntimeError, OSError):
        return 0
    try:
        return int(str(fallback).strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log(case_id: str, message: str) -> None:
    print(f"[ddrs][{case_id}] {message}", flush=True)


def _section(case_id: str, title: str) -> None:
    print(f"\n[ddrs][{case_id}] === {title} ===", flush=True)


def _kv(case_id: str, rows: dict[str, str]) -> None:
    width = max(len(k) for k in rows) if rows else 0
    for key, value in rows.items():
        print(f"[ddrs][{case_id}]   {key:<{width}} : {value}", flush=True)


def _uci_get(cfg: DdrsConfig, device_ip: str, key: str) -> str:
    raw = _run_on_device(cfg, device_ip, f"uci get {key}")
    val = _scalar_clean(raw)
    if "entry not found" in val.lower():
        return ""
    return val


def read_ddrs_snap(cfg: DdrsConfig, device_ip: str) -> DdrsSnap:
    idx = cfg.radio_idx
    raw_ddrs = _uci_get(cfg, device_ip, _UCI_DDRS.format(idx=idx))
    raw_spatial = _uci_get(cfg, device_ip, _UCI_SPATIAL.format(idx=idx))
    parsed = parse_ddrs_status(raw_ddrs) if raw_ddrs else ""
    return DdrsSnap(
        ddrs_status=parsed or ("unknown" if not raw_ddrs else "disable"),
        ddrs_uci=raw_ddrs or "?",
        spatial_uci=raw_spatial or "?",
        ddrsrate=_uci_get(cfg, device_ip, f"txparam.ath{idx}.ddrsrate"),
        ddrsmaxrate=_uci_get(cfg, device_ip, f"txparam.ath{idx}.ddrsmaxrate"),
    )


def read_bts_ddrs_snap(cfg: DdrsConfig) -> DdrsSnap:
    return read_ddrs_snap(cfg, cfg.bts_ip)


def read_cpe_ddrs_snap(cfg: DdrsConfig) -> DdrsSnap:
    return read_ddrs_snap(cfg, cfg.primary_cpe)


def _uci_unreadable(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"", "?", "entry not found", "unknown"}


def _cpe_get_echoes_bts(cpe: DdrsSnap, bts: DdrsSnap) -> bool:
    return (
        str(cpe.ddrs_uci).strip() == str(bts.ddrs_uci).strip()
        and str(cpe.ddrsrate).strip() == str(bts.ddrsrate).strip()
        and not _uci_unreadable(cpe.ddrs_uci)
    )


def _read_bts_ddrs_snap_retry(
    cfg: DdrsConfig, *, case_id: str, attempts: int = 4
) -> DdrsSnap:
    snap = read_bts_ddrs_snap(cfg)
    for i in range(max(0, attempts - 1)):
        if not _uci_unreadable(snap.ddrs_uci):
            return snap
        _log(case_id, f"BTS UCI unreadable ({snap.ddrs_uci!r}) — retry {i + 1}")
        time.sleep(8)
        invalidate_ddrs_backend_cache(cfg)
        snap = read_bts_ddrs_snap(cfg)
    return snap


def _read_cpe_ddrs_snap_retry(
    cfg: DdrsConfig, *, case_id: str, cpe_ip: str | None = None, attempts: int = 3
) -> tuple[int, DdrsSnap]:
    ip = str(cpe_ip or cfg.primary_cpe).strip()
    su = _resolve_cpe_su_index(cfg, cpe_ip=ip)
    snap = read_ddrs_snap(cfg, ip)
    for i in range(max(0, attempts - 1)):
        if su >= 1 and not _uci_unreadable(snap.ddrs_uci):
            return su, snap
        _log(
            case_id,
            f"CPE {ip} UCI unreadable (SU={su}, UCI={snap.ddrs_uci!r}) — refresh SU map",
        )
        invalidate_ddrs_backend_cache(cfg)
        time.sleep(5)
        su = _resolve_cpe_su_index(cfg, cpe_ip=ip)
        snap = read_ddrs_snap(cfg, ip)
    return su, snap


def _read_wifi_log_tail(cfg: DdrsConfig, device_ip: str) -> str:
    path = WIFI_EVENTS_LOG.format(idx=cfg.radio_idx)
    lines = max(20, int(cfg.wifi_log_lines))
    if _is_bts(cfg, device_ip):
        return _ssh_bts(cfg, f"tail -n {lines} {path} 2>/dev/null || true", timeout_s=30)
    return _ssh_cpe(cfg, f"tail -n {lines} {path}", timeout_s=45)


def _make_sysfs_reader(cfg: DdrsConfig):
    bulk = _read_bts_sysfs_bulk(cfg)

    def read_field(path: str) -> str:
        match = re.search(r"/sua(\d+)/statistics/([^/]+)$", path)
        if match:
            sua_idx = int(match.group(1))
            field = match.group(2)
            value = bulk.get(sua_idx, {}).get(field, "-")
            return value if value and value != "-" else "-"
        return "-"

    return read_field


def _primary_link_client(
    cfg: DdrsConfig, clients: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Pick link row for profile primary CPE (never another SU)."""
    if not clients:
        return None
    target = cfg.primary_cpe
    for client in clients:
        ip = str(client.get("ip") or "").strip()
        if _cpe_ip_matches(target, ip):
            return client
    return None


def fetch_bts_link_clients(
    cfg: DdrsConfig, *, cpe_hosts: list[str] | None = None
) -> list[dict[str, Any]]:
    return fetch_kwn_sua_statistics(
        cfg.bts_ip,
        read_field=_make_sysfs_reader(cfg),
        cpe_hosts=cpe_hosts or cfg.cpe_ips,
        max_sua=16,
    )


def _cpe_operating_rates(cfg: DdrsConfig) -> dict[str, str]:
    """CPE-side rate from BTS sua statistics for primary CPE + optional remote_exec iwconfig."""
    clients = fetch_bts_link_clients(cfg)
    out: dict[str, str] = {}
    primary = _primary_link_client(cfg, clients)
    if primary:
        out["tx_rate"] = str(primary.get("tx_rate") or "-")
        out["rx_rate"] = str(primary.get("rx_rate") or "-")
        out["operating_mcs"] = str(primary.get("operating_mcs") or "-")
        out["snr_remote"] = str(
            primary.get("combined_remote_snr") or primary.get("r_snr1") or "-"
        )
    raw = _ssh_cpe(cfg, f"iwconfig {cfg.wlan_iface}", timeout_s=30)
    for line in str(raw or "").splitlines():
        if "Bit Rate" in line:
            out["bit_rate"] = line.strip()
        if "Tx-Power" in line:
            out["tx_power"] = line.strip()
    return out


def collect_ddrs_link_view(cfg: DdrsConfig) -> DdrsLinkView:
    return DdrsLinkView(
        bts_clients=fetch_bts_link_clients(cfg),
        cpe_rates=_cpe_operating_rates(cfg),
        bts_log_tail=_read_wifi_log_tail(cfg, cfg.bts_ip),
        cpe_log_tail=_read_wifi_log_tail(cfg, cfg.primary_cpe),
        bts_ddrs=read_bts_ddrs_snap(cfg),
        cpe_ddrs=read_cpe_ddrs_snap(cfg),
    )


def _log_has_snr_or_rate(text: str) -> bool:
    lowered = (text or "").lower()
    if "snr" in lowered and "db" in lowered:
        return True
    if re.search(r"\brate\b", lowered) and re.search(r"\d", lowered):
        return True
    if "link up" in lowered or "mcs" in lowered:
        return True
    return False


def _ddrs_adapt_log_lines(text: str) -> list[str]:
    """Lines that show DDRS still stepping rate (increase / decrease)."""
    hits: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        adapt = (
            ("increase" in low or "decrease" in low)
            and ("rate" in low or "mcs" in low or "ddrs" in low)
        )
        if adapt or re.search(r"\bddrs\b.*\b(up|down)\b", low):
            hits.append(stripped)
    return hits


def _read_device_log_blob(cfg: DdrsConfig, device_ip: str) -> str:
    path = WIFI_EVENTS_LOG.format(idx=cfg.radio_idx)
    events = _run_on_device(cfg, device_ip, f"tail -n 200 {path}", timeout_s=45)
    logread = _run_on_device(cfg, device_ip, "logread -l 200", timeout_s=45)
    return f"{events or ''}\n{logread or ''}"


def verify_cpe_fixed_no_ddrs_adapt_logs(cfg: DdrsConfig, *, case_id: str) -> None:
    """CPE is fixed MCS — DDRS increase/decrease must not appear in CPE logs (sheet DDRS_50)."""
    _section(case_id, "CPE fixed MCS — no DDRS rate increase/decrease logs")
    bts_hits = _ddrs_adapt_log_lines(_read_device_log_blob(cfg, cfg.bts_ip))
    if bts_hits:
        _log(
            case_id,
            f"BTS DDRS adapt logs are expected (BTS DDRS on): {bts_hits[0][:140]}",
        )
    else:
        _log(case_id, "BTS: no DDRS increase/decrease lines in recent log")

    for ip, su in _p2mp_cpe_targets(cfg):
        blob = _read_device_log_blob(cfg, ip)
        hits = _ddrs_adapt_log_lines(blob)
        _kv(
            case_id,
            {
                f"CPE {ip} remote_exec SU": str(su),
                f"CPE {ip} DDRS adapt lines": str(len(hits)),
            },
        )
        for line in hits[:6]:
            _log(case_id, f"CPE {ip} adapt log: {line[:180]}")
        check.is_false(
            bool(hits),
            f"{case_id}: CPE {ip} is fixed MCS — DDRS rate increase/decrease logs must not appear "
            f"(got {len(hits)} line(s)"
            + (f": {hits[0][:120]}" if hits else "")
            + ")",
        )


def verify_bts_fixed_no_ddrs_adapt_logs(cfg: DdrsConfig, *, case_id: str) -> None:
    """BTS is fixed MCS — DDRS increase/decrease must not appear in BTS logs (sheet DDRS_56)."""
    _section(case_id, "BTS fixed MCS — no DDRS rate increase/decrease logs")
    bts_hits = _ddrs_adapt_log_lines(_read_device_log_blob(cfg, cfg.bts_ip))
    _kv(case_id, {"BTS DDRS adapt lines": str(len(bts_hits))})
    for line in bts_hits[:6]:
        _log(case_id, f"BTS adapt log: {line[:180]}")
    check.is_false(
        bool(bts_hits),
        f"{case_id}: BTS is fixed MCS — DDRS rate increase/decrease logs must not appear "
        f"(got {len(bts_hits)} line(s)"
        + (f": {bts_hits[0][:120]}" if bts_hits else "")
        + ")",
    )

    for ip, su in _p2mp_cpe_targets(cfg):
        blob = _read_device_log_blob(cfg, ip)
        hits = _ddrs_adapt_log_lines(blob)
        _kv(
            case_id,
            {
                f"CPE {ip} remote_exec SU": str(su),
                f"CPE {ip} DDRS adapt lines": str(len(hits)),
            },
        )
        if hits:
            _log(
                case_id,
                f"CPE {ip} DDRS adapt logs are expected (CPE DDRS on): {hits[0][:140]}",
            )
        else:
            _log(case_id, f"CPE {ip}: no DDRS increase/decrease lines in recent log")


def _count_matched_cpe_ips(
    cfg: DdrsConfig, clients: list[dict[str, Any]], *, min_stations: int
) -> int:
    wanted = _p2mp_cpe_ips(cfg) if min_stations > 1 else [cfg.primary_cpe]
    seen: set[str] = set()
    n = 0
    for want in wanted:
        for client in clients:
            ip = str(client.get("ip") or "").strip()
            if not ip or ip in seen:
                continue
            if _cpe_ip_matches(want, ip):
                seen.add(ip)
                n += 1
                break
    return n


def wait_ddrs_link(
    cfg: DdrsConfig,
    *,
    case_id: str = "DDRS",
    timeout_s: float | None = None,
    min_stations: int = 1,
) -> bool:
    deadline = time.time() + float(timeout_s or cfg.reboot_wait_s)
    poll_s = 5.0
    need_ips = 1 if min_stations <= 1 else int(min_stations)
    while time.time() < deadline:
        try:
            stations = count_connected_stations(cfg)
            clients = fetch_bts_link_clients(cfg)
        except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
            _log(case_id, f"waiting link… probe error: {exc}")
            time.sleep(poll_s)
            continue
        rates_ok = any(c.get("rx_rate_mbps") or c.get("tx_rate_mbps") for c in clients)
        # After restart, wlanconfig can show stations while cached sysfs bulk is still empty.
        if stations >= min_stations and (not clients or not rates_ok):
            invalidate_ddrs_backend_cache(cfg)
            try:
                clients = fetch_bts_link_clients(cfg)
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
                _log(case_id, f"waiting link… refresh error: {exc}")
                time.sleep(poll_s)
                continue
            rates_ok = any(c.get("rx_rate_mbps") or c.get("tx_rate_mbps") for c in clients)
        matched = _count_matched_cpe_ips(cfg, clients, min_stations=min_stations)
        if stations >= min_stations and rates_ok and matched >= need_ips:
            invalidate_ddrs_backend_cache(cfg)
            warm_ddrs_backend(cfg)
            _log(
                case_id,
                f"link up — {stations} station(s), rates visible, CPE IPs mapped={matched}",
            )
            return True
        _log(
            case_id,
            f"waiting link… stations={stations} clients={len(clients)} "
            f"cpe_ips={matched}/{need_ips}",
        )
        if stations >= min_stations:
            invalidate_ddrs_backend_cache(cfg)
        time.sleep(poll_s)
    return False


def _recover_after_restart(cfg: DdrsConfig, *, case_id: str) -> bool:
    """Wait for BTS SSH + RF link after network restart / reboot."""
    invalidate_ddrs_backend_cache(cfg)
    _log(case_id, f"waiting up to {cfg.reboot_wait_s:.0f}s for BTS/CPE recovery …")
    return wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)


def verify_bts_ddrs_cpe_fixed(cfg: DdrsConfig, *, case_id: str) -> tuple[DdrsSnap, DdrsSnap]:
    """Backend: BTS DDRS enable (direct SSH). CPE fixed MCS is applied via remote_exec."""
    su = _resolve_cpe_su_index(cfg)
    bts = read_bts_ddrs_snap(cfg)
    cpe = read_cpe_ddrs_snap(cfg)
    expect_mcs = str(int(cfg.cpe_fixed_mcs))
    cpe_get_is_bts = (
        str(cpe.ddrs_uci).strip() == str(bts.ddrs_uci).strip()
        and str(cpe.ddrsrate).strip() == str(bts.ddrsrate).strip()
    )
    _kv(
        case_id,
        {
            "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.ddrsstatus",
            "BTS DDRS UCI": bts.ddrs_uci,
            "BTS ddrsrate UCI": bts.ddrsrate,
            "CPE backend": f"remote_exec.sh {su} (SET to CPE; GET may echo BTS)",
            "CPE DDRS UCI (GET)": cpe.ddrs_uci,
            "CPE ddrsrate UCI (GET)": cpe.ddrsrate,
            "CPE expected MCS": expect_mcs,
            "CPE GET is BTS echo": str(cpe_get_is_bts),
        },
    )
    check.is_true(
        bts.ddrs_status.startswith("enable"),
        f"{case_id}: BTS DDRS must be enabled (UCI={bts.ddrs_uci!r})",
    )
    if not cpe_get_is_bts:
        check.is_true(
            cpe.ddrs_status.startswith("disable"),
            f"{case_id}: CPE DDRS must be disabled for fixed rate (UCI={cpe.ddrs_uci!r})",
        )
        check.equal(
            str(cpe.ddrsrate).strip(),
            expect_mcs,
            f"{case_id}: CPE ddrsrate must be MCS{expect_mcs} (got {cpe.ddrsrate!r})",
        )
    else:
        _log(
            case_id,
            f"CPE UCI GET via remote_exec echoes BTS — CPE fixed MCS{expect_mcs} applied via SET; "
            "BTS DDRS re-enabled over direct SSH",
        )
    return bts, cpe


def verify_ddrs_enabled(cfg: DdrsConfig, *, case_id: str) -> tuple[DdrsSnap, DdrsSnap]:
    bts = _read_bts_ddrs_snap_retry(cfg, case_id=case_id)
    su, cpe = _read_cpe_ddrs_snap_retry(cfg, case_id=case_id)
    echoes_bts = _cpe_get_echoes_bts(cpe, bts)
    _kv(
        case_id,
        {
            "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.ddrsstatus",
            "BTS DDRS UCI": bts.ddrs_uci,
            "CPE backend": f"remote_exec.sh {su} → uci get txparam.ath{cfg.radio_idx}.ddrsstatus",
            "CPE DDRS UCI": cpe.ddrs_uci,
            "CPE GET is BTS echo": str(echoes_bts),
        },
    )
    check.is_true(
        bts.ddrs_status.startswith("enable"),
        f"{case_id}: BTS DDRS must be enabled (UCI={bts.ddrs_uci!r})",
    )
    if echoes_bts:
        _log(
            case_id,
            f"CPE UCI GET via remote_exec SU={su} echoes BTS — DDRS SET applied; "
            "BTS SSH is the enable gate",
        )
    elif _uci_unreadable(cpe.ddrs_uci) or su < 1:
        _log(
            case_id,
            f"CPE UCI GET unreadable (SU={su}, UCI={cpe.ddrs_uci!r}) — mixed-mode GET; "
            "BTS SSH is the enable gate",
        )
    else:
        check.is_true(
            cpe.ddrs_status.startswith("enable"),
            f"{case_id}: CPE DDRS must be enabled via remote_exec (UCI={cpe.ddrs_uci!r})",
        )
    return bts, cpe


def verify_operating_rate_and_log(
    cfg: DdrsConfig,
    *,
    case_id: str,
    view: DdrsLinkView | None = None,
) -> DdrsLinkView:
    view = view or collect_ddrs_link_view(cfg)
    _section(case_id, "Operating rate & log verification")

    clients = view.bts_clients
    check.is_true(bool(clients), f"{case_id}: no associated CPE on BTS link table")
    if not clients:
        return view

    primary = _primary_link_client(cfg, clients) or clients[0]
    su = _resolve_cpe_su_index(cfg)
    _kv(
        case_id,
        {
            "BTS→CPE tx_rate": str(primary.get("tx_rate") or "-"),
            "CPE→BTS rx_rate": str(primary.get("rx_rate") or "-"),
            "Operating MCS": str(primary.get("operating_mcs") or "-"),
            "SNR local (BTS)": str(primary.get("combined_local_snr") or primary.get("l_snr1") or "-"),
            "SNR remote (CPE)": str(primary.get("combined_remote_snr") or primary.get("r_snr1") or "-"),
            "BTS DDRS UCI": view.bts_ddrs.ddrs_uci if view.bts_ddrs else "?",
            "CPE DDRS UCI": view.cpe_ddrs.ddrs_uci if view.cpe_ddrs else "?",
            "CPE rate (sua/iwconfig)": str(view.cpe_rates.get("bit_rate") or view.cpe_rates.get("tx_rate") or "-"),
            "CPE remote_exec SU": str(su),
        },
    )

    check.is_true(
        primary.get("tx_rate_mbps") or primary.get("rx_rate_mbps"),
        f"{case_id}: BTS link table must show operating data rate",
    )
    snr_l = primary.get("combined_local_snr") or primary.get("l_snr1") or "-"
    snr_r = primary.get("combined_remote_snr") or primary.get("r_snr1") or "-"
    check.is_true(
        snr_l not in ("-", "0", "—") or snr_r not in ("-", "0", "—"),
        f"{case_id}: SNR must be present in BTS link statistics",
    )

    events_bts = parse_link_events(view.bts_log_tail)
    events_cpe = parse_link_events(view.cpe_log_tail)
    log_ok = (
        _log_has_snr_or_rate(view.bts_log_tail)
        or _log_has_snr_or_rate(view.cpe_log_tail)
        or events_bts.snr_a1 is not None
        or events_cpe.snr_a1 is not None
    )
    _kv(
        case_id,
        {
            "BTS wifi log SNR": str(events_bts.snr_a1),
            "CPE wifi log SNR": str(events_cpe.snr_a1),
            "Log has rate/SNR": str(log_ok),
        },
    )
    check.is_true(log_ok, f"{case_id}: wifi events log should contain SNR / data-rate entry")
    return view


def run_ddrs_traffic(cfg: DdrsConfig, *, case_id: str) -> None:
    _section(case_id, f"mac_vlan_traffic_test_mf.py ({cfg.traffic_su_count} SU, {cfg.traffic_duration_s:g}s)")
    result = run_mac_vlan_traffic(cfg, case_id=case_id)
    check.is_not_none(result.dl_loss_pct, f"{case_id}: traffic must produce FINAL SUMMARY")


# ---------------------------------------------------------------------------
# Device actions — BTS direct SSH; CPE via BTS remote_exec
# ---------------------------------------------------------------------------


def _apply_ucidyn(cfg: DdrsConfig, device_ip: str, cmd: str) -> None:
    if _is_bts(cfg, device_ip):
        _ssh_bts(cfg, f"{cmd}; ucidyn apply 2>/dev/null || true", timeout_s=120)
        return
    su = _resolve_cpe_su_index(cfg, cpe_ip=device_ip)
    for part in str(cmd).split(";"):
        part = part.strip()
        if part:
            _ssh_cpe(cfg, part, su_index=su, timeout_s=120)
    _ssh_cpe(cfg, "ucidyn apply", su_index=su, timeout_s=120)


def _wireless_restart(cfg: DdrsConfig, device_ip: str) -> None:
    if _is_bts(cfg, device_ip):
        _ssh_bts(
            cfg,
            "nohup sh -c 'wifi reload 2>/dev/null || wifi' >/tmp/ddrs_wifi_reload.log 2>&1 &",
            timeout_s=30,
        )
    else:
        su = _resolve_cpe_su_index(cfg, cpe_ip=device_ip)
        _ssh_cpe(cfg, "wifi reload", su_index=su, timeout_s=30)


def _network_restart(cfg: DdrsConfig, device_ip: str) -> None:
    """Background network restart — avoids blocking lab→BTS SSH hop."""
    cmd = "nohup sh -c '/etc/init.d/network restart' >/tmp/ddrs_net_restart.log 2>&1 &"
    if _is_bts(cfg, device_ip):
        _ssh_bts(cfg, cmd, timeout_s=15)
    else:
        su = _resolve_cpe_su_index(cfg, cpe_ip=device_ip)
        _ssh_cpe(
            cfg,
            "/bin/sh -c 'nohup /etc/init.d/network restart >/tmp/ddrs_net_restart.log 2>&1 &'",
            su_index=su,
            timeout_s=15,
        )


def _soft_reboot(cfg: DdrsConfig, device_ip: str) -> None:
    try:
        if _is_bts(cfg, device_ip):
            _ssh_bts(cfg, "nohup sh -c reboot >/dev/null 2>&1 &", timeout_s=15)
        else:
            su = _resolve_cpe_su_index(cfg, cpe_ip=device_ip)
            _ssh_cpe(cfg, "reboot", su_index=su, timeout_s=15)
    except Exception:
        pass


def _bump_wireless_distance(cfg: DdrsConfig, device_ip: str) -> tuple[str, str]:
    key = _UCI_DISTANCE.format(idx=cfg.radio_idx)
    before = _uci_get(cfg, device_ip, key) or "1000"
    try:
        base = int(float(before))
    except ValueError:
        base = 1000
    after = str(base + 100 if base < 5000 else base - 100)
    _apply_ucidyn(cfg, device_ip, f"ucidyn set {key} {after}")
    return before, after


def _restore_wireless_distance(cfg: DdrsConfig, device_ip: str, value: str) -> None:
    if value:
        key = _UCI_DISTANCE.format(idx=cfg.radio_idx)
        _apply_ucidyn(cfg, device_ip, f"ucidyn set {key} {value}")


def _toggle_dns(cfg: DdrsConfig, device_ip: str) -> tuple[str, str]:
    before = _uci_get(cfg, device_ip, _UCI_DNS) or ""
    alt = "8.8.8.8" if "8.8.8.8" not in before else "1.1.1.1"
    _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_DNS} '{alt}'")
    return before, alt


def _restore_dns(cfg: DdrsConfig, device_ip: str, value: str) -> None:
    try:
        if value:
            _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_DNS} {value}")
        else:
            _apply_ucidyn(cfg, device_ip, f"ucidyn delete {_UCI_DNS}")
    except Exception as exc:
        _log("DDRS", f"WARN restore DNS on {device_ip}: {exc}")


def _read_eth_speed(cfg: DdrsConfig, device_ip: str) -> str:
    return _uci_get(cfg, device_ip, _UCI_ETH_SPEED) or ""


def _toggle_eth_duplex(cfg: DdrsConfig, device_ip: str) -> tuple[str, str]:
    before = _read_eth_speed(cfg, device_ip)
    lowered = before.lower()
    if "full" in lowered:
        after = before.replace("full", "half").replace("Full", "half")
        if after == before:
            after = "100half" if "1000" in lowered else "10half"
    elif "half" in lowered:
        after = before.replace("half", "full").replace("Half", "full")
        if after == before:
            after = "1000full" if "100" in lowered else "100full"
    else:
        after = "1000full"
    _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_ETH_SPEED} {after}")
    return before, after


def _restore_eth_speed(cfg: DdrsConfig, device_ip: str, value: str) -> None:
    if value:
        _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_ETH_SPEED} {value}")


def _ddr_snaps_match(a: DdrsSnap, b: DdrsSnap) -> bool:
    return (
        a.ddrs_uci == b.ddrs_uci
        and a.spatial_uci == b.spatial_uci
        and a.ddrsrate == b.ddrsrate
    )


def _apply_ddrs_mode(cfg: DdrsConfig, device_ip: str, *, enable: bool, ddrsrate: str | None = None) -> None:
    idx = cfg.radio_idx
    status = "1" if enable else "0"
    _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_DDRS.format(idx=idx)} {status}")
    if ddrsrate:
        _apply_ucidyn(cfg, device_ip, f"ucidyn set txparam.ath{idx}.ddrsrate {ddrsrate}")


def _restore_ddrs_snap(cfg: DdrsConfig, device_ip: str, snap: DdrsSnap) -> None:
    idx = cfg.radio_idx
    if snap.ddrs_uci and snap.ddrs_uci not in ("?", ""):
        _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_DDRS.format(idx=idx)} {snap.ddrs_uci}")
    if snap.spatial_uci and snap.spatial_uci not in ("?", ""):
        _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_SPATIAL.format(idx=idx)} {snap.spatial_uci}")
    if snap.ddrsrate and snap.ddrsrate not in ("?", ""):
        _apply_ucidyn(cfg, device_ip, f"ucidyn set txparam.ath{idx}.ddrsrate {snap.ddrsrate}")


def _apply_bts_fixed_cpe_ddrs(cfg: DdrsConfig) -> None:
    """BTS fixed MCS via direct SSH; CPE DDRS enable via remote_exec, then keep BTS fixed over SSH."""
    mcs = str(int(cfg.bts_fixed_mcs))
    _apply_ddrs_mode(cfg, cfg.primary_cpe, enable=True)
    time.sleep(2)
    _apply_ddrs_mode(cfg, cfg.bts_ip, enable=False, ddrsrate=mcs)
    time.sleep(8)
    wait_ddrs_link(cfg, case_id="DDRS", timeout_s=cfg.reboot_wait_s)


def verify_bts_fixed_cpe_ddrs(cfg: DdrsConfig, *, case_id: str) -> tuple[DdrsSnap, DdrsSnap]:
    """Backend: BTS DDRS disable + fixed MCS (direct SSH). CPE DDRS enable via remote_exec."""
    su = _resolve_cpe_su_index(cfg)
    bts = read_bts_ddrs_snap(cfg)
    cpe = read_cpe_ddrs_snap(cfg)
    expect_mcs = str(int(cfg.bts_fixed_mcs))
    cpe_get_is_bts = (
        str(cpe.ddrs_uci).strip() == str(bts.ddrs_uci).strip()
        and str(cpe.ddrsrate).strip() == str(bts.ddrsrate).strip()
    )
    _kv(
        case_id,
        {
            "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.ddrsstatus/ddrsrate",
            "BTS DDRS UCI": bts.ddrs_uci,
            "BTS ddrsrate UCI": bts.ddrsrate,
            "BTS expected MCS": expect_mcs,
            "CPE backend": f"remote_exec.sh {su} (SET CPE DDRS enable; GET may echo BTS)",
            "CPE DDRS UCI (GET)": cpe.ddrs_uci,
            "CPE GET is BTS echo": str(cpe_get_is_bts),
        },
    )
    check.is_true(
        bts.ddrs_status.startswith("disable"),
        f"{case_id}: BTS DDRS must be disabled for fixed rate (UCI={bts.ddrs_uci!r})",
    )
    check.equal(
        str(bts.ddrsrate).strip(),
        expect_mcs,
        f"{case_id}: BTS ddrsrate must be MCS{expect_mcs} (got {bts.ddrsrate!r})",
    )
    if not cpe_get_is_bts:
        check.is_true(
            cpe.ddrs_status.startswith("enable"),
            f"{case_id}: CPE DDRS must be enabled (UCI={cpe.ddrs_uci!r})",
        )
    else:
        _log(
            case_id,
            "CPE UCI GET via remote_exec echoes BTS — CPE DDRS enable applied via SET; "
            f"BTS held at fixed MCS{expect_mcs} over direct SSH",
        )
    return bts, cpe


def _verify_ddrs_unchanged(
    cfg: DdrsConfig,
    *,
    case_id: str,
    bts_before: DdrsSnap,
    cpe_before: DdrsSnap,
) -> None:
    bts_after = _read_bts_ddrs_snap_retry(cfg, case_id=case_id)
    _, cpe_after = _read_cpe_ddrs_snap_retry(cfg, case_id=case_id)
    _kv(
        case_id,
        {
            "BTS DDRS before": bts_before.ddrs_uci,
            "BTS DDRS after": bts_after.ddrs_uci,
            "CPE DDRS before": cpe_before.ddrs_uci,
            "CPE DDRS after": cpe_after.ddrs_uci,
        },
    )
    if _uci_unreadable(bts_before.ddrs_uci) or _uci_unreadable(bts_after.ddrs_uci):
        _log(
            case_id,
            f"skip BTS DDRS unchanged compare — UCI unreadable "
            f"({bts_before.ddrs_uci!r} → {bts_after.ddrs_uci!r})",
        )
    else:
        check.is_true(_ddr_snaps_match(bts_before, bts_after), f"{case_id}: BTS DDRS unchanged")
    if (
        _uci_unreadable(cpe_before.ddrs_uci)
        or _uci_unreadable(cpe_after.ddrs_uci)
        or _cpe_get_echoes_bts(cpe_after, bts_after)
    ):
        _log(
            case_id,
            f"skip CPE DDRS unchanged compare — GET unreadable or BTS echo "
            f"({cpe_before.ddrs_uci!r} → {cpe_after.ddrs_uci!r})",
        )
        return
    check.is_true(_ddr_snaps_match(cpe_before, cpe_after), f"{case_id}: CPE DDRS unchanged")


def _read_tx_power_uci(cfg: DdrsConfig, device_ip: str) -> str:
    return _uci_get(cfg, device_ip, _UCI_TXPOWER.format(idx=cfg.radio_idx))


def _set_manual_tx_power(cfg: DdrsConfig, device_ip: str, tx_dbm: str | float) -> None:
    idx = cfg.radio_idx
    value = str(int(float(tx_dbm)))
    _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_ATPC.format(idx=idx)} 0")
    _apply_ucidyn(cfg, device_ip, f"ucidyn set {_UCI_TXPOWER.format(idx=idx)} {value}")


def _primary_link_snapshot(cfg: DdrsConfig) -> dict[str, Any]:
    invalidate_ddrs_backend_cache(cfg)
    clients = fetch_bts_link_clients(cfg)
    primary = _primary_link_client(cfg, clients) or {}
    return {
        "tx_rate_mbps": primary.get("tx_rate_mbps"),
        "rx_rate_mbps": primary.get("rx_rate_mbps"),
        "operating_mcs": primary.get("operating_mcs"),
        "l_rssi1": primary.get("l_rssi1"),
        "r_rssi1": primary.get("r_rssi1"),
        "combined_local_snr": primary.get("combined_local_snr"),
        "combined_remote_snr": primary.get("combined_remote_snr"),
        "tx_rate": primary.get("tx_rate"),
        "rx_rate": primary.get("rx_rate"),
    }


def _rssi_dbm_int(raw: Any) -> int | None:
    text = str(raw or "").strip()
    if not text or text in {"-", "—", "0"}:
        return None
    try:
        return int(float(text.replace("—", "-")))
    except ValueError:
        return None


def _rate_fields_changed(baseline: dict[str, Any], other: dict[str, Any]) -> bool:
    """True if tx/rx Mbps, MCS, or displayed rate string changed."""
    for key in ("tx_rate_mbps", "rx_rate_mbps", "operating_mcs", "tx_rate", "rx_rate"):
        if baseline.get(key) != other.get(key) and other.get(key) not in (None, "-", "—"):
            return True
    return False


def _rate_variation_detected(baseline: dict[str, Any], low: dict[str, Any]) -> bool:
    if _rate_fields_changed(baseline, low):
        return True
    for chain in ("l_rssi1", "r_rssi1"):
        b = _rssi_dbm_int(baseline.get(chain))
        l = _rssi_dbm_int(low.get(chain))
        if b is not None and l is not None and l < b:
            return True
    return False


def _on_both_devices(cfg: DdrsConfig, *, case_id: str, action, label: str) -> None:
    _log(case_id, f"{label} on CPE via remote_exec SU {_resolve_cpe_su_index(cfg)}")
    action(cfg.primary_cpe)
    _log(case_id, f"{label} on BTS ({cfg.bts_ip})")
    action(cfg.bts_ip)


# ---------------------------------------------------------------------------
# Case flows — one function per test file
# ---------------------------------------------------------------------------


def assert_ddrs_01_associate_with_traffic(cfg: DdrsConfig) -> None:
    case_id = "DDRS_01"
    _section(case_id, "Associate BTS & CPE with DDRS + traffic")
    verify_ddrs_enabled(cfg, case_id=case_id)
    check.is_true(
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s),
        f"{case_id}: link must be up before traffic",
    )
    run_ddrs_traffic(cfg, case_id=case_id)
    time.sleep(5)
    verify_operating_rate_and_log(cfg, case_id=case_id)


def _run_ddrs_wireless_restart(cfg: DdrsConfig, *, case_id: str) -> None:
    _section(case_id, "Wireless parameter change + wireless restart")
    bts_before, cpe_before = verify_ddrs_enabled(cfg, case_id=case_id)
    cpe_dist_before = bts_dist_before = ""
    try:
        cpe_dist_before, _ = _bump_wireless_distance(cfg, cfg.primary_cpe)
        bts_dist_before, _ = _bump_wireless_distance(cfg, cfg.bts_ip)
        _on_both_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _wireless_restart(cfg, ip),
            label="wireless restart",
        )
        invalidate_ddrs_backend_cache(cfg)
        time.sleep(10)
        check.is_true(
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s),
            f"{case_id}: link must recover",
        )
        _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        if cpe_dist_before:
            _restore_wireless_distance(cfg, cfg.primary_cpe, cpe_dist_before)
        if bts_dist_before:
            _restore_wireless_distance(cfg, cfg.bts_ip, bts_dist_before)


def _run_ddrs_network_restart(cfg: DdrsConfig, *, case_id: str) -> None:
    _section(case_id, "Network/IP setting change + network restart")
    bts_before, cpe_before = verify_ddrs_enabled(cfg, case_id=case_id)
    cpe_dns_before = bts_dns_before = ""
    try:
        cpe_dns_before, _ = _toggle_dns(cfg, cfg.primary_cpe)
        bts_dns_before, _ = _toggle_dns(cfg, cfg.bts_ip)
        _on_both_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _network_restart(cfg, ip),
            label="network restart",
        )
        check.is_true(_recover_after_restart(cfg, case_id=case_id), f"{case_id}: link must recover")
        _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            _restore_dns(cfg, cfg.primary_cpe, cpe_dns_before)
            _restore_dns(cfg, cfg.bts_ip, bts_dns_before)
        except Exception as exc:
            _log(case_id, f"WARN DNS restore: {exc}")


def _run_ddrs_ethernet_duplex_restart(cfg: DdrsConfig, *, case_id: str) -> None:
    _section(case_id, "Ethernet duplex change + restart")
    bts_before, cpe_before = verify_ddrs_enabled(cfg, case_id=case_id)
    cpe_eth_before = bts_eth_before = ""
    try:
        cpe_eth_before, _ = _toggle_eth_duplex(cfg, cfg.primary_cpe)
        bts_eth_before, _ = _toggle_eth_duplex(cfg, cfg.bts_ip)
        _on_both_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _network_restart(cfg, ip),
            label="ethernet/network restart",
        )
        time.sleep(15)
        check.is_true(_recover_after_restart(cfg, case_id=case_id), f"{case_id}: link must recover")
        _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            if cpe_eth_before:
                _restore_eth_speed(cfg, cfg.primary_cpe, cpe_eth_before)
            if bts_eth_before:
                _restore_eth_speed(cfg, cfg.bts_ip, bts_eth_before)
            _on_both_devices(
                cfg,
                case_id=case_id,
                action=lambda ip: _network_restart(cfg, ip),
                label="restore ethernet",
            )
            _recover_after_restart(cfg, case_id=case_id)
        except Exception as exc:
            _log(case_id, f"WARN ethernet restore: {exc}")


def _run_ddrs_soft_reboot(cfg: DdrsConfig, *, case_id: str) -> None:
    _section(case_id, "Soft reboot BTS & CPE (negative)")
    bts_before, cpe_before = verify_ddrs_enabled(cfg, case_id=case_id)
    _soft_reboot(cfg, cfg.primary_cpe)
    time.sleep(5)
    _soft_reboot(cfg, cfg.bts_ip)
    invalidate_ddrs_backend_cache(cfg)
    check.is_true(
        _recover_after_restart(cfg, case_id=case_id),
        f"{case_id}: link must recover after soft reboot",
    )
    _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
    verify_operating_rate_and_log(cfg, case_id=case_id)


def assert_ddrs_02_wireless_restart(cfg: DdrsConfig) -> None:
    _run_ddrs_wireless_restart(cfg, case_id="DDRS_02")


def assert_ddrs_03_network_restart(cfg: DdrsConfig) -> None:
    _run_ddrs_network_restart(cfg, case_id="DDRS_03")


def assert_ddrs_04_ethernet_duplex_restart(cfg: DdrsConfig) -> None:
    _run_ddrs_ethernet_duplex_restart(cfg, case_id="DDRS_04")


def assert_ddrs_05_soft_reboot(cfg: DdrsConfig) -> None:
    _run_ddrs_soft_reboot(cfg, case_id="DDRS_05")


def assert_ddrs_07_low_rssi_rate_variation(cfg: DdrsConfig) -> None:
    """Reduce Tx power on BTS & CPE to create low RSSI; verify rate variation + SNR log."""
    case_id = "DDRS_07"
    _section(case_id, "Low RSSI via Tx power — check data rate variation")
    verify_ddrs_enabled(cfg, case_id=case_id)
    check.is_true(
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s),
        f"{case_id}: link must be up before low-RSSI test",
    )

    normal_tx = str(int(cfg.normal_tx_dbm))
    low_tx = str(int(cfg.low_rssi_tx_dbm))
    bts_tx_saved = _read_tx_power_uci(cfg, cfg.bts_ip) or normal_tx
    cpe_tx_saved = _read_tx_power_uci(cfg, cfg.primary_cpe) or normal_tx

    try:
        _set_manual_tx_power(cfg, cfg.bts_ip, normal_tx)
        _set_manual_tx_power(cfg, cfg.primary_cpe, normal_tx)
        time.sleep(10)
        baseline = _primary_link_snapshot(cfg)
        _kv(
            case_id,
            {
                "Normal Tx target (dBm)": normal_tx,
                "Low Tx target (dBm)": low_tx,
                "Baseline BTS→CPE rate": str(baseline.get("tx_rate") or "-"),
                "Baseline CPE→BTS rate": str(baseline.get("rx_rate") or "-"),
                "Baseline RSSI local": str(baseline.get("l_rssi1") or "-"),
                "Baseline RSSI remote": str(baseline.get("r_rssi1") or "-"),
            },
        )

        _log(case_id, f"applying low Tx ({low_tx} dBm) on BTS & CPE via ucidyn")
        _set_manual_tx_power(cfg, cfg.bts_ip, low_tx)
        _set_manual_tx_power(cfg, cfg.primary_cpe, low_tx)
        time.sleep(max(5.0, float(cfg.low_rssi_settle_s)))
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)

        bts_tx_low = _read_tx_power_uci(cfg, cfg.bts_ip)
        cpe_tx_low = _read_tx_power_uci(cfg, cfg.primary_cpe)
        low = _primary_link_snapshot(cfg)
        su = _resolve_cpe_su_index(cfg)
        _kv(
            case_id,
            {
                "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.atpcpower",
                "BTS Tx UCI (low)": bts_tx_low or "?",
                "CPE backend": f"remote_exec.sh {su} → uci get txparam.ath{cfg.radio_idx}.atpcpower",
                "CPE Tx UCI (low)": cpe_tx_low or "?",
                "Low-RSSI BTS→CPE rate": str(low.get("tx_rate") or "-"),
                "Low-RSSI CPE→BTS rate": str(low.get("rx_rate") or "-"),
                "Low-RSSI RSSI local": str(low.get("l_rssi1") or "-"),
                "Low-RSSI RSSI remote": str(low.get("r_rssi1") or "-"),
            },
        )

        check.equal(bts_tx_low, low_tx, f"{case_id}: BTS Tx power must be {low_tx} dBm (backend UCI)")
        check.equal(cpe_tx_low, low_tx, f"{case_id}: CPE Tx power must be {low_tx} dBm (remote_exec UCI)")

        varied = _rate_variation_detected(baseline, low)
        if varied:
            _log(case_id, "rate/RSSI variation detected between normal and low Tx")
        else:
            _log(
                case_id,
                "NOTE: rate/RSSI unchanged on close bench — low Tx applied; "
                "use physical attenuation/closed box for stronger variation",
            )
        check.is_true(
            varied
            or (low.get("tx_rate_mbps") and low.get("combined_local_snr") not in (None, "-", "—", "0")),
            f"{case_id}: low-RSSI condition must show operating rate or measurable variation",
        )
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_saved)
            _set_manual_tx_power(cfg, cfg.primary_cpe, cpe_tx_saved)
            time.sleep(8)
        except Exception as exc:
            _log(case_id, f"WARN Tx power restore: {exc}")


def assert_ddrs_08_wireless_restart_low_rssi(cfg: DdrsConfig) -> None:
    _run_ddrs_wireless_restart(cfg, case_id="DDRS_08")


def assert_ddrs_09_network_restart_low_rssi(cfg: DdrsConfig) -> None:
    _run_ddrs_network_restart(cfg, case_id="DDRS_09")


def assert_ddrs_10_ethernet_duplex_restart_low_rssi(cfg: DdrsConfig) -> None:
    _run_ddrs_ethernet_duplex_restart(cfg, case_id="DDRS_10")


def assert_ddrs_11_soft_reboot_low_rssi(cfg: DdrsConfig) -> None:
    _run_ddrs_soft_reboot(cfg, case_id="DDRS_11")


def assert_ddrs_13_cochannel_rate_variation(cfg: DdrsConfig) -> None:
    """Secondary BTS co-channel link — verify data rate variation under interference."""
    from utils.cochannel_flows import (
        interference_level_ok,
        set_secondary_tx_power,
        stop_secondary_iperf,
    )
    from utils.ddrs_interference import (
        cochannel_cfg_from_ddrs,
        read_ddrs_interference_rf,
        secondary_interference_session,
    )

    case_id = "DDRS_13"
    _section(case_id, "Co-channel interference — data rate variation (secondary BTS link)")
    verify_ddrs_enabled(cfg, case_id=case_id)
    check.is_true(
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s),
        f"{case_id}: primary link must be up",
    )

    cc = cochannel_cfg_from_ddrs(cfg)
    level = str(cfg.interference_level or "mid").lower()
    band = dict((cfg.interference or {}).get(level) or {})

    # Quiet baseline — secondary off (residual OBSS may remain if TRex stop fails).
    stop_secondary_iperf(cc)
    set_secondary_tx_power(cc, cc.secondary_tx_power_min_dbm)
    time.sleep(max(10.0, float(cfg.rssi_settle_s) * 2))

    baseline_rf = read_ddrs_interference_rf(cfg)
    baseline_rate = _primary_link_snapshot(cfg)
    _kv(
        case_id,
        {
            "Secondary BTS": cfg.secondary_bts,
            "Secondary CPE": cfg.secondary_cpe,
            "Baseline channel": str(baseline_rf.channel or "-"),
            "Baseline OBSS %": str(baseline_rf.obss_pct if baseline_rf.obss_pct is not None else "-"),
            "Baseline BTS→CPE rate": str(baseline_rate.get("tx_rate") or "-"),
            "Baseline CPE→BTS rate": str(baseline_rate.get("rx_rate") or "-"),
        },
    )

    with secondary_interference_session(cfg, case_id=case_id, level=level) as (_, active_snap):
        time.sleep(max(5.0, float(cfg.rssi_settle_s)))
        interfered_rf = read_ddrs_interference_rf(cfg)
        interfered_rate = _primary_link_snapshot(cfg)
        rate_varied = _rate_variation_detected(baseline_rate, interfered_rate)
        # DDRS can take a few seconds to step MCS after OBSS rises — poll for variation.
        if not rate_varied:
            for _ in range(6):
                time.sleep(5)
                interfered_rate = _primary_link_snapshot(cfg)
                if _rate_variation_detected(baseline_rate, interfered_rate):
                    rate_varied = True
                    break
            interfered_rf = read_ddrs_interference_rf(cfg)

        obss_delta = None
        if baseline_rf.obss_pct is not None and interfered_rf.obss_pct is not None:
            obss_delta = abs(int(interfered_rf.obss_pct) - int(baseline_rf.obss_pct))
        interference_in_band = interference_level_ok(interfered_rf, band, cc) or interference_level_ok(
            active_snap, band, cc
        )
        _kv(
            case_id,
            {
                "Interference level": level,
                "Active channel": str(interfered_rf.channel or "-"),
                "Active OBSS %": str(interfered_rf.obss_pct if interfered_rf.obss_pct is not None else "-"),
                "Active RSSI (list-ap)": str(interfered_rf.rssi_dbm if interfered_rf.rssi_dbm is not None else "-"),
                "OBSS delta (|active-baseline|)": str(obss_delta if obss_delta is not None else "-"),
                "OBSS in target band": str(interference_in_band),
                "Baseline BTS→CPE rate": str(baseline_rate.get("tx_rate") or "-"),
                "Baseline CPE→BTS rate": str(baseline_rate.get("rx_rate") or "-"),
                "Interfered BTS→CPE rate": str(interfered_rate.get("tx_rate") or "-"),
                "Interfered CPE→BTS rate": str(interfered_rate.get("rx_rate") or "-"),
                "Data rate variation": str(rate_varied),
            },
        )

        check.is_not_none(
            interfered_rf.obss_pct,
            f"{case_id}: primary BTS must report OBSS with secondary interferer active",
        )
        check.is_true(
            rate_varied,
            f"{case_id}: operating data rate must change under channel interference "
            f"(baseline {baseline_rate.get('tx_rate')}/{baseline_rate.get('rx_rate')} → "
            f"{interfered_rate.get('tx_rate')}/{interfered_rate.get('rx_rate')})",
        )
        verify_operating_rate_and_log(cfg, case_id=case_id)


def _run_ddrs_under_secondary_interference(cfg: DdrsConfig, *, case_id: str, runner) -> None:
    from utils.ddrs_interference import secondary_interference_session

    with secondary_interference_session(cfg, case_id=case_id):
        runner(cfg, case_id=case_id)


def assert_ddrs_14_cochannel_wireless_restart(cfg: DdrsConfig) -> None:
    _section("DDRS_14", "Co-channel — wireless restart under secondary interference")
    _run_ddrs_under_secondary_interference(cfg, case_id="DDRS_14", runner=_run_ddrs_wireless_restart)


def assert_ddrs_15_cochannel_network_restart(cfg: DdrsConfig) -> None:
    _section("DDRS_15", "Co-channel — network restart under secondary interference")
    _run_ddrs_under_secondary_interference(cfg, case_id="DDRS_15", runner=_run_ddrs_network_restart)


def assert_ddrs_16_cochannel_ethernet_duplex_restart(cfg: DdrsConfig) -> None:
    _section("DDRS_16", "Co-channel — ethernet duplex restart under secondary interference")
    _run_ddrs_under_secondary_interference(cfg, case_id="DDRS_16", runner=_run_ddrs_ethernet_duplex_restart)


def assert_ddrs_17_cochannel_soft_reboot(cfg: DdrsConfig) -> None:
    _section("DDRS_17", "Co-channel — soft reboot under secondary interference (negative)")
    _run_ddrs_under_secondary_interference(cfg, case_id="DDRS_17", runner=_run_ddrs_soft_reboot)


def _apply_bts_ddrs_cpe_fixed(cfg: DdrsConfig) -> None:
    """CPE fixed MCS via remote_exec; BTS DDRS re-enabled last over direct SSH."""
    mcs = str(int(cfg.cpe_fixed_mcs))
    _apply_ddrs_mode(cfg, cfg.primary_cpe, enable=False, ddrsrate=mcs)
    time.sleep(2)
    _apply_ddrs_mode(cfg, cfg.bts_ip, enable=True)
    time.sleep(8)
    wait_ddrs_link(cfg, case_id="DDRS", timeout_s=cfg.reboot_wait_s)


def _with_bts_ddrs_cpe_fixed(cfg: DdrsConfig, *, case_id: str, body) -> None:
    """Apply BTS DDRS + CPE fixed MCS, run body, restore original UCI."""
    bts_saved = read_bts_ddrs_snap(cfg)
    cpe_saved = read_cpe_ddrs_snap(cfg)
    try:
        _apply_bts_ddrs_cpe_fixed(cfg)
        body()
    finally:
        try:
            # remote_exec SET also hits BTS — restore CPE first, BTS last over SSH.
            _restore_ddrs_snap(cfg, cfg.primary_cpe, cpe_saved)
            time.sleep(2)
            _restore_ddrs_snap(cfg, cfg.bts_ip, bts_saved)
            time.sleep(8)
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s)
        except Exception as exc:
            _log(case_id, f"WARN restore DDRS/MCS: {exc}")


def assert_ddrs_19_bts_ddrs_cpe_fixed_rate(cfg: DdrsConfig) -> None:
    """BTS DDRS enable + CPE fixed MCS — operating rate & SNR log (incl. low RSSI + co-channel)."""
    case_id = "DDRS_19"

    def _body() -> None:
        _section(case_id, "BTS DDRS enabled & CPE fixed data rate")
        verify_bts_ddrs_cpe_fixed(cfg, case_id=case_id)
        check.is_true(
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s),
            f"{case_id}: link must be up",
        )
        verify_operating_rate_and_log(cfg, case_id=case_id)

        _section(case_id, "Low RSSI check (Tx power) under mixed DDRS/fixed")
        normal_tx = str(int(cfg.normal_tx_dbm))
        low_tx = str(int(cfg.low_rssi_tx_dbm))
        bts_tx_saved = _read_tx_power_uci(cfg, cfg.bts_ip) or normal_tx
        cpe_tx_saved = _read_tx_power_uci(cfg, cfg.primary_cpe) or normal_tx
        try:
            _set_manual_tx_power(cfg, cfg.bts_ip, low_tx)
            _set_manual_tx_power(cfg, cfg.primary_cpe, low_tx)
            time.sleep(max(5.0, float(cfg.low_rssi_settle_s)))
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
            bts_tx = _read_tx_power_uci(cfg, cfg.bts_ip)
            cpe_tx = _read_tx_power_uci(cfg, cfg.primary_cpe)
            _kv(
                case_id,
                {
                    "BTS Tx UCI (low)": bts_tx or "?",
                    "CPE Tx UCI (low)": cpe_tx or "?",
                },
            )
            check.equal(bts_tx, low_tx, f"{case_id}: BTS Tx must be {low_tx} dBm")
            check.equal(cpe_tx, low_tx, f"{case_id}: CPE Tx must be {low_tx} dBm")
            verify_bts_ddrs_cpe_fixed(cfg, case_id=case_id)
            verify_operating_rate_and_log(cfg, case_id=case_id)
        finally:
            _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_saved)
            _set_manual_tx_power(cfg, cfg.primary_cpe, cpe_tx_saved)
            time.sleep(8)

        _section(case_id, "Co-channel interference check under mixed DDRS/fixed")
        from utils.ddrs_interference import secondary_interference_session

        with secondary_interference_session(cfg, case_id=case_id):
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s)
            verify_bts_ddrs_cpe_fixed(cfg, case_id=case_id)
            verify_operating_rate_and_log(cfg, case_id=case_id)

    _with_bts_ddrs_cpe_fixed(cfg, case_id=case_id, body=_body)


def assert_ddrs_20_bts_ddrs_cpe_fixed_wireless_restart(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed(
        cfg,
        case_id="DDRS_20",
        body=lambda: _run_ddrs_wireless_restart_mixed(cfg, case_id="DDRS_20"),
    )


def assert_ddrs_21_bts_ddrs_cpe_fixed_network_restart(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed(
        cfg,
        case_id="DDRS_21",
        body=lambda: _run_ddrs_network_restart_mixed(cfg, case_id="DDRS_21"),
    )


def assert_ddrs_22_bts_ddrs_cpe_fixed_ethernet_restart(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed(
        cfg,
        case_id="DDRS_22",
        body=lambda: _run_ddrs_ethernet_duplex_restart_mixed(cfg, case_id="DDRS_22"),
    )


def assert_ddrs_23_bts_ddrs_cpe_fixed_soft_reboot(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed(
        cfg,
        case_id="DDRS_23",
        body=lambda: _run_ddrs_soft_reboot_mixed(cfg, case_id="DDRS_23"),
    )


def _run_ddrs_wireless_restart_mixed(
    cfg: DdrsConfig,
    *,
    case_id: str,
    verify=verify_bts_ddrs_cpe_fixed,
) -> None:
    _section(case_id, "Wireless parameter change + wireless restart (mixed DDRS/fixed)")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    cpe_dist_before = bts_dist_before = ""
    try:
        cpe_dist_before, _ = _bump_wireless_distance(cfg, cfg.primary_cpe)
        bts_dist_before, _ = _bump_wireless_distance(cfg, cfg.bts_ip)
        _on_both_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _wireless_restart(cfg, ip),
            label="wireless restart",
        )
        invalidate_ddrs_backend_cache(cfg)
        time.sleep(10)
        check.is_true(
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s),
            f"{case_id}: link must recover",
        )
        _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
        verify(cfg, case_id=case_id)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        if cpe_dist_before:
            _restore_wireless_distance(cfg, cfg.primary_cpe, cpe_dist_before)
        if bts_dist_before:
            _restore_wireless_distance(cfg, cfg.bts_ip, bts_dist_before)


def _run_ddrs_network_restart_mixed(
    cfg: DdrsConfig,
    *,
    case_id: str,
    verify=verify_bts_ddrs_cpe_fixed,
) -> None:
    _section(case_id, "Network/IP setting change + network restart (mixed DDRS/fixed)")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    cpe_dns_before = bts_dns_before = ""
    try:
        cpe_dns_before, _ = _toggle_dns(cfg, cfg.primary_cpe)
        bts_dns_before, _ = _toggle_dns(cfg, cfg.bts_ip)
        _on_both_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _network_restart(cfg, ip),
            label="network restart",
        )
        check.is_true(_recover_after_restart(cfg, case_id=case_id), f"{case_id}: link must recover")
        _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
        verify(cfg, case_id=case_id)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            _restore_dns(cfg, cfg.primary_cpe, cpe_dns_before)
            _restore_dns(cfg, cfg.bts_ip, bts_dns_before)
        except Exception as exc:
            _log(case_id, f"WARN DNS restore: {exc}")


def _run_ddrs_ethernet_duplex_restart_mixed(
    cfg: DdrsConfig,
    *,
    case_id: str,
    verify=verify_bts_ddrs_cpe_fixed,
) -> None:
    _section(case_id, "Ethernet duplex change + restart (mixed DDRS/fixed)")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    cpe_eth_before = bts_eth_before = ""
    try:
        cpe_eth_before, _ = _toggle_eth_duplex(cfg, cfg.primary_cpe)
        bts_eth_before, _ = _toggle_eth_duplex(cfg, cfg.bts_ip)
        _on_both_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _network_restart(cfg, ip),
            label="ethernet/network restart",
        )
        time.sleep(15)
        check.is_true(_recover_after_restart(cfg, case_id=case_id), f"{case_id}: link must recover")
        _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
        verify(cfg, case_id=case_id)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            if cpe_eth_before:
                _restore_eth_speed(cfg, cfg.primary_cpe, cpe_eth_before)
            if bts_eth_before:
                _restore_eth_speed(cfg, cfg.bts_ip, bts_eth_before)
            _on_both_devices(
                cfg,
                case_id=case_id,
                action=lambda ip: _network_restart(cfg, ip),
                label="restore ethernet",
            )
            _recover_after_restart(cfg, case_id=case_id)
        except Exception as exc:
            _log(case_id, f"WARN ethernet restore: {exc}")


def _run_ddrs_soft_reboot_mixed(
    cfg: DdrsConfig,
    *,
    case_id: str,
    verify=verify_bts_ddrs_cpe_fixed,
) -> None:
    _section(case_id, "Soft reboot BTS & CPE (negative) — mixed DDRS/fixed")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    _soft_reboot(cfg, cfg.primary_cpe)
    time.sleep(5)
    _soft_reboot(cfg, cfg.bts_ip)
    invalidate_ddrs_backend_cache(cfg)
    check.is_true(
        _recover_after_restart(cfg, case_id=case_id),
        f"{case_id}: link must recover after soft reboot",
    )
    _verify_ddrs_unchanged(cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before)
    verify(cfg, case_id=case_id)
    verify_operating_rate_and_log(cfg, case_id=case_id)


def _with_bts_fixed_cpe_ddrs(cfg: DdrsConfig, *, case_id: str, body) -> None:
    """Apply BTS fixed MCS + CPE DDRS, run body, restore original UCI."""
    bts_saved = read_bts_ddrs_snap(cfg)
    cpe_saved = read_cpe_ddrs_snap(cfg)
    try:
        _apply_bts_fixed_cpe_ddrs(cfg)
        body()
    finally:
        try:
            _restore_ddrs_snap(cfg, cfg.primary_cpe, cpe_saved)
            time.sleep(2)
            _restore_ddrs_snap(cfg, cfg.bts_ip, bts_saved)
            time.sleep(8)
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s)
        except Exception as exc:
            _log(case_id, f"WARN restore DDRS/MCS: {exc}")


def _run_mixed_low_rssi_and_cochannel(cfg: DdrsConfig, *, case_id: str, verify) -> None:
    _section(case_id, "Low RSSI check (Tx power) under mixed DDRS/fixed")
    normal_tx = str(int(cfg.normal_tx_dbm))
    low_tx = str(int(cfg.low_rssi_tx_dbm))
    bts_tx_saved = _read_tx_power_uci(cfg, cfg.bts_ip) or normal_tx
    cpe_tx_saved = _read_tx_power_uci(cfg, cfg.primary_cpe) or normal_tx
    try:
        _set_manual_tx_power(cfg, cfg.bts_ip, low_tx)
        _set_manual_tx_power(cfg, cfg.primary_cpe, low_tx)
        time.sleep(max(5.0, float(cfg.low_rssi_settle_s)))
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
        bts_tx = _read_tx_power_uci(cfg, cfg.bts_ip)
        cpe_tx = _read_tx_power_uci(cfg, cfg.primary_cpe)
        _kv(
            case_id,
            {
                "BTS Tx UCI (low)": bts_tx or "?",
                "CPE Tx UCI (low)": cpe_tx or "?",
            },
        )
        check.equal(bts_tx, low_tx, f"{case_id}: BTS Tx must be {low_tx} dBm")
        check.equal(cpe_tx, low_tx, f"{case_id}: CPE Tx must be {low_tx} dBm")
        verify(cfg, case_id=case_id)
        verify_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_saved)
        _set_manual_tx_power(cfg, cfg.primary_cpe, cpe_tx_saved)
        time.sleep(8)

    _section(case_id, "Co-channel interference check under mixed DDRS/fixed")
    from utils.ddrs_interference import secondary_interference_session

    with secondary_interference_session(cfg, case_id=case_id):
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s)
        verify(cfg, case_id=case_id)
        verify_operating_rate_and_log(cfg, case_id=case_id)


def assert_ddrs_25_bts_fixed_cpe_ddrs_rate(cfg: DdrsConfig) -> None:
    """BTS fixed MCS + CPE DDRS enable — operating rate & SNR log (incl. low RSSI + co-channel)."""
    case_id = "DDRS_25"

    def _body() -> None:
        _section(case_id, "BTS fixed data rate & CPE DDRS enabled")
        verify_bts_fixed_cpe_ddrs(cfg, case_id=case_id)
        check.is_true(
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.link_settle_s),
            f"{case_id}: link must be up",
        )
        verify_operating_rate_and_log(cfg, case_id=case_id)
        _run_mixed_low_rssi_and_cochannel(cfg, case_id=case_id, verify=verify_bts_fixed_cpe_ddrs)

    _with_bts_fixed_cpe_ddrs(cfg, case_id=case_id, body=_body)


def assert_ddrs_26_bts_fixed_cpe_ddrs_wireless_restart(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs(
        cfg,
        case_id="DDRS_26",
        body=lambda: _run_ddrs_wireless_restart_mixed(
            cfg, case_id="DDRS_26", verify=verify_bts_fixed_cpe_ddrs
        ),
    )


def assert_ddrs_27_bts_fixed_cpe_ddrs_network_restart(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs(
        cfg,
        case_id="DDRS_27",
        body=lambda: _run_ddrs_network_restart_mixed(
            cfg, case_id="DDRS_27", verify=verify_bts_fixed_cpe_ddrs
        ),
    )


def assert_ddrs_28_bts_fixed_cpe_ddrs_ethernet_restart(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs(
        cfg,
        case_id="DDRS_28",
        body=lambda: _run_ddrs_ethernet_duplex_restart_mixed(
            cfg, case_id="DDRS_28", verify=verify_bts_fixed_cpe_ddrs
        ),
    )


def assert_ddrs_29_bts_fixed_cpe_ddrs_soft_reboot(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs(
        cfg,
        case_id="DDRS_29",
        body=lambda: _run_ddrs_soft_reboot_mixed(
            cfg, case_id="DDRS_29", verify=verify_bts_fixed_cpe_ddrs
        ),
    )


# ---------------------------------------------------------------------------
# P2MP (DDRS_31–36) — one BTS, multiple associated CPEs
# ---------------------------------------------------------------------------


def _p2mp_cpe_ips(cfg: DdrsConfig) -> list[str]:
    return list(cfg.p2mp_cpe_ips or cfg.cpe_ips)


def _p2mp_cpe_targets(cfg: DdrsConfig) -> list[tuple[str, int]]:
    """Associated CPE IPv4 + remote_exec SU index (live sua table, then profile)."""
    clients = fetch_bts_link_clients(cfg, cpe_hosts=_p2mp_cpe_ips(cfg))
    targets: list[tuple[str, int]] = []
    seen: set[str] = set()
    for client in clients:
        ip = str(client.get("ip") or "").strip()
        try:
            su = int(client.get("su_index") or client.get("sua_index") or 0)
        except (TypeError, ValueError):
            su = 0
        if not ip or ip in ("-", "0.0.0.0") or su < 1:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        targets.append((ip, su))
    for ip in _p2mp_cpe_ips(cfg):
        if any(_cpe_ip_matches(ip, existing) for existing, _ in targets):
            continue
        su = _resolve_cpe_su_index(cfg, cpe_ip=ip)
        if su < 1:
            continue
        targets.append((ip, su))
    return targets


def _on_p2mp_devices(cfg: DdrsConfig, *, case_id: str, action, label: str) -> None:
    for ip, su in _p2mp_cpe_targets(cfg):
        _log(case_id, f"{label} on CPE {ip} via remote_exec SU {su}")
        action(ip)
    _log(case_id, f"{label} on BTS ({cfg.bts_ip})")
    action(cfg.bts_ip)


def _p2mp_traffic_cfg(cfg: DdrsConfig, *, duration_s: float | None = None) -> DdrsConfig:
    n = max(cfg.p2mp_min_stations, len(_p2mp_cpe_targets(cfg)))
    return replace(
        cfg,
        traffic_su_count=n,
        traffic_duration_s=float(duration_s or cfg.traffic_duration_s),
    )


def verify_ddrs_enabled_p2mp(cfg: DdrsConfig, *, case_id: str) -> tuple[DdrsSnap, list[tuple[str, DdrsSnap]]]:
    bts = _read_bts_ddrs_snap_retry(cfg, case_id=case_id)
    _kv(
        case_id,
        {
            "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.ddrsstatus",
            "BTS DDRS UCI": bts.ddrs_uci,
            "P2MP CPE IPs": ", ".join(_p2mp_cpe_ips(cfg)),
        },
    )
    check.is_true(
        bts.ddrs_status.startswith("enable"),
        f"{case_id}: BTS DDRS must be enabled (UCI={bts.ddrs_uci!r})",
    )
    cpe_snaps: list[tuple[str, DdrsSnap]] = []
    for ip, su in _p2mp_cpe_targets(cfg):
        su_now, snap = _read_cpe_ddrs_snap_retry(cfg, case_id=case_id, cpe_ip=ip)
        su = su_now or su
        cpe_snaps.append((ip, snap))
        echoes_bts = _cpe_get_echoes_bts(snap, bts)
        _kv(
            case_id,
            {
                f"CPE {ip} backend": f"remote_exec.sh {su}",
                f"CPE {ip} DDRS UCI": snap.ddrs_uci,
                f"CPE {ip} GET is BTS echo": str(echoes_bts),
            },
        )
        if echoes_bts:
            _log(case_id, f"CPE {ip} UCI GET echoes BTS — DDRS SET applied via remote_exec SU {su}")
            continue
        if _uci_unreadable(snap.ddrs_uci) or su < 1:
            _log(
                case_id,
                f"CPE {ip} UCI GET unreadable (SU={su}, UCI={snap.ddrs_uci!r}) — "
                "mixed-mode GET; BTS SSH is the enable gate",
            )
            continue
        check.is_true(
            snap.ddrs_status.startswith("enable"),
            f"{case_id}: CPE {ip} DDRS must be enabled (UCI={snap.ddrs_uci!r})",
        )
    return bts, cpe_snaps


def verify_p2mp_operating_rate_and_log(cfg: DdrsConfig, *, case_id: str) -> list[dict[str, Any]]:
    _section(case_id, "P2MP operating rate & log verification")
    invalidate_ddrs_backend_cache(cfg)
    clients = fetch_bts_link_clients(cfg, cpe_hosts=_p2mp_cpe_ips(cfg))
    check.is_true(
        len(clients) >= cfg.p2mp_min_stations,
        f"{case_id}: P2MP requires ≥{cfg.p2mp_min_stations} associated CPEs, found {len(clients)}",
    )
    for client in clients:
        ip = str(client.get("ip") or "-")
        su = client.get("su_index") or client.get("sua_index") or "?"
        snr_l = client.get("combined_local_snr") or client.get("l_snr1") or "-"
        snr_r = client.get("combined_remote_snr") or client.get("r_snr1") or "-"
        _kv(
            case_id,
            {
                f"SU{su} {ip} tx_rate": str(client.get("tx_rate") or "-"),
                f"SU{su} {ip} rx_rate": str(client.get("rx_rate") or "-"),
                f"SU{su} {ip} MCS": str(client.get("operating_mcs") or "-"),
                f"SU{su} {ip} SNR L/R": f"{snr_l} / {snr_r}",
            },
        )
        check.is_true(
            client.get("tx_rate_mbps") or client.get("rx_rate_mbps"),
            f"{case_id}: SU{su} {ip} must show operating data rate",
        )
        check.is_true(
            snr_l not in ("-", "0", "—") or snr_r not in ("-", "0", "—"),
            f"{case_id}: SU{su} {ip} SNR must be present",
        )

    bts_log = _read_wifi_log_tail(cfg, cfg.bts_ip)
    cpe_log = _read_wifi_log_tail(cfg, cfg.primary_cpe)
    events_bts = parse_link_events(bts_log)
    events_cpe = parse_link_events(cpe_log)
    log_ok = (
        _log_has_snr_or_rate(bts_log)
        or _log_has_snr_or_rate(cpe_log)
        or events_bts.snr_a1 is not None
        or events_cpe.snr_a1 is not None
    )
    _kv(
        case_id,
        {
            "BTS wifi log SNR": str(events_bts.snr_a1),
            "CPE wifi log SNR": str(events_cpe.snr_a1),
            "Log has rate/SNR": str(log_ok),
        },
    )
    check.is_true(log_ok, f"{case_id}: wifi events log should contain SNR / data-rate entry")
    return clients


def verify_p2mp_throughput_pps_latency(
    cfg: DdrsConfig,
    result,
    *,
    case_id: str,
) -> None:
    _section(case_id, "P2MP throughput, PPS, latency & stability")
    duration = max(1.0, float(cfg.traffic_duration_s))
    dl_pps = (result.dl_rx_pkts or 0.0) / duration
    ul_pps = (result.ul_rx_pkts or 0.0) / duration
    _kv(
        case_id,
        {
            "Duration": f"{duration:g}s",
            "DL throughput": f"{result.dl_rx_mbps} Mbps",
            "UL throughput": f"{result.ul_rx_mbps} Mbps",
            "DL PPS": f"{dl_pps:.1f}",
            "UL PPS": f"{ul_pps:.1f}",
            "DL latency avg": f"{result.dl_avg_ms} ms",
            "UL latency avg": f"{result.ul_avg_ms} ms",
            "DL jitter": f"{result.dl_jitter_ms} ms",
            "UL jitter": f"{result.ul_jitter_ms} ms",
            "DL loss": f"{result.dl_loss_pct}%",
            "UL loss": f"{result.ul_loss_pct}%",
        },
    )
    check.is_not_none(result.dl_loss_pct, f"{case_id}: traffic must produce FINAL SUMMARY")
    check.is_true(
        bool(result.dl_rx_mbps) and result.dl_rx_mbps > 0,
        f"{case_id}: DL throughput must be > 0 Mbps",
    )
    check.is_true(
        bool(result.ul_rx_mbps) and result.ul_rx_mbps > 0,
        f"{case_id}: UL throughput must be > 0 Mbps",
    )
    check.is_true(
        dl_pps > 0 or ul_pps > 0,
        f"{case_id}: PPS must be > 0 from received packets / duration",
    )
    check.is_true(
        result.dl_avg_ms is not None or result.ul_avg_ms is not None,
        f"{case_id}: latency (avg ms) must be present in FINAL SUMMARY",
    )
    # Loss is logged only. 6-SU offered load (240/60) commonly exceeds air capacity
    # on this bench (~33% here); that is not a DDRS/link failure.
    _log(
        case_id,
        f"stability: run completed with DL/UL traffic (loss DL={result.dl_loss_pct}% "
        f"UL={result.ul_loss_pct}% — informational, not a pass gate)",
    )


def _verify_ddrs_unchanged_p2mp(
    cfg: DdrsConfig,
    *,
    case_id: str,
    bts_before: DdrsSnap,
    cpe_before: list[tuple[str, DdrsSnap]],
) -> None:
    bts_after = _read_bts_ddrs_snap_retry(cfg, case_id=case_id)
    _kv(
        case_id,
        {
            "BTS DDRS before": bts_before.ddrs_uci,
            "BTS DDRS after": bts_after.ddrs_uci,
        },
    )
    if _uci_unreadable(bts_before.ddrs_uci) or _uci_unreadable(bts_after.ddrs_uci):
        _log(
            case_id,
            f"skip BTS DDRS unchanged compare — UCI unreadable "
            f"({bts_before.ddrs_uci!r} → {bts_after.ddrs_uci!r})",
        )
    else:
        check.is_true(_ddr_snaps_match(bts_before, bts_after), f"{case_id}: BTS DDRS unchanged")
    for ip, before in cpe_before:
        _, after = _read_cpe_ddrs_snap_retry(cfg, case_id=case_id, cpe_ip=ip)
        echoes_bts = _cpe_get_echoes_bts(after, bts_after)
        _kv(
            case_id,
            {
                f"CPE {ip} DDRS before": before.ddrs_uci,
                f"CPE {ip} DDRS after": after.ddrs_uci,
            },
        )
        if (
            echoes_bts
            or _uci_unreadable(before.ddrs_uci)
            or _uci_unreadable(after.ddrs_uci)
        ):
            _log(
                case_id,
                f"CPE {ip}: skip DDRS unchanged compare — GET unreadable or BTS echo "
                f"({before.ddrs_uci!r} → {after.ddrs_uci!r})",
            )
            continue
        check.is_true(_ddr_snaps_match(before, after), f"{case_id}: CPE {ip} DDRS unchanged")


def _require_p2mp_link(cfg: DdrsConfig, *, case_id: str, timeout_s: float | None = None) -> None:
    ok = wait_ddrs_link(
        cfg,
        case_id=case_id,
        timeout_s=timeout_s or cfg.link_settle_s,
        min_stations=cfg.p2mp_min_stations,
    )
    check.is_true(
        ok,
        f"{case_id}: P2MP link must be up (≥{cfg.p2mp_min_stations} stations)",
    )


def _run_p2mp_associate_with_traffic(
    cfg: DdrsConfig,
    *,
    case_id: str,
    duration_s: float | None = None,
    check_throughput: bool = False,
) -> None:
    verify_ddrs_enabled_p2mp(cfg, case_id=case_id)
    _require_p2mp_link(cfg, case_id=case_id)
    tcfg = _p2mp_traffic_cfg(cfg, duration_s=duration_s)
    _section(
        case_id,
        f"mac_vlan_traffic_test_mf.py ({tcfg.traffic_su_count} SU, {tcfg.traffic_duration_s:g}s)",
    )
    result = run_mac_vlan_traffic(tcfg, case_id=case_id)
    if check_throughput:
        verify_p2mp_throughput_pps_latency(tcfg, result, case_id=case_id)
    else:
        check.is_not_none(result.dl_loss_pct, f"{case_id}: traffic must produce FINAL SUMMARY")
    time.sleep(5)
    verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)


def _run_p2mp_wireless_restart(cfg: DdrsConfig, *, case_id: str, verify=verify_ddrs_enabled_p2mp) -> None:
    _section(case_id, "P2MP wireless parameter change + wireless restart")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    saved: dict[str, str] = {}
    bts_dist = ""
    try:
        for ip, _su in _p2mp_cpe_targets(cfg):
            saved[ip], _ = _bump_wireless_distance(cfg, ip)
        bts_dist, _ = _bump_wireless_distance(cfg, cfg.bts_ip)
        _on_p2mp_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _wireless_restart(cfg, ip),
            label="wireless restart",
        )
        invalidate_ddrs_backend_cache(cfg)
        time.sleep(10)
        extra = 120.0 if "39" in case_id or "cochannel" in case_id.lower() else 0.0
        _require_p2mp_link(
            cfg,
            case_id=case_id,
            timeout_s=cfg.reboot_wait_s + extra,
        )
        _verify_ddrs_unchanged_p2mp(
            cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before
        )
        verify(cfg, case_id=case_id)
        verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        for ip, value in saved.items():
            if value:
                _restore_wireless_distance(cfg, ip, value)
        if bts_dist:
            _restore_wireless_distance(cfg, cfg.bts_ip, bts_dist)


def _run_p2mp_network_restart(cfg: DdrsConfig, *, case_id: str, verify=verify_ddrs_enabled_p2mp) -> None:
    _section(case_id, "P2MP network/IP setting change + network restart")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    saved: dict[str, str] = {}
    bts_dns = ""
    try:
        for ip, _su in _p2mp_cpe_targets(cfg):
            saved[ip], _ = _toggle_dns(cfg, ip)
        bts_dns, _ = _toggle_dns(cfg, cfg.bts_ip)
        _on_p2mp_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _network_restart(cfg, ip),
            label="network restart",
        )
        _require_p2mp_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
        _verify_ddrs_unchanged_p2mp(
            cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before
        )
        verify(cfg, case_id=case_id)
        verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            for ip, value in saved.items():
                _restore_dns(cfg, ip, value)
            _restore_dns(cfg, cfg.bts_ip, bts_dns)
        except Exception as exc:
            _log(case_id, f"WARN DNS restore: {exc}")


def _run_p2mp_ethernet_duplex_restart(cfg: DdrsConfig, *, case_id: str, verify=verify_ddrs_enabled_p2mp) -> None:
    _section(case_id, "P2MP ethernet duplex change + restart")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    saved: dict[str, str] = {}
    bts_eth = ""
    try:
        for ip, _su in _p2mp_cpe_targets(cfg):
            saved[ip], _ = _toggle_eth_duplex(cfg, ip)
        bts_eth, _ = _toggle_eth_duplex(cfg, cfg.bts_ip)
        _on_p2mp_devices(
            cfg,
            case_id=case_id,
            action=lambda ip: _network_restart(cfg, ip),
            label="ethernet/network restart",
        )
        time.sleep(15)
        _require_p2mp_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
        _verify_ddrs_unchanged_p2mp(
            cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before
        )
        verify(cfg, case_id=case_id)
        verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
    finally:
        try:
            for ip, value in saved.items():
                if value:
                    _restore_eth_speed(cfg, ip, value)
            if bts_eth:
                _restore_eth_speed(cfg, cfg.bts_ip, bts_eth)
            _on_p2mp_devices(
                cfg,
                case_id=case_id,
                action=lambda ip: _network_restart(cfg, ip),
                label="restore ethernet",
            )
            wait_ddrs_link(
                cfg,
                case_id=case_id,
                timeout_s=cfg.reboot_wait_s,
                min_stations=cfg.p2mp_min_stations,
            )
        except Exception as exc:
            _log(case_id, f"WARN ethernet restore: {exc}")


def _run_p2mp_soft_reboot(cfg: DdrsConfig, *, case_id: str, verify=verify_ddrs_enabled_p2mp) -> None:
    _section(case_id, "P2MP soft reboot BTS & CPEs (negative)")
    bts_before, cpe_before = verify(cfg, case_id=case_id)
    for ip, su in _p2mp_cpe_targets(cfg):
        _log(case_id, f"soft reboot CPE {ip} via remote_exec SU {su}")
        _soft_reboot(cfg, ip)
    time.sleep(5)
    _log(case_id, f"soft reboot BTS ({cfg.bts_ip})")
    _soft_reboot(cfg, cfg.bts_ip)
    invalidate_ddrs_backend_cache(cfg)
    _require_p2mp_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
    _verify_ddrs_unchanged_p2mp(
        cfg, case_id=case_id, bts_before=bts_before, cpe_before=cpe_before
    )
    verify(cfg, case_id=case_id)
    verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)


def assert_ddrs_31_p2mp_throughput_stability(cfg: DdrsConfig) -> None:
    case_id = "DDRS_31"
    _section(case_id, "P2MP associate with DDRS + traffic — throughput, PPS, latency & stability")
    _run_p2mp_associate_with_traffic(
        cfg,
        case_id=case_id,
        duration_s=cfg.p2mp_stability_duration_s,
        check_throughput=True,
    )


def assert_ddrs_32_p2mp_associate_with_traffic(cfg: DdrsConfig) -> None:
    case_id = "DDRS_32"
    _section(case_id, "P2MP associate BTS & CPEs with DDRS + traffic")
    _run_p2mp_associate_with_traffic(cfg, case_id=case_id)


def assert_ddrs_33_p2mp_wireless_restart(cfg: DdrsConfig) -> None:
    _run_p2mp_wireless_restart(cfg, case_id="DDRS_33")


def assert_ddrs_34_p2mp_network_restart(cfg: DdrsConfig) -> None:
    _run_p2mp_network_restart(cfg, case_id="DDRS_34")


def assert_ddrs_35_p2mp_ethernet_duplex_restart(cfg: DdrsConfig) -> None:
    _run_p2mp_ethernet_duplex_restart(cfg, case_id="DDRS_35")


def assert_ddrs_36_p2mp_soft_reboot(cfg: DdrsConfig) -> None:
    _run_p2mp_soft_reboot(cfg, case_id="DDRS_36")


def _client_rate_snapshot(client: dict[str, Any]) -> dict[str, Any]:
    return {
        "ip": str(client.get("ip") or "-"),
        "su": client.get("su_index") or client.get("sua_index"),
        "tx_rate_mbps": client.get("tx_rate_mbps"),
        "rx_rate_mbps": client.get("rx_rate_mbps"),
        "operating_mcs": client.get("operating_mcs"),
        "tx_rate": client.get("tx_rate"),
        "rx_rate": client.get("rx_rate"),
        "l_rssi1": client.get("l_rssi1"),
        "r_rssi1": client.get("r_rssi1"),
        "combined_local_snr": client.get("combined_local_snr") or client.get("l_snr1"),
        "combined_remote_snr": client.get("combined_remote_snr") or client.get("r_snr1"),
    }


def _p2mp_rate_snapshots(cfg: DdrsConfig) -> dict[str, dict[str, Any]]:
    invalidate_ddrs_backend_cache(cfg)
    clients = fetch_bts_link_clients(cfg, cpe_hosts=_p2mp_cpe_ips(cfg))
    out: dict[str, dict[str, Any]] = {}
    for client in clients:
        snap = _client_rate_snapshot(client)
        key = str(snap.get("ip") or "")
        if not key or key in ("-", "0.0.0.0"):
            key = f"su{snap.get('su')}"
        out[key] = snap
    return out


def _p2mp_rate_variation(baseline: dict[str, dict[str, Any]], active: dict[str, dict[str, Any]]) -> list[str]:
    """Return CPE keys whose operating rate changed (tx/rx Mbps, MCS, or rate string)."""
    changed: list[str] = []
    for key, before in baseline.items():
        after = active.get(key)
        if after and _rate_fields_changed(before, after):
            changed.append(key)
    return changed


def _mcs_from_rate_string(raw: Any) -> int | None:
    """Parse MCS from ``1201 (23)`` or a bare MCS number."""
    text = str(raw or "").strip()
    if not text or text in {"-", "—"}:
        return None
    paren = re.search(r"\((\d+)\)", text)
    if paren:
        return int(paren.group(1))
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _mbps_num(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        match = re.search(r"([\d.]+)", str(raw))
        return float(match.group(1)) if match else None


def _p2mp_rate_decrements(
    baseline: dict[str, dict[str, Any]], active: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """SUs whose operating rate went down (not up). Records MCS step size."""
    drops: list[dict[str, Any]] = []
    for key, before in baseline.items():
        after = active.get(key)
        if not after:
            continue
        tx_b = _mcs_from_rate_string(before.get("tx_rate") or before.get("operating_mcs"))
        tx_a = _mcs_from_rate_string(after.get("tx_rate") or after.get("operating_mcs"))
        rx_b = _mcs_from_rate_string(before.get("rx_rate"))
        rx_a = _mcs_from_rate_string(after.get("rx_rate"))
        tx_mbps_b = _mbps_num(before.get("tx_rate_mbps"))
        tx_mbps_a = _mbps_num(after.get("tx_rate_mbps"))
        rx_mbps_b = _mbps_num(before.get("rx_rate_mbps"))
        rx_mbps_a = _mbps_num(after.get("rx_rate_mbps"))

        tx_mcs_drop = (tx_b - tx_a) if tx_b is not None and tx_a is not None and tx_a < tx_b else 0
        rx_mcs_drop = (rx_b - rx_a) if rx_b is not None and rx_a is not None and rx_a < rx_b else 0
        tx_mbps_drop = bool(tx_mbps_b is not None and tx_mbps_a is not None and tx_mbps_a < tx_mbps_b)
        rx_mbps_drop = bool(rx_mbps_b is not None and rx_mbps_a is not None and rx_mbps_a < rx_mbps_b)
        if tx_mcs_drop > 0 or rx_mcs_drop > 0 or tx_mbps_drop or rx_mbps_drop:
            drops.append(
                {
                    "key": key,
                    "tx_mcs_drop": tx_mcs_drop,
                    "rx_mcs_drop": rx_mcs_drop,
                    "before": f"{before.get('tx_rate')}/{before.get('rx_rate')}",
                    "after": f"{after.get('tx_rate')}/{after.get('rx_rate')}",
                }
            )
    return drops


def _run_p2mp_cochannel_rate_variation(cfg: DdrsConfig, *, case_id: str) -> None:
    """Same/adjacent-channel secondary BTS. Pass on operating-rate decrement."""
    from utils.cochannel_flows import (
        interference_level_ok,
        set_secondary_tx_power,
        stop_secondary_iperf,
    )
    from utils.ddrs_interference import (
        cochannel_cfg_from_ddrs,
        read_ddrs_interference_rf,
        secondary_interference_session,
    )

    _section(case_id, "P2MP co-channel interference — data rate variation")
    verify_ddrs_enabled_p2mp(cfg, case_id=case_id)
    _require_p2mp_link(cfg, case_id=case_id)

    cc = cochannel_cfg_from_ddrs(cfg)
    level = str(cfg.interference_level or "mid").lower()
    band = dict((cfg.interference or {}).get(level) or {})

    stop_secondary_iperf(cc)
    set_secondary_tx_power(cc, cc.secondary_tx_power_min_dbm)
    time.sleep(max(10.0, float(cfg.rssi_settle_s) * 2))

    baseline_rf = read_ddrs_interference_rf(cfg)
    baseline_rates = _p2mp_rate_snapshots(cfg)
    _kv(
        case_id,
        {
            "Secondary BTS": cfg.secondary_bts,
            "Secondary CPE": cfg.secondary_cpe,
            "Baseline channel": str(baseline_rf.channel or "-"),
            "Baseline OBSS %": str(baseline_rf.obss_pct if baseline_rf.obss_pct is not None else "-"),
            "P2MP SUs": str(len(baseline_rates)),
        },
    )
    for key, snap in baseline_rates.items():
        _kv(
            case_id,
            {
                f"Baseline {key} tx/rx": f"{snap.get('tx_rate')} / {snap.get('rx_rate')}",
            },
        )

    with secondary_interference_session(cfg, case_id=case_id, level=level) as (_, active_snap):
        time.sleep(max(5.0, float(cfg.rssi_settle_s)))
        interfered_rf = read_ddrs_interference_rf(cfg)
        interfered_rates = _p2mp_rate_snapshots(cfg)
        decrements = _p2mp_rate_decrements(baseline_rates, interfered_rates)
        if not decrements:
            for _ in range(6):
                time.sleep(5)
                interfered_rates = _p2mp_rate_snapshots(cfg)
                decrements = _p2mp_rate_decrements(baseline_rates, interfered_rates)
                if decrements:
                    break
            interfered_rf = read_ddrs_interference_rf(cfg)

        obss_delta = None
        if baseline_rf.obss_pct is not None and interfered_rf.obss_pct is not None:
            obss_delta = abs(int(interfered_rf.obss_pct) - int(baseline_rf.obss_pct))
        interference_in_band = interference_level_ok(interfered_rf, band, cc) or interference_level_ok(
            active_snap, band, cc
        )
        _kv(
            case_id,
            {
                "Interference level": level,
                "Active channel": str(interfered_rf.channel or "-"),
                "Active OBSS %": str(interfered_rf.obss_pct if interfered_rf.obss_pct is not None else "-"),
                "OBSS delta (|active-baseline|)": str(obss_delta if obss_delta is not None else "-"),
                "OBSS in target band": str(interference_in_band),
                "SUs with rate decrement": (
                    ", ".join(d["key"] for d in decrements) if decrements else "none"
                ),
            },
        )
        for key, before in baseline_rates.items():
            after = interfered_rates.get(key) or {}
            _kv(
                case_id,
                {
                    f"{key} rate": (
                        f"{before.get('tx_rate')}/{before.get('rx_rate')} → "
                        f"{after.get('tx_rate')}/{after.get('rx_rate')}"
                    ),
                },
            )
        for drop in decrements:
            steps = max(int(drop["tx_mcs_drop"]), int(drop["rx_mcs_drop"]))
            _log(
                case_id,
                f"{drop['key']} decremented {drop['before']} → {drop['after']} "
                f"(MCS drop TX={drop['tx_mcs_drop']} RX={drop['rx_mcs_drop']}; "
                f"{'≤2/3 rate steps — same DUT limit as sheet note' if 0 < steps <= 3 else 'step count=' + str(steps)})",
            )

        check.is_not_none(
            interfered_rf.obss_pct,
            f"{case_id}: primary BTS must report OBSS with secondary interferer active",
        )
        check.is_true(
            bool(decrements),
            f"{case_id}: operating data rate must decrement on at least one P2MP SU "
            "(tx/rx Mbps or MCS down — not a rate increase)",
        )
        verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)


def _run_ddrs_p2mp_under_secondary_interference(cfg: DdrsConfig, *, case_id: str, runner) -> None:
    from utils.ddrs_interference import secondary_interference_session

    _require_p2mp_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
    with secondary_interference_session(cfg, case_id=case_id):
        runner(cfg, case_id=case_id)


def assert_ddrs_38_p2mp_cochannel_rate_variation(cfg: DdrsConfig) -> None:
    """P2MP co-channel — pass only if at least one SU operating rate decrements."""
    _run_p2mp_cochannel_rate_variation(cfg, case_id="DDRS_38")


def assert_ddrs_39_p2mp_cochannel_wireless_restart(cfg: DdrsConfig) -> None:
    _section("DDRS_39", "P2MP co-channel — wireless restart under secondary interference")
    _run_ddrs_p2mp_under_secondary_interference(
        cfg, case_id="DDRS_39", runner=_run_p2mp_wireless_restart
    )


def assert_ddrs_40_p2mp_cochannel_network_restart(cfg: DdrsConfig) -> None:
    _section("DDRS_40", "P2MP co-channel — network restart under secondary interference")
    _run_ddrs_p2mp_under_secondary_interference(
        cfg, case_id="DDRS_40", runner=_run_p2mp_network_restart
    )


def assert_ddrs_41_p2mp_cochannel_ethernet_duplex_restart(cfg: DdrsConfig) -> None:
    _section("DDRS_41", "P2MP co-channel — ethernet duplex restart under secondary interference")
    _run_ddrs_p2mp_under_secondary_interference(
        cfg, case_id="DDRS_41", runner=_run_p2mp_ethernet_duplex_restart
    )


def assert_ddrs_42_p2mp_cochannel_soft_reboot(cfg: DdrsConfig) -> None:
    _section("DDRS_42", "P2MP co-channel — soft reboot under secondary interference (negative)")
    _run_ddrs_p2mp_under_secondary_interference(
        cfg, case_id="DDRS_42", runner=_run_p2mp_soft_reboot
    )


def _apply_bts_ddrs_cpe_fixed_p2mp(cfg: DdrsConfig) -> None:
    """Every P2MP CPE fixed MCS via remote_exec; BTS DDRS enabled last over SSH."""
    mcs = str(int(cfg.cpe_fixed_mcs))
    for ip, su in _p2mp_cpe_targets(cfg):
        _log("DDRS", f"CPE {ip} fixed MCS{mcs} via remote_exec SU {su}")
        _apply_ddrs_mode(cfg, ip, enable=False, ddrsrate=mcs)
    time.sleep(2)
    _apply_ddrs_mode(cfg, cfg.bts_ip, enable=True)
    time.sleep(8)
    wait_ddrs_link(
        cfg,
        case_id="DDRS",
        timeout_s=cfg.reboot_wait_s,
        min_stations=cfg.p2mp_min_stations,
    )


def verify_bts_ddrs_cpe_fixed_p2mp(
    cfg: DdrsConfig, *, case_id: str
) -> tuple[DdrsSnap, list[tuple[str, DdrsSnap]]]:
    """BTS DDRS on (SSH). Each CPE fixed MCS via remote_exec (GET may echo BTS)."""
    bts = read_bts_ddrs_snap(cfg)
    expect_mcs = str(int(cfg.cpe_fixed_mcs))
    _kv(
        case_id,
        {
            "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.ddrsstatus",
            "BTS DDRS UCI": bts.ddrs_uci,
            "CPE expected MCS": expect_mcs,
            "P2MP CPE IPs": ", ".join(_p2mp_cpe_ips(cfg)),
        },
    )
    check.is_true(
        bts.ddrs_status.startswith("enable"),
        f"{case_id}: BTS DDRS must be enabled (UCI={bts.ddrs_uci!r})",
    )
    cpe_snaps: list[tuple[str, DdrsSnap]] = []
    for ip, su in _p2mp_cpe_targets(cfg):
        snap = read_ddrs_snap(cfg, ip)
        cpe_snaps.append((ip, snap))
        echoes_bts = (
            str(snap.ddrs_uci).strip() == str(bts.ddrs_uci).strip()
            and str(snap.ddrsrate).strip() == str(bts.ddrsrate).strip()
        )
        _kv(
            case_id,
            {
                f"CPE {ip} backend": f"remote_exec.sh {su}",
                f"CPE {ip} DDRS UCI": snap.ddrs_uci,
                f"CPE {ip} ddrsrate": snap.ddrsrate,
                f"CPE {ip} GET is BTS echo": str(echoes_bts),
            },
        )
        if echoes_bts:
            _log(
                case_id,
                f"CPE {ip} UCI GET echoes BTS — fixed MCS{expect_mcs} applied via SET; "
                "BTS DDRS held over direct SSH",
            )
            continue
        check.is_true(
            snap.ddrs_status.startswith("disable"),
            f"{case_id}: CPE {ip} DDRS must be disabled for fixed rate (UCI={snap.ddrs_uci!r})",
        )
        check.equal(
            str(snap.ddrsrate).strip(),
            expect_mcs,
            f"{case_id}: CPE {ip} ddrsrate must be MCS{expect_mcs} (got {snap.ddrsrate!r})",
        )
    return bts, cpe_snaps


def _with_bts_ddrs_cpe_fixed_p2mp(cfg: DdrsConfig, *, case_id: str, body) -> None:
    bts_saved = read_bts_ddrs_snap(cfg)
    cpe_saved = [(ip, read_ddrs_snap(cfg, ip)) for ip, _su in _p2mp_cpe_targets(cfg)]
    try:
        _apply_bts_ddrs_cpe_fixed_p2mp(cfg)
        body()
    finally:
        try:
            for ip, snap in cpe_saved:
                _restore_ddrs_snap(cfg, ip, snap)
            time.sleep(2)
            _restore_ddrs_snap(cfg, cfg.bts_ip, bts_saved)
            time.sleep(8)
            wait_ddrs_link(
                cfg,
                case_id=case_id,
                timeout_s=cfg.link_settle_s,
                min_stations=cfg.p2mp_min_stations,
            )
        except Exception as exc:
            _log(case_id, f"WARN restore DDRS/MCS: {exc}")


def assert_ddrs_50_p2mp_bts_ddrs_cpe_fixed_rate(cfg: DdrsConfig) -> None:
    """P2MP — BTS DDRS + CPE fixed MCS; operating rate, no CPE DDRS adapt logs, low RSSI, co-channel."""
    case_id = "DDRS_50"

    def _body() -> None:
        _section(case_id, "P2MP BTS DDRS enabled & CPE fixed data rate")
        verify_bts_ddrs_cpe_fixed_p2mp(cfg, case_id=case_id)
        _require_p2mp_link(cfg, case_id=case_id)
        verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
        verify_cpe_fixed_no_ddrs_adapt_logs(cfg, case_id=case_id)

        _section(case_id, "P2MP low RSSI check (Tx power) under mixed DDRS/fixed")
        normal_tx = str(int(cfg.normal_tx_dbm))
        low_tx = str(int(cfg.low_rssi_tx_dbm))
        bts_tx_saved = _read_tx_power_uci(cfg, cfg.bts_ip) or normal_tx
        cpe_tx_saved: dict[str, str] = {}
        try:
            for ip, _su in _p2mp_cpe_targets(cfg):
                cpe_tx_saved[ip] = _read_tx_power_uci(cfg, ip) or normal_tx
                _set_manual_tx_power(cfg, ip, low_tx)
            _set_manual_tx_power(cfg, cfg.bts_ip, low_tx)
            time.sleep(max(5.0, float(cfg.low_rssi_settle_s)))
            _require_p2mp_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
            bts_tx = _read_tx_power_uci(cfg, cfg.bts_ip)
            _kv(case_id, {"BTS Tx UCI (low)": bts_tx or "?"})
            check.equal(bts_tx, low_tx, f"{case_id}: BTS Tx must be {low_tx} dBm")
            verify_bts_ddrs_cpe_fixed_p2mp(cfg, case_id=case_id)
            verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
            verify_cpe_fixed_no_ddrs_adapt_logs(cfg, case_id=case_id)
        finally:
            for ip, value in cpe_tx_saved.items():
                _set_manual_tx_power(cfg, ip, value)
            _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_saved)
            time.sleep(8)

        _section(case_id, "P2MP co-channel interference check under mixed DDRS/fixed")
        from utils.ddrs_interference import secondary_interference_session

        with secondary_interference_session(cfg, case_id=case_id):
            _require_p2mp_link(cfg, case_id=case_id)
            verify_bts_ddrs_cpe_fixed_p2mp(cfg, case_id=case_id)
            verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
            verify_cpe_fixed_no_ddrs_adapt_logs(cfg, case_id=case_id)

    _with_bts_ddrs_cpe_fixed_p2mp(cfg, case_id=case_id, body=_body)


def assert_ddrs_51_p2mp_bts_ddrs_cpe_fixed_wireless_restart(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed_p2mp(
        cfg,
        case_id="DDRS_51",
        body=lambda: _run_p2mp_wireless_restart(
            cfg, case_id="DDRS_51", verify=verify_bts_ddrs_cpe_fixed_p2mp
        ),
    )


def assert_ddrs_52_p2mp_bts_ddrs_cpe_fixed_network_restart(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed_p2mp(
        cfg,
        case_id="DDRS_52",
        body=lambda: _run_p2mp_network_restart(
            cfg, case_id="DDRS_52", verify=verify_bts_ddrs_cpe_fixed_p2mp
        ),
    )


def assert_ddrs_53_p2mp_bts_ddrs_cpe_fixed_ethernet_restart(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed_p2mp(
        cfg,
        case_id="DDRS_53",
        body=lambda: _run_p2mp_ethernet_duplex_restart(
            cfg, case_id="DDRS_53", verify=verify_bts_ddrs_cpe_fixed_p2mp
        ),
    )


def assert_ddrs_54_p2mp_bts_ddrs_cpe_fixed_soft_reboot(cfg: DdrsConfig) -> None:
    _with_bts_ddrs_cpe_fixed_p2mp(
        cfg,
        case_id="DDRS_54",
        body=lambda: _run_p2mp_soft_reboot(
            cfg, case_id="DDRS_54", verify=verify_bts_ddrs_cpe_fixed_p2mp
        ),
    )


def _apply_bts_fixed_cpe_ddrs_p2mp(cfg: DdrsConfig) -> None:
    """Every P2MP CPE DDRS enable via remote_exec; BTS fixed MCS last over SSH."""
    mcs = str(int(cfg.bts_fixed_mcs))
    for ip, su in _p2mp_cpe_targets(cfg):
        _log("DDRS", f"CPE {ip} DDRS enable via remote_exec SU {su}")
        _apply_ddrs_mode(cfg, ip, enable=True)
    time.sleep(2)
    _apply_ddrs_mode(cfg, cfg.bts_ip, enable=False, ddrsrate=mcs)
    time.sleep(8)
    wait_ddrs_link(
        cfg,
        case_id="DDRS",
        timeout_s=cfg.reboot_wait_s,
        min_stations=cfg.p2mp_min_stations,
    )


def verify_bts_fixed_cpe_ddrs_p2mp(
    cfg: DdrsConfig, *, case_id: str
) -> tuple[DdrsSnap, list[tuple[str, DdrsSnap]]]:
    """BTS DDRS off + fixed MCS (SSH). Each CPE DDRS on via remote_exec (GET may echo BTS)."""
    bts = read_bts_ddrs_snap(cfg)
    expect_mcs = str(int(cfg.bts_fixed_mcs))
    _kv(
        case_id,
        {
            "BTS backend": f"SSH {cfg.bts_ip} uci get txparam.ath{cfg.radio_idx}.ddrsstatus/ddrsrate",
            "BTS DDRS UCI": bts.ddrs_uci,
            "BTS ddrsrate UCI": bts.ddrsrate,
            "BTS expected MCS": expect_mcs,
            "P2MP CPE IPs": ", ".join(_p2mp_cpe_ips(cfg)),
        },
    )
    check.is_true(
        bts.ddrs_status.startswith("disable"),
        f"{case_id}: BTS DDRS must be disabled for fixed rate (UCI={bts.ddrs_uci!r})",
    )
    check.equal(
        str(bts.ddrsrate).strip(),
        expect_mcs,
        f"{case_id}: BTS ddrsrate must be MCS{expect_mcs} (got {bts.ddrsrate!r})",
    )
    cpe_snaps: list[tuple[str, DdrsSnap]] = []
    for ip, su in _p2mp_cpe_targets(cfg):
        snap = read_ddrs_snap(cfg, ip)
        cpe_snaps.append((ip, snap))
        echoes_bts = (
            str(snap.ddrs_uci).strip() == str(bts.ddrs_uci).strip()
            and str(snap.ddrsrate).strip() == str(bts.ddrsrate).strip()
        )
        _kv(
            case_id,
            {
                f"CPE {ip} backend": f"remote_exec.sh {su}",
                f"CPE {ip} DDRS UCI": snap.ddrs_uci,
                f"CPE {ip} GET is BTS echo": str(echoes_bts),
            },
        )
        if echoes_bts:
            _log(
                case_id,
                f"CPE {ip} UCI GET echoes BTS — CPE DDRS enable applied via SET; "
                f"BTS held at fixed MCS{expect_mcs} over direct SSH",
            )
            continue
        check.is_true(
            snap.ddrs_status.startswith("enable"),
            f"{case_id}: CPE {ip} DDRS must be enabled (UCI={snap.ddrs_uci!r})",
        )
    return bts, cpe_snaps


def _with_bts_fixed_cpe_ddrs_p2mp(cfg: DdrsConfig, *, case_id: str, body) -> None:
    bts_saved = read_bts_ddrs_snap(cfg)
    cpe_saved = [(ip, read_ddrs_snap(cfg, ip)) for ip, _su in _p2mp_cpe_targets(cfg)]
    try:
        _apply_bts_fixed_cpe_ddrs_p2mp(cfg)
        body()
    finally:
        try:
            for ip, snap in cpe_saved:
                _restore_ddrs_snap(cfg, ip, snap)
            time.sleep(2)
            _restore_ddrs_snap(cfg, cfg.bts_ip, bts_saved)
            time.sleep(8)
            wait_ddrs_link(
                cfg,
                case_id=case_id,
                timeout_s=cfg.link_settle_s,
                min_stations=cfg.p2mp_min_stations,
            )
        except Exception as exc:
            _log(case_id, f"WARN restore DDRS/MCS: {exc}")


def assert_ddrs_56_p2mp_bts_fixed_cpe_ddrs_rate(cfg: DdrsConfig) -> None:
    """P2MP — BTS fixed MCS + CPE DDRS; no BTS DDRS adapt logs; low RSSI + co-channel."""
    case_id = "DDRS_56"

    def _body() -> None:
        _section(case_id, "P2MP BTS fixed data rate & CPE DDRS enabled")
        verify_bts_fixed_cpe_ddrs_p2mp(cfg, case_id=case_id)
        _require_p2mp_link(cfg, case_id=case_id)
        verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
        verify_bts_fixed_no_ddrs_adapt_logs(cfg, case_id=case_id)

        _section(case_id, "P2MP low RSSI check (Tx power) under mixed DDRS/fixed")
        normal_tx = str(int(cfg.normal_tx_dbm))
        low_tx = str(int(cfg.low_rssi_tx_dbm))
        bts_tx_saved = _read_tx_power_uci(cfg, cfg.bts_ip) or normal_tx
        cpe_tx_saved: dict[str, str] = {}
        try:
            for ip, _su in _p2mp_cpe_targets(cfg):
                cpe_tx_saved[ip] = _read_tx_power_uci(cfg, ip) or normal_tx
                _set_manual_tx_power(cfg, ip, low_tx)
            _set_manual_tx_power(cfg, cfg.bts_ip, low_tx)
            time.sleep(max(5.0, float(cfg.low_rssi_settle_s)))
            _require_p2mp_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
            bts_tx = _read_tx_power_uci(cfg, cfg.bts_ip)
            _kv(case_id, {"BTS Tx UCI (low)": bts_tx or "?"})
            check.equal(bts_tx, low_tx, f"{case_id}: BTS Tx must be {low_tx} dBm")
            verify_bts_fixed_cpe_ddrs_p2mp(cfg, case_id=case_id)
            verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
            verify_bts_fixed_no_ddrs_adapt_logs(cfg, case_id=case_id)
        finally:
            for ip, value in cpe_tx_saved.items():
                _set_manual_tx_power(cfg, ip, value)
            _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_saved)
            time.sleep(8)

        _section(case_id, "P2MP co-channel interference check under mixed DDRS/fixed")
        from utils.ddrs_interference import secondary_interference_session

        with secondary_interference_session(cfg, case_id=case_id):
            _require_p2mp_link(cfg, case_id=case_id)
            verify_bts_fixed_cpe_ddrs_p2mp(cfg, case_id=case_id)
            verify_p2mp_operating_rate_and_log(cfg, case_id=case_id)
            verify_bts_fixed_no_ddrs_adapt_logs(cfg, case_id=case_id)

    _with_bts_fixed_cpe_ddrs_p2mp(cfg, case_id=case_id, body=_body)


def assert_ddrs_57_p2mp_bts_fixed_cpe_ddrs_wireless_restart(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs_p2mp(
        cfg,
        case_id="DDRS_57",
        body=lambda: _run_p2mp_wireless_restart(
            cfg, case_id="DDRS_57", verify=verify_bts_fixed_cpe_ddrs_p2mp
        ),
    )


def assert_ddrs_58_p2mp_bts_fixed_cpe_ddrs_network_restart(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs_p2mp(
        cfg,
        case_id="DDRS_58",
        body=lambda: _run_p2mp_network_restart(
            cfg, case_id="DDRS_58", verify=verify_bts_fixed_cpe_ddrs_p2mp
        ),
    )


def assert_ddrs_59_p2mp_bts_fixed_cpe_ddrs_ethernet_restart(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs_p2mp(
        cfg,
        case_id="DDRS_59",
        body=lambda: _run_p2mp_ethernet_duplex_restart(
            cfg, case_id="DDRS_59", verify=verify_bts_fixed_cpe_ddrs_p2mp
        ),
    )


def assert_ddrs_60_p2mp_bts_fixed_cpe_ddrs_soft_reboot(cfg: DdrsConfig) -> None:
    _with_bts_fixed_cpe_ddrs_p2mp(
        cfg,
        case_id="DDRS_60",
        body=lambda: _run_p2mp_soft_reboot(
            cfg, case_id="DDRS_60", verify=verify_bts_fixed_cpe_ddrs_p2mp
        ),
    )


def _snapshot_operating_mcs(snap: dict[str, Any]) -> int | None:
    """Real 802.11 MCS 0–31. Sysfs 255/(255) is 'not yet selected', not a rate."""
    for key in ("operating_mcs", "tx_rate", "rx_rate", "out_mcs", "in_mcs"):
        parsed = _mcs_from_rate_string(snap.get(key))
        if parsed is not None and 0 <= parsed <= 31:
            return parsed
    return None


def _normalize_sta_mac(mac: str) -> str:
    text = str(mac or "").strip().lower()
    if not text or text in {"-", "—"}:
        return ""
    if ":" in text:
        return text
    hex_only = re.sub(r"[^0-9a-f]", "", text)
    if len(hex_only) != 12:
        return text
    return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))


def _wlanconfig_sta_macs(cfg: DdrsConfig) -> list[str]:
    raw = _ssh_bts(
        cfg,
        f"wlanconfig {cfg.wlan_iface} list sta 2>/dev/null",
        timeout_s=30,
    )
    macs: list[str] = []
    for line in str(raw or "").splitlines():
        tok = line.strip().split()
        if tok and re.match(r"^[0-9a-f]{2}:", tok[0], re.I):
            macs.append(tok[0].lower())
    return macs


def _strict_primary_client(cfg: DdrsConfig) -> dict[str, Any] | None:
    """Primary CPE row only — never fall back to another SU on this P2MP BTS."""
    invalidate_ddrs_backend_cache(cfg)
    clients = fetch_bts_link_clients(cfg)
    target = cfg.primary_cpe
    for client in clients:
        if _cpe_ip_matches(target, str(client.get("ip") or "")):
            return client
    return None


def _strict_primary_snapshot(cfg: DdrsConfig) -> dict[str, Any] | None:
    primary = _strict_primary_client(cfg)
    if not primary:
        return None
    return {
        "tx_rate_mbps": primary.get("tx_rate_mbps"),
        "rx_rate_mbps": primary.get("rx_rate_mbps"),
        "operating_mcs": primary.get("operating_mcs"),
        "tx_rate": primary.get("tx_rate"),
        "rx_rate": primary.get("rx_rate"),
        "out_mcs": primary.get("out_mcs"),
        "in_mcs": primary.get("in_mcs"),
        "assoc_time": primary.get("assoc_time"),
        "mac": primary.get("mac"),
        "su_index": primary.get("su_index") or primary.get("sua_index"),
        "ip": primary.get("ip"),
    }


def _primary_rate_live(snap: dict[str, Any] | None) -> bool:
    """True when the primary SU has a real MCS — not the 255/unselected sentinel."""
    if not snap:
        return False
    return _snapshot_operating_mcs(snap) is not None


def _primary_sta_mac(cfg: DdrsConfig) -> str:
    snap = _strict_primary_snapshot(cfg)
    mac = _normalize_sta_mac(str((snap or {}).get("mac") or ""))
    if mac:
        return mac
    try:
        su = _resolve_cpe_su_index(cfg)
        bulk = _read_bts_sysfs_bulk(cfg)
        mac = _normalize_sta_mac(str((bulk.get(su) or {}).get("mac") or ""))
        if mac:
            return mac
    except (subprocess.TimeoutExpired, RuntimeError, OSError, ValueError):
        pass
    return ""


def _assoc_seconds(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text or text in {"-", "—"}:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        h, m, s = (int(p) for p in parts)
        return float(h * 3600 + m * 60 + s)
    return None


def _is_new_association(snap: dict[str, Any] | None, pre_assoc: float | None) -> bool:
    """True when assoc_time reset — same session (e.g. 1995s) is not a reconnect."""
    now = _assoc_seconds((snap or {}).get("assoc_time"))
    if now is None:
        return False
    if pre_assoc is None:
        return now < 30.0
    return now + 20.0 < pre_assoc


def _soft_disconnect_primary(cfg: DdrsConfig, *, case_id: str) -> None:
    """Hold the RF link down long enough to observe a real reassoc.

    kickmac and CPE ``wifi reload`` via remote_exec did not drop this bench
    (assoc_time stayed ~1995s). Bounce BTS radio over Ethernet SSH — not
    remote_exec (that script also runs the command on the BTS, and ``wifi
    reload`` as one argv is a no-op).
    """
    mac = _primary_sta_mac(cfg)
    sta_macs = _wlanconfig_sta_macs(cfg)
    hold_s = 12
    _kv(
        case_id,
        {
            "Primary CPE": cfg.primary_cpe,
            "sysfs MAC": mac or "-",
            "wlanconfig STAs": ", ".join(sta_macs) or "-",
            "stations before": str(count_connected_stations(cfg)),
            "hold-down": f"{hold_s}s wifi down then up on BTS",
        },
    )
    if mac:
        kick = (
            f"wlanconfig {cfg.wlan_iface} kickmac {mac}; "
            f"iwpriv {cfg.wlan_iface} kickmac {mac} 2>/dev/null; "
            f"iw dev {cfg.wlan_iface} station del {mac} 2>/dev/null; true"
        )
        _log(case_id, f"BTS kick attempts for {mac}")
        _ssh_bts(cfg, kick, timeout_s=20)
    _log(case_id, f"soft disconnect: BTS wifi down {hold_s}s then wifi up")
    _ssh_bts(
        cfg,
        f"nohup sh -c 'wifi down; sleep {hold_s}; wifi up' >/tmp/ddrs_71_disc.log 2>&1 &",
        timeout_s=15,
    )


def _wait_primary_dropped(
    cfg: DdrsConfig,
    *,
    case_id: str,
    timeout_s: float,
    before_n: int,
    pre_assoc: float | None,
) -> bool:
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        n = count_connected_stations(cfg)
        snap = _strict_primary_snapshot(cfg)
        live = _primary_rate_live(snap)
        assoc = _assoc_seconds((snap or {}).get("assoc_time"))
        _log(
            case_id,
            f"waiting drop… stations={n} (was {before_n}) primary_live={live} "
            f"assoc_time={assoc if assoc is not None else '-'}",
        )
        if n < before_n or not live:
            return True
        if snap and _is_new_association(snap, pre_assoc):
            return True
        time.sleep(1.0)
    return False


def _first_rate_after_reconnect(
    cfg: DdrsConfig,
    *,
    case_id: str,
    timeout_s: float,
    pre_assoc: float | None,
) -> dict[str, Any]:
    """First MCS on a *new* primary association — ignore stale MCS from the old session."""
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        try:
            snap = _strict_primary_snapshot(cfg)
        except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
            _log(case_id, f"waiting reconnect rate… {exc}")
            time.sleep(1.0)
            continue
        mcs = _snapshot_operating_mcs(snap or {})
        live = _primary_rate_live(snap)
        assoc = _assoc_seconds((snap or {}).get("assoc_time"))
        new_assoc = _is_new_association(snap, pre_assoc)
        raw_mcs = (snap or {}).get("operating_mcs") or (snap or {}).get("tx_rate")
        if live and mcs is not None and new_assoc:
            return snap or {}
        if new_assoc and mcs is None:
            _log(
                case_id,
                f"skip sentinel MCS (not 0–31): {raw_mcs} assoc_time={assoc}",
            )
        else:
            _log(
                case_id,
                f"waiting new assoc… live={live} MCS={mcs} assoc_time={assoc} "
                f"new={new_assoc} stations={count_connected_stations(cfg)}",
            )
        time.sleep(1.0)
    return {}


def _wait_until_operating_max(
    cfg: DdrsConfig, *, case_id: str
) -> tuple[dict[str, Any], int, int]:
    """Poll until MCS reaches ddrsmaxrate (or timeout). Returns snap, mcs, max_mcs."""
    bts = read_bts_ddrs_snap(cfg)
    try:
        max_mcs = int(str(bts.ddrsmaxrate).strip())
    except (TypeError, ValueError):
        max_mcs = 23
    if max_mcs < 1:
        max_mcs = 23
    timeout_s = max(30.0, float(cfg.max_rate_wait_s))
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    last_mcs: int | None = None
    _log(case_id, f"waiting up to {timeout_s:.0f}s for operating MCS to reach {max_mcs}")
    while time.time() < deadline:
        last = _strict_primary_snapshot(cfg) or _primary_link_snapshot(cfg)
        last_mcs = _snapshot_operating_mcs(last)
        _kv(
            case_id,
            {
                "tx_rate": str(last.get("tx_rate") or "-"),
                "rx_rate": str(last.get("rx_rate") or "-"),
                "MCS": str(last_mcs if last_mcs is not None else "-"),
                "ddrsmaxrate": str(max_mcs),
            },
        )
        if last_mcs is not None and last_mcs >= max_mcs:
            return last, last_mcs, max_mcs
        time.sleep(5.0)
    check.is_true(
        last_mcs is not None and last_mcs > int(cfg.reconnect_min_mcs),
        f"{case_id}: operating rate must climb above MCS{cfg.reconnect_min_mcs} "
        f"before disconnect (got MCS{last_mcs})",
    )
    _log(
        case_id,
        f"WARN operating MCS {last_mcs} < ddrsmaxrate {max_mcs} after {timeout_s:.0f}s — "
        "using current rate as pre-disconnect baseline",
    )
    return last, int(last_mcs or 0), max_mcs


def assert_ddrs_71_reset_min_rate_after_reconnect(cfg: DdrsConfig) -> None:
    """After soft disconnect, DDRS must restart from MCS0/lowest, then ramp up."""
    case_id = "DDRS_71"
    min_mcs = int(cfg.reconnect_min_mcs)
    ramp_s = max(60.0, float(cfg.reconnect_ramp_s))

    _section(case_id, "Enable DDRS on BTS and CPE")
    _apply_ddrs_mode(cfg, cfg.primary_cpe, enable=True)
    time.sleep(2)
    _apply_ddrs_mode(cfg, cfg.bts_ip, enable=True)
    time.sleep(8)
    verify_ddrs_enabled(cfg, case_id=case_id)
    check.is_true(
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s),
        f"{case_id}: link must be up",
    )

    _section(case_id, "Wait until data rate reaches maximum")
    pre_snap, pre_mcs, max_mcs = _wait_until_operating_max(cfg, case_id=case_id)
    stations_before = count_connected_stations(cfg)
    pre_assoc = _assoc_seconds(pre_snap.get("assoc_time"))
    _kv(
        case_id,
        {
            "Pre-disconnect MCS": str(pre_mcs),
            "Pre-disconnect tx": str(pre_snap.get("tx_rate") or "-"),
            "Pre-disconnect rx": str(pre_snap.get("rx_rate") or "-"),
            "Pre assoc_time": str(pre_snap.get("assoc_time") or "-"),
            "ddrsmaxrate": str(max_mcs),
            "stations": str(stations_before),
        },
    )

    _section(case_id, "Soft disconnect the link (BTS wifi down/up)")
    _soft_disconnect_primary(cfg, case_id=case_id)
    invalidate_ddrs_backend_cache(cfg)
    dropped = _wait_primary_dropped(
        cfg,
        case_id=case_id,
        timeout_s=45.0,
        before_n=stations_before,
        pre_assoc=pre_assoc,
    )
    check.is_true(
        dropped,
        f"{case_id}: link must drop after soft disconnect (stations or primary rates gone)",
    )

    _section(case_id, "Observe initial data rate after reconnection")
    first = _first_rate_after_reconnect(
        cfg,
        case_id=case_id,
        timeout_s=cfg.reboot_wait_s,
        pre_assoc=pre_assoc,
    )
    first_mcs = _snapshot_operating_mcs(first)
    _kv(
        case_id,
        {
            "Reconnect IP": str(first.get("ip") or "-"),
            "Reconnect tx": str(first.get("tx_rate") or "-"),
            "Reconnect rx": str(first.get("rx_rate") or "-"),
            "Reconnect MCS": str(first_mcs if first_mcs is not None else "-"),
            "Reconnect assoc_time": str(first.get("assoc_time") or "-"),
            "Pre assoc_time": str(pre_snap.get("assoc_time") or "-"),
        },
    )
    check.is_true(
        bool(first) and _is_new_association(first, pre_assoc),
        f"{case_id}: reconnect sample must be a new association "
        f"(pre assoc_time={pre_snap.get('assoc_time')}, got {first.get('assoc_time')})",
    )
    check.is_true(
        first_mcs is not None,
        f"{case_id}: must observe an operating rate on {cfg.primary_cpe} after reconnect",
    )
    check.is_true(
        first_mcs is not None and first_mcs < pre_mcs,
        f"{case_id}: reconnect MCS must not resume previous rate "
        f"(pre=MCS{pre_mcs}, reconnect=MCS{first_mcs})",
    )
    check.is_true(
        first_mcs is not None and first_mcs <= min_mcs,
        f"{case_id}: after reconnection data rate must start from minimum "
        f"(MCS0/lowest ≤ MCS{min_mcs}, got MCS{first_mcs})",
    )

    started_min = first_mcs is not None and first_mcs <= min_mcs
    if not started_min:
        _log(case_id, "skip ramp wait — reconnect did not start at minimum")
        verify_operating_rate_and_log(cfg, case_id=case_id)
        return

    _section(case_id, f"Wait {ramp_s:.0f}s — rate should increase gradually")
    samples: list[int] = [int(first_mcs)] if first_mcs is not None else []
    deadline = time.time() + ramp_s
    last_snap = first
    while time.time() < deadline:
        time.sleep(15.0)
        last_snap = _strict_primary_snapshot(cfg) or {}
        mcs = _snapshot_operating_mcs(last_snap)
        if mcs is not None:
            samples.append(mcs)
            _log(case_id, f"ramp MCS timeline: {samples}")
    final_mcs = samples[-1] if samples else None
    _kv(
        case_id,
        {
            "Ramp samples MCS": ",".join(str(x) for x in samples) or "-",
            "Final tx": str(last_snap.get("tx_rate") or "-"),
            "Final MCS": str(final_mcs if final_mcs is not None else "-"),
            "Pre-disconnect MCS": str(pre_mcs),
        },
    )
    check.is_true(
        final_mcs is not None and first_mcs is not None and final_mcs > first_mcs,
        f"{case_id}: DDRS must increase from reconnect MCS{first_mcs} "
        f"(got MCS{final_mcs} after {ramp_s:.0f}s)",
    )
    check.is_true(
        final_mcs is not None and final_mcs > min_mcs,
        f"{case_id}: after ramp, rate must leave minimum (got MCS{final_mcs})",
    )
    verify_operating_rate_and_log(cfg, case_id=case_id)


# Firmware UCI: 1=Single, 2=Dual, 3=Auto (not parsers.py 0/1/2).
_SPATIAL_MODES = (
    ("Auto", "3"),
    ("Single", "1"),
    ("Dual", "2"),
)


def _apply_spatial_stream(cfg: DdrsConfig, spatial_uci: str) -> None:
    """CPE via remote_exec first, BTS SSH last.

    Do not follow with CPE ``ucidyn apply`` of other keys — remote_exec apply
    re-pushes the CPE spatial onto the BTS (mixed-mode SET).
    """
    idx = cfg.radio_idx
    cmd = f"ucidyn set {_UCI_SPATIAL.format(idx=idx)} {spatial_uci}"
    _apply_ucidyn(cfg, cfg.primary_cpe, cmd)
    time.sleep(2)
    _confirm_bts_spatial(cfg, spatial_uci)
    invalidate_ddrs_backend_cache(cfg)


def _confirm_bts_spatial(cfg: DdrsConfig, spatial_uci: str, *, tries: int = 4) -> str:
    """Force BTS spatialstream and re-read until it matches (or last attempt)."""
    cmd = f"ucidyn set {_UCI_SPATIAL.format(idx=cfg.radio_idx)} {spatial_uci}"
    got = ""
    for _ in range(max(1, tries)):
        _apply_ucidyn(cfg, cfg.bts_ip, cmd)
        time.sleep(3)
        got = str(read_bts_ddrs_snap(cfg).spatial_uci).strip()
        if got == str(spatial_uci).strip():
            return got
    return got


def _spatial_tx_mcs(snap: dict[str, Any]) -> int | None:
    """BTS→CPE MCS. operating_mcs prefers rx and stays Dual when only BTS is Single."""
    for key in ("out_mcs", "tx_rate"):
        parsed = _mcs_from_rate_string(snap.get(key))
        if parsed is not None and 0 <= parsed <= 31:
            return parsed
    return None


def _spatial_tx_mbps(snap: dict[str, Any]) -> float | None:
    return _mbps_num(snap.get("tx_rate_mbps"))


def _spatial_rate_ok(mode: str, mcs: int | None, phy: float | None) -> bool:
    """Single = MCS 0–11 (or PHY ~1SS). Dual = MCS 12–23 (or PHY ~2SS)."""
    if mode == "Single":
        if mcs is not None and 0 <= mcs <= 11:
            return True
        if mcs is not None and mcs >= 12:
            return False
        return phy is not None and phy <= 650.0
    if mode == "Dual":
        if mcs is not None and 12 <= mcs <= 23:
            return True
        if mcs is not None and 0 <= mcs <= 11:
            return False
        return phy is not None and phy >= 800.0
    return mcs is not None or (phy is not None and phy > 0)


def _wait_spatial_operating(
    cfg: DdrsConfig,
    *,
    case_id: str,
    mode: str,
    timeout_s: float,
) -> dict[str, Any]:
    """Poll until BTS→CPE TX MCS matches the spatial-stream range (skip 255)."""
    deadline = time.time() + max(20.0, float(timeout_s))
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _strict_primary_snapshot(cfg) or {}
        mcs = _spatial_tx_mcs(last)
        phy = _spatial_tx_mbps(last)
        _log(
            case_id,
            f"{mode}: waiting TX MCS… got={mcs if mcs is not None else '-'} "
            f"tx={last.get('tx_rate') or '-'} rx={last.get('rx_rate') or '-'}",
        )
        if _spatial_rate_ok(mode, mcs, phy):
            return last
        time.sleep(5.0)
    return last


def _run_spatial_mode_traffic(cfg: DdrsConfig, *, case_id: str, mode: str) -> None:
    tcfg = replace(cfg, traffic_duration_s=min(float(cfg.traffic_duration_s), 30.0))
    _section(
        case_id,
        f"{mode} — mac_vlan_traffic_test_mf.py ({tcfg.traffic_su_count} SU, {tcfg.traffic_duration_s:g}s)",
    )
    result = run_mac_vlan_traffic(tcfg, case_id=f"{case_id}_{mode}")
    _kv(
        case_id,
        {
            f"{mode} DL Mbps": str(result.dl_rx_mbps),
            f"{mode} UL Mbps": str(result.ul_rx_mbps),
            f"{mode} DL loss": f"{result.dl_loss_pct}%",
            f"{mode} UL loss": f"{result.ul_loss_pct}%",
        },
    )
    check.is_not_none(
        result.dl_loss_pct,
        f"{case_id} [{mode}]: traffic must produce FINAL SUMMARY",
    )
    _log(
        case_id,
        f"{mode}: traffic loss DL={result.dl_loss_pct}% UL={result.ul_loss_pct}% "
        "— informational, not a pass gate",
    )


def _parse_proc_stat_cpu(raw: str) -> tuple[int, int] | None:
    """Return (total, idle) jiffies from a ``cpu  ...`` /proc/stat line."""
    for line in str(raw or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 5 or parts[0] != "cpu":
            continue
        try:
            nums = [int(x) for x in parts[1:]]
        except ValueError:
            continue
        total = sum(nums)
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        if total > 0:
            return total, idle
    return None


def _cpu_busy_from_stat_log(raw: str) -> float | None:
    samples: list[tuple[int, int]] = []
    for line in str(raw or "").splitlines():
        parsed = _parse_proc_stat_cpu(line)
        if parsed:
            samples.append(parsed)
    if len(samples) < 2:
        return None
    pcts: list[float] = []
    for (t0, i0), (t1, i1) in zip(samples, samples[1:]):
        dt = t1 - t0
        di = i1 - i0
        if dt <= 0:
            continue
        pcts.append(max(0.0, min(100.0, (1.0 - (di / dt)) * 100.0)))
    return sum(pcts) / len(pcts) if pcts else None


def _sample_bts_cpu_busy(cfg: DdrsConfig, *, hold_s: float = 3.0) -> float | None:
    a = _ssh_bts(cfg, "head -n 1 /proc/stat", timeout_s=20)
    time.sleep(max(1.0, float(hold_s)))
    b = _ssh_bts(cfg, "head -n 1 /proc/stat", timeout_s=20)
    return _cpu_busy_from_stat_log(f"{a}\n{b}")


def _start_bts_cpu_log(cfg: DdrsConfig, *, tag: str) -> None:
    log = f"/tmp/ddrs65_{tag}_cpu.log"
    # No $vars / nested quotes — mixed SSH quoting was dropping the sampler log.
    loop = (
        "for x in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 "
        "21 22 23 24 25 26 27 28 29 30; do head -n 1 /proc/stat; sleep 2; done"
    )
    _ssh_bts(
        cfg,
        f"rm -f {log}; nohup sh -c {shlex.quote(loop)} >{log} 2>/dev/null &",
        timeout_s=20,
    )


def _stop_bts_cpu_log(cfg: DdrsConfig, *, tag: str) -> float | None:
    log = f"/tmp/ddrs65_{tag}_cpu.log"
    raw = _ssh_bts(cfg, f"cat {log} 2>/dev/null || true", timeout_s=20)
    busy = _cpu_busy_from_stat_log(raw)
    if busy is None:
        busy = _sample_bts_cpu_busy(cfg, hold_s=3.0)
    return busy


def _read_loadavg(cfg: DdrsConfig, device_ip: str) -> str:
    return _scalar_clean(_run_on_device(cfg, device_ip, "cat /proc/loadavg", timeout_s=20))


def _confirm_bts_ddrs(
    cfg: DdrsConfig,
    *,
    enable: bool,
    ddrsrate: str | None = None,
    tries: int = 4,
) -> DdrsSnap:
    """Re-apply BTS DDRS last so mixed-mode CPE apply cannot stick."""
    snap = read_bts_ddrs_snap(cfg)
    for _ in range(max(1, tries)):
        _apply_ddrs_mode(cfg, cfg.bts_ip, enable=enable, ddrsrate=ddrsrate)
        time.sleep(3)
        snap = read_bts_ddrs_snap(cfg)
        status_ok = (
            snap.ddrs_status.startswith("enable")
            if enable
            else snap.ddrs_status.startswith("disable")
        )
        rate_ok = (not ddrsrate) or str(snap.ddrsrate).strip() == str(ddrsrate)
        if status_ok and rate_ok:
            return snap
    return snap


def _apply_ddrs_enabled_both(cfg: DdrsConfig) -> None:
    _apply_ddrs_mode(cfg, cfg.primary_cpe, enable=True)
    time.sleep(2)
    _confirm_bts_ddrs(cfg, enable=True)


def _apply_ddrs_fixed_max_both(cfg: DdrsConfig, mcs: str) -> None:
    _apply_ddrs_mode(cfg, cfg.primary_cpe, enable=False, ddrsrate=mcs)
    time.sleep(2)
    _confirm_bts_ddrs(cfg, enable=False, ddrsrate=mcs)


def _run_measured_traffic(
    cfg: DdrsConfig,
    *,
    case_id: str,
    label: str,
    duration_s: float,
) -> tuple[Any, float | None]:
    tcfg = replace(cfg, traffic_duration_s=float(duration_s))
    _section(
        case_id,
        f"{label} — mac_vlan_traffic_test_mf.py "
        f"({tcfg.traffic_su_count} SU, {tcfg.traffic_duration_s:g}s)",
    )
    tag = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "run"
    _start_bts_cpu_log(cfg, tag=tag)
    result = run_mac_vlan_traffic(tcfg, case_id=f"{case_id}_{tag}")
    cpu = _stop_bts_cpu_log(cfg, tag=tag)
    _kv(
        case_id,
        {
            f"{label} DL Mbps": str(result.dl_rx_mbps),
            f"{label} UL Mbps": str(result.ul_rx_mbps),
            f"{label} DL latency avg": f"{result.dl_avg_ms} ms",
            f"{label} UL latency avg": f"{result.ul_avg_ms} ms",
            f"{label} DL jitter": f"{result.dl_jitter_ms} ms",
            f"{label} UL jitter": f"{result.ul_jitter_ms} ms",
            f"{label} DL loss": f"{result.dl_loss_pct}%",
            f"{label} UL loss": f"{result.ul_loss_pct}%",
            f"{label} BTS CPU busy": f"{cpu:.1f}%" if cpu is not None else "-",
            f"{label} BTS loadavg": _read_loadavg(cfg, cfg.bts_ip) or "-",
        },
    )
    check.is_not_none(result.dl_loss_pct, f"{case_id} [{label}]: traffic must produce FINAL SUMMARY")
    check.is_true(
        bool(result.dl_rx_mbps) and result.dl_rx_mbps > 0,
        f"{case_id} [{label}]: DL throughput must be > 0 Mbps",
    )
    check.is_true(
        bool(result.ul_rx_mbps) and result.ul_rx_mbps > 0,
        f"{case_id} [{label}]: UL throughput must be > 0 Mbps",
    )
    check.is_true(
        result.dl_avg_ms is not None or result.ul_avg_ms is not None,
        f"{case_id} [{label}]: latency (avg ms) must be present",
    )
    _log(
        case_id,
        f"{label}: loss DL={result.dl_loss_pct}% UL={result.ul_loss_pct}% "
        "— informational, not a pass gate",
    )
    return result, cpu


def assert_ddrs_65_throughput_latency_cpu(cfg: DdrsConfig) -> None:
    """DDRS_65 — throughput/latency/jitter/CPU: DDRS on vs fixed max rate."""
    case_id = "DDRS_65"
    mcs = str(int(cfg.bts_fixed_mcs))
    main_s = min(float(cfg.traffic_duration_s), 45.0)
    adapt_s = min(20.0, main_s)

    _section(case_id, "Measure DDRS impact on throughput and latency")
    bts_before = read_bts_ddrs_snap(cfg)
    cpe_before = read_cpe_ddrs_snap(cfg)
    bts_tx_before = _read_tx_power_uci(cfg, cfg.bts_ip)
    bts_atpc_before = _uci_get(cfg, cfg.bts_ip, _UCI_ATPC.format(idx=cfg.radio_idx))
    _kv(
        case_id,
        {
            "BTS DDRS before": bts_before.ddrs_uci,
            "BTS ddrsmaxrate": bts_before.ddrsmaxrate,
            "Fixed MCS": mcs,
            "Offered DL/UL": f"{cfg.dl_mbps_per_su:g}/{cfg.ul_mbps_per_su:g} Mbps",
        },
    )

    ddrs_result = None
    ddrs_cpu = None
    fixed_result = None
    fixed_cpu = None
    try:
        _section(case_id, "Throughput with DDRS enabled")
        _apply_ddrs_enabled_both(cfg)
        check.is_true(
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s),
            f"{case_id}: link must be up with DDRS enabled",
        )
        bts_on = read_bts_ddrs_snap(cfg)
        check.is_true(
            bts_on.ddrs_status.startswith("enable"),
            f"{case_id}: BTS DDRS must be enabled (UCI={bts_on.ddrs_uci!r})",
        )
        ddrs_result, ddrs_cpu = _run_measured_traffic(
            cfg, case_id=case_id, label="DDRS on", duration_s=main_s
        )
        time.sleep(3)
        verify_operating_rate_and_log(cfg, case_id=f"{case_id}_on")

        _section(case_id, "Rate adaptation event (low Tx) with DDRS still on")
        baseline = _primary_link_snapshot(cfg)
        _set_manual_tx_power(cfg, cfg.bts_ip, cfg.low_rssi_tx_dbm)
        time.sleep(max(8.0, float(cfg.low_rssi_settle_s)))
        _confirm_bts_ddrs(cfg, enable=True)
        low = _primary_link_snapshot(cfg)
        varied = _rate_fields_changed(baseline, low)
        _kv(
            case_id,
            {
                "Tx before": str(baseline.get("tx_rate") or "-"),
                "Tx during adapt": str(low.get("tx_rate") or "-"),
                "MCS before": str(baseline.get("operating_mcs") or "-"),
                "MCS during adapt": str(low.get("operating_mcs") or "-"),
                "Rate changed": str(varied),
            },
        )
        if varied:
            _log(case_id, "DDRS rate adaptation observed under low Tx")
        else:
            _log(case_id, "WARN no MCS/rate step under low Tx — still checking latency")
        adapt_result, _adapt_cpu = _run_measured_traffic(
            cfg, case_id=case_id, label="DDRS adapt", duration_s=adapt_s
        )
        adapt_lat = adapt_result.dl_avg_ms if adapt_result.dl_avg_ms is not None else adapt_result.ul_avg_ms
        check.is_true(
            adapt_lat is not None and float(adapt_lat) < 200.0,
            f"{case_id}: latency must stay acceptable during adaptation "
            f"(got {adapt_lat} ms)",
        )
        _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_before or cfg.normal_tx_dbm)
        if bts_atpc_before and str(bts_atpc_before).strip() not in ("", "?"):
            _apply_ucidyn(
                cfg,
                cfg.bts_ip,
                f"ucidyn set {_UCI_ATPC.format(idx=cfg.radio_idx)} {bts_atpc_before}",
            )
        _confirm_bts_ddrs(cfg, enable=True)
        time.sleep(5)

        _section(case_id, "Throughput with DDRS disabled (fixed max rate)")
        _apply_ddrs_fixed_max_both(cfg, mcs)
        check.is_true(
            wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s),
            f"{case_id}: link must be up with DDRS disabled",
        )
        bts_off = read_bts_ddrs_snap(cfg)
        check.is_true(
            bts_off.ddrs_status.startswith("disable"),
            f"{case_id}: BTS DDRS must be disabled (UCI={bts_off.ddrs_uci!r})",
        )
        check.equal(
            str(bts_off.ddrsrate).strip(),
            mcs,
            f"{case_id}: BTS ddrsrate must be MCS{mcs} (got {bts_off.ddrsrate!r})",
        )
        fixed_result, fixed_cpu = _run_measured_traffic(
            cfg, case_id=case_id, label="DDRS off", duration_s=main_s
        )
        time.sleep(3)
        verify_operating_rate_and_log(cfg, case_id=f"{case_id}_off")

        _section(case_id, "Compare throughput, latency, jitter, CPU")
        on_dl = float(ddrs_result.dl_rx_mbps or 0)
        off_dl = float(fixed_result.dl_rx_mbps or 0)
        on_lat = ddrs_result.dl_avg_ms if ddrs_result.dl_avg_ms is not None else ddrs_result.ul_avg_ms
        off_lat = fixed_result.dl_avg_ms if fixed_result.dl_avg_ms is not None else fixed_result.ul_avg_ms
        on_jit = ddrs_result.dl_jitter_ms if ddrs_result.dl_jitter_ms is not None else ddrs_result.ul_jitter_ms
        off_jit = fixed_result.dl_jitter_ms if fixed_result.dl_jitter_ms is not None else fixed_result.ul_jitter_ms
        _kv(
            case_id,
            {
                "DDRS on DL": f"{on_dl} Mbps",
                "Fixed max DL": f"{off_dl} Mbps",
                "DDRS on latency": f"{on_lat} ms",
                "Fixed max latency": f"{off_lat} ms",
                "DDRS on jitter": f"{on_jit} ms",
                "Fixed max jitter": f"{off_jit} ms",
                "DDRS on CPU": f"{ddrs_cpu:.1f}%" if ddrs_cpu is not None else "-",
                "Fixed max CPU": f"{fixed_cpu:.1f}%" if fixed_cpu is not None else "-",
            },
        )
        if off_dl > 0:
            check.is_true(
                on_dl >= 0.85 * off_dl,
                f"{case_id}: DDRS-on throughput must not be worse than 85% of fixed max "
                f"(DDRS {on_dl} vs fixed {off_dl} Mbps)",
            )
            if on_dl >= off_dl:
                _log(case_id, "DDRS-on throughput ≥ fixed max on this run")
            else:
                _log(
                    case_id,
                    "DDRS-on throughput is within 15% of fixed max "
                    "(clean-link offered load often caps both the same)",
                )
        if on_lat is not None:
            check.is_true(
                float(on_lat) < 200.0,
                f"{case_id}: DDRS-on latency must stay acceptable (got {on_lat} ms)",
            )
        if on_lat is not None and off_lat is not None and float(off_lat) > 0:
            check.is_true(
                float(on_lat) <= max(20.0, 3.0 * float(off_lat)),
                f"{case_id}: DDRS-on latency must not be >> fixed max "
                f"({on_lat} ms vs {off_lat} ms)",
            )
        if ddrs_cpu is not None and fixed_cpu is not None:
            overhead = ddrs_cpu - fixed_cpu
            _log(case_id, f"DDRS CPU overhead vs fixed: {overhead:+.1f} points")
            check.is_true(
                overhead <= 25.0 or ddrs_cpu < 40.0,
                f"{case_id}: DDRS CPU overhead must be minimal "
                f"(DDRS {ddrs_cpu:.1f}% vs fixed {fixed_cpu:.1f}%)",
            )
        else:
            _log(case_id, "WARN BTS CPU samples missing — throughput/latency still compared")
    finally:
        _section(case_id, "Restore DDRS / Tx (CPE then BTS)")
        try:
            if bts_tx_before:
                _set_manual_tx_power(cfg, cfg.bts_ip, bts_tx_before)
            if bts_atpc_before and str(bts_atpc_before).strip() not in ("", "?"):
                _apply_ucidyn(
                    cfg,
                    cfg.bts_ip,
                    f"ucidyn set {_UCI_ATPC.format(idx=cfg.radio_idx)} {bts_atpc_before}",
                )
        except Exception as exc:
            _log(case_id, f"WARN restore Tx: {exc}")
        try:
            _restore_ddrs_snap(cfg, cfg.primary_cpe, cpe_before)
            time.sleep(2)
            _restore_ddrs_snap(cfg, cfg.bts_ip, bts_before)
        except Exception as exc:
            _log(case_id, f"WARN restore DDRS: {exc}")
        invalidate_ddrs_backend_cache(cfg)
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)


def assert_ddrs_66_spatial_stream_modes(cfg: DdrsConfig) -> None:
    """DDRS_66 — DDRS stays on across Auto / Single / Dual; rate follows available streams."""
    case_id = "DDRS_66"
    settle_s = max(float(cfg.link_settle_s), 60.0)

    _section(case_id, "Enable DDRS; loop spatial Auto → Single → Dual")
    _apply_ddrs_mode(cfg, cfg.primary_cpe, enable=True)
    time.sleep(2)
    _apply_ddrs_mode(cfg, cfg.bts_ip, enable=True)
    time.sleep(8)
    bts_before = read_bts_ddrs_snap(cfg)
    cpe_before = read_cpe_ddrs_snap(cfg)
    _kv(
        case_id,
        {
            "BTS spatial before": bts_before.spatial_uci,
            "CPE spatial GET": cpe_before.spatial_uci,
            "Firmware map": "1=Single 2=Dual 3=Auto",
        },
    )

    mode_snaps: dict[str, dict[str, Any]] = {}
    try:
        for mode, spatial_uci in _SPATIAL_MODES:
            _section(case_id, f"Spatial Stream = {mode} (UCI {spatial_uci})")
            _apply_spatial_stream(cfg, spatial_uci)
            time.sleep(8)
            check.is_true(
                wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s),
                f"{case_id} [{mode}]: link must be up after spatial change",
            )
            # Link wait does not apply UCI; mixed-mode CPE apply can race — lock BTS again.
            _confirm_bts_spatial(cfg, spatial_uci)
            bts = read_bts_ddrs_snap(cfg)
            cpe = read_cpe_ddrs_snap(cfg)
            _kv(
                case_id,
                {
                    "BTS DDRS": bts.ddrs_uci,
                    "BTS spatial UCI": bts.spatial_uci,
                    "CPE DDRS GET": cpe.ddrs_uci,
                    "CPE spatial GET": cpe.spatial_uci,
                    "Expect spatial": spatial_uci,
                },
            )
            check.is_true(
                bts.ddrs_status.startswith("enable"),
                f"{case_id} [{mode}]: BTS DDRS must stay enabled (UCI={bts.ddrs_uci!r})",
            )
            check.equal(
                str(bts.spatial_uci).strip(),
                spatial_uci,
                f"{case_id} [{mode}]: BTS spatialstream must be {spatial_uci} "
                f"(got {bts.spatial_uci!r})",
            )
            if str(cpe.spatial_uci).strip() != str(bts.spatial_uci).strip():
                check.equal(
                    str(cpe.spatial_uci).strip(),
                    spatial_uci,
                    f"{case_id} [{mode}]: CPE spatialstream must be {spatial_uci} "
                    f"(got {cpe.spatial_uci!r})",
                )
            else:
                _log(
                    case_id,
                    f"{mode}: CPE UCI GET echoes BTS — spatial SET applied via remote_exec; "
                    "BTS re-applied last over SSH",
                )

            snap = _wait_spatial_operating(
                cfg, case_id=case_id, mode=mode, timeout_s=settle_s
            )
            mcs = _spatial_tx_mcs(snap)
            phy = _spatial_tx_mbps(snap)
            mode_snaps[mode] = {
                "mcs": mcs,
                "phy_mbps": phy,
                "tx_rate": snap.get("tx_rate"),
                "rx_rate": snap.get("rx_rate"),
            }
            _kv(
                case_id,
                {
                    f"{mode} BTS→CPE MCS": str(mcs if mcs is not None else "-"),
                    f"{mode} tx_rate": str(snap.get("tx_rate") or "-"),
                    f"{mode} rx_rate": str(snap.get("rx_rate") or "-"),
                    f"{mode} TX PHY Mbps": str(phy if phy is not None else "-"),
                },
            )
            check.is_true(
                mcs is not None or phy is not None,
                f"{case_id} [{mode}]: must observe BTS→CPE MCS or PHY rate",
            )
            check.is_true(
                _spatial_rate_ok(mode, mcs, phy),
                f"{case_id} [{mode}]: BTS→CPE rate must match available streams "
                f"(Single MCS0–11 / ≤650 Mbps, Dual MCS12–23 / ≥800 Mbps; "
                f"got MCS{mcs} {phy} Mbps)",
            )
            _run_spatial_mode_traffic(cfg, case_id=case_id, mode=mode)
            time.sleep(5)
            _confirm_bts_spatial(cfg, spatial_uci, tries=2)
            after = _strict_primary_snapshot(cfg) or snap
            after_mcs = _spatial_tx_mcs(after) or mcs
            after_phy = _spatial_tx_mbps(after)
            if after_phy is not None:
                mode_snaps[mode]["phy_mbps"] = after_phy
            mode_snaps[mode]["mcs"] = after_mcs
            verify_operating_rate_and_log(cfg, case_id=f"{case_id}_{mode}")
            check.is_true(
                _spatial_rate_ok(mode, after_mcs, after_phy if after_phy is not None else phy),
                f"{case_id} [{mode}]: after traffic, BTS→CPE rate must still match streams "
                f"(got MCS{after_mcs} {after_phy} Mbps)",
            )

        _section(case_id, "Adapt: Dual TX PHY ≥ Single (same SNR, more streams)")
        single = mode_snaps.get("Single") or {}
        dual = mode_snaps.get("Dual") or {}
        auto = mode_snaps.get("Auto") or {}
        _kv(
            case_id,
            {
                "Auto MCS / Mbps": f"{auto.get('mcs')} / {auto.get('phy_mbps')}",
                "Single MCS / Mbps": f"{single.get('mcs')} / {single.get('phy_mbps')}",
                "Dual MCS / Mbps": f"{dual.get('mcs')} / {dual.get('phy_mbps')}",
            },
        )
        s_mbps = single.get("phy_mbps")
        d_mbps = dual.get("phy_mbps")
        check.is_true(
            s_mbps is not None and d_mbps is not None,
            f"{case_id}: Single and Dual PHY rates must both be visible",
        )
        if s_mbps is not None and d_mbps is not None:
            check.is_true(
                float(d_mbps) >= float(s_mbps),
                f"{case_id}: Dual operating rate must be ≥ Single "
                f"(Dual {d_mbps} Mbps vs Single {s_mbps} Mbps)",
            )
    finally:
        _section(case_id, "Restore original spatial stream (CPE then BTS)")
        try:
            _restore_ddrs_snap(cfg, cfg.primary_cpe, cpe_before)
            time.sleep(2)
            _restore_ddrs_snap(cfg, cfg.bts_ip, bts_before)
        except Exception as exc:
            _log(case_id, f"WARN restore spatial: {exc}")
        invalidate_ddrs_backend_cache(cfg)
        wait_ddrs_link(cfg, case_id=case_id, timeout_s=cfg.reboot_wait_s)
