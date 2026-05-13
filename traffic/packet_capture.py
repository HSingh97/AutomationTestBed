from __future__ import annotations

import asyncio
import csv
import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from config.defaults import CAPTURE_DEFAULTS
from utils.net_utils import format_ssh_host, is_ipv6_literal, normalize_ip
from utils.recovery_manager import get_active_recovery_manager

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


@dataclass(frozen=True)
class CaptureNodeConfig:
    name: str
    host: str
    interface: str


@dataclass(frozen=True)
class JumboCaptureConfig:
    enabled: bool
    username: str
    password: str
    tool: str
    artifact_dir: str
    remote_tmp_dir: str
    nodes: tuple[CaptureNodeConfig, ...]


@dataclass(frozen=True)
class RemoteCaptureSession:
    node: CaptureNodeConfig
    pid: str
    remote_pcap: str
    remote_summary: str
    remote_log: str


@dataclass(frozen=True)
class JumboCaptureBundle:
    case_id: str
    configured_mtu: str
    payload_size: int
    target: str
    local_dir: str
    sessions: tuple[RemoteCaptureSession, ...]


@dataclass(frozen=True)
class RemoteInterfaceState:
    node: CaptureNodeConfig
    original_mtu: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _profile_capture_section() -> tuple[dict, dict]:
    manager = get_active_recovery_manager()
    if not manager:
        return {}, {}
    active = manager.profile_bundle.active
    return active.get("capture", {}), active.get("dut", {})


def load_jumbo_capture_config() -> JumboCaptureConfig:
    capture, dut = _profile_capture_section()
    username = str(capture.get("username") or CAPTURE_DEFAULTS["username"]).strip()
    password = str(capture.get("password") or CAPTURE_DEFAULTS["password"])
    bts_host = str(capture.get("bts_host") or dut.get("bts_pc_ipv6") or dut.get("bts_pc_ip") or "").strip()
    cpe_host = str(capture.get("cpe_host") or dut.get("cpe_pc_ipv6") or dut.get("cpe_pc_ip") or "").strip()
    bts_interface = str(capture.get("bts_interface") or "").strip()
    cpe_interface = str(capture.get("cpe_interface") or "").strip()

    nodes: list[CaptureNodeConfig] = []
    if bts_host and bts_interface:
        nodes.append(CaptureNodeConfig(name="bts", host=bts_host, interface=bts_interface))
    if cpe_host and cpe_interface:
        nodes.append(CaptureNodeConfig(name="cpe", host=cpe_host, interface=cpe_interface))

    return JumboCaptureConfig(
        enabled=bool(capture.get("enabled", CAPTURE_DEFAULTS["enabled"])),
        username=username,
        password=password,
        tool=str(capture.get("tool") or CAPTURE_DEFAULTS["tool"]).strip().lower(),
        artifact_dir=str(capture.get("artifact_dir") or CAPTURE_DEFAULTS["artifact_dir"]).strip(),
        remote_tmp_dir=str(capture.get("remote_tmp_dir") or CAPTURE_DEFAULTS["remote_tmp_dir"]).strip(),
        nodes=tuple(nodes),
    )


def _node_map(config: JumboCaptureConfig) -> dict[str, CaptureNodeConfig]:
    return {node.name: node for node in config.nodes}


def get_backend_ping_nodes() -> tuple[CaptureNodeConfig, CaptureNodeConfig]:
    config = load_jumbo_capture_config()
    nodes = _node_map(config)
    try:
        return nodes["bts"], nodes["cpe"]
    except KeyError as exc:
        raise RuntimeError("Capture config must define both BTS and CPE backend hosts/interfaces for PC-to-PC ping.") from exc


def get_backend_nodes_apply_order() -> tuple[CaptureNodeConfig, ...]:
    config = load_jumbo_capture_config()
    nodes = _node_map(config)
    ordered: list[CaptureNodeConfig] = []
    for key in ("cpe", "bts"):
        if key in nodes:
            ordered.append(nodes[key])
    return tuple(ordered)


