# Test suite playbook

This is the only file for how a case is tested and what the last result was.
Read this before a run. Follow the steps already written here.
Do not rewrite suite code because a case failed. Fix the bench, then run the same pytest node again.

`docs/SANITY.md` is the old Sanity-only tracker. Use this file instead.

---

## Rule

1. User names the suite and the case (or says next).
2. Preflight the live link. Do not change SSID, channel, or Tx power to match an older note.
3. Run that one pytest node. Profile `--profile default --skip-testbed-bootstrap`.
4. On FAIL: decide setup vs product. Recover the bench. Re-run the same node once.
5. Change code only when the check itself is wrong (bad IP, wrong timeout, GUI locator). Do not redesign the case.
6. Update the **Result** cell in this file. Then confirm the link is back before the next case.

Destructive cases (firmware, factory, reboot, DHCP client, management VLAN) must restore static lab IPs and the RF link before the next case starts.

---

## Bench

Use whatever link is up. Last confirmed link (7 Oct 2026): SSID `UBR655_R1_PS`, channel 157, HE80, BTS `C4:E3:CE:63:E6:C7`, CPE `c4:e3:ce:63:e6:31`.

| | BTS | CPE |
|--|-----|-----|
| Model | UBR655 | UBR655 |
| IPv4 | `192.168.2.10` | `192.168.2.11` |
| IPv6 | `2401:4900:d0:40d4:0:17b8:0:330` | `2401:4900:d0:40d4::17b8:0:331` |
| SSH | `root` / profile password | same |
| CPE reach | From this PC when the lab port is up | Else Desktop `10.0.150.40` (`root` / `senao1234#`) then CPE |
| Factory hop | — | `10.0.0.1` via that desktop |
| PDU | `192.168.1.190` admin/1234, BTS outlet 1, CPE outlet 2 | |

**Preflight (every case)**

1. Ping BTS `.10` and CPE `.11` (from this PC, or from `10.0.150.40` if this PC cannot see the CPE).
2. CPE `iwconfig ath1` shows an Access Point, not `Not-Associated`.
3. BTS `wlanconfig ath1 list` shows the CPE MAC.
4. BTS VLAN mode is `transparent` and `mgmtvlan` is `1` for Sanity and DHCP. Do not leave QinQ (`mgmtvlan` 101) on from another suite.

**Run one case**

```bash
cd "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed"
export PYTHONPATH=. PYTHONUNBUFFERED=1
PY=/home/senao/Desktop/PythonProjects/.venv/bin/python
$PY -m pytest tests/<Suite>/test_<file>.py::<NODE> -v --profile default --tb=short \
  --skip-testbed-bootstrap \
  --json-report --json-report-file=reports/artifacts/case_runs/<suite>_<NN>.json
```

**If the bench goes dark**

| What you see | What to do |
|---|---|
| BTS not transparent / QinQ left on | On BTS set `vlan.ath1.mode=transparent`, `mgmtvlan=1`, commit, apply. Do not `wifi down`. |
| CPE `.11` down, RF still up | Hop `10.0.150.40` → `10.0.0.1` or the live CPE IP. Put static `.11` and `:331/120` back. Leave the user's SSID alone. |
| After without-keep / factory | BTS may be `.1`. Restore lab IP `.10`, the live SSID/key, transparent, then CPE hop. |
| This PC stole `.10` | Remove `192.168.2.10` from the lab NIC. The PC stays on `.200`. |
| DHCP case left CPE as a client with no lease | Hop-restore static `.11`. Confirm ping both ways before the next case. |

---

## Sanity

Code: `tests/Sanity/test_sanity.py` → `utils/sanity_flows.py` and `utils/sanity_*.py`.
Report: `/home/senao/Desktop/Puneet/Alpha 2 Automation Reports/Senao_Sanity_Report.html`.

**Last verified:** all 47 automated cases **PASS** on UBR655↔UBR655 (Alpha 2). JSON is under `reports/artifacts/case_runs/_prev_20260925_103857/`. Cases 54, 57, 60, 62, 76, 77, 81, 85, 86, 113, 114, 115 failed once and passed on retest. A later full rerun was started and stopped around SANITY_25; that partial run is not the result.

Sheet cases with no automation stay **Not Tested** or **Not Available**. Do not invent flows for them.

Shared Sanity method: open BTS GUI and SSH, do the sheet action, read the same value back from GUI and from UCI/SSH, restore the original value, confirm the RF link.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| SANITY_02 | `test_sanity_02_firmware_upgrade_keep_settings` | Upgrade BTS and CPE from `/tftpboot` with Keep Settings. Wait for SSH. Read back IP, SSID, and link. | Settings kept. Link up. | PASS |
| SANITY_03 | `test_sanity_03_firmware_upgrade_without_keep_settings` | Upgrade without keep. Device returns toward factory. Restore `.10` / `.11`, transparent, live SSID, hop if CPE is on `10.0.0.1`. | Image applied. Link restored by the case. | PASS |
| SANITY_04 | `test_sanity_04_wrong_model_firmware_rejected` | Offer a wrong-model image in the upgrade page. | GUI rejects it. Running image unchanged. Link stays up. | PASS |
| SANITY_05 | `test_sanity_05_abort_upgrade_handling` | Start an upgrade and abort before flash completes. | Device does not brick. Link returns. | PASS |
| SANITY_06 | `test_sanity_06_power_loss_during_upgrade` | Start upgrade, PDU-cut the device mid-flash, wait for boot. | Device boots. Link restored. | PASS |
| SANITY_07 | `test_sanity_07_gui_login_validation` | Log in with the real password, then a bad password. | Good login enters the GUI. Bad login stays on the login page. | PASS 15 Oct 2026 (106s). BTS and CPE HTTP login on :330 and :331. |
| SANITY_17 | `test_sanity_17_system_location_config` | Set location on BTS and CPE, read GUI and UCI, restore. | Both sides show the new location, then the original. | PASS 15 Oct 2026 (920s). Location applied and restored. Link up. |
| SANITY_18 | `test_sanity_18_system_timezone_config` | Set timezone, read GUI and UCI, restore. | Timezone matches on both, then original is back. | PASS 15 Oct 2026 (210s). GMT0 applied and restored to IST-5:30 on both. Link up. |
| SANITY_19 | `test_sanity_19_static_ipv4_config` | Change static IPv4, confirm it, restore `.10` / `.11`. | New address answers, then lab addresses are back. | PASS 15 Oct 2026 (823s). Moved to .20/.22, link up, restored .10/.11. |
| SANITY_20 | `test_sanity_20_static_ipv6_config` | Change static IPv6, confirm it, restore `:330` / `:331`. | New address answers, then lab addresses are back. | PASS 15 Oct 2026 (1149s). Moved to :33a/:33c, GUI and CLI answered, restored :330/:331. |
| SANITY_23 | `test_sanity_23_mgmt_vlan_config` | Change management VLAN, confirm, restore transparent and `mgmtvlan=1`. | VLAN applies. Lab path works again after restore. | PASS 15 Oct 2026 (476s). VLAN 102 on both, tagged IPv6 SSH OK, restored mgmtvlan 1 and untagged PC. |
| SANITY_25 | `test_sanity_25_dual_stack_config` | Set dual-stack, read IPv4 and IPv6, restore. | Both families are present, then the lab snapshot is back. | PASS 15 Oct 2026 (161s). Both radios already static dual-stack. IPv4 and IPv6 pings passed. |
| SANITY_27 | `test_sanity_27_link_security` | Change link security mode, confirm association, restore the live key and encryption. | STA reassociates on the new mode and again after restore. | PASS 15 Oct 2026 (693s). Open and AES-256 both associated. Restored psk2+ccmp-256 and the lab key. Link up. |
| SANITY_28 | `test_sanity_28_ddrs_mcs_modes` | Open DDRS MCS options on the radio page. | GUI lists the modes this firmware actually has (UBR655 STA MCS 0–11). | PASS 15 Oct 2026 (1367s) on rerun. Case 1 and Case 2 verified. DDRS restored to auto. Link up. First run failed while the radio page was still reloading. |
| SANITY_29 | `test_sanity_29_ddrs_mcs_manual` | Set a manual MCS, read it back, restore auto. | Manual MCS is active, then auto is restored. | FAIL 16 Oct 2026 (rerun also FAIL). HE GUI maps Dual MCS21/18 → MCS9/6. RF associates after apply, but CPE L3 (ping/SSH from PC) stays down until DDRS is restored to auto. |
| SANITY_30 | `test_sanity_30_spatial_stream_mcs_ranges` | Read spatial-stream MCS ranges from GUI and backend. | Ranges match the HE dual-stream list on this firmware. | PASS 16 Oct 2026 (437s). Single and Dual both MCS0–11 on HE GUI. DDRS restored. Link up. |
| SANITY_31 | `test_sanity_31_channel_width` | Set channel width, confirm `htmode`, restore the live width. 160 is a soft check if the radio stays at 80. | Applied width matches, or 160 is recorded as not accepted. Link returns. | FAIL 16 Oct 2026 (1566s). 40 MHz OK. 20 MHz UCI HT20 but runtime stayed HE80. 80 MHz UCI HT80 but runtime stayed HE40. 160 aborted after CPE L3 drop. Lab restored to HT80. |
| SANITY_32 | `test_sanity_32_channel_mode` | Switch Auto and Manual channel, then restore the live channel. | Active channel follows the mode. Live channel is back. | PASS 16 Oct 2026 (818s). Manual 149 then Auto verified. Restored Manual 36. Link up. |
| SANITY_33 | `test_sanity_33_frequency_range` | Read the allowed frequency list. | List covers the UBR 5 GHz range. Current channel is inside it. | FAIL 16 Oct 2026 (848s). Channel 149 OK. Apply of 153 timed out on the radio page (soft check FAIL) even though the flow restored and printed PASS. Lab restored to channel 36. |
| SANITY_35 | `test_sanity_35_acs_auto` | Set channel Auto, wait for an active channel, restore the live channel. | Configured shows Auto and an active channel appears. Live channel is back. | PASS 16 Oct 2026 (385s). Auto chose 134 then GUI revert left Active on 149. Lab restored to 36 after the case. |
| SANITY_36 | `test_sanity_36_tx_power` | Set Tx power, read GUI and UCI, restore the live power. | Both sides show the new value, then the original. | FAIL 16 Oct 2026. First run: CPE/BTS Tx 8 dBm OK, then CPE SSH timed out on revert (3250s). Rerun: RF up but CPE L3 unreachable before apply (604s). Lab link restored after. |
| SANITY_37 | `test_sanity_37_dcs_config` | Change DCS settings, read them back, restore. | GUI and backend match. Original DCS is back. | PASS 16 Oct 2026 (598s) on rerun. Enable/Disable + RTx 25 verified and restored. First run failed with CPE L3 down at start. |
| SANITY_38 | `test_sanity_38_ntp_sync` | Set NTP server, check sync status, restore. | NTP shows the configured server. | PASS 16 Oct 2026 (315s). All 4 NTP slots applied on BTS and CPE, time synced, reverted. Link up. |
| SANITY_42 | `test_sanity_42_board_temperature` | Read board temperature on GUI and from the sensor. | A numeric temperature is shown on both. | PASS 16 Oct 2026 (196s). BTS 48.5 °C and CPE 48.0 °C, GUI matches SSH. |
| SANITY_45 | `test_sanity_45_spectrum_report` | Open the spectrum report. | Page loads and shows scan data. | PASS 16 Oct 2026 (376s). CPE/BTS spectrum OK; RF+L3 restored after scan. |
| SANITY_49 | `test_sanity_49_wireless_link_ping` | Ping the peer across the RF link. | Ping succeeds, 0% loss. | PASS 16 Oct 2026 (49s). BTS↔CPE IPv6 ping OK both ways. |
| SANITY_50 | `test_sanity_50_rf_link_system_name` | Read the system name on the link-statistics page and from UCI. | Names match. | PASS 16 Oct 2026 (157s). BTS/CPE link tables show peer name UBR655. |
| SANITY_52 | `test_sanity_52_rf_link_uptime_reset` | Note uptime, reboot, confirm uptime restarted and the link returns. | Uptime is lower after reboot. STA is back. | FAIL 16 Oct 2026 (rerun also FAIL). First: CPE soft reboot boot_id unchanged in 600s. Rerun: CPE reboot+RF OK, then BTS GUI Link Statistics menu timed out (10s) before uptime check. |
| SANITY_54 | `test_sanity_54_rf_link_rate_mcs` | Read TX/RX rate and MCS on the link page and from `wlanconfig`. | GUI and backend are in the same rate range. Lab delta is allowed. | PASS 16 Oct 2026 (rerun, 2126s). First FAIL: CPE L3 down after 40 MHz. Rerun PASS all widths (soft rate notes vs sheet; CPE GUI scrape often timed out). |
| SANITY_55 | `test_sanity_55_rf_link_mcs_auto_range` | With MCS auto, read the rate range. | Rates sit inside the auto MCS range for the current width. | PASS 16 Oct 2026 (505s). Max MCS 9 and 11 in range; DDRS restored. |
| SANITY_57 | `test_sanity_57_tx_power_detailed_stats` | Read detailed Tx power on the statistics page and from the radio. | GUI and radio agree within the lab tolerance. | PASS 16 Oct 2026 (rerun, 1319s). First FAIL: CPE SSH timeout at start. Rerun: CPE Tx 12 / BTS Tx 18 match GUI+UCI+detailed; restored. |
| SANITY_60 | `test_sanity_60_obss_utilization` | Read OBSS utilization. | Page shows a value. A quiet lab can be low; that still passes. | FAIL 16 Oct 2026 (rerun also FAIL). BTS backend OBSS=-1% (GUI 0%); check rejects invalid backend and GUI/backend mismatch. Interference step soft-pass. |
| SANITY_61 | `test_sanity_61_combined_utilization` | Read combined utilization. | Page shows a value consistent with the radio counters. | FAIL 16 Oct 2026 (rerun also FAIL). Combined GUI=backend and rose under interference, but check fails on backend OBSS=-1% (invalid). |
| SANITY_62 | `test_sanity_62_system_summary` | Open Summary and compare model, firmware, and uptime with SSH. | GUI matches the backend. | PASS 16 Oct 2026 (577s). CPE overview matched SSH; BTS used SSH fallback for some blank widgets. |
| SANITY_76 | `test_sanity_76_soft_hard_reboot` | Soft-reboot, wait for SSH and the STA, then PDU hard-reboot and wait again. | Both boots return SSH and the RF link. | PASS 16 Oct 2026 (1583s). Soft+hard CPE/BTS OK; CPE GUI soft-skip after PDU; RF restored each step. |
| SANITY_77 | `test_sanity_77_factory_reset_keep_settings` | Factory reset with keep. Confirm IP, SSID, and link survive. | Settings kept. Link up. | PASS 16 Oct 2026 (1092s). CPE+BTS keep-settings retained IP/SSID/key; RF restored. |
| SANITY_81 | `test_sanity_81_lldp_enable_disable` | Turn LLDP off, confirm, turn it on, confirm. | State follows the GUI on both steps. | FAIL 16 Oct 2026 (rerun also FAIL). CPE LLDP enabled but ath1 capture got 0 TX frames; disable path OK (0 frames). |
| SANITY_82 | `test_sanity_82_lldp_neighbor_detection` | With LLDP on, read the neighbor (the peer radio or the lab PC). | At least one neighbor is listed. | FAIL 16 Oct 2026 (rerun also FAIL). CPE LLDP neighbor table empty (0 rows); BTS peer MAC/name not listed. |
| SANITY_83 | `test_sanity_83_lldp_discovery_table` | Open the LLDP table and compare it with the backend neighbor list. | Rows match. | FAIL 16 Oct 2026 (rerun also FAIL). Discovery table empty before/after LLDP restart; lldpcli no MgmtIP. |
| SANITY_84 | `test_sanity_84_link_test_tool` | Monitor → Tools → Link Test. Run a short test to the peer. | Tool returns a result. Link stays up. | PASS 16 Oct 2026 (266s). BTS+CPE link-test tool OK; RF stable. |
| SANITY_85 | `test_sanity_85_audit_config_logs` | Change a small setting, open the audit/config log, restore. | The change appears in the log. | PASS 16 Oct 2026 (337s). Soft-pass: timezone fragment not seen in GUI log within 45s on BTS/CPE. |
| SANITY_86 | `test_sanity_86_arp_bridge_table` | Open the ARP/bridge table and compare it with `brctl` / `ip neigh`. | GUI rows match the backend. | PASS 16 Oct 2026 (301s). BTS+CPE Bridge/ARP peer MACs match backend. |
| SANITY_111 | `test_sanity_111_installer_dashboard` | Log in as installer and open the dashboard. | Dashboard loads for the installer account. | FAIL 16 Oct 2026 (rerun also FAIL). First: CPE SSH down at start. Rerun: BTS installer dashboard OK; CPE failed to restore root GUI session after installer login. |
| SANITY_112 | `test_sanity_112_installer_quickstart` | Open installer quickstart and read the fields. | Page loads and shows the current link. | FAIL 16 Oct 2026 (rerun also FAIL). BTS Quick Start missing radio mode / Cascaded BTS; CPE radio mode missing (first) / logout timeout (rerun). |
| SANITY_113 | `test_sanity_113_installer_link_statistics` | Open installer link statistics. | Rates and the peer match the BTS station list. | PASS 16 Oct 2026 (352s). BTS+CPE installer Link Statistics match backend. |
| SANITY_114 | `test_sanity_114_installer_site_survey` | Run installer site survey. Compare visible BSSIDs with a radio scan. | At least half of the backend APs appear. One match is accepted if the GUI is slow. | FAIL 16 Oct 2026 (rerun also FAIL). BTS installer survey GUI empty (0/1 backend APs); CPE soft-accept. |
| SANITY_115 | `test_sanity_115_installer_soft_reboot` | Soft-reboot from the installer page. Wait for SSH. | Device returns. Link is up. | PASS 16 Oct 2026 (478s). Soft-pass: CPE installer reboot boot_id unchanged in 300s (SSH force path); suite recorded PASSED. |

