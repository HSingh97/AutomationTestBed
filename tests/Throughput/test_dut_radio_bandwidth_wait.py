from unittest.mock import patch

from traffic.dut_radio_config import (
    cac_log_state,
    configure_bandwidth_profile,
    configure_bts_bandwidth_ratio,
    configure_mcs_profile,
    configure_radio_profile,
    is_dfs_channel,
)


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
        "ucidyn set advwireless.ath1.htmode HT80 && "
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

    assert "ucidyn set advwireless.ath1.htmode HT40+" in captured[0]["command"]


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


def test_dfs_channel_and_cac_log():
    assert is_dfs_channel(52)
    assert is_dfs_channel(100)
    assert is_dfs_channel(144)
    assert not is_dfs_channel(36)
    assert not is_dfs_channel(149)
    log = (
        "Mon Nov 23 10:00:00 2026: DFS CAC started on channel 100\n"
        "Mon Nov 23 10:01:01 2026: DFS CAC end on channel 100\n"
        "Mon Nov 23 10:02:00 2026: DFS CAC started on channel 52\n"
    )
    assert cac_log_state(log, 100) == "done"
    assert cac_log_state(log, 52) == "running"
    assert cac_log_state(log, 36) == "idle"


def test_configure_bandwidth_profile_applies_once_per_group():
    with patch("traffic.dut_radio_config.wait_for_dfs_cac"):
        with patch("traffic.dut_radio_config.configure_bts_bandwidth_ratio", return_value="75") as mock_bw:
            with patch(
                "traffic.su_link_ping.wait_for_su_links",
                return_value={"ok": True, "responding": ["a", "b", "c", "d"]},
            ):
                with patch("traffic.dut_radio_config._wait_for_running_bandwidth"):
                    with patch("traffic.dut_radio_config._verify_bts_bandwidth_ratio"):
                        with patch(
                            "traffic.dut_radio_config.bandwidth_profile_matches",
                            return_value=False,
                        ):
                            report = configure_bandwidth_profile(
                                "10.0.0.1",
                                "root",
                                "pw",
                                1,
                                "HT80",
                                "75:25",
                                su_count=4,
                                verify=False,
                            )

    mock_bw.assert_called_once()
    assert report.get("bandwidth_ok") is True


def test_configure_bandwidth_profile_sets_cpe_htmode():
    with patch("traffic.dut_radio_config.configure_bts_bandwidth_ratio", return_value="50"):
        with patch(
            "traffic.su_link_ping.wait_for_su_links",
            return_value={"ok": True, "responding": ["192.168.3.21"]},
        ):
            with patch("traffic.dut_radio_config.bandwidth_profile_matches", return_value=False):
                with patch("traffic.dut_radio_config._read_uci", return_value="HT20"):
                    with patch(
                        "traffic.dut_radio_config._read_running_bandwidth",
                        return_value="HT20",
                    ):
                        with patch(
                            "traffic.dut_radio_config.run_ssh_command",
                            return_value="CPE_APPLY_STARTED",
                        ) as mock_ssh:
                            report = configure_bandwidth_profile(
                                "192.168.3.20",
                                "root",
                                "pw",
                                1,
                                "HT80",
                                "50:50",
                                cpe_hosts=["192.168.3.21"],
                                su_count=1,
                                verify=False,
                            )

    assert report.get("cpe_bandwidth_applied") is True
    chain = mock_ssh.call_args.args[3]
    assert "ucidyn set advwireless.ath1.htmode HT80" in chain
    assert "ucidyn apply" in chain
    assert mock_ssh.call_args.args[0] == "192.168.3.21"


def test_configure_bandwidth_profile_skips_when_running_width_already_matches():
    def fake_uci(ip, user, password, cmd):
        if "dlulratio" in cmd:
            return "50"
        if "htmode" in cmd:
            return "HT80"
        return ""

    with patch("traffic.dut_radio_config.configure_bts_bandwidth_ratio") as mock_bw:
        with patch("traffic.dut_radio_config.wait_for_dfs_cac") as mock_cac:
            with patch("traffic.dut_radio_config._read_uci", side_effect=fake_uci):
                with patch(
                    "traffic.dut_radio_config._read_running_bandwidth",
                    return_value="HT20",
                ):
                    with patch("traffic.dut_radio_config.run_ssh_command") as mock_ssh:
                        report = configure_bandwidth_profile(
                            "10.0.0.1",
                            "root",
                            "pw",
                            1,
                            "HT20",
                            "50:50",
                            cpe_hosts=["192.168.3.21"],
                            su_count=1,
                            verify=False,
                        )

    mock_bw.assert_not_called()
    mock_cac.assert_not_called()
    mock_ssh.assert_not_called()
    assert report.get("bandwidth_skipped") is True
    assert report.get("cpe_bandwidth_applied") is False


def test_configure_mcs_profile_does_not_apply_bandwidth():
    with patch("traffic.dut_radio_config.configure_bts_mcs_only"):
        with patch("traffic.dut_radio_config._apply_mcs_all_cpes"):
            with patch(
                "traffic.dut_radio_config.verify_mcs_all_devices",
                return_value={"mcs_config_ok": True},
            ):
                with patch(
                    "traffic.dut_radio_config.configure_bts_bandwidth_ratio"
                ) as mock_bw:
                    with patch(
                        "traffic.dut_radio_config.radio_profile_already_matches",
                        return_value=(False, {}),
                    ):
                        report = configure_mcs_profile(
                            "10.0.0.1",
                            "root",
                            "pw",
                            1,
                            "HT80",
                            "MCS23",
                            "75:25",
                            su_count=4,
                            verify=False,
                        )

    mock_bw.assert_not_called()
    assert report.get("mcs_config_ok") is True
