from scripts.channel_connectivity import (
    _radio_matches,
    active_channel_number,
    apply_commands,
    channels_for_bandwidth,
    next_channels,
    parse_channels_json,
)


CATALOG = {
    "val": {
        "5019": {
            "HT20": ["36", "40", "176"],
            "HT40+": ["36", "40"],
            "HT80": ["36", "149"],
        }
    }
}


def test_parse_channel_selection_keeps_requested_order():
    from scripts.channel_connectivity import parse_channel_selection

    available = [36, 40, 44, 48]
    assert parse_channel_selection("all", available) == available
    assert parse_channel_selection("", available) == available
    assert parse_channel_selection("48, 36, 36", available) == [48, 36]


def test_channels_follow_bandwidth_and_ht40_plus_key():
    assert channels_for_bandwidth(CATALOG, "5019", "HT20") == [36, 40, 176]
    assert channels_for_bandwidth(CATALOG, "5019", "HT40") == [36, 40]
    assert channels_for_bandwidth(CATALOG, "5019", "HT80") == [36, 149]


def test_parse_channels_json_ignores_ssh_banner():
    raw = "OpenWrt\n" + '{"val": {"5019": {"HT20": ["36"]}}}'
    parsed = parse_channels_json(raw)
    assert channels_for_bandwidth(parsed, "5019", "HT20") == [36]


def test_apply_sets_htmode_only_on_bandwidth_change():
    first = apply_commands(1, "HT80", 36)
    later = apply_commands(1, None, 40)
    assert "ucidyn set advwireless.ath1.htmode HT80" in first
    assert "ucidyn set advwireless.ath1.channel 36" in first
    assert first.endswith("ucidyn apply && echo APPLY_DONE")
    assert "htmode" not in later
    assert "ucidyn set advwireless.ath1.channel 40" in later
    assert "wireless.wifi" not in first
    assert "wireless.wifi" not in later


def test_next_channels_follow_the_current_one():
    assert next_channels([36, 37, 38, 39, 40, 41, 42], 39, 3) == [40, 41, 42]


def test_ht40_apply_uses_ht40_plus():
    command = apply_commands(1, "HT40", 52)
    assert "advwireless.ath1.htmode HT40+" in command


def test_pass_uses_advwireless_htmode_not_cfg80211tool():
    radio = {
        "htmode": "HT20",
        "channel": "36",
        "active": "36",
        "running": "HT80",
    }
    assert _radio_matches(radio, "HT20", 36) is True


def test_active_channel_from_iwconfig_frequency():
    text = "ath1  IEEE 802.11ax  ESSID:\"lab\"\n          Frequency:5.88 GHz"
    assert active_channel_number(text) == 176


def test_unassociated_frequency_is_not_a_channel():
    text = "Frequency:5.175 GHz  Access Point: Not-Associated"
    assert active_channel_number(text) is None
