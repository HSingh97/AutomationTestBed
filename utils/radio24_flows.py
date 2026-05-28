from __future__ import annotations

import asyncio

import pytest

from config.defaults import RADIO_24_TEST_VALUES
from pages.commands import RootCommands
from pages.locators import Radio24Locators, TopPanelLocators, UITimeouts
from pages.radio24_page import Radio24Page
from utils.parsers import extract_uci_value, parse_bandwidth, parse_iwconfig_active_channel, parse_radio_status
from utils.ui_helpers import (
    attach_dialog_handler,
    execute_form_save,
    execute_super_revert,
    execute_triple_apply,
    fill_luci_input,
    read_luci_input_value,
    validate_dropdown_lifecycle,
    validate_dropdown_value_lifecycle,
)
from utils.validators import validate_backend_param, validate_param

RADIO_24_IDX = 0


async def open_radio_24(gui_page, bsu_ip) -> str:
    """Open Wireless > 2.4 GHz Radio; return admin URL chunk for apply recovery."""
    page = Radio24Page(gui_page, local_ip=bsu_ip)
    await page.navigate()
    return page.RADIO_0_URL_CHUNK


async def _after_radio_24_apply(gui_page, bsu_ip):
    await open_radio_24(gui_page, bsu_ip)


def _alternate_ssid(baseline: str) -> str:
    baseline = (baseline or "").strip() or "UBR24G"
    candidate = f"{baseline}Z" if not baseline.endswith("Z") else f"{baseline}Y"
    return candidate[:32]


def _alternate_key(baseline: str) -> str:
    baseline = (baseline or "").strip() or "Senao24GKey"
    candidate = f"{baseline}1" if not baseline.endswith("1") else f"{baseline}2"
    return candidate[:63]


async def _wait_uci_value(root_ssh, uci_cmd: str, expected: str, *, attempts: int = 20) -> str:
    expected = str(expected).strip()
    last = ""
    for _ in range(attempts):
        last = extract_uci_value((await root_ssh.send_command(uci_cmd)).result)
        if last == expected:
            return last
        await asyncio.sleep(3)
    return last


async def _commit_radio24_change(gui_page, fallback_url: str, root_ssh, uci_cmd: str, expected: str) -> str:
    """Apply via Save/Apply/Super Apply and poll UCI until the expected value is stored."""
    await execute_triple_apply(gui_page, fallback_url)
    for _ in range(8):
        actual = await _wait_uci_value(root_ssh, uci_cmd, expected, attempts=1)
        if actual == expected:
            return actual
        super_apply = gui_page.locator(TopPanelLocators.SUPER_APPLY_BUTTON).first
        if await super_apply.is_visible(timeout=3000):
            await super_apply.click(force=True)
            await gui_page.wait_for_timeout(3000)
            continue
        if "/admin/apply" in (gui_page.url or ""):
            await execute_triple_apply(gui_page, fallback_url)
            continue
        await gui_page.wait_for_timeout(3000)
    return await _wait_uci_value(root_ssh, uci_cmd, expected, attempts=5)


async def _test_radio24_text_field(
    gui_page,
    root_ssh,
    bsu_ip,
    *,
    locator: str,
    uci_cmd: str,
    param_name: str,
    alternate,
    invalid_val: str,
) -> None:
    attach_dialog_handler(gui_page)
    fallback = await open_radio_24(gui_page, bsu_ip)
    baseline = extract_uci_value((await root_ssh.send_command(uci_cmd)).result)
    test_val = alternate(baseline)

    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)

    print(f"    -> [{param_name}] baseline='{baseline}', test='{test_val}'")
    await fill_luci_input(gui_page, locator, invalid_val)
    await execute_form_save(gui_page)
    if "/admin/apply" not in (gui_page.url or ""):
        print(f"    -> [{param_name}] invalid value rejected before apply (expected).")
    else:
        print(f"    -> [{param_name}] invalid value reached apply screen; reverting.")
        await execute_super_revert(gui_page, fallback)
    await _after_radio_24_apply(gui_page, bsu_ip)

    await fill_luci_input(gui_page, locator, test_val)
    await gui_page.wait_for_timeout(500)
    actual = await _commit_radio24_change(gui_page, fallback, root_ssh, uci_cmd, test_val)
    await _after_radio_24_apply(gui_page, bsu_ip)
    validate_backend_param(f"Backend {param_name}", test_val, actual)
    gui_val = await read_luci_input_value(gui_page, locator)
    if gui_val and gui_val != test_val:
        validate_param(f"Frontend {param_name}", test_val, gui_val)

    if baseline and baseline != test_val:
        print(f"    -> Restoring {param_name} to baseline '{baseline}'")
        await fill_luci_input(gui_page, locator, baseline)
        await gui_page.wait_for_timeout(500)
        restored = await _commit_radio24_change(gui_page, fallback, root_ssh, uci_cmd, baseline)
        await _after_radio_24_apply(gui_page, bsu_ip)
        validate_backend_param(f"Restore {param_name}", baseline, restored)


async def assert_gui_34_radio_24_status(root_ssh, gui_page, bsu_ip, device_creds):
    del device_creds
    fallback = await open_radio_24(gui_page, bsu_ip)
    await validate_dropdown_lifecycle(
        gui_page,
        root_ssh,
        locator=Radio24Locators.STATUS_DROPDOWN,
        expected_options=["Enable", "Disable"],
        uci_cmd=RootCommands.get_radio_status(RADIO_24_IDX),
        param_name="2.4 GHz Radio Status",
        fallback_url=fallback,
        parser=parse_radio_status,
        test_all_options=True,
    )


