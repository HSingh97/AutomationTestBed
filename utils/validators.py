import re
import pytest_check as check

# Import the necessary parsers from your parsers utility
from utils.parsers import parse_device_time, extract_ip_objects

def validate_param(param_name, ssh_val, gui_val):
    """Helper to evaluate, print status, and log soft asserts."""
    ssh_val_clean = str(ssh_val).strip() if ssh_val else ""
    gui_val_clean = str(gui_val).strip() if gui_val else ""

    no_info_strings = ["", "none", "n/a", "no information", "unknown", "uci: entry not found", "not found"]

    if ssh_val_clean.lower() in no_info_strings or gui_val_clean.lower() in no_info_strings:
        print(f"    -> {param_name}: NO INFORMATION (SSH: '{ssh_val_clean}' | GUI: '{gui_val_clean}')")
        if ssh_val_clean.lower() not in gui_val_clean.lower() and gui_val_clean.lower() not in ssh_val_clean.lower():
            check.is_in(ssh_val_clean, gui_val_clean,
                        f"{param_name} Mismatch! SSH: '{ssh_val_clean}' | GUI: '{gui_val_clean}'")
        return

    if ssh_val_clean in gui_val_clean:
        print(f"    -> {param_name}: PASSED")
        check.is_in(ssh_val_clean, gui_val_clean)
    else:
        print(f"    -> {param_name}: FAILED (SSH: '{ssh_val_clean}' | GUI: '{gui_val_clean}')")
        check.is_in(ssh_val_clean, gui_val_clean,
                    f"{param_name} Mismatch! SSH: '{ssh_val_clean}' | GUI: '{gui_val_clean}'")


def validate_network_address(param_name, ssh_v4, ssh_v6, gui_val):
    ssh_v4_clean = str(ssh_v4).strip() if ssh_v4 else ""
    ssh_v6_clean = str(ssh_v6).strip() if ssh_v6 else ""
    gui_val_clean = str(gui_val).strip() if gui_val else ""

    no_info_strings = ["", "none", "n/a", "no information", "unknown", "uci: entry not found", "not found"]

    v4_valid = ssh_v4_clean and ssh_v4_clean.lower() not in no_info_strings
    v6_valid = ssh_v6_clean and ssh_v6_clean.lower() not in no_info_strings

    if not v4_valid and not v6_valid:
        if gui_val_clean.lower() in no_info_strings:
            print(f"    -> {param_name}: NO INFORMATION (Both SSH and GUI empty)")
            return
        else:
            print(f"    -> {param_name}: FAILED (SSH has no info | GUI: '{gui_val_clean}')")
            check.fail(f"{param_name} Mismatch! SSH is empty but GUI shows: {gui_val_clean}")
            return

    gui_ip_objects = extract_ip_objects(gui_val_clean)
    v4_match, v6_match = False, False

    if v4_valid:
        import ipaddress
        try:
            v4_match = ipaddress.ip_interface(ssh_v4_clean).ip in gui_ip_objects
        except ValueError:
            pass

    if v6_valid:
        import ipaddress
        try:
            v6_match = ipaddress.ip_interface(ssh_v6_clean).ip in gui_ip_objects
        except ValueError:
            pass

    if v4_match or v6_match:
        print(f"    -> {param_name}: PASSED")
        check.is_true(True)
    else:
        print(f"    -> {param_name}: FAILED (SSH v4='{ssh_v4_clean}', v6='{ssh_v6_clean}' | GUI='{gui_val_clean}')")
        check.fail(f"{param_name} Mismatch! SSH IPs mathematically not found in GUI: {gui_val_clean}")


def validate_time(param_name, ssh_time, gui_time):
    if not ssh_time or not gui_time:
        print(f"    -> {param_name}: NO INFORMATION")
        check.fail(f"Missing time string.")
        return

    try:
        ssh_dt = parse_device_time(ssh_time)
        gui_dt = parse_device_time(gui_time)
        time_diff_seconds = abs((gui_dt - ssh_dt).total_seconds())

        if time_diff_seconds <= 10:
            print(f"    -> {param_name}: PASSED")
        else:
            print(f"    -> {param_name}: FAILED (Delta: {time_diff_seconds}s)")

        check.less_equal(time_diff_seconds, 10, f"{param_name} Mismatch!")
    except Exception as e:
        print(f"    -> {param_name}: FAILED TO PARSE")
        check.fail(f"Failed to parse time strings. Error: {e}")


