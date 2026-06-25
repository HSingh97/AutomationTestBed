#!/usr/bin/env python3
"""Inject product images into docs/lab-instruments.html from lab-tools deck + local assets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SRC = DOCS / "architecture-diagram-lab-tools.html"
OUT = DOCS / "lab-instruments.html"

ALT_MAP = {
    "trex": "TRex",
    "ixia": "Ixia",
    "ostinato": "Ostinato",
    "spectrum": "TTI PSA6005 spectrum analyser",
}

LOCAL_IMAGES: dict[str, tuple[Path, str]] = {
    "vaunix": (DOCS / "images" / "lda608v.jpg", "Vaunix LDA-608V-4"),
    "vna": (DOCS / "images" / "vna485.jpg", "VNA485"),
    "scope": (DOCS / "images" / "tps2024b.jpg", "Tektronix TPS2024B"),
}


def extract_images(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for m in re.finditer(
        r'<img class="tool-logo" src="(data:[^"]+)" alt="([^"]*)"', html
    ):
        src, alt = m.group(1), m.group(2)
        found[alt] = src
    return found


def image_tag(src: str, alt: str) -> str:
    return f'<img class="card-photo" src="{src}" alt="{alt}">'


def replace_card_visual(page: str, key: str, src: str, alt: str) -> tuple[str, int]:
    pattern = (
        rf'(<div class="card-visual {re.escape(key)}">)'
        r"(?:<i class=\"fas [^\"]+\"></i>|<img class=\"card-photo\"[^>]*>)"
        r"(</div>)"
    )
    repl = rf"\1{image_tag(src, alt)}\2"
    return re.subn(pattern, repl, page, count=1)


def main() -> int:
    if not SRC.is_file():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    if not OUT.is_file():
        print(f"missing target: {OUT}", file=sys.stderr)
        return 1

    src_html = SRC.read_text(encoding="utf-8")
    by_alt = extract_images(src_html)

    page = OUT.read_text(encoding="utf-8")

    for key, alt in ALT_MAP.items():
        data_uri = by_alt.get(alt)
        if not data_uri:
            print(f"warning: no image for alt={alt!r}", file=sys.stderr)
            continue
        page, n = replace_card_visual(page, key, data_uri, alt)
        if n != 1:
            print(f"warning: card-visual.{key} not updated ({n})", file=sys.stderr)

    for key, (path, alt) in LOCAL_IMAGES.items():
        if not path.is_file():
            print(f"warning: missing local image {path}", file=sys.stderr)
            continue
        rel = path.relative_to(DOCS).as_posix()
        page, n = replace_card_visual(page, key, rel, alt)
        if n != 1:
            print(f"warning: card-visual.{key} not updated ({n})", file=sys.stderr)

    OUT.write_text(page, encoding="utf-8")

    embed = ROOT / "scripts" / "embed_html_images.py"
    if embed.is_file():
        subprocess.run(
            [sys.executable, str(embed), str(OUT)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
