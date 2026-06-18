"""Apply DUT radio settings: BTS (bandwidth, DL/UL, MCS) and CPE (MCS only) over SSH."""

from __future__ import annotations

import ipaddress
import shlex
import subprocess
import time
import re

from pages.commands import RootCommands
from traffic.operating_rate_table import lookup_spec, mcs_number, normalize_bandwidth, uci_htmode_matches, uci_htmode_value
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


def parse_running_htmode(get_mode_output: str) -> str | None:
    """Map ``cfg80211tool athN get_mode`` output (e.g. ``11AHE20``, ``11AHE40PLUS``) to HT modes."""
    text = str(get_mode_output or "").strip().upper()
    if ":" in text:
        text = text.split(":")[-1].strip()
    for width in ("160", "80", "40", "20"):
        if f"HE{width}" in text or f"VHT{width}" in text or f"HT{width}" in text:
            return f"HT{width}"
    match = re.search(r"(?:HE|VHT|HT)(\d+)", text)
    if match:
        return f"HT{match.group(1)}"
    return None


def _fetch_cfg80211_mode(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
) -> tuple[str, str | None]:
    raw = run_ssh_command(
        ip,
        user,
        password,
        RootCommands.get_bandwidth(radio_idx),
        timeout_s=20,
    )
    return raw, parse_running_htmode(raw)


def _log_cfg80211_mode(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    label: str = "",
) -> tuple[str, str | None]:
    raw, parsed = _fetch_cfg80211_mode(ip, user, password, radio_idx)
    tag = f" ({label})" if label else ""
    print(
        f"[BTS] cfg80211tool ath{radio_idx} get_mode{tag}: {raw} "
        f"-> {parsed or 'unparsed'}"
    )
    return raw, parsed


def _extract_get_mode_line(output: str) -> str:
    for line in str(output or "").splitlines():
        if "get_mode" in line.lower():
            return line.strip()
    return ""


def _read_running_bandwidth(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
) -> str | None:
    _, parsed = _fetch_cfg80211_mode(ip, user, password, radio_idx)
    return parsed


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
    ssh_cmd = (
        f"sshpass -p {shlex.quote(password)} ssh {ssh_opts} {target} "
        f"{shlex.quote(command)}"
    )
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


def run_ssh_bash_session(
    ip: str,
    user: str,
    password: str,
    lines: list[str],
    *,
    timeout_s: int = 120,
) -> str:
    """Run multiple shell lines in one SSH session (wait/sleep must complete before exit)."""
    script = "set -e\n" + "\n".join(lines)
    return run_ssh_command(
        ip,
        user,
        password,
        f"sh -lc {shlex.quote(script)}",
        timeout_s=timeout_s,
    )


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
    """Run a command on CPE from the BTS shell (lab PC → BTS → CPE)."""
    escaped = inner_command.replace("'", "'\"'\"'")
    host = normalize_ip(cpe_ip)
    ipv6_opt = "-6 " if is_ipv6_literal(host) else ""
    return (
        f"sshpass -p '{password}' ssh {ipv6_opt}-T -o LogLevel=ERROR -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 "
        f"root@{host} '{escaped}'"
    )


def _remote_exec_bts_command(su_index: int, inner_command: str) -> str:
    """remote_exec with single-quoted inner command (safe on BTS shell)."""
    escaped = inner_command.replace("'", "'\"'\"'")
    return f"/usr/sbin/remote_exec.sh {su_index} '{escaped}'"


def _bts_has_sshpass(
    bts_ip: str,
    user: str,
    password: str,
    *,
    ssh_timeout_s: int = 30,
) -> bool:
    try:
        out = run_ssh_command(
            bts_ip,
            user,
            password,
            "command -v sshpass 2>/dev/null || which sshpass 2>/dev/null",
            timeout_s=ssh_timeout_s,
        )
        return bool(out.strip())
    except RuntimeError:
        return False


def _ip_matches(expected: str, observed: str) -> bool:
    if not expected or not observed or observed.strip() in {"", "-"}:
        return False
    observed_clean = observed.strip().split()[0]
    try:
        left = ipaddress.ip_address(normalize_ip(expected))
        right = ipaddress.ip_address(normalize_ip(observed_clean))
        return left == right
    except ValueError:
        exp = normalize_ip(expected)
        obs = normalize_ip(observed_clean)
        return exp == obs or exp in obs or obs in exp


