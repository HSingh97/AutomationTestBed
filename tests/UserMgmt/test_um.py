"""User Management suite — UM_01 … UM_50 (plan sheet User Mgmt).

Requires live GUI::

  pytest tests/UserMgmt -m UserMgmt --allow-um-lab -q
"""

from __future__ import annotations

import pytest

from utils.um_flows import execute_um_case, open_um_page

pytestmark = [pytest.mark.UserMgmt]


async def _run(case_id: str, um_host) -> None:
    async with open_um_page() as page:
        await execute_um_case(case_id, page, um_host)


@pytest.mark.UM_01
@pytest.mark.Functional
async def test_um_01(um_case, um_host):
    """Admin login with default password."""
    await _run("UM_01", um_host)


@pytest.mark.UM_02
@pytest.mark.Functional
async def test_um_02(um_case, um_host):
    """User login with default password."""
    await _run("UM_02", um_host)


@pytest.mark.UM_03
@pytest.mark.Functional
async def test_um_03(um_case, um_host):
    """Installer login with default password."""
    await _run("UM_03", um_host)


@pytest.mark.UM_04
@pytest.mark.Functional
async def test_um_04(um_case, um_host):
    """Admin changes User password."""
    await _run("UM_04", um_host)


@pytest.mark.UM_05
@pytest.mark.Functional
async def test_um_05(um_case, um_host):
    """User attempts to change password."""
    await _run("UM_05", um_host)


@pytest.mark.UM_06
@pytest.mark.Functional
async def test_um_06(um_case, um_host):
    """Installer attempts to change password."""
    await _run("UM_06", um_host)


@pytest.mark.UM_07
@pytest.mark.Functional
async def test_um_07(um_case, um_host):
    """Admin modifies wireless configuration."""
    await _run("UM_07", um_host)


@pytest.mark.UM_08
@pytest.mark.Functional
async def test_um_08(um_case, um_host):
    """User attempts to modify SSID."""
    await _run("UM_08", um_host)


@pytest.mark.UM_09
@pytest.mark.Functional
async def test_um_09(um_case, um_host):
    """Installer modifies Quick Start IP."""
    await _run("UM_09", um_host)


@pytest.mark.UM_10
@pytest.mark.Functional
async def test_um_10(um_case, um_host):
    """Admin initiates reboot."""
    await _run("UM_10", um_host)


@pytest.mark.UM_11
@pytest.mark.Functional
async def test_um_11(um_case, um_host):
    """User attempts reboot."""
    await _run("UM_11", um_host)


@pytest.mark.UM_12
@pytest.mark.Functional
async def test_um_12(um_case, um_host):
    """Installer attempts reboot."""
    await _run("UM_12", um_host)


@pytest.mark.UM_13
@pytest.mark.Functional
async def test_um_13(um_case, um_host):
    """Admin upgrades firmware."""
    await _run("UM_13", um_host)


@pytest.mark.UM_14
@pytest.mark.Functional
async def test_um_14(um_case, um_host):
    """User attempts firmware upgrade."""
    await _run("UM_14", um_host)


@pytest.mark.UM_15
@pytest.mark.Functional
async def test_um_15(um_case, um_host):
    """Installer attempts firmware upgrade."""
    await _run("UM_15", um_host)


@pytest.mark.UM_16
@pytest.mark.Functional
async def test_um_16(um_case, um_host):
    """Admin downloads configuration file."""
    await _run("UM_16", um_host)


@pytest.mark.UM_17
@pytest.mark.Functional
async def test_um_17(um_case, um_host):
    """User attempts config download."""
    await _run("UM_17", um_host)


@pytest.mark.UM_18
@pytest.mark.Functional
async def test_um_18(um_case, um_host):
    """Installer attempts config download."""
    await _run("UM_18", um_host)


@pytest.mark.UM_19
@pytest.mark.Functional
async def test_um_19(um_case, um_host):
    """Admin initiates factory reset."""
    await _run("UM_19", um_host)


@pytest.mark.UM_20
@pytest.mark.Functional
async def test_um_20(um_case, um_host):
    """User attempts factory reset."""
    await _run("UM_20", um_host)


@pytest.mark.UM_21
@pytest.mark.Functional
async def test_um_21(um_case, um_host):
    """Installer attempts factory reset."""
    await _run("UM_21", um_host)


@pytest.mark.UM_22
@pytest.mark.Functional
async def test_um_22(um_case, um_host):
    """Admin runs link test tool."""
    await _run("UM_22", um_host)


@pytest.mark.UM_23
@pytest.mark.Functional
async def test_um_23(um_case, um_host):
    """User attempts link test."""
    await _run("UM_23", um_host)


@pytest.mark.UM_24
@pytest.mark.Functional
async def test_um_24(um_case, um_host):
    """Installer attempts link test."""
    await _run("UM_24", um_host)


