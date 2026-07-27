#!/usr/bin/env python3
"""
QoS multi-DSCP throughput generator for BSU + SUs (TRex STL).

Creates up to 8 concurrent streams with distinct DSCP markings so QoS
classification / prioritization can be validated on BTS & CPE.

Designed to run on the TRex control host with:
  export PYTHONPATH=/opt/v3.06/automation/trex_control_plane/interactive/

Does not modify master_script_extended_16SU.py. Deploy alongside qos_classes.py
and qinq_tags.py (see traffic/deploy_trex_script.py --local-script).

Example:
  python3 qos_throughput_script.py \\
    --server-bsu 127.0.0.1 --su 1 --bw 400M --size 1500 --duration 30 \\
    --dir bidi --proto udp --debug
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
import warnings

from prettytable import PrettyTable

from qos_classes import (
    ACTIVE_QOS_PROFILE,
    QOS_TRAFFIC_CLASSES,
    format_class_table,
    select_classes,
)
from qinq_tags import apply_downlink_tags, apply_uplink_tags, format_vlan_label, header_sizes

warnings.filterwarnings("ignore", category=DeprecationWarning)

DEFAULT_LIVE_STATS_INTERVAL = 2
INDEFINITE_LIVE_STATS_INTERVAL = 30
RAMP_UP_SECONDS = 5
ZERO_RX_THRESHOLD = 3
IMIX_PROFILE = [
    {"size": 64, "percent": 50},
    {"size": 256, "percent": 30},
    {"size": 512, "percent": 20},
]


def parse_bw_str_to_mbps(bw_str) -> float:
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
    result: set[int] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        try:
            result.add(int(text))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"Invalid port id '{text}'") from exc
    return result or None


def resolve_ports_spec(ports_spec) -> set[int] | None:
    if ports_spec is not None:
        return parse_port_allowlist(ports_spec)
    return parse_port_allowlist(os.environ.get("TREX_PORTS"))


def size_type(value: str):
    text = str(value).strip().lower()
    if text == "imix":
        return "IMIX"
    try:
        size = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a valid integer or the keyword 'imix'."
        ) from exc
    if size < 64:
        raise argparse.ArgumentTypeError(f"Packet size must be >= 64, got {size}")
    return size


def _l4_layer(proto: str, sport: int, dport: int):
    if proto == "tcp":
        return TCP(sport=sport, dport=dport, flags="PA")
    return UDP(sport=sport, dport=dport)


def _build_ip_layer(tos: int, src_ip: str | None = None, dst_ip: str | None = None):
    kwargs: dict = {"tos": tos}
    if src_ip:
        kwargs["src"] = src_ip
    if dst_ip:
        kwargs["dst"] = dst_ip
    return IP(**kwargs)


def _pps_for_bw(bw_mbps: float, frame_size: int) -> float:
    if frame_size <= 0 or bw_mbps <= 0:
        return 0.0
    return (bw_mbps * 1_000_000) / (frame_size * 8)


def collect_port_live_stats(
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
    rows = []
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
        rows.append(
            [port["label"], f"{tx_mbps:.2f}", f"{rx_mbps:.2f}", f"{tx_pps:,.0f}", f"{rx_pps:,.0f}"]
        )
        history_log.append(
            {
                "elapsed_s": time.time() - start_time,
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
        table.align["Device"] = "l"
        for row in rows:
            table.add_row(row)
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n--- Live Stats @ {timestamp} ---")
        print(table)


def collect_flow_live_stats(clients, qos_classes, flow_history, enable_debug):
    """Sample per-DSCP flow TX/RX using STLFlowStats pg_ids."""
    sample = {"elapsed_s": time.time(), "classes": {}}
    rows = []
    for cls in qos_classes:
        name = cls["name"]
        tx_bps = 0.0
        rx_bps = 0.0
        for client in clients.values():
            stats = client.get_stats()
            flow_stats = stats.get("flow_stats") or {}
            for pg_id in (cls["pg_id_dl"], cls["pg_id_ul"]):
                entry = flow_stats.get(pg_id) or flow_stats.get(str(pg_id))
                if not entry:
                    continue
                tx_bps += float(entry.get("tx_bps", 0.0) or 0.0)
                rx_bps += float(entry.get("rx_bps", 0.0) or 0.0)
        tx_mbps = tx_bps / 1_000_000.0
        rx_mbps = rx_bps / 1_000_000.0
        sample["classes"][name] = {
            "label": cls["label"],
            "dscp": cls["dscp"],
            "tx_mbps": tx_mbps,
            "rx_mbps": rx_mbps,
        }
        rows.append(
            [
                cls["label"],
                cls["dscp"],
                cls["phb"],
                f"{tx_mbps:.2f}",
                f"{rx_mbps:.2f}",
            ]
        )
    flow_history.append(sample)

    if enable_debug and rows:
        table = PrettyTable()
        table.field_names = ["Class", "DSCP", "PHB", "TX Mbps", "RX Mbps"]
        table.align = "r"
        table.align["Class"] = "l"
        table.align["PHB"] = "l"
        for row in rows:
            table.add_row(row)
        print("\n--- Per-DSCP Flow Stats ---")
        print(table)


def print_port_summary(size, direction, proto, history, port_map):
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

    by_label: dict[str, list[dict]] = {}
    mapping = {(p["server"], p["port_id"]): p["label"] for p in port_map}
    for sample in history[1:] if len(history) >= 2 else history:
        label = mapping.get((sample["server"], sample["port"]))
        if not label:
            continue
        by_label.setdefault(label, []).append(sample)

    total_tx = 0.0
    total_rx = 0.0
    for label in sorted(by_label):
        samples = by_label[label]
        avg_tx = sum(s["tx_mbps"] for s in samples) / len(samples)
        min_tx = min(s["tx_mbps"] for s in samples)
        max_tx = max(s["tx_mbps"] for s in samples)
        avg_rx = sum(s["rx_mbps"] for s in samples) / len(samples)
        min_rx = min(s["rx_mbps"] for s in samples)
        max_rx = max(s["rx_mbps"] for s in samples)
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
            total_rx += avg_rx
        if (direction == "downlink" and is_bsu) or (direction == "uplink" and not is_bsu) or (
            direction == "bidi"
        ):
            total_tx += avg_tx

    table.add_row(["-" * 8, "-" * 15, "-" * 15, "-" * 15, "-" * 15, "-" * 15, "-" * 15])
    table.add_row(
        ["TOTAL", f"{total_tx:.2f}", "N/A", "N/A", f"{total_rx:.2f}", "N/A", "N/A"]
    )
    title = f" Port Summary {size} / {direction.upper()} / {proto.upper()} "
    print("\n\n" + title.center(80, "="))
    print(table)


def print_qos_flow_summary(size, direction, proto, flow_history, qos_classes):
    if not flow_history:
        return
    # Skip first sample (ramp / zero)
    samples = flow_history[1:] if len(flow_history) >= 2 else flow_history
    table = PrettyTable()
    table.field_names = [
        "Class",
        "DSCP",
        "PHB",
        "Share%",
        "Avg TX (Mbps)",
        "Avg RX (Mbps)",
        "Min RX",
        "Max RX",
    ]
    table.align = "r"
    table.align["Class"] = "l"
    table.align["PHB"] = "l"

    for cls in qos_classes:
        name = cls["name"]
        tx_vals = []
        rx_vals = []
        for sample in samples:
            entry = sample["classes"].get(name)
            if not entry:
                continue
            tx_vals.append(entry["tx_mbps"])
            rx_vals.append(entry["rx_mbps"])
        if not rx_vals:
            avg_tx = avg_rx = min_rx = max_rx = 0.0
        else:
            avg_tx = sum(tx_vals) / len(tx_vals)
            avg_rx = sum(rx_vals) / len(rx_vals)
            min_rx = min(rx_vals)
            max_rx = max(rx_vals)
        table.add_row(
            [
                cls["label"],
                cls["dscp"],
                cls["phb"],
                f"{float(cls['bw_share']) * 100:.1f}",
                f"{avg_tx:.2f}",
                f"{avg_rx:.2f}",
                f"{min_rx:.2f}",
                f"{max_rx:.2f}",
            ]
        )

    title = f" QoS DSCP Summary {size} / {direction.upper()} / {proto.upper()} "
    print("\n\n" + title.center(80, "="))
    print(table)


def add_qos_streams(
    *,
    client,
    ports: list[int],
    l2_header,
    qos_classes: list[dict],
    proto: str,
    size,
    header_size: int,
    total_bw_mbps: float,
    pg_id_key: str,
    is_imix: bool,
    enable_flow_stats: bool = False,
):
    """Add one STL stream per QoS class (fixed size or IMIX)."""
    for cls in qos_classes:
        class_bw = total_bw_mbps * float(cls["bw_share"])
        tos = int(cls["tos"])
        sport = int(cls["sport"])
        dport = int(cls["dport"])
        pg_id = int(cls[pg_id_key])

        def _packet_for(frame_size: int):
            pad = "x" * max(0, frame_size - header_size)
            return (
                l2_header
                / _build_ip_layer(tos)
                / _l4_layer(proto, sport, dport)
                / pad
            )

        def _stream(name: str, frame_size: int, pps: float):
            kwargs = {
                "name": name,
                "packet": STLPktBuilder(pkt=_packet_for(frame_size)),
                "mode": STLTXCont(pps=pps),
            }
            if enable_flow_stats:
                kwargs["flow_stats"] = STLFlowStats(pg_id=pg_id)
            return STLStream(**kwargs)

        if is_imix:
            for item in IMIX_PROFILE:
                bw = class_bw * (item["percent"] / 100.0)
                frame_size = int(item["size"])
                pps = _pps_for_bw(bw, frame_size)
                client.add_streams(
                    _stream(f"{cls['name']}_{pg_id_key}_{frame_size}", frame_size, pps),
                    ports=ports,
                )
        else:
            frame_size = int(size)
            pps = _pps_for_bw(class_bw, frame_size)
            client.add_streams(
                _stream(f"{cls['name']}_{pg_id_key}", frame_size, pps),
                ports=ports,
            )


def run_qos_throughput_test(
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
    proto_mode,
    vlan_id,
    svlan_id,
    cvlan_id,
    enable_debug,
    qos_classes: list[dict],
    ports_spec=None,
    enable_flow_stats: bool = False,
):
    # Imported here so --list-classes works without TRex installed locally.
    global Ether, IP, UDP, TCP, Dot1Q
    global STLClient, STLStream, STLPktBuilder, STLTXCont, STLFlowStats
    from trex_stl_lib.api import (  # noqa: WPS433
        Dot1Q,
        Ether,
        IP,
        STLClient,
        STLFlowStats,
        STLPktBuilder,
        STLStream,
        STLTXCont,
        TCP,
        UDP,
    )

    allowed_bsu_ports = resolve_ports_spec(ports_spec)
    is_imix_test_run = "IMIX" in sizes_to_test
    if is_imix_test_run and len(sizes_to_test) > 1:
        print("\nError: For IMIX tests, please specify only 'imix' as the size.")
        return

    if duration == 0:
        if len(sizes_to_test) > 1:
            print(
                f"\nInfo: Multiple sizes for indefinite run; using first size: {sizes_to_test[0]}"
            )
            sizes_to_test = [sizes_to_test[0]]
        if direction_mode == "all":
            print("\nInfo: --dir all with --duration 0 → defaulting to bidi.")
            direction_mode = "bidi"
        if proto_mode == "both":
            print("\nInfo: --proto both with --duration 0 → defaulting to udp.")
            proto_mode = "udp"

    if not bw_str and not dl_bw_str:
        print("\nError: No downlink bandwidth specified. Provide --bw or --dl-bw.")
        return
    if not bw_str and not ul_bw_str:
        print("\nError: No uplink bandwidth specified. Provide --bw or --ul-bw.")
        return

    if total_sus_req > 3 and not su_server_ip:
        print(f"\nError: {total_sus_req} SUs require --server-su.")
        return
    if total_sus_req > 7 and not su_server_2_ip:
        print(f"\nError: {total_sus_req} SUs require --server-su2.")
        return
    if total_sus_req > 11 and not su_server_3_ip:
        print(f"\nError: {total_sus_req} SUs require --server-su3.")
        return
    if total_sus_req > 15 and not su_server_4_ip:
        print(f"\nError: {total_sus_req} SUs require --server-su4.")
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
    port_map: list[dict] = []
    results: dict = {}
    start_time_lap = 0.0
    live_stats_history: list[dict] = []
    flow_history: list[dict] = []
    current_size = None
    current_direction = None
    current_proto = None

    try:
        for name, client in clients.items():
            print(f"Connecting to {name.upper()} server @ {server_ips[name]}...")
            client.connect()
            discovered_ports = client.get_all_ports()
            if name == "bsu" and allowed_bsu_ports is not None:
                discovered_ports = [p for p in discovered_ports if p in allowed_bsu_ports]
                if not discovered_ports:
                    print(
                        f"\nError: None of requested BSU ports {sorted(allowed_bsu_ports)} "
                        f"available on {server_ips[name]}."
                    )
                    return
            server_ports[name] = discovered_ports
            client.acquire(ports=server_ports[name], force=True)
            port_info = client.get_port_info(server_ports[name])
            macs[name] = {port["index"]: port["hw_mac"] for port in port_info}

        bsu_ports = server_ports["bsu"]
        bsu_port_id = 0 if 0 in bsu_ports else min(bsu_ports)

        all_available_su_ports: list[tuple[str, int]] = []
        for port in bsu_ports:
            if port != bsu_port_id:
                all_available_su_ports.append(("bsu", port))
        for remote in ("su", "su2", "su3", "su4"):
            if remote in server_ports:
                for port in server_ports[remote]:
                    all_available_su_ports.append((remote, port))

        if total_sus_req > len(all_available_su_ports):
            print(
                f"\nError: Requested {total_sus_req} SUs, only "
                f"{len(all_available_su_ports)} ports available."
            )
            return

        su_ports_for_this_test = all_available_su_ports[:total_sus_req]
        port_map.append(
            {
                "server": "bsu",
                "port_id": bsu_port_id,
                "role": "BSU",
                "label": "BSU",
                "mac": macs["bsu"][bsu_port_id],
            }
        )
        for index, (server_name, port_id) in enumerate(su_ports_for_this_test, start=1):
            port_map.append(
                {
                    "server": server_name,
                    "port_id": port_id,
                    "role": "SU",
                    "label": f"SU{index}",
                    "mac": macs[server_name][port_id],
                }
            )

        protocols_to_run = ["udp", "tcp"] if proto_mode == "both" else [proto_mode]
        directions_to_run = (
            ["bidi", "uplink", "downlink"] if direction_mode == "all" else [direction_mode]
        )

        print("\n" + "-" * 80)
        print("TRex QoS Multi-DSCP Throughput Test".center(80))
        print("-" * 80)
        print(format_class_table(qos_classes))
        print("-" * 80)

        param_table = PrettyTable(field_names=["Parameter", "Value"], header=False)
        param_table.align = "l"
        param_table.add_row(
            ["QoS Profile", f"{ACTIVE_QOS_PROFILE} (config/qos/ath1qos.conf)"]
        )
        param_table.add_row(["QoS Classes", ", ".join(c["label"] for c in qos_classes)])
        param_table.add_row(
            ["DSCP Values", ", ".join(str(c["dscp"]) for c in qos_classes)]
        )
        param_table.add_row(
            [
                "PIR Names",
                ", ".join(str(c.get("pir_name", "?")) for c in qos_classes),
            ]
        )
        param_table.add_row(["Stream Count", str(len(qos_classes))])
        param_table.add_row(
            ["Protocols", ", ".join(p.upper() for p in protocols_to_run)]
        )
        param_table.add_row(["Packet Sizes", ", ".join(map(str, sizes_to_test))])
        param_table.add_row(
            ["Directions", ", ".join(d.capitalize() for d in directions_to_run)]
        )
        param_table.add_row(["VLAN", format_vlan_label(vlan_id, svlan_id, cvlan_id)])
        param_table.add_row(["Downlink BW", f"{dl_bw_str if dl_bw_str else bw_str}"])
        param_table.add_row(["Uplink BW", f"{ul_bw_str if ul_bw_str else bw_str}"])
        param_table.add_row(["Total SUs", total_sus_req])
        if duration > 0:
            num_tests = len(protocols_to_run) * len(sizes_to_test) * len(directions_to_run)
            total_time_s = num_tests * (duration + RAMP_UP_SECONDS)
            param_table.add_row(["Unique Tests", num_tests])
            param_table.add_row(
                [
                    "Estimated Duration",
                    f"{total_time_s}s (~{total_time_s / 60:.1f} min)",
                ]
            )
        print(param_table)
        print("-" * 80)

        all_su_macs = [entry["mac"] for entry in port_map if entry["role"] == "SU"]
        test_num = 1
        num_total_tests = len(protocols_to_run) * len(sizes_to_test) * len(directions_to_run)

        for proto in protocols_to_run:
            current_proto = proto
            dl_header_size, ul_header_size = header_sizes(vlan_id, svlan_id, cvlan_id)

            for size in sizes_to_test:
                current_size = size
                results.setdefault(size, {})
                is_imix = size == "IMIX"

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

                    downlink_bw_per_su = (
                        total_dl_bw_mbps / total_sus_req if total_sus_req > 0 else 0.0
                    )
                    if subw_str:
                        uplink_bw_per_su = parse_bw_str_to_mbps(subw_str)
                    else:
                        uplink_bw_per_su = (
                            total_ul_bw_mbps / total_sus_req if total_sus_req > 0 else 0.0
                        )

                    live_stats_history = []
                    flow_history = []
                    for client in clients.values():
                        client.reset(ports=client.get_all_ports())

                    active_ports = {name: set() for name in clients}

                    if direction in ("downlink", "bidi"):
                        for su_mac in all_su_macs:
                            l2_header = apply_downlink_tags(
                                Ether(src=macs["bsu"][bsu_port_id], dst=su_mac),
                                vlan_id,
                                svlan_id,
                                cvlan_id,
                                dot1q_cls=Dot1Q,
                            )
                            add_qos_streams(
                                client=clients["bsu"],
                                ports=[bsu_port_id],
                                l2_header=l2_header,
                                qos_classes=qos_classes,
                                proto=proto,
                                size=size,
                                header_size=dl_header_size,
                                total_bw_mbps=downlink_bw_per_su,
                                pg_id_key="pg_id_dl",
                                is_imix=is_imix,
                                enable_flow_stats=enable_flow_stats,
                            )
                        active_ports["bsu"].add(bsu_port_id)

                    if direction in ("uplink", "bidi"):
                        for port in port_map:
                            if port["role"] != "SU":
                                continue
                            client = clients[port["server"]]
                            l2_header = apply_uplink_tags(
                                Ether(src=port["mac"], dst=macs["bsu"][bsu_port_id]),
                                vlan_id,
                                svlan_id,
                                cvlan_id,
                                dot1q_cls=Dot1Q,
                            )
                            add_qos_streams(
                                client=client,
                                ports=[port["port_id"]],
                                l2_header=l2_header,
                                qos_classes=qos_classes,
                                proto=proto,
                                size=size,
                                header_size=ul_header_size,
                                total_bw_mbps=uplink_bw_per_su,
                                pg_id_key="pg_id_ul",
                                is_imix=is_imix,
                                enable_flow_stats=enable_flow_stats,
                            )
                            active_ports[port["server"]].add(port["port_id"])

                    for client in clients.values():
                        client.clear_stats()
                    for server_name, port_list in active_ports.items():
                        if port_list:
                            clients[server_name].start(ports=list(port_list))

                    start_time_lap = time.time()
                    live_stats_interval = (
                        INDEFINITE_LIVE_STATS_INTERVAL
                        if duration == 0
                        else DEFAULT_LIVE_STATS_INTERVAL
                    )
                    stream_count = len(qos_classes)
                    run_msg = (
                        f"[Test {test_num}/{num_total_tests}] QoS {stream_count} DSCP streams "
                        f"{proto.upper()}/{direction.upper()} size={size} for {duration}s..."
                    )
                    if duration == 0:
                        run_msg = (
                            f"QoS {stream_count} DSCP streams {proto.upper()}/{direction.upper()} "
                            f"indefinite at {size}... (Ctrl+C to stop)"
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
                            remaining = total_lap_duration - (time.time() - start_time_lap)
                            sleep_for = min(live_stats_interval, remaining)
                        if sleep_for > 0:
                            time.sleep(sleep_for)

                        if (
                            not any(c.is_traffic_active() for c in clients.values())
                            and duration != 0
                        ):
                            break

                        current_stats = {
                            name: client.get_stats() for name, client in clients.items()
                        }
                        current_time = time.time()
                        time_delta = current_time - last_stats_time
                        elapsed_lap = current_time - start_time_lap

                        if elapsed_lap >= RAMP_UP_SECONDS:
                            collect_port_live_stats(
                                current_stats,
                                last_stats,
                                time_delta,
                                start_time_lap,
                                port_map,
                                live_stats_history,
                                enable_debug,
                            )
                            if enable_flow_stats:
                                collect_flow_live_stats(
                                    clients, qos_classes, flow_history, enable_debug
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

                            if total_rx_mbps < 0.1 and elapsed_lap > RAMP_UP_SECONDS:
                                zero_rx_strikes += 1
                            else:
                                zero_rx_strikes = 0
                            if zero_rx_strikes >= ZERO_RX_THRESHOLD:
                                print(
                                    f"\nError: Zero RX for "
                                    f"{zero_rx_strikes * live_stats_interval}s. "
                                    "Check cabling / VLAN / link."
                                )
                                break

                        last_stats, last_stats_time = current_stats, current_time

                    for client in clients.values():
                        if client.is_traffic_active():
                            client.stop()

                    results[size][direction][proto] = {
                        "history": live_stats_history.copy(),
                        "flow_history": flow_history.copy(),
                    }
                    test_num += 1

    except KeyboardInterrupt:
        print("\nInterrupted — capturing partial results...")
        if duration == 0 and start_time_lap > 0:
            elapsed = time.time() - start_time_lap
            minutes, seconds = divmod(elapsed, 60)
            print(f"Indefinite test ran for {int(minutes)}m {seconds:.1f}s.")
        for client in clients.values():
            if client and client.is_connected() and client.is_traffic_active():
                client.stop()
        if current_size is not None and current_direction is not None and current_proto is not None:
            results.setdefault(current_size, {}).setdefault(current_direction, {})
            results[current_size][current_direction][current_proto] = {
                "history": live_stats_history.copy(),
                "flow_history": flow_history.copy(),
            }
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
                    print(f"Error disconnecting {name.upper()}: {disconnect_error}")

        if results:
            for size, direction_results in sorted(
                results.items(), key=lambda item: (isinstance(item[0], str), item[0])
            ):
                for direction, proto_results in sorted(direction_results.items()):
                    for proto, result_data in sorted(proto_results.items()):
                        print_port_summary(
                            size, direction, proto, result_data.get("history", []), port_map
                        )
                        print_qos_flow_summary(
                            size,
                            direction,
                            proto,
                            result_data.get("flow_history", []),
                            qos_classes,
                        )


def main():
    if "--list-classes" in sys.argv:
        print(format_class_table(select_classes(equal_share=False)))
        return

    parser = argparse.ArgumentParser(
        description="TRex QoS throughput: 8 DSCP-tagged streams for BTS/CPE prioritization tests.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--server-bsu", type=str, required=True, help="TRex server IP with BSU port.")
    parser.add_argument("--server-su", type=str, default=None, help="Remote TRex for SUs 4–7.")
    parser.add_argument("--server-su2", type=str, default=None, help="Remote TRex for SUs 8–11.")
    parser.add_argument("--server-su3", type=str, default=None, help="Remote TRex for SUs 12–15.")
    parser.add_argument("--server-su4", type=str, default=None, help="Remote TRex for SUs 16–19.")
    parser.add_argument("--su", type=int, required=True, help="Number of SUs in the test.")
    parser.add_argument("--bw", type=str, default=None, help="Default UL+DL bandwidth (e.g. 400M).")
    parser.add_argument("--dl-bw", type=str, default=None, help="Total downlink bandwidth.")
    parser.add_argument("--ul-bw", type=str, default=None, help="Total uplink bandwidth.")
    parser.add_argument("--subw", type=str, default=None, help="Per-SU uplink bandwidth override.")
    parser.add_argument(
        "--size",
        nargs="+",
        type=size_type,
        required=True,
        help="Packet size(s), or 'imix'.",
    )
    parser.add_argument("--duration", type=int, default=10, help="Seconds (0 = indefinite).")
    parser.add_argument(
        "--dir",
        type=str,
        default="bidi",
        choices=["bidi", "uplink", "downlink", "all"],
        help="Traffic direction.",
    )
    parser.add_argument(
        "--proto",
        type=str,
        default="udp",
        choices=["udp", "tcp", "both"],
        help="L4 protocol.",
    )
    parser.add_argument("--vlan", type=int, default=None, help="Single 802.1Q VLAN.")
    parser.add_argument("--svlan", type=int, default=None, help="QinQ S-VLAN (with --cvlan).")
    parser.add_argument("--cvlan", type=int, default=None, help="QinQ C-VLAN (with --svlan).")
    parser.add_argument("--debug", action="store_true", help="Print live port + per-DSCP stats.")
    parser.add_argument(
        "--flow-stats",
        action="store_true",
        help=(
            "Enable STLFlowStats per DSCP (requires NIC RX filter / software mode support). "
            "Default off for broad NIC compatibility; port totals still reported."
        ),
    )
    parser.add_argument(
        "--ports",
        type=str,
        default=None,
        help="Comma-separated BSU port IDs (else TREX_PORTS).",
    )
    parser.add_argument(
        "--classes",
        nargs="+",
        default=None,
        help=(
            "Subset of class names/labels to run "
            f"(default: all {len(QOS_TRAFFIC_CLASSES)}). "
            "Examples: voice video gaming background"
        ),
    )
    parser.add_argument(
        "--tags",
        nargs="+",
        default=None,
        help="Select classes by case_tags (voice, video, gaming, backup, p2p, ...).",
    )
    parser.add_argument(
        "--equal-share",
        action="store_true",
        help="Split bandwidth equally across selected classes (ignore default shares).",
    )
    parser.add_argument(
        "--list-classes",
        action="store_true",
        help="Print the default 8 DSCP classes and exit.",
    )
    args = parser.parse_args()

    if (args.svlan is None) ^ (args.cvlan is None):
        parser.error("QinQ requires both --svlan and --cvlan.")
    if args.vlan is not None and args.svlan is not None:
        parser.error("Use either --vlan or --svlan/--cvlan, not both.")
    if args.classes and args.tags:
        parser.error("Use either --classes or --tags, not both.")

    try:
        qos_classes = select_classes(
            args.classes,
            tags=args.tags,
            equal_share=args.equal_share,
        )
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))

    run_qos_throughput_test(
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
        args.proto,
        args.vlan,
        args.svlan,
        args.cvlan,
        args.debug,
        qos_classes,
        ports_spec=args.ports,
        enable_flow_stats=args.flow_stats,
    )


if __name__ == "__main__":
    main()
