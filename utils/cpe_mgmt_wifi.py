"""
Step 1 — PC on CPE hidden 2.4 GHz management Wi‑Fi (manual join by default).

Join CPE mgmt Wi‑Fi on the PC yourself. Tests print the active SSID and check 169.254.254.1.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

import httpx

from utils.cpe_api_24_config import DEFAULT_CPE_API_24_BASE, CpeApi24Config

DEFAULT_NMCLI_CON_NAME = "cpe-api-24-mgmt"


@dataclass(frozen=True)
class CpeMgmtWifiCredentials:
    ssid: str
    password: str
    hidden: bool = True


@dataclass(frozen=True)
class PcWifiJoinResult:
    connected: bool
    interface: str | None
    ssid: str | None
    method: str  # manual | nmcli


def resolve_cpe_mgmt_wifi_credentials(config: CpeApi24Config) -> CpeMgmtWifiCredentials:
    """Only for --cpe-24-auto-join-wifi (optional nmcli join)."""
    ssid = config.cpe_mgmt_ssid.strip()
    password = config.cpe_mgmt_password
    if not ssid or password == "":
        raise RuntimeError(
            "Auto-join needs CPE mgmt Wi‑Fi on CLI:\n"
            "  --cpe-24-mgmt-ssid=<SSID> --cpe-24-mgmt-password=<key>\n"
            "Or join manually (default) and omit those flags."
        )
    return CpeMgmtWifiCredentials(
        ssid=ssid,
        password=str(password),
        hidden=config.cpe_mgmt_hidden,
    )


async def is_mgmt_api_reachable(
    base_url: str = DEFAULT_CPE_API_24_BASE,
    *,
    timeout_s: float = 2.0,
) -> bool:
    url = f"{base_url.rstrip('/')}/api/v1/cpe/sw-version"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=timeout_s)) as client:
            resp = await client.get(url)
            # When /etc/version is missing (API_08 setup), sw-version returns 503 but
            # the management API is still reachable. Treat 200/503 as "API up".
            return resp.status_code in (200, 503)
    except Exception:
        return False


async def wait_for_mgmt_api_reachable(
    base_url: str = DEFAULT_CPE_API_24_BASE,
    *,
    attempts: int = 10,
    delay_s: float = 1.0,
) -> bool:
    for _ in range(attempts):
        if await is_mgmt_api_reachable(base_url):
            return True
        await asyncio.sleep(delay_s)
    return False


async def _run_subprocess(*args: str, timeout_s: int = 120) -> tuple[int, str]:
    if not args:
        return 1, ""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "command timed out"
    return proc.returncode or 0, (stdout or b"").decode(errors="replace")


def _nmcli_available() -> bool:
    return shutil.which("nmcli") is not None


async def list_nmcli_wifi_devices() -> list[str]:
    code, out = await _run_subprocess("nmcli", "-t", "-f", "DEVICE,TYPE", "device", timeout_s=15)
    if code != 0:
        return []
    devices: list[str] = []
    for line in out.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            devices.append(parts[0])
    return devices


async def resolve_wifi_interface(preferred: str | None) -> str:
    wifi_devs = await list_nmcli_wifi_devices()
    preferred = (preferred or "").strip()
    if preferred and preferred in wifi_devs:
        return preferred
    if wifi_devs:
        resolved = wifi_devs[0]
        if preferred and preferred != resolved:
            print(
                f"[Step 1] --cpe-24-wifi-interface={preferred} not found; "
                f"using detected Wi‑Fi device '{resolved}'"
            )
        return resolved
    raise RuntimeError(
        "No Wi‑Fi adapter found (nmcli). Connect CPE mgmt Wi‑Fi manually, or pass "
        "--cpe-24-wifi-interface if nmcli cannot list devices."
        + (f" Requested interface '{preferred}' is not a Wi‑Fi device." if preferred else "")
    )


async def get_active_wifi_ssid(interface: str | None = None) -> tuple[str | None, str | None]:
    """
    Return (interface, active_ssid) for the Wi‑Fi NIC in use.
    Uses nmcli active scan list, then device connection name as fallback.
    """
    if not _nmcli_available():
        return None, None

    iface = interface
    if iface:
        code, out = await _run_subprocess(
            "nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", iface, timeout_s=15
        )
        if code == 0:
            state = ""
            con_name = ""
            for line in out.splitlines():
                if line.startswith("GENERAL.STATE:"):
                    state = line.split(":", 1)[1].strip()
                elif line.startswith("GENERAL.CONNECTION:"):
                    con_name = line.split(":", 1)[1].strip()
            if "connected" in state.lower() and con_name and con_name != "--":
                code2, out2 = await _run_subprocess(
                    "nmcli", "-g", "802-11-wireless.ssid", "connection", "show", con_name, timeout_s=15
                )
                if code2 == 0 and out2.strip():
                    return iface, out2.strip()

    code, out = await _run_subprocess("nmcli", "-t", "-f", "active,ssid", "dev", "wifi", timeout_s=15)
    if code == 0:
        for line in out.splitlines():
            if line.startswith("yes:"):
                ssid = line.split(":", 1)[1].strip()
                if ssid:
                    wifi_devs = await list_nmcli_wifi_devices()
                    return (iface or (wifi_devs[0] if wifi_devs else None)), ssid
    return iface, None


async def verify_manual_cpe_mgmt_wifi(config: CpeApi24Config) -> PcWifiJoinResult:
    """Before API_01: print PC active Wi‑Fi SSID; confirm CPE API at 169.254.254.1 is up."""
    iface_hint = config.wifi_interface
    iface: str | None = None
    active_ssid: str | None = None

    if _nmcli_available():
        try:
            iface = await resolve_wifi_interface(iface_hint)
            iface, active_ssid = await get_active_wifi_ssid(iface)
        except RuntimeError:
            pass

    print(
        f"\n[Step 1] PC Wi‑Fi (manual join — no mgmt SSID/password on CLI required)\n"
        f"  Connected SSID:  {active_ssid or '(could not read — join CPE mgmt Wi‑Fi)'}\n"
        f"  Wi‑Fi NIC:       {iface or '(unknown)'}\n"
        f"  CPE API target:  {config.base_url}\n"
    )

    expected = config.cpe_mgmt_ssid.strip()
    if expected and active_ssid and active_ssid != expected:
        raise RuntimeError(
            f"PC is on SSID '{active_ssid}', but --cpe-24-mgmt-ssid expects '{expected}'."
        )

    if active_ssid is None and _nmcli_available():
        print("  Note: nmcli did not report an active SSID; continuing if CPE API responds.")

    if not await wait_for_mgmt_api_reachable(config.base_url, attempts=15, delay_s=2.0):
        raise RuntimeError(
            f"CPE API {config.base_url} is not reachable.\n"
            "  1. Connect PC to CPE 2.4 GHz hidden mgmt Wi‑Fi manually.\n"
            f"  2. Confirm curl {config.base_url}/api/v1/cpe/sw-version returns 200.\n"
            "  3. Re-run pytest."
        )

    shown = active_ssid or expected or "(API reachable)"
    print(f"[Step 1] OK — connected SSID '{shown}'; ready for API_01 at {config.base_url}\n")
    return PcWifiJoinResult(
        connected=True,
        interface=iface,
        ssid=active_ssid or expected or None,
        method="manual",
    )


async def connect_pc_to_cpe_hidden_wifi(
    interface: str,
    creds: CpeMgmtWifiCredentials,
    *,
    connection_name: str = DEFAULT_NMCLI_CON_NAME,
    settle_s: float = 8.0,
    bts_ssid: str | None = None,
    quiet: bool = False,
) -> str:
    """Join PC to CPE 2.4 GHz hidden mgmt Wi‑Fi (not the BTS btsconnect network)."""
    if not _nmcli_available():
        raise RuntimeError(
            "nmcli not found. Join CPE mgmt Wi‑Fi manually (default) or install NetworkManager."
        )

    bts = (bts_ssid or "").strip()
    if bts and creds.ssid.strip() == bts:
        raise RuntimeError(
            f"Refusing nmcli join: {creds.ssid!r} is the BTS SSID — use CPE mgmt AP SSID only."
        )

    iface = await resolve_wifi_interface(interface)
    await _run_subprocess("nmcli", "radio", "wifi", "on", timeout_s=30)
    await _run_subprocess("nmcli", "device", "set", iface, "managed", "yes", timeout_s=30)
    await _run_subprocess("nmcli", "connection", "delete", connection_name, timeout_s=30)
    await _run_subprocess("nmcli", "device", "wifi", "rescan", timeout_s=45)
    if iface:
        await _run_subprocess("nmcli", "device", "wifi", "rescan", iface, timeout_s=45)
    await asyncio.sleep(3)

    connect_args = [
        "nmcli",
        "device",
        "wifi",
        "connect",
        creds.ssid,
        "password",
        creds.password,
    ]
    if creds.hidden:
        connect_args.extend(["hidden", "yes"])
    connect_args.extend(["ifname", iface])

    code, out = await _run_subprocess(*connect_args, timeout_s=90)
    if code != 0:
        raise RuntimeError(
            f"nmcli device wifi connect failed ({code}) on {iface}: {out[-600:]}\n"
            f"Available Wi‑Fi devices: {await list_nmcli_wifi_devices()}"
        )

    await asyncio.sleep(settle_s)
    _, active_ssid = await get_active_wifi_ssid(iface)
    if bts and active_ssid == bts:
        raise RuntimeError(
            f"PC joined BTS Wi‑Fi {bts!r} instead of CPE mgmt — disconnect and join CPE AP {creds.ssid!r}."
        )
    if not quiet and active_ssid and active_ssid != creds.ssid:
        print(
            f"    -> [WARN] PC SSID is {active_ssid!r} (expected CPE mgmt {creds.ssid!r}); "
            "checking API reachability..."
        )

    if not await is_mgmt_api_reachable():
        for _ in range(15):
            await asyncio.sleep(2)
            if await is_mgmt_api_reachable():
                if not quiet:
                    print(
                        f"    -> PC on CPE mgmt Wi‑Fi {creds.ssid!r} — "
                        f"API reachable at {DEFAULT_CPE_API_24_BASE}"
                    )
                return iface
        raise RuntimeError(
            f"Joined CPE mgmt '{creds.ssid}' on {iface} but {DEFAULT_CPE_API_24_BASE} is still unreachable."
        )
    if not quiet:
        print(f"    -> PC on CPE mgmt Wi‑Fi {creds.ssid!r} (not BTS) — API OK")
    return iface


async def disconnect_pc_cpe_wifi(
    connection_name: str = DEFAULT_NMCLI_CON_NAME,
    *,
    wifi_interface: str | None = None,
) -> None:
    if not _nmcli_available():
        return
    if wifi_interface:
        await _run_subprocess("nmcli", "device", "disconnect", wifi_interface, timeout_s=30)
    await _run_subprocess("nmcli", "connection", "down", connection_name, timeout_s=30)
    await _run_subprocess("nmcli", "connection", "delete", connection_name, timeout_s=30)


async def ensure_pc_on_cpe_mgmt_wifi(
    config: CpeApi24Config,
    *,
    wifi_interface: str | None,
    auto_join_wifi: bool,
) -> PcWifiJoinResult:
    """Default: manual join + verify. Optional: nmcli auto-join with --cpe-24-auto-join-wifi."""
    if auto_join_wifi:
        creds = resolve_cpe_mgmt_wifi_credentials(config)
        iface_hint = wifi_interface or config.wifi_interface
        print(
            f"[Step 1] Auto-join hidden SSID '{creds.ssid}' "
            f"(Wi‑Fi NIC: {iface_hint or 'auto-detect'})..."
        )
        used_iface = await connect_pc_to_cpe_hidden_wifi(
            iface_hint or "",
            creds,
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
        )
        print(f"[Step 1] Ready on {used_iface} — API tests use {config.base_url}")
        return PcWifiJoinResult(
            connected=True,
            interface=used_iface,
            ssid=creds.ssid,
            method="nmcli",
        )

    return await verify_manual_cpe_mgmt_wifi(config)


async def ensure_pc_mgmt_api_ready(
    config: CpeApi24Config,
    *,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
    auto_join_wifi: bool = False,
    label: str = "API",
) -> None:
    """
    Ensure PC can reach CPE mgmt API (169.254.254.1).
    After API_12 the CPE mgmt AP SSID changes — rejoin CPE 2.4 GHz AP only (not BTS HTDGTYWL).
    """
    if await is_mgmt_api_reachable(config.base_url, timeout_s=3.0):
        return

    bts_ssid = (config.bts_ssid or "").strip()
    print(
        f"\n[{label}] CPE mgmt API unreachable — rejoin CPE 2.4 GHz mgmt Wi‑Fi "
        f"(not BTS SSID {bts_ssid!r})..."
    )

    if auto_join_wifi and config.cpe_mgmt_ssid and config.cpe_mgmt_password:
        creds = resolve_cpe_mgmt_wifi_credentials(config)
        iface = await connect_pc_to_cpe_hidden_wifi(
            config.wifi_interface or "",
            creds,
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=bts_ssid,
        )
        print(f"    -> Rejoined CPE mgmt Wi‑Fi '{creds.ssid}' on {iface}.")
        return

    if bts_host and bts_password:
        from utils.cpe_api_24_lab import fetch_cpe_mgmt_ap_credentials

        creds = await fetch_cpe_mgmt_ap_credentials(
            bts_host=bts_host,
            username=bts_username,
            password=bts_password,
            bts_ssid=bts_ssid,
        )
        if not _nmcli_available():
            raise RuntimeError(
                f"[{label}] CPE mgmt API down and nmcli unavailable.\n"
                f"  New CPE mgmt AP SSID: {creds.ssid!r} (BTS SSID is {bts_ssid!r} — do not use that)\n"
                "  Join the CPE mgmt hidden SSID manually on the PC Wi‑Fi NIC."
            )
        iface = await connect_pc_to_cpe_hidden_wifi(
            config.wifi_interface or "",
            CpeMgmtWifiCredentials(ssid=creds.ssid, password=creds.password, hidden=creds.hidden),
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=bts_ssid,
        )
        print(f"    -> Rejoined new CPE mgmt Wi‑Fi '{creds.ssid}' on {iface}.")
        return

    raise RuntimeError(
        f"[{label}] CPE API {config.base_url} unreachable.\n"
        "  After preserveConfig=false the CPE mgmt AP SSID/password changes (not the BTS SSID).\n"
        "  Rejoin the new CPE 2.4 GHz mgmt hidden Wi‑Fi manually, or run with --local-ipv6 "
        "so tests can read CPE mgmt AP uci and nmcli connect."
    )


async def rejoin_cpe_mgmt_wifi(
    config: CpeApi24Config,
    creds: CpeMgmtWifiCredentials,
    *,
    label: str = "API",
    quiet: bool = False,
) -> str:
    """nmcli join to CPE mgmt AP only (never BTS btsconnect SSID)."""
    bts_ssid = (config.bts_ssid or "").strip()
    if not _nmcli_available():
        raise RuntimeError(
            f"[{label}] nmcli unavailable — join CPE mgmt SSID {creds.ssid!r} manually "
            f"(not BTS {bts_ssid!r})."
        )
    iface = await connect_pc_to_cpe_hidden_wifi(
        config.wifi_interface or "",
        creds,
        connection_name=config.wifi_connection_name,
        settle_s=config.wifi_settle_s,
        bts_ssid=bts_ssid,
        quiet=quiet,
    )
    if not quiet:
        print(f"    -> [{label}] CPE mgmt Wi‑Fi '{creds.ssid}' on {iface} (not BTS).")
    return iface
