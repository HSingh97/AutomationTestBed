import re
import datetime
import ipaddress
import random

def generate_test_ip(current_ip, version="v4"):
    """Generates a random test IP for validation."""
    if version == "v4":
        parts = current_ip.split('.')
        # Use a random octet between 100-200 to avoid common SU/Gateway IPs
        new_octet = random.randint(100, 200)
        # Ensure we don't pick the same one
        if str(new_octet) == parts[3]:
            new_octet += 5
        return f"{parts[0]}.{parts[1]}.{parts[2]}.{new_octet}"
    else:
        # For IPv6, we just change a small part of the suffix
        if not current_ip or ":" not in current_ip:
            return "2003:738:2c02::99/64"
        prefix = current_ip.rsplit(':', 1)[0]
        return f"{prefix}:{random.randint(10, 99)}/64"

    


def ssh_scalar(raw_output):
    """Normalized single value from noisy SSH/uci output (last meaningful line, no quotes)."""
    return clean_ssh_output(raw_output).replace("'", "")


def clean_ssh_output(raw_output):
    """
    Normalizes noisy interactive SSH output to the last meaningful line.
    Useful when command output is prefixed with banner/prompt/echo lines.
    """
    text = str(raw_output or "").replace("\r", "")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""

    # Ignore common shell prompt and command-echo lines.
    filtered = []
    prompt_or_echo_patterns = (
        r"^root@.+[:#]\s*$",
        r"^root@.+[:#]\s+.+$",
        r"^#\s*$",
        r"^uci\s+get\s+.+$",
    )
    for line in lines:
        if any(re.match(pat, line) for pat in prompt_or_echo_patterns):
            continue
        filtered.append(line)

    if not filtered:
        return lines[-1]
    return filtered[-1]


def extract_command_result(raw_output, command):
    """
    Extract command value from noisy interactive SSH output.
    Prioritizes the first meaningful line after the echoed command.
    """
    text = str(raw_output or "").replace("\r", "")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""

    cmd_idx = -1
    for idx, line in enumerate(lines):
        if command in line:
            cmd_idx = idx

    def _is_noise(line):
        if re.match(r"^root@.+[:#](\s+.*)?$", line):
            return True
        if line.startswith("BusyBox"):
            return True
        if re.match(r"^[\\/|_\-\s`'.()]+$", line):
            return True
        if line.startswith("/ __") or line.startswith("\\__") or line.startswith("|___"):
            return True
        return False

    if cmd_idx >= 0:
        for line in lines[cmd_idx + 1:]:
            if not _is_noise(line):
                return line

    cleaned = clean_ssh_output(raw_output)
    return cleaned


def extract_hostname_value(raw_output):
    """
    Extracts hostname from noisy SSH output.
    Hostname is expected as a single token without spaces.
    """
    text = str(raw_output or "").replace("\r", "")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""

    for line in reversed(lines):
        if re.match(r"^[A-Za-z0-9._-]+$", line):
            return line

    return clean_ssh_output(raw_output)


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
        return "BTS" if radio_idx == 1 else "AP"
    if val == "sta":
        return "CPE"
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


def parse_encryption(ssh_str):
    val = extract_uci_value(ssh_str).lower()

    if "ccmp-256" in val or "aes" in val or "psk2+ccmp-256" in val:
        return "AES-256"
    if "none" in val:
        return "None"
    return extract_uci_value(ssh_str)

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
    Converts the GUI string like '3d 2h 42m 28s' into raw seconds for math comparison.
    Handles variations like '42m 28s' or '28s'.
    """
    total_seconds = 0
    parts = gui_uptime_str.lower().split()

    for part in parts:
        if 'd' in part:
            total_seconds += int(part.replace('d', '')) * 86400
        elif 'h' in part:
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