"""REG_04 CPU/memory regression. Five conditions, polled every 5 seconds.

Skip unless --allow-regression is set. Duration comes from REG_CPU_DURATION_S
(default 60). The bench is the qa-lab-01 primary link.
"""

from __future__ import annotations

import os

import pytest

from utils.regression_cpu_flows import CASES, CpuMonitorConfig, run_cpu_case, write_cpu_report

pytestmark = [pytest.mark.Regression, pytest.mark.REG_04]


def _require_regression(request) -> None:
    if not request.config.getoption("--allow-regression"):
        pytest.skip("CPU regression skipped. Re-run with --allow-regression.")


def _config() -> CpuMonitorConfig:
    duration = int(os.environ.get("REG_CPU_DURATION_S", "60"))
    return CpuMonitorConfig(duration_s=duration, poll_s=5)


def test_reg_04_cpu_utilization(request):
    _require_regression(request)
    cfg = _config()
    results = [run_cpu_case(cfg, case) for case in CASES]
    report = write_cpu_report(cfg, results)
    failed = [result for result in results if not result.ok]
    assert not failed, (
        "REG_04 failed: "
        + "; ".join(
            f"scenario {item.case.scenario} {item.case.title}: {item.error or 'no samples'}"
            for item in failed
        )
        + f". Report {report}"
    )
