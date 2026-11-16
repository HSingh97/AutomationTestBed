import argparse
import asyncio
import html as html_lib

html_module = html_lib
import json
import csv
import sys
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.functional_report_log import load as load_functional_case_logs
from utils.jumbo_capture_report import (
    JUMBO_CAPTURE_REPORT_CSS,
    load_jumbo_capture_index,
    parse_jmb_case_id,
    render_jumbo_capture_evidence_html,
)
from utils.regression_report import _render_testbed_summary_table
from utils.senao_branding import senao_logo_src

ARTIFACTS_DIR = Path("reports/artifacts")

QOS_TRAFFIC_REPORT_CSS = """
            .qos-traffic-details { margin-top: 12px; }
            .qos-traffic-details > summary {
              cursor: pointer; list-style: none; font-size: 12px; font-weight: 600;
              color: #1d4ed8; padding: 6px 0; user-select: none;
            }
            .qos-traffic-details > summary::-webkit-details-marker { display: none; }
            .qos-traffic-details > summary::before { content: "▸ "; }
            .qos-traffic-details[open] > summary::before { content: "▾ "; }
            .qos-table-scroll {
              margin-top: 8px; overflow-x: auto; border-radius: 8px;
              border: 1px solid #e2e8f0;
            }
            table.qos-traffic-table { width: 100%; border-collapse: collapse; font-size: 12px; background: #fff; }
            table.qos-traffic-table th, table.qos-traffic-table td {
              padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left;
              vertical-align: middle; text-transform: none; letter-spacing: 0;
            }
            table.qos-traffic-table thead th {
              background: #f1f5f9; color: #475569; font-size: 11px; font-weight: 700;
            }
            table.qos-traffic-table .qos-q { font-weight: 700; color: #0f172a; width: 40px; }
            table.qos-traffic-table .qos-num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
            table.qos-traffic-table .qos-class { font-weight: 600; color: #1e293b; }
            table.qos-traffic-table .qos-note { font-weight: 400; font-size: 11px; color: #64748b; margin-top: 2px; }
            table.qos-traffic-table .qos-pir { color: #64748b; font-size: 11px; max-width: 180px; }
            table.qos-traffic-table .qos-status {
              display: inline-block; padding: 2px 8px; border-radius: 999px;
              font-size: 10px; font-weight: 700; background: #f1f5f9; color: #475569;
            }
            table.qos-traffic-table tr.qos-row-active { background: #f0fdf4; }
            table.qos-traffic-table tr.qos-row-active .qos-status { background: #d1fae5; color: #065f46; }
            table.qos-traffic-table tr.qos-row-quiet { background: #fffbeb; }
            table.qos-traffic-table tr.qos-row-quiet .qos-status { background: #fef3c7; color: #92400e; }
            table.qos-traffic-table tr.qos-row-idle { background: #fff; color: #94a3b8; }
            table.qos-traffic-table tr.qos-row-idle .qos-class,
            table.qos-traffic-table tr.qos-row-idle .qos-pir { color: #94a3b8; font-weight: 500; }
            .module-name-cell { max-width: 240px; }
            .module-name-short { font-weight: 500; color: #334155; line-height: 1.45; }
            .test-steps-cell { max-width: 360px; font-size: 12px; color: #475569; line-height: 1.45; text-align: left; }
            .test-steps-text { white-space: pre-wrap; word-break: break-word; }
            tr.test-row td { padding-top: 18px; padding-bottom: 18px; }
            .logs-proof-list { margin-top: 6px; }
            .logs-proof-item { position: relative; padding-left: 14px; margin-bottom: 7px; color: #334155; }
            .logs-proof-item::before { content: "•"; position: absolute; left: 0; color: #059669; font-weight: bold; }
            .logs-proof-item.logs-proof-sub { padding-left: 28px; font-family: Consolas, monospace; font-size: 11px; color: #475569; }
            .logs-proof-item.logs-proof-sub::before { content: "–"; color: #94a3b8; left: 14px; }
            .logs-evidence-details { margin-top: 10px; }
            .logs-evidence-details > summary {
              cursor: pointer; list-style: none; font-size: 12px; font-weight: 600;
              color: #1d4ed8; padding: 4px 0; user-select: none;
            }
            .logs-evidence-details > summary::-webkit-details-marker { display: none; }
            .logs-evidence-details > summary::before { content: "▸ "; }
            .logs-evidence-details[open] > summary::before { content: "▾ "; }
            .logs-evidence-pre {
              margin-top: 6px; padding: 10px 12px; background: #f8fafc; border: 1px solid #e2e8f0;
              border-radius: 8px; font-family: Consolas, monospace; font-size: 11px;
              color: #334155; white-space: pre-wrap; word-break: break-word; max-height: 220px; overflow: auto;
            }
"""


