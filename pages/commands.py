class RootCommands:
    """Linux backend commands executed via SSH as 'root'."""
    GET_MODEL = "cat /etc/ademodel"
    GET_HW_VERSION = "cat /etc/hwver"
    GET_BOOTLOADER = "cat /etc/blver"
    GET_TIME = "date"
    GET_TEMP = "tmp101"
    GET_GPS = ""
    GET_ELEVATION = ""
    GET_CPU = "cat /tmp/cpu_usage"
    GET_MEM = "cat /tmp/mem_usage"
    GET_IPv4 = "ucidyn get network.lan.ipaddr"
    GET_IPv6 = "ucidyn get network.lan.ip6addr"
    GET_GATEWAYv4 = "ucidyn get network.lan.gateway"
    GET_GATEWAYv6 = "ucidyn get network.lan.ip6gw"

    # --- PERFORMANCE COMMANDS (R1) ---
    GET_TX_R1 = "cat /sys/class/kwn/wifi1/statistics/tx_tput"
    GET_RX_R1 = "cat /sys/class/kwn/wifi1/statistics/rx_tput"

    # --- DYNAMIC LAN COMMANDS ---
    @staticmethod
    def get_mac_lan(eth_index):
        return f"cat /sys/class/net/eth{eth_index}/address"

    @staticmethod
    def get_speed_lan(eth_index):
        return f"cat /tmp/kwneth{eth_index}/speed"

    @staticmethod
    def get_duplex_lan(eth_index):
        return f"cat /tmp/kwneth{eth_index}/duplex"

    @staticmethod
    def get_cable_length_lan(eth_index):
        return f"cat /tmp/kwneth{eth_index}/cablelen"

    @staticmethod
    def get_tx_lan(eth_index):
        return f"cat /tmp/kwneth{eth_index}/tx_tput"

    @staticmethod
    def get_rx_lan(eth_index):
        return f"cat /tmp/kwneth{eth_index}/rx_tput"

    # --- DYNAMIC WIRELESS COMMANDS ---
    @staticmethod
    def get_radio_status(radio_idx):
        return f"uci show wireless.@wifi-iface[{radio_idx}].disabled"

    @staticmethod
    def get_mac_wireless(radio_idx):
        return f"ifconfig ath{radio_idx}"

    @staticmethod
    def get_link_type(radio_idx):
        return f"uci show wireless.wifi{radio_idx}.linktype"

    @staticmethod
    def get_radio_mode(radio_idx):
        return f"uci show wireless.@wifi-iface[{radio_idx}].mode"

    @staticmethod
    def get_bandwidth(radio_idx):
        return f"cfg80211tool ath{radio_idx} get_mode"

    @staticmethod
    def get_ssid(radio_idx):
        return f"uci show wireless.@wifi-iface[{radio_idx}].ssid"

    @staticmethod
    def get_configured_channel(radio_idx):
        return f"uci show advwireless.ath1{radio_idx}.channel"

    @staticmethod
    def get_active_channel(radio_idx):
        return f"iwconfig ath{radio_idx}"

    @staticmethod
    def get_security(radio_idx):
        return f"uci show wireless.@wifi-iface[{radio_idx}].encryption"

    @staticmethod
    def get_rtx_percentage(radio_idx):
        return f"cat /sys/class/kwn/wifi{radio_idx}/statistics/avg_rtx"

    @staticmethod
    def get_remote_partners(radio_idx):
        return f"cat /sys/class/kwn/wifi{radio_idx}/statistics/links"