def _bts_ssh_field(
    bts_ip: str,
    user: str,
    password: str,
    command: str,
    *,
    ssh_timeout_s: int = 60,
) -> str:
    raw = run_ssh_command(bts_ip, user, password, command, timeout_s=ssh_timeout_s)
    return _parse_uci_get_output(raw)


def _resolve_remote_exec_index_for_cpe(
    bts_ip: str,
    user: str,
    password: str,
    cpe_ip: str,
    link_wifi_idx: int,
    *,
    ssh_timeout_s: int = 60,
) -> int | None:
    """Map CPE management IP to BTS link-table sua index (remote_exec SU number)."""
    target = normalize_ip(cpe_ip)
    fallback_idx: int | None = None
    for assoc_idx in range(1, 33):
        assoc = _bts_ssh_field(
            bts_ip,
            user,
            password,
            RootCommands.get_link_stat_associd(link_wifi_idx, assoc_idx),
            ssh_timeout_s=ssh_timeout_s,
        )
        if assoc in {"", "0"}:
            continue
        ip_raw = run_ssh_command(
            bts_ip,
            user,
            password,
            RootCommands.get_link_stat_field(link_wifi_idx, assoc_idx, "ip"),
            timeout_s=ssh_timeout_s,
        )
        if _ip_matches(target, ip_raw):
            return assoc_idx
        if fallback_idx is None:
            fallback_idx = assoc_idx
    return fallback_idx


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
    user: str = "root",
    ssh_timeout_s: int = 60,
) -> str:
    if not _bts_has_sshpass(bts_ip, user, password, ssh_timeout_s=min(ssh_timeout_s, 30)):
        raise RuntimeError("sshpass not available on BTS for CPE relay")
    raw = run_ssh_via_bts(
        bts_ip, cpe_ip, user, password, cmd, ssh_timeout_s=ssh_timeout_s
    )
    value = _parse_uci_get_output(raw)
    if not value:
        raise RuntimeError(f"empty UCI response from CPE {cpe_ip}: {raw[:160]!r}")
    return value


def _read_cpe_uci_via_remote_exec(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    *,
    su_index: int,
    uci_key: str,
    ssh_timeout_s: int = 60,
) -> str:
    cmd = _remote_exec_bts_command(su_index, f"uci get {uci_key}")
    raw = run_ssh_command(bts_ip, user, password, cmd, timeout_s=ssh_timeout_s)
    value = _parse_uci_get_output(raw)
    if not value:
        raise RuntimeError(
            f"empty UCI via remote_exec SU{su_index} ({uci_key}): {raw[:160]!r}"
        )
    return value


