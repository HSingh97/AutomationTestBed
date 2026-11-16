"""
Run:
  pytest tests/DDRS/test_ddrs.py --profile ddrs_lab -v
  pytest tests/DDRS/test_ddrs.py --profile ddrs_lab -m DDRS_01 -v
"""

from __future__ import annotations

import pytest

from utils.ddrs_flows import (
    assert_ddrs_01_associate_with_traffic,
    assert_ddrs_02_wireless_restart,
    assert_ddrs_03_network_restart,
    assert_ddrs_04_ethernet_duplex_restart,
    assert_ddrs_05_soft_reboot,
    assert_ddrs_07_low_rssi_rate_variation,
    assert_ddrs_08_wireless_restart_low_rssi,
    assert_ddrs_09_network_restart_low_rssi,
    assert_ddrs_10_ethernet_duplex_restart_low_rssi,
    assert_ddrs_11_soft_reboot_low_rssi,
    assert_ddrs_13_cochannel_rate_variation,
    assert_ddrs_14_cochannel_wireless_restart,
    assert_ddrs_15_cochannel_network_restart,
    assert_ddrs_16_cochannel_ethernet_duplex_restart,
    assert_ddrs_17_cochannel_soft_reboot,
    assert_ddrs_19_bts_ddrs_cpe_fixed_rate,
    assert_ddrs_20_bts_ddrs_cpe_fixed_wireless_restart,
    assert_ddrs_21_bts_ddrs_cpe_fixed_network_restart,
    assert_ddrs_22_bts_ddrs_cpe_fixed_ethernet_restart,
    assert_ddrs_23_bts_ddrs_cpe_fixed_soft_reboot,
    assert_ddrs_25_bts_fixed_cpe_ddrs_rate,
    assert_ddrs_26_bts_fixed_cpe_ddrs_wireless_restart,
    assert_ddrs_27_bts_fixed_cpe_ddrs_network_restart,
    assert_ddrs_28_bts_fixed_cpe_ddrs_ethernet_restart,
    assert_ddrs_29_bts_fixed_cpe_ddrs_soft_reboot,
    assert_ddrs_31_p2mp_throughput_stability,
    assert_ddrs_32_p2mp_associate_with_traffic,
    assert_ddrs_33_p2mp_wireless_restart,
    assert_ddrs_34_p2mp_network_restart,
    assert_ddrs_35_p2mp_ethernet_duplex_restart,
    assert_ddrs_36_p2mp_soft_reboot,
    assert_ddrs_38_p2mp_cochannel_rate_variation,
    assert_ddrs_39_p2mp_cochannel_wireless_restart,
    assert_ddrs_40_p2mp_cochannel_network_restart,
    assert_ddrs_41_p2mp_cochannel_ethernet_duplex_restart,
    assert_ddrs_42_p2mp_cochannel_soft_reboot,
    assert_ddrs_50_p2mp_bts_ddrs_cpe_fixed_rate,
    assert_ddrs_51_p2mp_bts_ddrs_cpe_fixed_wireless_restart,
    assert_ddrs_52_p2mp_bts_ddrs_cpe_fixed_network_restart,
    assert_ddrs_53_p2mp_bts_ddrs_cpe_fixed_ethernet_restart,
    assert_ddrs_54_p2mp_bts_ddrs_cpe_fixed_soft_reboot,
    assert_ddrs_56_p2mp_bts_fixed_cpe_ddrs_rate,
    assert_ddrs_57_p2mp_bts_fixed_cpe_ddrs_wireless_restart,
    assert_ddrs_58_p2mp_bts_fixed_cpe_ddrs_network_restart,
    assert_ddrs_59_p2mp_bts_fixed_cpe_ddrs_ethernet_restart,
    assert_ddrs_60_p2mp_bts_fixed_cpe_ddrs_soft_reboot,
    assert_ddrs_65_throughput_latency_cpu,
    assert_ddrs_66_spatial_stream_modes,
    assert_ddrs_71_reset_min_rate_after_reconnect,
)

pytestmark = pytest.mark.DDRS


