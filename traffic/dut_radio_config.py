"""Apply DUT radio settings: BTS (bandwidth, DL/UL, MCS) and CPE (MCS only) over SSH."""

from __future__ import annotations

import subprocess
import time
import re

from pages.commands import RootCommands
from traffic.link_stats import fetch_link_clients
from traffic.operating_rate_table import lookup_spec, mcs_number, normalize_bandwidth
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


def _verify_bts_mcs(
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
        raise RuntimeError(f"BTS MCS verify failed on {ip}: " + "; ".join(mismatches))
    print(f"[BTS] MCS verified on {ip}: mcs={actual_mcs}, spatial={actual_spatial}")


def _verify_bts_bandwidth_ratio(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    bandwidth: str,
    dl_ul_percent: str,
) -> None:
    expected_bw = normalize_bandwidth(bandwidth)
    actual_bw = _read_uci(ip, user, password, f"uci get wireless.wifi{radio_idx}.htmode")
    actual_ratio = _read_uci(ip, user, password, f"uci get ath{radio_idx}qos.qoscfg.dlulratio")
    mismatches: list[str] = []
    if expected_bw not in actual_bw.upper():
        mismatches.append(f"htmode expected {expected_bw}, got {actual_bw}")
    if actual_ratio != dl_ul_percent:
        mismatches.append(f"dlulratio expected {dl_ul_percent}, got {actual_ratio}")
    if mismatches:
        raise RuntimeError(f"BTS bandwidth/ratio verify failed on {ip}: " + "; ".join(mismatches))
    print(f"[BTS] Bandwidth/ratio verified on {ip}: htmode={actual_bw}, dlulratio={actual_ratio}")


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


def configure_bts_mcs_only(
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
    """BTS only: fixed MCS (ddrsrate) and spatial stream."""
    uci_mcs = str(mcs_number(mcs_rate))
    print(f"[BTS] Applying MCS on {ip}: mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream}")
    commands = RootCommands.set_mcs_sequence_commands(radio_idx, mcs_rate, spatial_stream, uci_mcs)
    _run_ucidyn_sequence(ip, user, password, commands, ssh_timeout_s=ssh_timeout_s)
    run_ssh_command(ip, user, password, RootCommands.remote_apply_all_su(), timeout_s=ssh_timeout_s)
    if verify:
        _verify_bts_mcs(
            ip,
            user,
            password,
            radio_idx,
            mcs_rate=mcs_rate,
            spatial_stream=spatial_stream,
        )


def configure_bts_bandwidth_ratio(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    ratio: str,
    *,
    ssh_timeout_s: int = 60,
    verify: bool = True,
) -> str:
    """BTS only: htmode and DL/UL ratio (no MCS change)."""
    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
    print(
        f"[BTS] Applying bandwidth/ratio on {ip}: bw={bandwidth}, "
        f"ratio={ratio} (dlulratio={dl_ul_percent})"
    )
    commands: list[str] = []
    commands.extend(RootCommands.set_bandwidth_commands(radio_idx, normalize_bandwidth(bandwidth)))
    commands.extend(RootCommands.set_dl_ul_ratio_commands(radio_idx, dl_ul_percent))
    _run_ucidyn_sequence(ip, user, password, commands, ssh_timeout_s=ssh_timeout_s)
    run_ssh_command(ip, user, password, RootCommands.remote_apply_all_su(), timeout_s=ssh_timeout_s)
    if verify:
        _verify_bts_bandwidth_ratio(
            ip,
            user,
            password,
            radio_idx,
            bandwidth=bandwidth,
            dl_ul_percent=dl_ul_percent,
        )
    return dl_ul_percent


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
    """BTS: htmode, DL/UL ratio, and MCS (ddrsrate)."""
    dl_ul_percent = configure_bts_bandwidth_ratio(
        ip,
        user,
        password,
        radio_idx,
        bandwidth,
        ratio,
        ssh_timeout_s=ssh_timeout_s,
        verify=False,
    )
    configure_bts_mcs_only(
        ip,
        user,
        password,
        radio_idx,
        mcs_rate,
        spatial_stream,
        ssh_timeout_s=ssh_timeout_s,
        verify=False,
    )
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


def _parse_uci_get_output(raw: str) -> str:
    """Extract a UCI scalar from ssh/remote_exec output (may include noise lines)."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in reversed(lines):
        if "=" in line:
            return line.split("=", 1)[-1].strip().strip('"').strip("'")
        if line.isdigit():
            return line
    numbers = re.findall(r"\d+", raw)
    return numbers[-1] if numbers else raw.strip().strip('"').strip("'")


def _read_cpe_mcs_via_remote_exec(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    su_index: int,
    ssh_timeout_s: int,
    attempts: int = 4,
    pause_s: float = 2.0,
) -> str:
    cmd = RootCommands.remote_exec_command(su_index, f"uci get txparam.ath{radio_idx}.ddrsrate")
    last_raw = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(pause_s)
        last_raw = run_ssh_command(bts_ip, user, password, cmd, timeout_s=ssh_timeout_s)
        value = _parse_uci_get_output(last_raw)
        if value.isdigit():
            return value
    return _parse_uci_get_output(last_raw)


def _read_cpe_mcs(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    su_index: int,
    cpe_ip: str | None,
    prefer_bts_relay: bool,
    ssh_timeout_s: int,
) -> tuple[str, str]:
    """Read CPE ddrsrate/spatialstream via BTS relay or remote_exec."""
    if cpe_ip and prefer_bts_relay:
        try:
            mcs = _read_cpe_uci_via_bts(
                bts_ip,
                cpe_ip,
                password,
                f"uci get txparam.ath{radio_idx}.ddrsrate",
                ssh_timeout_s=ssh_timeout_s,
            )
            spatial = _read_cpe_uci_via_bts(
                bts_ip,
                cpe_ip,
                password,
                f"uci get txparam.ath{radio_idx}.spatialstream",
                ssh_timeout_s=ssh_timeout_s,
            )
            return mcs, spatial
        except RuntimeError:
            pass
    mcs = _read_cpe_mcs_via_remote_exec(
        bts_ip,
        user,
        password,
        radio_idx,
        su_index=su_index,
        ssh_timeout_s=ssh_timeout_s,
    )
    spatial_cmd = RootCommands.remote_exec_command(
        su_index, f"uci get txparam.ath{radio_idx}.spatialstream"
    )
    spatial_raw = run_ssh_command(bts_ip, user, password, spatial_cmd, timeout_s=ssh_timeout_s)
    spatial = _parse_uci_get_output(spatial_raw)
    return mcs, spatial


def _cpe_mcs_checks_via_snmp(
    bts_ip: str,
    *,
    expected_mcs: str,
    su_count: int,
    snmp_community: str,
    snmp_radio_idx: int,
) -> list[dict[str, object]]:
    """Read per-SU downlink MCS index from BTS link SNMP (matches device GUI)."""
    clients = fetch_link_clients(
        bts_ip,
        snmp_community=snmp_community,
        radio_idx=snmp_radio_idx,
        source="snmp",
    )
    checks: list[dict[str, object]] = []
    for index, client in enumerate(clients[:su_count], start=1):
        actual_mcs = str(client.get("out_mcs") or "").strip()
        out_rate = str(client.get("out_rate") or client.get("tx_rate") or "")
        ok = actual_mcs == expected_mcs
        checks.append(
            {
                "role": "CPE",
                "label": f"SU{index}",
                "su_index": index,
                "ip": client.get("ip") or "",
                "expected_mcs": expected_mcs,
                "actual_mcs": actual_mcs or out_rate,
                "out_rate": out_rate,
                "source": "snmp",
                "ok": ok,
            }
        )
    return checks


def verify_mcs_all_devices(
    bts_ip: str,
    user: str,
    password: str,
    bts_radio_idx: int,
    cpe_radio_idx: int,
    mcs_rate: str,
    spatial_stream: str,
    *,
    su_count: int,
    cpe_hosts: list[str] | None = None,
    prefer_cpe_via_bts: bool = False,
    ssh_timeout_s: int = 60,
    snmp_community: str | None = None,
    snmp_radio_idx: int = 2,
) -> dict[str, object]:
    """Confirm BTS and every CPE/SU have the same configured MCS (primary gate)."""
    expected_mcs = str(mcs_number(mcs_rate))
    cpe_list = [host.strip() for host in (cpe_hosts or []) if host.strip()]
    checks: list[dict[str, object]] = []

    bts_mcs = _read_uci(bts_ip, user, password, f"uci get txparam.ath{bts_radio_idx}.ddrsrate")
    bts_spatial = _read_uci(
        bts_ip, user, password, f"uci get txparam.ath{bts_radio_idx}.spatialstream"
    )
    checks.append(
        {
            "role": "BTS",
            "label": "BTS",
            "su_index": 0,
            "expected_mcs": expected_mcs,
            "actual_mcs": bts_mcs,
            "spatial_stream": bts_spatial,
            "ok": bts_mcs == expected_mcs and bts_spatial == spatial_stream,
        }
    )

    cpe_remote_indices = _discover_cpe_remote_exec_indices(
        bts_ip, user, password, su_count=su_count, ssh_timeout_s=ssh_timeout_s
    )
    snmp_cpe_checks: list[dict[str, object]] = []
    if snmp_community:
        snmp_cpe_checks = _cpe_mcs_checks_via_snmp(
            bts_ip,
            expected_mcs=expected_mcs,
            su_count=su_count,
            snmp_community=snmp_community,
            snmp_radio_idx=snmp_radio_idx,
        )

    if snmp_cpe_checks and len(snmp_cpe_checks) >= su_count:
        print(f"[MCS] CPE verify via SNMP link table ({len(snmp_cpe_checks)} SU(s))")
        checks.extend(snmp_cpe_checks)
    else:
        if len(cpe_remote_indices) < su_count:
            print(
                f"[WARN] Found {len(cpe_remote_indices)} CPE remote_exec index(es) "
                f"for su_count={su_count}"
            )
        for su_index in range(1, su_count + 1):
            cpe_ip = cpe_list[su_index - 1] if su_index <= len(cpe_list) else None
            remote_idx = (
                cpe_remote_indices[su_index - 1]
                if su_index - 1 < len(cpe_remote_indices)
                else su_index
            )
            if not _remote_exec_is_cpe_target(
                bts_ip, user, password, remote_idx, ssh_timeout_s=ssh_timeout_s
            ):
                checks.append(
                    {
                        "role": "CPE",
                        "label": f"SU{su_index}",
                        "su_index": su_index,
                        "ip": cpe_ip or "",
                        "expected_mcs": expected_mcs,
                        "actual_mcs": "?",
                        "spatial_stream": "",
                        "ok": False,
                        "error": f"remote_exec SU{remote_idx} does not reach CPE",
                    }
                )
                continue
            actual_mcs, actual_spatial = _read_cpe_mcs(
                bts_ip,
                user,
                password,
                cpe_radio_idx,
                su_index=remote_idx,
                cpe_ip=cpe_ip,
                prefer_bts_relay=False,
                ssh_timeout_s=ssh_timeout_s,
            )
            checks.append(
                {
                    "role": "CPE",
                    "label": f"SU{su_index}",
                    "su_index": su_index,
                    "ip": cpe_ip or "",
                    "expected_mcs": expected_mcs,
                    "actual_mcs": actual_mcs,
                    "spatial_stream": actual_spatial,
                    "source": "remote_exec",
                    "ok": actual_mcs == expected_mcs
                    and (not actual_spatial or actual_spatial == spatial_stream),
                }
            )

    all_ok = all(bool(row.get("ok")) for row in checks)
    for row in checks:
        status = "OK" if row.get("ok") else "MISMATCH"
        print(
            f"[MCS] {row['label']}: expected={expected_mcs}, "
            f"actual={row.get('actual_mcs')} ({status})"
        )
    if all_ok:
        print(f"[MCS] All {len(checks)} device(s) configured with {mcs_rate} (uci={expected_mcs})")
    else:
        bad = [str(row["label"]) for row in checks if not row.get("ok")]
        print(f"[MCS] MISMATCH on: {', '.join(bad)}")

    return {
        "configured_mcs": mcs_rate,
        "expected_uci_mcs": expected_mcs,
        "spatial_stream": spatial_stream,
        "checks": checks,
        "mcs_config_ok": all_ok,
    }


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
    verify_strict: bool = True,
    broadcast: bool = True,
) -> None:
    """Push MCS through BTS remote_exec (broadcast to BTS + all CPEs by default)."""
    if broadcast:
        configure_mcs_broadcast_bts_and_all_cpes(
            bts_ip,
            user,
            password,
            radio_idx,
            mcs_rate,
            spatial_stream,
            ssh_timeout_s=ssh_timeout_s,
            verify_bts=False,
        )
        if not verify:
            return
        actual_mcs = _read_cpe_mcs_via_remote_exec(
            bts_ip,
            user,
            password,
            radio_idx,
            su_index=su_index,
            ssh_timeout_s=ssh_timeout_s,
        )
        if _remote_exec_is_cpe_target(bts_ip, user, password, su_index, ssh_timeout_s=ssh_timeout_s):
            try:
                _verify_cpe_mcs_on_device(
                    label=f"remote_exec SU{su_index}",
                    read_mcs=actual_mcs,
                    read_spatial=spatial_stream,
                    mcs_rate=mcs_rate,
                    spatial_stream=spatial_stream,
                )
            except RuntimeError as exc:
                if verify_strict:
                    raise
                print(f"[WARN] {exc} — continuing; final MCS verify will confirm")
        else:
            print(
                f"[WARN] remote_exec SU{su_index} is BTS-local — "
                f"skipping per-SU verify (final MCS verify uses CPE indices)"
            )
        return

    uci_mcs = str(mcs_number(mcs_rate))
    print(
        f"[CPE] Applying via BTS remote_exec (SU{su_index}): "
        f"mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream}"
    )
    commands = RootCommands.mcs_ucidyn_set_commands(radio_idx, mcs_rate, spatial_stream, uci_mcs)
    for inner in commands:
        remote = RootCommands.remote_exec_command(su_index, inner)
        run_ssh_command(bts_ip, user, password, remote, timeout_s=ssh_timeout_s)
    run_ssh_command(bts_ip, user, password, RootCommands.remote_apply_all_su(), timeout_s=ssh_timeout_s)
    time.sleep(2.0)
    if verify and _remote_exec_is_cpe_target(
        bts_ip, user, password, su_index, ssh_timeout_s=ssh_timeout_s
    ):
        actual_mcs = _read_cpe_mcs_via_remote_exec(
            bts_ip,
            user,
            password,
            radio_idx,
            su_index=su_index,
            ssh_timeout_s=ssh_timeout_s,
        )
        try:
            _verify_cpe_mcs_on_device(
                label=f"remote_exec SU{su_index}",
                read_mcs=actual_mcs,
                read_spatial=spatial_stream,
                mcs_rate=mcs_rate,
                spatial_stream=spatial_stream,
            )
        except RuntimeError as exc:
            if verify_strict:
                raise
            print(f"[WARN] {exc} — continuing; final MCS verify will confirm")


DEFAULT_REMOTE_EXEC_BROADCAST_SU = 1


def _norm_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", str(mac).lower())


def _read_br_lan_mac(ip: str, user: str, password: str) -> str:
    raw = run_ssh_command(ip, user, password, "cat /sys/class/net/br-lan/address")
    return _norm_mac(raw)


def _remote_exec_is_cpe_target(
    bts_ip: str,
    user: str,
    password: str,
    su_index: int,
    *,
    bts_mac: str = "",
    ssh_timeout_s: int = 60,
) -> bool:
    """True when remote_exec SU index reaches a CPE (not BTS br-lan)."""
    probe_cmd = RootCommands.remote_exec_command(su_index, "echo REMOTE_EXEC_OK")
    try:
        probe = run_ssh_command(bts_ip, user, password, probe_cmd, timeout_s=ssh_timeout_s)
    except RuntimeError:
        return False
    if "REMOTE_EXEC_OK" not in probe:
        return False
    if not bts_mac:
        bts_mac = _read_br_lan_mac(bts_ip, user, password)
    peer_cmd = RootCommands.remote_exec_command(su_index, "cat /sys/class/net/br-lan/address")
    try:
        peer_raw = run_ssh_command(bts_ip, user, password, peer_cmd, timeout_s=ssh_timeout_s)
    except RuntimeError:
        return False
    peer_mac = _norm_mac(peer_raw)
    return bool(peer_mac) and peer_mac != bts_mac


def _discover_cpe_remote_exec_indices(
    bts_ip: str,
    user: str,
    password: str,
    *,
    su_count: int,
    ssh_timeout_s: int = 60,
) -> list[int]:
    """Find remote_exec indices that reach distinct CPEs (not BTS)."""
    bts_mac = _read_br_lan_mac(bts_ip, user, password)
    found: list[int] = []
    seen_macs: set[str] = set()
    for idx in range(1, 33):
        if not _remote_exec_is_cpe_target(
            bts_ip, user, password, idx, bts_mac=bts_mac, ssh_timeout_s=ssh_timeout_s
        ):
            continue
        peer_cmd = RootCommands.remote_exec_command(idx, "cat /sys/class/net/br-lan/address")
        try:
            peer_mac = _norm_mac(
                run_ssh_command(bts_ip, user, password, peer_cmd, timeout_s=ssh_timeout_s)
            )
        except RuntimeError:
            continue
        if peer_mac in seen_macs:
            continue
        seen_macs.add(peer_mac)
        found.append(idx)
        if len(found) >= su_count:
            break
    return found


def configure_mcs_broadcast_bts_and_all_cpes(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    mcs_rate: str,
    spatial_stream: str = "2",
    *,
    ssh_timeout_s: int = 60,
    verify_bts: bool = False,
) -> None:
    """
    Apply MCS on BTS and every connected CPE via remote_exec.sh 1 broadcast.

    On PTMP BTS, ``remote_exec.sh 1 "<ucidyn set ...>"`` fans the same command out to
    the BTS and all connected CPEs. Apply once at the end with ``remote_exec.sh 1 ucidyn apply``.
    """
    uci_mcs = str(mcs_number(mcs_rate))
    print(
        f"[MCS] Broadcasting via remote_exec.sh {DEFAULT_REMOTE_EXEC_BROADCAST_SU}: "
        f"mcs={mcs_rate} (uci={uci_mcs}), spatial={spatial_stream} → BTS + all CPEs"
    )
    set_commands = RootCommands.mcs_ucidyn_set_commands(
        radio_idx, mcs_rate, spatial_stream, uci_mcs
    )
    for inner in set_commands:
        remote = RootCommands.remote_exec_command(DEFAULT_REMOTE_EXEC_BROADCAST_SU, inner)
        run_ssh_command(bts_ip, user, password, remote, timeout_s=ssh_timeout_s)
        time.sleep(0.5)
    run_ssh_command(
        bts_ip,
        user,
        password,
        RootCommands.remote_apply_all_su(),
        timeout_s=ssh_timeout_s,
    )
    time.sleep(2.0)
    if verify_bts:
        _verify_bts_mcs(
            bts_ip,
            user,
            password,
            radio_idx,
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
    verify_strict = not prefer_bts_relay

    if prefer_bts_relay and cpe_ip:
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
            print(f"[CPE] BTS→CPE SSH to {cpe_ip} failed, trying remote_exec SU{su_index}...")

    if prefer_bts_relay:
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
            verify_strict=verify_strict,
        )
        _push_cpe_link_apply(bts_ip, user, password, ssh_timeout_s=ssh_timeout_s)
        return

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
            verify_strict=verify_strict,
        )
    except RuntimeError as exc:
        errors.append(f"remote_exec: {exc}")
        raise RuntimeError("All CPE MCS apply paths failed: " + " | ".join(errors)) from exc


def _apply_mcs_all_cpes(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    mcs_rate: str,
    spatial_stream: str,
    *,
    cpe_hosts: list[str] | None,
    su_count: int,
    prefer_cpe_via_bts: bool,
    ssh_timeout_s: int,
    verify: bool,
) -> None:
    cpe_list = [host.strip() for host in (cpe_hosts or []) if host.strip()]
    if prefer_cpe_via_bts or not cpe_list:
        configure_mcs_broadcast_bts_and_all_cpes(
            bts_ip,
            user,
            password,
            radio_idx,
            mcs_rate,
            spatial_stream,
            ssh_timeout_s=ssh_timeout_s,
            verify_bts=verify,
        )
        return
    for su_index, cpe_ip in enumerate(cpe_list, start=1):
        _apply_cpe_mcs(
            bts_ip,
            cpe_ip,
            user,
            password,
            radio_idx,
            mcs_rate,
            spatial_stream,
            su_index=su_index,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
            prefer_bts_relay=prefer_cpe_via_bts,
        )


def _reapply_mcs_all_devices(
    bts_ip: str,
    user: str,
    password: str,
    bts_radio_idx: int,
    cpe_radio_idx: int,
    mcs_rate: str,
    spatial_stream: str,
    *,
    su_count: int,
    ssh_timeout_s: int = 60,
) -> None:
    """Re-push MCS to BTS and every CPE after bandwidth/ratio change."""
    configure_mcs_broadcast_bts_and_all_cpes(
        bts_ip,
        user,
        password,
        bts_radio_idx,
        mcs_rate,
        spatial_stream,
        ssh_timeout_s=ssh_timeout_s,
        verify_bts=False,
    )


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
    su_count: int = 1,
    prefer_cpe_via_bts: bool = False,
    settle_s: float = 4.0,
    ssh_timeout_s: int = 60,
    verify: bool = True,
    snmp_community: str | None = None,
    snmp_radio_idx: int = 2,
) -> dict[str, object]:
    """
    Apply radio profile with MCS consistency as the primary gate.

    1. MCS on BTS + all CPEs
    2. BTS bandwidth + DL:UL ratio
    3. Re-sync MCS on BTS + all CPEs after BW/ratio change
    4. Verify every device has the same configured MCS

    Operating link rate is validated separately (secondary) by the matrix runner.
    """
    effective_settle = _settle_seconds(bandwidth, settle_s)
    cpe_radio = cpe_radio_idx if cpe_radio_idx is not None else radio_idx
    spec = lookup_spec(mcs_rate, bandwidth, spatial_streams=int(spatial_stream))
    effective_su_count = max(su_count, len([h for h in (cpe_hosts or []) if h.strip()]), 1)
    print(
        f"[CONFIG] Target MCS {spec['mcs']} ({spec['modulation']}); "
        f"operating rate ~{spec['operating_rate_mbps']:.0f} Mbps checked after config"
    )

    print(f"[CONFIG] Step 1/4: MCS={mcs_rate} on BTS + {effective_su_count} CPE(s)")
    if prefer_cpe_via_bts:
        configure_mcs_broadcast_bts_and_all_cpes(
            bts_ip,
            user,
            password,
            radio_idx,
            mcs_rate,
            spatial_stream,
            ssh_timeout_s=ssh_timeout_s,
            verify_bts=verify,
        )
    else:
        configure_bts_mcs_only(
            bts_ip,
            user,
            password,
            radio_idx,
            mcs_rate,
            spatial_stream,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
        )
        _apply_mcs_all_cpes(
            bts_ip,
            user,
            password,
            cpe_radio,
            mcs_rate,
            spatial_stream,
            cpe_hosts=cpe_hosts,
            su_count=effective_su_count,
            prefer_cpe_via_bts=prefer_cpe_via_bts,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
        )

    print(f"[CONFIG] Step 2/4: BTS bw={bandwidth}, DL:UL ratio={ratio}")
    configure_bts_bandwidth_ratio(
        bts_ip,
        user,
        password,
        radio_idx,
        bandwidth,
        ratio,
        ssh_timeout_s=ssh_timeout_s,
        verify=verify,
    )

    print(f"[CONFIG] Step 3/4: Re-sync MCS on BTS + SU1–SU{effective_su_count}")
    _reapply_mcs_all_devices(
        bts_ip,
        user,
        password,
        radio_idx,
        cpe_radio,
        mcs_rate,
        spatial_stream,
        su_count=effective_su_count,
        ssh_timeout_s=ssh_timeout_s,
    )

    print("[CONFIG] Step 4/4: Verify MCS on all devices")
    mcs_report = verify_mcs_all_devices(
        bts_ip,
        user,
        password,
        radio_idx,
        cpe_radio,
        mcs_rate,
        spatial_stream,
        su_count=effective_su_count,
        cpe_hosts=cpe_hosts,
        prefer_cpe_via_bts=prefer_cpe_via_bts,
        ssh_timeout_s=ssh_timeout_s,
        snmp_community=snmp_community,
        snmp_radio_idx=snmp_radio_idx,
    )
    if verify and not mcs_report.get("mcs_config_ok"):
        bad = [
            str(row.get("label"))
            for row in (mcs_report.get("checks") or [])
            if not row.get("ok")
        ]
        raise RuntimeError(
            f"MCS config mismatch — not all devices have {mcs_rate}: {', '.join(bad)}"
        )

    time.sleep(effective_settle)
    return mcs_report


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
