from traffic.kwn_sua_statistics import (
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
        "comb_rssi": "-52",
        "r_comb_rssi": "-55",
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
    assert client["tx_rate"] == "1201 (23)"
    assert client["rx_rate"] == "1080 (22)"
    assert client["operating_mcs"] == "22"


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
