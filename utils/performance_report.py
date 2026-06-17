"""Performance matrix HTML/CSV reports — same layout family as regression reports."""

from __future__ import annotations

import csv
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from traffic.operating_rate_table import lookup_spec
from utils.regression_report import _render_testbed_summary_table

SENAO_LOGO_URL = (
    "https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png"
)


def _rate_actual_cell(actual: float | None, ok: bool | None) -> str:
    """SNMP operating rate (Mbps) — green when matches spec data rate, red when not."""
    if actual is None:
        return "—"
    text = f"{actual:.0f}"
    if ok is True:
        return f"<span class='mark pass'>{text}</span>"
    if ok is False:
        return f"<span class='mark fail'>{text}</span>"
    return escape(text)


def _parse_dl_ul_fractions(ratio: str) -> tuple[float, float]:
    try:
        dl_part, ul_part = str(ratio).split(":")
        total = float(dl_part) + float(ul_part)
        if total <= 0:
            return 0.5, 0.5
        return float(dl_part) / total, float(ul_part) / total
    except (TypeError, ValueError):
        return 0.5, 0.5


def _throughput_pct_of_rate(measured: float, target_mbps: float) -> float:
    if target_mbps <= 0:
        return 0.0
    return (measured / target_mbps) * 100.0


def _direction_targets(data_rate_mbps: float, ratio: str) -> tuple[float, float, float]:
    """Split spec data rate by DL:UL ratio for per-direction throughput coloring."""
    dl_frac, ul_frac = _parse_dl_ul_fractions(ratio)
    dl_target = data_rate_mbps * dl_frac
    ul_target = data_rate_mbps * ul_frac
    return dl_target, ul_target, data_rate_mbps


def _resolve_row_spec(record: dict[str, Any]) -> tuple[dict[str, Any], float]:
    link = record.get("link_validation") or {}
    spec = link.get("spec") or {}
    expected = float(
        link.get("expected_operating_rate_mbps") or spec.get("operating_rate_mbps") or 0
    )
    if expected > 0 and spec:
        return spec, expected
    bandwidth = str(record.get("bandwidth") or "")
    mcs = str(record.get("mcs") or "")
    if not bandwidth or not mcs:
        return spec, expected
    try:
        spec = lookup_spec(mcs, bandwidth, spatial_streams=2)
        return spec, float(spec.get("operating_rate_mbps") or 0)
    except (TypeError, ValueError):
        return {}, 0.0


def _throughput_cell(measured: float, target_mbps: float) -> str:
    """Color throughput vs direction target (DL/UL share of data rate); zero is flagged."""
    if measured <= 0:
        return "<span class='tput-zero'>0.0</span> <span class='muted'>(no traffic)</span>"
    pct = _throughput_pct_of_rate(measured, target_mbps)
    if pct >= 70:
        css = "tput-good"
    elif pct >= 50:
        css = "tput-warn"
    else:
        css = "tput-bad"
    return f"<span class='{css}'>{measured:.1f}</span><span class='muted'> ({pct:.0f}%)</span>"


def _cpe_label(client: dict[str, Any], index: int) -> str:
    ip = str(client.get("ip") or "").strip()
    if ip and ip != "0.0.0.0":
        return ip
    return f"SU{index}"


def _trex_device_stats(stats: dict[str, Any], su_index: int) -> dict[str, Any]:
    trex = stats.get("trex") or {}
    by_device = trex.get("summary_by_device") or {}
    return by_device.get(f"SU{su_index}") or {}


