"""PROCESS test case catalog — official PROCESS_01–19 + extended in-depth cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["Functional", "Validation", "Negative", "Stress", "Extended"]


@dataclass(frozen=True)
class ProcessTestCase:
    case_id: str
    title: str
    action: str
    pass_criteria: str
    category: Category
    legacy_role: str
    sheet_ref: str = ""
    requires: tuple[str, ...] = ()
    official: bool = True


OFFICIAL_PROCESS_TEST_CASES: tuple[ProcessTestCase, ...] = (
    ProcessTestCase(
        "PROCESS_01",
        "Normal Operation – Visibility",
        "Check process monitor details for BTS",
        "All processes visible; uptime correct; restart count = 0; last restart timestamp = 0",
        "Functional",
        "BTS",
        "BTS-AT-SW-26",
    ),
    ProcessTestCase(
        "PROCESS_02",
        "Normal Operation – Uptime",
        "Verify uptime values after continuous run",
        "Uptime matches actual runtime; no discrepancies",
        "Functional",
        "BTS",
        "BTS-AT-SW-26",
    ),
    ProcessTestCase(
        "PROCESS_03",
        "Normal Operation – Restart Count",
        "Observe restart count without crashes",
        "Restart count remains 0",
        "Functional",
        "BTS",
        "BTS-AT-SW-26",
    ),
    ProcessTestCase(
        "PROCESS_04",
        "Normal Operation – Timestamp",
        "Verify last restart timestamp",
        "Timestamp remains 0 until crash occurs",
        "Functional",
        "BTS",
        "BTS-AT-SW-26",
    ),
    ProcessTestCase(
        "PROCESS_05",
        "Crash Single Process",
        "Induce crash in one BTS process (all monitored services)",
        "Device does not reboot; crashed process restarts; restart count increments; timestamp logged",
        "Functional",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_06",
        "Crash Multiple Processes",
        "Induce crashes in multiple processes sequentially (all monitored services)",
        "Each process restarts independently; restart counts increment correctly",
        "Functional",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_07",
        "Crash Critical Process",
        "Crash a critical BTS process (all monitored services)",
        "Device remains stable; process restarts; restart count increments",
        "Functional",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_08",
        "Crash Non-Critical Process",
        "Crash a background process (all monitored services)",
        "Process restarts; no service disruption; restart count increments",
        "Functional",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_09",
        "Crash Process Monitor",
        "Stop process monitoring (ubus call system watchdog '{\"stop\":true}'); check ping/uptime",
        "Device reboots automatically",
        "Functional",
        "BTS",
        "BTS-AT-SW-28",
        ("destructive",),
    ),
    ProcessTestCase(
        "PROCESS_10",
        "Restart Logging Accuracy",
        "Crash process and check logs (all monitored services)",
        "Restart event logged with correct timestamp and incremented count",
        "Functional",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_11",
        "Stress Test – High Load",
        "Run BTS under heavy CPU/memory load; crash under load (all monitored services)",
        "Process monitor continues to report uptime and restart counts correctly",
        "Stress",
        "BTS",
        "BTS-AT-SW-27",
        ("crash", "stress"),
    ),
    ProcessTestCase(
        "PROCESS_12",
        "Log Integrity",
        "Review logs after multiple restarts (all monitored services)",
        "Logs show accurate restart counts and timestamps",
        "Validation",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_13",
        "Unauthorized Kill Attempt",
        "Attempt to kill protected process without privileges (root SSH + local unpriv user)",
        "Action denied; unauthorized attempt logged; PID unchanged",
        "Functional",
        "BTS",
        "BTS-AT-SW-26",
    ),
    ProcessTestCase(
        "PROCESS_14",
        "Dependency Handling",
        "Crash parent process with child dependencies (network → dnsmasq)",
        "Parent restarts; child processes relaunched; restart logged",
        "Functional",
        "BTS",
        "BTS-AT-SW-27",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_15",
        "Monitor Recovery",
        "Restart process monitor after crash (kill init/procd)",
        "Device reboots; monitor resumes tracking processes",
        "Functional",
        "BTS",
        "BTS-AT-SW-28",
        ("destructive",),
    ),
    ProcessTestCase(
        "PROCESS_16",
        "Kill Single Process",
        "Kill one active process on CPE (all monitored services)",
        "Process restarts immediately with new PID; restart logged with timestamp",
        "Negative",
        "CPE",
        "CPE-TC-SW-41",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_17",
        "Kill Multiple Processes Sequentially",
        "Kill processes sequentially (all monitored services; 2× per service)",
        "Each process restarts independently; logs show separate restart events with correct timestamps",
        "Functional",
        "CPE",
        "CPE-TC-SW-41",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_18",
        "Kill Critical Process",
        "Kill a critical system process monitored by CPE (all monitored services)",
        "Process restarts automatically; device remains stable; restart logged",
        "Negative",
        "CPE",
        "CPE-TC-SW-41",
        ("crash",),
    ),
    ProcessTestCase(
        "PROCESS_19",
        "Kill Process Under Load",
        "Run high CPU load, then kill process (all monitored services)",
        "Process restarts promptly despite load; restart logged correctly",
        "Stress",
        "CPE",
        "CPE-TC-SW-41",
        ("crash", "stress"),
    ),
)

# Extended in-depth cases (automation / lab sheet — not in original SW-26/27/28/41 matrix).
EXTENDED_PROCESS_TEST_CASES: tuple[ProcessTestCase, ...] = (
    ProcessTestCase(
        "PROCESS_21",
        "GUI vs ubus Counter Parity",
        "After crash tests, compare Process Monitoring GUI table to ubus for every visible service",
        "GUI respawn/crash counters match ubus; no missing services in GUI",
        "Extended",
        "BTS",
        "EXT-SW-PROC-01",
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_22",
        "Core Dump Capture Audit",
        "After SEGV cases, list /overlay/data/procmon/cores and map to crashed services/PIDs",
        "Core file per SEGV target or documented PARTIAL with service list",
        "Extended",
        "BTS",
        "EXT-SW-PROC-02",
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_23",
        "Post-Crash Fleet Health",
        "After all non-destructive crash/kill cases, verify required services running and ubus-visible",
        "All MUST_BE_RUNNING services up; crashable set fully exercised in suite",
        "Extended",
        "BTS",
        "EXT-SW-PROC-03",
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_24",
        "SSH Session Resilience",
        "SEGV/KILL on sshd and network with SSH reconnect (isolated, not batched)",
        "SSH restored; service respawned; counters incremented; no full reboot",
        "Extended",
        "BTS",
        "EXT-SW-PROC-04",
        ("crash",),
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_25",
        "Batch Multi-Process Crash",
        "Simultaneous SEGV on a batch of non-critical services; verify each recovery",
        "All batched services recover independently; device stable",
        "Extended",
        "BTS",
        "EXT-SW-PROC-05",
        ("crash",),
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_26",
        "Procmon Readout Under Load",
        "CPU stress only — poll ubus/GUI counters and uptime without inducing crash",
        "Counters stable/readable under load; no spurious increments",
        "Extended",
        "BTS",
        "EXT-SW-PROC-06",
        ("stress",),
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_27",
        "Extended Dependency Chain",
        "Crash network; verify dnsmasq and odhcpd recovery and counter/logging behavior",
        "Parent and DHCP-related children recover; dependency logged",
        "Extended",
        "BTS",
        "EXT-SW-PROC-07",
        ("crash",),
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_28",
        "Unprivileged Kill (platform-aware)",
        "Try su/login/drop-priv kill on each protected service when nobody SSH is N/A",
        "Kill denied for each crashable service; PID unchanged; method documented",
        "Extended",
        "BTS",
        "EXT-SW-PROC-08",
        official=False,
    ),
    ProcessTestCase(
        "PROCESS_29",
        "SSH-Loss Recovery Reboot (automation)",
        "Before each crash/kill: arm reboot unless PROC_MON_HELLO within 60s; hello slides deadline while SSH up",
        "If automation loses SSH or hangs, DUT reboots; if hello received, no recovery reboot",
        "Extended",
        "BTS",
        "EXT-SW-PROC-09",
        official=False,
    ),
)

PROCESS_TEST_CASES: tuple[ProcessTestCase, ...] = OFFICIAL_PROCESS_TEST_CASES + EXTENDED_PROCESS_TEST_CASES

OFFICIAL_PROCESS_CASE_IDS: frozenset[str] = frozenset(
    case.case_id for case in OFFICIAL_PROCESS_TEST_CASES
)

EXTENDED_PROCESS_CASE_IDS: frozenset[str] = frozenset(
    case.case_id for case in EXTENDED_PROCESS_TEST_CASES
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