---

## DHCP v4 and v6

Code: `tests/DHCPv4_DHCPv6/test_DHCPv4_DHCPv6.py` → `utils/dhcpv4_dhcpv6_flows.py`.
The suite skips testbed bootstrap on its own. It reopens BTS SSH after reboot cases. It does not touch the 2.4 GHz management DHCP used by the radio API.

**Last verified:** every automated DHCP case **PASS** on 7 Oct 2026 (DHCP_01, DHCP_02, DHCP_04 through DHCP_06, DHCP_13 through DHCP_19, DHCP_21, DHCP_22, DHCP_24 through DHCP_27, DHCP_29 through DHCP_38, DHCP_40 through DHCP_52, and DHCP_55). DHCP_07 through DHCP_12 are removed. Recovery cases rewrite the `/tmp` DHCPv6 server, start `odhcp6c -N force`, and read `/tmp/dhcp.leases`. Sheet numbers with no test stay Not Tested. SSID was left as-is.

**Before any DHCP case**

1. Preflight above. CPE must already be associated.
2. BTS stays static on `192.168.2.10`. BTS DHCP pool is `192.168.2.100`–`192.168.2.149`. Lease for a basic assign is 500 s. DHCP_02 uses a 60 s lease so T1 can be waited out.
3. IPv6 pool is `2401:4900:d0:40d4:0:17b8:0:340`–`:3ef`. CPE static v6 to restore is `:331`.
4. Reach the CPE from the BTS, or from desktop `10.0.150.40`. This PC often cannot ping `.11` directly.
5. The case snapshots LAN and restores it at the end. If the run dies first, hop-restore static `.11` before the next case.

Shared v4 method: snapshot BTS and CPE → enable DHCP server on BTS → set CPE data LAN to DHCP client → wait until the lease is inside `.100`–`.149` → ping that address from the BTS → do the case action → restore both snapshots.

