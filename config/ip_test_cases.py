"""IP_01–IP_37 validation catalog (BTS / CPE networking)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["Functional", "Validation", "Negative"]
Stack = Literal["v4", "v6", "dual", "any"]
DeviceScope = Literal["both", "bts", "cpe"]


@dataclass(frozen=True)
class IpTestCase:
    case_id: str
    title: str
    category: Category
    devices: DeviceScope
    stack: Stack
    # Optional capability flags: manual_power, iperf, backup, firmware, gui, destructive
    requires: tuple[str, ...] = ()


IP_TEST_CASES: tuple[IpTestCase, ...] = (
    IpTestCase("IP_01", "Static IPv4 Configuration", "Functional", "both", "v4", ("gui",)),
    IpTestCase("IP_02", "IPv4 Ping (Local)", "Functional", "both", "v4"),
    IpTestCase("IP_03", "IPv4 Ping (Remote)", "Functional", "both", "v4"),
    IpTestCase("IP_04", "IPv4 Gateway Reachability", "Functional", "both", "v4"),
    IpTestCase("IP_05", "IPv4 Throughput", "Validation", "both", "v4", ("iperf",)),
    IpTestCase("IP_06", "IPv4 Long Ping", "Validation", "both", "v4"),
    IpTestCase("IP_07", "IPv4 MTU Change", "Validation", "both", "v4", ("gui",)),
    IpTestCase("IP_08", "IPv4 Fragmentation", "Validation", "both", "v4"),
    IpTestCase("IP_09", "Soft Reboot with IPv4", "Negative", "both", "v4", ("destructive",)),
    IpTestCase("IP_10", "Hard Reboot with IPv4", "Negative", "both", "v4", ("manual_power", "destructive")),
    IpTestCase("IP_11", "Soft Reset", "Negative", "both", "v4", ("destructive",)),
    IpTestCase("IP_12", "Reset with Retain Config", "Negative", "both", "v4", ("backup", "destructive")),
    IpTestCase("IP_13", "Reboot During Traffic", "Negative", "both", "v4", ("iperf", "destructive")),
    IpTestCase("IP_14", "Interface Flap", "Validation", "both", "v4", ("destructive",)),
    IpTestCase("IP_15", "Restore Backup", "Validation", "both", "v4", ("backup", "destructive")),
    IpTestCase("IP_16", "ARP Resolution", "Validation", "both", "v4"),
    IpTestCase("IP_17", "Static Route Validation", "Validation", "both", "v4"),
    IpTestCase("IP_18", "Static IPv6 Configuration", "Functional", "both", "v6", ("gui",)),
    IpTestCase("IP_19", "IPv6 Ping (Local)", "Functional", "both", "v6"),
    IpTestCase("IP_20", "IPv6 Ping (Remote)", "Functional", "both", "v6"),
    IpTestCase("IP_21", "IPv6 Gateway Reachability", "Functional", "both", "v6"),
    IpTestCase("IP_22", "IPv6 Throughput", "Validation", "both", "v6", ("iperf",)),
    IpTestCase("IP_23", "IPv6 Long Ping", "Validation", "both", "v6"),
    IpTestCase("IP_24", "IPv6 MTU Change", "Validation", "both", "v6", ("gui",)),
    IpTestCase("IP_25", "IPv6 Fragmentation", "Validation", "both", "v6"),
    IpTestCase("IP_26", "IPv6 Link-Local Connectivity", "Validation", "both", "v6"),
    IpTestCase("IP_27", "Neighbor Discovery", "Functional", "both", "v6"),
    IpTestCase("IP_28", "Soft Reboot with IPv6", "Negative", "both", "v6", ("destructive",)),
    IpTestCase("IP_29", "Soft Reboot BTS static / CPE dynamic IPv6", "Negative", "bts", "v6", ("destructive",)),
    IpTestCase("IP_30", "Hard Reboot with IPv6", "Negative", "both", "v6", ("manual_power", "destructive")),
    IpTestCase("IP_31", "Soft Reset", "Negative", "both", "v6", ("destructive",)),
    IpTestCase("IP_32", "Reset with Retain", "Negative", "both", "v6", ("backup", "destructive")),
    IpTestCase("IP_33", "Reboot During Traffic", "Negative", "both", "v6", ("iperf", "destructive")),
    IpTestCase("IP_34", "Interface Flap", "Validation", "both", "v6", ("destructive",)),
    IpTestCase("IP_35", "Restore Backup", "Functional", "both", "v6", ("backup", "destructive")),
    IpTestCase("IP_36", "Firmware Upgrade through HTTP", "Functional", "both", "dual", ("firmware", "gui", "destructive")),
    IpTestCase("IP_37", "Dual Stack Validation", "Functional", "both", "dual"),
)


def case_by_id(case_id: str) -> IpTestCase:
    for case in IP_TEST_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def device_targets(case: IpTestCase) -> tuple[str, ...]:
    if case.devices == "bts":
        return ("bts",)
    if case.devices == "cpe":
        return ("cpe",)
    return ("bts", "cpe")
