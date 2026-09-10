"""VLAN suite — synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx`` sheet ``VLAN``.

Gate live lab traffic with::

  pytest tests/VLAN -m VLAN --profile a60_lab --local-ip 10.0.0.120 --allow-vlan-lab -q
  pytest tests/VLAN -m VLAN_03 --profile a60_lab --allow-vlan-lab -q
"""

from __future__ import annotations

import pytest

from config.vlan_test_cases import all_case_ids, case_by_id
from utils.vlan_flows import execute_vlan_case

pytestmark = [pytest.mark.VLAN]


@pytest.fixture(autouse=True)
def _require_vlan_lab(request):
    case_id = getattr(request.node, "callspec", None)
    if case_id is not None and "case_id" in case_id.params:
        cid = case_id.params["case_id"]
        case = case_by_id(cid)
        if case.get("status") in ("not_applicable", "manual", "pending"):
            return
        mode = case.get("mode") or ""
        if mode.startswith("destructive"):
            if not request.config.getoption("--allow-vlan-destructive"):
                pytest.skip(
                    f"{cid} is destructive — pass --allow-vlan-destructive with --allow-vlan-lab"
                )
    if not request.config.getoption("--allow-vlan-lab"):
        case = None
        if case_id is not None and "case_id" in case_id.params:
            case = case_by_id(case_id.params["case_id"])
        if case and case.get("status") == "implemented":
            pytest.skip("VLAN lab cases require --allow-vlan-lab (live TRex + DUT VLAN apply)")


def _run(case_id: str, profile_bundle) -> None:
    case = case_by_id(case_id)
    status = case.get("status")
    note = case.get("note") or case.get("title", "")[:80]

    if status == "not_applicable":
        pytest.skip(f"Not implemented: {note}")
    if status == "manual":
        pytest.skip(f"Not implemented: {note}")
    if status == "pending":
        pytest.skip(f"Not implemented: {note}")
    if status != "implemented":
        pytest.skip(f"Not implemented: {note}")

    execute_vlan_case(case_id, profile_bundle.active)


@pytest.mark.parametrize("case_id", all_case_ids())
def test_vlan(case_id, profile_bundle):
    _run(case_id, profile_bundle)
