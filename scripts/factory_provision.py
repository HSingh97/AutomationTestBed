#!/usr/bin/env python3
"""
Factory-reset → basic config → link formation.

Run once after both devices are factory reset:

  PYTHONPATH=. python3 scripts/factory_provision.py --headed
  PYTHONPATH=. python3 scripts/factory_provision.py --then-bootstrap

Uses profiles/factory_provision.yaml (VLAN 200/201, installer/senao123 on fallback).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.factory_provision import provision_factory_reset
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
    fallback = args.fallback_ip or "10.0.0.1"

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

        state = await provision_factory_reset(
            bundle,
            creds,
            gui_page=page,
            fallback_ip=fallback,
        )
        await browser.close()

    print(
        f"\n[factory] link_up={state.link_up} "
        f"BTS={state.bts_mgmt_ipv6} notes={len(state.notes)}"
    )
    for note in state.notes:
        print(f"  - {note}")

    if args.then_bootstrap:
        print("\n[factory] Running full testbed bootstrap (sanity verify)...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            from utils.gui_login import login_if_needed as _login
            from utils.net_utils import normalize_ip

            host = normalize_ip(bundle.active["dut"].get("local_ipv6", ""))
            try:
                await _login(page, host, creds, wait_ms=3000, skip_recovery=True)
            except Exception:
                print("[factory] bootstrap GUI: mgmt IPv6 login failed, using fallback")
                fl = bundle.active.get("factory_login", {}) or {}
                await _login(
                    page,
                    fallback,
                    {"user": fl.get("username", "installer"), "pass": fl.get("password", "senao123")},
                    wait_ms=3000,
                    skip_recovery=True,
                )
            await bootstrap_testbed(
                bundle, creds, gui_page=page, cli_fallback_ip=fallback
            )
            await browser.close()

        final = TestbedState.load()
        if final:
            print(f"[ok] bootstrap link_up={final.link_up} CPE={final.cpe_mgmt_ipv6s}")

    return 0 if state.link_up else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UBR factory-reset provisioning")
    parser.add_argument("--profile", default="factory_provision")
    parser.add_argument("--recovery-profile", default="link_formation")
    parser.add_argument("--local-ipv6", default="")
    parser.add_argument("--username", default="root")
    parser.add_argument("--password", default="Sen@0ubRNwk$")
    parser.add_argument("--fallback-ip", default="10.0.0.1", help="BTS factory IPv4")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--then-bootstrap",
        action="store_true",
        help="Run bootstrap_testbed after factory provision (recommended)",
    )
    raise SystemExit(asyncio.run(_main(parser.parse_args())))
