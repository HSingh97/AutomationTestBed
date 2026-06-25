"""Generate a standalone Grafana-style lab dashboard HTML preview."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from traffic.phy_rate_targets import compute_traffic_targets


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
  out = Path(path)
  out.parent.mkdir(parents=True, exist_ok=True)

  iterations = data.get("iterations") or []
  peaks = data.get("peaks") or {}
  su_rows = data.get("su_health") or []
  su_total = int(data.get("su_count") or 4)
  passed = int(data.get("matrix_passed") or sum(1 for r in iterations if r.get("passed")))
  total = int(data.get("matrix_total") or len(iterations))
  peak_rx = max((float(r.get("rx_mbps") or 0) for r in iterations), default=0.0)
  best_pct = max((float(r.get("pct") or 0) for r in iterations if r.get("passed")), default=0.0)

  mcs_labels = [f"MCS{i}" for i in range(24)]

  def series_for_bw(bw: str) -> list[float]:
      by_mcs = {row["mcs"]: float(row.get("rx_mbps") or 0) for row in iterations if row["bandwidth"] == bw}
      return [by_mcs.get(label, 0.0) for label in mcs_labels]

  ht20_series = series_for_bw("HT20")
  ht40_series = series_for_bw("HT40")
  ht80_series = series_for_bw("HT80")

  pct_ht20 = []
  pct_ht80 = []
  for label in mcs_labels:
      for bw, bucket in (("HT20", pct_ht20), ("HT80", pct_ht80)):
          row = next((r for r in iterations if r["bandwidth"] == bw and r["mcs"] == label), None)
          bucket.append(float(row.get("pct") or 0) if row and row.get("passed") else None)

  bw_pass_counts = [
      peaks.get("HT20", {}).get("passed", 0),
      peaks.get("HT40", {}).get("passed", 0),
      peaks.get("HT80", {}).get("passed", 0),
  ]
  bw_fail_counts = [
      peaks.get("HT20", {}).get("total", 24) - bw_pass_counts[0],
      peaks.get("HT40", {}).get("total", 24) - bw_pass_counts[1],
      peaks.get("HT80", {}).get("total", 24) - bw_pass_counts[2],
  ]


  iter_rows_html = []
  for row in iterations:
      pct = float(row.get("pct") or 0)
      if not row.get("passed"):
          pct_cls = "bad"
          row_cls = "row-fail"
      elif pct >= 65:
          pct_cls = "ok"
          row_cls = "row-pass"
      elif pct >= 40:
          pct_cls = "warn"
          row_cls = "row-warn"
      else:
          pct_cls = "bad"
          row_cls = "row-warn"
      status_cls = "ok" if row.get("passed") else "bad"
      iter_rows_html.append(
          f"<tr class='{row_cls}' data-bw='{escape(row['bandwidth'])}'>"
          f"<td>{escape(row['bandwidth'])}</td><td>{escape(row['mcs'])}</td>"
          f"<td>{float(row.get('rx_mbps') or 0):.1f}</td>"
          f"<td>{float(row.get('target_mbps') or 0):.1f}</td>"
          f"<td><span class='pill {pct_cls}'>{pct:.0f}%</span></td>"
          f"<td><span class='pill {status_cls}'>{'PASS' if row.get('passed') else 'FAIL'}</span></td>"
          f"<td class='remark'>{escape(str(row.get('remark') or '—'))}</td>"
          f"</tr>"
      )

  heatmap_html = []
  bw_colors = {"HT20": "#73bf69", "HT40": "#5794f2", "HT80": "#b877d9"}
  for bw in ("HT20", "HT40", "HT80"):
      heatmap_html.append(f"<div class='hm-bw-label' style='color:{bw_colors[bw]}'>{bw}</div>")
      for mcs in mcs_labels:
          row = next((r for r in iterations if r["bandwidth"] == bw and r["mcs"] == mcs), None)
          if not row or not row.get("passed"):
              cls, text, tip = "hm-skip", "—", f"{bw} {mcs}: skipped"
          else:
              pct = float(row.get("pct") or 0)
              text = f"{pct:.0f}"
              tip = f"{bw} {mcs}: {float(row.get('rx_mbps') or 0):.1f} Mbps ({pct:.0f}% of target)"
              if pct >= 65:
                  cls = "hm-pass"
              elif pct >= 40:
                  cls = "hm-warn"
              else:
                  cls = "hm-fail"
          heatmap_html.append(
              f"<div class='hm-cell {cls}' title='{escape(tip)}'><span>{text}</span>"
              f"<small>{mcs.replace('MCS', '')}</small></div>"
          )

  colors = {"HT20": "#73bf69", "HT40": "#5794f2", "HT80": "#b877d9"}

  report_id = escape(str(data.get("report_id", "")))
  pass_cls = "ok" if passed == total else "warn" if passed >= total // 2 else "bad"
  pct_cls_top = "ok" if best_pct >= 65 else "warn" if best_pct >= 40 else "bad"
  pass_pct = round((passed / total) * 100) if total else 0
  ht40_fail_note = "HT40: 0/24 — SUs did not recover after bandwidth apply"

  html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR Performance Report #{report_id} — Grafana View</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0b0f14; --panel: #151a21; --panel-2: #1c222b; --border: #2a3340;
      --text: #e8eaed; --muted: #9aa3ad; --accent: #5794f2;
      --ok: #73bf69; --warn: #ff9830; --bad: #f2495c;
      --ht20: #73bf69; --ht40: #5794f2; --ht80: #b877d9;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: Inter, system-ui, sans-serif;
      background: radial-gradient(ellipse 120% 80% at 50% -20%, #1a2744 0%, var(--bg) 55%);
      color: var(--text); min-height: 100vh;
    }}
    .hero {{
      padding: 28px 24px 22px;
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 55%, #2563eb 100%);
      border-bottom: 1px solid rgba(255,255,255,.08);
      display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; flex-wrap: wrap;
    }}
    .hero h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 800; letter-spacing: -0.02em; }}
    .hero .meta {{ color: rgba(255,255,255,.82); font-size: 13px; line-height: 1.5; }}
    .hero-badge {{
      background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.2);
      border-radius: 999px; padding: 8px 16px; font-size: 13px; font-weight: 600;
      backdrop-filter: blur(8px);
    }}
    .grid {{ display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; padding: 18px; max-width: 1600px; margin: 0 auto; }}
    .panel {{
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
      border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,.25);
    }}
    .panel h2 {{
      margin: 0 0 12px; font-size: 12px; font-weight: 600; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.06em;
    }}
    .span-3 {{ grid-column: span 3; }} .span-4 {{ grid-column: span 4; }}
    .span-6 {{ grid-column: span 6; }} .span-8 {{ grid-column: span 8; }} .span-12 {{ grid-column: span 12; }}
    .kpi-row {{ display: flex; align-items: center; gap: 14px; }}
    .kpi-donut {{ width: 88px; height: 88px; flex-shrink: 0; }}
    .stat {{ font-size: 36px; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }}
    .stat small {{ font-size: 15px; color: var(--muted); font-weight: 500; }}
    .stat.ok {{ color: var(--ok); }} .stat.warn {{ color: var(--warn); }} .stat.bad {{ color: var(--bad); }}
    .legend {{ font-size: 11px; color: var(--muted); margin-top: 6px; line-height: 1.4; }}
    .alert-banner {{
      grid-column: span 12; padding: 12px 16px; border-radius: 8px;
      background: rgba(242,73,92,.12); border: 1px solid rgba(242,73,92,.35);
      color: #ffb4bc; font-size: 13px;
    }}
    .peak-row {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .peak-card {{
      border: 1px solid var(--border); border-radius: 8px; padding: 16px;
      background: linear-gradient(145deg, rgba(255,255,255,.04) 0%, transparent 60%);
      position: relative; overflow: hidden;
    }}
    .peak-card::before {{
      content: ''; position: absolute; top: 0; left: 0; width: 4px; height: 100%;
      background: var(--accent-color);
    }}
    .peak-bw {{ font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--accent-color); letter-spacing: 0.05em; }}
    .peak-tput {{ font-size: 28px; font-weight: 800; margin: 8px 0 4px; letter-spacing: -0.03em; }}
    .peak-mcs {{ font-size: 12px; color: var(--muted); }}
    .peak-bar {{ height: 4px; border-radius: 2px; background: var(--border); margin-top: 10px; overflow: hidden; }}
    .peak-bar span {{ display: block; height: 100%; background: var(--accent-color); border-radius: 2px; }}
    .bw-toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
    .bw-btn {{
      border: 1px solid var(--border); background: transparent; color: var(--text);
      border-radius: 999px; padding: 7px 14px; font-size: 12px; font-weight: 600;
      cursor: pointer; transition: all .15s ease;
    }}
    .bw-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    .bw-btn.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    .bw-btn[data-bw="HT20"].active {{ background: var(--ht20); border-color: var(--ht20); }}
    .bw-btn[data-bw="HT40"].active {{ background: var(--ht40); border-color: var(--ht40); }}
    .bw-btn[data-bw="HT80"].active {{ background: var(--ht80); border-color: var(--ht80); }}
    .heatmap {{
      display: grid;
      grid-template-columns: 52px repeat(24, 1fr);
      gap: 3px; align-items: stretch;
    }}
    .hm-bw-label {{
      display: flex; align-items: center; font-size: 11px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.04em;
    }}
    .hm-cell {{
      aspect-ratio: 1; border-radius: 4px; display: flex; flex-direction: column;
      align-items: center; justify-content: center; font-size: 10px; font-weight: 700;
      cursor: default; transition: transform .12s ease, box-shadow .12s ease;
      border: 1px solid transparent;
    }}
    .hm-cell:hover {{ transform: scale(1.08); z-index: 2; box-shadow: 0 4px 12px rgba(0,0,0,.4); }}
    .hm-cell small {{ font-size: 8px; font-weight: 500; opacity: .75; }}
    .hm-pass {{ background: rgba(115,191,105,.55); color: #e8ffe8; border-color: rgba(115,191,105,.4); }}
    .hm-warn {{ background: rgba(255,152,48,.45); color: #fff4e8; border-color: rgba(255,152,48,.4); }}
    .hm-fail {{ background: rgba(242,73,92,.45); color: #ffe8ea; border-color: rgba(242,73,92,.4); }}
    .hm-skip {{ background: rgba(80,80,90,.35); color: #888; border-color: var(--border); }}
    .hm-legend {{ display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 11px; color: var(--muted); }}
    .hm-legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
    .hm-legend i {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; position: sticky; top: 0; background: var(--panel-2); z-index: 1; }}
    tr.row-pass td {{ background: rgba(115,191,105,.04); }}
    tr.row-warn td {{ background: rgba(255,152,48,.04); }}
    tr.row-fail td {{ background: rgba(242,73,92,.06); }}
    tr.hidden {{ display: none; }}
    .pill {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
    .pill.ok {{ background: rgba(115,191,105,.18); color: var(--ok); }}
    .pill.warn {{ background: rgba(255,152,48,.18); color: var(--warn); }}
    .pill.bad {{ background: rgba(242,73,92,.18); color: var(--bad); }}
    canvas {{ max-height: 300px; }}
    .table-scroll {{ max-height: 480px; overflow: auto; border-radius: 6px; border: 1px solid var(--border); }}
    td.remark {{ color: var(--muted); font-size: 11px; max-width: 260px; }}
    .su-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }}
    .su-card {{
      border: 1px solid var(--border); border-radius: 8px; padding: 12px;
      background: rgba(255,255,255,.02);
    }}
    .su-card .name {{ font-weight: 700; font-size: 13px; }}
    .su-card .ip {{ font-family: monospace; font-size: 10px; color: var(--muted); word-break: break-all; margin: 4px 0 8px; }}
    .su-metrics {{ display: flex; gap: 12px; font-size: 12px; }}
    .su-metrics div span {{ display: block; font-size: 10px; color: var(--muted); text-transform: uppercase; }}
    @media (max-width: 1100px) {{
      .span-3, .span-4, .span-6, .span-8 {{ grid-column: span 12; }}
      .peak-row {{ grid-template-columns: 1fr; }}
      .heatmap {{ grid-template-columns: 40px repeat(12, 1fr); }}
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div>
      <h1>UBR Performance Report #{report_id}</h1>
      <div class="meta">{escape(str(data.get('stand')))} · {escape(str(data.get('profile')))}</div>
      <div class="meta">{escape(str(data.get('generated_at')))} · {escape(str(data.get('source_file', '')))}</div>
    </div>
    <div class="hero-badge">{passed}/{total} passed · Grafana preview</div>
  </header>

  <main class="grid">
    <div class="alert-banner">⚠ {escape(ht40_fail_note)}</div>

    <section class="panel span-3">
      <h2>Matrix pass rate</h2>
      <div class="kpi-row">
        <div class="kpi-donut"><canvas id="passDonut"></canvas></div>
        <div>
          <div class="stat {pass_cls}">{pass_pct}<small> %</small></div>
          <div class="legend">{passed} of {total} iterations<br/>MCS0–23 × HT20/40/80</div>
        </div>
      </div>
    </section>
    <section class="panel span-3">
      <h2>Peak throughput</h2>
      <div class="stat ok">{peak_rx:.0f}<small> Mbps</small></div>
      <div class="legend">{(peaks.get('HT80') or {}).get('mcs', '—')} @ HT80</div>
    </section>
    <section class="panel span-3">
      <h2>Best % of MCS target</h2>
      <div class="stat {pct_cls_top}">{best_pct:.0f}<small> %</small></div>
      <div class="legend">Pass ≥65% · Warn 40–65%</div>
    </section>
    <section class="panel span-3">
      <h2>Connected SUs</h2>
      <div class="stat ok">{su_total}<small> / {su_total}</small></div>
      <div class="legend">4-CPE lab topology</div>
    </section>

    <section class="panel span-12">
      <h2>Peak by bandwidth</h2>
      <div class="peak-row">{''.join(
          f"<div class='peak-card' style='--accent-color:{colors[bw]}'>"
          f"<span class='peak-bw'>{bw}</span>"
          f"<span class='peak-tput'>{float((peaks.get(bw) or {}).get('rx_mbps') or 0):.0f} <small style='font-size:14px;color:var(--muted)'>Mbps</small></span>"
          f"<span class='peak-mcs'>{escape(str((peaks.get(bw) or {}).get('mcs', '—')))} · "
          f"{(peaks.get(bw) or {}).get('passed', 0)}/{(peaks.get(bw) or {}).get('total', 24)} pass</span>"
          f"<div class='peak-bar'><span style='width:{100 * (peaks.get(bw) or {}).get('passed', 0) / max((peaks.get(bw) or {}).get('total', 24), 1):.0f}%'></span></div>"
          f"</div>"
          for bw in ("HT20", "HT40", "HT80")
      )}</div>
    </section>

    <section class="panel span-12">
      <h2>MCS × bandwidth heatmap (% of target)</h2>
      <div class="heatmap">{''.join(heatmap_html)}</div>
      <div class="hm-legend">
        <span><i style="background:rgba(115,191,105,.55)"></i> ≥65% pass</span>
        <span><i style="background:rgba(255,152,48,.45)"></i> 40–65% warn</span>
        <span><i style="background:rgba(242,73,92,.45)"></i> &lt;40% fail</span>
        <span><i style="background:rgba(80,80,90,.35)"></i> skipped</span>
      </div>
    </section>

    <section class="panel span-12">
      <div class="bw-toolbar" id="chartToolbar">
        <span style="font-size:12px;color:var(--muted);font-weight:600">Show:</span>
        <button type="button" class="bw-btn active" data-bw="all">All BW</button>
        <button type="button" class="bw-btn" data-bw="HT20">HT20</button>
        <button type="button" class="bw-btn" data-bw="HT40">HT40</button>
        <button type="button" class="bw-btn" data-bw="HT80">HT80</button>
      </div>
    </section>

    <section class="panel span-8" data-chart-panel="tput">
      <h2>Total throughput by MCS</h2>
      <canvas id="tputBwChart"></canvas>
    </section>
    <section class="panel span-4" data-chart-panel="pass">
      <h2>Pass vs fail by bandwidth</h2>
      <canvas id="passBwChart"></canvas>
    </section>

    <section class="panel span-12" data-chart-panel="pct">
      <h2>% of MCS effective target</h2>
      <canvas id="pctMcsChart"></canvas>
    </section>

    <section class="panel span-12">
      <h2>Per-CPE RF @ peak HT80 ({escape(str((peaks.get('HT80') or {}).get('mcs', '—')))})</h2>
      <div class="su-grid">{''.join(
          f"<div class='su-card'><div class='name'>{escape(s['label'])}</div>"
          f"<div class='ip'>{escape(s.get('ip', '—'))}</div>"
          f"<div class='su-metrics'>"
          f"<div><span>RSSI</span>{s.get('rssi', '—')} dBm</div>"
          f"<div><span>SNR</span>{s.get('snr', '—')} dB</div>"
          f"<div><span>Rate</span>{s.get('rate_mbps', 0)} Mbps</div>"
          f"</div></div>"
          for s in su_rows
      ) if su_rows else '<div class="legend">No RF snapshot available</div>'}</div>
    </section>

    <section class="panel span-12">
      <div class="bw-toolbar" id="tableToolbar">
        <span style="font-size:12px;color:var(--muted);font-weight:600">Filter table:</span>
        <button type="button" class="bw-btn active" data-bw="all">All</button>
        <button type="button" class="bw-btn" data-bw="HT20">HT20</button>
        <button type="button" class="bw-btn" data-bw="HT40">HT40</button>
        <button type="button" class="bw-btn" data-bw="HT80">HT80</button>
        <button type="button" class="bw-btn" data-bw="fail">Failures only</button>
      </div>
      <h2>Throughput matrix ({total} iterations)</h2>
      <div class="table-scroll">
        <table id="matrixTable">
          <thead>
            <tr><th>BW</th><th>MCS</th><th>Total Mbps</th><th>Target</th><th>% target</th><th>Status</th><th>Remarks</th></tr>
          </thead>
          <tbody>{''.join(iter_rows_html)}</tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    const mcsLabels = {json.dumps(mcs_labels)};
    const gridColor = 'rgba(42,51,64,0.6)';
    const chartData = {{
      HT20: {json.dumps(ht20_series)},
      HT40: {json.dumps(ht40_series)},
      HT80: {json.dumps(ht80_series)},
      pctHT20: {json.dumps(pct_ht20)},
      pctHT80: {json.dumps(pct_ht80)},
    }};
    const bwColors = {{ HT20: '#73bf69', HT40: '#5794f2', HT80: '#b877d9' }};
    let activeBw = 'all';

    const common = {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: '#d8d9da', font: {{ family: 'Inter' }} }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#9aa3ad', maxRotation: 0, font: {{ size: 10 }} }}, grid: {{ color: gridColor }} }},
        y: {{ ticks: {{ color: '#9aa3ad' }}, grid: {{ color: gridColor }} }}
      }}
    }};

    new Chart(document.getElementById('passDonut'), {{
      type: 'doughnut',
      data: {{
        labels: ['Pass', 'Fail'],
        datasets: [{{
          data: [{passed}, {total - passed}],
          backgroundColor: ['#73bf69', '#f2495c'],
          borderWidth: 0
        }}]
      }},
      options: {{
        cutout: '72%',
        plugins: {{ legend: {{ display: false }} }},
        maintainAspectRatio: true
      }}
    }});

    const tputChart = new Chart(document.getElementById('tputBwChart'), {{
      type: 'line',
      data: {{ labels: mcsLabels, datasets: [] }},
      options: {{
        ...common,
        interaction: {{ mode: 'index', intersect: false }},
        scales: {{ ...common.scales, y: {{ ...common.scales.y, suggestedMin: 0, title: {{ display: true, text: 'Mbps', color: '#9aa3ad' }} }} }}
      }}
    }});

    const passBwChart = new Chart(document.getElementById('passBwChart'), {{
      type: 'bar',
      data: {{
        labels: ['HT20', 'HT40', 'HT80'],
        datasets: [
          {{ label: 'Pass', data: {json.dumps(bw_pass_counts)}, backgroundColor: '#73bf69', borderRadius: 4 }},
          {{ label: 'Fail', data: {json.dumps(bw_fail_counts)}, backgroundColor: '#f2495c', borderRadius: 4 }}
        ]
      }},
      options: {{ ...common, scales: {{ x: {{ stacked: true, grid: {{ display: false }} }}, y: {{ stacked: true, max: 24, ticks: {{ stepSize: 6 }} }} }} }}
    }});

    const pctChart = new Chart(document.getElementById('pctMcsChart'), {{
      type: 'line',
      data: {{ labels: mcsLabels, datasets: [] }},
      options: {{
        ...common,
        scales: {{
          ...common.scales,
          y: {{ ...common.scales.y, suggestedMin: 0, suggestedMax: 110, title: {{ display: true, text: '% of target', color: '#9aa3ad' }} }}
        }}
      }}
    }});

    function buildDatasets(bw) {{
      const tput = [];
      const pct = [];
      if (bw === 'all' || bw === 'HT20') {{
        tput.push({{ label: 'HT20 Mbps', data: chartData.HT20, borderColor: bwColors.HT20, backgroundColor: 'rgba(115,191,105,.1)', fill: true, tension: 0.3, pointRadius: 2 }});
        pct.push({{ label: 'HT20 %', data: chartData.pctHT20, borderColor: bwColors.HT20, spanGaps: true, tension: 0.3, pointRadius: 2 }});
      }}
      if (bw === 'all' || bw === 'HT40') {{
        tput.push({{ label: 'HT40 Mbps', data: chartData.HT40, borderColor: bwColors.HT40, borderDash: [4,4], tension: 0.3, pointRadius: 2 }});
      }}
      if (bw === 'all' || bw === 'HT80') {{
        tput.push({{ label: 'HT80 Mbps', data: chartData.HT80, borderColor: bwColors.HT80, backgroundColor: 'rgba(184,119,217,.1)', fill: true, tension: 0.3, pointRadius: 2 }});
        pct.push({{ label: 'HT80 %', data: chartData.pctHT80, borderColor: bwColors.HT80, spanGaps: true, tension: 0.3, pointRadius: 2 }});
      }}
      pct.push({{ label: '65% pass', data: Array(24).fill(65), borderColor: 'rgba(115,191,105,.5)', borderDash: [6,4], pointRadius: 0, fill: false }});
      pct.push({{ label: '40% warn', data: Array(24).fill(40), borderColor: 'rgba(255,152,48,.4)', borderDash: [6,4], pointRadius: 0, fill: false }});
      return {{ tput, pct }};
    }}

    function updateCharts(bw) {{
      activeBw = bw;
      const {{ tput, pct }} = buildDatasets(bw);
      tputChart.data.datasets = tput;
      tputChart.update();
      pctChart.data.datasets = pct;
      pctChart.update();
    }}
    updateCharts('all');

    function wireToolbar(id, onSelect) {{
      document.getElementById(id).addEventListener('click', (e) => {{
        const btn = e.target.closest('.bw-btn');
        if (!btn) return;
        id === 'chartToolbar' ? updateCharts(btn.dataset.bw) : onSelect(btn.dataset.bw);
        document.querySelectorAll(`#${{id}} .bw-btn`).forEach(b => b.classList.toggle('active', b === btn));
      }});
    }}

    wireToolbar('chartToolbar', () => {{}});
    wireToolbar('tableToolbar', (bw) => {{
      document.querySelectorAll('#matrixTable tbody tr').forEach(row => {{
        const rowBw = row.dataset.bw;
        const isFail = row.classList.contains('row-fail');
        const show = bw === 'all' ? true : bw === 'fail' ? isFail : rowBw === bw;
        row.classList.toggle('hidden', !show);
      }});
    }});
  </script>
</body>
</html>
"""
  out.write_text(html, encoding="utf-8")
  return out
