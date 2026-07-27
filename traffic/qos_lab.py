"""Live QoS lab helpers: TRex multi-DSCP traffic + sua queue_stats capture."""

from __future__ import annotations

import csv
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TREX_HOST = "192.168.3.3"
DEFAULT_TREX_PASSWORD = "ubuntu"
DEFAULT_DUT_HOST = "10.0.0.1"
DEFAULT_DUT_PASSWORD = "Sen@0ubRNwk$"
DEFAULT_TREX_DIR = "/opt/v3.06"
DEFAULT_TREX_PYPATH = "/opt/v3.06/automation/trex_control_plane/interactive/"
DEFAULT_PORTS = "0,1"

# LuCI SFC priority order → sua queue index (validated in lab).
QUEUE_CLASS_MAP: list[dict[str, Any]] = [
    {"queue": 0, "priority": 1, "name": "control", "pir": "Control&Signaling", "dscp": 46},
    {"queue": 1, "priority": 2, "name": "voice", "pir": "Voice_DelayCriticalApplication5G", "dscp": 34},
    {"queue": 2, "priority": 3, "name": "oam", "pir": "OAM", "dscp": 28},
    {"queue": 3, "priority": 4, "name": "arvr", "pir": "ARVR5G_5GKeyEnterprise", "dscp": 40},
    {"queue": 4, "priority": 5, "name": "gold", "pir": "Gold4G_FWA5G_eMBB5G", "dscp": 32},
    {"queue": 5, "priority": 6, "name": "bronze", "pir": "Bronze4G", "dscp": 22},
    {"queue": 6, "priority": 7, "name": "best_effort", "pir": "ALL", "dscp": 0},
    {"queue": 7, "priority": 8, "name": "unassigned", "pir": None, "dscp": 18},
]

NAME_TO_QUEUE = {c["name"]: int(c["queue"]) for c in QUEUE_CLASS_MAP}
QUEUE_TO_META = {int(c["queue"]): c for c in QUEUE_CLASS_MAP}


@dataclass
class QueueStats:
    queue: int
    max_rx_mbps: float = 0.0
    max_tx_mbps: float = 0.0
    avg_rx_mbps: float = 0.0
    avg_tx_mbps: float = 0.0
    samples: int = 0


@dataclass
class QueueCaptureResult:
    sua: str
    csv_path: Path
    sample_count: int
    interval_s: float
    queues: dict[int, QueueStats] = field(default_factory=dict)
    peak_timestamp: str = ""
    peak_tx_sum_mbps: float = 0.0

    def queue(self, index: int) -> QueueStats:
        return self.queues[index]

    def by_name(self, name: str) -> QueueStats:
        return self.queues[NAME_TO_QUEUE[name]]


@dataclass
class QoSLabRunResult:
    trex_ok: bool
    duration_s: int
    dl_bw: str
    ul_bw: str
    classes: list[str] | None
    packet_size: int
    capture: QueueCaptureResult
    trex_log_tail: str = ""


