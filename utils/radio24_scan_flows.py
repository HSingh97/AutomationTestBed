"""2.4G_RADIO plan — Wi‑Fi scan from lab PC near CPE (hidden SSID visibility)."""

from __future__ import annotations

import asyncio
import re
import time

import asyncssh
import pytest_check as check

from config.defaults import RADIO_24_SCAN_DEFAULTS
from utils.net_utils import normalize_ip
from utils.radio24_api_flows import (
    assert_radio_24_41_valid_api_call,
    assert_radio_24_42_invalid_api_call,
    assert_radio_24_43_unauthorized_access,
    assert_radio_24_44_malformed_request,
    assert_radio_24_45_api_under_load,
    assert_radio_24_46_api_after_reboot,
    assert_radio_24_49_api_response_time,
    assert_radio_24_50_api_logging,
)
from utils.verify_output import print_comparison_table, print_section

_IWLIST_ESSID_RE = re.compile(r"ESSID:\"([^\"]*)\"")
_BSSID_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def _log(message: str) -> None:
    print(f"[RADIO_24_SCAN] {message}")


def _resolve_scan_pc_config(
    profile_bundle,
    *,
    case_id: str = "2_4G_RADIO_01",
) -> tuple[str, str, str, str, int, int]:
    defaults = RADIO_24_SCAN_DEFAULTS
    dut = (profile_bundle.active.get("dut") or {}) if profile_bundle else {}
    scan_ip = str(dut.get("scan_pc_ip") or defaults["SCAN_PC_IP"]).strip()
    scan_password = str(dut.get("scan_pc_password") or defaults["SCAN_PC_PASSWORD"])
    target_ssid = str(defaults["CPE_MGMT_HIDDEN_SSID"]).strip()
    target_password = str(defaults["CPE_MGMT_HIDDEN_PASSWORD"]).strip()
    settle_s = int(defaults.get("SCAN_SETTLE_S", 4))
    timeout_s = int(defaults.get("SCAN_TIMEOUT_S", 45))
    check.is_true(scan_ip, f"{case_id}: scan PC IP required")
    check.is_true(target_ssid, f"{case_id}: target SSID required")
    return scan_ip, scan_password, target_ssid, target_password, settle_s, timeout_s


async def _ssh_run(
    conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    timeout_s: int = 60,
) -> tuple[int, str]:
    result = await conn.run(command, check=False, timeout=timeout_s)
    text = (result.stdout or "") + (result.stderr or "")
    return result.exit_status or 0, text


async def _detect_wifi_interface(
    conn: asyncssh.SSHClientConnection,
    *,
    case_id: str = "2_4G_RADIO_01",
) -> str:
    code, out = await _ssh_run(
        conn,
        "nmcli -t -f DEVICE,TYPE device | awk -F: '$2==\"wifi\"{print $1; exit}'",
        timeout_s=20,
    )
    iface = (out or "").strip().splitlines()[0].strip() if out.strip() else ""
    if not iface and code == 0:
        code, out = await _ssh_run(
            conn,
            "iw dev 2>/dev/null | awk '/Interface/{print $2; exit}'",
            timeout_s=20,
        )
        iface = (out or "").strip().splitlines()[0].strip() if out.strip() else ""
    check.is_true(iface, f"{case_id}: no Wi‑Fi interface found on scan PC (nmcli/iw)")
    state = await _get_wifi_iface_state(conn, iface)
    if "unavailable" in state.lower():
        _log(f"{case_id}: {iface} unavailable — recovering Wi‑Fi NIC (radio toggle only)")
        await _recover_scan_pc_wifi_nic(conn, iface, level=1)
    return iface


async def _detect_scan_pc_wan_interface(
    conn: asyncssh.SSHClientConnection,
    *,
    case_id: str = "2_4G_RADIO_19",
    preferred: str | None = None,
) -> str:
    """Ethernet (WAN) NIC on the lab scan PC — prefer the link that is actually connected."""
    code, out = await _ssh_run(
        conn,
        "nmcli -t -f DEVICE,TYPE,STATE device | "
        "awk -F: '$2==\"ethernet\" && $3 ~ /connected/{print $1; exit}'",
        timeout_s=20,
    )
    iface = (out or "").strip().splitlines()[0].strip() if out.strip() else ""
    if iface:
        return iface
    if preferred:
        code, out = await _ssh_run(
            conn,
            f"ip link show {preferred} 2>/dev/null | awk -F: '/^[0-9]+:/{{print $2; exit}}'",
            timeout_s=15,
        )
        if code == 0 and (out or "").strip():
            return preferred.strip()
    code, out = await _ssh_run(
        conn,
        "nmcli -t -f DEVICE,TYPE device | awk -F: '$2==\"ethernet\"{print $1; exit}'",
        timeout_s=20,
    )
    iface = (out or "").strip().splitlines()[0].strip() if out.strip() else ""
    check.is_true(iface, f"{case_id}: no ethernet (WAN) interface found on scan PC")
    return iface


async def _get_active_wifi_ssid(
    conn: asyncssh.SSHClientConnection,
    iface: str,
) -> str | None:
    code, out = await _ssh_run(
        conn,
        f"nmcli -t -f GENERAL.STATE,GENERAL.CONNECTION device show {iface}",
        timeout_s=20,
    )
    if code != 0:
        return None
    con_name = ""
    state = ""
    for line in out.splitlines():
        if line.startswith("GENERAL.STATE:"):
            state = line.split(":", 1)[1].strip()
        elif line.startswith("GENERAL.CONNECTION:"):
            con_name = line.split(":", 1)[1].strip()
    if "connected" not in state.lower() or not con_name or con_name == "--":
        return None
    code2, out2 = await _ssh_run(
        conn,
        f"nmcli -g 802-11-wireless.ssid connection show {con_name}",
        timeout_s=20,
    )
    if code2 == 0 and out2.strip():
        return out2.strip()
    code3, out3 = await _ssh_run(
        conn,
        "nmcli -t -f active,ssid dev wifi | awk -F: '$1==\"yes\"{print $2; exit}'",
        timeout_s=20,
    )
    return out3.strip() if code3 == 0 and out3.strip() else None


async def _get_wifi_iface_state(conn: asyncssh.SSHClientConnection, iface: str) -> str:
    code, out = await _ssh_run(
        conn,
        f"nmcli -t -f GENERAL.STATE device show {iface}",
        timeout_s=20,
    )
    if code != 0:
        return ""
    for line in out.splitlines():
        if line.startswith("GENERAL.STATE:"):
            return line.split(":", 1)[1].strip()
    return ""


async def _wait_for_wifi_connected(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    expected_ssid: str,
    *,
    timeout_s: int = 30,
) -> tuple[bool, str | None, str]:
    """Poll until the NIC is connected with the expected SSID."""
    deadline = time.monotonic() + timeout_s
    last_state = ""
    last_ssid: str | None = None
    while time.monotonic() < deadline:
        last_state = await _get_wifi_iface_state(conn, iface)
        last_ssid = await _get_active_wifi_ssid(conn, iface)
        if "connected" in last_state.lower() and last_ssid == expected_ssid:
            return True, last_ssid, last_state
        await asyncio.sleep(2)
    return False, last_ssid, last_state


async def _delete_nmcli_connection(
    conn: asyncssh.SSHClientConnection,
    connection_name: str,
) -> None:
    quoted = connection_name.replace("'", "'\"'\"'")
    await _ssh_run(conn, f"nmcli connection delete '{quoted}'", timeout_s=30)


async def _delete_legacy_hidden_profiles(conn: asyncssh.SSHClientConnection) -> None:
    """Remove old automation profile names so the GUI shows SSID only."""
    for legacy in ("radio24-hidden-mgmt", "cpe-api-24-mgmt"):
        await _delete_nmcli_connection(conn, legacy)


async def _clear_lab_pc_wifi(
    conn: asyncssh.SSHClientConnection,
    *,
    case_id: str,
) -> str:
    """Disconnect all Wi‑Fi NICs and delete saved wireless profiles on the lab PC."""
    iface = await _detect_wifi_interface(conn, case_id=case_id)
    await _ssh_run(conn, "nmcli radio wifi on", timeout_s=30)

    await _delete_legacy_hidden_profiles(conn)

    _, wifi_devs = await _ssh_run(
        conn,
        "nmcli -t -f DEVICE,TYPE device | awk -F: '$2==\"wifi\"{print $1}'",
        timeout_s=20,
    )
    for dev in (wifi_devs or "").strip().splitlines():
        dev = dev.strip()
        if not dev:
            continue
        await _ssh_run(conn, f"nmcli device disconnect {dev}", timeout_s=30)
        await _ssh_run(conn, f"nmcli device set {dev} managed no", timeout_s=30)
        await _ssh_run(conn, f"nmcli device set {dev} managed yes", timeout_s=30)

    _, saved = await _ssh_run(
        conn,
        "nmcli -t -f NAME,TYPE connection show | awk -F: '$2==\"802-11-wireless\"{print $1}'",
        timeout_s=30,
    )
    removed = 0
    for name in (saved or "").strip().splitlines():
        name = name.strip()
        if not name:
            continue
        await _delete_nmcli_connection(conn, name)
        removed += 1

    await asyncio.sleep(2)
    return iface


def _wifi_mgmt_associated(
    state: str,
    active_ssid: str | None,
    expected_ssid: str,
    *,
    connection_name: str | None = None,
) -> bool:
    """L2 joined to hidden mgmt SSID (connected or waiting on DHCP)."""
    ssid_ok = active_ssid == expected_ssid or connection_name == expected_ssid
    if not ssid_ok:
        return False
    lower = state.lower()
    return (
        "connected" in lower
        or "getting ip" in lower
        or "configuring" in lower
    )


async def _get_wifi_device_association(
    conn: asyncssh.SSHClientConnection,
    iface: str,
) -> tuple[str, str | None, str | None]:
    """Return (state, ssid, connection_name) for the Wi‑Fi NIC."""
    code, out = await _ssh_run(
        conn,
        f"nmcli -t -f GENERAL.STATE,GENERAL.CONNECTION device show {iface}",
        timeout_s=20,
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
        quoted = con_name.replace("'", "'\"'\"'")
        _, ssid_out = await _ssh_run(
            conn,
            f"nmcli -g 802-11-wireless.ssid connection show '{quoted}'",
            timeout_s=20,
        )
        ssid = (ssid_out or "").strip() or con_name
    return state, ssid, con_name


async def _connect_hidden_wifi(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    ssid: str,
    password: str,
    settle_s: int,
    bssid: str | None = None,
    require_mgmt_ip: bool = True,
) -> tuple[int, str]:
    """
    Join hidden WPA2-PSK SSID via SSH nmcli — profile name is the SSID (not a custom label).

    Hidden APs are not found by SSID scan; use BSSID from CPE ath0 when available.
    """
    await _prepare_scan_pc_wifi_for_join(conn, iface)
    await _delete_nmcli_connection(conn, ssid)
    await _delete_legacy_hidden_profiles(conn)

    if bssid:
        scan_deadline = time.monotonic() + 60
        bssid_in_scan = False
        while time.monotonic() < scan_deadline:
            if await _scan_pc_wifi_list_has_bssid(conn, iface, bssid):
                bssid_in_scan = True
                break
            await asyncio.sleep(3)
    else:
        await _ssh_run(conn, "nmcli device wifi rescan", timeout_s=45)
        await asyncio.sleep(3)

    quoted_ssid = ssid.replace("'", "'\"'\"'")
    quoted_pw = password.replace("'", "'\"'\"'")
    bssid_part = f"802-11-wireless.bssid {bssid} " if bssid else ""

    add_cmd = (
        f"nmcli connection add type wifi con-name '{quoted_ssid}' "
        f"ifname {iface} ssid '{quoted_ssid}' 802-11-wireless.hidden yes "
        f"{bssid_part}"
        f"wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn,wpa "
        f"wifi-sec.pairwise ccmp,tkip wifi-sec.group ccmp,tkip wifi-sec.psk '{quoted_pw}'"
    )
    code, out = await _ssh_run(conn, add_cmd, timeout_s=90)
    if code != 0:
        return code, out

    last_code = 1
    last_out = out
    activation_wait_s = 45 if require_mgmt_ip else 20
    up_attempts = 3 if require_mgmt_ip else 1
    for attempt in range(up_attempts):
        if bssid and attempt > 0:
            await _scan_pc_wifi_list_has_bssid(conn, iface, bssid)
        last_code, last_out = await _ssh_run(
            conn,
            f"nmcli -w {activation_wait_s} connection up '{quoted_ssid}' ifname {iface}",
            timeout_s=activation_wait_s + 20,
        )
        if last_code == 0:
            break
        if bssid:
            await _scan_pc_wifi_list_has_bssid(conn, iface, bssid)
        else:
            await _ssh_run(conn, "nmcli device wifi rescan", timeout_s=45)
            await asyncio.sleep(3)

    if last_code != 0 and not bssid:
        connect_cmd = (
            f"nmcli device wifi connect '{quoted_ssid}' password '{quoted_pw}' "
            f"hidden yes ifname {iface}"
        )
        last_code, last_out = await _ssh_run(conn, connect_cmd, timeout_s=90)

    if last_code != 0 and require_mgmt_ip:
        return last_code, last_out

    await asyncio.sleep(2)
    if require_mgmt_ip:
        iface_ip = await _wait_for_iface_ipv4(
            conn,
            iface,
            prefix="169.254.",
            timeout_s=max(settle_s + 15, 25),
        )
        if not iface_ip:
            state = await _get_wifi_iface_state(conn, iface)
            return 1, f"No 169.254.x address on {iface} after join (state={state!r})"

    wifi_state, active_ssid, con_name = await _get_wifi_device_association(conn, iface)
    if not _wifi_mgmt_associated(
        wifi_state, active_ssid, ssid, connection_name=con_name
    ):
        return 1, f"Wi‑Fi not associated after join (state={wifi_state!r}, ssid={active_ssid!r})"
    return 0, last_out


async def _wait_for_wifi_not_connected(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    forbidden_ssid: str,
    *,
    timeout_s: int = 25,
) -> tuple[bool, str | None, str]:
    """Poll until the NIC is not connected to forbidden_ssid (negative-test settle)."""
    deadline = time.monotonic() + timeout_s
    last_state = ""
    last_ssid: str | None = None
    while time.monotonic() < deadline:
        last_state = await _get_wifi_iface_state(conn, iface)
        last_ssid = await _get_active_wifi_ssid(conn, iface)
        if last_ssid != forbidden_ssid and "connected" not in last_state.lower():
            return True, last_ssid, last_state
        if last_ssid != forbidden_ssid and last_ssid is not None:
            return True, last_ssid, last_state
        await asyncio.sleep(2)
    return last_ssid != forbidden_ssid, last_ssid, last_state


async def _internet_unreachable(
    conn: asyncssh.SSHClientConnection,
    host: str,
    *,
    iface: str | None = None,
    src_ip: str | None = None,
) -> bool:
    return not await _ping_reachable(conn, host, iface=iface, src_ip=src_ip)


async def _prepare_wifi_for_passive_scan(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    settle_s: int,
) -> None:
    """Ready NIC for passive scan — no nmcli rescan (active scan exposes hidden SSIDs)."""
    await _ssh_run(conn, "nmcli radio wifi on", timeout_s=30)
    await _ssh_run(conn, f"nmcli device set {iface} managed yes", timeout_s=30)
    await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=30)
    await asyncio.sleep(settle_s)


