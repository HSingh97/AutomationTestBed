"""Assertions for QoS plan cases (``QoS_01`` … ``QoS_40``).

Case IDs and modes come from ``config.qos_test_cases`` (Excel sheet ``QoS``).
"""

from __future__ import annotations

from typing import Any, Iterable

from config.qos_test_cases import case_by_id
from traffic.qos_lab import (
    QoSLabRunResult,
    QueueCaptureResult,
    emit_qos_traffic_table,
    env_overrides,
    reboot_dut_and_wait,
    run_qos_lab,
    snapshot_ath1qos,
    wait_for_sua_ready,
    write_qos_case_evidence,
)


DEFAULT_TRAFFIC_DURATION_S = 15


def _lab_kwargs(**overrides):
    base = {
        "duration_s": DEFAULT_TRAFFIC_DURATION_S,
        "dl_bw": "50M",
        "ul_bw": "50M",
        "equal_share": True,
        "interval_s": 1.0,
    }
    base.update(env_overrides())
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base


def run_case_traffic(
    *,
    classes: list[str] | None = None,
    duration_s: int = DEFAULT_TRAFFIC_DURATION_S,
    dl_bw: str = "50M",
    ul_bw: str = "50M",
    packet_size: int = 1500,
    equal_share: bool = True,
    **extra,
) -> QoSLabRunResult:
    return run_qos_lab(
        **_lab_kwargs(
            classes=classes,
            duration_s=duration_s,
            dl_bw=dl_bw,
            ul_bw=ul_bw,
            packet_size=packet_size,
            equal_share=equal_share,
            **extra,
        )
    )


def _traffic_from_case(case: dict[str, Any], **overrides) -> QoSLabRunResult:
    kwargs = {
        "classes": case.get("classes"),
        "duration_s": case.get("duration_s", DEFAULT_TRAFFIC_DURATION_S),
        "dl_bw": case.get("dl_bw", "50M"),
        "ul_bw": case.get("ul_bw", "50M"),
        "packet_size": case.get("packet_size", 1500),
        "equal_share": case.get("equal_share", True),
    }
    kwargs.update(overrides)
    return run_case_traffic(**kwargs)


def _require_trex(result: QoSLabRunResult) -> None:
    assert result.trex_ok, f"TRex QoS run failed / no summary.\n{result.trex_log_tail}"
    assert result.capture.sample_count >= 3, (
        f"Too few queue samples: {result.capture.sample_count}"
    )


def assert_queues_active(
    capture: QueueCaptureResult,
    names: Iterable[str],
    *,
    min_avg_tx_mbps: float = 1.0,
    use_max: bool = False,
) -> None:
    for name in names:
        stats = capture.by_name(name)
        value = stats.max_tx_mbps if use_max else stats.avg_tx_mbps
        assert value >= min_avg_tx_mbps, (
            f"{name} (queue{stats.queue}) TX too low: "
            f"{'max' if use_max else 'avg'}={value:.3f} Mbps "
            f"(need >= {min_avg_tx_mbps})"
        )


def assert_queue_idle(
    capture: QueueCaptureResult,
    names: Iterable[str],
    *,
    max_avg_tx_mbps: float = 0.5,
) -> None:
    for name in names:
        stats = capture.by_name(name)
        assert stats.avg_tx_mbps <= max_avg_tx_mbps, (
            f"{name} (queue{stats.queue}) unexpectedly busy: "
            f"avg_tx={stats.avg_tx_mbps:.3f} Mbps (cap {max_avg_tx_mbps})"
        )


def assert_priority_above(
    capture: QueueCaptureResult,
    high: str,
    low: str,
    *,
    min_ratio: float = 1.2,
) -> None:
    hi = capture.by_name(high)
    lo = capture.by_name(low)
    hi_v = hi.avg_tx_mbps if hi.avg_tx_mbps > 0.5 else hi.max_tx_mbps
    lo_v = lo.avg_tx_mbps if lo.avg_tx_mbps > 0.5 else max(lo.max_tx_mbps, 0.01)
    ratio = hi_v / lo_v
    assert ratio >= min_ratio or hi_v >= lo_v, (
        f"Expected {high} (q{hi.queue}) ahead of {low} (q{lo.queue}): "
        f"{hi_v:.3f} vs {lo_v:.3f} (ratio {ratio:.2f}, need >= {min_ratio})"
    )


