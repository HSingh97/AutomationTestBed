import pytest

from config.defaults import RADIO_TEST_VALUES
from pages.commands import RootCommands
from pages.locators import RadioPropertiesLocators, TopPanelLocators
from utils.parsers import (
    extract_uci_value,
    parse_bandwidth,
    parse_encryption,
    parse_iwconfig_active_channel,
    parse_radio_mode,
    parse_radio_status,
)
from utils.ui_helpers import (
    execute_triple_apply,
    validate_dropdown_lifecycle,
    validate_input_lifecycle,
)


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
