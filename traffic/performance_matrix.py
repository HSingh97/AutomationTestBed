#!/usr/bin/env python3
"""
Performance matrix runner: sweep bandwidth x MCS x DL/UL ratio and capture throughput.

For each combination the script:
  1. Applies BTS bandwidth once per htmode, then MCS per iteration
  2. Runs TRex throughput (stats_check)
  3. Saves per-iteration JSON plus consolidated CSV/HTML reports

Example:
  PYTHONPATH=. python3 traffic/performance_matrix.py \\
    --profile default \\
    --bandwidths HT20,HT40,HT80 \\
    --mcs MCS0,MCS1,MCS7 \\
    --ratios 80:20,50:50 \\
    --time 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config.defaults import PERFORMANCE_DEFAULTS, TRAFFIC_DEFAULTS

# Throughput matrix always runs bidirectional 75:25 (DL:UL).
FIXED_DL_UL_RATIO = "75:25"
from traffic.dut_radio_config import (
    configure_bandwidth_profile,
    configure_mcs_profile,
    read_running_bandwidth,
)
from traffic.link_stats import fetch_link_clients, resolve_cpe_hosts_for_run, validate_operating_rates
from traffic.operating_rate_table import normalize_bandwidth, operating_rate_mbps
from traffic.operating_rate_table import lookup_spec
from traffic.phy_rate_targets import compute_traffic_targets
from traffic.trex_runner import (
    build_trex_client_command,
    run_trex_stats_check,
    stop_remote_trex_servers,
)
from utils.vlan_uci import qinq_tags_from_profile
from utils.bench_config import profile_for_stand, recovery_profile_for_stand
from utils.console_output import enable_live_console_output
from utils.net_utils import normalize_ip
from utils.grafana_sample_report import build_matrix_grafana_payload, write_grafana_sample_html
from utils.performance_report import write_html_report, write_summary_csv
from utils.profile_manager import load_profile_bundle
from utils.recovery_manager import RecoveryManager
from utils.regression_device_info import collect_testbed_summary


def _apply_profile_run_defaults(args, profile_bundle) -> None:
    """Apply profile performance/traffic.trex defaults when CLI left values at factory defaults."""
    active = profile_bundle.active
    perf_section = dict(active.get("performance") or {})
    traffic_trex = dict((active.get("traffic") or {}).get("trex") or {})
    trex_defaults = TRAFFIC_DEFAULTS["trex"]

    if args.su_count == PERFORMANCE_DEFAULTS["su_count"] and perf_section.get("su_count"):
        args.su_count = int(perf_section["su_count"])
    if args.trex_ports == trex_defaults["ports"] and traffic_trex.get("ports"):
        args.trex_ports = str(traffic_trex["ports"])
    if traffic_trex.get("host"):
        if args.profile != PERFORMANCE_DEFAULTS["profile"] or getattr(args, "stand", ""):
            args.trex_server = str(traffic_trex["host"])
        elif args.trex_server == trex_defaults["host"]:
            args.trex_server = str(traffic_trex["host"])
    if traffic_trex.get("user") and not os.getenv("TREX_USER"):
        args.trex_user = str(traffic_trex["user"])
    if traffic_trex.get("password") and args.trex_password == os.getenv("TREX_PASSWORD", trex_defaults["password"]):
        if args.profile != PERFORMANCE_DEFAULTS["profile"] or getattr(args, "stand", ""):
            args.trex_password = str(traffic_trex["password"])
    su_servers = traffic_trex.get("su_servers") or []
    if isinstance(su_servers, str):
        su_servers = [su_servers]
    for idx, attr in enumerate(
        ("trex_server_su", "trex_server_su2", "trex_server_su3", "trex_server_su4")
    ):
        if idx < len(su_servers) and su_servers[idx] and not getattr(args, attr, ""):
            setattr(args, attr, str(su_servers[idx]))
    if args.time == PERFORMANCE_DEFAULTS["duration_s"] and perf_section.get("duration_s"):
        args.time = int(perf_section["duration_s"])
    if args.packet_size == PERFORMANCE_DEFAULTS["packet_size"] and perf_section.get("packet_size"):
        args.packet_size = int(perf_section["packet_size"])
    profile_selected = bool(getattr(args, "stand", "")) or args.profile != PERFORMANCE_DEFAULTS["profile"]
    if profile_selected:
        if "use_dynamic_target" in perf_section:
            args.use_dynamic_target = bool(perf_section["use_dynamic_target"])
        if perf_section.get("target_mbps") is not None:
            args.target = float(perf_section["target_mbps"])
        if perf_section.get("efficiency_factor") is not None:
            args.efficiency = float(perf_section["efficiency_factor"])
    if traffic_trex.get("server_startup_s"):
        args.trex_server_startup_s = int(traffic_trex["server_startup_s"])
    if traffic_trex.get("server_cores"):
        args.trex_server_cores = int(traffic_trex["server_cores"])
    if perf_section.get("skip_dut_config"):
        args.skip_dut_config = True
    if perf_section.get("skip_config_if_unchanged") is not None:
        args.skip_config_if_unchanged = bool(perf_section["skip_config_if_unchanged"])
    if perf_section.get("cpe_via_bts"):
        args.cpe_via_bts = True
    if perf_section.get("link_wait_s") is not None:
        args.link_wait_s = float(perf_section["link_wait_s"])
    if perf_section.get("radio_settle_s") is not None:
        args.radio_settle_s = float(perf_section["radio_settle_s"])
    if perf_section.get("operating_mcs_wait_s") is not None:
        args.operating_mcs_wait_s = float(perf_section["operating_mcs_wait_s"])
    if perf_section.get("mcs_kickmac_wait_s") is not None:
        args.mcs_kickmac_wait_s = float(perf_section["mcs_kickmac_wait_s"])
    if perf_section.get("bandwidth_apply_wait_s") is not None:
        args.bandwidth_apply_wait_s = float(perf_section["bandwidth_apply_wait_s"])
    if perf_section.get("su_link_wait_s") is not None:
        args.su_link_wait_s = float(perf_section["su_link_wait_s"])
    if perf_section.get("bandwidth_running_wait_s") is not None:
        args.bandwidth_running_wait_s = float(perf_section["bandwidth_running_wait_s"])
    if perf_section.get("require_all_su_for_bandwidth") is not None:
        args.require_all_su_for_bandwidth = bool(perf_section["require_all_su_for_bandwidth"])
    if perf_section.get("link_stats_source"):
        args.link_stats_source = str(perf_section["link_stats_source"]).strip()
    if perf_section.get("link_wifi_idx") is not None:
        args.link_wifi_idx = int(perf_section["link_wifi_idx"])
    elif perf_section.get("snmp_radio_index") is not None:
        args.link_wifi_idx = int(perf_section["snmp_radio_index"])
    else:
        args.link_wifi_idx = int(args.radio_index)


def _resolve_stand_profile_args(args) -> None:
    stand = str(getattr(args, "stand", "") or "").strip()
    if not stand:
        return
    if args.profile == PERFORMANCE_DEFAULTS["profile"]:
        args.profile = profile_for_stand(stand)
    if args.recovery_profile == PERFORMANCE_DEFAULTS["recovery_profile"]:
        args.recovery_profile = recovery_profile_for_stand(stand)


def _parse_csv_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_ratio(ratio: str) -> tuple[float, float]:
    try:
        dl_part, ul_part = ratio.split(":")
        return float(dl_part), float(ul_part)
    except Exception as exc:
        raise ValueError(f"Invalid ratio '{ratio}', expected DL:UL (e.g. 80:20)") from exc


def _traffic_profiles(ratios: list[str], *, include_directional: bool = True) -> list[dict[str, str]]:
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()
    if include_directional:
        profiles.extend(
            [
                {"name": "Downlink", "ratio": "100:0"},
                {"name": "Uplink", "ratio": "0:100"},
            ]
        )
        seen.update(profile["ratio"] for profile in profiles)
    for ratio in ratios:
        clean = ratio.strip()
        if clean and clean not in seen:
            profiles.append({"name": "Bidirectional", "ratio": clean})
            seen.add(clean)
    return profiles


def _format_traffic_target_log(targets: dict[str, object]) -> str:
    dl = targets.get("trex_dl_bw")
    ul = targets.get("trex_ul_bw")
    line = f"TRex total DL={dl} UL={ul}"
    su_count = targets.get("su_count")
    dl_per = targets.get("downlink_per_cpe_mbps")
    ul_per = targets.get("uplink_per_cpe_mbps")
    if su_count and dl_per is not None and ul_per is not None:
        line += f" | per CPE ({su_count}): DL={dl_per} UL={ul_per} Mbps"
    return line


def _operating_rate_for_traffic(
    *,
    bandwidth: str,
    mcs: str,
    spatial_stream: int,
    link_validation: dict[str, object] | None = None,
) -> float:
    """Operating data rate (Mbps) from MCS sheet; use measured link rate when lower."""
    sheet_rate = operating_rate_mbps(bandwidth, mcs, spatial_streams=spatial_stream)
    if not link_validation:
        return sheet_rate
    clients = link_validation.get("clients") or []
    measured: list[float] = []
    for client in clients:
        for key in ("out_rate_mbps", "tx_rate_mbps"):
            value = client.get(key)
            if value is not None and float(value) > 0:
                measured.append(float(value))
    if not measured:
        return sheet_rate
    stable_min = min(measured)
    stable_max = max(measured)
    if stable_max >= sheet_rate * 0.9:
        return sheet_rate
    if stable_min < sheet_rate * 0.9:
        print(
            f"[TARGET] Measured link rate {stable_min:.0f} Mbps < sheet {sheet_rate:.0f} Mbps "
            f"— using measured rate for TRex load"
        )
        return stable_min
    return sheet_rate


def _resolve_traffic_targets(
    *,
    bandwidth: str,
    mcs: str,
    ratio: str,
    args: argparse.Namespace,
    phy_overrides: dict[str, dict[str, float]],
    legacy_mcs_caps: dict[str, float],
    su_count: int | None = None,
    link_validation: dict[str, object] | None = None,
) -> dict[str, object]:
    if args.use_dynamic_target:
        operating_rate = _operating_rate_for_traffic(
            bandwidth=bandwidth,
            mcs=mcs,
            spatial_stream=int(args.spatial_stream),
            link_validation=link_validation,
        )
        return compute_traffic_targets(
            bandwidth=bandwidth,
            mcs=mcs,
            ratio=ratio,
            efficiency_factor=args.efficiency,
            target_ceiling_mbps=args.target if args.target > 0 else None,
            phy_overrides=phy_overrides,
            legacy_mcs_caps=legacy_mcs_caps if not args.ignore_legacy_caps else None,
            spatial_streams=int(args.spatial_stream),
            su_count=su_count,
            operating_rate_mbps=operating_rate,
        )

    dl_ratio, ul_ratio = _parse_ratio(ratio)
    total = dl_ratio + ul_ratio
    cap = legacy_mcs_caps.get(mcs.upper())
    effective = min(args.target, float(cap)) if cap is not None else args.target
    downlink_total = effective * (dl_ratio / total)
    uplink_total = effective * (ul_ratio / total)
    if downlink_total > 0 and uplink_total > 0:
        direction = "bidi"
    elif downlink_total > 0:
        direction = "downlink"
    else:
        direction = "uplink"
    result: dict[str, object] = {
        "phy_max_mbps": None,
        "efficiency_factor": args.efficiency,
        "effective_target_mbps": round(effective, 2),
        "target_ceiling_mbps": args.target,
        "downlink_mbps": round(downlink_total, 2),
        "uplink_mbps": round(uplink_total, 2),
        "trex_dl_bw": f"{max(1, int(round(downlink_total)))}M",
        "trex_ul_bw": f"{max(1, int(round(uplink_total)))}M",
        "trex_direction": direction,
    }
    if su_count and su_count > 0:
        result["su_count"] = su_count
        result["downlink_per_cpe_mbps"] = round(downlink_total / su_count, 2)
        result["uplink_per_cpe_mbps"] = round(uplink_total / su_count, 2)
    return result


def _resolve_dut_ip(profile_bundle) -> str:
    dut = profile_bundle.active["dut"]
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        return normalize_ip(str(dut["local_ipv6"]))
    return normalize_ip(str(dut.get("local_ip") or dut.get("local_ipv6") or ""))


def _resolve_dut_ssh_ip(profile_bundle, dut_ip: str) -> str:
    """SSH/mgmt reachability host — profile ssh_host overrides mgmt IPv6 when set."""
    dut = profile_bundle.active["dut"]
    ssh_host = str(dut.get("ssh_host") or "").strip()
    return normalize_ip(ssh_host) if ssh_host else dut_ip


def _ensure_lab_pc_fallback_for_bts_ssh(
    profile_bundle,
    dut_ssh_ip: str,
    *,
    password: str,
) -> None:
    """
    If BTS SSH is via 10.0.0.x (factory/WAN), ensure the lab backend NIC has a
    10.0.0.x address (e.g. enp3s0 → 10.0.0.10). Throughput does not always run
    full testbed bootstrap, so this must be explicit.
    """
    host = normalize_ip(dut_ssh_ip)
    if not host.startswith("10.0."):
        return
    tb = dict(profile_bundle.active.get("testbed") or {})
    primary = dict(tb.get("primary_pc") or {})
    if not primary:
        primary = {
            "local": True,
            "mgmt_interface": "enp3s0",
            "fallback_ipv4": "10.0.0.10",
            "fallback_prefix_len": 8,
        }
    primary.setdefault("local", not str(primary.get("ssh") or primary.get("internet_ssh") or "").strip())
    primary.setdefault("mgmt_interface", "enp3s0")
    primary.setdefault("fallback_ipv4", "10.0.0.10")
    primary.setdefault("fallback_prefix_len", 8)
    internet_ssh = str(primary.get("internet_ssh") or "").strip()
    if internet_ssh and not primary.get("ssh"):
        primary["ssh"] = internet_ssh
        primary["local"] = False

    from utils.lab_pc_net import ensure_fallback_subnet

    print(
        f"[testbed] Ensuring lab PC backend has 10.0.0.x for BTS SSH {host} "
        f"(iface={primary.get('mgmt_interface')}, fallback={primary.get('fallback_ipv4')})"
    )
    ok = asyncio.run(ensure_fallback_subnet(primary, password))
    if not ok:
        print(
            f"[WARN] Could not ensure 10.0.0.x on lab PC — SSH to {host} may fail "
            f"if the backend interface has no address in that subnet"
        )


def _artifact_name(bandwidth: str, mcs: str, mode: str, ratio: str) -> str:
    safe_ratio = ratio.replace(":", "_")
    return f"Throughput_{bandwidth}_{mcs}_{mode}_{safe_ratio}.json"


def _resolve_logs_paths(output_dir_arg: str) -> tuple[Path, Path, str]:
    """All performance artifacts live under reports/artifacts/."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logs_root = Path("reports/artifacts")
    logs_root.mkdir(parents=True, exist_ok=True)
    base = Path(output_dir_arg)
    if base.parts and tuple(base.parts[:2]) != ("reports", "artifacts"):
        base = logs_root
    elif base.parts == () or str(base) == ".":
        base = logs_root
    output_dir = base / f"performance_{timestamp}"
    html_path = logs_root / f"Performance_Report_{timestamp}.html"
    executed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return output_dir, html_path, executed_at


