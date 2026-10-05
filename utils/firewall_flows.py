"""Assertions for Firewall plan cases ``FIREWALL_01`` … ``FIREWALL_06``."""

from __future__ import annotations

from typing import Any

from config.firewall_test_cases import case_by_id
from utils.firewall_lab import (
    nmap_state,
    openvas_available,
    run_openvas_scan,
    scan_targets,
    tcp_port_open,
    write_evidence,
)


def execute_firewall_case(case_id: str, profile_active: dict[str, Any]) -> None:
    case = case_by_id(case_id)
    mode = case.get("mode")
    targets = scan_targets(profile_active)
    assert targets, "No BTS/CPE IPv4 mgmt host in profile"
    print(f"[firewall][{case_id}] targets={targets}")

    if mode == "port_expect":
        _mode_port_expect(case, targets)
        return
    if mode == "openvas_scan":
        _mode_openvas(case, targets)
        return
    raise RuntimeError(f"{case_id} unknown mode {mode!r}")


def _port_is_open(host: str, port: int) -> tuple[bool, bool, str]:
    sock_open = tcp_port_open(host, port)
    nmap = nmap_state(host, port)
    if nmap == "filtered":
        is_open = False
    elif nmap == "open":
        is_open = True
    else:
        is_open = sock_open
    return is_open, sock_open, nmap


def _mode_port_expect(case: dict[str, Any], targets: list[str]) -> None:
    port = int(case["port"])
    want_open = bool(case.get("want_open"))
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for host in targets:
        is_open, sock_open, nmap = _port_is_open(host, port)
        rows.append(
            {
                "host": host,
                "port": port,
                "socket_open": sock_open,
                "nmap": nmap,
                "is_open": is_open,
                "want_open": want_open,
            }
        )
        print(
            f"[firewall][{case['id']}] {host} tcp/{port} "
            f"open={is_open} socket={sock_open} nmap={nmap or 'n/a'}"
        )
        if want_open and not is_open:
            failures.append(
                f"{host}: expected TCP {port} open (socket={sock_open}, nmap={nmap or 'n/a'})"
            )
        if not want_open and is_open:
            failures.append(
                f"{host}: expected TCP {port} closed/filtered (socket={sock_open}, nmap={nmap or 'n/a'})"
            )
    write_evidence(case["id"], {"targets": rows})
    assert not failures, f"{case['id']}: " + "; ".join(failures)


def _mode_openvas(case: dict[str, Any], targets: list[str]) -> None:
    import pytest

    ok, detail = openvas_available()
    if not ok:
        pytest.skip(
            f"OpenVAS/GVM not installed ({detail}). "
            "Run scripts/install_openvas.sh, then set GVM_PASSWORD or OPENVAS_SCAN_CMD."
        )
    results = []
    crit_hosts = []
    for host in targets:
        result = run_openvas_scan(host)
        results.append({"host": host, **result})
        print(
            f"[firewall][{case['id']}] {host} openvas ok={result.get('ok')} "
            f"critical={result.get('critical')}"
        )
        if int(result.get("critical") or 0) > 0:
            crit_hosts.append(host)
    write_evidence(case["id"], {"scans": results})
    assert not crit_hosts, (
        f"{case['id']}: OpenVAS reported Critical findings on {', '.join(crit_hosts)}"
    )