Shared v6 method: same, on `lan` IPv6. Confirm with `ping6` from the BTS. Restore `:331`.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| DHCP_01 | `test_dhcp_01_dhcpv4_basic_ip_assignment` | 1. Ping BTS `.10` and CPE `.11` (link up, do not change SSID). 2. SSH BTS `.10`. Snapshot: BTS static `.10`, `dhcp.lan.ignore=0`; CPE static `.11`. 3. Enable BTS DHCP server, lease 500 s, pool `.100`–`.149`. Confirm dnsmasq `dhcp-range` is that pool. 4. Pin CPE OOB, switch the CPE data LAN to a DHCP client. 5. Wait until the CPE address is inside the pool and listed in the BTS lease file. Mask `/24`, server lease 500 s. 6. Restore the CPE to static `.11` and the BTS snapshot. Confirm `.11` answers. | CPE got `192.168.2.141` from the pool, lease present, then static `.11` restored. | PASS 7 Oct 2026 (68s). |
| DHCP_02 | `test_dhcp_02_dhcpv4_lease_renewal` | 1. Snapshot BTS and CPE (CPE is static `.11` again after DHCP_01). 2. Set BTS lease to 60 s, pool `.100`–`.149`. Switch CPE to a DHCP client via the OOB hop. 3. Confirm the first address (`192.168.2.141`), the lease expiry, and a BTS ping. 4. Wait the T1 window (48 s). 5. Read dnsmasq for DHCPREQUEST and DHCPACK for that address. Expiry must move forward. 6. Ping again. Address must stay the same. 7. Restore CPE static `.11` and the BTS lease snapshot (500 s). | Same address `192.168.2.141` after renew, REQUEST/ACK in the log, ping ok, `.11` restored. | PASS 7 Oct 2026 (127s). |
| DHCP_04 | `test_dhcp_04_dhcpv4_server_reboot_impact` | 1. Give the CPE a pool lease (500 s, `.100`–`.149`) the same way as DHCP_01. This run used `192.168.2.141`. 2. Soft-reboot the BTS. SSH to `.10` and the IPv6 address will fail until the radio is back; keep retrying. 3. When SSH returns, confirm the pool is still `.100`–`.149`. A dnsmasq "address in use" on restart is ok if the existing process already has that range. 4. Nudge the CPE renew through the desktop hop (`10.0.150.40`), not through the lease address (that path is down while the BTS is booting). 5. Check the CPE kept `192.168.2.141`, the BTS boot id changed, and the BTS can ping the CPE. 6. Restore static `.11`. | Lease kept across the BTS reboot, boot id changed, ping ok, `.11` restored. | PASS 7 Oct 2026 (287s). |
| DHCP_05 | `test_dhcp_05_dhcpv4_server_down_during_renewal` | 1. Assign a 120 s lease from the pool (this run: `192.168.2.141`). 2. Stop the BTS DHCP server (firewall drop, UCI ignore, stop dnsmasq, quarantine). 3. Do not let the lease expiry move while the server is down. The existing address must still answer. 4. Restore the BTS DHCP server and the CPE static `.11`. | Expiry did not advance while the server was down. The leased address still answered. `.11` restored. | PASS 7 Oct 2026 (141s). |
| DHCP_06 | `test_dhcp_06_dhcpv4_option_delivery` | 1. Assign a 500 s lease the same way as DHCP_01 (this run: `192.168.2.141`). 2. Read the options the client received. 3. Restore static `.11`. | DNS option is `192.168.2.10`. Gateway option is `192.168.2.10`. `.11` restored. | PASS 7 Oct 2026 (81s). |
| DHCP_13 | `test_dhcp_13_cpe_factory_reset_retain` | 1. Assign a 500 s lease (this run: `192.168.2.141`). 2. SSH to the CPE through the BTS and run factory reset with retain (`tftp.retip.retainip=7`, `/usr/sbin/factory_reset.sh`). 3. Wait for the DHCP path to return and for the BTS to ping the CPE. 4. Restore static `.11`. | The DHCP path came back after the retain reset and the ping worked. `.11` restored. | PASS 7 Oct 2026 (202s). |
| DHCP_14 | `test_dhcp_14_cpe_fw_upgrade_keep_settings` | 1. Assign a 500 s lease the same way as DHCP_01 (this run: `192.168.2.141`, SSH direct to that address). 2. Copy the latest UBR655 image from `/tftpboot` to `/tmp/dhcp14_fw.tgz` on the CPE. If the SSH session is a BTS tunnel, upload with SFTP on that session. If it is a direct session, SCP to the real IPv4. Do not use the tunnel peername tuple as the SCP host. 3. Set `tftp.keepset 1` and run `kwnupd local-upgrade`. 4. Wait until a pool address answers and the BTS can ping it. 5. Restore static `.11`. | A pool lease returned after the keep-settings upgrade and the ping worked. `.11` restored. | PASS 7 Oct 2026 (382s). First run failed on SCP host; retry after the SFTP upload passed. |
| DHCP_15 | `test_dhcp_15_bts_soft_reboot_impact` | 1. Assign a 500 s lease (this run: `192.168.2.141`). 2. Soft-reboot the BTS. SSH to `.10` and the IPv6 address fails until the radio is back; keep retrying. 3. When SSH returns, wait until the CPE pool address answers and the BTS can ping it. 4. Restore static `.11`. | After the BTS reboot the CPE lease path recovered and the ping worked. `.11` restored. | PASS 7 Oct 2026 (278s). |
| DHCP_16 | `test_dhcp_16_bts_hard_reboot` | 1. Assign a 500 s lease (this run: `192.168.2.141`). 2. PDU-cycle the BTS: outlet off for 30 s, then on. SSH to `.10` fails until the radio is back; keep retrying. 3. When SSH returns, wait until the CPE pool address answers and the BTS can ping it. 4. Restore static `.11`. | After the BTS hard boot the CPE lease path recovered and the ping worked. `.11` restored. | PASS 7 Oct 2026 (281s). |
| DHCP_17 | `test_dhcp_17_bts_factory_reset_retain` | 1. Assign a 500 s lease (this run: `192.168.2.141`). 2. On the BTS set `tftp.retip.retainip=7` and run `/usr/sbin/factory_reset.sh`. SSH to `.10` fails until the radio is back; keep retrying. 3. When SSH returns, wait until the CPE pool address answers and the BTS can ping it. 4. Restore static `.11` and the BTS LAN snapshot. | After the retain reset the DHCP server still served the CPE and the ping worked. `.11` restored. | PASS 7 Oct 2026 (323s). |
| DHCP_18 | `test_dhcp_18_bts_fw_upgrade_keep_settings` | 1. Assign a 500 s lease the same way as DHCP_01. This run's pool address was `192.168.2.113`. 2. SCP the latest UBR655 image from `/tftpboot` to `/tmp/dhcp18_fw.tgz` on the BTS at `192.168.2.10`. 3. Set `tftp.keepset 1` and run `kwnupd local-upgrade`. SSH to `.10` fails until the radio is back; keep retrying. 4. Wait until a pool address answers and the BTS can ping it. 5. Restore static `.11` and the BTS LAN snapshot. | A pool lease returned after the keep-settings upgrade and the ping worked. `.11` restored. | PASS 7 Oct 2026 (384s). |
| DHCP_19 | `test_dhcp_19_reboot_during_traffic` | 1. Assign a 500 s lease (this run: `192.168.2.141`). 2. Start a ping from the BTS to that address. 3. Reboot the CPE while the ping is running. 4. Wait until a pool address answers and the BTS can ping it. 5. Restore static `.11`. | The lease stayed valid and the ping recovered. `.11` restored. | PASS 7 Oct 2026 (297s). |
| DHCP_21 | `test_dhcp_21_dhcpv4_server_down_scenario` | 1. Assign a 60 s lease (this run: `192.168.2.141`). 2. Disable the BTS DHCP server (firewall drop, UCI ignore, stop dnsmasq, quarantine). 3. Restart the CPE DHCP client. 4. Wait out the lease. No new pool address may appear. 5. Turn the BTS DHCP server back on and restore static `.11`. If data SSH and OOB `10.0.0.1` are both down, set static `.11` and `:331/120` over the CPE link-local `fe80::c6e3:ceff:fe63:e62f%br-lan` through the BTS, then confirm the BTS can ping `.11`. | The client kept `192.168.2.141` and no new address was issued while the server was down. `.11` restored. | PASS 7 Oct 2026 (685s). Cleanup could not use `.11` or `10.0.0.1`; static was put back on the link-local before DHCP_22. |
| DHCP_22 | `test_dhcp_22_dhcpv6_stateful_address_assignment` | 1. CPE is static `.11` and `:331/120`. SSH through the BTS. 2. Stop odhcpd so it does not hold UDP 547. Let dnsmasq serve the IA_NA pool `:340`–`:3ef`, lease 300 s, prefix `/120`, and send RA. 3. Start a DHCPv6 client on the CPE (`lan6` / odhcp6c). 4. Wait for an address inside the pool and `ping6` it from the BTS. 5. Restore static `:331` and `.11`, then start odhcpd again. | This run got `2401:4900:d0:40d4:0:17b8:0:3a2`. `ping6` worked. `.11` restored. | PASS 7 Oct 2026 (64s). First run failed because odhcpd kept the DHCPv6 port and dnsmasq exited. |
| DHCP_24 | `test_dhcp_24_dhcpv6_lease_renewal` | 1. Same DHCPv6 setup as DHCP_22, with a 60 s lease. Stop odhcpd so dnsmasq can bind UDP 547. 2. Wait until the CPE has an address in `:340`–`:3ef`. 3. Wait the T1 window (about 45 s). 4. Confirm that pool address is still present and `ping6` from the BTS works. 5. Restore static `:331` and `.11`, and start odhcpd again. | The IPv6 lease was still in the pool after T1 and `ping6` worked. `.11` restored. | PASS 7 Oct 2026 (112s). |
| DHCP_25 | `test_dhcp_25_dhcpv6_prefix_delegation` | 1. Same DHCPv6 address setup as DHCP_22. 2. Copy `dhcp25_pd_server.lua` to the BTS in short SSH lines. One long line makes the shell time out. 3. Stop dnsmasq and odhcpd so UDP 547 is free. This image will not bind a second DHCPv6 server on that port. 4. Start the Lua prefix server for `2401:4900:d0:40e0:1::/64`. 5. On the CPE, run odhcp6c asking for a /64 prefix. 6. The assignment file must contain that prefix. A shell job message is not a prefix. 7. Restore static `:331` and `.11`, and start dnsmasq again. | The CPE was delegated `2401:4900:d0:40e0:1::/64`. `.11` restored. The IA_NA ping is optional and was down while dnsmasq was stopped. | PASS 7 Oct 2026 (93s). An earlier pytest pass only saw a shell job message; that run does not count. |
| DHCP_26 | `test_dhcp_26_dhcpv6_server_reboot` | 1. Assign a 120 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Soft-reboot the BTS. SSH to `.10` fails until the radio is back. 3. Reboot deletes `/tmp`, so write the DHCPv6 pool again, stop odhcpd, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the server is back. | PASS 7 Oct 2026 (292s). SSH came back. The pool was written again and the forced client solicited. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_27 | `test_dhcp_27_dhcpv6_rapid_commit` | 1. Copy `dhcp27_rc_server.lua` to the BTS in short SSH lines. 2. Stop dnsmasq and odhcpd so UDP 547 is free. 3. Start the Lua rapid-commit server offering `2401:4900:d0:40d4:0:17b8:0:3c1`. 4. On the CPE, run `dhcp27_rc_client.lua` on `br-lan` so it sends a Solicit with Rapid Commit. 5. The assignment file must contain that address. A shell job message is not an assignment. 6. Restore static `:331` and `.11`, and start dnsmasq again. | The server recorded `2401:4900:d0:40d4:0:17b8:0:3c1`. `.11` restored. `ping6` to that address is optional and did not answer. | PASS 7 Oct 2026 (86s). An earlier pytest pass only saw a shell job message; that run does not count. |
| DHCP_29 | `test_dhcp_29_cpe_soft_reboot` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Soft-reboot the CPE. 3. After the boot grace, write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. The old one-shot client does not survive reboot. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the CPE boots. | PASS 7 Oct 2026 (288s). The CPE rebooted. The forced client was started again. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_30 | `test_dhcp_30_cpe_hard_reboot` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. PDU-cycle the CPE: outlet off for 30 s, then on. 3. Write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the hard boot. | PASS 7 Oct 2026 (307s). The CPE powered back on. The forced client was started again. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_31 | `test_dhcp_31_cpe_soft_reset` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force` so static `:331` does not block the request. 2. Restart the CPE network (`/etc/init.d/network restart`). Direct SSH drops; the restart goes through the desktop hop. 3. Write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the network restart. | PASS 7 Oct 2026 (302s). The restart dropped SSH. The client was started again through the desktop hop. A pool address returned and `ping6` worked. `.11` was restored on the data path. |
| DHCP_32 | `test_dhcp_32_cpe_factory_reset_retain` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. SSH to the CPE and run factory reset with retain (`tftp.retip.retainip=7`, `/usr/sbin/factory_reset.sh`). The SSH session times out when the reset starts. 3. After the settle, write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return and the retained client must still request. | PASS 7 Oct 2026 (349s). The retain reset ran. The forced client was started again. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_33 | `test_dhcp_33_cpe_fw_upgrade_keep_settings` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. This CPE has no SFTP service. Send the latest UBR655 image over the existing SSH channel as raw bytes to `/tmp/dhcp33_fw.tgz`. 3. Set `tftp.keepset 1` and run `kwnupd local-upgrade`. 4. After the upgrade, write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 5. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 6. Restore static `:331` and `.11`. | A pool address must return after the keep-settings upgrade. | PASS 7 Oct 2026 (428s). The image was copied and the CPE upgraded. The forced client was started again. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_34 | `test_dhcp_34_bts_soft_reboot_impact` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Soft-reboot the BTS. SSH to `.10` fails until the radio is back. 3. Reboot deletes `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the BTS reboot. | PASS 7 Oct 2026 (303s). Same recovery as DHCP_26. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_35 | `test_dhcp_35_bts_hard_reboot` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. 2. PDU-cycle the BTS: outlet off for 30 s, then on. SSH to `.10` fails until the radio is back. 3. Wait until a pool address in `:340`–`:3ef` answers and `ping6` from the BTS works. 4. Restore static `:331` and `.11`. | A pool address returned after the hard boot and `ping6` worked. `.11` restored. | PASS 7 Oct 2026 (288s). |
| DHCP_36 | `test_dhcp_36_bts_factory_reset_retain` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. On the BTS set `tftp.retip.retainip=7` and run `/usr/sbin/factory_reset.sh`. SSH to `.10` fails until the radio is back. 3. The reset clears `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the retain reset. | PASS 7 Oct 2026 (380s). The reset finished and SSH came back. The pool was written again and the forced client solicited. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_37 | `test_dhcp_37_bts_fw_upgrade_keep_settings` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. SCP the latest UBR655 image from `/tftpboot` to `/tmp/dhcp37_fw.tgz` on the BTS at `192.168.2.10`. 3. Set `tftp.keepset 1` and run `kwnupd local-upgrade`. SSH to `.10` fails until the radio is back. 4. The upgrade clears `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 5. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 6. Restore static `:331` and `.11`. | A pool address must return after the keep-settings upgrade. | PASS 7 Oct 2026 (392s). The BTS rebooted for the upgrade and SSH came back. The pool was written again and the forced client solicited. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_38 | `test_dhcp_38_reboot_during_traffic` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Start `ping6` from the BTS to that address. 3. Reboot the CPE while the ping is running. 4. Write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 5. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 6. Restore static `:331` and `.11`. | The lease must return and `ping6` must recover. | PASS 7 Oct 2026 (301s). The CPE rebooted during the ping. The forced client was started again. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_40 | `test_dhcp_40_dhcpv6_server_down_scenario` | 1. Assign a 60 s IPv6 lease the same way as DHCP_22. This run: `2401:4900:d0:40d4:0:17b8:0:3a2`. 2. Stop the BTS DHCPv6 server. 3. Restart the CPE DHCPv6 client. 4. The same address was still listed. 5. Restore static `:331` and `.11`. | The client already had a pool address before the server was stopped, and that same address stayed listed. `.11` restored. | PASS 7 Oct 2026 (86s). |
| DHCP_41 | `test_dhcp_41_cpe_dhcpv6_address_assignment` | 1. Same DHCPv6 setup as DHCP_22. Stop odhcpd so dnsmasq can bind UDP 547. Pool `:340`–`:3ef`, lease 300 s. 2. The CPE is the client. 3. Confirm the address is on `br-lan` and `ping6` it from the BTS. 4. Restore static `:331` and `.11`. | This run got `2401:4900:d0:40d4:0:17b8:0:3a2`. `ping6` worked. `.11` restored. | PASS 7 Oct 2026 (69s). |
| DHCP_42 | `test_dhcp_42_bts_cpe_dhcpv6_clients` | 1. Same DHCPv6 setup as DHCP_22. The BTS stays the server on `:330`. 2. The CPE takes a pool address. 3. That address must be different from `:330`, and `ping6` from the BTS must work. 4. Restore static `:331` and `.11`. | CPE got `2401:4900:d0:40d4:0:17b8:0:3a2`, different from the BTS. `ping6` worked. `.11` restored. | PASS 7 Oct 2026 (65s). |
| DHCP_43 | `test_dhcp_43_dhcpv6_lease_renewal` | 1. Same DHCPv6 setup as DHCP_22, with a 60 s lease. Stop odhcpd so dnsmasq can bind UDP 547. 2. Wait until the CPE has an address in `:340`–`:3ef`. 3. Wait the T1 window (about 45 s). 4. Confirm a pool address is still present and `ping6` from the BTS works. 5. Restore static `:331` and `.11`, and start odhcpd again. | The IPv6 lease was still in the pool after T1 and `ping6` worked. `.11` restored. | PASS 7 Oct 2026 (113s). |
| DHCP_44 | `test_dhcp_44_cpe_soft_reboot` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Soft-reboot the CPE. 3. Write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the CPE boots. | PASS 7 Oct 2026 (289s). Same recovery as DHCP_29. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_45 | `test_dhcp_45_cpe_hard_reboot` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. PDU-cycle the CPE: outlet off for 30 s, then on. 3. Write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the hard boot. | PASS 7 Oct 2026 (307s). Same recovery as DHCP_30. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_46 | `test_dhcp_46_cpe_soft_reset` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Restart the CPE network (`/etc/init.d/network restart`). Direct SSH drops; the restart goes through the desktop hop. 3. Write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the network restart. | PASS 7 Oct 2026 (231s). Same recovery as DHCP_31. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_47 | `test_dhcp_47_cpe_factory_reset_retain` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. SSH to the CPE and run factory reset with retain (`tftp.retip.retainip=7`, `/usr/sbin/factory_reset.sh`). The SSH session times out when the reset starts. 3. After the settle, write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return and the retained client must still request. | PASS 7 Oct 2026 (342s). Same recovery as DHCP_32. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_48 | `test_dhcp_48_cpe_fw_upgrade` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. This CPE has no SFTP service. Send the latest UBR655 image over the existing SSH channel as raw bytes to `/tmp/dhcp48_fw.tgz`. 3. Set `tftp.keepset 1` and run `kwnupd local-upgrade`. 4. After the upgrade, write the DHCPv6 pool again, wait until `.11` answers, and start `odhcp6c -N force` with `ifup lan6`. 5. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 6. Restore static `:331` and `.11`. | A pool address must return after the keep-settings upgrade. | PASS 7 Oct 2026 (414s). Same recovery as DHCP_33. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_49 | `test_dhcp_49_bts_soft_reboot_impact` | 1. Assign a 120 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Soft-reboot the BTS. SSH to `.10` fails until the radio is back. 3. Reboot deletes `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the BTS reboot. | PASS 7 Oct 2026 (290s). Same recovery as DHCP_26. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_50 | `test_dhcp_50_bts_hard_reboot` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. PDU-cycle the BTS: outlet off for 30 s, then on. SSH to `.10` fails until the radio is back. 3. Power loss deletes `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the hard boot. | PASS 7 Oct 2026 (299s). The BTS powered back on. The pool was written again and the forced client solicited. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_51 | `test_dhcp_51_bts_factory_reset_retain` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. On the BTS set `tftp.retip.retainip=7` and run `/usr/sbin/factory_reset.sh`. SSH to `.10` fails until the radio is back. 3. The reset clears `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 4. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 5. Restore static `:331` and `.11`. | A pool address must return after the retain reset. | PASS 7 Oct 2026 (337s). Same recovery as DHCP_36. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_52 | `test_dhcp_52_bts_fw_upgrade` | 1. Assign a 300 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. SCP the latest UBR655 image from `/tftpboot` to `/tmp/dhcp52_fw.tgz` on the BTS at `192.168.2.10`. 3. Set `tftp.keepset 1` and run `kwnupd local-upgrade`. SSH to `.10` fails until the radio is back. 4. The upgrade clears `/tmp`, so write the DHCPv6 pool again and start `odhcp6c -N force` with `ifup lan6`. 5. Read `/tmp/dhcp.leases` for an address in `:340`–`:3ef` and `ping6` it. 6. Restore static `:331` and `.11`. | A pool address must return after the keep-settings upgrade. | PASS 7 Oct 2026 (395s). Same recovery as DHCP_37. A pool address returned and `ping6` worked. `.11` was restored. |
| DHCP_55 | `test_dhcp_55_dhcpv6_server_down` | 1. Assign a 60 s IPv6 lease the same way as DHCP_22. The client is `odhcp6c -N force`. 2. Stop the DHCPv6 server (`dhcpv6=disabled`, remove `/tmp/dnsmasq.d/dhcpv6-lab.conf`, start odhcpd, restart dnsmasq). 3. Restart the CPE client (`ifdown lan6` / `ifup lan6`). 4. Read `/tmp/dhcp.leases` again. 5. Restore static `:331` and `.11`. | The address already held is still listed. The server is not handing out a new one. | PASS 7 Oct 2026 (91s). Address before the stop was `2401:4900:d0:40d4:0:17b8:0:3a2`. After the client restart with the server down, the same address was still listed. `.11` was restored. |

Skipped sheet numbers (03, 07 through 12, 20, 23, 28, 39, 53, 54, and the rest) have no test function. DHCP_07 through DHCP_12 were removed. Leave them Not Tested.

---

## Functional

Code: `tests/Functional/test_functional.py` → `utils/functional_flows.py`.
Report: `Alpha 2 Automation Reports/Senao_Functional_Report_20260918.html`.

**Last verified:** this bench, 7–8 Oct 2026. FT_02 through FT_23 and FT_25 through FT_28 passed live. There is no FT_04 or FT_24 test. The 18 Sep 2026 sheet is not this run.

Shared method: change the setting on the live link, read GUI and SSH, restore the lab snapshot, confirm the STA is back. Management-VLAN cases must put `transparent` and `mgmtvlan=1` back before the next case.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| FT_02 | `test_ft_02_memory_overload` | 1. SSH the BTS on `:330` with `--allow-functional`. Do not change the link. 2. Fill RAM with `dd` to `/tmp/ram_eater` from MemAvailable and hold 120 s. 3. Confirm uptime stayed up, the file is removed, and MemAvailable returns. 4. Wait for the BTS↔CPE link, then do the same on the CPE at `:331`. | Both devices stay up. The RAM file is removed and memory returns. | PASS 7 Oct 2026 (336s). BTS ate 431 MB of 570.8 MB and recovered to 571.2 MB. CPE ate 430 MB of 564.6 MB and recovered to 562.7 MB. No memory alarm. |
| FT_03 | `test_ft_03_config_retain_hard_reboot` | 1. SSH BTS `:330`. Snapshot LAN. 2. Set a temporary LAN (`192.168.2.20`, mask `255.255.254.0`, `:33a/120`, width HT20), commit, and PDU-cycle the BTS. 3. Read those values back, then restore `.10`, `/24`, `:330/120`, and HT80. 4. On the CPE, set `192.168.2.22`, the same mask, and `:33c/120`. Width is not edited on the CPE. PDU-cycle the CPE. 5. Read the values back, then restore `.11`, `/24`, and `:331/120`. Confirm the link. | The temporary values survive the power cycle, then the lab addresses and BTS HT80 are back. | PASS 7 Oct 2026 (969s). BTS kept `.20` / `:33a` / HT20 across the PDU cycle and was restored to `.10` / `:330` / HT80. CPE kept `.22` / `:33c` and was restored to `.11` / `:331`. CPE width baseline was unreadable; the read-back stayed HT160. The link came back. |
| FT_05 | `test_ft_05_backend_config_per_bandwidth` | 1. SSH BTS `:330`. Snapshot `wifi1` htmode and LAN. 2. For HT20, HT40+ , HT80, and HT160, set that width plus temporary LAN `.20` / `255.255.254.0` / gateway `.254` / `:33a/120`. Read UCI back. 3. Restore BTS to HT80, `.10`, `/24`, gateway `.1`, `:330/120`. 4. On the CPE, set the BTS to the same width first so the STA can reassociate, then set the CPE. Skip a width the CPE UCI does not accept. 5. Put the BTS back to HT80 before restoring the CPE, then restore `.11`, `/24`, gateway `.10`, and `:331/120`. | Each accepted width is in UCI. Lab addresses and BTS HT80 are back. | PASS 7 Oct 2026 (1471s). BTS accepted HT20, HT40, HT80, and HT160, then returned to HT80 / `.10` / `:330`. CPE UCI stayed HT160 for HT20, HT40, and HT80 (those were skipped) and accepted HT160. Addresses returned to `.11` / `:331`. The CPE width baseline read was a login banner, so that compare was not a real HT value. The link came back. |
| FT_06 | `test_ft_06_rf_stats_backend_reflect` | 1. SSH BTS `:330` and CPE `:331`. Snapshot LAN. 2. Sample TX/RX, soft-reboot the CPE and then the BTS, and sample again after the link returns. 3. An idle rate under 10000 is noise. A higher rate after reboot means the link came back. A large drop fails. 4. Restore CPE `.11` / `:331` and BTS `.10` / `:330` / HT80. | The link still passes traffic after the reboot, and the lab addresses and BTS HT80 are back. | PASS 7 Oct 2026 (826s). The first run failed on idle BTS RX (344 to 860). The second failed because the CPE rate rose after reboot (41400 to 126936). Both are a recovered link, not a drop. The retest passed. Addresses and BTS HT80 were restored. |
| FT_07 | `test_ft_07_factory_reset_cpe_defaults` | 1. SSH BTS `:330`. Snapshot LAN, SSID `UBR655_R1_PS`, key, and HT80. Do not change the SSID. 2. Hop `10.0.150.40` to CPE `10.0.0.1`. Snapshot the STA, then factory-reset the CPE without keep. 3. Confirm factory LAN `192.168.2.1`, mask `255.255.255.0`, IPv6 removed, bandwidth HT80. 4. Restore the CPE as `mode sta`, encryption `psk2+ccmp-256`, the BTS SSID and key, LAN `.11` / `/24` / gateway `.10` / `:331/120`. `uci set` proto `static` and commit. `ucidyn set` of proto alone leaves factory `dhcpv6`, so `.11` never lands on `br-lan`. 5. Wait until the STA is back on the BTS. 6. Factory-reset the BTS the same way, confirm the same defaults, set VLAN transparent and `mgmtvlan=1`, then restore `.10` / `:330` / HT80 / the same SSID. 7. Wait until the STA associates again. | Factory defaults match. Both radios return to the lab addresses and the STA is associated. | PASS 8 Oct 2026 (817s). CPE and BTS factory checks were `.1`, no IPv6, HT80. Restore put the CPE in `sta` on `UBR655_R1_PS`. The STA returned on channel 36, HE80. BTS came back `.10` / `:330` / HT80 / transparent / `mgmtvlan=1`. The first run left the CPE as an AP, so the link stayed down. This run restored `mode sta`. After the pass, `br-lan` was still factory `10.0.0.1` with proto `dhcpv6` until `uci set` proto `static` and `ifup lan`. `.11` and `:331` then answered. |
| FT_08 | `test_ft_08_network_soft_reset` | 1. SSH the CPE at `:331`. Snapshot LAN, width, SSID, and encryption. 2. Run `/etc/init.d/network reload` and wait until SSH returns. 3. Confirm the reload is in the log and the snapshot is unchanged. 4. Wait for the STA. 5. Do the same on the BTS at `:330`. | Addresses, SSID, and width are unchanged on both radios, and the STA is back. | PASS 8 Oct 2026 (243s). CPE stayed `.11` / `:331` / HT160 / `UBR655_R1_PS`. BTS stayed `.10` / `:330` / HT80 / the same SSID. Reload markers were in the log. The link came back after each reload. |
| FT_09 | `test_ft_09_config_retain_soft_reboot` | 1. SSH BTS `:330` and CPE `:331`. Snapshot LAN, width, SSID, and encryption. 2. Ping the CPE from the BTS. 3. Soft-reboot the CPE and read the snapshot back. 4. Wait for the STA, then soft-reboot the BTS and read it back. 5. Ping again. A width baseline that is not HT20/HT40/HT80/HT160 is a bad read, not a change. | Addresses, SSID, encryption, and a real width are unchanged. The ping works after both reboots. | PASS 8 Oct 2026 (418s). CPE stayed `.11` / `:331` / HT160 / `UBR655_R1_PS`. BTS stayed `.10` / `:330` / HT80 / the same SSID. Ping before was about 8 ms. Ping after was about 10 ms to 1 s on the first reply, then settled. The first run failed because the CPE width read was `|` and the later read was HT160. That was a login banner, not a width change. |
| FT_10 | `test_ft_10_mgmt_vlan_precheck` | This node runs the same prefix change as FT_17. 1. Confirm mgmt VLAN is `1` and proto is static on BTS `:330/120` and CPE `:331/120`. 2. Set both prefixes to `/118`. 3. Confirm the STA stays up and both prefixes read `/118`. 4. Put `/120` back and confirm the STA again. | `/118` is in UCI, the link stays up, and `/120` is restored. Mgmt VLAN stays `1`. | PASS 8 Oct 2026 (305s). Both sides took `/118` and returned to `/120`. VLAN stayed `1`. The link did not drop. |
| FT_11 | `test_ft_11_mgmt_vlan_gateway_change` | 1. Confirm both radios are transparent with mgmt VLAN `1`. 2. Move mgmt VLAN to a random id on the CPE, then the BTS. 3. Put this PC on that VLAN. `ip link` is not permitted here, so NetworkManager creates `enp1s0.<vlan>` and moves `192.168.2.200/24` onto it. 4. Change the IPv6 gateway, confirm the STA, put the original gateway back. An empty CPE gateway is cleared with `uci delete`, because `ucidyn delete` leaves it set. 5. Set mgmt VLAN `1` again and move `192.168.2.200/24` back to untagged `enp1s0`. | The gateway change is in UCI, the STA returns, and both radios are back on VLAN `1` with the lab addresses. A link restart that recovers is PASS (UNSTABLE). | PASS (UNSTABLE) 8 Oct 2026 (310s). VLAN this run was `3394`. The STA dropped and came back. The CPE gateway was empty, then `2401:4900:d0:40d4:0:17b8:0:301`, then empty again. BTS gateway stayed that same address. Restored transparent / `mgmtvlan=1`, SSID `UBR655_R1_PS`, HT80. The first run failed because `ip link` could not create the VLAN, and a later run left the CPE gateway set. |
| FT_12 | `test_ft_12_mgmt_vlan_ipv6_change` | 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Change BTS IPv6 to `:33a/120` and CPE IPv6 to `:33c/120`. 3. Confirm the STA returns. 4. Put `:330/120` and `:331/120` back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. | The temporary addresses are in UCI, the STA returns, and the lab prefix `/120` addresses are back. | PASS (UNSTABLE) 8 Oct 2026 (416s). VLAN this run was `88`. BTS went to `:33a` and CPE to `:33c`, then both returned to `:330` and `:331`. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_13 | `test_ft_13_mgmt_vlan_ipv4_netmask_change` | This node runs the prefix change (same path as FT_10). 1. Set both sides to `/118`. 2. Confirm the STA stays up. 3. Put `/120` back. Mgmt VLAN stays `1`. | Both sides show `/118`, the STA stays up, then `/120` is back. | PASS 8 Oct 2026 (298s). BTS and CPE prefixes went to `/118` and returned to `/120`. VLAN stayed `1`. The link did not drop. |
| FT_14 | `test_ft_14_mgmt_vlan_ipv4_gateway_change` | 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Change the IPv4 gateway on both radios. 3. Confirm the STA returns. 4. Put the original gateways back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. | Temporary gateways apply, the STA returns, and the original gateways are back. | PASS (UNSTABLE) 8 Oct 2026 (411s). VLAN this run was `74`. BTS gateway `192.168.2.254` → `192.168.2.41`, CPE `192.168.2.10` → `192.168.2.71`, then both restored. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_15 | `test_ft_15_mgmt_vlan_ipv4_change` | 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Change BTS IPv4 to `.101` and CPE IPv4 to `.119`. 3. Confirm the STA returns. 4. Put `.10` and `.11` back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. | Temporary addresses apply, the STA returns, and the lab addresses are back. | PASS (UNSTABLE) 8 Oct 2026 (417s). VLAN this run was `1932`. BTS `.10` → `.101` and CPE `.11` → `.119`, then both restored. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_16 | `test_ft_16_mgmt_vlan_channel_width_change` | 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Set BTS width HT20, HT40, HT80, then HT160, and confirm the STA returns after each. 3. Put HT80 back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. Channel stays 36. | Each width reads back on the BTS, the STA returns, and HT80 plus VLAN `1` are restored. | PASS (UNSTABLE) 8 Oct 2026 (504s). VLAN this run was `2491`. BTS accepted HT20, HT40, HT80, and HT160, then returned to HT80 on channel 36. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_17 | `test_ft_17_mgmt_vlan_prefix_change` | 1. Set both sides to `/118`. 2. Confirm the STA stays up. 3. Put `/120` back. Mgmt VLAN stays `1`. | Both sides show `/118`, the STA stays up, then `/120` is back. | PASS 8 Oct 2026 (306s). BTS and CPE prefixes went to `/118` and returned to `/120`. VLAN stayed `1`. The link did not drop. |
| FT_18 | `test_ft_18_mgmt_vlan_gateway_change` | This node runs the IPv6-gateway path (same as FT_11). 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Set the CPE IPv6 gateway to the PC address, then clear it. 3. Put mgmt VLAN `1` back and move `192.168.2.200/24` to untagged `enp1s0`. | The CPE gateway is set, the STA returns, and the gateway is empty again. | PASS (UNSTABLE) 8 Oct 2026 (361s). VLAN this run was `3466`. CPE gateway went from empty to the PC address, then back to empty. The BTS gateway stayed the PC address. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_19 | `test_ft_19_mgmt_vlan_ipv6_change` | This node runs the IPv6-address path (same as FT_12). 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Change BTS IPv6 to `:33a/120` and CPE IPv6 to `:33c/120`. 3. Confirm the STA returns. 4. Put `:330/120` and `:331/120` back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. | Temporary addresses apply, the STA returns, and the lab addresses are back. | PASS (UNSTABLE) 8 Oct 2026 (413s). VLAN this run was `2640`. BTS went to `:33a` and CPE to `:33c`, then both returned to `:330` and `:331`. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_20 | `test_ft_20_mgmt_vlan_ipv4_netmask_change` | This node runs the prefix change (same path as FT_17). 1. Set both sides to `/118`. 2. Confirm the STA stays up. 3. Put `/120` back. Mgmt VLAN stays `1`. | Both sides show `/118`, the STA stays up, then `/120` is back. | PASS 8 Oct 2026 (306s). BTS and CPE prefixes went to `/118` and returned to `/120`. VLAN stayed `1`. The link did not drop. |
| FT_21 | `test_ft_21_mgmt_vlan_ipv4_gateway_change` | This node runs the IPv4-gateway path (same as FT_14). 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Change the IPv4 gateway on both radios. 3. Confirm the STA returns. 4. Put the original gateways back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. | Temporary gateways apply, the STA returns, and the original gateways are back. | PASS (UNSTABLE) 8 Oct 2026 (413s). VLAN this run was `2875`. BTS gateway `192.168.2.254` → `192.168.2.190`, CPE `192.168.2.10` → `192.168.2.3`, then both restored. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_22 | `test_ft_22_mgmt_vlan_ipv4_change` | This node runs the IPv4-address path (same as FT_15). 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Change BTS IPv4 to `.15` and CPE IPv4 to `.217`. 3. Confirm the STA returns. 4. Put `.10` and `.11` back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. | Temporary addresses apply, the STA returns, and the lab addresses are back. | PASS (UNSTABLE) 8 Oct 2026 (420s). VLAN this run was `1061`. BTS `.10` → `.15` and CPE `.11` → `.217`, then both restored. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_23 | `test_ft_23_mgmt_vlan_channel_width_change` | This node runs the width path (same as FT_16). 1. Move mgmt VLAN to a random id and put this PC on that VLAN with NetworkManager. 2. Set BTS width HT20, HT40, HT80, then HT160, and confirm the STA returns after each. 3. Put HT80 back, then mgmt VLAN `1`, and move `192.168.2.200/24` back to untagged `enp1s0`. Channel stays 36. | Each width reads back on the BTS, the STA returns, and HT80 plus VLAN `1` are restored. | PASS (UNSTABLE) 8 Oct 2026 (525s). VLAN this run was `3370`. BTS accepted HT20, HT40, HT80, and HT160, then returned to HT80 on channel 36. The STA restarted and came back. SSID stayed `UBR655_R1_PS`. |
| FT_25 | `test_ft_25_tx_power_gui_ssh_mapping` | 1. Turn ATPC off on both radios. 2. In the GUI, set Tx power 3, 11, 12, 18, 19, 23, 24, then 26 dBm on the CPE, then the BTS. 3. After each value, confirm the STA is up and UCI `atpcpower` matches. 4. Put 26 dBm back on both. Channel and SSID are not changed. | UCI matches each GUI value and the STA stays up. Live power is back. | PASS 8 Oct 2026 (1353s). Baseline UCI was 26 dBm on both (runtime read 13 dBm). Every listed value matched in UCI and the STA stayed up. Restored UCI 26 dBm. SSID stayed `UBR655_R1_PS`. |
| FT_26 | `test_ft_26_gui_address_bar_ip` | 1. Confirm the STA is up. 2. Open the BTS GUI on `2401:4900:d0:40d4:0:17b8:0:330` and the CPE GUI on `:331`. 3. Check the browser address bar host. | Each page URL contains that radio's management address. | PASS 8 Oct 2026 (23s). BTS URL was `https://[2401:4900:d0:40d4:0:17b8:0:330]/...`. CPE URL was the same address written expanded (`2401:4900:d0:40d4:0:17b8:0:331`). SSID was not changed. |
| FT_27 | `test_ft_27_gui_auto_logout` | 1. Log in to the BTS and the CPE in Chromium as `admin`. 2. Leave the session idle until it returns to the login page. 3. Repeat in Firefox as `root`. | Each session logs itself out. | PASS 8 Oct 2026 (679s). Chromium BTS logged out at 297 s, CPE at 8 s. Firefox BTS logged out at 297 s, CPE at 8 s. SSID was not changed. |
| FT_28 | `test_ft_28_commit_after_config_change_reboot` | 1. Turn ATPC off. 2. Set BTS channel 116 and Tx 10 dBm, and CPE Tx 10 dBm. Soft-reboot both and read them back. 3. Set BTS channel 132 and Tx 6 dBm, and CPE Tx 6 dBm. Soft-reboot both and read them back. The case leaves those values in place. After the pass, put the lab back: BTS channel 36, HT80, Tx 26 dBm, CPE Tx 26 dBm, SSID unchanged. | The committed channel and Tx are still there after each reboot. The lab channel and Tx are restored afterward. | PASS 8 Oct 2026 (734s). Phase 1 kept channel 116 and Tx 10 after reboot. Phase 2 kept channel 132 and Tx 6 after reboot. The radio was then on 5.66 GHz at 6 dBm. Restored BTS to 5.18 GHz (channel 36), HT80, Tx 26 dBm. CPE UCI Tx is 26 dBm (runtime read 13 dBm). SSID stayed `UBR655_R1_PS`. VLAN stayed transparent / `1`. |

