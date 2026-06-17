from traffic.trex_runner import parse_trex_client_output


SAMPLE_OUTPUT = """
Connecting to BSU server @ 127.0.0.1...

--- Live Stats @ 15:08:45 ---
+--------+---------+---------+--------+--------+
| Device | TX Mbps | RX Mbps | TX pps | RX pps |
+--------+---------+---------+--------+--------+
|    BSU |  401.02 |    4.56 | 33,330 |    379 |
|    SU1 |  401.09 |   24.92 | 33,335 |  2,071 |
+--------+---------+---------+--------+--------+

--- Live Stats @ 15:08:47 ---
+--------+---------+---------+--------+--------+
| Device | TX Mbps | RX Mbps | TX pps | RX pps |
+--------+---------+---------+--------+--------+
|    BSU |  401.17 |    4.73 | 33,341 |    393 |
|    SU1 |  401.07 |   22.73 | 33,334 |  1,889 |
+--------+---------+---------+--------+--------+

Disconnected from BSU server.

Summary for 1500 / BIDI / UDP (from Samples)
+----------+-----------------+-----------------+-----------------+
| Device   |   Avg RX (Mbps) |   Min RX (Mbps) |   Max RX (Mbps) |
+----------+-----------------+-----------------+-----------------+
| BSU      |            9.32 |            4.19 |           11.65 |
| SU1      |           26.42 |           22.18 |           29.49 |
| TOTAL    |           35.73 |             N/A |             N/A |
+----------+-----------------+-----------------+-----------------+

Consolidated Summary (UDP)
+----------+-------------+
| Pkt Size | Bidi (Mbps) |
+----------+-------------+
| 1500     |       35.73 |
+----------+-------------+
""".strip()


SUMMARY_OUTPUT_V2 = """
Summary for 1500 / BIDI / UDP (from Samples)
+----------+-----------------+-----------------+-----------------+-----------------+-----------------+-----------------+
| Device   |   Avg TX (Mbps) |   Min TX (Mbps) |   Max TX (Mbps) |   Avg RX (Mbps) |   Min RX (Mbps) |   Max RX (Mbps) |
+----------+-----------------+-----------------+-----------------+-----------------+-----------------+-----------------+
| BSU      |          600.12 |          598.00 |          602.50 |           48.20 |           45.10 |           51.00 |
| SU1      |           49.80 |           47.50 |           52.10 |          150.30 |          148.00 |          152.00 |
| SU2      |           51.10 |           49.00 |           53.00 |          149.70 |          147.50 |          151.20 |
| TOTAL    |          701.02 |             N/A |             N/A |          348.20 |             N/A |             N/A |
+----------+-----------------+-----------------+-----------------+-----------------+-----------------+-----------------+
""".strip()


def test_parse_trex_client_output_extracts_live_and_summary_metrics():
    parsed = parse_trex_client_output(SAMPLE_OUTPUT)

    assert len(parsed["live_samples"]) == 2
    assert parsed["combined"]["rx_mbps"] == 35.73
    assert parsed["downlink"]["rx_mbps"] == 26.42
    assert parsed["uplink"]["rx_mbps"] == 9.32
    assert parsed["summary_by_device"]["SU1"]["avg_rx_mbps"] == 26.42
    assert parsed["summary_by_device"]["SU1"]["avg_tx_mbps"] == 401.08
    assert parsed["consolidated_summary"][0]["bidi_mbps"] == 35.73


def test_parse_trex_summary_v2_extracts_per_stream_tx_for_uplink():
    parsed = parse_trex_client_output(SUMMARY_OUTPUT_V2)

    assert parsed["summary_by_device"]["SU1"]["avg_rx_mbps"] == 150.3
    assert parsed["summary_by_device"]["SU1"]["avg_tx_mbps"] == 49.8
    assert parsed["summary_by_device"]["SU2"]["avg_tx_mbps"] == 51.1
    assert parsed["downlink"]["rx_mbps"] == 300.0
    assert parsed["uplink"]["tx_mbps"] == 100.9
    assert parsed["uplink"]["rx_mbps"] == 48.2

