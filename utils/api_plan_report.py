"""Build API plan-sheet HTML from Excel + pytest JSON run artifacts."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

from utils.senao_branding import senao_logo_src

ARTIFACTS_DIR = Path("reports/artifacts")
DEFAULT_PLAN = Path.home() / "Downloads" / "Senao UBR P2MP Test Plan_v8.0a.xlsx"
DEFAULT_RUNS_DIR = ARTIFACTS_DIR / "api_runs"
SHEET_NAME = "API"


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True).replace("\n", "<br/>")


def _case_id(value: object) -> str:
    match = re.fullmatch(r"API_?(\d+)", str(value or "").strip(), re.I)
    return f"API_{int(match.group(1)):02d}" if match else ""


def _outcomes_from_json(report_json: Path) -> dict[str, dict[str, str]]:
    data = json.loads(report_json.read_text(encoding="utf-8"))
    results: dict[str, dict[str, str]] = {}
    for test in data.get("tests") or []:
        nodeid = str(test.get("nodeid") or "")
        match = re.search(r"test_api_(\d+)_", nodeid, re.I)
        if not match:
            continue
        case_id = f"API_{int(match.group(1)):02d}"
        outcome = str(test.get("outcome") or "failed").lower()
        if outcome == "passed":
            status = "PASS"
        elif outcome == "skipped":
            status = "NOT AVAILABLE"
        else:
            status = "FAIL"
        comment = "Automated"
        if status == "FAIL":
            longrepr = str((test.get("call") or {}).get("longrepr") or "")
            detail = (longrepr.strip().splitlines() or ["failed"])[-1]
            comment = f"Automated | {detail[:220]}"
        results[case_id] = {"status": status, "comment": comment}
    return results


def _outcomes(runs_dir: Path, report_json: Path | None) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    if report_json and report_json.is_file():
        results.update(_outcomes_from_json(report_json))
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("API_*.json")):
            results.update(_outcomes_from_json(path))
    return results


def _sheet_result_label(raw: object) -> str:
    """Map plan-sheet RESULT text to report badge labels."""
    text = str(raw or "").strip().lower().replace("\xa0", " ")
    if not text:
        return ""
    if "not available" in text:
        return "NOT AVAILABLE"
    if "not tested" in text:
        return "NOT TESTED"
    if text in {"pass", "passed"}:
        return "PASS"
    if text in {"fail", "failed"}:
        return "FAIL"
    return ""


def _plan_rows(plan: Path) -> list[dict[str, object]]:
    import openpyxl

    workbook = openpyxl.load_workbook(plan, data_only=True)
    sheet = workbook[SHEET_NAME]
    headers = [str(cell.value or "").strip().upper() for cell in sheet[1]]

    def index(name: str) -> int:
        return headers.index(name)

    rows: list[dict[str, object]] = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        case_id = _case_id(values[index("TESTCASE-ID")])
        if not case_id:
            continue
        desc = values[index("TEST DESCRIPTION")] or ""
        steps = values[index("TEST STEPS")] or ""
        expected = ""
        if "EXPECTED RESULT" in headers:
            expected = values[index("EXPECTED RESULT")] or ""
        if not str(desc).strip() and expected:
            desc = expected
        sheet_result = ""
        sheet_comment = ""
        if "RESULT" in headers:
            sheet_result = _sheet_result_label(values[index("RESULT")])
        if "COMMENTS" in headers:
            sheet_comment = str(values[index("COMMENTS")] or "").replace("\xa0", " ").strip()
        rows.append(
            {
                "sno": values[index("S.NO")],
                "id": case_id,
                "description": desc,
                "steps": steps,
                "sheet_result": sheet_result,
                "sheet_comment": sheet_comment,
            }
        )
    workbook.close()
    return rows


def _testbed() -> dict:
    for name in ("api_testbed.json", "testbed_summary.json"):
        path = ARTIFACTS_DIR / name
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _badge(status: str) -> str:
    colors = {
        "PASS": ("#dcfce7", "#166534"),
        "FAIL": ("#fef2f2", "#b91c1c"),
        "NOT AVAILABLE": ("#f1f5f9", "#475569"),
        "NOT TESTED": ("#fff7ed", "#c2410c"),
    }
    bg, fg = colors.get(status, colors["NOT AVAILABLE"])
    return f'<span class="badge" style="background:{bg};color:{fg};border:1px solid {fg}33">{status}</span>'


def _ubr(model: str) -> str:
    return model.replace("EOC", "UBR") if model and model != "—" else model


def generate(
    runs_dir: Path,
    report_json: Path | None,
    plan: Path,
    output: Path,
    run_date: str,
) -> Path:
    outcomes = _outcomes(runs_dir, report_json)
    plan_rows = _plan_rows(plan)
    testbed = _testbed()
    bts = testbed.get("bts") or {}
    cpe = testbed.get("cpe") or {}
    body: list[str] = []
    passed = failed = not_available = not_tested = 0

    for row in plan_rows:
        result = outcomes.get(str(row["id"]))
        if result:
            status = result["status"]
            comment = result["comment"]
            row_class = "row-auto"
        else:
            # Follow the plan sheet: only cases marked Not Available stay that way;
            # everything else we did not run in this automation pass is Not Tested.
            sheet_status = str(row.get("sheet_result") or "")
            if sheet_status == "NOT AVAILABLE":
                status = "NOT AVAILABLE"
                comment = str(row.get("sheet_comment") or "Not Available")
            else:
                status = "NOT TESTED"
                comment = "Not Tested (not automated)"
            row_class = ""
        passed += status == "PASS"
        failed += status == "FAIL"
        not_available += status == "NOT AVAILABLE"
        not_tested += status == "NOT TESTED"
        body.append(
            f'<tr class="{row_class}" data-status="{status}">'
            f'<td>{_esc(row["sno"])}</td><td class="tid">{_esc(row["id"])}</td>'
            f'<td>{_esc(row["description"])}</td><td>{_esc(row["steps"])}</td>'
            f"<td>{_badge(status)}</td><td>{_esc(comment)}</td></tr>"
        )

    def value(device: dict, *keys: str) -> str:
        for key in keys:
            if device.get(key) and device[key] != "—":
                return str(device[key])
        return "—"

    logo = senao_logo_src()
    total = len(plan_rows)
    bts_model = _ubr(value(bts, "model"))
    cpe_model = _ubr(value(cpe, "model"))
    bts_ip = value(bts, "ipv6", "ip")
    cpe_ip = value(cpe, "ipv6", "ip")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Senao API Test Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{--bg:#eef2f7;--card:#fff;--border:#e2e8f0;--title:#0f172a;--muted:#64748b;--pass:#10b981;--fail:#ef4444;--total:#1e3a8a}}
*{{box-sizing:border-box}} body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:auto}} .hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a 60%,#2563eb);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center}}
.hero h1{{margin:0 0 6px;font-size:22px}} .hero p{{margin:0;font-size:13px}} .hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}} .hero-logo img{{height:42px;display:block}}
.container{{background:#fff;border:1px solid #cbd5e1;border-radius:14px;overflow:hidden}} .cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px;text-align:center;border:1px solid var(--border);border-radius:10px;border-bottom:3px solid}} .card h3{{margin:0;font-size:22px}} .card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700}}
.total{{color:var(--total)}} .pass{{color:var(--pass)}} .fail{{color:var(--fail)}} .na{{color:#475569}} .nt{{color:#c2410c}}
.panel{{padding:12px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}} .panel h2{{font-size:17px;color:var(--title)}}
.testbed{{width:100%;border-collapse:collapse;background:#fff}} .testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left}} .testbed tbody th{{width:140px;color:var(--muted);font-size:11px;text-transform:uppercase}} .role{{background:#dbeafe;color:#1e3a8a;border-radius:999px;padding:3px 8px;margin-right:8px;font-size:10px}}
.meta{{margin-top:14px;font-size:13px}} .content{{padding:12px 20px 28px;overflow:auto}} .plan{{width:100%;border-collapse:collapse;font-size:13px}} .plan th{{background:#f8fafc;color:#1e3a8a;padding:12px 10px;border-bottom:2px solid #cbd5e1;text-align:left;font-size:11px}} .plan td{{padding:10px;border-bottom:1px solid var(--border);vertical-align:top;max-width:420px}}
.tid{{font-family:monospace;color:#1e3a8a;font-weight:600;white-space:nowrap}} .row-auto{{box-shadow:inset 3px 0 #86efac}} .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}}
@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1>Senao API Test Report</h1><p>Source plan: {_esc(plan.name)} · Sheet: {_esc(SHEET_NAME)} · Full sheet ({total} cases)</p></div><div class="hero-logo"><img src="{logo}" alt="Senao"></div></div>
<div class="container"><div class="cards">
<div class="card total"><h3>{total}</h3><p>Total</p></div><div class="card pass"><h3>{passed}</h3><p>Pass</p></div>
<div class="card fail"><h3>{failed}</h3><p>Fail</p></div>
<div class="card na"><h3>{not_available}</h3><p>Not Available</p></div>
<div class="card nt"><h3>{not_tested}</h3><p>Not Tested</p></div></div>
<div class="panel"><h2>Testbed Summary</h2><table class="testbed"><thead><tr><th></th><th><span class="role">BTS</span></th><th><span class="role">SU1</span></th></tr></thead>
<tbody><tr><th>Model</th><td>{_esc(bts_model)}</td><td>{_esc(cpe_model)}</td></tr>
<tr><th>FW Version</th><td>{_esc(value(bts, "fw_version", "fw", "firmware"))}</td><td>{_esc(value(cpe, "fw_version", "fw", "firmware"))}</td></tr>
<tr><th>IPv6 Address</th><td>{_esc(bts_ip)}</td><td>{_esc(cpe_ip)}</td></tr></tbody></table>
<div class="meta"><strong>Date:</strong> {_esc(run_date)} &nbsp; · &nbsp; <strong>Pytest automated:</strong> {len(outcomes)} &nbsp; · &nbsp; <strong>CPE API:</strong> http://169.254.254.1 (2.4 GHz mgmt)</div></div>
<div class="content"><table class="plan"><thead><tr><th>S.No.</th><th>TESTCASE-ID</th><th>TEST DESCRIPTION</th><th>TEST STEPS</th><th>RESULT</th><th>COMMENTS</th></tr></thead><tbody>
{''.join(body)}
</tbody></table></div></div></div></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    parser.add_argument("--report-json", default="")
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    output = Path(args.out) if args.out else ARTIFACTS_DIR / f"Senao_API_Report_{args.date}.html"
    report_json = Path(args.report_json) if args.report_json else None
    result = generate(Path(args.runs_dir), report_json, Path(args.plan), output, args.date)
    print(f"[api-report] wrote {result.resolve()}")


if __name__ == "__main__":
    main()
