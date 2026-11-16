#!/usr/bin/env python3
"""
Standalone jumbo frame throughput test (UBR650-style)
====================================================
Before traffic, the script can configure and verify (default --setup):
  • Lab NICs + BTS + each CPE/SU: Ethernet MTU 9000, br-lan MTU 9000
  • BTS: HT mode (HT20 / HT40+ / HT80), 5 GHz channel (e.g. 159 → 5795 MHz)
  • BSU role + associated SU(s) via /sys/class/kwn/sua*/statistics

Requirements:
  pip install scapy
  sshpass
  sudo (for raw L2 traffic)

Edit MAC_VLAN_TABLE and defaults below for your bench.

After the run, writes Senao-styled HTML (unless --no-html):
  Senao_Jumbo_Tput_Report_<timestamp>.html  (--report-dir)
"""
from __future__ import annotations

import argparse
import base64
import datetime
import errno
import html
import multiprocessing
import os
import re
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time
try:
    from scapy.all import Dot1Q, Ether, Raw
except ImportError:
    sys.exit("Install scapy:  pip install scapy")

# ─── Lab wiring (edit) ───────────────────────────────────────────────────────
BTS_IFACE = "enp3s0"
CPE_IFACE = "enp4s0"
BTS_SSH_IP = "192.168.1.31"
CPE_SSH_IPS = ["192.168.1.34"]   # add more SUs for P2MP, or use --cpe-ips
SSH_USER = "root"
RADIO_IDX = 1
JUMBO_MTU = 9000
DEFAULT_CHANNEL = 159            # UBR650 sheet: 159 (5795 MHz) — override with --channel
MIN_ASSOCIATED_SU = 1
MAX_SU_SLOTS = 16

# ("BTS", src, dst, vlan) = TX on BTS_IFACE; ("CPE", ...) = TX on CPE_IFACE
MAC_VLAN_TABLE = [
    ("BTS", "00:AA:BB:CC:DD:01", "00:11:22:33:44:01", 0),
    ("CPE", "00:11:22:33:44:01", "00:AA:BB:CC:DD:01", 0),
]

UBR650_PACKET_SIZES = [
    1500, 1502, 1504, 1506, 1508, 1510, 1512, 1514, 1516, 1518, 1520, 1530,
    1600, 1750, 2050, 2200, 3000, 3100, 3200, 3300, 3500, 3600, 4000, 4500,
    5000, 5200, 5500, 6000, 6200, 6500, 7000, 7200, 7500, 7800, 8000, 8200,
    8400, 8600, 8800, 9000,
]

# ─── Frame format ────────────────────────────────────────────────────────────
DL_MARKER = b"VLAN_DL_TST"
UL_MARKER = b"VLAN_UL_TST"
MARKER_LEN = 11
TS_LEN, IDX_LEN, SEQ_LEN = 8, 2, 4
PAYLOAD_HDR = MARKER_LEN + TS_LEN + IDX_LEN + SEQ_LEN
ETH_VLAN_HDR = 18
MIN_FRAME = 64
TS_OFF = ETH_VLAN_HDR + MARKER_LEN
SEQ_OFF = TS_OFF + TS_LEN + IDX_LEN


def _set_promisc(sock: socket.socket, iface: str) -> None:
    SOL_PACKET = getattr(socket, "SOL_PACKET", 263)
    PACKET_ADD_MEMBERSHIP = 1
    PACKET_MR_PROMISC = 1
    mreq = struct.pack(
        "IHH8s", socket.if_nametoindex(iface), PACKET_MR_PROMISC, 0, b"\x00" * 8,
    )
    try:
        sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)
    except OSError:
        pass


def _build_frame(src: str, dst: str, vlan: int, size: int, marker: bytes,
                 flow_idx: int, seq: int, tx_ns: int) -> bytearray:
    pay_len = size - ETH_VLAN_HDR
    pad = max(0, pay_len - PAYLOAD_HDR)
    payload = (
        marker
        + struct.pack(">Q", tx_ns)
        + struct.pack(">H", flow_idx)
        + struct.pack(">I", seq)
        + bytes(pad)
    )[:pay_len]
    return bytearray(bytes(Ether(src=src, dst=dst) / Dot1Q(vlan=vlan) / Raw(load=payload)))


def _mbps_to_pps(mbps: float, frame_size: int) -> float:
    return (mbps * 1e6) / (frame_size * 8) if mbps > 0 else 0.0


# ─── SSH / HT mode ───────────────────────────────────────────────────────────
def _canonical_ht(label: str) -> str:
    x = label.strip().upper().replace(" ", "")
    if x.startswith("HT40"):
        return "HT40"
    if x in ("HT20", "HT80", "HT160"):
        return x
    raise ValueError(f"Bad HT mode {label!r}")


def _uci_htmode(label: str) -> str:
    return "HT40+" if _canonical_ht(label) == "HT40" else _canonical_ht(label)


def _parse_running_htmode(text: str) -> str | None:
    t = (text or "").strip().upper()
    if ":" in t:
        t = t.split(":")[-1].strip()
    for w in ("160", "80", "40", "20"):
        if f"HE{w}" in t or f"HT{w}" in t or f"VHT{w}" in t or f"AHE{w}" in t:
            return f"HT{w}"
    m = re.search(r"(?:AHE|HE|VHT|HT)(\d+)", t)
    return f"HT{m.group(1)}" if m else None


def _ssh_try(host: str, password: str, cmd: str) -> str:
    try:
        return _ssh(host, password, cmd)
    except RuntimeError:
        return ""


def _ssh_clean(text: str) -> str:
    lines = [
        ln for ln in (text or "").splitlines()
        if ln.strip() and not ln.strip().lower().startswith("warning:")
    ]
    return "\n".join(lines).strip()


def _ssh(host: str, password: str, cmd: str, *, user: str = SSH_USER) -> str:
    if not password:
        raise RuntimeError("Set SMAC_DEVICE_PASSWORD or --device-password")
    cp = subprocess.run(
        [
            "sshpass", "-p", password,
            "ssh", "-T", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=12",
            f"{user}@{host}", cmd,
        ],
        capture_output=True, text=True, timeout=45,
    )
    out = _ssh_clean((cp.stdout or "") + (cp.stderr or ""))
    if cp.returncode != 0:
        raise RuntimeError(f"SSH {host}: {out.strip()}")
    return out


def _discover_eth_keys(host: str, password: str) -> list[str]:
    out = _ssh(host, password, "uci show ethernet 2>/dev/null | grep '.mtu=' || true")
    keys = []
    for line in out.splitlines():
        m = re.match(r"ethernet\.(eth\d+)\.mtu=", line.strip())
        if m:
            keys.append(m.group(1))
    return sorted(set(keys), key=lambda k: int(k[3:]))


