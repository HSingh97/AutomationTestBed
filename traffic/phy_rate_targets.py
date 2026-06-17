"""Derive TRex load targets from spec-sheet operating rates × efficiency."""

from __future__ import annotations

from typing import Any

from traffic.operating_rate_table import operating_rate_mbps, normalize_bandwidth, normalize_mcs


def phy_max_rate_mbps(
    bandwidth: str,
    mcs: str,
    *,
    overrides: dict[str, dict[str, float]] | None = None,
    spatial_streams: int = 2,
) -> float:
    """PHY / operating peak (Mbps) from the product rate sheet (dual stream by default)."""
    bw = normalize_bandwidth(bandwidth)
    mcs_key = normalize_mcs(mcs)
    if overrides:
        explicit = (overrides.get(bw) or {}).get(mcs_key)
        if explicit is not None:
            return float(explicit)
    return operating_rate_mbps(bw, mcs_key, spatial_streams=spatial_streams)


def compute_traffic_targets(
    *,
    bandwidth: str,
    mcs: str,
    ratio: str,
    efficiency_factor: float = 0.75,
    target_ceiling_mbps: float | None = None,
    phy_overrides: dict[str, dict[str, float]] | None = None,
    legacy_mcs_caps: dict[str, float] | None = None,
    spatial_streams: int = 2,
    su_count: int | None = None,
) -> dict[str, Any]:
    dl_ratio, ul_ratio = [float(part) for part in ratio.split(":")]
    total_ratio = dl_ratio + ul_ratio
    if total_ratio <= 0:
        raise ValueError(f"Invalid ratio '{ratio}'")

    phy_max = phy_max_rate_mbps(
        bandwidth, mcs, overrides=phy_overrides, spatial_streams=spatial_streams
    )
    effective = phy_max * efficiency_factor

    if target_ceiling_mbps is not None and target_ceiling_mbps > 0:
        effective = min(effective, target_ceiling_mbps)

    if legacy_mcs_caps:
        legacy_cap = legacy_mcs_caps.get(normalize_mcs(mcs))
        if legacy_cap is not None:
            effective = min(effective, legacy_cap * efficiency_factor)

    downlink_mbps = effective * (dl_ratio / total_ratio)
    uplink_mbps = effective * (ul_ratio / total_ratio)

    if downlink_mbps > 0 and uplink_mbps > 0:
        direction = "bidi"
    elif downlink_mbps > 0:
        direction = "downlink"
    else:
        direction = "uplink"

    result: dict[str, Any] = {
        "bandwidth": normalize_bandwidth(bandwidth),
        "mcs": normalize_mcs(mcs),
        "ratio": ratio,
        "phy_max_mbps": round(phy_max, 2),
        "efficiency_factor": efficiency_factor,
        "effective_target_mbps": round(effective, 2),
        "target_ceiling_mbps": target_ceiling_mbps,
        "downlink_mbps": round(downlink_mbps, 2),
        "uplink_mbps": round(uplink_mbps, 2),
        "trex_dl_bw": f"{max(1, int(round(downlink_mbps)))}M",
        "trex_ul_bw": f"{max(1, int(round(uplink_mbps)))}M",
        "trex_direction": direction,
    }
    if su_count and su_count > 0:
        result["su_count"] = su_count
        result["downlink_per_cpe_mbps"] = round(downlink_mbps / su_count, 2)
        result["uplink_per_cpe_mbps"] = round(uplink_mbps / su_count, 2)
    return result