@pytest.mark.order(1)
@pytest.mark.DDRS_01
def test_ddrs_01_associate_with_traffic(ddrs_config):
    """Associate BTS & CPE with DDRS enabled + traffic; verify operating rate & SNR log."""
    assert_ddrs_01_associate_with_traffic(ddrs_config)


@pytest.mark.order(2)
@pytest.mark.DDRS_02
def test_ddrs_02_wireless_restart(ddrs_config):
    """Wireless parameter change + wireless restart on BTS & CPE; verify DDRS rate & log."""
    assert_ddrs_02_wireless_restart(ddrs_config)


@pytest.mark.order(3)
@pytest.mark.DDRS_03
def test_ddrs_03_network_restart(ddrs_config):
    """Network/IP setting change + network restart on BTS & CPE; verify rate & log."""
    assert_ddrs_03_network_restart(ddrs_config)


@pytest.mark.order(4)
@pytest.mark.DDRS_04
def test_ddrs_04_ethernet_duplex_restart(ddrs_config):
    """Ethernet duplex change + restart; DDRS must work without change."""
    assert_ddrs_04_ethernet_duplex_restart(ddrs_config)


@pytest.mark.order(5)
@pytest.mark.DDRS_05
def test_ddrs_05_soft_reboot(ddrs_config):
    """Soft reboot BTS & CPE (negative); DDRS must recover unchanged."""
    assert_ddrs_05_soft_reboot(ddrs_config)


@pytest.mark.order(7)
@pytest.mark.DDRS_07
def test_ddrs_07_low_rssi_rate_variation(ddrs_config):
    """Reduce Tx power for low RSSI; verify data rate variation, operating rate & SNR log."""
    assert_ddrs_07_low_rssi_rate_variation(ddrs_config)


@pytest.mark.order(8)
@pytest.mark.DDRS_08
def test_ddrs_08_wireless_restart_low_rssi(ddrs_config):
    """Low RSSI — wireless parameter change + wireless restart; verify operating rate & log."""
    assert_ddrs_08_wireless_restart_low_rssi(ddrs_config)


@pytest.mark.order(9)
@pytest.mark.DDRS_09
def test_ddrs_09_network_restart_low_rssi(ddrs_config):
    """Low RSSI — network/IP setting change + network restart; DDRS must work without change."""
    assert_ddrs_09_network_restart_low_rssi(ddrs_config)


@pytest.mark.order(10)
@pytest.mark.DDRS_10
def test_ddrs_10_ethernet_duplex_restart_low_rssi(ddrs_config):
    """Low RSSI — ethernet duplex change + restart; DDRS must work without change."""
    assert_ddrs_10_ethernet_duplex_restart_low_rssi(ddrs_config)


@pytest.mark.order(11)
@pytest.mark.DDRS_11
def test_ddrs_11_soft_reboot_low_rssi(ddrs_config):
    """Low RSSI — soft reboot BTS & CPE (negative); DDRS must recover unchanged."""
    assert_ddrs_11_soft_reboot_low_rssi(ddrs_config)


@pytest.mark.order(13)
@pytest.mark.DDRS_13
def test_ddrs_13_cochannel_rate_variation(ddrs_config):
    """Co-channel secondary link — verify data rate variation under interference."""
    assert_ddrs_13_cochannel_rate_variation(ddrs_config)


@pytest.mark.order(14)
@pytest.mark.DDRS_14
def test_ddrs_14_cochannel_wireless_restart(ddrs_config):
    """Co-channel — wireless restart under secondary interference; verify rate & log."""
    assert_ddrs_14_cochannel_wireless_restart(ddrs_config)


@pytest.mark.order(15)
@pytest.mark.DDRS_15
def test_ddrs_15_cochannel_network_restart(ddrs_config):
    """Co-channel — network restart under secondary interference; verify rate & log."""
    assert_ddrs_15_cochannel_network_restart(ddrs_config)


@pytest.mark.order(16)
@pytest.mark.DDRS_16
def test_ddrs_16_cochannel_ethernet_duplex_restart(ddrs_config):
    """Co-channel — ethernet duplex restart under secondary interference."""
    assert_ddrs_16_cochannel_ethernet_duplex_restart(ddrs_config)


