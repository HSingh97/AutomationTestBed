import json
import csv
import sys
import re
from datetime import datetime


def get_group_marker(keywords):
    """
    Extracts the logical group name from pytest markers.
    Filters out default pytest markers and test names to find custom markers like 'Summary'.
    """
    ignore_list = {'pytestmark', 'asyncio', 'usefixtures', 'parametrize', 'filterwarnings'}

    # Check if 'keywords' is a dict (older pytest-json-report) or list (newer)
    if isinstance(keywords, dict):
        kw_list = keywords.keys()
    else:
        kw_list = keywords

    for kw in kw_list:
        # Ignore standard markers, the test file name, and specific GUI_XX markers
        if kw not in ignore_list and not kw.startswith('GUI_') and not kw.startswith('test_') and '.py' not in kw:
            return kw.capitalize()

    return "Ungrouped"


def extract_validated_parameters(test_data):
    """
    Extracts the parameter names from the captured stdout of the test.
    Looks for the pattern: "    -> PARAM NAME: PASSED"
    """
    stdout = test_data.get('call', {}).get('stdout', '')
    stdout += test_data.get('setup', {}).get('stdout', '')  # Just in case

    params = []
    # Regex captures everything between "-> " and ":"
    matches = re.finditer(r'->\s+(.*?):\s+(PASSED|FAILED|NO INFORMATION)', stdout)
    for match in matches:
        param_name = match.group(1).strip()
        # Avoid duplicates if a parameter is checked multiple times
        if param_name not in params:
            params.append(param_name)

    return params


