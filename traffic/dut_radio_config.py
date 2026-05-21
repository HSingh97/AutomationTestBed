"""Apply DUT radio settings: BTS (bandwidth, DL/UL, MCS) and CPE (MCS only) over SSH."""

from __future__ import annotations

import subprocess
import time

from pages.commands import RootCommands
from traffic.operating_rate_table import mcs_number, normalize_bandwidth
from utils.net_utils import format_ssh_host, is_ipv6_literal, normalize_ip


def ratio_to_uci_dl_percent(ratio: str) -> str:
    """Map DL:UL (e.g. 75:25) to ath*qos dlulratio UCI value (downlink percent)."""
    dl_part, ul_part = ratio.split(":")
    dl_val = float(dl_part)
    ul_val = float(ul_part)
    total = dl_val + ul_val
    if total <= 0:
        raise ValueError(f"Invalid ratio '{ratio}'")
    return str(int(round(100 * dl_val / total)))


def run_ssh_command(ip: str, user: str, password: str, command: str, *, timeout_s: int = 30) -> str:
    ssh_opts = (
        "-T -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o ConnectTimeout=12"
    )
    ssh_target = normalize_ip(ip)
    if is_ipv6_literal(ssh_target):
        target = f"-6 -l {user} {ssh_target}"
    else:
        target = f"{user}@{format_ssh_host(ip)}"
    ssh_cmd = f"sshpass -p '{password}' ssh {ssh_opts} {target} {command!r}"
    try:
        return subprocess.check_output(
            ssh_cmd,
            shell=True,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            text=True,
        ).strip()
    except subprocess.CalledProcessError as exc:
        output = (exc.output or "").strip()
        raise RuntimeError(f"SSH command failed on {ip}: {output}") from exc


def _run_ucidyn_sequence(
    ip: str,
    user: str,
    password: str,
    commands: list[str],
    *,
    ssh_timeout_s: int = 60,
) -> None:
    for cmd in commands:
        run_ssh_command(ip, user, password, cmd, timeout_s=ssh_timeout_s)


def _read_uci(ip: str, user: str, password: str, cmd: str) -> str:
    raw = run_ssh_command(ip, user, password, cmd)
    if "=" in raw:
        raw = raw.split("=", 1)[-1].strip()
    return raw.strip().strip('"')


def _cpe_ssh_relay_command(cpe_ip: str, password: str, inner_command: str) -> str:
    """Run a command on CPE by hopping through the BTS (lab PC often has no route to CPE)."""
    cpe_host = format_ssh_host(normalize_ip(cpe_ip))
    return (
        f"sshpass -p '{password}' ssh -6 -T -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 "
        f"root@{cpe_host} {inner_command!r}"
    )


def run_ssh_via_bts(
    bts_ip: str,
    cpe_ip: str,
    user: str,
    password: str,
    command: str,
    *,
    ssh_timeout_s: int = 60,
) -> str:
    relay = _cpe_ssh_relay_command(cpe_ip, password, command)
    return run_ssh_command(bts_ip, user, password, relay, timeout_s=ssh_timeout_s)


def _read_cpe_uci_via_bts(
    bts_ip: str,
    cpe_ip: str,
    password: str,
    cmd: str,
    *,
    ssh_timeout_s: int = 60,
) -> str:
    raw = run_ssh_via_bts(bts_ip, cpe_ip, "root", password, cmd, ssh_timeout_s=ssh_timeout_s)
    if "=" in raw:
        raw = raw.split("=", 1)[-1].strip()
    return raw.strip().strip('"')


def _verify_bts_config(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    bandwidth: str,
    mcs_rate: str,
    dl_ul_percent: str,
    spatial_stream: str,
) -> None:
    expected_bw = normalize_bandwidth(bandwidth)
    expected_mcs = str(mcs_number(mcs_rate))
    actual_bw = _read_uci(ip, user, password, f"uci get wireless.wifi{radio_idx}.htmode")
    actual_mcs = _read_uci(ip, user, password, f"uci get txparam.ath{radio_idx}.ddrsrate")
    actual_ratio = _read_uci(ip, user, password, f"uci get ath{radio_idx}qos.qoscfg.dlulratio")
    actual_spatial = _read_uci(ip, user, password, f"uci get txparam.ath{radio_idx}.spatialstream")

    mismatches: list[str] = []
    if expected_bw not in actual_bw.upper():
        mismatches.append(f"htmode expected {expected_bw}, got {actual_bw}")
    if actual_mcs != expected_mcs:
        mismatches.append(f"ddrsrate expected {expected_mcs}, got {actual_mcs}")
    if actual_ratio != dl_ul_percent:
        mismatches.append(f"dlulratio expected {dl_ul_percent}, got {actual_ratio}")
    if actual_spatial != spatial_stream:
        mismatches.append(f"spatialstream expected {spatial_stream}, got {actual_spatial}")
    if mismatches:
        raise RuntimeError(f"BTS config verify failed on {ip}: " + "; ".join(mismatches))
    print(
        f"[BTS] Verified on {ip}: htmode={actual_bw}, mcs={actual_mcs}, "
        f"dlulratio={actual_ratio}, spatial={actual_spatial}"
    )


