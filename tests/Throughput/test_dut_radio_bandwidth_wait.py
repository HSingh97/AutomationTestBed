from unittest.mock import patch

from traffic.dut_radio_config import configure_bts_bandwidth_ratio


def test_bandwidth_apply_uses_single_and_chain():
    captured: list[dict] = []

    def fake_ssh(ip, user, password, command, *, timeout_s=30):
        captured.append({"command": command, "timeout_s": timeout_s})
        return "ath1\tget_mode:11AHE80"

    with patch("traffic.dut_radio_config.run_ssh_command", side_effect=fake_ssh):
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
    chain = captured[0]["command"]
    assert chain == (
        "ucidyn set wireless.wifi1.htmode HT80 && "
        "ucidyn set ath1qos.qoscfg.dlulratio 75 && "
        "ucidyn apply && sleep 60 && cfg80211tool ath1 get_mode"
    )
    assert "remote_exec" not in chain
    assert chain.count("ucidyn apply") == 1
    assert captured[0]["timeout_s"] >= 90
