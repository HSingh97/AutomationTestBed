from __future__ import annotations

import asyncio
import os
import socket
from typing import Any

import pytest
from playwright.async_api import async_playwright
from scrapli.driver.generic import AsyncGenericDriver

from config.defaults import RADIO_TEST_VALUES, TRAFFIC_DEFAULTS
from pages.commands import RootCommands
from pages.locators import RadioPropertiesLocators, TopPanelLocators, UITimeouts
from pages.radio_properties_page import RadioPropertiesPage
from traffic.trex_runner import run_trex_stats_check
from utils.gui_login import login_if_needed
from utils.parsers import (
    extract_uci_value,
    parse_bandwidth,
    parse_encryption,
    parse_iwconfig_active_channel,
    parse_radio_mode,
    parse_radio_status,
)
from utils.recovery_manager import get_active_recovery_manager
from utils.ui_helpers import (
    execute_triple_apply,
    validate_dropdown_lifecycle,
    validate_dropdown_value_lifecycle,
    validate_input_lifecycle,
)
from utils.validators import validate_param

DL_UL_RATIO_OPTIONS = ["Auto", "50/50", "60/40", "70/30", "75/25", "80/20"]
TREX_DEFAULTS = TRAFFIC_DEFAULTS["trex"]


def _log(role: str, message: str):
    print(f"[RADIO][{role}] {message}")


def _remote_dut_host_from_profile() -> str | None:
    manager = get_active_recovery_manager()
    if not manager:
        return None
    dut = manager.profile_bundle.active.get("dut", {})
    ipv6_targets = dut.get("remote_ipv6s", [])
    if ipv6_targets:
        return str(ipv6_targets[0]).strip()
    ipv4_targets = dut.get("remote_ips", [])
    if ipv4_targets:
        return str(ipv4_targets[0]).strip()
    return None


async def _open_temp_root_ssh(host: str, password: str):
    conn = AsyncGenericDriver(
        host=host,
        auth_username="root",
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await conn.open()
    return conn


async def _send_command_with_retry(root_ssh, command: str, *, attempts: int = 2):
    last_exc = None
    for attempt in range(attempts):
        try:
            return await root_ssh.send_command(command)
        except Exception as exc:
            last_exc = exc
            if attempt == attempts - 1:
                raise
            try:
                await root_ssh.close()
            except Exception:
                pass
            await asyncio.sleep(2)
            await root_ssh.open()
    raise last_exc if last_exc else RuntimeError(f"Unable to run command: {command}")


async def _get_backend_value(root_ssh, command: str) -> str:
    response = await _send_command_with_retry(root_ssh, command)
    return extract_uci_value(response.result or "")


async def _open_remote_radio_target(host: str, device_creds: dict[str, str]):
    delays = (0, 5, 10)
    last_exc = None
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        remote_playwright = None
        remote_browser = None
        remote_context = None
        try:
            remote_playwright = await async_playwright().start()
            remote_browser = await remote_playwright.chromium.launch(headless=True)
            remote_context = await remote_browser.new_context(ignore_https_errors=True)
            remote_page = await remote_context.new_page()
            await login_if_needed(
                remote_page,
                host,
                device_creds,
                wait_ms=UITimeouts.LONG_WAIT_MS,
                skip_recovery=True,
            )
            remote_ssh = await _open_temp_root_ssh(host, device_creds["pass"])
            return remote_page, remote_ssh, remote_context, remote_browser, remote_playwright
        except Exception as exc:
            last_exc = exc
            try:
                if "remote_page" in locals():
                    await remote_page.close()
            finally:
                if remote_context is not None:
                    await remote_context.close()
                if remote_browser is not None:
                    await remote_browser.close()
                if remote_playwright is not None:
                    await remote_playwright.stop()
    raise last_exc if last_exc else RuntimeError(f"Unable to open remote radio target {host}.")


async def _open_radio_targets(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    await login_if_needed(gui_page, bsu_ip, device_creds, wait_ms=UITimeouts.MEDIUM_WAIT_MS)
    bts_ssh = await _open_temp_root_ssh(bsu_ip, device_creds["pass"])
    targets = [
        {
            "role": "BTS",
            "host": bsu_ip,
            "page": gui_page,
            "ssh": bts_ssh,
            "radio_page": RadioPropertiesPage(gui_page, local_ip=bsu_ip),
            "owns_resources": True,
        }
    ]

    remote_host = _remote_dut_host_from_profile()
    if remote_host:
        remote_page, remote_ssh, remote_context, remote_browser, remote_playwright = await _open_remote_radio_target(
            remote_host, device_creds
        )
        targets.append(
            {
                "role": "CPE",
                "host": remote_host,
                "page": remote_page,
                "ssh": remote_ssh,
                "radio_page": RadioPropertiesPage(remote_page, local_ip=remote_host),
                "context": remote_context,
                "browser": remote_browser,
                "playwright": remote_playwright,
                "owns_resources": True,
            }
        )
    return targets


async def _close_radio_targets(targets: list[dict[str, Any]]):
    for target in targets:
        if not target.get("owns_resources"):
            continue
        try:
            await target["ssh"].close()
        finally:
            if target.get("context") is None:
                continue
            try:
                await target["page"].close()
            finally:
                try:
                    await target["context"].close()
                finally:
                    try:
                        if target.get("browser") is not None:
                            await target["browser"].close()
                    finally:
                        if target.get("playwright") is not None:
                            await target["playwright"].stop()


async def _dropdown_options(gui_page, locator: str) -> list[dict[str, str]]:
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    return await element.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )


def _resolve_dropdown_option(options: list[dict[str, str]], token: str) -> dict[str, str] | None:
    token = str(token)
    for option in options:
        if option["text"] == token or option["value"] == token:
            return option
    return None


async def _selected_dropdown_text(gui_page, locator: str) -> str:
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    return await element.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )


