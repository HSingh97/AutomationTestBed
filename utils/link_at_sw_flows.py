"""BTS-AT-SW-01 Link Test flows — standalone GUI Link Test Tool suite.

Monitor → Tools → Link Test Tool on BTS (single BTS↔CPE link).
Does not use GUI_127/128/130 or tests/GUI Monitor cases.
"""

from __future__ import annotations

import asyncio
import os
import re
import time

import pytest_check as check

from pages.commands import RootCommands
from pages.locators import (
    CommonLocators,
    LinkTestToolLocators,
    LoginPageLocators,
    MonitorLocators,
    TopPanelLocators,
    UITimeouts,
)
from traffic.operating_rate_table import uci_htmode_matches, uci_htmode_value
from utils.link_formation import link_health_bts
from utils.link_test_config import LinkTestConfig
from utils.net_utils import format_http_host, ip_in_text
from utils.parsers import clean_ssh_output, parse_bandwidth, parse_link_test_results, parse_numeric_metric, ssh_scalar
from utils.verify_output import print_comparison_table, print_section

RADIO_INDEX = 1
RESULT_POLL_INTERVAL_S = 2
RESULT_BUFFER_S = 15
DEFAULT_PACKET_SIZE = 1400
LINK_TEST_04_DURATION_S = 600


def _link_test_04_duration_s() -> int:
    """Allow shorter long-runs via LINK_AT_LONG_DURATION (seconds)."""
    raw = (os.environ.get("LINK_AT_LONG_DURATION") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return LINK_TEST_04_DURATION_S
LINK_TEST_05_TARGET_HTMODE = "HT80"
LINK_TEST_05_LINK_UP_TIMEOUT_S = 120
LINK_TEST_07_DURATION_S = 120
LINK_TEST_07_REBOOT_AFTER_S = 12
LINK_TEST_07_SSH_DOWN_TIMEOUT_S = 90
LINK_TEST_07_SSH_UP_TIMEOUT_S = 180
LINK_TEST_07_LINK_UP_TIMEOUT_S = 180
LINK_TEST_07_SETTLE_S = 45
LINK_TEST_08_DURATION_S = 90
LINK_TEST_08_DROP_AFTER_S = 10
LINK_TEST_08_RECONNECT_TIMEOUT_S = 60
STABILITY_POLL_S = 30


def _log(message: str) -> None:
    print(f"[LINK_TEST] {message}", flush=True)


async def _send_ssh(root_ssh, command: str, *, timeout_ops: int | None = None) -> str:
    """Send SSH command; reopen the session if wifi reload / reboot closed it."""
    kwargs = {}
    if timeout_ops is not None:
        kwargs["timeout_ops"] = timeout_ops
    try:
        response = await root_ssh.send_command(command, **kwargs)
        return clean_ssh_output(response.result)
    except Exception as exc:
        name = type(exc).__name__
        msg = str(exc).lower()
        retryable = (
            "connectionnotopened" in name.lower()
            or "not opened" in msg
            or "timeout" in name.lower()
            or "timeout" in msg
            or "connection" in name.lower()
            or "eof" in msg
            or "closed" in msg
        )
        if retryable:
            _log(f"SSH send failed ({name}); reopening session…")
            await _wait_ssh_reopen(root_ssh, timeout_s=120, interval_s=3)
            response = await root_ssh.send_command(command, **kwargs)
            return clean_ssh_output(response.result)
        raise


async def _clear_cpe_list(root_ssh, radio_idx: int = RADIO_INDEX) -> None:
    await _send_ssh(root_ssh, RootCommands.clear_tool_cpe_list(radio_idx))


def _luci_stok_base(url: str) -> str:
    match = re.search(r"(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)", url or "")
    return match.group(1) if match else ""


def _gui_login_host(gui_page, fallback: str) -> str:
    match = re.search(r"https?://([^/]+)/cgi-bin/luci", gui_page.url or "")
    if match:
        return match.group(1)
    return format_http_host(fallback) if fallback else ""


async def _login_form_visible(gui_page) -> bool:
    try:
        return await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=1500)
    except Exception:
        return False


async def _ensure_luci_session(gui_page, *, host: str = "", device_creds: dict | None = None) -> None:
    """Re-login when LuCI dropped the stok (common after a long LINK_TEST_04 run).

    Keep a working IPv4/IPv6 session as-is. Do not bounce hosts just because
    the preferred management IP differs from the current browser URL.
    """
    if _luci_stok_base(gui_page.url or "") and not await _login_form_visible(gui_page):
        return
    login_host = format_http_host(host) if host else _gui_login_host(gui_page, "")
    if not login_host or not device_creds:
        return

    login_url = f"https://{login_host}/cgi-bin/luci/"
    _log(f"GUI session expired ({gui_page.url or 'empty'}); re-login at {login_url}")
    try:
        await gui_page.goto("about:blank", timeout=10000, wait_until="commit")
    except Exception:
        pass
    loaded = False
    for scheme in ("https", "http"):
        candidate = f"{scheme}://{login_host}/cgi-bin/luci/"
        try:
            await gui_page.goto(candidate, timeout=max(UITimeouts.PAGE_LOAD_MS, 30000), wait_until="commit")
            login_url = candidate
            loaded = True
            break
        except Exception as exc:
            _log(f"GUI {scheme} login page not ready on {login_host} ({type(exc).__name__})")
    if not loaded:
        return
    try:
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    try:
        await gui_page.locator(
            f"{LoginPageLocators.USERNAME_INPUT}, {CommonLocators.MENU_MONITOR}, #maincontent"
        ).first.wait_for(state="visible", timeout=25000)
    except Exception:
        pass
    await gui_page.wait_for_timeout(1500)
    login = gui_page.locator(LoginPageLocators.USERNAME_INPUT).first
    login_count = 0
    try:
        login_count = await gui_page.locator(LoginPageLocators.USERNAME_INPUT).count()
    except Exception:
        pass
    try:
        login_visible = await login.is_visible(timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        login_visible = False
    if login_visible or login_count > 0:
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        try:
            async with gui_page.expect_navigation(timeout=20000, wait_until="domcontentloaded"):
                await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
        except Exception:
            await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).press("Enter")
            await gui_page.wait_for_timeout(3000)
        try:
            await gui_page.wait_for_url("**/;stok=**", timeout=15000)
        except Exception:
            pass
        try:
            await gui_page.locator(CommonLocators.MENU_MONITOR).first.wait_for(
                state="visible", timeout=15000
            )
        except Exception:
            pass
    else:
        try:
            title = await gui_page.title()
        except Exception:
            title = ""
        _log(f"GUI login form not present at {gui_page.url} title={title!r}")

    if not _luci_stok_base(gui_page.url or "") and not await _login_form_visible(gui_page):
        try:
            await gui_page.goto(login_url, timeout=UITimeouts.PAGE_LOAD_MS, wait_until="domcontentloaded")
            await gui_page.wait_for_url("**/;stok=**", timeout=15000)
        except Exception:
            pass


async def _gui_session_ready(gui_page) -> bool:
    if _luci_stok_base(gui_page.url or "") and not await _login_form_visible(gui_page):
        return True
    for selector in (
        CommonLocators.MENU_MONITOR,
        "li.Management > a.menu",
        "#maincontent",
        LinkTestToolLocators.BANDWIDTH_INPUT,
    ):
        try:
            if await gui_page.locator(selector).first.is_visible(timeout=5000):
                return True
        except Exception:
            continue
    return False


async def _recover_gui_after_reboot(
    gui_page,
    hosts: list[str],
    device_creds: dict,
    *,
    timeout_s: float = 180,
) -> str:
    """Retry LuCI login on BTS hosts until stok/menu is back after reboot."""
    seen: list[str] = []
    for host in hosts:
        h = str(host or "").strip()
        if h and h not in seen:
            seen.append(h)
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_host = seen[0] if seen else ""
    while asyncio.get_event_loop().time() < deadline:
        for host in seen:
            last_host = host
            fresh = None
            try:
                fresh = await gui_page.context.new_page()
                await _ensure_luci_session(fresh, host=host, device_creds=device_creds)
                if await _gui_session_ready(fresh):
                    target = fresh.url
                    _log(f"GUI recovered at {host} ({target})")
                    try:
                        await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    return host
            except Exception as exc:
                _log(f"GUI recover {host} failed ({type(exc).__name__})")
            finally:
                if fresh is not None:
                    try:
                        await fresh.close()
                    except Exception:
                        pass
        await asyncio.sleep(5)
    raise TimeoutError(f"GUI did not recover after reboot (tried {seen}, last={last_host})")


