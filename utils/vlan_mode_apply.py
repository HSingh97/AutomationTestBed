"""Apply BTS/CPE VLAN modes synchronously for link-debug campaigns."""

from __future__ import annotations

import asyncio
from typing import Any

from scrapli.driver.generic import AsyncGenericDriver

from utils.net_utils import format_ssh_host, normalize_ip
from utils.vlan_control import ensure_bts_qinq_ssh, ensure_bts_transparent_ssh
from utils.vlan_uci import build_cpe_mgmtvlan_only_commands, build_cpe_untagged_commands


async def _open_bts_ssh(host: str, user: str, password: str) -> AsyncGenericDriver:
    from utils.net_utils import is_ipv6_literal

    clean = normalize_ip(host)
    # asyncssh accepts bare IPv6; bracket form can confuse some stacks.
    conn_host = clean if is_ipv6_literal(clean) else format_ssh_host(clean)
    conn = AsyncGenericDriver(
        host=conn_host,
        auth_username=user,
        auth_password=password,
        auth_strict_key=False,
        transport="asyncssh",
        timeout_socket=15,
        timeout_transport=20,
        timeout_ops=30,
    )
    await conn.open()
    return conn


def _cpe_jump_hosts(profile_active: dict[str, Any]) -> dict[str, str]:
    """Resolve CPE-side jump PC and CPE LAN IP from profile."""
    from traffic.vlan_lab import lab_hosts

    return lab_hosts(profile_active)


def _cpe_mode_ok_text(text: str, *, cpe_vlan_mode: str, profile_tb: dict[str, Any]) -> bool:
    mode = str(cpe_vlan_mode).strip().lower()
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "vlan.ath1." not in line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        short = key.strip().split(".")[-1].lower()
        parsed[short] = val.strip().strip("'\"")
    # Bare `uci get vlan.ath1.mode` output.
    if not parsed and text.strip() in {"0", "transparent", "untagged", "1", "2", "3"}:
        parsed["mode"] = text.strip().splitlines()[-1].strip()
    cur_mode = parsed.get("mode", "").strip().lower()
    if cur_mode not in {"0", "transparent", "untagged"}:
        print(f"[vlan] CPE mode={cur_mode or '?'} (want 0/transparent)")
        return False
    for key in ("svlan", "cvlan"):
        val = parsed.get(key, "").strip()
        if val and val not in {"0", ""}:
            print(f"[vlan] CPE still has {key}={val}")
            return False
    if mode in ("mgmt_enable", "mgmt", "mvlan_enable"):
        exp = str(int((profile_tb.get("mgmt_vlan") or {}).get("uci_value", 101)))
        if parsed.get("mgmtvlan", "").strip() != exp:
            print(
                f"[vlan] CPE mgmtvlan={parsed.get('mgmtvlan', '')!r} "
                f"(want {exp})"
            )
            return False
    return True


def _apply_cpe_via_jump(
    profile_active: dict[str, Any],
    *,
    cpe_vlan_mode: str,
    profile_tb: dict[str, Any],
) -> dict[str, Any]:
    """Apply CPE VLAN via CPE-side PC → CPE LAN SSH (no remote_exec / no BTS local run)."""
    hosts = _cpe_jump_hosts(profile_active)
    jump = hosts.get("cpe_pc_host") or ""
    cpe = hosts.get("cpe_lan_host") or ""
    if not jump or not cpe:
        return {"ok": False, "skipped": False, "error": "missing cpe_pc_host/cpe_lan_host"}

    from traffic.vlan_lab import _ssh_via_jump

    check = _ssh_via_jump(
        jump,
        hosts["cpe_pc_password"],
        cpe,
        hosts["cpe_lan_password"],
        "uci show vlan.ath1 2>/dev/null; uci get vlan.ath1.mode 2>/dev/null",
    )
    if _cpe_mode_ok_text(check, cpe_vlan_mode=cpe_vlan_mode, profile_tb=profile_tb):
        print(f"[vlan] CPE already OK ({cpe_vlan_mode}) via {cpe}; skip apply")
        return {"ok": True, "skipped": True, "via": "jump", "host": cpe}

    mode = str(cpe_vlan_mode).strip().lower()
    if mode in ("mgmt_enable", "mgmt", "mvlan_enable"):
        cmds = build_cpe_mgmtvlan_only_commands(profile_tb)
    else:
        cmds = build_cpe_untagged_commands(profile_tb)
    # Avoid network reload when possible — UCI commit alone; soft apply without bounce.
    cmds = [c for c in cmds if "network reload" not in c]
    if not cmds:
        return {"ok": False, "skipped": False, "error": "no CPE VLAN commands"}
    inner = " && ".join(cmds)
    print(f"[vlan] CPE applying {cpe_vlan_mode} via {cpe} (no network reload)")
    out = _ssh_via_jump(
        jump,
        hosts["cpe_pc_password"],
        cpe,
        hosts["cpe_lan_password"],
        inner + "; uci show vlan.ath1 2>/dev/null",
        timeout_s=120,
    )
    ok = _cpe_mode_ok_text(out, cpe_vlan_mode=cpe_vlan_mode, profile_tb=profile_tb)
    print(f"[vlan] CPE vlan apply via jump ok={ok}")
    return {"ok": ok, "skipped": False, "via": "jump", "host": cpe, "output_tail": out[-200:]}


