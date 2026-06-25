from traffic.performance_matrix import _print_matrix_console_table


def test_print_matrix_console_table_shows_mismatch_but_ran(capsys):
    records = [
        {
            "bandwidth": "HT20",
            "mcs": "MCS22",
            "passed": True,
            "mcs_config": {"mcs_config_ok": False, "checks": [
                {"role": "BTS", "actual_mcs": "22"},
                {"role": "CPE", "actual_mcs": "23"},
            ]},
            "mcs_mismatch_note": "MCS mismatch on SU1 — throughput ran anyway",
            "stats": {"combined": {"rx_mbps": 500.0}},
        },
    ]
    _print_matrix_console_table(records, bandwidths=["HT20"], mcs_rates=["MCS22"])
    output = capsys.readouterr().out
    assert "MISMATCH" in output
    assert "PASS" in output
    assert "MCS mismatch" in output


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
            "mcs_config": {"mcs_config_ok": True, "checks": [
                {"role": "BTS", "actual_mcs": "23"},
                {"role": "CPE", "actual_mcs": "23"},
                {"role": "CPE", "actual_mcs": "23"},
            ]},
        },
    ]

    _print_matrix_console_table(records, bandwidths=["HT20"], mcs_rates=["MCS22", "MCS23"])
    output = capsys.readouterr().out

    assert "PERFORMANCE MATRIX — CONSOLE SUMMARY" in output
    assert "HT20" in output
    assert "MCS22" in output
    assert "MCS23" in output
    assert "23" in output
    assert "BTS" not in output or "23" in output
