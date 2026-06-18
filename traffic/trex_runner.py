from __future__ import annotations

from collections.abc import Callable
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from utils.net_utils import format_ssh_host, is_ipv6_literal, normalize_ip
from utils.vlan_uci import _iface_keys, _qinq, qinq_tags_from_profile

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
    r"\s*(?P<avg_tx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<min_tx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<max_tx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<avg_rx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<min_rx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<max_rx>[\d,.]+|N/A)\s*\|$"
)
SUMMARY_ROW_LEGACY_RE = re.compile(
    r"^\|\s*(?P<device>[A-Za-z0-9_]+)\s*\|"
    r"\s*(?P<avg_rx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<min_rx>[\d,.]+|N/A)\s*\|"
    r"\s*(?P<max_rx>[\d,.]+|N/A)\s*\|$"
)
CONSOLIDATED_ROW_RE = re.compile(r"^\|\s*(?P<pkt_size>\d+)\s*\|\s*(?P<bidi_mbps>[\d,.]+)\s*\|$")
TREX_PORT_LINK_RE = re.compile(r"\(link\s+(UP|DOWN)\)\s*(\d+)", re.IGNORECASE)
TREX_PORT_HEADER_RE = re.compile(r"^\s*port\s*:\s*(\d+)\s*$", re.IGNORECASE)


class _OutputCollector:
    def __init__(
        self,
        process: subprocess.Popen[str],
        *,
        echo: bool = False,
        prefix: str = "",
    ):
        self._process = process
        self._lines: list[str] = []
        self._echo = echo
        self._prefix = prefix
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
            if self._echo:
                sys.stdout.write(f"{self._prefix}{line}")
                sys.stdout.flush()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _to_float(value: str) -> float:
    """Parse a numeric string; never raise (DUT samples may contain SSH error text)."""
    cleaned = (str(value) or "").strip().replace(",", "")
    if not cleaned or cleaned.upper() == "N/A":
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _build_ssh_command(host: str, user: str, password: str, remote_script: str) -> list[str]:
    ssh_host = format_ssh_host(host)
    ssh_target = normalize_ip(host)
    command: list[str] = []
    if password:
        command.extend(["sshpass", "-p", password])
    ssh_command = ["ssh", *SSH_OPTIONS]
    if is_ipv6_literal(ssh_target):
        ssh_command.extend(["-6", "-l", user, ssh_target])
    else:
        ssh_command.append(f"{user}@{ssh_host}")
    ssh_command.append(f"bash -lc {shlex.quote(remote_script)}")
    command.extend(ssh_command)
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


