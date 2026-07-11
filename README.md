# UBR Automation TestBed

End-to-end automation framework for **Senao UBR** point-to-multipoint (P2MP) and point-to-point (P2P) validation: GUI sanity, IP networking, jumbo frames, stability regression, process monitoring, 2.4 GHz CPE API, wireless security, ARP/bridge tables, lab attenuator/SNR sweeps, and **TRex throughput matrix** runs with rich HTML reporting.

Designed for repeatable execution on lab benches, locally via pytest, and in **Jenkins** with per-stand agent labels (`TARGET_STAND`).

---

## Table of contents

1. [Software stack](#software-stack)
2. [Architecture — how it works](#architecture--how-it-works)
3. [Project structure](#project-structure)
4. [Test suites and case coverage](#test-suites-and-case-coverage)
5. [Profiles, benches, and testbed](#profiles-benches-and-testbed)
6. [Running tests locally](#running-tests-locally)
7. [Throughput and performance matrix](#throughput-and-performance-matrix)
8. [Reports and artifacts](#reports-and-artifacts)
9. [Jenkins pipelines](#jenkins-pipelines)
10. [Lab instruments](#lab-instruments)
11. [Scripts reference](#scripts-reference)
12. [Documentation](#documentation)

---

## Software stack

### Python and test framework

| Component | Version / notes | Role |
|-----------|-----------------|------|
| **Python** | 3.10+ (README); `setup_env.sh` may use 3.11 on macOS | Runtime |
| **pytest** | 8.1.1 | Test runner, markers, collection hooks |
| **pytest-asyncio** | 0.23.5 | Async tests (`asyncio_mode = auto` in `pytest.ini`) |
| **pytest-xdist** | 3.5.0 | Parallel execution |
| **pytest-order** | 1.2.0 | Ordered suites (API, ProcessMonitor, Radio24, …) |
| **pytest-html** | 4.1.1 | HTML test reports |
| **pytest-json-report** | 1.5.0 | `reports/artifacts/report.json` |
| **pytest-check** | unpinned | Soft assertions |

### Device access and GUI

| Component | Role |
|-----------|------|
| **Playwright** 1.42.0 | LuCI GUI automation (Chromium); `playwright install chromium` |
| **scrapli[asyncio]** | Async SSH to BTS/CPE as `root` / `admin` |
| **asyncssh** | SSH transport layer for scrapli |
| **httpx** | HTTP client (CPE 2.4 GHz REST API) |
| **sshpass** | Sync SSH for TRex remote control and BTS KWN sysfs reads (system package) |
| **pysnmp** + **snmpget** (net-snmp) | VLAN labels, link stats fallback, attenuator SNR |

### Traffic generators

| Tool | Role |
|------|------|
| **TRex** v3.06 | Primary throughput backend — remote on lab hosts (`/opt/v3.06`); `traffic/trex_runner.py` drives `master_script_extended_16SU.py` over SSH |
| **IXIA / ixnetwork_restpy** | Optional benchmark backend via `traffic/throughput_runner.py` (`pip install ixnetwork_restpy requests` — not in `requirements.txt`) |

### Reporting (front-end)

| Component | Role |
|-----------|------|
| **Chart.js** 4.4.1 (CDN) | Live throughput / packet-loss charts in Grafana-style matrix HTML |
| **prettytable** | Console matrix tables during Jenkins runs |

### Lab hardware (optional)

| Component | Role |
|-----------|------|
| **Vaunix LDA-602** + `libLDAhid.so` | USB digital attenuators (2×2 MIMO chains); `instruments/vaunix_lda.py` |
| **TRex servers** | Dual-server topology on qa-lab-02 (`192.168.1.1`, `192.168.1.2`) |

### CI

| Component | Role |
|-----------|------|
| **Jenkins** | Groovy pipelines under `jenkins/`; agent labels per bench |
| **Groovy shared lib** | `jenkins/jenkins-common.groovy` — email, HTML publish, report rename |

Install Python dependencies:

```bash
python3.10 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium
# System (Linux): sshpass, snmp / net-snmp — see setup_env.sh
```

---

## Architecture — how it works

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Jenkins / pytest CLI                                                    │
│  (--profile, --allow-*, TEST_MARKERS, TARGET_STAND)                      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│  conftest.py — session fixtures, gates, reporting hooks                  │
│  testbed_ready → root_ssh → gui_page (Playwright LuCI session)           │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐     ┌─────────────────┐     ┌─────────────────────────┐
│ tests/GUI/    │     │ tests/IP/       │     │ traffic/performance_    │
│ Playwright +  │     │ scrapli SSH +   │     │ matrix.py               │
│ SSH verify    │     │ ping/iperf/ARP  │     │ TRex + DUT radio config │
└───────────────┘     └─────────────────┘     └─────────────────────────┘
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  DUT layer — BTS + CPE over mgmt IPv6 (or IPv4 lab profiles)           │
│  UCI / cfg80211tool / LuCI / BTS KWN sysfs / SNMP                        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Reports — regression_report.py, performance_report.py,                │
│            grafana_matrix_report.py → reports/artifacts/                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### Implementation patterns

1. **Profiles (`profiles/*.yaml`)** — Single source of truth for BTS/CPE IPs, credentials, VLAN/QinQ, TRex servers, `su_count`, MCS wait times, and feature flags. Jenkins resolves profile from `TARGET_STAND` via `config/benches.yaml`.

2. **Session bootstrap (`scripts/bootstrap_testbed.py`, `conftest.py:testbed_ready`)** — Applies mgmt VLAN, QinQ, discovers CPEs from BTS, optional archive restore (`config/BTS.tar.gz`, `config/CPE.tar.gz`). Skip with `--skip-testbed-bootstrap`.

3. **GUI tests** — Playwright drives LuCI; parallel **SSH** reads UCI/`cfg80211tool` to verify backend state matches GUI. Locators in `pages/locators.py`; commands in `pages/commands.py`.

4. **IP tests** — Catalog in `config/ip_test_cases.py` (`IP_01`–`IP_60`); **34 active** cases collected as pytest markers. BTS-only by default; CPE-side runs need `--allow-ip-cpe` or `-k cpe`.

5. **Recovery** — `RecoveryManager` + `profiles/link_formation.yaml` for link re-formation after reboots or factory reset.

6. **Throughput matrix** (`traffic/performance_matrix.py`):
   - Applies **bandwidth once per htmode**, then **MCS per iteration** via `traffic/dut_radio_config.py`
   - **BTS**: bandwidth, DL/UL ratio, MCS (SSH + UCI + `cfg80211tool`)
   - **CPE**: MCS (direct SSH or BTS `remote_exec` when `cpe_via_bts: true`)
   - Post-apply **MCS verify** reads operating MCS from BTS **KWN sysfs** (`/sys/class/kwn/sua{N}/statistics/`)
   - On MCS mismatch: **`kickmac`** disconnect → wait (`mcs_kickmac_wait_s`, default 18s) → re-verify; **TRex still runs** with a warning in console and report
   - **TRex** `stats_check` per cell; combined TX/RX Mbps, live samples, DUT retransmit counters
   - **Link stats** from BTS KWN sysfs (`traffic/kwn_sua_statistics.py`, `traffic/link_stats.py`) on qa-lab-02; SNMP fallback on other profiles

7. **Testbed summary for reports** (`utils/regression_device_info.py`):
   - BTS model from `cat /etc/ademodel`
   - FW from software version command
   - SU mgmt IPv6 from BTS sysfs `sua{N}/statistics/ipv6`
   - VLAN/QoS via SNMP + UCI
   - Compact IP display: `BTS2001:…:1111/120`, `SU12001:…:111b/120`

8. **Reporting** — Collectors in `conftest.py` and matrix runner write JSON + HTML under `reports/artifacts/`. Regression appends iterations unless `--regression-fresh`.

---

## Project structure

| Path | Purpose |
|------|---------|
| `conftest.py` | Global fixtures, CLI options, IP/regression collection hooks |
| `pytest.ini` | Markers, asyncio mode, log level |
| `config/` | `ip_test_cases.py`, `process_test_cases.py`, `benches.yaml`, defaults |
| `tests/GUI/` | LuCI GUI suites (`GUI_01`–`GUI_130` where implemented) |
| `tests/IP/` | IPv4/IPv6 networking (`IP_01`–`IP_36` active) |
| `tests/JumboFrames/` | Jumbo MTU (`JMB_01`–`JMB_10`) |
| `tests/Regression/` | Stability (`REG_01`–`REG_03`) |
| `tests/ProcessMonitor/` | Process watchdog tests (`PROCESS_01`–`19`) |
| `tests/API/` | CPE 2.4 GHz mgmt Wi‑Fi REST API (`API_01`–`API_18`) |
| `tests/Radio24Scan/` | 2.4 GHz radio scan/config (`RADIO_24_01`–`50` subset) |
| `tests/WirelessSecurity/` | Wireless security cases (`W_SECURITY_*`) |
| `tests/ArpBridgeTable/` | ARP & bridge table (`ARPBRIDGE_*`) |
| `tests/Lab/` | Vaunix attenuator + SNR (`--allow-attenuator-lab`) |
| `tests/Throughput/` | **Unit tests** for matrix, TRex parser, Grafana report (no DUT) |
| `traffic/` | `performance_matrix.py`, `trex_runner.py`, `dut_radio_config.py`, `link_stats.py`, `kwn_sua_statistics.py`, `throughput_runner.py` |
| `utils/` | Flow helpers, `regression_report.py`, `performance_report.py`, `grafana_matrix_report.py`, `regression_device_info.py` |
| `pages/` | Playwright locators + `RootCommands` SSH templates |
| `profiles/` | Per-lab YAML (`default`, `qa_lab_02`, `ipv4_quickrun`, …) |
| `instruments/` | Vaunix LDA control + SNR sweep |
| `vendor/vaunix_linux_sdk/` | Build `libLDAhid.so` |
| `jenkins/` | Groovy pipelines + `trigger-*.sh` |
| `scripts/` | Bootstrap, factory provision, report builders, coverage Excel |
| `docs/` | Testbed setup, validation flowcharts, architecture HTML, lab instruments |
| `reports/artifacts/` | Generated HTML, JSON, CSV, traces (gitignored content) |

---

## Test suites and case coverage

### Summary

| Suite | Directory | Marker(s) | Implemented IDs | Gate flag |
|-------|-----------|-----------|-----------------|-----------|
| **GUI** | `tests/GUI/` | `sanity`, submodule markers | **73** (`GUI_01`–`GUI_130`, gaps in plan) | Runs by default |
| **IP** | `tests/IP/` | `IP`, `IP_XX`, `IPv4`, `IPv6` | **34 active** / 60 catalog | `--allow-ip-suite` |
| **Jumbo** | `tests/JumboFrames/` | `JumboFrames` | **10** (`JMB_01`–`JMB_10`) | `JMB_07`/`JMB_10` need `--allow-destructive-jumbo` |
| **Regression** | `tests/Regression/` | `Regression` | **3** (`REG_01`–`REG_03`) | `--allow-regression` |
| **ProcessMonitor** | `tests/ProcessMonitor/` | `ProcessMonitor` | **19** (`PROCESS_01`–`19`) | `--allow-process-monitor` |
| **CPE API 2.4G** | `tests/API/` | `CPE_API_24` | **18** (`API_01`–`API_18`) | CPE mgmt Wi‑Fi setup |
| **Radio 2.4G scan** | `tests/Radio24Scan/` | `Radio24Scan` | **19** (`RADIO_24_*`) | Shares API conftest |
| **Wireless security** | `tests/WirelessSecurity/` | `WirelessSecurity` | **8** (`W_SECURITY_*`) | sanity |
| **ARP / Bridge** | `tests/ArpBridgeTable/` | `ArpBridgeTable` | **10** (`ARPBRIDGE_*`) | sanity |
| **Lab attenuator** | `tests/Lab/` | `attenuator` | **1** mock sweep | `--allow-attenuator-lab` |
| **Throughput unit** | `tests/Throughput/` | *(none)* | **~73** unit tests | No DUT required |

### GUI (`tests/GUI/`) — 73 tests

| File | Submodule | Case IDs |
|------|-----------|----------|
| `test_summary.py` | Summary | `GUI_01`–`GUI_04` |
| `test_topPanel.py` | TopPanel | `GUI_05`–`GUI_10` |
| `test_radio_properties.py` | WirelessProperties, DDRS | `GUI_17`–`GUI_26`, `GUI_28`, `GUI_29` |
| `test_radio_24.py` | Wireless24 | `GUI_34`–`GUI_39` |
| `test_network.py` | Network | `GUI_50`–`GUI_55`, `GUI_70`–`GUI_78` |
| `test_management.py` | Management | `GUI_63`–`GUI_68` |
| `test_monitor.py` | Monitor | `GUI_83`–`GUI_84`, `GUI_88`–`GUI_92`, `GUI_105`–`GUI_118`, `GUI_127`–`GUI_128`, `GUI_130` |

**Not automated in repo:** `GUI_11`–`16`, `GUI_27`, `GUI_30`–`33`, `GUI_40`–`49`, `GUI_56`–`62`, `GUI_69`, `GUI_79`–`82`, `GUI_85`–`87`, `GUI_93`–`104`, `GUI_119`–`126`, `GUI_129`, `GUI_131+`.

### IP (`tests/IP/test_IP.py`) — 34 active cases

- **Catalog:** `config/ip_test_cases.py` — `IP_01`–`IP_60`
- **Active:** `IP_01`–`IP_09`, `IP_11`–`IP_17` (IPv4); `IP_18`–`IP_28`, `IP_30`–`IP_34` (IPv6); `IP_35`, `IP_36` (dual-stack)
- **Manual / excluded:** `IP_10`, `IP_29` (PDU hard reboot)
- **Planned only:** `IP_37`–`IP_60`

```bash
pytest tests/IP/ -m IP -v --allow-ip-suite --profile ipv6_quickrun
pytest tests/IP/ -m IPv4 -v --allow-ip-suite --profile ipv4_quickrun
pytest tests/IP/ -m IP_05 -v --allow-ip-suite
```

### Jumbo (`JMB_01`–`JMB_10`)

ICMP validation across MTU 1500–9000, VLAN, P2MP, boundary values. **`JMB_07`** (reboot persistence) and **`JMB_10`** (factory reset) are destructive.

### Regression (`REG_01`–`REG_03`)

| ID | Behavior |
|----|----------|
| `REG_01` | N-cycle soft reboot; ping + web login after each |
| `REG_02` | N-cycle network soft reset (`/etc/init.d/network reload`) |
| `REG_03` | N-cycle firmware upgrade (needs `--firmware-image`) |

### ProcessMonitor (`PROCESS_01`–`19`)

Process watchdog, procd, memory/CPU stress. Destructive cases `PROCESS_09`, `PROCESS_15` need `--allow-destructive-process`. Uses dedicated BTS IP `192.168.2.1` in suite conftest.

### CPE 2.4 GHz API (`API_01`–`API_18`)

REST API against CPE at `169.254.254.1` after PC joins hidden mgmt Wi‑Fi (`utils/cpe_api_24_*`).

### Radio24Scan (`RADIO_24_*`)

2.4 GHz channel scan, DHCP, encryption — 19 cases mapped to product plan `2.4G_RADIO_XX`.

### WirelessSecurity, ArpBridgeTable

Wireless security policy cases and ARP/bridge learn-table validation (GUI + SSH flows).

---

## Profiles, benches, and testbed

### Bench registry (`config/benches.yaml`)

| `TARGET_STAND` | Profile | Description |
|----------------|---------|-------------|
| `test-harman2` | `default` | P2P Harman bench |
| `test-qa-lab-02` / `qa-lab-02` | `qa_lab_02` | PTMP 6-SU throughput lab; Jenkins agent on `10.0.150.102` |

### Profile files (`profiles/`)

| Profile | Use |
|---------|-----|
| `default.yaml` | Baseline IPv6 mgmt VLAN, QinQ 100/101, GUI/regression |
| `qa_lab_02.yaml` | 6 CPEs, dual TRex servers, `link_stats_source: ssh`, `su_count: 6`, `mcs_kickmac_wait_s: 18` |
| `ipv4_lab.yaml` | IPv4 GUI/link (`192.168.2.x`) |
| `ipv6_lab.yaml` | IPv6 monitor/GUI |
| `ipv4_quickrun.yaml` | Fast IPv4 IP suite (`IP_01`–`IP_17`); Jenkins ProcessMonitor / IPv4 |
| `ipv6_quickrun.yaml` | Fast IPv6 IP suite (`IP_18`–`IP_26`); Jenkins default for `IP` marker |
| `link_formation.yaml` | Recovery after reset — QinQ, archive restore |
| `factory_provision.yaml` | Factory reset → installer → AIRTEL SSID path |

### Default IPv6 testbed (`profiles/default.yaml`)

Factory defaults: **BTS = QinQ** (S-VLAN **100**, C-VLAN **101**), **CPE = transparent**. Bootstrap is SSH/UCI only (no SNMP for VLAN apply).

| Role | Typical IPv6 |
|------|----------------|
| BTS | `2401:4900:d0:40d4:0:17b8:0:330` |
| CPE | discovered or `…:331` |
| BTS PC | `…:301` |
| CPE PC | `…:302` |

See [docs/testbed-setup.md](docs/testbed-setup.md).

```bash
PYTHONPATH=. python3 scripts/bootstrap_testbed.py          # SSH bootstrap
PYTHONPATH=. python3 scripts/bootstrap_testbed.py --with-gui  # + archive restore via Playwright
```

---

## Running tests locally

### GUI

```bash
venv/bin/python -m pytest tests/GUI/ -v
venv/bin/python -m pytest tests/GUI/ -v -k "Summary"
venv/bin/python -m pytest tests/GUI/test_radio_24.py -v
```

### Jumbo, Regression, ProcessMonitor, API, other suites

```bash
pytest tests/JumboFrames/ -v
pytest tests/JumboFrames/ -v --allow-destructive-jumbo -k "JMB_07 or JMB_10"

pytest tests/Regression/ -v --allow-regression --regression-fresh -k REG_01 \
  --regression-iterations-reg01 2

pytest tests/ProcessMonitor/ -v --allow-process-monitor
pytest tests/API/ -v
pytest tests/Radio24Scan/ -v
pytest tests/WirelessSecurity/ -v
pytest tests/ArpBridgeTable/ -v
```

### IP suite flags

| Flag | Purpose |
|------|---------|
| `--allow-ip-suite` | Required to run IP tests |
| `--allow-ip-destructive` | Reboot, network reload, interface flap |
| `--allow-ip-cpe` | Include CPE-side parametrized runs |
| `--no-ip-stop-on-first-fail` | Continue after failure (Jenkins default) |
| `--fallback-ip` | Try `10.0.0.1` when mgmt IPv6 is down |

### Key global CLI options

| Option | Purpose |
|--------|---------|
| `--local-ipv6` / `--remote-ipv6` | BTS / CPE addresses |
| `--profile` / `--recovery-profile` | Active and recovery YAML |
| `--skip-testbed-bootstrap` | Skip session bootstrap |
| `--factory-provision` | Factory provision path |
| `--allow-regression` | Enable REG tests |
| `--regression-fresh` | New regression HTML (don't append) |
| `--firmware-image` | Path for `REG_03` |
| `--allow-attenuator-lab` | Enable lab attenuator test |

### Session fixtures (root `conftest.py`)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `testbed_ready` | session | Bootstrap mgmt VLAN, CPE discovery |
| `profile_bundle` | session | Load profile + recovery YAML |
| `recovery_manager` | session | Link/device recovery |
| `root_ssh` | session | Async SSH `root` to BTS |
| `gui_page` | session | Logged-in Playwright LuCI page |
| `run_ip` | function | `await run_ip("IP_05", "bts")` helper |

### Coverage workbook

```bash
python3 scripts/generate_automation_coverage.py
# → reports/artifacts/Automation_Coverage_May18.xlsx
```

---

## Throughput and performance matrix

### Overview

`traffic/performance_matrix.py` sweeps **bandwidth × MCS × DL/UL ratio**, configures the DUT, runs **TRex stats-check**, validates link rates, and writes JSON/CSV/HTML reports.

**Fixed matrix ratio:** 75:25 DL:UL (configurable per profile).

**Dynamic targets (default):** from `traffic/operating_rate_table.py` (product spec sheet) × efficiency factor (default 70%). Example: HT20 + MCS23 → 286 Mbps operating rate → ~200 Mbps effective target at 75% efficiency.

### DUT configuration flow

1. **Bandwidth** applied once per htmode group (`HT20`, `HT40`, `HT80`, …)
2. Wait for all SUs to link (`su_link_wait_s`, `bandwidth_running_wait_s`)
3. **MCS** applied per iteration — BTS + all CPEs
4. **Verify operating MCS** via BTS KWN sysfs (`read_operating_mcs_by_sua_slot`)
5. On mismatch: `kickmac` → wait `mcs_kickmac_wait_s` → re-read sysfs
6. **Continue TRex** even on persistent mismatch; report flags `mcs_mismatch_note`
7. TRex run; grade RX against effective target

### Link stats source

| Profile | Source |
|---------|--------|
| `qa_lab_02` | **SSH** — BTS `/sys/class/kwn/sua{N}/statistics/*` (MAC, IPv6, Tx/Rx rate, MCS, SNR, RSSI) |
| Others | SNMP and/or SSH via `traffic/link_stats.py` (`link_stats_source: auto`) |

### TRex topology (qa-lab-02)

- **BSU server** `192.168.1.1` — ports 1–3 (BTS + 3 CPE)
- **SU server** `192.168.1.2` — ports 0–2 (3 CPE)
- **6 SUs** total; QinQ tags from profile (`svlan`/`cvlan` 200/201)

### Run locally

```bash
# Dry-run (print matrix, no traffic)
PYTHONPATH=. python3 traffic/performance_matrix.py --dry-run

# Full matrix
PYTHONPATH=. python3 traffic/performance_matrix.py --profile qa_lab_02 --stand test-qa-lab-02 --time 30

# Subset
PYTHONPATH=. python3 traffic/performance_matrix.py \
  --stand test-qa-lab-02 --bandwidths HT80 --mcs MCS22,MCS23 --time 30

# Full BW × MCS0–MCS23 (72 cells on 3 bandwidths)
PYTHONPATH=. python3 traffic/performance_matrix.py \
  --stand test-qa-lab-02 \
  --bandwidths HT20,HT40,HT80 \
  --mcs MCS0,MCS1,MCS2,MCS3,MCS4,MCS5,MCS6,MCS7,MCS8,MCS9,MCS10,MCS11,MCS12,MCS13,MCS14,MCS15,MCS16,MCS17,MCS18,MCS19,MCS20,MCS21,MCS22,MCS23
```

### Jenkins trigger

```bash
TARGET_STAND=test-qa-lab-02 ./jenkins/trigger-throughput.sh \
  --bandwidth HT20,HT40,HT80 --mcs MCS23

# Full matrix example
MCS_LIST=$(python3 -c 'print(",".join(f"MCS{i}" for i in range(24)))')
TARGET_STAND=test-qa-lab-02 ./jenkins/trigger-throughput.sh \
  --bandwidth HT20,HT40,HT80 --mcs "$MCS_LIST"
```

Auth: `JENKINS_USER` + `~/.jenkins-api-token`. Default URL: `http://127.0.0.1:8081`.

### IXIA / standalone TRex

```bash
# IXIA benchmark (throughput_runner.py)
python3 traffic/throughput_runner.py --traffic-mode benchmark --ixia-ip 10.0.150.50 ...

# TRex stats-check only
TREX_PASSWORD='...' python3 traffic/throughput_runner.py \
  --traffic-mode stats_check --trex-server 192.168.3.3 --trex-ports 0,1 \
  --cpes 1 --ratio 50:50 --time 30
```

---

## Reports and artifacts

All outputs land under `reports/artifacts/` unless overridden.

### Regression

| Artifact | Description |
|----------|-------------|
| `Regression_Report.html` | Merged dashboard; appends iterations unless `--regression-fresh` |
| `regression_pytest.html` | Pytest HTML when `--allow-regression` |
| `Customer_Summary_<timestamp>.csv` | Customer export |
| `testbed_summary.json` | BTS/CPE model, FW, IP, VLAN, QoS |

### Performance matrix — two HTML styles

| Report | Module | Contents |
|--------|--------|----------|
| **Classic** `Performance_Report_<timestamp>.html` | `utils/performance_report.py` | Benchmark table, MCS + QAM + Mbps, operating-rate mismatch highlighting, per-SU TRex stats |
| **Grafana-style** `Performance_Report_<timestamp>_Grafana.html` | `utils/grafana_matrix_report.py` | Light-theme dashboard: testbed table (model from `/etc/ademodel`, SU **MAC** from sysfs), **link stats table** (Tx/Rx rate, SNR, RSSI — no Rx MCS column), KPI cards, **BW×MCS coverage matrix** (TX sent → RX got), **Chart.js** live throughput + packet loss, iteration log with TX/RX Mbps |

Also per run:

- `performance_<timestamp>/performance_matrix_summary.json` — full matrix payload
- `performance_<timestamp>/performance_matrix_summary.csv`
- `Throughput_<BW>_<MCS>_<Mode>_<ratio>.json` per iteration

Regenerate Grafana HTML from saved JSON:

```bash
PYTHONPATH=. python3 scripts/build_grafana_report_from_matrix_json.py \
  reports/artifacts/performance_<timestamp>/performance_matrix_summary.json \
  -o reports/artifacts/Performance_Report_Grafana.html \
  --report-id 87 --stand test-qa-lab-02 --profile qa_lab_02
```

Sample: `docs/samples/Senao_Performance_84_Grafana_Report.html`

### Unified / Jenkins report names

| Job | Renamed artifact |
|-----|------------------|
| AutomationFramework | `Senao_UBR_<build>_Report_<date>.html` |
| Throughput | `Senao_Performance_<build>_Report_<date>.html` (+ Grafana HTML when matrix exports it) |
| Regression | `Senao_Regression_<build>_Report_<date>.html` |

---

## Jenkins pipelines

| Pipeline file | Jenkins job | Purpose |
|---------------|-------------|---------|
| `jenkins/jenkins-AutomationFramework` | Automation Framework / Automation Test Cases | Unified GUI, IP, Regression, Jumbo, ProcessMonitor |
| `jenkins/jenkins-Throughput` | Automation Framework / Throughput Test - Trex | Performance matrix or VLAN debug campaign |
| `jenkins/jenkins-Regression` | Standalone regression | REG_01–03 checkboxes |
| `jenkins/jenkins-VlanLinkDebug` | VLAN Link Debug Campaign | QinQ vs transparent recovery |
| `jenkins/jenkins-common.groovy` | Shared | Email, `publishHTML`, report copy |

### Trigger scripts

| Script | Job |
|--------|-----|
| `jenkins/trigger-build.sh` | Automation Test Cases (`TEST_MARKERS`, `TEST_FILTER`) |
| `jenkins/trigger-throughput.sh` | Throughput Test - Trex |
| `jenkins/trigger-vlan-debug-campaign.sh` | VLAN Link Debug Campaign |
| `jenkins/run-local.sh` | Local mirror of AutomationFramework pipeline |

### Unified job — `TEST_MARKERS`

| Marker | Path | Auto profile |
|--------|------|--------------|
| `GUI` | `tests/GUI/` | `default` |
| `IP` | `tests/IP/` | `ipv6_quickrun` (or `ipv4_quickrun` for IPv4-only filter) |
| `Regression` | `tests/Regression/` | `default` |
| `JumboFrames` | `tests/JumboFrames/` | `default` |
| `ProcessMonitor` | `tests/ProcessMonitor/` | `ipv4_quickrun` |

**Examples**

| Goal | `TEST_MARKERS` | `TEST_FILTER` |
|------|----------------|---------------|
| GUI smoke | `GUI` | `Summary, TopPanel` |
| Full IP | `IP` | *(empty)* |
| One case | `IP` | `IP_18` |
| GUI + IP | `GUI,IP` | `Summary` + `RUN_IP_CPE=true` for CPE cases |

### Throughput job parameters

`TARGET_STAND`, `Bandwidth`, `MCS`, `DL:UL Ratio`, `Throughput Test Time`, `Packet Size`, `SU Count`, TRex server overrides, VLAN debug campaign flags.

---

## Lab instruments

### Automated in Python

| Instrument | Module | Notes |
|------------|--------|-------|
| **Vaunix LDA-602** (×2) | `instruments/vaunix_lda.py`, `instruments/attenuator_snr.py` | USB attenuation + BTS SNR readback; mock backend for CI |

```bash
sudo PYTHONPATH=. python3 scripts/attenuator_snr.py sweep --start-db 0 --stop-db 40 --step-db 10
PYTHONPATH=. pytest tests/Lab/ -v --allow-attenuator-lab --attenuator-backend mock
```

Build SDK: `./scripts/build_vaunix_linux_sdk.sh` → `vendor/vaunix_linux_sdk/lib/libLDAhid.so`

### Documented bench equipment (manual / future automation)

Catalog in [docs/lab-instruments.html](docs/lab-instruments.html) (built by `scripts/build_lab_instruments.py`):

- TRex traffic generators
- Ixia / Ostinato
- Vaunix LDA-608V attenuators
- TTI PSA6005 spectrum analyzer
- VNA485 vector network analyzer
- Tektronix TPS2024B oscilloscope

---

## Scripts reference

| Script | Purpose |
|--------|---------|
| `scripts/bootstrap_testbed.py` | Mgmt VLAN + CPE discovery bootstrap |
| `scripts/factory_provision.py` | Factory reset → basic config |
| `scripts/run_qa_lab_trex.sh` | Start/kill dual TRex on qa-lab-02 |
| `scripts/vlan_link_debug_campaign.py` | VLAN link-recovery campaign |
| `scripts/build_grafana_report_from_matrix_json.py` | Grafana HTML from matrix JSON |
| `scripts/build_sample_grafana_report.py` | Local Grafana preview |
| `scripts/build_lab_instruments.py` | Lab instruments HTML doc |
| `scripts/generate_automation_coverage.py` | Excel vs Senao test plan |
| `scripts/wait_link_run_radio24.py` | Wait for link, run Radio24Scan |
| `traffic/deploy_trex_script.py` | SCP TRex client script to server |

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/testbed-setup.md](docs/testbed-setup.md) | VLAN, IPv6, bootstrap, lab wiring |
| [docs/testcase-validation-flows.md](docs/testcase-validation-flows.md) | Per-case validation flowcharts |
| [docs/lab-instruments.html](docs/lab-instruments.html) | Bench instrument catalog |
| [docs/architecture-plan.html](docs/architecture-plan.html) | Architecture overview |
| [docs/architecture-diagram-automation.html](docs/architecture-diagram-automation.html) | Automation diagram |

---

## Quick reference

| Metric | Count |
|--------|------:|
| GUI automated tests | 73 |
| IP active cases | 34 |
| Jumbo | 10 |
| Regression | 3 |
| ProcessMonitor | 19 |
| CPE API 2.4G | 18 |
| Radio24Scan | 19 |
| WirelessSecurity | 8 |
| ArpBridgeTable | 10 |
| Throughput unit tests | ~73 |

**Regenerate coverage after adding tests:** `python3 scripts/generate_automation_coverage.py`

**Run throughput unit tests (no lab):** `PYTHONPATH=. pytest tests/Throughput/ -q`
