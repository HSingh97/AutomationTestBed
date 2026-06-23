"""Canonical process-monitor service catalog (Process_Monitor_Overview.pdf)."""

from __future__ import annotations

from typing import Any

# ubus service name -> process metadata.
# core_name: basename used in /overlay/data/procmon/cores/core.<core_name>.<pid>.<ts>
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

# Explicit ordered list — used to detect duplicate entries in the catalog file.
MONITORED_SERVICE_NAMES: tuple[str, ...] = tuple(MONITORED_SERVICES.keys())

CRASH_TEST_SERVICE_TARGETS: frozenset[str] = frozenset(
    {
        "dnsmasq",
        "snmpd",
        "odhcpd",
        "log",
        "snlog-scraper",
        "snlogd",
        "ezmcloud",
        "mcsd",
        "netlinkevents",
        "senao-openapi-server",
        "network",
        "compass",
        "kwn_devlocator",
        "uhttpd",
    }
)

# Preflight only: non-zero baseline on these services does not block the suite.
# PROCESS_01 still reports them as PARTIAL (possible firmware bug).
PREFLIGHT_COUNTER_EXEMPT: frozenset[str] = frozenset({"ntpd"})


def validate_monitored_services_catalog() -> list[str]:
    """Return human-readable errors for duplicate or inconsistent catalog entries."""
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
