"""Persist structured Functional case lines for Senao HTML reports."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

FUNCTIONAL_CASE_LOG_PATH = Path("reports/artifacts/functional_case_logs.json")
_FT_MARKER_RE = re.compile(r"^\[FT\]\[(FT_\d+)\]\s+(.+)$", re.I)
_FT_STANDARD_RE = re.compile(r"^\[FUNCTIONAL\]\s+(FT_\d+):\s*(.+)$", re.I)
_FT_DONE_RE = re.compile(r"^\[FUNCTIONAL\]\s+(FT_\d+)\s+done\s+[—-]\s*(.+)$", re.I)
_FT_COMPLETE_RE = re.compile(r"^\[FUNCTIONAL\]\s+(FT_\d+)\s+complete\s+[—-]\s*(.+)$", re.I)
_FT_BRACKET_RE = re.compile(r"^\[FUNCTIONAL\]\s+(FT_\d+)\s+\[[^\]]+\]\s*(.+)$", re.I)

_REPORT_NOISE = (
    "SSH session reopened",
    "SSH interrupted during",
    "waiting BTS↔CPE link up",
    "PC LAN untagged cleanup OK",
    "clearing PC LAN VLANs before verification",
    "opening BTS=",
    ": SSH ready",
    "SSH ready",
    "BTS VLAN mode transparent OK",
    "RF Link Down .. Waiting for link up",
    "Navigation retry failed",
    "Executing Triple-Apply sequence",
    "[DEBUG]",
    "-> [DEBUG]",
    "-> WARNING: Did not automatically return",
    "CPE hop SSH retry",
    "post-reset settle",
    "VLAN apply SSH dropped",
)

_LOCK = threading.Lock()
_MAX_LINES_PER_CASE = 24


def _normalize_case_id(case_id: str) -> str:
    return str(case_id or "").strip().upper()


def _clean_detail(message: str) -> str:
    detail = str(message or "").strip()
    if not detail:
        return ""
    if any(noise in detail for noise in _REPORT_NOISE):
        return ""
    return detail


def _read_payload() -> dict:
    if not FUNCTIONAL_CASE_LOG_PATH.is_file():
        return {"cases": {}}
    try:
        with FUNCTIONAL_CASE_LOG_PATH.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {"cases": {}}
    if not isinstance(payload, dict):
        return {"cases": {}}
    cases = payload.get("cases")
    if not isinstance(cases, dict):
        payload["cases"] = {}
    return payload


def append(case_id: str, message: str) -> None:
    """Append one report line for a Functional case."""
    case_id = _normalize_case_id(case_id)
    detail = _clean_detail(message)
    if not case_id or not detail:
        return
    with _LOCK:
        payload = _read_payload()
        cases = payload.setdefault("cases", {})
        lines = list(cases.get(case_id) or [])
        if lines and lines[-1] == detail:
            return
        lines.append(detail)
        cases[case_id] = lines[-_MAX_LINES_PER_CASE:]
        FUNCTIONAL_CASE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FUNCTIONAL_CASE_LOG_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)


def load() -> dict[str, list[str]]:
    payload = _read_payload()
    cases = payload.get("cases") or {}
    out: dict[str, list[str]] = {}
    for case_id, lines in cases.items():
        if not isinstance(lines, list):
            continue
        cleaned = [_clean_detail(str(line)) for line in lines]
        cleaned = [line for line in cleaned if line]
        if cleaned:
            out[_normalize_case_id(case_id)] = cleaned
    return out


def backfill_from_text(text: str) -> dict[str, list[str]]:
    """Parse [FUNCTIONAL]/[FT] lines from a saved console log."""
    cases: dict[str, list[str]] = {}
    parsers = (
        _FT_MARKER_RE,
        _FT_STANDARD_RE,
        _FT_DONE_RE,
        _FT_COMPLETE_RE,
        _FT_BRACKET_RE,
    )
    for raw in str(text or "").splitlines():
        line = raw.strip()
        match = None
        for parser in parsers:
            match = parser.match(line)
            if match:
                break
        if not match:
            continue
        case_id = _normalize_case_id(match.group(1))
        detail = _clean_detail(match.group(2))
        if parser is _FT_DONE_RE and detail:
            detail = f"done — {detail}"
        if parser is _FT_COMPLETE_RE and detail:
            detail = f"complete — {detail}"
        if not detail:
            continue
        bucket = cases.setdefault(case_id, [])
        if bucket and bucket[-1] == detail:
            continue
        bucket.append(detail)
        cases[case_id] = bucket[-_MAX_LINES_PER_CASE:]
    return cases


def merge_backfill(cases: dict[str, list[str]]) -> None:
    if not cases:
        return
    with _LOCK:
        payload = _read_payload()
        stored = payload.setdefault("cases", {})
        for case_id, lines in cases.items():
            existing = list(stored.get(case_id) or [])
            for line in lines:
                if line not in existing:
                    existing.append(line)
            stored[case_id] = existing[-_MAX_LINES_PER_CASE:]
        FUNCTIONAL_CASE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FUNCTIONAL_CASE_LOG_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