def _render_cpe_details_row(record: dict[str, Any], *, colspan: int = 15) -> str:
    link = record.get("link_validation") or {}
    clients = link.get("clients") or []
    if not clients:
        return ""

    stats = record.get("stats") or {}
    cpe_rows: list[str] = []
    for index, client in enumerate(clients, start=1):
        trex_dev = _trex_device_stats(stats, index)
        trex_rx = trex_dev.get("avg_rx_mbps")
        trex_rx_cell = f"{float(trex_rx):.1f}" if trex_rx is not None else "—"
        cpe_rows.append(
            f"""
          <tr>
            <td>{escape(_cpe_label(client, index))}</td>
            <td>{_rate_actual_cell(client.get('tx_rate_mbps'), client.get('tx_rate_ok'))}</td>
            <td>{_rate_actual_cell(client.get('rx_rate_mbps'), client.get('rx_rate_ok'))}</td>
            <td>{escape(str(client.get('l_snr1', '—')))}</td>
            <td>{escape(str(client.get('l_snr2', '—')))}</td>
            <td>{escape(str(client.get('r_snr1', '—')))}</td>
            <td>{escape(str(client.get('r_snr2', '—')))}</td>
            <td>{trex_rx_cell}</td>
          </tr>
            """
        )

    return f"""
        <tr class="cpe-detail-row">
          <td colspan="{colspan}">
            <div class="cpe-detail-wrap">
              <div class="cpe-detail-title">Per-SU link &amp; TRex throughput</div>
              <table class="cpe-sheet">
                <thead>
                  <tr>
                    <th>CPE / SU</th>
                    <th>Tx Rate<br/><span class="muted">(Mbps)</span></th>
                    <th>Rx Rate<br/><span class="muted">(Mbps)</span></th>
                    <th colspan="2">Local SNR (dB)</th>
                    <th colspan="2">Remote SNR (dB)</th>
                    <th>TRex Avg RX<br/><span class="muted">(Mbps)</span></th>
                  </tr>
                  <tr>
                    <th></th><th></th><th></th>
                    <th>A1</th><th>A2</th><th>A1</th><th>A2</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(cpe_rows)}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
        """


def _render_result_row(record: dict[str, Any]) -> str:
    stats = record.get("stats") or {}
    link = record.get("link_validation") or {}
    spec, expected_rate = _resolve_row_spec(record)
    clients = link.get("clients") or []
    primary = clients[0] if clients else {}
    ratio = str(record.get("ratio") or "50:50")
    dl_target, ul_target, bidi_target = _direction_targets(expected_rate, ratio)

    combined = stats.get("combined") or {}
    downlink = stats.get("downlink") or {}
    uplink = stats.get("uplink") or {}
    ul_mbps = float(uplink.get("rx_mbps") or 0)
    dl_mbps = float(downlink.get("rx_mbps") or 0)
    bidi_mbps = float(combined.get("rx_mbps") or 0)

    row_class = ""
    if link.get("operating_rate_mismatch"):
        row_class = "rate-mismatch"
    error_text = str(record.get("error") or "")
    if error_text:
        row_class = "rate-mismatch" if row_class else "run-error"

    configured_mcs = str(record.get("mcs") or spec.get("mcs") or "—")
    modulation = spec.get("modulation") or "—"
    if configured_mcs != "—" and modulation != "—":
        modulation_cell = f"{escape(configured_mcs)}<br/><span class='muted'>{escape(modulation)}</span>"
    else:
        modulation_cell = escape(modulation if modulation != "—" else configured_mcs)
    if expected_rate > 0:
        if link.get("operating_rate_mismatch"):
            data_rate_cell = (
                f"<span class='mark fail'>{expected_rate:.0f}</span>"
                f"<br/><span class='muted'>data rate mismatch</span>"
            )
        else:
            data_rate_cell = f"{expected_rate:.0f}"
    else:
        data_rate_cell = "—"

    error_note = ""
    if error_text:
        short = escape(error_text if len(error_text) <= 120 else error_text[:117] + "…")
        error_note = f"<br/><span class='muted'>{short}</span>"

    if len(clients) > 1:
        link_metric_cell = "<span class='muted'>per SU ↓</span>"
    else:
        link_metric_cell = _rate_actual_cell(primary.get("tx_rate_mbps"), primary.get("tx_rate_ok"))

    if len(clients) > 1:
        rx_metric_cell = "<span class='muted'>per SU ↓</span>"
    else:
        rx_metric_cell = _rate_actual_cell(primary.get("rx_rate_mbps"), primary.get("rx_rate_ok"))

    if len(clients) > 1:
        snr_cells = ("<span class='muted'>per SU ↓</span>",) * 4
    else:
        snr_cells = (
            escape(str(primary.get("l_snr1", "—"))),
            escape(str(primary.get("l_snr2", "—"))),
            escape(str(primary.get("r_snr1", "—"))),
            escape(str(primary.get("r_snr2", "—"))),
        )

    summary_row = f"""
        <tr class="{row_class}">
          <td>{escape(str(record.get('bandwidth', '—')))}</td>
          <td>{escape(str(record.get('mcs', '—')))}</td>
          <td>{escape(str(record.get('ratio', '—')))}</td>
          <td>{escape(str(link.get('connected_cpe_count', '—')))}</td>
          <td>{modulation_cell}</td>
          <td>{data_rate_cell}</td>
          <td>{link_metric_cell}</td>
          <td>{rx_metric_cell}</td>
          <td>{_throughput_cell(dl_mbps, dl_target)}{error_note}</td>
          <td>{_throughput_cell(ul_mbps, ul_target)}</td>
          <td>{_throughput_cell(bidi_mbps, bidi_target)}</td>
          <td>{snr_cells[0]}</td>
          <td>{snr_cells[1]}</td>
          <td>{snr_cells[2]}</td>
          <td>{snr_cells[3]}</td>
          <td>{escape(str(record.get('noise_dbm', '—')))}</td>
        </tr>
        """
    return summary_row + _render_cpe_details_row(record)