def _apply_cpe_via_cfgupdate(
    profile_active: dict[str, Any],
    *,
    cpe_vlan_mode: str,
    profile_tb: dict[str, Any],
) -> dict[str, Any]:
    """Fallback: cfgupdate to associated SU MAC (SET-only, no BTS local exec)."""
    from traffic.vlan_lab import _cfgupdate_cpe, lab_hosts

    hosts = lab_hosts(profile_active)
    mode = str(cpe_vlan_mode).strip().lower()
    if mode in ("mgmt_enable", "mgmt", "mvlan_enable"):
        cmds = build_cpe_mgmtvlan_only_commands(profile_tb)
    else:
        cmds = build_cpe_untagged_commands(profile_tb)
    cmds = [c for c in cmds if "network reload" not in c]
    for cmd in cmds:
        _cfgupdate_cpe(hosts, cmd)
    print(f"[vlan] CPE vlan apply via cfgupdate ({len(cmds)} cmds)")
    return {"ok": True, "skipped": False, "via": "cfgupdate"}


async def _apply_cpe_vlan(
    profile_active: dict[str, Any],
    *,
    cpe_vlan_mode: str,
    profile_tb: dict[str, Any],
    su_indexes: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Push CPE VLAN UCI — prefer jump SSH; fall back to cfgupdate."""
    try:
        result = _apply_cpe_via_jump(
            profile_active,
            cpe_vlan_mode=cpe_vlan_mode,
            profile_tb=profile_tb,
        )
        if result.get("ok") or result.get("skipped"):
            return [result]
        print(f"[vlan] CPE jump apply failed: {result}; trying cfgupdate")
    except Exception as exc:
        print(f"[vlan] CPE jump apply error: {exc}; trying cfgupdate")
    return [
        _apply_cpe_via_cfgupdate(
            profile_active,
            cpe_vlan_mode=cpe_vlan_mode,
            profile_tb=profile_tb,
        )
    ]


async def apply_vlan_mode_async(
    *,
    profile_active: dict[str, Any],
    bts_vlan_mode: str,
    su_count: int = 4,
    cpe_vlan_mode: str = "untagged",
    su_indexes: list[int] | None = None,
) -> dict[str, Any]:
    """
    Apply VLAN on BTS (+ CPE mode on associated SU).

    ``bts_vlan_mode``: ``qinq`` or ``transparent``.
    ``cpe_vlan_mode``: ``untagged`` (default) or ``mgmt_enable`` (transparent + mgmtvlan).
    """
    dut = profile_active.get("dut") or {}
    tb = profile_active.get("testbed") or {}
    user = str(dut.get("username") or "root")
    password = str(dut.get("password") or "")
    # Prefer IPv4 backup for UCI/control (works when DUT is transparent/untagged).
    # IPv6 is used for data-plane after lab-PC tagging matches DUT mgmtvlan.
    try:
        from traffic.vlan_lab import lab_hosts

        hosts = lab_hosts(profile_active)
        bts_host = str(hosts.get("dut_ipv4") or hosts.get("dut_host") or "")
    except Exception:
        bts_host = ""
    if not bts_host:
        bts_host = str(dut.get("ssh_host") or dut.get("local_ip") or dut.get("local_ipv6") or "10.0.0.1")
    mode = str(bts_vlan_mode).strip().lower()

    bts_ssh = await _open_bts_ssh(bts_host, user, password)
    try:
        if mode == "qinq":
            bts_ok = await ensure_bts_qinq_ssh(bts_ssh, tb)
        elif mode == "transparent":
            bts_ok = await ensure_bts_transparent_ssh(bts_ssh, tb)
        else:
            raise ValueError(f"Unsupported BTS VLAN mode: {bts_vlan_mode}")

        cpe_results = await _apply_cpe_vlan(
            profile_active,
            cpe_vlan_mode=cpe_vlan_mode,
            profile_tb=tb,
            su_indexes=su_indexes,
        )
        return {
            "bts_vlan_mode": mode,
            "cpe_vlan_mode": str(cpe_vlan_mode).strip().lower(),
            "bts_ok": bts_ok,
            "cpe_results": cpe_results,
            "bts_host": normalize_ip(bts_host.split("/")[0]) if ":" in bts_host else bts_host,
        }
    finally:
        await bts_ssh.close()


def apply_vlan_mode(
    *,
    profile_active: dict[str, Any],
    bts_vlan_mode: str,
    su_count: int = 4,
    cpe_vlan_mode: str = "untagged",
    su_indexes: list[int] | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        apply_vlan_mode_async(
            profile_active=profile_active,
            bts_vlan_mode=bts_vlan_mode,
            su_count=su_count,
            cpe_vlan_mode=cpe_vlan_mode,
            su_indexes=su_indexes,
        )
    )
