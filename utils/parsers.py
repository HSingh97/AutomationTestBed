import re
import datetime
import ipaddress


def parse_device_time(time_str):
    clean_str = re.sub(r'\s[A-Z]{3,4}\s', ' ', time_str)
    clean_str = re.sub(r'\s+', ' ', clean_str).strip()
    return datetime.datetime.strptime(clean_str, "%a %b %d %H:%M:%S %Y")


def extract_ip_objects(text):
    """Converts valid IP strings into mathematical objects to bypass compression differences."""
    potential_ips = re.findall(r'[a-fA-F0-9:\.]+', text)
    ip_objects = []
    for p in potential_ips:
        clean_p = p.strip('.:')
        try:
            ip_obj = ipaddress.ip_interface(clean_p).ip
            ip_objects.append(ip_obj)
        except ValueError:
            continue
    return ip_objects


def extract_uci_value(ssh_str):
    """Extracts value from 'uci show' output, e.g., key='value' -> value"""
    match = re.search(r"='?([^']*)'?", str(ssh_str))
    if match:
        return match.group(1).strip()
    return str(ssh_str).strip()


def parse_radio_status(ssh_str):
    val = extract_uci_value(ssh_str)
    if val == "0": return "Enable"
    if val == "1": return "Disable"
    return val


def parse_link_type(ssh_str):
    val = extract_uci_value(ssh_str)
    if val == "0": return "WI-FI"
    if val == "1": return "PTP"
    if val == "3": return "PTMP"
    return val


def parse_radio_mode(ssh_str, radio_idx):
    val = extract_uci_value(ssh_str).lower()
    if val == "ap":
        return "BSU" if radio_idx == 1 else "AP"
    if val == "sta":
        return "SU"
    return val


def parse_configured_channel(ssh_str):
    val = extract_uci_value(ssh_str).lower()
    if "entry not found" in val:
        return "Auto"
    return str(ssh_str).strip()


def parse_bandwidth(ssh_str):
    """Extracts 20, 40, 80, 160 and appends MHz"""
    match = re.search(r'(160|80|40|20)', str(ssh_str))
    if match:
        return f"{match.group(1)} MHz"
    return str(ssh_str).strip()


def parse_security(ssh_str):
    """Validates if security contains ccmp or psk"""
    val = extract_uci_value(ssh_str).lower()
    if "psk" in val or "ccmp" in val:
        return "Enabled"
    if val == "none":
        return "Disabled"
    return val


def parse_ifconfig_mac(ssh_str):
    """Extracts MAC Address from ifconfig block"""
    match = re.search(r'(?:HWaddr|ether)\s+([0-9A-Fa-f:]+)', str(ssh_str), re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""


def parse_iwconfig_active_channel(ssh_str):
    """
    Parses iwconfig output to extract frequency and calculate the Wi-Fi channel.
    Formats the return string to match GUI expectations: 'Channel (Frequency MHz)'
    """
    # Target the 'Frequency:X.XXX GHz' pattern from iwconfig
    match = re.search(r'Frequency:([\d.]+)\s*GHz', str(ssh_str), re.IGNORECASE)

    if match:
        try:
            freq_ghz = float(match.group(1))
            freq_mhz = int(round(freq_ghz * 1000))

            # Calculate Wi-Fi Channel based on standard bands
            channel = 0
            if 2412 <= freq_mhz <= 2472:
                channel = (freq_mhz - 2407) // 5
            elif freq_mhz == 2484:
                channel = 14
            elif freq_mhz >= 5955:  # 6 GHz Band
                channel = (freq_mhz - 5950) // 5
            elif freq_mhz >= 5000:  # 5 GHz Band
                channel = (freq_mhz - 5000) // 5

            if channel > 0:
                return f"{channel} ({freq_mhz} MHz)"
            else:
                return f"{freq_mhz} MHz"

        except ValueError:
            pass

    return ""


def parse_uptime_to_seconds(gui_uptime_str):
    """
    Converts the GUI string like '2h 42m 28s' into raw seconds for math comparison.
    Handles variations like '42m 28s' or '28s'.
    """
    total_seconds = 0
    parts = gui_uptime_str.lower().split()

    for part in parts:
        if 'h' in part:
            total_seconds += int(part.replace('h', '')) * 3600
        elif 'm' in part:
            total_seconds += int(part.replace('m', '')) * 60
        elif 's' in part:
            total_seconds += int(part.replace('s', ''))

    return total_seconds


def parse_desc_info(desc_str):
    """
    Parses a combined description string like '0.0.0.0   SNo. 2411XC813HCK'
    Returns a tuple: (sw_version, serial_number)
    """
    try:
        # Split the string around the 'SNo.' keyword
        parts = desc_str.split("SNo.")
        sw_ver = parts[0].strip()
        serial = parts[1].strip()
        return sw_ver, serial
    except IndexError:
        print(f"    -> WARNING: Failed to parse desc string, unexpected format: '{desc_str}'")
        return desc_str.strip(), "" # Fallback