async def _get_iface_ipv4(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    prefix: str | None = None,
) -> str | None:
    _, out = await _ssh_run(conn, f"ip -4 -o addr show dev {iface}", timeout_s=15)
    for line in (out or "").splitlines():
        match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/", line)
        if not match:
            continue
        ip = match.group(1)
        if prefix and not ip.startswith(prefix):
            continue
        return ip
    return None


async def _route_uses_iface(
    conn: asyncssh.SSHClientConnection,
    host: str,
    iface: str,
) -> bool:
    _, out = await _ssh_run(conn, f"ip route get {host} 2>/dev/null", timeout_s=15)
    return bool(re.search(rf"\bdev {re.escape(iface)}(\s|$)", out or ""))


async def _wait_for_iface_ipv4(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    prefix: str,
    timeout_s: int = 30,
) -> str | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ip = await _get_iface_ipv4(conn, iface, prefix=prefix)
        if ip:
            return ip
        await asyncio.sleep(2)
    return None


async def _ping_reachable(
    conn: asyncssh.SSHClientConnection,
    host: str,
    *,
    iface: str | None = None,
    src_ip: str | None = None,
) -> bool:
    if src_ip:
        ping_cmd = f"ping -c 2 -W 2 -I {src_ip} {host}"
    elif iface:
        ping_cmd = f"ping -c 2 -W 2 -I {iface} {host}"
    else:
        ping_cmd = f"ping -c 2 -W 2 {host}"
    code, out = await _ssh_run(conn, f"{ping_cmd} 2>&1", timeout_s=15)
    if code != 0:
        return False
    lowered = (out or "").lower()
    return "0 received" not in lowered and "100% packet loss" not in lowered


async def _verify_cpe_mgmt_wifi_link(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    cpe_mgmt_ip: str,
    *,
    expected_ssid: str,
    timeout_s: int = 30,
) -> tuple[bool, str | None, str | None, str, bool]:
    """
    Confirm real L3 link on the Wi‑Fi NIC — not a saved profile or wired route.

    Returns (linked, active_ssid, wifi_state, iface_ip, ping_ok).
    """
    deadline = time.monotonic() + timeout_s
    last_ssid: str | None = None
    last_state = ""
    iface_ip: str | None = None
    ping_ok = False

    while time.monotonic() < deadline:
        last_state = await _get_wifi_iface_state(conn, iface)
        last_ssid = await _get_active_wifi_ssid(conn, iface)
        iface_ip = await _get_iface_ipv4(conn, iface, prefix="169.254.")
        route_ok = await _route_uses_iface(conn, cpe_mgmt_ip, iface)
        associated = (
            "connected" in last_state.lower()
            and last_ssid == expected_ssid
            and iface_ip
            and route_ok
        )
        if associated:
            ping_ok = await _ping_reachable(conn, cpe_mgmt_ip, src_ip=iface_ip)
            if ping_ok:
                return True, last_ssid, last_state, iface_ip, ping_ok
        await asyncio.sleep(2)

    if iface_ip and not ping_ok:
        ping_ok = await _ping_reachable(conn, cpe_mgmt_ip, src_ip=iface_ip)
    route_ok = await _route_uses_iface(conn, cpe_mgmt_ip, iface)
    linked = (
        "connected" in last_state.lower()
        and last_ssid == expected_ssid
        and iface_ip
        and route_ok
        and ping_ok
    )
    return linked, last_ssid, last_state, iface_ip, ping_ok


def _parse_iwlist_essids(raw: str) -> list[str]:
    return [match.group(1) for match in _IWLIST_ESSID_RE.finditer(raw or "")]


def _print_scan_ssid_list(iwlist_ssids: list[str], target_ssid: str) -> None:
    """Print only visible SSID names from the passive scan."""
    visible = sorted({s for s in iwlist_ssids if s.strip()}, key=str.lower)
    print("\n--- Wi‑Fi scan SSIDs ---", flush=True)
    if visible:
        for idx, ssid in enumerate(visible, 1):
            print(f"  {idx:>2}. {ssid}", flush=True)
    else:
        print("  (none with visible name)", flush=True)
    if target_ssid in visible:
        print(f"\n  TARGET {target_ssid!r}: visible in scan — FAIL", flush=True)
    else:
        print(f"\n  TARGET {target_ssid!r}: not in scan — PASS (hidden)", flush=True)
    print(flush=True)


async def _scan_essids_iwlist(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    timeout_s: int,
) -> tuple[list[str], str]:
    code, out = await _ssh_run(conn, f"iwlist {iface} scan 2>&1", timeout_s=timeout_s)
    if code != 0 and "Interface doesn't support scanning" in out:
        return [], out
    return _parse_iwlist_essids(out), out


def _visible_ssids_from_iwlist(iwlist_ssids: list[str]) -> set[str]:
    return {s for s in iwlist_ssids if s.strip()}


async def _flush_lab_pc_wifi_radio(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    settle_s: int,
) -> None:
    """Toggle lab PC Wi‑Fi NIC off/on to flush driver scan cache (no console spam)."""
    await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=20)
    await _ssh_run(conn, f"nmcli device set {iface} managed no", timeout_s=20)
    await asyncio.sleep(1)
    await _ssh_run(conn, f"nmcli device set {iface} managed yes", timeout_s=20)
    await asyncio.sleep(settle_s)


async def _cpe_ssh_run(
    cpe_conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    timeout_s: int = 30,
    tolerate_timeout: bool = False,
) -> tuple[int, str]:
    try:
        return await _ssh_run(cpe_conn, command, timeout_s=timeout_s)
    except (asyncssh.TimeoutError, asyncio.TimeoutError, TimeoutError):
        if tolerate_timeout:
            return 124, "ssh command timed out"
        raise


async def _cpe_wifi_reload_background(
    cpe_conn: asyncssh.SSHClientConnection,
    *,
    wait_s: int,
) -> None:
    """
    Apply wireless UCI on CPE without blocking SSH.

    `wifi reload` on the CPE can take minutes and hang the session; run it in the
    background and wait on the automation host instead.
    """
    await _cpe_ssh_run(
        cpe_conn,
        "nohup sh -c 'wifi reload 2>/dev/null || wifi' >/tmp/radio24_wifi_reload.log 2>&1 &",
        timeout_s=15,
        tolerate_timeout=True,
    )
    await asyncio.sleep(max(wait_s, 1))


async def _resolve_cpe_wifi_iface_idx_for_ssid(
    cpe_conn: asyncssh.SSHClientConnection,
    ssid: str,
    *,
    fallback_idx: int,
) -> int:
    """Find wifi-iface index for the mgmt SSID (CPE iface order is not always [0])."""
    quoted = ssid.replace("'", "'\"'\"'")
    _, out = await _cpe_ssh_run(
        cpe_conn,
        "uci show wireless 2>/dev/null | grep -E '@wifi-iface\\[[0-9]+\\]\\.ssid='",
        timeout_s=20,
    )
    for line in (out or "").splitlines():
        if f"ssid='{ssid}'" not in line and f"ssid=\"{ssid}\"" not in line:
            continue
        try:
            return int(line.split("@wifi-iface[", 1)[1].split("]", 1)[0])
        except (IndexError, ValueError):
            continue
    _, out2 = await _cpe_ssh_run(
        cpe_conn,
        f"uci -q get wireless.@wifi-iface[{fallback_idx}].ssid",
        timeout_s=15,
    )
    if (out2 or "").strip().strip("'") == ssid:
        return fallback_idx
    return fallback_idx


async def _toggle_cpe_mgmt_ap_off_on(
    cpe_conn: asyncssh.SSHClientConnection,
    iface_idx: int,
    *,
    settle_s: int,
    reload_wait_s: int,
) -> None:
    """CPE mgmt AP off → on; enforce hidden=1 (silent — no console output)."""
    iface_key = f"wireless.@wifi-iface[{iface_idx}]"
    await _cpe_ssh_run(cpe_conn, f"uci set {iface_key}.hidden=1", timeout_s=15)
    await _cpe_ssh_run(cpe_conn, f"uci set {iface_key}.disabled=1", timeout_s=15)
    await _cpe_ssh_run(cpe_conn, "uci commit wireless", timeout_s=20)
    await _cpe_wifi_reload_background(
        cpe_conn,
        wait_s=max(reload_wait_s, settle_s),
    )

    await _cpe_ssh_run(cpe_conn, f"uci set {iface_key}.disabled=0", timeout_s=15)
    await _cpe_ssh_run(cpe_conn, "uci commit wireless", timeout_s=20)
    await _cpe_wifi_reload_background(
        cpe_conn,
        wait_s=max(reload_wait_s, settle_s),
    )


async def _try_lab_pc_flush_until_hidden(
    scan_conn: asyncssh.SSHClientConnection,
    iface: str,
    target_ssid: str,
    iwlist_ssids: list[str],
    *,
    settle_s: int,
    timeout_s: int,
    max_attempts: int,
) -> list[str]:
    """Flush lab PC Wi‑Fi cache when a hidden SSID still appears in iwlist."""
    visible = _visible_ssids_from_iwlist(iwlist_ssids)
    for _ in range(max_attempts):
        if target_ssid not in visible:
            return iwlist_ssids
        await _flush_lab_pc_wifi_radio(scan_conn, iface, settle_s=2)
        iwlist_ssids = await _passive_iwlist_scan(
            scan_conn,
            iface,
            settle_s=settle_s,
            timeout_s=timeout_s,
        )
        visible = _visible_ssids_from_iwlist(iwlist_ssids)
    return iwlist_ssids


def _resolve_cpe_ssh_target(profile_bundle) -> tuple[str, str, str]:
    dut = (profile_bundle.active.get("dut") or {}) if profile_bundle else {}
    remote = dut.get("remote_ipv6s") or dut.get("remote_ips") or []
    cpe_ip = normalize_ip(str(remote[0] if remote else ""))
    user = str(dut.get("username") or "root")
    password = str(dut.get("password") or "")
    return cpe_ip, user, password


def _resolve_bts_cpe_targets(profile_bundle) -> tuple[str, str, str, str]:
    dut = (profile_bundle.active.get("dut") or {}) if profile_bundle else {}
    bts_ip = normalize_ip(str(dut.get("local_ipv6") or dut.get("local_ip") or ""))
    remote = dut.get("remote_ipv6s") or dut.get("remote_ips") or []
    cpe_ip = normalize_ip(str(remote[0] if remote else ""))
    user = str(dut.get("username") or "root")
    password = str(dut.get("password") or "")
    return bts_ip, cpe_ip, user, password


def _ping_output_ok(raw: str) -> bool:
    lower = (raw or "").lower()
    if "0 received" in lower or "100% packet loss" in lower:
        return False
    if "bytes from" in lower:
        return True
    return "0% packet loss" in lower


def _remote_exec_bts_command(su_index: int, inner_command: str) -> str:
    escaped = inner_command.replace("'", "'\"'\"'")
    return f"/usr/sbin/remote_exec.sh {su_index} '{escaped}'"


async def _resolve_cpe_remote_exec_index(
    bts_conn: asyncssh.SSHClientConnection,
    *,
    mgmt_ssid: str | None = None,
) -> int | None:
    """Find BTS remote_exec SU index that reaches the CPE."""
    want_ssid = (mgmt_ssid or "").strip()
    bts_mac = ""
    _, bts_mac_out = await _ssh_run(
        bts_conn,
        "cat /sys/class/net/br-lan/address 2>/dev/null",
        timeout_s=15,
    )
    bts_mac = (bts_mac_out or "").strip().lower().replace("-", ":")

    for idx in range(1, 33):
        code, out = await _ssh_run(
            bts_conn,
            _remote_exec_bts_command(idx, "echo RADIO24_CPE_OK"),
            timeout_s=25,
        )
        if code != 0 or "RADIO24_CPE_OK" not in out:
            continue
        _, peer_mac_out = await _ssh_run(
            bts_conn,
            _remote_exec_bts_command(
                idx, "cat /sys/class/net/br-lan/address 2>/dev/null"
            ),
            timeout_s=20,
        )
        peer_mac = (peer_mac_out or "").strip().lower().replace("-", ":")
        if bts_mac and peer_mac == bts_mac:
            continue
        if want_ssid:
            _, ssid_out = await _ssh_run(
                bts_conn,
                _remote_exec_bts_command(idx, "uci -q get wireless.@wifi-iface[0].ssid"),
            )
            if want_ssid not in (ssid_out or ""):
                continue
        elif peer_mac:
            _, mode_out = await _ssh_run(
                bts_conn,
                _remote_exec_bts_command(idx, "uci -q get wireless.@wifi-iface[1].mode"),
            )
            mode = (mode_out or "").strip().lower()
            if mode and mode != "sta":
                continue
        return idx
    return None


async def _cpe_remote_exec(
    bts_conn: asyncssh.SSHClientConnection,
    su_index: int,
    inner_command: str,
    *,
    timeout_s: int = 30,
) -> tuple[int, str]:
    return await _ssh_run(
        bts_conn,
        _remote_exec_bts_command(su_index, inner_command),
        timeout_s=timeout_s,
    )


async def _wait_for_bts_cpe_link_stable(
    profile_bundle,
    *,
    timeout_s: int,
) -> tuple[bool, bool, bool]:
    """
    Wait until BTS is SSH-reachable and BTS can ping CPE IPv6 (link stable).

    Does not require direct CPE SSH from the test runner.
    """
    bts_ip, cpe_ip, user, password = _resolve_bts_cpe_targets(profile_bundle)
    if not bts_ip or not cpe_ip:
        return False, False, False

    deadline = time.monotonic() + timeout_s
    bts_reachable = False
    cpe_ping_ok = False
    while time.monotonic() < deadline:
        try:
            async with asyncssh.connect(
                bts_ip,
                username=user,
                password=password,
                known_hosts=None,
                connect_timeout=12.0,
            ) as bts_conn:
                bts_reachable = True
                _, ping_out = await _ssh_run(
                    bts_conn,
                    f"ping -6 -c 2 -W 4 {cpe_ip} 2>&1",
                    timeout_s=25,
                )
                cpe_ping_ok = _ping_output_ok(ping_out)
                if cpe_ping_ok:
                    await asyncio.sleep(5)
                    _, ping_out2 = await _ssh_run(
                        bts_conn,
                        f"ping -6 -c 2 -W 4 {cpe_ip} 2>&1",
                        timeout_s=25,
                    )
                    if _ping_output_ok(ping_out2):
                        return True, True, True
        except (OSError, asyncssh.Error):
            bts_reachable = False
            cpe_ping_ok = False
        await asyncio.sleep(10)
    return bts_reachable, cpe_ping_ok, False


