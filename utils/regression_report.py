"""Regression iteration collector and standalone HTML report renderer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


@dataclass
class HealthCheckResult:
    """Single ping or web check for one device role."""

    check_id: str
    device_role: str
    device_host: str
    check_type: str
    passed: bool
    detail: str = ""


@dataclass
class IterationRecord:
    case_id: str
    phase: str
    checks: list[HealthCheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(item.passed for item in self.checks)


class RegressionReportCollector:
    def __init__(self) -> None:
        self.iterations: list[IterationRecord] = []
        self.meta: dict[str, Any] = {}

    def set_meta(self, **kwargs: Any) -> None:
        self.meta.update(kwargs)

    def record_iteration(self, case_id: str, phase: str, checks: list[HealthCheckResult]) -> IterationRecord:
        record = IterationRecord(case_id=case_id, phase=phase, checks=list(checks))
        self.iterations.append(record)
        return record

    def summary_for_test(self, nodeid: str) -> str:
        case_hint = nodeid.split("::")[-1].upper()
        rows = [
            it
            for it in self.iterations
            if case_hint in it.case_id.upper() or it.case_id.upper() in case_hint
        ]
        if not rows:
            rows = self.iterations[-3:]
        rows = [row for row in rows if row.phase.lower() != "baseline"]
        if not rows:
            return "<p>No post-reset iteration data recorded.</p>"
        parts = []
        for row in rows:
            parts.append(
                f"<p><strong>{escape(_format_phase_label(row.phase))}</strong></p>"
                f"{_render_check_table(_iteration_matrix(row))}"
            )
        return "".join(parts)

    def render_html(self, output_path: Path, *, pytest_stats: dict[str, int] | None = None) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html = _build_html(self, pytest_stats=pytest_stats)
        output_path.write_text(html, encoding="utf-8")
        return output_path


_COLLECTOR: RegressionReportCollector | None = None


def get_regression_collector() -> RegressionReportCollector:
    global _COLLECTOR
    if _COLLECTOR is None:
        _COLLECTOR = RegressionReportCollector()
    return _COLLECTOR


def reset_regression_collector() -> None:
    global _COLLECTOR
    _COLLECTOR = RegressionReportCollector()


def _format_phase_label(phase: str) -> str:
    match = re.search(r"cycle-(\d+)", phase, flags=re.IGNORECASE)
    if match:
        return f"Iteration {match.group(1)}"
    return phase.replace("-", " ").title()


def _checkmark(ok: bool | None) -> str:
    if ok is None:
        return "<span class='mark na'>&mdash;</span>"
    if ok:
        return "<span class='mark pass'>&#10003;</span>"
    return "<span class='mark fail'>&#10007;</span>"


def _all_pass(values: list[bool]) -> bool | None:
    if not values:
        return None
    return all(values)


def _iteration_matrix(record: IterationRecord) -> dict[str, bool | None]:
    return {
        "ping_bts": _all_pass(
            [c.passed for c in record.checks if c.check_type.lower() == "ping" and "BTS" in c.device_role]
        ),
        "ping_cpe": _all_pass(
            [c.passed for c in record.checks if c.check_type.lower() == "ping" and "CPE" in c.device_role]
        ),
        "web_bts": _all_pass(
            [c.passed for c in record.checks if c.check_type.lower() == "web" and "BTS" in c.device_role]
        ),
        "web_cpe": _all_pass(
            [c.passed for c in record.checks if c.check_type.lower() == "web" and "CPE" in c.device_role]
        ),
    }


def _render_testbed_summary_table(summary: dict[str, Any]) -> str:
    """Fixed header: Model, FW, IP, Vlan, QOS for BTS and CPE."""
    bts = summary.get("bts", {}) if summary else {}
    cpe = summary.get("cpe", {}) if summary else {}

    def cell(data: dict, key: str) -> str:
        value = str(data.get(key, "—") or "—")
        if key == "ip":
            return f"<span class='ip-cell'>{escape(value)}</span>"
        return escape(value)

    rows = (
        ("Model", "model"),
        ("FW Version", "fw_version"),
        ("IP", "ip"),
        ("Vlan", "vlan"),
        ("QOS", "qos"),
    )
    body_rows = []
    for label, key in rows:
        body_rows.append(
            f"""
            <tr>
              <th class="row-label">{label}</th>
              <td>{cell(bts, key)}</td>
              <td>{cell(cpe, key)}</td>
            </tr>
            """
        )

    return f"""
    <table class="matrix summary-top">
      <thead>
        <tr>
          <th class="corner"></th>
          <th>BTS</th>
          <th>CPE</th>
        </tr>
      </thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
    """


def _render_check_table(matrix: dict[str, bool | None]) -> str:
    """BTS/CPE column table: rows Ping and Web Access with checkmarks."""
    return f"""
    <table class="matrix">
      <thead>
        <tr>
          <th class="corner"></th>
          <th>BTS</th>
          <th>CPE</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th class="row-label">Ping</th>
          <td class="cell">{_checkmark(matrix['ping_bts'])}</td>
          <td class="cell">{_checkmark(matrix['ping_cpe'])}</td>
        </tr>
        <tr>
          <th class="row-label">Web Access</th>
          <td class="cell">{_checkmark(matrix['web_bts'])}</td>
          <td class="cell">{_checkmark(matrix['web_cpe'])}</td>
        </tr>
      </tbody>
    </table>
    """


def _build_html(collector: RegressionReportCollector, *, pytest_stats: dict[str, int] | None) -> str:
    meta = collector.meta
    executed_at = escape(str(meta.get("executed_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    iterations_target = escape(str(meta.get("iterations", "—")))
    testbed_summary = meta.get("testbed_summary", {})
    testbed_table = _render_testbed_summary_table(testbed_summary)

    visible_iterations = [it for it in collector.iterations if it.phase.lower() != "baseline"]
    total_iters = len(visible_iterations)
    passed_iters = sum(1 for it in visible_iterations if it.passed)
    failed_iters = total_iters - passed_iters

    iteration_blocks = []
    for record in visible_iterations:
        matrix = _iteration_matrix(record)
        status_class = "ok" if record.passed else "bad"
        iteration_blocks.append(
            f"""
            <article class="iteration {status_class}">
              <div class="iteration-title">
                <h3>{escape(record.case_id)}</h3>
                <span class="phase">{escape(_format_phase_label(record.phase))}</span>
                <span class="status-pill {'pass' if record.passed else 'fail'}">
                  {'PASS' if record.passed else 'FAIL'}
                </span>
              </div>
              {_render_check_table(matrix)}
            </article>
            """
        )

    if not iteration_blocks:
        iteration_blocks.append(
            "<article class='iteration'><p>No iteration data captured. Run with <code>--allow-regression</code>.</p></article>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR Stability Regression Report</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    :root {{
      --bg: #eef2f7; --card: #ffffff; --text: #334155; --title: #0f172a;
      --border: #cbd5e1; --pass: #16a34a; --fail: #dc2626; --head: #1e3a8a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 28px 18px; font-family: 'Inter', sans-serif;
      background: var(--bg); color: var(--text);
    }}
    .wrap {{ max-width: 920px; margin: 0 auto; }}
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #2563eb 100%);
      color: #fff; border-radius: 14px; padding: 22px 26px; margin-bottom: 18px;
    }}
    .hero h1 {{ margin: 0 0 6px; font-size: 24px; }}
    .hero p {{ margin: 0; font-size: 13px; opacity: 0.92; }}
    .panel-top {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px; margin-bottom: 16px;
    }}
    .panel-top h2 {{ margin: 0 0 14px; font-size: 17px; color: var(--title); }}
    .run-meta {{
      display: flex; gap: 20px; flex-wrap: wrap; margin-top: 14px;
      font-size: 13px; color: #475569;
    }}
    .run-meta strong {{ color: var(--title); }}
    table.summary-top {{ max-width: 100%; margin-bottom: 0; }}
    .ip-cell {{ white-space: nowrap; font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; }}
    .summary {{
      display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;
    }}
    .chip {{
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 12px 16px; min-width: 110px; text-align: center;
    }}
    .chip strong {{ display: block; font-size: 22px; color: var(--title); }}
    .chip span {{ font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 600; }}
    .chip.pass strong {{ color: var(--pass); }}
    .chip.fail strong {{ color: var(--fail); }}
    .panel {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px;
    }}
    .panel > h2 {{ margin: 0 0 16px; font-size: 17px; color: var(--title); }}
    .iteration {{
      border: 1px solid var(--border); border-radius: 12px; padding: 16px;
      margin-bottom: 14px; background: #fafcff;
    }}
    .iteration.ok {{ border-left: 5px solid var(--pass); }}
    .iteration.bad {{ border-left: 5px solid var(--fail); background: #fff8f8; }}
    .iteration-title {{
      display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap;
    }}
    .iteration-title h3 {{ margin: 0; font-size: 15px; color: var(--title); }}
    .phase {{
      font-size: 13px; font-weight: 600; color: #475569; background: #e2e8f0;
      padding: 4px 10px; border-radius: 6px;
    }}
    .status-pill {{
      margin-left: auto; font-size: 11px; font-weight: 700; letter-spacing: 0.4px;
      padding: 5px 12px; border-radius: 999px;
    }}
    .status-pill.pass {{ background: #dcfce7; color: #166534; }}
    .status-pill.fail {{ background: #fee2e2; color: #991b1b; }}
    table.matrix {{
      width: 100%; max-width: 420px; border-collapse: collapse; background: #fff;
      border: 2px solid var(--border); border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(15,23,42,0.06);
    }}
    table.matrix th, table.matrix td {{
      border: 1px solid var(--border); padding: 14px 20px; text-align: center;
    }}
    table.matrix thead th {{
      background: #f8fafc; color: var(--head); font-size: 14px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.5px;
    }}
    table.matrix th.corner {{ background: #f1f5f9; width: 120px; }}
    table.matrix th.row-label {{
      text-align: left; background: #f8fafc; color: var(--title);
      font-size: 14px; font-weight: 600; padding-left: 16px;
    }}
    table.matrix td.cell {{ background: #fff; }}
    .mark {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 32px; height: 32px; font-size: 20px; font-weight: 700; border-radius: 8px;
    }}
    .mark.pass {{ color: var(--pass); background: #ecfdf5; }}
    .mark.fail {{ color: var(--fail); background: #fef2f2; }}
    .mark.na {{ color: #64748b; background: #f1f5f9; font-size: 16px; }}
    .footnote {{ margin-top: 14px; font-size: 12px; color: #64748b; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <h1>UBR Stability Regression Report</h1>
      <p>Network reload / reboot cycles — ping and web access on BTS and CPE (iterations only)</p>
    </header>

    <section class="panel-top">
      <h2>Testbed Summary</h2>
      {testbed_table}
      <div class="run-meta">
        <span><strong>Executed:</strong> {executed_at}</span>
        <span><strong>Target cycles:</strong> {iterations_target}</span>
      </div>
    </section>

    <section class="summary">
      <div class="chip"><strong>{total_iters}</strong><span>Iterations</span></div>
      <div class="chip pass"><strong>{passed_iters}</strong><span>Passed</span></div>
      <div class="chip fail"><strong>{failed_iters}</strong><span>Failed</span></div>
    </section>

    <section class="panel">
      <h2>Iteration Results</h2>
      {''.join(iteration_blocks)}
      <p class="footnote">
        &#10003; = check passed &nbsp;&nbsp; &#10007; = check failed.
        Ping: BTS column = BTS&#8594;CPE, CPE column = CPE&#8594;BTS.
      </p>
    </section>
  </div>
</body>
</html>"""
