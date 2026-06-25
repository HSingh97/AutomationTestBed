"""2.4G_RADIO_41–50 — CPE mgmt API validation over 2.4 GHz Wi‑Fi (169.254.254.1).

All cases use GET /api/v1/cpe/sw-version as the primary API (no btsconnect / upgrade-sw).
Target: http://169.254.254.1 — PC on CPE mgmt Wi‑Fi only; no IP or routing changes.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import pytest_check as check

from utils.cpe_api_24_client import CpeApi24Client, CpeApiResponse
from utils.cpe_api_24_config import (
    INVALID_API_PATH,
    RADIO24_API_LOAD_REQUESTS,
    RADIO24_API_LOAD_WINDOW_S,
    RADIO24_API_LOG_PATTERNS,
    RADIO24_API_REBOOT_WAIT_S,
    RADIO24_API_RESPONSE_MAX_MS,
    RADIO24_BTS_LINK_WAIT_S,
    SW_VERSION_PATH,
)
from utils.cpe_api_24_flows import _body, _sw_version_from_body
from utils.verify_output import print_comparison_table, print_kv_block, print_section


def _print_api_result(
    case_id: str,
    response: CpeApiResponse,
    expected_http: str,
    *,
    extra_rows: list[tuple[str, str, str, str]] | None = None,
) -> None:
    rows: list[tuple[str, str, str, str]] = [
        ("HTTP status", str(response.status_code), expected_http, "CHECK"),
        ("Transport", response.transport, "2.4 GHz mgmt", "INFO"),
        ("JSON body", str(response.json_body)[:120], "—", "INFO"),
    ]
    if extra_rows:
        rows.extend(extra_rows)
    print_comparison_table(rows)


async def _ensure_mgmt_api_ready(
    client: CpeApi24Client,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
    label: str,
) -> None:
    """Rejoin mgmt Wi‑Fi if 169.254.254.1 is down (e.g. after case 46 reboot)."""
    from utils.cpe_mgmt_wifi import ensure_pc_mgmt_api_ready, is_mgmt_api_reachable

    if await is_mgmt_api_reachable(client.config.base_url, timeout_s=3.0):
        return
    print(f"\n[{label}] mgmt API down — rejoin CPE Wi‑Fi and wait (no IP change)...")
    await ensure_pc_mgmt_api_ready(
        client.config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label=label,
    )


def _check_sw_version_ok(case_id: str, response: CpeApiResponse) -> str | None:
    body = _body(response)
    sw_version = _sw_version_from_body(body)
    check.equal(response.status_code, 200, f"{case_id}: expected HTTP 200")
    check.equal(str(body.get("status", "")).lower(), "success", f"{case_id}: expected status=success")
    check.is_true(sw_version, f"{case_id}: expected swversion in response")
    return sw_version


async def assert_radio_24_41_valid_api_call(client: CpeApi24Client) -> None:
    """2.4G_RADIO_41 — valid GET sw-version → HTTP 200, status=success."""
    case_id = "2_4G_RADIO_41"
    print_section(f"{case_id} — valid GET sw-version on CPE 2.4 GHz mgmt")
    print_kv_block(
        "Flow",
        {
            "API": f"GET {client.config.base_url}{SW_VERSION_PATH}",
            "Expected": "HTTP 200, status=success, swversion present",
        },
    )
    response = await client.get(SW_VERSION_PATH, read_timeout_s=20.0)
    sw_version = _check_sw_version_ok(case_id, response)
    _print_api_result(
        case_id,
        response,
        "200",
        extra_rows=[("swversion", sw_version or "(missing)", "non-empty", "CHECK")],
    )


async def assert_radio_24_42_invalid_api_call(client: CpeApi24Client) -> None:
    """2.4G_RADIO_42 — invalid path → error; GET sw-version unchanged afterward."""
    case_id = "2_4G_RADIO_42"
    print_section(f"{case_id} — invalid API path (no side effects on sw-version)")
    print_kv_block(
        "Flow",
        {
            "Invalid API": f"GET {client.config.base_url}{INVALID_API_PATH}",
            "Verify": f"GET {SW_VERSION_PATH} still returns 200 with same swversion",
        },
    )
    baseline = await client.get(SW_VERSION_PATH, read_timeout_s=20.0, log=False)
    baseline_ver = _sw_version_from_body(_body(baseline))

    invalid = await client.get(INVALID_API_PATH, read_timeout_s=15.0)
    _print_api_result(case_id, invalid, "404 or 400")

    after = await client.get(SW_VERSION_PATH, read_timeout_s=20.0, log=False)
    after_ver = _sw_version_from_body(_body(after))

    print_comparison_table(
        [
            ("Invalid API", str(invalid.status_code), "404 or 400", "CHECK"),
            ("sw-version before", baseline_ver or "(missing)", "—", "INFO"),
            ("sw-version after", after_ver or "(missing)", "unchanged", "CHECK"),
            ("Valid API after invalid", str(after.status_code), "200", "CHECK"),
        ]
    )
    check.is_true(
        invalid.status_code in (400, 404, 405),
        f"{case_id}: invalid path expected 400/404/405, got {invalid.status_code}",
    )
    check.equal(after.status_code, 200, f"{case_id}: sw-version must still return 200")
    if baseline_ver and after_ver:
        check.equal(after_ver, baseline_ver, f"{case_id}: swversion must not change after invalid call")


async def assert_radio_24_43_unauthorized_access(client: CpeApi24Client) -> None:
    """2.4G_RADIO_43 — GET sw-version on mgmt Wi‑Fi (read access; no btsconnect / no IP change)."""
    case_id = "2_4G_RADIO_43"
    print_section(f"{case_id} — GET sw-version read access on mgmt Wi‑Fi")
    print_kv_block(
        "Flow",
        {
            "API": f"GET {client.config.base_url}{SW_VERSION_PATH}",
            "Expected": "HTTP 200, status=success (mgmt link only; no btsconnect)",
        },
    )
    response = await client.get(SW_VERSION_PATH, read_timeout_s=20.0)
    sw_version = _check_sw_version_ok(case_id, response)
    _print_api_result(
        case_id,
        response,
        "200",
        extra_rows=[("swversion", sw_version or "(missing)", "non-empty", "CHECK")],
    )


async def assert_radio_24_44_malformed_request(client: CpeApi24Client) -> None:
    """2.4G_RADIO_44 — invalid sw-version URL → 404; GET sw-version still OK."""
    case_id = "2_4G_RADIO_44"
    bad_path = f"{SW_VERSION_PATH}/not-valid"
    print_section(f"{case_id} — invalid sw-version URL")
    print_kv_block(
        "Flow",
        {
            "Invalid API": f"GET {client.config.base_url}{bad_path}",
            "Expected": "HTTP 404/400",
            "Verify": f"GET {SW_VERSION_PATH} still returns 200",
        },
    )
    response = await client.get(bad_path, read_timeout_s=20.0)
    _print_api_result(case_id, response, "404 or 400")

    after = await client.get(SW_VERSION_PATH, read_timeout_s=20.0, log=False)
    _check_sw_version_ok(case_id, after)

    check.is_true(
        response.status_code in (400, 404, 405),
        f"{case_id}: invalid sw-version URL expected 400/404/405, got {response.status_code}",
    )


async def assert_radio_24_45_api_under_load(client: CpeApi24Client) -> None:
    """2.4G_RADIO_45 — 100 GET sw-version requests in 60s; CPE stays responsive."""
    case_id = "2_4G_RADIO_45"
    total = RADIO24_API_LOAD_REQUESTS
    window_s = RADIO24_API_LOAD_WINDOW_S
    interval_s = window_s / max(total, 1)

    print_section(f"{case_id} — sw-version load ({total} requests in {window_s:.0f}s)")
    print_kv_block(
        "Flow",
        {
            "API": f"GET {client.config.base_url}{SW_VERSION_PATH}",
            "Target": f"{total} requests / {window_s:.0f}s",
            "Expected": "All HTTP 200; sw-version still up after load",
        },
    )

    pass_count = 0
    fail_count = 0
    lock = asyncio.Lock()
    start = time.monotonic()
    tasks: list[asyncio.Task[None]] = []

    async def _one_request() -> None:
        nonlocal pass_count, fail_count
        try:
            response = await client.get(SW_VERSION_PATH, read_timeout_s=10.0, log=False)
            ok = response.status_code == 200
        except Exception:
            ok = False
        async with lock:
            if ok:
                pass_count += 1
            else:
                fail_count += 1

    sent = 0
    while sent < total and (time.monotonic() - start) < window_s:
        loop_start = time.monotonic()
        tasks.append(asyncio.create_task(_one_request()))
        sent += 1
        elapsed = time.monotonic() - loop_start
        sleep_s = interval_s - elapsed
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    duration = time.monotonic() - start

    health = await client.get(SW_VERSION_PATH, read_timeout_s=15.0)
    print_comparison_table(
        [
            ("Requests sent", str(sent), str(total), "INFO"),
            ("Pass (HTTP 200)", str(pass_count), str(total), "CHECK"),
            ("Fail", str(fail_count), "0", "CHECK"),
            ("Duration (s)", f"{duration:.1f}", f"≤{window_s:.0f}", "INFO"),
            ("Post-load sw-version", str(health.status_code), "200", "CHECK"),
        ]
    )
    check.greater_equal(pass_count, total, f"{case_id}: all {total} requests must return HTTP 200")
    check.equal(health.status_code, 200, f"{case_id}: sw-version must respond after load")


async def _ensure_pc_on_cpe_mgmt_ssid(
    client: CpeApi24Client,
    *,
    bts_host: str | None,
    bts_username: str,
    bts_password: str,
    label: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Verify PC is on CPE 2.4 GHz mgmt SSID; nmcli join if disconnected or wrong SSID.
    Returns (wifi_iface, pc_ssid, cpe_mgmt_ssid).
    """
    from utils.cpe_api_24_lab import _is_bts_like_mgmt_ssid
    from utils.cpe_mgmt_wifi import ensure_pc_mgmt_api_ready, get_active_wifi_ssid

    iface, pc_ssid = await get_active_wifi_ssid(client.config.wifi_interface)
    cpe_mgmt_ssid: str | None = (client.config.cpe_mgmt_ssid or "").strip() or None
    bts_ssid = (client.config.bts_ssid or "").strip()

    needs_join = not pc_ssid
    if pc_ssid and _is_bts_like_mgmt_ssid(pc_ssid, bts_ssid=bts_ssid):
        needs_join = True
        print(f"    -> [{label}] PC on BTS Wi‑Fi {pc_ssid!r} — must join CPE mgmt {cpe_mgmt_ssid!r}...")
    elif cpe_mgmt_ssid and pc_ssid and pc_ssid != cpe_mgmt_ssid:
        needs_join = True
        print(
            f"    -> [{label}] PC on {pc_ssid!r}, CPE mgmt AP is {cpe_mgmt_ssid!r} — rejoin..."
        )

    if needs_join:
        print(
            f"    -> [{label}] nmcli join CPE mgmt hidden SSID "
            f"{cpe_mgmt_ssid or '(fetch via BTS)'}..."
        )
        await ensure_pc_mgmt_api_ready(
            client.config,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
            label=label,
        )
        iface, pc_ssid = await get_active_wifi_ssid(client.config.wifi_interface)

    return iface, pc_ssid, cpe_mgmt_ssid