@pytest.mark.UM_25
@pytest.mark.Functional
async def test_um_25(um_case, um_host):
    """Admin views logs."""
    await _run("UM_25", um_host)


@pytest.mark.UM_26
@pytest.mark.Functional
async def test_um_26(um_case, um_host):
    """User views logs."""
    await _run("UM_26", um_host)


@pytest.mark.UM_27
@pytest.mark.Functional
async def test_um_27(um_case, um_host):
    """Installer views logs."""
    await _run("UM_27", um_host)


@pytest.mark.UM_28
@pytest.mark.Functional
async def test_um_28(um_case, um_host):
    """Admin runs diagnostics (Ping/Traceroute)."""
    await _run("UM_28", um_host)


@pytest.mark.UM_29
@pytest.mark.Functional
async def test_um_29(um_case, um_host):
    """User runs diagnostics (Ping/Traceroute)."""
    await _run("UM_29", um_host)


@pytest.mark.UM_30
@pytest.mark.Functional
async def test_um_30(um_case, um_host):
    """Installer runs diagnostics (Ping/Traceroute)."""
    await _run("UM_30", um_host)


@pytest.mark.UM_34
@pytest.mark.Functional
async def test_um_34(um_case, um_host):
    """Admin session timeout."""
    await _run("UM_34", um_host)


@pytest.mark.UM_35
@pytest.mark.Functional
async def test_um_35(um_case, um_host):
    """User session timeout."""
    await _run("UM_35", um_host)


@pytest.mark.UM_36
@pytest.mark.Functional
async def test_um_36(um_case, um_host):
    """Installer session timeout."""
    await _run("UM_36", um_host)


@pytest.mark.UM_37
@pytest.mark.Functional
async def test_um_37(um_case, um_host):
    """Admin attempts simultaneous login."""
    await _run("UM_37", um_host)


@pytest.mark.UM_38
@pytest.mark.Functional
async def test_um_38(um_case, um_host):
    """User attempts simultaneous login."""
    await _run("UM_38", um_host)


@pytest.mark.UM_39
@pytest.mark.Functional
async def test_um_39(um_case, um_host):
    """Installer attempts simultaneous login."""
    await _run("UM_39", um_host)


@pytest.mark.UM_40
@pytest.mark.Functional
async def test_um_40(um_case, um_host):
    """Admin disconnects wireless link."""
    await _run("UM_40", um_host)


@pytest.mark.UM_41
@pytest.mark.Functional
async def test_um_41(um_case, um_host):
    """User attempts disconnect wireless link."""
    await _run("UM_41", um_host)


@pytest.mark.UM_42
@pytest.mark.Functional
async def test_um_42(um_case, um_host):
    """Installer attempts disconnect wireless link."""
    await _run("UM_42", um_host)


@pytest.mark.UM_43
@pytest.mark.Functional
async def test_um_43(um_case, um_host):
    """Admin changes encryption type."""
    await _run("UM_43", um_host)


@pytest.mark.UM_44
@pytest.mark.Functional
async def test_um_44(um_case, um_host):
    """User attempts encryption change."""
    await _run("UM_44", um_host)


@pytest.mark.UM_45
@pytest.mark.Functional
async def test_um_45(um_case, um_host):
    """Installer attempts encryption change."""
    await _run("UM_45", um_host)


@pytest.mark.UM_46
@pytest.mark.Functional
async def test_um_46(um_case, um_host):
    """Admin modifies VLAN ID."""
    await _run("UM_46", um_host)


@pytest.mark.UM_47
@pytest.mark.Functional
async def test_um_47(um_case, um_host):
    """User attempts VLAN modification."""
    await _run("UM_47", um_host)


@pytest.mark.UM_48
@pytest.mark.Functional
async def test_um_48(um_case, um_host):
    """Installer modifies VLAN ID."""
    await _run("UM_48", um_host)


@pytest.mark.UM_49
@pytest.mark.Functional
async def test_um_49(um_case, um_host):
    """Admin views site survey."""
    await _run("UM_49", um_host)


@pytest.mark.UM_50
@pytest.mark.Functional
async def test_um_50(um_case, um_host):
    """Installer views site survey."""
    await _run("UM_50", um_host)


# Wrong-password cases last — failed logins can temporarily lock LuCI accounts
# and would flake later cases if run mid-suite.
@pytest.mark.UM_31
@pytest.mark.Functional
async def test_um_31(um_case, um_host):
    """Admin login with wrong password."""
    await _run("UM_31", um_host)


@pytest.mark.UM_32
@pytest.mark.Functional
async def test_um_32(um_case, um_host):
    """User login with wrong password."""
    await _run("UM_32", um_host)


@pytest.mark.UM_33
@pytest.mark.Functional
async def test_um_33(um_case, um_host):
    """Installer login with wrong password."""
    await _run("UM_33", um_host)
