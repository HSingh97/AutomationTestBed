# UBR Automation TestBed

Automation framework for UBR P2MP validation:

- **GUI** sanity and regression (Playwright + Pytest + SSH backend checks)
- **Jumbo Frame** MTU validation (GUI + SSH + ICMP)
- **Stability regression** (reboot, network reload, firmware upgrade cycles)
- **Throughput** hooks (IXIA benchmark / TRex stats-check)
- **Jenkins** parameterized GUI pipeline

## Tech Stack

- Python 3.10
- `pytest`, `pytest-asyncio`, `pytest-html`, `pytest-json-report`
- `playwright`, `scrapli`, `httpx`
- `ixnetwork_restpy` (IXIA)
- Jenkins Pipeline (Groovy)

## Project Structure

| Path | Purpose |
|------|---------|
| `conftest.py` | Shared fixtures, CLI options, regression/GUI reporting hooks |
| `tests/GUI/` | GUI suites (`GUI_01`–`GUI_112` where implemented, incl. `test_radio_24.py`) |
| `scripts/generate_automation_coverage.py` | Build `reports/artifacts/Automation_Coverage_May18.xlsx` from test plan |
| `instruments/` | Vaunix LDA-602 control + SNMP SNR (`attenuator_snr.py`) |
| `vendor/vaunix_linux_sdk/` | Vaunix `libLDAhid.so` + rebuild sources (`LDAhid.c`/`h` only) |
| `scripts/attenuator_snr.py` | Lab CLI: sweep, verify, parallel streams, SNMP monitor |
| `tests/Lab/` | Attenuator + SNR lab tests (`--allow-attenuator-lab`) |
| `tests/JumboFrames/` | Jumbo suite (`JMB_01`–`JMB_10`) |
| `tests/Regression/` | Stability regression (`REG_01`–`REG_03`) |
| `tests/IP/test_IP.py` | IPv4/IPv6 networking (`IP_01`–`IP_37`, BTS & CPE); run with `-m IP` |
| `config/ip_test_cases.py` | IP case catalog and markers |
| `utils/ip_test_flows.py` | SSH/GUI flows for IP validation |
| `tests/Throughput/` | TRex parser/unit helper (not a product GUI case) |
| `traffic/` | IXIA/TRex throughput runners |
| `utils/` | Flow helpers, recovery, regression report, device info |
| `pages/` | Locators and SSH command templates |
| `profiles/` | `default.yaml`, `link_formation.yaml` |
| `docs/testcase-validation-flows.md` | Per-case validation flowcharts |
| `jenkins/jenkins-AutomationFramework` | GUI test-case Jenkins pipeline |
| `jenkins/jenkins-Regression` | Stability regression Jenkins pipeline |
| `jenkins/jenkins-Throughput` | TRex throughput Jenkins pipeline |
| `jenkins/jenkins-common.groovy` | Shared email/HTML publish helpers |

## Test Case Status Summary

| Category | Automated in repo | Lab-validated (typical) | Notes |
|----------|------------------|-------------------------|--------|
| **GUI** | **58** | Most suites | See gaps below |
| **Jumbo** | **10** | `JMB_01`–`JMB_06`, `JMB_08`–`JMB_09` | `JMB_07`, `JMB_10` destructive, opt-in |
| **Regression** | **3** | `REG_01`, `REG_02` | `REG_03` needs firmware image |
| **IP** | **37** (73 runs) | Ping, gateway, ARP/ND, MTU, dual-stack | iperf/backup/firmware/power cases need profile flags or opt-in |
| **Throughput** | Script only | Manual / Jenkins | Not counted in product pytest IDs |
| **Total product cases in automation** | **108** | | GUI+Jumbo+Regression+IP; see coverage spreadsheet |

### Completed — automated and in test plan

#### Summary (4)

- `GUI_01` – Summary System  
- `GUI_02` – Summary Network  
- `GUI_03` – Summary Performance  
- `GUI_04` – Summary Wireless  

#### Top Panel (6)

- `GUI_05` – Top Panel Logo  
- `GUI_06` – Top Panel Parameters  
- `GUI_07` – Top Panel Radio Redirect  
- `GUI_08` – Home and Apply Buttons  
- `GUI_09` – Reboot Device (control visibility; does not reboot in sanity)  
- `GUI_10` – Logout  

#### Wireless Properties (13)

