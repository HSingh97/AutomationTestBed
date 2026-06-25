#!/usr/bin/env python3
"""Generate sample Grafana-style HTML report and export dashboard JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.grafana_sample_report import (
    default_sample_payload,
    parse_performance_report_html,
    write_grafana_sample_html,
)


def _write_dashboard_json(path: Path, payload: dict) -> None:
    """Minimal Grafana 10.x dashboard using TestData datasource."""
    iterations = payload.get("iterations") or []
    is_matrix = payload.get("report_kind") == "matrix"
    title = (
        f"UBR Performance Report #{payload.get('report_id')} (Sample)"
        if is_matrix
        else "UBR Lab — Performance & Link Health (Sample)"
    )
    uid = "ubr-performance-73" if is_matrix else "ubr-lab-sample"

    dashboard = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": _matrix_panels(iterations, payload) if is_matrix else _jenkins_panels(iterations),
        "refresh": "",
        "schemaVersion": 39,
        "tags": ["ubr", "lab", "throughput", "sample"],
        "templating": {"list": []},
        "time": {"from": "now-7d", "to": "now"},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dashboard, indent=2), encoding="utf-8")


def _jenkins_panels(iterations: list[dict]) -> list[dict]:
    return [
        {
            "datasource": {"type": "grafana-testdata-datasource", "uid": "grafana"},
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "max": 4,
                    "min": 0,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "red", "value": None},
                            {"color": "orange", "value": 3},
                            {"color": "green", "value": 4},
                        ],
                    },
                    "unit": "none",
                }
            },
            "gridPos": {"h": 5, "w": 4, "x": 0, "y": 0},
            "id": 1,
            "options": {
                "colorMode": "value",
                "graphMode": "none",
                "justifyMode": "center",
                "orientation": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "title": "Connected SUs",
            "type": "stat",
            "targets": [
                {
                    "refId": "A",
                    "scenarioId": "csv_content",
                    "csvContent": "time,value\n2026-06-19T00:00:00Z,3",
                }
            ],
        },
        {
            "datasource": {"type": "grafana-testdata-datasource", "uid": "grafana"},
            "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "unit": "Mbits"}},
            "gridPos": {"h": 8, "w": 12, "x": 4, "y": 0},
            "id": 2,
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "single"}},
            "title": "Combined RX throughput (sample)",
            "type": "timeseries",
            "targets": [
                {
                    "refId": "A",
                    "scenarioId": "csv_content",
                    "csvContent": _csv_series(iterations, value_key="rx_mbps"),
                },
                {
                    "refId": "B",
                    "scenarioId": "csv_content",
                    "csvContent": _csv_series(iterations, value_key="target_mbps"),
                },
            ],
        },
        {
            "gridPos": {"h": 8, "w": 16, "x": 8, "y": 10},
            "id": 5,
            "options": {"content": _markdown_table(iterations), "mode": "markdown"},
            "title": "Matrix iterations (sample data)",
            "type": "text",
        },
    ]


def _matrix_panels(iterations: list[dict], payload: dict) -> list[dict]:
    ht20 = [row for row in iterations if row.get("bandwidth") == "HT20"]
    ht80 = [row for row in iterations if row.get("bandwidth") == "HT80"]
    passed = int(payload.get("matrix_passed") or 0)
    total = int(payload.get("matrix_total") or len(iterations))
    peak = max((float(row.get("rx_mbps") or 0) for row in iterations), default=0.0)

    return [
        {
            "datasource": {"type": "grafana-testdata-datasource", "uid": "grafana"},
            "fieldConfig": {"defaults": {"unit": "none"}},
            "gridPos": {"h": 4, "w": 4, "x": 0, "y": 0},
            "id": 1,
            "title": "Matrix pass rate",
            "type": "stat",
            "targets": [
                {
                    "refId": "A",
                    "scenarioId": "csv_content",
                    "csvContent": f"time,value\n2026-06-18T19:10:23Z,{passed}/{total}",
                }
            ],
        },
        {
            "datasource": {"type": "grafana-testdata-datasource", "uid": "grafana"},
            "fieldConfig": {"defaults": {"unit": "Mbits"}},
            "gridPos": {"h": 4, "w": 4, "x": 4, "y": 0},
            "id": 2,
            "title": "Peak throughput",
            "type": "stat",
            "targets": [
                {
                    "refId": "A",
                    "scenarioId": "csv_content",
                    "csvContent": f"time,value\n2026-06-18T19:10:23Z,{peak:.1f}",
                }
            ],
        },
        {
            "datasource": {"type": "grafana-testdata-datasource", "uid": "grafana"},
            "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "unit": "Mbits"}},
            "gridPos": {"h": 9, "w": 12, "x": 0, "y": 4},
            "id": 3,
            "title": "HT20 total throughput by MCS",
            "type": "timeseries",
            "targets": [
                {
                    "refId": "A",
                    "scenarioId": "csv_content",
                    "csvContent": _csv_series(ht20, value_key="rx_mbps", label_key="mcs"),
                }
            ],
        },
        {
            "datasource": {"type": "grafana-testdata-datasource", "uid": "grafana"},
            "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "unit": "Mbits"}},
            "gridPos": {"h": 9, "w": 12, "x": 12, "y": 4},
            "id": 4,
            "title": "HT80 total throughput by MCS",
            "type": "timeseries",
            "targets": [
                {
                    "refId": "A",
                    "scenarioId": "csv_content",
                    "csvContent": _csv_series(ht80, value_key="rx_mbps", label_key="mcs"),
                }
            ],
        },
        {
            "gridPos": {"h": 10, "w": 24, "x": 0, "y": 13},
            "id": 5,
            "options": {"content": _matrix_markdown_table(iterations), "mode": "markdown"},
            "title": "Throughput matrix (report #73)",
            "type": "text",
        },
    ]


def _csv_series(
    rows: list[dict],
    *,
    value_key: str,
    label_key: str | None = None,
) -> str:
    lines = ["time,value"]
    for idx, row in enumerate(rows):
        label = row.get(label_key, idx) if label_key else idx
        ts = f"2026-06-18T{idx % 24:02d}:{(idx // 24) * 15:02d}:00Z"
        lines.append(f"{ts},{float(row.get(value_key) or 0)}  # {label}")
    return "\n".join(lines)


def _markdown_table(rows: list[dict]) -> str:
    header = "| Build | BW | MCS | VLAN | SUs | RX | Target | Pass |\n|---|---|---|---|---|---|---|---|"
    body = []
    for r in rows:
        body.append(
            f"| #{r.get('build', '—')} | {r['bandwidth']} | {r['mcs']} | {r.get('vlan_mode', '—')} "
            f"| {r.get('su_linked', 0)}/4 | {float(r.get('rx_mbps') or 0):.1f} "
            f"| {float(r.get('target_mbps') or 0):.1f} | {'✅' if r.get('passed') else '❌'} |"
        )
    return header + "\n" + "\n".join(body)


def _matrix_markdown_table(rows: list[dict]) -> str:
    header = "| BW | MCS | Total Mbps | Target | % | Pass | Remarks |\n|---|---|---:|---:|---:|---|---|"
    body = []
    for r in rows:
        pct = float(r.get("pct") or 0)
        remark = str(r.get("remark") or "—").replace("|", "/")
        body.append(
            f"| {r['bandwidth']} | {r['mcs']} | {float(r.get('rx_mbps') or 0):.1f} "
            f"| {float(r.get('target_mbps') or 0):.1f} | {pct:.0f}% "
            f"| {'✅' if r.get('passed') else '❌'} | {remark} |"
        )
    return header + "\n" + "\n".join(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Grafana-style sample HTML + dashboard JSON")
    parser.add_argument(
        "--from-report",
        type=Path,
        help="Senao performance report HTML to convert (e.g. Senao_Performance_73_Report_*.html)",
    )
    parser.add_argument(
        "--html-out",
        type=Path,
        help="Output path for standalone HTML preview",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Output path for Grafana dashboard JSON",
    )
    args = parser.parse_args()

    if args.from_report:
        payload = parse_performance_report_html(args.from_report)
        report_id = payload.get("report_id", "report")
        html_path = args.html_out or ROOT / "docs" / "samples" / f"Senao_Performance_{report_id}_Grafana_Report.html"
        dash_path = args.json_out or ROOT / "docs" / "samples" / "grafana" / f"performance-{report_id}-dashboard.json"
    else:
        payload = default_sample_payload()
        html_path = args.html_out or ROOT / "docs" / "samples" / "Senao_Lab_Grafana_Sample_Report.html"
        dash_path = args.json_out or ROOT / "docs" / "samples" / "grafana" / "ubr-lab-dashboard.json"

    write_grafana_sample_html(html_path, payload)
    _write_dashboard_json(dash_path, payload)

    print(f"Wrote HTML sample: {html_path}")
    print(f"Wrote Grafana dashboard JSON: {dash_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
