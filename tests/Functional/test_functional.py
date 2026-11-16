import pytest

from utils.functional_flows import (
    assert_ft_02_memory_overload,
    assert_ft_03_config_retain_hard_reboot,
    assert_ft_05_backend_config_per_bandwidth,
    assert_ft_06_rf_stats_backend_reflect,
    assert_ft_07_factory_reset_cpe_defaults,
    assert_ft_08_network_soft_reset,
    assert_ft_09_config_retain_soft_reboot,
    assert_ft_10_mgmt_vlan_precheck,
    assert_ft_11_mgmt_vlan_gateway_change,
    assert_ft_12_mgmt_vlan_ipv6_change,
    assert_ft_13_mgmt_vlan_ipv4_netmask_change,
    assert_ft_14_mgmt_vlan_ipv4_gateway_change,
    assert_ft_15_mgmt_vlan_ipv4_change,
    assert_ft_16_mgmt_vlan_channel_width_change,
    assert_ft_17_mgmt_vlan_prefix_change,
    assert_ft_18_mgmt_vlan_gateway_change,
    assert_ft_19_mgmt_vlan_ipv6_change,
    assert_ft_20_mgmt_vlan_ipv4_netmask_change,
    assert_ft_21_mgmt_vlan_ipv4_gateway_change,
    assert_ft_22_mgmt_vlan_ipv4_change,
    assert_ft_23_mgmt_vlan_channel_width_change,
    assert_ft_25_tx_power_gui_ssh_mapping,
    assert_ft_26_gui_address_bar_ip,
    assert_ft_27_gui_auto_logout,
    assert_ft_28_commit_after_config_change_reboot,
)

pytestmark = [pytest.mark.sanity, pytest.mark.Functional]


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_02
@pytest.mark.Functional
async def test_ft_02_memory_overload(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_02_memory_overload(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_03
@pytest.mark.Functional
async def test_ft_03_config_retain_hard_reboot(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_03_config_retain_hard_reboot(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_05
@pytest.mark.Functional
async def test_ft_05_backend_config_per_bandwidth(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_05_backend_config_per_bandwidth(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_06
@pytest.mark.Functional
async def test_ft_06_rf_stats_backend_reflect(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_06_rf_stats_backend_reflect(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(7)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_07
@pytest.mark.Functional
async def test_ft_07_factory_reset_cpe_defaults(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, gui_page, request,
):
    await assert_ft_07_factory_reset_cpe_defaults(
        root_ssh,cpe_ips,gui_page=gui_page,bsu_ip=bsu_ip,device_creds=device_creds,profile_bundle=profile_bundle,request=request,
    )


@pytest.mark.order(8)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_08
@pytest.mark.Functional
async def test_ft_08_network_soft_reset(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_08_network_soft_reset(
        root_ssh,cpe_ips,bsu_ip=bsu_ip,device_creds=device_creds,profile_bundle=profile_bundle,request=request,
    )


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_09
@pytest.mark.Functional
async def test_ft_09_config_retain_soft_reboot(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_09_config_retain_soft_reboot(
        root_ssh,cpe_ips,bsu_ip=bsu_ip,device_creds=device_creds,profile_bundle=profile_bundle,request=request,
    )


@pytest.mark.order(10)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_10
@pytest.mark.Functional
async def test_ft_10_mgmt_vlan_precheck(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_10_mgmt_vlan_precheck(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(11)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_11
@pytest.mark.Functional
async def test_ft_11_mgmt_vlan_gateway_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_11_mgmt_vlan_gateway_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(12)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_12
@pytest.mark.Functional
async def test_ft_12_mgmt_vlan_ipv6_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_12_mgmt_vlan_ipv6_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_13
@pytest.mark.Functional
async def test_ft_13_mgmt_vlan_ipv4_netmask_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_13_mgmt_vlan_ipv4_netmask_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_14
@pytest.mark.Functional
async def test_ft_14_mgmt_vlan_ipv4_gateway_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_14_mgmt_vlan_ipv4_gateway_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_15
@pytest.mark.Functional
async def test_ft_15_mgmt_vlan_ipv4_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_15_mgmt_vlan_ipv4_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(16)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_16
@pytest.mark.Functional
async def test_ft_16_mgmt_vlan_channel_width_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_16_mgmt_vlan_channel_width_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_17
@pytest.mark.Functional
async def test_ft_17_mgmt_vlan_prefix_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_17_mgmt_vlan_prefix_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_18
@pytest.mark.Functional
async def test_ft_18_mgmt_vlan_gateway_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_18_mgmt_vlan_gateway_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_19
@pytest.mark.Functional
async def test_ft_19_mgmt_vlan_ipv6_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_19_mgmt_vlan_ipv6_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_20
@pytest.mark.Functional
async def test_ft_20_mgmt_vlan_ipv4_netmask_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_20_mgmt_vlan_ipv4_netmask_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(21)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_21
@pytest.mark.Functional
async def test_ft_21_mgmt_vlan_ipv4_gateway_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_21_mgmt_vlan_ipv4_gateway_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(22)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_22
@pytest.mark.Functional
async def test_ft_22_mgmt_vlan_ipv4_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_22_mgmt_vlan_ipv4_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(23)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_23
@pytest.mark.Functional
async def test_ft_23_mgmt_vlan_channel_width_change(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_23_mgmt_vlan_channel_width_change(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(25)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_25
@pytest.mark.Functional
async def test_ft_25_tx_power_gui_ssh_mapping(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_25_tx_power_gui_ssh_mapping(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds,profile_bundle=profile_bundle, request=request,
    )
 

@pytest.mark.order(26)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_26
@pytest.mark.Functional
async def test_ft_26_gui_address_bar_ip(
    cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_26_gui_address_bar_ip(
        cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )


@pytest.mark.order(27)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_27
@pytest.mark.Functional
async def test_ft_27_gui_auto_logout(
    cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_27_gui_auto_logout(
        cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )

@pytest.mark.order(28)
@pytest.mark.asyncio(scope="session")
@pytest.mark.FT_28
@pytest.mark.Functional
async def test_ft_28_commit_after_config_change_reboot(
    root_ssh, cpe_ips, bsu_ip, device_creds, profile_bundle, request,
):
    await assert_ft_28_commit_after_config_change_reboot(
        root_ssh, cpe_ips, bsu_ip=bsu_ip, device_creds=device_creds, profile_bundle=profile_bundle, request=request,
    )

