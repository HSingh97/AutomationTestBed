"""Refuse lab-PC Ethernet port down / disconnect (DDRS and co-channel hops).

Quiet baseline is secondary BTS radio TX power (iwconfig txpower), never
``ip link`` / ``nmcli`` on the lab PC NICs (enp1s0, enp4s0, enx*, etc.).
"""

from __future__ import annotations

import re

_LAB_NIC_DOWN_RE = re.compile(
    r"(?:"
    r"ip\s+link\s+set\b[^\n;|&]*\bdown\b"
    r"|ifconfig\s+\S+\s+down\b"
    r"|\bifdown\b"
    r"|nmcli\s+device\s+disconnect\b"
    r"|nmcli\s+networking\s+off\b"
    r"|ip\s+link\s+delete\s+(?:enp|enx|eth)"
    r")",
    re.IGNORECASE,
)


def assert_no_lab_nic_down(remote_cmd: str) -> None:
    """Raise if a lab-PC SSH payload would take an Ethernet NIC down."""
    text = str(remote_cmd or "")
    if _LAB_NIC_DOWN_RE.search(text):
        raise RuntimeError(
            "refused lab-PC NIC down/disconnect "
            f"(DDRS/co-channel must not flap enp*/enx*): {text[:240]}"
        )