def _unique_trex_hosts(*hosts: str | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for host in hosts:
        if not host:
            continue
        clean = str(host).strip()
        if clean and clean not in seen:
            seen.add(clean)
            ordered.append(clean)
    return ordered


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
    collector = _OutputCollector(process, echo=False, prefix="[TRex server] ")
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


def parse_trex_server_port_states(server_output: str) -> dict[int, str]:
    """Parse TRex server per-port link state from interactive stats table output."""
    states: dict[int, str] = {}
    clean = _strip_ansi(server_output)
    for match in TREX_PORT_LINK_RE.finditer(clean):
        states[int(match.group(2))] = match.group(1).upper()

    # Header row often mixes plain port ids (UP) and "(link DOWN) N" cells.
    for line in clean.splitlines():
        lower = line.lower()
        if "ports |" not in lower or "-----" in line:
            continue
        for cell in line.split("|")[1:]:
            cell = cell.strip()
            down_match = re.match(r"\(link\s+DOWN\)\s*(\d+)", cell, flags=re.IGNORECASE)
            up_match = re.match(r"\(link\s+UP\)\s*(\d+)", cell, flags=re.IGNORECASE)
            if down_match:
                states[int(down_match.group(1))] = "DOWN"
            elif up_match:
                states[int(up_match.group(1))] = "UP"
            elif cell.isdigit():
                port = int(cell)
                if port not in states:
                    states[port] = "UP"

    # Startup banner: "port : 0" followed by "link : Link Up ..."
    current_port: int | None = None
    for line in clean.splitlines():
        port_match = TREX_PORT_HEADER_RE.match(line.strip())
        if port_match:
            current_port = int(port_match.group(1))
            continue
        if current_port is None:
            continue
        lower = line.lower()
        if "link up" in lower:
            states[current_port] = "UP"
            current_port = None
        elif "link down" in lower:
            states[current_port] = "DOWN"
            current_port = None
    return states


def _check_trex_ports_via_api(
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
    trex_pythonpath: str,
    trex_ports: str,
    suppress_warnings: bool = False,
) -> tuple[dict[int, str], str]:
    ports = [int(item.strip()) for item in trex_ports.split(",") if item.strip()]
    port_literal = ", ".join(str(port) for port in ports)
    remote_script = "\n".join(
        [
            "set -euo pipefail",
            f"export PYTHONPATH={shlex.quote(trex_pythonpath)}",
            "python3 - <<'PY'",
            "import sys",
            "from trex.stl.api import STLClient",
            f"ports = [{port_literal}]",
            "def _link_state(info):",
            "    if isinstance(info, list):",
            "        info = info[0] if info else {}",
            "    if isinstance(info, dict):",
            "        text = str(info.get('link', info.get('status', 'down'))).upper()",
            "    else:",
            "        text = str(info).upper()",
            "    return 'UP' if 'UP' in text else 'DOWN'",
            "states = {}",
            "client = STLClient(server='127.0.0.1')",
            "try:",
            "    client.connect()",
            "    for port in ports:",
            "        info = client.get_port_info(port)",
            "        states[port] = _link_state(info)",
            "finally:",
            "    try:",
            "        client.disconnect()",
            "    except Exception:",
            "        pass",
            "for port, state in sorted(states.items()):",
            "    print(f'TREX_PORT_STATUS {port} {state}')",
            "PY",
        ]
    )
    result = _run_remote_command(
        trex_server,
        trex_user,
        trex_password,
        remote_script,
        timeout_s=45,
        check=False,
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    states: dict[int, str] = {}
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == "TREX_PORT_STATUS":
            states[int(parts[1])] = parts[2].upper()
    if not states and output.strip() and not suppress_warnings:
        print(
            f"[TRex][WARN] Port API probe returned no TREX_PORT_STATUS lines "
            f"(exit {result.returncode}):\n{output.strip()}"
        )
    elif not states and not suppress_warnings:
        print(
            f"[TRex][WARN] Port API probe returned no data (exit {result.returncode}). "
            "Is the TRex server RPC listening on 127.0.0.1?"
        )
    return states, output


def wait_for_trex_ports_link_up(
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
    trex_pythonpath: str,
    trex_ports: str,
    server_output: str = "",
    server_output_getter: Callable[[], str] | None = None,
    timeout_s: float = 90.0,
    poll_s: float = 3.0,
) -> None:
    """Poll until requested TRex NIC ports report link UP (server RPC may need time after start)."""
    required = [int(item.strip()) for item in trex_ports.split(",") if item.strip()]
    deadline = time.time() + timeout_s
    attempt = 0
    last_api_debug = ""
    api_warned = False

    while time.time() < deadline:
        attempt += 1
        current_output = server_output_getter() if server_output_getter else server_output
        states = parse_trex_server_port_states(current_output)
        if not all(port in states for port in required):
            api_states, last_api_debug = _check_trex_ports_via_api(
                trex_server=trex_server,
                trex_user=trex_user,
                trex_password=trex_password,
                trex_pythonpath=trex_pythonpath,
                trex_ports=trex_ports,
                suppress_warnings=api_warned,
            )
            if not api_states and last_api_debug.strip():
                api_warned = True
            states.update(api_states)

        readable = ", ".join(f"{port}={states.get(port, 'UNKNOWN')}" for port in required)
        down_ports = [port for port in required if states.get(port) != "UP"]
        if not down_ports:
            print(f"[TRex] Port link check passed: {readable}")
            return

        if attempt == 1 or attempt % 5 == 0:
            print(f"[TRex] Waiting for port link UP (attempt {attempt}): {readable}")
        time.sleep(poll_s)

    detail = ""
    if last_api_debug.strip():
        tail = "\n".join(last_api_debug.strip().splitlines()[-12:])
        detail = f" Last API probe:\n{tail}"
    raise RuntimeError(
        f"TRex port(s) {required} are not link UP ({readable}). "
        f"Timed out after {timeout_s:.0f}s.{detail} "
        "Aborting test — fix NIC cabling/link or TRex server startup before re-running."
    )


def assert_trex_ports_link_up(
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
    trex_pythonpath: str,
    trex_ports: str,
    server_output: str = "",
) -> None:
    """Raise if any requested TRex port is not link UP (do not start traffic)."""
    wait_for_trex_ports_link_up(
        trex_server=trex_server,
        trex_user=trex_user,
        trex_password=trex_password,
        trex_pythonpath=trex_pythonpath,
        trex_ports=trex_ports,
        server_output=server_output,
        timeout_s=90.0,
        poll_s=3.0,
    )


def stop_remote_trex_server(
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
) -> None:
    """Stop any TRex server process on the remote host (idempotent)."""
    print(f"[TRex] Stopping remote TRex server on {trex_server} (if running)...")
    _run_remote_command(
        trex_server,
        trex_user,
        trex_password,
        "pkill -f '_t-rex-64' || true; pkill -f 't-rex-64' || true; sleep 1",
        timeout_s=15,
        check=False,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if not _has_running_trex_server(
            trex_server=trex_server,
            trex_user=trex_user,
            trex_password=trex_password,
        ):
            return
        _run_remote_command(
            trex_server,
            trex_user,
            trex_password,
            "pkill -9 -f '_t-rex-64' || true; pkill -9 -f 't-rex-64' || true",
            timeout_s=15,
            check=False,
        )
        time.sleep(1)
    print(f"[TRex] WARN: TRex process may still be running on {trex_server}")


def stop_remote_trex_servers(
    hosts: list[str],
    *,
    trex_user: str,
    trex_password: str,
) -> None:
    for host in _unique_trex_hosts(*hosts):
        stop_remote_trex_server(
            trex_server=host,
            trex_user=trex_user,
            trex_password=trex_password,
        )


def _has_running_trex_server(
    *,
    trex_server: str,
    trex_user: str,
    trex_password: str,
) -> bool:
    result = _run_remote_command(
        trex_server,
        trex_user,
        trex_password,
        "ps -eo pid=,args= | grep -E '[/_]t-rex-64 -i' | grep -v grep || true",
        timeout_s=15,
        check=False,
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    return bool(output)


def _all_trex_servers_running(
    hosts: list[str],
    *,
    trex_user: str,
    trex_password: str,
) -> bool:
    unique = _unique_trex_hosts(*hosts)
    if not unique:
        return False
    return all(
        _has_running_trex_server(
            trex_server=host,
            trex_user=trex_user,
            trex_password=trex_password,
        )
        for host in unique
    )


BUNDLED_CLIENT_SCRIPT = (
    Path(__file__).resolve().parent / "scripts" / "master_script_extended_16SU.py"
)
BUNDLED_QINQ_TAGS = Path(__file__).resolve().parent / "scripts" / "qinq_tags.py"


def _parse_uci_int_value(text: str) -> int | None:
    lines = (text or "").strip().splitlines()
    raw = lines[-1].strip().strip("'").strip('"') if lines else ""
    if not raw or raw.lower() in {"undefined", "uci: entry not found"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def read_bts_qinq_tags_from_device(
    *,
    host: str,
    user: str,
    password: str,
    profile_tb: dict | None = None,
) -> tuple[int | None, int | None, str]:
    """
    Read BTS QinQ S-VLAN / C-VLAN from UCI over SSH.

    Returns (svlan, cvlan, source) where source is 'device', 'profile', or 'none'.
    """
    tb = profile_tb or {}
    keys = _iface_keys(tb, "bts")
    svlan: int | None = None
    cvlan: int | None = None
    try:
        svlan_result = _run_remote_command(
            host,
            user,
            password,
            f"uci get {keys['svlan']} 2>/dev/null",
            timeout_s=20,
            check=False,
        )
        cvlan_result = _run_remote_command(
            host,
            user,
            password,
            f"uci get {keys['cvlan']} 2>/dev/null",
            timeout_s=20,
            check=False,
        )
        svlan = _parse_uci_int_value(svlan_result.stdout)
        cvlan = _parse_uci_int_value(cvlan_result.stdout)
    except Exception as exc:
        print(f"[QinQ][WARN] Failed to read VLAN UCI from {host}: {exc}")

    if svlan is not None and cvlan is not None:
        return svlan, cvlan, "device"

    qinq = _qinq(tb)
    profile_svlan = qinq.get("svlan")
    profile_cvlan = qinq.get("cvlan")
    if profile_svlan is not None and profile_cvlan is not None:
        return int(profile_svlan), int(profile_cvlan), "profile"
    return None, None, "none"


def resolve_trex_qinq_tags(
    *,
    host: str | None,
    user: str,
    password: str,
    profile_tb: dict | None,
    trex_svlan: int | None = None,
    trex_cvlan: int | None = None,
    qinq_enabled: bool = True,
) -> tuple[int | None, int | None]:
    """Resolve QinQ tags for TRex: CLI override, then profile testbed.qinq only."""
    del host, user, password  # profile is authoritative for TRex QinQ tagging
    if not qinq_enabled:
        return None, None
    if trex_svlan is not None and trex_cvlan is not None:
        print(f"[QinQ] Using CLI override svlan={trex_svlan} cvlan={trex_cvlan}")
        return trex_svlan, trex_cvlan
    if (trex_svlan is None) ^ (trex_cvlan is None):
        raise ValueError("QinQ requires both trex_svlan and trex_cvlan when either is set.")
    svlan, cvlan = qinq_tags_from_profile(profile_tb or {})
    if svlan is not None and cvlan is not None:
        print(f"[QinQ] Using profile svlan={svlan} cvlan={cvlan}")
        return svlan, cvlan
    print("[QinQ] No testbed.qinq in profile; TRex streams will be untagged.")
    return None, None


def bundled_client_script_path() -> str:
    return str(BUNDLED_CLIENT_SCRIPT)


def _normalize_client_script(script_path: str) -> tuple[str, str]:
    clean = script_path.strip() or "master_script_extended_16SU.py"
    if "/" in clean:
        return os.path.dirname(clean) or "~", os.path.basename(clean)
    return "~", clean


def _build_scp_command(host: str, user: str, password: str, local_path: str, remote_path: str) -> list[str]:
    ssh_host = format_ssh_host(host)
    ssh_target = normalize_ip(host)
    command: list[str] = []
    if password:
        command.extend(["sshpass", "-p", password])
    scp_command = ["scp", *SSH_OPTIONS]
    if is_ipv6_literal(ssh_target):
        scp_command.extend(["-6"])
    scp_command.extend([local_path, f"{user}@{ssh_host}:{remote_path}"])
    command.extend(scp_command)
    return command


def deploy_trex_client_script(
    *,
    trex_server: str,
    trex_user: str = "root",
    trex_password: str = "",
    local_script: str | None = None,
    remote_script: str = "~/master_script_extended_16SU.py",
) -> str:
    """Copy the bundled TRex client script to the remote TRex host."""
    source = Path(local_script or bundled_client_script_path())
    if not source.is_file():
        raise FileNotFoundError(f"TRex client script not found: {source}")

    remote_dir, remote_name = _normalize_client_script(remote_script)
    if remote_dir != "~":
        _run_remote_command(
            trex_server,
            trex_user,
            trex_password,
            f"mkdir -p {shlex.quote(remote_dir)}",
            timeout_s=20,
            check=True,
        )
    remote_target = remote_script if remote_dir == "~" else f"{remote_dir.rstrip('/')}/{remote_name}"

    result = subprocess.run(
        _build_scp_command(trex_server, trex_user, trex_password, str(source), remote_target),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to deploy TRex client script to {trex_server}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    if BUNDLED_QINQ_TAGS.is_file():
        qinq_remote = (
            "~/qinq_tags.py"
            if remote_dir == "~"
            else f"{remote_dir.rstrip('/')}/qinq_tags.py"
        )
        qinq_result = subprocess.run(
            _build_scp_command(trex_server, trex_user, trex_password, str(BUNDLED_QINQ_TAGS), qinq_remote),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if qinq_result.returncode != 0:
            raise RuntimeError(
                f"Failed to deploy qinq_tags.py to {trex_server}: "
                f"{(qinq_result.stderr or qinq_result.stdout).strip()}"
            )
    _run_remote_command(
        trex_server,
        trex_user,
        trex_password,
        f"chmod +x {shlex.quote(remote_target)}",
        timeout_s=15,
        check=False,
    )
    return remote_target


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
        combined = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()
        if any(
            err in combined
            for err in (
                "Could not resolve hostname",
                "Permission denied",
                "Connection refused",
                "Connection timed out",
                "No route to host",
            )
        ):
            snapshot[key] = ""
            continue
        raw_value = combined.splitlines()
        snapshot[key] = raw_value[-1].strip() if raw_value else ""
    return snapshot


def _device_avg_from_live(
    live_samples: list[dict[str, object]],
    field: str,
) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for sample in live_samples:
        devices = sample.get("devices") or {}
        if not isinstance(devices, dict):
            continue
        for device, metrics in devices.items():
            if not isinstance(metrics, dict):
                continue
            value = float(metrics.get(field) or 0.0)
            buckets.setdefault(str(device), []).append(value)
    return {device: _avg(values) for device, values in buckets.items() if values}


def _enrich_summary_with_live_tx(
    summary_devices: dict[str, dict[str, float]],
    live_samples: list[dict[str, object]],
) -> None:
    """Summary table only lists Avg RX; add per-device Avg TX from live samples."""
    tx_avgs = _device_avg_from_live(live_samples, "tx_mbps")
    for device, avg_tx in tx_avgs.items():
        summary_devices.setdefault(device, {})
        summary_devices[device]["avg_tx_mbps"] = avg_tx


def _parse_summary_row(line: str) -> tuple[str, dict[str, float]] | None:
    match = SUMMARY_ROW_RE.match(line)
    if match:
        device = match.group("device")
        if device.strip("-") == "":
            return None
        return device, {
            "avg_tx_mbps": _to_float(match.group("avg_tx")),
            "min_tx_mbps": _to_float(match.group("min_tx")),
            "max_tx_mbps": _to_float(match.group("max_tx")),
            "avg_rx_mbps": _to_float(match.group("avg_rx")),
            "min_rx_mbps": _to_float(match.group("min_rx")),
            "max_rx_mbps": _to_float(match.group("max_rx")),
        }
    legacy = SUMMARY_ROW_LEGACY_RE.match(line)
    if legacy:
        device = legacy.group("device")
        if device.strip("-") == "":
            return None
        return device, {
            "avg_rx_mbps": _to_float(legacy.group("avg_rx")),
            "min_rx_mbps": _to_float(legacy.group("min_rx")),
            "max_rx_mbps": _to_float(legacy.group("max_rx")),
        }
    return None


def _sum_device_metric(rows: list[dict[str, float]], key: str) -> float:
    return sum(float(row.get(key) or 0.0) for row in rows)


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
            parsed_row = _parse_summary_row(line)
            if parsed_row:
                device, metrics = parsed_row
                summary_devices[device] = metrics
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

    _enrich_summary_with_live_tx(summary_devices, live_samples)

    live_combined_rx = [sample["combined"]["rx_mbps"] for sample in live_samples]
    live_combined_tx = [sample["combined"]["tx_mbps"] for sample in live_samples]
    live_downlink_rx = [sample["downlink"]["rx_mbps"] for sample in live_samples]
    live_downlink_tx = [sample["downlink"]["tx_mbps"] for sample in live_samples]
    live_uplink_rx = [sample["uplink"]["rx_mbps"] for sample in live_samples]
    live_uplink_tx = [sample["uplink"]["tx_mbps"] for sample in live_samples]

    bsu_summary = [
        row
        for name, row in summary_devices.items()
        if name.upper().startswith("BSU") and name.upper() != "TOTAL"
    ]
    su_summary = [
        row
        for name, row in summary_devices.items()
        if not name.upper().startswith("BSU") and name.upper() != "TOTAL"
    ]
    total_summary = summary_devices.get("TOTAL", {})

    downlink = {
        "tx_mbps": _sum_device_metric(bsu_summary, "avg_tx_mbps") or _avg(live_downlink_tx),
        "rx_mbps": _sum_device_metric(su_summary, "avg_rx_mbps") or _avg(live_downlink_rx),
        "loss_pct": 0.0,
        "latency_ms": 0.0,
        "min_rx_mbps": min(live_downlink_rx) if live_downlink_rx else 0.0,
        "max_rx_mbps": max(live_downlink_rx) if live_downlink_rx else 0.0,
    }
    uplink = {
        "tx_mbps": _sum_device_metric(su_summary, "avg_tx_mbps") or _avg(live_uplink_tx),
        "rx_mbps": _sum_device_metric(bsu_summary, "avg_rx_mbps") or _avg(live_uplink_rx),
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


def build_trex_client_command(
    *,
    trex_client_script: str = "master_script_extended_16SU.py",
    trex_pythonpath: str = "/opt/v3.06/automation/trex_control_plane/interactive/",
    trex_ports: str = "0,1",
    trex_server_su: str | None = None,
    trex_server_su2: str | None = None,
    trex_server_su3: str | None = None,
    trex_server_su4: str | None = None,
    trex_su_count: int = 1,
    trex_dl_bw: str = "400M",
    trex_ul_bw: str = "400M",
    trex_subw: str | None = None,
    trex_packet_size: int = 1500,
    duration_s: int = 30,
    trex_direction: str = "bidi",
    trex_protocol: str = "udp",
    trex_vlan: int | None = None,
    trex_svlan: int | None = None,
    trex_cvlan: int | None = None,
    trex_enable_graph: bool = False,
) -> str:
    """Return the shell snippet run on the BSU TRex host to launch the client."""
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
    if trex_svlan is not None and trex_cvlan is not None:
        client_args.extend(["--svlan", str(trex_svlan), "--cvlan", str(trex_cvlan)])
    elif trex_vlan is not None:
        client_args.extend(["--vlan", str(trex_vlan)])
    if trex_enable_graph:
        client_args.append("--graph")
    return "\n".join(
        [
            f"export PYTHONPATH={shlex.quote(trex_pythonpath)}",
            f"export TREX_PORTS={shlex.quote(trex_ports)}",
            client_cd,
            " ".join(client_args),
        ]
    )


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
    trex_svlan: int | None = None,
    trex_cvlan: int | None = None,
    trex_qinq_enabled: bool = True,
    trex_qinq_host: str | None = None,
    profile_tb: dict | None = None,
    trex_enable_graph: bool = False,
    run_mode: str = "max_throughput",
    dut_host: str | None = None,
    dut_user: str = "root",
    dut_password: str = "",
    dut_radio_idx: int = 1,
    dut_sample_interval_s: int = 5,
    reuse_existing_server: bool = False,
    keep_server_running: bool = False,
    deploy_client_script: bool = False,
) -> dict[str, object]:
    server_process = None
    server_collector: _OutputCollector | None = None
    extra_server_handles: list[tuple[str, subprocess.Popen[str], _OutputCollector]] = []
    server_output = ""
    client_output = ""
    server_reused = False
    su_hosts = _unique_trex_hosts(
        trex_server_su,
        trex_server_su2,
        trex_server_su3,
        trex_server_su4,
    )
    all_server_hosts = _unique_trex_hosts(trex_server, *su_hosts)
    qinq_host = trex_qinq_host or dut_host

    if deploy_client_script:
        deployed_path = deploy_trex_client_script(
            trex_server=trex_server,
            trex_user=trex_user,
            trex_password=trex_password,
            remote_script=trex_client_script,
        )
        trex_client_script = deployed_path

    resolved_svlan: int | None = trex_svlan
    resolved_cvlan: int | None = trex_cvlan
    if resolved_svlan is None or resolved_cvlan is None:
        if trex_qinq_enabled:
            resolved_svlan, resolved_cvlan = resolve_trex_qinq_tags(
                host=qinq_host,
                user=dut_user,
                password=dut_password,
                profile_tb=profile_tb,
                trex_svlan=trex_svlan,
                trex_cvlan=trex_cvlan,
                qinq_enabled=True,
            )
        else:
            resolved_svlan, resolved_cvlan = None, None

    client_script = build_trex_client_command(
        trex_client_script=trex_client_script,
        trex_pythonpath=trex_pythonpath,
        trex_ports=trex_ports,
        trex_server_su=trex_server_su,
        trex_server_su2=trex_server_su2,
        trex_server_su3=trex_server_su3,
        trex_server_su4=trex_server_su4,
        trex_su_count=trex_su_count,
        trex_dl_bw=trex_dl_bw,
        trex_ul_bw=trex_ul_bw,
        trex_subw=trex_subw,
        trex_packet_size=trex_packet_size,
        duration_s=duration_s,
        trex_direction=trex_direction,
        trex_protocol=trex_protocol,
        trex_vlan=trex_vlan,
        trex_svlan=resolved_svlan,
        trex_cvlan=resolved_cvlan,
        trex_enable_graph=trex_enable_graph,
    )
    client_script = "\n".join(["set -euo pipefail", client_script])

    dut_counters: dict[str, object] = {"pre": None, "samples": [], "post": None}
    started_at = _utc_now()
    result: dict[str, object] | None = None

    try:
        if reuse_existing_server and _all_trex_servers_running(
            all_server_hosts,
            trex_user=trex_user,
            trex_password=trex_password,
        ):
            server_reused = True
            print(f"[TRex] Reusing existing remote TRex server(s): {', '.join(all_server_hosts)}")
            wait_for_trex_ports_link_up(
                trex_server=trex_server,
                trex_user=trex_user,
                trex_password=trex_password,
                trex_pythonpath=trex_pythonpath,
                trex_ports=trex_ports,
                server_output="",
                timeout_s=60.0,
            )
            for su_host in su_hosts:
                wait_for_trex_ports_link_up(
                    trex_server=su_host,
                    trex_user=trex_user,
                    trex_password=trex_password,
                    trex_pythonpath=trex_pythonpath,
                    trex_ports="0",
                    server_output="",
                    timeout_s=60.0,
                )
        else:
            stop_remote_trex_servers(
                all_server_hosts,
                trex_user=trex_user,
                trex_password=trex_password,
            )
            server_process, server_collector = _start_trex_server(
                trex_server=trex_server,
                trex_user=trex_user,
                trex_password=trex_password,
                trex_dir=trex_dir,
                server_cores=trex_server_cores,
            )
            for su_host in su_hosts:
                su_process, su_collector = _start_trex_server(
                    trex_server=su_host,
                    trex_user=trex_user,
                    trex_password=trex_password,
                    trex_dir=trex_dir,
                    server_cores=trex_server_cores,
                )
                extra_server_handles.append((su_host, su_process, su_collector))
            time.sleep(max(1, trex_server_startup_s))
            if server_process.poll() is not None:
                raise RuntimeError(
                    f"TRex server exited early on {trex_server} "
                    f"(rc={server_process.returncode}): "
                    f"{server_collector.tail() or server_collector.text()[:800]}"
                )
            for su_host, su_process, su_collector in extra_server_handles:
                if su_process.poll() is not None:
                    raise RuntimeError(
                        f"TRex server exited early on {su_host} "
                        f"(rc={su_process.returncode}): "
                        f"{su_collector.tail() or su_collector.text()[:800]}"
                    )
            if su_hosts:
                print(
                    f"[TRex] Started TRex on BSU {trex_server} + SU host(s): "
                    f"{', '.join(su_hosts)}"
                )
            wait_for_trex_ports_link_up(
                trex_server=trex_server,
                trex_user=trex_user,
                trex_password=trex_password,
                trex_pythonpath=trex_pythonpath,
                trex_ports=trex_ports,
                server_output=server_collector.text(),
                server_output_getter=server_collector.text,
                timeout_s=max(90.0, float(trex_server_startup_s) + 60.0),
            )
            for su_host in su_hosts:
                wait_for_trex_ports_link_up(
                    trex_server=su_host,
                    trex_user=trex_user,
                    trex_password=trex_password,
                    trex_pythonpath=trex_pythonpath,
                    trex_ports="0",
                    server_output="",
                    timeout_s=max(60.0, float(trex_server_startup_s) + 30.0),
                )

        dut_counters["pre"] = _sample_dut_counters(
            dut_host=dut_host,
            dut_user=dut_user,
            dut_password=dut_password,
            dut_radio_idx=dut_radio_idx,
        )

        print(
            f"[TRex] Starting throughput client: DL={trex_dl_bw} UL={trex_ul_bw} "
            f"duration={duration_s}s direction={trex_direction} proto={trex_protocol}"
        )
        client_process = subprocess.Popen(
            _build_ssh_command(trex_server, trex_user, trex_password, client_script),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        client_collector = _OutputCollector(client_process, echo=False, prefix="[TRex client] ")
        client_collector.start()

        deadline = time.time() + max(duration_s + 120, 180)
        trex_started = time.time()
        last_progress_log = trex_started
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
            now = time.time()
            if now - last_progress_log >= 10:
                elapsed = int(now - trex_started)
                print(f"[TRex] Throughput running... {elapsed}s elapsed (client still active)")
                last_progress_log = now
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
                "reused_existing_server": server_reused,
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
                "svlan": resolved_svlan,
                "cvlan": resolved_cvlan,
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
        err_text = str(exc)
        if "TRex port" in err_text and "not link UP" in err_text:
            raise
        validation = {
            "passed": False,
            "reason": err_text,
            "expected_min_mbps": expected_min_mbps,
            "observed_rx_mbps": 0.0,
            "run_mode": run_mode,
        }
        result = {
            "backend": "trex",
            "mode": run_mode,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "combined": {"tx_mbps": 0.0, "rx_mbps": 0.0, "loss_pct": 0.0, "latency_ms": 0.0},
            "downlink": {"tx_mbps": 0.0, "rx_mbps": 0.0, "loss_pct": 0.0, "latency_ms": 0.0},
            "uplink": {"tx_mbps": 0.0, "rx_mbps": 0.0, "loss_pct": 0.0, "latency_ms": 0.0},
            "dut_counters": dut_counters,
            "validation": validation,
            "live_samples": [],
            "summary_by_device": {},
            "consolidated_summary": [],
            "client_output_tail": "\n".join(client_output.splitlines()[-120:]),
            "server_output_tail": server_output,
        }
    finally:
        trex_completed_ok = bool(
            result is not None and (result.get("validation") or {}).get("passed")
        )
        should_stop = not server_reused and not (keep_server_running and trex_completed_ok)
        if should_stop:
            if server_process is not None and server_collector is not None:
                print(f"[TRex] Stopping TRex server on {trex_server}")
                server_output = _stop_trex_server(
                    server_process,
                    server_collector,
                    trex_server=trex_server,
                    trex_user=trex_user,
                    trex_password=trex_password,
                )
                if result is not None:
                    result["server_output_tail"] = server_output
            for su_host, su_process, su_collector in extra_server_handles:
                print(f"[TRex] Stopping TRex server on {su_host}")
                _stop_trex_server(
                    su_process,
                    su_collector,
                    trex_server=su_host,
                    trex_user=trex_user,
                    trex_password=trex_password,
                )

    if result is None:
        raise RuntimeError("TRex stats check failed before a result payload was produced.")

    if output_json:
        with open(output_json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)

    return result