@pytest.mark.order(17)
@pytest.mark.DDRS_17
def test_ddrs_17_cochannel_soft_reboot(ddrs_config):
    """Co-channel — soft reboot under secondary interference (negative)."""
    assert_ddrs_17_cochannel_soft_reboot(ddrs_config)


@pytest.mark.order(19)
@pytest.mark.DDRS_19
def test_ddrs_19_bts_ddrs_cpe_fixed_rate(ddrs_config):
    """BTS DDRS enabled & CPE fixed MCS; verify operating rate & SNR log (low RSSI + co-channel)."""
    assert_ddrs_19_bts_ddrs_cpe_fixed_rate(ddrs_config)


@pytest.mark.order(20)
@pytest.mark.DDRS_20
def test_ddrs_20_bts_ddrs_cpe_fixed_wireless_restart(ddrs_config):
    """Mixed DDRS/fixed — wireless restart; verify operating rate & log."""
    assert_ddrs_20_bts_ddrs_cpe_fixed_wireless_restart(ddrs_config)


@pytest.mark.order(21)
@pytest.mark.DDRS_21
def test_ddrs_21_bts_ddrs_cpe_fixed_network_restart(ddrs_config):
    """Mixed DDRS/fixed — network restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_21_bts_ddrs_cpe_fixed_network_restart(ddrs_config)


@pytest.mark.order(22)
@pytest.mark.DDRS_22
def test_ddrs_22_bts_ddrs_cpe_fixed_ethernet_restart(ddrs_config):
    """Mixed DDRS/fixed — ethernet duplex restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_22_bts_ddrs_cpe_fixed_ethernet_restart(ddrs_config)


@pytest.mark.order(23)
@pytest.mark.DDRS_23
def test_ddrs_23_bts_ddrs_cpe_fixed_soft_reboot(ddrs_config):
    """Mixed DDRS/fixed — soft reboot (negative); DDRS/MCS unchanged if link recovers."""
    assert_ddrs_23_bts_ddrs_cpe_fixed_soft_reboot(ddrs_config)


@pytest.mark.order(25)
@pytest.mark.DDRS_25
def test_ddrs_25_bts_fixed_cpe_ddrs_rate(ddrs_config):
    """BTS fixed MCS & CPE DDRS enabled; verify operating rate & SNR log (low RSSI + co-channel)."""
    assert_ddrs_25_bts_fixed_cpe_ddrs_rate(ddrs_config)


@pytest.mark.order(26)
@pytest.mark.DDRS_26
def test_ddrs_26_bts_fixed_cpe_ddrs_wireless_restart(ddrs_config):
    """BTS fixed / CPE DDRS — wireless restart; verify operating rate & log."""
    assert_ddrs_26_bts_fixed_cpe_ddrs_wireless_restart(ddrs_config)


@pytest.mark.order(27)
@pytest.mark.DDRS_27
def test_ddrs_27_bts_fixed_cpe_ddrs_network_restart(ddrs_config):
    """BTS fixed / CPE DDRS — network restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_27_bts_fixed_cpe_ddrs_network_restart(ddrs_config)


@pytest.mark.order(28)
@pytest.mark.DDRS_28
def test_ddrs_28_bts_fixed_cpe_ddrs_ethernet_restart(ddrs_config):
    """BTS fixed / CPE DDRS — ethernet duplex restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_28_bts_fixed_cpe_ddrs_ethernet_restart(ddrs_config)


@pytest.mark.order(29)
@pytest.mark.DDRS_29
def test_ddrs_29_bts_fixed_cpe_ddrs_soft_reboot(ddrs_config):
    """BTS fixed / CPE DDRS — soft reboot (negative); DDRS/MCS unchanged if link recovers."""
    assert_ddrs_29_bts_fixed_cpe_ddrs_soft_reboot(ddrs_config)


@pytest.mark.order(31)
@pytest.mark.DDRS_31
def test_ddrs_31_p2mp_throughput_stability(ddrs_config):
    """P2MP — associate with DDRS + traffic; verify throughput, PPS, latency & stability."""
    assert_ddrs_31_p2mp_throughput_stability(ddrs_config)


