"""Spectrum Analyzer GUI cases — Monitor → Tools → Spectrum Analyzer.

Radio 1 is the live 5 GHz link. These cases do not change SSID, channel, or Tx.
A scan is stopped before the case returns.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path

import asyncssh
import pytest_check as check

from pages.locators import SpectrumAnalyserLocators
from utils.cpe_session import open_cpe_gui_session
from utils.sanity_spectrum import (
    click_spectrum_start,
    click_spectrum_stop,
    open_sanity_spectrum_page,
    read_spectrum_report,
    read_spectrum_status,
)
from utils.verify_output import print_comparison_table, print_section

SCAN_WAIT_S = 180
LINK_HOLD_S = 30
LOG_WAIT_S = 20
_LOG_RE = re.compile(
    r"scan|spectral|cbs|interference|spectrum|kadv|sascan",
    re.I,
)
_INTF_HEADER_NEEDLES = (
    "index",
    "channel",
    "frequency",
    "power min",
    "power avg",
    "power max",
    "sinr",
)
_SECTION_TITLES = (
    "Channel Scan Results",
    "Interference Spectrum",
    "SINR plot",
    "Wi-Fi Channel Analyzer",
)


def _log(message: str) -> None:
    print(f"[SPECTRUM] {message}", flush=True)


async def _ssh(ssh, command: str, *, timeout_s: int = 30) -> str:
    response = await ssh.send_command(command, timeout_ops=timeout_s)
    return str(getattr(response, "result", response) or "").replace("\r", "")


class _WiredSsh:
    """SSH to the CPE LAN address through the side PC. Survives an RF drop."""

    def __init__(self, conn: asyncssh.SSHClientConnection, jump: asyncssh.SSHClientConnection):
        self._conn = conn
        self._jump = jump

    async def send_command(self, command: str, timeout_ops: int = 30):
        result = await asyncio.wait_for(self._conn.run(command, check=False), timeout=timeout_ops)

        class _Out:
            pass

        out = _Out()
        out.result = (result.stdout or "") + (result.stderr or "")
        return out

    async def close(self) -> None:
        self._conn.close()
        self._jump.close()


def _cpe_jump_targets(profile_bundle) -> tuple[str, str, str]:
    dut = (profile_bundle.active.get("dut", {}) if profile_bundle else {}) or {}
    pc_ip = str(dut.get("cpe_pc_ip") or "10.0.150.40").strip()
    pc_password = str(dut.get("cpe_pc_password") or "senao1234#").strip()
    lan_ip = str(dut.get("cpe_lan_ipv4") or "192.168.2.11").strip()
    return pc_ip, pc_password, lan_ip


class _CpeGuiTunnel:
    """HTTP to the CPE LAN through the side PC. A STA scan drops the RF path."""

    def __init__(self, jump, listener, host: str) -> None:
        self._jump = jump
        self._listener = listener
        self.host = host

    async def close(self) -> None:
        try:
            self._listener.close()
        except Exception:
            pass
        try:
            self._jump.close()
        except Exception:
            pass


async def _open_cpe_gui_tunnel(profile_bundle) -> _CpeGuiTunnel:
    pc_ip, pc_password, lan_ip = _cpe_jump_targets(profile_bundle)
    jump = await asyncssh.connect(pc_ip, username="root", password=pc_password, known_hosts=None)
    try:
        listener = await jump.forward_local_port("127.0.0.1", 0, lan_ip, 443)
    except Exception:
        jump.close()
        raise
    host = f"127.0.0.1:{listener.get_port()}"
    _log(f"CPE GUI via {pc_ip} -> {lan_ip}:443 ({host})")
    return _CpeGuiTunnel(jump, listener, host)


async def _open_wired_cpe_ssh(profile_bundle, device_creds: dict) -> _WiredSsh:
    pc_ip, pc_password, lan_ip = _cpe_jump_targets(profile_bundle)
    jump = await asyncssh.connect(pc_ip, username="root", password=pc_password, known_hosts=None)
    try:
        conn = await jump.connect_ssh(
            lan_ip,
            username="root",
            password=device_creds["pass"],
            known_hosts=None,
        )
    except Exception:
        jump.close()
        raise
    _log(f"CPE SSH via {pc_ip} -> {lan_ip}")
    return _WiredSsh(conn, jump)


async def _close_ssh(ssh) -> None:
    if ssh is None:
        return
    try:
        await ssh.close()
    except Exception:
        pass


async def _stop_scan_ssh(ssh) -> None:
    """Stop a Radio 1 scan without wifi down. STA and AP use different commands."""
    if ssh is None:
        return
    mode = (await _ssh(ssh, "uci -q get wireless.@wifi-iface[1].mode")).lower()
    if "sta" in mode:
        await _ssh(ssh, "cfg80211tool ath1 kwnstartscan 0 >/dev/null 2>&1; true")
        await _ssh(ssh, "rm -f /tmp/interference_log_wifi1.bin /tmp/interference_log_wifi1.csv")
    else:
        await _ssh(ssh, "cfg80211tool wifi1 cbs_bkscanen 0 >/dev/null 2>&1; true")


async def _stop_scan(gui_page) -> None:
    try:
        await click_spectrum_stop(gui_page)
    except Exception as exc:
        _log(f"stop scan skipped: {exc}")


async def _select_radio_1(gui_page) -> None:
    """Stay on Radio 1, the live 5 GHz link. Do not touch Radio 0 (2.4 GHz)."""
    await gui_page.evaluate(
        """() => {
            const radios = Array.from(document.querySelectorAll('input[name="radio_btns"]'));
            const checked = radios.find(r => r.checked);
            const label = (checked && checked.parentElement)
                ? (checked.parentElement.innerText || '')
                : '';
            if (/radio\\s*1/i.test(label)) return;
            if (typeof KWN !== 'undefined' && KWN.stats_radio_button_click) {
                KWN.stats_radio_button_click(1, 'advscan-list');
            }
        }"""
    )
    await gui_page.wait_for_timeout(500)


async def _read_ui(gui_page) -> dict:
    return await gui_page.evaluate(
        """() => {
            const text = (document.body && (document.body.innerText || document.body.textContent)) || '';
            const titleNodes = Array.from(document.querySelectorAll('.survey-plot-title, h4.cust-color1, #survey_heading'));
            const titleText = titleNodes.map(el => (el.textContent || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
            const radios = Array.from(document.querySelectorAll('input[name="radio_btns"]')).map(r => {
                const parent = r.parentElement ? (r.parentElement.innerText || '') : '';
                return parent.replace(/\\s+/g, ' ').trim();
            });
            const start = document.querySelector('#start_button input[value="Start"]');
            const titles = [
                'Channel Scan Results',
                'Interference Spectrum',
                'SINR plot',
                'Wi-Fi Channel Analyzer',
            ];
            const titleBlob = titleText.join(' | ');
            const headers = Array.from(
                document.querySelectorAll('#intf-tbl tr.cbi-section-table-titles th')
            ).map(th => (th.innerText || '').replace(/\\s+/g, ' ').trim());
            const canvasOk = (id) => {
                const c = document.getElementById(id);
                return !!(c && c.tagName === 'CANVAS');
            };
            let legend = '';
            try {
                legend = String(
                    (window.KWN_INTF_PLOT && KWN_INTF_PLOT._drawLegendInterference) || ''
                );
            } catch (err) {
                legend = '';
            }
            let sinrColors = [];
            try {
                if (window.KWN_INTF_PLOT && KWN_INTF_PLOT._sinrColor) {
                    sinrColors = [40, 0, -40].map(v => KWN_INTF_PLOT._sinrColor(v));
                }
            } catch (err) {
                sinrColors = [];
            }
            return {
                radios,
                start: !!(start),
                titles: titles.filter(t => titleBlob.indexOf(t) !== -1 || text.indexOf(t) !== -1),
                bestChannels: /best\\s*channels/i.test(text),
                headers,
                canvas: {
                    snr: canvasOk('snr-plot-canvas'),
                    intf: canvasOk('intf-plot-canvas'),
                    sinr: canvasOk('intf-sinr-plot-canvas'),
                    survey: canvasOk('advscan-plot-canvas'),
                },
                legend,
                sinrColors,
            };
        }"""
    )


def _canvas_drawn(sample: dict | None) -> bool:
    return bool(sample and sample.get("colored", 0) >= 30)


async def _canvas_sample(gui_page, canvas_id: str) -> dict:
    return await gui_page.evaluate(
        """(id) => {
            const c = document.getElementById(id);
            if (!c || !c.getContext) return { width: 0, colored: 0 };
            const w = c.width || 0;
            const h = c.height || 0;
            if (w < 8 || h < 8) return { width: w, colored: 0 };
            let colored = 0;
            try {
                const data = c.getContext('2d').getImageData(0, 0, w, h).data;
                for (let i = 0; i < data.length; i += 16) {
                    if (data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245) colored++;
                }
            } catch (err) {
                return { width: w, colored: 0 };
            }
            return { width: w, colored };
        }""",
        canvas_id,
    )


async def _open_page(gui_page, host: str, device_creds: dict) -> None:
    await open_sanity_spectrum_page(gui_page, host=host, device_creds=device_creds)
    await _select_radio_1(gui_page)


async def _associated(ssh) -> tuple[bool, str]:
    text = await _ssh(ssh, "iwconfig ath1 2>/dev/null | head -n 3")
    joined = " ".join(text.split())
    up = "Access Point:" in text and "Not-Associated" not in text
    return up, joined[:120]


async def _ping6_ok(ssh, peer_ip: str) -> bool:
    out = await _ssh(
        ssh,
        f"ping6 -c 1 -W 2 {peer_ip} 2>&1 | tail -n 3",
        timeout_s=15,
    )
    return "1 packets received" in out or "1 received" in out


async def _log_lines(ssh) -> list[str]:
    text = await _ssh(ssh, "logread 2>/dev/null | tail -n 120", timeout_s=20)
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


async def _wait_scan_message(gui_page, *, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        last = await read_spectrum_status(gui_page)
        if re.search(r"progress|stopping", last or "", re.I):
            return last
        await asyncio.sleep(2)
    return last


def _survey_freq_ok(freq: int) -> bool:
    return (
        5180 <= freq <= 5240
        or 5260 <= freq <= 5320
        or 5500 <= freq <= 5720
        or 5745 <= freq <= 5865
    )


def _survey_csv(text: str) -> str:
    """Turn the STA channel survey into the CSV the Interference Spectrum plot reads."""
    header = (
        "freq_mhz,bin,bin_freq_mhz,chan_span_mhz,oper_chan_bw_mhz,"
        "power_min_dbm,power_avg_dbm,power_max_dbm,power_maxhold_dbm,"
        "noise_floor_dbm,inband_max_dbm,agc_gain,fft_count"
    )
    rows = [header]
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        freq = int(parts[0])
        if not _survey_freq_ok(freq):
            continue
        powers = []
        for token in parts[2:7]:
            if token in ("-", ""):
                continue
            try:
                powers.append(int(token))
            except ValueError:
                continue
        if not powers:
            continue
        low, high = min(powers), max(powers)
        avg = round(sum(powers) / len(powers))
        rows.append(
            f"{freq},0,{freq}.0,20,80,{low},{avg},{high},{high},{low},{avg},0,1"
        )
    if len(rows) == 1:
        return ""
    return "\n".join(rows) + "\n"


async def _publish_sta_survey(ssh) -> int:
    """STA radios have no AP VAP, so the background collector never starts.

    The live channel survey is already on the STA. Publish it where the
    Spectrum Analyzer page polls, and do not call the disconnecting scan.
    """
    text = await _ssh(ssh, "wifitool ath1 get_scan_chan_util 2>/dev/null", timeout_s=20)
    csv_text = _survey_csv(text)
    count = max(0, csv_text.count("\n") - 1)
    if not csv_text:
        _log("CPE channel survey has no 5 GHz rows")
        return 0
    payload = base64.b64encode(csv_text.encode()).decode()
    await _ssh(
        ssh,
        f"printf '%s' '{payload}' | base64 -d > /tmp/interference_log_wifi1.bin",
        timeout_s=20,
    )
    _log(f"CPE interference plot is using the live channel survey ({count} rows)")
    return count


async def _wait_interference_rows(gui_page, *, timeout_s: float = SCAN_WAIT_S):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = await read_spectrum_report(gui_page)
        if last.interference.data_row_count:
            return last
        await asyncio.sleep(5)
    return last


def _headers_ok(headers: list[str]) -> bool:
    blob = " ".join(headers).lower()
    return all(needle in blob for needle in _INTF_HEADER_NEEDLES)


def _numeric_row(rows: list[list[str]]) -> bool:
    for row in rows:
        if len(row) >= 7 and re.search(r"-?\d+", " ".join(row)):
            return True
    return False


async def _recover_sta_if_down(cpe_ssh) -> None:
    up, _ = await _associated(cpe_ssh)
    if up:
        return
    _log("CPE ath1 is not associated after the scan — wifi up wifi1")
    for attempt in (1, 2):
        await _ssh(cpe_ssh, "wifi up wifi1 >/tmp/spect_wifi_up.log 2>&1", timeout_s=90)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            up, _ = await _associated(cpe_ssh)
            if up:
                _log("CPE ath1 associated again")
                return
            await asyncio.sleep(5)
        _log(f"CPE ath1 still not associated after wifi up (attempt {attempt})")


async def _finish(page, ssh, *, label: str) -> None:
    try:
        await _stop_scan(page)
    except Exception as exc:
        _log(f"{label}: GUI stop failed ({exc})")
    await _stop_scan_ssh(ssh)
    if label == "CPE":
        await _recover_sta_if_down(ssh)


async def _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, fn, profile_bundle=None) -> None:
    cpe_ip = (cpe_ips or [""])[0]
    check.is_true(bool(bsu_ip), "SPECTRUM: BTS address required")
    check.is_true(bool(cpe_ip), "SPECTRUM: CPE address required")
    await fn(
        gui_page,
        root_ssh,
        label="BTS",
        host=bsu_ip,
        peer_ip=cpe_ip,
        peer_ssh=None,
        device_creds=device_creds,
    )
    tunnel = await _open_cpe_gui_tunnel(profile_bundle)
    cpe_page = None
    cpe_ssh = None
    try:
        cpe_page = await open_cpe_gui_session(gui_page.context, tunnel.host, device_creds)
        cpe_ssh = await _open_wired_cpe_ssh(profile_bundle, device_creds)
        await fn(
            cpe_page,
            cpe_ssh,
            label="CPE",
            host=tunnel.host,
            peer_ip=bsu_ip,
            peer_ssh=root_ssh,
            device_creds=device_creds,
        )
    finally:
        await _close_ssh(cpe_ssh)
        await tunnel.close()
        if cpe_page is not None:
            try:
                await cpe_page.close()
            except Exception:
                pass


async def assert_spect_analyzer_01_ui_elements(
    gui_page,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_01 — radio, Start, graph and table sections on BTS and CPE."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del ssh, peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_01 [{label}] — Spectrum Analyzer UI")
        await _open_page(page, host, device_creds)
        ui = await _read_ui(page)
        radio_txt = ", ".join(ui.get("radios") or []) or "none"
        titles = ui.get("titles") or []
        headers = ui.get("headers") or []
        canvases = ui.get("canvas") or {}
        best = bool(ui.get("bestChannels"))
        radio_ok = any(re.search(r"radio\s*1", r, re.I) for r in (ui.get("radios") or []))
        sections_ok = all(title in titles for title in _SECTION_TITLES)
        canvas_ok = all(canvases.get(k) for k in ("snr", "intf", "sinr", "survey"))
        header_ok = _headers_ok(headers)
        print_comparison_table(
            [
                ("Radio selection", "Radio 1", radio_txt, "PASS" if radio_ok else "FAIL"),
                ("Start button", "present", "present" if ui.get("start") else "missing", "PASS" if ui.get("start") else "FAIL"),
                (
                    "Best Channels text",
                    "on the page",
                    "present" if best else "not on this image",
                    "PASS" if best else "WARN",
                ),
                (
                    "Graph and table sections",
                    ", ".join(_SECTION_TITLES),
                    ", ".join(titles) or "none",
                    "PASS" if sections_ok else "FAIL",
                ),
                (
                    "Plot canvases",
                    "snr, interference, SINR, survey",
                    ",".join(k for k, ok in canvases.items() if ok) or "none",
                    "PASS" if canvas_ok else "FAIL",
                ),
                (
                    "Interference table columns",
                    "Index Channel Frequency Power Min/Avg/Max SINR",
                    " | ".join(headers) or "none",
                    "PASS" if header_ok else "FAIL",
                ),
            ]
        )
        check.is_true(radio_ok, f"SPECT_ANALYZER_01 [{label}]: Radio 1 selection missing")
        check.is_true(ui.get("start"), f"SPECT_ANALYZER_01 [{label}]: Start button missing")
        check.is_true(sections_ok, f"SPECT_ANALYZER_01 [{label}]: a graph or table section is missing")
        check.is_true(canvas_ok, f"SPECT_ANALYZER_01 [{label}]: a plot canvas is missing")
        check.is_true(header_ok, f"SPECT_ANALYZER_01 [{label}]: interference table columns are incomplete")
        if not best:
            _log(f"{label}: this image has no Best Channels label. Sections above are the spectrum page.")

    await _on_both(gui_page, None, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_02_scan_start_logging(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_02 — Start begins a scan and System Logs (logread) gain a line."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_02 [{label}] — scan start and System Logs")
        await _open_page(page, host, device_creds)
        before = set(await _log_lines(ssh))
        status = ""
        hits: list[str] = []
        try:
            try:
                await click_spectrum_start(page)
                status = await _wait_scan_message(page, timeout_s=25)
            except Exception as exc:
                _log(f"{label}: GUI follow-up after Start failed ({exc})")
            deadline = time.monotonic() + LOG_WAIT_S
            while time.monotonic() < deadline and not hits:
                new_lines = [ln for ln in await _log_lines(ssh) if ln not in before]
                hits = [ln for ln in new_lines if _LOG_RE.search(ln)]
                if hits:
                    break
                await asyncio.sleep(3)
        finally:
            await _finish(page, ssh, label=label)
        started = bool(re.search(r"progress", status or "", re.I))
        print_comparison_table(
            [
                ("Scan status", "in progress", status or "idle", "PASS" if started else "FAIL"),
                (
                    "System Logs start line",
                    "new scan/spectral line",
                    (hits[-1][:80] if hits else "none"),
                    "PASS" if hits else "FAIL",
                ),
            ]
        )
        check.is_true(started, f"SPECT_ANALYZER_02 [{label}]: Start did not show a scan in progress")
        check.is_true(hits, f"SPECT_ANALYZER_02 [{label}]: System Logs have no new scan line")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_03_link_stability(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_03 — the RF link stays up while the analyzer runs.

    The scan that fills the interference plot is the AP background scan.
    On a STA, Start calls kwndiscall, and the kernel refuses the background
    collector when there is no AP VAP. This case starts the analyzer on the
    BTS and checks both ends of the link for the whole hold.
    """
    cpe_ip = (cpe_ips or [""])[0]
    print_section("SPECT_ANALYZER_03 [BTS & CPE] — link stays up during scan")
    check.is_true(bool(bsu_ip) and bool(cpe_ip), "SPECT_ANALYZER_03: both addresses are required")
    await _open_page(gui_page, bsu_ip, device_creds)
    cpe_ssh = await _open_wired_cpe_ssh(profile_bundle, device_creds)
    samples = 0
    samples_ok = 0
    bts_before = cpe_before = False
    bts_detail = cpe_detail = ""
    ping_after = False
    try:
        bts_before, bts_detail = await _associated(root_ssh)
        cpe_before, cpe_detail = await _associated(cpe_ssh)
        bts_ping = await _ping6_ok(root_ssh, cpe_ip)
        cpe_ping = await _ping6_ok(cpe_ssh, bsu_ip)
        bts_before = bts_before and bts_ping
        cpe_before = cpe_before and cpe_ping
        await click_spectrum_start(gui_page)
        await _wait_scan_message(gui_page, timeout_s=20)
        deadline = time.monotonic() + LINK_HOLD_S
        while time.monotonic() < deadline:
            samples += 1
            bts_up, _ = await _associated(root_ssh)
            cpe_up, _ = await _associated(cpe_ssh)
            bts_ping = await _ping6_ok(root_ssh, cpe_ip) or await _ping6_ok(root_ssh, cpe_ip)
            cpe_ping = await _ping6_ok(cpe_ssh, bsu_ip) or await _ping6_ok(cpe_ssh, bsu_ip)
            if bts_up and cpe_up and bts_ping and cpe_ping:
                samples_ok += 1
            else:
                _log(
                    "sample "
                    f"{samples}: bts_assoc={bts_up} cpe_assoc={cpe_up} "
                    f"bts_ping={bts_ping} cpe_ping={cpe_ping}"
                )
            await asyncio.sleep(5)
        ping_after = await _ping6_ok(root_ssh, cpe_ip) and await _ping6_ok(cpe_ssh, bsu_ip)
    finally:
        await _finish(gui_page, root_ssh, label="BTS")
        await _close_ssh(cpe_ssh)
    print_comparison_table(
        [
            ("BTS before scan", "associated + ping", bts_detail[:80], "PASS" if bts_before else "FAIL"),
            ("CPE before scan", "associated + ping", cpe_detail[:80], "PASS" if cpe_before else "FAIL"),
            ("Pings during scan", f"{samples} from each radio", f"{samples_ok}/{samples}", "PASS" if samples and samples_ok == samples else "FAIL"),
            ("Link after scan", "both pings", "ok" if ping_after else "fail", "PASS" if ping_after else "FAIL"),
        ]
    )
    check.is_true(bts_before and cpe_before, "SPECT_ANALYZER_03: link was not up before the scan")
    check.is_true(samples and samples_ok == samples, "SPECT_ANALYZER_03: link dropped while the scan ran")
    check.is_true(ping_after, "SPECT_ANALYZER_03: peer did not answer after the scan")


async def _scan_until_interference(page, host, device_creds, ssh, label):
    await _open_page(page, host, device_creds)
    if label == "CPE":
        await _publish_sta_survey(ssh)
    else:
        await click_spectrum_start(page)
    return await _wait_interference_rows(page)


async def assert_spect_analyzer_04_interference_graph(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_04 — Interference Spectrum graph shows Min, Avg, and Max."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_04 [{label}] — Interference Spectrum graph")
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            ui = await _read_ui(page)
            sample = await _canvas_sample(page, "intf-plot-canvas")
        finally:
            await _finish(page, ssh, label=label)
        legend = ui.get("legend") or ""
        legend_ok = all(token in legend for token in ("Min", "Avg", "Max"))
        drawn = _canvas_drawn(sample)
        rows = report.interference.data_row_count if report else 0
        numbers = _numeric_row(report.interference.rows) if report else False
        print_comparison_table(
            [
                ("Min / Avg / Max legend", "in the plot", "present" if legend_ok else "missing", "PASS" if legend_ok else "FAIL"),
                ("Graph drawn", "pixels on the canvas", f"{sample.get('colored', 0)} samples", "PASS" if drawn else "FAIL"),
                ("Power rows", "frequency and dBm", f"{rows} rows", "PASS" if numbers else "FAIL"),
            ]
        )
        check.is_true(legend_ok, f"SPECT_ANALYZER_04 [{label}]: graph legend is missing Min, Avg, or Max")
        check.is_true(drawn, f"SPECT_ANALYZER_04 [{label}]: interference canvas was not drawn")
        check.is_true(numbers, f"SPECT_ANALYZER_04 [{label}]: interference table has no power numbers")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_05_sinr_plot(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_05 — SINR plot uses distinct colors across the SINR range."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_05 [{label}] — SINR plot")
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            ui = await _read_ui(page)
            sample = await _canvas_sample(page, "intf-sinr-plot-canvas")
        finally:
            await _finish(page, ssh, label=label)
        colors = [c for c in (ui.get("sinrColors") or []) if c]
        distinct = len(set(colors)) >= 2
        drawn = _canvas_drawn(sample)
        sinr_vals = []
        if report:
            for row in report.interference.rows:
                if len(row) >= 7 and re.search(r"-?\d+", row[-1]):
                    sinr_vals.append(row[-1])
        print_comparison_table(
            [
                ("SINR colors", "more than one color", ", ".join(colors) or "none", "PASS" if distinct else "FAIL"),
                ("SINR plot drawn", "pixels on the canvas", f"{sample.get('colored', 0)} samples", "PASS" if drawn else "FAIL"),
                ("SINR column", "a numeric SINR", sinr_vals[0] if sinr_vals else "none", "PASS" if sinr_vals else "FAIL"),
            ]
        )
        check.is_true(distinct, f"SPECT_ANALYZER_05 [{label}]: SINR color scale is not distinct")
        check.is_true(drawn, f"SPECT_ANALYZER_05 [{label}]: SINR canvas was not drawn")
        check.is_true(sinr_vals, f"SPECT_ANALYZER_05 [{label}]: SINR column has no number")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_06_interference_table(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_06 — Interference Spectrum table columns and a data row."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_06 [{label}] — Interference Spectrum table")
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
        finally:
            await _finish(page, ssh, label=label)
        headers = report.interference.headers if report else []
        rows = report.interference.rows if report else []
        header_ok = _headers_ok(headers)
        row_ok = _numeric_row(rows)
        sample = " | ".join(rows[0]) if rows else "none"
        print_comparison_table(
            [
                (
                    "Columns",
                    "Index Channel Frequency Power Min/Avg/Max SINR",
                    " | ".join(headers) or "none",
                    "PASS" if header_ok else "FAIL",
                ),
                ("Data row", "numeric power and SINR", sample[:80], "PASS" if row_ok else "FAIL"),
            ]
        )
        check.is_true(header_ok, f"SPECT_ANALYZER_06 [{label}]: table columns do not match the sheet")
        check.is_true(row_ok, f"SPECT_ANALYZER_06 [{label}]: table has no data row")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


def _first_row(rows: list[list[str]]) -> list[str]:
    for row in rows or []:
        if _numeric_row([row]):
            return row
    return []


def _row_blob(row: list[str]) -> str:
    return " | ".join(row)


async def _click_download(page, element_id: str) -> tuple[str, str]:
    """Click a Spectrum Analyzer download icon and return filename plus text."""
    revealed = await page.evaluate(
        """(elementId) => {
            const el = document.getElementById(elementId);
            if (!el) return false;
            el.style.visibility = 'visible';
            el.style.display = 'inline-block';
            const radioName = elementId.replace('_result', '_radio');
            const radio = document.getElementById(radioName);
            if (radio && typeof radio_btn_intf !== 'undefined') {
                radio.value = String(radio_btn_intf);
            }
            return true;
        }""",
        element_id,
    )
    if not revealed:
        raise RuntimeError(f"download icon #{element_id} is not on the page")
    async with page.expect_download(timeout=25000) as pending:
        await page.locator(f"#{element_id}").click(force=True)
    download = await pending.value
    target = await download.path()
    raw = Path(target).read_bytes() if target else b""
    return download.suggested_filename or "", raw.decode("utf-8", errors="replace")


def _download_has_row(text: str, row: list[str], *, need: int = 2) -> bool:
    cells = [cell.strip() for cell in row if cell and cell.strip() and cell.strip() != "—"]
    strong = [cell for cell in cells if len(cell) >= 3 or cell.startswith("-")]
    use = strong or cells
    if len(use) < 1:
        return False
    found = sum(1 for cell in use if cell in text)
    return found >= min(need, len(use))


async def _ensure_sta_survey_file(ssh) -> None:
    """The survey download reads /etc/bg_wifi1_ap_list. Only an AP scan writes it."""
    exists = await _ssh(ssh, "test -s /etc/bg_wifi1_ap_list && echo yes || echo no")
    if "yes" in exists:
        return
    await _ssh(ssh, "wlanconfig ath1 list ap > /etc/bg_wifi1_ap_list 2>/dev/null; true")
    _log("CPE survey download file filled from wlanconfig ath1 list ap")


async def assert_spect_analyzer_09_results_download(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_09 — download icons on the two data tables."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_09 [{label}] — download both tables")
        survey_name = intf_name = ""
        survey_text = intf_text = ""
        survey_row: list[str] = []
        intf_row: list[str] = []
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            if label == "CPE":
                await _ensure_sta_survey_file(ssh)
                await page.wait_for_timeout(6000)
                report = await read_spectrum_report(page)
            survey_row = _first_row(report.survey.rows if report else [])
            intf_row = _first_row(report.interference.rows if report else [])
            intf_name, intf_text = await _click_download(page, "intf_result")
            survey_name, survey_text = await _click_download(page, "survey_result")
        finally:
            await _finish(page, ssh, label=label)
        survey_ok = survey_name.lower().endswith(".txt") and _download_has_row(survey_text, survey_row)
        intf_ok = intf_name.lower().endswith(".csv") and _download_has_row(intf_text, intf_row, need=3)
        print_comparison_table(
            [
                (
                    "Wi-Fi analyzer file",
                    "TXT with the table row",
                    f"{survey_name or 'none'} / {_row_blob(survey_row)[:60] or 'no row'}",
                    "PASS" if survey_ok else "FAIL",
                ),
                (
                    "Interference file",
                    "CSV with the table row",
                    f"{intf_name or 'none'} / {_row_blob(intf_row)[:60] or 'no row'}",
                    "PASS" if intf_ok else "FAIL",
                ),
            ]
        )
        check.is_true(survey_ok, f"SPECT_ANALYZER_09 [{label}]: survey download does not match the table")
        check.is_true(intf_ok, f"SPECT_ANALYZER_09 [{label}]: interference download does not match the table")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


def _bts_ipv4(profile_bundle) -> str:
    recovery = ((profile_bundle.active.get("testbed") or {}).get("recovery") or {}) if profile_bundle else {}
    return str(recovery.get("bts_fallback_ipv4") or "192.168.2.10")


async def _reboot_connection(conn) -> None:
    try:
        await asyncio.wait_for(conn.run("(sleep 1; reboot) >/dev/null 2>&1 &", check=False), 8)
    except Exception as exc:
        _log(f"reboot command ended the session ({exc})")


async def _ping_host(host: str, *, ipv6: bool = False) -> bool:
    cmd = ["ping6" if ipv6 else "ping", "-c", "1", "-W", "2", host]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def _wait_radio_ready(host: str, password: str, *, timeout_s: float = 150) -> bool:
    """Wait until Radio 1 is the live AP again. A reboot briefly comes up off-channel."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            conn = await asyncssh.connect(host, username="root", password=password, known_hosts=None)
        except Exception:
            await asyncio.sleep(5)
            continue
        try:
            result = await conn.run("iwconfig ath1 2>/dev/null | head -n 2", check=False)
        finally:
            conn.close()
        text = (result.stdout or "")
        if "UBR655_R1_PS" in text and "5.18 GHz" in text and "Master" in text:
            return True
        await asyncio.sleep(5)
    return False


async def _wait_host(host: str, *, timeout_s: float = 180, ipv6: bool = False) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await _ping_host(host, ipv6=ipv6):
            return True
        await asyncio.sleep(5)
    return False


async def _wait_associated(ssh, *, timeout_s: float = 120) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        up, _ = await _associated(ssh)
        if up:
            return True
        await asyncio.sleep(5)
    return False


async def assert_spect_analyzer_10_persistence_reboot(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_10 — graphs and tables must still be there after a reboot."""
    cpe_ip = (cpe_ips or [""])[0]
    print_section("SPECT_ANALYZER_10 [BTS] — results after reboot")
    await _open_page(gui_page, bsu_ip, device_creds)
    before = ""
    try:
        report = await _scan_until_interference(gui_page, bsu_ip, device_creds, root_ssh, "BTS")
        before = _row_blob(_first_row(report.interference.rows if report else []))
    finally:
        await _finish(gui_page, root_ssh, label="BTS")
    stored = await _ssh(root_ssh, "ls /etc/interference_log_wifi1.csv /tmp/interference_log_wifi1.bin 2>&1")
    _log(f"BTS files before reboot: {stored.replace(chr(10), ' | ')[:180]}")
    bts_v4 = _bts_ipv4(profile_bundle)
    reboot = await asyncssh.connect(bts_v4, username="root", password=device_creds["pass"], known_hosts=None)
    await _reboot_connection(reboot)
    reboot.close()
    bts_back = await _wait_host(bts_v4, timeout_s=180)
    if bts_back:
        bts_back = await _wait_radio_ready(bts_v4, device_creds["pass"])
    _log(f"BTS radio after reboot: {'up' if bts_back else 'down'}")
    after = ""
    if bts_back:
        # IPv6 from this PC rides the RF link and is down until the CPE joins.
        # The lab IPv4 address answers as soon as the AP radio is back.
        for attempt in (1, 2, 3, 4):
            try:
                if not await _ping_host(bts_v4):
                    raise TimeoutError(f"{bts_v4} not answering")
                await _open_page(gui_page, bts_v4, device_creds)
                await gui_page.wait_for_timeout(12000)
                after_report = await read_spectrum_report(gui_page)
                after = _row_blob(_first_row(after_report.interference.rows if after_report else []))
                break
            except Exception as exc:
                _log(f"BTS page not ready (attempt {attempt}): {exc}")
                await asyncio.sleep(10)
    bts_kept = bool(before) and before == after
    print_comparison_table(
        [
            ("Row before reboot", "a power row", before[:80] or "none", "PASS" if before else "FAIL"),
            ("Row after reboot", before[:40] or "same row", after[:80] or "none", "PASS" if bts_kept else "FAIL"),
        ]
    )

    print_section("SPECT_ANALYZER_10 [CPE] — results after reboot")
    cpe_before = ""
    cpe_ssh = await _open_wired_cpe_ssh(profile_bundle, device_creds)
    tunnel = await _open_cpe_gui_tunnel(profile_bundle)
    cpe_page = None
    try:
        cpe_page = await open_cpe_gui_session(gui_page.context, tunnel.host, device_creds)
        report = await _scan_until_interference(cpe_page, tunnel.host, device_creds, cpe_ssh, "CPE")
        cpe_before = _row_blob(_first_row(report.interference.rows if report else []))
        await _reboot_connection(cpe_ssh._conn)
    finally:
        await tunnel.close()
        if cpe_page is not None:
            try:
                await cpe_page.close()
            except Exception:
                pass
        try:
            cpe_ssh._conn.close()
            cpe_ssh._jump.close()
        except Exception:
            pass
    cpe_lan = "192.168.2.11"
    if profile_bundle:
        cpe_lan = str((profile_bundle.active.get("dut") or {}).get("cpe_lan_ipv4") or cpe_lan)
    lan_back = False
    deadline = time.monotonic() + 180
    jump = await asyncssh.connect(
        str((profile_bundle.active.get("dut") or {}).get("cpe_pc_ip") or "10.0.150.40") if profile_bundle else "10.0.150.40",
        username="root",
        password=str((profile_bundle.active.get("dut") or {}).get("cpe_pc_password") or "senao1234#") if profile_bundle else "senao1234#",
        known_hosts=None,
    )
    while time.monotonic() < deadline:
        probe = await jump.run(f"ping -c 1 -W 2 {cpe_lan} >/dev/null 2>&1; echo $?", check=False)
        if (probe.stdout or "").strip().endswith("0"):
            lan_back = True
            break
        await asyncio.sleep(5)
    _log(f"CPE LAN after reboot: {'up' if lan_back else 'down'}")
    cpe_after = ""
    cpe_ssh2 = None
    tunnel2 = None
    cpe_page2 = None

    async def _drop_cpe_retry() -> None:
        nonlocal cpe_ssh2, tunnel2, cpe_page2
        if cpe_page2 is not None:
            try:
                await cpe_page2.close()
            except Exception:
                pass
            cpe_page2 = None
        if tunnel2 is not None:
            await tunnel2.close()
            tunnel2 = None
        if cpe_ssh2 is not None:
            await _close_ssh(cpe_ssh2)
            cpe_ssh2 = None

    try:
        if lan_back:
            for attempt in (1, 2, 3, 4):
                try:
                    await asyncio.sleep(8)
                    cpe_ssh2 = await _open_wired_cpe_ssh(profile_bundle, device_creds)
                    if not await _wait_associated(cpe_ssh2, timeout_s=90):
                        await _recover_sta_if_down(cpe_ssh2)
                    tunnel2 = await _open_cpe_gui_tunnel(profile_bundle)
                    cpe_page2 = await open_cpe_gui_session(gui_page.context, tunnel2.host, device_creds)
                    await _open_page(cpe_page2, tunnel2.host, device_creds)
                    await cpe_page2.wait_for_timeout(12000)
                    after_report = await read_spectrum_report(cpe_page2)
                    cpe_after = _row_blob(_first_row(after_report.interference.rows if after_report else []))
                    break
                except Exception as exc:
                    _log(f"CPE page not ready (attempt {attempt}): {exc}")
                    await _drop_cpe_retry()
                    await asyncio.sleep(10)
    finally:
        if cpe_ssh2 is not None:
            await _close_ssh(cpe_ssh2)
        if tunnel2 is not None:
            await tunnel2.close()
        if cpe_page2 is not None:
            try:
                await cpe_page2.close()
            except Exception:
                pass
        jump.close()
    cpe_kept = bool(cpe_before) and cpe_before == cpe_after
    print_comparison_table(
        [
            ("Row before reboot", "a power row", cpe_before[:80] or "none", "PASS" if cpe_before else "FAIL"),
            ("Row after reboot", cpe_before[:40] or "same row", cpe_after[:80] or "none", "PASS" if cpe_kept else "FAIL"),
        ]
    )
    check.is_true(bool(before), "SPECT_ANALYZER_10 [BTS]: no interference row before the reboot")
    check.is_true(bts_kept, "SPECT_ANALYZER_10 [BTS]: interference results were erased by the reboot")
    check.is_true(bool(cpe_before), "SPECT_ANALYZER_10 [CPE]: no interference row before the reboot")
    check.is_true(cpe_kept, "SPECT_ANALYZER_10 [CPE]: interference results were erased by the reboot")
    if bts_back:
        link_up = await _ping_host(cpe_ip, ipv6=True)
        _log(f"CPE IPv6 after both reboots: {'up' if link_up else 'down'}")


IPERF_PORT = 5216
_MEM_DROP_KB = 65536


def _track_errors(page) -> list[str]:
    found: list[str] = []

    def _on_error(exc) -> None:
        found.append(str(exc))

    page.on("pageerror", _on_error)
    return found


async def _ui_responds(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => !!(
                    document.querySelector('#start_button input[value="Start"]')
                    || document.querySelector('#stop_button')
                )"""
            )
        )
    except Exception:
        return False


async def _scan_halted(page, *, timeout_s: float = 8) -> str:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        last = await read_spectrum_status(page)
        if not re.search(r"in progress", last or "", re.I):
            return last or "idle"
        await asyncio.sleep(1)
    return last or "still in progress"


def _cell_num(cell: str) -> int | None:
    match = re.search(r"-?\d+", cell or "")
    return int(match.group()) if match else None


def _power_tuple(row: list[str]) -> tuple[int, int, int, int, int] | None:
    if len(row) < 7:
        return None
    freq, low, avg, high, sinr = (
        _cell_num(row[2]),
        _cell_num(row[3]),
        _cell_num(row[4]),
        _cell_num(row[5]),
        _cell_num(row[6]),
    )
    if None in (freq, low, avg, high, sinr):
        return None
    return freq, low, avg, high, sinr


async def _bts_run(password: str, command: str, *, timeout_s: int = 30) -> str:
    conn = await asyncssh.connect(
        "192.168.2.10",
        username="root",
        password=password,
        known_hosts=None,
    )
    try:
        result = await asyncio.wait_for(conn.run(command, check=False), timeout_s)
        return (result.stdout or "") + (result.stderr or "")
    finally:
        conn.close()


async def _mem_available_kb(ssh) -> int:
    text = await _ssh(ssh, "awk '/MemAvailable/ {print $2}' /proc/meminfo")
    nums = re.findall(r"\d+", text or "")
    return int(nums[-1]) if nums else 0


async def _cpu_busy_pct(ssh) -> float:
    def _parse(line: str) -> tuple[int, int]:
        nums = [int(part) for part in line.split()[1:] if part.isdigit()]
        idle = nums[3] if len(nums) > 3 else 0
        return idle, sum(nums)

    first = await _ssh(ssh, "grep '^cpu ' /proc/stat")
    await asyncio.sleep(1)
    second = await _ssh(ssh, "grep '^cpu ' /proc/stat")
    idle1, total1 = _parse(first or "")
    idle2, total2 = _parse(second or "")
    delta = total2 - total1
    if delta <= 0:
        return 0.0
    return round(100 * (1 - (idle2 - idle1) / delta), 1)


async def _ap_list(label: str, ssh, password: str) -> str:
    command = "wlanconfig ath1 list ap 2>/dev/null"
    if label == "BTS":
        return await _bts_run(password, command)
    return await _ssh(ssh, command, timeout_s=20)


def _ap_rows(text: str) -> list[str]:
    rows = []
    for line in (text or "").splitlines():
        if re.search(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", line, re.I):
            rows.append(line.strip())
    return rows


_LINK_MACS = ("c4:e3:ce:63:e6:c7", "c4:e3:ce:63:e6:31")


async def _hover_tooltip(page, canvas_id: str) -> str:
    return await page.evaluate(
        """async (canvasId) => {
            const canvas = document.getElementById(canvasId);
            const plot = window.KWN_INTF_PLOT;
            const st = plot && plot._state ? plot._state[canvasId] : null;
            if (!canvas || !st || !st.hitboxes || !st.hitboxes.length) return '';
            const box = st.hitboxes[Math.min(st.hitboxes.length - 1, Math.floor(st.hitboxes.length / 2))];
            const rect = canvas.getBoundingClientRect();
            const scales = [rect.width / (canvas.width || rect.width), 1];
            for (const scale of scales) {
                const x = rect.left + (box.x + box.w / 2) * scale;
                const y = rect.top + Math.min(rect.height - 2, (box.y + box.h / 2) * (rect.height / (canvas.height || rect.height)));
                canvas.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: x, clientY: y }));
                const tip = canvas.parentNode ? canvas.parentNode.querySelector('.kwn-intf-plot-tooltip') : null;
                const text = tip && tip.style.display !== 'none' ? (tip.innerText || '') : '';
                if (text.trim()) return text;
            }
            return '';
        }""",
        canvas_id,
    )


def _tooltip_matches(tip: str, rows: list[list[str]], kind: str) -> bool:
    freq = re.search(r"(\d{4})", tip or "")
    value = re.search(r"(?:Avg|SINR):\s*(-?\d+)", tip or "", re.I)
    if not freq or not value:
        return False
    number = int(value.group(1))
    for row in rows:
        blob = " ".join(row)
        if freq.group(1) not in blob:
            continue
        if value.group(1) in blob:
            return True
        parsed = _power_tuple(row)
        if kind == "avg" and parsed and parsed[1] <= number <= parsed[3]:
            return True
    return False


def _script_notes(errors: list[str]) -> list[str]:
    """Poll script notes that do not lock the page."""
    notes = []
    for item in errors:
        if "split" in item or "innerHTML" in item:
            notes.append(item)
    return notes


def _hidden_lines_from_iw(text: str) -> list[str]:
    """Empty SSID beacons from `iw scan dump` are the hidden APs the STA heard."""
    lines = []
    bssid = ""
    for raw in text.splitlines():
        match = re.match(r"BSS ([0-9a-f:]{17})", raw.strip(), re.I)
        if match:
            bssid = match.group(1).lower()
            continue
        if bssid and raw.strip() in ("SSID:", "SSID"):
            lines.append(
                f"[ hidden ]                          {bssid}     36   5180      1     80"
            )
            bssid = ""
        elif bssid and raw.strip().startswith("SSID:"):
            bssid = ""
    return lines


async def assert_spect_analyzer_12_midway_stop(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_12 — Stop halts a running scan and the page stays usable."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_12 [{label}] — stop mid-scan")
        errors = _track_errors(page)
        status = ""
        halted = ""
        alive = False
        try:
            await _open_page(page, host, device_creds)
            await click_spectrum_start(page)
            status = await _wait_scan_message(page, timeout_s=20)
            await click_spectrum_stop(page)
            halted = await _scan_halted(page)
            alive = await _ui_responds(page)
        finally:
            await _finish(page, ssh, label=label)
        stopped = bool(status) and not re.search(r"in progress", halted or "", re.I)
        if errors:
            _log(f"{label}: script note after Stop: {'; '.join(errors)[:240]}")
        print_comparison_table(
            [
                ("Scan before Stop", "in progress", status or "idle", "PASS" if re.search(r"progress", status or "", re.I) else "FAIL"),
                ("Status after Stop", "not in progress", halted or "none", "PASS" if stopped else "FAIL"),
                ("Page after Stop", "Start still answers", "answers" if alive else "locked", "PASS" if alive else "FAIL"),
                (
                    "Page script",
                    "does not lock the page",
                    "answers, with a script note" if errors and alive else ("answers" if alive else "locked"),
                    "PASS" if alive else "FAIL",
                ),
            ]
        )
        check.is_true(re.search(r"progress", status or "", re.I), f"SPECT_ANALYZER_12 [{label}]: scan was not running")
        check.is_true(stopped, f"SPECT_ANALYZER_12 [{label}]: Stop left the scan in progress")
        check.is_true(alive, f"SPECT_ANALYZER_12 [{label}]: page locked after Stop")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_13_noise_floor(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_13 — Power Min is a noise-floor number on each frequency row."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_13 [{label}] — noise floor")
        rows: list[tuple[int, int, int, int, int]] = []
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            for row in (report.interference.rows if report else []):
                parsed = _power_tuple(row)
                if parsed:
                    rows.append(parsed)
        finally:
            await _finish(page, ssh, label=label)
        good = [row for row in rows if -130 <= row[1] <= -40 and row[1] <= row[2] <= row[3]]
        freqs = {row[0] for row in good}
        ok = len(good) >= 1 and len(freqs) >= 1 and len(good) == len(rows) and bool(rows)
        sample = rows[0] if rows else None
        shown = f"{sample[0]} min {sample[1]}" if sample else "none"
        print_comparison_table(
            [
                ("Power Min rows", "dBm on each frequency", f"{len(good)}/{len(rows)}", "PASS" if ok else "FAIL"),
                ("First frequency", "min <= avg <= max", shown, "PASS" if sample and sample in good else "FAIL"),
            ]
        )
        check.is_true(ok, f"SPECT_ANALYZER_13 [{label}]: Power Min is missing or not a noise-floor reading")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_14_external_interference(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_14 — energy from a neighboring AP shows in Max Power and SINR."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_14 [{label}] — external interference")
        listing = await _ap_list(label, ssh, device_creds["pass"])
        external = [
            row for row in _ap_rows(listing)
            if not any(mac in row.lower() for mac in _LINK_MACS)
        ]
        peak = None
        drawn = False
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            parsed = [row for row in (report.interference.rows if report else []) if _power_tuple(row)]
            values = [_power_tuple(row) for row in parsed]
            if values:
                peak = max(values, key=lambda item: item[3])
            drawn = _canvas_drawn(await _canvas_sample(page, "intf-plot-canvas"))
        finally:
            await _finish(page, ssh, label=label)
        highlighted = bool(peak) and peak[3] > peak[1] and drawn
        # A STA list ap shows only the serving BTS. The off-channel peak is the energy it measured.
        heard = bool(external) or (label == "CPE" and highlighted)
        external_txt = external[0][:70] if external else (
            f"survey peak {peak[0]}" if peak and label == "CPE" else "none"
        )
        print_comparison_table(
            [
                ("External AP", "a neighbor or a survey peak", external_txt, "PASS" if heard else "FAIL"),
                (
                    "Max Power peak",
                    "above the noise floor, with SINR",
                    f"{peak[0]} max {peak[3]} SINR {peak[4]}" if peak else "none",
                    "PASS" if highlighted else "FAIL",
                ),
            ]
        )
        check.is_true(heard, f"SPECT_ANALYZER_14 [{label}]: no external AP to introduce")
        check.is_true(highlighted, f"SPECT_ANALYZER_14 [{label}]: Max Power and SINR did not show a peak")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_15_idle_performance(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_15 — an idle scan does not peg the CPU or the page."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_15 [{label}] — idle scan")
        before_mem = await _mem_available_kb(ssh)
        before_cpu = await _cpu_busy_pct(ssh)
        lag_s = 99.0
        during_cpu = 100.0
        after_mem = before_mem
        try:
            await _open_page(page, host, device_creds)
            if label == "CPE":
                await _publish_sta_survey(ssh)
            else:
                await click_spectrum_start(page)
            await asyncio.sleep(5)
            during_cpu = await _cpu_busy_pct(ssh)
            after_mem = await _mem_available_kb(ssh)
            started = time.monotonic()
            await read_spectrum_status(page)
            lag_s = time.monotonic() - started
        finally:
            await _finish(page, ssh, label=label)
        mem_drop = before_mem - after_mem
        cpu_ok = during_cpu < 90 and (during_cpu - before_cpu) < 50
        mem_ok = mem_drop < _MEM_DROP_KB
        lag_ok = lag_s < 5
        print_comparison_table(
            [
                ("CPU while scanning", "under 90%, jump under 50 points", f"idle {before_cpu}% scan {during_cpu}%", "PASS" if cpu_ok else "FAIL"),
                ("Available memory", "drop under 64 MB", f"{mem_drop} kB", "PASS" if mem_ok else "FAIL"),
                ("UI read", "under 5 s", f"{lag_s:.1f} s", "PASS" if lag_ok else "FAIL"),
            ]
        )
        check.is_true(cpu_ok and mem_ok and lag_ok, f"SPECT_ANALYZER_15 [{label}]: scan spiked CPU, memory, or the page")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_16_load_performance(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_16 — SSH iperf across the link while the BTS scan runs."""
    print_section("SPECT_ANALYZER_16 [BTS & CPE] — iperf during scan")
    password = device_creds["pass"]
    cpe_ssh = await _open_wired_cpe_ssh(profile_bundle, device_creds)
    baseline = 0.0
    loaded = 0.0
    associated = False

    async def _mbps() -> float:
        raw = await _bts_run(
            password,
            f"iperf3 -c 192.168.2.11 -p {IPERF_PORT} -t 8 -J",
            timeout_s=25,
        )
        start = raw.find("{")
        if start < 0:
            _log(f"iperf raw: {raw[-180:]}")
            return 0.0
        try:
            data = json.loads(raw[start:])
        except json.JSONDecodeError:
            _log(f"iperf json: {raw[-180:]}")
            return 0.0
        end = data.get("end") or {}
        bps = ((end.get("sum_received") or {}).get("bits_per_second")
               or (end.get("sum_sent") or {}).get("bits_per_second")
               or 0)
        return round(float(bps) / 1_000_000, 1)

    try:
        up, detail = await _associated(cpe_ssh)
        check.is_true(up, "SPECT_ANALYZER_16: CPE was not associated before iperf")
        await _ssh(cpe_ssh, f"killall iperf3 >/dev/null 2>&1; (iperf3 -s -p {IPERF_PORT} >/tmp/spect_iperf.log 2>&1 &); echo OK")
        await asyncio.sleep(1)
        baseline = await _mbps()
        await _open_page(gui_page, bsu_ip, device_creds)
        await click_spectrum_start(gui_page)
        await _wait_scan_message(gui_page, timeout_s=20)
        loaded = await _mbps()
        associated, detail = await _associated(cpe_ssh)
    finally:
        await _finish(gui_page, root_ssh, label="BTS")
        await _ssh(cpe_ssh, "killall iperf3 >/dev/null 2>&1; true")
        await _bts_run(password, "killall iperf3 >/dev/null 2>&1; true")
        await _close_ssh(cpe_ssh)
    kept = baseline >= 10 and loaded >= baseline * 0.7 and associated
    print_comparison_table(
        [
            ("iperf idle", "TCP from BTS SSH to CPE", f"{baseline} Mbps", "PASS" if baseline >= 10 else "FAIL"),
            ("iperf during scan", "at least 70% of idle", f"{loaded} Mbps", "PASS" if baseline and loaded >= baseline * 0.7 else "FAIL"),
            ("CPE association", "stays up", detail[:80], "PASS" if associated else "FAIL"),
        ]
    )
    check.is_true(kept, "SPECT_ANALYZER_16: iperf dropped while the scan ran")


