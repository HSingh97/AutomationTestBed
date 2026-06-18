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
    assert parse_running_htmode("11ACVHT40") == "HT40"
    assert parse_running_htmode("11AHE160") == "HT160"


def test_wait_for_running_bandwidth_polls_until_match():
    calls = {"n": 0}

    def fake_fetch(ip, user, password, radio_idx):
        calls["n"] += 1
        mode = "HT80" if calls["n"] >= 2 else "HT20"
        return f"ath1\tget_mode:11AHE{mode[2:]}", mode

    with patch("traffic.dut_radio_config._fetch_cfg80211_mode", side_effect=fake_fetch):
        ok = _wait_for_running_bandwidth(
            "10.0.0.1",
            "root",
            "pw",
            1,
            "HT80",
            timeout_s=10,
            poll_s=0.1,
        )

    assert ok is True
    assert calls["n"] >= 2


def test_radio_profile_does_not_skip_when_uci_and_running_bandwidth_differ():
    mcs_report = {"mcs_config_ok": True}

    with patch("traffic.dut_radio_config.verify_mcs_all_devices", return_value=mcs_report):
        with patch("traffic.dut_radio_config._read_uci", side_effect=["HT80", "75"]):
            with patch("traffic.dut_radio_config._read_running_bandwidth", return_value="HT20"):
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
