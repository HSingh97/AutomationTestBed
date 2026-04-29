import re
import pytest
from scrapli.driver.generic import AsyncGenericDriver

from pages.locators import TopPanelLocators, RadioPropertiesLocators, LoginPageLocators
from pages.commands import RootCommands

# Import our universal UI helpers
from utils.ui_helpers import (
    validate_dropdown_lifecycle,
    validate_input_lifecycle,
    execute_triple_apply
)

# Import parsing helpers directly from utils.parsers
from utils.parsers import (
    extract_uci_value,
    parse_radio_status,
    parse_radio_mode,
    parse_link_type,
    parse_bandwidth,
    parse_encryption,
    parse_iwconfig_active_channel
)

pytestmark = pytest.mark.sanity

# Constants for this specific page
RADIO_1_URL_CHUNK = "/admin/wireless/radio1"


# =====================================================================
# SETUP: Navigate Helper
# =====================================================================
async def navigate_to_radio_properties_page(gui_page, local_ip="192.168.2.230"):
    if "chrome-error" in gui_page.url:
        print("    -> [RECOVERY] Chrome error page detected! Forcing a hard reload to router UI...")
        await gui_page.goto(f"https://{local_ip}/cgi-bin/luci/admin/wireless/radio1", timeout=15000)
        await gui_page.wait_for_timeout(4000)
    if "radio1" in gui_page.url.lower() and "admin" in gui_page.url.lower():
        return
    radio_1 = gui_page.locator(RadioPropertiesLocators.SUBMENU_RADIO_1).first
    if not await radio_1.is_visible():
        await gui_page.locator(RadioPropertiesLocators.MENU_WIRELESS).first.click()
        await gui_page.wait_for_timeout(1000)
    await radio_1.click()


# =====================================================================
# GUI_17: Wireless - Radio - Properties [Status]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_17
@pytest.mark.WirelessProperties
async def test_gui_17_radio_status(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_17: Verifying Radio Status (Enable/Disable)")

    await validate_dropdown_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.STATUS_DROPDOWN,
        expected_options=["Enable", "Disable"],
        uci_cmd=RootCommands.get_radio_status(1),
        param_name="Status",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=parse_radio_status,
        test_all_options=True
    )
    print("\n[+] GUI_17 Soft Validation Complete.")


# # =====================================================================
# # GUI_18: Wireless - Radio - Properties [Link Type]
# # =====================================================================
# @pytest.mark.asyncio(scope="session")
# @pytest.mark.GUI_18
# @pytest.mark.WirelessProperties
# async def test_gui_18_link_type(gui_page, root_ssh):
#     await navigate_to_radio_properties_page(gui_page)
#     print("\n[+] Starting GUI_18: Verifying Link Type (PTP/PTMP)")

#     await validate_dropdown_lifecycle(
#         gui_page, root_ssh,
#         locator=RadioPropertiesLocators.LINK_TYPE_DROPDOWN,
#         expected_options=["PTP", "PTMP"],
#         uci_cmd=RootCommands.get_link_type(1),
#         param_name="Link Type",
#         fallback_url=RADIO_1_URL_CHUNK,
#         parser=parse_link_type,
#         test_all_options=True
#     )
#     print("\n[+] GUI_18 Soft Validation Complete.")


# # =====================================================================
# # GUI_19: Wireless - Radio - Properties [Radio Mode]
# # =====================================================================
# @pytest.mark.asyncio(scope="session")
# @pytest.mark.GUI_19
# @pytest.mark.WirelessProperties
# async def test_gui_19_radio_mode(gui_page, root_ssh, request):
#     fallback_ip = request.config.getoption("--fallback-ip")
#     local_ip = request.config.getoption("--local-ip")
#     username = request.config.getoption("--username")
#     password = request.config.getoption("--password")

#     print(f"\n[+] Establishing isolated SSH connection to Fallback IP ({fallback_ip})...")
#     fallback_ssh = AsyncGenericDriver(
#         host=fallback_ip,
#         auth_username=username,
#         auth_password=password,
#         auth_strict_key=False,
#         transport="asyncssh"
#     )
#     await fallback_ssh.open()

#     try:
#         current_url = gui_page.url
#         if fallback_ip not in current_url:
#             print(f"    -> Pivoting GUI session to Fallback IP ({fallback_ip})...")
#             match = re.search(r'(https?://)(?:[^/]+)(/.*)', current_url)
#             if match:
#                 safe_url = f"https://{fallback_ip}{match.group(2)}"
#                 try:
#                     await gui_page.goto(safe_url, wait_until="domcontentloaded", timeout=10000)
#                 except Exception:
#                     await gui_page.goto(f"https://{fallback_ip}/admin/wireless/radio1", wait_until="domcontentloaded",
#                                         timeout=15000)

