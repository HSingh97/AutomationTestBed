from traffic.link_stats import validate_operating_rates


def test_operating_rate_match_within_tolerance():
    clients = [{"tx_rate_mbps": 280.0, "rx_rate_mbps": 286.0}]
    result = validate_operating_rates(
        bandwidth="HT20",
        configured_mcs="MCS23",
        clients=clients,
        tolerance_mbps=10.0,
    )
    assert result["expected_operating_rate_mbps"] == 286.0
    assert result["operating_rate_ok"] is True
    assert result["operating_rate_mismatch"] is False


def test_operating_rate_mismatch_flags_red():
    clients = [{"tx_rate_mbps": 100.0, "rx_rate_mbps": 86.0}]
    result = validate_operating_rates(
        bandwidth="HT20",
        configured_mcs="MCS23",
        clients=clients,
        tolerance_mbps=5.0,
    )
    assert result["operating_rate_mismatch"] is True
    assert result["operating_rate_ok"] is False
