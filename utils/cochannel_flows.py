"""Co-channel interference matrix — RSSI / OBSS / util + primary MAC/VLAN traffic."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pytest_check as check

from utils.lab_nic_guard import assert_no_lab_nic_down

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / "reports" / "artifacts"

FINAL_SUMMARY_RE = re.compile(
    r"BTS → CPE\s+"
    r"(?P<dl_tx>[\d,]+)\s+(?P<dl_rx>[\d,]+)\s+(?P<dl_drops>[\d,]+)\s+"
    r"(?P<dl_loss>[\d.]+)%\s+"
    r"(?P<dl_tx_mbps>[\d.]+)\s+(?P<dl_rx_mbps>[\d.]+)\s+"
    r"(?P<dl_avg>[\d.]+)\s+(?P<dl_min>[\d.]+)\s+(?P<dl_max>[\d.]+)\s+"
    r"(?P<dl_jitter>[\d.]+)"
    r".*?"
    r"CPE → BTS\s+"
    r"(?P<ul_tx>[\d,]+)\s+(?P<ul_rx>[\d,]+)\s+(?P<ul_drops>[\d,]+)\s+"
    r"(?P<ul_loss>[\d.]+)%\s+"
    r"(?P<ul_tx_mbps>[\d.]+)\s+(?P<ul_rx_mbps>[\d.]+)\s+"
    r"(?P<ul_avg>[\d.]+)\s+(?P<ul_min>[\d.]+)\s+(?P<ul_max>[\d.]+)\s+"
    r"(?P<ul_jitter>[\d.]+)",
    re.S,
)

RSSI_ROW_RE = re.compile(
    r"(?P<ssid>.{1,36}?)\s+"
    r"(?P<bssid>(?:[0-9a-f]{2}:){5}[0-9a-f]{2})\s+"
    r"(?P<chan>\d+)\s+(?P<freq>\d+)\s+(?P<rssi>-?\d+)",
    re.I,
)


@dataclass
class CoChannelConfig:
    lab_pc_ssh: str = "qa-lab-01@10.0.150.116"
    lab_pc_password: str = "senao1234#"
    device_password: str = "Sen@0ubRNwk$"
    primary_bts: str = "192.168.2.20"
    primary_cpes: list[str] = field(
        default_factory=lambda: [f"192.168.2.{i}" for i in range(21, 27)]
    )
    secondary_bts: str = "192.168.3.20"
    secondary_cpe: str = "192.168.3.21"
    wifi_iface: str = "wifi1"
    wlan_iface: str = "ath1"
    mac_vlan_script: str = (
        "/home/qa-lab-01/Desktop/Automation Scripts/mac_vlan_traffic_test_mf.py"
    )
    bts_iface: str = "enp4s0"
    cpe_iface: str = "enx8cae4cdd4da8"
    dl_mbps_per_su: float = 40.0
    ul_mbps_per_su: float = 10.0
    duration_s: float = 180.0
    buffer_s: float = 3.0
    su_counts: list[int] = field(default_factory=list)
    su_auto: bool = True
    su_step: int = 2
    su_start: int = 2
    interference: dict[str, Any] = field(default_factory=dict)
    interference_mode: str = "obss"  # obss | tx_power | throughput(alias of obss)
    secondary_tx_fixed_dbm: int = 1
    secondary_traffic_backend: str = "auto"  # auto | iperf3 | trex
    secondary_throughput_max_mbps: float = 400.0
    secondary_throughput_step_mbps: float = 25.0
    trex: dict[str, Any] = field(default_factory=dict)
    iperf_port: int = 5201
    iperf_port_ul: int = 5202
    rssi_settle_s: float = 12.0
    secondary_tx_power_min_dbm: int = 1
    secondary_tx_power_max_dbm: int = 26
    rf_sample_count: int = 36  # used if interval not set; prefer interval
    rf_sample_interval_s: float = 5.0  # sample OBSS/util/free every N seconds during tput
    rf_settle_before_sample_s: float = 0.0
    results_dir: str = "reports/artifacts"

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "CoChannelConfig":
        cc = dict(profile.get("cochannel") or {})
        duration = float(
            os.environ.get("COCHANNEL_DURATION_S")
            or cc.get("duration_s")
            or 180
        )
        return cls(
            lab_pc_ssh=str(cc.get("lab_pc_ssh") or cls.lab_pc_ssh),
            lab_pc_password=str(cc.get("lab_pc_password") or cls.lab_pc_password),
            device_password=str(cc.get("device_password") or cls.device_password),
            primary_bts=str(cc.get("primary_bts") or cls.primary_bts),
            primary_cpes=[str(x) for x in (cc.get("primary_cpes") or cls().primary_cpes)],
            secondary_bts=str(cc.get("secondary_bts") or cls.secondary_bts),
            secondary_cpe=str(cc.get("secondary_cpe") or cls.secondary_cpe),
            wifi_iface=str(cc.get("wifi_iface") or "wifi1"),
            wlan_iface=str(cc.get("wlan_iface") or "ath1"),
            mac_vlan_script=str(cc.get("mac_vlan_script") or cls.mac_vlan_script),
            bts_iface=str(cc.get("bts_iface") or "enp4s0"),
            cpe_iface=str(cc.get("cpe_iface") or "enx8cae4cdd4da8"),
            dl_mbps_per_su=float(cc.get("dl_mbps_per_su") or 40.0),
            ul_mbps_per_su=float(cc.get("ul_mbps_per_su") or 10.0),
            duration_s=duration,
            buffer_s=float(cc.get("buffer_s") or 3.0),
            su_step=int(cc.get("su_step") or 2),
            su_start=int(cc.get("su_start") or 2),
            su_counts=_parse_su_counts_cfg(cc.get("su_counts")),
            su_auto=_su_counts_is_auto(cc.get("su_counts")),
            interference=dict(cc.get("interference") or {}),
            interference_mode=str(cc.get("interference_mode") or "obss").lower(),
            secondary_tx_fixed_dbm=int(cc.get("secondary_tx_fixed_dbm") or 1),
            secondary_traffic_backend=str(cc.get("secondary_traffic_backend") or "auto").lower(),
            secondary_throughput_max_mbps=float(cc.get("secondary_throughput_max_mbps") or 400.0),
            secondary_throughput_step_mbps=float(cc.get("secondary_throughput_step_mbps") or 25.0),
            trex=dict(cc.get("trex") or profile.get("traffic", {}).get("trex") or {}),
            iperf_port=int(cc.get("iperf_port") or 5201),
            iperf_port_ul=int(cc.get("iperf_port_ul") or 5202),
            rssi_settle_s=float(cc.get("rssi_settle_s") or 12.0),
            secondary_tx_power_min_dbm=int(cc.get("secondary_tx_power_min_dbm") or 1),
            secondary_tx_power_max_dbm=int(cc.get("secondary_tx_power_max_dbm") or 26),
            rf_sample_count=int(cc.get("rf_sample_count") or 36),
            rf_sample_interval_s=float(cc.get("rf_sample_interval_s") or 5.0),
            rf_settle_before_sample_s=float(cc.get("rf_settle_before_sample_s") or 0.0),
            results_dir=str(cc.get("results_dir") or "reports/artifacts"),
        )


def _su_counts_is_auto(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip().lower() in ("auto", "")
    return False


def _parse_su_counts_cfg(raw: Any) -> list[int]:
    if raw is None or _su_counts_is_auto(raw):
        return []
    if isinstance(raw, (list, tuple)):
        return [int(x) for x in raw]
    if isinstance(raw, str):
        return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    return []


def build_su_count_steps(
    connected: int, *, step: int = 2, start: int = 2
) -> list[int]:
    """Build SU steps: start, start+step, … until all connected CPEs are used."""
    connected = int(connected)
    step = max(1, int(step))
    start = max(1, int(start))
    if connected <= 0:
        return []
    if connected < start:
        return [connected]
    out: list[int] = []
    n = start
    while n < connected:
        out.append(n)
        n += step
    if not out or out[-1] != connected:
        out.append(connected)
    return out


def count_bts_connected_stations(cfg: CoChannelConfig) -> int:
    """Count CPE stations associated on the primary BTS VAP."""
    raw = _ssh_device(
        cfg,
        cfg.primary_bts,
        f"wlanconfig {cfg.wlan_iface} list sta 2>/dev/null",
        timeout_s=45,
    )
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
    fallback = _ssh_device(
        cfg,
        cfg.primary_bts,
        f"wlanconfig {cfg.wlan_iface} list 2>/dev/null | "
        r"grep -cE '^[0-9a-f][0-9a-f]:' || echo 0",
        timeout_s=30,
    )
    try:
        return int(str(fallback).strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def resolve_su_counts(cfg: CoChannelConfig) -> list[int]:
    """
    Resolve SU matrix from BTS station count (2, 4, 6, … up to all CPEs).

    Override with COCHANNEL_SU_COUNTS=2,4,8 or profile su_counts: [2,4,6].
    """
    env = os.environ.get("COCHANNEL_SU_COUNTS", "").strip()
    if env:
        counts = [int(x.strip()) for x in env.split(",") if x.strip().isdigit()]
        if counts:
            print(f"[cochannel] SU counts from env: {counts}", flush=True)
            return counts
    if not cfg.su_auto and cfg.su_counts:
        print(f"[cochannel] SU counts from profile: {cfg.su_counts}", flush=True)
        return list(cfg.su_counts)

    connected = count_bts_connected_stations(cfg)
    counts = build_su_count_steps(
        connected, step=cfg.su_step, start=cfg.su_start
    )
    print(
        f"[cochannel] BTS {cfg.primary_bts}: {connected} CPE(s) on "
        f"{cfg.wlan_iface} → SU steps {counts} "
        f"(start={cfg.su_start}, step={cfg.su_step})",
        flush=True,
    )
    check.greater_equal(
        connected,
        cfg.su_start,
        f"Need ≥{cfg.su_start} connected CPEs on BTS; found {connected}",
    )
    if not counts:
        check.fail(f"Could not build SU matrix (connected={connected})")
    return counts


@dataclass
class RfSnap:
    rssi_dbm: int | None
    ssid: str
    bssid: str
    channel: str
    obss_pct: int | None
    util_pct: int | None
    raw_ap: str = ""


@dataclass
class TrafficSnap:
    dl_loss_pct: float | None = None
    dl_latency_ms: float | None = None
    dl_jitter_ms: float | None = None
    ul_loss_pct: float | None = None
    ul_latency_ms: float | None = None
    ul_jitter_ms: float | None = None
    dl_tx_mbps: float | None = None
    dl_rx_mbps: float | None = None
    ul_tx_mbps: float | None = None
    ul_rx_mbps: float | None = None
    raw_tail: str = ""


@dataclass
class MatrixRow:
    interference: str
    su_count: int
    rssi_dbm: int | None
    obss_pct: int | None
    util_pct: int | None
    dl_packet_drop_pct: float | None
    dl_latency_ms: float | None
    dl_jitter_ms: float | None
    ul_packet_drop_pct: float | None
    ul_latency_ms: float | None
    ul_jitter_ms: float | None
    secondary_iperf_mbps: float = 0.0
    secondary_tx_dbm: int | None = None
    free_pct: int | None = None
    phase: str = "traffic"  # baseline | traffic
    # Primary throughput (Mbps) — after / with primary traffic
    dl_tx_mbps: float | None = None
    dl_rx_mbps: float | None = None
    ul_tx_mbps: float | None = None
    ul_rx_mbps: float | None = None
    # RF before primary traffic / after (during avg or post)
    rssi_before_dbm: int | None = None
    obss_before_pct: int | None = None
    util_before_pct: int | None = None
    free_before_pct: int | None = None
    rssi_after_dbm: int | None = None
    obss_after_pct: int | None = None
    util_after_pct: int | None = None
    free_after_pct: int | None = None
    obss_sample_count: int = 0
    util_sample_count: int = 0
    obss_samples_json: str = ""
    util_samples_json: str = ""
    free_samples_json: str = ""
    traffic_series_json: str = ""
    notes: str = ""


def _free_channel_pct(util_pct: int | None) -> int | None:
    if util_pct is None:
        return None
    return max(0, min(100, 100 - int(util_pct)))


@dataclass
class ThroughputResult:
    traffic: TrafficSnap
    obss_avg: float | None = None
    util_avg: float | None = None
    free_avg: float | None = None
    obss_samples: list[int] = field(default_factory=list)
    util_samples: list[int] = field(default_factory=list)
    free_samples: list[int] = field(default_factory=list)
    traffic_series: dict[str, list[float]] = field(default_factory=dict)


def _ssh_lab(cfg: CoChannelConfig, remote_cmd: str, *, timeout_s: int = 120) -> str:
    """Run a shell command on the lab PC. Never downs lab Ethernet NICs."""
    assert_no_lab_nic_down(remote_cmd)
    user_host = cfg.lab_pc_ssh
    pw = cfg.lab_pc_password
    cmd = [
        "sshpass",
        "-p",
        pw,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=15",
        user_host,
        remote_cmd,
    ]
    for attempt in range(2):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            if attempt == 0:
                time.sleep(8)
                continue
            return ""
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if proc.returncode != 0 and not out.strip():
            if attempt == 0:
                time.sleep(5)
                continue
            raise RuntimeError(
                f"lab PC SSH failed (rc={proc.returncode}): {remote_cmd[:200]}"
            )
        return out
    return ""


def _ssh_device(
    cfg: CoChannelConfig,
    device_ip: str,
    device_cmd: str,
    *,
    timeout_s: int = 60,
) -> str:
    """SSH to a DUT via the lab PC hop."""
    inner = (
        f"sshpass -p {shlex.quote(cfg.device_password)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o LogLevel=ERROR -o ConnectTimeout=10 "
        f"root@{shlex.quote(device_ip)} {shlex.quote(device_cmd)}"
    )
    return _ssh_lab(cfg, inner, timeout_s=timeout_s)


def _parse_pct(raw: str, field: str) -> int | None:
    for line in str(raw or "").splitlines():
        if field in line and ":" in line:
            tok = line.split(":")[-1].strip().replace("%", "")
            m = re.search(r"(-?\d+(?:\.\d+)?)", tok)
            if m:
                return int(round(float(m.group(1))))
    return None


def _parse_max_rssi_ap(raw: str) -> tuple[int | None, str, str, str]:
    """Return (rssi, ssid, bssid, channel) for strongest (least-negative) AP row."""
    best: tuple[int, str, str, str] | None = None
    for line in str(raw or "").splitlines():
        if "SSID" in line.upper() and "BSSID" in line.upper():
            continue
        m = RSSI_ROW_RE.search(line)
        if not m:
            continue
        try:
            rssi = int(m.group("rssi"))
        except ValueError:
            continue
        if rssi >= 0:
            continue
        if best is None or rssi > best[0]:
            best = (
                rssi,
                m.group("ssid").strip(),
                m.group("bssid").strip(),
                m.group("chan").strip(),
            )
    if best is None:
        return None, "", "", ""
    return best[0], best[1], best[2], best[3]


def read_rf_snap(cfg: CoChannelConfig) -> RfSnap:
    """Read co-channel RSSI + OBSS + channel util from primary BTS."""
    ap_raw = ""
    util_raw = ""
    for attempt in range(3):
        ap_raw = _ssh_device(
            cfg,
            cfg.primary_bts,
            f"wlanconfig {cfg.wlan_iface} list ap 2>/dev/null",
            timeout_s=60,
        )
        rssi, ssid, bssid, chan = _parse_max_rssi_ap(ap_raw)
        util_raw = _ssh_device(
            cfg,
            cfg.primary_bts,
            f"cfg80211tool {cfg.wifi_iface} g_ch_util_obss; "
            f"cfg80211tool {cfg.wifi_iface} g_chanutil",
            timeout_s=45,
        )
        if str(ap_raw or "").strip() or rssi is not None:
            return RfSnap(
                rssi_dbm=rssi,
                ssid=ssid,
                bssid=bssid,
                channel=chan,
                obss_pct=_parse_pct(util_raw, "g_ch_util_obss"),
                util_pct=_parse_pct(util_raw, "g_chanutil"),
                raw_ap=ap_raw,
            )
        time.sleep(8)
    rssi, ssid, bssid, chan = _parse_max_rssi_ap(ap_raw)
    return RfSnap(
        rssi_dbm=rssi,
        ssid=ssid,
        bssid=bssid,
        channel=chan,
        obss_pct=_parse_pct(util_raw, "g_ch_util_obss"),
        util_pct=_parse_pct(util_raw, "g_chanutil"),
        raw_ap=ap_raw,
    )


# Active secondary load (Mbps) after interference tuning — used in matrix notes.
_active_secondary_mbps: float = 0.0


def stop_secondary_iperf(cfg: CoChannelConfig) -> None:
    """Best-effort stop secondary load (TRex master_script and/or iperf3)."""
    global _active_secondary_mbps
    _active_secondary_mbps = 0.0
    backend = _resolve_secondary_backend(cfg)
    if backend == "trex":
        _stop_secondary_trex(cfg)
    for host in (cfg.secondary_bts, cfg.secondary_cpe):
        try:
            _ssh_device(
                cfg,
                host,
                "killall iperf3 2>/dev/null || true; echo OK",
                timeout_s=15,
            )
        except Exception:
            pass


def lock_secondary_tx_power(cfg: CoChannelConfig) -> None:
    """Fix secondary BTS TX power (throughput mode — never tuned)."""
    dbm = int(cfg.secondary_tx_fixed_dbm)
    set_secondary_tx_power(cfg, dbm)


def _resolve_secondary_backend(cfg: CoChannelConfig) -> str:
    backend = str(cfg.secondary_traffic_backend or "auto").lower()
    if backend != "auto":
        return backend
    trex_host = str((cfg.trex or {}).get("host") or "192.168.1.1")
    # Reachability from lab PC (automation host may not route 192.168.1.x)
    try:
        out = _ssh_lab(
            cfg,
            f"ping -c 1 -W 1 {shlex.quote(trex_host)} >/dev/null && echo OK || echo FAIL",
            timeout_s=10,
        )
        if "OK" in out:
            return "trex"
    except Exception:
        pass
    return "iperf3"


def _trex_cfg(cfg: CoChannelConfig) -> dict[str, Any]:
    t = dict(cfg.trex or {})
    return {
        "host": str(t.get("host") or "192.168.1.1"),
        "user": str(t.get("user") or "root"),
        "password": str(t.get("password") or "ubuntu"),
        "pythonpath": str(
            t.get("pythonpath")
            or "/opt/v3.06/automation/trex_control_plane/interactive/"
        ),
        "client_script": str(
            t.get("client_script") or "/root/master_script_extended_16SU.py"
        ),
        "su": int(t.get("su") or 1),
        "duration_s": int(t.get("duration_s") or 7200),
    }


def _ssh_trex(cfg: CoChannelConfig, remote_cmd: str, *, timeout_s: int = 60) -> str:
    """SSH to secondary TRex host via lab PC hop."""
    t = _trex_cfg(cfg)
    inner = (
        f"sshpass -p {shlex.quote(t['password'])} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o LogLevel=ERROR -o ConnectTimeout=10 "
        f"{t['user']}@{t['host']} "
        f"{shlex.quote(remote_cmd)}"
    )
    return _ssh_lab(cfg, inner, timeout_s=timeout_s)


def _stop_secondary_trex(cfg: CoChannelConfig) -> None:
    t = _trex_cfg(cfg)
    # pkill may return non-zero when nothing matches — always exit 0 for nested SSH.
    remote = "pkill -9 -f master_script_extended_16SU.py 2>/dev/null; exit 0"
    inner = (
        f"sshpass -p {shlex.quote(t['password'])} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o LogLevel=ERROR -o ConnectTimeout=15 "
        f"{t['user']}@{t['host']} "
        f"{shlex.quote(remote)}"
    )
    try:
        _ssh_lab(cfg, inner, timeout_s=25)
    except Exception as exc:
        print(f"[cochannel] WARN stop secondary TRex: {exc}", flush=True)


def _start_secondary_trex(cfg: CoChannelConfig, mbps: float, *, tx_dbm: int | None = None) -> None:
    """Start long-duration bi-dir TRex load on secondary (master_script @ 192.168.1.1)."""
    global _active_secondary_mbps
    t = _trex_cfg(cfg)
    bw = f"{int(round(mbps))}M"
    tx_note = f", tx={tx_dbm} dBm" if tx_dbm is not None else ""
    print(
        f"[cochannel] secondary TRex load → {bw} bi-dir "
        f"(@{t['host']}, su={t['su']}){tx_note}",
        flush=True,
    )
    _stop_secondary_trex(cfg)
    time.sleep(1)
    # Long duration so load stays up while OBSS is polled / primary traffic runs.
    # --server-bsu must be the TRex box itself (same as manual).
    remote = (
        f"export PYTHONPATH={shlex.quote(t['pythonpath'])}; "
        f"cd /root; "
        f"nohup stdbuf -oL -eL python3 {shlex.quote(t['client_script'])} "
        f"--server-bsu {shlex.quote(t['host'])} "
        f"--su {int(t['su'])} --bw {shlex.quote(bw)} --size 1500 "
        f"--duration {int(t['duration_s'])} --debug "
        f"</dev/null >/tmp/cochannel_secondary_trex.log 2>&1 & echo STARTED"
    )
    try:
        out = _ssh_trex(cfg, remote, timeout_s=30)
        print(f"[cochannel] secondary TRex start: {out.strip()[:80]}", flush=True)
    except Exception as exc:
        print(f"[cochannel] WARN secondary TRex start: {exc}", flush=True)
    _active_secondary_mbps = mbps
    # Give streams a few seconds before first OBSS read
    time.sleep(8)


def start_secondary_throughput(cfg: CoChannelConfig, mbps: float, *, tx_dbm: int | None = None) -> None:
    """Start/adjust secondary UDP load (TRex preferred, else iperf3)."""
    global _active_secondary_mbps
    mbps = max(0.0, min(float(cfg.secondary_throughput_max_mbps), float(mbps)))
    if mbps <= 0:
        stop_secondary_iperf(cfg)
        return

    backend = _resolve_secondary_backend(cfg)
    if backend == "trex":
        _start_secondary_trex(cfg, mbps, tx_dbm=tx_dbm)
        return

    port = int(cfg.iperf_port)
    port_ul = int(cfg.iperf_port_ul)
    cpe = cfg.secondary_cpe
    bts = cfg.secondary_bts
    tx_note = f", tx={tx_dbm} dBm" if tx_dbm is not None else ""
    print(
        f"[cochannel] secondary iperf3 load → {mbps:g} Mbps bi-dir BTS↔CPE{tx_note}",
        flush=True,
    )
    # Ensure servers (best-effort; ignore timeouts — CPE mgmt can stall under RF load)
    try:
        _ssh_device(
            cfg,
            cpe,
            f"pgrep -f 'iperf3 -s -p {port}' >/dev/null || "
            f"(killall iperf3 2>/dev/null; nohup iperf3 -s -p {port} </dev/null >/dev/null 2>&1 &); "
            f"echo OK",
            timeout_s=20,
        )
    except Exception as exc:
        print(f"[cochannel] WARN secondary CPE iperf server: {exc}", flush=True)
    try:
        _ssh_device(
            cfg,
            bts,
            f"pgrep -f 'iperf3 -s -p {port_ul}' >/dev/null || "
            f"(nohup iperf3 -s -p {port_ul} </dev/null >/dev/null 2>&1 &); echo OK",
            timeout_s=20,
        )
    except Exception as exc:
        print(f"[cochannel] WARN secondary BTS iperf server: {exc}", flush=True)
    time.sleep(1)
    # Restart clients only (don't kill CPE server)
    try:
        _ssh_device(
            cfg,
            bts,
            f"pkill -f 'iperf3 -c {cpe}' 2>/dev/null || true; "
            f"nohup iperf3 -c {cpe} -p {port} -u -b {mbps:g}M -t 7200 "
            f"</dev/null >/dev/null 2>&1 & echo OK",
            timeout_s=20,
        )
    except Exception as exc:
        print(f"[cochannel] WARN secondary BTS→CPE client: {exc}", flush=True)
    try:
        _ssh_device(
            cfg,
            cpe,
            f"pkill -f 'iperf3 -c {bts}' 2>/dev/null || true; "
            f"nohup iperf3 -c {bts} -p {port_ul} -u -b {mbps:g}M -t 7200 "
            f"</dev/null >/dev/null 2>&1 & echo OK",
            timeout_s=20,
        )
    except Exception as exc:
        print(f"[cochannel] WARN secondary CPE→BTS client: {exc}", flush=True)
    _active_secondary_mbps = mbps


def set_secondary_tx_power(cfg: CoChannelConfig, dbm: int) -> None:
    """Set secondary BTS TX power (UCI atpcpower + iwconfig)."""
    lo = int(cfg.secondary_tx_power_min_dbm)
    hi = int(cfg.secondary_tx_power_max_dbm)
    dbm = max(lo, min(hi, int(dbm)))
    radio = cfg.wlan_iface  # ath1
    cmd = (
        f"uci set txparam.{radio}.atpcpower={dbm}; uci commit txparam; "
        f"iwconfig {radio} txpower {dbm}; "
        f"iwconfig {radio} 2>/dev/null | grep -i Tx-Power || true"
    )
    out = _ssh_device(cfg, cfg.secondary_bts, cmd, timeout_s=25)
    print(f"[cochannel] secondary TX power → {dbm} dBm ({out.strip()[:80]})", flush=True)


def _rssi_in_band(rssi: int | None, band: dict[str, Any]) -> bool:
    if rssi is None:
        return False
    lo = int(band.get("rssi_min", -100))
    hi = int(band.get("rssi_max", -30))
    return lo <= rssi <= hi


def _obss_in_band(obss: int | None, band: dict[str, Any]) -> bool:
    if obss is None:
        return False
    lo = int(band.get("obss_min", 0))
    hi = int(band.get("obss_max", 100))
    return lo <= obss <= hi


def interference_level_ok(snap: RfSnap, band: dict[str, Any], cfg: CoChannelConfig) -> bool:
    """True when OBSS is in band (preferred) or RSSI if OBSS bands not configured."""
    if band.get("obss_min") is not None or band.get("obss_max") is not None:
        return _obss_in_band(snap.obss_pct, band)
    if bool(band.get("tune_obss")):
        return _obss_in_band(snap.obss_pct, band)
    return _rssi_in_band(snap.rssi_dbm, band)


    if str(cfg.interference_mode or "obss").lower() in {"obss", "throughput"}:
        return _set_interference_level_obss(cfg, level, max_tune_steps=max_tune_steps)
    return _set_interference_level_tx_power(cfg, level, max_tune_steps=max_tune_steps)


def set_interference_level(
    cfg: CoChannelConfig,
    level: str,
    *,
    max_tune_steps: int = 8,
) -> RfSnap:
    """
    Set Low/Mid/High interference.

    Default (obss / throughput): TX power sets RSSI location; secondary load
    tunes OBSS. Legacy tx_power mode keeps RSSI-only tuning.
    """
    mode = str(cfg.interference_mode or "obss").lower()
    if mode in {"obss", "throughput"}:
        return _set_interference_level_obss(cfg, level, max_tune_steps=max_tune_steps)
    return _set_interference_level_tx_power(cfg, level, max_tune_steps=max_tune_steps)


def _set_interference_level_tx_power(
    cfg: CoChannelConfig,
    level: str,
    *,
    max_tune_steps: int = 12,
) -> RfSnap:
    """
    Set Low / Mid / High by secondary BTS TX power only (no iperf).

    Primary must see list-ap RSSI in the configured band.
    """
    level = level.lower().strip()
    bands = cfg.interference or {}
    band = dict(bands.get(level) or {})
    if not band:
        raise ValueError(f"Unknown interference level '{level}'")

    tx = int(band.get("secondary_tx_power_dbm") or cfg.secondary_tx_power_min_dbm)
    tune_tx = bool(band.get("tune_tx_power", True))
    target = int(band.get("rssi_target") or -70)

    print(
        f"[cochannel] Setting interference={level} "
        f"(RSSI target {target} dBm via secondary TX power only — no iperf)",
        flush=True,
    )

    stop_secondary_iperf(cfg)
    set_secondary_tx_power(cfg, tx)
    time.sleep(cfg.rssi_settle_s)
    snap = read_rf_snap(cfg)
    print(
        f"[cochannel] RSSI={snap.rssi_dbm} OBSS={snap.obss_pct}% UTIL={snap.util_pct}% "
        f"(tx={tx} dBm)",
        flush=True,
    )

    if tune_tx:
        for step in range(1, max_tune_steps + 1):
            if _rssi_in_band(snap.rssi_dbm, band):
                break
            if snap.rssi_dbm is None:
                break
            if snap.rssi_dbm < int(band.get("rssi_min", -100)):
                tx = min(cfg.secondary_tx_power_max_dbm, tx + 2)
            elif snap.rssi_dbm > int(band.get("rssi_max", -30)):
                tx = max(cfg.secondary_tx_power_min_dbm, tx - 2)
            else:
                break
            print(f"[cochannel] tune RSSI step {step}: TX → {tx} dBm", flush=True)
            set_secondary_tx_power(cfg, tx)
            time.sleep(cfg.rssi_settle_s)
            snap = read_rf_snap(cfg)
            print(
                f"[cochannel] RSSI={snap.rssi_dbm} OBSS={snap.obss_pct}% UTIL={snap.util_pct}%",
                flush=True,
            )

    ok = _rssi_in_band(snap.rssi_dbm, band)
    check.is_true(
        ok,
        f"{level}: RSSI {snap.rssi_dbm} not in "
        f"[{band.get('rssi_min')},{band.get('rssi_max')}] after TX tune "
        f"(final tx={tx} dBm)",
    )
    print(
        f"[cochannel] {level.upper()} RSSI check: "
        f"{'PASS' if ok else 'FAIL'} (RSSI={snap.rssi_dbm}, tx={tx} dBm)",
        flush=True,
    )
    return snap


def _set_interference_level_obss(
    cfg: CoChannelConfig,
    level: str,
    *,
    max_tune_steps: int = 16,
) -> RfSnap:
    """
    RSSI location via secondary TX power; interference via OBSS from secondary load.

    Low: TX=1, secondary link only (no load required).
    Mid/High: TX=15/26, ramp secondary throughput until OBSS in band.
    """
    level = level.lower().strip()
    bands = cfg.interference or {}
    band = dict(bands.get(level) or {})
    if not band:
        raise ValueError(f"Unknown interference level '{level}'")

    tx = int(band.get("secondary_tx_power_dbm") or cfg.secondary_tx_fixed_dbm or 1)
    need_load = bool(band.get("tune_obss", level != "low"))
    mbps = float(band.get("secondary_iperf_mbps") or 0)
    step = float(cfg.secondary_throughput_step_mbps)
    obss_lo = int(band.get("obss_min", 0))
    obss_hi = int(band.get("obss_max", 100))

    print(
        f"[cochannel] Setting interference={level} "
        f"(TX={tx} dBm for RSSI location; OBSS target [{obss_lo},{obss_hi}]%)",
        flush=True,
    )

    stop_secondary_iperf(cfg)
    set_secondary_tx_power(cfg, tx)
    time.sleep(cfg.rssi_settle_s)

    if need_load and mbps > 0:
        start_secondary_throughput(cfg, mbps, tx_dbm=tx)
        time.sleep(cfg.rssi_settle_s)
    elif need_load:
        # Start from a modest load so OBSS can climb
        mbps = max(step, 25.0)
        start_secondary_throughput(cfg, mbps, tx_dbm=tx)
        time.sleep(cfg.rssi_settle_s)

    snap = read_rf_snap(cfg)
    free = _free_channel_pct(snap.util_pct)
    print(
        f"[cochannel] RSSI={snap.rssi_dbm} OBSS={snap.obss_pct}% UTIL={snap.util_pct}% "
        f"FREE={free}% (load={mbps:g} Mbps, tx={tx} dBm)",
        flush=True,
    )

    if need_load:
        for step_i in range(1, max_tune_steps + 1):
            if _obss_in_band(snap.obss_pct, band):
                break
            if snap.obss_pct is None:
                break
            if snap.obss_pct < obss_lo:
                mbps = min(cfg.secondary_throughput_max_mbps, mbps + step)
            elif snap.obss_pct > obss_hi:
                mbps = max(0.0, mbps - step)
            else:
                break
            print(
                f"[cochannel] tune OBSS step {step_i}: load → {mbps:g} Mbps "
                f"(target [{obss_lo},{obss_hi}])",
                flush=True,
            )
            start_secondary_throughput(cfg, mbps, tx_dbm=tx)
            # Keep polling OBSS while secondary load is up (same as manual)
            settle = max(float(cfg.rssi_settle_s), 20.0)
            deadline = time.time() + settle
            while time.time() < deadline:
                time.sleep(5.0)
                snap = read_rf_snap(cfg)
                free = _free_channel_pct(snap.util_pct)
                print(
                    f"[cochannel] OBSS poll: RSSI={snap.rssi_dbm} "
                    f"OBSS={snap.obss_pct}% UTIL={snap.util_pct}% FREE={free}% "
                    f"(load={mbps:g} Mbps)",
                    flush=True,
                )
                if _obss_in_band(snap.obss_pct, band):
                    break
            if _obss_in_band(snap.obss_pct, band):
                break

    ok = _obss_in_band(snap.obss_pct, band)
    if not ok:
        print(
            f"[cochannel] WARN {level.upper()}: OBSS {snap.obss_pct} not yet in "
            f"[{obss_lo},{obss_hi}] after tune — continuing with achieved RF "
            f"(load={mbps:g} Mbps, tx={tx} dBm, RSSI={snap.rssi_dbm})",
            flush=True,
        )
    print(
        f"[cochannel] {level.upper()} OBSS check: "
        f"{'PASS' if ok else 'WARN'} "
        f"(RSSI={snap.rssi_dbm}, OBSS={snap.obss_pct}%, UTIL={snap.util_pct}%, "
        f"FREE={_free_channel_pct(snap.util_pct)}%, load={mbps:g} Mbps, tx={tx} dBm)",
        flush=True,
    )
    # Persist tuned load for matrix notes
    band["secondary_iperf_mbps"] = mbps
    band["_resolved_tx_dbm"] = tx
    if level in (cfg.interference or {}):
        cfg.interference[level]["secondary_iperf_mbps"] = mbps
        cfg.interference[level]["_resolved_tx_dbm"] = tx
    return snap


def _set_interference_level_throughput(
    cfg: CoChannelConfig,
    level: str,
    *,
    max_tune_steps: int = 16,
) -> RfSnap:
    """Alias — throughput mode now uses OBSS + TX location."""
    return _set_interference_level_obss(cfg, level, max_tune_steps=max_tune_steps)


def parse_traffic_live_series(
    output: str, *, interval_s: float = 5.0
) -> dict[str, list[float]]:
    """Parse periodic LIVE STATS blocks from mac_vlan traffic log."""
    text = output or ""
    series: dict[str, list[float]] = {
        "times_s": [],
        "dl_rx_mbps": [],
        "ul_rx_mbps": [],
        "dl_loss_pct": [],
        "ul_loss_pct": [],
        "dl_latency_ms": [],
        "ul_latency_ms": [],
        "dl_jitter_ms": [],
        "ul_jitter_ms": [],
    }
    parts = re.split(r"(?=LIVE STATS)", text, flags=re.I)
    for part in parts:
        if "LIVE STATS" not in part.upper():
            continue
        m = FINAL_SUMMARY_RE.search(part)
        if not m:
            continue
        g = m.groupdict()
        t_match = re.search(r"@\s*([\d.]+)\s*s", part, re.I)
        t = float(t_match.group(1)) if t_match else len(series["times_s"]) * interval_s
        series["times_s"].append(t)
        series["dl_rx_mbps"].append(float(g["dl_rx_mbps"]))
        series["ul_rx_mbps"].append(float(g["ul_rx_mbps"]))
        series["dl_loss_pct"].append(float(g["dl_loss"]))
        series["ul_loss_pct"].append(float(g["ul_loss"]))
        series["dl_latency_ms"].append(float(g["dl_avg"]))
        series["ul_latency_ms"].append(float(g["ul_avg"]))
        series["dl_jitter_ms"].append(float(g["dl_jitter"]))
        series["ul_jitter_ms"].append(float(g["ul_jitter"]))
    return series if series["times_s"] else {}


def parse_traffic_summary(output: str) -> TrafficSnap:
    text = output or ""
    # Prefer the FINAL SUMMARY block — LIVE STATS earlier in the log can
    # show transient TX/RX skew that recovers by end-of-test.
    anchor = text.rfind("FINAL SUMMARY")
    region = text[anchor:] if anchor >= 0 else text
    matches = list(FINAL_SUMMARY_RE.finditer(region))
    if not matches and anchor >= 0:
        matches = list(FINAL_SUMMARY_RE.finditer(text))
    if not matches:
        return TrafficSnap(raw_tail=text[-4000:])
    m = matches[-1]
    g = m.groupdict()

    def f(name: str) -> float:
        return float(g[name])

    return TrafficSnap(
        dl_loss_pct=f("dl_loss"),
        dl_latency_ms=f("dl_avg"),
        dl_jitter_ms=f("dl_jitter"),
        ul_loss_pct=f("ul_loss"),
        ul_latency_ms=f("ul_avg"),
        ul_jitter_ms=f("ul_jitter"),
        dl_tx_mbps=f("dl_tx_mbps"),
        dl_rx_mbps=f("dl_rx_mbps"),
        ul_tx_mbps=f("ul_tx_mbps"),
        ul_rx_mbps=f("ul_rx_mbps"),
        raw_tail=m.group(0),
    )


def _sample_obss_util_during(
    cfg: CoChannelConfig,
    *,
    duration_s: float,
    interval_s: float = 5.0,
    traffic_proc: subprocess.Popen[str] | None = None,
) -> tuple[float | None, float | None, float | None, list[int], list[int], list[int]]:
    """
    Sample OBSS / channel util / free every interval_s for the full duration.
    Returns averages + raw sample lists.
    """
    interval = max(1.0, float(interval_s))
    n_samples = max(1, int(round(float(duration_s) / interval)))
    obss_s: list[int] = []
    util_s: list[int] = []
    free_s: list[int] = []
    print(
        f"[cochannel] RF sampling every {interval:g}s for {duration_s:g}s "
        f"→ {n_samples} samples (OBSS / util / free)",
        flush=True,
    )
    t0 = time.monotonic()
    for i in range(n_samples):
        if traffic_proc is not None and traffic_proc.poll() is not None and i > 0:
            # Traffic finished early — take one last sample then stop
            pass
        snap = read_rf_snap(cfg)
        free = _free_channel_pct(snap.util_pct)
        if snap.obss_pct is not None:
            obss_s.append(int(snap.obss_pct))
        if snap.util_pct is not None:
            util_s.append(int(snap.util_pct))
        if free is not None:
            free_s.append(int(free))
        print(
            f"[cochannel] RF sample {i + 1}/{n_samples}: "
            f"OBSS={snap.obss_pct}% UTIL={snap.util_pct}% FREE={free}% "
            f"RSSI={snap.rssi_dbm}",
            flush=True,
        )
        if i + 1 >= n_samples:
            break
        if traffic_proc is not None and traffic_proc.poll() is not None:
            break
        # Stay on cadence relative to start
        next_t = t0 + (i + 1) * interval
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _avg(vals: list[int]) -> float | None:
        if not vals:
            return None
        return round(sum(vals) / len(vals), 1)

    obss_avg = _avg(obss_s)
    util_avg = _avg(util_s)
    free_avg = _avg(free_s)
    print(
        f"[cochannel] RF AVG over {len(obss_s)} samples: "
        f"OBSS={obss_avg}% UTIL={util_avg}% FREE={free_avg}%",
        flush=True,
    )
    return obss_avg, util_avg, free_avg, obss_s, util_s, free_s


def _pick_traffic_output(stdout: str, log_text: str) -> tuple[str, str]:
    """Prefer live stdout when it parses; fall back to log file only if needed."""
    stdout = stdout or ""
    log_text = log_text or ""
    if parse_traffic_summary(stdout).dl_loss_pct is not None:
        return stdout, "stdout"
    if parse_traffic_summary(log_text).dl_loss_pct is not None:
        return log_text, "log"
    return (stdout or log_text), "none"


def run_primary_throughput(
    cfg: CoChannelConfig, su_count: int, *, level: str = "run"
) -> ThroughputResult:
    """
    Run mac_vlan traffic on lab PC; sample OBSS/util/free every 5s for full duration.
    """
    level = str(level or "run").lower()
    su_count = int(su_count)
    max_su = max(cfg.su_counts) if cfg.su_counts else count_bts_connected_stations(cfg)
    max_su = max(max_su, su_count)
    if su_count < 1:
        raise ValueError(f"su_count must be ≥1, got {su_count}")
    if su_count > max_su:
        raise ValueError(f"su_count {su_count} exceeds available CPEs ({max_su})")

    dl = cfg.dl_mbps_per_su * su_count
    ul = cfg.ul_mbps_per_su * su_count
    duration = float(cfg.duration_s)
    interval = float(cfg.rf_sample_interval_s or 5.0)
    stats_iv = max(1, int(round(interval)))
    script = cfg.mac_vlan_script
    log_path = f"/tmp/cochannel_{level}_su{su_count}_out.log"
    wrap = f"/tmp/cochannel_{level}_run_su{su_count}.py"

    wrap_script = f"""import runpy
