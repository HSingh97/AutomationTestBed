"""Build Senao Link Test (BTS-AT-SW-01) HTML report from pytest JSON.

Sheet cases from plan images + automated suite markers.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
from datetime import datetime
from pathlib import Path

from utils.senao_branding import senao_logo_src

# Plan rows: (sno, tid, description, steps, target, category)
PLAN_CASES: list[tuple[int, str, str, str, str, str]] = [
    (3, "LINK_TEST_03", "Sequential UL/DL", "Run two UL/DL sessions sequentially on BTS; no overlap", "BTS", "Functional"),
    (4, "LINK_TEST_04", "Long Duration Test", "Run UL/DL for extended duration; session stays stable", "BTS", "Stress"),
    (5, "LINK_TEST_05", "High Bandwidth 80 MHz", "Run UL/DL at 80 MHz / high tool bandwidth", "BTS", "Functional"),
    (7, "LINK_TEST_07", "BTS Reboot During Test", "Start UL/DL, reboot BTS mid-test; session ends, BTS recovers", "BTS", "Functional"),
    (8, "LINK_TEST_08", "Client Reconnect After Drop", "Disconnect CPE mid UL/DL; reconnect; BTS accepts new session", "CPE", "Functional"),
    (13, "LINK_TEST_13", "Excessive Duration Test", "Run UL/DL beyond configured max duration", "BTS", "Stress"),
    (15, "LINK_TEST_15", "Valid Speed From CPE", "CPE GUI UL/DL with exactly 1 STA on BTS; metrics returned", "CPE", "Functional"),
    (17, "LINK_TEST_17", "CPE Reboot During Test", "Start UL/DL test, reboot CPE mid-test", "CPE", "Functional"),
    (18, "LINK_TEST_18", "Network Congestion", "Run UL/DL test under heavy traffic", "CPE", "Negative"),
    (19, "LINK_TEST_19", "Packet Loss Simulation", "Run UL/DL test with induced packet loss", "CPE", "Functional"),
    (20, "LINK_TEST_20", "Latency Simulation", "Run UL/DL test with high latency", "CPE", "Functional"),
    (22, "LINK_TEST_22", "Server Unreachable", "Run UL/DL test when BTS server is down", "CPE", "Negative"),
    (23, "LINK_TEST_23", "Metrics Validation", "Run UL/DL test and validate reported metrics", "CPE", "Functional"),
    (24, "LINK_TEST_24", "Rapid Repeated Tests", "Run UL/DL tests repeatedly in short intervals", "CPE", "Functional"),
]


def _esc(text: object) -> str:
    return html_module.escape(str(text or ""), quote=True).replace("\n", "<br/>")


def _case_key(n: int) -> str:
    """Match pytest markers / plan IDs: LINK_TEST_03, LINK_TEST_13, …"""
    return f"LINK_TEST_{n:02d}" if n < 10 else f"LINK_TEST_{n}"


def _short_fail(longrepr: str) -> str:
    text = str(longrepr or "").strip()
    if not text:
        return "Failed"
    checks = re.findall(r"FAILURE:\s*(?:check\s[^:\n]*:\s*)?(.+)", text)
    if checks:
        return checks[-1].strip().splitlines()[0].strip()[:180]
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^E\s+(\S.+)$", line)
        if not m:
            continue
        msg = m.group(1).strip()
        if ": " in msg:
            msg = msg.split(": ", 1)[1].strip()
        if msg and "site-packages" not in msg:
            return msg[:180]
    if "BTS↔CPE link not up" in text or "link not up" in text.lower():
        return "BTS↔CPE link not up (no station)"
    return "Failed — see case log"


def _load_outcomes(report_json: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not report_json.is_file():
        return out
    data = json.loads(report_json.read_text(encoding="utf-8"))
    for test in data.get("tests") or []:
        nodeid = str(test.get("nodeid") or "")
        keywords = [str(k) for k in (test.get("keywords") or [])]
        case_id = ""
        for kw in keywords:
            m = re.match(r"LINK_TEST_0*(\d+)$", kw.upper())
            if m:
                case_id = _case_key(int(m.group(1)))
                break
        if not case_id:
            m = re.search(r"test_link_test_(\d+)_", nodeid, re.I)
            if m:
                case_id = _case_key(int(m.group(1)))
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
            setup = test.get("setup") or {}
            longrepr = str(
                call.get("longrepr") or setup.get("longrepr") or test.get("longrepr") or ""
            )
            comment = f"Automated | {_short_fail(longrepr)}"
        # Prefer PASS over earlier FAIL if re-run
        prev = out.get(case_id)
        if prev and prev.get("status") == "PASS" and status != "PASS":
            continue
        out[case_id] = {"status": status, "comment": comment}
    return out


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
        f'border:1px solid {fg}33">{_esc(status)}</span>'
    )


def generate_link_test_html(
    *,
    report_json: Path,
    out_html: Path,
    run_date: str,
    bts_ip: str = "192.168.2.10",
    cpe_ip: str = "192.168.2.11",
    fw: str = "1.0.0.149",
    model: str = "UBR655",
    note: str = "",
) -> Path:
    outcomes = _load_outcomes(report_json)
    logo = senao_logo_src()

    counts = {"PASS": 0, "FAIL": 0, "NOT TESTED": 0, "NOT AVAILABLE": 0}
    rows_html: list[str] = []
    for sno, tid, desc, steps, target, category in PLAN_CASES:
        info = outcomes.get(tid) or {}
        status = str(info.get("status") or "NOT TESTED")
        comment = str(info.get("comment") or "Not Tested — CPE link down / not run this session")
        counts[status] = counts.get(status, 0) + 1
        auto_cls = "row-auto" if status in {"PASS", "FAIL"} else ""
        rows_html.append(
            f'<tr class="{auto_cls}" data-status="{_esc(status)}">'
            f"<td>{sno}</td>"
            f'<td class="tid">{_esc(tid)}</td>'
            f"<td>{_esc(desc)}</td>"
            f"<td>{_esc(steps)}<br/><small>Target: {_esc(target)} · {_esc(category)}</small></td>"
            f"<td>{_badge(status)}</td>"
            f'<td class="comment">{_esc(comment)}</td>'
            f"</tr>"
        )

    total = len(PLAN_CASES)
    meta_note = f" · {_esc(note)}" if note else ""
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Senao Link Test Report</title>
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
<div class="hero"><div><h1>Senao Link Test Report</h1><p>Source plan: BTS-AT-SW-01 Link Test · Sheet cases 03–08, 13, 15, 17–20, 22–24</p></div><div class="hero-logo"><img src="{logo}" alt="Senao"></div></div>
<div class="container"><div class="cards">
<div class="card total"><h3>{total}</h3><p>Total</p></div>
<div class="card pass"><h3>{counts.get('PASS',0)}</h3><p>Pass</p></div>
<div class="card fail"><h3>{counts.get('FAIL',0)}</h3><p>Fail</p></div>
<div class="card na"><h3>{counts.get('NOT AVAILABLE',0)}</h3><p>Not Available</p></div>
<div class="card nt"><h3>{counts.get('NOT TESTED',0)}</h3><p>Not Tested</p></div></div>
<div class="panel"><h2>Testbed Summary</h2>
<table class="testbed"><thead><tr><th></th><th><span class="role">BTS</span></th><th><span class="role">CPE</span></th></tr></thead>
<tbody><tr><th>Model</th><td>{_esc(model)}</td><td>UBR650</td></tr>
<tr><th>FW Version</th><td>{_esc(fw)}</td><td>{_esc(fw)}</td></tr>
<tr><th>IPv4 Address</th><td>{_esc(bts_ip)}/24</td><td>{_esc(cpe_ip)}/24</td></tr></tbody></table>
<div class="meta"><strong>Date:</strong> {_esc(run_date)} &nbsp; · &nbsp; <strong>Pytest automated:</strong> {sum(1 for v in outcomes.values() if v.get('status') in ('PASS','FAIL'))} &nbsp; · &nbsp; <strong>Profile:</strong> ipv4_lab{meta_note}</div></div>
<div class="content"><table class="plan"><thead><tr><th>S.No.</th><th>TESTCASE-ID</th><th>TEST DESCRIPTION</th><th>TEST STEPS</th><th>RESULT</th><th>COMMENTS</th></tr></thead><tbody>
{''.join(rows_html)}
</tbody></table></div></div></div></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    return out_html


def main() -> None:
    p = argparse.ArgumentParser(description="Generate Senao Link Test HTML report")
    p.add_argument("--report-json", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--bts-ip", default="192.168.2.10")
    p.add_argument("--cpe-ip", default="192.168.2.11")
    p.add_argument("--fw", default="1.0.0.149")
    p.add_argument("--note", default="")
    args = p.parse_args()
    path = generate_link_test_html(
        report_json=args.report_json,
        out_html=args.out,
        run_date=args.date,
        bts_ip=args.bts_ip,
        cpe_ip=args.cpe_ip,
        fw=args.fw,
        note=args.note,
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
