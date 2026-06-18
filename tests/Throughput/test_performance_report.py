from utils.performance_report import write_html_report


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
    assert "applyFilter('all')" in html
