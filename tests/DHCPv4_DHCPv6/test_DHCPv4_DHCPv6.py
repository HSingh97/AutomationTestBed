from __future__ import annotations

import pytest

from utils.dhcpv4_dhcpv6_flows import (
    assert_dhcp_01_dhcpv4_basic_ip_assignment,
    assert_dhcp_02_dhcpv4_lease_renewal,
    assert_dhcp_04_dhcpv4_server_reboot_impact,
    assert_dhcp_05_dhcpv4_server_down_during_renewal,
    assert_dhcp_06_dhcpv4_option_delivery,
    assert_dhcp_13_cpe_factory_reset_retain,
    assert_dhcp_14_cpe_fw_upgrade_keep_settings,
    assert_dhcp_15_bts_soft_reboot_impact,
    assert_dhcp_16_bts_hard_reboot,
    assert_dhcp_17_bts_factory_reset_retain,
    assert_dhcp_18_bts_fw_upgrade_keep_settings,
    assert_dhcp_19_reboot_during_traffic,
    assert_dhcp_21_dhcpv4_server_down_scenario,
    assert_dhcp_22_dhcpv6_stateful_address_assignment,
    assert_dhcp_24_dhcpv6_lease_renewal,
    assert_dhcp_25_dhcpv6_prefix_delegation,
    assert_dhcp_26_dhcpv6_server_reboot,
    assert_dhcp_27_dhcpv6_rapid_commit,
    assert_dhcp_29_cpe_soft_reboot,
    assert_dhcp_30_cpe_hard_reboot,
    assert_dhcp_31_cpe_soft_reset,
    assert_dhcp_32_cpe_factory_reset_retain,
    assert_dhcp_33_cpe_fw_upgrade_keep_settings,
    assert_dhcp_34_bts_soft_reboot_impact,
    assert_dhcp_35_bts_hard_reboot,
    assert_dhcp_36_bts_factory_reset_retain,
    assert_dhcp_37_bts_fw_upgrade_keep_settings,
    assert_dhcp_38_reboot_during_traffic,
    assert_dhcp_40_dhcpv6_server_down_scenario,
    assert_dhcp_41_cpe_dhcpv6_address_assignment,
    assert_dhcp_42_bts_cpe_dhcpv6_clients,
    assert_dhcp_43_dhcpv6_lease_renewal,
    assert_dhcp_44_cpe_soft_reboot,
    assert_dhcp_45_cpe_hard_reboot,
    assert_dhcp_46_cpe_soft_reset,
    assert_dhcp_47_cpe_factory_reset_retain,
    assert_dhcp_48_cpe_fw_upgrade,
    assert_dhcp_49_bts_soft_reboot_impact,
    assert_dhcp_50_bts_hard_reboot,
    assert_dhcp_51_bts_factory_reset_retain,
    assert_dhcp_52_bts_fw_upgrade,
    assert_dhcp_55_dhcpv6_server_down,
)

