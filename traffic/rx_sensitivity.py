"""Conducted Rx sensitivity sweep for a UBR point-to-point link.

The receiver under test is the PtP slave. Attenuation is increased until UDP
packet error rate exceeds the limit (10% for 802.11-based UBR). Sensitivity is
the slave RSSI at the last step that still met the limit.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from traffic.operating_rate_table import modulation_scheme, operating_rate_mbps


@dataclass
class RxSensitivityConfig:
    """Sweep limits. Attenuation is the programmable pad, not the fixed path loss."""

    offered_mbps: float
    phy_rate_mbps: float
    start_db: float = 0.0
    stop_db: float = 63.0
    step_db: float = 1.0
    coarse_step_db: float = 2.0
    seek_rssi_dbm: float | None = -40.0
    per_limit_pct: float = 10.0
    min_packets: int = 100
    confirm_fail: bool = True
    duration_s: float = 15.0
    baseline_loss_db: float = 0.0
    # Loss at a strong RSSI with low goodput is the traffic generator, not the radio.
    generator_rssi_ceiling_dbm: float = -50.0
    generator_ratio: float = 0.75
    rssi_stuck_steps: int = 3
    bandwidth: str = ""
    mcs: str = ""
    direction: str = "downlink"
    channel: int | None = None
    load_fraction: float = 0.60

    def validate(self) -> None:
        if self.step_db <= 0 or self.coarse_step_db <= 0:
            raise ValueError("Attenuation steps must be positive")
        if self.stop_db < self.start_db:
            raise ValueError("stop_db must be >= start_db")
        if self.duration_s < 1:
            raise ValueError("duration_s must be at least 1 second")
        if not 0 < self.per_limit_pct <= 100:
            raise ValueError("per_limit_pct must be in (0, 100]")
        if self.offered_mbps <= 0:
            raise ValueError("offered_mbps must be positive")
        if self.min_packets < 1:
            raise ValueError("min_packets must be at least 1")


@dataclass
class ReceiverSnapshot:
    rssi_dbm: float | None = None
    snr_db: float | None = None
    tx_mcs: str = ""
    rx_mcs: str = ""


@dataclass
class IperfSample:
    link_up: bool
    per_pct: float | None = None
    lost_packets: int = 0
    total_packets: int = 0
    throughput_mbps: float | None = None
    note: str = ""


@dataclass
class RxStep:
    step_index: int
    phase: str
    att_db: float
    baseline_loss_db: float
    total_loss_db: float
    rssi_dbm: float | None
    rssi_before_dbm: float | None
    rssi_after_dbm: float | None
    snr_db: float | None
    per_pct: float | None
    lost_packets: int
    total_packets: int
    throughput_mbps: float | None
    offered_mbps: float
    link_up: bool
    passed: bool
    use_for_threshold: bool
    repeat: int = 0
    tx_mcs: str = ""
    rx_mcs: str = ""
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key, value in list(row.items()):
            if value is None:
                row[key] = ""
        return row


@dataclass
class SensitivityResult:
    status: str
    sensitivity_rssi_dbm: float | None = None
    sensitivity_att_db: float | None = None
    sensitivity_per_pct: float | None = None
    sensitivity_snr_db: float | None = None
    cliff_rssi_dbm: float | None = None
    cliff_att_db: float | None = None
    cliff_per_pct: float | None = None
    note: str = ""


@dataclass
class RxSensitivityReport:
    result: SensitivityResult
    steps: list[RxStep] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": asdict(self.result),
            "config": self.config,
            "steps": [asdict(step) for step in self.steps],
        }


SetAttenuation = Callable[[float], None]
ReadReceiver = Callable[[], ReceiverSnapshot]
RunTraffic = Callable[[], tuple[IperfSample, ReceiverSnapshot, ReceiverSnapshot]]


def offered_udp_mbps(
    bandwidth: str,
    mcs: str,
    *,
    spatial_streams: int = 2,
    load_fraction: float = 0.60,
    max_mbps: float | None = None,
) -> tuple[float, float]:
    """Return (offered UDP Mbps, PHY operating rate Mbps).

    The offered load is the middle of the 50–70% method window unless
    ``load_fraction`` or ``max_mbps`` overrides it.
    """
    if not 0 < load_fraction <= 1:
        raise ValueError("load_fraction must be in (0, 1]")
    phy = float(operating_rate_mbps(bandwidth, mcs, spatial_streams=spatial_streams))
    offered = phy * float(load_fraction)
    if max_mbps is not None:
        offered = min(offered, float(max_mbps))
    if offered <= 0:
        raise ValueError("offered UDP rate must be positive")
    return round(offered, 2), phy


def median_dbm(values: list[float | None]) -> float | None:
    nums = sorted(value for value in values if value is not None)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return float(nums[mid])
    return (float(nums[mid - 1]) + float(nums[mid])) / 2.0


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "")
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _per_and_total(block: dict[str, Any]) -> tuple[float | None, int, int]:
    """Return (PER %, lost packets, total packets) from one iperf3 sum object.

    iperf3 has used ``packets`` both as packets-sent and as packets-received.
    ``lost_percent`` is trusted, and the packet total is the denominator that
    matches that percentage.
    """
    received = int(block.get("packets") or 0)
    has_lost = block.get("lost_packets") is not None
    lost = int(block.get("lost_packets") or 0)
    pct_raw = block.get("lost_percent")
    as_total = received
    as_received = received + lost
    if pct_raw is not None and has_lost:
        pct = float(pct_raw)
        candidates: list[tuple[float, int]] = []
        if as_received > 0:
            candidates.append((abs(pct - (100.0 * lost / as_received)), as_received))
        if as_total > 0 and as_total != as_received:
            candidates.append((abs(pct - (100.0 * lost / as_total)), as_total))
        if candidates:
            _delta, total = min(candidates)
            return pct, lost, total
        return pct, lost, max(as_total, as_received)
    if has_lost and as_received > 0:
        return 100.0 * lost / as_received, lost, as_received
    if received > 0 and pct_raw is not None:
        return float(pct_raw), lost, received
    return None, lost, received


def _loss_block(end: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("sum_received", "sum", "streams"):
        block = end.get(key)
        if isinstance(block, dict) and (
            "lost_packets" in block or "lost_percent" in block or "packets" in block
        ):
            return block
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict) and (
                    "lost_packets" in item or "lost_percent" in item
                ):
                    return item
    if "lost_packets" in end or "lost_percent" in end:
        return end
    return None


def parse_iperf3_udp(text: str) -> IperfSample:
    """Parse ``iperf3 -J`` UDP output into packet loss and goodput."""
    payload = _extract_json(text)
    if payload is None:
        snippet = " ".join(str(text or "").split())
        return IperfSample(link_up=False, note=snippet[-240:] or "no iperf3 output")
    error = payload.get("error")
    if error:
        return IperfSample(link_up=False, note=str(error))
    end = payload.get("end")
    if not isinstance(end, dict):
        return IperfSample(link_up=False, note="iperf3 JSON has no end summary")
    block = _loss_block(end)
    if block is None:
        return IperfSample(link_up=False, note="iperf3 JSON has no UDP loss counters")
    per_pct, lost, total = _per_and_total(block)
    bps = block.get("bits_per_second")
    if bps is None:
        sent = end.get("sum_sent")
        if isinstance(sent, dict):
            bps = sent.get("bits_per_second")
    throughput = float(bps) / 1_000_000.0 if bps is not None else None
    link_up = per_pct is not None or total > 0
    return IperfSample(
        link_up=link_up,
        per_pct=per_pct,
        lost_packets=lost,
        total_packets=total,
        throughput_mbps=None if throughput is None else round(throughput, 3),
        note="",
    )


def _is_pass(config: RxSensitivityConfig, sample: IperfSample) -> bool:
    return bool(
        sample.link_up
        and sample.per_pct is not None
        and sample.total_packets >= config.min_packets
        and sample.per_pct <= config.per_limit_pct
    )


def _is_fail(config: RxSensitivityConfig, sample: IperfSample) -> bool:
    if not sample.link_up:
        return True
    if sample.per_pct is None or sample.total_packets < config.min_packets:
        return False
    return sample.per_pct > config.per_limit_pct


def _is_inconclusive(config: RxSensitivityConfig, sample: IperfSample) -> bool:
    return sample.link_up and (
        sample.per_pct is None or sample.total_packets < config.min_packets
    )


def _generator_limited(config: RxSensitivityConfig, sample: IperfSample, rssi: float | None) -> bool:
    if not sample.link_up or sample.throughput_mbps is None or rssi is None:
        return False
    if rssi <= config.generator_rssi_ceiling_dbm:
        return False
    return sample.throughput_mbps < config.offered_mbps * config.generator_ratio


def _make_step(
    *,
    index: int,
    phase: str,
    att_db: float,
    config: RxSensitivityConfig,
    snap_rssi: float | None,
    snap_before: float | None,
    snap_after: float | None,
    snr: float | None,
    sample: IperfSample | None,
    passed: bool,
    use_for_threshold: bool,
    repeat: int,
    tx_mcs: str,
    rx_mcs: str,
    note: str,
) -> RxStep:
    traffic = sample or IperfSample(link_up=snap_rssi is not None)
    return RxStep(
        step_index=index,
        phase=phase,
        att_db=round(float(att_db), 2),
        baseline_loss_db=config.baseline_loss_db,
        total_loss_db=round(config.baseline_loss_db + float(att_db), 2),
        rssi_dbm=snap_rssi,
        rssi_before_dbm=snap_before,
        rssi_after_dbm=snap_after,
        snr_db=snr,
        per_pct=None if traffic.per_pct is None else round(float(traffic.per_pct), 3),
        lost_packets=traffic.lost_packets,
        total_packets=traffic.total_packets,
        throughput_mbps=traffic.throughput_mbps,
        offered_mbps=config.offered_mbps,
        link_up=traffic.link_up if sample is not None else snap_rssi is not None,
        passed=passed,
        use_for_threshold=use_for_threshold,
        repeat=repeat,
        tx_mcs=tx_mcs,
        rx_mcs=rx_mcs,
        note=note,
    )


def _advance(att_db: float, step_db: float, stop_db: float) -> float | None:
    nxt = round(att_db + step_db, 2)
    if nxt > stop_db + 1e-9:
        return None
    return nxt


def sweep_rx_sensitivity(
    config: RxSensitivityConfig,
    *,
    set_attenuation: SetAttenuation,
    read_receiver: ReadReceiver,
    run_traffic: RunTraffic,
) -> RxSensitivityReport:
    """Step the conducted pad and stop at the first confirmed PER failure.

    Seek uses RSSI only, in ``coarse_step_db`` moves, until the slave is at or
    below ``seek_rssi_dbm``. The PER dwell then uses ``step_db`` (1 dB).
    """
    config.validate()
    steps: list[RxStep] = []
    att = round(float(config.start_db), 2)
    stop = round(float(config.stop_db), 2)
    result = SensitivityResult(status="cliff_not_reached")

    def _finish() -> RxSensitivityReport:
        return RxSensitivityReport(result=result, steps=steps, config=asdict(config))

    if config.seek_rssi_dbm is not None:
        stagnant = 0
        previous: float | None = None
        while True:
            set_attenuation(att)
            snap = read_receiver()
            steps.append(
                _make_step(
                    index=len(steps),
                    phase="seek",
                    att_db=att,
                    config=config,
                    snap_rssi=snap.rssi_dbm,
                    snap_before=snap.rssi_dbm,
                    snap_after=None,
                    snr=snap.snr_db,
                    sample=None,
                    passed=False,
                    use_for_threshold=False,
                    repeat=0,
                    tx_mcs=snap.tx_mcs,
                    rx_mcs=snap.rx_mcs,
                    note="seek",
                )
            )
            if snap.rssi_dbm is None:
                result.status = "no_rssi"
                result.note = "Slave RSSI was not reported while seeking the start level."
                return _finish()
            if snap.rssi_dbm <= config.seek_rssi_dbm:
                break
            if previous is not None and (previous - snap.rssi_dbm) < 0.5:
                stagnant += 1
                if stagnant >= config.rssi_stuck_steps:
                    result.status = "rssi_not_tracking"
                    result.note = (
                        "RSSI did not fall as attenuation increased. "
                        "The programmable attenuator is not in the conducted path on every MIMO chain."
                    )
                    return _finish()
            else:
                stagnant = 0
            previous = snap.rssi_dbm
            nxt = _advance(att, config.coarse_step_db, stop)
            if nxt is None:
                break
            att = nxt

    last_pass: RxStep | None = None
    cliff: RxStep | None = None
    while att <= stop + 1e-9:
        set_attenuation(att)
        sample, before, after = run_traffic()
        rssi = median_dbm([before.rssi_dbm, after.rssi_dbm])
        snr = median_dbm([before.snr_db, after.snr_db])
        tx_mcs = after.tx_mcs or before.tx_mcs
        rx_mcs = after.rx_mcs or before.rx_mcs

        def _record(current: IperfSample, repeat: int, *, threshold: bool, passed: bool, note: str) -> RxStep:
            row = _make_step(
                index=len(steps),
                phase="fine",
                att_db=att,
                config=config,
                snap_rssi=rssi,
                snap_before=before.rssi_dbm,
                snap_after=after.rssi_dbm,
                snr=snr,
                sample=current,
                passed=passed,
                use_for_threshold=threshold,
                repeat=repeat,
                tx_mcs=tx_mcs,
                rx_mcs=rx_mcs,
                note=note or current.note,
            )
            steps.append(row)
            return row

        if _generator_limited(config, sample, rssi):
            _record(sample, 0, threshold=False, passed=False, note="generator_limited")
            result.status = "generator_limited"
            result.note = (
                f"At {rssi:.1f} dBm the UDP flow delivered {sample.throughput_mbps:.1f} Mbps "
                f"of {config.offered_mbps:.1f} Mbps offered. "
                "Lower --udp-mbps, or run iperf from LAN hosts instead of the radios."
            )
            return _finish()

        if _is_inconclusive(config, sample):
            retry, _, _ = run_traffic()
            if _is_inconclusive(config, retry):
                _record(retry, 1, threshold=False, passed=False, note=retry.note or "inconclusive")
                result.status = "no_measurement"
                result.note = retry.note or "UDP measurement did not return enough packets."
                return _finish()
            sample = retry

        if _is_fail(config, sample) and config.confirm_fail:
            _record(sample, 0, threshold=False, passed=False, note="confirming PER")
            sample, before2, after2 = run_traffic()
            rssi = median_dbm([before.rssi_dbm, after.rssi_dbm, before2.rssi_dbm, after2.rssi_dbm])
            snr = median_dbm([before.snr_db, after.snr_db, before2.snr_db, after2.snr_db])
            before, after = before2, after2

        passed = _is_pass(config, sample)
        failed = _is_fail(config, sample)
        row = _record(
            sample,
            1 if config.confirm_fail and failed else 0,
            threshold=True,
            passed=passed,
            note="" if passed or failed else (sample.note or "inconclusive"),
        )
        if failed:
            cliff = row
            if last_pass is None:
                result.status = "failed_at_first_step"
                result.note = (
                    "PER exceeded the limit on the first 1 dB step. "
                    "Lower the start attenuation so the sweep begins on a strong signal."
                )
            else:
                result.status = "sensitivity_found"
                result.sensitivity_rssi_dbm = last_pass.rssi_dbm
                result.sensitivity_att_db = last_pass.att_db
                result.sensitivity_per_pct = last_pass.per_pct
                result.sensitivity_snr_db = last_pass.snr_db
                result.note = (
                    f"Rx sensitivity is {last_pass.rssi_dbm} dBm "
                    f"({last_pass.att_db:.1f} dB pad, PER {last_pass.per_pct}%). "
                    f"The next step exceeded {config.per_limit_pct:.0f}% PER."
                )
            result.cliff_rssi_dbm = row.rssi_dbm
            result.cliff_att_db = row.att_db
            result.cliff_per_pct = row.per_pct
            return _finish()
        if not passed:
            result.status = "no_measurement"
            result.note = sample.note or "UDP measurement did not return enough packets."
            return _finish()
        if row.rssi_dbm is None:
            result.status = "no_rssi"
            result.note = "Traffic passed but the slave RSSI was not reported."
            return _finish()
        last_pass = row
        nxt = _advance(att, config.step_db, stop)
        if nxt is None:
            result.status = "cliff_not_reached"
            result.note = (
                f"PER stayed at or below {config.per_limit_pct:.0f}% through {att:.1f} dB. "
                "Increase --stop-db or add fixed attenuation."
            )
            return _finish()
        att = nxt

    if cliff is None and last_pass is not None and result.status == "cliff_not_reached":
        result.note = (
            f"PER stayed at or below {config.per_limit_pct:.0f}% through {last_pass.att_db:.1f} dB. "
            "Increase --stop-db or add fixed attenuation."
        )
    return _finish()


def format_summary(report: RxSensitivityReport) -> str:
    """Plain-text table of the sweep and the sensitivity call."""
    cfg = report.config
    direction = str(cfg.get("direction") or "downlink")
    receiver = "CPE (slave)" if direction == "downlink" else "BTS (master)"
    lines = [
        f"Rx sensitivity — receiver {receiver}",
        (
            f"  {cfg.get('mcs') or '-'}  {cfg.get('bandwidth') or '-'}  "
            f"channel {cfg.get('channel') if cfg.get('channel') is not None else 'unchanged'}  "
            f"PHY {cfg.get('phy_rate_mbps')} Mbps  "
            f"UDP {cfg.get('offered_mbps')} Mbps ({float(cfg.get('load_fraction') or 0) * 100:.0f}% of PHY)"
        ),
        (
            f"  PER limit {cfg.get('per_limit_pct')}%   "
            f"dwell {cfg.get('duration_s')} s   "
            f"fixed path loss {cfg.get('baseline_loss_db')} dB"
        ),
        "",
    ]
    seek_steps = [step for step in report.steps if step.phase == "seek" and step.rssi_dbm is not None]
    if seek_steps:
        landed = seek_steps[-1]
        lines.append(
            f"  Seek landed at {landed.att_db:.1f} dB, RSSI {landed.rssi_dbm:.1f} dBm"
        )
    lines.append(f"  {'att':>6} {'total':>7} {'RSSI':>8} {'SNR':>6} {'PER%':>8} {'Mbps':>8}  note")
    for step in report.steps:
        if step.phase != "fine" and step.per_pct is None:
            continue
        rssi = "" if step.rssi_dbm is None else f"{step.rssi_dbm:.1f}"
        snr = "" if step.snr_db is None else f"{step.snr_db:.1f}"
        per = "" if step.per_pct is None else f"{step.per_pct:.2f}"
        mbps = "" if step.throughput_mbps is None else f"{step.throughput_mbps:.1f}"
        marker = "sensitivity" if (
            report.result.sensitivity_att_db is not None
            and step.use_for_threshold
            and step.att_db == report.result.sensitivity_att_db
            and step.passed
        ) else step.note
        lines.append(
            f"  {step.att_db:6.1f} {step.total_loss_db:7.1f} {rssi:>8} {snr:>6} {per:>8} {mbps:>8}  {marker}"
        )
    lines.append("")
    result = report.result
    if result.status == "sensitivity_found":
        lines.append(
            f"Sensitivity: {result.sensitivity_rssi_dbm} dBm "
            f"at {result.sensitivity_att_db} dB attenuation "
            f"(PER {result.sensitivity_per_pct}%)."
        )
    else:
        lines.append(f"Sensitivity: not found ({result.status}).")
    if result.note:
        lines.append(result.note)
    return "\n".join(lines)


def write_rx_sensitivity_report(directory: str | Path, report: RxSensitivityReport, stem: str) -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / f"{stem}.csv"
    json_path = directory / f"{stem}.json"
    rows = [step.to_row() for step in report.steps]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        else:
            handle.write("")
    payload = report.to_dict()
    payload["modulation"] = ""
    if report.config.get("mcs"):
        try:
            payload["modulation"] = modulation_scheme(str(report.config["mcs"]))
        except ValueError:
            payload["modulation"] = ""
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return csv_path, json_path
