#!/usr/bin/env python3
"""Build human-friendly Word documents for API, Wireless Security, and 2.4 GHz Radio test suites."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs"


def _set_doc_defaults(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")


def _add_title_page(doc: Document, title: str, subtitle: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(title)
    run.bold = True
    run.font.size = Pt(26)
    run.font.color.rgb = RGBColor(0x1A, 0x47, 0x7A)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run(subtitle)
    r.font.size = Pt(14)
    r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    m = meta.add_run(f"UBR Automation TestBed\n{date.today().strftime('%B %d, %Y')}")
    m.font.size = Pt(11)
    m.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()


def _h1(doc: Document, text: str) -> None:
    doc.add_heading(text, level=1)


def _h2(doc: Document, text: str) -> None:
    doc.add_heading(text, level=2)


def _h3(doc: Document, text: str) -> None:
    doc.add_heading(text, level=3)


def _p(doc: Document, text: str, *, bold: bool = False) -> None:
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = bold


def _bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def _table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = h
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.bold = True
    for ri, row in enumerate(rows):
        cells = table.rows[ri + 1].cells
        for ci, val in enumerate(row):
            cells[ci].text = val
    doc.add_paragraph()


def _code(doc: Document, text: str) -> None:
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    para.paragraph_format.left_indent = Inches(0.25)
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)


def _case_block(
    doc: Document,
    case_id: str,
    title: str,
    purpose: str,
    steps: list[str],
    checks: list[str],
    expected: str,
    code_test: str,
    code_flow: str,
    notes: str = "",
) -> None:
    _h3(doc, f"{case_id} — {title}")
    _p(doc, "Purpose", bold=True)
    _p(doc, purpose)
    _p(doc, "What the automation does", bold=True)
    _bullets(doc, steps)
    _p(doc, "How we validate (pass criteria)", bold=True)
    _bullets(doc, checks)
    _p(doc, "Expected result", bold=True)
    _p(doc, expected)
    _table(
        doc,
        ["Item", "Location in codebase"],
        [
            ["Pytest test", code_test],
            ["Validation logic", code_flow],
        ],
    )
    if notes:
        _p(doc, "Notes", bold=True)
        _p(doc, notes)


def build_api_doc() -> Path:
    doc = Document()
    _set_doc_defaults(doc)
    _add_title_page(
        doc,
        "CPE API Test Suite",
        "Automated validation guide — API_01 through API_18",
    )

    _h1(doc, "1. Introduction")
    _p(
        doc,
        "This document explains how the automation team tests the CPE REST API over the "
        "2.4 GHz management Wi‑Fi network. Tests run from a lab PC connected to the CPE "
        "hidden management SSID (KWDEJPOQ). All API calls go to http://169.254.254.1.",
    )
    _p(doc, "Who should read this?", bold=True)
    _bullets(
        doc,
        [
            "Test engineers running or reviewing API automation",
            "Developers tracing failures back to code",
            "Lab staff preparing the bench before a run",
        ],
    )

    _h1(doc, "2. At a glance")
    _table(
        doc,
        ["Topic", "Detail"],
        [
            ["Test IDs", "API_01 … API_18 (18 cases, run in order)"],
            ["Pytest marker", "CPE_API_24"],
            ["Network path", "PC → CPE 2.4 GHz Wi‑Fi → 169.254.254.1"],
            ["Main test file", "tests/API/test_API.py"],
            ["Validation logic", "utils/cpe_api_24_flows.py"],
            ["HTTP client", "utils/cpe_api_24_client.py"],
        ],
    )

    _h1(doc, "3. Before you run")
    _h2(doc, "3.1 Lab requirements")
    _bullets(
        doc,
        [
            "Automation PC joined to CPE hidden management Wi‑Fi (SSID: KWDEJPOQ).",
            "CPE management API answers at http://169.254.254.1.",
            "For alignment and throughput cases (API_04 onward): active BTS↔CPE backhaul link.",
            "For firmware cases (API_09–API_15): valid .tgz image in /tftpboot on the test PC.",
        ],
    )
    _h2(doc, "3.2 Configuration")
    _p(doc, "Bench settings live in profiles/default.yaml under cpe_api_24:. BTS SSID and password for link tests are read from the BTS over SSH when you pass --local-ipv6.")

    _h1(doc, "4. How to run")
    _h2(doc, "4.1 Full suite")
    _code(
        doc,
        'cd "AutomationTestBed"\n'
        "python3 -m pytest tests/API/test_API.py -m CPE_API_24 \\\n"
        "  --profile default \\\n"
        "  --local-ipv6 2401:4900:d0:40d4:0:17b8:0:330 \\\n"
        "  --cpe-24-fw-file=Senao-UBR650-xxx-0.0.0.703-apps.tgz -v",
    )
    _h2(doc, "4.2 Single test")
    _code(doc, "python3 -m pytest tests/API/test_API.py -m API_01 --profile default -v")
    _h2(doc, "4.3 Useful options")
    _table(
        doc,
        ["Option", "When to use it"],
        [
            ["--profile default", "Load lab bench profile"],
            ["--local-ipv6 <BTS IPv6>", "Fetch BTS credentials and check link state"],
            ["--cpe-24-fw-file=<file>.tgz", "Firmware upload/upgrade tests"],
            ["--cpe-24-auto-join-wifi", "Let nmcli join CPE mgmt Wi‑Fi automatically"],
            ["--skip-cpe-api-24", "Skip entire API suite"],
        ],
    )

    _h1(doc, "5. What you see when a test runs")
    _p(
        doc,
        "Each case prints a clear banner, a short flow summary (URL and expected HTTP code), "
        "then a comparison table with columns: Field, Actual value, Expected value, Result. "
        "Pytest ends with PASSED or FAILED and an explanation if something did not match.",
    )

    _h1(doc, "6. Test case details")
    cases = [
        (
            "API_01",
            "Connect CPE to BTS (success)",
            "Verify that a valid BTS SSID and password lets the CPE form a backhaul link.",
            [
                "Factory-reset the CPE and confirm BTS has no active partners on radio 1.",
                "POST btsconnect with live BTS SSID and password.",
            ],
            ["HTTP 200", "JSON status = success", "Message contains Connected successfully"],
            "Link forms within ~2 minutes; test passes.",
            "tests/API/test_API.py → test_api_01_btsconnect_success",
            "utils/cpe_api_24_flows.py → assert_api_01_btsconnect_success",
        ),
        (
            "API_02",
            "Wrong password (authentication fail)",
            "Confirm the API rejects a bad password and does not treat it as success.",
            ["Factory-reset CPE after API_01.", "POST btsconnect with correct SSID but wrong password."],
            ["HTTP 401", "status = failure", "reason = authentication_fail"],
            "Authentication failure returned; test passes.",
            "test_api_02_btsconnect_authentication_fail",
            "assert_api_02_btsconnect_auth_fail",
        ),
        (
            "API_03",
            "Unknown SSID",
            "Confirm invalid BTS SSID returns not-found, not a generic error.",
            ["POST btsconnect with invalid SSID and valid password (no reset before this case)."],
            ["HTTP 404", "status = failure", "reason = ssid_not_found"],
            "SSID not found error; test passes.",
            "test_api_03_btsconnect_ssid_not_found",
            "assert_api_03_btsconnect_ssid_not_found",
        ),
        (
            "API_04",
            "Alignment stream (1 second)",
            "Read live alignment metrics over Server-Sent Events at 1 s interval.",
            ["Ensure BTS↔CPE link exists (btsconnect if needed).", "GET alignment-info/stream?interval_ms=1000."],
            ["HTTP 200", "Stream contains metrics or rssi_dbm"],
            "Live alignment stream received.",
            "test_api_04_alignment_info_stream_1s",
            "assert_api_04_alignment_info_stream_1s",
        ),
        (
            "API_05",
            "Alignment stream (5 seconds)",
            "Same as API_04 but with 5 s stream interval.",
            ["GET alignment-info/stream?interval_ms=5000."],
            ["HTTP 200", "Stream contains alignment metrics"],
            "Stream at 5 s interval works.",
            "test_api_05_alignment_info_stream_5s",
            "assert_api_05_alignment_info_stream_5s",
        ),
        (
            "API_06",
            "Alignment snapshot",
            "Fetch a one-shot alignment data JSON snapshot.",
            ["GET /api/v1/cpe/alignment-data."],
            ["HTTP 200", "status = success", "Success message in response"],
            "Alignment data returned.",
            "test_api_06_alignment_data",
            "assert_api_06_alignment_data",
        ),
        (
            "API_07",
            "Software version",
            "Read CPE firmware version via API.",
            ["GET /api/v1/cpe/sw-version."],
            ["HTTP 200", "status = success", "swversion field is non-empty"],
            "Version string returned.",
            "test_api_07_sw_version",
            "assert_api_07_sw_version",
        ),
        (
            "API_08",
            "Version file missing",
            "When /etc/version is removed, API must report unavailable (then file is restored).",
            ["SSH: backup and delete /etc/version.", "GET sw-version.", "Restore /etc/version in cleanup."],
            ["HTTP 503", "reason = software_info_unavailable"],
            "503 while file missing; file restored after test.",
            "test_api_08_sw_version_missing_file_returns_503",
            "assert_api_08_sw_version_unavailable_when_file_removed",
        ),
        (
            "API_09",
            "Upload firmware",
            "Upload a firmware .tgz from the lab PC to the CPE.",
            ["POST upload-sw with file from /tftpboot."],
            ["HTTP 200", "status = success"],
            "Firmware uploaded successfully.",
            "test_api_09_upload_sw",
            "assert_api_09_upload_sw",
            "Requires --cpe-24-fw-file on CLI.",
        ),
        (
            "API_10",
            "Upload status",
            "Confirm upload completed at 100%.",
            ["GET upload-sw-status after API_09."],
            ["HTTP 200", "state = SUCCESS", "progress = 100"],
            "Upload reported complete.",
            "test_api_10_upload_sw_status",
            "assert_api_10_upload_sw_status",
        ),
        (
            "API_11",
            "Upgrade (keep settings)",
            "Upgrade firmware while preserving configuration; wait for reboot and BTS link.",
            ["POST upgrade-sw with preserveConfig=true.", "Poll until device is up and linked."],
            ["Upgrade reaches success state", "BTS↔CPE link restored"],
            "Upgrade with config preserved completes.",
            "test_api_11_upgrade_sw_preserve_config",
            "assert_api_11_upgrade_sw_preserve_config",
        ),
        (
            "API_12",
            "Upgrade (factory reset)",
            "Upgrade without preserving config; link is re-formed for later tests.",
            ["POST upgrade-sw with preserveConfig=false."],
            ["Upgrade completes", "BTS link re-established afterward"],
            "Clean upgrade path works.",
            "test_api_12_upgrade_sw_no_preserve_config",
            "assert_api_12_upgrade_sw_no_preserve_config",
        ),
        (
            "API_13",
            "Upgrade in progress",
            "While upgrade runs, status API shows in-progress state.",
            ["Start upgrade and poll upgrade-sw-status."],
            ["Status shows INPROGRESS or equivalent"],
            "In-progress state observed.",
            "test_api_13_upgrade_sw_status_in_progress",
            "assert_api_13_upgrade_sw_status_in_progress",
        ),
        (
            "API_14",
            "Upgrade success status",
            "After valid upgrade, status shows success.",
            ["Poll upgrade-sw-status after successful upgrade."],
            ["Success state in response"],
            "Success status confirmed.",
            "test_api_14_upgrade_sw_status_success",
            "assert_api_14_upgrade_sw_status_success",
        ),
        (
            "API_15",
            "Invalid firmware",
            "Wrong-model firmware must be rejected.",
            ["Upload invalid FW, start upgrade, poll status."],
            ["HTTP 422 or explicit failure for invalid FW"],
            "Invalid firmware rejected.",
            "test_api_15_upgrade_sw_status_invalid_fw",
            "assert_api_15_upgrade_sw_status_invalid_fw",
        ),
        (
            "API_16",
            "Link throughput test",
            "Run built-in throughput test while BTS link is up.",
            ["Ensure link.", "POST link-throughput-test duration=30."],
            ["HTTP 200", "status = success", "dl_tput and ul_tput present"],
            "Throughput results returned.",
            "test_api_16_link_throughput_test",
            "assert_api_16_link_throughput_test",
        ),
        (
            "API_17",
            "Throughput without BTS",
            "With backhaul broken, throughput API must fail clearly.",
            ["Break CPE radio-1 STA SSID via SSH.", "POST throughput test.", "Restore SSID after."],
            ["HTTP 409", "reason contains server did not respond"],
            "Expected failure when no BTS server.",
            "test_api_17_link_throughput_test_no_bts_server",
            "assert_api_17_link_throughput_test_no_bts_server",
        ),
        (
            "API_18",
            "Throughput already running",
            "Second throughput request while first is running must return busy.",
            ["Start 30 s test.", "POST again after 2 s overlap."],
            ["HTTP 503", "reason = test_in_progress"],
            "Busy/in-progress response returned.",
            "test_api_18_link_throughput_test_in_progress",
            "assert_api_18_link_throughput_test_in_progress",
        ),
    ]
    for c in cases:
        _case_block(doc, *c)

    _h1(doc, "7. API endpoint quick reference")
    _table(
        doc,
        ["Endpoint", "Method", "Cases"],
        [
            ["/api/v1/CPE/btsconnect", "POST", "API_01 – API_03"],
            ["/api/v1/cpe/alignment-info/stream", "GET (SSE)", "API_04 – API_05"],
            ["/api/v1/cpe/alignment-data", "GET", "API_06"],
            ["/api/v1/cpe/sw-version", "GET", "API_07 – API_08"],
            ["/api/v1/cpe/upload-sw", "POST", "API_09"],
            ["/api/v1/cpe/upload-sw-status", "GET", "API_10"],
            ["/api/v1/cpe/upgrade-sw", "POST", "API_11 – API_15"],
            ["/api/v1/cpe/upgrade-sw-status", "GET", "API_13 – API_15"],
            ["/api/v1/cpe/link-throughput-test", "POST", "API_16 – API_18"],
        ],
    )

    out = OUT_DIR / "CPE-API-Test-Suite-Guide.docx"
    doc.save(out)
    return out


def build_wireless_security_doc() -> Path:
    doc = Document()
    _set_doc_defaults(doc)
    _add_title_page(
        doc,
        "Wireless Security Test Suite",
        "WPA2 / AES-256 validation on BTS and CPE (IPv6 lab)",
    )

    _h1(doc, "1. Introduction")
    _p(
        doc,
        "These tests verify wireless security on the 5 GHz backhaul (Radio 1): BTS acts as AP, "
        "CPE as STA. All cases require an IPv6 lab profile. The target encryption is WPA2-PSK "
        "with AES-256 (UCI value: psk2+ccmp-256).",
    )

    _h1(doc, "2. At a glance")
    _table(
        doc,
        ["Topic", "Detail"],
        [
            ["Implemented cases", "W_SECURITY_01, 03, 05, 06, 07, 09, 11, 14"],
            ["Pytest marker", "WirelessSecurity"],
            ["IP mode", "IPv6 only (strict_ipv6 in profile)"],
            ["Test file", "tests/WirelessSecurity/test_wireless_security.py"],
            ["Flow logic", "utils/wireless_security_flows.py"],
            ["GUI + SSH", "Playwright LuCI + root SSH on BTS and CPE"],
        ],
    )

    _h1(doc, "3. Before you run")
    _bullets(
        doc,
        [
            "Profile must set dut.ip_mode: ipv6 and list BTS/CPE IPv6 addresses.",
            "BTS and CPE reachable over IPv6 SSH.",
            "Optional: cpe_pc_ip for out-of-band CPE access when the link is down.",
        ],
    )

    _h1(doc, "4. How to run")
    _code(
        doc,
        "python3 -m pytest tests/WirelessSecurity/test_wireless_security.py \\\n"
        "  -m WirelessSecurity --profile default \\\n"
        "  --local-ipv6 2401:4900:d0:40d4:0:17b8:0:330 \\\n"
        "  --remote-ipv6 2401:4900:d0:40d4::17b8:0:331 -v",
    )
    _p(doc, "Run one case: add -m W_SECURITY_07")

    _h1(doc, "5. How validation works")
    _p(doc, "The automation checks security in three ways:")
    _bullets(
        doc,
        [
            "GUI — LuCI encryption dropdown shows AES-256.",
            "SSH / UCI — wireless.@wifi-iface[1].encryption matches psk2+ccmp-256.",
            "Link health — IPv6 ping BTS↔CPE, association IDs, and optionally device logs.",
        ],
    )
    _p(doc, "Terminal output uses comparison tables: each row shows what was expected vs what was measured, with PASS or FAIL.")

    _h1(doc, "6. Test case details")
    cases = [
        (
            "W_SECURITY_01",
            "Ensure WPA2 on BTS and CPE",
            "Make sure both devices use AES-256 before deeper security tests.",
            [
                "Open BTS radio page; set AES-256 in GUI if needed; apply.",
                "Open CPE GUI over IPv6; repeat on CPE.",
                "Verify GUI and UCI on both sides.",
            ],
            ["GUI = AES-256", "UCI = psk2+ccmp-256 on BTS and CPE"],
            "Both devices confirmed on WPA2 AES-256.",
            "test_w_security_01_configure_wpa2_ipv6",
            "assert_w_security_01_configure_wpa2",
            "SSID and passphrase are not changed — lab pre-configured.",
        ),
        (
            "W_SECURITY_03",
            "Insecure mode and logs",
            "Setting encryption to None must drop the link; logs may show rejection.",
            [
                "On BTS: set encryption=none via SSH, verify link down, read logs, restore WPA2.",
                "On CPE: same flow (IPv6 or out-of-band SSH).",
            ],
            ["Link drops after none", "Devices restored to WPA2", "Relevant log lines if present"],
            "Insecure setting rejected; WPA2 restored.",
            "test_w_security_03_validate_rejection_logs_ipv6",
            "assert_w_security_03_validate_rejection_logs",
        ),
        (
            "W_SECURITY_05",
            "Link restore time",
            "After restoring WPA2, measure how long until the link is back.",
            ["Same none→WPA2 flow as case 03.", "Record seconds until bidirectional IPv6 ping works."],
            ["Restore time ≤ 120 s (default) after BTS and CPE restore"],
            "Link comes back within allowed time.",
            "test_w_security_05_link_uptime_after_restore_ipv6",
            "assert_w_security_05_link_uptime_after_restore",
        ),
        (
            "W_SECURITY_06",
            "5-minute stability",
            "After each WPA2 restore, ping must stay stable for 5 minutes.",
            [
                "Full insecure flow.",
                "5 min BTS→CPE and CPE→BTS ping after BTS restore.",
                "Another 5 min after CPE restore.",
            ],
            ["Zero ping failures in each 5-minute window"],
            "Link stable for full monitor duration.",
            "test_w_security_06_link_stability_monitor_ipv6",
            "assert_w_security_06_link_stability_monitor",
        ),
        (
            "W_SECURITY_07",
            "WPA2-PSK connection",
            "Configure WPA2, connect, verify association and IPv6 assignment.",
            [
                "Set WPA2 on BTS and CPE via SSH.",
                "Wait for bidirectional IPv6 ping.",
                "Check BTS AP mode, CPE STA mode, association IDs, CPE IPv6 on link table.",
            ],
            ["Modes ap/sta correct", "Association present", "IPv6 assigned and visible on BTS"],
            "Secure link up with correct association and IP.",
            "test_w_security_07_wpa2_psk_connection_ipv6",
            "assert_w_security_07_wpa2_psk_connection",
        ),
        (
            "W_SECURITY_09",
            "Association after CPE reboot",
            "CPE must re-associate on WPA2 after reboot.",
            [
                "Configure WPA2 and verify initial association.",
                "Reboot CPE over IPv6 SSH.",
                "Wait for SSH and verify association again.",
            ],
            ["Association before reboot", "Association after reboot within timeout"],
            "WPA2 association survives CPE reboot.",
            "test_w_security_09_wpa2_association_ipv6",
            "assert_w_security_09_wpa2_association",
        ),
        (
            "W_SECURITY_11",
            "Open mode on CPE (negative)",
            "BTS stays secure; CPE open mode must break the link.",
            [
                "Baseline: both on WPA2, link up.",
                "Set CPE encryption=none; BTS unchanged.",
                "Verify link down ≥ 20 s; restore CPE WPA2; verify link stable.",
            ],
            ["Link drops when CPE is open", "Link stable after CPE restore"],
            "Mismatch correctly prevents connection.",
            "test_w_security_11_open_mode_attempt_ipv6",
            "assert_w_security_11_open_mode_attempt",
        ),
        (
            "W_SECURITY_14",
            "Wrong passphrase (negative)",
            "Wrong CPE key must drop link; correct key restores it.",
            [
                "Confirm both on WPA2 with lab key.",
                "Set wrong key on CPE.",
                "Verify link down; restore original key; verify link up.",
            ],
            ["Link down on wrong key", "Link up after correct key restored"],
            "Passphrase enforcement works.",
            "test_w_security_14_invalid_passphrase_ipv6",
            "assert_w_security_14_invalid_passphrase",
        ),
    ]
    for c in cases:
        _case_block(doc, *c)

    _h1(doc, "7. Run order")
    _table(
        doc,
        ["Order", "Case"],
        [
            ["1", "W_SECURITY_01"],
            ["3", "W_SECURITY_03"],
            ["5", "W_SECURITY_05"],
            ["6", "W_SECURITY_06"],
            ["7", "W_SECURITY_07"],
            ["9", "W_SECURITY_09"],
            ["11", "W_SECURITY_11"],
            ["14", "W_SECURITY_14"],
        ],
    )

    out = OUT_DIR / "Wireless-Security-Test-Suite-Guide.docx"
    doc.save(out)
    return out


def build_radio24_doc() -> Path:
    doc = Document()
    _set_doc_defaults(doc)
    _add_title_page(
        doc,
        "2.4 GHz Radio Test Suite",
        "Hidden SSID, DHCP, boot behavior, and management API — lab guide",
    )

    _h1(doc, "1. Introduction")
    _p(
        doc,
        "This suite tests the CPE 2.4 GHz management radio from a scan PC placed near the CPE. "
        "Most Wi‑Fi actions (scan, connect, ping) run on the scan PC via SSH — not on the "
        "pytest runner desktop. The hidden SSID is KWDEJPOQ; management IP is 169.254.254.1.",
    )

    _h1(doc, "2. Lab layout")
    _p(doc, "Typical bench wiring:")
    _bullets(
        doc,
        [
            "BTS ←—— 5 GHz link ——→ CPE",
            "CPE broadcasts hidden 2.4 GHz management Wi‑Fi",
            "Scan PC (default 10.0.150.7) sits near CPE with Wi‑Fi enabled",
            "Automation runner SSHs to scan PC to run nmcli / iwlist / ping",
        ],
    )

    _h1(doc, "3. At a glance")
    _table(
        doc,
        ["Topic", "Detail"],
        [
            ["Cases implemented", "2_4G_RADIO_01–22 and 41–50"],
            ["Pytest marker", "Radio24Scan"],
            ["Test file", "tests/Radio24Scan/test_2_4g_radio.py"],
            ["Scan / DHCP logic", "utils/radio24_scan_flows.py"],
            ["API cases 41–50", "utils/radio24_api_flows.py"],
            ["Expected DHCP client IP", "169.254.254.100"],
        ],
    )

    _h1(doc, "4. How to run")
    _h2(doc, "4.1 Full suite")
    _code(
        doc,
        "python3 -m pytest tests/Radio24Scan/test_2_4g_radio.py \\\n"
        "  -m Radio24Scan --profile default -v",
    )
    _h2(doc, "4.2 By category")
    _table(
        doc,
        ["Category", "Command hint"],
        [
            ["DHCP (07–10)", "./scripts/run_dhcp_cases_until_pass.sh"],
            ["Boot (11–12)", '-m "RADIO_24_11 or RADIO_24_12"'],
            ["API (41–50)", "Add --local-ipv6; PC on CPE mgmt Wi‑Fi"],
            ["Wait for link first", "python3 scripts/wait_link_run_radio24.py"],
        ],
    )

    _h1(doc, "5. What you see when a test runs")
    _p(
        doc,
        "Lines are prefixed with [RADIO_24_SCAN]. Each case prints a summary table: "
        "Wi‑Fi state, SSID, IP address, ping to CPE, and whether internet is blocked on "
        "the management interface (it should be).",
    )

    _h2(doc, "5.1 DHCP (07–10)")
    cases_dhcp = [
        (
            "2_4G_RADIO_07",
            "DHCP disabled",
            "With CPE DHCP off, client must not get mgmt IP.",
            ["Disable dhcp.lan24 on CPE.", "Scan PC tries to join.", "Restore DHCP after test."],
            ["No 169.254.x IP", "Cannot ping CPE mgmt"],
            "No connection without DHCP.",
            "test_radio_24_07_dhcp_server_down_no_ip",
            "assert_radio_24_07_dhcp_server_down_no_ip",
        ),
        (
            "2_4G_RADIO_08",
            "DHCP after reboot",
            "After reboot with DHCP on, client gets expected lease.",
            ["Enable DHCP.", "Reboot CPE.", "Connect hidden SSID."],
            ["Client IP = 169.254.254.100 (or pool match)"],
            "DHCP works after reboot.",
            "test_radio_24_08_dhcp_recovery_after_reboot",
            "assert_radio_24_08_dhcp_recovery_after_reboot",
        ),
        (
            "2_4G_RADIO_09",
            "Wi‑Fi toggle stress",
            "Toggling Wi‑Fi 25× must not break Ethernet; Wi‑Fi auto-reconnects.",
            ["Toggle scan PC Wi‑Fi radio.", "Check WAN port stays up.", "Verify DHCP reconnect."],
            ["LAN up throughout", "Wi‑Fi reconnects with DHCP IP"],
            "Resilient under radio toggling.",
            "test_radio_24_09_dhcp_stress_connect_disconnect",
            "assert_radio_24_09_dhcp_stress_connect_disconnect",
        ),
        (
            "2_4G_RADIO_10",
            "Lease in CPE logs",
            "CPE DHCP lease must match scan PC MAC, IP, and hostname.",
            ["Connect scan PC.", "Read MAC and hostname.", "Compare to CPE dhcp.leases."],
            ["MAC, IP, hostname all match lease file"],
            "Lease correctly recorded on CPE.",
            "test_radio_24_10_dhcp_logs_lease_assignment",
            "assert_radio_24_10_dhcp_logs_lease_assignment",
        ),
    ]
    for c in cases_dhcp:
        _case_block(doc, *c)

    _h2(doc, "5.2 Boot behavior (11–12)")
    _case_block(
        doc,
        "2_4G_RADIO_11",
        "No connect during boot",
        "While CPE is booting, hidden connect attempts must fail.",
        ["Reboot CPE.", "Repeatedly try to connect during ~90 s boot window."],
        ["Connect blocked during boot"],
        "SSID not available until boot completes.",
        "test_radio_24_11_no_ssid_during_boot",
        "assert_radio_24_11_no_ssid_during_boot",
    )
    _case_block(
        doc,
        "2_4G_RADIO_12",
        "Connect after boot",
        "Once CPE is up, hidden connect must succeed.",
        ["Reboot CPE.", "Wait until ready.", "Connect and ping mgmt IP."],
        ["Connect succeeds", "Ping 169.254.254.1 OK"],
        "Management Wi‑Fi available after boot.",
        "test_radio_24_12_ssid_appears_after_boot",
        "assert_radio_24_12_ssid_appears_after_boot",
    )

    _h2(doc, "5.4 Management API (41–50)")
    _p(doc, "Cases 41–50 use the same CPE API client as the main API suite. The automation PC must be on CPE mgmt Wi‑Fi.")
    _table(
        doc,
        ["Case", "What we test", "Pass criteria"],
        [
            ["41", "Valid GET sw-version", "HTTP 200, version in JSON"],
            ["42", "Invalid API path", "400/404; valid API still works"],
            ["43", "Read access on mgmt Wi‑Fi", "GET sw-version succeeds"],
            ["44", "Malformed URL", "404 on bad path; valid GET OK"],
            ["45", "Load test", "100 requests in 60 s, all HTTP 200"],
            ["46", "API after reboot", "SSH + GET sw-version; BTS link up"],
            ["49", "Response time", "Each sample < 500 ms; matches /etc/version"],
            ["50", "API logging", "Request appears in CPE logs"],
        ],
    )
    _p(doc, "Code: utils/radio24_api_flows.py; fixtures from tests/API/conftest.py")

    _h1(doc, "6. Key commands (on scan PC)")
    _table(
        doc,
        ["Command", "Used for"],
        [
            ["iwlist <iface> scan", "Passive SSID scan"],
            ["nmcli device wifi connect … hidden yes", "Join hidden SSID"],
            ["ping -I <iface> 169.254.254.1", "Reach CPE management"],
            ["ping -I <iface> 8.8.8.8", "Confirm no internet on mgmt Wi‑Fi"],
        ],
    )

    out = OUT_DIR / "2.4-GHz-Radio-Test-Suite-Guide.docx"
    doc.save(out)
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [build_api_doc(), build_wireless_security_doc(), build_radio24_doc()]
    for p in paths:
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
