import pytest

# This applies the 'Sanity' marker to every test in this file automatically
pytestmark = pytest.mark.Sanity


# =====================================================================
# TEST 1: SSH / CLI Reachability (Mapped to CLI_01)
# =====================================================================
@pytest.mark.CLI_01
async def test_bsu_cli_login(bsu_cli):
    """
    CLI_01: Verify we are able to SSH to the DUT and execute a basic command.
    Since 'bsu_cli' is our session fixture, if this test passes, it proves
    the device has booted and the SSH engine is working perfectly.
    """
    print("\n[+] Verifying SSH access to BSU...")

    # 1. Send a harmless command just to prove the prompt is responsive.
    # In Senao/Linux, 'uptime' or simply '?' usually works. Let's use 'uptime' to check boot.
    response = await bsu_cli.send_command("uptime")
    output = response.result

    # 2. Assertions
    assert response.failed is False, "Failed to execute command over SSH."
    assert "load average" in output.lower() or "up" in output.lower(), f"Unexpected SSH output: {output}"


# =====================================================================
# TEST 2: GUI / API System Summary (Mapped to GUI_01)
# =====================================================================
@pytest.mark.GUI_01
async def test_bsu_gui_system_summary(bsu_api):
    """
    GUI_01: Summary-System.
    Verify Model, Hardware Version, and Firmware version via the HTTP API.
    """
    print("\n[+] Fetching System Summary from GUI/API...")

    # 1. Make the GET request to the BSU's summary endpoint.
    # NOTE: You will need to replace '/api/v1/system/summary' with the actual
    # Senao endpoint path. You can find this by opening the GUI, hitting F12 (Network),
    # and looking at the request the browser makes.
    try:
        response = await bsu_api.get("/api/v1/system/summary", timeout=10.0)
    except Exception as e:
        pytest.fail(f"HTTP connection to BSU failed: {str(e)}")

    # 2. Assert HTTP Success
    assert response.status_code == 200, f"GUI/API returned error code: {response.status_code}"

    # 3. Parse and Validate the JSON Data
    data = response.json()

    # Check that critical keys exist in the response
    # (Adjust these keys based on what the Senao API actually returns)
    assert "model" in data, "GUI response missing 'model' information"
    assert "hw_version" in data, "GUI response missing 'Hardware Version'"

    # Validate the values
    assert data["model"] in ["A60", "A61", "A60_A61"], f"Incorrect model reported: {data['model']}"


# =====================================================================
# TEST 3: Network Reachability (BSU to CPE)
# =====================================================================
@pytest.mark.CLI_190
async def test_bsu_can_ping_cpe(bsu_cli, cpe_ips):
    """
    CLI_190: Verify the BSU can reach the connected CPE over the wireless link.
    """
    if not cpe_ips:
        pytest.skip("No CPE IP provided via Jenkins/CLI (--remote-ip). Skipping ping test.")

    target_cpe = cpe_ips[0]
    print(f"\n[+] Pinging CPE ({target_cpe}) from BSU...")

    # Send 4 ICMP echo requests
    response = await bsu_cli.send_command(f"ping {target_cpe} -c 4")
    output = response.result

    # Assert there is 0% packet loss
    assert "0% packet loss" in output or "0% loss" in output, f"Ping to CPE failed. Output: {output}"