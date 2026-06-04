"""P2MP link credentials: AIRTEL_SSID_GEN + SSH apply on BTS and CPE."""

from __future__ import annotations

import shlex
from typing import Any

from pages.commands import RootCommands
from utils.airtel_ssid_gen import LinkCredentials, generate_link_credentials
from utils.parsers import extract_uci_value


def link_config(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("link", {}) or {}


def link_auto_enabled(profile: dict[str, Any]) -> bool:
    link = link_config(profile)
    if "auto_credentials" in link:
        return bool(link["auto_credentials"])
    # Default: use generator when no static ssid override
    return not bool(str(link.get("ssid", "")).strip())


async def read_device_serial_ssh(ssh) -> str:
    raw = await ssh.send_command(RootCommands.GET_SERIAL_NO, timeout_ops=20)
    serial = extract_uci_value(str(raw.result or "").strip())
    return str(serial or "").strip().upper()


async def link_health_bts(ssh, profile: dict[str, Any]) -> tuple[bool, int]:
    """Return (link_up, connected_station_count) from BTS ath interface."""
    link = link_config(profile)
    min_clients = int(link.get("min_connected_clients", 1))
    radio_idx = int(link.get("radio_idx", 1))
    ath = f"ath{radio_idx}"
    out = await ssh.send_command(
        f"wlanconfig {ath} list 2>/dev/null | grep -cE '^[0-9a-f][0-9a-f]:' || echo 0",
        timeout_ops=20,
    )
    try:
        count = int(str(out.result or "0").strip().split()[0])
    except (ValueError, IndexError):
        count = 0
    if count < min_clients:
        out2 = await ssh.send_command(
            f"iw dev {ath} station dump 2>/dev/null | grep -c '^Station' || echo 0",
            timeout_ops=20,
        )
        try:
            count = int(str(out2.result or "0").strip().split()[0])
        except (ValueError, IndexError):
            count = 0
    return count >= min_clients, count


async def read_bts_wireless_diag(ssh, profile: dict[str, Any]) -> dict[str, str]:
    """Snapshot SSID/key/txpower on BTS for recovery logs."""
    link = link_config(profile)
    radio_idx = int(link.get("radio_idx", 1))
    ssid = await _read_radio_ssid(ssh, radio_idx)
    key = await _read_radio_key(ssh, radio_idx)
    raw_pwr = await ssh.send_command(RootCommands.get_tx_power(radio_idx), timeout_ops=15)
    tx_power = extract_uci_value(str(raw_pwr.result or "").strip())
    ok, clients = await link_health_bts(ssh, profile)
    return {
        "ssid": ssid,
        "key_len": str(len(key or "")),
        "tx_power": tx_power or "",
        "link_up": str(ok),
        "clients": str(clients),
    }


async def apply_bts_tx_power(ssh, profile: dict[str, Any], *, radio_idx: int | None = None) -> str:
    link = link_config(profile)
    idx = int(radio_idx if radio_idx is not None else link.get("radio_idx", 1))
    tx_power = _tx_power_default(profile)
    await ssh.send_command(f"ucidyn set txparam.ath{idx}.atpcpower {tx_power}", timeout_ops=30)
    await ssh.send_command("ucidyn apply", timeout_ops=60)
    return tx_power

def resolve_link_credentials_from_profile(
    profile: dict[str, Any],
    *,
    serial: str,
) -> LinkCredentials:
    link = link_config(profile)
    return generate_link_credentials(
        serial,
        radio_type=str(link.get("radio_type", "5G")),
        auth_mode=str(link.get("auth_mode", "32")),
        encryption_mode=str(link.get("encryption_mode", "8")),
        profile_link=link,
    )


async def resolve_link_credentials_ssh(
    bts_ssh,
    profile: dict[str, Any],
) -> LinkCredentials:
    link = link_config(profile)
    serial = str(link.get("bts_serial", "")).strip().upper()
    if not serial:
        serial = await read_device_serial_ssh(bts_ssh)
    if not serial:
        raise RuntimeError("BTS serial empty (fw_printenv -n dsn)")
    return resolve_link_credentials_from_profile(profile, serial=serial)


async def _read_radio_ssid(ssh, radio_idx: int) -> str:
    raw = await ssh.send_command(RootCommands.get_ssid(radio_idx), timeout_ops=20)
    return extract_uci_value(str(raw.result or "").strip())


async def _read_radio_key(ssh, radio_idx: int) -> str:
    raw = await ssh.send_command(RootCommands.get_encryption_key(radio_idx), timeout_ops=20)
    return extract_uci_value(str(raw.result or "").strip())


def _uci_set_commands(creds: LinkCredentials, radio_idx: int) -> list[str]:
    ssid_q = shlex.quote(creds.ssid)
    pass_q = shlex.quote(creds.password)
    idx = radio_idx
    return [
        f"uci set wireless.@wifi-iface[{idx}].ssid={ssid_q}",
        f"uci set wireless.@wifi-iface[{idx}].key={pass_q}",
        f"uci set wireless.wifi{idx}.nwksecret={pass_q}",
        "uci commit wireless",
        "wifi reload 2>/dev/null || /etc/init.d/network reload",
    ]


def _tx_power_default(profile: dict[str, Any], *, fallback: str = "1") -> str:
    link = link_config(profile)
    return str(link.get("tx_power_default", fallback)).strip() or fallback


async def apply_link_credentials_ssh(
    ssh,
    creds: LinkCredentials,
    *,
    radio_idx: int = 1,
    label: str = "device",
    force: bool = False,
) -> bool:
    """Apply generated SSID + password/key on BTS or CPE."""
    current_ssid = await _read_radio_ssid(ssh, radio_idx)
    current_key = await _read_radio_key(ssh, radio_idx)
    if not force and current_ssid == creds.ssid and current_key == creds.password:
        print(f"[link] {label} credentials already match (SSID={creds.ssid})")
        return False

    print(
        f"[link] {label} applying AIRTEL credentials "
        f"SSID {current_ssid!r}->{creds.ssid!r} (radio {radio_idx})"
        + (" (forced)" if force else "")
    )
    for cmd in _uci_set_commands(creds, radio_idx):
        await ssh.send_command(cmd, timeout_ops=60)
    restored_ssid = await _read_radio_ssid(ssh, radio_idx)
    restored_key = await _read_radio_key(ssh, radio_idx)
    ok = restored_ssid == creds.ssid and restored_key == creds.password
    if not ok:
        print(
            f"[link] WARN {label}: after apply SSID={restored_ssid!r} key_len={len(restored_key)}"
        )
    return ok


async def apply_link_credentials_via_secondary_pc(
    sec_cfg: dict[str, Any],
    creds: LinkCredentials,
    *,
    password: str,
    radio_idx: int = 1,
) -> bool:
    """Apply wireless UCI on CPE via CPE-side lab PC → factory IPv4 (recovery path)."""
    inner = " && ".join(shlex.quote(c) for c in _uci_set_commands(creds, radio_idx))
    return await _run_on_cpe_via_secondary_ssh(
        sec_cfg,
        {"dut": {"password": password}},
        inner,
        password=password,
        cpe_host=str(sec_cfg.get("cpe_factory_ipv4", "192.168.2.1")).strip(),
        timeout_ops=90,
        log_ok="CPE credentials",
    )


def _cpe_ssh_credential_attempts(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """(user, password) pairs for nested SSH from the secondary lab PC to the CPE."""
    dut = profile.get("dut", {}) or {}
    fl = profile.get("factory_login", {}) or {}
    attempts: list[tuple[str, str]] = [
        ("root", str(dut.get("password", ""))),
        ("root", str(fl.get("password", ""))),
        (str(fl.get("username", "installer")), str(fl.get("password", ""))),
    ]
    return [(u, p) for u, p in attempts if p]


async def _run_on_cpe_via_secondary_ssh(
    sec_cfg: dict[str, Any],
    profile: dict[str, Any],
    inner: str,
    *,
    password: str = "",
    cpe_host: str | None = None,
    timeout_ops: int = 120,
    log_ok: str,
) -> bool:
    """Run a shell command on the CPE via secondary PC, using sshpass when needed."""
    from scrapli.driver.generic import AsyncGenericDriver
    from utils.lab_pc_net import _parse_ssh_target

    ssh_target = str(sec_cfg.get("ssh", "")).strip()
    if not ssh_target:
        return False

    cpe_target = cpe_host or str(sec_cfg.get("cpe_factory_ipv4", "")).strip()
    if not cpe_target:
        cpe_target = cpe_ssh_access(profile)["host"]
    pc_host, pc_user = _parse_ssh_target(ssh_target)
    pc_pass = str(sec_cfg.get("password") or password or profile.get("dut", {}).get("password", ""))

    pc = AsyncGenericDriver(
        host=pc_host,
        auth_username=pc_user,
        auth_password=pc_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await pc.open()
    try:
        for cpe_user, cpe_pass in _cpe_ssh_credential_attempts(profile):
            remote = (
                f"sshpass -p {shlex.quote(cpe_pass)} ssh -o StrictHostKeyChecking=no "
                f"-o ConnectTimeout=20 {shlex.quote(cpe_user)}@{cpe_target} {inner}"
            )
            try:
                result = await pc.send_command(remote, timeout_ops=timeout_ops)
            except Exception as exc:
                print(f"[link] {log_ok} {cpe_user}@{cpe_target}: {exc}")
                continue
            out = str(result.result or "")
            if "error" in out.lower() and "entry not found" not in out.lower():
                print(f"[link] {log_ok} ({cpe_user}): {out[:200]}")
                continue
            print(f"[link] {log_ok} via {pc_user}@{pc_host} → {cpe_user}@{cpe_target}")
            return True
        return False
    finally:
        await pc.close()


def cpe_ssh_access(profile: dict[str, Any]) -> dict[str, str]:
    """Direct CPE SSH (factory / fallback on 10.0.0.x bench)."""
    link = link_config(profile)
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}
    cpe_acc = tb.get("cpe_access", {}) or {}
    dut = profile.get("dut", {}) or {}
    host = str(
        link.get("cpe_fallback_ipv4")
        or cpe_acc.get("fallback_ipv4")
        or sec.get("cpe_gui_ipv4")
        or sec.get("cpe_ssh_ipv4")
        or "10.0.0.1"
    ).strip()
    return {
        "host": host,
        "user": str(link.get("cpe_ssh_user", cpe_acc.get("username", "root"))),
        "password": str(
            link.get("cpe_ssh_password")
            or cpe_acc.get("password")
            or dut.get("password", "")
        ),
    }


async def ensure_cpe_link_credentials_always(
    profile: dict[str, Any],
    creds: LinkCredentials,
    *,
    force: bool | None = None,
    gui_page=None,
    headless: bool = True,
) -> bool:
    """
    Always push AIRTEL SSID/key to the real CPE.

    SSH-only path through the secondary CPE lab PC.
    """
    del force, gui_page, headless
    tb = profile.get("testbed", {}) or {}
    sec = tb.get("secondary_pc", {}) or {}

    if not str(sec.get("ssh", "")).strip():
        print("[link] CPE SSID/key skipped: set testbed.secondary_pc.ssh")
        return False
    return await _apply_cpe_credentials_via_secondary_ssh(profile, creds, sec_cfg=sec)


async def _apply_cpe_credentials_via_secondary_ssh(
    profile: dict[str, Any],
    creds: LinkCredentials,
    *,
    sec_cfg: dict[str, Any] | None = None,
) -> bool:
    """Apply CPE SSID/key (+ tx power default) via secondary PC SSH hop."""
    tb = profile.get("testbed", {}) or {}
    sec = sec_cfg if sec_cfg is not None else (tb.get("secondary_pc", {}) or {})
    link = link_config(profile)
    radio_idx = int(link.get("cpe_radio_idx", link.get("radio_idx", 1)))
    tx_power = _tx_power_default(profile)
    dyn_cmds = [
        f"ucidyn set wireless.@wifi-iface[{radio_idx}].ssid {shlex.quote(creds.ssid)}",
        f"ucidyn set wireless.@wifi-iface[{radio_idx}].key {shlex.quote(creds.password)}",
        f"ucidyn set wireless.wifi{radio_idx}.nwksecret {shlex.quote(creds.password)}",
        f"ucidyn set txparam.ath{radio_idx}.atpcpower {shlex.quote(tx_power)}",
        "ucidyn apply",
    ]
    inner = " && ".join(shlex.quote(c) for c in dyn_cmds)
    return await _run_on_cpe_via_secondary_ssh(
        sec,
        profile,
        inner,
        cpe_host=cpe_ssh_access(profile)["host"],
        timeout_ops=180,
        log_ok="CPE SSID/key",
    )


async def ensure_p2mp_link_credentials(
    *,
    bts_ssh,
    profile: dict[str, Any],
    bts_radio_idx: int | None = None,
) -> LinkCredentials:
    """
    Generate from BTS serial (AIRTEL_SSID_GEN) and apply on BTS only.

    CPE cannot be reached on mgmt IPv6 before RF link — use
    `apply_cpe_pre_link_via_secondary_pc()` from the CPE-side lab PC.
    """
    link = link_config(profile)
    bts_idx = int(bts_radio_idx if bts_radio_idx is not None else link.get("radio_idx", 1))

    creds = await resolve_link_credentials_ssh(bts_ssh, profile)
    print(
        f"[link] AIRTEL serial={creds.serial} {creds.radio_type} "
        f"→ SSID={creds.ssid} (auth={creds.auth_mode} enc={creds.encryption_mode})"
    )

    await apply_link_credentials_ssh(bts_ssh, creds, radio_idx=bts_idx, label="BTS")
    tx_power = _tx_power_default(profile)
    # Indoor setup: keep a low fixed tx power by default.
    await bts_ssh.send_command(f"ucidyn set txparam.ath{bts_idx}.atpcpower {tx_power}", timeout_ops=30)
    await bts_ssh.send_command("ucidyn apply", timeout_ops=60)
    return creds


async def apply_cpe_pre_link_via_secondary_pc(
    sec_cfg: dict[str, Any],
    creds: LinkCredentials,
    profile: dict[str, Any],
    *,
    profile_tb: dict[str, Any] | None = None,
    password: str,
    cpe_mode: str = "transparent",
) -> bool:
    """
    Configure CPE before RF link exists: CPE lab PC (internet) → CPE factory IPv4.

    Applies transparent VLAN + matching AIRTEL SSID/password in one hop.
    """
    from utils.vlan_uci import build_cpe_mgmtvlan_only_commands, build_cpe_untagged_commands

    if not str(sec_cfg.get("ssh", "")).strip():
        return False

    tb = profile_tb if profile_tb is not None else (profile.get("testbed", {}) or {})
    link = link_config(profile)
    radio_idx = int(link.get("cpe_radio_idx", link.get("radio_idx", 1)))

    uci_cmds: list[str] = []
    if cpe_mode in ("mgmtvlan_only", "mgmtvlan"):
        uci_cmds.extend(build_cpe_mgmtvlan_only_commands(tb))
    elif cpe_mode == "transparent":
        uci_cmds.extend(build_cpe_untagged_commands(tb))
    uci_cmds.extend(_uci_set_commands(creds, radio_idx))
    inner = " && ".join(shlex.quote(c) for c in uci_cmds)
    cpe_factory = str(sec_cfg.get("cpe_factory_ipv4", "192.168.2.1")).strip()
    ok = await _run_on_cpe_via_secondary_ssh(
        sec_cfg,
        profile,
        inner,
        password=password,
        cpe_host=cpe_factory,
        timeout_ops=120,
        log_ok=f"CPE pre-link ({cpe_mode} + SSID={creds.ssid})",
    )
    return ok
