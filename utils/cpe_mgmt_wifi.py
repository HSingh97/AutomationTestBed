"""
Step 1 — PC on CPE hidden 2.4 GHz management Wi‑Fi (manual join by default).

Join CPE mgmt Wi‑Fi on the PC yourself. Tests print the active SSID and check 169.254.254.1.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from dataclasses import dataclass

import httpx

from utils.cpe_api_24_config import DEFAULT_CPE_API_24_BASE, CPE_24_MGMT_API_HOST, CpeApi24Config

DEFAULT_NMCLI_CON_NAME = "KWDEJPOQ"  # same as CPE hidden mgmt SSID (see RADIO_24_SCAN_DEFAULTS)


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


async def get_wifi_device_association(
    iface: str,
) -> tuple[str, str | None, str | None]:
    """Return (nmcli GENERAL.STATE, SSID, connection name) for a Wi‑Fi NIC."""
    code, out = await _run_subprocess(
        "nmcli",
        "-t",
        "-f",
        "GENERAL.STATE,GENERAL.CONNECTION",
        "device",
        "show",
        iface,
        timeout_s=15,
    )
    state = ""
    con_name: str | None = None
    if code == 0:
        for line in out.splitlines():
            if line.startswith("GENERAL.STATE:"):
                state = line.split(":", 1)[1].strip()
            elif line.startswith("GENERAL.CONNECTION:"):
                raw = line.split(":", 1)[1].strip()
                con_name = raw if raw and raw != "--" else None

    ssid: str | None = None
    if con_name:
        code2, out2 = await _run_subprocess(
            "nmcli",
            "-g",
            "802-11-wireless.ssid",
            "connection",
            "show",
            con_name,
            timeout_s=15,
        )
        if code2 == 0 and out2.strip():
            ssid = out2.strip()
    if not ssid and con_name and nmcli_wifi_is_linked_state(state):
        ssid = con_name
    return state, ssid, con_name


def nmcli_wifi_is_linked_state(state: str) -> bool:
    """
    True when the NIC is L2-associated (not disconnected/unavailable).

    nmcli states look like ``100 (connected)`` or ``30 (disconnected)`` — never use
    ``'connected' in state`` because that matches *disconnected*.
    """
    lower = (state or "").lower()
    if not lower or "unavailable" in lower:
        return False
    if "(disconnected)" in lower or re.match(r"^\s*30\b", lower):
        return False
    if "(connected)" in lower:
        return True
    return "getting ip" in lower or "configuring" in lower or "(connecting)" in lower


def nmcli_wifi_on_expected_ssid(
    state: str,
    active_ssid: str | None,
    expected_ssid: str,
    *,
    connection_name: str | None = None,
) -> bool:
    """True when linked to the expected hidden/open SSID."""
    if not nmcli_wifi_is_linked_state(state):
        return False
    expected = expected_ssid.strip()
    if not expected:
        return False
    if active_ssid == expected:
        return True
    return connection_name == expected


async def get_active_wifi_ssid(interface: str | None = None) -> tuple[str | None, str | None]:
    """
    Return (interface, active_ssid) for the Wi‑Fi NIC in use.

    Uses nmcli device state (not substring heuristics) so disconnected NICs are not
    mistaken for connected.
    """
    if not _nmcli_available():
        return None, None

    iface = interface
    if not iface:
        wifi_devs = await list_nmcli_wifi_devices()
        iface = wifi_devs[0] if wifi_devs else None
    if not iface:
        return None, None

    state, ssid, _ = await get_wifi_device_association(iface)
    if nmcli_wifi_is_linked_state(state) and ssid:
        return iface, ssid

    code, out = await _run_subprocess("nmcli", "-t", "-f", "active,ssid", "dev", "wifi", timeout_s=15)
    if code == 0:
        for line in out.splitlines():
            if line.startswith("yes:"):
                scanned = line.split(":", 1)[1].strip()
                if scanned:
                    return iface, scanned
    return iface, None


async def _iface_has_mgmt_ipv4(iface: str, *, prefix: str = "169.254.") -> bool:
    code, out = await _run_subprocess("ip", "-4", "addr", "show", iface, timeout_s=15)
    if code != 0:
        return False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("inet ") and prefix in line:
            return True
    return False


async def verify_pc_reaches_cpe_ssh_mgmt_wifi_only(config: CpeApi24Config) -> None:
    """SSH to CPE at 169.254.254.1 only — proves mgmt Wi‑Fi path, not IPv6 fallback."""
    import asyncssh

    from utils.cpe_api_24_lab import _assert_ssh_session_is_cpe

    if not config.cpe_ssh_password:
        return
    async with asyncssh.connect(
        config.cpe_ssh_host,
        username=config.cpe_ssh_user,
        password=str(config.cpe_ssh_password),
        known_hosts=None,
        connect_timeout=8.0,
    ) as conn:
        await _assert_ssh_session_is_cpe(conn, context=f"PC mgmt Wi‑Fi → {config.cpe_ssh_host}")


async def verify_cpe_mgmt_wifi_linked(
    config: CpeApi24Config,
    creds: CpeMgmtWifiCredentials,
    *,
    iface: str | None = None,
    settle_s: float | None = None,
    require_api: bool = True,
) -> bool:
    """
    True when the PC Wi‑Fi NIC is associated to CPE mgmt with 169.254.x (and API up if required).

    Does not treat IPv6 SSH or stale connection profiles as a Wi‑Fi join.
    """
    if not _nmcli_available():
        return False

    wifi_iface = await resolve_wifi_interface(iface or config.wifi_interface or "")
    wait_s = settle_s if settle_s is not None else max(config.wifi_settle_s, 8.0)
    deadline = time.monotonic() + wait_s

    while time.monotonic() < deadline:
        state, ssid, con_name = await get_wifi_device_association(wifi_iface)
        if not nmcli_wifi_on_expected_ssid(state, ssid, creds.ssid, connection_name=con_name):
            await asyncio.sleep(1)
            continue
        if not await _iface_has_mgmt_ipv4(wifi_iface):
            await asyncio.sleep(1)
            continue
        if require_api and not await is_mgmt_api_reachable(config.base_url, timeout_s=3.0):
            await asyncio.sleep(1)
            continue
        try:
            await verify_pc_reaches_cpe_ssh_mgmt_wifi_only(config)
        except Exception:
            await asyncio.sleep(1)
            continue
        return True
    return False


async def verify_pc_reaches_cpe_ssh(config: CpeApi24Config) -> None:
    """169.254.254.1 is shared by BTS and CPE lan24 — confirm SSH session is the CPE."""
    import asyncssh

    from utils.cpe_api_24_lab import _assert_ssh_session_is_cpe, _ssh_session_device_role

    if not config.cpe_ssh_password:
        return

    async def _check_host(host: str, label: str) -> bool:
        try:
            async with asyncssh.connect(
                host,
                username=config.cpe_ssh_user,
                password=str(config.cpe_ssh_password),
                known_hosts=None,
                connect_timeout=8.0,
            ) as conn:
                await _assert_ssh_session_is_cpe(conn, context=f"PC Wi‑Fi → {label}")
                return True
        except Exception:
            return False

    if await _check_host(config.cpe_ssh_host, config.cpe_ssh_host):
        return
    fallback = (config.cpe_ssh_fallback_host or "").strip()
    if fallback and await _check_host(fallback, fallback):
        return

    try:
        async with asyncssh.connect(
            config.cpe_ssh_host,
            username=config.cpe_ssh_user,
            password=str(config.cpe_ssh_password),
            known_hosts=None,
            connect_timeout=8.0,
        ) as conn:
            role = await _ssh_session_device_role(conn)
            if role == "bts":
                ssid = await conn.run(
                    "uci -q get wireless.@wifi-iface[0].ssid", check=False
                )
                active = (ssid.stdout or "").strip()
                raise RuntimeError(
                    f"PC Wi‑Fi is on BTS lan24 mgmt ({active!r}), not CPE hidden mgmt.\n"
                    f"  Disconnect from BTS UBRMGMT* SSIDs and join CPE mgmt "
                    f"{config.cpe_mgmt_ssid!r} (hidden)."
                )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Cannot verify CPE SSH at {config.cpe_ssh_host}: {exc}\n"
            f"  Join CPE hidden mgmt Wi‑Fi {config.cpe_mgmt_ssid!r} on the PC."
        ) from exc
    raise RuntimeError(
        f"SSH at {config.cpe_ssh_host} is not the CPE.\n"
        f"  Join CPE hidden mgmt Wi‑Fi {config.cpe_mgmt_ssid!r} (not BTS UBRMGMT*)."
    )


def _mgmt_fetch_kwargs(
    config: CpeApi24Config,
    *,
    bts_host: str | None,
    bts_username: str,
    bts_password: str,
) -> dict:
    return {
        "username": bts_username,
        "password": bts_password,
        "bts_host": bts_host,
        "bts_ssid": (config.bts_ssid or "").strip() or None,
        "cpe_ipv6": (config.cpe_ssh_fallback_host or "").strip() or None,
        "known_mgmt_ssid": (config.cpe_mgmt_ssid or "").strip() or None,
    }


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

    from utils.cpe_api_24_lab import _is_bts_like_mgmt_ssid

    expected = config.cpe_mgmt_ssid.strip()
    if active_ssid and _is_bts_like_mgmt_ssid(active_ssid, bts_ssid=config.bts_ssid):
        raise RuntimeError(
            f"PC is on BTS 2.4 GHz Wi‑Fi '{active_ssid}' — join CPE hidden mgmt "
            f"{expected or 'KWDEJPOQ'} instead (not UBRMGMT* / BTS SSID)."
        )

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

    await verify_pc_reaches_cpe_ssh(config)

    shown = active_ssid or expected or "(API reachable)"
    print(f"[Step 1] OK — connected SSID '{shown}'; ready for API_01 at {config.base_url}\n")
    return PcWifiJoinResult(
        connected=True,
        interface=iface,
        ssid=active_ssid or expected or None,
        method="manual",
    )


async def _wait_for_iface_ipv4(
    iface: str,
    *,
    prefix: str = "169.254.",
    timeout_s: float = 30.0,
) -> str | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code, out = await _run_subprocess("ip", "-4", "addr", "show", iface, timeout_s=15)
        if code == 0:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("inet ") and prefix in line:
                    return line.split()[1].split("/")[0]
        await asyncio.sleep(2)
    return None


async def _create_cpe_hidden_wifi_profile(
    iface: str,
    creds: CpeMgmtWifiCredentials,
    *,
    connection_name: str,
    bssid: str | None,
) -> tuple[int, str]:
    """Create nmcli profile for hidden CPE mgmt — con-name is the SSID (scan cases 02+)."""
    profile_name = creds.ssid
    for name in {profile_name, connection_name, "radio24-hidden-mgmt", "cpe-api-24-mgmt"}:
        await _run_subprocess("nmcli", "connection", "delete", name, timeout_s=30)

    add_args = [
        "nmcli",
        "connection",
        "add",
        "type",
        "wifi",
        "con-name",
        profile_name,
        "ifname",
        iface,
        "ssid",
        creds.ssid,
        "802-11-wireless.hidden",
        "yes",
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.proto",
        "rsn,wpa",
        "wifi-sec.pairwise",
        "ccmp,tkip",
        "wifi-sec.group",
        "ccmp,tkip",
        "wifi-sec.psk",
        creds.password,
    ]
    if bssid:
        add_args.extend(["802-11-wireless.bssid", bssid.upper()])

    return await _run_subprocess(*add_args, timeout_s=90)


async def ensure_cpe_hidden_mgmt_profile(
    iface: str,
    creds: CpeMgmtWifiCredentials,
    *,
    connection_name: str,
    bssid: str | None,
) -> bool:
    """Ensure nmcli profile exists with optional BSSID; skip recreate when already correct."""
    profile_name = creds.ssid
    if bssid:
        code, out = await _run_subprocess(
            "nmcli",
            "-g",
            "802-11-wireless.bssid",
            "connection",
            "show",
            profile_name,
            timeout_s=15,
        )
        if code == 0 and out.strip().upper() == bssid.upper():
            return True
    code, _ = await _create_cpe_hidden_wifi_profile(
        iface, creds, connection_name=connection_name, bssid=bssid
    )
    return code == 0


def _normalize_nmcli_bssid(raw: str) -> str:
    return (raw or "").replace("\\", "").upper().strip()


async def _prepare_local_wifi_for_join(iface: str, *, reset_link: bool = False) -> None:
    """Prepare PC Wi‑Fi NIC before hidden join (radio on, managed; optional disconnect)."""
    await _run_subprocess("nmcli", "radio", "wifi", "on", timeout_s=30)
    await _run_subprocess("nmcli", "device", "set", iface, "managed", "yes", timeout_s=30)
    if reset_link:
        await _run_subprocess("nmcli", "device", "disconnect", iface, timeout_s=30)
        await _run_subprocess("ip", "-4", "addr", "flush", "dev", iface, timeout_s=15)
        await asyncio.sleep(2)


async def _local_wifi_list_has_bssid(iface: str, bssid: str) -> bool:
    """Return True when local nmcli Wi‑Fi list includes the CPE AP BSSID."""
    target = _normalize_nmcli_bssid(bssid)
    await _run_subprocess("nmcli", "device", "wifi", "rescan", timeout_s=45)
    if iface:
        await _run_subprocess("nmcli", "device", "wifi", "rescan", iface, timeout_s=45)
    await asyncio.sleep(4)
    code, out = await _run_subprocess(
        "nmcli",
        "-t",
        "-f",
        "BSSID",
        "device",
        "wifi",
        "list",
        "ifname",
        iface,
        timeout_s=30,
    )
    if code != 0:
        return False
    for line in (out or "").splitlines():
        if target and target in _normalize_nmcli_bssid(line):
            return True
    return False


async def try_rejoin_cpe_mgmt_wifi(
    config: CpeApi24Config,
    creds: CpeMgmtWifiCredentials,
    *,
    bssid: str | None = None,
    ensure_profile: bool = False,
    reset_link: bool = False,
    require_api: bool = True,
    max_attempts: int = 3,
) -> bool:
    """
    Best-effort hidden rejoin after CPE reboot (same pattern as scan cases 02+).

    Hidden SSIDs use saved profile + BSSID; tries connection up even when BSSID
    is not yet visible in nmcli scan (common right after CPE boot).
    """
    if not _nmcli_available():
        return False

    iface = await resolve_wifi_interface(config.wifi_interface or "")
    profile_name = creds.ssid

    await _prepare_local_wifi_for_join(iface, reset_link=reset_link)

    if ensure_profile or bssid:
        if not await ensure_cpe_hidden_mgmt_profile(
            iface,
            creds,
            connection_name=config.wifi_connection_name,
            bssid=bssid,
        ):
            return False

    if bssid:
        await _local_wifi_list_has_bssid(iface, bssid)
    else:
        await _run_subprocess("nmcli", "device", "wifi", "rescan", iface, timeout_s=45)
        await asyncio.sleep(3)

    for attempt in range(max(1, max_attempts)):
        code, _ = await _run_subprocess(
            "nmcli",
            "-w",
            "45",
            "connection",
            "up",
            profile_name,
            "ifname",
            iface,
            timeout_s=65,
        )
        if code == 0:
            if await verify_cpe_mgmt_wifi_linked(
                config,
                creds,
                iface=iface,
                settle_s=max(config.wifi_settle_s + 15, 25),
                require_api=require_api,
            ):
                return True
        if bssid:
            await _local_wifi_list_has_bssid(iface, bssid)
        else:
            await _run_subprocess("nmcli", "device", "wifi", "rescan", iface, timeout_s=45)
            await asyncio.sleep(3)
    return False


async def _join_cpe_hidden_wifi_profile(
    iface: str,
    creds: CpeMgmtWifiCredentials,
    *,
    connection_name: str,
    bssid: str | None,
    quiet: bool,
) -> tuple[int, str]:
    """Hidden CPE mgmt join via nmcli profile + connection up."""
    profile_name = creds.ssid
    code, out = await _create_cpe_hidden_wifi_profile(
        iface, creds, connection_name=connection_name, bssid=bssid
    )
    if code != 0:
        return code, out

    last_code, last_out = 1, out
    for attempt in range(4):
        await _run_subprocess("nmcli", "device", "wifi", "rescan", timeout_s=45)
        if iface:
            await _run_subprocess("nmcli", "device", "wifi", "rescan", iface, timeout_s=45)
        await asyncio.sleep(2)
        last_code, last_out = await _run_subprocess(
            "nmcli",
            "-w",
            "45",
            "connection",
            "up",
            profile_name,
            "ifname",
            iface,
            timeout_s=65,
        )
        if last_code == 0:
            if not quiet:
                print(f"    -> nmcli connection up '{profile_name}' on {iface} (attempt {attempt + 1})")
            return 0, last_out
    return last_code, last_out


async def _pc_needs_cpe_mgmt_join(
    config: CpeApi24Config,
    *,
    wifi_interface: str | None,
) -> tuple[bool, str | None, str | None]:
    """True when PC must nmcli-join CPE hidden mgmt (wrong SSID, BTS SSID, or not verified CPE)."""
    from utils.cpe_api_24_lab import _is_bts_like_mgmt_ssid

    if not _nmcli_available():
        return not await is_mgmt_api_reachable(config.base_url, timeout_s=3.0), None, None

    iface = await resolve_wifi_interface(wifi_interface or config.wifi_interface or "")
    creds = resolve_cpe_mgmt_wifi_credentials(config)
    if await verify_cpe_mgmt_wifi_linked(config, creds, iface=iface, settle_s=5.0):
        return False, iface, creds.ssid

    _, active_ssid = await get_active_wifi_ssid(iface)
    expected = (config.cpe_mgmt_ssid or "").strip()
    bts = (config.bts_ssid or "").strip()

    if active_ssid and _is_bts_like_mgmt_ssid(active_ssid, bts_ssid=bts):
        return True, iface, active_ssid
    if expected and active_ssid and active_ssid != expected:
        return True, iface, active_ssid
    if not active_ssid:
        return True, iface, active_ssid
    if not await is_mgmt_api_reachable(config.base_url, timeout_s=3.0):
        return True, iface, active_ssid
    try:
        await verify_pc_reaches_cpe_ssh(config)
        return False, iface, active_ssid
    except RuntimeError:
        return True, iface, active_ssid


async def connect_pc_to_cpe_hidden_wifi(
    interface: str,
    creds: CpeMgmtWifiCredentials,
    *,
    connection_name: str = DEFAULT_NMCLI_CON_NAME,
    settle_s: float = 8.0,
    bts_ssid: str | None = None,
    quiet: bool = False,
    config: CpeApi24Config | None = None,
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
    cached_bssid: str | None = None,
    require_api: bool = True,
) -> str:
    """Join PC to CPE 2.4 GHz hidden mgmt Wi‑Fi (not BTS UBRMGMT* / btsconnect)."""
    from utils.cpe_api_24_lab import _is_bts_like_mgmt_ssid, fetch_cpe_mgmt_ap_bssid

    if not _nmcli_available():
        raise RuntimeError(
            "nmcli not found. Join CPE mgmt Wi‑Fi manually (default) or install NetworkManager."
        )

    bts = (bts_ssid or "").strip()
    if _is_bts_like_mgmt_ssid(creds.ssid, bts_ssid=bts):
        raise RuntimeError(
            f"Refusing nmcli join: {creds.ssid!r} is a BTS 2.4 GHz SSID — "
            "use CPE hidden mgmt AP only (e.g. KWDEJPOQ)."
        )
    if bts and creds.ssid.strip() == bts:
        raise RuntimeError(
            f"Refusing nmcli join: {creds.ssid!r} is the BTS SSID — use CPE mgmt AP SSID only."
        )

    iface = await resolve_wifi_interface(interface)
    if not quiet:
        print(f"    -> Connecting PC Wi‑Fi {iface} to CPE hidden mgmt SSID {creds.ssid!r}...")

    await _run_subprocess("nmcli", "radio", "wifi", "on", timeout_s=30)
    await _run_subprocess("nmcli", "device", "set", iface, "managed", "yes", timeout_s=30)
    await _run_subprocess("nmcli", "device", "disconnect", iface, timeout_s=30)
    await asyncio.sleep(2)

    bssid: str | None = (cached_bssid or "").strip().upper() or None
    if not bssid and config and config.cpe_ssh_password:
        bssid = await fetch_cpe_mgmt_ap_bssid(
            username=bts_username or config.cpe_ssh_user,
            password=bts_password or str(config.cpe_ssh_password),
            cpe_ipv6=config.cpe_ssh_fallback_host,
            bts_host=bts_host,
            known_mgmt_ssid=creds.ssid,
        )

    code, out = await _join_cpe_hidden_wifi_profile(
        iface,
        creds,
        connection_name=connection_name,
        bssid=bssid,
        quiet=quiet,
    )

    if code != 0 and not creds.hidden:
        if not quiet:
            print(f"    -> profile join failed ({code}); trying nmcli wifi connect...")
        connect_args = [
            "nmcli",
            "device",
            "wifi",
            "connect",
            creds.ssid,
            "password",
            creds.password,
            "ifname",
            iface,
        ]
        code, out = await _run_subprocess(*connect_args, timeout_s=90)

    if code != 0:
        raise RuntimeError(
            f"Could not join CPE mgmt SSID {creds.ssid!r} on {iface} ({code}): {out[-600:]}\n"
            f"Available Wi‑Fi devices: {await list_nmcli_wifi_devices()}"
        )

    if config is None:
        await asyncio.sleep(settle_s)
        return iface

    if not await verify_cpe_mgmt_wifi_linked(
        config,
        creds,
        iface=iface,
        settle_s=max(settle_s + 20, 30),
        require_api=require_api,
    ):
        state, ssid, con_name = await get_wifi_device_association(iface)
        raise RuntimeError(
            f"CPE mgmt Wi‑Fi join not verified on {iface}.\n"
            f"  nmcli state: {state!r}, ssid: {ssid!r}, profile: {con_name!r}\n"
            f"  Expected linked hidden SSID {creds.ssid!r} with 169.254.x and "
            f"{config.base_url} reachable."
        )

    iface_ip = await _wait_for_iface_ipv4(iface, timeout_s=5)
    _, active_ssid = await get_active_wifi_ssid(iface)
    if bts and active_ssid == bts:
        raise RuntimeError(
            f"PC joined BTS Wi‑Fi {bts!r} instead of CPE mgmt — disconnect and join CPE AP {creds.ssid!r}."
        )
    if active_ssid and _is_bts_like_mgmt_ssid(active_ssid, bts_ssid=bts):
        raise RuntimeError(
            f"PC joined BTS Wi‑Fi {active_ssid!r} instead of CPE mgmt {creds.ssid!r}."
        )

    if not quiet:
        print(
            f"    -> PC on CPE mgmt Wi‑Fi {creds.ssid!r} on {iface}"
            + (f" ({iface_ip})" if iface_ip else "")
            + " — verified CPE (not BTS)"
        )
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
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
) -> PcWifiJoinResult:
    """
    Ensure PC can reach CPE mgmt API at 169.254.254.1.

    Auto nmcli-join CPE hidden mgmt when PC is disconnected, on BTS SSID, or SSH is not CPE.
    """
    needs_join, iface, active_ssid = await _pc_needs_cpe_mgmt_join(
        config, wifi_interface=wifi_interface
    )
    if not needs_join:
        return await verify_manual_cpe_mgmt_wifi(config)

    if active_ssid:
        print(
            f"\n[Step 1] PC on {active_ssid!r} — must join CPE 2.4 GHz hidden mgmt "
            f"{config.cpe_mgmt_ssid!r} (not BTS UBRMGMT*)..."
        )
    elif not await is_mgmt_api_reachable(config.base_url, timeout_s=3.0):
        print("\n[Step 1] CPE mgmt API unreachable — nmcli join CPE 2.4 GHz hidden mgmt...")

    if auto_join_wifi or (config.cpe_mgmt_ssid and config.cpe_mgmt_password != ""):
        creds = resolve_cpe_mgmt_wifi_credentials(config)
        iface_hint = wifi_interface or config.wifi_interface
        print(
            f"[Step 1] nmcli join CPE hidden SSID '{creds.ssid}' "
            f"(Wi‑Fi NIC: {iface_hint or 'auto-detect'})..."
        )
        used_iface = await connect_pc_to_cpe_hidden_wifi(
            iface_hint or "",
            creds,
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=(config.bts_ssid or "").strip() or None,
            config=config,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
        )
        print(f"[Step 1] Ready on {used_iface} — API tests use {config.base_url}\n")
        return PcWifiJoinResult(
            connected=True,
            interface=used_iface,
            ssid=creds.ssid,
            method="nmcli",
        )

    if bts_host and bts_password:
        from utils.cpe_api_24_lab import fetch_cpe_mgmt_ap_credentials

        bts_ssid = (config.bts_ssid or "").strip()
        print(
            f"\n[Step 1] CPE mgmt API unreachable — fetch CPE mgmt AP from BTS SSH "
            f"and nmcli join (not BTS SSID {bts_ssid!r})..."
        )
        creds = await fetch_cpe_mgmt_ap_credentials(
            **_mgmt_fetch_kwargs(
                config,
                bts_host=bts_host,
                bts_username=bts_username,
                bts_password=bts_password,
            )
        )
        if not _nmcli_available():
            raise RuntimeError(
                "CPE mgmt API unreachable and nmcli is not available.\n"
                f"  Join hidden SSID {creds.ssid!r} manually, then re-run."
            )
        used_iface = await connect_pc_to_cpe_hidden_wifi(
            wifi_interface or config.wifi_interface or "",
            CpeMgmtWifiCredentials(
                ssid=creds.ssid,
                password=creds.password,
                hidden=creds.hidden,
            ),
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=bts_ssid or None,
            config=config,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
        )
        print(
            f"[Step 1] OK — joined CPE mgmt SSID '{creds.ssid}' on {used_iface}; "
            f"API at {config.base_url}\n"
        )
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
        await verify_pc_reaches_cpe_ssh(config)
        return

    bts_ssid = (config.bts_ssid or "").strip()
    print(
        f"\n[{label}] CPE mgmt API unreachable — rejoin CPE 2.4 GHz mgmt Wi‑Fi "
        f"(not BTS SSID {bts_ssid!r})..."
    )

    if config.cpe_mgmt_ssid and config.cpe_mgmt_password != "":
        creds = resolve_cpe_mgmt_wifi_credentials(config)
        iface = await connect_pc_to_cpe_hidden_wifi(
            config.wifi_interface or "",
            creds,
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=bts_ssid,
            config=config,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
        )
        print(f"    -> Rejoined CPE mgmt Wi‑Fi '{creds.ssid}' on {iface}.")
        return

    if auto_join_wifi and config.cpe_mgmt_ssid and config.cpe_mgmt_password:
        creds = resolve_cpe_mgmt_wifi_credentials(config)
        iface = await connect_pc_to_cpe_hidden_wifi(
            config.wifi_interface or "",
            creds,
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=bts_ssid,
            config=config,
        )
        print(f"    -> Rejoined CPE mgmt Wi‑Fi '{creds.ssid}' on {iface}.")
        return

    if bts_host and bts_password:
        from utils.cpe_api_24_lab import fetch_cpe_mgmt_ap_credentials

        creds = await fetch_cpe_mgmt_ap_credentials(
            **_mgmt_fetch_kwargs(
                config,
                bts_host=bts_host,
                bts_username=bts_username,
                bts_password=bts_password,
            )
        )
        if not _nmcli_available():
            raise RuntimeError(
                f"[{label}] CPE mgmt API down and nmcli unavailable.\n"
                f"  CPE mgmt AP SSID: {creds.ssid!r} (BTS SSID is {bts_ssid!r} — do not use that)\n"
                "  Join the CPE mgmt hidden SSID manually on the PC Wi‑Fi NIC."
            )
        iface = await connect_pc_to_cpe_hidden_wifi(
            config.wifi_interface or "",
            CpeMgmtWifiCredentials(ssid=creds.ssid, password=creds.password, hidden=creds.hidden),
            connection_name=config.wifi_connection_name,
            settle_s=config.wifi_settle_s,
            bts_ssid=bts_ssid,
            config=config,
            bts_host=bts_host,
            bts_username=bts_username,
            bts_password=bts_password,
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
    bts_host: str | None = None,
    bts_username: str = "root",
    bts_password: str = "",
    cached_bssid: str | None = None,
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
        config=config,
        bts_host=bts_host,
        bts_username=bts_username,
        bts_password=bts_password,
        cached_bssid=cached_bssid,
    )
    if not quiet:
        print(f"    -> [{label}] CPE mgmt Wi‑Fi '{creds.ssid}' on {iface} (not BTS).")
    return iface
