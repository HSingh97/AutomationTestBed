"""
Novusmini IxNetwork : SENAO NETWORKS
Requirements:
   - Minimum IxNetwork 9.32EA
   - Python 2.7 and 3+
   pip install requests ixnetwork_restpy
   pip install weasyprint matplotlib

RestPy Doc:
    https://openixia.github.io/ixnetwork_restpy/#/overview
    https://github.com/OpenIxia/ixnetwork_restpy

Usage:
   - Fresh Start:     python <script> --mode clear
   - Keep Config:     python <script> --mode keep
   - Automation:      python <script> --cpes 16 --target 50 --ratio 70:30 --time 60 --output-json results.json
"""

import sys, os, time, traceback, subprocess, re, argparse, json, asyncio
from ixnetwork_restpy import *
from config.defaults import TRAFFIC_DEFAULTS
from pages.commands import RootCommands
from utils.net_utils import format_snmp_host, format_ssh_host, normalize_ip
from utils.profile_manager import load_profile_bundle
from utils.recovery_manager import RecoveryManager
from traffic.trex_runner import run_trex_stats_check

try:
    from weasyprint import HTML
    import matplotlib.pyplot as plt
    import io
    import base64
    from datetime import datetime
    pdf_libs_available = True
except ImportError:
    print("\n[WARNING] weasyprint or matplotlib not found. PDF generation will be skipped.")
    print("To enable reporting, run: pip install weasyprint matplotlib\n")
    pdf_libs_available = False

# =============================================================================
# ---> EASY CONFIGURATION BLOCK (DEFAULTS) <---
# =============================================================================

# 1. Hardware Mapping
API_SERVER_IP = '10.0.150.50'
CHASSIS_IP    = 'localchassis'

# Set your physical ports here: [Card ID, Port ID]
PORT_1_LOCATION = [1, 2]
PORT_2_LOCATION = [1, 3]

# 2. Test Scale & Duration
NUM_CPES          = 16
RAMP_UP_SECONDS   = 5
TEST_SECONDS      = 15

# 3. DUT (UBR/AP) SSH Credentials
DUT_IP   = "192.168.2.20"
SSH_USER = "root"
SSH_PASS = "Sen@0ubRNwk$"

# 4. DUT Wireless Interfaces
WIFI_INT = "wifi1"
WLAN_INT = "ath1"

# 5. DUT SNMP Configuration
SNMP_COMMUNITY = "ubr@rw123"
SNMP_RADIO_IDX = "2" 

# =============================================================================
# INTERNAL VARIABLES & OIDs
# =============================================================================
physicalPorts = [
    [CHASSIS_IP, PORT_1_LOCATION[0], PORT_1_LOCATION[1]], 
    [CHASSIS_IP, PORT_2_LOCATION[0], PORT_2_LOCATION[1]]
]

debugMode = True
forceTakePortOwnership = True
TREX_DEFAULTS = TRAFFIC_DEFAULTS["trex"]

frameSizeType = 'imix'   
fixedFrameSize = 1500    
imixProfile = [          
    { "size": "64", "weight": 1 },
    { "size": "512", "weight": 2 },
    { "size": "1400", "weight": 7 }
]

COLOR_DOWNLINK = '\033[92m' 
COLOR_UPLINK = '\033[96m'   
COLOR_RESET = '\033[0m'     

# OID Definitions
OID_RADIO_MODE_BASE = ".1.3.6.1.4.1.52619.1.1.1.1.1.2"
OID_LINK_TYPE_BASE = ".1.3.6.1.4.1.52619.1.1.10.1"
OID_SSID_BASE = ".1.3.6.1.4.1.52619.1.1.1.1.1.3"
OID_CONF_CHANNEL_BASE = ".1.3.6.1.4.1.52619.1.1.1.1.1.9"
OID_CONF_BW_BASE = ".1.3.6.1.4.1.52619.1.1.1.1.1.7"
OID_ACT_CHANNEL_BASE = ".1.3.6.1.4.1.52619.1.1.1.1.1.23"
OID_ACT_BW_BASE = ".1.3.6.1.4.1.52619.1.1.1.1.1.51"
OID_DDRS_BASE = ".1.3.6.1.4.1.52619.1.1.1.2.1.3"
OID_ATPC_BASE = ".1.3.6.1.4.1.52619.1.1.1.2.1.11"
OID_TX_POWER_BASE = ".1.3.6.1.4.1.52619.1.1.1.2.1.12"
OID_DCS_BASE = ".1.3.6.1.4.1.52619.1.1.1.3.1.2"
OID_VLAN_STATUS_SCALAR = ".1.3.6.1.4.1.52619.1.1.4.1.0"
OID_VLAN_MODE_SCALAR = ".1.3.6.1.4.1.52619.1.1.4.2.0"
OID_SU_IP_WALK_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.4" 
OID_LOCAL_SNRA1_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.13"
OID_LOCAL_SNRA2_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.14"
OID_REMOTE_SNRA1_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.15"
OID_REMOTE_SNRA2_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.16"
OID_TX_RATE_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.10"
OID_RX_RATE_BASE = ".1.3.6.1.4.1.52619.1.3.3.1.9"