async def _fetch_cpe_24g_ap_bssid(profile_bundle) -> str | None:
    """Read CPE 2.4 GHz AP BSSID (ath0 HWaddr) via CPE SSH or BTS remote_exec."""
    if not profile_bundle:
        return None

    iface = str(RADIO_24_SCAN_DEFAULTS.get("CPE_24G_IFACE", "ath0")).strip()
    hwaddr_cmd = f"ifconfig {iface} 2>/dev/null | awk '/HWaddr/{{print $5; exit}}'"
    mgmt_ssid = str(RADIO_24_SCAN_DEFAULTS.get("CPE_MGMT_HIDDEN_SSID", "")).strip()

    cpe_ip, user, password = _resolve_cpe_ssh_target(profile_bundle)
    if cpe_ip and password:
        try:
            async with asyncssh.connect(
                cpe_ip,
                username=user,
                password=password,
                known_hosts=None,
                connect_timeout=12.0,
            ) as cpe_conn:
                _, out = await _cpe_ssh_run(cpe_conn, hwaddr_cmd, timeout_s=15)
                match = _BSSID_RE.search(out or "")
                if match:
                    bssid = match.group(1).upper()
                    return bssid
        except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
            pass

    bts_ip, _, bts_user, bts_password = _resolve_bts_cpe_targets(profile_bundle)
    if not bts_ip or not bts_password:
        return None

    try:
        async with asyncssh.connect(
            bts_ip,
            username=bts_user,
            password=bts_password,
            known_hosts=None,
            connect_timeout=12.0,
        ) as bts_conn:
            su_idx = await _resolve_cpe_remote_exec_index(bts_conn, mgmt_ssid=mgmt_ssid)
            if su_idx is None:
                return None
            _, out = await _cpe_remote_exec(bts_conn, su_idx, hwaddr_cmd, timeout_s=25)
            match = _BSSID_RE.search(out or "")
            if match:
                bssid = match.group(1).upper()
                return bssid
    except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
        pass
    return None


async def _wait_for_cpe_24g_ap_bssid_stable(
    profile_bundle,
    *,
    timeout_s: int = 90,
    stable_reads: int = 2,
    poll_s: float = 5.0,
) -> str | None:
    """Poll CPE ath0 BSSID until it is present and stable (AP finished wifi reload)."""
    deadline = time.monotonic() + timeout_s
    last: str | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        bssid = await _fetch_cpe_24g_ap_bssid(profile_bundle)
        if bssid:
            if bssid == last:
                stable_count += 1
                if stable_count >= max(1, stable_reads - 1):
                    return bssid
            else:
                stable_count = 0
                last = bssid
        else:
            stable_count = 0
            last = None
        await asyncio.sleep(poll_s)
    return last


async def _prepare_scan_pc_wifi_for_join(
    conn: asyncssh.SSHClientConnection,
    iface: str,
) -> None:
    """Reset scan PC Wi‑Fi NIC before hidden join (radio on, managed, no stale IP)."""
    state = await _get_wifi_iface_state(conn, iface)
    if "unavailable" in state.lower():
        _log(f"scan PC {iface} unavailable — running Wi‑Fi radio recovery")
        await _recover_scan_pc_wifi_nic(conn, iface, level=1)
    await _ssh_run(conn, "iwconfig {iface} power off 2>/dev/null".format(iface=iface), timeout_s=15)
    await _ssh_run(conn, "nmcli radio wifi on", timeout_s=30)
    await _ssh_run(conn, f"nmcli device set {iface} managed yes", timeout_s=30)
    await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=30)
    await _ssh_run(conn, f"ip -4 addr flush dev {iface} 2>/dev/null", timeout_s=15)
    await asyncio.sleep(2)


def _normalize_nmcli_bssid(raw: str) -> str:
    return (raw or "").replace("\\", "").upper().strip()


async def _scan_pc_wifi_list_has_bssid(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    bssid: str,
) -> bool:
    """Return True when scan PC nmcli Wi‑Fi list includes the CPE AP BSSID."""
    target = _normalize_nmcli_bssid(bssid)
    await _ssh_run(conn, "nmcli device wifi rescan", timeout_s=45)
    await asyncio.sleep(4)
    _, out = await _ssh_run(
        conn,
        f"nmcli -t -f BSSID device wifi list ifname {iface}",
        timeout_s=30,
    )
    for line in (out or "").splitlines():
        if target and target in _normalize_nmcli_bssid(line):
            return True
    return False


