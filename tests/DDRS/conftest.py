"""DDRS suite — isolated conftest (skip generic testbed bootstrap).

Does not flap lab-PC Ethernet (enp1s0 / enp4s0 / enx*). Secondary quiet
baseline is BTS radio TX power via SSH, not a PC port down.
"""

from __future__ import annotations

import pytest

from utils.ddrs_flows import DdrsConfig, clear_ddrs_backend_cache, warm_ddrs_backend


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("DDRS") for item in items):
        config.option.skip_testbed_bootstrap = True


@pytest.fixture(scope="session")
def ddrs_config(profile_bundle) -> DdrsConfig:
    clear_ddrs_backend_cache()
    cfg = DdrsConfig.from_profile(profile_bundle.active)
    warm_ddrs_backend(cfg)
    return cfg
