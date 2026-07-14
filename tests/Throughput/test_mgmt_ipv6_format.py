from utils.net_utils import format_mgmt_ipv6_display
from utils.regression_report import _render_testbed_summary_table


def test_format_mgmt_ipv6_display_bts():
    assert (
        format_mgmt_ipv6_display(
            "BTS",
            "2001:2002:2003:2004:2005:2006:2007:1111",
            prefix_len=120,
        )
        == "BTS2001:2002:2003:2004:2005:2006:2007:1111/120"
    )


def test_format_mgmt_ipv6_display_rejects_uci_noise():
    assert format_mgmt_ipv6_display("BTS", "uci: Entry not found") == "—"
    assert format_mgmt_ipv6_display("BTS", "uci: Entry not found/120") == "—"


def test_testbed_summary_ip_row_uses_compact_format():
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
                "ip": "2001:2002:2003:2004:2005:2006:2007:111b",
                "vlan": "—",
                "qos": "—",
            }
        ],
    }
    html = _render_testbed_summary_table(summary)
    assert "BTS2001:2002:2003:2004:2005:2006:2007:1111/120" in html
    assert "SU12001:2002:2003:2004:2005:2006:2007:111b/120" in html
