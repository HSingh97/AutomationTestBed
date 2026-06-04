"""RF link recovery for IP suite: fallback SSH, SSID/key sync, txpower, backup checks."""

from __future__ import annotations

import asyncio
import re
import shlex
import tarfile
import time
from pathlib import Path
from typing import Any

from utils.link_formation import (
    apply_bts_tx_power,
    apply_link_credentials_ssh,
    ensure_cpe_link_credentials_always,
    ensure_p2mp_link_credentials,
    link_auto_enabled,
    link_health_bts,
    read_bts_wireless_diag,
    resolve_link_credentials_ssh,
)
from utils.link_ssid import ensure_bts_link_ssid_ssh


def _archive_path(profile: dict[str, Any], *, role: str = "bts") -> Path | None:
    from utils.device_backup import get_session_backup_path, resolve_restore_archive_path

    cached = get_session_backup_path(role)
    if cached is not None:
        return cached
    repo = Path(__file__).resolve().parent.parent
    ip_cfg = profile.get("ip_tests", {}) or {}
    resolved = resolve_restore_archive_path(profile, role=role, cfg=ip_cfg, repo_root=repo)
    if resolved is not None:
        return resolved
    recovery = profile.get("recovery", {}) or {}
    tb = profile.get("testbed", {}) or {}
    link_rec = tb.get("link_recovery", {}) or {}
    if role == "cpe":
        rel = link_rec.get("cpe_archive") or recovery.get("cpe_restore_archive", "")
    else:
        rel = link_rec.get("bts_archive") or recovery.get("bts_restore_archive", "")
    if not rel:
        return None
    path = Path(str(rel))
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if path.is_file() else None


def inspect_backup_for_link_issues(
    archive: Path,
    *,
    bad_lan_ips: tuple[str, ...] = ("192.168.2.1",),
    bad_tx_power: str = "26",
) -> list[str]:
    """
    Scan sysupgrade backup for settings that break RF link on this bench
    (e.g. LAN 192.168.2.1 + tx power 26 from stale BTS.tar.gz).
    """
    issues: list[str] = []
    if not archive.is_file():
        return [f"archive missing: {archive}"]
    try:
        with tarfile.open(archive, "r:*") as tar:
            network_text = ""
            wireless_text = ""
            txparam_text = ""
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.lstrip("./")
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                body = extracted.read().decode("utf-8", errors="replace")
                if name.endswith("config/network"):
                    network_text = body
                elif name.endswith("config/wireless"):
                    wireless_text = body
                elif "txparam" in name or name.endswith("config/txparam"):
                    txparam_text += body + "\n"
            for bad_ip in bad_lan_ips:
                if re.search(rf"option\s+ipaddr\s+'{re.escape(bad_ip)}'", network_text):
                    issues.append(f"network.lan.ipaddr={bad_ip} in {archive.name}")
            if bad_tx_power and re.search(
                rf"option\s+atpcpower\s+'{re.escape(bad_tx_power)}'", txparam_text
            ):
                issues.append(f"txparam.atpcpower={bad_tx_power} in {archive.name}")
            if not wireless_text.strip() and not txparam_text.strip():
                issues.append(f"no wireless/txparam config in {archive.name}")
    except tarfile.TarError as exc:
        issues.append(f"cannot read {archive.name}: {exc}")
    return issues


async def recover_testbed_link_ssh(
    *,
    bts_ssh,
    profile: dict[str, Any],
    password: str,
    notes: list[str],
    cfg: dict[str, Any] | None = None,
) -> bool:
    """
    Re-establish P2MP link using fallback SSH paths only (no GUI):
    1) Check wlan clients on BTS
    2) Sync SSID/key + txpower on BTS and CPE (secondary PC → CPE factory)
    3) Poll until min clients or timeout
    """
    cfg = cfg or {}
    link = profile.get("link", {}) or {}
    timeout_s = int(cfg.get("link_recovery_timeout_s", link.get("health_check_timeout_s", 90)))
    poll_s = max(2, int(cfg.get("link_recovery_poll_s", 3)))
    bts_idx = int(link.get("radio_idx", 1))

    ok, clients = await link_health_bts(bts_ssh, profile)
    diag = await read_bts_wireless_diag(bts_ssh, profile)
    notes.append(
        f"link check: up={ok} clients={clients} "
        f"ssid={diag.get('ssid', '')} tx={diag.get('tx_power', '')}"
    )
    if ok:
        return True

    archive = _archive_path(profile, role="bts")
    if archive:
        for issue in inspect_backup_for_link_issues(archive):
            notes.append(f"link recovery WARN backup: {issue}")

    notes.append("link down — applying SSID/key/txpower on BTS + CPE via fallback SSH")
    creds = None
    if link_auto_enabled(profile):
        creds = await ensure_p2mp_link_credentials(bts_ssh=bts_ssh, profile=profile)
        notes.append(f"link BTS AIRTEL SSID={creds.ssid} serial={creds.serial}")
    else:
        await ensure_bts_link_ssid_ssh(bts_ssh, radio_idx=bts_idx, profile=profile)
        await apply_bts_tx_power(bts_ssh, profile, radio_idx=bts_idx)
        creds = await resolve_link_credentials_ssh(bts_ssh, profile)
        notes.append(f"link BTS static SSID={creds.ssid}")

    if creds is not None:
        cpe_ok = await ensure_cpe_link_credentials_always(profile, creds, force=True)
        notes.append(f"link CPE credentials via secondary PC ok={cpe_ok}")

    deadline = time.monotonic() + max(10, timeout_s)
    while time.monotonic() < deadline:
        ok, clients = await link_health_bts(bts_ssh, profile)
        if ok:
            notes.append(f"link recovered: {clients} station(s) on BTS")
            return True
        await asyncio.sleep(poll_s)

    diag_after = await read_bts_wireless_diag(bts_ssh, profile)
    notes.append(
        f"link still down after recovery "
        f"(clients={diag_after.get('clients', '0')} ssid={diag_after.get('ssid', '')} "
        f"tx={diag_after.get('tx_power', '')})"
    )
    return False


