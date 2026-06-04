"""Lab preflight: fetch BTS 2.4 GHz credentials for btsconnect POST."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress

import asyncssh
from scrapli.driver.generic import AsyncGenericDriver

from pages.commands import RootCommands
from utils.cpe_api_24_config import DEFAULT_CPE_SSH_HOST
from utils.parsers import extract_uci_value, ssh_scalar

RADIO_24_IDX = 0
BTS_RADIO_IDX = 1


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
    current_idx: str | None = None
    for line in stdout.splitlines():
        if ".mode=" in line:
            current_idx = line.split("@wifi-iface[", 1)[1].split("]", 1)[0]
            iface.setdefault(current_idx, {})["mode"] = line.split("=", 1)[1].strip().strip("'")
        elif ".ssid=" in line:
            idx = line.split("@wifi-iface[", 1)[1].split("]", 1)[0]
            iface.setdefault(idx, {})["ssid"] = line.split("=", 1)[1].strip().strip("'")
        elif ".key=" in line:
            idx = line.split("@wifi-iface[", 1)[1].split("]", 1)[0]
            iface.setdefault(idx, {})["key"] = line.split("=", 1)[1].strip().strip("'")
        elif ".hidden=" in line:
            idx = line.split("@wifi-iface[", 1)[1].split("]", 1)[0]
            iface.setdefault(idx, {})["hidden"] = line.split("=", 1)[1].strip().strip("'")
    return iface


def _reject_if_bts_ssid(ssid: str, bts_ssid: str | None, *, context: str) -> None:
    bts = (bts_ssid or "").strip()
    if bts and ssid.strip() == bts:
        raise RuntimeError(
            f"{context}: SSID {ssid!r} is the BTS network — refusing (PC must join CPE mgmt Wi‑Fi only)."
        )


async def _read_cpe_mgmt_ap_from_conn(
    conn: asyncssh.SSHClientConnection,
    *,
    preferred_iface_idx: int = RADIO_24_IDX,
    bts_ssid: str | None = None,
    log_prefix: str,
) -> FetchedWifiCredentials:
    """Read CPE management AP (mode=ap on 2.4 GHz iface), not BTS STA."""
    idx = str(preferred_iface_idx)
    mode = await _uci_get(conn, f"wireless.@wifi-iface[{idx}].mode")
    ssid = await _uci_get(conn, f"wireless.@wifi-iface[{idx}].ssid")
    key = await _uci_get(conn, f"wireless.@wifi-iface[{idx}].key")
    hidden_raw = await _uci_get(conn, f"wireless.@wifi-iface[{idx}].hidden")
    if mode == "ap" and ssid and key:
        _reject_if_bts_ssid(ssid, bts_ssid, context=f"{log_prefix} iface[{idx}]")
        hidden = hidden_raw in ("1", "true", "yes")
        print(
            f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{idx}]: "
            f"ssid={ssid!r} hidden={hidden}"
        )
        return FetchedWifiCredentials(ssid=ssid, password=key, hidden=hidden)

    result = await conn.run(
        "uci show wireless | grep -E '@wifi-iface\\[[0-9]+\\]\\.(mode|ssid|key|hidden)='",
        check=False,
    )
    iface = _parse_wifi_ifaces_from_uci_show(result.stdout or "")
    for iface_idx, data in sorted(iface.items(), key=lambda item: int(item[0])):
        if data.get("mode") != "ap":
            continue
        ap_ssid = data.get("ssid", "")
        ap_key = data.get("key", "")
        if not ap_ssid or not ap_key:
            continue
        _reject_if_bts_ssid(ap_ssid, bts_ssid, context=f"{log_prefix} iface[{iface_idx}]")
        hidden = data.get("hidden", "1") in ("1", "true", "yes")
        print(
            f"    -> [{log_prefix}] CPE mgmt AP wifi-iface[{iface_idx}]: "
            f"ssid={ap_ssid!r} hidden={hidden}"
        )
        return FetchedWifiCredentials(ssid=ap_ssid, password=ap_key, hidden=hidden)

    raise RuntimeError(f"{log_prefix}: no CPE mgmt AP (mode=ap) found on device.")


async def fetch_cpe_mgmt_ap_credentials(
    *,
    username: str,
    password: str,
    cpe_host: str = DEFAULT_CPE_SSH_HOST,
    bts_host: str | None = None,
    bts_ssid: str | None = None,
    connect_timeout: float = 15.0,
) -> FetchedWifiCredentials:
    """CPE 2.4 GHz management AP credentials only (never BTS STA / btsconnect SSID)."""
    last_error: Exception | None = None

    try:
        async with asyncssh.connect(
            cpe_host,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=connect_timeout,
        ) as conn:
            return await _read_cpe_mgmt_ap_from_conn(
                conn,
                bts_ssid=bts_ssid,
                log_prefix=f"SSH CPE @ {cpe_host}",
            )
    except Exception as exc:
        last_error = exc

    if not bts_host:
        raise RuntimeError(
            f"Cannot read CPE mgmt AP from {cpe_host}: {last_error}"
        ) from last_error

    bts_conn: asyncssh.SSHClientConnection | None = None
    cpe_conn: asyncssh.SSHClientConnection | None = None
    try:
        bts_conn = await asyncssh.connect(
            bts_host,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=connect_timeout,
        )
        cpe_conn = await asyncssh.connect(
            cpe_host,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=connect_timeout,
            tunnel=bts_conn,
        )
        return await _read_cpe_mgmt_ap_from_conn(
            cpe_conn,
            bts_ssid=bts_ssid,
            log_prefix=f"SSH CPE @ {cpe_host} (via BTS tunnel — still CPE mgmt AP uci)",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Cannot read CPE mgmt AP from {cpe_host} (direct or via BTS {bts_host}): {exc}"
        ) from exc
    finally:
        if cpe_conn is not None:
            cpe_conn.close()
        if bts_conn is not None:
            bts_conn.close()


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
