import pytest
import random
import asyncio
import re
from pages.locators import NetworkLocators as NL
from pages.commands import RootCommands
from utils.parsers import generate_test_ip
from pages.locators import LoginPageLocators

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