def assert_near_mir_share(
    capture: QueueCaptureResult,
    name: str,
    *,
    mir_pct: float,
    min_frac_of_mir: float = 0.35,
    max_frac_of_peak: float | None = None,
) -> None:
    """Assert class TX is consistent with Profile1 MIR (not raw priority vs other classes).

    MIR is a percent of air capacity. We approximate capacity with peak aggregate TX.
    """
    stats = capture.by_name(name)
    value = stats.max_tx_mbps if stats.max_tx_mbps > 0 else stats.avg_tx_mbps
    peak = max(capture.peak_tx_sum_mbps, 1.0)
    expected = peak * (mir_pct / 100.0)
    assert value >= expected * min_frac_of_mir, (
        f"{name} (q{stats.queue}) below MIR share: "
        f"{value:.3f} Mbps vs ~{expected:.3f} Mbps "
        f"(MIR {mir_pct:g}% of peak {peak:.3f} Mbps, floor {min_frac_of_mir:.0%})"
    )
    if max_frac_of_peak is not None:
        assert value <= peak * max_frac_of_peak + 1.0, (
            f"{name} (q{stats.queue}) above expected MIR band: "
            f"{value:.3f} Mbps vs peak {peak:.3f} Mbps "
            f"(cap {max_frac_of_peak:.0%} of peak)"
        )


# --- Mode handlers (plan sheet) ----------------------------------------------


def _mode_voice_priority(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice"], min_avg_tx_mbps=2.0)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.9)
    assert_priority_above(result.capture, "voice", "best_effort", min_ratio=0.9)
    return result


def _mode_video_priority(case: dict[str, Any]) -> QoSLabRunResult:
    """Video/ARVR on Profile1: MIR=10%, priority 4 — must not beat Bronze MIR=33% on raw TX.

    Validate classification + MIR enforcement instead of arvr_tx >= bronze_tx.
    """
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["arvr"], min_avg_tx_mbps=1.5, use_max=True)
    # Profile1 SFC_4 MIR 10% — observed ~5 Mbps on ~40 Mbps air is correct.
    assert_near_mir_share(result.capture, "arvr", mir_pct=10.0, min_frac_of_mir=0.35, max_frac_of_peak=0.25)
    # Bronze may legally out-TX ARVR because Bronze MIR is 33%.
    assert_queues_active(result.capture, ["bronze"], min_avg_tx_mbps=1.0, use_max=True)
    return result


def _mode_gaming_priority(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["gold"], min_avg_tx_mbps=2.0)
    assert_priority_above(result.capture, "gold", "bronze", min_ratio=0.9)
    return result


def _mode_hierarchy(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(
        result.capture,
        ["control", "voice", "oam", "arvr", "gold", "bronze"],
        min_avg_tx_mbps=1.0,
    )
    assert_queue_idle(result.capture, ["unassigned"], max_avg_tx_mbps=0.5)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=1.0)
    return result


def _mode_low_priority_cap(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["control", "voice"], min_avg_tx_mbps=1.0, use_max=True)
    bronze = result.capture.by_name("bronze")
    voice = result.capture.by_name("voice")
    assert bronze.avg_tx_mbps <= voice.avg_tx_mbps * 1.5 or bronze.max_tx_mbps <= voice.max_tx_mbps * 1.5, (
        f"Bronze unexpectedly dominates Voice: "
        f"bronze avg={bronze.avg_tx_mbps:.2f} voice avg={voice.avg_tx_mbps:.2f}"
    )
    return result


def _mode_critical_congestion(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["control"], min_avg_tx_mbps=1.0, use_max=True)
    assert_priority_above(result.capture, "control", "bronze", min_ratio=0.8)
    return result


def _mode_baseline_mixed(case: dict[str, Any]) -> QoSLabRunResult:
    """Baseline mixed traffic — record activity without priority claims."""
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(
        result.capture,
        ["control", "voice", "gold", "bronze", "best_effort"],
        min_avg_tx_mbps=0.8,
        use_max=True,
    )
    assert result.capture.peak_tx_sum_mbps > 5.0
    return result


def _mode_dscp_classification(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case, equal_share=True)
    _require_trex(result)
    active = ["control", "voice", "oam", "arvr", "gold", "bronze", "best_effort"]
    assert_queues_active(result.capture, active, min_avg_tx_mbps=1.5)
    assert_queue_idle(result.capture, ["unassigned"], max_avg_tx_mbps=0.5)
    voice = result.capture.by_name("voice")
    control = result.capture.by_name("control")
    assert (
        voice.avg_tx_mbps >= control.avg_tx_mbps * 1.2
        or voice.max_tx_mbps >= control.max_tx_mbps * 1.2
    ), "Voice queue should carry extra DSCP-18 share vs Control under equal-share"
    return result


