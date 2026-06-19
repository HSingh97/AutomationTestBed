from unittest.mock import patch

from traffic.link_debug_snapshot import collect_link_debug_snapshot


def test_collect_link_debug_snapshot_shape():
    fake_clients = [
        {
            "sua_index": 1,
            "ip": "2001:2002:2003:2004:2005:2006:2007:11c9",
            "ipv6": "2001:2002:2003:2004:2005:2006:2007:11c9",
            "mac": "aa:bb:cc:dd:ee:01",
            "tx_rate": "51 (14)",
            "rx_rate": "51 (14)",
        }
    ]
    with patch("traffic.link_debug_snapshot.fetch_kwn_sua_statistics", return_value=fake_clients), patch(
        "traffic.link_debug_snapshot._read_bts_vlan_uci", return_value={"mode": "transparent"}
    ), patch(
        "traffic.link_debug_snapshot.read_running_bandwidth", return_value="HT20"
    ), patch(
        "traffic.link_debug_snapshot._ensure_local_qinq_iface", return_value=None
    ), patch(
        "traffic.link_debug_snapshot._ping_matrix",
        return_value=[{"host": "2001:2002:2003:2004:2005:2006:2007:11c9", "reachable": True}],
    ):
        snap = collect_link_debug_snapshot(
            label="test",
            bts_ip="10.0.0.1",
            bts_user="root",
            bts_password="x",
            profile_tb={},
            dut_cfg={},
            cpe_hosts=["2001:2002:2003:2004:2005:2006:2007:11c9"],
        )
    assert snap["sysfs_associated_count"] == 1
    assert snap["running_bandwidth"] == "HT20"
    assert snap["ping_ok_count"] == 1
