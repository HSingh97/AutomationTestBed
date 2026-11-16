"""DFS suite — ``Senao UBR P2MP Test Plan_Aug26_Alpha_UBR655.xlsx`` sheet ``DFS``.

Radar is injected on the DUT current channel::

  radartool -i wifi1 bangradar

Gate live runs with::

  pytest tests/DFS -m DFS --profile ipv4_lab --allow-dfs-lab -q
  ./scripts/run_dfs.sh --allow-dfs-lab
  ./scripts/run_dfs.sh --allow-dfs-lab --allow-dfs-long   # 30-minute NOL/NOP cases
"""

from __future__ import annotations

import pytest

from config.dfs_test_cases import all_case_ids, case_by_id
from utils.dfs_flows import execute_dfs_case, set_long_flag

pytestmark = [pytest.mark.DFS]


@pytest.fixture(autouse=True)
def _require_dfs_lab(request):
    spec = getattr(request.node, "callspec", None)
    if spec is not None and "case_id" in getattr(spec, "params", {}):
        case = case_by_id(spec.params["case_id"])
        if case.get("status") in ("na", "not_available"):
            return
    if not request.config.getoption("--allow-dfs-lab"):
        pytest.skip("DFS lab cases require --allow-dfs-lab (live channel/radar on BTS)")
    set_long_flag(bool(request.config.getoption("--allow-dfs-long")))


def _run(case_id: str, profile_bundle) -> None:
    case = case_by_id(case_id)
    status = case.get("status")
    note = case.get("note") or case.get("title", "")[:80]
    if status in ("na", "not_available"):
        pytest.skip(note if "not available" in note.lower() else f"Not available: {note}")
    if status != "implemented":
        pytest.skip(f"Not implemented: {note}")
    execute_dfs_case(case_id, profile_bundle.active)


@pytest.mark.parametrize("case_id", all_case_ids())
def test_dfs(case_id, profile_bundle):
    _run(case_id, profile_bundle)
