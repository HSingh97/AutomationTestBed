"""Control lab PC Wi‑Fi adapter for 2.4 GHz test cases (nmcli / iw)."""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from typing import Any


def _run(command: str) -> tuple[int, str]:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


async def connect_wifi_24(wifi_cfg: dict[str, Any]) -> bool:
    """Connect automation PC to lab 2.4 GHz SSID using NetworkManager when available."""
    if not wifi_cfg.get("enabled", False):
        return False
    iface = str(wifi_cfg.get("interface", "wlan0"))
    ssid = str(wifi_cfg.get("ssid", "")).strip()
    psk = str(wifi_cfg.get("psk", "")).strip()
    if not ssid:
        print("[wifi] skipped: no ssid in profile")
        return False

    # Prefer nmcli; fall back to iw dev connect (open networks only).
    if psk:
        cmd = (
            f"nmcli dev wifi connect {shlex.quote(ssid)} password {shlex.quote(psk)} "
            f"ifname {shlex.quote(iface)} 2>/dev/null"
        )
    else:
        cmd = f"nmcli dev wifi connect {shlex.quote(ssid)} ifname {shlex.quote(iface)} 2>/dev/null"

    rc, out = await asyncio.to_thread(_run, cmd)
    if rc == 0:
        print(f"[wifi] connected to {ssid} on {iface}")
        return True

    if not psk:
        cmd2 = f"iw dev {shlex.quote(iface)} connect {shlex.quote(ssid)}"
        rc2, out2 = await asyncio.to_thread(_run, cmd2)
        if rc2 == 0:
            print(f"[wifi] iw connected to {ssid} on {iface}")
            return True
        print(f"[wifi] connect failed: {out2[:300]}")
        return False

    print(f"[wifi] nmcli connect failed: {out[:300]}")
    return False


async def disconnect_wifi(wifi_cfg: dict[str, Any]) -> None:
    if not wifi_cfg.get("enabled", False):
        return
    iface = str(wifi_cfg.get("interface", "wlan0"))
    await asyncio.to_thread(_run, f"nmcli dev disconnect {shlex.quote(iface)} 2>/dev/null || true")