import sys

SCRIPT = {script!r}
SU = {su_count}
sys.argv = [
    SCRIPT,
    "--bts", {cfg.bts_iface!r},
    "--cpe", {cfg.cpe_iface!r},
    "--duration", "{duration:g}",
    "--buffer", "{cfg.buffer_s:g}",
    "--bts-rate-mbps", "{dl:g}",
    "--cpe-rate-mbps", "{ul:g}",
    "--stats-interval", "{stats_iv}",
]
ns = runpy.run_path(SCRIPT, run_name="mac_vlan_coch")
table = list(ns.get("MAC_VLAN_TABLE") or [])
bts = [r for r in table if r[0] == "BTS"][:SU]
cpe = [r for r in table if r[0] == "CPE"][:SU]
patched = bts + cpe
main_fn = ns["main"]
main_fn.__globals__["MAC_VLAN_TABLE"] = patched
ns["MAC_VLAN_TABLE"] = patched
print(f"[cochannel] Running {{SU}} SU  DL={{'{dl:g}'}} UL={{'{ul:g}'}} Mbps  "
      f"duration={{'{duration:g}'}}s  flows={{len(patched)}}")
main_fn()
"""

    blocking = f"""
set -e
WRAP={wrap}
LOG={log_path}
rm -f "$WRAP"
sudo rm -f "$LOG"
cat > "$WRAP" << 'PYEOF'
{wrap_script}PYEOF
test -f "$WRAP"
sudo sh -c "python3 '$WRAP' 2>&1 | tee '$LOG'"
"""
    print(
        f"[cochannel] Primary traffic: {su_count} SU, "
        f"{dl:g}/{ul:g} Mbps, {duration:g}s "
        f"(RF every {interval:g}s for full run) …",
        flush=True,
    )
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
        blocking,
    ]
    traffic_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    settle = max(0.0, float(cfg.rf_settle_before_sample_s or 0.0))
    if settle > 0:
        print(f"[cochannel] Waiting {settle:.0f}s settle before RF samples …", flush=True)
        time.sleep(settle)

    sample_duration = max(interval, duration - settle)
    obss_avg, util_avg, free_avg, obss_s, util_s, free_s = _sample_obss_util_during(
        cfg,
        duration_s=sample_duration,
        interval_s=interval,
        traffic_proc=traffic_proc,
    )

    if traffic_proc is not None:
        stdout_out = ""
        try:
            out_bytes, _ = traffic_proc.communicate(
                timeout=max(60.0, duration + cfg.buffer_s + 120)
            )
            stdout_out = out_bytes or ""
        except subprocess.TimeoutExpired:
            traffic_proc.kill()
            out_bytes, _ = traffic_proc.communicate()
            stdout_out = out_bytes or ""
        log_out = ""
        try:
            log_out = _ssh_lab(cfg, f"cat {log_path} 2>/dev/null || true", timeout_s=60)
        except Exception:
            pass
        out, src = _pick_traffic_output(stdout_out, log_out)
        print(f"[cochannel] Traffic parsed from {src} ({level}/{su_count}SU)", flush=True)

    traffic = parse_traffic_summary(out)
    traffic_series = parse_traffic_live_series(out, interval_s=interval)

    if traffic.dl_loss_pct is None:
        print("[cochannel] WARN: could not parse FINAL SUMMARY; see raw tail", flush=True)
        print((out or "")[-2000:], flush=True)
    else:
        print(
            f"[cochannel] DL loss={traffic.dl_loss_pct}% lat={traffic.dl_latency_ms}ms "
            f"jit={traffic.dl_jitter_ms}ms | "
            f"UL loss={traffic.ul_loss_pct}% lat={traffic.ul_latency_ms}ms "
            f"jit={traffic.ul_jitter_ms}ms",
            flush=True,
        )
    return ThroughputResult(
        traffic=traffic,
        obss_avg=obss_avg,
        util_avg=util_avg,
        free_avg=free_avg,
        obss_samples=obss_s,
        util_samples=util_s,
        free_samples=free_s,
        traffic_series=traffic_series,
    )



CSV_FIELDS = [
    "interference",
    "phase",
    "su_count",
    "secondary_tx_dbm",
    "secondary_iperf_mbps",
    "rssi_before_dbm",
    "obss_before_pct",
    "util_before_pct",
    "free_before_pct",
    "rssi_after_dbm",
    "obss_after_pct",
    "util_after_pct",
    "free_after_pct",
    "rssi_dbm",
    "obss_pct",
    "util_pct",
    "free_pct",
    "dl_tx_mbps",
    "dl_rx_mbps",
    "ul_tx_mbps",
    "ul_rx_mbps",
    "dl_packet_drop_pct",
    "dl_latency_ms",
    "dl_jitter_ms",
    "ul_packet_drop_pct",
    "ul_latency_ms",
    "ul_jitter_ms",
    "obss_sample_count",
    "util_sample_count",
    "obss_samples_json",
    "util_samples_json",
    "free_samples_json",
    "traffic_series_json",
    "notes",
]


def write_matrix_csv(rows: list[MatrixRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row))
    return path


def print_matrix_table(rows: list[MatrixRow]) -> None:
    hdr = (
        f"{'IF':<6} {'PHASE':<8} {'SU':>3} {'TX':>3} "
        f"{'OBSS_B':>6} {'UTIL_B':>6} {'FREE_B':>6} "
        f"{'OBSS_A':>6} {'UTIL_A':>6} {'FREE_A':>6} "
        f"{'DL_rx':>7} {'UL_rx':>7} {'DL%':>6} {'UL%':>6}"
    )
    print("\n" + "=" * len(hdr))
    print("  CO-CHANNEL RESULTS (before / after primary throughput)")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r.interference:<6} {r.phase:<8} {r.su_count:>3} "
            f"{str(r.secondary_tx_dbm if r.secondary_tx_dbm is not None else '—'):>3} "
            f"{str(r.obss_before_pct if r.obss_before_pct is not None else r.obss_pct):>6} "
            f"{str(r.util_before_pct if r.util_before_pct is not None else r.util_pct):>6} "
            f"{str(r.free_before_pct if r.free_before_pct is not None else r.free_pct):>6} "
            f"{str(r.obss_after_pct if r.obss_after_pct is not None else '—'):>6} "
            f"{str(r.util_after_pct if r.util_after_pct is not None else '—'):>6} "
            f"{str(r.free_after_pct if r.free_after_pct is not None else '—'):>6} "
            f"{_fmt(r.dl_rx_mbps):>7} {_fmt(r.ul_rx_mbps):>7} "
            f"{_fmt(r.dl_packet_drop_pct):>6} {_fmt(r.ul_packet_drop_pct):>6}"
        )
    print("=" * len(hdr) + "\n")


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}"


def print_baseline_rf(level: str, snap: RfSnap, *, tx: int, load_mbps: float) -> None:
    free = _free_channel_pct(snap.util_pct)
    print(
        f"[cochannel] BASELINE (no primary throughput) {level.upper()}: "
        f"RSSI={snap.rssi_dbm} dBm  OBSS={snap.obss_pct}%  "
        f"UTIL={snap.util_pct}%  FREE={free}%  "
        f"(secondary TX={tx} dBm, load={load_mbps:g} Mbps)",
        flush=True,
    )


def verify_backend_only(cfg: CoChannelConfig) -> RfSnap:
    """SSH probe: AP RSSI + OBSS + util on primary BTS (no traffic)."""
    snap = read_rf_snap(cfg)
    free = _free_channel_pct(snap.util_pct)
    print(
        f"[cochannel] Backend OK — RSSI={snap.rssi_dbm} dBm "
        f"({snap.ssid} {snap.bssid} ch{snap.channel}) "
        f"OBSS={snap.obss_pct}% UTIL={snap.util_pct}% FREE={free}%",
        flush=True,
    )
    check.is_not_none(snap.rssi_dbm, "wlanconfig list ap returned no RSSI")
    check.is_not_none(snap.obss_pct, "g_ch_util_obss missing")
    check.is_not_none(snap.util_pct, "g_chanutil missing")
    return snap


def run_cochannel_matrix(
    cfg: CoChannelConfig,
    *,
    levels: list[str] | None = None,
    su_counts: list[int] | None = None,
) -> list[MatrixRow]:
    levels = [x.lower() for x in (levels or ["low", "mid", "high"])]
    if su_counts is None:
        su_counts = resolve_su_counts(cfg)
    else:
        su_counts = list(su_counts)
    cfg.su_counts = su_counts
    connected_cpes = count_bts_connected_stations(cfg)
    rows: list[MatrixRow] = []

    try:
        for level in levels:
            band = dict((cfg.interference or {}).get(level) or {})
            snap = set_interference_level(cfg, level)
            # Re-read band after tune (mbps / tx may have been updated)
            band = dict((cfg.interference or {}).get(level) or {})
            tx = int(
                band.get("_resolved_tx_dbm")
                or band.get("secondary_tx_power_dbm")
                or cfg.secondary_tx_fixed_dbm
                or 1
            )
            iperf_mbps = float(
                band.get("secondary_iperf_mbps") or _active_secondary_mbps or 0
            )

            # --- Without primary throughput: report RF snapshot ---
            baseline = read_rf_snap(cfg)
            print_baseline_rf(level, baseline, tx=tx, load_mbps=iperf_mbps)
            base_free = _free_channel_pct(baseline.util_pct)
            rows.append(
                MatrixRow(
                    interference=level,
                    su_count=0,
                    rssi_dbm=baseline.rssi_dbm,
                    obss_pct=baseline.obss_pct,
                    util_pct=baseline.util_pct,
                    free_pct=base_free,
                    secondary_tx_dbm=tx,
                    dl_packet_drop_pct=None,
                    dl_latency_ms=None,
                    dl_jitter_ms=None,
                    ul_packet_drop_pct=None,
                    ul_latency_ms=None,
                    ul_jitter_ms=None,
                    secondary_iperf_mbps=iperf_mbps,
                    phase="baseline",
                    rssi_before_dbm=baseline.rssi_dbm,
                    obss_before_pct=baseline.obss_pct,
                    util_before_pct=baseline.util_pct,
                    free_before_pct=base_free,
                    notes=(
                        f"BEFORE primary throughput;"
                        f"tx={tx};sec_load={iperf_mbps};"
                        f"OBSS={baseline.obss_pct};UTIL={baseline.util_pct};FREE={base_free}"
                    ),
                )
            )

            # --- With primary throughput (3 min default from profile) ---
            for n in su_counts:
                pre = read_rf_snap(cfg)
                pre_free = _free_channel_pct(pre.util_pct)
                print(
                    f"[cochannel] === {level.upper()} / {n} SU (with primary traffic)  "
                    f"BEFORE RSSI={pre.rssi_dbm} OBSS={pre.obss_pct} UTIL={pre.util_pct} "
                    f"FREE={pre_free} ===",
                    flush=True,
                )
                result = run_primary_throughput(cfg, n, level=level)
                traffic = result.traffic
                post = read_rf_snap(cfg)
                obss = result.obss_avg if result.obss_avg is not None else post.obss_pct
                util = result.util_avg if result.util_avg is not None else post.util_pct
                free = (
                    result.free_avg
                    if result.free_avg is not None
                    else _free_channel_pct(
                        int(round(util)) if util is not None else post.util_pct
                    )
                )
                util_i = int(round(util)) if util is not None else None
                obss_i = int(round(obss)) if obss is not None else None
                free_i = int(round(free)) if free is not None else None
                print(
                    f"[cochannel] AFTER {level.upper()}/{n}SU: "
                    f"DL={traffic.dl_rx_mbps} Mbps UL={traffic.ul_rx_mbps} Mbps "
                    f"AVG OBSS={obss_i}% UTIL={util_i}% FREE={free_i}% "
                    f"(n={len(result.obss_samples)}) RSSI={post.rssi_dbm}",
                    flush=True,
                )
                row = MatrixRow(
                    interference=level,
                    su_count=n,
                    rssi_dbm=pre.rssi_dbm,
                    obss_pct=obss_i,
                    util_pct=util_i,
                    free_pct=free_i,
                    secondary_tx_dbm=tx,
                    dl_packet_drop_pct=traffic.dl_loss_pct,
                    dl_latency_ms=traffic.dl_latency_ms,
                    dl_jitter_ms=traffic.dl_jitter_ms,
                    ul_packet_drop_pct=traffic.ul_loss_pct,
                    ul_latency_ms=traffic.ul_latency_ms,
                    ul_jitter_ms=traffic.ul_jitter_ms,
                    secondary_iperf_mbps=iperf_mbps,
                    phase="traffic",
                    dl_tx_mbps=traffic.dl_tx_mbps,
                    dl_rx_mbps=traffic.dl_rx_mbps,
                    ul_tx_mbps=traffic.ul_tx_mbps,
                    ul_rx_mbps=traffic.ul_rx_mbps,
                    rssi_before_dbm=pre.rssi_dbm,
                    obss_before_pct=pre.obss_pct,
                    util_before_pct=pre.util_pct,
                    free_before_pct=pre_free,
                    rssi_after_dbm=post.rssi_dbm,
                    obss_after_pct=obss_i,
                    util_after_pct=util_i,
                    free_after_pct=free_i,
                    obss_sample_count=len(result.obss_samples),
                    util_sample_count=len(result.util_samples),
                    obss_samples_json=json.dumps(result.obss_samples),
                    util_samples_json=json.dumps(result.util_samples),
                    free_samples_json=json.dumps(result.free_samples),
                    traffic_series_json=(
                        json.dumps(result.traffic_series)
                        if result.traffic_series
                        else ""
                    ),
                    notes=(
                        f"AFTER primary throughput;"
                        f"tx={tx};sec_load={iperf_mbps};"
                        f"dl_rx={traffic.dl_rx_mbps};ul_rx={traffic.ul_rx_mbps};"
                        f"avg_obss={obss_i};avg_util={util_i};avg_free={free_i};"
                        f"rf_samples={len(result.obss_samples)};"
                        f"interval={cfg.rf_sample_interval_s}s"
                    ),
                )
                rows.append(row)
                check.is_not_none(
                    traffic.dl_loss_pct,
                    f"{level}/{n}SU: traffic summary parse failed",
                )
    finally:
        stop_secondary_iperf(cfg)
        try:
            set_secondary_tx_power(cfg, cfg.secondary_tx_power_min_dbm)
        except Exception as exc:
            print(f"[cochannel] WARN restore TX failed: {exc}", flush=True)

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO_ROOT / cfg.results_dir
    csv_path = out_dir / f"cochannel_matrix_{ts}.csv"
    write_matrix_csv(rows, csv_path)
    print_matrix_table(rows)
    print(f"[cochannel] CSV → {csv_path}", flush=True)

    try:
        from utils.cochannel_report import write_cochannel_reports

        write_cochannel_reports(
            rows,
            out_dir=out_dir,
            stem=f"cochannel_report_{ts}",
            meta={
                "duration_s": cfg.duration_s,
                "rf_samples": max(
                    1, int(round(cfg.duration_s / max(1.0, cfg.rf_sample_interval_s)))
                ),
                "rf_sample_interval_s": cfg.rf_sample_interval_s,
                "pytest_log": "/tmp/cochannel_full_matrix_final.log",
                "su_counts": cfg.su_counts,
                "connected_cpes": connected_cpes,
                "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "method": (
                    "TX power sets RSSI location; OBSS via secondary load; "
                    "baseline RF without primary traffic; during primary traffic "
                    "sample OBSS/util/free every 5s and report average"
                ),
            },
        )
    except Exception as exc:
        print(f"[cochannel] WARN report generation failed: {exc}", flush=True)

    return rows
