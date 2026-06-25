"""Apply DUT radio settings: BTS (bandwidth, DL/UL, MCS) and CPE (MCS only) over SSH."""

from __future__ import annotations

import ipaddress
import shlex
import subprocess
import time
import re

from pages.commands import RootCommands
from traffic.operating_rate_table import (
    lookup_spec,
    mcs_number,
    modulation_scheme,
    normalize_bandwidth,
    operating_rate_mbps,
    uci_htmode_matches,
    uci_htmode_value,
)
from traffic.kwn_sua_statistics import is_sua_associated, read_operating_mcs_by_sua_slot
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


def read_running_bandwidth(
    ip: str,
    user: str,
    password: str,
    radio_idx: int,
) -> str | None:
    """Return canonical HT mode (HT20/HT40/HT80) from cfg80211tool, or None."""
    return _read_running_bandwidth(ip, user, password, radio_idx)


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


def _read_cpe_uci_direct(
    cpe_ip: str,
    user: str,
    password: str,
    cmd: str,
    *,
    ssh_timeout_s: int = 60,
) -> str:
    """Read CPE UCI from the automation host (lab PC → CPE mgmt IPv6)."""
    raw = run_ssh_command(cpe_ip, user, password, cmd, timeout_s=ssh_timeout_s)
    value = _parse_uci_get_output(raw)
    if not value:
        raise RuntimeError(f"empty UCI response from CPE {cpe_ip}: {raw[:160]!r}")
    return value


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
    """Deprecated for verification — remote_exec.sh is SET-only on PTMP BTS."""
    del radio_idx
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

    if not cpe_ip:
        return "", "", "", "no CPE management IP"

    try:
        mcs = _read_cpe_uci_direct(
            cpe_ip,
            user,
            password,
            f"uci get {ddrs_key}",
            ssh_timeout_s=ssh_timeout_s,
        )
        spatial = _read_cpe_uci_direct(
            cpe_ip,
            user,
            password,
            f"uci get {spatial_key}",
            ssh_timeout_s=ssh_timeout_s,
        )
        if mcs.isdigit():
            return mcs, spatial, "direct_ssh", ""
        errors.append(f"direct_ssh: non-numeric ddrsrate {mcs!r}")
    except RuntimeError as exc:
        errors.append(f"direct_ssh: {exc}")

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

    return "", "", "", "; ".join(errors) or "could not read CPE UCI"


def _extend_cpe_hosts_from_bts_sysfs(
    bts_ip: str,
    user: str,
    password: str,
    *,
    su_count: int,
    cpe_hosts: list[str],
) -> list[str]:
    """Merge profile CPE IPs with live associated SU IPv6 from BTS KWN sysfs."""
    from traffic.kwn_sua_statistics import fetch_kwn_sua_statistics, resolve_sua_display_ip

    extended = [host.strip() for host in cpe_hosts if host.strip()]
    try:
        clients = fetch_kwn_sua_statistics(
            bts_ip,
            ssh_user=user,
            ssh_password=password,
            max_sua=max(su_count, len(extended) or 1),
            cpe_hosts=extended or None,
        )
        for client in clients:
            ip = str(client.get("ip") or "").strip()
            if not ip or ip == "-":
                ip = resolve_sua_display_ip(
                    ipv4=str(client.get("ip") or ""),
                    ipv6=str(client.get("ipv6") or ""),
                )
            if ip and ip != "-" and ip not in extended:
                extended.append(ip)
    except Exception:
        pass
    return extended


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
    """Read CPE ddrsrate/spatialstream via lab-PC→CPE SSH or BTS relay."""
    del su_index
    if not cpe_ip:
        raise RuntimeError("no CPE management IP for MCS read")
    try:
        mcs = _read_cpe_uci_direct(
            cpe_ip,
            "root",
            password,
            f"uci get txparam.ath{radio_idx}.ddrsrate",
            ssh_timeout_s=ssh_timeout_s,
        )
        spatial = _read_cpe_uci_direct(
            cpe_ip,
            "root",
            password,
            f"uci get txparam.ath{radio_idx}.spatialstream",
            ssh_timeout_s=ssh_timeout_s,
        )
        return mcs, spatial
    except RuntimeError:
        pass
    if prefer_bts_relay:
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
    raise RuntimeError(f"could not read MCS from CPE {cpe_ip}")


