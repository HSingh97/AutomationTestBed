"""Performance matrix HTML/CSV reports."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from traffic.operating_rate_table import lookup_spec, modulation_scheme
from utils.regression_report import _render_testbed_summary_table

SENAO_LOGO_URL = (
    "https://manuals.plus/wp-content/uploads/2023/06/Senao-Networks-logo.png"
)


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
        or record.get("effective_target_mbps")
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
        return "<span class='tput-zero'>0.0 Mbps</span>"
    pct = _throughput_pct_of_rate(measured, target_mbps)
    if pct >= 70:
        css = "tput-good"
    elif pct >= 50:
        css = "tput-warn"
    else:
        css = "tput-bad"
    return f"<span class='{css}'>{measured:.1f} Mbps</span>"


def _rate_cell(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text or text == "-":
        return "—"
    return escape(text)


def _parse_rate_mbps_from_display(raw: str | None) -> float | None:
    text = str(raw or "").strip()
    if not text or text in {"-", "—"}:
        return None
    match = re.search(r"([\d.]+)", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _rate_matches_expected(
    actual_mbps: float | None,
    expected_mbps: float,
    *,
    tolerance_mbps: float = 10.0,
    tolerance_pct: float = 0.08,
) -> bool:
    if actual_mbps is None or expected_mbps <= 0:
        return True
    delta = abs(actual_mbps - expected_mbps)
    return delta <= tolerance_mbps or delta <= expected_mbps * tolerance_pct


def _operating_rate_cell(
    raw: str | None,
    expected_mbps: float,
    *,
    tolerance_mbps: float = 10.0,
    tolerance_pct: float = 0.08,
) -> str:
    text = str(raw or "").strip()
    if not text or text == "-":
        return "—"
    actual = _parse_rate_mbps_from_display(text)
    inner = escape(text)
    if actual is not None and expected_mbps > 0:
        if not _rate_matches_expected(
            actual,
            expected_mbps,
            tolerance_mbps=tolerance_mbps,
            tolerance_pct=tolerance_pct,
        ):
            return f'<span class="rate-mismatch">{inner}</span>'
    return inner


def _result_badge(record: dict[str, Any]) -> str:
    if record.get("skipped_trex"):
        return "<span class='badge fail'>SKIP</span>"
    passed = record.get("throughput_passed")
    if passed is True:
        return "<span class='badge pass'>PASS</span>"
    if passed is False:
        return "<span class='badge fail'>FAIL</span>"
    return "<span class='badge neutral'>—</span>"


def _throughput_row_values(record: dict[str, Any]) -> dict[str, Any]:
    stats = record.get("stats") or {}
    spec, operating_rate = _resolve_row_spec(record)
    ratio = str(record.get("ratio") or "50:50")
    dl_target, ul_target, bidi_target = _direction_targets(
        float(record.get("effective_target_mbps") or operating_rate),
        ratio,
    )
    combined = stats.get("combined") or {}
    downlink = stats.get("downlink") or {}
    uplink = stats.get("uplink") or {}
    return {
        "bandwidth": str(record.get("bandwidth") or "—"),
        "mcs": str(record.get("mcs") or spec.get("mcs") or "—"),
        "modulation": str(spec.get("modulation") or "—"),
        "operating_rate": operating_rate,
        "ratio": ratio,
        "effective_target": float(record.get("effective_target_mbps") or bidi_target or 0),
        "dl_mbps": float(downlink.get("rx_mbps") or 0),
        "ul_mbps": float(uplink.get("rx_mbps") or 0),
        "bidi_mbps": float(combined.get("rx_mbps") or 0),
        "dl_target": dl_target,
        "ul_target": ul_target,
        "bidi_target": bidi_target,
        "skipped": bool(record.get("skipped_trex")),
        "error": str(record.get("error") or ""),
    }


def _ratio_sheet_label(ratio: str) -> str:
    return str(ratio).replace(":", "-")


def _parse_tput_mbps(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        match = re.search(r"([\d.]+)", text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None


def _trex_device_stats(stats: dict[str, Any], su_index: int) -> dict[str, Any]:
    trex = stats.get("trex") or {}
    by_device = trex.get("summary_by_device") or {}
    row = dict(by_device.get(f"SU{su_index}") or {})
    if row.get("avg_tx_mbps") is None:
        tx_values: list[float] = []
        for sample in trex.get("live_samples") or []:
            device = (sample.get("devices") or {}).get(f"SU{su_index}") or {}
            if isinstance(device, dict) and device.get("tx_mbps") is not None:
                tx_values.append(float(device["tx_mbps"]))
        if tx_values:
            row["avg_tx_mbps"] = sum(tx_values) / len(tx_values)
    return row


def _unit_ip_cell(unit: str, ip: str) -> str:
    name = escape(str(unit))
    addr = escape(str(ip))
    if not addr or addr in {"—", "-"}:
        return f'<span class="unit-line">{name}</span>'
    return (
        f'<span class="unit-line">{name}</span>'
        f'<span class="unit-ip">{addr}</span>'
    )


def _mcs_for_unit(record: dict[str, Any], *, su_index: int | None = None, label: str = "") -> str:
    mcs_config = record.get("mcs_config") or {}
    for check in mcs_config.get("checks") or []:
        if su_index is not None and check.get("su_index") == su_index:
            return str(check.get("actual_mcs") or "—")
        if label and str(check.get("label")) == label:
            return str(check.get("actual_mcs") or "—")
    if su_index == 0 or label == "BTS":
        return str(mcs_config.get("expected_uci_mcs") or record.get("mcs") or "—").replace("MCS", "")
    return "—"


def _mcs_display_cell(record: dict[str, Any], mcs_raw: str) -> str:
    num = str(mcs_raw or "").strip().replace("MCS", "")
    if not num or num in {"—", "?", "-"}:
        return "—"
    mcs_label = f"MCS{num}"
    try:
        modulation = modulation_scheme(f"MCS{num}")
    except (TypeError, ValueError):
        link = record.get("link_validation") or {}
        modulation = str((link.get("spec") or {}).get("modulation") or "")
    if modulation:
        return (
            f"<span class='mcs-label'>{escape(mcs_label)}</span>"
            f"<br/><span class='modulation'>{escape(modulation)}</span>"
        )
    return f"<span class='mcs-label'>{escape(mcs_label)}</span>"


def _record_mcs_group_cell(record: dict[str, Any], row: dict[str, Any]) -> str:
    mcs = str(record.get("mcs") or row.get("mcs") or "").strip()
    num = mcs.replace("MCS", "").strip()
    return _mcs_display_cell(record, num)


def _chain_pair(first: Any, second: Any) -> str:
    a1 = str(first or "").strip()
    a2 = str(second or "").strip()
    if (not a1 or a1 == "-") and (not a2 or a2 == "-"):
        return "—"
    left = a1 if a1 and a1 != "-" else "—"
    right = a2 if a2 and a2 != "-" else "—"
    if right in {"—", "-"}:
        return left
    if left in {"—", "-"}:
        return right
    return f"{left}/{right}"


def _link_clients_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("link_validation_post", "link_validation"):
        clients = (record.get(key) or {}).get("clients") or []
        if clients:
            return list(clients)
    return []


def _unit_rows_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per connected CPE (sheet-style grouping)."""
    rows: list[dict[str, Any]] = []
    stats = record.get("stats") or {}
    _, expected_operating_rate = _resolve_row_spec(record)
    clients = _link_clients_for_record(record)
    for index, client in enumerate(clients, start=1):
        name = str(client.get("system_name") or client.get("name") or f"cpe{index}").strip()
        if name.upper().startswith("UBR630") or "BTS" in name.upper():
            continue
        trex_su = int(client.get("su_index") or client.get("sua_index") or index)
        trex_dev = _trex_device_stats(stats, trex_su)
        trex_dl = trex_dev.get("avg_rx_mbps")
        trex_ul = trex_dev.get("avg_tx_mbps")
        if trex_ul is None:
            trex_ul = _parse_tput_mbps(
                client.get("throughput_in_mbps") or client.get("rx_tput")
            )
        tx_raw = client.get("tx_rate") or client.get("out_rate")
        rx_raw = client.get("rx_rate") or client.get("in_rate")
        display_ip = str(client.get("ip") or "—")
        rows.append(
            {
                "unit": name if name != "-" else f"cpe{index}",
                "ip": display_ip if display_ip not in {"", "-"} else "—",
                "unit_ip": _unit_ip_cell(
                    name if name != "-" else f"cpe{index}",
                    display_ip if display_ip not in {"", "-"} else "—",
                ),
                "snr_local": _chain_pair(client.get("l_snr1"), client.get("l_snr2")),
                "snr_remote": _chain_pair(client.get("r_snr1"), client.get("r_snr2")),
                "rssi_local": _chain_pair(client.get("l_rssi1"), client.get("l_rssi2")),
                "rssi_remote": _chain_pair(client.get("r_rssi1"), client.get("r_rssi2")),
                "tx_rate": _operating_rate_cell(tx_raw, expected_operating_rate),
                "rx_rate": _operating_rate_cell(rx_raw, expected_operating_rate),
                "tx_traffic": f"{float(trex_dl):.1f}" if trex_dl is not None else "—",
                "rx_traffic": f"{float(trex_ul):.1f}" if trex_ul is not None else "—",
            }
        )
    if not rows:
        mcs_config = record.get("mcs_config") or {}
        for check in mcs_config.get("checks") or []:
            if str(check.get("role") or "").upper() != "CPE":
                continue
            index = int(check.get("su_index") or 0)
            if index <= 0:
                continue
            trex_dev = _trex_device_stats(stats, index)
            trex_dl = trex_dev.get("avg_rx_mbps")
            trex_ul = trex_dev.get("avg_tx_mbps")
            cpe_hosts = record.get("cpe_hosts") or []
            fallback_ip = (
                str(cpe_hosts[index - 1])
                if isinstance(cpe_hosts, list) and 0 < index <= len(cpe_hosts)
                else "—"
            )
            rows.append(
                {
                    "unit": f"cpe{index}",
                    "ip": fallback_ip,
                    "unit_ip": _unit_ip_cell(f"cpe{index}", fallback_ip),
                    "snr_local": "—",
                    "snr_remote": "—",
                    "rssi_local": "—",
                    "rssi_remote": "—",
                    "tx_rate": "—",
                    "rx_rate": "—",
                    "tx_traffic": f"{float(trex_dl):.1f}" if trex_dl is not None else "—",
                    "rx_traffic": f"{float(trex_ul):.1f}" if trex_ul is not None else "—",
                }
            )
    if not rows:
        rows.append(
            {
                "unit": "—",
                "ip": "—",
                "unit_ip": "—",
                "snr_local": "—",
                "snr_remote": "—",
                "rssi_local": "—",
                "rssi_remote": "—",
                "tx_rate": "—",
                "rx_rate": "—",
                "tx_traffic": "—",
                "rx_traffic": "—",
            }
        )
    return rows