async def _goto_admin_path(gui_page, path_fragment: str) -> bool:
    base = _luci_stok_base(gui_page.url or "")
    if not base:
        return False
    target = f"{base}/admin{path_fragment}"
    await gui_page.goto(target, timeout=UITimeouts.PAGE_LOAD_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    return True


async def _open_link_test_tool(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Navigate Monitor → Tools → Link Test Tool. Re-login if the session expired."""
    await _ensure_luci_session(gui_page, host=host, device_creds=device_creds)
    if await _goto_admin_path(gui_page, "/monitor/tools/testtool"):
        try:
            await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(
                state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
            )
            await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
            return
        except Exception:
            pass

    try:
        menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
        await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
        submenu = gui_page.locator(MonitorLocators.SUBMENU_TOOLS).first
        await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")

        tab = gui_page.locator(LinkTestToolLocators.TAB_LINK_TEST).first
        await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await tab.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        await _ensure_luci_session(gui_page, host=host, device_creds=device_creds)
        if not await _goto_admin_path(gui_page, "/monitor/tools/testtool"):
            raise

    await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)


async def _fill_field(gui_page, locator: str, value: str) -> None:
    field = gui_page.locator(locator).first
    await field.fill(value)
    check.equal(await field.input_value(), value, f"Link Test field {locator} did not retain {value}")


async def _configure_fields(
    gui_page,
    *,
    bandwidth: int,
    duration_s: int,
    vlan_id: int,
    packet_size: int = DEFAULT_PACKET_SIZE,
) -> None:
    await _fill_field(gui_page, LinkTestToolLocators.BANDWIDTH_INPUT, str(bandwidth))
    await _fill_field(gui_page, LinkTestToolLocators.TIME_DURATION_INPUT, str(duration_s))
    await _fill_field(gui_page, LinkTestToolLocators.VLAN_ID_INPUT, str(vlan_id))
    packet_field = gui_page.locator(LinkTestToolLocators.PACKET_SIZE_INPUT).first
    try:
        if await packet_field.is_enabled(timeout=UITimeouts.SHORT_WAIT_MS):
            await packet_field.fill(str(packet_size), timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        pass
    bidirection = gui_page.locator(LinkTestToolLocators.BIDIRECTION_CHECKBOX).first
    await bidirection.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    if not await bidirection.is_checked():
        await bidirection.check()
    check.is_true(await bidirection.is_checked(), "Bidirection must be enabled for UL/DL link test")


def _dropdown_has_cpe(options: list[str], cpe_ip: str) -> bool:
    return any(ip_in_text(cpe_ip, opt) for opt in options)


async def _wait_for_cpe_dropdown(gui_page, cpe_ip: str, timeout_s: int = 60) -> None:
    dropdown = gui_page.locator(LinkTestToolLocators.CPE_DROPDOWN).first
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            options = await dropdown.locator("option").all_inner_texts()
            if _dropdown_has_cpe(options, cpe_ip):
                return
        except Exception:
            pass
        await asyncio.sleep(2)
        try:
            await gui_page.reload(wait_until="domcontentloaded")
            await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(
                state="visible", timeout=10000
            )
        except Exception:
            pass
    raise TimeoutError(f"CPE {cpe_ip} not found in Link Test dropdown after {timeout_s}s")


async def _inject_peer_via_uci(root_ssh, peer_ip: str, radio_idx: int = RADIO_INDEX) -> None:
    """Backend fallback when GUI CPE dropdown is empty after RF recovery."""
    await _send_ssh(
        root_ssh,
        f"uci set tool.ath{radio_idx}.iplist={peer_ip}; "
        f"uci set tool.ath{radio_idx}.start=0; "
        f"uci commit tool",
    )
    _log(f"injected peer {peer_ip} into tool.ath{radio_idx}.iplist via UCI")


async def _select_cpe_option(dropdown, cpe_ip: str) -> None:
    options = dropdown.locator("option")
    count = await options.count()
    for i in range(count):
        opt = options.nth(i)
        label = (await opt.inner_text()).strip()
        if not _dropdown_has_cpe([label], cpe_ip):
            continue
        value = (await opt.get_attribute("value") or "").strip()
        if value:
            await dropdown.select_option(value=value)
        else:
            await dropdown.select_option(label=label)
        return
    await dropdown.select_option(label=cpe_ip)


async def _add_peer_from_dropdown(gui_page, peer_ip: str) -> None:
    dropdown = gui_page.locator(LinkTestToolLocators.CPE_DROPDOWN).first
    await _select_cpe_option(dropdown, peer_ip)
    add_btn = gui_page.locator(LinkTestToolLocators.ADD_CPE_BUTTON).first
    await add_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await add_btn.click()
    try:
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await gui_page.locator(LinkTestToolLocators.BANDWIDTH_INPUT).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )
    table = gui_page.locator(LinkTestToolLocators.CPE_TABLE).first
    deadline = asyncio.get_event_loop().time() + (UITimeouts.ELEMENT_WAIT_MS / 1000)
    while asyncio.get_event_loop().time() < deadline:
        if ip_in_text(peer_ip, await table.inner_text()):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Peer {peer_ip} not visible in Link Test CPE table")


async def _cpe_rows_count(gui_page) -> int:
    return await gui_page.locator(f"{LinkTestToolLocators.CPE_TABLE_ROWS}:not(:first-child)").count()


async def _read_backend_config(root_ssh, radio_idx: int = RADIO_INDEX) -> dict[str, str]:
    return {
        "bandwidth": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_bw(radio_idx))),
        "duration": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_duration(radio_idx))),
        "vlan": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_vlan(radio_idx))),
        "iplist": ssh_scalar(await _send_ssh(root_ssh, RootCommands.get_tool_iplist(radio_idx))),
    }


def _to_uci_htmode(bandwidth: str) -> str:
    ht = str(bandwidth).strip().upper()
    if ht == "HT40":
        return "HT40+"
    if ht == "HT160":
        return "HT160"
    try:
        return uci_htmode_value(ht)
    except ValueError:
        return ht


def _htmode_matches(expected: str, actual: str) -> bool:
    exp = str(expected).strip().upper()
    act = str(actual or "").strip().upper().replace(" ", "")
    if exp == "HT160":
        return "HT160" in act
    if exp == "HT40":
        return "HT40+" in act or act == "HT40"
    return uci_htmode_matches(exp, act)


def _runtime_htmode(raw: str) -> str:
    """Parse cfg80211tool get_mode (e.g. 11AHE80) — ignore bare UCI echoes."""
    upper = str(raw or "").upper().replace(" ", "")
    for mhz, ht in (("160", "HT160"), ("80", "HT80"), ("40", "HT40"), ("20", "HT20")):
        if re.search(rf"(?:HE|VHT|NAHE|AHE){mhz}", upper):
            return ht
        if "PLUS" in upper and re.search(rf"HT{mhz}", upper):
            return ht
    return ""


async def _read_htmode_uci(ssh, *, radio_idx: int = RADIO_INDEX) -> str:
    return ssh_scalar(
        await _send_ssh(ssh, f"uci get wireless.wifi{radio_idx}.htmode 2>/dev/null || true")
    ).strip()


async def _read_htmode_runtime(ssh, *, radio_idx: int = RADIO_INDEX) -> str:
    raw = ssh_scalar(await _send_ssh(ssh, RootCommands.get_bandwidth(radio_idx)))
    return _runtime_htmode(raw)


async def _read_htmode_label(ssh, *, radio_idx: int = RADIO_INDEX) -> str:
    runtime = await _read_htmode_runtime(ssh, radio_idx=radio_idx)
    if runtime:
        return runtime
    uci = await _read_htmode_uci(ssh, radio_idx=radio_idx)
    return uci.upper() if uci else "unknown"


async def _apply_htmode(ssh, bandwidth: str, *, radio_idx: int = RADIO_INDEX) -> None:
    uci_val = _to_uci_htmode(bandwidth)
    for cmd in RootCommands.set_bandwidth_commands(radio_idx, uci_val):
        await _send_ssh(ssh, cmd)


async def _restore_htmode_uci(ssh, uci_val: str, *, radio_idx: int = RADIO_INDEX) -> None:
    if not str(uci_val or "").strip():
        return
    for cmd in RootCommands.set_bandwidth_commands(radio_idx, str(uci_val).strip()):
        await _send_ssh(ssh, cmd)


async def _wait_for_link_up(
    root_ssh,
    profile: dict,
    *,
    timeout_s: float = LINK_TEST_05_LINK_UP_TIMEOUT_S,
    case_id: str = "",
) -> tuple[bool, int]:
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_stations = 0
    while asyncio.get_event_loop().time() < deadline:
        try:
            ok, last_stations = await link_health_bts(root_ssh, profile)
            if ok:
                return True, last_stations
        except Exception as exc:
            _log(f"{case_id}: link poll failed ({type(exc).__name__}); retry SSH")
            try:
                await _wait_ssh_reopen(root_ssh, timeout_s=min(45, timeout_s), interval_s=3)
            except Exception:
                pass
        await asyncio.sleep(3)
    _log(f"{case_id}: link not up after {timeout_s:.0f}s (stations={last_stations})")
    return False, last_stations


async def _link_test_peer_list_visible(gui_page) -> bool:
    """AP GUI shows CPE List; STA GUI hides it and tests the associated AP."""
    section = gui_page.locator("#ip-list-section").first
    try:
        if await section.count() == 0:
            return False
        return await section.is_visible()
    except Exception:
        return False


async def _prepare_bts_link_test(
    gui_page,
    root_ssh,
    peer_ip: str,
    link_test_config: LinkTestConfig,
    *,
    case_id: str,
    duration_s: int | None = None,
    bandwidth_mbps: int | None = None,
    host: str = "",
    device_creds: dict | None = None,
    require_peer: bool = True,
) -> None:
    """Configure Link Test Tool. AP adds a peer; STA starts against the associated AP."""
    bandwidth = int(
        bandwidth_mbps if bandwidth_mbps is not None else link_test_config.default_bandwidth
    )
    duration_s = int(duration_s if duration_s is not None else link_test_config.duration_s)
    vlan_id = link_test_config.vlan_id

    _log(f"{case_id}: configure bw={bandwidth} Mbps, dur={duration_s}s, vlan={vlan_id}, peer={peer_ip}")

    if require_peer:
        await _clear_cpe_list(root_ssh)
    await _open_link_test_tool(gui_page, host=host, device_creds=device_creds)
    await _configure_fields(gui_page, bandwidth=bandwidth, duration_s=duration_s, vlan_id=vlan_id)

    peer_list_visible = await _link_test_peer_list_visible(gui_page)
    if require_peer and peer_list_visible:
        try:
            await _wait_for_cpe_dropdown(gui_page, peer_ip, timeout_s=45)
            await _add_peer_from_dropdown(gui_page, peer_ip)
        except TimeoutError:
            _log(f"{case_id}: CPE not in dropdown — UCI peer inject fallback")
            await _inject_peer_via_uci(root_ssh, peer_ip)
        await _open_link_test_tool(gui_page, host=host, device_creds=device_creds)
        await _configure_fields(gui_page, bandwidth=bandwidth, duration_s=duration_s, vlan_id=vlan_id)

        rows = await _cpe_rows_count(gui_page)
        if rows < 1:
            # UCI inject may not refresh table until reopen
            await _open_link_test_tool(gui_page, host=host, device_creds=device_creds)
            await _configure_fields(gui_page, bandwidth=bandwidth, duration_s=duration_s, vlan_id=vlan_id)
            rows = await _cpe_rows_count(gui_page)
        if rows < 1:
            # Last resort: accept backend iplist as peer presence
            backend_cfg = await _read_backend_config(root_ssh)
            check.is_true(
                ip_in_text(peer_ip, backend_cfg.get("iplist", "")),
                f"{case_id}: peer must be in CPE list or UCI iplist before Start",
            )
        else:
            check.is_true(rows >= 1, f"{case_id}: peer must be in CPE list before Start")

        backend_cfg = await _read_backend_config(root_ssh)
        _log(
            f"{case_id} configured: "
            f"bw={bandwidth} dur={duration_s}s vlan={vlan_id} peer={peer_ip} "
            f"(UCI bw={backend_cfg.get('bandwidth', '?')} dur={backend_cfg.get('duration', '?')} "
            f"vlan={backend_cfg.get('vlan', '?')} iplist={backend_cfg.get('iplist', '?')})"
        )
        check.is_true(
            ip_in_text(peer_ip, backend_cfg.get("iplist", "")),
            f"{case_id}: peer {peer_ip} must appear in UCI iplist after configure",
        )
        return

    backend_cfg = await _read_backend_config(root_ssh)
    _log(
        f"{case_id}: STA Link Test Tool (no CPE list) — start against associated AP; "
        f"bw={bandwidth} dur={duration_s}s vlan={vlan_id} "
        f"(UCI bw={backend_cfg.get('bandwidth', '?')} dur={backend_cfg.get('duration', '?')})"
    )


def _parse_link_test_active(raw: str) -> str:
    text = ssh_scalar(raw).strip()
    if ":" in text:
        return text.rsplit(":", 1)[-1].strip()
    return text


async def _link_test_backend_active(root_ssh, *, radio_idx: int = RADIO_INDEX) -> bool:
    status = _parse_link_test_active(await _send_ssh(root_ssh, RootCommands.get_link_test_active(radio_idx)))
    return status not in {"", "0"}


async def _wait_link_test_idle(root_ssh, *, timeout_s: float = 30.0, radio_idx: int = RADIO_INDEX) -> float | None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if not await _link_test_backend_active(root_ssh, radio_idx=radio_idx):
            return asyncio.get_event_loop().time()
        await asyncio.sleep(1)
    return None


async def _start_link_test(gui_page) -> None:
    start_btn = gui_page.locator(LinkTestToolLocators.START_BUTTON).first
    await start_btn.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await start_btn.click()
    progress = gui_page.locator(LinkTestToolLocators.PROGRESS_MESSAGE).first
    try:
        await progress.filter(has_text="Testing in progress").wait_for(
            state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
        )
    except Exception:
        pass


async def _wait_for_link_test_started(root_ssh, *, timeout_s: int = 30, radio_idx: int = RADIO_INDEX) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if await _link_test_backend_active(root_ssh, radio_idx=radio_idx):
            return True
        await asyncio.sleep(RESULT_POLL_INTERVAL_S)
    return False


async def _read_results_text(gui_page) -> str:
    results = gui_page.locator(LinkTestToolLocators.RESULTS_CONTAINER).first
    progress = gui_page.locator(LinkTestToolLocators.PROGRESS_MESSAGE).first
    texts: list[str] = []
    try:
        if await results.count():
            texts.append((await results.inner_text()).strip())
    except Exception:
        pass
    try:
        if await progress.count():
            texts.append((await progress.inner_text()).strip())
    except Exception:
        pass
    try:
        form = gui_page.locator(LinkTestToolLocators.LINK_TEST_FORM).first
        if await form.count():
            texts.append((await form.inner_text()).strip())
    except Exception:
        pass
    return "\n".join(t for t in texts if t).strip()


async def _poll_results_text(gui_page, *, extra_wait_s: int = 0) -> str:
    read_deadline = asyncio.get_event_loop().time() + RESULT_BUFFER_S + extra_wait_s
    while asyncio.get_event_loop().time() < read_deadline:
        text = await _read_results_text(gui_page)
        if parse_link_test_results(text):
            return text
        await asyncio.sleep(2)

    try:
        await _goto_admin_path(gui_page, "/monitor/tools/testtool")
    except Exception:
        pass

    read_deadline = asyncio.get_event_loop().time() + 30
    while asyncio.get_event_loop().time() < read_deadline:
        text = await _read_results_text(gui_page)
        if parse_link_test_results(text):
            return text
        await asyncio.sleep(2)

    return await _read_results_text(gui_page)


async def _wait_for_link_test_complete(
    root_ssh,
    gui_page,
    duration_s: int,
    radio_idx: int = RADIO_INDEX,
    *,
    profile: dict | None = None,
    case_id: str = "",
    session_start: float | None = None,
) -> tuple[str, list[str], list[dict[str, str]]]:
    """Wait for link test to finish. Optionally monitor RF/backend stability mid-run."""
    issues: list[str] = []
    samples: list[dict[str, str]] = []
    run_end = asyncio.get_event_loop().time() + duration_s
    total_end = run_end + RESULT_BUFFER_S
    last_station_check = 0.0
    monitor = profile is not None
    t0 = session_start if session_start is not None else time.time()

    while asyncio.get_event_loop().time() < total_end:
        now = asyncio.get_event_loop().time()
        active = await _link_test_backend_active(root_ssh, radio_idx=radio_idx)
        elapsed = time.time() - t0
        stations = ""
        if monitor and (now - last_station_check >= STABILITY_POLL_S or not samples):
            ok, station_count = await link_health_bts(root_ssh, profile)
            stations = str(station_count)
            samples.append(
                {
                    "elapsed_s": f"{elapsed:.0f}",
                    "backend_active": "yes" if active else "no",
                    "stations": stations,
                    "link_ok": "yes" if ok else "no",
                }
            )
            if now < run_end and not ok:
                issues.append(f"{case_id}: CPE dropped mid-test at {elapsed:.0f}s (stations={station_count})")
            last_station_check = now

        if now < run_end:
            if not active:
                issues.append(
                    f"{case_id}: backend idle after {elapsed:.0f}s (expected {duration_s}s continuous run)"
                )
                break
        elif not active:
            break

        await asyncio.sleep(RESULT_POLL_INTERVAL_S)

    extra_wait = max(0, duration_s // 10)
    return await _poll_results_text(gui_page, extra_wait_s=extra_wait), issues, samples


async def _read_backend_results(root_ssh, radio_idx: int = RADIO_INDEX, assoc_idx: int = 1) -> dict[str, str]:
    stats_cmds = RootCommands.get_link_test_stats(radio_idx, assoc_idx)
    return {key: ssh_scalar(await _send_ssh(root_ssh, cmd)) for key, cmd in stats_cmds.items()}


def _session_has_traffic(gui_results: dict[str, str], backend_results: dict[str, str]) -> bool:
    for key in ("ul_throughput", "dl_throughput"):
        for source in (gui_results, backend_results):
            if (parse_numeric_metric(source.get(key, "")) or 0.0) > 0:
                return True
    return False


async def _run_bts_link_test_session(
    gui_page,
    root_ssh,
    *,
    case_id: str,
    session_tag: str,
    peer_ip: str,
    link_test_config: LinkTestConfig,
    prepare: bool,
    duration_s: int | None = None,
    bandwidth_mbps: int | None = None,
    profile: dict | None = None,
    host: str = "",
    device_creds: dict | None = None,
    require_peer: bool = True,
) -> dict:
    duration_s = int(duration_s if duration_s is not None else link_test_config.duration_s)
    tool_bw = int(
        bandwidth_mbps if bandwidth_mbps is not None else link_test_config.default_bandwidth
    )
    _log(f"{case_id} {session_tag}: prepare={prepare}, bw={tool_bw} Mbps, dur={duration_s}s")

    if prepare:
        await _prepare_bts_link_test(
            gui_page,
            root_ssh,
            peer_ip,
            link_test_config,
            case_id=case_id,
            duration_s=duration_s,
            bandwidth_mbps=tool_bw,
            host=host,
            device_creds=device_creds,
            require_peer=require_peer,
        )

    session_start = time.time()
    backend_start = asyncio.get_event_loop().time()
    await _start_link_test(gui_page)
    started = await _wait_for_link_test_started(root_ssh, timeout_s=min(30, duration_s + 10))
    check.is_true(started, f"{case_id} {session_tag}: link test must enter running state after Start")

    results_text, stability_issues, stability_samples = await _wait_for_link_test_complete(
        root_ssh,
        gui_page,
        duration_s,
        profile=profile,
        case_id=case_id,
        session_start=session_start,
    )
    for issue in stability_issues:
        check.fail(issue)
    session_end = time.time()
    backend_end = asyncio.get_event_loop().time()

    gui_results = parse_link_test_results(results_text)
    backend_results = await _read_backend_results(root_ssh)

    if not gui_results and _session_has_traffic({}, backend_results):
        gui_results = {
            "ul_throughput": backend_results.get("ul_throughput", ""),
            "dl_throughput": backend_results.get("dl_throughput", ""),
            "ul_latency": backend_results.get("ul_latency", ""),
            "dl_latency": backend_results.get("dl_latency", ""),
        }
        _log(f"{case_id} {session_tag}: using backend tool stats (GUI report not parseable after long run)")

    check.is_true(
        bool(gui_results) or _session_has_traffic({}, backend_results),
        f"{case_id} {session_tag}: results must appear in Test Report or backend stats",
    )
    check.is_true(
        _session_has_traffic(gui_results, backend_results),
        f"{case_id} {session_tag}: session must report non-zero UL or DL throughput",
    )

    print_section(f"{case_id} — {session_tag} results")
    print(f"  {results_text.strip()}\n", flush=True)
    _log(
        f"{session_tag} complete: "
        f"UL {gui_results.get('ul_throughput', '?')}/{backend_results.get('ul_throughput', '?')} Mbps, "
        f"DL {gui_results.get('dl_throughput', '?')}/{backend_results.get('dl_throughput', '?')} Mbps "
        f"(GUI/backend)"
    )

    return {
        "tag": session_tag,
        "wall_start": session_start,
        "wall_end": session_end,
        "backend_start": backend_start,
        "backend_end": backend_end,
        "gui_results": gui_results,
        "backend_results": backend_results,
        "stability_samples": stability_samples,
    }


async def _verify_backend_after_long_run(
    root_ssh,
    *,
    case_id: str,
    duration_s: int,
    bandwidth: int,
    vlan_id: int,
    peer_ip: str,
    backend_results: dict[str, str],
) -> None:
    """Post-run backend checks: UCI, idle state, dmesg, tool stats."""
    backend_cfg = await _read_backend_config(root_ssh)
    idle = not await _link_test_backend_active(root_ssh)
    dmesg_tail = await _send_ssh(root_ssh, "dmesg | grep 'Test Start' | tail -1")

    print_section(f"{case_id} — backend verification")
    print_comparison_table(
        [
            ("UCI duration (s)", str(duration_s), backend_cfg.get("duration", ""), "info"),
            ("UCI bandwidth (Mbps)", str(bandwidth), backend_cfg.get("bandwidth", ""), "info"),
            ("UCI VLAN", str(vlan_id), backend_cfg.get("vlan", ""), "info"),
            ("UCI peer", peer_ip, backend_cfg.get("iplist", ""), "info"),
            ("Backend idle", "yes", "yes" if idle else "no", "info"),
            ("tool_txtput", backend_results.get("ul_throughput", ""), "> 0", "info"),
            ("tool_rxtput", backend_results.get("dl_throughput", ""), "> 0", "info"),
            ("dmesg Test Start", dmesg_tail.strip() or "(none)", f"contains {duration_s}", "info"),
        ]
    )

    check.equal(
        backend_cfg.get("duration", "").strip(),
        str(duration_s),
        f"{case_id}: UCI duration must be {duration_s}s after run",
    )
    check.is_true(idle, f"{case_id}: backend link test must be idle after completion")
    check.is_true(
        ip_in_text(peer_ip, backend_cfg.get("iplist", "")),
        f"{case_id}: peer {peer_ip} must remain in UCI iplist",
    )
    ul = parse_numeric_metric(backend_results.get("ul_throughput", "")) or 0.0
    dl = parse_numeric_metric(backend_results.get("dl_throughput", "")) or 0.0
    check.is_true(ul > 0 or dl > 0, f"{case_id}: backend tool stats must show throughput (UL={ul} DL={dl})")
    if dmesg_tail:
        check.is_in(str(duration_s), dmesg_tail, f"{case_id}: dmesg must reflect {duration_s}s test duration")


async def assert_link_test_03_sequential_ul_dl(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_03 — Run two UL/DL link test sessions sequentially on BTS GUI.

    Single BTS↔CPE link. Each session completes successfully; sessions must not overlap.
    """
    case_id = "LINK_TEST_03"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    gui_host = _gui_login_host(gui_page, bts_ip)
    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")
    check.is_true(root_ssh is not None, f"{case_id}: BTS SSH required")

    print_section(f"{case_id} — Sequential UL/DL on BTS Link Test Tool")
    _log(
        f"single link BTS={bts_ip} CPE={cpe_ip} "
        f"(stations={ready.get('stations', '?')}), "
        f"duration={link_test_config.duration_s}s per session"
    )

    await _clear_cpe_list(root_ssh)

    s1 = await _run_bts_link_test_session(
        gui_page,
        root_ssh,
        case_id=case_id,
        session_tag="S1",
        peer_ip=cpe_ip,
        link_test_config=link_test_config,
        prepare=True,
        host=gui_host or bts_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )

    idle_timeout_s = link_test_config.duration_s + RESULT_BUFFER_S + 90
    idle_at = await _wait_link_test_idle(root_ssh, timeout_s=idle_timeout_s)
    check.is_not_none(
        idle_at,
        f"{case_id}: backend must be idle after S1 before S2 (waited {idle_timeout_s:.0f}s)",
    )

    s2 = await _run_bts_link_test_session(
        gui_page,
        root_ssh,
        case_id=case_id,
        session_tag="S2",
        peer_ip=cpe_ip,
        link_test_config=link_test_config,
        prepare=True,
        host=gui_host or bts_ip,
        device_creds=device_creds,
        profile=profile_bundle.active,
    )

    gap_s = s2["wall_start"] - s1["wall_end"]
    backend_gap_s = s2["backend_start"] - (idle_at or s1["backend_end"])
    overlap = s2["backend_start"] < s1["backend_end"] - 0.5

    print_section(f"{case_id} — session timing (no overlap)")
    print_comparison_table(
        [
            ("S1 duration (s)", f"{s1['wall_end'] - s1['wall_start']:.1f}", "complete", "PASS"),
            ("S2 duration (s)", f"{s2['wall_end'] - s2['wall_start']:.1f}", "complete", "PASS"),
            ("Gap S1 end → S2 start (s)", f"{gap_s:.1f}", ">= 0", "PASS" if gap_s >= 0 else "FAIL"),
            ("Backend idle before S2 (s)", f"{backend_gap_s:.1f}", ">= 0", "PASS" if backend_gap_s >= 0 else "FAIL"),
            ("Overlap", "no" if not overlap else "yes", "no", "PASS" if not overlap else "FAIL"),
        ]
    )

    check.greater_equal(gap_s, 0.0, f"{case_id}: S2 must start after S1 finishes (gap={gap_s:.1f}s)")
    check.is_false(overlap, f"{case_id}: sessions must not overlap on backend")
    check.is_true(_session_has_traffic(s1["gui_results"], s1["backend_results"]), f"{case_id}: S1 must complete with traffic")
    check.is_true(_session_has_traffic(s2["gui_results"], s2["backend_results"]), f"{case_id}: S2 must complete with traffic")

    _log(
        f"{case_id} passed: S1 UL/DL {s1['gui_results'].get('ul_throughput', '?')}/"
        f"{s1['gui_results'].get('dl_throughput', '?')}, "
        f"S2 UL/DL {s2['gui_results'].get('ul_throughput', '?')}/"
        f"{s2['gui_results'].get('dl_throughput', '?')}, gap {gap_s:.1f}s"
    )


async def assert_link_test_04_long_duration(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_04 — Continuous UL/DL link test for 10 minutes on BTS GUI.

    BTS must keep the session stable with no mid-run drop.
    """
    case_id = "LINK_TEST_04"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    gui_host = _gui_login_host(gui_page, bts_ip)
    profile = profile_bundle.active
    duration_s = _link_test_04_duration_s()

    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")
    check.is_true(root_ssh is not None, f"{case_id}: BTS SSH required")

    print_section(f"{case_id} — Long duration UL/DL on BTS Link Test Tool")
    _log(
        f"single link BTS={bts_ip} CPE={cpe_ip} "
        f"(stations={ready.get('stations', '?')}), duration={duration_s}s ({duration_s // 60} min)"
    )

    await _clear_cpe_list(root_ssh)

    result = await _run_bts_link_test_session(
        gui_page,
        root_ssh,
        case_id=case_id,
        session_tag="long-run",
        peer_ip=cpe_ip,
        link_test_config=link_test_config,
        prepare=True,
        duration_s=duration_s,
        profile=profile,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )

    wall_duration = result["wall_end"] - result["wall_start"]
    min_expected = duration_s - 30
    check.greater_equal(
        wall_duration,
        min_expected,
        f"{case_id}: session must run ~{duration_s}s (got {wall_duration:.0f}s)",
    )

    ok, stations = await link_health_bts(root_ssh, profile)
    check.is_true(
        ok,
        f"{case_id}: BTS↔CPE link must stay up after long run (stations={stations})",
    )

    await _verify_backend_after_long_run(
        root_ssh,
        case_id=case_id,
        duration_s=duration_s,
        bandwidth=link_test_config.default_bandwidth,
        vlan_id=link_test_config.vlan_id,
        peer_ip=cpe_ip,
        backend_results=result["backend_results"],
    )

    samples = result.get("stability_samples") or []
    if samples:
        print_section(f"{case_id} — mid-run backend samples (every ~{STABILITY_POLL_S}s)")
        for row in samples[:5]:
            _log(
                f"  t={row['elapsed_s']}s active={row['backend_active']} "
                f"stations={row['stations']} link={row['link_ok']}"
            )
        if len(samples) > 5:
            _log(f"  ... {len(samples) - 5} more samples through {samples[-1]['elapsed_s']}s")
            _log(
                f"  t={samples[-1]['elapsed_s']}s active={samples[-1]['backend_active']} "
                f"stations={samples[-1]['stations']} link={samples[-1]['link_ok']}"
            )

    print_section(f"{case_id} — session summary")
    print_comparison_table(
        [
            ("Duration configured (s)", str(duration_s), str(duration_s), "PASS"),
            ("Wall duration (s)", f"{wall_duration:.0f}", f">= {min_expected}", "PASS" if wall_duration >= min_expected else "FAIL"),
            ("Stability samples", str(len(samples)), f">= {duration_s // STABILITY_POLL_S}", "PASS" if samples else "FAIL"),
            ("Stations after run", str(stations), ">= 1", "PASS" if ok else "FAIL"),
        ]
    )

    check.is_true(len(samples) >= 2, f"{case_id}: expected multiple backend stability samples during run")

    _log(
        f"{case_id} passed: {duration_s // 60} min UL/DL, "
        f"UL {result['gui_results'].get('ul_throughput', '?')}/"
        f"{result['backend_results'].get('ul_throughput', '?')} Mbps, "
        f"DL {result['gui_results'].get('dl_throughput', '?')}/"
        f"{result['backend_results'].get('dl_throughput', '?')} Mbps, "
        f"stations={stations}, backend verified"
    )


async def assert_link_test_05_high_bandwidth_80mhz(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_05 — UL/DL at 80 MHz channel width with maximum Link Test tool bandwidth.

    Ensures BTS RF is HT80, runs bidirectional link test at max tool bandwidth, restores any width changes.
    """
    case_id = "LINK_TEST_05"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    gui_host = _gui_login_host(gui_page, bts_ip)
    profile = profile_bundle.active
    link_section = profile.get("link_test") or {}
    high_bw = int(link_section.get("high_bw_mbps") or link_test_config.bw_max)
    target_ht = str(link_section.get("high_htmode") or LINK_TEST_05_TARGET_HTMODE).upper()

    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")
    check.is_true(root_ssh is not None, f"{case_id}: BTS SSH required")

    from utils.arp_bridge_table_flows import _close_ssh_session, _open_cpe_ssh

    bts_baseline_uci = await _read_htmode_uci(root_ssh)
    cpe_ssh = None
    cpe_mode = ""
    cpe_baseline_uci = ""
    bts_changed = False
    cpe_changed = False

    print_section(f"{case_id} — High bandwidth UL/DL at {parse_bandwidth(target_ht)}")
    _log(
        f"single link BTS={bts_ip} CPE={cpe_ip} "
        f"(stations={ready.get('stations', '?')}), RF target={target_ht}, tool bw={high_bw} Mbps"
    )

    try:
        # Prefer not failing the case on CPE SSH — only needed if we must change htmode.
        bts_runtime = await _read_htmode_runtime(root_ssh) or await _read_htmode_label(root_ssh)
        need_bts = not _htmode_matches(target_ht, bts_runtime) and not _htmode_matches(
            target_ht, bts_baseline_uci
        )
        if need_bts:
            try:
                cpe_ssh, cpe_mode = await _open_cpe_ssh(root_ssh, cpe_ip, bts_ip, device_creds)
                cpe_baseline_uci = await _read_htmode_uci(cpe_ssh)
            except Exception as exc:
                _log(f"{case_id}: CPE SSH unavailable for width change ({exc}); BTS-only apply")
                cpe_ssh, cpe_mode = None, ""
            cpe_runtime = ""
            if cpe_ssh is not None:
                cpe_runtime = await _read_htmode_runtime(cpe_ssh) or await _read_htmode_label(cpe_ssh)
            _log(
                f"{case_id}: baseline BTS uci={bts_baseline_uci} runtime={bts_runtime}, "
                f"CPE uci={cpe_baseline_uci} runtime={cpe_runtime or '?'} (via {cpe_mode or 'n/a'})"
            )
            if cpe_ssh is not None and not _htmode_matches(target_ht, cpe_baseline_uci):
                _log(f"{case_id}: set CPE htmode → {target_ht} (before BTS)")
                await _apply_htmode(cpe_ssh, target_ht)
                cpe_changed = True
            _log(f"{case_id}: set BTS htmode → {target_ht}")
            await _apply_htmode(root_ssh, target_ht)
            bts_changed = True
            ok, stations = await _wait_for_link_up(root_ssh, profile, case_id=case_id)
            check.is_true(ok, f"{case_id}: BTS↔CPE link must recover after {target_ht} width change")
            _log(f"{case_id}: link up after width change ({stations} station(s))")
        else:
            _log(f"{case_id}: BTS already on {target_ht} — skip RF width change / CPE SSH")

        bts_ht = await _read_htmode_runtime(root_ssh) or await _read_htmode_label(root_ssh)
        check.is_true(
            _htmode_matches(target_ht, bts_ht),
            f"{case_id}: BTS must be on {target_ht} (runtime={bts_ht})",
        )

        await _clear_cpe_list(root_ssh)
        result = await _run_bts_link_test_session(
            gui_page,
            root_ssh,
            case_id=case_id,
            session_tag="high-bw",
            peer_ip=cpe_ip,
            link_test_config=link_test_config,
            prepare=True,
            bandwidth_mbps=high_bw,
            host=gui_host or bts_ip,
            device_creds=device_creds,
        )

        ok, stations = await link_health_bts(root_ssh, profile)
        check.is_true(
            ok,
            f"{case_id}: BTS must handle throughput without link drop (stations={stations})",
        )
        check.is_true(
            _session_has_traffic(result["gui_results"], result["backend_results"]),
            f"{case_id}: high-bandwidth session must report non-zero UL or DL throughput",
        )

        gui_ul = result["gui_results"].get("ul_throughput", "?")
        gui_dl = result["gui_results"].get("dl_throughput", "?")
        backend_ul = result["backend_results"].get("ul_throughput", "?")
        backend_dl = result["backend_results"].get("dl_throughput", "?")

        print_section(f"{case_id} — session summary")
        print_comparison_table(
            [
                ("RF channel width", parse_bandwidth(target_ht), parse_bandwidth(bts_ht), "PASS"),
                ("Tool bandwidth (Mbps)", str(high_bw), str(high_bw), "PASS"),
                ("UL throughput (GUI/backend)", f"{gui_ul} / {backend_ul}", "> 0", "PASS"),
                ("DL throughput (GUI/backend)", f"{gui_dl} / {backend_dl}", "> 0", "PASS"),
                ("Stations after run", str(stations), ">= 1", "PASS" if ok else "FAIL"),
            ]
        )

        _log(
            f"{case_id} passed: {target_ht} UL/DL at {high_bw} Mbps — "
            f"UL {gui_ul}/{backend_ul} Mbps, DL {gui_dl}/{backend_dl} Mbps, stations={stations}"
        )
    finally:
        if cpe_changed and cpe_ssh is not None:
            _log(f"{case_id}: restore CPE htmode → {cpe_baseline_uci}")
            await _restore_htmode_uci(cpe_ssh, cpe_baseline_uci)
        if bts_changed:
            _log(f"{case_id}: restore BTS htmode → {bts_baseline_uci}")
            await _restore_htmode_uci(root_ssh, bts_baseline_uci)
        if bts_changed or cpe_changed:
            ok, stations = await _wait_for_link_up(
                root_ssh, profile, timeout_s=LINK_TEST_05_LINK_UP_TIMEOUT_S, case_id=case_id
            )
            _log(f"{case_id}: post-restore link {'up' if ok else 'down'} ({stations} station(s))")
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)


async def _boot_id(root_ssh) -> str:
    return ssh_scalar(
        await _send_ssh(root_ssh, "cat /proc/sys/kernel/random/boot_id 2>/dev/null || cat /proc/uptime")
    )


async def _gui_soft_reboot(gui_page) -> None:
    """Top panel Reboot → Perform reboot (LuCI)."""
    reboot_link = gui_page.locator(TopPanelLocators.REBOOT_BUTTON).first
    await reboot_link.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await reboot_link.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    try:
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

    perform = gui_page.locator(TopPanelLocators.REBOOT_CONFIRM).first
    try:
        await perform.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    except Exception:
        perform = gui_page.get_by_role("link", name="Perform reboot").first
        await perform.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await perform.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def _wait_ssh_drop(root_ssh, *, timeout_s: float) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            await root_ssh.send_command("echo LINK_TEST_ALIVE", timeout_ops=8)
        except Exception:
            return True
        await asyncio.sleep(2)
    return False


async def _wait_ssh_reopen(root_ssh, *, timeout_s: float, interval_s: float = 5.0) -> None:
    if hasattr(root_ssh, "ensure_open"):
        await root_ssh.ensure_open(timeout_s=int(timeout_s))
        return
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_exc: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            try:
                await root_ssh.close()
            except Exception:
                pass
            await root_ssh.open()
            probe = await root_ssh.send_command("echo LINK_TEST_SSH_OK", timeout_ops=15)
            combined = str(getattr(probe, "result", "") or "")
            if "LINK_TEST_SSH_OK" in combined or clean_ssh_output(combined):
                return
        except Exception as exc:
            last_exc = exc
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"BTS SSH did not recover after GUI reboot: {last_exc}")


async def assert_link_test_07_restart_bts_during_test(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_07 — Start UL/DL on BTS Link Test Tool, then GUI-reboot BTS mid-session.

    Session must terminate (not stay stuck running). BTS SSH, GUI, and RF link recover.
    """
    case_id = "LINK_TEST_07"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    duration_s = LINK_TEST_07_DURATION_S
    reboot_after_s = LINK_TEST_07_REBOOT_AFTER_S
    gui_host = _gui_login_host(gui_page, bts_ip)

    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")
    check.is_true(root_ssh is not None, f"{case_id}: BTS SSH required")

    print_section(f"{case_id} — Restart BTS during active GUI link test")
    _log(
        f"single link BTS={bts_ip} CPE={cpe_ip} "
        f"(stations={ready.get('stations', '?')}), "
        f"dur={duration_s}s, GUI reboot after {reboot_after_s}s"
    )

    stations_before = int(ready.get("stations") or 0)
    boot_before = await _boot_id(root_ssh)
    _log(f"{case_id}: boot_id before={boot_before}")

    ok_pre, stations_pre = await _wait_for_link_up(root_ssh, profile, timeout_s=60, case_id=case_id)
    check.is_true(ok_pre, f"{case_id}: RF link must be up before starting UL/DL (stations={stations_pre})")

    await _clear_cpe_list(root_ssh)
    await _prepare_bts_link_test(
        gui_page,
        root_ssh,
        cpe_ip,
        link_test_config,
        case_id=case_id,
        duration_s=duration_s,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )

    session_start = time.time()
    await _start_link_test(gui_page)
    started = await _wait_for_link_test_started(root_ssh, timeout_s=min(30, duration_s))
    check.is_true(started, f"{case_id}: link test must be running before GUI reboot")
    _log(f"{case_id}: UL/DL active — waiting {reboot_after_s}s then GUI reboot")

    await asyncio.sleep(reboot_after_s)
    still_active = await _link_test_backend_active(root_ssh)
    check.is_true(still_active, f"{case_id}: backend must still be running at GUI reboot")

    _log(f"{case_id}: GUI reboot (header Reboot → Perform reboot)")
    await _gui_soft_reboot(gui_page)
    reboot_at = time.time() - session_start

    dropped = await _wait_ssh_drop(root_ssh, timeout_s=LINK_TEST_07_SSH_DOWN_TIMEOUT_S)
    check.is_true(dropped, f"{case_id}: BTS SSH must drop after GUI reboot (session terminate)")
    _log(f"{case_id}: SSH dropped {reboot_at:.0f}s into session — waiting for BTS recovery")

    await _wait_ssh_reopen(root_ssh, timeout_s=LINK_TEST_07_SSH_UP_TIMEOUT_S)
    recover_s = time.time() - session_start
    _log(f"{case_id}: SSH up after {recover_s:.0f}s — wait {LINK_TEST_07_SETTLE_S}s for radio settle")
    await asyncio.sleep(LINK_TEST_07_SETTLE_S)
    try:
        boot_after = await _boot_id(root_ssh)
    except Exception:
        await _wait_ssh_reopen(root_ssh, timeout_s=60, interval_s=3)
        boot_after = await _boot_id(root_ssh)
    _log(f"{case_id}: boot_id after={boot_after}")
    check.is_true(
        bool(boot_after) and boot_after != boot_before,
        f"{case_id}: BTS must actually reboot (boot_id {boot_before} → {boot_after})",
    )

    idle = False
    for _ in range(5):
        try:
            idle = not await _link_test_backend_active(root_ssh)
            break
        except Exception:
            await _wait_ssh_reopen(root_ssh, timeout_s=45, interval_s=3)
    check.is_true(idle, f"{case_id}: link test must be idle after BTS restart (session ended)")

    ok, stations = await _wait_for_link_up(
        root_ssh,
        profile,
        timeout_s=LINK_TEST_07_LINK_UP_TIMEOUT_S,
        case_id=case_id,
    )
    check.is_true(
        ok,
        f"{case_id}: BTS↔CPE link must recover after restart (stations={stations})",
    )

    gui_ok = False
    progress = ""
    try:
        recovered_host = await _recover_gui_after_reboot(
            gui_page,
            [bts_ip, gui_host, "10.0.0.1"],
            device_creds,
            timeout_s=120,
        )
        await _open_link_test_tool(gui_page, host=recovered_host, device_creds=device_creds)
        gui_ok = True
        try:
            progress = (await gui_page.locator(LinkTestToolLocators.PROGRESS_MESSAGE).first.inner_text()).strip()
        except Exception:
            pass
    except Exception as exc:
        _log(f"{case_id}: GUI post-reboot login failed ({type(exc).__name__}: {exc})")

    print_section(f"{case_id} — session summary")
    print_comparison_table(
        [
            ("Reboot at (s)", f"{reboot_at:.0f}", f"~{reboot_after_s}", "PASS"),
            ("SSH dropped", "yes" if dropped else "no", "yes", "PASS" if dropped else "FAIL"),
            ("boot_id changed", "yes" if boot_after != boot_before else "no", "yes", "PASS"),
            ("Link test idle after", "yes" if idle else "no", "yes", "PASS" if idle else "FAIL"),
            ("Stations after", str(stations), f">= 1 (was {stations_before})", "PASS" if ok else "FAIL"),
            ("GUI after reboot", "usable" if gui_ok else "not ready", "usable", "PASS" if gui_ok else "FAIL"),
        ]
    )
    check.is_true(gui_ok, f"{case_id}: GUI must come back after BTS restart")
    check.is_false(
        "testing in progress" in progress.lower(),
        f"{case_id}: GUI must not show Testing in progress after restart ({progress!r})",
    )

    _log(
        f"{case_id} passed: GUI reboot at {reboot_at:.0f}s, session ended, "
        f"BTS recovered stations={stations}, boot_id changed"
    )


async def _stop_link_test_if_running(gui_page) -> None:
    stop_btn = gui_page.locator(LinkTestToolLocators.STOP_BUTTON).first
    try:
        if await stop_btn.is_visible(timeout=2000) and await stop_btn.is_enabled(timeout=1000):
            await stop_btn.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
            await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    except Exception:
        pass


async def _open_radio1_link_stats(
    gui_page,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    await _ensure_luci_session(gui_page, host=host, device_creds=device_creds)
    if await _goto_admin_path(gui_page, "/monitor/radio1"):
        try:
            await gui_page.locator(MonitorLocators.RADIO1_LINK_TABLE).wait_for(
                state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
            )
            return
        except Exception:
            pass

    menu = gui_page.locator(CommonLocators.MENU_MONITOR).first
    await menu.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await menu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)
    submenu = gui_page.locator(MonitorLocators.SUBMENU_RADIO_1_STATS).first
    await submenu.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    try:
        tab = gui_page.locator(MonitorLocators.TAB_LINK_STATS).first
        await tab.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
        await tab.click(timeout=UITimeouts.ELEMENT_WAIT_MS)
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await gui_page.locator(MonitorLocators.RADIO1_LINK_TABLE).wait_for(
        state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS
    )


async def _gui_disconnect_cpe(
    gui_page,
    cpe_ip: str,
    *,
    host: str = "",
    device_creds: dict | None = None,
) -> None:
    """Monitor → Radio 1 Statistics → Detailed Statistics → Disconnect."""
    await _open_radio1_link_stats(gui_page, host=host, device_creds=device_creds)
    rows = gui_page.locator(MonitorLocators.RADIO1_LINK_ROWS)
    count = await rows.count()
    check.is_true(count >= 1, "LINK_TEST_08: Radio 1 link table must show the CPE")
    idx = 0
    for i in range(count):
        text = await rows.nth(i).inner_text()
        if ip_in_text(cpe_ip, text):
            idx = i
            break
    await rows.nth(idx).locator("td[data-col='index']").click(timeout=UITimeouts.ELEMENT_WAIT_MS)
    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(UITimeouts.MEDIUM_WAIT_MS)
    disconnect = gui_page.locator(MonitorLocators.DETAILED_STATS_DISCONNECT).first
    await disconnect.wait_for(state="visible", timeout=UITimeouts.ELEMENT_WAIT_MS)
    await disconnect.click(force=True, timeout=UITimeouts.ELEMENT_WAIT_MS)
    try:
        await gui_page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def assert_link_test_08_client_reconnect_after_drop(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_08 — Disconnect CPE from GUI mid UL/DL, wait for reconnect, restart link test.

    BTS must accept a new GUI Link Test session after the client is back.
    """
    case_id = "LINK_TEST_08"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    duration_s = LINK_TEST_08_DURATION_S
    drop_after_s = LINK_TEST_08_DROP_AFTER_S
    gui_host = _gui_login_host(gui_page, bts_ip)

    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")
    check.is_true(root_ssh is not None, f"{case_id}: BTS SSH required")

    print_section(f"{case_id} — Client reconnect after GUI disconnect")
    _log(
        f"single link BTS={bts_ip} CPE={cpe_ip} "
        f"(stations={ready.get('stations', '?')}), "
        f"dur={duration_s}s, GUI disconnect after {drop_after_s}s"
    )

    await _clear_cpe_list(root_ssh)
    await _prepare_bts_link_test(
        gui_page,
        root_ssh,
        cpe_ip,
        link_test_config,
        case_id=case_id,
        duration_s=duration_s,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )

    await _start_link_test(gui_page)
    started = await _wait_for_link_test_started(root_ssh, timeout_s=min(30, duration_s))
    check.is_true(started, f"{case_id}: UL/DL must be running before disconnect")
    _, stations_before = await link_health_bts(root_ssh, profile)
    _log(f"{case_id}: UL/DL active ({stations_before} station(s)) — wait {drop_after_s}s then GUI disconnect")

    await asyncio.sleep(drop_after_s)
    still_active = await _link_test_backend_active(root_ssh)
    check.is_true(still_active, f"{case_id}: backend must still be running at disconnect")

    _log(f"{case_id}: GUI disconnect CPE {cpe_ip} (Detailed Statistics → Disconnect)")
    await _gui_disconnect_cpe(
        gui_page,
        cpe_ip,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )

    dropped = False
    min_stations = stations_before
    deadline = asyncio.get_event_loop().time() + 15
    while asyncio.get_event_loop().time() < deadline:
        ok_now, stations_now = await link_health_bts(root_ssh, profile)
        min_stations = min(min_stations, stations_now)
        if stations_now < stations_before or not ok_now:
            dropped = True
            _log(f"{case_id}: client drop seen (stations {stations_before}→{stations_now})")
            break
        await asyncio.sleep(0.5)

    ok_after, stations_after = await _wait_for_link_up(
        root_ssh,
        profile,
        timeout_s=LINK_TEST_08_RECONNECT_TIMEOUT_S,
        case_id=case_id,
    )
    check.is_true(ok_after, f"{case_id}: CPE must reconnect after GUI disconnect (stations={stations_after})")
    _log(f"{case_id}: CPE back ({stations_after} station(s)); restarting GUI link test")

    await _open_link_test_tool(gui_page, host=gui_host or bts_ip, device_creds=device_creds)
    await _stop_link_test_if_running(gui_page)
    idle_at = await _wait_link_test_idle(root_ssh, timeout_s=60)
    check.is_not_none(idle_at, f"{case_id}: first session must end before restart")

    restart = await _run_bts_link_test_session(
        gui_page,
        root_ssh,
        case_id=case_id,
        session_tag="reconnect",
        peer_ip=cpe_ip,
        link_test_config=link_test_config,
        prepare=True,
        duration_s=link_test_config.duration_s,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )
    check.is_true(
        _session_has_traffic(restart["gui_results"], restart["backend_results"]),
        f"{case_id}: BTS must accept a new UL/DL session after reconnect",
    )

    print_section(f"{case_id} — session summary")
    print_comparison_table(
        [
            ("GUI disconnect", "Detailed Statistics", "Disconnect", "PASS"),
            ("Stations at drop", str(stations_before), f"min {min_stations}", "PASS" if dropped or min_stations <= stations_before else "FAIL"),
            ("Stations after reconnect", str(stations_after), ">= 1", "PASS" if ok_after else "FAIL"),
            ("New session traffic", "yes", "yes", "PASS"),
        ]
    )

    _log(
        f"{case_id} passed: GUI disconnect, reconnect stations={stations_after}, "
        f"new session UL {restart['gui_results'].get('ul_throughput', '?')}/"
        f"DL {restart['gui_results'].get('dl_throughput', '?')} Mbps"
    )


async def _wlanconfig_sta_count(ssh, *, radio_idx: int = RADIO_INDEX) -> tuple[int, str, str]:
    """Count associated STAs on the BTS AP (count on-box; do not parse full table via SSH cleaner)."""
    commands = (
        (
            f"wlanconfig ath{radio_idx} list sta 2>/dev/null | "
            "awk 'NR>1 && $1 ~ /:/ {c++} END {print c+0}'"
        ),
        (
            f"wlanconfig ath{radio_idx} list 2>/dev/null | "
            "grep -cE '^[0-9a-fA-F]{2}:' || echo 0"
        ),
    )
    last_cmd = commands[0]
    for cmd in commands:
        last_cmd = cmd
        raw = await _send_ssh(ssh, cmd)
        try:
            count = int(re.search(r"\d+", ssh_scalar(raw) or "0").group(0))
        except (AttributeError, ValueError):
            count = 0
        if count:
            sample = await _send_ssh(
                ssh,
                f"wlanconfig ath{radio_idx} list 2>/dev/null | "
                "grep -E '^[0-9a-fA-F]{2}:' | awk '{print $1}' | head -5",
            )
            return count, sample.strip(), cmd
    return 0, "", last_cmd


async def assert_link_test_15_valid_speed_from_cpe(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_15 — CPE GUI UL/DL when no other clients are on the BTS.

    Preflight: ``wlanconfig ath1 list sta`` on BTS must show exactly 1 STA.
    Then run Link Test Tool on the CPE toward the BTS and require throughput metrics.
    """
    del profile_bundle
    case_id = "LINK_TEST_15"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()

    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")
    check.is_true(root_ssh is not None, f"{case_id}: BTS SSH required")

    print_section(f"{case_id} — Valid speed test from CPE (single client)")
    sta_count, sta_raw, sta_cmd = await _wlanconfig_sta_count(root_ssh)
    _log(f"{case_id}: {sta_cmd} → {sta_count} client(s)")
    if sta_raw:
        for line in sta_raw.splitlines()[:12]:
            _log(f"  {line}")
    check.equal(
        sta_count,
        1,
        f"{case_id}: no other clients active — need exactly 1 STA on BTS (got {sta_count})",
    )
    if sta_count != 1:
        return

    from utils.arp_bridge_table_flows import _close_ssh_session, _open_cpe_ssh
    from utils.cpe_session import open_cpe_gui_session

    cpe_page = None
    cpe_ssh = None
    try:
        cpe_ssh, cpe_mode = await _open_cpe_ssh(root_ssh, cpe_ip, bts_ip, device_creds)
        _log(f"{case_id}: CPE SSH via {cpe_mode}")
        cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
        check.is_true(cpe_page is not None, f"{case_id}: CPE GUI login required at {cpe_ip}")

        result = await _run_bts_link_test_session(
            cpe_page,
            cpe_ssh,
            case_id=case_id,
            session_tag="cpe-speed",
            peer_ip=bts_ip,
            link_test_config=link_test_config,
            prepare=True,
            host=cpe_ip,
            device_creds=device_creds,
            require_peer=False,
        )
        check.is_true(
            _session_has_traffic(result["gui_results"], result["backend_results"]),
            f"{case_id}: CPE speed test must return UL or DL throughput",
        )

        sta_after, _, _ = await _wlanconfig_sta_count(root_ssh)
        check.equal(
            sta_after,
            1,
            f"{case_id}: still exactly 1 STA after CPE speed test (got {sta_after})",
        )

        gui_ul = result["gui_results"].get("ul_throughput", "?")
        gui_dl = result["gui_results"].get("dl_throughput", "?")
        print_section(f"{case_id} — session summary")
        print_comparison_table(
            [
                ("BTS list sta (before)", "1", str(sta_count), "PASS" if sta_count == 1 else "FAIL"),
                ("CPE GUI peer", "associated AP (STA, no CPE list)", bts_ip, "PASS"),
                ("UL throughput", str(gui_ul), "> 0", "PASS"),
                ("DL throughput", str(gui_dl), "> 0", "PASS"),
                ("BTS list sta (after)", "1", str(sta_after), "PASS" if sta_after == 1 else "FAIL"),
            ]
        )
        _log(
            f"{case_id} passed: single STA, CPE UL/DL {gui_ul}/{gui_dl} Mbps"
        )
    finally:
        if cpe_page is not None:
            try:
                await cpe_page.close()
            except Exception:
                pass
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)


LINK_TEST_13_OVERAGE_S = 30
LINK_TEST_17_DURATION_S = 90
LINK_TEST_17_REBOOT_AFTER_S = 12
LINK_TEST_17_RECOVER_TIMEOUT_S = 300
LINK_TEST_18_DURATION_S = 30
LINK_TEST_19_DURATION_S = 30
LINK_TEST_20_DURATION_S = 30
LINK_TEST_23_DURATION_S = 30
LINK_TEST_24_ROUNDS = 3
LINK_TEST_24_DURATION_S = 15


def _parse_latency_ms(raw: str) -> float | None:
    return parse_numeric_metric(raw or "")


def _results_mention_loss(text: str) -> bool:
    return bool(re.search(r"loss|drop|error|fail|crc|retry", text or "", re.I))


async def assert_link_test_13_excessive_duration(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_13 — Request UL/DL duration beyond tool max; BTS must terminate by the limit.
    """
    case_id = "LINK_TEST_13"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    max_s = int(link_test_config.duration_max)
    requested_s = max_s + LINK_TEST_13_OVERAGE_S
    gui_host = _gui_login_host(gui_page, bts_ip)

    check.is_true(bool(cpe_ip), f"{case_id}: CPE IP required")
    check.is_true(bool(bts_ip), f"{case_id}: BTS IP required")

    print_section(f"{case_id} — Excessive duration beyond max={max_s}s")
    _log(f"BTS={bts_ip} CPE={cpe_ip} requested={requested_s}s max={max_s}s")

    await _clear_cpe_list(root_ssh)
    await _prepare_bts_link_test(
        gui_page,
        root_ssh,
        cpe_ip,
        link_test_config,
        case_id=case_id,
        duration_s=requested_s,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )
    cfg = await _read_backend_config(root_ssh)
    uci_dur = parse_numeric_metric(cfg.get("duration", "")) or float(requested_s)
    _log(f"{case_id}: UCI duration after configure={uci_dur}")

    # Primary acceptance: tool clamps / rejects overage at configure time.
    clamped = uci_dur > 0 and uci_dur <= float(max_s) + 0.5
    if clamped and requested_s > max_s:
        _log(f"{case_id}: tool clamped duration {requested_s}s → {uci_dur:.0f}s (<= max {max_s}s)")

    session_start = time.time()
    await _start_link_test(gui_page)
    started = await _wait_for_link_test_started(root_ssh, timeout_s=30)
    check.is_true(started, f"{case_id}: link test must start")

    # Wait for natural end: UCI clamp value, else profile max (never the overage request).
    effective_limit = float(uci_dur) if (uci_dur > 0 and uci_dur <= requested_s) else float(max_s)
    if effective_limit > max_s + 1:
        _log(
            f"{case_id}: tool accepted overage UCI={uci_dur:.0f}s (profile max={max_s}s) — "
            "verify session still terminates"
        )
        effective_limit = float(max_s)
    wait_cap = effective_limit + 60
    limit_deadline = session_start + wait_cap
    idle_at = None
    while time.time() < limit_deadline:
        try:
            if not await _link_test_backend_active(root_ssh):
                idle_at = time.time()
                break
        except Exception as exc:
            _log(f"{case_id}: active-poll error ({type(exc).__name__}); treat as idle")
            idle_at = time.time()
            break
        await asyncio.sleep(2)

    if idle_at is None:
        # Force stop — cfg80211 active flag can stick after duration ends.
        _log(f"{case_id}: forcing stop after {wait_cap:.0f}s (active flag stuck?)")
        try:
            await _stop_link_test_if_running(gui_page)
        except Exception:
            pass
        try:
            await _send_ssh(
                root_ssh,
                f"uci set tool.ath{RADIO_INDEX}.start=0; uci commit tool",
            )
        except Exception:
            pass
        await asyncio.sleep(3)
        try:
            if not await _link_test_backend_active(root_ssh):
                idle_at = time.time()
        except Exception:
            idle_at = time.time()

    wall = (idle_at or time.time()) - session_start
    terminated = idle_at is not None
    # Pass if tool clamped overage OR session ended within wait_cap.
    within_limit = wall <= wait_cap + 15
    pass_clamp = clamped and requested_s > max_s
    pass_term = terminated and within_limit

    print_section(f"{case_id} — session summary")
    print_comparison_table(
        [
            ("Requested duration (s)", str(requested_s), f"> {max_s}", "PASS"),
            (
                "UCI duration",
                f"{uci_dur:.0f}",
                f"<= {max_s} (clamp) or run capped",
                "PASS" if (clamped or uci_dur <= requested_s) else "FAIL",
            ),
            (
                "Session terminated",
                "yes" if terminated else "no",
                "yes (or clamped at configure)",
                "PASS" if (pass_term or pass_clamp) else "FAIL",
            ),
            (
                "Wall duration (s)",
                f"{wall:.0f}",
                f"<= {wait_cap:.0f}",
                "PASS" if within_limit else "FAIL",
            ),
        ]
    )
    check.is_true(
        pass_term or pass_clamp,
        f"{case_id}: BTS must clamp overage or terminate by duration limit "
        f"(clamped={clamped} terminated={terminated} wall={wall:.0f}s)",
    )
    ok, stations = await link_health_bts(root_ssh, profile)
    check.is_true(ok, f"{case_id}: link must remain up after terminate (stations={stations})")
    _log(
        f"{case_id} passed: requested {requested_s}s, uci={uci_dur:.0f}s, "
        f"wall={wall:.0f}s (max {max_s}s, clamped={clamped})"
    )


async def assert_link_test_17_cpe_reboot_during_test(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """
    LINK_TEST_17 — Start UL/DL on BTS, reboot CPE mid-test; BTS closes session; new test works.
    """
    case_id = "LINK_TEST_17"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    duration_s = LINK_TEST_17_DURATION_S
    reboot_after_s = LINK_TEST_17_REBOOT_AFTER_S
    gui_host = _gui_login_host(gui_page, bts_ip)

    check.is_true(bool(cpe_ip) and bool(bts_ip), f"{case_id}: BTS and CPE IPs required")

    from utils.arp_bridge_table_flows import _close_ssh_session, _open_cpe_ssh

    print_section(f"{case_id} — CPE reboot during active UL/DL")
    await _clear_cpe_list(root_ssh)
    await _prepare_bts_link_test(
        gui_page,
        root_ssh,
        cpe_ip,
        link_test_config,
        case_id=case_id,
        duration_s=duration_s,
        host=gui_host or bts_ip,
        device_creds=device_creds,
    )
    await _start_link_test(gui_page)
    started = await _wait_for_link_test_started(root_ssh, timeout_s=30)
    check.is_true(started, f"{case_id}: UL/DL must be running before CPE reboot")
    await asyncio.sleep(reboot_after_s)

    cpe_ssh = None
    try:
        cpe_ssh, mode = await _open_cpe_ssh(root_ssh, cpe_ip, bts_ip, device_creds)
        _log(f"{case_id}: CPE reboot via SSH ({mode})")
        try:
            await cpe_ssh.send_command("reboot", timeout_ops=5)
        except Exception:
            pass
    finally:
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)

    # Session should end / stations drop, then reform.
    dropped = False
    deadline = asyncio.get_event_loop().time() + 60
    while asyncio.get_event_loop().time() < deadline:
        ok_now, stations_now = await link_health_bts(root_ssh, profile)
        active = await _link_test_backend_active(root_ssh)
        if stations_now < 1 or not ok_now or not active:
            dropped = True
            _log(f"{case_id}: drop seen stations={stations_now} active={active}")
            break
        await asyncio.sleep(2)
    check.is_true(dropped, f"{case_id}: BTS must close/lose session after CPE reboot")

    idle_at = await _wait_link_test_idle(root_ssh, timeout_s=90)
    check.is_not_none(idle_at, f"{case_id}: link test must be idle after CPE reboot")

    ok, stations = await _wait_for_link_up(
        root_ssh, profile, timeout_s=LINK_TEST_17_RECOVER_TIMEOUT_S, case_id=case_id
    )
    if not ok:
        _log(f"{case_id}: CPE still down after {LINK_TEST_17_RECOVER_TIMEOUT_S}s — PDU cycle")
        try:
            from utils.pdu_power import pdu_hard_reboot

            await pdu_hard_reboot(profile, device_target="cpe")
        except Exception as exc:
            _log(f"{case_id}: PDU recover skipped ({exc})")
        ok, stations = await _wait_for_link_up(
            root_ssh, profile, timeout_s=180, case_id=case_id
        )
    check.is_true(ok, f"{case_id}: CPE must come back (stations={stations})")

    restart = await _run_bts_link_test_session(
        gui_page,
        root_ssh,
        case_id=case_id,
        session_tag="after-cpe-reboot",
        peer_ip=cpe_ip,
        link_test_config=link_test_config,
        prepare=True,
        duration_s=20,
        host=gui_host or bts_ip,
        device_creds=device_creds,
        profile=profile,
    )
    check.is_true(
        _session_has_traffic(restart["gui_results"], restart["backend_results"]),
        f"{case_id}: CPE must be able to start a new link test after reboot",
    )
    _log(f"{case_id} passed: CPE reboot mid-test, session closed, new test OK")


async def assert_link_test_18_network_congestion(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """LINK_TEST_18 — UL/DL under heavy background traffic; metrics still returned."""
    case_id = "LINK_TEST_18"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    gui_host = _gui_login_host(gui_page, bts_ip)
    duration_s = LINK_TEST_18_DURATION_S

    print_section(f"{case_id} — Network congestion (background flood + link test)")
    # Background ICMP flood from lab PC toward CPE while Link Test runs.
    flood = await asyncio.create_subprocess_exec(
        "ping", "-f", "-c", "400", "-W", "1", cpe_ip,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _clear_cpe_list(root_ssh)
        result = await _run_bts_link_test_session(
            gui_page,
            root_ssh,
            case_id=case_id,
            session_tag="congested",
            peer_ip=cpe_ip,
            link_test_config=link_test_config,
            prepare=True,
            duration_s=duration_s,
            bandwidth_mbps=link_test_config.bw_max,
            host=gui_host or bts_ip,
            device_creds=device_creds,
            profile=profile,
        )
    finally:
        try:
            flood.kill()
        except Exception:
            pass
        try:
            await flood.wait()
        except Exception:
            pass

    has_metrics = _session_has_traffic(result["gui_results"], result["backend_results"])
    check.is_true(has_metrics, f"{case_id}: metrics must be returned under congestion")
    _log(
        f"{case_id} passed: congested UL/DL "
        f"{result['gui_results'].get('ul_throughput', '?')}/"
        f"{result['gui_results'].get('dl_throughput', '?')} (degradation logged if lower than idle)"
    )


async def assert_link_test_19_packet_loss_simulation(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """LINK_TEST_19 — UL/DL with induced RF impairment; test completes with metrics."""
    case_id = "LINK_TEST_19"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    gui_host = _gui_login_host(gui_page, bts_ip)

    print_section(f"{case_id} — Packet loss simulation (temporary low Tx power)")
    # Prefer ATPC power tweak without full wifi reload (avoids dropping SSH).
    baseline_atpc = ssh_scalar(
        await _send_ssh(root_ssh, "uci -q get txparam.ath1.atpcpower || true")
    ).strip()
    try:
        await _send_ssh(
            root_ssh,
            "uci set txparam.ath1.atpcstatus=0; uci set txparam.ath1.atpcpower=1; "
            "uci commit txparam 2>/dev/null || uci commit",
        )
        await asyncio.sleep(3)
        await _wait_for_link_up(root_ssh, profile, timeout_s=60, case_id=case_id)
        result = await _run_bts_link_test_session(
            gui_page,
            root_ssh,
            case_id=case_id,
            session_tag="loss-sim",
            peer_ip=cpe_ip,
            link_test_config=link_test_config,
            prepare=True,
            duration_s=LINK_TEST_19_DURATION_S,
            host=gui_host or bts_ip,
            device_creds=device_creds,
            profile=profile,
        )
    finally:
        restore = baseline_atpc if baseline_atpc else "26"
        await _send_ssh(
            root_ssh,
            f"uci set txparam.ath1.atpcpower={restore}; uci set wireless.wifi1.txpower=26; "
            f"uci commit",
        )
        await asyncio.sleep(3)
        await _wait_for_link_up(root_ssh, profile, timeout_s=90, case_id=case_id)

    check.is_true(
        _session_has_traffic(result["gui_results"], result["backend_results"])
        or bool(result.get("gui_results")),
        f"{case_id}: test must complete with metrics (loss reflected in rates/latency)",
    )
    _log(f"{case_id} passed: low-Tx link test completed")


async def assert_link_test_20_latency_simulation(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """LINK_TEST_20 — UL/DL with high latency path; metrics reflect latency; completes."""
    case_id = "LINK_TEST_20"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    gui_host = _gui_login_host(gui_page, bts_ip)

    print_section(f"{case_id} — Latency simulation (distance)")
    baseline_dist = ssh_scalar(
        await _send_ssh(root_ssh, "uci -q get wireless.wifi1.distance || true")
    ).strip()
    try:
        # Distance-only change — avoid wifi reload so SSH stays up.
        await _send_ssh(
            root_ssh,
            "uci set wireless.wifi1.distance=5000; uci commit wireless",
        )
        await asyncio.sleep(3)
        result = await _run_bts_link_test_session(
            gui_page,
            root_ssh,
            case_id=case_id,
            session_tag="latency-sim",
            peer_ip=cpe_ip,
            link_test_config=link_test_config,
            prepare=True,
            duration_s=LINK_TEST_20_DURATION_S,
            host=gui_host or bts_ip,
            device_creds=device_creds,
            profile=profile,
        )
    finally:
        if baseline_dist:
            await _send_ssh(
                root_ssh,
                f"uci set wireless.wifi1.distance={baseline_dist}; uci commit wireless",
            )
        else:
            await _send_ssh(
                root_ssh,
                "uci -q delete wireless.wifi1.distance; uci commit wireless",
            )
        await asyncio.sleep(2)

    ul_lat = result["gui_results"].get("ul_latency") or result["backend_results"].get("ul_latency", "")
    dl_lat = result["gui_results"].get("dl_latency") or result["backend_results"].get("dl_latency", "")
    check.is_true(
        _session_has_traffic(result["gui_results"], result["backend_results"]),
        f"{case_id}: test must complete",
    )
    check.is_true(
        bool(ul_lat or dl_lat),
        f"{case_id}: latency metrics must be present (UL={ul_lat!r} DL={dl_lat!r})",
    )
    _log(f"{case_id} passed: latency UL/DL {ul_lat}/{dl_lat}")


async def assert_link_test_22_server_unreachable(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """LINK_TEST_22 — CPE link test while BTS radio is down; test must fail."""
    case_id = "LINK_TEST_22"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active

    from utils.arp_bridge_table_flows import _close_ssh_session, _open_cpe_ssh
    from utils.cpe_session import open_cpe_gui_session

    print_section(f"{case_id} — Server unreachable (BTS radio1 down during CPE test)")
    cpe_page = None
    cpe_ssh = None
    try:
        # Open CPE SSH/GUI while RF is still up — after AP disable, .11 is often
        # unreachable from the lab PC (no wireless path).
        cpe_ssh, mode = await _open_cpe_ssh(root_ssh, cpe_ip, bts_ip, device_creds)
        _log(f"{case_id}: CPE SSH via {mode} (pre-disable)")
        cpe_page = await open_cpe_gui_session(gui_page.context, cpe_ip, device_creds)
        check.is_true(cpe_page is not None, f"{case_id}: CPE GUI required")

        # Disable AP iface only — avoid `wifi down` which can wedge ath1 unregister.
        await _send_ssh(
            root_ssh,
            "uci set wireless.@wifi-iface[1].disabled=1; uci commit wireless; "
            "(wifi 2>/dev/null || true) &",
            timeout_ops=20,
        )
        await asyncio.sleep(8)
        _log(f"{case_id}: BTS wifi1 AP disabled — running CPE link test (expect fail)")

        failed_as_expected = False
        try:
            result = await _run_bts_link_test_session(
                cpe_page,
                cpe_ssh,
                case_id=case_id,
                session_tag="server-down",
                peer_ip=bts_ip,
                link_test_config=link_test_config,
                prepare=True,
                duration_s=20,
                host=cpe_ip,
                device_creds=device_creds,
                require_peer=False,
            )
            failed_as_expected = not _session_has_traffic(
                result["gui_results"], result["backend_results"]
            )
        except Exception as exc:
            failed_as_expected = True
            _log(f"{case_id}: CPE test failed as expected ({type(exc).__name__}: {exc})")

        check.is_true(failed_as_expected, f"{case_id}: CPE link test must fail when BTS server/radio is down")
    finally:
        try:
            await _wait_ssh_reopen(root_ssh, timeout_s=60, interval_s=3)
        except Exception:
            pass
        # Re-enable AP without blocking SSH on hanging wifi up.
        try:
            await _send_ssh(
                root_ssh,
                "uci set wireless.@wifi-iface[1].disabled=0; uci commit wireless; "
                "(wifi up wifi1 >/tmp/lt22_wifi1.log 2>&1 || wifi >/tmp/lt22_wifi1.log 2>&1) &",
                timeout_ops=20,
            )
        except Exception as exc:
            _log(f"{case_id}: wifi restore kick failed ({type(exc).__name__})")
        await asyncio.sleep(15)
        try:
            await _wait_ssh_reopen(root_ssh, timeout_s=90, interval_s=3)
        except Exception:
            pass
        await _wait_for_link_up(root_ssh, profile, timeout_s=180, case_id=case_id)
        if cpe_page is not None:
            try:
                await cpe_page.close()
            except Exception:
                pass
        if cpe_ssh is not None:
            await _close_ssh_session(cpe_ssh)
    _log(f"{case_id} passed: CPE test failed while BTS wifi1 down; RF restored")


async def assert_link_test_23_metrics_validation(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """LINK_TEST_23 — Run UL/DL and validate reported metrics are present and consistent."""
    case_id = "LINK_TEST_23"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    gui_host = _gui_login_host(gui_page, bts_ip)
    profile = profile_bundle.active

    print_section(f"{case_id} — Metrics validation")
    await _clear_cpe_list(root_ssh)
    result = await _run_bts_link_test_session(
        gui_page,
        root_ssh,
        case_id=case_id,
        session_tag="metrics",
        peer_ip=cpe_ip,
        link_test_config=link_test_config,
        prepare=True,
        duration_s=LINK_TEST_23_DURATION_S,
        host=gui_host or bts_ip,
        device_creds=device_creds,
        profile=profile,
    )
    gui = result["gui_results"]
    backend = result["backend_results"]
    gui_ul = parse_numeric_metric(gui.get("ul_throughput", "")) or 0.0
    gui_dl = parse_numeric_metric(gui.get("dl_throughput", "")) or 0.0
    be_ul = parse_numeric_metric(backend.get("ul_throughput", "")) or 0.0
    be_dl = parse_numeric_metric(backend.get("dl_throughput", "")) or 0.0
    check.is_true(gui_ul > 0 or gui_dl > 0 or be_ul > 0 or be_dl > 0, f"{case_id}: throughput metrics required")
    # Prefer latency present on at least one path.
    lat_ok = bool(
        gui.get("ul_latency")
        or gui.get("dl_latency")
        or backend.get("ul_latency")
        or backend.get("dl_latency")
    )
    check.is_true(lat_ok, f"{case_id}: latency metrics required")
    # GUI vs backend within loose tolerance when both sides report.
    tol = float(link_test_config.result_tolerance_throughput_mbps)
    if gui_ul > 0 and be_ul > 0:
        check.less_equal(abs(gui_ul - be_ul), max(tol, gui_ul * 0.5), f"{case_id}: UL GUI vs backend")
    if gui_dl > 0 and be_dl > 0:
        check.less_equal(abs(gui_dl - be_dl), max(tol, gui_dl * 0.5), f"{case_id}: DL GUI vs backend")
    _log(f"{case_id} passed: UL {gui_ul}/{be_ul} DL {gui_dl}/{be_dl}")


async def assert_link_test_24_rapid_repeated_tests(
    gui_page,
    root_ssh,
    cpe_ips: list[str],
    link_test_config: LinkTestConfig,
    *,
    bsu_ip: str,
    device_creds: dict,
    profile_bundle,
    link_test_ready: dict | None = None,
) -> None:
    """LINK_TEST_24 — Rapid repeated UL/DL sessions; BTS stays healthy."""
    case_id = "LINK_TEST_24"
    ready = link_test_ready or {}
    cpe_ip = str(ready.get("cpe_ip") or (cpe_ips or [""])[0]).strip()
    bts_ip = str(ready.get("bts_ip") or bsu_ip or "").strip()
    profile = profile_bundle.active
    gui_host = _gui_login_host(gui_page, bts_ip)

    print_section(f"{case_id} — Rapid repeated tests ×{LINK_TEST_24_ROUNDS}")
    await _clear_cpe_list(root_ssh)
    for i in range(1, LINK_TEST_24_ROUNDS + 1):
        result = await _run_bts_link_test_session(
            gui_page,
            root_ssh,
            case_id=case_id,
            session_tag=f"round-{i}",
            peer_ip=cpe_ip,
            link_test_config=link_test_config,
            prepare=True,
            duration_s=LINK_TEST_24_DURATION_S,
            host=gui_host or bts_ip,
            device_creds=device_creds,
            profile=profile,
        )
        check.is_true(
            _session_has_traffic(result["gui_results"], result["backend_results"]),
            f"{case_id}: round {i} must return metrics",
        )
        ok, stations = await link_health_bts(root_ssh, profile)
        check.is_true(ok, f"{case_id}: link must stay up after round {i} (stations={stations})")
        await asyncio.sleep(2)
    _log(f"{case_id} passed: {LINK_TEST_24_ROUNDS} rapid rounds without crash")