def format_queue_traffic_table(
    result: QoSLabRunResult,
    *,
    case_id: str = "",
    offered_classes: list[str] | None = None,
) -> tuple[str, str]:
    """Build console text + HTML table: stream/DSCP → queue TX stats.

    Returns ``(text, html)``.
    """
    offered = set(offered_classes or result.classes or [])
    # None / empty means all profile classes were offered.
    all_offered = not offered
    rows: list[dict[str, Any]] = []
    for meta in QUEUE_CLASS_MAP:
        q = int(meta["queue"])
        name = str(meta["name"])
        stats = result.capture.queues.get(q) or QueueStats(queue=q)
        was_offered = all_offered or name in offered
        # DSCP 18 stream folds into Voice; q7 has no PIR.
        note = ""
        if name == "unassigned":
            note = "no PIR; DSCP 18 → Voice"
        elif name == "voice" and (all_offered or "unassigned" in offered):
            note = "also carries DSCP 18"
        status = "OFFERED" if was_offered else "not offered"
        if was_offered and stats.avg_tx_mbps < 0.3 and stats.max_tx_mbps < 0.5:
            status = "OFFERED (quiet)"
        elif was_offered:
            status = "ACTIVE" if stats.avg_tx_mbps >= 0.5 or stats.max_tx_mbps >= 1.0 else "OFFERED"
        rows.append(
            {
                "queue": q,
                "name": name,
                "pir": meta.get("pir") or "—",
                "dscp": meta.get("dscp"),
                "avg_tx": stats.avg_tx_mbps,
                "max_tx": stats.max_tx_mbps,
                "avg_rx": stats.avg_rx_mbps,
                "status": status,
                "note": note,
            }
        )

    header = (
        f"QoS traffic table {case_id} | duration={result.duration_s}s "
        f"dl={result.dl_bw} ul={result.ul_bw} size={result.packet_size} "
        f"samples={result.capture.sample_count} csv={result.capture.csv_path.name}"
    )
    col = (
        f"{'Q':>2}  {'Class':<12} {'DSCP':>4}  {'AvgTX':>7}  {'MaxTX':>7}  "
        f"{'AvgRX':>7}  {'Status':<14}  PIR / note"
    )
    lines = [header, col, "-" * len(col)]
    for r in rows:
        pir_note = str(r["pir"])
        if r["note"]:
            pir_note = f"{pir_note} ({r['note']})"
        lines.append(
            f"{r['queue']:>2}  {r['name']:<12} {r['dscp']:>4}  "
            f"{r['avg_tx']:>6.2f}M  {r['max_tx']:>6.2f}M  "
            f"{r['avg_rx']:>6.2f}M  {r['status']:<14}  {pir_note}"
        )
    text = "\n".join(lines)

    body_rows = []
    for r in rows:
        status = r["status"]
        row_cls = "qos-row-idle"
        if status.startswith("ACTIVE"):
            row_cls = "qos-row-active"
        elif "quiet" in status:
            row_cls = "qos-row-quiet"
        elif status != "not offered":
            row_cls = "qos-row-offered"
        pir = escape_html(str(r["pir"]))
        note = f"<div class='qos-note'>{escape_html(r['note'])}</div>" if r["note"] else ""
        body_rows.append(
            f"<tr class='{row_cls}'>"
            f"<td class='qos-q'>q{r['queue']}</td>"
            f"<td class='qos-class'>{escape_html(r['name'])}{note}</td>"
            f"<td class='qos-num'>{r['dscp']}</td>"
            f"<td class='qos-num'>{r['avg_tx']:.2f}</td>"
            f"<td class='qos-num'>{r['max_tx']:.2f}</td>"
            f"<td><span class='qos-status'>{escape_html(status)}</span></td>"
            f"<td class='qos-pir'>{pir}</td>"
            "</tr>"
        )

    # Report UI: params first (elsewhere); queue table collapsed by default.
    html = (
        "<details class='qos-traffic-details'>"
        "<summary>Full queue table</summary>"
        "<div class='qos-table-scroll'>"
        "<table class='qos-traffic-table'>"
        "<thead><tr>"
        "<th>Q</th><th>Class</th><th>DSCP</th>"
        "<th>Avg TX</th><th>Max TX</th><th>Status</th><th>PIR</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        "</details>"
    )
    return text, html