async def assert_gui_35_radio_24_ssid(root_ssh, gui_page, bsu_ip, device_creds):
    del device_creds
    await _test_radio24_text_field(
        gui_page,
        root_ssh,
        bsu_ip,
        locator=Radio24Locators.SSID_INPUT,
        uci_cmd=RootCommands.get_ssid(RADIO_24_IDX),
        param_name="2.4 GHz SSID",
        alternate=_alternate_ssid,
        invalid_val=RADIO_24_TEST_VALUES["SSID_INVALID"],
    )


async def assert_gui_36_radio_24_bandwidth(root_ssh, gui_page, bsu_ip, device_creds):
    del device_creds
    fallback = await open_radio_24(gui_page, bsu_ip)
    await validate_dropdown_lifecycle(
        gui_page,
        root_ssh,
        locator=Radio24Locators.BANDWIDTH_DROPDOWN,
        expected_options=["20 MHz", "40 MHz"],
        uci_cmd=RootCommands.get_bandwidth(RADIO_24_IDX),
        param_name="2.4 GHz Bandwidth",
        fallback_url=fallback,
        parser=parse_bandwidth,
        test_all_options=True,
        use_gui_options=True,
    )


async def assert_gui_37_radio_24_channel_auto(root_ssh, gui_page, bsu_ip, device_creds):
    del device_creds
    fallback = await open_radio_24(gui_page, bsu_ip)

    channel_dropdown = gui_page.locator(Radio24Locators.CONFIGURED_CHANNEL_DROPDOWN).first
    active_display = gui_page.locator(Radio24Locators.ACTIVE_CHANNEL_DISPLAY).first
    await channel_dropdown.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)

    options = await channel_dropdown.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            value: (opt.value || "").trim(),
            text: (opt.textContent || "").trim(),
            disabled: !!opt.disabled
        }))
        """
    )
    enabled = [o for o in options if not o["disabled"]]
    assert enabled, "2.4 GHz configured channel dropdown has no options."
    assert all(o["value"].lower() == "auto" for o in enabled), (
        f"2.4 GHz channel must stay Auto-only; got: {enabled}"
    )

    gui_config = await channel_dropdown.evaluate(
        """
        el => {
            const selected = el.options[el.selectedIndex];
            return selected ? (selected.textContent || "").trim() : "";
        }
        """
    )
    cli_config = extract_uci_value(
        (await root_ssh.send_command(RootCommands.get_configured_channel(RADIO_24_IDX))).result
    )
    gui_select_value = await channel_dropdown.evaluate(
        "el => (el.value || '').trim().toLowerCase()"
    )
    assert gui_select_value == "auto", (
        f"2.4 GHz configured channel GUI value must be 'auto', got '{gui_select_value}'"
    )
    assert "auto" in gui_config.lower(), f"2.4 GHz configured channel label must be Auto, got '{gui_config}'"
    if cli_config.lower() not in ("auto", ""):
        print(
            f"    -> [INFO] UCI advwireless.ath{RADIO_24_IDX}.channel='{cli_config}' "
            "(GUI remains Auto-only; operational channel may differ)."
        )

    gui_active = (await active_display.inner_text()).strip()
    cli_active_raw = (await root_ssh.send_command(RootCommands.get_active_channel(RADIO_24_IDX))).result
    cli_active = parse_iwconfig_active_channel(cli_active_raw)

    if gui_active in ("-", "—", "") or "No such device" in cli_active_raw:
        print("    -> [CHANNEL] Radio not radiating; Auto-only configuration still verified.")
        return

    assert cli_active, f"Could not parse active channel from iwconfig: {cli_active_raw[:200]}"
    channel_num = cli_active.split("(", 1)[0].strip()
    assert channel_num in gui_active or gui_active in cli_active, (
        f"Active channel mismatch. GUI='{gui_active}', CLI='{cli_active}'"
    )


async def assert_gui_38_radio_24_encryption(root_ssh, gui_page, bsu_ip, device_creds):
    del device_creds
    fallback = await open_radio_24(gui_page, bsu_ip)
    await validate_dropdown_value_lifecycle(
        gui_page,
        root_ssh,
        locator=Radio24Locators.ENCRYPTION_DROPDOWN,
        uci_cmd=RootCommands.get_security(RADIO_24_IDX),
        param_name="2.4 GHz Encryption",
        fallback_url=fallback,
        expected_options=["None", "WPA2-PSK"],
        test_options=["None", "WPA2-PSK"],
    )


async def assert_gui_39_radio_24_key(root_ssh, gui_page, bsu_ip, device_creds):
    del device_creds
    enc = extract_uci_value(
        (await root_ssh.send_command(RootCommands.get_security(RADIO_24_IDX))).result
    ).lower()
    if enc in ("none", ""):
        pytest.skip("2.4 GHz encryption is None — key change not applicable (run GUI_38 first).")

    await _test_radio24_text_field(
        gui_page,
        root_ssh,
        bsu_ip,
        locator=Radio24Locators.ENCRYPTION_KEY_INPUT,
        uci_cmd=RootCommands.get_encryption_key(RADIO_24_IDX),
        param_name="2.4 GHz Key",
        alternate=_alternate_key,
        invalid_val=RADIO_24_TEST_VALUES["KEY_INVALID"],
    )