def apply_jumbo_mtu(host: str, password: str, mtu: int, *, label: str = "") -> None:
    tag = label or host
    keys = _discover_eth_keys(host, password)
    parts = [f"ucidyn set ethernet.{k}.mtu {mtu}" for k in keys]
    parts.append(f"ip link set dev br-lan mtu {mtu} 2>/dev/null || "
                 f"ifconfig br-lan mtu {mtu} 2>/dev/null || true")
    parts.append("ucidyn apply")
    print(f"[MTU] {tag}: {keys or ['eth*']} + br-lan → {mtu}")
    _ssh(host, password, "; ".join(parts))
    time.sleep(1)


def verify_mtu(host: str, password: str, mtu: int) -> bool:
    keys = _discover_eth_keys(host, password)
    ok = True
    for k in keys:
        val = _ssh(host, password, f"uci get ethernet.{k}.mtu 2>/dev/null || echo ?")
        if str(val).strip() != str(mtu):
            print(f"  [MTU] {host} {k} uci={val} (want {mtu})")
            ok = False
    br = _ssh(host, password, "cat /sys/class/net/br-lan/mtu 2>/dev/null || echo ?")
    if str(br).strip() != str(mtu):
        print(f"  [MTU] {host} br-lan={br} (want {mtu})")
        ok = False
    return ok