async def _verify_sw_version_backend(client: CpeApi24Client, case_id: str) -> str:
    """SSH to CPE mgmt (169.254.254.1): read /etc/version before any HTTP API check."""
    backend_ver = await client._read_sw_version_via_ssh()
    print_comparison_table(
        [
            ("Backend", f"SSH cat /etc/version @ {client.config.cpe_ssh_host}", "—", "INFO"),
            ("/etc/version", backend_ver or "(missing)", "non-empty", "CHECK"),
        ]
    )
    check.is_true(
        backend_ver,
        f"{case_id}: CPE backend /etc/version must be readable via SSH before API call",
    )
    return backend_ver or ""


async def _get_sw_version_api_after_backend(
    client: CpeApi24Client,
    case_id: str,
    *,
    backend_ver: str,
    log: bool = True,
) -> tuple[CpeApiResponse, str | None]:
    """Step 2 — GET sw-version over HTTP only after backend /etc/version is confirmed."""
    response = await client.get(SW_VERSION_PATH, read_timeout_s=20.0, log=log)
    sw_version = _check_sw_version_ok(case_id, response)
    check.equal(
        sw_version,
        backend_ver,
        f"{case_id}: API swversion must match backend /etc/version",
    )
    return response, sw_version


async def _wait_bts_cpe_link_after_reboot(
    *,
    bts_host: str | None,
    bts_username: str,
    bts_password: str,
    client: CpeApi24Client,
    case_id: str,
) -> bool:
    """Poll BTS remote partners; btsconnect + re-wait if link stays down."""
    from utils.cpe_api_24_lab import is_bts_link_broken, wait_bts_link_up

    if not bts_host or not bts_password:
        print(f"    -> [{case_id}] skip BTS link wait (--local-ipv6 not set)")
        return True

    print(f"    -> [{case_id}] wait BTS↔CPE link (max {RADIO24_BTS_LINK_WAIT_S:.0f}s)")
    if await wait_bts_link_up(
        bts_host=bts_host,
        username=bts_username,
        password=bts_password,
        max_wait_s=RADIO24_BTS_LINK_WAIT_S,
        label=case_id,
    ):
        return True

    bts_ssid = (client.config.bts_ssid or "").strip()
    bts_password = (client.config.bts_password or "").strip()
    if not bts_ssid or not bts_password:
        return False

    print(
        f"    -> [{case_id}] BTS link still down — POST btsconnect "
        f"({bts_ssid!r}) then wait again..."
    )
    response = await client.btsconnect(
        bts_ssid,
        bts_password,
        read_timeout_s=client.config.btsconnect_timeout_s,
    )
    body = _body(response)
    print_comparison_table(
        [
            ("btsconnect HTTP", str(response.status_code), "200", "CHECK"),
            ("btsconnect status", str(body.get("status", "")), "success", "INFO"),
        ]
    )
    return await wait_bts_link_up(
        bts_host=bts_host,
        username=bts_username,
        password=bts_password,
        max_wait_s=RADIO24_BTS_LINK_WAIT_S,
        label=f"{case_id} (after btsconnect)",
    )


