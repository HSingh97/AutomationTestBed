"""Rx sensitivity decision logic. No radios and no attenuator hardware."""

import json
import subprocess
import sys
from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[2]


def _config(**overrides) -> RxSensitivityConfig:
    base = dict(
        offered_mbps=90.0,
        phy_rate_mbps=150.0,
        start_db=0.0,
        stop_db=30.0,
        step_db=1.0,
        coarse_step_db=2.0,
        seek_rssi_dbm=-40.0,
        per_limit_pct=10.0,
        duration_s=15.0,
        bandwidth="HT20",
        mcs="MCS7",
        direction="downlink",
        load_fraction=0.60,
    )
    base.update(overrides)
    return RxSensitivityConfig(**base)


class _Rig:
    """RSSI falls 1 dB per 1 dB of pad. PER breaks at cliff_db."""

    def __init__(self, *, cliff_db: float = 20.0, blip_db: float | None = None) -> None:
        self.att = 0.0
        self.cliff_db = cliff_db
        self.blip_db = blip_db
        self.visits: dict[float, int] = {}
        self.traffic_calls = 0

    def set_att(self, att_db: float) -> None:
        self.att = float(att_db)

    def read(self) -> ReceiverSnapshot:
        return ReceiverSnapshot(rssi_dbm=-30.0 - self.att, snr_db=40.0 - self.att, tx_mcs="7", rx_mcs="7")

    def traffic(self):
        self.traffic_calls += 1
        self.visits[self.att] = self.visits.get(self.att, 0) + 1
        snap = self.read()
        fail = self.att >= self.cliff_db or (
            self.blip_db is not None and self.att == self.blip_db and self.visits[self.att] == 1
        )
        if fail:
            sample = IperfSample(
                link_up=True, per_pct=22.0, lost_packets=220, total_packets=1000, throughput_mbps=70.0
            )
        else:
            sample = IperfSample(
                link_up=True, per_pct=0.2, lost_packets=2, total_packets=1000, throughput_mbps=90.0
            )
        return sample, snap, snap


def test_offered_udp_is_sixty_percent_of_ht20_mcs7():
    offered, phy = offered_udp_mbps("HT20", "MCS7", spatial_streams=2, load_fraction=0.60)
    assert phy == 172.0
    assert offered == 103.2


def test_parse_iperf3_udp_accepts_both_packet_counter_shapes():
    packets_are_total = json.dumps(
        {"end": {"sum": {"packets": 1000, "lost_packets": 100, "lost_percent": 10.0, "bits_per_second": 50_000_000}}}
    )
    packets_are_received = json.dumps(
        {
            "end": {
                "sum_received": {
                    "packets": 900,
                    "lost_packets": 100,
                    "lost_percent": 10.0,
                    "bits_per_second": 45_000_000,
                }
            }
        }
    )
    total = parse_iperf3_udp("warning: something\n" + packets_are_total)
    received = parse_iperf3_udp(packets_are_received)
    assert total.per_pct == 10.0
    assert total.total_packets == 1000
    assert total.throughput_mbps == 50.0
    assert received.per_pct == 10.0
    assert received.total_packets == 1000
    assert received.throughput_mbps == 45.0


def test_parse_iperf3_udp_reports_a_down_link():
    sample = parse_iperf3_udp('{"error": "unable to connect to server"}')
    assert sample.link_up is False
    assert "unable to connect" in sample.note


def test_sweep_stops_at_the_step_before_ten_percent_per():
    rig = _Rig(cliff_db=20.0)
    report = sweep_rx_sensitivity(
        _config(),
        set_attenuation=rig.set_att,
        read_receiver=rig.read,
        run_traffic=rig.traffic,
    )
    assert report.result.status == "sensitivity_found"
    assert report.result.sensitivity_att_db == 19.0
    assert report.result.sensitivity_rssi_dbm == -49.0
    assert report.result.sensitivity_per_pct == 0.2
    assert report.result.cliff_att_db == 20.0
    assert report.result.cliff_per_pct == 22.0
    assert rig.visits[20.0] == 2
    text = format_summary(report)
    assert "Sensitivity: -49.0 dBm" in text
    assert "Seek landed at 10.0 dB" in text


def test_a_single_per_blip_does_not_end_the_sweep():
    rig = _Rig(cliff_db=12.0, blip_db=5.0)
    report = sweep_rx_sensitivity(
        _config(seek_rssi_dbm=None),
        set_attenuation=rig.set_att,
        read_receiver=rig.read,
        run_traffic=rig.traffic,
    )
    assert report.result.status == "sensitivity_found"
    assert report.result.sensitivity_att_db == 11.0
    assert report.result.sensitivity_rssi_dbm == -41.0


def test_sweep_aborts_when_rssi_ignores_the_attenuator():
    calls = {"traffic": 0}

    def read():
        return ReceiverSnapshot(rssi_dbm=-25.0, snr_db=30.0)

    def traffic():
        calls["traffic"] += 1
        snap = read()
        return IperfSample(link_up=True, per_pct=0.0, lost_packets=0, total_packets=1000, throughput_mbps=90.0), snap, snap

    report = sweep_rx_sensitivity(
        _config(seek_rssi_dbm=-40.0, rssi_stuck_steps=3),
        set_attenuation=lambda _db: None,
        read_receiver=read,
        run_traffic=traffic,
    )
    assert report.result.status == "rssi_not_tracking"
    assert calls["traffic"] == 0


def test_strong_signal_underrun_is_the_generator_not_sensitivity():
    def traffic():
        snap = ReceiverSnapshot(rssi_dbm=-40.0, snr_db=35.0)
        sample = IperfSample(
            link_up=True, per_pct=0.0, lost_packets=0, total_packets=1000, throughput_mbps=10.0
        )
        return sample, snap, snap

    report = sweep_rx_sensitivity(
        _config(seek_rssi_dbm=None, start_db=0.0, stop_db=0.0, offered_mbps=100.0),
        set_attenuation=lambda _db: None,
        read_receiver=lambda: ReceiverSnapshot(rssi_dbm=-40.0),
        run_traffic=traffic,
    )
    assert report.result.status == "generator_limited"
    assert report.result.sensitivity_rssi_dbm is None


def test_report_files_include_the_sensitivity_call(tmp_path):
    rig = _Rig(cliff_db=20.0)
    report = sweep_rx_sensitivity(
        _config(baseline_loss_db=40.0),
        set_attenuation=rig.set_att,
        read_receiver=rig.read,
        run_traffic=rig.traffic,
    )
    csv_path, json_path = write_rx_sensitivity_report(tmp_path, report, "sample")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["result"]["sensitivity_rssi_dbm"] == -49.0
    assert payload["modulation"] == "64-QAM 5/6"
    assert "sensitivity_rssi_dbm" not in csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "att_db" in csv_path.read_text(encoding="utf-8").splitlines()[0]
    fine = [row for row in payload["steps"] if row["phase"] == "fine" and row["att_db"] == 19.0]
    assert fine[0]["total_loss_db"] == 59.0


def test_dry_run_uses_the_bts_and_cpe_addresses():
    completed = subprocess.run(
        [sys.executable, "scripts/rx_sensitivity.py", "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "192.168.2.10" in completed.stdout
    assert "192.168.2.11" in completed.stdout
    assert "103.2 Mbps" in completed.stdout
    assert "PER <= 10%" in completed.stdout
