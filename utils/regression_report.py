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

DEFAULT_REGRESSION_REPORT = Path("reports/artifacts/Regression_Report.html")
DEFAULT_STATE_FILE = Path("reports/artifacts/regression_collector_state.json")

SENAO_LOGO_URL = (
    "https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png"
)

# Human-readable labels so REG_01 / REG_02 are not repeated without context.
CASE_CATALOG: dict[str, dict[str, str]] = {
    "REG_01": {
        "title": "Soft Reboot",
        "description": "BTS reboot; ping within 200s; Device Init Success in /etc/device_logs",
        "badge_class": "badge-reboot",
    },
    "REG_02": {
        "title": "Network Soft Reset",
        "description": "Network reload; link terminate/re-establish from WiFi events log (max 90s)",
        "badge_class": "badge-reset",
    },
    "REG_03": {
        "title": "Firmware Upgrade",
        "description": "Firmware flash; reboot confirmed in logs; ping within 7 minutes",
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
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """pass | partial | fail — partial = connectivity OK, link log validation not OK."""
        checks_ok = bool(self.checks) and all(item.passed for item in self.checks)
        if not checks_ok:
            return "fail"
        val = self.validation
        if val:
            if val.get("partial"):
                return "partial"
            if not val.get("passed", True):
                return "fail"
        return "pass"

    @property
    def passed(self) -> bool:
        return self.status == "pass"


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

    def record_iteration(
        self,
        case_id: str,
        phase: str,
        checks: list[HealthCheckResult],
        *,
        validation: dict[str, Any] | None = None,
    ) -> IterationRecord:
        record = IterationRecord(
            case_id=case_id,
            phase=phase,
            checks=list(checks),
            validation=dict(validation or {}),
        )
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
                IterationRecord(
                    case_id=row["case_id"],
                    phase=row["phase"],
                    checks=checks,
                    validation=dict(row.get("validation") or {}),
                )
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
                "validation": dict(record.validation or {}),
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
            validation=dict(row.get("validation") or {}),
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
    if not append and path.is_file():
        path.unlink()
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


def _phase_number(phase: str) -> int | None:
    match = re.search(r"(?:cycle|iteration)-(\d+)", phase, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _format_phase_label(phase: str) -> str:
    number = _phase_number(phase)
    if number is not None:
        return f"Iteration {number}"
    return phase.replace("-", " ").title()


def _iteration_sort_key(record: IterationRecord) -> tuple[str, int, str]:
    number = _phase_number(record.phase)
    iter_num = number if number is not None else 9999
    return (record.case_id, iter_num, record.phase)


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


def _is_populated_summary_value(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in ("—", "-", "N/A", "n/a", "None", "unknown")


def _render_testbed_summary_table(summary: dict[str, Any]) -> str:
    """BTS + one column per SU when ``cpes`` is populated; legacy BTS/CPE otherwise."""
    if not summary:
        summary = {}
    cpes = summary.get("cpes")
    if isinstance(cpes, list) and cpes:
        return _render_multi_unit_testbed_table(summary.get("bts", {}), cpes, summary)

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
    )
    if _is_populated_summary_value(bts.get("qos")) or _is_populated_summary_value(cpe.get("qos")):
        rows = (*rows, ("QOS", "qos"))
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


def _render_testbed_context_chips(summary: dict[str, Any]) -> str:
    chips: list[str] = []
    for key, label in (
        ("stand", "Stand"),
        ("profile", "Profile"),
        ("su_count", "Connected SUs"),
    ):
        value = summary.get(key)
        if value is None or str(value).strip() in {"", "—", "0"}:
            continue
        chips.append(
            f"<span class='testbed-chip'><strong>{escape(label)}:</strong> "
            f"{escape(str(value))}</span>"
        )
    if not chips:
        return ""
    return f'<div class="testbed-chips">{"".join(chips)}</div>'


def _device_header_cell(unit: dict[str, Any], *, default_label: str) -> str:
    label = str(unit.get("label") or default_label)
    ip = str(unit.get("ip") or "").strip()
    ip_line = (
        f"<span class='device-ip'>{escape(ip)}</span>"
        if ip and ip not in {"—", "-"}
        else ""
    )
    return (
        f"<span class='device-name'>{escape(label)}</span>"
        f"{ip_line}"
    )


def _summary_cell(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key, "—") or "—")
    if key == "ip":
        return f"<span class='ip-cell'>{escape(value)}</span>"
    return escape(value)


def _render_multi_unit_testbed_table(
    bts: dict[str, Any],
    cpes: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    units: list[tuple[str, dict[str, Any]]] = [("BTS", bts or {})]
    for unit in cpes:
        label = str(unit.get("label") or f"SU{unit.get('su_index', len(units))}")
        units.append((label, unit))

    rows = (
        ("Model", "model"),
        ("FW Version", "fw_version"),
        ("IP", "ip"),
        ("Vlan", "vlan"),
    )
    if any(_is_populated_summary_value(unit.get("qos")) for _, unit in units):
        rows = (*rows, ("QOS", "qos"))

    header_cells = "".join(
        f"<th class='device-head'>{_device_header_cell(unit, default_label=label)}</th>"
        for label, unit in units
    )
    body_rows = []
    for row_label, key in rows:
        cells = "".join(
            f"<td>{_summary_cell(unit, key)}</td>" for _, unit in units
        )
        body_rows.append(
            f"""
            <tr>
              <th class="row-label">{row_label}</th>
              {cells}
            </tr>
            """
        )

    chips = _render_testbed_context_chips(summary)
    return f"""
    {chips}
    <div class="testbed-scroll">
      <table class="data-table summary-top summary-multi">
        <thead>
          <tr>
            <th class="corner"></th>
            {header_cells}
          </tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """


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


def _render_case_summary_cards(case_id: str, subset: list[IterationRecord]) -> str:
    """GUI-style total / passed / partial / failed for one regression test type."""
    total = len(subset)
    passed_n = sum(1 for r in subset if r.status == "pass")
    partial_n = sum(1 for r in subset if r.status == "partial")
    failed_n = sum(1 for r in subset if r.status == "fail")
    partial_card = ""
    if partial_n:
        partial_card = f"""
      <div class="card stat-partial">
        <h3>{partial_n}</h3><p style="color: var(--partial);">Partial</p>
      </div>
      """
    return f"""
    <div class="summary-cards{' has-partial' if partial_n else ''}">
      <div class="card stat-total">
        <h3>{total}</h3><p>Total Executed</p>
      </div>
      <div class="card stat-pass">
        <h3>{passed_n}</h3><p style="color: var(--pass);">Passed</p>
      </div>
      {partial_card}
      <div class="card stat-fail">
        <h3>{failed_n}</h3><p style="color: var(--fail);">Failed</p>
      </div>
    </div>
    """


def _validation_row(label: str, value: str, *, ok: bool | None = None, warn: bool = False) -> str:
    if warn:
        mark = "<span class='val warn'>&#9888;</span>"
    elif ok is True:
        mark = "<span class='val pass'>&#10003;</span>"
    elif ok is False:
        mark = "<span class='val fail'>&#10007;</span>"
    else:
        mark = "<span class='val na'>&mdash;</span>"
    return f"""
    <tr>
      <th>{escape(label)}</th>
      <td>{mark} {escape(value)}</td>
    </tr>
    """


def _render_validation_panel(record: IterationRecord) -> str:
    val = record.validation or {}
    if not val or not (
        val.get("summary")
        or val.get("events")
        or val.get("log_excerpt")
        or val.get("device_time_before")
        or val.get("uptime_before_s") is not None
    ):
        return (
            "<div class='validation-panel validation-missing'>"
            "<p><em>Log validation was not stored for this iteration "
            "(re-run regression after updating the testbed collector).</em></p></div>"
        )

    rows = []
    is_soft_reset_log = bool(val.get("wifi_events_log_path"))
    is_soft_reboot_log = bool(val.get("device_logs_validation"))
    is_simplified_log = is_soft_reset_log or is_soft_reboot_log
    if val.get("summary"):
        rows.append(
            f"<tr><th>Summary</th><td>{escape(str(val['summary']))}</td></tr>"
        )
    if (val.get("device_time_before") or val.get("device_time_after")) and not is_simplified_log:
        rows.append(
            _validation_row(
                "Device time",
                f"before {val.get('device_time_before', '—')} → after {val.get('device_time_after', '—')}",
            )
        )
    if (val.get("uptime_before_s") is not None or val.get("uptime_after_s") is not None) and not is_soft_reboot_log:
        rows.append(
            _validation_row(
                "System uptime",
                f"{val.get('uptime_before_s', '—')}s → {val.get('uptime_after_s', '—')}s",
                ok=val.get("reboot_confirmed") if val.get("reboot_confirmed") is not None else None,
            )
        )
    if val.get("reboot_confirmed") is not None and is_soft_reboot_log:
        confirmed = bool(val.get("reboot_confirmed"))
        rows.append(
            _validation_row(
                "Device init confirmed",
                "Yes" if confirmed else "No",
                ok=confirmed if confirmed or not val.get("partial") else None,
                warn=bool(val.get("partial")) and not confirmed,
            )
        )
    elif val.get("reboot_confirmed") is not None and not is_soft_reset_log:
        rows.append(
            _validation_row(
                "Reboot confirmed (device logs + uptime)",
                "Yes" if val.get("reboot_confirmed") else "No",
                ok=bool(val.get("reboot_confirmed")),
            )
        )
    if val.get("uptime_stable") is not None:
        rows.append(
            _validation_row(
                "Uptime stable (no reboot)",
                "Yes" if val.get("uptime_stable") else "No",
                ok=bool(val.get("uptime_stable")),
            )
        )
    if val.get("ping_recovery_seconds") is not None:
        limit = val.get("ping_recovery_limit_s")
        limit_txt = f" (max {limit}s)" if limit else ""
        ping_secs = float(val["ping_recovery_seconds"])
        ping_ok = limit is None or ping_secs <= float(limit)
        ping_slow = bool(val.get("partial")) and not ping_ok and ping_secs > 0
        rows.append(
            _validation_row(
                "Ping recovery",
                f"{val['ping_recovery_seconds']}s{limit_txt}",
                ok=True if ping_ok else None,
                warn=ping_slow,
            )
        )
    if val.get("link_dropped") is not None:
        dropped = bool(val.get("link_dropped"))
        rows.append(
            _validation_row(
                "Link terminated",
                "Yes" if dropped else "No",
                ok=dropped if dropped or not val.get("partial") else None,
                warn=bool(val.get("partial")) and not dropped,
            )
        )
    if val.get("link_reestablished") is not None:
        restored = bool(val.get("link_reestablished"))
        rows.append(
            _validation_row(
                "Link re-established",
                "Yes" if restored else "No",
                ok=restored if restored or not val.get("partial") else None,
                warn=bool(val.get("partial")) and not restored,
            )
        )
    if val.get("link_restore_seconds") is not None:
        max_s = val.get("link_uptime_max_s")
        restore_ok = max_s is None or float(val["link_restore_seconds"]) <= float(max_s)
        rows.append(
            _validation_row(
                "Link restore time",
                f"{val['link_restore_seconds']}s"
                + (f" (max {max_s}s)" if max_s else ""),
                ok=restore_ok if restore_ok else None,
                warn=bool(val.get("partial")) and not restore_ok,
            )
        )
    if val.get("device_logs_ok") is not None and not is_simplified_log:
        rows.append(
            _validation_row(
                "Device logs (/etc/device_logs)",
                "Wireless/network activity found" if val.get("device_logs_ok") else "Not found in tail/grep",
                ok=bool(val.get("device_logs_ok")),
            )
        )
    if val.get("wireless_logs_ok") is not None and not is_simplified_log:
        rows.append(
            _validation_row(
                "Wireless logread",
                "Wireless/network activity found" if val.get("wireless_logs_ok") else "Not found",
                ok=bool(val.get("wireless_logs_ok")),
            )
        )

    events = val.get("events") or []
    timeline = ""
    if events and not is_simplified_log:
        items = "".join(
            f"<li><span class='evt-time'>{escape(e.get('time', ''))}</span> "
            f"<strong>{escape(e.get('step', ''))}</strong> — {escape(e.get('detail', ''))}</li>"
            for e in events
        )
        timeline = f"<ul class='evt-list'>{items}</ul>"

    log_excerpt = str(val.get("log_excerpt") or "").strip()
    if is_soft_reset_log:
        log_label = "WiFi link events"
    elif is_soft_reboot_log:
        log_label = "Device logs"
    else:
        log_label = "Device log excerpt"
    log_block = ""
    if log_excerpt:
        log_block = f"""
        <details class="log-details"{' open' if record.status != 'pass' else ''}>
          <summary>{escape(log_label)}</summary>
          <pre class="log-pre">{escape(log_excerpt)}</pre>
        </details>
        """

    if val.get("partial"):
        val_pill = "partial"
        val_label = "PARTIAL"
    elif bool(val.get("passed", True)):
        val_pill = "pass"
        val_label = "PASS"
    else:
        val_pill = "fail"
        val_label = "FAIL"
    return f"""
    <div class="validation-panel">
      <h4>Log &amp; event validation <span class="mini-pill {val_pill}">
        {val_label}</span></h4>
      <table class="validation-table">{''.join(rows)}</table>
      {timeline}
      {log_block}
    </div>
    """


def _render_iteration_article(record: IterationRecord) -> str:
    matrix = _iteration_matrix(record)
    status = record.status
    status_class = {"pass": "ok", "partial": "warn", "fail": "bad"}.get(status, "bad")
    status_label = status.upper()
    failures_html = _render_failure_notes(record)
    validation_html = _render_validation_panel(record)
    connectivity_open = "" if status == "pass" else " open"

    return f"""
    <article class="iteration {status_class}">
      <div class="iteration-title">
        <span class="phase">{escape(_format_phase_label(record.phase))}</span>
        <span class="status-pill {status}">{status_label}</span>
      </div>
      {validation_html}
      <details class="check-details"{connectivity_open}>
        <summary>Connectivity (ping &amp; web)</summary>
        {_render_check_table(matrix)}
        {failures_html}
      </details>
    </article>
    """


def _render_iteration_sections(visible_iterations: list[IterationRecord]) -> str:
    sorted_rows = sorted(visible_iterations, key=_iteration_sort_key)
    blocks: list[str] = []
    last_case: str | None = None
    case_subset: list[IterationRecord] = []

    def flush_case(case_id: str, rows: list[IterationRecord]) -> None:
        if not case_id or not rows:
            return
        info = _case_info(case_id)
        cycle_blocks = "".join(_render_iteration_article(r) for r in rows)
        blocks.append(
            f"""
            <section class="regression-type-block">
              <div class="type-header">
                <h3 class="test-section-title">
                  <span class="test-type-badge {info['badge_class']}">{escape(info['title'])}</span>
                  <span class="test-id-inline">{escape(case_id)}</span>
                </h3>
              </div>
              {_render_case_summary_cards(case_id, rows)}
              <div class="iterations-list">{cycle_blocks}</div>
            </section>
            """
        )

    for record in sorted_rows:
        if record.case_id != last_case:
            flush_case(last_case or "", case_subset)
            case_subset = []
            last_case = record.case_id
        case_subset.append(record)
    flush_case(last_case or "", case_subset)
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


def _build_html(collector: RegressionReportCollector, *, pytest_stats: dict[str, int] | None = None) -> str:
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

    run_case_ids = {
        str(c.get("case_id"))
        for c in (meta.get("cases") or [])
        if c.get("case_id")
    }
    visible_iterations = [
        it
        for it in collector.iterations
        if it.phase.lower() != "baseline" and (not run_case_ids or it.case_id in run_case_ids)
    ]
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
      --border: #cbd5e1; --pass: #16a34a; --fail: #dc2626; --partial: #d97706;
      --head: #1e3a8a;
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
    .panel {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px;
    }}
    .panel > h2 {{ margin: 0 0 16px; font-size: 17px; color: var(--title); }}
    .regression-type-block {{
      margin-bottom: 28px; border: 1px solid var(--border); border-radius: 14px;
      overflow: hidden; background: var(--card);
    }}
    .type-header {{
      padding: 16px 20px 0; background: #fff;
    }}
    .summary-cards {{
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
      padding: 16px 20px 20px; background: #f8fafc; border-bottom: 1px solid var(--border);
    }}
    .summary-cards.has-partial {{
      grid-template-columns: repeat(4, 1fr);
    }}
    .summary-cards .card {{
      padding: 18px 16px; border-radius: 10px; text-align: center;
      border: 1px solid var(--border); background: #fff;
      box-shadow: 0 1px 3px rgba(15,23,42,0.04);
    }}
    .summary-cards .card h3 {{
      margin: 0; font-size: 32px; font-weight: 700; color: var(--title);
    }}
    .summary-cards .card p {{
      margin: 8px 0 0; font-size: 12px; color: #64748b;
      text-transform: uppercase; font-weight: 600; letter-spacing: 0.4px;
    }}
    .summary-cards .stat-pass h3 {{ color: var(--pass); }}
    .summary-cards .stat-partial h3 {{ color: var(--partial); }}
    .summary-cards .stat-fail h3 {{ color: var(--fail); }}
    .iterations-list {{
      padding: 12px 16px 20px;
      display: flex; flex-direction: column; gap: 14px;
    }}
    .validation-panel {{
      margin: 10px 0 12px; padding: 14px 16px; background: #f8fafc;
      border: 1px solid var(--border); border-radius: 10px;
    }}
    .validation-panel h4 {{
      margin: 0 0 10px; font-size: 14px; color: var(--title);
      display: flex; align-items: center; gap: 10px;
    }}
    .mini-pill {{
      font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 999px;
    }}
    .mini-pill.pass {{ background: #dcfce7; color: #166534; }}
    .mini-pill.partial {{ background: #ffedd5; color: #9a3412; }}
    .mini-pill.fail {{ background: #fee2e2; color: #991b1b; }}
    table.validation-table {{
      width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 13px;
    }}
    table.validation-table th {{
      text-align: left; width: 34%; padding: 8px 10px; color: #475569;
      font-weight: 600; vertical-align: top; border-bottom: 1px solid #e2e8f0;
    }}
    table.validation-table td {{
      padding: 8px 10px; border-bottom: 1px solid #e2e8f0; color: var(--title);
    }}
    .val.pass {{ color: var(--pass); font-weight: 700; }}
    .val.warn {{ color: var(--partial); font-weight: 700; }}
    .val.fail {{ color: var(--fail); font-weight: 700; }}
    .val.na {{ color: #94a3b8; }}
    .evt-list {{
      margin: 8px 0 0; padding-left: 18px; font-size: 12px; color: #475569;
    }}
    .evt-list .evt-time {{
      font-family: Consolas, Monaco, monospace; color: #64748b; margin-right: 6px;
    }}
    details.log-details {{
      margin-top: 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: #0f172a;
    }}
    details.log-details summary {{
      cursor: pointer; padding: 10px 12px; font-size: 12px; font-weight: 600;
      color: #e2e8f0; background: #1e293b; border-radius: 8px 8px 0 0;
    }}
    pre.log-pre {{
      margin: 0; padding: 14px; font-size: 11px; line-height: 1.45;
      color: #e2e8f0; white-space: pre-wrap; word-break: break-word;
      max-height: 320px; overflow: auto;
    }}
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
    .test-section-title {{ margin: 0; font-size: 17px; color: var(--title); }}
    .iteration {{
      border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
      background: #fafcff;
    }}
    .iteration.compact {{ padding: 10px 14px; }}
    .iteration.ok {{ border-left: 5px solid var(--pass); }}
    .iteration.warn {{
      border-left: 5px solid var(--partial); background: #fffbeb;
    }}
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
    .status-pill.partial {{ background: #ffedd5; color: #9a3412; }}
    .status-pill.fail {{ background: #fee2e2; color: #991b1b; }}
    .status-pill.na {{ background: #f1f5f9; color: #64748b; }}
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
      .summary-cards {{ grid-template-columns: 1fr; }}
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
      <h3 style="margin:18px 0 12px;font-size:15px;color:var(--title);">Run Summary</h3>
      <div class="run-meta">
        <span><strong>BTS:</strong> {bts_host}</span>
        <span><strong>CPE:</strong> {cpe_label}</span>
        <span><strong>Target iterations:</strong> {iterations_target}</span>
      </div>
    </section>

    <section class="panel">
      <h2>Regression Results</h2>
      {iteration_body}
      <p class="footnote">
        Report lists only test types executed in this run.         Soft reboot: ping target 200s (extra grace before hard fail); slow ping or missing init log is
        <strong>partial</strong> (orange). Hard <strong>fail</strong> only when ping/web connectivity fails.
        Soft reset: link terminate/re-establish (max 90s).
      </p>
    </section>
  </div>
</body>
</html>"""