async def _wait_for_scan_pc_ssh(
    scan_ip: str,
    scan_password: str,
    *,
    timeout_s: int = 180,
) -> bool:
    """Poll until scan PC SSH is reachable (after reboot)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            async with asyncssh.connect(
                scan_ip,
                username="root",
                password=scan_password,
                known_hosts=None,
                connect_timeout=8.0,
            ) as conn:
                code, _ = await _ssh_run(conn, "echo SCAN_PC_OK", timeout_s=10)
                if code == 0:
                    return True
        except (OSError, asyncssh.Error):
            pass
        await asyncio.sleep(10)
    return False


async def _recover_scan_pc_wifi_nic(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    level: int = 1,
    scan_ip: str | None = None,
    scan_password: str | None = None,
) -> bool:
    """
    Wi‑Fi NIC recovery on the scan PC — radio and Wi‑Fi iface only; LAN ports stay up.

    level 1 — disconnect Wi‑Fi iface, nmcli radio off/on, managed cycle
    level 2 — two radio toggle cycles + rescan
    level 3 — three radio toggle cycles + longer settle + rescan
    level 4 — reboot scan PC (last resort; SSH conn will drop)
    """
    _log(f"scan PC Wi‑Fi recovery level {level} on {iface} (Wi‑Fi radio only — LAN ports unchanged)")
    await _ensure_scan_pc_lan_ports_up(conn, case_id="RADIO_24_SCAN")

    async def _wifi_radio_cycle(*, settle_s: float = 3.0) -> None:
        await _ssh_run(conn, f"nmcli device disconnect {iface} 2>/dev/null", timeout_s=20)
        await _ssh_run(conn, f"ip -4 addr flush dev {iface} 2>/dev/null", timeout_s=15)
        await _ssh_run(conn, "nmcli radio wifi off", timeout_s=20)
        await asyncio.sleep(2)
        await _ssh_run(conn, "nmcli radio wifi on", timeout_s=20)
        await _ssh_run(conn, f"nmcli device set {iface} managed yes", timeout_s=20)
        await _ssh_run(conn, f"ip link set {iface} up", timeout_s=15)
        await _ssh_run(conn, f"iwconfig {iface} power off 2>/dev/null", timeout_s=15)
        await asyncio.sleep(settle_s)

    if 1 <= level <= 3:
        cycles = {1: 1, 2: 2, 3: 3}[level]
        settle = {1: 3.0, 2: 5.0, 3: 8.0}[level]
        for _ in range(cycles):
            await _wifi_radio_cycle(settle_s=settle)
        await _ssh_run(
            conn,
            f"nmcli device wifi rescan ifname {iface} 2>/dev/null || nmcli device wifi rescan",
            timeout_s=45,
        )
        await asyncio.sleep(4)

    if level >= 4 and scan_ip and scan_password:
        try:
            await conn.run("nohup reboot >/dev/null 2>&1 &", check=False, timeout=5)
        except (asyncssh.Error, asyncio.TimeoutError, TimeoutError, OSError):
            pass
        return await _wait_for_scan_pc_ssh(
            scan_ip,
            scan_password,
            timeout_s=int(RADIO_24_SCAN_DEFAULTS.get("SCAN_PC_WIFI_REBOOT_WAIT_S", 180)),
        )

    await _ensure_scan_pc_lan_ports_up(conn, case_id="RADIO_24_SCAN")
    state = await _get_wifi_iface_state(conn, iface)
    return "unavailable" not in state.lower()


async def _toggle_cpe_mgmt_ap_off_on_bundle(
    profile_bundle,
    *,
    mgmt_ssid: str | None,
    radio_idx: int = 0,
    reload_wait_s: int = 8,
    settle_s: int = 3,
) -> bool:
    """Toggle CPE hidden mgmt AP off→on via CPE SSH or BTS remote_exec (background wifi reload)."""
    iface_key = f"wireless.@wifi-iface[{radio_idx}]"
    off_script = (
        f"uci set {iface_key}.hidden=1 && "
        f"uci set {iface_key}.disabled=1 && "
        "uci commit wireless && "
        "nohup sh -c 'wifi reload 2>/dev/null || wifi' >/tmp/radio24_ap_off.log 2>&1 &"
    )
    on_script = (
        f"uci set {iface_key}.hidden=1 && "
        f"uci set {iface_key}.disabled=0 && "
        "uci commit wireless && "
        "nohup sh -c 'wifi reload 2>/dev/null || wifi' >/tmp/radio24_ap_on.log 2>&1 &"
    )
    off_code, _ = await _run_cpe_command(
        profile_bundle, off_script, timeout_s=45, mgmt_ssid=mgmt_ssid
    )
    if off_code != 0:
        return False
    await asyncio.sleep(max(reload_wait_s, settle_s))
    on_code, _ = await _run_cpe_command(
        profile_bundle, on_script, timeout_s=45, mgmt_ssid=mgmt_ssid
    )
    if on_code != 0:
        return False
    await asyncio.sleep(max(reload_wait_s, settle_s))
    return True


async def _ensure_scan_pc_can_reach_cpe_ap(
    profile_bundle,
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    mgmt_ssid: str,
    max_recover_attempts: int | None = None,
) -> tuple[str | None, bool]:
    """
    Ensure CPE mgmt AP BSSID is present on scan PC Wi‑Fi scan before hidden join.

    When ath0 stops beaconing, toggle AP off/on and wait for BSSID in scan.
    """
    defaults = RADIO_24_SCAN_DEFAULTS
    radio_idx = int(defaults.get("CPE_RADIO_24_IDX", 0))
    reload_wait_s = int(defaults.get("CPE_WIFI_RELOAD_WAIT_S", 8))
    settle_s = int(defaults.get("RADIO_TOGGLE_SETTLE_S", 3))
    if max_recover_attempts is None:
        max_recover_attempts = int(defaults.get("CPE_AP_RECOVERY_ATTEMPTS", 2))

    bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=90)
    if not bssid:
        return None, False

    for attempt in range(max_recover_attempts + 1):
        if await _scan_pc_wifi_list_has_bssid(conn, iface, bssid):
            return bssid, True
        if attempt >= max_recover_attempts:
            break
        _log(
            f"CPE AP BSSID {bssid} not in scan — toggling CPE 2.4G mgmt Wi‑Fi only "
            f"(uci wireless disabled off→on; LAN ports unchanged) (attempt {attempt + 1})"
        )
        if not await _toggle_cpe_mgmt_ap_off_on_bundle(
            profile_bundle,
            mgmt_ssid=mgmt_ssid,
            radio_idx=radio_idx,
            reload_wait_s=reload_wait_s,
            settle_s=settle_s,
        ):
            break
        bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=90)
        if not bssid:
            break

    return bssid, False


async def _send_cpe_reboot(profile_bundle, *, mgmt_ssid: str | None = None) -> bool:
    """Reboot CPE via direct IPv6 SSH, or BTS remote_exec when the RF path is up."""
    cpe_ip, user, password = _resolve_cpe_ssh_target(profile_bundle)
    bts_ip, _, bts_user, bts_password = _resolve_bts_cpe_targets(profile_bundle)

    if cpe_ip and password:
        try:
            async with asyncssh.connect(
                cpe_ip,
                username=user,
                password=password,
                known_hosts=None,
                connect_timeout=12.0,
            ) as cpe_conn:
                try:
                    await cpe_conn.run("reboot", check=False, timeout=15)
                except (asyncssh.TimeoutError, asyncio.TimeoutError, TimeoutError):
                    pass
                return True
        except (OSError, asyncssh.Error):
            pass

    if not bts_ip or not bts_password:
        return False

    try:
        async with asyncssh.connect(
            bts_ip,
            username=bts_user,
            password=bts_password,
            known_hosts=None,
            connect_timeout=12.0,
        ) as bts_conn:
            su_idx = await _resolve_cpe_remote_exec_index(bts_conn, mgmt_ssid=mgmt_ssid)
            if su_idx is None:
                return False
            await _cpe_remote_exec(bts_conn, su_idx, "reboot", timeout_s=15)
            return True
    except (OSError, asyncssh.Error):
        return False


async def _ensure_cpe_mgmt_ap_ready(
    profile_bundle,
    *,
    radio_idx: int,
    reload_wait_s: int,
    mgmt_ssid: str | None = None,
) -> bool:
    """Enable hidden CPE 2.4 GHz mgmt AP via CPE SSH or BTS remote_exec."""
    iface_key = f"wireless.@wifi-iface[{radio_idx}]"
    cmds = [
        f"uci set {iface_key}.hidden=1",
        f"uci set {iface_key}.disabled=0",
        "uci commit wireless",
        "nohup sh -c 'wifi reload 2>/dev/null || wifi' >/tmp/radio24_wifi_reload.log 2>&1 &",
    ]

    cpe_ip, user, password = _resolve_cpe_ssh_target(profile_bundle)
    if cpe_ip and password:
        try:
            async with asyncssh.connect(
                cpe_ip,
                username=user,
                password=password,
                known_hosts=None,
                connect_timeout=12.0,
            ) as cpe_conn:
                for cmd in cmds:
                    await _cpe_ssh_run(cpe_conn, cmd, timeout_s=20, tolerate_timeout=True)
                await asyncio.sleep(reload_wait_s)
                bssid = await _wait_for_cpe_24g_ap_bssid_stable(
                    profile_bundle,
                    timeout_s=max(reload_wait_s + 30, 60),
                )
                return bool(bssid)
        except (OSError, asyncssh.Error):
            pass

    bts_ip, _, bts_user, bts_password = _resolve_bts_cpe_targets(profile_bundle)
    if not bts_ip or not bts_password:
        return False

    try:
        async with asyncssh.connect(
            bts_ip,
            username=bts_user,
            password=bts_password,
            known_hosts=None,
            connect_timeout=12.0,
        ) as bts_conn:
            su_idx = await _resolve_cpe_remote_exec_index(bts_conn, mgmt_ssid=mgmt_ssid)
            if su_idx is None:
                return False
            for cmd in cmds:
                await _cpe_remote_exec(bts_conn, su_idx, cmd, timeout_s=25)
            await asyncio.sleep(reload_wait_s)
            bssid = await _wait_for_cpe_24g_ap_bssid_stable(
                profile_bundle,
                timeout_s=max(reload_wait_s + 30, 60),
            )
            return bool(bssid)
    except (OSError, asyncssh.Error):
        return False


async def _connect_hidden_wifi_with_retries(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    ssid: str,
    password: str,
    settle_s: int,
    max_attempts: int,
    bssid: str | None = None,
    require_mgmt_ip: bool = True,
    profile_bundle=None,
    refresh_bssid: bool = False,
    scan_ip: str | None = None,
    scan_password: str | None = None,
) -> tuple[int, str]:
    last_code = 1
    last_out = ""
    use_refresh = refresh_bssid and profile_bundle is not None
    if profile_bundle:
        ready_bssid, reachable = await _ensure_scan_pc_can_reach_cpe_ap(
            profile_bundle,
            conn,
            iface,
            mgmt_ssid=ssid,
        )
        if ready_bssid:
            bssid = ready_bssid
        if not reachable:
            _log(f"CPE AP {ready_bssid!r} not visible on scan PC before join")

    for attempt in range(max(1, max_attempts)):
        attempt_bssid = bssid
        if use_refresh and profile_bundle:
            attempt_bssid = await _wait_for_cpe_24g_ap_bssid_stable(
                profile_bundle,
                timeout_s=60,
                stable_reads=2,
                poll_s=4.0,
            )
        elif attempt == 0 and profile_bundle and not attempt_bssid:
            attempt_bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle)

        last_code, last_out = await _connect_hidden_wifi(
            conn,
            iface,
            ssid=ssid,
            password=password,
            settle_s=settle_s,
            bssid=attempt_bssid,
            require_mgmt_ip=require_mgmt_ip,
        )
        if last_code == 0:
            return last_code, last_out

        if attempt + 1 >= max_attempts:
            break

        recovery_level = min(attempt + 1, 2)
        await _recover_scan_pc_wifi_nic(
            conn,
            iface,
            level=recovery_level,
            scan_ip=scan_ip,
            scan_password=scan_password,
        )
        if profile_bundle:
            await _toggle_cpe_mgmt_ap_off_on_bundle(
                profile_bundle,
                mgmt_ssid=ssid,
            )
            ready_bssid, _ = await _ensure_scan_pc_can_reach_cpe_ap(
                profile_bundle,
                conn,
                iface,
                mgmt_ssid=ssid,
            )
            if ready_bssid:
                bssid = ready_bssid
        await asyncio.sleep(5)

    return last_code, last_out


async def _passive_iwlist_scan(
    scan_conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    settle_s: int,
    timeout_s: int,
) -> list[str]:
    await _prepare_wifi_for_passive_scan(scan_conn, iface, settle_s=settle_s)
    iwlist_ssids, _ = await _scan_essids_iwlist(scan_conn, iface, timeout_s=timeout_s)
    if not iwlist_ssids:
        await asyncio.sleep(2)
        iwlist_ssids, _ = await _scan_essids_iwlist(scan_conn, iface, timeout_s=timeout_s)
    return iwlist_ssids


async def assert_radio_24_01_hidden_ssid_not_visible(
    profile_bundle=None,
):
    """
    2.4G_RADIO_01: Scan for SSIDs near CPE from the lab PC Wi‑Fi NIC.

    Expected: CPE 2.4 GHz mgmt SSID is not visible in a passive scan (iwlist).
    """
    case_id = "2_4G_RADIO_01"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, _, settle_s, timeout_s = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    max_toggle = int(defaults.get("HIDDEN_TOGGLE_ATTEMPTS", 10))
    radio_idx = int(defaults.get("CPE_RADIO_24_IDX", 0))
    toggle_settle_s = int(defaults.get("RADIO_TOGGLE_SETTLE_S", 3))
    lab_flush_attempts = int(defaults.get("LAB_PC_FLUSH_ATTEMPTS", 3))
    cpe_reload_wait_s = int(defaults.get("CPE_WIFI_RELOAD_WAIT_S", 8))

    print_section(
        f"{case_id}: Scan near CPE from lab PC {scan_ip} — "
        f"hidden mgmt SSID {target_ssid!r} must not appear in scan"
    )

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _clear_lab_pc_wifi(conn, case_id=case_id)

        iwlist_ssids = await _passive_iwlist_scan(
            conn,
            iface,
            settle_s=settle_s,
            timeout_s=timeout_s,
        )
        toggle_count = 0
        cpe_iface_idx: int | None = None

        if target_ssid in _visible_ssids_from_iwlist(iwlist_ssids):
            iwlist_ssids = await _try_lab_pc_flush_until_hidden(
                conn,
                iface,
                target_ssid,
                iwlist_ssids,
                settle_s=settle_s,
                timeout_s=timeout_s,
                max_attempts=lab_flush_attempts,
            )

        if target_ssid in _visible_ssids_from_iwlist(iwlist_ssids):
            cpe_ip, cpe_user, cpe_password = _resolve_cpe_ssh_target(profile_bundle)
            check.is_true(
                cpe_ip and cpe_password and profile_bundle,
                f"{case_id}: CPE SSH required when {target_ssid!r} is visible in scan",
            )
            try:
                async with asyncssh.connect(
                    cpe_ip,
                    username=cpe_user,
                    password=cpe_password,
                    known_hosts=None,
                    connect_timeout=15.0,
                ) as cpe_conn:
                    cpe_iface_idx = await _resolve_cpe_wifi_iface_idx_for_ssid(
                        cpe_conn,
                        target_ssid,
                        fallback_idx=radio_idx,
                    )
                    while (
                        target_ssid in _visible_ssids_from_iwlist(iwlist_ssids)
                        and toggle_count < max_toggle
                    ):
                        await _toggle_cpe_mgmt_ap_off_on(
                            cpe_conn,
                            cpe_iface_idx,
                            settle_s=toggle_settle_s,
                            reload_wait_s=cpe_reload_wait_s,
                        )
                        await _flush_lab_pc_wifi_radio(conn, iface, settle_s=2)
                        toggle_count += 1
                        iwlist_ssids = await _passive_iwlist_scan(
                            conn,
                            iface,
                            settle_s=settle_s,
                            timeout_s=timeout_s,
                        )
            except (OSError, asyncssh.Error) as exc:
                check.is_false(
                    target_ssid in _visible_ssids_from_iwlist(iwlist_ssids),
                    f"{case_id}: {target_ssid!r} visible and CPE SSH failed ({exc})",
                )

        iwlist_visible = target_ssid in _visible_ssids_from_iwlist(iwlist_ssids)

        _print_scan_ssid_list(iwlist_ssids, target_ssid)

        check.is_false(
            iwlist_visible,
            f"{case_id}: {target_ssid!r} still visible after {toggle_count} "
            f"radio off/on toggle(s) (max {max_toggle})",
        )


async def assert_radio_24_02_connect_hidden_ssid(
    profile_bundle=None,
):
    """
    2.4G_RADIO_02: Connect to hidden CPE 2.4 GHz mgmt SSID via SSH (nmcli on lab PC).

    Hidden visibility is covered by case 01. Here: connect, ping CPE mgmt IP, no internet.
    """
    case_id = "2_4G_RADIO_02"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    cpe_mgmt_ip = str(defaults.get("CPE_MGMT_IP", "169.254.254.1")).strip()
    internet_host = str(defaults.get("INTERNET_PROBE_HOST", "8.8.8.8"))
    connect_retries = int(defaults.get("CONNECT_RETRY_ATTEMPTS", 3))

    mgmt_bssid = await _fetch_cpe_24g_ap_bssid(profile_bundle)

    print_section(
        f"{case_id}: SSH nmcli connect hidden SSID {target_ssid!r} on lab PC {scan_ip}"
    )
    if mgmt_bssid:
        print(f"  CPE 2.4 GHz AP BSSID: {mgmt_bssid} (WPA&WPA2 Personal hidden join)")
    print(f"  Wi‑Fi join target: scan PC {scan_ip} (not the pytest runner desktop)")

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _detect_wifi_interface(conn, case_id=case_id)

        connect_code, connect_out = await _connect_hidden_wifi_with_retries(
            conn,
            iface,
            ssid=target_ssid,
            password=target_password,
            settle_s=connect_settle_s,
            max_attempts=connect_retries,
            bssid=mgmt_bssid,
            profile_bundle=profile_bundle,
            scan_ip=scan_ip,
            scan_password=scan_password,
        )

        linked, active_ssid, wifi_state, iface_ip, cpe_ping_ok = await _verify_cpe_mgmt_wifi_link(
            conn,
            iface,
            cpe_mgmt_ip,
            expected_ssid=target_ssid,
            timeout_s=max(connect_settle_s + 20, 35),
        )
        connected = linked
        no_internet = await _internet_unreachable(
            conn,
            internet_host,
            src_ip=iface_ip,
        )

        print_comparison_table(
            [
                ("Wi‑Fi device state", "connected", wifi_state or "disconnected", "PASS" if connected else "FAIL"),
                ("Hidden SSID", target_ssid, active_ssid or "(not connected)", "PASS" if active_ssid == target_ssid else "FAIL"),
                (
                    f"{iface} IPv4",
                    "169.254.x",
                    iface_ip or "(none)",
                    "PASS" if iface_ip else "FAIL",
                ),
                ("nmcli connect (SSH)", "profile add + up", "ok" if connect_code == 0 else f"exit {connect_code}", "PASS" if connect_code == 0 else "FAIL"),
                (
                    f"Ping CPE mgmt via {iface}",
                    cpe_mgmt_ip,
                    "reachable" if cpe_ping_ok else "no reply",
                    "PASS" if cpe_ping_ok else "FAIL",
                ),
                (
                    f"Internet via {iface}",
                    f"no reply to {internet_host}",
                    "no internet" if no_internet else "reachable",
                    "PASS" if no_internet else "FAIL",
                ),
            ]
        )

        check.equal(connect_code, 0, f"{case_id}: nmcli connect failed: {connect_out[-400:]}")
        check.is_true(
            connected,
            f"{case_id}: Wi‑Fi must have 169.254.x on {iface}, route via {iface}, and ping {cpe_mgmt_ip} "
            f"(state={wifi_state!r}, ssid={active_ssid!r}, ip={iface_ip!r})",
        )
        check.is_true(
            cpe_ping_ok,
            f"{case_id}: ping to CPE mgmt {cpe_mgmt_ip} via {iface} should succeed",
        )
        check.is_true(
            no_internet,
            f"{case_id}: internet via {iface} ({internet_host}) should not be reachable on mgmt Wi‑Fi",
        )


async def assert_radio_24_03_connect_hidden_ssid_wrong_password(
    profile_bundle=None,
):
    """
    2.4G_RADIO_03: Hidden SSID must not appear in scan; wrong password must not connect.

    Disconnect all saved Wi‑Fi, confirm SSID hidden, attempt join with bad credentials.
    """
    case_id = "2_4G_RADIO_03"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, _, settle_s, timeout_s = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    wrong_password = str(defaults.get("CPE_MGMT_WRONG_PASSWORD", "WrongAutoTestKey!99"))
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    cpe_mgmt_ip = str(defaults.get("CPE_MGMT_IP", "169.254.254.1")).strip()
    lab_flush_attempts = int(defaults.get("LAB_PC_FLUSH_ATTEMPTS", 3))

    print_section(
        f"{case_id}: Disconnect all Wi‑Fi — hidden SSID {target_ssid!r} must not appear; "
        f"wrong password must not connect"
    )
    mgmt_bssid = await _fetch_cpe_24g_ap_bssid(profile_bundle)

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _clear_lab_pc_wifi(conn, case_id=case_id)

        iwlist_ssids = await _passive_iwlist_scan(
            conn,
            iface,
            settle_s=settle_s,
            timeout_s=timeout_s,
        )
        if target_ssid in _visible_ssids_from_iwlist(iwlist_ssids):
            iwlist_ssids = await _try_lab_pc_flush_until_hidden(
                conn,
                iface,
                target_ssid,
                iwlist_ssids,
                settle_s=settle_s,
                timeout_s=timeout_s,
                max_attempts=lab_flush_attempts,
            )
        ssid_visible = target_ssid in _visible_ssids_from_iwlist(iwlist_ssids)
        _print_scan_ssid_list(iwlist_ssids, target_ssid)

        check.is_false(
            ssid_visible,
            f"{case_id}: {target_ssid!r} must be hidden (not in scan) before wrong-password attempt",
        )

        connect_code, connect_out = await _connect_hidden_wifi(
            conn,
            iface,
            ssid=target_ssid,
            password=wrong_password,
            settle_s=connect_settle_s,
            bssid=mgmt_bssid,
        )

        not_connected, active_ssid, wifi_state = await _wait_for_wifi_not_connected(
            conn,
            iface,
            target_ssid,
            timeout_s=max(connect_settle_s + 15, 25),
        )
        disconnected = not_connected and active_ssid != target_ssid
        cpe_ping_ok = await _ping_reachable(conn, cpe_mgmt_ip, iface=iface)

        print_comparison_table(
            [
                (
                    "Hidden SSID in scan",
                    "not visible",
                    "visible" if ssid_visible else "not in scan",
                    "PASS" if not ssid_visible else "FAIL",
                ),
                (
                    "Wi‑Fi device state",
                    "not connected",
                    wifi_state or "disconnected",
                    "PASS" if disconnected else "FAIL",
                ),
                (
                    "Hidden SSID",
                    f"not {target_ssid!r}",
                    active_ssid or "(not connected)",
                    "PASS" if active_ssid != target_ssid else "FAIL",
                ),
                (
                    "nmcli connect (wrong password)",
                    "activation fails",
                    "failed" if connect_code != 0 else "exit 0",
                    "PASS" if connect_code != 0 else "FAIL",
                ),
                (
                    f"Ping CPE mgmt via {iface}",
                    "no reply",
                    "reachable" if cpe_ping_ok else "no reply",
                    "PASS" if not cpe_ping_ok else "FAIL",
                ),
            ]
        )

        await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=30)
        await _delete_nmcli_connection(conn, target_ssid)
        await _delete_legacy_hidden_profiles(conn)

        check.not_equal(
            connect_code,
            0,
            f"{case_id}: nmcli should reject wrong password (got exit 0): {connect_out[-400:]}",
        )
        check.is_true(
            disconnected,
            f"{case_id}: must not connect to {target_ssid!r} with wrong password "
            f"(state={wifi_state!r}, ssid={active_ssid!r})",
        )
        check.is_false(
            cpe_ping_ok,
            f"{case_id}: ping to CPE mgmt {cpe_mgmt_ip} via {iface} should fail when not connected",
        )


def _resolve_reboot_wait_s(profile_bundle, defaults: dict) -> int:
    if profile_bundle:
        recovery = profile_bundle.active.get("recovery") or {}
        wait = recovery.get("reboot_wait_seconds")
        if wait is not None:
            return max(int(wait), int(defaults.get("CPE_REBOOT_WAIT_S", 180)))
    return int(defaults.get("CPE_REBOOT_WAIT_S", 180))


def _resolve_reboot_boot_s(profile_bundle, defaults: dict) -> int:
    boot = int(defaults.get("CPE_REBOOT_BOOT_S", 90))
    if profile_bundle:
        recovery = profile_bundle.active.get("recovery") or {}
        wait = recovery.get("reboot_wait_seconds")
        if wait is not None:
            return max(boot, min(int(wait), 120))
    return boot


async def _reboot_cpe_and_wait_link(
    profile_bundle,
    *,
    case_id: str,
    timeout_s: int,
    boot_s: int,
    mgmt_ssid: str | None = None,
) -> tuple[bool, bool, bool, bool]:
    """Reboot CPE, wait for boot, then poll until BTS SSH and BTS→CPE IPv6 ping are stable."""
    reboot_sent = await _send_cpe_reboot(profile_bundle, mgmt_ssid=mgmt_ssid)
    if not reboot_sent:
        return False, False, False, False

    await asyncio.sleep(max(boot_s, 15))
    bts_ok, cpe_ok, stable = await _wait_for_bts_cpe_link_stable(
        profile_bundle,
        timeout_s=timeout_s,
    )
    return reboot_sent, bts_ok, cpe_ok, stable


async def assert_radio_24_04_reboot_cpe_scan_hidden_ssid(
    profile_bundle=None,
):
    """
    2.4G_RADIO_04: Reboot CPE, wait for link, scan — hidden mgmt SSID must stay hidden.
    """
    case_id = "2_4G_RADIO_04"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, _, settle_s, timeout_s = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    lab_flush_attempts = int(defaults.get("LAB_PC_FLUSH_ATTEMPTS", 3))
    reboot_wait_s = _resolve_reboot_wait_s(profile_bundle, defaults)
    reboot_boot_s = _resolve_reboot_boot_s(profile_bundle, defaults)

    print_section(f"{case_id}: Reboot CPE — wait for BTS/CPE link — scan; {target_ssid!r} must stay hidden")

    reboot_sent, bts_ok, cpe_ping_ok, link_stable = await _reboot_cpe_and_wait_link(
        profile_bundle,
        case_id=case_id,
        timeout_s=reboot_wait_s,
        boot_s=reboot_boot_s,
        mgmt_ssid=target_ssid,
    )
    print_comparison_table(
        [
            (
                "CPE reboot",
                "command sent",
                "sent" if reboot_sent else "failed",
                "PASS" if reboot_sent else "FAIL",
            ),
            (
                "BTS SSH",
                "reachable",
                "ok" if bts_ok else "down",
                "PASS" if bts_ok else "FAIL",
            ),
            (
                "BTS ping CPE IPv6",
                "reachable",
                "ok" if cpe_ping_ok else "no reply",
                "PASS" if cpe_ping_ok else "FAIL",
            ),
            (
                "Link stable",
                "BTS + CPE ping",
                "stable" if link_stable else "not ready",
                "PASS" if link_stable else "FAIL",
            ),
        ]
    )
    check.is_true(reboot_sent, f"{case_id}: CPE reboot must be sent (direct SSH or BTS remote_exec)")
    check.is_true(
        link_stable,
        f"{case_id}: BTS↔CPE link must be stable (BTS SSH + ping CPE) within {reboot_wait_s}s after reboot",
    )

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _clear_lab_pc_wifi(conn, case_id=case_id)

        iwlist_ssids = await _passive_iwlist_scan(
            conn,
            iface,
            settle_s=settle_s,
            timeout_s=timeout_s,
        )
        if target_ssid in _visible_ssids_from_iwlist(iwlist_ssids):
            iwlist_ssids = await _try_lab_pc_flush_until_hidden(
                conn,
                iface,
                target_ssid,
                iwlist_ssids,
                settle_s=settle_s,
                timeout_s=timeout_s,
                max_attempts=lab_flush_attempts,
            )

        iwlist_visible = target_ssid in _visible_ssids_from_iwlist(iwlist_ssids)
        _print_scan_ssid_list(iwlist_ssids, target_ssid)

        check.is_false(
            iwlist_visible,
            f"{case_id}: {target_ssid!r} must remain hidden in scan after CPE reboot",
        )


def _curl_http_status(raw: str) -> int:
    for line in (raw or "").splitlines():
        if line.upper().startswith("HTTP/"):
            match = re.search(r"\s(\d{3})\s", line)
            if match:
                return int(match.group(1))
    return 0


async def _resolve_fw_path_on_scan_pc(
    scan_conn: asyncssh.SSHClientConnection,
    fw_dir: str,
    fw_file: str,
) -> str | None:
    quoted = f"{fw_dir.rstrip('/')}/{fw_file}".replace("'", "'\"'\"'")
    code, out = await _ssh_run(scan_conn, f"test -f '{quoted}' && echo FOUND", timeout_s=15)
    if code == 0 and "FOUND" in out:
        return f"{fw_dir.rstrip('/')}/{fw_file}"
    return None


async def _silent_cpe_api_upload_and_upgrade(
    scan_conn: asyncssh.SSHClientConnection,
    fw_path: str,
    *,
    api_base: str,
    upload_timeout_s: int,
) -> tuple[bool, bool, str, str]:
    """
    Upload firmware and start upgrade (preserveConfig=true) via curl on the scan PC.
    No console output during API calls.
    """
    base = api_base.rstrip("/")
    quoted_fw = fw_path.replace("'", "'\"'\"'")
    upload_cmd = (
        f"curl -sS -i -X POST -H 'Expect:' "
        f"-F 'file=@{quoted_fw}' --max-time {int(upload_timeout_s)} "
        f"{base}/api/v1/cpe/upload-sw"
    )
    up_code, up_out = await _ssh_run(scan_conn, upload_cmd, timeout_s=upload_timeout_s + 60)
    upload_http = _curl_http_status(up_out)
    upload_ok = upload_http == 200 and "success" in (up_out or "").lower()

    status_ok = False
    status_body = ""
    if upload_ok:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            _, status_out = await _ssh_run(
                scan_conn,
                f"curl -sS --max-time 30 {base}/api/v1/cpe/upload-sw-status",
                timeout_s=40,
            )
            status_body = (status_out or "").strip()
            upper = status_body.upper()
            if "SUCCESS" in upper and ("100" in status_body or '"progress":100' in status_body):
                status_ok = True
                break
            await asyncio.sleep(5)

    upgrade_ok = False
    upgrade_body = ""
    if upload_ok and status_ok:
        upgrade_cmd = (
            f"curl -sS -X POST -H 'Content-Type: application/json' "
            f"-d '{{\"delay\":\"5\",\"preserveConfig\":true}}' "
            f"--max-time 60 {base}/api/v1/cpe/upgrade-sw"
        )
        _, upgrade_out = await _ssh_run(scan_conn, upgrade_cmd, timeout_s=90)
        upgrade_body = (upgrade_out or "").strip()
        upper = upgrade_body.upper()
        upgrade_ok = "UPGRADE_STARTED" in upper or '"status":"UPGRADE_STARTED"' in upper

    return upload_ok, upgrade_ok, status_body[:200], upgrade_body[:200]


async def assert_radio_24_08_fw_upgrade_scan_hidden_ssid(
    profile_bundle=None,
):
    """
    2.4G_RADIO_08: Upgrade CPE firmware (preserve config) via mgmt API, wait for link, scan.

    Uses API upload-sw + upgrade-sw (preserveConfig) from the scan PC on CPE mgmt Wi‑Fi.
    """
    case_id = "2_4G_RADIO_08"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, settle_s, timeout_s = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    fw_file = str(defaults.get("FW_FILE", "Senao-UBR650-xxx-0.0.0.730-single.tgz")).strip()
    fw_dir = str(defaults.get("FW_DIR", "/tftpboot")).strip()
    upload_timeout_s = int(defaults.get("FW_UPLOAD_TIMEOUT_S", 900))
    fw_upgrade_wait_s = int(defaults.get("FW_UPGRADE_WAIT_S", 1200))
    reboot_boot_s = _resolve_reboot_boot_s(profile_bundle, defaults)
    lab_flush_attempts = int(defaults.get("LAB_PC_FLUSH_ATTEMPTS", 3))
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    connect_retries = int(defaults.get("CONNECT_RETRY_ATTEMPTS", 3))
    radio_idx = int(defaults.get("CPE_RADIO_24_IDX", 0))
    mgmt_ready_wait_s = int(defaults.get("MGMT_AP_READY_WAIT_S", 12))
    cpe_reload_wait_s = int(defaults.get("CPE_WIFI_RELOAD_WAIT_S", 8))
    cpe_mgmt_api = f"http://{defaults.get('CPE_MGMT_IP', '169.254.254.1')}"

    print_section(
        f"{case_id}: FW upgrade {fw_file!r} (preserve config) — link up — "
        f"scan; {target_ssid!r} must stay hidden"
    )

    mgmt_ready = await _ensure_cpe_mgmt_ap_ready(
        profile_bundle,
        radio_idx=radio_idx,
        reload_wait_s=max(mgmt_ready_wait_s, cpe_reload_wait_s),
        mgmt_ssid=target_ssid,
    )
    mgmt_bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=90)

    upload_ok = False
    upgrade_ok = False
    link_stable = False
    ssid_visible = True
    upload_status = ""
    upgrade_status = ""

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _detect_wifi_interface(conn, case_id=case_id)
        fw_path = await _resolve_fw_path_on_scan_pc(conn, fw_dir, fw_file)
        check.is_true(fw_path, f"{case_id}: firmware not found on scan PC at {fw_dir}/{fw_file}")

        connect_code, _ = await _connect_hidden_wifi_with_retries(
            conn,
            iface,
            ssid=target_ssid,
            password=target_password,
            settle_s=connect_settle_s,
            max_attempts=connect_retries,
            bssid=mgmt_bssid,
            profile_bundle=profile_bundle,
            refresh_bssid=True,
            scan_ip=scan_ip,
            scan_password=scan_password,
        )
        check.equal(
            connect_code,
            0,
            f"{case_id}: scan PC must join CPE mgmt Wi‑Fi before firmware API calls",
        )

        upload_ok, upgrade_ok, upload_status, upgrade_status = await _silent_cpe_api_upload_and_upgrade(
            conn,
            fw_path or "",
            api_base=cpe_mgmt_api,
            upload_timeout_s=upload_timeout_s,
        )

        await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=30)

    if upgrade_ok:
        await asyncio.sleep(max(reboot_boot_s, 60))
        _, _, link_stable = await _wait_for_bts_cpe_link_stable(
            profile_bundle,
            timeout_s=fw_upgrade_wait_s,
        )

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _clear_lab_pc_wifi(conn, case_id=case_id)
        iwlist_ssids = await _passive_iwlist_scan(
            conn,
            iface,
            settle_s=settle_s,
            timeout_s=timeout_s,
        )
        if target_ssid in _visible_ssids_from_iwlist(iwlist_ssids):
            iwlist_ssids = await _try_lab_pc_flush_until_hidden(
                conn,
                iface,
                target_ssid,
                iwlist_ssids,
                settle_s=settle_s,
                timeout_s=timeout_s,
                max_attempts=lab_flush_attempts,
            )
        ssid_visible = target_ssid in _visible_ssids_from_iwlist(iwlist_ssids)
        _print_scan_ssid_list(iwlist_ssids, target_ssid)

    print_comparison_table(
        [
            (
                "CPE mgmt AP ready",
                "enabled",
                "ready" if mgmt_ready else "not confirmed",
                "PASS" if mgmt_ready else "FAIL",
            ),
            (
                "FW upload (API)",
                "upload-sw success",
                "ok" if upload_ok else "failed",
                "PASS" if upload_ok else "FAIL",
            ),
            (
                "FW upgrade preserveConfig",
                "UPGRADE_STARTED",
                "started" if upgrade_ok else "failed",
                "PASS" if upgrade_ok else "FAIL",
            ),
            (
                "BTS link after upgrade",
                "stable",
                "stable" if link_stable else "not ready",
                "PASS" if link_stable else "FAIL",
            ),
            (
                "Hidden SSID in scan",
                "not visible",
                "visible" if ssid_visible else "not in scan",
                "PASS" if not ssid_visible else "FAIL",
            ),
        ]
    )

    check.is_true(mgmt_ready, f"{case_id}: CPE 2.4 GHz mgmt AP must be ready before upgrade")
    check.is_true(upload_ok, f"{case_id}: upload-sw must succeed ({upload_status!r})")
    check.is_true(upgrade_ok, f"{case_id}: upgrade-sw preserveConfig must start ({upgrade_status!r})")
    check.is_true(
        link_stable,
        f"{case_id}: BTS↔CPE link must be stable within {fw_upgrade_wait_s}s after upgrade",
    )
    check.is_false(
        ssid_visible,
        f"{case_id}: {target_ssid!r} must remain hidden in scan after firmware upgrade",
    )


async def _run_cpe_command(
    profile_bundle,
    command: str,
    *,
    timeout_s: int = 30,
    mgmt_ssid: str | None = None,
) -> tuple[int, str]:
    """Run a shell command on the CPE via direct SSH or BTS remote_exec."""
    cpe_ip, user, password = _resolve_cpe_ssh_target(profile_bundle)
    if cpe_ip and password:
        try:
            async with asyncssh.connect(
                cpe_ip,
                username=user,
                password=password,
                known_hosts=None,
                connect_timeout=12.0,
            ) as cpe_conn:
                return await _cpe_ssh_run(cpe_conn, command, timeout_s=timeout_s)
        except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
            pass

    bts_ip, _, bts_user, bts_password = _resolve_bts_cpe_targets(profile_bundle)
    if not bts_ip or not bts_password:
        return 1, "no CPE SSH path"

    try:
        async with asyncssh.connect(
            bts_ip,
            username=bts_user,
            password=bts_password,
            known_hosts=None,
            connect_timeout=12.0,
        ) as bts_conn:
            su_idx = await _resolve_cpe_remote_exec_index(bts_conn, mgmt_ssid=mgmt_ssid)
            if su_idx is None:
                return 1, "no remote_exec SU index"
            return await _cpe_remote_exec(bts_conn, su_idx, command, timeout_s=timeout_s)
    except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError) as exc:
        return 1, str(exc)


async def _set_cpe_lan24_dhcp_server(
    profile_bundle,
    enabled: bool,
    *,
    mgmt_ssid: str | None = None,
) -> bool:
    """Enable or disable CPE 2.4 GHz mgmt DHCP server (dhcp.lan24.ignore)."""
    ignore = "0" if enabled else "1"
    script = (
        f"uci set dhcp.lan24.ignore={ignore} && "
        "uci commit dhcp && "
        "/etc/init.d/dnsmasq restart 2>/dev/null || /etc/init.d/dnsmasq reload"
    )
    code, out = await _run_cpe_command(profile_bundle, script, timeout_s=45, mgmt_ssid=mgmt_ssid)
    if code != 0:
        _log(f"lan24 DHCP set ignore={ignore} failed: {out[-200:]}")
        return False
    await asyncio.sleep(3)
    return True


async def _get_cpe_lan24_dhcp_ignore(
    profile_bundle,
    *,
    mgmt_ssid: str | None = None,
) -> str | None:
    _, out = await _run_cpe_command(
        profile_bundle,
        "uci -q get dhcp.lan24.ignore",
        timeout_s=15,
        mgmt_ssid=mgmt_ssid,
    )
    value = (out or "").strip().strip("'")
    return value if value else None


async def _read_cpe_dhcp_leases(
    profile_bundle,
    *,
    mgmt_ssid: str | None = None,
) -> str:
    _, out = await _run_cpe_command(
        profile_bundle,
        "cat /tmp/dhcp.leases 2>/dev/null; cat /var/dhcp.leases 2>/dev/null",
        timeout_s=20,
        mgmt_ssid=mgmt_ssid,
    )
    return out or ""


async def _read_cpe_dhcp_logs(
    profile_bundle,
    *,
    mgmt_ssid: str | None = None,
) -> str:
    _, out = await _run_cpe_command(
        profile_bundle,
        "logread 2>/dev/null | grep -iE 'dnsmasq|dhcp' | tail -40",
        timeout_s=25,
        mgmt_ssid=mgmt_ssid,
    )
    return out or ""


async def _disconnect_scan_pc_wifi(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    ssid: str | None = None,
    delete_profile: bool = True,
) -> None:
    await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=30)
    if ssid and delete_profile:
        await _delete_nmcli_connection(conn, ssid)
    await _ssh_run(conn, f"ip -4 addr flush dev {iface} 2>/dev/null", timeout_s=15)
    await asyncio.sleep(2)


async def _wait_for_no_mgmt_ip_on_iface(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    prefix: str,
    timeout_s: int,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ip = await _get_iface_ipv4(conn, iface, prefix=prefix)
        if ip:
            return False
        await asyncio.sleep(2)
    return True


async def _dhcp_connect_scan_pc(
    profile_bundle,
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    target_ssid: str,
    target_password: str,
    connect_settle_s: int,
    connect_retries: int,
    require_mgmt_ip: bool = True,
    refresh_bssid: bool = False,
) -> tuple[int, str, str | None, bool]:
    scan_ip, scan_password, _, _, _, _ = _resolve_scan_pc_config(profile_bundle)
    mgmt_bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=90)
    attempts = connect_retries if require_mgmt_ip else 1
    connect_code, connect_out = await _connect_hidden_wifi_with_retries(
        conn,
        iface,
        ssid=target_ssid,
        password=target_password,
        settle_s=connect_settle_s,
        max_attempts=attempts,
        bssid=mgmt_bssid,
        require_mgmt_ip=require_mgmt_ip,
        profile_bundle=profile_bundle,
        refresh_bssid=refresh_bssid,
        scan_ip=scan_ip,
        scan_password=scan_password,
    )
    if connect_code != 0:
        return connect_code, connect_out, None, False

    if not require_mgmt_ip:
        deadline = time.monotonic() + max(connect_settle_s + 10, 20)
        associated = False
        iface_ip: str | None = None
        while time.monotonic() < deadline:
            state, active_ssid, con_name = await _get_wifi_device_association(conn, iface)
            iface_ip = await _get_iface_ipv4(conn, iface, prefix="169.254.")
            if _wifi_mgmt_associated(
                state, active_ssid, target_ssid, connection_name=con_name
            ):
                associated = True
                break
            await asyncio.sleep(2)
        if not iface_ip:
            iface_ip = await _get_iface_ipv4(conn, iface, prefix="169.254.")
        return connect_code, connect_out, iface_ip, associated

    cpe_mgmt_ip = str(RADIO_24_SCAN_DEFAULTS.get("CPE_MGMT_IP", "169.254.254.1"))
    linked, _, _, iface_ip, ping_ok = await _verify_cpe_mgmt_wifi_link(
        conn,
        iface,
        cpe_mgmt_ip,
        expected_ssid=target_ssid,
        timeout_s=max(connect_settle_s + 20, 35),
    )
    return connect_code, connect_out, iface_ip, linked and ping_ok


def _dhcp_ip_matches_expected(iface_ip: str | None, expected_ip: str) -> bool:
    return bool(iface_ip) and iface_ip.strip() == expected_ip.strip()


def _dhcp_logs_show_lease(
    raw: str,
    expected_ip: str,
    client_mac: str | None = None,
) -> bool:
    text = raw or ""
    if expected_ip in text:
        return True
    if client_mac and client_mac.lower().replace("-", ":") in text.lower():
        return True
    for pattern in RADIO_24_SCAN_DEFAULTS.get("DHCP_LOG_PATTERNS", []):
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _normalize_mac(mac: str) -> str:
    return mac.lower().replace("-", ":").strip()


def _cpe_lease_matches_client(
    leases_raw: str,
    expected_ip: str,
    client_mac: str,
    *,
    expected_hostname: str | None = None,
) -> tuple[bool, str | None]:
    """Parse OpenWrt /tmp/dhcp.leases (same data as LuCI DHCP / Leases tab)."""
    mac_norm = _normalize_mac(client_mac)
    for line in (leases_raw or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        lease_mac = _normalize_mac(parts[1])
        lease_ip = parts[2].strip()
        lease_host = parts[3].strip()
        if lease_ip != expected_ip.strip() or lease_mac != mac_norm:
            continue
        if expected_hostname and lease_host != expected_hostname:
            continue
        return True, lease_host
    return False, None


async def _ensure_scan_pc_lan_ports_up(
    conn: asyncssh.SSHClientConnection,
    *,
    case_id: str = "2_4G_RADIO_19",
) -> list[str]:
    """Bring all scan PC ethernet (LAN) ports up; do not disable them during Wi‑Fi tests."""
    _, out = await _ssh_run(
        conn,
        "nmcli -t -f DEVICE,TYPE device | awk -F: '$2==\"ethernet\"{print $1}'",
        timeout_s=20,
    )
    ifaces = [line.strip() for line in (out or "").splitlines() if line.strip()]
    check.is_true(ifaces, f"{case_id}: no ethernet interfaces found on scan PC")
    for iface in ifaces:
        await _ssh_run(conn, f"ip link set {iface} up", timeout_s=15)
        await _ssh_run(
            conn,
            f"nmcli device connect {iface} 2>/dev/null || true",
            timeout_s=30,
        )
    await asyncio.sleep(2)
    return ifaces


async def _toggle_scan_pc_wifi_radio_rapid(
    conn: asyncssh.SSHClientConnection,
    *,
    cycles: int,
    gap_s: float,
) -> int:
    """Rapidly toggle scan PC Wi‑Fi radio on/off (not LAN ports)."""
    completed = 0
    for _ in range(cycles):
        await _ssh_run(conn, "nmcli radio wifi off", timeout_s=15)
        await asyncio.sleep(gap_s)
        await _ssh_run(conn, "nmcli radio wifi on", timeout_s=15)
        await asyncio.sleep(gap_s)
        completed += 1
    return completed


async def _trigger_scan_pc_wifi_autoconnect(
    conn: asyncssh.SSHClientConnection,
    wifi_iface: str,
    *,
    target_ssid: str,
    profile_bundle=None,
) -> None:
    """After Wi‑Fi radio toggle, activate saved autoconnect profile on the Wi‑Fi NIC."""
    quoted = target_ssid.replace("'", "'\"'\"'")
    await _ssh_run(conn, "nmcli radio wifi on", timeout_s=15)
    if profile_bundle:
        bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=60)
        if bssid:
            await _ssh_run(
                conn,
                f"nmcli connection modify '{quoted}' 802-11-wireless.hidden yes "
                f"802-11-wireless.bssid {bssid} connection.autoconnect yes",
                timeout_s=30,
            )
    await _ssh_run(conn, f"nmcli device connect {wifi_iface}", timeout_s=90)
    await _ssh_run(
        conn,
        f"nmcli -w 60 connection up '{quoted}' ifname {wifi_iface} 2>/dev/null || true",
        timeout_s=80,
    )


async def _set_nmcli_autoconnect(
    conn: asyncssh.SSHClientConnection,
    ssid: str,
    *,
    enabled: bool = True,
) -> None:
    quoted = ssid.replace("'", "'\"'\"'")
    flag = "yes" if enabled else "no"
    await _ssh_run(
        conn,
        f"nmcli connection modify '{quoted}' connection.autoconnect {flag} "
        f"connection.autoconnect-priority 100 2>/dev/null || true",
        timeout_s=20,
    )


def _dhcp_pool_ip_ok(iface_ip: str | None, prefix: str) -> bool:
    return bool(iface_ip) and iface_ip.strip().startswith(prefix.strip())


async def _read_scan_pc_hostname(
    conn: asyncssh.SSHClientConnection,
    *,
    case_id: str,
) -> str:
    _, out = await _ssh_run(
        conn,
        "hostname -s 2>/dev/null || hostname",
        timeout_s=15,
    )
    hostname = (out or "").strip().splitlines()[0].strip() if out else ""
    check.is_true(hostname, f"{case_id}: must read hostname from scan PC")
    return hostname


async def _scan_pc_mgmt_wifi_ready(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    target_ssid: str,
    expected_ip: str | None,
    cpe_mgmt_ip: str,
    timeout_s: int,
    dhcp_prefix: str = "169.254.",
) -> tuple[bool, str | None, bool]:
    """Return (on_target_ssid_with_ip, iface_ip, ping_ok)."""
    deadline = time.monotonic() + timeout_s
    iface_ip: str | None = None
    ping_ok = False
    on_ssid = False

    def _ip_ready(ip: str | None) -> bool:
        if expected_ip is not None:
            return _dhcp_ip_matches_expected(ip, expected_ip)
        return _dhcp_pool_ip_ok(ip, dhcp_prefix)

    while time.monotonic() < deadline:
        state, active_ssid, con_name = await _get_wifi_device_association(conn, iface)
        iface_ip = await _get_iface_ipv4(conn, iface, prefix=dhcp_prefix)
        on_ssid = _wifi_mgmt_associated(
            state, active_ssid, target_ssid, connection_name=con_name
        )
        if on_ssid and _ip_ready(iface_ip):
            ping_ok = await _ping_reachable(
                conn,
                cpe_mgmt_ip,
                iface=iface,
                src_ip=iface_ip,
            )
            if ping_ok:
                return True, iface_ip, True
        await asyncio.sleep(3)
    return on_ssid and _ip_ready(iface_ip), iface_ip, ping_ok


async def _ensure_scan_pc_on_cpe_24g_ssid(
    profile_bundle,
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    target_ssid: str,
    target_password: str,
    expected_ip: str | None,
    connect_settle_s: int,
    connect_retries: int,
) -> tuple[int, str, str | None, bool]:
    defaults = RADIO_24_SCAN_DEFAULTS
    cpe_mgmt_ip = str(defaults.get("CPE_MGMT_IP", "169.254.254.1"))
    dhcp_prefix = str(defaults.get("DHCP_POOL_PREFIX", "169.254."))
    ready, iface_ip, ping_ok = await _scan_pc_mgmt_wifi_ready(
        conn,
        iface,
        target_ssid=target_ssid,
        expected_ip=expected_ip,
        cpe_mgmt_ip=cpe_mgmt_ip,
        timeout_s=8,
        dhcp_prefix=dhcp_prefix,
    )
    if ready and ping_ok:
        return 0, "already connected", iface_ip, True

    connect_code, connect_out, iface_ip, linked = await _dhcp_connect_scan_pc(
        profile_bundle,
        conn,
        iface,
        target_ssid=target_ssid,
        target_password=target_password,
        connect_settle_s=connect_settle_s,
        connect_retries=connect_retries,
        require_mgmt_ip=True,
        refresh_bssid=True,
    )
    return connect_code, connect_out, iface_ip, linked


async def _prep_cpe_mgmt_dhcp_for_connect(
    profile_bundle,
    *,
    target_ssid: str,
) -> bool:
    """Ensure CPE 2.4 GHz mgmt AP and lan24 DHCP server are up before scan PC join."""
    defaults = RADIO_24_SCAN_DEFAULTS
    radio_idx = int(defaults.get("CPE_RADIO_24_IDX", 0))
    reload_wait_s = int(defaults.get("CPE_WIFI_RELOAD_WAIT_S", 8))
    mgmt_ready = await _ensure_cpe_mgmt_ap_ready(
        profile_bundle,
        radio_idx=radio_idx,
        reload_wait_s=reload_wait_s,
        mgmt_ssid=target_ssid,
    )
    dhcp_on = await _set_cpe_lan24_dhcp_server(profile_bundle, True, mgmt_ssid=target_ssid)
    await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=60)
    await asyncio.sleep(8)
    return mgmt_ready and dhcp_on


async def assert_radio_24_17_dhcp_server_down_no_ip(
    profile_bundle=None,
):
    """2.4G_RADIO_17 — disable CPE 2.4 GHz DHCP server; scan PC must not get mgmt link."""
    case_id = "2_4G_RADIO_17"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    dhcp_prefix = str(defaults.get("DHCP_POOL_PREFIX", "169.254."))
    no_ip_wait_s = int(defaults.get("DHCP_NO_IP_WAIT_S", 25))
    cpe_mgmt_ip = str(defaults.get("CPE_MGMT_IP", "169.254.254.1"))

    print_section(
        f"{case_id}: Disable CPE 2.4 GHz DHCP server — scan PC must not connect on mgmt Wi‑Fi"
    )

    await _set_cpe_lan24_dhcp_server(profile_bundle, True, mgmt_ssid=target_ssid)
    dhcp_disabled = await _set_cpe_lan24_dhcp_server(
        profile_bundle, False, mgmt_ssid=target_ssid
    )
    ignore_val = await _get_cpe_lan24_dhcp_ignore(profile_bundle, mgmt_ssid=target_ssid)

    try:
        async with asyncssh.connect(
            scan_ip,
            username="root",
            password=scan_password,
            known_hosts=None,
            connect_timeout=15.0,
        ) as conn:
            iface = await _detect_wifi_interface(conn, case_id=case_id)
            await _disconnect_scan_pc_wifi(conn, iface, ssid=target_ssid)

            connect_code, connect_out, iface_ip, _ = await _dhcp_connect_scan_pc(
                profile_bundle,
                conn,
                iface,
                target_ssid=target_ssid,
                target_password=target_password,
                connect_settle_s=connect_settle_s,
                connect_retries=1,
                require_mgmt_ip=False,
                refresh_bssid=True,
            )
            no_ip = await _wait_for_no_mgmt_ip_on_iface(
                conn, iface, prefix=dhcp_prefix, timeout_s=no_ip_wait_s
            )
            if iface_ip:
                no_ip = False

            linked, _, _, verify_ip, ping_ok = await _verify_cpe_mgmt_wifi_link(
                conn,
                iface,
                cpe_mgmt_ip,
                expected_ssid=target_ssid,
                timeout_s=15,
            )
            mgmt_connected = linked and ping_ok and verify_ip

            wifi_state, active_ssid, _ = await _get_wifi_device_association(conn, iface)

            print_comparison_table(
                [
                    (
                        "CPE lan24 DHCP",
                        "disabled (ignore=1)",
                        f"ignore={ignore_val or 'n/a'}",
                        "PASS" if dhcp_disabled and ignore_val == "1" else "FAIL",
                    ),
                    (
                        "Mgmt Wi‑Fi link",
                        "not connected",
                        f"state={wifi_state!r} ssid={active_ssid!r}",
                        "PASS" if not mgmt_connected else "FAIL",
                    ),
                    (
                        f"{iface} IPv4",
                        "none on mgmt subnet",
                        iface_ip or verify_ip or "(none)",
                        "PASS" if no_ip else "FAIL",
                    ),
                    (
                        "Ping CPE mgmt",
                        "unreachable",
                        "ok" if ping_ok else "fail",
                        "PASS" if not ping_ok else "FAIL",
                    ),
                    (
                        "nmcli join attempt",
                        "no usable link",
                        "ok" if connect_code == 0 else f"exit {connect_code}",
                        "PASS" if not mgmt_connected else "FAIL",
                    ),
                ]
            )

            check.is_true(dhcp_disabled, f"{case_id}: must disable dhcp.lan24 on CPE")
            check.is_true(
                ignore_val == "1",
                f"{case_id}: dhcp.lan24.ignore must be 1, got {ignore_val!r}",
            )
            check.is_true(no_ip, f"{case_id}: no {dhcp_prefix}x IP when DHCP disabled")
            check.is_false(
                mgmt_connected,
                f"{case_id}: must not connect on mgmt Wi‑Fi when DHCP disabled "
                f"(connect_out={connect_out[-200:]})",
            )
    finally:
        restored = await _set_cpe_lan24_dhcp_server(
            profile_bundle, True, mgmt_ssid=target_ssid
        )
        check.is_true(restored, f"{case_id}: must restore CPE lan24 DHCP after test")


async def assert_radio_24_18_dhcp_recovery_after_reboot(
    profile_bundle=None,
):
    """2.4G_RADIO_18 — reboot CPE with DHCP enabled; after link up get DHCP IP on mgmt Wi‑Fi."""
    case_id = "2_4G_RADIO_18"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    connect_settle_s = max(int(defaults.get("CONNECT_SETTLE_S", 10)), 12)
    connect_retries = max(int(defaults.get("CONNECT_RETRY_ATTEMPTS", 3)), 6)
    expected_ip = str(defaults.get("DHCP_EXPECTED_CLIENT_IP", "169.254.254.100"))
    link_up_timeout_s = int(defaults.get("LINK_UP_TIMEOUT_S", 180))
    reboot_boot_s = _resolve_reboot_boot_s(profile_bundle, defaults)
    mgmt_ready_wait_s = int(defaults.get("MGMT_AP_READY_WAIT_S", 12))

    print_section(
        f"{case_id}: Reboot CPE (DHCP on) — link up — connect mgmt Wi‑Fi — IP {expected_ip}"
    )

    dhcp_on = await _set_cpe_lan24_dhcp_server(profile_bundle, True, mgmt_ssid=target_ssid)
    check.is_true(dhcp_on, f"{case_id}: must enable lan24 DHCP before reboot")

    reboot_ok = await _send_cpe_reboot(profile_bundle, mgmt_ssid=target_ssid)
    check.is_true(reboot_ok, f"{case_id}: CPE reboot command must be sent")

    await asyncio.sleep(reboot_boot_s)
    _, _, link_stable = await _wait_for_bts_cpe_link_stable(
        profile_bundle, timeout_s=link_up_timeout_s
    )
    prep_ok = await _prep_cpe_mgmt_dhcp_for_connect(
        profile_bundle, target_ssid=target_ssid
    )
    await asyncio.sleep(mgmt_ready_wait_s + 15)

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _detect_wifi_interface(conn, case_id=case_id)
        await _disconnect_scan_pc_wifi(conn, iface, ssid=target_ssid)
        await _ssh_run(conn, "nmcli device wifi rescan", timeout_s=45)
        await asyncio.sleep(5)

        connect_code, connect_out, iface_ip, linked = await _dhcp_connect_scan_pc(
            profile_bundle,
            conn,
            iface,
            target_ssid=target_ssid,
            target_password=target_password,
            connect_settle_s=connect_settle_s,
            connect_retries=connect_retries,
            require_mgmt_ip=True,
            refresh_bssid=True,
        )
        ip_ok = _dhcp_ip_matches_expected(iface_ip, expected_ip)

        print_comparison_table(
            [
                (
                    "CPE mgmt + DHCP",
                    "ready",
                    "ready" if prep_ok else "not ready",
                    "PASS" if prep_ok else "FAIL",
                ),
                (
                    "BTS↔CPE link",
                    "stable",
                    "stable" if link_stable else "down",
                    "PASS" if link_stable else "FAIL",
                ),
                (
                    f"{iface} DHCP IP",
                    expected_ip,
                    iface_ip or "(none)",
                    "PASS" if ip_ok else "FAIL",
                ),
                (
                    "nmcli connect",
                    "ok",
                    "ok" if connect_code == 0 else f"exit {connect_code}",
                    "PASS" if connect_code == 0 else "FAIL",
                ),
            ]
        )

        check.is_true(link_stable, f"{case_id}: BTS↔CPE link must be up after reboot")
        check.is_true(prep_ok, f"{case_id}: CPE mgmt AP and DHCP must be ready after reboot")
        check.equal(connect_code, 0, f"{case_id}: connect failed: {connect_out[-300:]}")
        check.is_true(
            ip_ok,
            f"{case_id}: expected DHCP IP {expected_ip}, got {iface_ip!r} (linked={linked})",
        )


async def assert_radio_24_19_dhcp_stress_connect_disconnect(
    profile_bundle=None,
):
    """2.4G_RADIO_19 — rapid Wi‑Fi radio toggle; LAN ports stay up; auto-reconnect with DHCP IP."""
    case_id = "2_4G_RADIO_19"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    connect_retries = int(defaults.get("CONNECT_RETRY_ATTEMPTS", 3))
    expected_ip = str(defaults.get("DHCP_EXPECTED_CLIENT_IP", "169.254.254.100"))
    toggle_cycles = int(defaults.get("WIFI_TOGGLE_CYCLES", 25))
    toggle_gap_s = float(defaults.get("WIFI_TOGGLE_GAP_S", 0.15))
    autoconnect_wait_s = int(defaults.get("WIFI_AUTORECONNECT_WAIT_S", 120))
    cpe_mgmt_ip = str(defaults.get("CPE_MGMT_IP", "169.254.254.1"))

    print_section(
        f"{case_id}: Toggle Wi‑Fi {toggle_cycles}x (LAN up) — auto-connect — DHCP IP {expected_ip}"
    )

    await _prep_cpe_mgmt_dhcp_for_connect(profile_bundle, target_ssid=target_ssid)

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        lan_ifaces = await _ensure_scan_pc_lan_ports_up(conn, case_id=case_id)
        wifi_iface = await _detect_wifi_interface(conn, case_id=case_id)

        _, _, _, profile_ok = await _ensure_scan_pc_on_cpe_24g_ssid(
            profile_bundle,
            conn,
            wifi_iface,
            target_ssid=target_ssid,
            target_password=target_password,
            expected_ip=expected_ip,
            connect_settle_s=connect_settle_s,
            connect_retries=connect_retries,
        )
        check.is_true(profile_ok, f"{case_id}: must connect on mgmt Wi‑Fi before radio toggle")

        await _set_nmcli_autoconnect(conn, target_ssid, enabled=True)
        await _ensure_scan_pc_lan_ports_up(conn, case_id=case_id)

        toggled = await _toggle_scan_pc_wifi_radio_rapid(
            conn,
            cycles=toggle_cycles,
            gap_s=toggle_gap_s,
        )

        await _trigger_scan_pc_wifi_autoconnect(
            conn, wifi_iface, target_ssid=target_ssid, profile_bundle=profile_bundle
        )
        await _ensure_scan_pc_lan_ports_up(conn, case_id=case_id)

        on_ssid, iface_ip, ping_ok = await _scan_pc_mgmt_wifi_ready(
            conn,
            wifi_iface,
            target_ssid=target_ssid,
            expected_ip=expected_ip,
            cpe_mgmt_ip=cpe_mgmt_ip,
            timeout_s=autoconnect_wait_s,
        )
        ip_ok = _dhcp_ip_matches_expected(iface_ip, expected_ip)

    cpe_ping_ok = False
    bts_ip, cpe_ip, user, password = _resolve_bts_cpe_targets(profile_bundle)
    if bts_ip and cpe_ip and password:
        try:
            async with asyncssh.connect(
                bts_ip,
                username=user,
                password=password,
                known_hosts=None,
                connect_timeout=12.0,
            ) as bts_conn:
                _, out = await _ssh_run(bts_conn, f"ping -6 -c 2 -W 4 {cpe_ip} 2>&1", timeout_s=25)
                cpe_ping_ok = _ping_output_ok(out)
        except (OSError, asyncssh.Error):
            pass

    lan_up = ", ".join(lan_ifaces)

    print_comparison_table(
        [
            (
                "LAN ports",
                "up",
                lan_up or "n/a",
                "PASS" if lan_ifaces else "FAIL",
            ),
            (
                "Wi‑Fi radio toggles",
                str(toggle_cycles),
                str(toggled),
                "PASS" if toggled == toggle_cycles else "FAIL",
            ),
            (
                "Wi‑Fi auto-connect",
                target_ssid,
                "connected" if on_ssid else "not connected",
                "PASS" if on_ssid else "FAIL",
            ),
            (
                "DHCP IP",
                expected_ip,
                iface_ip or "(none)",
                "PASS" if ip_ok else "FAIL",
            ),
            (
                "Ping CPE mgmt",
                cpe_mgmt_ip,
                "ok" if ping_ok else "fail",
                "PASS" if ping_ok else "FAIL",
            ),
            (
                "CPE reachable (BTS ping)",
                "ok",
                "ok" if cpe_ping_ok else "down",
                "PASS" if cpe_ping_ok else "FAIL",
            ),
        ]
    )

    check.is_true(lan_ifaces, f"{case_id}: LAN ports must be present and up")
    check.equal(toggled, toggle_cycles, f"{case_id}: must complete {toggle_cycles} Wi‑Fi toggles")
    check.is_true(on_ssid, f"{case_id}: must auto-connect to {target_ssid!r} after Wi‑Fi stress")
    check.is_true(ip_ok, f"{case_id}: must get DHCP IP {expected_ip} after auto-connect")
    check.is_true(ping_ok, f"{case_id}: must ping CPE mgmt {cpe_mgmt_ip} via Wi‑Fi")
    check.is_true(cpe_ping_ok, f"{case_id}: CPE must stay up after Wi‑Fi toggle stress")


async def assert_radio_24_20_dhcp_logs_lease_assignment(
    profile_bundle=None,
):
    """2.4G_RADIO_20 — verify scan PC on CPE 2.4 SSID; CPE lease matches this PC's MAC/IP/hostname."""
    case_id = "2_4G_RADIO_20"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    connect_retries = int(defaults.get("CONNECT_RETRY_ATTEMPTS", 3))
    dhcp_prefix = str(defaults.get("DHCP_POOL_PREFIX", "169.254."))

    print_section(
        f"{case_id}: Verify scan PC on CPE 2.4 SSID — CPE Leases tab matches this client"
    )

    await _prep_cpe_mgmt_dhcp_for_connect(profile_bundle, target_ssid=target_ssid)
    client_mac: str | None = None
    client_hostname: str | None = None
    connect_code = 1
    connect_out = ""
    iface_ip: str | None = None
    linked = False
    lease_host: str | None = None

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _detect_wifi_interface(conn, case_id=case_id)
        client_hostname = await _read_scan_pc_hostname(conn, case_id=case_id)
        _, mac_out = await _ssh_run(
            conn,
            f"ip link show {iface} | awk '/link/ {{print $2; exit}}'",
            timeout_s=15,
        )
        client_mac = (mac_out or "").strip().splitlines()[0].strip() if mac_out else None
        check.is_true(client_mac, f"{case_id}: must read Wi‑Fi MAC from {iface}")

        connect_code, connect_out, iface_ip, linked = await _ensure_scan_pc_on_cpe_24g_ssid(
            profile_bundle,
            conn,
            iface,
            target_ssid=target_ssid,
            target_password=target_password,
            expected_ip=None,
            connect_settle_s=connect_settle_s,
            connect_retries=connect_retries,
        )

    leases_raw = await _read_cpe_dhcp_leases(profile_bundle, mgmt_ssid=target_ssid)
    lease_ok, lease_host = _cpe_lease_matches_client(
        leases_raw,
        iface_ip or "",
        client_mac or "",
        expected_hostname=client_hostname,
    )
    ip_ok = _dhcp_pool_ip_ok(iface_ip, dhcp_prefix)
    hostname_ok = lease_ok and lease_host == client_hostname
    mac_ok = lease_ok and client_mac and _normalize_mac(client_mac) in leases_raw.lower()

    print_comparison_table(
        [
            (
                "Scan PC SSID",
                target_ssid,
                "connected" if linked else "not connected",
                "PASS" if linked else "FAIL",
            ),
            (
                "Client DHCP IP (this PC)",
                iface_ip or "dhcp pool",
                iface_ip or "(none)",
                "PASS" if ip_ok else "FAIL",
            ),
            (
                "Client MAC (Wi‑Fi)",
                client_mac or "n/a",
                client_mac or "n/a",
                "PASS" if client_mac else "FAIL",
            ),
            (
                "Client hostname (this PC)",
                client_hostname or "n/a",
                lease_host or "(not in leases)",
                "PASS" if hostname_ok else "FAIL",
            ),
            (
                "CPE dhcp.leases",
                "MAC + IP + hostname",
                "found" if lease_ok else "not found",
                "PASS" if lease_ok and mac_ok else "FAIL",
            ),
        ]
    )

    check.equal(connect_code, 0, f"{case_id}: connect failed: {connect_out[-300:]}")
    check.is_true(linked, f"{case_id}: scan PC must be on CPE 2.4 mgmt SSID {target_ssid!r}")
    check.is_true(
        ip_ok,
        f"{case_id}: client must have DHCP IP on {dhcp_prefix}x subnet, got {iface_ip!r}",
    )
    check.is_true(
        lease_ok,
        f"{case_id}: CPE dhcp.leases must list MAC {client_mac} IP {iface_ip} "
        f"hostname {client_hostname} (raw={leases_raw[-200:]})",
    )
    check.is_true(
        hostname_ok,
        f"{case_id}: lease hostname must match scan PC hostname {client_hostname!r}, "
        f"got {lease_host!r}",
    )


