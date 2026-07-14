from utils.net_utils import format_mgmt_ipv6_display
from utils.regression_report import _render_testbed_summary_table


def test_format_mgmt_ipv6_display_bts():
    assert (
        format_mgmt_ipv6_display(
            "BTS",
            "2001:2002:2003:2004:2005:2006:2007:1111",
            prefix_len=120,
        )
        == "2001:2002:2003:2004:2005:2006:2007:1111"
    )


def test_format_mgmt_ipv6_display_ipv4_host_only():
    assert format_mgmt_ipv6_display("SU1", "192.168.2.32", prefix_len=120) == "192.168.2.32"
    assert format_mgmt_ipv6_display("SU1", "192.168.2.32/120") == "192.168.2.32"


def test_format_mgmt_ipv6_display_rejects_uci_noise():
    assert format_mgmt_ipv6_display("BTS", "uci: Entry not found") == "—"
    assert format_mgmt_ipv6_display("BTS", "uci: Entry not found/120") == "—"


def test_testbed_summary_ip_row_uses_host_only():
    summary = {
        "ipv6_prefix_len": 120,
        "bts": {
            "model": "UBR630",
            "fw_version": "2.4.1.0",
            "ip": "2001:2002:2003:2004:2005:2006:2007:1111",
            "vlan": "Enabled ( QinQ )",
            "qos": "75:25",
        },
        "cpes": [
            {
                "label": "SU1",
                "su_index": 1,
                "model": "UBR620",
                "fw_version": "2.4.1.0",
                "ip": "192.168.2.32",
                "vlan": "—",
                "qos": "—",
            }
        ],
    }
    html = _render_testbed_summary_table(summary)
    assert "2001:2002:2003:2004:2005:2006:2007:1111" in html
    assert "192.168.2.32" in html
    assert "BTS2001:" not in html
    assert "SU1192." not in html
    assert "/120" not in html
