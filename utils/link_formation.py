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
    import shlex

    from scrapli.driver.generic import AsyncGenericDriver
    from utils.lab_pc_net import _parse_ssh_target

    ssh_target = str(sec_cfg.get("ssh", "")).strip()
    if not ssh_target:
        return False
    cpe_factory = str(sec_cfg.get("cpe_factory_ipv4", "192.168.2.1")).strip()
    host, user = _parse_ssh_target(ssh_target)
    pc_pass = str(sec_cfg.get("password") or password)
    inner = " && ".join(shlex.quote(c) for c in _uci_set_commands(creds, radio_idx))
    remote = (
        f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
        f"root@{cpe_factory} {inner}"
    )
    pc = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=pc_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await pc.open()
    try:
        result = await pc.send_command(remote, timeout_ops=90)
        out = str(result.result or "")
        if "error" in out.lower() and "entry not found" not in out.lower():
            print(f"[link] secondary→CPE credentials: {out[:300]}")
            return False
        print(f"[link] CPE credentials via {user}@{host} → {cpe_factory}")
        return True
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
    import shlex

    tb = profile.get("testbed", {}) or {}
    sec = sec_cfg if sec_cfg is not None else (tb.get("secondary_pc", {}) or {})
    ssh_target = str(sec.get("ssh", "")).strip()
    if not ssh_target:
        return False

    cpe_host = cpe_ssh_access(profile)["host"]
    from utils.lab_pc_net import _parse_ssh_target

    host, user = _parse_ssh_target(ssh_target)
    pc_pass = str(sec.get("password") or profile.get("dut", {}).get("password", ""))
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

    dut = profile.get("dut", {}) or {}
    fl = profile.get("factory_login", {}) or {}
    cpe_users = [
        ("root", str(dut.get("password", ""))),
        ("root", str(fl.get("password", ""))),
        (str(fl.get("username", "installer")), str(fl.get("password", ""))),
    ]

    from scrapli.driver.generic import AsyncGenericDriver

    pc = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=pc_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await pc.open()
    try:
        for cpe_user, cpe_pass in cpe_users:
            if not cpe_pass:
                continue
            remote = (
                f"sshpass -p {shlex.quote(cpe_pass)} ssh -o StrictHostKeyChecking=no "
                f"-o ConnectTimeout=20 {shlex.quote(cpe_user)}@{cpe_host} {inner}"
            )
            try:
                result = await pc.send_command(remote, timeout_ops=180)
            except Exception as exc:
                print(f"[link] secondary SSH {cpe_user}@{cpe_host}: {exc}")
                continue
            out = str(result.result or "")
            if "error" in out.lower() and "entry not found" not in out.lower():
                print(f"[link] secondary SSH→CPE ({cpe_user}): {out[:200]}")
                continue
            print(f"[link] CPE SSID/key via {user}@{host} → {cpe_user}@{cpe_host}")
            return True
        return False
    finally:
        await pc.close()


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
    import shlex

    from scrapli.driver.generic import AsyncGenericDriver
    from utils.lab_pc_net import _parse_ssh_target
    from utils.vlan_uci import build_cpe_mgmtvlan_only_commands, build_cpe_untagged_commands

    ssh_target = str(sec_cfg.get("ssh", "")).strip()
    if not ssh_target:
        return False

    cpe_factory = str(sec_cfg.get("cpe_factory_ipv4", "192.168.2.1")).strip()
    host, user = _parse_ssh_target(ssh_target)
    pc_pass = str(sec_cfg.get("password") or password)
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
    remote = (
        f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "
        f"root@{cpe_factory} {inner}"
    )

    pc = AsyncGenericDriver(
        host=host,
        auth_username=user,
        auth_password=pc_pass,
        auth_strict_key=False,
        transport="asyncssh",
    )
    await pc.open()
    try:
        result = await pc.send_command(remote, timeout_ops=120)
        out = str(result.result or "")
        if "error" in out.lower() and "entry not found" not in out.lower():
            print(f"[link] CPE pre-link setup: {out[:400]}")
            return False
        print(
            f"[link] CPE pre-link OK via {user}@{host} → {cpe_factory} "
            f"(transparent + SSID={creds.ssid})"
        )
        return True
    finally:
        await pc.close()
