#!/usr/bin/env python3
"""Embed local images as base64 data URIs in docs HTML (portable single-file output)."""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def embed_images(html: str, base_dir: Path) -> tuple[str, int]:
    cache: dict[str, str] = {}
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        src = match.group(1)
        if src.startswith("data:") or src.startswith("http"):
            return match.group(0)
        if src not in cache:
            img_path = (base_dir / src).resolve()
            if not img_path.is_file():
                print(f"warning: missing image {src}", file=sys.stderr)
                return match.group(0)
            mime = MIME.get(img_path.suffix.lower(), "application/octet-stream")
            cache[src] = base64.b64encode(img_path.read_bytes()).decode("ascii")
        count += 1
        mime = MIME.get(Path(src).suffix.lower(), "image/png")
        return f'src="data:{mime};base64,{cache[src]}"'

    return re.sub(r'src="([^"]+)"', repl, html), len(cache)


def process_file(src: Path, output: Path | None) -> int:
    out = output or src
    html, n = embed_images(src.read_text(encoding="utf-8"), src.parent)
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"Wrote {out.name} ({size_kb:.0f} KB, {n} unique images embedded)")
    return 0


def process_all(docs_dir: Path) -> int:
    files = sorted(docs_dir.glob("*.html"))
    if not files:
        print(f"no HTML files in {docs_dir}", file=sys.stderr)
        return 1
    for src in files:
        process_file(src, None)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, nargs="?", help="HTML file to update in place")
    parser.add_argument("-o", "--output", type=Path, help="Optional output path")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Embed images in every docs/*.html file",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=Path("docs"),
        help="Docs directory for --all (default: docs)",
    )
    args = parser.parse_args()

    if args.all:
        return process_all(args.docs_dir.resolve())

    if not args.html:
        parser.error("html file required unless --all is used")

    src = args.html.resolve()
    if not src.is_file():
        print(f"error: not found: {src}", file=sys.stderr)
        return 1
    return process_file(src, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
