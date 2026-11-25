"""CPU and memory regression samples.

Five cases, polled every 5 seconds for a configurable duration.
CPU is the radio's sysInfoCPUUsage (the same value as /tmp/cpu_usage).
Memory is UCD-SNMP real RAM. Station count still uses wlanconfig; this
agent's SNMP tree has no SU table. SNMP, SSH, and the LuCI tunnel run on
this host. The BTS must be reachable here; there is no jump through another PC.

1. No station, web page closed
2. Station associated, web page closed
3. No station, web page open
4. Station associated, web page open
5. Station associated, web page open, throughput running

The link is dropped by moving the BTS SSID off the CPE's SSID and restored
afterwards. The radio is not taken down with ``wifi down``. No-link cases
run first, then link cases, so the SSID changes only between those groups.
"""

from __future__ import annotations

import csv
import re
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

from utils.grafana_matrix_report import _REPORT_CSS, _device_chip, _status_pill
from utils.senao_branding import senao_logo_src

NOLINK_SSID = "UBR_CPU_NOLINK"
IPERF_PORT = 5210
_UCI_SSID_RE = re.compile(r"^(wireless\.\S+?)\.ssid='([^']*)'")
_UCI_KEY_RE = re.compile(r"^wireless\.[A-Za-z0-9_@\[\].-]+$")


@dataclass
class CpuMonitorConfig:
    lab_ssh: str = "qa-lab-01@10.0.150.116"
    lab_password: str = "senao1234#"
    device_password: str = "Sen@0ubRNwk$"
    gui_user: str = "root"
    bts_ip: str = "192.168.3.20"
    cpe_ip: str = "192.168.3.21"
    manage_link: bool = True
    snmp_community: str = "ubr@rw123"
    duration_s: int = 60
    poll_s: int = 5
    settle_s: int = 30
    wlan_iface: str = "ath1"
    restore_ssid: str = ""
    ssid_key: str = ""
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)


@dataclass(frozen=True)
class CpuCase:
    """One condition inside REG_04. The case id stays REG_04 for every row."""

    case_id: str
    scenario: str
    title: str
    link_up: bool
    webpage_open: bool
    throughput: bool


CASES: tuple[CpuCase, ...] = (
    CpuCase("REG_04", "1", "No link, webpage closed", False, False, False),
    CpuCase("REG_04", "2", "Link up, webpage closed", True, False, False),
    CpuCase("REG_04", "3", "No link, webpage open", False, True, False),
    CpuCase("REG_04", "4", "Link up, webpage open", True, True, False),
    CpuCase("REG_04", "5", "Link up, webpage open, throughput", True, True, True),
)


@dataclass
class CpuSample:
    t_s: int
    cpu_pct: float
    mem_used_pct: float
    mem_used_mb: float
    mem_total_mb: float
    stations: int


@dataclass
class CpuCaseResult:
    case: CpuCase
    samples: list[CpuSample]
    started_at: str
    iperf_note: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.samples)