# -----------------------------------------------------------------------------
# SSH & SNMP HELPER FUNCTIONS
# -----------------------------------------------------------------------------
def run_shell_command(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8').strip()
    except Exception:
        return ""

def run_ssh_command(ip, user, pw, command):
    ssh_opts = "-o LogLevel=ERROR -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8"
    ssh_host = format_ssh_host(ip)
    ssh_cmd = f"sshpass -p '{pw}' ssh {ssh_opts} {user}@{ssh_host} \"{command}\""
    return run_shell_command(ssh_cmd)

def configure_bandwidth_and_mcs(ip, user, pw, radio_idx, bandwidth, mcs_rate, spatial_stream, ddrs_rate):
    print(f"Applying radio profile: bw={bandwidth}, mcs={mcs_rate}, spatial_stream={spatial_stream}, ddrs_rate={ddrs_rate}")
    for cmd in RootCommands.set_bandwidth_commands(radio_idx, bandwidth):
        run_ssh_command(ip, user, pw, f"{cmd} || true")
    for cmd in RootCommands.set_mcs_sequence_commands(radio_idx, mcs_rate, spatial_stream, ddrs_rate):
        run_ssh_command(ip, user, pw, f"{cmd} || true")
    run_ssh_command(ip, user, pw, f"{RootCommands.remote_apply_all_su()} || true")
    time.sleep(4)

def parse_snmp_value(output: str) -> str:
    if "No Such" in output or not output: return "-"
    if "STRING:" in output:
        raw_str = output.split("STRING:")[1].strip()
        return raw_str.strip('"')
    raw_val = output.split(":")[-1].strip() if ":" in output else output.split(" ")[-1].strip()
    return raw_val.replace('"', '') if raw_val else "-"

def fetch_ssh_metrics(ip, user, pw, wifi_int, wlan_int):
    metrics = {"obss": "-", "chan_util": "-", "freq_ghz": "-"}
    ssh_opts = "-o LogLevel=ERROR -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
    ssh_host = format_ssh_host(ip)
    base_cmd = f"sshpass -p '{pw}' ssh {ssh_opts} {user}@{ssh_host}"
    
    obss_out = run_shell_command(f"{base_cmd} cfg80211tool {wifi_int} g_ch_util_obss")
    if ":" in obss_out: metrics["obss"] = obss_out.split(":")[1].strip()
    
    util_out = run_shell_command(f"{base_cmd} cfg80211tool {wifi_int} g_chanutil")
    if ":" in util_out: metrics["chan_util"] = util_out.split(":")[1].strip()
    
    freq_out = run_shell_command(f"{base_cmd} iwconfig {wlan_int}")
    match = re.search(r'Frequency:([\d\.]+)', freq_out)
    if match: metrics["freq_ghz"] = match.group(1)

    return metrics

def fetch_snmp_config(ip):
    snmp_host = format_snmp_host(ip)
    config = {}
    def get_val(o): return parse_snmp_value(run_shell_command(f"snmpget -v 2c -c {SNMP_COMMUNITY} {snmp_host} {o}.{SNMP_RADIO_IDX}"))
    def get_val_suf(o): return parse_snmp_value(run_shell_command(f"snmpget -v 2c -c {SNMP_COMMUNITY} {snmp_host} {o}.{SNMP_RADIO_IDX}.1"))
    def get_scalar(o): return parse_snmp_value(run_shell_command(f"snmpget -v 2c -c {SNMP_COMMUNITY} {snmp_host} {o}"))

    mode_raw = get_val(OID_RADIO_MODE_BASE).lower()
    config['Radio Mode'] = 'BSU' if 'ap' in mode_raw else ('SU' if 'sta' in mode_raw else mode_raw)
    lt = get_val(OID_LINK_TYPE_BASE)
    config['Link Type'] = 'PtP' if '1' in lt or 'ptp' in lt.lower() else ('PtMP' if '2' in lt or 'ptmp' in lt.lower() else lt)
    config['SSID'] = get_val(OID_SSID_BASE)
    config['Conf Chan'] = get_val(OID_CONF_CHANNEL_BASE)
    config['Conf BW'] = get_val(OID_CONF_BW_BASE)
    config['Act Chan'] = get_val(OID_ACT_CHANNEL_BASE)
    config['Act BW'] = get_val(OID_ACT_BW_BASE)
    config['Tx Power'] = get_val_suf(OID_TX_POWER_BASE)

    map_en = lambda x: "Enabled" if x == '1' else "Disabled"
    config['DDRS'] = map_en(get_val_suf(OID_DDRS_BASE))
    config['ATPC'] = map_en(get_val_suf(OID_ATPC_BASE))
    config['DCS'] = map_en(get_val(OID_DCS_BASE))

    v_stat = "Enabled" if get_scalar(OID_VLAN_STATUS_SCALAR) == '1' else "Disabled"
    v_mode = get_scalar(OID_VLAN_MODE_SCALAR)
    v_map = {'0': 'Transparent', '1': 'Access', '2': 'Trunk', '3': 'Q-in-Q'}
    config['VLAN Config'] = f"{v_stat} ( {v_map.get(v_mode, v_mode)} )"
    
    return config

def fetch_connected_clients(ip):
    clients = []
    snmp_host = format_snmp_host(ip)
    walk_cmd = f"snmpwalk -v 2c -c {SNMP_COMMUNITY} {snmp_host} {OID_SU_IP_WALK_BASE}.{SNMP_RADIO_IDX}"
    walk_out = run_shell_command(walk_cmd)
    
    for line in walk_out.splitlines():
        ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', line)
        idx_match = re.search(rf'\.{SNMP_RADIO_IDX}\.(\d+)', line)
        
        if ips and idx_match:
            su_ip = ips[-1] 
            su_index = idx_match.group(1)
            
            def get_rf(base): return parse_snmp_value(run_shell_command(f"snmpget -v 2c -c {SNMP_COMMUNITY} {snmp_host} {base}.{SNMP_RADIO_IDX}.{su_index}"))
            
            clients.append({
                "ip": su_ip,
                "l_snr1": get_rf(OID_LOCAL_SNRA1_BASE),
                "l_snr2": get_rf(OID_LOCAL_SNRA2_BASE),
                "r_snr1": get_rf(OID_REMOTE_SNRA1_BASE),
                "r_snr2": get_rf(OID_REMOTE_SNRA2_BASE),
                "tx_rate": get_rf(OID_TX_RATE_BASE),
                "rx_rate": get_rf(OID_RX_RATE_BASE)
            })
            
    try:
        clients.sort(key=lambda x: [int(p) for p in x['ip'].split('.')])
    except Exception:
        pass
        
    return clients

# -----------------------------------------------------------------------------
# IXNETWORK REPORT & EXECUTION
# -----------------------------------------------------------------------------
def safe_get(row, key, default='0'):
    try:
        val = row[key]
        return val if val != '' else default
    except Exception:
        return default

def ns_to_ms_float(ns_string):
    try:
        return float(ns_string) / 1_000_000.0
    except Exception:
        return 0.0

def generate_pdf_report(report_data, history, config, summary, env_metrics, snmp_config, connected_clients, filename="IxNetwork_Performance_Report.pdf"):
    if not pdf_libs_available:
        return

    print(f"Generating Modern Dashboard PDF Report: {filename}...")

    def fig_to_base64(fig):
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', dpi=200)
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')

    font_color = '#334155'
    grid_color = '#e2e8f0'

    downlink_data = [r for r in report_data if r['direction'] == 'Downlink']
    uplink_data = [r for r in report_data if r['direction'] == 'Uplink']
    dl_rows = [r['row'] for r in downlink_data]
    ul_rows = [r['row'] for r in uplink_data]

    def calc_subset_metrics(data_subset):
        if not data_subset: return {'tx': 0, 'rx': 0, 'loss': 0.0, 'lat': 0.0}
        tx = sum(r['tx_mbps'] for r in data_subset)
        rx = sum(r['rx_mbps'] for r in data_subset)
        loss = sum(r['loss'] for r in data_subset) / len(data_subset)
        lat = sum(r['avg_ms'] for r in data_subset) / len(data_subset)
        return {'tx': tx, 'rx': rx, 'loss': loss, 'lat': lat}

    dl_metrics = calc_subset_metrics(downlink_data)
    ul_metrics = calc_subset_metrics(uplink_data)

    def get_loss_style(loss_val):
        return "color: #ef4444; font-weight: 700;" if loss_val > 0.0 else "color: #10b981; font-weight: 600;"

    fig_tput, ax_tput = plt.subplots(figsize=(7.5, 4.5), facecolor='#ffffff')
    ax_tput.plot(history['time'], history['tx'], label='Total Tx (Mbps)', color='#4f46e5', linewidth=2.5)
    ax_tput.plot(history['time'], history['rx'], label='Total Rx (Mbps)', color='#0ea5e9', linewidth=2.5, linestyle='--')
    ax_tput.set_title('Aggregated Chassis Throughput Over Time', fontsize=11, fontweight='600', color=font_color, pad=15)
    ax_tput.set_xlabel('Active Test Duration (Seconds)', fontsize=9, color=font_color)
    ax_tput.set_ylabel('Throughput (Mbps)', fontsize=9, color=font_color)
    ax_tput.spines['top'].set_visible(False)
    ax_tput.spines['right'].set_visible(False)
    ax_tput.spines['left'].set_color(grid_color)
    ax_tput.spines['bottom'].set_color(grid_color)
    ax_tput.grid(axis='y', linestyle='--', alpha=0.7, color=grid_color)
    ax_tput.legend(loc='lower right', fontsize=8, framealpha=0.9, edgecolor=grid_color)
    overall_tput_img = fig_to_base64(fig_tput)

    fig_dl_tput, ax_dl_tput = plt.subplots(figsize=(7.5, 3.3), facecolor='#ffffff')
    if dl_rows and len(history['rx_streams']) > 0:
        for r_idx in dl_rows:
            if r_idx < len(history['rx_streams'][0]):
                stream_rx = [tp[r_idx] for tp in history['rx_streams']]
                ax_dl_tput.plot(history['time'], stream_rx, color='#10b981', alpha=0.6, linewidth=1.5)
    ax_dl_tput.set_title('Downlink Per-Stream Throughput (Rx)', fontsize=11, fontweight='600', color=font_color, pad=15)
    ax_dl_tput.set_xlabel('Active Test Duration (Seconds)', fontsize=9, color=font_color)
    ax_dl_tput.set_ylabel('Throughput (Mbps)', fontsize=9, color=font_color)
    ax_dl_tput.spines['top'].set_visible(False)
    ax_dl_tput.spines['right'].set_visible(False)
    ax_dl_tput.spines['left'].set_color(grid_color)
    ax_dl_tput.spines['bottom'].set_color(grid_color)
    ax_dl_tput.grid(axis='y', linestyle='--', alpha=0.7, color=grid_color)
    dl_tput_img = fig_to_base64(fig_dl_tput)

    fig_dl_lat, ax_dl_lat = plt.subplots(figsize=(7.5, 3.3), facecolor='#ffffff')
    if dl_rows and len(history['lat_streams']) > 0:
        for r_idx in dl_rows:
            if r_idx < len(history['lat_streams'][0]):
                stream_lat = [tp[r_idx] for tp in history['lat_streams']]
                ax_dl_lat.plot(history['time'], stream_lat, color='#10b981', alpha=0.4, linewidth=1.5)
    ax_dl_lat.set_title('Downlink Per-Stream Latency', fontsize=11, fontweight='600', color=font_color, pad=15)
    ax_dl_lat.set_xlabel('Active Test Duration (Seconds)', fontsize=9, color=font_color)
    ax_dl_lat.set_ylabel('Latency (ms)', fontsize=9, color=font_color)
    ax_dl_lat.spines['top'].set_visible(False)
    ax_dl_lat.spines['right'].set_visible(False)
    ax_dl_lat.spines['left'].set_color(grid_color)
    ax_dl_lat.spines['bottom'].set_color(grid_color)
    ax_dl_lat.grid(axis='y', linestyle='--', alpha=0.7, color=grid_color)
    dl_lat_img = fig_to_base64(fig_dl_lat)

    fig_ul_tput, ax_ul_tput = plt.subplots(figsize=(7.5, 3.3), facecolor='#ffffff')
    if ul_rows and len(history['rx_streams']) > 0:
        for r_idx in ul_rows:
            if r_idx < len(history['rx_streams'][0]):
                stream_rx = [tp[r_idx] for tp in history['rx_streams']]
                ax_ul_tput.plot(history['time'], stream_rx, color='#0ea5e9', alpha=0.6, linewidth=1.5)
    ax_ul_tput.set_title('Uplink Per-Stream Throughput (Rx)', fontsize=11, fontweight='600', color=font_color, pad=15)
    ax_ul_tput.set_xlabel('Active Test Duration (Seconds)', fontsize=9, color=font_color)
    ax_ul_tput.set_ylabel('Throughput (Mbps)', fontsize=9, color=font_color)
    ax_ul_tput.spines['top'].set_visible(False)
    ax_ul_tput.spines['right'].set_visible(False)
    ax_ul_tput.spines['left'].set_color(grid_color)
    ax_ul_tput.spines['bottom'].set_color(grid_color)
    ax_ul_tput.grid(axis='y', linestyle='--', alpha=0.7, color=grid_color)
    ul_tput_img = fig_to_base64(fig_ul_tput)

    fig_ul_lat, ax_ul_lat = plt.subplots(figsize=(7.5, 3.3), facecolor='#ffffff')
    if ul_rows and len(history['lat_streams']) > 0:
        for r_idx in ul_rows:
            if r_idx < len(history['lat_streams'][0]):
                stream_lat = [tp[r_idx] for tp in history['lat_streams']]
                ax_ul_lat.plot(history['time'], stream_lat, color='#0ea5e9', alpha=0.4, linewidth=1.5)
    ax_ul_lat.set_title('Uplink Per-Stream Latency', fontsize=11, fontweight='600', color=font_color, pad=15)
    ax_ul_lat.set_xlabel('Active Test Duration (Seconds)', fontsize=9, color=font_color)
    ax_ul_lat.set_ylabel('Latency (ms)', fontsize=9, color=font_color)
    ax_ul_lat.spines['top'].set_visible(False)
    ax_ul_lat.spines['right'].set_visible(False)
    ax_ul_lat.spines['left'].set_color(grid_color)
    ax_ul_lat.spines['bottom'].set_color(grid_color)
    ax_ul_lat.grid(axis='y', linestyle='--', alpha=0.7, color=grid_color)
    ul_lat_img = fig_to_base64(fig_ul_lat)

    def make_sum_row(data_subset, label):
        if not data_subset: return ""
        loss = sum(r['loss'] for r in data_subset) / len(data_subset)
        lat = sum(r['avg_ms'] for r in data_subset) / len(data_subset)
        tx = sum(r['tx_mbps'] for r in data_subset)
        rx = sum(r['rx_mbps'] for r in data_subset)
        return f"<tr class='summary-row'><td colspan='2' style='text-align:left; padding-left:15px;'>{label}</td><td>ALL</td><td>-</td><td>-</td><td>{loss:.3f}%</td><td>-</td><td>{lat:.3f}</td><td>-</td><td>{tx:.2f}</td><td>{rx:.2f}</td></tr>"

    def build_table_rows(data_subset):
        if not data_subset:
            return "<tr><td colspan='11' style='text-align:center; padding: 25px; font-weight: 600; color:#94a3b8;'>No active streams in this direction (Unidirectional Test)</td></tr>"

        rows_html = ""
        for r in data_subset:
            rows_html += "<tr>"
            rows_html += f"<td>{r['row']}</td>"
            rows_html += f"<td style='text-align: left;'><span style='font-size: 7.5pt; color: #334155;'>{r['pair']}</span></td>"
            rows_html += f"<td>{r['vlan']}</td>"
            rows_html += f"<td>{r['tx_frames']}</td>"
            rows_html += f"<td>{r['rx_frames']}</td>"
            loss_color = "color: #ef4444; font-weight: bold;" if r['loss'] > 0 else ""
            rows_html += f"<td style='{loss_color}'>{r['loss']:.3f}</td>"
            rows_html += f"<td>{r['min_ms']:.3f}</td>"
            rows_html += f"<td>{r['avg_ms']:.3f}</td>"
            rows_html += f"<td>{r['max_ms']:.3f}</td>"
            rows_html += f"<td>{r['tx_mbps']:.2f}</td>"
            rows_html += f"<td>{r['rx_mbps']:.2f}</td>"
            rows_html += "</tr>"
        return rows_html
        
    def build_client_rows(clients):
        if not clients: return "<tr><td colspan='7' style='text-align:center; padding: 30px; font-weight: 500; color:#94a3b8;'>No connected clients detected on the wireless interface.</td></tr>"
        rows_html = ""
        for c in clients:
            rows_html += f"<tr><td style='font-weight: 600; text-align: left; padding-left: 15px;'>{c['ip']}</td><td>{c['l_snr1']}</td><td>{c['l_snr2']}</td><td>{c['r_snr1']}</td><td>{c['r_snr2']}</td><td>{c['tx_rate']}</td><td>{c['rx_rate']}</td></tr>"
        return rows_html

    downlink_html = build_table_rows(downlink_data)
    uplink_html = build_table_rows(uplink_data)
    downlink_sum = make_sum_row(downlink_data, "DOWNLINK AVERAGE / TOTAL")
    uplink_sum = make_sum_row(uplink_data, "UPLINK AVERAGE / TOTAL")
    clients_html = build_client_rows(connected_clients)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        @page {{ size: A4 portrait; margin: 15mm; background-color: #f8fafc; }}
        body {{
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #1e293b; background-color: #f8fafc; margin: 0; padding: 0;
            -webkit-font-smoothing: antialiased;
        }}
        
        .header-banner {{
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #ffffff; padding: 25px 30px; border-radius: 12px;
            margin-bottom: 25px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }}
        .header-banner h1 {{ margin: 0 0 8px 0; font-size: 20pt; font-weight: 600; letter-spacing: 0.5px; }}
        .header-banner p {{ margin: 0; color: #94a3b8; font-size: 9.5pt; }}
        
        .card {{
            background: #ffffff; padding: 20px; border-radius: 12px;
            border: 1px solid #e2e8f0; margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        .card-title {{ margin-top: 0; font-size: 12pt; color: #1e293b; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; margin-bottom: 15px; font-weight: 600; }}
        
        .exec-table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; text-align: center; }}
        .exec-table th {{ background-color: #f1f5f9; color: #475569; font-weight: 600; padding: 12px; border-bottom: 2px solid #cbd5e1; }}
        .exec-table th:first-child {{ text-align: left; }}
        .exec-table td {{ padding: 12px; border-bottom: 1px solid #f1f5f9; color: #334155; }}
        .exec-table td:first-child {{ text-align: left; font-weight: 600; color: #1e293b; background-color: #fafafa; }}
        .exec-table tr:last-child td {{ border-bottom: none; }}
        .metric-val {{ font-weight: 600; }}

        .info-table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; }}
        .info-table th, .info-table td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #f1f5f9; }}
        .info-table th {{ color: #64748b; font-weight: 500; width: 35%; }}
        .info-table td {{ font-weight: 600; color: #334155; }}
        
        .data-table {{ width: 100%; border-collapse: collapse; font-size: 7.5pt; table-layout: auto; }}
        .data-table th {{ background-color: #f8fafc; color: #475569; font-weight: 600; padding: 10px 6px; text-align: center; border-bottom: 2px solid #e2e8f0; }}
        .data-table td {{ padding: 8px 6px; text-align: center; border-bottom: 1px solid #f1f5f9; }}
        .data-table tbody tr:nth-child(even) {{ background-color: #fafafa; }}
        
        .summary-row {{ background-color: #f1f5f9 !important; font-weight: 700; color: #0f172a; border-top: 2px solid #cbd5e1; }}
        .graph-wrapper {{ text-align: center; margin-bottom: 5px; }}
        .graph-wrapper img {{ max-width: 100%; height: auto; display: block; margin: 0 auto; }}
        .page-break {{ page-break-before: always; }}
    </style>
    </head>
    <body>
        <div class="header-banner">
            <h1>IxNetwork Performance Report</h1>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Target DUT: Senao Networks UBR</p>
        </div>
        
        <div class="card">
            <h2 class="card-title">Test Configuration Parameters</h2>
            <table class="info-table">
                <tr><th>No of CPE</th><td>{config['cpes']} Streams</td></tr>
                <tr><th>Direction</th><td>Bidirectional</td></tr>
                <tr><th>Traffic Frame Profile</th><td>{config['frame_config']}</td></tr>
                <tr><th>Downlink Throughput (Per CPE)</th><td>{config['dl_per_cpe']} Mbps</td></tr>
                <tr><th>Uplink Throughput (Per CPE)</th><td>{config['ul_per_cpe']} Mbps</td></tr>
                <tr><th>Downlink Load Target</th><td>{config['down_target']:.2f} Mbps</td></tr>
                <tr><th>Uplink Load Target</th><td>{config['up_target']:.2f} Mbps</td></tr>
                <tr><th>Duration</th><td>{config['duration']} Seconds (Exclusive of {RAMP_UP_SECONDS}s Ramp-up)</td></tr>
            </table>
        </div>

        <div class="card" style="padding: 0; overflow: hidden;">
            <div style="padding: 20px 20px 10px 20px;">
                <h2 class="card-title" style="margin-bottom: 0; border-bottom: none;">Summary</h2>
            </div>
            <table class="exec-table">
                <thead>
                    <tr>
                        <th>Metric</th>
                        <th>Downlink (Rx &rarr; Tx)</th>
                        <th>Uplink (Tx &rarr; Rx)</th>
                        <th style="background-color: #f8fafc;">Combined System</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Target Traffic</td>
                        <td class="metric-val">{config['down_target']:.2f} Mbps</td>
                        <td class="metric-val">{config['up_target']:.2f} Mbps</td>
                        <td class="metric-val" style="background-color: #f8fafc;">{config['down_target'] + config['up_target']:.2f} Mbps</td>
                    </tr>
                    <tr>
                        <td>Actual Throughput (Rx)</td>
                        <td class="metric-val">{dl_metrics['rx']:.2f} Mbps</td>
                        <td class="metric-val">{ul_metrics['rx']:.2f} Mbps</td>
                        <td class="metric-val" style="background-color: #f8fafc;">{summary['rx']:.2f} Mbps</td>
                    </tr>
                    <tr>
                        <td>Average Packet Loss</td>
                        <td class="metric-val" style="{get_loss_style(dl_metrics['loss'])}">{dl_metrics['loss']:.3f}%</td>
                        <td class="metric-val" style="{get_loss_style(ul_metrics['loss'])}">{ul_metrics['loss']:.3f}%</td>
                        <td class="metric-val" style="background-color: #f8fafc; {get_loss_style(summary['loss'])}">{summary['loss']:.3f}%</td>
                    </tr>
                    <tr>
                        <td>Average Latency</td>
                        <td class="metric-val">{dl_metrics['lat']:.2f} ms</td>
                        <td class="metric-val">{ul_metrics['lat']:.2f} ms</td>
                        <td class="metric-val" style="background-color: #f8fafc;">{summary['lat']:.2f} ms</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="page-break"></div>
        
        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 20px;">
            <h1 style="font-size: 16pt;">DUT & Wireless Telemetry</h1>
            <p>Baseline environmental and hardware metrics captured prior to traffic generation.</p>
        </div>
        
        <div class="card" style="padding: 0; overflow: hidden; margin-bottom: 25px;">
            <div style="padding: 20px 20px 10px 20px;"><h2 class="card-title" style="margin-bottom: 0; border-bottom: none;">Pre-Test Wireless Environment (SSH)</h2></div>
            <table class="info-table">
                <tr><th>Operating Freq</th><td>{env_metrics.get('freq_ghz', 'N/A')} GHz</td><th>Channel Utilization</th><td>{env_metrics.get('chan_util', 'N/A')}%</td></tr>
                <tr><th>OBSS Value</th><td colspan="3">{env_metrics.get('obss', 'N/A')}%</td></tr>
            </table>
        </div>
        
        <div class="card" style="padding: 0; overflow: hidden;">
            <div style="padding: 20px 20px 10px 20px;"><h2 class="card-title" style="margin-bottom: 0; border-bottom: none;">DUT Static Configuration (SNMP)</h2></div>
            <table class="info-table">
                <tr><th>Radio Mode</th><td>{snmp_config.get('Radio Mode', 'N/A')}</td><th>Link Type</th><td>{snmp_config.get('Link Type', 'N/A')}</td></tr>
                <tr><th>SSID</th><td>{snmp_config.get('SSID', 'N/A')}</td><th>Tx Power</th><td>{snmp_config.get('Tx Power', 'N/A')} dBm</td></tr>
                <tr><th>Conf Channel</th><td>{snmp_config.get('Conf Chan', 'N/A')}</td><th>Conf BW</th><td>{snmp_config.get('Conf BW', 'N/A')} MHz</td></tr>
                <tr><th>Act Channel</th><td>{snmp_config.get('Act Chan', 'N/A')}</td><th>Act BW</th><td>{snmp_config.get('Act BW', 'N/A')} MHz</td></tr>
                <tr><th>DDRS Status</th><td>{snmp_config.get('DDRS', 'N/A')}</td><th>ATPC Status</th><td>{snmp_config.get('ATPC', 'N/A')}</td></tr>
                <tr><th>DCS Status</th><td>{snmp_config.get('DCS', 'N/A')}</td><th>VLAN Config</th><td>{snmp_config.get('VLAN Config', 'N/A')}</td></tr>
            </table>
        </div>

        <div class="page-break"></div>

        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 20px;">
            <h1 style="font-size: 16pt;">Connected Clients RF Matrix</h1>
            <p>Real-time Signal-to-Noise Ratios and PHY negotiated rates across all active wireless clients.</p>
        </div>

        <div class="card" style="padding: 0; overflow: hidden;">
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="text-align: left; padding-left: 15px;">Client IP Address</th>
                        <th>Local SNR A1</th>
                        <th>Local SNR A2</th>
                        <th>Remote SNR A1</th>
                        <th>Remote SNR A2</th>
                        <th>Tx Rate (Mbps)</th>
                        <th>Rx Rate (Mbps)</th>
                    </tr>
                </thead>
                <tbody>
                    {clients_html}
                </tbody>
            </table>
        </div>

        <div class="page-break"></div>

        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 20px;">
            <h1 style="font-size: 16pt;">Overall Performance Telemetry</h1>
            <p>Aggregated Tx/Rx throughput for the entire chassis.</p>
        </div>
        
        <div class="card" style="padding: 15px; margin-bottom: 15px;">
            <div class="graph-wrapper">
                <img src="data:image/png;base64,{overall_tput_img}" />
            </div>
        </div>
        
        <div class="page-break"></div>

        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 20px;">
            <h1 style="font-size: 16pt;">Downlink Telemetry</h1>
            <p>Per-stream throughput and latency distribution.</p>
        </div>
        
        <div class="card" style="padding: 10px; margin-bottom: 15px;">
            <div class="graph-wrapper">
                <img src="data:image/png;base64,{dl_tput_img}" />
            </div>
        </div>
        
        <div class="card" style="padding: 10px;">
            <div class="graph-wrapper">
                <img src="data:image/png;base64,{dl_lat_img}" />
            </div>
        </div>
        
        <div class="page-break"></div>

        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 20px;">
            <h1 style="font-size: 16pt;">Uplink Telemetry</h1>
            <p>Per-stream throughput and latency distribution.</p>
        </div>
        
        <div class="card" style="padding: 10px; margin-bottom: 15px;">
            <div class="graph-wrapper">
                <img src="data:image/png;base64,{ul_tput_img}" />
            </div>
        </div>
        
        <div class="card" style="padding: 10px;">
            <div class="graph-wrapper">
                <img src="data:image/png;base64,{ul_lat_img}" />
            </div>
        </div>

        <div class="page-break"></div>

        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 15px;">
            <h1 style="font-size: 16pt;">Downlink Matrix</h1>
            <p>Stream-level metrics</p>
        </div>
        
        <div class="card" style="padding: 10px;">
            <table class="data-table">
                <thead>
                    <tr>
                        <th>ID</th><th style="text-align: left;">Pairing Configuration</th><th>VLAN</th>
                        <th>Tx Frames</th><th>Rx Frames</th><th>Loss %</th>
                        <th>Min (ms)</th><th>Avg (ms)</th><th>Max (ms)</th>
                        <th>Tx (Mbps)</th><th>Rx (Mbps)</th>
                    </tr>
                </thead>
                <tbody>
                    {downlink_html}
                    {downlink_sum}
                </tbody>
            </table>
        </div>

        <div class="page-break"></div>

        <div class="header-banner" style="padding: 15px 25px; margin-bottom: 15px;">
            <h1 style="font-size: 16pt;">Uplink Matrix</h1>
            <p>Stream-level metrics</p>
        </div>
        
        <div class="card" style="padding: 10px;">
            <table class="data-table">
                <thead>
                    <tr>
                        <th>ID</th><th style="text-align: left;">Pairing Configuration</th><th>VLAN</th>
                        <th>Tx Frames</th><th>Rx Frames</th><th>Loss %</th>
                        <th>Min (ms)</th><th>Avg (ms)</th><th>Max (ms)</th>
                        <th>Tx (Mbps)</th><th>Rx (Mbps)</th>
                    </tr>
                </thead>
                <tbody>
                    {uplink_html}
                    {uplink_sum}
                </tbody>
            </table>
        </div>

    </body>
    </html>
    """

    HTML(string=html_content).write_pdf(filename)
    print(f"PDF Successfully saved to: {os.path.abspath(filename)}\n")

try:
    print("\n" + "="*70)
    print(f"{'INITIATING TEST SEQUENCE':^70}")
    print("="*70)

    # =========================================================================
    # ARGPARSE SETUP & COMMAND LINE OVERRIDES
    # =========================================================================
    parser = argparse.ArgumentParser(description="IxNetwork Performance Test Script")
    parser.add_argument('--mode', type=str, choices=['clear', 'keep'], default='clear',
                        help="Choose 'clear' to wipe IxNetwork config (default) or 'keep' to use existing configuration.")
    parser.add_argument('--cpes', type=int, default=NUM_CPES,
                        help=f"Number of CPEs / Streams (Default: {NUM_CPES})")
    
    # NEW ARGUMENTS FOR JENKINS AUTOMATION
    parser.add_argument('--target', type=float, default=800.0,
                        help="Total aggregate target throughput across all CPEs in Mbps (Default: 800.0)")
    parser.add_argument('--ratio', type=str, default="50:50",
                        help="DL:UL ratio, e.g., 100:0, 50:50, 70:30 (Default: 50:50)")
    parser.add_argument('--output-json', type=str, default="ixia_results.json",
                        help="Path to save output JSON results for automation hooks.")
    parser.add_argument('--time', type=int, default=TEST_SECONDS,
                        help=f"Active test duration in seconds (Default: {TEST_SECONDS})")
    parser.add_argument('--ixia-ip', type=str, default=API_SERVER_IP,
                        help=f"Ixia API server IP/hostname (Default: {API_SERVER_IP})")
    parser.add_argument('--local-ip', type=str, default=DUT_IP,
                        help=f"DUT/local radio IP for SSH/SNMP metrics (Default: {DUT_IP})")
    parser.add_argument('--packet-size', type=str, choices=['imix', 'fixed'], default=frameSizeType,
                        help="Packet profile for traffic generation: imix or fixed")
    parser.add_argument('--bandwidth', type=str, default='HT80',
                        help="Bandwidth profile to apply over SSH before running IXIA")
    parser.add_argument('--mcs-rate', type=str, default='MCS7',
                        help="MCS rate to apply over SSH before running IXIA")
    parser.add_argument('--spatial-stream', type=str, default='2',
                        help="Spatial stream to apply over SSH for each iteration")
    parser.add_argument('--ddrs-rate', type=str, default='MCS7',
                        help="DDRS rate to apply over SSH for each iteration")
    parser.add_argument('--radio-index', type=int, default=1,
                        help="Radio index for wireless.wifi<idx> commands (Default: 1)")
    parser.add_argument('--profile', type=str, default='default',
                        help="Primary profile name from profiles/<name>.yaml")
    parser.add_argument('--recovery-profile', type=str, default='link_formation',
                        help="Recovery profile name from profiles/<name>.yaml")
    parser.add_argument('--traffic-mode', type=str, choices=['benchmark', 'stats_check'], default='benchmark',
                        help="benchmark uses IXIA; stats_check uses TRex for lightweight checks")
    parser.add_argument('--trex-server', type=str, default=TREX_DEFAULTS["host"],
                        help="TRex server for stats-check mode")
    parser.add_argument('--trex-user', type=str, default=TREX_DEFAULTS["user"],
                        help="SSH username for the TRex server")
    parser.add_argument('--trex-password', type=str, default=os.getenv("TREX_PASSWORD", TREX_DEFAULTS["password"]),
                        help="SSH password for the TRex server (or use TREX_PASSWORD env var)")
    parser.add_argument('--trex-dir', type=str, default=TREX_DEFAULTS["directory"],
                        help="Remote TRex install directory")
    parser.add_argument('--trex-pythonpath', type=str, default=TREX_DEFAULTS["pythonpath"],
                        help="Remote PYTHONPATH used by the TRex client script")
    parser.add_argument('--trex-client-script', type=str, default=TREX_DEFAULTS["client_script"],
                        help="Remote TRex client script path")
    parser.add_argument('--trex-ports', type=str, default=TREX_DEFAULTS["ports"],
                        help="Comma-separated TRex ports reserved for the run")
    parser.add_argument('--trex-server-su', type=str, default='',
                        help="Remote TRex SU server 1 IP for 4-7 SUs")
    parser.add_argument('--trex-server-su2', type=str, default='',
                        help="Remote TRex SU server 2 IP for 8-11 SUs")
    parser.add_argument('--trex-server-su3', type=str, default='',
                        help="Remote TRex SU server 3 IP for 12-15 SUs")
    parser.add_argument('--trex-server-su4', type=str, default='',
                        help="Remote TRex SU server 4 IP for 16-19 SUs")
    parser.add_argument('--trex-server-cores', type=int, default=TREX_DEFAULTS["server_cores"],
                        help="Core count passed to t-rex-64 -c")
    parser.add_argument('--trex-run-mode', type=str, choices=['max_throughput', 'counter_check'], default='max_throughput',
                        help="max_throughput captures achieved throughput; counter_check also validates DUT counters")
    parser.add_argument('--trex-direction', type=str, choices=['bidi', 'uplink', 'downlink', 'all'], default='',
                        help="Direction passed to the remote TRex script (blank derives from ratio)")
    parser.add_argument('--trex-proto', type=str, choices=['udp', 'tcp', 'both'], default='udp',
                        help="Protocol passed to the remote TRex script")
    parser.add_argument('--trex-subw', type=str, default='',
                        help="Optional per-SU uplink bandwidth override passed as --subw")
    parser.add_argument('--trex-vlan', type=int, default=-1,
                        help="Optional VLAN ID for the remote TRex script")
    parser.add_argument('--trex-graph', action='store_true',
                        help="Enable graph generation in the remote TRex script")
    parser.add_argument('--trex-su-count', type=int, default=0,
                        help="Override SU count for the TRex client script (0 uses --cpes)")
    parser.add_argument('--trex-dl-bw', type=str, default='',
                        help="Override TRex client downlink bandwidth, e.g. 400M")
    parser.add_argument('--trex-ul-bw', type=str, default='',
                        help="Override TRex client uplink bandwidth, e.g. 400M")
    parser.add_argument('--trex-packet-size', type=int, default=1500,
                        help="Packet size passed to the TRex client script")
    parser.add_argument('--trex-expected-min-mbps', type=float, default=0.0,
                        help="Optional pass/fail threshold for combined RX throughput in TRex mode")
    args = parser.parse_args()

    clear_config_flag = (args.mode == 'clear')
    NUM_CPES = args.cpes
    TEST_SECONDS = args.time
    API_SERVER_IP = args.ixia_ip
    DUT_IP = normalize_ip(args.local_ip)
    frameSizeType = args.packet_size
    fixedFrameSize = 1500
    profile_bundle = load_profile_bundle(
        profile_name=args.profile,
        recovery_profile_name=args.recovery_profile,
        local_ip=args.local_ip,
        username=SSH_USER,
        password=SSH_PASS,
    )
    dut_profile = profile_bundle.active["dut"]
    if dut_profile.get("ip_mode") == "ipv6" or dut_profile.get("strict_ipv6"):
        DUT_IP = normalize_ip(str(dut_profile["local_ipv6"]))
        if ":" not in DUT_IP:
            raise ValueError("Strict IPv6 mode is enabled, but local IPv6 is not configured correctly.")
    recovery_manager = RecoveryManager(profile_bundle)

    # Ratio Parser
    try:
        dl_ratio, ul_ratio = map(float, args.ratio.split(':'))
    except Exception:
        print("[WARNING] Invalid ratio format. Falling back to 50:50")
        dl_ratio, ul_ratio = 50.0, 50.0

    total_ratio = dl_ratio + ul_ratio
    totalCombinedMbps = args.target
    downlink_total_mbps = totalCombinedMbps * (dl_ratio / total_ratio) if total_ratio else 0.0
    uplink_total_mbps = totalCombinedMbps * (ul_ratio / total_ratio) if total_ratio else 0.0
    DOWNLINK_MBPS_PER_CPE = downlink_total_mbps / NUM_CPES if NUM_CPES else 0.0
    UPLINK_MBPS_PER_CPE = uplink_total_mbps / NUM_CPES if NUM_CPES else 0.0
    trex_su_count = args.trex_su_count or NUM_CPES
    trex_dl_bw = args.trex_dl_bw or f"{int(round(downlink_total_mbps))}M"
    trex_ul_bw = args.trex_ul_bw or f"{int(round(uplink_total_mbps))}M"
    if args.trex_direction:
        trex_direction = args.trex_direction
    elif downlink_total_mbps > 0 and uplink_total_mbps > 0:
        trex_direction = "bidi"
    elif downlink_total_mbps > 0:
        trex_direction = "downlink"
    else:
        trex_direction = "uplink"

    totalDownlinkBps = downlink_total_mbps * 1_000_000
    totalUplinkBps = uplink_total_mbps * 1_000_000

    imix_str_list = ", ".join([f"{p['size']}B({p['weight']})" for p in imixProfile])
    frame_output_str = f"Fixed {fixedFrameSize} Bytes" if frameSizeType == 'fixed' else f"IMIX: {imix_str_list}"

    # PRINT SCRIPT EXECUTION CONFIGURATION IMMEDIATELY
    print(f"\n--- SCRIPT EXECUTION CONFIGURATION ---")
    print(f"Mode:            {args.mode.upper()}")
    print(f"Number of CPEs:  {NUM_CPES}")
    print(f"Test Duration:   {TEST_SECONDS} Seconds (Plus {RAMP_UP_SECONDS}s Ramp-up)")
    print(f"Frame Profile:   {frame_output_str}")
    print(f"Ratio:           {args.ratio} (DL:UL)")
    print(f"Downlink Target: {downlink_total_mbps:.2f} Mbps total => {DOWNLINK_MBPS_PER_CPE:.2f} Mbps/CPE x {NUM_CPES}")
    print(f"Uplink Target:   {uplink_total_mbps:.2f} Mbps total => {UPLINK_MBPS_PER_CPE:.2f} Mbps/CPE x {NUM_CPES}")
    print(f"Total Combined:  {totalCombinedMbps:.2f} Mbps")
    if args.traffic_mode == "stats_check":
        print(f"TRex Server:     {args.trex_server} ({args.trex_user})")
        print(f"TRex Ports:      {args.trex_ports}")
        print(f"TRex Run Mode:   {args.trex_run_mode}")
        print(f"TRex Client:     {args.trex_client_script}")
        print(f"TRex Direction:  {trex_direction} | Proto={args.trex_proto}")
        print(f"TRex BWs:        DL={trex_dl_bw} | UL={trex_ul_bw} | SU={trex_su_count}")
    print("-" * 70 + "\n")
    if not asyncio.run(recovery_manager.is_gui_reachable(DUT_IP)):
        print("[RECOVERY] DUT GUI not reachable. Running soft recovery before traffic...")
        asyncio.run(recovery_manager.run_soft_recovery())

    if args.traffic_mode == "stats_check":
        trex_result = run_trex_stats_check(
            trex_server=args.trex_server,
            duration_s=TEST_SECONDS,
            expected_min_mbps=args.trex_expected_min_mbps,
            output_json=None,
            trex_user=args.trex_user,
            trex_password=args.trex_password,
            trex_dir=args.trex_dir,
            trex_pythonpath=args.trex_pythonpath,
            trex_client_script=args.trex_client_script,
            trex_ports=args.trex_ports,
            trex_server_su=args.trex_server_su or None,
            trex_server_su2=args.trex_server_su2 or None,
            trex_server_su3=args.trex_server_su3 or None,
            trex_server_su4=args.trex_server_su4 or None,
            trex_server_cores=args.trex_server_cores,
            trex_su_count=trex_su_count,
            trex_dl_bw=trex_dl_bw,
            trex_ul_bw=trex_ul_bw,
            trex_subw=args.trex_subw or None,
            trex_packet_size=args.trex_packet_size,
            trex_direction=trex_direction,
            trex_protocol=args.trex_proto,
            trex_vlan=args.trex_vlan if args.trex_vlan >= 0 else None,
            trex_enable_graph=args.trex_graph,
            run_mode=args.trex_run_mode,
            dut_host=DUT_IP,
            dut_user=SSH_USER,
            dut_password=SSH_PASS,
            dut_radio_idx=args.radio_index,
        )
        print("Fetching Connected Clients RF Data...")
        connected_clients = fetch_connected_clients(DUT_IP)
        json_export = {
            "mode": "stats_check",
            "profile": {"active": args.profile, "recovery": args.recovery_profile},
            "config": {
                "cpes": NUM_CPES,
                "target_mbps": totalCombinedMbps,
                "ratio": args.ratio,
                "traffic_backend": "trex",
                "trex_server": args.trex_server,
                "trex_user": args.trex_user,
                "trex_ports": args.trex_ports,
                "trex_run_mode": args.trex_run_mode,
                "trex_client_script": args.trex_client_script,
                "trex_dir": args.trex_dir,
                "trex_server_su": args.trex_server_su,
                "trex_server_su2": args.trex_server_su2,
                "trex_server_su3": args.trex_server_su3,
                "trex_server_su4": args.trex_server_su4,
                "trex_dl_bw": trex_dl_bw,
                "trex_ul_bw": trex_ul_bw,
                "trex_subw": args.trex_subw,
                "trex_packet_size": args.trex_packet_size,
                "trex_su_count": trex_su_count,
                "trex_direction": trex_direction,
                "trex_proto": args.trex_proto,
                "trex_vlan": None if args.trex_vlan < 0 else args.trex_vlan,
                "trex_graph": args.trex_graph,
                "bandwidth": args.bandwidth,
                "mcs_rate": args.mcs_rate,
                "spatial_stream": args.spatial_stream,
                "ddrs_rate": args.ddrs_rate,
            },
            "recovery": {
                "attempts": recovery_manager.metrics.attempts,
                "successes": recovery_manager.metrics.successes,
                "failures": recovery_manager.metrics.failures,
                "factory_resets": recovery_manager.metrics.factory_resets,
                "last_error": recovery_manager.metrics.last_error,
            },
            "combined": trex_result.get("combined", {}),
            "downlink": trex_result.get("downlink", {}),
            "uplink": trex_result.get("uplink", {}),
            "rf_metrics": connected_clients,
            "trex": trex_result,
        }
        with open(args.output_json, 'w') as f:
            json.dump(json_export, f, indent=4)
        print(f"TRex stats-check JSON exported to: {os.path.abspath(args.output_json)}")
        sys.exit(0)

    configure_bandwidth_and_mcs(
        DUT_IP, SSH_USER, SSH_PASS, args.radio_index,
        args.bandwidth, args.mcs_rate, args.spatial_stream, args.ddrs_rate
    )

    # 1. Fetch DUT Pre-Test Metrics
    env_metrics = fetch_ssh_metrics(DUT_IP, SSH_USER, SSH_PASS, WIFI_INT, WLAN_INT)
    print("Fetching Static SNMP Configuration...")
    snmp_config = fetch_snmp_config(DUT_IP)
    print("Fetching Connected Clients RF Data...")
    connected_clients = fetch_connected_clients(DUT_IP)

    # --- PRINT CONSOLE DASHBOARD ---
    print("\n" + "-"*70)
    print(f"{'PRE-TEST WIRELESS ENVIRONMENT (SSH)':^70}")
    print("-"*70)
    print(f"Operating Freq      : {env_metrics.get('freq_ghz', '-')} GHz")
    print(f"Channel Utilization : {env_metrics.get('chan_util', '-')}%")
    print(f"OBSS Value          : {env_metrics.get('obss', '-')}%")
    
    print("\n" + "-"*70)
    print(f"{'DUT STATIC CONFIGURATION (SNMP)':^70}")
    print("-"*70)
    print(f"Radio Mode          : {snmp_config.get('Radio Mode', '-')}")
    print(f"Link Type           : {snmp_config.get('Link Type', '-')}")
    print(f"SSID                : {snmp_config.get('SSID', '-')}")
    print(f"Conf Channel        : {snmp_config.get('Conf Chan', '-')}  |  Conf BW : {snmp_config.get('Conf BW', '-')} MHz")
    print(f"Act Channel         : {snmp_config.get('Act Chan', '-')}  |  Act BW  : {snmp_config.get('Act BW', '-')} MHz")
    print(f"Tx Power            : {snmp_config.get('Tx Power', '-')} dBm")
    print(f"DDRS Status         : {snmp_config.get('DDRS', '-')}  |  ATPC Status : {snmp_config.get('ATPC', '-')}")
    print(f"DCS Status          : {snmp_config.get('DCS', '-')}  |  VLAN Config : {snmp_config.get('VLAN Config', '-')}")
    
    print("\n" + "-"*70)
    print(f"{'CONNECTED CLIENTS (RF TELEMETRY)':^70}")
    print("-"*70)
    if connected_clients:
        print(f"{'IP Address':<15} | {'L.SNR A1':<8} | {'L.SNR A2':<8} | {'R.SNR A1':<8} | {'R.SNR A2':<8} | {'Tx Rate':<7} | {'Rx Rate':<7}")
        print("-" * 70)
        for c in connected_clients:
            print(f"{c['ip']:<15} | {c['l_snr1']:<8} | {c['l_snr2']:<8} | {c['r_snr1']:<8} | {c['r_snr2']:<8} | {c['tx_rate']:<7} | {c['rx_rate']:<7}")
    else:
        print("No connected wireless clients detected.")
    print("="*70 + "\n")

    print(f"Connecting to IxNetwork REST API... (ClearConfig={clear_config_flag})")
    session = SessionAssistant(IpAddress=API_SERVER_IP, RestPort=80, SessionName=None, SessionId=None, ApiKey=None,
                               ClearConfig=clear_config_flag, UrlPrefix="ixnetwork-mw", LogLevel='warning', LogFilename='restpy.log')
    ixNetwork = session.Ixnetwork

    ########################################################################################
    # Assign Ports
    ########################################################################################
    vport = dict()
    portMap = session.PortMapAssistant()

    for index, port in enumerate(physicalPorts):
        portName = 'Port_{}'.format(index + 1)
        vport[portName] = portMap.Map(IpAddress=port[0], CardId=port[1], PortId=port[2], Name=portName)

    portMap.Connect(forceTakePortOwnership)
    vport1 = ixNetwork.Vport.find(Name='Port_1')[0]
    vport2 = ixNetwork.Vport.find(Name='Port_2')[0]

    ########################################################################################
    #  Topology configuration START
    ########################################################################################

    existing_topology1 = ixNetwork.Topology.find(Name='Topology-Tx')
    if len(existing_topology1) > 0 and not clear_config_flag:
        print('Topology-Tx already exists. Using existing configuration.')
        topology1 = existing_topology1[0]
    else:
        print('Create Topology-Tx')
        topology1 = ixNetwork.Topology.add(Name='Topology-Tx', Ports=vport1)
        topology1_deviceGroup1 = topology1.DeviceGroup.add(Name='DeviceGroup1-Tx', Multiplier=str(NUM_CPES))
        topology1_ethernet1 = topology1_deviceGroup1.Ethernet.add()
        topology1_ethernet1.Mac.Increment(start_value='00:11:01:01:00:01', step_value='00:00:00:00:00:01')
        topology1_ethernet1.EnableVlans.Single(True)
        topology1_ethernet1_vlanValue = topology1_ethernet1.Vlan.find()[0].VlanId.Increment(start_value=21, step_value=1)
        topology1_ipv41 = topology1_deviceGroup1.Ethernet.find().Ipv4.add()
        topology1_ipv41.Address.Increment(start_value="172.10.10.51", step_value="0.0.0.1")
        topology1_ipv41.GatewayIp.Increment(start_value="172.10.10.71", step_value="0.0.0.1")
        topology1_ipv41.Prefix.Single(24)
        topology1_ipv41.ResolveGateway.Single(True)

    existing_topology2 = ixNetwork.Topology.find(Name='Topology-Rx')
    if len(existing_topology2) > 0 and not clear_config_flag:
        print('Topology-Rx already exists. Using existing configuration.')
        topology2 = existing_topology2[0]
    else:
        print('Create Topology-Rx')
        topology2 = ixNetwork.Topology.add(Name='Topology-Rx', Ports=vport2)
        topology2_deviceGroup1 = topology2.DeviceGroup.add(Name='DeviceGroup1-Rx', Multiplier=str(NUM_CPES))
        topology2_ethernet1 = topology2_deviceGroup1.Ethernet.add()
        topology2_ethernet1.Mac.Increment(start_value='00:12:01:01:00:01', step_value='00:00:00:00:00:01')
        topology2_ethernet1.EnableVlans.Single(True)
        topology2_ethernet1_vlanValue = topology2_ethernet1.Vlan.find()[0].VlanId.Increment(start_value=21, step_value=1)
        topology2_ipv41 = topology2_deviceGroup1.Ethernet.find().Ipv4.add()
        topology2_ipv41.Address.Increment(start_value="172.10.10.71", step_value="0.0.0.1")
        topology2_ipv41.GatewayIp.Increment(start_value="172.10.10.51", step_value="0.0.0.1")
        topology2_ipv41.Prefix.Single(24)
        topology2_ipv41.ResolveGateway.Single(True)

    time.sleep(3)
    ixNetwork.StartAllProtocols(Arg1='sync')
    time.sleep(3)

    if ixNetwork.Traffic.State in ['started', 'locked']:
        print("WARNING: Some traffic is already running! Stopping it now before proceeding...")
        ixNetwork.Traffic.StopStatelessTrafficBlocking()
        time.sleep(2)

    def configure_traffic_item(name, src_topo, dst_topo, target_bps):
        print(f"Configuring Traffic Item: '{name}'")
        
        ti_list = ixNetwork.Traffic.TrafficItem.find(Name=name)
        if len(ti_list) > 0 and not clear_config_flag:
            print(f"  -> Found existing traffic item. Updating...")
            traffic_obj = ti_list[0]
        else:
            print(f"  -> Building '{name}' from scratch...")
            traffic_obj = ixNetwork.Traffic.TrafficItem.add(Name=name, BiDirectional=False, TrafficType='ipv4')
            traffic_obj.EndpointSet.add(Sources=src_topo, Destinations=dst_topo)
            
        # Safely enforce 1:1 Mapping
        try: 
            traffic_obj.SrcDestMesh = 'oneToOne'
        except Exception: 
            pass

        # Safely enable Flow Tracking
        tracking = traffic_obj.Tracking.find()
        if len(tracking) > 0:
            tracking[0].TrackBy = ["trackingenabled0", "sourceDestValuePair0", "vlanVlanId0"]
        else:
            traffic_obj.Tracking.add(TrackBy=["trackingenabled0", "sourceDestValuePair0", "vlanVlanId0"])

        # Iterate over all ConfigElements to enforce GUI consistency
        for config_element in traffic_obj.ConfigElement.find():
            
            # 1. APPLY THROUGHPUT RATE
            print(f"  -> Setting target transmission to {target_bps / 1_000_000} Mbps")
            config_element.FrameRate.Type = 'bitsPerSecond'
            config_element.FrameRate.Rate = str(target_bps)

            # 2. APPLY RATE DISTRIBUTION 
            config_element.FrameRateDistribution.PortDistribution = 'splitRateEvenly'
            try: 
                config_element.FrameRateDistribution.StreamDistribution = 'splitRateEvenly'
            except Exception: 
                pass

            # 3. APPLY FRAME SIZE (IMIX VS FIXED)
            if frameSizeType == 'fixed':
                print(f"  -> Setting Frame Profile to Fixed {fixedFrameSize} Bytes")
                config_element.FrameSize.Type = 'fixed'
                config_element.FrameSize.FixedSize = int(fixedFrameSize)
            else:
                print(f"  -> Setting Frame Profile to IMIX...")
                config_element.FrameSize.Type = 'weightedPairs'
                
                # Use flat list of integers to bypass IxNetwork JSON casting crash
                flat_list = []
                for p in imixProfile:
                    flat_list.extend([int(p['size']), int(p['weight'])])
                
                try:
                    config_element.FrameSize.WeightedPairs = flat_list
                    print(f"  -> IMIX Profile Applied Successfully!")
                except Exception as e:
                    print(f"  [ERROR] IMIX assignment failed: {e}. Forcing Fixed 1500B.")
                    config_element.FrameSize.Type = 'fixed'
                    config_element.FrameSize.FixedSize = 1500
                    
        return traffic_obj

    # Create the required traffic items
    if totalDownlinkBps > 0:
        ti_downlink = configure_traffic_item('Downlink Traffic', topology1, topology2, totalDownlinkBps)
        ti_downlink.Generate()

    if totalUplinkBps > 0:
        ti_uplink = configure_traffic_item('Uplink Traffic', topology2, topology1, totalUplinkBps)
        ti_uplink.Generate()

    print("\nGenerate, Apply and Start Traffic...")
    ixNetwork.Traffic.Apply()
    time.sleep(2)
    ixNetwork.Traffic.StartStatelessTrafficBlocking()

    history_time = []
    history_tx = []
    history_rx = []
    history_lat_streams = []
    history_rx_streams = []

    sys.stdout.write(f"\nRamping up traffic for {RAMP_UP_SECONDS} seconds (Unrecorded): [")
    sys.stdout.flush()
    for i in range(1, RAMP_UP_SECONDS + 1):
        time.sleep(1)
        sys.stdout.write("r")
        sys.stdout.flush()
    sys.stdout.write("] DONE\n")

    sys.stdout.write(f"Recording stable traffic for {TEST_SECONDS} seconds: [")
    sys.stdout.flush()

    for i in range(1, TEST_SECONDS + 1):
        time.sleep(1)
        try:
            live_stats = session.StatViewAssistant('Flow Statistics')
            cur_tx_mbps = 0.0
            cur_rx_mbps = 0.0
            cur_stream_latencies = []
            cur_stream_rx_mbps = []

            for row in live_stats.Rows:
                tx_bps = float(safe_get(row, 'Tx Rate (bps)', '0'))
                rx_bps = float(safe_get(row, 'Rx Rate (bps)', '0'))
                lat_ns = float(safe_get(row, 'Store-Forward Avg Latency (ns)', safe_get(row, 'Avg Latency (ns)', '0')))

                cur_tx_mbps += (tx_bps / 1_000_000.0)
                cur_rx_mbps += (rx_bps / 1_000_000.0)

                stream_lat = lat_ns / 1_000_000.0
                stream_rx = rx_bps / 1_000_000.0
                cur_stream_latencies.append(stream_lat)
                cur_stream_rx_mbps.append(stream_rx)

            history_time.append(i)
            history_tx.append(cur_tx_mbps)
            history_rx.append(cur_rx_mbps)
            history_lat_streams.append(cur_stream_latencies)
            history_rx_streams.append(cur_stream_rx_mbps)
            sys.stdout.write("#")
        except Exception:
            sys.stdout.write("x")
        sys.stdout.flush()
    sys.stdout.write("] DONE\n\n")

    time.sleep(2)
    flowStatistics = session.StatViewAssistant('Flow Statistics')

    print("Print Final Flow Statistics \n")

    header = "{:<4} | {:<38} | {:<5} | {:<9} | {:<9} | {:<6} | {:<9} | {:<9} | {:<9} | {:<10} | {:<10}".format(
        "Row", "Source/Dest Value Pair", "VLAN", "Tx Frames", "Rx Frames", "Loss %", "Min(ms)", "Avg(ms)", "Max(ms)", "Tx(Mbps)", "Rx(Mbps)"
    )

    report_data = []
    
    dl_tx_total, dl_rx_total = 0.0, 0.0
    dl_mins, dl_avgs, dl_maxes, dl_loss_pcts = [], [], [], []
    
    ul_tx_total, ul_rx_total = 0.0, 0.0
    ul_mins, ul_avgs, ul_maxes, ul_loss_pcts = [], [], [], []

    print(header)

    current_direction_printed = None

    for rowNumber, flowStat in enumerate(flowStatistics.Rows):
        ti_name = safe_get(flowStat, 'Traffic Item', '')

        if 'Downlink' in ti_name: direction_name = 'Downlink'
        elif 'Uplink' in ti_name: direction_name = 'Uplink'
        else: direction_name = 'Downlink' if totalDownlinkBps > 0 else 'Uplink'

        if direction_name != current_direction_printed:
            print("-" * 145)
            if direction_name == 'Downlink':
                print(f"{COLOR_DOWNLINK}{' ' * 59} --- DOWNLINK STREAMS --- {COLOR_RESET}")
            else:
                print(f"{COLOR_UPLINK}{' ' * 60} --- UPLINK STREAMS --- {COLOR_RESET}")
            print("-" * 145)
            current_direction_printed = direction_name

        tx_frames = safe_get(flowStat, 'Tx Frames', '0')

        if str(tx_frames).isdigit() and int(tx_frames) > 0:
            vlan_id = safe_get(flowStat, 'VLAN:VLAN-ID', 'N/A')
            rx_frames = safe_get(flowStat, 'Rx Frames', '0')
            loss_pct_str = safe_get(flowStat, 'Loss %', '0')
            src_dest = safe_get(flowStat, 'Source/Dest Value Pair', 'N/A')

            min_lat_ns = safe_get(flowStat, 'Store-Forward Min Latency (ns)', safe_get(flowStat, 'Min Latency (ns)', '0'))
            avg_lat_ns = safe_get(flowStat, 'Store-Forward Avg Latency (ns)', safe_get(flowStat, 'Avg Latency (ns)', '0'))
            max_lat_ns = safe_get(flowStat, 'Store-Forward Max Latency (ns)', safe_get(flowStat, 'Max Latency (ns)', '0'))

            min_ms_f = ns_to_ms_float(min_lat_ns)
            avg_ms_f = ns_to_ms_float(avg_lat_ns)
            max_ms_f = ns_to_ms_float(max_lat_ns)

            try: loss_pct_f = float(loss_pct_str)
            except Exception: loss_pct_f = 0.0

            tx_bps_str = safe_get(flowStat, 'Tx Rate (bps)', '0')
            rx_bps_str = safe_get(flowStat, 'Rx Rate (bps)', '0')

            try: tx_mbps_f = float(tx_bps_str) / 1_000_000.0
            except Exception: tx_mbps_f = 0.0

            try: rx_mbps_f = float(rx_bps_str) / 1_000_000.0
            except Exception: rx_mbps_f = 0.0

            if direction_name == 'Downlink':
                dl_tx_total += tx_mbps_f
                dl_rx_total += rx_mbps_f
                dl_loss_pcts.append(loss_pct_f)
                if tx_mbps_f > 0:
                    dl_mins.append(min_ms_f)
                    dl_avgs.append(avg_ms_f)
                    dl_maxes.append(max_ms_f)
            else:
                ul_tx_total += tx_mbps_f
                ul_rx_total += rx_mbps_f
                ul_loss_pcts.append(loss_pct_f)
                if tx_mbps_f > 0:
                    ul_mins.append(min_ms_f)
                    ul_avgs.append(avg_ms_f)
                    ul_maxes.append(max_ms_f)

            report_data.append({
                "row": rowNumber, "pair": src_dest, "vlan": vlan_id,
                "tx_frames": tx_frames, "rx_frames": rx_frames, "loss": loss_pct_f,
                "min_ms": min_ms_f, "avg_ms": avg_ms_f, "max_ms": max_ms_f,
                "tx_mbps": tx_mbps_f, "rx_mbps": rx_mbps_f, "direction": direction_name
            })

            row_str = "{:<4} | {:<38} | {:<5} | {:<9} | {:<9} | {:<6.3f} | {:<9.3f} | {:<9.3f} | {:<9.3f} | {:<10.2f} | {:<10.2f}".format(
                rowNumber, src_dest, vlan_id, tx_frames, rx_frames, loss_pct_f, min_ms_f, avg_ms_f, max_ms_f, tx_mbps_f, rx_mbps_f
            )

            if direction_name == 'Downlink': print(COLOR_DOWNLINK + row_str + COLOR_RESET)
            else: print(COLOR_UPLINK + row_str + COLOR_RESET)

    print("-" * 145)

    def safe_min(lst): return min(lst) if lst else 0.0
    def safe_max(lst): return max(lst) if lst else 0.0
    def safe_avg(lst): return sum(lst) / len(lst) if lst else 0.0

    dl_min_val, dl_avg_val, dl_max_val = safe_min(dl_mins), safe_avg(dl_avgs), safe_max(dl_maxes)
    ul_min_val, ul_avg_val, ul_max_val = safe_min(ul_mins), safe_avg(ul_avgs), safe_max(ul_maxes)
    dl_avg_loss, ul_avg_loss = safe_avg(dl_loss_pcts), safe_avg(ul_loss_pcts)
    
    comb_tx = dl_tx_total + ul_tx_total
    comb_rx = dl_rx_total + ul_rx_total
    comb_min = safe_min(dl_mins + ul_mins)
    comb_avg = safe_avg(dl_avgs + ul_avgs)
    comb_max = safe_max(dl_maxes + ul_maxes)
    comb_avg_loss = safe_avg(dl_loss_pcts + ul_loss_pcts)

    print("\n" + "="*95)
    print(f"{'FINAL DIRECTIONAL SUMMARY':^95}")
    print("="*95)
    print("{:<10} | {:<12} | {:<12} | {:<8} | {:<12} | {:<12} | {:<12}".format("Direction", "Tx (Mbps)", "Rx (Mbps)", "Loss %", "Min Latency", "Avg Latency", "Max Latency"))
    print("-" * 95)
    print("{:<10} | {:<12.2f} | {:<12.2f} | {:<7.3f}% | {:<9.3f} ms | {:<9.3f} ms | {:<9.3f} ms".format("DOWNLINK", dl_tx_total, dl_rx_total, dl_avg_loss, dl_min_val, dl_avg_val, dl_max_val))
    print("{:<10} | {:<12.2f} | {:<12.2f} | {:<7.3f}% | {:<9.3f} ms | {:<9.3f} ms | {:<9.3f} ms".format("UPLINK", ul_tx_total, ul_rx_total, ul_avg_loss, ul_min_val, ul_avg_val, ul_max_val))
    print("-" * 95)
    print("{:<10} | {:<12.2f} | {:<12.2f} | {:<7.3f}% | {:<9.3f} ms | {:<9.3f} ms | {:<9.3f} ms".format("COMBINED", comb_tx, comb_rx, comb_avg_loss, comb_min, comb_avg, comb_max))
    print("="*95 + "\n")

    hist_dict = {'time': history_time, 'tx': history_tx, 'rx': history_rx, 'lat_streams': history_lat_streams, 'rx_streams': history_rx_streams}
    conf_dict = {
        'cpes': NUM_CPES,
        'frame_config': frame_output_str,
        'down_target': totalDownlinkBps/1_000_000,
        'up_target': totalUplinkBps/1_000_000,
        'duration': TEST_SECONDS,
        'dl_per_cpe': DOWNLINK_MBPS_PER_CPE,
        'ul_per_cpe': UPLINK_MBPS_PER_CPE
    }
    
    sum_dict = {'tx': comb_tx, 'rx': comb_rx, 'loss': comb_avg_loss, 'lat': comb_avg}

    current_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"IxNetwork_Performance_Report_{current_time_str}.pdf"

    generate_pdf_report(report_data, hist_dict, conf_dict, sum_dict, env_metrics, snmp_config, connected_clients, filename=report_filename)

    # =========================================================================
    # JSON EXPORT FOR JENKINS PIPELINE
    # =========================================================================
    json_export = {
        "mode": "benchmark",
        "profile": {"active": args.profile, "recovery": args.recovery_profile},
        "config": {
            "cpes": NUM_CPES,
            "target_mbps": totalCombinedMbps,
            "ratio": args.ratio,
            "ixia_ip": API_SERVER_IP,
            "local_ip": DUT_IP,
            "packet_profile": frameSizeType,
            "fixed_frame_size": fixedFrameSize,
            "bandwidth": args.bandwidth,
            "mcs_rate": args.mcs_rate,
            "spatial_stream": args.spatial_stream,
            "ddrs_rate": args.ddrs_rate
        },
        "recovery": {
            "attempts": recovery_manager.metrics.attempts,
            "successes": recovery_manager.metrics.successes,
            "failures": recovery_manager.metrics.failures,
            "factory_resets": recovery_manager.metrics.factory_resets,
            "last_error": recovery_manager.metrics.last_error,
        },
        "combined": {"tx_mbps": comb_tx, "rx_mbps": comb_rx, "loss_pct": comb_avg_loss, "latency_ms": comb_avg},
        "downlink": {"tx_mbps": dl_tx_total, "rx_mbps": dl_rx_total, "loss_pct": dl_avg_loss, "latency_ms": dl_avg_val},
        "uplink": {"tx_mbps": ul_tx_total, "rx_mbps": ul_rx_total, "loss_pct": ul_avg_loss, "latency_ms": ul_avg_val},
        "rf_metrics": connected_clients
    }
    
    with open(args.output_json, 'w') as f:
        json.dump(json_export, f, indent=4)
    print(f"JSON Output automatically exported for Jenkins parsing to: {os.path.abspath(args.output_json)}\n")

except Exception as errMsg:
    print('\n%s' % traceback.format_exc(None, errMsg))

finally:
    if 'ixNetwork' in locals():
        print("Performing script cleanup: Stopping traffic and protocols...")
        try:
            ixNetwork.Traffic.StopStatelessTrafficBlocking()
            ixNetwork.StopAllProtocols(Arg1='sync')
        except Exception as cleanupErr:
            print(f"Cleanup non-fatal error: {cleanupErr}")

    if debugMode == False and 'session' in locals():
        try:
            for vport in ixNetwork.Vport.find():
                vport.ReleasePort()
        except: pass

        if session.TestPlatform.Platform != 'windows':
            try: session.Session.remove()
            except: pass