def validate_temperature(param_name, ssh_val, gui_val, tolerance=1.0):
    ssh_clean = str(ssh_val).strip()
    gui_clean = str(gui_val).strip()

    no_info_strings = ["", "none", "n/a", "no information", "unknown"]

    if ssh_clean.lower() in no_info_strings or gui_clean.lower() in no_info_strings:
        print(f"    -> {param_name}: NO INFORMATION (SSH: '{ssh_clean}' | GUI: '{gui_clean}')")
        if ssh_clean.lower() not in gui_clean.lower() and gui_clean.lower() not in ssh_clean.lower():
            check.is_in(ssh_clean, gui_clean, f"{param_name} Mismatch!")
        return

    ssh_num_match = re.search(r'-?[\d.]+', ssh_clean)
    gui_num_match = re.search(r'-?[\d.]+', gui_clean)

    if ssh_num_match and gui_num_match:
        try:
            ssh_f = float(ssh_num_match.group())
            gui_f = float(gui_num_match.group())
            diff = abs(ssh_f - gui_f)

            if diff <= tolerance:
                print(f"    -> {param_name}: PASSED (Within {tolerance}° tolerance)")
            else:
                print(f"    -> {param_name}: FAILED (Drift > {tolerance}°. SSH: {ssh_clean} | GUI: {gui_clean})")

            check.less_equal(diff, tolerance,
                             f"{param_name} Drift exceeded {tolerance}°! SSH: {ssh_clean} | GUI: {gui_clean}")
        except ValueError:
            print(f"    -> {param_name}: FAILED TO PARSE NUMBERS")
            check.fail(f"Could not convert to float. SSH: {ssh_clean} | GUI: {gui_clean}")
    else:
        validate_param(param_name, ssh_clean, gui_clean)


def validate_cpu_mem(ssh_cpu, ssh_mem, gui_val, tolerance=5.0):
    ssh_cpu, ssh_mem, gui_val = str(ssh_cpu).strip(), str(ssh_mem).strip(), str(gui_val).strip()

    if not ssh_cpu or not ssh_mem:
        print(f"    -> CPU & MEMORY: NO INFORMATION")
        check.fail(f"Missing SSH data for CPU/Mem.")
        return

    match = re.search(r'\(\s*([\d.]+)\s*/\s*([\d.]+)\s*\)', gui_val)
    if match:
        try:
            gui_cpu_f, gui_mem_f = float(match.group(1)), float(match.group(2))
            ssh_cpu_f, ssh_mem_f = float(ssh_cpu), float(ssh_mem)

            cpu_diff, mem_diff = abs(ssh_cpu_f - gui_cpu_f), abs(ssh_mem_f - gui_mem_f)

            if cpu_diff <= tolerance and mem_diff <= tolerance:
                print(f"    -> CPU & MEMORY: PASSED (Within {tolerance}% tolerance)")
            else:
                print(
                    f"    -> CPU & MEMORY: FAILED (SSH: CPU={ssh_cpu}, MEM={ssh_mem} | GUI: CPU={gui_cpu_f}, MEM={gui_mem_f})")

            check.less_equal(cpu_diff, tolerance, f"CPU Spike!")
            check.less_equal(mem_diff, tolerance, f"Memory Spike!")
        except ValueError:
            print(f"    -> CPU & MEMORY: FAILED TO PARSE NUMBERS")
            check.fail(f"Could not convert CPU/Mem to floats.")
    else:
        print(f"    -> CPU & MEMORY: FAILED TO PARSE GUI STRING")
        check.fail(f"GUI CPU/Memory format mismatch.")


def validate_speed_duplex(param_name, ssh_speed, ssh_duplex, gui_val):
    ssh_s, ssh_d, gui_v = str(ssh_speed).strip().lower(), str(ssh_duplex).strip().lower(), str(gui_val).strip().lower()
    no_info_states = ["", "none", "n/a", "no information", "unknown", "down"]

    if gui_v in no_info_states or "down" in gui_v:
        ssh_num_match = re.search(r'\d+', ssh_s)
        ssh_num = ssh_num_match.group() if ssh_num_match else ""

        if ssh_s in no_info_states or "down" in ssh_s or not ssh_s or ssh_num == "10":
            print(f"    -> {param_name}: PASSED (Link Down)")
            check.is_true(True)
        else:
            print(f"    -> {param_name}: FAILED (SSH shows Up/Speed={ssh_speed}, GUI shows Down)")
            check.fail(f"{param_name} Mismatch!")
        return

    ssh_spd_match, gui_spd_match = re.search(r'\d+', ssh_s), re.search(r'\d+', gui_v)
    speed_match = (ssh_spd_match.group() == gui_spd_match.group()) if ssh_spd_match and gui_spd_match else False

    if "full" in ssh_d:
        duplex_match = "full" in gui_v
    elif "half" in ssh_d:
        duplex_match = "half" in gui_v
    else:
        duplex_match = False

    if speed_match and duplex_match:
        print(f"    -> {param_name}: PASSED")
    else:
        print(f"    -> {param_name}: FAILED (SSH: Speed={ssh_speed}, Duplex={ssh_duplex} | GUI: '{gui_val}')")

    check.is_true(speed_match, f"{param_name} Speed Mismatch! SSH: {ssh_speed} | GUI: {gui_val}")
    check.is_true(duplex_match, f"{param_name} Duplex Mismatch! SSH: {ssh_duplex} | GUI: {gui_val}")


