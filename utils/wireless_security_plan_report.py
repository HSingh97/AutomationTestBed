"""Build Wireless Security plan-sheet HTML from Excel + pytest JSON."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

from utils.functional_plan_report import _human_fail_comment
from utils.senao_branding import senao_logo_src

ARTIFACTS_DIR = Path("reports/artifacts")
DEFAULT_PLAN = Path.home() / "Downloads" / "Senao UBR P2MP Test Plan_v8.0a.xlsx"
SHEET_NAME = "Wireless Security - WPA2-WPA3"


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True).replace("\n", "<br/>")


def _case_id(value: object) -> str:
    match = re.fullmatch(r"W_SECURITY_?(\d+)", str(value or "").strip(), re.I)
    return f"W_SECURITY_{int(match.group(1)):02d}" if match else ""


def _outcomes(report_json: Path) -> dict[str, dict[str, str]]:
    data = json.loads(report_json.read_text(encoding="utf-8"))
    results: dict[str, dict[str, str]] = {}
    for test in data.get("tests") or []:
        nodeid = str(test.get("nodeid") or "")
        match = re.search(r"test_w_security_(\d+)_", nodeid, re.I)
        if not match:
            continue
        case_id = f"W_SECURITY_{int(match.group(1)):02d}"
        outcome = str(test.get("outcome") or "failed").lower()
        status = "PASS" if outcome == "passed" else ("NOT AVAILABLE" if outcome == "skipped" else "FAIL")
        comment = "Automated"
        if status == "FAIL":
            longrepr = str((test.get("call") or {}).get("longrepr") or "")
            comment = f"Automated — {_human_fail_comment(longrepr)}"
        results[case_id] = {"status": status, "comment": comment}
    return results


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
        if case_id:
            rows.append(
                {
                    "sno": values[index("S.NO")],
                    "id": case_id,
                    "description": values[index("TEST DESCRIPTION")] or "",
                    "steps": values[index("TEST STEPS")] or "",
                }
            )
    workbook.close()
    return rows


def _testbed() -> dict:
    path = ARTIFACTS_DIR / "testbed_summary.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _badge(status: str) -> str:
    colors = {
        "PASS": ("#dcfce7", "#166534"),
        "FAIL": ("#fef2f2", "#b91c1c"),
        "NOT AVAILABLE": ("#f1f5f9", "#475569"),
    }
    bg, fg = colors[status]
    return f'<span class="badge" style="background:{bg};color:{fg};border:1px solid {fg}33">{status}</span>'


def generate(report_json: Path, plan: Path, output: Path, run_date: str) -> Path:
    outcomes = _outcomes(report_json)
    plan_rows = _plan_rows(plan)
    testbed = _testbed()
    bts = testbed.get("bts") or {}
    cpe = testbed.get("cpe") or {}
    body: list[str] = []
    passed = failed = not_available = 0

    for row in plan_rows:
        result = outcomes.get(str(row["id"]))
        status = result["status"] if result else "NOT AVAILABLE"
        comment = result["comment"] if result else "Not Available"
        passed += status == "PASS"
        failed += status == "FAIL"
        not_available += status == "NOT AVAILABLE"
        row_class = "row-auto" if result else ""
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

    bts_model = value(bts, "model")
    cpe_model = value(cpe, "model")
    bts_fw = value(bts, "fw_version", "fw", "firmware")
    cpe_fw = value(cpe, "fw_version", "fw", "firmware")
    bts_ip = value(bts, "ip")
    cpe_ip = value(cpe, "ip")
    logo = senao_logo_src()
    total = len(plan_rows)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Senao Wireless Security Test Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{--bg:#eef2f7;--card:#fff;--border:#e2e8f0;--title:#0f172a;--muted:#64748b;--pass:#10b981;--fail:#ef4444;--warn:#f59e0b;--total:#1e3a8a}}
*{{box-sizing:border-box}} body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:auto}} .hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a 60%,#2563eb);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center}}
.hero h1{{margin:0 0 6px;font-size:22px}} .hero p{{margin:0;font-size:13px}} .hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}} .hero-logo img{{height:42px;display:block}}
.container{{background:#fff;border:1px solid #cbd5e1;border-radius:14px;overflow:hidden}} .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px;text-align:center;border:1px solid var(--border);border-radius:10px;border-bottom:3px solid}} .card h3{{margin:0;font-size:22px}} .card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700}}
.total{{color:var(--total)}} .pass{{color:var(--pass)}} .fail{{color:var(--fail)}} .na{{color:#475569}}
.panel{{padding:12px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}} .panel h2{{font-size:17px;color:var(--title)}}
.testbed{{width:100%;border-collapse:collapse;background:#fff}} .testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left}} .testbed tbody th{{width:140px;color:var(--muted);font-size:11px;text-transform:uppercase}} .role{{background:#dbeafe;color:#1e3a8a;border-radius:999px;padding:3px 8px;margin-right:8px;font-size:10px}}
.meta{{margin-top:14px;font-size:13px}} .content{{padding:12px 20px 28px;overflow:auto}} .plan{{width:100%;border-collapse:collapse;font-size:13px}} .plan th{{background:#f8fafc;color:#1e3a8a;padding:12px 10px;border-bottom:2px solid #cbd5e1;text-align:left;font-size:11px}} .plan td{{padding:10px;border-bottom:1px solid var(--border);vertical-align:top;max-width:420px}}
.tid{{font-family:monospace;color:#1e3a8a;font-weight:600;white-space:nowrap}} .row-auto{{box-shadow:inset 3px 0 #86efac}} .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}}
@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1>Senao Wireless Security Test Report</h1><p>Source plan: {_esc(plan.name)} · Sheet: {_esc(SHEET_NAME)} · Full sheet ({total} cases)</p></div><div class="hero-logo"><img src="{logo}" alt="Senao"></div></div>
<div class="container"><div class="cards">
<div class="card total"><h3>{total}</h3><p>Total</p></div><div class="card pass"><h3>{passed}</h3><p>Pass</p></div>
<div class="card fail"><h3>{failed}</h3><p>Fail</p></div><div class="card na"><h3>{not_available}</h3><p>Not Available</p></div></div>
<div class="panel"><h2>Testbed Summary</h2><table class="testbed"><thead><tr><th></th><th><span class="role">BTS</span>{_esc(bts_ip)}</th><th><span class="role">SU1</span>{_esc(cpe_ip)}</th></tr></thead>
<tbody><tr><th>Model</th><td>{_esc(bts_model)}</td><td>{_esc(cpe_model)}</td></tr><tr><th>FW Version</th><td>{_esc(bts_fw)}</td><td>{_esc(cpe_fw)}</td></tr><tr><th>IP</th><td>{_esc(bts_ip)}</td><td>{_esc(cpe_ip)}</td></tr></tbody></table>
<div class="meta"><strong>Date:</strong> {_esc(run_date)} &nbsp; · &nbsp; <strong>Pytest automated:</strong> {len(outcomes)}</div></div>
<div class="content"><table class="plan"><thead><tr><th>S.No.</th><th>TESTCASE-ID</th><th>TEST DESCRIPTION</th><th>TEST STEPS</th><th>RESULT</th><th>COMMENTS</th></tr></thead><tbody>
{''.join(body)}
</tbody></table></div></div></div></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-json", default=str(ARTIFACTS_DIR / "wireless_security_report.json"))
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    output = Path(args.out) if args.out else ARTIFACTS_DIR / f"Senao_Wireless_Security_Report_{args.date}.html"
    result = generate(Path(args.report_json), Path(args.plan), output, args.date)
    print(f"[wireless-security-report] wrote {result.resolve()}")


if __name__ == "__main__":
    main()
