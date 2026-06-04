#!/usr/bin/env python3
"""Generate automation coverage workbook from Senao UBR P2MP test plan Excel."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "Senao UBR P2MP Test Result_May18.xlsx"
OUTPUT_PATH = ROOT / "reports" / "artifacts" / "Automation_Coverage_May18.xlsx"

TC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*_\d+$")
CASE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*_\d+$")

JENKINS_JOBS = {
    "gui": {
        "pipeline_file": "jenkins/jenkins-AutomationFramework",
        "job_name": "jenkins-AutomationFramework (TEST_MARKERS=GUI)",
        "default_pytest_path": "tests/GUI/",
    },
    "ip": {
        "pipeline_file": "jenkins/jenkins-AutomationFramework",
        "job_name": "jenkins-AutomationFramework (TEST_MARKERS=IP)",
        "default_pytest_path": "tests/IP/",
    },
    "jumbo": {
        "pipeline_file": "jenkins/jenkins-AutomationFramework",
        "job_name": "jenkins-AutomationFramework (Jumbo — use tests/JumboFrames/)",
        "default_pytest_path": "tests/JumboFrames/",
    },
    "regression": {
        "pipeline_file": "jenkins/jenkins-Regression",
        "job_name": "jenkins-Regression (UBR Stability Regression)",
        "default_pytest_path": "tests/Regression/",
    },
    "throughput": {
        "pipeline_file": "jenkins/jenkins-Throughput",
        "job_name": "jenkins-Throughput (TRex matrix — no GUI_xx ID)",
        "default_pytest_path": "traffic/throughput_runner.py",
    },
}

# Maps automated pytest markers to plan IDs (when naming differs).
AUTOMATION_ALIASES: dict[str, str] = {}

FAST_GUI_PATTERNS = (
    "summary",
    "top panel",
    "wireless",
    "radio",
    "network",
    "dhcp",
    "ethernet",
    "management",
    "monitor",
    "logging",
    "ntp",
    "timezone",
    "location",
)


def collect_automated_ids() -> set[str]:
    return {r["case_id"] for r in collect_automation_index()}


def _module_pytest_marks(text: str) -> list[str]:
    marks: list[str] = []
    m = re.search(r"pytestmark\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if not m:
        return marks
    for mm in re.finditer(r"pytest\.mark\.(\w+)", m.group(1)):
        name = mm.group(1)
        if name != "asyncio":
            marks.append(name)
    return marks


def _jenkins_info(case_id: str, rel_test_file: str) -> dict[str, str]:
    if case_id.startswith("REG_"):
        job = JENKINS_JOBS["regression"]
    elif case_id.startswith("JMB_"):
        job = JENKINS_JOBS["jumbo"]
    elif case_id.startswith("IP_"):
        job = JENKINS_JOBS["ip"]
    else:
        job = JENKINS_JOBS["gui"]
    pytest_path = job["default_pytest_path"]
    if case_id.startswith("GUI_") and not case_id.startswith("REG_"):
        pytest_path = f"tests/GUI/  # file: {rel_test_file}"
    elif case_id.startswith("IP_"):
        pytest_path = f"tests/IP/  # file: {rel_test_file}"
    cmd = (
        f"PYTHONPATH=. pytest {job['default_pytest_path']} -v -k \"{case_id}\" "
        f"--profile default"
    )
    if case_id.startswith("IP_"):
        cmd = (
            f"PYTHONPATH=. pytest tests/IP/ -m IP -v -k \"{case_id}\" "
            f"--allow-ip-suite --profile ipv6_quickrun"
        )
    if case_id.startswith("JMB_") and case_id in ("JMB_07", "JMB_10"):
        cmd += " --allow-destructive-jumbo"
    if case_id.startswith("REG_"):
        cmd = (
            f"PYTHONPATH=. pytest tests/Regression/ -v -k \"{case_id}\" "
            f"--allow-regression --regression-fresh"
        )
    return {
        "jenkins_pipeline_file": job["pipeline_file"],
        "jenkins_job_name": job["job_name"],
        "pytest_path": pytest_path,
        "pytest_command": cmd,
    }


def collect_automation_index() -> list[dict]:
    """Map each automated TESTCASE-ID to pytest file, function, markers, Jenkins job."""
    records: list[dict] = []
    for py in sorted((ROOT / "tests").rglob("test_*.py")):
        text = py.read_text(encoding="utf-8", errors="ignore")
        rel_file = py.relative_to(ROOT).as_posix()
        module_marks = _module_pytest_marks(text)
        pending: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("@pytest.mark."):
                mark = stripped.split("@pytest.mark.", 1)[1].split("(", 1)[0].strip()
                if mark != "asyncio":
                    pending.append(mark)
                continue
            fn_match = re.match(r"async def (test_\w+)\s*\(", stripped)
            if not fn_match:
                continue
            func_name = fn_match.group(1)
            case_ids = [m for m in pending if CASE_ID_RE.match(m.upper())]
            suite_marks = list(dict.fromkeys(module_marks + [m for m in pending if not CASE_ID_RE.match(m.upper())]))
            pending = []

            helper = ""
            helper_m = re.search(rf"async def {func_name}\(.*?\n\s+await (\w+)\(", text)
            if helper_m:
                helper = helper_m.group(1)
            impl_module = ""
            import_m = re.search(
                rf"from (utils\.\w+|pages\.\w+) import[\s\S]*?{re.escape(helper)}",
                text,
            )
            if import_m:
                impl_module = import_m.group(1)

            for case_id in case_ids:
                cid = case_id.upper()
                jenkins = _jenkins_info(cid, rel_file)
                records.append(
                    {
                        "case_id": cid,
                        "test_function": func_name,
                        "pytest_file": rel_file,
                        "suite_markers": ", ".join(suite_marks),
                        "assertion_helper": helper,
                        "implementation_module": impl_module,
                        **jenkins,
                    }
                )
    records.sort(key=lambda r: (_case_id_sort_key(r["case_id"]), r["test_function"]))
    return records


def _case_id_sort_key(case_id: str) -> tuple:
    prefix, num = case_id.split("_", 1)
    return (prefix, int(num))


def extract_plan_cases(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    cases: list[dict] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        header_row = None
        tc_col = desc_col = result_col = type_col = dut_col = trace_col = None
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if i > 5000:
                break
            if not row:
                continue
            cells = [str(c).strip() if c is not None else "" for c in row]
            upper = [c.upper() for c in cells]
            if header_row is None:
                if "TESTCASE-ID" in upper:
                    header_row = i
                    tc_col = upper.index("TESTCASE-ID")
                    desc_col = upper.index("TEST DESCRIPTION") if "TEST DESCRIPTION" in upper else None
                    result_col = upper.index("RESULT") if "RESULT" in upper else None
                    type_col = upper.index("TEST TYPE") if "TEST TYPE" in upper else None
                    dut_col = upper.index("TEST RUN DUT") if "TEST RUN DUT" in upper else None
                    trace_col = upper.index("TRACEABILITY-ID") if "TRACEABILITY-ID" in upper else None
                continue
            if header_row and tc_col is not None:
                tc = cells[tc_col] if tc_col < len(cells) else ""
                if not TC_RE.match(tc):
                    continue
                cases.append(
                    {
                        "id": tc.upper(),
                        "sheet": sheet_name,
                        "desc": cells[desc_col][:200] if desc_col and desc_col < len(cells) else "",
                        "result": cells[result_col] if result_col and result_col < len(cells) else "",
                        "test_type": cells[type_col] if type_col and type_col < len(cells) else "",
                        "dut": cells[dut_col] if dut_col and dut_col < len(cells) else "",
                        "traceability": cells[trace_col] if trace_col and trace_col < len(cells) else "",
                    }
                )
    wb.close()
    return cases


def is_blocked(result: str) -> bool:
    r = (result or "").lower()
    return any(x in r for x in ("not available", "not tested", "not applicable", "n/a"))


def classify_case(case: dict, automated_ids: set[str]) -> tuple[str, str, str]:
    """Return (automation_status, feasibility, effort)."""
    cid = case["id"]
    if cid in automated_ids or AUTOMATION_ALIASES.get(cid) in automated_ids:
        return "Completed", "Done", "—"

    if cid in {"REG_01", "REG_02", "REG_03"}:
        return "Completed (repo only)", "Done", "—"

    sheet = case["sheet"]
    ttype = (case["test_type"] or "").lower()
    desc = (case["desc"] or "").lower()
    result = case["result"] or ""

    if is_blocked(result):
        return "Blocked / N/A (plan)", "Wait for feature", "—"

    if any(
        k in desc
        for k in (
            "led",
            "poe",
            "power on",
            "physical",
            "cable length",
            "spectrum",
            "site survey",
            "tx power measurement",
        )
    ) or "physical" in ttype:
        return "Not planned (manual lab)", "Manual / instruments", "N/A"

    if "throughput" in sheet.lower() or "ixia" in desc or "trex" in desc:
        return "Framework exists", "Semi-auto (TRex/IXIA)", "1–2 wks/suite"

    if cid.startswith("GUI_"):
        if any(p in desc.lower() for p in FAST_GUI_PATTERNS) or ttype in (
            "validation",
            "configuration",
            "functional",
        ):
            return "Not automated", "Fast (reuse GUI+SSH patterns)", "0.5–1 day"
        return "Not automated", "Medium (new GUI flow)", "1–2 days"

    if sheet in {
        "Jumbo Frames",
        "IP_Config",
        "DHCP_Server",
        "DDRS",
        "DCS",
        "OFDMA",
        "MU-MIMO",
        "ATPC",
        "QoS",
        "VLAN",
        "API",
        "Functional",
        "Logs",
        "GPS",
        "PPPoE",
        "Process Monitoring",
        "User Management",
    }:
        if "negative" in ttype or "corrupt" in desc or "factory reset" in desc or "power loss" in desc:
            return "Not automated", "Slow (destructive/negative)", "2–3 days"
        return "Not automated", "Medium (feature module)", "1–3 days"

    if cid.startswith("ARPBRIDGE"):
        if cid.endswith("_01") or cid.endswith("_02"):
            return "Partial coverage", "Partial (GUI_105–108 tables)", "0.5 day"
        return "Not automated", "Manual / no GUI", "N/A"

    if sheet in ("Sanity", "Ethernet_Test", "Speed Test"):
        return "Not automated", "Medium (mixed sanity)", "1–2 days"

    if sheet in ("Device_NMS_Interoperability", "NMS-Client Communication", "Wireless_Link_from_NMS"):
        return "Not automated", "Slow (NMS integration)", "1–2 weeks"

    return "Not automated", "Triage", "TBD"


def category_from_case(case: dict) -> str:
    cid = case["id"]
    if cid.startswith("GUI_"):
        n = int(cid.split("_")[1])
        if n <= 4:
            return "GUI — Summary"
        if n <= 10:
            return "GUI — Top Panel"
        if n <= 16:
            return "GUI — Quick Start"
        if n <= 29:
            return "GUI — Wireless / DDRS"
        if n <= 55:
            return "GUI — Network"
        if n <= 69:
            return "GUI — Management / Tools"
        if n <= 104:
            return "GUI — Other"
        return "GUI — Monitor"
    if cid.startswith("JMB_"):
        return "Jumbo Frames"
    if cid.startswith("REG_"):
        return "Regression"
    return case["sheet"]


def build_automation_index_sheet(
    wb: openpyxl.Workbook,
    index_rows: list[dict],
    plan_by_id: dict[str, dict],
) -> None:
    ws = wb.create_sheet("Automation Index")
    headers = [
        "TESTCASE-ID",
        "Plan Sheet",
        "Plan Description",
        "Pytest File",
        "Test Function",
        "Suite Markers",
        "Assertion Helper",
        "Implementation Module",
        "Jenkins Pipeline File",
        "Jenkins Job Name",
        "Pytest Path / Scope",
        "Example Pytest Command",
    ]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="2F5496")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    for row in index_rows:
        plan = plan_by_id.get(row["case_id"], {})
        ws.append(
            [
                row["case_id"],
                plan.get("sheet", "(not in May18 plan)"),
                plan.get("desc", ""),
                row["pytest_file"],
                row["test_function"],
                row["suite_markers"],
                row["assertion_helper"],
                row["implementation_module"],
                row["jenkins_pipeline_file"],
                row["jenkins_job_name"],
                row["pytest_path"],
                row["pytest_command"],
            ]
        )

    ws.freeze_panes = "A2"
    widths = [14, 22, 36, 32, 28, 24, 28, 28, 28, 36, 28, 52]
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    note_row = ws.max_row + 2
    ws.cell(row=note_row, column=1, value="Throughput (TRex)").font = Font(bold=True)
    tp = JENKINS_JOBS["throughput"]
    ws.append(
        [
            "(matrix)",
            "Throughput per MCS",
            "Not 1:1 pytest case IDs",
            "traffic/throughput_runner.py",
            "run_trex_matrix (CLI)",
            "Throughput",
            "—",
            "traffic/",
            tp["pipeline_file"],
            tp["job_name"],
            tp["default_pytest_path"],
            "Run via Jenkins jenkins-Throughput or traffic/throughput_runner.py",
        ]
    )


def build_workbook(
    cases: list[dict],
    automated_ids: set[str],
    index_rows: list[dict],
) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws_sum = wb.active
    ws_sum.title = "Summary"

    # Classify all cases
    rows_detail = []
    status_counter = Counter()
    feas_counter = Counter()
    sheet_counter = Counter()
    completed = 0
    actionable = 0
    fast_count = 0

    reg_only = [x for x in automated_ids if x.startswith("REG_")]

    for case in cases:
        status, feas, effort = classify_case(case, automated_ids)
        blocked = is_blocked(case["result"])
        if status.startswith("Completed"):
            completed += 1
        if not blocked and status != "Blocked / N/A (plan)":
            actionable += 1
        if feas.startswith("Fast"):
            fast_count += 1
        status_counter[status] += 1
        feas_counter[feas] += 1
        sheet_counter[case["sheet"]] += 1
        rows_detail.append(
            {
                **case,
                "category": category_from_case(case),
                "automation_status": status,
                "feasibility": feas,
                "effort_estimate": effort,
                "in_repo": "Yes" if case["id"] in automated_ids else ("Yes" if case["id"] in reg_only else "No"),
            }
        )

    total = len(cases)
  # Summary content
    today = date.today().isoformat()
    ws_sum["A1"] = "UBR P2MP — Automation Coverage vs May18 Test Plan"
    ws_sum["A1"].font = Font(bold=True, size=14)
    ws_sum["A2"] = f"Source: {PLAN_PATH.name}"
    ws_sum["A3"] = f"Generated: {today}"
    ws_sum["A4"] = f"Automation repo: {ROOT.name}"

    summary_rows = [
        ("", ""),
        ("Metric", "Count"),
        ("Total test cases in plan (with TESTCASE-ID)", total),
        ("Already automated (in pytest repo)", len(automated_ids)),
        ("  — matched to plan IDs", completed),
        ("  — regression only (REG_01–03, not in plan sheet)", len(reg_only)),
        ("Plan cases marked Pass/Fail (actionable)", actionable),
        ("Plan cases Blocked / N/A / Not Available", total - actionable),
        ("", ""),
        ("Fastest next wins (GUI Validation/Config, not blocked)", fast_count),
        ("Medium feature suites (VLAN, QoS, DDRS, etc.)", sum(1 for r in rows_detail if r["feasibility"].startswith("Medium"))),
        ("Semi-auto throughput (TRex/IXIA)", sum(1 for r in rows_detail if "TRex" in r["feasibility"])),
    ]

    manual_blocked = sum(
        1
        for r in rows_detail
        if r["feasibility"].startswith("Manual")
        or r["automation_status"].startswith("Blocked")
        or r["feasibility"] == "Wait for feature"
    )

    summary_rows[-1] = ("Manual / blocked / N/A (not targeted now)", manual_blocked)

    r0 = 6
    for label, val in summary_rows:
        ws_sum.cell(row=r0, column=1, value=label)
        ws_sum.cell(row=r0, column=2, value=val)
        if label == "Metric":
            ws_sum.cell(row=r0, column=1).font = Font(bold=True)
            ws_sum.cell(row=r0, column=2).font = Font(bold=True)
        r0 += 1

    r0 += 1
    ws_sum.cell(row=r0, column=1, value="Automation status breakdown").font = Font(bold=True)
    r0 += 1
    for k, v in status_counter.most_common():
        ws_sum.cell(row=r0, column=1, value=k)
        ws_sum.cell(row=r0, column=2, value=v)
        r0 += 1

    r0 += 1
    ws_sum.cell(row=r0, column=1, value="Feasibility breakdown").font = Font(bold=True)
    r0 += 1
    for k, v in feas_counter.most_common():
        ws_sum.cell(row=r0, column=1, value=k)
        ws_sum.cell(row=r0, column=2, value=v)
        r0 += 1

    r0 += 1
    ws_sum.cell(row=r0, column=1, value='See "Automation Index" sheet').font = Font(bold=True)
    ws_sum.cell(row=r0, column=2, value=f"{len(index_rows)} tests → pytest file + Jenkins job")
    r0 += 2
    ws_sum.cell(row=r0, column=1, value="Top plan sheets by case count").font = Font(bold=True)
    r0 += 1
    for k, v in sheet_counter.most_common(15):
        ws_sum.cell(row=r0, column=1, value=k)
        ws_sum.cell(row=r0, column=2, value=v)
        r0 += 1

    ws_sum.column_dimensions["A"].width = 52
    ws_sum.column_dimensions["B"].width = 18

    # GUI progress sheet
    ws_gui = wb.create_sheet("GUI Progress")
    gui_headers = ["TESTCASE-ID", "Description", "Plan Result", "Automated", "Feasibility", "Effort"]
    ws_gui.append(gui_headers)
    for h in gui_headers:
        ws_gui.cell(row=1, column=gui_headers.index(h) + 1).font = Font(bold=True)
    gui_cases = sorted(
        [r for r in rows_detail if r["id"].startswith("GUI_")],
        key=lambda x: int(x["id"].split("_")[1]),
    )
    for g in gui_cases:
        ws_gui.append(
            [
                g["id"],
                g["desc"],
                g["result"],
                "Yes" if g["in_repo"] == "Yes" else "No",
                g["feasibility"],
                g["effort_estimate"],
            ]
        )
    auto_gui = sum(1 for g in gui_cases if g["in_repo"] == "Yes")
    ws_gui.cell(row=len(gui_cases) + 3, column=1, value=f"GUI automated: {auto_gui} / {len(gui_cases)}")
    ws_gui.cell(row=len(gui_cases) + 4, column=1, value=f"GUI remaining (actionable): {sum(1 for g in gui_cases if g['in_repo']=='No' and not is_blocked(g['result']))}")

    # Full detail
    ws_all = wb.create_sheet("All Cases")
    headers = [
        "TESTCASE-ID",
        "Sheet",
        "Category",
        "Description",
        "Test Type",
        "DUT",
        "Plan Result",
        "In Repo",
        "Automation Status",
        "Feasibility",
        "Effort Estimate",
    ]
    ws_all.append(headers)
    header_fill = PatternFill("solid", fgColor="4472C4")
    for col, h in enumerate(headers, 1):
        cell = ws_all.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True)

    fill_done = PatternFill("solid", fgColor="C6EFCE")
    fill_fast = PatternFill("solid", fgColor="FFEB9C")
    fill_block = PatternFill("solid", fgColor="D9D9D9")

    for r in sorted(rows_detail, key=lambda x: (x["sheet"], x["id"])):
        row = [
            r["id"],
            r["sheet"],
            r["category"],
            r["desc"],
            r["test_type"],
            r["dut"],
            r["result"],
            r["in_repo"],
            r["automation_status"],
            r["feasibility"],
            r["effort_estimate"],
        ]
        ws_all.append(row)
        row_idx = ws_all.max_row
        if r["automation_status"].startswith("Completed"):
            for c in range(1, len(headers) + 1):
                ws_all.cell(row=row_idx, column=c).fill = fill_done
        elif r["feasibility"].startswith("Fast"):
            ws_all.cell(row=row_idx, column=10).fill = fill_fast
        elif r["automation_status"].startswith("Blocked"):
            for c in range(1, len(headers) + 1):
                ws_all.cell(row=row_idx, column=c).fill = fill_block

    for col in range(1, len(headers) + 1):
        ws_all.column_dimensions[get_column_letter(col)].width = 16 if col != 4 else 40

    # Roadmap sheet
    ws_road = wb.create_sheet("Roadmap (fastest)")
    ws_road.append(["Phase", "Scope", "Cases (approx)", "Calendar (rough)", "Notes"])
    for c in range(1, 6):
        ws_road.cell(row=1, column=c).font = Font(bold=True)
    gui_remaining_fast = sum(
        1
        for r in rows_detail
        if r["id"].startswith("GUI_") and r["in_repo"] == "No" and r["feasibility"].startswith("Fast") and not is_blocked(r["result"])
    )
    gui_remaining_med = sum(
        1
        for r in rows_detail
        if r["id"].startswith("GUI_") and r["in_repo"] == "No" and r["feasibility"].startswith("Medium") and not is_blocked(r["result"])
    )
    medium_features = sum(
        1
        for r in rows_detail
        if not r["id"].startswith("GUI_")
        and r["in_repo"] == "No"
        and r["feasibility"].startswith("Medium")
        and not is_blocked(r["result"])
    )
    roadmap = [
        ("0 — Done", "Sanity GUI, Network, Management, Monitor, Jumbo, Regression", len(automated_ids), "Complete", "65 pytest markers"),
        ("1 — Quick wins", "Remaining GUI Validation (Quick Start, tools, 2.4G pages)", gui_remaining_fast, "2–3 weeks", "Reuse validate_*_lifecycle helpers"),
        ("2 — GUI depth", "GUI configuration / multi-step", gui_remaining_med, "3–4 weeks", "Locators + apply_triple patterns"),
        ("3 — Feature parity", "VLAN, QoS, DDRS, DCS, OFDMA, API sheets", medium_features, "6–10 weeks", "Group by LuCI module"),
        ("4 — Throughput", "Throughput per MCS matrix", "Matrix (not row-per-ID)", "2–4 weeks", "TRex runner + Jenkins"),
        ("5 — NMS /interop", "Device_NMS_Interoperability", sheet_counter.get("Device_NMS_Interoperability", 0), "TBD", "API/NMS dependency"),
    ]
    for i, row in enumerate(roadmap, 2):
        for j, val in enumerate(row, 1):
            ws_road.cell(row=i, column=j, value=val)
    ws_road.column_dimensions["A"].width = 18
    ws_road.column_dimensions["B"].width = 45
    ws_road.column_dimensions["E"].width = 40

    plan_by_id = {c["id"]: c for c in cases}
    build_automation_index_sheet(wb, index_rows, plan_by_id)

    return wb


def main():
    if not PLAN_PATH.exists():
        raise SystemExit(f"Plan not found: {PLAN_PATH}")
    index_rows = collect_automation_index()
    automated_ids = {r["case_id"] for r in index_rows}
    cases = extract_plan_cases(PLAN_PATH)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb = build_workbook(cases, automated_ids, index_rows)
    wb.save(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Plan cases: {len(cases)} | Automated in repo: {len(automated_ids)}")
    print(f"Automation Index rows: {len(index_rows)}")


if __name__ == "__main__":
    main()