def validate_throughput(param_name, ssh_val, gui_val, tolerance=20.0):
    ssh_clean, gui_clean = str(ssh_val).strip(), str(gui_val).strip()
    no_info_strings = ["", "none", "n/a", "no information", "unknown", "down"]

    # If the SSH or GUI outright says it has no info/is down
    if not ssh_clean or ssh_clean.lower() in no_info_strings or gui_clean.lower() in no_info_strings:
        print(f"    -> {param_name}: NO INFORMATION/DOWN")
        check.is_true(True)
        return

    ssh_num_match = re.search(r'[\d.]+', ssh_clean)
    gui_num_match = re.search(r'[\d.]+', gui_clean)

    if ssh_num_match and gui_num_match:
        try:
            ssh_raw = float(ssh_num_match.group())
            gui_f = float(gui_num_match.group())

            # Convert SSH bits-per-second (bps) to Mbps unconditionally
            ssh_f = ssh_raw / 1000000.0

            # If both are essentially zero, they are idle
            if ssh_f < 0.01 and gui_f < 0.01:
                print(f"    -> {param_name}: PASSED (Idle: 0.00)")
                return

            # Calculate the FLAT difference in Mbps (no longer using percentages)
            diff = abs(ssh_f - gui_f)

            if diff <= tolerance:
                print(f"    -> {param_name}: PASSED (Within {tolerance} Mbps tolerance. SSH: {ssh_f:.2f} Mbps | GUI: {gui_f} Mbps)")
            else:
                print(f"    -> {param_name}: FAILED (Live Drift > {tolerance} Mbps. SSH: {ssh_f:.2f} Mbps | GUI: {gui_f} Mbps)")

            # Pass the failure to pytest-check
            check.less_equal(diff, tolerance, f"{param_name} Drift exceeded! SSH: {ssh_f:.2f} Mbps | GUI: {gui_f} Mbps")
        except ValueError:
            print(f"    -> {param_name}: FAILED TO PARSE NUMBERS")
            check.fail(f"Could not convert to float. SSH: {ssh_clean} | GUI: {gui_clean}")
    else:
        validate_param(param_name, ssh_clean, gui_clean)
        
def validate_percentage(param_name, ssh_val, gui_val, tolerance=5.0):
    ssh_clean, gui_clean = str(ssh_val).strip(), str(gui_val).strip()
    no_info_strings = ["", "none", "n/a", "no information", "unknown", "-"]

    if ssh_clean.lower() in no_info_strings or gui_clean.lower() in no_info_strings:
        print(f"    -> {param_name}: NO INFORMATION (SSH: '{ssh_clean}' | GUI: '{gui_clean}')")
        if ssh_clean.lower() not in gui_clean.lower() and gui_clean.lower() not in ssh_clean.lower():
            check.is_in(ssh_clean, gui_clean, f"{param_name} Mismatch!")
        return

    ssh_num_match = re.search(r'[\d.]+', ssh_clean)
    gui_num_match = re.search(r'[\d.]+', gui_clean)

    if ssh_num_match and gui_num_match:
        try:
            ssh_f, gui_f = float(ssh_num_match.group()), float(gui_num_match.group())
            diff = abs(ssh_f - gui_f)

            if diff <= tolerance:
                print(f"    -> {param_name}: PASSED (Within {tolerance}% tolerance)")
            else:
                print(f"    -> {param_name}: FAILED (Drift > {tolerance}%. SSH: {ssh_clean} | GUI: {gui_clean})")

            check.less_equal(diff, tolerance, f"{param_name} Drift exceeded!")
        except ValueError:
            print(f"    -> {param_name}: FAILED TO PARSE NUMBERS")
            check.fail(f"Could not convert to float. SSH: {ssh_clean} | GUI: {gui_clean}")
    else:
        validate_param(param_name, ssh_clean, gui_clean)