async def _set_dropdown_value(gui_page, locator: str, option_value: str):
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    if await element.is_visible():
        await element.select_option(value=option_value)
    else:
        await element.evaluate(
            """
            (el, value) => {
                el.value = value;
                Array.from(el.options).forEach(opt => {
                    opt.selected = opt.value === value;
                });
                el.dispatchEvent(new Event("change", { bubbles: true }));
                el.dispatchEvent(new Event("input", { bubbles: true }));
            }
            """,
            option_value,
        )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def _apply_dropdown_for_target(target, locator: str, command: str, param_name: str, option_token: str, fallback_url: str):
    options = await _dropdown_options(target["page"], locator)
    option = _resolve_dropdown_option(options, option_token)
    assert option, f"Unable to resolve option '{option_token}' for {param_name}."

    await _set_dropdown_value(target["page"], locator, option["value"])
    await execute_triple_apply(target["page"], fallback_url)

    backend_value = await _get_backend_value(target["ssh"], command)
    validate_param(f"{target['role']} Backend {param_name}", option["value"], backend_value)

    gui_text = await _selected_dropdown_text(target["page"], locator)
    validate_param(f"{target['role']} Frontend {param_name}", option["text"], gui_text)
    return option


async def _apply_backend_setting(target, key: str, value: str, verify_command: str, label: str):
    await _send_command_with_retry(target["ssh"], f"ucidyn set {key} {value}")
    await _send_command_with_retry(target["ssh"], "ucidyn apply")
    await asyncio.sleep(8)
    backend_value = await _get_backend_value(target["ssh"], verify_command)
    validate_param(f"{target['role']} {label}", str(value), backend_value)


def _to_floatish(value: object) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return 0.0


