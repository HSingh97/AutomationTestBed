#!/usr/bin/env python3
"""Poll BTS CPU and memory over SNMP every 5 seconds for REG_04.

REG_04 is one case with five conditions:
  1  no link, webpage closed
  2  link up, webpage closed
  3  no link, webpage open
  4  link up, webpage open
  5  link up, webpage open, throughput passing

Example:
  python3 scripts/regression_cpu_monitor.py --duration 60
  python3 scripts/regression_cpu_monitor.py --case 1 --duration 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.regression_cpu_flows import CASES, CpuMonitorConfig, run_cpu_case, write_cpu_report


def _select(names: list[str]) -> list:
    if not names or any(name.strip().upper() in {"REG_04", "ALL"} for name in names):
        return list(CASES)
    chosen = []
    known = ", ".join(f"{case.scenario} ({case.title})" for case in CASES)
    for name in names:
        key = name.strip().lower()
        match = [
            case
            for case in CASES
            if key in {case.scenario, case.title.lower()}
        ]
        if not match:
            raise SystemExit(f"Unknown REG_04 scenario {name!r}. Known: {known}")
        for case in match:
            if case not in chosen:
                chosen.append(case)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description="BTS CPU/memory regression (5 cases)")
    parser.add_argument("--duration", type=int, default=60, help="Seconds to sample each case")
    parser.add_argument("--poll", type=int, default=5, help="Seconds between SNMP samples")
    parser.add_argument(
        "--settle",
        type=int,
        default=30,
        help="Seconds to wait after setup before the first recorded sample",
    )
    parser.add_argument("--snmp-community", default="ubr@rw123")
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="REG_04 scenario 1..5 (repeatable). Default: all five",
    )
    parser.add_argument("--bts", default="192.168.3.20")
    parser.add_argument("--cpe", default="192.168.3.21")
    parser.add_argument("--lab", default="qa-lab-01@10.0.150.116")
    parser.add_argument("--lab-password", default="senao1234#")
    parser.add_argument("--device-password", default="Sen@0ubRNwk$")
    parser.add_argument("--gui-user", default="root")
    args = parser.parse_args()
    if args.duration < args.poll:
        raise SystemExit("--duration must be at least one poll interval")

    cfg = CpuMonitorConfig(
        lab_ssh=args.lab,
        lab_password=args.lab_password,
        device_password=args.device_password,
        gui_user=args.gui_user,
        bts_ip=args.bts,
        cpe_ip=args.cpe,
        snmp_community=args.snmp_community,
        duration_s=args.duration,
        poll_s=args.poll,
        settle_s=args.settle,
    )
    results = [run_cpu_case(cfg, case) for case in _select(args.case)]
    report = write_cpu_report(cfg, results)
    print(f"[cpu] report {report}")
    failed = [result.case.case_id for result in results if not result.ok]
    if failed:
        for result in results:
            if result.error:
                print(f"[cpu] {result.case.case_id} FAIL: {result.error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
