"""Unit tests for QoS queue capture parsing (no live TRex/DUT)."""

from __future__ import annotations

from pathlib import Path

from traffic.qos_lab import NAME_TO_QUEUE, parse_queue_capture_csv


def test_parse_queue_capture_csv_summary():
    csv_path = Path("reports/artifacts/sua4_queue_stats_20260723_094403.csv")
    if not csv_path.is_file():
        # Fallback: synthesize a tiny CSV
        csv_path = Path("/tmp/qos_queue_unit.csv")
        csv_path.write_text(
            "timestamp,q0_rx,q0_tx,q1_rx,q1_tx,q2_rx,q2_tx,q3_rx,q3_tx,"
            "q4_rx,q4_tx,q5_rx,q5_tx,q6_rx,q6_tx,q7_rx,q7_tx\n"
            "10:00:00,0,6000000,0,12000000,0,6000000,0,6000000,"
            "0,6000000,0,6000000,0,6000000,0,0\n"
            "10:00:01,0,5000000,0,10000000,0,5000000,0,5000000,"
            "0,5000000,0,5000000,0,4000000,0,0\n"
        )
    result = parse_queue_capture_csv(csv_path)
    assert result.sample_count >= 2
    assert set(result.queues) == set(range(8))
    assert result.queue(7).max_tx_mbps == 0.0 or result.queue(7).avg_tx_mbps == 0.0
    voice = result.by_name("voice")
    assert voice.queue == NAME_TO_QUEUE["voice"] == 1
    assert voice.max_tx_mbps >= voice.avg_tx_mbps


def test_name_to_queue_matches_luci_order():
    assert NAME_TO_QUEUE["control"] == 0
    assert NAME_TO_QUEUE["voice"] == 1
    assert NAME_TO_QUEUE["oam"] == 2
    assert NAME_TO_QUEUE["arvr"] == 3
    assert NAME_TO_QUEUE["gold"] == 4
    assert NAME_TO_QUEUE["bronze"] == 5
    assert NAME_TO_QUEUE["best_effort"] == 6
    assert NAME_TO_QUEUE["unassigned"] == 7