def _render_results_table(records: list[dict[str, Any]]) -> str:
    rows = [_render_result_row(record) for record in records]
    if not rows:
        rows = [
            "<tr><td colspan='15'>No performance iterations recorded.</td></tr>",
        ]
    return f"""
    <div class="sheet-scroll">
    <table class="sheet">
      <thead>
        <tr>
          <th rowspan="2">Bandwidth</th>
          <th rowspan="2">MCS</th>
          <th rowspan="2">DL:UL</th>
          <th rowspan="2">Connected CPE</th>
          <th rowspan="2">Modulation</th>
          <th rowspan="2">Data Rate<br/><span class="muted">(Mbps)</span></th>
          <th colspan="2">Rate (Mbps)</th>
          <th colspan="3">Throughput (Mbps)<br/><span class="muted">% of DL/UL share</span></th>
          <th colspan="2">Local SNR (dB)</th>
          <th colspan="2">Remote SNR (dB)</th>
          <th rowspan="2">Noise<br/>(dBm)</th>
        </tr>
        <tr>
          <th>Tx</th><th>Rx</th>
          <th>DL</th><th>UL</th><th>Bi-Di</th>
          <th>A1</th><th>A2</th><th>A1</th><th>A2</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    </div>
    """


def write_summary_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "bandwidth",
        "mcs",
        "mode",
        "ratio",
        "connected_cpe_count",
        "expected_operating_rate_mbps",
        "actual_tx_rate_mbps",
        "actual_rx_rate_mbps",
        "operating_rate_ok",
        "combined_rx_mbps",
        "downlink_rx_mbps",
        "uplink_rx_mbps",
        "throughput_pct_of_data_rate",
        "error",
        "artifact",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            stats = record.get("stats") or {}
            combined = stats.get("combined") or {}
            downlink = stats.get("downlink") or {}
            uplink = stats.get("uplink") or {}
            _, expected = _resolve_row_spec(record)
            link = record.get("link_validation") or {}
            primary = (link.get("clients") or [{}])[0]
            ratio = str(record.get("ratio") or "50:50")
            dl_target, ul_target, bidi_target = _direction_targets(expected, ratio)
            bidi = float(combined.get("rx_mbps") or 0)
            writer.writerow(
                {
                    "bandwidth": record.get("bandwidth"),
                    "mcs": record.get("mcs"),
                    "mode": record.get("mode"),
                    "ratio": record.get("ratio"),
                    "connected_cpe_count": link.get("connected_cpe_count"),
                    "expected_operating_rate_mbps": expected,
                    "actual_tx_rate_mbps": primary.get("tx_rate_mbps"),
                    "actual_rx_rate_mbps": primary.get("rx_rate_mbps"),
                    "operating_rate_ok": link.get("operating_rate_ok"),
                    "combined_rx_mbps": bidi,
                    "downlink_rx_mbps": downlink.get("rx_mbps", 0),
                    "uplink_rx_mbps": uplink.get("rx_mbps", 0),
                    "throughput_pct_of_data_rate": round(
                        _throughput_pct_of_rate(bidi, bidi_target), 1
                    )
                    if bidi_target > 0
                    else "",
                    "error": record.get("error", ""),
                    "artifact": record.get("artifact", ""),
                }
            )


