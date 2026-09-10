"""Incremental pytest-json-report checkpoints and recovery for aborted runs."""

from __future__ import annotations

import argparse
import atexit
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

ARTIFACTS_DIR = Path("reports/artifacts")
DEFAULT_REPORT_PATH = ARTIFACTS_DIR / "report.json"
IP_PROGRESS_PATH = ARTIFACTS_DIR / "ip_suite_progress.json"

_ACTIVE_CONFIG: Any = None
_HANDLERS_INSTALLED = False


def _report_path(config: Any) -> Path:
    raw = config.getoption("json_report_file", default="") if config is not None else ""
    return Path(raw) if raw else DEFAULT_REPORT_PATH


def _build_json_report(config: Any, *, partial: bool, exitcode: int | None = None) -> dict[str, Any] | None:
    plugin = getattr(config, "_json_report", None)
    tests_map = getattr(plugin, "_json_tests", None) if plugin is not None else None
    if not tests_map:
        return None

    from pytest_jsonreport.serialize import make_report, make_summary

    now = time.time()
    started = getattr(plugin, "_start_time", None) or now
    code = exitcode if exitcode is not None else getattr(config, "_abort_exitcode", 1)
    collected = len(tests_map) + int(getattr(plugin, "_num_deselected", 0) or 0)
    summary = make_summary(tests_map, collected=collected)
    if partial:
        summary["partial"] = True

    report = make_report(
        created=now,
        duration=now - started,
        exitcode=code,
        root=str(config.rootpath),
        environment=getattr(config, "_metadata", {}),
        summary=summary,
    )
    report["tests"] = list(tests_map.values())
    if partial:
        report["partial"] = True
    collectors = getattr(plugin, "_json_collectors", None)
    if collectors:
        report["collectors"] = collectors
    warnings = getattr(plugin, "_json_warnings", None)
    if warnings:
        report["warnings"] = warnings
    return report


def flush_json_report(config: Any, *, partial: bool = True, exitcode: int | None = None) -> bool:
    """Write current pytest-json-report state to disk (safe after each test)."""
    report = _build_json_report(config, partial=partial, exitcode=exitcode)
    if report is None:
        return False
    path = _report_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return True


def _outcome_to_pytest(outcome: str) -> str:
    clean = str(outcome or "").strip().lower()
    if clean in ("passed", "failed", "skipped", "error"):
        return clean
    # Suite progress uses "Not implemented" for catalog N/A/manual.
    if "not implemented" in clean or clean in ("not available", "not_available", "n/a"):
        return "skipped"
    return "failed"


def _keywords_for_nodeid(nodeid: str, case_id: str) -> list[str]:
    nid = str(nodeid or "")
    cid = str(case_id or "").upper()
    if "VLAN" in cid or "/VLAN/" in nid or "test_vlan" in nid:
        return ["VLAN", cid or "VLAN"]
    if "IP" in cid or "/IP/" in nid or "test_ip" in nid:
        return ["IP", cid or "IP"]
    if cid:
        return [cid.split("_")[0], cid]
    return ["Ungrouped"]


