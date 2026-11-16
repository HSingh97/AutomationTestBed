"""Build Senao Sanity plan-sheet HTML from Excel + pytest report.json.

Columns: S.No., TESTCASE-ID, TEST DESCRIPTION, TEST STEPS, RESULT, COMMENTS
(SECTION and EXPECTED RESULT intentionally omitted per lab report preference.)
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
from datetime import datetime
from pathlib import Path

from utils.senao_branding import senao_logo_src

ARTIFACTS_DIR = Path("reports/artifacts")
DEFAULT_PLAN = Path.home() / "Downloads" / "Senao UBR P2MP Test Plan_v8.0a.xlsx"


def _esc(text: object) -> str:
    return html_module.escape(str(text or ""), quote=True).replace("\n", "<br/>")


def _ingest_pytest_data(data: dict, out: dict[str, dict]) -> None:
    """Merge one pytest-json-report payload into SANITY_NN → {status, comment}."""
    for test in data.get("tests") or []:
        nodeid = str(test.get("nodeid") or "")
        keywords = test.get("keywords") or []
        case_id = ""
        for kw in keywords:
            m = re.match(r"SANITY_(\d+)$", str(kw), re.I)
            if m:
                case_id = f"SANITY_{int(m.group(1)):02d}"
                break
        if not case_id:
            m = re.search(r"test_sanity_(\d+)_", nodeid, re.I)
            if m:
                case_id = f"SANITY_{int(m.group(1)):02d}"
        if not case_id:
            continue
        outcome = str(test.get("outcome") or "failed").lower()
        status = {
            "passed": "PASS",
            "failed": "FAIL",
            "skipped": "NOT TESTED",
            "error": "FAIL",
            "xfailed": "FAIL",
            "xpassed": "PASS",
        }.get(outcome, "FAIL")
        comment = "Automated"
        if status == "FAIL":
            call = test.get("call") or {}
            longrepr = str(call.get("longrepr") or "")
            # Prefer first FAILURE line from pytest-check.
            fail_m = re.search(r"FAILURE:\s*(.+)", longrepr)
            detail = (fail_m.group(1).strip() if fail_m else longrepr.strip().splitlines()[0:1])
            if isinstance(detail, list):
                detail = detail[0] if detail else "failed"
            comment = f"Automated | {detail[:220]}"
        out[case_id] = {"status": status, "comment": comment, "automated": True}


def _load_pytest_outcomes(report_json: Path, runs_dir: Path | None = None) -> dict[str, dict]:
    """Map SANITY_NN → {outcome, comment} from pytest-json-report and/or case_runs/."""
    out: dict[str, dict] = {}
    if report_json.is_file():
        try:
            _ingest_pytest_data(json.loads(report_json.read_text(encoding="utf-8")), out)
        except Exception:
            pass
    if runs_dir and runs_dir.is_dir():
        # Prefer newest per case: sanity_NN.json then any sanity_NN_*.json by mtime.
        by_case: dict[str, Path] = {}
        for path in sorted(runs_dir.glob("sanity_*.json")):
            m = re.match(r"sanity_(\d+)", path.stem, re.I)
            if not m:
                continue
            case_id = f"SANITY_{int(m.group(1)):02d}"
            prev = by_case.get(case_id)
            if prev is None or path.stat().st_mtime >= prev.stat().st_mtime:
                by_case[case_id] = path
        for path in by_case.values():
            try:
                _ingest_pytest_data(json.loads(path.read_text(encoding="utf-8")), out)
            except Exception:
                continue
    return out


def _cell_rgb(cell) -> str | None:
    fill = getattr(cell, "fill", None)
    fg = getattr(fill, "fgColor", None) if fill is not None else None
    if fg is None or getattr(fg, "type", None) != "rgb" or not getattr(fg, "rgb", None):
        return None
    rgb = str(fg.rgb).upper()
    if rgb in ("00000000", "FFFFFFFF"):
        return None
    return rgb


# Plan sheet highlight: purple = covered in other suite scripts (Performance/Regression/…).
_PLAN_PURPLE_RGB = "FFBF819E"

# Cases deferred to another suite/script report: RESULT=Not Tested, comment names the report.
_SCRIPT_REPORT_BY_SECTION = {
    "led verification": "Senao_LED_Verification_Report",
    "jumbo frame": "Senao_Jumbo_Frames_Report",
    "qos": "Senao_QoS_Report",
    "rf link statistics": "Senao_Throughput_Report",
    "throughput test": "Senao_Throughput_Report",
    "multiple reboot": "Senao_Regression_Report",
    "multiple factory reset": "Senao_Regression_Report",
    "stability": "Senao_Stability_Report",
}

# LED Verification (SANITY_10–16): plan RESULT is Not Available, but show Not Tested + script refer.
_LED_SCRIPT_CASES = {f"SANITY_{n:02d}" for n in range(10, 17)}

# Manual / not automated — RESULT=Not Tested, COMMENTS=Not Automated (no purple row).
_NOT_AUTOMATED_CASES = {
    *{f"SANITY_{n:02d}" for n in range(10, 17)},  # LED Verification
    "SANITY_107",
    "SANITY_108",
    "SANITY_109",
    "SANITY_110",
}


def _script_report_name(section: str, tid: str = "") -> str:
    key = str(section or "").strip().lower()
    if tid in _LED_SCRIPT_CASES or "led" in key:
        return _SCRIPT_REPORT_BY_SECTION["led verification"]
    for needle, name in _SCRIPT_REPORT_BY_SECTION.items():
        if needle in key:
            return name
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(section or "Suite").strip()).strip("_") or "Suite"
    return f"Senao_{cleaned}_Report"


def _load_plan_rows(plan_xlsx: Path) -> list[dict]:
    import openpyxl

    # Need styles (not read_only) to detect purple "verify in suite" rows.
    wb = openpyxl.load_workbook(plan_xlsx, data_only=True)
    if "Sanity" not in wb.sheetnames:
        raise RuntimeError(f"No 'Sanity' sheet in {plan_xlsx}")
    ws = wb["Sanity"]
    header_cells = next(ws.iter_rows(min_row=1, max_row=1))
    header = [str(c.value or "").strip().upper() for c in header_cells]

    def col(*names: str) -> int | None:
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_sno = col("S.NO.", "S.NO", "SNO")
    i_id = col("TESTCASE-ID", "TEST CASE ID", "TESTCASE ID")
    i_desc = col("TEST DESCRIPTION", "DESCRIPTION")
    i_steps = col("TEST STEPS", "STEPS")
    i_section = col("SECTION")
    i_result = col("RESULT")
    i_comments = col("COMMENTS", "COMMENT")
    rows: list[dict] = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        if i_id is None:
            continue
        tid = str(row[i_id].value or "").strip().upper()
        if not re.match(r"SANITY_\d+$", tid):
            continue
        sno = row[i_sno].value if i_sno is not None else ""
        desc = row[i_desc].value if i_desc is not None else ""
        steps = row[i_steps].value if i_steps is not None else ""
        section = str(row[i_section].value or "").strip() if i_section is not None else ""
        sheet_result = str(row[i_result].value or "").strip() if i_result is not None else ""
        sheet_comment = str(row[i_comments].value or "").strip() if i_comments is not None else ""
        # Purple fill on identity/description columns → defer to other suite script reports.
        purple = any(
            _cell_rgb(row[i]) == _PLAN_PURPLE_RGB
            for i in range(min(len(row), 10))  # S.No. … TEST TYPE
        )
        rows.append(
            {
                "sno": sno if sno is not None else "",
                "id": tid,
                "desc": desc or "",
                "steps": steps or "",
                "section": section,
                "purple": purple,
                "sheet_result": sheet_result,
                "sheet_comment": sheet_comment,
            }
        )
    wb.close()
    return rows


def _badge(status: str) -> str:
    colors = {
        "PASS": ("#dcfce7", "#166534"),
        "FAIL": ("#fef2f2", "#b91c1c"),
        "NOT TESTED": ("#fff7ed", "#c2410c"),
        "NOT AVAILABLE": ("#f1f5f9", "#475569"),
    }
    bg, fg = colors.get(status, ("#f1f5f9", "#475569"))
    return (
        f'<span class="badge" style="background:{bg};color:{fg};'
        f'border:1px solid {fg}33;">{_esc(status)}</span>'
    )


def _load_testbed_summary() -> dict:
    path = ARTIFACTS_DIR / "testbed_summary.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def generate_sanity_html(
    *,
    report_json: Path,
    plan_xlsx: Path,
    out_html: Path,
    run_date: str | None = None,
    runs_dir: Path | None = None,
) -> Path:
    outcomes = _load_pytest_outcomes(report_json, runs_dir)
    plan_rows = _load_plan_rows(plan_xlsx)
    summary = _load_testbed_summary()
    date_str = run_date or datetime.now().strftime("%Y%m%d")

    total = len(plan_rows)
    passed = failed = not_tested = not_available = 0
    body_rows: list[str] = []
    for row in plan_rows:
        tid = row["id"]
        info = outcomes.get(tid)
        if info:
            status = info["status"]
            comment = info["comment"]
            auto_cls = "row-auto"
        elif tid in _NOT_AUTOMATED_CASES:
            status = "NOT TESTED"
            comment = "Not Automated"
            auto_cls = ""
        elif row.get("purple"):
            # RESULT = Not Tested; comment points at the named suite/script report.
            status = "NOT TESTED"
            report_name = _script_report_name(str(row.get("section") or ""), tid)
            comment = f"Automated Refer to {report_name}"
            auto_cls = "row-purple"
        elif str(row.get("sheet_result") or "").strip().lower() == "not available":
            status = "NOT AVAILABLE"
            sheet_com = str(row.get("sheet_comment") or "").strip()
            comment = sheet_com if sheet_com else "Not Available (per plan sheet)"
            auto_cls = "row-na"
        else:
            status = "NOT TESTED"
            comment = "Not Tested"
            auto_cls = ""

        if status == "PASS":
            passed += 1
        elif status == "FAIL":
            failed += 1
        elif status == "NOT TESTED":
            not_tested += 1
        else:
            not_available += 1
        body_rows.append(
            f'<tr class="test-row {auto_cls}" data-status="{status}">'
            f'<td>{_esc(row["sno"])}</td>'
            f'<td class="tid">{_esc(tid)}</td>'
            f'<td class="desc">{_esc(row["desc"])}</td>'
            f'<td class="steps">{_esc(row["steps"])}</td>'
            f"<td>{_badge(status)}</td>"
            f'<td class="comment">{_esc(comment)}</td>'
            f"</tr>"
        )

    bts = summary.get("bts") or {}
    cpe = summary.get("cpe") or {}
    # Lab DUT models for this Sanity run (Alpha 2 default when summary is empty).
    if not str(bts.get("model") or "").strip() or str(bts.get("model")).strip() in {"—", "-", "—"}:
        bts["model"] = "UBR655"
    if not str(cpe.get("model") or "").strip() or str(cpe.get("model")).strip() in {"—", "-", "—"}:
        cpe["model"] = "UBR655"
    su_count = int(summary.get("su_count") or len(summary.get("cpes") or []) or (1 if cpe else 0))

    def _fw(dev: dict) -> str:
        return str(
            dev.get("fw_version")
            or dev.get("fw")
            or dev.get("firmware")
            or "—"
        )

    def _ip(dev: dict) -> str:
        return str(dev.get("ip") or "—")

    def _model(dev: dict) -> str:
        return str(dev.get("model") or "—")

    bts_fw = _fw(bts)
    cpe_fw = _fw(cpe)
    bts_ip = _ip(bts)
    cpe_ip = _ip(cpe)
    bts_model = _model(bts)
    cpe_model = _model(cpe)
    auto_count = len(outcomes)
    logo = senao_logo_src()
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Senao Sanity Test Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{
  --bg:#eef2f7; --card:#fff; --border:#e2e8f0; --title:#0f172a; --muted:#64748b;
  --pass:#10b981; --fail:#ef4444; --warn:#f59e0b; --total:#1e3a8a; --na:#64748b;
}}
*{{box-sizing:border-box}}
body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:0 auto}}
.hero{{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 60%,#2563eb 100%);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center;gap:20px}}
.hero h1{{margin:0 0 6px;font-size:22px}}.hero p{{margin:0;opacity:.92;font-size:13px}}
.hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}}.hero-logo img{{height:42px;display:block}}
.container{{background:var(--card);border-radius:14px;border:1px solid #cbd5e1;box-shadow:0 4px 6px -1px rgba(15,23,42,.06);overflow:hidden;margin-bottom:18px}}
.summary-cards{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px 8px 10px;border-radius:10px;text-align:center;border:1px solid var(--border);background:#fff;box-shadow:0 1px 2px rgba(15,23,42,.04);border-bottom:3px solid var(--border)}}
.card h3{{margin:0;font-size:22px;font-weight:700;line-height:1.1}}
.card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700;letter-spacing:.4px;line-height:1.3}}
.card.stat-total{{border-bottom-color:var(--total)}} .card.stat-total h3{{color:var(--total)}} .card.stat-total p{{color:var(--total)}}
.card.stat-pass{{border-bottom-color:var(--pass)}} .card.stat-pass h3,.card.stat-pass p{{color:var(--pass)}}
.card.stat-fail{{border-bottom-color:var(--fail)}} .card.stat-fail h3,.card.stat-fail p{{color:var(--fail)}}
.card.stat-nt{{border-bottom-color:var(--warn)}} .card.stat-nt h3,.card.stat-nt p{{color:#d97706}}
.card.stat-na{{border-bottom-color:var(--na)}} .card.stat-na h3{{color:#334155}} .card.stat-na p{{color:var(--muted)}}
.row-na{{opacity:.92}}
.panel-top{{padding:8px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}}
.panel-top h2{{margin:12px 0 4px;font-size:17px;color:var(--title)}}
.panel-top .sub{{margin:0 0 14px;font-size:13px;color:#475569}}
.testbed{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--border);border-radius:12px;overflow:hidden}}
.testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}}
.testbed tr:last-child th,.testbed tr:last-child td{{border-bottom:none}}
.testbed thead th{{background:#fff;font-size:12px;font-weight:600;color:#475569;letter-spacing:.2px}}
.testbed thead th .role{{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#1e3a8a;background:#dbeafe;border-radius:999px;padding:3px 8px;margin-right:8px}}
.testbed tbody th{{width:140px;font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.45px;background:#fafbfc}}
.testbed td{{font-size:14px;font-weight:600;color:var(--title);font-family:Inter,sans-serif}}
.testbed td.mono{{font-family:Consolas,Monaco,monospace;font-size:12.5px;font-weight:500;color:#334155;word-break:break-all}}
.fw-pill{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;font-family:Consolas,Monaco,monospace;font-size:12px;font-weight:700}}
.run-meta{{display:flex;flex-wrap:wrap;gap:10px 18px;margin-top:14px;font-size:13px;color:#475569}}
.run-meta strong{{color:var(--title)}}
.meta-chip{{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;background:#fff;border:1px solid var(--border);box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.legend{{padding:14px 28px;font-size:12px;color:#475569;background:#fafbfc;border-bottom:1px solid var(--border);display:flex;flex-wrap:wrap;gap:16px;align-items:center}}
.swatch{{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:middle;margin-right:6px;border:1px solid #cbd5e1}}
.content{{padding:12px 20px 28px;overflow-x:auto}}
table.plan{{width:100%;border-collapse:collapse;font-size:13px}}
table.plan th{{position:sticky;top:0;background:#f8fafc;color:#1e3a8a;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:12px 10px;border-bottom:2px solid #cbd5e1;text-align:left;white-space:nowrap;z-index:1}}
table.plan td{{padding:10px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
table.plan tr:hover{{background:#f8fafc}}
.tid{{font-family:Consolas,Monaco,monospace;font-weight:600;color:#1e3a8a;white-space:nowrap}}
.row-auto{{box-shadow:inset 3px 0 0 #86efac}}
.row-purple{{background:#f5eef2}}
.badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700}}
.desc,.steps,.comment{{max-width:420px}}
@media (max-width:900px){{
  .summary-cards{{grid-template-columns:repeat(3,minmax(0,1fr))}}
}}
@media (max-width:560px){{
  .summary-cards{{grid-template-columns:1fr}}
}}
</style></head><body><div class="wrap">
<div class="hero"><div>
<h1>Senao Sanity Test Report</h1>
<p>Source plan: { _esc(plan_xlsx.name) } · Sheet: Sanity · Full sheet ({total} cases)</p>
</div><div class="hero-logo"><img src="{logo}" alt="Senao"/></div></div>
<div class="container">
<div class="summary-cards">
<div class="card stat-total"><h3>{total}</h3><p>Total</p></div>
<div class="card stat-pass"><h3>{passed}</h3><p>Pass</p></div>
<div class="card stat-fail"><h3>{failed}</h3><p>Fail</p></div>
<div class="card stat-nt"><h3>{not_tested}</h3><p>Not Tested</p></div>
<div class="card stat-na"><h3>{not_available}</h3><p>Not Available</p></div>
</div>
<div class="panel-top">
<h2>Testbed Summary</h2>
<p class="sub">Connected SUs: {su_count}</p>
<table class="testbed">
<thead><tr>
<th></th>
<th><span class="role">BTS</span>{_esc(bts_ip)}</th>
<th><span class="role">SU1</span>{_esc(cpe_ip)}</th>
</tr></thead>
<tbody>
<tr><th>Model</th><td>{_esc(bts_model)}</td><td>{_esc(cpe_model)}</td></tr>
<tr><th>FW Version</th><td><span class="fw-pill">{_esc(bts_fw)}</span></td><td><span class="fw-pill">{_esc(cpe_fw)}</span></td></tr>
<tr><th>IP</th><td class="mono">{_esc(bts_ip)}</td><td class="mono">{_esc(cpe_ip)}</td></tr>
</tbody>
</table>
<div class="run-meta">
<span class="meta-chip"><strong>Date</strong> {_esc(date_str)}</span>
<span class="meta-chip"><strong>Pytest automated</strong> {auto_count}</span>
<span class="meta-chip"><strong>BTS FW</strong> {_esc(bts_fw)}</span>
<span class="meta-chip"><strong>CPE FW</strong> {_esc(cpe_fw)}</span>
</div>
</div>
<div class="legend">
<span><span class="swatch" style="background:#f5eef2;border-color:#BF819E"></span> Purple = Not Tested · Automated Refer to suite report</span>
<span><span class="swatch" style="background:#fff;box-shadow:inset 3px 0 0 #86efac"></span> Green bar = Sanity automated (this run)</span>
<span>Not Available = per plan sheet RESULT</span>
<span>PASS comments: Automated · FAIL: reason</span>
</div>
<div class="content"><table class="plan"><thead><tr>
<th>S.No.</th>
<th>TESTCASE-ID</th>
<th>TEST DESCRIPTION</th>
<th>TEST STEPS</th>
<th>RESULT</th>
<th>COMMENTS</th>
</tr></thead><tbody>
{''.join(body_rows)}
</tbody></table></div>
</div></div></body></html>
"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    return out_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Sanity plan HTML report")
    parser.add_argument("--report-json", default=str(ARTIFACTS_DIR / "report.json"))
    parser.add_argument(
        "--runs-dir",
        default="",
        help="Optional case_runs dir (sanity_NN.json) merged with --report-json",
    )
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument(
        "--out",
        default="",
        help="Output HTML path (default reports/artifacts/Senao_Sanity_Report_<date>.html)",
    )
    args = parser.parse_args()
    out = Path(args.out) if args.out else ARTIFACTS_DIR / f"Senao_Sanity_Report_{args.date}.html"
    runs = Path(args.runs_dir) if args.runs_dir else None
    path = generate_sanity_html(
        report_json=Path(args.report_json),
        plan_xlsx=Path(args.plan),
        out_html=out,
        run_date=args.date,
        runs_dir=runs,
    )
    print(f"[sanity-report] wrote {path.resolve()}")


if __name__ == "__main__":
    main()
