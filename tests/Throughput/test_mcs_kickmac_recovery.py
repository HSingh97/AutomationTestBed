from unittest.mock import patch

from traffic.dut_radio_config import (
    _recover_su_mcs_mismatch_via_kickmac,
    kick_su_at_sua_slot,
    verify_mcs_all_devices,
)


def test_kickmac_command_uses_wlanconfig():
    with patch("traffic.dut_radio_config.run_ssh_command") as mock_ssh, patch(
        "traffic.dut_radio_config.read_operating_mcs_by_sua_slot",
        return_value={1: {"mac": "aabbccddeeff"}},
    ):
        mac = kick_su_at_sua_slot("10.0.0.1", "root", "pass", 1, radio_idx=1)
        assert mac == "aa:bb:cc:dd:ee:ff"
        assert "wlanconfig ath1 kickmac aa:bb:cc:dd:ee:ff" in mock_ssh.call_args[0][3]


@patch("traffic.dut_radio_config.time.sleep")
@patch("traffic.dut_radio_config.kick_su_at_sua_slot", return_value="aa:bb:cc:dd:ee:ff")
@patch("traffic.dut_radio_config._collect_mcs_checks")
def test_recover_kickmac_recollects_after_wait(mock_collect, _mock_kick, mock_sleep):
    mock_collect.return_value = [
        {"role": "BTS", "label": "BTS", "actual_mcs": "22", "ok": True},
        {"role": "CPE", "label": "SU1", "su_index": 1, "actual_mcs": "22", "ok": True},
    ]
    initial = [
        {"role": "BTS", "label": "BTS", "actual_mcs": "22", "ok": True},
        {"role": "CPE", "label": "SU1", "su_index": 1, "actual_mcs": "23", "ok": False},
    ]

    result = _recover_su_mcs_mismatch_via_kickmac(
        "10.0.0.1",
        "root",
        "pass",
        1,
        checks=initial,
        expected_mcs="22",
        spatial_stream="2",
        su_count=1,
        kick_wait_s=18.0,
    )

    _mock_kick.assert_called_once()
    mock_sleep.assert_called_once_with(18.0)
    assert result[1]["ok"] is True


@patch("traffic.dut_radio_config._recover_su_mcs_mismatch_via_kickmac")
@patch("traffic.dut_radio_config._collect_mcs_checks")
@patch("traffic.dut_radio_config.wait_for_operating_mcs_on_sus", return_value=True)
@patch("traffic.dut_radio_config._read_uci")
def test_verify_post_apply_invokes_kickmac_recovery(
    mock_uci, _mock_wait, mock_collect, mock_recover
):
    mock_uci.side_effect = ["22", "2"]
    mock_collect.return_value = [
        {"role": "BTS", "label": "BTS", "actual_mcs": "22", "ok": True, "source": "uci"},
        {"role": "CPE", "label": "SU1", "su_index": 1, "actual_mcs": "23", "ok": False},
    ]
    mock_recover.return_value = [
        {"role": "BTS", "label": "BTS", "actual_mcs": "22", "ok": True, "source": "uci"},
        {"role": "CPE", "label": "SU1", "su_index": 1, "actual_mcs": "22", "ok": True},
    ]

    report = verify_mcs_all_devices(
        "10.0.0.1",
        "root",
        "pass",
        1,
        1,
        "MCS22",
        "2",
        su_count=1,
        wait_for_operating_s=30,
        kickmac_wait_s=18,
        phase="post-apply",
    )
    mock_recover.assert_called_once()
    assert report["mcs_config_ok"] is True
