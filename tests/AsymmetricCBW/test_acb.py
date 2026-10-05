"""Asymmetric CBW suite — UBR655 sheet ``Asymmetric CBW`` (ACB_01–12).

  pytest tests/AsymmetricCBW -m AsymmetricCBW --profile ipv4_lab --allow-acb-lab -q
  ./scripts/run_acb.sh --allow-acb-lab
"""

from __future__ import annotations

import pytest

from config.acb_test_cases import all_case_ids, case_by_id
from utils.acb_flows import execute_acb_case
from utils.acb_lab import restore_acb

pytestmark = [pytest.mark.AsymmetricCBW]


@pytest.fixture(autouse=True)
def _require_acb_lab(request):
    if not request.config.getoption("--allow-acb-lab"):
        pytest.skip("Asymmetric CBW lab cases require --allow-acb-lab")


@pytest.fixture(scope="module", autouse=True)
def _restore_radio(request, profile_bundle):
    yield
    if request.config.getoption("--allow-acb-lab"):
        restore_acb(profile_bundle.active)


def _run(case_id: str, profile_bundle) -> None:
    case = case_by_id(case_id)
    if case.get("status") in ("manual", "na"):
        pytest.skip(case.get("note") or "Not available")
    if case.get("status") != "implemented":
        pytest.skip(case.get("note") or "Not implemented")
    execute_acb_case(case_id, profile_bundle.active)


@pytest.mark.parametrize("case_id", all_case_ids())
def test_acb(case_id, profile_bundle):
    _run(case_id, profile_bundle)
