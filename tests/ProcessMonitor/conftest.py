"""Process monitor suite fixtures."""

import pytest

from utils.process_monitor_flows import assert_process_monitor_preflight


@pytest.fixture(scope="session", autouse=True)
async def process_monitor_preflight(request, root_ssh, gui_page):
    """Run GUI + SSH preflight before any PROCESS_* test when the suite is enabled."""
    if not request.config.getoption("--allow-process-monitor"):
        yield
        return
    await assert_process_monitor_preflight(root_ssh, gui_page)
    yield