@pytest.mark.order(32)
@pytest.mark.DDRS_32
def test_ddrs_32_p2mp_associate_with_traffic(ddrs_config):
    """P2MP — associate BTS & CPEs with DDRS + traffic; verify operating rate & SNR log."""
    assert_ddrs_32_p2mp_associate_with_traffic(ddrs_config)


@pytest.mark.order(33)
@pytest.mark.DDRS_33
def test_ddrs_33_p2mp_wireless_restart(ddrs_config):
    """P2MP — wireless parameter change + wireless restart; verify operating rate & log."""
    assert_ddrs_33_p2mp_wireless_restart(ddrs_config)


@pytest.mark.order(34)
@pytest.mark.DDRS_34
def test_ddrs_34_p2mp_network_restart(ddrs_config):
    """P2MP — network/IP setting change + network restart; DDRS unchanged if link recovers."""
    assert_ddrs_34_p2mp_network_restart(ddrs_config)


@pytest.mark.order(35)
@pytest.mark.DDRS_35
def test_ddrs_35_p2mp_ethernet_duplex_restart(ddrs_config):
    """P2MP — ethernet duplex change + restart; DDRS unchanged if link recovers."""
    assert_ddrs_35_p2mp_ethernet_duplex_restart(ddrs_config)


@pytest.mark.order(36)
@pytest.mark.DDRS_36
def test_ddrs_36_p2mp_soft_reboot(ddrs_config):
    """P2MP — soft reboot BTS & CPEs (negative); DDRS unchanged if link recovers."""
    assert_ddrs_36_p2mp_soft_reboot(ddrs_config)


@pytest.mark.order(38)
@pytest.mark.DDRS_38
def test_ddrs_38_p2mp_cochannel_rate_variation(ddrs_config):
    """P2MP co-channel — data rate variation under secondary interference."""
    assert_ddrs_38_p2mp_cochannel_rate_variation(ddrs_config)


@pytest.mark.order(39)
@pytest.mark.DDRS_39
def test_ddrs_39_p2mp_cochannel_wireless_restart(ddrs_config):
    """P2MP co-channel — wireless restart under secondary interference; verify rate & log."""
    assert_ddrs_39_p2mp_cochannel_wireless_restart(ddrs_config)


@pytest.mark.order(40)
@pytest.mark.DDRS_40
def test_ddrs_40_p2mp_cochannel_network_restart(ddrs_config):
    """P2MP co-channel — network restart under secondary interference; DDRS unchanged if link recovers."""
    assert_ddrs_40_p2mp_cochannel_network_restart(ddrs_config)


@pytest.mark.order(41)
@pytest.mark.DDRS_41
def test_ddrs_41_p2mp_cochannel_ethernet_duplex_restart(ddrs_config):
    """P2MP co-channel — ethernet duplex restart under secondary interference."""
    assert_ddrs_41_p2mp_cochannel_ethernet_duplex_restart(ddrs_config)


@pytest.mark.order(42)
@pytest.mark.DDRS_42
def test_ddrs_42_p2mp_cochannel_soft_reboot(ddrs_config):
    """P2MP co-channel — soft reboot under secondary interference (negative)."""
    assert_ddrs_42_p2mp_cochannel_soft_reboot(ddrs_config)


@pytest.mark.order(50)
@pytest.mark.DDRS_50
def test_ddrs_50_p2mp_bts_ddrs_cpe_fixed_rate(ddrs_config):
    """P2MP — BTS DDRS enabled & CPE fixed MCS; no CPE DDRS adapt logs; low RSSI + co-channel."""
    assert_ddrs_50_p2mp_bts_ddrs_cpe_fixed_rate(ddrs_config)


