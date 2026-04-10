# pages/commands.py

class RootCommands:
    """Linux backend commands executed via SSH as 'root'."""
    GET_MODEL = "cat /etc/ademodel"
    GET_HW_VERSION = "cat /etc/hwver"
    GET_BOOTLOADER = "cat /etc/blver"
    GET_TIME = "date"
    GET_TEMP = "tmp101"
    GET_GPS = ""
    GET_ELEVATION = ""
    GET_CPU_MEM = "cat /proc/cpuinfo"
    GET_IP = "ip -4 addr show br-lan | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}'"
    GET_GATEWAY = "ip route | grep default | awk '{print $3}'"

    # Assuming eth0 is LAN 1 and eth1 is LAN 2
    GET_MAC_LAN1 = "cat /sys/class/net/eth0/address"
    GET_MAC_LAN2 = "cat /sys/class/net/eth1/address"

    # ethtool usually provides speed/duplex.
    GET_SPEED_DUPLEX_LAN1 = "ethtool eth0 | grep -E 'Speed|Duplex'"
    GET_SPEED_DUPLEX_LAN2 = "ethtool eth1 | grep -E 'Speed|Duplex'"

    # Cable length usually requires a switch-specific PHY command like 'swconfig' or a custom Senao binary
    GET_CABLE_LENGTH_LAN1 = "swconfig dev switch0 port 1 get link"  # Placeholder
    GET_CABLE_LENGTH_LAN2 = "swconfig dev switch0 port 2 get link"  # Placeholder

