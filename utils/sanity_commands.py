"""UCI / shell commands used by the Sanity suite only (no shared suite imports)."""

from __future__ import annotations


class SanityCommands:
    """SSH commands for config snapshot and model detection."""

    GET_NET_PROTO = "uci -q get network.lan.proto"
    GET_NET_IP = "uci -q get network.lan.ipaddr"
    GET_NET_MASK = "uci -q get network.lan.netmask"
    GET_NET_GW = "uci -q get network.lan.gateway"
    GET_NET_IP6 = "uci -q get network.lan.ip6addr"
    GET_NET_GW6 = "uci -q get network.lan.ip6gw"
    GET_HOSTNAME = "uci -q get system.@system[0].hostname"
    GET_LOCATION_SYSTEM_NAME = "uci -q get system.@system[0].cusname"
    GET_FW_VERSION = "cat /etc/version 2>/dev/null"
    GET_VLAN_MODE = "uci -q get vlan.ath1.mode"
    GET_VLAN_MGMT = "uci -q get vlan.ath1.mgmtvlan"

    @staticmethod
    def get_ssid(radio_idx: int = 1) -> str:
        return f"uci -q get wireless.@wifi-iface[{radio_idx}].ssid"

    @staticmethod
    def get_encryption(radio_idx: int = 1) -> str:
        return f"uci -q get wireless.@wifi-iface[{radio_idx}].encryption"

    @staticmethod
    def get_key(radio_idx: int = 1) -> str:
        return f"uci -q get wireless.@wifi-iface[{radio_idx}].key"

    @staticmethod
    def get_nwksecret(radio_idx: int = 1) -> str:
        return f"uci -q get wireless.wifi{radio_idx}.nwksecret"


# Keys captured before/after keep-settings / without-keep upgrades.
CONFIG_SNAPSHOT_FIELDS: tuple[tuple[str, str], ...] = (
    ("network.lan.proto", SanityCommands.GET_NET_PROTO),
    ("network.lan.ipaddr", SanityCommands.GET_NET_IP),
    ("network.lan.netmask", SanityCommands.GET_NET_MASK),
    ("network.lan.gateway", SanityCommands.GET_NET_GW),
    ("network.lan.ip6addr", SanityCommands.GET_NET_IP6),
    ("network.lan.ip6gw", SanityCommands.GET_NET_GW6),
    ("wireless.ssid", SanityCommands.get_ssid(1)),
    ("wireless.encryption", SanityCommands.get_encryption(1)),
    ("wireless.key", SanityCommands.get_key(1)),
    ("wireless.nwksecret", SanityCommands.get_nwksecret(1)),
    ("vlan.ath1.mode", SanityCommands.GET_VLAN_MODE),
    ("vlan.ath1.mgmtvlan", SanityCommands.GET_VLAN_MGMT),
    ("system.hostname", SanityCommands.GET_HOSTNAME),
)

MODEL_PROBES: tuple[str, ...] = (
    "cat /etc/ademodel 2>/dev/null",
    "grep -oE 'UBR[0-9]{3}' /etc/board.json 2>/dev/null | head -1",
    "uci -q get system.@system[0].model 2>/dev/null",
    "uci -q get system.@system[0].hostname 2>/dev/null",
    "hostname 2>/dev/null",
    # Last-resort: SSID / product strings often embed UBR###.
    "uci -q get wireless.@wifi-iface[1].ssid 2>/dev/null",
    "grep -oE 'UBR[0-9]{3}' /etc/config/wireless 2>/dev/null | head -1",
)
