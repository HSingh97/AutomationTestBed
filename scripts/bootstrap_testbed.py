#!/usr/bin/env python3
"""Run testbed bootstrap once (mgmt VLAN, QinQ/transparent, CPE discovery, link recovery)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.profile_manager import load_profile_bundle
from utils.testbed_bootstrap import TestbedState, bootstrap_testbed


async def _main(args: argparse.Namespace) -> int:
    bundle = load_profile_bundle(
        profile_name=args.profile,
        recovery_profile_name=args.recovery_profile,
        local_ip=args.local_ipv6,
        username=args.username,
        password=args.password,
    )
    creds = {"user": args.username, "pass": args.password}
    gui_page = None
    if args.with_gui:
        from playwright.async_api import async_playwright
        from utils.gui_login import login_if_needed
        from utils.net_utils import format_http_host

        dut = bundle.active["dut"]
        host_primary = dut.get("local_ipv6") or dut.get("local_ip")
        host_fallback = args.fallback_ip or None
        hosts_to_try = []
        for h in (host_primary, host_fallback):
            if h and h not in hosts_to_try:
                hosts_to_try.append(h)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            context = await browser.new_context(ignore_https_errors=True)
            last_exc = None
            for host in hosts_to_try:
                page = await context.new_page()
                try:
                    # `login_if_needed` does the navigation when the session is not loaded.
                    await login_if_needed(page, host, creds, wait_ms=3000, skip_recovery=True)
                    gui_page = page
                    break
                except Exception as exc:
                    last_exc = exc
                    await page.close()

            if gui_page is None:
                raise RuntimeError(f"GUI login failed for all hosts: {hosts_to_try}. Last={last_exc}")

            await bootstrap_testbed(
                bundle, creds, gui_page=gui_page, cli_fallback_ip=args.fallback_ip or None
            )
            await browser.close()
    else:
        await bootstrap_testbed(
            bundle, creds, gui_page=None, cli_fallback_ip=args.fallback_ip or None
        )

    state = TestbedState.load()
    if state:
        print(f"\n[ok] BTS={state.bts_mgmt_ipv6} CPE={state.cpe_mgmt_ipv6s} link_up={state.link_up}")
        for note in state.notes:
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UBR testbed bootstrap")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--recovery-profile", default="link_formation")
    parser.add_argument("--local-ipv6", default="")
    parser.add_argument("--username", default="root")
    parser.add_argument("--password", default="Sen@0ubRNwk$")
    parser.add_argument("--with-gui", action="store_true", help="Enable BTS/CPE .tgz restore via LuCI")
    parser.add_argument("--headed", action="store_true", help="Show browser when --with-gui")
    parser.add_argument(
        "--fallback-ip",
        default="10.0.0.1",
        help="BTS/CPE factory fallback IPv4 when mgmt IPv6 is down (VLAN/UCI bootstrap)",
    )
    raise SystemExit(asyncio.run(_main(parser.parse_args())))
