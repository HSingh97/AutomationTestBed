class LoginPageLocators:
    """CSS/XPath selectors for the main Login Page."""
    USERNAME_INPUT = "input[name='luci_username']"
    PASSWORD_INPUT = "input[name='luci_password']"
    LOGIN_BUTTON = "input[type='submit']"


class UITimeouts:
    SHORT_WAIT_MS = 1000
    MEDIUM_WAIT_MS = 3000
    LONG_WAIT_MS = 5000
    PAGE_LOAD_MS = 15000
    ELEMENT_WAIT_MS = 10000


class CommonLocators:
    """Selectors intended for reuse across pages/suites."""

    # Top/global action buttons
    SAVE_BUTTON = "input.cbi-button[value='Save']"
    APPLY_ICON = "#header_apply"
    CONFIRM_APPLY = "input[value='Apply']:visible"

    # Sidebar top-level menus
    MENU_WIRELESS = "li.Wireless > a.menu"
    MENU_NETWORK = "li.Network > a.menu"
    MENU_MANAGEMENT = "li.Management > a.menu"
    MENU_MONITOR = "li.Monitor > a.menu"

    # Common submenu selectors
    SUBMENU_NETWORK_IP = "ul.dropdown-menu a[href*='/network/ip']"
    SUBMENU_NETWORK_DHCP = "ul.dropdown-menu a[href*='/network/dhcp']"
    SUBMENU_NETWORK_ETH = "ul.dropdown-menu a[href*='/network/eth']"
    SUBMENU_MANAGEMENT_SYSTEM = "ul.dropdown-menu a[href*='/system/system']"

    @staticmethod
    def submenu_by_href(href_fragment: str) -> str:
        return f"ul.dropdown-menu a[href*='{href_fragment}']"


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
    MENU_NETWORK = CommonLocators.MENU_NETWORK
    SUBMENU_IP_CONFIG = CommonLocators.SUBMENU_NETWORK_IP
    SUBMENU_DHCP = CommonLocators.SUBMENU_NETWORK_DHCP
    SUBMENU_ETHERNET = CommonLocators.SUBMENU_NETWORK_ETH

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
    SAVE_BUTTON = CommonLocators.SAVE_BUTTON
    CONFIRM_APPLY = CommonLocators.CONFIRM_APPLY
    APPLY_ICON = CommonLocators.APPLY_ICON


class EthernetLocators:
    # Sidebar
    MENU_NETWORK = CommonLocators.MENU_NETWORK
    SUBMENU_ETHERNET = CommonLocators.SUBMENU_NETWORK_ETH

    # Tabs - Specific to LAN 1 / LAN 2
    LAN_TABS = "ul.cbi-tabmenu > li > a"

    # Fields (Wildcard matching for safety)
    SPEED_DROPDOWN = "select[name*='.speed']"
    MTU_INPUT = "input[name*='.mtu']"

    # Action Buttons (Matched to your stable Network logic)
    SAVE_BUTTON = CommonLocators.SAVE_BUTTON
    APPLY_ICON = CommonLocators.APPLY_ICON
    CONFIRM_APPLY = CommonLocators.CONFIRM_APPLY


class DHCPLocators:
    # Sidebar Navigation
    SUBMENU_DHCP = CommonLocators.SUBMENU_NETWORK_DHCP

    # --- Radio 1 (Default Tab) ---
    DHCP_SERVER_DROPDOWN = "select[name*='ignore']"
    LEASE_TIME_INPUT = "input[name*='leasetime']"

    # --- 2.4 GHz Radio Tab ---
    TAB_RADIO_24 = "ul.cbi-tabmenu > li > a[href*='dhcp24']"
    TAB_RADIO_24_TEXT = "2.4 GHz Radio"

    # IP Configuration Fields (Using wildcard for cbid compatibility)
    RADIO_24_IP = "input[name*='lan24.ipaddr']"
    RADIO_24_MASK = "input[name*='lan24.netmask']"

    SAVE_BUTTON = CommonLocators.SAVE_BUTTON
    RADIO_24_DHCP_DROPDOWN = "select[name*='lan24.ignore']"

    # DHCP Pool Configuration for 2.4 GHz tab
    RADIO_24_START_IP = "input[name*='lan24.start']"
    RADIO_24_END_IP = "input[name*='lan24.limit']"
    RADIO_24_LEASE_TIME_INPUT = "input[name*='lan24.leasetime']"

