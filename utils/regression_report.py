"""Regression iteration collector and standalone HTML report renderer."""

from __future__ import annotations

import fcntl
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

DEFAULT_REGRESSION_REPORT = Path("reports/Regression_Report.html")
DEFAULT_STATE_FILE = Path("reports/regression_collector_state.json")

SENAO_LOGO_URL = (
    "https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png"
)

# Human-readable labels so REG_01 / REG_02 are not repeated without context.
CASE_CATALOG: dict[str, dict[str, str]] = {
    "REG_01": {
        "title": "Soft Reboot",
        "description": "BTS reboot; after each cycle verify BTS↔CPE ping and web login",
        "badge_class": "badge-reboot",
    },
    "REG_02": {
        "title": "Network Soft Reset",
        "description": "CPE then BTS /etc/init.d/network reload; same health checks",
        "badge_class": "badge-reset",
    },
    "REG_03": {
        "title": "Firmware Upgrade",
        "description": "Flash firmware image; verify link after upgrade reboot",
        "badge_class": "badge-firmware",
    },
}


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
        self._state_path: Path | None = None

    def set_state_path(self, path: Path | str | None) -> None:
        self._state_path = Path(path) if path else None

    def set_meta(self, **kwargs: Any) -> None:
        self.meta.update(kwargs)

    def register_run_case(self, case_id: str, iterations: int) -> None:
        """Track each regression case in one report (REG_01, REG_02, …)."""
        cases: list[dict[str, Any]] = self.meta.setdefault("cases", [])
        for entry in cases:
            if entry.get("case_id") == case_id:
                entry["iterations"] = iterations
                return
        cases.append({"case_id": case_id, "iterations": iterations})
        self._persist_state()

    def record_iteration(self, case_id: str, phase: str, checks: list[HealthCheckResult]) -> IterationRecord:
        record = IterationRecord(case_id=case_id, phase=phase, checks=list(checks))
        self.iterations.append(record)
        self._persist_state()
        return record

    def merge_from(self, other: RegressionReportCollector) -> None:
        self.iterations.extend(other.iterations)
        for key, value in other.meta.items():
            if key == "cases":
                for case in value:
                    self.register_run_case(case["case_id"], int(case["iterations"]))
            elif key not in self.meta:
                self.meta[key] = value

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        _save_collector_state(self._state_path, self)

    @classmethod
    def load_from_state_file(cls, path: Path | str) -> RegressionReportCollector:
        collector = cls()
        collector.set_state_path(path)
        data = _load_collector_state(Path(path))
        if not data:
            return collector
        collector.meta = dict(data.get("meta") or {})
        for row in data.get("iterations") or []:
            checks = [HealthCheckResult(**item) for item in row.get("checks") or []]
            collector.iterations.append(
                IterationRecord(case_id=row["case_id"], phase=row["phase"], checks=checks)
            )
        return collector

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


def _locked_json_read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            payload = json.load(handle)
        except json.JSONDecodeError:
            payload = {}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return payload if isinstance(payload, dict) else {}


def _locked_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(payload, handle, indent=2)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _collector_to_dict(collector: RegressionReportCollector) -> dict[str, Any]:
    return {
        "meta": collector.meta,
        "iterations": [
            {
                "case_id": record.case_id,
                "phase": record.phase,
                "checks": [asdict(check) for check in record.checks],
            }
            for record in collector.iterations
        ],
    }


def _load_collector_state(path: Path) -> dict[str, Any]:
    return _locked_json_read(path)


def _save_collector_state(path: Path, collector: RegressionReportCollector) -> None:
    existing = _load_collector_state(path)
    incoming = _collector_to_dict(collector)
    merged_rows = _merge_iteration_rows(
        existing.get("iterations") or [],
        incoming.get("iterations") or [],
    )
    meta = dict(existing.get("meta") or {})
    meta.update(incoming.get("meta") or {})
    cases_by_id = {c["case_id"]: c for c in meta.get("cases", []) if c.get("case_id")}
    for case in incoming.get("meta", {}).get("cases", []):
        if case.get("case_id"):
            cases_by_id[case["case_id"]] = case
    meta["cases"] = list(cases_by_id.values())
    payload = {"meta": meta, "iterations": merged_rows}
    _locked_json_write(path, payload)
    collector.meta = meta
    collector.iterations = [
        IterationRecord(
            case_id=row["case_id"],
            phase=row["phase"],
            checks=[HealthCheckResult(**item) for item in row.get("checks") or []],
        )
        for row in merged_rows
    ]


