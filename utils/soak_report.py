"""HTML report for long-running soak scripts, styled to match the Senao suite reports.

Rendered from scratch after every iteration so the file on disk is always a
valid, openable report while a multi-hour soak is still running.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from utils.senao_branding import senao_logo_src
from utils.soak_common import metric_stats


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value: object, kind: str) -> str:
    if value is None or value == "":
        return "—"
    if kind == "seconds":
        return f"{float(value):.1f}s"
    if kind == "bool":
        return "Yes" if value else "No"
    return str(value)


def _badge(passed: bool) -> str:
    if passed:
        return '<span class="badge" style="background:#dcfce7;color:#166534">PASS</span>'
    return '<span class="badge" style="background:#fee2e2;color:#991b1b">FAIL</span>'


def _check_pill(label: str, ok: object) -> str:
    if ok is None:
        return f'<span class="pill pill-na">{_esc(label)}</span>'
    cls = "pill-ok" if ok else "pill-bad"
    return f'<span class="pill {cls}">{_esc(label)}</span>'


def _tail(text: str, lines: int) -> str:
    rows = [row for row in str(text).strip().splitlines() if row.strip()]
    if len(rows) <= lines:
        return "\n".join(rows)
    return "\n".join(rows[-lines:])


def _logs_block(
    iteration: dict[str, Any],
    detail_fields: list[dict[str, str]],
    *,
    log_lines: int = 6,
) -> str:
    """One collapsed dropdown per iteration: checks, key numbers, and short log proof."""
    parts: list[str] = []

    checks = iteration.get("checks") or {}
    if checks:
        pills = "".join(_check_pill(label, value) for label, value in checks.items())
        parts.append(f"<div class='sec'>Checks</div><div>{pills}</div>")

    if detail_fields:
        cells = []
        for spec in detail_fields:
            source = iteration.get(spec.get("source", "metrics")) or {}
            rendered = _fmt(source.get(spec["key"]), spec.get("fmt", "text"))
            if rendered == "—":
                continue
            cells.append(
                f"<div class='kv'><span>{_esc(spec['label'])}</span><b>{_esc(rendered)}</b></div>"
            )
        if cells:
            parts.append(f"<div class='sec'>Key numbers</div><div class='kvs'>{''.join(cells)}</div>")

    for name, text in (iteration.get("logs") or {}).items():
        trimmed = _tail(text, log_lines)
        if not trimmed:
            continue
        parts.append(f"<div class='sec'>{_esc(name)}</div><pre>{_esc(trimmed)}</pre>")

    if not parts:
        return "<em class='muted'>No detail captured.</em>"
    return (
        "<details><summary>Show details</summary>"
        f"<div class='logbox'>{''.join(parts)}</div></details>"
    )


def generate_soak_report(
    state,
    output: Path,
    *,
    title: str,
    subtitle: str,
    columns: list[dict[str, str]],
    stat_cards: list[dict[str, str]] | None = None,
    detail_fields: list[dict[str, str]] | None = None,
) -> Path:
    iterations = state.iterations
    total = state.total_iterations
    done = state.completed
    passed = state.passed
    failed = state.failed

    body: list[str] = []
    span = len(columns) + 4
    for item in iterations:
        cells = []
        for col in columns:
            source = item.get(col.get("source", "metrics")) or {}
            cells.append(f"<td>{_fmt(source.get(col['key']), col.get('fmt', 'text'))}</td>")

        body.append(
            f"<tr data-status=\"{'PASS' if item.get('passed') else 'FAIL'}\">"
            f"<td class='tid'>#{_esc(item.get('index'))}</td>"
            f"<td><span class='role'>{_esc(item.get('target'))}</span></td>"
            f"<td class='ts'>{_esc(item.get('started_at'))}</td>"
            f"{''.join(cells)}"
            f"<td>{_badge(bool(item.get('passed')))}</td></tr>"
            f"<tr class='logrow'><td colspan='{span}'>"
            f"<div class='summary-line'>{_esc(item.get('summary'))}</div>"
            f"{_logs_block(item, detail_fields or [])}</td></tr>"
        )

    cards = [
        f'<div class="card total"><h3>{done}/{total}</h3><p>Iterations Run</p></div>',
        f'<div class="card pass"><h3>{passed}</h3><p>Passed</p></div>',
        f'<div class="card fail"><h3>{failed}</h3><p>Failed</p></div>',
    ]
    for spec in stat_cards or []:
        stats = metric_stats(iterations, spec["key"])
        avg = _fmt(stats["avg"], "seconds")
        lo = _fmt(stats["min"], "seconds")
        hi = _fmt(stats["max"], "seconds")
        cards.append(
            f'<div class="card stat"><h3>{avg}</h3><p>{_esc(spec["label"])}</p>'
            f'<span class="rng">min {lo} · max {hi}</span></div>'
        )

    testbed = state.testbed or {}
    bts = testbed.get("bts") or {}
    cpe = testbed.get("cpe") or {}

    def value(device: dict, *keys: str) -> str:
        for key in keys:
            if device.get(key) and device[key] != "—":
                return str(device[key])
        return "—"

    def model_name(device: dict) -> str:
        """Devices report themselves as EOCxxx but ship and are logged as UBRxxx."""
        raw = value(device, "model")
        return raw.replace("EOC", "UBR") if raw != "—" else raw

    headers = "".join(f"<th>{_esc(col['header'])}</th>" for col in columns)
    logo = senao_logo_src()
    pass_rate = f"{(passed / done * 100):.1f}%" if done else "—"

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{{--bg:#eef2f7;--card:#fff;--border:#e2e8f0;--title:#0f172a;--muted:#64748b;--pass:#10b981;--fail:#ef4444;--total:#1e3a8a}}
*{{box-sizing:border-box}} body{{font-family:Inter,sans-serif;background:var(--bg);color:#334155;margin:0;padding:28px 18px}}
.wrap{{max-width:min(1800px,98vw);margin:auto}} .hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a 60%,#2563eb);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center}}
.hero h1{{margin:0 0 6px;font-size:22px}} .hero p{{margin:0;font-size:13px}} .hero-logo{{background:#fff;border-radius:10px;padding:10px 14px}} .hero-logo img{{height:42px;display:block}}
.container{{background:#fff;border:1px solid #cbd5e1;border-radius:14px;overflow:hidden}} .cards{{display:grid;grid-template-columns:repeat({len(cards)},1fr);gap:10px;padding:16px 22px 6px;background:#f8fafc}}
.card{{padding:10px;text-align:center;border:1px solid var(--border);border-radius:10px;border-bottom:3px solid}} .card h3{{margin:0;font-size:22px}} .card p{{margin:5px 0 0;font-size:9px;text-transform:uppercase;font-weight:700}}
.card .rng{{display:block;margin-top:3px;font-size:9px;color:var(--muted);font-weight:500}}
.total{{color:var(--total)}} .pass{{color:var(--pass)}} .fail{{color:var(--fail)}} .stat{{color:#7c3aed}}
.panel{{padding:12px 28px 22px;background:#f8fafc;border-bottom:1px solid var(--border)}} .panel h2{{font-size:17px;color:var(--title)}}
.testbed{{width:100%;border-collapse:collapse;background:#fff}} .testbed th,.testbed td{{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left}} .testbed tbody th{{width:150px;color:var(--muted);font-size:11px;text-transform:uppercase}}
.role{{background:#dbeafe;color:#1e3a8a;border-radius:999px;padding:3px 8px;font-size:10px;font-weight:600}}
.meta{{margin-top:14px;font-size:13px}} .content{{padding:12px 20px 28px;overflow:auto}} .plan{{width:100%;border-collapse:collapse;font-size:13px}} .plan th{{background:#f8fafc;color:#1e3a8a;padding:12px 10px;border-bottom:2px solid #cbd5e1;text-align:left;font-size:11px}} .plan td{{padding:10px;border-bottom:1px solid var(--border);vertical-align:top}}
.tid{{font-family:monospace;color:#1e3a8a;font-weight:600;white-space:nowrap}} .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}}
.pill{{display:inline-block;padding:2px 7px;margin:1px 2px;border-radius:999px;font-size:10px;font-weight:600;white-space:nowrap}}
.pill-ok{{background:#dcfce7;color:#166534}} .pill-bad{{background:#fee2e2;color:#991b1b}} .pill-na{{background:#f1f5f9;color:#64748b}}
.logrow td{{background:#f8fafc;padding:4px 14px 10px}} .logrow details{{margin:2px 0}} .logrow summary{{cursor:pointer;font-size:11px;font-weight:600;color:#1e3a8a;padding:2px 0}}
.logbox{{margin-top:6px;border-left:2px solid #cbd5e1;padding-left:10px;max-width:1100px}}
.sec{{font-size:10px;font-weight:700;text-transform:uppercase;color:var(--muted);margin:8px 0 3px}}
.kvs{{display:flex;flex-wrap:wrap;gap:6px 18px}} .kv{{font-size:11px}} .kv span{{color:var(--muted);margin-right:5px}} .kv b{{color:#0f172a}}
.logrow pre{{background:#0f172a;color:#e2e8f0;padding:8px 10px;border-radius:6px;overflow:auto;max-height:170px;font-size:10px;line-height:1.45;margin:0;white-space:pre-wrap;word-break:break-word}}
.summary-line{{font-size:12px;color:#334155;padding:4px 0 6px;font-weight:500}}
.tl{{width:100%;border-collapse:collapse;font-size:11px;background:#fff;border-radius:6px}} .tl td{{padding:4px 8px;border-bottom:1px solid var(--border)}}
.ts{{color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}} .stp{{font-weight:600;color:#1e3a8a;white-space:nowrap}}
.muted{{color:var(--muted);font-size:12px}}
</style></head><body><div class="wrap">
<div class="hero"><div><h1>{_esc(title)}</h1><p>{_esc(subtitle)}</p></div><div class="hero-logo"><img src="{logo}" alt="Senao"></div></div>
<div class="container"><div class="cards">
{''.join(cards)}</div>
<div class="panel"><h2>Testbed Summary</h2><table class="testbed"><thead><tr><th></th><th><span class="role">BTS</span></th><th><span class="role">SU1</span></th></tr></thead>
<tbody><tr><th>Model</th><td>{_esc(model_name(bts))}</td><td>{_esc(model_name(cpe))}</td></tr>
<tr><th>FW Version</th><td>{_esc(value(bts, "fw_version"))}</td><td>{_esc(value(cpe, "fw_version"))}</td></tr>
<tr><th>IPv6 Address</th><td>{_esc(value(bts, "ipv6", "ip"))}</td><td>{_esc(value(cpe, "ipv6", "ip"))}</td></tr></tbody></table>
<div class="meta"><strong>Started:</strong> {_esc(state.started_at)} &nbsp; · &nbsp; <strong>Progress:</strong> {done} of {total} &nbsp; · &nbsp; <strong>Pass rate:</strong> {pass_rate}</div></div>
<div class="content"><table class="plan"><thead><tr><th>Iter</th><th>Target</th><th>Started</th>{headers}<th>Result</th></tr></thead><tbody>
{''.join(body)}
</tbody></table></div></div></div></body></html>"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output
