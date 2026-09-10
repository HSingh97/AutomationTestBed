from traffic.trex_runner import build_trex_client_command, resolve_trex_qinq_tags


def test_build_trex_client_command_qinq_args():
    cmd = build_trex_client_command(
        trex_dl_bw="800M",
        trex_ul_bw="200M",
        trex_svlan=100,
        trex_cvlan=101,
    )
    assert "--svlan 100" in cmd
    assert "--cvlan 101" in cmd
    assert "--vlan" not in cmd


def test_qinq_asymmetric_tagging_helpers():
    from traffic.scripts.qinq_tags import apply_uplink_tags, format_vlan_label, header_sizes

    assert "QinQ" in format_vlan_label(None, 100, 101)
    dl_size, ul_size = header_sizes(None, 100, 101)
    assert dl_size == 50
    assert ul_size == 42

    class Header:
        type = 0

        def __truediv__(self, other):
            return self

    uplink_header = Header()
    assert apply_uplink_tags(uplink_header, None, 100, 101, dot1q_cls=object) is uplink_header


def test_build_trex_client_command_single_vlan():
    cmd = build_trex_client_command(
        trex_dl_bw="800M",
        trex_ul_bw="200M",
        trex_vlan=21,
    )
    assert "--vlan 21" in cmd
    assert "--svlan" not in cmd


def test_resolve_trex_qinq_tags_cli_override():
    svlan, cvlan = resolve_trex_qinq_tags(
        host=None,
        user="root",
        password="",
        profile_tb={},
        trex_svlan=200,
        trex_cvlan=201,
        qinq_enabled=True,
    )
    assert svlan == 200
    assert cvlan == 201


def test_resolve_trex_qinq_tags_from_profile():
    svlan, cvlan = resolve_trex_qinq_tags(
        host=None,
        user="root",
        password="",
        profile_tb={"qinq": {"svlan": 200, "cvlan": 201}},
        qinq_enabled=True,
    )
    assert svlan == 200
    assert cvlan == 201