def apply_lab_nic_mtu(bts_if: str, cpe_if: str, mtu: int) -> None:
    for iface in (bts_if, cpe_if):
        print(f"[MTU] lab NIC {iface} → {mtu}")
        cp = subprocess.run(
            ["ip", "link", "set", "dev", iface, "mtu", str(mtu)],
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            raise RuntimeError(f"ip link set {iface} mtu {mtu}: {cp.stderr.strip()}")


def read_radio(host: str, password: str, radio: int) -> dict:
    uci = _ssh_clean(_ssh_try(host, password, f"uci get wireless.wifi{radio}.htmode 2>/dev/null || true"))
    raw = _ssh_clean(_ssh_try(host, password, f"cfg80211tool ath{radio} get_mode 2>/dev/null || true"))
    iw = _ssh_clean(_ssh_try(host, password, f"iwconfig ath{radio} 2>/dev/null || true"))
    run = _parse_running_htmode(raw) or _parse_running_htmode(iw) or "?"
    ch_uci = _ssh_try(
        host, password,
        f"uci get advwireless.ath{radio}.channel 2>/dev/null || "
        f"uci get wireless.wifi{radio}.channel 2>/dev/null || true",
    )
    freq = ""
    for line in iw.splitlines():
        if "frequency" in line.lower():
            freq = line.strip()
            break
    mode = _ssh_try(
        host, password,
        f"uci get wireless.@wifi-iface[{radio}].mode 2>/dev/null || "
        f"iwconfig ath{radio} 2>/dev/null | grep -i Mode | head -1",
    )
    return {
        "uci": uci, "running": run, "raw": raw,
        "channel_uci": ch_uci.strip(),
        "frequency": freq.strip(),
        "wifi_mode": mode.strip(),
        "channel": f"uci={ch_uci.strip()} | {freq.strip()}",
    }


def apply_channel(host: str, password: str, radio: int, channel: int, wait_s: float = 90.0) -> None:
    print(f"[RF] {host}: channel → {channel}")
    _ssh(
        host, password,
        f"ucidyn set wireless.wifi{radio}.channel {channel}; "
        f"ucidyn set advwireless.ath{radio}.channel {channel}; "
        f"ucidyn apply",
    )
    deadline = time.time() + wait_s
    want = str(channel)
    while time.time() < deadline:
        st = read_radio(host, password, radio)
        if want in st["channel_uci"] or want in st["channel"]:
            print(f"[RF] Channel OK: {st['channel']}")
            return
        time.sleep(5)
    raise RuntimeError(f"Channel {channel} not confirmed on {host}")


def apply_ht(host: str, password: str, radio: int, ht: str, wait_s: float = 120.0) -> dict:
    uci = _uci_htmode(ht)
    want = _canonical_ht(ht)
    print(f"[RF] {host}: htmode → {uci}")
    _ssh(host, password, f"ucidyn set wireless.wifi{radio}.htmode {uci}; ucidyn apply")
    deadline = time.time() + wait_s
    st = read_radio(host, password, radio)
    uci_want = _uci_htmode(ht)
    while time.time() < deadline:
        st = read_radio(host, password, radio)
        uci_now = (st["uci"] or "").strip().upper().replace("HT40+", "HT40")
        if st["running"] == want or uci_now == uci_want.upper().replace("HT40+", "HT40"):
            print(f"[RF] HT OK: uci={st['uci']} running={st['running']}")
            return st
        time.sleep(5)
    raise RuntimeError(
        f"HT {want} not up; uci={st['uci']} running={st['running']} raw={st.get('raw', '')[:80]}",
    )


def _parse_su_dump(text: str) -> list[dict]:
    sus: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("SU|"):
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        try:
            idx = int(parts[1])
        except ValueError:
            continue
        mac, ip, rx, assoc = parts[2], parts[3], parts[4], parts[5]
        if not mac or mac in {"-", "00:00:00:00:00:00"}:
            continue
        sus.append({"index": idx, "mac": mac, "ip": ip, "rx": rx, "assoc": assoc})
    return sus


def fetch_associated_sus(host: str, password: str, radio: int, max_su: int) -> list[dict]:
    inner = (
        f"echo LINKS:$(cat /sys/class/kwn/wifi{radio}/statistics/links 2>/dev/null || echo 0); "
        f"for idx in $(seq 1 {max_su}); do "
        f'base="/sys/class/kwn/sua${{idx}}/statistics"; '
        f'[ -d "$base" ] || continue; '
        f'm=$(cat "$base/mac" 2>/dev/null); '
        f'ip=$(cat "$base/ip" 2>/dev/null); '
        f'rx=$(cat "$base/rx_rate" 2>/dev/null); '
        f'a=$(cat "$base/associd" 2>/dev/null); '
        f'echo "SU|$idx|$m|$ip|$rx|$a"; '
        f"done"
    )
    raw = _ssh(host, password, inner)
    sus = _parse_su_dump(raw)
    for line in raw.splitlines():
        if line.startswith("LINKS:"):
            print(f"[LINK] BTS wifi{radio} statistics/links = {line.split(':', 1)[1].strip()}")
    return sus


def verify_bsu(host: str, password: str, radio: int) -> bool:
    st = read_radio(host, password, radio)
    mode = (st.get("wifi_mode") or "").lower()
    if any(x in mode for x in ("ap", "master", "bsu", "wifi-master")):
        return True
    raw = _ssh(host, password, f"iwconfig ath{radio} 2>/dev/null | head -3")
    return "master" in raw.lower() or "access point" in raw.lower()


def wait_for_su_links(
    host: str, password: str, radio: int,
    min_su: int, max_su: int, timeout_s: float,
) -> list[dict]:
    print(f"[LINK] Waiting for ≥{min_su} associated SU(s) (timeout {timeout_s:.0f}s) …")
    deadline = time.time() + timeout_s
    last: list[dict] = []
    while time.time() < deadline:
        last = fetch_associated_sus(host, password, radio, max_su)
        if len(last) >= min_su:
            for su in last:
                print(f"  SU{su['index']}: mac={su.get('mac')} ip={su.get('ip')} "
                      f"rx={su.get('rx')} assoc={su.get('assoc')}")
            return last
        time.sleep(5)
    raise RuntimeError(
        f"Only {len(last)} SU(s) associated on {host}, need ≥{min_su}. "
        f"Check RF link / CPE power."
    )


def preflight_setup(args, ht_list: list[str]) -> None:
    """MTU + optional RF + link checks before packet sweep."""
    pwd = args.device_password
    bts = args.bts_ip
    cpe_ips = args.cpe_ips
    mtu = args.mtu
    radio = args.radio

    print("\n" + "=" * 72)
    print("  PRE-FLIGHT SETUP")
    print("=" * 72)

    if not verify_bsu(bts, pwd, radio):
        print(f"[WARN] BTS may not be in BSU/AP mode — check ath{radio}")

    if args.setup_mtu:
        apply_lab_nic_mtu(args.bts, args.cpe, mtu)
        apply_jumbo_mtu(bts, pwd, mtu, label="BTS")
        for ip in cpe_ips:
            apply_jumbo_mtu(ip, pwd, mtu, label=f"CPE {ip}")
        for ip in [bts] + list(cpe_ips):
            if not verify_mtu(ip, pwd, mtu):
                print(f"[WARN] MTU verify failed on {ip} — continuing anyway.")

    if args.setup_rf:
        if args.channel is not None:
            apply_channel(bts, pwd, radio, args.channel, wait_s=args.rf_wait)
        if len(ht_list) == 1:
            apply_ht(bts, pwd, radio, ht_list[0], wait_s=args.rf_wait)
        st = read_radio(bts, pwd, radio)
        print(f"  RF: running {st['running']}  {st['channel']}")

    wait_for_su_links(bts, pwd, radio, args.min_su, args.max_su, args.link_wait)
    print("=" * 72 + "\n")


# ─── TX process ──────────────────────────────────────────────────────────────
def _tx_main(iface, flows, marker, frame_size, pps, duration, stop, out_q):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    sent = 0
    t0 = t1 = 0.0
    interval = 1.0 / pps if pps > 0 else 0.0
    templates = [
        _build_frame(src, dst, vlan, frame_size, marker, i, 0, 0)
        for i, (src, dst, vlan) in enumerate(flows)
    ]
    seqs = [0] * len(flows)
    fi = 0
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    sock.bind((iface, 0))
    deadline = time.perf_counter() + duration
    t0 = time.perf_counter()
    next_t = t0
    try:
        while not stop.is_set() and time.perf_counter() < deadline:
            ba = templates[fi]
            seqs[fi] += 1
            struct.pack_into(">I", ba, SEQ_OFF, seqs[fi])
            struct.pack_into(">Q", ba, TS_OFF, time.perf_counter_ns())
            while True:
                try:
                    sock.send(ba)
                    sent += 1
                    break
                except OSError as e:
                    if e.errno not in (errno.ENOBUFS, errno.EAGAIN, errno.EWOULDBLOCK):
                        raise
                    time.sleep(1e-5)
            fi = (fi + 1) % len(flows)
            if interval > 0:
                next_t += interval
                rem = next_t - time.perf_counter()
                if rem > 0:
                    if rem < 0.001:
                        while time.perf_counter() < next_t:
                            pass
                    else:
                        time.sleep(rem)
    finally:
        sock.close()
        t1 = time.perf_counter()
    out_q.put({"sent": sent, "t0": t0, "t1": t1})


# ─── RX process ──────────────────────────────────────────────────────────────
def _rx_main(iface, marker, n_flows, frame_size, stop, ready_q, out_q):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    ETH_P_ALL = 0x0003
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
    sock.bind((iface, 0))
    _set_promisc(sock, iface)
    sock.settimeout(0.1)
    ready_q.put(True)
    rx = 0
    gaps = 0
    exp = {}
    t_first = t_last = 0.0
    uQ, uI = struct.Struct(">Q").unpack_from, struct.Struct(">I").unpack_from
    while not stop.is_set():
        try:
            data = sock.recv(65535)
        except socket.timeout:
            continue
        mp = data.find(marker)
        if mp < 0 or len(data) < mp + MARKER_LEN + TS_LEN + IDX_LEN + SEQ_LEN:
            continue
        base = mp + MARKER_LEN
        flow = struct.Struct(">H").unpack_from(data, base + TS_LEN)[0]
        seq = uI(data, base + TS_LEN + IDX_LEN)[0]
        if flow >= n_flows:
            continue
        now = time.perf_counter()
        if rx == 0:
            t_first = now
        t_last = now
        rx += 1
        e = exp.get(flow)
        if e is None:
            exp[flow] = seq + 1
        elif seq == e:
            exp[flow] = e + 1
        elif seq > e:
            gaps += seq - e
            exp[flow] = seq + 1
    sock.close()
    span = (t_last - t_first) if rx > 1 else 0.0
    out_q.put({"received": rx, "gaps": gaps, "span": span, "t_first": t_first, "t_last": t_last})


def run_bidir_test(
    bts_if: str, cpe_if: str,
    bts_flows, cpe_flows,
    frame_size: int, mbps: float, duration: float, buffer: float,
) -> tuple[dict, dict]:
    pps = _mbps_to_pps(mbps, frame_size)
    stop = multiprocessing.Event()
    dl_ready, ul_ready = multiprocessing.Queue(), multiprocessing.Queue()
    dl_res, ul_res = multiprocessing.Queue(), multiprocessing.Queue()
    tx_dl_res, tx_ul_res = multiprocessing.Queue(), multiprocessing.Queue()

    rx_dl = multiprocessing.Process(
        target=_rx_main,
        args=(cpe_if, DL_MARKER, len(bts_flows), frame_size, stop, dl_ready, dl_res),
        daemon=True,
    )
    rx_ul = multiprocessing.Process(
        target=_rx_main,
        args=(bts_if, UL_MARKER, len(cpe_flows), frame_size, stop, ul_ready, ul_res),
        daemon=True,
    )
    rx_dl.start()
    rx_ul.start()
    if not dl_ready.get(timeout=8) or not ul_ready.get(timeout=8):
        stop.set()
        raise RuntimeError("RX socket failed — check interfaces and root.")

    tx_dl = multiprocessing.Process(
        target=_tx_main,
        args=(bts_if, bts_flows, DL_MARKER, frame_size, pps, duration, stop, tx_dl_res),
        daemon=True,
    )
    tx_ul = multiprocessing.Process(
        target=_tx_main,
        args=(cpe_if, cpe_flows, UL_MARKER, frame_size, pps, duration, stop, tx_ul_res),
        daemon=True,
    )
    tx_dl.start()
    tx_ul.start()
    tx_dl.join(duration + 15)
    tx_ul.join(duration + 15)
    stop.set()
    time.sleep(buffer)
    rx_dl.join(10)
    rx_ul.join(10)

    def _qget(q, default):
        try:
            return q.get(timeout=5)
        except Exception:
            return default

    td = _qget(tx_dl_res, {"sent": 0, "t0": 0, "t1": 1})
    tu = _qget(tx_ul_res, {"sent": 0, "t0": 0, "t1": 1})
    rd = _qget(dl_res, {"received": 0, "gaps": 0, "span": 0})
    ru = _qget(ul_res, {"received": 0, "gaps": 0, "span": 0})

    def _stats(sent_d, rx_d, label):
        sent = sent_d["sent"]
        rx = rx_d["received"]
        tx_span = max(1e-9, sent_d["t1"] - sent_d["t0"])
        tx_mbps = (sent * frame_size * 8 / 1e6) / tx_span if sent else 0.0
        rx_mbps = (rx * frame_size * 8 / 1e6) / tx_span if rx else 0.0
        if sent:
            loss_pct = min(100.0, max(0.0, (sent - rx) / sent * 100.0))
        else:
            loss_pct = 100.0 if rx == 0 else 0.0
        seq_gap_pct = min(100.0, (rx_d["gaps"] / sent * 100.0)) if sent else 0.0
        return {
            "label": label, "sent": sent, "received": rx,
            "tx_mbps": tx_mbps, "rx_mbps": rx_mbps,
            "loss_pct": loss_pct,
            "seq_gap_pct": seq_gap_pct,
        }

    return _stats(td, rd, "DL"), _stats(tu, ru, "UL")


def _parse_size_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return sorted(set(out))


def _sizes_arg(mode: str, min_s: int, max_s: int, step: int, size_list: str) -> list[int]:
    if mode == "ubr650":
        return list(UBR650_PACKET_SIZES)
    if mode == "list":
        sizes = _parse_size_list(size_list)
        if not sizes:
            raise SystemExit("--size-list required when --sizes list (comma-separated bytes)")
        return sizes
    out = list(range(min_s, max_s + 1, step))
    if out and out[-1] != max_s:
        out.append(max_s)
    return sorted(set(out))


def parse_results_from_log(text: str) -> dict[int, dict]:
    """Map packet size (B) → {dl, ul, drops, total} from a prior run log."""
    by_size: dict[int, dict] = {}
    pending: int | None = None
    for line in text.splitlines():
        m = re.search(r"\[\s*\d+/\d+\]\s+(\d+)\s+B", line)
        if m:
            pending = int(m.group(1))
            continue
        if pending is not None and "Mbps" in line and "loss" in line:
            mm = re.search(r"([\d.]+)\+([\d.]+)\s+Mbps\s+loss\s+([\d.]+)%", line)
            if mm:
                dl, ul, loss = map(float, mm.groups())
                by_size[pending] = {
                    "dl": dl, "ul": ul, "drops": loss, "total": dl + ul,
                }
            pending = None
    return by_size


def _parse_cpe_ips(raw: str) -> list[str]:
    if not raw:
        return list(CPE_SSH_IPS)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _senao_logo_src() -> str:
    logo = Path(__file__).resolve().parent.parent / "reports" / "assets" / "senao-logo.svg"
    raw = logo.read_bytes() if logo.is_file() else (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="40" viewBox="0 0 180 40">'
        b'<text x="8" y="28" font-family="Arial,sans-serif" font-size="18" font-weight="700" '
        b'fill="#0071be">Senao Networks</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")


def _ht_bandwidth_label(ht: str) -> str:
    c = _canonical_ht(ht)
    return {"HT20": "20 MHz", "HT40": "40 MHz", "HT80": "80 MHz", "HT160": "160 MHz"}.get(c, c)


def _channel_freq_mhz(channel: int) -> int:
    return 5000 + 5 * channel


def _has_packet_counters(dl: dict, ul: dict) -> bool:
    for side in (dl, ul):
        if side.get("sent") or side.get("received"):
            return True
    return False


def _detail_for_report(row: dict, offered_mbps: float) -> tuple[dict, dict, bool]:
    dl = dict(row.get("dl_detail") or {})
    ul = dict(row.get("ul_detail") or {})
    if _has_packet_counters(dl, ul):
        return dl, ul, False
    loss = float(row.get("drops") or 0)
    dl = {
        "sent": None,
        "received": None,
        "tx_mbps": offered_mbps,
        "rx_mbps": float(row.get("dl") or 0),
        "loss_pct": loss,
    }
    ul = {
        "sent": None,
        "received": None,
        "tx_mbps": offered_mbps,
        "rx_mbps": float(row.get("ul") or 0),
        "loss_pct": loss,
    }
    return dl, ul, True


def _counter_cell(value) -> str:
    if value is None:
        return "—"
    return str(value)


def recalculate_row_loss_from_counters(row: dict) -> None:
    for side in ("dl_detail", "ul_detail"):
        d = row.get(side)
        if not d:
            continue
        sent = int(d.get("sent") or 0)
        rx = int(d.get("received") or 0)
        if sent:
            d["loss_pct"] = min(100.0, max(0.0, (sent - rx) / sent * 100.0))
        else:
            d["loss_pct"] = 100.0 if rx == 0 else 0.0
    dl = float((row.get("dl_detail") or {}).get("loss_pct", row.get("drops", 100)))
    ul = float((row.get("ul_detail") or {}).get("loss_pct", row.get("drops", 100)))
    row["drops"] = max(dl, ul)


def recalculate_row_tput_from_counters(row: dict) -> None:
    for side in ("dl_detail", "ul_detail"):
        d = row.get(side)
        if not d:
            continue
        sent = int(d.get("sent") or 0)
        rx = int(d.get("received") or 0)
        tx_mbps = float(d.get("tx_mbps") or 0)
        if sent and tx_mbps:
            d["rx_mbps"] = tx_mbps * rx / sent
        elif not sent:
            d["rx_mbps"] = 0.0
    row["dl"] = float((row.get("dl_detail") or {}).get("rx_mbps", row.get("dl") or 0))
    row["ul"] = float((row.get("ul_detail") or {}).get("rx_mbps", row.get("ul") or 0))
    row["total"] = row["dl"] + row["ul"]


def recalculate_rows_metrics(rows: list[dict]) -> None:
    for r in rows:
        recalculate_row_loss_from_counters(r)
        recalculate_row_tput_from_counters(r)


def write_results_json(rows: list[dict], json_path: str) -> None:
    import json

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f, indent=2)


def _result_badge(drops_pct: float, pass_pct: float) -> tuple[str, str]:
    if drops_pct >= 100:
        return "FAIL", "background:#fee2e2;color:#991b1b;border:1px solid #991b1b33"
    if drops_pct <= pass_pct:
        return "PASS", "background:#dcfce7;color:#166534;border:1px solid #16653433"
    return "MARGINAL", "background:#fff7ed;color:#c2410c;border:1px solid #c2410c33"


def write_html_report(
    *,
    rows: list[dict],
    out_dir: str,
    bts_iface: str,
    cpe_iface: str,
    bts_ip: str,
    cpe_ips: list[str],
    channel: int,
    htmodes: list[str],
    mtu: int,
    offered_mbps: float,
    duration_s: float,
    buffer_s: float,
    loss_pass_pct: float,
    sizes_mode: str,
    n_flows: int,
    compare_by_size: dict[int, dict] | None = None,
    compare_label: str = "Previous run",
    current_label: str = "Current run",
    output_path: str | None = None,
    compare_notes: dict[int, str] | None = None,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.datetime.now()
    stamp = ts.strftime("%Y%m%d_%H%M%S")
    path = output_path or os.path.join(out_dir, f"Senao_Jumbo_Tput_Report_{stamp}.html")

    recalculate_rows_metrics(rows)

    n = len(rows)
    passed = sum(1 for r in rows if r.get("drops", 100) <= loss_pass_pct and r.get("drops", 100) < 100)
    failed = sum(1 for r in rows if r.get("drops", 0) >= 100)
    marginal = n - passed - failed
    peak = max((r.get("total") or 0 for r in rows), default=0.0)
    best = max(rows, key=lambda r: r.get("total") or 0, default=None)
    best_label = f"{best['size']} B" if best else "—"

    ht_display = ", ".join(_canonical_ht(h) for h in htmodes)
    freq = _channel_freq_mhz(channel)
    logo = _senao_logo_src()
    ht_primary = _canonical_ht(htmodes[0]) if htmodes else ht_display
    bw_primary = _ht_bandwidth_label(ht_primary)
    offered_pair = f"{offered_mbps:.0f}+{offered_mbps:.0f}"

    def esc(x) -> str:
        return html.escape(str(x))

    detail_blocks: list[str] = []
    any_summary_only = False
    for r in rows:
        dl, ul, summary_only = _detail_for_report(r, offered_mbps)
        if summary_only:
            any_summary_only = True
        status, badge_style = _result_badge(r.get("drops", 100), loss_pass_pct)
        tput_short = f'{r["dl"]:.0f}+{r["ul"]:.0f} Mbps'
        summary_note = ""
        if summary_only:
            summary_note = (
                '<p class="detail-note">Sent/Received counts were not stored for this report '
                "(rebuilt from run log). Throughput and loss below match the log summary; "
                "loss % is the max of DL and UL when only one value was logged.</p>"
            )
        detail_blocks.append(
            f'<details class="size-detail" data-status="{esc(status)}">'
            f'<summary>'
            f'<span class="sum-idx">{r["sno"]}</span>'
            f'<span class="sum-size">{r["size"]} B</span>'
            f'<span class="sum-ht">{esc(r["ht"])}</span>'
            f'<span class="sum-tput">{tput_short}</span>'
            f'<span class="sum-loss">{r["drops"]:.2f}% loss</span>'
            f'<span class="badge" style="{badge_style}">{status}</span>'
            f'</summary>'
            f'<div class="detail-inner">'
            f'{summary_note}'
            f'<table class="plan detail"><thead><tr>'
            f'<th>Direction</th><th>Sent</th><th>Received</th>'
            f'<th>TX offered (Mbps)</th><th>RX throughput (Mbps)</th><th>Loss / gaps %</th>'
            f'</tr></thead><tbody>'
            f'<tr><td>Downlink (BTS→CPE)</td>'
            f'<td>{_counter_cell(dl.get("sent"))}</td><td>{_counter_cell(dl.get("received"))}</td>'
            f'<td>{dl.get("tx_mbps", 0):.2f}</td><td>{dl.get("rx_mbps", 0):.2f}</td>'
            f'<td>{dl.get("loss_pct", 0):.2f}</td></tr>'
            f'<tr><td>Uplink (CPE→BTS)</td>'
            f'<td>{_counter_cell(ul.get("sent"))}</td><td>{_counter_cell(ul.get("received"))}</td>'
            f'<td>{ul.get("tx_mbps", 0):.2f}</td><td>{ul.get("rx_mbps", 0):.2f}</td>'
            f'<td>{ul.get("loss_pct", 0):.2f}</td></tr>'
            f'</tbody></table>'
            f'<p class="meta">Duration {duration_s:.1f} s (+ {buffer_s:.1f} s buffer) · '
            f'Offered {offered_mbps:.1f} Mbps/dir · Flow pairs: {n_flows}</p>'
            f'</div></details>'
        )

    summary_rows: list[str] = []
    for r in rows:
        status, badge_style = _result_badge(r.get("drops", 100), loss_pass_pct)
        summary_rows.append(
            f'<tr class="row-auto" data-status="{esc(status)}">'
            f'<td>{r["sno"]}</td>'
            f'<td class="tid">{r["size"]}</td>'
            f'<td>{r["drops"]:.2f}</td>'
            f'<td>{r["total"]:.1f}</td>'
            f'<td><span class="badge" style="{badge_style}">{status}</span></td>'
            f'</tr>'
        )

    def _loss_pill(pct: float) -> str:
        if pct >= 100:
            cls = "loss-fail"
        elif pct <= loss_pass_pct:
            cls = "loss-ok"
        else:
            cls = "loss-warn"
        return f'<span class="loss-pill {cls}">{pct:.2f}</span>'

    def _tput_cell(dl: float, ul: float) -> str:
        return (
            f'<span class="tput-pair">'
            f'<span class="tput-val">{dl:.1f}</span>'
            f'<span class="tput-plus">+</span>'
            f'<span class="tput-val">{ul:.1f}</span>'
            f'<span class="tput-unit">Mbps</span></span>'
        )

    compare_html = ""
    if compare_by_size:
        cmp_rows: list[str] = []
        for r in rows:
            sz = r["size"]
            prev = compare_by_size.get(sz)
            if not prev:
                continue
            cmp_rows.append(
                f'<tr>'
                f'<td class="cmp-size">{sz:,}</td>'
                f'<td class="cmp-tput">{_tput_cell(prev["dl"], prev["ul"])}</td>'
                f'<td class="cmp-loss">{_loss_pill(prev["drops"])}</td>'
                f'<td class="cmp-tput cmp-tput-now">{_tput_cell(r["dl"], r["ul"])}</td>'
                f'<td class="cmp-loss">{_loss_pill(r["drops"])}</td>'
                f'</tr>'
            )
        if cmp_rows:
            compare_html = f"""
<div class="cmp-section">
<div class="cmp-head">
<div><h2>Throughput comparison</h2>
<p class="cmp-sub">Downlink + uplink offered throughput (Mbps per direction) and packet loss.</p></div>
<div class="cmp-legend">
<span class="loss-pill loss-ok">≤ {loss_pass_pct:g}%</span>
<span class="loss-pill loss-warn">&gt; {loss_pass_pct:g}%</span>
</div></div>
<div class="cmp-scroll">
<table class="cmp-table"><thead>
<tr class="cmp-grp-row">
<th class="cmp-size-h" rowspan="2">Size<br><span>(bytes)</span></th>
<th colspan="2" class="grp-prev">{esc(compare_label)}</th>
<th colspan="2" class="grp-cur">{esc(current_label)}</th>
</tr>
<tr class="cmp-col-row">
<th>Throughput</th><th>Loss</th>
<th>Throughput</th><th>Loss</th>
</tr></thead><tbody>
{"".join(cmp_rows)}
</tbody></table></div></div>"""

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Senao UBR650 Jumbo Throughput Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{--bg:#eef2f7;--card:#fff;--border:#e2e8f0;--title:#0f172a;--muted:#64748b;--pass:#10b981;--fail:#ef4444;--total:#1e3a8a}}
*{{box-sizing:border-box}} body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:auto}} .hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a 60%,#2563eb);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center}}
.hero h1{{margin:0 0 6px;font-size:22px}} .hero p{{margin:0;font-size:13px;opacity:.92}} .hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}} .hero-logo img{{height:42px;display:block}}
.container{{background:#fff;border:1px solid #cbd5e1;border-radius:14px;overflow:hidden}} .cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px;text-align:center;border:1px solid var(--border);border-radius:10px;border-bottom:3px solid}} .card h3{{margin:0;font-size:22px}} .card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700}}
.total{{color:var(--total)}} .pass{{color:var(--pass)}} .fail{{color:var(--fail)}} .warn{{color:#c2410c}} .peak{{color:#2563eb}}
.panel{{padding:12px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}} .panel h2{{font-size:17px;color:var(--title);margin:0 0 10px}}
.testbed{{width:100%;border-collapse:collapse;background:#fff}} .testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left}} .testbed tbody th{{width:180px;color:var(--muted);font-size:11px;text-transform:uppercase}}
.role{{background:#dbeafe;color:#1e3a8a;border-radius:999px;padding:3px 8px;margin-right:8px;font-size:10px;font-weight:600}}
.meta{{margin-top:14px;font-size:13px}} .summary-ctx{{margin:0 0 12px;font-size:13px;color:var(--muted);line-height:1.5}} .content{{padding:12px 20px 28px;overflow:auto}} .plan{{width:100%;border-collapse:collapse;font-size:13px}} .plan th{{background:#f8fafc;color:#1e3a8a;padding:12px 10px;border-bottom:2px solid #cbd5e1;text-align:left;font-size:11px}}
.plan td{{padding:10px;border-bottom:1px solid var(--border);vertical-align:top}} .tid{{font-family:monospace;color:#1e3a8a;font-weight:600;white-space:nowrap}}
.row-auto{{box-shadow:inset 3px 0 #86efac}} .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}}
.detail-stack{{display:flex;flex-direction:column;gap:6px;margin-top:8px}}
.size-detail{{background:#fff;border:1px solid var(--border);border-radius:10px;overflow:hidden}}
.size-detail[open]{{border-color:#93c5fd;box-shadow:0 1px 3px #1e3a8a14}}
.size-detail>summary{{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;padding:11px 14px;font-size:13px;font-weight:500;color:var(--title);background:#f8fafc;border-bottom:1px solid transparent}}
.size-detail[open]>summary{{border-bottom-color:var(--border);background:#eff6ff}}
.size-detail>summary::-webkit-details-marker{{display:none}}
.sum-idx{{font-family:monospace;color:var(--muted);min-width:2.5em}}
.sum-size{{font-family:monospace;color:#1e3a8a;font-weight:600}}
.sum-tput{{color:#334155}}
.sum-loss{{color:var(--muted);font-size:12px}}
.detail-inner{{padding:12px 14px 14px}}
.detail-note{{margin:0 0 10px;padding:8px 10px;font-size:12px;line-height:1.45;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:8px}}
.detail-toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:8px 0 12px}}
.detail-toolbar p{{margin:0;font-size:13px;color:var(--muted);flex:1}}
.detail-toolbar button{{font-family:inherit;font-size:12px;font-weight:600;padding:7px 14px;border-radius:8px;border:1px solid #cbd5e1;background:#fff;color:#1e3a8a;cursor:pointer}}
.detail-toolbar button:hover{{background:#eff6ff}}
.filter-bar{{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;margin:0 0 14px;padding:10px 14px;background:#f8fafc;border:1px solid var(--border);border-radius:10px}}
.filter-bar label{{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.filter-bar .filter-btns{{display:flex;flex-wrap:wrap;gap:6px}}
.filter-bar button{{font-family:inherit;font-size:12px;font-weight:600;padding:6px 12px;border-radius:999px;border:1px solid #cbd5e1;background:#fff;color:#334155;cursor:pointer}}
.filter-bar button:hover{{background:#eff6ff;border-color:#93c5fd}}
.filter-bar button.active{{background:#1e3a8a;color:#fff;border-color:#1e3a8a}}
.filter-count{{font-size:12px;color:var(--muted);margin-left:auto}}
.plan.detail td{{text-align:right}} .plan.detail td:first-child{{text-align:left;font-weight:500}}
tr.row-filter-hidden{{display:none}}
.size-detail.row-filter-hidden{{display:none}}
.cmp-section{{margin:36px 4px 8px;padding:0}}
.cmp-head{{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:12px;margin-bottom:14px;padding:0 4px}}
.cmp-section h2{{margin:0;font-size:18px;color:#0f172a;font-weight:600;letter-spacing:-.02em}}
.cmp-sub{{margin:6px 0 0;font-size:13px;color:#64748b;line-height:1.45}}
.cmp-legend{{display:flex;gap:8px;align-items:center;font-size:11px}}
.cmp-scroll{{overflow:auto;border:1px solid #dbeafe;border-radius:12px;box-shadow:0 4px 14px rgba(15,23,42,.06);max-height:min(70vh,720px)}}
.cmp-table{{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;background:#fff}}
.cmp-table thead{{position:sticky;top:0;z-index:2}}
.cmp-grp-row th{{padding:12px 14px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#fff;border-bottom:1px solid rgba(255,255,255,.15)}}
.cmp-col-row th{{padding:9px 14px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#e2e8f0;background:#0f172a}}
.cmp-size-h{{background:#334155;text-align:left;vertical-align:middle}}
.cmp-size-h span{{font-weight:500;text-transform:none;letter-spacing:0;color:#cbd5e1;font-size:10px}}
.grp-prev{{background:#475569;text-align:center}}
.grp-cur{{background:#1d4ed8;text-align:center}}
.cmp-table td{{padding:11px 14px;border-bottom:1px solid #eef2f7;vertical-align:middle}}
.cmp-table tbody tr:last-child td{{border-bottom:none}}
.cmp-table tbody tr:nth-child(even){{background:#f8fafc}}
.cmp-table tbody tr:hover{{background:#eff6ff}}
.cmp-size{{font-weight:600;font-variant-numeric:tabular-nums;color:#1e3a8a;font-size:13px;white-space:nowrap}}
.cmp-tput{{text-align:right}}
.cmp-tput-now .tput-val{{color:#1d4ed8}}
.tput-pair{{display:inline-flex;align-items:baseline;gap:4px;font-variant-numeric:tabular-nums}}
.tput-val{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;font-weight:600;color:#334155}}
.tput-plus{{color:#94a3b8;font-size:11px}}
.tput-unit{{font-size:10px;color:#94a3b8;margin-left:2px;font-weight:500}}
.cmp-loss{{text-align:center}}
.loss-pill{{display:inline-block;min-width:3.2em;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;font-family:ui-monospace,monospace}}
.loss-ok{{background:#ecfdf5;color:#047857;border:1px solid #a7f3d0}}
.loss-warn{{background:#fff7ed;color:#c2410c;border:1px solid #fed7aa}}
.loss-fail{{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca}}
@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1>UBR650 Jumbo Throughput Report</h1>
<p>Packet-size sweep · MAC/VLAN L2 · BTS ↔ CPE (bidirectional) · HT: {esc(ht_display)} · ch {channel} ({freq} MHz)</p></div>
<div class="hero-logo"><img src="{logo}" alt="Senao"></div></div>
<div class="container"><div class="cards">
<div class="card total"><h3>{n}</h3><p>Sizes tested</p></div>
<div class="card pass"><h3>{passed}</h3><p>Pass</p></div>
<div class="card fail"><h3>{failed}</h3><p>Fail</p></div>
<div class="card warn"><h3>{marginal}</h3><p>Marginal</p></div>
<div class="card peak"><h3>{peak:.0f}</h3><p>Peak total Mbps</p></div></div>
<div class="panel"><h2>Test configuration</h2>
<table class="testbed"><tbody>
<tr><th>BTS interface</th><td>{esc(bts_iface)}</td><th>CPE interface</th><td>{esc(cpe_iface)}</td></tr>
<tr><th>BTS SSH</th><td>{esc(bts_ip)}</td><th>CPE / SU SSH</th><td>{esc(", ".join(cpe_ips))}</td></tr>
<tr><th>HT mode(s)</th><td>{esc(ht_display)}</td><th>Channel / frequency</th><td>{channel} / {freq} MHz</td></tr>
<tr><th>MTU</th><td>{mtu}</td><th>Size list</th><td>{esc(sizes_mode)} ({n} steps)</td></tr>
<tr><th>Offered rate</th><td>{offered_mbps:.1f} Mbps per direction</td><th>Duration per size</th><td>{duration_s:.1f} s (+ {buffer_s:.1f} s buffer)</td></tr>
</tbody></table>
<div class="meta"><strong>Generated:</strong> {esc(ts.strftime("%Y-%m-%d %H:%M:%S"))} &nbsp;·&nbsp;
<strong>Best total throughput:</strong> {esc(best_label)} ({peak:.1f} Mbps combined RX)</div></div>
<div class="content"><h2 style="font-size:17px;color:#0f172a;margin:8px 0 8px">Summary — UBR650 throughput table</h2>
<p class="summary-ctx"><strong>5 GHz</strong> · {esc(ht_primary)} ({esc(bw_primary)}) · {freq} MHz (ch {channel}) · Tput DL+UL (Mbps) {offered_pair} · {duration_s:.0f} s per size</p>
<div class="filter-bar" id="result-filter">
<label for="result-filter">Result</label>
<div class="filter-btns" role="group" aria-label="Filter by result">
<button type="button" class="filter-btn active" data-filter="ALL">All ({n})</button>
<button type="button" class="filter-btn" data-filter="PASS">Pass ({passed})</button>
<button type="button" class="filter-btn" data-filter="MARGINAL">Marginal ({marginal})</button>
<button type="button" class="filter-btn" data-filter="FAIL">Fail ({failed})</button>
</div>
<span class="filter-count" id="filter-count">Showing {n} of {n} sizes</span>
</div>
<table class="plan" id="summary-plan"><thead><tr>
<th>S.No.</th><th>Packet size (B)</th><th>Drops (%)</th><th>Actual tput (Mbps)</th><th>Result</th>
</tr></thead><tbody id="summary-tbody">
{"".join(summary_rows)}
</tbody></table>
<h2 style="font-size:17px;color:#0f172a;margin:28px 0 6px">Per-size throughput detail</h2>
<div class="detail-toolbar">
<p id="detail-hint">{n} sizes — click a row to expand DL/UL counters. Use Result filter above for summary and detail.{" Sent/Received show — when this HTML was rebuilt from a .log only (no JSON)." if any_summary_only else ""}</p>
<button type="button" onclick="document.querySelectorAll('.size-detail').forEach(function(d){{d.open=true}})">Expand all</button>
<button type="button" onclick="document.querySelectorAll('.size-detail').forEach(function(d){{d.open=false}})">Collapse all</button>
</div>
<div class="detail-stack" id="detail-stack">
{"".join(detail_blocks)}
</div>
{compare_html}
</div></div></div>
<script>
(function() {{
  var total = {n};
  var btns = document.querySelectorAll(".filter-btn");
  var countEl = document.getElementById("filter-count");
  function applyFilter(status) {{
    var shown = 0;
    document.querySelectorAll("#summary-tbody tr[data-status]").forEach(function(tr) {{
      var ok = status === "ALL" || tr.getAttribute("data-status") === status;
      tr.classList.toggle("row-filter-hidden", !ok);
      if (ok) shown++;
    }});
    document.querySelectorAll("#detail-stack .size-detail[data-status]").forEach(function(el) {{
      var ok = status === "ALL" || el.getAttribute("data-status") === status;
      el.classList.toggle("row-filter-hidden", !ok);
    }});
    if (countEl) countEl.textContent = "Showing " + shown + " of " + total + " sizes";
    btns.forEach(function(b) {{
      b.classList.toggle("active", b.getAttribute("data-filter") === status);
    }});
  }}
  btns.forEach(function(btn) {{
    btn.addEventListener("click", function() {{
      applyFilter(btn.getAttribute("data-filter"));
    }});
  }});
}})();
</script>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


def main():
    p = argparse.ArgumentParser(description="Standalone jumbo throughput (UBR650-style)")
    p.add_argument("--bts-ip", default=BTS_SSH_IP)
    p.add_argument("--cpe-ips", default=",".join(CPE_SSH_IPS),
                   help="Comma-separated SU/CPE SSH IPs for MTU setup")
    p.add_argument("--device-password", default=os.environ.get("SMAC_DEVICE_PASSWORD", ""))
    p.add_argument("--radio", type=int, default=RADIO_IDX)
    p.add_argument("--htmodes", default="HT20", help="HT20,HT40+,HT80 — test each in order")
    p.add_argument("--channel", type=int, default=DEFAULT_CHANNEL,
                   help="5 GHz channel (UCI advwireless/wireless)")
    p.add_argument("--mtu", type=int, default=JUMBO_MTU)
    p.add_argument("--min-su", type=int, default=MIN_ASSOCIATED_SU,
                   help="Minimum associated SUs required on BTS")
    p.add_argument("--max-su", type=int, default=MAX_SU_SLOTS,
                   help="Max SUA slots to scan on BTS")
    p.add_argument("--link-wait", type=float, default=120.0)
    p.add_argument("--rf-wait", type=float, default=120.0, help="HT/channel settle timeout")
    p.add_argument("--no-setup", action="store_true",
                   help="Skip MTU + RF + link preflight (default: run setup)")
    p.add_argument("--setup-mtu", action="store_true", default=True)
    p.add_argument("--no-setup-mtu", action="store_true")
    p.add_argument("--setup-rf", action="store_true", default=True)
    p.add_argument("--no-setup-rf", action="store_true")
    p.add_argument("--preflight-only", action="store_true",
                   help="Configure/verify only — no traffic")
    p.add_argument("--bts", default=BTS_IFACE)
    p.add_argument("--cpe", default=CPE_IFACE)
    p.add_argument("--sizes", choices=("ubr650", "range", "list"), default="ubr650")
    p.add_argument("--size-list", default="",
                   help="Comma-separated frame sizes (B) when --sizes list")
    p.add_argument("--min", dest="min_size", type=int, default=1500)
    p.add_argument("--max", dest="max_size", type=int, default=9000)
    p.add_argument("--step", type=int, default=100)
    p.add_argument("--compare-log", default="",
                   help="Prior run .log — adds previous vs current table at end of HTML")
    p.add_argument("--compare-label", default="Previous run (751-size sweep)")
    p.add_argument("--mbps", type=float, default=80.0)
    p.add_argument("--duration", type=float, default=20.0)
    p.add_argument("--buffer", type=float, default=3.0)
    p.add_argument("--pause", type=float, default=2.0)
    p.add_argument("--no-html", action="store_true", help="Skip HTML report at end")
    p.add_argument("--report-dir", default=".", help="Directory for Senao_*.html report")
    p.add_argument("--loss-pass-pct", type=float, default=1.0,
                   help="Mark PASS in HTML when loss %% is at or below this")
    args = p.parse_args()

    if args.no_setup_mtu:
        args.setup_mtu = False
    if args.no_setup_rf:
        args.setup_rf = False
    args.cpe_ips = _parse_cpe_ips(args.cpe_ips)

    pwd = args.device_password
    ht_list = [x.strip() for x in args.htmodes.split(",") if x.strip()]
    do_setup = not args.no_setup

    if do_setup:
        preflight_setup(args, ht_list)
    else:
        print(f"[SSH] {SSH_USER}@{args.bts_ip} (wifi{args.radio})")
        st = read_radio(args.bts_ip, pwd, args.radio)
        print(f"  UCI={st['uci']}  running={st['running']}  {st['channel']}")

    if args.preflight_only:
        return

    if os.geteuid() != 0:
        sys.exit("[ERROR] Traffic requires: sudo python3 jumbo_frame_throughput_test.py …")

    bts_f = [(src, dst, vlan) for d, src, dst, vlan in MAC_VLAN_TABLE if d == "BTS"]
    cpe_f = [(src, dst, vlan) for d, src, dst, vlan in MAC_VLAN_TABLE if d == "CPE"]
    if not bts_f or not cpe_f:
        sys.exit("Edit MAC_VLAN_TABLE in this file.")

    sizes = _sizes_arg(args.sizes, args.min_size, args.max_size, args.step, args.size_list)
    compare_by_size: dict[int, dict] | None = None
    if args.compare_log:
        try:
            with open(args.compare_log, encoding="utf-8", errors="replace") as cf:
                compare_by_size = parse_results_from_log(cf.read())
            print(f"[COMPARE] Loaded {len(compare_by_size)} sizes from {args.compare_log}")
        except OSError as e:
            print(f"[WARN] --compare-log not read: {e}")
    sizes_mode_label = args.sizes
    if args.sizes == "list":
        sizes_mode_label = f"list ({len(sizes)} sizes)"
    elif args.sizes == "range":
        sizes_mode_label = f"range {args.min_size}-{args.max_size} step {args.step}"
    all_rows = []

    for ht in ht_list:
        if args.setup_rf and (len(ht_list) > 1 or args.no_setup):
            try:
                apply_ht(args.bts_ip, pwd, args.radio, ht, wait_s=args.rf_wait)
                wait_for_su_links(
                    args.bts_ip, pwd, args.radio,
                    args.min_su, args.max_su, args.link_wait,
                )
            except RuntimeError as e:
                print(f"[WARN] Skip HT {ht}: {e}")
                continue
        print(f"\n=== HT {_canonical_ht(ht)} | {len(sizes)} sizes | {args.mbps} Mbps/dir ===")
        for i, sz in enumerate(sizes, 1):
            print(f"  [{i}/{len(sizes)}] {sz} B …", flush=True)
            try:
                dl, ul = run_bidir_test(
                    args.bts, args.cpe, bts_f, cpe_f,
                    sz, args.mbps, args.duration, args.buffer,
                )
                row = dict(
                    sno=i, size=sz, ht=_canonical_ht(ht),
                    dl=dl["rx_mbps"], ul=ul["rx_mbps"],
                    drops=max(dl["loss_pct"], ul["loss_pct"]),
                    total=dl["rx_mbps"] + ul["rx_mbps"],
                    dl_detail=dl, ul_detail=ul,
                )
                print(f"       {dl['rx_mbps']:.1f}+{ul['rx_mbps']:.1f} Mbps  loss {row['drops']:.2f}%")
            except Exception as exc:
                row = dict(
                    sno=i, size=sz, ht=_canonical_ht(ht), dl=0, ul=0, drops=100, total=0,
                    dl_detail={}, ul_detail={},
                )
                print(f"       FAIL: {exc}")
            all_rows.append(row)
            time.sleep(args.pause)

    print("\n  S.no   Size     HT      DL+UL Mbps   Drops%   Total")
    print("  " + "-" * 52)
    for r in all_rows:
        print(f"  {r['sno']:4}  {r['size']:5}  {r['ht']:6}  "
              f"{r['dl']:.0f}+{r['ul']:.0f}        {r['drops']:5.2f}   {r['total']:.1f}")

    if all_rows and not args.no_html:
        rerun_ts = datetime.datetime.now()
        report_path = write_html_report(
            rows=all_rows,
            out_dir=args.report_dir,
            bts_iface=args.bts,
            cpe_iface=args.cpe,
            bts_ip=args.bts_ip,
            cpe_ips=args.cpe_ips,
            channel=args.channel,
            htmodes=ht_list,
            mtu=args.mtu,
            offered_mbps=args.mbps,
            duration_s=args.duration,
            buffer_s=args.buffer,
            loss_pass_pct=args.loss_pass_pct,
            sizes_mode=sizes_mode_label,
            n_flows=len(bts_f),
            compare_by_size=compare_by_size,
            compare_label=args.compare_label,
            current_label=f"Rerun {rerun_ts.strftime('%Y-%m-%d %H:%M')}",
        )
        json_path = os.path.splitext(report_path)[0] + ".json"
        write_results_json(all_rows, json_path)
        print(f"\n[REPORT] HTML: {os.path.abspath(report_path)}")
        print(f"[REPORT] JSON: {os.path.abspath(json_path)}")


if __name__ == "__main__":
    main()
