"""Link Test Tool settings: profile defaults with optional pytest CLI overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LINK_TEST_DEFAULTS = {
    "vlan_id": 0,
    "vlan_min": 0,
    "vlan_max": 4094,
    "duration_s": 30,
    "duration_min": 1,
    "duration_max": 300,
    "bw_min": 20,
    "bw_max": 400,
    "result_tolerance_throughput_mbps": 25,
    "result_tolerance_latency_ms": 5,
    "reference_results_path": None,
}


@dataclass(frozen=True)
class LinkTestConfig:
    vlan_id: int
    vlan_min: int
    vlan_max: int
    duration_s: int
    duration_min: int
    duration_max: int
    bw_min: int
    bw_max: int
    result_tolerance_throughput_mbps: float
    result_tolerance_latency_ms: float
    reference_results_path: str | None

    def __post_init__(self):
        if not 0 <= self.vlan_id <= 4094:
            raise ValueError(f"vlan_id must be 0-4094, got {self.vlan_id}")
        if not 0 <= self.vlan_min <= 4094 or not 0 <= self.vlan_max <= 4094:
            raise ValueError(f"vlan range must be 0-4094, got {self.vlan_min}-{self.vlan_max}")
        if self.vlan_min > self.vlan_max:
            raise ValueError(f"vlan_min ({self.vlan_min}) cannot exceed vlan_max ({self.vlan_max})")
        if self.duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {self.duration_s}")
        if self.duration_min <= 0 or self.duration_max <= 0:
            raise ValueError(f"duration range must be positive, got {self.duration_min}-{self.duration_max}")
        if self.duration_min > self.duration_max:
            raise ValueError(f"duration_min cannot exceed duration_max")
        if self.bw_min <= 0 or self.bw_max <= 0:
            raise ValueError(f"bw_min/bw_max must be positive, got {self.bw_min}/{self.bw_max}")
        if self.bw_min > self.bw_max:
            raise ValueError(f"bw_min ({self.bw_min}) cannot exceed bw_max ({self.bw_max})")

    @property
    def default_bandwidth(self) -> int:
        return min(self.bw_max, max(self.bw_min, (self.bw_min + self.bw_max) // 2))


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def resolve_link_test_config(profile_data: dict[str, Any], request) -> LinkTestConfig:
    """
    Merge link_test settings with priority: CLI > profile > LINK_TEST_DEFAULTS.
    """
    profile_section = profile_data.get("link_test") or {}
    merged = {**LINK_TEST_DEFAULTS, **profile_section}

    cli_map = {
        "vlan_id": request.config.getoption("--link-test-vlan"),
        "duration_s": request.config.getoption("--link-test-duration"),
        "bw_min": request.config.getoption("--link-test-bw-min"),
        "bw_max": request.config.getoption("--link-test-bw-max"),
    }
    for key, cli_val in cli_map.items():
        if cli_val is not None:
            merged[key] = _as_int(cli_val, key)

    ref_cli = request.config.getoption("--link-test-reference-json")
    if ref_cli is not None:
        merged["reference_results_path"] = ref_cli

    return LinkTestConfig(
        vlan_id=_as_int(merged["vlan_id"], "vlan_id"),
        vlan_min=_as_int(merged["vlan_min"], "vlan_min"),
        vlan_max=_as_int(merged["vlan_max"], "vlan_max"),
        duration_s=_as_int(merged["duration_s"], "duration_s"),
        duration_min=_as_int(merged["duration_min"], "duration_min"),
        duration_max=_as_int(merged["duration_max"], "duration_max"),
        bw_min=_as_int(merged["bw_min"], "bw_min"),
        bw_max=_as_int(merged["bw_max"], "bw_max"),
        result_tolerance_throughput_mbps=float(merged["result_tolerance_throughput_mbps"]),
        result_tolerance_latency_ms=float(merged["result_tolerance_latency_ms"]),
        reference_results_path=merged.get("reference_results_path") or None,
    )
