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
  Central fixtures and runtime options (`--local-ip`, `--remote-ip`, `--username`, `--password`).
- `tests/GUI/`  
  GUI test suites:
  - `test_summary.py`
  - `test_topPanel.py`
  - `test_radio_properties.py`
- `tests/Throughput/test_throughput.py`  
  IXIA traffic generation + throughput/latency collection + JSON/PDF outputs.
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
- Consolidated fixtures for SSH + GUI login in `conftest.py`.
- Jenkins GUI pipeline available (`jenkins-AutomationFramework`) with report publishing/email.

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
  --local-ip 192.168.2.230 \
  --remote-ip 192.168.2.231 \
  --username root \
  --password "Sen@0ubRNwk$"
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
  --local-ip 192.168.2.230 \
  --packet-size imix \
  --bandwidth HT80 \
  --mcs-rate MCS7 \
  --spatial-stream 2 \
  --ddrs-rate MCS7 \
  --radio-index 1 \
  --output-json current_ixia_run.json
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
- `TEST_FILTER` (example: `Summary, TopPanel, WirelessProperties`)

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

