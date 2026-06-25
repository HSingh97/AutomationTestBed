"""Canonical process-monitor service catalog for PROCESS_01–PROCESS_28 automation."""

from __future__ import annotations

from typing import Any

MONITORED_SERVICES: dict[str, dict[str, Any]] = {
    "cron": {"pgrep": "crond", "core_name": "crond", "critical": False},
    "rpcd": {"pgrep": "rpcd", "core_name": "rpcd", "critical": True},
    "network": {"pgrep": "netifd", "core_name": "netifd", "critical": True},
    "dnsmasq": {"pgrep": "dnsmasq", "core_name": "dnsmasq", "critical": False},
    "odhcpd": {"pgrep": "odhcpd", "core_name": "odhcpd", "critical": False},
    "snmpd": {"pgrep": "snmpd", "core_name": "snmpd", "critical": False},
    "snlogd": {"pgrep": "snlogd", "core_name": "snlogd", "critical": False},
    "snlog-scraper": {"pgrep": "snlog-scraper", "core_name": "snlog-scraper", "critical": False},
    "senao-openapi-server": {
        "pgrep": "senao-openapi-server",
        "core_name": "senao-openapi-server",
        "critical": False,
    },
    "breakpad": {"pgrep": "breakpad_reporter", "core_name": "breakpad_reporter", "critical": False},
    "ezmcloud": {"pgrep": "cloud-agentd", "core_name": "cloud-agentd", "critical": False},
    "kwn_devlocator": {"pgrep": "kwn_devlocator", "core_name": "kwn_devlocator", "critical": False},
    "netlinkevents": {"pgrep": "netlinkevents", "core_name": "netlinkevents", "critical": False},
    "ntpd": {"pgrep": "ntpd", "core_name": "ntpd", "critical": False},
    "sshd": {"pgrep": "sshd", "core_name": "sshd", "critical": True},
    "log": {"pgrep": "logd", "core_name": "logd", "critical": True},
    "mcsd": {"pgrep": "mcsd", "core_name": "mcsd", "critical": False},
    "uhttpd": {"pgrep": "uhttpd", "core_name": "uhttpd", "critical": False},
    "sysstat": {"pgrep": "sadc", "core_name": "sadc", "critical": False},
    "compass": {"pgrep": "Kwn-compass", "core_name": "Kwn-compass", "critical": False},
}

MONITORED_SERVICE_NAMES: tuple[str, ...] = tuple(MONITORED_SERVICES.keys())

CRASH_TEST_SERVICE_TARGETS: frozenset[str] = frozenset(MONITORED_SERVICE_NAMES)

# Least disruptive first; sshd/network last (may drop SSH during crash).
SERVICE_SWEEP_ORDER: tuple[str, ...] = (
    "cron",
    "breakpad",
    "sysstat",
    "compass",
    "snlogd",
    "snlog-scraper",
    "netlinkevents",
    "kwn_devlocator",
    "ezmcloud",
    "mcsd",
    "senao-openapi-server",
    "snmpd",
    "odhcpd",
    "dnsmasq",
    "ntpd",
    "uhttpd",
    "log",
    "rpcd",
    "sshd",
    "network",
)

SSH_RECONNECT_SERVICES: frozenset[str] = frozenset({"sshd", "network"})

# Each crash/kill case runs against every crashable service on the DUT.
CASE_SERVICE_TARGETS: dict[str, tuple[str, ...]] = {
    "PROCESS_01": MONITORED_SERVICE_NAMES,
    "PROCESS_02": MONITORED_SERVICE_NAMES,
    "PROCESS_03": MONITORED_SERVICE_NAMES,
    "PROCESS_04": MONITORED_SERVICE_NAMES,
    "PROCESS_05": MONITORED_SERVICE_NAMES,
    "PROCESS_06": MONITORED_SERVICE_NAMES,
    "PROCESS_07": MONITORED_SERVICE_NAMES,
    "PROCESS_08": MONITORED_SERVICE_NAMES,
    "PROCESS_10": MONITORED_SERVICE_NAMES,
    "PROCESS_11": MONITORED_SERVICE_NAMES,
    "PROCESS_12": MONITORED_SERVICE_NAMES,
    "PROCESS_13": MONITORED_SERVICE_NAMES,
    "PROCESS_14": ("network", "dnsmasq"),
    "PROCESS_16": MONITORED_SERVICE_NAMES,
    "PROCESS_17": MONITORED_SERVICE_NAMES,
    "PROCESS_18": MONITORED_SERVICE_NAMES,
    "PROCESS_19": MONITORED_SERVICE_NAMES,
    "PROCESS_21": MONITORED_SERVICE_NAMES,
    "PROCESS_22": MONITORED_SERVICE_NAMES,
    "PROCESS_23": MONITORED_SERVICE_NAMES,
    "PROCESS_24": ("sshd", "network"),
    "PROCESS_25": MONITORED_SERVICE_NAMES,
    "PROCESS_26": MONITORED_SERVICE_NAMES,
    "PROCESS_27": ("network", "dnsmasq", "odhcpd"),
    "PROCESS_28": MONITORED_SERVICE_NAMES,
}

PREFLIGHT_COUNTER_EXEMPT: frozenset[str] = frozenset({"ntpd"})


def validate_monitored_services_catalog() -> list[str]:
    errors: list[str] = []

    if len(MONITORED_SERVICE_NAMES) != len(set(MONITORED_SERVICE_NAMES)):
        seen: set[str] = set()
        dupes: list[str] = []
        for name in MONITORED_SERVICE_NAMES:
            if name in seen:
                dupes.append(name)
            seen.add(name)
        errors.append(f"Duplicate service names in MONITORED_SERVICE_NAMES: {', '.join(sorted(set(dupes)))}")

    if set(MONITORED_SERVICE_NAMES) != set(MONITORED_SERVICES.keys()):
        missing = set(MONITORED_SERVICES) - set(MONITORED_SERVICE_NAMES)
        extra = set(MONITORED_SERVICE_NAMES) - set(MONITORED_SERVICES)
        if missing:
            errors.append(f"MONITORED_SERVICES missing from ordered list: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"MONITORED_SERVICE_NAMES has unknown entries: {', '.join(sorted(extra))}")

    pgrep_to_services: dict[str, list[str]] = {}
    for name, meta in MONITORED_SERVICES.items():
        pgrep = meta.get("pgrep", "")
        pgrep_to_services.setdefault(str(pgrep), []).append(name)
    dup_pgrep = {pat: names for pat, names in pgrep_to_services.items() if len(names) > 1}
    if dup_pgrep:
        errors.append(
            "Duplicate pgrep patterns in catalog: "
            + "; ".join(f"{pat!r} -> {names}" for pat, names in sorted(dup_pgrep.items()))
        )

    unknown_targets = CRASH_TEST_SERVICE_TARGETS - set(MONITORED_SERVICES)
    if unknown_targets:
        errors.append(
            f"Crash test targets not in MONITORED_SERVICES: {', '.join(sorted(unknown_targets))}"
        )

    return errors
