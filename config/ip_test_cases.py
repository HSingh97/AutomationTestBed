"""IP_01–IP_60 validation catalog (BTS / CPE networking)."""

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
    # --- IP_01–IP_17 IPv4 ---
    IpTestCase("IP_01", "Static IPv4 Configuration", "Functional", "bts", "v4"),
    IpTestCase("IP_02", "IPv4 Ping (Local)", "Functional", "bts", "v4"),
    IpTestCase("IP_03", "IPv4 Ping (Remote)", "Functional", "bts", "v4"),
    IpTestCase("IP_04", "IPv4 Gateway Reachability", "Functional", "bts", "v4"),
    IpTestCase("IP_05", "IPv4 Throughput", "Validation", "bts", "v4", ("iperf",)),
    IpTestCase("IP_06", "IPv4 Long Ping", "Validation", "both", "v4"),
    IpTestCase("IP_07", "IPv4 MTU Change", "Validation", "both", "v4"),
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
    # --- IP_18–IP_34 IPv6 ---
    IpTestCase("IP_18", "Static IPv6 Configuration", "Functional", "both", "v6", ("gui",)),
    IpTestCase("IP_19", "IPv6 Ping (Local)", "Functional", "both", "v6"),
    IpTestCase("IP_20", "IPv6 Ping (Remote)", "Functional", "both", "v6"),
    IpTestCase("IP_21", "IPv6 Gateway Reachability", "Functional", "both", "v6"),
    IpTestCase("IP_22", "IPv6 Throughput", "Validation", "both", "v6", ("iperf",)),
    IpTestCase("IP_23", "IPv6 Long Ping", "Validation", "both", "v6"),
    IpTestCase("IP_24", "IPv6 MTU Change", "Validation", "both", "v6"),
    IpTestCase("IP_25", "IPv6 Fragmentation", "Validation", "both", "v6"),
    IpTestCase("IP_26", "IPv6 Link-Local Connectivity", "Validation", "both", "v6"),
    IpTestCase("IP_27", "Neighbor Discovery", "Functional", "both", "v6"),
    IpTestCase("IP_28", "Soft Reboot with IPv6", "Negative", "both", "v6", ("destructive",)),
    IpTestCase("IP_29", "Hard Reboot with IPv6", "Negative", "both", "v6", ("manual_power", "destructive")),
    IpTestCase("IP_30", "Soft Reset", "Negative", "both", "v6", ("destructive",)),
    IpTestCase("IP_31", "Reset with Retain", "Negative", "both", "v6", ("backup", "destructive")),
    IpTestCase("IP_32", "Reboot During Traffic", "Negative", "both", "v6", ("iperf", "destructive")),
    IpTestCase("IP_33", "Interface Flap", "Validation", "both", "v6", ("destructive",)),
    IpTestCase("IP_34", "Restore Backup", "Functional", "both", "v6", ("backup", "destructive")),
    # --- IP_35–IP_36 (automation handlers) ---
    IpTestCase(
        "IP_35",
        "Firmware Upgrade through HTTP",
        "Functional",
        "both",
        "dual",
        ("firmware", "gui", "destructive"),
    ),
    IpTestCase("IP_36", "Dual Stack Validation", "Functional", "both", "dual"),
    # --- IP_37–IP_60 (plan catalog; flows not implemented yet) ---
    IpTestCase("IP_37", "Ping isolation between CPEs", "Functional", "both", "dual"),
    IpTestCase("IP_38", "SNMP isolation between CPEs", "Functional", "both", "dual"),
    IpTestCase("IP_39", "SSH/Telnet isolation", "Functional", "both", "dual"),
    IpTestCase("IP_40", "Traceroute isolation", "Functional", "both", "dual"),
    IpTestCase("IP_41", "ARP isolation between CPEs", "Functional", "both", "dual"),
    IpTestCase("IP_42", "Management VLAN leakage check", "Functional", "both", "dual"),
    IpTestCase("IP_43", "Data VLAN leakage check", "Functional", "both", "dual"),
    IpTestCase("IP_44", "Cross-VLAN reachability test", "Functional", "both", "dual"),
    IpTestCase("IP_45", "QinQ enforcement test", "Functional", "both", "dual"),
    IpTestCase("IP_46", "Mixed traffic separation", "Functional", "both", "dual"),
    IpTestCase("IP_47", "DHCP broadcast containment", "Functional", "both", "dual"),
    IpTestCase("IP_48", "ARP broadcast containment", "Functional", "both", "dual"),
    IpTestCase("IP_49", "Multicast IPTV containment", "Functional", "both", "dual"),
    IpTestCase("IP_50", "Unauthorized multicast join", "Negative", "both", "dual"),
    IpTestCase("IP_51", "Flood containment test", "Functional", "both", "dual"),
    IpTestCase("IP_52", "Verify IGMP snooping", "Functional", "both", "dual"),
    IpTestCase("IP_53", "Verify MLD snooping", "Functional", "both", "dual"),
    IpTestCase("IP_54", "Validate non-member behavior", "Functional", "both", "dual"),
    IpTestCase("IP_55", "Verify MLD snooping on BTS", "Functional", "both", "dual"),
    IpTestCase("IP_56", "Verify DAD functionality", "Functional", "both", "dual"),
    IpTestCase("IP_57", "Validate DAD with rapid assignment", "Functional", "both", "dual"),
    IpTestCase("IP_58", "Stress test IGMP leave/join", "Validation", "both", "dual"),
    IpTestCase("IP_59", "IGMP snooping disabled", "Negative", "both", "dual"),
    IpTestCase("IP_60", "Incorrect MLD handling", "Negative", "both", "dual"),
)

