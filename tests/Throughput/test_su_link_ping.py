from unittest.mock import patch

from traffic.su_link_ping import (
    _in_network,
    _ipv6_network,
    build_qinq_pc_setup_commands,
    wait_for_su_links,
)


def test_build_qinq_pc_setup_commands_assigns_mgmt_ipv6_in_bts_subnet():
    profile_tb = {
        "qinq": {"svlan": 200, "cvlan": 201},
        "mgmt_vlan": {
            "prefix_len": 120,
            "ipv6_bts": "2001:2002:2003:2004:2005:2006:2007:1111",
            "ipv6_bts_pc": "2001:2002:2003:2004:2005:2006:2007:1101",
        },
        "lab_pc_tagging": {"bts": {"mode": "qinq", "svlan": 200, "cvlan": 201}},
        "primary_pc": {"mgmt_interface": "enp3s0"},
    }
    cidr = "2001:2002:2003:2004:2005:2006:2007:1101/120"
    joined, inner = build_qinq_pc_setup_commands(profile_tb, cidr=cidr)
    assert inner == "enp3s0.200.201"
    assert "2001:2002:2003:2004:2005:2006:2007:1101/120" in joined
    assert "ip -6 addr add" in joined
    net = _ipv6_network("2001:2002:2003:2004:2005:2006:2007:1111", 120)
    assert _in_network("2001:2002:2003:2004:2005:2006:2007:11c9", net)


def test_wait_for_su_links_discovers_targets_from_bts(monkeypatch):
    monkeypatch.setattr("traffic.su_link_ping._ensure_local_qinq_iface", lambda *a, **k: None)
    discovered = ["2001:2002:2003:2004:2005:2006:2007:11c9"]

    with patch("traffic.su_link_ping.discover_su_hosts_from_bts", return_value=discovered):
        with patch("traffic.su_link_ping._bts_ping_ok", return_value=True):
            result = wait_for_su_links(
                cpe_hosts=["2401:4900:d0:40d4::17b8:0:331"],
                profile_tb={},
                bts_ip="10.0.0.1",
                bts_user="root",
                bts_password="pw",
                timeout_s=1,
                poll_s=0.1,
            )

    assert result["ok"] is True
    assert result["targets"] == discovered


def test_wait_for_su_links_strict_does_not_say_continuing(monkeypatch):
    monkeypatch.setattr("traffic.su_link_ping.discover_su_hosts_from_bts", lambda *a, **k: ["::1"])
    monkeypatch.setattr("traffic.su_link_ping._ensure_local_qinq_iface", lambda *a, **k: None)
    monkeypatch.setattr("traffic.su_link_ping._bts_ping_ok", lambda *a, **k: False)

    result = wait_for_su_links(
        cpe_hosts=["::1"],
        profile_tb={},
        bts_ip="10.0.0.1",
        bts_user="root",
        bts_password="pw",
        timeout_s=0.2,
        poll_s=0.1,
        min_responding=4,
        strict=True,
    )

    assert result["ok"] is False

    monkeypatch.setattr("traffic.su_link_ping.discover_su_hosts_from_bts", lambda *a, **k: [])
    monkeypatch.setattr("traffic.su_link_ping._ensure_local_qinq_iface", lambda *a, **k: None)
    calls: list[str] = []

    def fake_bts_ping(bts_ip, user, password, host, *, count=2):
        calls.append(host)
        return host.endswith(":11c9")

    with patch("traffic.su_link_ping._bts_ping_ok", side_effect=fake_bts_ping):
        result = wait_for_su_links(
            cpe_hosts=["2001:2002:2003:2004:2005:2006:2007:11c9"],
            profile_tb={},
            bts_ip="10.0.0.1",
            bts_user="root",
            bts_password="pw",
            timeout_s=1,
            poll_s=0.1,
        )

    assert result["ok"] is True
    assert result["method"] == "bts-ssh"
    assert calls
