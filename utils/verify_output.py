"""Print GUI vs backend verification tables to the terminal during test runs."""

from __future__ import annotations

import re


def print_section(title: str):
    print(f"\n{'=' * 72}", flush=True)
    print(title, flush=True)
    print("=" * 72, flush=True)


def print_comparison_table(rows: list[tuple[str, str, str, str]]):
    """
    Print a comparison table.
    Each row: (field, gui_value, backend_value, status).
    """
    headers = ("Field", "GUI", "Backend/CLI", "Result")
    widths = [22, 22, 22, 8]
    for field, gui, backend, status in rows:
        widths[0] = max(widths[0], len(str(field)))
        widths[1] = max(widths[1], len(str(gui)))
        widths[2] = max(widths[2], len(str(backend)))
        widths[3] = max(widths[3], len(str(status)))

    def _row(cells):
        parts = []
        for i, cell in enumerate(cells):
            parts.append(str(cell).ljust(widths[i])[: widths[i]])
        print("  ".join(parts), flush=True)

    _row(headers)
    _row(["-" * widths[i] for i in range(4)])
    for row in rows:
        _row(row)
    print(flush=True)


def print_kv_block(title: str, data: dict[str, str]):
    print(f"  {title}:", flush=True)
    for key, value in data.items():
        print(f"    {key}: {value}", flush=True)


def _parse_num(value: str) -> float | None:
    match = re.search(r"[\d.]+", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _rate_mbps_numbers_match(gui_val: str, backend_val: str) -> bool:
    """Link table rates: compare leading Mbps integer; MCS index in () may drift."""
    gm = re.match(r"^\s*(\d+)", str(gui_val or "").strip())
    bm = re.match(r"^\s*(\d+)", str(backend_val or "").strip())
    return bool(gm and bm and gm.group(1) == bm.group(1))


def match_status(gui_val: str, backend_val: str, *, tolerance: float | None = None) -> str:
    gui = str(gui_val or "").strip()
    backend = str(backend_val or "").strip()
    if _rate_mbps_numbers_match(gui, backend):
        return "PASS"
    if tolerance is not None:
        g_num, b_num = _parse_num(gui), _parse_num(backend)
        if g_num is not None and b_num is not None:
            return "PASS" if abs(g_num - b_num) <= tolerance else "FAIL"
    if not gui and not backend:
        return "PASS"
    if gui in backend or backend in gui:
        return "PASS"
    return "FAIL"


def print_gui_backend_table(title: str, rows: list[tuple[str, str, str, str | None]]):
    """
    rows: (field_label, gui_value, backend_value, optional_tolerance)
    Prints table then returns list of (field, status) for checks.
    """
    print_section(title)
    table_rows = []
    for field, gui, backend, tol in rows:
        status = match_status(gui, backend, tolerance=tol)
        table_rows.append((field, gui, backend, status))
    print_comparison_table(table_rows)
    return table_rows