def _verify_cpe_mcs(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    mcs_rate: str,
    spatial_stream: str,
) -> None:
    expected_mcs = str(mcs_number(mcs_rate))
    actual_mcs = _read_uci(ip, user, password, f"uci get txparam.ath{radio_idx}.ddrsrate")
    actual_spatial = _read_uci(ip, user, password, f"uci get txparam.ath{radio_idx}.spatialstream")
    mismatches: list[str] = []
    if actual_mcs != expected_mcs:
        mismatches.append(f"ddrsrate expected {expected_mcs}, got {actual_mcs}")
    if actual_spatial != spatial_stream:
        mismatches.append(f"spatialstream expected {spatial_stream}, got {actual_spatial}")
    if mismatches:
        raise RuntimeError(f"CPE config verify failed on {ip}: " + "; ".join(mismatches))
    print(f"[CPE] Verified on {ip}: mcs={actual_mcs}, spatial={actual_spatial}")


def _settle_seconds(bandwidth: str, base_s: float) -> float:
    bw = normalize_bandwidth(bandwidth)
    if bw in ("HT80", "HT160"):
        return max(base_s, 8.0)
    if bw == "HT40":
        return max(base_s, 6.0)
    return base_s


def configure_bts_radio(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    mcs_rate: str,
    ratio: str,
    spatial_stream: str = "2",
    *,
    ssh_timeout_s: int = 60,
    verify: bool = True,
) -> str:
    """BTS only: htmode, DL/UL ratio, and MCS (ddrsrate)."""
    uci_mcs = str(mcs_number(mcs_rate))
    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
    print(
        f"[BTS] Applying on {ip}: bw={bandwidth}, ratio={ratio} (dlulratio={dl_ul_percent}), "
        f"mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream}"
    )
    commands: list[str] = []
    commands.extend(RootCommands.set_bandwidth_commands(radio_idx, normalize_bandwidth(bandwidth)))
    commands.extend(RootCommands.set_dl_ul_ratio_commands(radio_idx, dl_ul_percent))
    commands.extend(
        RootCommands.set_mcs_sequence_commands(radio_idx, mcs_rate, spatial_stream, uci_mcs)
    )
    _run_ucidyn_sequence(ip, user, password, commands, ssh_timeout_s=ssh_timeout_s)
    run_ssh_command(ip, user, password, RootCommands.remote_apply_all_su(), timeout_s=ssh_timeout_s)
    if verify:
        _verify_bts_config(
            ip,
            user,
            password,
            radio_idx,
            bandwidth=bandwidth,
            mcs_rate=mcs_rate,
            dl_ul_percent=dl_ul_percent,
            spatial_stream=spatial_stream,
        )
    return dl_ul_percent


def configure_cpe_mcs(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    mcs_rate: str,
    spatial_stream: str = "2",
    *,
    ssh_timeout_s: int = 60,
    verify: bool = True,
) -> None:
    """CPE only: fixed MCS (ddrsrate) and spatial stream — no bandwidth or DL/UL."""
    uci_mcs = str(mcs_number(mcs_rate))
    print(f"[CPE] Applying on {ip}: mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream}")
    commands = RootCommands.set_mcs_sequence_commands(radio_idx, mcs_rate, spatial_stream, uci_mcs)
    _run_ucidyn_sequence(ip, user, password, commands, ssh_timeout_s=ssh_timeout_s)
    if verify:
        _verify_cpe_mcs(
            ip,
            user,
            password,
            radio_idx,
            mcs_rate=mcs_rate,
            spatial_stream=spatial_stream,
        )


