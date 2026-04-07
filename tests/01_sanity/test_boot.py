import pytest
import re  # Added for strict pattern matching

pytestmark = pytest.mark.Sanity


# =====================================================================
# TEST 1: SSH / CLI Reachability (Mapped to CLI_01)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.CLI_01
async def test_bsu_cli_login(bsu_cli):
    """
    CLI_01: Verify we are able to SSH to the DUT and execute a basic command.
    """
    print("\n[+] Verifying SSH access to BSU...")
    response = await bsu_cli.send_command("uptime")
    output = response.result

    assert response.failed is False, "Failed to execute command over SSH."
    assert "load average" in output.lower() or "up" in output.lower(), f"Unexpected output: {output}"


# =====================================================================
# TEST 2: GUI / API System Summary (Mapped to GUI_01)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.GUI_01
async def test_bsu_gui_system_summary(bsu_api):
    """
    GUI_01: Verify the Web GUI is online and responding.
    """
    print("\n[+] Fetching the GUI Root/Login Page...")
    try:
        response = await bsu_api.get("/", timeout=10.0)
    except Exception as e:
        pytest.fail(f"HTTP connection to BSU failed: {str(e)}")

    assert response.status_code == 200, f"GUI/API returned error code: {response.status_code}"


# =====================================================================
# TEST 3: Network Reachability (BSU to CPE)
# =====================================================================
@pytest.mark.asyncio(scope="session")
@pytest.mark.CLI_190
async def test_bsu_can_ping_cpe(bsu_cli, cpe_ips):
    """
    CLI_190: Strict Ping verification using Regex.
    """
    if not cpe_ips:
        pytest.skip("No CPE IP provided via Jenkins/CLI. Skipping ping test.")

    target_cpe = cpe_ips[0]
    print(f"\n[+] Pinging CPE ({target_cpe}) from BSU...")

    response = await bsu_cli.send_command(f"ping {target_cpe} -c 4")
    output = response.result

    # STRICT FIX: Use regex to find the exact number before "% packet loss"
    # This captures the '100' in '100% packet loss' or '0' in '0% packet loss'
    match = re.search(r'(\d+)%\s*packet loss', output)

    if match:
        loss_percentage = int(match.group(1))
        assert loss_percentage == 0, f"Ping failed with {loss_percentage}% packet loss!\nOutput: {output}"
    else:
        pytest.fail(f"Could not parse ping statistics from output.\nOutput: {output}")