"""Unit tests: QoS catalog stays aligned to plan IDs and mode handlers."""

from __future__ import annotations

from config.qos_test_cases import (
    QOS_TEST_CASES,
    all_case_ids,
    case_by_id,
    implemented_case_ids,
    manual_case_ids,
)
from utils.qos_flows import _MODE_HANDLERS


def test_catalog_has_qos_01_through_40():
    ids = all_case_ids()
    assert ids == [f"QoS_{i:02d}" for i in range(1, 41)]
    assert len(QOS_TEST_CASES) == 40


def test_plan_ids_match_expected_titles():
    """Spot-check plan alignment for the remapped first ten cases."""
    assert "voice" in case_by_id("QoS_01")["title"].lower()
    assert "video" in case_by_id("QoS_02")["title"].lower()
    assert "gaming" in case_by_id("QoS_03")["title"].lower()
    assert "hierarchy" in case_by_id("QoS_04")["title"].lower()
    assert "bandwidth" in case_by_id("QoS_05")["title"].lower()
    assert "critical" in case_by_id("QoS_06")["title"].lower()
    assert "without qos" in case_by_id("QoS_07")["title"].lower()
    assert "dscp" in case_by_id("QoS_08")["title"].lower()
    assert "voice" in case_by_id("QoS_09")["title"].lower() and "data" in case_by_id("QoS_09")["title"].lower()
    assert "rate" in case_by_id("QoS_10")["title"].lower()


def test_implemented_modes_have_handlers():
    for case_id in implemented_case_ids():
        case = case_by_id(case_id)
        mode = case.get("mode")
        assert mode in _MODE_HANDLERS, f"{case_id} mode {mode!r} missing handler"


def test_not_available_cases_documented():
    for cid in ("QoS_27", "QoS_29", "QoS_36", "QoS_39"):
        case = case_by_id(cid)
        assert case["status"] == "not_available"
        assert case.get("note")
        assert "not available" in case["note"].lower()
        assert case.get("mode") is None
    assert "alerts" in case_by_id("QoS_36")["note"].lower()
    assert "user-group" in case_by_id("QoS_29")["note"].lower()


def test_manual_cases_documented():
    assert manual_case_ids() == ["QoS_28", "QoS_31", "QoS_33", "QoS_35"]
    for cid in manual_case_ids():
        case = case_by_id(cid)
        assert case["status"] == "manual"
        assert case.get("note", "").lower().startswith("to be tested manually")
        assert case.get("mode") is None


def test_qos_21_is_reboot_retention():
    case = case_by_id("QoS_21")
    assert case["status"] == "implemented"
    assert case["mode"] == "reboot_retention"


def test_qos_22_is_setup_covered_pass_by_default():
    case = case_by_id("QoS_22")
    assert case["status"] == "implemented"
    assert case["mode"] == "setup_covered"
    assert "wired" in case["note"].lower() and "wireless" in case["note"].lower()


def test_qos_30_is_encapsulated_traffic():
    case = case_by_id("QoS_30")
    assert case["status"] == "implemented"
    assert case["mode"] == "encapsulated_traffic"
    assert "qinq" in case["note"].lower()


def test_qos_08_is_dscp_not_voice():
    """Regression: QoS_08 must be DSCP classification (old suite had this as QOS_01)."""
    assert case_by_id("QoS_08")["mode"] == "dscp_classification"
    assert case_by_id("QoS_01")["mode"] == "voice_priority"
