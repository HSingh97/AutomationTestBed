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


def _chain_pair(a1: Any, a2: Any) -> str:
    """Format antenna-chain pair as ``a1/a2`` (em-dash when missing)."""
    left = str(a1 or "").strip() or "—"
    right = str(a2 or "").strip() or "—"
    if left in {"-", "0"}:
        left = "—"
    if right in {"-", "0"}:
        right = "—"
    return f"{left}/{right}"


def _client_local_snr(client: dict[str, Any]) -> str:
    return _chain_pair(client.get("l_snr1"), client.get("l_snr2"))


def _client_remote_snr(client: dict[str, Any]) -> str:
    return _chain_pair(client.get("r_snr1"), client.get("r_snr2"))


def _client_snr(client: dict[str, Any]) -> str:
    """Backward-compatible remote SNR a1/a2 (primary RF quality signal)."""
    return _client_remote_snr(client)


def _client_rssi_combined(client: dict[str, Any]) -> str:
    """Combined RSSI as Local/Remote (comb_rssi / r_comb_rssi)."""
    local = str(client.get("l_rssi1") or "").strip() or "—"
    remote = str(client.get("r_rssi1") or "").strip() or "—"
    if local in {"-", "0"}:
        local = "—"
    if remote in {"-", "0"}:
        remote = "—"
    return f"{local}/{remote}"


def _client_rssi(client: dict[str, Any]) -> str:
    """Backward-compatible single RSSI — prefer remote, else local."""
    remote = str(client.get("r_rssi1") or "").strip()
    local = str(client.get("l_rssi1") or "").strip()
    for value in (remote, local):
        if value and value not in {"-", "0", "—"}:
            return value
    return "—"


def _client_rx_mcs(client: dict[str, Any]) -> str:
    raw = (
        client.get("rx_rate_mcs")
        or client.get("in_mcs")
        or client.get("operating_mcs")
        or client.get("actual_mcs")
        or ""
    )
    text = str(raw).strip()
    if not text or text in {"—", "-"}:
        # Fall back to parenthetical MCS in rx_rate like "258 (22)"
        rate = str(client.get("rx_rate") or client.get("in_rate") or "")
        match = re.search(r"\((\d+)\)", rate)
        return match.group(1) if match else "—"
    return re.sub(r"^MCS", "", text, flags=re.IGNORECASE)


def _fmt_su_mbps(value: Any) -> str:
    num = _parse_float(value)
    if num is None:
        return "—"
    return f"{num:.1f}"


def _trex_su_stats(stats: dict[str, Any], su_index: int) -> dict[str, Any]:
    """Per-SU TRex averages from summary_by_device, with live-sample fallback."""
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
    if row.get("avg_rx_mbps") is None:
        rx_values: list[float] = []
        for sample in trex.get("live_samples") or []:
            device = (sample.get("devices") or {}).get(f"SU{su_index}") or {}
            if isinstance(device, dict) and device.get("rx_mbps") is not None:
                rx_values.append(float(device["rx_mbps"]))
        if rx_values:
            row["avg_rx_mbps"] = sum(rx_values) / len(rx_values)
    return row


