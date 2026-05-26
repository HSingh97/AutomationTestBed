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
| `tests/GUI/` | GUI suites (`GUI_01`–`GUI_112` where implemented) |
| `tests/JumboFrames/` | Jumbo suite (`JMB_01`–`JMB_10`) |
| `tests/Regression/` | Stability regression (`REG_01`–`REG_03`) |
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
| **GUI** | **52** | Most suites | See gaps below |
| **Jumbo** | **10** | `JMB_01`–`JMB_06`, `JMB_08`–`JMB_09` | `JMB_07`, `JMB_10` destructive, opt-in |
| **Regression** | **3** | `REG_01`, `REG_02` | `REG_03` needs firmware image |
| **Throughput** | Script only | Manual / Jenkins | Not counted in 65 product cases |
| **Total product cases in automation** | **65** | | |

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

- `GUI_88` – Management Timezone Random Validation  
- `GUI_89` – Management NTP Full Cycle  
- `GUI_90` – Sync with Browser Time  
- `GUI_91` – Management Logging IP and Port  
- `GUI_92` – Management Temperature Logging Cycle  
- `GUI_93` – Management Location Configuration  

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

Regression reports (default single file `reports/Regression_Report.html`; all runs/iterations append unless `--regression-fresh`):

- Fixed **Testbed Summary** table (Model, FW Version, IP, VLAN, QoS for BTS/CPE)  
- **Iteration 1+** only (baseline hidden)  
- Per-iteration **Ping / Web Access** checkmark table (BTS | CPE columns)  

---

### Pending — not automated or needs more validation

#### GUI IDs not implemented in this repo

These product IDs are **not** present under `tests/GUI/` (gaps in numbering vs a full manual test plan):

- `GUI_11`–`GUI_16`  
- `GUI_56`–`GUI_69`  
- `GUI_79`–`GUI_87`  
- `GUI_94`–`GUI_104`  

(Quick Start / other sections may exist in the product spec but are not automated here.)

#### Implemented but environment-dependent or lightly validated

| Case | Status |
|------|--------|
| `GUI_01` | CPU vs GUI tolerance can fail under load; may need tuning |
| `GUI_25`, `GUI_26` | CPE DDRS page; GUI dropdown vs SSH only (no TRex in GUI job) |
| `GUI_27` | Requires reachable remote CPE |
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

## Default IPv6 Testbed

From `profiles/default.yaml` (overridable via CLI):

- BTS: `2401:4900:d0:40d4:0:17b8:0:330`  
- CPE: `2401:4900:d0:40d4::17b8:0:331`  
- BTS PC: `2401:4900:d0:40d4::17b8:0:301`  
- CPE PC: `2401:4900:d0:40d4::17b8:0:302`  

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
```

### Jumbo Frames

```bash
venv/bin/python -m pytest tests/JumboFrames/ -v
venv/bin/python -m pytest tests/JumboFrames/ -v --allow-destructive-jumbo -k "JMB_07 or JMB_10"
```

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

- Regression dashboard: `reports/Regression_Report.html` (one merged file per `--regression-fresh` run; append without `--regression-fresh`)  
- Pytest HTML (auto when `--allow-regression`): `reports/regression_pytest.html`  
- Customer CSV: `reports/Customer_Summary_<timestamp>.csv`  
- Performance matrix: `logs/Performance_Report_<timestamp>.html` (artifacts in `logs/performance_<timestamp>/`)  

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

Sweeps all bandwidth/MCS/ratio combinations. **BTS** gets bandwidth + DL/UL ratio + MCS over SSH; **CPE** gets MCS only (direct SSH to `remote_ipv6s`). Then TRex runs per case. Outputs: JSON + CSV under `logs/performance_<timestamp>/`, HTML at `logs/Performance_Report_<timestamp>.html`.

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

Each run produces `Throughput_<BW>_<MCS>_<Mode>_<ratio>.json`, `performance_matrix_summary.json/csv` under `logs/performance_<timestamp>/`, and `logs/Performance_Report_<timestamp>.html` (testbed summary, pass/fail chips, per-iteration cards — same style as regression).

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
| `--regression-report PATH` | Single HTML report (default `reports/Regression_Report.html`) |
| `--regression-fresh` | Clear shared state; one new merged report for this run |
| `--firmware-image PATH` | Image for `REG_03` |
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

- **Filter:** `TEST_FILTER` (comma = OR), e.g. `Summary, TopPanel, WirelessProperties` or `test_gui`
- **Profile:** `PROFILE_NAME`, `RECOVERY_PROFILE_NAME`, optional `Local IPv6 Address`

### 2. Regression

- **Checkboxes:** Soft Reboot (`REG_01`), Network Soft Reset (`REG_02`), Firmware Upgrade (`REG_03`)
- **Iterations:** separate count per enabled test (`ITERATIONS_SOFT_REBOOT`, `ITERATIONS_SOFT_RESET`, `ITERATIONS_FIRMWARE`)
- **Report:** one merged `reports/Regression_Report.html` per build (`--regression-fresh`); Jenkins copies to `Senao_Regression_<build>_Report_<date>.html`
- **Optional:** `Local IPv6 Address`, **upload** `FIRMWARE_IMAGE` file (required when firmware upgrade is enabled)

### 3. Throughput

- **Params:** BTS IP, CPE IP, Bandwidth, MCS, packet size, DL:UL ratio, duration, TRex server

Create three separate Jenkins jobs, each pointing at the matching pipeline file above.

---

## Quick Reference

**65 automated product test cases** are implemented in this repository: **52 GUI + 10 Jumbo + 3 Regression**.

**Remaining work** is mainly: unnumbered GUI product IDs (`GUI_11`–`16`, `56`–`69`, etc.), deeper lab sign-off on conditional cases (TRex, destructive jumbo, firmware upgrade), and framework/reporting polish—not the core GUI/Jumbo/REG flows already coded.