def _read_cpe_configured_mcs(
    bts_ip: str,
    user: str,
    password: str,
    cpe_ip: str,
    cpe_radio_idx: int,
    link_wifi_idx: int,
    *,
    su_index: int,
    ssh_timeout_s: int = 60,
) -> tuple[str, str, str, str]:
    """
    Read configured ddrsrate/spatialstream on a CPE.
    Returns (mcs, spatial, source, error_message).
    """
    errors: list[str] = []
    ddrs_key = f"txparam.ath{cpe_radio_idx}.ddrsrate"
    spatial_key = f"txparam.ath{cpe_radio_idx}.spatialstream"

    if cpe_ip:
        try:
            mcs = _read_cpe_uci_via_bts(
                bts_ip,
                cpe_ip,
                password,
                f"uci get {ddrs_key}",
                ssh_timeout_s=ssh_timeout_s,
            )
            spatial = _read_cpe_uci_via_bts(
                bts_ip,
                cpe_ip,
                password,
                f"uci get {spatial_key}",
                ssh_timeout_s=ssh_timeout_s,
            )
            if mcs.isdigit():
                return mcs, spatial, "bts_ssh", ""
            errors.append(f"bts_ssh: non-numeric ddrsrate {mcs!r}")
        except RuntimeError as exc:
            errors.append(f"bts_ssh: {exc}")

    exec_idx = _resolve_remote_exec_index_for_cpe(
        bts_ip,
        user,
        password,
        cpe_ip,
        link_wifi_idx,
        ssh_timeout_s=ssh_timeout_s,
    )
    for idx in [exec_idx, su_index]:
        if idx is None:
            continue
        try:
            mcs = _read_cpe_uci_via_remote_exec(
                bts_ip,
                user,
                password,
                cpe_radio_idx,
                su_index=idx,
                uci_key=ddrs_key,
                ssh_timeout_s=ssh_timeout_s,
            )
            spatial = _read_cpe_uci_via_remote_exec(
                bts_ip,
                user,
                password,
                cpe_radio_idx,
                su_index=idx,
                uci_key=spatial_key,
                ssh_timeout_s=ssh_timeout_s,
            )
            if mcs.isdigit():
                return mcs, spatial, f"remote_exec:{idx}", ""
            errors.append(f"remote_exec SU{idx}: non-numeric ddrsrate {mcs!r}")
        except RuntimeError as exc:
            errors.append(f"remote_exec SU{idx}: {exc}")

    return "", "", "", "; ".join(errors) or "could not read CPE UCI"


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
    check_running: bool = True,
) -> None:
    expected_bw = normalize_bandwidth(bandwidth)
    actual_bw = _read_uci(ip, user, password, f"uci get wireless.wifi{radio_idx}.htmode")
    running_bw = _read_running_bandwidth(ip, user, password, radio_idx) if check_running else None
    actual_ratio = _read_uci(ip, user, password, f"uci get ath{radio_idx}qos.qoscfg.dlulratio")
    mismatches: list[str] = []
    if not uci_htmode_matches(bandwidth, actual_bw):
        mismatches.append(
            f"htmode expected {uci_htmode_value(bandwidth)}, got {actual_bw}"
        )
    if check_running and running_bw != expected_bw:
        mismatches.append(
            f"running mode expected {expected_bw}, got {running_bw or 'unknown'} (cfg80211tool)"
        )
    if actual_ratio != dl_ul_percent:
        mismatches.append(f"dlulratio expected {dl_ul_percent}, got {actual_ratio}")
    if mismatches:
        raise RuntimeError(f"BTS bandwidth/ratio verify failed on {ip}: " + "; ".join(mismatches))
    if check_running:
        print(
            f"[BTS] Bandwidth/ratio verified on {ip}: htmode={actual_bw}, "
            f"running={running_bw}, dlulratio={actual_ratio}"
        )
    else:
        print(
            f"[BTS] Bandwidth/ratio UCI verified on {ip}: htmode={actual_bw}, dlulratio={actual_ratio}"
        )