def run_local(cmd: list[str], *, timeout_s: int = 60) -> str:
    """Run a command on this host. Jenkins uses the TARGET_STAND node."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    out = proc.stdout or ""
    if proc.returncode != 0 and not out.strip():
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"command failed (rc={proc.returncode}): {err[:240]}")
    return out


def require_bts_reachable(cfg: CpuMonitorConfig) -> None:
    """Fail when this machine cannot reach the BTS. There is no lab-PC hop."""
    host = socket.gethostname()
    if shutil.which("snmpget") is None:
        raise RuntimeError(f"snmpget is not installed on {host}")
    proc = subprocess.run(
        ["ping", "-c", "2", "-W", "2", cfg.bts_ip],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"BTS {cfg.bts_ip} is not reachable from {host}. "
            "CPU sampling runs on this node only."
        )
    print(f"[cpu] BTS {cfg.bts_ip} is reachable from {host}", flush=True)


def ssh_device(
    cfg: CpuMonitorConfig,
    device_ip: str,
    device_cmd: str,
    *,
    timeout_s: int = 45,
) -> str:
    cmd = [
        "sshpass",
        "-p",
        cfg.device_password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=10",
        f"root@{device_ip}",
        device_cmd,
    ]
    return run_local(cmd, timeout_s=timeout_s)


# sysInfoCPUUsage. The agent returns the first field of /tmp/cpu_usage.
OID_CPU_USAGE = "1.3.6.1.4.1.14125.123.1.2.2.13.0"
# UCD-SNMP-MIB. Memory is real RAM, kB.
OID_MEM_TOTAL = "1.3.6.1.4.1.2021.4.5.0"
OID_MEM_AVAIL = "1.3.6.1.4.1.2021.4.6.0"


def parse_cpu_usage(raw: str) -> float:
    """Busy percent from sysInfoCPUUsage. The value is a DisplayString."""
    text = raw.strip().strip('"').strip("'")
    if "No Such" in text:
        raise ValueError(text)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        raise ValueError(f"no numeric CPU value in SNMP response: {text!r}")
    value = float(match.group(0))
    if value < 0 or value > 100:
        raise ValueError(f"CPU out of range 0-100: {value}")
    return round(value, 2)


def mem_used_from_kb(total_kb: int, avail_kb: int) -> tuple[float, float, float]:
    """Used percent and megabytes from SNMP total and available RAM."""
    if total_kb <= 0:
        raise ValueError(f"SNMP memory total is {total_kb}")
    used_kb = max(0, total_kb - max(0, avail_kb))
    return (
        round(100.0 * used_kb / total_kb, 1),
        round(used_kb / 1024.0, 1),
        round(total_kb / 1024.0, 1),
    )


def snmp_get(cfg: CpuMonitorConfig, oids: list[str]) -> list[str]:
    """One snmpget from this host. Values only, in request order."""
    cmd = [
        "snmpget",
        "-v2c",
        "-c",
        cfg.snmp_community,
        "-Ovq",
        "-t",
        "2",
        "-r",
        "1",
        cfg.bts_ip,
        *oids,
    ]
    raw = run_local(cmd, timeout_s=20)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) != len(oids) or any("No Such" in line or line.startswith("Timeout") for line in lines):
        raise RuntimeError(f"SNMP sample failed: {raw.strip()[:240]}")
    return lines


def read_cpu_mem(cfg: CpuMonitorConfig) -> tuple[float, float, float, float]:
    """CPU busy percent from sysInfoCPUUsage, and memory used from UCD-SNMP."""
    cpu_s, total_s, avail_s = snmp_get(cfg, [OID_CPU_USAGE, OID_MEM_TOTAL, OID_MEM_AVAIL])
    try:
        cpu = parse_cpu_usage(cpu_s)
        total_kb = int(float(total_s))
        avail_kb = int(float(avail_s))
    except ValueError as exc:
        raise RuntimeError(f"SNMP CPU/memory was not numeric: {cpu_s!r} {total_s!r} {avail_s!r}") from exc
    used_pct, used_mb, total_mb = mem_used_from_kb(total_kb, avail_kb)
    return cpu, used_pct, used_mb, total_mb


def read_station_count(cfg: CpuMonitorConfig) -> int:
    """Associated stations. This firmware does not expose that count over SNMP."""
    iface = cfg.wlan_iface
    raw = ssh_device(
        cfg,
        cfg.bts_ip,
        f"wlanconfig {iface} list sta 2>/dev/null | grep -c -E '^[0-9a-fA-F]{{2}}:' || true",
        timeout_s=20,
    )
    numbers = re.findall(r"\d+", raw)
    if not numbers:
        raise RuntimeError(f"station count was unreadable: {raw.strip()[:180]}")
    return int(numbers[-1])


def poll_cpu_mem(
    cfg: CpuMonitorConfig,
    web: "WebPage | None" = None,
    *,
    settle_s: int | None = None,
) -> list[CpuSample]:
    """One SNMP sample every poll_s across duration_s, after settle_s is left unrecorded."""
    poll = max(1, int(cfg.poll_s))
    duration = max(poll, int(cfg.duration_s))
    settle = max(0, int(cfg.settle_s if settle_s is None else settle_s))
    if settle:
        print(f"  settle {settle}s before capture", flush=True)
        waited = 0
        while waited < settle:
            step = min(poll, settle - waited)
            time.sleep(step)
            waited += step
            if web is not None:
                web.ensure_logged_in()
    points = max(1, duration // poll)
    samples: list[CpuSample] = []
    t0 = time.monotonic()
    for index in range(points):
        elapsed = time.monotonic() - t0
        if web is not None:
            web.ensure_logged_in()
        cpu, mem_pct, mem_mb, mem_total = read_cpu_mem(cfg)
        stations = read_station_count(cfg)
        samples.append(
            CpuSample(
                t_s=max(0, int(round(elapsed))),
                cpu_pct=cpu,
                mem_used_pct=mem_pct,
                mem_used_mb=mem_mb,
                mem_total_mb=mem_total,
                stations=stations,
            )
        )
        print(
            f"  t={samples[-1].t_s}s cpu={samples[-1].cpu_pct}% "
            f"mem={samples[-1].mem_used_pct}% sta={samples[-1].stations}",
            flush=True,
        )
        if index + 1 < points:
            sleep_for = poll - (time.monotonic() - t0 - elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)
    return samples


def _essid(cfg: CpuMonitorConfig) -> str:
    raw = ssh_device(cfg, cfg.bts_ip, f"iwconfig {cfg.wlan_iface} 2>/dev/null", timeout_s=25)
    match = re.search(r'ESSID:"([^"]*)"', raw)
    return match.group(1).strip() if match else ""


def _ssid_key_for(cfg: CpuMonitorConfig, essid: str) -> str:
    raw = ssh_device(cfg, cfg.bts_ip, "uci show wireless", timeout_s=25)
    for line in raw.splitlines():
        match = _UCI_SSID_RE.match(line.strip())
        if match and match.group(2) == essid:
            key = match.group(1) + ".ssid"
            if _UCI_KEY_RE.match(key):
                return key
    raise RuntimeError(f"UCI SSID key for {essid!r} was not found")


def _apply_ssid(cfg: CpuMonitorConfig, uci_key: str, ssid: str) -> None:
    if not _UCI_KEY_RE.match(uci_key):
        raise RuntimeError(f"refusing UCI key {uci_key!r}")
    # wifi reload does not move the live ESSID on this image. ucidyn apply does,
    # and it can drop the SSH session while the radio restarts.
    cmd = f"ucidyn set {uci_key} {shlex.quote(ssid)}; ucidyn apply; echo APPLY_DONE"
    try:
        ssh_device(cfg, cfg.bts_ip, cmd, timeout_s=90)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"[cpu] ssid apply connection closed ({exc})", flush=True)


def _wait_link(cfg: CpuMonitorConfig, *, essid: str, up: bool, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            seen = _essid(cfg)
            stations = read_station_count(cfg)
            last = f"essid={seen!r} sta={stations}"
            if seen == essid and stations is not None and (stations >= 1 if up else stations == 0):
                return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last = str(exc)[:180]
        time.sleep(5)
    state = "associated" if up else "clear of stations"
    raise RuntimeError(f"link did not become {state} on {essid!r} within {timeout_s}s ({last})")


def drop_link(cfg: CpuMonitorConfig) -> bool:
    """Move the BTS SSID so the CPE cannot associate. ath1 stays up.

    Returns True when the SSID was changed. A later no-link case leaves it.
    """
    current = _essid(cfg)
    if not current:
        raise RuntimeError(f"{cfg.wlan_iface} has no ESSID")
    if current != NOLINK_SSID:
        cfg.restore_ssid = current
        cfg.ssid_key = _ssid_key_for(cfg, current)
    elif cfg.ssid_key:
        stations = read_station_count(cfg)
        if stations == 0:
            print("[cpu] link already down, leaving the SSID unchanged", flush=True)
            return False
    if not cfg.ssid_key:
        raise RuntimeError("SSID UCI key is unknown")
    print(f"[cpu] dropping link: {cfg.restore_ssid or current} -> {NOLINK_SSID}", flush=True)
    _apply_ssid(cfg, cfg.ssid_key, NOLINK_SSID)
    _wait_link(cfg, essid=NOLINK_SSID, up=False, timeout_s=120)
    return True


def ensure_link(cfg: CpuMonitorConfig) -> bool:
    """Put the saved SSID back and wait for the CPE station.

    Returns True when the SSID was changed.
    """
    target = cfg.restore_ssid
    if not target:
        current = _essid(cfg)
        if current and current != NOLINK_SSID:
            cfg.restore_ssid = current
            target = current
        else:
            raise RuntimeError("original SSID was not recorded; refusing to guess")
    current = ""
    try:
        current = _essid(cfg)
    except (RuntimeError, subprocess.TimeoutExpired):
        current = ""
    snap_sta = None
    try:
        snap_sta = read_station_count(cfg)
    except (RuntimeError, subprocess.TimeoutExpired):
        snap_sta = None
    if current == target and snap_sta is not None and snap_sta >= 1:
        print(f"[cpu] link already up on {target}, leaving the SSID unchanged", flush=True)
        return False
    if not cfg.ssid_key:
        cfg.ssid_key = _ssid_key_for(cfg, current or target)
    print(f"[cpu] restoring link SSID {target}", flush=True)
    _apply_ssid(cfg, cfg.ssid_key, target)
    _wait_link(cfg, essid=target, up=True, timeout_s=150)
    return True


class WebPage:
    """LuCI left open through an SSH tunnel so the device keeps serving the page."""

    def __init__(self, cfg: CpuMonitorConfig) -> None:
        self.cfg = cfg
        self._tunnel: subprocess.Popen[str] | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self.local_port = 18443

    def open(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.local_port = int(sock.getsockname()[1])
        self._start_tunnel()
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(ignore_https_errors=True)
        self._page = self._context.new_page()
        self._submit_login()

    def ensure_logged_in(self) -> None:
        """Sign in again when LuCI has returned to the login form."""
        if self._page is None or not self._session_expired():
            return
        print("[cpu] LuCI session ended — signing in again", flush=True)
        self._submit_login()

    def _session_expired(self) -> bool:
        page = self._page
        if page is None:
            return False
        if self._login_form_visible():
            return True
        try:
            probe = page.evaluate(
                """async () => {
                    const res = await fetch(window.location.href, {
                        credentials: 'same-origin',
                        cache: 'no-store',
                    });
                    const text = await res.text();
                    const login = text.includes('name="luci_username"')
                        || text.includes("name='luci_username'");
                    return { status: res.status, login: login };
                }"""
            )
        except Exception:
            return True
        if not isinstance(probe, dict):
            return True
        return bool(probe.get("login")) or int(probe.get("status") or 0) in {401, 403}

    def _login_form_visible(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            user = page.locator("input[name='luci_username']")
            return user.count() > 0 and user.first.is_visible()
        except Exception:
            return False

    def _submit_login(self) -> None:
        page = self._page
        if page is None:
            raise RuntimeError("LuCI page is not open")
        url = f"https://127.0.0.1:{self.local_port}/cgi-bin/luci/"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        user = page.locator("input[name='luci_username']")
        if user.count() and user.first.is_visible():
            user.first.fill(self.cfg.gui_user)
            page.fill("input[name='luci_password']", self.cfg.device_password)
            page.press("input[name='luci_password']", "Enter")
            page.wait_for_timeout(4000)
        if "luci_username" in (page.content() or ""):
            shot = self.cfg.repo_root / "reports" / "artifacts" / "regression_cpu_login.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot))
            raise RuntimeError(f"LuCI login did not complete; screenshot {shot}")
        print(f"[cpu] webpage open at {page.url}", flush=True)

    def close(self) -> None:
        self._page = None
        self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        if self._tunnel is not None:
            self._tunnel.terminate()
            try:
                self._tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._tunnel.kill()
            self._tunnel = None

    def _start_tunnel(self) -> None:
        cmd = [
            "sshpass",
            "-p",
            self.cfg.device_password,
            "ssh",
            "-N",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectTimeout=12",
            "-L",
            f"127.0.0.1:{self.local_port}:127.0.0.1:443",
            f"root@{self.cfg.bts_ip}",
        ]
        self._tunnel = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)
        if self._tunnel.poll() is not None:
            err = ""
            if self._tunnel.stderr is not None:
                err = self._tunnel.stderr.read()[:300]
            raise RuntimeError(f"SSH tunnel to LuCI failed: {err}")


def start_throughput(cfg: CpuMonitorConfig) -> None:
    seconds = int(cfg.duration_s) + int(cfg.settle_s) + 30
    cpe_has = ssh_device(cfg, cfg.cpe_ip, "command -v iperf3 || true", timeout_s=20)
    bts_has = ssh_device(cfg, cfg.bts_ip, "command -v iperf3 || true", timeout_s=20)
    if "iperf3" not in cpe_has or "iperf3" not in bts_has:
        raise RuntimeError("iperf3 is not installed on both the BTS and the CPE")
    ssh_device(
        cfg,
        cfg.cpe_ip,
        f"(trap '' HUP; iperf3 -s -p {IPERF_PORT}) >/tmp/reg_cpu_iperf_s.log 2>&1 & echo STARTED",
        timeout_s=20,
    )
    ssh_device(
        cfg,
        cfg.bts_ip,
        (
            f"(trap '' HUP; iperf3 -c {cfg.cpe_ip} -p {IPERF_PORT} -t {seconds}) "
            f">/tmp/reg_cpu_iperf.log 2>&1 & echo STARTED"
        ),
        timeout_s=20,
    )
    log = ""
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        time.sleep(3)
        log = ssh_device(
            cfg, cfg.bts_ip, "tail -n 30 /tmp/reg_cpu_iperf.log 2>/dev/null || true", timeout_s=20
        )
        if "Mbits" in log or "Gbits" in log:
            print("[cpu] throughput is passing", flush=True)
            return
    raise RuntimeError(f"throughput did not start: {log.strip()[:300]}")


def stop_throughput(cfg: CpuMonitorConfig) -> None:
    ssh_device(
        cfg,
        cfg.bts_ip,
        f"pkill -f '[i]perf3 -c {cfg.cpe_ip}' >/dev/null 2>&1 || true; echo STOPPED",
        timeout_s=20,
    )
    ssh_device(
        cfg,
        cfg.cpe_ip,
        f"pkill -f '[i]perf3 -s -p {IPERF_PORT}' >/dev/null 2>&1 || true; echo STOPPED",
        timeout_s=20,
    )


def iperf_summary(cfg: CpuMonitorConfig) -> str:
    raw = ssh_device(
        cfg,
        cfg.bts_ip,
        "tail -n 40 /tmp/reg_cpu_iperf.log 2>/dev/null || true",
        timeout_s=20,
    )
    for line in reversed(raw.splitlines()):
        if "sender" in line or "receiver" in line or "Mbits" in line:
            return line.strip()
    return raw.strip().splitlines()[-1] if raw.strip() else ""


def _check_link_state(case: CpuCase, samples: list[CpuSample]) -> None:
    if case.link_up:
        bad = [row for row in samples if row.stations < 1]
        if bad:
            raise RuntimeError(
                f"{case.case_id} scenario {case.scenario}: link dropped during sampling ({len(bad)} polls)"
            )
    else:
        bad = [row for row in samples if row.stations != 0]
        if bad:
            raise RuntimeError(
                f"{case.case_id} scenario {case.scenario}: a station was associated during the no-link poll"
            )


def order_cases(cases: list[CpuCase]) -> list[CpuCase]:
    """No-link cases first, then link cases. Order inside each group is kept."""
    return [case for case in cases if not case.link_up] + [case for case in cases if case.link_up]


def run_cpu_case(
    cfg: CpuMonitorConfig,
    case: CpuCase,
    *,
    restore_after: bool = True,
) -> CpuCaseResult:
    print(
        f"[cpu] {case.case_id} scenario {case.scenario} {case.title} "
        f"for {cfg.duration_s}s every {cfg.poll_s}s",
        flush=True,
    )
    started = datetime.now().isoformat(timespec="seconds")
    web: WebPage | None = None
    traffic = False
    samples: list[CpuSample] = []
    note = ""
    try:
        changed = False
        if cfg.manage_link:
            if case.link_up:
                changed = ensure_link(cfg)
            else:
                changed = drop_link(cfg)
        else:
            print("[cpu] CPE IP is empty — leaving the SSID unchanged", flush=True)
        if case.webpage_open:
            web = WebPage(cfg)
            web.open()
        if case.throughput:
            traffic = True
            start_throughput(cfg)
        # The long wait follows an SSID change. The next case in the same
        # group keeps that SSID and starts recording.
        settle_s = cfg.settle_s if changed or not cfg.manage_link else 0
        if cfg.manage_link and not changed:
            print("[cpu] SSID unchanged, capture starts without another wait", flush=True)
        samples = poll_cpu_mem(
            cfg,
            web if case.webpage_open else None,
            settle_s=settle_s,
        )
        if cfg.manage_link:
            _check_link_state(case, samples)
        if traffic:
            note = iperf_summary(cfg)
            if "Mbits" not in note and "Gbits" not in note:
                raise RuntimeError(
                    f"{case.case_id} scenario {case.scenario}: throughput log has no bitrate"
                )
        return CpuCaseResult(case=case, samples=samples, started_at=started, iperf_note=note)
    except Exception as exc:
        return CpuCaseResult(
            case=case,
            samples=samples,
            started_at=started,
            iperf_note=note,
            error=str(exc),
        )
    finally:
        if traffic:
            try:
                stop_throughput(cfg)
            except Exception as exc:
                print(f"[cpu] stop throughput: {exc}", flush=True)
        if web is not None:
            web.close()
        if restore_after and cfg.manage_link:
            try:
                ensure_link(cfg)
            except Exception as exc:
                print(f"[cpu] restore link failed: {exc}", flush=True)


def _avg(values: list[float], ndigits: int = 1) -> float:
    return round(sum(values) / len(values), ndigits) if values else 0.0


_CASE_COLORS = ("#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed")


def _version_text(raw: str) -> str:
    for line in raw.replace("\r", "").splitlines():
        line = line.strip().split("---", 1)[0].strip()
        if line and not line.startswith("cat "):
            return line
    return ""


def _read_fw(cfg: CpuMonitorConfig, ip: str) -> str:
    try:
        raw = ssh_device(cfg, ip, "cat /etc/version 2>/dev/null", timeout_s=20)
    except (RuntimeError, subprocess.TimeoutExpired):
        raw = ""
    found = _version_text(raw)
    if found:
        return found
    if ip == cfg.cpe_ip:
        found = _read_cpe_fw_link_local(cfg)
        if found:
            return found
    return "—"


def _associated_sta_prefixes(cfg: CpuMonitorConfig) -> set[str]:
    """First five MAC octets of stations on the BTS radio."""
    try:
        raw = ssh_device(
            cfg,
            cfg.bts_ip,
            f"wlanconfig {cfg.wlan_iface} list sta 2>/dev/null",
            timeout_s=20,
        )
    except (RuntimeError, subprocess.TimeoutExpired):
        return set()
    prefixes: set[str] = set()
    for line in raw.splitlines():
        mac = line.split()[0].lower() if line.split() else ""
        if re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
            prefixes.add(":".join(mac.split(":")[:5]))
    return prefixes


def _read_cpe_fw_link_local(cfg: CpuMonitorConfig) -> str:
    """CPE /etc/version when its management IPv4 does not answer.

    The station can stay associated while br-lan is on another address.
    This host may still have the CPE ethernet as an IPv6 link-local neighbor.
    Only a neighbor that shares the associated station's MAC prefix is used,
    so another radio on the same switch is not reported as this CPE.
    """
    prefixes = _associated_sta_prefixes(cfg)
    if not prefixes:
        return ""
    try:
        v6 = run_local(["ip", "-6", "neigh", "show"], timeout_s=15)
    except (RuntimeError, subprocess.TimeoutExpired):
        return ""
    for line in v6.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("fe80:") or "lladdr" not in parts or "dev" not in parts:
            continue
        mac = parts[parts.index("lladdr") + 1].lower()
        if ":".join(mac.split(":")[:5]) not in prefixes:
            continue
        dev = parts[parts.index("dev") + 1]
        target = f"{parts[0].split('%', 1)[0]}%{dev}"
        try:
            raw = ssh_device(cfg, target, "cat /etc/version 2>/dev/null", timeout_s=20)
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        found = _version_text(raw)
        if found:
            return found
    return ""


def _cpu_svg(
    series: list[tuple[str, list[CpuSample], str]],
    *,
    title: str,
    scenario_ids: list[str] | None = None,
) -> str:
    """Inline CPU % line chart. series items are (label, samples, color)."""
    drawn = [(label, rows, color) for label, rows, color in series if rows]
    if not drawn:
        return ""
    width, height = 760, 280
    left, right, top, bottom = 58, 18, 36, 48
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_t = max(row.t_s for _, rows, _ in drawn for row in rows) or 1

    def x_of(t_s: int) -> float:
        return left + (t_s / max_t) * plot_w

    def y_of(cpu: float) -> float:
        return top + (1.0 - (max(0.0, min(100.0, cpu)) / 100.0)) * plot_h

    grid: list[str] = []
    for pct in (0, 25, 50, 75, 100):
        y = y_of(pct)
        grid.append(
            f"<line x1='{left}' y1='{y:.1f}' x2='{width - right}' y2='{y:.1f}' stroke='#e2e8f0'/>"
            f"<text x='{left - 8}' y='{y + 4:.1f}' text-anchor='end' font-size='16' fill='#64748b'>{pct}</text>"
        )
    lines: list[str] = []
    legend: list[str] = []
    for index, (label, rows, color) in enumerate(drawn):
        points = " ".join(f"{x_of(row.t_s):.1f},{y_of(row.cpu_pct):.1f}" for row in rows)
        lines.append(
            f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{points}'/>"
        )
        last = rows[-1]
        lines.append(
            f"<circle cx='{x_of(last.t_s):.1f}' cy='{y_of(last.cpu_pct):.1f}' r='3' fill='{color}'/>"
        )
        scenario = ""
        if scenario_ids and index < len(scenario_ids):
            scenario = scenario_ids[index]
        click = (
            f" type='button' data-scenario='{escape(scenario)}'"
            if scenario
            else " type='button'"
        )
        legend.append(
            f"<button class='legend-item'{click}>"
            f"<span class='swatch' style='background:{color}'></span>"
            f"<span>{escape(label)}</span></button>"
        )
        if index == 0:
            for row in rows:
                if row.t_s in {rows[0].t_s, rows[-1].t_s} or row.t_s % 30 == 0:
                    grid.append(
                        f"<text x='{x_of(row.t_s):.1f}' y='{height - 10}' text-anchor='middle' "
                        f"font-size='16' fill='#64748b'>{row.t_s}s</text>"
                    )
    title_html = f"<div class='chart-title'>{escape(title)}</div>" if title else ""
    label = escape(title or "CPU %")
    return (
        f"<div class='chart'>{title_html}"
        f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' aria-label='{label}'>"
        + "".join(grid)
        + f"<text x='{left}' y='{top - 8}' font-size='16' fill='#64748b'>CPU %</text>"
        + "".join(lines)
        + "</svg><div class='legend'>"
        + "".join(legend)
        + "</div></div>"
    )


_CPU_EXTRA_CSS = """
    .hero-shell {
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #2563eb 100%);
      border: none;
      color: #fff;
    }
    .hero-top {
      background: transparent;
      border-bottom: 1px solid rgba(255,255,255,.18);
    }
    .hero-eyebrow { color: rgba(255,255,255,.78); }
    .hero h1 { color: #fff; }
    .hero-time { color: rgba(255,255,255,.82); }
    .meta-chip {
      background: rgba(255,255,255,.12);
      color: #fff;
      border-color: rgba(255,255,255,.28);
    }
    .overview-strip { background: rgba(255,255,255,.08); }
    .fw-pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: #ecfdf5;
      color: #047857;
      border: 1px solid #a7f3d0;
      font-family: ui-monospace, Consolas, monospace;
      font-size: 12px;
      font-weight: 700;
    }
    .panel h2 {
      font-size: 18px;
      letter-spacing: 0;
      text-transform: none;
      color: var(--ink);
    }
    .panel-desc { font-size: 15px; color: var(--muted); }
    table.data-table { font-size: 15px; }
    table.data-table thead th {
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: none;
    }
    table.data-table td.mono { font-size: 14px; }
    .pill { font-size: 12px; padding: 5px 11px; }
    details.scenario {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
      overflow: hidden;
    }
    details.scenario > summary {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 16px 20px;
      cursor: pointer;
      list-style: none;
      font-size: 16px;
      font-weight: 600;
      color: var(--ink);
    }
    details.scenario > summary::-webkit-details-marker { display: none; }
    details.scenario > summary::before {
      content: "▸";
      color: var(--muted);
      margin-right: 10px;
      font-size: 14px;
    }
    details.scenario[open] > summary::before { content: "▾"; }
    details.scenario[open] > summary { border-bottom: 1px solid var(--line); }
    .scenario-title { display: flex; align-items: center; min-width: 0; flex: 1; }
    .scenario-meta {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-left: auto;
      font-size: 14px;
      font-weight: 500;
      color: var(--muted);
      white-space: nowrap;
    }
    .scenario-body { padding: 8px 20px 18px; }
    details.samples { margin-top: 12px; }
    details.samples > summary {
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      color: #1e3a8a;
      padding: 8px 0;
    }
    details.samples .table-scroll { max-height: 320px; }
    .chart {
      margin: 8px auto 0;
    }
    .chart svg {
      display: block;
      width: 100%;
      max-width: 860px;
      height: auto;
      margin: 0 auto;
    }
    .chart svg text {
      font-size: 16px;
      font-family: "Figtree", system-ui, sans-serif;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 22px;
      margin-top: 14px;
      max-width: none;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin: 0;
      padding: 4px 2px;
      border: 0;
      background: transparent;
      color: var(--ink);
      font: inherit;
      font-size: 15px;
      font-weight: 600;
      line-height: 1.3;
      white-space: nowrap;
      cursor: pointer;
    }
    .legend-item:hover { color: #1e3a8a; }
    tr.scenario-jump { cursor: pointer; }
    tr.scenario-jump:hover td { background: #eff6ff; }
    .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 3px; flex: 0 0 auto; }
    .chart-title { font-size: 16px; font-weight: 600; margin-bottom: 8px; color: var(--ink); }
    .note-fail { color: var(--bad); font-weight: 600; }
"""


def write_cpu_report(cfg: CpuMonitorConfig, results: list[CpuCaseResult]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    out_dir = cfg.repo_root / "reports" / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"CPU_Report_{stamp}_Grafana.html"
    csv_path = out_dir / f"CPU_Report_{stamp}.csv"
    bts_fw = _read_fw(cfg, cfg.bts_ip)
    cpe_fw = _read_fw(cfg, cfg.cpe_ip)
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed
    pass_cls = "ok" if failed == 0 and results else "bad" if passed == 0 else "warn"
    csv_rows = [["case", "scenario", "title", "t_s", "cpu_pct", "mem_used_pct", "mem_used_mb", "mem_total_mb", "stations"]]
    summary_rows: list[str] = []
    detail_blocks: list[str] = []
    for index, result in enumerate(results):
        cpu_vals = [row.cpu_pct for row in result.samples]
        mem_vals = [row.mem_used_pct for row in result.samples]
        status = "PASS" if result.ok else "FAIL"
        row_cls = "row-pass" if result.ok else "row-fail"
        cpu_txt = (
            f"{min(cpu_vals):.2f} / {_avg(cpu_vals, 2):.2f} / {max(cpu_vals):.2f}"
            if cpu_vals
            else "—"
        )
        mem_txt = f"{_avg(mem_vals):.1f}" if mem_vals else "—"
        link = "Up" if result.case.link_up else "Down"
        page = "Open" if result.case.webpage_open else "Closed"
        traffic = "Yes" if result.case.throughput else "No"
        summary_rows.append(
            f"<tr class='{row_cls} scenario-jump' data-scenario='{escape(result.case.scenario)}'>"
            f"<td class='mono'>{escape(result.case.scenario)}</td>"
            f"<td>{escape(result.case.title)}</td>"
            f"<td>{link}</td><td>{page}</td><td>{traffic}</td>"
            f"<td class='mono'>{cpu_txt}</td>"
            f"<td class='mono'>{mem_txt}</td>"
            f"<td>{_status_pill(status)}</td>"
            "</tr>"
        )
        show_mem_mb = any(row.mem_total_mb for row in result.samples)
        sample_rows = []
        for row in result.samples:
            csv_rows.append([
                result.case.case_id,
                result.case.scenario,
                result.case.title,
                row.t_s,
                row.cpu_pct,
                row.mem_used_pct,
                row.mem_used_mb,
                row.mem_total_mb,
                row.stations,
            ])
            mem_cell = f"<td class='mono'>{row.mem_used_mb}</td>" if show_mem_mb else ""
            sample_rows.append(
                "<tr>"
                f"<td class='mono'>{row.t_s}</td><td class='mono'>{row.cpu_pct}</td>"
                f"<td class='mono'>{row.mem_used_pct}</td>{mem_cell}"
                f"<td class='mono'>{row.stations}</td>"
                "</tr>"
            )
        color = _CASE_COLORS[index % len(_CASE_COLORS)]
        chart = _cpu_svg(
            [(f"{result.case.scenario}. {result.case.title}", result.samples, color)],
            title="",
            scenario_ids=[result.case.scenario],
        )
        note = f"<p>{escape(result.iperf_note)}</p>" if result.iperf_note else ""
        err = f"<p class='note-fail'>{escape(result.error)}</p>" if result.error else ""
        mem_head = "<th>Mem MB</th>" if show_mem_mb else ""
        detail_blocks.append(
            f"<details class='scenario span-12' id='scenario-{escape(result.case.scenario)}'>"
            f"<summary>"
            f"<span class='scenario-title'><span class='swatch' style='background:{color}'></span>"
            f"Scenario {escape(result.case.scenario)} — {escape(result.case.title)}</span>"
            f"<span class='scenario-meta'>CPU {escape(cpu_txt)}% · mem {escape(mem_txt)}% "
            f"{_status_pill(status)}</span>"
            f"</summary>"
            f"<div class='scenario-body'>{note}{err}{chart}"
            f"<details class='samples'><summary>Samples ({len(result.samples)})</summary>"
            f"<div class='table-scroll'><table class='data-table'>"
            f"<thead><tr><th>t (s)</th><th>CPU %</th><th>Mem %</th>{mem_head}<th>Stations</th></tr></thead>"
            f"<tbody>{''.join(sample_rows)}</tbody></table></div></details></div></details>"
        )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(csv_rows)
    overview = _cpu_svg(
        [
            (f"{result.case.scenario}. {result.case.title}", result.samples, _CASE_COLORS[index % len(_CASE_COLORS)])
            for index, result in enumerate(results)
        ],
        title="",
        scenario_ids=[result.case.scenario for result in results],
    )
    sampled_for = max(
        (row.t_s for result in results for row in result.samples),
        default=cfg.duration_s,
    )
    cpu_avgs = [
        _avg([row.cpu_pct for row in result.samples], 2)
        for result in results
        if result.samples
    ]
    avg_cpu = _avg(cpu_avgs) if cpu_avgs else 0.0
    logo = senao_logo_src()
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR REG_04 CPU utilization</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap" rel="stylesheet"/>
  <style>
{_REPORT_CSS}
{_CPU_EXTRA_CSS}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-shell">
      <div class="hero-top">
        <div>
          <div class="hero-brand">
            <div class="hero-logo">
              <img src="{logo}" alt="Senao Networks"/>
            </div>
            <div>
              <div class="hero-eyebrow">Senao CPU utilization report</div>
              <h1>REG_04 CPU Utilization</h1>
            </div>
          </div>
          <div class="hero-meta">
            <span class="meta-chip">BTS {escape(cfg.bts_ip)}</span>
            <span class="meta-chip">BTS FW {escape(bts_fw)}</span>
            <span class="meta-chip">CPE {escape(cfg.cpe_ip)}</span>
            <span class="meta-chip">CPE FW {escape(cpe_fw)}</span>
            <span class="meta-chip">{cfg.poll_s}s poll · {sampled_for}s each{f" · {cfg.settle_s}s settle" if cfg.settle_s else ""}</span>
          </div>
        </div>
        <div class="hero-status">
          <div class="hero-badge {pass_cls}">{passed}/{len(results)} passed</div>
          <div class="hero-time">{escape(when)}</div>
        </div>
      </div>
      <div class="overview-strip">
        <article class="ov-card tone-{pass_cls}">
          <div class="ov-label">REG_04</div>
          <div class="ov-value">{passed}<span>/{len(results)}</span></div>
          <div class="ov-sub">{failed} scenario(s) failed</div>
        </article>
        <article class="ov-card">
          <div class="ov-label">CPU average</div>
          <div class="ov-value">{avg_cpu:.2f}<span>%</span></div>
          <div class="ov-sub">mean of scenario averages</div>
        </article>
        <article class="ov-card">
          <div class="ov-label">Firmware</div>
          <div class="ov-value" style="font-size:22px">{escape(bts_fw)}</div>
          <div class="ov-sub">BTS /etc/version · CPE {escape(cpe_fw)}</div>
        </article>
      </div>
    </div>
  </header>
  <main class="grid">
    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Testbed summary</h2>
          <p class="panel-desc">CPU is sysInfoCPUUsage, the same value as /tmp/cpu_usage. Memory is UCD-SNMP real RAM. Firmware is the live /etc/version on each radio.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table testbed-table">
          <thead>
            <tr><th>Unit</th><th>Role</th><th>Mgmt IP</th><th>FW version</th></tr>
          </thead>
          <tbody>
            <tr class="row-bts">
              <td><strong>{_device_chip("BTS", is_bts=True)}</strong></td>
              <td>Access point</td>
              <td class="mono">{escape(cfg.bts_ip)}</td>
              <td><span class="fw-pill">{escape(bts_fw)}</span></td>
            </tr>
            <tr>
              <td><strong>{_device_chip("SU1")}</strong></td>
              <td>Station</td>
              <td class="mono">{escape(cfg.cpe_ip)}</td>
              <td><span class="fw-pill">{escape(cpe_fw)}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>CPU % across scenarios</h2>
          <p class="panel-desc">Click a condition to open its samples. Recording starts after a {cfg.settle_s}s settle, so the link change is not in these values.</p>
        </div>
      </div>
      {overview}
    </section>
    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>REG_04 scenarios</h2>
          <p class="panel-desc">CPU min / avg / max, and average memory used. Click a row to open that scenario.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>#</th><th>Condition</th><th>Link</th><th>Page</th><th>Throughput</th>
              <th>CPU min / avg / max %</th><th>Mem avg %</th><th>Result</th>
            </tr>
          </thead>
          <tbody>
            {''.join(summary_rows)}
          </tbody>
        </table>
      </div>
    </section>
    {''.join(detail_blocks)}
  </main>
  <script>
    document.querySelectorAll("[data-scenario]").forEach(function (el) {{
      el.addEventListener("click", function () {{
        var node = document.getElementById("scenario-" + el.getAttribute("data-scenario"));
        if (!node) return;
        node.open = true;
        node.scrollIntoView({{ behavior: "smooth", block: "start" }});
      }});
    }});
  </script>
</body>
</html>
"""
    html_path.write_text(page, encoding="utf-8")
    return html_path