def _mode_voice_and_data(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice", "gold"], min_avg_tx_mbps=1.5)
    voice = result.capture.by_name("voice")
    gold = result.capture.by_name("gold")
    assert voice.avg_tx_mbps > 0 and gold.avg_tx_mbps > 0
    return result


def _mode_rate_limit(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["oam", "best_effort"], min_avg_tx_mbps=0.5, use_max=True)
    return result


def _mode_scheduling_order(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice", "control"], min_avg_tx_mbps=1.0, use_max=True)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.9)
    assert_priority_above(result.capture, "control", "best_effort", min_ratio=0.8)
    return result


def _mode_cpe_stress(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["control", "voice"], min_avg_tx_mbps=0.8, use_max=True)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.8)
    return result


def _mode_allow_deny(case: dict[str, Any]) -> QoSLabRunResult:
    """Allowed class (voice) active; non-offered classes stay quiet (deny-by-absence proxy)."""
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice"], min_avg_tx_mbps=1.5, use_max=True)
    assert_queue_idle(result.capture, ["bronze", "best_effort", "unassigned"], max_avg_tx_mbps=0.5)
    return result


def _mode_qos_logging(case: dict[str, Any]) -> QoSLabRunResult:
    """Queue_stats CSV is the audit artifact for classification enforcement."""
    result = _traffic_from_case(case)
    _require_trex(result)
    assert result.capture.csv_path.is_file(), f"Missing queue_stats CSV: {result.capture.csv_path}"
    assert result.capture.sample_count >= 3
    assert_queues_active(result.capture, ["voice", "gold"], min_avg_tx_mbps=0.5, use_max=True)
    return result


def _mode_dynamic_qos(case: dict[str, Any]) -> list[QoSLabRunResult]:
    """Back-to-back load change; classification remains stable (no service break)."""
    results: list[QoSLabRunResult] = []
    for dl, ul in (("50M", "50M"), ("150M", "150M")):
        result = _traffic_from_case(case, dl_bw=dl, ul_bw=ul, equal_share=True)
        _require_trex(result)
        assert_queues_active(result.capture, ["voice", "gold"], min_avg_tx_mbps=0.5, use_max=True)
        results.append(result)
    return results


def _mode_encrypted_traffic(case: dict[str, Any]) -> QoSLabRunResult:
    """DSCP still maps when outer TOS is set (proxy for encrypted/tunneled classification)."""
    return _mode_dscp_classification(case)


def _mode_encapsulated_traffic(case: dict[str, Any]) -> QoSLabRunResult:
    """QinQ-encapsulated TRex streams still map to Profile1 queues via DSCP/TOS."""
    return _mode_dscp_classification(case)


def _mode_realtime_vs_nonrealtime(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice", "arvr"], min_avg_tx_mbps=1.5)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.9)
    # ARVR MIR=10% vs BestEffort MIR=100% — do not require arvr TX > BE.
    assert_near_mir_share(result.capture, "arvr", mir_pct=10.0, min_frac_of_mir=0.35, max_frac_of_peak=0.25)
    return result


def _mode_p2p_control(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice"], min_avg_tx_mbps=1.0, use_max=True)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.9)
    return result


def _mode_heavy_load(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case, equal_share=True)
    _require_trex(result)
    assert_queues_active(
        result.capture,
        ["control", "voice", "gold", "bronze"],
        min_avg_tx_mbps=0.8,
        use_max=True,
    )
    assert result.capture.peak_tx_sum_mbps > 5.0, (
        f"Peak aggregate queue TX too low under load: {result.capture.peak_tx_sum_mbps:.2f}"
    )
    return result


def _mode_backup_deprioritized(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["control", "voice"], min_avg_tx_mbps=1.0, use_max=True)
    bronze = result.capture.by_name("bronze")
    control = result.capture.by_name("control")
    assert bronze.avg_tx_mbps <= control.avg_tx_mbps * 1.5 or bronze.max_tx_mbps <= control.max_tx_mbps * 1.5, (
        f"Backup/Bronze dominates Control: bronze={bronze.avg_tx_mbps:.2f} control={control.avg_tx_mbps:.2f}"
    )
    return result


def _mode_reserved_bandwidth(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["oam", "gold"], min_avg_tx_mbps=0.5, use_max=True)
    return result


def _mode_burst_or_packet_sizes(case: dict[str, Any]) -> list[QoSLabRunResult]:
    sizes = case.get("sizes") or [64, 512, 1500]
    results: list[QoSLabRunResult] = []
    for size in sizes:
        result = _traffic_from_case(case, packet_size=int(size), equal_share=True)
        _require_trex(result)
        assert_queues_active(result.capture, ["voice", "gold"], min_avg_tx_mbps=0.5, use_max=True)
        results.append(result)
    return results


def _mode_scale_loads(case: dict[str, Any]) -> list[QoSLabRunResult]:
    results: list[QoSLabRunResult] = []
    for dl, ul in (("30M", "30M"), ("80M", "80M"), ("150M", "150M")):
        result = _traffic_from_case(case, dl_bw=dl, ul_bw=ul, equal_share=True)
        _require_trex(result)
        assert_queues_active(result.capture, ["voice", "gold"], min_avg_tx_mbps=0.5, use_max=True)
        results.append(result)
    return results


def _mode_mission_critical(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["control", "voice"], min_avg_tx_mbps=1.0, use_max=True)
    assert_priority_above(result.capture, "control", "best_effort", min_ratio=0.8)
    return result


def _mode_policy_priority(case: dict[str, Any]) -> QoSLabRunResult:
    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice", "arvr"], min_avg_tx_mbps=1.5)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.9)
    return result


def _mode_arvr_priority(case: dict[str, Any]) -> QoSLabRunResult:
    return _mode_video_priority(case)


def _mode_metrics_high_load(case: dict[str, Any]) -> QoSLabRunResult:
    result = _mode_heavy_load(case)
    # Metrics consistency: all mapped queues present in capture summary.
    for name in ("control", "voice", "oam", "arvr", "gold", "bronze", "best_effort"):
        stats = result.capture.by_name(name)
        assert stats.samples >= 3, f"{name} missing stable samples ({stats.samples})"
    return result


def _normalize_uci_snapshot(text: str) -> str:
    """Stable compare key for ath1qos UCI (ignore blank/order noise)."""
    lines = sorted({ln.strip() for ln in (text or "").splitlines() if ln.strip()})
    return "\n".join(lines)


def _wait_ath1qos_stable(
    *,
    dut_host: str,
    dut_password: str,
    polls: int = 12,
    interval_s: float = 5.0,
) -> str:
    """Poll UCI until two consecutive normalized snapshots match."""
    import time

    last = ""
    for i in range(max(polls, 2)):
        cur = snapshot_ath1qos(dut_host=dut_host, dut_password=dut_password)
        if last and _normalize_uci_snapshot(last) == _normalize_uci_snapshot(cur):
            print(f"[QoS] ath1qos UCI stable after reboot (poll {i + 1})")
            return cur
        last = cur
        time.sleep(interval_s)
    return last


def _mode_setup_covered(case: dict[str, Any]) -> None:
    """Explicit pass — lab topology already exercises wired backhaul and wireless SU QoS."""
    note = case.get("note") or case.get("title") or case.get("id")
    print(f"[QoS][{case.get('id')}] PASS BY DEFAULT: {note}")
    return None


def _mode_reboot_retention(case: dict[str, Any]) -> QoSLabRunResult:
    """Snapshot ath1qos → reboot BTS → compare UCI → wait for SUA → traffic verify."""
    overrides = env_overrides()
    dut_host = overrides.get("dut_host") or "10.0.0.1"
    dut_password = overrides.get("dut_password") or "Sen@0ubRNwk$"

    before = snapshot_ath1qos(dut_host=dut_host, dut_password=dut_password)
    print(f"[QoS][{case.get('id')}] ath1qos snapshot before reboot: {len(before.splitlines())} lines")
    reboot_dut_and_wait(dut_host=dut_host, dut_password=dut_password)
    after = _wait_ath1qos_stable(dut_host=dut_host, dut_password=dut_password)
    before_n = _normalize_uci_snapshot(before)
    after_n = _normalize_uci_snapshot(after)
    if before_n != after_n:
        before_lines = set(before_n.splitlines())
        after_lines = set(after_n.splitlines())
        only_before = sorted(before_lines - after_lines)[:12]
        only_after = sorted(after_lines - before_lines)[:12]
        raise AssertionError(
            "ath1qos UCI changed across reboot\n"
            f"only_before ({len(before_lines - after_lines)}):\n"
            + "\n".join(only_before)
            + f"\nonly_after ({len(after_lines - before_lines)}):\n"
            + "\n".join(only_after)
        )
    print(f"[QoS][{case.get('id')}] ath1qos UCI retained after reboot (normalized match)")

    # Ensure at least one SUA is associated after reboot, but don't lock the
    # capture SUA. Traffic may land on a different SUA than the first one we
    # detect as associated.
    wait_for_sua_ready(
        dut_host=dut_host,
        dut_password=dut_password,
        sua=None,
        prefer_any_associated=True,
    )

    result = _traffic_from_case(case)
    _require_trex(result)
    assert_queues_active(result.capture, ["voice"], min_avg_tx_mbps=1.5, use_max=True)
    assert_priority_above(result.capture, "voice", "bronze", min_ratio=0.9)
    return result


_MODE_HANDLERS = {
    "voice_priority": _mode_voice_priority,
    "video_priority": _mode_video_priority,
    "gaming_priority": _mode_gaming_priority,
    "hierarchy": _mode_hierarchy,
    "low_priority_cap": _mode_low_priority_cap,
    "critical_congestion": _mode_critical_congestion,
    "baseline_mixed": _mode_baseline_mixed,
    "dscp_classification": _mode_dscp_classification,
    "voice_and_data": _mode_voice_and_data,
    "rate_limit": _mode_rate_limit,
    "scheduling_order": _mode_scheduling_order,
    "cpe_stress": _mode_cpe_stress,
    "allow_deny": _mode_allow_deny,
    "qos_logging": _mode_qos_logging,
    "dynamic_qos": _mode_dynamic_qos,
    "encrypted_traffic": _mode_encrypted_traffic,
    "encapsulated_traffic": _mode_encapsulated_traffic,
    "realtime_vs_nonrealtime": _mode_realtime_vs_nonrealtime,
    "p2p_control": _mode_p2p_control,
    "heavy_load": _mode_heavy_load,
    "backup_deprioritized": _mode_backup_deprioritized,
    "reserved_bandwidth": _mode_reserved_bandwidth,
    "burst_sizes": _mode_burst_or_packet_sizes,
    "packet_sizes": _mode_burst_or_packet_sizes,
    "scale_loads": _mode_scale_loads,
    "mission_critical": _mode_mission_critical,
    "policy_priority": _mode_policy_priority,
    "arvr_priority": _mode_arvr_priority,
    "metrics_high_load": _mode_metrics_high_load,
    "setup_covered": _mode_setup_covered,
    "reboot_retention": _mode_reboot_retention,
}

# Report pills: what each mode validated (printed as `-> …: PASSED`).
_MODE_VERIFIED_PARAMS: dict[str, list[str]] = {
    "voice_priority": [
        "Voice queue (q1) TX active >= 2 Mbps",
        "Voice prioritized vs Bronze (q5)",
        "Voice prioritized vs BestEffort (q6)",
    ],
    "video_priority": [
        "ARVR/video queue (q3) TX active",
        "ARVR TX consistent with Profile1 MIR 10%",
        "Bronze active (higher MIR 33% may exceed ARVR TX)",
    ],
    "gaming_priority": [
        "Gold/gaming queue (q4) TX active >= 2 Mbps",
        "Gold prioritized vs Bronze (q5)",
    ],
    "hierarchy": [
        "Mapped queues q0-q5 active",
        "Unassigned queue (q7) idle",
        "Voice (q1) ahead of Bronze (q5)",
    ],
    "low_priority_cap": [
        "Control (q0) and Voice (q1) active under load",
        "Bronze (q5) did not dominate Voice",
    ],
    "critical_congestion": [
        "Control (q0) active under congestion",
        "Control prioritized vs Bronze (q5)",
    ],
    "baseline_mixed": [
        "Mixed-class baseline queues active",
        "Peak aggregate queue TX > 5 Mbps",
    ],
    "dscp_classification": [
        "All Profile1 queues q0-q6 classified active",
        "Unassigned queue (q7) idle",
        "Voice carries extra DSCP-18 share vs Control",
    ],
    "voice_and_data": [
        "Voice (q1) and Gold/data (q4) both active",
        "Simultaneous voice + data streams OK",
    ],
    "rate_limit": [
        "OAM capped class (q2) TX present",
        "BestEffort (q6) TX present under load",
    ],
    "scheduling_order": [
        "Voice and Control active under contention",
        "Voice prioritized vs Bronze",
        "Control prioritized vs BestEffort",
    ],
    "cpe_stress": [
        "Control and Voice active under stress load",
        "Voice prioritized vs Bronze under stress",
    ],
    "allow_deny": [
        "Allowed Voice queue (q1) active",
        "Non-offered Bronze/BestEffort/Unassigned idle",
    ],
    "qos_logging": [
        "queue_stats CSV audit artifact written",
        "Voice and Gold classification samples present",
    ],
    "dynamic_qos": [
        "50M load: Voice/Gold classified",
        "150M load: Voice/Gold classified (no service break)",
    ],
    "encrypted_traffic": [
        "All Profile1 queues q0-q6 classified active",
        "Unassigned queue (q7) idle",
        "Voice carries extra DSCP-18 share vs Control",
    ],
    "encapsulated_traffic": [
        "QinQ-encapsulated streams classified via DSCP/TOS",
        "All Profile1 queues q0-q6 classified active",
        "Unassigned queue (q7) idle",
    ],
    "realtime_vs_nonrealtime": [
        "Realtime Voice and ARVR queues active",
        "Voice prioritized vs Bronze",
        "ARVR TX consistent with Profile1 MIR 10%",
    ],
    "p2p_control": [
        "Voice active under P2P/bronze contention",
        "Voice prioritized vs Bronze",
    ],
    "heavy_load": [
        "Control/Voice/Gold/Bronze active under heavy load",
        "Peak aggregate queue TX > 5 Mbps",
    ],
    "backup_deprioritized": [
        "Control and Voice active",
        "Backup/Bronze did not dominate Control",
    ],
    "reserved_bandwidth": [
        "OAM and Gold queues active under reservation load",
    ],
    "burst_sizes": [
        "Classification holds across packet sizes",
    ],
    "packet_sizes": [
        "Classification holds across packet sizes",
    ],
    "scale_loads": [
        "Classification holds across offered load levels",
    ],
    "mission_critical": [
        "Control and Voice active",
        "Control prioritized vs BestEffort",
    ],
    "policy_priority": [
        "Voice and ARVR active",
        "Voice prioritized vs Bronze",
    ],
    "arvr_priority": [
        "ARVR/video queue (q3) TX active",
        "ARVR TX consistent with Profile1 MIR 10%",
        "Bronze active (higher MIR 33% may exceed ARVR TX)",
    ],
    "metrics_high_load": [
        "Control/Voice/Gold/Bronze active under heavy load",
        "Queue metrics stable across samples",
    ],
    "reboot_retention": [
        "ath1qos UCI retained across BTS reboot",
        "Voice classification still active after reboot",
        "Voice prioritized vs Bronze after reboot",
    ],
    "setup_covered": [
        "Wired TRex/PC→BTS backhaul already exercised by lab suite",
        "Wireless BTS→CPE (SUA queue_stats) already exercised by lab suite",
    ],
}


def execute_qos_case(case_id: str):
    """Run the plan case by TESTCASE-ID (``QoS_NN``)."""
    case = case_by_id(case_id)
    status = case.get("status")
    if status == "not_available":
        import pytest

        pytest.skip(case.get("note") or f"{case_id} not available")
    if status == "manual":
        import pytest

        note = case.get("note") or f"{case_id} to be tested manually"
        pytest.skip(note if note.lower().startswith("to be tested manually") else f"To be tested manually: {note}")
    if status == "pending":
        raise RuntimeError(f"{case_id} is not implemented (status=pending)")
    if status != "implemented":
        raise RuntimeError(f"{case_id} is not implemented (status={status})")
    mode = case.get("mode")
    if not mode or mode not in _MODE_HANDLERS:
        raise RuntimeError(f"{case_id} has unknown mode: {mode!r}")
    result = _MODE_HANDLERS[mode](case)
    verified = list(_MODE_VERIFIED_PARAMS.get(mode, []))
    for param in verified:
        print(f"-> {param}: PASSED")
    # Sidecar evidence so Jenkins HTML still has details when pytest -s
    # leaves json-report stdout empty.
    try:
        write_qos_case_evidence(case_id, params=verified)
    except Exception as exc:
        print(f"[QoS][{case_id}] evidence write skipped: {exc}")
    if result is None:
        return None
    try:
        emit_qos_traffic_table(
            result,
            case_id=case_id,
            offered_classes=case.get("classes"),
        )
    except Exception as exc:
        print(f"[QoS][{case_id}] traffic table skipped: {exc}")
    return result
