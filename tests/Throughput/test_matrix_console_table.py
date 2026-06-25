from traffic.performance_matrix import _print_matrix_console_table


def test_print_matrix_console_table_smoke(capsys):
    records = [
        {
            "bandwidth": "HT20",
            "mcs": "MCS22",
            "passed": False,
            "skipped_trex": True,
            "mcs_config": {"mcs_config_ok": False},
            "error": "MCS config mismatch",
        },
        {
            "bandwidth": "HT20",
            "mcs": "MCS23",
            "passed": True,
            "stats": {
                "combined": {"rx_mbps": 700.5},
                "downlink": {"rx_mbps": 525.0},
                "uplink": {"rx_mbps": 175.5},
            },
            "mcs_config": {"mcs_config_ok": True},
        },
    ]

    _print_matrix_console_table(records, bandwidths=["HT20"], mcs_rates=["MCS22", "MCS23"])
    output = capsys.readouterr().out

    assert "PERFORMANCE MATRIX — CONSOLE SUMMARY" in output
    assert "HT20" in output
    assert "MCS22" in output
    assert "MCS23" in output
    assert "700.5" in output