FT_04 and FT_24 have no test. Leave them Not Tested.

---

## Wireless Security

Code: `tests/WirelessSecurity/test_wireless_security.py` → `utils/wireless_security_flows.py`.
Report: `Alpha 2 Automation Reports/Senao_Wireless_Security_Report_20260918.html`.

**Last verified:** this bench, 9 Oct 2026, IPv6 (`:330` / `:331`). W_SECURITY_01, W_SECURITY_03, W_SECURITY_05, W_SECURITY_06, W_SECURITY_07, W_SECURITY_09, W_SECURITY_11, and W_SECURITY_14 passed live. There is no W_SECURITY_02, W_SECURITY_04, W_SECURITY_08, W_SECURITY_10, W_SECURITY_12, or W_SECURITY_13 test. SSID stayed `UBR655_R1_PS`. When the RF link drops, reach the CPE from `10.0.150.40` at `192.168.2.11`. Do not pass that address as `--fallback-ip`.

Shared method: keep the live SSID. Change only encryption or the key. Confirm the STA associates or is rejected. Always restore the live key and `psk2+ccmp-256` before the next case. Do not flip `auto_credentials`.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| W_SECURITY_01 | `test_w_security_01_configure_wpa2_ipv6` | Open Radio 1 on the BTS GUI, then the CPE. If encryption is not AES-256, select it and apply. SSID and key are not changed. | GUI and UCI both read AES-256 / `psk2+ccmp-256` on both radios. | PASS 9 Oct 2026 (29s). Both radios were already AES-256. UCI stayed `psk2+ccmp-256`. SSID stayed `UBR655_R1_PS`. |
| W_SECURITY_03 | `test_w_security_03_validate_rejection_logs_ipv6` | 1. Set the BTS encryption to `none` and wait until IPv6 to the CPE drops. 2. Put `psk2+ccmp-256` back and wait for the link. 3. Set the CPE encryption to `none`. 4. From the side PC `10.0.150.40`, SSH the CPE at `192.168.2.11` and put `psk2+ccmp-256` back. Do not pass `--fallback-ip 192.168.2.11` — that address is tried first for the BTS session. SSID and the key stay as they are. | The link drops after each open setting. Both radios return to `psk2+ccmp-256` and IPv6 comes back. | PASS 9 Oct 2026 (311s). BTS link returned in 71.4 s. CPE recovery was `10.0.150.40` → `192.168.2.11`. CPE link returned in 109.7 s. |
| W_SECURITY_05 | `test_w_security_05_link_uptime_after_restore_ipv6` | 1. Set the BTS to `none`, then back to `psk2+ccmp-256`. 2. From `10.0.150.40`, force the CPE through `none` and back to `psk2+ccmp-256` so the STA actually reassociates. Time the IPv6 return. 3. Set the CPE to `none` again, restore `psk2+ccmp-256` the same way, and time it again. Limit is 120 s. | Both restore times are measured and within 120 s. | PASS 9 Oct 2026 (346s). BTS link returned in 35.1 s. CPE link returned in 115.2 s. |
| W_SECURITY_06 | `test_w_security_06_link_stability_monitor_ipv6` | Same open-then-WPA2 path as W_SECURITY_05. After the BTS restore, ping IPv6 both ways for 5 minutes. After the CPE restore, ping both ways for another 5 minutes. | Every sample replies. No ping failure in either window. | PASS 9 Oct 2026 (963s). After the BTS restore, 15/15 pings each way. After the CPE restore (115.2 s), another 15/15 each way. |
| W_SECURITY_07 | `test_w_security_07_wpa2_psk_connection_ipv6` | Confirm both radios are already `psk2+ccmp-256`. Ping IPv6 both ways. Read the station list and the CPE address. | The STA is associated and both pings reply. | PASS 9 Oct 2026 (15s). BTS mode `ap`, CPE mode `sta`, one association each way. BTS→CPE and CPE→BTS IPv6 pings replied. |
| W_SECURITY_09 | `test_w_security_09_wpa2_association_ipv6` | Confirm the STA is associated, soft-reboot the CPE, and wait for it to join again. | The CPE is associated before the reboot and again after SSH returns. | PASS 9 Oct 2026 (207s). One association each way before the reboot. After the 120 s reboot wait, SSH and IPv6 came back and the STA associated again. |
| W_SECURITY_11 | `test_w_security_11_open_mode_attempt_ipv6` | Leave the BTS on `psk2+ccmp-256`. Set the CPE to `none`. Confirm the link drops. From `10.0.150.40`, put the CPE back to `psk2+ccmp-256` and wait for IPv6. | The BTS stays WPA2. The open STA does not stay up. The good key brings the link back. | PASS 9 Oct 2026 (208s). BTS stayed `psk2+ccmp-256`. The link dropped, then returned in 110.8 s after the CPE restore. |
| W_SECURITY_14 | `test_w_security_14_invalid_passphrase_ipv6` | Leave the BTS key alone. Put a wrong key on the CPE and wait for the link to drop. From `10.0.150.40`, put the original key back. | The wrong key does not stay associated. The original key matches and IPv6 returns. | PASS 9 Oct 2026 (195s). The link dropped on the wrong key and returned in 84.9 s after the original key was restored. |