def _render_run_outcome_banner(records: list[dict[str, Any]]) -> str:
    total = len(records)
    if total == 0:
        return (
            '<div class="run-outcome warn">'
            "<strong>No iterations recorded.</strong> Check Jenkins console for early failures."
            "</div>"
        )
    passed = sum(1 for row in records if row.get("passed"))
    failed = total - passed
    trex_errors = [
        str(row.get("error") or "")
        for row in records
        if row.get("error") and "TRex" in str(row.get("error"))
    ]
    if passed == total:
        css = "pass"
        headline = f"<strong>{passed}/{total} passed</strong> — all throughput iterations completed."
    elif passed == 0:
        css = "fail"
        headline = f"<strong>0/{total} passed</strong> — no throughput data captured."
    else:
        css = "warn"
        headline = f"<strong>{passed}/{total} passed</strong>, {failed} failed or skipped."
    detail = ""
    if trex_errors:
        sample = escape(trex_errors[0][:220])
        if len(trex_errors[0]) > 220:
            sample += "…"
        detail = f"<br/><span class='muted'>TRex: {sample}</span>"
    return f'<div class="run-outcome {css}">{headline}{detail}</div>'


def write_html_report(
    *,
    records: list[dict[str, Any]],
    run_meta: dict[str, Any],
    path: Path,
    testbed_summary: dict[str, Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    executed_at = escape(str(run_meta.get("executed_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    testbed_table = _render_testbed_summary_table(testbed_summary or {})
    outcome_banner = _render_run_outcome_banner(records)
    results_table = _render_results_table(records)

    meta_lines = []
    for key in (
        "Bandwidths",
        "MCS Rates",
        "Ratios",
        "Duration (s)",
        "TRex client command",
    ):
        if key in run_meta:
            value = run_meta[key]
            if key == "TRex client command":
                meta_lines.append(
                    f"<span class='trex-cmd'><strong>{escape(key)}:</strong> "
                    f"<code>{escape(str(value))}</code></span>"
                )
            else:
                meta_lines.append(f"<span><strong>{escape(key)}:</strong> {escape(value)}</span>")

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR Performance Report</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    :root {{
      --bg: #eef2f7; --card: #ffffff; --text: #334155; --title: #0f172a;
      --border: #cbd5e1; --pass: #16a34a; --fail: #dc2626; --warn: #ea580c; --head: #1e3a8a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 28px 18px; font-family: 'Inter', sans-serif;
      background: var(--bg); color: var(--text);
    }}
    .wrap {{ width: min(1600px, 98vw); max-width: 100%; margin: 0 auto; }}
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #2563eb 100%);
      color: #fff; border-radius: 14px; padding: 22px 26px; margin-bottom: 18px;
      display: flex; justify-content: space-between; align-items: center; gap: 20px;
    }}
    .hero-main {{ flex: 1; min-width: 0; }}
    .hero h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .hero-date {{ margin: 0; font-size: 14px; opacity: 0.92; font-weight: 500; }}
    .hero-logo {{
      flex-shrink: 0; background: #fff; border-radius: 10px; padding: 10px 14px;
      box-shadow: 0 2px 8px rgba(15,23,42,0.15);
    }}
    .hero-logo img {{ display: block; height: 42px; width: auto; }}
    .panel-top {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px; margin-bottom: 16px;
    }}
    .panel-top h2 {{ margin: 0 0 14px; font-size: 17px; color: var(--title); }}
    table.data-table {{
      width: 100%; border-collapse: collapse; background: #fff;
      border: 2px solid var(--border); border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(15,23,42,0.06); table-layout: fixed;
    }}
    table.data-table th, table.data-table td {{
      border: 1px solid var(--border); padding: 12px 16px; text-align: center;
      vertical-align: middle; word-break: break-word;
    }}
    table.data-table thead th {{
      background: #f8fafc; color: var(--head); font-size: 14px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.5px;
    }}
    table.data-table th.corner {{ background: #f1f5f9; width: 14%; }}
    table.data-table th.row-label {{
      text-align: left; background: #f8fafc; color: var(--title);
      font-size: 14px; font-weight: 600; padding-left: 16px; width: 14%;
    }}
    table.data-table td {{ background: #fff; }}
    table.summary-top {{ margin-bottom: 0; }}
    .run-meta {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px 14px; margin-top: 14px; font-size: 13px; color: #475569;
    }}
    .run-meta span {{
      padding: 10px 14px; background: #f8fafc; border-radius: 8px;
      border: 1px solid var(--border);
    }}
    .run-meta strong {{ color: var(--title); }}
    .run-outcome {{
      margin-top: 14px; padding: 12px 16px; border-radius: 8px; font-size: 14px;
      border: 1px solid var(--border); background: #f8fafc;
    }}
    .run-outcome.fail {{ background: #fff5f5; border-color: #fecaca; color: #991b1b; }}
    .run-outcome.pass {{ background: #f0fdf4; border-color: #bbf7d0; color: #166534; }}
    .run-outcome.warn {{ background: #fffbeb; border-color: #fde68a; color: #92400e; }}
    .panel {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px;
    }}
    .panel > h2 {{ margin: 0 0 16px; font-size: 17px; color: var(--title); }}
    .ip-cell {{ white-space: nowrap; font-family: Consolas, Monaco, monospace; font-size: 12px; }}
    table.matrix {{
      width: 100%; max-width: 100%; border-collapse: collapse; background: #fff;
      border: 2px solid var(--border); border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(15,23,42,0.06);
    }}
    table.matrix th, table.matrix td {{
      border: 1px solid var(--border); padding: 12px 16px; text-align: center;
    }}
    table.matrix thead th {{
      background: #f8fafc; color: var(--head); font-size: 13px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.5px;
    }}
    table.matrix th.corner {{ background: #f1f5f9; width: 100px; }}
    table.matrix th.row-label {{
      text-align: left; background: #f8fafc; color: var(--title);
      font-size: 13px; font-weight: 600; padding-left: 14px;
    }}
    .sheet-scroll {{ width: 100%; overflow-x: auto; }}
    table.sheet {{
      width: 100%; min-width: 100%; table-layout: auto; border-collapse: collapse;
      font-size: 13px; border: 2px solid var(--border); border-radius: 8px;
    }}
    table.sheet th, table.sheet td {{
      border: 1px solid var(--border); padding: 12px 10px; text-align: center;
      vertical-align: middle; word-wrap: break-word;
    }}
    table.sheet thead th {{
      background: #f8fafc; color: var(--head); font-weight: 700; font-size: 12px;
    }}
    table.sheet tbody tr:nth-child(even) td {{ background: #fafcff; }}
    table.sheet tbody tr.rate-mismatch td {{ background: #fff8f8; }}
    table.sheet tbody tr.run-error td {{ background: #fff5f5; }}
    table.sheet tbody tr.cpe-detail-row td {{
      background: #f8fafc; padding: 10px 14px 14px; text-align: left;
    }}
    .cpe-detail-wrap {{ width: 100%; }}
    .cpe-detail-title {{
      font-size: 12px; font-weight: 600; color: var(--head); margin: 0 0 8px;
    }}
    table.cpe-sheet {{
      width: 100%; border-collapse: collapse; font-size: 12px;
      border: 1px solid var(--border); background: #fff;
    }}
    table.cpe-sheet th, table.cpe-sheet td {{
      border: 1px solid var(--border); padding: 8px 10px; text-align: center;
    }}
    table.cpe-sheet thead th {{
      background: #eef2ff; color: var(--head); font-weight: 600; font-size: 11px;
    }}
    .muted {{ color: #64748b; font-size: 11px; }}
    .tput-good {{
      color: #166534; font-weight: 700; background: #dcfce7;
      padding: 2px 8px; border-radius: 4px;
    }}
    .tput-warn {{
      color: #9a3412; font-weight: 700; background: #ffedd5;
      padding: 2px 8px; border-radius: 4px;
    }}
    .tput-bad {{
      color: #991b1b; font-weight: 700; background: #fee2e2;
      padding: 2px 8px; border-radius: 4px;
    }}
    .tput-zero {{
      color: #991b1b; font-weight: 700; background: #fecaca;
      padding: 2px 8px; border-radius: 4px;
    }}
    .mark.pass {{ color: var(--pass); font-weight: 700; }}
    .mark.fail {{
      color: var(--fail); font-weight: 700; background: #fee2e2;
      padding: 2px 6px; border-radius: 4px;
    }}
    .footnote {{ margin-top: 14px; font-size: 12px; color: #64748b; line-height: 1.6; }}
    .trex-cmd {{
      display: block; margin-top: 8px; font-size: 12px; line-height: 1.5;
    }}
    .trex-cmd code {{
      display: block; margin-top: 4px; padding: 8px 10px; background: #f1f5f9;
      border-radius: 6px; font-family: Consolas, Monaco, monospace; font-size: 11px;
      white-space: pre-wrap; word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div class="hero-main">
        <h1>UBR Performance Report</h1>
        <p class="hero-date">{executed_at}</p>
      </div>
      <div class="hero-logo">
        <img src="{SENAO_LOGO_URL}" alt="Senao Networks"/>
      </div>
    </header>

    <section class="panel-top">
      <h2>Testbed Summary</h2>
      {testbed_table}
      <div class="run-meta">
        {''.join(meta_lines)}
      </div>
    </section>

    <section class="panel">
      <h2>Performance Results</h2>
      {outcome_banner}
      {results_table}
      <p class="footnote">
        <strong>Throughput</strong> % uses each direction&apos;s share of spec data rate
        (e.g. 75:25 → DL vs 75%, UL vs 25%; Bi-Di vs 100%):
        <span class="tput-good">green ≥70%</span>,
        <span class="tput-warn">orange 50–70%</span>,
        <span class="tput-bad">red &lt;50%</span>,
        <span class="tput-zero">zero = no traffic</span>.
        <strong>Data rate</strong> is from the spec sheet; <strong>Tx/Rx</strong> are DUT link operating rates from SSH/SNMP (green = match, red = mismatch).
        Pink rows highlight data rate mismatch; throughput pass/fail is based on TRex unless
        <code>--fail-on-rate-mismatch</code> is enabled. Run errors (e.g. TRex ports down) also use pink rows.
      </p>
    </section>
  </div>
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")
    return path
