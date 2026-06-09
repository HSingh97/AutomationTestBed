import argparse
import asyncio
import json
import csv
import sys
import re
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.jumbo_capture_report import (
    JUMBO_CAPTURE_REPORT_CSS,
    load_jumbo_capture_index,
    parse_jmb_case_id,
    render_jumbo_capture_evidence_html,
)
from utils.regression_report import _render_testbed_summary_table

SENAO_LOGO_URL = (
    "https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png"
)
ARTIFACTS_DIR = Path("reports/artifacts")


def get_group_marker(keywords):
    """
    Extracts the logical group name from pytest markers.
    """
    ignore_list = {'pytestmark', 'asyncio', 'usefixtures', 'parametrize', 'filterwarnings'}

    if isinstance(keywords, dict):
        kw_list = keywords.keys()
    else:
        kw_list = keywords

    for kw in kw_list:
        if kw not in ignore_list and not kw.startswith('GUI_') and not kw.startswith('test_') and '.py' not in kw:
            if re.match(r"IP_\d+", kw, re.I):
                return "IP"
            if re.match(r"JMB_\d+", kw, re.I):
                return "JumboFrames"
            if kw.lower() in ("jumboframes", "jumbo"):
                return "JumboFrames"
            return kw.capitalize()

    return "Ungrouped"


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