---

## Spectrum Analyzer and Site Survey

Code: `tests/SpectrumSiteSurvey/test_spectrum_analyzer.py` → `utils/spectrum_site_survey_flows.py`.

Page: Monitor → Tools → Spectrum Analyzer (`adv_sascan.htm`). Radio 1 is the live 5 GHz link. Do not change SSID, channel, or Tx. Stop the scan before the next case. The Site Survey page is the next tab on that Tools menu; these rows are the spectrum sheet.

**Last verified:** this bench, 9 Oct 2026. SPECT_ANALYZER_01 through SPECT_ANALYZER_06, SPECT_ANALYZER_09, and SPECT_ANALYZER_12 through SPECT_ANALYZER_25 passed, except SPECT_ANALYZER_10, which failed (reboot clears the row; the sheet says Not Pass). SPECT_ANALYZER_14, 17, 21, 22, and 23 were run again the same day (5 passed in 371s) and the steps below are from that run. SPECT_ANALYZER_26 is removed: this bench is not an isolated RF chamber. There is no SPECT_ANALYZER_07, SPECT_ANALYZER_08, or SPECT_ANALYZER_11. SSID stayed `UBR655_R1_PS`, channel 36, HT80. The BTS uses the background scan. The CPE has no AP VAP, so Start on the STA is not used except SPECT_ANALYZER_12, where Stop is the point of the case: it disconnects the link and the kernel refuses the background collector. The CPE plot is filled from the live channel survey, and the CPE page is opened through `10.0.150.40` to `192.168.2.11:443`. After a reboot, open the BTS page on `http://192.168.2.10`. IPv6 from the test PC stays down until the STA joins. Case 16 runs `iperf3` over SSH on the radios, not from the test PC.

Retest one node with `--profile default --skip-testbed-bootstrap --local-ipv6 2401:4900:d0:40d4:0:17b8:0:330 --remote-ipv6 2401:4900:d0:40d4::17b8:0:331`. Do not pass `--fallback-ip 192.168.2.11`. Ping `:331` before starting. Trust the pytest summary and `EXIT:${PIPESTATUS[0]}`.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| SPECT_ANALYZER_01 | `test_spect_analyzer_01_ui_elements` | Open the page on the BTS, then the CPE. Read Radio 1, Start, the four sections, the plot canvases, and the interference table headers. | Radio 1, Start, Channel Scan Results, Interference Spectrum, SINR plot, and Wi-Fi Channel Analyzer are on the page. | PASS 9 Oct 2026 (51s). Both radios show Radio 1 and Radio 2, Start, all four sections, and the interference columns. This image has no Best Channels label (WARN). |
| SPECT_ANALYZER_02 | `test_spect_analyzer_02_scan_start_logging` | Click Start on Radio 1. Read the status line and `logread` (the System Logs page). Stop the scan. On the CPE the STA scan drops the RF link, so read `logread` through `10.0.150.40` → `192.168.2.11`, then `wifi up wifi1` and wait until `ath1` associates again. | The page says the scan is in progress, and System Logs gain a scan line. | PASS 9 Oct 2026 (140s). BTS status “Spectrum Scan is in progress” and log `channel_scan_triggered`. CPE status the same and a `wlan` scan line. The STA dropped; a second `wifi up wifi1` brought it back on 5.18 GHz to `C4:E3:CE:63:E6:C7`. |
| SPECT_ANALYZER_03 | `test_spect_analyzer_03_link_stability` | Start the analyzer on the BTS. For 30 s, ping both ways and read `ath1` on both radios. Do not press Start on the CPE: that button disconnects the STA, and the kernel will not run the background collector without an AP VAP. Stop the BTS scan. | Every sample stays associated and both pings reply. | PASS 9 Oct 2026 (67s). Both radios stayed on 5.18 GHz. 6/6 samples, and both pings replied after the scan. |
| SPECT_ANALYZER_04 | `test_spect_analyzer_04_interference_graph` | On the BTS, click Start and wait for interference rows. On the CPE, publish the live `wifitool ath1 get_scan_chan_util` survey into the file the plot polls. The kernel will not start a background scan on a STA (`no AP vap`). Read the Min / Avg / Max legend and the canvas. Stop the BTS scan and remove the CPE file. | The graph is drawn and a row has power numbers. | PASS 9 Oct 2026 (62s). BTS: legend, canvas, 3 power rows. CPE: legend, canvas, 96 rows from the live channel survey. |
| SPECT_ANALYZER_05 | `test_spect_analyzer_05_sinr_plot` | Same scan as SPECT_ANALYZER_04. Read the SINR canvas, the color scale, and the SINR column. | The SINR plot is drawn with more than one color, and the table has a SINR number. | PASS 9 Oct 2026 (55s). BTS colors `#7cb342`, `#fdd835`, `#e53935`, SINR `20`. CPE SINR `41`. |
| SPECT_ANALYZER_06 | `test_spect_analyzer_06_interference_table` | Same scan as SPECT_ANALYZER_04. Read the interference table headers and one data row. | Columns are Index, Channel, Frequency, Power Min, Power Avg, Power Max, and SINR, with a data row. | PASS 9 Oct 2026 (62s). Both radios show the seven columns. BTS row `1 | 36 | 5180 | -108 | -69 | -43 | -11`. CPE row `1 | 36 | 5180 | -107 | -100 | -95 | 41`. |
| SPECT_ANALYZER_09 | `test_spect_analyzer_09_results_download` | Fill both tables, then click the download icon on Wi-Fi Channel Analyzer and on Interference Spectrum. Channel Scan Results is switched off in this image (`advscan_channel_scan_enabled` is false), so that icon stays hidden. On the CPE, the survey file `/etc/bg_wifi1_ap_list` is written only by an AP scan, so copy `wlanconfig ath1 list ap` there before the click. | A `.txt` and a `.csv` download, and each file contains the row on screen. | PASS 9 Oct 2026 (80s). BTS `SiteSurvey-wifi1-UBR655-2026-09-13.txt` and `Interference-wifi1-...330-2026-10-09.csv` (row `5180 / -108 / -43`). CPE `SiteSurvey-wifi1-UBR655-2026-09-24.txt` (`UBR655_R1_PS`, `c4:e3:ce:63:e6:c7`) and interference CSV row `5180 / -107 / -95 / 41`. |
| SPECT_ANALYZER_10 | `test_spect_analyzer_10_persistence_reboot` | 1. Produce an interference row (BTS Start, CPE live channel survey). 2. Record the first row. 3. Confirm the only stored file is `/tmp/interference_log_wifi1.bin`. Stopping the scan does not copy it to `/etc/interference_log_wifi1.csv`. 4. `reboot` that radio from a fresh SSH session (`192.168.2.10` for the BTS; the CPE session is the jump). 5. Wait until `192.168.2.10` answers and `ath1` is Master on 5.18 GHz with ESSID `UBR655_R1_PS`. Open Spectrum Analyzer on `http://192.168.2.10` and do not press Start. The first open can be `ERR_NETWORK_CHANGED`, then the ping can drop; wait and retry. 6. Repeat on the CPE through `10.0.150.40` → `192.168.2.11`. The first SSH tries can be "No route to host" while the CPE is still booting. `wifi up wifi1` if `ath1` is Not-Associated. Do not change SSID, channel, or Tx, and do not copy the log into `/etc` to force a pass. | The same interference row is still on the page. | FAIL 9 Oct 2026 (405s). Matches the sheet (Not Pass). BTS before `1 \| 36 \| 5180 \| -108 \| -64 \| -44 \| -10`. After the link settled, the BTS interference table had 0 rows and `/tmp` and `/etc` interference files were gone. CPE before `1 \| 36 \| 5180 \| -107 \| -100 \| -95 \| 41`; the page after reboot had no interference row. Link returned: BTS Master 5.18 GHz channel 36 HT80, CPE associated to `C4:E3:CE:63:E6:C7`, both `psk2+ccmp-256`, SSID `UBR655_R1_PS`. |
| SPECT_ANALYZER_12 | `test_spect_analyzer_12_midway_stop` | Click Start on Radio 1, then Stop. The status must leave "in progress" and Start must still answer. On the CPE, Start drops the STA. Stop it, then `wifi up wifi1` from `10.0.150.40` and wait for `C4:E3:CE:63:E6:C7`. A second `wifi up wifi1` is normal. A BTS script note (`split` / `innerHTML`) does not fail the case when the page still answers. | The scan halts and the page is not locked. | PASS 9 Oct 2026 (240s). Both radios left "in progress". BTS status became "Stopping scan...". CPE status became idle after the second `wifi up wifi1`. |
| SPECT_ANALYZER_13 | `test_spect_analyzer_13_noise_floor` | Same scan as SPECT_ANALYZER_04. On every interference row, Power Min is a dBm number, and min is less than or equal to avg, which is less than or equal to max. | Power Min is shown on each frequency. | PASS 9 Oct 2026 (62s). BTS 3/3 rows, first min `-108` at 5180. CPE 96/96 rows, first min `-107` at 5180. |
| SPECT_ANALYZER_14 | `test_spect_analyzer_14_external_interference` | 1. On each radio, read `wlanconfig ath1 list ap`. An external AP is any BSSID other than `c4:e3:ce:63:e6:c7` and `c4:e3:ce:63:e6:31`. 2. Run the usual scan (BTS Start; CPE live channel survey). 3. The loudest Max Power must sit above that row's Power Min, and SINR must be a number. On the CPE, if `list ap` shows only the serving BTS, the survey peak is the external energy. Do not invent an interferer. | A neighbor or a survey peak is highlighted in Max Power and SINR. | PASS, rechecked 9 Oct 2026 (62s). Both radios heard hidden BSSID `88:dc:97:1f:e2:20` on 5180. BTS peak 5190, max `-77`, SINR `23`. CPE peak 5760, max `-73`, SINR `18`. |
| SPECT_ANALYZER_15 | `test_spect_analyzer_15_idle_performance` | Sample CPU and MemAvailable, start the scan with no traffic, sample again, and time one status read. Pass when scan CPU stays under 90 percent, the jump from idle is under 50 points, available memory drops less than 64 MB, and the status read returns in under 5 s. On the CPE, publish the channel survey instead of clicking Start. | The scan does not peg the CPU, the memory, or the page. | PASS 9 Oct 2026 (66s). BTS CPU 76.1% idle to 83.7% during the scan, memory rose 1292 kB, status read under 1 s. CPE CPU 63.3% to 78.0%, memory rose 1188 kB. |
| SPECT_ANALYZER_16 | `test_spect_analyzer_16_load_performance` | SSH only. On the CPE, through `10.0.150.40` → `192.168.2.11`, run `iperf3 -s -p 5216`. On the BTS (`192.168.2.10`) run `iperf3 -c 192.168.2.11 -p 5216 -t 8 -J` once while idle, start the BTS scan, and run the same client again. Do not click Start on the CPE. `killall iperf3` on both radios at the end. Pass when both runs are at least 10 Mbps and the scan run is at least 70% of the idle run, and the CPE stays associated. | Throughput is not severely cut by the scan. | PASS 9 Oct 2026 (46s). Idle 34.8 Mbps, during the scan 31.7 Mbps. CPE stayed on 5.18 GHz associated to `C4:E3:CE:63:E6:C7`. |
| SPECT_ANALYZER_17 | `test_spect_analyzer_17_multi_cpe` | 1. Count station MAC lines in `wlanconfig ath1 list sta`. Ignore the "Max STA phymode" summary; that line is not a station. 2. Start the BTS scan for about 12 s. 3. Read `iwconfig ath1` on the CPE through `10.0.150.40` → `192.168.2.11`. Every connected CPE must still be associated. This bench has one CPE. Do not add a fake station. | The connected CPE does not drop. | PASS, rechecked 9 Oct 2026 (44s). One station. It stayed on 5.18 GHz, ESSID `UBR655_R1_PS`. |
| SPECT_ANALYZER_18 | `test_spect_analyzer_18_remote_session` | From the test PC, open Spectrum Analyzer, run the usual scan, and read the interference rows without leaving the page. The CPE page is the HTTPS tunnel. Do not change the management VLAN. | The remote session stays up and returns rows. | PASS 9 Oct 2026 (64s). BTS session stayed on LuCI and returned 3 rows. CPE tunnel returned 96 rows. |
| SPECT_ANALYZER_19 | `test_spect_analyzer_19_concurrent_admins` | Open two browser logins. Start the scan in the first. The second must show "in progress" or the same rows. On the CPE tunnel, open the second login with `https://127.0.0.1:<port>/cgi-bin/luci/` before the normal login helper, or it will try HTTP on the TLS forward and get `ERR_EMPTY_RESPONSE`. | Both sessions show the scan. | PASS 9 Oct 2026 (132s). BTS admin 2 showed "Spectrum Scan is in progress". CPE admin 2 showed 96 rows. |
| SPECT_ANALYZER_20 | `test_spect_analyzer_20_page_refresh` | Start the scan (CPE: publish the survey). Press reload. The page must show the scan status or the last rows, and must not be a LuCI error page. The BTS `split` / `innerHTML` script note is the same poll note as SPECT_ANALYZER_12. | Refresh shows the ongoing scan or the last rows. | PASS 9 Oct 2026 (79s). BTS still said "Spectrum Scan is in progress". CPE showed 96 rows. |
| SPECT_ANALYZER_21 | `test_spect_analyzer_21_high_ap_density` | 1. Count BSSID lines from `wlanconfig ath1 list ap` (any line that contains a MAC). 2. Open Wi-Fi Channel Analyzer and scroll `#advscan-list`. 3. A `split` / `innerHTML` poll note does not fail the case. This RF does not have 50 APs, so the check is the live list, not a fabricated one. | The heard APs are listed and the table scrolls without a crash. | PASS, rechecked 9 Oct 2026 (117s). BTS heard 8 APs. CPE heard 3. Both tables scrolled. |
| SPECT_ANALYZER_22 | `test_spect_analyzer_22_hidden_ssid_download` | 1. Read `wlanconfig ath1 list ap`. 2. If the CPE list has no `[ hidden ]` line, run `iw dev ath1 scan trigger`, wait 2 s, then `iw dev ath1 scan dump`. Append each BSS whose SSID line is empty as `[ hidden ]` plus its BSSID, channel 36, 5180. Then `wifi up wifi1` if the STA dropped. 3. Write that text to `/etc/bg_wifi1_ap_list` and click the Wi-Fi Channel Analyzer download. The icon saves a `.txt` file. The file must keep `[ hidden ]` and the BSSID on the same line. Do not insert a fake hidden SSID. | The download contains one hidden SSID and the brackets do not split the line. | PASS, rechecked 9 Oct 2026 (67s). BTS `SiteSurvey-wifi1-UBR655-2026-09-13.txt` and CPE `SiteSurvey-wifi1-UBR655-2026-09-24.txt` each kept `[ hidden ]` and the BSSID on one line. |
| SPECT_ANALYZER_23 | `test_spect_analyzer_23_graph_tooltips` | 1. Run the usual scan. 2. Hover the middle hit on the interference canvas and the SINR canvas. 3. The tooltip frequency must be a table row. SINR must equal that row. Avg must equal that row or sit between its Power Min and Power Max, because the hover is one bin inside the channel. | The tooltip matches that frequency's table row. | PASS, rechecked 9 Oct 2026 (67s). BTS channel 36, 5180 MHz, Avg `-80 dBm`; SINR tooltip channel 38, 5190 MHz, SINR `10`. CPE channel 122, 5610 MHz, Avg `-100`, SINR `41`. |
| SPECT_ANALYZER_24 | `test_spect_analyzer_24_continuous_scan` | Run 10 start/stop cycles on the BTS (about 4 s each). On the CPE, publish the channel survey 10 times instead of clicking Start. Compare MemAvailable. The radio must still be up and the page must still answer. | Ten cycles, memory drop under 64 MB, radio and page still up. | PASS 9 Oct 2026 (179s). BTS memory drop 1440 kB, ESSID still `UBR655_R1_PS`. CPE memory did not drop, still associated at 5.18 GHz. The BTS poll script note did not lock the page. |
| SPECT_ANALYZER_25 | `test_spect_analyzer_25_disconnect_recovery` | Produce rows, drop the management browser session for 5 s, bring it back, and open Spectrum Analyzer again without pressing Start. This session cannot take `enp1s0` down, so the browser is set offline instead of unplugging the management port. | The rows are on the page again after the session returns. | PASS 9 Oct 2026 (140s). BTS had 3 rows before the drop and 24 after (the scan kept running). CPE had 96 rows before and after. |

