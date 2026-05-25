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
  - `test_monitor.py` (GUI_83 Radio Stats, GUI_127/128/130 Link Test Tool)
- `tests/JumboFrames/`
  Jumbo frame suite:
  - `test_jumbo_frames.py` (JMB_01 ... JMB_10)
- `tests/Throughput/test_throughput.py`  
  IXIA benchmark or TRex stats-check execution + throughput/latency collection + JSON/PDF outputs.
- `profiles/`
  Profile-driven runtime defaults:
  - `default.yaml`
  - `link_formation.yaml`
- `utils/profile_manager.py`
  Profile loading, validation, and CLI override merge.
- `utils/recovery_manager.py`
  Shared link health checks and recovery metrics tracking.
- `utils/traffic/trex_runner.py`
  TRex stats-check runner abstraction.
- `pages/commands.py`  
  Shared backend command templates, including bandwidth and MCS sequence helpers.
- `jenkins/jenkins-AutomationFramework`  
  Jenkins pipeline for GUI automation.
- `jenkins/jenkins-Throughput`  
  Jenkins pipeline for throughput automation loops (Bandwidth/MCS/Ratio/Mode).

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
- `GUI_83` - Monitor Radio 1 Statistics Link (Index, System Name, IP/IPv6, Uptime, SNR, Rate, Throughput)
- `GUI_84` - Monitor Radio 1 Statistics Link CPE IP hyperlink opens CPE web GUI in new tab
- `GUI_88` - Monitor Radio 1 Link Detailed Statistics Back button returns to RF Link Statistics
- `GUI_89` - Monitor Radio 1 Link Detailed Statistics Disconnect briefly drops RF link
- `GUI_90` - Monitor Radio 1 Link Detailed Statistics Clear resets link traffic counters
- `GUI_91` - Monitor Radio 1 Link Detailed Statistics identity (IP, MAC, Name, GPS, SNR, Noise)
- `GUI_92` - Monitor Radio 1 Link Detailed Statistics performance (Power, Rate, Throughput, Packets, RTX, Firmware)
- `GUI_105` - Monitor Learn Table Bridge (MAC/local/age vs brctl; All/LAN1/LAN2/Radio1 filters)
- `GUI_106` - Monitor Learn Table Bridge Refresh and Clear
- `GUI_107` - Monitor Learn Table ARP vs /proc/net/arp
- `GUI_108` - Monitor Learn Table ARP Refresh and Clear
- `GUI_113` - Monitor Tools Diagnostics Ping (reachable CPE + unreachable IP)
- `GUI_114` - Monitor Tools Diagnostics Traceroute to CPE
- `GUI_115` - Monitor Tools Diagnostics Packet Capture (Radio 1, pcap file)
- `GUI_116` - Monitor Tools Diagnostics Console (help command)
- `GUI_117` - Monitor Tools Diagnostics Cable Length vs backend
- `GUI_118` - Monitor Tools Diagnostics LLDP neighbors table
- `GUI_127` - Monitor Tools Link Test Tool (bandwidth, duration, VLAN range + GUI/UCI verify)
- `GUI_128` - Monitor Tools Link Test Tool (add CPE from dropdown)
- `GUI_130` - Monitor Tools Link Test Tool (start test, validate results vs backend/tester)

### Throughput Automation

- IXIA REST-based throughput runner is implemented in `tests/Throughput/test_throughput.py`.
- Supports traffic modes via ratios:
  - Downlink (`100:0`)
  - Uplink (`0:100`)
  - Bidirectional (for input ratios, default includes `80:20`)
- Collects throughput + latency + loss and exports JSON for Jenkins.
- Generates IXIA PDF report from Python script output.
- Jenkins throughput pipeline loops all combinations of:
  - Bandwidth
  - MCS
  - DL/UL ratio profiles
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

Run Monitor cases (IPv4 lab, ordered 83 → 84 → 127 → 128 → 130):

```bash
python3 -m pytest tests/GUI/test_monitor.py -m GUI_83 --profile=ipv4_lab \
  --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v
```

**IPv6 lab** (BTS/CPE on IPv6 only — use `default`, `ipv6_lab`, or `--profile=default` with `--local-ipv6` / `--remote-ipv6`):

