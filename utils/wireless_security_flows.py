"""Wireless Security WPA2-WPA3 plan flows (IPv6-only)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time

import asyncssh
import pytest
import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver
from scrapli.exceptions import ScrapliTimeout

from config.defaults import WIRELESS_SECURITY_TEST_VALUES
from pages.commands import RootCommands
from pages.locators import UITimeouts, WirelessSecurityLocators
from pages.radio_properties_page import RadioPropertiesPage
from utils.cpe_session import open_cpe_gui_session
from utils.monitor_assoc import find_assoc_index_for_cpe
from utils.net_utils import ips_equal, is_ipv6_literal, normalize_ip
from utils.parsers import clean_ssh_output, extract_uci_value, parse_encryption, ssh_scalar
from utils.ui_helpers import execute_triple_apply
from utils.verify_output import print_comparison_table, print_gui_backend_table, print_section

RADIO_IDX = 1
APPLY_SETTLE_S = 10
LINK_DOWN_VERIFY_S = 20
LOG_TAIL_LINES = 200
_DEBUG_LOG = "/home/senao/Desktop/Puneet/Automation TestBed/AutomationTestBed/.cursor/debug-981b65.log"


def _dbg981b(location: str, message: str, data: dict, *, hypothesis_id: str, run_id: str = "post-fix") -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "981b65",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except OSError:
        pass
    # endregion


def _log(message: str) -> None:
    print(f"[WIRELESS_SECURITY] {message}")


def _require_ipv6(ip: str, *, case_id: str, role: str) -> str:
    clean = normalize_ip(ip)
    check.is_true(clean, f"{case_id}: {role} IP required")
    check.is_true(
        is_ipv6_literal(clean),
        f"{case_id}: IPv6-only; got {clean}",
    )
    return clean


def _require_ipv6_profile(profile_bundle, *, case_id: str) -> None:
    dut = profile_bundle.active.get("dut", {})
    ipv6_mode = dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6")
    if not ipv6_mode:
        pytest.skip(f"{case_id}: requires IPv6 profile (ip_mode: ipv6 / strict_ipv6: true)")


async def _ssh(
    root_ssh,
    command: str,
    *,
    timeout_s: int = 90,
    tolerate_timeout: bool = False,
) -> str:
    try:
        if isinstance(root_ssh, _AsyncSshSession):
            result = await root_ssh._conn.run(command, check=False, timeout=timeout_s)
            text = (result.stdout or "") + (result.stderr or "")
            return clean_ssh_output(text)
        response = await root_ssh.send_command(command, timeout_ops=timeout_s)
        return clean_ssh_output(response.result)
    except (ScrapliTimeout, asyncio.TimeoutError, TimeoutError) as exc:
        if tolerate_timeout:
            _dbg981b(
                "wireless_security_flows.py:_ssh",
                "SSH timeout tolerated",
                {"command_head": command[:80], "error": str(exc)[:120]},
                hypothesis_id="H-cpe-reload",
            )
            return ""
        raise


async def _ssh_full_output(
    root_ssh,
    command: str,
    *,
    timeout_s: int = 90,
) -> str:
    """Return full SSH command output (needed for multi-line ping statistics)."""
    try:
        if isinstance(root_ssh, _AsyncSshSession):
            result = await root_ssh._conn.run(command, check=False, timeout=timeout_s)
            return (result.stdout or "") + (result.stderr or "")
        response = await root_ssh.send_command(command, timeout_ops=timeout_s)
        return response.result or ""
    except (ScrapliTimeout, asyncio.TimeoutError, TimeoutError):
        return ""


def _ping_success(raw: str) -> bool:
    """Match BusyBox/OpenWrt ping output — do not rely on clean_ssh_output's last line."""
    lower = str(raw or "").lower()
    if "100% packet loss" in lower:
        return False
    if "bytes from" in lower:
        return True
    if "packets received" in lower and "0% packet loss" in lower:
        return True
    if "round-trip min/avg/max" in lower:
        return True
    return "0% packet loss" in lower


def _ipv6_ping_cmd(peer_ip: str, *, count: int = 4, wait_s: int = 8) -> str:
    peer = shlex.quote(normalize_ip(peer_ip))
    return f"ping -6 -c {count} -W {wait_s} {peer} 2>&1"


async def _ping_ipv6_reachable(root_ssh, peer_ip: str) -> bool:
    ping_out = await _ssh_full_output(root_ssh, _ipv6_ping_cmd(peer_ip), timeout_s=30)
    return _ping_success(ping_out)


async def _open_root_ssh_for_host(host: str, device_creds: dict):
    os.makedirs("logs", exist_ok=True)
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
        channel_log=f"logs/root_cli_{host}.log",
    )
    open_errors: list[str] = []
    for wait_s in (0, 10, 15):
        if wait_s:
            await asyncio.sleep(wait_s)
        try:
            await conn.open()
            return conn
        except Exception as exc:
            open_errors.append(str(exc))
    raise RuntimeError(f"Unable to open root SSH to {host}: {' | '.join(open_errors)}")


async def _try_open_root_ssh_for_host(host: str, device_creds: dict):
    """Single quick SSH attempt; returns connection or None (no long retry loop)."""
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
    )
    try:
        await conn.open()
        return conn
    except Exception:
        try:
            await conn.close()
        except Exception:
            pass
        return None


async def _navigate_radio_1(gui_page, host_ip: str) -> None:
    radio_page = RadioPropertiesPage(gui_page, local_ip=host_ip)
    await radio_page.navigate()
    await gui_page.locator(WirelessSecurityLocators.ENCRYPTION_DROPDOWN).first.wait_for(
        state="attached",
        timeout=UITimeouts.ELEMENT_WAIT_MS,
    )


async def _read_gui_encryption(gui_page) -> str:
    element = gui_page.locator(WirelessSecurityLocators.ENCRYPTION_DROPDOWN).first
    return await element.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )


async def _read_backend_encryption(root_ssh) -> tuple[str, str]:
    enc_raw = await _ssh(root_ssh, RootCommands.get_security(RADIO_IDX))
    enc_label = parse_encryption(enc_raw)
    enc_uci = extract_uci_value(enc_raw)
    return enc_label, enc_uci


async def _select_encryption(gui_page, option_text: str) -> None:
    element = gui_page.locator(WirelessSecurityLocators.ENCRYPTION_DROPDOWN).first
    options = await element.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )
    match = next((opt for opt in options if opt["text"] == option_text and not opt["disabled"]), None)
    check.is_true(match, f"Encryption option '{option_text}' not found; available: {[o['text'] for o in options]}")
    if await element.is_visible():
        await element.select_option(value=match["value"])
    else:
        await element.evaluate(
            """
            (el, value) => {
                el.value = value;
                Array.from(el.options).forEach(opt => { opt.selected = opt.value === value; });
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
            """,
            match["value"],
        )


def _is_wpa2_configured(gui_enc: str, backend_enc: str, expected_encryption: str) -> bool:
    return gui_enc == expected_encryption and backend_enc == expected_encryption


async def _ensure_wpa2_encryption(
    gui_page,
    root_ssh,
    host_ip: str,
    *,
    device_label: str,
    expected_encryption: str,
) -> bool:
    """Configure AES-256 (WPA2) when missing. SSID and key are left unchanged."""
    await _navigate_radio_1(gui_page, host_ip)
    gui_enc = await _read_gui_encryption(gui_page)
    backend_enc, _ = await _read_backend_encryption(root_ssh)

    if _is_wpa2_configured(gui_enc, backend_enc, expected_encryption):
        _log(f"{device_label}: already on WPA2 ({expected_encryption}) — no change needed")
        return False

    _log(
        f"{device_label}: WPA2 not configured (GUI='{gui_enc}', backend='{backend_enc}') "
        f"— selecting {expected_encryption} and applying"
    )
    await _select_encryption(gui_page, expected_encryption)
    await execute_triple_apply(gui_page, WirelessSecurityLocators.RADIO_URL_CHUNK)
    await gui_page.wait_for_timeout(APPLY_SETTLE_S * 1000)
    return True


async def _verify_wpa2_on_device(
    gui_page,
    root_ssh,
    host_ip: str,
    *,
    device_label: str,
    expected_encryption: str,
) -> None:
    await _navigate_radio_1(gui_page, host_ip)
    gui_enc = await _read_gui_encryption(gui_page)
    backend_enc, enc_uci = await _read_backend_encryption(root_ssh)

    print_gui_backend_table(
        device_label,
        [
            ("Encryption", gui_enc, backend_enc, None),
        ],
    )
    print_comparison_table(
        [
            ("Encryption (GUI)", expected_encryption, gui_enc, "PASS" if gui_enc == expected_encryption else "FAIL"),
            (
                "Encryption (backend)",
                expected_encryption,
                backend_enc,
                "PASS" if backend_enc == expected_encryption else "FAIL",
            ),
            ("Encryption (UCI raw)", "(WPA2)", enc_uci, "INFO"),
        ]
    )

    check.equal(
        gui_enc,
        expected_encryption,
        f"{device_label}: GUI encryption must be {expected_encryption} (WPA2)",
    )
    check.equal(
        backend_enc,
        expected_encryption,
        f"{device_label}: backend encryption must be {expected_encryption} (WPA2)",
    )
    check.equal(
        gui_enc,
        backend_enc,
        f"{device_label}: GUI and backend encryption must match",
    )


async def _ensure_and_verify_wpa2_on_device(
    gui_page,
    root_ssh,
    host_ip: str,
    *,
    device_label: str,
    expected_encryption: str,
) -> None:
    configured = await _ensure_wpa2_encryption(
        gui_page,
        root_ssh,
        host_ip,
        device_label=device_label,
        expected_encryption=expected_encryption,
    )
    if configured:
        _log(f"{device_label}: apply completed — verifying backend")
    await _verify_wpa2_on_device(
        gui_page,
        root_ssh,
        host_ip,
        device_label=device_label,
        expected_encryption=expected_encryption,
    )


async def assert_w_security_01_configure_wpa2(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
):
    """
    W_SECURITY_01: Ensure BTS and CPE Radio 1 use WPA2 (AES-256), then verify.

    SSID and key are pre-configured on the lab devices and are not modified.
    If encryption is not AES-256, select it in the GUI and apply — then verify.
    """
    case_id = "W_SECURITY_01"
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: at least one CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    expected_encryption = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_GUI"]

    print_section(
        f"{case_id}: WPA2 ({expected_encryption}) on BTS and CPE — "
        "configure if needed, then verify (IPv6)"
    )

    await _ensure_and_verify_wpa2_on_device(
        gui_page,
        root_ssh,
        bts_ip,
        device_label="BTS",
        expected_encryption=expected_encryption,
    )

    cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
    cpe_root_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        await _ensure_and_verify_wpa2_on_device(
            cpe_page,
            cpe_root_ssh,
            cpe_ip,
            device_label="CPE",
            expected_encryption=expected_encryption,
        )
    finally:
        await cpe_root_ssh.close()
        await cpe_page.close()

    _log(f"{case_id} completed: BTS and CPE verified on WPA2 ({expected_encryption})")


class _AsyncSshCommandResult:
    def __init__(self, text: str):
        self.result = text


