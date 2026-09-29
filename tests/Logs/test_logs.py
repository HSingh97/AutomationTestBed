"""Logs suite — synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx`` sheet ``Logs``.

Gate live DUT runs with::

  pytest tests/Logs -m Logs --profile a60_lab --local-ip 10.0.0.120 --allow-logs-lab -q
  ./scripts/run_a60_logs.sh --allow-logs-lab --allow-logs-destructive
"""

from __future__ import annotations

import pytest

from config.logs_test_cases import DESTRUCTIVE_MODES, all_case_ids, case_by_id
from utils.logs_flows import execute_logs_case

pytestmark = [pytest.mark.Logs]


@pytest.fixture(autouse=True)
def _require_logs_lab(request):
    case_id = getattr(request.node, "callspec", None)
    if case_id is not None and "case_id" in case_id.params:
        cid = case_id.params["case_id"]
        case = case_by_id(cid)
        if case.get("status") in ("pending", "manual"):
            return
        mode = str(case.get("mode") or "")
        if mode in DESTRUCTIVE_MODES:
            if not request.config.getoption("--allow-logs-lab"):
                pytest.skip("Logs lab cases require --allow-logs-lab")
            if not request.config.getoption("--allow-logs-destructive"):
                pytest.skip(
                    f"{cid} is destructive — pass --allow-logs-destructive with --allow-logs-lab"
                )
            return
    if not request.config.getoption("--allow-logs-lab"):
        if case_id is not None and "case_id" in case_id.params:
            case = case_by_id(case_id.params["case_id"])
            if case.get("status") == "implemented":
                pytest.skip("Logs lab cases require --allow-logs-lab (live DUT log apply)")


def _run(case_id: str, profile_bundle) -> None:
    case = case_by_id(case_id)
    status = case.get("status")
    note = case.get("note") or case.get("title", "")[:80]
    if status in ("pending", "manual"):
        pytest.skip(f"Not implemented: {note}")
    if status != "implemented":
        pytest.skip(f"Not implemented: {note}")
    execute_logs_case(case_id, profile_bundle.active)


@pytest.mark.parametrize("case_id", all_case_ids())
def test_logs(case_id, profile_bundle):
    _run(case_id, profile_bundle)