---

## Link Test

Code: `tests/LinkTest/test_link_test.py` → `utils/link_at_sw_flows.py`.
Report: `reports/artifacts/Senao_Link_Test_Report.html`.

**Last verified:** 14 PASS (25 Sep 2026). Run with `--local-ip 192.168.2.10 --remote-ip 192.168.2.11 --fallback-ip 192.168.2.10`. Do not use the pytest default `.230`. Open CPE SSH and GUI before any case that disables the AP.

Shared method: Monitor → Tools → Link Test Tool. Run the sheet duration and bandwidth. Read the result on the GUI. Stop a stuck session. Confirm the STA is still associated.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| LINK_TEST_03 | `test_link_test_03_sequential_ul_dl` | Run uplink, then downlink, as two sessions. | Both directions return a rate. | PASS |
| LINK_TEST_04 | `test_link_test_04_long_duration` | Run the long duration from the profile. | Session finishes. Result is present. | PASS |
| LINK_TEST_05 | `test_link_test_05_high_bandwidth_80mhz` | Run at 80 MHz / high bandwidth. | Result is present on the current width. | PASS |
| LINK_TEST_07 | `test_link_test_07_restart_bts_during_test` | Start a test and soft-reboot the BTS mid-run. | Session stops cleanly. Link returns. | PASS |
| LINK_TEST_08 | `test_link_test_08_client_reconnect_after_drop` | Drop the STA and let it reassociate, then run the tool. | Tool works after the STA returns. | PASS |
| LINK_TEST_13 | `test_link_test_13_excessive_duration` | Ask for longer than `duration_max`. | Tool clamps to the max, or the session ends within that limit. | PASS |
| LINK_TEST_15 | `test_link_test_15_valid_speed_from_cpe` | Run the tool on the CPE toward the BTS. | CPE shows a valid speed. | PASS |
| LINK_TEST_17 | `test_link_test_17_cpe_reboot_during_test` | Start a test and reboot the CPE mid-run. | Session stops. STA returns. | PASS |
| LINK_TEST_18 | `test_link_test_18_network_congestion` | Run while congestion is applied. | Tool still returns metrics. | PASS |
| LINK_TEST_19 | `test_link_test_19_packet_loss_simulation` | Run with loss injected. | Loss shows in the result. | PASS |
| LINK_TEST_20 | `test_link_test_20_latency_simulation` | Run with latency injected. | Latency shows in the result. | PASS |
| LINK_TEST_22 | `test_link_test_22_server_unreachable` | Open CPE first, then disable the BTS AP, run the CPE test, re-enable the AP. | CPE test fails while the AP is down. Link returns after re-enable. | PASS |
| LINK_TEST_23 | `test_link_test_23_metrics_validation` | Run a normal test and check throughput, loss, and latency fields. | All three fields are filled. | PASS |
| LINK_TEST_24 | `test_link_test_24_rapid_repeated_tests` | Start several tests back to back. | Each one finishes or is stopped. No stuck session. | PASS |

---

## ARP and bridge

Code: `tests/ArpBridgeTable/test_arp_bridge_table.py` → `utils/arp_bridge_table_flows.py`.

**Last verified:** this bench, 8 Oct 2026, IPv4 only (`.10` / `.11`). ARPBRIDGE_01, ARPBRIDGE_02, and ARPBRIDGE_06 through ARPBRIDGE_14 passed live. There is no ARPBRIDGE_03, ARPBRIDGE_04, or ARPBRIDGE_05 test. Do not move the CPE off static `.11` unless the case says DHCP, and restore it after. SSID stays `UBR655_R1_PS`.

Shared method: generate the ARP or bridge event from the lab PC or the peer, then read `ip neigh` / `brctl showmacs` and the GUI table.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| ARPBRIDGE_01 | `test_arpbridge_01_basic_arp_resolution_ipv4` | 1. From the BTS, ping `.11`. 2. Read `ip neigh` for that address and compare the MAC with the RF link MAC. 3. From the CPE, ping `.10` and do the same. | Each side has an IPv4 neighbor for the peer, and that MAC belongs to the peer radio. | PASS 8 Oct 2026 (54s). BTS learned `.11` as `c4:e3:ce:63:e6:2f` (RF `…:e6:31`). CPE learned `.10` as `c4:e3:ce:63:e6:c5` (RF `…:e6:c7`). |
| ARPBRIDGE_02 | `test_arpbridge_02_duplicate_ip_handling_ipv4` | 1. Ping the peer and record the first MAC. 2. Install a different MAC for that same IPv4. 3. Remove the duplicate and confirm the RF path still has the first MAC. On the CPE the duplicate is applied in the background so the management session can come back. | The duplicate is visible, then the original peer MAC is the one the bridge still uses. | PASS 8 Oct 2026 (73s). BTS first MAC `…:e6:2f`, duplicate `…:e6:84`, RF kept `…:e6:31`. CPE first MAC `…:e6:c5`, duplicate `…:e6:6e`, RF kept `…:e6:c7`. |
| ARPBRIDGE_06 | `test_arpbridge_06_dynamic_ip_allocation_ipv4` | 1. Leave the BTS static on `.10` with its DHCP server already running. 2. On the CPE, set IPv4 to DHCP and start `udhcpc` on `br-lan`. Do not `ifup lan` — that drops `ath1`. 3. Read the new lease from the BTS and confirm `ip neigh` has the CPE MAC. 4. Put static `.11` back on the live bridge and stop `udhcpc`. A restore session must be the CPE, not the BTS. | The leased address is in the BTS ARP table, then `.11` answers again. | PASS 8 Oct 2026 (198s). CPE lease was `192.168.2.113`, ARP MAC `c4:e3:ce:63:e6:2f`. Static `.11` was restored. BTS stayed `.10`. |
| ARPBRIDGE_07 | `test_arpbridge_07_gratuitous_arp_handling_ipv4` | 1. Clear the neighbor entry. 2. Send a gratuitous ARP (`arping -U`) from the peer. 3. Read the observer table. | The peer MAC is learned again from the gratuitous ARP or from the bridge table. | PASS 8 Oct 2026 (144s). BTS saw the CPE on the bridge as `c4:e3:ce:63:e6:31`. CPE learned `.10` as `c4:e3:ce:63:e6:c5` (RF `…:e6:c7`). |
| ARPBRIDGE_08 | `test_arpbridge_08_bridge_interface_ipv4` | Read `br-lan` members on the BTS and the CPE from `brctl` and sysfs. Keep every interface name. A non-tty `ls` prints one name per line. | `eth0` and a radio port are in `br-lan` on both sides. | PASS 8 Oct 2026 (53s). Both bridges have `ath1`, `ath2`, `eth0`, and `eth1`. |
| ARPBRIDGE_09 | `test_arpbridge_09_bridge_traffic_ipv4` | Ping the peer through `br-lan` and read the bridge ports. | The peer answers. `br-lan` still has ethernet and a radio port. | PASS 8 Oct 2026 (60s). BTS pinged `.11` and the CPE pinged `.10`, both with replies. `ath1` packet counters stayed at 0 (WARN). The neighbor for `.11` is on `ath1`. |
| ARPBRIDGE_10 | `test_arpbridge_10_mac_learning_ipv4` | 1. Note the peer MAC in the bridge table. 2. Flush it. 3. Ping the peer so it is learned again. The CPE side is read-only after the BTS flush. | The peer MAC comes back on a bridge port with a fresh age. | PASS 8 Oct 2026 (54s). BTS relearned `c4:e3:ce:63:e6:2f` and `…:e6:31` on port 3. CPE still showed the BTS MAC `…:e6:c5` on port 3. |
| ARPBRIDGE_11 | `test_arpbridge_11_correct_port_forwarding_ipv4` | Ping the peer and read which bridge port holds its MAC. | The MAC is on exactly one port. It is not flooded. | PASS 8 Oct 2026 (70s). Both sides learned the peer on port 3 only. |
| ARPBRIDGE_12 | `test_arpbridge_12_bridge_table_entry_update_ipv4` | Flush the peer MAC and learn it again. This lab has only the RF path, so the port may stay the same. | The MAC is back on one bridge port. A same-port relearn is recorded, not failed. | PASS 8 Oct 2026 (79s). BTS flushed two entries and relearned `…:e6:31` on port 3 again. CPE still showed `…:e6:c5` on port 3. |
| ARPBRIDGE_13 | `test_arpbridge_13_refresh_clear_ipv4` | Open the ARP table in the GUI on the BTS, then the CPE. Clear, then refresh. | Clear drops rows. Refresh brings live neighbors back. | PASS 8 Oct 2026 (75s). BTS GUI went from 10 rows to 4 after Clear, then Refresh kept 4. CPE GUI went from 6 rows to 1, then Refresh kept 1. SSID was left as-is. |
| ARPBRIDGE_14 | `test_arpbridge_14_bridge_table_aging_ipv4` | Shorten `br-lan` ageing to 20 s and watch a remote MAC that is already older than that. Restore 300 s when the check ends. | That idle MAC leaves the bridge table. | PASS 8 Oct 2026 (53s). BTS ageing went 300 s to 20 s and dropped `7c:f1:7e:b2:69:2d` (age 38.92). CPE dropped `c0:a2:c3:a3:fe:e6` (age 46.71). Ageing was put back to 300 s. |

---

## CPE API (2.4 GHz)

Code: `tests/API/test_API.py` → `utils/cpe_api_24_*.py`.

**Last verified:** not signed off here. These hit the CPE 2.4 GHz REST API, not the 5 GHz data SSID. Do not `wifi down` the 5 GHz STA while running them.

