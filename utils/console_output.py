"""Line-buffer stdout/stderr and default flush=True on print (Jenkins is not a TTY)."""

from __future__ import annotations

import builtins
import sys


def enable_live_console_output() -> None:
    if getattr(builtins, "_ubr_flush_print_installed", False):
        return
    _orig_print = builtins.print

    def print(*args, **kwargs):  # noqa: A001
        kwargs.setdefault("flush", True)
        return _orig_print(*args, **kwargs)

    builtins.print = print
    builtins._ubr_flush_print_installed = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(line_buffering=True)
            except Exception:
                pass
