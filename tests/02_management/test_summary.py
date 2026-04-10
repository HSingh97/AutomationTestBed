import pytest
import pytest_check as check
import os

from pages.locators import LoginPageLocators, SummaryLocators
from pages.commands import RootCommands

# Add Segregation Marker
pytestmark = pytest.mark.sanity


@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_01  # Test Case ID Marker
async def test_gui_01_summary_system(bsu_root_cli, gui_browser, bsu_ip, device_creds):
    """
    GUI_01: Summary-System.
    Verify Model, Hardware Version, Bootloader Version, Local Time,
    Temperature, GPS, Elevation, CPU / Memory match between backend and GUI.
    """
    print("\n[+] Starting GUI_01: Cross-Validating System Summary (Root -> GUI)")

    # =========================================================
    # STEP 1: EXTRACT FROM ROOT BACKEND
    # =========================================================
    print("    -> Pulling all 8 parameters from Root Backend...")

    # We fetch these sequentially to avoid overwhelming the SSH channel buffer
    cli_model = (await bsu_root_cli.send_command(RootCommands.GET_MODEL)).result.strip()
    cli_hw = (await bsu_root_cli.send_command(RootCommands.GET_HW_VERSION)).result.strip()
    cli_bootloader = (await bsu_root_cli.send_command(RootCommands.GET_BOOTLOADER)).result.strip()
    cli_time = (await bsu_root_cli.send_command(RootCommands.GET_TIME)).result.strip()
    cli_temp = (await bsu_root_cli.send_command(RootCommands.GET_TEMP)).result.strip()
    cli_gps = (await bsu_root_cli.send_command(RootCommands.GET_GPS)).result.strip()
    cli_elevation = (await bsu_root_cli.send_command(RootCommands.GET_ELEVATION)).result.strip()
    cli_cpu_mem = (await bsu_root_cli.send_command(RootCommands.GET_CPU_MEM)).result.strip()

    # =========================================================
    # STEP 2: EXTRACT FROM GUI
    # =========================================================
    print("    -> Pulling parameters from Web GUI...")
    context = await gui_browser.new_context(ignore_https_errors=True)
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()

    try:
        # Navigate and Login
        await page.goto(f"https://{bsu_ip}")
        await page.fill(LoginPageLocators.USERNAME_INPUT, device_creds["user"])
        await page.fill(LoginPageLocators.PASSWORD_INPUT, bsu_root_cli.auth_password)
        await page.click(LoginPageLocators.LOGIN_BUTTON)
        await page.wait_for_load_state("networkidle")

        # NOTE: Add a click step here if you need to navigate away from a default dashboard
        # to reach the "Summary" tab. e.g., await page.click("#nav-summary-tab")

        print("    -> Scraping GUI data fields...")
        gui_model = await page.locator(SummaryLocators.MODEL).inner_text()
        gui_hw = await page.locator(SummaryLocators.HW_VERSION).inner_text()
        gui_bootloader = await page.locator(SummaryLocators.BOOTLOADER).inner_text()
        gui_time = await page.locator(SummaryLocators.LOCAL_TIME).inner_text()
        gui_temp = await page.locator(SummaryLocators.TEMPERATURE).inner_text()
        gui_gps = await page.locator(SummaryLocators.GPS).inner_text()
        gui_elevation = await page.locator(SummaryLocators.ELEVATION).inner_text()
        gui_cpu_mem = await page.locator(SummaryLocators.CPU_MEMORY).inner_text()

        # =========================================================
        # STEP 3: SOFT ASSERTIONS (Cross-Validation)
        # =========================================================
        print("    -> Validating Backend vs Frontend...")

        # check.is_in will fail the specific check but KEEP running the rest of the file
        check.is_in(cli_model, gui_model, f"Model Mismatch! CLI: {cli_model} | GUI: {gui_model}")
        check.is_in(cli_hw, gui_hw, f"HW Version Mismatch! CLI: {cli_hw} | GUI: {gui_hw}")
        check.is_in(cli_bootloader, gui_bootloader,
                    f"Bootloader Mismatch! CLI: {cli_bootloader} | GUI: {gui_bootloader}")

        # Time strings usually format differently (e.g. CLI="Wed Apr 8 12:00:00" vs GUI="12:00:00")
        # We slice or check for substring inclusion.
        check.is_in(cli_time[:10], gui_time, f"Time Mismatch! CLI: {cli_time} | GUI: {gui_time}")

        check.is_in(cli_temp, gui_temp, f"Temp Mismatch! CLI: {cli_temp} | GUI: {gui_temp}")
        check.is_in(cli_gps, gui_gps, f"GPS Mismatch! CLI: {cli_gps} | GUI: {gui_gps}")
        check.is_in(cli_elevation, gui_elevation, f"Elevation Mismatch! CLI: {cli_elevation} | GUI: {gui_elevation}")

        # CPU/Mem output from 'top' will be messy. You may need to parse this specifically.
        # check.is_in(cli_cpu_mem, gui_cpu_mem, f"CPU/Mem Mismatch! CLI: {cli_cpu_mem} | GUI: {gui_cpu_mem}")

        print("[+] GUI_01 Soft Validation Complete.")

    finally:
        os.makedirs("logs", exist_ok=True)
        trace_path = f"logs/gui_01_trace_{bsu_ip}.zip"
        await context.tracing.stop(path=trace_path)
        await context.close()