def _trex_ssh_port_reachable(host: str, port: int = 22, timeout: float = 3.0) -> bool:
    """Best-effort reachability before spawning TRex (avoids long hangs when lab TRex is down)."""
    h = (host or "").strip().strip("[]")
    if not h:
        return False
    try:
        with socket.create_connection((h, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _run_trex_behavior_check(bsu_ip: str, device_creds: dict[str, str], label: str):
    flag = os.environ.get("UBR_SKIP_RADIO_TREX", "").strip().lower()
    if flag in ("1", "true", "yes"):
        pytest.skip("TRex-backed radio behavior checks skipped (UBR_SKIP_RADIO_TREX).")

    trex_host = str(TREX_DEFAULTS.get("host") or "").strip()
    if not _trex_ssh_port_reachable(trex_host):
        pytest.skip(
            f"TRex host {trex_host!r} not reachable on SSH port 22 within timeout; "
            "fix lab routing or set UBR_SKIP_RADIO_TREX=1 to skip when TRex is intentionally offline."
        )

    _log("TREX", f"Running lightweight throughput check for {label}.")
    result = await asyncio.to_thread(
        run_trex_stats_check,
        trex_server=TREX_DEFAULTS["host"],
        trex_user=TREX_DEFAULTS["user"],
        trex_password=TREX_DEFAULTS["password"],
        trex_dir=TREX_DEFAULTS["directory"],
        trex_pythonpath=TREX_DEFAULTS["pythonpath"],
        trex_client_script=TREX_DEFAULTS["client_script"],
        trex_ports=TREX_DEFAULTS["ports"],
        trex_server_cores=TREX_DEFAULTS["server_cores"],
        duration_s=int(RADIO_TEST_VALUES["TREX_BEHAVIOR_DURATION_S"]),
        expected_min_mbps=0.0,
        trex_su_count=1,
        trex_dl_bw=RADIO_TEST_VALUES["TREX_BEHAVIOR_DL_BW"],
        trex_ul_bw=RADIO_TEST_VALUES["TREX_BEHAVIOR_UL_BW"],
        run_mode="counter_check",
        dut_host=bsu_ip,
        dut_user="root",
        dut_password=device_creds["pass"],
        dut_radio_idx=1,
        dut_sample_interval_s=2,
        reuse_existing_server=True,
    )
    validation = result.get("validation") or {}
    assert validation.get("passed"), f"{label} behavior validation failed: {validation.get('reason', 'unknown error')}"

    post = (result.get("dut_counters") or {}).get("post") or {}
    observed = any(_to_floatish(post.get(key)) > 0 for key in ("tx_tput_mbps", "rx_tput_mbps"))
    assert observed, f"{label} behavior validation did not observe DUT throughput counters."
    return result


async def assert_radio_status_lifecycle(radio_page, root_ssh):
    await radio_page.navigate()
    await validate_dropdown_lifecycle(
        radio_page.page,
        root_ssh,
        locator=RadioPropertiesLocators.STATUS_DROPDOWN,
        expected_options=["Enable", "Disable"],
        uci_cmd=RootCommands.get_radio_status(1),
        param_name="Status",
        fallback_url=radio_page.RADIO_1_URL_CHUNK,
        parser=parse_radio_status,
        test_all_options=True,
    )


async def assert_ssid_lifecycle(radio_page, root_ssh):
    await radio_page.navigate()
    await validate_input_lifecycle(
        radio_page.page,
        root_ssh,
        locator=RadioPropertiesLocators.SSID_INPUT,
        valid_val=RADIO_TEST_VALUES["SSID_VALID"],
        invalid_val=RADIO_TEST_VALUES["SSID_INVALID"],
        uci_cmd=RootCommands.get_ssid(1),
        param_name="SSID",
        fallback_url=radio_page.RADIO_1_URL_CHUNK,
        parser=extract_uci_value,
    )


async def assert_bandwidth_lifecycle(radio_page, root_ssh):
    await radio_page.navigate()
    await validate_dropdown_lifecycle(
        radio_page.page,
        root_ssh,
        locator=RadioPropertiesLocators.BANDWIDTH_DROPDOWN,
        expected_options=["20 MHz", "40 MHz", "80 MHz", "160 MHz"],
        uci_cmd=RootCommands.get_bandwidth(1),
        param_name="Bandwidth",
        fallback_url=radio_page.RADIO_1_URL_CHUNK,
        parser=parse_bandwidth,
        test_all_options=True,
    )


async def assert_encryption_lifecycle(radio_page, root_ssh):
    await radio_page.navigate()
    await validate_dropdown_lifecycle(
        radio_page.page,
        root_ssh,
        locator=RadioPropertiesLocators.ENCRYPTION_DROPDOWN,
        expected_options=["AES-256", "None"],
        uci_cmd=RootCommands.get_security(1),
        param_name="Encryption",
        fallback_url=radio_page.RADIO_1_URL_CHUNK,
        parser=parse_encryption,
        test_all_options=True,
    )


async def assert_channel_consistency(radio_page, root_ssh):
    await radio_page.navigate()
    page = radio_page.page
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)

    channel_dropdown = page.locator(RadioPropertiesLocators.CONFIGURED_CHANNEL_DROPDOWN).first
    active_channel_display = page.locator(RadioPropertiesLocators.ACTIVE_CHANNEL_DISPLAY).first

    raw_mode_resp = await root_ssh.send_command(RootCommands.get_radio_mode(1))
    current_mode = parse_radio_mode(raw_mode_resp.result, 1)

    if current_mode == "BTS":
        for channel in RADIO_TEST_VALUES["CHANNEL_BTS_VALUES"]:
            await channel_dropdown.select_option(value=channel, timeout=5000)
            await execute_triple_apply(page, radio_page.RADIO_1_URL_CHUNK)

            for _ in range(15):
                cli_active_resp = await root_ssh.send_command(RootCommands.get_active_channel(1))
                if "No such device" not in cli_active_resp.result and (
                    "Channel" in cli_active_resp.result or "Frequency" in cli_active_resp.result
                ):
                    break
                await page.wait_for_timeout(3000)

            if "radio1" not in page.url:
                await radio_page.navigate()

            await page.wait_for_timeout(4000)

            channel_dropdown = page.locator(RadioPropertiesLocators.CONFIGURED_CHANNEL_DROPDOWN).first
            active_channel_display = page.locator(RadioPropertiesLocators.ACTIVE_CHANNEL_DISPLAY).first

            gui_config_val = await channel_dropdown.evaluate("el => el.options[el.selectedIndex].text")
            gui_active_val = await active_channel_display.inner_text()

            cli_config_resp = await root_ssh.send_command(RootCommands.get_configured_channel(1))
            cli_active_resp = await root_ssh.send_command(RootCommands.get_active_channel(1))

            cli_config_val = extract_uci_value(cli_config_resp.result)
            cli_active_val = parse_iwconfig_active_channel(cli_active_resp.result)

            assert channel in gui_config_val, f"GUI Config mismatch. Expected {channel}, got {gui_config_val}"
            assert channel in gui_active_val, f"GUI Active mismatch. Expected {channel}, got {gui_active_val}"
            assert channel in cli_config_val, f"CLI Config mismatch. Expected {channel}, got {cli_config_val}"
            assert channel in cli_active_val, f"CLI Active mismatch. Expected {channel}, got {cli_active_val}"

    elif current_mode == "CPE":
        gui_active_val = await active_channel_display.inner_text()
        cli_active_resp = await root_ssh.send_command(RootCommands.get_active_channel(1))
        cli_active_val = parse_iwconfig_active_channel(cli_active_resp.result)
        assert cli_active_val in gui_active_val or gui_active_val in cli_active_val, (
            "GUI Active Channel does not match CLI Active Channel in CPE mode."
        )
    else:
        pytest.fail(f"Unknown radio mode: {current_mode}")


async def assert_max_cpe_lifecycle(radio_page, root_ssh):
    await radio_page.navigate()
    page = radio_page.page

    cpe_locator = page.locator(RadioPropertiesLocators.MAXIMUM_SU_INPUT).first
    if await cpe_locator.count() == 0:
        pytest.fail(f"Could not find element {RadioPropertiesLocators.MAXIMUM_SU_INPUT} in the DOM.")

    invalid_val = RADIO_TEST_VALUES["MAX_CPE_INVALID"]
    await cpe_locator.evaluate(f'(el) => el.value = "{invalid_val}"')
    await cpe_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')
    await page.locator(TopPanelLocators.FORM_SAVE_BUTTON).click()
    await page.wait_for_timeout(3000)

    cli_resp_inv = await root_ssh.send_command(RootCommands.get_maxcpe(1))
    cli_val_inv = extract_uci_value(cli_resp_inv.result)
    assert RADIO_TEST_VALUES["MAX_CPE_INVALID"] not in cli_val_inv, (
        f"GUI accepted out-of-bounds value! CLI shows: {cli_val_inv}"
    )

    valid_val = RADIO_TEST_VALUES["MAX_CPE_VALID"]
    await cpe_locator.evaluate(f'(el) => el.value = "{valid_val}"')
    await cpe_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')
    await execute_triple_apply(page, radio_page.RADIO_1_URL_CHUNK)

    if "radio1" not in page.url:
        await radio_page.navigate()

    cli_resp = await root_ssh.send_command(RootCommands.get_maxcpe(1))
    cli_val = extract_uci_value(cli_resp.result)
    assert RADIO_TEST_VALUES["MAX_CPE_VALID"] in cli_val, (
        f"Backend Maximum SUs mismatch! Expected {RADIO_TEST_VALUES['MAX_CPE_VALID']}, got {cli_val}"
    )

    gui_val = await page.locator(RadioPropertiesLocators.MAXIMUM_SU_INPUT).input_value()
    assert gui_val == RADIO_TEST_VALUES["MAX_CPE_VALID"], (
        f"Frontend Maximum SUs mismatch! Expected {RADIO_TEST_VALUES['MAX_CPE_VALID']}, got {gui_val}"
    )


async def _assert_dl_ul_ratio_for_target(target):
    _log(target["role"], "Validating DL/UL Ratio lifecycle.")
    await target["radio_page"].navigate()
    await validate_dropdown_value_lifecycle(
        target["page"],
        target["ssh"],
        locator=RadioPropertiesLocators.DL_UL_RATIO_DROPDOWN,
        uci_cmd=RootCommands.get_dl_ul_ratio(1),
        param_name="DL/UL Ratio",
        fallback_url=target["radio_page"].RADIO_1_URL_CHUNK,
        expected_options=DL_UL_RATIO_OPTIONS,
        test_options=RADIO_TEST_VALUES["DL_UL_RATIO_VALUES"],
    )


async def _assert_ddrs_status_for_target(target):
    _log(target["role"], "Validating DDRS Status lifecycle.")
    await target["radio_page"].open_ddrs_atpc()
    await validate_dropdown_value_lifecycle(
        target["page"],
        target["ssh"],
        locator=RadioPropertiesLocators.DDRS_STATUS_DROPDOWN,
        uci_cmd=RootCommands.get_ddrs_status(1),
        param_name="DDRS Status",
        fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
        expected_options=RADIO_TEST_VALUES["DDRS_STATUS_VALUES"],
        test_options=RADIO_TEST_VALUES["DDRS_STATUS_VALUES"],
    )


async def _assert_spatial_stream_for_targets(targets, bsu_ip: str, device_creds: dict[str, str]):
    for target in targets:
        _log(target["role"], "Validating Spatial Stream lifecycle.")
        await target["radio_page"].open_ddrs_atpc()
        await validate_dropdown_value_lifecycle(
            target["page"],
            target["ssh"],
            locator=RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN,
            uci_cmd=RootCommands.get_spatial_stream(1),
            param_name="Spatial Stream",
            fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
            expected_options=["Single", "Dual", "Auto"],
            test_options=RADIO_TEST_VALUES["SPATIAL_STREAM_VALUES"],
        )

    originals = {target["role"]: await _get_backend_value(target["ssh"], RootCommands.get_spatial_stream(1)) for target in targets}
    try:
        for target in targets:
            await _apply_backend_setting(
                target,
                "txparam.ath1.spatialstream",
                "2",
                RootCommands.get_spatial_stream(1),
                "Spatial Stream behavior prep",
            )
        await _run_trex_behavior_check(bsu_ip, device_creds, "Spatial Stream")
    finally:
        for target in targets:
            await _apply_backend_setting(
                target,
                "txparam.ath1.spatialstream",
                originals[target["role"]],
                RootCommands.get_spatial_stream(1),
                "Spatial Stream Restore",
            )


async def _assert_modulation_index_for_targets(targets, bsu_ip: str, device_creds: dict[str, str]):
    originals = {
        target["role"]: {
            "ddrs": await _get_backend_value(target["ssh"], RootCommands.get_ddrs_status(1)),
            "spatial": await _get_backend_value(target["ssh"], RootCommands.get_spatial_stream(1)),
            "rate": await _get_backend_value(target["ssh"], RootCommands.get_ddrs_rate(1)),
        }
        for target in targets
    }

    try:
        for target in targets:
            _log(target["role"], "Preparing DDRS page for Modulation Index validation.")
            await target["radio_page"].open_ddrs_atpc()
            await _apply_dropdown_for_target(
                target,
                RadioPropertiesLocators.DDRS_STATUS_DROPDOWN,
                RootCommands.get_ddrs_status(1),
                "DDRS Status",
                "Disable",
                target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
            )
            await _apply_dropdown_for_target(
                target,
                RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN,
                RootCommands.get_spatial_stream(1),
                "Spatial Stream",
                "Dual",
                target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
            )
            await validate_dropdown_value_lifecycle(
                target["page"],
                target["ssh"],
                locator=RadioPropertiesLocators.MODULATION_INDEX_DROPDOWN,
                uci_cmd=RootCommands.get_ddrs_rate(1),
                param_name="Modulation Index",
                fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
                test_options=RADIO_TEST_VALUES["MODULATION_INDEX_VALUES"],
                skip_restore=True,
            )

        await _run_trex_behavior_check(bsu_ip, device_creds, "Modulation Index")
    finally:
        for target in targets:
            original = originals[target["role"]]
            await _apply_backend_setting(
                target,
                "txparam.ath1.ddrsrate",
                original["rate"],
                RootCommands.get_ddrs_rate(1),
                "Modulation Index Restore",
            )
            await _apply_backend_setting(
                target,
                "txparam.ath1.spatialstream",
                original["spatial"],
                RootCommands.get_spatial_stream(1),
                "Spatial Stream Restore",
            )
            await _apply_backend_setting(
                target,
                "txparam.ath1.ddrsstatus",
                original["ddrs"],
                RootCommands.get_ddrs_status(1),
                "DDRS Status Restore",
            )


async def _assert_atpc_status_for_target(target):
    _log(target["role"], "Validating ATPC Status lifecycle.")
    await target["radio_page"].open_ddrs_atpc()
    await validate_dropdown_value_lifecycle(
        target["page"],
        target["ssh"],
        locator=RadioPropertiesLocators.ATPC_STATUS_DROPDOWN,
        uci_cmd=RootCommands.get_atpc_status(1),
        param_name="ATPC Status",
        fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
        expected_options=["Disable", "Enable"],
        test_options=["Enable", "Disable"],
    )


async def _assert_transmit_power_for_target(target):
    _log(target["role"], "Validating Transmit Power boundaries.")
    await target["radio_page"].open_ddrs_atpc()
    await validate_input_lifecycle(
        target["page"],
        target["ssh"],
        locator=RadioPropertiesLocators.TRANSMIT_POWER_INPUT,
        valid_val=RADIO_TEST_VALUES["TRANSMIT_POWER_VALUES"][0],
        invalid_val=RADIO_TEST_VALUES["TRANSMIT_POWER_INVALID"],
        uci_cmd=RootCommands.get_tx_power(1),
        param_name="Transmit Power",
        fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
        parser=extract_uci_value,
        skip_restore=True,
    )
    await target["radio_page"].open_ddrs_atpc()
    await validate_input_lifecycle(
        target["page"],
        target["ssh"],
        locator=RadioPropertiesLocators.TRANSMIT_POWER_INPUT,
        valid_val=RADIO_TEST_VALUES["TRANSMIT_POWER_VALUES"][1],
        invalid_val=RADIO_TEST_VALUES["TRANSMIT_POWER_INVALID"],
        uci_cmd=RootCommands.get_tx_power(1),
        param_name="Transmit Power",
        fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
        parser=extract_uci_value,
    )


async def _assert_max_eirp_for_target(target):
    _log(target["role"], "Validating Maximum EIRP lifecycle.")
    await target["radio_page"].open_ddrs_atpc()
    await validate_input_lifecycle(
        target["page"],
        target["ssh"],
        locator=RadioPropertiesLocators.MAX_EIRP_INPUT,
        valid_val=RADIO_TEST_VALUES["MAX_EIRP_VALID"],
        invalid_val=RADIO_TEST_VALUES["MAX_EIRP_INVALID"],
        uci_cmd=RootCommands.get_max_eirp(1),
        param_name="Maximum EIRP",
        fallback_url=target["radio_page"].RADIO_1_DDRS_URL_CHUNK,
        parser=extract_uci_value,
    )


async def assert_gui_23_dl_ul_ratio(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_dl_ul_ratio_for_target(target)
    finally:
        await _close_radio_targets(targets)


async def assert_gui_24_ddrs_status(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_ddrs_status_for_target(target)
    finally:
        await _close_radio_targets(targets)


async def assert_gui_25_spatial_stream(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        await _assert_spatial_stream_for_targets(targets, bsu_ip, device_creds)
    finally:
        await _close_radio_targets(targets)


async def assert_gui_26_modulation_index(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        await _assert_modulation_index_for_targets(targets, bsu_ip, device_creds)
    finally:
        await _close_radio_targets(targets)


async def assert_gui_27_atpc_status(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        cpe_targets = [target for target in targets if target["role"] == "CPE"]
        if not cpe_targets:
            pytest.skip("GUI_27 requires a remote CPE target.")
        for target in cpe_targets:
            await _assert_atpc_status_for_target(target)
    finally:
        await _close_radio_targets(targets)


async def assert_gui_28_transmit_power(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_transmit_power_for_target(target)
    finally:
        await _close_radio_targets(targets)


async def assert_gui_29_maximum_eirp(gui_page, bsu_ip: str, device_creds: dict[str, str]):
    targets = await _open_radio_targets(gui_page, bsu_ip, device_creds)
    try:
        for target in targets:
            await _assert_max_eirp_for_target(target)
    finally:
        await _close_radio_targets(targets)
