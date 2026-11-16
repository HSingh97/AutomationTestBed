"""Arp & Bridge Table suite — do not rewire Functional/Sanity topology."""

from __future__ import annotations


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("ArpBridgeTable") for item in items):
        if hasattr(config.option, "skip_testbed_bootstrap"):
            config.option.skip_testbed_bootstrap = True