```bash
python3 -m pytest tests/GUI/test_monitor.py -m "GUI_83 or GUI_84" \
  --profile=ipv6_lab \
  --local-ipv6=2401:4900:d0:40d4:0:17b8:0:330 \
  --remote-ipv6=2401:4900:d0:40d4::17b8:0:331 \
  --recovery-profile=link_formation -v

# Full Monitor suite on IPv6
python3 -m pytest tests/GUI/test_monitor.py \
  --profile=ipv6_lab \
  --local-ipv6=2401:4900:d0:40d4:0:17b8:0:330 \
  --remote-ipv6=2401:4900:d0:40d4::17b8:0:331 \
  --recovery-profile=link_formation -v
```

Monitor flows auto-detect IPv6 (profile `ip_mode: ipv6`, bracketed LuCI URLs, compressed address matching). **GUI_107 ARP** is IPv4-oriented; on IPv6-only stacks the CPE may appear in the **Bridge** table only (ARP check is informational).

### Monitor scope (BTS only)

All Monitor GUI cases run on the **BTS** (`--local-ip` / `gui_page` + `root_ssh`). Use `--remote-ip` (or profile `remote_ips`) as the **linked peer CPE** for link stats, ping, ARP, link test, etc. **GUI_84** briefly opens the CPE web UI in a new browser tab to verify the hyperlink — that is not a separate CPE device test pass.

Run Monitor Link Test Tool cases (ordered 127 → 128 → 130):

```bash
# All three in sequence (~6 min)
python3 -m pytest tests/GUI/test_monitor.py -m "GUI_84" \
  --profile=ipv4_lab --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v

python3 -m pytest tests/GUI/test_monitor.py -m "GUI_88 or GUI_89 or GUI_90" \
  --profile=ipv4_lab --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v

python3 -m pytest tests/GUI/test_monitor.py -m "GUI_91 or GUI_92" \
  --profile=ipv4_lab --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v

python3 -m pytest tests/GUI/test_monitor.py -m "GUI_105 or GUI_106 or GUI_107 or GUI_108" \
  --profile=ipv4_lab --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v

python3 -m pytest tests/GUI/test_monitor.py -m "GUI_113 or GUI_114 or GUI_115 or GUI_116 or GUI_117 or GUI_118" \
  --profile=ipv4_lab --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v

python3 -m pytest tests/GUI/test_monitor.py -m "GUI_127 or GUI_128 or GUI_130" \
  --profile=ipv4_lab --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v

# Individual cases
python3 -m pytest tests/GUI/test_monitor.py -m GUI_127 --profile=ipv4_lab --local-ip=192.168.2.10 -v
python3 -m pytest tests/GUI/test_monitor.py -m GUI_128 --profile=ipv4_lab \
  --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v
python3 -m pytest tests/GUI/test_monitor.py -m GUI_130 --profile=ipv4_lab \
  --local-ip=192.168.2.10 --remote-ip=192.168.2.11 -v
```

Link Test settings: **CLI > profile `link_test` > defaults**. Optional external tester JSON for GUI_130:

```bash
python3 -m pytest tests/GUI/test_monitor.py -m GUI_130 --profile=ipv4_lab \
  --link-test-reference-json=reports/tester_link_results.json ...
```

Example `tester_link_results.json`:

```json
{
  "ul_throughput": 100,
  "dl_throughput": 120,
  "ul_latency": 3,
  "dl_latency": 25
}
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

## 2) Throughput script (standalone run)

```bash
python3.10 tests/Throughput/test_throughput.py \
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

## 2) Throughput Pipeline

- File: `jenkins/jenkins-Throughput`
- Purpose: execute full matrix for throughput validation and build detailed HTML output.

Key parameters include:
- `Bandwidth`
- `MCS`
- `Ratios`
- `Ixia Tool IP`
- `Target Total Throughput Mbps`
- `No of CPE`
- `Packet Size`
- `Spatial Stream`
- `DDRS Rate`
- `PROFILE_NAME`
- `RECOVERY_PROFILE_NAME`
- `TRAFFIC_MODE`
- `TRAFFIC_BACKEND`
- `TREX_SERVER`

## Current Status

- Core GUI automation: **stable and runnable**.
- Throughput automation: **implemented and runnable** with looping, mode coverage, and telemetry capture.
- Jenkins integration: **in place** for both GUI and throughput use-cases.
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