- `GUI_17` – Radio Status  
- `GUI_18` – SSID  
- `GUI_19` – Bandwidth  
- `GUI_20` – Channel  
- `GUI_21` – Encryption  
- `GUI_22` – Max CPE  
- `GUI_23` – DL/UL Ratio  
- `GUI_24` – DDRS Status (Enable/Disable options + dependent dropdown visibility/MCS ranges; Save per step)  
- `GUI_25` – Spatial Stream configure Single/Dual + SSH  
- `GUI_26` – Modulation Index configure sample MCS + SSH  
- `GUI_27` – ATPC Status (remote CPE)  
- `GUI_28` – Transmit Power  
- `GUI_29` – Maximum EIRP  

#### 2.4 GHz Radio — Wireless (6)

(`tests/GUI/test_radio_24.py`, `utils/radio24_flows.py`, page `/admin/wireless/radio0`)

Each case reads the **current UCI/GUI value as baseline**, applies a test value, verifies GUI + SSH, then **restores the baseline**.

- `GUI_34` – 2.4 GHz Radio Status (Enable / Disable)  
- `GUI_35` – 2.4 GHz SSID  
- `GUI_36` – 2.4 GHz Bandwidth (20 / 40 MHz)  
- `GUI_37` – 2.4 GHz Configured & Active Channel (Auto-only)  
- `GUI_38` – 2.4 GHz Encryption (None / WPA2-PSK)  
- `GUI_39` – 2.4 GHz Encryption Key  

#### Network (15)

- `GUI_50` – Network IP Configuration  
- `GUI_51` – Edit IP Configuration  
- `GUI_52` – Edit Netmask Configuration  
- `GUI_53` – Edit Gateway Configuration  
- `GUI_54` – Edit Fallback IP  
- `GUI_55` – Edit Fallback Netmask  
- `GUI_70` – Ethernet Speed and Duplex  
- `GUI_71` – Ethernet MTU  
- `GUI_72` – DHCP Server Status  
- `GUI_73` – DHCP Lease Time  
- `GUI_74` – DHCP 2.4 GHz Radio IP  
- `GUI_75` – DHCP 2.4 GHz Radio Netmask  
- `GUI_76` – DHCP 2.4 GHz Radio DHCP Status  
- `GUI_77` – DHCP 2.4 GHz Radio Pool Range  
- `GUI_78` – DHCP 2.4 GHz Radio Lease Time  

#### Management (6)

- `GUI_63` – Management Timezone Random Validation  
- `GUI_64` – Management NTP Full Cycle  
- `GUI_65` – Sync with Browser Time  
- `GUI_66` – Management Logging IP and Port  
- `GUI_67` – Management Temperature Logging Cycle  
- `GUI_68` – Management Location Configuration  

#### Monitor (8)

- `GUI_105` – Monitor Learn Table Bridge Table  
- `GUI_106` – Monitor Learn Table Bridge Refresh and Clear  
- `GUI_107` – Monitor Learn Table ARP Table  
- `GUI_108` – Monitor Learn Table ARP Refresh and Clear  
- `GUI_109` – Monitor System Logs Config Logs and Refresh  
- `GUI_110` – Monitor System Logs Device Logs and Refresh  
- `GUI_111` – Monitor System Logs Temperature Logs, Refresh, and Clear  
- `GUI_112` – Monitor System Logs System Logs and Refresh  

#### Jumbo Frames (10)

- `JMB_01` – Configure Jumbo and disable back to default  
- `JMB_02` – Configure MTU 9000  
- `JMB_03` – Min and mid MTU cycle with ICMP validation  
- `JMB_04` – Max MTU 9000 with ICMP validation  
- `JMB_05` – Jumbo with management VLAN and interface checks  
- `JMB_06` – Jumbo MTU 9000 with P2MP validation  
- `JMB_07` – Reboot persistence (**destructive**, `--allow-destructive-jumbo`)  
- `JMB_08` – MTU 1500 validation  
- `JMB_09` – Boundary and invalid MTU validation  
- `JMB_10` – Factory reset default MTU (**destructive**, `--allow-destructive-jumbo`)  

#### Stability Regression (3)

- `REG_01` – N-cycle **soft reboot**; after each cycle: BTS↔CPE ping + BTS/CPE web login  
- `REG_02` – N-cycle **network soft reset** (`/etc/init.d/network reload` on CPE then BTS); same health checks  
- `REG_03` – N-cycle **firmware upgrade** (GUI flash); same health checks; requires `--firmware-image`  

Regression reports (default single file `reports/artifacts/Regression_Report.html`; all runs/iterations append unless `--regression-fresh`):