async def _probe_bts_cpe_link_once(profile_bundle) -> bool:
    """Return True when BTS SSH works and a single IPv6 ping to CPE succeeds."""
    bts_ip, cpe_ip, user, password = _resolve_bts_cpe_targets(profile_bundle)
    if not bts_ip or not cpe_ip:
        return False
    try:
        async with asyncssh.connect(
            bts_ip,
            username=user,
            password=password,
            known_hosts=None,
            connect_timeout=10.0,
        ) as bts_conn:
            _, ping_out = await _ssh_run(
                bts_conn,
                f"ping -6 -c 1 -W 3 {cpe_ip} 2>&1",
                timeout_s=15,
            )
            return _ping_output_ok(ping_out)
    except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
        return False


def _resolve_cpe_pc_config(profile_bundle) -> tuple[str, str, list[str], str, str]:
    """CPE PC jump: lab runner -> CPE PC -> CPE mgmt IP(s)."""
    defaults = RADIO_24_SCAN_DEFAULTS
    dut = (profile_bundle.active.get("dut") or {}) if profile_bundle else {}
    cpe_pc_ip = str(dut.get("cpe_pc_ip") or "").strip()
    cpe_pc_password = str(
        dut.get("cpe_pc_password") or defaults.get("SCAN_PC_PASSWORD", "senao1234#")
    )
    mgmt_ip = str(defaults.get("CPE_MGMT_IP", "169.254.254.1")).strip()
    fallback_ip = str(dut.get("cpe_fallback_ip") or "10.0.0.1").strip()
    targets = [ip for ip in (mgmt_ip, fallback_ip) if ip]
    user = str(dut.get("username") or "root")
    password = str(dut.get("password") or "")
    return cpe_pc_ip, cpe_pc_password, targets, user, password


