import json
from pathlib import Path

from utils.grafana_matrix_report import enrich_matrix_payload, write_matrix_grafana_html


def _sample_record(*, bandwidth: str, mcs: str, rx: float, passed: bool = True, skipped: bool = False):
    return {
        "bandwidth": bandwidth,
        "mcs": mcs,
        "passed": passed,
        "skipped_trex": skipped,
        "effective_target_mbps": rx * 1.1,
        "spatial_stream": 2,
        "efficiency_factor": 0.7,
        "duration_s": 30,
        "stats": {
            "combined": {"rx_mbps": rx},
            "trex": {
                "live_samples": [
                    {
                        "timestamp": "12:00:01",
                        "combined": {"rx_mbps": rx * 0.9, "tx_mbps": rx, "tx_pps": 1000, "rx_pps": 950},
                    },
                    {
                        "timestamp": "12:00:06",
                        "combined": {"rx_mbps": rx, "tx_mbps": rx, "tx_pps": 1000, "rx_pps": 980},
                    },
                ],
                "dut_counters": {"samples": [{"avg_rtx_pct": "0.5"}, {"avg_rtx_pct": "0.2"}]},
            },
        },
        "link_validation": {
            "clients": [
                {
                    "su_index": 1,
                    "ipv6": "2001:2002:2003:2004:2005:2006:2007:111b",
                    "system_name": "UBR650_CPE_3",
                    "r_model": "EOC650-C23",
                    "rx_rate": "258 (22)",
                    "tx_rate": "258 (22)",
                    "rx_rate_mcs": "22",
                    "r_snr1": "60",
                    "r_snr2": "53",
                    "r_rssi1": "-54",
                }
            ]
        },
    }


def test_enrich_matrix_payload_builds_coverage_cells():
    records = [
        _sample_record(bandwidth="HT20", mcs="MCS22", rx=170.0),
        _sample_record(bandwidth="HT80", mcs="MCS22", rx=0, passed=False, skipped=True),
    ]
    payload = enrich_matrix_payload(
        records,
        testbed_summary={
            "bts": {"model": "UBR630", "fw_version": "2.4.1", "ip": "2001:2002:2003:2004:2005:2006:2007:1111"},
            "ipv6_prefix_len": 120,
            "su_count": 1,
        },
        report_id="84",
    )
    assert len(payload["matrix_cells"]) == 2
    assert payload["matrix_cells"][0]["rx_mbps"] == 170.0
    assert payload["matrix_cells"][1]["skipped"] is True
    assert payload["link_devices"][0]["ip_display"].startswith("SU1")
    assert "BTS2001" in payload["testbed"]["bts"]["ip_display"]


def test_write_matrix_grafana_html_smoke(tmp_path: Path):
    records = [_sample_record(bandwidth="HT40", mcs="MCS23", rx=359.0)]
    payload = enrich_matrix_payload(records, report_id="sample")
    out = write_matrix_grafana_html(tmp_path / "report.html", payload)
    html = out.read_text(encoding="utf-8")
    assert "Testbed summary" in html
    assert "Link stats" in html
    assert "cov-board" in html
    assert "Live throughput" in html
    assert "% target heatmap" not in html.lower()
    assert "timeSeriesChart" in html


def test_build84_json_roundtrip_if_present():
    path = Path("reports/artifacts/build84/performance_matrix_summary.json")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    payload = enrich_matrix_payload(data["records"], report_id="84")
    assert payload["matrix_total"] == len(data["records"])
    assert payload["matrix_cells"]
