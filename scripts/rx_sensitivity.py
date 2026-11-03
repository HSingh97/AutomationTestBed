#!/usr/bin/env python3
"""Conducted Rx sensitivity for a UBR PtP link.

The CPE is the receiver. UDP runs from the BTS to the CPE while both Vaunix
chains step up. Sensitivity is the last slave RSSI that still has PER <= 10%.

Preflight (no radio changes, no attenuation changes):

  python3 scripts/rx_sensitivity.py --preflight

Sweep (sudo is required for the Vaunix USB brick):

  sudo python3 scripts/rx_sensitivity.py --channel 36 --baseline-loss-db 40

Spot-check the current pad, without moving the attenuator:

  python3 scripts/rx_sensitivity.py --attenuator none
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from instruments.attenuator_snr import AttenuatorSnrController
from pages.commands import RootCommands
from traffic.dut_radio_config import read_running_bandwidth, run_ssh_command
from traffic.kwn_sua_statistics import fetch_kwn_sua_statistics
from traffic.operating_rate_table import (
    mcs_number,
    modulation_scheme,
    normalize_bandwidth,
    normalize_mcs,
    uci_htmode_matches,
    uci_htmode_value,
)
from traffic.rx_sensitivity import (
    IperfSample,
    ReceiverSnapshot,
    RxSensitivityConfig,
    format_summary,
    offered_udp_mbps,
    parse_iperf3_udp,
    sweep_rx_sensitivity,
    write_rx_sensitivity_report,
)
from utils.dfs_lab import channel_from_iwconfig
from utils.profile_manager import load_profile_bundle


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_CLIFF = 2
EXIT_GENERATOR = 3
EXIT_WEAK_START = 4
EXIT_NO_DATA = 5


@dataclass
class Settings:
    bts_ip: str
    cpe_ip: str
    user: str
    password: str
    radio_idx: int
    sua_index: int
    direction: str
    bandwidth: str
    mcs: str
    spatial_streams: int
    channel: int | None
    baseline_loss_db: float
    start_db: float
    stop_db: float
    step_db: float
    coarse_step_db: float
    seek_rssi_dbm: float | None
    per_limit_pct: float
    duration_s: float
    load_fraction: float
    udp_mbps: float | None
    iperf_port: int
    iperf_mode: str
    iperf_server: str
    link_wait_s: float
    apply_radio: bool
    continue_on_mcs_mismatch: bool
    attenuator_mode: str
    backend: str | None
    allow_mock: bool
    dll_path: str | None
    settle_s: float | None
    profile: dict
    output_dir: Path
    dry_run: bool
    preflight_only: bool


def _section(profile: dict, key: str):
    value = profile.get(key)
    return value if isinstance(value, dict) else {}


def _pick(cli_value, profile_value, default):
    if cli_value is not None:
        return cli_value
    if profile_value is not None and profile_value != "":
        return profile_value
    return default


def load_settings(args: argparse.Namespace) -> Settings:
    bundle = load_profile_bundle(
        profile_name=args.profile,
        local_ip=args.bts,
        remote_ip=args.cpe,
        username=args.username,
        password=args.password,
    )
    profile = bundle.active
    dut = profile.get("dut") or {}
    radio = _section(profile, "rx_sensitivity")
    remotes = list(dut.get("remote_ips") or [])
    cpe = str(remotes[0]) if remotes else ""
    if not cpe:
        raise SystemExit("CPE address is missing. Pass --cpe 192.168.2.11.")
    channel = _pick(args.channel, radio.get("channel"), None)
    seek = _pick(args.seek_rssi, radio.get("seek_rssi_dbm"), -40.0)
    if args.no_seek:
        seek = None
    return Settings(
        bts_ip=str(dut.get("local_ip") or ""),
        cpe_ip=cpe,
        user=str(dut.get("username") or "root"),
        password=str(dut.get("password") or ""),
        radio_idx=int(_pick(args.radio_idx, radio.get("radio_idx"), 1)),
        sua_index=int(_pick(args.sua_index, radio.get("sua_index"), 1)),
        direction=str(_pick(args.direction, radio.get("direction"), "downlink")),
        bandwidth=normalize_bandwidth(str(_pick(args.bandwidth, radio.get("bandwidth"), "HT20"))),
        mcs=normalize_mcs(str(_pick(args.mcs, radio.get("mcs"), "MCS7"))),
        spatial_streams=int(_pick(args.spatial_streams, radio.get("spatial_streams"), 2)),
        channel=None if channel in (None, "") else int(channel),
        baseline_loss_db=float(_pick(args.baseline_loss_db, radio.get("baseline_loss_db"), 0.0)),
        start_db=float(_pick(args.start_db, radio.get("start_db"), 0.0)),
        stop_db=float(_pick(args.stop_db, radio.get("stop_db"), 63.0)),
        step_db=float(_pick(args.step_db, radio.get("step_db"), 1.0)),
        coarse_step_db=float(_pick(args.coarse_step_db, radio.get("coarse_step_db"), 2.0)),
        seek_rssi_dbm=None if seek is None else float(seek),
        per_limit_pct=float(_pick(args.per_limit, radio.get("per_limit_pct"), 10.0)),
        duration_s=float(_pick(args.duration, radio.get("duration_s"), 15.0)),
        load_fraction=float(_pick(args.load_fraction, radio.get("load_fraction"), 0.60)),
        udp_mbps=args.udp_mbps,
        iperf_port=int(_pick(args.port, radio.get("iperf_port"), 5201)),
        iperf_mode=str(args.iperf_mode),
        iperf_server=str(args.iperf_server or ""),
        link_wait_s=float(_pick(args.link_wait, radio.get("link_wait_s"), 120.0)),
        apply_radio=not args.no_apply_radio,
        continue_on_mcs_mismatch=bool(args.continue_on_mcs_mismatch),
        attenuator_mode=str(args.attenuator),
        backend=args.backend,
        allow_mock=bool(args.allow_mock or args.backend == "mock"),
        dll_path=args.dll_path,
        settle_s=args.settle,
        profile=profile,
        output_dir=Path(args.output_dir),
        dry_run=bool(args.dry_run),
        preflight_only=bool(args.preflight),
    )


def ssh_capture(ip: str, user: str, password: str, command: str, *, timeout_s: int) -> str:
    try:
        return run_ssh_command(ip, user, password, command, timeout_s=timeout_s)
    except RuntimeError as exc:
        text = str(exc)
        prefix = f"SSH command failed on {ip}: "
        if text.startswith(prefix):
            return text[len(prefix) :]
        return text


def _metric(value: object) -> float | None:
    text = str(value or "").strip()
    if text in {"", "-", "—", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_receiver(settings: Settings) -> ReceiverSnapshot:
    clients = fetch_kwn_sua_statistics(
        settings.bts_ip,
        ssh_user=settings.user,
        ssh_password=settings.password,
        max_sua=max(settings.sua_index, 1),
        cpe_hosts=[settings.cpe_ip],
    )
    client = None
    for item in clients:
        if int(item.get("su_index") or 0) == settings.sua_index:
            client = item
            break
    if client is None and clients:
        client = clients[0]
    if client is None:
        return ReceiverSnapshot()
    if settings.direction == "uplink":
        rssi = _metric(client.get("l_rssi1"))
        snr = _metric(client.get("l_snr1")) or _metric(client.get("combined_local_snr"))
    else:
        rssi = _metric(client.get("r_rssi1"))
        snr = _metric(client.get("r_snr1")) or _metric(client.get("combined_remote_snr"))
    return ReceiverSnapshot(
        rssi_dbm=rssi,
        snr_db=snr,
        tx_mcs=str(client.get("out_mcs") or ""),
        rx_mcs=str(client.get("in_mcs") or ""),
    )


def wait_for_link(settings: Settings) -> bool:
    deadline = time.monotonic() + settings.link_wait_s
    command = f"ping -c 2 -W 2 {shlex.quote(settings.cpe_ip)}"
    while time.monotonic() < deadline:
        output = ssh_capture(
            settings.bts_ip, settings.user, settings.password, command, timeout_s=20
        )
        if "0% packet loss" in output:
            return True
        time.sleep(3)
    return False


def _uci(settings: Settings, host: str, key: str) -> str:
    raw = ssh_capture(
        host,
        settings.user,
        settings.password,
        f"uci get {key}",
        timeout_s=20,
    )
    return raw.strip().strip('"').splitlines()[-1].strip() if raw.strip() else ""


def _lock_command(settings: Settings, *, master: bool) -> str:
    """Set MCS and bandwidth, then one ucidyn apply. Same sequence on BTS and CPE."""
    radio = settings.radio_idx
    rate = mcs_number(settings.mcs)
    htmode = uci_htmode_value(settings.bandwidth)
    commands = [
        f"ucidyn set txparam.ath{radio}.ddrsrate {rate}",
        f"ucidyn set wireless.wifi{radio}.htmode {htmode}",
    ]
    if master and settings.channel is not None:
        commands.append(f"ucidyn set advwireless.ath{radio}.channel {settings.channel}")
        commands.append(f"ucidyn set wireless.wifi{radio}.channel {settings.channel}")
    commands.append("ucidyn apply")
    return "\n".join(commands)


def _radio_lock_matches(settings: Settings, host: str) -> bool:
    radio = settings.radio_idx
    try:
        rate = _uci(settings, host, f"txparam.ath{radio}.ddrsrate")
        htmode = _uci(settings, host, f"wireless.wifi{radio}.htmode")
    except RuntimeError:
        return False
    return rate == str(mcs_number(settings.mcs)) and uci_htmode_matches(settings.bandwidth, htmode)


def _lock_host(settings: Settings, host: str, label: str, *, master: bool) -> None:
    if _radio_lock_matches(settings, host):
        print(f"[rx] {label} {host} already at ddrsrate {mcs_number(settings.mcs)} {settings.bandwidth}")
        return
    print(
        f"[rx] Locking {label} {host}: {settings.mcs} {settings.bandwidth}"
        + (f" channel {settings.channel}" if master and settings.channel is not None else "")
    )
    deadline = time.monotonic() + settings.link_wait_s
    last_error = "no attempt"
    while time.monotonic() < deadline:
        try:
            run_ssh_command(
                host,
                settings.user,
                settings.password,
                _lock_command(settings, master=master),
                timeout_s=120,
            )
        except RuntimeError as exc:
            last_error = str(exc)
            time.sleep(5)
            continue
        if _radio_lock_matches(settings, host):
            print(f"[rx] {label} locked: ddrsrate {mcs_number(settings.mcs)} {settings.bandwidth}")
            return
        last_error = f"{label} UCI did not show the locked MCS after apply"
        time.sleep(3)
    raise RuntimeError(f"Could not lock {label} {host}: {last_error}")


def apply_radio_lock(settings: Settings) -> None:
    """Lock channel (optional), bandwidth, and MCS. Auto-MCS is forced off."""
    _lock_host(settings, settings.bts_ip, "BTS", master=True)
    print("[rx] Waiting for the CPE before locking the slave")
    if not wait_for_link(settings):
        raise RuntimeError(
            f"Link did not return within {settings.link_wait_s:.0f}s after the BTS lock."
        )
    _lock_host(settings, settings.cpe_ip, "CPE", master=False)
    print("[rx] Waiting for the link after the CPE lock")
    if not wait_for_link(settings):
        raise RuntimeError(
            f"Link did not return within {settings.link_wait_s:.0f}s after the CPE lock."
        )
    _verify_running_radio(settings)


def _mcs_matches(reported: str, expected: str) -> bool:
    text = str(reported or "").strip()
    if not text or text in {"-", "—"}:
        return False
    try:
        return mcs_number(text) == mcs_number(expected)
    except ValueError:
        return text == expected


def _verify_running_radio(settings: Settings) -> None:
    deadline = time.monotonic() + min(settings.link_wait_s, 60.0)
    expected_bw = normalize_bandwidth(settings.bandwidth)
    running = None
    while time.monotonic() < deadline:
        running = read_running_bandwidth(
            settings.bts_ip, settings.user, settings.password, settings.radio_idx
        )
        if running == expected_bw:
            break
        time.sleep(3)
    if running != expected_bw:
        raise RuntimeError(
            f"BTS is running {running or 'unknown'} after locking {expected_bw}."
        )
    print(f"[rx] BTS running bandwidth {running}")

    if settings.channel is not None:
        iw = ssh_capture(
            settings.bts_ip,
            settings.user,
            settings.password,
            RootCommands.get_active_channel(settings.radio_idx),
            timeout_s=20,
        )
        active = channel_from_iwconfig(iw)
        if active is not None and active != settings.channel:
            raise RuntimeError(f"BTS channel is {active}, expected {settings.channel}.")
        print(f"[rx] BTS channel {active if active is not None else 'unread'}")

    expected = str(mcs_number(settings.mcs))
    watched = "tx_mcs" if settings.direction == "downlink" else "rx_mcs"
    deadline = time.monotonic() + settings.link_wait_s
    last = ""
    while time.monotonic() < deadline:
        snap = read_receiver(settings)
        last = snap.tx_mcs if settings.direction == "downlink" else snap.rx_mcs
        if _mcs_matches(last, settings.mcs):
            print(f"[rx] Operating {watched} is {last} (locked index {expected})")
            return
        time.sleep(3)
    message = f"Operating {watched} is {last or 'unknown'}, expected {settings.mcs}."
    if settings.continue_on_mcs_mismatch:
        print(f"[rx] WARNING: {message}")
        return
    raise RuntimeError(message)


def start_iperf_server(settings: Settings, host: str) -> None:
    port = settings.iperf_port
    command = (
        "pkill -f 'iperf3 -s' >/dev/null 2>&1 || true; "
        "command -v iperf3 >/dev/null 2>&1 || { echo NO_IPERF; exit 2; }; "
        f"iperf3 -s -p {port} >/tmp/rxsens-iperf3.log 2>&1 & "
        "echo $! >/tmp/rxsens-iperf3.pid; "
        "sleep 0.4; echo SERVER_OK"
    )
    output = ssh_capture(host, settings.user, settings.password, command, timeout_s=20)
    if "NO_IPERF" in output:
        raise RuntimeError(f"iperf3 is not installed on {host}.")
    if "SERVER_OK" not in output:
        raise RuntimeError(f"Could not start iperf3 on {host}: {output[:240]}")
    print(f"[rx] iperf3 server on {host}:{port}")


def stop_iperf_server(settings: Settings, host: str) -> None:
    command = (
        "if [ -f /tmp/rxsens-iperf3.pid ]; then "
        "kill $(cat /tmp/rxsens-iperf3.pid) >/dev/null 2>&1 || true; fi; "
        "pkill -f 'iperf3 -s' >/dev/null 2>&1 || true"
    )
    ssh_capture(host, settings.user, settings.password, command, timeout_s=15)


def _traffic_endpoints(settings: Settings) -> tuple[str, str]:
    """Return (client host, server host) for a radio-local iperf3 flow."""
    if settings.direction == "uplink":
        return settings.cpe_ip, settings.bts_ip
    return settings.bts_ip, settings.cpe_ip


def run_radio_iperf(settings: Settings, offered_mbps: float) -> IperfSample:
    client_host, server_host = _traffic_endpoints(settings)
    if settings.iperf_mode == "external":
        server = settings.iperf_server or server_host
        command = [
            "iperf3",
            "-c",
            server,
            "-u",
            "-b",
            f"{offered_mbps:.1f}M",
            "-t",
            str(max(1, int(round(settings.duration_s)))),
            "-p",
            str(settings.iperf_port),
            "-J",
            "--connect-timeout",
            "5000",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.duration_s + 40,
                check=False,
            )
        except FileNotFoundError:
            return IperfSample(link_up=False, note="iperf3 is not installed on this host")
        return parse_iperf3_udp((completed.stdout or "") + "\n" + (completed.stderr or ""))

    command = (
        f"iperf3 -c {shlex.quote(server_host)} -u -b {offered_mbps:.1f}M "
        f"-t {max(1, int(round(settings.duration_s)))} -p {settings.iperf_port} "
        "-J --connect-timeout 5000 2>&1"
    )
    output = ssh_capture(
        client_host,
        settings.user,
        settings.password,
        command,
        timeout_s=int(settings.duration_s + 45),
    )
    return parse_iperf3_udp(output)


class Pad:
    def set_db(self, _attenuation_db: float) -> None:
        return None

    def close(self) -> None:
        return None

    def restore(self, _attenuation_db: float) -> None:
        return None


class VaunixPad(Pad):
    def __init__(self, settings: Settings) -> None:
        ctrl = AttenuatorSnrController.from_profile(settings.profile)
        if settings.backend:
            ctrl._api._backend = settings.backend
        if settings.dll_path:
            ctrl._api._dll_path = Path(settings.dll_path)
        if settings.settle_s is not None:
            ctrl.settle_seconds = settings.settle_s
        ctrl._api.open()
        if ctrl._api._backend == "mock" and not settings.allow_mock:
            reason = str(getattr(ctrl._api, "last_error", "") or "")
            ctrl._api.close()
            if "No Vaunix LDA" in reason:
                raise RuntimeError(
                    "No Vaunix LDA attenuator is visible on USB. "
                    "Connect both MIMO bricks to this computer and rerun with sudo. "
                    "Until then, use --attenuator manual to step the pad yourself."
                )
            raise RuntimeError(
                "Vaunix hardware did not open"
                + (f" ({reason})" if reason else "")
                + ". Build vendor/vaunix_linux_sdk and run this script with sudo."
            )
        ctrl.open()
        self.ctrl = ctrl
        self.channel = settings.channel
        devices = ctrl._api.list_devices()
        self.chains = min(2, len(devices))
        if self.chains < 1:
            ctrl.close()
            raise RuntimeError("No Vaunix attenuator detected.")
        names = ", ".join(f"id={item.device_id} serial={item.serial} {item.model}" for item in devices)
        print(f"[rx] Attenuators: {names}")
        if self.chains < 2:
            print(
                "[rx] WARNING: only one attenuator is connected. "
                "Both MIMO chains must be padded or the open chain holds the link up."
            )

    def set_db(self, attenuation_db: float) -> None:
        for chain in range(self.chains):
            self.ctrl.set_chain(chain, attenuation_db, channel=self.channel)
        self.ctrl._wait_settle()

    def close(self) -> None:
        self.ctrl.close()

    def restore(self, attenuation_db: float) -> None:
        print(f"[rx] Restoring attenuators to {attenuation_db:.1f} dB")
        self.set_db(attenuation_db)


class ManualPad(Pad):
    def set_db(self, attenuation_db: float) -> None:
        if not sys.stdin.isatty():
            raise RuntimeError("Manual attenuation needs an interactive terminal.")
        input(
            f"\nSet BOTH MIMO attenuator chains to {attenuation_db:.1f} dB, then press Enter. "
        )

    def restore(self, attenuation_db: float) -> None:
        if sys.stdin.isatty():
            input(f"\nRestore BOTH chains to {attenuation_db:.1f} dB, then press Enter. ")


def build_config(settings: Settings) -> RxSensitivityConfig:
    if settings.udp_mbps is not None:
        offered, phy = offered_udp_mbps(
            settings.bandwidth,
            settings.mcs,
            spatial_streams=settings.spatial_streams,
            load_fraction=1.0,
            max_mbps=settings.udp_mbps,
        )
        fraction = offered / phy if phy else settings.load_fraction
    else:
        offered, phy = offered_udp_mbps(
            settings.bandwidth,
            settings.mcs,
            spatial_streams=settings.spatial_streams,
            load_fraction=settings.load_fraction,
        )
        fraction = settings.load_fraction
    if not 0.50 <= fraction <= 0.70:
        print(
            f"[rx] NOTE: offered load is {fraction * 100:.0f}% of the PHY rate. "
            "The sensitivity method uses 50–70%."
        )
    seek = None if settings.attenuator_mode == "none" else settings.seek_rssi_dbm
    stop = settings.start_db if settings.attenuator_mode == "none" else settings.stop_db
    return RxSensitivityConfig(
        offered_mbps=offered,
        phy_rate_mbps=phy,
        start_db=settings.start_db,
        stop_db=stop,
        step_db=settings.step_db,
        coarse_step_db=settings.coarse_step_db,
        seek_rssi_dbm=seek,
        per_limit_pct=settings.per_limit_pct,
        duration_s=settings.duration_s,
        baseline_loss_db=settings.baseline_loss_db,
        bandwidth=settings.bandwidth,
        mcs=settings.mcs,
        direction=settings.direction,
        channel=settings.channel,
        load_fraction=round(fraction, 3),
    )


def plan_text(settings: Settings, config: RxSensitivityConfig) -> str:
    client, server = _traffic_endpoints(settings)
    if settings.iperf_mode == "external":
        flow = f"local iperf3 client -> {settings.iperf_server or server} UDP"
    else:
        flow = f"iperf3 UDP {client} -> {server}"
    receiver = "CPE slave RSSI (r_comb_rssi)" if settings.direction == "downlink" else "BTS master RSSI (comb_rssi)"
    channel = "leave the current channel" if settings.channel is None else f"lock channel {settings.channel}"
    radio = "lock bandwidth and MCS" if settings.apply_radio else "keep the current radio config"
    return "\n".join(
        [
            "Conducted Rx sensitivity",
            f"  Master / TX   {settings.bts_ip}",
            f"  Slave / RX    {settings.cpe_ip}",
            f"  Direction     {settings.direction} ({receiver})",
            f"  Radio         {radio}; {channel}",
            f"  Rate          {settings.mcs} {modulation_scheme(settings.mcs)} "
            f"{settings.bandwidth} x{settings.spatial_streams}",
            f"  PHY           {config.phy_rate_mbps:.1f} Mbps",
            f"  Offered UDP   {config.offered_mbps:.1f} Mbps for {config.duration_s:.0f}s each step",
            f"  Traffic       {flow}",
            f"  Attenuator    {settings.attenuator_mode}, both chains, "
            f"{config.start_db:.0f} to {config.stop_db:.0f} dB in {config.step_db:.0f} dB steps",
            f"  Seek          "
            + (
                "off"
                if config.seek_rssi_dbm is None
                else f"coarse {config.coarse_step_db:.0f} dB until RSSI <= {config.seek_rssi_dbm:.0f} dBm"
            ),
            f"  Fixed loss    {config.baseline_loss_db:.1f} dB (cables and fixed pads; not subtracted from RSSI)",
            f"  Threshold     PER <= {config.per_limit_pct:.0f}%",
            "  Call          lowest slave RSSI on the last passing 1 dB step",
        ]
    )


def open_pad(settings: Settings) -> Pad:
    if settings.attenuator_mode == "vaunix":
        return VaunixPad(settings)
    if settings.attenuator_mode == "manual":
        return ManualPad()
    print("[rx] Attenuator control is off. One measurement at the current pad.")
    return Pad()


def preflight(settings: Settings, config: RxSensitivityConfig) -> int:
    print(plan_text(settings, config))
    print("\n[rx] Preflight")
    ok = True
    for label, host in (("BTS", settings.bts_ip), ("CPE", settings.cpe_ip)):
        output = ssh_capture(host, settings.user, settings.password, "echo RXSENS_OK", timeout_s=15)
        if "RXSENS_OK" in output:
            print(f"  {label} SSH {host}: ok")
        else:
            ok = False
            print(f"  {label} SSH {host}: failed ({output[:160]})")
    if settings.iperf_mode == "radio":
        for label, host in (("BTS", settings.bts_ip), ("CPE", settings.cpe_ip)):
            output = ssh_capture(
                host,
                settings.user,
                settings.password,
                "command -v iperf3 >/dev/null 2>&1 && echo IPERF_OK || echo NO_IPERF",
                timeout_s=15,
            )
            if "IPERF_OK" in output:
                print(f"  {label} iperf3: ok")
            else:
                ok = False
                print(f"  {label} iperf3: missing")
    snap = read_receiver(settings)
    if snap.rssi_dbm is None:
        ok = False
        print("  Slave RSSI: not reported (link down or SUA stats empty)")
    else:
        snr = "-" if snap.snr_db is None else f"{snap.snr_db:.1f} dB"
        print(
            f"  Receiver RSSI: {snap.rssi_dbm:.1f} dBm   SNR {snr}   "
            f"tx MCS {snap.tx_mcs or '-'}   rx MCS {snap.rx_mcs or '-'}"
        )
    if settings.attenuator_mode == "vaunix":
        try:
            pad = VaunixPad(settings)
            pad.close()
            print("  Attenuator: ok")
        except Exception as exc:
            ok = False
            print(f"  Attenuator: {exc}")
    print("\nPreflight " + ("passed." if ok else "failed."))
    return EXIT_OK if ok else EXIT_ERROR


def execute(settings: Settings) -> int:
    config = build_config(settings)
    print(plan_text(settings, config))
    if settings.apply_radio:
        apply_radio_lock(settings)
    elif not wait_for_link(settings):
        raise RuntimeError("BTS cannot ping the CPE. Bring the conducted link up before the sweep.")

    _client_host, server_host = _traffic_endpoints(settings)
    server = server_host
    server_started = False
    pad: Pad | None = None
    try:
        if settings.iperf_mode == "radio":
            start_iperf_server(settings, server)
            server_started = True
        pad = open_pad(settings)

        def run_traffic():
            before = read_receiver(settings)
            sample = run_radio_iperf(settings, config.offered_mbps)
            after = read_receiver(settings)
            return sample, before, after

        report = sweep_rx_sensitivity(
            config,
            set_attenuation=pad.set_db,
            read_receiver=lambda: read_receiver(settings),
            run_traffic=run_traffic,
        )
        if settings.attenuator_mode == "none" and report.result.status == "cliff_not_reached":
            passed = [step for step in report.steps if step.phase == "fine" and step.passed]
            if passed:
                last = passed[-1]
                report.result.note = (
                    f"Spot check only: RSSI {last.rssi_dbm} dBm, PER {last.per_pct}%. "
                    "The attenuator was not moved."
                )
        elif settings.attenuator_mode == "none" and report.result.status == "failed_at_first_step":
            report.result.note = (
                "Spot check only: PER is already above the limit at the current pad."
            )
    finally:
        if pad is not None:
            try:
                pad.restore(settings.start_db)
            finally:
                pad.close()
        if server_started:
            stop_iperf_server(settings, server)

    print()
    print(format_summary(report))
    stem = "rx_sensitivity_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path, json_path = write_rx_sensitivity_report(settings.output_dir, report, stem)
    print(f"\nWrote {csv_path}")
    print(f"Wrote {json_path}")
    return {
        "sensitivity_found": EXIT_OK,
        "cliff_not_reached": EXIT_NO_CLIFF,
        "generator_limited": EXIT_GENERATOR,
        "failed_at_first_step": EXIT_WEAK_START,
    }.get(report.result.status, EXIT_NO_DATA)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Conducted UBR Rx sensitivity (PER <= 10% vs slave RSSI)."
    )
    parser.add_argument("--profile", default="rx_sensitivity")
    parser.add_argument("--bts", default=None, help="Master / transmitter. Default 192.168.2.10.")
    parser.add_argument("--cpe", default=None, help="Slave / receiver. Default 192.168.2.11.")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--direction", choices=("downlink", "uplink"), default=None)
    parser.add_argument("--bandwidth", default=None, help="HT20, HT40, or HT80.")
    parser.add_argument("--mcs", default=None, help="Locked MCS, for example MCS7.")
    parser.add_argument("--spatial-streams", type=int, default=None)
    parser.add_argument("--channel", type=int, default=None, help="Lock this Wi-Fi channel on the BTS.")
    parser.add_argument("--radio-idx", type=int, default=None)
    parser.add_argument("--sua-index", type=int, default=None)
    parser.add_argument("--baseline-loss-db", type=float, default=None, help="Fixed pads plus cables, in dB.")
    parser.add_argument("--start-db", type=float, default=None)
    parser.add_argument("--stop-db", type=float, default=None)
    parser.add_argument("--step-db", type=float, default=None)
    parser.add_argument("--coarse-step-db", type=float, default=None)
    parser.add_argument("--seek-rssi", type=float, default=None, help="Coarse-step until this RSSI, then use 1 dB.")
    parser.add_argument("--no-seek", action="store_true")
    parser.add_argument("--per-limit", type=float, default=None)
    parser.add_argument("--duration", type=float, default=None, help="UDP dwell per 1 dB step, seconds.")
    parser.add_argument("--load-fraction", type=float, default=None, help="Fraction of PHY rate. Method window is 0.50-0.70.")
    parser.add_argument("--udp-mbps", type=float, default=None, help="Override the offered UDP rate.")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--iperf-mode", choices=("radio", "external"), default="radio")
    parser.add_argument(
        "--iperf-server",
        default="",
        help="With --iperf-mode external, UDP server on the slave LAN.",
    )
    parser.add_argument("--link-wait", type=float, default=None)
    parser.add_argument("--no-apply-radio", action="store_true", help="Do not change channel, bandwidth, or MCS.")
    parser.add_argument("--continue-on-mcs-mismatch", action="store_true")
    parser.add_argument("--attenuator", choices=("vaunix", "manual", "none"), default="vaunix")
    parser.add_argument("--backend", default=None, help="Vaunix backend: auto, dll, or mock.")
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--dll-path", default=None)
    parser.add_argument("--settle", type=float, default=None, help="Seconds to wait after each attenuator write.")
    parser.add_argument("--output-dir", default="reports/rx_sensitivity")
    parser.add_argument("--preflight", action="store_true", help="Check SSH, iperf3, RSSI, and the attenuator.")
    parser.add_argument("--dry-run", action="store_true", help="Print the setup and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = load_settings(build_parser().parse_args(argv))
    if not settings.bts_ip:
        raise SystemExit("BTS address is missing. Pass --bts 192.168.2.10.")
    if settings.dry_run:
        print(plan_text(settings, build_config(settings)))
        return EXIT_OK
    if settings.preflight_only:
        return preflight(settings, build_config(settings))
    return execute(settings)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[rx] Interrupted. Restore the attenuator if a sweep was in progress.")
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"\n[rx] {exc}")
        raise SystemExit(EXIT_ERROR) from exc
