"""Lab suite — skip generic testbed bootstrap (co-channel drives lab PC hop)."""

from __future__ import annotations


def pytest_collection_modifyitems(config, items):
    if any(
        item.get_closest_marker("CoChannel") or item.get_closest_marker("Lab")
        for item in items
    ):
        config.option.skip_testbed_bootstrap = True
