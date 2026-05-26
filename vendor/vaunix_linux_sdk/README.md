# Vaunix LDA-602 (Linux USB)

Minimal vendored pieces for USB LDA control. Full Vaunix Linux SDK is **not** required in-repo.

## Required files

| Path | Purpose |
|------|---------|
| `lib/libLDAhid.so` | Runtime library (ctypes); build or copy from Vaunix SDK |
| `src/LDAhid.c`, `src/LDAhid.h` | Sources to rebuild `libLDAhid.so` after SDK updates |

## Not needed (removed from repo)

- **EthernetSDK** — network/Ethernet Lab Bricks only
- **USB_PythonApp** — duplicate sources; we use `instruments/vaunix_lda.py`
- **test.c / profile_test.c / makefiles** — Vaunix sample apps only

## Build

```bash
sudo apt-get install -y libusb-1.0-0-dev   # once
./scripts/build_vaunix_linux_sdk.sh
```

## Lab CLI

```bash
sudo PYTHONPATH=. python3 scripts/attenuator_snr.py verify --steps-db 0,40,0
sudo PYTHONPATH=. python3 scripts/attenuator_snr.py parallel --stream-att-db 40
```

Device IDs: `profiles/default.yaml` → `attenuator.chains` (serials **36831**, **36832**).