# IPv6 cases with execute_ip_case() handlers (IP_29 hard reboot excluded — manual PDU).
IPV6_IMPLEMENTED_CASE_IDS: frozenset[str] = frozenset(
    f"IP_{n:02d}" for n in range(18, 35) if n != 29
)

# Cases enabled in tests/IP/test_IP.py (implemented handlers only).
ACTIVE_IP_CASE_IDS: frozenset[str] = frozenset(
    {
        "IP_01",
        "IP_02",
        "IP_03",
        "IP_04",
        "IP_05",
        "IP_06",
        "IP_07",
        "IP_08",
        "IP_09",
        "IP_11",
        "IP_12",
        "IP_13",
        "IP_14",
        "IP_15",
        "IP_16",
        "IP_17",
    }
    | IPV6_IMPLEMENTED_CASE_IDS
    | {"IP_35", "IP_36"}
)

# Manual / not automatable with current bench.
INACTIVE_IP_CASE_IDS: frozenset[str] = frozenset(
    {
        "IP_10",  # manual PDU power
        "IP_29",  # manual PDU power
    }
)

# IP_37–IP_60: catalog only until isolation/multicast flows are added
PLANNED_IP_CASE_IDS: frozenset[str] = frozenset(
    f"IP_{n:02d}" for n in range(37, 61)
)


def case_by_id(case_id: str) -> IpTestCase:
    for case in IP_TEST_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def is_active_ip_case(case_id: str) -> bool:
    return case_id in ACTIVE_IP_CASE_IDS


# Ping / gateway / ARP / link-local — skip full link+CPE reconfigure between cases.
IP_FAST_PATH_CASE_IDS: frozenset[str] = frozenset(
    {
        "IP_02",
        "IP_03",
        "IP_04",
        "IP_16",
        "IP_19",
        "IP_20",
        "IP_21",
        "IP_23",
        "IP_26",
    }
)


def is_fast_path_ip_case(case_id: str) -> bool:
    return case_id in IP_FAST_PATH_CASE_IDS


# Lab PC mgmt-VLAN ping to BTS LAN must be verified before the case body (not just SSH/UCI guess).
IP_BTS_LAN_PING_CASE_IDS: frozenset[str] = frozenset(
    {
        "IP_02",
        "IP_03",
        "IP_04",
        "IP_16",
        "IP_19",
        "IP_20",
        "IP_21",
    }
)


def case_requires_bts_lan_ping(case_id: str) -> bool:
    return case_id in IP_BTS_LAN_PING_CASE_IDS


# Throughput / traffic-only — no full link-formation post-case recovery.
IP_LIGHT_POST_CASE_IDS: frozenset[str] = frozenset({"IP_05", "IP_22"})


def device_targets(case: IpTestCase) -> tuple[str, ...]:
    if case.devices == "bts":
        return ("bts",)
    if case.devices == "cpe":
        return ("cpe",)
    return ("bts", "cpe")
