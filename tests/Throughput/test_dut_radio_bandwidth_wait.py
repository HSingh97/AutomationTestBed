from unittest.mock import patch

from traffic.dut_radio_config import configure_bts_bandwidth_ratio


def test_bandwidth_apply_holds_ssh_session_with_sleep_and_cfg80211():
    captured: list[dict] = []

    def fake_bash(ip, user, password, lines, *, timeout_s=120):
        captured.append({"ip": ip, "lines": lines, "timeout_s": timeout_s})
        return "ath1\tget_mode:11AHE80PLUS"

    with patch("traffic.dut_radio_config.run_ssh_bash_session", side_effect=fake_bash):
        with patch("traffic.dut_radio_config._log_cfg80211_mode", return_value=("raw", "HT80")):
            with patch("traffic.dut_radio_config._verify_bts_bandwidth_ratio"):
                configure_bts_bandwidth_ratio(
                    "10.0.0.1",
                    "root",
                    "pw",
                    1,
                    "HT80",
                    "75:25",
                    bandwidth_apply_wait_s=60,
                    verify=True,
                )

    assert len(captured) == 1
    lines = captured[0]["lines"]
    assert any("wireless.wifi1.htmode HT80" in line for line in lines)
    assert any("remote_exec.sh" in line for line in lines)
    assert lines[-2] == "sleep 60"
    assert lines[-1] == "cfg80211tool ath1 get_mode"
    assert captured[0]["timeout_s"] >= 90
