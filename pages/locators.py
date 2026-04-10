# pages/locators.py

class LoginPageLocators:
    """CSS/XPath selectors for the main Login Page."""

    USERNAME_INPUT = "input[name='luci_username']"
    PASSWORD_INPUT = "input[name='luci_password']"

    # Matches: <input type="submit" value="Login" ...>
    LOGIN_BUTTON = "input[type='submit']"

class SummaryLocators:
    """CSS/XPath selectors for the Quick Start / Summary Page."""
    MODEL = "//*[@id='model']"
    HW_VERSION = "//*[@id='hwver']"
    BOOTLOADER = "//*[@id='blver']"
    LOCAL_TIME = "//*[@id='time']"
    TEMPERATURE = "//*[@id='temp']"
    GPS = "//*[@id='gps']"
    ELEVATION = "//*[@id='elevation']"
    CPU_MEMORY = "//*[@id='cpu_mem']"


class SummaryNetworkLocators:
    """CSS selectors for the Network Summary"""
    NETWORK_TAB_BUTTON = "#nav-network-summary"  # Only needed if it's on a separate sub-tab

    IP_ADDRESS = "//*[@id='ipv4']"
    GATEWAY = "//*[@id='gatewayv4']"
    MAC_LAN1 = "//*[@id='eth0_mac']"
    MAC_LAN2 = "//*[@id='eth1_mac']"
    SPEED_DUPLEX_LAN1 = "//*[@id='eth0_opmode']"
    SPEED_DUPLEX_LAN2 = "//*[@id='eth1_opmode']"
    CABLE_LENGTH_LAN1 = "//*[@id='eth0_cable']"
    CABLE_LENGTH_LAN2 = "//*[@id='eth1_cable']"