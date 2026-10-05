"""Asymmetric channel bandwidth.

Downlink width is the BTS ``wireless.wifi1.htmode``.
Uplink width is the CPE ``advwireless.ath1.force_bw`` (20/40/80, 0 = auto).
``dlulratio`` is duty-cycle and is not used here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from traffic.dut_radio_config import parse_running_htmode
from traffic.operating_rate_table import uci_htmode_value
from traffic.vlan_lab import _ssh_cmd, lab_hosts

ARTIFACT_DIR = Path("reports/artifacts/acb_evidence")

_ORIG: dict[str, str] | None = None
_PAIR_CACHE: dict[tuple[int, int], dict[str, Any]] = {}


def ssh(profile_active: dict[str, Any], remote: str, *, host: str = "", timeout_s: int = 45) -> str:
    dut = (profile_active or {}).get("dut") or {}
    hosts = lab_hosts(profile_active)
    target = host or str(
        dut.get("local_ip") or dut.get("ssh_host") or hosts.get("dut_ipv4") or hosts.get("bat_host") or ""
    ).split("/")[0]
    user = str(hosts.get("bat_user") or dut.get("username") or "root")
    password = str(hosts.get("bat_password") or dut.get("password") or "")
    return _ssh_cmd(target, password, remote, user=user, timeout_s=timeout_s)


def cpe_host(profile_active: dict[str, Any]) -> str:
    dut = (profile_active or {}).get("dut") or {}
    remotes = list(dut.get("remote_ips") or [])
    if not remotes and dut.get("remote_ip"):
        remotes = [str(dut.get("remote_ip"))]
    if not remotes:
        return ""
    return str(remotes[0]).split("/")[0]


def write_evidence(case_id: str, payload: dict[str, Any]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{case_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[acb] evidence -> {path}")


def _int_token(text: str) -> int:
    raw = (text or "").strip().splitlines()
    tok = raw[-1].strip() if raw else ""
    if tok.isdigit():
        return int(tok)
    return 0


def _force_bw_value(text: str) -> int:
    """Accept only a real UCI value. SSH errors contain the CPE IP and must not count."""
    raw = (text or "").strip().splitlines()
    tok = raw[-1].strip() if raw else ""
    if tok in {"0", "20", "40", "80"}:
        return int(tok)
    return -1


def _remember_original(profile_active: dict[str, Any]) -> None:
    global _ORIG
    if _ORIG is not None:
        return
    bts_ht = ssh(profile_active, "uci -q get wireless.wifi1.htmode || true")
    cpe = cpe_host(profile_active)
    cpe_bw = ""
    if cpe:
        cpe_bw = ssh(profile_active, "uci -q get advwireless.ath1.force_bw || echo 0", host=cpe)
    _ORIG = {
        "bts_htmode": (bts_ht.strip().splitlines() or ["HT80"])[-1].strip() or "HT80",
        "cpe_force_bw": (cpe_bw.strip().splitlines() or ["0"])[-1].strip() or "0",
        "cpe": cpe,
    }
    print(f"[acb] original BTS htmode={_ORIG['bts_htmode']} CPE force_bw={_ORIG['cpe_force_bw']}")


def restore_acb(profile_active: dict[str, Any]) -> None:
    if not _ORIG:
        return
    print(f"[acb] restore BTS htmode={_ORIG['bts_htmode']}")
    ssh(
        profile_active,
        f"ucidyn set wireless.wifi1.htmode {_ORIG['bts_htmode']}; ucidyn apply; echo BTS_RESTORE:$?",
        timeout_s=90,
    )
    _wait_link_only(profile_active, timeout_s=120)
    cpe = _ORIG.get("cpe") or cpe_host(profile_active)
    if cpe:
        print(f"[acb] restore CPE force_bw={_ORIG['cpe_force_bw']}")
        ssh(
            profile_active,
            f"ucidyn set advwireless.ath1.force_bw {_ORIG['cpe_force_bw']}; ucidyn apply; echo CPE_RESTORE:$?",
            host=cpe,
            timeout_s=60,
        )


def _su_index(profile_active: dict[str, Any]) -> int:
    blob = ssh(
        profile_active,
        "for i in 1 2 3 4 5 6 7 8; do "
        "a=$(cat /sys/class/kwn/sua$i/statistics/associd 2>/dev/null || echo 0); "
        "if [ \"$a\" != 0 ] && [ -n \"$a\" ]; then echo $i; exit 0; fi; done; echo 0",
    )
    return _int_token(blob)


def read_link_snapshot(profile_active: dict[str, Any]) -> dict[str, Any]:
    idx = _su_index(profile_active)
    mode = ssh(profile_active, "cfg80211tool ath1 get_mode 2>/dev/null || true")
    ht = ssh(profile_active, "uci -q get wireless.wifi1.htmode || true")
    base = f"/sys/class/kwn/sua{idx}/statistics" if idx else ""
    if idx:
        stats = ssh(
            profile_active,
            f"echo TX=$(cat {base}/tx_rate 2>/dev/null); "
            f"echo RX=$(cat {base}/rx_rate 2>/dev/null); "
            f"echo TXMCS=$(cat {base}/tx_rate_mcs 2>/dev/null); "
            f"echo RXMCS=$(cat {base}/rx_rate_mcs 2>/dev/null); "
            f"echo LINKS=$(cat /sys/class/kwn/wifi1/statistics/links 2>/dev/null)",
        )
    else:
        stats = ""
    cpe = cpe_host(profile_active)
    force = ""
    if cpe:
        force = ssh(profile_active, "uci -q get advwireless.ath1.force_bw || echo 0", host=cpe)
    running = parse_running_htmode(mode) or ""
    force_bw = _force_bw_value(force)
    return {
        "su": idx,
        "links": _field(stats, "LINKS"),
        "tx_rate": _field(stats, "TX"),
        "rx_rate": _field(stats, "RX"),
        "tx_mcs": _field(stats, "TXMCS"),
        "rx_mcs": _field(stats, "RXMCS"),
        "bts_htmode": (ht.strip().splitlines() or [""])[-1].strip(),
        "running_htmode": running,
        "force_bw": force_bw,
        "get_mode": mode.strip(),
    }


def _field(blob: str, name: str) -> int:
    for line in blob.splitlines():
        if line.startswith(f"{name}="):
            return _int_token(line.split("=", 1)[1])
    return 0


def apply_pair(profile_active: dict[str, Any], dl_mhz: int, ul_mhz: int) -> dict[str, Any]:
    """Set BTS downlink htmode and CPE uplink force_bw. Cached per pair."""
    key = (int(dl_mhz), int(ul_mhz))
    if key in _PAIR_CACHE:
        return _PAIR_CACHE[key]
    _remember_original(profile_active)
    cpe = cpe_host(profile_active)
    if not cpe:
        snap = {"ok": False, "error": "CPE IP missing (--remote-ip)"}
        _PAIR_CACHE[key] = snap
        return snap

    dl_ht = uci_htmode_value(f"HT{int(dl_mhz)}")
    current = ssh(profile_active, "uci -q get wireless.wifi1.htmode || true")
    current_ht = (current.strip().splitlines() or [""])[-1].strip()
    notes: list[str] = []
    # Set uplink while the CPE is still reachable, then change downlink.
    print(f"[acb] CPE {cpe} force_bw -> {ul_mhz}")
    out = ssh(
        profile_active,
        f"ucidyn set advwireless.ath1.force_bw {int(ul_mhz)}; ucidyn apply; echo BW_RC:$?",
        host=cpe,
        timeout_s=60,
    )
    notes.append(out[-400:])
    if "BW_RC:0" not in out:
        snap = {"ok": False, "error": f"CPE force_bw set failed: {out[-300:]}", "force_bw": -1}
        return snap
    if dl_ht.upper() not in current_ht.upper():
        print(f"[acb] BTS htmode {current_ht} -> {dl_ht}")
        out = ssh(
            profile_active,
            f"ucidyn set wireless.wifi1.htmode {dl_ht}; ucidyn apply; echo HT_RC:$?",
            timeout_s=90,
        )
        notes.append(out[-500:])
    snap = _wait_link(profile_active, dl_mhz, ul_mhz, timeout_s=150)
    snap["apply_notes"] = notes
    snap["wanted_dl"] = int(dl_mhz)
    snap["wanted_ul"] = int(ul_mhz)
    if snap.get("ok"):
        _PAIR_CACHE[key] = snap
    return snap


def _wait_link_only(profile_active: dict[str, Any], timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        links = _int_token(ssh(profile_active, "cat /sys/class/kwn/wifi1/statistics/links 2>/dev/null || echo 0"))
        if links >= 1:
            return
        time.sleep(4)


def _wait_link(profile_active: dict[str, Any], dl_mhz: int, ul_mhz: int, timeout_s: int = 150) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = read_link_snapshot(profile_active)
        running = str(last.get("running_htmode") or "")
        width_ok = str(dl_mhz) in running
        ul_ok = int(last.get("force_bw") or 0) == int(ul_mhz)
        linked = int(last.get("links") or 0) >= 1 and int(last.get("tx_rate") or 0) > 0 and int(last.get("rx_rate") or 0) > 0
        print(
            f"[acb] wait running={running} force_bw={last.get('force_bw')} "
            f"tx={last.get('tx_rate')} rx={last.get('rx_rate')} links={last.get('links')}"
        )
        if width_ok and ul_ok and linked:
            last["ok"] = True
            return last
        time.sleep(4)
    last["ok"] = False
    last["error"] = (
        f"DL {dl_mhz}/UL {ul_mhz} not up in {timeout_s}s "
        f"(running={last.get('running_htmode')}, force_bw={last.get('force_bw')}, "
        f"tx={last.get('tx_rate')}, rx={last.get('rx_rate')})"
    )
    return last