def icmp_payload_for_mtu(configured_mtu: int, target_host: str) -> int:
    ip_and_icmp_overhead = 48 if ":" in target_host else 28
    payload = configured_mtu - ip_and_icmp_overhead
    if payload <= 0:
        raise ValueError(f"Configured MTU {configured_mtu} is too small for ICMP validation.")
    return payload


def _ssh_command(host: str, username: str, password: str, remote_script: str) -> list[str]:
    ssh_host = normalize_ip(host)
    command: list[str] = []
    if password:
        command.extend(["sshpass", "-p", password])
    command.extend(["ssh", *SSH_OPTIONS])
    if is_ipv6_literal(ssh_host):
        command.append("-6")
    command.extend(["-l", username, ssh_host, f"bash -lc {shlex.quote(remote_script)}"])
    return command


def _scp_command(host: str, username: str, password: str, remote_path: str, local_path: str) -> list[str]:
    ssh_host = format_ssh_host(host)
    command: list[str] = []
    if password:
        command.extend(["sshpass", "-p", password])
    command.extend(
        [
            "scp",
            *SSH_OPTIONS,
            *(["-6"] if is_ipv6_literal(host) else []),
            f"{username}@{ssh_host}:{remote_path}",
            local_path,
        ]
    )
    return command


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


