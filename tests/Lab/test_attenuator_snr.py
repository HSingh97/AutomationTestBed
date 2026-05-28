"""Lab tests for Vaunix LDA602 + SNMP SNR (require hardware or mock backend)."""

import pytest

from instruments.attenuator_snr import AttenuatorSnrController

pytestmark = pytest.mark.attenuator


@pytest.fixture(scope="module")
def attenuator_ctrl(profile_bundle, request):
    backend = request.config.getoption("--attenuator-backend")
    if backend == "auto":
        backend = "mock"
    ctrl = AttenuatorSnrController.from_profile(profile_bundle.active)
    ctrl._api._backend = backend  # noqa: SLF001
    ctrl.open()
    yield ctrl
    ctrl.close()


@pytest.fixture(autouse=True)
def _require_attenuator_lab(request):
    if not request.config.getoption("--allow-attenuator-lab"):
        pytest.skip("Attenuator lab tests require --allow-attenuator-lab")


@pytest.mark.asyncio
async def test_attenuator_snr_sweep_mock(attenuator_ctrl):
    results = attenuator_ctrl.sweep_attenuation(
        channel=36,
        start_db=0,
        stop_db=6,
        step_db=3,
        capture_baseline=False,
    )
    assert len(results) == 3
    assert results[0].att_chain0_db == 0.0
    assert results[-1].att_chain0_db == 6.0
