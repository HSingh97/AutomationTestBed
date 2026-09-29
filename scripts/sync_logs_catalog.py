#!/usr/bin/env python3
"""Sync ``config/logs_test_cases.py`` from the Aug26 Alpha 2 test plan Logs sheet."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent.parent
XLSX = REPO / "Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx"
OUT = REPO / "config" / "logs_test_cases.py"


def _num(tid: str) -> int:
    return int(tid.split("_")[1])


def classify(tid: str, title: str, steps: str) -> tuple[str, str | None, str]:
    n = _num(tid)
    blob = f"{title} {steps}".lower()
    is_system = "system logs" in title.lower()

    # --- NMS extract (DUT-only; NMS pending) ---
    if tid in {"LOGS_01", "LOGS_02", "LOGS_03", "LOGS_04", "LOGS_05", "LOGS_07", "LOGS_09"}:
        return (
            "implemented",
            "dut_log_extract",
            "DUT: collect logread/snlog/session logs via SSH. "
            "(NMS side implementation and validation pending)",
        )
    if tid == "LOGS_06":
        return (
            "implemented",
            "persistent_reboot_x4",
            "Destructive: reboot BTS 4x; assert DUT logs still readable after.",
        )
    if tid == "LOGS_08":
        return (
            "implemented",
            "storage_limit_fill",
            "Destructive: fill log storage briefly; assert logging still works / recovers.",
        )

    # --- User login ---
    if tid in {"LOGS_96", "LOGS_97"}:
        role = "installer" if "installer" in blob else "admin"
        return (
            "implemented",
            "user_login_log",
            f"GUI login as {role}; assert DUT session/snlog user-log entry.",
        )

    # --- Soft / hard reboot ---
    if "softreboot" in blob.replace(" ", "") or "soft reboot" in blob:
        return (
            "implemented",
            "soft_reboot_log",
            "Destructive: soft reboot DUT; assert logs persist / regenerate.",
        )
    if "hardreboot" in blob.replace(" ", "") or "hard reboot" in blob:
        return (
            "implemented",
            "hard_reboot_log",
            "Destructive: hard-style reboot (sysrq/reboot -f); assert logs after recover.",
        )

    # --- Memory / FW ---
    if "memory" in blob and ("80%" in blob or "80 %" in blob or "80%" in steps):
        return (
            "implemented",
            "memory_pressure_log",
            "Destructive: raise mem use ~80%; assert DUT logs still writable.",
        )
    if "memory" in blob and ("95%" in blob or "95 %" in blob):
        return (
            "implemented",
            "memory_pressure_log",
            "Destructive: raise mem use ~95%; assert DUT logs still writable.",
        )
    if "firmware" in blob:
        return (
            "implemented",
            "firmware_upgrade_log",
            "Destructive: sysupgrade if LOGS_FW_IMAGE/profile image set; else skip.",
        )

    # --- Logging interval ---
    if "interval" in blob and ("30" in blob or "60" in blob):
        return (
            "implemented",
            "temp_interval_set",
            "DUT: set temploginterval via ucidyn; verify UCI (no full 30/60m wait).",
        )

    # --- NMS server / remote syslog / temp toggle / TZ / NTP / location ---
    if tid in {"LOGS_40", "LOGS_83"} or ("nms server" in blob and "set" in blob):
        return (
            "implemented",
            "nms_server_set_dut",
            "DUT: set NMS server UCI via ucidyn. "
            "(NMS side implementation and validation pending)",
        )
    if "server ip to save system logs" in blob or (
        "logging" in title.lower() and "server ip" in blob
    ):
        return (
            "implemented",
            "remote_syslog_set",
            "DUT: set system log_ip/log_port via ucidyn.",
        )
    if "temerature" in blob or "temperature log" in blob:
        return (
            "implemented",
            "temp_log_toggle",
            "DUT: toggle templogstatus via ucidyn.",
        )
    if "timezone" in blob:
        return ("implemented", "timezone_set_log", "DUT: set timezone via ucidyn.")
    if "ntp" in blob:
        return ("implemented", "ntp_set_log", "DUT: set NTP server via ucidyn.")
    if "bts id" in blob or "site id" in blob or "location" in title.lower():
        return ("implemented", "location_set_log", "DUT: set location fields via ucidyn.")

    # --- DHCP / leases (before 2.4 catch-all — titles may mention 2.4 GHz DHCP) ---
    if "dhcp" in blob or "lease" in blob:
        return (
            "implemented",
            "dhcp_config_log",
            "DUT: tweak dhcp.lan / lan24 / host entry; assert logs evidence.",
        )

    # --- 2.4 GHz radio ---
    if "2.4" in blob:
        return (
            "implemented",
            "radio24_config_log",
            "DUT: apply wifi0/2.4GHz UCI change; assert logs evidence.",
        )

    # --- QoS ---
    if "qos" in blob or "pir" in blob or "sfc" in blob:
        return (
            "implemented",
            "qos_config_log",
            "DUT: tweak ath1qos UCI; assert logs evidence.",
        )

    # --- Network IP / VLAN / eth ---
    if "mgmt vlan" in blob:
        return ("implemented", "mgmt_vlan_log", "DUT: set mgmtvlan via ucidyn.")
    if "q-in-q" in blob or "qinq" in blob or "inner and outer" in blob:
        return ("implemented", "qinq_vlan_log", "DUT: set QinQ svlan/cvlan via ucidyn.")
    if "ipv4" in blob or "ipv6" in blob or "ip configurations" in blob:
        return ("implemented", "ip_config_log", "DUT: touch network.lan IPv6 proto.")
    if "mtu" in blob:
        return ("implemented", "mtu_config_log", "DUT: set network.lan.mtu.")
    if "speed" in blob or "duplex" in blob or "dulpex" in blob:
        return ("implemented", "eth_speed_log", "DUT: eth speed/duplex status + log.")

    # --- 5 GHz radio (config/system) ---
    if "wireless configurations – radio" in title.lower() or (
        "radio" in blob and "2.4" not in blob and "qos" not in blob
    ):
        return (
            "implemented",
            "radio_config_log",
            f"DUT: apply safe radio UCI change; assert {'system' if is_system else 'config'} log evidence.",
        )

    return ("implemented", "dut_log_extract", f"DUT log collect fallback for {tid}.")


def load_rows() -> list[dict]:
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["Logs"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h).strip() if h else f"c{i}" for i, h in enumerate(rows[0][:12])]
    idx = {h: i for i, h in enumerate(hdr)}
    cases: list[dict] = []
    for row in rows[1:]:
        row = list(row[:12]) if row else []
        if not any(row):
            continue
        tid = str(row[idx["TESTCASE-ID"]] or "").strip()
        if not tid.startswith("LOGS_"):
            continue
        title = str(row[idx["TEST DESCRIPTION"]] or "").strip()
        steps = str(row[idx["TEST STEPS"]] or "").strip()
        status, mode, note = classify(tid, title, steps)
        cases.append(
            {
                "id": tid,
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
"""Logs catalog synced to ``Senao UBR P2MP Test Plan_Aug26_Alpha_2.xlsx`` sheet ``Logs``.

