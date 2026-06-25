from unittest.mock import patch

from traffic.dut_radio_config import verify_mcs_all_devices


def _sua_slots(count: int, mcs: str, *, rate: str = "960") -> dict[int, dict[str, str]]:
    return {
        idx: {
            "sua_index": str(idx),
            "rx_rate_mcs": mcs,
            "rx_rate": rate,
            "tx_rate_mcs": mcs,
            "tx_rate": "600",
            "mac": f"aa:bb:cc:dd:ee:{idx:02x}",
        }
        for idx in range(1, count + 1)
    }


@patch("traffic.dut_radio_config._read_uci")
@patch("traffic.dut_radio_config.read_operating_mcs_by_sua_slot")
def test_verify_mcs_all_devices_uses_bts_sysfs_rx_rate_mcs(mock_sysfs, mock_uci):
    mock_uci.side_effect = ["22", "2"]
    mock_sysfs.return_value = _sua_slots(6, "22")

    report = verify_mcs_all_devices(
        "10.0.0.1",
        "root",
        "pass",
        1,
        1,
        "MCS22",
        "2",
        su_count=6,
    )

    assert report["mcs_config_ok"] is True
    assert len(report["checks"]) == 7
    su_checks = [row for row in report["checks"] if row["role"] == "CPE"]
    assert len(su_checks) == 6
    assert all(row["source"] == "bts_sysfs:rx_rate_mcs" for row in su_checks)
    assert all(row["actual_mcs"] == "22" for row in su_checks)
    mock_sysfs.assert_called_once_with(
        "10.0.0.1",
        ssh_user="root",
        ssh_password="pass",
        max_sua=6,
    )


@patch("traffic.dut_radio_config._read_uci")
@patch("traffic.dut_radio_config.read_operating_mcs_by_sua_slot")
def test_verify_mcs_all_devices_fails_on_operating_mismatch(mock_sysfs, mock_uci):
    mock_uci.side_effect = ["23", "2"]
    slots = _sua_slots(6, "23")
    slots[5]["rx_rate_mcs"] = "22"
    mock_sysfs.return_value = slots

    report = verify_mcs_all_devices(
        "10.0.0.1",
        "root",
        "pass",
        1,
        1,
        "MCS23",
        "2",
        su_count=6,
    )

    assert report["mcs_config_ok"] is False
    su5 = next(row for row in report["checks"] if row["label"] == "SU5")
    assert su5["actual_mcs"] == "22"


@patch("traffic.dut_radio_config._read_uci")
@patch("traffic.dut_radio_config.read_operating_mcs_by_sua_slot")
def test_verify_mcs_all_devices_missing_sua_slot(mock_sysfs, mock_uci):
    mock_uci.side_effect = ["22", "2"]
    slots = _sua_slots(5, "22")
    mock_sysfs.return_value = slots

    report = verify_mcs_all_devices(
        "10.0.0.1",
        "root",
        "pass",
        1,
        1,
        "MCS22",
        "2",
        su_count=6,
    )

    assert report["mcs_config_ok"] is False
    su6 = next(row for row in report["checks"] if row["label"] == "SU6")
    assert "not associated" in str(su6.get("error", ""))
