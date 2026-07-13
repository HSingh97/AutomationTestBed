from traffic.link_stats import resolve_cpe_hosts_for_run


def test_resolve_cpe_hosts_caps_at_su_count():
    profile = [
        "2001:2002:2003:2004:2005:2006:2007:11c9",
        "2001:2002:2003:2004:2005:2006:2007:118b",
        "2001:2002:2003:2004:2005:2006:2007:1178",
        "2001:2002:2003:2004:2005:2006:2007:113d",
    ]
    detected = [
        {"su_index": 1, "ip": "2001:2002:2003:2004:2005:2006:2007:11c9"},
        {"su_index": 2, "ip": "2001:2002:2003:2004:2005:2006:2007:118b"},
        {"su_index": 3, "ip": "2001:2002:2003:2004:2005:2006:2007:1178"},
        {"su_index": 4, "ip": "2001:2002:2003:2004:2005:2006:2007:113d"},
        {"su_index": 5, "ip": "2001:2002:2003:2004:2005:2006:2007:1144"},
        {"su_index": 6, "ip": "2001:2002:2003:2004:2005:2006:2007:1186"},
        {"su_index": 7, "ip": "2001:2002:2003:2004:2005:2006:2007:111b"},
    ]

    hosts = resolve_cpe_hosts_for_run(profile, detected, su_count=6)

    assert len(hosts) == 6
    assert "2001:2002:2003:2004:2005:2006:2007:111b" not in hosts


def test_resolve_cpe_hosts_uses_live_only_without_profile_padding():
    profile = [
        "2001:2002:2003:2004:2005:2006:2007:11c9",
        "2001:2002:2003:2004:2005:2006:2007:118b",
        "2001:2002:2003:2004:2005:2006:2007:1178",
        "2001:2002:2003:2004:2005:2006:2007:113d",
    ]
    detected = [
        {"su_index": 1, "ip": "192.168.2.233"},
    ]

    hosts = resolve_cpe_hosts_for_run(profile, detected, su_count=6)

    assert hosts == ["192.168.2.233"]


def test_resolve_cpe_hosts_falls_back_to_profile_when_no_live_ips():
    profile = [
        "2001:2002:2003:2004:2005:2006:2007:11c9",
        "2001:2002:2003:2004:2005:2006:2007:118b",
    ]
    hosts = resolve_cpe_hosts_for_run(profile, [], su_count=6)
    assert hosts == profile


def test_resolve_cpe_hosts_rejects_sshpass_noise_as_live_ip():
    profile = ["2001:2002:2003:2004:2005:2006:2007:11c9"]
    detected = [
        {"su_index": 1, "ip": "/bin/sh: 1: sshpass: not found", "ipv6": "/bin/sh: 1: sshpass: not found"},
    ]
    hosts = resolve_cpe_hosts_for_run(profile, detected, su_count=6)
    assert hosts == profile