pytestmark = [pytest.mark.DHCPv4_DHCPv6]


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_01
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_01_dhcpv4_basic_ip_assignment(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    await assert_dhcp_01_dhcpv4_basic_ip_assignment(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_02
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_02_dhcpv4_lease_renewal(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    await assert_dhcp_02_dhcpv4_lease_renewal(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_04
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_04_dhcpv4_server_reboot_impact(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    await assert_dhcp_04_dhcpv4_server_reboot_impact(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_05
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_05_dhcpv4_server_down_during_renewal(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    await assert_dhcp_05_dhcpv4_server_down_during_renewal(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_06
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_06_dhcpv4_option_delivery(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    await assert_dhcp_06_dhcpv4_option_delivery(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_13
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_13_cpe_factory_reset_retain(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """DHCP_13 — Factory Reset retain/keep settings on CPE; DHCPv4 retained + auto IP."""
    await assert_dhcp_13_cpe_factory_reset_retain(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_14
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_14_cpe_fw_upgrade_keep_settings(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):

    await assert_dhcp_14_cpe_fw_upgrade_keep_settings(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_15
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_15_bts_soft_reboot_impact(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_15 — Soft Reboot BTS Impact (Negative): CPE DHCPv4 IP → reboot BTS →
    IP retained/renewed + connectivity restored → restore static.
    """
    await assert_dhcp_15_bts_soft_reboot_impact(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(16)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_16
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_16_bts_hard_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):

    await assert_dhcp_16_bts_hard_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_17
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_17_bts_factory_reset_retain(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_17 — Factory Reset retain BTS (Negative): keep settings on BTS, CPE
    reconnects, DHCPv4 still works → restore static.
    """
    await assert_dhcp_17_bts_factory_reset_retain(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_18
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_18_bts_fw_upgrade_keep_settings(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_18 — FW upgrade keep settings on BTS: DHCP → flash keep → long wait →
    CPE reconnects + DHCPv4 IP → restore static (same keep-settings path as DHCP_14).
    """
    await assert_dhcp_18_bts_fw_upgrade_keep_settings(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_19
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_19_reboot_during_traffic(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_19 — Reboot During Traffic (Negative): continuous BTS→CPE traffic,
    soft reboot CPE (or BTS), temporary drop then resume, no lease corruption.
    """
    await assert_dhcp_19_reboot_during_traffic(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(21)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_21
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_21_dhcpv4_server_down_scenario(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_21 — DHCPv4 Server Down: obtain IP → disable BTS DHCP → restart CPE
    client; existing lease retained; no new IP after expiry → restore static.
    """
    await assert_dhcp_21_dhcpv4_server_down_scenario(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(22)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_22
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_22_dhcpv6_stateful_address_assignment(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_22 — DHCPv6 Stateful: enable server → connect CPE → enable client →
    verify address / lease / reachability.
    """
    await assert_dhcp_22_dhcpv6_stateful_address_assignment(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(24)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_24
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_24_dhcpv6_lease_renewal(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_24 — DHCPv6 Lease Renewal: wait T1, monitor Renew/Reply,
    verify same address and no packet loss.
    """
    await assert_dhcp_24_dhcpv6_lease_renewal(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(25)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_25
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_25_dhcpv6_prefix_delegation(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_25 — DHCPv6 Prefix Delegation: enable PD on CPE, verify prefix,
    configure downstream subnet, confirm usable.
    """
    await assert_dhcp_25_dhcpv6_prefix_delegation(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(26)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_26
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_26_dhcpv6_server_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_26 — DHCPv6 Server Reboot: reboot BTS, monitor CPE connectivity,
    wait for renewal; CPE retains IPv6 then lease renews after recovery.
    """
    await assert_dhcp_26_dhcpv6_server_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(27)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_27
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_27_dhcpv6_rapid_commit(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_27 — DHCPv6 Rapid Commit: start CPE client, capture packets;
    expect Solicit/Reply 2-message exchange and faster IP assignment.
    """
    await assert_dhcp_27_dhcpv6_rapid_commit(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(29)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_29
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_29_cpe_soft_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_29 — Soft Reboot CPE: ensure DHCPv6 IP, soft reboot via CLI,
    verify IPv6 re-acquired, device reachable, no excessive delay.
    """
    await assert_dhcp_29_cpe_soft_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(30)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_30
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_30_cpe_hard_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    """
    DHCP_30 — Hard Reboot CPE: PDU power OFF 30s ON; DHCPv6 assigned;
    no lease corruption.
    """
    await assert_dhcp_30_cpe_hard_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )


@pytest.mark.order(31)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_31
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_31_cpe_soft_reset(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_31 — Soft Reset CPE: /etc/init.d/network restart; DHCPv6 restarts;
    IP re-assigned.
    """
    await assert_dhcp_31_cpe_soft_reset(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(32)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_32
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_32_cpe_factory_reset_retain(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_32 — Factory Reset retain/keep settings on CPE; DHCPv6 path retained;
    IP obtained automatically.
    """
    await assert_dhcp_32_cpe_factory_reset_retain(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(33)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_33
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_33_cpe_fw_upgrade_keep_settings(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_33 — FW upgrade keep settings on CPE (latest /tftpboot model image);
    DHCPv6 client persists + IP after reboot; restore original CPE IPv6.
    """
    await assert_dhcp_33_cpe_fw_upgrade_keep_settings(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(34)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_34
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_34_bts_soft_reboot_impact(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_34 — Soft Reboot BTS Impact: CPE has DHCPv6 IP → reboot BTS →
    temporary disconnect → retain/renew IP → connectivity restored.
    """
    await assert_dhcp_34_bts_soft_reboot_impact(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(35)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_35
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_35_bts_hard_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    """
    DHCP_35 — Hard Reboot BTS: PDU power OFF 30s ON; CPE reconnects;
    DHCPv6 works; no failure; restore original CPE IPv6.
    """
    await assert_dhcp_35_bts_hard_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )


@pytest.mark.order(36)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_36
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_36_bts_factory_reset_retain(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_36 — Factory Reset retain/keep settings on BTS; DHCPv6 still serves;
    CPE obtains IP; restore original CPE IPv6.
    """
    await assert_dhcp_36_bts_factory_reset_retain(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(37)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_37
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_37_bts_fw_upgrade_keep_settings(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_37 — FW upgrade keep settings on BTS (latest /tftpboot model image);
    DHCPv6 works + CPE IP; no packet drop; restore original CPE IPv6.
    """
    await assert_dhcp_37_bts_fw_upgrade_keep_settings(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(38)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_38
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_38_reboot_during_traffic(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_38 — Reboot During Traffic (Negative): continuous BTS→CPE ping6,
    soft reboot CPE (or BTS), temporary drop then resume, no lease corruption.
    """
    await assert_dhcp_38_reboot_during_traffic(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(40)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_40
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_40_dhcpv6_server_down_scenario(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_40 — DHCPv6 Server Down: obtain IP → disable BTS DHCPv6 → restart CPE
    client; existing lease retained; no new IP after expiry → restore orig IPv6.
    """
    await assert_dhcp_40_dhcpv6_server_down_scenario(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(41)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_41
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_41_cpe_dhcpv6_address_assignment(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_41 — CPE DHCPv6 Address Assignment: enable router DHCPv6, connect
    BTS & CPE, enable CPE client, verify ip -6 addr plus gateway/DNS.
    """
    await assert_dhcp_41_cpe_dhcpv6_address_assignment(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(42)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_42
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_42_bts_cpe_dhcpv6_clients(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_42 — BTS & CPE as DHCPv6 Clients: enable client on BTS then CPE,
    verify unique IPv6 addresses with no duplication.
    """
    await assert_dhcp_42_bts_cpe_dhcpv6_clients(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(43)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_43
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_43_dhcpv6_lease_renewal(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_43 — DHCPv6 Lease Renewal: obtain IPv6, wait T1 with ping6 monitor,
    verify smooth renewal and no packet loss; restore original CPE IPv6.
    """
    await assert_dhcp_43_dhcpv6_lease_renewal(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(44)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_44
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_44_cpe_soft_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_44 — Soft Reboot CPE: reboot via bench PC, verify IPv6 re-acquired
    and device reachable; restore original CPE IPv6.
    """
    await assert_dhcp_44_cpe_soft_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(45)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_45
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_45_cpe_hard_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    """
    DHCP_45 — Hard Reboot CPE: PDU power cycle, verify IPv6 assigned
    successfully; restore original CPE IPv6.
    """
    await assert_dhcp_45_cpe_hard_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )


@pytest.mark.order(46)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_46
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_46_cpe_soft_reset(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_46 — Soft Reset CPE: /etc/init.d/network restart, verify DHCPv6
    restarts and IPv6 is assigned; restore original CPE IPv6.
    """
    await assert_dhcp_46_cpe_soft_reset(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(47)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_47
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_47_cpe_factory_reset_retain(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_47 — Factory Reset retain/keep settings on CPE; config retained;
    IPv6 assigned; restore original CPE IPv6.
    """
    await assert_dhcp_47_cpe_factory_reset_retain(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(48)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_48
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_48_cpe_fw_upgrade(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_48 — Firmware Upgrade CPE: latest /tftpboot model image, reboot,
    verify DHCPv6 works; restore original CPE IPv6.
    """
    await assert_dhcp_48_cpe_fw_upgrade(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(49)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_49
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_49_bts_soft_reboot_impact(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_49 — Soft Reboot BTS Impact: reboot BTS, monitor CPE during outage,
    verify IPv6 restored; restore original CPE IPv6.
    """
    await assert_dhcp_49_bts_soft_reboot_impact(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(50)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_50
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_50_bts_hard_reboot(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    """
    DHCP_50 — Hard Reboot BTS: PDU power cycle BTS, verify CPE IPv6
    restored with no failure; restore original CPE IPv6.
    """
    await assert_dhcp_50_bts_hard_reboot(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )


@pytest.mark.order(51)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_51
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_51_bts_factory_reset_retain(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_51 — Factory Reset retain/keep settings on BTS; reconnect CPE;
    verify DHCPv6 forwarding works; restore original CPE IPv6.
    """
    await assert_dhcp_51_bts_factory_reset_retain(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(52)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_52
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_52_bts_fw_upgrade(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_52 — Firmware Upgrade BTS: latest /tftpboot model image, reboot,
    verify IPv6 with no DHCPv6 issue; restore original CPE IPv6.
    """
    await assert_dhcp_52_bts_fw_upgrade(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )


@pytest.mark.order(55)
@pytest.mark.asyncio(scope="session")
@pytest.mark.DHCP_55
@pytest.mark.DHCPv4_DHCPv6
async def test_dhcp_55_dhcpv6_server_down(
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
):
    """
    DHCP_55 — DHCPv6 Server Down: obtain IPv6, stop BTS DHCPv6, restart CPE
    client; lease retained until expiry; restore original CPE IPv6.
    """
    await assert_dhcp_55_dhcpv6_server_down(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
    )

