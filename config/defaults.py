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
    "Maximum SUs": "16",

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