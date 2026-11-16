"""DDRS co-channel / adjacent-channel interference — reuses co-channel secondary link."""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from typing import Any, Iterator

from utils.cochannel_flows import (
    CoChannelConfig,
    RfSnap,
    read_rf_snap,
    set_interference_level,
    set_secondary_tx_power,
    stop_secondary_iperf,
)
from utils.ddrs_flows import DdrsConfig


def cochannel_cfg_from_ddrs(cfg: DdrsConfig) -> CoChannelConfig:
    """Map DDRS lab profile → CoChannelConfig for secondary BTS/CPE interference."""
    return CoChannelConfig(
        lab_pc_ssh=cfg.lab_pc_ssh,
        lab_pc_password=cfg.lab_pc_password,
        device_password=cfg.device_password,
        primary_bts=cfg.bts_ip,
        primary_cpes=list(cfg.cpe_ips),
        secondary_bts=cfg.secondary_bts,
        secondary_cpe=cfg.secondary_cpe,
        wifi_iface=cfg.wifi_iface,
        wlan_iface=cfg.wlan_iface,
        mac_vlan_script=cfg.mac_vlan_script,
        bts_iface=cfg.bts_iface,
        cpe_iface=cfg.cpe_iface,
        dl_mbps_per_su=cfg.dl_mbps_per_su,
        ul_mbps_per_su=cfg.ul_mbps_per_su,
        duration_s=cfg.traffic_duration_s,
        buffer_s=cfg.buffer_s,
        interference=dict(cfg.interference),
        interference_mode=cfg.interference_mode,
        secondary_tx_fixed_dbm=int(cfg.secondary_tx_fixed_dbm),
        secondary_traffic_backend=cfg.secondary_traffic_backend,
        secondary_throughput_max_mbps=cfg.secondary_throughput_max_mbps,
        secondary_throughput_step_mbps=cfg.secondary_throughput_step_mbps,
        trex=dict(cfg.trex),
        iperf_port=cfg.iperf_port,
        iperf_port_ul=cfg.iperf_port_ul,
        rssi_settle_s=cfg.rssi_settle_s,
        secondary_tx_power_min_dbm=int(cfg.secondary_tx_power_min_dbm),
        secondary_tx_power_max_dbm=int(cfg.secondary_tx_power_max_dbm),
        results_dir=cfg.results_dir,
    )


def read_ddrs_interference_rf(cfg: DdrsConfig) -> RfSnap:
    return read_rf_snap(cochannel_cfg_from_ddrs(cfg))


@contextmanager
def secondary_interference_session(
    cfg: DdrsConfig,
    *,
    case_id: str,
    level: str | None = None,
) -> Iterator[tuple[CoChannelConfig, RfSnap]]:
    """Start co-channel secondary link + load; always stop on exit."""
    cc = cochannel_cfg_from_ddrs(cfg)
    active_level = str(level or cfg.interference_level or "mid").lower()
    print(
        f"[ddrs][{case_id}] secondary link {cc.secondary_bts}↔{cc.secondary_cpe} "
        f"interference={active_level}",
        flush=True,
    )
    from utils.ddrs_flows import wait_ddrs_link

    wait_ddrs_link(cfg, case_id=case_id, timeout_s=min(cfg.reboot_wait_s, 180.0), min_stations=1)
    stop_secondary_iperf(cc)
    snap = None
    last_exc: BaseException | None = None
    try:
        for attempt in range(3):
            try:
                snap = set_interference_level(cc, active_level)
                last_exc = None
                break
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
                last_exc = exc
                print(
                    f"[ddrs][{case_id}] WARN interference setup attempt {attempt + 1}/3: {exc}",
                    flush=True,
                )
                time.sleep(12)
        if snap is None:
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(f"{case_id}: secondary interference setup failed")
        yield cc, snap
    finally:
        stop_secondary_iperf(cc)
        set_secondary_tx_power(cc, cc.secondary_tx_power_min_dbm)
