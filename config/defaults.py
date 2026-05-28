# config/defaults.py

"""
Single Source of Truth for all device factory default values.
The keys here MUST match the 'param_name' passed into the UI helpers.
"""

# Fallback SSID when link.auto_credentials is false (default: use AIRTEL_SSID_GEN).
LINK_SSID = "ATUMNAWJ"

LINK_DEFAULTS = {
    "auto_credentials": True,
    "cpe_fallback_ipv4": "10.0.0.1",
    "always_apply_cpe_credentials": True,
    "airtel_gen_path": "",
    "radio_type": "5G",
    "auth_mode": "32",
    "encryption_mode": "8",
    "radio_idx": 1,
    "cpe_radio_idx": 1,
    "bts_serial": "",
    "ssid": "",
    "expected_mode": "linked",
    "min_connected_clients": 1,
    "health_check_timeout_s": 60,
}

DEFAULT_VALUES = {
    # Wireless -> Radio Properties
    "Status": "Enable",
    "Link Type": "PTMP",
    "Radio Mode": "BTS",
    "SSID": LINK_SSID,
    "Bandwidth": "20 MHz",
    "DL/UL Ratio": "Auto",
    "Maximum SUs": "16",
    "DDRS Status": "Enable",
    "Spatial Stream": "Auto",
    "ATPC Status": "Disable",
    "Transmit Power": "1",
    "Maximum EIRP": "0",

    "Syslog IP": "",
    "Temp Log Interval": "30",
    "Location Name": "UBR-Lab",

    # You can expand this as you build out other pages!
    # "IP Address": "192.168.1.1",
    # "Traffic Shaping Status": "Disable",
}

RADIO_24_TEST_VALUES = {
    "SSID_VALID": "UBR_24G_Auto_Test_SSID",
    "SSID_INVALID": "An_Invalid_33_Character_SSID_1234567",
    "KEY_VALID": "Senao24GTestKey!01",
    "KEY_INVALID": "x",
}

RADIO_TEST_VALUES = {
    "SSID_VALID": "A_Valid_32_Character_SSID_123456",
    "SSID_INVALID": "An_Invalid_33_Character_SSID_1234567",
    "MAX_CPE_VALID": "10",
    "MAX_CPE_INVALID": "34",
    "CHANNEL_BTS_VALUES": ["36", "149", "165"],
    "DL_UL_RATIO_VALUES": ["50/50", "80/20"],
    "DDRS_STATUS_VALUES": ["Disable", "Enable"],
    "SPATIAL_STREAM_VALUES": ["Single", "Dual"],
    "MODULATION_INDEX_VALUES": ["MCS12 ( 72 Mbps )", "MCS23 ( 1201 Mbps )"],
    "TRANSMIT_POWER_VALUES": ["1", "26"],
    "TRANSMIT_POWER_INVALID": "27",
    "MAX_EIRP_VALID": "10",
    "MAX_EIRP_INVALID": "101",
    "TREX_BEHAVIOR_DURATION_S": 10,
    "TREX_BEHAVIOR_DL_BW": "200M",
    "TREX_BEHAVIOR_UL_BW": "200M",
}


TRAFFIC_DEFAULTS = {
    "trex": {
        "host": "192.168.3.3",
        "user": "root",
        "password": "ubuntu",
        "directory": "/opt/v3.06",
        "pythonpath": "/opt/v3.06/automation/trex_control_plane/interactive/",
        "client_script": "master_script_extended_16SU.py",  # bundled at traffic/scripts/
        "ports": "0,1",
        "server_cores": 1,
        "server_startup_s": 25,
    }
}


PERFORMANCE_DEFAULTS = {
    "bandwidths": ["HT20", "HT40", "HT80", "HT160"],
    "mcs_rates": [f"MCS{i}" for i in range(24)],
    "ratios": ["80:20", "50:50", "70:30", "75:25"],
    "target_mbps": 800,
    "efficiency_factor": 0.75,
    "use_dynamic_target": True,
    "phy_max_rate_mbps": {},
    "duration_s": 30,
    "radio_index": 1,
    "cpe_radio_index": 1,
    "cpe_su_index": 1,
    "snmp_radio_index": 2,
    "link_wait_s": 45,
    "su_count": 1,
    "spatial_stream": "2",
    "snmp_community": "ubr@rw123",
    "noise_dbm": "-93",
    "packet_size": 1500,
    "profile": "default",
    "recovery_profile": "link_formation",
    "artifact_dir": "reports/artifacts",
    "mcs_traffic_cap_mbps": {
        "MCS0": 80,
        "MCS1": 120,
        "MCS2": 180,
        "MCS3": 260,
        "MCS4": 350,
        "MCS5": 450,
        "MCS6": 550,
        "MCS7": 650,
        "MCS8": 780,
        "MCS9": 900,
        "MCS10": 1050,
        "MCS11": 1200,
    },
}


