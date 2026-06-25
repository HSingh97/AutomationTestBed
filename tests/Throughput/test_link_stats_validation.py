from traffic.link_stats import validate_operating_rates


def test_operating_rate_match_on_out_rate_only():
    clients = [{"out_rate": "286 (23)", "in_rate": "72 (10)"}]
    result = validate_operating_rates(
        bandwidth="HT20",
        configured_mcs="MCS23",
        clients=clients,
        tolerance_mbps=10.0,
    )
    assert result["expected_operating_rate_mbps"] == 286.0
    assert result["operating_rate_ok"] is True
    assert result["operating_rate_mismatch"] is False


def test_uplink_rate_does_not_fail_validation():
    clients = [{"out_rate": "1201 (23)", "in_rate": "288 (15)"}]
    result = validate_operating_rates(
        bandwidth="HT80",
        configured_mcs="MCS23",
        clients=clients,
        tolerance_mbps=10.0,
    )
    assert result["expected_operating_rate_mbps"] == 1201.0
    assert result["operating_rate_ok"] is True


def test_operating_rate_mismatch_flags_red():
    clients = [{"out_rate": "144 (3)", "in_rate": "144 (3)"}]
    result = validate_operating_rates(
        bandwidth="HT80",
        configured_mcs="MCS23",
        clients=clients,
        tolerance_mbps=5.0,
    )
    assert result["operating_rate_mismatch"] is True
    assert result["operating_rate_ok"] is False