#             await gui_page.wait_for_timeout(2000)

#         try:
#             if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=2000):
#                 await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.fill(username)
#                 await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).first.fill(password)
#                 await gui_page.locator(LoginPageLocators.LOGIN_BUTTON).first.click()
#                 await gui_page.wait_for_timeout(3000)
#         except Exception:
#             pass

#         await navigate_to_radio_properties_page(gui_page)
#         print("\n[+] Starting GUI_19: Verifying Radio Mode (BTS/CPE)")

#         await validate_dropdown_lifecycle(
#             gui_page, fallback_ssh,
#             locator=RadioPropertiesLocators.RADIO_MODE_DROPDOWN,
#             expected_options=["BTS", "CPE"],
#             uci_cmd=RootCommands.get_radio_mode(1),
#             param_name="Radio Mode",
#             fallback_url=RADIO_1_URL_CHUNK,
#             parser=lambda raw: parse_radio_mode(raw, 1),
#             test_all_options=True
#         )
#     finally:
#         await fallback_ssh.close()
#         print(f"    -> [TEARDOWN] Waiting 15 seconds for router hardware to stabilize...")
#         await gui_page.wait_for_timeout(15000)

#         current_url = gui_page.url
#         if fallback_ip in current_url or "chrome-error" in current_url:
#             print(f"    -> [TEARDOWN] Pivoting GUI session back to Primary IP ({local_ip})...")
#             match = re.search(r'(https?://)(?:[^/]+)(/.*)', current_url)
#             if match and "chrome-error" not in current_url:
#                 safe_url = f"https://{local_ip}{match.group(2)}"
#                 try:
#                     await gui_page.goto(safe_url, wait_until="domcontentloaded", timeout=10000)
#                 except Exception:
#                     await gui_page.goto(f"https://{local_ip}/cgi-bin/luci", wait_until="domcontentloaded",
#                                         timeout=15000)
#             else:
#                 await gui_page.goto(f"https://{local_ip}/cgi-bin/luci", wait_until="domcontentloaded", timeout=15000)
#             await gui_page.wait_for_timeout(3000)
#             try:
#                 if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.is_visible(timeout=3000):
#                     await gui_page.locator(LoginPageLocators.USERNAME_INPUT).first.fill(username)
#                     await gui_page.locator(LoginPageLocators.PASSWORD_INPUT).first.fill(password)
#                     await gui_page.locator(LoginPageLocators.LOGIN_BUTTON).first.click()
#                     await gui_page.wait_for_timeout(5000)
#             except Exception:
#                 pass

#     print("\n[+] GUI_19 Soft Validation Complete.")


# =====================================================================
# GUI_18: Wireless - Radio - Properties [SSID]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_18
@pytest.mark.WirelessProperties
async def test_gui_20_ssid(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_20: Verifying SSID Input (Range 1-32 chars)")

    await validate_input_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.SSID_INPUT,
        valid_val="A_Valid_32_Character_SSID_123456",
        invalid_val="An_Invalid_33_Character_SSID_1234567",
        uci_cmd=RootCommands.get_ssid(1),
        param_name="SSID",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=extract_uci_value
    )
    print("\n[+] GUI_18 Soft Validation Complete.")


# =====================================================================
# GUI_19: Wireless - Radio - Properties [Bandwidth]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_19
@pytest.mark.WirelessProperties
async def test_gui_21_bandwidth(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_21: Verifying Bandwidth (20 / 40 / 80 MHz)")

    await validate_dropdown_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.BANDWIDTH_DROPDOWN,
        expected_options=["20 MHz", "40 MHz", "80 MHz", "160 MHz"],
        uci_cmd=RootCommands.get_bandwidth(1),
        param_name="Bandwidth",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=parse_bandwidth,
        test_all_options=True
    )
    print("\n[+] GUI_19 Soft Validation Complete.")


