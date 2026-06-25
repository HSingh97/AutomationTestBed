"""Lab preflight: fetch BTS 2.4 GHz credentials for btsconnect POST."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re

import asyncssh
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from utils.cpe_api_24_config import DEFAULT_CPE_SSH_HOST
from utils.parsers import extract_uci_value, ssh_scalar

RADIO_24_IDX = 0
BTS_RADIO_IDX = 1
_CPE_AP_BSSID_RE = re.compile(
    r"(?:HWaddr|hwaddr)\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FetchedWifiCredentials:
    ssid: str
    password: str
    hidden: bool


async def fetch_radio_wifi_credentials(
    ssh: AsyncGenericDriver,
    radio_idx: int = RADIO_24_IDX,
    *,
    label: str,
) -> FetchedWifiCredentials:
    ssid = ssh_scalar((await ssh.send_command(RootCommands.get_ssid(radio_idx))).result)
    password = ssh_scalar(
        (await ssh.send_command(RootCommands.get_encryption_key(radio_idx))).result
    )
    hidden_raw = extract_uci_value(
        (
            await ssh.send_command(
                f"uci get wireless.@wifi-iface[{radio_idx}].hidden"
            )
        ).result
    )
    hidden = hidden_raw.strip() in ("1", "true", "yes")
    if not ssid:
        raise RuntimeError(f"{label}: 2.4 GHz SSID is empty (wireless.@wifi-iface[{radio_idx}])")
    print(
        f"    -> [SSH {label}] radio {radio_idx}: ssid={ssid!r} "
        f"hidden={hidden} key={'set' if password else 'none'}"
    )
    return FetchedWifiCredentials(
        ssid=ssid.strip(),
        password=password.strip() if password else "",
        hidden=hidden,
    )


async def _uci_get(conn: asyncssh.SSHClientConnection, key: str) -> str:
    result = await conn.run(f"uci get {key}", check=False)
    if result.exit_status != 0:
        return ""
    return (result.stdout or "").strip()


async def fetch_bts_credentials_via_asyncssh(
    host: str,
    *,
    username: str,
    password: str,
    label: str,
    radio_idx: int = RADIO_24_IDX,
    connect_timeout: float = 15.0,
) -> FetchedWifiCredentials:
    async with asyncssh.connect(
        host,
        username=username,
        password=password,
        known_hosts=None,
        connect_timeout=connect_timeout,
    ) as conn:
        ssid = await _uci_get(conn, f"wireless.@wifi-iface[{radio_idx}].ssid")
        key = await _uci_get(conn, f"wireless.@wifi-iface[{radio_idx}].key")
        hidden_raw = await _uci_get(conn, f"wireless.@wifi-iface[{radio_idx}].hidden")
        hidden = hidden_raw in ("1", "true", "yes")
        if not ssid:
            raise RuntimeError(
                f"{label}: wireless.@wifi-iface[{radio_idx}].ssid is empty on {host}"
            )
        print(
            f"    -> [SSH {label} @ {host}] radio {radio_idx}: ssid={ssid!r} "
            f"hidden={hidden} key={'set' if key else 'none'}"
        )
        return FetchedWifiCredentials(
            ssid=ssid,
            password=key,
            hidden=hidden,
        )


def _parse_wifi_ifaces_from_uci_show(stdout: str) -> dict[str, dict[str, str]]:
    iface: dict[str, dict[str, str]] = {}
    for line in stdout.splitlines():
        if "@wifi-iface[" not in line or "=" not in line:
            continue
        try:
            idx = line.split("@wifi-iface[", 1)[1].split("]", 1)[0]
        except IndexError:
            continue
        tail = line.split("]", 1)[1]
        if not tail.startswith("."):
            continue
        field, _, raw = tail[1:].partition("=")
        value = raw.strip().strip("'").strip('"')
        iface.setdefault(idx, {})[field] = value
    return iface


async def _ssh_session_device_role(conn: asyncssh.SSHClientConnection) -> str:
    """
    Distinguish CPE vs BTS when both expose 169.254.254.1 on lan24 mgmt AP.
    CPE: wifi-iface[1] is STA (backhaul to BTS). BTS: wifi-iface[1] is AP (btsconnect).
    """
    mode = await _uci_get(conn, "wireless.@wifi-iface[1].mode")
    mode = mode.strip().lower()
    if mode == "sta":
        return "cpe"
    if mode == "ap":
        return "bts"
    return "unknown"


async def _assert_ssh_session_is_cpe(
    conn: asyncssh.SSHClientConnection,
    *,
    context: str,
) -> None:
    role = await _ssh_session_device_role(conn)
    if role == "bts":
        ssid = await _uci_get(conn, "wireless.@wifi-iface[0].ssid")
        raise RuntimeError(
            f"{context}: SSH reached BTS lan24 mgmt (SSID {ssid!r}), not CPE.\n"
            "  PC must join CPE 2.4 GHz hidden mgmt Wi‑Fi (e.g. KWDEJPOQ), not BTS UBRMGMT* SSID."
        )


def _is_bts_like_mgmt_ssid(ssid: str, *, bts_ssid: str | None = None) -> bool:
    """SSID names that belong to BTS 2.4 GHz — PC must never join these for CPE API tests."""
    name = ssid.strip()
    if not name:
        return False
    bts = (bts_ssid or "").strip()
    if bts and name == bts:
        return True
    upper = name.upper()
    if upper.startswith("UBRMGMT"):
        return True
    if upper.startswith("UBR655_R"):
        return True
    return False


def _reject_if_bts_ssid(ssid: str, bts_ssid: str | None, *, context: str) -> None:
    bts = (bts_ssid or "").strip()
    if bts and ssid.strip() == bts:
        raise RuntimeError(
            f"{context}: SSID {ssid!r} is the BTS btsconnect network — "
            "refusing (PC must join CPE mgmt Wi‑Fi only)."
        )
    if _is_bts_like_mgmt_ssid(ssid, bts_ssid=bts_ssid):
        raise RuntimeError(
            f"{context}: SSID {ssid!r} is a BTS 2.4 GHz network — "
            "refusing (PC must join CPE hidden mgmt AP only)."
        )


async def _read_cpe_mgmt_ap_from_conn(
    conn: asyncssh.SSHClientConnection,
    *,
    preferred_iface_idx: int = RADIO_24_IDX,
    bts_ssid: str | None = None,
    known_mgmt_ssid: str | None = None,
    log_prefix: str,
) -> FetchedWifiCredentials:
    """Read CPE lan24 hidden mgmt AP — must be on CPE SSH session, not BTS."""
    await _assert_ssh_session_is_cpe(conn, context=log_prefix)

    result = await conn.run(
        "uci show wireless | grep -E '@wifi-iface\\[[0-9]+\\]\\.(mode|ssid|key|hidden|network)='",
        check=False,
    )
    iface = _parse_wifi_ifaces_from_uci_show(result.stdout or "")
    want = (known_mgmt_ssid or "").strip()

    candidates: list[tuple[int, dict[str, str]]] = []
    for iface_idx, data in sorted(iface.items(), key=lambda item: int(item[0])):
        if data.get("mode") != "ap":
            continue
        ap_ssid = data.get("ssid", "")
        ap_key = data.get("key", "")
        if not ap_ssid or not ap_key:
            continue
        if _is_bts_like_mgmt_ssid(ap_ssid, bts_ssid=bts_ssid):
            continue
        _reject_if_bts_ssid(ap_ssid, bts_ssid, context=f"{log_prefix} iface[{iface_idx}]")
        candidates.append((int(iface_idx), data))

    if want:
        for iface_idx, data in candidates:
            if data.get("ssid", "") == want:
                hidden = data.get("hidden", "1") in ("1", "true", "yes")
                print(
                    f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
                    f"ssid={want!r} hidden={hidden}"
                )
                return FetchedWifiCredentials(
                    ssid=want,
                    password=data.get("key", ""),
                    hidden=hidden,
                )

    pref = str(preferred_iface_idx)
    for iface_idx, data in candidates:
        if str(iface_idx) == pref and data.get("network", "lan24") in ("lan24", ""):
            hidden = data.get("hidden", "1") in ("1", "true", "yes")
            ssid = data.get("ssid", "")
            print(
                f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
                f"ssid={ssid!r} hidden={hidden}"
            )
            return FetchedWifiCredentials(ssid=ssid, password=data.get("key", ""), hidden=hidden)

    for iface_idx, data in candidates:
        if data.get("network", "lan24") == "lan24":
            hidden = data.get("hidden", "1") in ("1", "true", "yes")
            ssid = data.get("ssid", "")
            print(
                f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
                f"ssid={ssid!r} hidden={hidden}"
            )
            return FetchedWifiCredentials(ssid=ssid, password=data.get("key", ""), hidden=hidden)

    for iface_idx, data in candidates:
        hidden = data.get("hidden", "1") in ("1", "true", "yes")
        ssid = data.get("ssid", "")
        print(
            f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
            f"ssid={ssid!r} hidden={hidden}"
        )
        return FetchedWifiCredentials(ssid=ssid, password=data.get("key", ""), hidden=hidden)

    raise RuntimeError(f"{log_prefix}: no CPE lan24 mgmt AP (mode=ap) found on CPE device.")


def _remote_exec_bts_command(su_index: int, inner_command: str) -> str:
    escaped = inner_command.replace("'", "'\"'\"'")
    return f"/usr/sbin/remote_exec.sh {su_index} '{escaped}'"


async def _ssh_run(conn: asyncssh.SSHClientConnection, command: str, *, timeout_s: float = 30.0) -> tuple[int, str]:
    result = await conn.run(command, check=False, timeout=timeout_s)
    out = (result.stdout or "") + (result.stderr or "")
    return result.exit_status or 0, out


async def _resolve_cpe_remote_exec_index(
    bts_conn: asyncssh.SSHClientConnection,
    *,
    mgmt_ssid: str | None = None,
) -> int | None:
    want_ssid = (mgmt_ssid or "").strip()
    _, bts_mac_out = await _ssh_run(
        bts_conn, "cat /sys/class/net/br-lan/address 2>/dev/null", timeout_s=15.0
    )
    bts_mac = (bts_mac_out or "").strip().lower().replace("-", ":")

    for idx in range(1, 33):
        code, out = await _ssh_run(
            bts_conn,
            _remote_exec_bts_command(idx, "echo CPE_API24_OK"),
            timeout_s=25.0,
        )
        if code != 0 or "CPE_API24_OK" not in out:
            continue
        _, peer_mac_out = await _ssh_run(
            bts_conn,
            _remote_exec_bts_command(idx, "cat /sys/class/net/br-lan/address 2>/dev/null"),
            timeout_s=20.0,
        )
        peer_mac = (peer_mac_out or "").strip().lower().replace("-", ":")
        if bts_mac and peer_mac == bts_mac:
            continue
        _, mode_out = await _ssh_run(
            bts_conn,
            _remote_exec_bts_command(idx, "uci -q get wireless.@wifi-iface[1].mode"),
            timeout_s=20.0,
        )
        if (mode_out or "").strip().lower() != "sta":
            continue
        if want_ssid:
            _, ssid_out = await _ssh_run(
                bts_conn,
                _remote_exec_bts_command(idx, "uci -q get wireless.@wifi-iface[0].ssid"),
                timeout_s=20.0,
            )
            if want_ssid not in (ssid_out or ""):
                continue
        return idx
    return None


async def _read_cpe_mgmt_ap_via_remote_exec(
    bts_conn: asyncssh.SSHClientConnection,
    *,
    su_index: int,
    bts_ssid: str | None,
    known_mgmt_ssid: str | None,
    log_prefix: str,
) -> FetchedWifiCredentials:
    code, out = await _ssh_run(
        bts_conn,
        _remote_exec_bts_command(
            su_index,
            "uci show wireless | grep -E '@wifi-iface\\[[0-9]+\\]\\.(mode|ssid|key|hidden|network)='",
        ),
        timeout_s=30.0,
    )
    if code != 0:
        raise RuntimeError(f"{log_prefix}: remote_exec uci failed ({code}): {out[-300:]}")
    iface = _parse_wifi_ifaces_from_uci_show(out)
    want = (known_mgmt_ssid or "").strip()
    candidates: list[tuple[int, dict[str, str]]] = []
    for iface_idx, data in sorted(iface.items(), key=lambda item: int(item[0])):
        if data.get("mode") != "ap":
            continue
        ap_ssid = data.get("ssid", "")
        ap_key = data.get("key", "")
        if not ap_ssid or not ap_key:
            continue
        if _is_bts_like_mgmt_ssid(ap_ssid, bts_ssid=bts_ssid):
            continue
        candidates.append((int(iface_idx), data))

    if want:
        for iface_idx, data in candidates:
            if data.get("ssid") == want:
                hidden = data.get("hidden", "1") in ("1", "true", "yes")
                print(
                    f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
                    f"ssid={want!r} hidden={hidden}"
                )
                return FetchedWifiCredentials(ssid=want, password=data.get("key", ""), hidden=hidden)

    for iface_idx, data in candidates:
        if data.get("network", "lan24") == "lan24":
            hidden = data.get("hidden", "1") in ("1", "true", "yes")
            ssid = data.get("ssid", "")
            print(
                f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
                f"ssid={ssid!r} hidden={hidden}"
            )
            return FetchedWifiCredentials(ssid=ssid, password=data.get("key", ""), hidden=hidden)

    raise RuntimeError(f"{log_prefix}: no CPE lan24 mgmt AP found via remote_exec.")


async def fetch_cpe_mgmt_ap_credentials(
    *,
    username: str,
    password: str,
    cpe_host: str = DEFAULT_CPE_SSH_HOST,
    bts_host: str | None = None,
    bts_ssid: str | None = None,
    cpe_ipv6: str | None = None,
    known_mgmt_ssid: str | None = None,
    connect_timeout: float = 15.0,
) -> FetchedWifiCredentials:
    """
    CPE 2.4 GHz hidden mgmt AP only (never BTS UBRMGMT* / btsconnect SSID).

    Both BTS and CPE use 169.254.254.1 on lan24 — verify SSH session is CPE (iface[1]=sta)
    or read from CPE IPv6 / BTS remote_exec instead of BTS tunnel to 169.254.254.1.
    """
    errors: list[str] = []

    async def _try_direct(host: str, label: str) -> FetchedWifiCredentials | None:
        try:
            async with asyncssh.connect(
                host,
                username=username,
                password=password,
                known_hosts=None,
                connect_timeout=connect_timeout,
            ) as conn:
                return await _read_cpe_mgmt_ap_from_conn(
                    conn,
                    bts_ssid=bts_ssid,
                    known_mgmt_ssid=known_mgmt_ssid,
                    log_prefix=f"SSH CPE @ {label}",
                )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            return None

    creds = await _try_direct(cpe_host, cpe_host)
    if creds is not None:
        return creds

    cpe_v6 = (cpe_ipv6 or "").strip()
    if cpe_v6:
        creds = await _try_direct(cpe_v6, cpe_v6)
        if creds is not None:
            return creds

    if bts_host:
        bts_conn: asyncssh.SSHClientConnection | None = None
        try:
            bts_conn = await asyncssh.connect(
                bts_host,
                username=username,
                password=password,
                known_hosts=None,
                connect_timeout=connect_timeout,
            )
            su_idx = await _resolve_cpe_remote_exec_index(
                bts_conn, mgmt_ssid=known_mgmt_ssid
            )
            if su_idx is not None:
                return await _read_cpe_mgmt_ap_via_remote_exec(
                    bts_conn,
                    su_index=su_idx,
                    bts_ssid=bts_ssid,
                    known_mgmt_ssid=known_mgmt_ssid,
                    log_prefix=f"SSH CPE via BTS remote_exec SU{su_idx}",
                )
            errors.append(f"BTS {bts_host}: no remote_exec SU index for CPE")
        except Exception as exc:
            errors.append(f"BTS remote_exec @ {bts_host}: {exc}")
        finally:
            if bts_conn is not None:
                bts_conn.close()

    detail = "\n  ".join(errors) if errors else "(no paths attempted)"
    raise RuntimeError(
        f"Cannot read CPE mgmt AP credentials.\n  {detail}\n"
        "  Join CPE hidden mgmt Wi‑Fi manually (not BTS UBRMGMT*), or pass --local-ipv6."
    )


async def fetch_cpe_mgmt_ap_bssid(
    *,
    username: str,
    password: str,
    cpe_ipv6: str | None = None,
    bts_host: str | None = None,
    known_mgmt_ssid: str | None = None,
    iface: str = "ath0",
    connect_timeout: float = 12.0,
) -> str | None:
    """Read CPE 2.4 GHz mgmt AP BSSID (ath0) — needed for hidden nmcli join on the PC."""
    cmd = f"ifconfig {iface} 2>/dev/null | awk '/HWaddr/{{print $5; exit}}'"

    def _parse_bssid(stdout: str) -> str | None:
        text = (stdout or "").strip()
        if not text:
            return None
        match = _CPE_AP_BSSID_RE.search(text)
        return match.group(1).upper() if match else text.split()[0].upper()

    cpe_v6 = (cpe_ipv6 or "").strip()
    if cpe_v6:
        try:
            async with asyncssh.connect(
                cpe_v6,
                username=username,
                password=password,
                known_hosts=None,
                connect_timeout=connect_timeout,
            ) as conn:
                await _assert_ssh_session_is_cpe(conn, context=f"BSSID @ {cpe_v6}")
                result = await conn.run(cmd, check=False, timeout=15.0)
                bssid = _parse_bssid(result.stdout or "")
                if bssid:
                    print(f"    -> [CPE BSSID] {bssid} from {iface} @ {cpe_v6}")
                    return bssid
        except Exception as exc:
            print(f"    -> [CPE BSSID] skip {cpe_v6}: {exc}")

    if bts_host:
        bts_conn: asyncssh.SSHClientConnection | None = None
        try:
            bts_conn = await asyncssh.connect(
                bts_host,
                username=username,
                password=password,
                known_hosts=None,
                connect_timeout=connect_timeout,
            )
            su_idx = await _resolve_cpe_remote_exec_index(
                bts_conn, mgmt_ssid=known_mgmt_ssid
            )
            if su_idx is not None:
                code, out = await _ssh_run(
                    bts_conn,
                    _remote_exec_bts_command(su_idx, cmd),
                    timeout_s=25.0,
                )
                if code == 0:
                    bssid = _parse_bssid(out)
                    if bssid:
                        print(f"    -> [CPE BSSID] {bssid} from remote_exec SU{su_idx}")
                        return bssid
        except Exception as exc:
            print(f"    -> [CPE BSSID] skip BTS remote_exec: {exc}")
        finally:
            if bts_conn is not None:
                bts_conn.close()
    return None


async def fetch_cpe_mgmt_ap_credentials_via_bts(
    *,
    bts_host: str,
    cpe_host: str = DEFAULT_CPE_SSH_HOST,
    username: str,
    password: str,
    connect_timeout: float = 15.0,
    bts_ssid: str | None = None,
) -> FetchedWifiCredentials:
    """Backward-compatible alias — prefer fetch_cpe_mgmt_ap_credentials."""
    return await fetch_cpe_mgmt_ap_credentials(
        username=username,
        password=password,
        cpe_host=cpe_host,
        bts_host=bts_host,
        bts_ssid=bts_ssid,
        connect_timeout=connect_timeout,
    )


async def fetch_bts_sta_credentials_from_cpe_mgmt(
    *,
    username: str,
    password: str,
    cpe_host: str = DEFAULT_CPE_SSH_HOST,
) -> FetchedWifiCredentials:
    """Read BTS STA ssid/key from CPE when BTS management IP is unreachable."""
    async with asyncssh.connect(
        cpe_host,
        username=username,
        password=password,
        known_hosts=None,
        connect_timeout=15.0,
    ) as conn:
        result = await conn.run(
            "uci show wireless | grep -E '@wifi-iface\\[[0-9]+\\]\\.(mode|ssid|key)='",
            check=False,
        )
        iface = _parse_wifi_ifaces_from_uci_show(result.stdout or "")

        for idx, data in sorted(iface.items(), key=lambda item: int(item[0])):
            if data.get("mode") != "sta":
                continue
            ssid = data.get("ssid", "")
            key = data.get("key", "")
            if ssid and key:
                print(
                    f"    -> [SSH CPE @ {cpe_host}] STA iface[{idx}]: "
                    f"ssid={ssid!r} key=set"
                )
                return FetchedWifiCredentials(ssid=ssid, password=key, hidden=True)

        raise RuntimeError(
            f"No BTS STA ssid/key found on CPE {cpe_host}. "
            "Pass --cpe-24-bts-ssid/password or reach BTS via --local-ipv6."
        )


async def fetch_bts_credentials_for_api(
    *,
    bts_host: str,
    cpe_mgmt_host: str,
    username: str,
    password: str,
    radio_idx: int = BTS_RADIO_IDX,
) -> FetchedWifiCredentials:
    """Prefer BTS SSH; fall back to CPE mgmt SSH when PC is on CPE Wi‑Fi only."""
    try:
        return await fetch_bts_credentials_via_asyncssh(
            bts_host,
            username=username,
            password=password,
            label="BTS",
            radio_idx=radio_idx,
            connect_timeout=10.0,
        )
    except Exception as exc:
        print(
            f"    -> [Preflight] BTS SSH to {bts_host} unavailable ({exc}). "
            f"Trying CPE mgmt SSH @ {cpe_mgmt_host}..."
        )
        return await fetch_bts_sta_credentials_from_cpe_mgmt(
            username=username,
            password=password,
            cpe_host=cpe_mgmt_host,
        )


async def is_bts_link_broken(
    *,
    bts_host: str,
    username: str,
    password: str,
    radio_idx: int = BTS_RADIO_IDX,
    log: bool = True,
) -> bool:
    """
    Return True when BTS reports zero remote partners on the radio.
    """
    async with asyncssh.connect(
        bts_host,
        username=username,
        password=password,
        known_hosts=None,
        connect_timeout=10.0,
    ) as conn:
        result = await conn.run(
            f"cat /sys/class/kwn/wifi{radio_idx}/statistics/links 2>/dev/null || echo -1",
            check=False,
            timeout=8,
        )
        raw = (result.stdout or "").strip()
        try:
            partners = int(raw.splitlines()[-1]) if raw else -1
        except Exception:
            partners = -1
        if log:
            print(
                f"    -> [SSH BTS @ {bts_host}] radio {radio_idx} remote partners={partners}"
            )
        return partners == 0


async def wait_bts_link_up(
    *,
    bts_host: str,
    username: str,
    password: str,
    radio_idx: int = BTS_RADIO_IDX,
    max_wait_s: float = 300.0,
    poll_s: float = 5.0,
    label: str = "API_17",
) -> bool:
    """Poll BTS until radio has at least one remote partner (link restored)."""
    import asyncio
    import time

    start = time.monotonic()
    deadline = start + max_wait_s
    last_progress = 0.0

    print(
        f"    -> [{label}] Waiting for BTS↔CPE link to return "
        f"(max {max_wait_s / 60:.0f} min)..."
    )
    while time.monotonic() < deadline:
        if not await is_bts_link_broken(
            bts_host=bts_host,
            username=username,
            password=password,
            radio_idx=radio_idx,
            log=False,
        ):
            elapsed = time.monotonic() - start
            print(
                f"    -> [{label}] BTS link up ({elapsed:.0f}s / {elapsed / 60:.1f} min)."
            )
            return True

        elapsed = time.monotonic() - start
        if elapsed - last_progress >= 30.0:
            print(f"    -> [{label}] still waiting for BTS link ({elapsed / 60:.0f} min)...")
            last_progress = elapsed
        await asyncio.sleep(poll_s)

    print(
        f"    -> [{label}] BTS link not seen within {max_wait_s / 60:.0f} min "
        "(later cases may need btsconnect)."
    )
    return False


async def fetch_attached_cpe_ipv6_candidates_from_bts(
    *,
    bts_host: str,
    username: str,
    password: str,
) -> list[str]:
    """
    Read attached CPE IPv6 candidates from BTS neighbor table.
    """
    async with asyncssh.connect(
        bts_host,
        username=username,
        password=password,
        known_hosts=None,
        connect_timeout=10.0,
    ) as conn:
        result = await conn.run("ip -6 neigh show", check=False, timeout=12)
        lines = (result.stdout or "").splitlines()
        candidates: list[str] = []
        for line in lines:
            line = line.strip()
            if "FAILED" in line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                ip = str(ipaddress.IPv6Address(parts[0]))
            except Exception:
                continue
            if ip.startswith("fe80:"):
                continue
            candidates.append(ip)

        if not candidates:
            raise RuntimeError(
                f"No global IPv6 neighbors found on BTS {bts_host}; cannot derive attached CPE IPv6."
            )
        uniq: list[str] = []
        for ip in candidates:
            if ip not in uniq:
                uniq.append(ip)
        print(f"    -> [SSH BTS @ {bts_host}] attached IPv6 candidates: {', '.join(uniq)}")
        return uniq
