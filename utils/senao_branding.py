"""Shared branding assets for Senao HTML reports (offline-safe data URIs)."""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGO_SVG = _REPO_ROOT / "reports" / "assets" / "senao-logo.svg"
_FALLBACK_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="40" '
    b'viewBox="0 0 180 40">'
    b'<text x="8" y="28" font-family="Arial,Helvetica,sans-serif" '
    b'font-size="18" font-weight="700" fill="#0071be">Senao Networks</text>'
    b"</svg>"
)


@lru_cache(maxsize=1)
def senao_logo_src() -> str:
    """Return a data-URI for the Senao logo (embedded so file:// reports show it)."""
    raw = _LOGO_SVG.read_bytes() if _LOGO_SVG.is_file() else _FALLBACK_SVG
    if not raw:
        raw = _FALLBACK_SVG
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
