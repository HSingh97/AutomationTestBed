"""Generate a standalone Grafana-style lab dashboard HTML preview."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from traffic.phy_rate_targets import compute_traffic_targets
from utils.grafana_matrix_report import enrich_matrix_payload, write_matrix_grafana_html


_ROW_RE = re.compile(
    r'<tr data-bw="(HT\d+)" data-iter-key="(\d+)" data-passed="([01])">(.*?)</tr>',
    re.DOTALL,
)


def _pct(observed: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return round((observed / target) * 100.0, 1)


def parse_performance_report_html(path: str | Path) -> dict[str, Any]:
    """Build Grafana payload from a Senao performance report HTML export."""
    html = Path(path).read_text(encoding="utf-8")
    source_name = Path(path).name

    date_match = re.search(r'class="hero-date">([^<]+)', html)
    stand_match = re.search(r"<strong>Stand:</strong>\s*([^<]+)", html)
    profile_match = re.search(r"<strong>Profile:</strong>\s*([^<]+)", html)
    su_match = re.search(r"<strong>Connected SUs:</strong>\s*(\d+)", html)
    outcome_match = re.search(r"run-outcome[^\"]*\"><strong>(\d+)/(\d+)\s+passed", html)

    seen_keys: set[int] = set()
    raw_iterations: list[dict[str, Any]] = []
    for match in _ROW_RE.finditer(html):
        bw, key_s, passed_s, body = match.groups()
        key = int(key_s)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        mcs_match = re.search(r"mcs-label'>(MCS\d+)", body)
        if not mcs_match:
            continue
        mcs = mcs_match.group(1)

        total_match = re.search(r"tput-(?:good|warn|bad|zero)'>([\d.]+)\s*Mbps", body)
        total_mbps = float(total_match.group(1)) if total_match else 0.0

        remark_match = re.search(r"remarks-cell'>(.*?)</td>", body, re.DOTALL)
        remark = re.sub(r"<[^>]+>", "", remark_match.group(1)).strip() if remark_match else ""

        targets = compute_traffic_targets(
            bandwidth=bw,
            mcs=mcs,
            ratio="75:25",
            efficiency_factor=0.75,
            su_count=int(su_match.group(1)) if su_match else 4,
        )
        target_mbps = float(targets["effective_target_mbps"])

        raw_iterations.append(
            {
                "iter_key": key,
                "bandwidth": bw,
                "mcs": mcs,
                "passed": passed_s == "1",
                "rx_mbps": total_mbps,
                "target_mbps": target_mbps,
                "remark": remark,
            }
        )

    raw_iterations.sort(key=lambda row: row["iter_key"])
    for row in raw_iterations:
        row["pct"] = _pct(float(row["rx_mbps"]), float(row["target_mbps"]))

    su_health = _parse_su_health_from_report(html, raw_iterations)
    peaks = _peak_by_bandwidth(raw_iterations)

    passed = int(outcome_match.group(1)) if outcome_match else sum(1 for r in raw_iterations if r["passed"])
    total = int(outcome_match.group(2)) if outcome_match else len(raw_iterations)

    report_id = re.search(r"Performance_(\d+)_Report", source_name)
    return {
        "report_kind": "matrix",
        "report_id": report_id.group(1) if report_id else source_name,
        "source_file": source_name,
        "stand": (stand_match.group(1).strip() if stand_match else "—"),
        "profile": (profile_match.group(1).strip() if profile_match else "—"),
        "generated_at": date_match.group(1).strip() if date_match else "—",
        "su_count": int(su_match.group(1)) if su_match else 4,
        "matrix_passed": passed,
        "matrix_total": total,
        "iterations": raw_iterations,
        "peaks": peaks,
        "su_health": su_health,
        "bandwidths": ["HT20", "HT40", "HT80"],
    }


def _peak_by_bandwidth(iterations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    peaks: dict[str, dict[str, Any]] = {}
    for bw in ("HT20", "HT40", "HT80"):
        rows = [row for row in iterations if row["bandwidth"] == bw]
        if not rows:
            continue
        best = max(rows, key=lambda row: float(row.get("rx_mbps") or 0))
        peaks[bw] = {
            "mcs": best["mcs"],
            "rx_mbps": float(best.get("rx_mbps") or 0),
            "passed": sum(1 for row in rows if row.get("passed")),
            "total": len(rows),
        }
    return peaks


def _parse_su_health_from_report(
    html: str,
    iterations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use the peak-throughput HT80 iteration for per-CPE RF snapshot."""
    ht80 = [row for row in iterations if row["bandwidth"] == "HT80" and row.get("passed")]
    if not ht80:
        return []
    ref = max(ht80, key=lambda row: float(row.get("rx_mbps") or 0))
    key = ref["iter_key"]
    rows = re.findall(
        rf'<tr data-bw="HT80" data-iter-key="{key}" data-passed="1">(.*?)</tr>',
        html,
        re.DOTALL,
    )
    health: list[dict[str, Any]] = []
    for idx, body in enumerate(rows, start=1):
        unit = re.search(r'unit-line">([^<]+)', body)
        ip = re.search(r'unit-ip">([^<]+)', body)
        nums = re.findall(r"<td>([^<]+)</td>", body)
        # unit, snr local, snr remote, rssi local, rssi remote, tx rate, rx rate, tx tput, rx tput
        rssi_vals = [n for n in nums[3:5] if re.fullmatch(r"-?\d+", n.strip())]
        snr_vals = [n for n in nums[1:3] if "/" in n]
        rate_vals = [n for n in nums[5:7] if re.search(r"\d", n)]
        rssi = int(rssi_vals[0]) if rssi_vals else None
        snr = int(snr_vals[0].split("/")[0]) if snr_vals else None
        rate_digits = re.search(r"(\d+)", rate_vals[-1]) if rate_vals else None
        health.append(
            {
                "label": unit.group(1).strip() if unit else f"SU{idx}",
                "ip": ip.group(1).strip() if ip else "—",
                "rssi": rssi,
                "snr": snr,
                "rate_mbps": int(rate_digits.group(1)) if rate_digits else 0,
                "linked": True,
            }
        )
    return health


