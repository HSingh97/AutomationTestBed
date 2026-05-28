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


def test_parse_trex_client_output_extracts_live_and_summary_metrics():
    parsed = parse_trex_client_output(SAMPLE_OUTPUT)

    assert len(parsed["live_samples"]) == 2
    assert parsed["combined"]["rx_mbps"] == 35.73
    assert parsed["downlink"]["rx_mbps"] == 26.42
    assert parsed["uplink"]["rx_mbps"] == 9.32
    assert parsed["summary_by_device"]["SU1"]["avg_rx_mbps"] == 26.42
    assert parsed["consolidated_summary"][0]["bidi_mbps"] == 35.73