def _fetch_link_validation(
    dut_ip: str,
    *,
    bandwidth: str,
    mcs: str,
    link_wifi_idx: int,
    spatial_stream: int,
    tolerance_mbps: float = 10.0,
    tolerance_pct: float = 0.08,
    source: str = "ssh",
    ssh_user: str = "root",
    ssh_password: str = "",
    cpe_hosts: list[str] | None = None,
) -> dict[str, object]:
    clients = fetch_link_clients(
        dut_ip,
        radio_idx=link_wifi_idx,
        source=source,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        cpe_hosts=cpe_hosts,
    )
    return validate_operating_rates(
        bandwidth=bandwidth,
        configured_mcs=mcs,
        clients=clients,
        spatial_streams=spatial_stream,
        tolerance_mbps=tolerance_mbps,
        tolerance_pct=tolerance_pct,
    )


def _wait_for_link_rate(
    dut_ip: str,
    *,
    bandwidth: str,
    mcs: str,
    link_wifi_idx: int,
    spatial_stream: int,
    tolerance_mbps: float = 10.0,
    tolerance_pct: float = 0.08,
    timeout_s: float = 45.0,
    poll_s: float = 3.0,
    source: str = "ssh",
    ssh_user: str = "root",
    ssh_password: str = "",
    cpe_hosts: list[str] | None = None,
) -> dict[str, object]:
    """Poll until operating Out rate matches spec (secondary); always continue to TRex."""
    expected = operating_rate_mbps(bandwidth, mcs, spatial_streams=spatial_stream)
    deadline = time.time() + timeout_s
    last_validation: dict[str, object] = {}
    while time.time() < deadline:
        validation = _fetch_link_validation(
            dut_ip,
            bandwidth=bandwidth,
            mcs=mcs,
            link_wifi_idx=link_wifi_idx,
            spatial_stream=spatial_stream,
            tolerance_mbps=tolerance_mbps,
            tolerance_pct=tolerance_pct,
            source=source,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            cpe_hosts=cpe_hosts,
        )
        last_validation = validation
        if validation.get("operating_rate_ok"):
            print(f"[DUT] Operating Out rate stable at {expected:.0f} Mbps")
            return validation
        clients = validation.get("clients") or []
        if clients:
            primary = clients[0]
            print(
                f"[DUT] (secondary) waiting for Out rate {expected:.0f} Mbps — "
                f"current Out={primary.get('out_rate') or primary.get('tx_rate')} "
                f"In={primary.get('in_rate') or primary.get('rx_rate')}"
            )
        time.sleep(poll_s)
    print(
        f"[WARN] Operating Out rate did not reach {expected:.0f} Mbps within {timeout_s:.0f}s "
        f"— continuing to throughput (MCS config is primary; report will flag rate mismatch)"
    )
    if not last_validation:
        failed_spec = lookup_spec(mcs, bandwidth, spatial_streams=spatial_stream)
        last_validation = {
            "configured_mcs": mcs,
            "spec": failed_spec,
            "expected_operating_rate_mbps": failed_spec["operating_rate_mbps"],
            "clients": [],
            "operating_rate_ok": False,
            "operating_rate_mismatch": True,
        }
    return last_validation