def _wait_for_running_bandwidth(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    *,
    timeout_s: float = 120.0,
    poll_s: float = 5.0,
) -> bool:
    """Poll cfg80211tool until runtime htmode matches (after apply + link recovery)."""
    expected_bw = normalize_bandwidth(bandwidth)
    deadline = time.time() + timeout_s
    attempt = 0
    print(
        f"[BTS] Waiting for running bandwidth {expected_bw} via cfg80211tool "
        f"(timeout {timeout_s:.0f}s)"
    )
    while time.time() < deadline:
        attempt += 1
        raw, running_bw = _fetch_cfg80211_mode(ip, user, password, radio_idx)
        if running_bw == expected_bw:
            print(
                f"[BTS] Running bandwidth {expected_bw} confirmed "
                f"(attempt {attempt}, {raw})"
            )
            return True
        if attempt == 1 or attempt % 4 == 0:
            print(
                f"[BTS] Running bandwidth {running_bw or 'unknown'} != {expected_bw} "
                f"(attempt {attempt}, cfg: {raw})"
            )
        time.sleep(poll_s)
    raw, running_bw = _fetch_cfg80211_mode(ip, user, password, radio_idx)
    print(
        f"[WARN] Running bandwidth still {running_bw or 'unknown'} after {timeout_s:.0f}s "
        f"(expected {expected_bw}, cfg: {raw})"
    )
    return False


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
    if not uci_htmode_matches(bandwidth, actual_bw):
        mismatches.append(
            f"htmode expected {uci_htmode_value(bandwidth)}, got {actual_bw}"
        )
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
    if bw == "HT80":
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
    bandwidth_apply_wait_s: float = 60.0,
    verify: bool = True,
) -> str:
    """BTS only: htmode and DL/UL ratio (no MCS change)."""
    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
    print(
        f"[BTS] Applying bandwidth/ratio on {ip}: bw={bandwidth}, "
        f"ratio={ratio} (dlulratio={dl_ul_percent})"
    )
    commands = RootCommands.set_bandwidth_ratio_apply_commands(
        radio_idx,
        uci_htmode_value(bandwidth),
        dl_ul_percent,
    )
    wait_s = max(0, int(round(bandwidth_apply_wait_s)))
    if wait_s > 0:
        commands.append(f"sleep {wait_s}")
        print(
            f"[BTS] Holding SSH session {wait_s}s after bandwidth apply "
            f"(device must finish before session closes)"
        )
    commands.append(RootCommands.get_bandwidth(radio_idx))
    chain = " && ".join(commands)
    session_timeout = ssh_timeout_s + wait_s + 30
    print(f"[BTS] Bandwidth apply chain: {chain}")
    session_output = run_ssh_command(ip, user, password, chain, timeout_s=session_timeout)
    mode_line = _extract_get_mode_line(session_output)
    if mode_line:
        parsed = parse_running_htmode(mode_line)
        print(
            f"[BTS] cfg80211tool in-session after {wait_s}s wait: {mode_line} "
            f"-> {parsed or 'unparsed'}"
        )
    _log_cfg80211_mode(ip, user, password, radio_idx, label=f"post-apply after {wait_s}s")
    if verify:
        _verify_bts_bandwidth_ratio(
            ip,
            user,
            password,
            radio_idx,
            bandwidth=bandwidth,
            dl_ul_percent=dl_ul_percent,
            check_running=False,
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
    last_value = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(pause_s)
        last_value = _read_cpe_uci_via_remote_exec(
            bts_ip,
            user,
            password,
            radio_idx,
            su_index=su_index,
            uci_key=f"txparam.ath{radio_idx}.ddrsrate",
            ssh_timeout_s=ssh_timeout_s,
        )
        if last_value.isdigit():
            return last_value
    return last_value


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
    spatial = _read_cpe_uci_via_remote_exec(
        bts_ip,
        user,
        password,
        radio_idx,
        su_index=su_index,
        uci_key=f"txparam.ath{radio_idx}.spatialstream",
        ssh_timeout_s=ssh_timeout_s,
    )
    return mcs, spatial


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
    bandwidth: str = "HT80",
) -> dict[str, object]:
    """Confirm BTS and every CPE/SU have the same configured MCS (UCI ddrsrate)."""
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
            "source": "uci",
            "ok": bts_mcs == expected_mcs and bts_spatial == spatial_stream,
        }
    )

    link_wifi_idx = bts_radio_idx
    has_sshpass = _bts_has_sshpass(bts_ip, user, password, ssh_timeout_s=min(ssh_timeout_s, 30))
    if has_sshpass:
        print("[MCS] CPE verify: BTS→CPE SSH relay (sshpass on BTS)")
    else:
        print("[MCS] CPE verify: sshpass not on BTS — will use remote_exec by link-table index")

    for su_index in range(1, su_count + 1):
        cpe_ip = cpe_list[su_index - 1] if su_index <= len(cpe_list) else None
        if not cpe_ip:
            checks.append(
                {
                    "role": "CPE",
                    "label": f"SU{su_index}",
                    "su_index": su_index,
                    "ip": "",
                    "expected_mcs": expected_mcs,
                    "actual_mcs": "?",
                    "spatial_stream": "",
                    "ok": False,
                    "error": "no CPE management IP in profile",
                }
            )
            continue

        actual_mcs, actual_spatial, source, error = _read_cpe_configured_mcs(
            bts_ip,
            user,
            password,
            cpe_ip,
            cpe_radio_idx,
            link_wifi_idx,
            su_index=su_index,
            ssh_timeout_s=ssh_timeout_s,
        )

        if not actual_mcs:
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
                    "error": error or f"could not read UCI MCS for SU{su_index}",
                }
            )
            continue

        checks.append(
            {
                "role": "CPE",
                "label": f"SU{su_index}",
                "su_index": su_index,
                "ip": cpe_ip or "",
                "expected_mcs": expected_mcs,
                "actual_mcs": actual_mcs,
                "spatial_stream": actual_spatial,
                "source": source,
                "ok": actual_mcs == expected_mcs
                and (not actual_spatial or actual_spatial == spatial_stream),
            }
        )

    all_ok = all(bool(row.get("ok")) for row in checks)
    for row in checks:
        status = "OK" if row.get("ok") else "MISMATCH"
        detail = f" [{row.get('source')}]" if row.get("source") else ""
        err = row.get("error")
        if err and not row.get("ok"):
            detail += f" — {err}"
        print(
            f"[MCS] {row['label']}: expected={expected_mcs}, "
            f"actual={row.get('actual_mcs')}{detail} ({status})"
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


def radio_profile_already_matches(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    mcs_rate: str,
    ratio: str,
    spatial_stream: str = "2",
    *,
    cpe_radio_idx: int | None = None,
    su_count: int = 1,
    cpe_hosts: list[str] | None = None,
    prefer_cpe_via_bts: bool = False,
    ssh_timeout_s: int = 60,
    snmp_community: str | None = None,
    snmp_radio_idx: int = 2,
) -> tuple[bool, dict[str, object]]:
    """Return True when BTS bw/ratio and MCS on all devices already match the target."""
    cpe_radio = cpe_radio_idx if cpe_radio_idx is not None else radio_idx
    expected_bw = normalize_bandwidth(bandwidth)
    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
    mcs_report = verify_mcs_all_devices(
        bts_ip,
        user,
        password,
        radio_idx,
        cpe_radio,
        mcs_rate,
        spatial_stream,
        su_count=su_count,
        cpe_hosts=cpe_hosts,
        prefer_cpe_via_bts=prefer_cpe_via_bts,
        ssh_timeout_s=ssh_timeout_s,
        snmp_community=snmp_community,
        snmp_radio_idx=snmp_radio_idx,
        bandwidth=expected_bw,
    )
    if not mcs_report.get("mcs_config_ok"):
        return False, mcs_report

    actual_bw = _read_uci(bts_ip, user, password, f"uci get wireless.wifi{radio_idx}.htmode")
    running_bw = _read_running_bandwidth(bts_ip, user, password, radio_idx)
    actual_ratio = _read_uci(
        bts_ip, user, password, f"uci get ath{radio_idx}qos.qoscfg.dlulratio"
    )
    uci_bw_ok = uci_htmode_matches(expected_bw, actual_bw.strip())
    running_bw_ok = running_bw == expected_bw
    ratio_ok = actual_ratio.strip() == dl_ul_percent
    if uci_bw_ok and not running_bw_ok:
        print(
            f"[CONFIG] UCI htmode={actual_bw.strip()} but running mode is "
            f"{running_bw or 'unknown'} (cfg80211tool) — bandwidth apply required"
        )
    return uci_bw_ok and running_bw_ok and ratio_ok, mcs_report


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
    bandwidth_apply_wait_s: float = 60.0,
    su_link_wait_s: float = 120.0,
    bandwidth_running_wait_s: float = 120.0,
    require_all_su_for_bandwidth: bool | None = None,
    profile_tb: dict | None = None,
    dut_cfg: dict | None = None,
    ssh_timeout_s: int = 60,
    verify: bool = True,
    snmp_community: str | None = None,
    snmp_radio_idx: int = 2,
    skip_if_unchanged: bool = True,
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
    require_all_su = (
        require_all_su_for_bandwidth
        if require_all_su_for_bandwidth is not None
        else su_count == 4
    )
    if skip_if_unchanged:
        matches, mcs_report = radio_profile_already_matches(
            bts_ip,
            user,
            password,
            radio_idx,
            bandwidth,
            mcs_rate,
            ratio,
            spatial_stream,
            cpe_radio_idx=cpe_radio,
            su_count=effective_su_count,
            cpe_hosts=cpe_hosts,
            prefer_cpe_via_bts=prefer_cpe_via_bts,
            ssh_timeout_s=ssh_timeout_s,
            snmp_community=snmp_community,
            snmp_radio_idx=snmp_radio_idx,
        )
        if matches:
            print(
                f"[CONFIG] Already configured: {mcs_rate}, {bandwidth}, ratio={ratio} "
                f"on BTS + {effective_su_count} CPE(s) — skipping apply"
            )
            mcs_report["bandwidth_skipped"] = False
            return mcs_report

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

    from traffic.su_link_ping import wait_for_su_links

    skip_bandwidth = False
    if require_all_su and su_link_wait_s > 0:
        pre_link = wait_for_su_links(
            cpe_hosts=cpe_hosts or [],
            profile_tb=profile_tb,
            bts_ip=bts_ip,
            bts_user=user,
            bts_password=password,
            dut=dut_cfg,
            timeout_s=su_link_wait_s,
            min_responding=su_count,
            phase="before bandwidth apply",
            strict=True,
        )
        if not pre_link.get("ok"):
            skip_bandwidth = True
            print(
                f"[CONFIG] Skipping bandwidth apply — only "
                f"{len(pre_link.get('responding', []))}/{su_count} SU(s) linked"
            )

    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
    if not skip_bandwidth:
        print(f"[CONFIG] Step 2/4: BTS bw={bandwidth}, DL:UL ratio={ratio}")
        dl_ul_percent = configure_bts_bandwidth_ratio(
            bts_ip,
            user,
            password,
            radio_idx,
            bandwidth,
            ratio,
            ssh_timeout_s=ssh_timeout_s,
            bandwidth_apply_wait_s=bandwidth_apply_wait_s,
            verify=verify,
        )

        if su_link_wait_s > 0:
            post_link = wait_for_su_links(
                cpe_hosts=cpe_hosts or [],
                profile_tb=profile_tb,
                bts_ip=bts_ip,
                bts_user=user,
                bts_password=password,
                dut=dut_cfg,
                timeout_s=su_link_wait_s,
                min_responding=su_count if require_all_su else None,
                phase="after bandwidth apply",
                strict=require_all_su,
            )
            if require_all_su and not post_link.get("ok"):
                raise RuntimeError(
                    f"Only {len(post_link.get('responding', []))}/{su_count} SUs linked after bandwidth apply"
                )

        if verify and bandwidth_running_wait_s > 0:
            _wait_for_running_bandwidth(
                bts_ip,
                user,
                password,
                radio_idx,
                bandwidth,
                timeout_s=bandwidth_running_wait_s,
            )
            _verify_bts_bandwidth_ratio(
                bts_ip,
                user,
                password,
                radio_idx,
                bandwidth=bandwidth,
                dl_ul_percent=dl_ul_percent,
                check_running=True,
            )
    else:
        print(f"[CONFIG] Step 2/4: Skipped BTS bw={bandwidth} (waiting for all {su_count} SUs)")

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
        bandwidth=bandwidth,
    )
    if verify and not mcs_report.get("mcs_config_ok"):
        bad = [
            str(row.get("label"))
            for row in (mcs_report.get("checks") or [])
            if not row.get("ok")
        ]
        mcs_report["error"] = (
            f"MCS config mismatch — not all devices have {mcs_rate}: {', '.join(bad)}"
        )
        print(f"[ERROR] {mcs_report['error']}")
        return mcs_report

    time.sleep(effective_settle)
    mcs_report["bandwidth_skipped"] = skip_bandwidth
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
    bandwidth_apply_wait_s: float = 60.0,
    su_link_wait_s: float = 120.0,
    bandwidth_running_wait_s: float = 120.0,
    require_all_su_for_bandwidth: bool | None = None,
    profile_tb: dict | None = None,
    dut_cfg: dict | None = None,
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
        bandwidth_apply_wait_s=bandwidth_apply_wait_s,
        su_link_wait_s=su_link_wait_s,
        bandwidth_running_wait_s=bandwidth_running_wait_s,
        require_all_su_for_bandwidth=require_all_su_for_bandwidth,
        profile_tb=profile_tb,
        dut_cfg=dut_cfg,
        ssh_timeout_s=ssh_timeout_s,
    )
