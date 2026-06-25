#!/usr/bin/env python3
"""Build Grafana-style HTML from a performance_matrix_summary.json export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.grafana_matrix_report import enrich_matrix_payload, write_matrix_grafana_html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary_json", type=Path, help="performance_matrix_summary.json path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output HTML path (default: alongside JSON with _Grafana suffix)",
    )
    parser.add_argument("--report-id", default="", help="Build id for report header")
    parser.add_argument("--stand", default="")
    parser.add_argument("--profile", default="")
    args = parser.parse_args()

    data = json.loads(args.summary_json.read_text(encoding="utf-8"))
    records = data.get("records") or []
    report_id = args.report_id or args.summary_json.parent.name.replace("performance_", "")
    out = args.output or args.summary_json.with_name(
        f"Performance_Report_{report_id}_Grafana.html"
    )

    payload = enrich_matrix_payload(
        records,
        testbed_summary=data.get("testbed_summary") or {},
        report_id=report_id,
        generated_at=str(data.get("finished_at") or data.get("started_at") or ""),
        stand=args.stand,
        profile=args.profile,
        source_file=args.summary_json.name,
    )
    write_matrix_grafana_html(out, payload)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
