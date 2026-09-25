"""Sanity-only Spectrum Analyser helpers (Monitor → Tools → Spectrum Analyzer)."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from pages.locators import (
    CommonLocators,
    DiagnosticsLocators,
    MonitorLocators,
    SpectrumAnalyserLocators,
    UITimeouts,
)
from utils.net_utils import format_luci_url
from utils.network_flows import _goto_admin_path
from utils.sanity_gui import sanity_login_if_needed
from utils.sanity_ssh import ensure_sanity_ssh_open


@dataclass
class SpectrumTable:
    name: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @property
    def data_row_count(self) -> int:
        return len(self.rows)


@dataclass
class SpectrumReport:
    start_freq: str = ""
    end_freq: str = ""
    status_msg: str = ""
    survey: SpectrumTable = field(default_factory=lambda: SpectrumTable("Wi-Fi Channel Analyzer"))
    channel: SpectrumTable = field(default_factory=lambda: SpectrumTable("Channel Scan Results"))
    interference: SpectrumTable = field(default_factory=lambda: SpectrumTable("Interference Spectrum"))
    ssh_intf_lines: list[str] = field(default_factory=list)

    @property
    def has_results(self) -> bool:
        return (
            self.survey.data_row_count > 0
            or self.channel.data_row_count > 0
            or self.interference.data_row_count > 0
            or bool(self.ssh_intf_lines)
        )


_COLLECTING_RE = re.compile(r"collecting data|no entries|no data|spectrum scan is in progress", re.I)


async def open_sanity_spectrum_page(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Monitor → Tools → Spectrum Analyzer."""
    if host and device_creds:
        await sanity_login_if_needed(gui_page, host, device_creds)

    opened = False
    try:
        menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
        await menu.wait_for(state="visible", timeout=8000)
        await menu.click()
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        tools = gui_page.locator(MonitorLocators.SUBMENU_TOOLS).first
        await tools.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await tools.click()
        await gui_page.wait_for_load_state("domcontentloaded")
        opened = True
    except Exception:
        opened = await _goto_admin_path(gui_page, "/monitor/tools")

    if not opened and host:
        url = f"{format_luci_url(host).rstrip('/')}/admin/monitor/tools/spectrum"
        await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        if device_creds:
            await sanity_login_if_needed(gui_page, host, device_creds)

    tab = gui_page.locator(SpectrumAnalyserLocators.TAB_SPECTRUM).or_(
        gui_page.locator(DiagnosticsLocators.TAB_SPECTRUM)
    ).first
    try:
        await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await tab.click()
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        if host:
            url = f"{format_luci_url(host).rstrip('/')}/admin/monitor/tools/spectrum"
            await gui_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            if device_creds:
                await sanity_login_if_needed(gui_page, host, device_creds)

    start_btn = gui_page.locator(SpectrumAnalyserLocators.START_BUTTON).first
    await start_btn.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def read_spectrum_freq_range(gui_page) -> tuple[str, str]:
    start = ""
    end = ""
    try:
        start = (await gui_page.locator(SpectrumAnalyserLocators.START_FREQ).input_value()).strip()
    except Exception:
        pass
    try:
        end = (await gui_page.locator(SpectrumAnalyserLocators.END_FREQ).input_value()).strip()
    except Exception:
        pass
    if not start or not end:
        # syncFreqValues() may not have run yet — pull from page JS values.
        try:
            pair = await gui_page.evaluate(
                """() => {
                    try {
                        if (typeof syncFreqValues === 'function') syncFreqValues();
                        const s = document.getElementById('start');
                        const e = document.getElementById('end');
                        return [(s && s.value) || '', (e && e.value) || ''];
                    } catch (err) {
                        return ['', ''];
                    }
                }"""
            )
            start = start or (pair[0] if pair else "")
            end = end or (pair[1] if pair else "")
        except Exception:
            pass
    return str(start or "").strip(), str(end or "").strip()


