import pytest

from utils.sanity_flows import (
    assert_sanity_02_firmware_upgrade_keep_settings,
    assert_sanity_03_firmware_upgrade_without_keep_settings,
    assert_sanity_04_wrong_model_firmware_rejected,
    assert_sanity_05_abort_upgrade_handling,
    assert_sanity_06_power_loss_during_upgrade,
    assert_sanity_07_gui_login_validation,
    assert_sanity_17_system_location_config,
    assert_sanity_18_system_timezone_config,
    assert_sanity_19_static_ipv4_config,
    assert_sanity_20_static_ipv6_config,
    assert_sanity_23_mgmt_vlan_config,
    assert_sanity_25_dual_stack_config,
    assert_sanity_27_link_security,
    assert_sanity_28_ddrs_mcs_modes,
    assert_sanity_29_ddrs_mcs_manual,
    assert_sanity_30_spatial_stream_mcs_ranges,
    assert_sanity_31_channel_width,
    assert_sanity_32_channel_mode,
    assert_sanity_33_frequency_range,
    assert_sanity_35_acs_auto,
    assert_sanity_36_tx_power,
    assert_sanity_37_dcs_config,
    assert_sanity_38_ntp_sync,
    assert_sanity_42_board_temperature,
    assert_sanity_45_spectrum_report,
    assert_sanity_49_wireless_link_ping,
    assert_sanity_50_rf_link_system_name,
    assert_sanity_52_rf_link_uptime_reset,
    assert_sanity_54_rf_link_rate_mcs,
    assert_sanity_55_rf_link_mcs_auto_range,
    assert_sanity_57_tx_power_detailed_stats,
    assert_sanity_60_obss_utilization,
    assert_sanity_61_combined_utilization,
    assert_sanity_62_system_summary,
    assert_sanity_76_soft_hard_reboot,
    assert_sanity_77_factory_reset_keep_settings,
    assert_sanity_81_lldp_enable_disable,
    assert_sanity_82_lldp_neighbor_detection,
    assert_sanity_83_lldp_discovery_table,
    assert_sanity_84_link_test_tool,
    assert_sanity_85_audit_config_logs,
    assert_sanity_86_arp_bridge_table,
    assert_sanity_111_installer_dashboard,
    assert_sanity_112_installer_quickstart,
    assert_sanity_113_installer_link_statistics,
    assert_sanity_114_installer_site_survey,
    assert_sanity_115_installer_soft_reboot,
)

pytestmark = pytest.mark.Sanity


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_02
async def test_sanity_02_firmware_upgrade_keep_settings(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    request,
):

    await assert_sanity_02_firmware_upgrade_keep_settings(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
    )


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_03
async def test_sanity_03_firmware_upgrade_without_keep_settings(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    request,
):


    await assert_sanity_03_firmware_upgrade_without_keep_settings(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
    )


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_04
async def test_sanity_04_wrong_model_firmware_rejected(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    request,
):
    await assert_sanity_04_wrong_model_firmware_rejected(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_05
async def test_sanity_05_abort_upgrade_handling(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    request,
):
    await assert_sanity_05_abort_upgrade_handling(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
    )


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_06
async def test_sanity_06_power_loss_during_upgrade(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    request,
):
    await assert_sanity_06_power_loss_during_upgrade(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
    )


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_07
async def test_sanity_07_gui_login_validation(
    gui_page,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_07_gui_login_validation(
        gui_page,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_17
async def test_sanity_17_system_location_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_17_system_location_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_18
async def test_sanity_18_system_timezone_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_18_system_timezone_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_19
async def test_sanity_19_static_ipv4_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_19_static_ipv4_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_20
async def test_sanity_20_static_ipv6_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_20_static_ipv6_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(23)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_23
async def test_sanity_23_mgmt_vlan_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_23_mgmt_vlan_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(25)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_25
async def test_sanity_25_dual_stack_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_25_dual_stack_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(27)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_27
async def test_sanity_27_link_security(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_27_link_security(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(28)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_28
async def test_sanity_28_ddrs_mcs_modes(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_28_ddrs_mcs_modes(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(29)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_29
async def test_sanity_29_ddrs_mcs_manual(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_29_ddrs_mcs_manual(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(30)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_30
async def test_sanity_30_spatial_stream_mcs_ranges(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_30_spatial_stream_mcs_ranges(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(31)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_31
async def test_sanity_31_channel_width(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_31_channel_width(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(32)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_32
async def test_sanity_32_channel_mode(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_32_channel_mode(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(33)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_33
async def test_sanity_33_frequency_range(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_33_frequency_range(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(35)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_35
async def test_sanity_35_acs_auto(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_35_acs_auto(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(36)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_36
async def test_sanity_36_tx_power(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_36_tx_power(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(37)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_37
async def test_sanity_37_dcs_config(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_37_dcs_config(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(38)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_38
async def test_sanity_38_ntp_sync(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_38_ntp_sync(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(42)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_42
async def test_sanity_42_board_temperature(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_42_board_temperature(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(45)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_45
async def test_sanity_45_spectrum_report(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_45_spectrum_report(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(49)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_49
async def test_sanity_49_wireless_link_ping(
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_49_wireless_link_ping(
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(50)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_50
async def test_sanity_50_rf_link_system_name(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_50_rf_link_system_name(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(52)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_52
async def test_sanity_52_rf_link_uptime_reset(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_52_rf_link_uptime_reset(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(54)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_54
async def test_sanity_54_rf_link_rate_mcs(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_54_rf_link_rate_mcs(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(55)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_55
async def test_sanity_55_rf_link_mcs_auto_range(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_55_rf_link_mcs_auto_range(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(57)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_57
async def test_sanity_57_tx_power_detailed_stats(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_57_tx_power_detailed_stats(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(60)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_60
async def test_sanity_60_obss_utilization(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_60_obss_utilization(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(61)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_61
async def test_sanity_61_combined_utilization(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_61_combined_utilization(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(62)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_62
async def test_sanity_62_system_summary(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_62_system_summary(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(76)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_76
async def test_sanity_76_soft_hard_reboot(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_76_soft_hard_reboot(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(77)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_77
async def test_sanity_77_factory_reset_keep_settings(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_77_factory_reset_keep_settings(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(81)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_81
async def test_sanity_81_lldp_enable_disable(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_81_lldp_enable_disable(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(82)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_82
async def test_sanity_82_lldp_neighbor_detection(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_82_lldp_neighbor_detection(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(83)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_83
async def test_sanity_83_lldp_discovery_table(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_83_lldp_discovery_table(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(84)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_84
async def test_sanity_84_link_test_tool(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
    request,
):
    await assert_sanity_84_link_test_tool(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
    )


@pytest.mark.order(85)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_85
async def test_sanity_85_audit_config_logs(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_85_audit_config_logs(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(86)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_86
async def test_sanity_86_arp_bridge_table(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_86_arp_bridge_table(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(111)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_111
async def test_sanity_111_installer_dashboard(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_111_installer_dashboard(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(112)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_112
async def test_sanity_112_installer_quickstart(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_112_installer_quickstart(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(113)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_113
async def test_sanity_113_installer_link_statistics(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_113_installer_link_statistics(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(114)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_114
async def test_sanity_114_installer_site_survey(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_114_installer_site_survey(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(115)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SANITY_115
async def test_sanity_115_installer_soft_reboot(
    gui_page,
    root_ssh,
    bsu_ip,
    cpe_ips,
    device_creds,
    profile_bundle,
):
    await assert_sanity_115_installer_soft_reboot(
        gui_page,
        root_ssh,
        bsu_ip=bsu_ip,
        cpe_ips=cpe_ips,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )

