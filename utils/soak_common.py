"""Shared plumbing for long-running soak scripts (hard reboot, factory reset).

These soaks run for many hours outside pytest, so everything here is built to
survive partial failure: every device read tolerates an unreachable peer, and
run state is checkpointed to disk after each iteration so an interrupted soak
resumes instead of restarting.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncssh
import yaml

ROOT = Path(__file__).resolve().parents[1]

WIFI_EVENTS_LOG = "/tmp/kwn-wifi{idx}-events.log"
DYING_GASP_REMOTE_LOG = "/tmp/kwn-dying-gasp-remote.log"
DYING_GASP_LOCAL_LOG = "/tmp/kwn-dying-gasp-local.log"

# Emitted by /usr/sbin/netlink_events/DGASP_REMOTE.sh when the peer loses power.
DGASP_BLOCK_MARKER = "Dying GASP Remote event"
DGASP_EVENT_MARKER = "Power Off"

EVENT_TS_RE = re.compile(r"^([A-Z][a-z]{2} [A-Z][a-z]{2} \d{2} \d{2}:\d{2}:\d{2} \d{4}):")
LINK_UP_RE = re.compile(r"established link with", re.IGNORECASE)
LINK_DOWN_RE = re.compile(r"terminated link", re.IGNORECASE)
LINK_REASON_RE = re.compile(r"Reason:\s*(.+?)\s*$", re.IGNORECASE)
LINK_UPTIME_RE = re.compile(r"Uptime:\s*([0-9:]+)")
SNR_RE = re.compile(r"SNR \( A1 : (\d+) dB A2 : (\d+) dB \)")


def load_profile(name_or_path: str) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.exists():
        path = ROOT / "profiles" / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {name_or_path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def profile_devices(profile: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return (bts_ip, cpe_ip, username, password) from the dut block."""
    dut = profile.get("dut", {}) or {}
    bts = str(dut.get("local_ip", "")).strip()
    remotes = dut.get("remote_ips") or []
    cpe = str(remotes[0]).strip() if remotes else ""
    return bts, cpe, str(dut.get("username", "root")), str(dut.get("password", ""))


# --------------------------------------------------------------------------
# SSH
# --------------------------------------------------------------------------


class CpeTunnel:
    """CPE connection that owns the BTS jump connection behind it."""

    def __init__(self, cpe_conn, bts_conn):
        self._cpe = cpe_conn
        self._bts = bts_conn

    def __getattr__(self, item):
        return getattr(self._cpe, item)

    def close(self) -> None:
        for conn in (self._cpe, self._bts):
            try:
                conn.close()
            except Exception:
                pass


async def open_ssh(host: str, username: str, password: str, *, timeout: int = 20):
    return await asyncssh.connect(
        host,
        username=username,
        password=password,
        known_hosts=None,
        connect_timeout=timeout,
    )


async def open_cpe_ssh(
    bts_host: str,
    cpe_host: str,
    username: str,
    password: str,
    *,
    timeout: int = 20,
):
    """Direct SSH to the CPE, falling back to a tunnel through the BTS.

    The lab PC has no working direct path to the CPE, so the tunnel is the
    normal case rather than the exception.
    """
    try:
        conn = await open_ssh(cpe_host, username, password, timeout=timeout)
        probe = await run_cmd(conn, "echo CPE_DIRECT_OK", timeout=15)
        if "CPE_DIRECT_OK" in probe:
            return conn
        conn.close()
    except Exception:
        pass

    bts_conn = await open_ssh(bts_host, username, password, timeout=timeout)
    try:
        cpe_conn = await asyncssh.connect(
            cpe_host,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=timeout,
            tunnel=bts_conn,
        )
    except Exception:
        try:
            bts_conn.close()
        except Exception:
            pass
        raise
    return CpeTunnel(cpe_conn, bts_conn)


async def run_cmd(conn, command: str, *, timeout: int = 30) -> str:
    try:
        result = await asyncio.wait_for(conn.run(command, check=False), timeout=timeout)
    except asyncio.TimeoutError:
        return ""
    return f"{result.stdout or ''}{result.stderr or ''}"


async def close_ssh(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Device reads
# --------------------------------------------------------------------------


async def read_uptime(conn) -> float | None:
    raw = await run_cmd(conn, "cat /proc/uptime", timeout=15)
    try:
        return float(raw.strip().split()[0])
    except (IndexError, ValueError):
        return None


async def read_link_count(conn, radio_idx: int = 1) -> int:
    raw = await run_cmd(
        conn,
        f"cat /sys/class/kwn/wifi{radio_idx}/statistics/links 2>/dev/null || echo 0",
        timeout=15,
    )
    try:
        return int(raw.strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0


async def read_file_lines(conn, path: str) -> int:
    raw = await run_cmd(conn, f"wc -l < {path} 2>/dev/null || echo 0", timeout=15)
    try:
        return int(raw.strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0


async def read_file_from(conn, path: str, start_line: int) -> str:
    """Read a log file from start_line onward, so we only see new entries."""
    raw = await run_cmd(
        conn, f"tail -n +{max(start_line + 1, 1)} {path} 2>/dev/null || true", timeout=20
    )
    return raw.strip()


async def read_boot_logs(conn, *, lines: int = 40) -> str:
    grep = (
        "logread 2>/dev/null | grep -Ei 'reboot|booting|kernel|procd|sysinit|jffs2' "
        f"| tail -n {lines}"
    )
    out = (await run_cmd(conn, grep, timeout=45)).strip()
    if not out:
        out = (await run_cmd(conn, f"dmesg 2>/dev/null | tail -n {lines}", timeout=45)).strip()
    return out


async def ping_from_device(conn, target: str, *, count: int = 3) -> bool:
    flag = "-6" if ":" in target else ""
    raw = await run_cmd(conn, f"ping {flag} -c {count} -W 2 {target}", timeout=count * 4 + 20)
    match = re.search(r"(\d+) packets received|(\d+) received", raw)
    if not match:
        return False
    received = next((g for g in match.groups() if g is not None), "0")
    return int(received) > 0


async def ping_from_pc(target: str, *, count: int = 2, timeout_s: int = 2) -> bool:
    cmd = ["ping"]
    if ":" in target:
        cmd.append("-6")
    cmd += ["-c", str(count), "-W", str(timeout_s), target]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=count * timeout_s + 10)
    except (asyncio.TimeoutError, OSError):
        return False
    return b" 0 received" not in stdout and proc.returncode == 0


async def wait_until(
    predicate,
    *,
    timeout_s: float,
    interval_s: float = 3.0,
    want: bool = True,
) -> float | None:
    """Poll predicate until it matches `want`. Returns elapsed seconds, or None on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        try:
            if bool(await predicate()) is want:
                return round(time.monotonic() - start, 1)
        except Exception:
            if want is False:
                return round(time.monotonic() - start, 1)
        await asyncio.sleep(interval_s)
    return None


# --------------------------------------------------------------------------
# Event log parsing
# --------------------------------------------------------------------------


def parse_event_time(line: str) -> datetime | None:
    match = EVENT_TS_RE.match(line.strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


@dataclass
class LinkEvents:
    """Parsed view of the wireless events log for one iteration."""

    raw: str = ""
    link_down_at: str = ""
    link_down_reason: str = ""
    link_uptime_before: str = ""
    link_up_at: str = ""
    snr_a1: int | None = None
    snr_a2: int | None = None
    power_off_seen: bool = False
    link_reform_seconds: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_link_events(raw: str) -> LinkEvents:
    events = LinkEvents(raw=raw.strip())
    down_ts: datetime | None = None
    up_ts: datetime | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        ts = parse_event_time(stripped)
        if DGASP_EVENT_MARKER.lower() in stripped.lower():
            events.power_off_seen = True
            if ts and not events.link_down_at:
                events.link_down_at = ts.isoformat(sep=" ")
                down_ts = ts
        if LINK_DOWN_RE.search(stripped):
            if ts:
                events.link_down_at = ts.isoformat(sep=" ")
                down_ts = ts
            reason = LINK_REASON_RE.search(stripped)
            if reason:
                events.link_down_reason = reason.group(1)
            uptime = LINK_UPTIME_RE.search(stripped)
            if uptime:
                events.link_uptime_before = uptime.group(1)
        if LINK_UP_RE.search(stripped):
            if ts:
                events.link_up_at = ts.isoformat(sep=" ")
                up_ts = ts
            snr = SNR_RE.search(stripped)
            if snr:
                events.snr_a1 = int(snr.group(1))
                events.snr_a2 = int(snr.group(2))

    if down_ts and up_ts and up_ts >= down_ts:
        events.link_reform_seconds = (up_ts - down_ts).total_seconds()
    return events


def has_dying_gasp(dgasp_text: str, events: LinkEvents) -> bool:
    return DGASP_BLOCK_MARKER.lower() in (dgasp_text or "").lower() or events.power_off_seen


def has_reboot_markers(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        marker in lowered
        for marker in ("reboot", "booting", "kernel", "procd", "sysinit", "jffs2", "watchdog")
    )


def uptime_indicates_reboot(before: float | None, after: float | None) -> bool:
    """A genuine reboot resets uptime, so `after` must be both small and lower."""
    if after is None:
        return False
    if after > 900:
        return False
    if before is None:
        return True
    return after < before


# --------------------------------------------------------------------------
# Checkpointed run state
# --------------------------------------------------------------------------


@dataclass
class Iteration:
    index: int
    target: str
    started_at: str = ""
    finished_at: str = ""
    passed: bool = False
    summary: str = ""
    checks: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, str]] = field(default_factory=list)

    def add_event(self, step: str, detail: str) -> None:
        self.events.append(
            {"time": datetime.now().strftime("%H:%M:%S"), "step": step, "detail": detail}
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SoakState:
    kind: str
    started_at: str
    total_iterations: int
    testbed: dict[str, Any] = field(default_factory=dict)
    iterations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return len(self.iterations)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.iterations if item.get("passed"))

    @property
    def failed(self) -> int:
        return self.completed - self.passed

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SoakState | None":
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return cls(**data)


def metric_stats(iterations: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    values = [
        float(item["metrics"][key])
        for item in iterations
        if isinstance(item.get("metrics"), dict) and item["metrics"].get(key) is not None
    ]
    if not values:
        return {"min": None, "max": None, "avg": None, "count": 0}
    return {
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "avg": round(sum(values) / len(values), 1),
        "count": len(values),
    }
