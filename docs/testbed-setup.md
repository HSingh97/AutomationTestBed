# Testbed setup — rigid mgmt IPv6 model

## Design principles

| Layer | Access method | Used for |
|-------|---------------|----------|
| **BTS / CPE (devices)** | **Mgmt IPv6 only** (`testbed.mgmt_vlan.ipv6_*`) | SSH, LuCI, all pytest cases, regression |
| **BTS lab PC / CPE lab PC** | **Internet IP (SSH)** | Ethernet VLAN/QinQ setup, Wireshark/tcpdump |
| **BTS / CPE recovery** | **Factory IPv4 only** (`10.0.0.1`, `192.168.2.1`) | Link down → restore `.tgz`, LuCI when mgmt IPv6 is unreachable |

**Strict rule:** IPv4 is never used to run test cases. It is only for bringing the bench back when mgmt IPv6 or RF link is broken.

---

## Topology

```
                    ┌──────────────── BTS lab PC ────────────────┐
                    │  Internet IP (SSH) → VLAN on enp3s0       │
                    │  QinQ enp3s0.100.101 → mgmt IPv6 ...:301  │
                    └──────────────────┬─────────────────────────┘
                                       │ double-tag 100/101
                              ┌────────▼────────┐
                              │ BTS  QinQ       │
                              │ svlan=100       │
                              │ cvlan=101       │
                              │ mgmtvlan=101    │
                              │ mgmt IPv6 ...330│
                              └────────┬────────┘
                                       │ RF (5 GHz SSID)
                              ┌────────▼────────┐
                              │ CPE transparent │
                              │ mgmt IPv6 ...331│
                              └────────┬────────┘
                                       │ untagged
                    ┌──────────────────▼─────────────────────────┐
                    │  CPE lab PC — Internet IP 10.0.150.187     │
                    │  untagged enp3s0 → mgmt IPv6 ...:302       │
                    └────────────────────────────────────────────┘
```

| Role | RF / data VLAN | Mgmt access (tests) | UCI |
|------|----------------|---------------------|-----|
| **BTS** | QinQ S-VLAN **100** + C-VLAN **101** | IPv6 `...:330` on mgmt VLAN **101** | `vlan.ath1.mode=qinq`, `svlan=100`, `cvlan=101`, `mgmtvlan=101` |
| **CPE** | **Transparent / untagged** | IPv6 `...:331` (same mgmt VLAN **101** via RF) | `vlan.ath1.mode=transparent`, svlan/cvlan cleared |
| **BTS PC** | Stacked `enp3s0.100.101` only | IPv6 `...:301` | Bootstrap flushes IPv4/IPv6 from untagged `enp3s0` so parent does not share mgmt with QinQ |
| **CPE PC** | Native untagged `enp3s0` | IPv6 `...:302` | Configured via `10.0.150.187` SSH |

---

## Factory reset → first bring-up

After **factory reset on both BTS and CPE**:

1. **`enp3s0` untagged + `10.0.0.xx`** — reach BTS fallback **`10.0.0.1`** (no QinQ yet)
2. **Basic BTS config** on fallback (installer GUI / UCI): IPv6, mgmt VLAN, NMS, AIRTEL SSID
3. **`enp3s0.200.201` QinQ + mgmt IPv6** — parent `enp3s0` **keeps** `10.0.0.xx` for recovery

Profile `factory_provision` uses VLAN **200/201** (not 100/101):

| Step | BTS | CPE |
|------|-----|-----|
| Login | `installer` / `senao123` on fallback `10.0.0.1` | `installer` / `senao123` on `192.168.2.1` (via CPE lab PC) |
| Quick Start | IPv6, mgmt VLAN **201**, NMS (IPv6 syslog) | **Mgmt VLAN only** (transparent RF; auto-associates to BTS) |
| SSID/key | **AIRTEL_SSID_GEN** from BTS serial (script) | Same SSID/password applied pre-link from CPE PC |

Automated run (installer GUI + UCI + link poll):

