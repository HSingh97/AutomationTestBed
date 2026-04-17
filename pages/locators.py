# pages/locators.py

class LoginPageLocators:
    """CSS/XPath selectors for the main Login Page."""
    USERNAME_INPUT = "input[name='luci_username']"
    PASSWORD_INPUT = "input[name='luci_password']"
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
    IP_ADDRESS = "//*[@id='ipv4']"
    GATEWAY = "//*[@id='gatewayv4']"
    MAC_LAN1 = "//*[@id='eth0_mac']"
    MAC_LAN2 = "//*[@id='eth1_mac']"
    SPEED_DUPLEX_LAN1 = "//*[@id='eth0_opmode']"
    SPEED_DUPLEX_LAN2 = "//*[@id='eth1_opmode']"
    CABLE_LENGTH_LAN1 = "//*[@id='eth0_cable']"
    CABLE_LENGTH_LAN2 = "//*[@id='eth1_cable']"

class SummaryPerformanceLocators:
    """CSS selectors for the Performance Summary"""
    TX_R1 = "//*[@id='wifi1_txtput']"
    RX_R1 = "//*[@id='wifi1_rxtput']"
    TX_LAN1 = "//*[@id='eth0_txtput']"
    RX_LAN1 = "//*[@id='eth0_rxtput']"
    TX_LAN2 = "//*[@id='eth1_txtput']"
    RX_LAN2 = "//*[@id='eth1_rxtput']"

class SummaryWirelessLocators:
    """CSS selectors for the Wireless Summary."""
    RADIO_STATUS = "//*[@id='wifi{0}_status']"
    MAC_ADDRESS = "//*[@id='wifi{0}_mac']"
    LINK_TYPE = "//*[@id='wifi{0}_linktype']"
    RADIO_MODE = "//*[@id='wifi{0}_mode']"
    BANDWIDTH = "//*[@id='wifi{0}_band']"
    SSID = "//*[@id='wifi{0}_ssid']"
    CONFIGURED_CHANNEL = "//*[@id='wifi{0}_cfgchan']"
    ACTIVE_CHANNEL = "//*[@id='wifi{0}_actchan']"
    SECURITY = "//*[@id='wifi{0}_security']"
    RTX_PERCENTAGE = "//*[@id='wifi{0}_rtx']"
    REMOTE_PARTNERS = "//*[@id='wifi{0}_links']"


# pages/locators.py

class NetworkLocators:
    """Technical selectors refined for duplicated LuCI menus."""

    # Target the 'Network' link specifically inside the desktop sidebar
    MENU_NETWORK = "div.sidebar li.Network > a.menu"

    # Target the 'IP Configuration' link specifically inside the sidebar's Network list
    # Using the href part is the most unique way to find it
    SUBMENU_IP_CONFIG = "div.sidebar a[href*='/network/ip']"

    # IP Configuration Fields - Using the exact 'name' attributes from your HTML
    IPv4_PROTO = "select[name='network.lan.proto']"
    IPv4_ADDRESS = "input[name='network.lan.ipaddr']"
    IPv4_NETMASK = "input[name='network.lan.netmask']"
    IPv4_GATEWAY = "input[name='network.lan.gateway']"

    IPv6_ADDRESS = "input[name='network.lan.ip6addr']"
    IPv6_GATEWAY = "input[name='network.lan.ip6gw']"

    # Action Buttons
    SAVE_BUTTON = "input.cbi-button[value='Save']"
    APPLY_ICON = "#header_apply"