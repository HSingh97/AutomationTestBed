from utils.channel_connectivity_report import write_channel_connectivity_report


def test_report_marks_passed_ping_and_leaves_other_channels_idle(tmp_path):
    rows = [
        {
            "bandwidth": "HT80",
            "channel": "46",
            "htmode_uci": "HT80",
            "active_channel": "46",
            "ping_ok": "yes",
            "ping_detail": "0% packet loss",
            "config_ok": "yes",
            "iwconfig": 'ath1  ESSID:"UBR650_R1_Lab"\nFrequency:5.23 GHz\nEncryption key:ABCD-EF01\n',
        },
        {
            "bandwidth": "HT80",
            "channel": "47",
            "htmode_uci": "HT80",
            "active_channel": "47",
            "ping_ok": "no",
            "ping_detail": "100% packet loss",
            "config_ok": "yes",
        },
    ]
    path = write_channel_connectivity_report(
        rows,
        tmp_path / "report.html",
        bts_ip="192.168.3.20",
        cpe_ip="192.168.3.21",
        country="5019",
        channels_by_bw={"HT80": [45, 46, 47, 48]},
        executed_at="2026-11-15 08:50 UTC",
    )
    html = path.read_text()
    assert "Channel connectivity report" in html
    assert "hero-shell" in html
    assert "1<span>/2</span>" in html
    assert "heat-pass" in html
    assert "heat-fail" in html
    assert "heat-empty" in html
    assert "192.168.3.21" in html
    assert "5.23 GHz" in html
    assert "ESSID" in html
    assert 'details class="iw-entry"' in html
    assert "Collapsed per channel" not in html
    assert "<details open" not in html
    assert "ABCD-EF01" not in html
    assert "Encryption key:[redacted]" in html
    assert "chart.js" not in html
    assert "coverageChart" not in html


def test_report_highlights_iwconfig_rate_that_is_not_the_bandwidth_max(tmp_path):
    rows = [
        {
            "bandwidth": "HT20",
            "channel": "36",
            "htmode_uci": "HT20",
            "active_channel": "36",
            "ping_ok": "yes",
            "ping_detail": "0% packet loss",
            "config_ok": "yes",
            "iwconfig": "ath1  ESSID:\"UBR650_R1_Lab\"\nBit Rate:2.4019 Gb/s\n",
        },
        {
            "bandwidth": "HT20",
            "channel": "40",
            "htmode_uci": "HT20",
            "active_channel": "40",
            "ping_ok": "yes",
            "ping_detail": "0% packet loss",
            "config_ok": "yes",
            "iwconfig": "ath1  ESSID:\"UBR650_R1_Lab\"\nBit Rate:286 Mb/s\n",
        },
    ]
    path = write_channel_connectivity_report(
        rows,
        tmp_path / "report.html",
        bts_ip="192.168.3.20",
        cpe_ip="192.168.3.21",
        country="5019",
        channels_by_bw={"HT20": [36, 40]},
        executed_at="2026-11-16 06:00 UTC",
    )
    html = path.read_text()
    assert "heat-warn" in html
    assert "heat-pass" in html
    assert "heat-cell heat-warn" in html
    assert "heat-rx'>✓" in html
    assert "2402" in html
    assert "Rate matches HT160" in html
    assert "HT20 max is 286 Mbps" in html
    assert "pill skip'>Rate" in html