```bash
# Recommended: headed browser for BTS Quick Start / Network / NMS
PYTHONPATH=. python3 scripts/factory_provision.py --headed --then-bootstrap

# SSH/UCI only (no LuCI)
PYTHONPATH=. python3 scripts/factory_provision.py --then-bootstrap

# Pytest session (SSH factory path, then normal bootstrap)
PYTHONPATH=. pytest tests/GUI/test_summary.py -v \
  --profile factory_provision --factory-provision --fallback-ip 10.0.0.1
```

Edit `profiles/factory_provision.yaml` for your mgmt IPv6 addresses and `nms.syslog_ipv6`.

---

## Automation checklist

Use this checklist for every new firmware drop.

1. Flash BTS + CPE with new build (factory reset recommended).
2. Confirm cabling + bench PCs are reachable.
3. Run:

```bash
PYTHONPATH=. python3 scripts/factory_provision.py --fallback-ip 10.0.0.1
```

4. Verify from output:
   - BTS configured on fallback path
   - CPE SSID/key applied via **secondary PC SSH hop** (`ucidyn apply`)
   - link formed (`clients >= 1`)
5. Run target pytest suites.
6. Run generic post-upgrade smoke:

```bash
PYTHONPATH=. python3 scripts/generic_ui_smoke.py --profile factory_provision --fallback-ip 10.0.0.1
```

Generates:
- `reports/artifacts/generic_ui_smoke_<timestamp>.json` (page results + CPU/memory samples)
- `reports/artifacts/generic_ui_smoke_<timestamp>.csv` (page timing table)
- `reports/artifacts/generic_ui_smoke_<timestamp>.html` (end-user dashboard report)

Default automation safeguards:
- BTS PC fallback subnet auto-ensure: `10.0.0.10/8`
- CPE PC fallback subnet auto-ensure: `10.0.0.11/8`
- Indoor tx power default: `1` on BTS and CPE
- Artifacts/reports: `reports/artifacts/`

---

## Session bootstrap (before any test case)

Runs when `testbed.bootstrap_on_start: true` (pytest default) or:

```bash
PYTHONPATH=. python3 scripts/bootstrap_testbed.py
PYTHONPATH=. python3 scripts/bootstrap_testbed.py --with-gui   # link recovery via LuCI
```

### Phase 1 — Lab PCs (internet IP)

1. **BTS-side PC** — QinQ subinterface + mgmt IPv6 (local or `primary_pc.internet_ssh`)
2. **CPE-side PC** — untagged NIC + mgmt IPv6 via `secondary_pc.ssh` (`10.0.150.187`)

This must succeed before device mgmt IPv6 is reachable on the wire.

### Phase 2 — Devices (mgmt IPv6 only)

1. SSH **BTS** at `mgmt_vlan.ipv6_bts` → verify/apply **QinQ**
2. Check RF link (`wlanconfig ath1 list` / `iw station dump`)
3. **AIRTEL link credentials** — `vendor/AIRTEL_SSID_GEN` derives SSID + password from BTS serial (`fw_printenv -n dsn`); same values applied on BTS and CPE (`link.auto_credentials: true`, no manual `link.ssid`)
4. Discover **CPE mgmt IPv6** from BTS (DHCPv6 / neighbor tables)
5. **CPE pre-link** — CPE lab PC (`secondary_pc.ssh` / internet IP) → CPE fallback/factory IPv4: transparent VLAN + same AIRTEL SSID/password via SSH `ucidyn apply` (mgmt IPv6 is **not** reachable before RF link)
6. Poll until `min_connected_clients` on `ath1`
7. After link up → discover CPE mgmt IPv6 from BTS (for test session)

All device SSH in this phase uses **IPv6 only**.

### Phase 3 — Recovery (IPv4 / GUI, only if needed)

Triggered when:

- BTS mgmt IPv6 SSH fails after lab PC setup, **or**
- RF link has no connected CPE (`link_up=false`)

Actions (in order):

1. SSH BTS on **recovery IPv4** (`10.0.0.1`) — optional VLAN fix if mgmt was down due to wrong tagging
2. **GUI restore** `config/BTS.tar.gz` (requires `--with-gui`)
3. **GUI restore** `config/CPE.tar.gz` on factory IP `192.168.2.1` (or manual via CPE PC)
4. Reboot wait → retry **mgmt IPv6** from phase 2

