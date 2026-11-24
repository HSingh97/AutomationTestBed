"""Install Playwright Chromium before any GUI test launches a browser.

A lab run is expected to prepare this itself. An empty ``PLAYWRIGHT_BROWSERS_PATH``
(for example a sandbox cache) must not hide a browser that can be installed
under the user's cache.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_CHROME_GLOB = "chromium-*/chrome-linux/chrome"


def browser_cache_dir() -> Path:
    return Path.home() / ".cache" / "ms-playwright"


def _chrome_bins(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [path for path in root.glob(_CHROME_GLOB) if path.is_file() and os.access(path, os.X_OK)]


def _usable(root: Path | None) -> bool:
    return bool(root and _chrome_bins(root))


def ensure_playwright_chromium() -> Path:
    """Point Playwright at a Chromium install, downloading one when it is missing."""
    durable = browser_cache_dir()
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    configured_path = Path(configured) if configured else None

    if _usable(configured_path):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configured_path)
        return configured_path

    if _usable(durable):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(durable)
        if configured_path and configured_path != durable:
            print(
                f"[playwright] Ignoring empty browser path {configured_path}; using {durable}",
                flush=True,
            )
        return durable

    durable.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(durable)
    print(f"[playwright] Chromium is not installed. Installing into {durable}", flush=True)
    completed = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        env=os.environ.copy(),
        check=False,
    )
    if completed.returncode != 0 or not _usable(durable):
        raise RuntimeError(
            "Playwright Chromium is not installed. "
            f"`{sys.executable} -m playwright install chromium` failed "
            f"(exit {completed.returncode}). Expected chrome under {durable}."
        )
    print(f"[playwright] Chromium ready at {durable}", flush=True)
    return durable
