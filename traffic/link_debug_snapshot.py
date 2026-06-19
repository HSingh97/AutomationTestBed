"""Collect link/VLAN debug snapshots during SU wait and bandwidth apply."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from traffic.dut_radio_config import read_running_bandwidth, run_ssh_command
from traffic.kwn_sua_statistics import fetch_kwn_sua_statistics, is_sua_associated
from traffic.su_link_ping import _bts_ping_ok, _ensure_local_qinq_iface, _local_ping_ok, _ping_cmd
from utils.net_utils import normalize_ip
from utils.vlan_uci import build_verify_commands


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_bts_vlan_uci(
    bts_ip: str,
    user: str,
    password: str,
    profile_tb: dict[str, Any],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for cmd in build_verify_commands(profile_tb, "bts"):
        try:
            raw = run_ssh_command(bts_ip, user, password, cmd, timeout_s=15).strip()
            key = cmd.rsplit(".", 1)[-1].split()[-1] if "." in cmd else cmd
            out[key] = raw
        except RuntimeError as exc:
            out[cmd] = f"ERROR: {exc}"
    return out


def _ping_matrix(
    *,
    targets: list[str],
    bts_ip: str,
    bts_user: str,
    bts_password: str,
    qinq_iface: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for host in targets:
        host = normalize_ip(host)
        pc_ok = (
            _local_ping_ok(_ping_cmd(host, interface=qinq_iface, count=2))
            if qinq_iface
            else False
        )
        bts_ok = _bts_ping_ok(bts_ip, bts_user, bts_password, host, count=2)
        rows.append(
            {
                "host": host,
                "ping_pc_qinq": pc_ok,
                "ping_bts_ssh": bts_ok,
                "reachable": pc_ok or bts_ok,
            }
        )
    return rows


def collect_link_debug_snapshot(
    *,
    label: str,
    bts_ip: str,
    bts_user: str,
    bts_password: str,
    profile_tb: dict[str, Any] | None,
    dut_cfg: dict[str, Any] | None,
    cpe_hosts: list[str],
    radio_idx: int = 1,
    attempt: int = 0,
    phase: str = "",
    qinq_iface: str | None = None,
) -> dict[str, Any]:
    """Gather BTS sysfs, VLAN UCI, running htmode, and per-SU ping status."""
    tb = profile_tb or {}
    discovered = fetch_kwn_sua_statistics(
        bts_ip,
        ssh_user=bts_user,
        ssh_password=bts_password,
        max_sua=16,
    )
    associated = [c for c in discovered if is_sua_associated(c)]
    profile_targets = [normalize_ip(h) for h in cpe_hosts if str(h).strip()]
    sysfs_hosts = [
        normalize_ip(str(c.get("ip") or ""))
        for c in associated
        if str(c.get("ip") or "").strip() and str(c.get("ip")) != "-"
    ]
    ping_targets = sysfs_hosts or profile_targets

    if qinq_iface is None and tb:
        qinq_iface = _ensure_local_qinq_iface(
            tb,
            dut_cfg,
            bts_ip=bts_ip,
            bts_user=bts_user,
            bts_password=bts_password,
        )

    running_bw = None
    try:
        running_bw = read_running_bandwidth(bts_ip, bts_user, bts_password, radio_idx)
    except Exception as exc:
        running_bw = f"ERROR: {exc}"

    ping_rows = _ping_matrix(
        targets=ping_targets,
        bts_ip=bts_ip,
        bts_user=bts_user,
        bts_password=bts_password,
        qinq_iface=qinq_iface,
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "phase": phase,
        "attempt": attempt,
        "bts_ip": bts_ip,
        "qinq_interface": qinq_iface,
        "running_bandwidth": running_bw,
        "vlan_uci_bts": _read_bts_vlan_uci(bts_ip, bts_user, bts_password, tb),
        "sysfs_sua_count": len(discovered),
        "sysfs_associated_count": len(associated),
        "sysfs_clients": associated,
        "profile_cpe_hosts": profile_targets,
        "ping_targets": ping_targets,
        "ping_matrix": ping_rows,
        "ping_ok_count": sum(1 for row in ping_rows if row["reachable"]),
        "ping_required": len(profile_targets) or len(ping_targets),
    }


def write_link_debug_snapshot(
    debug_dir: str | Path,
    *,
    label: str,
    **kwargs: Any,
) -> Path:
    """Write one JSON snapshot under ``debug_dir``."""
    root = Path(debug_dir)
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
    path = root / f"{_utc_stamp()}_{safe}.json"
    payload = collect_link_debug_snapshot(label=label, **kwargs)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[LINK-DEBUG] Wrote {path} (ping {payload['ping_ok_count']}/{payload['ping_required']})")
    return path
