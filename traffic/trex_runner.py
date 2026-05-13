from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone

from utils.net_utils import format_ssh_host

SSH_OPTIONS = [
    "-o",
    "LogLevel=ERROR",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "ConnectTimeout=12",
]

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
LIVE_HEADER_RE = re.compile(r"^--- Live Stats @ (?P<timestamp>[^-]+?) ---$")
LIVE_ROW_RE = re.compile(
    r"^\|\s*(?P<device>[A-Za-z0-9_]+)\s*\|"
    r"\s*(?P<tx_mbps>[\d,.]+)\s*\|"
    r"\s*(?P<rx_mbps>[\d,.]+)\s*\|"
    r"\s*(?P<tx_pps>[\d,.]+)\s*\|"
    r"\s*(?P<rx_pps>[\d,.]+)\s*\|$"
)
SUMMARY_ROW_RE = re.compile(
    r"^\|\s*(?P<device>[A-Za-z0-9_]+)\s*\|"
    r"\s*(?P<avg_rx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<min_rx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<max_rx>[\d,.]+|N/A)\s*\|$"
)
CONSOLIDATED_ROW_RE = re.compile(r"^\|\s*(?P<pkt_size>\d+)\s*\|\s*(?P<bidi_mbps>[\d,.]+)\s*\|$")


