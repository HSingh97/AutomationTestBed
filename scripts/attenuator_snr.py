#!/usr/bin/env python3
"""
Vaunix LDA-602 lab CLI: SNR sweep, live verify, parallel MIMO streams, SNMP monitor.

Requires libLDAhid.so (see vendor/vaunix_linux_sdk/) and sudo for USB HID.

Examples:
  PYTHONPATH=. python3 scripts/attenuator_snr.py sweep --start-db 0 --stop-db 40 --step-db 10
  sudo PYTHONPATH=. python3 scripts/attenuator_snr.py verify --steps-db 0,10,20,30,40,0
  sudo PYTHONPATH=. python3 scripts/attenuator_snr.py parallel --stream-att-db 40
  PYTHONPATH=. python3 scripts/attenuator_snr.py monitor --seconds 60
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from instruments.attenuator_snr import AttenuatorSnrController, SweepStepResult
from utils.profile_manager import load_profile_bundle


def _configure_ctrl(args: argparse.Namespace) -> AttenuatorSnrController:
    bundle = load_profile_bundle(profile_name=args.profile)
    ctrl = AttenuatorSnrController.from_profile(bundle.active)
    if getattr(args, "settle", None) is not None:
        ctrl.settle_seconds = args.settle
    backend = getattr(args, "backend", None)
    if backend:
        ctrl._api._backend = backend  # noqa: SLF001
    if getattr(args, "dll_path", None):
        ctrl._api._dll_path = Path(args.dll_path)  # noqa: SLF001
    return ctrl


def _open_hardware(ctrl: AttenuatorSnrController, args: argparse.Namespace) -> None:
    try:
        ctrl.open()
    except Exception as exc:
        backend = getattr(args, "backend", "dll")
        if backend == "dll" and not getattr(args, "allow_mock", False):
            print(f"\nERROR: Cannot open Vaunix hardware: {exc}")
            print("Build SDK: ./scripts/build_vaunix_linux_sdk.sh")
            print("Run with sudo for USB HID.")
            raise SystemExit(1) from exc
        raise
    if ctrl._api._backend == "mock" and getattr(args, "backend", None) == "dll":  # noqa: SLF001
        if not getattr(args, "allow_mock", False):
            print("\nERROR: Fell back to mock backend; install libLDAhid.so and use sudo.")
            raise SystemExit(1)


def _print_devices(ctrl: AttenuatorSnrController) -> None:
    for dev in ctrl._api.list_devices():
        print(f"  Device id={dev.device_id} serial={dev.serial} model={dev.model}")


def _print_snr(label: str, snr, att_label: str) -> None:
    f = snr.as_floats()
    print(
        f"  {label}: att=({att_label}) | "
        f"L-SNR ch0={f.get('l_snr1')} ch1={f.get('l_snr2')} | "
        f"R-SNR ch0={f.get('r_snr1')} ch1={f.get('r_snr2')} | "
        f"Tx={snr.tx_rate} Rx={snr.rx_rate}"
    )


def _set_both_chains_parallel(ctrl: AttenuatorSnrController, att0: float, att1: float) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        f0 = pool.submit(ctrl.set_chain, 0, att0)
        f1 = pool.submit(ctrl.set_chain, 1, att1)
        f0.result()
        f1.result()
    ctrl._wait_settle()


def cmd_sweep(args: argparse.Namespace) -> int:
    ctrl = _configure_ctrl(args)
    with ctrl:
        if args.chain0_db is not None or args.chain1_db is not None:
            snr = ctrl.set_link(
                channel=args.channel,
                frequency_mhz=args.frequency_mhz,
                att_chain0_db=args.chain0_db if args.chain0_db is not None else 0.0,
                att_chain1_db=args.chain1_db if args.chain1_db is not None else 0.0,
            )
            print(f"SNR after set: {snr}")
            return 0
        results = ctrl.sweep_attenuation(
            channel=args.channel,
            frequency_mhz=args.frequency_mhz,
            start_db=args.start_db,
            stop_db=args.stop_db,
            step_db=args.step_db,
        )
        out = AttenuatorSnrController.write_csv(args.output_csv, results)
        print(f"Wrote {out} ({len(results)} steps)")
        if args.assert_snr_drop:
            ctrl.assert_snr_decreases_with_attenuation(results)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    steps = [float(x.strip()) for x in args.steps_db.split(",") if x.strip()]
    ctrl = _configure_ctrl(args)
    print("=== Vaunix LDA + SNR verify ===")
    print(f"BTS: {ctrl.dut_ip} | SNMP radio_idx={ctrl.snmp_radio_idx}")
    print(f"steps (dB): {steps} | settle: {args.settle}s")
    _open_hardware(ctrl, args)
    _print_devices(ctrl)
    rows = []
    for att in steps:
        att_label = f"{att}/{att}"
        snr = ctrl.set_link(
            channel=args.channel,
            att_chain0_db=att,
            att_chain1_db=att,
            settle=True,
        )
        rows.append((att, snr))
        _print_snr(f"Step {att} dB", snr, att_label)
    print("\n=== Summary ===")
    print(f"{'Att (dB)':>8} | {'L-ch0':>6} {'L-ch1':>6} | {'R-ch0':>6} {'R-ch1':>6}")
    for att, snr in rows:
        f = snr.as_floats()
        print(
            f"{att:8.1f} | {f.get('l_snr1')!s:>6} {f.get('l_snr2')!s:>6} | "
            f"{f.get('r_snr1')!s:>6} {f.get('r_snr2')!s:>6}"
        )
    if len(rows) >= 2 and rows[-1][0] > rows[0][0]:
        sweep_results = [
            SweepStepResult(
                step_index=i,
                channel=args.channel,
                frequency_mhz=args.frequency_mhz,
                att_chain0_db=a,
                att_chain1_db=a,
                snr=s,
                elapsed_s=0,
            )
            for i, (a, s) in enumerate(rows)
        ]
        try:
            ctrl.assert_snr_decreases_with_attenuation(sweep_results, min_drop_db=0.5, metric="r_snr1")
        except AssertionError as exc:
            print(f"\nNote: {exc}")
    ctrl.close()
    return 0


def cmd_parallel(args: argparse.Namespace) -> int:
    ctrl = _configure_ctrl(args)
    print("=== Vaunix LDA + SNR parallel streams ===")
    print(f"BTS: {ctrl.dut_ip} | SNMP radio_idx={ctrl.snmp_radio_idx}")
    _open_hardware(ctrl, args)
    _print_devices(ctrl)
    matrix = [
        ("baseline", 0.0, 0.0),
        ("stream0 only", args.stream_att_db, 0.0),
        ("stream1 only", 0.0, args.stream_att_db),
        ("both streams", args.stream_att_db, args.stream_att_db),
        ("restore", 0.0, 0.0),
    ]
    rows: list[tuple] = []
    print(f"\n=== Parallel streams (@ {args.stream_att_db} dB isolation) ===")
    print(f"{'Case':<14} | {'Att0':>5} {'Att1':>5} | {'L0':>5} {'L1':>5} | {'R0':>5} {'R1':>5}")
    for name, a0, a1 in matrix:
        _set_both_chains_parallel(ctrl, a0, a1)
        snr = ctrl.read_snr()
        f = snr.as_floats()
        rows.append((name, a0, a1, snr))
        print(
            f"{name:<14} | {a0:5.1f} {a1:5.1f} | "
            f"{f.get('l_snr1')!s:>5} {f.get('l_snr2')!s:>5} | "
            f"{f.get('r_snr1')!s:>5} {f.get('r_snr2')!s:>5}"
        )
    base = rows[0][3].as_floats()
    print("\n=== Stream isolation (remote SNR delta vs baseline) ===")
    for label, row in (
        ("stream0 @ att", rows[1][3]),
        ("stream1 @ att", rows[2][3]),
        ("both @ att", rows[3][3]),
    ):
        cur = row.as_floats()
        d0 = (base.get("r_snr1") or 0) - (cur.get("r_snr1") or 0)
        d1 = (base.get("r_snr2") or 0) - (cur.get("r_snr2") or 0)
        print(f"  {label}: R0 delta={d0:+.1f} dB  R1 delta={d1:+.1f} dB")
    ctrl.close()
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    ctrl = _configure_ctrl(args)
    print(f"=== SNMP SNR monitor ({args.seconds}s) ===")
    print(f"BTS: {ctrl.dut_ip} | radio_idx={ctrl.snmp_radio_idx}")
    interval = max(1.0, min(args.settle, 5.0))
    print(f"{'t':>6} | {'L0':>5} {'L1':>5} | {'R0':>5} {'R1':>5} | Tx/Rx")
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        snr = ctrl.read_snr()
        f = snr.as_floats()
        elapsed = time.monotonic() - t0
        print(
            f"{elapsed:6.1f} | {f.get('l_snr1')!s:>5} {f.get('l_snr2')!s:>5} | "
            f"{f.get('r_snr1')!s:>5} {f.get('r_snr2')!s:>5} | {snr.tx_rate}/{snr.rx_rate}"
        )
        time.sleep(interval)
    return 0


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default="default")
    parser.add_argument("--backend", default=None, help="auto | dll | mock")
    parser.add_argument("--dll-path", default=None)
    parser.add_argument("--settle", type=float, default=5.0)
    parser.add_argument("--allow-mock", action="store_true")


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common)

    parser = argparse.ArgumentParser(description="Vaunix LDA-602 + SNMP SNR lab CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sweep = sub.add_parser("sweep", help="Step attenuation and write CSV", parents=[common])
    p_sweep.add_argument("--channel", type=int, default=None)
    p_sweep.add_argument("--frequency-mhz", type=float, default=None)
    p_sweep.add_argument("--start-db", type=float, default=0.0)
    p_sweep.add_argument("--stop-db", type=float, default=40.0)
    p_sweep.add_argument("--step-db", type=float, default=10.0)
    p_sweep.add_argument("--chain0-db", type=float, default=None)
    p_sweep.add_argument("--chain1-db", type=float, default=None)
    p_sweep.add_argument("--output-csv", default="logs/attenuator_snr_sweep.csv")
    p_sweep.add_argument("--assert-snr-drop", action="store_true")

    p_verify = sub.add_parser("verify", help="Live step list with SNR table", parents=[common])
    p_verify.add_argument("--channel", type=int, default=None)
    p_verify.add_argument("--frequency-mhz", type=float, default=None)
    p_verify.add_argument("--steps-db", default="0,10,20,30,40,20,0")

    p_parallel = sub.add_parser("parallel", help="Per-MIMO-stream isolation test", parents=[common])
    p_parallel.add_argument("--stream-att-db", type=float, default=40.0)

    p_monitor = sub.add_parser("monitor", help="SNMP-only SNR polling", parents=[common])
    p_monitor.add_argument("--seconds", type=float, default=60.0)

    args = parser.parse_args()
    if args.command in ("verify", "parallel") and args.backend is None:
        args.backend = "dll"
    handlers = {
        "sweep": cmd_sweep,
        "verify": cmd_verify,
        "parallel": cmd_parallel,
        "monitor": cmd_monitor,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