def build_matrix_grafana_payload(
    records: list[dict[str, Any]],
    *,
    testbed_summary: dict[str, Any] | None = None,
    report_id: str = "local",
    generated_at: str = "",
) -> dict[str, Any]:
    """Build Grafana-style matrix payload directly from performance matrix records."""
    summary = testbed_summary or {}
    return enrich_matrix_payload(
        records,
        testbed_summary=summary,
        report_id=report_id,
        generated_at=generated_at,
        stand=str(summary.get("stand") or ""),
        profile=str(summary.get("profile") or ""),
        source_file=f"performance-matrix-{report_id}",
    )


def default_sample_payload() -> dict[str, Any]:
    """Representative qa-lab-02 data from recent matrix runs (builds #74–#77)."""
    return {
        "stand": "test-qa-lab-02",
        "profile": "qa_lab_02",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "su_count": 4,
        "builds": [
            {"id": 74, "vlan": "QinQ", "su_link_wait_s": 120, "passed": 1, "total": 3},
            {"id": 75, "vlan": "transparent", "su_link_wait_s": 120, "passed": 2, "total": 3},
            {"id": 76, "vlan": "QinQ", "su_link_wait_s": 120, "passed": 1, "total": 3},
            {"id": 77, "vlan": "transparent", "su_link_wait_s": 240, "passed": 0, "total": 3},
        ],
        "iterations": [
            {
                "build": 74,
                "bandwidth": "HT20",
                "mcs": "MCS14",
                "vlan_mode": "QinQ",
                "su_linked": 4,
                "link_wait_s": 10,
                "rx_mbps": 32.96,
                "target_mbps": 35.7,
                "passed": True,
            },
            {
                "build": 74,
                "bandwidth": "HT40",
                "mcs": "MCS14",
                "vlan_mode": "QinQ",
                "su_linked": 3,
                "link_wait_s": 120,
                "rx_mbps": 0,
                "target_mbps": 71.4,
                "passed": False,
            },
            {
                "build": 74,
                "bandwidth": "HT80",
                "mcs": "MCS14",
                "vlan_mode": "QinQ",
                "su_linked": 3,
                "link_wait_s": 120,
                "rx_mbps": 0,
                "target_mbps": 140.0,
                "passed": False,
            },
            {
                "build": 75,
                "bandwidth": "HT20",
                "mcs": "MCS14",
                "vlan_mode": "transparent",
                "su_linked": 4,
                "link_wait_s": 10,
                "rx_mbps": 36.12,
                "target_mbps": 35.7,
                "passed": True,
            },
            {
                "build": 75,
                "bandwidth": "HT40",
                "mcs": "MCS14",
                "vlan_mode": "transparent",
                "su_linked": 4,
                "link_wait_s": 20,
                "rx_mbps": 72.25,
                "target_mbps": 71.4,
                "passed": True,
            },
            {
                "build": 75,
                "bandwidth": "HT80",
                "mcs": "MCS14",
                "vlan_mode": "transparent",
                "su_linked": 3,
                "link_wait_s": 120,
                "rx_mbps": 0,
                "target_mbps": 140.0,
                "passed": False,
            },
            {
                "build": 77,
                "bandwidth": "HT20",
                "mcs": "MCS14",
                "vlan_mode": "transparent",
                "su_linked": 0,
                "link_wait_s": 240,
                "rx_mbps": 0,
                "target_mbps": 35.7,
                "passed": False,
            },
            {
                "build": 77,
                "bandwidth": "HT40",
                "mcs": "MCS14",
                "vlan_mode": "transparent",
                "su_linked": 3,
                "link_wait_s": 240,
                "rx_mbps": 0,
                "target_mbps": 71.4,
                "passed": False,
            },
            {
                "build": 77,
                "bandwidth": "HT80",
                "mcs": "MCS14",
                "vlan_mode": "transparent",
                "su_linked": 3,
                "link_wait_s": 240,
                "rx_mbps": 0,
                "target_mbps": 140.0,
                "passed": False,
            },
        ],
        "su_health": [
            {"label": "SU1", "ip": "…:11c9", "rssi": -51, "snr": 38, "rate_mbps": 51, "linked": True},
            {"label": "SU2", "ip": "…:118b", "rssi": -54, "snr": 35, "rate_mbps": 51, "linked": True},
            {"label": "SU3", "ip": "…:1178", "rssi": -56, "snr": 33, "rate_mbps": 51, "linked": True},
            {"label": "SU4", "ip": "…:113d", "rssi": -62, "snr": 28, "rate_mbps": 0, "linked": False},
        ],
    }


