import pytest
import random
import asyncio
import re
from pages.locators import NetworkLocators as NL, DHCPLocators
from pages.commands import RootCommands
from utils.parsers import generate_test_ip
from pages.locators import LoginPageLocators
from pages.locators import EthernetLocators as EL

# Standard markers for Jenkins
pytestmark = [pytest.mark.sanity, pytest.mark.Network]


# =====================================================================
# GUI_50: IP CONFIGURATION VALIDATION (IPv4 & IPv6) - [STABLE]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_50
@pytest.mark.Network
async def test_gui_50_network_ip_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_50: Cross-Validating IP Configuration")

    print(f"    -> Step 1: Navigating to https://{bsu_ip}")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    print("    -> Step 2: Clicking Network -> IP Configuration")
    await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
    await gui_page.wait_for_timeout(1000)
    await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
    await gui_page.wait_for_selector(NL.IPv4_ADDRESS, timeout=15000)

    gui_ip4 = await gui_page.locator(NL.IPv4_ADDRESS).first.input_value()
    gui_ip6 = await gui_page.locator(NL.IPv6_ADDRESS).first.input_value()

    ssh_ip4 = (await root_ssh.send_command(RootCommands.GET_NET_IP)).result.strip()
    ssh_ip6 = (await root_ssh.send_command(RootCommands.GET_NET_IP6)).result.strip().replace("'", "")

    print(f"    -> [CHECK] IPv4 - GUI: {gui_ip4} | SSH: {ssh_ip4}")
    print(f"    -> [CHECK] IPv6 - GUI: {gui_ip6} | SSH: {ssh_ip6}")

    from utils.validators import validate_param
    validate_param("IPv4 ADDRESS", ssh_ip4, gui_ip4)
    validate_param("IPv6 ADDRESS", ssh_ip6, gui_ip6)
    print("[+] GUI_50 Passed Successfully.")