- Fixed **Testbed Summary** table (Model, FW Version, IP, VLAN, QoS for BTS/CPE)  
- **Iteration 1+** only (baseline hidden)  
- Per-iteration **Ping / Web Access** checkmark table (BTS | CPE columns)  

---

### Pending — not automated or needs more validation

#### GUI IDs not implemented in this repo

These product IDs are **not** present under `tests/GUI/` (gaps in numbering vs a full manual test plan):

- `GUI_11`–`GUI_16` (Quick Start)  
- `GUI_30`–`GUI_33`, `GUI_40`–`GUI_49` (other wireless pages; `GUI_34`–`GUI_39` are done)  
- `GUI_56`–`GUI_62`, `GUI_69` (Tools / extras; `GUI_63`–`GUI_68` Management are done)  
- `GUI_79`–`GUI_87`  
- `GUI_94`–`GUI_104`  

(Quick Start / other sections may exist in the product spec but are not automated here.)

#### Implemented but environment-dependent or lightly validated

| Case | Status |
|------|--------|
| `GUI_01` | CPU vs GUI tolerance can fail under load; may need tuning |
| `GUI_25`, `GUI_26` | CPE DDRS page; GUI dropdown vs SSH only (no TRex in GUI job) |
| `GUI_27` | Requires reachable remote CPE |
| `GUI_39` | Skipped when 2.4 GHz encryption is `None` (run after `GUI_38` or with WPA2-PSK enabled) |
| `JMB_07`, `JMB_10` | Destructive; skipped unless `--allow-destructive-jumbo` |
| `REG_03` | Implemented; needs firmware file path and reserved bench run |
| Regression QoS row | Collector may show `—` until QoS UCI/SNMP mapping is finalized |

#### Framework / tooling (not product test IDs)

- Throughput matrix reporting polish (`traffic/throughput_runner.py`)  
- Jenkins pipeline guardrails (bandwidth/MCS combinations)  
- Command centralization cleanup (remaining hardcoded paths)  
- Standardize duplicate Jenkins files: `jenkins-AutomationFramework` vs `jenkins_AutomationFramework`  
- Lab prerequisite doc (reservations, boot timing, firmware assumptions)  

---

## Default IPv6 Testbed (mgmt VLAN)

Factory defaults: **BTS = QinQ** (S-VLAN **100**, C-VLAN **101**, mgmt `vlan.ath1.mgmtvlan` **101**), **CPE = untagged/transparent**. Bootstrap uses **SSH/UCI only** (no SNMP). Lab PCs: BTS port stacked QinQ `enp3s0.100.101`, CPE port native untagged. All tests use mgmt IPv6 only.

From `profiles/default.yaml` → `testbed.mgmt_vlan` (overridable via CLI):

- BTS: `2401:4900:d0:40d4:0:17b8:0:330`  
- CPE: discovered from BTS or `2401:4900:d0:40d4::17b8:0:331`  
- BTS PC: `2401:4900:d0:40d4::17b8:0:301`  
- CPE PC (secondary bench): `2401:4900:d0:40d4::17b8:0:302`  

See [docs/testbed-setup.md](docs/testbed-setup.md). Bootstrap: `PYTHONPATH=. python3 scripts/bootstrap_testbed.py` (add `--with-gui` to load `config/BTS.tar.gz` / `config/CPE.tar.gz`). Pytest: omit `--skip-testbed-bootstrap` (default on).

---

## How to Run

### Setup

```bash
python3.10 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt pytest-check pytest-json-report
venv/bin/playwright install chromium
```

### GUI (full or filtered)

```bash
venv/bin/python -m pytest tests/GUI/ -v
venv/bin/python -m pytest tests/GUI/ -v -k "Summary"
venv/bin/python -m pytest tests/GUI/ -v -k "WirelessProperties"
venv/bin/python -m pytest tests/GUI/test_radio_24.py -v
venv/bin/python -m pytest tests/GUI/ -v -k "Wireless24"
```

### Vaunix LDA-602 attenuator (2×2 chains) + SNR

Two **LDA-602** units (one per MIMO chain) on the automation PC. Set attenuation and optional **Wi-Fi channel** (maps to LDA working frequency), then read **link SNR** from the BTS over SNMP (`traffic/link_stats.py`).

**Profile** (`profiles/default.yaml` → `attenuator:`): enable, SDK path, per-chain serial numbers, settle time, SNMP radio index.

