"""MCS-relative throughput pass/warn/fail grading for performance matrix."""

from __future__ import annotations

MCS_THROUGHPUT_FAIL_PCT = 40.0
MCS_THROUGHPUT_WARN_PCT = 65.0


def throughput_pct_of_target(observed_mbps: float, target_mbps: float) -> float:
    if target_mbps <= 0:
        return 0.0
    return (float(observed_mbps) / float(target_mbps)) * 100.0


def evaluate_mcs_throughput(
    observed_mbps: float,
    target_mbps: float,
    *,
    fail_pct: float = MCS_THROUGHPUT_FAIL_PCT,
    warn_pct: float = MCS_THROUGHPUT_WARN_PCT,
) -> dict[str, object]:
    """
    Grade combined throughput against the MCS effective target.

    - >= warn_pct (65%): pass (green)
    - fail_pct–warn_pct (40–65%): warn (orange) — below expected MCS throughput
    - < fail_pct (40%): fail
    """
    observed = float(observed_mbps or 0.0)
    target = float(target_mbps or 0.0)
    pct = throughput_pct_of_target(observed, target)

    if target <= 0:
        return {
            "grade": "pass" if observed > 0 else "fail",
            "passed": observed > 0,
            "throughput_warn": False,
            "pct_of_target": pct,
            "target_mbps": target,
            "observed_mbps": observed,
            "reason": "No MCS throughput target configured.",
        }

    if pct < fail_pct:
        grade = "fail"
        passed = False
        throughput_warn = False
        reason = (
            f"Combined RX {observed:.2f} Mbps is {pct:.0f}% of MCS target "
            f"{target:.2f} Mbps (fail below {fail_pct:.0f}%)."
        )
    elif pct < warn_pct:
        grade = "warn"
        passed = True
        throughput_warn = True
        reason = (
            f"Combined RX {observed:.2f} Mbps is {pct:.0f}% of MCS target "
            f"{target:.2f} Mbps — below expected MCS throughput "
            f"(warn below {warn_pct:.0f}%)."
        )
    else:
        grade = "pass"
        passed = True
        throughput_warn = False
        reason = (
            f"Combined RX {observed:.2f} Mbps met MCS target "
            f"({pct:.0f}% of {target:.2f} Mbps)."
        )

    return {
        "grade": grade,
        "passed": passed,
        "throughput_warn": throughput_warn,
        "pct_of_target": round(pct, 1),
        "target_mbps": target,
        "observed_mbps": observed,
        "reason": reason,
    }