def print_mcs_device_matrix(
    checks: list[dict[str, object]],
    *,
    mcs_rate: str,
    phase: str = "",
    bandwidth: str = "HT80",
    spatial_stream: str = "2",
) -> None:
    """Jenkins-friendly table: BTS UCI MCS + per-SU operating rx_rate_mcs."""
    expected = str(mcs_number(mcs_rate))
    modulation = modulation_scheme(mcs_rate)
    try:
        sheet_rate = operating_rate_mbps(bandwidth, mcs_rate, spatial_streams=int(spatial_stream))
    except (TypeError, ValueError):
        sheet_rate = 0.0
    before_apply = phase in {"skip-check", "before-apply", "pre-apply"}
    if before_apply:
        title = (
            f"[MCS] CURRENT MCS STATE (before apply) — target {mcs_rate} "
            f"({modulation}, {sheet_rate:.0f} Mbps)"
        )
    else:
        title = (
            f"[MCS] DEVICE MCS STATUS — target {mcs_rate} ({modulation}, {sheet_rate:.0f} Mbps)"
        )
        if phase:
            title += f" [{phase}]"
    print(title)
    header = f"{'Device':<8} | {'BTS UCI':<8} | {'SU rx_mcs':<10} | {'rx_rate':<10} | {'vs target':<8}"
    print(header)
    print("-" * len(header))
    for row in checks:
        label = str(row.get("label") or "")
        at_target = bool(row.get("ok"))
        if before_apply:
            status = "match" if at_target else str(row.get("actual_mcs") or "?")
        else:
            status = "OK" if at_target else "MISMATCH"
        if row.get("role") == "BTS":
            uci = str(row.get("actual_mcs") or "?")
            print(f"{label:<8} | {uci:<8} | {'—':<10} | {'—':<10} | {status:<8}")
            continue
        rx_mcs = str(row.get("actual_mcs") or "?")
        rx_rate = str(row.get("rx_rate_mbps") or "—")
        print(f"{label:<8} | {'—':<8} | {rx_mcs:<10} | {rx_rate:<10} | {status:<8}")
    print("")


def wait_for_operating_mcs_on_sus(
    bts_ip: str,
    user: str,
    password: str,
    *,
    expected_mcs: str,
    su_count: int,
    timeout_s: float = 60.0,
    poll_s: float = 3.0,
) -> bool:
    """Poll BTS sysfs until sua1..sua{su_count} report rx_rate_mcs == expected."""
    if timeout_s <= 0 or su_count <= 0:
        return False
    print(
        f"[MCS] Waiting for operating rx_rate_mcs={expected_mcs} on "
        f"sua1..sua{su_count} (timeout {timeout_s:.0f}s)"
    )
    deadline = time.time() + timeout_s
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        slots = read_operating_mcs_by_sua_slot(
            bts_ip,
            ssh_user=user,
            ssh_password=password,
            max_sua=su_count,
        )
        pending: list[str] = []
        for su_index in range(1, su_count + 1):
            slot = slots.get(su_index, {})
            if not is_sua_associated(slot):
                pending.append(f"SU{su_index}:not-associated")
                continue
            actual = str(slot.get("rx_rate_mcs") or "").strip()
            if actual != expected_mcs:
                pending.append(f"SU{su_index}:{actual or '?'}")
        if not pending:
            print(f"[MCS] Operating MCS {expected_mcs} on all {su_count} SU(s) (attempt {attempt})")
            return True
        if attempt == 1 or attempt % 5 == 0:
            print(f"[MCS] Operating MCS pending (attempt {attempt}): {', '.join(pending)}")
        time.sleep(poll_s)
    print(f"[MCS] Operating MCS wait timed out after {timeout_s:.0f}s")
    return False


def _normalize_kick_mac(mac: str) -> str:
    """Normalize sysfs MAC to colon-separated lowercase for wlanconfig kickmac."""
    text = str(mac or "").strip().lower()
    if not text or text == "-":
        return ""
    if ":" in text:
        return text
    hex_only = re.sub(r"[^0-9a-f]", "", text)
    if len(hex_only) != 12:
        return text
    return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))


