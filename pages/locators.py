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
    """Technical selectors refined for stable LuCI interaction and Fallback support."""

    # Sidebar Navigation (Reverted to the simple versions that pass)
    MENU_NETWORK = "li.Network > a.menu"
    SUBMENU_IP_CONFIG = "ul.dropdown-menu a[href*='/network/ip']"

    # IPv4 Configuration Fields (Using 'contains' for better cbid prefix handling)
    IPv4_PROTO = "select[name*='network.lan.proto']"
    IPv4_ADDRESS = "input[name*='network.lan.ipaddr']"
    IPv4_NETMASK = "input[name*='network.lan.netmask']"
    IPv4_GATEWAY = "input[name*='network.lan.gateway']"

    # IPv6 Configuration Fields
    IPv6_ADDRESS = "input[name*='network.lan.ip6addr']"
    IPv6_GATEWAY = "input[name*='network.lan.ip6gw']"

    # Fallback IP Configuration (Updated to match your UCI: fallback.lan)
    # Using 'contains' name search to bypass cbid/technical prefixes
    FALLBACK_IP = "input[name*='fallback.lan.ipaddr']"
    FALLBACK_NETMASK = "input[name*='fallback.lan.netmask']"

    # Action Buttons
    SAVE_BUTTON = "input.cbi-button[value='Save']"
    CONFIRM_APPLY = "input[value='Apply']"
    APPLY_ICON = "#header_apply"


class EthernetLocators:
    # Sidebar
    MENU_NETWORK = "li.Network > a.menu"
    SUBMENU_ETHERNET = "ul.dropdown-menu a[href*='/network/eth']"

    # Tabs - Specific to LAN 1 / LAN 2
    LAN_TABS = "ul.cbi-tabmenu > li > a"

    # Fields (Wildcard matching for safety)
    SPEED_DROPDOWN = "select[name*='.speed']"
    MTU_INPUT = "input[name*='.mtu']"

    # Action Buttons (Matched to your stable Network logic)
    SAVE_BUTTON = "input.cbi-button[value='Save']"
    APPLY_ICON = "#header_apply"
    CONFIRM_APPLY = "input[value='Apply']"


class DHCPLocators:
    # Sidebar Navigation
    SUBMENU_DHCP = "ul.dropdown-menu a[href*='/network/dhcp']"

    # --- Radio 1 (Default Tab) ---
    DHCP_SERVER_DROPDOWN = "select[name*='ignore']"
    LEASE_TIME_INPUT = "input[name*='leasetime']"

    # --- 2.4 GHz Radio Tab ---
    TAB_RADIO_24 = "ul.cbi-tabmenu > li > a[href*='dhcp24']"

    # IP Configuration Fields (Using wildcard for cbid compatibility)
    RADIO_24_IP = "input[name*='lan24.ipaddr']"
    RADIO_24_MASK = "input[name*='lan24.netmask']"

    SAVE_BUTTON = "input.cbi-button[value='Save']"
    RADIO_24_DHCP_DROPDOWN = "select[name*='lan24.ignore']"

    # DHCP Pool Configuration for 2.4 GHz tab
    RADIO_24_START_IP = "input[name*='lan24.start']"
    RADIO_24_END_IP = "input[name*='lan24.limit']"