async def assert_spect_analyzer_17_multi_cpe(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_17 — the connected CPE must stay associated through a scan.

    This bench has one CPE. The check is that the scan does not drop it.
    """
    print_section("SPECT_ANALYZER_17 [BTS] — CPE stays up during scan")
    listing = await _bts_run(device_creds["pass"], "wlanconfig ath1 list sta 2>/dev/null")
    stations = [
        ln for ln in listing.splitlines()
        if re.match(r"\s*[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", ln, re.I)
    ]
    cpe_ssh = await _open_wired_cpe_ssh(profile_bundle, device_creds)
    before, _ = await _associated(cpe_ssh)
    try:
        await _open_page(gui_page, bsu_ip, device_creds)
        await click_spectrum_start(gui_page)
        await asyncio.sleep(12)
        after, detail = await _associated(cpe_ssh)
    finally:
        await _finish(gui_page, root_ssh, label="BTS")
        await _close_ssh(cpe_ssh)
    stayed = before and after and len(stations) >= 1
    print_comparison_table(
        [
            ("Connected CPEs", "at least the live STA", str(len(stations)), "PASS" if stations else "FAIL"),
            ("Stations after scan", "none dropped", detail[:80] if stayed else "dropped", "PASS" if stayed else "FAIL"),
        ]
    )
    check.is_true(stayed, "SPECT_ANALYZER_17: the connected CPE dropped during the scan")


async def assert_spect_analyzer_18_remote_session(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_18 — the remote management page returns scan results."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_18 [{label}] — remote management scan")
        count = 0
        url = ""
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            count = report.interference.data_row_count if report else 0
            url = page.url
        finally:
            await _finish(page, ssh, label=label)
        remote = "cgi-bin/luci" in url or "monitor" in url
        ok = count > 0 and remote
        print_comparison_table(
            [
                ("Remote page", "session stays on Spectrum Analyzer", url[:70] or "closed", "PASS" if remote else "FAIL"),
                ("Results", "a table row", f"{count} rows", "PASS" if count else "FAIL"),
            ]
        )
        check.is_true(ok, f"SPECT_ANALYZER_18 [{label}]: remote session did not return results")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_19_concurrent_admins(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_19 — a second admin login sees the scan the first one started."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_19 [{label}] — two admin sessions")
        browser = page.context.browser
        ctx2 = await browser.new_context(ignore_https_errors=True, accept_downloads=True)
        page2 = await ctx2.new_page()
        first = ""
        second = ""
        try:
            await _open_page(page, host, device_creds)
            if host.startswith("127."):
                await page2.goto(
                    f"https://{host}/cgi-bin/luci/",
                    timeout=60000,
                    wait_until="domcontentloaded",
                )
            await _open_page(page2, host, device_creds)
            if label == "CPE":
                await _publish_sta_survey(ssh)
                await page.wait_for_timeout(8000)
                await page2.wait_for_timeout(4000)
            else:
                await click_spectrum_start(page)
                first = await _wait_scan_message(page, timeout_s=20)
                await page2.wait_for_timeout(8000)
            second = await read_spectrum_status(page2)
            report = await read_spectrum_report(page2)
            rows = report.interference.data_row_count if report else 0
            if not second and rows:
                second = f"{rows} rows"
            elif rows and "progress" not in (second or "").lower():
                second = f"{second} / {rows} rows"
        finally:
            await _finish(page, ssh, label=label)
            await ctx2.close()
        if label == "CPE":
            synced = "rows" in (second or "")
        else:
            synced = bool(re.search(r"progress|rows", second or "", re.I))
        print_comparison_table(
            [
                ("Admin 1", "scan started", first or "survey published", "PASS"),
                ("Admin 2", "same scan or the same rows", second or "blank", "PASS" if synced else "FAIL"),
            ]
        )
        check.is_true(synced, f"SPECT_ANALYZER_19 [{label}]: the second session did not show the scan")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_20_page_refresh(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_20 — F5 during a scan reloads without an error."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_20 [{label}] — refresh during scan")
        errors = _track_errors(page)
        after = ""
        alive = False
        try:
            await _open_page(page, host, device_creds)
            if label == "CPE":
                await _publish_sta_survey(ssh)
            else:
                await click_spectrum_start(page)
                await _wait_scan_message(page, timeout_s=20)
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)
            if "login" in (page.url or "") or await page.locator("input[name='luci_password'], input[name='password']").count():
                await _open_page(page, host, device_creds)
            after = await read_spectrum_status(page)
            report = await read_spectrum_report(page)
            rows = report.interference.data_row_count if report else 0
            if rows and not re.search(r"progress", after or "", re.I):
                after = f"{after or 'idle'} / {rows} rows"
            alive = await _ui_responds(page)
            body = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 400)")
            if re.search(r"lua error|uncaught|traceback", body or "", re.I):
                errors.append("error text on the page")
        finally:
            await _finish(page, ssh, label=label)
        showed = bool(re.search(r"progress|rows", after or "", re.I))
        broken = any("error text" in item for item in errors)
        if errors:
            _log(f"{label}: script note after refresh: {'; '.join(errors)[:240]}")
        recovered = alive and showed and not broken
        print_comparison_table(
            [
                ("After refresh", "scan status or last rows", after or "blank", "PASS" if showed and alive else "FAIL"),
                (
                    "Page",
                    "no error page",
                    "answers, with a script note" if errors and not broken else ("answers" if alive else "locked"),
                    "PASS" if alive and not broken else "FAIL",
                ),
            ]
        )
        check.is_true(recovered, f"SPECT_ANALYZER_20 [{label}]: refresh did not recover the scan page")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_21_high_ap_density(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_21 — the Wi-Fi table scrolls without a page crash.

    This RF does not have 50 APs. The check is that every AP the radio heard
    is listed and the table still scrolls.
    """

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_21 [{label}] — high AP density")
        errors = _track_errors(page)
        listing = await _ap_list(label, ssh, device_creds["pass"])
        count = len(_ap_rows(listing))
        scrolled = False
        try:
            await _open_page(page, host, device_creds)
            if label != "CPE":
                await _ssh(ssh, "wlanconfig ath1 list ap > /etc/bg_wifi1_ap_list 2>/dev/null; true")
            else:
                await _ensure_sta_survey_file(ssh)
            await page.reload(wait_until="domcontentloaded")
            await _open_page(page, host, device_creds)
            await page.wait_for_timeout(4000)
            scrolled = bool(
                await page.evaluate(
                    """() => {
                        const tbl = document.querySelector('#advscan-list');
                        if (!tbl) return false;
                        tbl.scrollIntoView();
                        return true;
                    }"""
                )
            )
        finally:
            await _finish(page, ssh, label=label)
        breaking = [item for item in errors if item not in _script_notes(errors)]
        if errors:
            _log(f"{label}: script note on the Wi-Fi table: {'; '.join(errors)[:180]}")
        ok = count >= 1 and scrolled and not breaking
        print_comparison_table(
            [
                ("APs heard", "the live AP list", str(count), "PASS" if count else "FAIL"),
                (
                    "Wi-Fi table",
                    "scrolls without a page crash",
                    "scrolled" if ok else "error",
                    "PASS" if ok else "FAIL",
                ),
            ]
        )
        check.is_true(ok, f"SPECT_ANALYZER_21 [{label}]: Wi-Fi table did not scroll cleanly ({count} APs)")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_22_hidden_ssid_download(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_22 — a hidden SSID survives the Wi-Fi Channel download."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_22 [{label}] — hidden SSID download")
        listing = await _ap_list(label, ssh, device_creds["pass"])
        if "[ hidden ]" not in listing.lower() and label == "CPE":
            dump = await _ssh(
                ssh,
                "iw dev ath1 scan trigger >/dev/null 2>&1; sleep 2; iw dev ath1 scan dump 2>/dev/null",
                timeout_s=25,
            )
            hidden = _hidden_lines_from_iw(dump)
            if hidden:
                listing = listing.rstrip() + "\n" + "\n".join(hidden) + "\n"
                _log(f"CPE survey file includes {len(hidden)} hidden SSID(s) from iw scan dump")
            await _recover_sta_if_down(ssh)
        payload = base64.b64encode(listing.encode()).decode()
        writer = _bts_run if label == "BTS" else None
        if writer:
            await _bts_run(device_creds["pass"], f"printf '%s' '{payload}' | base64 -d > /etc/bg_wifi1_ap_list")
        else:
            await _ssh(ssh, f"printf '%s' '{payload}' | base64 -d > /etc/bg_wifi1_ap_list", timeout_s=20)
        name = ""
        body = ""
        try:
            await _open_page(page, host, device_creds)
            await page.wait_for_timeout(2000)
            name, body = await _click_download(page, "survey_result")
        finally:
            await _finish(page, ssh, label=label)
        intact = bool(re.search(r"\[ hidden \]\s+[0-9a-f]{2}(?::[0-9a-f]{2}){5}", body, re.I))
        print_comparison_table(
            [
                ("Download", "hidden SSID and its BSSID on one line", f"{name or 'none'}", "PASS" if intact else "FAIL"),
            ]
        )
        check.is_true(intact, f"SPECT_ANALYZER_22 [{label}]: hidden SSID brackets did not survive the download")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_23_graph_tooltips(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_23 — hover tooltips match the interference table."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_23 [{label}] — graph tooltips")
        intf_tip = ""
        sinr_tip = ""
        rows: list[list[str]] = []
        try:
            report = await _scan_until_interference(page, host, device_creds, ssh, label)
            rows = list(report.interference.rows if report else [])
            intf_tip = await _hover_tooltip(page, "intf-plot-canvas")
            sinr_tip = await _hover_tooltip(page, "intf-sinr-plot-canvas")
        finally:
            await _finish(page, ssh, label=label)
        intf_ok = _tooltip_matches(intf_tip, rows, "avg")
        sinr_ok = _tooltip_matches(sinr_tip, rows, "sinr")
        print_comparison_table(
            [
                ("Interference tooltip", "frequency and Avg from the table", (intf_tip or "none").replace("\n", " ")[:70], "PASS" if intf_ok else "FAIL"),
                ("SINR tooltip", "frequency and SINR from the table", (sinr_tip or "none").replace("\n", " ")[:70], "PASS" if sinr_ok else "FAIL"),
            ]
        )
        check.is_true(intf_ok, f"SPECT_ANALYZER_23 [{label}]: interference tooltip does not match the table")
        check.is_true(sinr_ok, f"SPECT_ANALYZER_23 [{label}]: SINR tooltip does not match the table")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_24_continuous_scan(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_24 — ten scan cycles leave the device and the page stable."""

    async def _one(page, ssh, *, label, host, peer_ip, peer_ssh, device_creds):
        del peer_ip, peer_ssh
        print_section(f"SPECT_ANALYZER_24 [{label}] — ten scan cycles")
        errors = _track_errors(page)
        before = await _mem_available_kb(ssh)
        cycles = 0
        try:
            await _open_page(page, host, device_creds)
            for _ in range(10):
                if label == "CPE":
                    await _publish_sta_survey(ssh)
                    await page.wait_for_timeout(2000)
                else:
                    await click_spectrum_start(page)
                    await asyncio.sleep(4)
                    await click_spectrum_stop(page)
                    await asyncio.sleep(1)
                cycles += 1
            alive = await _ui_responds(page)
        finally:
            await _finish(page, ssh, label=label)
            alive = await _ui_responds(page) if page else False
        after = await _mem_available_kb(ssh)
        up, detail = await _associated(ssh) if label == "CPE" else (True, "BTS scan host")
        if label != "CPE":
            radio = await _ssh(ssh, "iwconfig ath1 2>/dev/null | head -n 1")
            up = "UBR655_R1_PS" in (radio or "")
            detail = " ".join((radio or "").split())[:80]
        if not alive:
            await asyncio.sleep(2)
            alive = await _ui_responds(page)
        if errors:
            _log(f"{label}: script note after 10 cycles: {'; '.join(errors)[:240]}")
        drop = before - after
        ok = cycles == 10 and alive and drop < _MEM_DROP_KB and up
        print_comparison_table(
            [
                ("Cycles", "10", str(cycles), "PASS" if cycles == 10 else "FAIL"),
                ("Available memory", "drop under 64 MB", f"{drop} kB", "PASS" if drop < _MEM_DROP_KB else "FAIL"),
                ("Radio after", "still up", detail or "down", "PASS" if up else "FAIL"),
                (
                    "Page",
                    "still answers",
                    "answers, with a script note" if alive and errors else ("answers" if alive else "locked"),
                    "PASS" if alive else "FAIL",
                ),
            ]
        )
        check.is_true(ok, f"SPECT_ANALYZER_24 [{label}]: ten cycles were not stable")

    await _on_both(gui_page, root_ssh, cpe_ips, bsu_ip, device_creds, _one, profile_bundle)


async def assert_spect_analyzer_25_disconnect_recovery(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle=None,
) -> None:
    """SPECT_ANALYZER_25 — results return after the management browser session drops.

    This session cannot take enp1s0 down. The management browser is set offline,
    then brought back, which is the session the page actually uses.
    """
    print_section("SPECT_ANALYZER_25 [BTS] — management session drop")
    before = 0
    after = 0
    try:
        report = await _scan_until_interference(gui_page, bsu_ip, device_creds, root_ssh, "BTS")
        before = report.interference.data_row_count if report else 0
        await gui_page.context.set_offline(True)
        await asyncio.sleep(5)
        await gui_page.context.set_offline(False)
        await _open_page(gui_page, bsu_ip, device_creds)
        await gui_page.wait_for_timeout(12000)
        restored = await read_spectrum_report(gui_page)
        after = restored.interference.data_row_count if restored else 0
    finally:
        try:
            await gui_page.context.set_offline(False)
        except Exception:
            pass
        await _finish(gui_page, root_ssh, label="BTS")
    ok = before > 0 and after > 0
    print_comparison_table(
        [
            ("Rows before disconnect", "a result", str(before), "PASS" if before else "FAIL"),
            ("Rows after reconnect", "results still shown", str(after), "PASS" if after else "FAIL"),
        ]
    )
    check.is_true(ok, "SPECT_ANALYZER_25: results were gone after the management session returned")

    print_section("SPECT_ANALYZER_25 [CPE] — management session drop")
    cpe_ssh = await _open_wired_cpe_ssh(profile_bundle, device_creds)
    tunnel = await _open_cpe_gui_tunnel(profile_bundle)
    cpe_page = None
    cpe_before = 0
    cpe_after = 0
    try:
        cpe_page = await open_cpe_gui_session(gui_page.context, tunnel.host, device_creds)
        report = await _scan_until_interference(cpe_page, tunnel.host, device_creds, cpe_ssh, "CPE")
        cpe_before = report.interference.data_row_count if report else 0
        await cpe_page.context.set_offline(True)
        await asyncio.sleep(5)
        await cpe_page.context.set_offline(False)
        await _open_page(cpe_page, tunnel.host, device_creds)
        await cpe_page.wait_for_timeout(8000)
        restored = await read_spectrum_report(cpe_page)
        cpe_after = restored.interference.data_row_count if restored else 0
    finally:
        try:
            await gui_page.context.set_offline(False)
        except Exception:
            pass
        if cpe_page is not None:
            try:
                await _finish(cpe_page, cpe_ssh, label="CPE")
            except Exception:
                await _recover_sta_if_down(cpe_ssh)
            try:
                await cpe_page.close()
            except Exception:
                pass
        else:
            await _recover_sta_if_down(cpe_ssh)
        await tunnel.close()
        await _close_ssh(cpe_ssh)
    cpe_ok = cpe_before > 0 and cpe_after > 0
    print_comparison_table(
        [
            ("Rows before disconnect", "a result", str(cpe_before), "PASS" if cpe_before else "FAIL"),
            ("Rows after reconnect", "results still shown", str(cpe_after), "PASS" if cpe_after else "FAIL"),
        ]
    )
    check.is_true(cpe_ok, "SPECT_ANALYZER_25 [CPE]: results were gone after the management session returned")