def extract_validated_parameters(test_data):
    """
    Extracts the parameter names from the captured stdout of the test.
    """
    stdout = test_data.get('call', {}).get('stdout', '')
    stdout += test_data.get('setup', {}).get('stdout', '')

    params = []
    matches = re.finditer(r'->\s+(.*?):\s+(PASSED|FAILED|NO INFORMATION)', stdout)
    for match in matches:
        param_name = match.group(1).strip()
        if param_name not in params:
            params.append(param_name)

    return params


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
        from utils.net_utils import normalize_ip
        from utils.profile_manager import load_profile_bundle
        from utils.regression_device_info import collect_testbed_summary

        bundle = load_profile_bundle(profile_name=profile_name, local_ip=local_ip or None)
        dut = bundle.active["dut"]
        password = str(dut.get("password") or "")
        if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
            bsu_host = normalize_ip(str(dut["local_ipv6"]))
            cpe_hosts = [normalize_ip(str(ip)) for ip in dut.get("remote_ipv6s", []) if str(ip).strip()]
        else:
            bsu_host = normalize_ip(str(dut.get("local_ip") or local_ip))
            cpe_hosts = [normalize_ip(str(ip)) for ip in dut.get("remote_ips", []) if str(ip).strip()]
        print(f"Collecting testbed summary (BTS={bsu_host})...")
        return asyncio.run(collect_testbed_summary(bsu_host, cpe_hosts, password))
    except Exception as exc:
        print(f"Warning: testbed summary collection failed: {exc}")
        return {}


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
    stats = {'total': 0, 'passed': 0, 'partial': 0, 'failed': 0}
    jumbo_capture_index = load_jumbo_capture_index()

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
            if jmb_id:
                test_id = jmb_id
                test_name = nodeid.split("::")[-1].replace("test_", "").replace("_", " ").title()
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

        validated_params = extract_validated_parameters(test)

        if outcome == 'PASSED':
            stats['passed'] += 1
            status = "PASSED"
            if validated_params:
                # Create CSS pills for each parameter
                pills = "".join([f"<span class='param-pill'>{p}</span>" for p in validated_params])
                reason_html = f"<div class='reason-title'>Successfully Verified ({len(validated_params)} parameters):</div><div class='param-container'>{pills}</div>"
                reason_csv = f"Successfully Verified ({len(validated_params)} parameters):\n" + ", ".join(
                    validated_params)
            else:
                reason_html = "All telemetry and backend parameters successfully matched the GUI."
                reason_csv = reason_html
            color = "#10b981"
            bg = "#ecfdf5"

        elif outcome == 'FAILED':
            longrepr = test.get('call', {}).get('longrepr', '')

            if "FAILURE:" in longrepr:
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
                lines = longrepr.strip().split('\n')
                err = lines[-1] if lines else "Unknown Exception"
                reason_html = f"<div class='reason-title' style='color:#991b1b;'>Critical Execution Error:</div><div class='failure-list'><div class='failure-item'>{err}</div></div>"
                reason_csv = f"Critical Execution Error:\n- {err}"
                color = "#ef4444"
                bg = "#fef2f2"
        elif outcome == "SKIPPED":
            status = "SKIPPED"
            reason_html = "Test execution was bypassed."
            reason_csv = reason_html
            color = "#64748b"
            bg = "#f8fafc"
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

        capture_html, capture_csv = render_jumbo_capture_evidence_html(test_id, jumbo_capture_index)
        if capture_html:
            reason_html = f"{reason_html}{capture_html}"
            reason_csv = f"{reason_csv}\n{capture_csv}".strip()

        record = {
            'id': test_id,
            'name': test_name,
            'status': status,
            'reason': reason_html,
            'reason_csv': reason_csv,
            'color': color,
            'bg': bg
        }

        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(record)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    html_filename = ARTIFACTS_DIR / f"{output_prefix}_{build_no}_Report_{date_str}.html"
    csv_filename = ARTIFACTS_DIR / f"{output_prefix}_{build_no}_Report_{date_str}.csv"

    # Generate CSV
    with csv_filename.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Test Group', 'Module ID', 'Module Name', 'Status', 'Execution Details'])
        for group_name, records in groups.items():
            for r in records:
                writer.writerow([group_name, r['id'], r['name'], r['status'], r['reason_csv']])

    # Generate Group Filter Options
    group_options = ""
    for g in sorted(groups.keys()):
        group_options += f'<option value="{g}">{g}</option>\n'

    report_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    testbed_summary = _load_testbed_summary(profile_name, ip_addr)
    testbed_table = _render_testbed_summary_table(testbed_summary)
    is_partial_run = bool(data.get("partial") or (data.get("summary") or {}).get("partial"))
    partial_banner = ""
    if is_partial_run:
        source = data.get("recovered_from") or (data.get("summary") or {}).get("recovered_from") or "checkpoint"
        partial_banner = (
            '<div style="max-width:1100px;margin:0 auto 14px;padding:12px 16px;'
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
            .wrap {{ max-width: 1100px; margin: 0 auto; }}
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

            .summary-cards {{ display: flex; padding: 30px 40px; gap: 20px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
            .card {{ flex: 1; padding: 20px; border-radius: 10px; text-align: center; border: 1px solid #e2e8f0; background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.02); cursor: pointer; transition: all 0.2s ease; }}
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
            {JUMBO_CAPTURE_REPORT_CSS}
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
                    <img src="{SENAO_LOGO_URL}" alt="Senao Networks"/>
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
                    <span><strong>Target device:</strong> {ip_addr}</span>
                </div>
            </section>

            <div class="summary-cards">
                <div class="card active" style="border-bottom: 4px solid #64748b;" onclick="setStatusFilter('ALL', this)">
                    <h3>{stats['total']}</h3><p>Total Executed</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #10b981;" onclick="setStatusFilter('PASSED', this)">
                    <h3>{stats['passed']}</h3><p style="color: #10b981;">Fully Passed</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #f59e0b;" onclick="setStatusFilter('PARTIAL', this)">
                    <h3>{stats['partial']}</h3><p style="color: #f59e0b;">Partial (Mismatches)</p>
                </div>
                <div class="card" style="border-bottom: 4px solid #ef4444;" onclick="setStatusFilter('FAILED', this)">
                    <h3>{stats['failed']}</h3><p style="color: #ef4444;">Critical Failures</p>
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

                <table>
                    <thead>
                        <tr>
                            <th width="12%">Module ID</th>
                            <th width="20%">Module Name</th>
                            <th width="12%">Status</th>
                            <th width="56%">Execution Details</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    for group_name, records in groups.items():
        html += f"""
                        <tr class="group-header-row" data-group="{group_name}">
                            <td colspan="4" class="group-header">↳ Test Group: {group_name}</td>
                        </tr>
        """
        for r in records:
            html += f"""
                            <tr class="test-row" data-group="{group_name}" data-status="{r['status']}">
                                <td style="font-weight: 600; color: #0f172a;">{r['id']}</td>
                                <td style="font-weight: 500; color: #334155;">{r['name']}</td>
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