class TopPanelLocators:
    """CSS/XPath selectors for the Top Panel Header Navigation."""
    LOGO = "a.header-logo, .brand"

    # !!! UPDATE THESE LOCATORS BY INSPECTING YOUR GUI !!!
    TOP_SYSNAME = "//*[@id='sysname']"
    TOP_DESC_INFO = "//*[@id='desc']"
    TOP_UPTIME = "//*[@id='uptime']"

    # Navigation Menus
    MENU_RADIO = "//*[@id='menu_radio']"

    SUBMENU_RADIO_0 = "//*[@id='radio_sec0']/ul"  # Usually 2.4 GHz
    SUBMENU_RADIO_1 = "//*[@id='radio_sec1']/ul"  # Radio 1 (5GHz or 6GHz)

    MENU_NETWORK = "//*[@id='menu_network']"

    # Action Buttons
    HOME_BUTTON = "li[title='Home']"
    APPLY_BUTTON = "//*[@id='header_apply']"
    REBOOT_BUTTON = "//*[@id='header_reboot']"
    REBOOT_CONFIRM = "//*[@id='maincontent']/div/p[3]/a"
    LOGOUT_BUTTON = "li[title='Logout']"

    SUPER_APPLY_BUTTON = "//*[@id='super_apply']"
    SUPER_REVERT_BUTTON = "//*[@id='super_revert']"

    FORM_SAVE_BUTTON = "//*[@id='maincontent']/div/div[2]/input"


class RadioPropertiesLocators:
    MENU_WIRELESS = CommonLocators.MENU_WIRELESS

    
    SUBMENU_RADIO_1 = CommonLocators.submenu_by_href("/wireless/radio1")

    # --- Form Elements ---
    STATUS_DROPDOWN = "//*[@id='maincontent']/div/div[1]/fieldset/form/div[1]/div/select"
    LINK_TYPE_DROPDOWN = "//*[@id='maincontent']/div/div[1]/fieldset/form/div[3]/div/select"

    RADIO_MODE_DROPDOWN = "//*[@id='maincontent']/div/div[1]/fieldset/form/div[4]/div/select"
    SSID_INPUT = "//*[@id='edit_ssid']//input"
    BANDWIDTH_DROPDOWN = "//select[@name='wireless.wifi1.htmode']"

    CONFIGURED_CHANNEL_DROPDOWN = "//*[@id='maincontent']/div/div[1]/fieldset/form/div[5]/div/select"
    ACTIVE_CHANNEL_DISPLAY = "//*[@id='maincontent']/div/div[1]/fieldset/form/div[5]/div/span"
    ENCRYPTION_DROPDOWN = "//*[@name='wireless.@wifi-iface[1].encryption']"
    ENCRYPTION_KEY_INPUT = "//*[@name='wireless.@wifi-iface[1].key']"
    NETWORK_SECRET_INPUT = "input[id*='nwksec'], input[name*='nwksecret']"
    DISTANCE_INPUT = "//*[@name='wireless.wifi1.distance']"
    MAXIMUM_SU_INPUT = "//*[@name='wireless.@wifi-iface[1].maxsta']"



class ManagementLocators:
    # Sidebar
    MENU_MANAGEMENT = CommonLocators.MENU_MANAGEMENT
    SUBMENU_SYSTEM = CommonLocators.SUBMENU_MANAGEMENT_SYSTEM
    TAB_LOCATION_LINK_TEXT = "Location"
    TAB_LOGGING_XPATH = '//*[@id="maincontent"]/div/ul/li[2]/a'

    # System Page - General Tab
    TIMEZONE_DROPDOWN = "select[name*='timezone']"
    SAVE_BUTTON = CommonLocators.SAVE_BUTTON

    NTP_SERVER_LIST = "div.cbi-section-node div.cbi-value"
    NTP_DELETE_BTNS = "input.cbi-button[value='Delete']"
    NTP_ADD_INPUT = "input[id*='cbi-system-ntp-server']"
    NTP_ADD_BTN = "input.cbi-button[value='Add']"

    # Legacy-page specific stable selectors used by Management test suite
    NTP_ADD_INPUT_XPATH = '//*[@id="ntp_addr"]'
    NTP_ADD_BTN_XPATH = '//*[@id="addserver"]/div/input[2]'
    NTP_DELETE_BTN_XPATH = '//input[@value="Delete"]'

    TIMEZONE_SELECT_XPATH = '//*[@id="maincontent"]/div/div[1]/fieldset/form/div[2]/div/select'
    SYNC_BROWSER_BTN_XPATH = '//*[@id="maincontent"]/div/div[2]/input[1]'

    LOG_IP_XPATH = '//*[@id="syslog_ip"]/div/input'
    LOG_PORT_XPATH = '//*[@id="syslog_port"]/div/input'
    TEMP_STATUS_XPATH = '//*[@id="temlog_status"]'
    TEMP_INTERVAL_XPATH = '//*[@id="templog_int"]/div/input'

    LOCATION_SYSTEM_NAME_XPATH = '//*[@id="cusname"]/div/input'
    LOCATION_ADDRESS_XPATH = '//*[@id="cusloc"]/div/input'
    LOCATION_EMAIL_XPATH = '//*[@id="cusemail"]/div/input'
    LOCATION_PHONE_XPATH = '//*[@id="cusphone"]/div/input'
    LOCATION_DISTANCE_XPATH = '//*[@id="maincontent"]/div/div[1]/fieldset/form/div[8]/div/select'

