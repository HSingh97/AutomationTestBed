import pytest

from utils.spectrum_site_survey_flows import (
    assert_spect_analyzer_01_ui_elements,
    assert_spect_analyzer_02_scan_start_logging,
    assert_spect_analyzer_03_link_stability,
    assert_spect_analyzer_04_interference_graph,
    assert_spect_analyzer_05_sinr_plot,
    assert_spect_analyzer_06_interference_table,
    assert_spect_analyzer_09_results_download,
    assert_spect_analyzer_10_persistence_reboot,
    assert_spect_analyzer_12_midway_stop,
    assert_spect_analyzer_13_noise_floor,
    assert_spect_analyzer_14_external_interference,
    assert_spect_analyzer_15_idle_performance,
    assert_spect_analyzer_16_load_performance,
    assert_spect_analyzer_17_multi_cpe,
    assert_spect_analyzer_18_remote_session,
    assert_spect_analyzer_19_concurrent_admins,
    assert_spect_analyzer_20_page_refresh,
    assert_spect_analyzer_21_high_ap_density,
    assert_spect_analyzer_22_hidden_ssid_download,
    assert_spect_analyzer_23_graph_tooltips,
    assert_spect_analyzer_24_continuous_scan,
    assert_spect_analyzer_25_disconnect_recovery,
)

pytestmark = [pytest.mark.SpectrumSiteSurvey]


@pytest.mark.order(1)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_01
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_01_ui_elements(
    gui_page,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_01_ui_elements(
        gui_page,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(2)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_02
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_02_scan_start_logging(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_02_scan_start_logging(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(3)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_03
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_03_link_stability(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_03_link_stability(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(4)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_04
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_04_interference_graph(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_04_interference_graph(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(5)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_05
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_05_sinr_plot(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_05_sinr_plot(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(6)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_06
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_06_interference_table(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_06_interference_table(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(9)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_09
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_09_results_download(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_09_results_download(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(10)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_10
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_10_persistence_reboot(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_10_persistence_reboot(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(12)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_12
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_12_midway_stop(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_12_midway_stop(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(13)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_13
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_13_noise_floor(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_13_noise_floor(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(14)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_14
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_14_external_interference(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_14_external_interference(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(15)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_15
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_15_idle_performance(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_15_idle_performance(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(16)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_16
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_16_load_performance(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_16_load_performance(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(17)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_17
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_17_multi_cpe(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_17_multi_cpe(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(18)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_18
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_18_remote_session(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_18_remote_session(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(19)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_19
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_19_concurrent_admins(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_19_concurrent_admins(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(20)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_20
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_20_page_refresh(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_20_page_refresh(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(21)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_21
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_21_high_ap_density(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_21_high_ap_density(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(22)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_22
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_22_hidden_ssid_download(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_22_hidden_ssid_download(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(23)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_23
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_23_graph_tooltips(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_23_graph_tooltips(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(24)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_24
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_24_continuous_scan(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_24_continuous_scan(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )


@pytest.mark.order(25)
@pytest.mark.asyncio(scope="session")
@pytest.mark.SPECT_ANALYZER_25
@pytest.mark.SpectrumSiteSurvey
async def test_spect_analyzer_25_disconnect_recovery(
    gui_page,
    root_ssh,
    cpe_ips,
    bsu_ip,
    device_creds,
    profile_bundle,
):
    await assert_spect_analyzer_25_disconnect_recovery(
        gui_page,
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
    )
