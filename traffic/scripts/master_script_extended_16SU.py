#!/usr/bin/env python3
"""
Multi-server TRex PTMP throughput generator for BSU + up to 16 SUs.

Designed to be executed on the TRex control host (or Jenkins node) with:
  export PYTHONPATH=/opt/v3.06/automation/trex_control_plane/interactive/

AutomationTestBed invokes this script remotely via traffic/trex_runner.py and
passes port selection through TREX_PORTS (e.g. "0,1") or --ports.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
import time
import warnings

import matplotlib.pyplot as plt
import pandas as pd
from prettytable import PrettyTable
from trex_stl_lib.api import *

warnings.filterwarnings("ignore", category=DeprecationWarning)

RESULTS_CSV_FILE = "trex_test_results.csv"
DEFAULT_LIVE_STATS_INTERVAL = 2
INDEFINITE_LIVE_STATS_INTERVAL = 30
GRAPH_TRIM_SECONDS = 5
SMOOTHING_WINDOW = 5
ZERO_RX_THRESHOLD = 3
RAMP_UP_SECONDS = 5
IMIX_PROFILE = [
    {"size": 64, "percent": 50},
    {"size": 256, "percent": 30},
    {"size": 512, "percent": 20},
]


def parse_bw_str_to_mbps(bw_str):
    if not isinstance(bw_str, str):
        return float(bw_str)
    bw_str = bw_str.strip().upper()
    if bw_str.endswith("G"):
        return float(bw_str[:-1]) * 1000
    if bw_str.endswith("M"):
        return float(bw_str[:-1])
    return float(bw_str)


def parse_port_allowlist(ports_spec) -> set[int] | None:
    if ports_spec is None:
        return None
    if isinstance(ports_spec, (list, tuple, set)):
        values = ports_spec
    else:
        text = str(ports_spec).strip()
        if not text:
            return None
        values = text.split(",")
    parsed = {int(str(item).strip()) for item in values if str(item).strip() != ""}
    return parsed or None


def resolve_ports_spec(cli_ports: str | None) -> set[int] | None:
    return parse_port_allowlist(cli_ports or os.environ.get("TREX_PORTS"))


def size_type(value):
    x_lower = value.lower()
    if x_lower == "imix":
        return "IMIX"
    if x_lower == "0":
        raise argparse.ArgumentTypeError(
            "Packet size '0' is not allowed. Please use 'imix' for IMIX tests."
        )
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a valid integer or the keyword 'imix'."
        ) from exc


def plot_throughput_over_time(history, direction, size, proto, port_map):
    if not history:
        print(f"\nNo live stats recorded for {size}/{direction}/{proto}, skipping graph generation.")
        return
    title_str = f"'{direction.upper()}' at {size} ({proto.upper()})"
    print(f"\nGenerating throughput graph for {title_str}...")
    try:
        df = pd.DataFrame(history)
        if df.empty:
            return

        if len(df) > 3:
            trimmed_df = df.iloc[1:-1].copy()
            print("Graph data trimmed to exclude the first and last samples.")
        else:
            trimmed_df = df.copy()

        mapping = {(p["server"], p["port_id"]): p["label"] for p in port_map}
        trimmed_df["label"] = trimmed_df.apply(
            lambda row: mapping.get((row["server"], row["port"])), axis=1
        )
        start_time = trimmed_df["elapsed_s"].min()
        trimmed_df["elapsed_s"] = trimmed_df["elapsed_s"] - start_time

        plt.style.use("ggplot")
        fig, ax = plt.subplots(figsize=(15, 8))
        unique_labels = sorted(trimmed_df["label"].unique())
        colors = plt.get_cmap("tab10", 10)
        for index, label in enumerate(unique_labels):
            port_df = trimmed_df[trimmed_df["label"] == label].copy()
            if port_df.empty:
                continue
            color = colors(index % 10)
            avg_val = port_df["rx_mbps"].mean()
            min_val = port_df["rx_mbps"].min()
            max_val = port_df["rx_mbps"].max()
            stats_label = (
                f"{label} RX\n"
                f"  Avg: {avg_val:<7.2f} Min: {min_val:<7.2f} Max: {max_val:<7.2f}"
            )
            if len(port_df) >= SMOOTHING_WINDOW:
                port_df["rx_smoothed"] = (
                    port_df["rx_mbps"].rolling(window=SMOOTHING_WINDOW, min_periods=1).mean()
                )
                line_data = port_df["rx_smoothed"]
            else:
                line_data = port_df["rx_mbps"]
            ax.plot(
                port_df["elapsed_s"],
                line_data,
                label=stats_label,
                color=color,
                linewidth=2.5,
            )
            ax.plot(
                port_df["elapsed_s"],
                port_df["rx_mbps"],
                color=color,
                alpha=0.3,
                marker="o",
                linestyle="None",
                markersize=3,
                label="_nolegend_",
            )
        ax.set_title(
            f"RX Throughput Over Time ({size} / {direction.capitalize()} / {proto.upper()})",
            fontsize=16,
            fontweight="bold",
        )
        ax.set_xlabel("Time (seconds)", fontsize=12)
        ax.set_ylabel("Throughput (Mbps)", fontsize=12)
        font_props = {"family": "monospace", "size": "medium"}
        ax.legend(
            loc="center left",
            bbox_to_anchor=(1.02, 0.5),
            prop=font_props,
            title="Device Stats (Mbps)",
        )
        ax.grid(True, which="both", linestyle="--", linewidth=0.5)
        plt.tight_layout(rect=[0, 0, 0.92, 1])
        filename = f"throughput_{size}_{direction}_{proto}.png"
        plt.savefig(filename)
        print(f"Graph saved as {filename}")
    except Exception as exc:
        print(f"\nCould not generate graph. Error: {exc}")


def print_summary_table(size, direction, proto, result, port_map):
    history = result.get("history", [])
    if not history:
        return
    table = PrettyTable()
    table.field_names = [
        "Device",
        "Avg TX (Mbps)",
        "Min TX (Mbps)",
        "Max TX (Mbps)",
        "Avg RX (Mbps)",
        "Min RX (Mbps)",
        "Max RX (Mbps)",
    ]
    table.align = "r"
    table.align["Device"] = "l"
    df = pd.DataFrame(history)
    if len(df) >= 2:
        df = df.iloc[1:].copy()

    mapping = {(p["server"], p["port_id"]): p["label"] for p in port_map}
    df["label"] = df.apply(lambda row: mapping.get((row["server"], row["port"])), axis=1)

    total_tx_accumulator = 0.0
    total_rx_accumulator = 0.0
    for label in sorted(df["label"].unique()):
        port_df = df[df["label"] == label]
        if port_df.empty:
            continue
        avg_tx = port_df["tx_mbps"].mean()
        min_tx = port_df["tx_mbps"].min()
        max_tx = port_df["tx_mbps"].max()
        avg_rx = port_df["rx_mbps"].mean()
        min_rx = port_df["rx_mbps"].min()
        max_rx = port_df["rx_mbps"].max()
        table.add_row(
            [
                label,
                f"{avg_tx:.2f}",
                f"{min_tx:.2f}",
                f"{max_tx:.2f}",
                f"{avg_rx:.2f}",
                f"{min_rx:.2f}",
                f"{max_rx:.2f}",
            ]
        )
        is_bsu = label == "BSU"
        if (direction == "downlink" and not is_bsu) or (direction == "uplink" and is_bsu) or (
            direction == "bidi"
        ):
            total_rx_accumulator += avg_rx
        if (direction == "downlink" and is_bsu) or (direction == "uplink" and not is_bsu) or (
            direction == "bidi"
        ):
            total_tx_accumulator += avg_tx
    table.add_row(["-" * 8, "-" * 15, "-" * 15, "-" * 15, "-" * 15, "-" * 15, "-" * 15])
    table.add_row(
        [
            "TOTAL",
            f"{total_tx_accumulator:.2f}",
            "N/A",
            "N/A",
            f"{total_rx_accumulator:.2f}",
            "N/A",
            "N/A",
        ]
    )
    title = f" Summary for {size} / {direction.upper()} / {proto.upper()} (from Samples) "
    print("\n\n" + title.center(80, "="))
    print(table)


def print_consolidated_summary(results, port_map):
    if not results:
        return
    all_protos: set[str] = set()
    all_directions: set[str] = set()
    all_sizes: set[str | int] = set()
    for size, size_res in results.items():
        all_sizes.add(size)
        for direction, dir_res in size_res.items():
            all_directions.add(direction)
            all_protos.update(dir_res.keys())
    if not all_directions or not all_protos:
        return

    sorted_protos = sorted(all_protos)
    sorted_directions = sorted(all_directions)
    for proto in sorted_protos:
        headers = ["Pkt Size"] + [f"{direction.capitalize()} (Mbps)" for direction in sorted_directions]
        table = PrettyTable()
        table.field_names = headers
        table.align = "r"
        table.align["Pkt Size"] = "l"
        for size in sorted(list(all_sizes), key=lambda item: (isinstance(item, str), item)):
            row = [size]
            for direction in sorted_directions:
                result = results.get(size, {}).get(direction, {}).get(proto)
                if result and result.get("history"):
                    history = result["history"]
                    df = pd.DataFrame(history)
                    if len(df) >= 2:
                        df = df.iloc[1:].copy()
                    mapping = {(p["server"], p["port_id"]): p["label"] for p in port_map}
                    df["label"] = df.apply(
                        lambda item: mapping.get((item["server"], item["port"])), axis=1
                    )
                    total_avg_accumulator = 0.0
                    for label in df["label"].unique():
                        port_df = df[df["label"] == label]
                        is_bsu = label == "BSU"
                        if (direction == "downlink" and not is_bsu) or (
                            direction == "uplink" and is_bsu
                        ) or (direction == "bidi"):
                            if not port_df.empty:
                                total_avg_accumulator += port_df["rx_mbps"].mean()
                    row.append(f"{total_avg_accumulator:.2f}")
                else:
                    row.append("N/A")
            table.add_row(row)
        title = f" Consolidated Summary ({proto.upper()}) "
        print("\n\n" + title.center(80, "="))
        print(table)


def collect_and_print_live_stats(
    current_stats,
    last_stats,
    time_delta,
    start_time,
    port_map,
    history_log,
    enable_debug,
):
    if time_delta < 0.1:
        return
    stats_data = []
    for port in port_map:
        server_name = port["server"]
        port_id = port["port_id"]
        if server_name not in current_stats or port_id not in current_stats[server_name]:
            continue
        stats_now = current_stats[server_name][port_id]
        stats_before = last_stats[server_name][port_id]
        delta_opackets = stats_now["opackets"] - stats_before["opackets"]
        delta_ipackets = stats_now["ipackets"] - stats_before["ipackets"]
        delta_obytes = stats_now["obytes"] - stats_before["obytes"]
        delta_ibytes = stats_now["ibytes"] - stats_before["ibytes"]
        tx_pps = delta_opackets / time_delta
        rx_pps = delta_ipackets / time_delta
        tx_mbps = (delta_obytes * 8) / (time_delta * 1_000_000)
        rx_mbps = (delta_ibytes * 8) / (time_delta * 1_000_000)
        stats_data.append(
            [port["label"], f"{tx_mbps:.2f}", f"{rx_mbps:.2f}", f"{tx_pps:,.0f}", f"{rx_pps:,.0f}"]
        )
        elapsed_s = time.time() - start_time
        history_log.append(
            {
                "elapsed_s": elapsed_s,
                "server": server_name,
                "port": port_id,
                "tx_mbps": tx_mbps,
                "rx_mbps": rx_mbps,
            }
        )

    if enable_debug:
        table = PrettyTable()
        table.field_names = ["Device", "TX Mbps", "RX Mbps", "TX pps", "RX pps"]
        table.align = "r"
        for row_data in stats_data:
            table.add_row(row_data)
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n--- Live Stats @ {timestamp} ---")
        print(table)


def run_multi_server_test(
    bsu_server_ip,
    su_server_ip,
    su_server_2_ip,
    su_server_3_ip,
    su_server_4_ip,
    total_sus_req,
    bw_str,
    dl_bw_str,
    ul_bw_str,
    subw_str,
    sizes_to_test,
    duration,
    direction_mode,
    enable_graph,
    proto_mode,
    vlan_id,
    enable_debug,
    ports_spec=None,
):
    allowed_bsu_ports = resolve_ports_spec(ports_spec)
    is_imix_test_run = "IMIX" in sizes_to_test
    if is_imix_test_run and len(sizes_to_test) > 1:
        print("\nError: For IMIX tests, please specify only 'imix' as the size.")
        return
    if duration == 0:
        if len(sizes_to_test) > 1:
            print(
                f"\nInfo: Multiple sizes provided for stability test. Using the first size: {sizes_to_test[0]}"
            )
            sizes_to_test = [sizes_to_test[0]]
        if direction_mode == "all":
            print("\nInfo: --dir was set to 'all' with --duration 0. Defaulting to 'bidi'.")
            direction_mode = "bidi"
        if proto_mode == "both":
            print("\nInfo: --proto was set to 'both' with --duration 0. Defaulting to 'udp'.")
            proto_mode = "udp"

    if not bw_str and not dl_bw_str:
        print("\nError: No downlink bandwidth specified. Please provide --bw or --dl-bw.")
        return
    if not bw_str and not ul_bw_str:
        print("\nError: No uplink bandwidth specified. Please provide --bw or --ul-bw.")
        return

    if total_sus_req > 3 and not su_server_ip:
        print(
            f"\nError: Requested {total_sus_req} SUs, which requires a second server, but --server-su was not provided."
        )
        return
    if total_sus_req > 7 and not su_server_2_ip:
        print(
            f"\nError: Requested {total_sus_req} SUs, which requires a third server, but --server-su2 was not provided."
        )
        return
    if total_sus_req > 11 and not su_server_3_ip:
        print(
            f"\nError: Requested {total_sus_req} SUs, which requires a fourth server, but --server-su3 was not provided."
        )
        return
    if total_sus_req > 15 and not su_server_4_ip:
        print(
            f"\nError: Requested {total_sus_req} SUs, which requires a fifth server, but --server-su4 was not provided."
        )
        return

    server_ips = {"bsu": bsu_server_ip}
    clients = {"bsu": STLClient(server=bsu_server_ip)}
    if total_sus_req > 3:
        server_ips["su"] = su_server_ip
        clients["su"] = STLClient(server=su_server_ip)
    if total_sus_req > 7:
        server_ips["su2"] = su_server_2_ip
        clients["su2"] = STLClient(server=su_server_2_ip)
    if total_sus_req > 11:
        server_ips["su3"] = su_server_3_ip
        clients["su3"] = STLClient(server=su_server_3_ip)
    if total_sus_req > 15:
        server_ips["su4"] = su_server_4_ip
        clients["su4"] = STLClient(server=su_server_4_ip)

    macs: dict[str, dict[int, str]] = {}
    server_ports: dict[str, list[int]] = {}
    port_map: list[dict[str, object]] = []
    results: dict = {}
    start_time_lap = 0
    current_size = None
    current_direction = None
    current_proto = None
    live_stats_history: list[dict[str, object]] = []

    try:
        for name, client in clients.items():
            print(f"Connecting to {name.upper()} server @ {server_ips[name]}...")
            client.connect()
            discovered_ports = client.get_all_ports()
            if name == "bsu" and allowed_bsu_ports is not None:
                discovered_ports = [port for port in discovered_ports if port in allowed_bsu_ports]
                if not discovered_ports:
                    print(
                        f"\nError: None of the requested BSU ports {sorted(allowed_bsu_ports)} "
                        f"are available on {server_ips[name]}."
                    )
                    return
            server_ports[name] = discovered_ports
            client.acquire(ports=server_ports[name], force=True)
            port_info = client.get_port_info(server_ports[name])
            macs[name] = {port["index"]: port["hw_mac"] for port in port_info}

        bsu_ports = server_ports["bsu"]
        if 0 in bsu_ports:
            bsu_port_id = 0
        else:
            bsu_port_id = min(bsu_ports)

        all_available_su_ports: list[tuple[str, int]] = []
        for port in bsu_ports:
            if port != bsu_port_id:
                all_available_su_ports.append(("bsu", port))
        if "su" in server_ports:
            for port in server_ports["su"]:
                all_available_su_ports.append(("su", port))
        if "su2" in server_ports:
            for port in server_ports["su2"]:
                all_available_su_ports.append(("su2", port))
        if "su3" in server_ports:
            for port in server_ports["su3"]:
                all_available_su_ports.append(("su3", port))
        if "su4" in server_ports:
            for port in server_ports["su4"]:
                all_available_su_ports.append(("su4", port))

        if total_sus_req > len(all_available_su_ports):
            print(
                f"\nError: Requested {total_sus_req} SUs, but only {len(all_available_su_ports)} "
                "are physically available across all connected servers."
            )
            return

        su_ports_for_this_test = all_available_su_ports[:total_sus_req]
        su_counter = 1
        port_map.append(
            {
                "server": "bsu",
                "port_id": bsu_port_id,
                "role": "BSU",
                "label": "BSU",
                "mac": macs["bsu"][bsu_port_id],
            }
        )
        for server_name, port_id in su_ports_for_this_test:
            port_map.append(
                {
                    "server": server_name,
                    "port_id": port_id,
                    "role": "SU",
                    "label": f"SU{su_counter}",
                    "mac": macs[server_name][port_id],
                }
            )
            su_counter += 1

        protocols_to_run = ["udp", "tcp"] if proto_mode == "both" else [proto_mode]
        directions_to_run = (
            ["bidi", "uplink", "downlink"] if direction_mode == "all" else [direction_mode]
        )

        print("\n" + "-" * 80)
        print("TRex Multi-Server PTMP Test".center(80))
        print("-" * 80)
        param_table = PrettyTable(field_names=["Parameter", "Value"], header=False)
        param_table.align["Parameter"] = "l"
        param_table.add_row(["Protocols to be Tested", ", ".join(proto.upper() for proto in protocols_to_run)])
        param_table.add_row(["Packet Sizes to be Tested", ", ".join(map(str, sizes_to_test))])
        param_table.add_row(["Directions to be Tested", ", ".join(direction.capitalize() for direction in directions_to_run)])
        param_table.add_row(["VLAN", vlan_id if vlan_id is not None else "Untagged"])
        if allowed_bsu_ports is not None:
            param_table.add_row(["BSU Port Allowlist", ", ".join(str(port) for port in sorted(allowed_bsu_ports))])
        param_table.add_row(["-" * 40, "-" * 40])
        param_table.add_row(["Configured Downlink BW", f"{dl_bw_str if dl_bw_str else bw_str}"])
        param_table.add_row(["Configured Uplink BW", f"{ul_bw_str if ul_bw_str else bw_str}"])
        param_table.add_row(["-" * 40, "-" * 40])
        param_table.add_row(["Total SUs in Test", total_sus_req])
        param_table.add_row(
            [" - SUs on BSU Server", len([port for port in su_ports_for_this_test if port[0] == "bsu"])]
        )
        if "su" in clients:
            param_table.add_row(
                [" - SUs on Remote Server 1", len([port for port in su_ports_for_this_test if port[0] == "su"])]
            )
        if "su2" in clients:
            param_table.add_row(
                [" - SUs on Remote Server 2", len([port for port in su_ports_for_this_test if port[0] == "su2"])]
            )
        if "su3" in clients:
            param_table.add_row(
                [" - SUs on Remote Server 3", len([port for port in su_ports_for_this_test if port[0] == "su3"])]
            )
        if "su4" in clients:
            param_table.add_row(
                [" - SUs on Remote Server 4", len([port for port in su_ports_for_this_test if port[0] == "su4"])]
            )

        if duration > 0:
            num_tests = len(protocols_to_run) * len(sizes_to_test) * len(directions_to_run)
            total_time_s = num_tests * (duration + RAMP_UP_SECONDS)
            param_table.add_row(["-" * 40, "-" * 40])
            param_table.add_row(["Number of Unique Tests", num_tests])
            param_table.add_row(
                ["Estimated Total Duration", f"{total_time_s} seconds (~{total_time_s / 60:.1f} minutes)"]
            )
        print(param_table)
        print("-" * 80)

        all_su_macs = [entry["mac"] for entry in port_map if entry["role"] == "SU"]
        test_num = 1
        num_total_tests = len(protocols_to_run) * len(sizes_to_test) * len(directions_to_run)

        for proto in protocols_to_run:
            current_proto = proto
            if proto == "tcp":
                l4_layer = TCP(sport=8000, dport=8080, flags="PA")
            else:
                l4_layer = UDP(sport=1025, dport=1234)
            base_header_size = 42
            if vlan_id is not None:
                base_header_size += 4

            for size in sizes_to_test:
                current_size = size
                results.setdefault(size, {})
                is_imix_test_current_size = size == "IMIX"
                if is_imix_test_current_size:
                    for item in IMIX_PROFILE:
                        item["pad"] = "x" * max(0, item["size"] - base_header_size)

                for direction in directions_to_run:
                    current_direction = direction
                    results[size].setdefault(direction, {})

                    total_dl_bw_mbps = parse_bw_str_to_mbps(dl_bw_str if dl_bw_str else bw_str)
                    total_ul_bw_mbps = parse_bw_str_to_mbps(ul_bw_str if ul_bw_str else bw_str)
                    if direction_mode == "all":
                        if direction == "downlink":
                            total_dl_bw_mbps *= 2
                        elif direction == "uplink":
                            total_ul_bw_mbps *= 2

                    downlink_bw_per_su_mbps = total_dl_bw_mbps / total_sus_req if total_sus_req > 0 else 0
                    if subw_str:
                        uplink_bw_per_su_mbps = parse_bw_str_to_mbps(subw_str)
                    else:
                        uplink_bw_per_su_mbps = total_ul_bw_mbps / total_sus_req if total_sus_req > 0 else 0

                    live_stats_history = []
                    for client in clients.values():
                        client.reset(ports=client.get_all_ports())

                    active_ports = {name: set() for name in clients}
                    if direction in ["downlink", "bidi"]:
                        for su_mac in all_su_macs:
                            l2_header = Ether(src=macs["bsu"][bsu_port_id], dst=su_mac)
                            if vlan_id is not None:
                                l2_header = l2_header / Dot1Q(vlan=vlan_id)
                            if is_imix_test_current_size:
                                for item in IMIX_PROFILE:
                                    bw = downlink_bw_per_su_mbps * (item["percent"] / 100.0)
                                    pps = (bw * 1_000_000) / (item["size"] * 8) if item["size"] > 0 else 0
                                    packet = l2_header / IP() / l4_layer / item["pad"]
                                    clients["bsu"].add_streams(
                                        STLStream(packet=STLPktBuilder(pkt=packet), mode=STLTXCont(pps=pps)),
                                        ports=[bsu_port_id],
                                    )
                            else:
                                pps = (downlink_bw_per_su_mbps * 1_000_000) / (size * 8)
                                pad = "x" * max(0, size - base_header_size)
                                packet = l2_header / IP() / l4_layer / pad
                                clients["bsu"].add_streams(
                                    STLStream(packet=STLPktBuilder(pkt=packet), mode=STLTXCont(pps=pps)),
                                    ports=[bsu_port_id],
                                )
                        active_ports["bsu"].add(bsu_port_id)

                    if direction in ["uplink", "bidi"]:
                        for port in port_map:
                            if port["role"] != "SU":
                                continue
                            client = clients[port["server"]]
                            l2_header = Ether(src=port["mac"], dst=macs["bsu"][bsu_port_id])
                            if vlan_id is not None:
                                l2_header = l2_header / Dot1Q(vlan=vlan_id)
                            if is_imix_test_current_size:
                                for item in IMIX_PROFILE:
                                    bw = uplink_bw_per_su_mbps * (item["percent"] / 100.0)
                                    pps = (bw * 1_000_000) / (item["size"] * 8) if item["size"] > 0 else 0
                                    packet = l2_header / IP() / l4_layer / item["pad"]
                                    client.add_streams(
                                        STLStream(packet=STLPktBuilder(pkt=packet), mode=STLTXCont(pps=pps)),
                                        ports=[port["port_id"]],
                                    )
                            else:
                                pps = (uplink_bw_per_su_mbps * 1_000_000) / (size * 8)
                                pad = "x" * max(0, size - base_header_size)
                                packet = l2_header / IP() / l4_layer / pad
                                client.add_streams(
                                    STLStream(packet=STLPktBuilder(pkt=packet), mode=STLTXCont(pps=pps)),
                                    ports=[port["port_id"]],
                                )
                            active_ports[port["server"]].add(port["port_id"])

                    for client in clients.values():
                        client.clear_stats()
                    for server_name, port_list in active_ports.items():
                        if port_list:
                            clients[server_name].start(ports=list(port_list))

                    start_time_lap = time.time()
                    live_stats_interval = (
                        INDEFINITE_LIVE_STATS_INTERVAL if duration == 0 else DEFAULT_LIVE_STATS_INTERVAL
                    )
                    run_msg = (
                        f"[Test {test_num}/{num_total_tests}] Running {proto.upper()}/{direction.upper()} "
                        f"test for {duration}s at {size}..."
                    )
                    if duration == 0:
                        run_msg = (
                            f"Running {proto.upper()}/{direction.upper()} test indefinitely at {size}... "
                            "(Press Ctrl+C to stop)"
                        )
                    print(f"\n{run_msg}")

                    last_stats_time = start_time_lap
                    last_stats = {name: client.get_stats() for name, client in clients.items()}
                    total_lap_duration = duration + RAMP_UP_SECONDS
                    run_condition = (
                        (lambda: True)
                        if duration == 0
                        else (lambda: (time.time() - start_time_lap) < total_lap_duration)
                    )
                    zero_rx_strikes = 0

                    while run_condition():
                        sleep_for = live_stats_interval
                        if duration != 0:
                            remaining_time = total_lap_duration - (time.time() - start_time_lap)
                            sleep_for = min(live_stats_interval, remaining_time)
                        if sleep_for > 0:
                            time.sleep(sleep_for)

                        if not any(client.is_traffic_active() for client in clients.values()) and duration != 0:
                            break

                        current_stats = {name: client.get_stats() for name, client in clients.items()}
                        current_time = time.time()
                        time_delta = current_time - last_stats_time
                        elapsed_lap_time = current_time - start_time_lap

                        if elapsed_lap_time >= RAMP_UP_SECONDS:
                            collect_and_print_live_stats(
                                current_stats,
                                last_stats,
                                time_delta,
                                start_time_lap,
                                port_map,
                                live_stats_history,
                                enable_debug,
                            )

                        if time_delta > 0.1:
                            total_rx_mbps = 0.0
                            for port in port_map:
                                server = port["server"]
                                port_id = port["port_id"]
                                if server not in current_stats or port_id not in current_stats[server]:
                                    continue
                                stats_now = current_stats[server][port_id]
                                stats_before = last_stats[server][port_id]
                                delta_ibytes = stats_now["ibytes"] - stats_before["ibytes"]
                                rx_mbps = (delta_ibytes * 8) / (time_delta * 1_000_000)
                                is_bsu = port["label"] == "BSU"
                                if (direction == "downlink" and not is_bsu) or (
                                    direction == "uplink" and is_bsu
                                ) or (direction == "bidi"):
                                    total_rx_mbps += rx_mbps

                            if total_rx_mbps < 0.1 and (time.time() - start_time_lap) > RAMP_UP_SECONDS:
                                zero_rx_strikes += 1
                            else:
                                zero_rx_strikes = 0

                            if zero_rx_strikes >= ZERO_RX_THRESHOLD:
                                print(
                                    f"\nError: Zero RX throughput detected for "
                                    f"{zero_rx_strikes * live_stats_interval} seconds."
                                )
                                print("Stopping test. Check physical setup and switch/VLAN configuration.")
                                break
                        last_stats, last_stats_time = current_stats, current_time

                    for client in clients.values():
                        if client.is_traffic_active():
                            client.stop()

                    total_avg_rx = 0.0
                    if live_stats_history:
                        df_res = pd.DataFrame(live_stats_history)
                        df_res_map = {(p["server"], p["port_id"]): p["label"] for p in port_map}
                        df_res["label"] = df_res.apply(
                            lambda row: df_res_map.get((row["server"], row["port"])), axis=1
                        )
                        for label in df_res["label"].unique():
                            port_df = df_res[df_res["label"] == label]
                            is_bsu = label == "BSU"
                            if (direction == "downlink" and not is_bsu) or (
                                direction == "uplink" and is_bsu
                            ) or (direction == "bidi"):
                                if not port_df.empty:
                                    total_avg_rx += port_df["rx_mbps"].mean()

                    results[size][direction][proto] = {
                        "history": live_stats_history.copy(),
                        "total_avg_rx": total_avg_rx,
                    }
                    test_num += 1
    except KeyboardInterrupt:
        print("\nTest interrupted by user. Capturing final stats for the partial run...")
        if duration == 0 and start_time_lap > 0:
            elapsed_time = time.time() - start_time_lap
            minutes, seconds = divmod(elapsed_time, 60)
            print(f"Indefinite test ran for {int(minutes)} minutes and {seconds:.2f} seconds.")
        if (
            start_time_lap > 0
            and current_size is not None
            and current_direction is not None
            and current_proto is not None
        ):
            for client in clients.values():
                if client and client.is_connected() and client.is_traffic_active():
                    client.stop()
            results.setdefault(current_size, {}).setdefault(current_direction, {})
            results[current_size][current_direction][current_proto] = {"history": live_stats_history.copy()}
            print(f"Incomplete results for {current_size}/{current_direction}/{current_proto} captured.")
    except Exception as exc:
        import traceback

        print(f"\nAn error occurred: {exc}")
        traceback.print_exc()
    finally:
        for name, client in clients.items():
            if client and client.is_connected():
                try:
                    client.stop()
                    client.disconnect()
                    print(f"\nDisconnected from {name.upper()} server.")
                except Exception as disconnect_error:
                    print(f"Error disconnecting from {name.upper()} server: {disconnect_error}")

        if results:
            for size, direction_results in sorted(
                results.items(), key=lambda item: (isinstance(item[0], str), item[0])
            ):
                for direction, proto_results in sorted(direction_results.items()):
                    for proto, result_data in sorted(proto_results.items()):
                        print_summary_table(size, direction, proto, result_data, port_map)
            print_consolidated_summary(results, port_map)
            if enable_graph or (duration == 0 and live_stats_history):
                for size, direction_results in sorted(
                    results.items(), key=lambda item: (isinstance(item[0], str), item[0])
                ):
                    for direction, proto_results in sorted(direction_results.items()):
                        for proto, result_data in sorted(proto_results.items()):
                            history = result_data.get("history", [])
                            if history:
                                plot_throughput_over_time(history, direction, size, proto, port_map)
            else:
                print("\nGraph generation skipped. Use --graph to enable.")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Server TRex PTMP throughput test script.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--server-bsu", type=str, required=True, help="IP of the TRex server with the BSU.")
    parser.add_argument("--server-su", type=str, default=None, help="IP of remote TRex server (4-7 SUs).")
    parser.add_argument("--server-su2", type=str, default=None, help="IP of second remote TRex server (8-11 SUs).")
    parser.add_argument("--server-su3", type=str, default=None, help="IP of third remote TRex server (12-15 SUs).")
    parser.add_argument("--server-su4", type=str, default=None, help="IP of fourth remote TRex server (16-19 SUs).")
    parser.add_argument("--su", type=int, required=True, help="Total number of SUs to include in the test.")
    parser.add_argument("--bw", type=str, default=None, help="Default bandwidth for both UL and DL (e.g., 400M).")
    parser.add_argument("--dl-bw", type=str, default=None, help="Total downlink bandwidth (e.g., 500M).")
    parser.add_argument("--ul-bw", type=str, default=None, help="Total uplink bandwidth (e.g., 100M).")
    parser.add_argument("--subw", type=str, default=None, help="Manual per-SU uplink bandwidth (e.g., 50M).")
    parser.add_argument(
        "--size",
        nargs="+",
        type=size_type,
        required=True,
        help="One or more packet sizes (e.g., 64 128). Use 'imix' for IMIX.",
    )
    parser.add_argument("--duration", type=int, default=10, help="Test duration in seconds. Use 0 for indefinite.")
    parser.add_argument(
        "--dir",
        type=str,
        default="bidi",
        choices=["bidi", "uplink", "downlink", "all"],
        help="Traffic direction.",
    )
    parser.add_argument("--graph", action="store_true", help="Enable graph generation at the end of the test.")
    parser.add_argument(
        "--proto",
        type=str,
        default="udp",
        choices=["udp", "tcp", "both"],
        help="Protocol to use (udp, tcp, or both).",
    )
    parser.add_argument("--vlan", type=int, default=None, help="Optional VLAN ID for 802.1Q tagged traffic.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose live stats during the test.")
    parser.add_argument(
        "--ports",
        type=str,
        default=None,
        help="Comma-separated BSU port IDs (e.g. 0,1). Falls back to TREX_PORTS env var.",
    )
    args = parser.parse_args()

    run_multi_server_test(
        args.server_bsu,
        args.server_su,
        args.server_su2,
        args.server_su3,
        args.server_su4,
        args.su,
        args.bw,
        args.dl_bw,
        args.ul_bw,
        args.subw,
        args.size,
        args.duration,
        args.dir,
        args.graph,
        args.proto,
        args.vlan,
        args.debug,
        ports_spec=args.ports,
    )


if __name__ == "__main__":
    main()