def _verify_cpe_mcs_on_device(
    *,
    label: str,
    read_mcs: str,
    read_spatial: str,
    mcs_rate: str,
    spatial_stream: str,
) -> None:
    expected_mcs = str(mcs_number(mcs_rate))
    mismatches: list[str] = []
    if read_mcs != expected_mcs:
        mismatches.append(f"ddrsrate expected {expected_mcs}, got {read_mcs}")
    if read_spatial != spatial_stream:
        mismatches.append(f"spatialstream expected {spatial_stream}, got {read_spatial}")
    if mismatches:
        raise RuntimeError(f"CPE config verify failed ({label}): " + "; ".join(mismatches))
    print(f"[CPE] Verified ({label}): mcs={read_mcs}, spatial={read_spatial}")


def configure_cpe_mcs_via_bts_ssh(
    bts_ip: str,
    cpe_ip: str,
    password: str,
    radio_idx: int,
    mcs_rate: str,
    spatial_stream: str = "2",
    *,
    ssh_timeout_s: int = 60,
    verify: bool = True,
) -> None:
    """Apply MCS on CPE by SSH from BTS → CPE (preferred when PC cannot reach CPE)."""
    uci_mcs = str(mcs_number(mcs_rate))
    print(
        f"[CPE] Applying via BTS→CPE SSH to {cpe_ip}: "
        f"mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream}"
    )
    commands = RootCommands.set_mcs_sequence_commands(radio_idx, mcs_rate, spatial_stream, uci_mcs)
    for cmd in commands:
        run_ssh_via_bts(bts_ip, cpe_ip, "root", password, cmd, ssh_timeout_s=ssh_timeout_s)
    if verify:
        actual_mcs = _read_cpe_uci_via_bts(
            bts_ip,
            cpe_ip,
            password,
            f"uci get txparam.ath{radio_idx}.ddrsrate",
            ssh_timeout_s=ssh_timeout_s,
        )
        actual_spatial = _read_cpe_uci_via_bts(
            bts_ip,
            cpe_ip,
            password,
            f"uci get txparam.ath{radio_idx}.spatialstream",
            ssh_timeout_s=ssh_timeout_s,
        )
        _verify_cpe_mcs_on_device(
            label=f"BTS→CPE SSH {cpe_ip}",
            read_mcs=actual_mcs,
            read_spatial=actual_spatial,
            mcs_rate=mcs_rate,
            spatial_stream=spatial_stream,
        )


def configure_cpe_mcs_via_bts_remote_exec(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    mcs_rate: str,
    spatial_stream: str = "2",
    *,
    su_index: int = 1,
    ssh_timeout_s: int = 60,
    verify: bool = True,
) -> None:
    """Fallback: push MCS to CPE through BTS remote_exec (single batched command)."""
    uci_mcs = str(mcs_number(mcs_rate))
    print(
        f"[CPE] Applying via BTS remote_exec (SU{su_index}): "
        f"mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream}"
    )
    batched = (
        f"ucidyn set txparam.ath{radio_idx}.ddrsstatus 0 && "
        f"ucidyn set txparam.ath{radio_idx}.spatialstream {spatial_stream} && "
        f"ucidyn set txparam.ath{radio_idx}.ddrsrate {uci_mcs} && "
        "ucidyn apply"
    )
    remote = RootCommands.remote_exec_command(su_index, batched)
    run_ssh_command(bts_ip, user, password, remote, timeout_s=ssh_timeout_s)
    run_ssh_command(bts_ip, user, password, RootCommands.remote_apply_all_su(), timeout_s=ssh_timeout_s)
    if verify:
        cmd = RootCommands.remote_exec_command(su_index, f"uci get txparam.ath{radio_idx}.ddrsrate")
        raw = run_ssh_command(bts_ip, user, password, cmd, timeout_s=ssh_timeout_s)
        actual_mcs = raw.strip().strip('"').split("\n")[-1].strip()
        if "=" in actual_mcs:
            actual_mcs = actual_mcs.split("=", 1)[-1].strip()
        _verify_cpe_mcs_on_device(
            label=f"remote_exec SU{su_index}",
            read_mcs=actual_mcs,
            read_spatial=spatial_stream,
            mcs_rate=mcs_rate,
            spatial_stream=spatial_stream,
        )


def _push_cpe_link_apply(bts_ip: str, user: str, password: str, *, ssh_timeout_s: int) -> None:
    run_ssh_command(bts_ip, user, password, RootCommands.remote_apply_all_su(), timeout_s=ssh_timeout_s)