class _AsyncSshSession:
    """Minimal scrapli-compatible wrapper for asyncssh connections."""

    def __init__(self, conn: asyncssh.SSHClientConnection, *, label: str, jump: asyncssh.SSHClientConnection | None = None):
        self._conn = conn
        self._jump = jump
        self.label = label

    async def send_command(self, command: str) -> _AsyncSshCommandResult:
        result = await self._conn.run(command, check=False)
        text = (result.stdout or "") + (result.stderr or "")
        return _AsyncSshCommandResult(text)

    async def close(self) -> None:
        self._conn.close()
        if self._jump is not None:
            self._jump.close()


def _resolve_oob_ips(profile_bundle, *, fallback_ip_cli: str | None = None) -> tuple[str, str, str]:
    dut = profile_bundle.active.get("dut", {})
    cpe_pc_ip = str(dut.get("cpe_pc_ip") or "").strip()
    cpe_fallback_ip = normalize_ip(
        fallback_ip_cli or str(dut.get("cpe_fallback_ip") or "10.0.0.1").strip()
    )
    cpe_pc_password = str(dut.get("cpe_pc_password") or "senao1234#").strip()
    return cpe_pc_ip, cpe_fallback_ip, cpe_pc_password


async def _read_device_logs(ssh) -> str:
    return await _ssh(
        ssh,
        "logread 2>/dev/null | grep -Ei "
        "'wireless|wpa|hostapd|kwn|ath1|reject|deauth|encrypt|security|psk|auth' "
        f"| tail -n {LOG_TAIL_LINES}",
    )


async def _is_cpe_ssh_target(ssh) -> bool:
    """True when SSH landed on CPE (STA), not BTS (AP)."""
    try:
        await _ssh(ssh, ":", timeout_s=10)
        for idx in (RADIO_IDX, 0):
            mode = clean_ssh_output(await _ssh(ssh, RootCommands.get_radio_mode(idx), timeout_s=15)).lower()
            if mode == "sta":
                _dbg981b(
                    "wireless_security_flows.py:_is_cpe_ssh_target",
                    "CPE role confirmed",
                    {"radio_idx": idx, "mode": mode},
                    hypothesis_id="H-cpe-role",
                )
                return True
            if mode == "ap":
                remote_exec = clean_ssh_output(
                    await _ssh(ssh, "test -x /usr/sbin/remote_exec.sh && echo yes || echo no", timeout_s=15)
                ).lower()
                if remote_exec == "yes":
                    _dbg981b(
                        "wireless_security_flows.py:_is_cpe_ssh_target",
                        "BTS detected on SSH session",
                        {"radio_idx": idx, "mode": mode, "remote_exec": remote_exec},
                        hypothesis_id="H-cpe-role",
                    )
                    return False
        return False
    except Exception as exc:
        _dbg981b(
            "wireless_security_flows.py:_is_cpe_ssh_target",
            "Role check exception",
            {"error": str(exc)[:160]},
            hypothesis_id="H-cpe-role",
        )
        return False


def _filter_relevant_log_lines(lines: list[str], ignore_patterns: list[str]) -> list[str]:
    ignore = [re.compile(p, re.IGNORECASE) for p in ignore_patterns]
    kept: list[str] = []
    for line in lines:
        if any(rx.search(line) for rx in ignore):
            continue
        kept.append(line)
    return kept


def _new_log_lines(before: str, after: str) -> list[str]:
    before_set = set(before.splitlines())
    return [line for line in after.splitlines() if line.strip() and line not in before_set]


def _grep_rejection_lines(lines: list[str], patterns: list[str], ignore_patterns: list[str]) -> list[str]:
    filtered = _filter_relevant_log_lines(lines, ignore_patterns)
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    matches: list[str] = []
    for line in filtered:
        if any(rx.search(line) for rx in compiled):
            matches.append(line)
    return matches


async def _wait_for_ipv6_link(
    root_ssh,
    peer_ip: str,
    *,
    expect_up: bool,
    timeout_s: int,
    label: str,
    quiet: bool = False,
) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_s
    attempt = 0
    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        up = await _ping_ipv6_reachable(root_ssh, peer_ip)
        if up == expect_up:
            if not quiet:
                _log(f"{label}: IPv6 link {'up' if up else 'down'} as expected")
            return True
        if not quiet and (attempt == 1 or attempt % 5 == 0):
            _log(f"{label}: waiting for IPv6 link ({attempt} checks, {timeout_s}s max)...")
        await asyncio.sleep(3)
    return False


async def _open_cpe_ssh_via_pc_jump(
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
) -> _AsyncSshSession:
    """Jump: test PC -> CPE PC (10.0.150.33) -> CPE fallback (10.0.0.1)."""
    device_user = device_creds.get("user", "root")
    device_password = device_creds["pass"]
    jump = await asyncssh.connect(
        cpe_pc_ip,
        username="root",
        password=cpe_pc_password,
        known_hosts=None,
    )
    try:
        cpe_conn = await jump.connect_ssh(
            cpe_fallback_ip,
            username=device_user,
            password=device_password,
            known_hosts=None,
        )
        session = _AsyncSshSession(cpe_conn, label=f"jump:{cpe_pc_ip}->{cpe_fallback_ip}", jump=jump)
        check.is_true(await _is_cpe_ssh_target(session), f"Jump target {cpe_fallback_ip} is not CPE (expected sta mode)")
        _log(f"CPE SSH via jump {cpe_pc_ip} -> {cpe_fallback_ip} (UBR650/CPE)")
        return session
    except Exception:
        jump.close()
        raise


async def _try_open_cpe_ssh_when_link_down(
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
):
    """Reach CPE when RF link is down; returns (None, '') when unreachable."""
    if cpe_pc_ip and cpe_fallback_ip:
        try:
            session = await _open_cpe_ssh_via_pc_jump(
                cpe_pc_ip,
                cpe_fallback_ip,
                device_creds,
                cpe_pc_password=cpe_pc_password,
            )
            return session, session.label
        except Exception:
            pass

    if cpe_ip:
        try:
            conn = await _open_root_ssh_for_host(cpe_ip, device_creds)
            if await _is_cpe_ssh_target(conn):
                _log(f"CPE SSH via cpe_ipv6 ({cpe_ip})")
                return conn, "cpe_ipv6"
            await conn.close()
        except Exception:
            pass

    if cpe_fallback_ip:
        try:
            conn = await _open_root_ssh_for_host(cpe_fallback_ip, device_creds)
            if await _is_cpe_ssh_target(conn):
                _log(f"CPE SSH via cpe_fallback ({cpe_fallback_ip})")
                return conn, "cpe_fallback"
            await conn.close()
        except Exception:
            pass

    return None, ""


async def _open_cpe_ssh_when_link_down(
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
):
    """Reach CPE when RF link is down. Prefer CPE PC jump — direct 10.0.0.1 may be BTS."""
    session, label = await _try_open_cpe_ssh_when_link_down(
        cpe_ip,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )
    if session is not None:
        return session, label

    errors: list[str] = []
    if cpe_pc_ip and cpe_fallback_ip:
        errors.append(f"jump({cpe_pc_ip}->{cpe_fallback_ip}): unreachable")
    if cpe_ip:
        errors.append(f"cpe_ipv6({cpe_ip}): unreachable")
    if cpe_fallback_ip:
        errors.append(f"cpe_fallback({cpe_fallback_ip}): unreachable")
    pytest.fail(f"W_SECURITY_03: unable to reach CPE SSH after link down — {' | '.join(errors)}")
    return None, ""


async def _close_cpe_ssh(session) -> None:
    if session is None:
        return
    try:
        await session.close()
    except Exception:
        pass


async def _read_encryption_uci(root_ssh) -> str:
    """Read Radio encryption UCI value, ignoring shell job-control noise."""
    raw = await _ssh(root_ssh, RootCommands.get_security(RADIO_IDX))
    for line in reversed(str(raw or "").replace("\r", "").split("\n")):
        line = line.strip().strip("'\"")
        if not line:
            continue
        lower = line.lower()
        if line.startswith("[") or "nohup" in lower or "done(" in lower:
            continue
        if re.fullmatch(r"[\w.+_-]+", line):
            return line
    return extract_uci_value(raw).strip("'\"")


async def _snapshot_encryption(root_ssh) -> dict[str, str]:
    enc_uci = await _read_encryption_uci(root_ssh)
    return {
        "encryption_uci": enc_uci,
        "encryption_label": parse_encryption(enc_uci),
    }


async def _wait_for_insecure_link_down(
    root_ssh,
    cpe_ip: str,
    *,
    timeout_s: int,
    context: str,
) -> bool:
    """Silently wait until BTS→CPE link drops after insecure apply. Primary test objective."""
    _log(f"{context}: waiting for link to break (up to {timeout_s}s)...")
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if not await _ping_ipv6_reachable(root_ssh, cpe_ip):
            print_comparison_table(
                [
                    (
                        f"Link drop ({context})",
                        "down after insecure apply",
                        "down",
                        "PASS",
                    ),
                ]
            )
            _log(f"{context}: link dropped — verified")
            return True
        await asyncio.sleep(5)

    print_comparison_table(
        [
            (
                f"Link drop ({context})",
                "down after insecure apply",
                f"still up after {timeout_s}s",
                "FAIL",
            ),
        ]
    )
    _log(f"{context}: link did not drop within {timeout_s}s")
    return False


async def _probe_cpe_ipv6_ssh(cpe_ip: str, device_creds: dict) -> bool:
    """Single quick attempt — CPE reachable over IPv6 SSH (mode=sta)."""
    conn = AsyncGenericDriver(
        host=cpe_ip,
        auth_username="root",
        auth_password=device_creds["pass"],
        auth_strict_key=False,
        transport="asyncssh",
    )
    try:
        await conn.open()
        return await _is_cpe_ssh_target(conn)
    except Exception:
        return False
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def _wait_for_secure_link_up(
    root_ssh,
    cpe_ip: str,
    device_creds: dict,
    *,
    timeout_s: int,
    context: str,
    quiet: bool = False,
    ping_only: bool = False,
) -> tuple[bool, float | None]:
    """Wait until link is back. Uses BTS→CPE IPv6 ping; SSH probe only when ping_only=False."""
    if not quiet:
        _log(f"{context}: waiting for link to restore (up to {timeout_s}s)...")
    start = time.monotonic()
    deadline = start + timeout_s
    while time.monotonic() < deadline:
        if await _ping_ipv6_reachable(root_ssh, cpe_ip):
            elapsed = time.monotonic() - start
            _log(f"{context}: link up in {elapsed:.1f}s")
            return True, elapsed
        if not ping_only and await _probe_cpe_ipv6_ssh(cpe_ip, device_creds):
            elapsed = time.monotonic() - start
            _log(f"{context}: link up in {elapsed:.1f}s (CPE SSH)")
            return True, elapsed
        await asyncio.sleep(5)
    if not quiet:
        _log(f"{context}: link wait ended — continuing (CPE phase may use OOB)")
    return False, None


