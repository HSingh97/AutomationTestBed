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

    if isinstance(keywords, dict):
        kw_list = keywords.keys()
    else:
        kw_list = keywords

    for kw in kw_list:
        if kw not in ignore_list and not kw.startswith('GUI_') and not kw.startswith('test_') and '.py' not in kw:
            return kw.capitalize()

    return "Ungrouped"


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
    E.g., "check 51.78 <= 5.0: CPU Spike!" -> "CPU Spike! (Detected Drift: 51.78% | Max Allowed: 5.0%)"
    """
    # 1. Check for numerical tolerance failures (e.g., drift)
    num_match = re.match(r'check\s+([\d.]+)\s*<=\s*([\d.]+):\s*(.*)', raw_failure)
    if num_match:
        val, tol, msg = num_match.groups()
        return f"{msg} <br/><span style='color:#ef4444; font-size:12px;'>↳ <b>Drift Analysis:</b> Detected <b>{float(val):.2f}%</b> (Exceeds allowed {tol}%)</span>"

    # 2. Strip standard pytest_check prefix (e.g., "check 'ap' in 'SU': Mismatch...")
    clean_msg = re.sub(r'^check\s+.*?:\s*', '', raw_failure)
    return clean_msg


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

        # Format Module Name (e.g., 'summary_system' -> 'System-Summary')
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
        outcome = test.get('outcome', 'unknown').upper()
        reason = ""

        validated_params = extract_validated_parameters(test)

        if outcome == 'PASSED':
            stats['passed'] += 1
            status = "PASSED"
            if validated_params:
                reason = f"<span style='color:#0f172a; font-weight:600;'>Successfully Verified ({len(validated_params)} parameters):</span><br/>" + ", ".join(
                    validated_params)
            else:
                reason = "All telemetry and backend parameters successfully matched the GUI."
            color = "#10b981"
            bg = "#ecfdf5"

        elif outcome == 'FAILED':
            longrepr = test.get('call', {}).get('longrepr', '')

            if "FAILURE:" in longrepr:
                stats['partial'] += 1
                status = "PARTIAL"
                raw_failures = re.findall(r'FAILURE: (.*)', longrepr)

                # Apply our new professional formatter to each failure
                clean_failures = [clean_failure_message(f) for f in raw_failures]

                reason = "<span style='color:#b45309; font-weight:600;'>Discrepancies Detected:</span><br/>• " + "<br/>• ".join(
                    clean_failures)
                color = "#d97706"
                bg = "#fffbeb"
            else:
                stats['failed'] += 1
                status = "FAILED"
                lines = longrepr.strip().split('\n')
                err = lines[-1] if lines else "Unknown Exception"
                reason = f"<span style='color:#991b1b; font-weight:600;'>Critical Execution Error:</span><br/>{err}"
                color = "#ef4444"
                bg = "#fef2f2"
        else:
            status = "SKIPPED"
            reason = "Test execution was bypassed."
            color = "#64748b"
            bg = "#f8fafc"

        record = {
            'id': test_id,
            'name': test_name,
            'status': status,
            'reason': reason,
            'reason_csv': reason.replace('<b>', '').replace('</b>', '').replace('<strong>', '').replace('</strong>',
                                                                                                        '').replace(
                '<span', '<').replace('</span>', '').replace('<br/>', '\n').replace('• ', '- '),
            'color': color,
            'bg': bg
        }

        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(record)

    html_filename = f"Senao_Release_{build_no}_Report_{date_str}.html"
    csv_filename = f"Senao_Release_{build_no}_Report_{date_str}.csv"

    # Generate CSV
    with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Test Group', 'Module ID', 'Module Name', 'Status', 'Execution Details'])
        for group_name, records in groups.items():
            for r in records:
                writer.writerow([group_name, r['id'], r['name'], r['status'], r['reason_csv']])

    # Generate Professional HTML
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Senao Quality Assurance Report</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            body {{ font-family: 'Inter', sans-serif; background-color: #f1f5f9; color: #334155; margin: 0; padding: 40px 20px; }}
            .container {{ max-width: 1100px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03); overflow: hidden; }}
            .header {{ background-color: #ffffff; padding: 30px 40px; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center; }}
            .logo-title {{ display: flex; align-items: center; gap: 24px; }}
            .logo-title img {{ height: 45px; }}
            .logo-text h1 {{ margin: 0; color: #0f172a; font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }}
            .logo-text p {{ margin: 4px 0 0 0; color: #64748b; font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }}
            .meta-info {{ background: #f8fafc; padding: 12px 20px; border-radius: 8px; border: 1px solid #e2e8f0; text-align: right; font-size: 13px; color: #475569; line-height: 1.6; }}
            .meta-info strong {{ color: #0f172a; }}

            .summary-cards {{ display: flex; padding: 30px 40px; gap: 20px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
            .card {{ flex: 1; padding: 20px; border-radius: 10px; text-align: center; border: 1px solid #e2e8f0; background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }}
            .card h3 {{ margin: 0; font-size: 32px; font-weight: 700; color: #0f172a; }}
            .card p {{ margin: 8px 0 0 0; font-size: 12px; color: #64748b; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }}

            .content {{ padding: 0 40px 40px 40px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ padding: 16px 20px; text-align: left; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
            th {{ background-color: #ffffff; color: #64748b; font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; border-bottom: 2px solid #e2e8f0; }}
            tr:hover {{ background-color: #f8fafc; transition: background-color 0.2s ease; }}
            .group-header {{ background-color: #f1f5f9 !important; color: #334155; font-weight: 700; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; padding-top: 24px; border-bottom: 2px solid #cbd5e1; }}

            .badge {{ padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: 700; text-align: center; display: inline-block; letter-spacing: 0.5px; text-transform: uppercase; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}
            .reason-cell {{ font-size: 13px; color: #475569; line-height: 1.6; word-break: break-word; }}
            .reason-cell b {{ color: #0f172a; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo-title">
                    <img src="https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png" alt="Senao Networks">
                    <div class="logo-text">
                        <h1>Validation Execution Report</h1>
                        <p>Automated Device Telemetry & Consistency Analysis</p>
                    </div>
                </div>
                <div class="meta-info">
                    <strong>Build Release:</strong> #{build_no}<br>
                    <strong>Target Device IP:</strong> {ip_addr}<br>
                    <strong>Timestamp:</strong> {datetime.now().strftime('%d %b %Y, %H:%M:%S')}
                </div>
            </div>

            <div class="summary-cards">
                <div class="card"><h3>{stats['total']}</h3><p>Modules Executed</p></div>
                <div class="card" style="border-bottom: 4px solid #10b981;"><h3>{stats['passed']}</h3><p style="color: #10b981;">Fully Passed</p></div>
                <div class="card" style="border-bottom: 4px solid #f59e0b;"><h3>{stats['partial']}</h3><p style="color: #f59e0b;">Partial (Mismatches)</p></div>
                <div class="card" style="border-bottom: 4px solid #ef4444;"><h3>{stats['failed']}</h3><p style="color: #ef4444;">Critical Failures</p></div>
            </div>

            <div class="content">
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
                        <tr>
                            <td colspan="4" class="group-header">↳ Test Group: {group_name}</td>
                        </tr>
        """
        for r in records:
            html += f"""
                            <tr>
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
    </body>
    </html>
    """

    with open(html_filename, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ Generated Professional Reports: {html_filename} & {csv_filename}")


if __name__ == "__main__":
    generate()