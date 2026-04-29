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
    """CSS selectors for the Wireless Summary. Use {0} to format dynamically for wifi0, wifi1."""

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
    MENU_WIRELESS = "xpath=/html/body/header/div/div/div[1]/ul/li[2]/a"
    SUBMENU_RADIO_1 = "//*[@id='Wireless']/li[1]/a"

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