def _check_bandwidth_before_trex(
    dut_ip: str,
    *,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    mcs: str,
    bandwidth_skipped: bool,
    link_wifi_idx: int,
    spatial_stream: int,
    tolerance_mbps: float,
    tolerance_pct: float,
    source: str,
    cpe_hosts: list[str] | None,
) -> tuple[bool, str, dict[str, object]]:
    """Return (ready, error_message, link_validation). Skips TRex when BW does not match."""
    if bandwidth_skipped:
        return (
            False,
            f"Bandwidth {bandwidth} was not applied (waiting for all SUs to link)",
            {},
        )

    expected_bw = normalize_bandwidth(bandwidth)
    running_bw = read_running_bandwidth(dut_ip, user, password, radio_idx)
    if running_bw and running_bw != expected_bw:
        return (
            False,
            f"Running bandwidth {running_bw} != requested {expected_bw}",
            {},
        )

    validation = _fetch_link_validation(
        dut_ip,
        bandwidth=bandwidth,
        mcs=mcs,
        link_wifi_idx=link_wifi_idx,
        spatial_stream=spatial_stream,
        tolerance_mbps=tolerance_mbps,
        tolerance_pct=tolerance_pct,
        source=source,
        ssh_user=user,
        ssh_password=password,
        cpe_hosts=cpe_hosts,
    )
    if validation.get("operating_rate_ok"):
        return True, "", validation

    if running_bw is None or running_bw != expected_bw:
        clients = validation.get("clients") or []
        if clients:
            primary = clients[0]
            actual = primary.get("out_rate") or primary.get("tx_rate")
            expected_rate = validation.get("expected_operating_rate_mbps")
            return (
                False,
                (
                    f"Operating rate mismatch for {expected_bw}/{mcs}: "
                    f"expected ~{expected_rate} Mbps, current Out={actual}"
                ),
                validation,
            )
        return (
            False,
            (
                f"Bandwidth/rate not confirmed for {expected_bw} "
                f"(running={running_bw or 'unknown'})"
            ),
            validation,
        )

    return True, "", validation


def _mcs_mismatch_note(mcs_config: dict[str, object]) -> str:
    """Human-readable note when post-apply MCS verify did not match target."""
    if mcs_config.get("mcs_config_ok", True):
        return ""
    existing = str(mcs_config.get("mcs_mismatch_note") or mcs_config.get("error") or "").strip()
    if existing:
        return existing
    bad = [
        str(row.get("label"))
        for row in (mcs_config.get("checks") or [])
        if not row.get("ok")
    ]
    if bad:
        return f"MCS mismatch on {', '.join(bad)} — throughput ran anyway"
    return "MCS mismatch — throughput ran anyway"


def _mcs_status_summary(mcs_config: dict[str, object]) -> tuple[str, str]:
    """Return (bts_uci_mcs, su_operating_mcs_csv) from verify checks."""
    checks = mcs_config.get("checks") or []
    bts_mcs = "—"
    su_values: list[str] = []
    for row in checks:
        if row.get("role") == "BTS":
            bts_mcs = str(row.get("actual_mcs") or "—")
        elif row.get("role") == "CPE":
            su_values.append(str(row.get("actual_mcs") or "?"))
    return bts_mcs, ",".join(su_values) if su_values else "—"


def _print_matrix_console_table(
    records: list[dict[str, object]],
    *,
    bandwidths: list[str],
    mcs_rates: list[str],
) -> None:
    """ASCII matrix for Jenkins console: config + TRex result per BW/MCS cell."""
    by_key: dict[tuple[str, str], dict[str, object]] = {}
    for record in records:
        by_key[(str(record.get("bandwidth") or ""), str(record.get("mcs") or ""))] = record

    print("\n" + "=" * 96)
    print("PERFORMANCE MATRIX — CONSOLE SUMMARY")
    print("=" * 96)
    header = (
        f"{'Bandwidth':<10} | {'MCS':<6} | {'BTS':<4} | {'SU rx_mcs':<18} | "
        f"{'Config':<8} | {'TRex':<8} | {'RX Mbps':<10} | Notes"
    )
    print(header)
    print("-" * len(header))

    for bandwidth in bandwidths:
        for mcs in mcs_rates:
            record = by_key.get((bandwidth, mcs), {})
            mcs_config = record.get("mcs_config") or {}
            bts_mcs, su_mcs = _mcs_status_summary(mcs_config)
            if record.get("skipped_trex"):
                config_status = "FAIL" if mcs_config.get("mcs_config_ok") is False else "SKIP"
                trex_status = "SKIP"
            elif record.get("passed"):
                config_status = (
                    "MISMATCH" if mcs_config.get("mcs_config_ok") is False else "OK"
                )
                trex_status = "PASS"
            elif record:
                config_status = (
                    "MISMATCH" if mcs_config.get("mcs_config_ok") is False else "OK"
                )
                trex_status = "FAIL"
            else:
                config_status = "—"
                trex_status = "—"

            stats = record.get("stats") or {}
            combined = stats.get("combined") or {}
            rx_mbps = combined.get("rx_mbps")

            def _fmt_mbps(value: object) -> str:
                if value is None or value == "":
                    return "—"
                try:
                    return f"{float(value):.1f}"
                except (TypeError, ValueError):
                    return str(value)

            notes = str(record.get("mcs_mismatch_note") or record.get("error") or "").strip()
            if not notes:
                notes = _mcs_mismatch_note(mcs_config)
            if len(notes) > 32:
                notes = notes[:29] + "..."
            if not notes and record.get("throughput_grade") == "warn":
                notes = "throughput warn"

            su_display = su_mcs if len(su_mcs) <= 18 else su_mcs[:15] + "..."
            print(
                f"{bandwidth:<10} | {mcs:<6} | {bts_mcs:<4} | {su_display:<18} | "
                f"{config_status:<8} | {trex_status:<8} | {_fmt_mbps(rx_mbps):<10} | {notes}"
            )

    print("=" * 96)