**SDK:** `vendor/vaunix_linux_sdk/lib/libLDAhid.so` (build from `src/LDAhid.c` via `./scripts/build_vaunix_linux_sdk.sh`). USB control needs **sudo**. Without hardware, use `--backend mock`.

```bash
# Sweep 0→40 dB → CSV
sudo PYTHONPATH=. python3 scripts/attenuator_snr.py sweep --start-db 0 --stop-db 40 --step-db 10

# Live verify (step list + summary table)
sudo PYTHONPATH=. python3 scripts/attenuator_snr.py verify --steps-db 0,10,20,30,40,0

# Both MIMO streams (parallel chain control + per-stream SNR)
sudo PYTHONPATH=. python3 scripts/attenuator_snr.py parallel --stream-att-db 40

# Mock backend (no USB)
PYTHONPATH=. python3 scripts/attenuator_snr.py sweep --backend mock --start-db 0 --stop-db 10 --step-db 5
```

**From Python:**

```python
from instruments import AttenuatorSnrController
from utils.profile_manager import load_profile_bundle

bundle = load_profile_bundle("default")
with AttenuatorSnrController.from_profile(bundle.active) as att:
    att.set_link(channel=36, att_chain0_db=0, att_chain1_db=0)
    rows = att.sweep_attenuation(channel=36, start_db=0, stop_db=20, step_db=2)
    att.assert_snr_decreases_with_attenuation(rows)
```

**Pytest (mock):** `PYTHONPATH=. pytest tests/Lab/ -v --allow-attenuator-lab --attenuator-backend mock`

### Automation coverage report (vs May18 test plan)

```bash
python3 scripts/generate_automation_coverage.py
# Output: reports/artifacts/Automation_Coverage_May18.xlsx
# Sheets: Summary, GUI Progress, All Cases, Roadmap, Automation Index (pytest file + Jenkins job per case)
```

### Jumbo Frames

```bash
venv/bin/python -m pytest tests/JumboFrames/ -v
venv/bin/python -m pytest tests/JumboFrames/ -v --allow-destructive-jumbo -k "JMB_07 or JMB_10"
```

### IP validation (`IP_01`–`IP_37`)

Catalog in `config/ip_test_cases.py`. Most cases run on **BTS** and **CPE**; **`IP_01`–`IP_05` are BTS-only** (local DUT). Configure addresses in `profiles/default.yaml` → `ip_tests:`.

For BTS-only remote ping/throughput (`IP_03`, `IP_05`), set `ip_tests.use_cpe_peer: false` and `ip_tests.remote_ping_host` (lab PC or gateway) — not the CPE management IP.

```bash
# Collect (68 tests with IP_01–IP_05 BTS-only; was 73 when those were duplicated on CPE)
venv/bin/python -m pytest tests/IP/ --collect-only -q

# Full IP suite (marker IP on every case)
venv/bin/python -m pytest tests/IP/ -m IP -v --allow-ip-suite --profile ipv4_quickrun

# IPv4 functional block (BTS only): IP_01–IP_05
venv/bin/python -m pytest tests/IP/ -v --allow-ip-suite --profile ipv4_quickrun \
  -k "IP_01 or IP_02 or IP_03 or IP_04 or IP_05"

# Safe functional/validation (ping, gateway, ARP, IPv6 ND, dual-stack)
venv/bin/python -m pytest tests/IP/ -v --allow-ip-suite -k "IP_02 or IP_19 or IP_20 or IP_37"

# Destructive (reboot, network reload, interface flap) — lab only
venv/bin/python -m pytest tests/IP/ -v --allow-ip-suite --allow-ip-destructive -k "IP_09 or IP_11"

# Optional: set in profile to un-skip throughput / backup / firmware cases
# ip_tests.iperf_server_v4 / iperf_server_v6
# ip_tests.backup_archive_path
# ip_tests.firmware_image_path
```

Flags: `--allow-ip-suite` (required), `--allow-ip-destructive` (reboot/reset/flap). GUI static-IP/MTU cases use the BTS LuCI session only.

Reachability: SSH and Web UI try the profile/CLI **fallback** management IP (`ip_tests.fallback_ipv4` or `--fallback-ip`, default `10.0.0.1`) when the primary address is down. Remote ping cases **confirm local ping first**, then ping the peer.

### Stability Regression