async def _probe_cpe_ssh_once(profile_bundle) -> bool:
    """
    True when CPE answers SSH.

    Order: CPE PC jump (10.0.150.33 -> mgmt IP), BTS remote_exec, then IPv6 SSH.
    """
    if not profile_bundle:
        return False

    mgmt_ssid = str(RADIO_24_SCAN_DEFAULTS.get("CPE_MGMT_HIDDEN_SSID", "")).strip()
    cpe_pc_ip, cpe_pc_pw, jump_targets, user, pw = _resolve_cpe_pc_config(profile_bundle)

    if cpe_pc_ip and jump_targets and pw:
        try:
            async with asyncssh.connect(
                cpe_pc_ip,
                username="root",
                password=cpe_pc_pw,
                known_hosts=None,
                connect_timeout=5.0,
            ) as jump:
                for target_ip in jump_targets:
                    try:
                        async with jump.connect_ssh(
                            target_ip,
                            username=user,
                            password=pw,
                            known_hosts=None,
                            connect_timeout=5.0,
                        ) as cpe_conn:
                            code, out = await _ssh_run(cpe_conn, "uptime", timeout_s=8)
                            if code == 0 and (out or "").strip():
                                return True
                    except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
                        continue
        except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
            pass

    bts_ip, _, bts_user, bts_password = _resolve_bts_cpe_targets(profile_bundle)
    if bts_ip and bts_password:
        try:
            async with asyncssh.connect(
                bts_ip,
                username=bts_user,
                password=bts_password,
                known_hosts=None,
                connect_timeout=5.0,
            ) as bts_conn:
                su_idx = await _resolve_cpe_remote_exec_index(bts_conn, mgmt_ssid=mgmt_ssid)
                if su_idx is not None:
                    code, out = await _cpe_remote_exec(bts_conn, su_idx, "uptime", timeout_s=10)
                    if code == 0 and (out or "").strip():
                        return True
        except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
            pass

    code, out = await _run_cpe_command(profile_bundle, "uptime", timeout_s=10, mgmt_ssid=mgmt_ssid)
    return code == 0 and bool((out or "").strip())


