# config/defaults.py

"""
Single Source of Truth for all device factory default values.
The keys here MUST match the 'param_name' passed into the UI helpers.
"""

# Lab P2MP link SSID — BTS and CPE must match or the link drops between GUI tests.
LINK_SSID = "ATUMNAWJ"

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
    "Transmit Power": "26",
    "Maximum EIRP": "0",

    # You can expand this as you build out other pages!
    # "IP Address": "192.168.1.1",
    # "Traffic Shaping Status": "Disable",
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
    "artifact_dir": "logs",
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


CAPTURE_DEFAULTS = {
    "enabled": False,
    "username": "root",
    "password": "senao1234#",
    "tool": "tcpdump",
    "artifact_dir": "logs/jumbo_captures",
    "remote_tmp_dir": "/tmp/ubr_jumbo_captures",
}