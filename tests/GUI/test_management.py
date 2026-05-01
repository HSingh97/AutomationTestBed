import pytest
import random
import asyncio
from pages.locators import LoginPageLocators, NetworkLocators as NL, ManagementLocators as ML
from pages.commands import RootCommands
from datetime import datetime

# Standard markers
pytestmark = [pytest.mark.sanity, pytest.mark.Management]

# =====================================================================
# GUI_88: System Timezone Configuration Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_88
@pytest.mark.Management
async def test_gui_88_timezone_random(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_88: Random System Timezone Validation")

    # --- Step 1: Login ---
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    # --- Step 2: Capture Current TZ from CLI ---
    res = await root_ssh.send_command(RootCommands.GET_TIMEZONE)
    current_tz = res.result.strip().splitlines()[-1].replace("'", "")
    print(f"    -> [CLI CURRENT]: {current_tz}")

    # --- Step 3: Navigate Management -> System ---
    print("    -> Action: Navigating Management -> System")

    # Check if Management sidebar is already expanded
    mgmt_menu = gui_page.locator("div.sidebar li.Management").first
    if "active" not in (await mgmt_menu.get_attribute("class") or ""):
        await gui_page.locator("div.sidebar li.Management > a.menu").first.click()
        await gui_page.wait_for_timeout(1500)

    await gui_page.locator("li.Management ul.dropdown-menu a").filter(has_text="System").first.click()
    await gui_page.wait_for_load_state("networkidle")
    await gui_page.wait_for_timeout(3000)

    # --- Step 4: Pick Random Timezone ---
    dropdown = gui_page.locator(ML.TIMEZONE_DROPDOWN).first
    await dropdown.scroll_into_view_if_needed()

    # Correct Playwright method to get all option values
    options = await dropdown.locator("option").evaluate_all("nodes => nodes.map(n => n.value)")

    # Filter out empty options and current TZ
    valid_choices = [opt for opt in options if opt and opt != current_tz]
    selected_val = random.choice(valid_choices)

    print(f"    -> Action: Selecting Random TZ: {selected_val}")
    await dropdown.select_option(value=selected_val)
    await gui_page.wait_for_timeout(2000)

    # --- Step 5: Trinity Apply ---
    print("       Action: [Save -> Red Apply -> Apply]")
    await gui_page.locator(ML.SAVE_BUTTON).first.click()
    await gui_page.wait_for_timeout(5000)
    await gui_page.locator(NL.APPLY_ICON).first.click()
    await gui_page.wait_for_timeout(5000)
    await gui_page.locator(NL.CONFIRM_APPLY).first.click()

    print("       Waiting 25s for system commitment...")
    await asyncio.sleep(25)

    # --- Step 6: Final Backend Verify ---
    verify_res = await root_ssh.send_command(RootCommands.GET_TIMEZONE)
    actual_tz = verify_res.result.strip().splitlines()[-1].replace("'", "")

    print(f"       [CLI VERIFY]: {actual_tz}")

    # Handle the backend alias for UTC
    if selected_val == "UTC" and actual_tz == "GMT0":
        assert True
    else:
        assert actual_tz == selected_val, f"Mismatch! Expected {selected_val}, got {actual_tz}"

    print(f"\n[+] GUI_88: Successful.")

# =====================================================================
# GUI_89: NTP Server Full Cycle with ntpd Backend Verification
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_89
@pytest.mark.Management
async def test_gui_89_ntp_full_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_89: NTP Host Verification")

    # XPaths provided by User
    XPATH_ADD_INPUT = '//*[@id="ntp_addr"]'
    XPATH_ADD_BTN = '//*[@id="addserver"]/div/input[2]'
    XPATH_ANY_DELETE = '//input[@value="Delete"]'

    async def navigate_to_system():
        await gui_page.locator("div.sidebar li.Management > a.menu").first.click()
        await gui_page.wait_for_timeout(2000)
        await gui_page.locator("li.Management ul.dropdown-menu a").filter(has_text="System").first.click()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(3000)

    async def apply_trinity_flow():
        await gui_page.locator(ML.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(5000)

        apply_icon = gui_page.locator(NL.APPLY_ICON).first
        await apply_icon.wait_for(state="visible", timeout=10000)
        await apply_icon.click()
        await gui_page.wait_for_timeout(5000)

        confirm_btn = gui_page.locator(NL.CONFIRM_APPLY).first
        await confirm_btn.wait_for(state="visible", timeout=10000)
        await confirm_btn.click()

        print("       Waiting 20s for System commitment...")
        await asyncio.sleep(20)

    # --- Step 1: Login ---
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    # --- Step 2: Delete All Existing rows in GUI ---
    print("\n[!] Step 1: Clearing GUI list (Delete all rows)")
    await navigate_to_system()

    del_btns = gui_page.locator(XPATH_ANY_DELETE)
    count = await del_btns.count()
    if count > 0:
        print(f"       Detected {count} servers. Clicking delete for all...")
        for _ in range(count):
            await gui_page.locator(XPATH_ANY_DELETE).first.click()
            await gui_page.wait_for_timeout(1000)
        await apply_trinity_flow()
    else:
        print("       No servers in GUI. Ready for addition.")

    # --- Step 3: Incremental Addition (1 to 4) ---
    for i in range(1, 5):
        # Generate random server name for this step
        random_srv = f"test-srv-{i}-{random.randint(10, 99)}.google.com"
        print(f"\n[!] Step 2.{i}: Adding Server: {random_srv}")

        # Navigate and Add
        await navigate_to_system()
        await gui_page.locator(XPATH_ADD_INPUT).fill(random_srv)
        await gui_page.locator(XPATH_ADD_BTN).click()
        await gui_page.wait_for_timeout(2000)

        # Trinity Apply Flow
        await apply_trinity_flow()

        # CLI Verification specifically checking the ntpd host indices
        print(f"       [CLI VERIFY] Checking ntpd.@server[{i}].host...")
        # Index i is used because @server[0] is typically reserved for GPS (127.127.28.0)
        verify_cmd = f"uci get ntpd.@server[{i}].host"
        res = await root_ssh.send_command(verify_cmd)

        backend_host = res.result.strip().replace("'", "")
        print(f"       Backend reported: {backend_host}")

        assert backend_host == random_srv, f"Verification failed! Expected {random_srv}, got {backend_host}"
        print(f"       [SUCCESS] Server {i} verified in CLI.")

    print("\n[!] Step 3: Verifying Max Limit (4) Enforcement")
    await navigate_to_system()

    add_bar = gui_page.locator(XPATH_ADD_INPUT)
    is_visible = await add_bar.is_visible()

    if not is_visible:
        print("       [VERIFIED] Add bar hidden. Max limit of 4 enforced.")
    else:
        is_disabled = await add_bar.is_disabled()
        assert is_disabled, "FAIL: Add bar still active after 4 servers configured!"
        print("       [VERIFIED] Add bar is disabled. Max limit of 4 enforced.")

    print(f"\n[+] GUI_89 Passed")


# =====================================================================
# GUI_90: Sync with Browser Time Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_90
@pytest.mark.Management
async def test_gui_90_sync_with_browser(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_90: Sync with Browser Time Validation")

    # XPaths
    XPATH_TIMEZONE_SELECT = '//*[@id="maincontent"]/div/div[1]/fieldset/form/div[2]/div/select'
    XPATH_SYNC_BTN = '//*[@id="maincontent"]/div/div[2]/input[1]'
    TECHNICAL_IST = "IST-5:30"

    async def navigate_to_system_general():
        mgmt_menu = gui_page.locator("div.sidebar li.Management > a.menu").first
        await mgmt_menu.wait_for(state="visible", timeout=10000)
        await mgmt_menu.click()
        await gui_page.wait_for_timeout(1500)

        system_sub = gui_page.locator("li.Management ul.dropdown-menu a").filter(has_text="System").first
        await system_sub.wait_for(state="visible", timeout=10000)
        await system_sub.click()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(2000)

    async def apply_trinity_flow():
        await gui_page.locator(ML.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)
        await gui_page.locator(NL.APPLY_ICON).first.click()
        await gui_page.wait_for_timeout(4000)
        await gui_page.locator(NL.CONFIRM_APPLY).first.click()
        await asyncio.sleep(20)

    # --- Step 1: Login ---
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(4000)

    # --- Step 2: Initial CLI Check ---
    res_start = await root_ssh.send_command("date; uci get system.@system[0].timezone")
    print(f"\n[!] Step 1: Pre-Check State:\n{res_start.result.strip()}")

    await navigate_to_system_general()

    # --- Step 3: Induction Logic ---
    dropdown = gui_page.locator(XPATH_TIMEZONE_SELECT)
    await dropdown.wait_for(state="visible")
    current_val = await dropdown.evaluate("el => el.value")

    if TECHNICAL_IST in current_val:
        all_values = await dropdown.locator("option").evaluate_all("options => options.map(o => o.value)")
        random_val = random.choice([v for v in all_values if v != current_val and v != ""])
        print(f"\n[!] Step 2: Shifting from IST to Random TZ ({random_val})")
        await dropdown.select_option(value=random_val)
        await apply_trinity_flow()

        res_check_mid = await root_ssh.send_command("date")
        print(f"    -> [CHECK] Local Time during Induction: {res_check_mid.result.strip()}")

        await navigate_to_system_general()
        print(f"\n[!] Step 3: Setting back to {TECHNICAL_IST}")
        await dropdown.select_option(value=TECHNICAL_IST)
        await apply_trinity_flow()
    else:
        print(f"\n[!] Device not in IST. Configuring to IST first.")
        await dropdown.select_option(value=TECHNICAL_IST)
        await apply_trinity_flow()

    # --- Step 4: Sync with Browser ---
    await navigate_to_system_general()

    # Capture local time right before clicking sync
    pc_time_now = datetime.now()
    pc_total_minutes = pc_time_now.hour * 60 + pc_time_now.minute

    print(f"\n[!] Step 4: Clicking 'Sync with Browser'")
    await gui_page.locator(XPATH_SYNC_BTN).click()
    await gui_page.wait_for_timeout(2000)

    print("    -> Action: Refreshing page to verify...")
    await gui_page.reload()
    await gui_page.wait_for_timeout(3000)

    # --- Step 5: Final Verification with Tolerance ---
    res_final = await root_ssh.send_command("date '+%H:%M'; uci get system.@system[0].timezone")
    lines = res_final.result.strip().splitlines()

    backend_time_str = lines[0]  # e.g. "16:04"
    backend_tz = lines[1]

    # Convert backend time to minutes for comparison
    b_hour, b_min = map(int, backend_time_str.split(':'))
    backend_total_minutes = b_hour * 60 + b_min

    print(f"    -> [PC/Browser Time]: {pc_time_now.strftime('%H:%M')}")
    print(f"    -> [Device Backend ]: {backend_time_str}")
    print(f"    -> [Device Timezone]: {backend_tz}")

    # Calculate absolute difference
    time_diff = abs(pc_total_minutes - backend_total_minutes)

    assert time_diff <= 1, f"Sync Mismatch! Difference is {time_diff} minutes (Allowed: 1)"
    print(f"\n[+] GUI_90 Passed: Sync verified ")


# =====================================================================
# GUI_91: System Log Server IP & Port Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_91
@pytest.mark.Management
async def test_gui_91_logging_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_91: System Log Server Configuration Validation")

    # XPaths provided by User
    XPATH_LOGGING_TAB = '//*[@id="maincontent"]/div/ul/li[2]/a'
    XPATH_LOG_IP = '//*[@id="syslog_ip"]/div/input'
    XPATH_LOG_PORT = '//*[@id="syslog_port"]/div/input'

    async def navigate_to_logging():
        # 1. Expand Management
        mgmt_menu = gui_page.locator("div.sidebar li.Management > a.menu").first
        await mgmt_menu.wait_for(state="visible", timeout=10000)
        await mgmt_menu.click()
        await gui_page.wait_for_timeout(2000)

        # 2. Click System Submenu
        system_sub = gui_page.locator("li.Management ul.dropdown-menu a").filter(has_text="System").first
        await system_sub.wait_for(state="visible", timeout=10000)
        await system_sub.click()
        await gui_page.wait_for_load_state("networkidle")

        # 3. Click Logging Tab
        logging_tab = gui_page.locator(XPATH_LOGGING_TAB)
        await logging_tab.wait_for(state="visible", timeout=10000)
        await logging_tab.click()
        await gui_page.wait_for_timeout(2000)

    async def apply_trinity_flow():
        # Click Save
        await gui_page.locator(ML.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(5000)

        # Click Red Save Icon
        await gui_page.locator(NL.APPLY_ICON).first.click()
        await gui_page.wait_for_timeout(5000)

        # Click final Apply
        await gui_page.locator(NL.CONFIRM_APPLY).first.click()
        print("       Waiting 25s for logging services to restart...")
        await asyncio.sleep(25)

    # --- Step 1: Login ---
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    # --- Step 2: Initial CLI Check & Capture ---
    print("\n[!] Step 1: Capturing initial backend state")
    res_ip = await root_ssh.send_command("uci get system.@system[0].log_ip")
    res_port = await root_ssh.send_command("uci get system.@system[0].log_port")

    orig_ip = res_ip.result.strip() if "Entry not found" not in res_ip.result else ""
    orig_port = res_port.result.strip() if "Entry not found" not in res_port.result else "514"

    print(f"    -> [CLI PRE-CHECK] IP: '{orig_ip}', Port: '{orig_port}'")

    try:
        # --- Step 3: Configure Random IP and Port ---
        await navigate_to_logging()
        test_ip = f"192.168.1.{random.randint(2, 254)}"
        test_port = str(random.randint(500, 600))

        print(f"\n[!] Step 2: Configuring Test IP: {test_ip} and Port: {test_port}")
        await gui_page.locator(XPATH_LOG_IP).fill(test_ip)
        await gui_page.locator(XPATH_LOG_PORT).fill(test_port)

        await apply_trinity_flow()

        # --- Step 4: Verify in Backend ---
        print("\n[!] Step 3: Verifying changes in Backend CLI")
        verify_ip = await root_ssh.send_command("uci get system.@system[0].log_ip")
        verify_port = await root_ssh.send_command("uci get system.@system[0].log_port")

        actual_ip = verify_ip.result.strip()
        actual_port = verify_port.result.strip()

        print(f"    -> [CLI VERIFY] IP: {actual_ip}, Port: {actual_port}")
        assert actual_ip == test_ip, f"IP Mismatch! Expected {test_ip}, got {actual_ip}"
        assert actual_port == test_port, f"Port Mismatch! Expected {test_port}, got {actual_port}"
        print("       [SUCCESS] Backend matches GUI configuration.")

    finally:
        # --- Step 5: Revert to Original ---
        print(f"\n[!] Step 4: Cleaning up - Reverting to original state")
        try:
            # Re-login if session timed out during sleep
            await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
            await gui_page.wait_for_timeout(3000)

            if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
                await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
                await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
                await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")

            await navigate_to_logging()

            print(f"       Restoring IP: '{orig_ip}' Port: '{orig_port}'")
            await gui_page.locator(XPATH_LOG_IP).fill(orig_ip)
            await gui_page.locator(XPATH_LOG_PORT).fill(orig_port)

            await apply_trinity_flow()
            print("    -> Cleanup Complete.")
        except Exception as e:
            print(f"    -> Cleanup failed: {e}")

    print(f"\n[+] GUI_91 Validation Complete.")


# =====================================================================
# GUI_92: Temperature Log  Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_92
@pytest.mark.Management
async def test_gui_92_temp_logging_cycle(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_92: Temperature Log Validation")

    # XPaths
    XPATH_LOGGING_TAB = '//*[@id="maincontent"]/div/ul/li[2]/a'
    XPATH_TEMP_STATUS = '//*[@id="temlog_status"]'
    XPATH_TEMP_INTERVAL = '//*[@id="templog_int"]/div/input'

    async def navigate_to_logging_page():
        await gui_page.locator("div.sidebar li.Management > a.menu").first.click()
        await gui_page.wait_for_timeout(1500)
        await gui_page.locator("li.Management ul.dropdown-menu a").filter(has_text="System").first.click()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.locator(XPATH_LOGGING_TAB).click()
        await gui_page.wait_for_timeout(2000)

    async def apply_trinity_flow():
        await gui_page.locator(ML.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(4000)
        await gui_page.locator(NL.APPLY_ICON).first.click()
        await gui_page.wait_for_timeout(4000)
        await gui_page.locator(NL.CONFIRM_APPLY).first.click()
        await asyncio.sleep(20)

    # --- Step 1: Login ---
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(4000)

    # --- Step 2: Initial Check ---
    res_status = await root_ssh.send_command("uci get system.@system[0].templogstatus")
    orig_status = res_status.result.strip()
    print(f"    -> [CLI PRE-CHECK] Current Status: {orig_status}")

    await navigate_to_logging_page()

    if orig_status == "1":
        print("\n[!] Case 1: Currently Enabled. Disabling first...")
        await gui_page.locator(XPATH_TEMP_STATUS).select_option(label="Disable")
        await apply_trinity_flow()
        # Verify Disable in Backend
        check_dis = await root_ssh.send_command("uci get system.@system[0].templogstatus")
        assert check_dis.result.strip() == "0", "Failed to disable status in backend!"

        # Now Enable it for the 1-min test
        await navigate_to_logging_page()
        print("    -> Re-enabling for 1-minute interval test...")
        await gui_page.locator(XPATH_TEMP_STATUS).select_option(label="Enable")
        await gui_page.locator(XPATH_TEMP_INTERVAL).fill("1")
        await apply_trinity_flow()
    else:
        print("\n[!] Case 2: Currently Disabled. Enabling and setting 1-min interval...")
        await gui_page.locator(XPATH_TEMP_STATUS).select_option(label="Enable")
        await gui_page.locator(XPATH_TEMP_INTERVAL).fill("1")
        await apply_trinity_flow()

    # Verify Enabled in Backend
    check_en = await root_ssh.send_command("uci get system.@system[0].templogstatus")
    assert check_en.result.strip() == "1", "Failed to enable status in backend!"

    # --- Step 4: 3-Minute CLI Monitoring ---
    print("\n[!] Step 4: Monitoring /tmp/temp-log for 3 minutes...")

    previous_line_count = 0
    for m in range(1, 4):
        print(f"    -> Minute {m}: Waiting 65 seconds for log entry...")
        await asyncio.sleep(65)  # Wait slightly more than a minute

        res_log = await root_ssh.send_command("cat /tmp/temp-log | wc -l")
        current_line_count = int(res_log.result.strip())

        log_content = await root_ssh.send_command("tail -n 1 /tmp/temp-log")
        print(f"       [LOG ENTRY]: {log_content.result.strip()}")

        assert current_line_count > previous_line_count, f"Minute {m}: No new log entry detected!"
        previous_line_count = current_line_count

    print("\n[!] Step 5: Reverting interval to 30 (Status stays Enabled)")
    await navigate_to_logging_page()
    await gui_page.locator(XPATH_TEMP_INTERVAL).fill("30")
    await apply_trinity_flow()

    final_int = await root_ssh.send_command("uci get system.@system[0].temploginterval")
    assert final_int.result.strip() == "30", "Final interval revert failed!"

    print(f"\n[+] GUI_92 Complete")


# =====================================================================
# GUI_93:System Location Parameters Validation
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_93
@pytest.mark.Management
async def test_gui_93_location_config(root_ssh, gui_page, bsu_ip, device_creds):
    print(f"\n[+] Starting GUI_93: System Location Parameters Validation")

    # Field XPaths
    XPATH_SYS_NAME = '//*[@id="cusname"]/div/input'
    XPATH_LOCATION = '//*[@id="cusloc"]/div/input'
    XPATH_EMAIL = '//*[@id="cusemail"]/div/input'
    XPATH_PHONE = '//*[@id="cusphone"]/div/input'
    XPATH_DISTANCE = '//*[@id="maincontent"]/div/div[1]/fieldset/form/div[8]/div/select'

    async def navigate_to_location_tab():

        # 1. Expand Management sidebar only if it's not already expanded
        mgmt_menu_parent = gui_page.locator("div.sidebar li.Management").first
        class_attribute = await mgmt_menu_parent.get_attribute("class") or ""

        if "active" not in class_attribute:
            print("       (Expanding Sidebar)")
            await gui_page.locator("div.sidebar li.Management > a.menu").first.click()
            await gui_page.wait_for_timeout(1500)
        else:
            print("       (Sidebar already expanded)")

        # 2. Click System submenu
        await gui_page.locator("li.Management ul.dropdown-menu a").filter(has_text="System").first.click()
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(3000)

        # 3. Click "Location" link text
        print("    -> Action: Clicking 'Location' Tab")
        loc_link = gui_page.get_by_role("link", name="Location", exact=True)
        await loc_link.wait_for(state="visible", timeout=2000)
        await loc_link.click()
        await gui_page.wait_for_timeout(2000)

    async def apply_trinity_flow():
        await gui_page.locator(ML.SAVE_BUTTON).first.click()
        await gui_page.wait_for_timeout(5000)
        await gui_page.locator(NL.APPLY_ICON).first.click()
        await gui_page.wait_for_timeout(5000)
        await gui_page.locator(NL.CONFIRM_APPLY).first.click()
        await asyncio.sleep(15)

    async def verify_backend(expected, label):
        print(f"       [CLI VERIFY - {label}] Checking UCI values...")
        actual = {
            "name": (await root_ssh.send_command("uci get advwireless.ath.customername")).result.strip(),
            "loc": (await root_ssh.send_command("uci get system.@system[0].location")).result.strip(),
            "email": (await root_ssh.send_command("uci get system.@system[0].email")).result.strip(),
            "phone": (await root_ssh.send_command("uci get system.@system[0].contact")).result.strip(),
            "dist": (await root_ssh.send_command("uci get system.@system[0].distance")).result.strip()
        }
        for k in expected:
            assert actual[k] == expected[k], f"Mismatch in {k}! Backend has '{actual[k]}', expected '{expected[k]}'"
        print(f"       [SUCCESS] Backend matched {label} dataset.")

    # --- Step 1: Initial Login ---
    await gui_page.goto(f"https://{bsu_ip}/cgi-bin/luci/")
    if await gui_page.locator(LoginPageLocators.USERNAME_INPUT).is_visible():
        await gui_page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await gui_page.fill(LoginPageLocators.PASSWORD_INPUT, device_creds["pass"])
        await gui_page.press(LoginPageLocators.PASSWORD_INPUT, "Enter")
        await gui_page.wait_for_timeout(5000)

    # --- Step 2: Capture Originals ---
    print("\n[!] Step 1: Pre-Check (Initial Original Values)")
    orig_vals = {
        "name": (await root_ssh.send_command("uci get advwireless.ath.customername")).result.strip(),
        "loc": (await root_ssh.send_command("uci get system.@system[0].location")).result.strip(),
        "email": (await root_ssh.send_command("uci get system.@system[0].email")).result.strip(),
        "phone": (await root_ssh.send_command("uci get system.@system[0].contact")).result.strip(),
        "dist": (await root_ssh.send_command("uci get system.@system[0].distance")).result.strip()
    }
    for k, v in orig_vals.items(): print(f"    -> {k.upper()}: {v}")

    # --- Step 3: Random Configuration Cycle ---
    await navigate_to_location_tab()

    random_data = {
        "name": f"UBR-{random.randint(100, 999)}",
        "loc": f"CITY-{random.randint(10, 99)}",
        "email": f"auto{random.randint(1, 99)}@senao.com",
        "phone": f"12345{random.randint(1000, 9999)}",
        "dist": "2" if orig_vals["dist"] == "1" else "1"
    }

    print(f"\n[!] Step 2: Configuring Random Test Values")
    await gui_page.locator(XPATH_SYS_NAME).fill(random_data["name"])
    await gui_page.locator(XPATH_LOCATION).fill(random_data["loc"])
    await gui_page.locator(XPATH_EMAIL).fill(random_data["email"])
    await gui_page.locator(XPATH_PHONE).fill(random_data["phone"])
    await gui_page.locator(XPATH_DISTANCE).select_option(value=random_data["dist"])

    await apply_trinity_flow()
    await verify_backend(random_data, "RANDOM_CONFIG")

    # --- Step 4: Revert Cycle ---
    print(f"\n[!] Step 3: Re-configuring back to Original Values")

    # 1. Fresh reload to clear any UI glitches
    await gui_page.reload()
    await gui_page.wait_for_load_state("networkidle")

    # 2. Re-navigate
    await navigate_to_location_tab()

    # 3. Fill original values
    print(f"    -> Action: Restoring originals in GUI")
    await gui_page.locator(XPATH_SYS_NAME).fill(orig_vals["name"])
    await gui_page.locator(XPATH_LOCATION).fill(orig_vals["loc"])
    await gui_page.locator(XPATH_EMAIL).fill(orig_vals["email"])
    await gui_page.locator(XPATH_PHONE).fill(orig_vals["phone"])
    await gui_page.locator(XPATH_DISTANCE).select_option(value=orig_vals["dist"])

    # 4. Apply and Verify
    await apply_trinity_flow()
    await verify_backend(orig_vals, "ORIGINAL_REVERT")

    print(f"\n[+] GUI_93: Complete.")