def _render_throughput_matrix(records: list[dict[str, Any]]) -> str:
    """Sheet-style matrix: CPE rows per MCS, shared columns rowspan."""
    if not records:
        return ""

    col_count = 18
    body_rows: list[str] = []
    for rec_idx, record in enumerate(records):
        units = _unit_rows_for_record(record)
        row_span = len(units)
        row = _throughput_row_values(record)
        bandwidth = escape(row["bandwidth"])
        mimo = "Dual" if int(record.get("spatial_stream") or 2) >= 2 else "Single"
        packet = escape(str(record.get("packet_size") or "—"))
        mcs_group = _record_mcs_group_cell(record, row)
        ratio = escape(_ratio_sheet_label(row["ratio"]))
        duration = escape(str(record.get("duration_s") or "—"))
        noise = escape(str(record.get("noise_dbm") or "—"))

        if row["skipped"]:
            total_cell = "<span class='muted'>—</span>"
            remarks = escape(row["error"][:120] if row["error"] else "Skipped")
        else:
            total_cell = _throughput_cell(row["bidi_mbps"], row["bidi_target"])
            remarks = _result_badge(record)

        for idx, unit in enumerate(units):
            shared = ""
            if idx == 0:
                shared = f"""
            <td rowspan="{row_span}">{bandwidth}</td>
            <td rowspan="{row_span}">{escape(mimo)}</td>
            <td rowspan="{row_span}">{packet}</td>
            <td rowspan="{row_span}" class="mcs-group-cell">{mcs_group}</td>
            <td rowspan="{row_span}">{ratio}</td>
            <td rowspan="{row_span}">{noise}</td>
            <td rowspan="{row_span}">{duration}</td>
                """
            trailing = ""
            if idx == 0:
                trailing = f"""
            <td rowspan="{row_span}" class="total-cell">{total_cell}</td>
            <td rowspan="{row_span}" class="remarks-cell">{remarks}</td>
                """
            body_rows.append(
                f"""
          <tr>
            {shared}
            <td class="unit-cell">{unit['unit_ip']}</td>
            <td>{escape(str(unit['snr_local']))}</td>
            <td>{escape(str(unit['snr_remote']))}</td>
            <td>{escape(str(unit['rssi_local']))}</td>
            <td>{escape(str(unit['rssi_remote']))}</td>
            <td>{unit['tx_rate']}</td>
            <td>{unit['rx_rate']}</td>
            <td>{escape(str(unit['tx_traffic']))}</td>
            <td>{escape(str(unit['rx_traffic']))}</td>
            {trailing}
          </tr>
                """
            )
        if rec_idx < len(records) - 1:
            body_rows.append(f'<tr class="mcs-spacer"><td colspan="{col_count}"></td></tr>')

    return f"""
    <div class="table-block throughput-matrix-block">
      <div class="table-title">Throughput Matrix</div>
      <table class="matrix throughput-sheet">
          <colgroup>
            <col class="col-bw"/><col class="col-mimo"/><col class="col-pkt"/><col class="col-mcs-group"/>
            <col class="col-ratio"/><col class="col-noise"/><col class="col-dur"/><col class="col-unit"/>
            <col class="col-snr"/><col class="col-snr"/><col class="col-rssi"/><col class="col-rssi"/>
            <col class="col-rate"/><col class="col-rate"/><col class="col-tput"/><col class="col-tput"/>
            <col class="col-total"/><col class="col-remarks"/>
          </colgroup>
          <thead>
            <tr>
              <th rowspan="2">Bandwidth</th>
              <th rowspan="2">MIMO</th>
              <th rowspan="2">Packet<br/>Size</th>
              <th rowspan="2">MCS</th>
              <th rowspan="2">DL:UL<br/>Ratio</th>
              <th rowspan="2">Noise Floor<br/><span class="muted">dBm</span></th>
              <th rowspan="2">Duration<br/><span class="muted">s</span></th>
              <th rowspan="2">Unit / IP</th>
              <th colspan="2">SNR</th>
              <th colspan="2">RSSI</th>
              <th rowspan="2">Tx Data Rate<br/><span class="muted">Mb/s</span></th>
              <th rowspan="2">Rx Data Rate<br/><span class="muted">Mb/s</span></th>
              <th rowspan="2">Tx Traffic<br/><span class="muted">Mb/s</span></th>
              <th rowspan="2">Rx Traffic<br/><span class="muted">Mb/s</span></th>
              <th rowspan="2">Total Throughput<br/><span class="muted">Mb/s</span></th>
              <th rowspan="2">Remarks</th>
            </tr>
            <tr>
              <th>Local A1/A2</th>
              <th>Remote A1/A2</th>
              <th>Local A1/A2</th>
              <th>Remote A1/A2</th>
            </tr>
          </thead>
          <tbody>
            {''.join(body_rows)}
          </tbody>
        </table>
    </div>
    """