```bash
# Soft reboot (2 cycles)
venv/bin/python -m pytest tests/Regression/ -v --allow-regression --regression-fresh \
  -k REG_01 --regression-iterations-reg01 2

# Network soft reset (2 cycles)
venv/bin/python -m pytest tests/Regression/ -v --allow-regression --regression-fresh \
  -k REG_02 --regression-iterations-reg02 2

# Reboot + soft reset in one merged report (different iteration counts)
venv/bin/python -m pytest tests/Regression/ -v --allow-regression --regression-fresh \
  -k "REG_01 or REG_02" --regression-iterations-reg01 2 --regression-iterations-reg02 5

# Firmware upgrade (provide image)
venv/bin/python -m pytest tests/Regression/ -v --allow-regression --regression-fresh \
  -k REG_03 --regression-iterations-reg03 1 --firmware-image /path/to/firmware.bin
```

Reports:

- Regression dashboard: `reports/artifacts/Regression_Report.html` (one merged file per `--regression-fresh` run; append without `--regression-fresh`)  
- Pytest HTML (auto when `--allow-regression`): `reports/artifacts/regression_pytest.html`  
- Customer CSV: `reports/artifacts/Customer_Summary_<timestamp>.csv`  
- Performance matrix: `reports/artifacts/Performance_Report_<timestamp>.html` (artifacts in `reports/artifacts/performance_<timestamp>/`)  

### Throughput (IXIA example)

```bash
python3.10 traffic/throughput_runner.py \
  --mode keep --cpes 16 --target 800 --ratio 80:20 --time 15 \
  --ixia-ip 10.0.150.50 \
  --local-ip 2401:4900:d0:40d4:0:17b8:0:330 \
  --profile default --recovery-profile link_formation \
  --traffic-mode benchmark
```

### Throughput (TRex stats-check)

The TRex client script lives in `traffic/scripts/master_script_extended_16SU.py`. Deploy it once, then run stats-check mode (server + client over SSH, ports via `TREX_PORTS`):

```bash
# Deploy bundled script to the TRex host (192.168.3.3 by default)
python3 traffic/deploy_trex_script.py --trex-server 192.168.3.3 --trex-password '<password>'

# Lightweight throughput / counter validation
TREX_PASSWORD='<password>' python3 traffic/throughput_runner.py \
  --traffic-mode stats_check \
  --trex-server 192.168.3.3 \
  --trex-ports 0,1 \
  --cpes 1 --ratio 50:50 --target 400 --time 30 \
  --trex-run-mode counter_check \
  --output-json trex_results.json

# Or deploy + run in one step
python3 traffic/trex_stats_check.py --deploy-client-script --time 30 --expected-min-mbps 0
```

`trex_runner.py` exports `TREX_PORTS` to the remote script; use `--ports 0,1` on the script directly when running manually.

### Performance matrix (bandwidth × MCS × DL/UL ratio)

Sweeps all bandwidth/MCS/ratio combinations. **BTS** gets bandwidth + DL/UL ratio + MCS over SSH; **CPE** gets MCS only (direct SSH to `remote_ipv6s`). Then TRex runs per case. Outputs: JSON + CSV under `reports/artifacts/performance_<timestamp>/`, HTML at `reports/artifacts/Performance_Report_<timestamp>.html`.

**Dynamic targets (default):** rates come from the product spec sheet (`traffic/operating_rate_table.py`, Dual column). Example: HT20 + MCS23 → **286 Mbps** operating rate; at 75% efficiency and 75:25 → **~215 Mbps** → **161M DL + 54M UL**.

**Report:** fetches SNMP link stats (Tx/Rx rate, SNR) and renders the benchmark table layout. Rows turn **red** when operating rate ≠ spec for the configured MCS.

```bash
# Preview the full matrix (no traffic)
PYTHONPATH=. python3 traffic/performance_matrix.py --dry-run

# Full matrix (default: HT20–HT160, MCS0–MCS11, DL/UL/Uplink/Bidi ratios)
PYTHONPATH=. python3 traffic/performance_matrix.py --profile default --time 30

# Subset example
PYTHONPATH=. python3 traffic/performance_matrix.py \
  --bandwidths HT80,HT160 --mcs MCS5,MCS7,MCS9 --ratios 80:20 --time 15
```

Each run produces `Throughput_<BW>_<MCS>_<Mode>_<ratio>.json`, `performance_matrix_summary.json/csv` under `reports/artifacts/performance_<timestamp>/`, and `reports/artifacts/Performance_Report_<timestamp>.html` (testbed summary, pass/fail chips, per-iteration cards — same style as regression).

---

## Key CLI Options

