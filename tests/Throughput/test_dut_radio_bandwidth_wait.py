from unittest.mock import patch

from traffic.dut_radio_config import configure_bts_bandwidth_ratio, configure_radio_profile


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


def test_bandwidth_apply_ht40_uses_ht40_plus_uci_value():
    captured: list[dict] = []

    def fake_ssh(ip, user, password, command, *, timeout_s=30):
        captured.append({"command": command})
        return "ath1\tget_mode:11AHE40PLUS"

    with patch("traffic.dut_radio_config.run_ssh_command", side_effect=fake_ssh):
        with patch("traffic.dut_radio_config._log_cfg80211_mode", return_value=("raw", "HT40")):
            with patch("traffic.dut_radio_config._verify_bts_bandwidth_ratio"):
                configure_bts_bandwidth_ratio(
                    "10.0.0.1",
                    "root",
                    "pw",
                    1,
                    "HT40",
                    "75:25",
                    bandwidth_apply_wait_s=60,
                    verify=False,
                )

    assert "ucidyn set wireless.wifi1.htmode HT40+" in captured[0]["command"]


def test_configure_radio_profile_applies_bandwidth_without_pre_link_gate():
    with patch("traffic.dut_radio_config.radio_profile_already_matches", return_value=(False, {})):
        with patch("traffic.dut_radio_config.configure_bts_mcs_only"):
            with patch("traffic.dut_radio_config._apply_mcs_all_cpes"):
                with patch("traffic.dut_radio_config.configure_bts_bandwidth_ratio", return_value="75") as mock_bw:
                    with patch("traffic.dut_radio_config._reapply_mcs_all_devices"):
                        with patch(
                            "traffic.dut_radio_config.verify_mcs_all_devices",
                            return_value={"mcs_config_ok": True},
                        ):
                            with patch(
                                "traffic.su_link_ping.wait_for_su_links",
                                return_value={"ok": True, "responding": ["a", "b", "c", "d"]},
                            ) as mock_wait:
                                report = configure_radio_profile(
                                    "10.0.0.1",
                                    "root",
                                    "pw",
                                    1,
                                    "HT80",
                                    "MCS23",
                                    "75:25",
                                    su_count=4,
                                    su_link_wait_s=120,
                                    verify=False,
                                )

    mock_bw.assert_called_once()
    mock_wait.assert_called_once()
    assert mock_wait.call_args.kwargs["phase"] == "after bandwidth apply"
    assert report.get("bandwidth_skipped") is False