async def _run_async(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(_run, command, check=check)


async def run_remote_command(host: str, username: str, password: str, remote_script: str, *, check: bool = True):
    return await _run_async(_ssh_command(host, username, password, remote_script), check=check)


async def run_remote_command_with_retry(
    host: str,
    username: str,
    password: str,
    remote_script: str,
    *,
    check: bool = True,
    attempts: int = 3,
    delay_seconds: float = 3.0,
):
    last_exc = None
    for attempt in range(attempts):
        try:
            return await run_remote_command(host, username, password, remote_script, check=check)
        except (subprocess.CalledProcessError, OSError) as exc:
            last_exc = exc
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(delay_seconds)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Unable to run remote command on {host}.")


def _remote_capture_command(tool: str, interface: str, bpf_filter: str, remote_pcap: str) -> str:
    if tool == "dumpcap":
        args = ["dumpcap", "-i", interface, "-P", "-q", "-f", bpf_filter, "-w", remote_pcap]
    else:
        args = ["tcpdump", "-i", interface, "-nn", "-U", "-s", "0", "-w", remote_pcap, bpf_filter]
    return " ".join(shlex.quote(arg) for arg in args)


def _capture_extension(tool: str) -> str:
    return ".pcapng" if tool == "dumpcap" else ".pcap"


def _bpf_filter_for_target(target: str) -> str:
    return f"icmp6 and host {target}" if ":" in target else f"icmp and host {target}"


def _summary_command(remote_pcap: str, remote_summary: str, *, target: str) -> str:
    if ":" in target:
        tshark_cmd = (
            f"tshark -r {shlex.quote(remote_pcap)} -c 20 "
            "-T fields -E header=y -E separator=, "
            "-e frame.number -e frame.len -e ipv6.src -e ipv6.dst -e _ws.col.Protocol -e icmpv6.type "
            f"> {shlex.quote(remote_summary)}"
        )
    else:
        tshark_cmd = (
            f"tshark -r {shlex.quote(remote_pcap)} -c 20 "
            "-T fields -E header=y -E separator=, "
            "-e frame.number -e frame.len -e ip.src -e ip.dst -e _ws.col.Protocol -e icmp.type "
            f"> {shlex.quote(remote_summary)}"
        )
    fallback_cmd = f"tcpdump -nn -r {shlex.quote(remote_pcap)} -c 20 > {shlex.quote(remote_summary)}"
    return f"if command -v tshark >/dev/null 2>&1; then {tshark_cmd}; else {fallback_cmd}; fi"


def _local_capture_dir(config: JumboCaptureConfig, case_id: str, configured_mtu: str) -> Path:
    root = Path(config.artifact_dir)
    if not root.is_absolute():
        root = _repo_root() / root
    token = time.strftime("%Y%m%d_%H%M%S")
    path = root / case_id / f"mtu_{configured_mtu}_{token}"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _read_remote_interface_mtu(node: CaptureNodeConfig, config: JumboCaptureConfig) -> str:
    cmd = (
        f"ip -o link show dev {shlex.quote(node.interface)} | "
        "awk '{for (i=1; i<=NF; i++) if ($i == \"mtu\") {print $(i+1); exit}}'"
    )
    result = await run_remote_command(node.host, config.username, config.password, cmd, check=True)
    mtu = (result.stdout or "").strip().splitlines()
    return mtu[-1].strip() if mtu else ""


async def read_backend_interface_mtus() -> dict[str, str]:
    config = load_jumbo_capture_config()
    mtus: dict[str, str] = {}
    for node in get_backend_nodes_apply_order():
        try:
            mtus[node.name] = await _read_remote_interface_mtu(node, config)
        except Exception:
            mtus[node.name] = ""
    return mtus


async def force_backend_interface_mtu(configured_mtu: int, *, best_effort: bool = False) -> None:
    config = load_jumbo_capture_config()
    for node in get_backend_nodes_apply_order():
        await run_remote_command(
            node.host,
            config.username,
            config.password,
            f"ip link set dev {shlex.quote(node.interface)} mtu {configured_mtu}",
            check=not best_effort,
        )


async def set_backend_interface_mtu(configured_mtu: int) -> None:
    config = load_jumbo_capture_config()
    for node in get_backend_nodes_apply_order():
        await run_remote_command_with_retry(
            node.host,
            config.username,
            config.password,
            f"ip link set dev {shlex.quote(node.interface)} mtu {configured_mtu}",
            check=True,
            attempts=3,
            delay_seconds=2.0,
        )
    await asyncio.sleep(1)


async def prepare_backend_interface_mtu(configured_mtu: int) -> tuple[RemoteInterfaceState, ...]:
    config = load_jumbo_capture_config()
    states: list[RemoteInterfaceState] = []
    for node in get_backend_nodes_apply_order():
        original_mtu = await _read_remote_interface_mtu(node, config)
        if not original_mtu:
            raise RuntimeError(f"Unable to read MTU on backend host {node.host} interface {node.interface}.")
        await run_remote_command_with_retry(
            node.host,
            config.username,
            config.password,
            f"ip link set dev {shlex.quote(node.interface)} mtu {configured_mtu}",
            check=True,
            attempts=3,
            delay_seconds=2.0,
        )
        states.append(RemoteInterfaceState(node=node, original_mtu=original_mtu))
    await asyncio.sleep(1)
    return tuple(states)


async def restore_backend_interface_mtu(states: tuple[RemoteInterfaceState, ...]) -> None:
    if not states:
        return
    config = load_jumbo_capture_config()
    for state in states:
        await run_remote_command(
            state.node.host,
            config.username,
            config.password,
            f"ip link set dev {shlex.quote(state.node.interface)} mtu {shlex.quote(state.original_mtu)}",
            check=False,
        )


async def start_jumbo_icmp_capture(case_id: str, configured_mtu: str, payload_size: int, target: str) -> JumboCaptureBundle | None:
    config = load_jumbo_capture_config()
    if not config.enabled:
        return None
    if not config.nodes:
        raise RuntimeError("Capture is enabled, but no capture nodes/interfaces are configured in the active profile.")

    local_dir = _local_capture_dir(config, case_id, configured_mtu)
    bpf_filter = _bpf_filter_for_target(target)
    token = time.strftime("%Y%m%d_%H%M%S")
    sessions: list[RemoteCaptureSession] = []

    for node in config.nodes:
        remote_prefix = f"{config.remote_tmp_dir.rstrip('/')}/{case_id.lower()}_{node.name}_mtu_{configured_mtu}_{token}"
        remote_pcap = f"{remote_prefix}{_capture_extension(config.tool)}"
        remote_summary = f"{remote_prefix}_summary.txt"
        remote_log = f"{remote_prefix}.log"
        capture_exec = _remote_capture_command(config.tool, node.interface, bpf_filter, remote_pcap)
        remote_script = "\n".join(
            [
                "set -euo pipefail",
                f"mkdir -p {shlex.quote(config.remote_tmp_dir)}",
                f"rm -f {shlex.quote(remote_pcap)} {shlex.quote(remote_summary)} {shlex.quote(remote_log)}",
                f"nohup sh -lc {shlex.quote(capture_exec)} > {shlex.quote(remote_log)} 2>&1 &",
                "echo $!",
            ]
        )
        result = await run_remote_command_with_retry(
            node.host,
            config.username,
            config.password,
            remote_script,
            check=True,
            attempts=4,
            delay_seconds=4.0,
        )
        pid = (result.stdout or "").strip().splitlines()[-1].strip()
        if not pid.isdigit():
            raise RuntimeError(
                f"Unable to start capture on {node.name} ({node.host}/{node.interface}). Output: {(result.stdout or result.stderr).strip()}"
            )
        sessions.append(
            RemoteCaptureSession(
                node=node,
                pid=pid,
                remote_pcap=remote_pcap,
                remote_summary=remote_summary,
                remote_log=remote_log,
            )
        )

    await asyncio.sleep(1)
    return JumboCaptureBundle(
        case_id=case_id,
        configured_mtu=configured_mtu,
        payload_size=payload_size,
        target=target,
        local_dir=str(local_dir),
        sessions=tuple(sessions),
    )


def _write_evidence_svg(bundle: JumboCaptureBundle, metadata: dict) -> str:
    local_dir = Path(bundle.local_dir)
    lines = [
        f"Case: {bundle.case_id}",
        f"Configured MTU: {bundle.configured_mtu}",
        f"ICMP payload: {bundle.payload_size}",
        f"Target: {bundle.target}",
        "",
    ]
    for capture in metadata.get("captures", []):
        lines.append(f"[{capture['node']}] host={capture['host']} iface={capture['interface']}")
        summary_path = capture.get("local_summary", "")
        if summary_path and Path(summary_path).exists():
            text = Path(summary_path).read_text(encoding="utf-8", errors="ignore").splitlines()[:12]
            lines.extend(text or ["<no summary lines>"])
        else:
            lines.append("<summary not available>")
        lines.append("")
    if metadata.get("ping_output"):
        lines.append("[ping output]")
        lines.extend(str(metadata["ping_output"]).splitlines()[:10])

    width = 1600
    line_height = 18
    height = max(240, 40 + (len(lines) * line_height))
    text_elements = []
    for idx, line in enumerate(lines):
        y = 32 + (idx * line_height)
        text_elements.append(
            f'<text x="24" y="{y}" font-family="monospace" font-size="14" fill="#d4d4d4">{escape(line)}</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        '<rect width="100%" height="100%" fill="#1e1e1e"/>'
        '<text x="24" y="24" font-family="monospace" font-size="16" fill="#4fc1ff">'
        "Jumbo Capture Evidence"
        "</text>"
        + "".join(text_elements)
        + "</svg>"
    )
    output_path = local_dir / "capture_evidence.svg"
    output_path.write_text(svg, encoding="utf-8")
    return str(output_path)


def _summarize_capture_file(local_summary: Path) -> dict[str, int]:
    packet_count = 0
    max_frame_len = 0
    if not local_summary.exists():
        return {"packet_count": 0, "max_frame_len": 0}
    text = local_summary.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not text:
        return {"packet_count": 0, "max_frame_len": 0}
    if text[0].startswith("frame.number,"):
        reader = csv.DictReader(text)
        for row in reader:
            try:
                frame_len = int(str(row.get("frame.len", "0")).strip() or "0")
            except ValueError:
                frame_len = 0
            packet_count += 1
            max_frame_len = max(max_frame_len, frame_len)
    else:
        for line in text:
            match = re.search(r"length\s+(\d+)", line)
            if match:
                packet_count += 1
                max_frame_len = max(max_frame_len, int(match.group(1)))
    return {"packet_count": packet_count, "max_frame_len": max_frame_len}


def validate_capture_metadata(metadata: dict, *, min_packet_count: int = 1, min_frame_len: int | None = None) -> None:
    for capture in metadata.get("captures", []):
        packet_count = int(capture.get("packet_count", 0))
        max_frame_len = int(capture.get("max_frame_len", 0))
        assert packet_count >= min_packet_count, (
            f"{capture['node']} capture did not see enough packets: expected at least {min_packet_count}, got {packet_count}."
        )
        if min_frame_len is not None:
            assert max_frame_len >= min_frame_len, (
                f"{capture['node']} capture max frame length {max_frame_len} is below expected minimum {min_frame_len}."
            )


async def run_backend_pc_ping(*, configured_mtu: int, count: int = 5) -> dict[str, object]:
    config = load_jumbo_capture_config()
    source_node, target_node = get_backend_ping_nodes()
    payload_size = icmp_payload_for_mtu(configured_mtu, target_node.host)
    if ":" in target_node.host:
        cmd = f"ping -6 -I {shlex.quote(source_node.interface)} -c {count} -s {payload_size} {target_node.host}"
    else:
        cmd = f"ping -I {shlex.quote(source_node.interface)} -c {count} -s {payload_size} {target_node.host}"
    result = await run_remote_command(source_node.host, config.username, config.password, cmd, check=False)
    output = (result.stdout or result.stderr or "").strip()
    return {
        "source_host": source_node.host,
        "source_interface": source_node.interface,
        "target_host": target_node.host,
        "command": cmd,
        "payload_size": payload_size,
        "returncode": result.returncode,
        "output": output,
    }


async def finalize_jumbo_icmp_capture(bundle: JumboCaptureBundle, *, ping_output: str = "") -> dict | None:
    if not bundle:
        return None

    config = load_jumbo_capture_config()
    local_dir = Path(bundle.local_dir)
    captures = []

    for session in bundle.sessions:
        stop_script = "\n".join(
            [
                "set +e",
                f"kill -INT {shlex.quote(session.pid)} >/dev/null 2>&1 || true",
                "sleep 2",
                f"kill -TERM {shlex.quote(session.pid)} >/dev/null 2>&1 || true",
                "sleep 1",
                _summary_command(session.remote_pcap, session.remote_summary, target=bundle.target),
            ]
        )
        await _run_async(_ssh_command(session.node.host, config.username, config.password, stop_script), check=False)

        local_pcap = local_dir / f"{session.node.name}{Path(session.remote_pcap).suffix}"
        local_summary = local_dir / f"{session.node.name}_summary.txt"
        local_log = local_dir / f"{session.node.name}.log"

        await _run_async(
            _scp_command(session.node.host, config.username, config.password, session.remote_pcap, str(local_pcap)),
            check=False,
        )
        await _run_async(
            _scp_command(session.node.host, config.username, config.password, session.remote_summary, str(local_summary)),
            check=False,
        )
        await _run_async(
            _scp_command(session.node.host, config.username, config.password, session.remote_log, str(local_log)),
            check=False,
        )

        stats = _summarize_capture_file(local_summary)
        captures.append(
            {
                "node": session.node.name,
                "host": session.node.host,
                "interface": session.node.interface,
                "pid": session.pid,
                "local_pcap": str(local_pcap),
                "local_summary": str(local_summary),
                "local_log": str(local_log),
                "packet_count": stats["packet_count"],
                "max_frame_len": stats["max_frame_len"],
            }
        )

    metadata = {
        "case_id": bundle.case_id,
        "configured_mtu": bundle.configured_mtu,
        "payload_size": bundle.payload_size,
        "target": bundle.target,
        "local_dir": bundle.local_dir,
        "captures": captures,
        "ping_output": ping_output,
    }
    metadata["evidence_svg"] = _write_evidence_svg(bundle, metadata)
    metadata_path = local_dir / "capture_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
