"""Radio24Scan lab helpers — NO Functional mgmt-VLAN (101) setup.

Radio24 / CPE API method: scan-PC Wi‑Fi + factory/untagged reachability.
Functional suite owns BTS VLAN / PC LAN mgmt VLAN separately.
"""

from __future__ import annotations

import shlex
import subprocess


def _run_local(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def ensure_radio24_lab_ipv6_routes(
    profile_bundle,
    *,
    disconnect_wifi: bool = True,
) -> None:
    """
    Light Radio24 preflight only — do not create or use mgmt VLAN 101.

    Functional bootstrap is intentionally separate. Radio24 reaches:
      - CPE via untagged Ethernet IPv6 and/or 2.4 GHz mgmt Wi‑Fi
      - BTS via factory IPv4 (e.g. 192.168.2.10), not lab-PC VLAN subinterfaces

    If a stale Functional VLAN (enp1s0.101) is left up with duplicate IPv6,
    tear it down so the untagged path is not poisoned for Radio24.
    """
    del profile_bundle  # keep signature stable for callers
    lines = [
        # Remove Functional leftover so Radio24 does not run on mgmt VLAN 101.
        "ip link set enp1s0.101 down 2>/dev/null || true",
        "ip link delete enp1s0.101 2>/dev/null || true",
        "ip link set enp1s0 up 2>/dev/null || true",
        "nmcli radio wifi on 2>/dev/null || true",
        "ip link set wlp3s0 up 2>/dev/null || true",
    ]
    if disconnect_wifi:
        lines.insert(0, "nmcli device disconnect wlp3s0 2>/dev/null || true")
    script = "set +e\n" + "\n".join(lines) + "\nexit 0\n"
    rc, out = _run_local(f"sudo -n sh -c {shlex.quote(script)}")
    if rc != 0:
        print(f"[radio24] lab Wi‑Fi preflight failed (rc={rc}): {out[:300]}")
    else:
        print("[radio24] lab preflight OK (no mgmt VLAN — Wi‑Fi/factory path only)")
