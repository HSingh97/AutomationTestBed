"""Console progress summary for pytest suites (Jenkins-friendly)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from prettytable import PrettyTable

IP_SUITE_PROGRESS_PATH = Path("reports/artifacts/ip_suite_progress.json")

# Any suite case id: IP_01, JMB_04, REG_12, ...
_CASE_ID_RE = re.compile(r"([A-Z]{2,}_\d+)", re.I)
_TARGET_RE = re.compile(r"_(bts|cpe)(?:\[|$)", re.I)


@dataclass
class _IpSuiteRow:
    label: str
    case_id: str
    target: str
    outcome: str = "PENDING"
    duration_s: float = 0.0


class IpSuiteProgress:
    def __init__(self, nodeids: list[str]) -> None:
        self._nodeids = list(nodeids)
        self._rows: list[_IpSuiteRow] = []
        self._index: dict[str, int] = {}
        self._started = time.monotonic()
        self._completed = 0
        for nodeid in nodeids:
            case_id, target, label = _parse_nodeid(nodeid)
            key = nodeid
            self._index[key] = len(self._rows)
            self._rows.append(
                _IpSuiteRow(label=label, case_id=case_id, target=target)
            )
        self.total = len(self._rows)
        if self.total:
            print(f"\n[suite] {self.total} test(s) queued — progress table after each case\n")
            self._persist()

    def record(self, nodeid: str, *, outcome: str, duration_s: float) -> None:
        idx = self._index.get(nodeid)
        if idx is None:
            return
        row = self._rows[idx]
        if row.outcome != "PENDING":
            return
        row.outcome = outcome
        row.duration_s = duration_s
        self._completed += 1
        self._persist()
        self._print_table()

    def _persist(self) -> None:
        payload = {
            "partial": self._completed < self.total,
            "completed": self._completed,
            "total": self.total,
            "elapsed_s": time.monotonic() - self._started,
            "tests": [
                {
                    "nodeid": self._nodeids[idx],
                    "case_id": row.case_id,
                    "target": row.target,
                    "outcome": row.outcome,
                    "duration_s": row.duration_s,
                }
                for idx, row in enumerate(self._rows)
                if row.outcome != "PENDING"
            ],
        }
        IP_SUITE_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with IP_SUITE_PROGRESS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def _print_table(self) -> None:
        done = self._completed
        total = max(1, self.total)
        pct = int(100 * done / total)
        elapsed = time.monotonic() - self._started
        avg = elapsed / done if done else 0.0
        remaining = max(0, total - done)
        eta_s = int(avg * remaining) if done else 0
        eta_txt = _format_duration(eta_s)

        passed = sum(1 for r in self._rows if r.outcome == "PASSED")
        failed = sum(1 for r in self._rows if r.outcome == "FAILED")
        skipped = sum(1 for r in self._rows if r.outcome == "SKIPPED")
        errors = sum(1 for r in self._rows if r.outcome == "ERROR")

        table = PrettyTable()
        table.field_names = ["Case", "Device", "Result", "Time"]
        table.align["Case"] = "l"
        table.align["Device"] = "l"
        table.align["Result"] = "l"
        table.align["Time"] = "r"
        for row in self._rows:
            if row.outcome == "PENDING":
                continue
            table.add_row(
                [
                    row.case_id,
                    row.target.upper(),
                    row.outcome,
                    f"{row.duration_s:.0f}s" if row.duration_s else "—",
                ]
            )

        print(
            f"\n[suite] Progress {done}/{total} ({pct}%) | "
            f"PASS {passed} FAIL {failed} SKIP {skipped} ERR {errors} | "
            f"elapsed {_format_duration(int(elapsed))} | ETA ~{eta_txt}\n"
        )
        print(table)
        print(flush=True)


def _parse_nodeid(nodeid: str) -> tuple[str, str, str]:
    base = nodeid.split("::")[-1]
    case_m = _CASE_ID_RE.search(base)
    case_id = case_m.group(1).upper() if case_m else base.removeprefix("test_")[:24]
    target_m = _TARGET_RE.search(base)
    target = (target_m.group(1) if target_m else "bts").lower()
    if "extended" in base and "[" in nodeid:
        param = nodeid.split("[", 1)[-1].rstrip("]")
        parts = param.split("-", 1)
        if len(parts) == 2:
            case_id, target = parts[0].upper(), parts[1].lower()
    label = f"{case_id}-{target.upper()}"
    return case_id, target, label


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def flush_ip_suite_progress(config) -> None:
    """Persist latest IP suite table (e.g. on abort)."""
    progress = getattr(config, "_ip_suite_progress", None)
    if progress is not None:
        progress._persist()


def outcome_from_report(report) -> str:
    if report.skipped:
        return "SKIPPED"
    if report.failed:
        return "FAILED" if report.when == "call" else "ERROR"
    if report.passed:
        return "PASSED"
    return "—"
