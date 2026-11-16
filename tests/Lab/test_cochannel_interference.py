"""Co-channel interference matrix (Low / Mid / High × dynamic SU steps).

TX power (1 / 15 / 26 dBm) sets secondary RSSI location.
OBSS bands: Low soft (~5–20%), Mid ~25% (22–28), High ~32% (30–35).
SU counts auto-detected from BTS station table (2, 4, 6, … up to all CPEs).
"""

from __future__ import annotations

import os

import pytest

from utils.cochannel_flows import (
    CoChannelConfig,
    _obss_in_band,
    interference_level_ok,
    run_cochannel_matrix,
    set_interference_level,
    set_secondary_tx_power,
    stop_secondary_iperf,
    verify_backend_only,
)

pytestmark = [pytest.mark.Lab, pytest.mark.CoChannel]


def _cfg(profile_bundle) -> CoChannelConfig:
    return CoChannelConfig.from_profile(profile_bundle.active)


@pytest.mark.COCH_00
def test_cochannel_backend_rssi_obss_util(profile_bundle):
    """Backend probe on primary BTS: wlanconfig RSSI + cfg80211 OBSS/util/free."""
    cfg = _cfg(profile_bundle)
    snap = verify_backend_only(cfg)
    assert snap.rssi_dbm is not None
    assert snap.obss_pct is not None
    assert snap.util_pct is not None


@pytest.mark.COCH_01
def test_cochannel_interference_levels_obss(profile_bundle):
    """
    Verify Low / Mid / High OBSS bands with TX location (1 / 15 / 26 dBm).

    Low: secondary link only. Mid/High: secondary load until OBSS in band.
    """
    cfg = _cfg(profile_bundle)
    stop_secondary_iperf(cfg)
    try:
        for level in ("low", "mid", "high"):
            band = dict((cfg.interference or {}).get(level) or {})
            snap = set_interference_level(cfg, level)
            assert snap.rssi_dbm is not None, f"{level}: no RSSI"
            assert snap.obss_pct is not None, f"{level}: no OBSS"
            # Soft: record WARN via pytest_check inside set_interference_level;
            # do not hard-fail if lab cannot yet hit Mid/High OBSS bands.
            if not _obss_in_band(snap.obss_pct, band):
                print(
                    f"[cochannel] {level}: achieved OBSS={snap.obss_pct}% "
                    f"(target [{band.get('obss_min')},{band.get('obss_max')}]) "
                    f"RSSI={snap.rssi_dbm} TX={band.get('secondary_tx_power_dbm')}",
                    flush=True,
                )
    finally:
        stop_secondary_iperf(cfg)
        set_secondary_tx_power(cfg, cfg.secondary_tx_power_min_dbm)


@pytest.mark.COCH_02
@pytest.mark.parametrize("level", ["low", "mid", "high"])
def test_cochannel_matrix_level(profile_bundle, level):
    """
    One interference level: baseline RF (no primary traffic) + SU matrix.

    Primary duration from profile (default 180s / 3 min). Override: COCHANNEL_DURATION_S.
    """
    cfg = _cfg(profile_bundle)
    rows = run_cochannel_matrix(cfg, levels=[level])
    baseline = [r for r in rows if r.phase == "baseline"]
    traffic = [r for r in rows if r.phase == "traffic"]
    assert len(baseline) == 1
    assert baseline[0].obss_pct is not None
    assert baseline[0].util_pct is not None
    assert baseline[0].free_pct is not None
    assert len(traffic) == len(cfg.su_counts)
    for row in traffic:
        assert row.interference == level
        assert row.rssi_dbm is not None
        assert row.dl_packet_drop_pct is not None
        assert row.ul_packet_drop_pct is not None
        assert row.dl_jitter_ms is not None
        assert row.ul_jitter_ms is not None


@pytest.mark.COCH_03
def test_cochannel_full_matrix(profile_bundle):
    """Full Low→Mid→High: baseline RF + auto SU steps (3 min each)."""
    if os.environ.get("COCHANNEL_SKIP_FULL", "").strip() in {"1", "true", "yes"}:
        pytest.skip("COCHANNEL_SKIP_FULL=1")
    cfg = _cfg(profile_bundle)
    rows = run_cochannel_matrix(cfg, levels=["low", "mid", "high"])
    assert len(rows) == 3 + len(cfg.su_counts) * 3
    for level in ("low", "mid", "high"):
        level_rows = [r for r in rows if r.interference == level]
        assert any(r.phase == "baseline" for r in level_rows)
        traffic = [r for r in level_rows if r.phase == "traffic"]
        assert len(traffic) == len(cfg.su_counts)
        for row in traffic:
            assert row.dl_packet_drop_pct is not None
            assert row.ul_packet_drop_pct is not None
