# config/defaults.py

"""
Single Source of Truth for all device factory default values.
The keys here MUST match the 'param_name' passed into the UI helpers.
"""

DEFAULT_VALUES = {
    # Wireless -> Radio Properties
    "Status": "Enable",
    "Link Type": "PTMP",
    "Radio Mode": "BTS",
    "SSID": "Senao_Default_SSID",
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
        "client_script": "master_script_extended_16SU.py",
        "ports": "0,1",
        "server_cores": 4,
    }
}


CAPTURE_DEFAULTS = {
    "enabled": False,
    "username": "root",
    "password": "senao1234#",
    "tool": "tcpdump",
    "artifact_dir": "logs/jumbo_captures",
    "remote_tmp_dir": "/tmp/ubr_jumbo_captures",
}