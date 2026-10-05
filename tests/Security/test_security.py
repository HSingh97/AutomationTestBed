"""Security suite — UBR655 sheet ``Security`` (SEC-01–16).

  pytest tests/Security -m Security --profile ipv4_lab --allow-security-lab -q
  ./scripts/run_security.sh --allow-security-lab
"""

from __future__ import annotations

import pytest

from config.security_test_cases import all_case_ids, case_by_id
from utils.security_flows import execute_security_case

pytestmark = [pytest.mark.Security]


@pytest.fixture(autouse=True)
def _require_security_lab(request):
    if not request.config.getoption("--allow-security-lab"):
        pytest.skip("Security lab cases require --allow-security-lab")


def _run(case_id: str, profile_bundle) -> None:
    case = case_by_id(case_id)
    if case.get("status") in ("manual", "na"):
        pytest.skip(case.get("note") or "Manual / not available")
    if case.get("status") != "implemented":
        pytest.skip(case.get("note") or "Not implemented")
    execute_security_case(case_id, profile_bundle.active)


@pytest.mark.parametrize("case_id", all_case_ids())
def test_security(case_id, profile_bundle):
    _run(case_id, profile_bundle)