# =====================================================================
# GUI_20: Wireless - Radio - Properties [Configured & Active Channel]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_20
@pytest.mark.WirelessProperties
async def test_gui_22_channel(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_22: Verifying Configured Channel & Active Channel")

    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)

    # Use robust dynamic locators instead of fragile absolute XPaths
    channel_dropdown = gui_page.locator("//select[contains(@name, '.channel')]").first
    active_channel_display = gui_page.locator("#opchannel").first

    raw_mode_resp = await root_ssh.send_command(RootCommands.get_radio_mode(1))
    current_mode = parse_radio_mode(raw_mode_resp.result, 1)
    print(f"    -> Current Radio Mode detected as: {current_mode}")

    if current_mode == "BTS":
        print("    -> [BTS Mode] Executing 4-way channel validation.")
        test_channels = ["36", "149", "165"]

        for channel in test_channels:
            print(f"       -> Testing Channel: {channel}")

            await channel_dropdown.select_option(value=channel, timeout=5000)
            await execute_triple_apply(gui_page, RADIO_1_URL_CHUNK)

            print("          -> Waiting for radio hardware to restart and stabilize...")
            # HARDWARE STABILIZATION LOOP: Look for Channel or Frequency to confirm interface is up
            for _ in range(15):  # Up to 45 seconds
                cli_active_resp = await root_ssh.send_command(RootCommands.get_active_channel(1))
                if "No such device" not in cli_active_resp.result and (
                        "Channel" in cli_active_resp.result or "Frequency" in cli_active_resp.result):
                    print("          -> Hardware stabilized.")
                    break
                await gui_page.wait_for_timeout(3000)
            else:
                print("          -> [WARNING] Hardware took too long to stabilize.")

            # Ensure we are back on the properties page after apply
            if "radio1" not in gui_page.url:
                await navigate_to_radio_properties_page(gui_page)

            # Give LuCI's JavaScript a moment to fetch the new channel data via AJAX
            await gui_page.wait_for_timeout(4000)

            # Re-resolve locators after page load
            channel_dropdown = gui_page.locator("//select[contains(@name, '.channel')]").first
            active_channel_display = gui_page.locator("#opchannel").first

            gui_config_val = await channel_dropdown.evaluate("el => el.options[el.selectedIndex].text")
            gui_active_val = await active_channel_display.inner_text()

            cli_config_resp = await root_ssh.send_command(RootCommands.get_configured_channel(1))
            cli_active_resp = await root_ssh.send_command(RootCommands.get_active_channel(1))

            cli_config_val = extract_uci_value(cli_config_resp.result)
            cli_active_val = parse_iwconfig_active_channel(cli_active_resp.result)

            print(f"          - GUI Configured: {gui_config_val.strip()}")
            print(f"          - GUI Active:     {gui_active_val.strip()}")
            print(f"          - CLI Configured: {cli_config_val.strip()}")
            print(f"          - CLI Active:     {cli_active_val.strip()}")

            assert channel in gui_config_val, f"GUI Config mismatch. Expected {channel}, got {gui_config_val}"
            assert channel in gui_active_val, f"GUI Active mismatch. Expected {channel}, got {gui_active_val}"
            assert channel in cli_config_val, f"CLI Config mismatch. Expected {channel}, got {cli_config_val}"
            assert channel in cli_active_val, f"CLI Active mismatch. Expected {channel}, got {cli_active_val}"
            print("          [PASS] All 4 parameters match.")

    elif current_mode == "CPE":
        print("    -> [CPE Mode] Confirming Active Channel matches CLI Active Channel.")

        gui_active_val = await active_channel_display.inner_text()
        cli_active_resp = await root_ssh.send_command(RootCommands.get_active_channel(1))
        cli_active_val = parse_iwconfig_active_channel(cli_active_resp.result)

        print(f"          - GUI Active: {gui_active_val.strip()}")
        print(f"          - CLI Active: {cli_active_val.strip()}")

        assert cli_active_val in gui_active_val or gui_active_val in cli_active_val, "GUI Active Channel does not match CLI Active Channel in CPE mode."
        print("          [PASS] Active channels match in CPE mode.")

    else:
        pytest.fail(f"Unknown radio mode: {current_mode}")

    print("\n[+] GUI_20 Soft Validation Complete.")


