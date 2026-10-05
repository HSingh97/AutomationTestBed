"""Asymmetric CBW catalog — ``Senao UBR P2MP Test Plan_Aug26_Alpha_UBR655.xlsx`` sheet ``Asymmetric CBW``."""

from __future__ import annotations

from typing import Any

ACB_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "ACB_01",
        "title": "DL/UL Capacity Scaling",
        "steps": "DL 80 MHz, UL 40 MHz; generate traffic both directions.",
        "expected": "DL scales with wider channel; UL limited by 40 MHz.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 80,
        "ul_mhz": 40,
    },
    {
        "id": "ACB_02",
        "title": "Packet Loss %, Retransmission %",
        "steps": "DL 40 MHz, UL 20 MHz; capture retransmission stats.",
        "expected": "UL congestion observed; retransmissions increase.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 40,
        "ul_mhz": 20,
    },
    {
        "id": "ACB_03",
        "title": "Latency Distribution",
        "steps": "DL 80 MHz, UL 20 MHz; ping/jitter under load.",
        "expected": "UL latency/jitter higher than DL.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 80,
        "ul_mhz": 20,
    },
    {
        "id": "ACB_04",
        "title": "Burst Traffic Handling",
        "steps": "DL 80 MHz, UL 20 MHz; bursty short uploads.",
        "expected": "UL latency spikes; DL unaffected.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 80,
        "ul_mhz": 20,
    },
    {
        "id": "ACB_05",
        "title": "Modulation Efficiency (Mbps per scheme)",
        "steps": "Force DL 40 MHz (64QAM), UL 20 MHz (QPSK).",
        "expected": "DL higher throughput; UL constrained.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 40,
        "ul_mhz": 20,
    },
    {
        "id": "ACB_06",
        "title": "BER %, FER %",
        "steps": "DL 80 MHz, UL 20 MHz; BER/FER under load.",
        "expected": "UL error rate rises faster due to congestion.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 80,
        "ul_mhz": 20,
    },
    {
        "id": "ACB_07",
        "title": "Uplink Congestion Impact on Downlink",
        "steps": "DL 40 MHz, UL 20 MHz; heavy UL; measure DL.",
        "expected": "DL remains stable; UL congestion does not spill over.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 40,
        "ul_mhz": 20,
    },
    {
        "id": "ACB_08",
        "title": "Dynamic Bandwidth Allocation",
        "steps": "Enable dynamic BW; mixed VoIP/video/bulk.",
        "expected": "Bandwidth dynamically adjusted.",
        "status": "na",
        "mode": "skip_na",
        "note": "Sheet: Not Tested. No dynamic-allocation control on this stand.",
    },
    {
        "id": "ACB_09",
        "title": "Subscriber Distance Effect",
        "steps": "Near/far SS with simultaneous UL/DL.",
        "expected": "Distant SS UL degrades faster.",
        "status": "na",
        "mode": "skip_na",
        "note": "Sheet: Not Tested. Needs attenuator/distance setup.",
    },
    {
        "id": "ACB_10",
        "title": "Mixed Service Validation",
        "steps": "DL 80 / UL 20; VoIP + video + FTP.",
        "expected": "VoIP/video hold; FTP throttled on UL.",
        "status": "na",
        "mode": "skip_na",
        "note": "Sheet: Not Tested. Mixed-service traffic profile not on this stand.",
    },
    {
        "id": "ACB_11",
        "title": "Mixed Service Validation",
        "steps": "DL 80 / UL 20; video DL + file upload UL.",
        "expected": "DL video smooth; UL slower but functional.",
        "status": "na",
        "mode": "skip_na",
        "note": "Sheet: Not Tested. Mixed-service traffic profile not on this stand.",
    },
    {
        "id": "ACB_12",
        "title": "Queue Build-Up Analysis",
        "steps": "DL 80 / UL 20; sustained UL; monitor BS UL queue.",
        "expected": "Queue grows; scheduler drops excess.",
        "status": "implemented",
        "mode": "apply_verify",
        "dl_mhz": 80,
        "ul_mhz": 20,
    },
]


def all_case_ids() -> list[str]:
    return [c["id"] for c in ACB_TEST_CASES]


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in ACB_TEST_CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)
