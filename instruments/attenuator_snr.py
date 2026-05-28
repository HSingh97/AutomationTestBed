"""2x2 Vaunix LDA602 control with link SNR readout (SNMP)."""

from __future__ import annotations

import asyncio
import csv
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from instruments.vaunix_lda import VaunixLdaApi, wifi_channel_to_mhz
from traffic.link_stats import fetch_link_clients


@dataclass
class SnrReading:
    """Per-chain SNR from BTS SNMP (local = BTS RX, remote = CPE-side)."""

    l_snr1: str
    l_snr2: str
    r_snr1: str
    r_snr2: str
    tx_rate: str = "-"
    rx_rate: str = "-"
    cpe_ip: str = ""

    @classmethod
    def from_client(cls, client: dict[str, Any]) -> SnrReading:
        return cls(
            l_snr1=str(client.get("l_snr1", "-")),
            l_snr2=str(client.get("l_snr2", "-")),
            r_snr1=str(client.get("r_snr1", "-")),
            r_snr2=str(client.get("r_snr2", "-")),
            tx_rate=str(client.get("tx_rate", "-")),
            rx_rate=str(client.get("rx_rate", "-")),
            cpe_ip=str(client.get("ip", "")),
        )

    def as_floats(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for key in ("l_snr1", "l_snr2", "r_snr1", "r_snr2"):
            try:
                out[key] = float(self.__dict__[key])
            except (TypeError, ValueError):
                out[key] = None
        return out


@dataclass
class SweepStepResult:
    step_index: int
    channel: int | None
    frequency_mhz: float | None
    att_chain0_db: float
    att_chain1_db: float
    snr: SnrReading
    elapsed_s: float

    def to_row(self) -> dict[str, Any]:
        base = {
            "step": self.step_index,
            "channel": self.channel if self.channel is not None else "",
            "frequency_mhz": self.frequency_mhz if self.frequency_mhz is not None else "",
            "att_chain0_db": self.att_chain0_db,
            "att_chain1_db": self.att_chain1_db,
            "elapsed_s": round(self.elapsed_s, 2),
        }
        base.update({f"snr_{k}": v for k, v in asdict(self.snr).items() if k != "cpe_ip"})
        base["cpe_ip"] = self.snr.cpe_ip
        return base


def _parse_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    from config.defaults import ATTENUATOR_DEFAULTS

    merged = dict(ATTENUATOR_DEFAULTS)
    if cfg:
        merged.update(cfg)
    return merged


class AttenuatorSnrController:
    """
    Control two LDA-602 units (one per 2x2 chain) and observe link SNR via SNMP.

    Typical usage::

        ctrl = AttenuatorSnrController.from_profile(profile_bundle.active)
        ctrl.open()
        ctrl.set_link(channel=36, att_chain0_db=0, att_chain1_db=0)
        results = ctrl.sweep_attenuation(channel=36, start_db=0, stop_db=20, step_db=2)
        ctrl.close()
    """

    def __init__(
        self,
        *,
        dut_ip: str,
        snmp_community: str = "ubr@rw123",
        snmp_radio_idx: int = 1,
        dll_path: str | None = None,
        backend: str = "auto",
        chain_serials: list[int | None] | None = None,
        chain_device_ids: list[int | None] | None = None,
        chain_lab_brick_ids: list[int | None] | None = None,
        settle_seconds: float = 3.0,
        min_attenuation_db: float = 0.0,
        max_attenuation_db: float = 63.0,
        su_index: int = 0,
    ) -> None:
        self.dut_ip = dut_ip
        self.snmp_community = snmp_community
        self.snmp_radio_idx = snmp_radio_idx
        self.settle_seconds = settle_seconds
        self.min_attenuation_db = min_attenuation_db
        self.max_attenuation_db = max_attenuation_db
        self.su_index = su_index
        self._chain_serials = chain_serials or [None, None]
        self._chain_device_ids = chain_device_ids or [None, None]
        self._chain_lab_brick_ids = chain_lab_brick_ids or [None, None]
        self._api = VaunixLdaApi(dll_path=dll_path, backend=backend)
        self._baseline_snr: SnrReading | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> AttenuatorSnrController:
        dut = profile.get("dut", {})
        att = _parse_cfg(profile.get("attenuator"))
        dut_ip = att.get("dut_ip") or dut.get("local_ipv6") or dut.get("local_ip") or ""
        snmp = att.get("snmp", {}) or {}
        chains = att.get("chains", []) or []

        def _chain_field(key: str) -> list[int | None]:
            values = [c.get(key) for c in chains] if chains else []
            while len(values) < 2:
                values.append(None)
            return values[:2]

        return cls(
            dut_ip=str(dut_ip),
            snmp_community=str(snmp.get("community", att.get("snmp_community", "ubr@rw123"))),
            snmp_radio_idx=int(snmp.get("radio_idx", att.get("snmp_radio_idx", 1))),
            dll_path=att.get("dll_path") or None,
            backend=str(att.get("backend", "auto")),
            chain_serials=_chain_field("serial"),
            chain_device_ids=_chain_field("device_id"),
            chain_lab_brick_ids=_chain_field("lab_brick_id"),
            settle_seconds=float(att.get("settle_seconds", 3.0)),
            min_attenuation_db=float(att.get("min_attenuation_db", 0.0)),
            max_attenuation_db=float(att.get("max_attenuation_db", 63.0)),
            su_index=int(att.get("su_index", 0)),
        )

    def open(self) -> None:
        self._api.open()
        devices = self._api.list_devices()
        print(f"    -> [ATTEN] {len(devices)} LDA device(s): " + ", ".join(
            f"chain→id={d.device_id} serial={d.serial} {d.model}" for d in devices
        ))

    def close(self) -> None:
        self._api.close()

    def __enter__(self) -> AttenuatorSnrController:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def read_snr(self) -> SnrReading:
        clients = fetch_link_clients(
            self.dut_ip,
            snmp_community=self.snmp_community,
            radio_idx=self.snmp_radio_idx,
        )
        if not clients:
            return SnrReading("-", "-", "-", "-")
        idx = min(self.su_index, len(clients) - 1)
        return SnrReading.from_client(clients[idx])

    def capture_baseline_snr(self) -> SnrReading:
        self._baseline_snr = self.read_snr()
        print(
            "    -> [ATTEN] Baseline SNR: "
            f"L1={self._baseline_snr.l_snr1} L2={self._baseline_snr.l_snr2} "
            f"R1={self._baseline_snr.r_snr1} R2={self._baseline_snr.r_snr2}"
        )
        return self._baseline_snr

    def set_chain(
        self,
        chain: int,
        attenuation_db: float,
        *,
        channel: int | str | None = None,
        frequency_mhz: float | None = None,
    ) -> None:
        att_db = max(self.min_attenuation_db, min(self.max_attenuation_db, float(attenuation_db)))
        serial = self._chain_serials[chain] if chain < len(self._chain_serials) else None
        device_id = self._chain_device_ids[chain] if chain < len(self._chain_device_ids) else None
        lab_brick_id = self._chain_lab_brick_ids[chain] if chain < len(self._chain_lab_brick_ids) else None
        dev_id = self._api.resolve_device_id(
            chain,
            serial=serial,
            device_id=device_id,
            lab_brick_id=lab_brick_id,
        )
        if frequency_mhz is None and channel is not None:
            frequency_mhz = float(wifi_channel_to_mhz(channel))
        if frequency_mhz is not None:
            self._api.set_working_frequency_mhz(dev_id, frequency_mhz)
        self._api.set_attenuation_db(dev_id, att_db)
        print(f"    -> [ATTEN] Chain {chain} (dev {dev_id}): {att_db} dB", end="")
        if frequency_mhz is not None:
            print(f" @ {frequency_mhz} MHz", end="")
        print()

    def set_link(
        self,
        *,
        channel: int | str | None = None,
        frequency_mhz: float | None = None,
        att_chain0_db: float = 0.0,
        att_chain1_db: float = 0.0,
        settle: bool = True,
    ) -> SnrReading:
        """Set both chains (optionally tune working frequency from Wi-Fi channel) and read SNR."""
        for chain, att in enumerate((att_chain0_db, att_chain1_db)):
            self.set_chain(chain, att, channel=channel, frequency_mhz=frequency_mhz)
        if settle:
            self._wait_settle()
        return self.read_snr()

    def sweep_attenuation(
        self,
        *,
        channel: int | str | None = None,
        frequency_mhz: float | None = None,
        start_db: float = 0.0,
        stop_db: float = 30.0,
        step_db: float = 2.0,
        symmetric: bool = True,
        att_chain0_db: float | None = None,
        att_chain1_db: float | None = None,
        capture_baseline: bool = True,
    ) -> list[SweepStepResult]:
        """
        Step attenuation up or down and record SNR after each step.

        If ``symmetric`` is True (default), both chains use the same attenuation each step.
        Otherwise pass explicit per-chain values via ``att_chain0_db`` / ``att_chain1_db`` each call
        by setting ``symmetric=False`` and using ``sweep_custom`` (not implemented) — for now
        symmetric only.
        """
        if frequency_mhz is None and channel is not None:
            frequency_mhz = float(wifi_channel_to_mhz(channel))

        start_db = max(self.min_attenuation_db, start_db)
        stop_db = min(self.max_attenuation_db, stop_db)
        if step_db <= 0:
            raise ValueError("step_db must be positive")

        steps: list[float] = []
        current = start_db
        while current <= stop_db + 1e-6:
            steps.append(round(current, 2))
            current += step_db

        results: list[SweepStepResult] = []
        if capture_baseline:
            self.set_link(
                channel=channel,
                frequency_mhz=frequency_mhz,
                att_chain0_db=start_db,
                att_chain1_db=start_db,
            )
            self.capture_baseline_snr()

        ch_num = int(channel) if channel is not None else None
        for idx, att in enumerate(steps):
            t0 = time.monotonic()
            if symmetric:
                c0, c1 = att, att
            else:
                c0 = att if att_chain0_db is None else att_chain0_db
                c1 = att if att_chain1_db is None else att_chain1_db
            snr = self.set_link(
                channel=channel,
                frequency_mhz=frequency_mhz,
                att_chain0_db=c0,
                att_chain1_db=c1,
                settle=True,
            )
            elapsed = time.monotonic() - t0
            row = SweepStepResult(
                step_index=idx,
                channel=ch_num,
                frequency_mhz=frequency_mhz,
                att_chain0_db=c0,
                att_chain1_db=c1,
                snr=snr,
                elapsed_s=elapsed,
            )
            results.append(row)
            floats = snr.as_floats()
            print(
                f"    -> [ATTEN] Step {idx}: {c0}/{c1} dB | "
                f"L-SNR {floats.get('l_snr1')} / {floats.get('l_snr2')} dB | "
                f"R-SNR {floats.get('r_snr1')} / {floats.get('r_snr2')} dB"
            )
        return results

    def assert_snr_decreases_with_attenuation(
        self,
        results: list[SweepStepResult],
        *,
        min_drop_db: float = 1.0,
        metric: str = "l_snr1",
    ) -> None:
        """Verify SNR drops when attenuation increases (sanity check on sweep data)."""
        series: list[tuple[float, float]] = []
        for row in results:
            val = row.snr.as_floats().get(metric)
            if val is None:
                continue
            series.append((row.att_chain0_db, val))
        if len(series) < 2:
            print(f"    -> [ATTEN] Not enough SNR samples to validate trend on {metric}.")
            return
        att_start, snr_start = series[0]
        att_end, snr_end = series[-1]
        if att_end <= att_start:
            return
        if snr_end > snr_start - min_drop_db:
            raise AssertionError(
                f"Expected SNR ({metric}) to drop by at least {min_drop_db} dB when attenuation "
                f"increased {att_start}→{att_end} dB; SNR went {snr_start}→{snr_end}."
            )
        print(
            f"    -> [ATTEN] SNR trend OK ({metric}): {snr_start}→{snr_end} dB "
            f"for attenuation {att_start}→{att_end} dB"
        )

    def _wait_settle(self) -> None:
        time.sleep(self.settle_seconds)

    @staticmethod
    def write_csv(path: str | Path, results: list[SweepStepResult]) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not results:
            path.write_text("")
            return path
        rows = [r.to_row() for r in results]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return path


async def sweep_attenuation_async(controller: AttenuatorSnrController, **kwargs) -> list[SweepStepResult]:
    """Run blocking sweep in a thread (for pytest-asyncio)."""
    return await asyncio.to_thread(controller.sweep_attenuation, **kwargs)
