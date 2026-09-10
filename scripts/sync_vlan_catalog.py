#!/usr/bin/env python3
"""Sync ``config/vlan_test_cases.py`` from the Aug26 Alpha 2 test plan VLAN sheet."""

from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent.parent
XLSX = REPO / "Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx"
OUT = REPO / "config" / "vlan_test_cases.py"

# Not QinQ / transparent / trunk data-path scope for A60/A61 alpha lab.
# VLAN_08/09/14/19 are implemented (data-path; NMS left out).
NA_A60_A61 = {
    "VLAN_01",
    "VLAN_02",
    "VLAN_04",
    "VLAN_06",
    "VLAN_07",
    "VLAN_11",
    "VLAN_12",
    "VLAN_13",
    "VLAN_15",
    "VLAN_16",
    "VLAN_17",
    "VLAN_18",
}


def _num(tid: str) -> int:
    return int(tid.split("_")[1])


def classify(tid: str, title: str, steps: str) -> tuple[str, str | None, str]:
    """Return (status, mode, note)."""
    n = _num(tid)
    blob = f"{title} {steps}".lower()

    if tid in NA_A60_A61:
        return (
            "not_applicable",
            None,
            "Not applicable for A60/A61 alpha — outside QinQ/transparent/trunk VLAN scope.",
        )

    if tid == "VLAN_10":
        return (
            "manual",
            None,
            "To be tested manually: requires managed switch configured as VLAN trunk.",
        )

    if tid == "VLAN_29":
        return (
            "manual",
            None,
            "To be tested manually: CPE default ethernet / management GUI reachability (browser).",
        )

    if tid == "VLAN_30":
        return (
            "manual",
            None,
            "To be tested manually: CPE VLAN mode changes (trunk/QinQ/mgmt) via GUI.",
        )

    if tid in ("VLAN_24", "VLAN_31"):
        return (
            "manual",
            None,
            "To be tested manually: ethernet link flap / speed-duplex change under VLAN load.",
        )

    if tid == "VLAN_03":
        return ("implemented", "qinq_throughput", "TRex QinQ double-tag; verify end-to-end throughput.")

    if tid == "VLAN_05":
        return (
            "implemented",
            "qinq_negative_outer_only",
            "TRex outer-tag only; traffic must not pass (missing inner VLAN).",
        )

    if tid in ("VLAN_08", "VLAN_09"):
        return (
            "implemented",
            "qinq_data_tag_verify",
            "Data-path only (NMS skipped): QinQ UCI svlan/cvlan + TRex double-tag throughput.",
        )

    if tid == "VLAN_14":
        return (
            "implemented",
            "vlan_id_range_check",
            "ucidyn set mgmtvlan accept 1-4094; reject <1, >4094, 1002-1005, decimals, alphabets.",
        )

    if tid == "VLAN_19":
        return (
            "implemented",
            "tagged_data_pass_fail",
            "QinQ same svlan/cvlan must pass; wrong tags must not pass (TRex).",
        )

    if 20 <= n <= 23:
        direction = "pe_to_ce" if n in (22, 23) else "ce_to_pe"
        under_load = n in (21, 23)
        mode = "transparent_ping_under_load" if under_load else "transparent_ping_idle"
        return (
            "implemented",
            mode,
            f"Transparent bridge ping {direction.replace('_', ' ')}"
            + (" with TRex background load." if under_load else " without background traffic."),
        )

    if tid in ("VLAN_25", "VLAN_26"):
        return (
            "implemented",
            "destructive_bts_reboot",
            "BTS reboot under VLAN load — requires --allow-vlan-destructive.",
        )

    if tid in ("VLAN_27", "VLAN_28"):
        return (
            "implemented",
            "destructive_cpe_reboot",
            "CPE reboot under VLAN load — requires --allow-vlan-destructive.",
        )

    if 32 <= n <= 44:
        return (
            "implemented",
            "transparent_throughput",
            "BTS transparent + MVLAN disable; TRex untagged path throughput.",
        )

    if 45 <= n <= 59:
        return (
            "implemented",
            "qinq_hybrid_throughput",
            "BTS QinQ + CPE transparent with MVLAN enable; TRex QinQ throughput.",
        )

    return ("pending", None, f"Unclassified VLAN case {tid}.")


def load_rows() -> list[dict]:
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["VLAN"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    idx = {str(h).strip(): i for i, h in enumerate(hdr) if h}
    cases: list[dict] = []
    for row in rows[1:]:
        tid = row[idx["TESTCASE-ID"]]
        if not tid:
            continue
        title = str(row[idx["TEST DESCRIPTION"]] or "").strip()
        steps = str(row[idx["TEST STEPS"]] or "").strip()
        status, mode, note = classify(str(tid), title, steps)
        cases.append(
            {
                "id": str(tid),
                "title": title.replace("\n", " "),
                "steps": steps,
                "expected": str(row[idx["EXPECTED RESULT"]] or "").strip(),
                "dut": str(row[idx["TEST RUN DUT"]] or "").strip(),
                "type": str(row[idx["TEST TYPE"]] or "").strip(),
                "plan_result": str(row[idx["RESULT"]] or "").strip(),
                "status": status,
                "mode": mode,
                "note": note,
            }
        )
    wb.close()
    return cases


def render(cases: list[dict]) -> str:
    body = json.dumps(cases, indent=4).replace(": null", ": None")
    header = '''\
"""VLAN catalog synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx`` sheet ``VLAN``.

Regenerate: ``PYTHONPATH=. python3 scripts/sync_vlan_catalog.py``

``status``:
  - implemented — TRex / ping automation (QinQ, transparent, trunk-related data path)
  - manual — needs switch, GUI, or physical link ops
  - not_applicable — N/A for A60/A61 alpha (mgmt/access VLAN modes outside scope)
  - pending — not classified yet

Alpha scope: QinQ + transparent + trunk data path only; mgmt/MVLAN config cases marked N/A.
"""

from __future__ import annotations

from typing import Any

VLAN_TEST_CASES: list[dict[str, Any]] = '''
    footer = '''


def all_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES]


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in VLAN_TEST_CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def implemented_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "implemented"]


def manual_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "manual"]


def not_applicable_case_ids() -> list[str]:
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "not_applicable"]


def lab_case_ids() -> list[str]:
    """Cases that may hit live TRex/DUT when executed."""
    return [c["id"] for c in VLAN_TEST_CASES if c.get("status") == "implemented"]
'''
    return header + body + footer


def main() -> None:
    if not XLSX.is_file():
        raise SystemExit(f"Test plan not found: {XLSX}")
    cases = load_rows()
    expected = [f"VLAN_{i:02d}" for i in range(1, 60)]
    ids = [c["id"] for c in cases]
    if ids != expected:
        raise SystemExit(f"Expected VLAN_01..59, got {len(ids)} cases: {ids[:5]}..{ids[-3:]}")
    OUT.write_text(render(cases), encoding="utf-8")
    impl = sum(1 for c in cases if c["status"] == "implemented")
    na = sum(1 for c in cases if c["status"] == "not_applicable")
    manual = sum(1 for c in cases if c["status"] == "manual")
    print(f"Wrote {OUT} — implemented={impl} not_applicable={na} manual={manual} total={len(cases)}")


if __name__ == "__main__":
    main()