def _su_rf_rows(
    clients: list[dict[str, Any]],
    *,
    stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stats = stats or {}
    for client in sorted(
        clients,
        key=lambda row: int(row.get("su_index") or row.get("sua_index") or 0),
    ):
        su_index = int(client.get("su_index") or client.get("sua_index") or 0)
        label = f"SU{su_index}" if su_index else "SU"
        trex_dev = _trex_su_stats(stats, su_index) if su_index else {}
        rows.append(
            {
                "label": label,
                "mac": str(client.get("mac") or "—").strip() or "—",
                "tx_rate": str(client.get("tx_rate") or client.get("out_rate") or "—"),
                "rx_rate": str(client.get("rx_rate") or client.get("in_rate") or "—"),
                "rx_mcs": _client_rx_mcs(client),
                "tx_mbps": _fmt_su_mbps(trex_dev.get("avg_tx_mbps")),
                "rx_mbps": _fmt_su_mbps(trex_dev.get("avg_rx_mbps")),
                "local_snr": _client_local_snr(client),
                "remote_snr": _client_remote_snr(client),
                "snr": _client_remote_snr(client),
                "rssi": _client_rssi_combined(client),
                "rssi_local": str(client.get("l_rssi1") or "—"),
                "rssi_remote": str(client.get("r_rssi1") or "—"),
            }
        )
    return rows


def _snr_rssi_summaries(su_rf: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (snr_summary, rssi_summary) — remote SNR min/avg and RSSI Local/Remote worst."""
    snr_mins: list[float] = []
    snr_avgs: list[float] = []
    local_rssi: list[float] = []
    remote_rssi: list[float] = []
    for row in su_rf:
        parts = [
            p
            for p in str(row.get("remote_snr") or row.get("snr") or "").split("/")
            if p and p != "—"
        ]
        nums = [_parse_float(p) for p in parts]
        nums = [n for n in nums if n is not None]
        if nums:
            snr_mins.append(min(nums))
            snr_avgs.append(sum(nums) / len(nums))
        rssi_text = str(row.get("rssi") or "")
        if "/" in rssi_text:
            left, right = rssi_text.split("/", 1)
            local = _parse_float(left)
            remote = _parse_float(right)
        else:
            local = _parse_float(row.get("rssi_local"))
            remote = _parse_float(row.get("rssi_remote") or row.get("rssi"))
        if local is not None:
            local_rssi.append(local)
        if remote is not None:
            remote_rssi.append(remote)
    if snr_mins:
        snr_summary = f"{min(snr_mins):.0f}/{sum(snr_avgs) / len(snr_avgs):.0f}"
    else:
        snr_summary = "—"
    if local_rssi or remote_rssi:
        local_part = f"{min(local_rssi):.0f}" if local_rssi else "—"
        remote_part = f"{min(remote_rssi):.0f}" if remote_rssi else "—"
        rssi_summary = f"{local_part}/{remote_part}"
    else:
        rssi_summary = "—"
    return snr_summary, rssi_summary


def _rf_from_record(record: dict[str, Any]) -> dict[str, Any]:
    stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
    for key in ("link_validation_post", "link_validation"):
        clients = (record.get(key) or {}).get("clients") or []
        if clients:
            su_rf = _su_rf_rows(list(clients), stats=stats)
            snr_summary, rssi_summary = _snr_rssi_summaries(su_rf)
            return {
                "su_rf": su_rf,
                "snr_summary": snr_summary,
                "rssi_summary": rssi_summary,
            }
    return {"su_rf": [], "snr_summary": "—", "rssi_summary": "—"}


def _wanted_mcs_num(mcs: str) -> str:
    return re.sub(r"^MCS", "", str(mcs or "").strip(), flags=re.IGNORECASE) or "—"


def _enrich_mismatch_remark(
    note: str,
    *,
    wanted_mcs: str,
    su_rf: list[dict[str, Any]],
    mcs_config: dict[str, Any] | None = None,
) -> str:
    """Attach expected vs actual MCS and SNR when a mismatch note is present."""
    text = (note or "").strip()
    if not text or "mismatch" not in text.lower():
        return text or "—"

    want = _wanted_mcs_num(wanted_mcs)
    details: list[str] = []

    # Prefer verify checks (role/label + actual_mcs) when available
    checks = (mcs_config or {}).get("checks") or []
    bad_checks = [row for row in checks if not row.get("ok")]
    if bad_checks:
        for row in bad_checks:
            label = str(row.get("label") or row.get("role") or "SU")
            got = _wanted_mcs_num(str(row.get("actual_mcs") or "?"))
            snr = "—"
            for rf in su_rf:
                if rf["label"] == label or label.endswith(rf["label"]):
                    snr = rf.get("snr") or "—"
                    if got in {"?", "—"}:
                        got = str(rf.get("rx_mcs") or got)
                    break
            details.append(f"{label}: want MCS{want} got MCS{got} (SNR {snr})")
    elif su_rf:
        for rf in su_rf:
            got = str(rf.get("rx_mcs") or "—")
            if got != want and got != "—":
                details.append(
                    f"{rf['label']}: want MCS{want} got MCS{got} (SNR {rf.get('snr') or '—'})"
                )

    if details:
        return "MCS mismatch — " + "; ".join(details)
    return text


def _link_device_rows(link_clients: list[dict[str, Any]], *, prefix_len: int = 120) -> list[dict[str, Any]]:
    """Inventory-style rows (still used for testbed MAC merge / payload compat)."""
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
                "local_snr": _client_local_snr(client),
                "remote_snr": _client_remote_snr(client),
                "snr": _client_remote_snr(client),
                "rssi": _client_rssi_combined(client),
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
        mcs_label = str(record.get("mcs") or "")
        rf = _rf_from_record(record)
        note = str(
            record.get("mcs_mismatch_note")
            or (record.get("mcs_config") or {}).get("mcs_mismatch_note")
            or record.get("error")
            or ""
        ).strip()
        if skipped and not note:
            note = "TRex skipped"
        note = _enrich_mismatch_remark(
            note,
            wanted_mcs=mcs_label,
            su_rf=rf["su_rf"],
            mcs_config=record.get("mcs_config") if isinstance(record.get("mcs_config"), dict) else None,
        )
        cells.append(
            {
                "iter_key": rec_idx,
                "bandwidth": str(record.get("bandwidth") or ""),
                "mcs": mcs_label,
                "tested": True,
                "skipped": skipped,
                "passed": bool(record.get("passed")),
                "tx_mbps": tx_mbps,
                "rx_mbps": rx_mbps,
                "target_mbps": target_mbps,
                "pct": _pct(rx_mbps, target_mbps) if not skipped else 0.0,
                "remark": note or "—",
                "snr_summary": rf["snr_summary"],
                "rssi_summary": rf["rssi_summary"],
                "su_rf": rf["su_rf"],
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
            "snr_summary": cell["snr_summary"],
            "rssi_summary": cell["rssi_summary"],
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


def _render_coverage_matrix(data: dict[str, Any]) -> str:
    bandwidths = data.get("bandwidths") or ["HT20", "HT40", "HT80"]
    mcs_rates = data.get("mcs_rates") or []
    cells = data.get("matrix_cells") or []
    by_key = {(c["bandwidth"], c["mcs"]): c for c in cells}

    header_cells = "".join(
        f"<th class='heat-mcs'>{escape(mcs.replace('MCS', ''))}</th>" for mcs in mcs_rates
    )
    rows_html = []
    for bw in bandwidths:
        cell_html = []
        for mcs in mcs_rates:
            cell = by_key.get((bw, mcs))
            if not cell:
                cell_html.append("<td class='heat-cell heat-empty'>—</td>")
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
            snr = cell.get("snr_summary") or "—"
            rx_label = "—" if cell.get("skipped") else f"{rx:.0f}"
            title = (
                f"{bw} {mcs}: skipped"
                if cell.get("skipped")
                else f"{bw} {mcs}: {tx:.0f} sent → {rx:.0f} got ({pct:.0f}% target) · SNR {snr}"
            )
            cell_html.append(
                f"<td><button type='button' class='heat-cell heat-{status}' "
                f"data-iter-key='{cell['iter_key']}' title='{escape(title)}'>"
                f"<span class='heat-rx'>{rx_label}</span>"
                f"</button></td>"
            )
        rows_html.append(
            f"<tr><th class='heat-bw'>{escape(bw)}</th>{''.join(cell_html)}</tr>"
        )

    return f"""
    <div class="table-scroll heat-scroll" id="coverageMatrix">
      <table class="heat-table">
        <thead>
          <tr><th class="heat-corner">BW \\ MCS</th>{header_cells}</tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    <div class="cov-legend">
      <span><i class="lg pass"></i> Pass</span>
      <span><i class="lg fail"></i> Fail</span>
      <span><i class="lg skip"></i> Skipped</span>
      <span><i class="lg empty"></i> Not tested</span>
      <span class="legend-note">Cell value = RX Mbps · hover for TX→RX and SNR · click for detail</span>
    </div>
    """


def _render_kpi_cards(data: dict[str, Any], *, passed: int, ran: int, total: int, peak_rx: float, pass_cls: str) -> str:
    peaks = data.get("peaks") or {}
    best_bw = max(peaks.items(), key=lambda item: float((item[1] or {}).get("rx_mbps") or 0))[0] if peaks else "—"
    mcs_rates = data.get("mcs_rates") or []
    bandwidths = data.get("bandwidths") or []
    coverage_sub = f"{len(bandwidths)} BW × {len(mcs_rates)} MCS" if mcs_rates else "matrix cells"
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
          <div class="kpi-sub">From testbed inventory</div>
        </div>
      </article>
      <article class="kpi-card accent-neutral">
        <div class="kpi-icon">▦</div>
        <div class="kpi-body">
          <div class="kpi-label">Coverage</div>
          <div class="kpi-value">{total}<span>cells</span></div>
          <div class="kpi-sub">{escape(coverage_sub)}</div>
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

    .heat-scroll { overflow: auto; border-radius: var(--radius-sm); border: 1px solid var(--panel-border); }
    table.heat-table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 4px;
      font-size: 12px;
      background: #ffffff;
      min-width: max-content;
    }
    table.heat-table th, table.heat-table td {
      padding: 0;
      text-align: center;
      vertical-align: middle;
    }
    .heat-corner, .heat-bw {
      position: sticky;
      left: 0;
      z-index: 2;
      background: #f8fafc;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      padding: 8px 10px !important;
      white-space: nowrap;
    }
    .heat-mcs {
      font-size: 10px;
      font-weight: 700;
      color: var(--muted);
      padding: 6px 4px !important;
      min-width: 40px;
    }
    .heat-cell {
      appearance: none;
      display: grid;
      place-items: center;
      width: 100%;
      min-width: 40px;
      height: 40px;
      border-radius: 8px;
      border: 1px solid var(--panel-border);
      background: #f8fafc;
      color: var(--text);
      cursor: pointer;
      font-weight: 800;
      font-size: 12px;
      padding: 0;
      transition: transform .12s ease, box-shadow .12s ease, outline-color .12s ease;
    }
    .heat-cell:hover, .heat-cell.selected {
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(15,23,42,0.08);
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    td.heat-cell.heat-empty, .heat-empty {
      display: grid;
      place-items: center;
      min-width: 40px;
      height: 40px;
      border-radius: 8px;
      color: var(--muted);
      background: #f1f5f9;
      border: 1px dashed #cbd5e1;
      cursor: default;
    }
    .heat-pass { background: #ecfdf5; border-color: #6ee7b7; color: #047857; }
    .heat-fail { background: #fef2f2; border-color: #fca5a5; color: #b91c1c; }
    .heat-skip { background: #fffbeb; border-color: #fcd34d; color: #b45309; }
    .heat-rx { line-height: 1; }

    .cov-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
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
    .legend-note { margin-left: auto; font-size: 11px; }
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
      padding: 8px 18px 16px;
      min-height: 280px;
      background: #f8fafc;
    }
    .cell-rf {
      padding: 0 18px 20px;
    }
    .cell-rf h3 {
      margin: 0 0 10px;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #475569;
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
        snr = cell.get("snr_summary") or "—"
        rssi = cell.get("rssi_summary") or "—"
        rssi_cell = "—" if cell.get("skipped") or rssi == "—" else (f"{rssi} dBm" if "/" in str(rssi) or str(rssi).startswith("-") or str(rssi)[0].isdigit() else str(rssi))
        iter_rows.append(
            f"<tr data-iter-key='{cell['iter_key']}' "
            f"class='{'row-skip' if cell.get('skipped') else 'row-pass' if cell.get('passed') else 'row-fail'}'>"
            f"<td><strong>{escape(cell['bandwidth'])}</strong></td>"
            f"<td>{escape(cell['mcs'])}</td>"
            f"<td>{'—' if cell.get('skipped') else format(float(cell.get('tx_mbps') or 0), '.1f')}</td>"
            f"<td>{'—' if cell.get('skipped') else format(float(cell.get('rx_mbps') or 0), '.1f')}</td>"
            f"<td>{float(cell.get('target_mbps') or 0):.1f}</td>"
            f"<td>{escape(str(snr))}</td>"
            f"<td>{escape(rssi_cell)}</td>"
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
          <p class="panel-desc">BTS and SU inventory — model, firmware, MAC, and management IPv6 (/120).</p>
        </div>
      </div>
      {_render_testbed_table(data)}
    </section>

    <section class="panel span-12">
      {_render_kpi_cards(data, passed=passed, ran=ran, total=total, peak_rx=peak_rx, pass_cls=pass_cls)}
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Coverage matrix</h2>
          <p class="panel-desc">Compact BW × MCS heatmap. Cell value is RX Mbps. Click a cell for live chart and per-SU RF (SNR/RSSI) from that iteration.</p>
        </div>
      </div>
      {_render_coverage_matrix(data)}
    </section>

    <section class="panel span-12 chart-panel" id="chartSection">
      <div class="chart-head">
        <h2>Cell detail</h2>
        <div class="series-title" id="seriesTitle">Select a matrix cell</div>
        <div class="series-meta" id="seriesMeta">Click a heatmap cell to plot TRex live samples and show RF for that MCS.</div>
      </div>
      <div class="chart-wrap">
        <canvas id="timeSeriesChart"></canvas>
      </div>
      <div class="cell-rf">
        <h3>RF snapshot for selected cell</h3>
        <div class="table-scroll">
          <table class="data-table" id="cellRfTable">
            <thead>
              <tr>
                <th>Unit</th><th>MAC</th>
                <th>Downlink rate</th><th>Uplink rate</th>
                <th>Uplink Mbps</th><th>Downlink Mbps</th>
                <th>Local SNR<br><span style="font-weight:500;opacity:.75">a1/a2</span></th>
                <th>Remote SNR<br><span style="font-weight:500;opacity:.75">a1/a2</span></th>
                <th>RSSI (combined)<br><span style="font-weight:500;opacity:.75">Local/Remote</span></th>
              </tr>
            </thead>
            <tbody id="cellRfBody">
              <tr><td colspan="9" class="empty-note" style="border:none">Select a cell to load per-SU RF.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="panel span-12">
      <div class="panel-head">
        <div>
          <h2>Iteration log</h2>
          <p class="panel-desc">Full matrix audit trail with TX/RX, per-cell SNR/RSSI summaries, and remarks.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table" id="iterLog">
          <thead>
            <tr>
              <th>BW</th><th>MCS</th><th>TX Mbps</th><th>RX Mbps</th><th>Target</th>
              <th>Remote SNR</th><th>RSSI L/R</th><th>Status</th><th>Remarks</th>
            </tr>
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
            "target_mbps": cell.get("target_mbps"),
            "snr_summary": cell.get("snr_summary") or "—",
            "rssi_summary": cell.get("rssi_summary") or "—",
            "su_rf": cell.get("su_rf") or [],
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

    function renderRfTable(suRf) {{
      const body = document.getElementById('cellRfBody');
      if (!suRf || !suRf.length) {{
        body.innerHTML = '<tr><td colspan="9"><p class="empty-note" style="border:none;margin:0">No RF snapshot for this cell.</p></td></tr>';
        return;
      }}
      body.innerHTML = suRf.map(row => `
        <tr>
          <td><strong>${{row.label || '—'}}</strong></td>
          <td class="mono">${{row.mac || '—'}}</td>
          <td>${{row.tx_rate || '—'}}</td>
          <td>${{row.rx_rate || '—'}}</td>
          <td>${{row.tx_mbps || '—'}}</td>
          <td>${{row.rx_mbps || '—'}}</td>
          <td>${{row.local_snr || '—'}}</td>
          <td>${{row.remote_snr || row.snr || '—'}}</td>
          <td>${{row.rssi && row.rssi !== '—' ? row.rssi + ' dBm' : '—'}}</td>
        </tr>`).join('');
    }}

    function selectCell(key) {{
      activeKey = String(key);
      document.querySelectorAll('.heat-cell[data-iter-key]').forEach(el => {{
        el.classList.toggle('selected', el.dataset.iterKey === activeKey);
      }});
      const meta = cellMeta[activeKey] || {{}};
      const series = seriesData[activeKey] || {{ labels: [], rx_mbps: [], loss_pct: [] }};
      document.getElementById('seriesTitle').textContent =
        meta.skipped ? `${{meta.bandwidth || '—'}} · ${{meta.mcs || '—'}} · skipped` :
        `${{meta.bandwidth || '—'}} · ${{meta.mcs || '—'}} · ${{(meta.tx_mbps || 0).toFixed(1)}} sent → ${{(meta.rx_mbps || 0).toFixed(1)}} Mbps got`;
      document.getElementById('seriesMeta').textContent =
        meta.skipped ? (meta.remark || 'TRex not run') :
        `${{series.labels.length}} live samples · Remote SNR ${{meta.snr_summary || '—'}} · RSSI ${{meta.rssi_summary && meta.rssi_summary !== '—' ? meta.rssi_summary + ' dBm' : '—'}} · loss from DUT avg_rtx or TX/RX PPS delta`;
      renderRfTable(meta.su_rf || []);
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
      const cell = event.target.closest('.heat-cell[data-iter-key]');
      if (!cell || cell.classList.contains('heat-empty')) return;
      selectCell(cell.dataset.iterKey);
    }});

    if (activeKey && seriesData[activeKey]) selectCell(activeKey);
  </script>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out
