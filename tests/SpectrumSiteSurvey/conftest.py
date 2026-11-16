"""Spectrum Analyzer and Site Survey — do not bootstrap or rewrite the live link."""

from __future__ import annotations


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("SpectrumSiteSurvey") for item in items):
        if hasattr(config.option, "skip_testbed_bootstrap"):
            config.option.skip_testbed_bootstrap = True
