#!/usr/bin/env python3
"""Generate sample Senao performance HTML reports for local preview."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.performance_report import write_html_report  # noqa: E402

CPE_HOSTS = [
    "2001:2002:2003:2004:2005:2006:2007:11c9",
    "2001:2002:2003:2004:2005:2006:2007:118b",
    "2001:2002:2003:2004:2005:2006:2007:1178",
    "2001:2002:2003:2004:2005:2006:2007:113d",
]

BUILD71_ROWS = [
    ("HT20", "MCS23", 178.12, 286.0),
    ("HT40", "MCS23", 400.92, 573.0),
    ("HT80", "MCS23", 790.11, 1201.0),
]

DEMO_ROWS = [
    ("HT20", "MCS21", 165.0, 258.0),
    ("HT20", "MCS22", 172.0, 275.0),
    ("HT20", "MCS23", 178.12, 286.0),
    ("HT40", "MCS21", 360.0, 516.0),
    ("HT40", "MCS22", 382.0, 545.0),
    ("HT40", "MCS23", 400.92, 573.0),
    ("HT80", "MCS21", 720.0, 1080.0),
    ("HT80", "MCS22", 755.0, 1140.0),
    ("HT80", "MCS23", 790.11, 1201.0),
]


def _client_row(su_index: int, tx_rate: float) -> dict:
    return {
        "system_name": f"cpe{su_index}",
        "su_index": su_index,
        "ip": CPE_HOSTS[su_index - 1],
        "tx_rate": f"{tx_rate:.0f} ({su_index})",
        "rx_rate": f"{tx_rate:.0f} ({su_index})",
        "l_snr1": "38",
        "l_snr2": "37",
        "r_snr1": "36",
        "r_snr2": "35",
        "l_rssi1": "-52",
        "l_rssi2": "-53",
        "r_rssi1": "-54",
        "r_rssi2": "-55",
    }


def _records(rows: list[tuple[str, str, float, float]]) -> list[dict]:
    records: list[dict] = []
    for bandwidth, mcs, bidi, operating_rate in rows:
        dl = round(bidi * 0.75, 2)
        ul = round(bidi * 0.25, 2)
        per_cpe = round(dl / 4, 2)
        records.append(
            {
                "bandwidth": bandwidth,
                "mcs": mcs,
                "mode": "Bidirectional",
                "ratio": "75:25",
                "passed": True,
                "throughput_passed": True,
                "operating_rate_ok": bandwidth != "HT80",
                "effective_target_mbps": round(operating_rate * 0.7, 2),
                "operating_rate_mbps": operating_rate,
                "packet_size": 1500,
                "duration_s": 30,
                "noise_dbm": "-93",
                "spatial_stream": 2,
                "cpe_hosts": CPE_HOSTS,
                "stats": {
                    "combined": {"rx_mbps": bidi},
                    "downlink": {"rx_mbps": dl},
                    "uplink": {"rx_mbps": ul},
                    "trex": {
                        "summary_by_device": {
                            f"SU{i}": {"avg_rx_mbps": per_cpe, "avg_tx_mbps": round(ul / 4, 2)}
                            for i in range(1, 5)
                        }
                    },
                },
                "link_validation": {
                    "operating_rate_ok": bandwidth != "HT80",
                    "expected_operating_rate_mbps": operating_rate,
                    "clients": [_client_row(i, operating_rate / 4) for i in range(1, 5)],
                    "spec": {"mcs": mcs, "modulation": "1024-QAM 5/6", "operating_rate_mbps": operating_rate},
                },
                "mcs_config": {
                    "mcs_config_ok": True,
                    "expected_uci_mcs": mcs.replace("MCS", ""),
                    "checks": [
                        {"label": "BTS", "role": "BTS", "su_index": 0, "actual_mcs": mcs.replace("MCS", ""), "ok": True},
                        *[
                            {
                                "label": f"SU{idx}",
                                "role": "CPE",
                                "su_index": idx,
                                "actual_mcs": mcs.replace("MCS", ""),
                                "ok": True,
                            }
                            for idx in range(1, 5)
                        ],
                    ],
                },
            }
        )
    return records


def main() -> None:
    samples_dir = ROOT / "docs" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    build71_path = samples_dir / "Senao_Performance_71_Report_20260618.html"
    write_html_report(
        records=_records(BUILD71_ROWS),
        run_meta={
            "executed_at": "2026-06-18 17:58:44 IST",
            "Bandwidths": "HT20, HT40, HT80",
            "MCS Rates": "MCS23",
            "Ratios": "75:25",
            "Duration (s)": "30",
        },
        path=build71_path,
        testbed_summary={
            "bts": {
                "model": "UBR-630",
                "fw_version": "qa-lab-02",
                "ip": "2001:2002:2003:2004:2005:2006:2007:1111",
                "vlan": "200/201",
                "qos": "75:25",
            },
            "cpe": {
                "model": "UBR-620 ×4",
                "fw_version": "qa-lab-02",
                "ip": "4 SU /120",
                "vlan": "200/201",
            },
            "stand": "test-qa-lab-02",
            "jenkins_build": "71",
        },
    )

    demo_path = samples_dir / "Senao_Performance_Sample_MultiMCS_Report.html"
    write_html_report(
        records=_records(DEMO_ROWS),
        run_meta={
            "executed_at": "2026-06-18 18:30:00 IST",
            "Bandwidths": "HT20, HT40, HT80",
            "MCS Rates": "MCS21, MCS22, MCS23",
            "Ratios": "75:25",
            "Duration (s)": "30",
        },
        path=demo_path,
        testbed_summary={
            "bts": {
                "model": "UBR-630",
                "fw_version": "qa-lab-02",
                "ip": "2001:2002:2003:2004:2005:2006:2007:1111",
                "vlan": "200/201",
                "qos": "75:25",
            },
            "cpe": {
                "model": "UBR-620 ×4",
                "fw_version": "qa-lab-02",
                "ip": "4 SU /120",
                "vlan": "200/201",
            },
            "stand": "test-qa-lab-02",
            "jenkins_build": "sample",
        },
    )

    print(f"Wrote {build71_path}")
    print(f"Wrote {demo_path}")


if __name__ == "__main__":
    main()
