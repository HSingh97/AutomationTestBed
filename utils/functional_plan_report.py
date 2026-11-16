"""Build Senao Functional plan-sheet HTML from Excel + pytest report.json.

Columns: S.No., TESTCASE-ID, TEST DESCRIPTION, TEST STEPS, RESULT, COMMENTS
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
SHEET_NAME = "Functional"


def _esc(text: object) -> str:
    return html_module.escape(str(text or ""), quote=True).replace("\n", "<br/>")


def _normalize_ft_id(raw: str) -> str:
    m = re.match(r"FT_?(\d+)$", str(raw or "").strip().upper())
    if not m:
        return ""
    return f"FT_{int(m.group(1)):02d}"


def _human_fail_comment(longrepr: str) -> str:
    """Turn pytest/scrapli traceback into a short report comment (no library paths)."""
    text = str(longrepr or "").strip()
    if not text:
        return "Failed"

    checks = re.findall(r"FAILURE:\s*(?:check\s[^:\n]*:\s*)?(.+)", text)
    if checks:
        msg = checks[-1].strip().splitlines()[0].strip()
        msg = re.sub(r"^check\s+bool\(False\):\s*", "", msg, flags=re.I)
        return _shorten_fail_reason(msg)

    exception_msgs: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^E\s+(\S.+)$", line)
        if not m:
            continue
        msg = m.group(1).strip()
        if re.search(r"CancelledError$|TimeoutError\(\)|TimeoutError$", msg):
            continue
        if "site-packages" in msg or msg.startswith("/"):
            continue
        if ": " in msg:
            _cls, rest = msg.split(": ", 1)
            if rest.strip():
                msg = rest.strip()
        exception_msgs.append(msg)
    if exception_msgs:
        return _shorten_fail_reason(exception_msgs[-1])

    if "timed out reading from transport" in text.lower() or "scraplitimeout" in text.lower():
        return _shorten_fail_reason("timed out reading from transport")
    if "reconnect timeout" in text.lower():
        m = re.search(r"(CPE|BTS) SSH reconnect timeout[^\n]*", text, flags=re.I)
        return _shorten_fail_reason(m.group(0) if m else "SSH reconnect timeout")
    return "Failed — see case log"


def _shorten_fail_reason(msg: str) -> str:
    raw = re.sub(r"\s+", " ", str(msg or "")).strip(" |")
    lower = raw.lower()
    if "timed out reading from transport" in lower or "scraplitimeout" in lower:
        return "SSH timed out while applying config (device dropped the session)"
    if "reconnect timeout" in lower:
        return raw[:180]
    if "auto-logout" in lower:
        return raw[:180]
    if "rate changed too much" in lower:
        return raw[:180]
    if len(raw) > 180:
        return raw[:177] + "..."
    return raw or "Failed"


def _load_pytest_outcomes(report_json: Path) -> dict[str, dict]:
    """Map FT_NN → {status, comment} from pytest-json-report."""
    out: dict[str, dict] = {}
    if not report_json.is_file():
        return out
    data = json.loads(report_json.read_text(encoding="utf-8"))
    for test in data.get("tests") or []:
        nodeid = str(test.get("nodeid") or "")
        keywords = test.get("keywords") or []
        case_id = ""
        for kw in keywords:
            case_id = _normalize_ft_id(str(kw))
            if case_id:
                break
        if not case_id:
            m = re.search(r"test_ft_(\d+)_", nodeid, re.I)
            if m:
                case_id = f"FT_{int(m.group(1)):02d}"
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
            longrepr = str(call.get("longrepr") or test.get("longrepr") or "")
            comment = f"Automated — {_human_fail_comment(longrepr)}"
        out[case_id] = {"status": status, "comment": comment, "automated": True}
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


_PLAN_PURPLE_RGB = "FFBF819E"


def _load_plan_rows(plan_xlsx: Path) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(plan_xlsx, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        raise RuntimeError(f"No '{SHEET_NAME}' sheet in {plan_xlsx}")
    ws = wb[SHEET_NAME]
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
    i_result = col("RESULT")
    i_comments = col("COMMENTS", "COMMENT")
    rows: list[dict] = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        if i_id is None:
            continue
        tid = _normalize_ft_id(str(row[i_id].value or ""))
        if not tid:
            continue
        sno = row[i_sno].value if i_sno is not None else ""
        desc = row[i_desc].value if i_desc is not None else ""
        steps = row[i_steps].value if i_steps is not None else ""
        sheet_result = str(row[i_result].value or "").strip() if i_result is not None else ""
        sheet_comment = str(row[i_comments].value or "").strip() if i_comments is not None else ""
        purple = any(_cell_rgb(row[i]) == _PLAN_PURPLE_RGB for i in range(min(len(row), 10)))
        rows.append(
            {
                "sno": sno if sno is not None else "",
                "id": tid,
                "desc": desc or "",
                "steps": steps or "",
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


def generate_functional_html(
    *,
    report_json: Path,
    plan_xlsx: Path,
    out_html: Path,
    run_date: str | None = None,
) -> Path:
    outcomes = _load_pytest_outcomes(report_json)
    plan_rows = _load_plan_rows(plan_xlsx)
    summary = _load_testbed_summary()
    date_str = run_date or datetime.now().strftime("%Y%m%d")

    total = len(plan_rows)
    passed = failed = not_tested = 0
    body_rows: list[str] = []
    for row in plan_rows:
        tid = row["id"]
        info = outcomes.get(tid)
        if info:
            status = info["status"]
            comment = info["comment"]
            auto_cls = "row-auto"
        else:
            # Every case not executed in this run is reported uniformly as Not Tested.
            status = "NOT TESTED"
            comment = "Not Tested"
            auto_cls = "row-purple" if row.get("purple") else ""

        if status == "PASS":
            passed += 1
        elif status == "FAIL":
            failed += 1
        elif status == "NOT TESTED":
            not_tested += 1
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
    su_count = int(summary.get("su_count") or len(summary.get("cpes") or []) or (1 if cpe else 0))

    def _fw(dev: dict) -> str:
        return str(dev.get("fw_version") or dev.get("fw") or dev.get("firmware") or "—")

    bts_fw, cpe_fw = _fw(bts), _fw(cpe)
    bts_ip = str(bts.get("ip") or "—")
    cpe_ip = str(cpe.get("ip") or "—")
    bts_model = str(bts.get("model") or "—")
    cpe_model = str(cpe.get("model") or "—")
    auto_count = len(outcomes)
    logo = senao_logo_src()

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Senao Functional Test Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{
  --bg:#eef2f7; --card:#fff; --border:#e2e8f0; --title:#0f172a; --muted:#64748b;
  --pass:#10b981; --fail:#ef4444; --warn:#f59e0b; --total:#1e3a8a;
}}
*{{box-sizing:border-box}}
body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:0 auto}}
.hero{{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 60%,#2563eb 100%);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center;gap:20px}}
.hero h1{{margin:0 0 6px;font-size:22px}}.hero p{{margin:0;opacity:.92;font-size:13px}}
.hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}}.hero-logo img{{height:42px;display:block}}
.container{{background:var(--card);border-radius:14px;border:1px solid #cbd5e1;box-shadow:0 4px 6px -1px rgba(15,23,42,.06);overflow:hidden;margin-bottom:18px}}
.summary-cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px 8px 10px;border-radius:10px;text-align:center;border:1px solid var(--border);background:#fff;box-shadow:0 1px 2px rgba(15,23,42,.04);border-bottom:3px solid var(--border)}}
.card h3{{margin:0;font-size:22px;font-weight:700;line-height:1.1}}
.card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700;letter-spacing:.4px;line-height:1.3}}
.card.stat-total{{border-bottom-color:var(--total)}} .card.stat-total h3{{color:var(--total)}} .card.stat-total p{{color:var(--total)}}
.card.stat-pass{{border-bottom-color:var(--pass)}} .card.stat-pass h3,.card.stat-pass p{{color:var(--pass)}}
.card.stat-fail{{border-bottom-color:var(--fail)}} .card.stat-fail h3,.card.stat-fail p{{color:var(--fail)}}
.card.stat-nt{{border-bottom-color:var(--warn)}} .card.stat-nt h3,.card.stat-nt p{{color:#d97706}}
.panel-top{{padding:8px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}}
.panel-top h2{{margin:12px 0 4px;font-size:17px;color:var(--title)}}
.panel-top .sub{{margin:0 0 14px;font-size:13px;color:#475569}}
.testbed{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--border);border-radius:12px;overflow:hidden}}
.testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}}
.testbed tr:last-child th,.testbed tr:last-child td{{border-bottom:none}}
.testbed thead th{{background:#fff;font-size:12px;font-weight:600;color:#475569}}
.testbed thead th .role{{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#1e3a8a;background:#dbeafe;border-radius:999px;padding:3px 8px;margin-right:8px}}
.testbed tbody th{{width:140px;font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.45px;background:#fafbfc}}
.testbed td{{font-size:14px;font-weight:600;color:var(--title)}}
.testbed td.mono{{font-family:Consolas,Monaco,monospace;font-size:12.5px;font-weight:500;color:#334155;word-break:break-all}}
.fw-pill{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;font-family:Consolas,Monaco,monospace;font-size:12px;font-weight:700}}
.run-meta{{display:flex;flex-wrap:wrap;gap:10px 18px;margin-top:14px;font-size:13px;color:#475569}}
.run-meta strong{{color:var(--title)}}
.meta-chip{{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;background:#fff;border:1px solid var(--border)}}
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
@media (max-width:900px){{.summary-cards{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
@media (max-width:560px){{.summary-cards{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<div class="hero"><div>
<h1>Senao Functional Test Report</h1>
<p>Source plan: {_esc(plan_xlsx.name)} · Sheet: Functional · Full sheet ({total} cases)</p>
</div><div class="hero-logo"><img src="{logo}" alt="Senao"/></div></div>
<div class="container">
<div class="summary-cards">
<div class="card stat-total"><h3>{total}</h3><p>Total</p></div>
<div class="card stat-pass"><h3>{passed}</h3><p>Pass</p></div>
<div class="card stat-fail"><h3>{failed}</h3><p>Fail</p></div>
<div class="card stat-nt"><h3>{not_tested}</h3><p>Not Tested</p></div>
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
<span><span class="swatch" style="background:#fff;box-shadow:inset 3px 0 0 #86efac"></span> Green bar = Functional automated (this run)</span>
<span>Not Tested = not executed in this run</span>
<span>PASS comments: Automated · FAIL: short reason (no traceback)</span>
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
    parser = argparse.ArgumentParser(description="Generate Functional plan HTML report")
    parser.add_argument("--report-json", default=str(ARTIFACTS_DIR / "functional_report.json"))
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    out = Path(args.out) if args.out else ARTIFACTS_DIR / f"Senao_Functional_Report_{args.date}.html"
    path = generate_functional_html(
        report_json=Path(args.report_json),
        plan_xlsx=Path(args.plan),
        out_html=out,
        run_date=args.date,
    )
    print(f"[functional-report] wrote {path.resolve()}")


if __name__ == "__main__":
    main()
