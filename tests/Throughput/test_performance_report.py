from utils.performance_report import write_html_report
from utils.regression_report import _render_testbed_summary_table


def _sample_record(bandwidth: str, mcs: str, bidi: float) -> dict:
    return {
        "bandwidth": bandwidth,
        "mcs": mcs,
        "mode": "Bidirectional",
        "ratio": "75:25",
        "passed": True,
        "throughput_passed": True,
        "effective_target_mbps": 200.0,
        "operating_rate_mbps": 286.0,
        "packet_size": 1500,
        "duration_s": 30,
        "noise_dbm": "-93",
        "spatial_stream": 2,
        "stats": {
            "combined": {"rx_mbps": bidi},
            "downlink": {"rx_mbps": bidi * 0.75},
            "uplink": {"rx_mbps": bidi * 0.25},
        },
        "link_validation": {
            "clients": [
                {
                    "system_name": "cpe1",
                    "su_index": 1,
                    "ip": "2001:2002:2003:2004:2005:2006:2007:11c9",
                    "tx_rate": "286 (23)",
                    "rx_rate": "286 (23)",
                }
            ],
            "spec": {"mcs": mcs, "modulation": "1024-QAM 5/6", "operating_rate_mbps": 286.0},
        },
        "mcs_config": {"mcs_config_ok": True},
    }


def test_html_report_includes_bandwidth_filter_controls(tmp_path):
    records = [
        _sample_record("HT20", "MCS23", 178.0),
        _sample_record("HT40", "MCS23", 401.0),
        _sample_record("HT80", "MCS23", 790.0),
    ]
    path = tmp_path / "report.html"
    write_html_report(
        records=records,
        run_meta={"executed_at": "2026-06-18", "Bandwidths": "HT20, HT40, HT80"},
        path=path,
    )
    html = path.read_text(encoding="utf-8")
    assert 'data-bw="HT20"' in html
    assert 'data-bw="HT40"' in html
    assert 'data-bw-filter="HT80"' in html
    assert 'class="bw-btn active" data-bw="all"' in html
    assert "790 Mbps" in html
    assert "1/1 PASS" not in html
    assert "peak " not in html.lower()
    assert "applyFilter('all')" in html


def test_testbed_summary_renders_one_column_per_su():
    summary = {
        "stand": "test-qa-lab-02",
        "profile": "qa_lab_02",
        "su_count": 4,
        "bts": {
            "model": "UBR-630",
            "fw_version": "2.4.1.0",
            "ip": "2001:2002:2003:2004:2005:2006:2007:1111",
            "vlan": "Enabled ( QinQ )",
            "qos": "75:25",
        },
        "cpes": [
            {
                "label": f"SU{idx}",
                "su_index": idx,
                "model": "UBR-620",
                "fw_version": "2.4.1.0",
                "ip": f"2001:2002:2003:2004:2005:2006:2007:11{idx:02x}",
                "vlan": "Enabled ( QinQ )",
                "qos": "—",
            }
            for idx in range(1, 5)
        ],
    }
    html = _render_testbed_summary_table(summary)
    assert "summary-multi" in html
    assert "<span class='device-name'>BTS</span>" in html
    assert "<span class='device-name'>SU1</span>" in html
    assert "<span class='device-name'>SU4</span>" in html
    assert "Connected SUs" in html