async def apply_bts_tx_power_from_cfg(ctx) -> str:
    """Set BTS tx power for bench link (ip_tests.link_recovery_tx_power, default 1)."""
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    link = profile.get("link", {}) or {}
    idx = int(link.get("radio_idx", 1))
    tx = str(cfg.get("link_recovery_tx_power", link.get("tx_power_default", "1"))).strip() or "1"
    await ctx.ssh.send_command(
        f"ucidyn set txparam.ath{idx}.atpcpower {shlex.quote(tx)}",
        timeout_ops=30,
    )
    await ctx.ssh.send_command("ucidyn apply", timeout_ops=60)
    ctx.notes.append(f"link-formation: BTS txpower set to {tx}")
    print(f"[link-formation] BTS txpower={tx} (bench RF, separate from IP retain check)")
    return tx


async def run_post_reset_link_formation(ctx) -> bool:
    """
    Post-reset RF bench setup only: tx power + SSID/key sync + client poll.
    Does not change LAN UCI — IP retain verification is separate (IP_12).
    """
    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    if ctx.device_target != "bts" or not cfg.get("enable_link_recovery", True):
        return True

    notes = ctx.notes
    password = str(cfg.get("_password", ""))
    cid = getattr(getattr(ctx, "case", None), "case_id", "IP")

    try:
        from utils.ip_test_flows import _close_ssh, _device_ssh_host_candidates, _wait_ssh_any

        if ctx.ssh is None:
            hosts = _device_ssh_host_candidates(ctx)
            if not hosts:
                notes.append(f"{cid}: link-formation: no SSH hosts")
                return False
            new_ssh, effective = await _wait_ssh_any(hosts, password, timeout_s=90, interval_s=3)
            ctx.ssh = new_ssh
            ctx.host = effective
            notes.append(f"{cid}: link-formation SSH via {effective}")

        await apply_bts_tx_power_from_cfg(ctx)
        return await recover_testbed_link_ssh(
            bts_ssh=ctx.ssh,
            profile=profile,
            password=password,
            notes=notes,
            cfg=cfg,
        )
    except Exception as exc:
        notes.append(f"{cid}: link-formation failed: {exc}")
        print(f"[link-formation] FAIL: {exc}")
        return False


async def ensure_testbed_link_ready(ctx) -> bool:
    """
    Connect BTS on fallback if needed, run link recovery, update ctx.ssh.
    Called before CPE precheck and after destructive IP cases.
    """
    from utils.ip_test_flows import _close_ssh, _device_ssh_host_candidates, _open_ssh, _wait_ssh_any

    cfg = ctx.cfg
    profile = cfg.get("_profile") or {}
    if not cfg.get("enable_link_recovery", True):
        return True

    password = str(cfg.get("_password", ""))
    notes = ctx.notes

    hosts = _device_ssh_host_candidates(ctx)
    if not hosts:
        notes.append("link recovery: no SSH hosts")
        return False

    try:
        if ctx.ssh is not None:
            try:
                ok, _ = await link_health_bts(ctx.ssh, profile)
                if ok:
                    notes.append("link precheck: RF link up on current BTS SSH")
                    return True
            except Exception as exc:
                notes.append(f"link precheck on current SSH failed: {exc}")

        new_ssh, effective = await _wait_ssh_any(hosts, password, timeout_s=90, interval_s=3)
        if ctx.ssh is not None and ctx.ssh is not new_ssh:
            await _close_ssh(ctx.ssh)
        ctx.ssh = new_ssh
        ctx.host = effective
        notes.append(f"link recovery SSH via {effective}")

        return await recover_testbed_link_ssh(
            bts_ssh=ctx.ssh,
            profile=profile,
            password=password,
            notes=notes,
            cfg=cfg,
        )
    except Exception as exc:
        notes.append(f"link recovery failed: {exc}")
        return False
