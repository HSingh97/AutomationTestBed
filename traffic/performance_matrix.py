#!/usr/bin/env python3
"""
Performance matrix runner: sweep bandwidth x MCS x DL/UL ratio and capture throughput.

For each combination the script:
  1. Applies DUT radio settings (htmode + modulation) over SSH
  2. Runs TRex throughput (stats_check)
  3. Saves per-iteration JSON plus consolidated CSV/HTML reports

Example:
  PYTHONPATH=. python3 traffic/performance_matrix.py \\
    --profile default \\
    --bandwidths HT20,HT40,HT80,HT160 \\
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
from traffic.dut_radio_config import configure_radio_profile
from traffic.link_stats import fetch_link_clients, validate_operating_rates
from traffic.operating_rate_table import operating_rate_mbps
from traffic.operating_rate_table import lookup_spec
from traffic.phy_rate_targets import compute_traffic_targets
from traffic.trex_runner import run_trex_stats_check
from utils.net_utils import normalize_ip
from utils.performance_report import write_html_report, write_summary_csv
from utils.profile_manager import load_profile_bundle
from utils.recovery_manager import RecoveryManager
from utils.regression_device_info import collect_testbed_summary


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


def _resolve_traffic_targets(
    *,
    bandwidth: str,
    mcs: str,
    ratio: str,
    args: argparse.Namespace,
    phy_overrides: dict[str, dict[str, float]],
    legacy_mcs_caps: dict[str, float],
) -> dict[str, object]:
    if args.use_dynamic_target:
        return compute_traffic_targets(
            bandwidth=bandwidth,
            mcs=mcs,
            ratio=ratio,
            efficiency_factor=args.efficiency,
            target_ceiling_mbps=args.target if args.target > 0 else None,
            phy_overrides=phy_overrides,
            legacy_mcs_caps=legacy_mcs_caps if not args.ignore_legacy_caps else None,
            spatial_streams=int(args.spatial_stream),
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
    return {
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


def _resolve_dut_ip(profile_bundle) -> str:
    dut = profile_bundle.active["dut"]
    if dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6"):
        return normalize_ip(str(dut["local_ipv6"]))
    return normalize_ip(str(dut.get("local_ip") or dut.get("local_ipv6") or ""))


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


def _wait_for_link_rate(
    dut_ip: str,
    *,
    bandwidth: str,
    mcs: str,
    snmp_community: str,
    snmp_radio_index: int,
    spatial_stream: int,
    timeout_s: float = 45.0,
    poll_s: float = 3.0,
) -> bool:
    """Poll SNMP until operating rate matches spec or timeout."""
    expected = operating_rate_mbps(bandwidth, mcs, spatial_streams=spatial_stream)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        clients = fetch_link_clients(
            dut_ip, snmp_community=snmp_community, radio_idx=snmp_radio_index
        )
        if clients:
            validation = validate_operating_rates(
                bandwidth=bandwidth,
                configured_mcs=mcs,
                clients=clients,
                spatial_streams=spatial_stream,
            )
            if validation.get("operating_rate_ok"):
                print(f"[DUT] Link rate stable at {expected:.0f} Mbps")
                return True
            if clients:
                primary = clients[0]
                print(
                    f"[DUT] Waiting for link rate {expected:.0f} Mbps — "
                    f"Tx={primary.get('tx_rate')} Rx={primary.get('rx_rate')}"
                )
        time.sleep(poll_s)
    print(f"[WARN] Link rate did not stabilize at {expected:.0f} Mbps within {timeout_s:.0f}s")
    return False


async def _ensure_dut_ready(recovery_manager: RecoveryManager, dut_ip: str) -> None:
    if not await recovery_manager.is_gui_reachable(dut_ip):
        print("[RECOVERY] DUT GUI not reachable — running soft recovery before matrix...")
        await recovery_manager.run_soft_recovery()


def run_performance_matrix(args: argparse.Namespace) -> dict[str, object]:
    perf_defaults = PERFORMANCE_DEFAULTS
    trex_defaults = TRAFFIC_DEFAULTS["trex"]

    bandwidths = _parse_csv_list(args.bandwidths)
    mcs_rates = [mcs.upper() for mcs in _parse_csv_list(args.mcs)]
    bidir_ratios = _parse_csv_list(args.ratios)
    traffic_profiles = _traffic_profiles(bidir_ratios, include_directional=not args.ratios_only)
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
    recovery_manager = RecoveryManager(profile_bundle)
    dut = profile_bundle.active["dut"]
    dut_ip = _resolve_dut_ip(profile_bundle)
    dut_user = args.dut_user or dut["username"]
    dut_password = args.dut_password or dut["password"]

    output_dir, html_path, executed_at = _resolve_logs_paths(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    testbed_summary: dict[str, object] = {}

    total_iterations = len(bandwidths) * len(mcs_rates) * len(traffic_profiles)
    print("\n" + "=" * 72)
    print("UBR PERFORMANCE MATRIX")
    print("=" * 72)
    print(f"DUT IP:           {dut_ip}")
    print(f"TRex server:      {args.trex_server}")
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
                    )
                    spec = lookup_spec(mcs, bandwidth, spatial_streams=int(args.spatial_stream))
                    print(
                        f"[{iteration}/{total_iterations}] "
                        f"BW={bandwidth} MCS={mcs} Mode={profile['name']} Ratio={profile['ratio']} | "
                        f"sheet_rate={spec['operating_rate_mbps']} Mbps, "
                        f"effective={targets['effective_target_mbps']} Mbps, "
                        f"TRex DL={targets['trex_dl_bw']} UL={targets['trex_ul_bw']}"
                    )
        return {
            "dry_run": True,
            "iterations": total_iterations,
            "output_dir": str(output_dir),
            "html_report": str(html_path),
        }

    cpe_hosts = [str(ip) for ip in (dut.get("remote_ipv6s") or dut.get("remote_ips") or [])]
    try:
        testbed_summary = asyncio.run(
            collect_testbed_summary(dut_ip, cpe_hosts, dut_password)
        )
    except Exception as exc:
        print(f"[WARN] Testbed summary collection failed: {exc}")

    asyncio.run(_ensure_dut_ready(recovery_manager, dut_ip))

    records: list[dict[str, object]] = []
    iteration = 0
    started_at = datetime.now(timezone.utc).isoformat()

    for bandwidth in bandwidths:
        for mcs in mcs_rates:
            for profile in traffic_profiles:
                iteration += 1
                ratio = profile["ratio"]
                mode = profile["name"]
                print(
                    f"\n>>> Configuring: BTS bw/ratio/mcs + CPE mcs | "
                    f"bw={bandwidth}, mcs={mcs}, ratio={ratio}, cpe={cpe_hosts or 'none'}"
                )
                try:
                    configure_radio_profile(
                        dut_ip,
                        dut_user,
                        dut_password,
                        args.radio_index,
                        bandwidth,
                        mcs,
                        ratio,
                        args.spatial_stream,
                        cpe_hosts=cpe_hosts,
                        cpe_radio_idx=args.cpe_radio_index,
                        cpe_su_index=args.cpe_su_index,
                        prefer_cpe_via_bts=args.cpe_via_bts,
                        settle_s=args.radio_settle_s,
                    )
                    _wait_for_link_rate(
                        dut_ip,
                        bandwidth=bandwidth,
                        mcs=mcs,
                        snmp_community=args.snmp_community,
                        snmp_radio_index=args.snmp_radio_index,
                        spatial_stream=int(args.spatial_stream),
                        timeout_s=args.link_wait_s,
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
                )
                print(
                    f"\n--- [{iteration}/{total_iterations}] "
                    f"BW={bandwidth} MCS={mcs} Mode={mode} Ratio={ratio} ---"
                )
                spec = lookup_spec(mcs, bandwidth, spatial_streams=int(args.spatial_stream))
                print(
                    f"Targets: sheet_operating_rate={spec['operating_rate_mbps']} Mbps, "
                    f"effective={targets['effective_target_mbps']} Mbps "
                    f"(efficiency={targets['efficiency_factor']:.0%}), "
                    f"TRex DL={targets['trex_dl_bw']} UL={targets['trex_ul_bw']}"
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
                    "efficiency_factor": targets.get("efficiency_factor"),
                    "effective_target_mbps": effective_target,
                    "downlink_target_mbps": targets.get("downlink_mbps"),
                    "uplink_target_mbps": targets.get("uplink_mbps"),
                    "trex_dl_bw": dl_bw,
                    "trex_ul_bw": ul_bw,
                    "trex_direction": direction,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                artifact = output_dir / _artifact_name(bandwidth, mcs, mode, ratio)
                record["artifact"] = str(artifact)

                try:
                    trex_result = run_trex_stats_check(
                        trex_server=args.trex_server,
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
                        trex_su_count=args.su_count,
                        trex_dl_bw=dl_bw,
                        trex_ul_bw=ul_bw,
                        trex_packet_size=args.packet_size,
                        trex_direction=direction,
                        trex_protocol=args.trex_proto,
                        dut_host=dut_ip,
                        dut_user=dut_user,
                        dut_password=dut_password,
                        dut_radio_idx=args.radio_index,
                        deploy_client_script=args.deploy_client_script and iteration == 1,
                        reuse_existing_server=False,
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
                            "efficiency_factor": targets.get("efficiency_factor"),
                            "effective_target_mbps": effective_target,
                            "downlink_target_mbps": targets.get("downlink_mbps"),
                            "uplink_target_mbps": targets.get("uplink_mbps"),
                            "trex_dl_bw": dl_bw,
                            "trex_ul_bw": ul_bw,
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
                    clients = fetch_link_clients(
                        dut_ip,
                        snmp_community=args.snmp_community,
                        radio_idx=args.snmp_radio_index,
                    )
                    link_validation = validate_operating_rates(
                        bandwidth=bandwidth,
                        configured_mcs=mcs,
                        clients=clients,
                        spatial_streams=int(args.spatial_stream),
                        tolerance_mbps=args.rate_tolerance_mbps,
                        tolerance_pct=args.rate_tolerance_pct,
                    )
                    record["link_validation"] = link_validation
                    record["noise_dbm"] = args.noise_dbm
                    export["link_validation"] = link_validation
                    trex_passed = bool(validation.get("passed"))
                    rate_ok = bool(link_validation.get("operating_rate_ok"))
                    record["operating_rate_ok"] = rate_ok
                    record["passed"] = trex_passed and (rate_ok or not args.fail_on_rate_mismatch)
                    record["stats"] = export
                    record["finished_at"] = datetime.now(timezone.utc).isoformat()
                    with artifact.open("w", encoding="utf-8") as handle:
                        json.dump(export, handle, indent=2)
                    rate_note = "rate OK" if rate_ok else "RATE MISMATCH"
                    print(
                        f"Result: {'PASS' if record['passed'] else 'FAIL'} | "
                        f"combined_rx={export['combined'].get('rx_mbps', 0):.2f} Mbps | {rate_note}"
                    )
                    if link_validation.get("operating_rate_mismatch") and clients:
                        primary = clients[0]
                        print(
                            f"  Link rates: Tx={primary.get('tx_rate')} Rx={primary.get('rx_rate')} "
                            f"(expected {link_validation['expected_operating_rate_mbps']} Mbps)"
                        )
                except Exception as exc:
                    record["passed"] = False
                    err_text = str(exc)
                    record["error"] = err_text
                    record["stats"] = {}
                    record["noise_dbm"] = args.noise_dbm
                    try:
                        clients = fetch_link_clients(
                            dut_ip,
                            snmp_community=args.snmp_community,
                            radio_idx=args.snmp_radio_index,
                        )
                        record["link_validation"] = validate_operating_rates(
                            bandwidth=bandwidth,
                            configured_mcs=mcs,
                            clients=clients,
                            spatial_streams=int(args.spatial_stream),
                            tolerance_mbps=args.rate_tolerance_mbps,
                            tolerance_pct=args.rate_tolerance_pct,
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
    run_meta = {
        "executed_at": executed_at,
        "DUT IP": dut_ip,
        "TRex Server": args.trex_server,
        "Profile": args.profile,
        "Bandwidths": ", ".join(bandwidths),
        "MCS Rates": ", ".join(mcs_rates),
        "Ratios": ", ".join(bidir_ratios),
        "Target Mode": "dynamic" if args.use_dynamic_target else "static",
        "Efficiency Factor": str(args.efficiency),
        "Target Ceiling Mbps": str(args.target),
        "Duration (s)": str(args.time),
    }
    write_summary_csv(records, csv_path)
    write_html_report(
        records=records,
        run_meta=run_meta,
        path=html_path,
        testbed_summary=testbed_summary,
    )

    print("\n" + "=" * 72)
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
        default=",".join(perf["ratios"]),
        help="Comma-separated bidirectional DL:UL ratios; 100:0 and 0:100 are always included",
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
        help="Derive TRex DL/UL from PHY max(bandwidth,MCS) x efficiency (default: on)",
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
    parser.add_argument("--noise-dbm", default=perf.get("noise_dbm", "-93"),
                        help="Noise floor shown in report when not polled")
    parser.add_argument("--rate-tolerance-mbps", type=float, default=10.0,
                        help="Absolute Mbps tolerance for operating-rate match")
    parser.add_argument("--rate-tolerance-pct", type=float, default=0.08,
                        help="Relative tolerance for operating-rate match")
    parser.add_argument(
        "--fail-on-rate-mismatch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mark iteration failed when SNMP rate != spec sheet (default: true)",
    )
    parser.add_argument("--packet-size", type=int, default=perf["packet_size"])
    parser.add_argument("--expected-min-mbps", type=float, default=0.0)
    parser.add_argument("--pause-s", type=float, default=2.0, help="Pause between iterations")
    parser.add_argument("--radio-settle-s", type=float, default=6.0,
                        help="Wait after DUT radio apply (HT80/HT160 use at least 8s)")
    parser.add_argument("--link-wait-s", type=float, default=perf.get("link_wait_s", 45.0),
                        help="Poll SNMP until operating rate matches spec before TRex")
    parser.add_argument(
        "--output-dir",
        default=perf["artifact_dir"],
        help="Base directory under reports/artifacts/ for per-run artifacts",
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
    parser.add_argument("--trex-user", default=trex["user"])
    parser.add_argument("--trex-password", default=os.getenv("TREX_PASSWORD", trex["password"]))
    parser.add_argument("--trex-dir", default=trex["directory"])
    parser.add_argument("--trex-pythonpath", default=trex["pythonpath"])
    parser.add_argument("--trex-client-script", default=trex["client_script"])
    parser.add_argument("--trex-ports", default=trex["ports"])
    parser.add_argument("--trex-server-cores", type=int, default=trex["server_cores"])
    parser.add_argument("--trex-server-startup-s", type=int, default=trex.get("server_startup_s", 25))
    parser.add_argument("--trex-proto", choices=["udp", "tcp", "both"], default="udp")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = run_performance_matrix(args)
    if summary.get("dry_run"):
        return 0
    failed = int(summary.get("failed", 0))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
