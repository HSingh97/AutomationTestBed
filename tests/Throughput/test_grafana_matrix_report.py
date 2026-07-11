import json
from pathlib import Path

from utils.grafana_matrix_report import enrich_matrix_payload, write_matrix_grafana_html


def _sample_record(
    *,
    bandwidth: str,
    mcs: str,
    rx: float,
    passed: bool = True,
    skipped: bool = False,
    mismatch: bool = False,
):
    clients = [
        {
            "su_index": 1,
            "ipv6": "2001:2002:2003:2004:2005:2006:2007:111b",
            "mac": "aa:bb:cc:dd:ee:01",
            "system_name": "UBR650_CPE_3",
            "r_model": "EOC650-C23",
            "rx_rate": "258 (22)",
            "tx_rate": "258 (22)",
            "rx_rate_mcs": "22" if mismatch else mcs.replace("MCS", ""),
            "r_snr1": "60",
            "r_snr2": "53",
            "r_rssi1": "-54",
        }
    ]
    record = {
        "bandwidth": bandwidth,
        "mcs": mcs,
        "passed": passed,
        "skipped_trex": skipped,
        "effective_target_mbps": rx * 1.1 if rx else 10.0,
        "spatial_stream": 2,
        "efficiency_factor": 0.7,
        "duration_s": 30,
        "stats": {
            "combined": {"rx_mbps": rx, "tx_mbps": rx * 1.05 if rx else 0.0},
            "trex": {
                "summary_by_device": {
                    "SU1": {
                        "avg_tx_mbps": round(rx * 0.25, 2) if rx else 0.0,
                        "avg_rx_mbps": round(rx * 0.75, 2) if rx else 0.0,
                    }
                },
                "live_samples": [
                    {
                        "timestamp": "12:00:01",
                        "combined": {"rx_mbps": rx * 0.9, "tx_mbps": rx, "tx_pps": 1000, "rx_pps": 950},
                        "devices": {
                            "SU1": {
                                "tx_mbps": round(rx * 0.25, 2) if rx else 0.0,
                                "rx_mbps": round(rx * 0.75, 2) if rx else 0.0,
                            }
                        },
                    },
                    {
                        "timestamp": "12:00:06",
                        "combined": {"rx_mbps": rx, "tx_mbps": rx, "tx_pps": 1000, "rx_pps": 980},
                        "devices": {
                            "SU1": {
                                "tx_mbps": round(rx * 0.25, 2) if rx else 0.0,
                                "rx_mbps": round(rx * 0.75, 2) if rx else 0.0,
                            }
                        },
                    },
                ],
                "dut_counters": {"samples": [{"avg_rtx_pct": "0.5"}, {"avg_rtx_pct": "0.2"}]},
            },
        },
        "link_validation_post": {"clients": clients},
        "link_validation": {"clients": clients},
    }
    if mismatch:
        record["mcs_mismatch_note"] = "MCS mismatch on SU1 — throughput will still run"
        record["mcs_config"] = {
            "mcs_config_ok": False,
            "checks": [{"label": "SU1", "role": "CPE", "ok": False, "actual_mcs": "22"}],
        }
    return record


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
    assert payload["matrix_cells"][0]["tx_mbps"] == 170.0 * 1.05
    assert payload["matrix_cells"][0]["snr_summary"] == "53/56"
    assert payload["matrix_cells"][0]["rssi_summary"] == "-54"
    assert payload["matrix_cells"][0]["su_rf"][0]["mac"] == "aa:bb:cc:dd:ee:01"
    assert payload["matrix_cells"][0]["su_rf"][0]["tx_mbps"] == "42.5"
    assert payload["matrix_cells"][0]["su_rf"][0]["rx_mbps"] == "127.5"
    assert payload["matrix_cells"][1]["skipped"] is True
    assert "BTS2001" in payload["testbed"]["bts"]["ip_display"]


def test_enrich_mismatch_remark_includes_actual_mcs_and_snr():
    records = [_sample_record(bandwidth="HT80", mcs="MCS23", rx=750.0, mismatch=True)]
    payload = enrich_matrix_payload(records, report_id="mismatch")
    remark = payload["matrix_cells"][0]["remark"]
    assert "want MCS23" in remark
    assert "got MCS22" in remark
    assert "SNR 60/53" in remark


def test_write_matrix_grafana_html_smoke(tmp_path: Path):
    records = [_sample_record(bandwidth="HT40", mcs="MCS23", rx=359.0)]
    payload = enrich_matrix_payload(records, report_id="sample")
    out = write_matrix_grafana_html(tmp_path / "report.html", payload)
    html = out.read_text(encoding="utf-8")
    assert "Testbed summary" in html
    assert "Link stats" not in html
    assert "heat-table" in html
    assert "heat-cell" in html
    assert "cov-card" not in html
    assert "cellRfTable" in html
    assert "TX Mbps" in html
    assert "RX Mbps" in html
    assert "Rx MCS" not in html
    assert ">SNR<" in html
    assert ">RSSI<" in html
    assert "aa:bb:cc:dd:ee:01" in html
    assert "Cell detail" in html
    assert "timeSeriesChart" in html
    assert "% target heatmap" not in html.lower()


def test_build84_json_roundtrip_if_present():
    path = Path("reports/artifacts/build84/performance_matrix_summary.json")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    payload = enrich_matrix_payload(data["records"], report_id="84")
    assert payload["matrix_total"] == len(data["records"])
    assert payload["matrix_cells"]
    assert "snr_summary" in payload["matrix_cells"][0]