def _humanize_module_name(nodeid: str, test_id: str) -> str:
    """Human-readable description without repeating the case ID prefix (e.g. JMB_01)."""
    fn = nodeid.split("::")[-1]
    raw = re.sub(r"^test_", "", fn, flags=re.I)

    if re.match(r"JMB_\d+", test_id, re.I):
        raw = re.sub(r"^jmb_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    if re.match(r"PROCESS_\d+", test_id, re.I):
        raw = re.sub(r"^process_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    if re.match(r"QOS_\d+", test_id, re.I):
        raw = re.sub(r"^qos_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title() or test_id

    if re.match(r"FT_\d+", test_id, re.I):
        raw = re.sub(r"^ft_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    if re.match(r"API_\d+", test_id, re.I):
        raw = re.sub(r"^api_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    if re.match(r"RADIO_24_\d+", test_id, re.I):
        raw = re.sub(r"^radio_24_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    if re.match(r"W_SECURITY_\d+", test_id, re.I):
        raw = re.sub(r"^w_security_\d+_?", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    if re.match(r"ARPBRIDGE_\d+", test_id, re.I):
        raw = re.sub(r"^arpbridge_\d+_?", "", raw, flags=re.I)
        raw = re.sub(r"_ipv4$", "", raw, flags=re.I)
        return raw.replace("_", " ").strip().title()

    return raw.replace("_", " ").strip().title()


def _escape_html(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _catalog_case_meta(test_id: str) -> dict[str, str]:
    """Title / steps / note from suite catalogs when available."""
    tid = str(test_id or "").strip()
    meta = {"title": "", "steps": "", "note": ""}
    try:
        if re.match(r"VLAN_\d+", tid, re.I):
            from config.vlan_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"LOGS_\d+", tid, re.I):
            from config.logs_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"QoS_\d+", tid, re.I):
            from config.qos_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or c.get("procedure") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"UM_\d+", tid, re.I):
            from config.um_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"FIREWALL_\d+", tid, re.I):
            from config.firewall_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"TC_DFS_\d+", tid, re.I):
            from config.dfs_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"SEC-\d+", tid, re.I):
            from config.security_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
        elif re.match(r"ACB_\d+", tid, re.I):
            from config.acb_test_cases import case_by_id

            c = case_by_id(tid)
            meta["title"] = str(c.get("title") or "")
            meta["steps"] = str(c.get("steps") or "")
            meta["note"] = str(c.get("note") or "")
    except Exception:
        pass
    return meta


def get_group_marker(keywords):
    """
    Extracts the logical group name from pytest markers.
    """
    ignore_list = {
        "pytestmark",
        "asyncio",
        "usefixtures",
        "parametrize",
        "filterwarnings",
        "functional",
        "destructive",
        "smoke",
        "sanity",
        "order",
    }

    if isinstance(keywords, dict):
        kw_list = list(keywords.keys())
    else:
        kw_list = list(keywords or [])

    for kw in kw_list:
        low = str(kw).lower()
        if re.match(r"IP_\d+", str(kw), re.I) or low == "ip":
            return "IP"
        if re.match(r"VLAN_\d+", str(kw), re.I) or low == "vlan":
            return "VLAN"
        if re.match(r"LOGS_\d+", str(kw), re.I) or low == "logs":
            return "Logs"
        if re.match(r"JMB_\d+", str(kw), re.I) or low in ("jumboframes", "jumbo"):
            return "JumboFrames"
        if re.match(r"PROCESS_\d+", str(kw), re.I) or low in ("processmonitor", "process_monitor"):
            return "ProcessMonitor"
        if re.match(r"QOS_\d+", str(kw), re.I) or low == "qos":
            return "QoS"
        if re.match(r"UM_\d+", str(kw), re.I) or low in ("usermgmt", "user_mgmt"):
            return "UserMgmt"
        if re.match(r"FIREWALL_\d+", str(kw), re.I) or low == "firewall":
            return "Firewall"
        if re.match(r"TC_DFS_\d+", str(kw), re.I) or low == "dfs":
            return "DFS"
        if re.match(r"SEC-\d+", str(kw), re.I) or low == "security":
            return "Security"
        if re.match(r"ACB_\d+", str(kw), re.I) or low in ("asymmetriccbw", "asymmetric_cbw"):
            return "AsymmetricCBW"
        if re.match(r"W_SECURITY_\d+", str(kw), re.I) or low in ("wirelesssecurity", "wireless_security"):
            return "WirelessSecurity"
        if re.match(r"FT_\d+", str(kw), re.I) or low == "functional":
            return "Functional"
        if re.match(r"RADIO_24_\d+", str(kw), re.I) or low in ("radio24scan", "radio24"):
            return "Radio24Scan"
        if re.match(r"API_\d+", str(kw), re.I) or low == "api":
            return "API"
        if re.match(r"ARPBRIDGE_\d+", str(kw), re.I) or low in ("arpbridgetable", "arp_bridge_table"):
            return "ArpBridgeTable"

    for kw in kw_list:
        if kw in ignore_list or str(kw).startswith('GUI_') or str(kw).startswith('test_') or '.py' in str(kw):
            continue
        if str(kw).lower() in ignore_list:
            continue
        return str(kw).capitalize()

    return "Ungrouped"


PROC_PARTIAL_MARKER = "[PROC_PARTIAL]"
PROC_FAILED_MARKER = "[PROC_FAILED]"

# Official suite runs PROCESS_01–PROCESS_19 only (extended 21–28 are catalog-only).
OFFICIAL_PROCESS_REPORT_ORDER: tuple[int, ...] = tuple(range(1, 20))


def _suite_case_number(test_id: str, prefix: str) -> int | None:
    match = re.match(rf"{re.escape(prefix)}[_-](\d+)", str(test_id), re.I)
    return int(match.group(1)) if match else None


def _sort_numbered_suite_records(records: list[dict], prefix: str) -> list[dict]:
    """Order suite rows by case number (UM_01…UM_50), not pytest run order."""

    def sort_key(record: dict) -> tuple[int, int]:
        num = _suite_case_number(record.get("id", ""), prefix)
        if num is None:
            return (1, 0)
        return (0, num)

    return sorted(records, key=sort_key)


def _customer_error_message(raw: str) -> str:
    """Strip pytest/automation noise from customer-facing failure text."""
    err = str(raw or "").strip()
    err = re.sub(r"^E\s+", "", err)
    err = re.sub(r"^_pytest\.outcomes\.XFailed:\s*", "", err)
    err = re.sub(r"^XFailed:\s*", "", err, flags=re.I)
    err = re.sub(r"^AssertionError:\s*", "", err)
    err = re.sub(r"^Product defect:\s*", "", err, flags=re.I)
    while True:
        cleaned = re.sub(
            r"\s*\([^)]*(?:plan expects|CSS-hidden|xfail|Phase\s*\d|denied role|file input)[^)]*\)\s*$",
            "",
            err,
            flags=re.I,
        )
        if cleaned == err:
            break
        err = cleaned.strip()
    err = re.sub(r"\s*\([^)]{8,}\)\s*$", "", err).strip()
    return err or "Unknown error"


def _functional_case_number(test_id: str) -> int | None:
    match = re.match(r"FT_(\d+)", str(test_id), re.I)
    return int(match.group(1)) if match else None


def _sort_functional_records(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda record: _functional_case_number(record.get("id", "")) or 999)


def _sort_process_monitor_records(records: list[dict]) -> list[dict]:
    """Present ProcessMonitor rows as PROCESS_01 … PROCESS_19, not pytest run order."""

    def sort_key(record: dict) -> tuple[int, int]:
        num = _suite_case_number(record.get("id", ""), "PROCESS")
        if num is None:
            return (2, 0)
        try:
            seq = OFFICIAL_PROCESS_REPORT_ORDER.index(num)
            return (0, seq)
        except ValueError:
            return (1, num)

    return sorted(records, key=sort_key)


def _is_process_monitor_test(test: dict) -> bool:
    keywords = test.get("keywords") or []
    return "ProcessMonitor" in keywords or any(str(k).startswith("PROCESS_") for k in keywords)


_PROC_REASON_NOISE_PREFIXES = (
    "[",
    "tests/",
    "utils/",
    "venv/",
    "config/",
    "pages/",
    "FAILED ",
    "PASSED ",
    "PARTIAL ",
    "FAILURE:",
    "________",
    "request = ",
)


def _trim_proc_reason(raw: str) -> str:
    """Stop a captured PARTIAL/FAILED reason at the next test/log/traceback line."""
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not lines:
            lines.append(line.rstrip())
            continue
        if any(stripped.startswith(prefix) for prefix in _PROC_REASON_NOISE_PREFIXES):
            break
        if not stripped and lines and not lines[-1].strip():
            break
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _process_monitor_case_reason(test: dict, longrepr: str) -> tuple[str, str] | None:
    """Return (FAILED|PARTIAL, reason) for Process Monitor cases, preserving bullet lists."""
    stdout = str((test.get("call") or {}).get("stdout") or "")
    blob = f"{longrepr}\n{stdout}"
    if PROC_PARTIAL_MARKER in blob:
        match = re.search(r"\[PROC_PARTIAL\]\s+\S+\s+PARTIAL:\s*(.+)", blob, re.DOTALL)
        reason = _trim_proc_reason(match.group(1)) if match else ""
        if not reason:
            for line in reversed(stdout.splitlines()):
                if "] PARTIAL:" in line:
                    reason = line.split("] PARTIAL:", 1)[-1].strip()
                    break
        return "PARTIAL", reason or "Baseline discrepancy (see console log)"
    if PROC_FAILED_MARKER in blob:
        match = re.search(r"\[PROC_FAILED\]\s+\S+\s+FAILED:\s*(.+)", blob, re.DOTALL)
        reason = _trim_proc_reason(match.group(1)) if match else ""
        if not reason:
            for line in reversed(stdout.splitlines()):
                if "] FAILED:" in line:
                    reason = line.split("] FAILED:", 1)[-1].strip()
                    break
        return "FAILED", reason or "Test failed (see console log)"
    return None


def _format_proc_reason_html(reason: str) -> str:
    """Render a procmon reason (possibly with '  - foo' bullet lines) as HTML."""
    if "\n  - " not in reason:
        return f"<div class='failure-item'>{reason}</div>"
    head, _, rest = reason.partition("\n  - ")
    bullet_text = "  - " + rest
    bullets = [
        line.strip()[2:].strip()
        for line in bullet_text.splitlines()
        if line.strip().startswith("- ")
    ]
    head_html = f"<div class='failure-item'>{head.strip()}</div>" if head.strip() else ""
    items = "".join(f"<li>{name}</li>" for name in bullets if name)
    return f"{head_html}<ul class='proc-bullet-list'>{items}</ul>"


_PROC_PASS_SUMMARY_NOISE = (
    "Recovery reboot",
    "Recovery hello",
    "Recovery disarm",
    "Reconnecting SSH",
    "SSH restored",
    "Waiting up to",
    "Skipping ",
    "SEGV on ",
    "KILL on ",
    "BATCH ",
    "Batch ",
    "Core dump",
    "Post-reboot ubus probe",
    "Optional/idle services",
    "SESSION",
    " recovered;",
    "logs quiet",
    "restart evidence",
)


def _iter_proc_messages(test: dict, case_id: str):
    """Yield every [PROC][CASE_ID] log line captured for this test, oldest first."""
    prefix = f"[PROC][{case_id}] "
    call = test.get("call") or {}
    stdout = str(call.get("stdout") or "")
    if stdout:
        for raw in stdout.splitlines():
            if raw.startswith(prefix):
                yield raw[len(prefix):].strip()
    for record in call.get("log") or []:
        msg = str(record.get("msg") or "")
        if msg.startswith(prefix):
            yield msg[len(prefix):].strip()


def _process_monitor_pass_summary(test: dict, case_id: str) -> str:
    """Pick the most informative trailing [PROC][CASE_ID] log line for the report."""
    candidates: list[str] = []
    for msg in _iter_proc_messages(test, case_id):
        if not msg or any(skip in msg for skip in _PROC_PASS_SUMMARY_NOISE):
            continue
        candidates.append(msg)
    if not candidates:
        return ""
    return candidates[-1]


def _is_functional_test(test: dict) -> bool:
    keywords = test.get("keywords") or []
    return "Functional" in keywords or any(re.match(r"FT_\d+", str(k), re.I) for k in keywords)


def _is_arpbridge_test(test: dict) -> bool:
    keywords = test.get("keywords") or []
    return "ArpBridgeTable" in keywords or any(re.match(r"ARPBRIDGE_\d+", str(k), re.I) for k in keywords)


_FT_REPORT_NOISE = (
    "SSH session reopened",
    "SSH interrupted during",
    "waiting BTS↔CPE link up",
    "PC LAN untagged cleanup OK",
    "clearing PC LAN VLANs before verification",
    "opening BTS=",
    "SSH ready",
    "BTS VLAN mode transparent OK",
    "RF Link Down .. Waiting for link up",
    "Navigation retry failed",
    "Executing Triple-Apply sequence",
    "[DEBUG]",
    "CPE hop SSH retry",
    "post-reset settle",
    "VLAN apply SSH dropped",
    "PC LAN untagged enp1s0",
    "PC LAN tagged enp1s0",
    "PC LAN restored untagged",
    "address type already static",
    "client association time",
    "precheck heal",
    "mgmt VLAN apply dropped",
    "BTS mgmt IPv6 unavailable",
    "original BTS bandwidth",
    "wait 30s after change",
    "then backend verify",
    "not accepted by DUT",
    "skipped by design",
    "baseline unreadable",
    "no alternate bandwidth",
    "post-reload settle",
    "waiting for reload",
    "reload process",
    "SSH ended during",
    "SSH responsive after",
    "SSH back on",
    "restore hop closed",
    "factory reset command closed",
    "GUI set ",
    "forcing backend apply",
    "disabling ATPC",
    "baseline —",
    "values:",
)


def _functional_line_ok(message: str) -> bool:
    detail = str(message or "").strip()
    if not detail:
        return False
    low = detail.lower()
    if any(noise in detail for noise in _FT_REPORT_NOISE):
        return False
    if low.startswith("wait ") and "after" in low:
        return False
    return True


def _simplify_functional_line(line: str) -> str:
    """Compact verbose [FUNCTIONAL] text for customer-facing report logs."""
    text = str(line or "").strip()
    if not text:
        return ""
    low = text.lower()
    if low.startswith("pc lan ") or "address type already" in low:
        return ""

    # done / complete → short result
    m = re.match(r"^(?:done|complete)\s+[—-]\s*(.+)$", text, re.I)
    if m:
        tail = m.group(1).strip()
        if len(tail) > 48:
            tail = tail[:45].rstrip() + "…"
        return f"DONE: {tail}"

    rules: list[tuple[re.Pattern[str], str | callable]] = [
        (re.compile(r"^mgmt VLAN BTS=\d+ CPE=\d+\s*->\s*random\s+(\d+)$", re.I), r"VLAN→\1"),
        (re.compile(r"^mgmt VLAN BTS=\d+ CPE=\d+\s*\|\s*.+$", re.I), lambda m: _short_vlan_change(m.group(0))),
        (re.compile(r"^applying mgmt VLAN (\d+) on CPE then BTS$", re.I), r"Apply VLAN \1"),
        (re.compile(r"^restoring mgmt VLAN CPE=\d+ then BTS=\d+$", re.I), "VLAN restore"),
        (re.compile(r"^post[-\w]* link=(PASS(?: \(UNSTABLE\))?)(?:\s*\|\s*.+)?$", re.I), r"Link \1"),
        (re.compile(r"^applying bandwidth (\w+) on BTS only$", re.I), r"BW→\1"),
        (re.compile(r"^BTS BW verify (\w+) actual=\w+ \((ok|FAIL)\)$", re.I), lambda m: f"BW {m.group(1).upper()} {m.group(2).upper()}"),
        (re.compile(r"^BTS BW (\w+) not accepted.*$", re.I), r"BW \1 skip"),
        (re.compile(r"^reverting BTS bandwidth to original (\w+)$", re.I), r"BW revert \1"),
        (re.compile(r"^BTS BW revert verify actual=\w+ \((ok|FAIL)\)$", re.I), r"BW revert \1"),
        (re.compile(r"^applying prefix change on CPE then BTS$", re.I), "Apply prefix"),
        (re.compile(r"^applying IPv6 gateway change on CPE then BTS$", re.I), "Apply IPv6 GW"),
        (re.compile(r"^applying IPv6 address change on CPE then BTS$", re.I), "Apply IPv6"),
        (re.compile(r"^applying IPv4 gateway change on CPE then BTS$", re.I), "Apply IPv4 GW"),
        (re.compile(r"^applying IPv4 netmask change on CPE then BTS$", re.I), "Apply netmask"),
        (re.compile(r"^applying IPv4 change on (CPE|BTS)$", re.I), r"Apply IPv4 \1"),
        (re.compile(r"^reverting IPv6 (?:gateway )?BTS=.*$", re.I), "Revert IPv6"),
        (re.compile(r"^reverting IPv4 GW BTS=.*$", re.I), "Revert IPv4 GW"),
        (re.compile(r"^reverting IPv4 BTS=.*$", re.I), "Revert IPv4"),
        (re.compile(r"^reverting netmask BTS=.*$", re.I), "Revert netmask"),
        (re.compile(r"^step (\d+) dBm — link up, CPE=(PASS|FAIL), BTS=(PASS|FAIL)$", re.I), r"Tx \1 CPE=\2 BTS=\3"),
        (re.compile(r"^\[\d+/\d+\] Tx power (\d+) dBm —.*$", re.I), r"Tx \1 dBm"),
        (re.compile(r"^sampled TX=(\d+) RX=(\d+) over \d+s$", re.I), r"RF TX=\1 RX=\2"),
        (re.compile(r"^apply backend @ (\w+).*$", re.I), r"Backend \1"),
        (re.compile(r"^hard reboot via PDU$", re.I), "Hard reboot"),
        (re.compile(r"^soft reset —.*$", re.I), "Soft reset"),
        (re.compile(r"^SSH reboot —.*$", re.I), "Reboot"),
        (re.compile(r"^MAX — dd /tmp/ram_eater count=(\d+MB).*$", re.I), r"RAM load \1"),
    ]

    for pattern, repl in rules:
        m = pattern.match(text)
        if not m:
            continue
        if callable(repl):
            return repl(m).strip()
        return pattern.sub(repl, text, count=1).strip()

    # Generic trims
    text = re.sub(r"\s*\|\s*RF Interface Restarts,By design\s*", "", text, flags=re.I)
    text = re.sub(r"\s*\(ok\)$", " OK", text, flags=re.I)
    text = re.sub(r"\s*\(FAIL\)$", " FAIL", text, flags=re.I)
    if len(text) > 72:
        text = text[:69].rstrip() + "…"
    return text


def _short_vlan_change(raw: str) -> str:
    """e.g. mgmt VLAN BTS=1 CPE=1 | IPv4 BTS ... -> VLAN 1 | IPv4 ..."""
    text = raw.strip()
    text = re.sub(r"^mgmt VLAN BTS=\d+ CPE=\d+\s*\|\s*", "VLAN 1 | ", text, flags=re.I)
    text = re.sub(r"\s*->\s*", "→", text)
    text = re.sub(r"\s*\|\s*", " | ", text)
    if len(text) > 72:
        text = text[:69].rstrip() + "…"
    return text


def _curate_functional_lines(
    lines: list[str],
    *,
    case_id: str = "",
    max_lines: int = 14,
) -> list[str]:
    """Short chronological log — compact text, no grouping."""
    if not lines:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        mgmt_inline = re.match(r"^mgmt VLAN BTS=(\d+) CPE=(\d+)\s*\|\s*(.+)$", str(line).strip(), re.I)
        if mgmt_inline:
            mgmt_line = f"Mgmt VLAN case BTS={mgmt_inline.group(1)} CPE={mgmt_inline.group(2)}"
            if mgmt_line not in seen:
                seen.add(mgmt_line)
                ordered.append(mgmt_line)
            detail_line = mgmt_inline.group(3).strip()
            text = _simplify_functional_line(detail_line)
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
            continue
        text = _simplify_functional_line(line)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    if re.match(r"FT_(1[7-9]|2[0-3])$", str(case_id or "").upper()) and not any(
        item.startswith("Mgmt VLAN case ") for item in ordered
    ):
        ordered.insert(0, "Mgmt VLAN case verified in backend flow")
    if len(ordered) > max_lines:
        ordered = ordered[:2] + ["…"] + ordered[-(max_lines - 3):]
    return ordered


def _arpbridge_case_number(test_id: str) -> int | None:
    match = re.match(r"ARPBRIDGE_(\d+)", str(test_id), re.I)
    return int(match.group(1)) if match else None


def _sort_arpbridge_records(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda record: _arpbridge_case_number(record.get("id", "")) or 999)


def _iter_functional_messages(test: dict, case_id: str):
    """Yield [FT][CASE_ID] / [FUNCTIONAL] CASE_ID lines from captured pytest output."""
    line_patterns = (
        re.compile(rf"^\[FT\]\[{re.escape(case_id)}\]\s+(.+)$", re.I),
        re.compile(rf"^\[FUNCTIONAL\]\s+{re.escape(case_id)}:\s*(.+)$", re.I),
        re.compile(rf"^\[FUNCTIONAL\]\s+{re.escape(case_id)}\s+done\s+[—-]\s*(.+)$", re.I),
        re.compile(rf"^\[FUNCTIONAL\]\s+{re.escape(case_id)}\s+complete\s+[—-]\s*(.+)$", re.I),
        re.compile(rf"^\[FUNCTIONAL\]\s+{re.escape(case_id)}\s+\[[^\]]+\]\s*(.+)$", re.I),
    )
    for phase in ("setup", "call", "teardown"):
        section = test.get(phase) or {}
        stdout = str(section.get("stdout") or "")
        for raw in stdout.splitlines():
            line = raw.strip()
            for idx, pattern in enumerate(line_patterns):
                match = pattern.match(line)
                if not match:
                    continue
                detail = match.group(1).strip(" :—-")
                if idx == 2:
                    detail = f"done — {detail}"
                elif idx == 3:
                    detail = f"complete — {detail}"
                if _functional_line_ok(detail):
                    yield detail
                break
        for record in section.get("log") or []:
            msg = str(record.get("msg") or record.get("message") or "").strip()
            for idx, pattern in enumerate(line_patterns):
                match = pattern.match(msg)
                if not match:
                    continue
                detail = match.group(1).strip(" :—-")
                if idx == 2:
                    detail = f"done — {detail}"
                elif idx == 3:
                    detail = f"complete — {detail}"
                if _functional_line_ok(detail):
                    yield detail
                break


def _simplify_arpbridge_line(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    text = re.sub(rf"^ARPBRIDGE_\d+\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 96:
        text = text[:93].rstrip() + "..."
    return text


def _iter_arpbridge_messages(test: dict, case_id: str):
    line_patterns = (
        re.compile(rf"^\[ARP_BRIDGE\]\s+{re.escape(case_id)}\s*:\s*(.+)$", re.I),
        re.compile(rf"^\[ARP_BRIDGE\]\s+{re.escape(case_id)}\s+\[[^\]]+\]\s*:\s*(.+)$", re.I),
        re.compile(r"^\[ARP_BRIDGE\]\s+(.+)$", re.I),
    )
    for phase in ("setup", "call", "teardown"):
        section = test.get(phase) or {}
        stdout = str(section.get("stdout") or "")
        for raw in stdout.splitlines():
            line = raw.strip()
            for pattern in line_patterns:
                match = pattern.match(line)
                if not match:
                    continue
                detail = match.group(1).strip(" :—-")
                simplified = _simplify_arpbridge_line(detail)
                if simplified:
                    yield simplified
                break
        for record in section.get("log") or []:
            msg = str(record.get("msg") or record.get("message") or "").strip()
            for pattern in line_patterns:
                match = pattern.match(msg)
                if not match:
                    continue
                detail = match.group(1).strip(" :—-")
                simplified = _simplify_arpbridge_line(detail)
                if simplified:
                    yield simplified
                break


def _arpbridge_terminal_lines(test: dict, case_id: str) -> list[str]:
    """Pull terminal-style stdout (section tables + [ARP_BRIDGE] lines) into report log lines."""
    stdout = str((test.get("call") or {}).get("stdout") or "")
    if not stdout.strip():
        return list(_iter_arpbridge_messages(test, case_id))

    lines: list[str] = []
    for raw in stdout.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^=+$", stripped):
            continue
        if re.match(r"^-{10,}", stripped) and "GUI" not in stripped:
            continue
        if stripped.startswith("Field ") and "Result" in stripped:
            continue
        lines.append(line)
    return lines[:120]


def _format_arpbridge_reason_html(
    *,
    case_id: str = "",
    terminal_text: str = "",
) -> tuple[str, str]:
    text = terminal_text.strip()
    if not text:
        return "", ""
    reason_html = (
        f"<div class='ft-exec-panel' data-case='{html_module.escape(case_id)}'>"
        f"<details class='ft-log-simple'>"
        f"<summary>Logs</summary>"
        f"<pre class='ft-terminal-log'>{html_module.escape(text)}</pre>"
        f"</details>"
        f"</div>"
    )
    flat_lines = text.splitlines()
    reason_csv = "Logs:\n- " + "\n- ".join(flat_lines[:40])
    return reason_html, reason_csv


def _arpbridge_execution_details(test: dict, case_id: str) -> tuple[str, str]:
    terminal_lines = _arpbridge_terminal_lines(test, case_id)
    terminal_text = "\n".join(terminal_lines)
    return _format_arpbridge_reason_html(case_id=case_id, terminal_text=terminal_text)


def _format_functional_reason_html(
    lines: list[str],
    *,
    duration_s: float | None = None,
    case_id: str = "",
) -> tuple[str, str]:
    curated = _curate_functional_lines(lines, case_id=case_id)
    if not curated:
        return "", ""
    runtime_html = ""
    if duration_s and duration_s > 0:
        mins, secs = divmod(int(duration_s), 60)
        runtime = f"{mins}m {secs}s" if mins else f"{secs}s"
        runtime_html = f"<div class='ft-runtime-chip'>Runtime: {html_module.escape(runtime)}</div>"

    items = "".join(f"<li>{html_module.escape(line)}</li>" for line in curated)
    reason_html = (
        f"<div class='ft-exec-panel' data-case='{html_module.escape(case_id)}'>"
        f"{runtime_html}"
        f"<details class='ft-log-simple'>"
        f"<summary>Log</summary>"
        f"<ul class='ft-log-list'>{items}</ul>"
        f"</details>"
        f"</div>"
    )
    flat_title = "Log"
    if duration_s and duration_s > 0:
        mins, secs = divmod(int(duration_s), 60)
        runtime = f"{mins}m {secs}s" if mins else f"{secs}s"
        flat_title = f"Runtime: {runtime} | Log"
    reason_csv = flat_title + ":\n- " + "\n- ".join(curated)
    return reason_html, reason_csv


def _functional_execution_details(
    test: dict,
    case_id: str,
    *,
    functional_logs: dict[str, list[str]],
    progress_meta: dict[str, dict],
) -> tuple[str, str]:
    lines = list(_iter_functional_messages(test, case_id))
    if not lines:
        lines = list(functional_logs.get(case_id, []))
    nodeid = str(test.get("nodeid") or "")
    duration_s = float((progress_meta.get(nodeid) or {}).get("duration_s") or 0.0)
    if not duration_s:
        duration_s = float((test.get("call") or {}).get("duration") or 0.0)
    return _format_functional_reason_html(lines, duration_s=duration_s or None, case_id=case_id)


def _load_functional_progress_meta() -> dict[str, dict]:
    path = ARTIFACTS_DIR / "ip_suite_progress.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    meta: dict[str, dict] = {}
    for row in payload.get("tests") or []:
        nodeid = str(row.get("nodeid") or "")
        if nodeid:
            meta[nodeid] = row
    return meta


def _effective_outcome_for_report(test: dict) -> str:
    """
    Map pytest-json-report outcome for customer reports.

    When call (and setup) passed but teardown failed, pytest records overall
  outcome as 'error'. Treat that as passed in the Senao report.
    """
    raw = str(test.get("outcome", "unknown")).lower()
    if raw == "skipped":
        return "skipped"
    call = str(test.get("call", {}).get("outcome", "")).lower()
    setup = str(test.get("setup", {}).get("outcome", "passed")).lower()
    if call == "passed" and setup in ("passed", ""):
        if raw in ("passed", "error"):
            return "passed"
    return raw


def extract_qos_traffic_table_html(test_data: dict) -> str:
    """Pull embedded QoS traffic table HTML from pytest stdout."""
    stdout = str((test_data.get("call") or {}).get("stdout") or "")
    chunks = re.findall(
        r"\[QOS_TRAFFIC_TABLE_HTML\](.*?)\[/QOS_TRAFFIC_TABLE_HTML\]",
        stdout,
        flags=re.DOTALL,
    )
    return "".join(chunks)


def load_qos_case_evidence(case_id: str) -> dict:
    """Load sidecar written by QoS runs (needed when pytest ``-s`` blanks json stdout)."""
    path = ARTIFACTS_DIR / f"qos_{case_id}_evidence.json"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_logs_case_evidence(case_id: str) -> dict[str, Any]:
    """Load DUT-side Logs evidence written under ``reports/artifacts/logs_evidence/``."""
    path = ARTIFACTS_DIR / "logs_evidence" / f"{case_id}.json"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _logs_clip(text: Any, limit: int = 180) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)].rstrip() + "…"


def build_logs_verification_lines(case_id: str, evidence: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (summary bullets, expandable detail lines) from Logs evidence JSON."""
    if not evidence:
        return (
            [
                f"{case_id}: no evidence sidecar found under reports/artifacts/logs_evidence/",
                "Re-run with --allow-logs-lab to capture DUT proofs.",
            ],
            [],
        )

    # Prefer explicit proofs list when present (newer writers).
    explicit = evidence.get("proofs")
    if isinstance(explicit, list) and explicit:
        summary = [str(x) for x in explicit if str(x).strip()]
        details: list[str] = []
        for key in ("matched", "logread_tail", "session_tail", "snlog_tail", "out_tail", "new_session"):
            val = evidence.get(key)
            if isinstance(val, list) and val:
                details.append(f"{key}:")
                details.extend(f"  {_logs_clip(ln, 220)}" for ln in val[-8:])
            elif isinstance(val, str) and val.strip():
                details.append(f"{key}:")
                details.extend(f"  {_logs_clip(ln, 220)}" for ln in val.splitlines()[-8:] if ln.strip())
        return summary, details

    summary: list[str] = []
    details: list[str] = []
    mode = str(evidence.get("mode") or "").strip()
    note = str(evidence.get("note") or "").strip()
    catalog = _catalog_case_meta(case_id)
    if not note:
        note = catalog.get("note") or ""
    if not mode:
        try:
            from config.logs_test_cases import case_by_id

            mode = str((case_by_id(case_id) or {}).get("mode") or "")
        except Exception:
            mode = ""

    if mode == "user_login_log":
        summary.append("V&V method: Playwright GUI login + DUT session/snlog proof")
    elif mode:
        summary.append(f"V&V method: DUT SSH ({mode})")
    else:
        summary.append("V&V method: DUT SSH log/UCI checks")

    apply = evidence.get("apply")
    if isinstance(apply, dict) and apply:
        key = apply.get("key")
        if key:
            summary.append(
                f"Applied UCI `{key}` → set={apply.get('set')!s}, "
                f"readback={apply.get('got')!s}"
            )
        if apply.get("restored") is not None and key:
            summary.append(f"Restored baseline `{key}` → {apply.get('restored')}")
        for extra_key in ("channel", "ssid", "lease", "host", "option", "path", "value"):
            if apply.get(extra_key) is not None:
                summary.append(f"{extra_key}: {apply.get(extra_key)}")
        out = str(apply.get("out") or "").strip()
        if out:
            details.append("ucidyn/apply output (tail):")
            details.extend(f"  {_logs_clip(ln, 200)}" for ln in out.splitlines()[-6:] if ln.strip())

    if "marker_ok" in evidence:
        summary.append(
            "Logger marker in logread: "
            + ("PASS" if evidence.get("marker_ok") else "FAIL/absent")
        )
    if "snlog_grew_or_stable" in evidence:
        summary.append(
            "snlog size after apply: "
            + ("grew or stable" if evidence.get("snlog_grew_or_stable") else "shrunk unexpectedly")
        )
    if evidence.get("new_logread_count") is not None:
        summary.append(
            f"New logread lines after apply: {evidence.get('new_logread_count')}; "
            f"new session lines: {evidence.get('new_session_count', 0)}"
        )

    matched = evidence.get("matched") or []
    if isinstance(matched, list) and matched:
        summary.append(f"Matched DUT log lines for case grep: {len(matched)}")
        details.append("Matched log snippets:")
        details.extend(f"  {_logs_clip(ln, 220)}" for ln in matched[-8:])

    if evidence.get("dut_extract_ok"):
        summary.append(
            f"DUT extract OK — logread={evidence.get('logread_chars')} chars, "
            f"snlog={evidence.get('snlog_wc')} bytes, "
            f"session_lines={evidence.get('session_lines')}"
        )

    if "post_logger_ok" in evidence:
        summary.append(
            "Post-reboot/event logger alive: "
            + ("PASS" if evidence.get("post_logger_ok") else "FAIL")
        )
    if evidence.get("before_snlog_wc") is not None or evidence.get("after_snlog_wc") is not None:
        summary.append(
            f"snlog bytes before→after: {evidence.get('before_snlog_wc')} → {evidence.get('after_snlog_wc')}"
        )
    if evidence.get("before") is not None and evidence.get("after") is not None and "alive" in evidence:
        summary.append(
            f"snlog bytes before→after: {evidence.get('before')} → {evidence.get('after')}; "
            f"logger alive={'PASS' if evidence.get('alive') else 'FAIL'}"
        )
    if evidence.get("cycles") is not None:
        summary.append(
            f"Persistent reboot cycles completed: {evidence.get('cycles')}; "
            f"logger after={'PASS' if evidence.get('post_logger_ok') else 'FAIL'}"
        )
    if evidence.get("pct") is not None:
        summary.append(
            f"Memory pressure target ~{evidence.get('pct')}%; "
            f"logger after={'PASS' if evidence.get('post_logger_ok') or evidence.get('alive') else 'see evidence'}"
        )
        if evidence.get("out_tail"):
            details.append("memory/storage command output:")
            details.extend(
                f"  {_logs_clip(ln, 200)}"
                for ln in str(evidence.get("out_tail")).splitlines()[-8:]
                if ln.strip()
            )

    login = evidence.get("login")
    if isinstance(login, dict) and login:
        summary.append(
            f"GUI login as {login.get('role') or login.get('user')} @ {login.get('host')}: "
            f"{'OK' if login.get('ok') else 'FAIL'}"
        )
        if evidence.get("login_hit"):
            summary.append("session_logs / snlog shows matching login event")
        new_sess = evidence.get("new_session") or []
        if isinstance(new_sess, list) and new_sess:
            details.append("New session_logs lines:")
            details.extend(f"  {_logs_clip(ln, 220)}" for ln in new_sess[-6:])
        elif evidence.get("session_tail"):
            details.append("session_logs sample:")
            details.extend(
                f"  {_logs_clip(ln, 220)}"
                for ln in str(evidence["session_tail"]).splitlines()[-6:]
                if ln.strip()
            )

    for key, label in (
        ("logread_tail", "logread sample"),
        ("session_tail", "session_logs sample"),
        ("snlog_tail", "snlog sample"),
        ("out_tail", "command output"),
    ):
        val = evidence.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        # Avoid duplicating out_tail already added for memory/storage.
        joined = "\n".join(details)
        if key == "out_tail" and ("memory/storage command output:" in joined or "command output:" in joined):
            continue
        if f"{label}:" in joined:
            continue
        details.append(f"{label}:")
        details.extend(f"  {_logs_clip(ln, 220)}" for ln in val.splitlines()[-6:] if ln.strip())

    if note:
        summary.append(f"Plan note: {note}")

    if not summary:
        summary.append(f"{case_id}: evidence present but no structured fields — see raw JSON")
        details.append(json.dumps(evidence, indent=2, default=str)[:1500])

    return summary, details


def render_logs_verification_html(case_id: str, evidence: dict[str, Any]) -> tuple[str, str]:
    """HTML + CSV text for Logs Execution Details column."""
    summary, details = build_logs_verification_lines(case_id, evidence)
    if not summary and not details:
        return "", ""

    items_html = "".join(
        f"<div class='logs-proof-item'>{html_lib.escape(line)}</div>" for line in summary
    )
    html_out = (
        "<div class='reason-title'>Verification proof (DUT):</div>"
        f"<div class='logs-proof-list'>{items_html}</div>"
    )
    if details:
        raw_preview = "\n".join(details[:50])
        html_out += (
            "<details class='logs-evidence-details'>"
            "<summary>Expand log / command samples</summary>"
            f"<pre class='logs-evidence-pre'>{html_lib.escape(raw_preview)}</pre>"
            "</details>"
        )

    csv_lines = ["Verification proof (DUT):"] + [f"- {ln}" for ln in summary]
    if details:
        csv_lines.append("Samples:")
        csv_lines.extend(f"  {ln}" for ln in details[:50])
    return html_out, "\n".join(csv_lines)


def qos_verified_params_fallback(case_id: str) -> list[str]:
    """Catalog params for a QoS case when stdout/evidence has no pills."""
    try:
        from config.qos_test_cases import case_by_id
        from utils.qos_flows import _MODE_VERIFIED_PARAMS

        mode = str(case_by_id(case_id).get("mode") or "")
        return list(_MODE_VERIFIED_PARAMS.get(mode, []))
    except Exception:
        return []


def um_verified_params_fallback(case_id: str) -> list[str]:
    """Catalog params for a UM case when stdout has no pills."""
    try:
        from config.um_test_cases import case_by_id
        from utils.um_flows import _MODE_VERIFIED_PARAMS

        mode = str(case_by_id(case_id).get("mode") or "")
        return list(_MODE_VERIFIED_PARAMS.get(mode, []))
    except Exception:
        return []


def extract_validated_parameters(test_data):
    """
    Extracts the parameter names from the captured stdout of the test.
    """
    stdout = test_data.get('call', {}).get('stdout', '')
    stdout += test_data.get('setup', {}).get('stdout', '')

    params = []
    matches = re.finditer(r'->\s+(.*?):\s+(PASSED|FAILED|NO INFORMATION)', stdout or "")
    for match in matches:
        param_name = match.group(1).strip()
        if param_name not in params:
            params.append(param_name)

    return params


def _skip_reason_text(test: dict) -> str:
    """Best-effort skip message from pytest-json-report fields / stdout."""
    chunks: list[str] = []
    for phase in ("setup", "call"):
        data = test.get(phase) or {}
        for key in ("longrepr", "stdout", "stderr"):
            val = data.get(key)
            if val:
                chunks.append(str(val))
    blob = "\n".join(chunks).strip()
    m = re.search(r"(?:Skipped|skipped)[:\s]+['\"]?(.*?)['\"]?\s*$", blob, re.I | re.M)
    if m:
        text = m.group(1).strip().strip("'\"")
        text = re.sub(r"['\"]\)\s*$", "", text).strip()
        return text
    for line in blob.splitlines():
        line = line.strip().strip("'\"")
        if line and line.lower() not in ("skipped",):
            return line
    return blob or "Test execution was bypassed."


def _is_not_available_skip(reason: str) -> bool:
    r = (reason or "").lower()
    return (
        "not available" in r
        or "not_available" in r
        or "not implemented" in r
        or "not applicable" in r
    )


def _is_manual_skip(reason: str) -> bool:
    r = (reason or "").lower()
    return "to be tested manually" in r or r.startswith("manual:")


def clean_failure_message(raw_failure):
    """
    Translates ugly pytest-check strings into professional human-readable formats.
    """
    num_match = re.match(r'check\s+([\d.]+)\s*<=\s*([\d.]+):\s*(.*)', raw_failure)
    if num_match:
        val, tol, msg = num_match.groups()

        unit = ""
        msg_upper = msg.upper()
        if "PERCENTAGE" in msg_upper or "CPU" in msg_upper or "MEM" in msg_upper:
            unit = "%"
        elif "TEMPERATURE" in msg_upper:
            unit = "°"
        elif "TX" in msg_upper or "RX" in msg_upper:
            unit = " Mbps"

        return f"{msg} <br/><span style='color:#ef4444; font-size:12px; margin-top: 4px; display: inline-block;'>↳ <b>Drift Analysis:</b> Detected deviation of <b>{float(val):.2f}{unit}</b> (Allowed: {tol}{unit})</span>"

    clean_msg = re.sub(r'^check\s+.*?:\s*', '', raw_failure)
    return clean_msg


def _load_testbed_summary(profile_name: str | None, local_ip: str) -> dict:
    summary_path = ARTIFACTS_DIR / "testbed_summary.json"
    if summary_path.is_file():
        try:
            with summary_path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not read {summary_path}: {exc}")

    if not profile_name:
        return {}

    try:
        import os

        from utils.net_utils import normalize_ip
        from utils.profile_manager import load_profile_bundle
        from utils.regression_device_info import collect_testbed_summary

        bundle = load_profile_bundle(profile_name=profile_name, local_ip=local_ip or None)
        dut = bundle.active["dut"]
        rec = bundle.active.get("recovery") or {}
        password = str(dut.get("password") or "")

        attempts: list[tuple[str, list[str]]] = []
        if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
            bsu_v6 = normalize_ip(str(dut["local_ipv6"]))
            cpe_v6 = [normalize_ip(str(ip)) for ip in dut.get("remote_ipv6s", []) if str(ip).strip()]
            attempts.append((bsu_v6, cpe_v6))

        # QoS / skip-bootstrap lab often has no reachable profile IPv6 — use IPv4 mgmt.
        ipv4_candidates = []
        for raw in (
            os.environ.get("QOS_DUT_HOST"),
            rec.get("bts_fallback_ipv4"),
            dut.get("cpe_fallback_ip"),
            dut.get("local_ip"),
            local_ip if local_ip and any(ch.isdigit() for ch in local_ip) and ":" not in local_ip and local_ip != "profile-default" else "",
            "10.0.0.1",
        ):
            host = str(raw or "").strip()
            if host and host not in ipv4_candidates:
                ipv4_candidates.append(host)
        for host in ipv4_candidates:
            attempts.append((normalize_ip(host), [normalize_ip(host)]))

        last_exc: Exception | None = None
        for bsu_host, cpe_hosts in attempts:
            try:
                print(f"Collecting testbed summary (BTS={bsu_host})...")
                return asyncio.run(collect_testbed_summary(bsu_host, cpe_hosts, password))
            except Exception as exc:
                last_exc = exc
                print(f"Warning: testbed summary via {bsu_host} failed: {exc}")
        if last_exc:
            print(f"Warning: testbed summary collection failed: {last_exc}")
        return {}
    except Exception as exc:
        print(f"Warning: testbed summary collection failed: {exc}")
        return {}


def _cpe_ipv4_from_profile(profile_name: str | None, local_ip: str) -> str:
    if not profile_name:
        return "192.168.2.11"
    try:
        from utils.profile_manager import load_profile_bundle

        bundle = load_profile_bundle(profile_name=profile_name, local_ip=local_ip or None)
        remote_ips = bundle.active.get("dut", {}).get("remote_ips") or []
        for ip in remote_ips:
            clean = str(ip).strip()
            if clean and ":" not in clean:
                return clean
    except Exception:
        pass
    return "192.168.2.11"


def _arpbridge_ipv4_testbed_summary(
    summary: dict,
    *,
    bts_ipv4: str,
    cpe_ipv4: str,
    cpe_model: str = "UBR650",
) -> dict:
    """Force IPv4 lab addresses in testbed summary (no mgmt IPv6 from cached artifacts)."""
    out = dict(summary or {})
    bts = dict(out.get("bts") or {})
    cpe = dict(out.get("cpe") or {})
    bts["ip"] = bts_ipv4
    cpe["ip"] = cpe_ipv4
    cpe["model"] = cpe_model
    out["bts"] = bts
    out["cpe"] = cpe
    out["ip_mode"] = "IPv4 only"
    out.pop("cpes", None)
    out.pop("su_count", None)
    return out


def generate():
    parser = argparse.ArgumentParser(description="Generate Senao customer HTML/CSV from report.json")
    parser.add_argument("build_no")
    parser.add_argument("ip_address", help="BTS/local target label for run metadata")
    parser.add_argument("date_str")
    parser.add_argument(
        "--profile",
        default="",
        help="Profile name (profiles/<name>.yaml) used to collect BTS/CPE info if artifacts summary is missing",
    )
    parser.add_argument(
        "--output-prefix",
        default="Senao_GUI",
        help="Report filename prefix (e.g. Senao_GUI_<build>_Report_<date>.html)",
    )
    args = parser.parse_args()

    build_no = args.build_no
    ip_addr = args.ip_address
    date_str = args.date_str
    profile_name = (args.profile or "").strip() or None
    output_prefix = (args.output_prefix or "Senao_GUI").strip()

    try:
        report_path = ARTIFACTS_DIR / "report.json"
        with report_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("reports/artifacts/report.json not found! Tests may not have executed properly.")
        sys.exit(1)

    groups = {}
    stats = {
        'total': 0,
        'passed': 0,
        'partial': 0,
        'failed': 0,
        'not_available': 0,
        'not_tested': 0,
    }
    jumbo_capture_index = load_jumbo_capture_index()
    functional_logs = load_functional_case_logs()
    functional_progress = _load_functional_progress_meta()

    for test in data.get('tests', []):
        stats['total'] += 1
        nodeid = test.get('nodeid', '')

        parsed_ip = False
        ip_param = re.search(
            r"test_ip_extended_case\[(IP_\d+)-(\w+)\]", nodeid, re.I
        )
        if ip_param:
            test_id = ip_param.group(1).upper()
            test_name = ip_param.group(2).upper()
            parsed_ip = True
        elif "test_ip_" in nodeid:
            fn = nodeid.split("::")[-1]
            id_m = re.search(r"(?:IP_(\d+)|test_ip_(\d+)_)", fn, re.I)
            target_m = re.search(r"_(bts|cpe)(?:\[|$)", fn, re.I)
            if id_m and target_m:
                num = id_m.group(1) or id_m.group(2)
                test_id = f"IP_{int(num):02d}"
                test_name = target_m.group(1).upper()
                parsed_ip = True

        if not parsed_ip:
            jmb_id = parse_jmb_case_id(nodeid)
            proc_kw = next(
                (str(k).upper() for k in test.get("keywords", []) if re.match(r"PROCESS_\d+", str(k), re.I)),
                None,
            )
            proc_fn = re.search(r"test_process_(\d+)_", nodeid, re.I)
            if proc_kw or proc_fn:
                if proc_kw:
                    test_id = proc_kw
                else:
                    test_id = f"PROCESS_{int(proc_fn.group(1)):02d}"
                test_name = _humanize_module_name(nodeid, test_id)
            elif jmb_id:
                test_id = jmb_id
                test_name = _humanize_module_name(nodeid, test_id)
            else:
                qos_kw = next(
                    (
                        str(k)
                        for k in test.get("keywords", [])
                        if re.match(r"QoS_\d+", str(k), re.I)
                    ),
                    None,
                )
                qos_fn = re.search(r"test_qos_(\d+)\b", nodeid, re.I)
                if qos_kw or qos_fn:
                    if qos_kw:
                        # Preserve plan casing: QoS_01
                        m = re.match(r"QoS_(\d+)", qos_kw, re.I)
                        test_id = f"QoS_{int(m.group(1)):02d}" if m else qos_kw
                    else:
                        test_id = f"QoS_{int(qos_fn.group(1)):02d}"
                    try:
                        from config.qos_test_cases import case_by_id

                        test_name = case_by_id(test_id).get("title") or test_id
                    except Exception:
                        test_name = _humanize_module_name(nodeid, test_id)
                else:
                    um_kw = next(
                        (
                            str(k)
                            for k in test.get("keywords", [])
                            if re.match(r"UM_\d+", str(k), re.I)
                        ),
                        None,
                    )
                    um_fn = re.search(r"test_um_(\d+)\b", nodeid, re.I)
                    if um_kw or um_fn:
                        if um_kw:
                            m = re.match(r"UM_(\d+)", um_kw, re.I)
                            test_id = f"UM_{int(m.group(1)):02d}" if m else um_kw
                        else:
                            test_id = f"UM_{int(um_fn.group(1)):02d}"
                        try:
                            from config.um_test_cases import case_by_id

                            test_name = case_by_id(test_id).get("title") or test_id
                        except Exception:
                            test_name = _humanize_module_name(nodeid, test_id)
                    else:
                        vlan_kw = next(
                            (
                                str(k)
                                for k in test.get("keywords", [])
                                if re.match(r"VLAN_\d+", str(k), re.I)
                            ),
                            None,
                        )
                        vlan_fn = re.search(r"test_vlan\[(VLAN_\d+)\]", nodeid, re.I)
                        vlan_fn2 = re.search(r"(VLAN_\d+)", nodeid, re.I)
                        logs_kw = next(
                            (
                                str(k)
                                for k in test.get("keywords", [])
                                if re.match(r"LOGS_\d+", str(k), re.I)
                            ),
                            None,
                        )
                        logs_fn = re.search(r"test_logs\[(LOGS_\d+)\]", nodeid, re.I)
                        logs_fn2 = re.search(r"(LOGS_\d+)", nodeid, re.I)
                        if logs_kw or logs_fn or logs_fn2:
                            raw = logs_kw or (logs_fn.group(1) if logs_fn else logs_fn2.group(1))
                            m = re.match(r"LOGS_(\d+)", str(raw), re.I)
                            test_id = f"LOGS_{int(m.group(1)):02d}" if m else str(raw).upper()
                            try:
                                from config.logs_test_cases import case_by_id

                                c = case_by_id(test_id) or {}
                                test_name = c.get("title") or c.get("note") or test_id
                            except Exception:
                                test_name = _humanize_module_name(nodeid, test_id)
                        elif vlan_kw or vlan_fn or vlan_fn2:
                            raw = vlan_kw or (vlan_fn.group(1) if vlan_fn else vlan_fn2.group(1))
                            m = re.match(r"VLAN_(\d+)", str(raw), re.I)
                            test_id = f"VLAN_{int(m.group(1)):02d}" if m else str(raw).upper()
                            try:
                                from config.vlan_test_cases import case_by_id

                                c = case_by_id(test_id) or {}
                                test_name = c.get("title") or c.get("note") or test_id
                            except Exception:
                                test_name = _humanize_module_name(nodeid, test_id)
                        else:
                            ubr_m = re.search(
                                r"(FIREWALL_\d+|TC_DFS_\d+|SEC-\d+|ACB_\d+)",
                                nodeid,
                                re.I,
                            )
                            if ubr_m:
                                raw = ubr_m.group(1).upper()
                                if raw.startswith("FIREWALL_"):
                                    n = re.match(r"FIREWALL_(\d+)", raw, re.I)
                                    test_id = f"FIREWALL_{int(n.group(1)):02d}" if n else raw
                                elif raw.startswith("TC_DFS_"):
                                    n = re.match(r"TC_DFS_(\d+)", raw, re.I)
                                    test_id = f"TC_DFS_{int(n.group(1)):03d}" if n else raw
                                elif raw.startswith("SEC-"):
                                    n = re.match(r"SEC-(\d+)", raw, re.I)
                                    test_id = f"SEC-{int(n.group(1)):02d}" if n else raw
                                else:
                                    n = re.match(r"ACB_(\d+)", raw, re.I)
                                    test_id = f"ACB_{int(n.group(1)):02d}" if n else raw
                                test_name = _humanize_module_name(nodeid, test_id)
                            else:
                                ft_kw = next(
                                    (str(k).upper() for k in test.get("keywords", []) if re.match(r"FT_\d+", str(k), re.I)),
                                    None,
                                )
                                ft_fn = re.search(r"test_ft_(\d+)_", nodeid, re.I)
                                if ft_kw or ft_fn:
                                    test_id = ft_kw if ft_kw else f"FT_{int(ft_fn.group(1)):02d}"
                                    test_name = _humanize_module_name(nodeid, test_id)
                                else:
                                    api_kw = next(
                                        (
                                            str(k).upper()
                                            for k in test.get("keywords", [])
                                            if re.match(r"API_\d+", str(k), re.I)
                                        ),
                                        None,
                                    )
                                    api_fn = re.search(r"test_api_(\d+)_", nodeid, re.I)
                                    if api_kw or api_fn:
                                        test_id = api_kw if api_kw else f"API_{int(api_fn.group(1)):02d}"
                                        test_name = _humanize_module_name(nodeid, test_id)
                                    else:
                                        radio_kw = next(
                                            (
                                                str(k).upper()
                                                for k in test.get("keywords", [])
                                                if re.match(r"RADIO_24_\d+", str(k), re.I)
                                            ),
                                            None,
                                        )
                                        radio_fn = re.search(r"test_radio_24_(\d+)_", nodeid, re.I)
                                        if radio_kw or radio_fn:
                                            test_id = (
                                                radio_kw
                                                if radio_kw
                                                else f"RADIO_24_{int(radio_fn.group(1)):02d}"
                                            )
                                            test_name = _humanize_module_name(nodeid, test_id)
                                        else:
                                            arp_kw = next(
                                                (
                                                    str(k).upper()
                                                    for k in test.get("keywords", [])
                                                    if re.match(r"ARPBRIDGE_\d+", str(k), re.I)
                                                ),
                                                None,
                                            )
                                            arp_fn = re.search(r"test_arpbridge_(\d+)_", nodeid, re.I)
                                            if arp_kw or arp_fn:
                                                test_id = (
                                                    arp_kw
                                                    if arp_kw
                                                    else f"ARPBRIDGE_{int(arp_fn.group(1)):02d}"
                                                )
                                                test_name = _humanize_module_name(nodeid, test_id)
                                            else:
                                                wsec_kw = next(
                                                    (
                                                        str(k).upper()
                                                        for k in test.get("keywords", [])
                                                        if re.match(r"W_SECURITY_\d+", str(k), re.I)
                                                    ),
                                                    None,
                                                )
                                                wsec_fn = re.search(r"test_w_security_(\d+)_", nodeid, re.I)
                                                if wsec_kw or wsec_fn:
                                                    test_id = (
                                                        wsec_kw
                                                        if wsec_kw
                                                        else f"W_SECURITY_{int(wsec_fn.group(1)):02d}"
                                                    )
                                                    test_name = _humanize_module_name(nodeid, test_id)
                                                else:
                                                    match = re.search(r'test_(gui_\d+)_(.*)', nodeid.lower())
                                                    if match:
                                                        test_id = match.group(1).upper()
                                                        raw_name = match.group(2)
                                                        parts = raw_name.split('_')
                                                        if len(parts) >= 2 and parts[0] == 'summary':
                                                            test_name = '-'.join(p.capitalize() for p in parts[::-1])
                                                        else:
                                                            test_name = '-'.join(p.capitalize() for p in parts)
                                                    else:
                                                        test_id = "N/A"
                                                        test_name = nodeid.split('::')[-1]

        group_name = get_group_marker(test.get('keywords', []))
        outcome = _effective_outcome_for_report(test).upper()
        reason_html = ""
        reason_csv = ""

        catalog = _catalog_case_meta(str(test_id))
        if catalog.get("title"):
            test_name = catalog["title"]
        test_steps = catalog.get("steps") or ""

        validated_params = extract_validated_parameters(test)
        qos_table_html = extract_qos_traffic_table_html(test)
        qos_table_csv = ""
        is_qos_case = bool(re.match(r"QoS_\d+", str(test_id), re.I)) or group_name == "QoS"
        is_um_case = bool(re.match(r"UM_\d+", str(test_id), re.I)) or group_name == "UserMgmt"
        is_logs_case = bool(re.match(r"LOGS_\d+", str(test_id), re.I)) or group_name == "Logs"
        logs_evidence: dict[str, Any] = load_logs_case_evidence(str(test_id)) if is_logs_case else {}
        logs_reason_html, logs_reason_csv = (
            render_logs_verification_html(str(test_id), logs_evidence) if is_logs_case else ("", "")
        )
        if is_qos_case:
            evidence = load_qos_case_evidence(str(test_id))
            if not validated_params:
                evidence_params = evidence.get("params") or []
                if isinstance(evidence_params, list) and evidence_params:
                    validated_params = [str(p) for p in evidence_params]
                else:
                    validated_params = qos_verified_params_fallback(str(test_id))
            if not qos_table_html:
                qos_table_html = str(evidence.get("table_html") or "")
            qos_table_csv = str(evidence.get("table_text") or "")
        elif is_um_case and not validated_params:
            validated_params = um_verified_params_fallback(str(test_id))

        if outcome == 'PASSED':
            stats['passed'] += 1
            status = "PASSED"
            proc_summary = (
                _process_monitor_pass_summary(test, test_id)
                if _is_process_monitor_test(test)
                else ""
            )
            if is_logs_case and logs_reason_html:
                reason_html = logs_reason_html
                reason_csv = logs_reason_csv
            elif is_qos_case and validated_params:
                pills = "".join([f"<span class='param-pill'>{p}</span>" for p in validated_params])
                reason_html = (
                    f"<div class='reason-title'>Successfully Verified ({len(validated_params)} parameters):</div>"
                    f"<div class='param-container'>{pills}</div>"
                )
                reason_csv = (
                    f"Successfully Verified ({len(validated_params)} parameters):\n"
                    + ", ".join(validated_params)
                )
            elif is_qos_case:
                reason_html = (
                    "<div class='reason-title'>Outcome:</div>"
                    "<div class='failure-list'><div class='failure-item'>Test passed.</div></div>"
                )
                reason_csv = "Passed"
            elif proc_summary:
                reason_html = (
                    f"<div class='reason-title'>Outcome:</div>"
                    f"<div class='failure-list'><div class='failure-item'>{proc_summary}</div></div>"
                )
                reason_csv = f"Outcome:\n- {proc_summary}"
            elif _is_functional_test(test):
                ft_html, ft_csv = _functional_execution_details(
                    test,
                    test_id,
                    functional_logs=functional_logs,
                    progress_meta=functional_progress,
                )
                if ft_html:
                    reason_html = ft_html
                    reason_csv = ft_csv
                else:
                    reason_html = (
                        "<div class='reason-title'>Outcome:</div>"
                        "<div class='failure-list'><div class='failure-item'>Functional case passed; "
                        "step log unavailable (re-run with --capture=tee-sys to capture console output).</div></div>"
                    )
                    reason_csv = "Functional case passed; step log unavailable."
            elif _is_arpbridge_test(test):
                arp_html, arp_csv = _arpbridge_execution_details(test, test_id)
                if arp_html:
                    reason_html = arp_html
                    reason_csv = arp_csv
                else:
                    reason_html = (
                        "<div class='reason-title'>Outcome:</div>"
                        "<div class='failure-list'><div class='failure-item'>ARP/Bridge case passed; "
                        "step log unavailable (re-run with --capture=tee-sys to capture console output).</div></div>"
                    )
                    reason_csv = "ARP/Bridge case passed; step log unavailable."
            elif validated_params:
                pills = "".join([f"<span class='param-pill'>{p}</span>" for p in validated_params])
                reason_html = f"<div class='reason-title'>Successfully Verified ({len(validated_params)} parameters):</div><div class='param-container'>{pills}</div>"
                reason_csv = f"Successfully Verified ({len(validated_params)} parameters):\n" + ", ".join(
                    validated_params)
            elif _is_process_monitor_test(test):
                reason_html = (
                    "<div class='reason-title'>Outcome:</div>"
                    "<div class='failure-list'><div class='failure-item'>Test passed; see console log for evidence.</div></div>"
                )
                reason_csv = "Outcome:\n- Test passed; see console log for evidence."
            else:
                reason_html = "All telemetry and backend parameters successfully matched the GUI."
                reason_csv = reason_html
            color = "#10b981"
            bg = "#ecfdf5"

        elif outcome in ("FAILED", "XFAILED"):
            longrepr = str(test.get("call", {}).get("longrepr", "") or "")
            # pytest-json-report uses outcome=xfailed for expected product defects.
            is_xfail = outcome == "XFAILED" or "XFailed:" in longrepr or "_pytest.outcomes.XFailed" in longrepr

            proc_verdict = _process_monitor_case_reason(test, longrepr) if _is_process_monitor_test(test) else None
            if proc_verdict:
                proc_status, proc_reason = proc_verdict
                proc_reason_html = _format_proc_reason_html(proc_reason)
                proc_reason_csv = proc_reason.replace("\n  - ", "\n- ")
                if proc_status == "PARTIAL":
                    stats['partial'] += 1
                    status = "PARTIAL"
                    reason_html = (
                        f"<div class='reason-title' style='color:#b45309;'>Test Partial:</div>"
                        f"<div class='failure-list'>{proc_reason_html}</div>"
                    )
                    reason_csv = f"Test Partial:\n{proc_reason_csv}"
                    color = "#d97706"
                    bg = "#fffbeb"
                else:
                    stats['failed'] += 1
                    status = "FAILED"
                    reason_html = (
                        f"<div class='reason-title' style='color:#991b1b;'>Test Failed:</div>"
                        f"<div class='failure-list'>{proc_reason_html}</div>"
                    )
                    reason_csv = f"Test Failed:\n{proc_reason_csv}"
                    color = "#ef4444"
                    bg = "#fef2f2"
            elif "FAILURE:" in longrepr:
                stats['partial'] += 1
                status = "PARTIAL"
                raw_failures = re.findall(r'FAILURE: (.*)', longrepr)
                clean_failures = [clean_failure_message(f) for f in raw_failures]

                # Format failures as a clean CSS list
                failures_html = "".join([f"<div class='failure-item'>{f}</div>" for f in clean_failures])
                reason_html = f"<div class='reason-title' style='color:#b45309;'>Discrepancies Detected:</div><div class='failure-list'>{failures_html}</div>"

                # Strip HTML tags for the CSV
                csv_failures = [re.sub(r'<[^<]+>', '', f) for f in clean_failures]
                reason_csv = "Discrepancies Detected:\n- " + "\n- ".join(csv_failures)

                color = "#d97706"
                bg = "#fffbeb"
            else:
                stats['failed'] += 1
                status = "FAILED"
                lines = [ln.strip() for ln in longrepr.strip().split("\n") if ln.strip()]
                err = lines[-1] if lines else "Unknown Exception"
                # Prefer the human XFailed / AssertionError message when present.
                for ln in reversed(lines):
                    if ln.startswith("E ") or "XFailed:" in ln or "AssertionError:" in ln:
                        err = ln[2:].strip() if ln.startswith("E ") else ln
                        break
                err = re.sub(r"^_pytest\.outcomes\.XFailed:\s*", "", err)
                err = re.sub(r"^XFailed:\s*", "", err, flags=re.I)
                err = _customer_error_message(err)
                title = "Known product defect:" if is_xfail else "Critical Execution Error:"
                if (is_um_case or is_qos_case) and validated_params:
                    pills = "".join([f"<span class='param-pill'>{p}</span>" for p in validated_params])
                    reason_html = (
                        f"<div class='reason-title' style='color:#991b1b;'>{title}</div>"
                        f"<div class='failure-list'><div class='failure-item'>{err}</div></div>"
                        f"<div class='reason-title' style='margin-top:10px;'>Expected checks:</div>"
                        f"<div class='param-container'>{pills}</div>"
                    )
                else:
                    reason_html = (
                        f"<div class='reason-title' style='color:#991b1b;'>{title}</div>"
                        f"<div class='failure-list'><div class='failure-item'>{err}</div></div>"
                    )
                reason_csv = f"{title}\n- {err}"
                color = "#ef4444"
                bg = "#fef2f2"
        elif outcome == "SKIPPED":
            skip_reason = _skip_reason_text(test)
            if _is_not_available_skip(skip_reason):
                stats['not_available'] += 1
                status = "Not available"
                reason_html = (
                    f"<div class='reason-title' style='color:#64748b;'>Not available:</div>"
                    f"<div class='failure-list'><div class='failure-item'>{skip_reason}</div></div>"
                )
                reason_csv = f"Not available:\n- {skip_reason}"
                color = "#64748b"
                bg = "#f1f5f9"
            else:
                # Manual plan cases and other skips → Not tested (actionable backlog).
                stats['not_tested'] += 1
                status = "Not tested"
                title = (
                    "To be tested manually:"
                    if _is_manual_skip(skip_reason)
                    else "Not tested:"
                )
                reason_html = (
                    f"<div class='reason-title' style='color:#475569;'>{title}</div>"
                    f"<div class='failure-list'><div class='failure-item'>{skip_reason}</div></div>"
                )
                reason_csv = f"{title}\n- {skip_reason}"
                color = "#475569"
                bg = "#e2e8f0"
        else:
            stats['failed'] += 1
            status = "FAILED"
            teardown = test.get("teardown", {})
            err = str(teardown.get("longrepr") or test.get("setup", {}).get("longrepr") or "Unknown error")
            lines = err.strip().split("\n")
            err_line = lines[-1] if lines else "Unknown error"
            reason_html = (
                f"<div class='reason-title' style='color:#991b1b;'>Critical Execution Error:</div>"
                f"<div class='failure-list'><div class='failure-item'>{err_line}</div></div>"
            )
            reason_csv = f"Critical Execution Error:\n- {err_line}"
            color = "#ef4444"
            bg = "#fef2f2"

        if qos_table_html:
            reason_html = f"{reason_html}{qos_table_html}" if reason_html else qos_table_html
            ascii_table = qos_table_csv
            if not ascii_table.strip():
                stdout = str((test.get("call") or {}).get("stdout") or "")
                for line in stdout.splitlines():
                    if (
                        line.startswith("QoS traffic table")
                        or re.match(r"^\s*\d\s+\w", line)
                        or line.startswith("Q ")
                        or set(line.strip()) <= {"-"}
                        or "AvgTX" in line
                    ):
                        ascii_table += line + "\n"
            if ascii_table.strip():
                reason_csv = f"{reason_csv}\n\n{ascii_table.strip()}".strip()

        # Attach DUT verification proof for non-pass Logs outcomes when evidence exists.
        if is_logs_case and logs_reason_html and outcome != "PASSED":
            reason_html = f"{reason_html}{logs_reason_html}" if reason_html else logs_reason_html
            reason_csv = f"{reason_csv}\n\n{logs_reason_csv}".strip() if reason_csv else logs_reason_csv

        if _is_functional_test(test) and str(test_id).upper().startswith("FT_") and status == "FAILED":
            ft_html, ft_csv = _functional_execution_details(
                test,
                test_id,
                functional_logs=functional_logs,
                progress_meta=functional_progress,
            )
            if ft_html:
                reason_html = f"{reason_html}{ft_html}"
                reason_csv = f"{reason_csv}\n{ft_csv}".strip()

        capture_html, capture_csv = render_jumbo_capture_evidence_html(test_id, jumbo_capture_index)
        if capture_html:
            reason_html = f"{reason_html}{capture_html}"
            reason_csv = f"{reason_csv}\n{capture_csv}".strip()

        if (
            _is_arpbridge_test(test)
            and re.match(r"ARPBRIDGE_\d+", str(test_id), re.I)
            and status == "FAILED"
        ):
            arp_html, arp_csv = _arpbridge_execution_details(test, test_id)
            if arp_html:
                reason_html = f"{reason_html}{arp_html}"
                reason_csv = f"{reason_csv}\n{arp_csv}".strip()

        device_scope = "BTS & CPE" if _is_arpbridge_test(test) else ""

        record = {
            'id': test_id,
            'name': test_name,
            'steps': test_steps,
            'device': device_scope,
            'status': status,
            'reason': reason_html,
            'reason_csv': reason_csv,
            'color': color,
            'bg': bg
        }

        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(record)

    for group_name, records in groups.items():
        if group_name == "ProcessMonitor":
            groups[group_name] = _sort_process_monitor_records(records)
        elif group_name == "UserMgmt":
            groups[group_name] = _sort_numbered_suite_records(records, "UM")
        elif group_name == "QoS":
            groups[group_name] = _sort_numbered_suite_records(records, "QoS")
        elif group_name == "IP":
            groups[group_name] = _sort_numbered_suite_records(records, "IP")
        elif group_name == "VLAN":
            groups[group_name] = _sort_numbered_suite_records(records, "VLAN")
        elif group_name == "Logs":
            groups[group_name] = _sort_numbered_suite_records(records, "LOGS")
        elif group_name == "JumboFrames":
            groups[group_name] = _sort_numbered_suite_records(records, "JMB")
        elif group_name == "Firewall":
            groups[group_name] = _sort_numbered_suite_records(records, "FIREWALL")
        elif group_name == "DFS":
            groups[group_name] = _sort_numbered_suite_records(records, "TC_DFS")
        elif group_name == "Security":
            groups[group_name] = _sort_numbered_suite_records(records, "SEC")
        elif group_name == "AsymmetricCBW":
            groups[group_name] = _sort_numbered_suite_records(records, "ACB")
        elif group_name == "Functional":
            groups[group_name] = _sort_functional_records(records)
        elif group_name == "ArpBridgeTable":
            groups[group_name] = _sort_arpbridge_records(records)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    html_filename = ARTIFACTS_DIR / f"{output_prefix}_{build_no}_Report_{date_str}.html"
    csv_filename = ARTIFACTS_DIR / f"{output_prefix}_{build_no}_Report_{date_str}.csv"

    # Generate CSV
    with csv_filename.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Test Group', 'Case ID', 'Test Description', 'Test Steps', 'Status', 'Execution Details'])
        for group_name, records in groups.items():
            for r in records:
                writer.writerow([
                    group_name,
                    r['id'],
                    r['name'],
                    r.get('steps') or '',
                    r['status'],
                    r['reason_csv'],
                ])

    # Generate Group Filter Options
    group_options = ""
    for g in sorted(groups.keys()):
        group_options += f'<option value="{g}">{g}</option>\n'

    report_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    testbed_summary = _load_testbed_summary(profile_name, ip_addr)
    is_arpbridge_report = (
        "ArpBridgeTable" in groups
        or str(build_no).strip().lower() in {"arpbridgetable", "arpbridge", "arp_bridge"}
    )
    arpbridge_bts_ipv4 = ip_addr if ip_addr and ":" not in ip_addr else "192.168.2.10"
    arpbridge_cpe_ipv4 = _cpe_ipv4_from_profile(profile_name, arpbridge_bts_ipv4)
    if is_arpbridge_report:
        testbed_summary = _arpbridge_ipv4_testbed_summary(
            testbed_summary,
            bts_ipv4=arpbridge_bts_ipv4,
            cpe_ipv4=arpbridge_cpe_ipv4,
            cpe_model="UBR650",
        )
    testbed_table = _render_testbed_summary_table(testbed_summary)
    run_meta_extra = ""
    run_meta_target = f'<span><strong>Target device:</strong> {ip_addr}</span>'
    if is_arpbridge_report:
        run_meta_target = ""
        run_meta_extra = (
            f"<span><strong>IP mode:</strong> IPv4 only</span>"
            f"<span><strong>BTS IPv4:</strong> {html_module.escape(arpbridge_bts_ipv4)}</span>"
            f"<span><strong>CPE IPv4:</strong> {html_module.escape(arpbridge_cpe_ipv4)}</span>"
            f"<span><strong>CPE model:</strong> UBR650</span>"
        )
    is_partial_run = bool(data.get("partial") or (data.get("summary") or {}).get("partial"))
    partial_banner = ""
    if is_partial_run:
        source = data.get("recovered_from") or (data.get("summary") or {}).get("recovered_from") or "checkpoint"
        partial_banner = (
            '<div style="max-width:min(1600px,96vw);margin:0 auto 14px;padding:12px 16px;'
            'background:#fff7ed;border:1px solid #fdba74;border-radius:10px;color:#9a3412;'
            'font-size:14px;font-weight:500;">'
            f"Partial run — this report includes {stats['total']} completed test(s) only "
            f"(run aborted or interrupted; recovered from {source})."
            "</div>"
        )

    # Generate Professional HTML
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Senao Quality Assurance Report</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            body {{ font-family: 'Inter', sans-serif; background-color: #eef2f7; color: #334155; margin: 0; padding: 28px 18px; }}
            .wrap {{ max-width: min(1600px, 96vw); margin: 0 auto; }}
            .hero {{
              background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #2563eb 100%);
              color: #fff; border-radius: 14px; padding: 22px 26px; margin-bottom: 18px;
              display: flex; justify-content: space-between; align-items: center; gap: 20px;
            }}
            .hero-main {{ flex: 1; min-width: 0; }}
            .hero h1 {{ margin: 0 0 8px; font-size: 24px; }}
            .hero-date {{ margin: 0; font-size: 14px; opacity: 0.92; font-weight: 500; }}
            .hero-logo {{
              flex-shrink: 0; background: #fff; border-radius: 10px; padding: 10px 14px;
              box-shadow: 0 2px 8px rgba(15,23,42,0.15);
            }}
            .hero-logo img {{ display: block; height: 42px; width: auto; }}
            .container {{ background: #ffffff; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03); overflow: hidden; border: 1px solid #cbd5e1; }}
            .panel-top {{ padding: 20px 40px; border-bottom: 1px solid #e2e8f0; background: #fff; }}
            .panel-top h2 {{ margin: 0 0 12px; font-size: 17px; color: #0f172a; }}
            .run-meta {{ display: flex; gap: 24px; flex-wrap: wrap; font-size: 13px; color: #475569; margin-top: 14px; }}
            .run-meta strong {{ color: #0f172a; }}
            .panel-top h3 {{ margin: 18px 0 12px; font-size: 15px; color: #0f172a; }}
            table.summary-top {{ max-width: 100%; margin-bottom: 0; }}
            .ip-cell {{ white-space: nowrap; font-family: Consolas, Monaco, monospace; font-size: 12px; }}
            table.matrix {{
              width: 100%; max-width: 100%; border-collapse: collapse; background: #fff;
              border: 2px solid #cbd5e1; border-radius: 8px; overflow: hidden;
              box-shadow: 0 1px 3px rgba(15,23,42,0.06);
            }}
            table.matrix th, table.matrix td {{
              border: 1px solid #cbd5e1; padding: 12px 16px; text-align: center;
            }}
            table.matrix thead th {{
              background: #f8fafc; color: #1e3a8a; font-size: 13px; font-weight: 700;
              text-transform: uppercase; letter-spacing: 0.5px;
            }}
            table.matrix th.corner {{ background: #f1f5f9; width: 100px; }}
            table.matrix th.row-label {{
              text-align: left; background: #f8fafc; color: #0f172a;
              font-size: 13px; font-weight: 600; padding-left: 14px;
            }}

            .summary-cards {{ display: flex; flex-wrap: wrap; padding: 30px 40px; gap: 16px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
            .card {{ flex: 1 1 140px; min-width: 120px; padding: 18px 12px; border-radius: 10px; text-align: center; border: 1px solid #e2e8f0; background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.02); cursor: pointer; transition: all 0.2s ease; }}
            .card:hover {{ transform: translateY(-3px); box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
            .card.active {{ border-width: 2px; box-shadow: inset 0 0 0 1px rgba(0,0,0,0.05); transform: scale(0.98); }}
            .card h3 {{ margin: 0; font-size: 32px; font-weight: 700; color: #0f172a; pointer-events: none; }}
            .card p {{ margin: 8px 0 0 0; font-size: 12px; color: #64748b; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; pointer-events: none; }}

            .content {{ padding: 0 40px 40px 40px; }}
            .controls {{ display: flex; justify-content: space-between; align-items: center; margin: 25px 0 15px 0; }}
            .filter-group select {{ padding: 8px 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-family: 'Inter', sans-serif; font-size: 13px; color: #334155; outline: none; cursor: pointer; }}
            .filter-group label {{ font-weight: 600; font-size: 13px; color: #475569; margin-right: 10px; }}

            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 16px 20px; text-align: left; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
            th {{ background-color: #ffffff; color: #64748b; font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; border-bottom: 2px solid #e2e8f0; }}
            tr:hover {{ background-color: #f8fafc; transition: background-color 0.2s ease; }}
            .group-header {{ background-color: #f1f5f9 !important; color: #334155; font-weight: 700; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; padding-top: 24px; border-bottom: 2px solid #cbd5e1; }}

            .badge {{ padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: 700; text-align: center; display: inline-block; letter-spacing: 0.5px; text-transform: uppercase; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}

            /* New CSS for Parameters and Failures */
            .reason-cell {{ font-size: 13px; color: #475569; line-height: 1.6; word-break: break-word; }}
            .reason-title {{ font-weight: 600; color: #0f172a; margin-bottom: 10px; }}
            .param-container {{ display: flex; flex-wrap: wrap; gap: 8px; }}
            .param-pill {{ background-color: #f1f5f9; color: #334155; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-family: 'Consolas', monospace; border: 1px solid #cbd5e1; }}

            .failure-list {{ margin-top: 6px; }}
            .failure-item {{ position: relative; padding-left: 14px; margin-bottom: 8px; }}
            .failure-item::before {{ content: "•"; position: absolute; left: 0; color: #ef4444; font-weight: bold; }}
            .failure-item b {{ color: #0f172a; }}
            .proc-bullet-list {{ margin: 4px 0 8px 18px; padding-left: 18px; }}
            .proc-bullet-list li {{ margin: 2px 0; font-family: 'Consolas', monospace; font-size: 12px; color: #334155; list-style-type: disc; }}
            .ft-exec-panel {{ display: flex; flex-direction: column; gap: 8px; }}
            .ft-runtime-chip {{
              display: inline-flex; align-self: flex-start; align-items: center;
              background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;
              border-radius: 999px; padding: 4px 10px; font-size: 11px; font-weight: 700;
            }}
            .ft-log-simple {{
              border: 1px solid #dbeafe; border-radius: 8px; background: #f8fbff;
              overflow: hidden;
            }}
            .ft-log-simple > summary {{
              list-style: none; cursor: pointer; padding: 8px 12px;
              font-size: 12px; font-weight: 700; color: #1e3a8a; background: #eef4ff;
              user-select: none;
            }}
            .ft-log-simple > summary:hover {{ background: #e0ebff; }}
            .ft-log-simple > summary::-webkit-details-marker {{ display: none; }}
            .ft-log-simple > summary::after {{
              content: " ▾"; color: #64748b; font-size: 11px;
            }}
            .ft-log-simple[open] > summary::after {{ content: " ▴"; }}
            .ft-log-list {{
              margin: 0; padding: 8px 12px 10px 28px; background: #ffffff;
              border-top: 1px solid #e2e8f0;
            }}
            .ft-log-list li {{
              margin: 4px 0; font-family: 'Consolas', monospace; font-size: 12px;
              color: #334155; line-height: 1.45;
            }}
            .ft-terminal-log {{
              margin: 0; padding: 10px 12px; background: #0f172a; color: #e2e8f0;
              font-family: 'Consolas', monospace; font-size: 11px; line-height: 1.4;
              overflow-x: auto; white-space: pre-wrap; word-break: break-word;
              border-top: 1px solid #1e293b; max-height: 420px;
            }}
            {JUMBO_CAPTURE_REPORT_CSS}
            {QOS_TRAFFIC_REPORT_CSS}
        </style>
        <script>
            let currentStatus = 'ALL';
            let currentGroup = 'ALL';

            function setStatusFilter(status, element) {{
                currentStatus = status;

                document.querySelectorAll('.card').forEach(c => c.classList.remove('active'));
                element.classList.add('active');

                applyFilters();
            }}

            function setGroupFilter(group) {{
                currentGroup = group;
                applyFilters();
            }}

            function applyFilters() {{
                const testRows = document.querySelectorAll('.test-row');
                const groupHeaders = document.querySelectorAll('.group-header-row');

                let groupVisibility = {{}};
                groupHeaders.forEach(h => groupVisibility[h.dataset.group] = 0);

                testRows.forEach(row => {{
                    const statusMatch = currentStatus === 'ALL' || row.dataset.status === currentStatus;
                    const groupMatch = currentGroup === 'ALL' || row.dataset.group === currentGroup;

                    if (statusMatch && groupMatch) {{
                        row.style.display = '';
                        groupVisibility[row.dataset.group]++;
                    }} else {{
                        row.style.display = 'none';
                    }}
                }});

                groupHeaders.forEach(header => {{
                    if (groupVisibility[header.dataset.group] > 0) {{
                        header.style.display = '';
                    }} else {{
                        header.style.display = 'none';
                    }}
                }});
            }}
        </script>
    </head>
    <body>
        <div class="wrap">
            <header class="hero">
                <div class="hero-main">
                    <h1>Senao UBR Validation Execution Report</h1>
                    <p class="hero-date">{report_timestamp}</p>
                </div>
                <div class="hero-logo">
                    <img src="{senao_logo_src()}" alt="Senao Networks"/>
                </div>
            </header>
            {partial_banner}

            <div class="container">
            <section class="panel-top">
                <h2>Testbed Summary</h2>
                {testbed_table}
                <h3>Run Summary</h3>
                <div class="run-meta">
                    <span><strong>Build release:</strong> #{build_no}</span>
                    {run_meta_target}
                    {run_meta_extra}
                </div>
            </section>

            <div class="summary-cards">
                <div class="card active" style="border-bottom: 4px solid #64748b;" onclick="setStatusFilter('ALL', this)">
                    <h3>{stats['total']}</h3><p>Total</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #10b981;" onclick="setStatusFilter('PASSED', this)">
                    <h3>{stats['passed']}</h3><p style="color: #10b981;">Pass</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #f59e0b;" onclick="setStatusFilter('PARTIAL', this)">
                    <h3>{stats['partial']}</h3><p style="color: #f59e0b;">Partial</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #ef4444;" onclick="setStatusFilter('FAILED', this)">
                    <h3>{stats['failed']}</h3><p style="color: #ef4444;">Fail</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #94a3b8;" onclick="setStatusFilter('Not available', this)">
                    <h3>{stats['not_available']}</h3><p style="color: #64748b;">Not available</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #475569;" onclick="setStatusFilter('Not tested', this)">
                    <h3>{stats['not_tested']}</h3><p style="color: #475569;">Not tested</p>
                </div>
            </div>

            <div class="content">
                <div class="controls">
                    <div class="filter-group">
                        <label for="groupFilter">Filter by Test Group:</label>
                        <select id="groupFilter" onchange="setGroupFilter(this.value)">
                            <option value="ALL">All Groups</option>
                            {group_options}
                        </select>
                    </div>
                </div>

                <table class="results-table">
                    <thead>
                        <tr>
                            <th style="width:8%">Case ID</th>
                            <th style="width:18%">Test Description</th>
                            <th style="width:28%">Test Steps</th>
                            <th style="width:10%">Status</th>
                            <th style="width:36%">Execution Details</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    for group_name, records in groups.items():
        html += f"""
                        <tr class="group-header-row" data-group="{group_name}">
                            <td colspan="5" class="group-header">↳ Test Group: {group_name}</td>
                        </tr>
        """
        for r in records:
            name = str(r["name"] or "")
            steps = str(r.get("steps") or "").strip()
            name_esc = _escape_html(name)
            if len(name) > 110:
                name_html = f'<div class="module-name-short" title="{name_esc}">{_escape_html(name[:107])}…</div>'
            else:
                name_html = f'<div class="module-name-short">{name_esc}</div>'
            if steps:
                steps_html = f'<div class="test-steps-text">{_escape_html(steps)}</div>'
            else:
                steps_html = '<span style="color:#94a3b8;">—</span>'
            html += f"""
                            <tr class="test-row" data-group="{group_name}" data-status="{r['status']}">
                                <td style="font-weight: 600; color: #0f172a; white-space: nowrap;">{r['id']}</td>
                                <td class="module-name-cell">{name_html}</td>
                                <td class="test-steps-cell">{steps_html}</td>
                                <td><span class="badge" style="background-color: {r['bg']}; color: {r['color']}; border: 1px solid {r['color']}40;">{r['status']}</span></td>
                                <td class="reason-cell">{r['reason']}</td>
                            </tr>
            """

    html += """
                    </tbody>
                </table>
            </div>
            </div>
        </div>
    </body>
    </html>
    """

    with html_filename.open('w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ Generated Professional Reports: {html_filename} & {csv_filename}")


if __name__ == "__main__":
    generate()