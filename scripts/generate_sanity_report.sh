#!/usr/bin/env bash
# Build Senao Sanity HTML (same engine as Alpha / Alpha 2 lab reports).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.

PY="${PY:-}"
if [[ -z "$PY" || ! -x "$PY" ]]; then
  if [[ -x "$ROOT/venv/bin/python" ]]; then
    PY="$ROOT/venv/bin/python"
  elif [[ -x /home/senao/Desktop/PythonProjects/.venv/bin/python ]]; then
    PY=/home/senao/Desktop/PythonProjects/.venv/bin/python
  else
    PY=python3
  fi
fi

OUT="${1:-}"
PLAN="${PLAN_XLSX:-}"
if [[ -z "$PLAN" ]]; then
  PLAN="$HOME/Downloads/Senao UBR P2MP Test Plan_v8.0a.xlsx"
fi
DATE="${REPORT_DATE:-$(date +%Y%m%d)}"
RUNS_DIR="${RUNS_DIR:-reports/artifacts/case_runs}"
REPORT_JSON="${REPORT_JSON:-reports/artifacts/sanity_report.json}"

if [[ ! -f "$PLAN" ]]; then
  echo "[sanity-report] plan missing: $PLAN" >&2
  exit 1
fi

mkdir -p reports/artifacts

# Prefer live testbed_summary from pytest; else write Alpha-2 shaped header.
"$PY" - <<'PY'
import json
from pathlib import Path

p = Path("reports/artifacts/testbed_summary.json")
if p.is_file():
    try:
        data = json.loads(p.read_text())
        if (data.get("bts") or {}).get("model"):
            print(f"[sanity-report] keeping {p}")
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        pass
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(
    json.dumps(
        {
            "su_count": 1,
            "bts": {
                "model": "UBR655",
                "fw_version": "1.0.0.149",
                "ip": "2401:4900:d0:40d4:0:17b8:0:330",
            },
            "cpe": {
                "model": "UBR655",
                "fw_version": "1.0.0.149",
                "ip": "2401:4900:d0:40d4::17b8:0:331",
            },
        },
        indent=2,
    )
    + "\n"
)
print(f"[sanity-report] wrote default testbed_summary -> {p.resolve()}")
PY

if [[ -z "$OUT" ]]; then
  OUT="reports/artifacts/Senao_Sanity_Report_${DATE}.html"
fi

"$PY" utils/sanity_plan_report.py \
  --report-json "$REPORT_JSON" \
  --runs-dir "$RUNS_DIR" \
  --plan "$PLAN" \
  --date "$DATE" \
  --out "$OUT"

echo "[sanity-report] OK $OUT"