def write_grafana_sample_html(
    path: str | Path,
    payload: dict[str, Any] | None = None,
) -> Path:
    data = payload or default_sample_payload()
    if data.get("report_kind") == "matrix":
        return _write_matrix_grafana_html(path, data)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    iterations = data.get("iterations") or []
    for row in iterations:
        row["pct"] = _pct(float(row.get("rx_mbps") or 0), float(row.get("target_mbps") or 0))

    chart_labels = [f"#{r['build']} {r['bandwidth']}" for r in iterations]
    chart_rx = [float(r.get("rx_mbps") or 0) for r in iterations]
    chart_target = [float(r.get("target_mbps") or 0) for r in iterations]
    chart_pct = [float(r.get("pct") or 0) for r in iterations]
    chart_link_wait = [float(r.get("link_wait_s") or 0) for r in iterations]
    chart_su = [int(r.get("su_linked") or 0) for r in iterations]

    su_rows = data.get("su_health") or []
    latest_linked = sum(1 for s in su_rows if s.get("linked"))
    su_total = int(data.get("su_count") or 4)

    build_rows_html = []
    for b in data.get("builds") or []:
        build_rows_html.append(
            f"<tr><td>#{escape(str(b['id']))}</td>"
            f"<td>{escape(str(b.get('vlan', '—')))}</td>"
            f"<td>{b.get('su_link_wait_s', '—')}s</td>"
            f"<td>{b.get('passed', 0)}/{b.get('total', 0)}</td></tr>"
        )

    iter_rows_html = []
    for r in iterations:
        pct = float(r.get("pct") or 0)
        if pct >= 65:
            pct_cls = "ok"
        elif pct >= 40:
            pct_cls = "warn"
        else:
            pct_cls = "bad"
        status = "PASS" if r.get("passed") else "FAIL"
        status_cls = "ok" if r.get("passed") else "bad"
        iter_rows_html.append(
            f"<tr>"
            f"<td>#{r['build']}</td><td>{escape(r['bandwidth'])}</td><td>{escape(r['mcs'])}</td>"
            f"<td>{escape(str(r.get('vlan_mode', '—')))}</td>"
            f"<td>{r.get('su_linked', 0)}/{su_total}</td>"
            f"<td>{r.get('link_wait_s', 0)}s</td>"
            f"<td>{float(r.get('rx_mbps') or 0):.1f}</td>"
            f"<td>{float(r.get('target_mbps') or 0):.1f}</td>"
            f"<td><span class='pill {pct_cls}'>{pct:.0f}%</span></td>"
            f"<td><span class='pill {status_cls}'>{status}</span></td>"
            f"</tr>"
        )

    su_health_html = []
    for s in su_rows:
        linked_cls = "ok" if s.get("linked") else "bad"
        su_health_html.append(
            f"<tr><td>{escape(s['label'])}</td><td>{escape(s.get('ip', '—'))}</td>"
            f"<td>{s.get('rssi', '—')} dBm</td><td>{s.get('snr', '—')} dB</td>"
            f"<td>{s.get('rate_mbps', 0)} Mbps</td>"
            f"<td><span class='pill {linked_cls}'>{'UP' if s.get('linked') else 'DOWN'}</span></td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR Lab — Grafana Sample Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0b0c0e;
      --panel: #181b1f;
      --border: #2a2e33;
      --text: #d8d9da;
      --muted: #8e8e8e;
      --accent: #5794f2;
      --ok: #73bf69;
      --warn: #ff9830;
      --bad: #f2495c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 20px;
      border-bottom: 1px solid var(--border);
      background: #111217;
    }}
    .top h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
    .top .meta {{ color: var(--muted); font-size: 12px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 12px;
      padding: 16px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 12px 14px;
      min-height: 120px;
    }}
    .panel h2 {{
      margin: 0 0 10px;
      font-size: 13px;
      font-weight: 500;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .span-3 {{ grid-column: span 3; }}
    .span-4 {{ grid-column: span 4; }}
    .span-6 {{ grid-column: span 6; }}
    .span-8 {{ grid-column: span 8; }}
    .span-12 {{ grid-column: span 12; }}
    .stat {{
      font-size: 42px;
      font-weight: 600;
      line-height: 1;
    }}
    .stat small {{ font-size: 14px; color: var(--muted); font-weight: 400; }}
    .stat.ok {{ color: var(--ok); }}
    .stat.warn {{ color: var(--warn); }}
    .stat.bad {{ color: var(--bad); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ color: var(--muted); font-weight: 500; }}
    .pill {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
    }}
    .pill.ok {{ background: rgba(115,191,105,.15); color: var(--ok); }}
    .pill.warn {{ background: rgba(255,152,48,.15); color: var(--warn); }}
    .pill.bad {{ background: rgba(242,73,92,.15); color: var(--bad); }}
    .legend {{ font-size: 11px; color: var(--muted); margin-top: 8px; }}
    canvas {{ max-height: 260px; }}
    @media (max-width: 1100px) {{
      .span-3, .span-4, .span-6, .span-8 {{ grid-column: span 12; }}
    }}
  </style>
</head>
<body>
  <header class="top">
    <div>
      <h1>UBR Lab Performance · Grafana Sample</h1>
      <div class="meta">{escape(str(data.get('stand')))} · {escape(str(data.get('profile')))} · {escape(str(data.get('generated_at')))}</div>
    </div>
    <div class="meta">Sample report — import <code>docs/samples/grafana/ubr-lab-dashboard.json</code> for live Grafana</div>
  </header>

  <main class="grid">
    <section class="panel span-3">
      <h2>Connected SUs</h2>
      <div class="stat {'ok' if latest_linked >= su_total else 'warn' if latest_linked >= 3 else 'bad'}">{latest_linked}<small> / {su_total}</small></div>
      <div class="legend">From KWN sysfs / ping matrix (sample snapshot)</div>
    </section>
    <section class="panel span-3">
      <h2>Latest throughput</h2>
      <div class="stat ok">{max(chart_rx):.1f}<small> Mbps</small></div>
      <div class="legend">Peak combined RX across sample runs</div>
    </section>
    <section class="panel span-3">
      <h2>MCS target (best)</h2>
      <div class="stat {'ok' if max(chart_pct) >= 65 else 'warn' if max(chart_pct) >= 40 else 'bad'}">{max(chart_pct):.0f}<small> %</small></div>
      <div class="legend">Pass ≥65% · Warn 40–65% · Fail &lt;40%</div>
    </section>
    <section class="panel span-3">
      <h2>Matrix pass rate</h2>
      <div class="stat warn">{sum(1 for r in iterations if r.get('passed'))}<small> / {len(iterations)}</small></div>
      <div class="legend">Iterations in sample dataset</div>
    </section>

    <section class="panel span-8">
      <h2>Combined RX vs MCS target (Mbps)</h2>
      <canvas id="tputChart"></canvas>
    </section>
    <section class="panel span-4">
      <h2>% of MCS target</h2>
      <canvas id="pctChart"></canvas>
    </section>

    <section class="panel span-6">
      <h2>SU link recovery time after BW apply</h2>
      <canvas id="linkChart"></canvas>
    </section>
    <section class="panel span-6">
      <h2>SUs linked per iteration</h2>
      <canvas id="suChart"></canvas>
    </section>

    <section class="panel span-4">
      <h2>Jenkins builds (sample)</h2>
      <table>
        <thead><tr><th>Build</th><th>VLAN</th><th>Wait</th><th>Pass</th></tr></thead>
        <tbody>{''.join(build_rows_html)}</tbody>
      </table>
    </section>
    <section class="panel span-8">
      <h2>Per-SU RF health (sample)</h2>
      <table>
        <thead><tr><th>SU</th><th>IPv6</th><th>RSSI</th><th>SNR</th><th>Rate</th><th>Link</th></tr></thead>
        <tbody>{''.join(su_health_html)}</tbody>
      </table>
    </section>

    <section class="panel span-12">
      <h2>Matrix iterations detail</h2>
      <table>
        <thead>
          <tr>
            <th>Build</th><th>BW</th><th>MCS</th><th>VLAN</th><th>SUs</th><th>Link wait</th>
            <th>RX Mbps</th><th>Target</th><th>% target</th><th>Status</th>
          </tr>
        </thead>
        <tbody>{''.join(iter_rows_html)}</tbody>
      </table>
    </section>
  </main>

  <script>
    const labels = {json.dumps(chart_labels)};
    const gridColor = '#2a2e33';
    const common = {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: '#d8d9da' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#8e8e8e', maxRotation: 45 }}, grid: {{ color: gridColor }} }},
        y: {{ ticks: {{ color: '#8e8e8e' }}, grid: {{ color: gridColor }} }}
      }}
    }};

    new Chart(document.getElementById('tputChart'), {{
      type: 'bar',
      data: {{
        labels,
        datasets: [
          {{ label: 'Combined RX', data: {json.dumps(chart_rx)}, backgroundColor: '#5794f2' }},
          {{ label: 'MCS target', data: {json.dumps(chart_target)}, backgroundColor: '#444' }}
        ]
      }},
      options: common
    }});

    new Chart(document.getElementById('pctChart'), {{
      type: 'line',
      data: {{
        labels,
        datasets: [{{
          label: '% of target',
          data: {json.dumps(chart_pct)},
          borderColor: '#ff9830',
          backgroundColor: 'rgba(255,152,48,.15)',
          fill: true,
          tension: 0.25
        }}]
      }},
      options: {{
        ...common,
        plugins: {{
          ...common.plugins,
          annotation: {{}}
        }},
        scales: {{
          ...common.scales,
          y: {{ ...common.scales.y, suggestedMin: 0, suggestedMax: 120 }}
        }}
      }}
    }});

    new Chart(document.getElementById('linkChart'), {{
      type: 'bar',
      data: {{
        labels,
        datasets: [{{
          label: 'Seconds to 4/4 (or timeout)',
          data: {json.dumps(chart_link_wait)},
          backgroundColor: {json.dumps(['#73bf69' if s < 60 else '#ff9830' if s < 120 else '#f2495c' for s in chart_link_wait])}
        }}]
      }},
      options: common
    }});

    new Chart(document.getElementById('suChart'), {{
      type: 'line',
      data: {{
        labels,
        datasets: [{{
          label: 'SUs linked',
          data: {json.dumps(chart_su)},
          borderColor: '#73bf69',
          stepped: true,
          fill: false
        }}]
      }},
      options: {{
        ...common,
        scales: {{
          ...common.scales,
          y: {{ ...common.scales.y, suggestedMin: 0, suggestedMax: 4, ticks: {{ stepSize: 1 }} }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out


def _write_matrix_grafana_html(path: str | Path, data: dict[str, Any]) -> Path:
    return write_matrix_grafana_html(path, data)