| Option | Purpose |
|--------|---------|
| `--local-ipv6` | BTS IPv6 |
| `--remote-ipv6` | CPE IPv6 (comma-separated) |
| `--profile` | Active profile (`default`) |
| `--recovery-profile` | Recovery profile (`link_formation`) |
| `--allow-destructive-jumbo` | Enable `JMB_07`, `JMB_10` |
| `--allow-regression` | Enable `REG_01`–`REG_03` |
| `--regression-iterations N` | Default cycle count when per-case options unset |
| `--regression-iterations-reg01 N` | Soft reboot cycles |
| `--regression-iterations-reg02 N` | Network soft reset cycles |
| `--regression-iterations-reg03 N` | Firmware upgrade cycles |
| `--regression-report PATH` | Single HTML report (default `reports/artifacts/Regression_Report.html`) |
| `--regression-fresh` | Clear shared state; one new merged report for this run |
| `--firmware-image PATH` | Image for `REG_03` |
| `--allow-attenuator-lab` | Enable `tests/Lab/test_attenuator_snr.py` |
| `--attenuator-backend` | `auto` \| `dll` \| `mock` for Vaunix LDA |
Profile regression block (`profiles/default.yaml`):

```yaml
regression:
  iterations: 3
  ping_count: 5
  web_timeout_seconds: 45
  web_retry_count: 5
  web_retry_interval_seconds: 12
  web_post_ping_delay_seconds: 5
  network_reload_wait_seconds: 30
  reboot_wait_seconds: 150
  reboot_via_gui: false
  firmware_image: ""
```

---

## Documentation

- Validation flowcharts: [docs/testcase-validation-flows.md](docs/testcase-validation-flows.md)

---

## Jenkins (three pipelines)

All jobs run on agent label **`TARGET_STAND`** (lab bench). Reports share the same layout: Senao hero header, logo, **Testbed Summary** (BTS/CPE model/FW/IP/VLAN/QoS), then run-specific results.

| Job file | Purpose | Standard HTML artifact |
|----------|---------|------------------------|
| `jenkins/jenkins-AutomationFramework` | GUI test cases (`tests/GUI/`) | `Senao_GUI_<build>_Report_<date>.html` (+ CSV) |
| `jenkins/jenkins-Regression` | Stability regression (`tests/Regression/`, `--allow-regression`) | `Senao_Regression_<build>_Report_<date>.html` |
| `jenkins/jenkins-Throughput` | TRex performance matrix (`traffic/performance_matrix.py`) | `Senao_Performance_<build>_Report_<date>.html` |

Shared helpers: `jenkins/jenkins-common.groovy` (email, `publishHTML`, report copy/rename).

### 1. GUI test cases

- **Filter:** `TEST_FILTER` (comma = OR), e.g. `Summary, TopPanel, WirelessProperties, Wireless24` or `GUI_34`
- **Note:** Jumbo tests live under `tests/JumboFrames/` — run locally or extend the pipeline path; destructive cases need `--allow-destructive-jumbo`
- **Profile:** `PROFILE_NAME`, `RECOVERY_PROFILE_NAME`, optional `Local IPv6 Address`

### 2. Regression

- **Checkboxes:** Soft Reboot (`REG_01`), Network Soft Reset (`REG_02`), Firmware Upgrade (`REG_03`)
- **Iterations:** separate count per enabled test (`ITERATIONS_SOFT_REBOOT`, `ITERATIONS_SOFT_RESET`, `ITERATIONS_FIRMWARE`)
- **Report:** one merged `reports/artifacts/Regression_Report.html` per build (`--regression-fresh`); Jenkins copies to `Senao_Regression_<build>_Report_<date>.html`
- **Optional:** `Local IPv6 Address`, **upload** `FIRMWARE_IMAGE` file (required when firmware upgrade is enabled)

### 3. Throughput

- **Params:** BTS IP, CPE IP, Bandwidth, MCS, packet size, DL:UL ratio, duration, TRex server

Create three separate Jenkins jobs, each pointing at the matching pipeline file above.

---

## Quick Reference

**71 automated product test cases** are implemented in this repository: **58 GUI + 10 Jumbo + 3 Regression**.

**Remaining work** is mainly: other GUI product IDs (`GUI_11`–`16`, `GUI_30`–`33`, `GUI_40`–`49`, `GUI_56`–`62`, etc.), deeper lab sign-off on conditional cases (TRex, destructive jumbo, firmware upgrade), and framework/reporting polish—not the core GUI/Jumbo/REG flows already coded.

Regenerate the coverage workbook after adding tests: `python3 scripts/generate_automation_coverage.py`.
