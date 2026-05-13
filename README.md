# UBR Automation TestBed

Automation framework for UBR validation with:
- GUI sanity/regression checks (Playwright + Pytest)
- IXIA IxNetwork throughput and latency validation
- Jenkins pipelines for scheduled and parameterized execution

## Tech Stack

- Python 3.10
- `pytest`, `pytest-asyncio`
- `playwright`
- `scrapli` (SSH command execution)
- `ixnetwork_restpy` (IXIA automation)
- Jenkins Pipeline (Groovy)

## Project Structure

- `conftest.py`  
  Central fixtures and runtime options (`--local-ipv6`, `--remote-ipv6`, `--profile`, `--recovery-profile`, `--allow-destructive-jumbo`, etc.).
- `tests/GUI/`  
  GUI test suites:
  - `test_summary.py`
  - `test_topPanel.py`
  - `test_radio_properties.py`
  - `test_network.py`
  - `test_management.py`
- `tests/JumboFrames/`
  Jumbo frame suite:
  - `test_jumbo_frames.py` (JMB_01 ... JMB_10)
- `traffic/throughput_runner.py`  
  Shared framework throughput entrypoint for IXIA benchmark or TRex stats-check execution.
- `traffic/trex_stats_check.py`
  Shared lightweight TRex sanity/stats runner.
- `profiles/`
  Profile-driven runtime defaults:
  - `default.yaml`
  - `link_formation.yaml`
- `utils/profile_manager.py`
  Profile loading, validation, and CLI override merge.
- `utils/recovery_manager.py`
  Shared link health checks and recovery metrics tracking.
- `traffic/trex_runner.py`
  TRex stats-check runner abstraction.
- `pages/commands.py`  
  Shared backend command templates, including bandwidth and MCS sequence helpers.
- `jenkins/jenkins-AutomationFramework`  
  Jenkins pipeline for GUI automation.

## What Is Already Done

### GUI Automation

- Summary page validation flows implemented.
- Top panel validations implemented.
- Radio properties lifecycle validations implemented (status, SSID, bandwidth, channel, encryption, max CPE).
- Network validations implemented (IP config, gateway/netmask/fallback, Ethernet, DHCP main and 2.4GHz).
- Management validations implemented (timezone, NTP, browser-time sync, logging config, temperature logging, location tab).
- Jumbo frame validations implemented in dedicated suite (`tests/JumboFrames`), with destructive cases opt-in.
- Consolidated fixtures for SSH + GUI login in `conftest.py`.
- Jenkins GUI pipeline available (`jenkins-AutomationFramework`) with report publishing/email.
- Profile-driven execution and centralized recovery framework integrated.

### Implemented Jumbo Test Case Index

- `JMB_01` - Configure Jumbo + disable back to default
- `JMB_02` - Configure MTU 9000
- `JMB_03` - Min/mid MTU cycle + ICMP validation
- `JMB_04` - Max MTU 9000 + ICMP validation
- `JMB_05` - Jumbo with management VLAN/interface checks
- `JMB_06` - Jumbo MTU 9000 + ICMP validation (aligned with `JMB_04`)
- `JMB_07` - Reboot persistence (**destructive; gated**)
- `JMB_08` - MTU 1500 validation
- `JMB_09` - Boundary and invalid MTU validation
- `JMB_10` - Factory reset default MTU (**destructive; gated**)

Jumbo execution status: **JMB_01 through JMB_10 implemented and validated**.

## Default IPv6 Testbed Configuration

These values are now defaulted in profiles and CLI for consistent runs:

- **BTS / DUT IPv6**: `2401:4900:d0:40d4:0:17b8:0:330`
- **CPE IPv6**: `2401:4900:d0:40d4::17b8:0:331`
- **BTS PC IPv6**: `2401:4900:d0:40d4::17b8:0:301`
- **CPE PC IPv6**: `2401:4900:d0:40d4::17b8:0:302`

### Implemented GUI Test Case Index (Done So Far)

- `GUI_01` - Summary System
- `GUI_02` - Summary Network
- `GUI_03` - Summary Performance
- `GUI_04` - Summary Wireless
- `GUI_05` - Top Panel Logo
- `GUI_06` - Top Panel Parameters
- `GUI_07` - Top Panel Radio Redirect
- `GUI_08` - Home and Apply Buttons
- `GUI_09` - Reboot Device
- `GUI_10` - Logout
- `GUI_17` - Radio Status
- `GUI_18` - SSID
- `GUI_19` - Bandwidth
- `GUI_20` - Channel
- `GUI_21` - Encryption
- `GUI_22` - Max CPE
- `GUI_50` - Network IP Configuration
- `GUI_51` - Edit IP Configuration
- `GUI_52` - Edit Netmask Configuration
- `GUI_53` - Edit Gateway Configuration
- `GUI_54` - Edit Fallback IP
- `GUI_55` - Edit Fallback Netmask
- `GUI_70` - Ethernet Speed/Duplex
- `GUI_71` - Ethernet MTU
- `GUI_72` - DHCP Server Status
- `GUI_73` - DHCP Lease Time
- `GUI_74` - DHCP 2.4GHz Radio IP
- `GUI_75` - DHCP 2.4GHz Radio Netmask
- `GUI_76` - DHCP 2.4GHz Radio DHCP Status
- `GUI_77` - DHCP 2.4GHz Radio Pool Range
- `GUI_78` - DHCP 2.4GHz Radio Lease Time
- `GUI_88` - Management Timezone Random Validation
- `GUI_89` - Management NTP Full Cycle
- `GUI_90` - Sync with Browser Time
- `GUI_91` - Management Logging IP/Port
- `GUI_92` - Management Temperature Logging Cycle
- `GUI_93` - Management Location Configuration