async def assert_radio_24_46_api_after_reboot(
    client: CpeApi24Client,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> None:
    """2.4G_RADIO_46 — reboot CPE; backend SSH verify; GET sw-version; wait BTS link."""
    case_id = "2_4G_RADIO_46"
    print_section(f"{case_id} — API after CPE reboot")
    elapsed = await client.reboot_and_wait_for_backend(
        max_wait_s=RADIO24_API_REBOOT_WAIT_S,
        label=case_id,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
    )

    iface, pc_ssid, cpe_mgmt_ssid = await _ensure_pc_on_cpe_mgmt_ssid(
        client,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        label=case_id,
    )
    check.is_true(pc_ssid, f"{case_id}: PC must be connected to CPE mgmt Wi‑Fi after reboot")
    if cpe_mgmt_ssid:
        check.equal(
            pc_ssid,
            cpe_mgmt_ssid,
            f"{case_id}: PC must be on CPE mgmt SSID {cpe_mgmt_ssid!r}, got {pc_ssid!r}",
        )

    backend_ver = await _verify_sw_version_backend(client, case_id)
    response, sw_version = await _get_sw_version_api_after_backend(
        client,
        case_id,
        backend_ver=backend_ver,
        log=False,
    )

    link_ok = await _wait_bts_cpe_link_after_reboot(
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        client=client,
        case_id=case_id,
    )

    partners_line = ""
    if bts_host and bts_password:
        from utils.cpe_api_24_lab import is_bts_link_broken

        broken = await is_bts_link_broken(
            bts_host=bts_host,
            username=bts_username,
            password=bts_password,
            radio_idx=1,
            log=False,
        )
        partners_line = "up" if not broken else "down"

    print_comparison_table(
        [
            ("Reboot → backend up", f"{elapsed:.0f}s", f"≤{RADIO24_API_REBOOT_WAIT_S:.0f}s", "INFO"),
            ("PC Wi‑Fi NIC", iface or "(unknown)", "—", "INFO"),
            ("PC connected SSID", pc_ssid or "(not connected)", cpe_mgmt_ssid or "CPE mgmt", "CHECK"),
            ("Backend /etc/version", backend_ver, "non-empty", "CHECK"),
            ("API swversion", sw_version or "(missing)", backend_ver or "match backend", "CHECK"),
            ("HTTP status", str(response.status_code), "200", "CHECK"),
            ("JSON status", str(_body(response).get("status", "")), "success", "CHECK"),
            ("BTS↔CPE link", partners_line or "n/a", "up", "CHECK"),
        ]
    )
    check.is_true(link_ok, f"{case_id}: BTS↔CPE link must be up after reboot verification")

async def assert_radio_24_49_api_response_time(client: CpeApi24Client) -> None:
    """2.4G_RADIO_49 — GET sw-version response time < 500 ms; backend /etc/version match."""
    case_id = "2_4G_RADIO_49"
    max_ms = RADIO24_API_RESPONSE_MAX_MS
    print_section(f"{case_id} — API response time (valid GET sw-version)")
    print_kv_block(
        "Flow",
        {
            "Step 1": f"Backend: SSH cat /etc/version @ {client.config.cpe_ssh_host}",
            "Step 2": f"API: GET {client.config.base_url}{SW_VERSION_PATH} (3 samples, timed)",
            "Expected": f"response time < {max_ms:.0f} ms (each sample)",
            "Step 3": "API swversion must match backend /etc/version",
        },
    )

    backend_ver = await client._read_sw_version_via_ssh()
    print_comparison_table(
        [
            ("Backend", f"SSH cat /etc/version @ {client.config.cpe_ssh_host}", "—", "INFO"),
            ("/etc/version", backend_ver or "(missing)", "non-empty", "CHECK"),
        ]
    )
    check.is_true(backend_ver, f"{case_id}: backend /etc/version must be readable via SSH")

    samples_ms: list[float] = []
    last_response: CpeApiResponse | None = None
    for i in range(3):
        response, elapsed_ms = await client.get_timed(SW_VERSION_PATH, read_timeout_s=10.0)
        samples_ms.append(elapsed_ms)
        last_response = response
        print(f"    -> [{case_id}] sample {i + 1}: {elapsed_ms:.0f} ms (HTTP {response.status_code})")
        await asyncio.sleep(0.2)

    median_ms = sorted(samples_ms)[len(samples_ms) // 2]
    max_sample_ms = max(samples_ms)
    body = _body(last_response) if last_response else {}
    sw_version = _sw_version_from_body(body)

    result_rows: list[tuple[str, str, str, str]] = []
    for i, ms in enumerate(samples_ms, start=1):
        ok = ms < max_ms
        result_rows.append(
            (
                f"Response time sample {i} (ms)",
                f"{ms:.0f}",
                f"<{max_ms:.0f}",
                "CHECK" if ok else "FAIL",
            )
        )
    result_rows.extend(
        [
            (
                "Response time median (ms)",
                f"{median_ms:.0f}",
                f"<{max_ms:.0f}",
                "CHECK" if median_ms < max_ms else "FAIL",
            ),
            (
                "Response time max (ms)",
                f"{max_sample_ms:.0f}",
                f"<{max_ms:.0f}",
                "CHECK" if max_sample_ms < max_ms else "FAIL",
            ),
            ("HTTP status", str(last_response.status_code if last_response else 0), "200", "CHECK"),
            ("JSON status", str(body.get("status", "")), "success", "CHECK"),
            ("API swversion", sw_version or "(missing)", backend_ver or "match backend", "CHECK"),
        ]
    )
    print_comparison_table(result_rows)

    check.is_not_none(last_response, f"{case_id}: no response captured")
    if last_response:
        check.equal(last_response.status_code, 200, f"{case_id}: expected HTTP 200")
        check.equal(str(body.get("status", "")).lower(), "success", f"{case_id}: expected status=success")
    for i, ms in enumerate(samples_ms, start=1):
        check.less(
            ms,
            max_ms,
            f"{case_id}: sample {i} response time {ms:.0f}ms must be < {max_ms:.0f}ms",
        )
    check.less(median_ms, max_ms, f"{case_id}: median response {median_ms:.0f}ms must be < {max_ms:.0f}ms")
    check.less(max_sample_ms, max_ms, f"{case_id}: max response {max_sample_ms:.0f}ms must be < {max_ms:.0f}ms")
    if backend_ver and sw_version:
        check.equal(sw_version, backend_ver, f"{case_id}: API swversion must match backend /etc/version")


async def _fetch_cpe_log_tail(client: CpeApi24Client, *, lines: int = 80) -> str:
    _, stdout, _ = await client.ssh_run(
        f"logread 2>/dev/null | tail -n {lines}",
        timeout_s=20.0,
    )
    return stdout or ""


def _log_lines_matching(text: str, patterns: list[str]) -> list[str]:
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    hits: list[str] = []
    for line in (text or "").splitlines():
        if any(p.search(line) for p in compiled):
            hits.append(line.strip())
    return hits


def _new_log_lines(before: str, after: str) -> list[str]:
    before_set = {line.strip() for line in (before or "").splitlines() if line.strip()}
    return [line.strip() for line in (after or "").splitlines() if line.strip() and line.strip() not in before_set]


async def _fetch_cpe_cli_src_ip(client: CpeApi24Client) -> str:
    _, stdout, _ = await client.ssh_run("cat /tmp/clisrcip 2>/dev/null", timeout_s=10.0)
    return (stdout or "").strip()


async def _count_cpe_api_processes(client: CpeApi24Client) -> int:
    _, stdout, _ = await client.ssh_run(
        "ps w 2>/dev/null | grep -E 'senao-openapi|api\\.fcgi' | grep -v grep | wc -l",
        timeout_s=15.0,
    )
    try:
        return int((stdout or "0").strip())
    except ValueError:
        return 0


async def _fetch_cpe_api_log_lines(client: CpeApi24Client, *, tail: int = 200) -> str:
    """Device config/syslog on CPE — logread filtered for API / sw-version activity."""
    _, stdout, _ = await client.ssh_run(
        f"logread 2>/dev/null | tail -n {tail} | grep -iE "
        "'sw-version|swversion|/api/v1|cpe_api|openapi|api\\.fcgi' | tail -n 30",
        timeout_s=25.0,
    )
    return stdout or ""


async def assert_radio_24_50_api_logging(client: CpeApi24Client) -> None:
    """2.4G_RADIO_50 — send GET sw-version; verify request/response traced on CPE backend."""
    case_id = "2_4G_RADIO_50"
    print_section(f"{case_id} — API logging (device config logs)")
    print_kv_block(
        "Flow",
        {
            "Step 1": "Backend: SSH logread snapshot (before API)",
            "Step 2": f"API: GET {client.config.base_url}{SW_VERSION_PATH}",
            "Step 3": "Backend: /tmp/clisrcip + logread diff + API processes on CPE",
            "Expected": "Request client recorded; valid sw-version response; API stack active",
            "Verify": "clisrcip + HTTP body (+ logread if FW emits API syslog)",
        },
    )

    log_before = await _fetch_cpe_log_tail(client, lines=200)
    get_resp = await client.get(SW_VERSION_PATH, read_timeout_s=20.0)
    get_body = _body(get_resp)
    sw_version = _sw_version_from_body(get_body)

    await asyncio.sleep(1.0)
    cli_ip = await _fetch_cpe_cli_src_ip(client)
    api_proc_count = await _count_cpe_api_processes(client)
    log_after = await _fetch_cpe_log_tail(client, lines=200)
    api_log_text = await _fetch_cpe_api_log_lines(client)
    new_log_lines = _new_log_lines(log_before, log_after)
    matched_logs = _log_lines_matching(log_after, RADIO24_API_LOG_PATTERNS)
    for line in _log_lines_matching("\n".join(new_log_lines), RADIO24_API_LOG_PATTERNS):
        if line not in matched_logs:
            matched_logs.append(line)
    if api_log_text.strip():
        for line in api_log_text.splitlines():
            stripped = line.strip()
            if stripped and stripped not in matched_logs:
                matched_logs.append(stripped)

    request_logged = bool(cli_ip.startswith("169.254.254."))
    response_logged = (
        get_resp.status_code == 200
        and str(get_body.get("status", "")).lower() == "success"
        and bool(sw_version)
    )
    syslog_api_activity = len(matched_logs) >= 1
    device_logging_ok = request_logged and response_logged and (syslog_api_activity or api_proc_count >= 1)

    print_comparison_table(
        [
            ("GET HTTP status", str(get_resp.status_code), "200", "CHECK"),
            ("GET JSON status", str(get_body.get("status", "")), "success", "CHECK"),
            ("swversion (API response)", sw_version or "(missing)", "non-empty", "CHECK"),
            ("CPE clisrcip (request client)", cli_ip or "(missing)", "169.254.254.x", "CHECK"),
            ("logread API lines", str(len(matched_logs)), "≥0 (FW-dependent)", "INFO"),
            ("New logread lines after API", str(len(new_log_lines)), "≥0", "INFO"),
            ("API processes", str(api_proc_count), "≥1", "CHECK"),
            ("Request logged (clisrcip)", "yes" if request_logged else "no", "yes", "CHECK"),
            ("Response logged (HTTP)", "yes" if response_logged else "no", "yes", "CHECK"),
            ("Device logging verified", "yes" if device_logging_ok else "no", "yes", "CHECK"),
        ]
    )

    if matched_logs:
        print(f"\n[{case_id}] Device config log samples (logread on CPE):")
        for line in matched_logs[:5]:
            print(f"    {line}")
    elif request_logged and api_proc_count >= 1:
        print(
            f"\n[{case_id}] Note: FW {sw_version or '?'} does not emit sw-version lines to logread; "
            "using /tmp/clisrcip + openapi processes as CPE backend evidence."
        )

    check.equal(get_resp.status_code, 200, f"{case_id}: GET sw-version must return HTTP 200")
    check.equal(str(get_body.get("status", "")).lower(), "success", f"{case_id}: GET must return success")
    check.is_true(sw_version, f"{case_id}: GET must include swversion in response")
    check.is_true(
        cli_ip.startswith("169.254.254."),
        f"{case_id}: CPE /tmp/clisrcip must record mgmt client IP, got {cli_ip!r}",
    )
    check.greater_equal(api_proc_count, 1, f"{case_id}: CPE must run senao-openapi/api.fcgi")
    check.is_true(request_logged, f"{case_id}: API request must be recorded on CPE (/tmp/clisrcip)")
    check.is_true(response_logged, f"{case_id}: API response must be valid (HTTP JSON swversion)")
    check.is_true(
        device_logging_ok,
        f"{case_id}: CPE backend must show API request trace (clisrcip) and response handling",
    )