def escape_html(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def emit_qos_traffic_table(
    result: QoSLabRunResult | list[QoSLabRunResult],
    *,
    case_id: str = "",
    offered_classes: list[str] | None = None,
) -> None:
    """Print traffic table to stdout (console + pytest-json / Jenkins report)."""
    results = result if isinstance(result, list) else [result]
    for idx, run in enumerate(results, start=1):
        label = case_id if len(results) == 1 else f"{case_id} run{idx}"
        text, html = format_queue_traffic_table(
            run, case_id=label, offered_classes=offered_classes or run.classes
        )
        print(text)
        print(f"[QOS_TRAFFIC_TABLE_HTML]{html}[/QOS_TRAFFIC_TABLE_HTML]")


def _ssh(host: str, password: str, remote: str, *, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    cmd = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"ConnectTimeout={min(12, timeout_s)}",
        f"root@{host}",
        remote,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)


def _scp_files(host: str, password: str, local_paths: list[Path], remote_dir: str = "/root/") -> None:
    cmd = [
        "sshpass",
        "-p",
        password,
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        *[str(p) for p in local_paths],
        f"root@{host}:{remote_dir}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"SCP to {host} failed: {(result.stderr or result.stdout).strip()}")


def discover_active_sua(
    *,
    dut_host: str = DEFAULT_DUT_HOST,
    dut_password: str = DEFAULT_DUT_PASSWORD,
    prefer: str = "sua4",
) -> str:
    """Return the active suaN for queue_stats capture.

    Prefers ``prefer`` (or ``QOS_SUA``) unless another SUA clearly has meaningful
    traffic. Tiny residual counters (a few kbps) must not steal selection away
    from the lab SU before TRex starts.
    """
    prefer = (os.environ.get("QOS_SUA") or prefer or "sua4").strip()
    # sysfs rx/tx_tput are in bps; ignore noise below ~0.1 Mbps.
    min_meaningful_bps = int(os.environ.get("QOS_SUA_MIN_BPS", "100000"))
    script = r"""
for s in /sys/class/kwn/sua*; do
  [ -d "$s/queue_stats" ] || continue
  name=$(basename "$s")
  sum=0
  for q in 0 1 2 3 4 5 6 7; do
    rx=$(cat "$s/queue_stats/queue$q/rx_tput" 2>/dev/null || echo 0)
    tx=$(cat "$s/queue_stats/queue$q/tx_tput" 2>/dev/null || echo 0)
    sum=$((sum + rx + tx))
  done
  mac=$(cat "$s/statistics/mac" 2>/dev/null || true)
  ipv6=$(cat "$s/statistics/ipv6" 2>/dev/null || true)
  assoc=$(cat "$s/statistics/associd" 2>/dev/null || true)
  echo "$name $sum mac=${mac:-} ipv6=${ipv6:-} assoc=${assoc:-}"
done
"""
    result = _ssh(dut_host, dut_password, script, timeout_s=40)
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    scores: dict[str, int] = {}
    associated: list[str] = []
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith("sua"):
            continue
        name = parts[0]
        try:
            scores[name] = int(parts[1])
        except ValueError:
            continue
        fields = {}
        for token in parts[2:]:
            if "=" in token:
                k, v = token.split("=", 1)
                fields[k] = v
        mac = fields.get("mac", "").strip()
        ipv6 = fields.get("ipv6", "").strip()
        assoc = fields.get("assoc", "").strip()
        if (mac and mac not in {"-", "00:00:00:00:00:00"}) or (
            ipv6 and ipv6 not in {"-", "::", "0"}
        ) or (assoc and assoc not in {"-", "0"}):
            associated.append(name)

    # Prefer configured/preferred SUA when it has meaningful traffic.
    if scores.get(prefer, 0) >= min_meaningful_bps:
        print(f"[QoS] Using preferred {prefer} (queue activity {scores[prefer]} bps)")
        return prefer

    # Otherwise pick the busiest SUA only if activity is clearly real traffic.
    if scores:
        best_name, best_sum = max(scores.items(), key=lambda item: item[1])
        if best_sum >= min_meaningful_bps:
            print(f"[QoS] Using {best_name} (queue activity {best_sum} bps)")
            return best_name

    # Idle lab: use an associated SUA. Prefer configured name only if it is associated.
    if prefer in associated:
        print(f"[QoS] Queues idle — using associated preferred {prefer}")
        return prefer
    if associated:
        # Sort suaN numerically so selection is stable (e.g. sua1 before sua10).
        def _sua_key(name: str) -> int:
            try:
                return int(name.replace("sua", ""))
            except ValueError:
                return 999
        chosen = sorted(associated, key=_sua_key)[0]
        print(f"[QoS] Queues idle — using associated {chosen} (prefer={prefer} not linked)")
        return chosen
    print(f"[QoS] Queues idle / no association — falling back to {prefer}")
    return prefer


def parse_queue_capture_csv(csv_path: Path | str) -> QueueCaptureResult:
    path = Path(csv_path)
    rows = list(csv.DictReader(path.open()))
    if not rows:
        raise ValueError(f"No samples in {path}")

    queues: dict[int, QueueStats] = {}
    peak_i = 0
    peak_sum = -1.0
    for i, row in enumerate(rows):
        tx_sum = 0.0
        for q in range(8):
            tx_sum += float(row.get(f"q{q}_tx") or 0) / 1e6
        if tx_sum > peak_sum:
            peak_sum = tx_sum
            peak_i = i

    for q in range(8):
        rxs = [float(r.get(f"q{q}_rx") or 0) / 1e6 for r in rows]
        txs = [float(r.get(f"q{q}_tx") or 0) / 1e6 for r in rows]
        queues[q] = QueueStats(
            queue=q,
            max_rx_mbps=max(rxs),
            max_tx_mbps=max(txs),
            avg_rx_mbps=sum(rxs) / len(rxs),
            avg_tx_mbps=sum(txs) / len(txs),
            samples=len(rows),
        )

    # Estimate interval from timestamps when possible.
    interval = 1.0
    if len(rows) >= 2:
        try:
            t0 = datetime.strptime(rows[0]["timestamp"], "%H:%M:%S")
            t1 = datetime.strptime(rows[1]["timestamp"], "%H:%M:%S")
            interval = max(0.5, (t1 - t0).total_seconds())
        except Exception:
            interval = 1.0

    sua = "sua?"
    name = path.name
    if name.startswith("sua") and "_queue_stats" in name:
        sua = name.split("_queue_stats", 1)[0]

    return QueueCaptureResult(
        sua=sua,
        csv_path=path,
        sample_count=len(rows),
        interval_s=interval,
        queues=queues,
        peak_timestamp=rows[peak_i].get("timestamp", ""),
        peak_tx_sum_mbps=peak_sum,
    )


def start_trex_server(
    *,
    trex_host: str = DEFAULT_TREX_HOST,
    trex_password: str = DEFAULT_TREX_PASSWORD,
    trex_dir: str = DEFAULT_TREX_DIR,
) -> None:
    """Start TRex daemon and wait until RPC port 4501 is listening."""
    remote = f"""
set -e
pkill -f '_t-rex-64' 2>/dev/null || true
pkill -f 't-rex-64' 2>/dev/null || true
sleep 2
cd {shlex.quote(trex_dir)}
./t-rex-64 -i --no-scapy-server -c 1 --no-ofed-check > /tmp/trex_server.log 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  sleep 3
  if ss -lntp 2>/dev/null | grep -q ':4501'; then
    echo TREX_READY
    exit 0
  fi
  if netstat -lntp 2>/dev/null | grep -q ':4501'; then
    echo TREX_READY
    exit 0
  fi
done
echo TREX_FAIL
tail -40 /tmp/trex_server.log || true
exit 1
"""
    cmd = [
        "sshpass",
        "-p",
        trex_password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=12",
        f"root@{trex_host}",
        "bash",
        "-s",
    ]
    result = subprocess.run(
        cmd,
        input=remote,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if "TREX_READY" not in (result.stdout or ""):
        raise RuntimeError(
            f"TRex failed to start on {trex_host}:\n"
            f"{(result.stdout or '')[-600:]}\n{(result.stderr or '')[-300:]}"
        )



def stop_trex(
    *,
    trex_host: str = DEFAULT_TREX_HOST,
    trex_password: str = DEFAULT_TREX_PASSWORD,
) -> None:
    _ssh(
        trex_host,
        trex_password,
        "pkill -f qos_throughput_script.py 2>/dev/null || true; "
        "pkill -f '_t-rex-64' 2>/dev/null || true; "
        "pkill -f 't-rex-64' 2>/dev/null || true; echo stopped",
        timeout_s=20,
    )


def _resolve_qos_qinq() -> tuple[int | None, int | None]:
    """QinQ tags for QoS TRex streams (env override, else profile testbed.qinq)."""
    env_s = os.environ.get("QOS_SVLAN")
    env_c = os.environ.get("QOS_CVLAN")
    if env_s and env_c:
        return int(env_s), int(env_c)
    try:
        import yaml
        from utils.vlan_uci import qinq_tags_from_profile

        profile = (
            os.environ.get("QOS_PROFILE")
            or os.environ.get("PROFILE_NAME")
            or "default"
        )
        path = REPO_ROOT / "profiles" / f"{profile}.yaml"
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            svlan, cvlan = qinq_tags_from_profile(data.get("testbed") or {})
            if svlan is not None and cvlan is not None:
                return svlan, cvlan
    except Exception as exc:
        print(f"[QoS] QinQ profile resolve skipped: {exc}")
    # Match profiles/default.yaml lab tags.
    return 100, 101


def deploy_qos_scripts(
    *,
    trex_host: str = DEFAULT_TREX_HOST,
    trex_password: str = DEFAULT_TREX_PASSWORD,
) -> None:
    scripts = REPO_ROOT / "traffic" / "scripts"
    _scp_files(
        trex_host,
        trex_password,
        [
            scripts / "qos_throughput_script.py",
            scripts / "qos_classes.py",
            scripts / "qinq_tags.py",
        ],
    )


def _build_trex_client_cmd(
    *,
    duration_s: int,
    dl_bw: str,
    ul_bw: str,
    packet_size: int,
    ports: str,
    classes: list[str] | None,
    equal_share: bool,
    direction: str,
    proto: str,
    svlan: int | None = None,
    cvlan: int | None = None,
) -> str:
    args = [
        "python3",
        "qos_throughput_script.py",
        "--server-bsu",
        "127.0.0.1",
        "--su",
        "1",
        "--dl-bw",
        dl_bw,
        "--ul-bw",
        ul_bw,
        "--size",
        str(packet_size),
        "--duration",
        str(duration_s),
        "--dir",
        direction,
        "--proto",
        proto,
        "--ports",
        ports,
    ]
    if equal_share:
        args.append("--equal-share")
    if classes:
        args.append("--classes")
        args.extend(classes)
    if svlan is not None and cvlan is not None:
        args.extend(["--svlan", str(svlan), "--cvlan", str(cvlan)])
    return " ".join(shlex.quote(a) for a in args)


def capture_queue_stats(
    *,
    dut_host: str,
    dut_password: str,
    sua: str,
    duration_s: int,
    interval_s: float,
    out_csv: Path,
) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as handle:
        header = ["timestamp"] + [f"q{q}_{side}" for q in range(8) for side in ("rx", "tx")]
        handle.write(",".join(header) + "\n")
        handle.flush()
        end = time.monotonic() + duration_s
        while time.monotonic() < end:
            ts = datetime.now().strftime("%H:%M:%S")
            remote = (
                f"for q in 0 1 2 3 4 5 6 7; do "
                f"rx=$(cat /sys/class/kwn/{sua}/queue_stats/queue$q/rx_tput 2>/dev/null || echo 0); "
                f"tx=$(cat /sys/class/kwn/{sua}/queue_stats/queue$q/tx_tput 2>/dev/null || echo 0); "
                f"printf '%s %s ' \"$rx\" \"$tx\"; done; echo"
            )
            result = _ssh(dut_host, dut_password, remote, timeout_s=15)
            parts = (result.stdout or "").strip().split()
            values: list[str] = []
            for i in range(0, min(len(parts), 16), 2):
                values.extend([parts[i], parts[i + 1] if i + 1 < len(parts) else "0"])
            while len(values) < 16:
                values.append("0")
            handle.write(ts + "," + ",".join(values[:16]) + "\n")
            handle.flush()
            time.sleep(interval_s)


def run_qos_lab(
    *,
    duration_s: int = 15,
    dl_bw: str = "50M",
    ul_bw: str = "50M",
    packet_size: int = 1500,
    classes: list[str] | None = None,
    equal_share: bool = True,
    direction: str = "bidi",
    proto: str = "udp",
    interval_s: float = 1.0,
    trex_host: str = DEFAULT_TREX_HOST,
    trex_password: str = DEFAULT_TREX_PASSWORD,
    dut_host: str = DEFAULT_DUT_HOST,
    dut_password: str = DEFAULT_DUT_PASSWORD,
    trex_ports: str = DEFAULT_PORTS,
    trex_pythonpath: str = DEFAULT_TREX_PYPATH,
    sua: str | None = None,
    artifact_dir: str | Path | None = None,
) -> QoSLabRunResult:
    """Start TRex QoS traffic and capture sua queue_stats in parallel.

    One TRex + DUT lab can only run one QoS case at a time. A file lock serializes
    concurrent callers (e.g. pytest-xdist) so they wait instead of colliding.
    """
    artifact = Path(artifact_dir or (REPO_ROOT / "reports" / "artifacts"))
    artifact.mkdir(parents=True, exist_ok=True)
    lock_path = artifact / ".qos_lab.lock"
    lock_fh = open(lock_path, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass

    try:
        return _run_qos_lab_unlocked(
            duration_s=duration_s,
            dl_bw=dl_bw,
            ul_bw=ul_bw,
            packet_size=packet_size,
            classes=classes,
            equal_share=equal_share,
            direction=direction,
            proto=proto,
            interval_s=interval_s,
            trex_host=trex_host,
            trex_password=trex_password,
            dut_host=dut_host,
            dut_password=dut_password,
            trex_ports=trex_ports,
            trex_pythonpath=trex_pythonpath,
            sua=sua,
            artifact_dir=artifact,
        )
    finally:
        try:
            import fcntl

            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


def _run_qos_lab_unlocked(
    *,
    duration_s: int,
    dl_bw: str,
    ul_bw: str,
    packet_size: int,
    classes: list[str] | None,
    equal_share: bool,
    direction: str,
    proto: str,
    interval_s: float,
    trex_host: str,
    trex_password: str,
    dut_host: str,
    dut_password: str,
    trex_ports: str,
    trex_pythonpath: str,
    sua: str | None,
    artifact_dir: Path,
) -> QoSLabRunResult:
    active_sua = sua or discover_active_sua(dut_host=dut_host, dut_password=dut_password)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = artifact_dir / f"{active_sua}_queue_stats_{stamp}.csv"

    deploy_qos_scripts(trex_host=trex_host, trex_password=trex_password)
    start_trex_server(trex_host=trex_host, trex_password=trex_password)

    svlan, cvlan = _resolve_qos_qinq()
    if svlan is not None and cvlan is not None:
        print(f"[QoS] TRex QinQ tags svlan={svlan} cvlan={cvlan}")
    else:
        print("[QoS] TRex streams untagged (no QinQ resolved)")

    client_cmd = _build_trex_client_cmd(
        duration_s=duration_s,
        dl_bw=dl_bw,
        ul_bw=ul_bw,
        packet_size=packet_size,
        ports=trex_ports,
        classes=classes,
        equal_share=equal_share,
        direction=direction,
        proto=proto,
        svlan=svlan,
        cvlan=cvlan,
    )
    remote_traffic = (
        f"export PYTHONPATH={shlex.quote(trex_pythonpath)}; "
        f"export TREX_PORTS={shlex.quote(trex_ports)}; "
        f"cd /root && {client_cmd} > /tmp/qos_lab_run.log 2>&1; echo EXIT:$?"
    )

    traffic_proc = subprocess.Popen(
        [
            "sshpass",
            "-p",
            trex_password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            f"root@{trex_host}",
            remote_traffic,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Short ramp so 15s runs still leave enough capture window.
    ramp_s = 2 if duration_s <= 20 else 5
    time.sleep(ramp_s)
    try:
        capture_queue_stats(
            dut_host=dut_host,
            dut_password=dut_password,
            sua=active_sua,
            duration_s=max(5, duration_s - ramp_s),
            interval_s=interval_s,
            out_csv=csv_path,
        )
        stdout, _ = traffic_proc.communicate(timeout=duration_s + 60)
    finally:
        if traffic_proc.poll() is None:
            traffic_proc.kill()
        stop_trex(trex_host=trex_host, trex_password=trex_password)

    log_tail = _ssh(
        trex_host,
        trex_password,
        "tail -40 /tmp/qos_lab_run.log 2>/dev/null || true",
        timeout_s=15,
    ).stdout or ""
    trex_ok = "Port Summary" in log_tail or "EXIT:0" in (stdout or "")
    capture = parse_queue_capture_csv(csv_path)
    capture.sua = active_sua
    return QoSLabRunResult(
        trex_ok=trex_ok,
        duration_s=duration_s,
        dl_bw=dl_bw,
        ul_bw=ul_bw,
        classes=classes,
        packet_size=packet_size,
        capture=capture,
        trex_log_tail=log_tail[-1500:],
    )


def snapshot_ath1qos(
    *,
    dut_host: str = DEFAULT_DUT_HOST,
    dut_password: str = DEFAULT_DUT_PASSWORD,
) -> str:
    """Return normalized ``uci show ath1qos`` for reboot retention compare."""
    result = _ssh(
        dut_host,
        dut_password,
        "uci show ath1qos 2>/dev/null | sort",
        timeout_s=30,
    )
    text = (result.stdout or "").strip()
    if not text:
        raise RuntimeError(f"Empty ath1qos UCI on {dut_host}: {(result.stderr or '').strip()}")
    return text


def _lab_ping_ok(host: str) -> bool:
    ping = subprocess.run(
        ["ping", "-c", "1", "-W", "1", host],
        capture_output=True,
        text=True,
        check=False,
    )
    return ping.returncode == 0


def reboot_dut_and_wait(
    *,
    dut_host: str = DEFAULT_DUT_HOST,
    dut_password: str = DEFAULT_DUT_PASSWORD,
    down_poll_s: int = 90,
    up_timeout_s: int = 300,
) -> None:
    """Reboot BTS via SSH and wait until SSH answers again (sync helper).

    SSH readiness alone is not enough for QoS traffic — call
    ``wait_for_sua_ready`` before generating traffic.
    """
    # Fire reboot; connection usually drops mid-command.
    try:
        _ssh(dut_host, dut_password, "sync; reboot", timeout_s=12)
    except Exception:
        pass

    deadline = time.monotonic() + down_poll_s
    while time.monotonic() < deadline:
        if not _lab_ping_ok(dut_host):
            break
        time.sleep(2)

    up_deadline = time.monotonic() + up_timeout_s
    while time.monotonic() < up_deadline:
        if _lab_ping_ok(dut_host):
            probe = _ssh(dut_host, dut_password, "echo QOS_REBOOT_OK", timeout_s=12)
            if "QOS_REBOOT_OK" in (probe.stdout or ""):
                return
        time.sleep(5)
    raise TimeoutError(f"DUT {dut_host} did not restore SSH within {up_timeout_s}s after reboot")


def wait_for_sua_ready(
    *,
    dut_host: str = DEFAULT_DUT_HOST,
    dut_password: str = DEFAULT_DUT_PASSWORD,
    sua: str | None = None,
    timeout_s: float | None = None,
    poll_s: float = 5.0,
) -> str:
    """Poll until ``suaN`` queue_stats exists and the SU looks associated.

    Used after reboot: mgmt SSH returns before RF/SUA is ready, so traffic
    assertions would see 0 Mbps TX if we did not wait for the link.
    """
    prefer = (sua or os.environ.get("QOS_SUA") or "sua4").strip()
    timeout = float(timeout_s if timeout_s is not None else os.environ.get("QOS_REBOOT_LINK_TIMEOUT_S", "300"))
    deadline = time.monotonic() + max(timeout, 30.0)
    print(
        f"[QoS] Waiting for {prefer} link (queue_stats + association) "
        f"up to {timeout:.0f}s after reboot…"
    )
    last_detail = "not checked yet"
    while time.monotonic() < deadline:
        remote = (
            f"qs=/sys/class/kwn/{prefer}/queue_stats; "
            f"st=/sys/class/kwn/{prefer}/statistics; "
            f'if [ ! -d "$qs" ]; then echo "NO_QS"; exit 0; fi; '
            f'mac=$(cat "$st/mac" 2>/dev/null || true); '
            f'ipv6=$(cat "$st/ipv6" 2>/dev/null || true); '
            f'ip=$(cat "$st/ip" 2>/dev/null || true); '
            f'assoc=$(cat "$st/associd" 2>/dev/null || true); '
            f'rx=$(cat "$st/rx_rate" 2>/dev/null || true); '
            f'tx=$(cat "$st/tx_rate" 2>/dev/null || true); '
            f'echo "QS_OK mac=$mac ipv6=$ipv6 ip=$ip assoc=$assoc rx=$rx tx=$tx"'
        )
        try:
            result = _ssh(dut_host, dut_password, remote, timeout_s=20)
            out = (result.stdout or "").strip().splitlines()
            line = out[-1] if out else ""
        except Exception as exc:
            last_detail = f"ssh error: {exc}"
            time.sleep(poll_s)
            continue

        if not line.startswith("QS_OK"):
            last_detail = line or "queue_stats missing"
            time.sleep(poll_s)
            continue

        # Parse mac=/ipv6=/… fields from the probe line.
        fields: dict[str, str] = {}
        for token in line.split()[1:]:
            if "=" in token:
                key, val = token.split("=", 1)
                fields[key] = val

        from traffic.kwn_sua_statistics import is_sua_associated

        if is_sua_associated(fields):
            detail = (
                f"mac={fields.get('mac', '-')}, "
                f"assoc={fields.get('assoc', '-')}, "
                f"rx={fields.get('rx', '-')}"
            )
            print(f"[QoS] {prefer} ready — {detail}")
            return prefer

        last_detail = (
            f"queue_stats present but not associated yet "
            f"(mac={fields.get('mac', '-')}, ipv6={fields.get('ipv6', '-')}, "
            f"assoc={fields.get('assoc', '-')})"
        )
        time.sleep(poll_s)

    raise TimeoutError(
        f"{prefer} did not associate within {timeout:.0f}s after reboot "
        f"(last: {last_detail})"
    )


def env_overrides() -> dict[str, str]:
    """Optional env knobs for CI/lab: QOS_TREX_HOST, QOS_DUT_HOST, QOS_SUA, …"""
    keys = {
        "trex_host": "QOS_TREX_HOST",
        "trex_password": "QOS_TREX_PASSWORD",
        "dut_host": "QOS_DUT_HOST",
        "dut_password": "QOS_DUT_PASSWORD",
        "sua": "QOS_SUA",
        "trex_ports": "QOS_TREX_PORTS",
    }
    return {k: os.environ[v] for k, v in keys.items() if os.environ.get(v)}