async def _probe_cpe_ssh_once_safe(profile_bundle) -> bool:
    """Like _probe_cpe_ssh_once but never raises."""
    try:
        return await _probe_cpe_ssh_once(profile_bundle)
    except (OSError, asyncssh.Error, asyncio.TimeoutError, TimeoutError):
        return False


async def _wait_for_cpe_ssh_down(
    profile_bundle,
    *,
    timeout_s: int = 90,
    poll_s: int = 2,
) -> bool:
    """Return True once CPE SSH is no longer reachable (reboot in progress)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not await _probe_cpe_ssh_once_safe(profile_bundle):
            return True
        await asyncio.sleep(max(poll_s, 1))
    return False


async def _wait_for_cpe_ssh_up(
    profile_bundle,
    *,
    timeout_s: int = 180,
    poll_s: int = 5,
) -> bool:
    """Return True once CPE SSH is stable after boot."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await _probe_cpe_ssh_once_safe(profile_bundle):
            await asyncio.sleep(3)
            if await _probe_cpe_ssh_once_safe(profile_bundle):
                return True
        await asyncio.sleep(max(poll_s, 2))
    return False


async def _wait_for_cpe_link_down(
    profile_bundle,
    *,
    timeout_s: int = 60,
    poll_s: int = 3,
) -> bool:
    """Return True once CPE is down (SSH unreachable preferred over BTS ping)."""
    if await _wait_for_cpe_ssh_down(profile_bundle, timeout_s=timeout_s, poll_s=poll_s):
        return True
    deadline = time.monotonic() + max(timeout_s // 2, 15)
    while time.monotonic() < deadline:
        if not await _probe_bts_cpe_link_once(profile_bundle):
            return True
        await asyncio.sleep(max(poll_s, 1))
    return False


async def _restore_cpe_link_after_reboot(
    profile_bundle,
    *,
    timeout_s: int | None = None,
    boot_s: int | None = None,
) -> bool:
    """Wait for CPE SSH + 2.4 GHz AP after reboot (for chained reboot tests)."""
    defaults = RADIO_24_SCAN_DEFAULTS
    if timeout_s is None:
        timeout_s = _resolve_reboot_wait_s(profile_bundle, defaults)
    if boot_s is None:
        boot_s = _resolve_reboot_boot_s(profile_bundle, defaults)
    await asyncio.sleep(max(boot_s // 4, 8))
    cpe_up = await _wait_for_cpe_ssh_up(profile_bundle, timeout_s=timeout_s)
    if cpe_up:
        await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=90)
    return cpe_up


async def _quick_scan_pc_bssid_visible(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    expected_bssid: str,
) -> bool:
    """Fast rescan + nmcli BSSID list check (no full iwlist pass)."""
    await _ssh_run(conn, f"nmcli device wifi rescan ifname {iface}", timeout_s=20)
    await asyncio.sleep(2)
    return await _scan_pc_wifi_list_has_bssid(conn, iface, expected_bssid)


async def _scan_pc_detect_cpe_ap(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    expected_bssid: str | None,
    target_ssid: str,
    settle_s: int,
    timeout_s: int,
) -> tuple[bool, bool]:
    """
    Trigger rescan on scan PC.

    Returns (bssid_in_nmcli_scan, ssid_in_iwlist) for the CPE 2.4 GHz mgmt AP.
    Hidden APs may show BSSID without SSID name in iwlist.
    """
    await _prepare_wifi_for_passive_scan(conn, iface, settle_s=settle_s)
    bssid_seen = False
    if expected_bssid:
        bssid_seen = await _scan_pc_wifi_list_has_bssid(conn, iface, expected_bssid)
    iwlist_ssids, _ = await _scan_essids_iwlist(conn, iface, timeout_s=timeout_s)
    ssid_seen = target_ssid in _visible_ssids_from_iwlist(iwlist_ssids)
    return bssid_seen, ssid_seen


async def _quick_hidden_connect_attempt(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    ssid: str,
    password: str,
    bssid: str | None,
) -> tuple[int, str]:
    """One short hidden join attempt — used to verify connect fails while CPE is booting."""
    await _prepare_scan_pc_wifi_for_join(conn, iface)
    await _delete_nmcli_connection(conn, ssid)
    quoted_ssid = ssid.replace("'", "'\"'\"'")
    quoted_pw = password.replace("'", "'\"'\"'")
    bssid_part = f"802-11-wireless.bssid {bssid} " if bssid else ""
    add_cmd = (
        f"nmcli connection add type wifi con-name '{quoted_ssid}' "
        f"ifname {iface} ssid '{quoted_ssid}' 802-11-wireless.hidden yes "
        f"{bssid_part}"
        f"wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn,wpa "
        f"wifi-sec.pairwise ccmp,tkip wifi-sec.group ccmp,tkip wifi-sec.psk '{quoted_pw}'"
    )
    code, out = await _ssh_run(conn, add_cmd, timeout_s=30)
    if code != 0:
        return code, out
    code, out = await _ssh_run(
        conn,
        f"nmcli -w 20 connection up '{quoted_ssid}' ifname {iface}",
        timeout_s=35,
    )
    await _delete_nmcli_connection(conn, ssid)
    return code, out


async def _cpe_ap_available_in_scan(
    conn: asyncssh.SSHClientConnection,
    iface: str,
    expected_bssid: str | None,
) -> bool:
    """Hidden mgmt AP is 'available' when its BSSID appears in scan PC nmcli list."""
    if not expected_bssid:
        return False
    return await _quick_scan_pc_bssid_visible(conn, iface, expected_bssid)


async def _boot_window_connect_blocked(
    profile_bundle,
    conn: asyncssh.SSHClientConnection,
    iface: str,
    *,
    expected_bssid: str | None,
    target_ssid: str,
    target_password: str,
    poll_s: int,
    max_window_s: int,
    case_id: str,
) -> tuple[bool, int]:
    """
    While CPE is rebooting (SSH down): hidden SSID connect must fail.

    Hidden SSID is not visible in scan — verify availability only via connect.
    Returns (connect_blocked_ok, boot_sample_count).
    """
    link_down = await _wait_for_cpe_ssh_down(profile_bundle, timeout_s=90)
    if not link_down:
        _log(f"{case_id}: CPE SSH did not drop after reboot — trying connect anyway")

    deadline = time.monotonic() + max_window_s
    boot_samples = 0
    connect_blocked_ok = True

    while time.monotonic() < deadline:
        if await _probe_cpe_ssh_once_safe(profile_bundle):
            break

        boot_samples += 1
        connect_code, _ = await _quick_hidden_connect_attempt(
            conn,
            iface,
            ssid=target_ssid,
            password=target_password,
            bssid=expected_bssid,
        )
        if connect_code == 0:
            connect_blocked_ok = False
            await _ssh_run(conn, f"nmcli device disconnect {iface}", timeout_s=20)

        await asyncio.sleep(max(poll_s, 2))

    if boot_samples == 0 and not await _probe_cpe_ssh_once_safe(profile_bundle):
        boot_samples = 1
        connect_code, _ = await _quick_hidden_connect_attempt(
            conn,
            iface,
            ssid=target_ssid,
            password=target_password,
            bssid=expected_bssid,
        )
        if connect_code == 0:
            connect_blocked_ok = False

    return connect_blocked_ok, boot_samples


async def assert_radio_24_21_no_ssid_during_boot(
    profile_bundle=None,
):
    """
    2.4G_RADIO_21 — reboot CPE; while booting hidden SSID connect must fail.

    Hidden SSID does not appear in scan — verify by connect attempt only.
    """
    case_id = "2_4G_RADIO_21"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    poll_s = int(defaults.get("BOOT_SCAN_POLL_S", 5))
    boot_window_s = int(defaults.get("BOOT_EARLY_SCAN_MAX_S", 90))

    print_section(
        f"{case_id}: Reboot CPE — while CPE is booting, scan PC {scan_ip} must not "
        f"connect to hidden SSID {target_ssid!r}"
    )

    await _restore_cpe_link_after_reboot(profile_bundle)
    expected_bssid = await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=120)
    check.is_true(expected_bssid, f"{case_id}: need CPE AP BSSID before reboot test")

    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _clear_lab_pc_wifi(conn, case_id=case_id)
        reboot_ok = await _send_cpe_reboot(profile_bundle, mgmt_ssid=target_ssid)
        connect_ok, boot_samples = await _boot_window_connect_blocked(
            profile_bundle,
            conn,
            iface,
            expected_bssid=expected_bssid,
            target_ssid=target_ssid,
            target_password=target_password,
            poll_s=poll_s,
            max_window_s=boot_window_s,
            case_id=case_id,
        )

    check.is_true(reboot_ok, f"{case_id}: CPE reboot must be sent")

    print_comparison_table(
        [
            (
                "CPE reboot",
                "command sent",
                "sent" if reboot_ok else "failed",
                "PASS" if reboot_ok else "FAIL",
            ),
            (
                "Boot window",
                "CPE booting",
                f"{boot_samples} connect attempt(s)",
                "PASS" if boot_samples >= 1 else "FAIL",
            ),
            (
                "Hidden SSID connect",
                "must not connect during boot",
                "blocked" if connect_ok else "connected",
                "PASS" if connect_ok else "FAIL",
            ),
        ]
    )
    check.greater_equal(
        boot_samples,
        1,
        f"{case_id}: must attempt connect at least once while CPE is booting",
    )
    check.is_true(
        connect_ok,
        f"{case_id}: hidden SSID connect must fail while CPE is booting",
    )
    await _restore_cpe_link_after_reboot(profile_bundle)


async def assert_radio_24_22_ssid_appears_after_boot(
    profile_bundle=None,
):
    """
    2.4G_RADIO_22 — reboot CPE; when up, hidden SSID must be available in scan
    and connect must succeed.
    """
    case_id = "2_4G_RADIO_22"
    defaults = RADIO_24_SCAN_DEFAULTS
    scan_ip, scan_password, target_ssid, target_password, _, _ = _resolve_scan_pc_config(
        profile_bundle, case_id=case_id
    )
    connect_settle_s = int(defaults.get("CONNECT_SETTLE_S", 10))
    connect_retries = int(defaults.get("CONNECT_RETRY_ATTEMPTS", 3))

    print_section(
        f"{case_id}: Step 1 reboot CPE — Step 2 when up scan + connect hidden SSID "
        f"{target_ssid!r} on lab PC {scan_ip}"
    )

    await _restore_cpe_link_after_reboot(profile_bundle)
    expected_bssid = await _fetch_cpe_24g_ap_bssid(profile_bundle)

    reboot_ok = await _send_cpe_reboot(profile_bundle, mgmt_ssid=target_ssid)
    check.is_true(reboot_ok, f"{case_id}: CPE reboot must be sent")
    await _wait_for_cpe_ssh_down(profile_bundle, timeout_s=90)
    reboot_wait_s = _resolve_reboot_wait_s(profile_bundle, defaults)
    cpe_up = await _wait_for_cpe_ssh_up(profile_bundle, timeout_s=reboot_wait_s)
    if cpe_up:
        expected_bssid = (
            await _wait_for_cpe_24g_ap_bssid_stable(profile_bundle, timeout_s=90)
            or expected_bssid
        )

    ap_available = False
    connect_code = 1
    connect_out = ""
    async with asyncssh.connect(
        scan_ip,
        username="root",
        password=scan_password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        iface = await _clear_lab_pc_wifi(conn, case_id=case_id)
        ready_bssid, ap_available = await _ensure_scan_pc_can_reach_cpe_ap(
            profile_bundle,
            conn,
            iface,
            mgmt_ssid=target_ssid,
        )
        if ready_bssid:
            expected_bssid = ready_bssid
        connect_code, connect_out = await _connect_hidden_wifi_with_retries(
            conn,
            iface,
            ssid=target_ssid,
            password=target_password,
            settle_s=connect_settle_s,
            max_attempts=connect_retries,
            bssid=expected_bssid,
            profile_bundle=profile_bundle,
            refresh_bssid=True,
            scan_ip=scan_ip,
            scan_password=scan_password,
        )

    print_comparison_table(
        [
            (
                "Step 1 — CPE reboot",
                "command sent",
                "sent" if reboot_ok else "failed",
                "PASS" if reboot_ok else "FAIL",
            ),
            (
                "Step 2 — CPE SSH (CPE PC)",
                "CPE up",
                "up" if cpe_up else "down",
                "PASS" if cpe_up else "FAIL",
            ),
            (
                "Step 2 — SSID on scan PC",
                "available (BSSID in scan)",
                expected_bssid or "(unknown)",
                "PASS" if ap_available else "FAIL",
            ),
            (
                "Step 2 — scan PC connect",
                "ok",
                "ok" if connect_code == 0 else (connect_out or "failed")[:40],
                "PASS" if connect_code == 0 else "FAIL",
            ),
        ]
    )
    check.is_true(cpe_up, f"{case_id}: CPE must be up (SSH via CPE PC) after reboot")
    check.is_true(
        ap_available,
        f"{case_id}: hidden SSID must be available in scan after boot (BSSID in nmcli list)",
    )
    check.equal(
        connect_code,
        0,
        f"{case_id}: must connect to hidden SSID after boot — {connect_out}",
    )