### Throughput Automation

- Shared throughput entrypoint is `traffic/throughput_runner.py`.
- Supports traffic modes via ratios:
  - Downlink (`100:0`)
  - Uplink (`0:100`)
  - Bidirectional (for input ratios, default includes `80:20`)
- Collects throughput + latency + loss and exports JSON for Jenkins.
- Generates IXIA PDF report from Python script output.
- MCS iteration applies backend settings per loop using command helpers:
  - disable DDRS
  - set spatial stream
  - set DDRS rate
  - apply (and remote apply for connected SU)
- MCS-based traffic cap logic added to prevent over-driving low MCS profiles.

## Scripts You Can Run

## 1) GUI suites (local run)

Create environment and install dependencies:

```bash
python3.10 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt pytest-check pytest-json-report
venv/bin/playwright install chromium
```

Run full GUI suite:

```bash
venv/bin/python -m pytest tests/GUI/ -v
```

Run Jumbo Frames suite:

```bash
venv/bin/python -m pytest tests/JumboFrames/ -v
```

Run only summary tests:

```bash
venv/bin/python -m pytest tests/GUI/ -v -k "Summary"
```

Run only top panel tests:

```bash
venv/bin/python -m pytest tests/GUI/ -v -k "TopPanel"
```

Run only wireless properties tests:

```bash
venv/bin/python -m pytest tests/GUI/ -v -k "WirelessProperties"
```

Run with custom DUT details:

```bash
venv/bin/python -m pytest tests/GUI/ -v \
  --local-ipv6 2401:4900:d0:40d4:0:17b8:0:330 \
  --remote-ipv6 2401:4900:d0:40d4::17b8:0:331 \
  --username root \
  --password "Sen@0ubRNwk$"
```

Run destructive jumbo cases (reboot/factory reset):

```bash
venv/bin/python -m pytest tests/JumboFrames/test_jumbo_frames.py -v \
  --allow-destructive-jumbo \
  -k "JMB_07 or JMB_10"
```

## 2) Throughput script (shared runner)

```bash
python3.10 traffic/throughput_runner.py \
  --mode keep \
  --cpes 16 \
  --target 800 \
  --ratio 80:20 \
  --time 15 \
  --ixia-ip 10.0.150.50 \
  --local-ip 2401:4900:d0:40d4:0:17b8:0:330 \
  --packet-size imix \
  --bandwidth HT80 \
  --mcs-rate MCS7 \
  --spatial-stream 2 \
  --ddrs-rate MCS7 \
  --radio-index 1 \
  --output-json current_ixia_run.json \
  --profile default \
  --recovery-profile link_formation \
  --traffic-mode benchmark
```

Notes:
- `--target` is treated as aggregate throughput across all CPEs.
- Ratio split is applied first, then divided by CPE count.
- Example: `800` with `80:20` and `16` CPE => `640 DL / 160 UL`; per-CPE is `40 / 10`.

## Jenkins Pipelines

## 1) GUI Pipeline

- File: `jenkins/jenkins-AutomationFramework`
- Purpose: execute GUI tests by filter and publish customer reports.

Key parameter:
- `TEST_FILTER` (example: `Summary, TopPanel, WirelessProperties, JumboFrames`)
- `Local IPv6 Address`
- `PROFILE_NAME`
- `RECOVERY_PROFILE_NAME`
- `ENABLE_DESTRUCTIVE_JUMBO` (set true only for `JMB_07`/`JMB_10`)

Jumbo from Jenkins examples:

```text
TEST_FILTER=JumboFrames
ENABLE_DESTRUCTIVE_JUMBO=false
```

```text
TEST_FILTER=JMB_07 or JMB_10
ENABLE_DESTRUCTIVE_JUMBO=true
```

## Current Status

- Core GUI automation: **stable and runnable**.
- Throughput automation: **implemented and runnable** with looping, mode coverage, and telemetry capture.
- Jenkins integration: **in place** for GUI use-cases.
- Command standardization: **started** via `pages/commands.py` helper methods for throughput radio configuration sequence.

## Work In Progress

- Full command centralization cleanup:
  - Additional hardcoded command strings can still be moved into shared command helpers.
- Throughput report polish:
  - HTML iteration matrix is present; further UI refinement/grouping is still possible for readability at large matrix sizes.
- Pipeline hardening:
  - Add stronger validation/guardrails for unsupported bandwidth/MCS combinations and environment-specific command keys.
- Documentation growth:
  - Add environment prerequisites page (IXIA server requirements, DUT firmware assumptions, and known limits).

## Known Notes

- There are two similarly named GUI pipeline files (`jenkins-AutomationFramework` and `jenkins_AutomationFramework`); standardize to one active file to avoid confusion.
- GUI pipeline expects `TARGET_STAND` to be available in Jenkins environment.
- Factory reset / profile-restore flows depend on DUT boot timing; use the destructive toggle only when the bench is reserved.