def _apply_cpe_mcs(
    bts_ip: str,
    cpe_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    mcs_rate: str,
    spatial_stream: str,
    *,
    su_index: int,
    ssh_timeout_s: int,
    verify: bool,
    prefer_bts_relay: bool,
) -> None:
    errors: list[str] = []

    if cpe_ip and not prefer_bts_relay:
        try:
            configure_cpe_mcs(
                cpe_ip,
                user,
                password,
                radio_idx,
                mcs_rate,
                spatial_stream,
                ssh_timeout_s=ssh_timeout_s,
                verify=verify,
            )
            _push_cpe_link_apply(bts_ip, user, password, ssh_timeout_s=ssh_timeout_s)
            return
        except RuntimeError as exc:
            errors.append(f"direct SSH: {exc}")

    if cpe_ip:
        try:
            configure_cpe_mcs_via_bts_ssh(
                bts_ip,
                cpe_ip,
                password,
                radio_idx,
                mcs_rate,
                spatial_stream,
                ssh_timeout_s=ssh_timeout_s,
                verify=verify,
            )
            _push_cpe_link_apply(bts_ip, user, password, ssh_timeout_s=ssh_timeout_s)
            return
        except RuntimeError as exc:
            errors.append(f"BTS→CPE SSH: {exc}")

    print("[CPE] Falling back to BTS remote_exec...")
    try:
        configure_cpe_mcs_via_bts_remote_exec(
            bts_ip,
            user,
            password,
            radio_idx,
            mcs_rate,
            spatial_stream,
            su_index=su_index,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
        )
    except RuntimeError as exc:
        errors.append(f"remote_exec: {exc}")
        raise RuntimeError("All CPE MCS apply paths failed: " + " | ".join(errors)) from exc


def configure_radio_profile(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    mcs_rate: str,
    ratio: str,
    spatial_stream: str = "2",
    *,
    cpe_hosts: list[str] | None = None,
    cpe_radio_idx: int | None = None,
    cpe_su_index: int = 1,
    prefer_cpe_via_bts: bool = False,
    settle_s: float = 4.0,
    ssh_timeout_s: int = 60,
    verify: bool = True,
) -> None:
    """
    Apply radio profile across the link (order matters):
      1. CPE(s): MCS only
      2. BTS: bandwidth + DL/UL ratio + MCS
    """
    effective_settle = _settle_seconds(bandwidth, settle_s)
    cpe_radio = cpe_radio_idx if cpe_radio_idx is not None else radio_idx
    cpe_list = [host.strip() for host in (cpe_hosts or []) if host.strip()]

    print(f"[CONFIG] Step 1/2: CPE MCS={mcs_rate}")
    if not cpe_list:
        print("[WARN] No CPE hosts in profile — using BTS remote_exec only for CPE MCS")
        configure_cpe_mcs_via_bts_remote_exec(
            bts_ip,
            user,
            password,
            cpe_radio,
            mcs_rate,
            spatial_stream,
            su_index=cpe_su_index,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
        )
    else:
        for cpe_ip in cpe_list:
            _apply_cpe_mcs(
                bts_ip,
                cpe_ip,
                user,
                password,
                cpe_radio,
                mcs_rate,
                spatial_stream,
                su_index=cpe_su_index,
                ssh_timeout_s=ssh_timeout_s,
                verify=verify,
                prefer_bts_relay=prefer_cpe_via_bts,
            )

    print(f"[CONFIG] Step 2/2: BTS bw={bandwidth}, ratio={ratio}, MCS={mcs_rate}")
    configure_bts_radio(
        bts_ip,
        user,
        password,
        radio_idx,
        bandwidth,
        mcs_rate,
        ratio,
        spatial_stream,
        ssh_timeout_s=ssh_timeout_s,
        verify=verify,
    )
    _push_cpe_link_apply(bts_ip, user, password, ssh_timeout_s=ssh_timeout_s)

    time.sleep(effective_settle)


def configure_bandwidth_and_mcs(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    mcs_rate: str,
    spatial_stream: str = "2",
    ddrs_rate: str | None = None,
    *,
    ratio: str = "50:50",
    cpe_hosts: list[str] | None = None,
    settle_s: float = 4.0,
    ssh_timeout_s: int = 60,
) -> None:
    """Backward-compatible wrapper."""
    configure_radio_profile(
        ip,
        user,
        password,
        radio_idx,
        bandwidth,
        mcs_rate,
        ratio,
        spatial_stream,
        cpe_hosts=cpe_hosts,
        settle_s=settle_s,
        ssh_timeout_s=ssh_timeout_s,
    )
