"""Co-channel test reports — CSV + HTML only (before/after RF + throughput)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from utils.senao_branding import senao_logo_src

REPO_ROOT = Path(__file__).resolve().parent.parent

LEVEL_ORDER = ("low", "mid", "high")
LEVEL_LABEL = {
    "low": "Low Interference",
    "mid": "Mid Interference",
    "high": "High Interference",
}

CSV_FIELDS = [
    "interference",
    "phase",
    "su_count",
    "secondary_tx_dbm",
    "secondary_iperf_mbps",
    "rssi_before_dbm",
    "obss_before_pct",
    "util_before_pct",
    "free_before_pct",
    "rssi_after_dbm",
    "obss_after_pct",
    "util_after_pct",
    "free_after_pct",
    "rssi_dbm",
    "obss_pct",
    "util_pct",
    "free_pct",
    "dl_tx_mbps",
    "dl_rx_mbps",
    "ul_tx_mbps",
    "ul_rx_mbps",
    "dl_packet_drop_pct",
    "dl_latency_ms",
    "dl_jitter_ms",
    "ul_packet_drop_pct",
    "ul_latency_ms",
    "ul_jitter_ms",
    "obss_sample_count",
    "util_sample_count",
    "obss_samples_json",
    "util_samples_json",
    "free_samples_json",
    "traffic_series_json",
    "notes",
]


def _fmt(v: Any, digits: int = 2) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _g(r: Any, key: str, default: Any = None) -> Any:
    if isinstance(r, dict):
        return r.get(key, default)
    return getattr(r, key, default)


def _rows_by_level(rows: Iterable[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {k: [] for k in LEVEL_ORDER}
    for r in rows:
        level = str(_g(r, "interference") or "").lower()
        if level in out:
            out[level].append(r)
    for level in out:
        out[level].sort(key=lambda x: int(_g(x, "su_count") or 0))
    return out


def rows_from_csv(path: Path | str) -> list[Any]:
    """Load matrix rows from a cochannel CSV (for regenerating reports)."""
    from types import SimpleNamespace

    path = Path(path)
    rows: list[Any] = []
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for d in reader:
            def _num(key: str, cast=float):
                if key not in d or d[key] is None:
                    return None
                v = str(d[key]).strip()
                if v in ("", "None", "—"):
                    return None
                try:
                    return cast(v)
                except ValueError:
                    return None

            ns = SimpleNamespace(
                interference=(d.get("interference") or "").strip().lower(),
                phase=(d.get("phase") or "traffic").strip().lower(),
                su_count=_num("su_count", int) or 0,
                notes=d.get("notes") or "",
            )
            for key in CSV_FIELDS:
                if key in ("interference", "phase", "su_count", "notes"):
                    continue
                if key.endswith("_json"):
                    setattr(ns, key, (d.get(key) or "").strip())
                    continue
                if key.endswith("_count"):
                    cast = int
                elif key.endswith("_dbm") or key == "secondary_tx_dbm":
                    cast = int
                elif key.endswith("_pct"):
                    cast = float
                else:
                    cast = float
                if key == "secondary_iperf_mbps":
                    setattr(ns, key, _num(key) or 0.0)
                else:
                    setattr(ns, key, _num(key, cast))
            rows.append(ns)
    return rows


RF_SAMPLE_LOG_RE = re.compile(
    r"RF sample \d+/\d+: OBSS=(\d+)% UTIL=(\d+)% FREE=(\d+)%"
)
RUN_START_LOG_RE = re.compile(
    r"=== (LOW|MID|HIGH) / (\d+) SU \(with primary traffic\)"
)


def _loads_json_list(raw: Any) -> list:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(str(raw))
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _loads_json_dict(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(str(raw))
        return val if isinstance(val, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _series_payload(r: Any, *, interval_s: float = 5.0) -> dict[str, Any]:
    obss = _loads_json_list(_g(r, "obss_samples_json"))
    util = _loads_json_list(_g(r, "util_samples_json"))
    free = _loads_json_list(_g(r, "free_samples_json"))
    traffic = _loads_json_dict(_g(r, "traffic_series_json"))
    if not obss and not traffic:
        return {}

    times = traffic.get("times_s") or []
    n = len(obss) or len(times) or max(len(util), len(free), 1)
    if times:
        labels = [f"{int(float(t))}s" for t in times]
        n = len(labels)
    else:
        labels = [f"{int(i * interval_s)}s" for i in range(n)]

    out: dict[str, Any] = {"labels": labels, "interval_s": interval_s}
    if obss:
        out["obss"] = obss[:n]
    if util:
        out["util"] = util[:n]
    if free:
        out["free"] = free[:n]
    for key in (
        "dl_rx_mbps",
        "ul_rx_mbps",
        "dl_loss_pct",
        "ul_loss_pct",
        "dl_latency_ms",
        "ul_latency_ms",
        "dl_jitter_ms",
        "ul_jitter_ms",
    ):
        if traffic.get(key):
            out[key] = traffic[key]

    flat_map = {
        "dl_rx_mbps": "dl_rx_mbps",
        "ul_rx_mbps": "ul_rx_mbps",
        "dl_loss_pct": "dl_packet_drop_pct",
        "ul_loss_pct": "ul_packet_drop_pct",
        "dl_latency_ms": "dl_latency_ms",
        "ul_latency_ms": "ul_latency_ms",
        "dl_jitter_ms": "dl_jitter_ms",
        "ul_jitter_ms": "ul_jitter_ms",
    }
    for out_key, row_key in flat_map.items():
        if out.get(out_key):
            continue
        v = _g(r, row_key)
        if v is not None and n:
            out[out_key] = [float(v)] * n
    return out


def enrich_rows_from_pytest_log(rows: list[Any], log_path: Path | str) -> None:
    """Backfill RF sample arrays from pytest log when CSV lacks JSON series."""
    path = Path(log_path)
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks: dict[tuple[str, int], dict[str, list[int]]] = {}
    current: tuple[str, int] | None = None
    obss_l: list[int] = []
    util_l: list[int] = []
    free_l: list[int] = []

    def _flush() -> None:
        nonlocal obss_l, util_l, free_l, current
        if current and obss_l:
            chunks[current] = {
                "obss": obss_l[:],
                "util": util_l[:],
                "free": free_l[:],
            }
        obss_l, util_l, free_l = [], [], []

    for line in text.splitlines():
        m = RUN_START_LOG_RE.search(line)
        if m:
            _flush()
            current = (m.group(1).lower(), int(m.group(2)))
            continue
        sm = RF_SAMPLE_LOG_RE.search(line)
        if sm and current:
            obss_l.append(int(sm.group(1)))
            util_l.append(int(sm.group(2)))
            free_l.append(int(sm.group(3)))
    _flush()

    for r in rows:
        if str(_g(r, "phase") or "") != "traffic":
            continue
        key = (str(_g(r, "interference") or "").lower(), int(_g(r, "su_count") or 0))
        if _g(r, "obss_samples_json"):
            continue
        data = chunks.get(key)
        if not data:
            continue
        setattr(r, "obss_samples_json", json.dumps(data["obss"]))
        setattr(r, "util_samples_json", json.dumps(data["util"]))
        setattr(r, "free_samples_json", json.dumps(data["free"]))


def write_cochannel_csv(rows: list[Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            if hasattr(row, "__dataclass_fields__"):
                d = asdict(row)
            elif isinstance(row, dict):
                d = row
            else:
                d = vars(row)
            w.writerow({k: d.get(k) for k in CSV_FIELDS})
    return path


def write_cochannel_html(rows: list[Any], path: Path, *, meta: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    log_path = meta.get("pytest_log") or "/tmp/cochannel_full_matrix_final.log"
    enrich_rows_from_pytest_log(rows, log_path)
    generated = meta.get("generated_at") or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_level = _rows_by_level(rows)
    logo = senao_logo_src()
    n_traffic = sum(1 for r in rows if str(_g(r, "phase") or "") == "traffic")
    n_ok = sum(
        1
        for r in rows
        if str(_g(r, "phase") or "") == "traffic"
        and _g(r, "dl_packet_drop_pct") is not None
        and _g(r, "ul_packet_drop_pct") is not None
    )
    su_steps = meta.get("su_counts") or sorted(
        {
            int(_g(r, "su_count") or 0)
            for r in rows
            if str(_g(r, "phase") or "") == "traffic" and int(_g(r, "su_count") or 0) > 0
        }
    )
    su_label = " / ".join(str(x) for x in su_steps) or "—"
    connected_cpes = meta.get("connected_cpes")
    cpe_stat = (
        f"{su_label} <span class='stat-sub'>({connected_cpes} CPEs)</span>"
        if connected_cpes
        else su_label
    )

    chart_series_json: dict[str, dict[str, dict]] = {lv: {} for lv in LEVEL_ORDER}

    def _drop_class(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        if x <= 1.0:
            return "good"
        if x <= 10.0:
            return "warn"
        return "bad"

    def _rssi_badge(level_rows: list[Any], b: Any | None) -> str:
        rssi = None
        if b is not None:
            rssi = _g(b, "rssi_before_dbm")
            if rssi is None:
                rssi = _g(b, "rssi_dbm")
        if rssi is None and level_rows:
            for r in level_rows:
                rssi = _g(r, "rssi_before_dbm")
                if rssi is None:
                    rssi = _g(r, "rssi_dbm")
                if rssi is not None:
                    break
        return f"RSSI {rssi} dBm" if rssi is not None else "RSSI —"

    def _section(level: str) -> str:
        level_rows = by_level.get(level) or []
        baselines = [r for r in level_rows if str(_g(r, "phase") or "") == "baseline"]
        traffic = [r for r in level_rows if str(_g(r, "phase") or "") != "baseline"]
        b = baselines[0] if baselines else None
        badge = _rssi_badge(level_rows, b)

        if b is not None:
            obss_b = _g(b, "obss_before_pct")
            if obss_b is None:
                obss_b = _g(b, "obss_pct")
            util_b = _g(b, "util_before_pct")
            if util_b is None:
                util_b = _g(b, "util_pct")
            free_b = _g(b, "free_before_pct")
            if free_b is None:
                free_b = _g(b, "free_pct")
            before_body = (
                "<tr>"
                f"<td class='rf'>{_fmt(obss_b, 0)}</td>"
                f"<td class='rf'>{_fmt(util_b, 0)}</td>"
                f"<td class='rf'>{_fmt(free_b, 0)}</td>"
                "</tr>"
            )
        else:
            before_body = '<tr><td colspan="3" class="empty">No data</td></tr>'

        before_table = f"""
          <h3 class="tbl-title">Without primary throughput</h3>
          <div class="table-wrap">
            <table class="tbl-before">
              <thead>
                <tr>
                  <th class="rf">OBSS (%)</th>
                  <th class="rf">Channel util (%)</th>
                  <th class="rf">Channel free (%)</th>
                </tr>
              </thead>
              <tbody>{before_body}</tbody>
            </table>
          </div>
        """

        interval_s = float(meta.get("rf_sample_interval_s") or 5.0)
        graph_rows_html = ""
        if not traffic:
            tput_body = '<tr><td colspan="14" class="empty">No throughput data</td></tr>'
        else:
            tput_body = ""
            for r in traffic:
                dl = _g(r, "dl_packet_drop_pct")
                ul = _g(r, "ul_packet_drop_pct")
                su = _g(r, "su_count") or "—"
                chart_id = f"{level}-{su}"
                series = _series_payload(r, interval_s=interval_s)
                if series:
                    chart_series_json[level][str(su)] = series
                    graph_rows_html += f"""
          <details class="graph-drop" id="graph-{chart_id}">
            <summary>{su} SU — OBSS · Latency · Jitter</summary>
            <div class="graph-row" data-level="{level}" data-su="{su}">
              <div class="graph-panels">
                <div class="chart-panel">
                  <h4>OBSS % <span class="chart-sub">every {interval_s:g}s</span></h4>
                  <div class="chart-wrap"><canvas id="c-{chart_id}-obss"></canvas></div>
                </div>
                <div class="chart-panel">
                  <h4>Latency <span class="chart-sub">ms · DL / UL</span></h4>
                  <div class="chart-wrap"><canvas id="c-{chart_id}-lat"></canvas></div>
                </div>
                <div class="chart-panel">
                  <h4>Jitter <span class="chart-sub">ms · DL / UL</span></h4>
                  <div class="chart-wrap"><canvas id="c-{chart_id}-jit"></canvas></div>
                </div>
              </div>
            </div>
          </details>
                    """
                tput_body += (
                    "<tr>"
                    f"<td class='su'>{su}</td>"
                    f"<td class='rf'>{_fmt(_g(r,'obss_after_pct') if _g(r,'obss_after_pct') is not None else _g(r,'obss_pct'), 1)}</td>"
                    f"<td class='rf'>{_fmt(_g(r,'util_after_pct') if _g(r,'util_after_pct') is not None else _g(r,'util_pct'), 1)}</td>"
                    f"<td class='rf'>{_fmt(_g(r,'free_after_pct') if _g(r,'free_after_pct') is not None else _g(r,'free_pct'), 1)}</td>"
                    f"<td class='tput'>{_fmt(_g(r,'dl_tx_mbps'))}</td>"
                    f"<td class='tput'>{_fmt(_g(r,'dl_rx_mbps'))}</td>"
                    f"<td class='{_drop_class(dl)}'>{_fmt(dl)}</td>"
                    f"<td>{_fmt(_g(r,'dl_latency_ms'))}</td>"
                    f"<td>{_fmt(_g(r,'dl_jitter_ms'))}</td>"
                    f"<td class='tput'>{_fmt(_g(r,'ul_tx_mbps'))}</td>"
                    f"<td class='tput'>{_fmt(_g(r,'ul_rx_mbps'))}</td>"
                    f"<td class='{_drop_class(ul)}'>{_fmt(ul)}</td>"
                    f"<td>{_fmt(_g(r,'ul_latency_ms'))}</td>"
                    f"<td>{_fmt(_g(r,'ul_jitter_ms'))}</td>"
                    "</tr>"
                )

        graphs_section = ""
        if graph_rows_html:
            graphs_section = f"""
          <div class="graph-section">
            <h3 class="tbl-title">Graphs</h3>
            {graph_rows_html}
          </div>
        """

        tput_table = f"""
          <h3 class="tbl-title">Primary throughput</h3>
          <div class="table-wrap">
            <table class="tbl-tput">
              <thead>
                <tr>
                  <th class="su" rowspan="2">SU</th>
                  <th class="rf" colspan="3">RF avg during test</th>
                  <th class="dl" colspan="5">Downlink</th>
                  <th class="ul" colspan="5">Uplink</th>
                </tr>
                <tr>
                  <th class="rf">OBSS %</th>
                  <th class="rf">Util %</th>
                  <th class="rf">Free %</th>
                  <th class="dl">TX Mbps</th>
                  <th class="dl">RX Mbps</th>
                  <th class="dl">Drop %</th>
                  <th class="dl">Lat ms</th>
                  <th class="dl">Jitter ms</th>
                  <th class="ul">TX Mbps</th>
                  <th class="ul">RX Mbps</th>
                  <th class="ul">Drop %</th>
                  <th class="ul">Lat ms</th>
                  <th class="ul">Jitter ms</th>
                </tr>
              </thead>
              <tbody>{tput_body}</tbody>
            </table>
          </div>
          {graphs_section}
        """

        return f"""
        <section class="card level-{level}">
          <div class="level-head">
            <h2>{LEVEL_LABEL[level]}</h2>
            <span class="badge badge-{level}">{badge}</span>
          </div>
          {before_table}
          {tput_table}
        </section>
        """

    sections_html = "".join(_section(lv) for lv in LEVEL_ORDER)
    charts_json = json.dumps(chart_series_json)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Senao Co-Channel Interference Report</title>
  <style>
    :root {{
      --bg: #eef2f7; --card: #ffffff; --text: #334155; --title: #0f172a;
      --border: #cbd5e1; --navy: #1e3a8a; --muted: #64748b;
      --pass: #10b981; --fail: #ef4444; --warn: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 28px 18px 48px;
      font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
      background:
        radial-gradient(1200px 500px at 10% -10%, #dbeafe 0%, transparent 55%),
        radial-gradient(900px 400px at 100% 0%, #e0f2fe 0%, transparent 50%),
        var(--bg);
      color: var(--text);
    }}
    .wrap {{ max-width: 1320px; margin: 0 auto; }}
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 55%, #2563eb 100%);
      color: #fff; border-radius: 16px; padding: 22px 28px; margin-bottom: 18px;
      display: flex; justify-content: space-between; align-items: center; gap: 20px;
      box-shadow: 0 12px 30px rgba(15, 23, 42, 0.22);
    }}
    .hero h1 {{ margin: 0; font-size: 24px; letter-spacing: -0.02em; }}
    .hero-logo {{ background: #fff; border-radius: 10px; padding: 10px 14px; }}
    .hero-logo img {{ height: 42px; display: block; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .chip {{
      background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.2);
      border-radius: 999px; padding: 5px 11px; font-size: 12px;
    }}
    .summary {{
      display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 10px;
      margin-bottom: 18px;
    }}
    .stat {{
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 14px 10px; text-align: center;
      box-shadow: 0 2px 8px rgba(15,23,42,0.04);
      border-bottom: 3px solid var(--border);
    }}
    .stat h3 {{ margin: 0; font-size: 22px; color: var(--navy); }}
    .stat p {{
      margin: 6px 0 0; font-size: 10px; text-transform: uppercase;
      letter-spacing: 0.45px; font-weight: 700; color: var(--muted);
    }}
    .stat.ok {{ border-bottom-color: var(--pass); }}
    .stat.ok h3 {{ color: var(--pass); }}
    .stat-sub {{
      display: block; font-size: 10px; font-weight: 600; color: var(--muted);
      margin-top: 4px; letter-spacing: 0.02em; text-transform: none;
    }}
    .card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 16px; margin-bottom: 16px;
      box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05);
    }}
    .level-head {{
      display: flex; justify-content: space-between; align-items: center;
      gap: 12px; margin-bottom: 14px;
    }}
    .level-head h2 {{ margin: 0; font-size: 17px; color: var(--title); }}
    .badge {{
      border-radius: 999px; padding: 5px 12px; font-size: 12px; font-weight: 600;
      border: 1px solid transparent;
    }}
    .badge-low {{ background: #ecfdf5; color: #047857; border-color: #a7f3d0; }}
    .badge-mid {{ background: #fff7ed; color: #9a3412; border-color: #fdba74; }}
    .badge-high {{ background: #fef2f2; color: #991b1b; border-color: #fca5a5; }}
    .tbl-title {{
      margin: 4px 0 8px; font-size: 13px; text-transform: uppercase;
      letter-spacing: 0.04em; color: var(--muted);
    }}
    .table-wrap {{
      overflow-x: auto; border-radius: 12px; border: 1px solid var(--border);
      margin-bottom: 16px;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    .tbl-before {{ min-width: 480px; }}
    .tbl-tput {{ min-width: 860px; }}
    .graph-section {{
      margin-top: 16px; padding-top: 8px;
      border-top: 1px dashed var(--border);
    }}
    .graph-drop {{
      border: 1px solid var(--border); border-radius: 12px;
      margin-bottom: 12px; background: #fff; overflow: hidden;
    }}
    .graph-drop summary {{
      cursor: pointer; padding: 12px 16px; font-size: 13px; font-weight: 700;
      color: var(--navy); background: #f1f5f9; list-style: none; user-select: none;
    }}
    .graph-drop summary::-webkit-details-marker {{ display: none; }}
    .graph-drop[open] summary {{ border-bottom: 1px solid var(--border); }}
    .graph-drop .graph-row {{ padding: 14px 16px 16px; }}
    .graph-panels {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
    }}
    .chart-panel {{
      border: 1px solid var(--border); border-radius: 12px;
      padding: 14px 16px 12px; background: linear-gradient(180deg, #fafbfc 0%, #fff 100%);
      box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
    }}
    .chart-panel h4 {{
      margin: 0 0 10px; font-size: 12px; text-transform: uppercase;
      letter-spacing: 0.05em; color: var(--title); font-weight: 700;
    }}
    .chart-sub {{
      font-weight: 500; text-transform: none; letter-spacing: 0;
      color: var(--muted); font-size: 11px;
    }}
    .chart-wrap {{ position: relative; height: 240px; width: 100%; }}
    th, td {{
      border-bottom: 1px solid var(--border); padding: 10px 8px;
      text-align: center; font-size: 13px;
    }}
    th {{
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.03em;
      color: #0f172a; font-weight: 700;
    }}
    th.su {{ background: #dcfce7; }}
    th.dl {{ background: #fef3c7; }}
    th.ul {{ background: #fce7f3; }}
    th.rf {{ background: #e0e7ff; }}
    th.tput {{ background: #ccfbf1; }}
    td.su {{ font-weight: 700; color: var(--navy); background: #f8fafc; }}
    td.rf, td.tput {{ background: #f8fafc; font-variant-numeric: tabular-nums; }}
    td.good {{ color: #047857; font-weight: 700; }}
    td.warn {{ color: #b45309; font-weight: 700; }}
    td.bad {{ color: #b91c1c; font-weight: 700; }}
    tbody tr:hover td {{ background: #f1f5f9; }}
    .empty {{ color: #94a3b8; font-style: italic; padding: 14px; }}
    footer {{ margin-top: 8px; text-align: center; color: #94a3b8; font-size: 12px; }}
    @media (max-width: 1100px) {{
      .graph-panels {{ grid-template-columns: 1fr; }}
      .chart-wrap {{ height: 220px; }}
    }}
    @media (max-width: 800px) {{
      .summary {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
      .hero {{ flex-direction: column; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div>
        <h1>Co-Channel Interference Report</h1>
        <div class="meta">
          <span class="chip">Generated: {generated}</span>
        </div>
      </div>
      <div class="hero-logo"><img src="{logo}" alt="Senao"/></div>
    </header>

    <div class="summary">
      <div class="stat"><h3>{n_traffic}</h3><p>Traffic Cells</p></div>
      <div class="stat ok"><h3>{n_ok}</h3><p>With Throughput</p></div>
      <div class="stat"><h3>3</h3><p>Interference Levels</p></div>
      <div class="stat"><h3>{cpe_stat}</h3><p>CPE Counts</p></div>
    </div>

    {sections_html}

    <footer>Senao AutomationTestBed · CoChannel</footer>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
  (function () {{
    const SERIES = {charts_json};
    const charts = {{}};

    function lineChart(canvas, labels, datasets, yTitle) {{
      return new Chart(canvas, {{
        type: "line",
        data: {{ labels, datasets }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: {{ mode: "index", intersect: false }},
          plugins: {{
            legend: {{
              display: datasets.length > 1,
              position: "top",
              align: "end",
              labels: {{ boxWidth: 12, padding: 14, font: {{ size: 11 }} }}
            }},
            tooltip: {{ bodyFont: {{ size: 12 }}, titleFont: {{ size: 12 }} }}
          }},
          scales: {{
            x: {{
              title: {{ display: true, text: "Time (s)", font: {{ size: 11 }} }},
              ticks: {{ maxTicksLimit: 10, font: {{ size: 10 }} }},
              grid: {{ color: "#e2e8f0" }}
            }},
            y: {{
              title: {{ display: !!yTitle, text: yTitle || "", font: {{ size: 11 }} }},
              ticks: {{ font: {{ size: 10 }} }},
              grid: {{ color: "#e2e8f0" }}
            }}
          }}
        }}
      }});
    }}

    function ds(label, data, color) {{
      return {{
        label,
        data,
        borderColor: color,
        backgroundColor: color + "18",
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        fill: false
      }};
    }}

    function renderRowCharts(level, su) {{
      const key = SERIES[level] && SERIES[level][String(su)];
      if (!key) return;
      const prefix = level + "-" + su;
      const labels = key.labels || [];
      const mk = (suffix, datasets, yTitle) => {{
        const id = "c-" + prefix + "-" + suffix;
        const el = document.getElementById(id);
        if (!el || charts[id]) return;
        charts[id] = lineChart(el, labels, datasets, yTitle);
      }};
      if (key.obss) mk("obss", [ds("OBSS %", key.obss, "#2563eb")], "%");
      const lat = [];
      if (key.dl_latency_ms) lat.push(ds("DL", key.dl_latency_ms, "#d97706"));
      if (key.ul_latency_ms) lat.push(ds("UL", key.ul_latency_ms, "#db2777"));
      if (lat.length) mk("lat", lat, "ms");
      const jit = [];
      if (key.dl_jitter_ms) jit.push(ds("DL", key.dl_jitter_ms, "#d97706"));
      if (key.ul_jitter_ms) jit.push(ds("UL", key.ul_jitter_ms, "#db2777"));
      if (jit.length) mk("jit", jit, "ms");
    }}

    document.querySelectorAll("details.graph-drop").forEach(function (det) {{
      det.addEventListener("toggle", function () {{
        if (!det.open) return;
        const m = det.id.match(/^graph-(low|mid|high)-(\\d+)$/);
        if (m) renderRowCharts(m[1], m[2]);
      }});
    }});
  }})();
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path


def write_cochannel_reports(
    rows: list[Any],
    *,
    out_dir: str | Path | None = None,
    meta: dict[str, Any] | None = None,
    stem: str | None = None,
) -> dict[str, Path]:
    """Write CSV + HTML only; return paths."""
    out = Path(out_dir) if out_dir else REPO_ROOT / "reports" / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = stem or f"cochannel_report_{ts}"
    meta = dict(meta or {})
    meta.setdefault("generated_at", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    paths = {
        "csv": write_cochannel_csv(rows, out / f"{stem}.csv"),
        "html": write_cochannel_html(rows, out / f"{stem}.html", meta=meta),
    }
    print(f"[cochannel] CSV  → {paths['csv']}", flush=True)
    print(f"[cochannel] HTML → {paths['html']}", flush=True)
    return paths
