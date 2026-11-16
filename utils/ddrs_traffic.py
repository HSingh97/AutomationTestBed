"""DDRS suite throughput — mac_vlan_traffic_test_mf.py on the lab PC (isolated)."""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from utils.lab_nic_guard import assert_no_lab_nic_down

if TYPE_CHECKING:
    from utils.ddrs_flows import DdrsConfig

FINAL_SUMMARY_RE = re.compile(
    r"BTS → CPE\s+"
    r"(?P<dl_tx>[\d,]+)\s+(?P<dl_rx>[\d,]+)\s+(?P<dl_drops>[\d,]+)\s+"
    r"(?P<dl_loss>[\d.]+)%\s+"
    r"(?P<dl_tx_mbps>[\d.]+)\s+(?P<dl_rx_mbps>[\d.]+)\s+"
    r"(?P<dl_avg>[\d.]+)\s+(?P<dl_min>[\d.]+)\s+(?P<dl_max>[\d.]+)\s+"
    r"(?P<dl_jitter>[\d.]+)"
    r".*?"
    r"CPE → BTS\s+"
    r"(?P<ul_tx>[\d,]+)\s+(?P<ul_rx>[\d,]+)\s+(?P<ul_drops>[\d,]+)\s+"
    r"(?P<ul_loss>[\d.]+)%\s+"
    r"(?P<ul_tx_mbps>[\d.]+)\s+(?P<ul_rx_mbps>[\d.]+)\s+"
    r"(?P<ul_avg>[\d.]+)\s+(?P<ul_min>[\d.]+)\s+(?P<ul_max>[\d.]+)\s+"
    r"(?P<ul_jitter>[\d.]+)",
    re.S,
)

ONE_DIR_ROW_RE = re.compile(
    r"(?P<label>BTS → CPE|CPE → BTS)\s+"
    r"(?P<tx>[\d,]+)\s+(?P<rx>[\d,]+)\s+(?P<drops>[\d,]+)\s+"
    r"(?P<loss>[\d.]+)%\s+"
    r"(?P<tx_mbps>[\d.]+)\s+(?P<rx_mbps>[\d.]+)\s+"
    r"(?P<avg>[\d.]+)\s+(?P<min>[\d.]+)\s+(?P<max>[\d.]+)\s+"
    r"(?P<jitter>[\d.]+)"
)


@dataclass
class DdrsTrafficResult:
    dl_loss_pct: float | None = None
    ul_loss_pct: float | None = None
    dl_rx_mbps: float | None = None
    ul_rx_mbps: float | None = None
    dl_jitter_ms: float | None = None
    ul_jitter_ms: float | None = None
    dl_avg_ms: float | None = None
    ul_avg_ms: float | None = None
    dl_rx_pkts: float | None = None
    ul_rx_pkts: float | None = None
    raw_tail: str = ""


def parse_mac_vlan_summary(output: str) -> DdrsTrafficResult:
    text = output or ""
    anchor = text.rfind("FINAL SUMMARY")
    region = text[anchor:] if anchor >= 0 else text
    matches = list(FINAL_SUMMARY_RE.finditer(region))
    if not matches and anchor >= 0:
        matches = list(FINAL_SUMMARY_RE.finditer(text))
    if matches:
        g = matches[-1].groupdict()

        def f(name: str) -> float:
            return float(str(g[name]).replace(",", ""))

        return DdrsTrafficResult(
            dl_loss_pct=f("dl_loss"),
            ul_loss_pct=f("ul_loss"),
            dl_rx_mbps=f("dl_rx_mbps"),
            ul_rx_mbps=f("ul_rx_mbps"),
            dl_jitter_ms=f("dl_jitter"),
            ul_jitter_ms=f("ul_jitter"),
            dl_avg_ms=f("dl_avg"),
            ul_avg_ms=f("ul_avg"),
            dl_rx_pkts=f("dl_rx"),
            ul_rx_pkts=f("ul_rx"),
            raw_tail=matches[-1].group(0),
        )

    result = DdrsTrafficResult(raw_tail=text[-4000:])
    rows = list(ONE_DIR_ROW_RE.finditer(region))
    if not rows and anchor >= 0:
        rows = list(ONE_DIR_ROW_RE.finditer(text))
    if not rows:
        return result

    def f_row(g: dict[str, str], name: str) -> float:
        return float(str(g[name]).replace(",", ""))

    for match in rows:
        g = match.groupdict()
        if g["label"].startswith("BTS"):
            result.dl_loss_pct = f_row(g, "loss")
            result.dl_rx_mbps = f_row(g, "rx_mbps")
            result.dl_jitter_ms = f_row(g, "jitter")
            result.dl_avg_ms = f_row(g, "avg")
            result.dl_rx_pkts = f_row(g, "rx")
        else:
            result.ul_loss_pct = f_row(g, "loss")
            result.ul_rx_mbps = f_row(g, "rx_mbps")
            result.ul_jitter_ms = f_row(g, "jitter")
            result.ul_avg_ms = f_row(g, "avg")
            result.ul_rx_pkts = f_row(g, "rx")
    result.raw_tail = rows[-1].group(0)
    return result


