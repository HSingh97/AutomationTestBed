from traffic.kwn_sua_statistics import (
    _parse_bulk_kwn_output,
    fetch_kwn_sua_statistics,
    is_sua_associated,
    normalize_kwn_sua_client,
    resolve_sua_display_ip,
)


def test_resolve_sua_display_ip_prefers_ipv4():
    assert resolve_sua_display_ip(ipv4="192.168.1.10", ipv6="2001::1") == "192.168.1.10"


def test_resolve_sua_display_ip_falls_back_to_ipv6_when_ipv4_zero():
    ip = resolve_sua_display_ip(
        ipv4="0.0.0.0",
        ipv6="2001:2002:2003:2004:2005:2006:2007:11c9",
    )
    assert "2001:2002" in ip


def test_resolve_sua_display_ip_rejects_sshpass_noise():
    noise = "/bin/sh: 1: sshpass: not found"
    assert resolve_sua_display_ip(ipv4=noise, ipv6=noise) == ""
    assert resolve_sua_display_ip(ipv4=noise, ipv6="2001::1") == "2001::1"


def test_is_sua_associated_ignores_ssh_noise_as_ip():
    noise = "/bin/sh: 1: sshpass: not found"
    assert is_sua_associated({"ip": noise, "ipv6": noise}) is False
    assert is_sua_associated({"ip": noise, "mac": "aa:bb:cc:dd:ee:ff"}) is True


def test_is_sua_associated_requires_identity_or_traffic():
    assert is_sua_associated({"ipv6": "2001::1"}) is True
    assert is_sua_associated({"rx_rate": "1080"}) is True
    assert is_sua_associated({"l_snra1": "0"}) is False


def test_normalize_maps_snra_rates_and_mcs():
    raw = {
        "sua_index": 4,
        "ip": "0.0.0.0",
        "ipv6": "2001:2002:2003:2004:2005:2006:2007:113d",
        "l_snra1": "54",
        "l_snra2": "52",
        "r_snra1": "50",
        "r_snra2": "49",
        "comb_rssi": "62",
        "r_comb_rssi": "63",
        "tx_rate": "1201",
        "rx_rate": "1080",
        "tx_rate_mcs": "23",
        "rx_rate_mcs": "22",
        "r_custname": "cpe-lab-4",
    }
    client = normalize_kwn_sua_client(raw, display_index=4)
    assert client["system_name"] == "cpe-lab-4"
    assert "2001:2002" in client["ip"]
    assert client["l_snr1"] == "54"
    assert client["l_rssi1"] == "-62"
    assert client["l_rssi2"] == "—"
    assert client["r_rssi1"] == "-63"
    assert client["tx_rate"] == "1201 (23)"
    assert client["rx_rate"] == "1080 (22)"
    assert client["operating_mcs"] == "22"


def test_format_rssi_dbm_keeps_negative_values():
    from traffic.kwn_sua_statistics import format_rssi_dbm

    assert format_rssi_dbm("-52") == "-52"
    assert format_rssi_dbm("62") == "-62"


def test_normalize_ignores_l_power_for_rssi_chain():
    raw = {
        "sua_index": 1,
        "comb_rssi": "62",
        "r_comb_rssi": "63",
        "l_power": "1",
        "r_power": "1",
        "rx_rate": "1201",
    }
    client = normalize_kwn_sua_client(raw, display_index=1)
    assert client["l_rssi1"] == "-62"
    assert client["l_rssi2"] == "—"
    assert client["r_rssi1"] == "-63"
    assert client["r_rssi2"] == "—"


def test_parse_bulk_kwn_output():
    raw = (
        "SUA_INDEX=1\n"
        "ipv6=2001::1\n"
        "tx_rate=1201\n"
        "---\n"
        "SUA_INDEX=3\n"
        "ipv6=2001::3\n"
        "rx_rate=1080\n"
        "---\n"
    )
    slots = _parse_bulk_kwn_output(raw)
    assert slots[1]["ipv6"] == "2001::1"
    assert slots[1]["tx_rate"] == "1201"
    assert slots[3]["rx_rate"] == "1080"


def test_fetch_kwn_sua_statistics_uses_injected_reader():
    fields = {
        "/sys/class/kwn/sua1/statistics/ipv6": "2001::11c9",
        "/sys/class/kwn/sua1/statistics/rx_rate": "1201",
        "/sys/class/kwn/sua1/statistics/l_snra1": "38",
        "/sys/class/kwn/sua1/statistics/l_snra2": "37",
        "/sys/class/kwn/sua2/statistics/ipv6": "",
        "/sys/class/kwn/sua2/statistics/l_snra1": "0",
    }

    def _read(path: str) -> str:
        return fields.get(path, "-")

    clients = fetch_kwn_sua_statistics(
        "10.0.0.1",
        read_field=_read,
        max_sua=3,
    )
    assert len(clients) == 1
    assert clients[0]["su_index"] == 1
    assert clients[0]["l_snr1"] == "38"
