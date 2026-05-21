from traffic.operating_rate_table import operating_rate_mbps
from traffic.phy_rate_targets import compute_traffic_targets


def test_ht20_mcs23_operating_rate_from_sheet():
    assert operating_rate_mbps("HT20", "MCS23") == 286.0


def test_ht20_mcs23_75_25_dynamic_targets():
    targets = compute_traffic_targets(
        bandwidth="HT20",
        mcs="MCS23",
        ratio="75:25",
        efficiency_factor=0.75,
    )
    assert targets["phy_max_mbps"] == 286.0
    assert targets["effective_target_mbps"] == 214.5
    assert targets["downlink_mbps"] == 160.88
    assert targets["uplink_mbps"] == 53.62
    assert targets["trex_dl_bw"] == "161M"
    assert targets["trex_ul_bw"] == "54M"


def test_ht80_mcs23_operating_rate_dual_from_sheet():
    assert operating_rate_mbps("HT80", "MCS23") == 1201.0


def test_ht160_mcs23_operating_rate_dual_from_sheet():
    assert operating_rate_mbps("HT160", "MCS23") == 2401.0