async def set_spectrum_freq_range(gui_page, start_mhz: str | int, end_mhz: str | int) -> None:
    start_s = str(start_mhz).strip()
    end_s = str(end_mhz).strip()
    await gui_page.evaluate(
        """([start, end]) => {
            const s = document.getElementById('start');
            const e = document.getElementById('end');
            if (s) {
                s.value = start;
                s.dispatchEvent(new Event('input', { bubbles: true }));
                s.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (e) {
                e.value = end;
                e.dispatchEvent(new Event('input', { bubbles: true }));
                e.dispatchEvent(new Event('change', { bubbles: true }));
            }
            try {
                if (typeof values === 'object' && values) {
                    const ri = (typeof radio_btn_intf !== 'undefined') ? radio_btn_intf : 1;
                    values['startfreq-ath' + ri] = start;
                    values['endfreq-ath' + ri] = end;
                }
            } catch (err) {}
        }""",
        [start_s, end_s],
    )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def click_spectrum_start(gui_page) -> None:
    # Prefer JS start_stop_scan(1) — matches GUI onclick reliably.
    started = await gui_page.evaluate(
        """() => {
            if (typeof start_stop_scan === 'function') {
                start_stop_scan(1);
                return true;
            }
            const btn = document.querySelector('#start_button input[value="Start"]');
            if (btn) { btn.click(); return true; }
            return false;
        }"""
    )
    if not started:
        await gui_page.locator(SpectrumAnalyserLocators.START_BUTTON).first.click(force=True)
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def click_spectrum_stop(gui_page) -> None:
    await gui_page.evaluate(
        """() => {
            if (typeof start_stop_scan === 'function') {
                start_stop_scan(0);
                return;
            }
            const btn = document.querySelector('#stop_button input[value="Stop"]');
            if (btn) btn.click();
        }"""
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def read_spectrum_status(gui_page) -> str:
    try:
        return (await gui_page.locator(SpectrumAnalyserLocators.STATUS_MSG).inner_text()).strip()
    except Exception:
        return ""


async def _read_html_table(gui_page, table_selector: str, name: str) -> SpectrumTable:
    data = await gui_page.evaluate(
        """(sel) => {
            const tbl = document.querySelector(sel);
            if (!tbl) return { headers: [], rows: [] };
            const headers = Array.from(tbl.querySelectorAll('tr.cbi-section-table-titles th, tr.cbi-section-table-titles td'))
                .map(el => (el.innerText || '').trim())
                .filter(Boolean);
            const rows = [];
            tbl.querySelectorAll('tr.cbi-section-table-row').forEach(tr => {
                const cells = Array.from(tr.querySelectorAll('td'))
                    .map(td => (td.innerText || '').replace(/\\s+/g, ' ').trim());
                if (!cells.length) return;
                const joined = cells.join(' ').toLowerCase();
                if (joined.includes('collecting data') || joined.includes('no entries')) return;
                // Skip placeholder colspan-only rows
                if (cells.length === 1 && !/\\d/.test(cells[0])) return;
                rows.push(cells);
            });
            return { headers, rows };
        }""",
        table_selector,
    )
    return SpectrumTable(
        name=name,
        headers=list(data.get("headers") or []),
        rows=[list(r) for r in (data.get("rows") or [])],
    )


async def read_spectrum_report(gui_page) -> SpectrumReport:
    start, end = await read_spectrum_freq_range(gui_page)
    status = await read_spectrum_status(gui_page)
    survey = await _read_html_table(
        gui_page, SpectrumAnalyserLocators.SURVEY_TABLE, "Wi-Fi Channel Analyzer"
    )
    channel = await _read_html_table(
        gui_page, SpectrumAnalyserLocators.CHANNEL_TABLE, "Channel Scan Results"
    )
    interference = await _read_html_table(
        gui_page, SpectrumAnalyserLocators.INTERFERENCE_TABLE, "Interference Spectrum"
    )
    return SpectrumReport(
        start_freq=start,
        end_freq=end,
        status_msg=status,
        survey=survey,
        channel=channel,
        interference=interference,
    )


async def wait_spectrum_results(
    gui_page,
    *,
    timeout_s: float = 240,
    poll_s: float = 5,
) -> SpectrumReport:
    """Poll until at least one results table has data, or timeout."""
    deadline = time.monotonic() + max(10.0, float(timeout_s))
    last = SpectrumReport()
    while time.monotonic() < deadline:
        last = await read_spectrum_report(gui_page)
        if last.has_results:
            # Prefer channel or interference metrics for sheet expected fields.
            if last.channel.data_row_count or last.interference.data_row_count:
                return last
            if last.survey.data_row_count:
                return last
        status = (last.status_msg or "").lower()
        if status and "progress" not in status and not _COLLECTING_RE.search(status):
            # Scan stopped — give one more settle for table fill.
            await asyncio.sleep(min(3.0, poll_s))
            last = await read_spectrum_report(gui_page)
            if last.has_results:
                return last
        await asyncio.sleep(max(1.0, float(poll_s)))
    return last


async def read_ssh_interference_csv(ssh, *, radio_idx: int = 1, max_lines: int = 0) -> list[str]:
    """Read interference CSV produced by spectrum scan (full file unless max_lines>0)."""
    await ensure_sanity_ssh_open(ssh)
    path = f"/tmp/interference_log_wifi{radio_idx}.csv"
    cmd = f"test -f {path} && wc -l < {path}; test -f {path} && cat {path}"
    if max_lines and max_lines > 0:
        cmd = f"test -f {path} && wc -l < {path}; test -f {path} && head -n {int(max_lines)} {path}"
    response = await ssh.send_command(cmd, timeout_ops=60)
    text = str(response.result or "").replace("\r", "")
    lines: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("root@"):
            continue
        lines.append(s)
    return lines


def print_spectrum_table_full(table: SpectrumTable) -> None:
    """Print every row of a spectrum results table to the terminal."""
    print(f"\n=== {table.name} ({table.data_row_count} rows) ===", flush=True)
    if table.headers:
        print("  | " + " | ".join(table.headers) + " |", flush=True)
        print("  |-" + "-|-".join("-" * max(4, len(h)) for h in table.headers) + "-|", flush=True)
    if not table.rows:
        print("  (no data rows)", flush=True)
        return
    for i, row in enumerate(table.rows, start=1):
        # Pad/truncate cells to header width when possible
        cells = list(row)
        if table.headers and len(cells) < len(table.headers):
            cells.extend([""] * (len(table.headers) - len(cells)))
        print(f"  {i:>4}. " + " | ".join(cells), flush=True)


def print_spectrum_report_full(report: SpectrumReport, *, label: str = "") -> None:
    title = f"SPECTRUM REPORT{' — ' + label if label else ''}"
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}", flush=True)
    print(
        f"  Freq range : {report.start_freq or '—'} – {report.end_freq or '—'} MHz\n"
        f"  Status     : {report.status_msg or '(idle)'}\n"
        f"  Survey     : {report.survey.data_row_count} rows\n"
        f"  Channel    : {report.channel.data_row_count} rows\n"
        f"  Interference: {report.interference.data_row_count} rows",
        flush=True,
    )
    print_spectrum_table_full(report.survey)
    print_spectrum_table_full(report.channel)
    print_spectrum_table_full(report.interference)
    if report.ssh_intf_lines:
        print(
            f"\n=== Interference CSV (SSH) — {len(report.ssh_intf_lines)} lines ===",
            flush=True,
        )
        for ln in report.ssh_intf_lines:
            print(f"  {ln}", flush=True)
