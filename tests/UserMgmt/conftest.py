"""User Management suite fixtures — gate lab runs before SSH/Playwright."""

from __future__ import annotations

import os
import re

import pytest

from config.um_test_cases import case_by_id
from utils.um_flows import ensure_admin_password_sync


def _gate_um_case(request) -> dict:
    """Skip before heavy fixtures when lab flags / case status require it."""
    name = request.node.name
    m = re.search(r"test_um_(\d+)", name)
    if not m:
        return {}

    case = case_by_id(f"UM_{int(m.group(1)):02d}")
    status = case.get("status")
    if status == "pending":
        pytest.skip(case.get("note") or f"{case['id']} pending automation")
    if status == "destructive":
        if not request.config.getoption("--allow-um-lab"):
            pytest.skip("UM lab cases require --allow-um-lab")
        if not request.config.getoption("--allow-um-destructive"):
            pytest.skip(
                f"{case['id']} destructive — pass --allow-um-destructive with --allow-um-lab"
            )
        return case
    if not request.config.getoption("--allow-um-lab"):
        pytest.skip("UM lab cases require --allow-um-lab (live LuCI GUI)")
    return case


@pytest.fixture
def um_case(request):
    """Catalog entry + lab gate (must run before Playwright / bsu_ip)."""
    return _gate_um_case(request)


@pytest.fixture
def um_host(um_case, bsu_ip, request):
    """Prefer IPv4 fallback for LuCI when profile IPv6 is unreachable."""
    return (
        os.environ.get("UM_GUI_HOST")
        or os.environ.get("QOS_DUT_HOST")
        or request.config.getoption("--fallback-ip")
        or bsu_ip
    )


@pytest.fixture(scope="session")
def um_admin_ready(request):
    """Once per session: set DUT admin password to lab default admin1234 if needed."""
    if not request.config.getoption("--allow-um-lab"):
        yield
        return

    host = (
        os.environ.get("UM_GUI_HOST")
        or os.environ.get("QOS_DUT_HOST")
        or request.config.getoption("--fallback-ip")
        or "10.0.0.1"
    )
    password = os.environ.get("UM_ADMIN_PASSWORD") or "admin1234"
    ensure_admin_password_sync(host, new_password=password)
    yield


@pytest.fixture(autouse=True)
def _um_require_admin_ready(request, um_case):
    """Pull session password prep into each runnable lab case."""
    if um_case.get("status") in ("implemented", "destructive"):
        request.getfixturevalue("um_admin_ready")
