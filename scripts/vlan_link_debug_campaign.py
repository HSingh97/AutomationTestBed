#!/usr/bin/env python3
"""
Automated QinQ vs transparent VLAN link-recovery campaign.

For each VLAN mode (qinq, transparent):
  1. Apply BTS VLAN (QinQ double-tag or transparent) + CPE untagged
  2. Run performance matrix N times with extended timeouts and link debug snapshots
  3. Archive console output, matrix summary, and per-attempt JSON under reports/link_debug_campaign/

Example (on Jenkins agent / lab PC):
  PYTHONPATH=. python3 scripts/vlan_link_debug_campaign.py \\
    --stand test-qa-lab-02 \\
    --iterations 3 \\
    --bandwidths HT20,HT40,HT80 \\
    --mcs MCS14
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from traffic.link_debug_snapshot import write_link_debug_snapshot
from utils.bench_config import profile_for_stand
from utils.profile_manager import load_profile_bundle
from utils.vlan_mode_apply import apply_vlan_mode


def _utc_dirname() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _run_matrix(
    *,
    stand: str,
    profile: str,
    bandwidths: str,
    mcs: str,
    iteration_dir: Path,
    su_link_wait_s: float,
    bandwidth_apply_wait_s: float,
    bandwidth_running_wait_s: float,
    trex_qinq: bool,
    duration_s: int,
    su_count: int,
) -> dict[str, object]:
    iteration_dir.mkdir(parents=True, exist_ok=True)
    link_debug = iteration_dir / "link_wait"
    matrix_out = iteration_dir / "matrix_artifacts"
    matrix_out.mkdir(parents=True, exist_ok=True)
    log_path = iteration_dir / "matrix_console.log"

    cmd = [
        sys.executable,
        str(REPO / "traffic" / "performance_matrix.py"),
        "--bandwidths",
        bandwidths,
        "--mcs",
        mcs,
        "--ratios",
        "75:25",
        "--ratios-only",
        "--no-fail-on-rate-mismatch",
        "--time",
        str(duration_s),
        "--packet-size",
        "1500",
        "--deploy-client-script",
        "--stand",
        stand,
        "--profile",
        profile,
        "--su-count",
        str(su_count),
        "--su-link-wait-s",
        str(su_link_wait_s),
        "--bandwidth-apply-wait-s",
        str(bandwidth_apply_wait_s),
        "--bandwidth-running-wait-s",
        str(bandwidth_running_wait_s),
        "--link-debug-dir",
        str(link_debug),
        "--output-dir",
        str(matrix_out),
    ]
    if not trex_qinq:
        cmd.append("--no-qinq")

    print(f"[campaign] Matrix command: {' '.join(cmd)}")
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO),
            env={**os.environ, "PYTHONPATH": str(REPO)},
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            check=False,
        )

    summary_json = None
    for candidate in sorted(matrix_out.glob("performance_matrix_summary.json"), reverse=True):
        summary_json = candidate
        break
    if summary_json is None:
        for candidate in sorted((REPO / "reports" / "artifacts").glob("performance_*/performance_matrix_summary.json"), reverse=True):
            shutil.copy2(candidate, iteration_dir / "performance_matrix_summary.json")
            summary_json = iteration_dir / "performance_matrix_summary.json"
            break
    elif summary_json.exists():
        shutil.copy2(summary_json, iteration_dir / "performance_matrix_summary.json")

    report_html = sorted(matrix_out.glob("Performance_Report_*.html"), reverse=True)
    if report_html:
        shutil.copy2(report_html[0], iteration_dir / report_html[0].name)

    records: list[dict] = []
    summary_path = iteration_dir / "performance_matrix_summary.json"
    if summary_path.exists():
        raw = json.loads(summary_path.read_text(encoding="utf-8"))
        records = raw if isinstance(raw, list) else raw.get("records", [])

    return {
        "exit_code": proc.returncode,
        "log_path": str(log_path),
        "summary_path": str(summary_path) if summary_path.exists() else "",
        "passed": sum(1 for r in records if r.get("passed")),
        "total": len(records),
        "records": [
            {
                "bandwidth": r.get("bandwidth"),
                "mcs": r.get("mcs"),
                "passed": r.get("passed"),
                "error": r.get("error"),
                "rx_mbps": (r.get("stats") or {}).get("combined", {}).get("rx_mbps"),
            }
            for r in records
        ],
    }


def _run_mode_phase(
    *,
    mode_name: str,
    bts_vlan_mode: str,
    trex_qinq: bool,
    args: argparse.Namespace,
    campaign_root: Path,
    profile_bundle,
) -> dict[str, object]:
    mode_dir = campaign_root / mode_name
    mode_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'=' * 60}\n[campaign] VLAN phase: {mode_name.upper()} (BTS={bts_vlan_mode}, TRex QinQ={trex_qinq})\n{'=' * 60}")

    vlan_result = apply_vlan_mode(
        profile_active=profile_bundle.active,
        bts_vlan_mode=bts_vlan_mode,
        su_count=args.su_count,
    )
    (mode_dir / "vlan_apply.json").write_text(json.dumps(vlan_result, indent=2), encoding="utf-8")

    dut = profile_bundle.active.get("dut") or {}
    tb = profile_bundle.active.get("testbed") or {}
    bts_ip = str(dut.get("ssh_host") or "10.0.0.1")
    user = str(dut.get("username") or "root")
    password = str(dut.get("password") or "")
    cpe_hosts = list(dut.get("remote_ipv6s") or [])

    write_link_debug_snapshot(
        mode_dir / "post_vlan_apply",
        label="post_vlan_apply",
        bts_ip=bts_ip,
        bts_user=user,
        bts_password=password,
        profile_tb=tb,
        dut_cfg=dut,
        cpe_hosts=cpe_hosts,
        phase=f"after vlan apply ({mode_name})",
    )

    if args.settle_after_vlan_s > 0:
        print(f"[campaign] Settling {args.settle_after_vlan_s:.0f}s after VLAN apply...")
        time.sleep(args.settle_after_vlan_s)

    iteration_results: list[dict[str, object]] = []
    for idx in range(1, args.iterations + 1):
        iter_dir = mode_dir / f"iter_{idx:02d}"
        print(f"\n[campaign] {mode_name} iteration {idx}/{args.iterations}")
        result = _run_matrix(
            stand=args.stand,
            profile=args.profile,
            bandwidths=args.bandwidths,
            mcs=args.mcs,
            iteration_dir=iter_dir,
            su_link_wait_s=args.su_link_wait_s,
            bandwidth_apply_wait_s=args.bandwidth_apply_wait_s,
            bandwidth_running_wait_s=args.bandwidth_running_wait_s,
            trex_qinq=trex_qinq,
            duration_s=args.duration_s,
            su_count=args.su_count,
        )
        result["iteration"] = idx
        iteration_results.append(result)
        print(
            f"[campaign] {mode_name} iter {idx}: exit={result['exit_code']} "
            f"passed={result['passed']}/{result['total']}"
        )
        if args.pause_between_iterations_s > 0 and idx < args.iterations:
            time.sleep(args.pause_between_iterations_s)

    phase_summary = {
        "mode": mode_name,
        "bts_vlan_mode": bts_vlan_mode,
        "trex_qinq": trex_qinq,
        "vlan_apply": vlan_result,
        "iterations": iteration_results,
        "timeouts": {
            "su_link_wait_s": args.su_link_wait_s,
            "bandwidth_apply_wait_s": args.bandwidth_apply_wait_s,
            "bandwidth_running_wait_s": args.bandwidth_running_wait_s,
        },
    }
    (mode_dir / "phase_summary.json").write_text(json.dumps(phase_summary, indent=2), encoding="utf-8")
    return phase_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QinQ vs transparent VLAN link debug campaign")
    parser.add_argument("--stand", default="test-qa-lab-02")
    parser.add_argument("--profile", default="", help="Override profile (default: from benches.yaml)")
    parser.add_argument("--bandwidths", default="HT20,HT40,HT80")
    parser.add_argument("--mcs", default="MCS14")
    parser.add_argument("--iterations", type=int, default=3, help="Matrix runs per VLAN mode")
    parser.add_argument("--duration-s", type=int, default=30)
    parser.add_argument("--su-count", type=int, default=4)
    parser.add_argument("--su-link-wait-s", type=float, default=240.0)
    parser.add_argument("--bandwidth-apply-wait-s", type=float, default=90.0)
    parser.add_argument("--bandwidth-running-wait-s", type=float, default=180.0)
    parser.add_argument("--settle-after-vlan-s", type=float, default=30.0)
    parser.add_argument("--pause-between-iterations-s", type=float, default=15.0)
    parser.add_argument(
        "--output-root",
        default=str(REPO / "reports" / "link_debug_campaign"),
        help="Campaign artifact root",
    )
    parser.add_argument(
        "--modes",
        default="qinq,transparent",
        help="Comma-separated phases: qinq, transparent",
    )
    parser.add_argument("--skip-qinq", action="store_true", help="Skip QinQ phase")
    parser.add_argument("--skip-transparent", action="store_true", help="Skip transparent phase")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile_name = args.profile.strip() or profile_for_stand(args.stand)
    bundle = load_profile_bundle(profile_name=profile_name)
    args.profile = profile_name

    campaign_root = Path(args.output_root) / _utc_dirname()
    campaign_root.mkdir(parents=True, exist_ok=True)
    print(f"[campaign] Artifacts: {campaign_root}")

    meta = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stand": args.stand,
        "profile": profile_name,
        "bandwidths": args.bandwidths,
        "mcs": args.mcs,
        "iterations_per_mode": args.iterations,
    }
    (campaign_root / "campaign_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    phases: list[tuple[str, str, bool]] = []
    requested = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    if "qinq" in requested and not args.skip_qinq:
        phases.append(("qinq", "qinq", True))
    if "transparent" in requested and not args.skip_transparent:
        phases.append(("transparent", "transparent", False))

    all_phases: list[dict[str, object]] = []
    for mode_name, bts_mode, trex_qinq in phases:
        all_phases.append(
            _run_mode_phase(
                mode_name=mode_name,
                bts_vlan_mode=bts_mode,
                trex_qinq=trex_qinq,
                args=args,
                campaign_root=campaign_root,
                profile_bundle=bundle,
            )
        )

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["phases"] = all_phases
    (campaign_root / "campaign_summary.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\n[campaign] Complete — summary: {campaign_root / 'campaign_summary.json'}")

    any_fail = False
    for phase in all_phases:
        for it in phase.get("iterations", []):
            if int(it.get("exit_code", 0)) != 0:
                any_fail = True
            elif int(it.get("passed", 0)) < int(it.get("total", 0)):
                any_fail = True
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