class _OutputCollector:
    def __init__(self, process: subprocess.Popen[str]):
        self._process = process
        self._lines: list[str] = []
        self._thread = threading.Thread(target=self._consume, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def text(self) -> str:
        return "".join(self._lines)

    def tail(self, line_count: int = 80) -> str:
        return "".join(self._lines[-line_count:])

    def _consume(self) -> None:
        if not self._process.stdout:
            return
        for line in self._process.stdout:
            self._lines.append(line)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _to_float(value: str) -> float:
    cleaned = (value or "").strip().replace(",", "")
    if not cleaned or cleaned.upper() == "N/A":
        return 0.0
    return float(cleaned)


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _build_ssh_command(host: str, user: str, password: str, remote_script: str) -> list[str]:
    ssh_host = format_ssh_host(host)
    command: list[str] = []
    if password:
        command.extend(["sshpass", "-p", password])
    command.extend(["ssh", *SSH_OPTIONS, f"{user}@{ssh_host}", f"bash -lc {shlex.quote(remote_script)}"])
    return command


def _run_remote_command(
    host: str,
    user: str,
    password: str,
    remote_script: str,
    *,
    timeout_s: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        _build_ssh_command(host, user, password, remote_script),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Remote command failed on {host} with exit code {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result


def _start_trex_server(
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
    trex_dir: str,
    server_cores: int,
) -> tuple[subprocess.Popen[str], _OutputCollector]:
    remote_script = "\n".join(
        [
            "set -euo pipefail",
            "echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages",
            f"cd {shlex.quote(trex_dir)}",
            f"./t-rex-64 -i --no-scapy-server -c {int(server_cores)} --no-ofed-check",
        ]
    )
    process = subprocess.Popen(
        _build_ssh_command(trex_server, trex_user, trex_password, remote_script),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    collector = _OutputCollector(process)
    collector.start()
    return process, collector


def _stop_trex_server(
    process: subprocess.Popen[str],
    collector: _OutputCollector,
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
) -> str:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        collector.join(timeout=2)
        try:
            _run_remote_command(
                trex_server,
                trex_user,
                trex_password,
                "pkill -f 't-rex-64 -i --no-scapy-server' || true",
                timeout_s=15,
                check=False,
            )
        except Exception:
            pass
    return collector.tail()


def _normalize_client_script(script_path: str) -> tuple[str, str]:
    clean = script_path.strip() or "master_script_extended_16SU.py"
    if "/" in clean:
        return os.path.dirname(clean) or "~", os.path.basename(clean)
    return "~", clean


def _sample_dut_counters(
    *,
    dut_host: str | None,
    dut_user: str,
    dut_password: str,
    dut_radio_idx: int,
) -> dict[str, object] | None:
    if not dut_host:
        return None

    commands = {
        "tx_tput_mbps": f"cat /sys/class/kwn/wifi{dut_radio_idx}/statistics/tx_tput",
        "rx_tput_mbps": f"cat /sys/class/kwn/wifi{dut_radio_idx}/statistics/rx_tput",
        "avg_rtx_pct": f"cat /sys/class/kwn/wifi{dut_radio_idx}/statistics/avg_rtx",
        "link_count": f"cat /sys/class/kwn/wifi{dut_radio_idx}/statistics/links",
    }
    snapshot: dict[str, object] = {"captured_at": _utc_now()}
    for key, command in commands.items():
        result = _run_remote_command(
            dut_host,
            dut_user,
            dut_password,
            command,
            timeout_s=15,
            check=False,
        )
        raw_value = (result.stdout or result.stderr or "").strip().splitlines()
        snapshot[key] = raw_value[-1].strip() if raw_value else ""
    return snapshot


def parse_trex_client_output(raw_output: str) -> dict[str, object]:
    lines = [_strip_ansi(line).rstrip() for line in raw_output.splitlines()]

    live_samples: list[dict[str, object]] = []
    summary_devices: dict[str, dict[str, float]] = {}
    consolidated: list[dict[str, float]] = []
    current_sample: dict[str, object] | None = None
    in_summary = False
    in_consolidated = False

    def finalize_sample() -> None:
        nonlocal current_sample
        if not current_sample or not current_sample["devices"]:
            return
        devices = current_sample["devices"]
        bsu_devices = [row for name, row in devices.items() if str(name).upper().startswith("BSU")]
        su_devices = [row for name, row in devices.items() if not str(name).upper().startswith("BSU")]
        current_sample["downlink"] = {
            "tx_mbps": sum(row["tx_mbps"] for row in bsu_devices),
            "rx_mbps": sum(row["rx_mbps"] for row in su_devices),
            "tx_pps": sum(row["tx_pps"] for row in bsu_devices),
            "rx_pps": sum(row["rx_pps"] for row in su_devices),
        }
        current_sample["uplink"] = {
            "tx_mbps": sum(row["tx_mbps"] for row in su_devices),
            "rx_mbps": sum(row["rx_mbps"] for row in bsu_devices),
            "tx_pps": sum(row["tx_pps"] for row in su_devices),
            "rx_pps": sum(row["rx_pps"] for row in bsu_devices),
        }
        current_sample["combined"] = {
            "tx_mbps": current_sample["downlink"]["tx_mbps"] + current_sample["uplink"]["tx_mbps"],
            "rx_mbps": current_sample["downlink"]["rx_mbps"] + current_sample["uplink"]["rx_mbps"],
            "tx_pps": current_sample["downlink"]["tx_pps"] + current_sample["uplink"]["tx_pps"],
            "rx_pps": current_sample["downlink"]["rx_pps"] + current_sample["uplink"]["rx_pps"],
        }
        live_samples.append(current_sample)
        current_sample = None

    for line in lines:
        header_match = LIVE_HEADER_RE.match(line)
        if header_match:
            finalize_sample()
            current_sample = {"timestamp": header_match.group("timestamp").strip(), "devices": {}}
            in_summary = False
            in_consolidated = False
            continue

        live_match = LIVE_ROW_RE.match(line)
        if current_sample and live_match:
            current_sample["devices"][live_match.group("device")] = {
                "tx_mbps": _to_float(live_match.group("tx_mbps")),
                "rx_mbps": _to_float(live_match.group("rx_mbps")),
                "tx_pps": _to_float(live_match.group("tx_pps")),
                "rx_pps": _to_float(live_match.group("rx_pps")),
            }
            continue

        if "Summary for" in line and "Avg RX" in raw_output:
            finalize_sample()
            in_summary = True
            in_consolidated = False
            continue
        if "Consolidated Summary" in line:
            in_consolidated = True
            in_summary = False
            continue

        if in_summary:
            summary_match = SUMMARY_ROW_RE.match(line)
            if summary_match:
                summary_devices[summary_match.group("device")] = {
                    "avg_rx_mbps": _to_float(summary_match.group("avg_rx")),
                    "min_rx_mbps": _to_float(summary_match.group("min_rx")),
                    "max_rx_mbps": _to_float(summary_match.group("max_rx")),
                }
            continue

        if in_consolidated:
            consolidated_match = CONSOLIDATED_ROW_RE.match(line)
            if consolidated_match:
                consolidated.append(
                    {
                        "pkt_size": float(consolidated_match.group("pkt_size")),
                        "bidi_mbps": _to_float(consolidated_match.group("bidi_mbps")),
                    }
                )

    finalize_sample()

    live_combined_rx = [sample["combined"]["rx_mbps"] for sample in live_samples]
    live_combined_tx = [sample["combined"]["tx_mbps"] for sample in live_samples]
    live_downlink_rx = [sample["downlink"]["rx_mbps"] for sample in live_samples]
    live_downlink_tx = [sample["downlink"]["tx_mbps"] for sample in live_samples]
    live_uplink_rx = [sample["uplink"]["rx_mbps"] for sample in live_samples]
    live_uplink_tx = [sample["uplink"]["tx_mbps"] for sample in live_samples]

    bsu_summary = [
        row for name, row in summary_devices.items() if name.upper().startswith("BSU") and name.upper() != "TOTAL"
    ]
    su_summary = [
        row for name, row in summary_devices.items() if not name.upper().startswith("BSU") and name.upper() != "TOTAL"
    ]
    total_summary = summary_devices.get("TOTAL", {})

    downlink = {
        "tx_mbps": sum(row["avg_rx_mbps"] for row in bsu_summary) if bsu_summary else _avg(live_downlink_tx),
        "rx_mbps": sum(row["avg_rx_mbps"] for row in su_summary) if su_summary else _avg(live_downlink_rx),
        "loss_pct": 0.0,
        "latency_ms": 0.0,
        "min_rx_mbps": min(live_downlink_rx) if live_downlink_rx else 0.0,
        "max_rx_mbps": max(live_downlink_rx) if live_downlink_rx else 0.0,
    }
    uplink = {
        "tx_mbps": sum(row["avg_rx_mbps"] for row in su_summary) if su_summary else _avg(live_uplink_tx),
        "rx_mbps": sum(row["avg_rx_mbps"] for row in bsu_summary) if bsu_summary else _avg(live_uplink_rx),
        "loss_pct": 0.0,
        "latency_ms": 0.0,
        "min_rx_mbps": min(live_uplink_rx) if live_uplink_rx else 0.0,
        "max_rx_mbps": max(live_uplink_rx) if live_uplink_rx else 0.0,
    }

    combined = {
        "tx_mbps": _avg(live_combined_tx),
        "rx_mbps": total_summary.get("avg_rx_mbps", _avg(live_combined_rx)),
        "loss_pct": 0.0,
        "latency_ms": 0.0,
        "min_rx_mbps": total_summary.get("min_rx_mbps", min(live_combined_rx) if live_combined_rx else 0.0),
        "max_rx_mbps": total_summary.get("max_rx_mbps", max(live_combined_rx) if live_combined_rx else 0.0),
    }

    return {
        "combined": combined,
        "downlink": downlink,
        "uplink": uplink,
        "live_samples": live_samples,
        "summary_by_device": summary_devices,
        "consolidated_summary": consolidated,
    }


def run_trex_stats_check(
    *,
    trex_server: str,
    duration_s: int,
    expected_min_mbps: float = 0.0,
    output_json: str | None = None,
    trex_user: str = "root",
    trex_password: str = "",
    trex_dir: str = "/opt/v3.06",
    trex_pythonpath: str = "/opt/v3.06/automation/trex_control_plane/interactive/",
    trex_client_script: str = "master_script_extended_16SU.py",
    trex_ports: str = "0,1",
    trex_server_su: str | None = None,
    trex_server_su2: str | None = None,
    trex_server_su3: str | None = None,
    trex_server_su4: str | None = None,
    trex_server_cores: int = 4,
    trex_server_startup_s: int = 8,
    trex_su_count: int = 1,
    trex_dl_bw: str = "400M",
    trex_ul_bw: str = "400M",
    trex_subw: str | None = None,
    trex_packet_size: int = 1500,
    trex_direction: str = "bidi",
    trex_protocol: str = "udp",
    trex_vlan: int | None = None,
    trex_enable_graph: bool = False,
    run_mode: str = "max_throughput",
    dut_host: str | None = None,
    dut_user: str = "root",
    dut_password: str = "",
    dut_radio_idx: int = 1,
    dut_sample_interval_s: int = 5,
) -> dict[str, object]:
    server_process = None
    server_output = ""
    client_output = ""

    client_dir, client_name = _normalize_client_script(trex_client_script)
    client_cd = "cd ~" if client_dir == "~" else f"cd {shlex.quote(client_dir)}"
    client_args = [
        "python3",
        shlex.quote(client_name),
        "--debug",
        "--server-bsu",
        "127.0.0.1",
        "--su",
        str(trex_su_count),
        "--dl-bw",
        trex_dl_bw,
        "--ul-bw",
        trex_ul_bw,
        "--size",
        str(trex_packet_size),
        "--duration",
        str(duration_s),
        "--dir",
        trex_direction,
        "--proto",
        trex_protocol,
    ]
    if trex_server_su:
        client_args.extend(["--server-su", trex_server_su])
    if trex_server_su2:
        client_args.extend(["--server-su2", trex_server_su2])
    if trex_server_su3:
        client_args.extend(["--server-su3", trex_server_su3])
    if trex_server_su4:
        client_args.extend(["--server-su4", trex_server_su4])
    if trex_subw:
        client_args.extend(["--subw", trex_subw])
    if trex_vlan is not None:
        client_args.extend(["--vlan", str(trex_vlan)])
    if trex_enable_graph:
        client_args.append("--graph")
    client_script = "\n".join(
        [
            "set -euo pipefail",
            f"export PYTHONPATH={shlex.quote(trex_pythonpath)}",
            f"export TREX_PORTS={shlex.quote(trex_ports)}",
            client_cd,
            " ".join(client_args),
        ]
    )

    dut_counters: dict[str, object] = {"pre": None, "samples": [], "post": None}
    started_at = _utc_now()
    validation: dict[str, object]

    try:
        server_process, server_collector = _start_trex_server(
            trex_server=trex_server,
            trex_user=trex_user,
            trex_password=trex_password,
            trex_dir=trex_dir,
            server_cores=trex_server_cores,
        )
        time.sleep(max(1, trex_server_startup_s))
        if server_process.poll() is not None:
            raise RuntimeError(f"TRex server exited early: {server_collector.tail()}")

        dut_counters["pre"] = _sample_dut_counters(
            dut_host=dut_host,
            dut_user=dut_user,
            dut_password=dut_password,
            dut_radio_idx=dut_radio_idx,
        )

        client_process = subprocess.Popen(
            _build_ssh_command(trex_server, trex_user, trex_password, client_script),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        client_collector = _OutputCollector(client_process)
        client_collector.start()

        deadline = time.time() + max(duration_s + 120, 180)
        while client_process.poll() is None and time.time() < deadline:
            if dut_host:
                snapshot = _sample_dut_counters(
                    dut_host=dut_host,
                    dut_user=dut_user,
                    dut_password=dut_password,
                    dut_radio_idx=dut_radio_idx,
                )
                if snapshot:
                    dut_counters["samples"].append(snapshot)
            time.sleep(max(1, dut_sample_interval_s))

        if client_process.poll() is None:
            client_process.terminate()
            try:
                client_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                client_process.kill()
                client_process.wait(timeout=5)
            raise RuntimeError("TRex client run timed out before completion.")

        client_collector.join(timeout=2)
        client_output = client_collector.text()
        if client_process.returncode != 0:
            raise RuntimeError(f"TRex client exited with code {client_process.returncode}: {client_collector.tail()}")

        dut_counters["post"] = _sample_dut_counters(
            dut_host=dut_host,
            dut_user=dut_user,
            dut_password=dut_password,
            dut_radio_idx=dut_radio_idx,
        )

        parsed = parse_trex_client_output(client_output)
        observed_rx_mbps = float(parsed["combined"]["rx_mbps"])
        traffic_observed = observed_rx_mbps > 0.0 or bool(parsed["live_samples"])

        if expected_min_mbps > 0:
            passed = observed_rx_mbps >= expected_min_mbps
            reason = (
                f"Combined RX {observed_rx_mbps:.2f} Mbps met expected minimum {expected_min_mbps:.2f} Mbps."
                if passed
                else f"Combined RX {observed_rx_mbps:.2f} Mbps stayed below expected minimum {expected_min_mbps:.2f} Mbps."
            )
        elif run_mode == "counter_check":
            post = dut_counters.get("post") or {}
            tx_seen = _to_float(str(post.get("tx_tput_mbps", "0"))) > 0
            rx_seen = _to_float(str(post.get("rx_tput_mbps", "0"))) > 0
            passed = traffic_observed and (tx_seen or rx_seen)
            reason = (
                "Traffic was observed in TRex output and DUT-side throughput counters were non-zero."
                if passed
                else "TRex output or DUT-side throughput counters did not show active traffic."
            )
        else:
            passed = traffic_observed
            reason = "Traffic samples were captured from TRex output." if passed else "No traffic samples were captured."

        validation = {
            "passed": passed,
            "reason": reason,
            "expected_min_mbps": expected_min_mbps,
            "observed_rx_mbps": observed_rx_mbps,
            "run_mode": run_mode,
        }

        result = {
            "backend": "trex",
            "mode": run_mode,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "trex_server": {
                "host": trex_server,
                "user": trex_user,
                "ports": [port.strip() for port in trex_ports.split(",") if port.strip()],
                "su_servers": [host for host in [trex_server_su, trex_server_su2, trex_server_su3, trex_server_su4] if host],
                "directory": trex_dir,
                "pythonpath": trex_pythonpath,
                "client_script": trex_client_script,
                "server_cores": trex_server_cores,
            },
            "client_config": {
                "su_count": trex_su_count,
                "dl_bw": trex_dl_bw,
                "ul_bw": trex_ul_bw,
                "subw": trex_subw,
                "packet_size": trex_packet_size,
                "duration_s": duration_s,
                "direction": trex_direction,
                "protocol": trex_protocol,
                "vlan": trex_vlan,
                "graph": trex_enable_graph,
            },
            "combined": parsed["combined"],
            "downlink": parsed["downlink"],
            "uplink": parsed["uplink"],
            "dut_counters": dut_counters,
            "validation": validation,
            "live_samples": parsed["live_samples"],
            "summary_by_device": parsed["summary_by_device"],
            "consolidated_summary": parsed["consolidated_summary"],
            "client_output_tail": "\n".join(client_output.splitlines()[-120:]),
            "server_output_tail": "",
        }
    except Exception as exc:
        result = {
            "backend": "trex",
            "mode": run_mode,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "combined": {"tx_mbps": 0.0, "rx_mbps": 0.0, "loss_pct": 0.0, "latency_ms": 0.0},
            "downlink": {"tx_mbps": 0.0, "rx_mbps": 0.0, "loss_pct": 0.0, "latency_ms": 0.0},
            "uplink": {"tx_mbps": 0.0, "rx_mbps": 0.0, "loss_pct": 0.0, "latency_ms": 0.0},
            "dut_counters": dut_counters,
            "validation": {"passed": False, "reason": str(exc), "expected_min_mbps": expected_min_mbps, "run_mode": run_mode},
            "live_samples": [],
            "summary_by_device": {},
            "consolidated_summary": [],
            "client_output_tail": "\n".join(client_output.splitlines()[-120:]),
            "server_output_tail": server_output,
        }
    finally:
        if server_process is not None:
            server_output = _stop_trex_server(
                server_process,
                server_collector,
                trex_server=trex_server,
                trex_user=trex_user,
                trex_password=trex_password,
            )
            result["server_output_tail"] = server_output

    if output_json:
        with open(output_json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)

    return result

