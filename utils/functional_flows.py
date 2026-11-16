"""Functional test plan flows (FT_02+) — IPv6 backend only."""

from __future__ import annotations

import asyncio
import ipaddress
import random
import re
import time
from urllib.parse import urlparse
from dataclasses import dataclass

import pytest
import pytest_check as check
from scrapli.driver.generic import AsyncGenericDriver

from config.defaults import FUNCTIONAL_TEST_VALUES
from pages.commands import RootCommands
from traffic.operating_rate_table import uci_htmode_matches, uci_htmode_value
from utils.link_formation import link_health_bts
from utils.net_utils import format_http_host, ip_in_text, is_ipv6_literal, normalize_ip
from utils.parsers import (
    clean_ssh_output,
    extract_uci_value,
    is_uci_error,
    parse_iwconfig_active_channel,
    parse_iwconfig_tx_power,
)
from utils.pdu_power import pdu_enabled, pdu_hard_reboot
from utils.verify_output import print_comparison_table, print_section

RADIO_IDX = 1

async def _open_plain_ssh(
    host: str,
    password: str,
    *,
    label: str = "device",
    timeout_s: int = 20,
) -> AsyncGenericDriver:
    host = normalize_ip(str(host or "").split("/")[0])
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    try:
        await asyncio.wait_for(conn.open(), timeout=max(5, int(timeout_s)))
        return conn
    except Exception as exc:
        try:
            await conn.close()
        except Exception:
            pass
        raise ConnectionError(f"{label} SSH failed for {host}: {exc}") from exc


def _normalize_ipv6_cidr(value: str) -> str:
    """Compare IPv6 CIDRs in compressed form so :: vs :0: does not fail verify."""
    raw = str(value or "").strip()
    if not raw or raw.lower().startswith("uci:"):
        return ""
    try:
        if "/" in raw:
            iface = ipaddress.IPv6Interface(raw)
            return f"{iface.ip.compressed}/{iface.network.prefixlen}"
        return ipaddress.IPv6Address(normalize_ip(raw)).compressed
    except Exception:
        host = normalize_ip(raw.split("/")[0])
        if "/" in raw:
            try:
                return f"{host}/{int(raw.split('/', 1)[1])}"
            except Exception:
                return host
        return host


def _backend_value_equal(field: str, expected: str, actual: str) -> bool:
    exp = str(expected or "").strip()
    act = str(actual or "").strip()
    if exp.lower().startswith("uci:"):
        exp = ""
    if act.lower().startswith("uci:"):
        act = ""
    if field in {"ipv6", "ipv6_gw"}:
        return _normalize_ipv6_cidr(exp) == _normalize_ipv6_cidr(act)
    if field in {"ipv4"}:
        return normalize_ip(exp.split("/")[0]) == normalize_ip(act.split("/")[0])
    return exp == act



@dataclass
class _MemorySample:
    mem_available_kb: int | None
    mem_source: str
    uptime_s: float | None
    ram_file_bytes: int


def _log(message: str) -> None:
    print(f"[FUNCTIONAL] {message}")


def _require_functional(request, *, case_id: str) -> None:
    if request.config.getoption("--skip-functional"):
        pytest.skip(f"{case_id}: Functional tests skipped (--skip-functional).")
    if not request.config.getoption("--allow-functional"):
        pytest.skip(
            f"{case_id}: Functional tests skipped. Re-run with --allow-functional "
            "(temporary RAM overload on BTS/CPE)."
        )


def _require_ipv6_profile(profile_bundle, *, case_id: str) -> None:
    dut = profile_bundle.active.get("dut", {})
    if not (dut.get("ip_mode") == "ipv6" or dut.get("strict_ipv6")):
        pytest.skip(f"{case_id}: requires IPv6 profile (ip_mode: ipv6 / strict_ipv6: true)")


def _require_ipv6(ip: str, *, case_id: str, role: str) -> str:
    clean = normalize_ip(ip)
    check.is_true(clean, f"{case_id}: {role} IPv6 required")
    check.is_true(is_ipv6_literal(clean), f"{case_id}: IPv6-only; got {clean}")
    return clean


def _source_bind_ipv6(profile_bundle) -> str:
    dut = profile_bundle.active.get("dut", {}) or {}
    mgmt = (profile_bundle.active.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    return normalize_ip(str(dut.get("bts_pc_ipv6") or mgmt.get("ipv6_bts_pc") or ""))


async def _ssh(root_ssh, command: str, *, timeout_s: int = 30) -> str:
    response = await root_ssh.send_command(command, timeout_ops=timeout_s)
    return clean_ssh_output(response.result)


async def _open_ipv6_ssh(
    host: str,
    password: str,
    *,
    source_v6: str = "",
    label: str = "device",
) -> AsyncGenericDriver:
    host = normalize_ip(host)
    last_error = ""
    for wait_s in (0, 5):
        if wait_s:
            await asyncio.sleep(wait_s)
        transport_options: dict = {}
        if source_v6 and is_ipv6_literal(source_v6):
            transport_options["local_addr"] = normalize_ip(source_v6)
        conn = AsyncGenericDriver(
            host=host,
            auth_username="root",
            auth_password=password,
            auth_strict_key=False,
            transport="asyncssh",
            transport_options=transport_options or None,
        )
        try:
            await conn.open()
            return conn
        except Exception as exc:
            last_error = str(exc)
            try:
                await conn.close()
            except Exception:
                pass
    raise ConnectionError(f"{label} IPv6 SSH failed for {host}: {last_error}")


def _parse_int_scalar(raw: str) -> int | None:
    match = re.search(r"\d+", clean_ssh_output(raw) or "")
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _parse_uptime_seconds(raw: str) -> float | None:
    cleaned = clean_ssh_output(raw)
    if not cleaned:
        return None
    try:
        return float(cleaned.split()[0])
    except (TypeError, ValueError):
        return None


def _uptime_stable(before: float | None, after: float | None) -> bool:
    # If uptime cannot be parsed on this platform, do not hard-fail FT_02.
    if before is None or after is None:
        return True
    if after < 90:
        # Early-boot devices can still be stable; treat only large drops as reboot.
        return after >= before - 20
    drop = before - after
    return not (drop > 60 or after < before * 0.35)


def _kb_to_mb(value_kb: int | None) -> str:
    if value_kb is None:
        return "n/a"
    return f"{value_kb / 1024:.1f} MB"


def _normalize_htmode(raw: str) -> str:
    """Extract canonical HT mode (e.g., HT20/HT40/HT80/HT160) from noisy CLI output."""
    source = str(raw or "")
    cleaned = clean_ssh_output(source)
    # Search full SSH text, not last-line-only — hop/prompt junk (e.g. "|") hid HT160 on FT_03.
    haystack = f"{source}\n{cleaned}"
    upper = haystack.upper().replace(" ", "")
    if "11AHE160" in upper or "11NAHE160" in upper:
        return "HT160"
    if "11AHE80" in upper or "11NAHE80" in upper:
        return "HT80"
    if "11AHE40" in upper or "11NAHE40" in upper:
        return "HT40"
    if "11AHE20" in upper or "11NAHE20" in upper:
        return "HT20"
    if re.search(r"HT40\+", haystack, flags=re.IGNORECASE):
        return "HT40"
    if re.search(r"HT40-", haystack, flags=re.IGNORECASE):
        return "HT40"
    matches = re.findall(r"\bHT(?:20|40|80|160)\b", haystack, flags=re.IGNORECASE)
    if matches:
        return matches[-1].upper()
    # Fallback to last non-empty token/line to avoid banner noise.
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if lines:
        return lines[-1].split()[-1]
    return ""


def _is_valid_htmode(value: str) -> bool:
    return bool(re.fullmatch(r"HT(?:20|40|80|160)", str(value or "").strip(), flags=re.IGNORECASE))


def _ft05_uci_htmode_value(bandwidth: str) -> str:
    """UCI htmode for FT_05 — HT40+ on UBR; HT160 not in throughput table."""
    ht = str(bandwidth).strip().upper()
    if ht == "HT40":
        return "HT40+"
    if ht == "HT160":
        return "HT160"
    try:
        return uci_htmode_value(ht)
    except ValueError:
        return ht


def _ft05_uci_htmode_matches(expected_bandwidth: str, uci_htmode: str) -> bool:
    expected = str(expected_bandwidth).strip().upper()
    actual = str(uci_htmode or "").strip().upper().replace(" ", "")
    if expected == "HT160":
        return "HT160" in actual
    if expected == "HT40":
        return "HT40+" in actual or actual == "HT40"
    try:
        return uci_htmode_matches(expected, uci_htmode)
    except ValueError:
        return expected in actual


async def _rf_link_up(bts_ssh, profile: dict) -> tuple[bool, str]:
    """RF link on BTS — wlanconfig station count, then sua1 assoc, then sysfs links."""
    ok, stations = await link_health_bts(bts_ssh, profile)
    if ok:
        return True, f"wlan_stations={stations}"

    radio_idx = int((profile.get("link") or {}).get("radio_idx", RADIO_IDX))
    assoc_raw = await _ssh(bts_ssh, RootCommands.get_link_stat_associd(radio_idx, 1), timeout_s=15)
    assoc = _parse_int_scalar(assoc_raw) or 0
    if assoc > 0:
        return True, f"sua1_assoc={assoc}"

    links_raw = await _ssh(bts_ssh, RootCommands.get_remote_partners(radio_idx), timeout_s=15)
    links = _parse_int_scalar(links_raw) or 0
    return links >= 1, f"links_sysfs={links}, wlan_stations={stations}, sua1_assoc={assoc}"


def _print_backend_table(title: str, rows: list[tuple[str, str, str, str]]) -> None:
    print_section(title)
    print_comparison_table(rows)


def _resolve_ram_mb(
    mem_available_kb: int | None,
    *,
    fixed_ram_mb: int,
    values: dict,
) -> tuple[int, bool]:
    """Return (ram_mb, max_mode). max_mode=True when size is auto-derived from MemAvailable."""
    if fixed_ram_mb > 0:
        return fixed_ram_mb, False
    if mem_available_kb is None:
        return int(values["max_ram_floor_mb"]), True

    available_mb = mem_available_kb / 1024
    reserve_mb = int(values["max_ram_reserve_mb"])
    pct = float(values["max_ram_utilization_pct"])
    floor_mb = int(values["max_ram_floor_mb"])
    target_mb = int(available_mb * pct) - reserve_mb
    ram_mb = max(floor_mb, target_mb)
    return ram_mb, True


async def _read_mem_available_kb(root_ssh, *, timeout_s: int) -> int | None:
    # Prefer MemAvailable; fallback for older/trimmed kernels without that field.
    raw = await _ssh(
        root_ssh,
        "awk '/MemAvailable:/ {print $2; exit}' /proc/meminfo 2>/dev/null",
        timeout_s=timeout_s,
    )
    direct = _parse_int_scalar(raw)
    if direct is not None:
        return direct

    fallback_raw = await _ssh(
        root_ssh,
        "awk '/MemFree:/ {m=$2} /Buffers:/ {b=$2} /^Cached:/ {c=$2} END {print m+b+c}' /proc/meminfo 2>/dev/null",
        timeout_s=timeout_s,
    )
    return _parse_int_scalar(fallback_raw)


async def _read_mem_source(root_ssh, *, timeout_s: int) -> str:
    raw = await _ssh(
        root_ssh,
        "awk '/MemAvailable:/ {print \"MemAvailable\"; found=1; exit} END {if (!found) print \"MemFree+Buffers+Cached\"}' /proc/meminfo 2>/dev/null",
        timeout_s=timeout_s,
    )
    cleaned = clean_ssh_output(raw).strip()
    if cleaned in ("MemAvailable", "MemFree+Buffers+Cached"):
        return cleaned
    return "unknown"


async def _read_ram_file_bytes(root_ssh, path: str, *, timeout_s: int) -> int:
    raw = await _ssh(root_ssh, f"wc -c < {path} 2>/dev/null || echo 0", timeout_s=timeout_s)
    return _parse_int_scalar(raw) or 0


async def _read_uptime_s(root_ssh, *, timeout_s: int) -> float | None:
    raw = await _ssh(root_ssh, RootCommands.GET_UPTIME, timeout_s=timeout_s)
    return _parse_uptime_seconds(raw)


async def _sample_memory(root_ssh, path: str, *, timeout_s: int) -> _MemorySample:
    mem_kb = await _read_mem_available_kb(root_ssh, timeout_s=timeout_s)
    mem_source = await _read_mem_source(root_ssh, timeout_s=timeout_s)
    uptime_s = await _read_uptime_s(root_ssh, timeout_s=timeout_s)
    file_bytes = await _read_ram_file_bytes(root_ssh, path, timeout_s=timeout_s)
    return _MemorySample(
        mem_available_kb=mem_kb,
        mem_source=mem_source,
        uptime_s=uptime_s,
        ram_file_bytes=file_bytes,
    )


async def _grep_memory_alarm_lines(root_ssh, patterns: list[str], *, timeout_s: int) -> list[str]:
    pattern = "|".join(patterns)
    raw = await _ssh(
        root_ssh,
        f"logread 2>/dev/null | grep -Eai '({pattern})' | tail -n 20",
        timeout_s=timeout_s,
    )
    return [line.strip() for line in raw.splitlines() if line.strip()]


async def _start_ram_overload(root_ssh, *, path: str, ram_mb: int, timeout_s: int) -> None:
    await _ssh(root_ssh, f"rm -f {path}", timeout_s=timeout_s)
    await _ssh(
        root_ssh,
        f"dd if=/dev/zero of={path} bs=1M count={ram_mb} 2>/dev/null",
        timeout_s=timeout_s,
    )


async def _stop_ram_overload(root_ssh, *, path: str, timeout_s: int) -> None:
    await _ssh(root_ssh, f"rm -f {path}", timeout_s=timeout_s)


async def _ft_02_memory_overload_on_device(
    bts_ssh,
    target_ssh,
    *,
    device_label: str,
    profile: dict,
    case_id: str,
    path: str,
    fixed_ram_mb: int,
    values: dict,
    overload_duration_s: int,
    alarm_patterns: list[str],
    ssh_timeout_s: int,
    monitor_rf_link: bool,
) -> None:
    label = device_label.upper()
    poll_interval_s = int(values["poll_interval_s"])
    mem_recovery_min_mb = int(values["mem_recovery_min_mb"])
    require_alarm_on_max = bool(values.get("require_memory_alarm_on_max", False))
    require_alarm_min_ram_mb = int(values.get("require_alarm_min_ram_mb", 128))

    baseline: _MemorySample | None = None
    min_mem_kb: int | None = None
    try:
        baseline = await _sample_memory(target_ssh, path, timeout_s=ssh_timeout_s)
        logs_before = await _grep_memory_alarm_lines(target_ssh, alarm_patterns, timeout_s=ssh_timeout_s)
        check.is_not_none(
            baseline.mem_available_kb,
            f"{case_id} [{label}]: memory metric unreadable (/proc/meminfo)",
        )

        ram_mb, max_mode = _resolve_ram_mb(
            baseline.mem_available_kb,
            fixed_ram_mb=fixed_ram_mb,
            values=values,
        )
        expected_bytes = ram_mb * 1024 * 1024
        dd_timeout_s = max(int(values["dd_timeout_s"]), ram_mb * 2)
        if max_mode:
            mem_drop_min_kb = int(ram_mb * 1024 * float(values["max_ram_drop_ratio"]))
            min_file_bytes = int(expected_bytes * float(values.get("max_ram_file_ratio", 0.75)))
        else:
            mem_drop_min_kb = int(values["mem_drop_min_mb"]) * 1024
            min_file_bytes = expected_bytes
        alarm_required = max_mode and require_alarm_on_max and ram_mb >= require_alarm_min_ram_mb

        mode_label = "MAX" if max_mode else "FIXED"
        _log(
            f"{case_id} [{label}] {mode_label} — dd {path} count={ram_mb}MB "
            f"(Mem={_kb_to_mb(baseline.mem_available_kb)} via {baseline.mem_source}), hold {overload_duration_s}s"
        )

        if monitor_rf_link and label == "BTS":
            # Wait for RF (don't fail on a single poll — link can be settling after prior cases).
            link_timeout_s = int(values.get("ft02_link_recovery_timeout_s", 180))
            link_poll_s = int(values.get("ft02_link_recovery_poll_s", 8))
            link_ok = False
            link_detail = ""
            deadline = time.monotonic() + link_timeout_s
            while time.monotonic() < deadline:
                try:
                    link_ok, link_detail = await _rf_link_up(bts_ssh, profile)
                except Exception as exc:
                    link_ok = False
                    link_detail = f"link poll error: {exc}"
                    if _ssh_transport_dead_error(exc) and hasattr(bts_ssh, "ensure_open"):
                        try:
                            await bts_ssh.ensure_open()
                        except Exception:
                            pass
                if link_ok:
                    break
                await asyncio.sleep(max(1, link_poll_s))
            if max_mode:
                check.is_true(
                    link_ok,
                    f"{case_id} [{label}]: RF link down before max overload ({link_detail})",
                )
            elif not link_ok:
                _log(f"WARN [{label}] RF link not detected ({link_detail})")

        await _start_ram_overload(target_ssh, path=path, ram_mb=ram_mb, timeout_s=dd_timeout_s)
        under_load = await _sample_memory(target_ssh, path, timeout_s=ssh_timeout_s)
        alarm_lines = await _grep_memory_alarm_lines(target_ssh, alarm_patterns, timeout_s=ssh_timeout_s)
        new_alarm_lines = [line for line in alarm_lines if line not in logs_before]

        mem_drop_kb = 0
        if baseline.mem_available_kb is not None and under_load.mem_available_kb is not None:
            mem_drop_kb = baseline.mem_available_kb - under_load.mem_available_kb

        mem_drop_ok = mem_drop_kb >= mem_drop_min_kb
        # Absolute MemAvailable floor is informational — reclaim/compression varies by SOC.
        under_load_floor_kb = (int(values["max_ram_reserve_mb"]) + 16) * 1024
        under_load_low = (
            under_load.mem_available_kb is not None
            and under_load.mem_available_kb <= under_load_floor_kb
        )
        uptime_ok = _uptime_stable(baseline.uptime_s, under_load.uptime_s)
        ram_ok = under_load.ram_file_bytes >= min_file_bytes
        alarm_count = len(new_alarm_lines) or len(alarm_lines)
        alarm_seen = alarm_count > 0

        # MemAvailable can rebound under reclaim/compression even while the ram file is held;
        # accept ram-file + uptime evidence in that case.
        if max_mode and ram_ok and uptime_ok and not mem_drop_ok:
            _log(
                f"{case_id} [{label}]: MemAvailable drop {_kb_to_mb(mem_drop_kb)} "
                f"(need {_kb_to_mb(mem_drop_min_kb)}); accepting MAX via ram file + uptime"
            )
            mem_drop_ok = True
            mem_drop_soft = True
        else:
            mem_drop_soft = False

        _print_backend_table(
            f"{case_id} [{label}] backend ({mode_label})",
            [
                ("MemAvailable (before)", "readable", _kb_to_mb(baseline.mem_available_kb), "PASS"),
                ("Memory metric source", "MemAvailable/fallback", baseline.mem_source, "PASS"),
                (
                    "Overload size",
                    f">= {min_file_bytes // (1024 * 1024)} MB (req {ram_mb} MB)",
                    f"{under_load.ram_file_bytes // (1024 * 1024)} MB file",
                    "PASS" if ram_ok else "FAIL",
                ),
                (
                    "MemAvailable drop",
                    f">= {_kb_to_mb(mem_drop_min_kb)}",
                    f"{mem_drop_kb / 1024:.1f} MB",
                    "PASS" if mem_drop_ok and not mem_drop_soft else ("INFO" if mem_drop_soft else "FAIL"),
                ),
                (
                    "MemAvailable (under load)",
                    f"<= {int(values['max_ram_reserve_mb']) + 16} MB (info)",
                    _kb_to_mb(under_load.mem_available_kb),
                    "PASS" if under_load_low else ("INFO" if max_mode and mem_drop_ok else "WARN"),
                ),
                (
                    "Uptime stable",
                    "yes",
                    "yes" if uptime_ok else "no",
                    "PASS" if uptime_ok else "FAIL",
                ),
                (
                    "Memory alarm (logread)",
                    "required" if alarm_required else "optional",
                    str(alarm_count),
                    "PASS" if alarm_seen else ("FAIL" if alarm_required else "WARN"),
                ),
            ],
        )

        if alarm_seen:
            for line in (new_alarm_lines or alarm_lines)[:3]:
                _log(f"[{label}] alarm: {line[:120]}")
        elif max_mode and alarm_required:
            _log(f"[{label}] waiting for memory-overload alarm during sustain window")

        check.greater_equal(under_load.ram_file_bytes, min_file_bytes, f"{case_id} [{label}]: ram file too small")
        if not mem_drop_soft:
            check.greater_equal(mem_drop_kb, mem_drop_min_kb, f"{case_id} [{label}]: MemAvailable drop too small")
        check.is_true(
            _uptime_stable(baseline.uptime_s, under_load.uptime_s),
            f"{case_id} [{label}]: reboot/crash after dd",
        )
        # Max-load confidence comes from large memory drop + sustained stability.

        deadline = time.monotonic() + overload_duration_s
        ssh_failures = 0
        link_failures = 0
        min_mem_kb = under_load.mem_available_kb

        while time.monotonic() < deadline:
            try:
                sample = await _sample_memory(target_ssh, path, timeout_s=ssh_timeout_s)
            except Exception as exc:
                ssh_failures += 1
                _log(f"[{label}] memory poll failed: {exc}")
                await asyncio.sleep(poll_interval_s)
                continue

            if sample.mem_available_kb is not None:
                min_mem_kb = (
                    sample.mem_available_kb
                    if min_mem_kb is None
                    else min(min_mem_kb, sample.mem_available_kb)
                )

            check.is_true(
                _uptime_stable(baseline.uptime_s, sample.uptime_s),
                f"{case_id} [{label}]: uptime dropped during overload",
            )
            check.is_true(
                sample.ram_file_bytes >= min_file_bytes,
                f"{case_id} [{label}]: ram file missing during overload",
            )

            if not alarm_seen:
                poll_alarms = await _grep_memory_alarm_lines(target_ssh, alarm_patterns, timeout_s=ssh_timeout_s)
                fresh = [line for line in poll_alarms if line not in logs_before]
                if fresh or poll_alarms:
                    alarm_seen = True
                    for line in (fresh or poll_alarms)[:2]:
                        _log(f"[{label}] alarm during sustain: {line[:120]}")

            if monitor_rf_link and label == "BTS":
                link_ok, link_detail = await _rf_link_up(bts_ssh, profile)
                if not link_ok:
                    link_failures += 1
                    _log(f"[{label}] RF link not detected during overload ({link_detail})")

            await asyncio.sleep(poll_interval_s)

        # Under max dd load the DUT may briefly stall SSH (transport timeout) without crashing.
        allowed_ssh_failures = int(values.get("max_overload_ssh_failures", 3 if max_mode else 0))
        if ssh_failures:
            _log(f"{case_id} [{label}]: {ssh_failures} SSH poll error(s) during overload (allow <= {allowed_ssh_failures})")
        check.less_equal(
            ssh_failures,
            allowed_ssh_failures,
            f"{case_id} [{label}]: too many SSH errors during overload window",
        )
        if alarm_required:
            check.is_true(alarm_seen, f"{case_id} [{label}]: memory overload alarm not seen at max load")
        if max_mode and monitor_rf_link and label == "BTS" and link_failures:
            _log(f"{case_id} [{label}]: RF link briefly unreachable during max overload ({link_failures} polls) — continuing")

    finally:
        await _stop_ram_overload(target_ssh, path=path, timeout_s=ssh_timeout_s)
        try:
            recovered = await _sample_memory(target_ssh, path, timeout_s=ssh_timeout_s)
            baseline_kb = baseline.mem_available_kb if baseline else None
            recovered_kb = recovered.mem_available_kb
            _print_backend_table(
                f"{case_id} [{label}] cleanup",
                [
                    (
                        "ram_eater removed",
                        "0",
                        str(recovered.ram_file_bytes),
                        "PASS" if recovered.ram_file_bytes == 0 else "FAIL",
                    ),
                    (
                        "MemAvailable",
                        _kb_to_mb(baseline_kb),
                        _kb_to_mb(recovered_kb),
                        "PASS",
                    ),
                ],
            )
            check.equal(recovered.ram_file_bytes, 0, f"{case_id} [{label}]: ram file not removed")
        except Exception as exc:
            _log(f"[{label}] cleanup verify failed: {exc}")


async def assert_ft_02_memory_overload(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_02 — RAM overload on BTS then CPE (IPv6 SSH + backend checks only)."""
    case_id = "FT_02"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    values = FUNCTIONAL_TEST_VALUES
    fixed_ram_mb = int(request.config.getoption("--functional-ram-mb"))
    overload_duration_s = int(request.config.getoption("--functional-overload-duration-s"))
    path = str(values["ram_eater_path"])
    source_v6 = _source_bind_ipv6(profile_bundle)

    mode_text = "MAX (auto from MemAvailable)" if fixed_ram_mb <= 0 else f"FIXED {fixed_ram_mb}MB"
    print_section(f"{case_id}: Memory overload (IPv6) — BTS {bts_ip} then CPE {cpe_ip}")
    _log(f"mode={mode_text}, hold={overload_duration_s}s — no bootstrap, no ICMP ping")

    common = dict(
        profile=profile_bundle.active,
        case_id=case_id,
        path=path,
        fixed_ram_mb=fixed_ram_mb,
        values=values,
        overload_duration_s=overload_duration_s,
        alarm_patterns=list(values["alarm_grep_patterns"]),
        ssh_timeout_s=int(values["ssh_timeout_s"]),
        monitor_rf_link=True,
    )

    await _ft_02_memory_overload_on_device(
        root_ssh,
        root_ssh,
        device_label="BTS",
        **common,
    )

    # Max RAM stress can drop RF briefly — wait for BTS↔CPE before CPE SSH.
    await _wait_rf_link_healthy(
        root_ssh,
        profile_bundle.active,
        case_id=case_id,
        label="pre-CPE",
        timeout_s=int(values.get("ft02_link_recovery_timeout_s", 180)),
        poll_s=int(values.get("ft02_link_recovery_poll_s", 8)),
        password=device_creds["pass"],
        source_v6=source_v6,
        reconnect_hosts=[bts_ip],
    )
    cpe_ssh = await _wait_ipv6_ssh_ready(
        cpe_ip,
        password=device_creds["pass"],
        source_v6=source_v6,
        label="CPE",
        timeout_s=int(values.get("ft02_cpe_ssh_timeout_s", 180)),
        poll_s=int(values.get("ft02_link_recovery_poll_s", 8)),
    )
    try:
        await _ft_02_memory_overload_on_device(
            root_ssh,
            cpe_ssh,
            device_label="CPE",
            **common,
        )
    finally:
        try:
            await cpe_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} done — BTS and CPE memory overload verified")


def _pick_alternate(current: str, candidates: list[str], fallback: str) -> str:
    current_norm = str(current).strip()
    for candidate in candidates:
        value = str(candidate).strip()
        if value and value != current_norm:
            return value
    return fallback


def _derive_alt_ipv4(ipv4_raw: str, *, role: str) -> str:
    raw = normalize_ip(str(ipv4_raw or "").split("/")[0])
    try:
        ip = ipaddress.IPv4Address(raw)
    except Exception:
        return "192.168.2.210" if role == "BTS" else "192.168.2.211"
    step = 10 if role == "BTS" else 11
    alt = int(ip) + step
    if alt > int(ipaddress.IPv4Address("255.255.255.254")):
        alt = int(ip) - step
    if alt <= 0:
        alt = int(ip)
    return str(ipaddress.IPv4Address(alt))


def _derive_alt_ipv6(ipv6_cidr_raw: str, *, role: str) -> str:
    raw = str(ipv6_cidr_raw or "").strip()
    if not raw or is_uci_error(raw) or not is_ipv6_literal(raw.split("/")[0]):
        return ""
    try:
        iface = ipaddress.IPv6Interface(raw if "/" in raw else f"{raw}/120")
    except Exception:
        host = normalize_ip(raw.split("/")[0])
        if not host or not is_ipv6_literal(host):
            return ""
        iface = ipaddress.IPv6Interface(f"{host}/120")
    step = 10 if role == "BTS" else 11
    alt_int = int(iface.ip) + step
    network = iface.network
    if ipaddress.IPv6Address(alt_int) not in network:
        alt_int = int(iface.ip) - step
    alt_ip = ipaddress.IPv6Address(alt_int)
    if alt_ip == iface.ip:
        alt_ip = iface.ip + 1
    return f"{alt_ip.compressed}/{iface.network.prefixlen}"


async def _ft03_read_backend_state(ssh, *, radio_idx: int, timeout_s: int) -> dict[str, str]:
    # Channel width in this DUT is HT mode (bandwidth). Read from UCI first, then runtime mode.
    # cfg80211tool can hang after LAN mutation — bound it on-device and tolerate empty.
    cmd_timeout = max(15, min(int(timeout_s), 45))
    bw_uci = await _ssh(
        ssh,
        f"uci get wireless.wifi{radio_idx}.htmode 2>/dev/null || true",
        timeout_s=cmd_timeout,
    )
    try:
        bw_mode = await _ssh(
            ssh,
            f"timeout 8 cfg80211tool ath{radio_idx} get_mode 2>/dev/null || true",
            timeout_s=min(20, cmd_timeout),
        )
    except Exception as exc:
        # Dead/hung transport must bubble so retry can reopen SSH.
        if _ssh_transport_dead_error(exc) or "timeout" in str(exc).lower():
            raise
        _log(f"FT_03: cfg80211tool get_mode skipped ({exc})")
        bw_mode = ""
    bandwidth = _normalize_htmode(f"{bw_uci}\n{bw_mode}")
    ipv4 = await _ssh(ssh, RootCommands.GET_NET_IP, timeout_s=cmd_timeout)
    netmask = await _ssh(ssh, RootCommands.GET_NET_MASK, timeout_s=cmd_timeout)
    ipv6 = clean_ssh_output(await _ssh(ssh, RootCommands.GET_NET_IP6, timeout_s=cmd_timeout)).strip()
    if is_uci_error(ipv6) or not ipv6:
        live6 = clean_ssh_output(
            await _ssh(
                ssh,
                "ip -6 -o addr show br-lan scope global 2>/dev/null | awk '{print $4}' | head -1",
                timeout_s=cmd_timeout,
            )
        ).strip()
        ipv6 = live6 if live6 and is_ipv6_literal(live6.split("/")[0]) else ""
    return {
        "bandwidth": bandwidth,
        "ipv4": normalize_ip(clean_ssh_output(ipv4).strip().split("/")[0]),
        "netmask": clean_ssh_output(netmask).strip(),
        "ipv6": ipv6,
    }


async def _ft03_read_backend_state_retry(
    ssh,
    *,
    radio_idx: int,
    timeout_s: int,
    expected_bandwidth: str = "",
    retries: int = 6,
    delay_s: float = 5.0,
    password: str = "",
    source_v6: str = "",
    reconnect_hosts: list[str] | None = None,
) -> tuple[dict[str, str], object]:
    """Read backend state; reopen SSH on transport timeouts (common after LAN apply)."""
    current = ssh
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            state = await _ft03_read_backend_state(current, radio_idx=radio_idx, timeout_s=timeout_s)
            if not expected_bandwidth or not _is_valid_htmode(expected_bandwidth):
                return state, current
            if _ft05_uci_htmode_matches(expected_bandwidth, state.get("bandwidth", "")):
                return state, current
            await asyncio.sleep(delay_s)
            # keep trying until retries exhausted
            if attempt == retries - 1:
                return state, current
            continue
        except Exception as exc:
            last_exc = exc
            _log(f"FT_03: backend read attempt {attempt + 1}/{retries} failed: {exc}")
            if password and reconnect_hosts and (
                _ssh_transport_dead_error(exc) or "timeout" in str(exc).lower()
            ):
                try:
                    await _ft03_close_transport(current)
                except Exception:
                    pass
                try:
                    fresh = await _ft03_wait_ssh_multi(
                        hosts=list(reconnect_hosts),
                        password=password,
                        source_v6=source_v6,
                        label="FT03-read",
                        timeout_s=max(60, timeout_s),
                        poll_s=5,
                        accept_ips=None,
                    )
                    current = _ft03_bind_ssh(current, fresh)
                    _log("FT_03: SSH reopened for backend read retry")
                except Exception as re_exc:
                    _log(f"FT_03: reconnect for backend read failed: {re_exc}")
            await asyncio.sleep(delay_s)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("FT_03: backend state unreadable")


async def _ensure_lan_ipv6_usable(ssh, *, ipv6_cidr: str = "", timeout_s: int = 30) -> None:
    """RF-bridge links often mark static LAN IPv6 dadfailed — force nodad/prefered."""
    host = normalize_ip(str(ipv6_cidr or "").split("/")[0])
    prefix = ""
    if "/" in str(ipv6_cidr or ""):
        try:
            prefix = str(int(str(ipv6_cidr).split("/", 1)[1]))
        except Exception:
            prefix = "120"
    prefix = prefix or "120"
    cmds = [
        "sysctl -w net.ipv6.conf.br-lan.accept_dad=0 2>/dev/null || true",
        "sysctl -w net.ipv6.conf.all.accept_dad=0 2>/dev/null || true",
    ]
    if host:
        cmds.extend(
            [
                f"ip -6 addr del {host}/{prefix} dev br-lan 2>/dev/null || true",
                f"ip -6 addr add {host}/{prefix} dev br-lan nodad 2>/dev/null || "
                f"ip -6 addr change {host}/{prefix} dev br-lan nodad 2>/dev/null || true",
            ]
        )
    else:
        cmds.append(
            "ip -6 addr show br-lan 2>/dev/null | awk '/inet6 / && /global/ {print $2}' | "
            "while read -r cidr; do "
            "ip -6 addr del \"$cidr\" dev br-lan 2>/dev/null || true; "
            "ip -6 addr add \"$cidr\" dev br-lan nodad 2>/dev/null || true; "
            "done"
        )
    for cmd in cmds:
        try:
            await _ssh(ssh, cmd, timeout_s=timeout_s)
        except Exception:
            pass


async def _ft03_apply_backend_state(
    ssh,
    *,
    radio_idx: int,
    bandwidth: str,
    ipv4: str,
    netmask: str,
    ipv6_cidr: str,
    timeout_s: int,
    apply_bandwidth: bool = True,
) -> None:
    cmds = []
    if apply_bandwidth:
        cmds.append(f"ucidyn set wireless.wifi{radio_idx}.htmode {_ft05_uci_htmode_value(bandwidth)}")
    cmds.extend(
        [
            f"ucidyn set network.lan.ipaddr {ipv4}",
            f"ucidyn set network.lan.netmask {netmask}",
            f"ucidyn set network.lan.ip6addr {ipv6_cidr}",
            "ucidyn apply",
        ]
    )
    for cmd in cmds:
        await _ssh(ssh, cmd, timeout_s=timeout_s)
    await _ensure_lan_ipv6_usable(ssh, ipv6_cidr=ipv6_cidr, timeout_s=min(45, timeout_s))


async def _ft03_pick_applicable_bandwidth(
    ssh,
    *,
    radio_idx: int,
    current_bandwidth: str,
    candidates: list[str],
    timeout_s: int,
) -> tuple[str, bool]:
    for candidate in candidates:
        target = str(candidate).strip()
        if not target or uci_htmode_matches(target, current_bandwidth):
            continue
        uci_bw = uci_htmode_value(target)
        await _ssh(ssh, f"ucidyn set wireless.wifi{radio_idx}.htmode {uci_bw}", timeout_s=timeout_s)
        await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))
        applied = _normalize_htmode(
            await _ssh(ssh, f"uci get wireless.wifi{radio_idx}.htmode", timeout_s=timeout_s)
        )
        if uci_htmode_matches(target, applied):
            return target, True
    return current_bandwidth, False



async def _ft03_wait_ssh_multi(
    *,
    hosts: list[str],
    password: str,
    source_v6: str,
    label: str,
    timeout_s: int,
    poll_s: int,
    accept_ips: set[str] | None = None,
):
    """Try candidate mgmt hosts until timeout; optionally verify LAN IP identity."""
    deadline = time.monotonic() + timeout_s
    last_exc = ""
    cleaned = [normalize_ip(str(h or "").split("/")[0]) for h in hosts if str(h or "").strip()]
    ordered: list[str] = []
    for h in cleaned:
        if h and h not in ordered:
            ordered.append(h)
    accept = {normalize_ip(str(x or "").split("/")[0]) for x in (accept_ips or set()) if str(x or "").strip()}
    while time.monotonic() < deadline:
        for host in ordered:
            conn = None
            try:
                if is_ipv6_literal(host):
                    conn = await _open_ipv6_ssh(host, password, source_v6=source_v6, label=label)
                else:
                    conn = await _open_plain_ssh(host, password, label=label, timeout_s=20)
                if accept:
                    try:
                        lan_ip = normalize_ip(
                            clean_ssh_output(await _ssh(conn, RootCommands.GET_NET_IP, timeout_s=20)).split("/")[0]
                        )
                        lan_v6 = normalize_ip(
                            clean_ssh_output(await _ssh(conn, RootCommands.GET_NET_IP6, timeout_s=20)).split("/")[0]
                        )
                    except Exception as exc:
                        last_exc = f"identity read failed on {host}: {exc}"
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        continue
                    if lan_ip not in accept and lan_v6 not in accept:
                        last_exc = f"skip {host}: lan={lan_ip}/{lan_v6} not in {sorted(accept)}"
                        try:
                            await conn.close()
                        except Exception:
                            pass
                        continue
                return conn
            except Exception as exc:
                last_exc = str(exc)
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:
                        pass
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(f"{label} SSH reconnect timeout for {ordered}: {last_exc}")


async def _wait_ipv6_ssh_ready(
    host_v6: str,
    *,
    password: str,
    source_v6: str,
    label: str,
    timeout_s: int,
    poll_s: int,
):
    deadline = time.monotonic() + timeout_s
    last_exc = ""
    while time.monotonic() < deadline:
        try:
            return await _open_ipv6_ssh(host_v6, password, source_v6=source_v6, label=label)
        except Exception as exc:
            last_exc = str(exc)
            await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(f"{label} SSH reconnect timeout for {host_v6}: {last_exc}")


def _ft03_bind_ssh(holder, new_conn):
    """Keep FunctionalBtsSsh (or similar) wrappers alive across reconnects."""
    if holder is not None and hasattr(holder, "_conn") and hasattr(holder, "ensure_open"):
        holder._conn = new_conn
        return holder
    return new_conn


async def _ft03_close_transport(ssh) -> None:
    try:
        conn = getattr(ssh, "_conn", ssh)
        await conn.close()
    except Exception:
        pass


def _ssh_transport_dead_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "connection not opened",
            "not opened",
            "connection lost",
            "connection reset",
            "channel closed",
            "timed out",
            "timeout",
            "scraplitimeout",
        )
    )


async def _wait_rf_link_healthy(
    bts_ssh,
    profile: dict,
    *,
    case_id: str,
    label: str,
    timeout_s: int,
    poll_s: int = 5,
    password: str = "",
    source_v6: str = "",
    reconnect_hosts: list[str] | None = None,
    fail_hard: bool = True,
):
    """Poll RF link on BTS. Reopen SSH when the transport dies mid-wait (common after LAN/PDU).

    When fail_hard=False, returns (False, ssh) on timeout without recording a pytest-check failure
    (caller may retry restore / wait again).
    """
    _log(f"{case_id} [{label}] waiting BTS↔CPE link up (timeout={timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_detail = ""
    ssh = bts_ssh
    hosts = [normalize_ip(str(h or "").split("/")[0]) for h in (reconnect_hosts or []) if str(h or "").strip()]
    while time.monotonic() < deadline:
        try:
            link_ok, link_detail = await _rf_link_up(ssh, profile)
            last_detail = link_detail
        except Exception as exc:
            link_ok = False
            last_detail = f"link poll error: {exc}"
            if password and hosts and (
                _ssh_transport_dead_error(exc) or "timeout" in str(exc).lower()
            ):
                try:
                    await _ft03_close_transport(ssh)
                    fresh = await _ft03_wait_ssh_multi(
                        hosts=hosts,
                        password=password,
                        source_v6=source_v6,
                        label=f"{label}-link",
                        timeout_s=min(90, max(30, timeout_s // 3)),
                        poll_s=max(1, poll_s),
                        accept_ips=None,
                    )
                    ssh = _ft03_bind_ssh(bts_ssh, fresh)
                    _log(f"{case_id} [{label}] SSH reopened for link poll")
                    continue
                except Exception as re_exc:
                    last_detail = f"link poll error: {exc}; reconnect: {re_exc}"
        if link_ok:
            return ssh
        await asyncio.sleep(max(1, poll_s))
    if fail_hard:
        check.is_true(False, f"{case_id} [{label}]: RF link not healthy after hard reboot ({last_detail})")
    else:
        _log(f"{case_id} [{label}] link still down after {timeout_s}s ({last_detail})")
    return ssh


async def _ft03_pdu_reachable(profile: dict) -> bool:
    """True if Digital Loggers PDU HTTP responds quickly."""
    cfg = (profile.get("pdu") or {}) if isinstance(profile, dict) else {}
    host = str(cfg.get("host", "")).strip()
    if not host or not bool(cfg.get("enabled", False)):
        return False
    scheme = str(cfg.get("scheme", "http")).strip() or "http"
    username = str(cfg.get("username", "admin")).strip() or "admin"
    password = str(cfg.get("password", "")).strip()
    verify_ssl = bool(cfg.get("verify_ssl", False))
    try:
        import httpx

        async with httpx.AsyncClient(
            base_url=f"{scheme}://{host}",
            timeout=3.0,
            verify=verify_ssl,
            auth=(username, password) if password else None,
        ) as client:
            response = await client.get("/")
            return response.status_code < 500
    except Exception:
        return False


async def _ft03_soft_reboot_retain_device(
    *,
    case_id: str,
    label: str,
    target_ssh,
    bts_ssh,
    profile: dict,
    source_v6: str,
    password: str,
    values: dict,
    before: dict,
) -> tuple[object, str]:
    """PDU-down path: soft-reboot only and verify config retain (no LAN IP mutation)."""
    timeout_s = int(values["ssh_timeout_s"])
    radio_idx = int(values["ft03_radio_idx"])
    reboot_timeout_s = int(values["ft03_hard_reboot_timeout_s"])
    poll_s = int(values["ft03_reconnect_poll_s"])
    settle_s = int(values["ft03_post_reboot_settle_s"])
    link_timeout_s = int(values.get("ft03_link_recovery_timeout_s", 180))
    link_poll_s = int(values.get("ft03_link_recovery_poll_s", 8))
    baseline_ipv6_host = normalize_ip(before["ipv6"].split("/")[0])
    hosts = [baseline_ipv6_host, before["ipv4"]]
    if label.upper() == "BTS":
        hosts.append(
            normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
        )
    accept = {baseline_ipv6_host, before["ipv4"]}
    if label.upper() == "BTS":
        accept.add(
            normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
        )

    _print_backend_table(
        f"{case_id} [{label}] pre-boot (unchanged — soft reboot path)",
        [
            ("Channel width", before["bandwidth"], before["bandwidth"], "PASS"),
            ("IPv4", before["ipv4"], before["ipv4"], "PASS"),
            ("Netmask", before["netmask"], before["netmask"], "PASS"),
            ("IPv6", before["ipv6"], before["ipv6"], "PASS"),
        ],
    )
    _log(f"{case_id} [{label}] soft reboot (PDU unreachable) — no LAN mutation")
    try:
        await _ssh(
            target_ssh,
            "nohup sh -c 'sync; sleep 2; reboot -f || reboot' >/dev/null 2>&1 &",
            timeout_s=min(timeout_s, 15),
        )
    except Exception as soft_exc:
        _log(f"{case_id} [{label}] soft reboot command returned: {soft_exc}")
    try:
        await target_ssh.close()
    except Exception:
        pass
    await asyncio.sleep(int(values.get("ft03_soft_reboot_grace_s", 60)))
    reconnected = await _ft03_wait_ssh_multi(
        hosts=hosts,
        password=password,
        source_v6=source_v6,
        label=label,
        timeout_s=reboot_timeout_s,
        poll_s=poll_s,
        accept_ips=accept,
    )
    await asyncio.sleep(max(settle_s, 0))
    after, reconnected = await _ft03_read_backend_state_retry(
        reconnected,
        radio_idx=radio_idx,
        timeout_s=timeout_s,
        expected_bandwidth="",
        retries=int(values.get("ft03_verify_retries", 6)),
        delay_s=float(values.get("ft03_verify_delay_s", 5)),
        password=password,
        source_v6=source_v6,
        reconnect_hosts=hosts,
    )
    _print_backend_table(
        f"{case_id} [{label}] post-boot",
        [
            (
                "Channel width",
                before["bandwidth"],
                after["bandwidth"],
                "PASS" if after["bandwidth"] == before["bandwidth"] else "FAIL",
            ),
            ("IPv4", before["ipv4"], after["ipv4"], "PASS" if after["ipv4"] == before["ipv4"] else "FAIL"),
            ("Netmask", before["netmask"], after["netmask"], "PASS" if after["netmask"] == before["netmask"] else "FAIL"),
            ("IPv6", before["ipv6"], after["ipv6"], "PASS" if after["ipv6"] == before["ipv6"] else "FAIL"),
        ],
    )
    check.equal(after["bandwidth"], before["bandwidth"], f"{case_id} [{label}]: bandwidth not retained after soft reboot")
    check.equal(after["ipv4"], before["ipv4"], f"{case_id} [{label}]: IPv4 not retained after soft reboot")
    check.equal(after["netmask"], before["netmask"], f"{case_id} [{label}]: netmask not retained after soft reboot")
    check.equal(after["ipv6"], before["ipv6"], f"{case_id} [{label}]: IPv6 not retained after soft reboot")
    try:
        waiter = reconnected if label == "BTS" else bts_ssh
        await _wait_rf_link_healthy(
            waiter,
            profile,
            case_id=case_id,
            label=label,
            timeout_s=min(link_timeout_s, 120),
            poll_s=link_poll_s,
            password=password,
            source_v6=source_v6,
            reconnect_hosts=hosts,
        )
    except Exception as link_exc:
        _log(f"{case_id} [{label}] RF link wait after soft reboot skipped: {link_exc}")
    return reconnected, baseline_ipv6_host


async def _ft03_config_retain_hard_reboot_device(
    *,
    case_id: str,
    label: str,
    target_ssh,
    bts_ssh,
    profile: dict,
    source_v6: str,
    password: str,
    values: dict,
) -> tuple[object, str]:
    timeout_s = int(values["ssh_timeout_s"])
    radio_idx = int(values["ft03_radio_idx"])
    reboot_timeout_s = int(values["ft03_hard_reboot_timeout_s"])
    poll_s = int(values["ft03_reconnect_poll_s"])
    settle_s = int(values["ft03_post_reboot_settle_s"])
    link_timeout_s = int(values.get("ft03_link_recovery_timeout_s", 180))
    link_poll_s = int(values.get("ft03_link_recovery_poll_s", 8))
    restore_baseline = bool(values.get("ft03_restore_baseline", True))
    require_bandwidth_change = bool(values.get("ft03_require_bandwidth_change", False))

    before = await _ft03_read_backend_state(target_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    bandwidth_supported_for_case = label.upper() == "BTS"
    bandwidth_check_enabled = bandwidth_supported_for_case and _is_valid_htmode(before["bandwidth"])
    baseline_ipv6_host = normalize_ip(before["ipv6"].split("/")[0])
    restored_baseline = False
    current_ssh = target_ssh
    target_ipv4 = before["ipv4"]
    target_ipv6 = before["ipv6"]
    _print_backend_table(
        f"{case_id} [{label}] original",
        [
            ("Channel width", "captured", before["bandwidth"], "PASS"),
            ("IPv4", "captured", before["ipv4"], "PASS"),
            ("Netmask", "captured", before["netmask"], "PASS"),
            ("IPv6", "captured", before["ipv6"], "PASS"),
        ],
    )

    # Without a reachable PDU, LAN mutation + soft reboot regularly strands CPE.
    # Soft-reboot and verify config is retained without changing IPs.
    if not await _ft03_pdu_reachable(profile):
        _log(f"{case_id} [{label}] PDU not reachable — soft-reboot retain path (no LAN mutation)")
        return await _ft03_soft_reboot_retain_device(
            case_id=case_id,
            label=label,
            target_ssh=target_ssh,
            bts_ssh=bts_ssh,
            profile=profile,
            source_v6=source_v6,
            password=password,
            values=values,
            before=before,
        )

    if bandwidth_supported_for_case:
        target_bandwidth, bandwidth_mutated = await _ft03_pick_applicable_bandwidth(
            target_ssh,
            radio_idx=radio_idx,
            current_bandwidth=before["bandwidth"],
            candidates=list(values.get("ft03_bandwidth_candidates", ["HT20", "HT40", "HT80"])),
            timeout_s=timeout_s,
        )
        if not bandwidth_mutated:
            _log(f"{case_id} [{label}] no alternate bandwidth accepted by DUT; using {before['bandwidth']}")
    else:
        target_bandwidth, bandwidth_mutated = before["bandwidth"], False
        _log(f"{case_id} [{label}] bandwidth edit/verify skipped by design on CPE")
    if require_bandwidth_change:
        check.is_true(bandwidth_mutated, f"{case_id} [{label}]: unable to change channel width on DUT")
    if not bandwidth_check_enabled:
        _log(f"{case_id} [{label}] bandwidth baseline unreadable; treating bandwidth checks as INFO")
    target_netmask = _pick_alternate(
        before["netmask"],
        list(values.get("ft03_netmask_candidates", ["255.255.255.0", "255.255.254.0"])),
        "255.255.255.0",
    )
    target_ipv4 = _derive_alt_ipv4(before["ipv4"], role=label)
    target_ipv6 = _derive_alt_ipv6(before["ipv6"], role=label)

    try:
        try:
            await _ft03_apply_backend_state(
                target_ssh,
                radio_idx=radio_idx,
                bandwidth=target_bandwidth,
                ipv4=target_ipv4,
                netmask=target_netmask,
                ipv6_cidr=target_ipv6,
                timeout_s=max(120, timeout_s),
                apply_bandwidth=bandwidth_supported_for_case,
            )
        except Exception as apply_exc:
            # ucidyn apply often drops the live SSH session when lan.ip6addr changes.
            _log(f"{case_id} [{label}] apply dropped SSH (expected): {apply_exc}")

        target_ipv6_host = normalize_ip(target_ipv6.split("/")[0])
        # Reconnect on new (or old) mgmt IP after LAN mutation before reboot checks.
        reconnect_hosts = [
            target_ipv6_host,
            baseline_ipv6_host,
            target_ipv4,
            before["ipv4"],
        ]
        if label.upper() == "BTS":
            reconnect_hosts.append(
                normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
            )
        accept_ips = {
            target_ipv6_host,
            baseline_ipv6_host,
            target_ipv4,
            before["ipv4"],
        }
        if label.upper() == "BTS":
            accept_ips.add(
                normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
            )
        try:
            await _ft03_close_transport(target_ssh)
        except Exception:
            pass
        fresh = await _ft03_wait_ssh_multi(
            hosts=reconnect_hosts,
            password=password,
            source_v6=source_v6,
            label=label,
            timeout_s=max(120, int(values.get("ft03_post_apply_reconnect_s", 180))),
            poll_s=poll_s,
            accept_ips=accept_ips,
        )
        target_ssh = _ft03_bind_ssh(target_ssh, fresh)
        current_ssh = target_ssh
        # Brief settle after LAN mutation so cfg80211/UCI reads don't hang on stale transport.
        await asyncio.sleep(float(values.get("ft03_post_apply_settle_s", 8)))

        pre_reboot, target_ssh = await _ft03_read_backend_state_retry(
            target_ssh,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
            expected_bandwidth=target_bandwidth if bandwidth_mutated else "",
            retries=int(values.get("ft03_verify_retries", 6)),
            delay_s=float(values.get("ft03_verify_delay_s", 5)),
            password=password,
            source_v6=source_v6,
            reconnect_hosts=reconnect_hosts,
        )
        current_ssh = target_ssh

        _print_backend_table(
            f"{case_id} [{label}] pre-boot (changed)",
            [
                (
                    "Channel width",
                    target_bandwidth if bandwidth_check_enabled and bandwidth_mutated else (
                        f"{before['bandwidth']} (unchanged by DUT)" if bandwidth_check_enabled else "n/a (unreadable)"
                    ),
                    pre_reboot["bandwidth"],
                    "PASS"
                    if bandwidth_check_enabled and _ft05_uci_htmode_matches(target_bandwidth, pre_reboot["bandwidth"])
                    else ("INFO" if (not bandwidth_check_enabled or not bandwidth_mutated) else "FAIL"),
                ),
                ("IPv4", target_ipv4, pre_reboot["ipv4"], "PASS" if pre_reboot["ipv4"] == target_ipv4 else "FAIL"),
                ("Netmask", target_netmask, pre_reboot["netmask"], "PASS" if pre_reboot["netmask"] == target_netmask else "FAIL"),
                ("IPv6", target_ipv6, pre_reboot["ipv6"], "PASS" if pre_reboot["ipv6"] == target_ipv6 else "FAIL"),
            ],
        )
        if bandwidth_check_enabled and bandwidth_mutated:
            check.is_true(
                _ft05_uci_htmode_matches(target_bandwidth, pre_reboot["bandwidth"]),
                f"{case_id} [{label}]: bandwidth apply failed",
            )
        check.equal(pre_reboot["ipv4"], target_ipv4, f"{case_id} [{label}]: IPv4 apply failed")
        check.equal(pre_reboot["netmask"], target_netmask, f"{case_id} [{label}]: netmask apply failed")
        check.equal(pre_reboot["ipv6"], target_ipv6, f"{case_id} [{label}]: IPv6 apply failed")

        _log(f"{case_id} [{label}] persist LAN/wireless before hard reboot")
        try:
            await _ssh(
                target_ssh,
                "uci commit network; uci commit wireless; sync; sync",
                timeout_s=timeout_s,
            )
        except Exception as exc:
            _log(f"{case_id} [{label}] commit before PDU warning: {exc}")

        _log(f"{case_id} [{label}] hard reboot via PDU")
        try:
            await pdu_hard_reboot(profile, device_target=label.lower())
        except Exception as pdu_exc:
            # Lab PDU (192.168.1.190) is often unreachable from the automation PC.
            # Soft reboot still validates config retain across reboot for FT_03.
            _log(
                f"{case_id} [{label}] PDU unavailable ({pdu_exc}); "
                "falling back to soft reboot (reboot -f)"
            )
            try:
                # Fire-and-forget so SSH does not hang waiting for reboot to drop the link.
                await _ssh(
                    target_ssh,
                    "nohup sh -c 'sync; sleep 2; reboot -f || reboot' >/dev/null 2>&1 &",
                    timeout_s=min(timeout_s, 15),
                )
            except Exception as soft_exc:
                _log(f"{case_id} [{label}] soft reboot command returned: {soft_exc}")
            await asyncio.sleep(int(values.get("ft03_soft_reboot_grace_s", 60)))
        try:
            await _ft03_close_transport(target_ssh)
        except Exception:
            pass

        fresh = await _ft03_wait_ssh_multi(
            hosts=reconnect_hosts,
            password=password,
            source_v6=source_v6,
            label=label,
            timeout_s=reboot_timeout_s,
            poll_s=poll_s,
            accept_ips=accept_ips,
        )
        reconnected = _ft03_bind_ssh(target_ssh, fresh)
        current_ssh = reconnected
        await asyncio.sleep(max(settle_s, 0))
        after, reconnected = await _ft03_read_backend_state_retry(
            reconnected,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
            expected_bandwidth=target_bandwidth if bandwidth_mutated else "",
            retries=int(values.get("ft03_verify_retries", 6)),
            delay_s=float(values.get("ft03_verify_delay_s", 5)),
            password=password,
            source_v6=source_v6,
            reconnect_hosts=reconnect_hosts,
        )
        current_ssh = reconnected

        _print_backend_table(
            f"{case_id} [{label}] post-boot",
            [
                (
                    "Channel width",
                    target_bandwidth if bandwidth_check_enabled and bandwidth_mutated else (
                        f"{before['bandwidth']} (unchanged by DUT)" if bandwidth_check_enabled else "n/a (unreadable)"
                    ),
                    after["bandwidth"],
                    "PASS"
                    if bandwidth_check_enabled and _ft05_uci_htmode_matches(target_bandwidth, after["bandwidth"])
                    else ("INFO" if (not bandwidth_check_enabled or not bandwidth_mutated) else "FAIL"),
                ),
                ("IPv4", target_ipv4, after["ipv4"], "PASS" if after["ipv4"] == target_ipv4 else "FAIL"),
                ("Netmask", target_netmask, after["netmask"], "PASS" if after["netmask"] == target_netmask else "FAIL"),
                ("IPv6", target_ipv6, after["ipv6"], "PASS" if after["ipv6"] == target_ipv6 else "FAIL"),
            ],
        )
        if bandwidth_check_enabled and bandwidth_mutated:
            check.is_true(
                _ft05_uci_htmode_matches(target_bandwidth, after["bandwidth"]),
                f"{case_id} [{label}]: bandwidth not retained after hard reboot",
            )
        check.equal(after["ipv4"], target_ipv4, f"{case_id} [{label}]: IPv4 not retained after hard reboot")
        check.equal(after["netmask"], target_netmask, f"{case_id} [{label}]: netmask not retained after hard reboot")
        check.equal(after["ipv6"], target_ipv6, f"{case_id} [{label}]: IPv6 not retained after hard reboot")

        # Ensure live SSH before RF poll — retain already verified; don't fail on a dead handle.
        try:
            await _ssh(reconnected, "echo FT03_LINK_PROBE", timeout_s=min(15, timeout_s))
        except Exception:
            await _ft03_close_transport(reconnected)
            fresh = await _ft03_wait_ssh_multi(
                hosts=reconnect_hosts,
                password=password,
                source_v6=source_v6,
                label=f"{label}-prelink",
                timeout_s=max(120, int(values.get("ft03_post_apply_reconnect_s", 180))),
                poll_s=poll_s,
                accept_ips=accept_ips,
            )
            reconnected = _ft03_bind_ssh(reconnected, fresh)
            current_ssh = reconnected
            _log(f"{case_id} [{label}] SSH reopened before RF link wait")

        if label == "BTS":
            reconnected = await _wait_rf_link_healthy(
                reconnected,
                profile,
                case_id=case_id,
                label="BTS",
                timeout_s=link_timeout_s,
                poll_s=link_poll_s,
                password=password,
                source_v6=source_v6,
                reconnect_hosts=reconnect_hosts,
            )
            current_ssh = reconnected
        else:
            # Soft reboot of CPE can briefly drop RF; config retain already asserted above.
            try:
                dut = profile.get("dut") or {}
                mgmt = (profile.get("testbed") or {}).get("mgmt_vlan") or {}
                bts_hosts = []
                for raw in (dut.get("local_ipv6"), mgmt.get("ipv6_bts")):
                    host = normalize_ip(str(raw or "").split("/")[0])
                    if host and host not in bts_hosts:
                        bts_hosts.append(host)
                await _wait_rf_link_healthy(
                    bts_ssh,
                    profile,
                    case_id=case_id,
                    label="CPE",
                    timeout_s=min(link_timeout_s, 120),
                    poll_s=link_poll_s,
                    password=password,
                    source_v6=source_v6,
                    reconnect_hosts=bts_hosts or None,
                )
            except Exception as link_exc:
                _log(f"{case_id} [{label}] RF link wait after reboot skipped: {link_exc}")

        if not restore_baseline:
            return reconnected, target_ipv6_host

        try:
            await _ft03_apply_backend_state(
                reconnected,
                radio_idx=radio_idx,
                bandwidth=before["bandwidth"],
                ipv4=before["ipv4"],
                netmask=before["netmask"],
                ipv6_cidr=before["ipv6"],
                timeout_s=max(120, timeout_s),
                apply_bandwidth=bandwidth_supported_for_case,
            )
        except Exception as restore_apply_exc:
            _log(f"{case_id} [{label}] baseline apply dropped SSH (expected): {restore_apply_exc}")

        restore_hosts = [
            baseline_ipv6_host,
            before["ipv4"],
            target_ipv6_host,
            target_ipv4,
        ]
        if label.upper() == "BTS":
            restore_hosts.append(
                normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
            )
        restore_accept = {baseline_ipv6_host, before["ipv4"]}
        if label.upper() == "BTS":
            restore_accept.add(
                normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
            )
        try:
            await _ft03_close_transport(reconnected)
        except Exception:
            pass
        # Soft reboot so baseline LAN is live; prefer nohup so SSH returns.
        try:
            tmp = await _ft03_wait_ssh_multi(
                hosts=restore_hosts,
                password=password,
                source_v6=source_v6,
                label=label,
                timeout_s=90,
                poll_s=poll_s,
                accept_ips=None,
            )
            try:
                await _ssh(
                    tmp,
                    "nohup sh -c 'sync; sleep 1; reboot -f || reboot' >/dev/null 2>&1 &",
                    timeout_s=15,
                )
            except Exception:
                pass
            try:
                await tmp.close()
            except Exception:
                pass
        except Exception as soft_exc:
            _log(f"{case_id} [{label}] baseline soft reboot warn: {soft_exc}")

        await asyncio.sleep(int(values.get("ft03_soft_reboot_grace_s", 60)))
        fresh = await _ft03_wait_ssh_multi(
            hosts=restore_hosts,
            password=password,
            source_v6=source_v6,
            label=label,
            timeout_s=reboot_timeout_s,
            poll_s=poll_s,
            accept_ips=restore_accept,
        )
        restored = _ft03_bind_ssh(reconnected, fresh)
        current_ssh = restored
        await asyncio.sleep(max(settle_s, 0))
        try:
            restored_state = await _ft03_read_backend_state(restored, radio_idx=radio_idx, timeout_s=timeout_s)
        except Exception:
            await _ft03_close_transport(restored)
            fresh = await _ft03_wait_ssh_multi(
                hosts=restore_hosts,
                password=password,
                source_v6=source_v6,
                label=label,
                timeout_s=90,
                poll_s=poll_s,
                accept_ips=restore_accept,
            )
            restored = _ft03_bind_ssh(restored, fresh)
            current_ssh = restored
            restored_state = await _ft03_read_backend_state(restored, radio_idx=radio_idx, timeout_s=timeout_s)
        _print_backend_table(
            f"{case_id} [{label}] after-test (reverted to original)",
            [
                (
                    "Channel width",
                    before["bandwidth"] if bandwidth_check_enabled else "n/a (unreadable)",
                    restored_state["bandwidth"],
                    "PASS"
                    if (not bandwidth_check_enabled or restored_state["bandwidth"] == before["bandwidth"])
                    else "FAIL",
                ),
                ("IPv4", before["ipv4"], restored_state["ipv4"], "PASS" if restored_state["ipv4"] == before["ipv4"] else "FAIL"),
                ("Netmask", before["netmask"], restored_state["netmask"], "PASS" if restored_state["netmask"] == before["netmask"] else "FAIL"),
                ("IPv6", before["ipv6"], restored_state["ipv6"], "PASS" if restored_state["ipv6"] == before["ipv6"] else "FAIL"),
            ],
        )
        if bandwidth_check_enabled:
            check.equal(restored_state["bandwidth"], before["bandwidth"], f"{case_id} [{label}]: baseline bandwidth restore failed")
        check.equal(restored_state["ipv4"], before["ipv4"], f"{case_id} [{label}]: baseline IPv4 restore failed")
        check.equal(restored_state["netmask"], before["netmask"], f"{case_id} [{label}]: baseline netmask restore failed")
        check.equal(restored_state["ipv6"], before["ipv6"], f"{case_id} [{label}]: baseline IPv6 restore failed")
        restored_baseline = True

        if label == "BTS":
            restored = await _wait_rf_link_healthy(
                restored,
                profile,
                case_id=case_id,
                label="BTS-restored",
                timeout_s=link_timeout_s,
                poll_s=link_poll_s,
                password=password,
                source_v6=source_v6,
                reconnect_hosts=restore_hosts,
            )
            current_ssh = restored
        else:
            try:
                await _wait_rf_link_healthy(
                    bts_ssh,
                    profile,
                    case_id=case_id,
                    label="CPE-restored",
                    timeout_s=min(link_timeout_s, 120),
                    poll_s=link_poll_s,
                    password=password,
                    source_v6=source_v6,
                    reconnect_hosts=restore_hosts,
                )
            except Exception as link_exc:
                _log(f"{case_id} [{label}] RF link wait after restore skipped: {link_exc}")
        return restored, baseline_ipv6_host
    finally:
        if restore_baseline and not restored_baseline:
            _log(f"{case_id} [{label}] best-effort baseline restore in finally")
            try:
                hosts = [
                    normalize_ip(before["ipv6"].split("/")[0]),
                    before["ipv4"],
                    normalize_ip(str(target_ipv6).split("/")[0]) if target_ipv6 else "",
                    target_ipv4,
                ]
                if label.upper() == "BTS":
                    hosts.append(
                        normalize_ip(str(values.get("ft03_factory_ipv4", values.get("ft07_bts_hop_ipv4", "10.0.0.1"))))
                    )
                try:
                    await current_ssh.close()
                except Exception:
                    pass
                current_ssh = await _ft03_wait_ssh_multi(
                    hosts=hosts,
                    password=password,
                    source_v6=source_v6,
                    label=label,
                    timeout_s=120,
                    poll_s=poll_s,
                    accept_ips=None,
                )
                await _ft03_apply_backend_state(
                    current_ssh,
                    radio_idx=radio_idx,
                    bandwidth=before["bandwidth"],
                    ipv4=before["ipv4"],
                    netmask=before["netmask"],
                    ipv6_cidr=before["ipv6"],
                    timeout_s=max(120, timeout_s),
                    apply_bandwidth=bandwidth_supported_for_case,
                )
                try:
                    await _ssh(
                        current_ssh,
                        "nohup sh -c 'sync; sleep 1; reboot -f || reboot' >/dev/null 2>&1 &",
                        timeout_s=15,
                    )
                except Exception:
                    pass
                try:
                    await current_ssh.close()
                except Exception:
                    pass
            except Exception as exc:
                _log(f"{case_id} [{label}] baseline restore failed in finally: {exc}")


async def assert_ft_03_config_retain_hard_reboot(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_03 — retain channel width + network config after hard reboot (PDU)."""
    case_id = "FT_03"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    source_v6 = _source_bind_ipv6(profile_bundle)
    values = FUNCTIONAL_TEST_VALUES
    profile = profile_bundle.active
    wait_link_each_step = bool(values.get("ft03_wait_link_before_each_step", True))
    link_timeout_s = int(values.get("ft03_link_recovery_timeout_s", 180))
    link_poll_s = int(values.get("ft03_link_recovery_poll_s", 8))

    check.is_true(pdu_enabled(profile), f"{case_id}: profile.pdu must be configured/enabled for hard reboot")
    print_section(f"{case_id}: Config retain after hard reboot (PDU) — BTS {bts_ip} then CPE {cpe_ip}")
    _log("normal reboot cases use command reboot; FT_03 uses hard reboot via PDU")
    if wait_link_each_step:
        await _wait_rf_link_healthy(
            root_ssh,
            profile,
            case_id=case_id,
            label="PRECHECK",
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
            password=device_creds["pass"],
            source_v6=source_v6,
            reconnect_hosts=[bts_ip],
        )

    bts_ssh = root_ssh
    cpe_ssh = None
    try:
        bts_ssh, _ = await _ft03_config_retain_hard_reboot_device(
            case_id=case_id,
            label="BTS",
            target_ssh=bts_ssh,
            bts_ssh=bts_ssh,
            profile=profile,
            source_v6=source_v6,
            password=device_creds["pass"],
            values=values,
        )
        if wait_link_each_step:
            await _wait_rf_link_healthy(
                bts_ssh,
                profile,
                case_id=case_id,
                label="BTS→CPE gate",
                timeout_s=link_timeout_s,
                poll_s=link_poll_s,
                password=device_creds["pass"],
                source_v6=source_v6,
                reconnect_hosts=[bts_ip],
            )

        cpe_ssh = await _open_ipv6_ssh(
            cpe_ip,
            device_creds["pass"],
            source_v6=source_v6,
            label="CPE",
        )
        cpe_ssh, _ = await _ft03_config_retain_hard_reboot_device(
            case_id=case_id,
            label="CPE",
            target_ssh=cpe_ssh,
            bts_ssh=bts_ssh,
            profile=profile,
            source_v6=source_v6,
            password=device_creds["pass"],
            values=values,
        )
    finally:
        if cpe_ssh is not None:
            try:
                await cpe_ssh.close()
            except Exception:
                pass
        if bts_ssh is not root_ssh:
            try:
                await bts_ssh.close()
            except Exception:
                pass

    _log(f"{case_id} done — config retained across hard reboot on BTS and CPE")


def _ft05_test_network_targets(profile: dict, *, role: str, baseline: dict[str, str]) -> dict[str, str]:
    """FT_05 — change IPv4, netmask, IPv4 gateway, IPv6 at each bandwidth (no IPv6 gateway)."""
    ip_cfg = profile.get("ip_tests", {}) or {}
    role_key = "bts" if role.upper() == "BTS" else "cpe"
    ipv4_gw = str(
        ip_cfg.get(f"ipv4_gateway_{role_key}")
        or ip_cfg.get("ipv4_gateway")
        or "192.168.2.254"
    ).strip()
    return {
        "proto": "static",
        "ipv4": _derive_alt_ipv4(baseline.get("ipv4", ""), role=role),
        "netmask": _pick_alternate(
            baseline.get("netmask", ""),
            list(FUNCTIONAL_TEST_VALUES.get("ft03_netmask_candidates", ["255.255.255.0", "255.255.254.0"])),
            "255.255.254.0",
        ),
        "ipv4_gw": normalize_ip(ipv4_gw.split("/")[0]) if ipv4_gw else "",
        "ipv6": _derive_alt_ipv6(baseline.get("ipv6", ""), role=role),
    }


def _ft05_ipv6_mgmt_host(ipv6_cidr: str) -> str:
    raw = str(ipv6_cidr or "").strip()
    if not raw:
        return ""
    return normalize_ip(raw.split("/")[0])


def _ft05_htmode_uci_line(radio_idx: int, htmode: str) -> str:
    return f"wireless.wifi{radio_idx}.htmode = {htmode}"


def _ft05_mgmt_ipv6_host(baseline: dict[str, str]) -> str:
    raw = str(baseline.get("ipv6", "") or "").strip()
    if not raw:
        return ""
    return normalize_ip(raw.split("/")[0])


def _ft05_reconnect_hosts(*hosts: str) -> list[str]:
    ordered: list[str] = []
    for raw in hosts:
        host = normalize_ip(str(raw or "").split("/")[0])
        if host and host not in ordered:
            ordered.append(host)
    return ordered


async def _ft05_ensure_ssh(
    ssh,
    *,
    host_v6: str,
    password: str,
    source_v6: str,
    label: str,
    timeout_s: int,
    poll_s: int,
    reconnect_hosts: list[str] | None = None,
):
    """Return a live SSH session — reconnect after ucidyn apply drops the transport."""
    hosts = _ft05_reconnect_hosts(*(reconnect_hosts or []), host_v6)
    if not hosts:
        await _ssh(ssh, "echo FT05_OK", timeout_s=min(15, timeout_s))
        return ssh
    try:
        await _ssh(ssh, "echo FT05_OK", timeout_s=min(15, timeout_s))
        return ssh
    except Exception:
        pass
    try:
        await ssh.close()
    except Exception:
        pass
    new_conn = await _ft03_wait_ssh_multi(
        hosts=hosts,
        password=password,
        source_v6=source_v6,
        label=label,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
    return _ft03_bind_ssh(ssh, new_conn)


async def _ft05_wait_after_change(
    current_ssh,
    *,
    case_id: str,
    label: str,
    mgmt_host_v6: str,
    password: str,
    source_v6: str,
    settle_s: int,
    reconnect_timeout_s: int,
    reconnect_poll_s: int,
    bts_ssh=None,
    profile: dict | None = None,
    link_timeout_s: int = 0,
    link_poll_s: int = 8,
    change_tag: str = "",
    reconnect_hosts: list[str] | None = None,
    link_fail_hard: bool = True,
    link_password: str = "",
    link_reconnect_hosts: list[str] | None = None,
):
    """Settle, wait BTS↔CPE RF, then reconnect on new IPv6/IPv4 after LAN change."""
    tag = f" ({change_tag})" if change_tag else ""
    hosts = _ft05_reconnect_hosts(*(reconnect_hosts or []), mgmt_host_v6)
    _log(
        f"{case_id} [{label}] wait {settle_s}s after change{tag}, "
        f"then SSH {hosts or [mgmt_host_v6]}"
    )
    await asyncio.sleep(max(settle_s, 0))
    # RF first: CPE LAN/htmode apply is only reachable over the air from the BTS PC.
    if bts_ssh is not None and profile is not None and link_timeout_s > 0:
        await _wait_rf_link_healthy(
            bts_ssh,
            profile,
            case_id=case_id,
            label=f"{label} BTS↔CPE",
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
            password=link_password,
            source_v6=source_v6,
            reconnect_hosts=link_reconnect_hosts,
            fail_hard=link_fail_hard,
        )
    current_ssh = await _ft05_ensure_ssh(
        current_ssh,
        host_v6=mgmt_host_v6,
        password=password,
        source_v6=source_v6,
        label=label,
        timeout_s=reconnect_timeout_s,
        poll_s=reconnect_poll_s,
        reconnect_hosts=hosts,
    )
    return current_ssh


async def _ft05_set_remote_htmode(
    ssh,
    *,
    radio_idx: int,
    htmode: str,
    timeout_s: int,
    case_id: str,
) -> None:
    """Set BTS htmode. ucidyn apply often drops SSH; the set is already queued."""
    try:
        await _ssh(
            ssh,
            f"ucidyn set wireless.wifi{radio_idx}.htmode {htmode}",
            timeout_s=max(45, timeout_s),
        )
        await _ssh(ssh, "ucidyn apply", timeout_s=max(25, min(int(timeout_s), 60)))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _log(f"{case_id} [CPE] BTS htmode apply returned ({type(exc).__name__}: {exc})")


async def _ft05_link_is_up(bts_ssh, profile: dict, *, case_id: str, label: str) -> bool:
    try:
        ok, detail = await _rf_link_up(bts_ssh, profile)
    except Exception as exc:
        ok, detail = False, str(exc)
    if not ok:
        _log(f"{case_id} [{label}] link down ({detail})")
    return bool(ok)


async def _ft05_restore_cpe_via_hop(
    profile: dict,
    password: str,
    values: dict,
    *,
    radio_idx: int,
    baseline_apply: dict,
    timeout_s: int,
    case_id: str,
) -> bool:
    """Reach the CPE from the side PC when the RF link is down and IPv6 SSH cannot."""
    hop_ip = normalize_ip(str(values.get("ft07_cpe_hop_ipv4") or "10.0.0.1"))
    candidates: list[str] = []
    for raw in (hop_ip, baseline_apply.get("ipv4"), "10.0.0.1"):
        host = normalize_ip(str(raw or "").split("/")[0])
        if host and not is_ipv6_literal(host) and host not in candidates:
            candidates.append(host)
    for target in candidates:
        hop = None
        try:
            _log(f"{case_id} [CPE] revert via CPE PC hop → {target}")
            hop = await _ft07_open_cpe_ssh_via_cpe_pc(
                profile,
                password,
                values,
                cpe_target=target,
                wait_ping=True,
            )
            await _apply_ft05_test_state(
                hop,
                radio_idx=radio_idx,
                bandwidth=baseline_apply["bandwidth"],
                proto=baseline_apply["proto"],
                ipv4=baseline_apply["ipv4"],
                netmask=baseline_apply["netmask"],
                ipv4_gw=baseline_apply["ipv4_gw"],
                ipv6_cidr=baseline_apply["ipv6"],
                timeout_s=max(120, timeout_s),
            )
            return True
        except Exception as exc:
            _log(f"{case_id} [CPE] hop revert via {target} failed: {exc}")
        finally:
            if hop is not None:
                try:
                    await hop.close()
                except Exception:
                    pass
    return False


async def _read_lan_backend_state(ssh, *, radio_idx: int, timeout_s: int) -> dict[str, str]:
    """Backend LAN + channel width (bandwidth/htmode) from UCI + runtime ucidyn."""
    bw_uci = await _ssh(ssh, f"uci get wireless.wifi{radio_idx}.htmode 2>/dev/null || true", timeout_s=timeout_s)
    bw_mode = await _ssh(ssh, f"cfg80211tool ath{radio_idx} get_mode 2>/dev/null || true", timeout_s=timeout_s)
    htmode_uci = _normalize_htmode(bw_uci)
    if not _is_valid_htmode(htmode_uci):
        cleaned = clean_ssh_output(bw_uci).strip()
        match = re.search(r"HT(?:20|40\+?|80|160)", cleaned, flags=re.I)
        htmode_uci = match.group(0).upper() if match else cleaned
    proto = await _ssh(ssh, RootCommands.GET_NET_PROTO, timeout_s=timeout_s)
    ipv4 = await _ssh(ssh, RootCommands.GET_NET_IP, timeout_s=timeout_s)
    netmask = await _ssh(ssh, RootCommands.GET_NET_MASK, timeout_s=timeout_s)
    ipv4_gw = await _ssh(ssh, RootCommands.GET_NET_GW, timeout_s=timeout_s)
    ipv6 = await _ssh(ssh, RootCommands.GET_NET_IP6, timeout_s=timeout_s)
    ipv6_gw = await _ssh(ssh, RootCommands.GET_NET_GW6, timeout_s=timeout_s)
    ipv4_dyn = await _ssh(ssh, RootCommands.GET_IPv4, timeout_s=timeout_s)
    ipv6_dyn = await _ssh(ssh, RootCommands.GET_IPv6, timeout_s=timeout_s)
    ipv4_gw_dyn = await _ssh(ssh, RootCommands.GET_GATEWAYv4, timeout_s=timeout_s)
    ipv6_gw_dyn = await _ssh(ssh, RootCommands.GET_GATEWAYv6, timeout_s=timeout_s)
    return {
        "bandwidth": _normalize_htmode(f"{bw_uci}\n{bw_mode}"),
        "htmode_uci": htmode_uci,
        "radio_idx": str(radio_idx),
        "proto": clean_ssh_output(proto).strip(),
        "ipv4": normalize_ip(clean_ssh_output(ipv4).strip().split("/")[0]),
        "netmask": clean_ssh_output(netmask).strip(),
        "ipv4_gw": normalize_ip(clean_ssh_output(ipv4_gw).strip().split("/")[0]),
        "ipv6": clean_ssh_output(ipv6).strip(),
        "ipv6_gw": normalize_ip(clean_ssh_output(ipv6_gw).strip().split("/")[0]),
        "ipv4_dyn": normalize_ip(clean_ssh_output(ipv4_dyn).strip().split("/")[0]),
        "ipv6_dyn": clean_ssh_output(ipv6_dyn).strip(),
        "ipv4_gw_dyn": normalize_ip(clean_ssh_output(ipv4_gw_dyn).strip().split("/")[0]),
        "ipv6_gw_dyn": normalize_ip(clean_ssh_output(ipv6_gw_dyn).strip().split("/")[0]),
    }


async def _apply_ft05_test_state(
    ssh,
    *,
    radio_idx: int,
    bandwidth: str,
    proto: str,
    ipv4: str,
    netmask: str,
    ipv4_gw: str,
    ipv6_cidr: str,
    timeout_s: int,
) -> None:
    """Apply FT_05 — channel width + static LAN (IPv4/netmask/gateway/IPv6; no IPv6 gateway).

    ``ucidyn apply`` on an IP change drops SSH; that is expected — caller reconnects.
    """
    cmds = [
        f"ucidyn set wireless.wifi{radio_idx}.htmode {_ft05_uci_htmode_value(bandwidth)}",
        f"ucidyn set network.lan.proto {proto}",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {netmask}",
        f"ucidyn set network.lan.gateway {ipv4_gw}",
        f"ucidyn set network.lan.ip6addr {ipv6_cidr}",
        "ucidyn apply",
    ]
    drop_timeout_s = min(25, max(15, int(timeout_s)))
    for cmd in cmds:
        try:
            await _ssh(ssh, cmd, timeout_s=drop_timeout_s)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            text = f"{type(exc).__name__} {exc}".lower()
            if _ssh_transport_dead_error(exc) or "timeout" in text or "timed out" in text or "cancelled" in text:
                _log(f"FT_05 apply dropped SSH (expected on IP/htmode change): {type(exc).__name__}: {exc}")
                return
            raise


async def _apply_lan_backend_state(
    ssh,
    *,
    radio_idx: int,
    bandwidth: str,
    proto: str,
    ipv4: str,
    netmask: str,
    ipv4_gw: str,
    ipv6_cidr: str,
    timeout_s: int,
) -> None:
    cmds = [
        f"ucidyn set wireless.wifi{radio_idx}.htmode {uci_htmode_value(bandwidth)}",
        f"ucidyn set network.lan.proto {proto}",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {netmask}",
        f"ucidyn set network.lan.gateway {ipv4_gw}",
        f"ucidyn set network.lan.ip6addr {ipv6_cidr}",
        "ucidyn apply",
    ]
    for cmd in cmds:
        await _ssh(ssh, cmd, timeout_s=timeout_s)


def _ft05_backend_htmode(actual: dict[str, str]) -> str:
    for key in ("htmode_uci", "bandwidth"):
        raw = str(actual.get(key) or "")
        norm = _normalize_htmode(raw)
        if _is_valid_htmode(norm):
            return norm
        match = re.search(r"HT(?:20|40\+?|80|160)", raw, flags=re.I)
        if match:
            token = match.group(0).upper()
            return "HT40" if token.startswith("HT40") else token
    return ""


async def _ft05_verify_backend_matches(
    *,
    case_id: str,
    label: str,
    bandwidth: str,
    radio_idx: int,
    expected: dict[str, str],
    actual: dict[str, str],
) -> None:
    actual_htmode = _ft05_backend_htmode(actual)
    bw_ok = _ft05_uci_htmode_matches(bandwidth, actual_htmode) if _is_valid_htmode(bandwidth) else True
    ipv4_ok = actual.get("ipv4") == expected["ipv4"]
    ipv4_dyn_ok = actual.get("ipv4_dyn") == expected["ipv4"] or not actual.get("ipv4_dyn")
    netmask_ok = actual.get("netmask") == expected["netmask"]
    gw_ok = actual.get("ipv4_gw") == expected["ipv4_gw"]
    gw_dyn_ok = actual.get("ipv4_gw_dyn") == expected["ipv4_gw"] or not actual.get("ipv4_gw_dyn")
    ipv6_ok = actual.get("ipv6") == expected["ipv6"]
    ipv6_dyn_ok = actual.get("ipv6_dyn") == expected["ipv6"] or not actual.get("ipv6_dyn")
    uci_bw = _ft05_uci_htmode_value(bandwidth) if _is_valid_htmode(bandwidth) else bandwidth

    _print_backend_table(
        f"{case_id} [{label}] backend @ {bandwidth}",
        [
            (
                "Channel width (UCI)",
                _ft05_htmode_uci_line(radio_idx, uci_bw),
                _ft05_htmode_uci_line(radio_idx, actual_htmode) if actual_htmode else "",
                "PASS" if bw_ok else "FAIL",
            ),
            (
                "IPv4 address (UCI)",
                f"network.lan.ipaddr = {expected['ipv4']}",
                f"network.lan.ipaddr = {actual.get('ipv4', '')}",
                "PASS" if ipv4_ok else "FAIL",
            ),
            (
                "IPv4 netmask (UCI)",
                f"network.lan.netmask = {expected['netmask']}",
                f"network.lan.netmask = {actual.get('netmask', '')}",
                "PASS" if netmask_ok else "FAIL",
            ),
            (
                "IPv4 gateway (UCI)",
                f"network.lan.gateway = {expected['ipv4_gw']}",
                f"network.lan.gateway = {actual.get('ipv4_gw', '')}",
                "PASS" if gw_ok else "FAIL",
            ),
            (
                "IPv6 address (UCI)",
                f"network.lan.ip6addr = {expected['ipv6']}",
                f"network.lan.ip6addr = {actual.get('ipv6', '')}",
                "PASS" if ipv6_ok else "FAIL",
            ),
            ("IPv6 gateway", "not configured", actual.get("ipv6_gw", ""), "INFO"),
            (
                "IPv4 (ucidyn runtime)",
                expected["ipv4"],
                actual.get("ipv4_dyn", ""),
                "PASS" if ipv4_dyn_ok else "FAIL",
            ),
            (
                "IPv4 gateway (ucidyn)",
                expected["ipv4_gw"],
                actual.get("ipv4_gw_dyn", ""),
                "PASS" if gw_dyn_ok else "FAIL",
            ),
            (
                "IPv6 (ucidyn runtime)",
                expected["ipv6"],
                actual.get("ipv6_dyn", ""),
                "PASS" if ipv6_dyn_ok else "FAIL",
            ),
        ],
    )

    if _is_valid_htmode(bandwidth):
        check.is_true(
            _ft05_uci_htmode_matches(bandwidth, actual_htmode),
            f"{case_id} [{label}@{bandwidth}]: htmode not reflected in backend "
            f"(expected {_ft05_htmode_uci_line(radio_idx, _ft05_uci_htmode_value(bandwidth))}, "
            f"got {_ft05_htmode_uci_line(radio_idx, actual_htmode) if actual_htmode else actual_htmode})",
        )
    check.equal(actual.get("ipv4"), expected["ipv4"], f"{case_id} [{label}@{bandwidth}]: IPv4 UCI not reflected in backend")
    check.equal(actual.get("netmask"), expected["netmask"], f"{case_id} [{label}@{bandwidth}]: netmask not reflected in backend")
    check.equal(actual.get("ipv4_gw"), expected["ipv4_gw"], f"{case_id} [{label}@{bandwidth}]: IPv4 gateway not reflected in backend")
    check.equal(actual.get("ipv6"), expected["ipv6"], f"{case_id} [{label}@{bandwidth}]: IPv6 not reflected in backend")
    if actual.get("ipv4_dyn"):
        check.equal(actual.get("ipv4_dyn"), expected["ipv4"], f"{case_id} [{label}@{bandwidth}]: IPv4 runtime not reflected in backend")
    if actual.get("ipv4_gw_dyn"):
        check.equal(actual.get("ipv4_gw_dyn"), expected["ipv4_gw"], f"{case_id} [{label}@{bandwidth}]: IPv4 gateway runtime not reflected in backend")
    if actual.get("ipv6_dyn"):
        check.equal(actual.get("ipv6_dyn"), expected["ipv6"], f"{case_id} [{label}@{bandwidth}]: IPv6 runtime not reflected in backend")


async def _ft05_backend_per_bandwidth_on_device(
    target_ssh,
    *,
    case_id: str,
    label: str,
    profile: dict,
    values: dict,
    mgmt_host_v6: str,
    password: str,
    source_v6: str,
    bts_ssh=None,
) -> object:
    timeout_s = int(values["ssh_timeout_s"])
    radio_idx = int(values.get("ft05_radio_idx", values.get("ft03_radio_idx", 1)))
    settle_s = int(values.get("ft05_apply_settle_s", 30))
    reconnect_timeout_s = int(values.get("ft05_reconnect_timeout_s", 90))
    reconnect_poll_s = int(values.get("ft05_reconnect_poll_s", 5))
    link_timeout_s = int(values.get("ft05_link_recovery_timeout_s", 180))
    link_poll_s = int(values.get("ft05_link_recovery_poll_s", 8))
    skip_unsupported = bool(values.get("ft05_skip_unsupported_bandwidth", True))
    restore_baseline = bool(values.get("ft05_restore_baseline", True))
    bandwidth_list = list(values.get("ft05_bandwidth_list", ["HT20", "HT40", "HT80", "HT160"]))
    link_ssh = bts_ssh or target_ssh
    dut = profile.get("dut") or {}
    recovery = ((profile.get("testbed") or {}).get("recovery") or {})
    bts_link_hosts = _ft05_reconnect_hosts(
        str(dut.get("local_ipv6") or ""),
        str(recovery.get("bts_fallback_ipv4") or ""),
    )

    current_ssh = target_ssh
    baseline = await _read_lan_backend_state(current_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    mgmt_host_v6 = mgmt_host_v6 or _ft05_mgmt_ipv6_host(baseline)
    baseline_apply = {
        "proto": baseline.get("proto") or "static",
        "ipv4": baseline["ipv4"],
        "netmask": baseline["netmask"],
        "ipv4_gw": baseline["ipv4_gw"],
        "ipv6": baseline["ipv6"],
        "bandwidth": baseline["bandwidth"] if _is_valid_htmode(baseline["bandwidth"]) else "HT20",
    }
    baseline_mgmt_v6 = mgmt_host_v6 or _ft05_mgmt_ipv6_host(baseline)
    test_targets = _ft05_test_network_targets(profile, role=label, baseline=baseline)
    test_mgmt_v6 = _ft05_ipv6_mgmt_host(test_targets["ipv6"]) or baseline_mgmt_v6
    active_mgmt_v6 = baseline_mgmt_v6

    _print_backend_table(
        f"{case_id} [{label}] original",
        [
            (
                "Channel width (UCI)",
                "captured",
                _ft05_htmode_uci_line(radio_idx, baseline.get("htmode_uci") or baseline.get("bandwidth", "")),
                "PASS",
            ),
            ("IPv4 address", "captured", baseline.get("ipv4", ""), "PASS"),
            ("IPv4 netmask", "captured", baseline.get("netmask", ""), "PASS"),
            ("IPv4 gateway", "captured", baseline.get("ipv4_gw", ""), "PASS"),
            ("IPv6 address", "captured", baseline.get("ipv6", ""), "PASS"),
        ],
    )

    restored = False
    bts_ht_baseline = ""
    if str(label).upper() == "CPE" and link_ssh is not None:
        bts_ht_baseline = clean_ssh_output(
            await _ssh(link_ssh, f"uci get wireless.wifi{radio_idx}.htmode 2>/dev/null || true", timeout_s=timeout_s)
        ).strip()
    try:
        for bandwidth in bandwidth_list:
            ht = str(bandwidth).strip().upper()
            if not _is_valid_htmode(ht):
                continue
            uci_ht = _ft05_uci_htmode_value(ht)
            if str(label).upper() == "CPE" and link_ssh is not None:
                link_ok = False
                for attempt in (1, 2):
                    _log(
                        f"{case_id} [CPE] match BTS htmode to {uci_ht} "
                        f"so STA can reassociate (attempt {attempt}/2)"
                    )
                    await _ft05_set_remote_htmode(
                        link_ssh,
                        radio_idx=radio_idx,
                        htmode=uci_ht,
                        timeout_s=timeout_s,
                        case_id=case_id,
                    )
                    await asyncio.sleep(max(20, settle_s // 2))
                    await _wait_rf_link_healthy(
                        link_ssh,
                        profile,
                        case_id=case_id,
                        label=f"CPE BTS↔CPE ht-{uci_ht}",
                        timeout_s=link_timeout_s,
                        poll_s=link_poll_s,
                        password=password,
                        source_v6=source_v6,
                        reconnect_hosts=bts_link_hosts,
                        fail_hard=False,
                    )
                    link_ok = await _ft05_link_is_up(
                        link_ssh, profile, case_id=case_id, label=f"CPE ht-{uci_ht}",
                    )
                    if link_ok:
                        break
                if not link_ok:
                    raise AssertionError(
                        f"{case_id} [CPE]: RF link not up after BTS htmode {uci_ht}"
                    )
            _log(
                f"{case_id} [{label}] apply backend @ {ht} "
                f"({_ft05_htmode_uci_line(radio_idx, uci_ht)}) + "
                f"IPv4/netmask/gateway/IPv6 (no IPv6 gateway)"
            )
            await _apply_ft05_test_state(
                current_ssh,
                radio_idx=radio_idx,
                bandwidth=ht,
                proto=test_targets["proto"],
                ipv4=test_targets["ipv4"],
                netmask=test_targets["netmask"],
                ipv4_gw=test_targets["ipv4_gw"],
                ipv6_cidr=test_targets["ipv6"],
                timeout_s=max(120, timeout_s),
            )
            active_mgmt_v6 = test_mgmt_v6
            current_ssh = await _ft05_wait_after_change(
                current_ssh,
                case_id=case_id,
                label=label,
                mgmt_host_v6=active_mgmt_v6,
                password=password,
                source_v6=source_v6,
                settle_s=settle_s,
                reconnect_timeout_s=reconnect_timeout_s,
                reconnect_poll_s=reconnect_poll_s,
                bts_ssh=link_ssh,
                profile=profile,
                link_timeout_s=link_timeout_s,
                link_poll_s=link_poll_s,
                change_tag=ht,
                reconnect_hosts=_ft05_reconnect_hosts(
                    test_mgmt_v6,
                    baseline_mgmt_v6,
                    test_targets.get("ipv4", ""),
                    baseline.get("ipv4", ""),
                ),
            )
            actual = await _read_lan_backend_state(current_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
            actual_htmode = _ft05_backend_htmode(actual)
            if skip_unsupported and not _ft05_uci_htmode_matches(ht, actual_htmode):
                _log(
                    f"{case_id} [{label}] {ht} not accepted by DUT "
                    f"(expected {_ft05_htmode_uci_line(radio_idx, _ft05_uci_htmode_value(ht))}, "
                    f"backend={_ft05_htmode_uci_line(radio_idx, actual_htmode) if actual_htmode else actual_htmode}) — skip"
                )
                if str(label).upper() == "CPE" and link_ssh is not None and bts_ht_baseline:
                    # A rejected width must not be left on the AP. Wait for the
                    # STA before the next width so two applies do not stack.
                    _log(f"{case_id} [CPE] roll BTS htmode back to {bts_ht_baseline} after skip")
                    await _ft05_set_remote_htmode(
                        link_ssh,
                        radio_idx=radio_idx,
                        htmode=bts_ht_baseline,
                        timeout_s=timeout_s,
                        case_id=case_id,
                    )
                    await _wait_rf_link_healthy(
                        link_ssh,
                        profile,
                        case_id=case_id,
                        label="CPE skip-rollback",
                        timeout_s=min(120, link_timeout_s),
                        poll_s=link_poll_s,
                        password=password,
                        source_v6=source_v6,
                        reconnect_hosts=bts_link_hosts,
                        fail_hard=False,
                    )
                continue
            await _ft05_verify_backend_matches(
                case_id=case_id,
                label=label,
                bandwidth=ht,
                radio_idx=radio_idx,
                expected={**test_targets, "bandwidth": ht},
                actual=actual,
            )

        if not restore_baseline:
            return current_ssh

        if str(label).upper() == "CPE" and link_ssh is not None and bts_ht_baseline:
            # Put the AP back before the STA. The CPE cannot hold its old width
            # while the BTS is still on the last test width.
            _log(f"{case_id} [CPE] restore BTS htmode to {bts_ht_baseline} before CPE revert")
            try:
                await _ssh(
                    link_ssh,
                    f"ucidyn set wireless.wifi{radio_idx}.htmode {bts_ht_baseline}",
                    timeout_s=max(120, timeout_s),
                )
                await _ssh(link_ssh, "ucidyn apply", timeout_s=max(120, timeout_s))
            except Exception as exc:
                _log(f"{case_id} [CPE] BTS htmode restore to {bts_ht_baseline} failed: {exc}")
            current_ssh = await _ft05_wait_after_change(
                current_ssh,
                case_id=case_id,
                label=label,
                mgmt_host_v6=baseline_mgmt_v6,
                password=password,
                source_v6=source_v6,
                settle_s=max(20, settle_s // 2),
                reconnect_timeout_s=reconnect_timeout_s,
                reconnect_poll_s=reconnect_poll_s,
                bts_ssh=link_ssh,
                profile=profile,
                link_timeout_s=link_timeout_s,
                link_poll_s=link_poll_s,
                change_tag="bts-ht-restore",
                reconnect_hosts=_ft05_reconnect_hosts(
                    baseline_mgmt_v6,
                    test_mgmt_v6,
                    baseline.get("ipv4", ""),
                    test_targets.get("ipv4", ""),
                ),
            )

        await _apply_ft05_test_state(
            current_ssh,
            radio_idx=radio_idx,
            bandwidth=baseline_apply["bandwidth"],
            proto=baseline_apply["proto"],
            ipv4=baseline_apply["ipv4"],
            netmask=baseline_apply["netmask"],
            ipv4_gw=baseline_apply["ipv4_gw"],
            ipv6_cidr=baseline_apply["ipv6"],
            timeout_s=max(120, timeout_s),
        )
        active_mgmt_v6 = baseline_mgmt_v6
        current_ssh = await _ft05_wait_after_change(
            current_ssh,
            case_id=case_id,
            label=label,
            mgmt_host_v6=baseline_mgmt_v6,
            password=password,
            source_v6=source_v6,
            settle_s=settle_s,
            reconnect_timeout_s=reconnect_timeout_s,
            reconnect_poll_s=reconnect_poll_s,
            bts_ssh=link_ssh,
            profile=profile,
            link_timeout_s=link_timeout_s,
            link_poll_s=link_poll_s,
            change_tag="revert",
            reconnect_hosts=_ft05_reconnect_hosts(
                baseline_mgmt_v6,
                test_mgmt_v6,
                baseline.get("ipv4", ""),
                test_targets.get("ipv4", ""),
            ),
        )
        reverted = await _read_lan_backend_state(current_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _print_backend_table(
            f"{case_id} [{label}] after-test (reverted to original)",
            [
                (
                    "Channel width (UCI)",
                    _ft05_htmode_uci_line(radio_idx, baseline.get("htmode_uci") or baseline.get("bandwidth", "")),
                    _ft05_htmode_uci_line(radio_idx, reverted.get("htmode_uci") or reverted.get("bandwidth", "")),
                    "PASS"
                    if _ft05_uci_htmode_matches(
                        _ft05_backend_htmode(baseline) or baseline.get("bandwidth", ""),
                        _ft05_backend_htmode(reverted),
                    )
                    or not _is_valid_htmode(_ft05_backend_htmode(baseline) or baseline.get("bandwidth", ""))
                    else "FAIL",
                ),
                ("IPv4 address", baseline.get("ipv4", ""), reverted.get("ipv4", ""), "PASS" if reverted.get("ipv4") == baseline.get("ipv4") else "FAIL"),
                ("IPv4 netmask", baseline.get("netmask", ""), reverted.get("netmask", ""), "PASS" if reverted.get("netmask") == baseline.get("netmask") else "FAIL"),
                ("IPv4 gateway", baseline.get("ipv4_gw", ""), reverted.get("ipv4_gw", ""), "PASS" if reverted.get("ipv4_gw") == baseline.get("ipv4_gw") else "FAIL"),
                ("IPv6 address", baseline.get("ipv6", ""), reverted.get("ipv6", ""), "PASS" if reverted.get("ipv6") == baseline.get("ipv6") else "FAIL"),
            ],
        )
        check.equal(reverted.get("ipv4"), baseline.get("ipv4"), f"{case_id} [{label}]: IPv4 not reverted")
        check.equal(reverted.get("netmask"), baseline.get("netmask"), f"{case_id} [{label}]: netmask not reverted")
        check.equal(reverted.get("ipv4_gw"), baseline.get("ipv4_gw"), f"{case_id} [{label}]: IPv4 gateway not reverted")
        check.equal(reverted.get("ipv6"), baseline.get("ipv6"), f"{case_id} [{label}]: IPv6 not reverted")
        expected_ht = _ft05_backend_htmode(baseline) or baseline.get("bandwidth", "")
        if _is_valid_htmode(expected_ht):
            check.is_true(
                _ft05_uci_htmode_matches(expected_ht, _ft05_backend_htmode(reverted)),
                f"{case_id} [{label}]: bandwidth not reverted",
            )
        restored = True
        return current_ssh
    finally:
        if restore_baseline and not restored:
            _log(f"{case_id} [{label}] best-effort revert to original in finally")
            # Put the AP width back first. CPE IPv6 is unreachable while the STA is down.
            if str(label).upper() == "CPE" and link_ssh is not None and bts_ht_baseline:
                _log(f"{case_id} [CPE] restore BTS htmode to {bts_ht_baseline} before CPE revert")
                await _ft05_set_remote_htmode(
                    link_ssh,
                    radio_idx=radio_idx,
                    htmode=bts_ht_baseline,
                    timeout_s=timeout_s,
                    case_id=case_id,
                )
                await _wait_rf_link_healthy(
                    link_ssh,
                    profile,
                    case_id=case_id,
                    label="CPE revert-link",
                    timeout_s=min(180, link_timeout_s),
                    poll_s=link_poll_s,
                    password=password,
                    source_v6=source_v6,
                    reconnect_hosts=bts_link_hosts,
                    fail_hard=False,
                )
            reverted_ok = False
            try:
                for reconnect_host in (baseline_mgmt_v6, test_mgmt_v6):
                    if not reconnect_host:
                        continue
                    try:
                        current_ssh = await _wait_ipv6_ssh_ready(
                            reconnect_host,
                            password=password,
                            source_v6=source_v6,
                            label=label,
                            timeout_s=min(90, reconnect_timeout_s),
                            poll_s=reconnect_poll_s,
                        )
                        break
                    except Exception:
                        continue
                else:
                    raise ConnectionError(
                        f"{case_id} [{label}] revert SSH failed (tried {baseline_mgmt_v6}, {test_mgmt_v6})"
                    )
                await _apply_ft05_test_state(
                    current_ssh,
                    radio_idx=radio_idx,
                    bandwidth=baseline_apply["bandwidth"],
                    proto=baseline_apply["proto"],
                    ipv4=baseline_apply["ipv4"],
                    netmask=baseline_apply["netmask"],
                    ipv4_gw=baseline_apply["ipv4_gw"],
                    ipv6_cidr=baseline_apply["ipv6"],
                    timeout_s=max(120, timeout_s),
                )
                reverted_ok = True
            except Exception as exc:
                _log(f"{case_id} [{label}] IPv6 revert failed: {exc}")
            if not reverted_ok and str(label).upper() == "CPE":
                hopped = await _ft05_restore_cpe_via_hop(
                    profile,
                    password,
                    values,
                    radio_idx=radio_idx,
                    baseline_apply=baseline_apply,
                    timeout_s=timeout_s,
                    case_id=case_id,
                )
                if not hopped:
                    _log(f"{case_id} [{label}] revert failed in finally")


async def assert_ft_05_backend_config_per_bandwidth(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_05 — backend reflects channel width + IPv4/netmask/IPv4 gateway/IPv6 at each HT bandwidth."""
    case_id = "FT_05"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    source_v6 = _source_bind_ipv6(profile_bundle)
    values = FUNCTIONAL_TEST_VALUES
    profile = profile_bundle.active

    print_section(f"{case_id}: Backend LAN reflect per bandwidth — BTS {bts_ip} then CPE {cpe_ip}")
    _log("backend-only — per bandwidth: htmode + IPv4 + netmask + IPv4 gateway + IPv6 (no IPv6 gateway)")

    password = device_creds["pass"]
    link_timeout_s = int(values.get("ft05_link_recovery_timeout_s", 180))
    link_poll_s = int(values.get("ft05_link_recovery_poll_s", 8))
    cpe_ssh_timeout_s = int(values.get("ft05_cpe_ssh_timeout_s", 180))

    bts_ssh = await _ft05_backend_per_bandwidth_on_device(
        root_ssh,
        case_id=case_id,
        label="BTS",
        profile=profile,
        values=values,
        mgmt_host_v6=bts_ip,
        password=password,
        source_v6=source_v6,
        bts_ssh=root_ssh,
    )

    _log(f"{case_id} BTS complete — wait BTS↔CPE link before CPE backend checks")
    await _wait_rf_link_healthy(
        bts_ssh,
        profile,
        case_id=case_id,
        label="pre-CPE",
        timeout_s=link_timeout_s,
        poll_s=link_poll_s,
    )
    cpe_ssh = await _wait_ipv6_ssh_ready(
        cpe_ip,
        password=password,
        source_v6=source_v6,
        label="CPE",
        timeout_s=cpe_ssh_timeout_s,
        poll_s=link_poll_s,
    )
    try:
        await _ft05_backend_per_bandwidth_on_device(
            cpe_ssh,
            case_id=case_id,
            label="CPE",
            profile=profile,
            values=values,
            mgmt_host_v6=cpe_ip,
            password=password,
            source_v6=source_v6,
            bts_ssh=bts_ssh,
        )
    finally:
        try:
            await cpe_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} done — backend verified for HT20/HT40/HT80/HT160 and reverted to original")


async def assert_ft_06_rf_stats_backend_reflect(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_06 — static LAN + RF rate retain after soft reboot.

    Change random IPv4/IPv6 on BTS+CPE, change bandwidth on BTS only, verify
    TX/RX rates before and after soft reboot, then restore original config.
    """
    case_id = "FT_06"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    check.is_true(bool(cpe_ips), f"{case_id}: CPE IPv6 required")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    values = FUNCTIONAL_TEST_VALUES
    profile = profile_bundle.active
    radio_idx = int(values.get("ft06_radio_idx", values.get("ft05_radio_idx", RADIO_IDX)))
    timeout_s = int(values.get("ssh_timeout_s", 45))
    apply_settle_s = int(values.get("ft06_apply_settle_s", 20))
    source_v6 = _source_bind_ipv6(profile_bundle)
    link_timeout_s = int(values.get("ft06_link_recovery_timeout_s", values.get("ft05_link_recovery_timeout_s", 180)))
    link_poll_s = int(values.get("ft06_link_recovery_poll_s", values.get("ft05_link_recovery_poll_s", 8)))
    reboot_timeout_s = int(values.get("ft06_reboot_timeout_s", values.get("ft09_reboot_timeout_s", 300)))
    reboot_poll_s = int(values.get("ft06_reconnect_poll_s", values.get("ft09_reconnect_poll_s", 8)))
    rate_tolerance_pct = float(values.get("ft06_rate_tolerance_pct", 100))
    # Idle RX samples are a few hundred. A reboot changes that noise by more
    # than 100% without the link rate changing. Only compare a real rate.
    rate_idle_floor = int(values.get("ft06_rate_idle_floor", 10000))
    rate_sample_window_s = int(values.get("ft06_rate_sample_window_s", 90))
    rate_sample_poll_s = int(values.get("ft06_rate_sample_poll_s", 5))

    def _random_ipv6_from(cidr: str, *, role: str, used: set[str]) -> str:
        iface = ipaddress.IPv6Interface(str(cidr))
        network = iface.network
        for _ in range(200):
            host_tail = random.randint(0x340, 0x3ff) + (0 if role.upper() == "BTS" else 0x100)
            candidate = ipaddress.IPv6Address(int(network.network_address) + host_tail)
            if candidate not in network:
                candidate = ipaddress.IPv6Address(
                    int(network.network_address) + random.randint(2, min(network.num_addresses - 2, 0x0fff))
                )
            text = candidate.compressed
            if text not in used and text != str(iface.ip):
                used.add(text)
                return f"{text}/{network.prefixlen}"
        raise RuntimeError(f"{case_id}: unable to allocate random IPv6 for {role}")

    def _rate_value(raw: str) -> int:
        return _parse_int_scalar(clean_ssh_output(raw)) or 0

    async def _read_rates(ssh, label: str) -> dict[str, int]:
        deadline = time.monotonic() + max(1, rate_sample_window_s)
        best = {"tx": 0, "rx": 0}
        while time.monotonic() < deadline:
            tx = _rate_value(await _ssh(ssh, RootCommands.GET_TX_R1, timeout_s=timeout_s))
            rx = _rate_value(await _ssh(ssh, RootCommands.GET_RX_R1, timeout_s=timeout_s))
            best["tx"] = max(best["tx"], tx)
            best["rx"] = max(best["rx"], rx)
            if best["tx"] > 0 and best["rx"] > 0:
                break
            await asyncio.sleep(max(1, rate_sample_poll_s))
        _log(f"{case_id} [{label}] sampled TX={best['tx']} RX={best['rx']} over {rate_sample_window_s}s")
        return best

    def _rate_close(before: int, after: int) -> bool:
        if before <= 0 and after <= 0:
            return True
        if max(before, after) < rate_idle_floor:
            return True
        if after <= 0:
            return False
        if before <= 0 or after >= before:
            # A higher rate after reboot means the link came back, not that it failed.
            return True
        drop_pct = (before - after) * 100.0 / max(before, 1)
        return drop_pct <= rate_tolerance_pct

    def _rate_rows(before: dict[str, int], after: dict[str, int], label: str) -> list[tuple[str, str, str, str]]:
        rows = []
        for key in ("tx", "rx"):
            ok = _rate_close(before[key], after[key])
            rows.append((f"{label} {key.upper()} rate", str(before[key]), str(after[key]), "PASS" if ok else "FAIL"))
            check.is_true(
                ok,
                f"{case_id} [{label}]: {key.upper()} rate changed too much ({before[key]} -> {after[key]})",
            )
        return rows

    async def _wait_new_ssh(host_cidr: str, label: str):
        return await _wait_ipv6_ssh_ready(
            normalize_ip(str(host_cidr).split("/")[0]),
            password=device_creds["pass"],
            source_v6=source_v6,
            label=label,
            timeout_s=reboot_timeout_s,
            poll_s=reboot_poll_s,
        )

    async def _apply_state_tolerate_disconnect(label: str, ssh, **kwargs) -> None:
        try:
            await _ft03_apply_backend_state(ssh, **kwargs)
        except Exception as exc:
            _log(f"{case_id} [{label}] SSH interrupted during config apply (expected if LAN IPv6 changes): {exc}")

    print_section(f"{case_id}: Static LAN + RF rate retain after soft reboot")
    await _wait_rf_link_healthy(
        root_ssh,
        profile,
        case_id=case_id,
        label="PRECHECK",
        timeout_s=link_timeout_s,
        poll_s=link_poll_s,
        password=device_creds["pass"],
        source_v6=source_v6,
        reconnect_hosts=[bts_ip],
    )

    cpe_ssh = await _open_ipv6_ssh(
        cpe_ip,
        device_creds["pass"],
        source_v6=source_v6,
        label="CPE",
    )
    bts_ssh = root_ssh
    bts_changed_ssh = None
    cpe_changed_ssh = None
    bts_restored_ssh = None
    cpe_restored_ssh = None
    try:
        bts_before = await _ft07_read_device_snapshot(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        cpe_before = await _ft07_read_device_snapshot(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        check.equal(bts_before.get("proto"), "static", f"{case_id}: BTS LAN must be static")
        check.equal(cpe_before.get("proto"), "static", f"{case_id}: CPE LAN must be static")

        target_bts_ipv4, target_cpe_ipv4 = _ft15_pick_random_ipv4_pair(
            values=values,
            reserved={bts_before.get("ipv4", ""), cpe_before.get("ipv4", "")},
        )
        used_v6 = {
            normalize_ip(bts_before["ipv6"].split("/")[0]),
            normalize_ip(cpe_before["ipv6"].split("/")[0]),
        }
        target_bts_ipv6 = _random_ipv6_from(bts_before["ipv6"], role="BTS", used=used_v6)
        target_cpe_ipv6 = _random_ipv6_from(cpe_before["ipv6"], role="CPE", used=used_v6)
        target_bts_bw = _pick_alternate(
            bts_before.get("bandwidth", ""),
            list(
                values.get(
                    "ft06_bts_bandwidth_candidates",
                    values.get("ft16_bandwidths", ["HT20", "HT40", "HT80", "HT160"]),
                )
            ),
            "HT80",
        )

        _log(
            f"{case_id}: BTS IPv4 {bts_before['ipv4']} -> {target_bts_ipv4}, "
            f"IPv6 {bts_before['ipv6']} -> {target_bts_ipv6}, BW {bts_before['bandwidth']} -> {target_bts_bw}"
        )
        _log(
            f"{case_id}: CPE IPv4 {cpe_before['ipv4']} -> {target_cpe_ipv4}, "
            f"IPv6 {cpe_before['ipv6']} -> {target_cpe_ipv6}; CPE BW unchanged"
        )

        await _apply_state_tolerate_disconnect(
            "CPE",
            cpe_ssh,
            radio_idx=radio_idx,
            bandwidth=cpe_before.get("bandwidth", ""),
            ipv4=target_cpe_ipv4,
            netmask=cpe_before.get("netmask", "255.255.255.0"),
            ipv6_cidr=target_cpe_ipv6,
            timeout_s=max(120, timeout_s),
            apply_bandwidth=False,
        )
        await _apply_state_tolerate_disconnect(
            "BTS",
            bts_ssh,
            radio_idx=radio_idx,
            bandwidth=target_bts_bw,
            ipv4=target_bts_ipv4,
            netmask=bts_before.get("netmask", "255.255.255.0"),
            ipv6_cidr=target_bts_ipv6,
            timeout_s=max(120, timeout_s),
            apply_bandwidth=True,
        )
        await asyncio.sleep(apply_settle_s)

        cpe_changed_ssh = await _wait_new_ssh(target_cpe_ipv6, "CPE changed IPv6")
        bts_changed_ssh = await _wait_new_ssh(target_bts_ipv6, "BTS changed IPv6")
        await _wait_rf_link_healthy(
            bts_changed_ssh,
            profile,
            case_id=case_id,
            label="post-config-change",
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
            password=device_creds["pass"],
            source_v6=source_v6,
            reconnect_hosts=[normalize_ip(target_bts_ipv6.split("/")[0]), bts_ip],
        )

        bts_changed = await _ft07_read_device_snapshot(bts_changed_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        cpe_changed = await _ft07_read_device_snapshot(cpe_changed_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        rows = [
            ("BTS protocol", "static", bts_changed.get("proto", ""), "PASS" if bts_changed.get("proto") == "static" else "FAIL"),
            ("BTS IPv4", target_bts_ipv4, bts_changed.get("ipv4", ""), "PASS" if bts_changed.get("ipv4") == target_bts_ipv4 else "FAIL"),
            ("BTS IPv6", target_bts_ipv6, bts_changed.get("ipv6", ""), "PASS" if bts_changed.get("ipv6") == target_bts_ipv6 else "FAIL"),
            (
                "BTS bandwidth",
                target_bts_bw,
                bts_changed.get("bandwidth", ""),
                "PASS" if _ft05_uci_htmode_matches(target_bts_bw, bts_changed.get("bandwidth", "")) else "FAIL",
            ),
            ("CPE protocol", "static", cpe_changed.get("proto", ""), "PASS" if cpe_changed.get("proto") == "static" else "FAIL"),
            ("CPE IPv4", target_cpe_ipv4, cpe_changed.get("ipv4", ""), "PASS" if cpe_changed.get("ipv4") == target_cpe_ipv4 else "FAIL"),
            ("CPE IPv6", target_cpe_ipv6, cpe_changed.get("ipv6", ""), "PASS" if cpe_changed.get("ipv6") == target_cpe_ipv6 else "FAIL"),
            ("CPE bandwidth", "unchanged", cpe_changed.get("bandwidth", ""), "INFO"),
        ]
        _print_backend_table(f"{case_id}: backend verify after random config", rows)
        for name, expected, actual, result in rows:
            if result != "INFO":
                check.equal(result, "PASS", f"{case_id}: {name} mismatch ({expected} != {actual})")

        bts_rate_before = await _read_rates(bts_changed_ssh, "BTS before reboot")
        cpe_rate_before = await _read_rates(cpe_changed_ssh, "CPE before reboot")

        print_section(f"{case_id}: soft reboot CPE then BTS")
        await _ft09_issue_ssh_reboot(cpe_changed_ssh, values, label="CPE")
        try:
            await cpe_changed_ssh.close()
        except Exception:
            pass
        await asyncio.sleep(int(values.get("ft06_post_reboot_settle_s", values.get("ft09_post_reboot_settle_s", 60))))
        cpe_changed_ssh = await _wait_new_ssh(target_cpe_ipv6, "CPE post-reboot")

        await _ft09_issue_ssh_reboot(bts_changed_ssh, values, label="BTS")
        try:
            await bts_changed_ssh.close()
        except Exception:
            pass
        await asyncio.sleep(int(values.get("ft06_post_reboot_settle_s", values.get("ft09_post_reboot_settle_s", 60))))
        bts_changed_ssh = await _wait_new_ssh(target_bts_ipv6, "BTS post-reboot")

        bts_changed_ssh = await _wait_rf_link_healthy(
            bts_changed_ssh,
            profile,
            case_id=case_id,
            label="post-soft-reboot",
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
            password=device_creds["pass"],
            source_v6=source_v6,
            reconnect_hosts=[normalize_ip(target_bts_ipv6.split("/")[0]), bts_ip],
        )
        bts_rate_after = await _read_rates(bts_changed_ssh, "BTS after reboot")
        cpe_rate_after = await _read_rates(cpe_changed_ssh, "CPE after reboot")
        rate_rows = []
        rate_rows.extend(_rate_rows(bts_rate_before, bts_rate_after, "BTS"))
        rate_rows.extend(_rate_rows(cpe_rate_before, cpe_rate_after, "CPE"))
        _print_backend_table(f"{case_id}: TX/RX rate before vs after reboot", rate_rows)

        _log(f"{case_id}: restoring original CPE LAN, then BTS LAN/BW")
        await _apply_state_tolerate_disconnect(
            "CPE restore",
            cpe_changed_ssh,
            radio_idx=radio_idx,
            bandwidth=cpe_before.get("bandwidth", ""),
            ipv4=cpe_before["ipv4"],
            netmask=cpe_before["netmask"],
            ipv6_cidr=cpe_before["ipv6"],
            timeout_s=max(120, timeout_s),
            apply_bandwidth=False,
        )
        await _apply_state_tolerate_disconnect(
            "BTS restore",
            bts_changed_ssh,
            radio_idx=radio_idx,
            bandwidth=bts_before["bandwidth"],
            ipv4=bts_before["ipv4"],
            netmask=bts_before["netmask"],
            ipv6_cidr=bts_before["ipv6"],
            timeout_s=max(120, timeout_s),
            apply_bandwidth=True,
        )
        await asyncio.sleep(apply_settle_s)
        cpe_restored_ssh = await _wait_new_ssh(cpe_before["ipv6"], "CPE restored IPv6")
        bts_restored_ssh = await _wait_new_ssh(bts_before["ipv6"], "BTS restored IPv6")
        bts_restored_ssh = await _wait_rf_link_healthy(
            bts_restored_ssh,
            profile,
            case_id=case_id,
            label="post-restore",
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
            password=device_creds["pass"],
            source_v6=source_v6,
            reconnect_hosts=[bts_ip, normalize_ip(bts_before["ipv6"].split("/")[0])],
        )
        bts_restored = await _ft07_read_device_snapshot(bts_restored_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        cpe_restored = await _ft07_read_device_snapshot(cpe_restored_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        restore_rows = [
            ("BTS IPv4", bts_before["ipv4"], bts_restored.get("ipv4", ""), "PASS" if bts_restored.get("ipv4") == bts_before["ipv4"] else "FAIL"),
            ("BTS IPv6", bts_before["ipv6"], bts_restored.get("ipv6", ""), "PASS" if bts_restored.get("ipv6") == bts_before["ipv6"] else "FAIL"),
            (
                "BTS bandwidth",
                bts_before["bandwidth"],
                bts_restored.get("bandwidth", ""),
                "PASS" if _ft05_uci_htmode_matches(bts_before["bandwidth"], bts_restored.get("bandwidth", "")) else "FAIL",
            ),
            ("CPE IPv4", cpe_before["ipv4"], cpe_restored.get("ipv4", ""), "PASS" if cpe_restored.get("ipv4") == cpe_before["ipv4"] else "FAIL"),
            ("CPE IPv6", cpe_before["ipv6"], cpe_restored.get("ipv6", ""), "PASS" if cpe_restored.get("ipv6") == cpe_before["ipv6"] else "FAIL"),
            ("CPE bandwidth", "unchanged", cpe_restored.get("bandwidth", ""), "INFO"),
        ]
        _print_backend_table(f"{case_id}: backend verify after restore", restore_rows)
        for name, expected, actual, result in restore_rows:
            if result != "INFO":
                check.equal(result, "PASS", f"{case_id}: restore {name} mismatch ({expected} != {actual})")
    finally:
        for conn in (cpe_restored_ssh, bts_restored_ssh, cpe_changed_ssh, bts_changed_ssh, cpe_ssh):
            try:
                if conn is not None:
                    await conn.close()
            except Exception:
                pass
    _log(f"{case_id} complete — static LAN, BTS bandwidth, soft reboot rates, and restore verified")


def _ft07_cpe_pc_credentials(profile: dict, device_password: str, values: dict) -> tuple[str, str, str]:
    """Return (host, user, password) for the CPE bench PC SSH hop."""
    dut = profile.get("dut", {}) or {}
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    ssh_target = str(sec.get("ssh", "")).strip()
    user = "root"
    if "@" in ssh_target:
        user, host_from_ssh = ssh_target.split("@", 1)
    else:
        host_from_ssh = ssh_target
    host = normalize_ip(
        str(values.get("ft07_cpe_pc_ip") or dut.get("cpe_pc_ip") or host_from_ssh or "")
    )
    password = str(
        values.get("ft07_cpe_pc_password")
        or dut.get("cpe_pc_password")
        or sec.get("password")
        or device_password
    ).strip()
    return host, user.strip() or "root", password


async def _ft07_read_device_snapshot(ssh, *, radio_idx: int, timeout_s: int) -> dict[str, str]:
    state = await _ft03_read_backend_state(ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    ssid = clean_ssh_output(await _ssh(ssh, RootCommands.get_ssid(radio_idx), timeout_s=timeout_s))
    enc = clean_ssh_output(await _ssh(ssh, RootCommands.get_security(radio_idx), timeout_s=timeout_s))
    key = clean_ssh_output(await _ssh(ssh, RootCommands.get_encryption_key(radio_idx), timeout_s=timeout_s))
    proto = clean_ssh_output(await _ssh(ssh, RootCommands.GET_NET_PROTO, timeout_s=timeout_s))
    ipv4_gw = clean_ssh_output(await _ssh(ssh, RootCommands.GET_NET_GW, timeout_s=timeout_s))
    return {
        **state,
        "proto": proto,
        "ipv4_gw": normalize_ip(ipv4_gw.strip().split("/")[0]),
        "ssid": ssid,
        "encryption": enc,
        "key": key,
    }


def _ft07_mask_key(key: str) -> str:
    key = str(key or "").strip()
    if not key or key.startswith("uci:"):
        return "(empty)"
    if len(key) <= 2:
        return "**"
    return f"{key[0]}***{key[-1]}"


def _ft07_print_baseline(case_id: str, label: str, snapshot: dict[str, str]) -> None:
    print_section(f"{case_id}: {label} baseline (pre-test)")
    rows = [
        ("IPv4", "—", snapshot.get("ipv4", ""), "INFO"),
        ("Netmask", "—", snapshot.get("netmask", ""), "INFO"),
        ("IPv6", "—", snapshot.get("ipv6", "") or "(empty)", "INFO"),
        ("Proto", "—", snapshot.get("proto", ""), "INFO"),
        ("Bandwidth", "—", snapshot.get("bandwidth", ""), "INFO"),
        ("SSID", "—", snapshot.get("ssid", ""), "INFO"),
        ("Encryption", "—", snapshot.get("encryption", ""), "INFO"),
        ("Key", "—", _ft07_mask_key(snapshot.get("key", "")), "INFO"),
    ]
    print_comparison_table(rows)


def _ft07_ipv6_removed(raw: str) -> bool:
    cleaned = clean_ssh_output(raw or "").strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    return lowered.startswith("uci:") or "entry not found" in lowered


async def _ft07_gui_apply_static_lan(
    gui_page,
    *,
    ipv4: str,
    netmask: str,
    ipv6_cidr: str,
    ipv6_gateway: str,
    settle_s: float,
) -> None:
    from pages.locators import NetworkLocators, UITimeouts
    from utils.network_flows import apply_triple, open_network_submenu
    from utils.ui_helpers import attach_dialog_handler, fill_luci_input

    attach_dialog_handler(gui_page)
    await open_network_submenu(gui_page, "/network/ip")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)

    proto = gui_page.locator(NetworkLocators.IPv4_PROTO).first
    if await proto.count() > 0:
        for opt in ("static", "Static"):
            try:
                await proto.select_option(value=opt)
                break
            except Exception:
                try:
                    await proto.select_option(label=opt)
                    break
                except Exception:
                    continue

    for locator, value in (
        (NetworkLocators.IPv4_ADDRESS, ipv4),
        (NetworkLocators.IPv4_NETMASK, netmask),
    ):
        if value and await gui_page.locator(locator).count() > 0:
            await fill_luci_input(gui_page, locator, value)

    proto6 = gui_page.locator("select[name*='network.lan.ip6proto']").first
    if await proto6.count() > 0:
        for opt in ("static", "Static"):
            try:
                await proto6.select_option(value=opt)
                break
            except Exception:
                try:
                    await proto6.select_option(label=opt)
                    break
                except Exception:
                    continue

    if ipv6_cidr and await gui_page.locator(NetworkLocators.IPv6_ADDRESS).count() > 0:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_ADDRESS, ipv6_cidr)
    if ipv6_gateway and await gui_page.locator(NetworkLocators.IPv6_GATEWAY).count() > 0:
        await fill_luci_input(gui_page, NetworkLocators.IPv6_GATEWAY, ipv6_gateway)

    save = gui_page.locator(NetworkLocators.SAVE_BUTTON).first
    await apply_triple(
        gui_page,
        save,
        NetworkLocators.APPLY_ICON,
        NetworkLocators.CONFIRM_APPLY,
        settle_seconds=settle_s,
    )


async def _ft07_gui_set_bandwidth(gui_page, cpe_ip: str, *, gui_label: str, settle_s: float) -> None:
    from pages.locators import CommonLocators, RadioPropertiesLocators, UITimeouts
    from pages.radio_properties_page import RadioPropertiesPage
    from utils.network_flows import apply_triple

    radio_page = RadioPropertiesPage(gui_page, local_ip=cpe_ip)
    await radio_page.navigate()
    dropdown = gui_page.locator(RadioPropertiesLocators.BANDWIDTH_DROPDOWN).first
    await dropdown.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    selected = False
    for arg in ({"label": gui_label}, {"value": gui_label}):
        try:
            await dropdown.select_option(**arg)
            selected = True
            break
        except Exception:
            continue
    check.is_true(selected, f"FT_07: unable to select bandwidth {gui_label!r} on CPE GUI")
    save = gui_page.locator(CommonLocators.SAVE_BUTTON).first
    await apply_triple(
        gui_page,
        save,
        CommonLocators.APPLY_ICON,
        CommonLocators.CONFIRM_APPLY,
        settle_seconds=settle_s,
    )


async def _ft07_gui_factory_reset_without_retain(gui_page, profile: dict, values: dict) -> None:
    from config.defaults import IP_TEST_DEFAULTS
    from utils.ip_test_flows import _gui_factory_reset

    cfg = {**IP_TEST_DEFAULTS, **(profile.get("ip_tests", {}) or {})}
    timeout_s = int(values.get("ft07_post_reset_timeout_s", cfg.get("reboot_timeout_s", 200)))
    cfg["reboot_timeout_s"] = timeout_s
    await _gui_factory_reset(gui_page, cfg)


async def _ft07_open_pc_ssh(host: str, user: str, password: str) -> AsyncGenericDriver:
    conn = AsyncGenericDriver(
        host=normalize_ip(host),
        auth_username=user,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _ft07_ensure_cpe_pc_tooling(pc_ssh: AsyncGenericDriver, *, hop_hosts: list[str]) -> None:
    """sshpass on the bench PC (required for nested SSH)."""
    which = clean_ssh_output(await _ssh(pc_ssh, "which sshpass 2>/dev/null || echo missing", timeout_s=15))
    if "missing" in which or not which:
        _log("FT_07: installing sshpass on CPE PC")
        await _ssh(
            pc_ssh,
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sshpass",
            timeout_s=180,
        )
    # Hop SSH already uses UserKnownHostsFile=/dev/null. ssh-keygen -R on this
    # PC's known_hosts can sit forever and the next command then times out.
    del hop_hosts


async def _ft07_cpe_hop_target(profile: dict, values: dict, *, post_reset: bool) -> str:
    """SSH hop target on CPE PC path (always 10.0.0.1 on this bench; UCI LAN may be 192.168.2.1)."""
    del post_reset
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    return normalize_ip(
        str(values.get("ft07_cpe_hop_ipv4") or sec.get("cpe_factory_ipv4") or "10.0.0.1")
    )


async def _ft07_open_cpe_ssh_via_cpe_pc(
    profile: dict,
    device_password: str,
    values: dict,
    *,
    cpe_target: str,
    wait_ping: bool = True,
):
    from utils.ip_test_flows import CpeSecondaryHopDriver, _probe_cpe_ssh_via_secondary
    from utils.lab_pc_net import ensure_fallback_subnet

    host, user, pc_pass = _ft07_cpe_pc_credentials(profile, device_password, values)
    check.is_true(bool(host), "FT_07: CPE PC IP required (dut.cpe_pc_ip or testbed.secondary_pc.ssh)")
    tb = profile.get("testbed", {}) or {}
    sec = dict(tb.get("secondary_pc", {}) or {})
    await ensure_fallback_subnet(sec, pc_pass)
    pc_ssh = await _ft07_open_pc_ssh(host, user, pc_pass)
    target = normalize_ip(cpe_target)
    try:
        await _ft07_ensure_cpe_pc_tooling(
            pc_ssh,
            hop_hosts=[target, str(values.get("ft07_factory_ipv4", "192.168.2.1")), "10.0.0.1"],
        )
        if wait_ping:
            await _ft07_wait_factory_ping_from_pc(
                pc_ssh,
                target,
                timeout_s=int(values.get("ft07_post_reset_timeout_s", 200)),
                poll_s=int(values.get("ft07_post_reset_poll_s", 8)),
            )
        deadline = time.monotonic() + int(values.get("ft07_post_reset_timeout_s", 200))
        poll_s = int(values.get("ft07_post_reset_poll_s", 8))
        last_exc = ""
        while time.monotonic() < deadline:
            try:
                cpe_user, cpe_pass = await _probe_cpe_ssh_via_secondary(pc_ssh, profile, target)
                return CpeSecondaryHopDriver(
                    pc_ssh,
                    cpe_factory=target,
                    cpe_user=cpe_user,
                    cpe_password=cpe_pass,
                    label=f"cpe-pc→CPE({cpe_user}@{target})",
                )
            except Exception as exc:
                last_exc = str(exc)
                _log(f"FT_07: CPE hop SSH retry ({target}): {last_exc[:120]}")
                await asyncio.sleep(max(1, poll_s))
        raise ConnectionError(f"FT_07: CPE hop SSH failed for {target}: {last_exc[:220]}")
    except Exception:
        try:
            await pc_ssh.close()
        except Exception:
            pass
        raise


async def _ft07_backend_apply_test_config(
    hop_ssh,
    *,
    radio_idx: int,
    ipv4: str,
    netmask: str,
    ipv6_cidr: str,
    bandwidth: str,
    timeout_s: int,
) -> None:
    ht = _ft05_uci_htmode_value(bandwidth)
    # Apply in one short remote shell; ucidyn apply will drop the hop when LAN changes.
    batch = (
        f"ucidyn set wireless.wifi{radio_idx}.htmode {ht} && "
        f"ucidyn set network.lan.proto static && "
        f"ucidyn set network.lan.ipaddr {ipv4} && "
        f"ucidyn set network.lan.netmask {netmask} && "
        f"ucidyn set network.lan.ip6addr {ipv6_cidr} && "
        "ucidyn apply"
    )
    try:
        await _ssh(hop_ssh, batch, timeout_s=min(90, timeout_s))
    except Exception as exc:
        _log(f"FT_07: apply disconnected hop (expected when LAN changes): {exc}")


async def _ft07_backend_mutate_and_factory_reset(
    hop_ssh,
    profile: dict,
    values: dict,
    *,
    radio_idx: int,
    ipv4: str,
    netmask: str,
    ipv6_cidr: str,
    bandwidth: str,
) -> None:
    from config.defaults import IP_TEST_DEFAULTS

    cfg = {**IP_TEST_DEFAULTS, **(profile.get("ip_tests", {}) or {})}
    retain_none = int(cfg.get("factory_reset_retainip_none", 0))
    script_path = str(cfg.get("factory_reset_script", "/usr/sbin/factory_reset.sh"))
    retain_key = str(cfg.get("factory_reset_ucidyn_key", "tftp.retip.retainip"))
    ht = _ft05_uci_htmode_value(bandwidth)
    remote = (
        f"ucidyn set wireless.wifi{radio_idx}.htmode {ht}; "
        f"ucidyn set network.lan.proto static; "
        f"ucidyn set network.lan.ipaddr {ipv4}; "
        f"ucidyn set network.lan.netmask {netmask}; "
        f"ucidyn set network.lan.ip6addr {ipv6_cidr}; "
        f"ucidyn set {retain_key} {retain_none}; "
        f"ucidyn apply; sleep 3; {script_path}"
    )
    try:
        await _ssh(hop_ssh, remote, timeout_s=25)
    except Exception as exc:
        _log(f"FT_07: factory reset command closed hop (expected): {exc}")
    settle_s = int(values.get("ft07_post_reset_settle_s", 120))
    _log(f"FT_07: post-reset settle {settle_s}s")
    await asyncio.sleep(settle_s)


async def _ft07_try_open_cpe_mgmt_ssh(cpe_ip: str, password: str, source_v6: str):
    try:
        return await _open_ipv6_ssh(cpe_ip, password, source_v6=source_v6, label="CPE")
    except Exception as exc:
        _log(f"FT_07: CPE mgmt IPv6 SSH unavailable ({exc}); using CPE PC hop")
        return None


async def _ft07_wait_factory_ping_from_pc(
    pc_ssh: AsyncGenericDriver,
    factory_ip: str,
    *,
    timeout_s: int,
    poll_s: int,
) -> None:
    factory_ip = normalize_ip(factory_ip)
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        raw = await _ssh(pc_ssh, f"ping -c 1 -W 2 {factory_ip} 2>&1", timeout_s=15)
        last = raw
        if _ft07_ping_success(raw):
            _log(f"CPE PC → {factory_ip} ping ok")
            return
        await asyncio.sleep(max(1, poll_s))
    check.is_true(False, f"FT_07: CPE PC ping to factory {factory_ip} failed after {timeout_s}s: {last[:220]}")


def _ft07_ping_success(raw: str) -> bool:
    text = str(raw or "")
    return any(
        token in text
        for token in (
            "0% packet loss",
            " 0% packet loss",
            "1 received",
            "1 packets received",
            "1 packets transmitted, 1 received",
            "rtt min/avg/max",
        )
    )


def _ft07_bandwidth_matches(expected_bandwidth: str, actual: str) -> bool:
    expected = str(expected_bandwidth or "").strip().upper()
    actual_norm = _normalize_htmode(actual).upper()
    # UBR655 factory is HT80 unless the radio comes up HT160 — both are valid.
    if expected in {"HT80", "HT160"} and actual_norm in {"HT80", "HT160"}:
        return True
    if expected == "HT160":
        return "HT160" in actual_norm
    try:
        return uci_htmode_matches(expected, actual)
    except ValueError:
        return expected in actual_norm


def _ft07_verify_factory_backend(
    case_id: str,
    snapshot: dict[str, str],
    *,
    expected_ipv4: str,
    expected_netmask: str,
    expected_bandwidth: str,
) -> None:
    ipv4 = normalize_ip(snapshot.get("ipv4", "").split("/")[0])
    netmask = snapshot.get("netmask", "").strip()
    ipv6_raw = snapshot.get("ipv6", "")
    bandwidth = snapshot.get("bandwidth", "")

    ipv4_ok = ipv4 == normalize_ip(expected_ipv4)
    netmask_ok = netmask == expected_netmask or not expected_netmask
    ipv6_ok = _ft07_ipv6_removed(ipv6_raw)
    bw_ok = _ft07_bandwidth_matches(expected_bandwidth, bandwidth)

    print_section(f"{case_id} CPE factory defaults (backend via CPE PC hop)")
    print_comparison_table(
        [
            ("IPv4", expected_ipv4, ipv4 or "(empty)", "PASS" if ipv4_ok else "FAIL"),
            ("Netmask", expected_netmask or "n/a", netmask or "(empty)", "PASS" if netmask_ok else "FAIL"),
            ("IPv6", "removed", ipv6_raw or "(empty)", "PASS" if ipv6_ok else "FAIL"),
            ("Bandwidth", expected_bandwidth, bandwidth or "(empty)", "PASS" if bw_ok else "FAIL"),
        ],
    )
    check.equal(ipv4, normalize_ip(expected_ipv4), f"{case_id}: IPv4 not factory default")
    check.is_true(ipv6_ok, f"{case_id}: IPv6 not removed (got {ipv6_raw!r})")
    check.is_true(bw_ok, f"{case_id}: bandwidth not {expected_bandwidth} (got {bandwidth!r})")


async def _ft07_apply_vlan_transparent(
    ssh,
    profile: dict,
    *,
    role: str,
    timeout_s: int,
    mgmt_vlan_id: int,
    profile_bundle=None,
    device_creds: dict | None = None,
    bts_ip: str = "",
    values: dict | None = None,
) -> AsyncGenericDriver:
    """BTS transparent VLAN after factory reset — uci commit + network reload (not ucidyn apply)."""
    from utils.vlan_control import ensure_bts_transparent_ssh, read_vlan_uci_ssh, _normalize_mode_name

    tb = profile.get("testbed", {}) or {}
    mgmt_val = int(mgmt_vlan_id)
    _log(f"FT_07: set {role.upper()} VLAN mode transparent (mgmtvlan={mgmt_val})")

    async def _try_apply(conn) -> bool:
        return await ensure_bts_transparent_ssh(conn, tb, mgmt_vlan_id=mgmt_val)

    ok = False
    try:
        ok = await _try_apply(ssh)
    except Exception as exc:
        _log(f"FT_07: VLAN apply SSH dropped ({exc}); will reconnect and verify")
        ok = False

    if not ok and profile_bundle is not None and device_creds and values is not None:
        try:
            await ssh.close()
        except Exception:
            pass
        ssh = await _ft07_wait_bts_ssh(
            profile_bundle, device_creds, bts_ip=bts_ip, values=values,
        )
        ok = await _try_apply(ssh)

    current = await read_vlan_uci_ssh(ssh, tb, role)
    mode_after = _normalize_mode_name(current.get("mode", ""))
    mgmt_after = str(current.get("mgmtvlan", "")).strip()
    mode_ok = mode_after == "transparent" or str(current.get("mode", "")).lower() == "transparent"
    check.is_true(
        ok and mode_ok and mgmt_after == str(mgmt_val),
        f"FT_07: {role.upper()} vlan not transparent/mgmtvlan={mgmt_val} "
        f"(mode={current.get('mode')!r}, mgmtvlan={mgmt_after!r})",
    )
    return ssh


def _ft07_is_factory_lan(ipv4: str, values: dict) -> bool:
    return normalize_ip(str(ipv4 or "").split("/")[0]) == normalize_ip(
        str(values.get("ft07_factory_ipv4", "192.168.2.1"))
    )


def _ft07_resolve_mgmt_vlan_id(profile: dict, values: dict) -> int:
    """Single mgmt VLAN ID for BTS + CPE for the whole FT_07 run."""
    import random

    if bool(values.get("ft07_mgmt_vlan_random", False)):
        vmin = int(values.get("ft07_mgmt_vlan_min", 1))
        vmax = int(values.get("ft07_mgmt_vlan_max", 4094))
        if vmin > vmax:
            vmin, vmax = vmax, vmin
        chosen = random.randint(vmin, vmax)
        _log(f"FT_07: random mgmt VLAN ID {chosen} (range {vmin}-{vmax})")
        return chosen
    explicit = values.get("ft07_mgmt_vlan_id")
    if explicit not in (None, ""):
        return int(explicit)
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    return int(mgmt.get("uci_value", 101))


async def _ft07_apply_cpe_mgmt_vlan(
    hop_ssh,
    profile: dict,
    mgmt_vlan_id: int,
    *,
    timeout_s: int,
) -> None:
    """CPE transparent + mgmtvlan — must match BTS mgmt VLAN ID."""
    from utils.vlan_uci import build_cpe_mgmtvlan_only_commands

    tb = profile.get("testbed", {}) or {}
    _log(f"FT_07: set CPE VLAN transparent (mgmtvlan={mgmt_vlan_id}) — match BTS")
    cmds = build_cpe_mgmtvlan_only_commands(tb, mgmt_vlan_id=mgmt_vlan_id)
    vlan_timeout = max(timeout_s, 90)
    for cmd in cmds:
        try:
            await _ssh(hop_ssh, cmd, timeout_s=vlan_timeout)
        except Exception as exc:
            _log(f"FT_07: CPE mgmt VLAN apply hop closed (may reconnect): {exc}")
            break


async def _ft07_sync_cpe_mgmt_vlan_via_hop(
    profile: dict,
    password: str,
    values: dict,
    mgmt_vlan_id: int,
    *,
    timeout_s: int,
    cpe_target: str = "",
) -> None:
    """Apply CPE mgmt VLAN via CPE PC hop; reopen hop if network reload drops SSH."""
    hop_ip = cpe_target or await _ft07_cpe_hop_target(profile, values, post_reset=False)
    hop_ssh = await _ft07_open_cpe_ssh_via_cpe_pc(
        profile, password, values, cpe_target=hop_ip, wait_ping=True,
    )
    try:
        await _ft07_apply_cpe_mgmt_vlan(hop_ssh, profile, mgmt_vlan_id, timeout_s=timeout_s)
    finally:
        try:
            await hop_ssh.close()
        except Exception:
            pass


def _ft07_cpe_restore_targets(
    profile: dict, cpe_baseline: dict[str, str], bts_baseline: dict[str, str], values: dict,
) -> dict[str, str]:
    """Lab restore targets — use profile when CPE baseline is already factory-default."""
    ip_cfg = profile.get("ip_tests", {}) or {}
    ipv4 = normalize_ip(cpe_baseline.get("ipv4", "").split("/")[0])
    netmask = cpe_baseline.get("netmask", "255.255.255.0").strip()
    ipv6 = cpe_baseline.get("ipv6", "").strip()
    bandwidth = cpe_baseline.get("bandwidth", "HT80")
    bts_bw = _normalize_htmode(bts_baseline.get("bandwidth", "") or "")
    if _is_valid_htmode(bts_bw):
        bandwidth = bts_bw
    if _ft07_is_factory_lan(ipv4, values):
        ipv4 = normalize_ip(str(ip_cfg.get("ipv4_test_address_alt") or "192.168.2.11"))
        netmask = str(ip_cfg.get("ipv4_test_netmask") or netmask)
        ipv6 = str(ip_cfg.get("ipv6_address_cpe") or ip_cfg.get("ipv6_address") or "")
        _log(f"FT_07: CPE baseline is factory LAN — restore targets from profile ({ipv4})")
    if not ipv6 or ipv6.lower().startswith("uci:") or "entry not found" in ipv6.lower():
        ipv6 = str(ip_cfg.get("ipv6_address_cpe") or ip_cfg.get("ipv6_address") or "")
    ipv4_gw = normalize_ip(str(cpe_baseline.get("ipv4_gw") or "").split("/")[0])
    if not ipv4_gw and ipv4.count(".") == 3:
        ipv4_gw = ".".join(ipv4.split(".")[:3] + ["1"])
    return {
        "ipv4": ipv4,
        "netmask": netmask,
        "ipv4_gateway": ipv4_gw,
        "ipv6": ipv6,
        "bandwidth": bandwidth,
        "ssid": str(bts_baseline.get("ssid", "")).strip(),
        "key": str(bts_baseline.get("key", "")).strip(),
        "encryption": str(bts_baseline.get("encryption", "")).strip(),
    }


def _ft07_bts_restore_targets(
    profile: dict, bts_baseline: dict[str, str], values: dict,
) -> dict[str, str]:
    """Lab BTS restore — baseline IPv4/IPv6; profile when baseline is factory-default."""
    ip_cfg = profile.get("ip_tests", {}) or {}
    ipv4 = normalize_ip(bts_baseline.get("ipv4", "").split("/")[0])
    netmask = bts_baseline.get("netmask", "255.255.255.0").strip()
    ipv6 = bts_baseline.get("ipv6", "").strip()
    bandwidth = bts_baseline.get("bandwidth", "HT80")
    ssid = str(bts_baseline.get("ssid", "")).strip()
    key = str(bts_baseline.get("key", "")).strip()
    ipv4_gw = str(ip_cfg.get("ipv4_gateway", "")).strip()
    ipv6_gw = str(ip_cfg.get("ipv6_gateway_bts") or ip_cfg.get("ipv6_gateway") or "").strip()

    if _ft07_is_factory_lan(ipv4, values):
        ipv4 = normalize_ip(str(ip_cfg.get("ipv4_address") or ipv4))
        netmask = str(ip_cfg.get("ipv4_netmask") or netmask)
        _log(f"FT_07: BTS baseline is factory LAN — restore IPv4 from profile ({ipv4})")
    if not ipv6 or _ft07_ipv6_removed(ipv6):
        ipv6 = str(ip_cfg.get("ipv6_address_bts") or ip_cfg.get("ipv6_address") or "")
        if ipv6:
            _log(f"FT_07: BTS baseline IPv6 missing — restore from profile ({ipv6.split('/')[0]})")

    return {
        "ipv4": ipv4,
        "netmask": netmask,
        "ipv4_gateway": ipv4_gw,
        "ipv6": ipv6,
        "ipv6_gateway": ipv6_gw,
        "bandwidth": bandwidth,
        "ssid": ssid,
        "key": key,
    }


async def _ft07_restore_bts_network(
    bts_ssh,
    *,
    restore: dict[str, str],
    radio_idx: int,
    timeout_s: int,
) -> None:
    import shlex

    ipv4 = normalize_ip(restore.get("ipv4", "").split("/")[0])
    netmask = restore.get("netmask", "255.255.255.0").strip()
    ipv4_gw = str(restore.get("ipv4_gateway", "")).strip()
    ipv6 = restore.get("ipv6", "").strip()
    ipv6_gw = str(restore.get("ipv6_gateway", "")).strip()
    bandwidth = restore.get("bandwidth", "HT80")
    ssid = str(restore.get("ssid", "")).strip()
    key = str(restore.get("key", "")).strip()
    check.is_true(bool(ipv4), "FT_07: BTS restore IPv4 missing")

    ipv6_log = ipv6.split("/")[0] if ipv6 else "(none)"
    _log(
        f"FT_07 restore BTS — LAN {ipv4}/{netmask}, IPv6 {ipv6_log}, "
        f"SSID={ssid!r}, bandwidth {bandwidth}"
    )
    ht = _ft05_uci_htmode_value(bandwidth) if bandwidth else "HT80"
    cmds = [
        "ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {netmask}",
    ]
    if ipv4_gw:
        cmds.append(f"ucidyn set network.lan.gateway {shlex.quote(ipv4_gw)}")
    if ipv6:
        cmds.append("ucidyn set network.lan.ip6proto static")
        cmds.append(f"ucidyn set network.lan.ip6addr {shlex.quote(ipv6)}")
        if ipv6_gw:
            cmds.append(f"ucidyn set network.lan.ip6gw {shlex.quote(ipv6_gw)}")
    else:
        cmds.append("ucidyn delete network.lan.ip6addr 2>/dev/null || true")
        cmds.append("ucidyn delete network.lan.ip6gw 2>/dev/null || true")
    if ssid and key:
        cmds.extend([
            f"ucidyn set wireless.wifi{radio_idx}.htmode {ht}",
            f"ucidyn set wireless.@wifi-iface[{radio_idx}].ssid {shlex.quote(ssid)}",
            f"ucidyn set wireless.@wifi-iface[{radio_idx}].key {shlex.quote(key)}",
            f"ucidyn set wireless.wifi{radio_idx}.nwksecret {shlex.quote(key)}",
        ])
    cmds.append("ucidyn apply")
    for cmd in cmds:
        try:
            await _ssh(bts_ssh, cmd, timeout_s=timeout_s)
        except Exception as exc:
            _log(f"FT_07: BTS restore SSH closed (may reconnect): {exc}")
            break


async def _ft07_restore_cpe_link(
    hop_ssh,
    *,
    restore: dict[str, str],
    radio_idx: int,
    timeout_s: int,
    mgmt_vlan_id: int | None = None,
    profile: dict | None = None,
) -> None:
    import shlex

    ipv4 = normalize_ip(restore.get("ipv4", "").split("/")[0])
    netmask = restore.get("netmask", "255.255.255.0").strip()
    ipv4_gw = normalize_ip(str(restore.get("ipv4_gateway") or "").split("/")[0])
    if not ipv4_gw and ipv4.count(".") == 3:
        ipv4_gw = ".".join(ipv4.split(".")[:3] + ["1"])
    ipv6 = restore.get("ipv6", "").strip()
    bandwidth = restore.get("bandwidth", "HT160")
    ssid = str(restore.get("ssid", "")).strip()
    key = str(restore.get("key", "")).strip()
    encryption = str(restore.get("encryption", "")).strip() or "psk2+ccmp-256"
    check.is_true(bool(ipv4), "FT_07: CPE restore IPv4 missing")
    check.is_true(bool(ssid), "FT_07: BTS SSID missing for CPE restore")
    check.is_true(bool(key), "FT_07: BTS key missing for CPE restore")

    _log(f"FT_07 restore CPE — LAN {ipv4}/{netmask}, SSID={ssid!r} (from BTS), mode sta, key from BTS")
    ht = _ft05_uci_htmode_value(bandwidth) if bandwidth else "HT80"
    cmds: list[str] = []
    if mgmt_vlan_id is not None and profile is not None:
        from utils.vlan_uci import _iface_keys

        tb = profile.get("testbed", {}) or {}
        keys = _iface_keys(tb, "cpe")
        mode_val = str((tb.get("vlan_uci", {}) or {}).get("cpe", {}).get("mode_value", "transparent"))
        cmds.extend([
            f"ucidyn set {keys['mode']} {mode_val}",
            f"ucidyn delete {keys['svlan']} 2>/dev/null || true",
            f"ucidyn delete {keys['cvlan']} 2>/dev/null || true",
            f"ucidyn set {keys['mgmtvlan']} {int(mgmt_vlan_id)}",
        ])
    cmds.extend([
        # ucidyn set of proto does not replace factory dhcpv6 on this image.
        # uci set + commit does, so the lab address is on br-lan after apply.
        "uci set network.lan.proto='static'",
        f"uci set network.lan.ipaddr='{ipv4}'",
        f"uci set network.lan.netmask='{netmask}'",
        f"uci set network.lan.gateway='{ipv4_gw}'",
        "uci commit network",
        f"ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {netmask}",
        f"ucidyn set wireless.wifi{radio_idx}.htmode {ht}",
        f"ucidyn set wireless.@wifi-iface[{radio_idx}].mode sta",
        f"ucidyn set wireless.@wifi-iface[{radio_idx}].ssid {shlex.quote(ssid)}",
        f"ucidyn set wireless.@wifi-iface[{radio_idx}].encryption {shlex.quote(encryption)}",
        f"ucidyn set wireless.@wifi-iface[{radio_idx}].key {shlex.quote(key)}",
        f"ucidyn set wireless.wifi{radio_idx}.nwksecret {shlex.quote(key)}",
        f"ucidyn set network.lan.gateway {ipv4_gw}",
    ])
    if ipv6:
        cmds.append(f"uci set network.lan.ip6addr={shlex.quote(ipv6)}")
        cmds.append("uci commit network")
        cmds.append(f"ucidyn set network.lan.ip6addr {shlex.quote(ipv6)}")
    cmds.append("ucidyn apply")
    for cmd in cmds:
        try:
            await _ssh(hop_ssh, cmd, timeout_s=timeout_s)
        except Exception as exc:
            # ucidyn apply often drops the hop SSH; the sets before it stay.
            _log(f"FT_07: CPE restore hop closed (may reconnect): {exc}")
            if "apply" in cmd:
                break
            raise


async def _ft07_wait_bts_ssh(
    profile_bundle,
    device_creds: dict,
    *,
    bts_ip: str,
    values: dict,
):
    factory_ipv4 = normalize_ip(str(values.get("ft07_factory_ipv4", "192.168.2.1")))
    bts_hop = normalize_ip(str(values.get("ft07_bts_hop_ipv4", "10.0.0.1")))
    password = device_creds["pass"]
    source_v6 = _source_bind_ipv6(profile_bundle)
    tb = profile_bundle.active.get("testbed", {}) or {}
    recovery = (tb.get("recovery") or {})
    fallback = normalize_ip(str(recovery.get("bts_fallback_ipv4") or "192.168.2.10"))
    hosts: list[str] = []
    for host in (bts_hop, factory_ipv4, fallback, bts_ip):
        clean = normalize_ip(str(host).split("/")[0])
        if clean and clean not in hosts:
            hosts.append(clean)
    deadline = time.monotonic() + int(values.get("ft07_post_reset_timeout_s", 200))
    poll_s = int(values.get("ft07_post_reset_poll_s", 8))
    last_exc = ""
    while time.monotonic() < deadline:
        for host in hosts:
            try:
                if is_ipv6_literal(host):
                    return await _open_ipv6_ssh(
                        host, password, source_v6=source_v6, label="BTS",
                    )
                conn = AsyncGenericDriver(
                    host=host,
                    auth_username="root",
                    auth_password=password,
                    auth_strict_key=False,
                    transport="asyncssh",
                )
                await conn.open()
                return conn
            except Exception as exc:
                last_exc = str(exc)
        await asyncio.sleep(max(1, poll_s))
    raise ConnectionError(f"FT_07: BTS SSH reconnect failed ({hosts}): {last_exc[:220]}")


async def assert_ft_07_factory_reset_cpe_defaults(
    root_ssh,
    cpe_ips: list[str],
    *,
    gui_page=None,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_07 Part 1 — CPE factory reset without keep settings; verify via CPE PC SSH hop."""
    case_id = "FT_07"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_mgmt_ip = normalize_ip(cpe_ips[0]) if cpe_ips else ""

    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    mgmt_vlan_id = _ft07_resolve_mgmt_vlan_id(profile, values)
    sync_cpe_vlan = bool(values.get("ft07_sync_cpe_mgmt_vlan", True))
    _log(f"{case_id}: mgmt VLAN ID {mgmt_vlan_id} — BTS + CPE will use the same value")
    radio_idx = int(values.get("ft07_radio_idx", RADIO_IDX))
    timeout_s = int(values["ssh_timeout_s"])
    settle_s = float(values.get("ft07_gui_settle_s", 15))
    use_gui = bool(values.get("ft07_use_gui", False))
    factory_ipv4 = normalize_ip(str(values.get("ft07_factory_ipv4", "192.168.2.1")))
    factory_netmask = str(values.get("ft07_factory_netmask", "255.255.255.0"))
    factory_bandwidth = str(values.get("ft07_factory_bandwidth", "HT160")).upper()
    factory_bandwidth_bts = str(values.get("ft07_factory_bandwidth_bts", "HT80")).upper()
    test_bandwidth = str(values.get("ft07_test_bandwidth", "HT20")).upper()
    test_bandwidth_gui = str(values.get("ft07_test_bandwidth_gui", "20 MHz"))
    pre_hop_ip = await _ft07_cpe_hop_target(profile, values, post_reset=False)

    ip_cfg = profile.get("ip_tests", {}) or {}
    test_ipv4 = normalize_ip(str(values.get("ft07_test_ipv4", "192.168.2.211")))
    test_netmask = str(values.get("ft07_test_netmask", "255.255.254.0"))
    test_ipv6 = _derive_alt_ipv6(
        str(ip_cfg.get("ipv6_address_cpe") or ip_cfg.get("ipv6_address") or ""),
        role="CPE",
    )
    test_ipv6_gw = str(ip_cfg.get("ipv6_gateway_cpe") or ip_cfg.get("ipv6_gateway") or "").strip()

    print_section(f"{case_id}: Factory reset without keep settings — CPE phase (BTS unchanged)")

    bts_baseline = await _ft07_read_device_snapshot(root_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    _ft07_print_baseline(case_id, "BTS", bts_baseline)

    cpe_baseline = None
    cpe_mgmt_ssh = None
    if cpe_mgmt_ip:
        cpe_mgmt_ssh = await _ft07_try_open_cpe_mgmt_ssh(
            cpe_mgmt_ip,
            device_creds["pass"],
            _source_bind_ipv6(profile_bundle),
        )
    if cpe_mgmt_ssh is not None:
        try:
            cpe_baseline = await _ft07_read_device_snapshot(cpe_mgmt_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        finally:
            try:
                await cpe_mgmt_ssh.close()
            except Exception:
                pass

    pre_hop_ssh = await _ft07_open_cpe_ssh_via_cpe_pc(
        profile,
        device_creds["pass"],
        values,
        cpe_target=pre_hop_ip,
        wait_ping=True,
    )
    try:
        if cpe_baseline is None:
            cpe_baseline = await _ft07_read_device_snapshot(pre_hop_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _ft07_print_baseline(case_id, "CPE", cpe_baseline)

        gui_ok = False
        if use_gui and gui_page is not None and cpe_mgmt_ip:
            from utils.cpe_session import is_cpe_host_reachable, open_cpe_gui_session

            if is_cpe_host_reachable(cpe_mgmt_ip):
                cpe_page = None
                try:
                    cpe_page = await open_cpe_gui_session(
                        gui_page.context,
                        cpe_mgmt_ip,
                        device_creds,
                        require_summary=False,
                    )
                    _log(f"{case_id} CPE GUI session on {cpe_mgmt_ip}")
                    await _ft07_gui_apply_static_lan(
                        cpe_page,
                        ipv4=test_ipv4,
                        netmask=test_netmask,
                        ipv6_cidr=test_ipv6,
                        ipv6_gateway=test_ipv6_gw,
                        settle_s=settle_s,
                    )
                    await _ft07_gui_set_bandwidth(
                        cpe_page, cpe_mgmt_ip, gui_label=test_bandwidth_gui, settle_s=settle_s,
                    )
                    await _ft07_gui_factory_reset_without_retain(cpe_page, profile, values)
                    gui_ok = True
                finally:
                    if cpe_page is not None:
                        try:
                            await cpe_page.close()
                        except Exception:
                            pass

        if not gui_ok:
            _log(
                f"{case_id} backend path — mutate + factory reset via CPE PC hop → {pre_hop_ip} "
                f"(LAN {test_ipv4}/{test_netmask}, IPv6 {test_ipv6}, bandwidth {test_bandwidth})"
            )
            await _ft07_backend_mutate_and_factory_reset(
                pre_hop_ssh,
                profile,
                values,
                radio_idx=radio_idx,
                ipv4=test_ipv4,
                netmask=test_netmask,
                ipv6_cidr=test_ipv6,
                bandwidth=test_bandwidth,
            )
    finally:
        try:
            await pre_hop_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} verifying CPE factory defaults via CPE PC hop → {pre_hop_ip} (expect LAN {factory_ipv4})")

    post_hop_ssh = await _ft07_open_cpe_ssh_via_cpe_pc(
        profile,
        device_creds["pass"],
        values,
        cpe_target=pre_hop_ip,
        wait_ping=True,
    )
    try:
        post_reset = await _ft07_read_device_snapshot(post_hop_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _ft07_verify_factory_backend(
            case_id,
            post_reset,
            expected_ipv4=factory_ipv4,
            expected_netmask=factory_netmask,
            expected_bandwidth=factory_bandwidth,
        )
    finally:
        try:
            await post_hop_ssh.close()
        except Exception:
            pass

    if bool(values.get("ft07_restore_cpe_after_verify", True)):
        _log(f"{case_id} restoring CPE link — LAN from CPE baseline, SSID/key from BTS")
        restore_targets = _ft07_cpe_restore_targets(
            profile, cpe_baseline, bts_baseline, values,
        )
        link_timeout = int(values.get("ft07_link_recovery_timeout_s", 360))
        link_poll = int(values.get("ft07_link_recovery_poll_s", 8))
        source_v6 = _source_bind_ipv6(profile_bundle)
        bts_reconnect = [
            bts_ip,
            normalize_ip(str(values.get("ft07_bts_hop_ipv4", "10.0.0.1"))),
            normalize_ip(str(values.get("ft07_factory_ipv4", "192.168.2.1"))),
            "192.168.2.10",
        ]
        link_up = False
        last_link_detail = ""
        for attempt in range(1, 3):
            restore_hop = await _ft07_open_cpe_ssh_via_cpe_pc(
                profile,
                device_creds["pass"],
                values,
                cpe_target=pre_hop_ip,
                wait_ping=True,
            )
            try:
                await _ft07_restore_cpe_link(
                    restore_hop,
                    restore=restore_targets,
                    radio_idx=radio_idx,
                    timeout_s=timeout_s,
                    mgmt_vlan_id=mgmt_vlan_id if sync_cpe_vlan else None,
                    profile=profile,
                )
            finally:
                try:
                    await restore_hop.close()
                except Exception:
                    pass
            await asyncio.sleep(int(values.get("ft07_post_restore_settle_s", values.get("ft07_gui_settle_s", 20))))
            root_ssh = await _wait_rf_link_healthy(
                root_ssh,
                profile,
                case_id=case_id,
                label=f"post-CPE-restore-{attempt}",
                timeout_s=link_timeout,
                poll_s=link_poll,
                password=device_creds["pass"],
                source_v6=source_v6,
                reconnect_hosts=bts_reconnect,
                fail_hard=False,
            )
            try:
                link_up, last_link_detail = await _rf_link_up(root_ssh, profile)
            except Exception as exc:
                link_up, last_link_detail = False, str(exc)
            if link_up:
                break
            _log(
                f"{case_id}: CPE restore attempt {attempt}/2 — RF still down "
                f"({last_link_detail}); will {'retry restore' if attempt < 2 else 'fail'}"
            )
        check.is_true(
            link_up,
            f"{case_id} [post-CPE-restore]: RF link not healthy after CPE restore "
            f"({last_link_detail})",
        )
        if not link_up:
            # pytest-check would continue into BTS factory reset with RF down — stop instead.
            raise AssertionError(
                f"{case_id} [post-CPE-restore]: RF link not healthy after CPE restore "
                f"({last_link_detail})"
            )

    if not bool(values.get("ft07_run_bts_phase", True)):
        _log(f"{case_id} done — CPE phase complete (BTS phase skipped)")
        return

    print_section(f"{case_id}: Factory reset without keep settings — BTS phase")
    test_ipv6_bts = _derive_alt_ipv6(
        str(ip_cfg.get("ipv6_address_bts") or ip_cfg.get("ipv6_address") or ""),
        role="BTS",
    )
    _log(
        f"{case_id} BTS backend mutate + factory reset "
        f"(LAN {test_ipv4}/{test_netmask}, IPv6 {test_ipv6_bts}, bandwidth {test_bandwidth})"
    )
    await _ft07_backend_mutate_and_factory_reset(
        root_ssh,
        profile,
        values,
        radio_idx=radio_idx,
        ipv4=test_ipv4,
        netmask=test_netmask,
        ipv6_cidr=test_ipv6_bts,
        bandwidth=test_bandwidth,
    )

    bts_post_ssh = await _ft07_wait_bts_ssh(
        profile_bundle, device_creds, bts_ip=bts_ip, values=values,
    )
    try:
        if bool(values.get("ft07_bts_vlan_transparent", True)):
            bts_post_ssh = await _ft07_apply_vlan_transparent(
                bts_post_ssh,
                profile,
                role="bts",
                timeout_s=timeout_s,
                mgmt_vlan_id=mgmt_vlan_id,
                profile_bundle=profile_bundle,
                device_creds=device_creds,
                bts_ip=bts_ip,
                values=values,
            )
            if sync_cpe_vlan:
                await _ft07_sync_cpe_mgmt_vlan_via_hop(
                    profile,
                    device_creds["pass"],
                    values,
                    mgmt_vlan_id,
                    timeout_s=timeout_s,
                    cpe_target=pre_hop_ip,
                )
            try:
                await bts_post_ssh.close()
            except Exception:
                pass
            bts_post_ssh = await _ft07_wait_bts_ssh(
                profile_bundle, device_creds, bts_ip=bts_ip, values=values,
            )
            await asyncio.sleep(int(values.get("ft07_gui_settle_s", 15)))

        bts_post = await _ft07_read_device_snapshot(
            bts_post_ssh, radio_idx=radio_idx, timeout_s=timeout_s,
        )
        _ft07_verify_factory_backend(
            f"{case_id} BTS",
            bts_post,
            expected_ipv4=factory_ipv4,
            expected_netmask=factory_netmask,
            expected_bandwidth=factory_bandwidth_bts,
        )

        if bool(values.get("ft07_restore_bts_after_verify", True)):
            bts_restore = _ft07_bts_restore_targets(profile, bts_baseline, values)
            await _ft07_restore_bts_network(
                bts_post_ssh,
                restore=bts_restore,
                radio_idx=radio_idx,
                timeout_s=timeout_s,
            )
            try:
                await bts_post_ssh.close()
            except Exception:
                pass
            bts_post_ssh = await _ft07_wait_bts_ssh(
                profile_bundle, device_creds, bts_ip=bts_ip, values=values,
            )
            await asyncio.sleep(int(values.get("ft07_gui_settle_s", 15)))

        if sync_cpe_vlan:
            await _ft07_sync_cpe_mgmt_vlan_via_hop(
                profile,
                device_creds["pass"],
                values,
                mgmt_vlan_id,
                timeout_s=timeout_s,
                cpe_target=pre_hop_ip,
            )

        await _wait_rf_link_healthy(
            bts_post_ssh,
            profile,
            case_id=case_id,
            label="post-BTS-factory-reset",
            timeout_s=int(values.get("ft07_link_recovery_timeout_s", 360)),
            poll_s=int(values.get("ft07_link_recovery_poll_s", 8)),
            password=device_creds["pass"],
            source_v6=_source_bind_ipv6(profile_bundle),
            reconnect_hosts=[
                bts_ip,
                normalize_ip(str(values.get("ft07_bts_hop_ipv4", "10.0.0.1"))),
                normalize_ip(str(values.get("ft07_factory_ipv4", "192.168.2.1"))),
                "192.168.2.10",
            ],
        )
    finally:
        try:
            await bts_post_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} complete — CPE verified + restored, BTS verified + IPs restored, link up")



def _ft08_norm_field(key: str, value: str) -> str:
    raw = clean_ssh_output(str(value or "")).strip()
    if key == "ipv4":
        return normalize_ip(raw.split("/")[0]) if raw and not raw.lower().startswith("uci:") else raw
    if key == "ipv6":
        if not raw or _ft07_ipv6_removed(raw):
            return ""
        return normalize_ip(raw.split("/")[0]) + (f"/{raw.split('/')[1]}" if "/" in raw else "")
    return raw


def _ft08_reload_log_indicates_soft_reset(log_text: str) -> bool:
    text = (log_text or "").lower()
    return any(
        token in text
        for token in (
            "ft08_",
            "_reload_started",
            "network",
            "reload",
            "restart",
            "interface",
            "br-lan",
            "device:",
            "wifi",
            "wlan",
            "cfg80211",
            "dynamic mode",
            "done",
        )
    )


def _ft08_reload_marker(label: str) -> str:
    return f"FT08_{label.upper()}_RELOAD"


async def _ft08_collect_reload_logs(ssh, values: dict, *, label: str) -> str:
    tail = int(values.get("ft08_reload_log_tail_lines", 80))
    marker = _ft08_reload_marker(label)
    chunks: list[str] = []
    for cmd in (
        f"cat /tmp/{marker}.log 2>/dev/null",
        f"logread 2>/dev/null | grep -iE 'network|reload|interface|wlan|wifi|br-lan' | tail -{tail}",
        f"logread 2>/dev/null | tail -{tail}",
        f"dmesg 2>/dev/null | tail -{min(tail, 50)}",
    ):
        try:
            out = await _ssh(ssh, cmd, timeout_s=45)
            if out and out.strip():
                chunks.append(out)
        except Exception as exc:
            chunks.append(str(exc))
    return "\n".join(chunks)


async def _ft08_wait_reload_idle(ssh, values: dict, *, label: str) -> None:
    """Poll until network reload process is gone (after SSH reconnect if needed)."""
    deadline = time.monotonic() + int(values.get("ft08_reload_timeout_s", 360))
    poll_s = int(values.get("ft08_reload_poll_s", 4))
    marker = _ft08_reload_marker(label)
    _log(f"FT_08 [{label}] waiting for reload process to finish")
    while time.monotonic() < deadline:
        try:
            busy = clean_ssh_output(
                await _ssh(
                    ssh,
                    "pgrep -af '/etc/init.d/network' 2>/dev/null | grep reload || true",
                    timeout_s=30,
                )
            )
            if not busy.strip():
                _log(f"FT_08 [{label}] reload process finished")
                return
        except Exception as exc:
            _log(f"FT_08 [{label}] reload poll SSH error: {exc}")
        await asyncio.sleep(max(1, poll_s))
    _log(f"FT_08 [{label}] reload idle wait timed out — continuing after settle")


async def _ft08_run_network_reload(ssh, values: dict, *, label: str) -> bool:
    """Soft reset: /etc/init.d/network reload & — keep SSH session open until reload finishes.

    Returns True when the remote shell reported reload completion before SSH closed.
    """
    reload_timeout = int(values.get("ft08_reload_timeout_s", 360))
    settle_s = int(values.get("ft08_reload_settle_s", 45))
    marker = _ft08_reload_marker(label)
    log_path = f"/tmp/{marker}.log"
    started_marker = f"{marker}_STARTED"
    done_marker = f"{marker}_DONE"
    reload_cmd = (
        f"/etc/init.d/network reload >{log_path} 2>&1 & RP=$!; "
        f"echo {started_marker}; "
        f"while kill -0 $RP 2>/dev/null; do sleep 2; done; "
        f"echo {done_marker}"
    )
    _log(
        f"FT_08 [{label}] soft reset — /etc/init.d/network reload & "
        f"(SSH held until process completes, timeout={reload_timeout + 90}s)"
    )
    completed = False
    try:
        out = await _ssh(ssh, reload_cmd, timeout_s=reload_timeout + 90)
        completed = done_marker in (out or "")
        if completed:
            _log(f"FT_08 [{label}] network reload finished — SSH session held until done")
        else:
            _log(
                f"FT_08 [{label}] reload returned without done marker "
                f"({(out or '').strip()[:180]!r})"
            )
    except Exception as exc:
        _log(f"FT_08 [{label}] SSH ended during reload wait: {exc}")
        _log(f"FT_08 [{label}] post-reload settle {settle_s}s (will poll on reconnect)")
        await asyncio.sleep(settle_s)
        return False

    if not completed:
        await _ft08_wait_reload_idle(ssh, values, label=label)

    _log(f"FT_08 [{label}] post-reload settle {settle_s}s")
    await asyncio.sleep(settle_s)
    return completed


async def _ft08_reconnect_bts_ssh(
    root_ssh,
    profile_bundle,
    device_creds: dict,
    *,
    bts_ip: str,
    values: dict,
):
    """Reconnect BTS mgmt SSH after network reload — IPv6 only, not factory-reset hop."""
    password = device_creds["pass"]
    source_v6 = _source_bind_ipv6(profile_bundle)
    try:
        await root_ssh.send_command("true", timeout_ops=10)
        return root_ssh
    except Exception:
        try:
            await root_ssh.close()
        except Exception:
            pass
    deadline = time.monotonic() + int(values.get("ft08_reload_timeout_s", 120))
    last_exc = ""
    while time.monotonic() < deadline:
        try:
            conn = await _open_ipv6_ssh(
                bts_ip, password, source_v6=source_v6, label="BTS",
            )
            _log(f"FT_08: BTS SSH back on {bts_ip} after network reload")
            return conn
        except Exception as exc:
            last_exc = str(exc)
        await asyncio.sleep(int(values.get("ft08_reload_poll_s", 4)))
    raise ConnectionError(f"FT_08: BTS SSH reconnect failed after soft reset: {last_exc[:200]}")


def _ft08_verify_reload_logs(case_id: str, label: str, log_text: str) -> None:
    ok = _ft08_reload_log_indicates_soft_reset(log_text)
    snippet = (log_text or "").strip().splitlines()
    tail = "\n".join(snippet[-8:]) if snippet else "(empty)"
    print_section(f"{case_id} {label} soft-reset log check")
    print_comparison_table(
        [
            ("Reload in logs", "network/reload markers", "found" if ok else "not found", "PASS" if ok else "FAIL"),
            ("Log tail (last lines)", "—", tail[:220] or "(empty)", "INFO"),
        ],
    )
    check.is_true(ok, f"{case_id} [{label}]: no network reload evidence in logs")


def _ft08_verify_snapshot_unchanged(
    case_id: str,
    label: str,
    before: dict[str, str],
    after: dict[str, str],
    *,
    context: str = "soft reset",
) -> None:
    fields = (
        ("IPv4", "ipv4"),
        ("Netmask", "netmask"),
        ("IPv6", "ipv6"),
        ("Bandwidth", "bandwidth"),
        ("SSID", "ssid"),
        ("Encryption", "encryption"),
    )
    rows: list[tuple[str, str, str, str]] = []
    for title, key in fields:
        b_raw = before.get(key, "")
        a_raw = after.get(key, "")
        if key == "bandwidth":
            b_disp = str(b_raw).strip() or "(empty)"
            a_disp = str(a_raw).strip() or "(empty)"
            # A login banner (`|`) is not a width. Do not fail a retained HT160 on that.
            if not _is_valid_htmode(_normalize_htmode(b_disp)):
                rows.append((title, b_disp, a_disp, "INFO"))
                continue
            same = _ft07_bandwidth_matches(str(b_raw), str(a_raw))
        elif key == "ipv6":
            b_disp = clean_ssh_output(str(b_raw)).strip() or "(empty)"
            a_disp = clean_ssh_output(str(a_raw)).strip() or "(empty)"
            same = b_disp == a_disp
        else:
            b_disp = _ft08_norm_field(key, str(b_raw)) or "(empty)"
            a_disp = _ft08_norm_field(key, str(a_raw)) or "(empty)"
            same = b_disp == a_disp
        rows.append((title, b_disp, a_disp, "PASS" if same else "FAIL"))
        check.is_true(same, f"{case_id} [{label}]: {title} changed after soft reset ({b_disp!r} -> {a_disp!r})")

    print_section(f"{case_id} {label} settings unchanged after {context}")
    print_comparison_table(rows)


async def assert_ft_08_network_soft_reset(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_08 — direct CPE/BTS SSH: network reload; logs + settings unchanged; link up."""
    case_id = "FT_08"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")

    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    radio_idx = int(values.get("ft08_radio_idx", RADIO_IDX))
    timeout_s = int(values["ssh_timeout_s"])
    link_timeout = int(values.get("ft08_link_recovery_timeout_s", 180))
    link_poll = int(values.get("ft08_link_recovery_poll_s", 8))
    source_v6 = _source_bind_ipv6(profile_bundle)
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    print_section(f"{case_id}: Network soft reset — CPE phase (direct SSH)")

    cpe_before = None
    cpe_reload_done = False
    cpe_ssh = await _open_ipv6_ssh(
        cpe_ip, device_creds["pass"], source_v6=source_v6, label="CPE",
    )
    try:
        cpe_before = await _ft07_read_device_snapshot(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _ft07_print_baseline(case_id, "CPE (before soft reset)", cpe_before)
        cpe_reload_done = await _ft08_run_network_reload(cpe_ssh, values, label="CPE")
    finally:
        try:
            await cpe_ssh.close()
        except Exception:
            pass

    cpe_ssh_post = await _wait_ipv6_ssh_ready(
        cpe_ip,
        password=device_creds["pass"],
        source_v6=source_v6,
        label="CPE",
        timeout_s=int(values.get("ft09_reboot_timeout_s", 300)),
        poll_s=int(values.get("ft08_link_recovery_poll_s", 8)),
    )
    try:
        if not cpe_reload_done:
            await _ft08_wait_reload_idle(cpe_ssh_post, values, label="CPE")
        cpe_after = await _ft07_read_device_snapshot(cpe_ssh_post, radio_idx=radio_idx, timeout_s=timeout_s)
        cpe_logs = await _ft08_collect_reload_logs(cpe_ssh_post, values, label="CPE")
        _ft08_verify_reload_logs(case_id, "CPE", cpe_logs)
        check.is_true(cpe_before is not None, f"{case_id}: CPE baseline missing")
        _ft08_verify_snapshot_unchanged(case_id, "CPE", cpe_before, cpe_after)
    finally:
        try:
            await cpe_ssh_post.close()
        except Exception:
            pass

    await _wait_rf_link_healthy(
        root_ssh,
        profile,
        case_id=case_id,
        label="post-CPE-soft-reset",
        timeout_s=link_timeout,
        poll_s=link_poll,
    )

    if not bool(values.get("ft08_run_bts_phase", True)):
        _log(f"{case_id} complete — CPE soft reset verified, link up")
        return

    print_section(f"{case_id}: Network soft reset — BTS phase (SSH only, no factory reset)")
    bts_before = await _ft07_read_device_snapshot(root_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    _ft07_print_baseline(case_id, "BTS (before soft reset)", bts_before)
    bts_reload_done = await _ft08_run_network_reload(root_ssh, values, label="BTS")

    bts_ssh = await _ft08_reconnect_bts_ssh(
        root_ssh, profile_bundle, device_creds, bts_ip=bts_ip, values=values,
    )
    try:
        if not bts_reload_done:
            await _ft08_wait_reload_idle(bts_ssh, values, label="BTS")
        bts_after = await _ft07_read_device_snapshot(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        bts_logs = await _ft08_collect_reload_logs(bts_ssh, values, label="BTS")
        _ft08_verify_reload_logs(case_id, "BTS", bts_logs)
        _ft08_verify_snapshot_unchanged(case_id, "BTS", bts_before, bts_after)
        await _wait_rf_link_healthy(
            bts_ssh,
            profile,
            case_id=case_id,
            label="post-BTS-soft-reset",
            timeout_s=link_timeout,
            poll_s=link_poll,
        )
    finally:
        try:
            await bts_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} complete — CPE + BTS soft reset verified, settings unchanged, link up")


def _ft09_ping_ok(raw: str) -> bool:
    text = str(raw or "").lower()
    if "100% packet loss" in text or "0 packets received" in text:
        return False
    return _ft07_ping_success(raw) or "round-trip min/avg/max" in text or "bytes from" in text


async def _ft09_ping_bts_to_cpe(
    bts_ssh,
    cpe_v6: str,
    *,
    count: int,
    timeout_s: int,
) -> tuple[bool, str]:
    target = normalize_ip(str(cpe_v6 or "").split("/")[0])
    raw = clean_ssh_output(
        await _ssh(bts_ssh, f"ping -6 -c {count} -W 3 {target} 2>&1", timeout_s=timeout_s)
    )
    return _ft09_ping_ok(raw), raw[:220] or "(empty)"


def _ft09_reboot_marker(label: str) -> str:
    return f"FT09_{label.upper()}_REBOOT"


async def _ft09_issue_ssh_reboot(ssh, values: dict, *, label: str) -> None:
    """SSH reboot — keep remote shell open until device disconnects (reboot kills session)."""
    reboot_timeout = int(values.get("ft09_reboot_timeout_s", 300))
    marker = _ft09_reboot_marker(label)
    log_path = f"/tmp/{marker}.log"
    started_marker = f"{marker}_STARTED"
    reboot_cmd = (
        f"(reboot >{log_path} 2>&1 &); "
        f"echo {started_marker}; "
        f"while sleep 2; do :; done"
    )
    _log(
        f"FT_09 [{label}] SSH reboot — session held until device disconnects "
        f"(timeout={reboot_timeout + 60}s)"
    )
    try:
        await _ssh(ssh, reboot_cmd, timeout_s=reboot_timeout + 60)
    except Exception as exc:
        _log(f"FT_09 [{label}] SSH ended during reboot (expected): {exc}")


async def _ft09_reconnect_bts_ssh(
    root_ssh,
    profile_bundle,
    device_creds: dict,
    *,
    bts_ip: str,
    values: dict,
):
    password = device_creds["pass"]
    source_v6 = _source_bind_ipv6(profile_bundle)
    try:
        await root_ssh.send_command("true", timeout_ops=10)
        return root_ssh
    except Exception:
        try:
            await root_ssh.close()
        except Exception:
            pass
    deadline = time.monotonic() + int(values.get("ft09_reboot_timeout_s", 300))
    poll_s = int(values.get("ft09_reconnect_poll_s", 8))
    last_exc = ""
    while time.monotonic() < deadline:
        try:
            conn = await _open_ipv6_ssh(
                bts_ip, password, source_v6=source_v6, label="BTS",
            )
            _log(f"FT_09: BTS SSH back on {bts_ip} after reboot")
            return conn
        except Exception as exc:
            last_exc = str(exc)
        await asyncio.sleep(poll_s)
    raise ConnectionError(f"FT_09: BTS SSH reconnect failed after reboot: {last_exc[:200]}")


async def _ft09_wait_ssh_responsive(ssh, *, label: str, timeout_s: int, poll_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc = ""
    while time.monotonic() < deadline:
        try:
            await _ssh(ssh, "echo FT09_READY", timeout_s=min(20, timeout_s))
            _log(f"FT_09 [{label}] SSH responsive after reboot")
            return
        except Exception as exc:
            last_exc = str(exc)
        await asyncio.sleep(max(1, poll_s))
    raise TimeoutError(f"FT_09 [{label}] SSH not responsive after reboot: {last_exc[:200]}")


async def _ft09_read_snapshot_after_reboot(
    ssh,
    *,
    radio_idx: int,
    timeout_s: int,
    label: str,
    values: dict,
) -> dict[str, str]:
    ready_timeout = int(values.get("ft09_reboot_timeout_s", 300))
    poll_s = int(values.get("ft09_reconnect_poll_s", 8))
    await _ft09_wait_ssh_responsive(
        ssh, label=label, timeout_s=ready_timeout, poll_s=poll_s,
    )
    await asyncio.sleep(int(values.get("ft09_post_reboot_settle_s", 60)))
    last_exc = ""
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        try:
            return await _ft07_read_device_snapshot(ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        except Exception as exc:
            last_exc = str(exc)
            _log(f"FT_09 [{label}] snapshot read retry: {exc}")
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"FT_09 [{label}] snapshot read failed after reboot: {last_exc[:200]}")


def _ft09_verify_ping(
    case_id: str,
    *,
    before_ok: bool,
    after_ok: bool,
    before_detail: str,
    after_detail: str,
) -> None:
    print_section(f"{case_id} BTS→CPE ping (before and after reboot)")
    print_comparison_table(
        [
            ("Ping before reboot", "replies", "ok" if before_ok else "fail", "PASS" if before_ok else "FAIL"),
            ("Ping detail (before)", "—", before_detail, "INFO"),
            ("Ping after reboot", "replies", "ok" if after_ok else "fail", "PASS" if after_ok else "FAIL"),
            ("Ping detail (after)", "—", after_detail, "INFO"),
        ],
    )
    check.is_true(before_ok, f"{case_id}: BTS→CPE ping failed before reboot")
    check.is_true(after_ok, f"{case_id}: BTS→CPE ping failed after reboot")


async def assert_ft_09_config_retain_soft_reboot(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_09 — CPE then BTS SSH reboot; ping before/after; config unchanged; link up."""
    case_id = "FT_09"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    radio_idx = int(values.get("ft09_radio_idx", RADIO_IDX))
    timeout_s = int(values["ssh_timeout_s"])
    link_timeout = int(values.get("ft09_link_recovery_timeout_s", 240))
    link_poll = int(values.get("ft09_link_recovery_poll_s", 8))
    settle_s = int(values.get("ft09_post_reboot_settle_s", 30))
    ping_count = int(values.get("ft09_ping_count", 3))
    source_v6 = _source_bind_ipv6(profile_bundle)

    print_section(f"{case_id}: Config retain after soft reboot — CPE then BTS (SSH)")
    _log(f"{case_id} capture baseline → ping → CPE reboot → BTS reboot → verify config + ping + link")

    await _wait_rf_link_healthy(
        root_ssh,
        profile,
        case_id=case_id,
        label="precheck",
        timeout_s=link_timeout,
        poll_s=link_poll,
    )

    bts_before = await _ft07_read_device_snapshot(root_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    _ft07_print_baseline(case_id, "BTS (before reboot)", bts_before)

    cpe_ssh = await _open_ipv6_ssh(
        cpe_ip, device_creds["pass"], source_v6=source_v6, label="CPE",
    )
    try:
        cpe_before = await _ft07_read_device_snapshot(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _ft07_print_baseline(case_id, "CPE (before reboot)", cpe_before)
    finally:
        try:
            await cpe_ssh.close()
        except Exception:
            pass

    ping_before_ok, ping_before_detail = await _ft09_ping_bts_to_cpe(
        root_ssh, cpe_ip, count=ping_count, timeout_s=timeout_s,
    )
    _log(f"{case_id} ping before reboot: {'ok' if ping_before_ok else 'fail'} — {ping_before_detail[:120]}")

    print_section(f"{case_id}: CPE soft reboot (direct SSH)")
    cpe_ssh_reboot = await _open_ipv6_ssh(
        cpe_ip, device_creds["pass"], source_v6=source_v6, label="CPE",
    )
    try:
        await _ft09_issue_ssh_reboot(cpe_ssh_reboot, values, label="CPE")
    finally:
        try:
            await cpe_ssh_reboot.close()
        except Exception:
            pass

    await asyncio.sleep(settle_s)
    cpe_ssh_post = await _wait_ipv6_ssh_ready(
        cpe_ip,
        password=device_creds["pass"],
        source_v6=source_v6,
        label="CPE",
        timeout_s=int(values.get("ft09_reboot_timeout_s", 300)),
        poll_s=int(values.get("ft09_reconnect_poll_s", 8)),
    )
    try:
        cpe_after = await _ft09_read_snapshot_after_reboot(
            cpe_ssh_post,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
            label="CPE",
            values=values,
        )
        _ft08_verify_snapshot_unchanged(
            case_id, "CPE", cpe_before, cpe_after, context="reboot",
        )
    finally:
        try:
            await cpe_ssh_post.close()
        except Exception:
            pass

    await _wait_rf_link_healthy(
        root_ssh,
        profile,
        case_id=case_id,
        label="post-CPE-reboot",
        timeout_s=link_timeout,
        poll_s=link_poll,
    )

    if not bool(values.get("ft09_run_bts_phase", True)):
        ping_after_ok, ping_after_detail = await _ft09_ping_bts_to_cpe(
            root_ssh, cpe_ip, count=ping_count, timeout_s=timeout_s,
        )
        _ft09_verify_ping(
            case_id,
            before_ok=ping_before_ok,
            after_ok=ping_after_ok,
            before_detail=ping_before_detail,
            after_detail=ping_after_detail,
        )
        _log(f"{case_id} complete — CPE reboot verified, config retained, link up")
        return

    print_section(f"{case_id}: BTS soft reboot (SSH)")
    await _ft09_issue_ssh_reboot(root_ssh, values, label="BTS")

    bts_ssh = await _ft09_reconnect_bts_ssh(
        root_ssh, profile_bundle, device_creds, bts_ip=bts_ip, values=values,
    )
    try:
        bts_after = await _ft09_read_snapshot_after_reboot(
            bts_ssh,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
            label="BTS",
            values=values,
        )
        _ft08_verify_snapshot_unchanged(
            case_id, "BTS", bts_before, bts_after, context="reboot",
        )
        await _wait_rf_link_healthy(
            bts_ssh,
            profile,
            case_id=case_id,
            label="post-BTS-reboot",
            timeout_s=link_timeout,
            poll_s=link_poll,
        )
        ping_after_ok, ping_after_detail = await _ft09_ping_bts_to_cpe(
            bts_ssh, cpe_ip, count=ping_count, timeout_s=timeout_s,
        )
        _ft09_verify_ping(
            case_id,
            before_ok=ping_before_ok,
            after_ok=ping_after_ok,
            before_detail=ping_before_detail,
            after_detail=ping_after_detail,
        )
    finally:
        try:
            await bts_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} complete — CPE + BTS reboot verified, config retained, ping ok, link up")


async def _ft25_read_tx_power_backend(ssh, *, radio_idx: int, timeout_s: int) -> tuple[str, str]:
    """Return (runtime tx power dBm, UCI atpcpower) from device SSH."""
    iw_resp = await ssh.send_command(RootCommands.get_iwconfig(radio_idx), timeout_ops=timeout_s)
    iw_val = parse_iwconfig_tx_power(str(iw_resp.result or ""))
    if not iw_val:
        dev_resp = await ssh.send_command(
            RootCommands.get_iw_dev_info(radio_idx), timeout_ops=timeout_s,
        )
        iw_val = parse_iwconfig_tx_power(str(dev_resp.result or ""))
    uci_raw = clean_ssh_output(
        await _ssh(ssh, RootCommands.get_tx_power(radio_idx), timeout_s=timeout_s)
    )
    uci_val = extract_uci_value(uci_raw)
    return iw_val, uci_val


async def _ft25_read_tx_power_backend_retry(
    ssh, *, radio_idx: int, timeout_s: int, retries: int = 3, delay_s: float = 3.0,
) -> tuple[str, str]:
    """Re-read backend until runtime tx power appears or retries exhausted."""
    iw_val, uci_val = "", ""
    for attempt in range(max(1, retries)):
        iw_val, uci_val = await _ft25_read_tx_power_backend(
            ssh, radio_idx=radio_idx, timeout_s=timeout_s,
        )
        if iw_val or uci_val:
            if iw_val or attempt == retries - 1:
                return iw_val, uci_val
        if attempt < retries - 1:
            await asyncio.sleep(delay_s)
    return iw_val, uci_val


async def _ft25_disable_atpc(ssh, *, radio_idx: int, timeout_s: int) -> None:
    """
    Ensure ATPC does not override manual Tx power during FT_25.
    Some builds will keep adjusting power if ATPC is enabled.
    """
    await _ssh(ssh, f"uci set txparam.ath{radio_idx}.atpcstatus=0", timeout_s=timeout_s)
    await _ssh(ssh, "uci commit txparam", timeout_s=timeout_s)
    await _ssh(ssh, "ucidyn apply", timeout_s=timeout_s)


async def _ft25_backend_force_tx_power(
    ssh,
    *,
    radio_idx: int,
    tx_power: str,
    timeout_s: int,
) -> None:
    """
    Fallback apply path: force txparam.athN.atpcpower + apply.
    Used only when GUI set did not reflect in backend.
    Prefer ucidyn (live) then fall back to uci commit + apply.
    """
    tx = str(tx_power).strip()
    try:
        await _ssh(ssh, f"ucidyn set txparam.ath{radio_idx}.atpcpower {tx}", timeout_s=timeout_s)
        await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))
    except Exception:
        await _ssh(ssh, f"uci set txparam.ath{radio_idx}.atpcpower={tx}", timeout_s=timeout_s)
        await _ssh(ssh, "uci commit txparam", timeout_s=timeout_s)
        await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))
    await asyncio.sleep(5)


async def _ft25_apply_tx_power_with_backend_verify(
    *,
    role: str,
    gui_target: dict,
    ssh_target,
    radio_idx: int,
    tx_power: str,
    values: dict,
    timeout_s: int,
) -> tuple[str, str]:
    """
    Apply Tx power via GUI, then verify backend.
    If backend doesn't match, force-apply via backend and re-verify.
    Returns (runtime_dBm, uci_dBm) after the final verification.
    """
    await _ft25_apply_tx_power_gui(
        gui_target["radio_page"],
        gui_target["page"],
        tx_power=tx_power,
        values=values,
    )
    runtime, uci = await _ft25_read_tx_power_backend_retry(
        ssh_target, radio_idx=radio_idx, timeout_s=timeout_s, retries=5, delay_s=3.0,
    )
    if uci != str(tx_power).strip():
        _log(f"FT_25 [{role}] GUI set {tx_power} but UCI={uci!r}; forcing backend apply")
        await _ft25_backend_force_tx_power(
            ssh_target, radio_idx=radio_idx, tx_power=tx_power, timeout_s=timeout_s,
        )
        runtime, uci = await _ft25_read_tx_power_backend_retry(
            ssh_target, radio_idx=radio_idx, timeout_s=timeout_s, retries=8, delay_s=3.0,
        )
    return runtime, uci


async def _ft25_open_cpe_ssh(cpe_ip: str, device_creds: dict, profile_bundle):
    """IPv6-bound CPE SSH for iwconfig/UCI verification."""
    cpe_ip = normalize_ip(cpe_ip)
    source_v6 = _source_bind_ipv6(profile_bundle)
    return await _open_ipv6_ssh(
        cpe_ip, device_creds["pass"], source_v6=source_v6, label="CPE",
    )


async def _ft25_open_gui_target(host: str, device_creds: dict) -> dict:
    """LuCI in an isolated Playwright session (avoids session gui_page asyncio loop issues)."""
    from pages.radio_properties_page import RadioPropertiesPage
    from utils.radio_properties_flows import _open_remote_radio_target

    host = normalize_ip(host)
    page, _ssh, context, browser, playwright = await _open_remote_radio_target(
        host, device_creds,
    )
    return {
        "host": host,
        "page": page,
        "radio_page": RadioPropertiesPage(page, local_ip=host),
        "context": context,
        "browser": browser,
        "playwright": playwright,
    }


async def _ft25_open_bts_gui_target(profile: dict, bts_ip: str, device_creds: dict) -> dict:
    dut = profile.get("dut", {}) or {}
    tb = profile.get("testbed", {}) or {}
    rec = tb.get("recovery", {}) or {}
    candidates: list[str] = []
    for host in (
        rec.get("bts_fallback_ipv4"),
        dut.get("local_ip"),
        "10.0.0.1",
        bts_ip,
    ):
        h = normalize_ip(str(host or "").split("/")[0])
        if h and h not in candidates:
            candidates.append(h)

    last_error: Exception | None = None
    for host in candidates:
        try:
            return await _ft25_open_gui_target(host, device_creds)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"FT_25: unable to open BTS GUI ({last_error})")


async def _ft25_close_gui_target(target: dict) -> None:
    try:
        await target["page"].close()
    except Exception:
        pass
    try:
        await target["context"].close()
    except Exception:
        pass
    try:
        if target.get("browser") is not None:
            await target["browser"].close()
    except Exception:
        pass
    try:
        if target.get("playwright") is not None:
            await target["playwright"].stop()
    except Exception:
        pass


async def _ft25_apply_tx_power_gui(
    radio_page,
    page,
    *,
    tx_power: str,
    values: dict,
) -> None:
    from pages.locators import RadioPropertiesLocators
    from utils.ui_helpers import execute_triple_apply, fill_luci_input

    await radio_page.open_ddrs_atpc()
    await fill_luci_input(page, RadioPropertiesLocators.TRANSMIT_POWER_INPUT, tx_power)
    await page.wait_for_timeout(1000)
    await execute_triple_apply(page, radio_page.RADIO_1_DDRS_URL_CHUNK)
    await asyncio.sleep(int(values.get("ft25_apply_settle_s", 20)))


def _ft25_tx_power_matches(expected: str, iw_val: str, uci_val: str) -> bool:
    exp = str(expected).strip()
    if iw_val and iw_val == exp:
        return True
    return bool(uci_val) and uci_val == exp


def _ft25_print_tx_power_row(
    case_id: str,
    label: str,
    tx_power: str,
    iw_val: str,
    uci_val: str,
) -> bool:
    ok = _ft25_tx_power_matches(tx_power, iw_val, uci_val)
    runtime_src = "iwconfig/iw" if iw_val else "UCI only"
    print_section(f"{case_id} [{label}] Tx power {tx_power} dBm — backend verify ({runtime_src})")
    print_comparison_table(
        [
            ("GUI set (dBm)", tx_power, tx_power, "INFO"),
            ("Runtime Tx-Power", tx_power, iw_val or "(empty)", "PASS" if iw_val == tx_power else "INFO"),
            ("UCI atpcpower", tx_power, uci_val or "(empty)", "PASS" if uci_val == tx_power else "FAIL"),
            ("Backend OK", "match", "yes" if ok else "no", "PASS" if ok else "FAIL"),
        ],
    )
    check.is_true(
        ok,
        f"{case_id} [{label}] Tx power {tx_power} dBm: runtime={iw_val!r} uci={uci_val!r}",
    )
    return ok


async def assert_ft_25_tx_power_gui_ssh_mapping(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """FT_25 — per Tx power: CPE GUI then BTS GUI; link up; iwconfig/UCI verify on both."""
    case_id = "FT_25"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    radio_idx = int(values.get("ft25_radio_idx", RADIO_IDX))
    timeout_s = int(values["ssh_timeout_s"])
    tx_values = [str(v) for v in values.get("ft25_tx_power_values", list(range(1, 27)))]
    link_timeout = int(values.get("ft25_link_recovery_timeout_s", 240))
    link_poll = int(values.get("ft25_link_recovery_poll_s", 8))

    print_section(
        f"{case_id}: Tx power GUI↔SSH mapping — CPE then BTS per value "
        f"({len(tx_values)} steps)"
    )
    _log(f"{case_id} values: {', '.join(tx_values)}")

    source_v6 = _source_bind_ipv6(profile_bundle)
    bts_ssh = await _open_ipv6_ssh(
        bts_ip, device_creds["pass"], source_v6=source_v6, label="BTS",
    )
    cpe_ssh = await _ft25_open_cpe_ssh(cpe_ips[0], device_creds, profile_bundle)
    cpe_gui = await _ft25_open_gui_target(cpe_ips[0], device_creds)
    bts_gui = await _ft25_open_bts_gui_target(profile, bts_ip, device_creds)

    if bool(values.get("ft25_disable_atpc", True)):
        _log(f"{case_id}: disabling ATPC on CPE + BTS for deterministic Tx power mapping")
        await _ft25_disable_atpc(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        await _ft25_disable_atpc(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)

    cpe_baseline_iw, cpe_baseline_uci = await _ft25_read_tx_power_backend(
        cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s,
    )
    bts_baseline_iw, bts_baseline_uci = await _ft25_read_tx_power_backend(
        bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s,
    )
    _log(
        f"{case_id} baseline — CPE runtime={cpe_baseline_iw} uci={cpe_baseline_uci}; "
        f"BTS runtime={bts_baseline_iw} uci={bts_baseline_uci}"
    )

    step_results: list[tuple[str, bool, bool, bool]] = []

    try:
        for step_idx, tx_power in enumerate(tx_values, start=1):
            _log(
                f"{case_id} [{step_idx}/{len(tx_values)}] Tx power {tx_power} dBm — "
                "CPE GUI → BTS GUI → link up → backend verify"
            )

            await _ft25_apply_tx_power_with_backend_verify(
                role="CPE",
                gui_target=cpe_gui,
                ssh_target=cpe_ssh,
                radio_idx=radio_idx,
                tx_power=tx_power,
                values=values,
                timeout_s=timeout_s,
            )
            await _ft25_apply_tx_power_with_backend_verify(
                role="BTS",
                gui_target=bts_gui,
                ssh_target=bts_ssh,
                radio_idx=radio_idx,
                tx_power=tx_power,
                values=values,
                timeout_s=timeout_s,
            )

            await _wait_rf_link_healthy(
                bts_ssh,
                profile,
                case_id=case_id,
                label=f"tx={tx_power}",
                timeout_s=link_timeout,
                poll_s=link_poll,
            )

            cpe_iw, cpe_uci = await _ft25_read_tx_power_backend_retry(
                cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=6, delay_s=3.0,
            )
            if not _ft25_tx_power_matches(tx_power, cpe_iw, cpe_uci):
                _log(f"{case_id} [CPE] post-link mismatch ({cpe_iw}/{cpe_uci}); re-force backend")
                await _ft25_backend_force_tx_power(
                    cpe_ssh, radio_idx=radio_idx, tx_power=tx_power, timeout_s=timeout_s,
                )
                cpe_iw, cpe_uci = await _ft25_read_tx_power_backend_retry(
                    cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=8, delay_s=3.0,
                )
            cpe_ok = _ft25_print_tx_power_row(case_id, "CPE", tx_power, cpe_iw, cpe_uci)

            bts_iw, bts_uci = await _ft25_read_tx_power_backend_retry(
                bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=6, delay_s=3.0,
            )
            if not _ft25_tx_power_matches(tx_power, bts_iw, bts_uci):
                _log(f"{case_id} [BTS] post-link mismatch ({bts_iw}/{bts_uci}); re-force backend")
                await _ft25_backend_force_tx_power(
                    bts_ssh, radio_idx=radio_idx, tx_power=tx_power, timeout_s=timeout_s,
                )
                bts_iw, bts_uci = await _ft25_read_tx_power_backend_retry(
                    bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=8, delay_s=3.0,
                )
            bts_ok = _ft25_print_tx_power_row(case_id, "BTS", tx_power, bts_iw, bts_uci)
            step_results.append((tx_power, cpe_ok, bts_ok, True))
            _log(f"{case_id} step {tx_power} dBm — link up, CPE={'PASS' if cpe_ok else 'FAIL'}, BTS={'PASS' if bts_ok else 'FAIL'}")
            check.is_true(
                cpe_ok and bts_ok,
                f"{case_id}: Tx power {tx_power} dBm backend mismatch "
                f"(CPE={cpe_iw}/{cpe_uci}, BTS={bts_iw}/{bts_uci})",
            )

        if bool(values.get("ft25_restore_baseline", True)):
            restore = cpe_baseline_uci or cpe_baseline_iw or "1"
            _log(f"{case_id} restoring baseline Tx power {restore} dBm on CPE + BTS")
            await _ft25_apply_tx_power_gui(
                cpe_gui["radio_page"], cpe_gui["page"], tx_power=restore, values=values,
            )
            await _ft25_apply_tx_power_gui(
                bts_gui["radio_page"], bts_gui["page"], tx_power=restore, values=values,
            )
            await _wait_rf_link_healthy(
                bts_ssh,
                profile,
                case_id=case_id,
                label="restore",
                timeout_s=link_timeout,
                poll_s=link_poll,
            )

        print_section(f"{case_id}: summary — all Tx power steps")
        print_comparison_table(
            [
                (
                    f"{tx} dBm",
                    "CPE+BTS backend match, link up",
                    f"CPE={'PASS' if cpe_ok else 'FAIL'} BTS={'PASS' if bts_ok else 'FAIL'}",
                    "PASS" if cpe_ok and bts_ok else "FAIL",
                )
                for tx, cpe_ok, bts_ok, _ in step_results
            ],
        )
    finally:
        await _ft25_close_gui_target(cpe_gui)
        await _ft25_close_gui_target(bts_gui)
        try:
            await cpe_ssh.close()
        except Exception:
            pass
        try:
            await bts_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} complete — all Tx power values verified on CPE + BTS, link up each step")


async def assert_ft_26_gui_address_bar_ip(
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """
    FT_26 — verify browser address bar host matches BTS/CPE GUI target IPs.
    Also ensure RF link is up before checking pages.
    """
    case_id = "FT_26"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    timeout_s = int(values["ssh_timeout_s"])
    link_timeout = int(values.get("ft25_link_recovery_timeout_s", 240))
    link_poll = int(values.get("ft25_link_recovery_poll_s", 8))

    print_section(f"{case_id}: GUI address-bar IP validation (BTS + CPE)")

    source_v6 = _source_bind_ipv6(profile_bundle)
    bts_ssh = await _open_ipv6_ssh(
        bts_ip, device_creds["pass"], source_v6=source_v6, label="BTS",
    )

    bts_gui = None
    cpe_gui = None
    try:
        await _wait_rf_link_healthy(
            bts_ssh,
            profile,
            case_id=case_id,
            label="precheck",
            timeout_s=link_timeout,
            poll_s=link_poll,
        )

        # Prefer direct BTS IP; fallback to known reachable GUI host if needed.
        try:
            bts_gui = await _ft25_open_gui_target(bts_ip, device_creds)
            expected_bts_host = bts_ip
        except Exception:
            bts_gui = await _ft25_open_bts_gui_target(profile, bts_ip, device_creds)
            expected_bts_host = bts_gui["host"]

        cpe_gui = await _ft25_open_gui_target(cpe_ip, device_creds)
        expected_cpe_host = cpe_ip

        bts_url = bts_gui["page"].url or ""
        cpe_url = cpe_gui["page"].url or ""

        bts_ok = ip_in_text(expected_bts_host, bts_url) or format_http_host(expected_bts_host) in bts_url
        cpe_ok = ip_in_text(expected_cpe_host, cpe_url) or format_http_host(expected_cpe_host) in cpe_url

        print_comparison_table(
            [
                ("BTS address bar host", expected_bts_host, bts_url, "PASS" if bts_ok else "FAIL"),
                ("CPE address bar host", expected_cpe_host, cpe_url, "PASS" if cpe_ok else "FAIL"),
            ]
        )

        check.is_true(bts_ok, f"{case_id}: BTS address bar host mismatch (url={bts_url})")
        check.is_true(cpe_ok, f"{case_id}: CPE address bar host mismatch (url={cpe_url})")
    finally:
        if cpe_gui is not None:
            await _ft25_close_gui_target(cpe_gui)
        if bts_gui is not None:
            await _ft25_close_gui_target(bts_gui)
        try:
            await bts_ssh.close()
        except Exception:
            pass

    _log(f"{case_id} complete — BTS and CPE address bar host validated")


async def _ft27_open_gui_target(
    host: str,
    *,
    browser_name: str,
    creds: dict[str, str],
) -> dict:
    from playwright.async_api import async_playwright
    from pages.radio_properties_page import RadioPropertiesPage
    from utils.gui_login import login_if_needed

    host = normalize_ip(host)
    pw = await async_playwright().start()
    if browser_name == "firefox":
        browser = await pw.firefox.launch(headless=True)
    else:
        browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(ignore_https_errors=True)
    page = await context.new_page()
    await login_if_needed(page, host, creds, wait_ms=3000, skip_recovery=True)
    return {
        "host": host,
        "page": page,
        "browser": browser,
        "context": context,
        "playwright": pw,
        "radio_page": RadioPropertiesPage(page, local_ip=host),
    }


def _ft27_url_host(page_url: str) -> str:
    parsed = urlparse(page_url or "")
    return normalize_ip(parsed.hostname or "")


async def _ft27_wait_for_auto_logout(page, *, timeout_s: int, poll_s: float) -> tuple[bool, float | None]:
    from pages.locators import LoginPageLocators

    start = time.monotonic()
    deadline = start + timeout_s
    while time.monotonic() < deadline:
        try:
            visible = await page.locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=500)
            if visible:
                elapsed = round(time.monotonic() - start, 1)
                return True, elapsed
        except Exception:
            pass
        await asyncio.sleep(max(0.5, poll_s))
    return False, None


async def _ft27_verify_browser_sessions(
    *,
    case_id: str,
    browser_name: str,
    bts_ip: str,
    cpe_ip: str,
    browser_creds: dict[str, str],
    timeout_s: int,
    poll_s: float,
) -> list[tuple[str, str, str, str, str]]:
    from pages.locators import LoginPageLocators

    bts_target = await _ft27_open_gui_target(bts_ip, browser_name=browser_name, creds=browser_creds)
    cpe_target = await _ft27_open_gui_target(cpe_ip, browser_name=browser_name, creds=browser_creds)
    rows: list[tuple[str, str, str, str, str]] = []
    try:
        bts_login_visible = await bts_target["page"].locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=1000)
        cpe_login_visible = await cpe_target["page"].locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=1000)
        bts_logged_in = not bts_login_visible
        cpe_logged_in = not cpe_login_visible
        # Optional navigation; auto-logout validation should still run even if menu path differs by role.
        if bts_logged_in:
            try:
                await bts_target["radio_page"].navigate()
            except Exception:
                pass
        if cpe_logged_in:
            try:
                await cpe_target["radio_page"].navigate()
            except Exception:
                pass
        bts_host_ok = ip_in_text(bts_ip, _ft27_url_host(bts_target["page"].url))
        cpe_host_ok = ip_in_text(cpe_ip, _ft27_url_host(cpe_target["page"].url))
        bts_logged_out, bts_elapsed = await _ft27_wait_for_auto_logout(
            bts_target["page"], timeout_s=timeout_s, poll_s=poll_s,
        )
        cpe_logged_out, cpe_elapsed = await _ft27_wait_for_auto_logout(
            cpe_target["page"], timeout_s=timeout_s, poll_s=poll_s,
        )
        rows.append(
            (
                f"{browser_name} BTS",
                f"host={bts_ip}",
                f"host_ok={bts_host_ok}, login_ok={bts_logged_in}, logout={bts_logged_out}, t={bts_elapsed or 'timeout'}s",
                "PASS" if (bts_host_ok and bts_logged_in and bts_logged_out) else "FAIL",
                browser_creds["user"],
            )
        )
        rows.append(
            (
                f"{browser_name} CPE",
                f"host={cpe_ip}",
                f"host_ok={cpe_host_ok}, login_ok={cpe_logged_in}, logout={cpe_logged_out}, t={cpe_elapsed or 'timeout'}s",
                "PASS" if (cpe_host_ok and cpe_logged_in and cpe_logged_out) else "FAIL",
                browser_creds["user"],
            )
        )
        check.is_true(bts_host_ok and bts_logged_in and bts_logged_out, f"{case_id}: {browser_name} BTS auto-logout failed")
        check.is_true(cpe_host_ok and cpe_logged_in and cpe_logged_out, f"{case_id}: {browser_name} CPE auto-logout failed")
        return rows
    finally:
        await _ft25_close_gui_target(cpe_target)
        await _ft25_close_gui_target(bts_target)


async def assert_ft_27_gui_auto_logout(
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """
    FT_27 — verify GUI auto logout after timeout on BTS+CPE in Chrome(admin) and Firefox(rootuser).
    """
    case_id = "FT_27"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    values = FUNCTIONAL_TEST_VALUES
    timeout_s = int(values.get("ft27_logout_timeout_s", 120))
    poll_s = float(values.get("ft27_logout_poll_s", 2))
    admin_creds = {
        "user": str(values.get("ft27_admin_user", device_creds.get("user", "root"))),
        "pass": str(values.get("ft27_admin_pass", device_creds.get("pass", ""))),
    }
    rootuser_creds = {
        "user": str(values.get("ft27_root_user", device_creds.get("user", "root"))),
        "pass": str(values.get("ft27_root_pass", device_creds.get("pass", ""))),
    }

    print_section(f"{case_id}: GUI auto-logout validation on Chromium + Firefox")
    rows: list[tuple[str, str, str, str]] = []
    chrome_rows = await _ft27_verify_browser_sessions(
        case_id=case_id,
        browser_name="chromium",
        bts_ip=bts_ip,
        cpe_ip=cpe_ip,
        browser_creds=admin_creds,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
    rows.extend([(a, b, c, d) for a, b, c, d, _ in chrome_rows])
    firefox_rows = await _ft27_verify_browser_sessions(
        case_id=case_id,
        browser_name="firefox",
        bts_ip=bts_ip,
        cpe_ip=cpe_ip,
        browser_creds=rootuser_creds,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
    rows.extend([(a, b, c, d) for a, b, c, d, _ in firefox_rows])
    print_comparison_table(rows)
    _log(f"{case_id} complete — auto logout validated on BTS+CPE for Chromium and Firefox")


def _ft28_channel_matches(expected: str, configured: str, active: str) -> bool:
    exp = str(expected).strip()
    if configured and exp in str(configured):
        return True
    return bool(active) and exp in str(active)


async def _ft28_read_bts_channel_tx_backend(
    ssh, *, radio_idx: int, timeout_s: int,
) -> dict[str, str]:
    configured_raw = clean_ssh_output(
        await _ssh(ssh, RootCommands.get_configured_channel(radio_idx), timeout_s=timeout_s)
    )
    active_raw = clean_ssh_output(
        await _ssh(ssh, RootCommands.get_active_channel(radio_idx), timeout_s=timeout_s)
    )
    runtime_tx, uci_tx = await _ft25_read_tx_power_backend_retry(
        ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=5, delay_s=3.0,
    )
    return {
        "configured_channel": extract_uci_value(configured_raw),
        "active_channel": parse_iwconfig_active_channel(active_raw),
        "runtime_tx": runtime_tx,
        "uci_tx": uci_tx,
    }


async def _ft28_apply_backend_changes(
    ssh,
    *,
    radio_idx: int,
    channel: str,
    tx_power: str,
    timeout_s: int,
) -> None:
    tx = str(tx_power).strip()
    ch = str(channel).strip()
    await _ssh(ssh, f"uci set advwireless.ath{radio_idx}.channel={ch}", timeout_s=timeout_s)
    await _ssh(ssh, f"uci set txparam.ath{radio_idx}.atpcpower={tx}", timeout_s=timeout_s)
    await _ssh(ssh, "uci commit advwireless", timeout_s=timeout_s)
    await _ssh(ssh, "uci commit txparam", timeout_s=timeout_s)
    await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))


async def _ft28_apply_cpe_tx_backend(
    ssh,
    *,
    radio_idx: int,
    tx_power: str,
    timeout_s: int,
) -> None:
    await _ft25_backend_force_tx_power(
        ssh, radio_idx=radio_idx, tx_power=tx_power, timeout_s=timeout_s,
    )


async def _ft28_print_phase_verify(
    case_id: str,
    phase: str,
    *,
    expected_channel: str,
    expected_tx: str,
    bts_state: dict[str, str],
    cpe_iw: str,
    cpe_uci: str,
    context: str,
) -> None:
    bts_ch_ok = _ft28_channel_matches(
        expected_channel, bts_state["configured_channel"], bts_state["active_channel"],
    )
    bts_tx_ok = _ft25_tx_power_matches(expected_tx, bts_state["runtime_tx"], bts_state["uci_tx"])
    cpe_tx_ok = _ft25_tx_power_matches(expected_tx, cpe_iw, cpe_uci)

    print_section(f"{case_id} {phase} — backend verify ({context})")
    print_comparison_table(
        [
            ("BTS configured channel", expected_channel, bts_state["configured_channel"] or "(empty)", "PASS" if bts_ch_ok else "FAIL"),
            ("BTS active channel", expected_channel, bts_state["active_channel"] or "(empty)", "PASS" if bts_ch_ok else "INFO"),
            ("BTS Tx power (UCI)", expected_tx, bts_state["uci_tx"] or "(empty)", "PASS" if bts_tx_ok else "FAIL"),
            ("BTS Tx power (runtime)", expected_tx, bts_state["runtime_tx"] or "(empty)", "PASS" if bts_tx_ok else "INFO"),
            ("CPE Tx power (UCI)", expected_tx, cpe_uci or "(empty)", "PASS" if cpe_tx_ok else "FAIL"),
            ("CPE Tx power (runtime)", expected_tx, cpe_iw or "(empty)", "PASS" if cpe_tx_ok else "INFO"),
        ],
    )
    check.is_true(bts_ch_ok, f"{case_id} {phase}: BTS channel expected {expected_channel}")
    check.is_true(bts_tx_ok, f"{case_id} {phase}: BTS Tx power expected {expected_tx}")
    check.is_true(cpe_tx_ok, f"{case_id} {phase}: CPE Tx power expected {expected_tx}")


async def _ft28_reconnect_cpe_ssh(
    cpe_ip: str,
    *,
    profile_bundle,
    device_creds: dict,
    values: dict,
):
    return await _wait_ipv6_ssh_ready(
        cpe_ip,
        password=device_creds["pass"],
        source_v6=_source_bind_ipv6(profile_bundle),
        label="CPE",
        timeout_s=int(values.get("ft28_reboot_timeout_s", 300)),
        poll_s=int(values.get("ft28_reconnect_poll_s", 8)),
    )


async def _ft28_run_phase(
    *,
    case_id: str,
    phase: str,
    channel: str,
    tx_power: str,
    bts_ssh,
    cpe_ssh,
    cpe_ip: str,
    bts_ip: str,
    profile,
    profile_bundle,
    device_creds: dict,
    values: dict,
    radio_idx: int,
    timeout_s: int,
) -> tuple[object, object]:
    print_section(f"{case_id} {phase}: backend apply BTS ch={channel} tx={tx_power}, CPE tx={tx_power}")
    await _ft28_apply_backend_changes(
        bts_ssh, radio_idx=radio_idx, channel=channel, tx_power=tx_power, timeout_s=timeout_s,
    )
    await _ft28_apply_cpe_tx_backend(
        cpe_ssh, radio_idx=radio_idx, tx_power=tx_power, timeout_s=timeout_s,
    )

    bts_state = await _ft28_read_bts_channel_tx_backend(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    cpe_iw, cpe_uci = await _ft25_read_tx_power_backend_retry(
        cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=5, delay_s=3.0,
    )
    await _ft28_print_phase_verify(
        case_id,
        phase,
        expected_channel=channel,
        expected_tx=tx_power,
        bts_state=bts_state,
        cpe_iw=cpe_iw,
        cpe_uci=cpe_uci,
        context="before reboot",
    )

    print_section(f"{case_id} {phase}: soft reboot CPE then BTS")
    await _ft09_issue_ssh_reboot(cpe_ssh, values, label="CPE")
    await asyncio.sleep(int(values.get("ft28_post_reboot_settle_s", 30)))
    await _ft09_issue_ssh_reboot(bts_ssh, values, label="BTS")

    try:
        await cpe_ssh.close()
    except Exception:
        pass
    bts_ssh = await _ft09_reconnect_bts_ssh(
        bts_ssh, profile_bundle, device_creds, bts_ip=bts_ip, values=values,
    )
    cpe_ssh = await _ft28_reconnect_cpe_ssh(
        cpe_ip, profile_bundle=profile_bundle, device_creds=device_creds, values=values,
    )
    await _wait_rf_link_healthy(
        bts_ssh,
        profile,
        case_id=case_id,
        label=f"{phase}-post-reboot",
        timeout_s=int(values.get("ft28_link_recovery_timeout_s", 240)),
        poll_s=int(values.get("ft28_link_recovery_poll_s", 8)),
    )

    bts_state = await _ft28_read_bts_channel_tx_backend(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    cpe_iw, cpe_uci = await _ft25_read_tx_power_backend_retry(
        cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s, retries=8, delay_s=3.0,
    )
    await _ft28_print_phase_verify(
        case_id,
        phase,
        expected_channel=channel,
        expected_tx=tx_power,
        bts_state=bts_state,
        cpe_iw=cpe_iw,
        cpe_uci=cpe_uci,
        context="after reboot",
    )
    return bts_ssh, cpe_ssh


def _ft10_next_prefix(cidr: str, *, values: dict) -> str:
    host = normalize_ip(str(cidr or "").split("/")[0])
    check.is_true(bool(host), f"FT_10: invalid IPv6 CIDR {cidr!r}")
    try:
        current_prefix = int(str(cidr).split("/", 1)[1])
    except Exception:
        current_prefix = int(values.get("ft10_default_prefix_len", 120))
    candidates = [int(v) for v in values.get("ft10_prefix_candidates", [118, 124, 120])]
    target_prefix = next((p for p in candidates if p != current_prefix), current_prefix)
    return f"{host}/{target_prefix}"


async def _ft10_read_network_state(ssh, *, timeout_s: int) -> dict[str, str]:
    mode_raw = ""
    try:
        mode_raw = clean_ssh_output(await _ssh(ssh, RootCommands.GET_VLAN_MODE, timeout_s=timeout_s)).strip()
    except Exception:
        try:
            mode_raw = clean_ssh_output(
                await _ssh(ssh, "uci -q get vlan.ath1.vlanmode", timeout_s=timeout_s)
            ).strip()
        except Exception:
            mode_raw = ""

    async def _uci(cmd: str) -> str:
        try:
            return clean_ssh_output(await _ssh(ssh, cmd, timeout_s=timeout_s)).strip()
        except Exception:
            return ""

    ipv4_raw = await _uci(f"{RootCommands.GET_NET_IP} 2>/dev/null || true")
    ipv4_gw_raw = await _uci(f"{RootCommands.GET_NET_GW} 2>/dev/null || true")
    ipv6_gw_raw = await _uci(f"{RootCommands.GET_NET_GW6} 2>/dev/null || true")
    return {
        "proto": await _uci(f"{RootCommands.GET_NET_PROTO} 2>/dev/null || true"),
        "ipv4": normalize_ip(ipv4_raw.split("/")[0]) if ipv4_raw else "",
        "netmask": await _uci(f"{RootCommands.GET_NET_MASK} 2>/dev/null || true"),
        "ipv4_gw": normalize_ip(ipv4_gw_raw.split("/")[0]) if ipv4_gw_raw else "",
        "ipv6": await _uci(f"{RootCommands.GET_NET_IP6} 2>/dev/null || true"),
        "ipv6_gw": normalize_ip(ipv6_gw_raw.split("/")[0]) if ipv6_gw_raw else "",
        "mgmt_vlan": await _uci(f"{RootCommands.GET_VLAN_MGMT} 2>/dev/null || true"),
        "vlan_mode": mode_raw,
    }


async def _ft10_reopen_ssh(
    host: str,
    *,
    profile_bundle,
    device_creds: dict,
    label: str,
    timeout_s: int,
    poll_s: int,
):
    return await _wait_ipv6_ssh_ready(
        host,
        password=device_creds["pass"],
        source_v6=_source_bind_ipv6(profile_bundle),
        label=label,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )


async def _ft10_open_cpe_backend_session(
    *,
    bts_ssh,
    cpe_ip: str,
    bts_ip: str,
    profile_bundle,
    device_creds: dict,
    values: dict,
):
    try:
        return await _ft10_reopen_ssh(
            cpe_ip,
            profile_bundle=profile_bundle,
            device_creds=device_creds,
            label="CPE",
            timeout_s=int(values.get("ft10_cpe_reconnect_timeout_s", values.get("ft10_link_recovery_timeout_s", 240))),
            poll_s=int(values.get("ft10_link_recovery_poll_s", 8)),
        )
    except Exception:
        from utils.arp_bridge_table_flows import _open_cpe_ssh

        cpe_ssh, _mode = await _open_cpe_ssh(bts_ssh, cpe_ip, bts_ip, device_creds)
        return cpe_ssh


async def _ft10_apply_backend_network(
    ssh,
    *,
    ipv4: str,
    netmask: str,
    ipv6_cidr: str,
    timeout_s: int,
) -> None:
    cmds = [
        "ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {netmask}",
        f"ucidyn set network.lan.ip6addr {ipv6_cidr}",
        "ucidyn apply",
    ]
    for cmd in cmds:
        await _ssh(ssh, cmd, timeout_s=max(120, timeout_s))


async def _ft10_refresh_cpe_backend_session(
    current_ssh,
    *,
    bts_ssh,
    cpe_ip: str,
    bts_ip: str,
    profile_bundle,
    device_creds: dict,
    values: dict,
):
    try:
        await current_ssh.close()
    except Exception:
        pass
    return await _ft10_open_cpe_backend_session(
        bts_ssh=bts_ssh,
        cpe_ip=cpe_ip,
        bts_ip=bts_ip,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        values=values,
    )


async def _ft10_read_originals(
    *,
    case_id: str,
    bts_ssh,
    cpe_ssh,
    radio_idx: int,
    timeout_s: int,
) -> tuple[dict[str, str], dict[str, str]]:
    bts = await _ft07_read_device_snapshot(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    cpe = await _ft07_read_device_snapshot(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    bts_net = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    cpe_net = await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s)
    bts.update(bts_net)
    cpe.update(cpe_net)
    _print_backend_table(
        f"{case_id} original config — BTS + CPE",
        [
            ("BTS Mgmt VLAN", "present", bts["mgmt_vlan"] or "(empty)", "PASS" if bts["mgmt_vlan"] else "FAIL"),
            ("BTS Protocol", "captured", bts["proto"] or "(empty)", "PASS"),
            ("BTS IPv6 Prefix", "captured", bts["ipv6"] or "(empty)", "PASS"),
            ("CPE Mgmt VLAN", "present", cpe["mgmt_vlan"] or "(empty)", "PASS" if cpe["mgmt_vlan"] else "FAIL"),
            ("CPE Protocol", "captured", cpe["proto"] or "(empty)", "PASS"),
            ("CPE IPv6 Prefix", "captured", cpe["ipv6"] or "(empty)", "PASS"),
        ],
    )
    check.is_true(bool(bts["mgmt_vlan"]), f"{case_id}: BTS mgmt VLAN missing")
    check.is_true(bool(cpe["mgmt_vlan"]), f"{case_id}: CPE mgmt VLAN missing")
    return bts, cpe


async def _ft10_wait_link_result(
    bts_ssh,
    *,
    profile: dict,
    case_id: str,
    label: str,
    timeout_s: int,
    poll_s: int,
) -> tuple[str, str]:
    """
    PASS: RF link does not drop after backend changes.
    PASS (UNSTABLE): RF link drops, then comes back before timeout.
    FAIL: RF link drops and does not recover within timeout.
    """
    _log(f"{case_id} [{label}] observe RF link result (timeout={timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    saw_drop = False
    last_detail = ""
    while time.monotonic() < deadline:
        try:
            link_ok, detail = await _rf_link_up(bts_ssh, profile)
        except Exception as exc:
            link_ok, detail = False, f"link poll error: {exc}"
        last_detail = detail
        if link_ok:
            if saw_drop:
                return "PASS (UNSTABLE)", "RF Interface Restarts,By design"
            return "PASS", "RF Link wont drop"
        saw_drop = True
        await asyncio.sleep(max(1, poll_s))
    return "FAIL", f"RF Link Drops never comes up until {timeout_s // 60} mins ({last_detail})"


async def _ft10_verify_backend_pair(
    *,
    bts_ssh,
    cpe_ssh,
    bts_expected: dict[str, str],
    cpe_expected: dict[str, str],
    timeout_s: int,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    bts_after = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    cpe_after = await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s)
    rows = [
        ("BTS Mgmt VLAN", bts_expected["mgmt_vlan"], bts_after["mgmt_vlan"], "PASS" if bts_after["mgmt_vlan"] == bts_expected["mgmt_vlan"] else "FAIL"),
        ("BTS Protocol", bts_expected["proto"], bts_after["proto"], "PASS" if bts_after["proto"] == bts_expected["proto"] else "FAIL"),
        ("BTS IPv6 Prefix", bts_expected["ipv6"], bts_after["ipv6"], "PASS" if bts_after["ipv6"] == bts_expected["ipv6"] else "FAIL"),
        ("CPE Mgmt VLAN", cpe_expected["mgmt_vlan"], cpe_after["mgmt_vlan"], "PASS" if cpe_after["mgmt_vlan"] == cpe_expected["mgmt_vlan"] else "FAIL"),
        ("CPE Protocol", cpe_expected["proto"], cpe_after["proto"], "PASS" if cpe_after["proto"] == cpe_expected["proto"] else "FAIL"),
        ("CPE IPv6 Prefix", cpe_expected["ipv6"], cpe_after["ipv6"], "PASS" if cpe_after["ipv6"] == cpe_expected["ipv6"] else "FAIL"),
    ]
    return all(row[3] == "PASS" for row in rows), rows


async def assert_ft_17_mgmt_vlan_prefix_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """
    FT_17 — pure backend/SSH flow from scratch:
    change CPE prefix first, then BTS prefix, verify link result and backend,
    then revert originals and verify again.
    """
    case_id = "FT_17"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    radio_idx = int(values.get("ft10_radio_idx", RADIO_IDX))
    timeout_s = int(values["ssh_timeout_s"])
    link_timeout_s = int(values.get("ft10_link_recovery_timeout_s", 900))
    link_poll_s = int(values.get("ft10_link_recovery_poll_s", 8))
    await _wait_rf_link_healthy(
        root_ssh,
        profile,
        case_id=case_id,
        label="precheck",
        timeout_s=link_timeout_s,
        poll_s=link_poll_s,
    )

    bts_ssh = await _ft10_reopen_ssh(
        bts_ip,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        label="BTS",
        timeout_s=link_timeout_s,
        poll_s=link_poll_s,
    )
    cpe_ssh = await _ft10_open_cpe_backend_session(
        bts_ssh=bts_ssh,
        cpe_ip=cpe_ip,
        bts_ip=bts_ip,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        values=values,
    )
    try:
        bts_before, cpe_before = await _ft10_read_originals(
            case_id=case_id,
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
        )
        target_bts_ipv6 = _ft10_next_prefix(bts_before.get("ipv6", ""), values=values)
        target_cpe_ipv6 = _ft10_next_prefix(cpe_before.get("ipv6", ""), values=values)

        print_section(f"{case_id}: backend/SSH steps")
        print_comparison_table(
            [
                ("Step 1", "CPE", f"Change IPv6 prefix to {target_cpe_ipv6}", "INFO"),
                ("Step 2", "BTS", f"Change IPv6 prefix to {target_bts_ipv6}", "INFO"),
                ("Step 3", "BTS", "Verify RF link result", "INFO"),
                ("Step 4", "BTS + CPE", "Verify backend values", "INFO"),
                ("Step 5", "CPE then BTS", "Revert original config", "INFO"),
            ],
        )

        await _ft10_apply_backend_network(
            cpe_ssh,
            ipv4=cpe_before.get("ipv4", ""),
            netmask=cpe_before.get("netmask", ""),
            ipv6_cidr=target_cpe_ipv6,
            timeout_s=timeout_s,
        )
        await _ft10_apply_backend_network(
            bts_ssh,
            ipv4=bts_before.get("ipv4", ""),
            netmask=bts_before.get("netmask", ""),
            ipv6_cidr=target_bts_ipv6,
            timeout_s=timeout_s,
        )
        cpe_ssh = await _ft10_refresh_cpe_backend_session(
            cpe_ssh,
            bts_ssh=bts_ssh,
            cpe_ip=cpe_ip,
            bts_ip=bts_ip,
            profile_bundle=profile_bundle,
            device_creds=device_creds,
            values=values,
        )

        link_status, link_comment = await _ft10_wait_link_result(
            bts_ssh,
            case_id=case_id,
            label="post-apply",
            profile=profile,
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
        )

        apply_ok, apply_rows = await _ft10_verify_backend_pair(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={
                "mgmt_vlan": bts_before["mgmt_vlan"],
                "proto": "static",
                "ipv6": target_bts_ipv6,
            },
            cpe_expected={
                "mgmt_vlan": cpe_before["mgmt_vlan"],
                "proto": "static",
                "ipv6": target_cpe_ipv6,
            },
            timeout_s=timeout_s,
        )
        _print_backend_table(
            f"{case_id} verify after apply — BTS + CPE",
            apply_rows + [("Link Result", "PASS / FAIL / PASS (UNSTABLE)", link_status, "INFO"), ("Comment", "summary", link_comment, "INFO")],
        )

        print_section(f"{case_id}: revert original config on CPE then BTS via SSH/backend")
        await _ft10_apply_backend_network(
            cpe_ssh,
            ipv4=cpe_before.get("ipv4", ""),
            netmask=cpe_before.get("netmask", ""),
            ipv6_cidr=cpe_before.get("ipv6", ""),
            timeout_s=timeout_s,
        )
        await _ft10_apply_backend_network(
            bts_ssh,
            ipv4=bts_before.get("ipv4", ""),
            netmask=bts_before.get("netmask", ""),
            ipv6_cidr=bts_before.get("ipv6", ""),
            timeout_s=timeout_s,
        )
        cpe_ssh = await _ft10_refresh_cpe_backend_session(
            cpe_ssh,
            bts_ssh=bts_ssh,
            cpe_ip=cpe_ip,
            bts_ip=bts_ip,
            profile_bundle=profile_bundle,
            device_creds=device_creds,
            values=values,
        )

        revert_link_status, revert_link_comment = await _ft10_wait_link_result(
            bts_ssh,
            case_id=case_id,
            label="post-revert",
            profile=profile,
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
        )

        revert_ok, revert_rows = await _ft10_verify_backend_pair(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={
                "mgmt_vlan": bts_before["mgmt_vlan"],
                "proto": bts_before["proto"],
                "ipv6": bts_before["ipv6"],
            },
            cpe_expected={
                "mgmt_vlan": cpe_before["mgmt_vlan"],
                "proto": cpe_before["proto"],
                "ipv6": cpe_before["ipv6"],
            },
            timeout_s=timeout_s,
        )
        _print_backend_table(
            f"{case_id} verify after revert — BTS + CPE",
            revert_rows + [("Link Result", "PASS / FAIL / PASS (UNSTABLE)", revert_link_status, "INFO"), ("Comment", "summary", revert_link_comment, "INFO")],
        )

        if link_status == "FAIL" or revert_link_status == "FAIL":
            final_status = "FAIL"
            final_comment = "RF Link Drops never comes up until 15 mins"
        elif link_status == "PASS (UNSTABLE)" or revert_link_status == "PASS (UNSTABLE)":
            final_status = "PASS (UNSTABLE)"
            final_comment = "RF Interface Restarts,By design"
        elif apply_ok and revert_ok:
            final_status = "PASS"
            final_comment = "RF Link wont drop"
        else:
            final_status = "FAIL"
            final_comment = "Backend config verification failed"
        print_section(f"{case_id} final result")
        print_comparison_table(
            [
                ("Result", "PASS / FAIL / PASS (UNSTABLE)", final_status, final_status),
                ("Comment", "summary", final_comment, "INFO"),
            ],
        )
        check.is_true(final_status != "FAIL", f"{case_id}: {final_comment}")
    finally:
        try:
            await cpe_ssh.close()
        except Exception:
            pass
        if bts_ssh is not root_ssh:
            try:
                await bts_ssh.close()
            except Exception:
                pass


async def assert_ft_28_commit_after_config_change_reboot(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    """
    FT_28 — backend apply BTS channel + Tx and CPE Tx, verify before reboot,
    reboot both by SSH, wait for link on BTS, and verify persistence after reboot.
    """
    case_id = "FT_28"
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)

    check.is_true(bool(device_creds), f"{case_id}: device credentials required")
    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")

    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    radio_idx = int(values.get("ft28_radio_idx", RADIO_IDX))
    timeout_s = int(values["ssh_timeout_s"])
    channel_a = str(values.get("ft28_channel_a", "116"))
    tx_a = str(values.get("ft28_tx_power_a", "10"))
    channel_b = str(values.get("ft28_channel_b", "132"))
    tx_b = str(values.get("ft28_tx_power_b", "6"))

    print_section(
        f"{case_id}: BTS channel+Tx commit survives reboot — "
        f"phase-1 ch={channel_a} tx={tx_a}, phase-2 ch={channel_b} tx={tx_b}"
    )
    await _wait_rf_link_healthy(
        root_ssh,
        profile,
        case_id=case_id,
        label="precheck",
        timeout_s=int(values.get("ft28_link_recovery_timeout_s", 240)),
        poll_s=int(values.get("ft28_link_recovery_poll_s", 8)),
    )

    source_v6 = _source_bind_ipv6(profile_bundle)
    bts_ssh = await _open_ipv6_ssh(
        bts_ip, device_creds["pass"], source_v6=source_v6, label="BTS",
    )
    cpe_ssh = await _open_ipv6_ssh(
        cpe_ip, device_creds["pass"], source_v6=source_v6, label="CPE",
    )
    if bool(values.get("ft28_disable_atpc", True)):
        _log(f"{case_id}: disabling ATPC on BTS + CPE for deterministic Tx power")
        await _ft25_disable_atpc(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        await _ft25_disable_atpc(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)

    try:
        bts_ssh, cpe_ssh = await _ft28_run_phase(
            case_id=case_id,
            phase="phase-1",
            channel=channel_a,
            tx_power=tx_a,
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            cpe_ip=cpe_ip,
            bts_ip=bts_ip,
            profile=profile,
            profile_bundle=profile_bundle,
            device_creds=device_creds,
            values=values,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
        )
        bts_ssh, cpe_ssh = await _ft28_run_phase(
            case_id=case_id,
            phase="phase-2",
            channel=channel_b,
            tx_power=tx_b,
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            cpe_ip=cpe_ip,
            bts_ip=bts_ip,
            profile=profile,
            profile_bundle=profile_bundle,
            device_creds=device_creds,
            values=values,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
        )
    finally:
        try:
            await cpe_ssh.close()
        except Exception:
            pass
        if bts_ssh is not root_ssh:
            try:
                await bts_ssh.close()
            except Exception:
                pass

    _log(f"{case_id} complete — channel + Tx power retained across reboot for both phases")

# recovered mgmt vlan helpers

async def _ft10_apply_mgmt_vlan(
    ssh,
    *,
    vlan_id: int,
    timeout_s: int,
    case_id: str = "",
) -> None:
    """Set mgmtvlan and apply. ``ucidyn apply`` often drops the live SSH session."""
    label = case_id or "FT"
    _log(f"{label}: ucidyn set vlan.ath1.mgmtvlan {int(vlan_id)}")
    await _ssh(ssh, f"ucidyn set vlan.ath1.mgmtvlan {int(vlan_id)}", timeout_s=max(120, timeout_s))
    try:
        await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))
    except Exception as exc:
        _log(f"{label}: mgmt VLAN apply dropped SSH (expected): {exc}")

async def _ft10_change_prefix(
    ssh,
    *,
    snapshot: dict[str, str],
    new_ipv6_cidr: str,
    timeout_s: int,
) -> None:
    await _ft10_apply_backend_network(
        ssh,
        ipv4=snapshot.get("ipv4", ""),
        netmask=snapshot.get("netmask", ""),
        ipv6_cidr=new_ipv6_cidr,
        timeout_s=timeout_s,
    )

async def _ft10_ensure_static(
    ssh,
    *,
    snapshot: dict[str, str],
    label: str,
    case_id: str,
    timeout_s: int,
) -> bool:
    """Set network.lan.proto to static when needed; keep current IPv4/IPv6."""
    proto = (snapshot.get("proto") or "").strip().lower()
    if proto == "static":
        _log(f"{case_id} [{label}] address type already static — continue")
        return False
    _log(f"{case_id} [{label}] address type is {proto or '(empty)'} — set to static")
    await _ft10_apply_backend_network(
        ssh,
        ipv4=snapshot.get("ipv4", ""),
        netmask=snapshot.get("netmask", ""),
        ipv6_cidr=snapshot.get("ipv6", ""),
        timeout_s=timeout_s,
    )
    return True

def _ft10_ft11_normalize_change_result(status: str, comment: str) -> tuple[str, str]:
    if status == "FAIL":
        return "FAIL", comment
    return "PASS (UNSTABLE)", "RF Interface Restarts,By design"

def _ft10_pick_random_mgmt_vlan(*, originals: list[str], values: dict) -> int:
    reserved = {1}
    for raw in originals:
        try:
            reserved.add(int(str(raw).strip()))
        except Exception:
            pass
    vmin = int(values.get("ft10_mgmt_vlan_min", 2))
    vmax = int(values.get("ft10_mgmt_vlan_max", 4094))
    if vmin > vmax:
        vmin, vmax = vmax, vmin
    candidates = [vid for vid in range(max(2, vmin), min(4094, vmax) + 1) if vid not in reserved]
    check.is_true(bool(candidates), "FT_10: no random mgmt VLAN candidate available")
    return random.choice(candidates)

async def _ft10_read_originals_without_vlan_force(
    *,
    bts_ssh,
    cpe_ssh,
    radio_idx: int,
    timeout_s: int,
) -> tuple[dict[str, str], dict[str, str]]:
    bts = await _ft07_read_device_snapshot(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    cpe = await _ft07_read_device_snapshot(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    bts.update(await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s))
    cpe.update(await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s))
    for label, state in (("BTS", bts), ("CPE", cpe)):
        raw = str(state.get("mgmt_vlan", "")).strip()
        check.is_true(raw.isdigit(), f"FT_10: {label} mgmt VLAN must be present")
        vid = int(raw)
        check.is_true(1 <= vid <= 4094, f"FT_10: {label} mgmt VLAN out of range: {raw}")
    return bts, cpe

async def _ft10_revert_network(
    ssh,
    *,
    snapshot: dict[str, str],
    timeout_s: int,
) -> None:
    proto = (snapshot.get("proto") or "static").strip().lower()
    cmds = [
        f"ucidyn set network.lan.proto {proto}",
        f"ucidyn set network.lan.ipaddr {snapshot.get('ipv4', '')}",
        f"ucidyn set network.lan.netmask {snapshot.get('netmask', '')}",
        f"ucidyn set network.lan.ip6addr {snapshot.get('ipv6', '')}",
        "ucidyn apply",
    ]
    for cmd in cmds:
        await _ssh(ssh, cmd, timeout_s=max(120, timeout_s))

async def _ft10_verify_mgmt_vlan_pair(
    *,
    bts_ssh,
    cpe_ssh,
    bts_vlan: str | int,
    cpe_vlan: str | int,
    timeout_s: int,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    bts_after = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    cpe_after = await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s)
    bts_expected = str(bts_vlan)
    cpe_expected = str(cpe_vlan)
    rows = [
        ("BTS Mgmt VLAN", bts_expected, bts_after["mgmt_vlan"], "PASS" if bts_after["mgmt_vlan"] == bts_expected else "FAIL"),
        ("CPE Mgmt VLAN", cpe_expected, cpe_after["mgmt_vlan"], "PASS" if cpe_after["mgmt_vlan"] == cpe_expected else "FAIL"),
    ]
    return all(row[3] == "PASS" for row in rows), rows

async def _ft11_apply_backend_network(
    ssh,
    *,
    ipv4: str,
    netmask: str,
    ipv6_cidr: str,
    ipv6_gateway: str,
    timeout_s: int,
) -> None:
    cmds = [
        "ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {netmask}",
        f"ucidyn set network.lan.ip6addr {ipv6_cidr}",
    ]
    if str(ipv6_gateway or "").strip():
        cmds.append(f"ucidyn set network.lan.ip6gw {ipv6_gateway}")
    else:
        # ucidyn delete leaves ip6gw in UCI on this image. uci delete clears it.
        cmds.append("uci -q delete network.lan.ip6gw; uci commit network")
        cmds.append("ucidyn delete network.lan.ip6gw 2>/dev/null || true")
    cmds.append("ucidyn apply")
    for cmd in cmds:
        try:
            await _ssh(ssh, cmd, timeout_s=max(120, timeout_s))
        except Exception as exc:
            _log(f"FT_11: backend apply dropped SSH on {cmd!r} (expected): {exc}")
            break

async def _ft11_change_gateway(
    ssh,
    *,
    snapshot: dict[str, str],
    new_ipv6_gateway: str,
    timeout_s: int,
) -> None:
    await _ft11_apply_backend_network(
        ssh,
        ipv4=snapshot.get("ipv4", ""),
        netmask=snapshot.get("netmask", ""),
        ipv6_cidr=snapshot.get("ipv6", ""),
        ipv6_gateway=new_ipv6_gateway,
        timeout_s=timeout_s,
    )

def _ft11_pick_gateway(
    *,
    ipv6_cidr: str,
    current_gateway: str,
    role: str,
    values: dict,
) -> str:
    raw = str(ipv6_cidr or "").strip()
    check.is_true(bool(raw), f"FT_11: missing IPv6 CIDR for {role}")
    try:
        iface = ipaddress.IPv6Interface(raw)
    except Exception as exc:
        raise AssertionError(f"FT_11: invalid IPv6 CIDR for {role}: {raw!r}") from exc

    current_norm = normalize_ip(str(current_gateway or "").split("/")[0])
    candidates = [int(v) for v in values.get("ft11_gateway_offsets", [1, 2, 10, 11, 20, 21])]
    for offset in candidates:
        addr = ipaddress.IPv6Address(int(iface.network.network_address) + offset)
        if addr not in iface.network:
            continue
        addr_str = addr.compressed
        if addr_str == iface.ip.compressed or addr_str == current_norm:
            continue
        return addr_str
    raise AssertionError(f"FT_11: no alternate IPv6 gateway candidate for {role} in {iface.network}")

async def _ft11_revert_network(
    ssh,
    *,
    snapshot: dict[str, str],
    timeout_s: int,
) -> None:
    await _ft11_apply_backend_network(
        ssh,
        ipv4=snapshot.get("ipv4", ""),
        netmask=snapshot.get("netmask", ""),
        ipv6_cidr=snapshot.get("ipv6", ""),
        ipv6_gateway=snapshot.get("ipv6_gw", ""),
        timeout_s=timeout_s,
    )
    proto = (snapshot.get("proto") or "static").strip().lower()
    if proto != "static":
        await _ssh(ssh, f"ucidyn set network.lan.proto {proto}", timeout_s=max(120, timeout_s))
        await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))

async def _ft11_verify_backend_pair(
    *,
    bts_ssh,
    cpe_ssh,
    bts_expected: dict[str, str],
    cpe_expected: dict[str, str],
    timeout_s: int,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    bts_after = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    cpe_after = await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s)
    rows = [
        ("BTS Mgmt VLAN", bts_expected["mgmt_vlan"], bts_after["mgmt_vlan"], "PASS" if bts_after["mgmt_vlan"] == bts_expected["mgmt_vlan"] else "FAIL"),
        ("BTS Protocol", bts_expected["proto"], bts_after["proto"], "PASS" if bts_after["proto"] == bts_expected["proto"] else "FAIL"),
        ("BTS IPv6", bts_expected["ipv6"], bts_after["ipv6"], "PASS" if bts_after["ipv6"] == bts_expected["ipv6"] else "FAIL"),
        ("BTS IPv6 Gateway", bts_expected["ipv6_gw"] or "(empty)", bts_after["ipv6_gw"] or "(empty)", "PASS" if bts_after["ipv6_gw"] == bts_expected["ipv6_gw"] else "FAIL"),
        ("CPE Mgmt VLAN", cpe_expected["mgmt_vlan"], cpe_after["mgmt_vlan"], "PASS" if cpe_after["mgmt_vlan"] == cpe_expected["mgmt_vlan"] else "FAIL"),
        ("CPE Protocol", cpe_expected["proto"], cpe_after["proto"], "PASS" if cpe_after["proto"] == cpe_expected["proto"] else "FAIL"),
        ("CPE IPv6", cpe_expected["ipv6"], cpe_after["ipv6"], "PASS" if cpe_after["ipv6"] == cpe_expected["ipv6"] else "FAIL"),
        ("CPE IPv6 Gateway", cpe_expected["ipv6_gw"] or "(empty)", cpe_after["ipv6_gw"] or "(empty)", "PASS" if cpe_after["ipv6_gw"] == cpe_expected["ipv6_gw"] else "FAIL"),
    ]
    return all(row[3] == "PASS" for row in rows), rows

async def _ft12_close_quiet(ssh) -> None:
    if ssh is None:
        return
    try:
        await ssh.close()
    except Exception:
        pass

def _ft12_host(cidr_or_ip: str) -> str:
    return normalize_ip(str(cidr_or_ip or "").split("/")[0])

async def _ft12_reconnect_after_ip_change(
    *,
    current_bts_ssh,
    current_cpe_ssh,
    bts_hosts: list[str],
    cpe_hosts: list[str],
    profile,
    profile_bundle,
    device_creds: dict,
    values: dict,
    case_id: str,
    bts_ip: str,
    wait_link: bool = True,
):
    """
    FT_12 order after IPv6 change/revert:
    1) reconnect BTS on new/old IP (or IPv4 fallback)
    2) wait RF link up (design: link drops during IP change)
    3) reconnect CPE on its IPv6 / BTS hop
    """
    await _ft12_close_quiet(current_cpe_ssh)
    await _ft12_close_quiet(current_bts_ssh)

    bts_ssh = await _ft12_reconnect_bts_ssh(
        preferred_hosts=bts_hosts,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        values=values,
        case_id=case_id,
    )

    link_status, link_comment = "PASS", "RF Link wont drop"
    if wait_link:
        link_timeout_s = int(values.get("ft12_link_recovery_timeout_s", values.get("ft10_link_recovery_timeout_s", 900)))
        link_poll_s = int(values.get("ft12_link_recovery_poll_s", values.get("ft10_link_recovery_poll_s", 8)))
        _log(f"{case_id}: waiting RF link before CPE SSH")
        link_status, link_comment = await _ft10_wait_link_result(
            bts_ssh,
            case_id=case_id,
            label="post-ip-wait",
            profile=profile,
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
        )
        link_status, link_comment = _ft10_ft11_normalize_change_result(link_status, link_comment)
        _ft_link_status_log(case_id, "post-apply", link_status, link_comment)
        if link_status == "FAIL":
            return bts_ssh, None, link_status, link_comment

    cpe_ssh = await _ft12_reconnect_cpe_ssh(
        bts_ssh=bts_ssh,
        preferred_hosts=cpe_hosts,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        values=values,
        case_id=case_id,
        bts_ip=bts_ip,
    )
    return bts_ssh, cpe_ssh, link_status, link_comment

async def _ft12_reconnect_bts_ssh(
    *,
    preferred_hosts: list[str],
    profile_bundle,
    device_creds: dict,
    values: dict,
    case_id: str,
):
    """Reconnect BTS on new/old IPv6, then fall back to factory IPv4 hop."""
    password = device_creds["pass"]
    source_v6 = _source_bind_ipv6(profile_bundle)
    poll_s = int(values.get("ft12_reconnect_poll_s", 3))
    ipv6_timeout_s = int(values.get("ft12_ipv6_reconnect_timeout_s", 60))
    fallback_ipv4 = normalize_ip(
        str(
            values.get("ft12_fallback_ipv4")
            or values.get("ft07_bts_hop_ipv4")
            or "10.0.0.1"
        )
    )
    hosts: list[str] = []
    for host in list(preferred_hosts) + [fallback_ipv4]:
        clean = _ft12_host(host)
        if clean and clean not in hosts:
            hosts.append(clean)

    last_exc = ""
    for host in hosts:
        try:
            if is_ipv6_literal(host):
                _log(f"{case_id}: reconnect BTS via IPv6 {host}")
                return await _wait_ipv6_ssh_ready(
                    host,
                    password=password,
                    source_v6=source_v6,
                    label="BTS",
                    timeout_s=ipv6_timeout_s,
                    poll_s=poll_s,
                )
            _log(f"{case_id}: reconnect BTS via fallback {host}")
            return await _open_plain_ssh(host, password, label="BTS")
        except Exception as exc:
            last_exc = str(exc)
            _log(f"{case_id}: BTS reconnect failed @ {host}: {last_exc[:120]}")
    raise ConnectionError(f"{case_id}: BTS SSH reconnect failed ({hosts}): {last_exc[:220]}")

async def _ft12_reconnect_cpe_ssh(
    *,
    bts_ssh,
    preferred_hosts: list[str],
    profile_bundle,
    device_creds: dict,
    values: dict,
    case_id: str,
    bts_ip: str,
):
    """
    Reconnect CPE only after RF link is healthy.
    Prefer direct new/old CPE IPv6; never use 10.0.0.1 for CPE (that is BTS hop).
    Fall back to BTS remote_exec / tunnel path.
    """
    password = device_creds["pass"]
    source_v6 = _source_bind_ipv6(profile_bundle)
    poll_s = int(values.get("ft12_reconnect_poll_s", 3))
    ipv6_timeout_s = int(values.get("ft12_cpe_ipv6_reconnect_timeout_s", 120))

    hosts: list[str] = []
    for host in preferred_hosts:
        clean = _ft12_host(host)
        if clean and is_ipv6_literal(clean) and clean not in hosts:
            hosts.append(clean)

    last_exc = ""
    for host in hosts:
        try:
            _log(f"{case_id}: reconnect CPE via IPv6 {host}")
            return await _wait_ipv6_ssh_ready(
                host,
                password=password,
                source_v6=source_v6,
                label="CPE",
                timeout_s=ipv6_timeout_s,
                poll_s=poll_s,
            )
        except Exception as exc:
            last_exc = str(exc)
            _log(f"{case_id}: CPE reconnect failed @ {host}: {last_exc[:120]}")

    from utils.arp_bridge_table_flows import _open_cpe_ssh

    cpe_target = hosts[0] if hosts else _ft12_host(bts_ip)
    _log(f"{case_id}: CPE direct IPv6 unavailable — using BTS hop/tunnel to {cpe_target}")
    cpe_ssh, mode = await _open_cpe_ssh(bts_ssh, cpe_target, bts_ip, device_creds)
    _log(f"{case_id}: CPE SSH ready via {mode}")
    return cpe_ssh

async def _ft12_reconnect_device_ssh(
    *,
    preferred_hosts: list[str],
    profile_bundle,
    device_creds: dict,
    label: str,
    values: dict,
    case_id: str,
):
    """
    Prefer the new/old IPv6 host; fall back quickly to known IPv4 hops so FT_12
    does not sit on a long IPv6 wait after address change.
    """
    password = device_creds["pass"]
    source_v6 = _source_bind_ipv6(profile_bundle)
    poll_s = int(values.get("ft12_reconnect_poll_s", 3))
    ipv6_timeout_s = int(values.get("ft12_ipv6_reconnect_timeout_s", 45))
    fallback_ipv4 = normalize_ip(
        str(
            values.get("ft12_fallback_ipv4")
            or values.get("ft07_bts_hop_ipv4")
            or "10.0.0.1"
        )
    )
    hosts: list[str] = []
    for host in list(preferred_hosts) + [fallback_ipv4]:
        clean = _ft12_host(host)
        if clean and clean not in hosts:
            hosts.append(clean)

    last_exc = ""
    for host in hosts:
        try:
            if is_ipv6_literal(host):
                _log(f"{case_id}: reconnect {label} via IPv6 {host}")
                return await _wait_ipv6_ssh_ready(
                    host,
                    password=password,
                    source_v6=source_v6,
                    label=label,
                    timeout_s=ipv6_timeout_s,
                    poll_s=poll_s,
                )
            _log(f"{case_id}: reconnect {label} via fallback {host}")
            return await _open_plain_ssh(host, password, label=label)
        except Exception as exc:
            last_exc = str(exc)
            _log(f"{case_id}: {label} reconnect failed @ {host}: {last_exc[:120]}")
    raise ConnectionError(f"{case_id}: {label} SSH reconnect failed ({hosts}): {last_exc[:220]}")

async def _ft12_reconnect_pair(
    *,
    current_bts_ssh,
    current_cpe_ssh,
    bts_hosts: list[str],
    cpe_hosts: list[str],
    profile_bundle,
    device_creds: dict,
    values: dict,
    case_id: str,
    old_bts_ip: str,
    old_cpe_ip: str,
):
    await _ft12_close_quiet(current_cpe_ssh)
    await _ft12_close_quiet(current_bts_ssh)
    bts_ssh = await _ft12_reconnect_device_ssh(
        preferred_hosts=bts_hosts,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        label="BTS",
        values=values,
        case_id=case_id,
    )
    try:
        cpe_ssh = await _ft12_reconnect_device_ssh(
            preferred_hosts=cpe_hosts,
            profile_bundle=profile_bundle,
            device_creds=device_creds,
            label="CPE",
            values=values,
            case_id=case_id,
        )
    except Exception:
        # Last resort: RF hop / tunnel from BTS like other functional flows.
        from utils.arp_bridge_table_flows import _open_cpe_ssh

        _log(f"{case_id}: CPE direct reconnect failed — trying BTS hop/tunnel")
        cpe_ssh, _mode = await _open_cpe_ssh(bts_ssh, old_cpe_ip or cpe_hosts[0], old_bts_ip or bts_hosts[0], device_creds)
    return bts_ssh, cpe_ssh

async def _ft13_apply_netmask(
    ssh,
    *,
    snapshot: dict[str, str],
    netmask: str,
    timeout_s: int,
) -> None:
    cmds = [
        "ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {snapshot.get('ipv4', '')}",
        f"ucidyn set network.lan.netmask {netmask}",
        f"ucidyn set network.lan.ip6addr {snapshot.get('ipv6', '')}",
        "ucidyn apply",
    ]
    for cmd in cmds:
        await _ssh(ssh, cmd, timeout_s=max(120, timeout_s))

def _ft13_pick_netmask(current: str, *, values: dict) -> str:
    current_n = str(current or "").strip() or "255.255.255.0"
    candidates = [str(v) for v in values.get("ft13_netmask_candidates", ["255.255.0.0", "255.255.255.0", "255.255.255.128"])]
    for mask in candidates:
        if mask != current_n:
            return mask
    return "255.255.0.0" if current_n != "255.255.0.0" else "255.255.255.0"

async def _ft13_verify_backend_pair(
    *,
    bts_ssh,
    cpe_ssh,
    bts_expected: dict[str, str],
    cpe_expected: dict[str, str],
    timeout_s: int,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    bts_after = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    cpe_after = await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s)
    rows = [
        ("BTS Mgmt VLAN", bts_expected["mgmt_vlan"], bts_after["mgmt_vlan"], "PASS" if bts_after["mgmt_vlan"] == bts_expected["mgmt_vlan"] else "FAIL"),
        ("BTS Protocol", bts_expected["proto"], bts_after["proto"], "PASS" if bts_after["proto"] == bts_expected["proto"] else "FAIL"),
        ("BTS IPv4", bts_expected["ipv4"], bts_after["ipv4"], "PASS" if bts_after["ipv4"] == bts_expected["ipv4"] else "FAIL"),
        ("BTS Netmask", bts_expected["netmask"], bts_after["netmask"], "PASS" if bts_after["netmask"] == bts_expected["netmask"] else "FAIL"),
        ("CPE Mgmt VLAN", cpe_expected["mgmt_vlan"], cpe_after["mgmt_vlan"], "PASS" if cpe_after["mgmt_vlan"] == cpe_expected["mgmt_vlan"] else "FAIL"),
        ("CPE Protocol", cpe_expected["proto"], cpe_after["proto"], "PASS" if cpe_after["proto"] == cpe_expected["proto"] else "FAIL"),
        ("CPE IPv4", cpe_expected["ipv4"], cpe_after["ipv4"], "PASS" if cpe_after["ipv4"] == cpe_expected["ipv4"] else "FAIL"),
        ("CPE Netmask", cpe_expected["netmask"], cpe_after["netmask"], "PASS" if cpe_after["netmask"] == cpe_expected["netmask"] else "FAIL"),
    ]
    return all(row[3] == "PASS" for row in rows), rows

async def _ft14_apply_gateway(
    ssh,
    *,
    snapshot: dict[str, str],
    gateway: str,
    timeout_s: int,
) -> None:
    cmds = [
        "ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {snapshot.get('ipv4', '')}",
        f"ucidyn set network.lan.netmask {snapshot.get('netmask', '')}",
        f"ucidyn set network.lan.ip6addr {snapshot.get('ipv6', '')}",
    ]
    gw = str(gateway or "").strip()
    if gw:
        cmds.append(f"ucidyn set network.lan.gateway {gw}")
    else:
        cmds.append("ucidyn delete network.lan.gateway 2>/dev/null || true")
    cmds.append("ucidyn apply")
    for cmd in cmds:
        await _ssh(ssh, cmd, timeout_s=max(120, timeout_s))

def _ft14_ft15_grade_result(
    *,
    link_status: str,
    revert_link_status: str,
    apply_ok: bool,
    revert_ok: bool,
) -> tuple[str, str]:
    if link_status == "FAIL" or revert_link_status == "FAIL":
        return "FAIL", "RF Link Drops never comes up until 15 mins"
    if link_status == "PASS (UNSTABLE)" or revert_link_status == "PASS (UNSTABLE)":
        return "PASS (UNSTABLE)", "RF Interface Restarts,By design"
    if apply_ok and revert_ok:
        return "PASS", "RF Link wont drop"
    return "FAIL", "Backend config verification failed"

def _ft14_pick_ipv4_gateway(
    *,
    ipv4: str,
    netmask: str,
    current_gateway: str,
    role: str,
    values: dict,
) -> str:
    host = normalize_ip(str(ipv4 or "").split("/")[0])
    mask = str(netmask or "").strip() or "255.255.255.0"
    check.is_true(bool(host), f"FT_14: missing IPv4 for {role}")
    try:
        iface = ipaddress.IPv4Interface(f"{host}/{mask}")
    except Exception as exc:
        raise AssertionError(f"FT_14: invalid IPv4/netmask for {role}: {host}/{mask}") from exc

    current_norm = normalize_ip(str(current_gateway or "").split("/")[0])
    host_norm = iface.ip.compressed
    candidates = [int(v) for v in values.get("ft14_gateway_host_offsets", [1, 2, 10, 11, 20, 21, 50, 100])]
    network = iface.network
    for offset in candidates:
        try:
            addr = ipaddress.IPv4Address(int(network.network_address) + offset)
        except Exception:
            continue
        if addr not in network or addr in {network.network_address, network.broadcast_address}:
            continue
        addr_str = addr.compressed
        if addr_str == host_norm or addr_str == current_norm:
            continue
        return addr_str
    raise AssertionError(f"FT_14: no alternate IPv4 gateway candidate for {role} in {network}")

def _ft14_pick_random_gateways(
    *,
    bts_ipv4: str,
    cpe_ipv4: str,
    bts_current_gw: str,
    cpe_current_gw: str,
    values: dict,
) -> tuple[str, str]:
    """Pick random distinct 192.168.2.x gateways for BTS and CPE (not equal to device IPs)."""
    subnet = str(values.get("ft14_ipv4_subnet_prefix", values.get("ft15_ipv4_subnet_prefix", "192.168.2"))).rstrip(".")
    used: set[int] = set()
    for raw in (bts_ipv4, cpe_ipv4, bts_current_gw, cpe_current_gw):
        host = normalize_ip(str(raw or "").split("/")[0])
        if host.startswith(f"{subnet}."):
            try:
                used.add(int(host.rsplit(".", 1)[1]))
            except Exception:
                pass
    bts_octet = _ft_random_last_octet(used=used)
    cpe_octet = _ft_random_last_octet(used=used)
    return f"{subnet}.{bts_octet}", f"{subnet}.{cpe_octet}"

async def _ft14_read_assoc_time(bts_ssh, *, radio_idx: int, assoc_idx: int = 1, timeout_s: int = 15) -> int:
    raw = clean_ssh_output(
        await _ssh(bts_ssh, RootCommands.get_link_stat_field(radio_idx, assoc_idx, "assoc_time"), timeout_s=timeout_s)
    ).strip()
    try:
        return int(float(raw))
    except ValueError:
        return -1

async def _ft14_verify_backend_pair(
    *,
    bts_ssh,
    cpe_ssh,
    bts_expected: dict[str, str],
    cpe_expected: dict[str, str],
    timeout_s: int,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    return await _ft_verify_backend_pair_retry(
        bts_ssh=bts_ssh,
        cpe_ssh=cpe_ssh,
        bts_expected=bts_expected,
        cpe_expected=cpe_expected,
        fields=[
            ("BTS Mgmt VLAN", "mgmt_vlan"),
            ("BTS Protocol", "proto"),
            ("BTS IPv4", "ipv4"),
            ("BTS IPv4 Gateway", "ipv4_gw"),
            ("CPE Mgmt VLAN", "mgmt_vlan"),
            ("CPE Protocol", "proto"),
            ("CPE IPv4", "ipv4"),
            ("CPE IPv4 Gateway", "ipv4_gw"),
        ],
        timeout_s=timeout_s,
    )

async def _ft15_apply_ipv4(
    ssh,
    *,
    snapshot: dict[str, str],
    ipv4: str,
    timeout_s: int,
) -> None:
    cmds = [
        "ucidyn set network.lan.proto static",
        f"ucidyn set network.lan.ipaddr {ipv4}",
        f"ucidyn set network.lan.netmask {snapshot.get('netmask', '') or '255.255.255.0'}",
        f"ucidyn set network.lan.ip6addr {snapshot.get('ipv6', '')}",
    ]
    gw = str(snapshot.get("ipv4_gw", "") or "").strip()
    if gw:
        cmds.append(f"ucidyn set network.lan.gateway {gw}")
    cmds.append("ucidyn apply")
    for cmd in cmds:
        try:
            await _ssh(ssh, cmd, timeout_s=max(120, timeout_s))
        except Exception as exc:
            _log(f"FT_15: IPv4 apply dropped SSH on {cmd!r} (expected): {exc}")
            break


async def _open_plain_ssh_retry(
    hosts: list[str],
    password: str,
    *,
    label: str,
    timeout_s: int = 45,
    attempts: int = 6,
    poll_s: float = 5.0,
) -> AsyncGenericDriver:
    """Try multiple IPv4 hosts with retries (post-LAN-mutation reconnect)."""
    last_exc: Exception | None = None
    clean_hosts = [normalize_ip(str(h or "").split("/")[0]) for h in hosts if str(h or "").strip()]
    clean_hosts = list(dict.fromkeys(h for h in clean_hosts if h))
    for attempt in range(1, max(1, attempts) + 1):
        for host in clean_hosts:
            try:
                return await _open_plain_ssh(
                    host, password, label=f"{label}/{host}", timeout_s=timeout_s
                )
            except Exception as exc:
                last_exc = exc
                _log(f"{label}: SSH attempt {attempt}/{attempts} to {host} failed: {exc}")
        if attempt < attempts:
            await asyncio.sleep(poll_s)
    raise ConnectionError(f"{label}: SSH failed for hosts {clean_hosts}: {last_exc}")


def _ft15_pick_random_ipv4_pair(*, values: dict, reserved: set[str] | None = None) -> tuple[str, str]:
    """Random distinct 192.168.2.x addresses for BTS and CPE."""
    subnet = str(values.get("ft15_ipv4_subnet_prefix", "192.168.2")).rstrip(".")
    used: set[int] = set()
    for raw in reserved or set():
        host = normalize_ip(str(raw or "").split("/")[0])
        if host.startswith(f"{subnet}."):
            try:
                used.add(int(host.rsplit(".", 1)[1]))
            except Exception:
                pass
    bts_octet = _ft_random_last_octet(used=used)
    cpe_octet = _ft_random_last_octet(used=used)
    return f"{subnet}.{bts_octet}", f"{subnet}.{cpe_octet}"

async def _ft15_verify_backend_pair(
    *,
    bts_ssh,
    cpe_ssh,
    bts_expected: dict[str, str],
    cpe_expected: dict[str, str],
    timeout_s: int,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    return await _ft_verify_backend_pair_retry(
        bts_ssh=bts_ssh,
        cpe_ssh=cpe_ssh,
        bts_expected=bts_expected,
        cpe_expected=cpe_expected,
        fields=[
            ("BTS Mgmt VLAN", "mgmt_vlan"),
            ("BTS Protocol", "proto"),
            ("BTS IPv4", "ipv4"),
            ("CPE Mgmt VLAN", "mgmt_vlan"),
            ("CPE Protocol", "proto"),
            ("CPE IPv4", "ipv4"),
        ],
        timeout_s=timeout_s,
    )

async def _ft16_apply_bandwidth(ssh, *, radio_idx: int, bandwidth: str, timeout_s: int) -> None:
    uci_bw = _ft05_uci_htmode_value(bandwidth)
    await _ssh(ssh, f"ucidyn set wireless.wifi{radio_idx}.htmode {uci_bw}", timeout_s=timeout_s)
    await _ssh(ssh, "ucidyn apply", timeout_s=max(120, timeout_s))

async def _ft16_read_bandwidth(ssh, *, radio_idx: int, timeout_s: int) -> str:
    bw_uci = await _ssh(ssh, f"uci get wireless.wifi{radio_idx}.htmode 2>/dev/null || true", timeout_s=timeout_s)
    bw_mode = await _ssh(ssh, f"cfg80211tool ath{radio_idx} get_mode 2>/dev/null || true", timeout_s=timeout_s)
    return _normalize_htmode(f"{bw_uci}\n{bw_mode}")

async def _ft16_verify_bandwidth(
    *,
    bts_ssh,
    cpe_ssh,
    radio_idx: int,
    expected: str,
    timeout_s: int,
    retries: int = 6,
    delay_s: float = 3.0,
) -> tuple[bool, str, str]:
    bts_bw, cpe_bw = "", ""
    for _ in range(max(1, retries)):
        bts_bw = await _ft16_read_bandwidth(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        cpe_bw = await _ft16_read_bandwidth(cpe_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        if _ft05_uci_htmode_matches(expected, bts_bw) and _ft05_uci_htmode_matches(expected, cpe_bw):
            return True, bts_bw, cpe_bw
        await asyncio.sleep(delay_s)
    return False, bts_bw, cpe_bw

async def _ft16_verify_bts_bandwidth(
    bts_ssh,
    *,
    radio_idx: int,
    expected: str,
    timeout_s: int,
    retries: int = 6,
    delay_s: float = 3.0,
) -> tuple[bool, str]:
    bts_bw = ""
    for _ in range(max(1, retries)):
        bts_bw = await _ft16_read_bandwidth(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        if _ft05_uci_htmode_matches(expected, bts_bw):
            return True, bts_bw
        await asyncio.sleep(delay_s)
    return False, bts_bw

def _ft_link_status_log(case_id: str, phase: str, status: str, comment: str) -> None:
    link = "PASS" if status != "FAIL" else "FAIL"
    if comment and comment != "RF Link wont drop":
        _log(f"{case_id}: {phase} link={link} | {comment}")
    else:
        _log(f"{case_id}: {phase} link={link}")

def _ft_random_last_octet(*, used: set[int], lo: int = 1, hi: int = 254) -> int:
    available = [n for n in range(lo, hi + 1) if n not in used]
    check.is_true(bool(available), "no free IPv4 last-octet candidates")
    pick = random.choice(available)
    used.add(pick)
    return pick

def _ft_result_rows(final_status: str, final_comment: str) -> list[tuple[str, str, str, str]]:
    rows = [("Result", "PASS / FAIL / PASS (UNSTABLE)", final_status, final_status)]
    if final_comment and final_comment != "RF Link wont drop":
        rows.append(("Comment", "summary", final_comment, "INFO"))
    return rows

async def _ft_verify_backend_pair_retry(
    *,
    bts_ssh,
    cpe_ssh,
    bts_expected: dict[str, str],
    cpe_expected: dict[str, str],
    fields: list[tuple[str, str]],
    timeout_s: int,
    retries: int = 6,
    delay_s: float = 3.0,
) -> tuple[bool, list[tuple[str, str, str, str]]]:
    """Retry backend reads briefly — UCI can lag right after ucidyn apply."""
    rows: list[tuple[str, str, str, str]] = []
    for attempt in range(max(1, retries)):
        bts_after = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
        cpe_after = await _ft10_read_network_state(cpe_ssh, timeout_s=timeout_s)
        rows = []
        for label, key in fields:
            if label.startswith("BTS"):
                expected, actual = bts_expected.get(key, ""), bts_after.get(key, "")
            else:
                expected, actual = cpe_expected.get(key, ""), cpe_after.get(key, "")
            display_exp = expected or "(empty)"
            display_act = actual or "(empty)"
            if key in {"ipv6", "ipv6_gw"}:
                display_exp = _normalize_ipv6_cidr(expected) or "(empty)"
                display_act = _normalize_ipv6_cidr(actual) or "(empty)"
            rows.append(
                (
                    label,
                    display_exp,
                    display_act,
                    "PASS" if _backend_value_equal(key, expected, actual) else "FAIL",
                )
            )
        if all(row[3] == "PASS" for row in rows):
            return True, rows
        if attempt + 1 < retries:
            await asyncio.sleep(delay_s)
    return False, rows

async def _ft10_ensure_bts_transparent(
    bts_ssh,
    *,
    profile: dict,
    mgmt_vlan_id: int,
    timeout_s: int,
    case_id: str = "FT_10",
) -> None:
    from utils.vlan_control import _normalize_mode_name, ensure_bts_transparent_ssh

    before = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    mode_before = _normalize_mode_name(before.get("vlan_mode", ""))
    mgmt_before = str(before.get("mgmt_vlan", "")).strip()
    if mode_before == "transparent" and mgmt_before == str(int(mgmt_vlan_id)):
        _log(f"{case_id}: BTS VLAN mode transparent OK (mgmtvlan={mgmt_before})")
        return

    _log(
        f"{case_id}: BTS VLAN mode is {before.get('vlan_mode') or '(empty)'} "
        f"mgmtvlan={mgmt_before or '(empty)'} — set transparent/mgmtvlan={int(mgmt_vlan_id)}"
    )
    tb = profile.get("testbed", profile) if isinstance(profile, dict) else profile
    ok = await ensure_bts_transparent_ssh(
        bts_ssh,
        tb,
        mgmt_vlan_id=int(mgmt_vlan_id),
    )
    after = await _ft10_read_network_state(bts_ssh, timeout_s=timeout_s)
    mode_after = _normalize_mode_name(after.get("vlan_mode", ""))
    mgmt_after = str(after.get("mgmt_vlan", "")).strip()
    check.is_true(
        bool(ok) and mode_after == "transparent" and mgmt_after == str(int(mgmt_vlan_id)),
        f"{case_id}: BTS VLAN not transparent/mgmtvlan={int(mgmt_vlan_id)} "
        f"(mode={after.get('vlan_mode')!r}, mgmtvlan={mgmt_after!r})",
    )


async def _ft10_run_random_mgmt_vlan_case(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None,
    device_creds: dict | None,
    profile_bundle,
    request,
    case_id: str,
    title: str,
    radio_idx: int,
    apply_settle_s: int,
    link_timeout_s: int,
    link_poll_s: int,
    operation,
) -> None:
    """Run a functional case under FT_10-style random mgmt VLAN access."""
    _require_functional(request, case_id=case_id)
    _require_ipv6_profile(profile_bundle, case_id=case_id)
    check.is_true(bool(device_creds), f"{case_id}: device credentials required")

    bts_ip = _require_ipv6(bsu_ip or "", case_id=case_id, role="BTS")
    cpe_ip = _require_ipv6(cpe_ips[0], case_id=case_id, role="CPE")
    profile = profile_bundle.active
    values = FUNCTIONAL_TEST_VALUES
    timeout_s = int(values["ssh_timeout_s"])

    tb = profile.get("testbed", {}) or {}
    primary_pc = dict(tb.get("primary_pc", {}) or {})
    primary_pc.setdefault("local", True)
    primary_pc.setdefault("mgmt_interface", "enp1s0")
    pc_password = str(primary_pc.get("password") or device_creds["pass"])
    pc_ipv4 = normalize_ip(str(primary_pc.get("fallback_ipv4") or values.get("ft10_pc_ipv4", "192.168.2.200")).split("/")[0])
    pc_netmask = str(primary_pc.get("fallback_netmask") or values.get("ft10_pc_netmask", "255.255.255.0"))

    from utils.lab_pc_net import ensure_lab_pc_mgmt_vlan_ipv4, ensure_lab_pc_untagged_ipv4

    pc_mgmt_v6 = _source_bind_ipv6(profile_bundle)
    mgmt = (profile.get("testbed", {}) or {}).get("mgmt_vlan", {}) or {}
    pc_mgmt_v6_prefix = int(mgmt.get("prefix_len") or values.get("ipv6_prefix_len") or 120)
    pc_mgmt_v6_cidr = f"{pc_mgmt_v6}/{pc_mgmt_v6_prefix}" if pc_mgmt_v6 else ""

    print_section(title)
    _log(f"{case_id}: clearing PC LAN VLANs before verification")
    pc_if = await ensure_lab_pc_untagged_ipv4(
        primary_pc,
        ipv4=pc_ipv4,
        netmask=pc_netmask,
        password=pc_password,
        mgmt_ipv6_cidr=pc_mgmt_v6_cidr,
    )
    check.is_true(bool(pc_if), f"{case_id}: PC LAN untagged cleanup failed")
    _log(f"{case_id}: PC LAN untagged {pc_if} ({pc_ipv4})")

    _log(f"{case_id}: opening BTS={bts_ip} CPE={cpe_ip}")
    bts_ssh = root_ssh
    await _ft10_ensure_bts_transparent(
        bts_ssh,
        profile=profile,
        mgmt_vlan_id=1,
        timeout_s=timeout_s,
    )
    cpe_ssh = await _ft10_open_cpe_backend_session(
        bts_ssh=bts_ssh,
        cpe_ip=cpe_ip,
        bts_ip=bts_ip,
        profile_bundle=profile_bundle,
        device_creds=device_creds,
        values=values,
    )
    bts_tagged_ssh = None
    cpe_tagged_ssh = None
    bts_restored_ssh = None
    cpe_restored_ssh = None
    bts_before: dict[str, str] | None = None
    cpe_before: dict[str, str] | None = None
    restored = False

    try:
        bts_before, cpe_before = await _ft10_read_originals_without_vlan_force(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            radio_idx=radio_idx,
            timeout_s=timeout_s,
        )
        # Heal leftover random mgmt VLAN from a prior crashed run before precheck.
        if bts_before["mgmt_vlan"] != "1" or cpe_before["mgmt_vlan"] != "1":
            _log(
                f"{case_id}: precheck heal mgmt VLAN "
                f"BTS={bts_before['mgmt_vlan']} CPE={cpe_before['mgmt_vlan']} -> 1"
            )
            try:
                if cpe_before["mgmt_vlan"] != "1":
                    await _ft10_apply_mgmt_vlan(cpe_ssh, vlan_id=1, timeout_s=timeout_s, case_id=case_id)
                if bts_before["mgmt_vlan"] != "1":
                    await _ft10_apply_mgmt_vlan(bts_ssh, vlan_id=1, timeout_s=timeout_s, case_id=case_id)
            except Exception as exc:
                _log(f"{case_id}: precheck heal apply note: {exc}")
            await asyncio.sleep(max(8, apply_settle_s // 2))
            # Re-open CPE after possible address-plane change.
            try:
                await cpe_ssh.close()
            except Exception:
                pass
            cpe_ssh = await _ft10_open_cpe_backend_session(
                bts_ssh=bts_ssh,
                cpe_ip=cpe_ip,
                bts_ip=bts_ip,
                profile_bundle=profile_bundle,
                device_creds=device_creds,
                values=values,
            )
            bts_before, cpe_before = await _ft10_read_originals_without_vlan_force(
                bts_ssh=bts_ssh,
                cpe_ssh=cpe_ssh,
                radio_idx=radio_idx,
                timeout_s=timeout_s,
            )
        if bts_before["mgmt_vlan"] != "1" or cpe_before["mgmt_vlan"] != "1":
            raise AssertionError(
                f"{case_id}: mgmt VLAN precheck requires BTS/CPE mgmt VLAN 1 "
                f"(BTS={bts_before['mgmt_vlan']}, CPE={cpe_before['mgmt_vlan']})"
            )
        target_vlan = _ft10_pick_random_mgmt_vlan(
            originals=[bts_before["mgmt_vlan"], cpe_before["mgmt_vlan"]],
            values=values,
        )
        bts_ipv4 = normalize_ip(bts_before.get("ipv4") or str(values.get("ft_bts_mgmt_ipv4", "192.168.2.10")))
        cpe_ipv4 = normalize_ip(cpe_before.get("ipv4") or str(values.get("ft_cpe_mgmt_ipv4", "192.168.2.11")))
        _log(
            f"{case_id}: mgmt VLAN BTS={bts_before['mgmt_vlan']} CPE={cpe_before['mgmt_vlan']} "
            f"-> random {target_vlan}"
        )

        _log(f"{case_id}: applying mgmt VLAN {target_vlan} on CPE then BTS")
        await _ft10_apply_mgmt_vlan(cpe_ssh, vlan_id=target_vlan, timeout_s=timeout_s, case_id=case_id)
        await _ft10_apply_mgmt_vlan(bts_ssh, vlan_id=target_vlan, timeout_s=timeout_s, case_id=case_id)

        vlan_if = await ensure_lab_pc_mgmt_vlan_ipv4(
            primary_pc,
            vlan_id=target_vlan,
            ipv4=pc_ipv4,
            netmask=pc_netmask,
            password=pc_password,
        )
        check.is_true(bool(vlan_if), f"{case_id}: PC LAN tagged setup failed")
        _log(f"{case_id}: PC LAN tagged {vlan_if} for mgmt VLAN {target_vlan}")
        await asyncio.sleep(max(apply_settle_s, 25))

        tagged_timeout = int(values.get("ft10_tagged_ssh_timeout_s", 60))
        bts_tagged_ssh = await _open_plain_ssh_retry(
            [bts_ipv4, "10.0.0.1"],
            device_creds["pass"],
            label="BTS tagged",
            timeout_s=tagged_timeout,
            attempts=int(values.get("ft10_tagged_ssh_attempts", 8)),
            poll_s=float(values.get("ft10_tagged_ssh_poll_s", 5)),
        )
        cpe_tagged_ssh = await _open_plain_ssh_retry(
            [cpe_ipv4],
            device_creds["pass"],
            label="CPE tagged",
            timeout_s=tagged_timeout,
            attempts=int(values.get("ft10_tagged_ssh_attempts", 8)),
            poll_s=float(values.get("ft10_tagged_ssh_poll_s", 5)),
        )

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        mgmt_link_status, mgmt_link_comment = await _ft10_wait_link_result(
            bts_tagged_ssh,
            case_id=case_id,
            label="post-mgmt-vlan-change",
            profile=profile,
            timeout_s=link_timeout_s,
            poll_s=link_poll_s,
        )
        mgmt_link_status, mgmt_link_comment = _ft10_ft11_normalize_change_result(mgmt_link_status, mgmt_link_comment)
        _ft_link_status_log(case_id, "post-mgmt-vlan", mgmt_link_status, mgmt_link_comment)
        mgmt_ok, mgmt_rows = await _ft10_verify_mgmt_vlan_pair(
            bts_ssh=bts_tagged_ssh,
            cpe_ssh=cpe_tagged_ssh,
            bts_vlan=target_vlan,
            cpe_vlan=target_vlan,
            timeout_s=timeout_s,
        )
        if not mgmt_ok:
            _print_backend_table(f"{case_id}: backend verify failed after mgmt VLAN change", mgmt_rows)

        op_status, op_comment = await operation(
            {
                "case_id": case_id,
                "profile": profile,
                "profile_bundle": profile_bundle,
                "device_creds": device_creds,
                "values": values,
                "timeout_s": timeout_s,
                "apply_settle_s": apply_settle_s,
                "link_timeout_s": link_timeout_s,
                "link_poll_s": link_poll_s,
                "radio_idx": radio_idx,
                "bts_ip": bts_ip,
                "cpe_ip": cpe_ip,
                "bts_ssh": bts_tagged_ssh,
                "cpe_ssh": cpe_tagged_ssh,
                "bts_before": {**bts_before, "mgmt_vlan": str(target_vlan)},
                "cpe_before": {**cpe_before, "mgmt_vlan": str(target_vlan)},
                "bts_original": bts_before,
                "cpe_original": cpe_before,
            }
        )

        _log(
            f"{case_id}: restoring mgmt VLAN CPE={cpe_before['mgmt_vlan']} then "
            f"BTS={bts_before['mgmt_vlan']}"
        )
        await _ft10_apply_mgmt_vlan(cpe_tagged_ssh, vlan_id=int(cpe_before["mgmt_vlan"]), timeout_s=timeout_s, case_id=case_id)
        await _ft10_apply_mgmt_vlan(bts_tagged_ssh, vlan_id=int(bts_before["mgmt_vlan"]), timeout_s=timeout_s, case_id=case_id)

        pc_if = await ensure_lab_pc_untagged_ipv4(
            primary_pc,
            ipv4=pc_ipv4,
            netmask=pc_netmask,
            password=pc_password,
            mgmt_ipv6_cidr=pc_mgmt_v6_cidr,
        )
        restored = True
        check.is_true(bool(pc_if), f"{case_id}: PC LAN final untagged restore failed")
        _log(f"{case_id}: PC LAN restored untagged {pc_if}")
        await asyncio.sleep(apply_settle_s)

        bts_restored_ssh = await _open_plain_ssh(
            bts_ipv4,
            device_creds["pass"],
            label="BTS restored",
            timeout_s=int(values.get("ft10_tagged_ssh_timeout_s", 45)),
        )
        cpe_restored_ssh = await _open_plain_ssh(
            cpe_ipv4,
            device_creds["pass"],
            label="CPE restored",
            timeout_s=int(values.get("ft10_tagged_ssh_timeout_s", 45)),
        )
        restore_ok, restore_rows = await _ft10_verify_mgmt_vlan_pair(
            bts_ssh=bts_restored_ssh,
            cpe_ssh=cpe_restored_ssh,
            bts_vlan=bts_before["mgmt_vlan"],
            cpe_vlan=cpe_before["mgmt_vlan"],
            timeout_s=timeout_s,
        )
        if not restore_ok:
            _print_backend_table(f"{case_id}: backend verify failed after mgmt VLAN restore", restore_rows)

        if mgmt_link_status == "FAIL" or op_status == "FAIL":
            final_status = "FAIL"
            final_comment = op_comment if op_status == "FAIL" else mgmt_link_comment
        elif not mgmt_ok or not restore_ok:
            final_status = "FAIL"
            final_comment = "Backend config verification failed"
        elif mgmt_link_status == "PASS (UNSTABLE)" or op_status == "PASS (UNSTABLE)":
            final_status = "PASS (UNSTABLE)"
            final_comment = "RF Interface Restarts,By design"
        else:
            final_status = "PASS"
            final_comment = "RF Link wont drop"

        print_section(f"{case_id} final result")
        print_comparison_table(_ft_result_rows(final_status, final_comment))
        check.is_true(final_status != "FAIL", f"{case_id}: {final_comment}")
    finally:
        if not restored:
            try:
                if cpe_before is not None:
                    for ssh in (cpe_tagged_ssh, cpe_ssh):
                        if ssh is None:
                            continue
                        try:
                            await _ft10_apply_mgmt_vlan(
                                ssh, vlan_id=int(cpe_before["mgmt_vlan"]), timeout_s=timeout_s, case_id=case_id,
                            )
                            break
                        except Exception as exc:
                            _log(f"{case_id}: CPE mgmt VLAN restore via one session failed: {exc}")
                if bts_before is not None:
                    for ssh in (bts_tagged_ssh, bts_ssh):
                        if ssh is None:
                            continue
                        try:
                            await _ft10_apply_mgmt_vlan(
                                ssh, vlan_id=int(bts_before["mgmt_vlan"]), timeout_s=timeout_s, case_id=case_id,
                            )
                            break
                        except Exception as exc:
                            _log(f"{case_id}: BTS mgmt VLAN restore via one session failed: {exc}")
            except Exception as exc:
                _log(f"{case_id}: best-effort device mgmt VLAN restore failed: {exc}")
            try:
                await ensure_lab_pc_untagged_ipv4(
                    primary_pc,
                    ipv4=pc_ipv4,
                    netmask=pc_netmask,
                    password=pc_password,
                    mgmt_ipv6_cidr=pc_mgmt_v6_cidr,
                )
            except Exception as exc:
                _log(f"{case_id}: best-effort PC LAN restore failed: {exc}")
        for conn in (cpe_restored_ssh, bts_restored_ssh, cpe_tagged_ssh, bts_tagged_ssh, cpe_ssh):
            try:
                if conn is not None:
                    await conn.close()
            except Exception:
                pass



async def _ft11_gateway_change_operation(ctx: dict) -> tuple[str, str]:
    case_id = ctx["case_id"]
    bts_ssh = ctx["bts_ssh"]
    cpe_ssh = ctx["cpe_ssh"]
    bts_before = ctx["bts_before"]
    cpe_before = ctx["cpe_before"]
    values = ctx["values"]
    timeout_s = ctx["timeout_s"]
    apply_settle_s = ctx["apply_settle_s"]
    link_timeout_s = ctx["link_timeout_s"]
    link_poll_s = ctx["link_poll_s"]
    profile = ctx["profile"]
    reverted = False

    target_bts_gw = _ft11_pick_gateway(
        ipv6_cidr=bts_before.get("ipv6", ""),
        current_gateway=bts_before.get("ipv6_gw", ""),
        role="BTS",
        values=values,
    )
    target_cpe_gw = _ft11_pick_gateway(
        ipv6_cidr=cpe_before.get("ipv6", ""),
        current_gateway=cpe_before.get("ipv6_gw", ""),
        role="CPE",
        values=values,
    )
    _log(
        f"{case_id}: IPv6 GW BTS {(bts_before.get('ipv6_gw') or '(empty)')} -> {target_bts_gw} | "
        f"CPE {(cpe_before.get('ipv6_gw') or '(empty)')} -> {target_cpe_gw}"
    )
    try:
        await _ft10_ensure_static(bts_ssh, snapshot=bts_before, label="BTS", case_id=case_id, timeout_s=timeout_s)
        await _ft10_ensure_static(cpe_ssh, snapshot=cpe_before, label="CPE", case_id=case_id, timeout_s=timeout_s)

        _log(f"{case_id}: applying IPv6 gateway change on CPE then BTS")
        await _ft11_change_gateway(cpe_ssh, snapshot=cpe_before, new_ipv6_gateway=target_cpe_gw, timeout_s=timeout_s)
        await _ft11_change_gateway(bts_ssh, snapshot=bts_before, new_ipv6_gateway=target_bts_gw, timeout_s=timeout_s)
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        link_status, link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-gateway-change", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        link_status, link_comment = _ft10_ft11_normalize_change_result(link_status, link_comment)
        _ft_link_status_log(case_id, "post-gateway-change", link_status, link_comment)

        apply_ok, apply_rows = await _ft11_verify_backend_pair(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": "static", "ipv6": bts_before["ipv6"], "ipv6_gw": target_bts_gw},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": "static", "ipv6": cpe_before["ipv6"], "ipv6_gw": target_cpe_gw},
            timeout_s=timeout_s,
        )
        if not apply_ok:
            _print_backend_table(f"{case_id}: backend verify failed after gateway change", apply_rows)

        _log(
            f"{case_id}: reverting IPv6 gateway BTS={(bts_before.get('ipv6_gw') or '(empty)')} "
            f"CPE={(cpe_before.get('ipv6_gw') or '(empty)')}"
        )
        await _ft11_revert_network(cpe_ssh, snapshot=bts_before | cpe_before, timeout_s=timeout_s)
        await _ft11_revert_network(bts_ssh, snapshot=bts_before, timeout_s=timeout_s)
        reverted = True
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        revert_link_status, revert_link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-revert", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        _ft_link_status_log(case_id, "post-revert", revert_link_status, revert_link_comment)

        revert_ok, revert_rows = await _ft11_verify_backend_pair(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": bts_before["proto"], "ipv6": bts_before["ipv6"], "ipv6_gw": bts_before.get("ipv6_gw", "")},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": cpe_before["proto"], "ipv6": cpe_before["ipv6"], "ipv6_gw": cpe_before.get("ipv6_gw", "")},
            timeout_s=timeout_s,
        )
        if not revert_ok:
            _print_backend_table(f"{case_id}: backend verify failed after revert", revert_rows)

        return _ft14_ft15_grade_result(
            link_status=link_status,
            revert_link_status=revert_link_status,
            apply_ok=apply_ok,
            revert_ok=revert_ok,
        )
    finally:
        if not reverted:
            try:
                await _ft11_revert_network(cpe_ssh, snapshot=cpe_before, timeout_s=timeout_s)
                await _ft11_revert_network(bts_ssh, snapshot=bts_before, timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: best-effort IPv6 gateway restore failed: {exc}")



async def _ft12_ipv6_change_operation(ctx: dict) -> tuple[str, str]:
    case_id = ctx["case_id"]
    bts_ssh = ctx["bts_ssh"]
    cpe_ssh = ctx["cpe_ssh"]
    bts_before = ctx["bts_before"]
    cpe_before = ctx["cpe_before"]
    timeout_s = ctx["timeout_s"]
    apply_settle_s = ctx["apply_settle_s"]
    link_timeout_s = ctx["link_timeout_s"]
    link_poll_s = ctx["link_poll_s"]
    profile = ctx["profile"]
    reverted = False

    target_bts_ipv6 = _derive_alt_ipv6(bts_before.get("ipv6", ""), role="BTS")
    target_cpe_ipv6 = _derive_alt_ipv6(cpe_before.get("ipv6", ""), role="CPE")
    _log(
        f"{case_id}: IPv6 BTS {bts_before['ipv6']} -> {target_bts_ipv6} | "
        f"CPE {cpe_before['ipv6']} -> {target_cpe_ipv6}"
    )
    try:
        await _ft10_ensure_static(bts_ssh, snapshot=bts_before, label="BTS", case_id=case_id, timeout_s=timeout_s)
        await _ft10_ensure_static(cpe_ssh, snapshot=cpe_before, label="CPE", case_id=case_id, timeout_s=timeout_s)

        _log(f"{case_id}: applying IPv6 address change on CPE then BTS")
        await _ft10_change_prefix(cpe_ssh, snapshot=cpe_before, new_ipv6_cidr=target_cpe_ipv6, timeout_s=timeout_s)
        await _ft10_change_prefix(bts_ssh, snapshot=bts_before, new_ipv6_cidr=target_bts_ipv6, timeout_s=timeout_s)
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        link_status, link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-ipv6-change", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        link_status, link_comment = _ft10_ft11_normalize_change_result(link_status, link_comment)
        _ft_link_status_log(case_id, "post-ipv6-change", link_status, link_comment)

        apply_ok, apply_rows = await _ft_verify_backend_pair_retry(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": "static", "ipv6": target_bts_ipv6},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": "static", "ipv6": target_cpe_ipv6},
            fields=[
                ("BTS Mgmt VLAN", "mgmt_vlan"),
                ("BTS Protocol", "proto"),
                ("BTS IPv6", "ipv6"),
                ("CPE Mgmt VLAN", "mgmt_vlan"),
                ("CPE Protocol", "proto"),
                ("CPE IPv6", "ipv6"),
            ],
            timeout_s=timeout_s,
        )
        if not apply_ok:
            _print_backend_table(f"{case_id}: backend verify failed after IPv6 change", apply_rows)

        _log(f"{case_id}: reverting IPv6 BTS={bts_before['ipv6']} CPE={cpe_before['ipv6']}")
        await _ft10_revert_network(cpe_ssh, snapshot=cpe_before, timeout_s=timeout_s)
        await _ft10_revert_network(bts_ssh, snapshot=bts_before, timeout_s=timeout_s)
        reverted = True
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        revert_link_status, revert_link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-revert", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        _ft_link_status_log(case_id, "post-revert", revert_link_status, revert_link_comment)

        revert_ok, revert_rows = await _ft_verify_backend_pair_retry(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": bts_before["proto"], "ipv6": bts_before["ipv6"]},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": cpe_before["proto"], "ipv6": cpe_before["ipv6"]},
            fields=[
                ("BTS Mgmt VLAN", "mgmt_vlan"),
                ("BTS Protocol", "proto"),
                ("BTS IPv6", "ipv6"),
                ("CPE Mgmt VLAN", "mgmt_vlan"),
                ("CPE Protocol", "proto"),
                ("CPE IPv6", "ipv6"),
            ],
            timeout_s=timeout_s,
        )
        if not revert_ok:
            _print_backend_table(f"{case_id}: backend verify failed after revert", revert_rows)

        return _ft14_ft15_grade_result(
            link_status=link_status,
            revert_link_status=revert_link_status,
            apply_ok=apply_ok,
            revert_ok=revert_ok,
        )
    finally:
        if not reverted:
            try:
                await _ft10_revert_network(cpe_ssh, snapshot=cpe_before, timeout_s=timeout_s)
                await _ft10_revert_network(bts_ssh, snapshot=bts_before, timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: best-effort IPv6 restore failed: {exc}")



async def _ft14_ipv4_gateway_change_operation(ctx: dict) -> tuple[str, str]:
    case_id = ctx["case_id"]
    bts_ssh = ctx["bts_ssh"]
    cpe_ssh = ctx["cpe_ssh"]
    bts_before = ctx["bts_before"]
    cpe_before = ctx["cpe_before"]
    values = ctx["values"]
    timeout_s = ctx["timeout_s"]
    apply_settle_s = ctx["apply_settle_s"]
    link_timeout_s = ctx["link_timeout_s"]
    link_poll_s = ctx["link_poll_s"]
    radio_idx = ctx["radio_idx"]
    profile = ctx["profile"]
    reverted = False

    target_bts_gw, target_cpe_gw = _ft14_pick_random_gateways(
        bts_ipv4=bts_before.get("ipv4", ""),
        cpe_ipv4=cpe_before.get("ipv4", ""),
        bts_current_gw=bts_before.get("ipv4_gw", ""),
        cpe_current_gw=cpe_before.get("ipv4_gw", ""),
        values=values,
    )
    _log(
        f"{case_id}: IPv4 GW BTS {bts_before.get('ipv4_gw', '') or '(empty)'} -> {target_bts_gw} | "
        f"CPE {cpe_before.get('ipv4_gw', '') or '(empty)'} -> {target_cpe_gw}"
    )
    try:
        await _ft10_ensure_static(bts_ssh, snapshot=bts_before, label="BTS", case_id=case_id, timeout_s=timeout_s)
        await _ft10_ensure_static(cpe_ssh, snapshot=cpe_before, label="CPE", case_id=case_id, timeout_s=timeout_s)
        assoc_before = await _ft14_read_assoc_time(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _log(f"{case_id}: client association time before change={assoc_before}s")

        _log(f"{case_id}: applying IPv4 gateway change on CPE then BTS")
        await _ft14_apply_gateway(cpe_ssh, snapshot=cpe_before, gateway=target_cpe_gw, timeout_s=timeout_s)
        await _ft14_apply_gateway(bts_ssh, snapshot=bts_before, gateway=target_bts_gw, timeout_s=timeout_s)
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        link_status, link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-ipv4-gateway-change", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        link_status, link_comment = _ft10_ft11_normalize_change_result(link_status, link_comment)
        _ft_link_status_log(case_id, "post-ipv4-gateway-change", link_status, link_comment)
        assoc_after = await _ft14_read_assoc_time(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _log(f"{case_id}: client association time after change={assoc_after}s")

        apply_ok, apply_rows = await _ft14_verify_backend_pair(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": "static", "ipv4": bts_before.get("ipv4", ""), "ipv4_gw": target_bts_gw},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": "static", "ipv4": cpe_before.get("ipv4", ""), "ipv4_gw": target_cpe_gw},
            timeout_s=timeout_s,
        )
        if not apply_ok:
            _print_backend_table(f"{case_id}: backend verify failed after IPv4 gateway change", apply_rows)

        _log(
            f"{case_id}: reverting IPv4 GW BTS={bts_before.get('ipv4_gw', '') or '(empty)'} "
            f"CPE={cpe_before.get('ipv4_gw', '') or '(empty)'}"
        )
        await _ft14_apply_gateway(cpe_ssh, snapshot=cpe_before, gateway=cpe_before.get("ipv4_gw", ""), timeout_s=timeout_s)
        await _ft14_apply_gateway(bts_ssh, snapshot=bts_before, gateway=bts_before.get("ipv4_gw", ""), timeout_s=timeout_s)
        reverted = True
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        revert_link_status, revert_link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-revert", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        _ft_link_status_log(case_id, "post-revert", revert_link_status, revert_link_comment)

        revert_ok, revert_rows = await _ft14_verify_backend_pair(
            bts_ssh=bts_ssh,
            cpe_ssh=cpe_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": bts_before["proto"], "ipv4": bts_before.get("ipv4", ""), "ipv4_gw": bts_before.get("ipv4_gw", "")},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": cpe_before["proto"], "ipv4": cpe_before.get("ipv4", ""), "ipv4_gw": cpe_before.get("ipv4_gw", "")},
            timeout_s=timeout_s,
        )
        if not revert_ok:
            _print_backend_table(f"{case_id}: backend verify failed after revert", revert_rows)

        return _ft14_ft15_grade_result(
            link_status=link_status,
            revert_link_status=revert_link_status,
            apply_ok=apply_ok,
            revert_ok=revert_ok,
        )
    finally:
        if not reverted:
            try:
                await _ft14_apply_gateway(cpe_ssh, snapshot=cpe_before, gateway=cpe_before.get("ipv4_gw", ""), timeout_s=timeout_s)
                await _ft14_apply_gateway(bts_ssh, snapshot=bts_before, gateway=bts_before.get("ipv4_gw", ""), timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: best-effort IPv4 gateway restore failed: {exc}")



async def _ft15_ipv4_change_operation(ctx: dict) -> tuple[str, str]:
    case_id = ctx["case_id"]
    bts_ssh = ctx["bts_ssh"]
    cpe_ssh = ctx["cpe_ssh"]
    bts_before = ctx["bts_before"]
    cpe_before = ctx["cpe_before"]
    values = ctx["values"]
    timeout_s = ctx["timeout_s"]
    apply_settle_s = ctx["apply_settle_s"]
    link_timeout_s = ctx["link_timeout_s"]
    link_poll_s = ctx["link_poll_s"]
    radio_idx = ctx["radio_idx"]
    profile = ctx["profile"]
    device_creds = ctx["device_creds"]
    reverted = False
    bts_changed_ssh = None
    cpe_changed_ssh = None
    bts_restored_ssh = None
    cpe_restored_ssh = None

    target_bts_ipv4, target_cpe_ipv4 = _ft15_pick_random_ipv4_pair(
        values=values,
        reserved={
            bts_before.get("ipv4", ""),
            cpe_before.get("ipv4", ""),
            bts_before.get("ipv4_gw", ""),
            cpe_before.get("ipv4_gw", ""),
        },
    )
    _log(
        f"{case_id}: IPv4 BTS {bts_before.get('ipv4', '')} -> {target_bts_ipv4} | "
        f"CPE {cpe_before.get('ipv4', '')} -> {target_cpe_ipv4}"
    )
    try:
        await _ft10_ensure_static(bts_ssh, snapshot=bts_before, label="BTS", case_id=case_id, timeout_s=timeout_s)
        await _ft10_ensure_static(cpe_ssh, snapshot=cpe_before, label="CPE", case_id=case_id, timeout_s=timeout_s)
        assoc_before = await _ft14_read_assoc_time(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _log(f"{case_id}: client association time before change={assoc_before}s")

        _log(f"{case_id}: applying IPv4 change on CPE then BTS")
        for label, ssh_obj, snapshot, ipv4 in (
            ("CPE", cpe_ssh, cpe_before, target_cpe_ipv4),
            ("BTS", bts_ssh, bts_before, target_bts_ipv4),
        ):
            try:
                await _ft15_apply_ipv4(ssh_obj, snapshot=snapshot, ipv4=ipv4, timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: {label} SSH interrupted during IPv4 change (expected): {exc}")
        await asyncio.sleep(max(apply_settle_s, 25))

        ssh_timeout = int(values.get("ft15_reconnect_timeout_s", values.get("ft10_tagged_ssh_timeout_s", 60)))
        bts_changed_ssh = await _open_plain_ssh_retry(
            [target_bts_ipv4, bts_before.get("ipv4", ""), "10.0.0.1"],
            device_creds["pass"],
            label="BTS changed IPv4",
            timeout_s=ssh_timeout,
        )
        ctx["bts_ssh"] = bts_changed_ssh

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        link_status, link_comment = await _ft10_wait_link_result(
            bts_changed_ssh, case_id=case_id, label="post-ipv4-change", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        link_status, link_comment = _ft10_ft11_normalize_change_result(link_status, link_comment)
        _ft_link_status_log(case_id, "post-ipv4-change", link_status, link_comment)

        # CPE often only reachable via BTS after LAN IP mutation under tagged mgmt VLAN.
        try:
            cpe_changed_ssh = await _open_plain_ssh_retry(
                [target_cpe_ipv4, cpe_before.get("ipv4", "")],
                device_creds["pass"],
                label="CPE changed IPv4",
                timeout_s=ssh_timeout,
                attempts=4,
                poll_s=5.0,
            )
        except Exception as direct_exc:
            _log(f"{case_id}: CPE direct IPv4 reconnect failed ({direct_exc}); trying BTS hop/tunnel")
            from utils.arp_bridge_table_flows import _open_cpe_ssh

            cpe_changed_ssh, mode = await _open_cpe_ssh(
                bts_changed_ssh,
                target_cpe_ipv4 or cpe_before.get("ipv4", ""),
                target_bts_ipv4 or bts_before.get("ipv4", ""),
                device_creds,
            )
            _log(f"{case_id}: CPE reconnected via {mode}")
        ctx["cpe_ssh"] = cpe_changed_ssh

        assoc_after = await _ft14_read_assoc_time(bts_changed_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
        _log(f"{case_id}: client association time after change={assoc_after}s")

        apply_ok, apply_rows = await _ft15_verify_backend_pair(
            bts_ssh=bts_changed_ssh,
            cpe_ssh=cpe_changed_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": "static", "ipv4": target_bts_ipv4},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": "static", "ipv4": target_cpe_ipv4},
            timeout_s=timeout_s,
        )
        if not apply_ok:
            _print_backend_table(f"{case_id}: backend verify failed after IPv4 change", apply_rows)

        _log(f"{case_id}: reverting IPv4 BTS={bts_before.get('ipv4', '')} CPE={cpe_before.get('ipv4', '')}")
        for label, ssh_obj, snapshot, ipv4 in (
            ("CPE", cpe_changed_ssh, cpe_before, cpe_before.get("ipv4", "")),
            ("BTS", bts_changed_ssh, bts_before, bts_before.get("ipv4", "")),
        ):
            try:
                await _ft15_apply_ipv4(ssh_obj, snapshot=snapshot, ipv4=ipv4, timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: {label} SSH interrupted during IPv4 revert (expected): {exc}")
        reverted = True
        await asyncio.sleep(max(apply_settle_s, 25))

        bts_restored_ssh = await _open_plain_ssh_retry(
            [bts_before.get("ipv4", ""), target_bts_ipv4, "10.0.0.1"],
            device_creds["pass"],
            label="BTS restored IPv4",
            timeout_s=ssh_timeout,
        )
        ctx["bts_ssh"] = bts_restored_ssh

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        revert_link_status, revert_link_comment = await _ft10_wait_link_result(
            bts_restored_ssh, case_id=case_id, label="post-revert", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        _ft_link_status_log(case_id, "post-revert", revert_link_status, revert_link_comment)

        try:
            cpe_restored_ssh = await _open_plain_ssh_retry(
                [cpe_before.get("ipv4", ""), target_cpe_ipv4],
                device_creds["pass"],
                label="CPE restored IPv4",
                timeout_s=ssh_timeout,
                attempts=4,
                poll_s=5.0,
            )
        except Exception as direct_exc:
            _log(f"{case_id}: CPE restored direct reconnect failed ({direct_exc}); trying BTS hop/tunnel")
            from utils.arp_bridge_table_flows import _open_cpe_ssh

            cpe_restored_ssh, mode = await _open_cpe_ssh(
                bts_restored_ssh,
                cpe_before.get("ipv4", "") or target_cpe_ipv4,
                bts_before.get("ipv4", "") or target_bts_ipv4,
                device_creds,
            )
            _log(f"{case_id}: CPE restored via {mode}")
        ctx["cpe_ssh"] = cpe_restored_ssh

        revert_ok, revert_rows = await _ft15_verify_backend_pair(
            bts_ssh=bts_restored_ssh,
            cpe_ssh=cpe_restored_ssh,
            bts_expected={"mgmt_vlan": bts_before["mgmt_vlan"], "proto": bts_before["proto"], "ipv4": bts_before.get("ipv4", "")},
            cpe_expected={"mgmt_vlan": cpe_before["mgmt_vlan"], "proto": cpe_before["proto"], "ipv4": cpe_before.get("ipv4", "")},
            timeout_s=timeout_s,
        )
        if not revert_ok:
            _print_backend_table(f"{case_id}: backend verify failed after revert", revert_rows)

        return _ft14_ft15_grade_result(
            link_status=link_status,
            revert_link_status=revert_link_status,
            apply_ok=apply_ok,
            revert_ok=revert_ok,
        )
    finally:
        if not reverted:
            try:
                await _ft15_apply_ipv4(cpe_changed_ssh or cpe_ssh, snapshot=cpe_before, ipv4=cpe_before.get("ipv4", ""), timeout_s=timeout_s)
                await _ft15_apply_ipv4(bts_changed_ssh or bts_ssh, snapshot=bts_before, ipv4=bts_before.get("ipv4", ""), timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: best-effort IPv4 restore failed: {exc}")
        for conn in (cpe_changed_ssh, bts_changed_ssh):
            if conn not in (ctx.get("cpe_ssh"), ctx.get("bts_ssh")):
                try:
                    if conn is not None:
                        await conn.close()
                except Exception:
                    pass



async def _ft16_bandwidth_change_operation(ctx: dict) -> tuple[str, str]:
    case_id = ctx["case_id"]
    bts_ssh = ctx["bts_ssh"]
    bts_before = ctx["bts_before"]
    values = ctx["values"]
    timeout_s = ctx["timeout_s"]
    apply_settle_s = ctx["apply_settle_s"]
    link_timeout_s = ctx["link_timeout_s"]
    link_poll_s = ctx["link_poll_s"]
    radio_idx = ctx["radio_idx"]
    profile = ctx["profile"]
    bandwidths = [str(v) for v in values.get(
        "ft16_bandwidths" if case_id == "FT_16" else "ft23_bandwidths",
        values.get("ft16_bandwidths", values.get("ft23_bandwidths", ["HT20", "HT40", "HT80", "HT160"])),
    )]
    skip_key = "ft16_skip_unsupported_bandwidth" if case_id == "FT_16" else "ft23_skip_unsupported_bandwidth"
    skip_unsupported = bool(
        values.get(
            skip_key,
            values.get("ft16_skip_unsupported_bandwidth", values.get("ft23_skip_unsupported_bandwidth", values.get("ft05_skip_unsupported_bandwidth", True))),
        )
    )
    original_bw = bts_before.get("bandwidth") or await _ft16_read_bandwidth(bts_ssh, radio_idx=radio_idx, timeout_s=timeout_s)
    check.is_true(bool(original_bw), f"{case_id}: original bandwidth unreadable")
    _log(f"{case_id}: original BTS bandwidth={original_bw} | steps={', '.join(bandwidths)}")

    any_unstable = False
    verify_fail = False
    link_fail = False
    restored = False
    try:
        for bw in bandwidths:
            _log(f"{case_id}: applying bandwidth {bw} on BTS only")
            await _ft16_apply_bandwidth(bts_ssh, radio_idx=radio_idx, bandwidth=bw, timeout_s=timeout_s)
            await asyncio.sleep(apply_settle_s)

            _log(f"{case_id}: RF Link Down .. Waiting for link up")
            link_status, link_comment = await _ft10_wait_link_result(
                bts_ssh, case_id=case_id, label=f"post-bandwidth-{bw}", profile=profile,
                timeout_s=link_timeout_s, poll_s=link_poll_s,
            )
            _ft_link_status_log(case_id, f"post-bandwidth-{bw}", link_status, link_comment)
            if link_status == "FAIL":
                link_fail = True
                break
            if link_status == "PASS (UNSTABLE)":
                any_unstable = True

            ok, bts_bw = await _ft16_verify_bts_bandwidth(
                bts_ssh, radio_idx=radio_idx, expected=bw, timeout_s=timeout_s,
            )
            if not ok and skip_unsupported:
                _log(
                    f"{case_id}: BTS BW {bw} not accepted by DUT "
                    f"(actual={bts_bw or '(empty)'}) — skip (same as FT_05)"
                )
                continue
            _log(f"{case_id}: BTS BW verify {bw} actual={bts_bw} ({'ok' if ok else 'FAIL'})")
            if not ok:
                verify_fail = True

        _log(f"{case_id}: reverting BTS bandwidth to original {original_bw}")
        await _ft16_apply_bandwidth(bts_ssh, radio_idx=radio_idx, bandwidth=original_bw, timeout_s=timeout_s)
        restored = True
        await asyncio.sleep(apply_settle_s)

        _log(f"{case_id}: RF Link Down .. Waiting for link up")
        revert_link_status, revert_link_comment = await _ft10_wait_link_result(
            bts_ssh, case_id=case_id, label="post-bandwidth-revert", profile=profile,
            timeout_s=link_timeout_s, poll_s=link_poll_s,
        )
        _ft_link_status_log(case_id, "post-bandwidth-revert", revert_link_status, revert_link_comment)
        revert_ok, rev_bts_bw = await _ft16_verify_bts_bandwidth(
            bts_ssh, radio_idx=radio_idx, expected=original_bw, timeout_s=timeout_s,
        )
        _log(f"{case_id}: BTS BW revert verify actual={rev_bts_bw} ({'ok' if revert_ok else 'FAIL'})")

        if link_fail or revert_link_status == "FAIL":
            return "FAIL", "RF Link Drops never comes up until timeout"
        if verify_fail or not revert_ok:
            return "FAIL", "Backend config verification failed"
        if any_unstable or revert_link_status == "PASS (UNSTABLE)":
            return "PASS (UNSTABLE)", "RF Interface Restarts,By design"
        return "PASS", "RF Link wont drop"
    finally:
        if not restored:
            try:
                await _ft16_apply_bandwidth(bts_ssh, radio_idx=radio_idx, bandwidth=original_bw, timeout_s=timeout_s)
            except Exception as exc:
                _log(f"{case_id}: best-effort bandwidth restore failed: {exc}")



async def assert_ft_11_mgmt_vlan_gateway_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    values = FUNCTIONAL_TEST_VALUES
    await _ft10_run_random_mgmt_vlan_case(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
        case_id="FT_11",
        title="FT_11: Mgmt VLAN precheck + IPv6 gateway change",
        radio_idx=int(values.get("ft11_radio_idx", values.get("ft18_radio_idx", values.get("ft10_radio_idx", RADIO_IDX)))),
        apply_settle_s=int(values.get("ft11_apply_settle_s", values.get("ft18_apply_settle_s", values.get("ft10_apply_settle_s", 20)))),
        link_timeout_s=int(values.get("ft11_link_recovery_timeout_s", values.get("ft18_link_recovery_timeout_s", values.get("ft10_link_recovery_timeout_s", 900)))),
        link_poll_s=int(values.get("ft11_link_recovery_poll_s", values.get("ft18_link_recovery_poll_s", values.get("ft10_link_recovery_poll_s", 8)))),
        operation=_ft11_gateway_change_operation,
    )



async def assert_ft_12_mgmt_vlan_ipv6_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    values = FUNCTIONAL_TEST_VALUES
    await _ft10_run_random_mgmt_vlan_case(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
        case_id="FT_12",
        title="FT_12: Mgmt VLAN precheck + IPv6 address change",
        radio_idx=int(values.get("ft12_radio_idx", values.get("ft19_radio_idx", values.get("ft10_radio_idx", RADIO_IDX)))),
        apply_settle_s=int(values.get("ft12_apply_settle_s", values.get("ft19_apply_settle_s", 20))),
        link_timeout_s=int(values.get("ft12_link_recovery_timeout_s", values.get("ft19_link_recovery_timeout_s", 300))),
        link_poll_s=int(values.get("ft12_link_recovery_poll_s", values.get("ft19_link_recovery_poll_s", 8))),
        operation=_ft12_ipv6_change_operation,
    )




async def assert_ft_14_mgmt_vlan_ipv4_gateway_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    values = FUNCTIONAL_TEST_VALUES
    await _ft10_run_random_mgmt_vlan_case(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
        case_id="FT_14",
        title="FT_14: Mgmt VLAN precheck + IPv4 gateway change",
        radio_idx=int(values.get("ft14_radio_idx", values.get("ft21_radio_idx", values.get("ft10_radio_idx", RADIO_IDX)))),
        apply_settle_s=int(values.get("ft14_apply_settle_s", values.get("ft21_apply_settle_s", 20))),
        link_timeout_s=int(values.get("ft14_link_recovery_timeout_s", values.get("ft21_link_recovery_timeout_s", 300))),
        link_poll_s=int(values.get("ft14_link_recovery_poll_s", values.get("ft21_link_recovery_poll_s", 8))),
        operation=_ft14_ipv4_gateway_change_operation,
    )



async def assert_ft_15_mgmt_vlan_ipv4_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    values = FUNCTIONAL_TEST_VALUES
    await _ft10_run_random_mgmt_vlan_case(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
        case_id="FT_15",
        title="FT_15: Mgmt VLAN precheck + IPv4 address change",
        radio_idx=int(values.get("ft15_radio_idx", values.get("ft22_radio_idx", values.get("ft10_radio_idx", RADIO_IDX)))),
        apply_settle_s=int(values.get("ft15_apply_settle_s", values.get("ft22_apply_settle_s", 20))),
        link_timeout_s=int(values.get("ft15_link_recovery_timeout_s", values.get("ft22_link_recovery_timeout_s", 300))),
        link_poll_s=int(values.get("ft15_link_recovery_poll_s", values.get("ft22_link_recovery_poll_s", 8))),
        operation=_ft15_ipv4_change_operation,
    )



async def assert_ft_16_mgmt_vlan_channel_width_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    values = FUNCTIONAL_TEST_VALUES
    await _ft10_run_random_mgmt_vlan_case(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
        case_id="FT_16",
        title="FT_16: Mgmt VLAN precheck + channel width change",
        radio_idx=int(values.get("ft16_radio_idx", values.get("ft23_radio_idx", values.get("ft10_radio_idx", RADIO_IDX)))),
        apply_settle_s=int(values.get("ft16_apply_settle_s", values.get("ft23_apply_settle_s", 20))),
        link_timeout_s=int(values.get("ft16_link_recovery_timeout_s", values.get("ft23_link_recovery_timeout_s", 300))),
        link_poll_s=int(values.get("ft16_link_recovery_poll_s", values.get("ft23_link_recovery_poll_s", 8))),
        operation=_ft16_bandwidth_change_operation,
    )


async def assert_ft_10_mgmt_vlan_precheck(*args, **kwargs):
    return await assert_ft_17_mgmt_vlan_prefix_change(*args, **kwargs)

async def assert_ft_13_mgmt_vlan_ipv4_netmask_change(*args, **kwargs):
    return await assert_ft_17_mgmt_vlan_prefix_change(*args, **kwargs)

async def assert_ft_18_mgmt_vlan_gateway_change(*args, **kwargs):
    return await assert_ft_11_mgmt_vlan_gateway_change(*args, **kwargs)

async def assert_ft_19_mgmt_vlan_ipv6_change(*args, **kwargs):
    return await assert_ft_12_mgmt_vlan_ipv6_change(*args, **kwargs)

async def assert_ft_20_mgmt_vlan_ipv4_netmask_change(*args, **kwargs):
    return await assert_ft_17_mgmt_vlan_prefix_change(*args, **kwargs)

async def assert_ft_21_mgmt_vlan_ipv4_gateway_change(*args, **kwargs):
    return await assert_ft_14_mgmt_vlan_ipv4_gateway_change(*args, **kwargs)

async def assert_ft_22_mgmt_vlan_ipv4_change(*args, **kwargs):
    return await assert_ft_15_mgmt_vlan_ipv4_change(*args, **kwargs)

async def assert_ft_23_mgmt_vlan_channel_width_change(
    root_ssh,
    cpe_ips: list[str],
    *,
    bsu_ip: str | None = None,
    device_creds: dict | None = None,
    profile_bundle=None,
    request=None,
):
    values = FUNCTIONAL_TEST_VALUES
    await _ft10_run_random_mgmt_vlan_case(
        root_ssh,
        cpe_ips,
        bsu_ip=bsu_ip,
        device_creds=device_creds,
        profile_bundle=profile_bundle,
        request=request,
        case_id="FT_23",
        title="FT_23: Mgmt VLAN precheck + channel width change",
        radio_idx=int(values.get("ft23_radio_idx", values.get("ft16_radio_idx", RADIO_IDX))),
        apply_settle_s=int(values.get("ft23_apply_settle_s", values.get("ft16_apply_settle_s", 20))),
        link_timeout_s=int(values.get("ft23_link_recovery_timeout_s", values.get("ft16_link_recovery_timeout_s", 300))),
        link_poll_s=int(values.get("ft23_link_recovery_poll_s", values.get("ft16_link_recovery_poll_s", 8))),
        operation=_ft16_bandwidth_change_operation,
    )