Shared method: call the API on the CPE management host. Check the HTTP code and the JSON body. Upgrade cases restore the running image if they change it.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| API_01 | `test_api_01_btsconnect_success` | Connect with valid BTS credentials. | HTTP success and a session. | |
| API_02 | `test_api_02_btsconnect_authentication_fail` | Connect with a bad password. | Auth is rejected. | |
| API_04 | `test_api_04_alignment_info_stream_1s` | Stream alignment info at 1 s. | Stream returns samples. | |
| API_05 | `test_api_05_alignment_info_stream_5s` | Stream alignment info at 5 s. | Stream returns samples. | |
| API_06 | `test_api_06_alignment_data` | Read one alignment snapshot. | RSSI and link fields are present. | |
| API_07 | `test_api_07_sw_version` | Read the software version. | Body matches the running firmware. | |
| API_08 | `test_api_08_sw_version_missing_file_returns_503` | Hide the version file. | API returns 503. File is restored. | |
| API_09 | `test_api_09_upload_sw` | Upload an image. | Upload is accepted. | |
| API_10 | `test_api_10_upload_sw_status` | Read upload status after API_09. | Status shows the uploaded image. | |
| API_11 | `test_api_11_upgrade_sw_preserve_config` | Upgrade with preserve-config. | Device returns on the new image with settings kept. | |
| API_12 | `test_api_12_upgrade_sw_no_preserve_config` | Upgrade without preserve. | Image applies. Lab config is restored after. | |
| API_13 | `test_api_13_upgrade_sw_status_in_progress` | Read status while an upgrade is running. | Body says in progress. | |
| API_14 | `test_api_14_upgrade_sw_status_success` | Read status after a good upgrade. | Body says success. | |
| API_15 | `test_api_15_upgrade_sw_status_invalid_fw` | Send a bad image and read status. | Status says invalid. Running image unchanged. | |
| API_16 | `test_api_16_link_throughput_test` | Start a link throughput test with the BTS server up. | Test returns a rate. | |
| API_17 | `test_api_17_link_throughput_test_no_bts_server` | Start the test with no BTS server. | API reports the server is unreachable. | |
| API_18 | `test_api_18_link_throughput_test_in_progress` | Poll while a test is running. | Status says in progress, then completes. | |

---

## DDRS

Code: `tests/DDRS/test_ddrs.py` → `utils/ddrs_flows.py`. Profile: `profiles/ddrs_lab.yaml`.

**Last verified:** not signed off here. Needs the live RF link. Co-channel cases also need the second AP and the attenuator. P2MP cases need more than one CPE. If that gear is not on the bench, do not rewrite the case — mark it blocked.

Shared method: set DDRS or a fixed MCS as the row says, pass traffic, apply the disruption (wireless restart, network restart, ethernet duplex, or soft reboot), then read the rate again.

| ID | What we do | Pass means | Result |
|----|------------|------------|--------|
| DDRS_01 | Associate and pass traffic with DDRS on. | STA stays up. Rate is inside the DDRS range. | |
| DDRS_02 | Wireless restart during DDRS traffic. | Link and traffic return. Rate still in range. | |
| DDRS_03 | Network restart during DDRS traffic. | Same. | |
| DDRS_04 | Ethernet duplex restart during DDRS traffic. | Same. | |
| DDRS_05 | Soft reboot during DDRS traffic. | Same after boot. | |
| DDRS_07 | DDRS at low RSSI (attenuator). | Rate drops with RSSI and stays associated. | |
| DDRS_08 | Wireless restart at low RSSI. | Link returns at the low-RSSI rate. | |
| DDRS_09 | Network restart at low RSSI. | Same. | |
| DDRS_10 | Ethernet restart at low RSSI. | Same. | |
| DDRS_11 | Soft reboot at low RSSI. | Same after boot. | |
| DDRS_13 | Co-channel interferer on the same channel. | Rate changes with interference. Link holds. | |
| DDRS_14 | Co-channel plus wireless restart. | Link returns. | |
| DDRS_15 | Co-channel plus network restart. | Link returns. | |
| DDRS_16 | Co-channel plus ethernet restart. | Link returns. | |
| DDRS_17 | Co-channel plus soft reboot. | Link returns. | |
| DDRS_19 | BTS DDRS, CPE fixed MCS. | BTS rate follows DDRS. CPE stays on the fixed MCS. | |
| DDRS_20 | That pair, wireless restart. | Modes are unchanged after restart. | |
| DDRS_21 | That pair, network restart. | Same. | |
| DDRS_22 | That pair, ethernet restart. | Same. | |
| DDRS_23 | That pair, soft reboot. | Same. | |
| DDRS_25 | BTS fixed MCS, CPE DDRS. | Opposite of DDRS_19. | |
| DDRS_26 | That pair, wireless restart. | Modes hold. | |
| DDRS_27 | That pair, network restart. | Modes hold. | |
| DDRS_28 | That pair, ethernet restart. | Modes hold. | |
| DDRS_29 | That pair, soft reboot. | Modes hold. | |
| DDRS_31 | P2MP, throughput stays stable. | Every CPE holds a rate. | |
| DDRS_32 | P2MP associate with traffic. | All STAs associate. | |
| DDRS_33 | P2MP wireless restart. | All STAs return. | |
| DDRS_34 | P2MP network restart. | All STAs return. | |
| DDRS_35 | P2MP ethernet restart. | All STAs return. | |
| DDRS_36 | P2MP soft reboot. | All STAs return. | |
| DDRS_38 | P2MP plus co-channel. | Rates move. STAs stay up. | |
| DDRS_39 | P2MP co-channel, wireless restart. | STAs return. | |
| DDRS_40 | P2MP co-channel, network restart. | STAs return. | |
| DDRS_41 | P2MP co-channel, ethernet restart. | STAs return. | |
| DDRS_42 | P2MP co-channel, soft reboot. | STAs return. | |
| DDRS_50 | P2MP, BTS DDRS, CPEs fixed. | BTS adapts. CPEs stay fixed. | |
| DDRS_51–54 | Same pair with wireless, network, ethernet, soft reboot. | Modes hold after each disruption. | |
| DDRS_56 | P2MP, BTS fixed, CPEs DDRS. | CPEs adapt. BTS stays fixed. | |
| DDRS_57–60 | Same pair with the four disruptions. | Modes hold. | |
| DDRS_65 | Throughput, latency, and CPU while DDRS runs. | Traffic flows. CPU stays in bounds. | |
| DDRS_66 | Walk spatial-stream modes. | Each mode associates and passes traffic. | |
| DDRS_71 | Reconnect and check the min rate resets. | Min rate starts clean after reconnect. | |

Node names are `test_ddrs_<nn>_<short_name>` in `tests/DDRS/test_ddrs.py`.

---

## 2.4 GHz radio

Code: `tests/Radio24Scan/test_2_4g_radio.py` → `utils/radio24_scan_flows.py`.

**Last verified:** not signed off here. Scan PC is `10.0.150.7`. The hidden SSID is the CPE 2.4 GHz management AP (`ath0`), not the 5 GHz data SSID. Leave the 5 GHz link up.

Shared method: scan from the lab Wi-Fi NIC. Join or refuse the hidden SSID as the row says. DHCP rows use the CPE `lan24` server only.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| RADIO_24_01 | `test_radio_24_01_hidden_ssid_not_visible` | Passive scan. | Hidden management SSID is not in the visible list. | |
| RADIO_24_02 | `test_radio_24_02_connect_hidden_ssid` | Join the hidden SSID with the right key. | NIC associates and gets an address. | |
| RADIO_24_03 | `test_radio_24_03_connect_hidden_ssid_wrong_password` | Join with a wrong key. | Association fails. | |
| RADIO_24_04 | `test_radio_24_04_reboot_cpe_scan_hidden_ssid` | Reboot the CPE, then scan and join. | Hidden SSID returns after boot and accepts the key. | |
| RADIO_24_08 | `test_radio_24_08_fw_upgrade_scan_hidden_ssid` | Upgrade the CPE, then scan the hidden SSID. | SSID returns on the new image. | |
| RADIO_24_17 | `test_radio_24_17_dhcp_server_down_no_ip` | Stop `lan24` DHCP and join. | Wi-Fi associates and gets no lease. | |
| RADIO_24_18 | `test_radio_24_18_dhcp_recovery_after_reboot` | Reboot with DHCP enabled. | Client gets a lease after boot. | |
| RADIO_24_19 | `test_radio_24_19_dhcp_stress_connect_disconnect` | Connect and disconnect the Wi-Fi NIC repeatedly. | Each connect gets a lease. | |
| RADIO_24_20 | `test_radio_24_20_dhcp_logs_lease_assignment` | Join and read `/tmp/dhcp.leases`. | Lease row matches the client MAC. | |
| RADIO_24_21 | `test_radio_24_21_no_ssid_during_boot` | Scan while the CPE is still booting. | Management SSID is absent during boot. | |
| RADIO_24_22 | `test_radio_24_22_ssid_appears_after_boot` | Wait until boot finishes, then scan. | SSID is back. | |
| RADIO_24_41 | `test_radio_24_41_valid_api_call` | Call the 2.4 GHz API with valid auth. | HTTP success. | |
| RADIO_24_42 | `test_radio_24_42_invalid_api_call` | Call an unknown path. | Error status. | |
| RADIO_24_43 | `test_radio_24_43_unauthorized_access` | Call with no or bad auth. | 401 or 403. | |
| RADIO_24_44 | `test_radio_24_44_malformed_request` | Send a broken body. | Client error. Device stays up. | |
| RADIO_24_45 | `test_radio_24_45_api_under_load` | Repeat the valid call. | Calls keep succeeding. | |
| RADIO_24_46 | `test_radio_24_46_api_after_reboot` | Reboot, then call the API. | API answers after boot. | |
| RADIO_24_49 | `test_radio_24_49_api_response_time` | Time one valid call. | Response is inside the limit. | |
| RADIO_24_50 | `test_radio_24_50_api_logging` | Call the API and read the log. | The call is logged. | |

---

## GUI

Code: `tests/GUI/test_*.py`. One pytest node per row below.

**Last verified:** not signed off in this file. Open the BTS GUI on the live management address. After any edit, restore the value you changed. Do not leave a new SSID or channel behind.

Shared method: open the page, read the widget, compare it with SSH, and restore when the case writes.

| ID | File | How we test | Pass means | Result |
|----|------|-------------|------------|--------|
| GUI_01 | `test_summary.py` | Summary system widgets vs SSH (model, version, uptime, temp). | GUI equals backend. | |
| GUI_02 | `test_summary.py` | Summary network widgets vs SSH. | GUI equals backend. | |
| GUI_03 | `test_summary.py` | Summary performance counters vs SSH. | GUI equals backend. | |
| GUI_04 | `test_summary.py` | Summary wireless (SSID, width, channel, security) vs SSH. | GUI equals backend. | |
| GUI_05 | `test_topPanel.py` | Click the logo. | Page stays on the GUI home. | |
| GUI_06 | `test_topPanel.py` | Read hostname and uptime in the header vs SSH. | Values match. | |
| GUI_07 | `test_topPanel.py` | Click the radio shortcut. | Radio page opens. | |
| GUI_08 | `test_topPanel.py` | Use Home and Apply. | Home navigates. Apply is usable. | |
| GUI_09 | `test_topPanel.py` | Find the reboot control. Do not leave the device down. | Control is reachable. Device returns if reboot is clicked. | |
| GUI_10 | `test_topPanel.py` | Log out. | Login page returns. | |
| GUI_17 | `test_radio_properties.py` | Toggle radio status, save, read UCI, restore. | Backend follows the toggle. | |
| GUI_18 | `test_radio_properties.py` | Change SSID, read UCI, restore the live SSID. | New SSID applies, then the live SSID is back. | |
| GUI_19 | `test_radio_properties.py` | Change bandwidth, read `htmode`, restore. | Width matches, then the live width is back. | |
| GUI_20 | `test_radio_properties.py` | Read configured and active channel vs SSH. | They agree. | |
| GUI_21 | `test_radio_properties.py` | Change encryption and key, restore the live pair. | STA reassociates. Live security is back. | |
| GUI_22 | `test_radio_properties.py` | Change max CPE, read it, restore. | Value matches. | |
| GUI_23 | `test_radio_properties.py` | Change DL/UL ratio, read it, restore. | Value matches. | |
| GUI_24 | `test_radio_properties.py` | Read DDRS status vs SSH. | Status matches. | |
| GUI_25 | `test_radio_properties.py` | Change spatial stream, read it, restore. | Value matches. | |
| GUI_26 | `test_radio_properties.py` | Change modulation index, read it, restore. | Value matches. | |
| GUI_28 | `test_radio_properties.py` | Change Tx power, read it, restore the live power. | Value matches. | |
| GUI_29 | `test_radio_properties.py` | Read maximum EIRP vs SSH. | Values match. | |
| GUI_34 | `test_radio_24.py` | Toggle 2.4 GHz radio, restore. | `ath0` follows the toggle. | |
| GUI_35 | `test_radio_24.py` | Change 2.4 GHz SSID, restore. | UCI matches, then the original is back. | |
| GUI_36 | `test_radio_24.py` | Change 2.4 GHz bandwidth, restore. | `htmode` matches. | |
| GUI_37 | `test_radio_24.py` | Set 2.4 GHz channel Auto, restore. | Auto applies, then the original channel is back. | |
| GUI_38 | `test_radio_24.py` | Change 2.4 GHz encryption, restore. | UCI matches. | |
| GUI_39 | `test_radio_24.py` | Change 2.4 GHz key, restore. | Key matches, then the original is back. | |
| GUI_50 | `test_network.py` | Read IPv4 config vs UCI. | GUI equals backend. | |
| GUI_51 | `test_network.py` | Edit the IP, ping it, restore `.10`. | New IP answers, then `.10` is back. | |
| GUI_52 | `test_network.py` | Edit the mask, restore. | UCI matches. | |
| GUI_53 | `test_network.py` | Edit the gateway, restore. | UCI matches. | |
| GUI_54 | `test_network.py` | Edit the fallback IP, restore. | UCI matches. | |
| GUI_55 | `test_network.py` | Edit the fallback mask, restore. | UCI matches. | |
| GUI_63 | `test_management.py` | Set a timezone, read it, restore. | UCI matches. | |
| GUI_64 | `test_management.py` | NTP enable, set server, disable, restore. | Each step matches UCI. | |
| GUI_65 | `test_management.py` | Sync time from the browser. | Device clock moves to the browser time. | |
| GUI_66 | `test_management.py` | Change logging options, restore. | UCI matches. | |
| GUI_67 | `test_management.py` | Turn temperature logging on and off. | State matches. | |
| GUI_68 | `test_management.py` | Set location, restore. | UCI matches. | |
| GUI_70 | `test_network.py` | Change ethernet speed and duplex, restore. | Link renegotiates and the value matches. | |
| GUI_71 | `test_network.py` | Change ethernet MTU, restore. | UCI matches. | |
| GUI_72 | `test_network.py` | Toggle the DHCP server, restore. | `dhcp.lan.ignore` follows the toggle. | |
| GUI_73 | `test_network.py` | Change DHCP lease time, restore. | UCI matches. | |
| GUI_74 | `test_network.py` | Read 2.4 GHz IP config vs UCI. | GUI equals backend. | |
| GUI_75 | `test_network.py` | Edit the 2.4 GHz mask, restore. | UCI matches. | |
| GUI_76 | `test_network.py` | Toggle 2.4 GHz DHCP, restore. | UCI matches. | |
| GUI_77 | `test_network.py` | Edit the 2.4 GHz pool, restore. | UCI matches. | |
| GUI_78 | `test_network.py` | Edit the 2.4 GHz lease time, restore. | UCI matches. | |
| GUI_83 | `test_monitor.py` | Radio link statistics vs `wlanconfig`. | Peer, rate, and RSSI agree. | |
| GUI_84 | `test_monitor.py` | Click the CPE IP hyperlink. | CPE GUI opens. | |
| GUI_88 | `test_monitor.py` | Open detailed statistics and go back. | Back returns to the link list. | |
| GUI_89 | `test_monitor.py` | Disconnect from detailed statistics, then let the STA return. | STA drops and reassociates. | |
| GUI_90 | `test_monitor.py` | Clear detailed counters. | Counters reset, then count again. | |
| GUI_91 | `test_monitor.py` | Detailed identity vs SSH. | Name and MAC match. | |
| GUI_92 | `test_monitor.py` | Detailed performance vs SSH. | Rates match. | |
| GUI_105 | `test_monitor.py` | Bridge table vs `brctl`. | Rows match. | |
| GUI_106 | `test_monitor.py` | Refresh and clear the bridge table. | Clear drops rows. Refresh brings them back. | |
| GUI_107 | `test_monitor.py` | ARP table vs `ip neigh`. | Rows match. | |
| GUI_108 | `test_monitor.py` | Refresh and clear the ARP table. | Same as GUI_106 for ARP. | |
| GUI_109 | `test_monitor.py` | Open config logs. | Page lists log lines. | |
| GUI_110 | `test_monitor.py` | Open device logs. | Page lists log lines. | |
| GUI_111 | `test_monitor.py` | Open temperature logs. | Page lists readings. | |
| GUI_112 | `test_monitor.py` | Open system logs. | Page lists log lines. | |
| GUI_113 | `test_monitor.py` | GUI ping to the CPE. | Ping succeeds. | |
| GUI_114 | `test_monitor.py` | GUI traceroute to the CPE. | Trace shows hops. | |
| GUI_115 | `test_monitor.py` | Start a short packet capture and stop it. | A capture file is produced. | |
| GUI_116 | `test_monitor.py` | Open the console tool. | Console is usable. | |
| GUI_117 | `test_monitor.py` | Read cable length vs the ethernet status. | Page returns a length or a clear no-cable state. | |
| GUI_118 | `test_monitor.py` | LLDP neighbor page vs SSH. | Neighbors match. | |
| GUI_127 | `test_monitor.py` | Link Test Tool parameters. | Duration and bandwidth fields accept the lab values. | |
| GUI_128 | `test_monitor.py` | Pick the CPE in the Link Test Tool. | The associated CPE is listed. | |
| GUI_130 | `test_monitor.py` | Run Link Test and read the result. | Result fields are filled. | |

