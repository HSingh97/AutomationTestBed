"""Jumbo wire-capture index and Senao HTML report snippets."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ARTIFACTS_DIR = Path("reports/artifacts")
INDEX_FILENAME = "jumbo_captures_index.json"
_MAX_SUMMARY_ROWS = 8


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def index_path() -> Path:
    return _repo_root() / ARTIFACTS_DIR / INDEX_FILENAME


def _relative_artifact(path: str | Path) -> str:
    clean = Path(str(path))
    root = _repo_root() / ARTIFACTS_DIR
    try:
        return clean.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        try:
            return clean.relative_to(root).as_posix()
        except ValueError:
            return clean.name


def _bts_capture(metadata: dict) -> dict | None:
    for capture in metadata.get("captures") or []:
        if str(capture.get("node") or "").lower() == "bts":
            return capture
    return None


def build_index_entry(metadata: dict) -> dict:
    bts = _bts_capture(metadata) or {}
    configured_mtu = int(str(metadata.get("configured_mtu") or "0") or "0")
    min_wire = configured_mtu + 6 if configured_mtu else 0
    max_frame = int(bts.get("max_frame_len") or 0)
    ping_source = str(metadata.get("ping_source") or "pc")
    pc_max = int(metadata.get("pc_max_frame_len") or 0)
    return {
        "case_id": str(metadata.get("case_id") or "").upper(),
        "configured_mtu": str(metadata.get("configured_mtu") or ""),
        "target": str(metadata.get("target") or ""),
        "payload_size": int(metadata.get("payload_size") or 0),
        "ping_source": ping_source,
        "local_dir_rel": _relative_artifact(metadata.get("local_dir") or ""),
        "evidence_svg_rel": _relative_artifact(metadata.get("evidence_svg") or ""),
        "bts_pcap_rel": _relative_artifact(bts.get("local_pcap") or ""),
        "bts_summary_rel": _relative_artifact(bts.get("local_summary") or ""),
        "bts_packet_count": int(bts.get("packet_count") or 0),
        "bts_max_frame_len": max_frame,
        "pc_max_frame_len": pc_max,
        "jumbo_wire_ok": bool(max_frame >= min_wire) if min_wire else False,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def update_jumbo_capture_index(metadata: dict) -> dict:
    """Append one capture run to reports/artifacts/jumbo_captures_index.json."""
    case_id = str(metadata.get("case_id") or "").upper()
    if not case_id:
        return {}

    entry = build_index_entry(metadata)
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict = {"updated_at": entry["captured_at"], "cases": {}}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {"updated_at": entry["captured_at"], "cases": {}}

    cases = dict(payload.get("cases") or {})
    rows = list(cases.get(case_id) or [])
    rows.append(entry)
    cases[case_id] = rows
    payload["cases"] = cases
    payload["updated_at"] = entry["captured_at"]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return entry


def load_jumbo_capture_index() -> dict:
    path = index_path()
    if not path.is_file():
        return {"cases": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"cases": {}}


def parse_jmb_case_id(nodeid: str) -> str | None:
    fn = nodeid.split("::")[-1].lower()
    match = re.search(r"test_jmb_(\d+)", fn)
    if match:
        return f"JMB_{int(match.group(1)):02d}"
    for token in nodeid.replace("::", " ").split():
        marker = re.search(r"JMB_(\d+)", token, re.I)
        if marker:
            return f"JMB_{int(marker.group(1)):02d}"
    return None


def _render_summary_table(summary_rel: str) -> str:
    summary_path = _repo_root() / ARTIFACTS_DIR / summary_rel
    if not summary_path.is_file():
        return "<p class='capture-muted'>Summary file not available.</p>"

    lines = summary_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines or not lines[0].startswith("frame.number,"):
        preview = "<br/>".join(escape(line) for line in lines[:_MAX_SUMMARY_ROWS] if line.strip())
        return f"<pre class='capture-pre'>{preview or 'No summary lines.'}</pre>"

    reader = csv.DictReader(lines)
    rows = list(reader)[:_MAX_SUMMARY_ROWS]
    if not rows:
        return "<p class='capture-muted'>No parsed frames in summary.</p>"

    headers = list(rows[0].keys())
    head_html = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body_html = ""
    for row in rows:
        body_html += "<tr>" + "".join(f"<td>{escape(str(row.get(h, '')))}</td>" for h in headers) + "</tr>"

    return (
        "<table class='capture-table'><thead><tr>"
        f"{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
    )


def _latest_run_entries(entries: list[dict]) -> list[dict]:
    """
    Tail of the history covering the most recent test run: walk backwards
    collecting entries until a configured MTU repeats (multi-MTU cases such as
    JMB_01/JMB_03 log one capture per MTU within a single run).
    """
    seen: set[str] = set()
    latest: list[dict] = []
    for entry in reversed(entries):
        mtu = str(entry.get("configured_mtu") or "")
        if mtu in seen:
            break
        seen.add(mtu)
        latest.append(entry)
    return list(reversed(latest))


def render_jumbo_capture_evidence_html(case_id: str, index: dict | None = None) -> tuple[str, str]:
    """
    Return (html_fragment, csv_plain_text) for one JMB case.
    Paths in links are relative to reports/artifacts/ (same folder as the Senao HTML report).
    The block renders collapsed by default; readers expand it on demand.
    """
    data = index if index is not None else load_jumbo_capture_index()
    entries = list((data.get("cases") or {}).get(case_id.upper()) or [])
    if not entries:
        return "", ""

    entries = _latest_run_entries(entries)

    gist = " · ".join(
        f"MTU {escape(str(e.get('configured_mtu') or '?'))}: "
        f"{int(e.get('bts_packet_count') or 0)} pkts, max {int(e.get('bts_max_frame_len') or 0)}"
        for e in entries
    )
    html_parts = [
        "<details class='jumbo-capture-block'>",
        "<summary class='capture-summary'>"
        "<span class='capture-summary-title'>Wire Capture Evidence (tcpdump / tshark)</span>"
        f"<span class='capture-summary-gist'>{gist}</span>"
        "<span class='capture-summary-hint'>click to expand</span>"
        "</summary>",
    ]
    csv_lines = ["Wire capture evidence:"]

    for entry in entries:
        mtu = entry.get("configured_mtu") or "?"
        pkt = int(entry.get("bts_packet_count") or 0)
        max_len = int(entry.get("bts_max_frame_len") or 0)
        pc_max = int(entry.get("pc_max_frame_len") or 0)
        ping_src = entry.get("ping_source") or "pc"
        jumbo_ok = bool(entry.get("jumbo_wire_ok"))
        pcap_rel = entry.get("bts_pcap_rel") or ""
        summary_rel = entry.get("bts_summary_rel") or ""
        svg_rel = entry.get("evidence_svg_rel") or ""

        warn_html = "" if jumbo_ok else " · <span class='capture-warn'>not jumbo on wire</span>"
        html_parts.append(
            f"<div class='capture-run'>"
            f"<p class='capture-meta'><strong>MTU {escape(str(mtu))}</strong> · "
            f"BTS ICMP echo packets: <strong>{pkt}</strong> · max frame.len: <strong>{max_len}</strong> · "
            f"ping source: <strong>{escape(str(ping_src))}</strong>{warn_html}</p>"
        )
        if ping_src == "device" and pc_max and pc_max < 1600:
            expected_cmp = "≥" if jumbo_ok else "<"
            expected_len = int(mtu) + 14
            html_parts.append(
                "<p class='capture-note'>Lab PC ping was fragmented (~"
                f"{pc_max} byte frames, 0% replies). Device-originated ICMP from BTS "
                f"({expected_cmp}{expected_len} expected) is used for wire proof.</p>"
            )
        elif ping_src == "pc" and not jumbo_ok and max_len:
            html_parts.append(
                "<p class='capture-note'>Frames are near 1500 bytes — raise parent + VLAN MTU on the "
                "BTS lab PC (enp3s0 then enp3s0.101) before PC ping for end-to-end jumbo on the tap.</p>"
            )
        if pcap_rel:
            html_parts.append(
                f"<p class='capture-links'>"
                f"<a class='capture-link' href='{escape(pcap_rel)}' target='_blank' rel='noopener'>"
                f"Download BTS pcap</a>"
            )
            if summary_rel:
                html_parts.append(
                    f" · <a class='capture-link' href='{escape(summary_rel)}' target='_blank' rel='noopener'>"
                    f"frame summary CSV</a>"
                )
            html_parts.append("</p>")

        table_html = _render_summary_table(summary_rel) if summary_rel else ""
        if table_html:
            html_parts.append("<div class='capture-table-wrap'>" + table_html + "</div>")

        # The capture_evidence.svg snapshot duplicates the table above; keep it as a
        # downloadable artifact only instead of inlining it in the report.
        if svg_rel:
            html_parts.append(
                f"<p class='capture-links'><a class='capture-link' href='{escape(svg_rel)}' "
                f"target='_blank' rel='noopener'>capture evidence snapshot (SVG)</a></p>"
            )

        html_parts.append("</div>")
        csv_lines.append(
            f"- MTU {mtu}: {pkt} BTS packets, max frame.len {max_len}, pcap={pcap_rel or 'n/a'}"
        )

    html_parts.append("</details>")
    return "".join(html_parts), "\n".join(csv_lines)


JUMBO_CAPTURE_REPORT_CSS = """
            .jumbo-capture-block { margin-top: 14px; padding: 0; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }
            .capture-summary { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 11px 16px; cursor: pointer; user-select: none; list-style: none; background: #f1f5f9; }
            .capture-summary::-webkit-details-marker { display: none; }
            .capture-summary::before { content: '\\25B8'; color: #2563eb; font-size: 12px; transition: transform 0.15s ease; }
            details[open] > .capture-summary::before { transform: rotate(90deg); }
            details[open] > .capture-summary { border-bottom: 1px solid #e2e8f0; }
            .capture-summary:hover { background: #e2e8f0; }
            .capture-summary-title { font-weight: 700; font-size: 12px; color: #1e3a8a; }
            .capture-summary-gist { font-size: 11px; color: #475569; }
            .capture-summary-hint { margin-left: auto; font-size: 10px; color: #94a3b8; font-style: italic; }
            details[open] > .capture-summary .capture-summary-hint { display: none; }
            .jumbo-capture-block .capture-run { padding: 12px 16px; }
            .capture-run + .capture-run { margin-top: 16px; padding-top: 14px; border-top: 1px dashed #cbd5e1; }
            .capture-meta { margin: 0 0 8px; font-size: 12px; color: #475569; }
            .capture-links { margin: 0 0 10px; font-size: 12px; }
            .capture-link { color: #2563eb; font-weight: 600; text-decoration: none; }
            .capture-link:hover { text-decoration: underline; }
            .capture-table-wrap { overflow-x: auto; margin: 8px 0 10px; }
            .capture-table { width: 100%; border-collapse: collapse; font-size: 11px; font-family: Consolas, Monaco, monospace; }
            .capture-table th, .capture-table td { border: 1px solid #e2e8f0; padding: 4px 10px; text-align: left; white-space: nowrap; }
            .capture-table th { background: #f1f5f9; color: #1e3a8a; }
            .capture-svg-wrap { margin-top: 10px; overflow-x: auto; background: #1e1e1e; border-radius: 6px; border: 1px solid #334155; }
            .capture-svg-wrap svg { display: block; max-width: 100%; height: auto; }
            .capture-pre { margin: 8px 0 0; padding: 8px; background: #0f172a; color: #e2e8f0; border-radius: 6px; font-size: 11px; overflow-x: auto; }
            .capture-muted { margin: 6px 0 0; font-size: 12px; color: #64748b; }
            .capture-note { margin: 0 0 10px; font-size: 12px; color: #92400e; background: #fffbeb; border-left: 3px solid #f59e0b; padding: 8px 10px; border-radius: 4px; }
            .capture-warn { color: #b45309; font-weight: 600; }
"""
