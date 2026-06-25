from traffic.throughput_validation import (
    MCS_THROUGHPUT_FAIL_PCT,
    MCS_THROUGHPUT_WARN_PCT,
    evaluate_mcs_throughput,
    throughput_pct_of_target,
)


def test_throughput_pct_of_target():
    assert throughput_pct_of_target(65.0, 100.0) == 65.0
    assert throughput_pct_of_target(0.0, 100.0) == 0.0
    assert throughput_pct_of_target(10.0, 0.0) == 0.0


def test_mcs_throughput_pass_at_or_above_warn_threshold():
    target = 100.0
    at_warn = evaluate_mcs_throughput(target * MCS_THROUGHPUT_WARN_PCT / 100, target)
    above = evaluate_mcs_throughput(target * 0.80, target)
    assert at_warn["grade"] == "pass"
    assert at_warn["passed"] is True
    assert at_warn["throughput_warn"] is False
    assert above["grade"] == "pass"


def test_mcs_throughput_warn_between_fail_and_warn_thresholds():
    target = 100.0
    result = evaluate_mcs_throughput(50.0, target)
    assert result["grade"] == "warn"
    assert result["passed"] is True
    assert result["throughput_warn"] is True
    assert MCS_THROUGHPUT_FAIL_PCT <= result["pct_of_target"] < MCS_THROUGHPUT_WARN_PCT


def test_mcs_throughput_fail_below_fail_threshold():
    target = 100.0
    result = evaluate_mcs_throughput(30.0, target)
    assert result["grade"] == "fail"
    assert result["passed"] is False
    assert result["throughput_warn"] is False


def test_mcs_throughput_zero_rx_fails():
    result = evaluate_mcs_throughput(0.0, 5.6)
    assert result["grade"] == "fail"
    assert result["passed"] is False
