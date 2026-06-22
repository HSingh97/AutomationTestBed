"""PROCESS_01–PROCESS_20 process monitor validation catalog (local standalone DUT)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["Functional", "Validation", "Negative", "Stress"]


@dataclass(frozen=True)
class ProcessTestCase:
    case_id: str
    title: str
    category: Category
    # Original plan tagged BTS/CPE; all cases run on the local standalone device.
    legacy_role: str
    requires: tuple[str, ...] = ()


PROCESS_TEST_CASES: tuple[ProcessTestCase, ...] = (
    ProcessTestCase("PROCESS_01", "Normal Operation – Visibility", "Functional", "BTS"),
    ProcessTestCase("PROCESS_02", "Normal Operation – Uptime", "Functional", "BTS"),
    ProcessTestCase("PROCESS_03", "Normal Operation – Restart Count", "Functional", "BTS"),
    ProcessTestCase("PROCESS_04", "Normal Operation – Timestamp", "Functional", "BTS"),
    ProcessTestCase("PROCESS_05", "Crash Single Process", "Functional", "BTS", ("crash",)),
    ProcessTestCase("PROCESS_06", "Crash Multiple Processes", "Functional", "BTS", ("crash",)),
    ProcessTestCase("PROCESS_07", "Crash Critical Process", "Functional", "BTS", ("crash",)),
    ProcessTestCase("PROCESS_08", "Crash Non-Critical Process", "Functional", "BTS", ("crash",)),
    ProcessTestCase(
        "PROCESS_09",
        "Crash Process Monitor (watchdog)",
        "Functional",
        "BTS",
        ("destructive",),
    ),
    ProcessTestCase("PROCESS_10", "Restart Logging Accuracy", "Functional", "BTS", ("crash",)),
    ProcessTestCase("PROCESS_11", "Stress Test – High Load", "Stress", "BTS", ("crash", "stress")),
    ProcessTestCase("PROCESS_12", "Log Integrity", "Validation", "BTS", ("crash",)),
    ProcessTestCase("PROCESS_13", "Unauthorized Kill Attempt", "Functional", "BTS"),
    ProcessTestCase("PROCESS_14", "Dependency Handling", "Functional", "BTS", ("crash",)),
    ProcessTestCase(
        "PROCESS_15",
        "Monitor Recovery",
        "Functional",
        "BTS",
        ("destructive",),
    ),
    ProcessTestCase("PROCESS_16", "Kill Single Process", "Negative", "CPE", ("crash",)),
    ProcessTestCase("PROCESS_17", "Kill Multiple Processes Sequentially", "Functional", "CPE", ("crash",)),
    ProcessTestCase("PROCESS_18", "Kill Critical Process", "Negative", "CPE", ("crash",)),
    ProcessTestCase("PROCESS_19", "Kill Process Under Load", "Stress", "CPE", ("crash", "stress")),
    ProcessTestCase("PROCESS_20", "Unauthorized Kill Attempt", "Functional", "CPE"),
)

ACTIVE_PROCESS_CASE_IDS: frozenset[str] = frozenset(case.case_id for case in PROCESS_TEST_CASES)

DESTRUCTIVE_PROCESS_CASE_IDS: frozenset[str] = frozenset(
    case.case_id for case in PROCESS_TEST_CASES if "destructive" in case.requires
)


def case_by_id(case_id: str) -> ProcessTestCase:
    for case in PROCESS_TEST_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)