# =====================================================================
# GUI_21: Wireless - Radio - Properties [Encryption]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_21
@pytest.mark.WirelessProperties
async def test_gui_23_encryption(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_23: Verifying Encryption (AES-256 vs None)")

    await validate_dropdown_lifecycle(
        gui_page, root_ssh,
        locator=RadioPropertiesLocators.ENCRYPTION_DROPDOWN,
        expected_options=["AES-256", "None"],
        uci_cmd=RootCommands.get_security(1),
        param_name="Encryption",
        fallback_url=RADIO_1_URL_CHUNK,
        parser=parse_encryption,
        test_all_options=True
    )
    print("\n[+] GUI_21 Soft Validation Complete.")


# # =====================================================================
# # GUI_24: Wireless - Radio - Properties [Key & Network Secret]
# # =====================================================================
# @pytest.mark.asyncio(scope="session")
# @pytest.mark.GUI_24
# @pytest.mark.WirelessProperties
# async def test_gui_24_key_and_secret(gui_page, root_ssh):
#     """
#     Batches the inputs for Encryption Key and Network Secret using pure DOM manipulation
#     to prevent Playwright focus-loss bugs and concatenation.
#     """
#     await navigate_to_radio_properties_page(gui_page)
#     print("\n[+] Starting GUI_24: Verifying Key & Network Secret (Batched Execution)")

#     await gui_page.wait_for_load_state("domcontentloaded")
#     await gui_page.wait_for_timeout(2000)

#     enc_locator = gui_page.locator("//select[contains(@name, 'encryption')]").first

#     # Helper to natively select encryption so the UI visually updates
#     async def set_encryption_visually(target_label):
#         print(f"    -> [UI STATE] Setting Encryption to {target_label}...")
#         try:
#             await enc_locator.select_option(label=target_label, force=True, timeout=3000)
#         except Exception:
#             target_val = "psk2+ccmp-256" if "AES" in target_label else "none"
#             await enc_locator.select_option(value=target_val, force=True)
#         await gui_page.wait_for_timeout(1500)

#     # CRITICAL: Pure JS Injector - Impossible to concatenate
#     async def inject_values(key_val, sec_val):
#         print(f"    -> [INJECT] Key='{key_val}', Secret='{sec_val}'")
#         await gui_page.evaluate(f"""() => {{
#             let k = document.getElementsByName('wireless.@wifi-iface[1].key')[0];
#             let s = document.getElementsByName('wireless.wifi1.nwksecret')[0];
#             if(k) {{ k.value = '{key_val}'; k.dispatchEvent(new Event('change', {{bubbles: true}})); }}
#             if(s) {{ s.value = '{sec_val}'; s.dispatchEvent(new Event('change', {{bubbles: true}})); }}
#         }}""")

#     # 1. Fetch current defaults from backend for ALL three fields
#     cli_enc_resp = await root_ssh.send_command(RootCommands.get_security(1))
#     default_enc = extract_uci_value(cli_enc_resp.result).strip()

#     cli_key_resp = await root_ssh.send_command(RootCommands.get_encryption_key(1))
#     default_key = extract_uci_value(cli_key_resp.result).strip()

#     cli_sec_resp = await root_ssh.send_command(RootCommands.get_network_secret(1))
#     default_secret = extract_uci_value(cli_sec_resp.result).strip()
#     print(f"    -> Cached Defaults - Enc: '{default_enc}', Key: '{default_key}', Secret: '{default_secret}'")

#     # 2. Setup Dialog Handler for Alert Interception
#     alert_triggered = False

#     async def dialog_handler(dialog):
#         nonlocal alert_triggered
#         alert_triggered = True
#         print(f"    -> [UI ALERT DETECTED] Auto-accepting popup: '{dialog.message.strip()}'")
#         try:
#             await dialog.accept()
#         except:
#             pass

#     gui_page.on("dialog", dialog_handler)

#     # Reveal fields using native interaction
#     await set_encryption_visually("AES-256")

#     # 3. Test Invalid Key Boundary
#     print("    -> Testing Invalid Key Boundary: 'short'")
#     await inject_values("short", default_secret)
#     await gui_page.locator(TopPanelLocators.FORM_SAVE_BUTTON).click()
#     await gui_page.wait_for_timeout(2000)
#     assert alert_triggered, "GUI failed to block invalid Encryption Key!"
#     alert_triggered = False

#     # 4. Test Invalid Secret Boundary
#     print("    -> Testing Invalid Secret Boundary: 'srt' (Under 8 chars)")
#     await inject_values("ValidKey123", "srt")
#     await gui_page.locator(TopPanelLocators.FORM_SAVE_BUTTON).click()
#     await gui_page.wait_for_timeout(2000)

#     cli_sec_inv = extract_uci_value((await root_ssh.send_command(RootCommands.get_network_secret(1))).result).strip()
#     assert cli_sec_inv != "srt", "Backend accepted invalid 3-char Secret!"
#     print("       [PASS] Invalid Network Secret rejected.")

#     gui_page.remove_listener("dialog", dialog_handler)

#     # 5. BATCHED APPLY: Valid Data
#     print("    -> Applying Valid Configs for BOTH Key and Secret...")
#     await set_encryption_visually("AES-256")

#     await inject_values("ValidKey12345", "ValidSec")
#     await execute_triple_apply(gui_page, RADIO_1_URL_CHUNK)

#     if "radio1" not in gui_page.url:
#         print("    -> WARNING: Did not automatically return. Forcing navigation to /admin/wireless/radio1")
#         await navigate_to_radio_properties_page(gui_page)
#         await set_encryption_visually("AES-256")

#     # 6. Verify Backend & GUI (Strict Equality)
#     print("    -> Verifying persistence in Backend and Frontend...")
#     cli_key_check = extract_uci_value((await root_ssh.send_command(RootCommands.get_encryption_key(1))).result).strip()
#     cli_sec_check = extract_uci_value((await root_ssh.send_command(RootCommands.get_network_secret(1))).result).strip()

#     assert cli_key_check == "ValidKey12345", f"Key mismatch! Expected 'ValidKey12345', Got: '{cli_key_check}'"
#     assert cli_sec_check == "ValidSec", f"Secret mismatch! Expected 'ValidSec', Got: '{cli_sec_check}'"

#     # Pull GUI values directly from DOM state
#     gui_vals = await gui_page.evaluate("""() => {
#         return {
#             key: document.getElementsByName('wireless.@wifi-iface[1].key')[0]?.value,
#             sec: document.getElementsByName('wireless.wifi1.nwksecret')[0]?.value
#         };
#     }""")
#     assert gui_vals['key'] == "ValidKey12345", f"GUI Key Mismatch! Expected 'ValidKey12345', Got: '{gui_vals['key']}'"
#     assert gui_vals['sec'] == "ValidSec", f"GUI Secret Mismatch! Expected 'ValidSec', Got: '{gui_vals['sec']}'"
#     print("       [PASS] Key and Secret successfully updated in backend and GUI.")

#     # 7. Batched Restoration to Defaults
#     print("    -> Restoring ALL fields to original defaults...")

#     # Clean up the concatenated mess from the previous runs if it got saved
#     safe_default_key = "ubr@1234" if "Valid" in default_key else default_key
#     safe_default_sec = "ubr@1234" if "Valid" in default_secret else default_secret

#     await set_encryption_visually("AES-256")
#     await inject_values(safe_default_key, safe_default_sec)

#     # Only revert to 'None' if that was actually the default
#     if "none" in parse_encryption(default_enc).lower():
#         await set_encryption_visually("None")

#     await execute_triple_apply(gui_page, RADIO_1_URL_CHUNK)

#     print("\n[+] GUI_24 Soft Validation Complete.")


# # =====================================================================
# # GUI_25: Wireless - Radio - Properties [Distance]
# # =====================================================================
# @pytest.mark.asyncio(scope="session")
# @pytest.mark.GUI_25
# @pytest.mark.WirelessProperties
# async def test_gui_25_distance(gui_page, root_ssh):
#     """
#     Custom lifecycle to bypass strict Playwright visibility checks.
#     LuCI often uses CSS to hide raw `<input>` tags, causing standard
#     Playwright `.fill()` and `visible` waits to timeout.
#     """
#     await navigate_to_radio_properties_page(gui_page)
#     print("\n[+] Starting GUI_25: Verifying Distance (Limit 1-30)")
#
#     await gui_page.wait_for_load_state("domcontentloaded")
#     await gui_page.wait_for_timeout(3000)
#
#     dist_locator = gui_page.locator(RadioPropertiesLocators.DISTANCE_INPUT)
#
#     # Check if element exists in the DOM
#     if await dist_locator.count() == 0:
#         pytest.fail(f"CRITICAL: Could not find element {RadioPropertiesLocators.DISTANCE_INPUT} in the DOM.")
#
#     # --- VALID VALUE TEST ---
#     print("    -> Testing Valid Value: 20")
#
#     # Inject value via JS, bypassing "display: none" wrappers
#     await dist_locator.evaluate('(el) => el.value = "20"')
#     await dist_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')
#
#     await execute_triple_apply(gui_page, RADIO_1_URL_CHUNK)
#
#     if "radio1" not in gui_page.url:
#         await navigate_to_radio_properties_page(gui_page)
#
#     print("    -> Verifying persistence in Backend and Frontend...")
#     # Verify Backend
#     cli_resp = await root_ssh.send_command(RootCommands.get_distance(1))
#     cli_val = extract_uci_value(cli_resp.result)
#     assert "20" in cli_val, f"Backend Distance mismatch! Expected 20, got {cli_val}"
#
#     # Verify GUI
#     gui_dist_val = await gui_page.locator(RadioPropertiesLocators.DISTANCE_INPUT).input_value()
#     assert gui_dist_val == "20", f"GUI Distance mismatch! Expected 20, got {gui_dist_val}"
#
#     print("       [PASS] Valid distance successfully updated in both CLI and GUI.")
#
#     # --- INVALID VALUE TEST ---
#     print("    -> Testing Invalid Value: 35 (Should be rejected)")
#
#     # Inject out-of-bounds value
#     await dist_locator.evaluate('(el) => el.value = "35"')
#     await dist_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')
#
#     # Only click save since it should trigger frontend validation error
#     await gui_page.locator(TopPanelLocators.FORM_SAVE_BUTTON).click()
#     print("    -> Applied changes. Checking rejection...")
#     await gui_page.wait_for_timeout(3000)
#
#     # Check backend to ensure invalid value was REJECTED (should still be 20)
#     cli_resp_inv = await root_ssh.send_command(RootCommands.get_distance(1))
#     cli_val_inv = extract_uci_value(cli_resp_inv.result)
#
#     assert "35" not in cli_val_inv, f"GUI accepted an out-of-bounds value! CLI shows: {cli_val_inv}"
#     print("       [PASS] Invalid distance was correctly rejected by the system.")
#
#     print("\n[+] GUI_25 Soft Validation Complete.")

# =====================================================================
# GUI_22: Wireless - Radio - Properties [MAX CPEs]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_22
@pytest.mark.WirelessProperties
async def test_gui_22_max_cpe(gui_page, root_ssh):
    await navigate_to_radio_properties_page(gui_page)
    print("\n[+] Starting GUI_22: Verifying MAX Cpe Input (Range 1-32 )")

    await gui_page.wait_for_load_state("domcontentloaded")
    await gui_page.wait_for_timeout(2000)

    cpe_locator = gui_page.locator(RadioPropertiesLocators.MAXIMUM_SU_INPUT)
    if await cpe_locator.count() == 0:
        pytest.fail(f"CRITICAL: Could not find element {RadioPropertiesLocators.MAXIMUM_SU_INPUT} in the DOM.")

    # --- INVALID VALUE TEST ---
    print("    -> Testing Invalid Boundary: '34'")
    await cpe_locator.evaluate('(el) => el.value = "34"')
    await cpe_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')
    await gui_page.locator(TopPanelLocators.FORM_SAVE_BUTTON).click()
    await gui_page.wait_for_timeout(3000)

    cli_resp_inv = await root_ssh.send_command(RootCommands.get_maxcpe(1))
    cli_val_inv = extract_uci_value(cli_resp_inv.result)
    assert "34" not in cli_val_inv, f"GUI accepted out-of-bounds value! CLI shows: {cli_val_inv}"
    print("    -> Invalid boundary correctly rejected by GUI.")

    # --- VALID VALUE TEST ---
    print("    -> Applying and Verifying Valid Config: '10'")
    await cpe_locator.evaluate('(el) => el.value = "10"')
    await cpe_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')

    await execute_triple_apply(gui_page, RADIO_1_URL_CHUNK)

    if "radio1" not in gui_page.url:
        await navigate_to_radio_properties_page(gui_page)

    # Verify Backend
    cli_resp = await root_ssh.send_command(RootCommands.get_maxcpe(1))
    cli_val = extract_uci_value(cli_resp.result)
    assert "10" in cli_val, f"Backend Maximum SUs Change Mismatch! Expected 10, got {cli_val}"

    # Verify Frontend
    gui_val = await gui_page.locator(RadioPropertiesLocators.MAXIMUM_SU_INPUT).input_value()
    assert gui_val == "10", f"Frontend Maximum SUs Change Mismatch! Expected 10, got {gui_val}"

    print("    -> Maximum SUs successfully updated to 10.")

    # --- RESTORE DEFAULT ---
    print("    -> Restoring Maximum SUs to centralized default: '16'")
    await cpe_locator.evaluate('(el) => el.value = "16"')
    await cpe_locator.evaluate('(el) => el.dispatchEvent(new Event("change", { bubbles: true }))')
    await execute_triple_apply(gui_page, RADIO_1_URL_CHUNK)

    print("\n[+] GUI_22 Soft Validation Complete.")