def kick_su_at_sua_slot(
    bts_ip: str,
    user: str,
    password: str,
    sua_index: int,
    *,
    radio_idx: int = 1,
    ssh_timeout_s: int = 60,
) -> str:
    """Disconnect one SU via BTS ``wlanconfig athN kickmac`` (MAC from sua sysfs)."""
    slots = read_operating_mcs_by_sua_slot(
        bts_ip,
        ssh_user=user,
        ssh_password=password,
        max_sua=sua_index,
    )
    slot = slots.get(sua_index, {})
    mac = _normalize_kick_mac(str(slot.get("mac") or ""))
    if not mac:
        raise RuntimeError(f"no MAC in sysfs for sua{sua_index}")
    cmd = RootCommands.kickmac_command(radio_idx, mac)
    try:
        run_ssh_command(bts_ip, user, password, cmd, timeout_s=ssh_timeout_s)
    except RuntimeError:
        run_ssh_command(bts_ip, user, password, f"kickmac {mac}", timeout_s=ssh_timeout_s)
    return mac


def _recover_su_mcs_mismatch_via_kickmac(
    bts_ip: str,
    user: str,
    password: str,
    bts_radio_idx: int,
    *,
    checks: list[dict[str, object]],
    expected_mcs: str,
    spatial_stream: str,
    su_count: int,
    kick_wait_s: float = 18.0,
    ssh_timeout_s: int = 60,
) -> list[dict[str, object]]:
    """On SU operating MCS mismatch: kickmac, wait, then re-read sysfs."""
    mismatched = [
        row
        for row in checks
        if row.get("role") == "CPE" and not row.get("ok") and row.get("su_index")
    ]
    if not mismatched:
        return checks

    print(
        f"[MCS] {len(mismatched)} SU(s) off-target (expected MCS index {expected_mcs}) — "
        f"kickmac disconnect, wait {kick_wait_s:.0f}s, re-check"
    )
    for row in mismatched:
        su_index = int(row["su_index"])
        prior_mcs = str(row.get("actual_mcs") or "?")
        try:
            mac = kick_su_at_sua_slot(
                bts_ip,
                user,
                password,
                su_index,
                radio_idx=bts_radio_idx,
                ssh_timeout_s=ssh_timeout_s,
            )
            print(
                f"[MCS] kickmac SU{su_index} mac={mac} "
                f"(was rx_rate_mcs={prior_mcs}, target={expected_mcs})"
            )
        except RuntimeError as exc:
            print(f"[MCS] WARN kickmac SU{su_index} failed: {exc}")

    time.sleep(max(kick_wait_s, 0.0))
    return _collect_mcs_checks(
        bts_ip,
        user,
        password,
        bts_radio_idx,
        expected_mcs=expected_mcs,
        spatial_stream=spatial_stream,
        su_count=su_count,
    )


