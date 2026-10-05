"""Firewall suite — ``Senao UBR P2MP Test Plan_Aug26_Alpha_UBR655.xlsx`` sheet ``Firewall``.

Gate live DUT runs with::

  pytest tests/Firewall -m Firewall --profile ipv4_lab --allow-firewall-lab -q
  ./scripts/run_firewall.sh --allow-firewall-lab
"""

from __future__ import annotations

import pytest

from config.firewall_test_cases import all_case_ids, case_by_id
from utils.firewall_flows import execute_firewall_case

pytestmark = [pytest.mark.Firewall]


@pytest.fixture(autouse=True)
def _require_firewall_lab(request):
    if not request.config.getoption("--allow-firewall-lab"):
        pytest.skip("Firewall lab cases require --allow-firewall-lab (live BTS/CPE port scan)")


def _run(case_id: str, profile_bundle) -> None:
    case = case_by_id(case_id)
    status = case.get("status")
    note = case.get("note") or case.get("title", "")[:80]
    if status != "implemented":
        pytest.skip(f"Not implemented: {note}")
    execute_firewall_case(case_id, profile_bundle.active)


@pytest.mark.parametrize("case_id", all_case_ids())
def test_firewall(case_id, profile_bundle):
    _run(case_id, profile_bundle)