def _ssh_lab(cfg: "DdrsConfig", remote_cmd: str, *, timeout_s: int = 600) -> str:
    assert_no_lab_nic_down(remote_cmd)
    cmd = [
        "sshpass",
        "-p",
        cfg.lab_pc_password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=15",
        cfg.lab_pc_ssh,
        remote_cmd,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    if proc.returncode != 0 and not out.strip():
        raise RuntimeError(
            f"[ddrs] lab PC SSH failed (rc={proc.returncode}): {remote_cmd[:200]}"
        )
    return out


def run_mac_vlan_traffic(
    cfg: "DdrsConfig",
    *,
    case_id: str,
    su_count: int | None = None,
    dl_mbps_per_su: float | None = None,
    ul_mbps_per_su: float | None = None,
    duration_s: float | None = None,
    mode: str = "bidir",
    mac_refresh_s: int | None = None,
) -> DdrsTrafficResult:
    """
    Run mac_vlan_traffic_test_mf.py on the lab PC for ``su_count`` SU pairs.

    Uses the same wrapper pattern as the traffic script's MAC_VLAN_TABLE patching.
    ``mode`` is downlink / uplink / bidir (script default is bidir).
    """
    su = max(1, int(su_count or cfg.traffic_su_count))
    per_dl = cfg.dl_mbps_per_su if dl_mbps_per_su is None else float(dl_mbps_per_su)
    per_ul = cfg.ul_mbps_per_su if ul_mbps_per_su is None else float(ul_mbps_per_su)
    dl = per_dl * su
    ul = per_ul * su
    duration = float(cfg.traffic_duration_s if duration_s is None else duration_s)
    traffic_mode = str(mode or "bidir").strip().lower()
    if traffic_mode not in {"downlink", "uplink", "bidir"}:
        traffic_mode = "bidir"
    refresh = int(mac_refresh_s) if mac_refresh_s is not None else 5
    stats_iv = max(1, int(round(float(cfg.stats_interval_s))))
    script = cfg.mac_vlan_script
    log_path = f"/tmp/ddrs_{case_id.lower()}_su{su}_out.log"
    wrap = f"/tmp/ddrs_{case_id.lower()}_run_su{su}.py"

    extra_mode = ""
    if traffic_mode != "bidir":
        extra_mode = f'    "--mac-refresh", "{refresh}",\n'

    wrap_script = f"""import runpy
import sys

SCRIPT = {script!r}
SU = {su}
sys.argv = [
    SCRIPT,
    "--bts", {cfg.bts_iface!r},
    "--cpe", {cfg.cpe_iface!r},
    "--duration", "{duration:g}",
    "--buffer", "{cfg.buffer_s:g}",
    "--bts-rate-mbps", "{dl:g}",
    "--cpe-rate-mbps", "{ul:g}",
    "--stats-interval", "{stats_iv}",
    "--mode", {traffic_mode!r},
{extra_mode}]
ns = runpy.run_path(SCRIPT, run_name="mac_vlan_ddrs")
table = list(ns.get("MAC_VLAN_TABLE") or [])
bts = [r for r in table if r[0] == "BTS"][:SU]
cpe = [r for r in table if r[0] == "CPE"][:SU]
patched = bts + cpe
main_fn = ns["main"]
main_fn.__globals__["MAC_VLAN_TABLE"] = patched
ns["MAC_VLAN_TABLE"] = patched
print(f"[ddrs] mac_vlan SU={{SU}} DL={{'{dl:g}'}} UL={{'{ul:g}'}} Mbps duration={{'{duration:g}'}}s")
main_fn()
"""

    blocking = f"""
set -e
WRAP={shlex.quote(wrap)}
LOG={shlex.quote(log_path)}
rm -f "$WRAP"
sudo rm -f "$LOG"
cat > "$WRAP" << 'PYEOF'
{wrap_script}PYEOF
test -f "$WRAP"
sudo sh -c "python3 '$WRAP' 2>&1 | tee '$LOG'"
"""

    print(
        f"[ddrs][{case_id}] mac_vlan_traffic_test_mf.py: {su} SU, "
        f"mode={traffic_mode} DL={dl:g} UL={ul:g} Mbps, {duration:g}s …",
        flush=True,
    )
    started = time.time()
    stdout = _ssh_lab(
        cfg,
        blocking,
        timeout_s=max(120.0, duration + cfg.buffer_s + 180),
    )
    elapsed = time.time() - started
    log_out = ""
    try:
        log_out = _ssh_lab(cfg, f"cat {shlex.quote(log_path)} 2>/dev/null || true", timeout_s=60)
    except Exception:
        pass

    parsed = parse_mac_vlan_summary(stdout)
    combined = (
        stdout
        if parsed.dl_loss_pct is not None or parsed.ul_loss_pct is not None
        else (log_out or stdout)
    )
    result = parse_mac_vlan_summary(combined)
    print(
        f"[ddrs][{case_id}] traffic done in {elapsed:.0f}s — "
        f"DL loss={result.dl_loss_pct}% rx={result.dl_rx_mbps} Mbps "
        f"UL rx={result.ul_rx_mbps} Mbps",
        flush=True,
    )
    if result.dl_loss_pct is None and result.ul_loss_pct is None:
        print(f"[ddrs][{case_id}] WARN: no FINAL SUMMARY; tail:\n{(combined or '')[-2000:]}", flush=True)
    return result