State: `logs/testbed_state.json`

---

## Profile (`profiles/default.yaml`)

```yaml
dut:
  ip_mode: ipv6
  strict_ipv6: true

testbed:
  strict_ipv6: true
  recovery:
    bts_fallback_ipv4: "10.0.0.1"
  factory_defaults:
    bts_vlan_mode: qinq
    cpe_vlan_mode: transparent
  qinq: { svlan: 100, cvlan: 101 }
  mgmt_vlan:
    uci_key: vlan.ath1.mgmtvlan
    uci_value: 101
    ipv6_bts: "2401:4900:d0:40d4:0:17b8:0:330"
    ipv6_cpe: "2401:4900:d0:40d4::17b8:0:331"
    ipv6_bts_pc: "2401:4900:d0:40d4::17b8:0:301"
    ipv6_cpe_pc: "2401:4900:d0:40d4::17b8:0:302"
  primary_pc:
    local: true
    mgmt_interface: enp3s0
    fallback_ipv4: "10.0.0.10"
    fallback_prefix_len: 8
    internet_ssh: ""          # BTS PC internet IP if not local automation host
  secondary_pc:
    enabled: true
    ssh: "root@10.0.150.187"
    password: "senao1234#"
    mgmt_interface: enp3s0
    fallback_ipv4: "10.0.0.11"
    fallback_prefix_len: 8
    cpe_factory_ipv4: "192.168.2.1"
  link_recovery:
    bts_archive: config/BTS.tar.gz
    cpe_archive: config/CPE.tar.gz

link:
  auto_credentials: true
  tx_power_default: "1"
  radio_idx: 1
  min_connected_clients: 1
  health_check_timeout_s: 60

recovery:
  restore_default_ip: "192.168.2.1"
  reboot_wait_seconds: 120

capture:
  enabled: true
  bts_host: ""                # BTS PC internet IP for tcpdump (or primary_pc internet_ssh)
  cpe_host: "10.0.150.187"    # CPE PC internet IP
  bts_interface: enp3s0.100.101
  cpe_interface: enp3s0

ip_tests:
  fallback_ipv4: ""           # empty = strict IPv6 for IP suite
  reachability_use_device_fallback: false
```

---

## What runs on which IP

| Action | IP |
|--------|-----|
| GUI tests, SSH tests, IP suite, regression | BTS/CPE **mgmt IPv6** |
| Bootstrap VLAN verify on BTS/CPE | **mgmt IPv6** |
| Lab PC QinQ / untagged setup | **Internet IP** (PC SSH) |
| Wireshark / tcpdump on lab PCs | **Internet IP** (`capture.bts_host` / `cpe_host`) |
| Archive restore, factory LuCI | **Recovery IPv4** (`10.0.0.1`, `192.168.2.1`) |
| CPE VLAN hop when mgmt down | CPE PC internet → CPE factory IPv4 (recovery only) |

---

## Commands

```bash
# Normal bootstrap (strict mgmt IPv6 for devices)
PYTHONPATH=. python3 scripts/bootstrap_testbed.py

# With link recovery (LuCI .tgz restore over factory IPv4)
PYTHONPATH=. python3 scripts/bootstrap_testbed.py --with-gui

# Pytest (bootstrap runs first unless skipped)
PYTHONPATH=. pytest tests/GUI/ -v
PYTHONPATH=. pytest tests/IP/ -v --allow-ip-suite

# Skip bootstrap when bench is already verified
PYTHONPATH=. pytest tests/GUI/ -v --skip-testbed-bootstrap
```

---

## Link not forming

1. Confirm lab PC VLANs: mgmt IPv6 only on `enp3s0.100.101` (BTS PC — not on bare `enp3s0`), and on `enp3s0` (CPE PC)
2. Run `scripts/bootstrap_testbed.py --with-gui` to restore BTS/CPE archives
3. On CPE PC (`10.0.150.187`), load `config/CPE.tar.gz` manually if GUI restore from automation host fails
4. Verify live link: `wlanconfig ath1 list` on BTS (expect CPE MAC, RSSI > -80)

Place archives under `config/BTS.tar.gz` and `config/CPE.tar.gz`.
