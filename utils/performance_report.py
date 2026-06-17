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


def _rate_actual_cell(raw: str | None, ok: bool | None) -> str:
    """Operating rate with MCS index — green when Out rate matches spec."""
    if not raw or raw == "-":
        return "—"
    text = escape(str(raw))
    if ok is True:
        return f"<span class='mark pass'>{text}</span>"
    if ok is False:
        return f"<span class='mark fail'>{text}</span>"
    return text


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
    dl_frac, ul_frac = _parse_dl_ul_fractions(ratio)
    dl_target = data_rate_mbps * dl_frac
    ul_target = data_rate_mbps * ul_frac
    return dl_target, ul_target, data_rate_mbps


def _resolve_row_spec(record: dict[str, Any]) -> tuple[dict[str, Any], float]:
    link = record.get("link_validation") or {}
    spec = link.get("spec") or {}
    expected = float(
        link.get("expected_operating_rate_mbps")
        or record.get("operating_rate_mbps")
        or spec.get("operating_rate_mbps")
        or 0
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
    name = str(client.get("system_name") or client.get("name") or "").strip()
    if name and name != "-":
        return name
    ip = str(client.get("ip") or "").strip()
    if ip and ip not in {"0.0.0.0", "-"}:
        return ip
    return f"SU{index}"


def _trex_device_stats(stats: dict[str, Any], su_index: int) -> dict[str, Any]:
    trex = stats.get("trex") or {}
    by_device = trex.get("summary_by_device") or {}
    return by_device.get(f"SU{su_index}") or {}


def _iteration_heading(record: dict[str, Any], index: int, total: int) -> str:
    if total <= 1:
        return ""
    return (
        f"<h3 class='iteration-title'>"
        f"Iteration {index}/{total}: "
        f"{escape(str(record.get('bandwidth', '—')))} · "
        f"{escape(str(record.get('mcs', '—')))} · "
        f"DL:UL {escape(str(record.get('ratio', '—')))}"
        f"</h3>"
    )


def _render_link_stream_table(record: dict[str, Any]) -> str:
    """Per-SU link table aligned with BTS Monitor → Radio Statistics → Link."""
    link = record.get("link_validation") or {}
    clients = link.get("clients") or []
    if not clients:
        return "<p class='muted'>No per-SU link statistics captured.</p>"

    stats = record.get("stats") or {}
    rows: list[str] = []
    for index, client in enumerate(clients, start=1):
        trex_dev = _trex_device_stats(stats, index)
        trex_dl = trex_dev.get("avg_rx_mbps")
        trex_ul = trex_dev.get("avg_tx_mbps")
        dl_cell = f"{float(trex_dl):.1f}" if trex_dl is not None else "—"
        ul_cell = f"{float(trex_ul):.1f}" if trex_ul is not None else "—"
        local_snr = client.get("combined_local_snr") or "—"
        remote_snr = client.get("combined_remote_snr") or "—"
        ip = str(client.get("ip") or "—")
        row_class = "rate-mismatch" if client.get("rate_mismatch") else ""
        rows.append(
            f"""
          <tr class="{row_class}">
            <td>{index}</td>
            <td>{escape(_cpe_label(client, index))}</td>
            <td class="ip-cell">{escape(ip)}</td>
            <td>{escape(str(local_snr))} / {escape(str(remote_snr))}</td>
            <td>{_rate_actual_cell(client.get('out_rate'), client.get('out_rate_ok'))}</td>
            <td>{_rate_actual_cell(client.get('in_rate'), None)}</td>
            <td>{dl_cell}</td>
            <td>{ul_cell}</td>
          </tr>
            """
        )

    return f"""
    <div class="table-block">
      <div class="table-title">Link Stream Statistics (per SU)</div>
      <div class="sheet-scroll">
        <table class="matrix link-stream">
          <thead>
            <tr>
              <th>#</th>
              <th>System Name</th>
              <th>IP Address</th>
              <th>Combined SNR (dB)<br/><span class="muted">Local / Remote</span></th>
              <th>Rate Out (Mbps)<br/><span class="muted">BTS → CPE</span></th>
              <th>Rate In (Mbps)<br/><span class="muted">CPE → BTS</span></th>
              <th>TRex DL RX<br/><span class="muted">(Mbps)</span></th>
              <th>TRex UL TX<br/><span class="muted">(Mbps)</span></th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>
    </div>
    """


def _render_throughput_summary_table(record: dict[str, Any]) -> str:
    """Final summary: MCS, ratio, total throughput, operating rate."""
    stats = record.get("stats") or {}
    link = record.get("link_validation") or {}
    spec, expected_rate = _resolve_row_spec(record)
    ratio = str(record.get("ratio") or "50:50")
    dl_target, ul_target, bidi_target = _direction_targets(
        float(record.get("effective_target_mbps") or expected_rate),
        ratio,
    )

    combined = stats.get("combined") or {}
    downlink = stats.get("downlink") or {}
    uplink = stats.get("uplink") or {}
    dl_mbps = float(downlink.get("rx_mbps") or 0)
    ul_mbps = float(uplink.get("rx_mbps") or 0)
    bidi_mbps = float(combined.get("rx_mbps") or 0)

    configured_mcs = str(record.get("mcs") or spec.get("mcs") or "—")
    modulation = spec.get("modulation") or "—"
    mcs_index = spec.get("mcs_pair") or configured_mcs.replace("MCS", "")

    rate_mismatch = bool(link.get("operating_rate_mismatch"))
    if expected_rate > 0 and rate_mismatch:
        data_rate_cell = (
            f"<span class='mark fail'>{expected_rate:.0f}</span>"
            f"<br/><span class='muted'>data rate mismatch</span>"
        )
    elif expected_rate > 0:
        data_rate_cell = f"{expected_rate:.0f}"
    else:
        data_rate_cell = "—"

    passed = record.get("passed")
    mcs_config = record.get("mcs_config") or {}
    if mcs_config.get("mcs_config_ok") is True:
        mcs_cell = "<span class='mark pass'>All devices OK</span>"
    elif mcs_config.get("mcs_config_ok") is False:
        mcs_cell = "<span class='mark fail'>MCS mismatch</span>"
    else:
        mcs_cell = "—"

    if passed is True:
        status_cell = "<span class='mark pass'>PASS</span>"
    elif passed is False:
        status_cell = "<span class='mark fail'>FAIL</span>"
    else:
        status_cell = "—"

    error_text = str(record.get("error") or "")
    error_row = ""
    if error_text:
        short = escape(error_text if len(error_text) <= 160 else error_text[:157] + "…")
        error_row = f"""
        <tr>
          <th class="row-label">Error</th>
          <td colspan="3" class="error-cell">{short}</td>
        </tr>
        """

    return f"""
    <div class="table-block">
      <div class="table-title">Throughput Summary</div>
      <table class="matrix summary-matrix">
        <tbody>
          <tr>
            <th class="row-label">Bandwidth</th>
            <td>{escape(str(record.get('bandwidth', '—')))}</td>
            <th class="row-label">MCS Index</th>
            <td>{escape(str(mcs_index))} <span class="muted">({escape(configured_mcs)})</span></td>
          </tr>
          <tr>
            <th class="row-label">Modulation</th>
            <td>{escape(str(modulation))}</td>
            <th class="row-label">DL:UL Ratio</th>
            <td>{escape(ratio)}</td>
          </tr>
          <tr>
            <th class="row-label">Operating Data Rate</th>
            <td>{data_rate_cell}</td>
            <th class="row-label">Connected CPE</th>
            <td>{escape(str(link.get('connected_cpe_count', '—')))}</td>
          </tr>
          <tr>
            <th class="row-label">Effective Target</th>
            <td>{escape(str(record.get('effective_target_mbps', '—')))} Mbps</td>
            <th class="row-label">TRex Requested</th>
            <td>DL {escape(str(record.get('trex_dl_bw', '—')))} · UL {escape(str(record.get('trex_ul_bw', '—')))}</td>
          </tr>
          <tr>
            <th class="row-label">MCS Config (all devices)</th>
            <td>{mcs_cell}</td>
            <th class="row-label">Configured MCS</th>
            <td>{escape(str(mcs_config.get('configured_mcs') or record.get('mcs') or '—'))}</td>
          </tr>
          <tr>
            <th class="row-label">Total Downlink RX</th>
            <td>{_throughput_cell(dl_mbps, dl_target)}</td>
            <th class="row-label">Total Uplink RX</th>
            <td>{_throughput_cell(ul_mbps, ul_target)}</td>
          </tr>
          <tr>
            <th class="row-label">Total Bi-Directional</th>
            <td>{_throughput_cell(bidi_mbps, bidi_target)}</td>
            <th class="row-label">Result</th>
            <td>{status_cell}</td>
          </tr>
          <tr>
            <th class="row-label">Noise (dBm)</th>
            <td colspan="3">{escape(str(record.get('noise_dbm', '—')))}</td>
          </tr>
          {error_row}
        </tbody>
      </table>
    </div>
    """


def _render_result_block(record: dict[str, Any], *, index: int, total: int) -> str:
    return (
        _iteration_heading(record, index, total)
        + _render_link_stream_table(record)
        + _render_throughput_summary_table(record)
    )


def _render_results_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p>No performance iterations recorded.</p>"
    total = len(records)
    blocks = [
        f"<div class='iteration-block'>{_render_result_block(record, index=idx, total=total)}</div>"
        for idx, record in enumerate(records, start=1)
    ]
    return "\n".join(blocks)


def write_summary_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "bandwidth",
        "mcs",
        "mode",
        "ratio",
        "connected_cpe_count",
        "expected_operating_rate_mbps",
        "actual_out_rate_mbps",
        "actual_in_rate_mbps",
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
            _, _, bidi_target = _direction_targets(
                float(record.get("effective_target_mbps") or expected),
                ratio,
            )
            bidi = float(combined.get("rx_mbps") or 0)
            writer.writerow(
                {
                    "bandwidth": record.get("bandwidth"),
                    "mcs": record.get("mcs"),
                    "mode": record.get("mode"),
                    "ratio": record.get("ratio"),
                    "connected_cpe_count": link.get("connected_cpe_count"),
                    "expected_operating_rate_mbps": expected,
                    "actual_out_rate_mbps": primary.get("out_rate_mbps") or primary.get("tx_rate_mbps"),
                    "actual_in_rate_mbps": primary.get("in_rate_mbps") or primary.get("rx_rate_mbps"),
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
    .iteration-block {{
      margin-bottom: 28px; padding-bottom: 24px; border-bottom: 1px solid var(--border);
    }}
    .iteration-block:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
    .iteration-title {{
      margin: 0 0 14px; font-size: 15px; color: var(--head); font-weight: 600;
    }}
    .table-block {{ margin-bottom: 18px; }}
    .table-title {{
      font-size: 13px; font-weight: 700; color: var(--head); margin: 0 0 8px;
      text-transform: uppercase; letter-spacing: 0.4px;
    }}
    .ip-cell {{ white-space: nowrap; font-family: Consolas, Monaco, monospace; font-size: 11px; }}
    table.matrix {{
      width: 100%; max-width: 100%; border-collapse: collapse; background: #fff;
      border: 2px solid var(--border); border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(15,23,42,0.06);
    }}
    table.matrix th, table.matrix td {{
      border: 1px solid var(--border); padding: 12px 14px; text-align: center;
      vertical-align: middle;
    }}
    table.matrix thead th {{
      background: #f8fafc; color: var(--head); font-size: 12px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.4px;
    }}
    table.matrix th.row-label {{
      text-align: left; background: #f8fafc; color: var(--title);
      font-size: 13px; font-weight: 600; padding-left: 14px; width: 18%;
    }}
    table.matrix tbody tr.rate-mismatch td {{ background: #fff8f8; }}
    table.summary-matrix td {{ text-align: left; }}
    .sheet-scroll {{ width: 100%; overflow-x: auto; }}
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
    .error-cell {{ color: #991b1b; text-align: left; }}
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
        <strong>Link table</strong> mirrors BTS Monitor → Link: Out/In rates include MCS index when available.
        Out rate (BTS→CPE) is validated against the configured MCS; In rate (uplink) is informational.
        <strong>Throughput summary</strong> uses TRex totals vs effective target split by DL:UL ratio
        (<span class="tput-good">green ≥70%</span>,
        <span class="tput-warn">orange 50–70%</span>,
        <span class="tput-bad">red &lt;50%</span>).
        Pink per-SU rows highlight Out-rate mismatch with the MCS sheet.
      </p>
    </section>
  </div>
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")
    return path
