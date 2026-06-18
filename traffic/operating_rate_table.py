"""
Operating rates from the UBR PHY specification sheet (Dual spatial stream / Mbps).

Source columns: Rate (Single / Dual) — we use Dual as the operating rate when spatial_stream=2.
MCS12–MCS23 map to the same modulation row as MCS0–MCS11 (index = mcs_number % 12).
"""

from __future__ import annotations

import re
from typing import Any

MODULATION_SCHEMES: list[str] = [
    "BPSK 1/2",
    "QPSK 1/2",
    "QPSK 3/4",
    "16-QAM 1/2",
    "16-QAM 3/4",
    "64-QAM 2/3",
    "64-QAM 3/4",
    "64-QAM 5/6",
    "256-QAM 3/4",
    "256-QAM 5/6",
    "1024-QAM 3/4",
    "1024-QAM 5/6",
]

# Dual-stream operating rate (Mbps) per bandwidth, indexed by MCS0–MCS11 row.
OPERATING_RATE_DUAL_MBPS: dict[str, list[float]] = {
    "HT20": [17, 34, 51, 68, 103, 137, 154, 172, 206, 229, 258, 286],
    "HT40": [34, 68, 103, 137, 206, 275, 309, 344, 412, 455, 516, 573],
    "HT80": [72, 144, 216, 288, 432, 576, 648, 720, 864, 960, 1080, 1201],
}

# Single-stream reference (Mbps) — same sheet, first value in Rate column.
OPERATING_RATE_SINGLE_MBPS: dict[str, list[float]] = {
    "HT20": [8, 17, 25, 34, 51, 68, 77, 86, 103, 114, 129, 143],
    "HT40": [17, 34, 51, 68, 103, 137, 154, 172, 206, 229, 258, 286],
    "HT80": [36, 72, 108, 144, 216, 288, 324, 360, 432, 480, 540, 600],
}

MIN_SNR_DB: dict[str, list[float]] = {
    "HT20": [11, 13, 16, 19, 21, 24, 26, 28, 30, 32, 34, 36],
    "HT40": [13, 16, 19, 21, 24, 27, 29, 31, 33, 36, 38, 41],
    "HT80": [15, 18, 21, 23, 26, 29, 31, 34, 37, 40, 42, 44],
}

# HT20 lab reference TCP throughput (Mbps) from benchmark sheet — for efficiency display.
HT20_REFERENCE_TCP_MBPS: dict[int, dict[str, float]] = {
    0: {"uplink": 6.31, "downlink": 6.42, "bidi": 6.12},
    1: {"uplink": 12.7, "downlink": 12.8, "bidi": 12.41},
    2: {"uplink": 19.5, "downlink": 19.7, "bidi": 19.37},
    3: {"uplink": 26.3, "downlink": 26.5, "bidi": 25.68},
    4: {"uplink": 39.5, "downlink": 39.8, "bidi": 39.3},
    5: {"uplink": 51.4, "downlink": 53.9, "bidi": 52.4},
    6: {"uplink": 59.9, "downlink": 60.4, "bidi": 59.9},
    7: {"uplink": 65.4, "downlink": 67.1, "bidi": 66.2},
    8: {"uplink": 79.4, "downlink": 71.2, "bidi": 80.3},
    9: {"uplink": 88.1, "downlink": 88.9, "bidi": 88.8},
    10: {"uplink": 97.9, "downlink": 100.0, "bidi": 100.5},
}


def normalize_bandwidth(bandwidth: str) -> str:
    clean = bandwidth.strip().upper().replace(" ", "")
    if clean.endswith("+"):
        clean = clean.rstrip("+")
    if clean not in OPERATING_RATE_DUAL_MBPS:
        raise ValueError(f"Unsupported bandwidth '{bandwidth}'")
    return clean


def uci_htmode_value(bandwidth: str) -> str:
    """UCI/htmode string for ucidyn set (HT40 requires HT40+ on UBR630)."""
    bw = normalize_bandwidth(bandwidth)
    if bw == "HT40":
        return "HT40+"
    return bw


def uci_htmode_matches(expected_bandwidth: str, uci_htmode: str) -> bool:
    expected_uci = uci_htmode_value(expected_bandwidth)
    actual = str(uci_htmode or "").strip().upper().replace(" ", "")
    return expected_uci in actual or normalize_bandwidth(expected_bandwidth) in actual


def normalize_mcs(mcs: str) -> str:
    clean = mcs.strip().upper()
    if not clean.startswith("MCS"):
        clean = f"MCS{clean}"
    if not re.fullmatch(r"MCS\d+", clean):
        raise ValueError(f"Unsupported MCS '{mcs}'")
    return clean


def mcs_number(mcs: str) -> int:
    return int(normalize_mcs(mcs).replace("MCS", ""))


def sheet_row_index(mcs: str) -> int:
    """Map MCS0–MCS23 onto the 12 modulation rows in the spec sheet."""
    return mcs_number(mcs) % 12


def modulation_scheme(mcs: str) -> str:
    return MODULATION_SCHEMES[sheet_row_index(mcs)]


def operating_rate_mbps(
    bandwidth: str,
    mcs: str,
    *,
    spatial_streams: int = 2,
) -> float:
    bw = normalize_bandwidth(bandwidth)
    row = sheet_row_index(mcs)
    table = OPERATING_RATE_DUAL_MBPS if spatial_streams >= 2 else OPERATING_RATE_SINGLE_MBPS
    return float(table[bw][row])


def min_snr_db(bandwidth: str, mcs: str) -> float:
    bw = normalize_bandwidth(bandwidth)
    return float(MIN_SNR_DB[bw][sheet_row_index(mcs)])


def lookup_spec(mcs: str, bandwidth: str, *, spatial_streams: int = 2) -> dict[str, Any]:
    row = sheet_row_index(mcs)
    bw = normalize_bandwidth(bandwidth)
    rate = operating_rate_mbps(bw, mcs, spatial_streams=spatial_streams)
    spec: dict[str, Any] = {
        "mcs": normalize_mcs(mcs),
        "mcs_pair": f"MCS{row} / MCS{row + 12}",
        "bandwidth": bw,
        "modulation": modulation_scheme(mcs),
        "operating_rate_mbps": rate,
        "operating_rate_single_mbps": OPERATING_RATE_SINGLE_MBPS[bw][row],
        "operating_rate_dual_mbps": OPERATING_RATE_DUAL_MBPS[bw][row],
        "min_snr_db": MIN_SNR_DB[bw][row],
        "spatial_streams": spatial_streams,
    }
    if bw == "HT20" and row in HT20_REFERENCE_TCP_MBPS:
        spec["reference_tcp_mbps"] = HT20_REFERENCE_TCP_MBPS[row]
    return spec
