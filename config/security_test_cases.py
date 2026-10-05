"""Security catalog synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_UBR655.xlsx`` sheet ``Security``."""

from __future__ import annotations

from typing import Any

SECURITY_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "SEC-01",
        "title": "Verify Secure Boot implementation with partner certificates.",
        "steps": "Flash an unsigned or falsely signed image.",
        "expected": "Bootloader rejects the image.",
        "status": "manual",
        "mode": "skip_manual",
        "note": "Needs an unsigned image and a boot-cycle; not on this stand.",
    },
    {
        "id": "SEC-02",
        "title": "Verify firmware upgrade relies on mandatory forced FOTA.",
        "steps": "Push FOTA from server; try to decline locally.",
        "expected": "Upgrade is forced without user intervention.",
        "status": "manual",
        "mode": "skip_manual",
        "note": "Needs a FOTA/ACS server.",
    },
    {
        "id": "SEC-03",
        "title": "Test firmware anti-rollback (downgrade protection).",
        "steps": "Flash a previous firmware other than (n-1).",
        "expected": "Downgrade is rejected if not (n-1).",
        "status": "implemented",
        "mode": "anti_rollback",
        "note": "Requires SECURITY_OLD_FIRMWARE path; otherwise skip.",
    },
    {
        "id": "SEC-04",
        "title": "Verify local updates using old firmware are blocked.",
        "steps": "Upload an old package via WebGUI flashops.",
        "expected": "WebUI rejects the file.",
        "status": "implemented",
        "mode": "local_old_fw",
        "note": "Requires SECURITY_OLD_FIRMWARE path; otherwise skip.",
    },
    {
        "id": "SEC-05",
        "title": "Verify only necessary ports (HTTPS, DNS) are open.",
        "steps": "TCP/UDP port scan against the device IP.",
        "expected": "Only HTTPS 443 and DNS 53 are open.",
        "status": "implemented",
        "mode": "port_scan",
        "note": "Plan comment listed extra open ports on this platform.",
    },
    {
        "id": "SEC-06",
        "title": "Ensure services run on secure, patched versions.",
        "steps": "Identify HTTPS/IoT service versions vs known high/critical CVEs.",
        "expected": "No known high/critical versions on exposed services.",
        "status": "implemented",
        "mode": "service_versions",
    },
    {
        "id": "SEC-07",
        "title": "Verify remote management via WAN is disabled.",
        "steps": "Access WebGUI/SSH/Telnet from the WAN interface.",
        "expected": "WAN management blocked; NMS/TR-069 only.",
        "status": "implemented",
        "mode": "wan_mgmt",
        "note": "Needs SECURITY_WAN_HOST or a reachable WAN IP.",
    },
    {
        "id": "SEC-08",
        "title": "Check that TR-069 (NMS) parameters are not editable in UI.",
        "steps": "Attempt to edit ACS URL or credentials.",
        "expected": "Parameters greyed out / cannot be saved.",
        "status": "implemented",
        "mode": "tr069_readonly",
    },
    {
        "id": "SEC-09",
        "title": "Verify server-side filtering and sanitization of user inputs.",
        "steps": "Inject SQLi / special characters into LuCI inputs.",
        "expected": "Server rejects or sanitizes; no execution.",
        "status": "implemented",
        "mode": "input_sanitize",
    },
    {
        "id": "SEC-10",
        "title": "Verify DNS suffix parameter in DHCP pool is blank.",
        "steps": "Inspect DHCP Option 15 / UCI domain.",
        "expected": "DNS suffix absent or blank.",
        "status": "implemented",
        "mode": "dhcp_suffix",
    },
    {
        "id": "SEC-11",
        "title": "Test for Command Injection and XSS vulnerabilities.",
        "steps": "Inject `; reboot` and `<script>` into Ping/Traceroute.",
        "expected": "Inputs sanitized; OS commands not executed.",
        "status": "implemented",
        "mode": "cmd_inject",
    },
    {
        "id": "SEC-12",
        "title": "Verify Directory Traversal protection.",
        "steps": "GET/POST `../../../etc/passwd`.",
        "expected": "Parent directories denied.",
        "status": "implemented",
        "mode": "traversal",
    },
    {
        "id": "SEC-13",
        "title": "Verify hardcoded credentials cannot be edited remotely.",
        "steps": "Attempt to modify hardcoded/root passwords via API/WebGUI.",
        "expected": "Credentials cannot be modified remotely.",
        "status": "implemented",
        "mode": "creds_locked",
        "note": "Plan: root login currently enabled on this release.",
    },
    {
        "id": "SEC-14",
        "title": "Ensure no root account exists and management is via TR-069 only.",
        "steps": "Try to login as root; verify a single standard user.",
        "expected": "Root access is completely restricted.",
        "status": "implemented",
        "mode": "no_root",
        "note": "Plan: root login currently enabled.",
    },
    {
        "id": "SEC-15",
        "title": "Verify device identifiers are read-only.",
        "steps": "Attempt to rewrite SN / MAC via UCI or config.",
        "expected": "Identifiers are immutable.",
        "status": "implemented",
        "mode": "ids_readonly",
    },
    {
        "id": "SEC-16",
        "title": "Verify software device identifiers match product box.",
        "steps": "Compare SN/MAC in software with the physical box label.",
        "expected": "Software matches the printed label.",
        "status": "manual",
        "mode": "skip_manual",
        "note": "Needs a physical box label check.",
    },
]


def all_case_ids() -> list[str]:
    return [c["id"] for c in SECURITY_TEST_CASES]


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in SECURITY_TEST_CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)