# =====================================================================
# GUI_51: EDIT & VERIFY IP ADDRESS (IPv4 + IPv6)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_51
@pytest.mark.Network
async def test_gui_51_edit_ip_config(gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_51: Dual-Stack Edit & Revert (v4/v6)")
    HEADER_RED_ICON = "#header_apply"
    CONFIRM_APPLY_BTN = "input[value='Apply']"

    print(f"    -> Step 1: Login to {bsu_ip}")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    print("    -> Step 2: Navigating to IP Settings")
    await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
    await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
    await gui_page.wait_for_selector(NL.IPv4_ADDRESS, timeout=15000)

    orig_ip4 = await gui_page.locator(NL.IPv4_ADDRESS).first.input_value()
    orig_ip6 = await gui_page.locator(NL.IPv6_ADDRESS).first.input_value()
    new_ip4 = generate_test_ip(orig_ip4, "v4")
    new_ip6 = f"{orig_ip6.split('/')[0].rsplit(':', 1)[0]}:{random.randint(100, 999)}/64"

    try:
        print(f"    -> Step 3: Changing Stack to IPv4={new_ip4} | IPv6={new_ip6}")
        await gui_page.locator(NL.IPv4_ADDRESS).first.fill(new_ip4)
        await gui_page.locator(NL.IPv6_ADDRESS).first.fill(new_ip6)
        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(2000)

        print("    -> Step 4: Clicking Red Header Apply Icon")
        await gui_page.locator(HEADER_RED_ICON).first.click()
        await gui_page.wait_for_timeout(2000)

        print("    -> Step 5: Clicking Final Apply Button")
        await gui_page.locator(CONFIRM_APPLY_BTN).first.click()

        print(f"    -> Waiting 40s for stable network transition to {new_ip4}...")
        await asyncio.sleep(40)

        print(f"    -> Step 6: Verification via Backend SSH at {new_ip4}")
        from scrapli.driver.generic.async_driver import AsyncGenericDriver
        verify_ssh = AsyncGenericDriver(host=new_ip4, auth_username=device_creds["user"],
                                        auth_password=device_creds["pass"], auth_strict_key=False, transport="asyncssh")
        await verify_ssh.open()
        try:
            raw_v4 = (await verify_ssh.send_command(RootCommands.GET_NET_IP)).result
            raw_v6 = (await verify_ssh.send_command(RootCommands.GET_NET_IP6)).result

            res4 = next((line.strip() for line in reversed(raw_v4.splitlines()) if
                         re.match(r"^\d+\.\d+\.\d+\.\d+$", line.strip())), None)
            res6 = next((line.strip().replace("'", "") for line in reversed(raw_v6.splitlines()) if ":" in line), None)

            print(f"       [SSH Result] IPv4: {res4} | IPv6: {res6}")
            assert res4 == new_ip4
            assert new_ip6 in res6
            print(f"    -> [SUCCESS] Dual-Stack verified via CLI.")
        finally:
            await verify_ssh.close()

    finally:
        print(f"    -> Step 7 [REVERT]: Returning to Original Stack via {new_ip4}")
        try:
            await gui_page.goto(f"https://{new_ip4}/cgi-bin/luci/")
            await gui_page.wait_for_timeout(5000)
            if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
                await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
                await gui_page.wait_for_timeout(3000)

            await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
            await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
            await gui_page.locator(NL.IPv4_ADDRESS).first.fill(orig_ip4)
            await gui_page.locator(NL.IPv6_ADDRESS).first.fill(orig_ip6)
            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(2000)
            await gui_page.locator(HEADER_RED_ICON).first.click()
            await gui_page.wait_for_timeout(2000)
            await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
            print("    -> Reversion complete. Device returning to base IP.")
            await asyncio.sleep(30)
        except Exception as e:
            print(f"    -> [Warning] Reversion cleanup failed: {e}")


# =====================================================================
# GUI_52: EDIT & VERIFY SUBNET MASK - [STABLE]
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_52
@pytest.mark.Network
async def test_gui_52_edit_netmask_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_52: Netmask Edit & Verify")
    HEADER_RED_ICON = "#header_apply"
    CONFIRM_APPLY_BTN = "input[value='Apply']"

    print(f"    -> Step 1: Login to {bsu_ip}")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    print("    -> Step 2: Navigating to IP Settings")
    await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
    await gui_page.wait_for_timeout(1000)
    await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
    await gui_page.wait_for_selector(NL.IPv4_NETMASK, timeout=15000)

    orig_mask = await gui_page.locator(NL.IPv4_NETMASK).first.input_value()
    new_mask = "255.255.0.0" if orig_mask == "255.255.255.0" else "255.255.255.0"

    try:
        print(f"    -> Step 3: Changing Mask to {new_mask}")
        await gui_page.locator(NL.IPv4_NETMASK).first.fill(new_mask)
        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(2000)
        await gui_page.locator(HEADER_RED_ICON).first.click()
        await gui_page.wait_for_timeout(2000)
        await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
        await asyncio.sleep(20)

        print("    -> Step 4: Verification via SSH")
        ssh_mask = (await root_ssh.send_command(RootCommands.GET_NET_MASK)).result.strip().splitlines()[-1]
        print(f"       [SSH Result] Mask: {ssh_mask}")
        assert ssh_mask == new_mask
        print(f"    -> [SUCCESS] Netmask verified in CLI.")

    finally:
        print(f"    -> Step 5 [REVERT]: Returning Mask to {orig_mask}")
        try:
            await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
            await gui_page.wait_for_timeout(3000)
            if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
                await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")

            await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
            await gui_page.wait_for_timeout(1000)
            await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
            await gui_page.locator(NL.IPv4_NETMASK).first.fill(orig_mask)
            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(2000)
            await gui_page.locator(HEADER_RED_ICON).first.click()
            await gui_page.wait_for_timeout(2000)
            await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
            await asyncio.sleep(15)
        except Exception as e:
            print(f"    -> Cleanup Error: {e}")


# =====================================================================
# GUI_53: EDIT & VERIFY GATEWAY
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_53
@pytest.mark.Network
async def test_gui_53_edit_gateway_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_53: Gateway Edit & Verify")
    HEADER_RED_ICON = "#header_apply"
    CONFIRM_APPLY_BTN = "input[value='Apply']"

    print(f"    -> Step 1: Login to {bsu_ip}")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    print("    -> Step 2: Navigating to IP Settings")
    await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
    await gui_page.wait_for_timeout(1000)
    await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
    await gui_page.wait_for_selector(NL.IPv4_GATEWAY, timeout=15000)

    orig_gw = await gui_page.locator(NL.IPv4_GATEWAY).first.input_value()
    new_gw = "192.168.2.254" if orig_gw != "192.168.2.254" else "192.168.2.253"

    try:
        print(f"    -> Step 3: Changing Gateway to {new_gw}")
        await gui_page.locator(NL.IPv4_GATEWAY).first.fill(new_gw)
        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(2000)
        await gui_page.locator(HEADER_RED_ICON).first.click()
        await gui_page.wait_for_timeout(2000)
        await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
        await asyncio.sleep(25)

        print("    -> Step 4: Verification via SSH")
        ssh_gw = (await root_ssh.send_command(RootCommands.GET_NET_GW)).result.strip().splitlines()[-1]
        print(f"       [SSH Result] Gateway: {ssh_gw}")
        assert ssh_gw == new_gw
        print(f"    -> [SUCCESS] Gateway verified in CLI.")

    finally:
        print(f"    -> Step 5 [REVERT]: Returning Gateway to original: {orig_gw}")
        try:
            await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
            await gui_page.wait_for_timeout(3000)
            if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
                await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")

            await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
            await gui_page.wait_for_timeout(1000)
            await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()
            await gui_page.locator(NL.IPv4_GATEWAY).first.fill(orig_gw)
            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(2000)
            await gui_page.locator(HEADER_RED_ICON).first.click()
            await gui_page.wait_for_timeout(2000)
            await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
            await asyncio.sleep(20)
        except Exception as e:
            print(f"    -> Cleanup Error: {e}")


# =====================================================================
# GUI_54: EDIT & VERIFY FALLBACK IP ADDRESS
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_54
@pytest.mark.Network
async def test_gui_54_edit_fallback_ip(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_54: Fallback IP Edit & Verify")
    HEADER_RED_ICON = "#header_apply"
    CONFIRM_APPLY_BTN = "input[value='Apply']"

    print(f"    -> Step 1: Login to {bsu_ip}")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    print("    -> Step 2: Navigating to Network -> IP Configuration")
    await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
    await gui_page.wait_for_timeout(1000)
    await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()

    await gui_page.wait_for_load_state("networkidle")
    await gui_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    await gui_page.wait_for_selector(NL.FALLBACK_IP, timeout=15000)
    orig_fallback = await gui_page.locator(NL.FALLBACK_IP).first.input_value()
    new_fallback = "10.0.0.11" if orig_fallback != "10.0.0.11" else "10.0.0.1"

    print(f"    -> Step 3: Changing Fallback IP to {new_fallback}")
    await gui_page.locator(NL.FALLBACK_IP).first.fill(new_fallback)

    print("    -> Step 4: [ACTION] Save -> Apply")
    await gui_page.locator(NL.SAVE_BUTTON).first.click()
    await gui_page.wait_for_timeout(3000)
    await gui_page.locator(HEADER_RED_ICON).first.click()
    await gui_page.wait_for_timeout(2000)
    await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
    await asyncio.sleep(15)

    print("    -> Step 5: Verification via SSH")
    cmd_res = (await root_ssh.send_command("uci get fallback.lan.ipaddr")).result.strip()
    ssh_fb = cmd_res.splitlines()[-1].replace("'", "")
    print(f"       [SSH Result] Fallback IP: {ssh_fb}")
    assert ssh_fb == new_fallback
    print(f"    -> [SUCCESS] Fallback IP verified in CLI.")


# =====================================================================
# GUI_55: EDIT & VERIFY FALLBACK SUBNET MASK
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_55
@pytest.mark.Network
async def test_gui_55_edit_fallback_mask(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_55: Fallback Mask Edit & Verify")
    HEADER_RED_ICON = "#header_apply"
    CONFIRM_APPLY_BTN = "input[value='Apply']"

    print(f"    -> Step 1: Login to {bsu_ip}")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    print("    -> Step 2: Navigating to Network -> IP Configuration")
    await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
    await gui_page.wait_for_timeout(1000)
    await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/ip']").first.click()

    await gui_page.wait_for_load_state("networkidle")
    await gui_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    mask_field = gui_page.locator(NL.FALLBACK_NETMASK).first
    await mask_field.wait_for(state="visible", timeout=20000)

    orig_fb_mask = await mask_field.input_value()
    new_mask = "255.255.255.0" if orig_fb_mask != "255.255.255.0" else "255.255.0.0"

    print(f"    -> Step 3: Changing Fallback Mask to {new_mask}")
    await mask_field.fill(new_mask)

    print("    -> Step 4: [ACTION] Save -> Apply")
    await gui_page.locator(NL.SAVE_BUTTON).first.click()
    await gui_page.wait_for_timeout(3000)
    await gui_page.locator(HEADER_RED_ICON).first.click()
    await gui_page.wait_for_timeout(2000)
    await gui_page.locator(CONFIRM_APPLY_BTN).first.click()
    await asyncio.sleep(15)

    print("    -> Step 5: Verification via SSH")
    cmd_res = (await root_ssh.send_command("uci get fallback.lan.netmask")).result.strip()
    ssh_mask = cmd_res.splitlines()[-1].replace("'", "")
    print(f"       [SSH Result] Fallback Mask: {ssh_mask}")
    assert ssh_mask == new_mask
    print(f"    -> [SUCCESS] Fallback Mask verified in CLI.")


# =====================================================================
# GUI_70: Ethernet Speed/Duplex Validation (State-Aware)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_70
@pytest.mark.Network
async def test_gui_70_ethernet_speed_duplex(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_70: Ethernet Speed/Duplex Validation")

    speed_map = {
        "Auto Negotiation": "0",
        "100Mbps-Full": "4",
        "1000Mbps-Full": "5"
    }
    # Reverse map to find labels from UCI values
    uci_to_label = {v: k for k, v in speed_map.items()}

    print(f"    -> Step 1: Login and Navigate to Ethernet")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    async def go_to_ethernet():
        if "/network/eth" not in gui_page.url:
            await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
            await gui_page.wait_for_timeout(1000)
            await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/eth']").first.click()
            await gui_page.wait_for_load_state("networkidle")

    await go_to_ethernet()
    tab_locator = gui_page.locator("ul.cbi-tabmenu > li > a")
    await tab_locator.first.wait_for(state="visible", timeout=15000)
    num_lans = len(await tab_locator.all())
    print(f"    -> Detected {num_lans} LAN interface(s).")

    for i in range(num_lans):
        await go_to_ethernet()
        current_tabs = await gui_page.locator("ul.cbi-tabmenu > li > a").all()
        lan_label = await current_tabs[i].inner_text()
        eth_interface = f"eth{i}"

        print(f"\n[!] Testing {lan_label} ({eth_interface})")
        await current_tabs[i].click()
        await gui_page.wait_for_timeout(2000)

        # 1. Verify Start State via CLI
        init_res = (await root_ssh.send_command(f"uci get ethernet.{eth_interface}.speed")).result.strip()
        init_uci = init_res.splitlines()[-1].replace("'", "")
        init_label = uci_to_label.get(init_uci, "Unknown")
        print(f"    -> Initial CLI State for {eth_interface}: {init_label} ({init_uci})")

        # 2. Reorder testing list: Move current mode to the END
        # This ensures we always change the mode and trigger the Apply button
        test_modes = ["Auto Negotiation", "100Mbps-Full", "1000Mbps-Full"]
        test_modes.remove(init_label)
        test_modes.append(init_label)  # Current mode is now the last one tested

        print(f"    -> Test Sequence: {' -> '.join(test_modes)}")

        for mode_label in test_modes:
            await go_to_ethernet()
            await gui_page.locator("ul.cbi-tabmenu > li > a").nth(i).click()
            await gui_page.wait_for_timeout(2000)

            target_uci = speed_map[mode_label]
            print(f"    -> Configuring {mode_label} (UCI: {target_uci})")

            # Check if UI is already set to target (Edge case)
            current_ui_val = await gui_page.locator(EL.SPEED_DROPDOWN).first.input_value()
            if current_ui_val == target_uci:
                print(f"       [Info] UI already shows {mode_label}. Skipping to ensure Red Apply trigger later.")
                continue

            # Select and Save
            await gui_page.locator(EL.SPEED_DROPDOWN).first.select_option(label=mode_label)
            await gui_page.locator(EL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(3000)

            # Standard Apply Flow
            apply_icon = gui_page.locator(NL.APPLY_ICON).first
            try:
                await apply_icon.wait_for(state="visible", timeout=5000)
                await apply_icon.click()
                await gui_page.wait_for_timeout(3000)

                confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
                await confirm_btn.wait_for(state="visible", timeout=10000)
                await confirm_btn.click()

                print(f"       Waiting 20s for commit...")
                await asyncio.sleep(20)
            except Exception:
                print(f"       [Warning] Apply buttons did not appear for {mode_label}.")

            # 3. Backend Verification
            res = (await root_ssh.send_command(f"uci get ethernet.{eth_interface}.speed")).result.strip()
            clean_res = res.splitlines()[-1].replace("'", "")
            print(f"       [CLI Verification] {eth_interface} is now: {clean_res}")
            assert clean_res == target_uci, f"Failed to verify {mode_label} on {eth_interface}"
            await asyncio.sleep(2)

    print(f"\n[+] GUI_70 Validation Complete.")


# =====================================================================
# GUI_71: Ethernet MTU Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_71
@pytest.mark.Network
async def test_gui_71_ethernet_mtu(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_71: Ethernet MTU Validation")

    print(f"    -> Step 1: Login and Navigate to Ethernet")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(3000)

    async def go_to_ethernet():
        if "/network/eth" not in gui_page.url:
            await gui_page.locator("div.sidebar li.Network > a.menu").first.click()
            await gui_page.wait_for_timeout(1000)
            await gui_page.locator("div.sidebar li.Network ul.dropdown-menu a[href*='/network/eth']").first.click()
            await gui_page.wait_for_load_state("networkidle")

    await go_to_ethernet()
    tab_locator = gui_page.locator("ul.cbi-tabmenu > li > a")
    await tab_locator.first.wait_for(state="visible", timeout=15000)
    num_lans = len(await tab_locator.all())
    print(f"    -> Detected {num_lans} LAN interface(s).")

    for i in range(num_lans):
        await go_to_ethernet()
        current_tabs = await gui_page.locator("ul.cbi-tabmenu > li > a").all()
        lan_label = await current_tabs[i].inner_text()
        eth_interface = f"eth{i}"

        print(f"\n[!] Testing MTU on {lan_label} ({eth_interface})")
        await current_tabs[i].click()
        await gui_page.wait_for_timeout(2000)

        # We will test 2 random MTU values
        test_mtus = [str(random.randint(1501, 3000)), str(random.randint(3001, 9000))]

        for target_mtu in test_mtus:
            await go_to_ethernet()
            await gui_page.locator("ul.cbi-tabmenu > li > a").nth(i).click()
            await gui_page.wait_for_timeout(2000)

            print(f"    -> Configuring MTU: {target_mtu}")

            # Fill MTU and Save
            mtu_field = gui_page.locator(EL.MTU_INPUT).first
            await mtu_field.wait_for(state="visible", timeout=10000)
            await mtu_field.fill(target_mtu)

            await gui_page.locator(EL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(3000)

            # Standard Apply Flow
            apply_icon = gui_page.locator(NL.APPLY_ICON).first
            try:
                await apply_icon.wait_for(state="visible", timeout=5000)
                await apply_icon.click()
                await gui_page.wait_for_timeout(3000)

                confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
                await confirm_btn.wait_for(state="visible", timeout=10000)
                await confirm_btn.click()

                await asyncio.sleep(10)
            except Exception:
                print(f"       [Warning] Apply buttons did not appear for MTU {target_mtu}.")

            # Backend Verification
            res = (await root_ssh.send_command(f"uci get ethernet.{eth_interface}.mtu")).result.strip()
            clean_res = res.splitlines()[-1].replace("'", "")
            print(f"       [CLI Verification] {eth_interface} MTU is now: {clean_res}")

            assert clean_res == target_mtu, f"MTU mismatch on {eth_interface}! Expected {target_mtu}, got {clean_res}"
            await asyncio.sleep(2)

    # Revert to default 1500 for cleanliness
    print(f"\n[!] Cleaning up: Reverting to MTU 1500")
    for i in range(num_lans):
        await go_to_ethernet()
        await gui_page.locator("ul.cbi-tabmenu > li > a").nth(i).click()
        await gui_page.locator(EL.MTU_INPUT).first.fill("1500")
        await gui_page.locator(EL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(2000)
        if await gui_page.locator(NL.APPLY_ICON).is_visible():
            await gui_page.locator(NL.APPLY_ICON).first.click()
            await gui_page.locator(NL.CONFIRM_APPLY).first.click()
            await asyncio.sleep(5)

    print(f"\n[+] GUI_71 MTU Validation Complete.")

# =====================================================================
# GUI_72: DHCP Server Status Validation (Radio 1)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_72
@pytest.mark.Network
async def test_gui_72_dhcp_server_status(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_72: DHCP Server Enable/Disable Validation")

    async def navigate_to_dhcp():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases")
        # 1. Click Network Menu (Check if already expanded)
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)

        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        # 2. Click DHCP / Leases Submenu
        dhcp_submenu = gui_page.locator("ul.dropdown-menu a[href*='/network/dhcp']").first
        await dhcp_submenu.wait_for(state="visible", timeout=5000)
        await dhcp_submenu.click()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(2000)

    print(f"    Login")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    # Initial Navigation
    await navigate_to_dhcp()

    # 1. Check Initial Backend State
    init_res = (await root_ssh.send_command("uci get dhcp.lan.ignore")).result.strip()
    init_status = init_res.splitlines()[-1].replace("'", "")
    if "Entry not found" in init_status:
        init_status = "0"

    current_mode = "Disabled" if init_status == "1" else "Enabled"
    print(f"    -> Initial CLI State: {current_mode} (ignore='{init_status}')")

    # 2. Define Toggle sequence
    test_sequence = ["Disable", "Enable"] if current_mode == "Enabled" else ["Enable", "Disable"]

    for mode in test_sequence:
        print(f"\n[!] Cycle: Setting DHCP to {mode}")

        if "/network/dhcp" not in gui_page.url:
            await navigate_to_dhcp()

        # Select and Save
        dropdown = gui_page.locator("select[name*='ignore']").first
        await dropdown.wait_for(state="visible", timeout=15000)

        print(f"       Action: Selecting {mode} and Saving")
        await dropdown.select_option(label=mode)
        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)

        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        if await apply_icon.is_visible(timeout=5000):
            await apply_icon.click()
            await gui_page.wait_for_timeout(5000)

            confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
            await confirm_btn.wait_for(state="visible", timeout=10000)
            await confirm_btn.click()

            print(f"       Waiting 10s for service transition...")
            await asyncio.sleep(10)

        # 4. Verification in CLI
        target_val = "1" if mode == "Disable" else "0"
        verify_res = (await root_ssh.send_command("uci get dhcp.lan.ignore")).result.strip()
        actual_val = verify_res.splitlines()[-1].replace("'", "")

        if "Entry not found" in actual_val and target_val == "0":
            actual_val = "0"

        print(f"       [CLI Verification] Result: {actual_val} (Expected: {target_val})")
        assert actual_val == target_val, f"DHCP state mismatch! Expected {mode}"

        await asyncio.sleep(2)

    print(f"\n[+] GUI_72  Complete .")


# =====================================================================
# GUI_73: DHCP Lease Time Validation (Radio 1)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_73
@pytest.mark.Network
async def test_gui_73_dhcp_lease_time(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_73: DHCP Lease Time Validation")

    async def navigate_to_dhcp():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases")
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)

        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        dhcp_submenu = gui_page.locator("ul.dropdown-menu a[href*='/network/dhcp']").first
        await dhcp_submenu.wait_for(state="visible", timeout=5000)
        await dhcp_submenu.click()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(2000)

    print(f"    -> Step 1: Login Process")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    # Initial Navigation
    await navigate_to_dhcp()

    # Get original lease time to revert later
    orig_res = (await root_ssh.send_command("uci get dhcp.lan.leasetime")).result.strip()
    orig_lease = orig_res.splitlines()[-1].replace("'", "")
    if "Entry not found" in orig_lease:
        orig_lease = "43200"
    print(f"    -> Original Lease Time: {orig_lease}")

    # Define 2 random lease time values
    test_values = [str(random.randint(120, 20000)), str(random.randint(20001, 86400))]

    try:
        for target_time in test_values:
            print(f"\n[!] Cycle: Setting DHCP Lease Time to {target_time}s")

            if "/network/dhcp" not in gui_page.url:
                await navigate_to_dhcp()

            # 1. Fill Value and Save
            lease_field = gui_page.locator("input[name*='leasetime']").first
            await lease_field.wait_for(state="visible", timeout=10000)
            await lease_field.fill(target_time)

            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(4000)

            # 2. Full Apply Flow
            apply_icon = gui_page.locator(NL.APPLY_ICON).first
            if await apply_icon.is_visible(timeout=5000):
                await apply_icon.click()
                await gui_page.wait_for_timeout(5000)

                confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
                await confirm_btn.wait_for(state="visible", timeout=10000)
                await confirm_btn.click()

                await asyncio.sleep(7)

            # 3. CLI Verification
            verify_res = (await root_ssh.send_command("uci get dhcp.lan.leasetime")).result.strip()
            actual_val = verify_res.splitlines()[-1].replace("'", "")
            print(f"       [CLI Verification] dhcp.lan.leasetime='{actual_val}'")
            assert actual_val == target_time

    finally:
        # Cleanup: Revert to original with FULL Apply sequence
        print(f"\n[!] Cleaning up: Reverting Lease Time to {orig_lease}")
        try:
            await navigate_to_dhcp()
            await gui_page.locator("input[name*='leasetime']").first.fill(orig_lease)
            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(3000)

            if await gui_page.locator(NL.APPLY_ICON).is_visible():
                await gui_page.locator(NL.APPLY_ICON).first.click()
                await gui_page.wait_for_timeout(4000)
                await gui_page.locator(NL.CONFIRM_APPLY).first.click()
                await asyncio.sleep(7)
                print(f"    -> [REVERT SUCCESS] Lease time restored to {orig_lease}")
        except Exception as e:
            print(f"    -> Revert failed: {e}")

    print(f"\n[+] GUI_73 Validation Complete.")


# =====================================================================
# GUI_74: DHCP 2.4 GHz Radio IP Configuration Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_74
@pytest.mark.Network
async def test_gui_74_radio_24_ip_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_74: 2.4 GHz Radio IP Configuration Validation")

    async def navigate_to_radio_24():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases -> 2.4 GHz Radio")
        # Step 1: Network Menu
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)
        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        # Step 2: DHCP Submenu
        await gui_page.locator("ul.dropdown-menu a[href*='/network/dhcp']").first.click()
        await gui_page.wait_for_load_state("networkidle")

        # Step 3: Select 2.4 GHz Radio Tab
        radio_tab = gui_page.locator("ul.cbi-tabmenu > li > a").filter(has_text="2.4 GHz Radio").first
        await radio_tab.wait_for(state="visible", timeout=10000)
        await radio_tab.click()
        await gui_page.wait_for_timeout(2000)

    print(f"    -> Step 1: Login Process")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    await navigate_to_radio_24()

    # Get original IP to revert later
    orig_res = (await root_ssh.send_command("uci get network.lan24.ipaddr")).result.strip()
    orig_ip = orig_res.splitlines()[-1].replace("'", "")
    print(f"    -> Original 2.4GHz IP: {orig_ip}")

    # Generate a random IP (using your logic from GUI_51)
    new_ip = generate_test_ip(orig_ip, "v4")

    try:
        print(f"\n[!] Configuring New IP: {new_ip}")

        # 1. Fill and Save
        ip_field = gui_page.locator("input[name*='lan24.ipaddr']").first
        await ip_field.wait_for(state="visible", timeout=10000)
        await ip_field.fill(new_ip)

        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)

        # 2. Full Apply Flow
        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        if await apply_icon.is_visible(timeout=5000):
            await apply_icon.click()
            await gui_page.wait_for_timeout(5000)

            confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
            await confirm_btn.wait_for(state="visible", timeout=10000)
            await confirm_btn.click()

            print(f"       Waiting 20s for IP application...")
            await asyncio.sleep(20)

        # 3. CLI Verification
        verify_res = (await root_ssh.send_command("uci get network.lan24.ipaddr")).result.strip()
        actual_val = verify_res.splitlines()[-1].replace("'", "")
        print(f"       [CLI Verification] network.lan24.ipaddr='{actual_val}'")
        assert actual_val == new_ip, f"IP mismatch! Expected {new_ip}"

    finally:
        # Cleanup: Revert to original with FULL Apply sequence
        print(f"\n[!] Cleaning up: Reverting 2.4GHz IP to {orig_ip}")
        try:
            await navigate_to_radio_24()
            await gui_page.locator("input[name*='lan24.ipaddr']").first.fill(orig_ip)
            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(3000)

            if await gui_page.locator(NL.APPLY_ICON).is_visible():
                await gui_page.locator(NL.APPLY_ICON).first.click()
                await gui_page.wait_for_timeout(4000)
                await gui_page.locator(NL.CONFIRM_APPLY).first.click()
                await asyncio.sleep(10)
                print(f"    -> [REVERT SUCCESS] IP restored to {orig_ip}")
        except Exception as e:
            print(f"    -> Revert failed: {e}")

    print(f"\n[+] GUI_74 Validation Complete.")


# =====================================================================
# GUI_75: DHCP 2.4 GHz Radio Subnet Mask Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_75
@pytest.mark.Network
async def test_gui_75_radio_24_mask_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_75: 2.4 GHz Radio Subnet Mask Validation")

    async def navigate_to_radio_24():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases -> 2.4 GHz Radio")
        # Step 1: Network Menu
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)
        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        # Step 2: DHCP Submenu
        await gui_page.locator("ul.dropdown-menu a[href*='/network/dhcp']").first.click()
        await gui_page.wait_for_load_state("networkidle")

        # Step 3: Select 2.4 GHz Radio Tab
        radio_tab = gui_page.locator("ul.cbi-tabmenu > li > a").filter(has_text="2.4 GHz Radio").first
        await radio_tab.wait_for(state="visible", timeout=10000)
        await radio_tab.click()
        await gui_page.wait_for_timeout(2000)

    print(f"    -> Step 1: Login Process")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    await navigate_to_radio_24()

    # Get original Mask to revert later
    orig_res = (await root_ssh.send_command("uci get network.lan24.netmask")).result.strip()
    orig_mask = orig_res.splitlines()[-1].replace("'", "")
    print(f"    -> Original 2.4GHz Subnet Mask: {orig_mask}")

    # Determine a new mask to test (Toggle between 255.255.255.0 and 255.255.0.0)
    new_mask = "255.255.0.0" if orig_mask == "255.255.255.0" else "255.255.255.0"

    try:
        print(f"\n[!] Configuring New Mask: {new_mask}")

        # 1. Fill and Save
        mask_field = gui_page.locator("input[name*='lan24.netmask']").first
        await mask_field.wait_for(state="visible", timeout=10000)
        await mask_field.fill(new_mask)

        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)

        # 2. Full Apply Flow (Save -> Red Apply -> Apply)
        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        if await apply_icon.is_visible(timeout=5000):
            await apply_icon.click()
            await gui_page.wait_for_timeout(5000)

            confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
            await confirm_btn.wait_for(state="visible", timeout=10000)
            await confirm_btn.click()

            await asyncio.sleep(10)

        # 3. CLI Verification
        verify_res = (await root_ssh.send_command("uci get network.lan24.netmask")).result.strip()
        actual_val = verify_res.splitlines()[-1].replace("'", "")
        print(f"       [CLI Verification] network.lan24.netmask='{actual_val}'")
        assert actual_val == new_mask, f"Mask mismatch! Expected {new_mask}"

    finally:
        # Cleanup: Revert to original with FULL Apply sequence
        print(f"\n[!] Cleaning up: Reverting 2.4GHz Mask to {orig_mask}")
        try:
            if "/network/dhcp" not in gui_page.url:
                await navigate_to_radio_24()
            else:
                # Ensure correct tab is clicked if already on page
                await gui_page.locator("ul.cbi-tabmenu > li > a").filter(has_text="2.4 GHz Radio").first.click()

            await gui_page.locator("input[name*='lan24.netmask']").first.fill(orig_mask)
            await gui_page.locator(NL.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(3000)

            if await gui_page.locator(NL.APPLY_ICON).is_visible():
                await gui_page.locator(NL.APPLY_ICON).first.click()
                await gui_page.wait_for_timeout(4000)
                await gui_page.locator(NL.CONFIRM_APPLY).first.click()
                await asyncio.sleep(10)
                print(f"    -> [REVERT SUCCESS] Mask restored to {orig_mask}")
        except Exception as e:
            print(f"    -> Revert failed: {e}")

    print(f"\n[+] GUI_75 Validation Complete.")


# =====================================================================
# GUI_76: DHCP 2.4 GHz Radio DHCP Server Status Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_76
@pytest.mark.Network
async def test_gui_76_radio_24_dhcp_status(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_76: 2.4 GHz Radio DHCP Server Enable/Disable Validation")

    async def navigate_to_radio_24():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases -> 2.4 GHz Radio")
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)
        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        await gui_page.locator("ul.dropdown-menu a[href*='/network/dhcp']").first.click()
        await gui_page.wait_for_load_state("networkidle")

        radio_tab = gui_page.locator("ul.cbi-tabmenu > li > a").filter(has_text="2.4 GHz Radio").first
        await radio_tab.wait_for(state="visible", timeout=10000)
        await radio_tab.click()
        await gui_page.wait_for_timeout(2000)

    print(f"    -> Step 1: Login Process")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    await navigate_to_radio_24()

    # 1. Check Initial Backend State via CLI
    # ignore='1' is Disable, ignore='0' or missing is Enable
    init_res = (await root_ssh.send_command("uci get dhcp.lan24.ignore")).result.strip()
    init_status = init_res.splitlines()[-1].replace("'", "")

    if "Entry not found" in init_status:
        init_status = "0"

    current_mode = "Disable" if init_status == "1" else "Enable"
    print(f"    -> Initial CLI State: {current_mode} (ignore='{init_status}')")

    # 2. Define Toggle sequence
    test_sequence = ["Disable", "Enable"] if current_mode == "Enable" else ["Enable", "Disable"]

    for mode in test_sequence:
        print(f"\n[!] Cycle: Setting 2.4GHz DHCP Server to {mode}")

        if "/network/dhcp" not in gui_page.url:
            await navigate_to_radio_24()
        else:
            # Ensure we are on the correct tab
            await gui_page.locator("ul.cbi-tabmenu > li > a").filter(has_text="2.4 GHz Radio").first.click()

        # Locate dropdown
        dropdown = gui_page.locator("select[name*='lan24.ignore']").first
        await dropdown.wait_for(state="visible", timeout=15000)

        print(f"       Action: Selecting {mode} and Saving")
        await dropdown.select_option(label=mode)
        await gui_page.locator(NL.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)

        # 3. Full Apply Flow (Save -> Red Apply -> Apply)
        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        if await apply_icon.is_visible(timeout=5000):
            await apply_icon.click()
            await gui_page.wait_for_timeout(5000)

            confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
            await confirm_btn.wait_for(state="visible", timeout=10000)
            await confirm_btn.click()
            await asyncio.sleep(10)

        # 4. Verification in CLI
        target_val = "1" if mode == "Disable" else "0"
        verify_res = (await root_ssh.send_command("uci get dhcp.lan24.ignore")).result.strip()
        actual_val = verify_res.splitlines()[-1].replace("'", "")

        if "Entry not found" in actual_val and target_val == "0":
            actual_val = "0"

        print(f"       [CLI Verification] Result: {actual_val} (Expected: {target_val})")
        assert actual_val == target_val, f"2.4GHz DHCP state mismatch! Expected {mode}"

        await asyncio.sleep(2)

    print(f"\n[+] GUI_76 Validation Complete.")


# =====================================================================
# GUI_77: DHCP 2.4 GHz Radio Start & End IP Pool Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_77
@pytest.mark.Network
async def test_gui_77_radio_24_pool_range(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_77: 2.4 GHz Radio Start/End IP Validation")

    async def navigate_to_radio_24():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases -> 2.4 GHz Radio")
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)

        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        await gui_page.locator(DHCPLocators.SUBMENU_DHCP).first.click()
        await gui_page.wait_for_load_state("networkidle")

        radio_tab = gui_page.locator(DHCPLocators.TAB_RADIO_24).first
        await radio_tab.wait_for(state="visible", timeout=10000)
        await radio_tab.click()
        await gui_page.wait_for_timeout(2000)

    print(f"    -> Step 1: Login Process")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    await navigate_to_radio_24()

    # 1. Fetch Original Full IPs via CLI
    o_start_res = await root_ssh.send_command("uci get dhcp.lan24.start")
    o_start = o_start_res.result.strip().splitlines()[-1].replace("'", "")

    o_limit_res = await root_ssh.send_command("uci get dhcp.lan24.limit")
    o_limit = o_limit_res.result.strip().splitlines()[-1].replace("'", "")

    print(f"    -> Original Range: Start={o_start}, End={o_limit}")
    prefix = ".".join(o_start.split(".")[:-1])
    n_start = f"{prefix}.{random.randint(101, 145)}"
    n_limit = f"{prefix}.{random.randint(146, 250)}"

    try:
        print(f"\n[!] Step 2: Configuring New IPs: Start={n_start}, End={n_limit}")

        # Fill Full Start IP
        start_input = gui_page.locator(DHCPLocators.RADIO_24_START_IP).first
        await start_input.wait_for(state="visible", timeout=10000)
        await start_input.fill(n_start)

        # Fill Full End IP
        end_input = gui_page.locator(DHCPLocators.RADIO_24_END_IP).first
        await end_input.fill(n_limit)

        # Save
        await gui_page.locator(DHCPLocators.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)

        # 3. Apply Flow
        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        if await apply_icon.is_visible(timeout=5000):
            await apply_icon.click()
            await gui_page.wait_for_timeout(6000)

            confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
            await confirm_btn.wait_for(state="visible", timeout=15000)
            await confirm_btn.click()

            await asyncio.sleep(10)

        # 4. Verification
        v_start = (await root_ssh.send_command("uci get dhcp.lan24.start")).result.strip().splitlines()[-1].replace("'",
                                                                                                                    "")
        v_limit = (await root_ssh.send_command("uci get dhcp.lan24.limit")).result.strip().splitlines()[-1].replace("'",
                                                                                                                    "")

        print(f"       [CLI Verification] Start: {v_start} | End: {v_limit}")
        assert v_start == n_start and v_limit == n_limit, "Full IP range mismatch in backend!"

    finally:
        # 5. Full Reversion
        print(f"\n[!] Step 3: Cleaning up: Reverting to Original Start={o_start}, End={o_limit}")
        try:
            if "/network/dhcp" not in gui_page.url:
                await navigate_to_radio_24()

            await gui_page.locator(DHCPLocators.RADIO_24_START_IP).first.fill(o_start)
            await gui_page.locator(DHCPLocators.RADIO_24_END_IP).first.fill(o_limit)

            await gui_page.locator(DHCPLocators.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(4000)

            if await gui_page.locator(NL.APPLY_ICON).is_visible():
                await gui_page.locator(NL.APPLY_ICON).first.click()
                await gui_page.wait_for_timeout(6000)
                await gui_page.locator(NL.CONFIRM_APPLY).first.click()
                await asyncio.sleep(15)
                print("    -> [REVERT SUCCESS] Original IPs restored.")
        except Exception as e:
            print(f"    -> Revert failed: {e}")

    print(f"\n[+] GUI_77 Validation Complete.")


# =====================================================================
# GUI_78: DHCP 2.4 GHz Radio Lease Time Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_78
@pytest.mark.Network
async def test_gui_78_radio_24_lease_time(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_78: 2.4 GHz Radio Lease Time Validation")

    async def navigate_to_radio_24():
        print("    -> GUI Flow: Clicking Network -> DHCP / Leases -> 2.4 GHz Radio")
        network_menu = gui_page.locator("div.sidebar li.Network > a.menu").first
        await network_menu.wait_for(state="visible", timeout=10000)

        is_expanded = await network_menu.evaluate("el => el.parentElement.classList.contains('active')")
        if not is_expanded:
            await network_menu.click()
            await gui_page.wait_for_timeout(1000)

        await gui_page.locator(DHCPLocators.SUBMENU_DHCP).first.click()
        await gui_page.wait_for_load_state("networkidle")

        radio_tab = gui_page.locator(DHCPLocators.TAB_RADIO_24).first
        await radio_tab.wait_for(state="visible", timeout=10000)
        await radio_tab.click()
        await gui_page.wait_for_timeout(2000)

    # 1. Login Process
    print(f"    -> Step 1: Login Process")
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    await navigate_to_radio_24()

    # 2. Fetch Original Value
    orig_res = await root_ssh.send_command("uci get dhcp.lan24.leasetime")
    orig_lease = orig_res.result.strip().splitlines()[-1].replace("'", "")
    if "Entry not found" in orig_lease:
        orig_lease = "300"
    print(f"    -> Original Lease Time: {orig_lease}")

    # 3. Configure New Value
    target_time = str(random.randint(120, 86400))
    print(f"\n[!] Configuring 2.4GHz Lease Time to {target_time}s")

    try:
        # Edit
        lease_input = gui_page.locator("input[name*='lan24.leasetime']").first
        await lease_input.fill(target_time)
        await gui_page.locator(DHCPLocators.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)

        # 4. Apply Flow
        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        if await apply_icon.is_visible(timeout=5000):
            await apply_icon.click()
            await gui_page.wait_for_timeout(5000)

            confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
            await confirm_btn.wait_for(state="visible", timeout=10000)
            await confirm_btn.click()

            await asyncio.sleep(7)

        # 5. CLI Verification
        verify_res = await root_ssh.send_command("uci get dhcp.lan24.leasetime")
        actual_val = verify_res.result.strip().splitlines()[-1].replace("'", "")
        print(f"       [CLI Verification] Result: {actual_val}")
        assert actual_val == target_time

    finally:
        # 6. Revert
        print(f"\n[!] Step 4: Reverting to Original Lease Time={orig_lease}")
        try:
            await gui_page.wait_for_timeout(2000)
            await navigate_to_radio_24()

            await gui_page.locator("input[name*='lan24.leasetime']").first.fill(orig_lease)
            await gui_page.locator(DHCPLocators.SAVE_BUTTON).first.click()
            await gui_page.wait_for_timeout(3000)

            if await gui_page.locator(NL.APPLY_ICON).is_visible():
                await gui_page.locator(NL.APPLY_ICON).first.click()
                await gui_page.wait_for_timeout(4000)
                await gui_page.locator(NL.CONFIRM_APPLY).first.click()
                await asyncio.sleep(7)
                print("    -> [REVERT SUCCESS]")
        except Exception as e:
            print(f"    -> Revert failed: {e}")

    print(f"\n[+] GUI_78 Validation Complete.")