def _append_skipped_iteration(
    records: list[dict[str, object]],
    *,
    bandwidth: str,
    mcs: str,
    mode: str,
    ratio: str,
    target_mbps: float,
    error: str,
    mcs_config: dict[str, object] | None = None,
    link_validation: dict[str, object] | None = None,
    noise_dbm: str,
) -> None:
    records.append(
        {
            "bandwidth": bandwidth,
            "mcs": mcs,
            "mode": mode,
            "ratio": ratio,
            "requested_target_mbps": target_mbps,
            "passed": False,
            "skipped_trex": True,
            "operating_rate_ok": bool((link_validation or {}).get("operating_rate_ok")),
            "error": error,
            "stats": {},
            "mcs_config": mcs_config or {},
            "link_validation": link_validation or {},
            "noise_dbm": noise_dbm,
        }
    )


async def _ensure_dut_ready(recovery_manager: RecoveryManager, dut_ip: str) -> None:
    if not await recovery_manager.is_gui_reachable(dut_ip):
        print("[RECOVERY] DUT GUI not reachable — running soft recovery before matrix...")
        await recovery_manager.run_soft_recovery()


def run_performance_matrix(args: argparse.Namespace) -> dict[str, object]:
    perf_defaults = PERFORMANCE_DEFAULTS
    trex_defaults = TRAFFIC_DEFAULTS["trex"]

    bandwidths = _parse_csv_list(args.bandwidths)
    mcs_rates = [mcs.upper() for mcs in _parse_csv_list(args.mcs)]
    bidir_ratios = [FIXED_DL_UL_RATIO]
    traffic_profiles = [{"name": "Bidirectional", "ratio": FIXED_DL_UL_RATIO}]
    mcs_caps = {key.upper(): float(value) for key, value in perf_defaults["mcs_traffic_cap_mbps"].items()}
    phy_overrides = perf_defaults.get("phy_max_rate_mbps") or {}

    profile_bundle = load_profile_bundle(
        profile_name=args.profile,
        recovery_profile_name=args.recovery_profile,
        local_ip=args.local_ip or None,
        remote_ip=args.cpe_ip or None,
        username=args.dut_user,
        password=args.dut_password,
    )
    _apply_profile_run_defaults(args, profile_bundle)
    recovery_manager = RecoveryManager(profile_bundle)
    dut = profile_bundle.active["dut"]
    dut_ip = _resolve_dut_ip(profile_bundle)
    dut_ssh_ip = _resolve_dut_ssh_ip(profile_bundle, dut_ip)
    dut_user = args.dut_user or dut["username"]
    dut_password = args.dut_password or dut["password"]
    testbed_tb = dict(profile_bundle.active.get("testbed") or {})
    if not args.skip_dut_config:
        _ensure_lab_pc_fallback_for_bts_ssh(
            profile_bundle, dut_ssh_ip, password=dut_password
        )

    qinq_svlan = args.trex_svlan
    qinq_cvlan = args.trex_cvlan
    if args.trex_qinq and qinq_svlan is None and qinq_cvlan is None:
        qinq_svlan, qinq_cvlan = qinq_tags_from_profile(testbed_tb)
        if qinq_svlan is not None and qinq_cvlan is not None:
            print(f"[QinQ] TRex tags from profile: svlan={qinq_svlan} cvlan={qinq_cvlan}")
        else:
            print("[QinQ] No testbed.qinq in profile; TRex streams will be untagged")
    elif qinq_svlan is not None and qinq_cvlan is not None:
        print(f"[QinQ] TRex tags: svlan={qinq_svlan} cvlan={qinq_cvlan} (CLI override)")
    elif not args.trex_qinq:
        print("[QinQ] Disabled (--no-qinq); TRex streams will be untagged")
        qinq_svlan, qinq_cvlan = None, None

    output_dir, html_path, executed_at = _resolve_logs_paths(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    testbed_summary: dict[str, object] = {}

    total_iterations = len(bandwidths) * len(mcs_rates) * len(traffic_profiles)
    print("\n" + "=" * 72)
    print("UBR PERFORMANCE MATRIX")
    print("=" * 72)
    print(f"DUT IP:           {dut_ip}")
    if dut_ssh_ip != dut_ip:
        print(f"DUT SSH:          {dut_ssh_ip}")
    print(f"DUT link stats:   {args.link_stats_source} via {dut_ssh_ip}")
    print(f"TRex BSU server:  {args.trex_server}")
    su_hosts = [h for h in (args.trex_server_su, args.trex_server_su2, args.trex_server_su3, args.trex_server_su4) if h]
    if su_hosts:
        print(f"TRex SU servers:  {', '.join(su_hosts)}")
    print(f"TRex SU count:    {args.su_count}")
    print(f"TRex BSU ports:   {args.trex_ports}")
    print(f"Bandwidths:       {', '.join(bandwidths)}")
    print(f"MCS rates:        {', '.join(mcs_rates)}")
    print(f"Traffic profiles: {len(traffic_profiles)} per MCS/BW ({total_iterations} total runs)")
    print(
        f"Target mode:      {'dynamic (PHY max x efficiency)' if args.use_dynamic_target else 'static ceiling'}"
    )
    print(f"Efficiency:       {args.efficiency:.0%}")
    if args.target > 0:
        print(f"Target ceiling:   {args.target} Mbps (optional cap)")
    print(f"Duration:         {args.time}s")
    print(f"Output directory: {output_dir}")
    print(f"HTML report:      {html_path}")
    print("=" * 72 + "\n")

    if args.dry_run:
        iteration = 0
        for bandwidth in bandwidths:
            for mcs in mcs_rates:
                for profile in traffic_profiles:
                    iteration += 1
                    targets = _resolve_traffic_targets(
                        bandwidth=bandwidth,
                        mcs=mcs,
                        ratio=profile["ratio"],
                        args=args,
                        phy_overrides=phy_overrides,
                        legacy_mcs_caps=mcs_caps,
                        su_count=args.su_count,
                    )
                    spec = lookup_spec(mcs, bandwidth, spatial_streams=int(args.spatial_stream))
                    print(
                        f"[{iteration}/{total_iterations}] "
                        f"BW={bandwidth} MCS={mcs} Mode={profile['name']} Ratio={profile['ratio']} | "
                        f"sheet_rate={spec['operating_rate_mbps']} Mbps, "
                        f"effective={targets['effective_target_mbps']} Mbps, "
                        f"{_format_traffic_target_log(targets)}"
                    )
        return {
            "dry_run": True,
            "iterations": total_iterations,
            "output_dir": str(output_dir),
            "html_report": str(html_path),
        }

    profile_hosts = [str(ip) for ip in (dut.get("remote_ipv6s") or dut.get("remote_ips") or [])]
    configured_su = args.su_count
    detected_clients = fetch_link_clients(
        dut_ssh_ip,
        radio_idx=args.link_wifi_idx,
        source=args.link_stats_source,
        ssh_user=dut_user,
        ssh_password=dut_password,
        cpe_hosts=profile_hosts,
        max_sua=max(configured_su, 8),
    )
    detected_count = len(detected_clients)
    # Live associated IPs only — never pad with profile placeholders while any SU is up.
    live_hosts = resolve_cpe_hosts_for_run([], detected_clients, su_count=configured_su)
    if live_hosts:
        cpe_hosts = live_hosts
        args.su_count = len(cpe_hosts)
        print(
            f"[DUT] Using {len(cpe_hosts)} live-associated SU(s) for throughput "
            f"(profile su_count was {configured_su}): {', '.join(cpe_hosts)}"
        )
        print(f"[DUT] Effective TRex SU count: {args.su_count}")
        if detected_count > configured_su:
            print(
                f"[DUT] BTS reports {detected_count} associated SUA slot(s); "
                f"capped to {len(cpe_hosts)} for this run"
            )
    else:
        cpe_hosts = resolve_cpe_hosts_for_run(
            profile_hosts,
            [],
            su_count=configured_su,
        )
        if cpe_hosts:
            print(
                f"[DUT] No live SU IPs from sysfs — falling back to profile hosts "
                f"({len(cpe_hosts)}/{configured_su}): {', '.join(cpe_hosts)}"
            )
        else:
            print(
                f"[WARN] Could not detect connected CPE IPs via {args.link_stats_source}; "
                f"using su_count={args.su_count} without CPE host list"
            )
    if not args.skip_dut_config:
        try:
            mgmt = testbed_tb.get("mgmt_vlan") or {}
            ipv6_prefix_len = int(mgmt.get("prefix_len") or dut.get("ipv6_prefix_len") or 120)
            testbed_summary = asyncio.run(
                collect_testbed_summary(
                    dut_ip,
                    cpe_hosts,
                    dut_password,
                    bts_ssh_host=dut_ssh_ip,
                    ipv6_prefix_len=ipv6_prefix_len,
                    su_count=args.su_count,
                )
            )
            testbed_summary["stand"] = args.stand or profile_bundle.active.get("name", "")
            testbed_summary["profile"] = args.profile
        except Exception as exc:
            print(f"[WARN] Testbed summary collection failed: {exc}")

        asyncio.run(_ensure_dut_ready(recovery_manager, dut_ssh_ip))
    else:
        print("[CONFIG] Skipping DUT recovery/radio config (--skip-dut-config); TRex traffic only")

    records: list[dict[str, object]] = []
    iteration = 0
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        for bandwidth in bandwidths:
            bandwidth_group_ok = True
            bw_group_error = ""
            bw_group_config: dict[str, object] = {}
            group_ratio = traffic_profiles[0]["ratio"] if traffic_profiles else FIXED_DL_UL_RATIO

            if not args.skip_dut_config:
                print(
                    f"\n>>> Applying bandwidth once for all MCS: "
                    f"bw={bandwidth}, ratio={group_ratio}"
                )
                try:
                    bw_group_config = configure_bandwidth_profile(
                        dut_ssh_ip,
                        dut_user,
                        dut_password,
                        args.radio_index,
                        bandwidth,
                        group_ratio,
                        cpe_hosts=cpe_hosts,
                        su_count=args.su_count,
                        bandwidth_apply_wait_s=args.bandwidth_apply_wait_s,
                        su_link_wait_s=args.su_link_wait_s,
                        bandwidth_running_wait_s=args.bandwidth_running_wait_s,
                        require_all_su_for_bandwidth=args.require_all_su_for_bandwidth,
                        profile_tb=testbed_tb,
                        dut_cfg=dut,
                        skip_if_unchanged=args.skip_config_if_unchanged,
                        link_debug_dir=(
                            str(Path(args.link_debug_dir) / normalize_bandwidth(bandwidth))
                            if args.link_debug_dir
                            else None
                        ),
                    )
                except Exception as exc:
                    bandwidth_group_ok = False
                    bw_group_error = str(exc)
                    print(f"[ERROR] Bandwidth profile failed for {bandwidth}: {exc}")

            for mcs in mcs_rates:
                for profile in traffic_profiles:
                    iteration += 1
                    ratio = profile["ratio"]
                    mode = profile["name"]
                    print(
                        f"\n>>> Configuring MCS only | "
                        f"bw={bandwidth}, mcs={mcs}, ratio={ratio}, cpe={cpe_hosts or 'none'}"
                    )
                    pre_trex_link_validation: dict[str, object] = {}
                    mcs_config: dict[str, object] = dict(bw_group_config)
                    if args.skip_dut_config:
                        print("[CONFIG] Skipping DUT radio apply (--skip-dut-config)")
                    elif not bandwidth_group_ok:
                        print(f"[ERROR] {bw_group_error} — skipping MCS/TRex for {mcs}")
                        _append_skipped_iteration(
                            records,
                            bandwidth=bandwidth,
                            mcs=mcs,
                            mode=mode,
                            ratio=ratio,
                            target_mbps=args.target,
                            error=bw_group_error,
                            mcs_config=mcs_config,
                            noise_dbm=args.noise_dbm,
                        )
                        continue
                    else:
                        try:
                            mcs_config = configure_mcs_profile(
                                dut_ssh_ip,
                                dut_user,
                                dut_password,
                                args.radio_index,
                                bandwidth,
                                mcs,
                                ratio,
                                args.spatial_stream,
                                cpe_hosts=cpe_hosts,
                                cpe_radio_idx=args.cpe_radio_index,
                                su_count=args.su_count,
                                prefer_cpe_via_bts=args.cpe_via_bts,
                                settle_s=args.radio_settle_s,
                                operating_mcs_wait_s=args.operating_mcs_wait_s,
                                mcs_kickmac_wait_s=args.mcs_kickmac_wait_s,
                                snmp_community=args.snmp_community,
                                snmp_radio_idx=args.snmp_radio_index,
                                skip_if_unchanged=args.skip_config_if_unchanged,
                            )
                            if not mcs_config.get("mcs_config_ok", True):
                                mcs_note = _mcs_mismatch_note(mcs_config)
                                mcs_config = dict(mcs_config)
                                mcs_config["mcs_mismatch_note"] = mcs_note
                                print(
                                    f"[WARN] {mcs_note} — continuing with TRex throughput"
                                )
                            bw_ready, bw_error, pre_trex_link_validation = (
                                _check_bandwidth_before_trex(
                                    dut_ssh_ip,
                                    user=dut_user,
                                    password=dut_password,
                                    radio_idx=args.radio_index,
                                    bandwidth=bandwidth,
                                    mcs=mcs,
                                    bandwidth_skipped=bool(
                                        mcs_config.get("bandwidth_skipped")
                                    ),
                                    link_wifi_idx=args.link_wifi_idx,
                                    spatial_stream=int(args.spatial_stream),
                                    tolerance_mbps=args.rate_tolerance_mbps,
                                    tolerance_pct=args.rate_tolerance_pct,
                                    source=args.link_stats_source,
                                    cpe_hosts=cpe_hosts,
                                )
                            )
                            if not bw_ready:
                                print(f"[ERROR] {bw_error} — skipping TRex")
                                _append_skipped_iteration(
                                    records,
                                    bandwidth=bandwidth,
                                    mcs=mcs,
                                    mode=mode,
                                    ratio=ratio,
                                    target_mbps=args.target,
                                    error=bw_error,
                                    mcs_config=mcs_config,
                                    link_validation=pre_trex_link_validation,
                                    noise_dbm=args.noise_dbm,
                                )
                                continue
                            print(
                                "[DUT] MCS configured on all devices — "
                                "polling operating rate (secondary, does not block TRex)"
                            )
                            pre_trex_link_validation = _wait_for_link_rate(
                                dut_ssh_ip,
                                bandwidth=bandwidth,
                                mcs=mcs,
                                link_wifi_idx=args.link_wifi_idx,
                                spatial_stream=int(args.spatial_stream),
                                tolerance_mbps=args.rate_tolerance_mbps,
                                tolerance_pct=args.rate_tolerance_pct,
                                timeout_s=args.link_wait_s,
                                source=args.link_stats_source,
                                ssh_user=dut_user,
                                ssh_password=dut_password,
                                cpe_hosts=cpe_hosts,
                            )
                        except Exception as exc:
                            print(f"[ERROR] Failed to configure DUT for {bandwidth}/{mcs}/{ratio}: {exc}")
                            try:
                                failed_spec = lookup_spec(
                                    mcs, bandwidth, spatial_streams=int(args.spatial_stream)
                                )
                                link_validation = {
                                    "configured_mcs": mcs,
                                    "spec": failed_spec,
                                    "expected_operating_rate_mbps": failed_spec["operating_rate_mbps"],
                                    "clients": [],
                                    "operating_rate_ok": False,
                                    "operating_rate_mismatch": True,
                                }
                            except Exception:
                                link_validation = {}
                            records.append(
                                {
                                    "bandwidth": bandwidth,
                                    "mcs": mcs,
                                    "mode": mode,
                                    "ratio": ratio,
                                    "requested_target_mbps": args.target,
                                    "passed": False,
                                    "error": f"DUT config failed: {exc}",
                                    "stats": {},
                                    "link_validation": link_validation,
                                    "noise_dbm": args.noise_dbm,
                                }
                            )
                            continue
                    targets = _resolve_traffic_targets(
                        bandwidth=bandwidth,
                        mcs=mcs,
                        ratio=ratio,
                        args=args,
                        phy_overrides=phy_overrides,
                        legacy_mcs_caps=mcs_caps,
                        su_count=args.su_count,
                        link_validation=pre_trex_link_validation or None,
                    )
                    print(
                        f"\n--- [{iteration}/{total_iterations}] "
                        f"BW={bandwidth} MCS={mcs} Mode={mode} Ratio={ratio} ---"
                    )
                    spec = lookup_spec(mcs, bandwidth, spatial_streams=int(args.spatial_stream))
                    print(
                        f"Targets: operating_rate={targets.get('operating_rate_mbps', spec['operating_rate_mbps'])} Mbps, "
                        f"effective={targets['effective_target_mbps']} Mbps "
                        f"(efficiency={targets['efficiency_factor']:.0%}), "
                        f"{_format_traffic_target_log(targets)}"
                    )
                    dl_bw = targets["trex_dl_bw"]
                    ul_bw = targets["trex_ul_bw"]
                    direction = targets["trex_direction"]
                    effective_target = targets["effective_target_mbps"]
                    record: dict[str, object] = {
                        "bandwidth": bandwidth,
                        "mcs": mcs,
                        "mode": mode,
                        "ratio": ratio,
                        "requested_target_mbps": args.target,
                        "phy_max_mbps": targets.get("phy_max_mbps"),
                        "operating_rate_mbps": targets.get("operating_rate_mbps"),
                        "efficiency_factor": targets.get("efficiency_factor"),
                        "effective_target_mbps": effective_target,
                        "downlink_target_mbps": targets.get("downlink_mbps"),
                        "uplink_target_mbps": targets.get("uplink_mbps"),
                        "trex_dl_bw": dl_bw,
                        "trex_ul_bw": ul_bw,
                        "trex_direction": direction,
                        "downlink_per_cpe_mbps": targets.get("downlink_per_cpe_mbps"),
                        "uplink_per_cpe_mbps": targets.get("uplink_per_cpe_mbps"),
                        "link_validation": pre_trex_link_validation,
                        "mcs_config": mcs_config,
                        "mcs_mismatch_note": _mcs_mismatch_note(mcs_config),
                        "cpe_hosts": cpe_hosts,
                        "packet_size": args.packet_size,
                        "duration_s": args.time,
                        "spatial_stream": int(args.spatial_stream),
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                    artifact = output_dir / _artifact_name(bandwidth, mcs, mode, ratio)
                    record["artifact"] = str(artifact)
    
                    try:
                        print(
                            f"[TRex] Launching throughput for {bandwidth}/{mcs} "
                            f"({mode}, {ratio}) — {args.time}s"
                        )
                        trex_result = run_trex_stats_check(
                            trex_server=args.trex_server,
                            trex_server_su=args.trex_server_su or None,
                            trex_server_su2=args.trex_server_su2 or None,
                            trex_server_su3=args.trex_server_su3 or None,
                            trex_server_su4=args.trex_server_su4 or None,
                            trex_user=args.trex_user,
                            trex_password=args.trex_password,
                            trex_dir=args.trex_dir,
                            trex_pythonpath=args.trex_pythonpath,
                            trex_client_script=args.trex_client_script,
                            trex_ports=args.trex_ports,
                            trex_server_cores=args.trex_server_cores,
                            trex_server_startup_s=args.trex_server_startup_s,
                            duration_s=args.time,
                            expected_min_mbps=args.expected_min_mbps,
                            mcs_target_mbps=effective_target,
                            trex_su_count=args.su_count,
                            trex_dl_bw=dl_bw,
                            trex_ul_bw=ul_bw,
                            trex_packet_size=args.packet_size,
                            trex_direction=direction,
                            trex_protocol=args.trex_proto,
                            trex_svlan=qinq_svlan,
                            trex_cvlan=qinq_cvlan,
                            trex_qinq_enabled=args.trex_qinq,
                            trex_qinq_host=dut_ssh_ip,
                            profile_tb=testbed_tb,
                            dut_host=dut_ssh_ip,
                            dut_user=dut_user,
                            dut_password=dut_password,
                            dut_radio_idx=args.radio_index,
                            deploy_client_script=args.deploy_client_script and iteration == 1,
                            reuse_existing_server=iteration > 1,
                            keep_server_running=iteration < total_iterations,
                        )
                        export = {
                            "mode": "performance_matrix",
                            "profile": {
                                "active": args.profile,
                                "recovery": args.recovery_profile,
                            },
                            "matrix": {
                                "bandwidth": bandwidth,
                                "mcs": mcs,
                                "mode": mode,
                                "ratio": ratio,
                                "requested_target_mbps": args.target,
                                "phy_max_mbps": targets.get("phy_max_mbps"),
                                "operating_rate_mbps": targets.get("operating_rate_mbps"),
                                "efficiency_factor": targets.get("efficiency_factor"),
                                "effective_target_mbps": effective_target,
                                "downlink_target_mbps": targets.get("downlink_mbps"),
                                "uplink_target_mbps": targets.get("uplink_mbps"),
                                "trex_dl_bw": dl_bw,
                                "trex_ul_bw": ul_bw,
                                "downlink_per_cpe_mbps": targets.get("downlink_per_cpe_mbps"),
                                "uplink_per_cpe_mbps": targets.get("uplink_per_cpe_mbps"),
                            },
                            "config": {
                                "target_mbps": effective_target,
                                "phy_max_mbps": targets.get("phy_max_mbps"),
                                "efficiency_factor": targets.get("efficiency_factor"),
                                "ratio": ratio,
                                "traffic_backend": "trex",
                                "duration_s": args.time,
                                "su_count": args.su_count,
                                "packet_size": args.packet_size,
                                "spatial_stream": args.spatial_stream,
                                "bandwidth": bandwidth,
                                "mcs_rate": mcs,
                            },
                            "recovery": {
                                "attempts": recovery_manager.metrics.attempts,
                                "successes": recovery_manager.metrics.successes,
                                "failures": recovery_manager.metrics.failures,
                                "factory_resets": recovery_manager.metrics.factory_resets,
                                "last_error": recovery_manager.metrics.last_error,
                            },
                            "combined": trex_result.get("combined", {}),
                            "downlink": trex_result.get("downlink", {}),
                            "uplink": trex_result.get("uplink", {}),
                            "trex": trex_result,
                        }
                        validation = trex_result.get("validation") or {}
                        link_validation = pre_trex_link_validation or record.get("link_validation") or {}
                        post_link_validation = _fetch_link_validation(
                            dut_ssh_ip,
                            bandwidth=bandwidth,
                            mcs=mcs,
                            link_wifi_idx=args.link_wifi_idx,
                            spatial_stream=int(args.spatial_stream),
                            tolerance_mbps=args.rate_tolerance_mbps,
                            tolerance_pct=args.rate_tolerance_pct,
                            source=args.link_stats_source,
                            ssh_user=dut_user,
                            ssh_password=dut_password,
                            cpe_hosts=cpe_hosts,
                        )
                        post_clients = post_link_validation.get("clients") or []
                        pre_clients = link_validation.get("clients") or []
                        if post_clients and not pre_clients:
                            link_validation = post_link_validation
                        clients = link_validation.get("clients") or []
                        record["link_validation"] = link_validation
                        record["link_validation_post"] = post_link_validation
                        record["noise_dbm"] = args.noise_dbm
                        export["link_validation"] = link_validation
                        export["link_validation_post"] = post_link_validation
                        trex_passed = bool(validation.get("passed"))
                        throughput_grade = validation.get("grade")
                        rate_ok = bool(link_validation.get("operating_rate_ok"))
                        record["operating_rate_ok"] = rate_ok
                        record["throughput_passed"] = trex_passed
                        record["throughput_grade"] = throughput_grade
                        record["throughput_warn"] = bool(validation.get("throughput_warn"))
                        record["throughput_pct_of_target"] = validation.get("pct_of_target")
                        record["passed"] = trex_passed and (
                            rate_ok or not args.fail_on_rate_mismatch
                        )
                        record["stats"] = export
                        record["finished_at"] = datetime.now(timezone.utc).isoformat()
                        with artifact.open("w", encoding="utf-8") as handle:
                            json.dump(export, handle, indent=2)
                        if rate_ok:
                            rate_note = "rate OK"
                        elif args.fail_on_rate_mismatch:
                            rate_note = "data rate mismatch (FAIL)"
                        else:
                            rate_note = "data rate mismatch (report only)"
                        if not record["passed"]:
                            trex_status = "FAIL"
                        elif throughput_grade == "warn":
                            trex_status = "WARN"
                        else:
                            trex_status = "PASS"
                        print(
                            f"Result: {trex_status} | "
                            f"combined_rx={export['combined'].get('rx_mbps', 0):.2f} Mbps | {rate_note}"
                        )
                        if validation.get("reason") and throughput_grade in {"warn", "fail"}:
                            print(f"  {validation['reason']}")
                        if link_validation.get("operating_rate_mismatch") and clients:
                            primary = clients[0]
                            print(
                                f"  Link rates: Out={primary.get('out_rate') or primary.get('tx_rate')} "
                                f"In={primary.get('in_rate') or primary.get('rx_rate')} "
                                f"(expected Out {link_validation['expected_operating_rate_mbps']} Mbps)"
                            )
                        if post_clients:
                            rf_bits = []
                            for client in post_clients[:6]:
                                su = client.get("su_index") or client.get("sua_index") or "?"
                                snr = f"{client.get('r_snr1') or '—'}/{client.get('r_snr2') or '—'}"
                                rssi = client.get("r_rssi1") or client.get("l_rssi1") or "—"
                                rf_bits.append(f"SU{su} SNR={snr} RSSI={rssi}")
                            print(f"  RF post-TRex: {' | '.join(rf_bits)}")
                    except Exception as exc:
                        record["passed"] = False
                        err_text = str(exc)
                        record["error"] = err_text
                        record["stats"] = {}
                        record["noise_dbm"] = args.noise_dbm
                        try:
                            record["link_validation"] = _fetch_link_validation(
                                dut_ssh_ip,
                                bandwidth=bandwidth,
                                mcs=mcs,
                                link_wifi_idx=args.link_wifi_idx,
                                spatial_stream=int(args.spatial_stream),
                                tolerance_mbps=args.rate_tolerance_mbps,
                                tolerance_pct=args.rate_tolerance_pct,
                                source=args.link_stats_source,
                                ssh_user=dut_user,
                                ssh_password=dut_password,
                                cpe_hosts=cpe_hosts,
                            )
                        except Exception:
                            failed_spec = lookup_spec(
                                mcs, bandwidth, spatial_streams=int(args.spatial_stream)
                            )
                            record["link_validation"] = {
                                "configured_mcs": mcs,
                                "spec": failed_spec,
                                "expected_operating_rate_mbps": failed_spec["operating_rate_mbps"],
                                "clients": [],
                                "operating_rate_ok": False,
                                "operating_rate_mismatch": True,
                            }
                        record["finished_at"] = datetime.now(timezone.utc).isoformat()
                        with artifact.open("w", encoding="utf-8") as handle:
                            json.dump(record, handle, indent=2)
                        if "TRex port" in err_text and "not link UP" in err_text:
                            print(f"Result: SKIP (TRex ports down) | {err_text}")
                        else:
                            print(f"Result: FAIL | {exc}")
    
                    records.append(record)
                    if args.pause_s > 0:
                        time.sleep(args.pause_s)
    finally:
        trex_hosts = [args.trex_server]
        trex_hosts.extend(
            host
            for host in (
                args.trex_server_su,
                args.trex_server_su2,
                args.trex_server_su3,
                args.trex_server_su4,
            )
            if host
        )
        stop_remote_trex_servers(
            trex_hosts,
            trex_user=args.trex_user,
            trex_password=args.trex_password,
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    passed_count = sum(1 for row in records if row.get("passed"))
    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "total_iterations": len(records),
        "passed": passed_count,
        "failed": len(records) - passed_count,
        "output_dir": str(output_dir),
        "html_report": str(html_path),
        "records": records,
    }
    summary_path = output_dir / "performance_matrix_summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    csv_path = output_dir / "performance_matrix_summary.csv"
    trex_cmd_record = next((row for row in records if row.get("trex_dl_bw")), None)
    run_meta = {
        "executed_at": executed_at,
        "Bandwidths": ", ".join(bandwidths),
        "MCS Rates": ", ".join(mcs_rates),
        "Ratios": ", ".join(bidir_ratios),
        "Duration (s)": str(args.time),
    }
    if trex_cmd_record:
        run_meta["TRex client command"] = build_trex_client_command(
            trex_client_script=args.trex_client_script,
            trex_pythonpath=args.trex_pythonpath,
            trex_ports=args.trex_ports,
            trex_server_su=args.trex_server_su or None,
            trex_server_su2=args.trex_server_su2 or None,
            trex_server_su3=args.trex_server_su3 or None,
            trex_server_su4=args.trex_server_su4 or None,
            trex_su_count=args.su_count,
            trex_dl_bw=str(trex_cmd_record.get("trex_dl_bw")),
            trex_ul_bw=str(trex_cmd_record.get("trex_ul_bw")),
            trex_packet_size=args.packet_size,
            duration_s=args.time,
            trex_direction=str(trex_cmd_record.get("trex_direction") or "bidi"),
            trex_protocol=args.trex_proto,
            trex_svlan=qinq_svlan,
            trex_cvlan=qinq_cvlan,
        )
    write_summary_csv(records, csv_path)
    write_html_report(
        records=records,
        run_meta=run_meta,
        path=html_path,
        testbed_summary=testbed_summary,
    )
    grafana_path = html_path.with_name(html_path.stem + "_Grafana.html")
    try:
        report_id = html_path.stem.replace("Performance_Report_", "")
        grafana_payload = build_matrix_grafana_payload(
            records,
            testbed_summary=testbed_summary,
            report_id=report_id,
            generated_at=executed_at,
        )
        write_grafana_sample_html(grafana_path, grafana_payload)
        print(f"Grafana report: {grafana_path}")
    except Exception as exc:
        print(f"[WARN] Grafana report generation failed: {exc}")

    print("\n" + "=" * 72)
    _print_matrix_console_table(
        records,
        bandwidths=bandwidths,
        mcs_rates=mcs_rates,
    )
    print(f"Matrix complete: {passed_count}/{len(records)} passed")
    print(f"Summary JSON: {summary_path}")
    print(f"Summary CSV:  {csv_path}")
    print(f"HTML report:  {html_path}")
    print("=" * 72 + "\n")
    return summary


def build_parser() -> argparse.ArgumentParser:
    perf = PERFORMANCE_DEFAULTS
    trex = TRAFFIC_DEFAULTS["trex"]
    parser = argparse.ArgumentParser(
        description="Run throughput performance matrix across bandwidth, MCS, and DL/UL ratios."
    )
    parser.add_argument("--profile", default=perf["profile"])
    parser.add_argument("--recovery-profile", default=perf["recovery_profile"])
    parser.add_argument(
        "--stand",
        default="",
        help="Lab bench id from config/benches.yaml (sets --profile when left at default)",
    )
    parser.add_argument("--local-ip", default="", help="Override BTS (local DUT) IP from profile")
    parser.add_argument(
        "--cpe-ip",
        default="",
        help="Override CPE IP(s) from profile (comma-separated)",
    )
    parser.add_argument("--dut-user", default="")
    parser.add_argument("--dut-password", default="")
    parser.add_argument(
        "--bandwidths",
        default=",".join(perf["bandwidths"]),
        help="Comma-separated htmode values (HT20,HT40,...)",
    )
    parser.add_argument(
        "--mcs",
        default=",".join(perf["mcs_rates"]),
        help="Comma-separated MCS values (MCS0,MCS1,...)",
    )
    parser.add_argument(
        "--ratios",
        default=FIXED_DL_UL_RATIO,
        help=f"Ignored — matrix always uses fixed DL:UL ratio {FIXED_DL_UL_RATIO}",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=perf["target_mbps"],
        help="Optional ceiling Mbps when using dynamic targets (0 = no ceiling)",
    )
    parser.add_argument(
        "--efficiency",
        type=float,
        default=perf.get("efficiency_factor", 0.75),
        help="Fraction of PHY max used as TRex load (default 0.75)",
    )
    parser.add_argument(
        "--use-dynamic-target",
        action=argparse.BooleanOptionalAction,
        default=perf.get("use_dynamic_target", True),
        help="Derive TRex DL/UL from operating data rate(MCS,bandwidth) x efficiency (default: on)",
    )
    parser.add_argument(
        "--ignore-legacy-caps",
        action="store_true",
        help="Do not apply legacy Jenkins MCS traffic caps when computing dynamic targets",
    )
    parser.add_argument("--time", type=int, default=perf["duration_s"])
    parser.add_argument("--radio-index", type=int, default=perf["radio_index"],
                        help="UCI radio index (wireless.wifiN / txparam.athN)")
    parser.add_argument("--snmp-radio-index", type=int, default=perf.get("snmp_radio_index", 2),
                        help="SNMP SU table index for link stats (default: 2)")
    parser.add_argument("--cpe-radio-index", type=int, default=perf.get("cpe_radio_index", 1),
                        help="UCI radio index on CPE (txparam.athN)")
    parser.add_argument("--cpe-su-index", type=int, default=perf.get("cpe_su_index", 1),
                        help="SU index for BTS remote_exec when pushing MCS to CPE")
    parser.add_argument(
        "--cpe-via-bts",
        action="store_true",
        help="Always apply CPE MCS via BTS remote_exec (skip direct CPE SSH)",
    )
    parser.add_argument("--su-count", type=int, default=perf["su_count"])
    parser.add_argument("--spatial-stream", default=perf["spatial_stream"],
                        help="Spatial streams (2 = use Dual column from spec sheet)")
    parser.add_argument("--snmp-community", default=perf.get("snmp_community", "ubr@rw123"))
    parser.add_argument(
        "--link-stats-source",
        choices=["auto", "ssh", "snmp"],
        default="auto",
        help="Link stats source for CPE count and rate validation (default: auto=SSH then SNMP)",
    )
    parser.add_argument("--noise-dbm", default=perf.get("noise_dbm", "-93"),
                        help="Noise floor shown in report when not polled")
    parser.add_argument("--rate-tolerance-mbps", type=float, default=10.0,
                        help="Absolute Mbps tolerance for operating-rate match")
    parser.add_argument("--rate-tolerance-pct", type=float, default=0.08,
                        help="Relative tolerance for operating-rate match")
    parser.add_argument(
        "--fail-on-rate-mismatch",
        action=argparse.BooleanOptionalAction,
        default=perf.get("fail_on_rate_mismatch", False),
        help="Mark iteration failed when SNMP rate != spec sheet (default: false — report only)",
    )
    parser.add_argument("--packet-size", type=int, default=perf["packet_size"])
    parser.add_argument("--expected-min-mbps", type=float, default=0.0)
    parser.add_argument("--pause-s", type=float, default=2.0, help="Pause between iterations")
    parser.add_argument("--radio-settle-s", type=float, default=perf["radio_settle_s"],
                        help="Seconds to wait after MCS apply before operating-MCS verify")
    parser.add_argument(
        "--operating-mcs-wait-s",
        type=float,
        default=perf["operating_mcs_wait_s"],
        help="Poll BTS sysfs rx_rate_mcs until all SUs match target (seconds)",
    )
    parser.add_argument(
        "--mcs-kickmac-wait-s",
        type=float,
        default=perf["mcs_kickmac_wait_s"],
        help="After kickmac on MCS mismatch, wait this long before re-check (seconds)",
    )
    parser.add_argument(
        "--bandwidth-apply-wait-s",
        type=float,
        default=float(perf.get("bandwidth_apply_wait_s", 60)),
        help="Seconds to hold BTS SSH open after bandwidth/ratio ucidyn apply (default 60)",
    )
    parser.add_argument(
        "--su-link-wait-s",
        type=float,
        default=float(perf.get("su_link_wait_s", 120)),
        help="Seconds to ping SUs after bandwidth apply before MCS re-sync (default 120)",
    )
    parser.add_argument(
        "--bandwidth-running-wait-s",
        type=float,
        default=float(perf.get("bandwidth_running_wait_s", 120)),
        help="Seconds to poll cfg80211tool running htmode after SU link wait (default 120)",
    )
    parser.add_argument(
        "--link-debug-dir",
        default="",
        help="Write per-attempt link/VLAN/sysfs JSON snapshots under this directory (per bandwidth subdir)",
    )
    parser.add_argument(
        "--require-all-su-for-bandwidth",
        action=argparse.BooleanOptionalAction,
        default=perf.get("require_all_su_for_bandwidth"),
        help="Wait for all su_count SUs before bandwidth; skip bw if not all linked (default: true when su_count=4)",
    )
    parser.add_argument("--link-wait-s", type=float, default=perf.get("link_wait_s", 45.0),
                        help="Poll SNMP until operating rate matches spec before TRex")
    parser.add_argument(
        "--output-dir",
        default=perf["artifact_dir"],
        help="Base directory under reports/artifacts/ for per-run artifacts",
    )
    parser.add_argument(
        "--skip-config-if-unchanged",
        action=argparse.BooleanOptionalAction,
        default=perf.get("skip_config_if_unchanged", True),
        help="Skip MCS/bw/ratio apply when BTS + all CPEs already match target (default: on)",
    )
    parser.add_argument(
        "--skip-dut-config",
        action="store_true",
        help="Skip DUT recovery and radio MCS/bw apply; run TRex traffic only (lab pre-configured)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print matrix plan without running traffic")
    parser.add_argument(
        "--ratios-only",
        action="store_true",
        help="Run only the bidirectional ratios from --ratios (skip 100:0 and 0:100)",
    )
    parser.add_argument(
        "--deploy-client-script",
        action="store_true",
        help="Deploy bundled TRex client script before the first iteration",
    )
    parser.add_argument("--trex-server", default=trex["host"])
    parser.add_argument("--trex-server-su", default="", help="Remote TRex SU server 1 (4-7 SUs)")
    parser.add_argument("--trex-server-su2", default="", help="Remote TRex SU server 2 (8-11 SUs)")
    parser.add_argument("--trex-server-su3", default="", help="Remote TRex SU server 3 (12-15 SUs)")
    parser.add_argument("--trex-server-su4", default="", help="Remote TRex SU server 4 (16+ SUs)")
    parser.add_argument("--trex-user", default=trex["user"])
    parser.add_argument("--trex-password", default=os.getenv("TREX_PASSWORD", trex["password"]))
    parser.add_argument("--trex-dir", default=trex["directory"])
    parser.add_argument("--trex-pythonpath", default=trex["pythonpath"])
    parser.add_argument("--trex-client-script", default=trex["client_script"])
    parser.add_argument("--trex-ports", default=trex["ports"])
    parser.add_argument("--trex-server-cores", type=int, default=trex["server_cores"])
    parser.add_argument("--trex-server-startup-s", type=int, default=trex.get("server_startup_s", 25))
    parser.add_argument("--trex-proto", choices=["udp", "tcp", "both"], default="udp")
    parser.add_argument("--trex-svlan", type=int, default=None, help="QinQ outer S-VLAN override for TRex")
    parser.add_argument("--trex-cvlan", type=int, default=None, help="QinQ inner C-VLAN override for TRex")
    parser.add_argument(
        "--no-qinq",
        dest="trex_qinq",
        action="store_false",
        help="Disable QinQ tag read from BTS; send untagged TRex streams",
    )
    parser.set_defaults(trex_qinq=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    enable_live_console_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.trex_svlan is None) ^ (args.trex_cvlan is None):
        parser.error("QinQ requires both --trex-svlan and --trex-cvlan when either is set.")
    _resolve_stand_profile_args(args)
    summary = run_performance_matrix(args)
    if summary.get("dry_run"):
        return 0
    failed = int(summary.get("failed", 0))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
