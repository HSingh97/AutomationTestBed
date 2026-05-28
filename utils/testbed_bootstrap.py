"""Session bootstrap: factory VLAN defaults, mgmt VLAN, CPE discovery, link recovery, lab PCs."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver

from utils.cpe_discovery import discover_cpe_ipv6s
from utils.lab_pc_net import _parse_ssh_target, configure_mgmt_interface, ensure_fallback_subnet
from utils.link_formation import (
    apply_cpe_pre_link_via_secondary_pc,
    ensure_cpe_link_credentials_always,
    ensure_p2mp_link_credentials,
    link_auto_enabled,
)
from utils.link_ssid import ensure_bts_link_ssid_ssh
from utils.net_utils import normalize_ip
from utils.reachability import (
    collect_recovery_fallback_hosts,
    is_strict_ipv6,
)
from utils.regression_flows import _open_root_ssh, _wait_for_ssh
from utils.vlan_control import ensure_vlan_mode_ssh
from utils.vlan_uci import build_cpe_untagged_commands, lab_pc_vlan_plan
from utils.wifi_lab import connect_wifi_24

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "logs" / "testbed_state.json"


@dataclass
class TestbedState:
    bts_mgmt_ipv6: str = ""
    cpe_mgmt_ipv6s: list[str] = field(default_factory=list)
    bts_pc_ipv6: str = ""
    cpe_pc_ipv6: str = ""
    mgmt_vlan_id: int = 0
    qinq_svlan: int = 100
    qinq_cvlan: int = 101
    mgmtvlan_uci: int = 101
    bts_tagging: str = "qinq"
    cpe_tagging: str = "untagged"
    link_up: bool = False
    notes: list[str] = field(default_factory=list)

    def persist(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> TestbedState | None:
        if not STATE_PATH.is_file():
            return None
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return cls(**{k: data[k] for k in asdict(cls()).keys() if k in data})
        except Exception:
            return None


def _tb(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("testbed", {}) or {}


def _mgmt(profile: dict[str, Any]) -> dict[str, Any]:
    return _tb(profile).get("mgmt_vlan", {}) or {}


def _apply_mgmt_to_dut(profile: dict[str, Any], state: TestbedState) -> None:
    """All tests use mgmt VLAN addresses from profile / discovery."""
    tb = _tb(profile)
    dut = profile["dut"]
    mgmt = _mgmt(profile)
    state.bts_mgmt_ipv6 = normalize_ip(str(mgmt.get("ipv6_bts") or dut.get("local_ipv6", "")))
    state.cpe_mgmt_ipv6s = [
        normalize_ip(str(mgmt.get("ipv6_cpe") or (dut.get("remote_ipv6s") or [""])[0]))
    ]
    state.bts_pc_ipv6 = normalize_ip(str(mgmt.get("ipv6_bts_pc") or dut.get("bts_pc_ipv6", "")))
    state.cpe_pc_ipv6 = normalize_ip(str(mgmt.get("ipv6_cpe_pc") or dut.get("cpe_pc_ipv6", "")))
    qinq = tb.get("qinq", {}) or {}
    state.qinq_svlan = int(qinq.get("svlan", 100))
    state.qinq_cvlan = int(qinq.get("cvlan", 101))
    state.mgmtvlan_uci = int(mgmt.get("uci_value", state.qinq_cvlan))
    state.mgmt_vlan_id = int(mgmt.get("lab_pc_vlan_id", state.qinq_cvlan))
    state.bts_tagging = "qinq"
    state.cpe_tagging = "untagged"

    dut["local_ipv6"] = state.bts_mgmt_ipv6
    dut["remote_ipv6s"] = list(state.cpe_mgmt_ipv6s)
    dut["bts_pc_ipv6"] = state.bts_pc_ipv6
    dut["cpe_pc_ipv6"] = state.cpe_pc_ipv6
    dut["mgmt_oob_ipv6"] = state.bts_pc_ipv6


async def _open_mgmt_strict(
    primary: str,
    password: str,
    *,
    label: str = "device",
    retries: int = 3,
    retry_s: int = 5,
    source_ipv6: str = "",
) -> tuple[AsyncGenericDriver, str]:
    """SSH to device on mgmt IPv6 only (no IPv4 fallbacks)."""
    host = normalize_ip(primary)
    source = normalize_ip(source_ipv6) if source_ipv6 else ""
    last = ""
    for attempt in range(max(1, retries)):
        try:
            return await _open_root_ssh(host, password), host
        except Exception as exc:
            last = str(exc)
            # Some hosts have multiple IPv6 routes/interfaces (tailscale, parent NIC, VLAN NIC).
            # If mgmt reachability exists but SSH picks a bad source path, retry with explicit source bind.
            if source and ":" in host:
                try:
                    conn = AsyncGenericDriver(
                        host=host,
                        auth_username="root",
                        auth_password=password,
                        auth_strict_key=False,
                        transport="asyncssh",
                        transport_options={"local_addr": source},
                    )
                    await conn.open()
                    return conn, host
                except Exception as src_exc:
                    last = f"{last}; source-bind({source})={src_exc}"
            if attempt + 1 < retries:
                await asyncio.sleep(retry_s)
    raise ConnectionError(f"{label} mgmt IPv6 SSH failed for {host}: {last}")


async def _open_with_recovery_fallbacks(
    primary: str,
    password: str,
    fallbacks: list[str],
    *,
    label: str = "device",
) -> tuple[AsyncGenericDriver, str]:
    """SSH during recovery — mgmt IPv6 first, then factory IPv4 chain."""
    return await _open_with_fallbacks(primary, password, fallbacks, label=label)


async def _setup_lab_pcs(
    active: dict[str, Any],
    state: TestbedState,
    password: str,
) -> None:
    """Configure lab PC Ethernet tagging + mgmt IPv6 via internet SSH (before device access)."""
    tb = _tb(active)
    mgmt = _mgmt(active)
    prefix_len = int(mgmt.get("prefix_len", 120))
    bts_pc_tag = lab_pc_vlan_plan(tb, side="bts")
    cpe_pc_tag = lab_pc_vlan_plan(tb, side="cpe")

    primary_pc = dict(tb.get("primary_pc", {}) or {})
    internet_ssh = str(primary_pc.get("internet_ssh", "")).strip()
    if internet_ssh:
        primary_pc["ssh"] = internet_ssh
        primary_pc["local"] = False
    else:
        primary_pc.setdefault("local", not primary_pc.get("ssh"))
    primary_pc.setdefault("fallback_ipv4", "10.0.0.10")
    await ensure_fallback_subnet(primary_pc, password)

    await configure_mgmt_interface(
        primary_pc,
        ipv6_address=f"{state.bts_pc_ipv6}/{prefix_len}",
        prefix_len=prefix_len,
        password=password,
        vlan_id=state.qinq_cvlan,
        tagging=bts_pc_tag,
    )

    secondary_pc = tb.get("secondary_pc", {}) or {}
    if secondary_pc.get("enabled", True):
        sec_cfg = dict(secondary_pc)
        if not sec_cfg.get("password"):
            sec_cfg["password"] = password
        sec_cfg.setdefault("fallback_ipv4", "10.0.0.11")
        await ensure_fallback_subnet(sec_cfg, str(sec_cfg.get("password") or password))
        await configure_mgmt_interface(
            sec_cfg,
            ipv6_address=f"{state.cpe_pc_ipv6}/{prefix_len}",
            prefix_len=prefix_len,
            password=str(sec_cfg.get("password") or password),
            vlan_id=0,
            tagging=cpe_pc_tag,
        )


async def _open_with_fallbacks(
    primary: str,
    password: str,
    fallbacks: list[str],
    *,
    label: str = "device",
) -> tuple[AsyncGenericDriver, str]:
    hosts = [normalize_ip(primary)] + [normalize_ip(h) for h in fallbacks if h]
    seen: set[str] = set()
    ordered = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            ordered.append(h)
    last = ""
    primary_n = normalize_ip(primary)
    for host in ordered:
        try:
            ssh = await _open_root_ssh(host, password)
            if host != primary_n:
                print(f"[testbed] {label} SSH via fallback {host} (primary {primary_n} unreachable)")
            return ssh, host
        except Exception as exc:
            last = str(exc)
    raise ConnectionError(f"SSH failed for {ordered}: {last}")


async def _configure_cpe_vlan_via_secondary_pc(
    sec_cfg: dict[str, Any],
    password: str,
    profile_tb: dict[str, Any],
    vlan_ssh: dict[str, Any],
    cpe_mode: str,
) -> bool:
    """Hop through CPE-side lab PC when CPE mgmt IPv6 / factory IP is not reachable from primary."""
    import shlex

    ssh_target = str(sec_cfg.get("ssh", "")).strip()
    if not ssh_target:
        return False
    cpe_factory = normalize_ip(str(sec_cfg.get("cpe_factory_ipv4", "192.168.2.1")))
    host, user = _parse_ssh_target(ssh_target)
    pc_pass = str(sec_cfg.get("password") or password)
    uci_cmds = build_cpe_untagged_commands(profile_tb) if cpe_mode == "transparent" else []
    if not uci_cmds:
        return False

    from scrapli.driver.generic import AsyncGenericDriver

    pc = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=pc_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await pc.open()
    try:
        inner = " && ".join(shlex.quote(c) for c in uci_cmds)
        remote = (
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
            f"root@{cpe_factory} {inner}"
        )
        result = await pc.send_command(remote, timeout_ops=90)
        out = str(result.result or "")
        if "error" in out.lower() and "entry not found" not in out.lower():
            print(f"[testbed] secondary→CPE VLAN: {out[:300]}")
            return False
        print(f"[testbed] CPE VLAN via secondary PC {user}@{host} → {cpe_factory}")
        return True
    finally:
        await pc.close()


async def _link_health_bts(ssh, profile: dict[str, Any]) -> bool:
    link = profile.get("link", {})
    min_clients = int(link.get("min_connected_clients", 1))
    # Link SSID is on ath{radio_idx}; `wlanconfig wifiN` often fails (-22) on this build.
    radio_idx = int(link.get("radio_idx", 1))
    ath = f"ath{radio_idx}"
    out = await ssh.send_command(
        f"wlanconfig {ath} list 2>/dev/null | grep -cE '^[0-9a-f][0-9a-f]:' || echo 0",
        timeout_ops=20,
    )
    try:
        count = int(str(out.result or "0").strip().split()[0])
    except (ValueError, IndexError):
        count = 0
    if count < min_clients:
        out2 = await ssh.send_command(
            f"iw dev {ath} station dump 2>/dev/null | grep -c '^Station' || echo 0",
            timeout_ops=20,
        )
        try:
            count = int(str(out2.result or "0").strip().split()[0])
        except (ValueError, IndexError):
            count = 0
    return count >= min_clients


async def _restore_link_via_archives(
    *,
    profile_bundle,
    device_creds,
    gui_page,
    password: str,
) -> None:
    """BTS/CPE restore from active + link_formation profile archives."""
    from utils.recovery_manager import RecoveryManager, get_active_recovery_manager

    manager = get_active_recovery_manager()
    if manager is None and gui_page is not None:
        manager = RecoveryManager(profile_bundle)
    if manager is None or gui_page is None:
        return
    recovery = dict(profile_bundle.active.get("recovery", {}))
    link_profile = profile_bundle.recovery.get("link", {}) or {}
    link_rec = _tb(profile_bundle.active).get("link_recovery", {}) or {}
    # Merge link-formation profile targets into active link for post-restore SSID/MCS tuning
    active_link = profile_bundle.active.setdefault("link", {})
    for key in ("ssid", "target_bandwidth", "target_mcs_rate", "target_spatial_stream", "target_ddrs_rate"):
        if link_profile.get(key):
            active_link[key] = link_profile[key]
    bts_role = "BTS"
    cpe_role = "CPE"

    dut = profile_bundle.active["dut"]
    bts_target = normalize_ip(dut.get("local_ipv6", ""))
    restore_ip = str(recovery.get("restore_default_ip", "192.168.2.1"))

    if link_rec.get("restore_bts", True):
        ok = await manager.run_profile_restore(
            gui_page=gui_page,
            device_creds=device_creds,
            role=bts_role,
            post_restore_ip=bts_target,
        )
        if ok:
            print("[testbed] BTS link-formation archive restored.")
        else:
            print(f"[testbed] BTS restore skipped/failed: {manager.metrics.last_error}")

    if not link_rec.get("restore_cpe", True):
        return

    sec = _tb(profile_bundle.active).get("secondary_pc", {}) or {}
    cpe_fallback = str(sec.get("cpe_factory_ipv4", "192.168.2.1"))
    cpe_archive = link_rec.get("cpe_archive") or recovery.get("cpe_restore_archive", "config/CPE.tar.gz")
    recovery = dict(recovery)
    recovery["cpe_restore_archive"] = cpe_archive
    profile_bundle.active["recovery"] = recovery

    # CPE restore: use secondary automation path (SSH to PC near CPE) when direct CPE SSH fails.
    cpe_hosts_try = [normalize_ip(h) for h in dut.get("remote_ipv6s", []) if h]
    cpe_hosts_try.append(cpe_fallback)
    cpe_ssh = None
    for host in cpe_hosts_try:
        try:
            cpe_ssh = await _open_root_ssh(host, password)
            print(f"[testbed] CPE reachable at {host} for restore check.")
            await cpe_ssh.close()
            break
        except Exception:
            continue

    if cpe_ssh is None:
        # When link is down we need CPE on factory/known config so association can recover.
        # Prefer restoring via GUI when we have a live `gui_page`.
        post_restore_ip = ""
        remote_ips = dut.get("remote_ipv6s") or []
        if remote_ips:
            post_restore_ip = normalize_ip(str(remote_ips[0]))

        print("[testbed] CPE not reachable — attempting CPE archive restore via GUI.")
        ok = await manager.run_profile_restore(
            gui_page=gui_page,
            device_creds=device_creds,
            role=cpe_role,
            post_restore_ip=post_restore_ip or None,
        )
        if ok:
            print("[testbed] CPE link-formation archive restored.")
        else:
            print("[testbed] CPE archive restore failed — use secondary PC / manual load if needed.")
            ssh_target = str(sec.get("ssh", "")).strip()
            if ssh_target:
                print(f"[testbed] Secondary PC: {ssh_target} — load {cpe_archive} manually.")


async def bootstrap_testbed(
    profile_bundle,
    device_creds: dict[str, str],
    *,
    gui_page=None,
    skip_link_recovery: bool = False,
    cli_fallback_ip: str | None = None,
) -> TestbedState:
    """
    Rigid testbed bootstrap:
    1) Lab PCs via internet IP (Ethernet VLAN + mgmt IPv6)
    2) BTS/CPE via mgmt IPv6 only (QinQ / transparent)
    3) Recovery via factory IPv4 + GUI archives only when link/mgmt fails
    """
    active = profile_bundle.active
    tb = _tb(active)
    password = device_creds["pass"]
    state = TestbedState()
    _apply_mgmt_to_dut(active, state)

    if not tb.get("enabled", True):
        state.persist()
        return state

    mgmt = _mgmt(active)
    factory = tb.get("factory_defaults", {}) or {}
    vlan_ssh = tb.get("vlan_ssh", {}) or {}
    bts_recovery = collect_recovery_fallback_hosts(active, role="bts", cli_fallback=cli_fallback_ip)
    cpe_recovery = collect_recovery_fallback_hosts(active, role="cpe", cli_fallback=cli_fallback_ip)
    strict = is_strict_ipv6(active)
    if strict:
        state.notes.append("strict_ipv6: device access mgmt IPv6 only; IPv4 recovery-only")

    # --- Phase 1: lab PCs (internet SSH) before device mgmt IPv6 is on the wire ---
    print("[testbed] Phase 1: lab PC Ethernet VLAN + mgmt IPv6")
    await _setup_lab_pcs(active, state, password)

    bts_ssh: AsyncGenericDriver | None = None
    bts_host = state.bts_mgmt_ipv6
    used_recovery = False

    async def _connect_bts_strict() -> tuple[AsyncGenericDriver, str]:
        ssh, host = await _open_mgmt_strict(
            state.bts_mgmt_ipv6,
            password,
            label="BTS",
            source_ipv6=state.bts_pc_ipv6,
        )
        print(f"[testbed] BTS SSH via mgmt IPv6 {host}")
        return ssh, host

    async def _connect_bts_recovery() -> tuple[AsyncGenericDriver, str]:
        nonlocal used_recovery
        used_recovery = True
        ssh, host = await _open_with_recovery_fallbacks(
            state.bts_mgmt_ipv6, password, bts_recovery, label="BTS recovery"
        )
        print(f"[testbed] BTS SSH via recovery path {host}")
        state.notes.append(f"BTS recovery SSH via {host}")
        return ssh, host

    try:
        try:
            bts_ssh, bts_host = await _connect_bts_strict()
        except ConnectionError as exc:
            state.notes.append(f"BTS mgmt IPv6 unreachable: {exc}")
            print(f"[testbed] {exc}")
            if not skip_link_recovery:
                bts_ssh, bts_host = await _connect_bts_recovery()
            else:
                raise

        # --- Phase 2: verify/apply VLAN on BTS (prefer mgmt IPv6 session) ---
        bts_mode = str(factory.get("bts_vlan_mode", "qinq"))
        if tb.get("configure_vlan_modes", True):
            ok = await ensure_vlan_mode_ssh(bts_ssh, "bts", bts_mode, vlan_ssh, profile_tb=tb)
            state.notes.append(
                f"BTS QinQ svlan={state.qinq_svlan} cvlan={state.qinq_cvlan} "
                f"mgmtvlan(vlan.ath1.mgmtvlan)={state.mgmtvlan_uci} ok={ok}"
            )

        for cmd in mgmt.get("bts_apply_commands", []) or []:
            if cmd:
                await bts_ssh.send_command(cmd, timeout_ops=60)

        link_ok = await _link_health_bts(bts_ssh, active)
        if not skip_link_recovery and not link_ok:
            state.notes.append("link down at bootstrap")
            if gui_page is not None:
                await _restore_link_via_archives(
                    profile_bundle=profile_bundle,
                    device_creds=device_creds,
                    gui_page=gui_page,
                    password=password,
                )
                await asyncio.sleep(int(active.get("recovery", {}).get("reboot_wait_seconds", 60)))
                await bts_ssh.close()
                bts_ssh, bts_host = await _connect_bts_strict()
                used_recovery = False
                if link_auto_enabled(active):
                    link_creds = await ensure_p2mp_link_credentials(
                        bts_ssh=bts_ssh,
                        profile=active,
                    )
                    state.notes.append(
                        f"post-restore AIRTEL SSID={link_creds.ssid} serial={link_creds.serial}"
                    )
            elif strict and bts_recovery:
                print("[testbed] link down — retry after recovery IPv4 VLAN check")
                rec_ssh, rec_host = await _connect_bts_recovery()
                await rec_ssh.close()
                bts_ssh, bts_host = await _connect_bts_strict()

        link_cfg = active.get("link", {}) or {}
        link_creds = None  # AIRTEL_SSID_GEN result when auto_credentials
        if link_auto_enabled(active):
            link_creds = await ensure_p2mp_link_credentials(
                bts_ssh=bts_ssh,
                profile=active,
            )
            state.notes.append(f"link AIRTEL SSID={link_creds.ssid} serial={link_creds.serial}")
            active.setdefault("link", {})["ssid"] = link_creds.ssid
        else:
            await ensure_bts_link_ssid_ssh(
                bts_ssh, radio_idx=int(link_cfg.get("radio_idx", 1)), profile=active
            )

        # --- CPE: always set SSID/key via root on fallback (default 10.0.0.1) ---
        if link_auto_enabled(active) and link_creds is not None:
            cpe_direct = await ensure_cpe_link_credentials_always(active, link_creds, force=True)
            state.notes.append(f"CPE SSID/key direct (10.0.0.1) ok={cpe_direct}")

        # --- CPE pre-link VLAN via secondary PC when mgmt IPv6 not up yet ---
        cpe_mode = str(factory.get("cpe_vlan_mode", "transparent"))
        sec = tb.get("secondary_pc", {}) or {}
        if tb.get("configure_vlan_modes", True) and sec.get("enabled", True) and sec.get("ssh"):
            if link_auto_enabled(active) and link_creds is not None:
                ok = await apply_cpe_pre_link_via_secondary_pc(
                    sec,
                    link_creds,
                    active,
                    profile_tb=tb,
                    password=password,
                    cpe_mode=cpe_mode,
                )
                state.notes.append(f"CPE pre-link (secondary PC) ok={ok}")
            else:
                try:
                    ok = await _configure_cpe_vlan_via_secondary_pc(
                        sec, password, tb, vlan_ssh, cpe_mode
                    )
                    state.notes.append(f"CPE VLAN pre-link (secondary PC) ok={ok}")
                except Exception as sec_exc:
                    state.notes.append(f"CPE pre-link failed: {sec_exc}")
        else:
            state.notes.append("CPE pre-link skipped (secondary_pc.ssh not configured)")

        link_tb = active.get("link", {}) or {}
        timeout_s = int(link_tb.get("health_check_timeout_s", 60))
        poll_s = 3
        started = time.time()
        state.link_up = False
        while time.time() - started < timeout_s:
            if await _link_health_bts(bts_ssh, active):
                state.link_up = True
                break
            await asyncio.sleep(poll_s)

        if state.link_up:
            discovered = await discover_cpe_ipv6s(
                bts_ssh,
                tb,
                bts_mgmt_ipv6=state.bts_mgmt_ipv6,
                exclude_ipv6s=[state.bts_pc_ipv6, state.cpe_pc_ipv6],
            )
            if discovered:
                state.cpe_mgmt_ipv6s = [normalize_ip(discovered[0])]
                active["dut"]["remote_ipv6s"] = list(state.cpe_mgmt_ipv6s)
                state.notes.append(f"CPE mgmt IPv6 after link: {state.cpe_mgmt_ipv6s[0]}")
                print(f"[testbed] CPE discovered after link: {state.cpe_mgmt_ipv6s}")
            else:
                print("[testbed] CPE mgmt not in BTS tables yet — profile address used for tests.")

        if not state.link_up:
            state.notes.append(
                "link still down after bootstrap — run bootstrap_testbed.py --with-gui"
            )

    finally:
        if bts_ssh is not None:
            await bts_ssh.close()

    wifi_cfg = tb.get("wifi", {}) or {}
    if wifi_cfg.get("enabled", False):
        await connect_wifi_24(wifi_cfg)

    state.persist()
    print(
        f"[testbed] bootstrap complete link_up={state.link_up} "
        f"BTS={state.bts_mgmt_ipv6} CPE={state.cpe_mgmt_ipv6s} strict_ipv6={strict}"
    )
    return state
