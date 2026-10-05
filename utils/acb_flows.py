"""Assertions for Asymmetric CBW ``ACB_01`` … ``ACB_12``."""

from __future__ import annotations

from typing import Any

import pytest

from config.acb_test_cases import case_by_id
from utils.acb_lab import apply_pair, write_evidence

# Sheet steps need a stimulus this stand does not have. The bandwidth pair is
# still applied so a config failure is a fail, then the metric is skipped.
TRAFFIC_SKIP = {
    "ACB_02": "Retransmission growth needs several CPEs uploading at once; this stand has one CPE.",
    "ACB_03": "UL vs DL latency under load needs a filled uplink; link-test receive traffic stays near 1 Mbps here.",
    "ACB_04": "Burst uploads are not generated on this stand.",
    "ACB_06": "BER/FER needs a bit-error instrument; not on this stand.",
    "ACB_12": "Uplink queue growth needs sustained UL traffic; queues stay empty without it.",
}


def execute_acb_case(case_id: str, profile_active: dict[str, Any]) -> None:
    case = case_by_id(case_id)
    if case.get("mode") == "skip_na" or case.get("status") == "na":
        pytest.skip(case.get("note") or "Not available on this stand")

    dl = int(case["dl_mhz"])
    ul = int(case["ul_mhz"])
    snap = apply_pair(profile_active, dl, ul)
    write_evidence(case_id, {"wanted": {"dl": dl, "ul": ul}, "snap": snap})
    assert snap.get("ok"), snap.get("error") or f"{case_id}: asymmetric CBW did not apply"
    assert int(snap.get("force_bw") or 0) == ul, (
        f"{case_id}: CPE force_bw={snap.get('force_bw')}, wanted UL {ul} MHz"
    )
    assert str(dl) in str(snap.get("running_htmode") or ""), (
        f"{case_id}: BTS running {snap.get('running_htmode')}, wanted DL {dl} MHz"
    )

    tx = int(snap.get("tx_rate") or 0)
    rx = int(snap.get("rx_rate") or 0)
    if case_id in TRAFFIC_SKIP:
        pytest.skip(
            f"{case_id}: DL {dl} MHz / UL {ul} MHz applied "
            f"(PHY tx={tx} Mbps, rx={rx} Mbps). {TRAFFIC_SKIP[case_id]}"
        )

    assert tx > rx, (
        f"{case_id}: DL PHY {tx} Mbps is not above UL PHY {rx} Mbps "
        f"with DL {dl} / UL {ul}"
    )
    expect = dl / ul
    ratio = tx / rx
    assert ratio >= expect * 0.6, (
        f"{case_id}: PHY ratio {ratio:.2f} is below 60% of bandwidth ratio {expect:.2f} "
        f"(tx={tx}, rx={rx})"
    )
