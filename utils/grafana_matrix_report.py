"""Grafana-style HTML report for throughput matrix runs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from traffic.operating_rate_table import lookup_spec
from utils.net_utils import format_mgmt_ipv6_display


def _pct(observed: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return round((observed / target) * 100.0, 1)


def _parse_float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"—", "-", "ash:", "n/a"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _link_clients_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in reversed(records):
        if record.get("skipped_trex"):
            continue
        for key in ("link_validation_post", "link_validation"):
            clients = (record.get(key) or {}).get("clients") or []
            if clients:
                return list(clients)
    return []


def _testbed_devices(
    testbed_summary: dict[str, Any] | None,
    link_clients: list[dict[str, Any]],
    *,
    prefix_len: int = 120,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary = testbed_summary or {}
    prefix = int(summary.get("ipv6_prefix_len") or prefix_len)
    bts = dict(summary.get("bts") or {})
    if bts:
        bts["ip_display"] = format_mgmt_ipv6_display("BTS", str(bts.get("ip") or ""), prefix_len=prefix)
        bts["label"] = "BTS"

    devices: list[dict[str, Any]] = []
    cpes = summary.get("cpes")
    link_mac_by_su = {
        int(client.get("su_index") or client.get("sua_index") or 0): str(client.get("mac") or "").strip()
        for client in link_clients
    }
    if isinstance(cpes, list) and cpes:
        for unit in cpes:
            label = str(unit.get("label") or f"SU{unit.get('su_index', len(devices) + 1)}")
            su_index = int(unit.get("su_index") or len(devices) + 1)
            devices.append(
                {
                    **unit,
                    "label": label,
                    "su_index": su_index,
                    "mac": str(unit.get("mac") or link_mac_by_su.get(su_index) or "—"),
                    "ip_display": format_mgmt_ipv6_display(label, str(unit.get("ip") or ""), prefix_len=prefix),
                }
            )
    elif link_clients:
        mac_by_su = {
            int(client.get("su_index") or client.get("sua_index") or 0): str(client.get("mac") or "").strip()
            for client in link_clients
        }
        for client in link_clients:
            su_index = int(client.get("su_index") or client.get("sua_index") or len(devices) + 1)
            label = f"SU{su_index}"
            ipv6 = str(client.get("ipv6") or client.get("ip") or "")
            devices.append(
                {
                    "label": label,
                    "su_index": su_index,
                    "model": str(client.get("r_model") or "—"),
                    "fw_version": "—",
                    "ip": ipv6 or "—",
                    "ip_display": format_mgmt_ipv6_display(label, ipv6, prefix_len=prefix),
                    "mac": mac_by_su.get(su_index) or str(client.get("mac") or "—"),
                    "vlan": "—",
                    "qos": "—",
                }
            )
    return bts, devices


def _link_device_rows(link_clients: list[dict[str, Any]], *, prefix_len: int = 120) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for client in sorted(
        link_clients,
        key=lambda row: int(row.get("su_index") or row.get("sua_index") or 0),
    ):
        su_index = int(client.get("su_index") or client.get("sua_index") or 0)
        label = f"SU{su_index}" if su_index else "SU"
        ipv6 = str(client.get("ipv6") or client.get("ip") or "")
        rows.append(
            {
                "label": label,
                "mac": str(client.get("mac") or "—").strip() or "—",
                "model": str(client.get("r_model") or "—"),
                "serial": str(client.get("r_serialno") or "—"),
                "ip_display": format_mgmt_ipv6_display(label, ipv6, prefix_len=prefix_len),
                "tx_rate": str(client.get("tx_rate") or client.get("out_rate") or "—"),
                "rx_rate": str(client.get("rx_rate") or client.get("in_rate") or "—"),
                "snr": f"{client.get('r_snr1') or '—'}/{client.get('r_snr2') or '—'}",
                "rssi": str(client.get("r_rssi1") or client.get("l_rssi1") or "—"),
            }
        )
    return rows


def _extract_time_series(record: dict[str, Any]) -> dict[str, list[Any]]:
    trex = (record.get("stats") or {}).get("trex") or {}
    live_samples = trex.get("live_samples") or []
    dut_samples = ((trex.get("dut_counters") or {}).get("samples") or [])
    dut_loss: list[float | None] = []
    for sample in dut_samples:
        dut_loss.append(_parse_float(sample.get("avg_rtx_pct")))

    labels: list[str] = []
    rx_mbps: list[float] = []
    loss_pct: list[float] = []
    for index, sample in enumerate(live_samples):
        labels.append(str(sample.get("timestamp") or f"+{index * 5}s"))
        combined = sample.get("combined") or {}
        rx = float(combined.get("rx_mbps") or 0.0)
        rx_mbps.append(round(rx, 2))
        tx_pps = float(combined.get("tx_pps") or 0.0)
        rx_pps = float(combined.get("rx_pps") or 0.0)
        if index < len(dut_loss) and dut_loss[index] is not None:
            loss_pct.append(round(dut_loss[index], 3))
        elif tx_pps > 0:
            loss_pct.append(round(max(0.0, (tx_pps - rx_pps) / tx_pps * 100.0), 3))
        else:
            loss_pct.append(0.0)
    return {"labels": labels, "rx_mbps": rx_mbps, "loss_pct": loss_pct}


def _target_mbps_for_record(record: dict[str, Any]) -> float:
    target = float(record.get("effective_target_mbps") or 0)
    if target > 0:
        return target
    bandwidth = str(record.get("bandwidth") or "")
    mcs = str(record.get("mcs") or "")
    if not bandwidth or not mcs:
        return 0.0
    try:
        spec = lookup_spec(mcs, bandwidth, spatial_streams=int(record.get("spatial_stream") or 2))
        efficiency = float(record.get("efficiency_factor") or 0.7)
        return round(float(spec["operating_rate_mbps"]) * efficiency, 1)
    except (TypeError, ValueError):
        return 0.0


def _build_matrix_cells(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for rec_idx, record in enumerate(records):
        skipped = bool(record.get("skipped_trex"))
        stats = record.get("stats") or {}
        combined = stats.get("combined") or {}
        rx_mbps = 0.0 if skipped else float(combined.get("rx_mbps") or 0.0)
        tx_mbps = 0.0 if skipped else float(combined.get("tx_mbps") or 0.0)
        target_mbps = _target_mbps_for_record(record)
        note = str(
            record.get("mcs_mismatch_note")
            or (record.get("mcs_config") or {}).get("mcs_mismatch_note")
            or record.get("error")
            or ""
        ).strip()
        if skipped and not note:
            note = "TRex skipped"
        cells.append(
            {
                "iter_key": rec_idx,
                "bandwidth": str(record.get("bandwidth") or ""),
                "mcs": str(record.get("mcs") or ""),
                "tested": True,
                "skipped": skipped,
                "passed": bool(record.get("passed")),
                "tx_mbps": tx_mbps,
                "rx_mbps": rx_mbps,
                "target_mbps": target_mbps,
                "pct": _pct(rx_mbps, target_mbps) if not skipped else 0.0,
                "remark": note or "—",
                "duration_s": int(record.get("duration_s") or 0),
                "time_series": None if skipped else _extract_time_series(record),
            }
        )
    return cells


def _peak_by_bandwidth(cells: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    peaks: dict[str, dict[str, Any]] = {}
    for bw in ("HT20", "HT40", "HT80"):
        rows = [row for row in cells if row["bandwidth"] == bw and not row.get("skipped")]
        if not rows:
            continue
        best = max(rows, key=lambda row: float(row.get("rx_mbps") or 0))
        peaks[bw] = {
            "mcs": best["mcs"],
            "rx_mbps": float(best.get("rx_mbps") or 0),
            "passed": sum(1 for row in rows if row.get("passed")),
            "total": len(rows),
        }
    return peaks


def enrich_matrix_payload(
    records: list[dict[str, Any]],
    *,
    testbed_summary: dict[str, Any] | None = None,
    report_id: str = "local",
    generated_at: str = "",
    stand: str = "",
    profile: str = "",
    source_file: str = "",
) -> dict[str, Any]:
    """Build full matrix Grafana payload from performance matrix records."""
    summary = testbed_summary or {}
    prefix_len = int(summary.get("ipv6_prefix_len") or 120)
    link_clients = _link_clients_from_records(records)
    bts, testbed_devices = _testbed_devices(summary, link_clients, prefix_len=prefix_len)
    matrix_cells = _build_matrix_cells(records)
    peaks = _peak_by_bandwidth(matrix_cells)
    ran_cells = [cell for cell in matrix_cells if not cell.get("skipped")]
    passed = sum(1 for cell in ran_cells if cell.get("passed"))
    total_ran = len(ran_cells)
    total_tested = len(matrix_cells)

    iterations = [
        {
            "iter_key": cell["iter_key"],
            "bandwidth": cell["bandwidth"],
            "mcs": cell["mcs"],
            "passed": cell["passed"],
            "tx_mbps": cell["tx_mbps"],
            "rx_mbps": cell["rx_mbps"],
            "target_mbps": cell["target_mbps"],
            "pct": cell["pct"],
            "remark": cell["remark"],
            "skipped": cell["skipped"],
        }
        for cell in matrix_cells
    ]

    bandwidths = sorted({cell["bandwidth"] for cell in matrix_cells if cell.get("bandwidth")})
    mcs_rates = sorted(
        {cell["mcs"] for cell in matrix_cells if cell.get("mcs")},
        key=lambda label: int(re.sub(r"[^0-9]", "", label) or 0),
    )

    return {
        "report_kind": "matrix",
        "report_id": report_id,
        "source_file": source_file or f"performance-matrix-{report_id}",
        "stand": stand or str(summary.get("stand") or "—"),
        "profile": profile or str(summary.get("profile") or "—"),
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "su_count": int(summary.get("su_count") or len(testbed_devices) or len(link_clients) or 4),
        "ipv6_prefix_len": prefix_len,
        "matrix_passed": passed,
        "matrix_total": total_tested,
        "matrix_ran": total_ran,
        "iterations": iterations,
        "matrix_cells": matrix_cells,
        "bandwidths": bandwidths,
        "mcs_rates": mcs_rates,
        "peaks": peaks,
        "testbed": {"bts": bts, "devices": testbed_devices},
        "link_devices": _link_device_rows(link_clients, prefix_len=prefix_len),
    }


def _device_chip(label: str, *, is_bts: bool = False) -> str:
    css = "device-chip bts" if is_bts else "device-chip su"
    return f'<span class="{css}"><span class="chip-dot"></span>{escape(label)}</span>'


def _status_pill(status: str) -> str:
    mapping = {
        "PASS": ("pill pass", "Pass"),
        "FAIL": ("pill fail", "Fail"),
        "SKIP": ("pill skip", "Skipped"),
    }
    css, text = mapping.get(status, ("pill neutral", status))
    return f'<span class="{css}">{text}</span>'


def _render_testbed_table(data: dict[str, Any]) -> str:
    bts = (data.get("testbed") or {}).get("bts") or {}
    devices = (data.get("testbed") or {}).get("devices") or []
    units: list[tuple[str, dict[str, Any], bool]] = []
    if bts:
        units.append(("BTS", bts, True))
    for device in devices:
        units.append((str(device.get("label") or "SU"), device, False))

    if not units:
        return '<p class="empty-note">No testbed summary collected for this run.</p>'

    rows = []
    for label, unit, is_bts in units:
        row_cls = "row-bts" if is_bts else ""
        rows.append(
            f"<tr class='{row_cls}'>"
            f"<td><strong>{_device_chip(label, is_bts=is_bts)}</strong></td>"
            f"<td>{escape(str(unit.get('model') or '—'))}</td>"
            f"<td>{escape(str(unit.get('fw_version') or '—'))}</td>"
            f"<td class='mono'>{escape(str(unit.get('mac') or '—'))}</td>"
            f"<td class='mono'>{escape(str(unit.get('ip_display') or unit.get('ip') or '—'))}</td>"
            f"<td>{escape(str(unit.get('vlan') or '—'))}</td>"
            f"<td>{escape(str(unit.get('qos') or '—'))}</td>"
            f"</tr>"
        )
    return f"""
    <div class="table-scroll">
      <table class="data-table testbed-table">
        <thead>
          <tr>
            <th>Unit</th><th>Model</th><th>Firmware</th><th>MAC</th>
            <th>Mgmt IPv6</th><th>VLAN</th><th>QoS</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _render_link_table(link_devices: list[dict[str, Any]]) -> str:
    if not link_devices:
        return '<p class="empty-note">No per-SU link snapshot (from BTS KWN sysfs).</p>'
    rows = []
    for device in link_devices:
        rows.append(
            f"<tr>"
            f"<td><strong>{escape(device['label'])}</strong></td>"
            f"<td class='mono'>{escape(device['mac'])}</td>"
            f"<td>{escape(device['model'])}</td>"
            f"<td class='mono'>{escape(device['ip_display'])}</td>"
            f"<td>{escape(device['tx_rate'])}</td>"
            f"<td>{escape(device['rx_rate'])}</td>"
            f"<td>{escape(device['snr'])}</td>"
            f"<td>{escape(device['rssi'])} dBm</td>"
            f"</tr>"
        )
    return f"""
    <div class="table-scroll">
      <table class="data-table link-table">
        <thead>
          <tr>
            <th>Unit</th><th>MAC</th><th>Model</th><th>IPv6</th>
            <th>Tx rate</th><th>Rx rate</th><th>SNR</th><th>RSSI</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _render_coverage_matrix(data: dict[str, Any]) -> str:
    bandwidths = data.get("bandwidths") or ["HT20", "HT40", "HT80"]
    mcs_rates = data.get("mcs_rates") or []
    cells = data.get("matrix_cells") or []
    by_key = {(c["bandwidth"], c["mcs"]): c for c in cells}
    bw_colors = {"HT20": "#34d399", "HT40": "#60a5fa", "HT80": "#c084fc"}
    status_icon = {"pass": "✓", "fail": "✕", "skip": "!"}

    rows_html = []
    for bw in bandwidths:
        cell_html = []
        for mcs in mcs_rates:
            cell = by_key.get((bw, mcs))
            if not cell:
                cell_html.append("<div class='cov-card cov-empty'><span>—</span></div>")
                continue
            if cell.get("skipped"):
                status = "skip"
            elif cell.get("passed"):
                status = "pass"
            else:
                status = "fail"
            rx = float(cell.get("rx_mbps") or 0)
            tx = float(cell.get("tx_mbps") or 0)
            pct = float(cell.get("pct") or 0)
            rx_label = "—" if cell.get("skipped") else f"{rx:.0f}"
            tx_rx_label = "" if cell.get("skipped") else f"{tx:.0f} sent → {rx:.0f} got"
            pct_label = "" if cell.get("skipped") else f"{pct:.0f}% of target"
            cell_html.append(
                f"<button type='button' class='cov-card cov-{status}' "
                f"data-iter-key='{cell['iter_key']}' "
                f"style='--bw-color:{bw_colors.get(bw, '#94a3b8')}'>"
                f"<span class='cov-icon'>{status_icon[status]}</span>"
                f"<span class='cov-mcs'>{escape(mcs)}</span>"
                f"<span class='cov-rx'>{rx_label}<small>Mbps RX</small></span>"
                f"<span class='cov-pct'>{tx_rx_label or '—'}</span>"
                f"<span class='cov-cta'>{pct_label or 'View live chart'}</span>"
                f"</button>"
            )
        rows_html.append(
            f"<div class='cov-row'>"
            f"<div class='cov-bw-label' style='--bw-color:{bw_colors.get(bw, '#94a3b8')}'>{escape(bw)}</div>"
            f"<div class='cov-row-cells'>{''.join(cell_html)}</div></div>"
        )

    return f"""
    <div class="cov-board" id="coverageMatrix">
      {''.join(rows_html)}
    </div>
    <div class="cov-legend">
      <span><i class="lg pass"></i> Pass</span>
      <span><i class="lg fail"></i> Fail</span>
      <span><i class="lg skip"></i> Skipped</span>
      <span><i class="lg empty"></i> Not tested</span>
    </div>
    """


def _render_kpi_cards(data: dict[str, Any], *, passed: int, ran: int, total: int, peak_rx: float, pass_cls: str) -> str:
    peaks = data.get("peaks") or {}
    best_bw = max(peaks.items(), key=lambda item: float((item[1] or {}).get("rx_mbps") or 0))[0] if peaks else "—"
    return f"""
    <div class="kpi-grid">
      <article class="kpi-card accent-{pass_cls}">
        <div class="kpi-icon">◎</div>
        <div class="kpi-body">
          <div class="kpi-label">Matrix pass rate</div>
          <div class="kpi-value">{passed}<span>/ {ran}</span></div>
          <div class="kpi-sub">TRex iterations executed</div>
        </div>
      </article>
      <article class="kpi-card accent-ok">
        <div class="kpi-icon">⚡</div>
        <div class="kpi-body">
          <div class="kpi-label">Peak throughput</div>
          <div class="kpi-value">{peak_rx:.0f}<span>Mbps</span></div>
          <div class="kpi-sub">Best on {escape(best_bw)}</div>
        </div>
      </article>
      <article class="kpi-card accent-ok">
        <div class="kpi-icon">⬡</div>
        <div class="kpi-body">
          <div class="kpi-label">Connected SUs</div>
          <div class="kpi-value">{int(data.get('su_count') or 0)}</div>
          <div class="kpi-sub">Live link snapshot</div>
        </div>
      </article>
      <article class="kpi-card accent-neutral">
        <div class="kpi-icon">▦</div>
        <div class="kpi-body">
          <div class="kpi-label">Coverage</div>
          <div class="kpi-value">{total}<span>cells</span></div>
          <div class="kpi-sub">{escape(' × '.join(data.get('mcs_rates') or []))}</div>
        </div>
      </article>
    </div>
    """


_REPORT_CSS = """
    :root {
      --bg0: #f8fafc;
      --bg1: #f1f5f9;
      --panel: #ffffff;
      --panel-border: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --accent: #2563eb;
      --accent2: #4f46e5;
      --ok: #059669;
      --warn: #d97706;
      --bad: #dc2626;
      --shadow: 0 4px 24px rgba(15, 23, 42, 0.06);
      --radius: 14px;
      --radius-sm: 10px;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      font-family: "Plus Jakarta Sans", Inter, system-ui, sans-serif;
      color: var(--text);
      min-height: 100vh;
      background: linear-gradient(180deg, var(--bg0), var(--bg1));
    }
    .hero {
      position: relative;
      padding: 28px 28px 24px;
      border-bottom: 1px solid var(--panel-border);
      background: linear-gradient(135deg, #ffffff 0%, #eff6ff 55%, #e0e7ff 100%);
      overflow: hidden;
    }
    .hero::after {
      content: "";
      position: absolute;
      right: -60px;
      top: -60px;
      width: 220px;
      height: 220px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(37,99,235,0.08), transparent 68%);
      pointer-events: none;
    }
    .hero-inner {
      max-width: 1680px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 24px;
      flex-wrap: wrap;
      position: relative;
      z-index: 1;
    }
    .hero-brand {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
    }
    .hero-mark {
      width: 48px;
      height: 48px;
      border-radius: 14px;
      display: grid;
      place-items: center;
      font-weight: 800;
      font-size: 14px;
      letter-spacing: 0.04em;
      color: #1d4ed8;
      background: #ffffff;
      border: 1px solid #bfdbfe;
      box-shadow: 0 2px 8px rgba(37,99,235,0.12);
    }
    .hero-eyebrow {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--muted);
      font-weight: 700;
    }
    .hero h1 {
      margin: 4px 0 0;
      font-size: clamp(28px, 4vw, 38px);
      font-weight: 800;
      letter-spacing: -0.03em;
      line-height: 1.05;
      color: var(--text);
    }
    .hero-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .meta-chip {
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      color: #334155;
      background: #ffffff;
      border: 1px solid var(--panel-border);
    }
    .hero-status {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 10px;
    }
    .hero-badge {
      padding: 12px 18px;
      border-radius: 12px;
      font-size: 14px;
      font-weight: 700;
      background: #ffffff;
      border: 1px solid var(--panel-border);
      box-shadow: var(--shadow);
    }
    .hero-badge.ok { border-color: #6ee7b7; color: #047857; background: #ecfdf5; }
    .hero-badge.warn { border-color: #fcd34d; color: #b45309; background: #fffbeb; }
    .hero-badge.bad { border-color: #fca5a5; color: #b91c1c; background: #fef2f2; }
    .hero-time { font-size: 12px; color: var(--muted); }

    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 18px;
      padding: 22px 18px 40px;
      max-width: 1680px;
      margin: 0 auto;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: var(--radius);
      padding: 20px 22px;
      box-shadow: var(--shadow);
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 16px;
    }
    .panel h2 {
      margin: 0;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #475569;
    }
    .panel-desc {
      margin: 6px 0 0;
      font-size: 13px;
      line-height: 1.5;
      color: var(--muted);
      max-width: 62ch;
    }
    .span-12 { grid-column: span 12; }

    .device-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .device-chip .chip-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
    }
    .device-chip.bts { color: #b45309; background: #fffbeb; border: 1px solid #fde68a; }
    .device-chip.su { color: #1d4ed8; background: #eff6ff; border: 1px solid #bfdbfe; }

    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .kpi-card {
      display: flex;
      gap: 14px;
      align-items: center;
      padding: 16px 18px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--panel-border);
      background: #f8fafc;
    }
    .kpi-icon {
      width: 44px;
      height: 44px;
      border-radius: 12px;
      display: grid;
      place-items: center;
      font-size: 18px;
      background: #eff6ff;
      border: 1px solid #bfdbfe;
    }
    .kpi-card.accent-ok .kpi-icon { background: #ecfdf5; border-color: #6ee7b7; }
    .kpi-card.accent-warn .kpi-icon { background: #fffbeb; border-color: #fcd34d; }
    .kpi-card.accent-bad .kpi-icon { background: #fef2f2; border-color: #fca5a5; }
    .kpi-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); font-weight: 700; }
    .kpi-value {
      margin-top: 4px;
      font-size: 30px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--text);
    }
    .kpi-value span { font-size: 14px; color: var(--muted); font-weight: 600; margin-left: 4px; }
    .kpi-sub { margin-top: 6px; font-size: 12px; color: var(--muted); }

    .cov-board { display: grid; gap: 12px; }
    .cov-row {
      display: grid;
      grid-template-columns: 72px 1fr;
      gap: 12px;
      align-items: stretch;
    }
    .cov-bw-label {
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--radius-sm);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      color: var(--bw-color);
      background: color-mix(in srgb, var(--bw-color) 10%, white);
      border: 1px solid color-mix(in srgb, var(--bw-color) 25%, white);
    }
    .cov-row-cells {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 10px;
    }
    .cov-card {
      appearance: none;
      border: 1px solid var(--panel-border);
      border-radius: 12px;
      padding: 14px 12px;
      min-height: 128px;
      background: #ffffff;
      color: var(--text);
      cursor: pointer;
      text-align: left;
      display: grid;
      gap: 4px;
      position: relative;
      overflow: hidden;
      transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
    }
    .cov-card::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--bw-color);
      opacity: 0.85;
    }
    .cov-card:hover, .cov-card.selected {
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(15,23,42,0.08);
      border-color: color-mix(in srgb, var(--bw-color) 35%, #e2e8f0);
    }
    .cov-card.selected { outline: 2px solid color-mix(in srgb, var(--bw-color) 40%, transparent); outline-offset: 1px; }
    .cov-card.cov-pass { background: linear-gradient(180deg, #ecfdf5, #ffffff); }
    .cov-card.cov-fail { background: linear-gradient(180deg, #fef2f2, #ffffff); }
    .cov-card.cov-skip { background: linear-gradient(180deg, #fffbeb, #ffffff); }
    .cov-card.cov-empty { opacity: 0.5; cursor: default; display: grid; place-items: center; color: var(--muted); }
    .cov-icon {
      width: 24px;
      height: 24px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      font-size: 12px;
      font-weight: 800;
      background: #f1f5f9;
      border: 1px solid var(--panel-border);
    }
    .cov-mcs { font-size: 13px; font-weight: 800; letter-spacing: 0.04em; }
    .cov-rx { font-size: 24px; font-weight: 800; letter-spacing: -0.03em; line-height: 1.1; }
    .cov-rx small { font-size: 11px; color: var(--muted); font-weight: 700; margin-left: 4px; }
    .cov-pct { font-size: 11px; color: var(--muted); font-weight: 600; }
    .cov-cta {
      margin-top: 4px;
      font-size: 11px;
      font-weight: 700;
      color: var(--accent);
      opacity: 0;
      transform: translateY(4px);
      transition: opacity .16s ease, transform .16s ease;
    }
    .cov-card:hover .cov-cta, .cov-card.selected .cov-cta { opacity: 1; transform: translateY(0); }
    .cov-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      margin-top: 14px;
      font-size: 12px;
      color: var(--muted);
    }
    .cov-legend i.lg {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      margin-right: 6px;
      vertical-align: -1px;
    }
    i.lg.pass { background: var(--ok); }
    i.lg.fail { background: var(--bad); }
    i.lg.skip { background: var(--warn); }
    i.lg.empty { background: #94a3b8; }

    .chart-panel {
      padding: 0;
      overflow: hidden;
    }
    .chart-head {
      padding: 20px 22px 0;
    }
    .series-title { font-size: 18px; font-weight: 800; letter-spacing: -0.02em; color: var(--text); }
    .series-meta { font-size: 13px; color: var(--muted); margin-top: 6px; }
    .chart-wrap {
      padding: 8px 18px 22px;
      min-height: 320px;
      background: #f8fafc;
    }

    .table-scroll { overflow: auto; border-radius: var(--radius-sm); border: 1px solid var(--panel-border); }
    table.data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      background: #ffffff;
    }
    table.data-table th, table.data-table td {
      padding: 11px 14px;
      border-bottom: 1px solid #f1f5f9;
      text-align: left;
      vertical-align: middle;
    }
    table.data-table td.mono {
      font-family: ui-monospace, "Cascadia Code", monospace;
      font-size: 12px;
      color: #334155;
      word-break: break-all;
      white-space: normal;
      max-width: 280px;
    }
    table.data-table thead th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f8fafc;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      border-bottom: 1px solid var(--panel-border);
      white-space: nowrap;
    }
    table.data-table tbody tr:hover { background: #f8fafc; }
    table.data-table tbody tr.row-bts { background: #fffbeb; }
    table.data-table tbody tr.row-bts:hover { background: #fef3c7; }
    table.data-table tbody tr.row-pass { box-shadow: inset 3px 0 0 #34d399; }
    table.data-table tbody tr.row-fail { box-shadow: inset 3px 0 0 #f87171; }
    table.data-table tbody tr.row-skip { box-shadow: inset 3px 0 0 #fbbf24; }

    .pill {
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .pill.pass { color: #047857; background: #ecfdf5; border: 1px solid #6ee7b7; }
    .pill.fail { color: #b91c1c; background: #fef2f2; border: 1px solid #fca5a5; }
    .pill.skip { color: #b45309; background: #fffbeb; border: 1px solid #fcd34d; }
    .pill.neutral { color: #475569; background: #f1f5f9; border: 1px solid #e2e8f0; }

    .empty-note {
      margin: 0;
      padding: 18px;
      border-radius: var(--radius-sm);
      border: 1px dashed #cbd5e1;
      color: var(--muted);
      background: #f8fafc;
      font-size: 13px;
    }

    @media (max-width: 1100px) {
      .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .cov-row { grid-template-columns: 1fr; }
      .cov-bw-label { min-height: 36px; }
    }
    @media (max-width: 720px) {
      .kpi-grid { grid-template-columns: 1fr; }
      .hero-status { align-items: flex-start; }
    }

"""


def write_matrix_grafana_html(path: str | Path, data: dict[str, Any]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    passed = int(data.get("matrix_passed") or 0)
    total = int(data.get("matrix_total") or 0)
    ran = int(data.get("matrix_ran") or passed)
    peaks = data.get("peaks") or {}
    peak_rx = max((float((peaks.get(bw) or {}).get("rx_mbps") or 0) for bw in peaks), default=0.0)
    report_id = escape(str(data.get("report_id", "")))
    pass_cls = "ok" if passed == ran and ran else "warn" if passed >= ran // 2 else "bad"

    series_json = {
        str(cell["iter_key"]): cell.get("time_series") or {"labels": [], "rx_mbps": [], "loss_pct": []}
        for cell in (data.get("matrix_cells") or [])
    }
    default_key = next(
        (
            str(cell["iter_key"])
            for cell in (data.get("matrix_cells") or [])
            if not cell.get("skipped") and cell.get("time_series", {}).get("labels")
        ),
        "0",
    )

    generated = escape(str(data.get("generated_at") or ""))
    if "T" in generated and len(generated) > 18:
        generated = generated.replace("T", " · ").split("+")[0]

    iter_rows = []
    for cell in data.get("matrix_cells") or []:
        if cell.get("skipped"):
            status = "SKIP"
        elif cell.get("passed"):
            status = "PASS"
        else:
            status = "FAIL"
        iter_rows.append(
            f"<tr data-iter-key='{cell['iter_key']}' "
            f"class='{'row-skip' if cell.get('skipped') else 'row-pass' if cell.get('passed') else 'row-fail'}'>"
            f"<td><strong>{escape(cell['bandwidth'])}</strong></td>"
            f"<td>{escape(cell['mcs'])}</td>"
            f"<td>{'—' if cell.get('skipped') else format(float(cell.get('tx_mbps') or 0), '.1f')}</td>"
            f"<td>{'—' if cell.get('skipped') else format(float(cell.get('rx_mbps') or 0), '.1f')}</td>"
            f"<td>{float(cell.get('target_mbps') or 0):.1f}</td>"
            f"<td>{_status_pill(status)}</td>"
            f"<td>{escape(str(cell.get('remark') or '—'))}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>UBR Performance Report #{report_id}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>{_REPORT_CSS}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <div class="hero-brand">
          <div class="hero-mark">UBR</div>
          <div>
            <div class="hero-eyebrow">Throughput matrix report</div>
            <h1>Performance Run #{report_id}</h1>
          </div>
        </div>
        <div class="hero-meta">
          <span class="meta-chip">{escape(str(data.get('stand')))}</span>
          <span class="meta-chip">{escape(str(data.get('profile')))}</span>
          <span class="meta-chip">{total} test cells</span>
        </div>
      </div>
      <div class="hero-status">
        <div class="hero-badge {pass_cls}">{passed}/{ran} TRex passed</div>
        <div class="hero-time">{generated}</div>
      </div>
    </div>
  </header>

  <main class="grid">
    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Testbed summary</h2>
          <p class="panel-desc">BTS and SU inventory — model, firmware, hostname, and management IPv6 (/120).</p>
        </div>
      </div>
      {_render_testbed_table(data)}
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Link stats</h2>
          <p class="panel-desc">Live RF snapshot from BTS KWN sysfs during the reference iteration.</p>
        </div>
      </div>
      {_render_link_table(data.get('link_devices') or [])}
    </section>

    <section class="panel span-12">
      {_render_kpi_cards(data, passed=passed, ran=ran, total=total, peak_rx=peak_rx, pass_cls=pass_cls)}
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Coverage matrix</h2>
          <p class="panel-desc">Bandwidth × MCS cells executed in this run. Click a card to plot throughput and loss over time.</p>
        </div>
      </div>
      {_render_coverage_matrix(data)}
    </section>

    <section class="panel span-12 chart-panel" id="chartSection">
      <div class="chart-head">
        <h2>Live throughput</h2>
        <div class="series-title" id="seriesTitle">Select a matrix cell</div>
        <div class="series-meta" id="seriesMeta">Click a highlighted BW×MCS card to plot TRex live samples.</div>
      </div>
      <div class="chart-wrap">
        <canvas id="timeSeriesChart"></canvas>
      </div>
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Iteration log</h2>
          <p class="panel-desc">Full matrix audit trail with throughput result and remarks.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table" id="iterLog">
          <thead>
            <tr><th>BW</th><th>MCS</th><th>TX Mbps</th><th>RX Mbps</th><th>Target</th><th>Status</th><th>Remarks</th></tr>
          </thead>
          <tbody>{''.join(iter_rows)}</tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    const seriesData = {json.dumps(series_json)};
    const cellMeta = {json.dumps({
        str(cell["iter_key"]): {
            "bandwidth": cell["bandwidth"],
            "mcs": cell["mcs"],
            "tx_mbps": cell["tx_mbps"],
            "rx_mbps": cell["rx_mbps"],
            "remark": cell["remark"],
            "skipped": cell["skipped"],
        }
        for cell in (data.get("matrix_cells") or [])
    })};
    let activeKey = {json.dumps(default_key)};
    const gridColor = 'rgba(148,163,184,0.25)';
    const chartEl = document.getElementById('timeSeriesChart');
    const chartCtx = chartEl.getContext('2d');
    const rxGradient = chartCtx.createLinearGradient(0, 0, 0, 320);
    rxGradient.addColorStop(0, 'rgba(37,99,235,0.18)');
    rxGradient.addColorStop(1, 'rgba(37,99,235,0.02)');

    const chart = new Chart(chartEl, {{
      type: 'line',
      data: {{ labels: [], datasets: [] }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{
            labels: {{ color: '#334155', font: {{ family: 'Plus Jakarta Sans', size: 12, weight: '600' }} }}
          }},
          tooltip: {{
            backgroundColor: '#ffffff',
            borderColor: '#e2e8f0',
            borderWidth: 1,
            titleColor: '#0f172a',
            bodyColor: '#475569',
            titleFont: {{ family: 'Plus Jakarta Sans', weight: '700' }},
            bodyFont: {{ family: 'Plus Jakarta Sans' }},
            padding: 12,
            cornerRadius: 10,
          }}
        }},
        scales: {{
          x: {{
            ticks: {{ color: '#64748b', font: {{ family: 'Plus Jakarta Sans' }} }},
            grid: {{ color: gridColor }}
          }},
          y: {{
            type: 'linear', position: 'left',
            title: {{ display: true, text: 'Throughput (Mbps)', color: '#64748b', font: {{ weight: '600' }} }},
            ticks: {{ color: '#64748b' }}, grid: {{ color: gridColor }}, suggestedMin: 0
          }},
          y1: {{
            type: 'linear', position: 'right',
            title: {{ display: true, text: 'Packet loss %', color: '#d97706', font: {{ weight: '600' }} }},
            ticks: {{ color: '#d97706' }}, grid: {{ drawOnChartArea: false }}, suggestedMin: 0
          }}
        }}
      }}
    }});

    function selectCell(key) {{
      activeKey = String(key);
      document.querySelectorAll('.cov-card[data-iter-key]').forEach(el => {{
        el.classList.toggle('selected', el.dataset.iterKey === activeKey);
      }});
      const meta = cellMeta[activeKey] || {{}};
      const series = seriesData[activeKey] || {{ labels: [], rx_mbps: [], loss_pct: [] }};
      document.getElementById('seriesTitle').textContent =
        meta.skipped ? `${{meta.bandwidth || '—'}} · ${{meta.mcs || '—'}} · skipped` :
        `${{meta.bandwidth || '—'}} · ${{meta.mcs || '—'}} · ${{(meta.tx_mbps || 0).toFixed(1)}} sent → ${{(meta.rx_mbps || 0).toFixed(1)}} Mbps got`;
      document.getElementById('seriesMeta').textContent =
        meta.skipped ? (meta.remark || 'TRex not run') :
        `${{series.labels.length}} live samples · loss from DUT avg_rtx or TX/RX PPS delta`;
      chart.data.labels = series.labels;
      chart.data.datasets = [
        {{
          label: 'Combined RX Mbps',
          data: series.rx_mbps,
          borderColor: '#2563eb',
          backgroundColor: rxGradient,
          fill: true,
          tension: 0.35,
          yAxisID: 'y',
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#2563eb',
          borderWidth: 2.5,
        }},
        {{
          label: 'Packet loss %',
          data: series.loss_pct,
          borderColor: '#d97706',
          backgroundColor: 'rgba(217,119,6,0.08)',
          borderDash: [6, 4],
          tension: 0.3,
          yAxisID: 'y1',
          pointRadius: 3,
          borderWidth: 2,
        }},
      ];
      chart.update();
      document.getElementById('chartSection').scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
    }}

    document.getElementById('coverageMatrix').addEventListener('click', (event) => {{
      const cell = event.target.closest('.cov-card[data-iter-key]');
      if (!cell || cell.classList.contains('cov-empty')) return;
      selectCell(cell.dataset.iterKey);
    }});

    if (activeKey && seriesData[activeKey]) selectCell(activeKey);
  </script>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out
