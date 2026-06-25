from unittest.mock import patch

from traffic.performance_matrix import _check_bandwidth_before_trex


def test_skips_when_bandwidth_apply_was_skipped():
    ready, err, validation = _check_bandwidth_before_trex(
        "10.0.0.1",
        user="root",
        password="pw",
        radio_idx=1,
        bandwidth="HT20",
        mcs="MCS23",
        bandwidth_skipped=True,
        link_wifi_idx=1,
        spatial_stream=2,
        tolerance_mbps=10.0,
        tolerance_pct=0.08,
        source="ssh",
        cpe_hosts=[],
    )
    assert not ready
    assert "not applied" in err
    assert validation == {}


def test_skips_when_running_bandwidth_differs():
    with patch(
        "traffic.performance_matrix.read_running_bandwidth",
        return_value="HT40",
    ):
        ready, err, validation = _check_bandwidth_before_trex(
            "10.0.0.1",
            user="root",
            password="pw",
            radio_idx=1,
            bandwidth="HT20",
            mcs="MCS23",
            bandwidth_skipped=False,
            link_wifi_idx=1,
            spatial_stream=2,
            tolerance_mbps=10.0,
            tolerance_pct=0.08,
            source="ssh",
            cpe_hosts=[],
        )
    assert not ready
    assert err == "Running bandwidth HT40 != requested HT20"
    assert validation == {}


def test_skips_on_operating_rate_mismatch_when_running_unknown():
    validation_payload = {
        "operating_rate_ok": False,
        "operating_rate_mismatch": True,
        "expected_operating_rate_mbps": 286.0,
        "clients": [{"out_rate": 573, "in_rate": 573}],
    }
    with patch(
        "traffic.performance_matrix.read_running_bandwidth",
        return_value=None,
    ):
        with patch(
            "traffic.performance_matrix._fetch_link_validation",
            return_value=validation_payload,
        ):
            ready, err, validation = _check_bandwidth_before_trex(
                "10.0.0.1",
                user="root",
                password="pw",
                radio_idx=1,
                bandwidth="HT20",
                mcs="MCS23",
                bandwidth_skipped=False,
                link_wifi_idx=1,
                spatial_stream=2,
                tolerance_mbps=10.0,
                tolerance_pct=0.08,
                source="ssh",
                cpe_hosts=[],
            )
    assert not ready
    assert "Operating rate mismatch" in err
    assert validation == validation_payload


def test_allows_trex_when_running_bandwidth_and_rate_match():
    validation_payload = {
        "operating_rate_ok": True,
        "operating_rate_mismatch": False,
        "expected_operating_rate_mbps": 573.0,
        "clients": [{"out_rate": 573, "in_rate": 573}],
    }
    with patch(
        "traffic.performance_matrix.read_running_bandwidth",
        return_value="HT40",
    ):
        with patch(
            "traffic.performance_matrix._fetch_link_validation",
            return_value=validation_payload,
        ):
            ready, err, validation = _check_bandwidth_before_trex(
                "10.0.0.1",
                user="root",
                password="pw",
                radio_idx=1,
                bandwidth="HT40",
                mcs="MCS23",
                bandwidth_skipped=False,
                link_wifi_idx=1,
                spatial_stream=2,
                tolerance_mbps=10.0,
                tolerance_pct=0.08,
                source="ssh",
                cpe_hosts=[],
            )
    assert ready
    assert err == ""
    assert validation == validation_payload