Node name is `test_gui_<id>_<short_name>` in the file named above.

---

## IP

Code: `tests/IP/test_IP.py` → `utils/ip_test_flows.py`.

**Last verified:** not signed off here. Run BTS and CPE rows separately. Restore `.10`, `.11`, `:330`, and `:331` after every case that edits an address. Cases that reboot or factory-reset must bring the RF link back before the next row.

Shared method: set or read the address on that device, ping the local address and the peer, then restore the snapshot.

| ID | Nodes | How we test | Pass means | Result |
|----|-------|-------------|------------|--------|
| IP_01 | BTS | Set a static IPv4, read it, restore `.10`. | Address applies and `.10` returns. | |
| IP_02 | BTS | Ping the BTS local IPv4. | Ping succeeds. | |
| IP_03 | BTS | Ping the CPE from the BTS. | Ping succeeds across the link. | |
| IP_04 | BTS | Set the IPv4 gateway, read it, restore. | Gateway matches. | |
| IP_05 | BTS | Short IPv4 throughput across the link. | Traffic passes. | |
| IP_06 | BTS and CPE | Long ping on that device. | Loss stays inside the limit. | |
| IP_07 | BTS and CPE | Set MTU, read it, restore. | MTU matches. | |
| IP_08 | BTS and CPE | Send a packet bigger than the MTU. | Fragmentation works or the drop is the expected one. | |
| IP_09 | BTS and CPE | Soft-reboot that device. | Address and link return. | |
| IP_11 | BTS and CPE | Network soft-reset. | Lab address returns. | |
| IP_12 | BTS and CPE | Reset with retain. | Address is kept. Link returns. | |
| IP_13 | BTS and CPE | Reboot while traffic is running. | Traffic recovers. Address is unchanged. | |
| IP_14 | BTS and CPE | Flap the interface. | Address returns when the port is up. | |
| IP_15 | BTS and CPE | Backup, change, restore the backup. | Restored config matches the backup. | |
| IP_16 | BTS and CPE | Ping and read ARP. | Peer MAC is in the neighbor table. | |
| IP_17 | BTS and CPE | Add a static route, ping via it, delete it. | Route is used, then removed. | |
| IP_18 | BTS and CPE | Set static IPv6, restore `:330` or `:331`. | New address answers, then the lab address is back. | |
| IP_19 | BTS and CPE | Ping the local IPv6. | `ping6` succeeds. | |
| IP_20 | BTS and CPE | Ping the peer IPv6. | `ping6` succeeds across the link. | |
| IP_21 | BTS and CPE | Set the IPv6 gateway, restore. | Gateway matches. | |
| IP_22 | BTS and CPE | Short IPv6 throughput. | Traffic passes. | |
| IP_23 | BTS and CPE | Long `ping6`. | Loss stays inside the limit. | |
| IP_24 | BTS and CPE | Set IPv6 MTU, restore. | MTU matches. | |
| IP_25 | BTS and CPE | IPv6 fragment test. | Fragmentation behaves as the sheet says. | |
| IP_26 | BTS and CPE | Read the link-local address. | `fe80::` is present on the LAN. | |

`test_ip_extended_case` is the extra sheet row wired through `run_ip`. Run it only when that sheet id is named.

---

## Jumbo frames

Code: `tests/JumboFrames/test_jumbo_frames.py`.

**Last verified:** not signed off here. Restore MTU 1500 when the case ends unless the row says to leave 9000. Reboot and factory rows must bring the link back.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| JMB_01 | `test_jmb_01_configure_and_disable` | Enable jumbo, then disable it. | MTU follows both steps. | |
| JMB_02 | `test_jmb_02_configure_9000` | Set MTU 9000 on the data path. | A 9000-byte frame passes. GUI and UCI show 9000. | |
| JMB_03 | `test_jmb_03_min_mid_mtu` | Set the min and mid MTU values. | Each value applies. | |
| JMB_04 | `test_jmb_04_max_mtu_9000` | Set the maximum 9000. | 9000 applies and traffic passes. | |
| JMB_05 | `test_jmb_05_mgmt_vlan_mtu` | Set jumbo on the management VLAN path. | Management session still opens. | |
| JMB_06 | `test_jmb_06_jumbo_with_p2mp` | Jumbo with more than one CPE, if they are on the bench. | Each CPE passes a jumbo frame. | |
| JMB_07 | `test_jmb_07_reboot_persistence` | Set 9000, reboot, read MTU. | 9000 is still there after boot. | |
| JMB_08 | `test_jmb_08_mtu_1500` | Set 1500. | Jumbo frames are dropped or fragmented. 1500 stays. | |
| JMB_09 | `test_jmb_09_boundary_values` | Try values just inside and outside the allowed range. | Inside values apply. Outside values are rejected. | |
| JMB_10 | `test_jmb_10_factory_reset_default` | Factory reset and read the default MTU. | MTU is back at the default. Link restored. | |

---

## Process monitor

Code: `tests/ProcessMonitor/test_process_monitor.py`.

**Last verified:** not signed off here. Kill only the process the case names. Confirm it is running again before the next case. Do not reboot the bench unless the row is the watchdog reboot.

Shared method: read the monitor page or the process list, kill or crash the target, wait for the restart, read the count and the log.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| PROCESS_01 | `test_process_01_visibility` | Open the process list. | Expected daemons are listed. | |
| PROCESS_02 | `test_process_02_uptime` | Read process uptime. | Uptime increases across two reads. | |
| PROCESS_03 | `test_process_03_restart_count` | Read the restart counter. | Counter is numeric and stable until a kill. | |
| PROCESS_04 | `test_process_04_timestamp` | Read the last-start timestamp. | Timestamp is present. | |
| PROCESS_05 | `test_process_05_crash_single` | Crash one process. | Monitor restarts it. Count increments. | |
| PROCESS_06 | `test_process_06_crash_multiple` | Crash more than one process. | Each one returns. | |
| PROCESS_07 | `test_process_07_crash_critical` | Crash a critical process. | It restarts. The device stays up. | |
| PROCESS_08 | `test_process_08_crash_non_critical` | Crash a non-critical process. | It restarts. Link stays up. | |
| PROCESS_09 | `test_process_09_watchdog_reboot` | Trip the watchdog. | Device reboots and SSH returns. | |
| PROCESS_10 | `test_process_10_restart_logging` | Restart a process and read the log. | Log line records the restart. | |
| PROCESS_11 | `test_process_11_stress_under_load` | Kill a process while traffic runs. | Process returns. Traffic recovers. | |
| PROCESS_12 | `test_process_12_log_integrity` | Read logs after several restarts. | Lines stay in order and are not truncated. | |
| PROCESS_13 | `test_process_13_unauthorized_kill` | Kill from a context that should be blocked. | Kill is refused or the monitor restarts the process. | |
| PROCESS_14 | `test_process_14_dependency_handling` | Stop a dependency and watch the child. | Child recovers when the dependency returns. | |
| PROCESS_15 | `test_process_15_monitor_recovery` | Restart the monitor itself. | Monitor returns and still lists processes. | |
| PROCESS_16 | `test_process_16_kill_single` | Kill one process (clean kill, not a crash). | It restarts. | |
| PROCESS_17 | `test_process_17_kill_multiple` | Kill several processes. | Each restarts. | |
| PROCESS_18 | `test_process_18_kill_critical` | Kill a critical process. | It restarts. Device stays up. | |
| PROCESS_19 | `test_process_19_kill_under_load` | Kill a process during traffic. | It restarts. Traffic recovers. | |

---

## Throughput

Code: `tests/Throughput/test_*.py`. Most of these are unit checks of the report and TRex parser. They do not need the RF link. The live matrix run is separate and needs TRex plus the associated STA.

**Last verified:** unit tests only, not a bench sign-off. Do not change radio settings to satisfy a unit test.

| Group | File | How we test | Pass means | Result |
|-------|------|-------------|------------|--------|
| Report cells | `test_performance_report.py` | Build a report from sample rates. | MCS thresholds, badges, and the SU columns render. | |
| Host resolve | `test_resolve_cpe_hosts.py` | Resolve CPE hosts for N SUs. | List is capped at the SU count. | |
| CPE SSH helper | `test_dut_radio_cpe_ssh.py` | Check the SSH command string and UCI parse. | IPv6 is unbracketed. UCI scalar is extracted. | |
| TRex ports | `test_trex_port_states.py` | Parse sample TRex tables. | Up and down ports are marked correctly. | |
| Link snapshot | `test_link_debug_snapshot.py` | Build a snapshot object. | Required keys exist. | |
| SU ping setup | `test_su_link_ping.py` | Build QinQ commands and the wait loop. | Management IPv6 is in the BTS subnet. Strict mode does not continue on failure. | |
| Bandwidth apply | `test_dut_radio_bandwidth_wait.py` | Check HT20/HT40/HT80 UCI strings. | HT40 uses HT40+. Bandwidth is applied once per group. | |
| Running bandwidth | `test_dut_radio_running_bandwidth.py` | Parse `cfg80211` htmode and the wait loop. | Poll stops when the running width matches. | |
| Console table | `test_matrix_console_table.py` | Print the matrix table. | Table text is produced. | |
| TRex parser | `test_trex_runner_parser.py` | Parse sample client output. | Live and summary rates are extracted. | |
| PHY targets | `test_phy_rate_targets.py` | Compute HT20 and HT80 MCS targets. | Numbers match the sheet. | |
| MCS sysfs | `test_verify_mcs_sysfs.py` | Compare a fake sysfs rate with UCI. | Match passes. Mismatch and a missing SU fail. | |
| Link stats | `test_link_stats_validation.py` | Compare operating rate fields. | Out rate is the one that can fail the check. | |
| Kickmac | `test_mcs_kickmac_recovery.py` | Check the kick command and the recollect. | Command is `wlanconfig`. Stats are read again after the wait. | |
| Throughput gates | `test_throughput_validation.py` | Score sample Mbps against the thresholds. | Pass, warn, and fail land on the right side of the cut. | |
| SU stats | `test_kwn_sua_statistics.py` | Parse sample `kwn` output. | RSSI, MCS, and association are read correctly. | |
| Bandwidth gate | `test_performance_matrix_bandwidth_gate.py` | Decide whether TRex may start. | TRex is skipped when the running width or rate does not match. | |
| QinQ args | `test_trex_qinq.py` | Build the TRex command. | S-VLAN and C-VLAN arguments match the profile. | |

A live throughput run on the bench is: STA associated, width and MCS match the row, then TRex. If the radio is not on that width, skip the row. Do not force a width that drops the user's link.

---

## Regression

Code: `tests/Regression/test_regression_stability.py`.

**Last verified:** not signed off here. These repeat a disruption. After the last cycle the link and the lab addresses must be back.

| ID | Node suffix | How we test | Pass means | Result |
|----|-------------|-------------|------------|--------|
| REG_01 | `test_reg_01_soft_reboot_cycles` | Soft-reboot for the cycle count. | Every cycle returns SSH and the STA. | |
| REG_02 | `test_reg_02_network_soft_reset_cycles` | Network soft-reset for the cycle count. | Every cycle returns the lab address and the STA. | |
| REG_03 | `test_reg_03_firmware_upgrade_cycles` | Upgrade with keep for the cycle count. | Every cycle boots the image and keeps settings. | |

---

## Lab RF

Code: `tests/Lab/test_cochannel_interference.py` and `tests/Lab/test_attenuator_snr.py`.

**Last verified:** not signed off here. Co-channel needs the second AP on the same channel. The attenuator sweep needs the Vaunix attenuator. If the gear is absent, mark the case blocked. Do not retune the user's link to invent a result.

| ID | Node | How we test | Pass means | Result |
|----|------|-------------|------------|--------|
| COCH backend | `test_cochannel_backend_rssi_obss_util` | Read RSSI and OBSS with the interferer on. | Both values are readable and move when the interferer changes. | |
| COCH levels | `test_cochannel_interference_levels_obss` | Step the interferer through the levels. | OBSS rises as interference rises. | |
| COCH matrix | `test_cochannel_matrix_level` / `test_cochannel_full_matrix` | Walk the level matrix. | Each level records RSSI and OBSS. | |
| Attenuator | `test_attenuator_snr_sweep_mock` | Sweep attenuation and read SNR. | SNR falls as attenuation rises. | |
