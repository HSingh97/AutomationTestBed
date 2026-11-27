from unittest.mock import patch

from traffic.dut_radio_config import (
    _wait_for_running_bandwidth,
    parse_running_htmode,
    radio_profile_already_matches,
)


def test_parse_running_htmode_maps_cfg80211_output():
    assert parse_running_htmode("ath1\tget_mode:11AHE20") == "HT20"
    assert parse_running_htmode("11AHE80") == "HT80"
    assert parse_running_htmode("11AHE40PLUS") == "HT40"
    assert parse_running_htmode("11AHE40MINUS") == "HT40"
    assert parse_running_htmode("11ACVHT40") == "HT40"
    assert parse_running_htmode("11AHE160") == "HT160"


def test_wait_for_running_bandwidth_uses_operating_rate_not_get_mode():
    calls = {"n": 0}

    def fake_sample(ip, user, password):
        calls["n"] += 1
        # MCS23 sheet: HT20 is 286, HT80 is 1201. get_mode is not consulted.
        return (286.0, "MCS23") if calls["n"] == 1 else (1201.0, "MCS23")

    with patch("traffic.dut_radio_config._live_operating_sample", side_effect=fake_sample):
        ok = _wait_for_running_bandwidth(
            "10.0.0.1",
            "root",
            "pw",
            1,
            "HT80",
            timeout_s=10,
            poll_s=0.01,
        )

    assert ok is True
    assert calls["n"] >= 2


def test_radio_profile_does_not_skip_when_operating_rate_is_another_width():
    mcs_report = {"mcs_config_ok": True}

    with patch("traffic.dut_radio_config.verify_mcs_all_devices", return_value=mcs_report):
        with patch("traffic.dut_radio_config._read_uci", side_effect=["HT80", "75"]):
            with patch(
                "traffic.dut_radio_config._live_operating_sample",
                return_value=(286.0, "MCS23"),
            ):
                matches, report = radio_profile_already_matches(
                    "10.0.0.1",
                    "root",
                    "pw",
                    1,
                    "HT80",
                    "MCS23",
                    "75:25",
                )

    assert matches is False
    assert report is mcs_report