def _render_results_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p>No performance iterations recorded.</p>"
    return _render_throughput_matrix(records)


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
            "<strong>No iterations recorded.</strong>"
            "</div>"
        )
    passed = sum(1 for row in records if row.get("passed"))
    if passed == total:
        css = "pass"
        headline = f"<strong>{passed}/{total} passed</strong>"
    elif passed == 0:
        css = "fail"
        headline = f"<strong>0/{total} passed</strong>"
    else:
        css = "warn"
        headline = f"<strong>{passed}/{total} passed</strong>"
    return f'<div class="run-outcome {css}">{headline}</div>'


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
    for key in ("Bandwidths", "MCS Rates", "Ratios", "Duration (s)"):
        if key in run_meta:
            meta_lines.append(f"<span><strong>{escape(key)}:</strong> {escape(str(run_meta[key]))}</span>")

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
    body {{ margin: 0; padding: 20px 10px; font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); }}
    .wrap {{ width: 100%; max-width: none; margin: 0 auto; }}
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #2563eb 100%);
      color: #fff; border-radius: 14px; padding: 22px 26px; margin-bottom: 18px;
      display: flex; justify-content: space-between; align-items: center; gap: 20px;
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .hero-date {{ margin: 0; font-size: 14px; opacity: 0.92; }}
    .hero-logo {{ background: #fff; border-radius: 10px; padding: 10px 14px; }}
    .hero-logo img {{ display: block; height: 42px; }}
    .panel-top, .panel {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px; margin-bottom: 16px;
    }}
    .panel-top h2, .panel > h2 {{ margin: 0 0 14px; font-size: 17px; color: var(--title); }}
    table.data-table {{ width: 100%; border-collapse: collapse; }}
    table.data-table th, table.data-table td {{
      border: 1px solid var(--border); padding: 10px 14px; text-align: center;
    }}
    table.data-table th.row-label {{ text-align: left; background: #f8fafc; }}
    .run-meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; font-size: 13px; }}
    .run-meta span {{ padding: 8px 12px; background: #f8fafc; border-radius: 8px; border: 1px solid var(--border); }}
    .run-outcome {{ margin-bottom: 16px; padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border); }}
    .run-outcome.pass {{ background: #f0fdf4; border-color: #bbf7d0; color: #166534; }}
    .run-outcome.fail {{ background: #fff5f5; border-color: #fecaca; color: #991b1b; }}
    .run-outcome.warn {{ background: #fffbeb; border-color: #fde68a; color: #92400e; }}
    .iteration-block {{ margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }}
    .iteration-block:last-child {{ border-bottom: none; }}
    .iteration-title {{ margin: 0 0 12px; font-size: 15px; color: var(--head); }}
    .table-block {{ margin-bottom: 18px; }}
    .table-title {{ font-size: 13px; font-weight: 700; color: var(--head); margin: 0 0 8px; text-transform: uppercase; }}
    table.matrix {{ width: 100%; border-collapse: collapse; background: #fff; border: 2px solid var(--border); }}
    table.matrix th, table.matrix td {{ border: 1px solid var(--border); padding: 11px 12px; text-align: center; }}
    table.matrix thead th {{ background: #f8fafc; color: var(--head); font-size: 12px; font-weight: 700; }}
    table.matrix th.row-label {{ text-align: left; background: #f8fafc; width: 18%; }}
    table.summary-table td {{ text-align: left; }}
    table.throughput-sheet {{
      width: 100%;
      table-layout: fixed;
    }}
    table.throughput-sheet col.col-bw {{ width: 6%; }}
    table.throughput-sheet col.col-mimo {{ width: 4%; }}
    table.throughput-sheet col.col-pkt {{ width: 4%; }}
    table.throughput-sheet col.col-mcs-group {{ width: 8%; }}
    table.throughput-sheet col.col-ratio {{ width: 5%; }}
    table.throughput-sheet col.col-noise {{ width: 5%; }}
    table.throughput-sheet col.col-dur {{ width: 4%; }}
    table.throughput-sheet col.col-unit {{ width: 18%; }}
    table.throughput-sheet col.col-snr {{ width: 5%; }}
    table.throughput-sheet col.col-rssi {{ width: 5%; }}
    table.throughput-sheet col.col-rate {{ width: 7%; }}
    table.throughput-sheet col.col-tput {{ width: 5%; }}
    table.throughput-sheet col.col-total {{ width: 9%; }}
    table.throughput-sheet col.col-remarks {{ width: 6%; }}
    table.throughput-sheet th,
    table.throughput-sheet td {{
      padding: 6px 5px;
      font-size: 12px;
      white-space: normal;
      line-height: 1.3;
      word-break: break-word;
    }}
    table.throughput-sheet thead th {{
      background: #f4a261; color: #1a1a1a; font-size: 11px; font-weight: 700;
    }}
    table.throughput-sheet td[rowspan] {{ background: #fffbeb; font-weight: 600; vertical-align: middle; }}
    table.throughput-sheet .unit-line {{
      display: block;
      font-weight: 600;
      text-align: left;
      font-size: 10px;
      line-height: 1.25;
    }}
    table.throughput-sheet .unit-ip {{
      display: block;
      font-family: Consolas, Monaco, monospace;
      font-weight: 400;
      font-size: 9px;
      line-height: 1.35;
      word-break: break-all;
      white-space: normal;
      color: #334155;
      margin-top: 2px;
    }}
    table.throughput-sheet .mcs-group-cell {{ line-height: 1.2; vertical-align: middle; }}
    table.throughput-sheet .mcs-label {{ font-weight: 700; }}
    table.throughput-sheet .modulation {{ color: #64748b; font-size: 10px; }}
    table.throughput-sheet .rate-mismatch {{
      color: #991b1b; font-weight: 700; background: #fee2e2;
      padding: 1px 5px; border-radius: 3px; display: inline-block;
    }}
    table.throughput-sheet .total-cell {{ font-size: 11px; }}
    table.throughput-sheet .remarks-cell {{ font-size: 11px; }}
    table.throughput-sheet tr.mcs-spacer td {{
      height: 14px; padding: 0; border: none; background: var(--bg);
    }}
    .throughput-matrix-block {{ overflow: visible; }}
    .ip-cell {{ font-family: Consolas, Monaco, monospace; font-size: 11px; white-space: nowrap; }}
    .sheet-scroll {{ overflow-x: auto; }}
    .muted {{ color: #64748b; font-size: 11px; }}
    .tput-good {{ color: #166534; font-weight: 700; background: #dcfce7; padding: 2px 8px; border-radius: 4px; }}
    .tput-warn {{ color: #9a3412; font-weight: 700; background: #ffedd5; padding: 2px 8px; border-radius: 4px; }}
    .tput-bad {{ color: #991b1b; font-weight: 700; background: #fee2e2; padding: 2px 8px; border-radius: 4px; }}
    .tput-zero {{ color: #991b1b; font-weight: 700; background: #fecaca; padding: 2px 8px; border-radius: 4px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .badge.pass {{ background: #dcfce7; color: #166534; }}
    .badge.fail {{ background: #fee2e2; color: #991b1b; }}
    .badge.neutral {{ background: #e2e8f0; color: #475569; }}
    .skip-note {{ color: #991b1b; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div>
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
      <div class="run-meta">{''.join(meta_lines)}</div>
    </section>

    <section class="panel">
      <h2>Performance Results</h2>
      {outcome_banner}
      {results_table}
    </section>
  </div>
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")
    return path