def synthesize_from_ip_progress(
    progress_path: Path = IP_PROGRESS_PATH,
    output_path: Path = DEFAULT_REPORT_PATH,
) -> bool:
    """Build a minimal report.json from persisted IP/VLAN suite progress."""
    if not progress_path.is_file():
        return False
    try:
        with progress_path.open(encoding="utf-8") as handle:
            progress = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return False

    rows = progress.get("tests") or []
    if not rows:
        return False

    tests: list[dict[str, Any]] = []
    summary: dict[str, int] = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "total": 0}
    for row in rows:
        raw_outcome = str(row.get("outcome", "") or "")
        outcome = _outcome_to_pytest(raw_outcome)
        if outcome == "passed":
            summary["passed"] += 1
        elif outcome == "skipped":
            summary["skipped"] += 1
        elif outcome == "error":
            summary["error"] += 1
        else:
            summary["failed"] += 1
        summary["total"] += 1

        nodeid = str(row.get("nodeid", ""))
        case_id = str(row.get("case_id", "") or "")
        keywords = _keywords_for_nodeid(nodeid, case_id)
        duration = float(row.get("duration_s", 0.0) or 0.0)
        longrepr = str(row.get("longrepr") or "").strip()
        if not longrepr:
            if "not implemented" in raw_outcome.lower():
                longrepr = f"Skipped: Not implemented: {case_id}"
            elif outcome == "skipped":
                longrepr = f"Skipped: {case_id}"
            elif outcome != "passed":
                longrepr = f"{case_id}: {raw_outcome or 'failed'}"

        tests.append(
            {
                "nodeid": nodeid,
                "lineno": 0,
                "outcome": outcome,
                "keywords": keywords,
                "setup": {"duration": 0.0, "outcome": "passed"},
                "call": {
                    "duration": duration,
                    "outcome": outcome,
                    "longrepr": longrepr,
                },
            }
        )

    report = {
        "created": time.time(),
        "duration": float(progress.get("elapsed_s", 0.0) or 0.0),
        "exitcode": int(progress.get("exitcode", 1 if summary["failed"] else 0)),
        "root": str(_REPO_ROOT),
        "environment": {},
        "summary": {
            **summary,
            "collected": summary["total"],
            "partial": bool(progress.get("partial")),
            "recovered_from": "ip_suite_progress",
        },
        "tests": tests,
        "partial": bool(progress.get("partial")),
        "recovered_from": "ip_suite_progress",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return True


def recover_report_json(
    output_path: Path = DEFAULT_REPORT_PATH,
    progress_path: Path = IP_PROGRESS_PATH,
) -> bool:
    """Ensure report.json exists — synthesize from IP progress if needed."""
    if output_path.is_file():
        try:
            with output_path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("tests"):
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return synthesize_from_ip_progress(progress_path, output_path)


def _handle_abort(signum: int, _frame: Any) -> None:
    config = _ACTIVE_CONFIG
    if config is not None:
        config._abort_exitcode = 130 if signum == signal.SIGINT else 143
        try:
            flush_json_report(config, partial=True, exitcode=config._abort_exitcode)
        except Exception:
            pass
        try:
            from utils.ip_suite_progress import flush_ip_suite_progress

            flush_ip_suite_progress(config)
        except Exception:
            pass
    raise SystemExit(config._abort_exitcode if config is not None else 128 + signum)


def _atexit_flush() -> None:
    config = _ACTIVE_CONFIG
    if config is None:
        return
    try:
        flush_json_report(config, partial=True, exitcode=getattr(config, "_abort_exitcode", 1))
    except Exception:
        pass
    try:
        from utils.ip_suite_progress import flush_ip_suite_progress

        flush_ip_suite_progress(config)
    except Exception:
        pass


def register_abort_handlers(config: Any) -> None:
    """Register SIGTERM/SIGINT handlers to flush partial report before exit."""
    global _ACTIVE_CONFIG, _HANDLERS_INSTALLED
    _ACTIVE_CONFIG = config
    if _HANDLERS_INSTALLED:
        return
    if not config.getoption("json_report_file", default=""):
        return
    atexit.register(_atexit_flush)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_abort)
        except (OSError, ValueError):
            pass
    _HANDLERS_INSTALLED = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover or validate report.json after aborted pytest")
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Synthesize reports/artifacts/report.json from ip_suite_progress.json if missing",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_REPORT_PATH),
        help="Target report.json path",
    )
    parser.add_argument(
        "--progress",
        default=str(IP_PROGRESS_PATH),
        help="IP suite progress checkpoint path",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    progress = Path(args.progress)
    if args.recover:
        if recover_report_json(output, progress):
            print(f"Recovered partial report: {output}")
            return 0
        print(f"No recoverable progress at {progress} and no usable {output}")
        return 1

    if output.is_file():
        print(f"report.json present: {output}")
        return 0
    print(f"report.json missing: {output}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
