class RootCommands:
    """Linux backend commands executed via SSH as 'root'."""

    # --- SYSTEM INFORMATION ---
    GET_MODEL = "cat /etc/ademodel"
    GET_HW_VERSION = "cat /etc/hwver"
    GET_BOOTLOADER = "cat /etc/blver"
    GET_TIME = "date"
    GET_TEMP = "tmp101"
    GET_GPS = ""
    GET_ELEVATION = ""
    GET_CPU = "cat /tmp/cpu_usage"
    GET_MEM = "cat /tmp/mem_usage"

    # --- NETWORK CONFIGURATION (Used for GUI_50 Validation) ---
    # These 'uci' commands fetch the actual saved settings
    GET_NET_PROTO = "uci get network.lan.proto"
    GET_NET_IP = "uci get network.lan.ipaddr"
    GET_NET_MASK = "uci get network.lan.netmask"
    GET_NET_GW = "uci get network.lan.gateway"
    GET_NET_IP6 = "uci get network.lan.ip6addr"
    GET_NET_GW6 = "uci get network.lan.ip6gw"

    # --- NETWORK DYNAMIC STATUS (Used for Summary Pages) ---
    # These 'ucidyn' commands fetch live/assigned status
    GET_SYSNAME = "uci get system.@system[0].hostname"
    GET_SW_VERSION = "cat /etc/version; echo"
    GET_SERIAL_NO = "fw_printenv -n dsn"
    GET_UPTIME = "cat /proc/uptime"

    GET_IPv4 = "ucidyn get network.lan.ipaddr"
    GET_IPv6 = "ucidyn get network.lan.ip6addr"
    GET_GATEWAYv4 = "ucidyn get network.lan.gateway"
    GET_GATEWAYv6 = "ucidyn get network.lan.ip6gw"

    # --- PERFORMANCE COMMANDS (R1) ---
    GET_TX_R1 = "cat /sys/class/kwn/wifi1/statistics/tx_tput"
    GET_RX_R1 = "cat /sys/class/kwn/wifi1/statistics/rx_tput"

    # --- SYSTEM / TIMEZONE ---
    GET_TIMEZONE = "uci get system.@system[0].timezone"
    GET_LOCAL_TIME = "date"

    # Optional: Command to check logs for the timestamp verify
    GET_LOGS = "logread | tail -n 20"

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
        return f"uci get wireless.@wifi-iface[{radio_idx}].disabled"

    @staticmethod
    def get_mac_wireless(radio_idx):
        return f"ifconfig ath{radio_idx}"

    @staticmethod
    def get_link_type(radio_idx):
        return f"uci get wireless.wifi{radio_idx}.linktype"

    @staticmethod
    def get_radio_mode(radio_idx):
        return f"uci get wireless.@wifi-iface[{radio_idx}].mode"

    @staticmethod
    def get_bandwidth(radio_idx):
        return f"cfg80211tool ath{radio_idx} get_mode"

    @staticmethod
    def get_ssid(radio_idx):
        return f"uci get wireless.@wifi-iface[{radio_idx}].ssid"

    @staticmethod
    def get_configured_channel(radio_idx):
        return f"uci get advwireless.ath{radio_idx}.channel"

    @staticmethod
    def get_active_channel(radio_idx):
        return f"iwconfig ath{radio_idx}"

    @staticmethod
    def get_security(radio_idx):
        # Used for both getting Security protocol and verifying Encryption
        return f"uci get wireless.@wifi-iface[{radio_idx}].encryption"

    @staticmethod
    def get_rtx_percentage(radio_idx):
        return f"cat /sys/class/kwn/wifi{radio_idx}/statistics/avg_rtx"

    @staticmethod
    def get_remote_partners(radio_idx):
        return f"cat /sys/class/kwn/wifi{radio_idx}/statistics/links"

    @staticmethod
    def get_encryption_key(radio_idx):
        return f"uci get wireless.@wifi-iface[{radio_idx}].key"

    @staticmethod
    def get_network_secret(radio_idx):
        return f"uci get wireless.wifi{radio_idx}.nwksecret"

    @staticmethod
    def get_distance(radio_idx):
        return f"uci get wireless.wifi{radio_idx}.distance"

    @staticmethod
    def get_maxcpe(radio_idx):
        return f"uci get wireless.@wifi-iface[{radio_idx}].maxsta"