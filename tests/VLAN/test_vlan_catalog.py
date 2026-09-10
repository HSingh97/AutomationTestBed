"""Unit tests: VLAN catalog aligned to Aug26 Alpha 2 plan."""

from __future__ import annotations

from config.vlan_test_cases import (
    VLAN_TEST_CASES,
    all_case_ids,
    case_by_id,
    implemented_case_ids,
    manual_case_ids,
    not_applicable_case_ids,
)
from utils.vlan_flows import _MODE_HANDLERS


def test_catalog_has_vlan_01_through_59():
    ids = all_case_ids()
    assert ids == [f"VLAN_{i:02d}" for i in range(1, 60)]
    assert len(VLAN_TEST_CASES) == 59


def test_alpha_scope_counts():
    assert len(implemented_case_ids()) == 42
    assert len(not_applicable_case_ids()) == 16
    assert len(manual_case_ids()) == 1


def test_implemented_modes_have_handlers():
    for case_id in implemented_case_ids():
        mode = case_by_id(case_id).get("mode")
        assert mode in _MODE_HANDLERS, f"{case_id} mode {mode!r} missing handler"


def test_na_cases_are_out_of_scope():
    for cid in not_applicable_case_ids():
        note = case_by_id(cid).get("note", "").lower()
        assert "a60/a61" in note or "not applicable" in note


def test_qinq_cases():
    assert case_by_id("VLAN_03")["mode"] == "qinq_throughput"
    assert case_by_id("VLAN_05")["mode"] == "qinq_negative_outer_only"


def test_transparent_bridge_ping_modes():
    assert case_by_id("VLAN_20")["mode"] == "transparent_ping_idle"
    assert case_by_id("VLAN_21")["mode"] == "transparent_ping_under_load"


def test_hybrid_qinq_range():
    for i in range(45, 60):
        assert case_by_id(f"VLAN_{i:02d}")["mode"] == "qinq_hybrid_throughput"