def generate():
    if len(sys.argv) < 4:
        print("Usage: python report_generator.py <build_no> <ip_address> <date_str>")
        sys.exit(1)

    build_no = sys.argv[1]
    ip_addr = sys.argv[2]
    date_str = sys.argv[3]

    try:
        with open('report.json', 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("report.json not found! Tests may not have executed properly.")
        sys.exit(1)

    groups = {}
    stats = {'total': 0, 'passed': 0, 'partial': 0, 'failed': 0}

    for test in data.get('tests', []):
        stats['total'] += 1
        nodeid = test.get('nodeid', '')

        # Extract Test ID and Name
        match = re.search(r'test_(gui_\d+)_(.*)', nodeid.lower())
        if match:
            test_id = match.group(1).upper()
            test_name = match.group(2).replace('_', ' ').title()
        else:
            test_id = "N/A"
            test_name = nodeid.split('::')[-1]

        # Extract Group Marker
        group_name = get_group_marker(test.get('keywords', []))

        outcome = test.get('outcome', 'unknown').upper()
        reason = ""

        # Extract Validated Parameters for Detailed Reporting
        validated_params = extract_validated_parameters(test)

        if outcome == 'PASSED':
            stats['passed'] += 1
            status = "PASSED"
            if validated_params:
                reason = f"<strong>Successfully Validated ({len(validated_params)} parameters):</strong><br/>• " + "<br/>• ".join(
                    validated_params)
            else:
                reason = "All backend parameters successfully matched the GUI."
            color = "#10b981"  # Green
            bg = "#d1fae5"

        elif outcome == 'FAILED':
            longrepr = test.get('call', {}).get('longrepr', '')

            # If pytest-check caught mismatches, we mark it as "PARTIAL"
            if "FAILURE:" in longrepr:
                stats['partial'] += 1
                status = "PARTIAL"
                failures = re.findall(r'FAILURE: (.*)', longrepr)
                reason = "<strong>Mismatches Found:</strong><br/>• " + "<br/>• ".join(failures)
                color = "#f59e0b"  # Orange
                bg = "#fef3c7"
            else:
                # If it's a real Python crash/timeout, it's a hard FAILED
                stats['failed'] += 1
                status = "FAILED"
                lines = longrepr.strip().split('\n')
                err = lines[-1] if lines else "Unknown Exception"
                reason = f"<strong>Critical Script Error:</strong><br/>{err}"
                color = "#ef4444"  # Red
                bg = "#fee2e2"
        else:
            status = "SKIPPED"
            reason = "Test was skipped."
            color = "#64748b"
            bg = "#f1f5f9"

        record = {
            'id': test_id,
            'name': test_name,
            'status': status,
            'reason': reason,
            'reason_csv': reason.replace('<strong>', '').replace('</strong>', '').replace('<br/>', '\n').replace('• ',
                                                                                                                 '- '),
            'color': color,
            'bg': bg
        }

        # Add to groups dictionary
        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(record)

    # File Naming
    html_filename = f"Senao_Release_{build_no}_Report_{date_str}.html"
    csv_filename = f"Senao_Release_{build_no}_Report_{date_str}.csv"

    # 1. Generate CSV
    with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Test Group', 'Test ID', 'Module Name', 'Status', 'Details / Reason'])
        for group_name, records in groups.items():
            for r in records:
                writer.writerow([group_name, r['id'], r['name'], r['status'], r['reason_csv']])

    # 2. Generate HTML
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Senao Customer Report</title>
        <style>
            body {{ font-family: 'Segoe UI', system-ui, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 30px; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }}
            .header {{ border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 30px; display: flex; justify-content: space-between; align-items: flex-end; }}
            .logo-title {{ display: flex; align-items: center; gap: 20px; }}
            .logo-title img {{ height: 50px; }}
            .logo-text h1 {{ margin: 0; color: #0f172a; font-size: 28px; font-weight: 800; letter-spacing: -0.5px; }}
            .logo-text p {{ margin: 8px 0 0 0; color: #64748b; font-size: 15px; }}
            .meta-info {{ text-align: right; font-size: 14px; color: #475569; line-height: 1.6; }}
            .summary-cards {{ display: flex; gap: 20px; margin-bottom: 40px; }}
            .card {{ flex: 1; padding: 25px; border-radius: 10px; text-align: center; border: 1px solid #e2e8f0; }}
            .card h3 {{ margin: 0; font-size: 36px; color: #0f172a; }}
            .card p {{ margin: 8px 0 0 0; font-size: 13px; color: #64748b; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 18px; text-align: left; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
            th {{ background-color: #f8fafc; color: #475569; font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; }}
            tr:hover {{ background-color: #f8fafc; }}
            .group-header {{ background-color: #e2e8f0 !important; color: #0f172a; font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }}
            .badge {{ padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 700; text-align: center; display: inline-block; letter-spacing: 0.5px; }}
            .reason-cell {{ font-family: 'Consolas', monospace; font-size: 13px; color: #334155; line-height: 1.6; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo-title">
                    <img src="https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png" alt="Senao Networks">
                    <div class="logo-text">
                        <h1>Test Automation Report</h1>
                        <p>Firmware UI vs CLI Parameter Validation</p>
                    </div>
                </div>
                <div class="meta-info">
                    <strong>Build ID:</strong> {build_no}<br>
                    <strong>Target IP:</strong> {ip_addr}<br>
                    <strong>Executed:</strong> {datetime.now().strftime('%d %b %Y, %H:%M:%S')}
                </div>
            </div>

            <div class="summary-cards">
                <div class="card" style="background-color: #f8fafc;"><h3>{stats['total']}</h3><p>Total Modules</p></div>
                <div class="card" style="background-color: #ecfdf5; border-color: #10b981;"><h3>{stats['passed']}</h3><p style="color: #10b981;">Fully Passed</p></div>
                <div class="card" style="background-color: #fef3c7; border-color: #f59e0b;"><h3>{stats['partial']}</h3><p style="color: #f59e0b;">Partial (Mismatches)</p></div>
                <div class="card" style="background-color: #fef2f2; border-color: #ef4444;"><h3>{stats['failed']}</h3><p style="color: #ef4444;">Critical Failures</p></div>
            </div>

            <table>
                <thead>
                    <tr>
                        <th width="12%">Module ID</th>
                        <th width="20%">Module Name</th>
                        <th width="13%">Status</th>
                        <th width="55%">Details / Reason</th>
                    </tr>
                </thead>
                <tbody>
    """

    # Grouped Table Rows
    for group_name, records in groups.items():
        # Insert Group Header
        html += f"""
                    <tr>
                        <td colspan="4" class="group-header">📂 Test Group: {group_name}</td>
                    </tr>
        """
        # Insert Tests for that Group
        for r in records:
            html += f"""
                        <tr>
                            <td style="font-weight: 600; color: #0f172a; padding-left: 25px;">{r['id']}</td>
                            <td style="font-weight: 500;">{r['name']}</td>
                            <td><span class="badge" style="background-color: {r['bg']}; color: {r['color']}; border: 1px solid {r['color']}40;">{r['status']}</span></td>
                            <td class="reason-cell">{r['reason']}</td>
                        </tr>
            """

    html += """
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """

    with open(html_filename, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ Generated Customer Reports: {html_filename} & {csv_filename}")


if __name__ == "__main__":
    generate()