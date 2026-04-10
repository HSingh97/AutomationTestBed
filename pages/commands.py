# pages/commands.py

class RootCommands:
    """Linux backend commands executed via SSH as 'root'."""
    GET_MODEL = "cat /etc/model"
    GET_HW_VERSION = "cat /etc/hw_version"
    GET_BOOTLOADER = "cat /etc/bootloader"
    GET_TIME = "date"
    GET_TEMP = "cat /sys/class/thermal/thermal_zone0/temp"
    GET_GPS = ""
    GET_ELEVATION = ""
    GET_CPU_MEM = "cat /proc/cpuinfo"

class AdminCommands:
    """Device CLI commands executed via SSH as 'admin'."""
    GET_MODEL = "show system info"
    GET_TIME = "show system time"