ATTENUATOR_DEFAULTS = {
    "enabled": False,
    "backend": "auto",  # auto | dll | mock
    "dll_path": "",
    "settle_seconds": 3.0,
    "min_attenuation_db": 0.0,
    "max_attenuation_db": 63.0,
    "su_index": 0,
    "snmp_community": "ubr@rw123",
    "snmp_radio_idx": 2,
    "chains": [
        {"chain": 0, "device_id": None, "serial": None, "lab_brick_id": None, "label": "MIMO chain 0"},
        {"chain": 1, "device_id": None, "serial": None, "lab_brick_id": None, "label": "MIMO chain 1"},
    ],
}

IP_TEST_DEFAULTS = {
    "enabled": False,
    "ipv4_address": "192.168.2.1",
    "ipv4_netmask": "255.255.255.0",
    "ipv4_gateway": "192.168.2.254",
    "ipv6_address": "",
    "ipv6_gateway": "",
    "ping_count_short": 4,
    "ping_count_long": 100,
    "max_ping_loss_pct": 1.0,
    "mtu_test_value": 1400,
    "mtu_restore_value": 1500,
    "gui_settle_seconds": 12,
    "reboot_timeout_s": 200,
    "network_reload_wait_s": 30,
    "iface_up_wait_s": 15,
    "iperf_duration_s": 10,
    "iperf_server_v4": "",
    "iperf_server_v6": "",
    "backup_archive_path": "",
    "firmware_image_path": "",
    "static_route_cidr": "",
    "ipv6_link_local_iface": "eth0",
    "ipv6_link_local_peer": "",
    "fallback_ipv4": "",
    "fallback_ipv6": "",
    "reachability_use_device_fallback": True,
    "ssh_connect_attempts": 3,
    "ssh_connect_retry_interval_s": 15,
    "remote_ping_retries": 5,
    "remote_ping_retry_interval_s": 10,
    "post_reboot_remote_ping_retries": 12,
    "post_reboot_remote_ping_interval_s": 10,
    "post_reload_remote_ping_retries": 8,
}

TESTBED_DEFAULTS = {
    "enabled": True,
    "bootstrap_on_start": True,
    "configure_vlan_modes": True,
    "strict_ipv6": True,
    "recovery": {
        "bts_fallback_ipv4": "10.0.0.1",
        "bootstrap_fallback_ipv4s": [],
    },
    "bootstrap_fallback_ipv4": "10.0.0.1",
    "bootstrap_fallback_ipv4s": [],
    "factory_defaults": {
        "bts_vlan_mode": "qinq",
        "cpe_vlan_mode": "transparent",
    },
    "qinq": {"svlan": 100, "cvlan": 101},
    "mgmt_vlan": {
        "prefix_len": 64,
        "uci_key": "vlan.ath1.mgmtvlan",
        "uci_value": 101,
        "lab_pc_vlan_id": 101,
        "ipv6_bts": "",
        "ipv6_cpe": "",
        "ipv6_bts_pc": "",
        "ipv6_cpe_pc": "",
        "set_on_cpe": False,
        "bts_apply_commands": [],
        "cpe_apply_commands": [],
    },
    "vlan_uci": {
        "bts_radio": "ath1",
        "cpe_radio": "ath1",
        "bts": {
            "mode_key": "vlan.ath1.mode",
            "mode_value": "qinq",
            "svlan_key": "vlan.ath1.svlan",
            "cvlan_key": "vlan.ath1.cvlan",
            "mgmtvlan_key": "vlan.ath1.mgmtvlan",
        },
        "cpe": {
            "mode_key": "vlan.ath1.mode",
            "mode_value": "transparent",
        },
    },
    "lab_pc_tagging": {
        "bts": {"mode": "qinq", "svlan": 100, "cvlan": 101},
        "cpe": {"mode": "untagged"},
    },
    "primary_pc": {
        "local": True,
        "mgmt_interface": "enp3s0",
        "fallback_ipv4": "10.0.0.10",
        "fallback_prefix_len": 8,
        "internet_ssh": "",
    },
    "secondary_pc": {
        "enabled": True,
        "ssh": "",
        "mgmt_interface": "enp3s0",
        "fallback_ipv4": "10.0.0.11",
        "fallback_prefix_len": 8,
        "cpe_factory_ipv4": "192.168.2.1",
    },
    "wifi": {
        "enabled": False,
        "interface": "wlan0",
        "ssid": "",
        "psk": "",
    },
    "vlan_ssh": {
        "bts": {"verify_commands": [], "modes": {}},
        "cpe": {"verify_commands": [], "modes": {}},
    },
    "cpe_discovery": {
        "radio_idx": 2,
        "lease_commands": [],
        "discover_commands": [],
    },
    "link_recovery": {
        "restore_bts": True,
        "restore_cpe": True,
        "bts_archive": "config/BTS.tar.gz",
        "cpe_archive": "config/CPE.tar.gz",
    },
}

CAPTURE_DEFAULTS = {
    "enabled": False,
    "username": "root",
    "password": "senao1234#",
    "tool": "tcpdump",
    "artifact_dir": "reports/artifacts/jumbo_captures",
    "remote_tmp_dir": "/tmp/ubr_jumbo_captures",
}