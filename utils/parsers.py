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


def parse_iwconfig_mac(ssh_str):
    """Extracts MAC Address from iwconfig block"""
    match = re.search(r'(?:Access Point:|HWaddr)\s*([0-9A-Fa-f:]+)', str(ssh_str), re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""


def parse_iwconfig_active_channel(ssh_str):
    """Extracts frequency (5.835 GHz) and converts to MHz (5835)"""
    match = re.search(r'Frequency:([\d.]+)\s*GHz', str(ssh_str))
    if match:
        freq_ghz = float(match.group(1))
        freq_mhz = int(freq_ghz * 1000)
        return str(freq_mhz)
    return ""