def _merge_iteration_rows(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append new cycle rows; replace same case_id+phase if re-run."""
    merged = {(_row["case_id"], _row["phase"]): _row for _row in existing}
    for row in incoming:
        merged[(row["case_id"], row["phase"])] = row
    return list(merged.values())


def init_regression_collector_for_session(
    *,
    state_path: Path | str,
    append: bool,
) -> RegressionReportCollector:
    """One shared collector for parallel/sequential runs writing the same report."""
    global _COLLECTOR
    path = Path(state_path)
    if append and path.is_file():
        _COLLECTOR = RegressionReportCollector.load_from_state_file(path)
    else:
        _COLLECTOR = RegressionReportCollector()
    _COLLECTOR.set_state_path(path)
    return _COLLECTOR


def _case_info(case_id: str) -> dict[str, str]:
    return CASE_CATALOG.get(
        case_id,
        {
            "title": case_id,
            "description": "Stability regression",
            "badge_class": "badge-other",
        },
    )


def _format_phase_label(phase: str) -> str:
    match = re.search(r"cycle-(\d+)", phase, flags=re.IGNORECASE)
    if match:
        return f"Cycle {match.group(1)}"
    return phase.replace("-", " ").title()


def _iteration_sort_key(record: IterationRecord) -> tuple[str, int, str]:
    match = re.search(r"cycle-(\d+)", record.phase, flags=re.IGNORECASE)
    cycle_num = int(match.group(1)) if match else 9999
    return (record.case_id, cycle_num, record.phase)


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


def _pytest_stat_chips(total, passed, failed) -> str:
    if total is None:
        return ""
    failed_count = failed if failed is not None else 0
    passed_count = passed if passed is not None else 0
    return f"""
      <div class="chip"><strong>{total}</strong><span>Pytest cases</span></div>
      <div class="chip pass"><strong>{passed_count}</strong><span>Cases passed</span></div>
      <div class="chip fail"><strong>{failed_count}</strong><span>Cases failed</span></div>
    """


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
    <table class="data-table summary-top">
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


def _compact_pass_summary(matrix: dict[str, bool | None]) -> str:
    labels = []
    if matrix.get("ping_bts"):
        labels.append("BTS→CPE ping")
    if matrix.get("ping_cpe"):
        labels.append("CPE→BTS ping")
    if matrix.get("web_bts"):
        labels.append("BTS web")
    if matrix.get("web_cpe"):
        labels.append("CPE web")
    return " · ".join(labels) if labels else "All checks passed"


def _render_failure_notes(record: IterationRecord) -> str:
    failures = [c for c in record.checks if not c.passed]
    if not failures:
        return ""
    items = "".join(
        f"<li><strong>{escape(c.check_type)}</strong> ({escape(c.device_role)}): "
        f"{escape(c.detail or 'failed')}</li>"
        for c in failures
    )
    return f"<ul class='fail-list'>{items}</ul>"


def _render_run_types_overview(
    cases: list[dict[str, Any]], visible_iterations: list[IterationRecord]
) -> str:
    """Summary table: which test type (reboot vs reset) and pass/fail per type."""
    case_ids = [c.get("case_id") for c in cases if c.get("case_id")]
    if not case_ids:
        case_ids = sorted({r.case_id for r in visible_iterations})

    rows = []
    for case_id in case_ids:
        info = _case_info(str(case_id))
        subset = [r for r in visible_iterations if r.case_id == case_id]
        passed_n = sum(1 for r in subset if r.passed)
        failed_n = len(subset) - passed_n
        target = next((c.get("iterations") for c in cases if c.get("case_id") == case_id), len(subset))
        status = "pass" if subset and failed_n == 0 else ("fail" if failed_n else "na")
        rows.append(
            f"""
            <tr>
              <td><span class="test-type-badge {info['badge_class']}">{escape(info['title'])}</span>
                  <span class="test-id-inline">{escape(case_id)}</span></td>
              <td>{escape(info['description'])}</td>
              <td class="num">{escape(str(target))}</td>
              <td class="num pass-cell">{passed_n}</td>
              <td class="num fail-cell">{failed_n}</td>
              <td><span class="status-pill {status}">{'PASS' if status == 'pass' else ('FAIL' if status == 'fail' else '—')}</span></td>
            </tr>
            """
        )
    if not rows:
        return ""
    return f"""
    <h3 class="subhead">Regression test types in this report</h3>
    <table class="data-table run-types">
      <thead>
        <tr>
          <th>Test</th>
          <th>Description</th>
          <th>Target cycles</th>
          <th>Passed</th>
          <th>Failed</th>
          <th>Overall</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _render_iteration_article(record: IterationRecord) -> str:
    info = _case_info(record.case_id)
    matrix = _iteration_matrix(record)
    passed = record.passed
    status_class = "ok" if passed else "bad"
    details_attr = "" if passed else " open"

    compact = ""
    if passed:
        compact = f"""
        <p class="pass-summary">
          <span class="mark pass large">&#10003;</span>
          <span>All checks passed — {_compact_pass_summary(matrix)}</span>
        </p>
        """

    failures_html = _render_failure_notes(record)

    return f"""
    <article class="iteration {status_class} {info['badge_class']}">
      <div class="iteration-title">
        <div class="iteration-head">
          <span class="test-type-badge {info['badge_class']}">{escape(info['title'])}</span>
          <span class="test-id-inline">{escape(record.case_id)}</span>
        </div>
        <span class="phase">{escape(_format_phase_label(record.phase))}</span>
        <span class="status-pill {'pass' if passed else 'fail'}">{'PASS' if passed else 'FAIL'}</span>
      </div>
      {compact}
      <details class="check-details"{details_attr}>
        <summary>{'Show ping &amp; web details' if passed else 'Ping &amp; web details (failures)'}</summary>
        {_render_check_table(matrix)}
        {failures_html}
      </details>
    </article>
    """


def _render_iteration_sections(visible_iterations: list[IterationRecord]) -> str:
    sorted_rows = sorted(visible_iterations, key=_iteration_sort_key)
    blocks: list[str] = []
    last_case: str | None = None
    for record in sorted_rows:
        if record.case_id != last_case:
            info = _case_info(record.case_id)
            blocks.append(
                f"""
                <div class="test-section">
                  <h3 class="test-section-title">
                    <span class="test-type-badge {info['badge_class']}">{escape(info['title'])}</span>
                    <span class="test-id-inline">{escape(record.case_id)}</span>
                  </h3>
                  <p class="test-section-desc">{escape(info['description'])}</p>
                </div>
                """
            )
            last_case = record.case_id
        blocks.append(_render_iteration_article(record))
    return "".join(blocks)


def _render_check_table(matrix: dict[str, bool | None]) -> str:
    """BTS/CPE column table: rows Ping and Web Access with checkmarks."""
    return f"""
    <table class="data-table check-matrix">
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
    cases = meta.get("cases") or []
    if cases:
        parts = []
        for case in cases:
            cid = str(case.get("case_id", "?"))
            info = _case_info(cid)
            parts.append(f"{info['title']} ({cid}) × {case.get('iterations', '?')}")
        iterations_target = escape(", ".join(parts))
    else:
        iterations_target = escape(str(meta.get("iterations", "—")))
    bts_host = escape(str(meta.get("bts_host", "—")))
    cpe_hosts = meta.get("cpe_hosts") or []
    cpe_label = escape(", ".join(str(h) for h in cpe_hosts) if cpe_hosts else "—")
    testbed_summary = meta.get("testbed_summary", {})
    testbed_table = _render_testbed_summary_table(testbed_summary)

    pytest_total = pytest_passed = pytest_failed = None
    if pytest_stats:
        pytest_total = pytest_stats.get("total")
        pytest_passed = pytest_stats.get("passed")
        pytest_failed = pytest_stats.get("failed")

    visible_iterations = [it for it in collector.iterations if it.phase.lower() != "baseline"]
    total_iters = len(visible_iterations)
    passed_iters = sum(1 for it in visible_iterations if it.passed)
    failed_iters = total_iters - passed_iters

    run_types_table = _render_run_types_overview(cases, visible_iterations)
    iteration_body = _render_iteration_sections(visible_iterations)
    if not iteration_body:
        iteration_body = (
            "<article class='iteration'><p>No iteration data captured. "
            "Run with <code>--allow-regression</code>.</p></article>"
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
    html {{ width: 100%; }}
    body {{
      margin: 0; padding: clamp(16px, 2.5vw, 36px) clamp(20px, 4vw, 56px);
      font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text);
      width: 100%; min-height: 100vh;
    }}
    .wrap {{ width: 100%; max-width: none; margin: 0; }}
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
    .panel-top {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px; margin-bottom: 16px;
    }}
    .panel-top h2 {{ margin: 0 0 14px; font-size: 17px; color: var(--title); }}
    .run-meta {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px 24px; margin-top: 14px; font-size: 13px; color: #475569;
    }}
    .run-meta span {{
      padding: 10px 14px; background: #f8fafc; border-radius: 8px;
      border: 1px solid var(--border);
    }}
    .run-meta strong {{ color: var(--title); }}
    table.data-table {{
      width: 100%; border-collapse: collapse; background: #fff;
      border: 2px solid var(--border); border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(15,23,42,0.06); table-layout: fixed;
    }}
    table.data-table th, table.data-table td {{
      border: 1px solid var(--border); padding: 12px 16px; text-align: center;
      vertical-align: middle; word-break: break-word;
    }}
    table.data-table thead th {{
      background: #f8fafc; color: var(--head); font-size: 14px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.5px;
    }}
    table.data-table th.corner {{ background: #f1f5f9; width: 14%; }}
    table.data-table th.row-label {{
      text-align: left; background: #f8fafc; color: var(--title);
      font-size: 14px; font-weight: 600; padding-left: 16px; width: 14%;
    }}
    table.data-table td {{ background: #fff; }}
    table.summary-top {{ margin-bottom: 0; }}
    table.run-types th, table.run-types td {{ text-align: left; }}
    table.run-types .num {{ text-align: center; }}
    .ip-cell {{ white-space: nowrap; font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; }}
    .summary {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 14px; margin-bottom: 18px; width: 100%;
    }}
    .chip {{
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 14px 18px; text-align: center; width: 100%;
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
    .subhead {{ margin: 18px 0 10px; font-size: 15px; color: var(--title); }}
    table.run-types {{ margin-bottom: 8px; font-size: 13px; }}
    table.run-types .num {{ width: 8%; }}
    .pass-cell {{ color: var(--pass); font-weight: 600; }}
    .fail-cell {{ color: var(--fail); font-weight: 600; }}
    .test-type-badge {{
      display: inline-block; font-size: 13px; font-weight: 700; padding: 4px 10px;
      border-radius: 6px; margin-right: 8px;
    }}
    .badge-reboot {{ background: #dbeafe; color: #1e40af; }}
    .badge-reset {{ background: #ffedd5; color: #9a3412; }}
    .badge-firmware {{ background: #f3e8ff; color: #6b21a8; }}
    .badge-other {{ background: #f1f5f9; color: #475569; }}
    .test-id-inline {{
      font-size: 12px; color: #64748b; font-family: Consolas, Monaco, monospace;
    }}
    .iterations-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 520px), 1fr));
      gap: 12px; width: 100%;
    }}
    .iterations-grid .test-section {{ grid-column: 1 / -1; }}
    .iterations-grid > .iteration {{ margin-bottom: 0; }}
    .test-section {{ margin: 20px 0 10px; padding-top: 4px; }}
    .test-section-title {{ margin: 0 0 6px; font-size: 17px; color: var(--title); }}
    .test-section-desc {{ margin: 0 0 12px; font-size: 13px; color: #64748b; }}
    .iteration {{
      border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
      margin-bottom: 10px; background: #fafcff;
    }}
    .iteration.ok {{ border-left: 5px solid var(--pass); }}
    .iteration.bad {{ border-left: 5px solid var(--fail); background: #fff8f8; }}
    .iteration-title {{
      display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap;
    }}
    .iteration-head {{ display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }}
    .phase {{
      font-size: 12px; font-weight: 600; color: #475569; background: #e2e8f0;
      padding: 4px 10px; border-radius: 6px;
    }}
    .status-pill {{
      margin-left: auto; font-size: 11px; font-weight: 700; letter-spacing: 0.4px;
      padding: 5px 12px; border-radius: 999px;
    }}
    .status-pill.pass {{ background: #dcfce7; color: #166534; }}
    .status-pill.fail {{ background: #fee2e2; color: #991b1b; }}
    .status-pill.na {{ background: #f1f5f9; color: #64748b; }}
    .pass-summary {{
      display: flex; align-items: center; gap: 10px; margin: 0 0 8px;
      font-size: 14px; color: #166534; font-weight: 500;
    }}
    .mark.large {{ width: 28px; height: 28px; font-size: 16px; }}
    details.check-details {{
      margin-top: 4px; border: 1px dashed var(--border); border-radius: 8px;
      padding: 0 14px 12px; background: #fff; width: 100%;
    }}
    details.check-details summary {{
      cursor: pointer; font-size: 13px; font-weight: 600; color: #475569;
      padding: 10px 0 6px; list-style-position: outside;
    }}
    details.check-details[open] summary {{ margin-bottom: 8px; }}
    details.check-details .check-matrix {{ margin-top: 4px; }}
    table.check-matrix {{ max-width: none; }}
    table.check-matrix td.cell {{ background: #fff; }}
    .fail-list {{ margin: 8px 0 0; padding-left: 20px; font-size: 13px; color: #991b1b; }}
    .mark {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 32px; height: 32px; font-size: 20px; font-weight: 700; border-radius: 8px;
    }}
    .mark.pass {{ color: var(--pass); background: #ecfdf5; }}
    .mark.fail {{ color: var(--fail); background: #fef2f2; }}
    .mark.na {{ color: #64748b; background: #f1f5f9; font-size: 16px; }}
    .footnote {{ margin-top: 14px; font-size: 12px; color: #64748b; text-align: center; }}
    @media (max-width: 720px) {{
      table.data-table {{ table-layout: auto; font-size: 12px; }}
      table.data-table th, table.data-table td {{ padding: 10px 8px; }}
      .hero {{ flex-direction: column; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div class="hero-main">
        <h1>UBR Stability Regression Report</h1>
        <p class="hero-date">{executed_at}</p>
      </div>
      <div class="hero-logo">
        <img src="{SENAO_LOGO_URL}" alt="Senao Networks"/>
      </div>
    </header>

    <section class="panel-top">
      <h2>Testbed Summary</h2>
      {testbed_table}
      {run_types_table}
      <h3 style="margin:18px 0 12px;font-size:15px;color:var(--title);">Run Summary</h3>
      <div class="run-meta">
        <span><strong>BTS:</strong> {bts_host}</span>
        <span><strong>CPE:</strong> {cpe_label}</span>
        <span><strong>Target cycles:</strong> {iterations_target}</span>
      </div>
    </section>

    <section class="summary">
      <div class="chip"><strong>{total_iters}</strong><span>Health iterations</span></div>
      <div class="chip pass"><strong>{passed_iters}</strong><span>Iterations passed</span></div>
      <div class="chip fail"><strong>{failed_iters}</strong><span>Iterations failed</span></div>
      {_pytest_stat_chips(pytest_total, pytest_passed, pytest_failed)}
    </section>

    <section class="panel">
      <h2>Iteration Results</h2>
      <p class="footnote" style="margin-top:0;margin-bottom:14px;">
        Each block shows the <strong>test type</strong> (Soft Reboot vs Network Soft Reset).
        Passing cycles show a summary only; expand <em>Show ping &amp; web details</em> for the matrix.
      </p>
      <div class="iterations-grid">{iteration_body}</div>
      <p class="footnote">
        &#10003; = passed &nbsp; &#10007; = failed.
        Ping: BTS column = BTS&#8594;CPE; CPE column = CPE&#8594;BTS.
      </p>
    </section>
  </div>
</body>
</html>"""
