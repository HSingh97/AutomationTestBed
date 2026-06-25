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
    GET_FALLBACK_IP = "uci get fallback.lan.ipaddr"
    GET_FALLBACK_MASK = "uci get fallback.lan.netmask"
    GET_VLAN_MGMT = "uci get vlan.ath1.mgmtvlan"
    GET_VLAN_SVLAN = "uci get vlan.ath1.svlan"
    GET_VLAN_CVLAN = "uci get vlan.ath1.cvlan"
    GET_VLAN_MODE = "uci get vlan.ath1.mode"
    GET_DHCP_IGNORE = "uci get dhcp.lan.ignore"
    GET_DHCP_LEASE = "uci get dhcp.lan.leasetime"
    GET_LAN24_IP = "uci get network.lan24.ipaddr"
    GET_LAN24_MASK = "uci get network.lan24.netmask"
    GET_LAN24_DHCP_IGNORE = "uci get dhcp.lan24.ignore"
    GET_LAN24_START = "uci get dhcp.lan24.start"
    GET_LAN24_LIMIT = "uci get dhcp.lan24.limit"
    GET_LAN24_LEASE = "uci get dhcp.lan24.leasetime"

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
    GET_BRIDGE_FDB = "brctl showmacs br-lan"
    GET_ARP_TABLE = "arp"
    GET_CONFIG_LOGS = "sed -n '1,200p' /etc/config_logs 2>/dev/null"
    GET_DEVICE_LOGS = "sed -n '1,200p' /etc/device_logs 2>/dev/null"
    GET_DEVICE_LOGS_TAIL = "tail -n 150 /etc/device_logs 2>/dev/null"
    GET_DEVICE_LOGS_REBOOT_GREP = (
        "grep -iE 'reboot|restart|reset|power|boot|watchdog' /etc/device_logs 2>/dev/null | tail -n 60"
    )
    GET_LOGREAD_REBOOT_GREP = (
        "logread 2>/dev/null | grep -iE "
        "'reboot|restart|kernel|procd|init|jffs2|watchdog|sysinit|software reset|umount' | tail -n 60"
    )
    GET_LOGREAD_WIRELESS_GREP = (
        "logread 2>/dev/null | grep -iE "
        "'wifi|wireless|ath|link|network|reload|partner|disconnect|connect|kwn' | tail -n 60"
    )
    GET_DEVICE_LOGS_WIRELESS_GREP = (
        "grep -iE 'wifi|wireless|ath|link|network|reload|partner|disconnect|connect|kwn' "
        "/etc/device_logs 2>/dev/null | tail -n 60"
    )
    GET_TEMPERATURE_LOGS = "sed -n '1,200p' /tmp/temp-log 2>/dev/null"
    GET_SYSTEM_LOGS = "logread"

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
    def get_link_stat_field(radio_idx, assoc_idx, field):
        return (
            f"cat /sys/class/kwn/wifi{radio_idx}/statistics/sua{assoc_idx}/{field} "
            f"2>/dev/null || echo -"
        )

    @staticmethod
    def get_link_stat_associd(radio_idx, assoc_idx):
        return (
            f"cat /sys/class/kwn/wifi{radio_idx}/statistics/sua{assoc_idx}/assoc "
            f"2>/dev/null || echo 0"
        )

    @staticmethod
    def get_wifi_events_log(radio_idx: int) -> str:
        return f"cat /tmp/kwn-wifi{radio_idx}-events.log 2>/dev/null"

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

    @staticmethod
    def get_dl_ul_ratio(radio_idx):
        return f"uci get ath{radio_idx}qos.qoscfg.dlulratio"

    @staticmethod
    def get_ddrs_status(radio_idx):
        return f"uci get txparam.ath{radio_idx}.ddrsstatus"

    @staticmethod
    def get_spatial_stream(radio_idx):
        return f"uci get txparam.ath{radio_idx}.spatialstream"

    @staticmethod
    def get_ddrs_rate(radio_idx):
        return f"uci get txparam.ath{radio_idx}.ddrsrate"

    @staticmethod
    def get_atpc_status(radio_idx):
        return f"uci get txparam.ath{radio_idx}.atpcstatus"

    @staticmethod
    def get_tx_power(radio_idx):
        return f"uci get txparam.ath{radio_idx}.atpcpower"

    @staticmethod
    def get_max_eirp(radio_idx):
        return f"uci get txparam.ath{radio_idx}.maxeirp"

    # --- THROUGHPUT CONFIG COMMANDS ---
    @staticmethod
    def mcs_ucidyn_set_commands(radio_idx, mcs_rate, spatial_stream, ddrs_rate):
        """UCI set commands only — caller applies once via remote_exec broadcast."""
        modulation_rate = ddrs_rate if ddrs_rate is not None else mcs_rate
        return [
            f"ucidyn set txparam.ath{radio_idx}.ddrsstatus 0",
            f"ucidyn set txparam.ath{radio_idx}.spatialstream {spatial_stream}",
            f"ucidyn set txparam.ath{radio_idx}.ddrsrate {modulation_rate}",
        ]

    @staticmethod
    def set_mcs_sequence_commands(radio_idx, mcs_rate, spatial_stream, ddrs_rate):
        """ddrs_rate should be the numeric UCI index (e.g. 23 for MCS23)."""
        return RootCommands.mcs_ucidyn_set_commands(
            radio_idx, mcs_rate, spatial_stream, ddrs_rate
        ) + ["ucidyn apply"]

    @staticmethod
    def set_bandwidth_commands(radio_idx, bandwidth):
        return [
            f"ucidyn set wireless.wifi{radio_idx}.htmode {bandwidth}",
            "ucidyn apply",
        ]

    @staticmethod
    def set_dl_ul_ratio_commands(radio_idx, dl_ul_percent: str):
        return [
            f"ucidyn set ath{radio_idx}qos.qoscfg.dlulratio {dl_ul_percent}",
            "ucidyn apply",
        ]

    @staticmethod
    def set_bandwidth_ratio_apply_commands(radio_idx, bandwidth, dl_ul_percent: str):
        """Set htmode + DL:UL ratio, then single ucidyn apply (no remote_exec for BTS bw)."""
        return [
            f"ucidyn set wireless.wifi{radio_idx}.htmode {bandwidth}",
            f"ucidyn set ath{radio_idx}qos.qoscfg.dlulratio {dl_ul_percent}",
            "ucidyn apply",
        ]

    @staticmethod
    def remote_exec_command(su_index: int, command: str) -> str:
        """SET/apply on CPE via BTS RF path — do not use for UCI get / verification."""
        safe = str(command).replace('"', '\\"')
        return f'/usr/sbin/remote_exec.sh {su_index} "{safe}"'

    @staticmethod
    def remote_apply_all_su():
        return RootCommands.remote_exec_command(1, "ucidyn apply")

    @staticmethod
    def emit_system_log_marker(marker: str):
        safe_marker = str(marker).replace("'", "'\"'\"'")
        return f"logger -t cursor_monitor '{safe_marker}'"