"""Channel-connectivity HTML report, using the throughput matrix theme."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from traffic.operating_rate_table import lookup_spec, normalize_bandwidth
from utils.grafana_matrix_report import (
    SENAO_LOGO_URL,
    _REPORT_CSS,
    _bw_chip,
    _bw_tone,
    _device_chip,
    _status_pill,
)

DFS_CHANNELS = set(range(100, 145))
_LOSS_RE = re.compile(r"(\d+(?:\.\d+)?)%\s+packet loss")

_KEY_RE = re.compile(r"(Encryption key:)(\S+)", re.IGNORECASE)

_EXTRA_CSS = """
    .ov-card.tone-ok::before { background: #5fad87; }
    th.heat-mcs.dfs { color: var(--warn); }
    pre.iw-log {
      margin: 0;
      padding: 14px 16px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #f7f9fc;
      color: #334155;
      font-family: ui-monospace, "Cascadia Code", "SF Mono", monospace;
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
      overflow: auto;
    }
    details.iw-entry {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
    }
    details.iw-entry summary {
      cursor: pointer;
      padding: 12px 14px;
      font-size: 13px;
      font-weight: 600;
      color: var(--ink);
    }
    details.iw-entry pre.iw-log {
      margin: 0 12px 12px;
    }
    .cell-rate-note { margin-top: 8px; font-size: 13px; color: var(--warn); }
    .heat-warn { background: var(--warn-soft); border-color: var(--warn-line); color: var(--warn); }
"""


_BITRATE_RE = re.compile(
    r"Bit\s*Rate[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*([GMK])?b/s",
    re.IGNORECASE,
)
# Dual MCS23 sheet rates. HT160 is the air rate iwconfig shows as about 2.402 Gb/s.
_MAX_LINK_MBPS = {
    "HT20": 286.0,
    "HT40": 573.0,
    "HT80": 1201.0,
    "HT160": 2402.0,
}


def iwconfig_bitrate_mbps(text: str) -> float | None:
    match = _BITRATE_RE.search(text or "")
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "M").upper()
    if unit == "G":
        return value * 1000.0
    if unit == "K":
        return value / 1000.0
    return value


def max_link_rate_mbps(bandwidth: str) -> float | None:
    try:
        bw = normalize_bandwidth(bandwidth)
    except ValueError:
        return _MAX_LINK_MBPS.get(str(bandwidth or "").upper().replace("+", ""))
    return float(lookup_spec("MCS23", bw, spatial_streams=2)["operating_rate_mbps"])


def _rate_close(observed: float, expected: float) -> bool:
    return abs(observed - expected) <= max(25.0, expected * 0.08)


def inferred_link_bandwidth(mbps: float) -> str | None:
    best_bw = ""
    best_delta: float | None = None
    for bandwidth, expected in _MAX_LINK_MBPS.items():
        delta = abs(mbps - expected)
        if best_delta is None or delta < best_delta:
            best_bw = bandwidth
            best_delta = delta
    if best_bw and _rate_close(mbps, _MAX_LINK_MBPS[best_bw]):
        return best_bw
    return None


def link_rate_check(row: dict[str, str]) -> dict[str, str]:
    """Compare iwconfig Bit Rate with the max dual-stream rate for this bandwidth."""
    expected = max_link_rate_mbps(row.get("bandwidth") or "")
    observed = iwconfig_bitrate_mbps(row.get("iwconfig") or "")
    if expected is None or observed is None:
        return {"status": "unknown", "label": "—", "note": ""}
    expected_label = f"{expected:.0f} Mbps"
    observed_label = f"{observed:.0f} Mbps"
    if _rate_close(observed, expected):
        return {"status": "match", "label": observed_label, "note": f"max {expected_label}"}
    try:
        requested = normalize_bandwidth(row.get("bandwidth") or "")
    except ValueError:
        requested = ""
    wider = inferred_link_bandwidth(observed)
    note = f"iwconfig {observed_label}, {row.get('bandwidth')} max is {expected_label}"
    if wider and wider != requested:
        note += f". Rate matches {wider}"
    return {"status": "mismatch", "label": observed_label, "note": note}


def _passed(row: dict[str, str]) -> bool:
    return row.get("config_ok") == "yes" and row.get("ping_ok") == "yes"


def _frequency_ghz(channel: int) -> str:
    mhz = 5000 + int(channel) * 5
    return f"{mhz / 1000:.3f}".rstrip("0").rstrip(".")


def _loss_value(detail: str) -> float | None:
    match = _LOSS_RE.search(detail or "")
    if not match:
        return None
    return float(match.group(1))


def _loss_label(detail: str) -> str:
    value = _loss_value(detail)
    if value is None:
        return "—"
    if value == int(value):
        return f"{int(value)}%"
    return f"{value:.1f}%"


def _cell_key(bandwidth: str, channel: int) -> str:
    return f"{bandwidth}|{channel}"


def _public_iwconfig(text: str) -> str:
    """Keep the iwconfig text and hide a configured encryption key."""
    raw = (text or "").strip()
    if not raw:
        return ""

    def _hide(match: re.Match[str]) -> str:
        value = match.group(2)
        if value.lower() in {"off", "on"}:
            return match.group(0)
        return f"{match.group(1)}[redacted]"

    return _KEY_RE.sub(_hide, raw)


def write_channel_connectivity_report(
    rows: list[dict[str, str]],
    path: Path,
    *,
    bts_ip: str,
    cpe_ip: str,
    country: str,
    channels_by_bw: dict[str, list[int]],
    executed_at: str | None = None,
    report_id: str | None = None,
) -> Path:
    """Write a throughput-style HTML report. Untested channels stay empty."""
    when = executed_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stamp = report_id or Path(path).stem
    tested = len(rows)
    passed = sum(1 for row in rows if _passed(row))
    failed = tested - passed
    rate_warn = sum(
        1 for row in rows if _passed(row) and link_rate_check(row)["status"] == "mismatch"
    )
    planned = sum(len(items) for items in channels_by_bw.values())
    rate_sub = (
        f"{rate_warn} linked at the wrong iwconfig rate"
        if rate_warn
        else "iwconfig rate matches the bandwidth"
    )
    if tested and failed == 0 and rate_warn == 0:
        pass_cls = "ok"
    elif passed == 0:
        pass_cls = "bad"
    else:
        pass_cls = "warn"
    by_key = {(row["bandwidth"], str(row["channel"])): row for row in rows}

    header_channels: list[int] = []
    seen: set[int] = set()
    for channels in channels_by_bw.values():
        for channel in channels:
            if channel not in seen:
                seen.add(channel)
                header_channels.append(channel)

    header_html = "".join(
        f"<th class='heat-mcs{' dfs' if channel in DFS_CHANNELS else ''}' "
        f"title='{'DFS ' if channel in DFS_CHANNELS else ''}{_frequency_ghz(channel)} GHz'>"
        f"{channel}</th>"
        for channel in header_channels
    )

    heat_rows: list[str] = []
    catalog: dict[str, dict[str, str]] = {}
    for bandwidth, channels in channels_by_bw.items():
        present = set(channels)
        cells: list[str] = []
        for channel in header_channels:
            if channel not in present:
                cells.append("<td><span class='heat-cell heat-empty'>—</span></td>")
                continue
            row = by_key.get((bandwidth, str(channel)))
            if row is None:
                cells.append("<td><span class='heat-cell heat-empty'>—</span></td>")
                continue
            ok = _passed(row)
            rate = link_rate_check(row)
            if not ok:
                status = "fail"
                result = "Fail"
            elif rate["status"] == "mismatch":
                status = "warn"
                result = "Rate"
            else:
                status = "pass"
                result = "Pass"
            key = _cell_key(bandwidth, channel)
            loss = _loss_label(row.get("ping_detail") or "")
            cell_text = "✓" if status == "warn" else loss
            title = (
                f"{bandwidth} {channel} · {_frequency_ghz(channel)} GHz · "
                f"{result} · {row.get('ping_detail') or '—'}"
            )
            if rate["note"]:
                title += f" · {rate['note']}"
            cells.append(
                f"<td><button type='button' class='heat-cell heat-{status}' "
                f"data-key='{escape(key)}' title='{escape(title)}'>"
                f"<span class='heat-rx'>{escape(cell_text)}</span>"
                f"</button></td>"
            )
            catalog[key] = {
                "bandwidth": bandwidth,
                "channel": str(channel),
                "frequency": f"{_frequency_ghz(channel)} GHz",
                "htmode": row.get("htmode_uci") or "—",
                "active": row.get("active_channel") or "—",
                "ping": "up" if row.get("ping_ok") == "yes" else "down",
                "detail": row.get("ping_detail") or "—",
                "result": result,
                "rate": rate["label"],
                "rate_note": rate["note"],
                "iwconfig": _public_iwconfig(row.get("iwconfig") or "") or "(no iwconfig output)",
            }
        heat_rows.append(f"<tr><th class='heat-bw'>{escape(bandwidth)}</th>{''.join(cells)}</tr>")

    log_rows: list[str] = []
    previous = None
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bandwidth"]] = counts.get(row["bandwidth"], 0) + 1
    for row in rows:
        bandwidth = row["bandwidth"]
        if bandwidth != previous:
            n = counts[bandwidth]
            tone = _bw_tone(bandwidth)
            log_rows.append(
                f"<tr class='bw-group-sep bw-tone-{tone}'>"
                f"<td colspan='9'>{_bw_chip(bandwidth)}"
                f"<span class='bw-group-meta'>{n} channel{'s' if n != 1 else ''}</span></td></tr>"
            )
            previous = bandwidth
        ok = _passed(row)
        rate = link_rate_check(row)
        channel = int(row["channel"])
        tone = _bw_tone(bandwidth)
        if not ok:
            row_cls = "row-fail"
            pill = _status_pill("FAIL")
        elif rate["status"] == "mismatch":
            row_cls = "row-skip"
            pill = "<span class='pill skip'>Rate</span>"
        else:
            row_cls = "row-pass"
            pill = _status_pill("PASS")
        log_rows.append(
            f"<tr class='{row_cls} bw-tone-{tone}'>"
            f"<td>{_bw_chip(bandwidth)}</td>"
            f"<td class='mono'>{channel}</td>"
            f"<td>{_frequency_ghz(channel)} GHz</td>"
            f"<td>{escape(row.get('htmode_uci') or '—')}</td>"
            f"<td class='mono'>{escape(row.get('active_channel') or '—')}</td>"
            f"<td>{'up' if row.get('ping_ok') == 'yes' else 'down'}</td>"
            f"<td>{escape(row.get('ping_detail') or '—')}</td>"
            f"<td class='mono' title='{escape(rate['note'])}'>{escape(rate['label'])}</td>"
            f"<td>{pill}</td>"
            "</tr>"
        )

    default_key = next(iter(catalog), "")
    first = catalog.get(default_key, {})

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR Channel Connectivity #{escape(stamp)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap" rel="stylesheet"/>
  <style>
"""
    page += _REPORT_CSS
    page += _EXTRA_CSS
    page += f"""
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-shell">
      <div class="hero-top">
        <div>
          <div class="hero-brand">
            <div class="hero-logo">
              <img src="{SENAO_LOGO_URL}" alt="Senao Networks"/>
            </div>
            <div>
              <div class="hero-eyebrow">Channel connectivity report</div>
              <h1>Channel Sweep #{escape(stamp)}</h1>
            </div>
          </div>
          <div class="hero-meta">
            <span class="meta-chip">country {escape(country)}</span>
            <span class="meta-chip">BTS {escape(bts_ip)}</span>
            <span class="meta-chip">SU {escape(cpe_ip)}</span>
            <span class="meta-chip">1 SU</span>
          </div>
        </div>
        <div class="hero-status">
          <div class="hero-badge {pass_cls}">{passed}/{tested} passed</div>
          <div class="hero-time">{escape(when)}</div>
        </div>
      </div>
      <div class="overview-strip">
        <article class="ov-card tone-{pass_cls}">
          <div class="ov-label">Result</div>
          <div class="ov-value">{passed}<span>/{tested}</span></div>
          <div class="ov-sub">{rate_sub}</div>
        </article>
        <article class="ov-card">
          <div class="ov-label">Failed</div>
          <div class="ov-value">{failed}<span>ch</span></div>
          <div class="ov-sub">ping down or channel mismatch</div>
        </article>
        <article class="ov-card">
          <div class="ov-label">Plan</div>
          <div class="ov-value">{planned}<span>ch</span></div>
          <div class="ov-sub">{tested} tested · {planned - tested} not run</div>
        </article>
      </div>
    </div>
  </header>

  <main class="grid">
    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Testbed summary</h2>
          <p class="panel-desc">BTS and the SU that was pinged from the lab host.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table testbed-table">
          <thead>
            <tr><th>Unit</th><th>Role</th><th>Mgmt IP</th></tr>
          </thead>
          <tbody>
            <tr class="row-bts">
              <td><strong>{_device_chip("BTS", is_bts=True)}</strong></td>
              <td>Access point</td>
              <td class="mono">{escape(bts_ip)}</td>
            </tr>
            <tr>
              <td><strong>{_device_chip("SU1")}</strong></td>
              <td>Station</td>
              <td class="mono">{escape(cpe_ip)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Coverage matrix</h2>
          <p class="panel-desc">Bandwidth by channel. A yellow cell is linked, but the iwconfig bit rate is not the max rate for that bandwidth. Amber column headers are DFS.</p>
        </div>
      </div>
      <div class="table-scroll heat-scroll" id="coverageMatrix">
        <table class="heat-table">
          <thead>
            <tr><th class="heat-corner">BW \\ Channel</th>{header_html}</tr>
          </thead>
          <tbody>
            {''.join(heat_rows)}
          </tbody>
        </table>
      </div>
      <div class="cov-legend">
        <span><i class="lg pass"></i> Pass</span>
        <span><i class="lg skip"></i> Link rate</span>
        <span><i class="lg fail"></i> Fail</span>
        <span><i class="lg empty"></i> Not tested</span>
        <span class="legend-note">Green cell = packet loss · yellow tick = linked, iwconfig rate is not the max · click a cell for ping and iwconfig</span>
      </div>
    </section>

    <section class="panel span-12" id="chartSection">
      <div class="panel-head">
        <div>
          <h2>Selected channel</h2>
          <p class="panel-desc" id="seriesMeta">{escape(first.get("bandwidth", "—"))} {escape(first.get("channel", ""))} · {escape(first.get("detail", "Click a heatmap cell."))}</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>Bandwidth</th><th>Channel</th><th>Frequency</th><th>HT mode</th>
              <th>Active</th><th>SU ping</th><th>Link rate</th><th>Result</th>
            </tr>
          </thead>
          <tbody id="cellDetail">
            <tr>
              <td id="dBw">{escape(first.get("bandwidth", "—"))}</td>
              <td id="dCh" class="mono">{escape(first.get("channel", "—"))}</td>
              <td id="dFreq">{escape(first.get("frequency", "—"))}</td>
              <td id="dHt">{escape(first.get("htmode", "—"))}</td>
              <td id="dActive" class="mono">{escape(first.get("active", "—"))}</td>
              <td id="dPing">{escape(first.get("ping", "—"))}</td>
              <td id="dRate" class="mono">{escape(first.get("rate", "—"))}</td>
              <td id="dResult">{escape(first.get("result", "—"))}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="cell-rate-note" id="dRateNote">{escape(first.get("rate_note", ""))}</p>
      <details class="iw-entry">
        <summary id="iwconfigSummary">iwconfig ath1 · {escape(first.get("bandwidth", ""))} {escape(first.get("channel", ""))}</summary>
        <pre class="iw-log" id="iwconfigLog">{escape(first.get("iwconfig", "(no iwconfig output)"))}</pre>
      </details>
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Channel log</h2>
          <p class="panel-desc">Every tested channel, grouped by bandwidth, with the SU ping result.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table" id="iterLog">
          <thead>
            <tr>
              <th>BW</th><th>Channel</th><th>Frequency</th><th>HT mode</th>
              <th>Active</th><th>SU ping</th><th>Ping detail</th><th>Link rate</th><th>Status</th>
            </tr>
          </thead>
          <tbody>{''.join(log_rows)}</tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const cells = {json.dumps(catalog)};
    const meta = document.getElementById("seriesMeta");
    const iwconfigLog = document.getElementById("iwconfigLog");
    function show(key) {{
      const cell = cells[key];
      if (!cell) return;
      meta.textContent = cell.bandwidth + " " + cell.channel + " · " + cell.detail;
      document.getElementById("dBw").textContent = cell.bandwidth;
      document.getElementById("dCh").textContent = cell.channel;
      document.getElementById("dFreq").textContent = cell.frequency;
      document.getElementById("dHt").textContent = cell.htmode;
      document.getElementById("dActive").textContent = cell.active;
      document.getElementById("dPing").textContent = cell.ping;
      document.getElementById("dRate").textContent = cell.rate;
      document.getElementById("dResult").textContent = cell.result;
      document.getElementById("dRateNote").textContent = cell.rate_note || "";
      if (cell.rate_note) meta.textContent += " · " + cell.rate_note;
      iwconfigLog.textContent = cell.iwconfig;
      document.getElementById("iwconfigSummary").textContent = "iwconfig ath1 · " + cell.bandwidth + " " + cell.channel;
      document.querySelectorAll(".heat-cell.selected").forEach((node) => node.classList.remove("selected"));
      const button = document.querySelector(".heat-cell[data-key='" + key + "']");
      if (button) button.classList.add("selected");
    }}
    document.querySelectorAll(".heat-cell[data-key]").forEach((button) => {{
      button.addEventListener("click", () => show(button.dataset.key));
    }});
  </script>
</body>
</html>
"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
