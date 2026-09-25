# Sanity — one-by-one (Alpha 2 / UBR655↔UBR655)

**One file for the whole Sanity suite.**  
You say which case → agent reads this → runs that nodeid only → verifies PASS + bench still healthy → updates the **Results** column here. No per-case MD files.

---

## Bench (fixed)

| | BTS | CPE |
|--|-----|-----|
| Model / FW | UBR655 / **1.0.0.149** | UBR655 / **1.0.0.149** |
| IPv4 | `192.168.2.10` | `192.168.2.11` |
| IPv6 | `2401:4900:d0:40d4:0:17b8:0:330` | `2401:4900:d0:40d4::17b8:0:331` |
| OOB hop | — | `10.0.0.1` via CPE PC `10.0.150.40` |
| Creds | root / profile password | same |
| Profile | `--profile default --skip-testbed-bootstrap` | |
| PDU | `192.168.1.190` BTS=1 CPE=2 | |

**Before every case**

1. Ping BTS/CPE v4 + v6.
2. BTS: `vlan.ath1.mode` = `transparent`|`0`, `mgmtvlan` = `1`.
3. RF: `wlanconfig ath1 list` shows ≥1 STA.

**After destructive cases** (03, 06, 23, 76, 77, FW/factory/reboot/mgmt-vlan): re-check preflight; recover before next case (see Recovery).

**How to run one case**

```bash
cd <repo>
export PYTHONPATH=. PYTHONUNBUFFERED=1
PY=/home/senao/Desktop/PythonProjects/.venv/bin/python
$PY -m pytest tests/Sanity/test_sanity.py::<NODE> -v --profile default --tb=short \
  --skip-testbed-bootstrap \
  --json-report --json-report-file=reports/artifacts/case_runs/sanity_<NN>.json
```

Artifacts: `reports/artifacts/case_runs/sanity_NN.json` + `.log`.  
Code: `tests/Sanity/test_sanity.py` → `utils/sanity_*.py` / `utils/sanity_flows.py`.  
Report at end (once): `utils/sanity_plan_report.py` → Alpha 2 folder.

---

## Recovery (when CPE/BTS go dark)

| Situation | What to do |
|-----------|------------|
| BTS not transparent / QinQ left from other suites | On BTS: `mode=transparent`, `mgmtvlan=1`, commit, `ucidyn apply` |
| CPE `.11` / `:331` down but RF up | Tunnel via BTS link-local or hop `10.0.150.40`→`10.0.0.1`; set static `.11` + `:331/120`, STA, transparent |
| After without-keep / factory | BTS may be `.1`; restore SSID/key/ch149, transparent, then CPE hop bootstrap |
| Lab PC stole `.10` on `enp1s0.101` | `sudo ip addr del 192.168.2.10/24 dev enp1s0.101` (PC stays on `.200`) |
| Duplicate IP on CPE | Ensure CPE is `.11` only (not `.10`) |

Helpers (prefer these): `ensure_bts_rf_transparent`, `ensure_sanity_cpe_ssh`, `sanity_03_restore_link`, `bootstrap_cpe_network_from_profile` in `utils/sanity_recovery.py`.

---

## Order of work

1. User names case (or “next”).
2. Agent: preflight → read row below → run **once**.
3. On FAIL: fix **existing** flow/helper if clearly wrong; do not rewrite the suite. Re-run **once** after fix.
4. Update **Result** in the table (`PASS` / `FAIL` + short note).
5. Confirm bench healthy → stop and wait for next case (unless user said “continue”).

---

## Case tracker (47 automated)

