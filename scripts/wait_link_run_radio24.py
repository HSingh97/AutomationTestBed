#!/usr/bin/env python3
"""Wait for BTS↔CPE IPv6 link after reboot, then run Radio24Scan pytest markers."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import asyncssh

BTS_IPV6 = "2401:4900:d0:40d4:0:17b8:0:330"
CPE_IPV6 = "2401:4900:d0:40d4::17b8:0:331"
SSH_USER = "root"
SSH_PASS = "Sen@0ubRNwk$"
POLL_INTERVAL_S = 15
LINK_SETTLE_S = 45
MAX_WAIT_S = 600
DEFAULT_MARKER = "RADIO_24_01 or RADIO_24_02"


async def _link_stable() -> bool:
    try:
        async with asyncssh.connect(
            BTS_IPV6,
            username=SSH_USER,
            password=SSH_PASS,
            known_hosts=None,
            connect_timeout=12.0,
        ) as conn:
            for _ in range(2):
                result = await conn.run(
                    f"ping -6 -c 2 -W 4 {CPE_IPV6} 2>&1",
                    check=False,
                    timeout=25,
                )
                out = ((result.stdout or "") + (result.stderr or "")).lower()
                if "bytes from" not in out or "0 received" in out or "100% packet loss" in out:
                    return False
                await asyncio.sleep(5)
            return True
    except (OSError, asyncssh.Error):
        return False


async def wait_for_link() -> bool:
    deadline = time.monotonic() + MAX_WAIT_S
    while time.monotonic() < deadline:
        if await _link_stable():
            print(f"BTS↔CPE link stable — settling {LINK_SETTLE_S}s before pytest...")
            await asyncio.sleep(LINK_SETTLE_S)
            return True
        remaining = int(deadline - time.monotonic())
        print(f"waiting for BTS↔CPE link ({remaining}s left)...")
        await asyncio.sleep(POLL_INTERVAL_S)
    return False


def main() -> int:
    marker = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MARKER
    if not asyncio.run(wait_for_link()):
        print("timed out waiting for BTS↔CPE link")
        return 1

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/Radio24Scan/test_2_4g_radio.py",
        "-m",
        marker,
        "--profile=default",
        "-v",
    ]
    print("running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
