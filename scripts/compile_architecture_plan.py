#!/usr/bin/env python3
"""Compile customer architecture / test plans into one portable HTML document."""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

LOGO_FILE = "images/senao-logo.png"

SECTIONS = [
    ("embedded-manual", "1. Embedded Manual", "architecture-diagram-manual.html"),
    ("embedded-tools", "2. Embedded Tools", "architecture-diagram-lab-tools.html"),
    ("embedded-automation", "3. Embedded Automation", "architecture-diagram-automation.html"),
    ("field-plan", "4. Field Test Plan", "field-test-plan.html"),
    ("nms-manual", "5. NMS Manual", "nms-manual-testing-plan.html"),
    ("nms-automation", "6. NMS Automation", "nms-automation-plan.html"),
]

SHELL_CSS = """
:root {
    --navy: #0f172a;
    --blue: #1d4ed8;
    --border: #e2e8f0;
    --text: #1e293b;
    --muted: #64748b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
    font-family: 'Inter', sans-serif;
    background: #eef2f6;
    color: var(--text);
}
.master-wrap { max-width: 1680px; margin: 0 auto; padding: 0 16px 48px; }

/* Cover */
.cover-page {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    background: linear-gradient(135deg, var(--navy) 0%, #312e81 55%, #1e3a8a 100%);
    color: white;
    page-break-after: always;
    margin: 0 -16px;
    padding: 48px 24px;
}
.senao-logo {
    width: auto;
    height: auto;
    object-fit: contain;
    display: block;
}
.cover-page .senao-logo {
    width: min(336px, 63vw);
    max-height: 101px;
    margin-bottom: 36px;
}
.cover-page h1 { font-size: clamp(26px, 4vw, 36px); font-weight: 800; letter-spacing: -0.02em; margin-bottom: 12px; }
.cover-page p { font-size: 15px; opacity: 0.9; max-width: 520px; line-height: 1.6; padding: 0 12px; }

/* Index */
.index-page {
    min-height: 100vh;
    background: white;
    border: 1px solid var(--border);
    border-radius: 16px;
    margin: 24px 0;
    padding: 40px 48px;
    page-break-after: always;
    box-shadow: 0 12px 32px -8px rgb(0 0 0 / 0.1);
}
.index-page h2 { font-size: 22px; font-weight: 800; color: var(--navy); margin-bottom: 28px; }
.index-list { list-style: none; display: flex; flex-direction: column; gap: 12px; }
.index-list a {
    display: flex; align-items: center; gap: 14px;
    padding: 16px 20px; border-radius: 12px;
    border: 1px solid #bfdbfe; background: #eff6ff;
    text-decoration: none; color: var(--text); font-weight: 700; font-size: 15px;
    transition: background 0.15s, border-color 0.15s;
}
.index-list a:hover { background: #dbeafe; border-color: #93c5fd; }
.index-list .num {
    width: 32px; height: 32px; border-radius: 8px;
    background: var(--blue); color: white;
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 800; flex-shrink: 0;
}

/* Section pages */
.compiled-section {
    page-break-before: always;
    margin: 24px 0;
}
.sheet-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; padding: 14px 20px;
    background: white; border: 1px solid var(--border);
    border-radius: 12px 12px 0 0;
    border-bottom: 2px solid #bfdbfe;
}
.sheet-header .brand { display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1; }
.sheet-header .senao-logo {
    width: min(210px, 39vw);
    max-height: 62px;
    flex-shrink: 0;
}
.sheet-header .doc-title { font-size: clamp(15px, 2.2vw, 18px); font-weight: 800; color: var(--navy); white-space: nowrap; }
.sheet-header .section-label {
    font-size: clamp(13px, 1.8vw, 15px); font-weight: 700; color: var(--blue);
    text-transform: uppercase; letter-spacing: 0.5px;
    text-align: right; flex-shrink: 0; max-width: 42%;
}
@media (max-width: 640px) {
    .sheet-header { flex-wrap: wrap; padding: 12px 14px; }
    .sheet-header .doc-title { font-size: 14px; }
    .sheet-header .section-label { max-width: 100%; text-align: left; width: 100%; margin-top: 4px; }
    .cover-page .senao-logo { width: min(280px, 64vw); max-height: 78px; }
    .sheet-header .senao-logo { width: min(182px, 53vw); max-height: 50px; }
}
.section-inner {
    background: transparent;
    padding: 0;
}
.section-inner > .diagram-container,
.section-inner > .page {
    border-radius: 0 0 16px 16px !important;
    margin-top: 0 !important;
    max-width: 100% !important;
}

@media print {
    body { background: white; }
    .master-wrap { padding: 0; max-width: none; }
    .cover-page { margin: 0; }
    .index-page, .compiled-section { margin: 0; box-shadow: none; page-break-before: always; }
    .index-list a { border-color: #ccc; }
}
"""


def load_logo_data_uri(docs_dir: Path) -> str:
    logo_path = docs_dir / LOGO_FILE
    if not logo_path.is_file():
        raise FileNotFoundError(f"Senao logo not found: {logo_path}")
    b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def extract_styles(html: str) -> str:
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    scoped = []
    for block in blocks:
        # Avoid resetting global body inside sections
        block = re.sub(
            r"\bbody\s*\{",
            ".section-inner {",
            block,
        )
        scoped.append(block.strip())
    return "\n".join(scoped)


def extract_body_inner(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    if not m:
        raise ValueError("no <body> found")
    return m.group(1).strip()


def compile_plan(docs_dir: Path, output: Path, logo_uri: str | None = None) -> None:
    logo = logo_uri or load_logo_data_uri(docs_dir)
    logo_img = f'<img class="senao-logo" src="{logo}" alt="Senao Networks">'

    index_items = []
    section_html = []
    all_styles = [SHELL_CSS]

    for sec_id, label, filename in SECTIONS:
        path = docs_dir / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        raw = path.read_text(encoding="utf-8")
        all_styles.append(f"/* --- {filename} --- */\n{extract_styles(raw)}")
        inner = extract_body_inner(raw)
        index_items.append(
            f'<li><a href="#{sec_id}"><span class="num">{label.split(".")[0]}</span>{label.split(". ", 1)[1]}</a></li>'
        )
        section_html.append(f"""
<section class="compiled-section" id="{sec_id}">
    <header class="sheet-header">
        <div class="brand">{logo_img}<span class="doc-title">Architecture Plan / Diagrams</span></div>
        <span class="section-label">{label}</span>
    </header>
    <div class="section-inner">{inner}</div>
</section>""")

    index_list = "\n".join(index_items)
    sections = "\n".join(section_html)
    styles = "\n".join(all_styles)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UBR Architecture Plan / Diagrams</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>{styles}</style>
</head>
<body>
<div class="master-wrap">
    <section class="cover-page">
        {logo_img}
        <h1>Architecture Plan / Diagrams</h1>
        <p>UBR embedded validation, lab tools, automation testbed, field testing, and NMS plans — customer overview.</p>
    </section>

    <section class="index-page" id="index">
        <header class="sheet-header" style="border-radius:12px;margin-bottom:28px;">
            <div class="brand">{logo_img}<span class="doc-title">Architecture Plan / Diagrams</span></div>
            <span class="section-label">Contents</span>
        </header>
        <h2>Index</h2>
        <ol class="index-list">{index_list}</ol>
    </section>

    {sections}
</div>
</body>
</html>"""

    output.write_text(doc, encoding="utf-8")
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Wrote {output} ({size_mb:.2f} MB)")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    docs = root / "docs"
    out = docs / "architecture-plan.html"
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
    compile_plan(docs, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
