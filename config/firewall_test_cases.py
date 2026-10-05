"""Firewall catalog synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_UBR655.xlsx`` sheet ``Firewall``."""

from __future__ import annotations

from typing import Any

FIREWALL_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "FIREWALL_01",
        "title": "Verify HTTPS port accessibility",
        "steps": "Attempt connection via HTTPS (443)",
        "expected": "Connection succeeds, secure session established",
        "dut": "BTS",
        "type": "Vulnerability",
        "status": "implemented",
        "mode": "port_expect",
        "port": 443,
        "proto": "tcp",
        "want_open": True,
    },
    {
        "id": "FIREWALL_02",
        "title": "Validate HTTP port blocking",
        "steps": "Attempt connection via HTTP (80)",
        "expected": "Connection denied, port inaccessible",
        "dut": "BTS",
        "type": "Vulnerability",
        "status": "implemented",
        "mode": "port_expect",
        "port": 80,
        "proto": "tcp",
        "want_open": False,
    },
    {
        "id": "FIREWALL_03",
        "title": "Validate SSH port blocking",
        "steps": "Attempt SSH connection (22)",
        "expected": "Connection denied, port inaccessible",
        "dut": "BTS",
        "type": "Vulnerability",
        "status": "implemented",
        "mode": "port_expect",
        "port": 22,
        "proto": "tcp",
        "want_open": False,
        "note": "Plan previously failed: SSH session still established on this platform.",
    },
    {
        "id": "FIREWALL_04",
        "title": "Validate Telnet port blocking",
        "steps": "Attempt Telnet connection (23)",
        "expected": "Connection denied, port inaccessible",
        "dut": "BTS",
        "type": "Vulnerability",
        "status": "implemented",
        "mode": "port_expect",
        "port": 23,
        "proto": "tcp",
        "want_open": False,
    },
    {
        "id": "FIREWALL_05",
        "title": "Validate FTP port blocking",
        "steps": "Attempt FTP connection (21)",
        "expected": "Connection denied, port inaccessible",
        "dut": "BTS",
        "type": "Vulnerability",
        "status": "implemented",
        "mode": "port_expect",
        "port": 21,
        "proto": "tcp",
        "want_open": False,
        "note": "Plan previously failed: FTP still reachable on this platform.",
    },
    {
        "id": "FIREWALL_06",
        "title": "Run OpenVAS vulnerability scan",
        "steps": "Execute scan against device",
        "expected": "No critical vulnerabilities detected, firewall blocks malicious probes",
        "dut": "BTS",
        "type": "Vulnerability",
        "status": "implemented",
        "mode": "openvas_scan",
        "note": "Requires Greenbone/OpenVAS on the lab PC. See scripts/install_openvas.sh.",
    },
]


def all_case_ids() -> list[str]:
    return [c["id"] for c in FIREWALL_TEST_CASES]


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in FIREWALL_TEST_CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)
