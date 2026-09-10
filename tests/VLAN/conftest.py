"""VLAN suite hooks — TRex fail-fast summary + post-suite VLAN restore."""

from __future__ import annotations


def pytest_sessionfinish(session, exitstatus):
    try:
        from traffic.vlan_lab import print_trex_infra_summary, trex_infra_is_down

        if trex_infra_is_down():
            print_trex_infra_summary()
    except Exception:
        pass

    # Always restore DUT VLAN to transparent + mgmtvlan=1 after the suite.
    if not session.config.getoption("--allow-vlan-lab", default=False):
        return
    try:
        from pathlib import Path

        import yaml

        from traffic.vlan_lab import restore_vlan_lab_post_suite

        profile_name = session.config.getoption("--profile") or "a60_lab"
        path = Path(__file__).resolve().parents[2] / "profiles" / f"{profile_name}.yaml"
        profile: dict = {}
        if path.is_file():
            profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        print(
            f"\n[vlan][teardown] Session end — restoring transparent + mgmtvlan=1 "
            f"(profile={profile_name})..."
        )
        restore_vlan_lab_post_suite(profile, mgmtvlan=1)
    except Exception as exc:
        print(f"[vlan][teardown] WARN: post-suite VLAN restore failed: {exc}")