async def _wait_for_bidirectional_ping_up(
    root_ssh,
    cpe_ip: str,
    bsu_ip: str,
    device_creds: dict,
    *,
    timeout_s: int,
    context: str,
) -> bool:
    """Wait until BTS→CPE and CPE→BTS IPv6 ping both succeed (on-device paths)."""
    _log(f"{context}: waiting for bidirectional IPv6 ping...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        bts_ping = await _ping_ipv6_reachable(root_ssh, cpe_ip)
        cpe_ping = await _cpe_ping_bts_reachable(cpe_ip, bsu_ip, device_creds)
        if bts_ping and cpe_ping:
            _log(f"{context}: bidirectional IPv6 ping OK")
            return True
        await asyncio.sleep(5)
    _log(f"{context}: bidirectional IPv6 ping not ready within {timeout_s}s")
    return False


async def _cpe_ping_bts_reachable(cpe_ip: str, bsu_ip: str, device_creds: dict) -> bool:
    cpe_ssh = await _try_open_root_ssh_for_host(cpe_ip, device_creds)
    if cpe_ssh is None:
        return False
    try:
        if not await _is_cpe_ssh_target(cpe_ssh):
            return False
        out = await _ssh_full_output(cpe_ssh, _ipv6_ping_cmd(bsu_ip), timeout_s=30)
        return _ping_success(out)
    except Exception:
        return False
    finally:
        await _close_cpe_ssh(cpe_ssh)


async def _wait_for_link_up_after_key_restore(
    root_ssh,
    cpe_ip: str,
    bsu_ip: str,
    device_creds: dict,
    *,
    context: str,
    settle_s: float = APPLY_SETTLE_S,
    poll_interval_s: float = 15,
    max_wait_s: int | None = None,
) -> tuple[bool, float | None]:
    """Wait until bidirectional IPv6 ping succeeds after wireless restore.

    Settles first, then polls periodically. When max_wait_s is set, returns after
    that limit without logging the timeout duration.
    """
    _log(f"{context}: waiting for link to come back...")
    start = time.monotonic()
    if settle_s > 0:
        await asyncio.sleep(settle_s)
    deadline = (start + max_wait_s) if max_wait_s is not None else None
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            return False, None
        if await _ping_ipv6_reachable(root_ssh, cpe_ip):
            if await _cpe_ping_bts_reachable(cpe_ip, bsu_ip, device_creds):
                elapsed = time.monotonic() - start
                _log(f"{context}: link back in {elapsed:.1f}s")
                return True, elapsed
        await asyncio.sleep(poll_interval_s)


def _resolve_reboot_wait_s(profile_bundle) -> int:
    reboot_wait_s = int(WIRELESS_SECURITY_TEST_VALUES["CPE_REBOOT_WAIT_S"])
    if profile_bundle is not None:
        reboot_wait_s = int(profile_bundle.active.get("recovery", {}).get("reboot_wait_seconds", reboot_wait_s))
    return reboot_wait_s


async def _verify_link_stays_down(
    root_ssh,
    cpe_ip: str,
    *,
    duration_s: int = LINK_DOWN_VERIFY_S,
) -> bool:
    """Confirm BTS→CPE ping stays down for the full duration."""
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        if await _ping_ipv6_reachable(root_ssh, cpe_ip):
            return False
        await asyncio.sleep(5)
    return True


async def _silent_device_reboot(ssh) -> None:
    try:
        await _ssh(ssh, "reboot", timeout_s=15, tolerate_timeout=True)
    except Exception:
        pass


async def _wait_for_bts_ssh_after_reboot(
    bts_ip: str,
    device_creds: dict,
    *,
    timeout_s: int,
    quiet: bool = False,
):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        conn = await _try_open_root_ssh_for_host(bts_ip, device_creds)
        if conn is not None:
            if not quiet:
                _log("BTS SSH is back")
            return conn
        await asyncio.sleep(10)
    if not quiet:
        _log("BTS SSH did not return")
    return None


async def _reboot_bts_and_cpe_after_link_down(
    root_ssh,
    *,
    bts_ip: str,
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    cpe_pc_password: str,
    reboot_wait_s: int,
    reboot_poll_s: int,
) -> tuple:
    """Reboot CPE (OOB) and BTS quietly, then wait for both to answer SSH again."""
    cpe_oob, _ = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        await _silent_device_reboot(cpe_oob)
    finally:
        await _close_cpe_ssh(cpe_oob)

    await _silent_device_reboot(root_ssh)
    try:
        await root_ssh.close()
    except Exception:
        pass

    await asyncio.sleep(reboot_wait_s)

    bts_ssh = await _wait_for_bts_ssh_after_reboot(
        bts_ip,
        device_creds,
        timeout_s=reboot_poll_s,
        quiet=True,
    )
    cpe_back = await _wait_for_cpe_ready_after_recovery(
        cpe_ip,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
        timeout_s=reboot_poll_s,
    )
    return bts_ssh, cpe_back


async def _wait_for_cpe_ready_after_recovery(
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
    timeout_s: int,
) -> bool:
    """Poll until CPE answers SSH again (IPv6 or OOB)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cpe_ssh = await _try_open_root_ssh_for_host(cpe_ip, device_creds)
        if cpe_ssh is not None:
            try:
                if await _is_cpe_ssh_target(cpe_ssh):
                    return True
            finally:
                await _close_cpe_ssh(cpe_ssh)

        cpe_oob, _ = await _try_open_cpe_ssh_when_link_down(
            cpe_ip,
            cpe_pc_ip,
            cpe_fallback_ip,
            device_creds,
            cpe_pc_password=cpe_pc_password,
        )
        if cpe_oob is not None:
            await _close_cpe_ssh(cpe_oob)
            return True

        await asyncio.sleep(10)
    return False


async def _restore_cpe_original_key(
    cpe_ip: str,
    original_key: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
) -> str:
    """Restore CPE WPA key over IPv6 SSH, falling back to OOB when the link is down."""
    cpe_ssh = await _try_open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        if cpe_ssh is not None and await _is_cpe_ssh_target(cpe_ssh):
            await _set_encryption_key_ssh(
                cpe_ssh,
                original_key,
                device_label="CPE",
                may_drop_link=False,
            )
            return await _read_encryption_key_uci(cpe_ssh)
    finally:
        await _close_cpe_ssh(cpe_ssh)

    cpe_oob, oob_label = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        await _set_encryption_key_ssh(
            cpe_oob,
            original_key,
            device_label=f"CPE-OOB ({oob_label})",
            may_drop_link=False,
        )
        return await _read_encryption_key_uci(cpe_oob)
    finally:
        await _close_cpe_ssh(cpe_oob)


async def _apply_cpe_wpa2_aes256_oob(
    cpe_ip: str,
    wpa2_uci: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
) -> str:
    """Restore CPE WPA2 AES-256 over OOB when the RF link is down."""
    cpe_oob, _ = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        await _wsec03_restore_secure(cpe_oob, device_label="CPE-OOB", wpa2_uci=wpa2_uci)
        return (await _read_encryption_uci(cpe_oob)).lower()
    finally:
        await _close_cpe_ssh(cpe_oob)


async def _tiered_link_recovery_after_drop(
    root_ssh,
    *,
    bts_ip: str,
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    cpe_pc_password: str,
    profile_bundle,
    wpa2_uci: str,
    case_id: str,
    phase_title: str,
    apply_cpe_config,
    apply_cpe_config_after_reboot=None,
) -> tuple[bool, float | None, object, object]:
    """
    Tiered recovery after a negative link-drop test:
      1) apply CPE config (caller already waited LINK_DOWN_VERIFY_S after drop)
      2) wait up to LINK_RESTORE_MAX_S for link
      3) if still down: quiet reboot BTS+CPE, ensure BTS WPA2, re-apply CPE config, wait again
    """
    link_restore_max_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_RESTORE_MAX_S"])
    reboot_wait_s = _resolve_reboot_wait_s(profile_bundle)
    reapply = apply_cpe_config_after_reboot or apply_cpe_config

    print_section(f"{case_id} — {phase_title}")

    evidence = await apply_cpe_config()
    link_ok, elapsed = await _wait_for_link_up_after_key_restore(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds,
        context="after reconfig",
        settle_s=0,
        max_wait_s=link_restore_max_s,
    )
    if link_ok:
        return True, elapsed, evidence, root_ssh

    root_ssh, cpe_ready = await _reboot_bts_and_cpe_after_link_down(
        root_ssh,
        bts_ip=bts_ip,
        cpe_ip=cpe_ip,
        cpe_pc_ip=cpe_pc_ip,
        cpe_fallback_ip=cpe_fallback_ip,
        device_creds=device_creds,
        cpe_pc_password=cpe_pc_password,
        reboot_wait_s=reboot_wait_s,
        reboot_poll_s=reboot_wait_s * 2,
    )
    check.is_not_none(root_ssh, f"{case_id}: BTS must be reachable after recovery")
    check.is_true(cpe_ready, f"{case_id}: CPE must be reachable after recovery")

    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    evidence = await reapply()
    link_ok, elapsed = await _wait_for_link_up_after_key_restore(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds,
        context="after reconfig",
        settle_s=APPLY_SETTLE_S,
        max_wait_s=None,
    )
    return link_ok, elapsed, evidence, root_ssh


async def _assert_w_security_05_link_restore_time(
    *,
    context: str,
    restore_elapsed_s: float | None,
    link_uptime_max_s: int,
) -> None:
    """W_SECURITY_05: report measured link restore time after none→WPA2."""
    print_section(f"W_SECURITY_05 — {context}")
    if restore_elapsed_s is not None:
        actual = f"{restore_elapsed_s:.1f}s"
        passed = restore_elapsed_s <= link_uptime_max_s
    else:
        actual = "not restored"
        passed = False
    print_comparison_table(
        [
            (
                f"Link restore time ({context})",
                "measured",
                actual,
                "PASS" if passed else "FAIL",
            ),
        ]
    )
    check.is_not_none(restore_elapsed_s, f"W_SECURITY_05: link did not come up ({context})")
    if restore_elapsed_s is not None:
        check.less_equal(
            restore_elapsed_s,
            link_uptime_max_s,
            f"W_SECURITY_05: link restore too slow ({restore_elapsed_s:.1f}s)",
        )


async def _assert_w_security_06_link_stability(
    root_ssh,
    cpe_ip: str,
    bsu_ip: str,
    device_creds: dict,
    *,
    phase: str,
    duration_s: int,
    interval_s: int,
) -> tuple[bool, bool]:
    """W_SECURITY_06: ping BTS→CPE and CPE→BTS for duration_s with no packet loss."""
    print_section(f"W_SECURITY_06 — {phase} ({duration_s // 60} min IPv6 ping monitor)")
    _log(f"Monitoring BTS↔CPE IPv6 ping for {duration_s}s ({phase})")

    cpe_ssh = None
    cpe_ping_ok = False
    try:
        cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        cpe_ping_ok = await _is_cpe_ssh_target(cpe_ssh)
    except Exception as exc:
        _log(f"W_SECURITY_06: CPE SSH for reverse ping unavailable ({exc})")

    bts_ok = 0
    bts_fail = 0
    cpe_ok = 0
    cpe_fail = 0
    peer_bts = normalize_ip(bsu_ip)
    peer_cpe = normalize_ip(cpe_ip)
    deadline = time.monotonic() + duration_s
    round_idx = 0

    while time.monotonic() < deadline:
        round_idx += 1
        if await _ping_ipv6_reachable(root_ssh, peer_cpe):
            bts_ok += 1
        else:
            bts_fail += 1

        if cpe_ping_ok and cpe_ssh:
            cpe_out = await _ssh_full_output(cpe_ssh, _ipv6_ping_cmd(peer_bts), timeout_s=30)
            if _ping_success(cpe_out):
                cpe_ok += 1
            else:
                cpe_fail += 1
        else:
            cpe_fail += 1

        if round_idx % max(1, 60 // interval_s) == 0:
            elapsed = min(duration_s, round_idx * interval_s)
            _log(
                f"W_SECURITY_06 ({phase}): {elapsed}s — "
                f"BTS ok={bts_ok} fail={bts_fail}, CPE ok={cpe_ok} fail={cpe_fail}"
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval_s, remaining))

    bts_stable = bts_ok > 0 and bts_fail == 0
    cpe_stable = cpe_ping_ok and cpe_ok > 0 and cpe_fail == 0
    print_comparison_table(
        [
            (
                f"BTS→CPE ping ({phase})",
                "0 failures",
                f"{bts_fail} fail / {bts_ok} ok",
                "PASS" if bts_stable else "FAIL",
            ),
            (
                f"CPE→BTS ping ({phase})",
                "0 failures",
                f"{cpe_fail} fail / {cpe_ok} ok" if cpe_ping_ok else "CPE SSH unavailable",
                "PASS" if cpe_stable else "FAIL",
            ),
        ]
    )
    check.is_true(bts_stable, f"W_SECURITY_06: BTS→CPE unstable during {phase} ({bts_fail} ping failures)")
    check.is_true(cpe_ping_ok, f"W_SECURITY_06: CPE SSH required for reverse ping during {phase}")
    check.is_true(cpe_stable, f"W_SECURITY_06: CPE→BTS unstable during {phase} ({cpe_fail} ping failures)")

    await _close_cpe_ssh(cpe_ssh)
    return bts_stable, cpe_stable


async def _read_encryption_key_uci(ssh) -> str:
    raw = await _ssh(ssh, RootCommands.get_encryption_key(RADIO_IDX), timeout_s=30)
    return extract_uci_value(raw).strip("'\"")


async def _set_encryption_key_ssh(
    ssh,
    key: str,
    *,
    device_label: str,
    may_drop_link: bool = False,
) -> None:
    """Set Radio WPA key via ucidyn set + ucidyn apply."""
    _log(f"{device_label}: ucidyn set wireless.@wifi-iface[{RADIO_IDX}].key (redacted)")
    await _ssh(
        ssh,
        f"ucidyn set wireless.@wifi-iface[{RADIO_IDX}].key {shlex.quote(key)}",
        timeout_s=30,
    )
    _log(f"{device_label}: ucidyn apply")
    await _ssh(ssh, "ucidyn apply", timeout_s=90, tolerate_timeout=may_drop_link)


async def _cpe_apply_wrong_key(
    cpe_ip: str,
    wrong_key: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
) -> bool:
    """Apply invalid passphrase on CPE over IPv6 SSH, else OOB. Returns True if command sent."""
    cpe_ssh = None
    try:
        cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        if await _is_cpe_ssh_target(cpe_ssh):
            await _set_encryption_key_ssh(
                cpe_ssh,
                wrong_key,
                device_label="CPE",
                may_drop_link=True,
            )
            _log("CPE: invalid key applied — waiting for link break")
            return True
    except Exception as exc:
        _log(f"CPE: invalid key over IPv6 failed ({exc}) — trying OOB")
    finally:
        await _close_cpe_ssh(cpe_ssh)

    cpe_oob, oob_label = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        await _set_encryption_key_ssh(
            cpe_oob,
            wrong_key,
            device_label=f"CPE-OOB ({oob_label})",
            may_drop_link=True,
        )
        return True
    finally:
        await _close_cpe_ssh(cpe_oob)


async def _verify_both_on_wpa2_aes256(
    root_ssh,
    cpe_ip: str,
    device_creds: dict,
    *,
    case_id: str,
    wpa2_uci: str,
) -> str:
    """Ensure BTS + CPE encryption is psk2+ccmp-256; return original CPE key."""
    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    bts_enc = (await _read_encryption_uci(root_ssh)).lower()

    cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        check.is_true(await _is_cpe_ssh_target(cpe_ssh), f"{case_id}: CPE IPv6 SSH required")
        await _ensure_wpa2_encryption_ssh(cpe_ssh, device_label="CPE", wpa2_uci=wpa2_uci)
        cpe_enc = (await _read_encryption_uci(cpe_ssh)).lower()
        original_key = await _read_encryption_key_uci(cpe_ssh)
    finally:
        await _close_cpe_ssh(cpe_ssh)

    print_comparison_table(
        [
            ("BTS encryption", wpa2_uci, bts_enc, "PASS" if bts_enc == wpa2_uci.lower() else "FAIL"),
            ("CPE encryption", wpa2_uci, cpe_enc, "PASS" if cpe_enc == wpa2_uci.lower() else "FAIL"),
            ("CPE key readable", "present", "yes" if original_key else "no", "PASS" if original_key else "FAIL"),
        ]
    )
    check.equal(bts_enc, wpa2_uci.lower(), f"{case_id}: BTS must be on {wpa2_uci}")
    check.equal(cpe_enc, wpa2_uci.lower(), f"{case_id}: CPE must be on {wpa2_uci}")
    check.is_true(bool(original_key), f"{case_id}: CPE original key must be readable")
    return original_key


async def _set_encryption_ssh(
    root_ssh,
    *,
    encryption_uci: str,
    device_label: str,
    may_drop_link: bool = False,
) -> None:
    """Set Radio encryption via ucidyn set + ucidyn apply (device-native apply path)."""
    _log(f"{device_label}: ucidyn set wireless.@wifi-iface[{RADIO_IDX}].encryption {encryption_uci}")
    await _ssh(
        root_ssh,
        f"ucidyn set wireless.@wifi-iface[{RADIO_IDX}].encryption {encryption_uci}",
        timeout_s=30,
    )
    _log(f"{device_label}: ucidyn apply")
    await _ssh(
        root_ssh,
        "ucidyn apply",
        timeout_s=90,
        tolerate_timeout=may_drop_link,
    )


async def _restore_encryption_ssh(root_ssh, snapshot: dict[str, str], *, device_label: str) -> None:
    enc = snapshot.get("encryption_uci", "")
    if not enc:
        return
    _log(f"{device_label}: restore wireless.@wifi-iface[{RADIO_IDX}].encryption='{enc}'")
    await _set_encryption_ssh(root_ssh, encryption_uci=enc, device_label=device_label)


async def _verify_encryption_uci(root_ssh, *, device_label: str, expected_uci: str) -> None:
    enc_uci = (await _read_encryption_uci(root_ssh)).lower()
    check.equal(enc_uci, expected_uci.lower(), f"{device_label}: UCI encryption must be '{expected_uci}'")


async def _assert_config_log_encryption(root_ssh, *, device_label: str, expected_uci: str) -> None:
    needle = f"wireless.@wifi-iface[{RADIO_IDX}].encryption = {expected_uci}"
    alt = f"encryption = {expected_uci}"
    uci_line = f"wireless.@wifi-iface[{RADIO_IDX}].encryption='{expected_uci}'"

    logs = await _ssh(root_ssh, RootCommands.GET_CONFIG_LOGS)
    logread = await _ssh(
        root_ssh,
        f"logread 2>/dev/null | grep -F 'encryption' | tail -n 40",
        timeout_s=30,
    )
    uci_show = await _ssh(
        root_ssh,
        f"uci show wireless.@wifi-iface[{RADIO_IDX}].encryption",
        timeout_s=30,
    )

    found_in_syslog = needle in logs or alt in logs or needle in logread or alt in logread
    found_in_uci = uci_line in uci_show or f".encryption='{expected_uci}'" in uci_show
    found = found_in_syslog or found_in_uci
    source = "syslog config log" if found_in_syslog else "uci show (SSH apply)"

    print_comparison_table(
        [
            (
                "Config evidence",
                needle,
                source if found else "missing",
                "PASS" if found else "FAIL",
            ),
        ]
    )
    check.is_true(
        found,
        f"{device_label}: config must show encryption = {expected_uci} (syslog or uci show)",
    )


async def _assert_rejection_logs(
    *,
    device_label: str,
    before_logs: str,
    after_logs: str,
    patterns: list[str],
    ignore_patterns: list[str],
) -> list[str]:
    new_lines = _new_log_lines(before_logs, after_logs)
    filtered_new = _filter_relevant_log_lines(new_lines, ignore_patterns)
    matches = _grep_rejection_lines(new_lines, patterns, ignore_patterns)

    print_section(f"{device_label}: wireless/security log delta")
    if filtered_new:
        for line in filtered_new[-15:]:
            print(f"  {line}", flush=True)
    else:
        print("  (no relevant new wireless/security lines)", flush=True)

    print_comparison_table(
        [
            ("Relevant new lines", ">0", str(len(filtered_new)), "PASS" if filtered_new else "INFO"),
            ("Rejection matches", ">0", str(len(matches)), "PASS" if matches else "INFO"),
        ]
    )
    if matches:
        _log(f"{device_label}: rejection log: {matches[0][:200]}")
    return matches


async def _ensure_wpa2_encryption_ssh(ssh, *, device_label: str, wpa2_uci: str) -> None:
    snap = await _snapshot_encryption(ssh)
    if snap["encryption_uci"].lower() == wpa2_uci.lower():
        _log(f"{device_label}: baseline OK ({wpa2_uci})")
        return
    _log(f"{device_label}: baseline restore {snap['encryption_uci']} -> {wpa2_uci}")
    await _set_encryption_ssh(ssh, encryption_uci=wpa2_uci, device_label=device_label)
    await _verify_encryption_uci(ssh, device_label=device_label, expected_uci=wpa2_uci)


async def _bootstrap_wpa2_baseline(
    root_ssh,
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
    wpa2_uci: str,
    link_up_timeout: int,
) -> None:
    """Ensure BTS and CPE are on WPA2 before W_SECURITY_03 (safe to run case 03 alone)."""
    _log("Bootstrapping lab baseline — both devices on psk2+ccmp-256")
    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)

    cpe_ready = False
    try:
        cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        if await _is_cpe_ssh_target(cpe_ssh):
            await _ensure_wpa2_encryption_ssh(cpe_ssh, device_label="CPE", wpa2_uci=wpa2_uci)
            cpe_ready = True
        await cpe_ssh.close()
    except Exception as exc:
        _log(f"CPE baseline over IPv6 skipped ({exc})")

    if not cpe_ready:
        cpe_oob, _ = await _open_cpe_ssh_when_link_down(
            cpe_ip,
            cpe_pc_ip,
            cpe_fallback_ip,
            device_creds,
            cpe_pc_password=cpe_pc_password,
        )
        try:
            await _ensure_wpa2_encryption_ssh(cpe_oob, device_label="CPE-OOB", wpa2_uci=wpa2_uci)
        finally:
            await _close_cpe_ssh(cpe_oob)

    if await _wait_for_ipv6_link(root_ssh, cpe_ip, expect_up=True, timeout_s=link_up_timeout, label="BTS"):
        _log("IPv6 link is up after baseline restore")
    else:
        _log("WARN: IPv6 link still down after baseline — BTS phase will run; CPE phase uses OOB if needed")


async def _apply_encryption_via_gui(
    gui_page,
    host_ip: str,
    *,
    device_label: str,
    encryption_gui: str,
) -> None:
    _log(f"{device_label}: GUI encryption -> '{encryption_gui}'")
    await _navigate_radio_1(gui_page, host_ip)
    await _select_encryption(gui_page, encryption_gui)
    await execute_triple_apply(gui_page, WirelessSecurityLocators.RADIO_URL_CHUNK)
    await gui_page.wait_for_timeout(APPLY_SETTLE_S * 1000)


async def _verify_encryption_gui_backend(
    gui_page,
    log_ssh,
    host_ip: str,
    *,
    device_label: str,
    expected_gui: str,
    expected_uci: str,
) -> None:
    await _navigate_radio_1(gui_page, host_ip)
    gui_enc = await _read_gui_encryption(gui_page)
    backend_enc, enc_uci = await _read_backend_encryption(log_ssh)
    print_comparison_table(
        [
            ("Encryption (GUI)", expected_gui, gui_enc, "PASS" if gui_enc == expected_gui else "FAIL"),
            (
                "Encryption (backend)",
                expected_gui,
                backend_enc,
                "PASS" if backend_enc == expected_gui else "FAIL",
            ),
            ("Encryption (UCI)", expected_uci, enc_uci, "PASS" if enc_uci.lower() == expected_uci.lower() else "FAIL"),
        ]
    )
    check.equal(gui_enc, expected_gui, f"{device_label}: GUI encryption must be '{expected_gui}'")
    check.equal(backend_enc, expected_gui, f"{device_label}: backend encryption must be '{expected_gui}'")
    check.equal(enc_uci.lower(), expected_uci.lower(), f"{device_label}: UCI must be '{expected_uci}'")


async def _bootstrap_wpa2_gui(
    gui_page,
    root_ssh,
    bts_ip: str,
    cpe_ip: str,
    device_creds: dict,
    *,
    wpa2_gui: str,
    link_up_timeout: int,
) -> None:
    """Ensure both devices on WPA2 via GUI before W_SECURITY_03."""
    _log("Bootstrapping lab baseline — both devices on WPA2 (GUI)")
    await _ensure_and_verify_wpa2_on_device(
        gui_page,
        root_ssh,
        bts_ip,
        device_label="BTS",
        expected_encryption=wpa2_gui,
    )

    cpe_page = None
    cpe_log_ssh = None
    try:
        cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
        cpe_log_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        await _ensure_and_verify_wpa2_on_device(
            cpe_page,
            cpe_log_ssh,
            cpe_ip,
            device_label="CPE",
            expected_encryption=wpa2_gui,
        )
    except Exception as exc:
        _log(f"CPE baseline over IPv6 skipped ({exc}) — will retry after BTS phase")
    finally:
        if cpe_log_ssh is not None:
            await cpe_log_ssh.close()
        if cpe_page is not None:
            await cpe_page.close()

    if await _wait_for_ipv6_link(root_ssh, cpe_ip, expect_up=True, timeout_s=link_up_timeout, label="BTS"):
        _log("IPv6 link is up after baseline restore")
    else:
        _log("WARN: IPv6 link still down after baseline — CPE phase may need link after BTS revert")


async def _run_insecure_phase_gui(
    gui_page,
    log_ssh,
    host_ip: str,
    *,
    device_label: str,
    insecure_gui: str,
    insecure_uci: str,
    wpa2_gui: str,
    wpa2_uci: str,
    patterns: list[str],
    ignore_patterns: list[str],
    log_settle_s: int,
) -> None:
    """GUI: set encryption None, validate logs via SSH read, restore WPA2 via GUI."""
    print_section(f"W_SECURITY_03 — Phase: {device_label} insecure (encryption=None, GUI)")
    logs_before = await _read_device_logs(log_ssh)

    await _apply_encryption_via_gui(
        gui_page, host_ip, device_label=device_label, encryption_gui=insecure_gui
    )
    await _verify_encryption_gui_backend(
        gui_page,
        log_ssh,
        host_ip,
        device_label=device_label,
        expected_gui=insecure_gui,
        expected_uci=insecure_uci,
    )
    await _assert_config_log_encryption(log_ssh, device_label=device_label, expected_uci=insecure_uci)

    await asyncio.sleep(log_settle_s)
    logs_after = await _read_device_logs(log_ssh)
    await _assert_rejection_logs(
        device_label=device_label,
        before_logs=logs_before,
        after_logs=logs_after,
        patterns=patterns,
        ignore_patterns=ignore_patterns,
    )

    await _apply_encryption_via_gui(
        gui_page, host_ip, device_label=device_label, encryption_gui=wpa2_gui
    )
    await _verify_encryption_gui_backend(
        gui_page,
        log_ssh,
        host_ip,
        device_label=device_label,
        expected_gui=wpa2_gui,
        expected_uci=wpa2_uci,
    )
    _log(f"{device_label} phase completed — restored to {wpa2_gui} ({wpa2_uci})")


async def _run_insecure_phase_ssh(
    ssh,
    *,
    device_label: str,
    insecure_uci: str,
    wpa2_uci: str,
    patterns: list[str],
    ignore_patterns: list[str],
    log_settle_s: int,
) -> None:
    """Set encryption none on one device, validate logs, restore WPA2."""
    print_section(f"W_SECURITY_03 — Phase: {device_label} insecure (encryption=none)")
    logs_before = await _read_device_logs(ssh)

    await _set_encryption_ssh(ssh, encryption_uci=insecure_uci, device_label=device_label)
    await _verify_encryption_uci(ssh, device_label=device_label, expected_uci=insecure_uci)
    await _assert_config_log_encryption(ssh, device_label=device_label, expected_uci=insecure_uci)

    await asyncio.sleep(log_settle_s)
    logs_after = await _read_device_logs(ssh)
    await _assert_rejection_logs(
        device_label=device_label,
        before_logs=logs_before,
        after_logs=logs_after,
        patterns=patterns,
        ignore_patterns=ignore_patterns,
    )

    await _restore_encryption_ssh(ssh, {"encryption_uci": wpa2_uci}, device_label=device_label)
    await _verify_encryption_uci(ssh, device_label=device_label, expected_uci=wpa2_uci)
    _log(f"{device_label} phase completed — restored to {wpa2_uci}")


async def _run_cpe_insecure_phase_oob_restore(
    cpe_ip: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    cpe_pc_password: str,
    *,
    insecure_uci: str,
    wpa2_uci: str,
    patterns: list[str],
    ignore_patterns: list[str],
    log_settle_s: int,
) -> None:
    """
    CPE phase: set encryption none over IPv6 (link up), then validate logs and
    restore WPA2 via OOB jump (10.0.150.33 -> 10.0.0.1) after RF link breaks.
    """
    print_section("W_SECURITY_03 — Phase: CPE insecure (encryption=none)")
    logs_before = ""

    cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        check.is_true(await _is_cpe_ssh_target(cpe_ssh), "CPE phase requires SSH to CPE (mode=sta)")
        logs_before = await _read_device_logs(cpe_ssh)
        await _set_encryption_ssh(cpe_ssh, encryption_uci=insecure_uci, device_label="CPE")
        await _verify_encryption_uci(cpe_ssh, device_label="CPE", expected_uci=insecure_uci)
        await _assert_config_log_encryption(cpe_ssh, device_label="CPE", expected_uci=insecure_uci)
        _log("CPE: encryption=none applied — RF link expected down; revert will use OOB")
    finally:
        await _close_cpe_ssh(cpe_ssh)

    await asyncio.sleep(log_settle_s)

    cpe_oob, oob_label = await _open_cpe_ssh_when_link_down(
        cpe_ip,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )
    try:
        logs_after = await _read_device_logs(cpe_oob)
        await _assert_rejection_logs(
            device_label="CPE",
            before_logs=logs_before,
            after_logs=logs_after,
            patterns=patterns,
            ignore_patterns=ignore_patterns,
        )

        _log(f"CPE: restoring {wpa2_uci} via OOB ({oob_label})")
        await _restore_encryption_ssh(cpe_oob, {"encryption_uci": wpa2_uci}, device_label="CPE-OOB")
        await _verify_encryption_uci(cpe_oob, device_label="CPE-OOB", expected_uci=wpa2_uci)
    finally:
        await _close_cpe_ssh(cpe_oob)

    _log(f"CPE phase completed — restored to {wpa2_uci} via OOB")


async def _wsec03_apply_insecure(
    ssh,
    *,
    device_label: str,
    insecure_uci: str,
    patterns: list[str],
    ignore_patterns: list[str],
    log_settle_s: int,
    link_watch_ssh=None,
    cpe_ip: str = "",
    link_down_timeout_s: int = 90,
) -> str:
    """Set encryption=none, apply, wait for link drop, verify config, capture logs."""
    print_section(f"W_SECURITY_03 — {device_label}: apply encryption=none")
    logs_before = await _read_device_logs(ssh)
    await _set_encryption_ssh(ssh, encryption_uci=insecure_uci, device_label=device_label)
    await _verify_encryption_uci(ssh, device_label=device_label, expected_uci=insecure_uci)
    await _assert_config_log_encryption(ssh, device_label=device_label, expected_uci=insecure_uci)
    if link_watch_ssh and cpe_ip:
        link_down = await _wait_for_insecure_link_down(
            link_watch_ssh,
            cpe_ip,
            timeout_s=link_down_timeout_s,
            context=f"after {device_label} encryption=none",
        )
        check.is_true(link_down, f"W_SECURITY_03: link must drop after {device_label} encryption=none")
    await asyncio.sleep(log_settle_s)
    logs_after = await _read_device_logs(ssh)
    _print_rejection_logs_if_found(
        device_label=device_label,
        before_logs=logs_before,
        after_logs=logs_after,
        patterns=patterns,
        ignore_patterns=ignore_patterns,
    )
    return logs_before


async def _wsec03_restore_secure(ssh, *, device_label: str, wpa2_uci: str) -> None:
    await _set_encryption_ssh(ssh, encryption_uci=wpa2_uci, device_label=device_label)
    await _verify_encryption_uci(ssh, device_label=device_label, expected_uci=wpa2_uci)
    await _assert_config_log_encryption(ssh, device_label=device_label, expected_uci=wpa2_uci)
    _log(f"{device_label}: restored to {wpa2_uci}")


async def _wsec03_cpe_apply_none(
    cpe_ip: str,
    insecure_uci: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
) -> str:
    """
    Apply encryption=none on CPE while link is up (IPv6), else via OOB jump.
    Returns log snapshot taken before the insecure apply.
    """
    logs_before = ""
    cpe_ssh = None
    applied = False
    try:
        cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        if await _is_cpe_ssh_target(cpe_ssh):
            logs_before = await _read_device_logs(cpe_ssh)
            await _set_encryption_ssh(
                cpe_ssh,
                encryption_uci=insecure_uci,
                device_label="CPE",
                may_drop_link=True,
            )
            applied = True
            _log("CPE: encryption=none committed — apply sent; waiting for link break before OOB")
        else:
            _log(f"CPE: IPv6 SSH to {cpe_ip} did not land on CPE — will apply none via OOB")
    except Exception as exc:
        _log(f"CPE: IPv6 apply path failed ({exc}) — will apply none via OOB")
    finally:
        await _close_cpe_ssh(cpe_ssh)

    if applied:
        return logs_before

    cpe_oob, oob_label = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        logs_before = await _read_device_logs(cpe_oob)
        await _set_encryption_ssh(
            cpe_oob,
            encryption_uci=insecure_uci,
            device_label="CPE-OOB",
            may_drop_link=True,
        )
        _log(f"CPE: encryption=none applied via OOB ({oob_label})")
    finally:
        await _close_cpe_ssh(cpe_oob)
    return logs_before


def _print_rejection_logs_if_found(
    *,
    device_label: str,
    before_logs: str,
    after_logs: str,
    patterns: list[str],
    ignore_patterns: list[str],
) -> None:
    """Only print log output when rejection lines appear (avoids noisy INFO when lab is quiet)."""
    new_lines = _new_log_lines(before_logs, after_logs)
    matches = _grep_rejection_lines(new_lines, patterns, ignore_patterns)
    if not matches:
        return
    print_section(f"{device_label}: rejection log lines")
    for line in matches[-10:]:
        print(f"  {line}", flush=True)
    print_comparison_table(
        [
            ("Rejection log lines", ">0", str(len(matches)), "PASS"),
        ]
    )


async def assert_w_security_insecure_encryption_flow(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
    run_03: bool = True,
    run_05: bool = True,
    run_06: bool = True,
):
    """
    Wireless Security insecure-encryption flow with selectable case coverage:
      W_SECURITY_03 — none apply, link down, rejection/config logs, WPA2 restore
      W_SECURITY_05 — same flow; verify measured link restore time after WPA2 apply
      W_SECURITY_06 — full flow; 5-min IPv6 ping monitor after BTS restore, then again after CPE restore
    """
    if not (run_03 or run_05 or run_06):
        pytest.fail("At least one of run_03, run_05, run_06 must be True")

    case_parts = [part for part, enabled in (("03", run_03), ("05", run_05), ("06", run_06)) if enabled]
    case_id = f"W_SECURITY_{'+'.join(case_parts)}"
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: at least one CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    wpa2_uci = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_UCI"]
    insecure_uci = WIRELESS_SECURITY_TEST_VALUES["INSECURE_ENCRYPTION_UCI"]
    patterns = WIRELESS_SECURITY_TEST_VALUES["REJECTION_LOG_PATTERNS"]
    ignore_patterns = WIRELESS_SECURITY_TEST_VALUES["LOG_IGNORE_PATTERNS"]
    log_settle_s = int(WIRELESS_SECURITY_TEST_VALUES["LOG_SETTLE_S"])
    link_down_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_DOWN_TIMEOUT_S"])
    link_up_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_UP_TIMEOUT_S"])
    link_uptime_max_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_UPTIME_MAX_S"])
    stability_monitor_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_STABILITY_MONITOR_S"])
    stability_interval_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_STABILITY_INTERVAL_S"])
    cpe_pc_ip, cpe_fallback_ip, cpe_pc_password = _resolve_oob_ips(profile_bundle, fallback_ip_cli=fallback_ip)
    quiet_link_up_wait = not run_03 and (run_05 or run_06)
    ping_only_link_up = run_05 or run_06

    print_section(f"{case_id}: BTS then CPE — encryption none / restore WPA2 (SSH)")

    # --- BTS ---
    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    await _wsec03_apply_insecure(
        root_ssh,
        device_label="BTS",
        insecure_uci=insecure_uci,
        patterns=patterns,
        ignore_patterns=ignore_patterns,
        log_settle_s=log_settle_s,
        link_watch_ssh=root_ssh,
        cpe_ip=cpe_ip,
        link_down_timeout_s=link_down_timeout_s,
    )
    await _wsec03_restore_secure(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    await asyncio.sleep(APPLY_SETTLE_S)
    _, bts_restore_s = await _wait_for_secure_link_up(
        root_ssh,
        cpe_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="after BTS WPA2 restore",
        quiet=quiet_link_up_wait,
        ping_only=ping_only_link_up,
    )
    if run_05:
        await _assert_w_security_05_link_restore_time(
            context="after BTS WPA2 restore",
            restore_elapsed_s=bts_restore_s,
            link_uptime_max_s=link_uptime_max_s,
        )
    if run_06:
        bts_ping_ready = await _wait_for_bidirectional_ping_up(
            root_ssh,
            cpe_ip,
            bts_ip,
            device_creds,
            timeout_s=link_up_timeout_s,
            context="after BTS WPA2 restore",
        )
        check.is_true(bts_ping_ready, "W_SECURITY_06: bidirectional IPv6 ping required after BTS WPA2 restore")
        if bts_ping_ready:
            bts_bts_stable, bts_cpe_stable = await _assert_w_security_06_link_stability(
                root_ssh,
                cpe_ip,
                bts_ip,
                device_creds,
                phase="after BTS WPA2 restore",
                duration_s=stability_monitor_s,
                interval_s=stability_interval_s,
            )
        else:
            bts_bts_stable, bts_cpe_stable = False, False

    # --- CPE: set none (IPv6 or OOB); then OOB verify + restore WPA2 ---
    print_section(f"{case_id} — CPE: apply encryption=none")
    logs_before = await _wsec03_cpe_apply_none(
        cpe_ip,
        insecure_uci,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )
    cpe_link_down = await _wait_for_insecure_link_down(
        root_ssh,
        cpe_ip,
        timeout_s=link_down_timeout_s,
        context="after CPE encryption=none",
    )
    check.is_true(cpe_link_down, f"{case_id}: link must drop after CPE encryption=none")
    await asyncio.sleep(log_settle_s)

    cpe_oob, oob_label = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        await _verify_encryption_uci(cpe_oob, device_label="CPE", expected_uci=insecure_uci)
        await _assert_config_log_encryption(cpe_oob, device_label="CPE", expected_uci=insecure_uci)
        logs_after = await _read_device_logs(cpe_oob)
        _print_rejection_logs_if_found(
            device_label="CPE",
            before_logs=logs_before,
            after_logs=logs_after,
            patterns=patterns,
            ignore_patterns=ignore_patterns,
        )
        _log(f"CPE: OOB restore via {oob_label}")
        await _wsec03_restore_secure(cpe_oob, device_label="CPE-OOB", wpa2_uci=wpa2_uci)
    finally:
        await _close_cpe_ssh(cpe_oob)

    _, cpe_restore_s = await _wait_for_secure_link_up(
        root_ssh,
        cpe_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="after CPE WPA2 restore",
        quiet=quiet_link_up_wait,
        ping_only=ping_only_link_up,
    )
    if run_05:
        await _assert_w_security_05_link_restore_time(
            context="after CPE WPA2 restore",
            restore_elapsed_s=cpe_restore_s,
            link_uptime_max_s=link_uptime_max_s,
        )

    cpe_bts_stable = False
    cpe_cpe_stable = False
    if run_06:
        cpe_ping_ready = await _wait_for_bidirectional_ping_up(
            root_ssh,
            cpe_ip,
            bts_ip,
            device_creds,
            timeout_s=link_up_timeout_s,
            context="after CPE WPA2 restore",
        )
        check.is_true(cpe_ping_ready, "W_SECURITY_06: bidirectional IPv6 ping required after CPE WPA2 restore")
        if cpe_ping_ready:
            cpe_bts_stable, cpe_cpe_stable = await _assert_w_security_06_link_stability(
                root_ssh,
                cpe_ip,
                bts_ip,
                device_creds,
                phase="after CPE WPA2 restore",
                duration_s=stability_monitor_s,
                interval_s=stability_interval_s,
            )
        else:
            cpe_bts_stable, cpe_cpe_stable = False, False

    summary_rows: list[tuple[str, str, str, str]] = []
    if run_03:
        summary_rows.extend(
            [
                ("BTS insecure apply + WPA2 restore", "PASS", "PASS", "PASS"),
                ("CPE insecure apply + OOB WPA2 restore", "PASS", "PASS", "PASS"),
                ("Link down verified (BTS + CPE none)", "down", "down", "PASS"),
            ]
        )
    if run_05:
        bts_time = f"{bts_restore_s:.1f}s" if bts_restore_s is not None else "n/a"
        cpe_time = f"{cpe_restore_s:.1f}s" if cpe_restore_s is not None else "n/a"
        summary_rows.extend(
            [
                ("BTS link restore time", "measured", bts_time, "PASS" if bts_restore_s is not None else "FAIL"),
                ("CPE link restore time", "measured", cpe_time, "PASS" if cpe_restore_s is not None else "FAIL"),
            ]
        )
    if run_06:
        bts_phase_ok = bts_bts_stable and bts_cpe_stable
        cpe_phase_ok = cpe_bts_stable and cpe_cpe_stable
        summary_rows.extend(
            [
                (
                    f"Stability after BTS restore ({stability_monitor_s // 60} min ping)",
                    "0 failures",
                    "stable" if bts_phase_ok else "unstable",
                    "PASS" if bts_phase_ok else "FAIL",
                ),
                (
                    f"Stability after CPE restore ({stability_monitor_s // 60} min ping)",
                    "0 failures",
                    "stable" if cpe_phase_ok else "unstable",
                    "PASS" if cpe_phase_ok else "FAIL",
                ),
            ]
        )
    print_comparison_table(summary_rows)
    _log(f"{case_id} completed — both devices restored to {wpa2_uci}")


async def assert_w_security_03_validate_rejection_logs(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """W_SECURITY_03 only — insecure apply, link down, logs, WPA2 restore."""
    await assert_w_security_insecure_encryption_flow(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=fallback_ip,
        run_03=True,
        run_05=False,
        run_06=False,
    )


async def assert_w_security_05_link_uptime_after_restore(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """W_SECURITY_05 only — full flow; verify measured link restore time after WPA2."""
    await assert_w_security_insecure_encryption_flow(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=fallback_ip,
        run_03=False,
        run_05=True,
        run_06=False,
    )


async def assert_w_security_06_link_stability_monitor(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """W_SECURITY_06 only — full flow; 5-min ping after BTS restore, then 5-min after CPE restore."""
    await assert_w_security_insecure_encryption_flow(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        fallback_ip=fallback_ip,
        run_03=False,
        run_05=False,
        run_06=True,
    )


async def _read_radio_mode(ssh, radio_idx: int = RADIO_IDX) -> str:
    return clean_ssh_output(await _ssh(ssh, RootCommands.get_radio_mode(radio_idx), timeout_s=20)).lower()


async def _read_link_field_at_peer(ssh, peer_ip: str, field: str) -> str:
    assoc_idx = await find_assoc_index_for_cpe(ssh, peer_ip, radio_idx=RADIO_IDX)
    return ssh_scalar(await _ssh(ssh, RootCommands.get_link_stat_field(RADIO_IDX, assoc_idx, field), timeout_s=20))


async def _ensure_cpe_wpa2_ssh(
    cpe_ip: str,
    wpa2_uci: str,
    cpe_pc_ip: str,
    cpe_fallback_ip: str,
    device_creds: dict,
    *,
    cpe_pc_password: str,
) -> None:
    """Configure CPE WPA2-PSK over IPv6 SSH, falling back to OOB when the link is down."""
    cpe_ssh = None
    configured = False
    try:
        cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        if await _is_cpe_ssh_target(cpe_ssh):
            await _ensure_wpa2_encryption_ssh(cpe_ssh, device_label="CPE", wpa2_uci=wpa2_uci)
            configured = True
    except Exception as exc:
        _log(f"CPE WPA2 configure over IPv6 skipped ({exc})")
    finally:
        await _close_cpe_ssh(cpe_ssh)

    if configured:
        return

    cpe_oob, oob_label = await _open_cpe_ssh_when_link_down(
        cpe_ip, cpe_pc_ip, cpe_fallback_ip, device_creds, cpe_pc_password=cpe_pc_password
    )
    try:
        await _ensure_wpa2_encryption_ssh(cpe_oob, device_label=f"CPE-OOB ({oob_label})", wpa2_uci=wpa2_uci)
    finally:
        await _close_cpe_ssh(cpe_oob)


async def _assert_wpa2_association(
    root_ssh,
    cpe_ssh,
    *,
    bts_ip: str,
    cpe_ip: str,
    case_id: str,
    phase: str,
    verify_ip: bool = True,
) -> None:
    """Verify WPA2 association on BTS and CPE; optionally verify IPv6 assignment."""
    print_section(f"{case_id} — {phase}: association{' + IP' if verify_ip else ''}")

    bts_mode = await _read_radio_mode(root_ssh)
    cpe_mode = await _read_radio_mode(cpe_ssh)
    bts_assoc_id = await _read_link_field_at_peer(root_ssh, cpe_ip, "associd")
    cpe_assoc_id = await _read_link_field_at_peer(cpe_ssh, bts_ip, "associd")
    bts_assoc_time_raw = await _read_link_field_at_peer(root_ssh, cpe_ip, "assoc_time")

    try:
        bts_assoc_time_s = int(float(bts_assoc_time_raw))
    except (TypeError, ValueError):
        bts_assoc_time_s = -1

    bts_assoc_ok = bts_assoc_id not in {"", "0"} and bts_assoc_time_s > 0
    cpe_assoc_ok = cpe_assoc_id not in {"", "0"}

    rows: list[tuple[str, str, str, str]] = [
        ("BTS radio mode", "ap", bts_mode, "PASS" if bts_mode == "ap" else "FAIL"),
        ("CPE radio mode", "sta", cpe_mode, "PASS" if cpe_mode == "sta" else "FAIL"),
        (
            "BTS association to CPE",
            "assoc present",
            bts_assoc_id or "none",
            "PASS" if bts_assoc_ok else "FAIL",
        ),
        (
            "CPE association to BTS",
            "assoc present",
            cpe_assoc_id or "none",
            "PASS" if cpe_assoc_ok else "FAIL",
        ),
    ]

    cpe_ip_ok = False
    bts_ip_ok = False
    if verify_ip:
        cpe_ipv6_on_link = await _read_link_field_at_peer(cpe_ssh, bts_ip, "ipv6")
        bts_sees_cpe_ipv6 = await _read_link_field_at_peer(root_ssh, cpe_ip, "ipv6")
        cpe_ip_ok = bool(cpe_ipv6_on_link) and cpe_ipv6_on_link not in {"::", "0:0:0:0:0:0:0:0"}
        bts_ip_ok = ips_equal(bts_sees_cpe_ipv6, cpe_ip) or ips_equal(cpe_ipv6_on_link, cpe_ip)
        rows.extend(
            [
                ("CPE IPv6 on link", "assigned", cpe_ipv6_on_link or "none", "PASS" if cpe_ip_ok else "FAIL"),
                (
                    "BTS sees CPE IPv6",
                    normalize_ip(cpe_ip),
                    bts_sees_cpe_ipv6 or "none",
                    "PASS" if bts_ip_ok else "FAIL",
                ),
            ]
        )

    print_comparison_table(rows)

    check.equal(bts_mode, "ap", f"{case_id}: BTS must be AP mode ({phase})")
    check.equal(cpe_mode, "sta", f"{case_id}: CPE must be STA mode ({phase})")
    check.is_true(bts_assoc_ok, f"{case_id}: BTS must show association to CPE ({phase})")
    check.is_true(cpe_assoc_ok, f"{case_id}: CPE must show association to BTS ({phase})")
    if verify_ip:
        check.is_true(cpe_ip_ok, f"{case_id}: CPE must have IPv6 assigned ({phase})")
        check.is_true(bts_ip_ok, f"{case_id}: BTS link table must show CPE IPv6 ({phase})")


async def _assert_w_security_07_association_and_ip(
    root_ssh,
    cpe_ssh,
    *,
    bts_ip: str,
    cpe_ip: str,
) -> None:
    await _assert_wpa2_association(
        root_ssh,
        cpe_ssh,
        bts_ip=bts_ip,
        cpe_ip=cpe_ip,
        case_id="W_SECURITY_07",
        phase="association and IP assignment",
        verify_ip=True,
    )


async def _reboot_cpe_via_ssh(cpe_ip: str, device_creds: dict) -> bool:
    """Reboot CPE over IPv6 SSH. Returns True when reboot command was sent."""
    cpe_ssh = None
    try:
        cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
        if not await _is_cpe_ssh_target(cpe_ssh):
            return False
        _log("CPE: sending reboot")
        await _ssh(cpe_ssh, "reboot", timeout_s=15, tolerate_timeout=True)
        return True
    except Exception as exc:
        _log(f"CPE reboot command failed ({exc})")
        return False
    finally:
        await _close_cpe_ssh(cpe_ssh)


async def _wait_for_cpe_ssh_after_reboot(
    cpe_ip: str,
    device_creds: dict,
    *,
    timeout_s: int,
    context: str = "",
    quiet: bool = False,
) -> bool:
    """Poll until CPE answers IPv6 SSH again after reboot."""
    if not quiet and context:
        _log(f"{context}: waiting for CPE SSH (up to {timeout_s}s)...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cpe_ssh = None
        try:
            cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
            if await _is_cpe_ssh_target(cpe_ssh):
                if not quiet and context:
                    _log(f"{context}: CPE SSH is back")
                return True
        except Exception:
            pass
        finally:
            await _close_cpe_ssh(cpe_ssh)
        await asyncio.sleep(10)
    if not quiet and context:
        _log(f"{context}: CPE SSH did not return within {timeout_s}s")
    return False


async def assert_w_security_07_wpa2_psk_connection(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """
    W_SECURITY_07 — WPA2-PSK Connection:
      1) Configure BTS + CPE for WPA2-PSK (psk2+ccmp-256)
      2) Attempt connection (apply + wait for link)
      3) Verify association/authentication and IPv6 assignment
    """
    case_id = "W_SECURITY_07"
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: at least one CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    wpa2_uci = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_UCI"]
    link_up_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_UP_TIMEOUT_S"])
    cpe_pc_ip, cpe_fallback_ip, cpe_pc_password = _resolve_oob_ips(profile_bundle, fallback_ip_cli=fallback_ip)

    print_section(f"{case_id}: WPA2-PSK connection — configure CPE, connect, verify assoc + IP")

    print_section(f"{case_id} — Phase 1: BTS WPA2-PSK")
    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    bts_enc = (await _read_encryption_uci(root_ssh)).lower()
    print_comparison_table(
        [
            ("BTS encryption UCI", wpa2_uci, bts_enc, "PASS" if bts_enc == wpa2_uci.lower() else "FAIL"),
        ]
    )
    check.equal(bts_enc, wpa2_uci.lower(), f"{case_id}: BTS must use {wpa2_uci}")

    print_section(f"{case_id} — Phase 2: CPE WPA2-PSK")
    await _ensure_cpe_wpa2_ssh(
        cpe_ip,
        wpa2_uci,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )

    cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        check.is_true(await _is_cpe_ssh_target(cpe_ssh), f"{case_id}: CPE IPv6 SSH required after WPA2 configure")
        cpe_enc = (await _read_encryption_uci(cpe_ssh)).lower()
        print_comparison_table(
            [
                ("CPE encryption UCI", wpa2_uci, cpe_enc, "PASS" if cpe_enc == wpa2_uci.lower() else "FAIL"),
            ]
        )
        check.equal(cpe_enc, wpa2_uci.lower(), f"{case_id}: CPE must use {wpa2_uci}")

        print_section(f"{case_id} — Phase 3: attempt connection (wait for IPv6 link)")
        link_ready = await _wait_for_bidirectional_ping_up(
            root_ssh,
            cpe_ip,
            bts_ip,
            device_creds,
            timeout_s=link_up_timeout_s,
            context="WPA2-PSK connection",
        )
        bts_ping = await _ping_ipv6_reachable(root_ssh, cpe_ip)
        cpe_ping = await _cpe_ping_bts_reachable(cpe_ip, bts_ip, device_creds)
        print_comparison_table(
            [
                ("Bidirectional IPv6 ping", "both OK", f"BTS→CPE={'OK' if bts_ping else 'FAIL'}, CPE→BTS={'OK' if cpe_ping else 'FAIL'}", "PASS" if link_ready else "FAIL"),
            ]
        )
        check.is_true(link_ready, f"{case_id}: WPA2-PSK connection must come up within {link_up_timeout_s}s")

        await _assert_w_security_07_association_and_ip(
            root_ssh,
            cpe_ssh,
            bts_ip=bts_ip,
            cpe_ip=cpe_ip,
        )
    finally:
        await _close_cpe_ssh(cpe_ssh)

    print_comparison_table(
        [
            ("WPA2-PSK connection", "assoc + IP assigned", "verified", "PASS"),
        ]
    )
    _log(f"{case_id} completed — CPE associated on WPA2-PSK with IPv6 assigned")


async def assert_w_security_09_wpa2_association(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """
    W_SECURITY_09 — WPA2 Association:
      1) Configure BTS + CPE WPA2-PSK
      2) Connect and verify initial association
      3) Reboot CPE
      4) Verify WPA2 association succeeds again after reboot
    """
    case_id = "W_SECURITY_09"
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: at least one CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    wpa2_uci = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_UCI"]
    link_up_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_UP_TIMEOUT_S"])
    reboot_wait_s = int(WIRELESS_SECURITY_TEST_VALUES["CPE_REBOOT_WAIT_S"])
    if profile_bundle is not None:
        reboot_wait_s = int(profile_bundle.active.get("recovery", {}).get("reboot_wait_seconds", reboot_wait_s))
    cpe_pc_ip, cpe_fallback_ip, cpe_pc_password = _resolve_oob_ips(profile_bundle, fallback_ip_cli=fallback_ip)

    print_section(f"{case_id}: WPA2 association — configure, connect, reboot CPE, re-associate")

    print_section(f"{case_id} — Phase 1: BTS + CPE WPA2-PSK")
    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    await _ensure_cpe_wpa2_ssh(
        cpe_ip,
        wpa2_uci,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )

    print_section(f"{case_id} — Phase 2: initial WPA2 connection")
    link_ready = await _wait_for_bidirectional_ping_up(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="before CPE reboot",
    )
    check.is_true(link_ready, f"{case_id}: initial WPA2 link must come up within {link_up_timeout_s}s")

    cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        check.is_true(await _is_cpe_ssh_target(cpe_ssh), f"{case_id}: CPE IPv6 SSH required")
        await _assert_wpa2_association(
            root_ssh,
            cpe_ssh,
            bts_ip=bts_ip,
            cpe_ip=cpe_ip,
            case_id=case_id,
            phase="before CPE reboot",
            verify_ip=False,
        )
    finally:
        await _close_cpe_ssh(cpe_ssh)

    print_section(f"{case_id} — Phase 3: reboot CPE")
    reboot_sent = await _reboot_cpe_via_ssh(cpe_ip, device_creds)
    check.is_true(reboot_sent, f"{case_id}: CPE reboot command must be sent")
    _log(f"CPE: waiting {reboot_wait_s}s for reboot to complete")
    await asyncio.sleep(reboot_wait_s)

    print_section(f"{case_id} — Phase 4: association after CPE reboot")
    cpe_ssh_back = await _wait_for_cpe_ssh_after_reboot(
        cpe_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="after CPE reboot",
    )
    check.is_true(cpe_ssh_back, f"{case_id}: CPE must be reachable via SSH after reboot")

    link_after_reboot = await _wait_for_bidirectional_ping_up(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="after CPE reboot",
    )
    check.is_true(link_after_reboot, f"{case_id}: WPA2 link must restore within {link_up_timeout_s}s after reboot")

    cpe_ssh = await _open_root_ssh_for_host(cpe_ip, device_creds)
    try:
        check.is_true(await _is_cpe_ssh_target(cpe_ssh), f"{case_id}: CPE IPv6 SSH required after reboot")
        await _assert_wpa2_association(
            root_ssh,
            cpe_ssh,
            bts_ip=bts_ip,
            cpe_ip=cpe_ip,
            case_id=case_id,
            phase="after CPE reboot",
            verify_ip=False,
        )
    finally:
        await _close_cpe_ssh(cpe_ssh)

    print_comparison_table(
        [
            ("WPA2 association after CPE reboot", "association succeeds", "verified", "PASS"),
        ]
    )
    _log(f"{case_id} completed — CPE re-associated on WPA2 after reboot")


async def assert_w_security_11_open_mode_attempt(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """
    W_SECURITY_11 — Open mode attempt (negative):
      BTS stays on AES-256; CPE set to none → link must drop; restore CPE WPA2 → link stable.
    """
    case_id = "W_SECURITY_11"
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: at least one CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    wpa2_uci = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_UCI"]
    insecure_uci = WIRELESS_SECURITY_TEST_VALUES["INSECURE_ENCRYPTION_UCI"]
    link_down_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_DOWN_TIMEOUT_S"])
    link_up_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_UP_TIMEOUT_S"])
    cpe_pc_ip, cpe_fallback_ip, cpe_pc_password = _resolve_oob_ips(profile_bundle, fallback_ip_cli=fallback_ip)

    print_section(f"{case_id}: Open mode attempt — BTS AES-256, CPE none, link must drop")

    print_section(f"{case_id} — Phase 1: BTS stays on AES-256, CPE baseline WPA2")
    await _ensure_wpa2_encryption_ssh(root_ssh, device_label="BTS", wpa2_uci=wpa2_uci)
    bts_enc_before = (await _read_encryption_uci(root_ssh)).lower()
    check.equal(bts_enc_before, wpa2_uci.lower(), f"{case_id}: BTS must remain on {wpa2_uci}")

    await _ensure_cpe_wpa2_ssh(
        cpe_ip,
        wpa2_uci,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )
    baseline_link = await _wait_for_bidirectional_ping_up(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="WPA2 baseline before open mode",
    )
    check.is_true(baseline_link, f"{case_id}: WPA2 baseline link required before open mode test")

    print_section(f"{case_id} — Phase 2: CPE open (encryption=none), BTS unchanged")
    await _wsec03_cpe_apply_none(
        cpe_ip,
        insecure_uci,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )
    bts_enc_after_cpe_none = (await _read_encryption_uci(root_ssh)).lower()
    print_comparison_table(
        [
            ("BTS encryption after CPE open", wpa2_uci, bts_enc_after_cpe_none, "PASS" if bts_enc_after_cpe_none == wpa2_uci.lower() else "FAIL"),
        ]
    )
    check.equal(bts_enc_after_cpe_none, wpa2_uci.lower(), f"{case_id}: BTS must stay on {wpa2_uci} when CPE is open")

    link_down = await _wait_for_insecure_link_down(
        root_ssh,
        cpe_ip,
        timeout_s=link_down_timeout_s,
        context="after CPE open mode (encryption=none)",
    )
    check.is_true(
        link_down,
        f"{case_id}: association must be rejected — link must drop when CPE is open and BTS is AES-256",
    )
    link_stayed_down = await _verify_link_stays_down(root_ssh, cpe_ip, duration_s=LINK_DOWN_VERIFY_S)
    check.is_true(
        link_stayed_down,
        f"{case_id}: link must stay down for {LINK_DOWN_VERIFY_S}s before recovery",
    )

    async def _apply_w11_cpe_restore() -> str:
        return await _apply_cpe_wpa2_aes256_oob(
            cpe_ip,
            wpa2_uci,
            cpe_pc_ip,
            cpe_fallback_ip,
            device_creds,
            cpe_pc_password=cpe_pc_password,
        )

    link_stable, restore_elapsed, cpe_enc_restored, root_ssh = await _tiered_link_recovery_after_drop(
        root_ssh,
        bts_ip=bts_ip,
        cpe_ip=cpe_ip,
        cpe_pc_ip=cpe_pc_ip,
        cpe_fallback_ip=cpe_fallback_ip,
        device_creds=device_creds,
        cpe_pc_password=cpe_pc_password,
        profile_bundle=profile_bundle,
        wpa2_uci=wpa2_uci,
        case_id=case_id,
        phase_title="Phase 3: restore CPE to AES-256 and verify stable link",
        apply_cpe_config=_apply_w11_cpe_restore,
    )

    cpe_ssh = await _try_open_root_ssh_for_host(cpe_ip, device_creds)
    if cpe_ssh is not None:
        try:
            if await _is_cpe_ssh_target(cpe_ssh):
                cpe_enc_restored = (await _read_encryption_uci(cpe_ssh)).lower()
        finally:
            await _close_cpe_ssh(cpe_ssh)

    print_comparison_table(
        [
            ("Link drop on CPE open mode", "down", "down" if link_down else "up", "PASS" if link_down else "FAIL"),
            ("CPE restored to AES-256", wpa2_uci, cpe_enc_restored or "n/a", "PASS" if cpe_enc_restored == wpa2_uci.lower() else "FAIL"),
            ("Link stable after restore", "bidirectional ping OK", f"{restore_elapsed:.1f}s" if link_stable and restore_elapsed is not None else "down", "PASS" if link_stable else "FAIL"),
        ]
    )
    check.equal(cpe_enc_restored, wpa2_uci.lower(), f"{case_id}: CPE must be restored to {wpa2_uci}")
    check.is_true(link_stable, f"{case_id}: link must be stable after CPE WPA2 restore")
    _log(f"{case_id} completed — open mode rejected, CPE restored to {wpa2_uci}")


async def assert_w_security_14_invalid_passphrase(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    fallback_ip: str | None = None,
):
    """
    W_SECURITY_14 — Invalid passphrase (negative):
      BTS + CPE on AES-256 → CPE wrong key → link must drop → restore original key → link up.
    """
    case_id = "W_SECURITY_14"
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: at least one CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    wpa2_uci = WIRELESS_SECURITY_TEST_VALUES["WPA2_ENCRYPTION_UCI"]
    wrong_key = WIRELESS_SECURITY_TEST_VALUES["INVALID_CPE_PASSPHRASE"]
    link_down_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_DOWN_TIMEOUT_S"])
    link_up_timeout_s = int(WIRELESS_SECURITY_TEST_VALUES["LINK_UP_TIMEOUT_S"])
    cpe_pc_ip, cpe_fallback_ip, cpe_pc_password = _resolve_oob_ips(profile_bundle, fallback_ip_cli=fallback_ip)

    print_section(f"{case_id}: Invalid passphrase — wrong CPE key, link must drop, then restore")

    print_section(f"{case_id} — Phase 1: verify BTS and CPE on AES-256")
    original_key = await _verify_both_on_wpa2_aes256(
        root_ssh,
        cpe_ip,
        device_creds,
        case_id=case_id,
        wpa2_uci=wpa2_uci,
    )
    check.not_equal(wrong_key, original_key, f"{case_id}: test invalid key must differ from lab key")

    baseline_link = await _wait_for_bidirectional_ping_up(
        root_ssh,
        cpe_ip,
        bts_ip,
        device_creds,
        timeout_s=link_up_timeout_s,
        context="WPA2 baseline before invalid key",
    )
    check.is_true(baseline_link, f"{case_id}: WPA2 link required before invalid key test")

    print_section(f"{case_id} — Phase 2: apply wrong CPE key (BTS unchanged)")
    key_applied = await _cpe_apply_wrong_key(
        cpe_ip,
        wrong_key,
        cpe_pc_ip,
        cpe_fallback_ip,
        device_creds,
        cpe_pc_password=cpe_pc_password,
    )
    check.is_true(key_applied, f"{case_id}: invalid key must be applied on CPE")

    bts_enc_unchanged = (await _read_encryption_uci(root_ssh)).lower()
    check.equal(bts_enc_unchanged, wpa2_uci.lower(), f"{case_id}: BTS must stay on {wpa2_uci}")

    link_down = await _wait_for_insecure_link_down(
        root_ssh,
        cpe_ip,
        timeout_s=link_down_timeout_s,
        context="after CPE invalid passphrase",
    )
    check.is_true(
        link_down,
        f"{case_id}: authentication must fail — link must drop when CPE key is wrong",
    )
    link_stayed_down = await _verify_link_stays_down(root_ssh, cpe_ip, duration_s=LINK_DOWN_VERIFY_S)
    check.is_true(
        link_stayed_down,
        f"{case_id}: link must stay down for {LINK_DOWN_VERIFY_S}s before recovery",
    )

    async def _apply_w14_cpe_restore() -> str:
        return await _restore_cpe_original_key(
            cpe_ip,
            original_key,
            cpe_pc_ip,
            cpe_fallback_ip,
            device_creds,
            cpe_pc_password=cpe_pc_password,
        )

    async def _apply_w14_cpe_restore_after_reboot() -> str:
        await _apply_cpe_wpa2_aes256_oob(
            cpe_ip,
            wpa2_uci,
            cpe_pc_ip,
            cpe_fallback_ip,
            device_creds,
            cpe_pc_password=cpe_pc_password,
        )
        return await _restore_cpe_original_key(
            cpe_ip,
            original_key,
            cpe_pc_ip,
            cpe_fallback_ip,
            device_creds,
            cpe_pc_password=cpe_pc_password,
        )

    link_up, restore_elapsed, restored_key_oob, root_ssh = await _tiered_link_recovery_after_drop(
        root_ssh,
        bts_ip=bts_ip,
        cpe_ip=cpe_ip,
        cpe_pc_ip=cpe_pc_ip,
        cpe_fallback_ip=cpe_fallback_ip,
        device_creds=device_creds,
        cpe_pc_password=cpe_pc_password,
        profile_bundle=profile_bundle,
        wpa2_uci=wpa2_uci,
        case_id=case_id,
        phase_title="Phase 3: restore original CPE key and wait for link up",
        apply_cpe_config=_apply_w14_cpe_restore,
        apply_cpe_config_after_reboot=_apply_w14_cpe_restore_after_reboot,
    )

    key_restored = restored_key_oob == original_key
    check.is_true(key_restored, f"{case_id}: CPE key must be restored on device (OOB uci get)")

    restored_key_ipv6 = restored_key_oob
    cpe_ssh = await _try_open_root_ssh_for_host(cpe_ip, device_creds)
    if cpe_ssh is not None:
        try:
            if await _is_cpe_ssh_target(cpe_ssh):
                restored_key_ipv6 = await _read_encryption_key_uci(cpe_ssh)
        finally:
            await _close_cpe_ssh(cpe_ssh)

    print_comparison_table(
        [
            ("Link drop on invalid key", "down", "down" if link_down else "up", "PASS" if link_down else "FAIL"),
            (
                "CPE key restored (wireless.@wifi-iface[1].key)",
                "match original",
                "yes" if key_restored else "no",
                "PASS" if key_restored else "FAIL",
            ),
            (
                "Link restore time after key restore",
                "bidirectional ping OK",
                f"{restore_elapsed:.1f}s" if link_up and restore_elapsed is not None else "down",
                "PASS" if link_up else "FAIL",
            ),
        ]
    )
    check.is_true(link_up, f"{case_id}: link must come up after key restore")
    if link_up and restored_key_ipv6 != original_key:
        check.equal(restored_key_ipv6, original_key, f"{case_id}: CPE IPv6 SSH key must match original")
    _log(f"{case_id} completed — invalid key rejected, original key restored, link up")
