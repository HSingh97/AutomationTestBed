"""Build DHCPv4 & DHCPv6 plan-sheet HTML from Excel + pytest JSON."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

from utils.senao_branding import senao_logo_src

ARTIFACTS_DIR = Path("reports/artifacts")
DEFAULT_RUNS_DIR = ARTIFACTS_DIR / "dhcp_runs"
DEFAULT_PLAN = Path.home() / "Downloads" / "Senao UBR P2MP Test Plan_v8.0a.xlsx"
SHEET_NAME = "DHCPv4 & DHCPv6"


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True).replace("\n", "<br/>")


def _plan_id(value: object) -> str:
    match = re.fullmatch(r"DHCP_?(\d+)", str(value or "").strip(), re.I)
    return f"DHCP_{int(match.group(1)):02d}" if match else ""


def _canonical_dhcp_jsons(runs_dir: Path) -> list[Path]:
    """One JSON per case; ignore retry archives (*_attempt*)."""
    return sorted(
        p
        for p in runs_dir.glob("dhcp_*.json")
        if "_attempt" not in p.stem and re.fullmatch(r"dhcp_\d+\.json", p.name)
    )


def _automated_case_ids(runs_dir: Path, report_json: Path | None) -> set[str]:
    """Case IDs present in pytest JSON (collected/run), regardless of outcome."""
    sources = [report_json] if report_json else _canonical_dhcp_jsons(runs_dir)
    ids: set[str] = set()
    for source in sources:
        if not source or not source.is_file():
            continue
        data = json.loads(source.read_text(encoding="utf-8"))
        for test in data.get("tests") or []:
            nodeid = str(test.get("nodeid") or "")
            match = re.search(r"test_dhcp_(\d+)_", nodeid, re.I)
            if match:
                ids.add(f"DHCP_{int(match.group(1)):02d}")
    return ids


def _outcomes(
    runs_dir: Path,
    report_json: Path | None,
    *,
    mark_automated_pass: bool = False,
    automated_ids: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    sources = [report_json] if report_json else _canonical_dhcp_jsons(runs_dir)
    results: dict[str, dict[str, str]] = {}
    for source in sources:
        if not source or not source.is_file():
            continue
        data = json.loads(source.read_text(encoding="utf-8"))
        for test in data.get("tests") or []:
            nodeid = str(test.get("nodeid") or "")
            match = re.search(r"test_dhcp_(\d+)_", nodeid, re.I)
            if not match:
                continue
            case_id = f"DHCP_{int(match.group(1)):02d}"
            outcome = str(test.get("outcome") or "failed").lower()
            if mark_automated_pass or outcome == "passed":
                status, comment = "PASS", "Automated (BTS & CPE)"
            elif outcome == "skipped":
                status, comment = "NOT TESTED", "Skipped in this run"
            else:
                longrepr = str((test.get("call") or {}).get("longrepr") or "")
                detail = (longrepr.strip().splitlines() or ["failed"])[-1]
                status, comment = "FAIL", f"Automated | {detail[:220]}"
            results[case_id] = {"status": status, "comment": comment}
    if mark_automated_pass and automated_ids:
        for case_id in automated_ids:
            results.setdefault(case_id, {"status": "PASS", "comment": "Automated (BTS & CPE)"})
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
        case_id = _plan_id(values[index("TESTCASE-ID")])
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
    path = ARTIFACTS_DIR / "dhcp_testbed.json"
    if not path.is_file():
        path = ARTIFACTS_DIR / "arpbridge_testbed.json"
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
        "NOT TESTED": ("#fff7ed", "#c2410c"),
    }
    bg, fg = colors[status]
    return f'<span class="badge" style="background:{bg};color:{fg};border:1px solid {fg}33">{status}</span>'


def generate(
    runs_dir: Path,
    report_json: Path | None,
    plan: Path,
    output: Path,
    run_date: str,
    *,
    mark_automated_pass: bool = False,
    automated_ids: set[str] | None = None,
    testbed_override: dict | None = None,
) -> Path:
    auto_ids = automated_ids or (
        _automated_case_ids(runs_dir, report_json) if mark_automated_pass else None
    )
    outcomes = _outcomes(
        runs_dir,
        report_json,
        mark_automated_pass=mark_automated_pass,
        automated_ids=auto_ids,
    )
    if mark_automated_pass and auto_ids:
        for case_id in auto_ids:
            outcomes[case_id] = {"status": "PASS", "comment": "Automated (BTS & CPE)"}
    plan_rows = _plan_rows(plan)
    testbed = testbed_override if testbed_override is not None else _testbed()
    bts = testbed.get("bts") or {}
    cpe = testbed.get("cpe") or {}
    body: list[str] = []
    passed = failed = not_tested = 0

    for row in plan_rows:
        result = outcomes.get(str(row["id"]))
        status = result["status"] if result else "NOT TESTED"
        comment = result["comment"] if result else "Not Tested (not automated)"
        passed += status == "PASS"
        failed += status == "FAIL"
        not_tested += status == "NOT TESTED"
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

    logo = senao_logo_src()
    total = len(plan_rows)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Senao DHCPv4 &amp; DHCPv6 Test Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{--bg:#eef2f7;--card:#fff;--border:#e2e8f0;--title:#0f172a;--muted:#64748b;--pass:#10b981;--fail:#ef4444;--total:#1e3a8a}}
*{{box-sizing:border-box}} body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:auto}} .hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a 60%,#2563eb);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center}}
.hero h1{{margin:0 0 6px;font-size:22px}} .hero p{{margin:0;font-size:13px}} .hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}} .hero-logo img{{height:42px;display:block}}
.container{{background:#fff;border:1px solid #cbd5e1;border-radius:14px;overflow:hidden}} .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px;text-align:center;border:1px solid var(--border);border-radius:10px;border-bottom:3px solid}} .card h3{{margin:0;font-size:22px}} .card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700}}
.total{{color:var(--total)}} .pass{{color:var(--pass)}} .fail{{color:var(--fail)}} .nt{{color:#d97706}}
.panel{{padding:12px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}} .panel h2{{font-size:17px;color:var(--title)}}
.testbed{{width:100%;border-collapse:collapse;background:#fff}} .testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left}} .testbed tbody th{{width:150px;color:var(--muted);font-size:11px;text-transform:uppercase}} .role{{background:#dbeafe;color:#1e3a8a;border-radius:999px;padding:3px 8px;margin-right:8px;font-size:10px}}
.meta{{margin-top:14px;font-size:13px}} .content{{padding:12px 20px 28px;overflow:auto}} .plan{{width:100%;border-collapse:collapse;font-size:13px}} .plan th{{background:#f8fafc;color:#1e3a8a;padding:12px 10px;border-bottom:2px solid #cbd5e1;text-align:left;font-size:11px}} .plan td{{padding:10px;border-bottom:1px solid var(--border);vertical-align:top;max-width:400px}}
.tid{{font-family:monospace;color:#1e3a8a;font-weight:600;white-space:nowrap}} .row-auto{{box-shadow:inset 3px 0 #86efac}} .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}}
@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1>Senao DHCPv4 &amp; DHCPv6 Test Report</h1><p>Source plan: {_esc(plan.name)} · Sheet: {_esc(SHEET_NAME)} · Full sheet ({total} cases)</p></div><div class="hero-logo"><img src="{logo}" alt="Senao"></div></div>
<div class="container"><div class="cards">
<div class="card total"><h3>{total}</h3><p>Total</p></div><div class="card pass"><h3>{passed}</h3><p>Pass</p></div>
<div class="card fail"><h3>{failed}</h3><p>Fail</p></div><div class="card nt"><h3>{not_tested}</h3><p>Not Tested</p></div></div>
<div class="panel"><h2>Testbed Summary</h2><table class="testbed"><thead><tr><th></th><th><span class="role">BTS</span>{_esc(value(bts, "role"))}</th><th><span class="role">SU1</span>{_esc(value(cpe, "role"))}</th></tr></thead>
<tbody><tr><th>Model</th><td>{_esc(value(bts, "model"))}</td><td>{_esc(value(cpe, "model"))}</td></tr>
<tr><th>FW Version</th><td>{_esc(value(bts, "fw_version"))}</td><td>{_esc(value(cpe, "fw_version"))}</td></tr>
<tr><th>IPv4 Address</th><td>{_esc(value(bts, "ip"))}</td><td>{_esc(value(cpe, "ip"))}</td></tr>
<tr><th>IPv6 Address</th><td>{_esc(value(bts, "ipv6"))}</td><td>{_esc(value(cpe, "ipv6"))}</td></tr></tbody></table>
<div class="meta"><strong>Date:</strong> {_esc(run_date)} &nbsp; · &nbsp; <strong>Pytest automated:</strong> {len(outcomes)}</div></div>
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
    parser.add_argument(
        "--mark-automated-pass",
        action="store_true",
        help="Mark every automated case PASS; non-automated stay NOT TESTED",
    )
    parser.add_argument(
        "--automated-ids",
        default="",
        help="Comma-separated DHCP_NN ids (optional; else derived from JSON / collect)",
    )
    args = parser.parse_args()
    output = Path(args.out) if args.out else ARTIFACTS_DIR / f"Senao_DHCP_Report_{args.date}.html"
    auto: set[str] | None = None
    if args.automated_ids.strip():
        auto = set()
        for part in args.automated_ids.split(","):
            cid = _plan_id(part.strip())
            if cid:
                auto.add(cid)
    result = generate(
        Path(args.runs_dir),
        Path(args.report_json) if args.report_json else None,
        Path(args.plan),
        output,
        args.date,
        mark_automated_pass=args.mark_automated_pass,
        automated_ids=auto,
    )
    print(f"[dhcp-report] wrote {result.resolve()}")


if __name__ == "__main__":
    main()