def _collect_mcs_checks(
    bts_ip: str,
    user: str,
    password: str,
    bts_radio_idx: int,
    *,
    expected_mcs: str,
    spatial_stream: str,
    su_count: int,
) -> list[dict[str, object]]:
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
    sua_slots = read_operating_mcs_by_sua_slot(
        bts_ip,
        ssh_user=user,
        ssh_password=password,
        max_sua=su_count,
    )
    for su_index in range(1, su_count + 1):
        slot = sua_slots.get(su_index, {})
        if not slot or not is_sua_associated(slot):
            checks.append(
                {
                    "role": "CPE",
                    "label": f"SU{su_index}",
                    "su_index": su_index,
                    "sua_slot": su_index,
                    "expected_mcs": expected_mcs,
                    "actual_mcs": "?",
                    "rx_rate_mbps": "",
                    "ok": False,
                    "error": f"sua{su_index} not associated on BTS",
                }
            )
            continue
        actual_mcs = str(slot.get("rx_rate_mcs") or "").strip()
        rx_rate = str(slot.get("rx_rate") or "").strip()
        if not actual_mcs or actual_mcs in {"-", "0"}:
            checks.append(
                {
                    "role": "CPE",
                    "label": f"SU{su_index}",
                    "su_index": su_index,
                    "sua_slot": su_index,
                    "expected_mcs": expected_mcs,
                    "actual_mcs": "?",
                    "rx_rate_mbps": rx_rate if rx_rate not in {"", "-"} else "",
                    "ok": False,
                    "error": f"sua{su_index} rx_rate_mcs empty in BTS sysfs",
                }
            )
            continue
        checks.append(
            {
                "role": "CPE",
                "label": f"SU{su_index}",
                "su_index": su_index,
                "sua_slot": su_index,
                "expected_mcs": expected_mcs,
                "actual_mcs": actual_mcs,
                "rx_rate_mbps": rx_rate if rx_rate not in {"", "-"} else "",
                "source": "bts_sysfs:rx_rate_mcs",
                "ok": actual_mcs == expected_mcs,
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
    bandwidth: str = "HT80",
    wait_for_operating_s: float = 0.0,
    kickmac_on_mismatch: bool = True,
    kickmac_wait_s: float = 18.0,
    phase: str = "",
) -> dict[str, object]:
    """Confirm BTS UCI MCS and every SU operating MCS via BTS sysfs ``rx_rate_mcs``."""
    del cpe_radio_idx, prefer_cpe_via_bts, snmp_community, snmp_radio_idx, cpe_hosts
    display_bandwidth = bandwidth
    expected_mcs = str(mcs_number(mcs_rate))
    before_apply = phase in {"skip-check", "before-apply", "pre-apply"}

    if wait_for_operating_s > 0 and not before_apply:
        wait_for_operating_mcs_on_sus(
            bts_ip,
            user,
            password,
            expected_mcs=expected_mcs,
            su_count=su_count,
            timeout_s=wait_for_operating_s,
        )

    checks = _collect_mcs_checks(
        bts_ip,
        user,
        password,
        bts_radio_idx,
        expected_mcs=expected_mcs,
        spatial_stream=spatial_stream,
        su_count=su_count,
    )

    if phase == "post-apply" and kickmac_on_mismatch:
        checks = _recover_su_mcs_mismatch_via_kickmac(
            bts_ip,
            user,
            password,
            bts_radio_idx,
            checks=checks,
            expected_mcs=expected_mcs,
            spatial_stream=spatial_stream,
            su_count=su_count,
            kick_wait_s=kickmac_wait_s,
            ssh_timeout_s=ssh_timeout_s,
        )

    print_mcs_device_matrix(
        checks,
        mcs_rate=mcs_rate,
        phase=phase or "verify",
        bandwidth=display_bandwidth,
        spatial_stream=spatial_stream,
    )

    all_ok = all(bool(row.get("ok")) for row in checks)
    if before_apply:
        if all_ok:
            print(f"[MCS] Already at target {mcs_rate} (index {expected_mcs}) — MCS apply can be skipped")
        else:
            print(
                f"[MCS] Not at target {mcs_rate} yet (devices still on prior MCS) — "
                f"MCS apply will run next; this is NOT a verify failure"
            )
    else:
        for row in checks:
            status = "OK" if row.get("ok") else "MISMATCH"
            detail = f" [{row.get('source')}]" if row.get("source") else ""
            rate = row.get("rx_rate_mbps")
            if rate:
                detail += f" rx_rate={rate} Mbps"
            err = row.get("error")
            if err and not row.get("ok"):
                detail += f" — {err}"
            print(
                f"[MCS] {row['label']}: expected={expected_mcs}, "
                f"actual={row.get('actual_mcs')}{detail} ({status})"
            )
        if all_ok:
            print(
                f"[MCS] All {len(checks)} device(s) at operating {mcs_rate} "
                f"(MCS index {expected_mcs})"
            )
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
        print(
            "[MCS] Skipping per-SU verify after remote_exec broadcast "
            "(final verify uses BTS sysfs rx_rate_mcs on sua1..suaN)"
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
    if verify:
        print(
            f"[MCS] Skipping post-apply verify on remote_exec SU{su_index} "
            "(final verify uses BTS sysfs rx_rate_mcs on sua1..suaN)"
        )


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
        phase="skip-check",
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


def bandwidth_profile_matches(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    ratio: str,
) -> bool:
    """Return True when BTS htmode, running mode, and DL:UL ratio already match."""
    expected_bw = normalize_bandwidth(bandwidth)
    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
    actual_bw = _read_uci(bts_ip, user, password, f"uci get wireless.wifi{radio_idx}.htmode")
    running_bw = _read_running_bandwidth(bts_ip, user, password, radio_idx)
    actual_ratio = _read_uci(
        bts_ip, user, password, f"uci get ath{radio_idx}qos.qoscfg.dlulratio"
    )
    return (
        uci_htmode_matches(expected_bw, actual_bw.strip())
        and running_bw == expected_bw
        and actual_ratio.strip() == dl_ul_percent
    )


def configure_bandwidth_profile(
    bts_ip: str,
    user: str,
    password: str,
    radio_idx: int,
    bandwidth: str,
    ratio: str,
    *,
    cpe_hosts: list[str] | None = None,
    su_count: int = 1,
    profile_tb: dict | None = None,
    dut_cfg: dict | None = None,
    ssh_timeout_s: int = 60,
    bandwidth_apply_wait_s: float = 60.0,
    su_link_wait_s: float = 120.0,
    bandwidth_running_wait_s: float = 120.0,
    require_all_su_for_bandwidth: bool | None = None,
    link_debug_dir: str | None = None,
    verify: bool = True,
    skip_if_unchanged: bool = True,
) -> dict[str, object]:
    """Apply BTS htmode + DL:UL ratio once per bandwidth group (no MCS change)."""
    from traffic.su_link_ping import wait_for_su_links

    cpe_hosts = _cap_cpe_hosts(cpe_hosts, su_count)
    require_all_su = (
        require_all_su_for_bandwidth
        if require_all_su_for_bandwidth is not None
        else su_count == 4
    )
    if skip_if_unchanged and bandwidth_profile_matches(
        bts_ip, user, password, radio_idx, bandwidth, ratio
    ):
        print(
            f"[CONFIG] Bandwidth already {normalize_bandwidth(bandwidth)}, "
            f"ratio={ratio} — skipping apply"
        )
        return {"bandwidth_ok": True, "bandwidth_skipped": True}

    print(
        f"[CONFIG] Applying BTS bw={normalize_bandwidth(bandwidth)}, DL:UL ratio={ratio}"
    )
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
            link_debug_dir=link_debug_dir,
            radio_idx=radio_idx,
        )
        if require_all_su and not post_link.get("ok"):
            raise RuntimeError(
                f"Only {len(post_link.get('responding', []))}/{su_count} SUs linked after bandwidth apply"
            )

    if verify and bandwidth_running_wait_s > 0:
        _wait_for_running_bandwidth(
            bts_ip,
            user, password, radio_idx, bandwidth, timeout_s=bandwidth_running_wait_s
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

    return {"bandwidth_ok": True, "bandwidth_skipped": False}


def _cap_cpe_hosts(cpe_hosts: list[str] | None, su_count: int) -> list[str]:
    """Dedupe and limit CPE host list to the configured SU count."""
    capped: list[str] = []
    for host in cpe_hosts or []:
        clean = str(host).strip()
        if not clean:
            continue
        normalized = normalize_ip(clean)
        if normalized not in capped:
            capped.append(normalized)
        if len(capped) >= su_count:
            break
    return capped


def configure_mcs_profile(
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
    su_count: int = 1,
    prefer_cpe_via_bts: bool = False,
    settle_s: float = 4.0,
    operating_mcs_wait_s: float = 45.0,
    mcs_kickmac_wait_s: float = 18.0,
    ssh_timeout_s: int = 60,
    verify: bool = True,
    snmp_community: str | None = None,
    snmp_radio_idx: int = 2,
    skip_if_unchanged: bool = True,
) -> dict[str, object]:
    """Apply MCS on BTS + all CPEs without changing bandwidth (matrix inner loop)."""
    effective_settle = _settle_seconds(bandwidth, settle_s)
    cpe_radio = cpe_radio_idx if cpe_radio_idx is not None else radio_idx
    spec = lookup_spec(mcs_rate, bandwidth, spatial_streams=int(spatial_stream))
    cpe_hosts = _cap_cpe_hosts(cpe_hosts, su_count)

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
            su_count=su_count,
            cpe_hosts=cpe_hosts,
            prefer_cpe_via_bts=prefer_cpe_via_bts,
            ssh_timeout_s=ssh_timeout_s,
            snmp_community=snmp_community,
            snmp_radio_idx=snmp_radio_idx,
        )
        if matches:
            print(
                f"[CONFIG] Already configured: {mcs_rate}, {bandwidth}, ratio={ratio} "
                f"on BTS + {su_count} CPE(s) — skipping MCS apply"
            )
            mcs_report["bandwidth_skipped"] = False
            return mcs_report

    print(
        f"[CONFIG] Applying {mcs_rate} via remote_exec.sh 1 broadcast → BTS + {su_count} CPE(s)"
    )
    print(
        f"[CONFIG] Target MCS {spec['mcs']} ({spec['modulation']}); "
        f"operating rate ~{spec['operating_rate_mbps']:.0f} Mbps checked after apply"
    )
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
            su_count=su_count,
            prefer_cpe_via_bts=prefer_cpe_via_bts,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
        )

    time.sleep(effective_settle)
    print(f"[CONFIG] Post-apply verify {mcs_rate} (poll sysfs up to {operating_mcs_wait_s:.0f}s)")
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
        bandwidth=bandwidth,
        wait_for_operating_s=operating_mcs_wait_s,
        kickmac_wait_s=mcs_kickmac_wait_s,
        phase="post-apply",
    )
    if verify and not mcs_report.get("mcs_config_ok"):
        bad = [
            str(row.get("label"))
            for row in (mcs_report.get("checks") or [])
            if not row.get("ok")
        ]
        mcs_report["mcs_mismatch_note"] = (
            f"MCS mismatch on {', '.join(bad)} — throughput will still run"
        )
        mcs_report["error"] = mcs_report["mcs_mismatch_note"]
        print(f"[WARN] {mcs_report['mcs_mismatch_note']}")
        return mcs_report

    mcs_report["bandwidth_skipped"] = False
    return mcs_report


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
    operating_mcs_wait_s: float = 45.0,
    mcs_kickmac_wait_s: float = 18.0,
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
    cpe_hosts = _cap_cpe_hosts(cpe_hosts, su_count)
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
            su_count=su_count,
            cpe_hosts=cpe_hosts,
            prefer_cpe_via_bts=prefer_cpe_via_bts,
            ssh_timeout_s=ssh_timeout_s,
            snmp_community=snmp_community,
            snmp_radio_idx=snmp_radio_idx,
        )
        if matches:
            print(
                f"[CONFIG] Already configured: {mcs_rate}, {bandwidth}, ratio={ratio} "
                f"on BTS + {su_count} CPE(s) — skipping apply"
            )
            mcs_report["bandwidth_skipped"] = False
            return mcs_report

    print(
        f"[CONFIG] Target MCS {spec['mcs']} ({spec['modulation']}); "
        f"operating rate ~{spec['operating_rate_mbps']:.0f} Mbps checked after config"
    )

    print(f"[CONFIG] Step 1/4: MCS={mcs_rate} on BTS + {su_count} CPE(s)")
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
            su_count=su_count,
            prefer_cpe_via_bts=prefer_cpe_via_bts,
            ssh_timeout_s=ssh_timeout_s,
            verify=verify,
        )

    from traffic.su_link_ping import wait_for_su_links

    dl_ul_percent = ratio_to_uci_dl_percent(ratio)
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

    print(f"[CONFIG] Step 3/4: Re-sync MCS on BTS + SU1–SU{su_count}")
    _reapply_mcs_all_devices(
        bts_ip,
        user,
        password,
        radio_idx,
        cpe_radio,
        mcs_rate,
        spatial_stream,
        su_count=su_count,
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
        su_count=su_count,
        cpe_hosts=cpe_hosts,
        prefer_cpe_via_bts=prefer_cpe_via_bts,
        ssh_timeout_s=ssh_timeout_s,
        snmp_community=snmp_community,
        snmp_radio_idx=snmp_radio_idx,
        bandwidth=bandwidth,
        wait_for_operating_s=operating_mcs_wait_s,
        kickmac_wait_s=mcs_kickmac_wait_s,
        phase="post-apply",
    )
    if verify and not mcs_report.get("mcs_config_ok"):
        bad = [
            str(row.get("label"))
            for row in (mcs_report.get("checks") or [])
            if not row.get("ok")
        ]
        mcs_report["mcs_mismatch_note"] = (
            f"MCS mismatch on {', '.join(bad)} — throughput will still run"
        )
        mcs_report["error"] = mcs_report["mcs_mismatch_note"]
        print(f"[WARN] {mcs_report['mcs_mismatch_note']}")
        return mcs_report

    time.sleep(effective_settle)
    mcs_report["bandwidth_skipped"] = False
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