@pytest.mark.order(51)
@pytest.mark.DDRS_51
def test_ddrs_51_p2mp_bts_ddrs_cpe_fixed_wireless_restart(ddrs_config):
    """P2MP mixed DDRS/fixed — wireless restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_51_p2mp_bts_ddrs_cpe_fixed_wireless_restart(ddrs_config)


@pytest.mark.order(52)
@pytest.mark.DDRS_52
def test_ddrs_52_p2mp_bts_ddrs_cpe_fixed_network_restart(ddrs_config):
    """P2MP mixed DDRS/fixed — network restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_52_p2mp_bts_ddrs_cpe_fixed_network_restart(ddrs_config)


@pytest.mark.order(53)
@pytest.mark.DDRS_53
def test_ddrs_53_p2mp_bts_ddrs_cpe_fixed_ethernet_restart(ddrs_config):
    """P2MP mixed DDRS/fixed — ethernet duplex restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_53_p2mp_bts_ddrs_cpe_fixed_ethernet_restart(ddrs_config)


@pytest.mark.order(54)
@pytest.mark.DDRS_54
def test_ddrs_54_p2mp_bts_ddrs_cpe_fixed_soft_reboot(ddrs_config):
    """P2MP mixed DDRS/fixed — soft reboot (negative); DDRS/MCS unchanged if link recovers."""
    assert_ddrs_54_p2mp_bts_ddrs_cpe_fixed_soft_reboot(ddrs_config)


@pytest.mark.order(56)
@pytest.mark.DDRS_56
def test_ddrs_56_p2mp_bts_fixed_cpe_ddrs_rate(ddrs_config):
    """P2MP — BTS fixed MCS & CPE DDRS enabled; no BTS DDRS adapt logs; low RSSI + co-channel."""
    assert_ddrs_56_p2mp_bts_fixed_cpe_ddrs_rate(ddrs_config)


@pytest.mark.order(57)
@pytest.mark.DDRS_57
def test_ddrs_57_p2mp_bts_fixed_cpe_ddrs_wireless_restart(ddrs_config):
    """P2MP BTS fixed / CPE DDRS — wireless restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_57_p2mp_bts_fixed_cpe_ddrs_wireless_restart(ddrs_config)


@pytest.mark.order(58)
@pytest.mark.DDRS_58
def test_ddrs_58_p2mp_bts_fixed_cpe_ddrs_network_restart(ddrs_config):
    """P2MP BTS fixed / CPE DDRS — network restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_58_p2mp_bts_fixed_cpe_ddrs_network_restart(ddrs_config)


@pytest.mark.order(59)
@pytest.mark.DDRS_59
def test_ddrs_59_p2mp_bts_fixed_cpe_ddrs_ethernet_restart(ddrs_config):
    """P2MP BTS fixed / CPE DDRS — ethernet duplex restart; DDRS/MCS unchanged if link recovers."""
    assert_ddrs_59_p2mp_bts_fixed_cpe_ddrs_ethernet_restart(ddrs_config)


@pytest.mark.order(60)
@pytest.mark.DDRS_60
def test_ddrs_60_p2mp_bts_fixed_cpe_ddrs_soft_reboot(ddrs_config):
    """P2MP BTS fixed / CPE DDRS — soft reboot (negative); DDRS/MCS unchanged if link recovers."""
    assert_ddrs_60_p2mp_bts_fixed_cpe_ddrs_soft_reboot(ddrs_config)


@pytest.mark.order(65)
@pytest.mark.DDRS_65
def test_ddrs_65_throughput_latency_cpu(ddrs_config):
    """DDRS on vs fixed max rate — compare throughput, latency, jitter, and CPU."""
    assert_ddrs_65_throughput_latency_cpu(ddrs_config)


@pytest.mark.order(66)
@pytest.mark.DDRS_66
def test_ddrs_66_spatial_stream_modes(ddrs_config):
    """Spatial Auto / Single / Dual — DDRS adapts operating rate to available streams."""
    assert_ddrs_66_spatial_stream_modes(ddrs_config)


@pytest.mark.order(71)
@pytest.mark.DDRS_71
def test_ddrs_71_reset_min_rate_after_reconnect(ddrs_config):
    """After soft disconnect, DDRS must restart from MCS0 then ramp to optimal."""
    assert_ddrs_71_reset_min_rate_after_reconnect(ddrs_config)