| # | ID | Pytest node (suffix) | What we verify | Risk / recover | Result |
|---|-----|----------------------|----------------|----------------|--------|
| 1 | SANITY_02 | `…_firmware_upgrade_keep_settings` | FW upgrade **with** keep on BTS+CPE; settings retained; link OK | reboot window; wait SSH | PASS |
| 2 | SANITY_03 | `…_firmware_upgrade_without_keep_settings` | FW **without** keep; factory-ish wipe; **must restore** link | **HIGH** — transparent + hop restore after | PASS |
| 3 | SANITY_04 | `…_wrong_model_firmware_rejected` | Wrong image rejected; device stays healthy | low | PASS |
| 4 | SANITY_05 | `…_abort_upgrade_handling` | Abort mid-upgrade; no brick | medium | PASS |
| 5 | SANITY_06 | `…_power_loss_during_upgrade` | PDU cut during flash; recover | **HIGH** — PDU + long wait | PASS |
| 6 | SANITY_07 | `…_gui_login_validation` | GUI login good/bad creds | low | PASS |
| 7 | SANITY_17 | `…_system_location_config` | Location fields set/read BTS+CPE | low | PASS |
| 8 | SANITY_18 | `…_system_timezone_config` | Timezone set/read | low | PASS |
| 9 | SANITY_19 | `…_static_ipv4_config` | Static IPv4 apply + restore | medium — don’t leave bad IP | PASS |
| 10 | SANITY_20 | `…_static_ipv6_config` | Static IPv6 apply + restore | medium | PASS |
| 11 | SANITY_23 | `…_mgmt_vlan_config` | Mgmt VLAN change + restore | **HIGH** — restore transparent/lab path | PASS |
| 12 | SANITY_25 | `…_dual_stack_config` | Dual-stack config | medium | PASS |
| 13 | SANITY_27 | `…_link_security` | Link security modes | medium — RF | PASS |
| 14 | SANITY_28 | `…_ddrs_mcs_modes` | DDRS MCS mode options | GUI MCS list (Alpha2 FW) | PASS |
| 15 | SANITY_29 | `…_ddrs_mcs_manual` | Manual MCS | GUI MCS list | PASS |
| 16 | SANITY_30 | `…_spatial_stream_mcs_ranges` | Spatial stream / MCS ranges | HE Dual MCS0–11 OK | PASS |
| 17 | SANITY_31 | `…_channel_width` | Channel width HT | RF; 160 soft | PASS |
| 18 | SANITY_32 | `…_channel_mode` | Auto/Manual channel; lab expects **ch149** recovery | RF / ACS | PASS |
| 19 | SANITY_33 | `…_frequency_range` | Frequency range | low | PASS |
| 20 | SANITY_35 | `…_acs_auto` | ACS auto then restore ch149 | RF | PASS |
| 21 | SANITY_36 | `…_tx_power` | TX power set/check | low | PASS |
| 22 | SANITY_37 | `…_dcs_config` | DCS | low | PASS |
| 23 | SANITY_38 | `…_ntp_sync` | NTP | low | PASS |
| 24 | SANITY_42 | `…_board_temperature` | Temp readable | low | PASS |
| 25 | SANITY_45 | `…_spectrum_report` | Spectrum report | low | PASS |
| 26 | SANITY_49 | `…_wireless_link_ping` | Ping over RF link | low | PASS |
| 27 | SANITY_50 | `…_rf_link_system_name` | System name on link stats | low | PASS |
| 28 | SANITY_52 | `…_rf_link_uptime_reset` | Uptime reset behavior | medium | PASS |
| 29 | SANITY_54 | `…_rf_link_rate_mcs` | Rate/MCS on link | low | |
| 30 | SANITY_55 | `…_rf_link_mcs_auto_range` | MCS auto range | low | |
| 31 | SANITY_57 | `…_tx_power_detailed_stats` | Detailed TX stats | low | |
| 32 | SANITY_60 | `…_obss_utilization` | OBSS util | low | |
| 33 | SANITY_61 | `…_combined_utilization` | Combined util | low | |
| 34 | SANITY_62 | `…_system_summary` | System summary page | low | |
| 35 | SANITY_76 | `…_soft_hard_reboot` | Soft + hard (PDU) reboot | **HIGH** — wait + recover | |
| 36 | SANITY_77 | `…_factory_reset_keep_settings` | Factory **with** keep | **HIGH** — retain + link | |
| 37 | SANITY_81 | `…_lldp_enable_disable` | LLDP on/off | low | |
| 38 | SANITY_82 | `…_lldp_neighbor_detection` | LLDP neighbor | needs neighbor | |
| 39 | SANITY_83 | `…_lldp_discovery_table` | LLDP table | needs neighbor | |
| 40 | SANITY_84 | `…_link_test_tool` | Link test tool | medium | |
| 41 | SANITY_85 | `…_audit_config_logs` | Audit/config logs | low | |
| 42 | SANITY_86 | `…_arp_bridge_table` | ARP/bridge table GUI | low | |
| 43 | SANITY_111 | `…_installer_dashboard` | Installer dashboard | installer login | |
| 44 | SANITY_112 | `…_installer_quickstart` | Installer quickstart | installer | |
| 45 | SANITY_113 | `…_installer_link_statistics` | Installer link stats | installer | |
| 46 | SANITY_114 | `…_installer_site_survey` | Site survey | installer | |
| 47 | SANITY_115 | `…_installer_soft_reboot` | Installer soft reboot | medium | |

Full nodeid example:

`tests/Sanity/test_sanity.py::test_sanity_02_firmware_upgrade_keep_settings`

---

## Progress log

| When | Case | Outcome | Note |
|------|------|---------|------|
| 2026-09-23 16:44+ | SANITY_02…29 | PASS | One-by-one runner |
| 2026-09-23 18:54 | SANITY_30 | PASS | PASS |
| 2026-09-23 20:04 | SANITY_31 | PASS | PASS |
| 2026-09-23 20:05+ | SANITY_32… | RUNNING | one-by-one continuing |

---

## Agent short rule

```text
docs/SANITY.md is the only Sanity playbook.
One case at a time. Preflight → pytest one node → update Result.
BTS transparent + mgmtvlan=1. UBR655↔UBR655 FW 1.0.0.149.
User picks the case (or says next).
```