Regenerate: ``PYTHONPATH=. python3 scripts/sync_logs_catalog.py``

``status``:
  - implemented — DUT-side automation (NMS client validation may still be pending)
  - pending — deferred
  - manual — needs external gear

Destructive modes require ``--allow-logs-destructive``.
NMS-related extract/set cases validate DUT only;
NMS side implementation and validation are pending.
"""

from __future__ import annotations

from typing import Any

LOGS_TEST_CASES: list[dict[str, Any]] = '''
    footer = '''


def all_case_ids() -> list[str]:
    return [c["id"] for c in LOGS_TEST_CASES]


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in LOGS_TEST_CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def implemented_case_ids() -> list[str]:
    return [c["id"] for c in LOGS_TEST_CASES if c.get("status") == "implemented"]


DESTRUCTIVE_MODES = frozenset(
    {
        "soft_reboot_log",
        "hard_reboot_log",
        "memory_pressure_log",
        "firmware_upgrade_log",
        "persistent_reboot_x4",
        "storage_limit_fill",
    }
)
'''
    return header + body + footer


def main() -> None:
    if not XLSX.is_file():
        raise SystemExit(f"Test plan not found: {XLSX}")
    cases = load_rows()
    OUT.write_text(render(cases), encoding="utf-8")
    impl = sum(1 for c in cases if c["status"] == "implemented")
    print(f"Wrote {OUT} — implemented={impl} total={len(cases)}")
    from collections import Counter

    print("modes:", Counter(c.get("mode") for c in cases))


if __name__ == "__main__":
    main()
