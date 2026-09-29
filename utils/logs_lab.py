"""DUT-side Logs helpers: SSH snapshot, snlog/session_logs, ucidyn apply."""

from __future__ import annotations

import re
import time
from typing import Any

from traffic.vlan_lab import _ssh_cmd, ensure_lab_mgmt_access, lab_hosts

NMS_PENDING_NOTE = "NMS side implementation and validation pending"


def dut_ssh_host(profile_active: dict[str, Any]) -> tuple[str, str, str]:
    hosts = lab_hosts(profile_active)
    host = str(hosts.get("dut_ipv4") or hosts.get("dut_host") or "")
    return host, str(hosts["dut_user"]), str(hosts["dut_password"])


def ssh(profile_active: dict[str, Any], remote: str, *, timeout_s: int = 45) -> str:
    host, user, password = dut_ssh_host(profile_active)
    return _ssh_cmd(host, password, remote, user=user, timeout_s=timeout_s)


def prepare_mgmt(profile_active: dict[str, Any]) -> None:
    ensure_lab_mgmt_access(profile_active)


def ucidyn_set(profile_active: dict[str, Any], key: str, value: str | int) -> str:
    from shlex import quote

    return ssh(profile_active, f"ucidyn set {key} {quote(str(value))}; echo UCIDYN_RC:$?")


def uci_get(profile_active: dict[str, Any], key: str) -> str:
    out = ssh(profile_active, f"ucidyn get {key} 2>/dev/null || uci get {key} 2>/dev/null || true")
    lines = [ln.strip().strip("'\"") for ln in out.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def snapshot_logs(profile_active: dict[str, Any]) -> dict[str, Any]:
    """Capture DUT log artifacts used by Config / System / User log tabs."""
    raw = ssh(
        profile_active,
        "echo '---LOGREAD---'; logread 2>/dev/null | tail -n 80; "
        "echo '---SNLOG_WC---'; wc -c </var/log/snlog_json 2>/dev/null || echo 0; "
        "echo '---SNLOG_TAIL---'; tail -c 2500 /var/log/snlog_json 2>/dev/null || true; "
        "echo '---SESSION---'; tail -n 40 /tmp/session_logs 2>/dev/null || true; "
        "echo '---TEMP---'; tail -n 10 /tmp/temp-log 2>/dev/null || true; "
        "echo '---WIFI_EV---'; tail -n 20 /tmp/kwn-wifi1-events.log 2>/dev/null || true",
        timeout_s=60,
    )
    parts = {
        "logread": _section(raw, "LOGREAD", "SNLOG_WC"),
        "snlog_wc": _section(raw, "SNLOG_WC", "SNLOG_TAIL").strip(),
        "snlog_tail": _section(raw, "SNLOG_TAIL", "SESSION"),
        "session": _section(raw, "SESSION", "TEMP"),
        "temp": _section(raw, "TEMP", "WIFI_EV"),
        "wifi_events": _section(raw, "WIFI_EV", None),
        "ts": time.time(),
    }
    try:
        parts["snlog_wc_int"] = int(re.sub(r"\D", "", parts["snlog_wc"]) or "0")
    except Exception:
        parts["snlog_wc_int"] = 0
    return parts


def _section(raw: str, start: str, end: str | None) -> str:
    marker = f"---{start}---"
    if marker not in raw:
        return ""
    body = raw.split(marker, 1)[1]
    if end:
        end_m = f"---{end}---"
        if end_m in body:
            body = body.split(end_m, 1)[0]
    return body.strip()


def delta_lines(before: str, after: str) -> list[str]:
    """Return lines present in after that were not in before (order-preserving)."""
    bset = set(before.splitlines())
    return [ln for ln in after.splitlines() if ln and ln not in bset]


def logging_alive(profile_active: dict[str, Any], marker: str) -> bool:
    ssh(profile_active, f"logger {marker}")
    time.sleep(0.8)
    out = ssh(profile_active, f"logread 2>/dev/null | grep -F {marker!r} | tail -n 3")
    return marker in out


def write_logs_evidence(case_id: str, payload: dict[str, Any]) -> None:
    from pathlib import Path
    import json

    out_dir = Path("reports/artifacts/logs_evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[logs] evidence -> {path}")
