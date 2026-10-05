"""Firewall lab: TCP port probes + optional Greenbone/OpenVAS scan."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from traffic.vlan_lab import lab_hosts

ARTIFACT_DIR = Path("reports/artifacts/firewall_evidence")


def _first_ip(*candidates: Any) -> str:
    for raw in candidates:
        if isinstance(raw, (list, tuple)):
            for item in raw:
                text = str(item or "").split("/")[0].strip()
                if text:
                    return text
            continue
        text = str(raw or "").split("/")[0].strip()
        if text:
            return text
    return ""


def dut_mgmt_host(profile_active: dict[str, Any]) -> str:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    return _first_ip(
        dut.get("local_ip"),
        dut.get("ssh_host"),
        hosts.get("dut_ipv4"),
        hosts.get("dut_host"),
    )


def cpe_mgmt_host(profile_active: dict[str, Any]) -> str:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    remotes = dut.get("remote_ips") or []
    return _first_ip(
        remotes[0] if remotes else "",
        hosts.get("cpe_lan_host"),
    )


def host_reachable(host: str) -> bool:
    if not host:
        return False
    if tcp_port_open(host, 443, timeout_s=2.0) or tcp_port_open(host, 22, timeout_s=2.0):
        return True
    return tcp_port_open(host, 80, timeout_s=1.5)


def scan_targets(profile_active: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for host in (dut_mgmt_host(profile_active), cpe_mgmt_host(profile_active)):
        if not host or host in seen:
            continue
        if not host_reachable(host):
            print(f"[firewall] skip unreachable target {host}")
            continue
        seen.append(host)
    return seen


def tcp_port_open(host: str, port: int, *, timeout_s: float = 3.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def nmap_state(host: str, port: int) -> str:
    """Return nmap state (open/closed/filtered) when nmap is installed."""
    nmap = shutil.which("nmap")
    if not nmap:
        return ""
    proc = subprocess.run(
        [nmap, "-Pn", "-p", str(int(port)), "--host-timeout", "20s", host],
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    for state in ("open", "filtered", "closed"):
        if f"{port}/tcp {state}" in text or f"{port}/tcp {state}" in text.replace("  ", " "):
            return state
        if f"{port}/tcp" in text and state in text:
            # e.g. "22/tcp open  ssh"
            for line in text.splitlines():
                if line.strip().startswith(f"{port}/tcp") and state in line.split():
                    return state
    return ""


def write_evidence(case_id: str, payload: dict[str, Any]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{case_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[firewall] evidence -> {path}")


def openvas_available() -> tuple[bool, str]:
    """Detect a usable Greenbone/OpenVAS CLI on the lab PC."""
    for name in ("gvm-cli", "omp", "greenbone-security-assistant"):
        path = shutil.which(name)
        if path:
            return True, path
    if shutil.which("docker"):
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        names = proc.stdout or ""
        if any(tok in names.lower() for tok in ("gvm", "gvmd", "openvas", "greenbone")):
            return True, "docker"
    hint = os.environ.get("OPENVAS_HINT") or "scripts/install_openvas.sh"
    return False, hint


def run_openvas_scan(host: str) -> dict[str, Any]:
    """Best-effort targeted scan. Returns parsed summary; caller asserts no Critical."""
    xml_cmd = os.environ.get("OPENVAS_SCAN_CMD", "").strip()
    if xml_cmd:
        proc = subprocess.run(
            xml_cmd.format(host=host),
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=int(os.environ.get("OPENVAS_TIMEOUT_S", "1800")),
        )
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return {
            "ok": proc.returncode == 0,
            "rc": proc.returncode,
            "output": text[-8000:],
            "critical": _count_severity(text, "critical"),
            "high": _count_severity(text, "high"),
        }

    gvm = shutil.which("gvm-cli")
    if not gvm:
        raise RuntimeError("OpenVAS/GVM CLI not found")
    user = os.environ.get("GVM_USER", "admin")
    password = os.environ.get("GVM_PASSWORD", "")
    if not password:
        raise RuntimeError("Set GVM_PASSWORD (and optional GVM_USER) for gvm-cli")
    # Socket mode after `gvm-start` on Ubuntu.
    create = subprocess.run(
        [
            gvm,
            "socket",
            "--gmp-username",
            user,
            "--gmp-password",
            password,
            "--xml",
            f"<create_target><name>ubr-{int(time.time())}</name>"
            f"<hosts>{host}</hosts></create_target>",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return {
        "ok": create.returncode == 0 and "status_text=\"OK\"" in (create.stdout or ""),
        "rc": create.returncode,
        "output": ((create.stdout or "") + (create.stderr or ""))[-8000:],
        "critical": _count_severity(create.stdout or "", "critical"),
        "high": _count_severity(create.stdout or "", "high"),
        "note": "Target created; full scan needs a configured GVM scan config / task. "
        "Set OPENVAS_SCAN_CMD for a complete task XML workflow.",
    }


def _count_severity(text: str, word: str) -> int:
    return text.lower().count(word.lower())
